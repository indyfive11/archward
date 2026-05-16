from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class BuildFailure(BaseModel):
    """An AUR package whose build/install failed during update.

    `last_lines` is the tail of helper output for that package (typically 50 lines),
    captured for the result view's "Retry these later" hint.
    """

    model_config = ConfigDict(frozen=True)

    package: str
    last_lines: tuple[str, ...]


class QuarantineSnapshot(BaseModel):
    """Read-only snapshot of quarantine state for the result view.

    Populated by the AUR phase after save(); carries only what the result
    view needs — active entries (counting + quarantined) and their retry dates.
    """

    model_config = ConfigDict(frozen=True)

    # list of (package, version, status, failure_count, retry_after_iso_or_none)
    active: tuple[tuple[str, str, str, int, str | None], ...]


class AurResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    exit_code: int
    failures: tuple[BuildFailure, ...]
    skipped: bool = False  # True when --no-aur or no helper detected
    skip_reason: str | None = None
    quarantine: QuarantineSnapshot | None = None  # None when quarantine disabled
