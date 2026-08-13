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
            imported_cfg = load_config(src)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(
                self, "Import failed",
                f"Could not parse {src} as an archward config:\n{e}",
            )
            return

        # Security (v0.4.18): an imported profile can carry [hooks] — shell
        # commands archward executes via `sh -c` on the next update run.
        # Never accept those silently from a file of unknown origin.
        hook_cmds = list(imported_cfg.hooks.pre_update) + list(imported_cfg.hooks.post_verify)
        if hook_cmds:
            listed = "\n".join(f"  {c}" for c in hook_cmds)
            resp = QMessageBox.warning(
                self,
                "Profile contains shell hooks",
                f"This profile defines {len(hook_cmds)} shell command(s) that "
                "archward will execute during the next update run:\n\n"
                f"{listed}\n\n"
                "Only import hooks from a source you trust.\n\nImport anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if resp != QMessageBox.StandardButton.Yes:
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

        # Overwrite confirm (v0.4.18): the name prompt lets a colliding name
        # through when it equals the suggested default, which used to
        # silently clobber the existing profile.
        if target.exists():
            resp = QMessageBox.question(
                self,
                "Overwrite profile?",
                f"A profile named {name!r} already exists:\n{target}\n\n"
                "Overwrite it with the imported file?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if resp != QMessageBox.StandardButton.Yes:
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
