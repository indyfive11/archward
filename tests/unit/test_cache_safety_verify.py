"""Tests for v0.4.4 F2 — verify-phase `_cache_safety_check`.

Stubs `pq.list_all`, the pacman cache dir, and `scan_cleaning_hooks`
against tmp fixtures so nothing touches the real system. Asserts the
verdict + that the FAIL path names the cause (hook vs prune) so the
v0.4.0 "What to do?" button has actionable detail.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from archward.models.verify import CheckStatus
from archward.pipeline import verify_phase
from archward.system import cache_policy as cp


def _snap(tmp_path: Path, lines: list[str] | None) -> Path:
    """Build a snapshot dir; `lines` None → omit packages/all.txt."""
    root = tmp_path / "snap"
    if lines is not None:
        pkg = root / "packages"
        pkg.mkdir(parents=True)
        (pkg / "all.txt").write_text("\n".join(lines) + "\n")
    else:
        root.mkdir(parents=True)
    return root


@pytest.fixture
def cache_dir(tmp_path, monkeypatch) -> Path:
    """Point the check's cache scan at a tmp dir.

    The check resolves cache dirs via cp.read_cache_dirs() (honours
    pacman.conf CacheDir), so stub THAT — not the PACMAN_CACHE_DIR
    constant — or the test would scan the host's real cache.
    """
    d = tmp_path / "pkgcache"
    d.mkdir()
    monkeypatch.setattr(cp, "read_cache_dirs", lambda *a, **k: (d,))
    return d


def _stub_installed(monkeypatch, pairs: list[tuple[str, str]]) -> None:
    monkeypatch.setattr(verify_phase.pq, "list_all", lambda: list(pairs))


def test_no_snapshot_list_skips(tmp_path, cache_dir, monkeypatch) -> None:
    _stub_installed(monkeypatch, [("foo", "2-1")])
    chk = verify_phase._cache_safety_check(_snap(tmp_path, None))
    assert chk.status is CheckStatus.PASS
    assert "skipped" in chk.message


def test_nothing_changed_passes(tmp_path, cache_dir, monkeypatch) -> None:
    _stub_installed(monkeypatch, [("foo", "1-1"), ("bar", "2-1")])
    chk = verify_phase._cache_safety_check(
        _snap(tmp_path, ["foo 1-1", "bar 2-1"])
    )
    assert chk.status is CheckStatus.PASS
    assert "no package versions changed" in chk.message


def test_old_file_present_passes(tmp_path, cache_dir, monkeypatch) -> None:
    # foo updated 1-1 → 2-1; the pre-update 1-1 file is still cached.
    (cache_dir / "foo-1-1-x86_64.pkg.tar.zst").write_bytes(b"x")
    _stub_installed(monkeypatch, [("foo", "2-1")])
    monkeypatch.setattr(cp, "scan_cleaning_hooks", lambda: ())
    chk = verify_phase._cache_safety_check(_snap(tmp_path, ["foo 1-1"]))
    assert chk.status is CheckStatus.PASS
    assert "rollback available" in chk.message


def test_old_file_missing_with_hook_fails(tmp_path, cache_dir, monkeypatch) -> None:
    _stub_installed(monkeypatch, [("foo", "2-1")])
    monkeypatch.setattr(
        cp, "scan_cleaning_hooks",
        lambda: (Path("/etc/pacman.d/hooks/zz-clean.hook"),),
    )
    chk = verify_phase._cache_safety_check(_snap(tmp_path, ["foo 1-1"]))
    assert chk.status is CheckStatus.FAIL
    assert "rollback unavailable" in chk.message
    assert "zz-clean.hook" in chk.detail
    # Maps to the registered hint key for the "What to do?" button.
    assert chk.name == "rollback-cache"


def test_old_file_missing_no_hook_fails_with_prune_cause(
    tmp_path, cache_dir, monkeypatch
) -> None:
    _stub_installed(monkeypatch, [("foo", "2-1")])
    monkeypatch.setattr(cp, "scan_cleaning_hooks", lambda: ())
    chk = verify_phase._cache_safety_check(_snap(tmp_path, ["foo 1-1"]))
    assert chk.status is CheckStatus.FAIL
    assert "paccache" in chk.detail


def test_epoch_version_prefix_matches(tmp_path, cache_dir, monkeypatch) -> None:
    # Epoch packages: `pacman -Q` prints `2:1.2.3-4`, and the cache
    # filename embeds the colon literally.
    (cache_dir / "foo-2:1.2.3-4-x86_64.pkg.tar.zst").write_bytes(b"x")
    _stub_installed(monkeypatch, [("foo", "2:1.2.4-1")])
    monkeypatch.setattr(cp, "scan_cleaning_hooks", lambda: ())
    chk = verify_phase._cache_safety_check(_snap(tmp_path, ["foo 2:1.2.3-4"]))
    assert chk.status is CheckStatus.PASS


def test_relocated_cachedir_is_scanned(tmp_path, monkeypatch) -> None:
    """Regression: a moved CacheDir must not mass-false-FAIL. The old
    file lives in the *relocated* dir; read_cache_dirs() reports it."""
    moved = tmp_path / "bigdisk" / "pkgcache"
    moved.mkdir(parents=True)
    (moved / "foo-1-1-x86_64.pkg.tar.zst").write_bytes(b"x")
    monkeypatch.setattr(cp, "read_cache_dirs", lambda *a, **k: (moved,))
    _stub_installed(monkeypatch, [("foo", "2-1")])
    monkeypatch.setattr(cp, "scan_cleaning_hooks", lambda: ())
    chk = verify_phase._cache_safety_check(_snap(tmp_path, ["foo 1-1"]))
    assert chk.status is CheckStatus.PASS
    assert "rollback available" in chk.message


def test_multiple_cachedirs_union(tmp_path, monkeypatch) -> None:
    c1 = tmp_path / "c1"; c1.mkdir()
    c2 = tmp_path / "c2"; c2.mkdir()
    (c2 / "bar-9-9-x86_64.pkg.tar.zst").write_bytes(b"x")  # only in 2nd dir
    monkeypatch.setattr(cp, "read_cache_dirs", lambda *args, **kw: (c1, c2))
    _stub_installed(monkeypatch, [("bar", "10-1")])
    monkeypatch.setattr(cp, "scan_cleaning_hooks", lambda: ())
    chk = verify_phase._cache_safety_check(_snap(tmp_path, ["bar 9-9"]))
    assert chk.status is CheckStatus.PASS


def test_scan_failure_skips_not_fails(tmp_path, monkeypatch) -> None:
    """Cache scan timeout/OSError → PASS-skip, never a mass false FAIL."""
    monkeypatch.setattr(cp, "read_cache_dirs",
                        lambda *a, **k: (tmp_path / "unreadable",))

    def boom(fn, timeout):
        raise TimeoutError("slow cache fs")

    monkeypatch.setattr(verify_phase, "_call_with_timeout", boom)
    _stub_installed(monkeypatch, [("foo", "2-1")])
    chk = verify_phase._cache_safety_check(_snap(tmp_path, ["foo 1-1"]))
    assert chk.status is CheckStatus.PASS
    assert "skipped" in chk.message
