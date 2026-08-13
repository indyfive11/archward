"""Per-emitter PHASE_RESULT statuses (v0.5 typed event protocol).

Replaces the old regex-classification spec: the rail no longer infers
status from result-message prose — every emitter declares PhaseStatus
explicitly. These tests pin each emitter's declared status to the same
rail behavior the old classifier produced (parity), so a status change
is a deliberate diff here, never an accidental wording side effect.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from archward.config.defaults import default_config
from archward.events import (
    EventBus,
    PacnewFilesPayload,
    PhaseEvent,
    PhaseEventKind,
    PhaseStatus,
)


class RecordingBus(EventBus):
    def __init__(self) -> None:
        super().__init__()
        self.results: list[PhaseEvent] = []
        self.subscribe(self._record)

    def _record(self, ev: PhaseEvent) -> None:
        if ev.kind is PhaseEventKind.PHASE_RESULT:
            self.results.append(ev)

    def last(self) -> PhaseEvent:
        assert self.results, "no PHASE_RESULT emitted"
        return self.results[-1]


# ── protocol basics ───────────────────────────────────────────────────────


def test_emit_result_requires_status() -> None:
    with pytest.raises(TypeError):
        EventBus().emit_result("verify", "message only")  # type: ignore[call-arg]


def test_unknown_phase_rejected() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        EventBus().emit_log("not-a-phase", "hi")


def test_rollback_phase_accepted() -> None:
    """The Snapshot Browser routes rollback logs through the live bus."""
    bus = RecordingBus()
    bus.emit_log("rollback", "restoring /etc/foo")  # must not raise


def test_status_vocabulary_matches_rail_glyphs() -> None:
    from archward.ui.phase_rail import _STATUS_GLYPHS

    for status in PhaseStatus:
        assert status.value in _STATUS_GLYPHS


# ── pacnew phase ──────────────────────────────────────────────────────────


def test_pacnew_none_is_pass(monkeypatch, tmp_path) -> None:
    from archward.pipeline import pacnew_phase

    monkeypatch.setattr(pacnew_phase, "find_pacnew_files", lambda since_epoch=None: [])
    bus = RecordingBus()
    pacnew_phase.scan_pacnew(default_config(), tmp_path, bus)
    assert bus.last().status is PhaseStatus.PASS


def test_pacnew_found_is_pass_with_payload(monkeypatch, tmp_path) -> None:
    """Parity: files-need-attention kept the rail green pre-v0.5; the
    attention surface is the Pacnew view + RESULT tag."""
    from archward.pipeline import pacnew_phase

    monkeypatch.setattr(
        pacnew_phase, "find_pacnew_files",
        lambda since_epoch=None: [Path("/etc/x.conf.pacnew")],
    )
    bus = RecordingBus()
    files = pacnew_phase.scan_pacnew(default_config(), tmp_path, bus)
    ev = bus.last()
    assert ev.status is PhaseStatus.PASS
    assert isinstance(ev.payload, PacnewFilesPayload)
    assert len(ev.payload.files) == len(files) == 1


# ── risk phase ────────────────────────────────────────────────────────────


def _checkupdates_stub(ok: bool):
    class _CU:
        pass

    cu = _CU()
    cu.ok = ok
    cu.error = None if ok else "checkupdates timed out"
    cu.pending = []
    return cu


def test_risk_check_failure_is_warn_and_carries_error(monkeypatch) -> None:
    from archward.events import RiskPendingPayload
    from archward.pipeline import risk

    monkeypatch.setattr(risk.pq, "checkupdates", lambda: _checkupdates_stub(False))
    bus = RecordingBus()
    risk.classify_pending(default_config(), bus)
    ev = bus.last()
    assert ev.status is PhaseStatus.WARN
    assert isinstance(ev.payload, RiskPendingPayload)
    assert ev.payload.check_error == "checkupdates timed out"


def test_risk_clean_check_is_pass(monkeypatch) -> None:
    from archward.pipeline import risk

    monkeypatch.setattr(risk.pq, "checkupdates", lambda: _checkupdates_stub(True))
    bus = RecordingBus()
    risk.classify_pending(default_config(), bus)
    assert bus.last().status is PhaseStatus.PASS


def test_risk_preview_pinned_pass_even_with_replacements(monkeypatch) -> None:
    """Preview is informational — replacements/conflicts must not flip the
    rail (pre-v0.5 parity)."""
    from archward.pipeline import risk

    class _Preview:
        package_count = 12
        replacements = [("old", "new")]
        conflicts = ["conflict warning"]

    monkeypatch.setattr(risk.pq, "preview_transaction", lambda: _Preview())
    bus = RecordingBus()
    risk.preview_transaction(bus)
    assert bus.last().status is PhaseStatus.PASS


# ── official update ───────────────────────────────────────────────────────


def _run_official(monkeypatch, code: int) -> RecordingBus:
    from archward.pipeline import update_official

    monkeypatch.setattr(
        update_official, "run_streaming",
        lambda *a, **k: (code, ["error: it broke"] if code else []),
    )
    bus = RecordingBus()

    class _S:
        def argv_prefix(self):
            return []

        def env(self):
            return {}

    update_official.run_official_update(default_config(), _S(), bus)
    return bus


def test_official_completed_is_pass(monkeypatch) -> None:
    assert _run_official(monkeypatch, 0).last().status is PhaseStatus.PASS


def test_official_failed_is_fail(monkeypatch) -> None:
    assert _run_official(monkeypatch, 1).last().status is PhaseStatus.FAIL


# ── gates ─────────────────────────────────────────────────────────────────


def test_gates_pass_and_fail_statuses(monkeypatch) -> None:
    from archward.pipeline import gates

    class _Snap:
        age_seconds = 0

    monkeypatch.setattr(gates.disk, "free_gb", lambda _p: 500)
    bus = RecordingBus()
    gates.run_gates(default_config(), _Snap(), bus)
    assert bus.last().status is PhaseStatus.PASS

    monkeypatch.setattr(gates.disk, "free_gb", lambda _p: 0)
    bus2 = RecordingBus()
    gates.run_gates(default_config(), _Snap(), bus2)
    assert bus2.last().status is PhaseStatus.FAIL


# ── hooks ─────────────────────────────────────────────────────────────────


def _run_hooks(commands: list[str], *, abort_on_failure: bool) -> RecordingBus:
    from archward.models.config import HooksConfig
    from archward.pipeline.hooks import HookRunner

    bus = RecordingBus()
    runner = HookRunner(
        HooksConfig(
            pre_update=tuple(commands),
            fail_pipeline_on_error=abort_on_failure,
        ),
        bus,
    )
    runner.run_pre_update(None)
    return bus


def test_hooks_all_pass(tmp_path) -> None:
    assert _run_hooks(["true"], abort_on_failure=False).last().status is PhaseStatus.PASS


def test_hooks_warning(tmp_path) -> None:
    assert _run_hooks(["false"], abort_on_failure=False).last().status is PhaseStatus.WARN


def test_hooks_abort_is_fail(tmp_path) -> None:
    assert _run_hooks(["false"], abort_on_failure=True).last().status is PhaseStatus.FAIL


# ── AUR phase ─────────────────────────────────────────────────────────────


def _aur_cfg(*, enabled: bool = True, skip: bool = False):
    cfg = default_config()
    return cfg.model_copy(
        update={"aur": cfg.aur.model_copy(update={"enabled": enabled, "skip": skip})}
    )


def test_aur_force_skip_is_skipped() -> None:
    from archward.pipeline import update_aur

    bus = RecordingBus()
    update_aur.run_aur_update(_aur_cfg(), None, bus, force_skip=True)
    assert bus.last().status is PhaseStatus.SKIPPED


def test_aur_no_helper_is_skipped(monkeypatch) -> None:
    from archward.pipeline import update_aur

    monkeypatch.setattr(update_aur, "discover", lambda prefs: None)
    monkeypatch.setattr(update_aur, "_list_installed_aur", lambda: [])
    bus = RecordingBus()
    update_aur.run_aur_update(_aur_cfg(), None, bus)
    assert bus.last().status is PhaseStatus.SKIPPED


class _HelperStub:
    name = "yay"

    def __init__(self, pending, exit_code=0, captured=None):
        self._pending = pending
        self._exit = exit_code
        self._captured = captured or []

    def list_pending(self):
        return self._pending

    def run_update(self, **kw):
        return self._exit, self._captured


def test_aur_no_pending_is_pass(monkeypatch) -> None:
    from archward.pipeline import update_aur

    monkeypatch.setattr(update_aur, "discover", lambda prefs: _HelperStub([]))
    bus = RecordingBus()
    update_aur.run_aur_update(_aur_cfg(), None, bus)
    assert bus.last().status is PhaseStatus.PASS


def test_aur_helper_failure_is_fail(monkeypatch, tmp_path) -> None:
    from archward.pipeline import update_aur

    monkeypatch.setattr(
        update_aur, "discover",
        lambda prefs: _HelperStub([("somepkg", "1", "2")], exit_code=1),
    )
    monkeypatch.setattr(
        "archward.aur.quarantine.AurQuarantine.state_path",
        property(lambda self: tmp_path / "quarantine.json"),
        raising=False,
    )
    bus = RecordingBus()
    update_aur.run_aur_update(_aur_cfg(), None, bus)
    assert bus.last().status is PhaseStatus.FAIL
