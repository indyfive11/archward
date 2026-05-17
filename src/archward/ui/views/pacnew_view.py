"""Pacnew phase content view — table of detected .pacnew files with per-row actions.

Each row exposes four actions:
  - View Diff  → DiffDialog (unified diff, syntax-highlighted)
  - Keep Ours  → sudo rm <path>.pacnew
  - Take New   → sudo cp -a orig orig.pre-archward.bak + sudo mv .pacnew → orig
                 + sudo chown + sudo chmod (preserves original ownership/mode)
  - Edit       → spawn meld / kdiff3 with sudo -A; sudoedit fallback; final
                 fallback shows the paths in a message box for manual handling.

Per-row state is reflected by disabling buttons after a terminal action and
labeling the row as resolved.
"""

from __future__ import annotations

import logging
import shutil
import subprocess

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from archward.events import EventBus
from archward.models.pacnew import PacnewAction, PacnewFile, PacnewRecommendation
from archward.pacman.pacnew import apply_action
from archward.privilege.sudo import SudoStrategy
from archward.ui.dialogs.diff_dialog import DiffDialog
from archward.ui.persistent_state import load_column_widths, save_column_widths
from archward.ui.theme import status_palette

log = logging.getLogger(__name__)


def _rec_colors() -> dict[PacnewRecommendation, QColor]:
    p = status_palette()
    return {
        PacnewRecommendation.KEEP_OURS: p.keep_ours_fg,
        PacnewRecommendation.TAKE_NEW: p.take_new_fg,
        PacnewRecommendation.REVIEW_NEEDED: p.review_needed_fg,
    }

# Graphical merge tools, preferred order. Each takes "<orig> <new>" arguments.
_MERGE_TOOLS = ("meld", "kdiff3", "kompare")


class PacnewView(QWidget):
    """Read-write pacnew resolution table.

    The view is unusable until `set_context(strategy, bus)` is called — that
    provides the privilege strategy needed to mutate /etc files and the event
    bus to log the actions.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._header = QLabel("Pacnew files")
        self._header.setStyleSheet("font-weight: bold; padding: 8px;")
        self._tree = QTreeWidget()
        self._tree.setColumnCount(5)
        self._tree.setHeaderLabels(["File", "Recommendation", "Note", "Status", "Actions"])
        self._tree.setRootIsDecorated(False)
        self._tree.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        hdr = self._tree.header()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionsMovable(False)
        hdr.setMinimumSectionSize(40)
        hdr.sectionResized.connect(self._save_column_widths)
        self._restore_column_widths()

        layout = QVBoxLayout(self)
        layout.addWidget(self._header)
        layout.addWidget(self._tree, stretch=1)

        self._strategy: SudoStrategy | None = None
        self._bus: EventBus | None = None
        # File-row registry so we can mark rows resolved without rebuilding.
        self._rows: list[tuple[PacnewFile, QTreeWidgetItem]] = []

    # ── Wiring ────────────────────────────────────────────────────────────

    def set_context(self, strategy: SudoStrategy, bus: EventBus) -> None:
        """Provide the privilege strategy + event bus used by row actions."""
        self._strategy = strategy
        self._bus = bus

    def set_files(self, files: list[PacnewFile]) -> None:
        self._tree.clear()
        self._rows.clear()
        if not files:
            self._header.setText("Pacnew — no new .pacnew files")
            return
        self._header.setText(f"Pacnew — {len(files)} file(s) need attention")
        for f in files:
            self._add_row(f)

    def _save_column_widths(self) -> None:
        hdr = self._tree.header()
        save_column_widths("ui/pacnew_columns", [hdr.sectionSize(1), hdr.sectionSize(2)])

    def _restore_column_widths(self) -> None:
        w1, w2 = load_column_widths("ui/pacnew_columns", [140, 120])
        self._tree.header().resizeSection(1, w1)
        self._tree.header().resizeSection(2, w2)

    def reset(self) -> None:
        self._tree.clear()
        self._rows.clear()
        self._header.setText("Pacnew files")

    # ── Row construction ──────────────────────────────────────────────────

    def _add_row(self, pacnew: PacnewFile) -> None:
        item = QTreeWidgetItem(
            [
                str(pacnew.path),
                pacnew.recommendation.value,
                pacnew.note or "",
                "",  # status
                "",  # actions placeholder
            ]
        )
        color = _rec_colors().get(pacnew.recommendation)
        if color is not None:
            item.setForeground(1, color)
        self._tree.addTopLevelItem(item)

        # Build the action button strip for column 4.
        actions = QWidget()
        row_layout = QHBoxLayout(actions)
        row_layout.setContentsMargins(2, 2, 2, 2)
        row_layout.setSpacing(4)

        diff_btn = _small_btn("View Diff")
        keep_btn = _small_btn("Keep Ours")
        take_btn = _small_btn("Take New")
        edit_btn = _small_btn("Edit")
        leave_btn = _small_btn("Leave")

        diff_btn.clicked.connect(lambda *, f=pacnew: self._on_view_diff(f))
        keep_btn.clicked.connect(lambda *, f=pacnew, it=item: self._on_action(f, PacnewAction.KEEP_OURS, it))
        take_btn.clicked.connect(lambda *, f=pacnew, it=item: self._on_action(f, PacnewAction.TAKE_NEW, it))
        edit_btn.clicked.connect(lambda *, f=pacnew, it=item: self._on_edit(f, it))
        leave_btn.clicked.connect(lambda *, f=pacnew, it=item: self._on_action(f, PacnewAction.LEAVE, it))

        for b in (diff_btn, keep_btn, take_btn, edit_btn, leave_btn):
            row_layout.addWidget(b)
        row_layout.addStretch(1)
        self._tree.setItemWidget(item, 4, actions)

        self._rows.append((pacnew, item))

    # ── Action handlers ───────────────────────────────────────────────────

    def _on_view_diff(self, pacnew: PacnewFile) -> None:
        if self._strategy is None:
            self._missing_context()
            return
        dlg = DiffDialog(pacnew.original_path, pacnew.path, self._strategy, parent=self)
        dlg.exec()

    def _on_action(self, pacnew: PacnewFile, action: PacnewAction, item: QTreeWidgetItem) -> None:
        if self._strategy is None:
            self._missing_context()
            return
        self._log(f"applying {action.value} → {pacnew.path}")
        try:
            apply_action(pacnew, action, self._strategy)
        except RuntimeError as e:
            QMessageBox.critical(self, "Action failed", f"{action.value} on {pacnew.path}:\n\n{e}")
            self._mark_status(item, "FAILED", status_palette().fail_fg)
            return
        self._mark_resolved(item, action)

    def _on_edit(self, pacnew: PacnewFile, item: QTreeWidgetItem) -> None:
        """Spawn a graphical merge tool. After it exits the user is responsible for
        deciding whether the .pacnew is still present; archward doesn't auto-mark
        as resolved because we can't know what the user did."""
        if self._strategy is None:
            self._missing_context()
            return
        tool = _find_merge_tool()
        if tool is None:
            QMessageBox.information(
                self,
                "No merge tool",
                "Install meld / kdiff3 / kompare for graphical merge, or run\n\n"
                f"  sudoedit {pacnew.original_path}\n\n"
                "(and inspect/discard the .pacnew at):\n\n"
                f"  {pacnew.path}",
            )
            return

        argv = [
            *self._strategy.argv_prefix(),
            tool,
            str(pacnew.original_path),
            str(pacnew.path),
        ]
        env = self._strategy.env()
        try:
            subprocess.Popen(argv, env=env)
        except OSError as e:
            QMessageBox.critical(self, "Edit failed", f"Could not launch {tool}: {e}")
            return
        self._log(f"launched {tool} on {pacnew.original_path} vs {pacnew.path}")
        self._mark_status(item, "editing (external)", status_palette().take_new_fg)

    # ── Helpers ───────────────────────────────────────────────────────────

    def _mark_resolved(self, item: QTreeWidgetItem, action: PacnewAction) -> None:
        labels = {
            PacnewAction.KEEP_OURS: "kept ours",
            PacnewAction.TAKE_NEW: "took new",
            PacnewAction.LEAVE: "left in place",
            PacnewAction.EDIT: "edited",
        }
        self._mark_status(item, labels.get(action, action.value), status_palette().pass_fg)
        # Disable the row's buttons so the user doesn't double-apply.
        actions_widget = self._tree.itemWidget(item, 4)
        if actions_widget is not None:
            for b in actions_widget.findChildren(QPushButton):
                b.setEnabled(False)

    def _mark_status(self, item: QTreeWidgetItem, text: str, color: QColor | None = None) -> None:
        item.setText(3, text)
        if color is not None:
            item.setForeground(3, color)

    def _log(self, message: str) -> None:
        if self._bus is not None:
            self._bus.emit_log("pacnew", message)
        else:
            log.info(message)

    def _missing_context(self) -> None:
        QMessageBox.warning(
            self,
            "Internal error",
            "PacnewView has no sudo strategy / event bus wired. "
            "This is a bug — please open an issue.",
        )


def _small_btn(label: str) -> QPushButton:
    btn = QPushButton(label)
    btn.setStyleSheet("padding: 2px 8px;")
    return btn


def _find_merge_tool() -> str | None:
    for tool in _MERGE_TOOLS:
        if shutil.which(tool):
            return tool
    return None
