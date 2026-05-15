"""Tests for v0.4.3 part 4 — rollback subcommands.

All four subcommands are tested with `run_capture` mocked so the tests
never touch real /etc, /var/cache/pacman/pkg, or pacman. The key paths
under test are:

  - Snapshot resolution (missing → exit 3).
  - Filename → live-target mapping in `rollback config`.
  - Boot-critical refusal in `rollback package` without --confirm.
  - Boot-critical YES gate in `rollback package` with --confirm.
  - Stdin Y/N confirmation in `rollback all-configs`.
  - Plan + boot-critical refusal in `rollback all-packages`.
  - --yes flag bypasses the casual confirm but NOT the YES gate.
"""

from __future__ import annotations

from argparse import Namespace
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from archward.cli_subcommands import rollback as cmd
from archward.pipeline.rollback import BulkResult, RollbackOp, RollbackResult


def _seed_snapshot(snap_dir: Path, snap_id: str = "2026-05-15_134116") -> Path:
    p = snap_dir / snap_id
    p.mkdir(parents=True)
    (p / ".timestamp").write_text(f"{int(datetime.now().timestamp())}\n")
    cfg_dir = p / "configs"
    cfg_dir.mkdir()
    (cfg_dir / "mirrorlist").write_text("Server = https://example.org/\n")
    (cfg_dir / "pacman.conf").write_text("# stub\n")
    pkg_dir = p / "packages"
    pkg_dir.mkdir()
    (pkg_dir / "critical.txt").write_text(
        "=== Critical package versions pre-update ===\n"
        "glibc: 2.42-2\nnvidia: 575.64.05-1\n"
    )
    return p


def _fake_cfg(snap_dir: Path) -> MagicMock:
    cfg = MagicMock()
    cfg.general.snapshot_dir = snap_dir
    cfg.risk.kernel_patterns = ()
    cfg.risk.kernel_pattern_exclude = ()
    return cfg


def _patch_cfg(monkeypatch, snap_dir: Path) -> None:
    monkeypatch.setattr(cmd, "build_config", lambda *a, **k: _fake_cfg(snap_dir))
    monkeypatch.setattr(cmd, "build_sudo_strategy", lambda *a, **k: MagicMock())


# ── rollback config ──────────────────────────────────────────────────


def test_config_missing_snapshot_returns_3(tmp_path, monkeypatch, capsys) -> None:
    _patch_cfg(monkeypatch, tmp_path)
    args = Namespace(snapshot_id="nope", filename="mirrorlist")
    code = cmd.cmd_config(args, None)
    assert code == 3
    assert "not found" in capsys.readouterr().err


def test_config_unknown_filename_returns_2(tmp_path, monkeypatch, capsys) -> None:
    snap_dir = tmp_path / "snapshots"
    _seed_snapshot(snap_dir)
    _patch_cfg(monkeypatch, snap_dir)

    args = Namespace(snapshot_id="2026-05-15_134116", filename="not-captured")
    code = cmd.cmd_config(args, None)
    assert code == 2
    assert "no captured file" in capsys.readouterr().err


def test_config_happy_path(tmp_path, monkeypatch, capsys) -> None:
    snap_dir = tmp_path / "snapshots"
    _seed_snapshot(snap_dir)
    _patch_cfg(monkeypatch, snap_dir)

    captured_ops: list[RollbackOp] = []

    def fake_restore(op, snap_file, strategy):
        captured_ops.append(op)
        return RollbackResult(op, True, f"restored {op.target}")

    monkeypatch.setattr(cmd, "restore_config", fake_restore)

    args = Namespace(snapshot_id="2026-05-15_134116", filename="mirrorlist")
    code = cmd.cmd_config(args, None)
    assert code == 0
    assert len(captured_ops) == 1
    assert captured_ops[0].target == "/etc/pacman.d/mirrorlist"


# ── rollback package ─────────────────────────────────────────────────


def test_package_unknown_returns_2(tmp_path, monkeypatch, capsys) -> None:
    snap_dir = tmp_path / "snapshots"
    _seed_snapshot(snap_dir)
    _patch_cfg(monkeypatch, snap_dir)

    args = Namespace(
        snapshot_id="2026-05-15_134116",
        package="not-in-snapshot",
        confirm_boot_critical=False,
    )
    code = cmd.cmd_package(args, None)
    assert code == 2
    assert "was not captured" in capsys.readouterr().err


def test_package_boot_critical_refused_without_flag(tmp_path, monkeypatch, capsys) -> None:
    snap_dir = tmp_path / "snapshots"
    _seed_snapshot(snap_dir)
    _patch_cfg(monkeypatch, snap_dir)

    args = Namespace(
        snapshot_id="2026-05-15_134116",
        package="glibc",
        confirm_boot_critical=False,
    )
    code = cmd.cmd_package(args, None)
    assert code == 2
    err = capsys.readouterr().err
    assert "boot-critical" in err
    assert "--confirm-boot-critical" in err


def test_package_boot_critical_yes_gate_aborts_on_no(tmp_path, monkeypatch, capsys) -> None:
    """--confirm-boot-critical alone isn't enough — must also type YES on stdin."""
    snap_dir = tmp_path / "snapshots"
    _seed_snapshot(snap_dir)
    _patch_cfg(monkeypatch, snap_dir)
    # User types `y` (lower) which is NOT case-sensitive "YES".
    monkeypatch.setattr("builtins.input", lambda *a, **k: "y")

    downgrade_called = {"n": 0}
    monkeypatch.setattr(
        cmd, "downgrade_package",
        lambda op, s: downgrade_called.__setitem__("n", downgrade_called["n"] + 1) or RollbackResult(op, True, "ok"),
    )

    args = Namespace(
        snapshot_id="2026-05-15_134116",
        package="glibc",
        confirm_boot_critical=True,
    )
    code = cmd.cmd_package(args, None)
    assert code == 0  # aborted-by-user is exit 0
    assert downgrade_called["n"] == 0  # downgrade never ran


def test_package_boot_critical_proceeds_with_yes(tmp_path, monkeypatch) -> None:
    snap_dir = tmp_path / "snapshots"
    _seed_snapshot(snap_dir)
    _patch_cfg(monkeypatch, snap_dir)
    monkeypatch.setattr("builtins.input", lambda *a, **k: "YES")

    captured = []
    monkeypatch.setattr(
        cmd, "downgrade_package",
        lambda op, s: captured.append(op) or RollbackResult(op, True, "downgraded"),
    )

    args = Namespace(
        snapshot_id="2026-05-15_134116",
        package="glibc",
        confirm_boot_critical=True,
    )
    code = cmd.cmd_package(args, None)
    assert code == 0
    assert len(captured) == 1
    assert captured[0].target == "glibc"
    assert captured[0].to_version == "2.42-2"


def test_package_non_critical_no_yes_gate(tmp_path, monkeypatch) -> None:
    """Non-boot-critical packages don't need confirm_boot_critical or YES."""
    snap_dir = tmp_path / "snapshots"
    _seed_snapshot(snap_dir)
    _patch_cfg(monkeypatch, snap_dir)

    captured = []
    monkeypatch.setattr(
        cmd, "downgrade_package",
        lambda op, s: captured.append(op) or RollbackResult(op, True, "downgraded"),
    )

    args = Namespace(
        snapshot_id="2026-05-15_134116",
        package="nvidia",
        confirm_boot_critical=False,
    )
    code = cmd.cmd_package(args, None)
    assert code == 0
    assert captured[0].target == "nvidia"


# ── rollback all-configs ─────────────────────────────────────────────


def test_all_configs_y_proceeds(tmp_path, monkeypatch, capsys) -> None:
    snap_dir = tmp_path / "snapshots"
    _seed_snapshot(snap_dir)
    _patch_cfg(monkeypatch, snap_dir)
    monkeypatch.setattr("builtins.input", lambda *a, **k: "y")
    monkeypatch.setattr(cmd, "take_snapshot", lambda *a, **k: MagicMock(meta=MagicMock(snapshot_id="pre-rb")))
    monkeypatch.setattr(
        cmd, "restore_all_configs",
        lambda *a, **k: BulkResult(success=True, message="restored 2/2", changed=(), skipped=()),
    )

    args = Namespace(snapshot_id="2026-05-15_134116", yes=False)
    code = cmd.cmd_all_configs(args, None)
    assert code == 0


def test_all_configs_yes_flag_skips_prompt(tmp_path, monkeypatch, capsys) -> None:
    snap_dir = tmp_path / "snapshots"
    _seed_snapshot(snap_dir)
    _patch_cfg(monkeypatch, snap_dir)

    # If input() were called, this would block — proves --yes bypassed it.
    monkeypatch.setattr("builtins.input", lambda *a, **k: (_ for _ in ()).throw(AssertionError("input called")))
    monkeypatch.setattr(cmd, "take_snapshot", lambda *a, **k: MagicMock(meta=MagicMock(snapshot_id="pre-rb")))
    monkeypatch.setattr(
        cmd, "restore_all_configs",
        lambda *a, **k: BulkResult(success=True, message="ok", changed=(), skipped=()),
    )

    args = Namespace(snapshot_id="2026-05-15_134116", yes=True)
    code = cmd.cmd_all_configs(args, None)
    assert code == 0


# ── rollback all-packages ────────────────────────────────────────────


def test_all_packages_boot_critical_refused_without_flag(tmp_path, monkeypatch, capsys) -> None:
    snap_dir = tmp_path / "snapshots"
    _seed_snapshot(snap_dir)
    _patch_cfg(monkeypatch, snap_dir)

    cache_path = tmp_path / "fake.pkg.tar.zst"
    cache_path.touch()
    monkeypatch.setattr(
        cmd, "plan_bulk_package_apply",
        lambda *a, **k: (
            [("glibc", "2.42-3", "2.42-2", cache_path)],
            [],
        ),
    )

    args = Namespace(
        snapshot_id="2026-05-15_134116",
        confirm_boot_critical=False,
    )
    code = cmd.cmd_all_packages(args, None)
    assert code == 2
    err = capsys.readouterr().err
    assert "boot-critical packages in plan" in err
    assert "--confirm-boot-critical" in err


def test_all_packages_nothing_to_do(tmp_path, monkeypatch, capsys) -> None:
    snap_dir = tmp_path / "snapshots"
    _seed_snapshot(snap_dir)
    _patch_cfg(monkeypatch, snap_dir)
    monkeypatch.setattr(cmd, "plan_bulk_package_apply", lambda *a, **k: ([], []))

    args = Namespace(
        snapshot_id="2026-05-15_134116",
        confirm_boot_critical=False,
    )
    code = cmd.cmd_all_packages(args, None)
    assert code == 0
    assert "nothing to apply" in capsys.readouterr().out


def test_all_packages_proceeds_with_yes_on_boot_critical(tmp_path, monkeypatch, capsys) -> None:
    snap_dir = tmp_path / "snapshots"
    _seed_snapshot(snap_dir)
    _patch_cfg(monkeypatch, snap_dir)

    cache_path = tmp_path / "fake.pkg.tar.zst"
    cache_path.touch()
    monkeypatch.setattr(
        cmd, "plan_bulk_package_apply",
        lambda *a, **k: (
            [("glibc", "2.42-3", "2.42-2", cache_path)],
            [],
        ),
    )
    monkeypatch.setattr("builtins.input", lambda *a, **k: "YES")
    monkeypatch.setattr(cmd, "take_snapshot", lambda *a, **k: MagicMock(meta=MagicMock(snapshot_id="pre-rb")))
    monkeypatch.setattr(
        cmd, "apply_all_packages",
        lambda *a, **k: BulkResult(success=True, message="ok", changed=(), skipped=()),
    )

    args = Namespace(
        snapshot_id="2026-05-15_134116",
        confirm_boot_critical=True,
    )
    code = cmd.cmd_all_packages(args, None)
    assert code == 0
