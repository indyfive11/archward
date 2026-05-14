"""Pacnew phase content view — table of detected .pacnew files.

Phase 5 ships read-only display; per-row apply buttons (Keep Ours / Take New /
Edit) and the diff dialog are reserved for Phase 6 polish. Users currently
resolve pacnews from a terminal as they did with the bash pipeline.
"""

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

from archward.models.pacnew import PacnewFile, PacnewRecommendation

_REC_COLORS = {
    PacnewRecommendation.KEEP_OURS: QColor(80, 180, 100),
    PacnewRecommendation.TAKE_NEW: QColor(80, 130, 200),
    PacnewRecommendation.REVIEW_NEEDED: QColor(220, 170, 60),
}


class PacnewView(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._header = QLabel("Pacnew files")
        self._header.setStyleSheet("font-weight: bold; padding: 8px;")
        self._tree = QTreeWidget()
        self._tree.setColumnCount(3)
        self._tree.setHeaderLabels(["File", "Recommendation", "Note"])
        self._tree.setRootIsDecorated(False)
        self._tree.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)

        layout = QVBoxLayout(self)
        layout.addWidget(self._header)
        layout.addWidget(self._tree, stretch=1)

    def set_files(self, files: list[PacnewFile]) -> None:
        self._tree.clear()
        if not files:
            self._header.setText("Pacnew — no new .pacnew files")
            return
        self._header.setText(f"Pacnew — {len(files)} file(s) need attention")
        for f in files:
            item = QTreeWidgetItem(
                [str(f.path), f.recommendation.value, f.note or ""]
            )
            color = _REC_COLORS.get(f.recommendation)
            if color is not None:
                item.setForeground(1, color)
            self._tree.addTopLevelItem(item)

    def reset(self) -> None:
        self._tree.clear()
        self._header.setText("Pacnew files")
