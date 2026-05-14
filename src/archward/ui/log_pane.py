"""Read-only monospace log pane.

Backed by a QPlainTextEdit with a soft 10k-line cap (auto-trims the head when
exceeded). Auto-scrolls to the bottom by default; user can toggle.
"""

from __future__ import annotations

from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import QPlainTextEdit

_MAX_LINES = 10_000


class LogPane(QPlainTextEdit):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setUndoRedoEnabled(False)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        font = QFont("monospace")
        font.setStyleHint(QFont.StyleHint.TypeWriter)
        self.setFont(font)
        self.setMaximumBlockCount(_MAX_LINES)
        self._autoscroll = True

    def append_line(self, text: str) -> None:
        self.appendPlainText(text)
        if self._autoscroll:
            cursor = self.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            self.setTextCursor(cursor)

    def set_autoscroll(self, enabled: bool) -> None:
        self._autoscroll = enabled

    def clear_log(self) -> None:
        self.clear()
