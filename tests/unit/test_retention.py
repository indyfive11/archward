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


def _cfg_with_snap_dir(snap_dir: Path, keep: int, keep_days: int = 0, keep_min: int = 0):
    """Mutate a default config to point at tmp + specific retention settings.

    keep_days=0 and keep_min=0 disable age-based pruning so existing count-only
    tests are unaffected by the new fields.
    """
    cfg = default_config()
    new_general = cfg.general.model_copy(update={
        "snapshot_dir": snap_dir,
        "keep_snapshots": keep,
        "keep_days": keep_days,
        "keep_min": keep_min,
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


# ── Age-based pruning tests ───────────────────────────────────────────────────

def test_age_prune_removes_old(tmp_path: Path) -> None:
    """A snapshot older than keep_days is deleted by the age pass."""
    snap_dir = tmp_path / "snapshots"
    now = time.time()
    fresh = _make_snap(snap_dir, "snap-fresh", now - 5 * 86400)   # 5 days old
    old   = _make_snap(snap_dir, "snap-old",   now - 40 * 86400)  # 40 days old
    # keep_min=0 so the floor doesn't protect snap-old
    cfg = _cfg_with_snap_dir(snap_dir, keep=10, keep_days=30, keep_min=0)

    removed = prune_snapshots(cfg)

    assert old not in [p for p in snap_dir.iterdir() if p.is_dir()]
    assert fresh.exists()
    assert len(removed) == 1


def test_age_prune_respects_keep_min_floor(tmp_path: Path) -> None:
    """The newest keep_min snapshots are protected from age pruning."""
    snap_dir = tmp_path / "snapshots"
    now = time.time()
    snap1 = _make_snap(snap_dir, "snap-1", now - 60 * 86400)  # 60d — oldest
    snap2 = _make_snap(snap_dir, "snap-2", now - 50 * 86400)  # 50d
    snap3 = _make_snap(snap_dir, "snap-3", now - 40 * 86400)  # 40d — newest, protected
    cfg = _cfg_with_snap_dir(snap_dir, keep=10, keep_days=30, keep_min=1)

    removed = prune_snapshots(cfg)

    # snap-3 is the newest → protected by keep_min=1; snap-1 and snap-2 pruned
    assert snap3.exists()
    assert not snap1.exists()
    assert not snap2.exists()
    assert len(removed) == 2


def test_age_prune_disabled_when_keep_days_zero(tmp_path: Path) -> None:
    """keep_days=0 disables age-based pruning entirely."""
    snap_dir = tmp_path / "snapshots"
    now = time.time()
    old = _make_snap(snap_dir, "snap-old", now - 365 * 86400)  # 1 year old
    cfg = _cfg_with_snap_dir(snap_dir, keep=10, keep_days=0, keep_min=0)

    removed = prune_snapshots(cfg)

    assert removed == []
    assert old.exists()


def test_age_prune_skipped_on_explicit_keep(tmp_path: Path) -> None:
    """Passing an explicit keep value skips the age pass (Prune now button)."""
    snap_dir = tmp_path / "snapshots"
    now = time.time()
    old = _make_snap(snap_dir, "snap-old", now - 40 * 86400)
    cfg = _cfg_with_snap_dir(snap_dir, keep=10, keep_days=30, keep_min=0)

    # Explicit keep — age pass must not fire
    removed = prune_snapshots(cfg, keep=5)

    assert removed == []
    assert old.exists()


def test_count_cap_and_age_prune_combine(tmp_path: Path) -> None:
    """Count cap fires first; age prune operates only on the survivors."""
    snap_dir = tmp_path / "snapshots"
    now = time.time()
    # 4 snapshots: 2 fresh, 2 old
    snap1 = _make_snap(snap_dir, "snap-1", now - 50 * 86400)  # old
    snap2 = _make_snap(snap_dir, "snap-2", now - 45 * 86400)  # old
    snap3 = _make_snap(snap_dir, "snap-3", now - 5 * 86400)   # fresh
    snap4 = _make_snap(snap_dir, "snap-4", now - 2 * 86400)   # fresh (newest)
    # keep_snapshots=3 → count pass removes snap-1 (oldest)
    # keep_days=30 → age pass removes snap-2 from survivors [snap-4, snap-3, snap-2]
    # keep_min=1 → snap-4 (newest) protected, snap-3 and snap-2 subject to age
    cfg = _cfg_with_snap_dir(snap_dir, keep=3, keep_days=30, keep_min=1)

    removed = prune_snapshots(cfg)

    assert not snap1.exists()  # removed by count cap
    assert not snap2.exists()  # removed by age prune
    assert snap3.exists()      # fresh — not over-age
    assert snap4.exists()      # protected by keep_min floor
    assert len(removed) == 2


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
