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
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
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

log = logging.getLogger(__name__)


# ── Small helpers ────────────────────────────────────────────────────────


def _lines_to_tuple(text: str) -> tuple[str, ...]:
    return tuple(line.strip() for line in text.splitlines() if line.strip())


def _tuple_to_lines(items) -> str:
    return "\n".join(items)


def _open_in_editor(parent: QWidget, path: Path) -> None:
    """Open `path` in $VISUAL / $EDITOR, falling back to xdg-open.

    Shared by the Advanced and Profiles tabs so the open-in-editor
    affordance behaves identically regardless of which file the user
    points it at.
    """
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    if editor:
        subprocess.Popen([editor, str(path)])
        return
    try:
        subprocess.Popen(["xdg-open", str(path)])
    except FileNotFoundError:
        QMessageBox.warning(
            parent,
            "No editor",
            "Set $EDITOR or install xdg-utils to open the config file from here.",
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

        self._notify_on_completion = QCheckBox("Show a desktop notification when the pipeline finishes")

        snapshot_row = QHBoxLayout()
        snapshot_row.addWidget(self._snapshot_dir, stretch=1)
        snapshot_row.addWidget(snapshot_browse)
        log_row = QHBoxLayout()
        log_row.addWidget(self._log_dir, stretch=1)
        log_row.addWidget(log_browse)

        form = QFormLayout(self)
        form.addRow("Snapshot directory:", _field_with_help(_wrap(snapshot_row), "general", "snapshot_dir"))
        form.addRow("Keep N snapshots:", _field_with_help(self._keep_snapshots, "general", "keep_snapshots"))
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
        self._keep_logs.setValue(cfg.general.keep_logs)
        self._notify_on_completion.setChecked(cfg.general.notify_on_completion)

    def dump(self) -> GeneralConfig:
        return GeneralConfig(
            snapshot_dir=Path(self._snapshot_dir.text()),
            log_dir=Path(self._log_dir.text()),
            keep_snapshots=self._keep_snapshots.value(),
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

        form = QFormLayout(self)
        form.addRow("Snapshot max age:", _field_with_help(self._max_age, "gates", "snapshot_max_age_minutes"))
        form.addRow("Minimum free disk on /:", _field_with_help(self._min_disk, "gates", "min_disk_gb"))
        form.addRow("", _field_with_help(self._allow_override, "gates", "allow_override"))

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

        hint = _help_label(
            "Pacnew rules are edited by hand in config.toml. Use the Advanced "
            "tab's 'Open config.toml' to launch your editor."
        )

        form_top = QFormLayout()
        form_top.addRow("Default strategy:",
                        _field_with_help(self._default, "pacnew", "default_strategy"))

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
        layout.addWidget(_help_label(help_text.get("aur", "enabled")))
        layout.addWidget(self._skip)
        layout.addWidget(_help_label(help_text.get("aur", "skip")))
        layout.addWidget(_lbl("Helper preference (first found on PATH wins; one per line):"))
        layout.addWidget(self._helper_preference, stretch=1)
        layout.addWidget(_help_label(help_text.get("aur", "helper_preference")))

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


class _VerifyTab(_Tab):
    section = "verify"

    def __init__(self) -> None:
        super().__init__()
        self._enabled = QCheckBox("Enable verify phase")
        self._reboot_log = QLineEdit()
        self._reboot_log.setPlaceholderText("/var/log/reboot-recommendation-trigger.log")

        form = QFormLayout(self)
        form.addRow("", _field_with_help(self._enabled, "verify", "enabled"))
        form.addRow("Reboot-recommended log:",
                    _field_with_help(self._reboot_log, "verify", "reboot_log"))

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

        layout.addWidget(_lbl("Pre-update hooks (run before pacman -Syu, one per line):"))
        layout.addWidget(self._pre_update, stretch=1)
        layout.addWidget(_help_label(help_text.get("hooks", "pre_update")))

        layout.addWidget(_lbl("Post-verify hooks (run after verify phase, one per line):"))
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

        # Two-column button grid: cheap and predictable.
        btn_row1 = QHBoxLayout()
        btn_row1.addWidget(self._switch_btn)
        btn_row1.addWidget(self._open_btn)
        btn_row2 = QHBoxLayout()
        btn_row2.addWidget(self._new_defaults_btn)
        btn_row2.addWidget(self._save_as_btn)
        btn_row3 = QHBoxLayout()
        btn_row3.addWidget(self._rename_btn)
        btn_row3.addWidget(self._delete_btn)

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

        self._profiles = _ProfilesTab(config_path=config_path)
        self._profiles.profile_switch_requested.connect(self._on_profile_switch)
        self._profiles.save_current_as_requested.connect(self._on_save_current_as)
        self._profiles.profile_renamed.connect(self._on_profile_renamed)
        # profile_created / profile_deleted are informational only — the tab
        # already refreshed its own list, and the dialog has nothing to do.

        self._tab_widget = QTabWidget()
        for label, tab in zip(labels, self._tabs):
            self._tab_widget.addTab(tab, label)
        self._tab_widget.addTab(self._profiles, "Profiles")
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
            path = write_config(new_cfg, self._config_path)
        except OSError as e:
            target = self._config_path if self._config_path is not None else default_config_path()
            QMessageBox.critical(self, "Save failed", f"Could not write {target}:\n{e}")
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
    """Section-level help banner shown at the top of a tab. None if missing."""
    body = help_text.get(section, key)
    if not body:
        return None
    lbl = _help_label(body)
    # Override _help_label's left indent — section banners look better
    # flush-left with extra vertical breathing room.
    lbl.setStyleSheet(
        "color: palette(text);"
        "font-style: italic;"
        "font-size: 11px;"
        "padding: 4px 0 8px 0;"
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
