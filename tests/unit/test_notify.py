"""Notification composer — RESULT → urgency/title/body mapping."""

from __future__ import annotations

from archward.models.aur import AurResult, BuildFailure
from archward.pipeline.pipeline import PipelineResult
from archward.pipeline.report import ReportSummary
from archward.system.notify import Notification, compose_completion


def _summary(tag: str, fail: int = 0, warn: int = 0, secondary: tuple[str, ...] = ()) -> ReportSummary:
    return ReportSummary(
        tag=tag,
        secondary_tags=secondary,
        fail_count=fail,
        warn_count=warn,
        reboot_needed=False,
    )


def _result(summary: ReportSummary, *, aur: AurResult | None = None) -> PipelineResult:
    r = PipelineResult()
    r.summary = summary
    r.aur = aur
    return r


def test_success_is_low_urgency() -> None:
    n = compose_completion(_result(_summary("RESULT:SUCCESS")))
    assert n is not None
    assert n.urgency == "low"
    assert n.title == "Update complete"


def test_reboot_needed_is_normal() -> None:
    n = compose_completion(_result(_summary("RESULT:REBOOT_NEEDED")))
    assert n is not None
    assert n.urgency == "normal"
    assert n.title == "Reboot required"
    assert "Reboot" in n.body


def test_verify_failed_is_critical() -> None:
    n = compose_completion(_result(_summary("RESULT:VERIFY_FAILED", fail=2, warn=1)))
    assert n is not None
    assert n.urgency == "critical"
    assert "2 FAIL" in n.body
    assert "1 WARN" in n.body


def test_update_failed_includes_reason() -> None:
    s = _summary("RESULT:UPDATE_FAILED")
    r = _result(s)
    r.aborted_reason = "pacman db locked by yay (pid 1234)"
    n = compose_completion(r)
    assert n is not None
    assert n.urgency == "critical"
    assert "pacman db locked" in n.body


def test_secondary_tags_annotated() -> None:
    n = compose_completion(
        _result(_summary("RESULT:REBOOT_NEEDED", secondary=("RESULT:PACNEW_MERGE_NEEDED",)))
    )
    assert n is not None
    assert "Pacnew merge pending" in n.body


def test_aur_failures_listed() -> None:
    aur = AurResult(
        exit_code=1,
        failures=(
            BuildFailure(package="radarr", last_lines=("ERROR: build failed",)),
            BuildFailure(package="sonarr", last_lines=("ERROR: build failed",)),
        ),
    )
    n = compose_completion(_result(_summary("RESULT:SUCCESS"), aur=aur))
    assert n is not None
    assert "radarr" in n.body
    assert "sonarr" in n.body


def test_aur_failures_truncated_at_three() -> None:
    aur = AurResult(
        exit_code=1,
        failures=tuple(
            BuildFailure(package=f"pkg-{i}", last_lines=("ERROR",)) for i in range(5)
        ),
    )
    n = compose_completion(_result(_summary("RESULT:SUCCESS"), aur=aur))
    assert n is not None
    assert "+2 more" in n.body


def test_no_summary_returns_none() -> None:
    r = PipelineResult()
    r.summary = None
    assert compose_completion(r) is None


def test_none_result_returns_none() -> None:
    assert compose_completion(None) is None
