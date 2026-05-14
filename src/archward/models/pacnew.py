from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict


class PacnewRecommendation(StrEnum):
    """What the matched TOML rule recommends. Stored on PacnewFile."""

    KEEP_OURS = "keep_ours"
    TAKE_NEW = "take_new"
    REVIEW_NEEDED = "review_needed"


class PacnewAction(StrEnum):
    """What the user (or auto-mode) chose at runtime. Passed to apply_action()."""

    KEEP_OURS = "keep_ours"
    TAKE_NEW = "take_new"
    EDIT = "edit"
    LEAVE = "leave"


class PacnewFile(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: Path
    original_path: Path
    recommendation: PacnewRecommendation
    rule_pattern: str | None = None
    note: str | None = None
    detected_at: datetime
