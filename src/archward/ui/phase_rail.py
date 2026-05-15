"""Left-side phase rail showing each pipeline phase + status.

Phase 4: unicode icons. Phase 5 may swap for animated spinner during running.
v2: clickable for back-navigation — selecting a row emits `phase_clicked`
so the main window can switch the content area to that phase's view.

v0.4.0 branding: rows colored against the active brand palette —
running rows are bolded with a faint teal background tint; passed rows
get a teal glyph. Warn/fail/skipped keep their semantic status colors.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush
from PySide6.QtWidgets import QListWidget, QListWidgetItem

from archward.ui.theme import brand_palette, status_palette

# Phase name → display label. Order here defines display order in the rail.
_PHASES: tuple[tuple[str, str], ...] = (
    ("preflight", "Preflight"),
    ("snapshot", "Snapshot"),
    ("gates", "Gates"),
    ("risk", "Risk"),
    ("hooks_pre", "Pre-hooks"),
    ("update_official", "Update (official)"),
    ("update_aur", "Update (AUR)"),
    ("pacnew", "Pacnew"),
    ("verify", "Verify"),
    ("hooks_post", "Post-hooks"),
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
    """Left rail with one row per pipeline phase, status updated by the controller.

    Clicking a row emits `phase_clicked(phase_key)` so the parent can navigate
    the central content area to that phase's view. Useful after a run when
    PacnewView needs attention but the stack has already auto-advanced to the
    verify/result view.
    """

    phase_clicked = Signal(str)  # phase key (e.g. "pacnew", "risk", "verify")

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._items: dict[str, QListWidgetItem] = {}
        self._status: dict[str, str] = {}
        # Cache palettes once at construction; set_status fires many times
        # during heavy update-phase event traffic and re-querying the
        # application palette per call starves the paint queue.
        self._brand = brand_palette()
        self._status_pal = status_palette()
        self._build()
        self.itemClicked.connect(self._on_item_clicked)

    def _build(self) -> None:
        for key, label in _PHASES:
            item = QListWidgetItem(f"{_STATUS_GLYPHS['pending']}  {label}")
            item.setData(Qt.ItemDataRole.UserRole, key)
            self.addItem(item)
            self._items[key] = item
            self._status[key] = "pending"

    def set_status(self, phase: str, status: str) -> None:
        """Update the icon + brand color for a single phase. Unknown phases ignored."""
        item = self._items.get(phase)
        if item is None:
            return
        label = next((lbl for k, lbl in _PHASES if k == phase), phase)
        glyph = _STATUS_GLYPHS.get(status, "?")
        item.setText(f"{glyph}  {label}")
        self._status[phase] = status
        self._apply_row_style(item, status)

    def _apply_row_style(self, item: QListWidgetItem, status: str) -> None:
        """Color + bold the row according to status.

        running: bold + brand teal foreground (selection highlight from
                 Qt is the "you are here" cue; we don't paint a separate
                 background tint — that work compounds with the heavy
                 paint traffic during pacman -Syu and starves the GUI).
        pass:    brand teal foreground.
        warn:    status warn_fg. fail: status fail_fg. skipped: muted gray.
        pending: default — no color override.
        """
        font = item.font()
        font.setBold(status == "running")
        item.setFont(font)

        fg = None
        if status in ("running", "pass"):
            fg = self._brand.accent_fg
        elif status == "warn":
            fg = self._status_pal.warn_fg
        elif status == "fail":
            fg = self._status_pal.fail_fg
        elif status == "skipped":
            fg = self._status_pal.skipped_fg

        if fg is not None:
            item.setForeground(QBrush(fg))
        else:  # pending / unknown — clear any prior color
            item.setData(Qt.ItemDataRole.ForegroundRole, None)

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

    def select_phase(self, phase: str) -> None:
        """Programmatically select a row without firing phase_clicked.

        Used by the main window to sync the rail's highlighted row with the
        currently-shown stack page when the pipeline auto-advances views.
        """
        item = self._items.get(phase)
        if item is None:
            return
        self.blockSignals(True)
        try:
            self.setCurrentItem(item)
        finally:
            self.blockSignals(False)

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        phase = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(phase, str) and phase:
            self.phase_clicked.emit(phase)
