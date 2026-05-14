"""archward main window.

Layout (Phase 5):
  ┌─────────────────────────────────────────────────────┐
  │ Toolbar:  [Run Dry-Run] [Run Update]  Distro: ...   │
  ├──────────────┬──────────────────────────────────────┤
  │ Phase rail   │ QStackedWidget — active phase view   │
  │              │                                      │
  │              │                                      │
  ├──────────────┼──────────────────────────────────────┤
  │              │ Log pane (collapsible bottom dock)   │
  ├──────────────┴──────────────────────────────────────┤
  │ Status bar                                          │
  └─────────────────────────────────────────────────────┘

Pipeline runs in a QThread. PhaseEvent crosses the boundary via QtEventBridge.
HIGH-risk approval and gate-override use GuiPrompter (BlockingQueuedConnection).
"""

from __future__ import annotations

import logging
import threading

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QLabel,
    QMainWindow,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from archward.app import build_config, build_sudo_strategy
from archward.events import EventBus, PhaseEvent, PhaseEventKind
from archward.logging_setup import setup_logging
from archward.models.config import ConfigModel
from archward.models.gate import GateResult
from archward.models.pacnew import PacnewFile
from archward.models.update import PendingUpdate
from archward.models.verify import VerifyResult
from archward.pipeline.pipeline import Mode, PipelineResult, run_pipeline
from archward.privilege.sudo import SudoStrategy
from archward.system import notify
from archward.system.distro import detect_distro
from archward.ui.dialogs.preferences import PreferencesDialog
from archward.ui.log_pane import LogPane
from archward.ui.phase_rail import PhaseRail
from archward.ui.prompter import GuiPrompter
from archward.ui.qt_bus import QtEventBridge
from archward.ui.views.gates_view import GatesView
from archward.ui.views.pacnew_view import PacnewView
from archward.ui.views.result_banner import ResultBanner
from archward.ui.views.risk_view import RiskView
from archward.ui.views.snapshot_view import SnapshotView
from archward.ui.views.update_view import UpdateView
from archward.ui.views.verify_view import VerifyView

log = logging.getLogger(__name__)


# Phase name → view widget key used for the stacked widget.
# No mapping for "result" — the result is shown in a persistent bottom banner
# rather than swapping the central view; the user keeps their last phase
# context (risk for dry-run, verify for real updates) visible after completion.
_PHASE_TO_VIEW = {
    "preflight": "gates",
    "snapshot": "snapshot",
    "gates": "gates",
    "risk": "risk",
    "update_official": "update",
    "update_aur": "update",
    "pacnew": "pacnew",
    "verify": "verify",
}


class PipelineWorker(QThread):
    finished_with_result = Signal(object)  # PipelineResult

    def __init__(
        self,
        cfg: ConfigModel,
        strategy: SudoStrategy,
        bus: EventBus,
        mode: Mode,
        prompter,
        *,
        no_aur: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.strategy = strategy
        self.bus = bus
        self.mode = mode
        self.prompter = prompter
        self.no_aur = no_aur
        self.cancel_event = threading.Event()
        self.result: PipelineResult | None = None

    def run(self) -> None:
        try:
            self.result = run_pipeline(
                self.cfg,
                self.strategy,
                self.bus,
                self.mode,
                no_aur=self.no_aur,
                cancel_event=self.cancel_event,
                prompter=self.prompter,
            )
        except Exception:  # noqa: BLE001
            log.exception("pipeline raised; emitting None result")
            self.result = None
        self.finished_with_result.emit(self.result)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Archward")
        self.resize(1200, 800)

        # ── State ──────────────────────────────────────────────────────────
        self.cfg = build_config()
        setup_logging(self.cfg.general.log_dir)
        self.bus: EventBus | None = None
        self.bridge: QtEventBridge | None = None
        self.strategy: SudoStrategy = build_sudo_strategy(self.cfg)
        self.prompter = GuiPrompter(parent=self)
        self.worker: PipelineWorker | None = None

        # ── Phase views ────────────────────────────────────────────────────
        self._views = {
            "snapshot": SnapshotView(),
            "gates": GatesView(),
            "risk": RiskView(),
            "update": UpdateView(),
            "pacnew": PacnewView(),
            "verify": VerifyView(),
        }
        self._stack = QStackedWidget()
        for v in self._views.values():
            self._stack.addWidget(v)
        # Idle default — snapshot page is fine; first phase event will switch.
        self._stack.setCurrentWidget(self._views["snapshot"])

        self._rail = PhaseRail()
        self._rail.setMinimumWidth(220)
        self._rail.setMaximumWidth(280)
        self._rail.phase_clicked.connect(self._on_rail_clicked)
        self._log = LogPane()
        self._result_banner = ResultBanner()

        # Vertical splitter: stacked-view (top) + log (bottom, collapsible).
        right_split = QSplitter(Qt.Orientation.Vertical)
        right_split.addWidget(self._stack)
        right_split.addWidget(self._log)
        right_split.setStretchFactor(0, 3)
        right_split.setStretchFactor(1, 1)
        right_split.setSizes([550, 180])

        # Horizontal splitter: rail + right-side stack.
        main_split = QSplitter(Qt.Orientation.Horizontal)
        main_split.addWidget(self._rail)
        main_split.addWidget(right_split)
        main_split.setStretchFactor(0, 0)
        main_split.setStretchFactor(1, 1)
        main_split.setSizes([240, 960])

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(main_split)
        layout.addWidget(self._result_banner)  # persistent strip at the bottom
        self.setCentralWidget(central)

        # ── Toolbar ────────────────────────────────────────────────────────
        toolbar = QToolBar()
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        self._dry_btn = QPushButton("Run Dry-Run")
        self._dry_btn.clicked.connect(lambda: self._start_run(Mode.DRY_RUN))
        toolbar.addWidget(self._dry_btn)

        self._update_btn = QPushButton("Run Update")
        self._update_btn.setToolTip(
            "Run pacman -Syu and the AUR phase. HIGH RISK packages will prompt for confirmation."
        )
        self._update_btn.clicked.connect(lambda: self._start_run(Mode.INTERACTIVE))
        toolbar.addWidget(self._update_btn)

        toolbar.addSeparator()
        self._prefs_btn = QPushButton("Preferences…")
        self._prefs_btn.clicked.connect(self._open_preferences)
        toolbar.addWidget(self._prefs_btn)

        toolbar.addSeparator()
        distro = detect_distro()
        toolbar.addWidget(QLabel(f"  Distro: {distro.pretty_name}  "))

        # ── Status bar ─────────────────────────────────────────────────────
        self._status = QStatusBar()
        self.setStatusBar(self._status)
        self._status.showMessage("Ready.")

        # Track snapshot step progress (parsed from log lines "[N/6] ...").
        self._snapshot_step = 0

    # ── Run control ────────────────────────────────────────────────────────

    def _start_run(self, mode: Mode) -> None:
        if self.worker is not None and self.worker.isRunning():
            return
        self._reset_views()
        self._dry_btn.setEnabled(False)
        self._update_btn.setEnabled(False)
        label = "dry-run" if mode is Mode.DRY_RUN else "update"
        self._status.showMessage(f"Running {label}…")

        # Fresh bus per run so old subscribers don't pile up.
        self.bus = EventBus()
        self.bridge = QtEventBridge(self.bus, parent=self)
        self.bridge.event.connect(self._on_phase_event)

        # PacnewView's per-row actions need access to sudo + bus.
        self._views["pacnew"].set_context(self.strategy, self.bus)

        self.worker = PipelineWorker(
            self.cfg,
            self.strategy,
            self.bus,
            mode,
            self.prompter,
            parent=self,
        )
        self.worker.finished_with_result.connect(self._on_pipeline_done)
        self.worker.start()

    def _reset_views(self) -> None:
        self._rail.reset()
        self._log.clear_log()
        self._snapshot_step = 0
        self._result_banner.reset()
        for v in self._views.values():
            if hasattr(v, "reset"):
                v.reset()

    # ── Event routing ──────────────────────────────────────────────────────

    def _on_rail_clicked(self, phase: str) -> None:
        """Rail-click navigation: switch the central view to the clicked phase."""
        view_key = _PHASE_TO_VIEW.get(phase)
        if view_key and view_key in self._views:
            self._stack.setCurrentWidget(self._views[view_key])

    def _on_phase_event(self, ev: PhaseEvent) -> None:
        view_key = _PHASE_TO_VIEW.get(ev.phase)
        if view_key:
            self._stack.setCurrentWidget(self._views[view_key])
            self._rail.select_phase(ev.phase)

        if ev.kind is PhaseEventKind.PHASE_START:
            self._rail.set_status(ev.phase, "running")
            self._log.append_line(f"[{ev.phase}] {ev.message or ''}")
            # Update phase-specific view header.
            if ev.phase == "update_official":
                self._views["update"].set_header("Running pacman -Syu")
            elif ev.phase == "update_aur":
                self._views["update"].set_header("Running AUR helper")

        elif ev.kind is PhaseEventKind.PHASE_LOG:
            msg = ev.message or ""
            self._log.append_line(msg)
            # Snapshot step progress — parse "[N/6] StepName".
            if ev.phase == "snapshot" and len(msg) > 5 and msg[0] == "[" and "]" in msg:
                try:
                    bracket = msg.index("]")
                    n_total = msg[1:bracket]
                    if "/" in n_total:
                        idx = int(n_total.split("/")[0])
                        self._snapshot_step = idx
                        self._views["snapshot"].note_step(idx)
                except ValueError:
                    pass
            # Update stream view for update_*/pacnew live output.
            if ev.phase in ("update_official", "update_aur"):
                self._views["update"].append(msg)

        elif ev.kind is PhaseEventKind.PHASE_RESULT:
            msg = (ev.message or "").lower()
            if "fail" in msg or "abort" in msg:
                rail_status = "fail"
            elif "skip" in msg:
                rail_status = "skipped"
            elif "warn" in msg:
                rail_status = "warn"
            else:
                rail_status = "pass"
            self._rail.set_status(ev.phase, rail_status)
            self._log.append_line(f"  → {ev.message or ''}")
            self._absorb_payload(ev)

    def _absorb_payload(self, ev: PhaseEvent) -> None:
        """Push rich payload data into the appropriate view (audit-shaped data, not log strings)."""
        if ev.payload is None:
            return
        if ev.phase in ("preflight", "gates") and "results" in ev.payload:
            results = [GateResult.model_validate(r) for r in ev.payload["results"]]
            # Both preflight and gates render into the same view; preflight clears
            # it then gates appends rather than overwrites? For Phase 5 simplicity,
            # the most recent set replaces — preflight's single check stays visible
            # until gates fires, then gates' two checks replace.
            existing = (
                self._views["gates"]._tree.topLevelItemCount()  # type: ignore[attr-defined]
                if ev.phase == "gates"
                else 0
            )
            if existing:
                # Append gates results below preflight (rebuild full list).
                # Simplest path: re-show only gates results for the gates phase.
                self._views["gates"].set_results(results)
            else:
                self._views["gates"].set_results(results)
        elif ev.phase == "snapshot":
            # Snapshot result fires after all steps — mark all complete.
            self._views["snapshot"].mark_complete()
        elif ev.phase == "risk" and "pending" in ev.payload:
            pending = [PendingUpdate.model_validate(p) for p in ev.payload["pending"]]
            self._views["risk"].set_pending(pending)
        elif ev.phase == "risk" and "package_count" in ev.payload:
            # Transaction-preview result event.
            self._views["risk"].set_preview_banner(
                ev.payload.get("replacement_count", 0),
                ev.payload.get("conflict_count", 0),
            )
        elif ev.phase == "pacnew" and "files" in ev.payload:
            files = [PacnewFile.model_validate(f) for f in ev.payload["files"]]
            self._views["pacnew"].set_files(files)
        elif ev.phase == "verify" and "result" in ev.payload:
            vr = VerifyResult.model_validate(ev.payload["result"])
            self._views["verify"].set_result(vr)

    # ── Completion ─────────────────────────────────────────────────────────

    def _on_pipeline_done(self, result: PipelineResult | None) -> None:
        self._dry_btn.setEnabled(True)
        self._update_btn.setEnabled(True)
        if result is None:
            self._status.showMessage("Pipeline failed unexpectedly — see log.")
            return
        if result.summary:
            tag = result.summary.tag
            self._rail.set_status(
                "result",
                "pass" if tag == "RESULT:SUCCESS" else
                "fail" if tag in ("RESULT:UPDATE_FAILED", "RESULT:VERIFY_FAILED") else
                "warn",
            )
            self._status.showMessage(f"Done. {tag}")
            self._log.append_line("")
            self._log.append_line(f"=== {tag} ===")
            for sec in result.summary.secondary_tags:
                self._log.append_line(f"  + {sec}")
            if result.aborted_reason:
                self._log.append_line(f"  reason: {result.aborted_reason}")
        # Auto-jump to the most actionable view if one stands out: verify on
        # FAIL, pacnew on PACNEW_MERGE_NEEDED. Otherwise stay on the last phase
        # view (risk for dry-run, verify for real runs).
        if result.summary:
            tag = result.summary.tag
            if tag == "RESULT:VERIFY_FAILED":
                self._stack.setCurrentWidget(self._views["verify"])
                self._rail.select_phase("verify")
            elif tag == "RESULT:PACNEW_MERGE_NEEDED" or result.pacnew_count > 0:
                self._stack.setCurrentWidget(self._views["pacnew"])
                self._rail.select_phase("pacnew")
        self._result_banner.show_result(result)
        self._rail.mark_unstarted_skipped()
        # Desktop notification on completion.
        notify.notify_completion(result, self.cfg)

    # ── Preferences ────────────────────────────────────────────────────────

    def _open_preferences(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            self._status.showMessage("Pipeline running — close it before editing preferences.")
            return
        dlg = PreferencesDialog(self.cfg, parent=self)
        dlg.config_saved.connect(self._on_config_saved)
        dlg.exec()

    def _on_config_saved(self, new_cfg: ConfigModel) -> None:
        """Reload state from the freshly-saved config."""
        self.cfg = new_cfg
        # Privilege/askpass may have changed → rebuild the sudo strategy.
        self.strategy = build_sudo_strategy(self.cfg)
        # Ensure new snapshot/log dirs exist.
        self.cfg.general.snapshot_dir.mkdir(parents=True, exist_ok=True)
        self.cfg.general.log_dir.mkdir(parents=True, exist_ok=True)
        self._status.showMessage("Preferences saved.")

    # ── Window lifecycle ───────────────────────────────────────────────────

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.worker is not None and self.worker.isRunning():
            self.worker.cancel_event.set()
            self.worker.wait(3000)
        super().closeEvent(event)
