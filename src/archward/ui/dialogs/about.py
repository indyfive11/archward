"""About archward — small modal showing icon, version, license, links.

KISS: read-only QLabel rows + the bundled shield icon at 96px. No HTML
templating, no fancy animation — just brand presence + identity facts.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
)

from archward import __version__
from archward.ui.icon import archward_icon
from archward.ui.theme import brand_palette


class AboutDialog(QDialog):
    """Help → About modal."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("About Archward")
        self.setModal(True)

        brand = brand_palette()

        # ── Icon (left) ────────────────────────────────────────────────────
        icon_label = QLabel()
        pix = archward_icon().pixmap(96, 96)
        icon_label.setPixmap(pix)
        icon_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        icon_label.setFixedWidth(110)

        # ── Identity block (right) ─────────────────────────────────────────
        title = QLabel(f"<b style='font-size: 18px;'>Archward {__version__}</b>")
        title.setStyleSheet(f"color: {brand.accent_text_css};")

        tagline = QLabel("Safe-update GUI for Arch-based Linux distributions")
        tagline.setStyleSheet("font-style: italic; padding-bottom: 8px;")

        body = QLabel(
            "<table cellpadding='2'>"
            "<tr><td><b>License:</b></td><td>GPL-3.0-or-later</td></tr>"
            "<tr><td><b>Source:</b></td>"
            "<td><a href='https://github.com/indyfive11/archward'>"
            "github.com/indyfive11/archward</a></td></tr>"
            "<tr><td><b>AUR:</b></td>"
            "<td><a href='https://aur.archlinux.org/packages/archward'>"
            "aur.archlinux.org/packages/archward</a></td></tr>"
            "</table>"
            "<p>archward wraps <code>pacman -Syu</code> in a snapshot → "
            "gate → classify → update → verify pipeline, so a failed "
            "update is always recoverable.</p>"
        )
        body.setOpenExternalLinks(True)
        body.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextBrowserInteraction
        )
        body.setWordWrap(True)

        identity = QVBoxLayout()
        identity.setSpacing(2)
        identity.addWidget(title)
        identity.addWidget(tagline)
        identity.addWidget(body)
        identity.addStretch(1)

        top_row = QHBoxLayout()
        top_row.addWidget(icon_label)
        top_row.addLayout(identity, stretch=1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.addLayout(top_row)
        layout.addWidget(buttons)

        self.resize(540, 280)
