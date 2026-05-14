"""Bridge between the pure-Python EventBus and Qt signals.

Pipeline runs in a QThread and emits PhaseEvent into the EventBus. EventBus is
synchronous: subscribers are called on the emitting thread. The bridge below
subscribes to the bus and re-emits a Qt signal — because the QObject lives on
the main thread, Qt's auto-connection rules deliver the signal via
QueuedConnection. This is the *only* thing that crosses the worker→main
boundary; UI code never imports pipeline internals.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from archward.events import EventBus, PhaseEvent


class QtEventBridge(QObject):
    """Re-emit EventBus events as Qt signals (cross-thread safe via QueuedConnection)."""

    # `object` so PhaseEvent (a Pydantic BaseModel) passes through Qt's meta-type
    # system without a custom registration. Pydantic models are frozen for the
    # cross-thread hop — see audit A1.
    event = Signal(object)

    def __init__(self, bus: EventBus, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._bus = bus
        self._bus.subscribe(self._on_bus_event)

    def _on_bus_event(self, ev: PhaseEvent) -> None:
        # Called on the emitting thread (typically the pipeline QThread).
        # The Qt signal hop queues delivery onto the main thread because this
        # QObject was created there.
        self.event.emit(ev)
