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


class _AdvancedTab(QWidget):
    """Not a _Tab — doesn't have load/dump. Provides actions that mutate the
    parent dialog's draft config."""

    redetect_requested = Signal()
    reset_requested = Signal()

    def __init__(self, config_path: Path | None = None) -> None:
        super().__init__()
        # When a profile is active, all "active config" affordances must point
        # at that profile's file, not the default config.toml.
        self._active_path = config_path if config_path is not None else default_config_path()

        redetect_btn = QPushButton("Re-detect…")
        redetect_btn.setToolTip(
            "Re-run distro/kernel/AUR/service detection and propose changes."
        )
        redetect_btn.clicked.connect(self.redetect_requested.emit)

        reset_btn = QPushButton("Reset to defaults…")
        reset_btn.setToolTip("Replace all settings with archward defaults.")
        reset_btn.clicked.connect(self.reset_requested.emit)

        open_cfg_btn = QPushButton("Open config file in editor")
        open_cfg_btn.setToolTip(
            "Opens the active config file in $EDITOR or the desktop default."
        )
        open_cfg_btn.clicked.connect(self._open_config)

        self._path_label = _help_label(f"Active config file: {self._active_path}")

        layout = QVBoxLayout(self)
        layout.addWidget(redetect_btn)
        layout.addWidget(reset_btn)
        layout.addWidget(open_cfg_btn)
        layout.addStretch(1)
        layout.addWidget(self._path_label)

    def set_active_path(self, path: Path) -> None:
        """Update which file the open-in-editor / path-label point at."""
        self._active_path = path
        self._path_label.setText(f"Active config file: {path}")

    def _open_config(self) -> None:
        _open_in_editor(self, self._active_path)
