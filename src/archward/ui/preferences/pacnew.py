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


_PACNEW_STRATEGY_VALUES = ("keep_ours", "take_new", "review_needed")


class _PacnewTab(_Tab):
    section = "pacnew"

    def __init__(self) -> None:
        super().__init__()
        self._default = QComboBox()
        self._default.addItems(_PACNEW_STRATEGY_VALUES)

        self._rules = QTableWidget(0, 3)
        self._rules.setHorizontalHeaderLabels(["Pattern", "Strategy", "Note"])
        self._rules.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._rules.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._rules.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._rules.verticalHeader().setVisible(False)

        add_btn = QPushButton("Add rule")
        add_btn.clicked.connect(lambda: self._add_rule_row())
        del_btn = QPushButton("Remove selected")
        del_btn.clicked.connect(self._remove_selected_rules)
        restore_btn = QPushButton("Restore defaults…")
        restore_btn.clicked.connect(self._restore_defaults)
        btn_row = QHBoxLayout()
        btn_row.addWidget(add_btn)
        btn_row.addWidget(del_btn)
        btn_row.addWidget(restore_btn)
        btn_row.addStretch(1)

        form_top = QFormLayout()
        form_top.addRow("Default strategy:",
                        _field_with_help(self._default, "pacnew", "default_strategy"))

        layout = QVBoxLayout(self)
        layout.addLayout(form_top)
        layout.addWidget(_lbl("Rules — first matching pattern wins (fnmatch globs):"))
        layout.addWidget(self._rules, stretch=1)
        rules_help = _help_label(help_text.get("pacnew", "_section_rules"))
        if rules_help.text():
            layout.addWidget(rules_help)
        layout.addLayout(btn_row)

    def _add_rule_row(
        self,
        pattern: str = "",
        strategy: str = "review_needed",
        note: str = "",
    ) -> None:
        row = self._rules.rowCount()
        self._rules.insertRow(row)
        self._rules.setItem(row, 0, QTableWidgetItem(pattern))
        combo = QComboBox()
        combo.addItems(_PACNEW_STRATEGY_VALUES)
        combo.setCurrentText(strategy)
        self._rules.setCellWidget(row, 1, combo)
        self._rules.setItem(row, 2, QTableWidgetItem(note))

    def _remove_selected_rules(self) -> None:
        rows = sorted({i.row() for i in self._rules.selectedIndexes()}, reverse=True)
        for r in rows:
            self._rules.removeRow(r)

    def _restore_defaults(self) -> None:
        if self._rules.rowCount() > 0:
            answer = QMessageBox.question(
                self,
                "Restore default pacnew rules",
                "Replace the current rule list with the built-in defaults? "
                "Your custom rules will be lost.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._rules.setRowCount(0)
        for rule in default_config().pacnew.rules:
            self._add_rule_row(rule.pattern, rule.strategy.value, rule.note or "")

    def load(self, cfg: ConfigModel) -> None:
        self._default.setCurrentText(cfg.pacnew.default_strategy.value)
        self._rules.setRowCount(0)
        for rule in cfg.pacnew.rules:
            self._add_rule_row(rule.pattern, rule.strategy.value, rule.note or "")

    def dump(self) -> PacnewConfig:
        from archward.models.config import PacnewRule
        from archward.models.pacnew import PacnewRecommendation

        rules: list[PacnewRule] = []
        for r in range(self._rules.rowCount()):
            pat_item = self._rules.item(r, 0)
            note_item = self._rules.item(r, 2)
            combo = self._rules.cellWidget(r, 1)
            pattern = pat_item.text().strip() if pat_item else ""
            if not pattern:
                continue  # blank rows dropped on save
            note_text = note_item.text().strip() if note_item else ""
            rules.append(PacnewRule(
                pattern=pattern,
                strategy=PacnewRecommendation(combo.currentText()),
                note=note_text or None,
            ))
        return PacnewConfig(
            default_strategy=PacnewRecommendation(self._default.currentText()),
            rules=tuple(rules),
        )
