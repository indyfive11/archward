"""archward Preferences dialog.

Tabs (one Pydantic sub-model per tab):
  General · Gates · Risk · Services · Pacnew · AUR · Pacman · Verify · Privilege · Advanced

Edit flow:
  1. Dialog opens with the current ConfigModel loaded into widgets.
  2. User edits in any tab; changes stay in the widgets (not persisted).
  3. Save → validate via Pydantic → write to ~/.config/archward/config.toml.
  4. Cancel → discard changes.

The Pacnew rules list is shown read-only — editing the rule list requires
direct config.toml hand-editing (Advanced tab has an "Open config.toml" shortcut
for that). All other config is editable in-place.
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)
from pydantic import ValidationError

from archward.config.defaults import default_config
from archward.config.detect import apply_detection, diff_against, run_full_detection
from archward.config.loader import default_config_path, merge_partial, write_config
from archward.models.config import (
    AurConfig,
    ConfigModel,
    GatesConfig,
    GeneralConfig,
    PacmanConfig,
    PacnewConfig,
    PrivilegeConfig,
    RiskConfig,
    ServicesConfig,
    VerifyConfig,
)

log = logging.getLogger(__name__)


# ── Small helpers ────────────────────────────────────────────────────────


def _lines_to_tuple(text: str) -> tuple[str, ...]:
    return tuple(line.strip() for line in text.splitlines() if line.strip())


def _tuple_to_lines(items) -> str:
    return "\n".join(items)


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
        self._keep_snapshots.setRange(1, 100)
        self._keep_logs = QSpinBox()
        self._keep_logs.setRange(1, 100)

        snapshot_row = QHBoxLayout()
        snapshot_row.addWidget(self._snapshot_dir, stretch=1)
        snapshot_row.addWidget(snapshot_browse)
        log_row = QHBoxLayout()
        log_row.addWidget(self._log_dir, stretch=1)
        log_row.addWidget(log_browse)

        form = QFormLayout(self)
        form.addRow("Snapshot directory:", _wrap(snapshot_row))
        form.addRow("Keep N snapshots:", self._keep_snapshots)
        form.addRow("Log directory:", _wrap(log_row))
        form.addRow("Keep N log files:", self._keep_logs)

    def _browse(self, target: QLineEdit) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Choose directory", target.text())
        if directory:
            target.setText(directory)

    def load(self, cfg: ConfigModel) -> None:
        self._snapshot_dir.setText(str(cfg.general.snapshot_dir))
        self._log_dir.setText(str(cfg.general.log_dir))
        self._keep_snapshots.setValue(cfg.general.keep_snapshots)
        self._keep_logs.setValue(cfg.general.keep_logs)

    def dump(self) -> GeneralConfig:
        return GeneralConfig(
            snapshot_dir=Path(self._snapshot_dir.text()),
            log_dir=Path(self._log_dir.text()),
            keep_snapshots=self._keep_snapshots.value(),
            keep_logs=self._keep_logs.value(),
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

        form = QFormLayout(self)
        form.addRow("Snapshot max age:", self._max_age)
        form.addRow("Minimum free disk on /:", self._min_disk)
        form.addRow("", self._allow_override)

    def load(self, cfg: ConfigModel) -> None:
        self._max_age.setValue(cfg.gates.snapshot_max_age_minutes)
        self._min_disk.setValue(cfg.gates.min_disk_gb)
        self._allow_override.setChecked(cfg.gates.allow_override)

    def dump(self) -> GatesConfig:
        return GatesConfig(
            snapshot_max_age_minutes=self._max_age.value(),
            min_disk_gb=self._min_disk.value(),
            allow_override=self._allow_override.isChecked(),
        )


class _RiskTab(_Tab):
    section = "risk"

    def __init__(self) -> None:
        super().__init__()
        self._high = _make_list_edit()
        self._medium_patterns = _make_list_edit()
        self._kernel_patterns = _make_list_edit()
        self._kernel_excludes = _make_list_edit()

        form = QFormLayout(self)
        form.addRow(_lbl("HIGH-risk packages (exact match, one per line):"), self._high)
        form.addRow(_lbl("MEDIUM patterns (fnmatch glob, one per line):"), self._medium_patterns)
        form.addRow(_lbl("Kernel patterns (fnmatch, → HIGH + is_kernel):"), self._kernel_patterns)
        form.addRow(_lbl("Kernel pattern excludes (e.g. linux-firmware*):"), self._kernel_excludes)

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

        layout = QVBoxLayout(self)
        layout.addWidget(_lbl("Services to verify (one per line; default severity is 'critical'):"))
        layout.addWidget(self._to_verify, stretch=2)
        layout.addWidget(_lbl("Per-unit severity overrides:"))
        layout.addWidget(self._severity, stretch=1)
        layout.addLayout(btn_row)

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
        )


class _PacnewTab(_Tab):
    section = "pacnew"

    def __init__(self) -> None:
        super().__init__()
        self._default = QComboBox()
        self._default.addItems(["keep_ours", "take_new", "review_needed"])

        self._tree = QTreeWidget()
        self._tree.setColumnCount(3)
        self._tree.setHeaderLabels(["Pattern", "Strategy", "Note"])
        self._tree.setRootIsDecorated(False)
        self._tree.setSelectionMode(QTreeWidget.SelectionMode.NoSelection)
        self._tree.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        hint = QLabel(
            "Pacnew rules are edited by hand in config.toml. Use the Advanced "
            "tab's 'Open config.toml' to launch your editor."
        )
        hint.setStyleSheet("color: #6c757d;")
        hint.setWordWrap(True)

        form_top = QFormLayout()
        form_top.addRow("Default strategy:", self._default)

        layout = QVBoxLayout(self)
        layout.addLayout(form_top)
        layout.addWidget(_lbl("Rules (read-only):"))
        layout.addWidget(self._tree, stretch=1)
        layout.addWidget(hint)

        # Preserve the loaded rules so dump() can return them unchanged.
        self._loaded_rules: tuple = ()

    def load(self, cfg: ConfigModel) -> None:
        self._default.setCurrentText(cfg.pacnew.default_strategy.value)
        self._loaded_rules = cfg.pacnew.rules
        self._tree.clear()
        for rule in cfg.pacnew.rules:
            self._tree.addTopLevelItem(
                QTreeWidgetItem([rule.pattern, rule.strategy.value, rule.note or ""])
            )

    def dump(self) -> PacnewConfig:
        from archward.models.pacnew import PacnewRecommendation

        return PacnewConfig(
            default_strategy=PacnewRecommendation(self._default.currentText()),
            rules=self._loaded_rules,
        )


class _AurTab(_Tab):
    section = "aur"

    def __init__(self) -> None:
        super().__init__()
        self._enabled = QCheckBox("Enable AUR phase")
        self._skip = QCheckBox("Skip even when enabled (one-shot override)")
        self._helper_preference = _make_list_edit()
        self._helper_preference.setPlaceholderText("yay\nparu\naurutils")

        layout = QVBoxLayout(self)
        layout.addWidget(self._enabled)
        layout.addWidget(self._skip)
        layout.addWidget(_lbl("Helper preference (first found on PATH wins; one per line):"))
        layout.addWidget(self._helper_preference, stretch=1)

    def load(self, cfg: ConfigModel) -> None:
        self._enabled.setChecked(cfg.aur.enabled)
        self._skip.setChecked(cfg.aur.skip)
        self._helper_preference.setPlainText(_tuple_to_lines(cfg.aur.helper_preference))

    def dump(self) -> AurConfig:
        return AurConfig(
            enabled=self._enabled.isChecked(),
            skip=self._skip.isChecked(),
            helper_preference=_lines_to_tuple(self._helper_preference.toPlainText()),
        )


class _PacmanTab(_Tab):
    section = "pacman"

    def __init__(self) -> None:
        super().__init__()
        self._noconfirm = QCheckBox("Pass --noconfirm to pacman")
        self._extra_args = _make_list_edit()
        self._extra_args.setPlaceholderText("--needed\n--overwrite\n/etc/foo")

        layout = QVBoxLayout(self)
        layout.addWidget(self._noconfirm)
        layout.addWidget(_lbl("Extra pacman arguments (one per line):"))
        layout.addWidget(self._extra_args, stretch=1)

    def load(self, cfg: ConfigModel) -> None:
        self._noconfirm.setChecked(cfg.pacman.noconfirm)
        self._extra_args.setPlainText(_tuple_to_lines(cfg.pacman.extra_args))

    def dump(self) -> PacmanConfig:
        return PacmanConfig(
            noconfirm=self._noconfirm.isChecked(),
            extra_args=_lines_to_tuple(self._extra_args.toPlainText()),
        )


class _VerifyTab(_Tab):
    section = "verify"

    def __init__(self) -> None:
        super().__init__()
        self._enabled = QCheckBox("Enable verify phase")
        self._reboot_log = QLineEdit()
        self._reboot_log.setPlaceholderText("/var/log/reboot-recommendation-trigger.log")

        form = QFormLayout(self)
        form.addRow("", self._enabled)
        form.addRow("Reboot-recommended log:", self._reboot_log)
        hint = QLabel(
            "Empty path disables the reboot-log check. EndeavorOS provides "
            "/var/log/reboot-recommendation-trigger.log via eos-reboot-required."
        )
        hint.setStyleSheet("color: #6c757d;")
        hint.setWordWrap(True)
        form.addRow("", hint)

    def load(self, cfg: ConfigModel) -> None:
        self._enabled.setChecked(cfg.verify.enabled)
        self._reboot_log.setText(cfg.verify.reboot_log)

    def dump(self) -> VerifyConfig:
        return VerifyConfig(
            enabled=self._enabled.isChecked(),
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
        form.addRow("Sudo strategy:", self._mode)
        form.addRow("Askpass override (path):", _wrap(ask_row))
        hint = QLabel(
            "Leave Askpass empty to auto-discover (ksshaskpass → lxqt-openssh-askpass "
            "→ ssh-askpass). The 'auto' strategy picks askpass+persistent if a binary "
            "is found."
        )
        hint.setStyleSheet("color: #6c757d;")
        hint.setWordWrap(True)
        form.addRow("", hint)

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


class _AdvancedTab(QWidget):
    """Not a _Tab — doesn't have load/dump. Provides actions that mutate the
    parent dialog's draft config."""

    redetect_requested = Signal()
    reset_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        redetect_btn = QPushButton("Re-detect…")
        redetect_btn.setToolTip(
            "Re-run distro/kernel/AUR/service detection and propose changes."
        )
        redetect_btn.clicked.connect(self.redetect_requested.emit)

        reset_btn = QPushButton("Reset to defaults…")
        reset_btn.setToolTip("Replace all settings with archward defaults.")
        reset_btn.clicked.connect(self.reset_requested.emit)

        open_cfg_btn = QPushButton("Open config.toml in editor")
        open_cfg_btn.setToolTip(
            "Opens the active config file in $EDITOR or the desktop default."
        )
        open_cfg_btn.clicked.connect(self._open_config)

        path_label = QLabel(f"Active config file: {default_config_path()}")
        path_label.setStyleSheet("color: #6c757d;")
        path_label.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.addWidget(redetect_btn)
        layout.addWidget(reset_btn)
        layout.addWidget(open_cfg_btn)
        layout.addStretch(1)
        layout.addWidget(path_label)

    def _open_config(self) -> None:
        path = default_config_path()
        editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
        if editor:
            subprocess.Popen([editor, str(path)])
            return
        # Fall back to xdg-open for the desktop's default text editor.
        try:
            subprocess.Popen(["xdg-open", str(path)])
        except FileNotFoundError:
            QMessageBox.warning(
                self,
                "No editor",
                "Set $EDITOR or install xdg-utils to open the config file from here.",
            )


# ── Dialog ───────────────────────────────────────────────────────────────


class PreferencesDialog(QDialog):
    """Modal preferences editor."""

    config_saved = Signal(object)  # ConfigModel — emitted after Save succeeds

    def __init__(self, cfg: ConfigModel, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("archward — Preferences")
        self.resize(900, 700)

        self._cfg = cfg

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
        ]
        self._advanced = _AdvancedTab()
        self._advanced.redetect_requested.connect(self._on_redetect)
        self._advanced.reset_requested.connect(self._on_reset)

        self._tab_widget = QTabWidget()
        for label, tab in zip(labels, self._tabs):
            self._tab_widget.addTab(tab, label)
        self._tab_widget.addTab(self._advanced, "Advanced")

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(self._tab_widget)
        layout.addWidget(buttons)

        self._load_all()

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
            path = write_config(new_cfg)
        except OSError as e:
            QMessageBox.critical(self, "Save failed", f"Could not write {default_config_path()}:\n{e}")
            return
        self._cfg = new_cfg
        log.info("preferences saved to %s", path)
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
        if diff.aur_disable:
            lines.append("+ aur.enabled = false  (no AUR helper detected)")

        button = QMessageBox.question(
            self,
            "Re-detect — proposed changes",
            "\n".join(lines)
            + "\n\nApply these to the current draft? "
            "(Services additions are included; you can still Cancel without saving.)",
        )
        if button != QMessageBox.StandardButton.Yes:
            return

        self._cfg = apply_detection(current, det, diff, accept_services=True)
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


# ── Internal helpers (factored after the tabs for readability) ───────────


def _lbl(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet("color: #6c757d;")
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
