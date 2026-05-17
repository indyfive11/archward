"""Result banner — compact horizontal strip shown at the bottom after completion.

Replaces the full-page ResultView. The current phase view (risk for dry-run,
verify for real updates) stays visible above so the user keeps the context they
were just looking at; the banner just tells them the final RESULT tag in
human-friendly form ("Needs Review" rather than "RESULT:NEEDS_REVIEW").
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from archward.pipeline.pipeline import PipelineResult
from archward.ui.theme import brand_palette, brand_success_colors, status_palette


# Tag → (severity_key, human label). The actual bg/fg colors are pulled from
# the active theme via status_palette() at render time so dark/light themes
# render appropriately.
_TAG_INFO = {
    "RESULT:SUCCESS": ("success", "Success"),
    "RESULT:REBOOT_NEEDED": ("info", "Reboot Needed"),
    "RESULT:PACNEW_MERGE_NEEDED": ("info", "Pacnew Merge Needed"),
    "RESULT:NEEDS_REVIEW": ("info", "Needs Review"),
    "RESULT:VERIFY_FAILED": ("danger", "Verify Failed"),
    "RESULT:UPDATE_FAILED": ("danger", "Update Failed"),
}


def _colors_for(severity: str) -> tuple[str, str]:
    """Return (bg, fg) CSS strings for a severity key from the active theme.

    v0.4.0: 'success' uses the brand-themed teal palette instead of the
    generic Bootstrap-derived green so a clean run carries archward's
    identity color.
    """
    if severity == "success":
        return brand_success_colors()
    p = status_palette()
    return {
        "info": (p.info_bg, p.info_fg),
        "danger": (p.danger_bg, p.danger_fg),
        "neutral": (p.neutral_bg, p.neutral_fg),
    }.get(severity, (p.neutral_bg, p.neutral_fg))


class ResultBanner(QWidget):
    orphan_manage_requested = Signal(list)  # list[str] of orphan package names

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._label = QLabel("")
        self._label.setStyleSheet("font-weight: bold; padding: 2px 10px;")
        _brand = brand_palette()
        self._orphan_btn = QPushButton("Manage orphan packages…")
        self._orphan_btn.setFlat(True)
        self._orphan_btn.setStyleSheet(
            f"QPushButton {{ color: {_brand.accent_text_css}; "
            f"text-decoration: underline; padding: 0 8px; }}"
            f"QPushButton:hover {{ background: {_brand.accent_bg_tint}; }}"
        )
        self._orphan_btn.setVisible(False)
        self._orphan_btn.clicked.connect(self._on_orphan_clicked)
        self._orphans: list[str] = []
        self._detail = QLabel("")
        self._detail.setStyleSheet("padding: 2px 10px;")
        self._detail.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._label)
        layout.addWidget(self._orphan_btn)
        layout.addWidget(self._detail, stretch=1)

        self.setFixedHeight(24)
        self.setVisible(False)

    def _on_orphan_clicked(self) -> None:
        self.orphan_manage_requested.emit(self._orphans)

    def show_result(self, result: PipelineResult) -> None:
        if result.summary is None:
            self._label.setText("Pipeline produced no summary")
            self._detail.setText(result.aborted_reason or "")
            bg, fg = _colors_for("neutral")
            self._apply_style(bg, fg)
            self.setVisible(True)
            return

        tag = result.summary.tag
        severity, human = _TAG_INFO.get(tag, ("neutral", tag))
        bg, fg = _colors_for(severity)
        self._label.setText(human)
        self._apply_style(bg, fg)

        # Orphan CTA — show when verify detected orphans.
        self._orphans = []
        if result.verify:
            for check in result.verify.checks:
                if check.name == "orphans" and check.detail:
                    self._orphans = [ln.strip() for ln in check.detail.splitlines() if ln.strip()]
                    break
        self._orphan_btn.setVisible(bool(self._orphans))

        # Right-side detail: a compact one-liner of the most relevant context.
        bits: list[str] = []
        for sec in result.summary.secondary_tags:
            _sev2, sec_human = _TAG_INFO.get(sec, ("neutral", sec))
            bits.append(f"+ {sec_human}")
        if result.summary.fail_count or result.summary.warn_count:
            bits.append(
                f"verify: {result.summary.fail_count} FAIL · "
                f"{result.summary.warn_count} WARN"
            )
        if result.aur and result.aur.failures:
            bits.append(f"AUR: {len(result.aur.failures)} build failure(s)")
        if result.summary.reboot_needed:
            bits.append("reboot required to activate kernel")
        if result.aborted_reason:
            bits.append(result.aborted_reason)

        self._detail.setText("    ".join(bits))
        self.setVisible(True)

    def reset(self) -> None:
        """Hide the banner — called at the start of each run."""
        self._label.setText("")
        self._detail.setText("")
        self._orphan_btn.setVisible(False)
        self._orphans = []
        self.setVisible(False)

    def _apply_style(self, bg: str, fg: str) -> None:
        self._label.setStyleSheet(
            f"font-weight: bold; padding: 2px 10px; background: {bg}; color: {fg};"
        )
        self._detail.setStyleSheet(f"padding: 2px 10px; background: {bg}; color: {fg};")
