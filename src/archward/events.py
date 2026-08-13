"""Typed pipeline event protocol (v0.5).

The bus carries `PhaseEvent`s from the (worker-thread) pipeline to
front-end subscribers. Three type layers replace the old string protocol:

- `Phase` — the closed set of phase names (rail rows, log prefixes).
- `PhaseStatus` — explicit per-result status declared by each emitter;
  the GUI rail consumes this directly instead of regex-classifying the
  result message prose (the v0.4.16 rail-substring bug class).
- `EventPayload` subclasses — frozen models carried by reference across
  the worker→Qt-main-thread hop (frozen pydantic models are safe to
  share; this replaces the old stringly-keyed dict payloads that were
  dumped to json and re-validated on the other side).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from enum import StrEnum
from threading import Lock

from pydantic import BaseModel, ConfigDict, Field

from archward.models.gate import GateResult
from archward.models.hook import HookResult
from archward.models.pacnew import PacnewFile
from archward.models.update import PendingUpdate
from archward.models.verify import VerifyResult


class Phase(StrEnum):
    PREFLIGHT = "preflight"
    SNAPSHOT = "snapshot"
    GATES = "gates"
    RISK = "risk"
    HOOKS_PRE = "hooks_pre"
    UPDATE_OFFICIAL = "update_official"
    UPDATE_AUR = "update_aur"
    PACNEW = "pacnew"
    VERIFY = "verify"
    HOOKS_POST = "hooks_post"
    PIPELINE = "pipeline"
    # Emitted by the Snapshot Browser's rollback actions, which route
    # through the active bus so their logs land in the main log pane.
    ROLLBACK = "rollback"


class PhaseStatus(StrEnum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    SKIPPED = "skipped"


class PhaseEventKind(StrEnum):
    PHASE_START = "phase.start"
    PHASE_PROGRESS = "phase.progress"
    PHASE_LOG = "phase.log"
    PHASE_RESULT = "phase.result"


class EventPayload(BaseModel):
    """Base for typed PHASE_RESULT / PHASE_PROGRESS payloads."""

    model_config = ConfigDict(frozen=True)


class GateResultsPayload(EventPayload):
    """preflight / gates — full gate outcome for the Gates view."""

    results: tuple[GateResult, ...]


class RiskPendingPayload(EventPayload):
    """risk — classified pending updates; `check_error` set when the
    checkupdates call itself failed (previously silently discarded)."""

    pending: tuple[PendingUpdate, ...]
    check_error: str | None = None


class TransactionPreviewPayload(EventPayload):
    """risk — pacman -Sup preview counts for the Risk view banner."""

    package_count: int
    replacement_count: int
    conflict_count: int


class PacnewFilesPayload(EventPayload):
    files: tuple[PacnewFile, ...]


class VerifyResultPayload(EventPayload):
    result: VerifyResult


class HookResultsPayload(EventPayload):
    """hooks_pre / hooks_post — routed by ev.phase, not payload type."""

    results: tuple[HookResult, ...]


class SnapshotProgressPayload(EventPayload):
    """snapshot — drives the SnapshotView step checklist (PHASE_PROGRESS)."""

    step: int
    total: int
    label: str


class SnapshotDonePayload(EventPayload):
    snapshot_id: str


class PhaseEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: PhaseEventKind
    phase: Phase
    message: str | None = None
    # Explicit emitter-declared status; set on PHASE_RESULT.
    status: PhaseStatus | None = None
    payload: EventPayload | None = None
    timestamp: datetime = Field(default_factory=datetime.now)


Subscriber = Callable[[PhaseEvent], None]


class EventBus:
    """Plain-Python pub/sub; Qt bridge attaches a subscriber that re-emits as a Qt signal."""

    def __init__(self) -> None:
        self._subscribers: list[Subscriber] = []
        self._lock = Lock()

    def subscribe(self, fn: Subscriber) -> None:
        with self._lock:
            self._subscribers.append(fn)

    def emit(self, event: PhaseEvent) -> None:
        # Copy subscriber list under lock, then call outside the lock to avoid
        # holding it across user callbacks.
        with self._lock:
            subs = list(self._subscribers)
        for fn in subs:
            fn(event)

    def emit_log(self, phase: Phase | str, message: str) -> None:
        self.emit(PhaseEvent(kind=PhaseEventKind.PHASE_LOG, phase=phase, message=message))

    def emit_start(self, phase: Phase | str, message: str | None = None) -> None:
        self.emit(PhaseEvent(kind=PhaseEventKind.PHASE_START, phase=phase, message=message))

    def emit_progress(
        self, phase: Phase | str, payload: EventPayload, message: str | None = None
    ) -> None:
        self.emit(
            PhaseEvent(
                kind=PhaseEventKind.PHASE_PROGRESS,
                phase=phase,
                message=message,
                payload=payload,
            )
        )

    def emit_result(
        self,
        phase: Phase | str,
        message: str,
        status: PhaseStatus,
        payload: EventPayload | None = None,
    ) -> None:
        self.emit(
            PhaseEvent(
                kind=PhaseEventKind.PHASE_RESULT,
                phase=phase,
                message=message,
                status=status,
                payload=payload,
            )
        )
