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


@dataclass(frozen=True)
class TagMeta:
    """Presentation metadata for a RESULT tag — the single source consumed
    by the GUI rail, the result banner, and desktop notifications (v0.5;
    previously three hand-maintained maps that could drift)."""

    rail_status: str   # phase-rail status: pass | warn | fail | skipped
    severity: str      # banner severity key: success | info | neutral | danger
    human: str         # banner label
    urgency: str       # notification urgency: low | normal | critical
    title: str         # notification title


# The RESULT tag *strings* are a public contract ($ARCHWARD_RESULT in hooks,
# CLI output) and stay stable; only this presentation table may evolve.
TAG_META: dict[str, TagMeta] = {
    "RESULT:SUCCESS": TagMeta("pass", "success", "Success", "low", "Update complete"),
    "RESULT:NEEDS_REVIEW": TagMeta("warn", "info", "Needs Review", "low", "Review needed"),
    "RESULT:REBOOT_NEEDED": TagMeta("warn", "info", "Reboot Needed", "normal", "Reboot required"),
    "RESULT:PACNEW_MERGE_NEEDED": TagMeta(
        "warn", "info", "Pacnew Merge Needed", "normal", "Pacnew merge pending"
    ),
    "RESULT:ABORTED": TagMeta("warn", "neutral", "Aborted", "normal", "Update aborted"),
    "RESULT:VERIFY_FAILED": TagMeta("fail", "danger", "Verify Failed", "critical", "Verify failed"),
    "RESULT:UPDATE_FAILED": TagMeta("fail", "danger", "Update Failed", "critical", "Update failed"),
}


def derive_result(
    *,
    preflight_failed: bool,
    update_exit_code: int | None,
    pending: list[PendingUpdate],
    verify: VerifyResult | None,
    pacnew_count: int,
    was_dry_run: bool = False,
    pending_check_ok: bool = True,
    aborted: bool = False,
) -> ReportSummary:
    """Map pipeline state to a primary RESULT tag and secondary annotations.

    `aborted` (v0.4.18) marks runs the pipeline stopped deliberately before
    completing — user declines (HIGH-risk, gate/WARN override), gate refusals,
    pre-flight failures, and user cancellation. These map to RESULT:ABORTED
    rather than RESULT:UPDATE_FAILED: nothing failed, archward just did not
    proceed. UPDATE_FAILED is reserved for an actual failed pacman run (and
    the no-summary defensive path).
    """

    # Highest-precedence: deliberate abort. A cancel can land AFTER a
    # successful official update (the transaction is never killed), so
    # reboot/pacnew secondaries still apply when the update went through.
    if aborted:
        update_was_applied = update_exit_code == 0
        reboot_needed = update_was_applied and any(p.is_kernel for p in pending)
        return ReportSummary(
            tag="RESULT:ABORTED",
            secondary_tags=tuple(
                t
                for t in (
                    "RESULT:REBOOT_NEEDED" if reboot_needed else None,
                    "RESULT:PACNEW_MERGE_NEEDED" if pacnew_count > 0 else None,
                )
                if t
            ),
            fail_count=0,
            warn_count=0,
            reboot_needed=reboot_needed,
        )

    # Failure paths.
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
            secondary_tags=tuple(
                t
                for t in (
                    "RESULT:PACNEW_MERGE_NEEDED" if pacnew_count > 0 else None,
                )
                if t
            ),
            fail_count=(verify.fail_count if verify else 0),
            warn_count=(verify.warn_count if verify else 0),
            reboot_needed=False,
        )

    # Dry-run: pending packages are informational. Bash compat: emit NEEDS_REVIEW
    # when HIGH-risk pending is present; otherwise SUCCESS. An empty pending
    # list only means "nothing to do" when the checkupdates call actually
    # worked — a failed check must not report SUCCESS.
    if was_dry_run:
        if not pending_check_ok or any(p.risk is RiskLevel.HIGH for p in pending):
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
