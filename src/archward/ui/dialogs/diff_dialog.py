"""Unified-diff viewer for .pacnew vs original.

Reads the two files (falling back to `sudo cat` if direct read fails — common
for /etc/sudoers.d/* and similar 600-mode targets), runs them through
difflib.unified_diff, and renders the result in a QPlainTextEdit with a
QSyntaxHighlighter that colors hunks and +/- lines.
"""

from __future__ import annotations

import difflib
import logging
from pathlib import Path

from PySide6.QtCore import QRegularExpression, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QSyntaxHighlighter,
    QTextCharFormat,
    QTextDocument,
)
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
)

from archward.pacman.runner import run_capture
from archward.privilege.sudo import SudoStrategy

log = logging.getLogger(__name__)


class _DiffHighlighter(QSyntaxHighlighter):
    """Color-code unified-diff lines: red removals, green additions, gray hunks."""

    def __init__(self, parent: QTextDocument) -> None:
        super().__init__(parent)
        self._rules: list[tuple[QRegularExpression, QTextCharFormat]] = []

        fmt_add = QTextCharFormat()
        fmt_add.setForeground(QColor("#155724"))
        fmt_add.setBackground(QColor("#d4edda"))
        # `+++` is the file header; `+` starting a line is an addition.
        self._rules.append((QRegularExpression(r"^\+(?!\+\+).*$"), fmt_add))

        fmt_del = QTextCharFormat()
        fmt_del.setForeground(QColor("#721c24"))
        fmt_del.setBackground(QColor("#f8d7da"))
        self._rules.append((QRegularExpression(r"^-(?!--).*$"), fmt_del))

        fmt_hunk = QTextCharFormat()
        fmt_hunk.setForeground(QColor("#6c757d"))
        fmt_hunk.setFontWeight(QFont.Weight.Bold)
        self._rules.append((QRegularExpression(r"^@@.*@@.*$"), fmt_hunk))

        fmt_header = QTextCharFormat()
        fmt_header.setForeground(QColor("#383d41"))
        fmt_header.setFontWeight(QFont.Weight.Bold)
        self._rules.append((QRegularExpression(r"^(?:\+{3}|-{3}) .*$"), fmt_header))

    def highlightBlock(self, text: str) -> None:  # noqa: N802 — Qt naming
        for pattern, fmt in self._rules:
            it = pattern.globalMatch(text)
            while it.hasNext():
                match = it.next()
                self.setFormat(match.capturedStart(), match.capturedLength(), fmt)


def _read_file(path: Path, strategy: SudoStrategy) -> tuple[str, str | None]:
    """Read `path`. Falls back to sudo cat on PermissionError. Returns (content, error_or_None)."""
    try:
        return path.read_text(encoding="utf-8", errors="replace"), None
    except PermissionError:
        log.info("direct read of %s denied; trying sudo cat", path)
    except OSError as e:
        return "", f"could not read {path}: {e}"

    code, out, err = run_capture(["cat", str(path)], strategy=strategy)
    if code != 0:
        return "", f"sudo cat {path} failed: {err.strip() or 'exit ' + str(code)}"
    return out, None


def render_diff(orig: Path, new: Path, strategy: SudoStrategy) -> tuple[str, str | None]:
    """Render a unified diff. Returns (diff_text, error_or_None)."""
    a_text, err = _read_file(orig, strategy)
    if err:
        return "", err
    b_text, err = _read_file(new, strategy)
    if err:
        return "", err
    a_lines = a_text.splitlines(keepends=True)
    b_lines = b_text.splitlines(keepends=True)
    diff = difflib.unified_diff(a_lines, b_lines, fromfile=str(orig), tofile=str(new), n=3)
    return "".join(diff), None


class DiffDialog(QDialog):
    """Modal unified-diff viewer."""

    def __init__(self, orig: Path, new: Path, strategy: SudoStrategy, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"archward — diff: {orig.name}")
        self.resize(1000, 700)

        header = QLabel(f"<b>Original:</b> {orig}    →    <b>New:</b> {new}")
        header.setStyleSheet("padding: 6px;")
        header.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        self._view = QPlainTextEdit()
        self._view.setReadOnly(True)
        self._view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        mono = QFont("monospace")
        mono.setStyleHint(QFont.StyleHint.TypeWriter)
        self._view.setFont(mono)
        self._highlighter = _DiffHighlighter(self._view.document())

        diff_text, err = render_diff(orig, new, strategy)
        if err:
            self._view.setPlainText(f"(error rendering diff)\n\n{err}")
        elif not diff_text.strip():
            self._view.setPlainText("(files are identical — pacman wrote a .pacnew but content matches)")
        else:
            self._view.setPlainText(diff_text)

        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        close.accepted.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.addWidget(header)
        layout.addWidget(self._view, stretch=1)
        layout.addWidget(close)
