from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict


class RiskLevel(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class PendingUpdate(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    old_version: str
    new_version: str
    source: Literal["official", "aur"]
    risk: RiskLevel
    is_kernel: bool = False
    reason: str | None = None
