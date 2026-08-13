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


class _ServicesTab(_Tab):
    section = "services"

    def __init__(self) -> None:
        super().__init__()
        self._to_verify = _make_list_edit()

        self._severity = QTableWidget(0, 2)
        self._severity.setHorizontalHeaderLabels(["Unit", "Severity (critical | watch)"])
        self._severity.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._severity.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._severity.verticalHeader().setVisible(False)

        add_btn = QPushButton("Add override")
        add_btn.clicked.connect(lambda: self._severity.insertRow(self._severity.rowCount()))
        del_btn = QPushButton("Remove selected")
        del_btn.clicked.connect(self._remove_selected_severity)
        btn_row = QHBoxLayout()
        btn_row.addWidget(add_btn)
        btn_row.addWidget(del_btn)
        btn_row.addStretch(1)

        self._auto_prune = QCheckBox("Auto-prune stale entries during verify")

        layout = QVBoxLayout(self)
        layout.addWidget(_lbl("Services to verify (one per line; default severity is 'critical'):"))
        layout.addWidget(self._to_verify, stretch=2)
        services_help = _help_label(help_text.get("services", "to_verify"))
        if services_help.text():
            layout.addWidget(services_help)
        layout.addWidget(_lbl("Per-unit severity overrides:"))
        layout.addWidget(self._severity, stretch=1)
        severity_help = _help_label(help_text.get("services", "severity"))
        if severity_help.text():
            layout.addWidget(severity_help)
        layout.addLayout(btn_row)
        layout.addWidget(self._auto_prune)
        auto_prune_help = _help_label(help_text.get("services", "auto_prune"))
        if auto_prune_help.text():
            layout.addWidget(auto_prune_help)

    def _remove_selected_severity(self) -> None:
        rows = sorted({i.row() for i in self._severity.selectedIndexes()}, reverse=True)
        for r in rows:
            self._severity.removeRow(r)

    def load(self, cfg: ConfigModel) -> None:
        self._to_verify.setPlainText(_tuple_to_lines(cfg.services.to_verify))
        self._severity.setRowCount(0)
        for unit, sev in (cfg.services.severity or {}).items():
            row = self._severity.rowCount()
            self._severity.insertRow(row)
            self._severity.setItem(row, 0, QTableWidgetItem(unit))
            self._severity.setItem(row, 1, QTableWidgetItem(sev))
        self._auto_prune.setChecked(cfg.services.auto_prune)

    def dump(self) -> ServicesConfig:
        severity: dict[str, str] = {}
        for r in range(self._severity.rowCount()):
            unit_item = self._severity.item(r, 0)
            sev_item = self._severity.item(r, 1)
            unit = unit_item.text().strip() if unit_item else ""
            sev = sev_item.text().strip() if sev_item else ""
            if unit and sev:
                severity[unit] = sev
        return ServicesConfig(
            to_verify=_lines_to_tuple(self._to_verify.toPlainText()),
            severity=severity,
            auto_prune=self._auto_prune.isChecked(),
        )
