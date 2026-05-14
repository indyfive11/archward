"""Risk phase content view — three stacked HIGH/MEDIUM/LOW trees.

HIGH packages are highlighted in red. Transaction-preview replacements show
in a separate banner above the tree. Phase 5 doesn't implement per-row
"deselect" yet; --noconfirm runs the full pacman -Syu, and HIGH-risk approval
is handled via the GuiPrompter modal.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QHeaderView,
    QLabel,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from archward.models.update import PendingUpdate, RiskLevel

_HIGH_COLOR = QColor(220, 70, 70)
_MEDIUM_COLOR = QColor(220, 170, 60)
_KERNEL_COLOR = QColor(245, 130, 60)


class RiskView(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._summary = QLabel("Risk classification")
        self._summary.setStyleSheet("font-weight: bold; padding: 8px;")
        self._counts = QLabel("")
        self._counts.setStyleSheet("padding: 0 8px 8px 8px;")
        self._preview_banner = QLabel("")
        self._preview_banner.setStyleSheet(
            "padding: 4px 8px; background: #fff3cd; border: 1px solid #ffeeba; color: #856404;"
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

        layout = QVBoxLayout(self)
        layout.addWidget(self._summary)
        layout.addWidget(self._counts)
        layout.addWidget(self._preview_banner)
        layout.addWidget(self._tree, stretch=1)

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

        for level, label, color in (
            (RiskLevel.HIGH, "HIGH RISK", _HIGH_COLOR),
            (RiskLevel.MEDIUM, "MEDIUM RISK", _MEDIUM_COLOR),
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
            for p in packages:
                tag = " [kernel]" if p.is_kernel else f" [{p.source}]" if p.source == "aur" else ""
                child = QTreeWidgetItem(
                    [p.name, f"{p.old_version} → {p.new_version}", (p.reason or "") + tag]
                )
                if p.is_kernel:
                    child.setForeground(0, _KERNEL_COLOR)
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
