"""Tests for v0.4.7 stale-library detection — verify phase _stale_libs_check."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from archward.config.defaults import default_config
from archward.models.verify import CheckStatus
from archward.pipeline import verify_phase


# ── config helpers ────────────────────────────────────────────────────────────

def _cfg(stale_libs: bool = True):
    cfg = default_config()
    return cfg.model_copy(update={
        "verify": cfg.verify.model_copy(update={"stale_libs": stale_libs}),
    })


# ── fake /proc tree helpers ───────────────────────────────────────────────────

def _make_proc(tmp_path: Path, pids: dict) -> Path:
    """Create a minimal fake /proc directory.

    pids: {pid: {"maps": "<content>", "cgroup": "<content>"}}
    """
    proc = tmp_path / "proc"
    proc.mkdir()
    for pid, files in pids.items():
        pid_dir = proc / str(pid)
        pid_dir.mkdir()
        if "maps" in files:
            (pid_dir / "maps").write_text(files["maps"])
        if "cgroup" in files:
            (pid_dir / "cgroup").write_text(files["cgroup"])
    return proc


_CGROUP_SSHD = "0::/system.slice/sshd.service\n"
_CGROUP_PIPEWIRE = "0::/user.slice/user-1000.slice/user@1000.service/app.slice/pipewire.service\n"
_MAPS_CLEAN = "7f1234000000-7f1234001000 r--p 00000000 08:01 12345 /usr/lib/libm.so.6\n"
_MAPS_STALE = (
    "7f1234000000-7f1234001000 r--p 00000000 08:01 12345 /usr/lib/libssl.so.3 (deleted)\n"
    "7f1235000000-7f1235001000 r--p 00000000 08:01 12346 /usr/lib/libm.so.6\n"
)
_MAPS_NON_LIB = (
    "7f1234000000-7f1234001000 r--p 00000000 08:01 12345 /tmp/jit_cache (deleted)\n"
    "7f1235000000-7f1235001000 r--p 00000000 08:01 12346 /run/shm/foo.sock (deleted)\n"
)
_MAPS_MULTI_LIB = (
    "7f1234000000-7f1234001000 r--p 00000000 08:01 12345 /usr/lib/libssl.so.3 (deleted)\n"
    "7f1235000000-7f1235001000 r--p 00000000 08:01 12346 /usr/lib/libcrypto.so.3 (deleted)\n"
)


# ── disabled ─────────────────────────────────────────────────────────────────

def test_disabled_returns_pass() -> None:
    result = verify_phase._stale_libs_check(_cfg(stale_libs=False))
    assert result.status is CheckStatus.PASS
    assert "disabled" in result.message


# ── full scan via sudo ────────────────────────────────────────────────────────

def test_full_scan_no_stale(monkeypatch) -> None:
    monkeypatch.setattr(verify_phase, "_sudo_scan", lambda _: [])
    monkeypatch.setattr(verify_phase, "_SCAN_SCRIPT_CANDIDATES",
                        [Path("/usr/share/archward/stale_libs_scan")])
    with patch.object(Path, "exists", return_value=True):
        result = verify_phase._stale_libs_check(_cfg())
    assert result.status is CheckStatus.PASS
    assert "No services" in result.message
    assert "user-visible" not in result.message


def test_full_scan_finds_stale(monkeypatch) -> None:
    entries = [{"unit": "sshd.service", "deleted": ["/usr/lib/libssl.so.3"]}]
    monkeypatch.setattr(verify_phase, "_sudo_scan", lambda _: entries)
    monkeypatch.setattr(verify_phase, "_SCAN_SCRIPT_CANDIDATES",
                        [Path("/usr/share/archward/stale_libs_scan")])
    with patch.object(Path, "exists", return_value=True):
        result = verify_phase._stale_libs_check(_cfg())
    assert result.status is CheckStatus.WARN
    assert "1 service" in result.message
    assert "sshd.service" in result.detail
    assert "/usr/lib/libssl.so.3" in result.detail
    assert "user-visible" not in result.detail


def test_full_scan_no_fallback_note(monkeypatch) -> None:
    """Full coverage path must NOT include the user-visible-only note."""
    entries = [{"unit": "NetworkManager.service", "deleted": ["/usr/lib/libnm.so.0"]}]
    monkeypatch.setattr(verify_phase, "_sudo_scan", lambda _: entries)
    monkeypatch.setattr(verify_phase, "_SCAN_SCRIPT_CANDIDATES",
                        [Path("/usr/share/archward/stale_libs_scan")])
    with patch.object(Path, "exists", return_value=True):
        result = verify_phase._stale_libs_check(_cfg())
    assert "system services not scanned" not in (result.detail or "")


# ── fallback: user-visible scan ───────────────────────────────────────────────

def test_fallback_no_stale(tmp_path) -> None:
    proc = _make_proc(tmp_path, {
        1234: {"maps": _MAPS_CLEAN, "cgroup": _CGROUP_SSHD},
    })
    with patch.object(verify_phase, "_sudo_scan", return_value=None), \
         patch.object(verify_phase, "_SCAN_SCRIPT_CANDIDATES", []):
        result = verify_phase._stale_libs_check(
            _cfg(),
        )
        # Force inline by calling _user_visible_scan directly with fake proc
        inline = verify_phase._user_visible_scan(proc_dir=proc)
    assert inline == []


def test_fallback_finds_stale(tmp_path) -> None:
    proc = _make_proc(tmp_path, {
        1234: {"maps": _MAPS_STALE, "cgroup": _CGROUP_PIPEWIRE},
    })
    inline = verify_phase._user_visible_scan(proc_dir=proc)
    assert len(inline) == 1
    assert inline[0]["unit"] == "pipewire.service"
    assert "/usr/lib/libssl.so.3" in inline[0]["deleted"]


def test_fallback_detail_has_coverage_note(tmp_path, monkeypatch) -> None:
    proc = _make_proc(tmp_path, {
        1234: {"maps": _MAPS_STALE, "cgroup": _CGROUP_PIPEWIRE},
    })
    monkeypatch.setattr(verify_phase, "_sudo_scan", lambda _: None)
    monkeypatch.setattr(verify_phase, "_SCAN_SCRIPT_CANDIDATES", [])
    monkeypatch.setattr(
        verify_phase, "_user_visible_scan",
        lambda **_: [{"unit": "pipewire.service", "deleted": ["/usr/lib/libssl.so.3"]}],
    )
    result = verify_phase._stale_libs_check(_cfg())
    assert result.status is CheckStatus.WARN
    assert "system services not scanned" in result.detail


# ── filtering ─────────────────────────────────────────────────────────────────

def test_filters_non_lib_paths(tmp_path) -> None:
    proc = _make_proc(tmp_path, {
        1234: {"maps": _MAPS_NON_LIB, "cgroup": _CGROUP_SSHD},
    })
    result = verify_phase._user_visible_scan(proc_dir=proc)
    assert result == []


def test_groups_multiple_pids_by_unit(tmp_path) -> None:
    proc = _make_proc(tmp_path, {
        100: {"maps": _MAPS_STALE, "cgroup": _CGROUP_SSHD},
        101: {"maps": _MAPS_MULTI_LIB, "cgroup": _CGROUP_SSHD},
    })
    result = verify_phase._user_visible_scan(proc_dir=proc)
    assert len(result) == 1
    assert result[0]["unit"] == "sshd.service"
    deleted = set(result[0]["deleted"])
    assert "/usr/lib/libssl.so.3" in deleted
    assert "/usr/lib/libcrypto.so.3" in deleted


# ── timeout ───────────────────────────────────────────────────────────────────

def test_inline_timeout_returns_warn(monkeypatch) -> None:
    monkeypatch.setattr(verify_phase, "_sudo_scan", lambda _: None)
    monkeypatch.setattr(verify_phase, "_SCAN_SCRIPT_CANDIDATES", [])

    def _raise(*a, **k):
        raise TimeoutError("mocked timeout")

    monkeypatch.setattr(verify_phase, "_call_with_timeout", _raise)
    result = verify_phase._stale_libs_check(_cfg())
    assert result.status is CheckStatus.WARN
    assert "timed out" in result.message


# ── script not found fallback ─────────────────────────────────────────────────

def test_script_not_found_uses_inline(monkeypatch, tmp_path) -> None:
    """When no script candidate exists, falls back to _user_visible_scan."""
    monkeypatch.setattr(verify_phase, "_SCAN_SCRIPT_CANDIDATES", [])

    called = []

    def _fake_user_scan(**_):
        called.append(True)
        return []

    monkeypatch.setattr(verify_phase, "_user_visible_scan", _fake_user_scan)
    monkeypatch.setattr(
        verify_phase, "_call_with_timeout",
        lambda fn, t: fn(),
    )
    result = verify_phase._stale_libs_check(_cfg())
    assert called
    assert result.status is CheckStatus.PASS
