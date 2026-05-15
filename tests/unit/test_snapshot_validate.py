"""Tests for v0.4.4 F4 — snapshot.validate_snapshot() + CLI refusal.

validate_snapshot returns the load-bearing sections a rollback needs
that are missing/empty. The CLI rollback resolver refuses (exit 3)
up front when any are absent, instead of failing cryptically half-way
through a restore.
"""

from __future__ import annotations

from argparse import Namespace
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

from archward.cli_subcommands import rollback as rb
from archward.pipeline.snapshot import validate_snapshot


def _complete(p: Path) -> Path:
    p.mkdir(parents=True)
    (p / ".timestamp").write_text(f"{int(datetime.now().timestamp())}\n")
    pkg = p / "packages"
    pkg.mkdir()
    (pkg / "all.txt").write_text("bash 5.2-1\n")
    (pkg / "critical.txt").write_text("=== Critical ===\n")
    (p / "configs").mkdir()
    return p


def test_complete_snapshot_has_no_problems(tmp_path) -> None:
    assert validate_snapshot(_complete(tmp_path / "snap")) == []


def test_missing_timestamp_flagged(tmp_path) -> None:
    p = _complete(tmp_path / "snap")
    (p / ".timestamp").unlink()
    probs = validate_snapshot(p)
    assert any(".timestamp" in x for x in probs)


def test_unreadable_timestamp_flagged(tmp_path) -> None:
    p = _complete(tmp_path / "snap")
    (p / ".timestamp").write_text("not-an-epoch\n")
    probs = validate_snapshot(p)
    assert any("epoch" in x for x in probs)


def test_empty_all_txt_flagged(tmp_path) -> None:
    p = _complete(tmp_path / "snap")
    (p / "packages" / "all.txt").write_text("   \n")
    probs = validate_snapshot(p)
    assert any("all.txt" in x for x in probs)


def test_missing_all_txt_flagged(tmp_path) -> None:
    p = _complete(tmp_path / "snap")
    (p / "packages" / "all.txt").unlink()
    assert any("all.txt" in x for x in validate_snapshot(p))


def test_missing_critical_txt_NOT_flagged(tmp_path) -> None:
    """Regression: critical.txt is reconstructable from all.txt + kernel
    patterns (pre-v0.2.0 snapshots never had it). Refusing on its
    absence would be a false 'incomplete'."""
    p = _complete(tmp_path / "snap")
    (p / "packages" / "critical.txt").unlink()
    assert validate_snapshot(p) == []


def test_missing_configs_dir_flagged(tmp_path) -> None:
    p = _complete(tmp_path / "snap")
    (p / "configs").rmdir()
    assert any("configs/" in x for x in validate_snapshot(p))


def test_multiple_problems_all_reported(tmp_path) -> None:
    p = tmp_path / "snap"
    p.mkdir()
    # Bare dir → the 3 hard-required sections flagged
    # (.timestamp, packages/all.txt, configs/).
    probs = validate_snapshot(p)
    assert len(probs) >= 3
    assert any(".timestamp" in x for x in probs)
    assert any("all.txt" in x for x in probs)
    assert any("configs/" in x for x in probs)


# ── CLI rollback refusal ──────────────────────────────────────────────


def _fake_cfg(snap_dir: Path) -> MagicMock:
    cfg = MagicMock()
    cfg.general.snapshot_dir = snap_dir
    cfg.risk.kernel_patterns = ()
    cfg.risk.kernel_pattern_exclude = ()
    return cfg


def test_cli_rollback_refuses_incomplete_snapshot(tmp_path, monkeypatch, capsys) -> None:
    snap_dir = tmp_path / "snapshots"
    incomplete = snap_dir / "2026-05-15_partial"
    incomplete.mkdir(parents=True)
    (incomplete / ".timestamp").write_text(f"{int(datetime.now().timestamp())}\n")
    # No packages/, no configs/ → incomplete.

    monkeypatch.setattr(rb, "build_config", lambda *a, **k: _fake_cfg(snap_dir))
    monkeypatch.setattr(rb, "build_sudo_strategy", lambda *a, **k: MagicMock())

    args = Namespace(snapshot_id="2026-05-15_partial", filename="mirrorlist")
    code = rb.cmd_config(args, None)
    assert code == 3
    err = capsys.readouterr().err
    assert "incomplete" in err
    assert "all.txt" in err  # the specific missing section is named


def test_cli_rollback_resolver_accepts_complete(tmp_path, monkeypatch) -> None:
    snap_dir = tmp_path / "snapshots"
    _complete(snap_dir / "2026-05-15_ok")
    cfg = _fake_cfg(snap_dir)
    assert rb._resolve_snapshot_path(cfg, "2026-05-15_ok") == snap_dir / "2026-05-15_ok"


def test_cli_rollback_resolver_missing_dir_says_not_found(
    tmp_path, monkeypatch, capsys
) -> None:
    cfg = _fake_cfg(tmp_path)
    assert rb._resolve_snapshot_path(cfg, "nope") is None
    assert "not found" in capsys.readouterr().err
