"""Snapshot retention — honor cfg.general.keep_snapshots / keep_days / keep_min.

Two-pass pruning runs at the end of every pipeline run:

  Pass 1 — hard count cap: delete oldest pre-snapshots beyond keep_snapshots.
            Protects disk when updates run frequently.

  Pass 2 — age prune: delete pre-snapshots whose .timestamp predates the
            keep_days cutoff, but always spare the newest keep_min entries
            regardless of age. Skipped when keep is passed explicitly (the
            "Prune now…" button) so the user's chosen count is respected.

v0.4.7: `-after` post-snapshot siblings are deleted together with their
        paired pre-snapshot in both passes.
"""

from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path

from archward.models.config import ConfigModel

log = logging.getLogger(__name__)


def _delete_with_sibling(
    path: Path,
    post_by_base: dict[str, Path],
    removed: list[Path],
) -> None:
    """Delete a pre-snapshot directory and its paired -after sibling if present."""
    try:
        shutil.rmtree(path)
        removed.append(path)
        log.info("pruned snapshot: %s", path)
    except OSError as e:
        log.warning("failed to prune %s: %s", path, e)
    sibling = post_by_base.get(path.name)
    if sibling is not None:
        try:
            shutil.rmtree(sibling)
            removed.append(sibling)
            log.info("pruned post-snapshot: %s", sibling)
        except OSError as e:
            log.warning("failed to prune post-snapshot %s: %s", sibling, e)


def prune_snapshots(cfg: ConfigModel, *, keep: int | None = None) -> list[Path]:
    """Delete snapshots beyond the configured limits. Returns paths removed.

    `keep=None` (default) uses cfg.general.keep_snapshots and also runs the
    age-based prune pass. Passing an explicit keep value (from the "Prune now…"
    button) applies a count-only prune and skips the age pass.
    """
    target_keep = cfg.general.keep_snapshots if keep is None else keep
    if target_keep <= 0:
        log.debug("snapshot prune: disabled (keep=%s)", target_keep)
        return []

    snap_dir = cfg.general.snapshot_dir
    if not snap_dir.exists():
        return []

    all_snaps = [
        p for p in snap_dir.iterdir()
        if p.is_dir() and (p / ".timestamp").exists()
    ]

    pre = [p for p in all_snaps if not p.name.endswith("-after")]
    post_by_base = {
        p.name.removesuffix("-after"): p
        for p in all_snaps if p.name.endswith("-after")
    }

    # Newest first.
    pre.sort(key=lambda p: p.stat().st_mtime, reverse=True)

    removed: list[Path] = []

    # Pass 1: hard count cap.
    surviving = pre[:target_keep]
    for path in pre[target_keep:]:
        _delete_with_sibling(path, post_by_base, removed)

    # Pass 2: age prune — automatic runs only (keep is None).
    if keep is None and cfg.general.keep_days > 0:
        cutoff = time.time() - cfg.general.keep_days * 86400
        floor = max(0, cfg.general.keep_min)
        for i, path in enumerate(surviving):
            if i < floor:
                continue
            if not path.exists():
                continue  # already deleted in pass 1 (shouldn't happen, but safe)
            ts_path = path / ".timestamp"
            try:
                ts = float(ts_path.read_text().strip())
            except (OSError, ValueError):
                continue
            if ts < cutoff:
                _delete_with_sibling(path, post_by_base, removed)

    return removed
