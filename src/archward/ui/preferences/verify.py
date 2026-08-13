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
        self._cfg: ConfigModel | None = None  # remembered by load() for sudo strategy
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

    def _build_strategy(self):
        from archward.app import build_sudo_strategy
        from archward.config.loader import load_config

        # v0.4.17: honor the profile being edited (previously always
        # load_config() — i.e. the default profile's privilege settings).
        return build_sudo_strategy(self._cfg if self._cfg is not None else load_config())

    def _toggle_sudoers(self) -> None:
        from archward.pacman.runner import run_capture
        from archward.ui.off_thread import run_off_thread

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
            strategy = self._build_strategy()

            def _remove() -> str:
                code, _out, err = run_capture(
                    ["rm", str(_STALE_LIBS_SUDOERS_PATH)], strategy=strategy,
                )
                if code != 0:
                    raise RuntimeError(
                        f"Could not remove {_STALE_LIBS_SUDOERS_PATH}:\n{err.strip()}"
                    )
                return "sudoers entry removed."

            # Off-thread (v0.4.17): sudo can block on askpass; the GUI
            # thread must stay free to show that dialog.
            self._stale_libs_sudo_btn.setEnabled(False)
            run_off_thread(
                self,
                fn=_remove,
                title="Remove sudoers entry",
                progress_label=f"Removing {_STALE_LIBS_SUDOERS_PATH}…",
                on_done=self._on_sudoers_toggled,
            )
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
            strategy = self._build_strategy()

            def _write() -> str:
                code, _out, err = run_capture(
                    ["tee", str(_STALE_LIBS_SUDOERS_PATH)],
                    strategy=strategy,
                    input_text=_STALE_LIBS_SUDOERS_LINE,
                )
                if code != 0:
                    raise RuntimeError(
                        f"Could not write {_STALE_LIBS_SUDOERS_PATH}:\n{err.strip()}"
                    )
                # Lock down permissions (sudoers.d files must be 0440)
                run_capture(
                    ["chmod", "0440", str(_STALE_LIBS_SUDOERS_PATH)], strategy=strategy,
                )
                return (
                    "Full stale-library coverage enabled.\n"
                    f"sudoers entry written to {_STALE_LIBS_SUDOERS_PATH}."
                )

            self._stale_libs_sudo_btn.setEnabled(False)
            run_off_thread(
                self,
                fn=_write,
                title="Enable full coverage",
                progress_label=f"Writing {_STALE_LIBS_SUDOERS_PATH}…",
                on_done=self._on_sudoers_toggled,
            )

    def _on_sudoers_toggled(self, result: object) -> None:
        self._stale_libs_sudo_btn.setEnabled(True)
        if isinstance(result, Exception):
            QMessageBox.critical(self, "sudoers update failed", str(result))
        else:
            QMessageBox.information(self, "Done", str(result))
        self._refresh_sudo_btn()

    def load(self, cfg: ConfigModel) -> None:
        self._cfg = cfg
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
