"""Tests for v0.4.3 part 3 — snapshot list/show/prune subcommands."""

from __future__ import annotations

from argparse import Namespace
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from archward.cli_subcommands import snapshot as cmd


def _seed_snapshot(snap_dir: Path, snap_id: str, ts_offset_sec: int = 0) -> Path:
    p = snap_dir / snap_id
    p.mkdir(parents=True)
    ts = int(datetime.now().timestamp()) - ts_offset_sec
    (p / ".timestamp").write_text(f"{ts}\n")
    (p / "system").mkdir()
    (p / "system" / "kernel-running.txt").write_text("6.13.4-arch1-1\n")
    (p / "system" / "os-release.txt").write_text("ID=endeavouros\n")
    (p / "configs").mkdir()
    (p / "configs" / "pacman.conf").write_text("# stub\n")
    (p / "packages").mkdir()
    (p / "packages" / "critical.txt").write_text(
        "=== Critical package versions pre-update ===\n"
        "glibc: 2.42-3\nlinux: 6.13.4-arch1-1\n"
    )
    return p


def _fake_cfg(snap_dir: Path) -> MagicMock:
    cfg = MagicMock()
    cfg.general.snapshot_dir = snap_dir
    cfg.general.keep_snapshots = 10
    cfg.risk.kernel_patterns = ("linux", "linux-*")
    cfg.risk.kernel_pattern_exclude = ()
    return cfg


def _patch(monkeypatch, snap_dir: Path) -> None:
    monkeypatch.setattr(cmd, "build_config", lambda *a, **k: _fake_cfg(snap_dir))


# ── list ──────────────────────────────────────────────────────────────


def test_list_empty(tmp_path, monkeypatch, capsys) -> None:
    _patch(monkeypatch, tmp_path / "snapshots")
    args = Namespace(limit=20, all=False)
    code = cmd.cmd_list(args, None)
    assert code == 0
    assert "no snapshots" in capsys.readouterr().out


def test_list_with_snapshots_shows_newest_first(tmp_path, monkeypatch, capsys) -> None:
    snap_dir = tmp_path / "snapshots"
    _seed_snapshot(snap_dir, "2026-05-13_120000", ts_offset_sec=86400 * 2)
    _seed_snapshot(snap_dir, "2026-05-14_120000", ts_offset_sec=86400)
    _seed_snapshot(snap_dir, "2026-05-15_120000", ts_offset_sec=3600)
    _patch(monkeypatch, snap_dir)

    args = Namespace(limit=20, all=False)
    cmd.cmd_list(args, None)
    out = capsys.readouterr().out
    pos_15 = out.find("2026-05-15_120000")
    pos_14 = out.find("2026-05-14_120000")
    pos_13 = out.find("2026-05-13_120000")
    assert pos_15 < pos_14 < pos_13, f"snapshots not newest-first:\n{out}"


def test_list_limit_truncates(tmp_path, monkeypatch, capsys) -> None:
    snap_dir = tmp_path / "snapshots"
    for i in range(5):
        _seed_snapshot(snap_dir, f"2026-05-1{i}_120000", ts_offset_sec=86400 * i)
    _patch(monkeypatch, snap_dir)

    args = Namespace(limit=2, all=False)
    cmd.cmd_list(args, None)
    out = capsys.readouterr().out
    assert "and 3 older" in out


def test_list_all_shows_everything(tmp_path, monkeypatch, capsys) -> None:
    snap_dir = tmp_path / "snapshots"
    for i in range(5):
        _seed_snapshot(snap_dir, f"2026-05-1{i}_120000", ts_offset_sec=86400 * i)
    _patch(monkeypatch, snap_dir)

    args = Namespace(limit=2, all=True)
    cmd.cmd_list(args, None)
    out = capsys.readouterr().out
    assert "older" not in out
    # All five present in output.
    for i in range(5):
        assert f"2026-05-1{i}_120000" in out


# ── show ──────────────────────────────────────────────────────────────


def test_show_missing_returns_3(tmp_path, monkeypatch, capsys) -> None:
    _patch(monkeypatch, tmp_path)
    args = Namespace(snapshot_id="does-not-exist")
    code = cmd.cmd_show(args, None)
    assert code == 3
    assert "not found" in capsys.readouterr().err


def test_show_renders_meta_and_packages(tmp_path, monkeypatch, capsys) -> None:
    snap_dir = tmp_path / "snapshots"
    _seed_snapshot(snap_dir, "2026-05-15_120000")
    _patch(monkeypatch, snap_dir)

    args = Namespace(snapshot_id="2026-05-15_120000")
    code = cmd.cmd_show(args, None)
    assert code == 0
    out = capsys.readouterr().out
    assert "2026-05-15_120000" in out
    assert "endeavouros" in out
    assert "6.13.4-arch1-1" in out
    assert "glibc" in out  # from critical.txt


# ── prune ─────────────────────────────────────────────────────────────


def test_prune_nothing_to_do(tmp_path, monkeypatch, capsys) -> None:
    snap_dir = tmp_path / "snapshots"
    _seed_snapshot(snap_dir, "2026-05-15_120000")
    _patch(monkeypatch, snap_dir)

    args = Namespace(keep=10, yes=False)
    code = cmd.cmd_prune(args, None)
    assert code == 0
    assert "nothing to prune" in capsys.readouterr().out


def test_prune_with_yes_skips_confirm(tmp_path, monkeypatch, capsys) -> None:
    snap_dir = tmp_path / "snapshots"
    for i in range(5):
        _seed_snapshot(snap_dir, f"2026-05-1{i}_120000", ts_offset_sec=86400 * i)
    _patch(monkeypatch, snap_dir)

    args = Namespace(keep=2, yes=True)
    code = cmd.cmd_prune(args, None)
    assert code == 0

    remaining = [p for p in snap_dir.iterdir() if p.is_dir()]
    assert len(remaining) == 2


def test_prune_declined_at_prompt(tmp_path, monkeypatch, capsys) -> None:
    """Without --yes, a `n` answer aborts the prune."""
    snap_dir = tmp_path / "snapshots"
    for i in range(5):
        _seed_snapshot(snap_dir, f"2026-05-1{i}_120000", ts_offset_sec=86400 * i)
    _patch(monkeypatch, snap_dir)
    monkeypatch.setattr("builtins.input", lambda *a, **k: "n")

    args = Namespace(keep=2, yes=False)
    code = cmd.cmd_prune(args, None)
    assert code == 0
    assert "aborted" in capsys.readouterr().out

    remaining = [p for p in snap_dir.iterdir() if p.is_dir()]
    assert len(remaining) == 5


def test_prune_uses_cfg_default_keep(tmp_path, monkeypatch, capsys) -> None:
    """--keep unset means use cfg.general.keep_snapshots."""
    snap_dir = tmp_path / "snapshots"
    for i in range(5):
        _seed_snapshot(snap_dir, f"2026-05-1{i}_120000", ts_offset_sec=86400 * i)

    cfg = MagicMock()
    cfg.general.snapshot_dir = snap_dir
    cfg.general.keep_snapshots = 3
    cfg.risk.kernel_patterns = ()
    cfg.risk.kernel_pattern_exclude = ()
    monkeypatch.setattr(cmd, "build_config", lambda *a, **k: cfg)

    args = Namespace(keep=None, yes=True)
    cmd.cmd_prune(args, None)
    remaining = [p for p in snap_dir.iterdir() if p.is_dir()]
    assert len(remaining) == 3
