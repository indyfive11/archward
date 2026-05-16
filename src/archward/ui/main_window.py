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
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QLabel,
    QMainWindow,
    QPushButton,
    QSizePolicy,
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
from archward.models.hook import HookResult
from archward.models.pacnew import PacnewFile
from archward.models.update import PendingUpdate
from archward.models.verify import VerifyResult
from archward.pipeline.pipeline import Mode, PipelineResult, run_pipeline
from archward.privilege.sudo import SudoStrategy
from archward.system import notify
from archward.system.distro import detect_distro
from archward.ui.dialogs.preferences import PreferencesDialog
from archward.ui.dialogs.snapshot_browser import SnapshotBrowser
from archward.ui.log_pane import LogPane
from archward.ui.phase_rail import PhaseRail
from archward.ui.prompter import GuiPrompter, PkgbuildPrompter, UpdatePrompter
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
# hooks_pre/hooks_post route to the Verify view, which has a dedicated
# "hooks" bucket for their output (v0.3.1).
_PHASE_TO_VIEW = {
    "preflight": "gates",
    "snapshot": "snapshot",
    "gates": "gates",
    "risk": "risk",
    "hooks_pre": "verify",
    "update_official": "update",
    "update_aur": "update",
    "pacnew": "pacnew",
    "verify": "verify",
    "hooks_post": "verify",
}


class WarmupWorker(QThread):
    """Runs strategy.warmup() off the Qt main thread (v0.4.5 F4b).

    The askpass dialog (ksshaskpass) blocks until the user responds. Running
    it on the main thread freezes the event loop, preventing status-bar repaints
    and any other UI activity. Moving warmup to a daemon QThread keeps the UI
    alive while the dialog is open.
    """

    warmup_done = Signal(bool)  # True = success, False = failure

    def __init__(self, strategy: SudoStrategy, parent=None) -> None:
        super().__init__(parent)
        self.strategy = strategy

    def run(self) -> None:
        try:
            ok = self.strategy.warmup()
        except Exception:  # noqa: BLE001 — warmup must never crash the run
            log.exception("sudo warmup raised")
            ok = False
        self.warmup_done.emit(ok)


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
        config_path: Path | None = None,
        prompt_provider=None,
        pkgbuild_reviewer=None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.strategy = strategy
        self.bus = bus
        self.mode = mode
        self.prompter = prompter
        self.no_aur = no_aur
        self.config_path = config_path
        self.prompt_provider = prompt_provider
        self.pkgbuild_reviewer = pkgbuild_reviewer
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
                config_path=self.config_path,
                prompt_provider=self.prompt_provider,
                pkgbuild_reviewer=self.pkgbuild_reviewer,
            )
        except Exception:  # noqa: BLE001
            log.exception("pipeline raised; emitting None result")
            self.result = None
        self.finished_with_result.emit(self.result)


class MainWindow(QMainWindow):
    def __init__(self, config_path: Path | None = None) -> None:
        super().__init__()
        # Window title reflects the active profile so the user can tell at a
        # glance which config a launched window edits.
        self.config_path = config_path
        if config_path is not None:
            self.setWindowTitle(f"Archward — profile: {config_path.stem}")
        else:
            self.setWindowTitle("Archward")
        self.resize(1200, 800)

        # Persist the active profile path so the next launch (without
        # --profile) can reopen it if the remember-last-used toggle is on.
        # No-op when the toggle is off; safe to call unconditionally.
        from archward.ui.persistent_state import set_last_used_profile_path, get_remember_last_profile
        if get_remember_last_profile():
            set_last_used_profile_path(config_path)

        # ── State ──────────────────────────────────────────────────────────
        self.cfg = build_config(config_path)
        setup_logging(self.cfg.general.log_dir)
        self.bus: EventBus | None = None
        self.bridge: QtEventBridge | None = None
        self.strategy: SudoStrategy = build_sudo_strategy(self.cfg)
        # Prompter is built after the views — it needs a RiskView reference
        # for the v0.3.0 inline-decision flow.
        self.prompter: GuiPrompter | None = None
        # v0.4.0 update prompter: routes pacman/AUR interactive prompts to the
        # UpdateView's inline input row. Built alongside the regular prompter.
        self.update_prompter: UpdatePrompter | None = None
        # v0.4.0 PKGBUILD review prompter: surfaces the PkgbuildReviewDialog
        # per AUR package when noconfirm=False.
        self.pkgbuild_prompter: PkgbuildPrompter | None = None
        self.worker: PipelineWorker | None = None
        self._warmup_worker: WarmupWorker | None = None
        self._pending_mode: Mode | None = None

        # ── Phase views ────────────────────────────────────────────────────
        self._views = {
            "snapshot": SnapshotView(),
            "gates": GatesView(),
            "risk": RiskView(),
            "update": UpdateView(),
            "pacnew": PacnewView(),
            "verify": VerifyView(),
        }
        self.prompter = GuiPrompter(risk_view=self._views["risk"], parent=self)
        self.update_prompter = UpdatePrompter(
            update_view=self._views["update"], parent=self
        )
        self.pkgbuild_prompter = PkgbuildPrompter(main_window=self)
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

        # Brand cue: shield icon + "Archward <version>" — added as two
        # direct toolbar widgets, NOT wrapped in a QWidget+QHBoxLayout.
        # An earlier attempt used a custom container which collapsed to
        # zero width when the toolbar re-laid-out under heavy paint
        # pressure (visible as the brand chip vanishing during pacman
        # -Syu and never coming back). Two plain QLabels added directly
        # let the toolbar's own layout manage their geometry.
        from archward import __version__
        from archward.ui.icon import archward_icon
        from archward.ui.theme import brand_palette
        _brand_accent = brand_palette().accent_text_css
        _icon_lbl = QLabel()
        _icon_lbl.setPixmap(archward_icon().pixmap(22, 22))
        _icon_lbl.setContentsMargins(8, 0, 4, 0)
        toolbar.addWidget(_icon_lbl)
        _name_lbl = QLabel(f"<b>Archward</b> {__version__}")
        _name_lbl.setStyleSheet(f"color: {_brand_accent}; padding-right: 8px;")
        toolbar.addWidget(_name_lbl)
        toolbar.addSeparator()

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
        self._snap_btn = QPushButton("Snapshot Browser…")
        self._snap_btn.setToolTip(
            "Browse past snapshots; restore individual configs or downgrade "
            "specific packages from /var/cache/pacman/pkg/."
        )
        self._snap_btn.clicked.connect(self._open_snapshot_browser)
        toolbar.addWidget(self._snap_btn)

        self._prefs_btn = QPushButton("Preferences…")
        self._prefs_btn.clicked.connect(self._open_preferences)
        toolbar.addWidget(self._prefs_btn)

        toolbar.addSeparator()
        distro = detect_distro()
        toolbar.addWidget(QLabel(f"  Distro: {distro.pretty_name}  "))

        # Spacer pushes the About button to the far right of the toolbar.
        _spacer = QWidget()
        _spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(_spacer)
        self._about_btn = QPushButton("About")
        self._about_btn.setToolTip("Version, license, project links.")
        self._about_btn.clicked.connect(self._open_about)
        toolbar.addWidget(self._about_btn)

        # ── Status bar ─────────────────────────────────────────────────────
        self._status = QStatusBar()
        self.setStatusBar(self._status)
        if config_path is not None:
            self._status.showMessage(f"Ready. (profile: {config_path.stem} — {config_path})")
        else:
            self._status.showMessage("Ready.")

        # Track snapshot step progress (parsed from log lines "[N/6] ...").
        self._snapshot_step = 0

    # ── Run control ────────────────────────────────────────────────────────

    def _start_run(self, mode: Mode) -> None:
        if self.worker is not None and self.worker.isRunning():
            return
        if self._warmup_worker is not None and self._warmup_worker.isRunning():
            return
        self._reset_views()
        self._dry_btn.setEnabled(False)
        self._update_btn.setEnabled(False)
        self._pending_mode = mode

        # v0.4.5 F4b: run warmup on a background QThread so the askpass dialog
        # (ksshaskpass) doesn't freeze the event loop. The status bar stays
        # responsive and repaints correctly while waiting for the user's
        # password. _on_warmup_done starts the pipeline when warmup finishes.
        self._status.showMessage("Authenticating…")
        self._warmup_worker = WarmupWorker(self.strategy, parent=self)
        self._warmup_worker.warmup_done.connect(self._on_warmup_done)
        self._warmup_worker.start()

    def _on_warmup_done(self, ok: bool) -> None:
        if ok:
            log.info("sudo warmup succeeded — timestamp warm")
        else:
            log.warning("sudo warmup failed; the pipeline will re-prompt on first sudo call")
            self._status.showMessage(
                "sudo warmup failed — askpass may prompt again during the run."
            )
        # Warmup failure is non-fatal: the pipeline re-prompts on the first
        # sudo call inside snapshot. Proceed regardless.
        if self._pending_mode is not None:
            self._launch_pipeline(self._pending_mode)

    def _launch_pipeline(self, mode: Mode) -> None:
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
            config_path=self.config_path,
            prompt_provider=self.update_prompter.prompt if self.update_prompter else None,
            pkgbuild_reviewer=self.pkgbuild_prompter,
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

    def _warmup_sudo_for_run(self) -> bool:
        """Prime the sudo timestamp via askpass before the pipeline starts.

        Runs `sudo -A -v` synchronously on the main thread. The askpass
        binary (ksshaskpass on KDE) shows its own dialog while we block;
        this is the same UX as the CLI path's setup_app warmup. Returns
        True on success, False on failure — failure is non-fatal: the
        pipeline will simply re-prompt at the first sudo call inside
        snapshot, matching pre-v0.4.2 behavior.

        Calling this here (rather than in __init__) means archward
        doesn't pop a password prompt unless the user actually clicks
        Run / Dry-Run.
        """
        self._status.showMessage("Authenticating with sudo…")
        # Give Qt a tick to repaint the status bar before the blocking call.
        from PySide6.QtCore import QCoreApplication
        QCoreApplication.processEvents()
        try:
            ok = self.strategy.warmup()
        except Exception:  # noqa: BLE001 — warmup must never crash the run
            log.exception("sudo warmup raised")
            ok = False
        if ok:
            log.info("sudo warmup succeeded — timestamp warm")
        else:
            log.warning("sudo warmup failed; the pipeline will re-prompt on first sudo call")
            self._status.showMessage(
                "sudo warmup failed — askpass may prompt again during the run."
            )
        return ok

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
        elif ev.phase == "hooks_pre" and "hook_results" in ev.payload:
            hooks = tuple(HookResult.model_validate(h) for h in ev.payload["hook_results"])
            self._views["verify"].set_pre_hooks(hooks)
        elif ev.phase == "hooks_post" and "hook_results" in ev.payload:
            hooks = tuple(HookResult.model_validate(h) for h in ev.payload["hook_results"])
            self._views["verify"].set_post_hooks(hooks)

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

    def _open_snapshot_browser(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            self._status.showMessage("Pipeline running — close it before browsing snapshots.")
            return
        # Reuse the active bus if a run produced one (so rollback log lines route
        # through the same log pane); otherwise the browser falls back to the
        # logging module.
        dlg = SnapshotBrowser(self.cfg, self.strategy, self.bus, parent=self)
        dlg.exec()

    def _open_about(self) -> None:
        from archward.ui.dialogs.about import AboutDialog
        AboutDialog(parent=self).exec()

    def _open_preferences(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            self._status.showMessage("Pipeline running — close it before editing preferences.")
            return
        dlg = PreferencesDialog(self.cfg, config_path=self.config_path, parent=self)
        dlg.config_saved.connect(self._on_config_saved)
        # Profile-switch handler needs the dialog reference so it can call
        # apply_profile_switch() back after rebuilding cfg.
        dlg.profile_switch_requested.connect(
            lambda new_path: self._on_profile_switch_requested(new_path, dialog=dlg)
        )
        dlg.exec()

    def _on_config_saved(self, new_cfg: ConfigModel) -> None:
        """Reload state from the freshly-saved config."""
        self.cfg = new_cfg
        # Privilege/askpass may have changed → rebuild the sudo strategy.
        self.strategy = build_sudo_strategy(self.cfg)
        # Ensure new snapshot/log dirs exist before re-routing logs to them.
        self.cfg.general.snapshot_dir.mkdir(parents=True, exist_ok=True)
        self.cfg.general.log_dir.mkdir(parents=True, exist_ok=True)
        # Re-route logging if log_dir changed (otherwise the old
        # RotatingFileHandler would keep writing to the previous path).
        # setup_logging() is idempotent — it clears handlers before installing.
        setup_logging(self.cfg.general.log_dir)
        self._status.showMessage("Preferences saved.")

    def _on_profile_switch_requested(self, new_path, *, dialog) -> None:
        """Switch the running window to a different profile (or default).

        new_path: Path | None. None == default ~/.config/archward/config.toml.
        Refused while a pipeline is running (Profile tab disables the button
        in that case too; this is defense in depth).
        """
        if self.worker is not None and self.worker.isRunning():
            self._status.showMessage("Pipeline running — cannot switch profile.")
            return
        self.config_path = new_path
        self.cfg = build_config(new_path)
        self.strategy = build_sudo_strategy(self.cfg)
        setup_logging(self.cfg.general.log_dir)
        if new_path is not None:
            self.setWindowTitle(f"Archward — profile: {new_path.stem}")
            self._status.showMessage(
                f"Switched to profile: {new_path.stem} — {new_path}"
            )
        else:
            self.setWindowTitle("Archward")
            self._status.showMessage("Switched to default config.")
        log.info("profile switched to %s", new_path if new_path else "(default)")
        # Persist as last-used if the QSettings toggle is on.
        from archward.ui.persistent_state import set_last_used_profile_path, get_remember_last_profile
        if get_remember_last_profile():
            set_last_used_profile_path(new_path)
        # Refresh the still-open Preferences dialog so its widgets reflect
        # the newly-active profile without the user having to close + reopen.
        dialog.apply_profile_switch(self.cfg, new_path)

    # ── Window lifecycle ───────────────────────────────────────────────────

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.worker is not None and self.worker.isRunning():
            self.worker.cancel_event.set()
            # If the worker is blocked waiting on a HIGH-risk decision the
            # user can no longer make, force-cancel that wait so the thread
            # can exit cleanly.
            if self.prompter is not None:
                self.prompter.cancel_pending_decision()
            # Same defensive cancel for an in-flight pacman/AUR prompt.
            if self.update_prompter is not None:
                self.update_prompter.cancel_pending()
            self.worker.wait(3000)
        super().closeEvent(event)
