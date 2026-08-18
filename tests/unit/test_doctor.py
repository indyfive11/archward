"""`archward doctor` — check framework, individual checks, exit codes,
--json contract, and the read-only invariant.

Doctor exists for broken boxes, so the framework tests are the load-bearing
ones: a crashing check must become a FAIL row (not kill the report), a
hanging check must become a WARN row, and NOTHING may be written to disk —
including the config bootstrap-write and the state-dir mkdir that the
normal cfg path (`build_config`) performs.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from pathlib import Path

import pytest

import archward.config.paths as paths_mod
from archward.cli_subcommands import doctor
from archward.models.verify import CheckStatus


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path, monkeypatch):
    """Point config + state at empty tmp dirs and neutralize env probes."""
    cfg_home = tmp_path / "config"
    state_home = tmp_path / "state"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg_home))
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))
    monkeypatch.delenv("SUDO_ASKPASS", raising=False)
    return tmp_path


def _args(json_flag: bool = False) -> argparse.Namespace:
    return argparse.Namespace(json=json_flag)


def _quiet_checks(monkeypatch, **overrides):
    """Stub every check body to a single PASS row, then apply overrides.

    Overrides map check-fn name -> replacement callable (or a list of
    CheckRow to return).
    """
    def one_pass(name):
        return lambda *a, **k: [doctor.CheckRow(name, CheckStatus.PASS, "ok")]

    bodies = {
        "_identity_rows": one_pass("archward"),
        "_root_rows": lambda: [],
        "_distro_rows": one_pass("distro"),
        "_config_rows": lambda config_path, holder: (
            holder.__setitem__("cfg", __import__(
                "archward.config.defaults", fromlist=["default_config"]
            ).default_config())
            or [doctor.CheckRow("config", CheckStatus.PASS, "ok")]
        ),
        "_sudo_rows": one_pass("sudo"),
        "_askpass_rows": one_pass("askpass"),
        "_pacman_rows": one_pass("pacman"),
        "_db_lock_rows": one_pass("pacman-db-lock"),
        "_disk_rows": one_pass("disk-space"),
        "_cache_rows": one_pass("cache-safety"),
        "_state_rows": one_pass("state-dirs"),
        "_aur_rows": one_pass("aur-helper"),
        "_services_rows": one_pass("services-config"),
        "_gui_rows": one_pass("gui-stack"),
        "_lastrun_rows": one_pass("last-run"),
    }
    for fn_name, replacement in overrides.items():
        if isinstance(replacement, list):
            rows = replacement
            bodies[fn_name] = lambda *a, _rows=rows, **k: _rows
        else:
            bodies[fn_name] = replacement
    for fn_name, body in bodies.items():
        monkeypatch.setattr(doctor, fn_name, body)


# ── framework ─────────────────────────────────────────────────────────────


def test_crashing_check_becomes_fail_row_and_report_survives(monkeypatch, capsys):
    def boom():
        raise RuntimeError("kaput")

    _quiet_checks(monkeypatch, _gui_rows=boom)
    rc = doctor.cmd_doctor(_args(), None)
    out = capsys.readouterr().out
    assert "check crashed: RuntimeError: kaput" in out
    assert "last-run" in out  # checks after the crash still ran
    assert rc == 1


def test_hanging_check_becomes_warn_row(monkeypatch, capsys):
    monkeypatch.setattr(doctor, "_CHECK_DEADLINE_S", 0.2)

    def hang(*a, **k):
        time.sleep(5)
        return []

    _quiet_checks(monkeypatch, _cache_rows=hang)
    start = time.monotonic()
    rc = doctor.cmd_doctor(_args(), None)
    assert time.monotonic() - start < 4  # did not wait out the sleep
    out = capsys.readouterr().out
    assert "timed out" in out
    assert rc == 2


def test_all_pass_exits_zero(monkeypatch, capsys):
    _quiet_checks(monkeypatch)
    assert doctor.cmd_doctor(_args(), None) == 0
    assert "0 fail" in capsys.readouterr().out


def test_warn_only_exits_two(monkeypatch, capsys):
    _quiet_checks(monkeypatch, _aur_rows=[
        doctor.CheckRow("aur-helper", CheckStatus.WARN, "no helper")
    ])
    assert doctor.cmd_doctor(_args(), None) == 2


def test_skipped_counts_in_neither_tally_and_exits_zero(monkeypatch, capsys):
    _quiet_checks(monkeypatch, _lastrun_rows=[
        doctor.CheckRow("last-run", CheckStatus.SKIPPED, "no runs recorded")
    ])
    rc = doctor.cmd_doctor(_args(), None)
    out = capsys.readouterr().out
    assert rc == 0
    assert "summary: 13 pass, 0 warn, 0 fail, 1 skipped" in out


def test_fail_beats_warn_exits_one(monkeypatch, capsys):
    _quiet_checks(
        monkeypatch,
        _aur_rows=[doctor.CheckRow("aur-helper", CheckStatus.WARN, "w")],
        _disk_rows=[doctor.CheckRow("disk-space", CheckStatus.FAIL, "f")],
    )
    assert doctor.cmd_doctor(_args(), None) == 1


def test_config_crash_falls_back_to_defaults_and_report_continues(monkeypatch, capsys):
    def boom(config_path, holder):
        raise OSError("config exploded")

    _quiet_checks(monkeypatch, _config_rows=boom)
    rc = doctor.cmd_doctor(_args(), None)
    out = capsys.readouterr().out
    assert "check crashed" in out
    assert "remaining checks use defaults" in out
    assert "last-run" in out
    assert rc == 1


# ── read-only invariant ───────────────────────────────────────────────────


def test_doctor_creates_no_files(tmp_path, monkeypatch, capsys):
    """The real cmd_doctor (no stubbed checks, only subprocess probes
    neutralized) must not create the config file, the state dir, or
    anything else under the isolated XDG roots."""
    monkeypatch.setattr(doctor, "_gui_rows", lambda: [])
    monkeypatch.setattr(doctor, "_sudo_rows", lambda holder: [])
    monkeypatch.setattr(doctor, "_pacman_rows", lambda: [])
    monkeypatch.setattr(doctor, "_services_rows", lambda cfg: [])
    monkeypatch.setattr(doctor, "_cache_rows", lambda cfg: [])

    doctor.cmd_doctor(_args(), None)

    cfg_home = Path(os.environ["XDG_CONFIG_HOME"])
    state_home = Path(os.environ["XDG_STATE_HOME"])
    assert not cfg_home.exists(), "doctor bootstrap-wrote the config"
    assert not state_home.exists(), "doctor created the state dir"


# ── config check ──────────────────────────────────────────────────────────


def test_config_missing_file_reports_defaults_without_writing():
    holder: dict = {}
    rows = doctor._config_rows(None, holder)
    assert rows[0].status is CheckStatus.PASS
    assert "no config file yet" in rows[0].message
    assert "cfg" in holder
    assert not (Path(os.environ["XDG_CONFIG_HOME"]) / "archward").exists()


def test_config_unreadable_file_is_fail(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text("[general]\n")
    cfg_file.chmod(0o000)
    try:
        holder: dict = {}
        rows = doctor._config_rows(cfg_file, holder)
        assert rows[0].status is CheckStatus.FAIL
        assert "not readable" in rows[0].message
        assert "cfg" in holder  # downstream checks still get defaults
    finally:
        cfg_file.chmod(0o644)


def test_config_parse_error_is_fail(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text("this is not [ toml ===")
    rows = doctor._config_rows(cfg_file, {})
    assert rows[0].status is CheckStatus.FAIL
    assert "parse error" in rows[0].message.lower()
    assert cfg_file.read_text() == "this is not [ toml ==="  # untouched


def test_config_valid_file_is_pass(tmp_path):
    from archward.config.defaults import default_config
    from archward.config.loader import write_config

    cfg_file = tmp_path / "config.toml"
    write_config(default_config(), cfg_file)
    holder: dict = {}
    rows = doctor._config_rows(cfg_file, holder)
    assert rows[0].status is CheckStatus.PASS
    assert "valid" in rows[0].message


def test_config_directory_at_path_is_fail(tmp_path):
    cfg_dir = tmp_path / "config.toml"
    cfg_dir.mkdir()
    holder: dict = {}
    rows = doctor._config_rows(cfg_dir, holder)
    assert rows[0].status is CheckStatus.FAIL
    assert "cannot read" in rows[0].message
    assert "cfg" in holder


def test_config_dead_symlink_is_warn(tmp_path):
    link = tmp_path / "config.toml"
    link.symlink_to(tmp_path / "missing-target.toml")
    rows = doctor._config_rows(link, {})
    assert rows[0].status is CheckStatus.WARN
    assert "symlink" in rows[0].message
    assert link.is_symlink()  # untouched


def test_config_newer_schema_plus_fallback_reports_both(tmp_path):
    from archward.config.defaults import default_config
    from archward.config.loader import write_config

    cfg_file = tmp_path / "config.toml"
    write_config(default_config(), cfg_file)
    text = cfg_file.read_text()
    text = text.replace("schema_version = 1", "schema_version = 99")
    text = text.replace("min_disk_gb = 5", 'min_disk_gb = "not-an-int"')
    cfg_file.write_text(text)
    rows = doctor._config_rows(cfg_file, {})
    assert [r.status for r in rows] == [CheckStatus.WARN, CheckStatus.WARN]
    assert "newer" in rows[0].message
    assert "gates" in rows[1].message


def test_config_fallback_section_is_warn(tmp_path):
    from archward.config.defaults import default_config
    from archward.config.loader import write_config

    cfg_file = tmp_path / "config.toml"
    write_config(default_config(), cfg_file)
    text = cfg_file.read_text().replace(
        "min_disk_gb = 5", 'min_disk_gb = "not-an-int"'
    )
    assert text != cfg_file.read_text(), "fixture: expected default not found"
    cfg_file.write_text(text)
    rows = doctor._config_rows(cfg_file, {})
    assert rows[0].status is CheckStatus.WARN
    assert "gates" in rows[0].message


# ── individual checks ─────────────────────────────────────────────────────


def test_askpass_headless_warn_only_without_passwordless_sudo(monkeypatch):
    from archward.config.defaults import default_config

    monkeypatch.setattr(
        "archward.privilege.sudo.discover_askpass", lambda o: "/usr/bin/ksshaskpass"
    )
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)

    cfg = default_config()
    # headless + NOPASSWD → healthy server, no warning
    rows = doctor._askpass_rows(cfg, sudo_ok=True)
    assert all(r.status is CheckStatus.PASS for r in rows)
    # headless + password required → the askpass can't render
    rows = doctor._askpass_rows(cfg, sudo_ok=False)
    assert any(
        r.status is CheckStatus.WARN and "headless" in r.message for r in rows
    )


def test_askpass_none_is_fail(monkeypatch):
    from archward.config.defaults import default_config

    monkeypatch.setattr("archward.privilege.sudo.discover_askpass", lambda o: None)
    monkeypatch.setenv("DISPLAY", ":0")
    rows = doctor._askpass_rows(default_config(), sudo_ok=False)
    assert rows[0].status is CheckStatus.FAIL


def test_askpass_broken_override_is_warn(monkeypatch):
    from archward.config.defaults import default_config
    from archward.config.loader import merge_partial
    from archward.models.config import PrivilegeConfig

    monkeypatch.setattr(
        "archward.privilege.sudo.discover_askpass", lambda o: "/usr/bin/ksshaskpass"
    )
    monkeypatch.setenv("DISPLAY", ":0")
    cfg = merge_partial(
        default_config(), privilege=PrivilegeConfig(askpass="/nonexistent/askpass")
    )
    rows = doctor._askpass_rows(cfg, sudo_ok=True)
    assert rows[0].status is CheckStatus.WARN
    assert "fell back" in rows[0].message


def test_askpass_broken_override_and_poisoned_env_warns_twice(monkeypatch, tmp_path):
    from archward.config.defaults import default_config
    from archward.config.loader import merge_partial
    from archward.models.config import PrivilegeConfig

    fake = tmp_path / "evil-askpass"
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)
    monkeypatch.setenv("SUDO_ASKPASS", str(fake))
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(
        "archward.privilege.sudo.discover_askpass", lambda o: str(fake)
    )
    cfg = merge_partial(
        default_config(), privilege=PrivilegeConfig(askpass="/nonexistent/askpass")
    )
    rows = doctor._askpass_rows(cfg, sudo_ok=True)
    assert [r.status for r in rows] == [CheckStatus.WARN, CheckStatus.WARN]
    assert "fell back" in rows[0].message
    assert "outside /usr" in rows[1].message


def test_askpass_untrusted_env_var_is_warn(monkeypatch, tmp_path):
    from archward.config.defaults import default_config

    fake = tmp_path / "evil-askpass"
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)
    monkeypatch.setenv("SUDO_ASKPASS", str(fake))
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(
        "archward.privilege.sudo.discover_askpass", lambda o: str(fake)
    )
    rows = doctor._askpass_rows(default_config(), sudo_ok=True)
    assert rows[0].status is CheckStatus.WARN
    assert "outside /usr" in rows[0].message


def test_db_lock_fail_with_stale_detail(monkeypatch):
    monkeypatch.setattr(
        "archward.pacman.runner.check_pacman_db_lock",
        lambda: (True, "stale lock (no live process)"),
    )
    rows = doctor._db_lock_rows()
    assert rows[0].status is CheckStatus.FAIL
    assert "sudo rm /var/lib/pacman/db.lck" in rows[0].detail


def test_db_lock_live_holder_detail(monkeypatch):
    monkeypatch.setattr(
        "archward.pacman.runner.check_pacman_db_lock",
        lambda: (True, "pacman (pid 1234)"),
    )
    rows = doctor._db_lock_rows()
    assert rows[0].status is CheckStatus.FAIL
    assert "Wait for it to finish" in rows[0].detail


def test_pacman_contrib_missing_is_warn(monkeypatch):
    monkeypatch.setattr(
        doctor.shutil, "which",
        lambda b: "/usr/bin/pacman" if b == "pacman" else None,
    )
    rows = doctor._pacman_rows()
    assert rows[0].status is CheckStatus.WARN
    assert "checkupdates" in rows[0].message and "paccache" in rows[0].message


def test_distro_not_arch_is_fail(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(
        "archward.system.distro.detect_distro",
        lambda path=None: SimpleNamespace(
            id="debian", pretty_name="Debian 13", is_arch_based=False,
            detected_via="ID",
        ),
    )
    rows = doctor._distro_rows()
    assert rows[0].status is CheckStatus.FAIL


def test_sudo_branches(monkeypatch):
    import subprocess as sp

    # sudo missing entirely
    monkeypatch.setattr(doctor.shutil, "which", lambda b: None)
    holder = {"ok": False}
    rows = doctor._sudo_rows(holder)
    assert rows[0].status is CheckStatus.FAIL

    monkeypatch.setattr(doctor.shutil, "which", lambda b: "/usr/bin/sudo")

    # NOPASSWD / warm timestamp
    monkeypatch.setattr(
        doctor.subprocess, "run",
        lambda *a, **k: sp.CompletedProcess(a, 0, stdout=b"", stderr=b""),
    )
    holder = {"ok": False}
    rows = doctor._sudo_rows(holder)
    assert rows[0].status is CheckStatus.PASS
    assert holder["ok"] is True

    # cold timestamp / unknown rights
    monkeypatch.setattr(
        doctor.subprocess, "run",
        lambda *a, **k: sp.CompletedProcess(a, 1, stdout=b"", stderr=b""),
    )
    holder = {"ok": False}
    rows = doctor._sudo_rows(holder)
    assert rows[0].status is CheckStatus.PASS
    assert holder["ok"] is False
    assert "can't be verified" in rows[0].detail

    # wedged sudo
    def raise_timeout(*a, **k):
        raise sp.TimeoutExpired(cmd=a[0], timeout=5)

    monkeypatch.setattr(doctor.subprocess, "run", raise_timeout)
    rows = doctor._sudo_rows({"ok": False})
    assert rows[0].status is CheckStatus.WARN


def test_cache_tiers(monkeypatch):
    from types import SimpleNamespace

    from archward.config.defaults import default_config
    from archward.system import cache_policy as cp

    def policy(safety, hooks=()):
        return SimpleNamespace(
            safety=safety, cleaning_hooks=tuple(hooks), effective_keep=3,
            timer_state="enabled", explanation="why",
        )

    cfg = default_config()
    assert cfg.cache.warn_if_unmanaged is True

    monkeypatch.setattr(
        "archward.system.cache_policy.detect_cache_policy",
        lambda: policy(cp.RollbackSafety.DANGEROUS),
    )
    assert doctor._cache_rows(cfg)[0].status is CheckStatus.WARN

    monkeypatch.setattr(
        "archward.system.cache_policy.detect_cache_policy",
        lambda: policy(cp.RollbackSafety.BALANCED, hooks=(Path("/x/clean.hook"),)),
    )
    assert doctor._cache_rows(cfg)[0].status is CheckStatus.WARN

    monkeypatch.setattr(
        "archward.system.cache_policy.detect_cache_policy",
        lambda: policy(cp.RollbackSafety.UNMANAGED),
    )
    assert doctor._cache_rows(cfg)[0].status is CheckStatus.WARN

    monkeypatch.setattr(
        "archward.system.cache_policy.detect_cache_policy",
        lambda: policy(cp.RollbackSafety.GENEROUS),
    )
    assert doctor._cache_rows(cfg)[0].status is CheckStatus.PASS


def test_state_dirs_unwritable_fail_vs_mode_warn(tmp_path, monkeypatch):
    from archward.config.defaults import default_config

    state_root = tmp_path / "state"
    state_root.mkdir(mode=0o700)
    monkeypatch.setattr(paths_mod, "state_dir", lambda: state_root)
    cfg = default_config()

    rows = doctor._state_rows(cfg)
    assert rows[0].status is CheckStatus.PASS

    state_root.chmod(0o755)  # loose perms, still writable → WARN
    rows = doctor._state_rows(cfg)
    assert rows[0].status is CheckStatus.WARN
    assert "755" in rows[0].message

    sub = state_root / "runs"
    sub.mkdir()
    sub.chmod(0o500)  # unwritable subdir → FAIL
    try:
        rows = doctor._state_rows(cfg)
        assert rows[0].status is CheckStatus.FAIL
        assert "runs" in rows[0].message
    finally:
        sub.chmod(0o700)


def test_disk_below_minimum_is_fail(monkeypatch):
    from archward.config.defaults import default_config

    monkeypatch.setattr("archward.system.disk.free_gb", lambda p="/": 1)
    rows = doctor._disk_rows(default_config())
    assert rows[0].status is CheckStatus.FAIL

    monkeypatch.setattr("archward.system.disk.free_gb", lambda p="/": 500)
    rows = doctor._disk_rows(default_config())
    assert rows[0].status is CheckStatus.PASS


def test_services_skipped_without_systemctl(monkeypatch):
    from archward.config.defaults import default_config
    from archward.config.loader import merge_partial
    from archward.models.config import ServicesConfig

    cfg = merge_partial(
        default_config(),
        services=ServicesConfig(to_verify=("foo.service",)),
    )
    monkeypatch.setattr(doctor.shutil, "which", lambda b: None)
    rows = doctor._services_rows(cfg)
    assert rows[0].status is CheckStatus.SKIPPED
    assert "systemctl" in rows[0].message


def test_services_stale_entries_warn(monkeypatch):
    from archward.config.defaults import default_config
    from archward.config.loader import merge_partial
    from archward.models.config import ServicesConfig

    cfg = merge_partial(
        default_config(),
        services=ServicesConfig(to_verify=("good.service", "gone.service")),
    )
    monkeypatch.setattr(doctor.shutil, "which", lambda b: f"/usr/bin/{b}")
    monkeypatch.setattr(
        "archward.config.detect.detect_stale_services",
        lambda cfg: ("gone.service",),
    )
    rows = doctor._services_rows(cfg)
    assert rows[0].status is CheckStatus.WARN
    assert "gone.service" in rows[0].message


def test_aur_skip_true_does_not_warn(monkeypatch):
    from archward.config.defaults import default_config
    from archward.config.loader import merge_partial
    from archward.models.config import AurConfig

    monkeypatch.setattr(
        "archward.config.detect.detect_aur_helper", lambda pref: None
    )
    cfg = merge_partial(default_config(), aur=AurConfig(enabled=True, skip=True))
    rows = doctor._aur_rows(cfg)
    assert rows[0].status is CheckStatus.PASS

    cfg = merge_partial(default_config(), aur=AurConfig(enabled=True, skip=False))
    rows = doctor._aur_rows(cfg)
    assert rows[0].status is CheckStatus.WARN


def test_gui_stack_skipped_when_pyside_missing(monkeypatch):
    import importlib.util

    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    rows = doctor._gui_rows()
    assert rows[0].status is CheckStatus.SKIPPED


def test_gui_stack_platform_failure_is_fail(monkeypatch):
    import importlib.util

    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())
    monkeypatch.setenv("DISPLAY", ":0")

    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv[-1])
        code = argv[-1]
        rc = 0 if code == "import PySide6.QtWidgets" else 134
        return subprocess.CompletedProcess(
            argv, rc, stdout="",
            stderr="qt.qpa.plugin: could not load the Qt platform plugin",
        )

    monkeypatch.setattr(doctor.subprocess, "run", fake_run)
    rows = doctor._gui_rows()
    assert len(calls) == 2  # import probe passed, platform probe ran
    assert rows[0].status is CheckStatus.FAIL
    assert "platform" in rows[0].message


def test_gui_stack_headless_skips_platform_probe(monkeypatch):
    import importlib.util

    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)

    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv[-1])
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(doctor.subprocess, "run", fake_run)
    rows = doctor._gui_rows()
    assert calls == ["import PySide6.QtWidgets"]
    assert rows[0].status is CheckStatus.PASS
    assert "headless" in rows[0].message


def test_last_run_no_records_is_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(paths_mod, "state_dir", lambda: tmp_path / "state")
    rows = doctor._lastrun_rows()
    assert rows[0].status is CheckStatus.SKIPPED


def test_last_run_corrupt_newest_is_warn(tmp_path, monkeypatch):
    monkeypatch.setattr(paths_mod, "state_dir", lambda: tmp_path)
    runs = tmp_path / "runs"
    runs.mkdir(parents=True)
    (runs / "2026-08-17_120000.json").write_text("{corrupt")
    rows = doctor._lastrun_rows()
    assert rows[0].status is CheckStatus.WARN
    assert "unreadable" in rows[0].message


def test_last_run_failed_tag_is_warn(tmp_path, monkeypatch):
    monkeypatch.setattr(paths_mod, "state_dir", lambda: tmp_path)
    runs = tmp_path / "runs"
    runs.mkdir(parents=True)
    doc = {
        "run_id": "2026-08-17_120000",
        "started": "2026-08-17T12:00:00+00:00",
        "result": {"tag": "RESULT:UPDATE_FAILED"},
    }
    (runs / "2026-08-17_120000.json").write_text(json.dumps(doc))
    rows = doctor._lastrun_rows()
    assert rows[0].status is CheckStatus.WARN
    assert "RESULT:UPDATE_FAILED" in rows[0].message


def test_root_guard(monkeypatch):
    monkeypatch.setattr(doctor.os, "geteuid", lambda: 0)
    rows = doctor._root_rows()
    assert rows[0].status is CheckStatus.WARN
    monkeypatch.setattr(doctor.os, "geteuid", lambda: 1000)
    assert doctor._root_rows() == []


# ── --json contract ───────────────────────────────────────────────────────


def test_json_output_shape(monkeypatch, capsys):
    _quiet_checks(monkeypatch, _disk_rows=[
        doctor.CheckRow("disk-space", CheckStatus.FAIL, "full", "hint")
    ])
    rc = doctor.cmd_doctor(_args(json_flag=True), None)
    doc = json.loads(capsys.readouterr().out)
    assert doc["schema_version"] == 1
    assert isinstance(doc["checks"], list)
    row = next(c for c in doc["checks"] if c["name"] == "disk-space")
    assert row == {
        "name": "disk-space", "status": "fail",
        "message": "full", "detail": "hint",
    }
    statuses = {c["status"] for c in doc["checks"]}
    assert statuses <= {"pass", "warn", "fail", "skipped"}
    assert rc == 1


# ── CLI dispatch ──────────────────────────────────────────────────────────


def test_cli_dispatch_routes_to_doctor(monkeypatch):
    from archward import cli

    called = {}

    def fake_cmd(args, config_path):
        called["json"] = args.json
        return 0

    monkeypatch.setattr("archward.cli_subcommands.doctor.cmd_doctor", fake_cmd)
    rc = cli.main(["doctor", "--json"])
    assert rc == 0
    assert called == {"json": True}
