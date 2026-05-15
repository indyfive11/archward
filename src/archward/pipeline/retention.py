"""Snapshot retention — honor cfg.general.keep_snapshots.

Pre-v0.4.0 the setting was a no-op (the GUI exposed it but the pipeline
ignored it). v0.4.0 wires this in: at the end of each pipeline run we
delete snapshots older than the configured keep-count. Users can also
invoke `prune_snapshots()` directly via the snapshot browser's "Prune
now…" button.

KISS:
- Identify snapshots by their `.timestamp` marker (set by pipeline.snapshot).
- Sort newest-first by directory mtime (more robust than parsing names).
- shutil.rmtree the surplus; ignore individual-dir failures.
- keep <= 0 means "disabled" — the function returns without touching anything.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from archward.models.config import ConfigModel

log = logging.getLogger(__name__)


def prune_snapshots(cfg: ConfigModel, *, keep: int | None = None) -> list[Path]:
    """Delete snapshots beyond the keep-count. Returns the list of paths removed.

    `keep=None` (default) uses `cfg.general.keep_snapshots`. Passing an
    explicit keep value lets the "Prune now…" button override.
    """
    target_keep = cfg.general.keep_snapshots if keep is None else keep
    if target_keep <= 0:
        log.debug("snapshot prune: disabled (keep=%s)", target_keep)
        return []

    snap_dir = cfg.general.snapshot_dir
    if not snap_dir.exists():
        return []

    snapshots = [
        p for p in snap_dir.iterdir()
        if p.is_dir() and (p / ".timestamp").exists()
    ]
    # Newest first — keep the top `target_keep`, delete the rest.
    snapshots.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    to_delete = snapshots[target_keep:]
    removed: list[Path] = []
    for path in to_delete:
        try:
            shutil.rmtree(path)
            removed.append(path)
            log.info("pruned snapshot: %s", path)
        except OSError as e:
            log.warning("failed to prune %s: %s", path, e)
    return removed
