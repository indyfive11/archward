"""Per-run history artifact.

Every pipeline run writes a structured JSON record to
``state_dir()/runs/<run_id>.json`` — the audit trail behind
``archward history`` and the GUI Run History dialog, and the stable
scripting surface (``archward history list --json`` / ``show <id> --json``).

The artifact schema is a PUBLIC CONTRACT (like the RESULT: tag strings,
see pipeline/report.py): fields may be ADDED without a version bump and
consumers must ignore unknown fields; ``schema_version`` increments only
when a field is renamed, removed, or changes shape. It is a separate
namespace from config.toml's ``schema_version``.

The document is hand-built from the pipeline's models rather than
``model_dump()``-ed so internal model changes never leak into the contract.

RunRecorder subscribes to the per-run EventBus to capture the per-phase
timeline (started/finished/status/duration). ``phases`` is an ordered list
of phase *executions*: the optional after-snapshot legitimately produces a
second ``snapshot`` entry. A phase absent from the list never started
(unconfigured hooks, verify disabled, or the run aborted earlier).
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from archward import __version__
from archward.config import paths
from archward.events import EventBus, Phase, PhaseEvent, PhaseEventKind

if TYPE_CHECKING:  # pragma: no cover - import cycle guard (pipeline imports us)
    from archward.pipeline.pipeline import Mode, PipelineResult

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1

# Phases that never belong in a run's phases[] timeline: "pipeline" is
# run-level bookkeeping (its data is the document's top level) and
# "rollback" events come from the Snapshot Browser between runs.
_EXCLUDED_PHASES = frozenset({Phase.PIPELINE, Phase.ROLLBACK})


def runs_dir() -> Path:
    """The run-history directory. Late-bound through the paths module so
    test fixtures that monkeypatch ``archward.config.paths`` attributes
    redirect us too."""
    return paths.state_dir() / "runs"


def _aware_now() -> datetime:
    return datetime.now().astimezone()


def _aware(dt: datetime) -> datetime:
    """Attach the local timezone to naive datetimes (PhaseEvent.timestamp
    is naive local, events.py:126)."""
    return dt.astimezone() if dt.tzinfo is None else dt


class RunRecorder:
    """Captures the per-phase timeline off the run's EventBus and writes the
    run document when the pipeline finishes (including crash paths).

    Thread-safety: pipeline phases emit from the worker thread, but the GUI
    main thread emits PHASE_LOG on the same live bus mid-run, and the bus
    outlives the run on the GUI side (Snapshot Browser rollback events keep
    flowing) — hence the lock and the ``_finalized`` guard.
    """

    def __init__(self, bus: EventBus) -> None:
        self._lock = threading.Lock()
        self._phases: list[dict[str, Any]] = []
        self._started = _aware_now()
        self._finalized = False
        self._discarded = False
        bus.subscribe(self._on_event)

    # ── event capture ────────────────────────────────────────────────────

    def _on_event(self, ev: PhaseEvent) -> None:
        # A raising subscriber would kill the pipeline thread (events.py has
        # no isolation) — never let recording break the run.
        try:
            if ev.kind not in (PhaseEventKind.PHASE_START, PhaseEventKind.PHASE_RESULT):
                return
            if ev.phase in _EXCLUDED_PHASES:
                return
            with self._lock:
                if self._finalized:
                    return
                if ev.kind is PhaseEventKind.PHASE_START:
                    self._phases.append(
                        {
                            "phase": str(ev.phase),
                            "status": None,
                            "message": None,
                            "started": _aware(ev.timestamp).isoformat(),
                            "finished": None,
                            "duration_s": None,
                            "_started_dt": _aware(ev.timestamp),
                        }
                    )
                else:  # PHASE_RESULT — close the last open entry for this phase
                    entry = None
                    for cand in reversed(self._phases):
                        if cand["phase"] == str(ev.phase):
                            entry = cand
                            break
                    if entry is None:
                        # result-without-start (defensive): record it anyway
                        entry = {
                            "phase": str(ev.phase),
                            "status": None,
                            "message": None,
                            "started": None,
                            "finished": None,
                            "duration_s": None,
                            "_started_dt": None,
                        }
                        self._phases.append(entry)
                    # last result wins within an execution
                    finished = _aware(ev.timestamp)
                    entry["status"] = str(ev.status) if ev.status is not None else None
                    entry["message"] = ev.message
                    entry["finished"] = finished.isoformat()
                    started_dt = entry.get("_started_dt")
                    if started_dt is not None:
                        entry["duration_s"] = max(
                            0.0, round((finished - started_dt).total_seconds(), 3)
                        )
        except Exception:  # noqa: BLE001
            log.exception("run recorder failed to process an event; continuing")

    # ── finalize ─────────────────────────────────────────────────────────

    def discard(self) -> None:
        """Mark this run as not-to-be-recorded (lock-contention abort:
        nothing ran, another instance owns the real run)."""
        with self._lock:
            self._discarded = True
            self._finalized = True

    def finalize(
        self,
        *,
        cfg: Any,
        mode: "Mode",
        no_aur: bool,
        result: "PipelineResult | None",
        error: BaseException | None,
        bus: EventBus | None = None,
    ) -> None:
        """Build + write the run document. Never raises — a recording
        failure must not turn a finished run into a crashed one."""
        try:
            with self._lock:
                if self._finalized:
                    return
                self._finalized = True
                phases = [
                    {k: v for k, v in entry.items() if not k.startswith("_")}
                    for entry in self._phases
                ]
            doc = build_run_document(
                started=self._started,
                finished=_aware_now(),
                mode=mode,
                no_aur=no_aur,
                result=result,
                error=error,
                phases=phases,
            )
            path = write_run_document(doc)
            if path is None:
                # write_run_document already logged the failure; surface it
                # on the bus so the log pane shows it (scope: WARN, not silent).
                self._warn_on_bus(bus)
                return
            keep = getattr(getattr(cfg, "general", None), "keep_runs", 0)
            if keep and keep > 0:
                prune_runs(keep)
        except Exception:  # noqa: BLE001
            log.exception("failed to write run-history record; continuing")
            self._warn_on_bus(bus)

    @staticmethod
    def _warn_on_bus(bus: EventBus | None) -> None:
        if bus is None:
            return
        try:
            bus.emit_log(
                Phase.PIPELINE,
                "WARN: could not write the run-history record (see log).",
            )
        except Exception:  # noqa: BLE001
            pass


# ── document build / write / read ────────────────────────────────────────


def build_run_document(
    *,
    started: datetime,
    finished: datetime,
    mode: "Mode",
    no_aur: bool,
    result: "PipelineResult | None",
    error: BaseException | None,
    phases: list[dict[str, Any]],
) -> dict[str, Any]:
    """Assemble the public run document (see module docstring for the
    stability contract). ``result`` is None only on the crash path."""
    doc: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "run_id": started.strftime("%Y-%m-%d_%H%M%S"),
        "started": started.isoformat(),
        "finished": finished.isoformat(),
        "duration_s": max(0.0, round((finished - started).total_seconds(), 3)),
        "archward_version": __version__,
        "mode": str(mode),
        "no_aur": no_aur,
        "result": None,
        "aborted_reason": None,
        "update_exit_code": None,
        "update_error_lines": [],
        "snapshot_id": None,
        "pacnew_count": 0,
        "deselected_packages": [],
        "pending": [],
        "phases": phases,
        "verify": None,
        "aur": None,
        "hooks": {"pre": [], "post": []},
        "config_rewritten": False,
    }
    if error is not None:
        doc["aborted_reason"] = f"exception: {type(error).__name__}"
    if result is None:
        return doc

    if result.summary is not None:
        doc["result"] = {
            "tag": result.summary.tag,
            "secondary_tags": list(result.summary.secondary_tags),
            "fail_count": result.summary.fail_count,
            "warn_count": result.summary.warn_count,
            "reboot_needed": result.summary.reboot_needed,
        }
    doc["aborted_reason"] = result.aborted_reason or doc["aborted_reason"]
    doc["update_exit_code"] = result.update_exit_code
    doc["update_error_lines"] = list(result.update_error_lines)
    doc["snapshot_id"] = result.snapshot_id
    doc["pacnew_count"] = result.pacnew_count
    doc["deselected_packages"] = list(result.deselected_packages)
    doc["pending"] = [
        {
            "name": p.name,
            "old_version": p.old_version,
            "new_version": p.new_version,
            "source": p.source,
            "risk": str(p.risk),
            "is_kernel": p.is_kernel,
            "reason": p.reason,
        }
        for p in result.pending
    ]
    if result.verify is not None:
        checks = [
            {
                "bucket": c.bucket,
                "name": c.name,
                "status": str(c.status),
                "message": c.message,
                "detail": c.detail,
            }
            for c in result.verify.checks
        ]
        doc["verify"] = {
            "fail_count": result.verify.fail_count,
            "warn_count": result.verify.warn_count,
            "skipped_count": sum(1 for c in checks if c["status"] == "skipped"),
            "checks": checks,
        }
    if result.aur is not None:
        doc["aur"] = {
            "exit_code": result.aur.exit_code,
            "skipped": result.aur.skipped,
            "skip_reason": result.aur.skip_reason,
            "failures": [
                # last_lines re-capped: the final 20 lines carry the actual error
                {"package": f.package, "last_lines": list(f.last_lines[-20:])}
                for f in result.aur.failures
            ],
            "quarantine": [
                {
                    "package": q.package,
                    "version": q.version,
                    "status": q.status,
                    "failure_count": q.failure_count,
                    "retry_after": q.retry_after,  # UTC-aware ISO (upstream format)
                    "last_error": q.last_error,
                }
                for q in (result.aur.quarantine.active if result.aur.quarantine else ())
            ],
        }
    doc["hooks"] = {
        "pre": [_hook_dict(h) for h in result.pre_hook_results],
        "post": [_hook_dict(h) for h in result.post_hook_results],
    }
    doc["config_rewritten"] = result.config_rewritten
    return doc


def _hook_dict(h: Any) -> dict[str, Any]:
    # command strings can carry user secrets (webhook URLs); they stay in
    # the 0o700 state root and are needed to identify the hook — documented
    # caveat in docs/cli.md. output_lines deliberately stay out.
    return {"command": h.command, "status": str(h.status), "exit_code": h.exit_code}


def write_run_document(doc: dict[str, Any]) -> Path | None:
    """Atomically write the document under runs_dir(). On run_id collision
    (sub-second runs are routine in tests and fast aborts) append -2, -3, …
    Returns the written path, or None on failure (logged, never raises)."""
    try:
        rdir = runs_dir()
        rdir.mkdir(parents=True, exist_ok=True)
        base = doc["run_id"]
        run_id = base
        n = 1
        while (rdir / f"{run_id}.json").exists():
            n += 1
            run_id = f"{base}-{n}"
        doc = dict(doc, run_id=run_id)
        target = rdir / f"{run_id}.json"
        tmp = rdir / f".{run_id}.json.tmp"
        tmp.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, target)
        return target
    except Exception:  # noqa: BLE001
        log.exception("could not write run document")
        return None


def _run_sort_key(p: Path) -> tuple[str, int]:
    """Chronological sort key for run files. A bare lexicographic sort
    inverts same-second collision suffixes ('-' < '.', and -10 < -2), so
    split the stem into (base_id, suffix_number)."""
    m = re.match(r"^(\d{4}-\d{2}-\d{2}_\d{6})(?:-(\d+))?$", p.stem)
    if m is None:
        return (p.stem, 0)
    return (m.group(1), int(m.group(2) or 1))


def prune_runs(keep: int) -> list[Path]:
    """Delete run files beyond the newest `keep` (count cap only — run
    files are tiny; no age pass). Returns removed paths; never raises."""
    removed: list[Path] = []
    try:
        rdir = runs_dir()
        if keep <= 0 or not rdir.is_dir():
            return removed
        files = sorted(
            (p for p in rdir.glob("*.json") if not p.name.startswith(".")),
            key=_run_sort_key,
            reverse=True,
        )
        for p in files[keep:]:
            try:
                p.unlink()
                removed.append(p)
            except OSError:
                log.warning("could not prune run file %s", p)
    except Exception:  # noqa: BLE001
        log.exception("run-history prune failed")
    return removed


def list_run_files() -> list[Path]:
    """Run files newest-first by filename (run_id sorts chronologically)."""
    rdir = runs_dir()
    if not rdir.is_dir():
        return []
    return sorted(
        (p for p in rdir.glob("*.json") if not p.name.startswith(".")),
        key=_run_sort_key,
        reverse=True,
    )


def load_run(path: Path) -> dict[str, Any] | None:
    """Parse one run file; None if unreadable/corrupt (logged)."""
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        if not isinstance(doc, dict):
            return None
        return doc
    except (OSError, ValueError):
        log.warning("unreadable run file %s", path)
        return None


def summary_row(doc: dict[str, Any]) -> dict[str, Any]:
    """The `history list --json` row shape — summary only; full detail
    lives behind `history show <id> --json`."""
    result = doc.get("result") or {}
    return {
        "run_id": doc.get("run_id"),
        "started": doc.get("started"),
        "duration_s": doc.get("duration_s"),
        "mode": doc.get("mode"),
        "tag": result.get("tag"),
        "fail_count": result.get("fail_count"),
        "warn_count": result.get("warn_count"),
        "snapshot_id": doc.get("snapshot_id"),
    }
