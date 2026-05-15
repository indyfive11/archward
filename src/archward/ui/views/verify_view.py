"""Verify phase content view — checks grouped by bucket.

v0.3.1+ also surfaces user hook outcomes (pre_update + post_verify) as a
third bucket "hooks" so the user can see what their custom shell hooks did
without having to read the log pane. Each hook row shows the command (truncated),
exit code, status, and a one-line preview of the captured output.

v0.4.0 adds a 4th column "Action" — FAIL rows get a "What to do?" button
that opens a context-specific hint sourced from help_text.py. If no hint
is registered for the check, the column stays empty.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from archward.models.hook import HookResult, HookStatus
from archward.models.verify import CheckStatus, VerifyResult
from archward.ui.dialogs import help_text
from archward.ui.theme import brand_palette, status_palette

_MAX_CMD_CHARS = 70


def _hint_key_for(check_bucket: str, check_name: str) -> str:
    """Map a (bucket, name) verify-check to the help_text.HELP key.

    Universal checks are keyed by their name with `-` → `_`. Services and
    plugin checks are keyed by their bucket name (one hint serves the
    whole bucket — per-unit/per-plugin hints would explode the help dict
    for little gain).
    """
    if check_bucket == "services":
        return "service"
    if check_bucket == "plugin":
        return "plugin"
    return check_name.replace("-", "_")


def _status_colors():
    p = status_palette()
    return {
        CheckStatus.PASS: p.pass_fg,
        CheckStatus.WARN: p.warn_fg,
        CheckStatus.FAIL: p.fail_fg,
    }


def _hook_status_colors():
    p = status_palette()
    return {
        HookStatus.PASS: p.pass_fg,
        HookStatus.FAIL: p.fail_fg,
        HookStatus.TIMEOUT: p.fail_fg,
    }


def _truncate(s: str, n: int = _MAX_CMD_CHARS) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


class VerifyView(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._summary = QLabel("Verify")
        self._summary.setStyleSheet("font-weight: bold; padding: 8px;")
        self._tree = QTreeWidget()
        self._tree.setColumnCount(4)
        self._tree.setHeaderLabels(["Check", "Status", "Message", "Action"])
        self._tree.setRootIsDecorated(True)
        self._tree.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._tree.header().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)

        layout = QVBoxLayout(self)
        layout.addWidget(self._summary)
        layout.addWidget(self._tree, stretch=1)

        # Cached state for combined re-rendering when verify and hooks arrive
        # in separate payload events.
        self._verify_result: VerifyResult | None = None
        self._pre_hooks: tuple[HookResult, ...] = ()
        self._post_hooks: tuple[HookResult, ...] = ()

    # ── External setters (one per payload type) ────────────────────────────

    def set_result(self, result: VerifyResult) -> None:
        self._verify_result = result
        self._rerender()

    def set_pre_hooks(self, hooks: tuple[HookResult, ...]) -> None:
        self._pre_hooks = hooks
        self._rerender()

    def set_post_hooks(self, hooks: tuple[HookResult, ...]) -> None:
        self._post_hooks = hooks
        self._rerender()

    def reset(self) -> None:
        self._verify_result = None
        self._pre_hooks = ()
        self._post_hooks = ()
        self._tree.clear()
        self._summary.setText("Verify")

    # ── Rendering ──────────────────────────────────────────────────────────

    def _rerender(self) -> None:
        self._tree.clear()
        colors = _status_colors()
        hook_colors = _hook_status_colors()

        # Header summary (verify counts plus hook counts).
        bits = ["Verify"]
        if self._verify_result is not None:
            r = self._verify_result
            bits.append(
                f" — {r.fail_count} FAIL, {r.warn_count} WARN, "
                f"{'reboot needed' if r.reboot_needed else 'no reboot'}"
            )
        all_hooks = list(self._pre_hooks) + list(self._post_hooks)
        if all_hooks:
            hook_fail = sum(1 for h in all_hooks if h.status is not HookStatus.PASS)
            bits.append(f"  ·  hooks: {hook_fail}/{len(all_hooks)} failing")
        self._summary.setText("".join(bits))

        # Verify checks bucketed by their own bucket field. Seeding the
        # dict fixes the render order regardless of which bucket the
        # check list happens to surface first.
        if self._verify_result is not None:
            buckets: dict[str, list] = {"universal": [], "services": [], "plugin": []}
            for c in self._verify_result.checks:
                buckets.setdefault(c.bucket, []).append(c)
            for bucket, checks in buckets.items():
                if not checks:
                    continue
                group = QTreeWidgetItem([f"{bucket}  ({len(checks)})", "", "", ""])
                self._make_bold(group)
                for c in checks:
                    child = QTreeWidgetItem(
                        [c.name, c.status.value.upper(), c.message, ""]
                    )
                    color = colors.get(c.status)
                    if color is not None:
                        child.setForeground(1, color)
                    if c.detail:
                        child.addChild(QTreeWidgetItem(["", "", c.detail, ""]))
                    group.addChild(child)
                    self._maybe_attach_hint_button(child, c)
                self._tree.addTopLevelItem(group)

        # Hooks bucket — pre + post combined, with [pre]/[post] tag.
        if all_hooks:
            group = QTreeWidgetItem([f"hooks  ({len(all_hooks)})", "", "", ""])
            self._make_bold(group)
            for h in all_hooks:
                tag = "[pre]" if h.phase == "pre_update" else "[post]"
                cmd_label = f"{tag} {_truncate(h.command)}"
                # The output preview: last non-empty line, else "(no output)".
                preview = ""
                for line in reversed(h.output_lines):
                    if line.strip():
                        preview = line
                        break
                if not preview:
                    preview = "(no output)" if h.status is HookStatus.PASS else f"exit {h.exit_code}"
                status_label = h.status.value.upper()
                if h.status is HookStatus.TIMEOUT:
                    status_label = "TIMEOUT"
                child = QTreeWidgetItem([cmd_label, status_label, preview, ""])
                child.setToolTip(0, h.command)  # full command on hover
                color = hook_colors.get(h.status)
                if color is not None:
                    child.setForeground(1, color)
                # If there's more output than the preview, attach all lines as a child.
                if len(h.output_lines) > 1:
                    detail = QTreeWidgetItem(["", "", "\n".join(h.output_lines), ""])
                    child.addChild(detail)
                group.addChild(child)
            self._tree.addTopLevelItem(group)

        self._tree.expandAll()

    def _maybe_attach_hint_button(self, item: QTreeWidgetItem, check) -> None:
        """Place a 'What to do?' button in column 3 for FAIL rows that have
        a registered hint. Other rows (PASS/WARN, or unknown check names)
        get nothing in that column."""
        if check.status is not CheckStatus.FAIL:
            return
        hint_key = _hint_key_for(check.bucket, check.name)
        hint = help_text.get("verify_hint", hint_key)
        if not hint:
            return
        btn = QPushButton("What to do?")
        btn.setFlat(True)
        # Brand accent — text + hover background.
        _brand = brand_palette()
        btn.setStyleSheet(
            f"QPushButton {{ color: {_brand.accent_text_css}; "
            f"text-decoration: underline; padding: 2px 6px; }}"
            f"QPushButton:hover {{ background: {_brand.accent_bg_tint}; }}"
        )
        check_name = check.name  # captured for closure

        def _show_hint() -> None:
            box = QMessageBox(self)
            box.setWindowTitle(f"archward — {check_name}")
            box.setIcon(QMessageBox.Icon.Information)
            box.setText(f"<b>{check_name}</b> — what to do")
            box.setInformativeText(hint)
            box.setStandardButtons(QMessageBox.StandardButton.Ok)
            box.exec()

        btn.clicked.connect(_show_hint)
        self._tree.setItemWidget(item, 3, btn)

    @staticmethod
    def _make_bold(item: QTreeWidgetItem) -> None:
        from PySide6.QtGui import QBrush
        font = item.font(0)
        font.setBold(True)
        item.setFont(0, font)
        # Tint bucket headers in archward's brand teal so the groupings
        # pop visually without competing with the per-row status colors.
        accent = brand_palette().accent_fg
        item.setForeground(0, QBrush(accent))
