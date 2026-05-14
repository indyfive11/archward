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


class AurResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    exit_code: int
    failures: tuple[BuildFailure, ...]
    skipped: bool = False  # True when --no-aur or no helper detected
    skip_reason: str | None = None
