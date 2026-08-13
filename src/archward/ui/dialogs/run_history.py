"""Run History dialog.

Read-only browser over the per-run JSON records under
``state_dir()/runs/`` (written by run_record.RunRecorder): a table of
runs newest-first with a plain-text detail pane for the selected run.
Deliberately minimal v1 — no filtering, no deletion (pruning is automatic
via general.keep_runs), no rollback actions (those live in the Snapshot
Browser). The CLI twin is ``archward history``.
"""

from __future__ import annotations

import logging
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from archward.pipeline import run_record
from archward.pipeline.report import TAG_META

log = logging.getLogger(__name__)


def _fmt_duration(seconds: float | None) -> str:
    if seconds is None:
        return "?"
    if seconds < 60:
        return f"{seconds:.0f}s"
    minutes = int(seconds // 60)
    return f"{minutes}m{seconds - minutes * 60:02.0f}s"


def _fmt_started(raw: str | None) -> str:
    if not raw:
        return "?"
    try:
        return datetime.fromisoformat(raw).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return raw


def _result_label(doc: dict) -> str:
    result = doc.get("result") or {}
    tag = result.get("tag")
    if not tag:
        return "(crashed)"
    meta = TAG_META.get(tag)
    return meta.human if meta else tag


class RunHistoryDialog(QDialog):
    """Read-only run-history browser: table on top, detail pane below."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Run History")
        self.resize(860, 560)

        layout = QVBoxLayout(self)

        self._docs: list[dict] = []
        for path in run_record.list_run_files():
            doc = run_record.load_run(path)
            if doc is not None:
                self._docs.append(doc)

        if not self._docs:
            layout.addWidget(
                QLabel(
                    "No run history yet — records appear here after the next "
                    "update run (including dry runs)."
                )
            )
        else:
            self._table = QTableWidget(len(self._docs), 5, self)
            self._table.setHorizontalHeaderLabels(
                ["Run", "Started", "Result", "Duration", "Mode"]
            )
            self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
            self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
            self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
            self._table.verticalHeader().setVisible(False)
            for row, doc in enumerate(self._docs):
                cells = (
                    doc.get("run_id", "?"),
                    _fmt_started(doc.get("started")),
                    _result_label(doc),
                    _fmt_duration(doc.get("duration_s")),
                    doc.get("mode") or "?",
                )
                for col, text in enumerate(cells):
                    item = QTableWidgetItem(str(text))
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    self._table.setItem(row, col, item)
            self._table.resizeColumnsToContents()
            self._table.horizontalHeader().setStretchLastSection(True)
            self._table.itemSelectionChanged.connect(self._on_selection)
            layout.addWidget(self._table, 2)

            self._detail = QPlainTextEdit(self)
            self._detail.setReadOnly(True)
            layout.addWidget(self._detail, 3)

            self._table.selectRow(0)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.rejected.connect(self.reject)
        row = QHBoxLayout()
        row.addWidget(
            QLabel(f"Records: {run_record.runs_dir()}  (kept per general.keep_runs)")
        )
        row.addStretch(1)
        row.addWidget(buttons)
        layout.addLayout(row)

    # ── detail rendering ─────────────────────────────────────────────────

    def _on_selection(self) -> None:
        rows = self._table.selectionModel().selectedRows()
        if not rows:
            return
        doc = self._docs[rows[0].row()]
        self._detail.setPlainText(self._render_detail(doc))

    @staticmethod
    def _render_detail(doc: dict) -> str:
        result = doc.get("result") or {}
        lines: list[str] = []
        lines.append(f"Run {doc.get('run_id', '?')} — archward {doc.get('archward_version', '?')}")
        lines.append(
            f"Started {doc.get('started', '?')}   duration {_fmt_duration(doc.get('duration_s'))}"
            f"   mode {doc.get('mode', '?')}" + ("   --no-aur" if doc.get("no_aur") else "")
        )
        tag = result.get("tag")
        if tag:
            extra = ""
            if result.get("secondary_tags"):
                extra = "  (+ " + ", ".join(result["secondary_tags"]) + ")"
            lines.append(
                f"Result: {tag}{extra} — {result.get('fail_count', 0)} FAIL, "
                f"{result.get('warn_count', 0)} WARN"
                + ("; reboot needed" if result.get("reboot_needed") else "")
            )
        else:
            lines.append("Result: (none recorded — run crashed)")
        if doc.get("aborted_reason"):
            lines.append(f"Aborted: {doc['aborted_reason']}")
        snap = doc.get("snapshot_id")
        if snap:
            lines.append(f"Snapshot: {snap} (may be pruned — see Snapshot Browser)")
        lines.append("")

        phases = doc.get("phases") or []
        lines.append(f"Phases ({len(phases)}):")
        for ph in phases:
            status = ph.get("status") or "…"
            lines.append(
                f"  {ph.get('phase', '?'):16} {status:8} "
                f"{_fmt_duration(ph.get('duration_s')):>8}  {ph.get('message') or ''}"
            )
        if not phases:
            lines.append("  (none recorded)")

        err_lines = doc.get("update_error_lines") or []
        if err_lines:
            lines.append("")
            lines.append("Update errors:")
            lines.extend(f"  {line}" for line in err_lines)

        pending = doc.get("pending") or []
        if pending:
            high = sum(1 for p in pending if p.get("risk") == "high")
            lines.append("")
            lines.append(f"Packages in transaction: {len(pending)} ({high} HIGH risk)")

        aur = doc.get("aur")
        if aur:
            if aur.get("failures"):
                lines.append(
                    "AUR build failures: "
                    + ", ".join(f.get("package", "?") for f in aur["failures"])
                )
            if aur.get("quarantine"):
                lines.append(
                    "AUR quarantine active: "
                    + ", ".join(q.get("package", "?") for q in aur["quarantine"])
                )

        if doc.get("pacnew_count"):
            lines.append("")
            lines.append(f"Pacnew files pending: {doc['pacnew_count']}")

        return "\n".join(lines)
