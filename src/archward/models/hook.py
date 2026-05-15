"""Hook outcome model.

One HookResult per user-defined shell command that ran via HookRunner. Carries
enough context for the Verify view to render a "hooks" bucket: command text,
exit code, captured output (stdout + stderr concatenated, line-bounded),
and a phase tag distinguishing pre_update from post_verify.

Status derives from exit code:
  PASS = exit 0
  FAIL = exit non-zero (any reason: failed assertion, timeout, missing binary)
  TIMEOUT = the subprocess.TimeoutExpired path
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict


class HookStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    TIMEOUT = "timeout"


class HookResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    command: str
    phase: Literal["pre_update", "post_verify"]
    status: HookStatus
    exit_code: int                          # -1 for timeout
    output_lines: tuple[str, ...]           # stdout + stderr, line-split
