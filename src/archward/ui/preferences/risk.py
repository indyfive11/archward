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


class _RiskTab(_Tab):
    section = "risk"

    def __init__(self) -> None:
        super().__init__()
        self._high = _make_list_edit()
        self._medium_patterns = _make_list_edit()
        self._kernel_patterns = _make_list_edit()
        self._kernel_excludes = _make_list_edit()

        layout = QVBoxLayout(self)
        section_help = _section_help("risk")
        if section_help is not None:
            layout.addWidget(section_help)

        form = QFormLayout()
        form.addRow(_lbl("HIGH-risk packages (exact match, one per line):"),
                    _field_with_help(self._high, "risk", "high"))
        form.addRow(_lbl("MEDIUM patterns (fnmatch glob, one per line):"),
                    _field_with_help(self._medium_patterns, "risk", "medium_patterns"))
        form.addRow(_lbl("Kernel patterns (fnmatch, → HIGH + is_kernel):"),
                    _field_with_help(self._kernel_patterns, "risk", "kernel_patterns"))
        form.addRow(_lbl("Kernel pattern excludes (e.g. linux-firmware*):"),
                    _field_with_help(self._kernel_excludes, "risk", "kernel_pattern_exclude"))

        form_wrap = QWidget()
        form_wrap.setLayout(form)
        layout.addWidget(form_wrap, stretch=1)

    def load(self, cfg: ConfigModel) -> None:
        self._high.setPlainText(_tuple_to_lines(cfg.risk.high))
        self._medium_patterns.setPlainText(_tuple_to_lines(cfg.risk.medium_patterns))
        self._kernel_patterns.setPlainText(_tuple_to_lines(cfg.risk.kernel_patterns))
        self._kernel_excludes.setPlainText(_tuple_to_lines(cfg.risk.kernel_pattern_exclude))

    def dump(self) -> RiskConfig:
        return RiskConfig(
            high=_lines_to_tuple(self._high.toPlainText()),
            medium_patterns=_lines_to_tuple(self._medium_patterns.toPlainText()),
            kernel_patterns=_lines_to_tuple(self._kernel_patterns.toPlainText()),
            kernel_pattern_exclude=_lines_to_tuple(self._kernel_excludes.toPlainText()),
        )
