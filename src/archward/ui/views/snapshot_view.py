"""Snapshot phase content view — simple progress label + step list.

Phase 5 minimal: a label showing the current step (1/6 Packages, …, 6/6 Pacnew
baseline). Phase 6 may swap for a ticking checklist.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QListWidget, QListWidgetItem, QVBoxLayout, QWidget

_STEPS = (
    "Packages",
    "Configs",
    "Network",
    "Services",
    "System",
    "Pacnew baseline",
)


class SnapshotView(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._label = QLabel("Snapshot phase — capturing system state…")
        self._label.setStyleSheet("font-weight: bold; padding: 8px;")
        self._list = QListWidget()
        self._list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        for step in _STEPS:
            self._list.addItem(QListWidgetItem(f"○  {step}"))

        layout = QVBoxLayout(self)
        layout.addWidget(self._label)
        layout.addWidget(self._list, stretch=1)

    def note_step(self, idx: int, total: int = 6) -> None:
        """Mark step `idx` (1-based) as complete and the next as in-progress."""
        if 1 <= idx <= len(_STEPS):
            self._list.item(idx - 1).setText(f"●  {_STEPS[idx - 1]}")
            self._label.setText(f"Snapshot phase — step {idx}/{total}: {_STEPS[idx - 1]}")

    def mark_complete(self) -> None:
        self._label.setText("Snapshot complete.")
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item.text().startswith("○"):
                item.setText("●" + item.text()[1:])

    def reset(self) -> None:
        self._label.setText("Snapshot phase — capturing system state…")
        for i in range(self._list.count()):
            self._list.item(i).setText(f"○  {_STEPS[i]}")
