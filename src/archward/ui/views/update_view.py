"""Update phase content view — streaming monospace pane (shared official + AUR).

v0.4.0: inline prompt row at the bottom. When `cfg.pacman.noconfirm=False`,
pacman/yay/paru prompts are detected in the stream and surfaced here so
the user can answer without leaving the GUI. The row is hidden by default
and only shown for the duration of a single prompt.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class UpdateView(QWidget):
    # Emitted when the user clicks Send. Carries the literal response
    # string that should be written to the subprocess stdin.
    response_ready = Signal(str)

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

        # ── Inline prompt row (hidden when no prompt is pending) ──────────
        self._prompt_row = QWidget()
        prompt_layout = QHBoxLayout(self._prompt_row)
        prompt_layout.setContentsMargins(8, 4, 8, 8)
        self._prompt_label = QLabel()
        # Brand teal so the prompt visually stands apart from the streaming log.
        from archward.ui.theme import brand_palette as _bp_init
        _prompt_color = _bp_init().accent_text_css
        self._prompt_label.setStyleSheet(f"font-weight: bold; color: {_prompt_color};")
        self._prompt_input = QLineEdit()
        self._prompt_input.setFont(font)
        self._prompt_input.returnPressed.connect(self._send)
        self._send_btn = QPushButton("Send")
        self._send_btn.clicked.connect(self._send)
        # Brand accent: text color only. No setDefault(True) — a hidden
        # default button corrupts the window's default-button chain and
        # makes sibling buttons (Preferences, About) fail to repaint
        # until a hover event triggers a style poll. No pseudo-selectors
        # — they force expensive style re-resolution on every paint, which
        # starves the GUI during heavy update-phase event traffic.
        from archward.ui.theme import brand_palette
        self._send_btn.setStyleSheet(
            f"color: {brand_palette().accent_text_css}; font-weight: bold;"
        )
        prompt_layout.addWidget(self._prompt_label)
        prompt_layout.addWidget(self._prompt_input, stretch=1)
        prompt_layout.addWidget(self._send_btn)
        self._prompt_row.setVisible(False)

        layout = QVBoxLayout(self)
        layout.addWidget(self._header)
        layout.addWidget(self._stream, stretch=1)
        layout.addWidget(self._prompt_row)

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
        self.hide_prompt()

    # ── Inline prompt API (called from UpdatePrompter on main thread) ──────

    def show_prompt(self, label: str, default: str = "") -> None:
        """Light up the input row with a label and pre-filled default."""
        self._prompt_label.setText(label or "Enter response:")
        self._prompt_input.setText(default)
        self._prompt_input.selectAll()
        self._prompt_row.setVisible(True)
        self._prompt_input.setFocus()

    def hide_prompt(self) -> None:
        self._prompt_row.setVisible(False)
        self._prompt_label.setText("")
        self._prompt_input.clear()

    def _send(self) -> None:
        text = self._prompt_input.text()
        self.hide_prompt()
        self.response_ready.emit(text)
