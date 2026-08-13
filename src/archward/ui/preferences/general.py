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


class _GeneralTab(_Tab):
    section = "general"

    def __init__(self) -> None:
        super().__init__()
        self._snapshot_dir = QLineEdit()
        snapshot_browse = QPushButton("Browse…")
        snapshot_browse.clicked.connect(lambda: self._browse(self._snapshot_dir))

        self._log_dir = QLineEdit()
        log_browse = QPushButton("Browse…")
        log_browse.clicked.connect(lambda: self._browse(self._log_dir))

        self._keep_snapshots = QSpinBox()
        self._keep_snapshots.setRange(1, 500)
        self._keep_days = QSpinBox()
        self._keep_days.setRange(0, 3650)
        self._keep_days.setSpecialValueText("disabled")
        self._keep_min = QSpinBox()
        self._keep_min.setRange(0, 100)
        self._keep_logs = QSpinBox()
        self._keep_logs.setRange(1, 100)

        self._notify_on_completion = QCheckBox("Show a desktop notification when the pipeline finishes")
        self._after_snapshot = QCheckBox("Take a snapshot after a successful verify pass")

        snapshot_row = QHBoxLayout()
        snapshot_row.addWidget(self._snapshot_dir, stretch=1)
        snapshot_row.addWidget(snapshot_browse)
        log_row = QHBoxLayout()
        log_row.addWidget(self._log_dir, stretch=1)
        log_row.addWidget(log_browse)

        form = QFormLayout(self)
        form.addRow("Snapshot directory:", _field_with_help(_wrap(snapshot_row), "general", "snapshot_dir"))
        form.addRow("Max snapshots (hard cap):", _field_with_help(self._keep_snapshots, "general", "keep_snapshots"))
        form.addRow("Prune snapshots older than (days):", _field_with_help(self._keep_days, "general", "keep_days"))
        form.addRow("Always keep at least:", _field_with_help(self._keep_min, "general", "keep_min"))
        form.addRow("", _field_with_help(self._after_snapshot, "general", "after_snapshot"))
        form.addRow("Log directory:", _field_with_help(_wrap(log_row), "general", "log_dir"))
        form.addRow("Keep N log files:", _field_with_help(self._keep_logs, "general", "keep_logs"))
        form.addRow("", _field_with_help(self._notify_on_completion, "general", "notify_on_completion"))

    def _browse(self, target: QLineEdit) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Choose directory", target.text())
        if directory:
            target.setText(directory)

    def load(self, cfg: ConfigModel) -> None:
        self._snapshot_dir.setText(str(cfg.general.snapshot_dir))
        self._log_dir.setText(str(cfg.general.log_dir))
        self._keep_snapshots.setValue(cfg.general.keep_snapshots)
        self._keep_days.setValue(cfg.general.keep_days)
        self._keep_min.setValue(cfg.general.keep_min)
        self._keep_logs.setValue(cfg.general.keep_logs)
        self._notify_on_completion.setChecked(cfg.general.notify_on_completion)
        self._after_snapshot.setChecked(cfg.general.after_snapshot)

    def dump(self) -> GeneralConfig:
        return GeneralConfig(
            snapshot_dir=Path(self._snapshot_dir.text()),
            log_dir=Path(self._log_dir.text()),
            keep_snapshots=self._keep_snapshots.value(),
            keep_days=self._keep_days.value(),
            keep_min=self._keep_min.value(),
            after_snapshot=self._after_snapshot.isChecked(),
            keep_logs=self._keep_logs.value(),
            notify_on_completion=self._notify_on_completion.isChecked(),
        )
