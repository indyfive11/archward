"""GUI prompter — blocks the pipeline (worker) thread on a main-thread modal.

The pipeline calls `prompter.confirm_high_risk(...)` on the worker thread. We
emit a Qt signal to a slot on the main thread, blocking the worker until the
slot returns the user's answer.

Implementation note: a BlockingQueuedConnection between worker thread and main
thread is the supported Qt idiom here. The worker thread MUST NOT be the same
as the receiver's thread (Qt would deadlock); since the GuiPrompter lives on
the main thread and is called from the QThread worker, this constraint is met
by construction.
"""

from __future__ import annotations

import logging
import threading

from PySide6.QtCore import QObject, Qt, Signal, Slot
from PySide6.QtWidgets import QMessageBox

from archward.models.gate import GateResult
from archward.models.update import PendingUpdate

log = logging.getLogger(__name__)


class _AnswerHolder:
    """Mutable result container for the blocking call. Plain object so it
    doesn't need Qt meta-type registration."""

    def __init__(self) -> None:
        self.answer: bool = False


class GuiPrompter(QObject):
    """Lives on the main thread; routes prompts through QMessageBox.

    The blocking signal hop is the supported Qt idiom for "ask a question on the
    main thread from a worker and wait for the answer." The worker thread is
    suspended at the signal.emit() call until the slot returns.
    """

    # Signals fired from worker thread → handled on main thread synchronously.
    # `object` is the AnswerHolder; receiving side mutates it in place.
    _high_risk_requested = Signal(object, object)  # (high_packages_list, holder)
    _gate_override_requested = Signal(object, object)  # (gate, holder)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        # Blocking-queued so the worker is suspended until the main thread runs
        # the slot. Connecting in __init__ ensures the QObject's thread is the
        # main thread at the time of connection.
        self._high_risk_requested.connect(
            self._on_high_risk_main_thread, Qt.ConnectionType.BlockingQueuedConnection
        )
        self._gate_override_requested.connect(
            self._on_gate_override_main_thread, Qt.ConnectionType.BlockingQueuedConnection
        )

    # ── Pipeline-facing API (called on worker thread) ──────────────────────

    def confirm_high_risk(self, high: list[PendingUpdate]) -> bool:
        if threading.current_thread() is threading.main_thread():
            # Safety: caller is on main thread (e.g. CLI smoke). Skip the queued
            # hop; show the dialog directly.
            return self._show_high_risk_dialog(high)
        holder = _AnswerHolder()
        self._high_risk_requested.emit(list(high), holder)
        return holder.answer

    def confirm_gate_override(self, gate: GateResult) -> bool:
        if threading.current_thread() is threading.main_thread():
            return self._show_gate_dialog(gate)
        holder = _AnswerHolder()
        self._gate_override_requested.emit(gate, holder)
        return holder.answer

    # ── Main-thread slots ──────────────────────────────────────────────────

    @Slot(object, object)
    def _on_high_risk_main_thread(self, high: list, holder: _AnswerHolder) -> None:
        holder.answer = self._show_high_risk_dialog(high)

    @Slot(object, object)
    def _on_gate_override_main_thread(self, gate: GateResult, holder: _AnswerHolder) -> None:
        holder.answer = self._show_gate_dialog(gate)

    # ── Dialog construction ────────────────────────────────────────────────

    def _show_high_risk_dialog(self, high: list[PendingUpdate]) -> bool:
        lines = [f"  {p.name}  {p.old_version} → {p.new_version}" for p in high]
        body = (
            f"{len(high)} HIGH RISK package(s) would be updated.\n\n"
            + "\n".join(lines)
            + "\n\nThese may need a reboot, a .pacnew merge, or a session restart "
            "to take effect.\n\nProceed?"
        )
        box = QMessageBox()
        box.setWindowTitle("archward — HIGH RISK update")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText("Proceed with HIGH RISK update?")
        box.setInformativeText(body)
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        return box.exec() == QMessageBox.StandardButton.Yes

    def _show_gate_dialog(self, gate: GateResult) -> bool:
        box = QMessageBox()
        box.setWindowTitle(f"archward — gate {gate.name}")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(f"Gate '{gate.name}' failed.")
        box.setInformativeText(f"{gate.message}\n\nOverride and proceed?")
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        return box.exec() == QMessageBox.StandardButton.Yes
