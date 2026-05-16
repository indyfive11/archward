"""archward Preferences dialog.

Tabs (one Pydantic sub-model per tab):
  General · Gates · Risk · Services · Pacnew · AUR · Pacman · Verify · Privilege · Advanced

Edit flow:
  1. Dialog opens with the current ConfigModel loaded into widgets.
  2. User edits in any tab; changes stay in the widgets (not persisted).
  3. Save → validate via Pydantic → write to ~/.config/archward/config.toml.
  4. Cancel → discard changes.

All config is editable in-place; the Advanced tab still ships an
"Open config.toml" shortcut for users who prefer their editor for bulk
edits, but every field has a dedicated widget in the dialog.
"""

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


# ── Small helpers ────────────────────────────────────────────────────────


def _lines_to_tuple(text: str) -> tuple[str, ...]:
    return tuple(line.strip() for line in text.splitlines() if line.strip())


def _tuple_to_lines(items) -> str:
    return "\n".join(items)


def _grey_out(item: QTableWidgetItem) -> None:
    """Apply a greyed-out foreground to a read-only table item."""
    item.setForeground(QColor("#888888"))


def _open_in_editor(parent: QWidget, path: Path) -> None:
    """Open `path` in the user's preferred GUI editor.

    Priority order:
      1. `$VISUAL` if explicitly set — semantically the "GUI-capable editor".
      2. `xdg-open` — routes through freedesktop mime associations
         (Kate/gedit/code/etc. depending on the user's setup).
      3. `$EDITOR` as a last resort.

    Why not just $EDITOR? `$EDITOR` traditionally points at a terminal
    editor (nvim/vim/nano). Spawning a terminal editor via QProcess
    without a TTY produces no visible window — the process exits
    immediately. xdg-open is the freedesktop primitive for "open file
    in the user's default app" and is what KDE/GNOME/etc. honor.

    Shared by the Advanced and Profiles tabs so the open-in-editor
    affordance behaves identically regardless of which file the user
    points it at.
    """
    candidates: list[str] = []
    if os.environ.get("VISUAL"):
        candidates.append(os.environ["VISUAL"])
    candidates.append("xdg-open")
    if os.environ.get("EDITOR"):
        candidates.append(os.environ["EDITOR"])

    for cmd in candidates:
        try:
            subprocess.Popen([cmd, str(path)])
            return
        except FileNotFoundError:
            continue
    QMessageBox.warning(
        parent,
        "No editor available",
        "Couldn't find xdg-open, $VISUAL, or $EDITOR. Install xdg-utils "
        "or set $VISUAL to a GUI editor (e.g. kate, gedit, code).",
    )


# ── Tab base ─────────────────────────────────────────────────────────────


class _Tab(QWidget):
    """Common interface: tabs read from a ConfigModel on load() and produce
    overrides on dump()."""

    section: str = ""  # ConfigModel attribute this tab edits

    def load(self, cfg: ConfigModel) -> None:
        raise NotImplementedError

    def dump(self):
        """Return a Pydantic sub-model representing this tab's current state."""
        raise NotImplementedError

    def save_extra(self, cfg: ConfigModel) -> None:
        """Optional: called after config is saved, for tab state that lives
        outside the config model (e.g. separate JSON state files)."""


# ── Individual tabs ──────────────────────────────────────────────────────


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


_STALE_LIBS_SUDOERS_PATH = Path("/etc/sudoers.d/archward-stale-libs")
_STALE_LIBS_SCAN_SCRIPT = Path("/usr/share/archward/stale_libs_scan")
_STALE_LIBS_SUDOERS_LINE = (
    f"# Managed by archward — allows full stale-library scan without password prompt.\n"
    f"# Remove this file to restrict stale-libs check to user-visible processes only.\n"
    f"%wheel ALL=(root) NOPASSWD: /usr/bin/python3 {_STALE_LIBS_SCAN_SCRIPT}\n"
)


class _VerifyTab(_Tab):
    section = "verify"

    def __init__(self) -> None:
        super().__init__()
        self._enabled = QCheckBox("Enable verify phase")
        self._security_advisories = QCheckBox("Check Arch Security Advisories")
        self._stale_libs = QCheckBox("Detect stale library versions after update")
        self._stale_libs_sudo_btn = QPushButton()
        self._reboot_log = QLineEdit()
        self._reboot_log.setPlaceholderText("/var/log/reboot-recommendation-trigger.log")

        stale_row = QHBoxLayout()
        stale_row.setContentsMargins(0, 0, 0, 0)
        stale_row.addWidget(self._stale_libs)
        stale_row.addSpacing(12)
        stale_row.addWidget(self._stale_libs_sudo_btn)
        stale_row.addStretch()

        form = QFormLayout(self)
        form.addRow("", _field_with_help(self._enabled, "verify", "enabled"))
        form.addRow("", _field_with_help(self._security_advisories, "verify", "security_advisories"))
        form.addRow("", _field_with_help(_wrap(stale_row), "verify", "stale_libs"))
        form.addRow("Reboot-recommended log:",
                    _field_with_help(self._reboot_log, "verify", "reboot_log"))

        self._stale_libs_sudo_btn.clicked.connect(self._toggle_sudoers)
        self._refresh_sudo_btn()

    def _sudoers_active(self) -> bool:
        return _STALE_LIBS_SUDOERS_PATH.exists()

    def _refresh_sudo_btn(self) -> None:
        if self._sudoers_active():
            self._stale_libs_sudo_btn.setText("Full coverage enabled ✓")
            self._stale_libs_sudo_btn.setToolTip(
                f"sudoers entry active at {_STALE_LIBS_SUDOERS_PATH}.\n"
                "Click to remove (reverts to user-visible scan only)."
            )
        else:
            self._stale_libs_sudo_btn.setText("Enable full coverage…")
            self._stale_libs_sudo_btn.setToolTip(
                "Adds a sudoers entry so archward can scan system services\n"
                "(sshd, NetworkManager, etc.) without a password prompt.\n"
                f"Writes: {_STALE_LIBS_SUDOERS_PATH}"
            )

    def _toggle_sudoers(self) -> None:
        from archward.app import build_sudo_strategy
        from archward.config.loader import load_config
        from archward.pacman.runner import run_capture

        if self._sudoers_active():
            confirm = QMessageBox.question(
                self,
                "Remove sudoers entry",
                f"Remove {_STALE_LIBS_SUDOERS_PATH}?\n\n"
                "The stale-libs check will revert to scanning user-visible\n"
                "processes only (KDE/Plasma, pipewire, browsers).",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if confirm != QMessageBox.StandardButton.Yes:
                return
            strategy = build_sudo_strategy(load_config())
            code, _out, err = run_capture(
                ["rm", str(_STALE_LIBS_SUDOERS_PATH)], strategy=strategy,
            )
            if code != 0:
                QMessageBox.critical(
                    self, "Remove failed",
                    f"Could not remove {_STALE_LIBS_SUDOERS_PATH}:\n{err.strip()}",
                )
                return
            QMessageBox.information(self, "Removed", "sudoers entry removed.")
        else:
            preview = (
                "Enable full stale-library coverage?\n\n"
                "archward will write (via sudo / askpass):\n\n"
                f"  {_STALE_LIBS_SUDOERS_PATH}\n\n"
                "Contents:\n"
                f"{_STALE_LIBS_SUDOERS_LINE}\n"
                "This allows archward to read /proc/<pid>/maps for all running\n"
                "processes without a password prompt, so system services like\n"
                "sshd and NetworkManager are included in the stale-libs check.\n\n"
                "Proceed?"
            )
            if QMessageBox.question(
                self, "Enable full coverage", preview,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            ) != QMessageBox.StandardButton.Yes:
                return
            strategy = build_sudo_strategy(load_config())
            code, _out, err = run_capture(
                ["tee", str(_STALE_LIBS_SUDOERS_PATH)],
                strategy=strategy,
                input_text=_STALE_LIBS_SUDOERS_LINE,
            )
            if code != 0:
                QMessageBox.critical(
                    self, "Write failed",
                    f"Could not write {_STALE_LIBS_SUDOERS_PATH}:\n{err.strip()}",
                )
                return
            # Lock down permissions (sudoers.d files must be 0440)
            run_capture(
                ["chmod", "0440", str(_STALE_LIBS_SUDOERS_PATH)], strategy=strategy,
            )
            QMessageBox.information(
                self, "Enabled",
                "Full stale-library coverage enabled.\n"
                f"sudoers entry written to {_STALE_LIBS_SUDOERS_PATH}.",
            )

        self._refresh_sudo_btn()

    def load(self, cfg: ConfigModel) -> None:
        self._enabled.setChecked(cfg.verify.enabled)
        self._security_advisories.setChecked(cfg.verify.security_advisories)
        self._stale_libs.setChecked(cfg.verify.stale_libs)
        self._reboot_log.setText(cfg.verify.reboot_log)
        self._refresh_sudo_btn()

    def dump(self) -> VerifyConfig:
        return VerifyConfig(
            enabled=self._enabled.isChecked(),
            security_advisories=self._security_advisories.isChecked(),
            stale_libs=self._stale_libs.isChecked(),
            reboot_log=self._reboot_log.text().strip(),
        )


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


# ── Cache tab (v0.4.4) ───────────────────────────────────────────────────


class _CacheTab(QWidget):
    """Pacman cache-policy awareness + previewed-sudo preset apply.

    Not a `_Tab` — it manages *system* policy
    (/etc/conf.d/pacman-contrib + paccache.timer), not config.toml, so
    it has no load()/dump(). It detects the live policy, shows a
    rollback-safety verdict, and applies environment presets through
    the same run_capture + SudoStrategy path rollback/pacnew use, with
    the exact commands shown for confirmation first.
    """

    def __init__(self, cfg: ConfigModel) -> None:
        super().__init__()
        self._cfg = cfg

        intro = _section_help("cache")
        refresh_btn = QPushButton("Re-scan cache policy")
        refresh_btn.clicked.connect(self._render)

        # A container we rebuild on every (re-)scan.
        self._body = QWidget()
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(0, 0, 0, 0)

        layout = QVBoxLayout(self)
        if intro is not None:
            layout.addWidget(intro)
        layout.addWidget(refresh_btn)
        layout.addWidget(self._body, stretch=1)

        self._render()

    # ── render ─────────────────────────────────────────────────────────

    def _clear_body(self) -> None:
        while self._body_layout.count():
            item = self._body_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _render(self) -> None:
        from archward.system import cache_policy as cp
        from archward.ui.theme import brand_palette, status_palette

        self._clear_body()
        pol = cp.detect_cache_policy()

        sp = status_palette()
        brand = brand_palette()
        # Verdict → (bg, fg).
        verdict_colors = {
            cp.RollbackSafety.DANGEROUS: (sp.danger_bg, sp.danger_fg),
            cp.RollbackSafety.TIGHT: (sp.info_bg, sp.info_fg),
            cp.RollbackSafety.BALANCED: (sp.success_bg, sp.success_fg),
            cp.RollbackSafety.GENEROUS: (sp.success_bg, sp.success_fg),
            cp.RollbackSafety.UNMANAGED: (sp.info_bg, sp.info_fg),
        }
        bg, fg = verdict_colors.get(pol.safety, (sp.neutral_bg, sp.neutral_fg))

        banner = QLabel(
            f"<b>Rollback safety: {pol.safety.value.upper()}</b><br>{pol.explanation}"
        )
        banner.setWordWrap(True)
        banner.setStyleSheet(
            f"background: {bg}; color: {fg}; padding: 10px; "
            f"border-left: 4px solid {brand.accent_border};"
        )
        self._body_layout.addWidget(banner)

        size_mib = pol.cache_size_bytes / (1024 * 1024)
        hooks_txt = (
            ", ".join(h.name for h in pol.cleaning_hooks)
            if pol.cleaning_hooks else "(none — good)"
        )
        panel = QLabel(
            "<table cellpadding='3'>"
            f"<tr><td><b>paccache.timer:</b></td><td>{pol.timer_state}</td></tr>"
            f"<tr><td><b>PACCACHE_ARGS:</b></td><td><code>{pol.paccache_args or '(unset)'}</code></td></tr>"
            f"<tr><td><b>Effective keep:</b></td><td>{pol.effective_keep} version(s)</td></tr>"
            f"<tr><td><b>CleanMethod:</b></td><td>{', '.join(pol.clean_method)}</td></tr>"
            f"<tr><td><b>Cleaning hooks:</b></td><td>{hooks_txt}</td></tr>"
            f"<tr><td><b>Cache size:</b></td><td>{size_mib:,.0f} MiB, "
            f"{pol.cache_file_count} package file(s)</td></tr>"
            "</table>"
        )
        panel.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._body_layout.addWidget(panel)

        if pol.cleaning_hooks:
            warn = _help_label(
                "A cache-cleaning hook runs inside the pacman transaction. "
                "archward will NOT auto-delete it (it may belong to another "
                "package). Review/remove it yourself: "
                + ", ".join(str(h) for h in pol.cleaning_hooks)
            )
            self._body_layout.addWidget(warn)

        self._body_layout.addWidget(_lbl("Apply an environment preset:"))
        for preset in cp.CACHE_PRESETS:
            btn = QPushButton(f"{preset.label}  —  {preset.paccache_args}"
                              f"{' + timer' if preset.enable_timer else ' (no timer)'}")
            btn.setToolTip(preset.description)
            btn.clicked.connect(lambda _checked=False, p=preset: self._apply_preset(p))
            self._body_layout.addWidget(btn)

        # Custom keep-N row.
        custom_row = QHBoxLayout()
        custom_row.addWidget(_lbl("Custom: keep"))
        self._custom_keep = QSpinBox()
        self._custom_keep.setRange(1, 99)
        self._custom_keep.setValue(max(pol.effective_keep, 1))
        custom_row.addWidget(self._custom_keep)
        custom_row.addWidget(_lbl("versions, timer enabled"))
        custom_btn = QPushButton("Apply custom")
        custom_btn.clicked.connect(self._apply_custom)
        custom_row.addWidget(custom_btn)
        custom_row.addStretch(1)
        self._body_layout.addLayout(custom_row)
        self._body_layout.addStretch(1)

    # ── apply ──────────────────────────────────────────────────────────

    def _confirm_and_run(self, label: str, paccache_args: str, enable_timer: bool) -> None:
        from archward.app import build_sudo_strategy
        from archward.pacman.runner import run_capture
        from archward.system import cache_policy as cp

        conf_content = (
            "# Managed by archward (Cache tab).\n"
            f"PACCACHE_ARGS='{paccache_args}'\n"
        )
        timer_verb = "enable --now" if enable_timer else "disable --now"
        preview = (
            f"Apply the '{label}' cache policy?\n\n"
            f"archward will run (via sudo / askpass):\n\n"
            f"  1. write /etc/conf.d/pacman-contrib:\n"
            f"       PACCACHE_ARGS='{paccache_args}'\n"
            f"  2. sudo systemctl {timer_verb} paccache.timer\n\n"
            "These are system-level changes. Proceed?"
        )
        if QMessageBox.question(
            self, "Confirm cache policy", preview,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return

        strategy = build_sudo_strategy(self._cfg)
        code, _out, err = run_capture(
            ["tee", str(cp._CONF_D)], strategy=strategy, input_text=conf_content,
        )
        if code != 0:
            QMessageBox.critical(
                self, "Write failed",
                f"Could not write /etc/conf.d/pacman-contrib:\n{err.strip()}",
            )
            return

        verb = ["enable", "--now"] if enable_timer else ["disable", "--now"]
        code, _out, err = run_capture(
            ["systemctl", *verb, "paccache.timer"], strategy=strategy,
        )
        if code != 0:
            QMessageBox.warning(
                self, "Timer toggle failed",
                "PACCACHE_ARGS was written, but toggling paccache.timer "
                f"failed:\n{err.strip()}",
            )
        else:
            QMessageBox.information(
                self, "Applied", f"Cache policy '{label}' applied.",
            )
        self._render()

    def _apply_preset(self, preset) -> None:
        self._confirm_and_run(preset.label, preset.paccache_args, preset.enable_timer)

    def _apply_custom(self) -> None:
        n = self._custom_keep.value()
        self._confirm_and_run(f"custom keep {n}", f"-rk{n}", enable_timer=True)


# ── Profiles tab ─────────────────────────────────────────────────────────


# Sentinel data on the (default) row's QListWidgetItem — distinguishes the
# default config.toml from named profiles without comparing paths.
_DEFAULT_ROLE = Qt.ItemDataRole.UserRole + 1


class _ProfilesTab(QWidget):
    """Profile switcher + manager.

    Not a `_Tab` — has no load()/dump(). Signals up to PreferencesDialog,
    which handles dirty-check on switch and refreshes its own state.
    """

    profile_switch_requested = Signal(object)   # Path | None
    profile_created = Signal(str)               # profile name
    profile_renamed = Signal(object, object)    # (old_path, new_path)
    profile_deleted = Signal(object)            # Path

    def __init__(self, config_path: Path | None = None, parent=None) -> None:
        super().__init__(parent)
        self._active_path: Path | None = config_path  # None == default config.toml

        self._list = QListWidget()
        self._list.itemSelectionChanged.connect(self._update_button_states)
        self._list.itemDoubleClicked.connect(lambda _i: self._on_switch())

        self._switch_btn = QPushButton("Switch to selected")
        self._switch_btn.setToolTip(
            "Reload the window against the selected profile. Unsaved edits "
            "in other tabs will prompt to Save / Discard / Cancel."
        )
        self._switch_btn.clicked.connect(self._on_switch)

        self._open_btn = QPushButton("Open in editor")
        self._open_btn.setToolTip("Open the selected profile in $EDITOR / xdg-open.")
        self._open_btn.clicked.connect(self._on_open)

        self._new_defaults_btn = QPushButton("New from defaults…")
        self._new_defaults_btn.setToolTip(
            "Create a new profile pre-populated with archward defaults."
        )
        self._new_defaults_btn.clicked.connect(self._on_new_from_defaults)

        self._save_as_btn = QPushButton("Save current as new…")
        self._save_as_btn.setToolTip(
            "Snapshot the current dialog state into a new profile file. "
            "Does not switch to it."
        )
        self._save_as_btn.clicked.connect(self._on_save_as)

        self._rename_btn = QPushButton("Rename…")
        self._rename_btn.setToolTip(
            "Rename the selected profile file. The default config cannot be renamed."
        )
        self._rename_btn.clicked.connect(self._on_rename)

        self._delete_btn = QPushButton("Delete…")
        self._delete_btn.setToolTip(
            "Delete the selected profile file. The active profile and the "
            "default config cannot be deleted."
        )
        self._delete_btn.clicked.connect(self._on_delete)

        self._diff_btn = QPushButton("Diff vs default…")
        self._diff_btn.setToolTip(
            "Show a unified diff of the selected profile against archward "
            "defaults. Read-only — useful for 'what does this profile "
            "actually change?'"
        )
        self._diff_btn.clicked.connect(self._on_diff_vs_default)

        self._import_btn = QPushButton("Import…")
        self._import_btn.setToolTip(
            "Load a profile .toml from anywhere on disk. The file is "
            "validated, then copied into ~/.config/archward/profiles/ "
            "under a name you choose."
        )
        self._import_btn.clicked.connect(self._on_import)

        self._export_btn = QPushButton("Export…")
        self._export_btn.setToolTip(
            "Copy the selected profile to a chosen location for "
            "sharing or backup."
        )
        self._export_btn.clicked.connect(self._on_export)

        # Three-row button grid: primary actions on top, manage-data
        # actions below. Diff sits next to Open in editor because both
        # are inspect-only.
        btn_row1 = QHBoxLayout()
        btn_row1.addWidget(self._switch_btn)
        btn_row1.addWidget(self._open_btn)
        btn_row1.addWidget(self._diff_btn)
        btn_row2 = QHBoxLayout()
        btn_row2.addWidget(self._new_defaults_btn)
        btn_row2.addWidget(self._save_as_btn)
        btn_row3 = QHBoxLayout()
        btn_row3.addWidget(self._import_btn)
        btn_row3.addWidget(self._export_btn)
        btn_row4 = QHBoxLayout()
        btn_row4.addWidget(self._rename_btn)
        btn_row4.addWidget(self._delete_btn)

        self._summary = _help_label("")
        self._hint = _help_label(
            "Switching reloads the window against the selected profile. "
            "Refused while a pipeline is running."
        )

        # Remember-last-used toggle backed by QSettings (independent of the
        # active profile's config.toml — it's GUI session state).
        from archward.ui import persistent_state as _ps  # local: PySide6-dependent
        self._remember_last = QCheckBox("Remember last-used profile across launches")
        self._remember_last.setChecked(_ps.get_remember_last_profile())
        self._remember_last.toggled.connect(self._on_remember_toggled)

        layout = QVBoxLayout(self)
        section_help = _section_help("profiles")
        if section_help is not None:
            layout.addWidget(section_help)
        layout.addWidget(self._list, 1)
        layout.addLayout(btn_row1)
        layout.addLayout(btn_row2)
        layout.addLayout(btn_row3)
        layout.addLayout(btn_row4)
        layout.addWidget(self._summary)
        layout.addWidget(self._hint)
        layout.addWidget(self._remember_last)
        remember_help = _help_label(help_text.get("profiles", "remember_last_used"))
        if remember_help.text():
            layout.addWidget(remember_help)

        self.refresh_list(self._active_path)

    def _on_remember_toggled(self, checked: bool) -> None:
        from archward.ui import persistent_state as _ps
        _ps.set_remember_last_profile(checked)
        if not checked:
            # Drop the stored path so a later re-enable doesn't read a
            # stale value from a profile the user may have since deleted.
            _ps.clear_last_used_profile_path()
        else:
            # Seed with the currently-active profile so the next launch
            # without --profile actually reopens what's open now.
            _ps.set_last_used_profile_path(self._active_path)

    # ── Public API ────────────────────────────────────────────────────────

    def set_active(self, config_path: Path | None) -> None:
        """Update which row carries the active marker; re-render."""
        self._active_path = config_path
        self.refresh_list(self._active_path)

    def refresh_list(self, active: Path | None) -> None:
        """Rebuild the list from disk; preserve active marker; restore selection."""
        self._active_path = active
        prev_selected_path = self._selected_path()

        self._list.clear()

        # Row 0 is always the default config (pseudo-profile).
        default_path = default_config_path()
        default_item = QListWidgetItem(
            self._format_row(name="(default)", path=default_path, is_active=(active is None))
        )
        default_item.setData(Qt.ItemDataRole.UserRole, default_path)
        default_item.setData(_DEFAULT_ROLE, True)
        self._list.addItem(default_item)

        for name in config_paths.iter_profiles():
            p = config_paths.profile_config_path(name)
            is_active = (active is not None and p == active)
            item = QListWidgetItem(self._format_row(name=name, path=p, is_active=is_active))
            item.setData(Qt.ItemDataRole.UserRole, p)
            item.setData(_DEFAULT_ROLE, False)
            self._list.addItem(item)

        # Restore selection (prefer previous; otherwise select the active row).
        target = prev_selected_path or (active if active is not None else default_path)
        for i in range(self._list.count()):
            if self._list.item(i).data(Qt.ItemDataRole.UserRole) == target:
                self._list.setCurrentRow(i)
                break
        else:
            self._list.setCurrentRow(0)

        self._update_summary()
        self._update_button_states()

    # ── Helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _format_row(*, name: str, path: Path, is_active: bool) -> str:
        marker = "★ " if is_active else "  "
        return f"{marker}{name}    {path}"

    def _selected_item(self) -> QListWidgetItem | None:
        items = self._list.selectedItems()
        return items[0] if items else None

    def _selected_path(self) -> Path | None:
        item = self._selected_item()
        if item is None:
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    def _selected_is_default(self) -> bool:
        item = self._selected_item()
        return bool(item and item.data(_DEFAULT_ROLE))

    def _selected_is_active(self) -> bool:
        sel = self._selected_path()
        if sel is None:
            return False
        if self._active_path is None:
            return self._selected_is_default()
        return sel == self._active_path

    def _update_summary(self) -> None:
        if self._active_path is None:
            self._summary.setText(f"Active: (default) — {default_config_path()}")
        else:
            self._summary.setText(
                f"Active: {self._active_path.stem} — {self._active_path}"
            )

    def _update_button_states(self) -> None:
        item_selected = self._selected_item() is not None
        is_default = self._selected_is_default()
        is_active = self._selected_is_active()
        self._switch_btn.setEnabled(item_selected and not is_active)
        self._open_btn.setEnabled(item_selected)
        # Diff vs default: only meaningful for named profiles (the default
        # row would diff against itself and show nothing).
        self._diff_btn.setEnabled(item_selected and not is_default)
        self._export_btn.setEnabled(item_selected and not is_default)
        self._rename_btn.setEnabled(item_selected and not is_default)
        self._delete_btn.setEnabled(item_selected and not is_default and not is_active)

    # ── Action slots ──────────────────────────────────────────────────────

    def _on_switch(self) -> None:
        item = self._selected_item()
        if item is None or self._selected_is_active():
            return
        target = None if item.data(_DEFAULT_ROLE) else item.data(Qt.ItemDataRole.UserRole)
        self.profile_switch_requested.emit(target)

    def _on_open(self) -> None:
        path = self._selected_path()
        if path is not None:
            _open_in_editor(self, path)

    def _on_new_from_defaults(self) -> None:
        name = self._prompt_for_new_name("New profile (from defaults)")
        if name is None:
            return
        try:
            path = config_paths.profile_config_path(name)
        except ValueError as e:
            QMessageBox.warning(self, "Invalid name", str(e))
            return
        try:
            write_config(default_config(), path)
        except OSError as e:
            QMessageBox.critical(self, "Create failed", f"Could not write {path}:\n{e}")
            return
        self.profile_created.emit(name)
        self.refresh_list(self._active_path)
        self._select_path(path)

    def _on_save_as(self) -> None:
        # Defer to the parent dialog so it can build the draft via _build_draft.
        # The actual write happens in the dialog's slot to keep all validation
        # and tab-orchestration logic there.
        name = self._prompt_for_new_name("Save current state as new profile")
        if name is None:
            return
        # Emit a sentinel: profile_created with a leading "@save-as:" prefix
        # would be a hack. Cleaner: dedicated signal.
        self.save_current_as_requested.emit(name)

    save_current_as_requested = Signal(str)  # profile name

    def _on_rename(self) -> None:
        if self._selected_is_default():
            return
        old_path = self._selected_path()
        if old_path is None:
            return
        new_name = self._prompt_for_new_name(
            "Rename profile",
            default=old_path.stem,
        )
        if new_name is None or new_name == old_path.stem:
            return
        try:
            new_path = config_paths.profile_config_path(new_name)
        except ValueError as e:
            QMessageBox.warning(self, "Invalid name", str(e))
            return
        try:
            old_path.rename(new_path)
        except OSError as e:
            QMessageBox.critical(self, "Rename failed", f"Could not rename:\n{e}")
            return
        self.profile_renamed.emit(old_path, new_path)
        # If active was renamed, the parent will update _active_path and
        # then call refresh; until then, optimistically update locally.
        if self._active_path == old_path:
            self._active_path = new_path
        self.refresh_list(self._active_path)
        self._select_path(new_path)

    def _on_delete(self) -> None:
        if self._selected_is_default() or self._selected_is_active():
            return
        path = self._selected_path()
        if path is None:
            return
        button = QMessageBox.question(
            self,
            "Delete profile",
            f"Delete profile {path.stem!r}?\n\n{path}",
        )
        if button != QMessageBox.StandardButton.Yes:
            return
        try:
            path.unlink()
        except OSError as e:
            QMessageBox.critical(self, "Delete failed", f"Could not delete {path}:\n{e}")
            return
        self.profile_deleted.emit(path)
        self.refresh_list(self._active_path)

    def _on_diff_vs_default(self) -> None:
        """Render a unified diff of the selected profile against defaults."""
        if self._selected_is_default():
            return
        path = self._selected_path()
        if path is None:
            return
        from archward.config.defaults import default_config
        from archward.config.diff import unified_diff
        from archward.config.loader import load_config
        from archward.ui.dialogs.diff_dialog import TextDiffDialog

        try:
            profile_cfg = load_config(path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Diff failed", f"Could not load {path}:\n{e}")
            return
        diff_lines = unified_diff(
            default_config(), profile_cfg,
            a_label="defaults", b_label=path.stem,
        )
        dlg = TextDiffDialog(
            diff_text="".join(diff_lines),
            title=f"archward — {path.stem} vs defaults",
            header_html=(
                f"<b>Profile:</b> {path.stem}   <b>File:</b> {path}<br>"
                f"<b>Comparison:</b> archward defaults → this profile"
            ),
            parent=self,
        )
        dlg.exec()

    def _on_import(self) -> None:
        """Pick a .toml file from anywhere, validate, copy into profile_dir."""
        from archward.config.loader import load_config

        src_str, _ = QFileDialog.getOpenFileName(
            self,
            "Import profile",
            str(Path.home()),
            "TOML files (*.toml);;All files (*)",
        )
        if not src_str:
            return
        src = Path(src_str)

        # Validate by attempting to parse the TOML through the config loader.
        # Per-section validation errors fall back to defaults, but a wholly
        # unreadable file gets logged and we can surface a clearer message.
        try:
            load_config(src)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(
                self, "Import failed",
                f"Could not parse {src} as an archward config:\n{e}",
            )
            return

        # Default the new profile name to the source file's stem so users
        # who exported and re-import a roundtrip get a sensible default.
        suggested = src.stem if config_paths.valid_profile_name(src.stem) else ""
        name = self._prompt_for_new_name("Import profile — choose a name", default=suggested)
        if name is None:
            return
        try:
            target = config_paths.profile_config_path(name)
        except ValueError as e:
            QMessageBox.warning(self, "Invalid name", str(e))
            return

        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            import shutil
            shutil.copyfile(src, target)
        except OSError as e:
            QMessageBox.critical(self, "Import failed", f"Could not copy to {target}:\n{e}")
            return

        log.info("imported profile %s → %s", src, target)
        self.profile_created.emit(name)
        self.refresh_list(self._active_path)
        self._select_path(target)

    def _on_export(self) -> None:
        """Copy the selected profile to a chosen filesystem location."""
        if self._selected_is_default():
            return
        src = self._selected_path()
        if src is None:
            return
        default_dest = str(Path.home() / f"{src.stem}.toml")
        dest_str, _ = QFileDialog.getSaveFileName(
            self,
            f"Export profile {src.stem}",
            default_dest,
            "TOML files (*.toml);;All files (*)",
        )
        if not dest_str:
            return
        dest = Path(dest_str)
        try:
            import shutil
            shutil.copyfile(src, dest)
        except OSError as e:
            QMessageBox.critical(self, "Export failed", f"Could not write {dest}:\n{e}")
            return
        log.info("exported profile %s → %s", src, dest)
        QMessageBox.information(
            self, "Profile exported",
            f"Wrote {src.stem} to:\n{dest}",
        )

    # ── Sub-prompts ───────────────────────────────────────────────────────

    def _prompt_for_new_name(self, title: str, *, default: str = "") -> str | None:
        """Prompt for a profile name; loop until valid + non-colliding or canceled."""
        text = default
        while True:
            name, ok = QInputDialog.getText(
                self,
                title,
                "Profile name (letters, digits, _ and -; must start alphanumeric):",
                text=text,
            )
            if not ok:
                return None
            name = name.strip()
            if not config_paths.valid_profile_name(name):
                QMessageBox.warning(
                    self,
                    "Invalid name",
                    f"{name!r} is not a valid profile name. Use letters, digits, "
                    "underscore, or dash; must start with a letter or digit; "
                    "max 64 characters.",
                )
                text = name
                continue
            target = config_paths.profile_config_path(name)
            if target.exists() and name != default:
                QMessageBox.warning(
                    self,
                    "Already exists",
                    f"A profile named {name!r} already exists at:\n{target}",
                )
                text = name
                continue
            return name

    def _select_path(self, path: Path) -> None:
        for i in range(self._list.count()):
            if self._list.item(i).data(Qt.ItemDataRole.UserRole) == path:
                self._list.setCurrentRow(i)
                return


# ── Dialog ───────────────────────────────────────────────────────────────


class PreferencesDialog(QDialog):
    """Modal preferences editor."""

    config_saved = Signal(object)  # ConfigModel — emitted after Save succeeds
    profile_switch_requested = Signal(object)  # Path | None — relayed up to MainWindow

    def __init__(
        self,
        cfg: ConfigModel,
        config_path: Path | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._set_title_for_path(config_path)
        self.resize(900, 700)

        self._cfg = cfg
        self._config_path = config_path

        self._tabs: list[_Tab] = [
            _GeneralTab(),
            _GatesTab(),
            _RiskTab(),
            _ServicesTab(),
            _PacnewTab(),
            _AurTab(),
            _PacmanTab(),
            _VerifyTab(),
            _PrivilegeTab(),
            _HooksTab(),
        ]
        labels = [
            "General",
            "Gates",
            "Risk",
            "Services",
            "Pacnew",
            "AUR",
            "Pacman",
            "Verify",
            "Privilege",
            "Hooks",
        ]
        self._advanced = _AdvancedTab(config_path=config_path)
        self._advanced.redetect_requested.connect(self._on_redetect)
        self._advanced.reset_requested.connect(self._on_reset)

        self._cache = _CacheTab(cfg)

        self._profiles = _ProfilesTab(config_path=config_path)
        self._profiles.profile_switch_requested.connect(self._on_profile_switch)
        self._profiles.save_current_as_requested.connect(self._on_save_current_as)
        self._profiles.profile_renamed.connect(self._on_profile_renamed)
        # profile_created / profile_deleted are informational only — the tab
        # already refreshed its own list, and the dialog has nothing to do.

        # ── Sidebar + stacked content ──────────────────────────────────────
        self._sidebar = QListWidget()
        self._sidebar.setFixedWidth(175)
        self._sidebar.setSpacing(1)
        self._content = QStackedWidget()

        # Maps sidebar row index → _Tab (or None for non-config entries).
        self._sidebar_row_to_tab: dict[int, _Tab | None] = {}
        # Maps sidebar row index → sidebar display label (for reset dialog).
        self._sidebar_row_to_label: dict[int, str] = {}
        # Keep _config_tab_indices as row→tab for existing helpers.
        self._config_tab_indices: dict[int, _Tab] = {}

        from archward.ui.theme import brand_palette
        brand = brand_palette()

        def _add_category(text: str) -> None:
            item = QListWidgetItem(text)
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            font = QFont()
            font.setBold(True)
            font.setPointSize(font.pointSize() - 1)
            item.setFont(font)
            item.setForeground(brand.accent_fg)
            item.setData(Qt.ItemDataRole.UserRole, "category")
            self._sidebar.addItem(item)
            row = self._sidebar.count() - 1
            self._sidebar_row_to_tab[row] = None

        def _add_entry(label: str, widget: QWidget, tab: _Tab | None = None) -> None:
            item = QListWidgetItem(f"  {label}")
            self._sidebar.addItem(item)
            row = self._sidebar.count() - 1
            self._content.addWidget(widget)
            self._sidebar_row_to_tab[row] = tab
            self._sidebar_row_to_label[row] = label
            if tab is not None:
                self._config_tab_indices[row] = tab

        def _add_separator() -> None:
            item = QListWidgetItem()
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            item.setSizeHint(item.sizeHint().__class__(0, 8))
            self._sidebar.addItem(item)
            row = self._sidebar.count() - 1
            self._sidebar_row_to_tab[row] = None

        tab_map = dict(zip(labels, self._tabs))

        _add_category("WORKFLOW")
        _add_entry("General",   tab_map["General"],   tab_map["General"])
        _add_entry("Gates",     tab_map["Gates"],     tab_map["Gates"])
        _add_category("PACKAGES")
        _add_entry("AUR",       tab_map["AUR"],       tab_map["AUR"])
        _add_entry("Pacman",    tab_map["Pacman"],    tab_map["Pacman"])
        _add_entry("Pacnew",    tab_map["Pacnew"],    tab_map["Pacnew"])
        _add_category("SAFETY")
        _add_entry("Risk",      tab_map["Risk"],      tab_map["Risk"])
        _add_entry("Verify",    tab_map["Verify"],    tab_map["Verify"])
        _add_entry("Privilege", tab_map["Privilege"], tab_map["Privilege"])
        _add_category("SYSTEM")
        _add_entry("Services",  tab_map["Services"],  tab_map["Services"])
        _add_entry("Hooks",     tab_map["Hooks"],     tab_map["Hooks"])
        _add_separator()
        _add_entry("Profiles",  self._profiles)
        _add_entry("Cache",     self._cache)
        _add_entry("Advanced",  self._advanced)

        self._sidebar.currentRowChanged.connect(self._on_sidebar_row_changed)

        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)
        body_layout.addWidget(self._sidebar)
        body_layout.addWidget(self._content, stretch=1)

        self._restore_tab_btn = QPushButton("Restore tab defaults")
        restore_all_btn = QPushButton("Restore all defaults")
        self._restore_tab_btn.clicked.connect(self._on_reset_current_tab)
        restore_all_btn.clicked.connect(self._on_reset)

        save_cancel = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        save_cancel.accepted.connect(self._on_save)
        save_cancel.rejected.connect(self.reject)

        btn_row = QHBoxLayout()
        btn_row.addWidget(self._restore_tab_btn)
        btn_row.addWidget(restore_all_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(save_cancel)

        layout = QVBoxLayout(self)
        layout.addWidget(body, stretch=1)
        layout.addLayout(btn_row)

        self._load_all()

        # Select the first selectable row (General).
        for row in range(self._sidebar.count()):
            item = self._sidebar.item(row)
            if item and (item.flags() & Qt.ItemFlag.ItemIsEnabled) and row in self._sidebar_row_to_label:
                self._sidebar.setCurrentRow(row)
                break
        self._on_sidebar_row_changed(self._sidebar.currentRow())

    # ── Tab orchestration ─────────────────────────────────────────────────

    def _load_all(self) -> None:
        for tab in self._tabs:
            tab.load(self._cfg)

    def _build_draft(self) -> ConfigModel:
        """Validate every tab and assemble an updated ConfigModel. Raises ValidationError."""
        overrides = {tab.section: tab.dump() for tab in self._tabs}
        return merge_partial(self._cfg, **overrides)

    # ── Button slots ──────────────────────────────────────────────────────

    def _on_save(self) -> None:
        try:
            new_cfg = self._build_draft()
        except ValidationError as e:
            QMessageBox.critical(
                self,
                "Invalid configuration",
                f"Validation failed:\n\n{e}",
            )
            return
        try:
            path = write_config(new_cfg, self._config_path)
        except OSError as e:
            target = self._config_path if self._config_path is not None else default_config_path()
            QMessageBox.critical(self, "Save failed", f"Could not write {target}:\n{e}")
            return
        self._cfg = new_cfg
        log.info("preferences saved to %s", path)
        # Let tabs persist any extra state (e.g. quarantine JSON) that lives
        # outside the config model.
        for tab in self._tabs:
            tab.save_extra(new_cfg)
        self.config_saved.emit(new_cfg)
        self.accept()

    def _on_redetect(self) -> None:
        # Build a draft from the current widgets so detection runs against the
        # in-progress edits, not just what's on disk.
        try:
            current = self._build_draft()
        except ValidationError:
            QMessageBox.warning(
                self,
                "Invalid configuration",
                "Fix validation errors in the other tabs before re-detecting.",
            )
            return

        det = run_full_detection()
        diff = diff_against(current, det)

        if (
            not diff.kernel_additions
            and not diff.service_additions
            and not diff.service_removals
            and not diff.aur_disable
        ):
            QMessageBox.information(
                self,
                "Re-detect",
                "Config already reflects the detected state — no changes proposed.",
            )
            return

        lines: list[str] = []
        if diff.kernel_additions:
            lines.append(f"+ risk.high: add {', '.join(diff.kernel_additions)}")
        if diff.service_additions:
            lines.append(
                f"+ services.to_verify: add {len(diff.service_additions)} service(s)"
            )
        if diff.service_removals:
            preview = ", ".join(diff.service_removals[:3])
            if len(diff.service_removals) > 3:
                preview += f", … (+{len(diff.service_removals) - 3} more)"
            lines.append(
                f"- services.to_verify: remove {len(diff.service_removals)} stale unit(s): {preview}"
            )
        if diff.aur_disable:
            lines.append("+ aur.enabled = false  (no AUR helper detected)")

        # The additions prompt and the removals prompt are independent so
        # the user can take one but skip the other — same axis-split as
        # the CLI's --detect.
        button = QMessageBox.question(
            self,
            "Re-detect — proposed changes",
            "\n".join(lines)
            + "\n\nApply additions (kernels, services, AUR) to the current draft? "
            "(You can still Cancel before Save.)",
        )
        accept_additions = button == QMessageBox.StandardButton.Yes

        accept_removals = False
        if diff.service_removals:
            r_button = QMessageBox.question(
                self,
                "Re-detect — remove stale services?",
                f"Drop {len(diff.service_removals)} stale unit(s) from services.to_verify?\n\n"
                "These units no longer resolve via `systemctl cat`. Removing them "
                "is opt-in so accidental unit-file moves don't silently drop entries.",
            )
            accept_removals = r_button == QMessageBox.StandardButton.Yes

        if not accept_additions and not accept_removals:
            return

        # Filter the diff so a "no" on either prompt actually drops those changes.
        from archward.config.detect import ConfigDiff as _CD
        effective = _CD(
            kernel_additions=diff.kernel_additions if accept_additions else (),
            service_additions=diff.service_additions if accept_additions else (),
            aur_disable=diff.aur_disable if accept_additions else False,
            helper_set_to=diff.helper_set_to,
            service_removals=diff.service_removals if accept_removals else (),
        )
        self._cfg = apply_detection(
            current, det, effective,
            accept_services=accept_additions,
            accept_service_removals=accept_removals,
        )
        self._load_all()

    def _on_reset(self) -> None:
        button = QMessageBox.question(
            self,
            "Reset to defaults",
            "Replace ALL current preferences with archward defaults?\n\n"
            "This does not write to disk until you click Save.",
        )
        if button != QMessageBox.StandardButton.Yes:
            return
        self._cfg = default_config()
        self._load_all()

    def _on_sidebar_row_changed(self, row: int) -> None:
        # Skip non-selectable rows (categories, separators).
        if row not in self._sidebar_row_to_label:
            return
        stack_idx = list(self._sidebar_row_to_label.keys()).index(row)
        self._content.setCurrentIndex(stack_idx)
        self._restore_tab_btn.setEnabled(row in self._config_tab_indices)

    def _on_reset_current_tab(self) -> None:
        row = self._sidebar.currentRow()
        tab = self._config_tab_indices.get(row)
        if tab is None:
            return
        tab_name = self._sidebar_row_to_label.get(row, "")
        result = QMessageBox.question(
            self,
            "Restore tab defaults",
            f"Reset the '{tab_name}' tab to archward defaults?\n\n"
            "This does not write to disk until you click Save.",
        )
        if result != QMessageBox.StandardButton.Yes:
            return
        tab.load(default_config())

    # ── Profile-tab handlers ──────────────────────────────────────────────

    def _set_title_for_path(self, config_path: Path | None) -> None:
        if config_path is not None:
            self.setWindowTitle(f"archward — Preferences (profile: {config_path.stem})")
        else:
            self.setWindowTitle("archward — Preferences")

    def _is_dirty(self) -> bool | None:
        """True if the draft differs from self._cfg, False if equal, None on
        validation error (caller decides how to handle)."""
        try:
            draft = self._build_draft()
        except ValidationError:
            return None
        return draft != self._cfg

    def _on_profile_switch(self, target_path) -> None:
        """Dirty-check, then relay the switch up to MainWindow.

        target_path is Path | None (None == default config).
        """
        dirty = self._is_dirty()
        if dirty is None:
            QMessageBox.warning(
                self,
                "Invalid configuration",
                "Fix validation errors in the other tabs before switching profiles.",
            )
            return

        if dirty:
            current_label = (
                self._config_path.stem if self._config_path is not None else "(default)"
            )
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Question)
            box.setWindowTitle("Unsaved changes")
            box.setText(f"You have unsaved edits in the current profile ({current_label}).")
            box.setInformativeText(
                "Save them, discard them, or cancel the switch?"
            )
            save_btn = box.addButton("Save and switch", QMessageBox.ButtonRole.AcceptRole)
            discard_btn = box.addButton("Discard and switch", QMessageBox.ButtonRole.DestructiveRole)
            cancel_btn = box.addButton(QMessageBox.StandardButton.Cancel)
            box.setDefaultButton(cancel_btn)
            box.exec()
            clicked = box.clickedButton()
            if clicked is cancel_btn:
                return
            if clicked is save_btn:
                try:
                    draft = self._build_draft()
                    saved_path = write_config(draft, self._config_path)
                except (ValidationError, OSError) as e:
                    QMessageBox.critical(self, "Save failed", str(e))
                    return
                self._cfg = draft
                log.info("preferences saved to %s (pre-switch)", saved_path)
                self.config_saved.emit(draft)
            # Discard falls through without saving.

        # Relay up; MainWindow updates self.cfg/strategy/logging/title and
        # then calls back via apply_profile_switch() to refresh this dialog.
        self.profile_switch_requested.emit(target_path)

    def apply_profile_switch(self, new_cfg: ConfigModel, new_path: Path | None) -> None:
        """Called by MainWindow after it has rebuilt its state, so the open
        dialog can re-render against the new profile without closing."""
        self._cfg = new_cfg
        self._config_path = new_path
        self._set_title_for_path(new_path)
        self._advanced.set_active_path(
            new_path if new_path is not None else default_config_path()
        )
        self._profiles.set_active(new_path)
        self._load_all()

    def _on_save_current_as(self, name: str) -> None:
        try:
            target = config_paths.profile_config_path(name)
        except ValueError as e:
            QMessageBox.warning(self, "Invalid name", str(e))
            return
        try:
            draft = self._build_draft()
        except ValidationError as e:
            QMessageBox.critical(
                self,
                "Invalid configuration",
                f"Fix validation errors before saving as a new profile:\n\n{e}",
            )
            return
        try:
            write_config(draft, target)
        except OSError as e:
            QMessageBox.critical(self, "Save failed", f"Could not write {target}:\n{e}")
            return
        log.info("saved current draft to new profile %s", target)
        self._profiles.refresh_list(self._config_path)
        self._profiles._select_path(target)

    def _on_profile_renamed(self, old_path, new_path) -> None:
        if self._config_path != old_path:
            return  # A non-active profile was renamed; dialog state unaffected.

        # Active profile was renamed. The file on disk is already at
        # new_path (the Profiles tab moved it via Path.rename). If the
        # dialog holds unsaved edits, persist them to the new path so the
        # MainWindow reload doesn't clobber the user's draft.
        dirty = self._is_dirty()
        if dirty:
            try:
                draft = self._build_draft()
                write_config(draft, new_path)
                self._cfg = draft
                log.info("preserved draft across active-profile rename → %s", new_path)
            except (ValidationError, OSError) as e:
                log.warning("could not preserve draft across rename: %s", e)

        self._config_path = new_path
        self._set_title_for_path(new_path)
        self._advanced.set_active_path(new_path)
        # Relay to MainWindow so its config_path / window title / status follow.
        self.profile_switch_requested.emit(new_path)


# ── Internal helpers (factored after the tabs for readability) ───────────


def _lbl(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet("color: #6c757d;")
    return lbl


def _help_label(text: str) -> QLabel:
    """Help label using full-strength text color + italic.

    Prior iterations used `palette(mid)` and `#6c757d` for visual muting, but
    both rendered invisibly on some themes — Plasma Breeze pins `mid` very
    close to the window background, and hard-coded grays disappear on dark
    themes. Using `palette(text)` guarantees the help is always readable
    regardless of theme; visual hierarchy comes from italic + smaller font +
    slight left indent rather than from color.
    """
    lbl = QLabel(text)
    lbl.setStyleSheet(
        "color: palette(text);"
        "font-style: italic;"
        "padding-left: 8px;"
        "font-size: 11px;"
    )
    lbl.setWordWrap(True)
    return lbl


def _field_with_help(widget: QWidget, section: str, field: str) -> QWidget:
    """Wrap `widget` with a small help label below it. The label text is sourced
    from help_text.HELP keyed by (section, field). Missing keys produce no label."""
    body = help_text.get(section, field)
    if not body:
        return widget
    container = QWidget()
    vbox = QVBoxLayout(container)
    vbox.setContentsMargins(0, 0, 0, 0)
    vbox.setSpacing(2)
    vbox.addWidget(widget)
    vbox.addWidget(_help_label(body))
    return container


def _section_help(section: str, key: str = "_section") -> QLabel | None:
    """Section-level help banner shown at the top of a tab. None if missing.

    Styled with a brand-teal left border so every Preferences tab reads as
    coherently themed — the banner's stripe matches the running-phase
    stripe in the main window's phase rail.
    """
    body = help_text.get(section, key)
    if not body:
        return None
    from archward.ui.theme import brand_palette
    accent = brand_palette().accent_border
    lbl = _help_label(body)
    lbl.setStyleSheet(
        "color: palette(text);"
        "font-style: italic;"
        "font-size: 11px;"
        "padding: 4px 0 8px 12px;"
        f"border-left: 3px solid {accent};"
    )
    return lbl


def _wrap(layout) -> QWidget:
    """Wrap a layout in a QWidget so it can be added to a QFormLayout row."""
    w = QWidget()
    w.setLayout(layout)
    return w


def _make_list_edit() -> QPlainTextEdit:
    edit = QPlainTextEdit()
    edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
    font = QFont("monospace")
    font.setStyleHint(QFont.StyleHint.TypeWriter)
    edit.setFont(font)
    return edit
