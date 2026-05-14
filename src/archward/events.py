from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from enum import StrEnum
from threading import Lock
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class PhaseEventKind(StrEnum):
    PHASE_START = "phase.start"
    PHASE_PROGRESS = "phase.progress"
    PHASE_LOG = "phase.log"
    PHASE_RESULT = "phase.result"
    PIPELINE_DONE = "pipeline.done"


class PhaseEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: PhaseEventKind
    phase: str
    message: str | None = None
    payload: dict[str, Any] | None = None
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

    def emit_log(self, phase: str, message: str) -> None:
        self.emit(PhaseEvent(kind=PhaseEventKind.PHASE_LOG, phase=phase, message=message))

    def emit_start(self, phase: str, message: str | None = None) -> None:
        self.emit(PhaseEvent(kind=PhaseEventKind.PHASE_START, phase=phase, message=message))

    def emit_result(self, phase: str, message: str, payload: dict[str, Any] | None = None) -> None:
        self.emit(
            PhaseEvent(
                kind=PhaseEventKind.PHASE_RESULT, phase=phase, message=message, payload=payload
            )
        )
