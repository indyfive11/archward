"""PKGBUILD review modal (F3, v0.4.0).

Shown per AUR package when `cfg.pacman.noconfirm=False`. Read-only
PKGBUILD body + Approve/Reject buttons. KISS: no syntax highlighting, no
diff against previous version — plain monospace pane. Both are v0.5
candidates if users ask.

The "fetch failed" state surfaces Skip / Retry / Cancel buttons instead
of Approve/Reject so the user can decide between dropping the package,
retrying the clone, or aborting the whole AUR review.
"""

from __future__ import annotations

from enum import Enum, auto

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)


class PkgbuildReviewResult(Enum):
    APPROVE = auto()
    REJECT = auto()       # skip just this package; continue with others
    RETRY = auto()        # re-fetch the PKGBUILD (fetch-failed state only)
    CANCEL_ALL = auto()   # abort the entire PKGBUILD review sequence


class PkgbuildReviewDialog(QDialog):
    """Modal — invoke `.review()` and check the returned enum."""

    def __init__(
        self,
        pkg: str,
        pkgbuild_content: str | None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._result = PkgbuildReviewResult.CANCEL_ALL
        self.setWindowTitle(f"archward — review PKGBUILD: {pkg}")
        self.resize(720, 520)

        layout = QVBoxLayout(self)

        if pkgbuild_content is None:
            # Fetch-failed branch.
            label = QLabel(
                f"<b>{pkg}</b> — failed to fetch PKGBUILD from the AUR.\n"
                "Network issue, missing package, or git timeout."
            )
            layout.addWidget(label)

            buttons = QDialogButtonBox()
            skip_btn = QPushButton("Skip this package")
            retry_btn = QPushButton("Retry")
            cancel_btn = QPushButton("Cancel review")
            buttons.addButton(skip_btn, QDialogButtonBox.ButtonRole.RejectRole)
            buttons.addButton(retry_btn, QDialogButtonBox.ButtonRole.ActionRole)
            buttons.addButton(cancel_btn, QDialogButtonBox.ButtonRole.DestructiveRole)
            layout.addWidget(buttons)

            skip_btn.clicked.connect(self._on_reject)
            retry_btn.clicked.connect(self._on_retry)
            cancel_btn.clicked.connect(self._on_cancel_all)
            return

        from archward.ui.theme import brand_palette
        _brand = brand_palette()
        header = QLabel(
            f"<b>{pkg}</b> — review the PKGBUILD before building. "
            "Approve to build, Reject to skip just this package (others continue)."
        )
        header.setWordWrap(True)
        header.setStyleSheet(
            f"padding: 8px 10px; "
            f"background: {_brand.accent_bg_tint}; "
            f"border-left: 3px solid {_brand.accent_border};"
        )
        layout.addWidget(header)

        viewer = QPlainTextEdit()
        viewer.setReadOnly(True)
        viewer.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        font = QFont("monospace")
        font.setStyleHint(QFont.StyleHint.TypeWriter)
        viewer.setFont(font)
        viewer.setPlainText(pkgbuild_content)
        # setPlainText already positions the cursor at offset 0; the
        # default scroll position is the top of the document.
        layout.addWidget(viewer, stretch=1)

        buttons = QDialogButtonBox()
        approve_btn = QPushButton("Approve")
        reject_btn = QPushButton("Reject (skip this package)")
        cancel_btn = QPushButton("Cancel review")
        buttons.addButton(approve_btn, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(reject_btn, QDialogButtonBox.ButtonRole.RejectRole)
        buttons.addButton(cancel_btn, QDialogButtonBox.ButtonRole.DestructiveRole)
        layout.addWidget(buttons)

        approve_btn.clicked.connect(self._on_approve)
        reject_btn.clicked.connect(self._on_reject)
        cancel_btn.clicked.connect(self._on_cancel_all)

        approve_btn.setDefault(True)
        approve_btn.setAutoDefault(True)
        # Enter approves; Esc cancels (Qt default for DialogButtonBox).
        self.setWindowModality(Qt.WindowModality.ApplicationModal)

    # ── Button slots ───────────────────────────────────────────────────────

    def _on_approve(self) -> None:
        self._result = PkgbuildReviewResult.APPROVE
        self.accept()

    def _on_reject(self) -> None:
        self._result = PkgbuildReviewResult.REJECT
        self.accept()

    def _on_retry(self) -> None:
        self._result = PkgbuildReviewResult.RETRY
        self.accept()

    def _on_cancel_all(self) -> None:
        self._result = PkgbuildReviewResult.CANCEL_ALL
        self.reject()

    # ── Result accessor ────────────────────────────────────────────────────

    def review(self) -> PkgbuildReviewResult:
        """Show modal, return the enum. Esc → CANCEL_ALL."""
        self.exec()
        return self._result
