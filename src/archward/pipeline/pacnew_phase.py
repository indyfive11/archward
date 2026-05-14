"""Pacnew detection phase.

Phase 1 just enumerates and reports. Action application (apply_action) is
exposed via the CLI for the interactive flow; Phase 1 does not auto-apply.
"""

from __future__ import annotations

import logging
from pathlib import Path

from archward.events import EventBus
from archward.models.config import ConfigModel
from archward.models.pacnew import PacnewFile
from archward.pacman.pacnew import classify, find_pacnew_files

log = logging.getLogger(__name__)

PHASE = "pacnew"


def scan_pacnew(cfg: ConfigModel, snapshot_path: Path, bus: EventBus) -> list[PacnewFile]:
    """Find .pacnew files newer than the snapshot timestamp."""
    bus.emit_start(PHASE, "Scanning for .pacnew files")
    ts_path = snapshot_path / ".timestamp"
    since_epoch: int | None = None
    if ts_path.exists():
        try:
            since_epoch = int(ts_path.read_text().strip())
        except (OSError, ValueError):
            since_epoch = None

    paths = find_pacnew_files(since_epoch=since_epoch)
    if not paths:
        bus.emit_result(PHASE, "No new .pacnew files")
        return []

    files = [classify(p, cfg.pacnew) for p in paths]
    bus.emit_log(PHASE, f"Found {len(files)} new .pacnew file(s)")
    for f in files:
        bus.emit_log(
            PHASE,
            f"  {f.path}  ({f.recommendation.value}"
            + (f" — {f.note}" if f.note else "")
            + ")",
        )
    bus.emit_result(
        PHASE,
        f"{len(files)} pacnew file(s) need attention",
        payload={"files": [f.model_dump(mode="json") for f in files]},
    )
    return files
