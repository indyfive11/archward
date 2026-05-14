"""GUI prompter — blocks the pipeline (worker) thread on main-thread interaction.

Two decision points:

1. **HIGH-risk approval (v0.3.0)** — uses inline interaction with the
   RiskView's checkboxes + Proceed/Cancel buttons instead of a separate
   modal. The worker thread calls `decide_high_risk(high)`; we activate
   the view's buttons via a queued signal, wait on a `threading.Event`,
   then return `(proceed, deselected_pkg_names)`. The user sees the same
   risk table they were already looking at, can uncheck specific packages,
   and clicks Proceed/Cancel inline.

2. **Gate override** — still a QMessageBox modal (it's a binary recoverable
   decision, no per-row state involved).

Implementation note: the inline approach lets the user interact with the
table (checkboxes) while the worker thread waits. The `threading.Event` is
set by Qt signal handlers running on the main thread, which unblocks the
worker.
"""

from __future__ import annotations

import logging
import threading

from PySide6.QtCore import QObject, Qt, Signal, Slot
from PySide6.QtWidgets import QMessageBox

from archward.models.gate import GateResult
from archward.models.update import PendingUpdate
from archward.ui.views.risk_view import RiskView

log = logging.getLogger(__name__)


class _AnswerHolder:
    """Mutable result container for the gate-override blocking call."""

    def __init__(self) -> None:
        self.answer: bool = False


class GuiPrompter(QObject):
    """Lives on the main thread; routes prompts through inline view interactions
    or QMessageBox modals as appropriate."""

    # Signal emitted from worker thread → cross-thread auto-becomes
    # QueuedConnection delivery to enable_decision on the main thread.
    _enable_risk_decision = Signal(str)

    # Gate override is still a modal — same blocking-queued pattern as before.
    _gate_override_requested = Signal(object, object)  # (gate, holder)

    def __init__(self, risk_view: RiskView, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._risk_view = risk_view
        # decide_high_risk synchronization: worker thread blocks on this event;
        # the RiskView's `decision_made` signal handler sets the answer + event.
        self._decision_event = threading.Event()
        self._decision_answer: tuple[bool, list[str]] = (False, [])

        # Cross-thread activation of the Risk view buttons. Worker emits;
        # Qt's auto-connection rules deliver via QueuedConnection because
        # the receiver lives on the main thread.
        self._enable_risk_decision.connect(self._risk_view.enable_decision)

        # RiskView's decision_made fires on the main thread when the user
        # clicks Proceed/Cancel. Auto-connection → DirectConnection since
        # GuiPrompter also lives on the main thread.
        self._risk_view.decision_made.connect(self._on_decision)

        # Gate override stays a modal.
        self._gate_override_requested.connect(
            self._on_gate_override_main_thread,
            Qt.ConnectionType.BlockingQueuedConnection,
        )

    # ── Pipeline-facing API (called on worker thread) ──────────────────────

    def decide_high_risk(
        self, high: list[PendingUpdate]
    ) -> tuple[bool, list[str]]:
        """Activate the RiskView's decision controls; block until user clicks.

        Returns (proceed, ignored_pkg_names). When called from the main thread
        (e.g. CLI smoke), falls back to a QMessageBox without deselect support.
        """
        if threading.current_thread() is threading.main_thread():
            proceed = self._show_high_risk_dialog_fallback(high)
            return proceed, []

        # Activate the RiskView's buttons via the queued signal. emit() on
        # the worker thread schedules enable_decision() to run on main.
        self._decision_event.clear()
        self._decision_answer = (False, [])
        prompt = (
            f"{len(high)} HIGH RISK package(s) require approval. "
            "Uncheck any you want to skip, then choose:"
        )
        self._enable_risk_decision.emit(prompt)

        # Block worker thread until the user clicks Proceed/Cancel.
        self._decision_event.wait()
        return self._decision_answer

    def confirm_gate_override(self, gate: GateResult) -> bool:
        if threading.current_thread() is threading.main_thread():
            return self._show_gate_dialog(gate)
        holder = _AnswerHolder()
        self._gate_override_requested.emit(gate, holder)
        return holder.answer

    def cancel_pending_decision(self) -> None:
        """Force any in-flight decide_high_risk to return (False, []).

        Called from MainWindow.closeEvent so the worker doesn't hang waiting
        on user input the user can no longer give.
        """
        self._decision_answer = (False, [])
        self._decision_event.set()

    # ── Main-thread slots ──────────────────────────────────────────────────

    @Slot(bool, list)
    def _on_decision(self, proceed: bool, ignored: list) -> None:
        """RiskView.decision_made handler. Runs on main thread."""
        self._decision_answer = (proceed, list(ignored))
        self._decision_event.set()

    @Slot(object, object)
    def _on_gate_override_main_thread(
        self, gate: GateResult, holder: _AnswerHolder
    ) -> None:
        holder.answer = self._show_gate_dialog(gate)

    # ── Fallback dialog (main-thread caller) ───────────────────────────────

    def _show_high_risk_dialog_fallback(self, high: list[PendingUpdate]) -> bool:
        lines = [f"  {p.name}  {p.old_version} → {p.new_version}" for p in high]
        body = (
            f"{len(high)} HIGH RISK package(s) would be updated.\n\n"
            + "\n".join(lines)
            + "\n\nProceed?"
        )
        box = QMessageBox()
        box.setWindowTitle("archward — HIGH RISK update")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText("Proceed with HIGH RISK update?")
        box.setInformativeText(body)
        box.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        box.setDefaultButton(QMessageBox.StandardButton.No)
        return box.exec() == QMessageBox.StandardButton.Yes

    def _show_gate_dialog(self, gate: GateResult) -> bool:
        box = QMessageBox()
        box.setWindowTitle(f"archward — gate {gate.name}")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(f"Gate '{gate.name}' failed.")
        box.setInformativeText(f"{gate.message}\n\nOverride and proceed?")
        box.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        box.setDefaultButton(QMessageBox.StandardButton.No)
        return box.exec() == QMessageBox.StandardButton.Yes
