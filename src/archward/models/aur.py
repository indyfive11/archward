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


class QuarantineActiveEntry(BaseModel):
    """One active quarantine entry carried to the result view."""

    model_config = ConfigDict(frozen=True)

    package: str
    version: str
    status: str
    failure_count: int
    retry_after: str | None
    last_error: str | None  # first 80 chars of the build error, for quick diagnosis


class QuarantineSnapshot(BaseModel):
    """Read-only snapshot of quarantine state for the result view.

    Populated by the AUR phase after save(); carries only what the result
    view needs — active entries (counting + quarantined), their retry dates,
    and a short build-failure excerpt so users can tell upstream vs local issues.
    """

    model_config = ConfigDict(frozen=True)

    active: tuple[QuarantineActiveEntry, ...]


class AurResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    exit_code: int
    failures: tuple[BuildFailure, ...]
    skipped: bool = False  # True when --no-aur or no helper detected
    skip_reason: str | None = None
    quarantine: QuarantineSnapshot | None = None  # None when quarantine disabled
