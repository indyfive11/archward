"""v0.4.18 "UX honesty" — Qt-free surface tests.

Covers:
- RESULT:ABORTED for declines / gate refusals / cancels (item 2)
- UPDATE_FAILED still scans pacnew + carries pacman error lines (item 3)
- per-WARN prompts, all-FAIL gate iteration, WARN can_override no longer
  inverted by gates.allow_override (item 4)
- one-shot aur.skip actually resets (item 5)
- CLI TTY prompt provider maps Enter to the prompt default (item 6)
"""

from __future__ import annotations

import threading
import tomllib
from pathlib import Path

import pytest

import archward.config.paths as paths_mod
from archward.config.defaults import default_config
from archward.events import EventBus
from archward.models.gate import GateResult, GateStatus
from archward.models.update import PendingUpdate, RiskLevel
from archward.pipeline import pipeline as pl
from archward.pipeline.report import derive_result
from archward.pipeline.risk import ClassifiedPending
from archward.pipeline.update_official import OfficialUpdateOutcome, extract_error_lines


@pytest.fixture(autouse=True)
def _tmp_lock(tmp_path, monkeypatch):
    """Keep run_pipeline's instance lock out of the real state dir."""
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
    snapshot_id = "20260810-000000"
    path = Path("/nonexistent")


class _Snapshot:
    meta = _Meta()


def _pending(risk: RiskLevel = RiskLevel.LOW, kernel: bool = False) -> ClassifiedPending:
    return ClassifiedPending(
        updates=[
            PendingUpdate(
                name="linux" if kernel else "zlib",
                old_version="1",
                new_version="2",
                risk=risk,
                is_kernel=kernel,
                source="official",
            )
        ]
    )


class _RecordingPrompter:
    """Prompter answering confirm_gate_override from a scripted list."""

    def __init__(self, gate_answers: list[bool], high_risk: bool = True) -> None:
        self._answers = list(gate_answers)
        self._high_risk = high_risk
        self.asked: list[str] = []

    def decide_high_risk(self, high):
        return self._high_risk, []

    def confirm_gate_override(self, gate: GateResult) -> bool:
        self.asked.append(gate.name)
        return self._answers.pop(0) if self._answers else False


def _wire_stub_phases(
    monkeypatch,
    calls: list[str],
    *,
    preflight=(),
    gate_results=(),
    pending: ClassifiedPending | None = None,
    official_outcome: OfficialUpdateOutcome | None = None,
    pacnew_files: list | None = None,
):
    monkeypatch.setattr(
        pl.gates_phase, "preflight_checks", lambda cfg, bus: list(preflight)
    )
    monkeypatch.setattr(
        pl.snapshot_phase,
        "take_snapshot",
        lambda cfg, strategy, bus, **kw: calls.append("snapshot") or _Snapshot(),
    )
    monkeypatch.setattr(
        pl.gates_phase, "run_gates", lambda cfg, snap, bus: list(gate_results)
    )
    monkeypatch.setattr(
        pl.risk_phase,
        "classify_pending",
        lambda cfg, bus: pending if pending is not None else ClassifiedPending(updates=[]),
    )
    monkeypatch.setattr(
        pl.risk_phase,
        "preview_transaction",
        lambda bus: type("P", (), {"replacements": []})(),
    )

    def _official(cfg, strategy, bus, **kw):
        calls.append("official")
        return official_outcome or OfficialUpdateOutcome(exit_code=0)

    monkeypatch.setattr(pl.update_official, "run_official_update", _official)
    monkeypatch.setattr(
        pl.update_aur,
        "run_aur_update",
        lambda cfg, strategy, bus, **kw: calls.append("aur") or None,
    )
    monkeypatch.setattr(
        pl.pacnew_phase,
        "scan_pacnew",
        lambda cfg, path, bus: calls.append("pacnew") or list(pacnew_files or []),
    )
    monkeypatch.setattr(
        pl.verify_phase,
        "run_verify",
        lambda cfg, snap, bus, **kw: calls.append("verify") or None,
    )
    monkeypatch.setattr(pl.retention, "prune_snapshots", lambda cfg: [])


def _run(cfg=None, *, prompter, mode=pl.Mode.INTERACTIVE):
    return pl.run_pipeline(
        cfg or default_config(),
        _Strategy(),
        EventBus(),
        mode,
        cancel_event=threading.Event(),
        prompter=prompter,
    )


# ── derive_result: ABORTED semantics ──────────────────────────────────────


def test_aborted_tag() -> None:
    s = derive_result(
        preflight_failed=True,
        update_exit_code=None,
        pending=[],
        verify=None,
        pacnew_count=0,
        aborted=True,
    )
    assert s.tag == "RESULT:ABORTED"
    assert s.secondary_tags == ()


def test_aborted_after_applied_kernel_update_flags_reboot() -> None:
    """Cancel AFTER a successful kernel update: ABORTED, but the reboot the
    applied update requires is not hidden."""
    p = PendingUpdate(
        name="linux", old_version="1", new_version="2",
        risk=RiskLevel.HIGH, is_kernel=True, source="official",
    )
    s = derive_result(
        preflight_failed=False,
        update_exit_code=0,
        pending=[p],
        verify=None,
        pacnew_count=2,
        aborted=True,
    )
    assert s.tag == "RESULT:ABORTED"
    assert s.reboot_needed is True
    assert "RESULT:REBOOT_NEEDED" in s.secondary_tags
    assert "RESULT:PACNEW_MERGE_NEEDED" in s.secondary_tags


def test_update_failed_carries_pacnew_secondary() -> None:
    s = derive_result(
        preflight_failed=False,
        update_exit_code=1,
        pending=[],
        verify=None,
        pacnew_count=1,
    )
    assert s.tag == "RESULT:UPDATE_FAILED"
    assert "RESULT:PACNEW_MERGE_NEEDED" in s.secondary_tags


# ── pipeline: abort paths yield ABORTED ───────────────────────────────────


def test_user_decline_high_risk_is_aborted(monkeypatch) -> None:
    calls: list[str] = []
    _wire_stub_phases(monkeypatch, calls, pending=_pending(RiskLevel.HIGH))
    result = _run(prompter=_RecordingPrompter([], high_risk=False))
    assert result.aborted_reason == "user declined HIGH RISK update"
    assert result.summary.tag == "RESULT:ABORTED"
    assert "official" not in calls


def test_preflight_fail_is_aborted(monkeypatch) -> None:
    calls: list[str] = []
    fail = GateResult(name="pacman-db-lock", status=GateStatus.FAIL, message="locked")
    _wire_stub_phases(monkeypatch, calls, preflight=[fail])
    result = _run(prompter=_RecordingPrompter([]))
    assert result.preflight_failed is True
    assert result.summary.tag == "RESULT:ABORTED"


# ── pipeline: failed update honesty ──────────────────────────────────────


def test_failed_update_still_scans_pacnew_and_carries_errors(monkeypatch) -> None:
    calls: list[str] = []
    err = ("error: failed to commit transaction (conflicting files)",)
    _wire_stub_phases(
        monkeypatch,
        calls,
        pending=_pending(),
        official_outcome=OfficialUpdateOutcome(exit_code=1, error_lines=err),
        pacnew_files=["/etc/x.conf.pacnew"],
    )
    result = _run(prompter=_RecordingPrompter([]))
    assert result.summary.tag == "RESULT:UPDATE_FAILED"
    assert result.update_error_lines == err
    assert "pacnew" in calls          # scan still ran
    assert result.pacnew_count == 1
    assert "RESULT:PACNEW_MERGE_NEEDED" in result.summary.secondary_tags
    assert "aur" not in calls and "verify" not in calls


def test_extract_error_lines_prefers_error_markers() -> None:
    captured = [
        ":: Synchronizing package databases...",
        "error: failed retrieving file 'core.db'",
        "some progress line",
        "error: failed to synchronize all databases",
    ]
    assert extract_error_lines(captured) == (
        "error: failed retrieving file 'core.db'",
        "error: failed to synchronize all databases",
    )


def test_extract_error_lines_falls_back_to_tail() -> None:
    captured = ["line one", "", "line two", "killed"]
    assert extract_error_lines(captured) == ("line one", "line two", "killed")


# ── pipeline: gate/WARN prompting ────────────────────────────────────────


def _warn(name: str) -> GateResult:
    return GateResult(
        name=name, status=GateStatus.WARN, message=f"{name} warns",
        detail="detail", can_override=True,
    )


def test_every_preflight_warn_gets_its_own_prompt(monkeypatch) -> None:
    calls: list[str] = []
    _wire_stub_phases(
        monkeypatch, calls, preflight=[_warn("cache-safety"), _warn("arch-news")]
    )
    prompter = _RecordingPrompter([True, True])
    result = _run(prompter=prompter)
    assert prompter.asked == ["cache-safety", "arch-news"]
    assert result.aborted_reason is None


def test_declining_second_warn_aborts(monkeypatch) -> None:
    calls: list[str] = []
    _wire_stub_phases(
        monkeypatch, calls, preflight=[_warn("cache-safety"), _warn("arch-news")]
    )
    prompter = _RecordingPrompter([True, False])
    result = _run(prompter=prompter)
    assert result.summary.tag == "RESULT:ABORTED"
    assert "arch-news" in result.aborted_reason
    assert "snapshot" not in calls


def _gate_fail(name: str) -> GateResult:
    return GateResult(
        name=name, status=GateStatus.FAIL, message=f"{name} failed",
        can_override=True,
    )


def test_all_gate_fails_are_prompted(monkeypatch) -> None:
    """Overriding the first FAIL must not silently skip the second."""
    calls: list[str] = []
    _wire_stub_phases(
        monkeypatch, calls,
        gate_results=[_gate_fail("snapshot-age"), _gate_fail("disk-space")],
    )
    prompter = _RecordingPrompter([True, False])
    result = _run(prompter=prompter)
    assert prompter.asked == ["snapshot-age", "disk-space"]
    assert result.summary.tag == "RESULT:ABORTED"
    assert "disk-space" in result.aborted_reason


def test_overriding_every_gate_fail_proceeds(monkeypatch) -> None:
    calls: list[str] = []
    _wire_stub_phases(
        monkeypatch, calls,
        gate_results=[_gate_fail("snapshot-age"), _gate_fail("disk-space")],
    )
    result = _run(prompter=_RecordingPrompter([True, True]))
    assert result.aborted_reason is None
    assert "aur" in calls


def test_warn_can_override_not_tied_to_allow_override(monkeypatch) -> None:
    """gates.allow_override=False (strict) must NOT remove the WARN bail
    prompt — that inverted the setting pre-v0.4.18."""
    from archward.pipeline import gates
    from archward.system import cache_policy as cp

    monkeypatch.setattr(gates, "check_pacman_db_lock", lambda: (False, None))
    monkeypatch.setattr(gates.an, "fetch_news", lambda: [])
    monkeypatch.setattr(gates, "latest_snapshot", lambda d: None)
    monkeypatch.setattr(
        gates.cp,
        "detect_cache_policy",
        lambda: cp.CachePolicy(
            timer_state="disabled",
            paccache_args="",
            effective_keep=3,
            clean_method=("KeepInstalled",),
            cleaning_hooks=(Path("/etc/pacman.d/hooks/zz-clean.hook"),),
            cache_size_bytes=0,
            cache_file_count=0,
            safety=cp.RollbackSafety.DANGEROUS,
            explanation="a hook will eat your rollback",
        ),
    )
    cfg = default_config()
    cfg = cfg.model_copy(
        update={"gates": cfg.gates.model_copy(update={"allow_override": False})}
    )
    results = gates.preflight_checks(cfg, EventBus())
    cs = next(r for r in results if r.name == "cache-safety")
    assert cs.status is GateStatus.WARN
    assert cs.can_override is True


# ── pipeline: one-shot aur.skip reset ────────────────────────────────────


def test_aur_skip_one_shot_resets_in_config(monkeypatch, tmp_path) -> None:
    calls: list[str] = []
    _wire_stub_phases(monkeypatch, calls)
    cfg = default_config()
    cfg = cfg.model_copy(update={"aur": cfg.aur.model_copy(update={"skip": True})})
    config_path = tmp_path / "config.toml"

    result = pl.run_pipeline(
        cfg,
        _Strategy(),
        EventBus(),
        pl.Mode.INTERACTIVE,
        cancel_event=threading.Event(),
        prompter=_RecordingPrompter([]),
        config_path=config_path,
    )

    assert result.config_rewritten is True
    written = tomllib.loads(config_path.read_text())
    assert written["aur"]["skip"] is False


def test_aur_skip_untouched_when_not_set(monkeypatch, tmp_path) -> None:
    calls: list[str] = []
    _wire_stub_phases(monkeypatch, calls)
    config_path = tmp_path / "config.toml"

    result = pl.run_pipeline(
        default_config(),
        _Strategy(),
        EventBus(),
        pl.Mode.INTERACTIVE,
        cancel_event=threading.Event(),
        prompter=_RecordingPrompter([]),
        config_path=config_path,
    )

    assert result.config_rewritten is False
    assert not config_path.exists()


# ── CLI TTY prompt provider ──────────────────────────────────────────────


def test_tty_provider_forwards_answer(monkeypatch) -> None:
    from archward.cli import _tty_prompt_provider
    from archward.pacman.prompts import PromptKind

    monkeypatch.setattr("builtins.input", lambda _prompt: "n")
    assert _tty_prompt_provider(":: Proceed with installation? [Y/n]", PromptKind.YES_NO) == "n"


def test_tty_provider_maps_enter_to_default(monkeypatch) -> None:
    """The runner treats '' as CANCEL — a bare Enter must become the prompt
    default instead."""
    from archward.cli import _tty_prompt_provider
    from archward.pacman.prompts import PromptKind

    monkeypatch.setattr("builtins.input", lambda _prompt: "")
    assert _tty_prompt_provider(":: Proceed? [Y/n]", PromptKind.YES_NO) == "Y"


def test_tty_provider_eof_returns_default(monkeypatch) -> None:
    from archward.cli import _tty_prompt_provider
    from archward.pacman.prompts import PromptKind

    def _raise(_prompt):
        raise EOFError

    monkeypatch.setattr("builtins.input", _raise)
    assert _tty_prompt_provider(":: Proceed? [Y/n]", PromptKind.YES_NO) == "Y"
