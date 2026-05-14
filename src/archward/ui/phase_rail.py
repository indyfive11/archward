"""Left-side phase rail showing each pipeline phase + status.

Phase 4: unicode icons. Phase 5 may swap for animated spinner during running.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QListWidget, QListWidgetItem

# Phase name → display label. Order here defines display order in the rail.
_PHASES: tuple[tuple[str, str], ...] = (
    ("preflight", "Preflight"),
    ("snapshot", "Snapshot"),
    ("gates", "Gates"),
    ("risk", "Risk"),
    ("update_official", "Update (official)"),
    ("update_aur", "Update (AUR)"),
    ("pacnew", "Pacnew"),
    ("verify", "Verify"),
    ("result", "Result"),
)

# Status → unicode glyph.
_STATUS_GLYPHS = {
    "pending": "○",
    "running": "⟳",
    "pass": "●",
    "warn": "▲",
    "fail": "✕",
    "skipped": "–",
}


class PhaseRail(QListWidget):
    """Left rail with one row per pipeline phase, status updated by the controller."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._items: dict[str, QListWidgetItem] = {}
        self._status: dict[str, str] = {}
        self._build()

    def _build(self) -> None:
        for key, label in _PHASES:
            item = QListWidgetItem(f"{_STATUS_GLYPHS['pending']}  {label}")
            item.setData(Qt.ItemDataRole.UserRole, key)
            self.addItem(item)
            self._items[key] = item
            self._status[key] = "pending"

    def set_status(self, phase: str, status: str) -> None:
        """Update the icon for a single phase. Unknown phases are ignored."""
        item = self._items.get(phase)
        if item is None:
            return
        label = next((lbl for k, lbl in _PHASES if k == phase), phase)
        glyph = _STATUS_GLYPHS.get(status, "?")
        item.setText(f"{glyph}  {label}")
        self._status[phase] = status

    def reset(self) -> None:
        """Reset all phases to pending — used when re-running the pipeline."""
        for phase, _ in _PHASES:
            self.set_status(phase, "pending")

    def mark_unstarted_skipped(self) -> None:
        """Flip every phase still at 'pending' to 'skipped'.

        Called at pipeline completion so dry-run leaves phases that never
        executed (update_official, update_aur, pacnew, verify) showing as
        skipped rather than perpetually pending.
        """
        for phase in list(self._status):
            if self._status[phase] == "pending":
                self.set_status(phase, "skipped")
