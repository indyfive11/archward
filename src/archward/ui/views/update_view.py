"""Update phase content view — streaming monospace pane (shared official + AUR)."""

from __future__ import annotations

from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import QLabel, QPlainTextEdit, QVBoxLayout, QWidget


class UpdateView(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._header = QLabel("Update")
        self._header.setStyleSheet("font-weight: bold; padding: 8px;")
        self._stream = QPlainTextEdit()
        self._stream.setReadOnly(True)
        self._stream.setUndoRedoEnabled(False)
        self._stream.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        font = QFont("monospace")
        font.setStyleHint(QFont.StyleHint.TypeWriter)
        self._stream.setFont(font)
        self._stream.setMaximumBlockCount(10_000)

        layout = QVBoxLayout(self)
        layout.addWidget(self._header)
        layout.addWidget(self._stream, stretch=1)

    def set_header(self, label: str) -> None:
        self._header.setText(label)

    def append(self, line: str) -> None:
        self._stream.appendPlainText(line)
        cursor = self._stream.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self._stream.setTextCursor(cursor)

    def reset(self) -> None:
        self._stream.clear()
        self._header.setText("Update")
