"""Tests for the after-snapshot feature (v0.4.7).

Covers:
- SnapshotMeta.is_post property
- retention paired deletion (pre+post counted/pruned together)
"""

from __future__ import annotations

import os
from pathlib import Path

from archward.config.defaults import default_config
from archward.pipeline.retention import prune_snapshots


# ── SnapshotMeta.is_post ──────────────────────────────────────────────────────

def test_snapshot_meta_is_post_true() -> None:
    from datetime import datetime
    from archward.models.snapshot import SnapshotMeta
    meta = SnapshotMeta(
        snapshot_id="2026-05-16_103757-after",
        created_at=datetime.now(),
        path=Path("/tmp/fake"),
        distro_id="arch",
        kernel_release="6.9.0",
        free_disk_gb=10,
    )
    assert meta.is_post is True


def test_snapshot_meta_is_post_false() -> None:
    from datetime import datetime
    from archward.models.snapshot import SnapshotMeta
    meta = SnapshotMeta(
        snapshot_id="2026-05-16_103757",
        created_at=datetime.now(),
        path=Path("/tmp/fake"),
        distro_id="arch",
        kernel_release="6.9.0",
        free_disk_gb=10,
    )
    assert meta.is_post is False


# ── Retention with paired -after snapshots ────────────────────────────────────

def _make_snap(snap_dir: Path, name: str, mtime: float) -> Path:
    p = snap_dir / name
    p.mkdir(parents=True)
    (p / ".timestamp").write_text(str(int(mtime)))
    os.utime(p, (mtime, mtime))
    return p


def _cfg_with_snap_dir(snap_dir: Path, keep: int):
    cfg = default_config()
    new_general = cfg.general.model_copy(update={
        "snapshot_dir": snap_dir,
        "keep_snapshots": keep,
    })
    return cfg.model_copy(update={"general": new_general})


def test_retention_prunes_pairs(tmp_path: Path) -> None:
    """Pruning a pre-snapshot also deletes its paired -after sibling."""
    snap_dir = tmp_path / "snapshots"
    pre1 = _make_snap(snap_dir, "snap-1", 1.0)
    post1 = _make_snap(snap_dir, "snap-1-after", 1.5)
    pre2 = _make_snap(snap_dir, "snap-2", 2.0)
    _make_snap(snap_dir, "snap-2-after", 2.5)
    pre3 = _make_snap(snap_dir, "snap-3", 3.0)

    cfg = _cfg_with_snap_dir(snap_dir, keep=2)
    removed = prune_snapshots(cfg)

    # snap-1 and snap-1-after should be removed (oldest pre pruned, pair follows)
    assert pre1 not in [p for p in snap_dir.iterdir()]
    assert post1 not in [p for p in snap_dir.iterdir()]
    # snap-2 and snap-3 stay
    assert pre2.exists()
    assert pre3.exists()
    assert len(removed) == 2  # snap-1 and snap-1-after


def test_retention_respects_post_only_count(tmp_path: Path) -> None:
    """-after snapshots don't count toward keep_snapshots quota."""
    snap_dir = tmp_path / "snapshots"
    pre1 = _make_snap(snap_dir, "snap-1", 1.0)
    _make_snap(snap_dir, "snap-1-after", 1.5)
    pre2 = _make_snap(snap_dir, "snap-2", 2.0)
    _make_snap(snap_dir, "snap-2-after", 2.5)

    cfg = _cfg_with_snap_dir(snap_dir, keep=2)
    removed = prune_snapshots(cfg)

    # 2 pre-snapshots, keep=2 → nothing pruned
    assert removed == []
    assert pre1.exists()
    assert pre2.exists()


def test_retention_unpaired_post_is_left_alone(tmp_path: Path) -> None:
    """An orphaned -after dir (no matching pre) is not deleted by retention."""
    snap_dir = tmp_path / "snapshots"
    pre1 = _make_snap(snap_dir, "snap-1", 1.0)
    pre2 = _make_snap(snap_dir, "snap-2", 2.0)
    orphan = _make_snap(snap_dir, "snap-0-after", 0.5)  # no snap-0 pre

    cfg = _cfg_with_snap_dir(snap_dir, keep=2)
    removed = prune_snapshots(cfg)

    assert removed == []  # 2 pre-snapshots, keep=2, no pruning needed
    assert pre1.exists()
    assert pre2.exists()
    assert orphan.exists()
