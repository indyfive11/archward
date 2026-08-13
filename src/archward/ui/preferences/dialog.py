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
from archward.ui.preferences.advanced import _AdvancedTab
from archward.ui.preferences.aur import _AurTab
from archward.ui.preferences.cache import _CacheTab
from archward.ui.preferences.gates import _GatesTab
from archward.ui.preferences.general import _GeneralTab
from archward.ui.preferences.hooks import _HooksTab
from archward.ui.preferences.pacman import _PacmanTab
from archward.ui.preferences.pacnew import _PacnewTab
from archward.ui.preferences.privilege import _PrivilegeTab
from archward.ui.preferences.profiles import _DEFAULT_ROLE, _ProfilesTab  # noqa: F401
from archward.ui.preferences.risk import _RiskTab
from archward.ui.preferences.services import _ServicesTab
from archward.ui.preferences.verify import _VerifyTab


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
        # v0.5 honesty: if a section on disk failed validation at load, the
        # values shown here are substituted defaults, and Save will replace
        # the broken on-disk section. Say so up front.
        self._warn_fallback_sections()

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

        # Off-thread (v0.4.17): the full detection scan (systemctl queries,
        # helper discovery) takes seconds and used to freeze the dialog.
        from archward.ui.off_thread import run_off_thread

        run_off_thread(
            self,
            fn=run_full_detection,
            title="Re-detect",
            progress_label="Scanning system (kernels, services, AUR helper)…",
            on_done=lambda det: self._on_redetect_scanned(det, current),
        )

    def _on_redetect_scanned(self, det: object, current: ConfigModel) -> None:
        if isinstance(det, Exception):
            QMessageBox.critical(self, "Re-detect failed", str(det))
            return
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

    def _warn_fallback_sections(self) -> None:
        from archward.config.loader import load_config_report

        try:
            _cfg, report = load_config_report(self._config_path)
        except Exception:  # noqa: BLE001 — a notice must never block the dialog
            return
        if report.parse_error:
            QMessageBox.warning(
                self,
                "Config file failed to parse",
                "The config file on disk is not valid TOML — EVERY value shown "
                "here is an archward default.\n\n"
                "Saving will replace the whole file (nothing can be preserved "
                "from an unparseable document, including any plugin-owned "
                "sections). To keep your hand-edited content, close "
                "Preferences and fix the file first.",
            )
            return
        if report.read_only:
            QMessageBox.warning(
                self,
                "Config is read-only",
                "The config file was written by a newer archward "
                f"(schema_version={report.schema_version}) — this version "
                "will not modify it. You can view settings, but Save will "
                "be refused.",
            )
            return
        if not report.fallback_sections:
            return
        names = ", ".join(f"[{s}]" for s in report.fallback_sections)
        QMessageBox.warning(
            self,
            "Config section(s) failed to load",
            f"The on-disk section(s) {names} failed validation and the values "
            "shown here are archward defaults.\n\n"
            "Saving will replace the broken section(s) with the values shown. "
            "To keep your hand-edited values instead, close Preferences and "
            "fix the file first.",
        )

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
