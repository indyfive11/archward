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


class _PrivilegeTab(_Tab):
    section = "privilege"

    def __init__(self) -> None:
        super().__init__()
        self._mode = QComboBox()
        self._mode.addItems(["auto", "askpass", "pkexec", "persistent_sudo"])
        self._askpass = QLineEdit()
        askpass_browse = QPushButton("Browse…")
        askpass_browse.clicked.connect(self._browse_askpass)

        ask_row = QHBoxLayout()
        ask_row.addWidget(self._askpass, stretch=1)
        ask_row.addWidget(askpass_browse)

        form = QFormLayout(self)
        form.addRow("Sudo strategy:", _field_with_help(self._mode, "privilege", "mode"))
        form.addRow("Askpass override (path):",
                    _field_with_help(_wrap(ask_row), "privilege", "askpass"))

    def _browse_askpass(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Askpass binary", "/usr/bin")
        if path:
            self._askpass.setText(path)

    def load(self, cfg: ConfigModel) -> None:
        self._mode.setCurrentText(cfg.privilege.mode)
        self._askpass.setText(cfg.privilege.askpass)

    def dump(self) -> PrivilegeConfig:
        return PrivilegeConfig(
            mode=self._mode.currentText(),
            askpass=self._askpass.text().strip(),
        )
