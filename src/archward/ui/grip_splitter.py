"""Shared grip-handle splitter widget used throughout the archward UI.

GripSplitter is a drop-in for QSplitter.  Every handle paints three
small dots centred on the handle bar.  Dot orientation follows the
splitter direction so the indicator always points perpendicular to the
drag axis (horizontal dots on a vertical/drag-up-down handle; vertical
dots on a horizontal/drag-left-right handle).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import QSplitter, QSplitterHandle


class GripHandle(QSplitterHandle):
    def __init__(self, orientation, parent) -> None:
        super().__init__(orientation, parent)
        from archward.ui.theme import brand_palette
        self._accent = brand_palette().accent_fg

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(self._accent, 1))
        cx = self.width() // 2
        cy = self.height() // 2
        if self.orientation() == Qt.Orientation.Vertical:
            # Handle bar is horizontal — dots spread left/right
            for dx in (-5, 0, 5):
                painter.drawEllipse(cx + dx - 2, cy - 2, 4, 4)
        else:
            # Handle bar is vertical — dots spread up/down
            for dy in (-5, 0, 5):
                painter.drawEllipse(cx - 2, cy + dy - 2, 4, 4)


class GripSplitter(QSplitter):
    """QSplitter subclass with a visible grip-dot handle and hover highlight."""

    def __init__(self, orientation: Qt.Orientation = Qt.Orientation.Horizontal, parent=None) -> None:
        super().__init__(orientation, parent)
        self.setHandleWidth(8)
        self.setStyleSheet("QSplitter::handle:hover { background-color: palette(highlight); }")

    def createHandle(self) -> QSplitterHandle:
        return GripHandle(self.orientation(), self)
