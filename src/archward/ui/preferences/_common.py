"""Split from the monolithic preferences.py (v0.5) — see
archward/ui/preferences/__init__.py for the package map. The import
block is shared across tab modules; unused names are mechanical fallout."""


from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from pydantic import ValidationError

from archward.config import paths as config_paths
from archward.config.defaults import default_config
from archward.config.detect import apply_detection, diff_against, run_full_detection
from archward.config.loader import default_config_path, merge_partial, write_config
from archward.models.config import (
    AurConfig,
    ConfigModel,
    GatesConfig,
    GeneralConfig,
    HooksConfig,
    PacmanConfig,
    PacnewConfig,
    PrivilegeConfig,
    RiskConfig,
    ServicesConfig,
    VerifyConfig,
)
from archward.ui.dialogs import help_text
from archward.ui.dialogs.hook_templates import (
    HOOK_TEMPLATES,
    format_template_for_insertion,
)

log = logging.getLogger(__name__)


def _lines_to_tuple(text: str) -> tuple[str, ...]:
    return tuple(line.strip() for line in text.splitlines() if line.strip())


def _tuple_to_lines(items) -> str:
    return "\n".join(items)


def _grey_out(item: QTableWidgetItem) -> None:
    """Apply a greyed-out foreground to a read-only table item."""
    item.setForeground(QColor("#888888"))


def _open_in_editor(parent: QWidget, path: Path) -> None:
    """Open `path` in the user's preferred GUI editor.

    Priority order:
      1. `$VISUAL` if explicitly set — semantically the "GUI-capable editor".
      2. `xdg-open` — routes through freedesktop mime associations
         (Kate/gedit/code/etc. depending on the user's setup).
      3. `$EDITOR` as a last resort.

    Why not just $EDITOR? `$EDITOR` traditionally points at a terminal
    editor (nvim/vim/nano). Spawning a terminal editor via QProcess
    without a TTY produces no visible window — the process exits
    immediately. xdg-open is the freedesktop primitive for "open file
    in the user's default app" and is what KDE/GNOME/etc. honor.

    Shared by the Advanced and Profiles tabs so the open-in-editor
    affordance behaves identically regardless of which file the user
    points it at.
    """
    candidates: list[str] = []
    if os.environ.get("VISUAL"):
        candidates.append(os.environ["VISUAL"])
    candidates.append("xdg-open")
    if os.environ.get("EDITOR"):
        candidates.append(os.environ["EDITOR"])

    for cmd in candidates:
        try:
            subprocess.Popen([cmd, str(path)])
            return
        except FileNotFoundError:
            continue
    QMessageBox.warning(
        parent,
        "No editor available",
        "Couldn't find xdg-open, $VISUAL, or $EDITOR. Install xdg-utils "
        "or set $VISUAL to a GUI editor (e.g. kate, gedit, code).",
    )


def _lbl(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet("color: #6c757d;")
    return lbl


def _help_label(text: str) -> QLabel:
    """Help label using full-strength text color + italic.

    Prior iterations used `palette(mid)` and `#6c757d` for visual muting, but
    both rendered invisibly on some themes — Plasma Breeze pins `mid` very
    close to the window background, and hard-coded grays disappear on dark
    themes. Using `palette(text)` guarantees the help is always readable
    regardless of theme; visual hierarchy comes from italic + smaller font +
    slight left indent rather than from color.
    """
    lbl = QLabel(text)
    lbl.setStyleSheet(
        "color: palette(text);"
        "font-style: italic;"
        "padding-left: 8px;"
        "font-size: 11px;"
    )
    lbl.setWordWrap(True)
    return lbl


def _field_with_help(widget: QWidget, section: str, field: str) -> QWidget:
    """Wrap `widget` with a small help label below it. The label text is sourced
    from help_text.HELP keyed by (section, field). Missing keys produce no label."""
    body = help_text.get(section, field)
    if not body:
        return widget
    container = QWidget()
    vbox = QVBoxLayout(container)
    vbox.setContentsMargins(0, 0, 0, 0)
    vbox.setSpacing(2)
    vbox.addWidget(widget)
    vbox.addWidget(_help_label(body))
    return container


def _section_help(section: str, key: str = "_section") -> QLabel | None:
    """Section-level help banner shown at the top of a tab. None if missing.

    Styled with a brand-teal left border so every Preferences tab reads as
    coherently themed — the banner's stripe matches the running-phase
    stripe in the main window's phase rail.
    """
    body = help_text.get(section, key)
    if not body:
        return None
    from archward.ui.theme import brand_palette
    accent = brand_palette().accent_border
    lbl = _help_label(body)
    lbl.setStyleSheet(
        "color: palette(text);"
        "font-style: italic;"
        "font-size: 11px;"
        "padding: 4px 0 8px 12px;"
        f"border-left: 3px solid {accent};"
    )
    return lbl


def _wrap(layout) -> QWidget:
    """Wrap a layout in a QWidget so it can be added to a QFormLayout row."""
    w = QWidget()
    w.setLayout(layout)
    return w


def _make_list_edit() -> QPlainTextEdit:
    edit = QPlainTextEdit()
    edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
    font = QFont("monospace")
    font.setStyleHint(QFont.StyleHint.TypeWriter)
    edit.setFont(font)
    return edit
