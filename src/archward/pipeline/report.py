"""Result aggregation — emits the final RESULT: tag.

Per audit G4, REBOOT_NEEDED is derived from any of three sources:
  (a) a PendingUpdate.is_kernel=True was successfully *applied*
  (b) verify saw running != installed kernel
  (c) verify.reboot_log mtime > snapshot.timestamp

Dry-run mode: pending packages are informational only — no update was applied,
so REBOOT_NEEDED never fires from is_kernel. Bash compatibility tag NEEDS_REVIEW
is emitted when a dry-run sees HIGH-risk pending packages.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from archward.models.update import PendingUpdate, RiskLevel
from archward.models.verify import VerifyResult

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReportSummary:
    tag: str  # primary RESULT: tag
    secondary_tags: tuple[str, ...]  # additional tags applicable (informational)
    fail_count: int
    warn_count: int
    reboot_needed: bool


def derive_result(
    *,
    preflight_failed: bool,
    update_exit_code: int | None,
    pending: list[PendingUpdate],
    verify: VerifyResult | None,
    pacnew_count: int,
    was_dry_run: bool = False,
) -> ReportSummary:
    """Map pipeline state to a primary RESULT tag and secondary annotations."""

    # Highest-precedence: failure paths.
    if preflight_failed:
        return ReportSummary(
            tag="RESULT:UPDATE_FAILED",
            secondary_tags=(),
            fail_count=0,
            warn_count=0,
            reboot_needed=False,
        )
    if update_exit_code is not None and update_exit_code != 0:
        return ReportSummary(
            tag="RESULT:UPDATE_FAILED",
            secondary_tags=(),
            fail_count=(verify.fail_count if verify else 0),
            warn_count=(verify.warn_count if verify else 0),
            reboot_needed=False,
        )

    # Dry-run: pending packages are informational. Bash compat: emit NEEDS_REVIEW
    # when HIGH-risk pending is present; otherwise SUCCESS.
    if was_dry_run:
        if any(p.risk is RiskLevel.HIGH for p in pending):
            return ReportSummary(
                tag="RESULT:NEEDS_REVIEW",
                secondary_tags=(),
                fail_count=0,
                warn_count=0,
                reboot_needed=False,
            )
        return ReportSummary(
            tag="RESULT:SUCCESS",
            secondary_tags=(),
            fail_count=0,
            warn_count=0,
            reboot_needed=False,
        )

    fail_count = verify.fail_count if verify else 0
    warn_count = verify.warn_count if verify else 0
    update_was_applied = update_exit_code == 0
    reboot_needed = (verify.reboot_needed if verify else False) or (
        update_was_applied and any(p.is_kernel for p in pending)
    )

    if verify is not None and fail_count > 0:
        return ReportSummary(
            tag="RESULT:VERIFY_FAILED",
            secondary_tags=tuple(
                t
                for t in (
                    "RESULT:REBOOT_NEEDED" if reboot_needed else None,
                    "RESULT:PACNEW_MERGE_NEEDED" if pacnew_count > 0 else None,
                )
                if t
            ),
            fail_count=fail_count,
            warn_count=warn_count,
            reboot_needed=reboot_needed,
        )

    if reboot_needed:
        return ReportSummary(
            tag="RESULT:REBOOT_NEEDED",
            secondary_tags=tuple(
                t for t in ("RESULT:PACNEW_MERGE_NEEDED" if pacnew_count > 0 else None,) if t
            ),
            fail_count=fail_count,
            warn_count=warn_count,
            reboot_needed=True,
        )

    if pacnew_count > 0:
        return ReportSummary(
            tag="RESULT:PACNEW_MERGE_NEEDED",
            secondary_tags=(),
            fail_count=fail_count,
            warn_count=warn_count,
            reboot_needed=False,
        )

    return ReportSummary(
        tag="RESULT:SUCCESS",
        secondary_tags=(),
        fail_count=fail_count,
        warn_count=warn_count,
        reboot_needed=False,
    )
