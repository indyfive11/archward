"""Gates phase content view — table of gate results."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QHeaderView,
    QLabel,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from archward.models.gate import GateResult, GateStatus

_STATUS_COLORS = {
    GateStatus.PASS: QColor(80, 180, 100),
    GateStatus.WARN: QColor(220, 170, 60),
    GateStatus.FAIL: QColor(220, 70, 70),
    GateStatus.SKIPPED: QColor(160, 160, 160),
}


class GatesView(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._header = QLabel("Gate checks")
        self._header.setStyleSheet("font-weight: bold; padding: 8px;")
        self._tree = QTreeWidget()
        self._tree.setColumnCount(3)
        self._tree.setHeaderLabels(["Gate", "Status", "Message"])
        self._tree.setRootIsDecorated(False)
        self._tree.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)

        layout = QVBoxLayout(self)
        layout.addWidget(self._header)
        layout.addWidget(self._tree, stretch=1)

    def set_results(self, results: list[GateResult]) -> None:
        self._tree.clear()
        for r in results:
            item = QTreeWidgetItem([r.name, r.status.value.upper(), r.message])
            color = _STATUS_COLORS.get(r.status)
            if color is not None:
                item.setForeground(1, color)
            if r.detail:
                child = QTreeWidgetItem(["", "", r.detail])
                item.addChild(child)
            self._tree.addTopLevelItem(item)
        self._tree.expandAll()

    def reset(self) -> None:
        self._tree.clear()
