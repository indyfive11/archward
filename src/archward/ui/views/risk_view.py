"""Risk phase content view — three stacked HIGH/MEDIUM/LOW trees.

HIGH packages are highlighted in red. Transaction-preview replacements show
in a separate banner above the tree.

v0.3.0 adds per-row deselection via checkboxes plus inline Proceed/Cancel
buttons. The buttons stay disabled until the prompter calls
`enable_decision()`; clicking either emits `decision_made(proceed, ignored)`
where `ignored` is the list of package names whose checkbox was unchecked.
GuiPrompter listens for this signal to unblock the worker thread.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from archward.models.update import PendingUpdate, RiskLevel
from archward.ui.theme import status_palette


class RiskView(QWidget):
    """Risk classification display + (v0.3.0) inline deselect + decision controls."""

    # Emitted when user clicks Proceed/Cancel during the decision window.
    # (proceed: bool, ignored_pkg_names: list[str])
    decision_made = Signal(bool, list)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._summary = QLabel("Risk classification")
        self._summary.setStyleSheet("font-weight: bold; padding: 8px;")
        self._counts = QLabel("")
        self._counts.setStyleSheet("padding: 0 8px 8px 8px;")
        p = status_palette()
        self._preview_banner = QLabel("")
        self._preview_banner.setStyleSheet(
            f"padding: 4px 8px; "
            f"background: {p.preview_warning_bg}; "
            f"border: 1px solid {p.preview_warning_border}; "
            f"color: {p.preview_warning_fg};"
        )
        self._preview_banner.setVisible(False)
        self._preview_banner.setWordWrap(True)

        self._tree = QTreeWidget()
        self._tree.setColumnCount(3)
        self._tree.setHeaderLabels(["Package", "Old → New", "Reason"])
        self._tree.setRootIsDecorated(True)
        self._tree.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)

        # Decision buttons — disabled by default; the prompter enables them
        # via enable_decision() when waiting for user input.
        self._decision_label = QLabel("")
        self._decision_label.setStyleSheet("padding: 4px 8px;")
        self._proceed_btn = QPushButton("Proceed with update")
        self._cancel_btn = QPushButton("Cancel update")
        self._proceed_btn.setEnabled(False)
        self._cancel_btn.setEnabled(False)
        self._proceed_btn.clicked.connect(self._on_proceed)
        self._cancel_btn.clicked.connect(self._on_cancel)

        button_row = QHBoxLayout()
        button_row.addWidget(self._decision_label, stretch=1)
        button_row.addWidget(self._cancel_btn)
        button_row.addWidget(self._proceed_btn)

        layout = QVBoxLayout(self)
        layout.addWidget(self._summary)
        layout.addWidget(self._counts)
        layout.addWidget(self._preview_banner)
        layout.addWidget(self._tree, stretch=1)
        layout.addLayout(button_row)

    # ── Population ────────────────────────────────────────────────────────

    def set_pending(self, pending: list[PendingUpdate]) -> None:
        self._tree.clear()
        by_risk: dict[RiskLevel, list[PendingUpdate]] = {
            RiskLevel.HIGH: [],
            RiskLevel.MEDIUM: [],
            RiskLevel.LOW: [],
        }
        for p in pending:
            by_risk[p.risk].append(p)

        self._counts.setText(
            f"{len(pending)} total — {len(by_risk[RiskLevel.HIGH])} HIGH · "
            f"{len(by_risk[RiskLevel.MEDIUM])} MEDIUM · {len(by_risk[RiskLevel.LOW])} LOW"
        )

        palette = status_palette()
        for level, label, color in (
            (RiskLevel.HIGH, "HIGH RISK", palette.high_fg),
            (RiskLevel.MEDIUM, "MEDIUM RISK", palette.medium_fg),
            (RiskLevel.LOW, "LOW RISK", None),
        ):
            packages = by_risk[level]
            if not packages:
                continue
            group = QTreeWidgetItem([f"{label}  ({len(packages)})", "", ""])
            font = group.font(0)
            font.setBold(True)
            group.setFont(0, font)
            if color is not None:
                group.setForeground(0, color)
            for pu in packages:
                tag = " [kernel]" if pu.is_kernel else f" [{pu.source}]" if pu.source == "aur" else ""
                child = QTreeWidgetItem(
                    [pu.name, f"{pu.old_version} → {pu.new_version}", (pu.reason or "") + tag]
                )
                if pu.is_kernel:
                    child.setForeground(0, palette.kernel_fg)
                # Per-row checkbox — checked = include in update.
                child.setFlags(child.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                child.setCheckState(0, Qt.CheckState.Checked)
                # Stash the package name in UserRole for fast deselection lookup.
                child.setData(0, Qt.ItemDataRole.UserRole, pu.name)
                group.addChild(child)
            self._tree.addTopLevelItem(group)
        self._tree.expandAll()

    def set_preview_banner(self, replacement_count: int, conflict_count: int) -> None:
        if replacement_count == 0 and conflict_count == 0:
            self._preview_banner.setVisible(False)
            return
        parts = []
        if replacement_count:
            parts.append(f"{replacement_count} package replacement(s)")
        if conflict_count:
            parts.append(f"{conflict_count} conflict warning(s)")
        self._preview_banner.setText(
            "NOTE: " + ", ".join(parts) + ". --noconfirm defaults these to 'No' — "
            "review the log and consider running pacman manually if anything looks off."
        )
        self._preview_banner.setVisible(True)

    def reset(self) -> None:
        self._tree.clear()
        self._counts.setText("")
        self._preview_banner.setVisible(False)
        self.disable_decision()

    # ── Decision-window controls ──────────────────────────────────────────

    def enable_decision(self, prompt: str = "") -> None:
        """Activate Proceed/Cancel buttons; called by the prompter when blocking.

        `prompt` is shown next to the buttons explaining what the decision is
        (typically "HIGH RISK detected — review packages and choose:").
        """
        self._decision_label.setText(prompt)
        self._proceed_btn.setEnabled(True)
        self._cancel_btn.setEnabled(True)

    def disable_decision(self) -> None:
        self._decision_label.setText("")
        self._proceed_btn.setEnabled(False)
        self._cancel_btn.setEnabled(False)

    def collect_deselected(self) -> list[str]:
        """Walk the tree and return names of all unchecked leaf rows."""
        deselected: list[str] = []
        for i in range(self._tree.topLevelItemCount()):
            group = self._tree.topLevelItem(i)
            for j in range(group.childCount()):
                child = group.child(j)
                if child.checkState(0) == Qt.CheckState.Unchecked:
                    name = child.data(0, Qt.ItemDataRole.UserRole)
                    if isinstance(name, str) and name:
                        deselected.append(name)
        return deselected

    def _on_proceed(self) -> None:
        deselected = self.collect_deselected()
        self.disable_decision()
        self.decision_made.emit(True, deselected)

    def _on_cancel(self) -> None:
        self.disable_decision()
        self.decision_made.emit(False, [])
