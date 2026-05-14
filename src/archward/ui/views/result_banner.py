"""Result banner — compact horizontal strip shown at the bottom after completion.

Replaces the full-page ResultView. The current phase view (risk for dry-run,
verify for real updates) stays visible above so the user keeps the context they
were just looking at; the banner just tells them the final RESULT tag in
human-friendly form ("Needs Review" rather than "RESULT:NEEDS_REVIEW").
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from archward.pipeline.pipeline import PipelineResult

# Tag → (background, foreground, human label).
_TAG_STYLE = {
    "RESULT:SUCCESS": ("#d4edda", "#155724", "Success"),
    "RESULT:REBOOT_NEEDED": ("#fff3cd", "#856404", "Reboot Needed"),
    "RESULT:PACNEW_MERGE_NEEDED": ("#fff3cd", "#856404", "Pacnew Merge Needed"),
    "RESULT:NEEDS_REVIEW": ("#fff3cd", "#856404", "Needs Review"),
    "RESULT:VERIFY_FAILED": ("#f8d7da", "#721c24", "Verify Failed"),
    "RESULT:UPDATE_FAILED": ("#f8d7da", "#721c24", "Update Failed"),
}


class ResultBanner(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._label = QLabel("")
        self._label.setStyleSheet("font-weight: bold; padding: 2px 10px;")
        self._detail = QLabel("")
        self._detail.setStyleSheet("padding: 2px 10px;")
        self._detail.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._label)
        layout.addWidget(self._detail, stretch=1)

        self.setFixedHeight(24)
        self.setVisible(False)

    def show_result(self, result: PipelineResult) -> None:
        if result.summary is None:
            self._label.setText("Pipeline produced no summary")
            self._detail.setText(result.aborted_reason or "")
            self._apply_style("#e2e3e5", "#383d41")
            self.setVisible(True)
            return

        tag = result.summary.tag
        bg, fg, human = _TAG_STYLE.get(tag, ("#e2e3e5", "#383d41", tag))
        self._label.setText(human)
        self._apply_style(bg, fg)

        # Right-side detail: a compact one-liner of the most relevant context.
        bits: list[str] = []
        for sec in result.summary.secondary_tags:
            _bg2, _fg2, sec_human = _TAG_STYLE.get(sec, (None, None, sec))
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
        self.setVisible(False)

    def _apply_style(self, bg: str, fg: str) -> None:
        self._label.setStyleSheet(
            f"font-weight: bold; padding: 2px 10px; background: {bg}; color: {fg};"
        )
        self._detail.setStyleSheet(f"padding: 2px 10px; background: {bg}; color: {fg};")
