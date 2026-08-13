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

from archward.ui.preferences._common import (  # noqa: F401
    _field_with_help,
    _grey_out,
    _help_label,
    _lbl,
    _lines_to_tuple,
    _make_list_edit,
    _open_in_editor,
    _section_help,
    _tuple_to_lines,
    _wrap,
)
from archward.ui.preferences.tabs_base import _Tab  # noqa: F401


class _PacmanTab(_Tab):
    section = "pacman"

    def __init__(self) -> None:
        super().__init__()
        self._noconfirm = QCheckBox("Pass --noconfirm to pacman")
        self._extra_args = _make_list_edit()
        self._extra_args.setPlaceholderText("--needed\n--overwrite\n/etc/foo")

        layout = QVBoxLayout(self)
        layout.addWidget(self._noconfirm)
        layout.addWidget(_help_label(help_text.get("pacman", "noconfirm")))
        layout.addWidget(_lbl("Extra pacman arguments (one per line):"))
        layout.addWidget(self._extra_args, stretch=1)
        layout.addWidget(_help_label(help_text.get("pacman", "extra_args")))

    def load(self, cfg: ConfigModel) -> None:
        self._noconfirm.setChecked(cfg.pacman.noconfirm)
        self._extra_args.setPlainText(_tuple_to_lines(cfg.pacman.extra_args))

    def dump(self) -> PacmanConfig:
        return PacmanConfig(
            noconfirm=self._noconfirm.isChecked(),
            extra_args=_lines_to_tuple(self._extra_args.toPlainText()),
        )
