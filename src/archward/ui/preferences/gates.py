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


class _GatesTab(_Tab):
    section = "gates"

    def __init__(self) -> None:
        super().__init__()
        self._max_age = QSpinBox()
        self._max_age.setRange(1, 1440)
        self._max_age.setSuffix(" min")
        self._min_disk = QSpinBox()
        self._min_disk.setRange(1, 1000)
        self._min_disk.setSuffix(" GB")
        self._allow_override = QCheckBox("Allow override on recoverable gate failures")
        self._skip_news = QCheckBox("Skip Arch News pre-flight check")

        form = QFormLayout(self)
        form.addRow("Snapshot max age:", _field_with_help(self._max_age, "gates", "snapshot_max_age_minutes"))
        form.addRow("Minimum free disk on /:", _field_with_help(self._min_disk, "gates", "min_disk_gb"))
        form.addRow("", _field_with_help(self._allow_override, "gates", "allow_override"))
        form.addRow("", _field_with_help(self._skip_news, "gates", "skip_news_check"))

    def load(self, cfg: ConfigModel) -> None:
        self._max_age.setValue(cfg.gates.snapshot_max_age_minutes)
        self._min_disk.setValue(cfg.gates.min_disk_gb)
        self._allow_override.setChecked(cfg.gates.allow_override)
        self._skip_news.setChecked(cfg.gates.skip_news_check)

    def dump(self) -> GatesConfig:
        return GatesConfig(
            snapshot_max_age_minutes=self._max_age.value(),
            min_disk_gb=self._min_disk.value(),
            allow_override=self._allow_override.isChecked(),
            skip_news_check=self._skip_news.isChecked(),
        )
