"""Run-history artifact: recorder, writer, retention, and the
`archward history` CLI surface.

Integration tests route through run_pipeline (BOTH lock arms — the
pressure test caught that the CLI takes the acquire_instance_lock=False
early branch) with stubbed phases writing REAL files to a tmp state dir,
so a lying fixture can't green-light a writer that never runs.
"""

from __future__ import annotations

import argparse
import json
import threading
from pathlib import Path

import pytest

import archward.config.paths as paths_mod
from archward.config.defaults import default_config
from archward.events import EventBus, Phase, PhaseStatus
from archward.models.update import PendingUpdate, RiskLevel
from archward.models.verify import VerifyResult
from archward.pipeline import pipeline as pl
from archward.pipeline import run_record
from archward.pipeline.risk import ClassifiedPending
from archward.pipeline.update_official import OfficialUpdateOutcome


@pytest.fixture(autouse=True)
def _tmp_state(tmp_path, monkeypatch):
    monkeypatch.setattr(paths_mod, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(paths_mod, "lock_file", lambda: tmp_path / "archward.lock")
    return tmp_path


class _Strategy:
    def argv_prefix(self):
        return []

    def env(self):
        return {}

    def warmup(self):
        return True


class _Meta:
    snapshot_id = "2026-08-13_000000"
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


def _clean_verify() -> VerifyResult:
    return VerifyResult(checks=(), fail_count=0, warn_count=0, reboot_needed=False)


def _wire(monkeypatch, *, pending: ClassifiedPending,
          verify_result: VerifyResult | None = None,
          snapshot_raises: bool = False):
    def _take_snapshot(cfg, strategy, bus, snapshot_id=None, **kw):
        bus.emit_start(Phase.SNAPSHOT, "Snapshot")
        if snapshot_raises:
            raise RuntimeError("disk on fire")
        bus.emit_result(Phase.SNAPSHOT, "Snapshot complete", PhaseStatus.PASS)
        return _Snapshot()

    monkeypatch.setattr(pl.gates_phase, "preflight_checks", lambda cfg, bus: [])
    monkeypatch.setattr(pl.snapshot_phase, "take_snapshot", _take_snapshot)
    monkeypatch.setattr(pl.gates_phase, "run_gates", lambda cfg, snap, bus: [])
    monkeypatch.setattr(pl.risk_phase, "classify_pending", lambda cfg, bus: pending)
    monkeypatch.setattr(
        pl.risk_phase, "preview_transaction",
        lambda bus: type("P", (), {"replacements": []})(),
    )
    monkeypatch.setattr(
        pl.update_official, "run_official_update",
        lambda cfg, strategy, bus, **kw: OfficialUpdateOutcome(exit_code=0),
    )
    monkeypatch.setattr(
        pl.update_aur, "run_aur_update", lambda cfg, strategy, bus, **kw: None
    )
    monkeypatch.setattr(pl.pacnew_phase, "scan_pacnew", lambda cfg, path, bus: [])
    monkeypatch.setattr(
        pl.verify_phase, "run_verify",
        lambda cfg, snap, bus, **kw: verify_result,
    )
    monkeypatch.setattr(pl.retention, "prune_snapshots", lambda cfg: [])


def _run(cfg, mode, *, acquire_instance_lock=True):
    return pl.run_pipeline(
        cfg,
        _Strategy(),
        EventBus(),
        mode,
        cancel_event=threading.Event(),
        prompter=pl.AutoYesPrompter(),
        acquire_instance_lock=acquire_instance_lock,
    )


def _run_docs(tmp_path) -> list[dict]:
    return [
        json.loads(p.read_text())
        for p in sorted((tmp_path / "runs").glob("*.json"))
    ]


# ── recorder unit ─────────────────────────────────────────────────────────


def test_recorder_captures_phase_timeline(_tmp_state) -> None:
    bus = EventBus()
    rec = run_record.RunRecorder(bus)
    bus.emit_start(Phase.GATES, "Gates")
    bus.emit_result(Phase.GATES, "gates ok", PhaseStatus.PASS)
    bus.emit_log(Phase.GATES, "log lines are not phase events")
    rec.finalize(
        cfg=default_config(), mode=pl.Mode.INTERACTIVE, no_aur=False,
        result=pl.PipelineResult(), error=None,
    )
    docs = _run_docs(_tmp_state)
    assert len(docs) == 1
    (doc,) = docs
    assert doc["schema_version"] == run_record.SCHEMA_VERSION
    (phase,) = doc["phases"]
    assert phase["phase"] == "gates"
    assert phase["status"] == "pass"
    assert phase["message"] == "gates ok"
    assert phase["duration_s"] >= 0
    assert phase["started"] and phase["finished"]


def test_recorder_ignores_events_after_finalize(_tmp_state) -> None:
    bus = EventBus()
    rec = run_record.RunRecorder(bus)
    rec.finalize(
        cfg=default_config(), mode=pl.Mode.INTERACTIVE, no_aur=False,
        result=pl.PipelineResult(), error=None,
    )
    # Snapshot Browser rollback events keep flowing on the GUI's old bus.
    bus.emit_start(Phase.ROLLBACK, "rollback")
    bus.emit_result(Phase.ROLLBACK, "done", PhaseStatus.PASS)
    (doc,) = _run_docs(_tmp_state)
    assert doc["phases"] == []
    # And a second finalize doesn't write a second file.
    rec.finalize(
        cfg=default_config(), mode=pl.Mode.INTERACTIVE, no_aur=False,
        result=pl.PipelineResult(), error=None,
    )
    assert len(_run_docs(_tmp_state)) == 1


def test_write_collision_gets_suffix(_tmp_state) -> None:
    doc = {"run_id": "2026-08-13_120000", "schema_version": 1}
    p1 = run_record.write_run_document(dict(doc))
    p2 = run_record.write_run_document(dict(doc))
    assert p1.name == "2026-08-13_120000.json"
    assert p2.name == "2026-08-13_120000-2.json"
    assert json.loads(p2.read_text())["run_id"] == "2026-08-13_120000-2"


def test_collision_suffix_sorts_newer_than_base(_tmp_state) -> None:
    # '-' < '.' lexicographically, so a naive name sort would call the -2
    # collision file OLDER than its base — `history show latest` and prune
    # order must treat it as newer.
    doc = {"run_id": "2026-08-13_120000", "schema_version": 1}
    run_record.write_run_document(dict(doc))
    run_record.write_run_document(dict(doc))
    names = [p.stem for p in run_record.list_run_files()]
    assert names == ["2026-08-13_120000-2", "2026-08-13_120000"]
    removed = run_record.prune_runs(1)
    assert [p.stem for p in removed] == ["2026-08-13_120000"]


def test_prune_runs_keeps_newest(_tmp_state) -> None:
    for hour in ("10", "11", "12"):
        run_record.write_run_document(
            {"run_id": f"2026-08-13_{hour}0000", "schema_version": 1}
        )
    removed = run_record.prune_runs(2)
    assert [p.name for p in removed] == ["2026-08-13_100000.json"]
    assert [p.stem for p in run_record.list_run_files()] == [
        "2026-08-13_120000", "2026-08-13_110000",
    ]


# ── pipeline integration: both lock arms ──────────────────────────────────


@pytest.mark.parametrize("lock_arm", [True, False], ids=["gui-arm", "cli-arm"])
def test_success_run_writes_record_on_both_arms(monkeypatch, _tmp_state, lock_arm) -> None:
    _wire(monkeypatch, pending=_pending(("zlib", RiskLevel.LOW)),
          verify_result=_clean_verify())
    result = _run(default_config(), pl.Mode.INTERACTIVE,
                  acquire_instance_lock=lock_arm)
    assert result.summary.tag == "RESULT:SUCCESS"
    (doc,) = _run_docs(_tmp_state)
    assert doc["result"]["tag"] == "RESULT:SUCCESS"
    assert doc["mode"] == "interactive"
    assert doc["snapshot_id"] == "2026-08-13_000000"
    assert doc["pending"] == [{
        "name": "zlib", "old_version": "1", "new_version": "2",
        "source": "official", "risk": "low", "is_kernel": False, "reason": None,
    }]
    phase_names = [p["phase"] for p in doc["phases"]]
    assert "snapshot" in phase_names
    assert doc["verify"] == {
        "fail_count": 0, "warn_count": 0, "skipped_count": 0, "checks": [],
    }


def test_aborted_run_records_reason(monkeypatch, _tmp_state) -> None:
    _wire(monkeypatch, pending=_pending(("linux", RiskLevel.HIGH)))
    result = _run(default_config(), pl.Mode.AUTO)
    assert result.summary.tag == "RESULT:ABORTED"
    (doc,) = _run_docs(_tmp_state)
    assert doc["result"]["tag"] == "RESULT:ABORTED"
    assert doc["aborted_reason"] == "HIGH RISK packages present in auto mode"


def test_dry_run_recorded_with_mode(monkeypatch, _tmp_state) -> None:
    _wire(monkeypatch, pending=ClassifiedPending(updates=[]))
    _run(default_config(), pl.Mode.DRY_RUN)
    (doc,) = _run_docs(_tmp_state)
    assert doc["mode"] == "dry-run"
    assert doc["result"]["tag"] == "RESULT:SUCCESS"


def test_crash_writes_partial_record_and_reraises(monkeypatch, _tmp_state) -> None:
    _wire(monkeypatch, pending=ClassifiedPending(updates=[]), snapshot_raises=True)
    with pytest.raises(RuntimeError, match="disk on fire"):
        _run(default_config(), pl.Mode.INTERACTIVE)
    (doc,) = _run_docs(_tmp_state)
    assert doc["result"] is None
    assert doc["aborted_reason"] == "exception: RuntimeError"
    # The snapshot phase started but never finished.
    snap_phases = [p for p in doc["phases"] if p["phase"] == "snapshot"]
    assert len(snap_phases) == 1
    assert snap_phases[0]["finished"] is None
    assert snap_phases[0]["status"] is None


def test_lock_contention_writes_no_record(monkeypatch, _tmp_state) -> None:
    import fcntl

    _wire(monkeypatch, pending=ClassifiedPending(updates=[]))
    lock_path = _tmp_state / "archward.lock"
    with open(lock_path, "w") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = _run(default_config(), pl.Mode.INTERACTIVE)
    assert result.summary.tag == "RESULT:ABORTED"
    assert not (_tmp_state / "runs").exists()


def test_unwritable_runs_dir_warns_and_does_not_break_the_run(monkeypatch, _tmp_state) -> None:
    # A FILE named "runs" makes mkdir fail — recording must degrade to a
    # bus WARN (visible in the log pane), never break the run itself.
    (_tmp_state / "runs").write_text("not a directory")
    _wire(monkeypatch, pending=ClassifiedPending(updates=[]),
          verify_result=_clean_verify())
    bus = EventBus()
    logs: list[str] = []
    bus.subscribe(lambda ev: logs.append(ev.message or ""))
    result = pl.run_pipeline(
        default_config(), _Strategy(), bus, pl.Mode.INTERACTIVE,
        cancel_event=threading.Event(), prompter=pl.AutoYesPrompter(),
    )
    assert result.summary.tag == "RESULT:SUCCESS"
    assert (_tmp_state / "runs").is_file()  # no record was written
    assert any("could not write the run-history record" in m for m in logs)


def test_after_snapshot_yields_second_phase_entry(monkeypatch, _tmp_state) -> None:
    _wire(monkeypatch, pending=ClassifiedPending(updates=[]),
          verify_result=_clean_verify())
    cfg = default_config()
    cfg = cfg.model_copy(update={
        "general": cfg.general.model_copy(update={"after_snapshot": True}),
    })
    _run(cfg, pl.Mode.INTERACTIVE)
    (doc,) = _run_docs(_tmp_state)
    snaps = [p for p in doc["phases"] if p["phase"] == "snapshot"]
    assert len(snaps) == 2
    assert all(p["status"] == "pass" for p in snaps)


def test_retention_prunes_after_run(monkeypatch, _tmp_state) -> None:
    for hour in ("01", "02", "03"):
        run_record.write_run_document(
            {"run_id": f"2026-08-12_{hour}0000", "schema_version": 1}
        )
    _wire(monkeypatch, pending=ClassifiedPending(updates=[]),
          verify_result=_clean_verify())
    cfg = default_config()
    cfg = cfg.model_copy(update={
        "general": cfg.general.model_copy(update={"keep_runs": 2}),
    })
    _run(cfg, pl.Mode.INTERACTIVE)
    names = [p.stem for p in run_record.list_run_files()]
    assert len(names) == 2
    assert "2026-08-12_010000" not in names
    assert "2026-08-12_020000" not in names  # pruned too: new run + newest old


# ── CLI surface ───────────────────────────────────────────────────────────


def _seed_run(tmp_path, run_id: str, tag: str = "RESULT:SUCCESS") -> None:
    run_record.write_run_document({
        "schema_version": 1,
        "run_id": run_id,
        "started": "2026-08-13T08:00:00-06:00",
        "finished": "2026-08-13T08:01:00-06:00",
        "duration_s": 60.0,
        "archward_version": "0.5.0",
        "mode": "interactive",
        "no_aur": False,
        "result": {"tag": tag, "secondary_tags": [], "fail_count": 0,
                   "warn_count": 0, "reboot_needed": False},
        "aborted_reason": None,
        "update_exit_code": 0,
        "update_error_lines": [],
        "snapshot_id": "2026-08-13_075959",
        "pacnew_count": 0,
        "deselected_packages": [],
        "pending": [],
        "phases": [{"phase": "gates", "status": "pass", "message": "ok",
                    "started": None, "finished": None, "duration_s": 1.5}],
        "verify": None,
        "aur": None,
        "hooks": {"pre": [], "post": []},
        "config_rewritten": False,
    })


def test_history_list_table_and_json(_tmp_state, capsys) -> None:
    from archward.cli_subcommands import history

    _seed_run(_tmp_state, "2026-08-13_080000")
    _seed_run(_tmp_state, "2026-08-13_090000", tag="RESULT:UPDATE_FAILED")

    args = argparse.Namespace(limit=20, all=False, json=False)
    assert history.cmd_list(args, None) == 0
    out = capsys.readouterr().out
    assert "2026-08-13_090000" in out and "RESULT:UPDATE_FAILED" in out

    args = argparse.Namespace(limit=20, all=False, json=True)
    assert history.cmd_list(args, None) == 0
    rows = json.loads(capsys.readouterr().out)
    assert [r["run_id"] for r in rows] == ["2026-08-13_090000", "2026-08-13_080000"]
    assert set(rows[0]) == {
        "run_id", "started", "duration_s", "mode", "tag",
        "fail_count", "warn_count", "snapshot_id",
    }


def test_history_bare_invocation_defaults_to_list(_tmp_state, capsys) -> None:
    # `archward history` parses to history_action=None with NO list args —
    # the dispatch default must survive the missing attributes.
    from archward import cli

    _seed_run(_tmp_state, "2026-08-13_080000")
    args = argparse.Namespace(command="history", history_action=None)
    assert cli._dispatch_subcommand(args, None) == 0
    assert "2026-08-13_080000" in capsys.readouterr().out


def test_history_show_human_latest_and_json(_tmp_state, capsys) -> None:
    from archward.cli_subcommands import history

    _seed_run(_tmp_state, "2026-08-13_080000")
    _seed_run(_tmp_state, "2026-08-13_090000")

    args = argparse.Namespace(run_id="latest", json=False)
    assert history.cmd_show(args, None) == 0
    out = capsys.readouterr().out
    assert "Run: 2026-08-13_090000" in out
    assert "RESULT:SUCCESS" in out

    args = argparse.Namespace(run_id="2026-08-13_080000", json=True)
    assert history.cmd_show(args, None) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["run_id"] == "2026-08-13_080000"


def test_history_show_not_found_exits_3(_tmp_state, capsys) -> None:
    from archward.cli_subcommands import history

    args = argparse.Namespace(run_id="2020-01-01_000000", json=False)
    assert history.cmd_show(args, None) == 3
    assert "run not found" in capsys.readouterr().err


def test_history_show_json_on_corrupt_file_exits_3(_tmp_state, capsys) -> None:
    # A corrupt record must exit 3 on the --json path too — never pipe
    # garbage to a scripting consumer with exit 0.
    from archward.cli_subcommands import history

    runs = _tmp_state / "runs"
    runs.mkdir()
    (runs / "2026-08-13_080000.json").write_text("{truncated")
    args = argparse.Namespace(run_id="2026-08-13_080000", json=True)
    assert history.cmd_show(args, None) == 3
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "unreadable run file" in captured.err
