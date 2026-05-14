from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict


class CheckStatus(StrEnum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


class VerifyCheck(BaseModel):
    model_config = ConfigDict(frozen=True)

    bucket: Literal["universal", "services"]
    name: str
    status: CheckStatus
    message: str
    detail: str | None = None


class VerifyResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    checks: tuple[VerifyCheck, ...]
    fail_count: int
    warn_count: int
    reboot_needed: bool
