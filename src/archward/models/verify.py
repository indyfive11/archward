from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict


class CheckStatus(StrEnum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    # v0.5: a check that could not run (probe timeout, offline, disabled by
    # config, nothing to assess). Counts in neither fail nor warn tallies.
    # Previously encoded as PASS + "skipped" prose.
    SKIPPED = "skipped"


class VerifyCheck(BaseModel):
    model_config = ConfigDict(frozen=True)

    bucket: Literal["universal", "services", "plugin"]
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
