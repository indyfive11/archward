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


class _HooksTab(_Tab):
    section = "hooks"

    def __init__(self) -> None:
        super().__init__()
        self._pre_update = _make_list_edit()
        self._pre_update.setPlaceholderText(
            "# One shell command per line\n"
            "# e.g.:\n"
            "rsync -a ~/Documents /mnt/backup/\n"
            "echo Pre-update at $(date) >> ~/.archward-runs.log"
        )
        self._post_verify = _make_list_edit()
        self._post_verify.setPlaceholderText(
            "# e.g.:\n"
            "/usr/bin/notify-send -u low 'archward done' \"RESULT: $ARCHWARD_PHASE\""
        )
        self._timeout = QSpinBox()
        self._timeout.setRange(1, 3600)
        self._timeout.setSuffix(" s")
        self._fail_on_error = QCheckBox(
            "Abort pipeline if any pre_update hook exits non-zero"
        )

        layout = QVBoxLayout(self)
        section_help = _section_help("hooks")
        if section_help is not None:
            layout.addWidget(section_help)

        # ── Pre-update editor + template dropdown ─────────────────────────
        pre_header_row = QHBoxLayout()
        pre_header_row.addWidget(
            _lbl("Pre-update hooks (run before pacman -Syu, one per line):")
        )
        pre_header_row.addStretch(1)
        pre_template = QComboBox()
        pre_template.addItem("Insert template…")
        for label, (kind, _body) in HOOK_TEMPLATES.items():
            if kind == "pre":
                pre_template.addItem(label)
        pre_template.currentIndexChanged.connect(
            lambda i, combo=pre_template: self._insert_template(
                combo, self._pre_update
            )
        )
        pre_header_row.addWidget(pre_template)
        layout.addLayout(pre_header_row)
        layout.addWidget(self._pre_update, stretch=1)
        layout.addWidget(_help_label(help_text.get("hooks", "pre_update")))

        # ── Post-verify editor + template dropdown ────────────────────────
        post_header_row = QHBoxLayout()
        post_header_row.addWidget(
            _lbl("Post-verify hooks (run after verify phase, one per line):")
        )
        post_header_row.addStretch(1)
        post_template = QComboBox()
        post_template.addItem("Insert template…")
        for label, (kind, _body) in HOOK_TEMPLATES.items():
            if kind == "post":
                post_template.addItem(label)
        post_template.currentIndexChanged.connect(
            lambda i, combo=post_template: self._insert_template(
                combo, self._post_verify
            )
        )
        post_header_row.addWidget(post_template)
        layout.addLayout(post_header_row)
        layout.addWidget(self._post_verify, stretch=1)
        layout.addWidget(_help_label(help_text.get("hooks", "post_verify")))

        form = QFormLayout()
        form.addRow("Per-hook timeout:", _field_with_help(self._timeout, "hooks", "timeout_seconds"))
        form.addRow("", _field_with_help(self._fail_on_error, "hooks", "fail_pipeline_on_error"))
        form_widget = QWidget()
        form_widget.setLayout(form)
        layout.addWidget(form_widget)

    def load(self, cfg: ConfigModel) -> None:
        self._pre_update.setPlainText(_tuple_to_lines(cfg.hooks.pre_update))
        self._post_verify.setPlainText(_tuple_to_lines(cfg.hooks.post_verify))
        self._timeout.setValue(cfg.hooks.timeout_seconds)
        self._fail_on_error.setChecked(cfg.hooks.fail_pipeline_on_error)

    def dump(self) -> HooksConfig:
        return HooksConfig(
            pre_update=_lines_to_tuple(self._pre_update.toPlainText()),
            post_verify=_lines_to_tuple(self._post_verify.toPlainText()),
            timeout_seconds=self._timeout.value(),
            fail_pipeline_on_error=self._fail_on_error.isChecked(),
        )

    def _insert_template(self, combo: QComboBox, editor: QPlainTextEdit) -> None:
        """Append the selected template body to the editor, then reset the
        combobox to its placeholder so the user can pick the same template
        again if they want a second copy."""
        idx = combo.currentIndex()
        if idx <= 0:  # 0 = "Insert template…" placeholder
            return
        label = combo.currentText()
        snippet = format_template_for_insertion(label)
        if not snippet:
            combo.setCurrentIndex(0)
            return
        existing = editor.toPlainText()
        sep = "" if not existing or existing.endswith("\n") else "\n"
        editor.setPlainText(existing + sep + snippet)
        combo.blockSignals(True)
        combo.setCurrentIndex(0)
        combo.blockSignals(False)
