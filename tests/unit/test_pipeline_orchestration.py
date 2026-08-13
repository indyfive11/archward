"""Pipeline orchestration matrix (v0.5, roadmap #3 first half).

Table-driven tests over `_run_pipeline_impl` with injected phase stubs —
the 500-line orchestrator previously had no tests on its branch structure
beyond cancel boundaries (test_pipeline_cancel) and the v0.4.18 abort
paths (test_ux_honesty). This file covers the rest: dry-run exits,
HIGH-risk gating incl. deselection, hook-fail aborts, verify-disabled,
after-snapshot and retention trigger conditions.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

import archward.config.paths as paths_mod
from archward.config.defaults import default_config
from archward.events import EventBus
from archward.models.config import GeneralConfig, HooksConfig, VerifyConfig
from archward.models.update import PendingUpdate, RiskLevel
from archward.models.verify import VerifyResult
from archward.pipeline import pipeline as pl
from archward.pipeline.risk import ClassifiedPending
from archward.pipeline.update_official import OfficialUpdateOutcome


@pytest.fixture(autouse=True)
def _tmp_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(paths_mod, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(paths_mod, "lock_file", lambda: tmp_path / "archward.lock")


class _Strategy:
    def argv_prefix(self):
        return []

    def env(self):
        return {}

    def warmup(self):
        return True


class _Meta:
    snapshot_id = "20260811-000000"
    path = Path("/nonexistent")


class _Snapshot:
    meta = _Meta()


def _pending(*specs: tuple[str, RiskLevel]) -> ClassifiedPending:
    return ClassifiedPending(
        updates=[
            PendingUpdate(
                name=name, old_version="1", new_version="2",
                risk=risk, is_kernel=False, source="official",
            )
            for name, risk in specs
        ]
    )


class _Recorder:
    """Records phase invocations + interesting kwargs."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.official_ignore: list[str] | None = None
        self.snapshots_taken: list[str | None] = []


def _wire(monkeypatch, rec: _Recorder, *, pending: ClassifiedPending,
          verify_result: VerifyResult | None = None):
    def _take_snapshot(cfg, strategy, bus, snapshot_id=None, **kw):
        rec.calls.append("snapshot")
        rec.snapshots_taken.append(snapshot_id)
        return _Snapshot()

    monkeypatch.setattr(pl.gates_phase, "preflight_checks", lambda cfg, bus: [])
    monkeypatch.setattr(pl.snapshot_phase, "take_snapshot", _take_snapshot)
    monkeypatch.setattr(pl.gates_phase, "run_gates", lambda cfg, snap, bus: [])
    monkeypatch.setattr(pl.risk_phase, "classify_pending", lambda cfg, bus: pending)
    monkeypatch.setattr(
        pl.risk_phase, "preview_transaction",
        lambda bus: type("P", (), {"replacements": []})(),
    )

    def _official(cfg, strategy, bus, *, ignore=None, **kw):
        rec.calls.append("official")
        rec.official_ignore = list(ignore or [])
        return OfficialUpdateOutcome(exit_code=0)

    monkeypatch.setattr(pl.update_official, "run_official_update", _official)
    monkeypatch.setattr(
        pl.update_aur, "run_aur_update",
        lambda cfg, strategy, bus, **kw: rec.calls.append("aur") or None,
    )
    monkeypatch.setattr(
        pl.pacnew_phase, "scan_pacnew",
        lambda cfg, path, bus: rec.calls.append("pacnew") or [],
    )
    monkeypatch.setattr(
        pl.verify_phase, "run_verify",
        lambda cfg, snap, bus, **kw: rec.calls.append("verify") or verify_result,
    )
    monkeypatch.setattr(
        pl.retention, "prune_snapshots",
        lambda cfg: rec.calls.append("retention") or [],
    )


def _run(cfg, mode, *, prompter=None, monkey_rec=None):
    return pl.run_pipeline(
        cfg,
        _Strategy(),
        EventBus(),
        mode,
        cancel_event=threading.Event(),
        prompter=prompter or pl.AutoYesPrompter(),
    )


# ── dry-run exits ─────────────────────────────────────────────────────────


def test_dry_run_no_pending_is_success_and_stops(monkeypatch) -> None:
    rec = _Recorder()
    _wire(monkeypatch, rec, pending=ClassifiedPending(updates=[]))
    result = _run(default_config(), pl.Mode.DRY_RUN)
    assert result.summary.tag == "RESULT:SUCCESS"
    assert "official" not in rec.calls and "verify" not in rec.calls


def test_dry_run_with_high_pending_is_needs_review(monkeypatch) -> None:
    rec = _Recorder()
    _wire(monkeypatch, rec, pending=_pending(("linux", RiskLevel.HIGH)))
    result = _run(default_config(), pl.Mode.DRY_RUN)
    assert result.summary.tag == "RESULT:NEEDS_REVIEW"
    assert "official" not in rec.calls
    assert "retention" not in rec.calls  # dry-run never prunes


# ── HIGH-risk gating ──────────────────────────────────────────────────────


def test_auto_mode_high_risk_aborts(monkeypatch) -> None:
    rec = _Recorder()
    _wire(monkeypatch, rec, pending=_pending(("linux", RiskLevel.HIGH)))
    result = _run(default_config(), pl.Mode.AUTO)
    assert result.summary.tag == "RESULT:ABORTED"
    assert result.aborted_reason == "HIGH RISK packages present in auto mode"
    assert "official" not in rec.calls


def test_deselected_packages_reach_pacman_ignore(monkeypatch) -> None:
    rec = _Recorder()
    _wire(
        monkeypatch, rec,
        pending=_pending(("linux", RiskLevel.HIGH), ("zlib", RiskLevel.LOW)),
    )

    class _DeselectPrompter:
        def decide_high_risk(self, high):
            return True, ["linux"]

        def confirm_gate_override(self, gate):
            return True

    result = _run(default_config(), pl.Mode.INTERACTIVE, prompter=_DeselectPrompter())
    assert result.deselected_packages == ("linux",)
    assert rec.official_ignore == ["linux"]
    assert result.summary is not None


# ── pre-update hook failure ───────────────────────────────────────────────


def test_failing_pre_hook_aborts_before_update(monkeypatch) -> None:
    rec = _Recorder()
    _wire(monkeypatch, rec, pending=_pending(("zlib", RiskLevel.LOW)))
    cfg = default_config().model_copy(update={
        "hooks": HooksConfig(pre_update=("false",), fail_pipeline_on_error=True),
    })
    result = _run(cfg, pl.Mode.INTERACTIVE)
    assert result.summary.tag == "RESULT:ABORTED"
    assert "pre_update hook failed" in result.aborted_reason
    assert "official" not in rec.calls
    assert len(result.pre_hook_results) == 1


def test_failing_pre_hook_without_abort_flag_proceeds(monkeypatch) -> None:
    rec = _Recorder()
    _wire(monkeypatch, rec, pending=_pending(("zlib", RiskLevel.LOW)))
    cfg = default_config().model_copy(update={
        "hooks": HooksConfig(pre_update=("false",), fail_pipeline_on_error=False),
    })
    result = _run(cfg, pl.Mode.INTERACTIVE)
    assert result.aborted_reason is None
    assert "official" in rec.calls


# ── verify-disabled path ──────────────────────────────────────────────────


def test_verify_disabled_skips_verify_and_post_hooks(monkeypatch) -> None:
    rec = _Recorder()
    _wire(monkeypatch, rec, pending=ClassifiedPending(updates=[]))
    cfg = default_config().model_copy(update={
        "verify": VerifyConfig(enabled=False),
        # A post_verify hook that must NOT run when verify is disabled.
        "hooks": HooksConfig(post_verify=("true",)),
    })
    result = _run(cfg, pl.Mode.INTERACTIVE)
    assert "verify" not in rec.calls
    assert result.verify is None
    assert result.post_hook_results == ()


# ── after-snapshot + retention trigger conditions ─────────────────────────


def _clean_verify() -> VerifyResult:
    return VerifyResult(checks=(), fail_count=0, warn_count=0, reboot_needed=False)


def _failed_verify() -> VerifyResult:
    return VerifyResult(checks=(), fail_count=1, warn_count=0, reboot_needed=False)


def test_after_snapshot_taken_on_clean_verify(monkeypatch) -> None:
    rec = _Recorder()
    _wire(monkeypatch, rec, pending=ClassifiedPending(updates=[]),
          verify_result=_clean_verify())
    cfg = default_config()
    cfg = cfg.model_copy(update={
        "general": cfg.general.model_copy(update={"after_snapshot": True}),
    })
    _run(cfg, pl.Mode.INTERACTIVE)
    assert rec.snapshots_taken.count(None) == 1          # the pre-snapshot
    afters = [s for s in rec.snapshots_taken if s]
    assert len(afters) == 1 and afters[0].endswith("-after")


def test_no_after_snapshot_on_failed_verify(monkeypatch) -> None:
    rec = _Recorder()
    _wire(monkeypatch, rec, pending=ClassifiedPending(updates=[]),
          verify_result=_failed_verify())
    cfg = default_config()
    cfg = cfg.model_copy(update={
        "general": cfg.general.model_copy(update={"after_snapshot": True}),
    })
    _run(cfg, pl.Mode.INTERACTIVE)
    assert [s for s in rec.snapshots_taken if s] == []


def test_retention_runs_on_completed_pipeline(monkeypatch) -> None:
    rec = _Recorder()
    _wire(monkeypatch, rec, pending=ClassifiedPending(updates=[]),
          verify_result=_clean_verify())
    _run(default_config(), pl.Mode.INTERACTIVE)
    assert rec.calls.count("retention") == 1
