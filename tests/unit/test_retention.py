"""Tests for snapshot retention (F6, v0.4.0).

Uses a tmp_path with a fake snapshot layout (each subdir has a
.timestamp file so prune_snapshots() recognizes it). Verifies:

- newest N kept, others deleted
- keep <= 0 is a no-op
- missing snapshot_dir is graceful
- returns the list of deleted paths
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from archward.config.defaults import default_config
from archward.pipeline.retention import prune_snapshots


def _make_snap(snap_dir: Path, name: str, mtime: float) -> Path:
    """Create a directory shaped like a real snapshot (with .timestamp)."""
    p = snap_dir / name
    p.mkdir(parents=True)
    (p / ".timestamp").write_text(str(int(mtime)))
    (p / "config-files").mkdir()  # add some bulk to exercise rmtree
    (p / "config-files" / "dummy.txt").write_text("x")
    # Set the directory mtime explicitly so the sort is deterministic.
    import os
    os.utime(p, (mtime, mtime))
    return p


def _cfg_with_snap_dir(snap_dir: Path, keep: int):
    """Mutate a default config to point at tmp + a specific keep_snapshots."""
    cfg = default_config()
    # ConfigModel is frozen; rebuild general via model_copy.
    new_general = cfg.general.model_copy(update={
        "snapshot_dir": snap_dir,
        "keep_snapshots": keep,
    })
    return cfg.model_copy(update={"general": new_general})


def test_keep_three_of_five(tmp_path: Path) -> None:
    snap_dir = tmp_path / "snapshots"
    # Five snapshots, mtimes 1..5
    paths = [_make_snap(snap_dir, f"snap-{i}", float(i)) for i in range(1, 6)]
    cfg = _cfg_with_snap_dir(snap_dir, keep=3)

    removed = prune_snapshots(cfg)

    # Two oldest (mtime 1, 2) removed; three newest (mtime 3, 4, 5) kept.
    assert len(removed) == 2
    assert not paths[0].exists()
    assert not paths[1].exists()
    assert paths[2].exists()
    assert paths[3].exists()
    assert paths[4].exists()


def test_keep_zero_is_disabled(tmp_path: Path) -> None:
    """keep_snapshots <= 0 is the 'disabled' sentinel — never prunes."""
    snap_dir = tmp_path / "snapshots"
    for i in range(1, 4):
        _make_snap(snap_dir, f"snap-{i}", float(i))
    cfg = _cfg_with_snap_dir(snap_dir, keep=0)

    removed = prune_snapshots(cfg)
    assert removed == []
    assert len(list(snap_dir.iterdir())) == 3


def test_explicit_keep_override(tmp_path: Path) -> None:
    """The Prune-now button passes an explicit keep value."""
    snap_dir = tmp_path / "snapshots"
    for i in range(1, 6):
        _make_snap(snap_dir, f"snap-{i}", float(i))
    cfg = _cfg_with_snap_dir(snap_dir, keep=10)  # cfg says keep 10

    removed = prune_snapshots(cfg, keep=2)  # but user asks for 2
    assert len(removed) == 3


def test_missing_snapshot_dir_returns_empty(tmp_path: Path) -> None:
    cfg = _cfg_with_snap_dir(tmp_path / "does-not-exist", keep=5)
    assert prune_snapshots(cfg) == []


def test_keep_more_than_existing_is_noop(tmp_path: Path) -> None:
    """keep > existing snapshot count → nothing deleted."""
    snap_dir = tmp_path / "snapshots"
    for i in range(1, 4):
        _make_snap(snap_dir, f"snap-{i}", float(i))
    cfg = _cfg_with_snap_dir(snap_dir, keep=10)

    removed = prune_snapshots(cfg)
    assert removed == []
    assert len(list(snap_dir.iterdir())) == 3


def test_dirs_without_timestamp_are_ignored(tmp_path: Path) -> None:
    """A loose directory in snapshot_dir without `.timestamp` is not a snapshot."""
    snap_dir = tmp_path / "snapshots"
    snap_dir.mkdir()
    # Create a real snapshot…
    _make_snap(snap_dir, "snap-real", 100.0)
    # …plus a stray directory that shouldn't be touched.
    stray = snap_dir / "stray-loose-dir"
    stray.mkdir()
    (stray / "important-file").write_text("don't delete me")
    cfg = _cfg_with_snap_dir(snap_dir, keep=0)

    # Even keep=0 doesn't touch non-snapshot dirs (the function early-returns
    # for keep<=0 anyway). Try keep=1 to force a prune evaluation.
    cfg = _cfg_with_snap_dir(snap_dir, keep=1)
    prune_snapshots(cfg)
    # The stray dir survives because it lacks a .timestamp marker.
    assert stray.exists()
    assert (stray / "important-file").exists()
