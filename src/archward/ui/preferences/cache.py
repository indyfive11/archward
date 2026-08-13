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

        from archward.ui.off_thread import run_off_thread

        strategy = build_sudo_strategy(self._cfg)

        # Off-thread (v0.4.17): both sudo calls can block on askpass; the
        # GUI thread must stay free to show that dialog.
        def _apply() -> str:
            code, _out, err = run_capture(
                ["tee", str(cp._CONF_D)], strategy=strategy, input_text=conf_content,
            )
            if code != 0:
                raise RuntimeError(
                    f"Could not write /etc/conf.d/pacman-contrib:\n{err.strip()}"
                )
            verb = ["enable", "--now"] if enable_timer else ["disable", "--now"]
            code, _out, err = run_capture(
                ["systemctl", *verb, "paccache.timer"], strategy=strategy,
            )
            if code != 0:
                return (
                    "PARTIAL: PACCACHE_ARGS was written, but toggling "
                    f"paccache.timer failed:\n{err.strip()}"
                )
            return f"Cache policy '{label}' applied."

        run_off_thread(
            self,
            fn=_apply,
            title="Apply cache policy",
            progress_label=f"Applying '{label}'…",
            on_done=self._on_policy_applied,
        )

    def _on_policy_applied(self, result: object) -> None:
        if isinstance(result, Exception):
            QMessageBox.critical(self, "Write failed", str(result))
        elif isinstance(result, str) and result.startswith("PARTIAL: "):
            QMessageBox.warning(
                self, "Timer toggle failed", result.removeprefix("PARTIAL: ")
            )
        else:
            QMessageBox.information(self, "Applied", str(result))
        self._render()

    def _apply_preset(self, preset) -> None:
        self._confirm_and_run(preset.label, preset.paccache_args, preset.enable_timer)

    def _apply_custom(self) -> None:
        n = self._custom_keep.value()
        self._confirm_and_run(f"custom keep {n}", f"-rk{n}", enable_timer=True)
