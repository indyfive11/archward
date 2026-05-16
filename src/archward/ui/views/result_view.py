"""Result phase content view — banner + summary, shown when pipeline completes."""

from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from archward.pipeline.pipeline import PipelineResult

_BANNER_BG = {
    "RESULT:SUCCESS": ("#d4edda", "#155724"),
    "RESULT:REBOOT_NEEDED": ("#fff3cd", "#856404"),
    "RESULT:PACNEW_MERGE_NEEDED": ("#fff3cd", "#856404"),
    "RESULT:NEEDS_REVIEW": ("#fff3cd", "#856404"),
    "RESULT:VERIFY_FAILED": ("#f8d7da", "#721c24"),
    "RESULT:UPDATE_FAILED": ("#f8d7da", "#721c24"),
}


class ResultView(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._banner = QLabel("Pipeline result")
        banner_font = QFont()
        banner_font.setPointSize(banner_font.pointSize() + 4)
        banner_font.setBold(True)
        self._banner.setFont(banner_font)
        self._banner.setStyleSheet(
            "padding: 16px; background: #e2e3e5; color: #383d41; border-radius: 4px;"
        )
        self._details = QLabel("")
        self._details.setStyleSheet("padding: 8px 16px;")
        self._details.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.addWidget(self._banner)
        layout.addWidget(self._details, stretch=1)

    def set_result(self, result: PipelineResult) -> None:
        if result.summary is None:
            self._banner.setText("Pipeline produced no summary")
            self._details.setText(result.aborted_reason or "")
            return

        tag = result.summary.tag
        bg, fg = _BANNER_BG.get(tag, ("#e2e3e5", "#383d41"))
        self._banner.setText(tag)
        self._banner.setStyleSheet(
            f"padding: 16px; background: {bg}; color: {fg}; border-radius: 4px;"
        )

        lines: list[str] = []
        for sec in result.summary.secondary_tags:
            lines.append(f"  + {sec}")
        if result.aborted_reason:
            lines.append(f"  reason: {result.aborted_reason}")
        if result.summary.fail_count or result.summary.warn_count:
            lines.append(
                f"  verify: {result.summary.fail_count} FAIL · "
                f"{result.summary.warn_count} WARN"
            )
        if result.aur and result.aur.failures:
            lines.append(f"  AUR: {len(result.aur.failures)} build failure(s):")
            for f in result.aur.failures:
                lines.append(f"    - {f.package}")
        if result.aur and result.aur.quarantine:
            active = [
                (pkg, ver, status, fails, retry)
                for pkg, ver, status, fails, retry in result.aur.quarantine.active
                if status != "resolved"
            ]
            if active:
                lines.append(f"  AUR quarantine: {len(active)} package(s):")
                for pkg, ver, status, fails, retry in active:
                    retry_str = f" — retry {retry}" if retry else ""
                    lines.append(f"    - {pkg} {ver} ({status}, {fails} failure(s){retry_str})")
                lines.append("    (see Preferences → AUR or `archward aur quarantine list`)")
        if result.summary.reboot_needed:
            lines.append("")
            lines.append("ACTION: Reboot to activate the new kernel.")

        self._details.setText("\n".join(lines) or "All checks passed.")

    def reset(self) -> None:
        self._banner.setText("Pipeline result")
        self._banner.setStyleSheet(
            "padding: 16px; background: #e2e3e5; color: #383d41; border-radius: 4px;"
        )
        self._details.setText("")
