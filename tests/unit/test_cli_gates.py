"""Tests for `archward gates` — standalone pre-flight + gate checks."""

from __future__ import annotations

from argparse import Namespace
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from archward.cli import _build_parser
from archward.cli_subcommands import gates as cmd
from archward.models.gate import GateResult, GateStatus


# ── parser ────────────────────────────────────────────────────────────────


def test_gates_parser() -> None:
    args = _build_parser().parse_args(["gates"])
    assert args.command == "gates"
    assert args.snapshot is None


def test_gates_parser_with_snapshot() -> None:
    args = _build_parser().parse_args(["gates", "--snapshot", "2026-05-19_093016"])
    assert args.snapshot == "2026-05-19_093016"


# ── helpers ───────────────────────────────────────────────────────────────


def _fake_cfg(snap_dir: Path) -> MagicMock:
    cfg = MagicMock()
    cfg.general.snapshot_dir = snap_dir
    cfg.gates.min_disk_gb = 5
    cfg.gates.allow_override = True
    return cfg


def _pass_result(name: str) -> GateResult:
    return GateResult(name=name, status=GateStatus.PASS, message="ok")


def _warn_result(name: str) -> GateResult:
    return GateResult(name=name, status=GateStatus.WARN, message="warning")


def _fail_result(name: str) -> GateResult:
    return GateResult(name=name, status=GateStatus.FAIL, message="failed")


def _seed_snapshot(snap_dir: Path, snap_id: str = "2026-05-15_120000") -> Path:
    p = snap_dir / snap_id
    p.mkdir(parents=True)
    ts = int(datetime.now().timestamp())
    (p / ".timestamp").write_text(f"{ts}\n")
    (p / "configs").mkdir()
    (p / "packages").mkdir()
    (p / "packages" / "all.txt").write_text("glibc 2.42-1\n")
    (p / "system").mkdir()
    (p / "system" / "os-release.txt").write_text("ID=arch\n")
    (p / "system" / "kernel-running.txt").write_text("6.9.0\n")
    return p


# ── cmd_gates exit codes ──────────────────────────────────────────────────


def test_gates_returns_0_all_pass(tmp_path, monkeypatch, capsys) -> None:
    snap_dir = tmp_path / "snapshots"
    _seed_snapshot(snap_dir)
    monkeypatch.setattr(cmd, "build_config", lambda *a, **k: _fake_cfg(snap_dir))
    monkeypatch.setattr(cmd, "preflight_checks", lambda cfg, bus: [_pass_result("db-lock")])
    monkeypatch.setattr(cmd, "run_gates", lambda cfg, snap, bus: [_pass_result("disk-space")])

    args = Namespace(snapshot=None)
    assert cmd.cmd_gates(args, None) == 0


def test_gates_returns_1_on_fail(tmp_path, monkeypatch, capsys) -> None:
    snap_dir = tmp_path / "snapshots"
    _seed_snapshot(snap_dir)
    monkeypatch.setattr(cmd, "build_config", lambda *a, **k: _fake_cfg(snap_dir))
    monkeypatch.setattr(cmd, "preflight_checks", lambda cfg, bus: [_fail_result("db-lock")])
    monkeypatch.setattr(cmd, "run_gates", lambda cfg, snap, bus: [_pass_result("disk-space")])

    args = Namespace(snapshot=None)
    assert cmd.cmd_gates(args, None) == 1


def test_gates_returns_2_on_warn_only(tmp_path, monkeypatch, capsys) -> None:
    snap_dir = tmp_path / "snapshots"
    _seed_snapshot(snap_dir)
    monkeypatch.setattr(cmd, "build_config", lambda *a, **k: _fake_cfg(snap_dir))
    monkeypatch.setattr(cmd, "preflight_checks", lambda cfg, bus: [_warn_result("cache-safety")])
    monkeypatch.setattr(cmd, "run_gates", lambda cfg, snap, bus: [_pass_result("disk-space")])

    args = Namespace(snapshot=None)
    assert cmd.cmd_gates(args, None) == 2


def test_gates_skips_snapshot_age_when_no_snapshots(tmp_path, monkeypatch, capsys) -> None:
    snap_dir = tmp_path / "empty_snaps"
    snap_dir.mkdir()
    monkeypatch.setattr(cmd, "build_config", lambda *a, **k: _fake_cfg(snap_dir))
    monkeypatch.setattr(cmd, "preflight_checks", lambda cfg, bus: [_pass_result("db-lock")])

    import archward.system.disk as _disk_mod
    monkeypatch.setattr(_disk_mod, "free_gb", lambda *a, **k: 50)

    args = Namespace(snapshot=None)
    code = cmd.cmd_gates(args, None)
    out = capsys.readouterr().out
    assert "SKIP" in out
    assert "no snapshots found" in out
    assert code == 0


def test_gates_inline_disk_fail_when_no_snapshots(tmp_path, monkeypatch, capsys) -> None:
    snap_dir = tmp_path / "empty_snaps"
    snap_dir.mkdir()
    monkeypatch.setattr(cmd, "build_config", lambda *a, **k: _fake_cfg(snap_dir))
    monkeypatch.setattr(cmd, "preflight_checks", lambda cfg, bus: [_pass_result("db-lock")])

    import archward.system.disk as _disk_mod
    monkeypatch.setattr(_disk_mod, "free_gb", lambda *a, **k: 2)  # below 5 GB min

    args = Namespace(snapshot=None)
    code = cmd.cmd_gates(args, None)
    out = capsys.readouterr().out
    assert "FAIL" in out
    assert code == 1
