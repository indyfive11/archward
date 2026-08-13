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


class _AurTab(_Tab):
    section = "aur"

    # Quarantine table column indices
    _COL_PKG     = 0
    _COL_VER     = 1
    _COL_STATUS  = 2
    _COL_FAILS   = 3
    _COL_RETRY   = 4
    _COL_ERROR   = 5

    def __init__(self) -> None:
        super().__init__()

        # ── Config controls ───────────────────────────────────────────────
        self._enabled = QCheckBox("Enable AUR phase")
        self._skip = QCheckBox("Skip even when enabled (one-shot override)")
        self._helper_preference = _make_list_edit()
        self._helper_preference.setPlaceholderText("yay\nparu\naurutils")

        self._quarantine_enabled = QCheckBox("Enable build quarantine")
        self._quarantine_min_failures = QSpinBox()
        self._quarantine_min_failures.setRange(1, 10)
        self._quarantine_min_failures.setSuffix(" failure(s)")
        self._quarantine_initial_days = QSpinBox()
        self._quarantine_initial_days.setRange(1, 28)
        self._quarantine_initial_days.setSuffix(" day(s)")
        self._quarantine_max_days = QSpinBox()
        self._quarantine_max_days.setRange(7, 90)
        self._quarantine_max_days.setSuffix(" day(s)")

        qform = QFormLayout()
        qform.addRow("", self._quarantine_enabled)
        qform.addRow("", _help_label(help_text.get("aur", "quarantine_enabled")))
        qform.addRow("Quarantine after:", self._quarantine_min_failures)
        qform.addRow("", _help_label(help_text.get("aur", "quarantine_min_failures")))
        qform.addRow("Initial retry window:", self._quarantine_initial_days)
        qform.addRow("", _help_label(help_text.get("aur", "quarantine_initial_days")))
        qform.addRow("Maximum retry window:", self._quarantine_max_days)
        qform.addRow("", _help_label(help_text.get("aur", "quarantine_max_days")))

        # ── Quarantine history table ──────────────────────────────────────
        self._qtable = QTableWidget(0, 6)
        self._qtable.setHorizontalHeaderLabels(
            ["Package", "Version", "Status", "Failures", "Retry / Resolved", "Last Error"]
        )
        self._qtable.horizontalHeader().setStretchLastSection(True)
        self._qtable.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._qtable.setEditTriggers(QTableWidget.EditTrigger.DoubleClicked |
                                     QTableWidget.EditTrigger.SelectedClicked)
        self._qtable.setMinimumHeight(160)

        btn_row = QHBoxLayout()
        self._btn_clear_sel = QPushButton("Clear selected")
        self._btn_clear_resolved = QPushButton("Clear resolved")
        self._btn_clear_all = QPushButton("Clear all")
        btn_row.addWidget(self._btn_clear_sel)
        btn_row.addWidget(self._btn_clear_resolved)
        btn_row.addWidget(self._btn_clear_all)
        btn_row.addStretch()
        self._btn_clear_sel.clicked.connect(self._on_clear_selected)
        self._btn_clear_resolved.clicked.connect(self._on_clear_resolved)
        self._btn_clear_all.clicked.connect(self._on_clear_all)

        # ── Layout ────────────────────────────────────────────────────────
        layout = QVBoxLayout(self)
        layout.addWidget(self._enabled)
        layout.addWidget(_help_label(help_text.get("aur", "enabled")))
        layout.addWidget(self._skip)
        layout.addWidget(_help_label(help_text.get("aur", "skip")))
        layout.addWidget(_lbl("Helper preference (first found on PATH wins; one per line):"))
        layout.addWidget(self._helper_preference)
        layout.addWidget(_help_label(help_text.get("aur", "helper_preference")))
        layout.addWidget(_lbl("— Build quarantine —"))
        layout.addLayout(qform)
        layout.addWidget(_lbl("Quarantine history (double-click active rows to edit):"))
        layout.addWidget(self._qtable, stretch=1)
        layout.addLayout(btn_row)

    # ── Load ──────────────────────────────────────────────────────────────

    def load(self, cfg: ConfigModel) -> None:
        self._enabled.setChecked(cfg.aur.enabled)
        self._skip.setChecked(cfg.aur.skip)
        self._helper_preference.setPlainText(_tuple_to_lines(cfg.aur.helper_preference))
        self._quarantine_enabled.setChecked(cfg.aur.quarantine_enabled)
        self._quarantine_min_failures.setValue(cfg.aur.quarantine_min_failures)
        self._quarantine_initial_days.setValue(cfg.aur.quarantine_initial_days)
        self._quarantine_max_days.setValue(cfg.aur.quarantine_max_days)
        self._reload_quarantine_table(cfg)

    def _reload_quarantine_table(self, cfg: ConfigModel) -> None:
        from archward.aur.quarantine import AurQuarantine
        q = AurQuarantine(cfg.aur)
        q.load()
        self._populate_qtable(q.entries())

    def _populate_qtable(self, entries: list) -> None:
        self._qtable.setRowCount(0)
        for pkg, entry in entries:
            row = self._qtable.rowCount()
            self._qtable.insertRow(row)
            editable = entry.status != "resolved"
            flags_ro = Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
            flags_rw = flags_ro | Qt.ItemFlag.ItemIsEditable

            # Package (read-only)
            pkg_item = QTableWidgetItem(pkg)
            pkg_item.setFlags(flags_ro)
            self._qtable.setItem(row, self._COL_PKG, pkg_item)

            # Version (read-only)
            ver_item = QTableWidgetItem(entry.version)
            ver_item.setFlags(flags_ro)
            self._qtable.setItem(row, self._COL_VER, ver_item)

            # Status — QComboBox for active rows, read-only label for resolved
            if editable:
                status_combo = QComboBox()
                status_combo.addItems(["counting", "quarantined", "resolved"])
                status_combo.setCurrentText(entry.status)
                self._qtable.setCellWidget(row, self._COL_STATUS, status_combo)
            else:
                status_item = QTableWidgetItem("resolved")
                status_item.setFlags(flags_ro)
                _grey_out(status_item)
                self._qtable.setItem(row, self._COL_STATUS, status_item)

            # Failures — editable int for active rows
            fail_item = QTableWidgetItem(str(entry.failure_count))
            fail_item.setFlags(flags_rw if editable else flags_ro)
            if not editable:
                _grey_out(fail_item)
            self._qtable.setItem(row, self._COL_FAILS, fail_item)

            # Retry / Resolved date
            if entry.status == "quarantined" and entry.retry_after is not None:
                from datetime import datetime, timezone
                date_str = datetime.fromtimestamp(
                    entry.retry_after, tz=timezone.utc
                ).strftime("%Y-%m-%d")
                date_item = QTableWidgetItem(date_str)
                date_item.setFlags(flags_rw)
                self._qtable.setItem(row, self._COL_RETRY, date_item)
            elif entry.status == "resolved" and entry.resolved_at is not None:
                from datetime import datetime, timezone
                date_str = datetime.fromtimestamp(
                    entry.resolved_at, tz=timezone.utc
                ).strftime("%Y-%m-%d")
                date_item = QTableWidgetItem(date_str)
                date_item.setFlags(flags_ro)
                _grey_out(date_item)
                self._qtable.setItem(row, self._COL_RETRY, date_item)
            else:
                dash_item = QTableWidgetItem("—")
                dash_item.setFlags(flags_ro)
                self._qtable.setItem(row, self._COL_RETRY, dash_item)

            # Last error (read-only, truncated)
            err_item = QTableWidgetItem((entry.last_error or "")[:80])
            err_item.setFlags(flags_ro)
            if not editable:
                _grey_out(err_item)
            self._qtable.setItem(row, self._COL_ERROR, err_item)

    # ── Dump ──────────────────────────────────────────────────────────────

    def dump(self) -> AurConfig:
        return AurConfig(
            enabled=self._enabled.isChecked(),
            skip=self._skip.isChecked(),
            helper_preference=_lines_to_tuple(self._helper_preference.toPlainText()),
            quarantine_enabled=self._quarantine_enabled.isChecked(),
            quarantine_min_failures=self._quarantine_min_failures.value(),
            quarantine_initial_days=self._quarantine_initial_days.value(),
            quarantine_max_days=self._quarantine_max_days.value(),
        )

    def save_extra(self, cfg: ConfigModel) -> None:
        """Write quarantine table edits to the state JSON."""
        from archward.aur.quarantine import AurQuarantine
        import time as _time
        q = AurQuarantine(cfg.aur)
        q.load()

        for row in range(self._qtable.rowCount()):
            pkg_item = self._qtable.item(row, self._COL_PKG)
            if pkg_item is None:
                continue
            pkg = pkg_item.text()

            # Status (might be a combo or a plain item)
            status_widget = self._qtable.cellWidget(row, self._COL_STATUS)
            if isinstance(status_widget, QComboBox):
                new_status = status_widget.currentText()
            else:
                status_item = self._qtable.item(row, self._COL_STATUS)
                new_status = status_item.text() if status_item else ""

            # Failure count
            fail_item = self._qtable.item(row, self._COL_FAILS)
            try:
                new_count = int(fail_item.text()) if fail_item else None
            except ValueError:
                new_count = None

            # Retry After (date string → timestamp)
            retry_item = self._qtable.item(row, self._COL_RETRY)
            new_retry: float | None = None
            if retry_item and retry_item.text() not in ("—", ""):
                try:
                    from datetime import datetime, timezone
                    dt = datetime.strptime(retry_item.text(), "%Y-%m-%d").replace(
                        tzinfo=timezone.utc
                    )
                    new_retry = dt.timestamp()
                except ValueError:
                    pass

            patch: dict = {}
            if new_status:
                patch["status"] = new_status
            if new_count is not None:
                patch["failure_count"] = new_count
            if new_status == "quarantined" and new_retry is not None:
                patch["retry_after"] = new_retry
            if patch:
                q.update_entry(pkg, patch)

        q.save()

    # ── Button slots ──────────────────────────────────────────────────────

    def _on_clear_selected(self) -> None:
        for item in self._qtable.selectedItems():
            row = item.row()
            status_widget = self._qtable.cellWidget(row, self._COL_STATUS)
            if isinstance(status_widget, QComboBox):
                status_widget.setCurrentText("resolved")

    def _on_clear_resolved(self) -> None:
        rows_to_remove = []
        for row in range(self._qtable.rowCount()):
            status_widget = self._qtable.cellWidget(row, self._COL_STATUS)
            if isinstance(status_widget, QComboBox):
                if status_widget.currentText() == "resolved":
                    rows_to_remove.append(row)
            else:
                item = self._qtable.item(row, self._COL_STATUS)
                if item and item.text() == "resolved":
                    rows_to_remove.append(row)
        for row in reversed(rows_to_remove):
            self._qtable.removeRow(row)

    def _on_clear_all(self) -> None:
        for row in range(self._qtable.rowCount()):
            status_widget = self._qtable.cellWidget(row, self._COL_STATUS)
            if isinstance(status_widget, QComboBox):
                status_widget.setCurrentText("resolved")
