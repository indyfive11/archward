from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class GateStatus(StrEnum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    SKIPPED = "skipped"


class GateResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    status: GateStatus
    message: str
    detail: str | None = None
    can_override: bool = False
