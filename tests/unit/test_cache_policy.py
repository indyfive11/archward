"""Tests for v0.4.4 F1a — cache_policy detection + assessment.

All filesystem + subprocess access is stubbed against tmp fixtures, so
nothing touches the real /etc, /var/cache, or systemctl.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from archward.system import cache_policy as cp
from archward.system.cache_policy import RollbackSafety


# ── effective_keep parsing ────────────────────────────────────────────


@pytest.mark.parametrize("args,expected", [
    ("", 3),                 # unset → bare `paccache -r` keeps 3
    ("-rk3", 3),
    ("-rk1", 1),
    ("-k 2", 2),
    ("-k5", 5),
    ("--keep 7", 7),
    ("--keep=10", 10),
    ("-rk5 -ruk2", 5),       # first keep wins
    ("-v", 3),               # no keep flag → default 3
    ("garbage", 3),
])
def test_effective_keep(args, expected) -> None:
    assert cp.effective_keep(args) == expected


# ── timer state ───────────────────────────────────────────────────────


def test_timer_state_enabled(monkeypatch) -> None:
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: type("R", (), {"stdout": "enabled\n", "returncode": 0})())
    assert cp.paccache_timer_state() == "enabled"


def test_timer_state_disabled(monkeypatch) -> None:
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: type("R", (), {"stdout": "disabled\n", "returncode": 1})())
    assert cp.paccache_timer_state() == "disabled"


def test_timer_state_not_installed_on_unknown_unit(monkeypatch) -> None:
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: type("R", (), {"stdout": "", "returncode": 1})())
    assert cp.paccache_timer_state() == "not-installed"


def test_timer_state_not_installed_no_systemctl(monkeypatch) -> None:
    def boom(*a, **k):
        raise FileNotFoundError()
    monkeypatch.setattr(subprocess, "run", boom)
    assert cp.paccache_timer_state() == "not-installed"


def test_timer_state_timeout_is_not_installed(monkeypatch) -> None:
    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="systemctl", timeout=5)
    monkeypatch.setattr(subprocess, "run", boom)
    assert cp.paccache_timer_state() == "not-installed"


# ── PACCACHE_ARGS parsing ─────────────────────────────────────────────


def test_read_paccache_args_unset(tmp_path) -> None:
    conf = tmp_path / "pacman-contrib"
    conf.write_text("PACCACHE_ARGS=\n")
    assert cp.read_paccache_args(conf) == ""


def test_read_paccache_args_quoted(tmp_path) -> None:
    conf = tmp_path / "pacman-contrib"
    conf.write_text("# comment\nPACCACHE_ARGS='-rk5 -ruk2'\n")
    assert cp.read_paccache_args(conf) == "-rk5 -ruk2"


def test_read_paccache_args_missing_file(tmp_path) -> None:
    assert cp.read_paccache_args(tmp_path / "nope") == ""


def test_read_paccache_args_ignores_comment(tmp_path) -> None:
    conf = tmp_path / "pacman-contrib"
    conf.write_text("#PACCACHE_ARGS='-rk1'\nPACCACHE_ARGS='-rk8'\n")
    assert cp.read_paccache_args(conf) == "-rk8"


# ── CleanMethod parsing ───────────────────────────────────────────────


def test_clean_method_default_when_unset(tmp_path) -> None:
    pc = tmp_path / "pacman.conf"
    pc.write_text("[options]\nHoldPkg = pacman glibc\n")
    assert cp.read_clean_method(pc) == ("KeepInstalled",)


def test_clean_method_keepcurrent(tmp_path) -> None:
    pc = tmp_path / "pacman.conf"
    pc.write_text("[options]\nCleanMethod = KeepCurrent\n")
    assert cp.read_clean_method(pc) == ("KeepCurrent",)


def test_clean_method_missing_file_defaults() -> None:
    assert cp.read_clean_method(Path("/nonexistent/pacman.conf")) == ("KeepInstalled",)


# ── CacheDir parsing (A1) ─────────────────────────────────────────────


def test_cache_dirs_default_when_unset(tmp_path) -> None:
    pc = tmp_path / "pacman.conf"
    pc.write_text("[options]\n#CacheDir = /var/cache/pacman/pkg/\n")
    assert cp.read_cache_dirs(pc) == (cp.PACMAN_CACHE_DIR,)


def test_cache_dirs_missing_file_default() -> None:
    assert cp.read_cache_dirs(Path("/nope/pacman.conf")) == (cp.PACMAN_CACHE_DIR,)


def test_cache_dirs_relocated(tmp_path) -> None:
    pc = tmp_path / "pacman.conf"
    pc.write_text("[options]\nCacheDir = /mnt/big/pkgcache/\n")
    assert cp.read_cache_dirs(pc) == (Path("/mnt/big/pkgcache"),)


def test_cache_dirs_multiple_lines_and_spaced(tmp_path) -> None:
    pc = tmp_path / "pacman.conf"
    pc.write_text(
        "[options]\n"
        "CacheDir = /var/cache/pacman/pkg/\n"
        "CacheDir = /mnt/a/cache  /mnt/b/cache\n"
    )
    assert cp.read_cache_dirs(pc) == (
        Path("/var/cache/pacman/pkg"),
        Path("/mnt/a/cache"),
        Path("/mnt/b/cache"),
    )


# ── CleanMethod verdict edge (A3) ─────────────────────────────────────


def test_keepcurrent_with_keepinstalled_is_not_dangerous() -> None:
    """Both set → paccache keeps the union, downgrade target survives."""
    s, _ = cp._assess("enabled", 3, ("KeepInstalled", "KeepCurrent"), ())
    assert s is not RollbackSafety.DANGEROUS


def test_keepcurrent_alone_still_dangerous() -> None:
    s, _ = cp._assess("enabled", 3, ("KeepCurrent",), ())
    assert s is RollbackSafety.DANGEROUS


# ── dangerous-hook detection ──────────────────────────────────────────


def test_scan_cleaning_hooks_detects_paccache(tmp_path) -> None:
    d = tmp_path / "hooks"
    d.mkdir()
    (d / "clean.hook").write_text(
        "[Trigger]\nOperation = Upgrade\nType = Package\nTarget = *\n"
        "[Action]\nDescription = clean\nWhen = PostTransaction\n"
        "Exec = /usr/bin/paccache -rk1\n"
    )
    found = cp.scan_cleaning_hooks((d,))
    assert len(found) == 1
    assert found[0].name == "clean.hook"


def test_scan_cleaning_hooks_detects_pacman_Sc(tmp_path) -> None:
    d = tmp_path / "hooks"
    d.mkdir()
    (d / "sc.hook").write_text(
        "[Action]\nExec = /bin/sh -c 'pacman -Scc --noconfirm'\n"
    )
    assert len(cp.scan_cleaning_hooks((d,))) == 1


def test_scan_cleaning_hooks_no_false_positive_on_comment_mention(tmp_path) -> None:
    """A hook that mentions paccache only in a Description, not Exec."""
    d = tmp_path / "hooks"
    d.mkdir()
    (d / "innocent.hook").write_text(
        "[Action]\nDescription = run after paccache would have run\n"
        "Exec = /usr/bin/glib-compile-schemas /usr/share/glib-2.0/schemas\n"
    )
    assert cp.scan_cleaning_hooks((d,)) == ()


def test_scan_cleaning_hooks_no_false_positive_on_compile_schemas(tmp_path) -> None:
    """Regression: a loose `-Sc` substring match catches glib-compile-schemas."""
    d = tmp_path / "hooks"
    d.mkdir()
    (d / "glib.hook").write_text(
        "[Action]\nExec = /usr/bin/glib-compile-schemas /usr/share/glib-2.0/schemas\n"
    )
    assert cp.scan_cleaning_hooks((d,)) == ()


def test_scan_cleaning_hooks_missing_dirs_ok() -> None:
    assert cp.scan_cleaning_hooks((Path("/no/such/dir"),)) == ()


# ── cache_stats ───────────────────────────────────────────────────────


def test_cache_stats(tmp_path) -> None:
    (tmp_path / "foo-1.0-1-x86_64.pkg.tar.zst").write_bytes(b"x" * 100)
    (tmp_path / "foo-1.1-1-x86_64.pkg.tar.zst").write_bytes(b"y" * 200)
    (tmp_path / "bar-2.0-1-x86_64.pkg.tar.xz").write_bytes(b"z" * 50)
    (tmp_path / "not-a-package.txt").write_text("ignore me")
    (tmp_path / "foo-1.0-1-x86_64.pkg.tar.zst.sig").write_bytes(b"sig")
    size, count = cp.cache_stats(tmp_path)
    assert size == 350
    assert count == 3


def test_cache_stats_missing_dir() -> None:
    assert cp.cache_stats(Path("/no/such/cache")) == (0, 0)


# ── verdict matrix ────────────────────────────────────────────────────


def test_verdict_dangerous_on_cleaning_hook() -> None:
    s, msg = cp._assess("disabled", 3, ("KeepInstalled",),
                        (Path("/etc/pacman.d/hooks/clean.hook"),))
    assert s is RollbackSafety.DANGEROUS
    assert "hook" in msg.lower()


def test_verdict_dangerous_on_keepcurrent_with_timer() -> None:
    s, _ = cp._assess("enabled", 3, ("KeepCurrent",), ())
    assert s is RollbackSafety.DANGEROUS


def test_verdict_dangerous_on_keep1_with_timer() -> None:
    s, _ = cp._assess("enabled", 1, ("KeepInstalled",), ())
    assert s is RollbackSafety.DANGEROUS


def test_verdict_tight_on_keep2() -> None:
    s, _ = cp._assess("enabled", 2, ("KeepInstalled",), ())
    assert s is RollbackSafety.TIGHT


def test_verdict_balanced_on_keep3_timer() -> None:
    s, _ = cp._assess("enabled", 3, ("KeepInstalled",), ())
    assert s is RollbackSafety.BALANCED


def test_verdict_generous_on_keep10_timer() -> None:
    s, _ = cp._assess("enabled", 10, ("KeepInstalled",), ())
    assert s is RollbackSafety.GENEROUS


def test_verdict_unmanaged_no_timer_no_hook() -> None:
    """The live-machine case: timer disabled, no cleaning hook."""
    s, msg = cp._assess("disabled", 3, ("KeepInstalled",), ())
    assert s is RollbackSafety.UNMANAGED
    assert "never auto-pruned" in msg


# ── presets ───────────────────────────────────────────────────────────


def test_all_presets_present() -> None:
    keys = {p.key for p in cp.CACHE_PRESETS}
    assert keys == {"home", "workstation", "server", "mission-critical"}


def test_mission_critical_disables_timer() -> None:
    mc = next(p for p in cp.CACHE_PRESETS if p.key == "mission-critical")
    assert mc.enable_timer is False
    assert "-rk15" in mc.paccache_args


def test_preset_commands_enable_timer() -> None:
    home = next(p for p in cp.CACHE_PRESETS if p.key == "home")
    cmds = cp.preset_commands(home)
    assert ["tee", "/etc/conf.d/pacman-contrib"] in cmds
    assert ["systemctl", "enable", "--now", "paccache.timer"] in cmds


def test_preset_commands_disable_timer_for_mission_critical() -> None:
    mc = next(p for p in cp.CACHE_PRESETS if p.key == "mission-critical")
    cmds = cp.preset_commands(mc)
    assert ["systemctl", "disable", "--now", "paccache.timer"] in cmds


def test_preset_conf_content_has_args() -> None:
    server = next(p for p in cp.CACHE_PRESETS if p.key == "server")
    content = cp.preset_conf_content(server)
    assert "PACCACHE_ARGS='-rk10'" in content
    assert content.startswith("# Managed by archward")
