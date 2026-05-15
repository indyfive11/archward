"""Tests for v0.4.3 part 2 — the `archward verify` subcommand.

Mocks `run_verify` so we don't shell out for real systemd/kernel checks;
the integration of `run_verify` is covered by test_verify_plugins.py and
the verify-specific test files. Here we verify the CLI plumbing:
snapshot resolution, missing-snapshot exit codes, exit-code mapping
based on the derived RESULT tag.
"""

from __future__ import annotations

from argparse import Namespace
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from archward.cli_subcommands import verify as cmd
from archward.models.verify import CheckStatus, VerifyCheck, VerifyResult


def _seed_snapshot(snap_dir: Path, snap_id: str = "2026-05-15_134116") -> Path:
    """Drop a minimal *complete* snapshot fixture into snap_dir / snap_id.

    Complete per v0.4.4 F4 validate_snapshot(): .timestamp + non-empty
    packages/all.txt + packages/critical.txt + configs/.
    """
    p = snap_dir / snap_id
    p.mkdir(parents=True)
    (p / ".timestamp").write_text(f"{int(datetime.now().timestamp())}\n")
    (p / "system").mkdir()
    (p / "system" / "kernel-running.txt").write_text("6.13.4-arch1-1\n")
    (p / "system" / "os-release.txt").write_text("ID=endeavouros\n")
    pkg = p / "packages"
    pkg.mkdir()
    (pkg / "all.txt").write_text("bash 5.2-1\nglibc 2.39-1\n")
    (pkg / "critical.txt").write_text("=== Critical ===\nglibc: 2.39-1\n")
    (p / "configs").mkdir()
    return p


def _fake_cfg(snap_dir: Path) -> MagicMock:
    cfg = MagicMock()
    cfg.general.snapshot_dir = snap_dir
    cfg.verify.enabled = True
    return cfg


def _patch_build_cfg(monkeypatch, snap_dir: Path) -> None:
    """Patch build_config + build_sudo_strategy in the verify module."""
    monkeypatch.setattr(cmd, "build_config", lambda *a, **k: _fake_cfg(snap_dir))
    monkeypatch.setattr(cmd, "build_sudo_strategy", lambda *a, **k: MagicMock())
    monkeypatch.setattr(cmd.notify, "notify_completion", lambda *a, **k: None)


def test_no_snapshots_returns_3(tmp_path, monkeypatch, capsys) -> None:
    """archward verify on an empty snapshot dir prints helpful message + exits 3."""
    _patch_build_cfg(monkeypatch, tmp_path)
    args = Namespace(snapshot=None)
    code = cmd.cmd_verify(args, None)
    assert code == 3
    err = capsys.readouterr().err
    assert "no snapshots" in err


def test_explicit_snapshot_not_found_returns_3(tmp_path, monkeypatch, capsys) -> None:
    """--snapshot pointing at a non-existent ID exits 3."""
    _patch_build_cfg(monkeypatch, tmp_path)
    args = Namespace(snapshot="nope-not-real")
    code = cmd.cmd_verify(args, None)
    assert code == 3
    assert "not found" in capsys.readouterr().err


def test_happy_path_returns_0(tmp_path, monkeypatch) -> None:
    """A snapshot with no FAILs maps to RESULT:SUCCESS → exit 0."""
    snap_dir = tmp_path / "snapshots"
    _seed_snapshot(snap_dir)
    _patch_build_cfg(monkeypatch, snap_dir)

    fake_verify = VerifyResult(
        checks=(VerifyCheck(
            bucket="universal", name="kernel",
            status=CheckStatus.PASS, message="kernel matches",
        ),),
        fail_count=0, warn_count=0, reboot_needed=False,
    )
    monkeypatch.setattr(cmd, "run_verify", lambda *a, **k: fake_verify)

    args = Namespace(snapshot=None)
    code = cmd.cmd_verify(args, None)
    assert code == 0


def test_verify_failed_maps_to_exit_1(tmp_path, monkeypatch) -> None:
    snap_dir = tmp_path / "snapshots"
    _seed_snapshot(snap_dir)
    _patch_build_cfg(monkeypatch, snap_dir)

    fake_verify = VerifyResult(
        checks=(VerifyCheck(
            bucket="services", name="sshd.service",
            status=CheckStatus.FAIL, message="inactive",
        ),),
        fail_count=1, warn_count=0, reboot_needed=False,
    )
    monkeypatch.setattr(cmd, "run_verify", lambda *a, **k: fake_verify)

    args = Namespace(snapshot=None)
    code = cmd.cmd_verify(args, None)
    assert code == 1


def test_reboot_needed_maps_to_exit_2(tmp_path, monkeypatch) -> None:
    snap_dir = tmp_path / "snapshots"
    _seed_snapshot(snap_dir)
    _patch_build_cfg(monkeypatch, snap_dir)

    fake_verify = VerifyResult(
        checks=(VerifyCheck(
            bucket="universal", name="kernel",
            status=CheckStatus.WARN, message="kernel mismatch",
        ),),
        fail_count=0, warn_count=1, reboot_needed=True,
    )
    monkeypatch.setattr(cmd, "run_verify", lambda *a, **k: fake_verify)

    args = Namespace(snapshot=None)
    code = cmd.cmd_verify(args, None)
    assert code == 2


def test_explicit_snapshot_id_used(tmp_path, monkeypatch) -> None:
    """--snapshot <id> selects that specific snapshot, not the latest."""
    snap_dir = tmp_path / "snapshots"
    _seed_snapshot(snap_dir, "2026-05-14_100000")
    target = _seed_snapshot(snap_dir, "2026-05-15_120000")

    _patch_build_cfg(monkeypatch, snap_dir)

    captured = {}

    def fake_run(cfg, snapshot, bus, *, config_path):
        captured["snapshot_path"] = snapshot.meta.path
        return VerifyResult(
            checks=(), fail_count=0, warn_count=0, reboot_needed=False,
        )

    monkeypatch.setattr(cmd, "run_verify", fake_run)

    args = Namespace(snapshot="2026-05-15_120000")
    code = cmd.cmd_verify(args, None)
    assert code == 0
    assert captured["snapshot_path"] == target


def test_incomplete_snapshot_returns_3(tmp_path, monkeypatch, capsys) -> None:
    """A directory that exists but lacks .timestamp is rejected."""
    snap_dir = tmp_path / "snapshots"
    broken = snap_dir / "2026-05-15_broken"
    broken.mkdir(parents=True)
    # NO .timestamp written.

    _patch_build_cfg(monkeypatch, snap_dir)

    args = Namespace(snapshot="2026-05-15_broken")
    code = cmd.cmd_verify(args, None)
    assert code == 3
    assert "incomplete" in capsys.readouterr().err
