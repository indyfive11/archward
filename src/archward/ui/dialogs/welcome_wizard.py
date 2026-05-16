"""Welcome wizard — shown on first launch and via Help → Setup Wizard.

6-page QDialog guiding new users through:
  0. Welcome
  1. Profile name
  2. System detection (optional)
  3. Key preferences (4 binary settings)
  4. Snapshot retention
  5. Summary / Finish

On Finish: writes the named profile to disk, marks wizard_completed in
QSettings, and sets result_path so the caller can switch to the new profile.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from archward.config.defaults import default_config
from archward.config.detect import (
    DetectionResult,
    apply_detection,
    diff_against,
    run_full_detection,
)
from archward.config.loader import merge_partial, write_config
from archward.config.paths import profile_config_path, valid_profile_name
from archward.ui.icon import archward_icon
from archward.ui.persistent_state import set_wizard_completed
from archward.ui.theme import brand_palette


# ── Page base ─────────────────────────────────────────────────────────────────


class _Page(QWidget):
    """Base for each wizard page."""

    def heading(self, text: str) -> QLabel:
        brand = brand_palette()
        lbl = QLabel(text)
        font = QFont()
        font.setPointSize(14)
        font.setBold(True)
        lbl.setFont(font)
        lbl.setStyleSheet(f"color: {brand.accent_text_css};")
        return lbl

    def body(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        return lbl


# ── Page 0: Welcome ───────────────────────────────────────────────────────────


class _WelcomePage(_Page):
    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.setSpacing(12)

        icon_lbl = QLabel()
        icon_lbl.setPixmap(archward_icon().pixmap(96, 96))
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(icon_lbl)

        layout.addWidget(self.heading("Welcome to Archward"))

        text = self.body(
            "Archward is a safe-update GUI for Arch-based Linux distributions. "
            "It wraps <code>pacman -Syu</code> in a snapshot → gate → classify → "
            "update → verify pipeline so every update is recoverable.\n\n"
            "This wizard takes about two minutes and helps you create your first "
            "profile with settings matched to your system."
        )
        text.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(text)
        layout.addStretch(1)


# ── Page 1: Profile name ──────────────────────────────────────────────────────


class _ProfilePage(_Page):
    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.setSpacing(10)

        layout.addWidget(self.heading("Name your setup"))
        layout.addWidget(self.body(
            "Archward stores settings in profiles — you can have one per machine, "
            "use case, or just one for everything."
        ))

        self._name_edit = QLineEdit("default")
        self._name_edit.textChanged.connect(self._validate)
        layout.addWidget(self._name_edit)

        self._error_lbl = QLabel()
        self._error_lbl.setStyleSheet("color: red;")
        self._error_lbl.hide()
        layout.addWidget(self._error_lbl)

        self._path_lbl = QLabel()
        self._path_lbl.setStyleSheet("color: gray; font-style: italic;")
        layout.addWidget(self._path_lbl)

        layout.addStretch(1)
        self._validate(self._name_edit.text())

    def _validate(self, text: str) -> None:
        if valid_profile_name(text):
            self._error_lbl.hide()
            path = profile_config_path(text)
            self._path_lbl.setText(f"Will be saved to: {path}")
        else:
            self._error_lbl.setText(
                "Name must start with a letter and contain only letters, digits, "
                "hyphens, and underscores (max 64 chars)."
            )
            self._error_lbl.show()
            self._path_lbl.setText("")

    def is_valid(self) -> bool:
        return valid_profile_name(self._name_edit.text())

    def name(self) -> str:
        return self._name_edit.text()


# ── Page 2: System detection ──────────────────────────────────────────────────


class _DetectPage(_Page):
    def __init__(self) -> None:
        super().__init__()
        self._det: DetectionResult | None = None

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.setSpacing(10)

        layout.addWidget(self.heading("Let Archward look at your system"))
        layout.addWidget(self.body(
            "We'll check your kernel packages, AUR helper, and running services "
            "so the config comes pre-filled for your machine."
        ))

        self._scan_btn = QPushButton("Scan now")
        self._scan_btn.setFixedWidth(120)
        self._scan_btn.clicked.connect(self._run_scan)
        layout.addWidget(self._scan_btn)

        self._result_lbl = QLabel()
        self._result_lbl.setWordWrap(True)
        self._result_lbl.hide()
        layout.addWidget(self._result_lbl)

        self._apply_check = QRadioButton("Apply these detections to my profile")
        self._apply_check.setChecked(True)
        self._apply_check.hide()
        layout.addWidget(self._apply_check)

        skip_lbl = QLabel("<a href='#'>Skip this step</a>")
        skip_lbl.setTextFormat(Qt.TextFormat.RichText)
        skip_lbl.setOpenExternalLinks(False)
        skip_lbl.linkActivated.connect(lambda _: self._skip())
        layout.addWidget(skip_lbl)

        layout.addStretch(1)
        self._skipped = False

    def _run_scan(self) -> None:
        self._scan_btn.setEnabled(False)
        self._scan_btn.setText("Scanning…")
        QApplication.processEvents()
        self._det = run_full_detection()
        self._scan_btn.setText("Scan now")
        self._scan_btn.setEnabled(True)
        self._skipped = False

        kernels_str = ", ".join(self._det.kernels) if self._det.kernels else "(none)"
        helper_str = self._det.helper or "(none)"
        lines = [
            f"Kernels: {kernels_str}",
            f"AUR helper: {helper_str}",
        ]
        self._result_lbl.setText(" · ".join(lines))
        self._result_lbl.show()
        self._apply_check.show()

    def _skip(self) -> None:
        self._skipped = True
        self._det = None
        self._result_lbl.setText("Detection skipped.")
        self._result_lbl.show()
        self._apply_check.hide()

    @property
    def apply_detection(self) -> bool:
        return (
            not self._skipped
            and self._det is not None
            and self._apply_check.isChecked()
        )

    @property
    def detection(self) -> DetectionResult | None:
        return self._det


# ── Page 3: Key preferences ───────────────────────────────────────────────────


def _radio_pair(label_yes: str, label_no: str, default_yes: bool) -> tuple[QWidget, QButtonGroup]:
    group = QButtonGroup()
    yes_btn = QRadioButton(label_yes)
    no_btn = QRadioButton(label_no)
    group.addButton(yes_btn, 1)
    group.addButton(no_btn, 0)
    if default_yes:
        yes_btn.setChecked(True)
    else:
        no_btn.setChecked(True)
    row = QWidget()
    hl = QHBoxLayout(row)
    hl.setContentsMargins(0, 0, 0, 0)
    hl.addWidget(yes_btn)
    hl.addWidget(no_btn)
    hl.addStretch(1)
    return row, group


class _PrefsPage(_Page):
    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.setSpacing(12)

        layout.addWidget(self.heading("A few important settings"))

        def _add_setting(lbl_text: str, yes_lbl: str, no_lbl: str, default: bool) -> QButtonGroup:
            layout.addWidget(self.body(lbl_text))
            row, grp = _radio_pair(yes_lbl, no_lbl, default)
            layout.addWidget(row)
            return grp

        self._aur_grp = _add_setting(
            "Enable AUR package updates alongside official packages?",
            "Yes, enable AUR", "No, official packages only", True,
        )
        self._pkgbuild_grp = _add_setting(
            "Show each AUR package's build script before it runs?",
            "Yes, let me review", "No, skip review", False,
        )
        self._snapshot_grp = _add_setting(
            "Take a second snapshot after a successful update for before/after comparison?",
            "Yes, after-snapshot on", "No", True,
        )
        self._notify_grp = _add_setting(
            "Send a desktop notification when an update finishes?",
            "Yes", "No", True,
        )

        layout.addStretch(1)

    def aur_enabled(self) -> bool:
        return self._aur_grp.checkedId() == 1

    def pkgbuild_review(self) -> bool:
        return self._pkgbuild_grp.checkedId() == 1

    def after_snapshot(self) -> bool:
        return self._snapshot_grp.checkedId() == 1

    def notifications(self) -> bool:
        return self._notify_grp.checkedId() == 1


# ── Page 4: Snapshot retention ─────────────────────────────────────────────────


_PRESETS = [
    ("3 months (90 days)", 90),
    ("6 months (180 days) — Recommended", 180),
    ("1 year (365 days)", 365),
]


class _RetentionPage(_Page):
    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.setSpacing(10)

        layout.addWidget(self.heading("How long to keep snapshots"))
        layout.addWidget(self.body(
            "Snapshots are tiny (~100–500 KB each) and let you roll back after an update."
        ))

        self._days_grp = QButtonGroup(self)
        for label, days in _PRESETS:
            btn = QRadioButton(label)
            btn.setProperty("days", days)
            self._days_grp.addButton(btn)
            layout.addWidget(btn)
            if days == 180:
                btn.setChecked(True)

        self._custom_btn = QRadioButton("Custom…")
        self._days_grp.addButton(self._custom_btn)
        layout.addWidget(self._custom_btn)

        self._custom_spin = QSpinBox()
        self._custom_spin.setRange(7, 3650)
        self._custom_spin.setValue(180)
        self._custom_spin.setSuffix(" days")
        self._custom_spin.setEnabled(False)
        self._custom_spin.setFixedWidth(130)
        layout.addWidget(self._custom_spin)

        self._days_grp.buttonToggled.connect(self._on_toggle)

        keep_row = QHBoxLayout()
        keep_row.addWidget(self.body("Always keep at least"))
        self._min_spin = QSpinBox()
        self._min_spin.setRange(1, 50)
        self._min_spin.setValue(2)
        self._min_spin.setFixedWidth(70)
        keep_row.addWidget(self._min_spin)
        keep_row.addWidget(self.body("recent snapshots"))
        keep_row.addStretch(1)
        layout.addLayout(keep_row)

        layout.addStretch(1)

    def _on_toggle(self, btn: QRadioButton, checked: bool) -> None:
        if checked:
            self._custom_spin.setEnabled(btn is self._custom_btn)

    def values(self) -> tuple[int, int]:
        if self._custom_btn.isChecked():
            days = self._custom_spin.value()
        else:
            checked = self._days_grp.checkedButton()
            days = checked.property("days") if checked else 180
        return days, self._min_spin.value()


# ── Page 5: Summary ───────────────────────────────────────────────────────────


class _SummaryPage(_Page):
    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.setSpacing(12)

        layout.addWidget(self.heading("You're all set"))

        self._summary_lbl = QLabel()
        self._summary_lbl.setTextFormat(Qt.TextFormat.RichText)
        self._summary_lbl.setWordWrap(True)
        layout.addWidget(self._summary_lbl)

        layout.addWidget(self.body("You can change any setting in Preferences at any time."))
        layout.addStretch(1)

    def populate(
        self,
        name: str,
        aur_enabled: bool,
        helper: str | None,
        pkgbuild_review: bool,
        after_snapshot: bool,
        keep_days: int,
        keep_min: int,
        notify: bool,
    ) -> None:
        aur_str = f"enabled ({helper} detected)" if aur_enabled and helper else (
            "enabled" if aur_enabled else "disabled"
        )
        path = profile_config_path(name)
        rows = [
            ("Profile", name),
            ("Location", str(path)),
            ("AUR updates", aur_str),
            ("PKGBUILD review", "on" if pkgbuild_review else "off"),
            ("After-snapshot", "on" if after_snapshot else "off"),
            ("Retention", f"{keep_days} days, keep ≥ {keep_min}"),
            ("Notifications", "on" if notify else "off"),
        ]
        html = "<table cellspacing='4'>"
        for key, val in rows:
            html += f"<tr><td><b>{key}:</b></td><td>{val}</td></tr>"
        html += "</table>"
        self._summary_lbl.setText(html)


# ── Wizard ────────────────────────────────────────────────────────────────────


class WelcomeWizard(QDialog):
    """6-page first-run wizard. Sets result_path on Finish."""

    result_path: Path | None = None

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Archward Setup Wizard")
        self.setModal(True)
        self.resize(560, 480)

        # ── Pages ──────────────────────────────────────────────────────────
        self._page_welcome = _WelcomePage()
        self._page_profile = _ProfilePage()
        self._page_detect = _DetectPage()
        self._page_prefs = _PrefsPage()
        self._page_retention = _RetentionPage()
        self._page_summary = _SummaryPage()

        self._stack = QStackedWidget()
        for page in (
            self._page_welcome,
            self._page_profile,
            self._page_detect,
            self._page_prefs,
            self._page_retention,
            self._page_summary,
        ):
            self._stack.addWidget(page)

        # ── Navigation footer ──────────────────────────────────────────────
        self._back_btn = QPushButton("← Back")
        self._back_btn.clicked.connect(self._go_back)

        self._next_btn = QPushButton("Next →")
        self._next_btn.setDefault(True)
        self._next_btn.clicked.connect(self._go_next)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)

        footer = QHBoxLayout()
        footer.addWidget(cancel_btn)
        footer.addStretch(1)
        footer.addWidget(self._back_btn)
        footer.addWidget(self._next_btn)

        layout = QVBoxLayout(self)
        layout.addWidget(self._stack, stretch=1)
        layout.addLayout(footer)

        self._update_nav()

    # ── Navigation ─────────────────────────────────────────────────────────

    def _current(self) -> int:
        return self._stack.currentIndex()

    def _last(self) -> int:
        return self._stack.count() - 1

    def _update_nav(self) -> None:
        idx = self._current()
        self._back_btn.setEnabled(idx > 0)
        if idx == self._last():
            self._next_btn.setText("Finish")
        else:
            self._next_btn.setText("Next →")
        # Block Next on invalid profile name.
        if idx == 1:
            self._next_btn.setEnabled(self._page_profile.is_valid())
        else:
            self._next_btn.setEnabled(True)

    def _go_back(self) -> None:
        if self._current() > 0:
            self._stack.setCurrentIndex(self._current() - 1)
            self._update_nav()

    def _go_next(self) -> None:
        idx = self._current()
        if idx == self._last():
            self._on_finish()
            return
        # Populate summary just before showing it.
        if idx == self._last() - 1:
            self._populate_summary()
        self._stack.setCurrentIndex(idx + 1)
        self._update_nav()

    # ── Profile name validation live ───────────────────────────────────────

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._page_profile._name_edit.textChanged.connect(
            lambda _: self._update_nav() if self._current() == 1 else None
        )

    # ── Summary population ─────────────────────────────────────────────────

    def _populate_summary(self) -> None:
        helper = self._page_detect.detection.helper if self._page_detect.detection else None
        keep_days, keep_min = self._page_retention.values()
        self._page_summary.populate(
            name=self._page_profile.name(),
            aur_enabled=self._page_prefs.aur_enabled(),
            helper=helper,
            pkgbuild_review=self._page_prefs.pkgbuild_review(),
            after_snapshot=self._page_prefs.after_snapshot(),
            keep_days=keep_days,
            keep_min=keep_min,
            notify=self._page_prefs.notifications(),
        )

    # ── Finish ─────────────────────────────────────────────────────────────

    def _on_finish(self) -> None:
        cfg = self._build_config()
        name = self._page_profile.name()
        path = profile_config_path(name)
        write_config(cfg, path)
        set_wizard_completed()
        self.result_path = path
        self.accept()

    def _build_config(self):
        cfg = default_config()

        det = self._page_detect.detection
        if self._page_detect.apply_detection and det is not None:
            diff = diff_against(cfg, det)
            cfg = apply_detection(cfg, det, diff, accept_services=True, accept_service_removals=False)

        keep_days, keep_min = self._page_retention.values()

        cfg = merge_partial(
            cfg,
            general=cfg.general.model_copy(update={
                "after_snapshot": self._page_prefs.after_snapshot(),
                "notify_on_completion": self._page_prefs.notifications(),
                "keep_days": keep_days,
                "keep_min": keep_min,
            }),
            aur=cfg.aur.model_copy(update={
                "enabled": self._page_prefs.aur_enabled(),
            }),
            pacman=cfg.pacman.model_copy(update={
                "noconfirm": not self._page_prefs.pkgbuild_review(),
            }),
        )
        return cfg
