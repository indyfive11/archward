"""Audit G4 — RESULT precedence and reboot signal aggregation."""

from __future__ import annotations

from archward.models.update import PendingUpdate, RiskLevel
from archward.models.verify import CheckStatus, VerifyCheck, VerifyResult
from archward.pipeline.report import derive_result


def _pending(name: str, is_kernel: bool = False) -> PendingUpdate:
    return PendingUpdate(
        name=name,
        old_version="1.0",
        new_version="1.1",
        source="official",
        risk=RiskLevel.HIGH if is_kernel else RiskLevel.LOW,
        is_kernel=is_kernel,
    )


def _verify(fail: int = 0, warn: int = 0, reboot: bool = False) -> VerifyResult:
    return VerifyResult(checks=(), fail_count=fail, warn_count=warn, reboot_needed=reboot)


def test_preflight_failure_short_circuits():
    s = derive_result(
        preflight_failed=True,
        update_exit_code=None,
        pending=[],
        verify=None,
        pacnew_count=0,
    )
    assert s.tag == "RESULT:UPDATE_FAILED"


def test_update_nonzero_is_failure():
    s = derive_result(
        preflight_failed=False,
        update_exit_code=1,
        pending=[_pending("foo")],
        verify=None,
        pacnew_count=0,
    )
    assert s.tag == "RESULT:UPDATE_FAILED"


def test_verify_fail_beats_reboot():
    s = derive_result(
        preflight_failed=False,
        update_exit_code=0,
        pending=[_pending("linux", is_kernel=True)],
        verify=_verify(fail=2, reboot=True),
        pacnew_count=0,
    )
    assert s.tag == "RESULT:VERIFY_FAILED"
    assert "RESULT:REBOOT_NEEDED" in s.secondary_tags


def test_kernel_in_pending_triggers_reboot():
    """Audit G4: if a kernel update was applied (exit 0), REBOOT_NEEDED even if verify didn't notice yet."""
    s = derive_result(
        preflight_failed=False,
        update_exit_code=0,
        pending=[_pending("linux-cachyos-bore", is_kernel=True)],
        verify=_verify(reboot=False),
        pacnew_count=0,
    )
    assert s.tag == "RESULT:REBOOT_NEEDED"


def test_kernel_pending_in_dry_run_does_NOT_trigger_reboot():
    """Dry-run: pending packages weren't applied — REBOOT_NEEDED must not fire."""
    s = derive_result(
        preflight_failed=False,
        update_exit_code=None,
        pending=[_pending("linux", is_kernel=True)],
        verify=None,
        pacnew_count=0,
        was_dry_run=True,
    )
    # HIGH-risk pending in dry-run → NEEDS_REVIEW (bash compat)
    assert s.tag == "RESULT:NEEDS_REVIEW"
    assert s.reboot_needed is False


def test_dry_run_only_low_risk_is_success():
    s = derive_result(
        preflight_failed=False,
        update_exit_code=None,
        pending=[_pending("vim"), _pending("htop")],
        verify=None,
        pacnew_count=0,
        was_dry_run=True,
    )
    assert s.tag == "RESULT:SUCCESS"


def test_dry_run_no_pending_is_success():
    s = derive_result(
        preflight_failed=False,
        update_exit_code=None,
        pending=[],
        verify=None,
        pacnew_count=0,
        was_dry_run=True,
    )
    assert s.tag == "RESULT:SUCCESS"


def test_pacnew_when_clean():
    s = derive_result(
        preflight_failed=False,
        update_exit_code=0,
        pending=[_pending("vim")],
        verify=_verify(),
        pacnew_count=3,
    )
    assert s.tag == "RESULT:PACNEW_MERGE_NEEDED"


def test_success_when_clean():
    s = derive_result(
        preflight_failed=False,
        update_exit_code=0,
        pending=[_pending("vim")],
        verify=_verify(),
        pacnew_count=0,
    )
    assert s.tag == "RESULT:SUCCESS"


def test_reboot_with_pacnew_secondary():
    s = derive_result(
        preflight_failed=False,
        update_exit_code=0,
        pending=[_pending("linux", is_kernel=True)],
        verify=_verify(reboot=True),
        pacnew_count=2,
    )
    assert s.tag == "RESULT:REBOOT_NEEDED"
    assert "RESULT:PACNEW_MERGE_NEEDED" in s.secondary_tags
