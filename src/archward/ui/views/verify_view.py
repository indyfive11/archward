"""Verify phase content view — checks grouped by bucket."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHeaderView,
    QLabel,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from archward.models.verify import CheckStatus, VerifyResult
from archward.ui.theme import status_palette


def _status_colors():
    p = status_palette()
    return {
        CheckStatus.PASS: p.pass_fg,
        CheckStatus.WARN: p.warn_fg,
        CheckStatus.FAIL: p.fail_fg,
    }


class VerifyView(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._summary = QLabel("Verify")
        self._summary.setStyleSheet("font-weight: bold; padding: 8px;")
        self._tree = QTreeWidget()
        self._tree.setColumnCount(3)
        self._tree.setHeaderLabels(["Check", "Status", "Message"])
        self._tree.setRootIsDecorated(True)
        self._tree.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)

        layout = QVBoxLayout(self)
        layout.addWidget(self._summary)
        layout.addWidget(self._tree, stretch=1)

    def set_result(self, result: VerifyResult) -> None:
        self._tree.clear()
        self._summary.setText(
            f"Verify — {result.fail_count} FAIL, {result.warn_count} WARN, "
            f"{'reboot needed' if result.reboot_needed else 'no reboot'}"
        )

        colors = _status_colors()

        # Group by bucket.
        buckets: dict[str, list] = {"universal": [], "services": []}
        for c in result.checks:
            buckets.setdefault(c.bucket, []).append(c)

        for bucket, checks in buckets.items():
            if not checks:
                continue
            group = QTreeWidgetItem([f"{bucket}  ({len(checks)})", "", ""])
            font = group.font(0)
            font.setBold(True)
            group.setFont(0, font)
            for c in checks:
                child = QTreeWidgetItem([c.name, c.status.value.upper(), c.message])
                color = colors.get(c.status)
                if color is not None:
                    child.setForeground(1, color)
                if c.detail:
                    detail = QTreeWidgetItem(["", "", c.detail])
                    child.addChild(detail)
                group.addChild(child)
            self._tree.addTopLevelItem(group)
        self._tree.expandAll()

    def reset(self) -> None:
        self._tree.clear()
        self._summary.setText("Verify")
