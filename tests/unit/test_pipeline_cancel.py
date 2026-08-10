"""Cancel is honored at phase boundaries (v0.4.17).

A cancel that arrives while pacman runs (the subprocess is allowed to finish)
must not launch the AUR helper, pacnew scan, verify, or hooks afterwards.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

import archward.config.paths as paths_mod
from archward.config.defaults import default_config
from archward.events import EventBus
from archward.models.update import PendingUpdate, RiskLevel
from archward.pipeline import pipeline as pl
from archward.pipeline.risk import ClassifiedPending


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


def _pending_one() -> ClassifiedPending:
    return ClassifiedPending(
        updates=[
            PendingUpdate(
                name="zlib",
                old_version="1",
                new_version="2",
                risk=RiskLevel.LOW,
                source="official",
            )
        ]
    )


def _wire_stub_phases(monkeypatch, calls: list[str], *, cancel_during_update=None):
    monkeypatch.setattr(pl.gates_phase, "preflight_checks", lambda cfg, bus: [])
    monkeypatch.setattr(
        pl.snapshot_phase,
        "take_snapshot",
        lambda cfg, strategy, bus, **kw: calls.append("snapshot") or _Snapshot(),
    )
    monkeypatch.setattr(pl.gates_phase, "run_gates", lambda cfg, snap, bus: [])
    monkeypatch.setattr(
        pl.risk_phase, "classify_pending", lambda cfg, bus: _pending_one()
    )
    monkeypatch.setattr(
        pl.risk_phase, "preview_transaction", lambda bus: type("P", (), {"replacements": []})()
    )

    def _official(cfg, strategy, bus, **kw):
        calls.append("official")
        if cancel_during_update is not None:
            cancel_during_update.set()
        return pl.update_official.OfficialUpdateOutcome(exit_code=0)

    monkeypatch.setattr(pl.update_official, "run_official_update", _official)
    monkeypatch.setattr(
        pl.update_aur,
        "run_aur_update",
        lambda cfg, strategy, bus, **kw: calls.append("aur") or None,
    )
    monkeypatch.setattr(
        pl.pacnew_phase,
        "scan_pacnew",
        lambda cfg, path, bus: calls.append("pacnew") or [],
    )
    monkeypatch.setattr(
        pl.verify_phase,
        "run_verify",
        lambda cfg, snap, bus, **kw: calls.append("verify") or None,
    )
    monkeypatch.setattr(pl.retention, "prune_snapshots", lambda cfg: [])


def test_cancel_during_official_update_skips_all_later_phases(monkeypatch):
    calls: list[str] = []
    cancel = threading.Event()
    _wire_stub_phases(monkeypatch, calls, cancel_during_update=cancel)

    result = pl.run_pipeline(
        default_config(),
        _Strategy(),
        EventBus(),
        pl.Mode.INTERACTIVE,
        cancel_event=cancel,
        prompter=pl.AutoYesPrompter(),
    )

    assert calls == ["snapshot", "official"]
    assert result.aborted_reason == "cancelled by user"
    assert result.summary is not None
    assert result.summary.tag == "RESULT:ABORTED"


def test_cancel_before_snapshot_returns_before_risk(monkeypatch):
    calls: list[str] = []
    cancel = threading.Event()
    cancel.set()
    _wire_stub_phases(monkeypatch, calls)

    result = pl.run_pipeline(
        default_config(),
        _Strategy(),
        EventBus(),
        pl.Mode.INTERACTIVE,
        cancel_event=cancel,
        prompter=pl.AutoYesPrompter(),
    )

    # Snapshot runs (already in flight semantics: checked at the boundary
    # AFTER it), everything later is skipped.
    assert calls == ["snapshot"]
    assert result.aborted_reason == "cancelled by user"


def test_no_cancel_runs_all_phases(monkeypatch):
    """Sanity: with no cancel the same stub wiring reaches every phase."""
    calls: list[str] = []
    _wire_stub_phases(monkeypatch, calls)

    result = pl.run_pipeline(
        default_config(),
        _Strategy(),
        EventBus(),
        pl.Mode.INTERACTIVE,
        cancel_event=threading.Event(),
        prompter=pl.AutoYesPrompter(),
    )

    assert calls == ["snapshot", "official", "aur", "pacnew", "verify"]
    assert result.aborted_reason is None
