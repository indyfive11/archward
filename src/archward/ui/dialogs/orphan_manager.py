"""Orphan package manager dialog.

Lists orphaned packages (those installed as deps but no longer required),
lets the user select which to remove, runs a dry-run preview showing ALL
packages that pacman -Rs will actually touch (including cascaded deps),
asks for explicit confirmation, then removes in a background worker thread
(snapshot first if the most recent pre-snapshot is older than
_SNAPSHOT_THRESHOLD_MINUTES).
"""

from __future__ import annotations

import logging
import subprocess
import time

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from archward.models.config import ConfigModel
from archward.privilege.sudo import SudoStrategy

log = logging.getLogger(__name__)

_SNAPSHOT_THRESHOLD_MINUTES = 10


def _latest_pre_snapshot_age_minutes(cfg: ConfigModel) -> float | None:
    """Return age in minutes of the newest pre-snapshot, or None if none exists."""
    snap_dir = cfg.general.snapshot_dir
    if not snap_dir.exists():
        return None
    candidates = sorted(
        (
            p for p in snap_dir.iterdir()
            if p.is_dir() and (p / ".timestamp").exists()
            and not p.name.endswith("-after")
        ),
        key=lambda p: p.name,
        reverse=True,
    )
    if not candidates:
        return None
    try:
        ts = float((candidates[0] / ".timestamp").read_text().strip())
    except (ValueError, OSError):
        return None
    return (time.time() - ts) / 60


def _dry_run_removal(packages: list[str]) -> list[str]:
    """Return the full list of packages pacman -Rs would remove (dry-run).

    Uses `pacman -Rsp <pkgs>` (print mode) which outputs one package name
    per line without actually touching the system.  Returns an empty list
    if pacman is unavailable or the dry-run itself fails.
    """
    try:
        r = subprocess.run(
            ["pacman", "-Rsp", *packages],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        lines = [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]
        # pacman --print emits "pkgname-version" lines; strip the version so we
        # get plain names that match what's in the checkbox list.
        names: list[str] = []
        for line in lines:
            # Format is "pkgname-pkgver-pkgrel" — strip from the last hyphen-digit.
            # Simplest robust approach: strip from the second-to-last hyphen.
            parts = line.rsplit("-", 2)
            if len(parts) >= 2:
                names.append(parts[0])
            else:
                names.append(line)
        return names
    except Exception:  # noqa: BLE001
        return []


class _RemovalWorker(QThread):
    """Background worker: optional safety snapshot then pacman -Rs.

    Runs entirely off the main thread so archward's Wayland connection
    stays live and ksshaskpass can receive keyboard focus.
    """

    line_ready = Signal(str)
    finished_ok = Signal(bool)

    def __init__(
        self,
        cfg: ConfigModel,
        strategy: SudoStrategy,
        packages: list[str],
        needs_snap: bool,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._cfg = cfg
        self._strategy = strategy
        self._packages = packages
        self._needs_snap = needs_snap

    def run(self) -> None:
        if self._needs_snap:
            self.line_ready.emit("Taking safety snapshot…")
            try:
                from archward.events import EventBus
                from archward.pipeline.snapshot import take_snapshot
                take_snapshot(self._cfg, self._strategy, EventBus())
                self.line_ready.emit("Snapshot complete.")
            except Exception as e:  # noqa: BLE001
                log.exception("safety snapshot failed in orphan removal")
                self.line_ready.emit(f"Snapshot failed: {e}")
                self.finished_ok.emit(False)
                return

        self.line_ready.emit(f"Removing: {', '.join(self._packages)}")
        try:
            from archward.pacman.runner import run_capture
            rc, out, err = run_capture(
                ["pacman", "-Rs", "--noconfirm", *self._packages],
                strategy=self._strategy,
            )
        except Exception as e:  # noqa: BLE001
            log.exception("run_capture raised during orphan removal")
            self.line_ready.emit(f"Error: {e}")
            self.finished_ok.emit(False)
            return

        combined = (out + err).strip()
        for line in combined.splitlines():
            self.line_ready.emit(line)
        self.finished_ok.emit(rc == 0)


class _ScanWorker(QThread):
    scan_done = Signal(list)  # list[str] of orphan names

    def run(self) -> None:
        try:
            r = subprocess.run(
                ["pacman", "-Qdtq"],
                capture_output=True,
                text=True,
                check=False,
                timeout=15,
            )
            names = [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]
        except Exception:  # noqa: BLE001
            names = []
        self.scan_done.emit(names)


class OrphanManagerDialog(QDialog):
    def __init__(
        self,
        cfg: ConfigModel,
        strategy: SudoStrategy,
        orphans: list[str],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Orphan Package Manager")
        self.setMinimumSize(560, 420)
        self._cfg = cfg
        self._strategy = strategy
        self._worker: _RemovalWorker | None = None
        self._scan_worker: _ScanWorker | None = None

        # ── Description ───────────────────────────────────────────────────
        desc = QLabel(
            "These packages were installed as dependencies\n"
            "but nothing currently requires them."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("padding: 4px 0;")

        # ── Package list ──────────────────────────────────────────────────
        self._list = QListWidget()
        self._list.itemChanged.connect(self._on_item_changed)

        sel_row = QHBoxLayout()
        self._sel_all_btn = QPushButton("Select All")
        self._desel_all_btn = QPushButton("Deselect All")
        self._sel_all_btn.clicked.connect(self._select_all)
        self._desel_all_btn.clicked.connect(self._deselect_all)
        sel_row.addStretch(1)
        sel_row.addWidget(self._sel_all_btn)
        sel_row.addWidget(self._desel_all_btn)

        # ── Safety snapshot banner ────────────────────────────────────────
        self._snap_label = QLabel("")
        self._snap_label.setWordWrap(True)
        self._snap_label.setStyleSheet(
            "padding: 6px 10px; background: #fff3cd; color: #856404; border-radius: 4px;"
        )
        self._snap_label.setVisible(False)

        # ── Output log (shown after removal starts) ───────────────────────
        self._output = QPlainTextEdit()
        self._output.setReadOnly(True)
        self._output.setVisible(False)
        self._output.setMaximumHeight(160)

        # ── Footer buttons ────────────────────────────────────────────────
        self._cancel_btn = QPushButton("Cancel")
        self._remove_btn = QPushButton("Remove Selected…")
        self._remove_btn.setEnabled(False)
        self._cancel_btn.clicked.connect(self.reject)
        self._remove_btn.clicked.connect(self._on_remove_clicked)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_row.addWidget(self._cancel_btn)
        btn_row.addWidget(self._remove_btn)

        layout = QVBoxLayout(self)
        layout.addWidget(desc)
        layout.addWidget(self._list, stretch=1)
        layout.addLayout(sel_row)
        layout.addWidget(self._snap_label)
        layout.addWidget(self._output)
        layout.addLayout(btn_row)

        if orphans:
            self._populate(orphans)
        else:
            self._start_scan()

    # ── Close / cleanup ────────────────────────────────────────────────────

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._scan_worker and self._scan_worker.isRunning():
            self._scan_worker.wait(2000)
        if self._worker and self._worker.isRunning():
            self._worker.wait(5000)
        super().closeEvent(event)

    # ── List management ────────────────────────────────────────────────────

    def _populate(self, names: list[str]) -> None:
        self._list.clear()
        if not names:
            placeholder = QListWidgetItem("No orphaned packages found.")
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            self._list.addItem(placeholder)
            self._sel_all_btn.setEnabled(False)
            self._desel_all_btn.setEnabled(False)
            self._update_snap_banner()
            return
        for name in names:
            item = QListWidgetItem(name)
            item.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsUserCheckable
            )
            item.setCheckState(Qt.CheckState.Checked)
            self._list.addItem(item)
        self._update_remove_btn()
        self._update_snap_banner()

    def _start_scan(self) -> None:
        placeholder = QListWidgetItem("Scanning for orphaned packages…")
        placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
        self._list.addItem(placeholder)
        self._sel_all_btn.setEnabled(False)
        self._desel_all_btn.setEnabled(False)
        self._scan_worker = _ScanWorker(parent=self)
        self._scan_worker.scan_done.connect(self._on_scan_done)
        self._scan_worker.start()

    def _on_scan_done(self, names: list[str]) -> None:
        self._sel_all_btn.setEnabled(True)
        self._desel_all_btn.setEnabled(True)
        self._populate(names)

    def _select_all(self) -> None:
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                item.setCheckState(Qt.CheckState.Checked)

    def _deselect_all(self) -> None:
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                item.setCheckState(Qt.CheckState.Unchecked)

    def _checked_names(self) -> list[str]:
        names = []
        for i in range(self._list.count()):
            item = self._list.item(i)
            if (
                item.flags() & Qt.ItemFlag.ItemIsUserCheckable
                and item.checkState() == Qt.CheckState.Checked
            ):
                names.append(item.text())
        return names

    def _on_item_changed(self, _item) -> None:
        self._update_remove_btn()

    def _update_remove_btn(self) -> None:
        count = len(self._checked_names())
        self._remove_btn.setEnabled(count > 0)
        if count:
            self._remove_btn.setText(f"Remove Selected… ({count} pkg{'s' if count != 1 else ''})")
        else:
            self._remove_btn.setText("Remove Selected…")

    def _update_snap_banner(self) -> None:
        age = _latest_pre_snapshot_age_minutes(self._cfg)
        if age is None or age > _SNAPSHOT_THRESHOLD_MINUTES:
            self._snap_label.setText(
                "A safety snapshot will be taken before removal."
            )
        else:
            mins = int(age)
            self._snap_label.setText(
                f"Latest snapshot is {mins} minute{'s' if mins != 1 else ''} old — "
                "using it as the safety snapshot."
            )
        self._snap_label.setVisible(True)

    # ── Removal ────────────────────────────────────────────────────────────

    def _on_remove_clicked(self) -> None:
        packages = self._checked_names()
        if not packages:
            return

        # Dry-run: find out what pacman -Rs will ACTUALLY remove (may include
        # cascaded deps of the selected packages that are also orphaned).
        full_list = _dry_run_removal(packages)

        # If the dry-run found additional packages beyond what the user selected,
        # surface them explicitly so there are no surprises.
        cascaded = [p for p in full_list if p not in packages]

        if full_list:
            msg = _build_confirm_message(packages, cascaded)
        else:
            # dry-run failed (pacman unavailable?) — fall back to listing selected only.
            msg = (
                f"<b>Remove {len(packages)} package{'s' if len(packages) != 1 else ''}?</b>"
                f"<br><br>{', '.join(sorted(packages))}"
                "<br><br>This action cannot be undone (a safety snapshot will be taken first)."
            )

        box = QMessageBox(self)
        box.setWindowTitle("Confirm removal")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(msg)
        box.setStandardButtons(
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel
        )
        box.button(QMessageBox.StandardButton.Ok).setText("Remove")
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if box.exec() != QMessageBox.StandardButton.Ok:
            return

        # Confirmed — lock UI and start background worker.
        self._remove_btn.setEnabled(False)
        self._cancel_btn.setEnabled(False)
        self._output.setVisible(True)

        age = _latest_pre_snapshot_age_minutes(self._cfg)
        needs_snap = age is None or age > _SNAPSHOT_THRESHOLD_MINUTES

        self._worker = _RemovalWorker(
            self._cfg, self._strategy, packages, needs_snap, parent=self
        )
        self._worker.line_ready.connect(self._output.appendPlainText)
        self._worker.finished_ok.connect(self._on_removal_done)
        self._worker.start()

    def _on_removal_done(self, success: bool) -> None:
        self._cancel_btn.setText("Close")
        self._cancel_btn.setEnabled(True)
        if success:
            QMessageBox.information(
                self, "Done", "Selected packages removed successfully."
            )
        else:
            QMessageBox.warning(
                self, "Removal failed",
                "pacman -Rs returned a non-zero exit code. See the output log above."
            )


def _build_confirm_message(selected: list[str], cascaded: list[str]) -> str:
    """Build the confirmation message shown before removal."""
    sel_str = ", ".join(sorted(selected))
    lines = [
        f"<b>Remove {len(selected)} selected package{'s' if len(selected) != 1 else ''}?</b>",
        f"<br>{sel_str}",
    ]
    if cascaded:
        casc_str = ", ".join(sorted(cascaded))
        lines += [
            f"<br><br><b>pacman will also remove {len(cascaded)} cascaded "
            f"dep{'s' if len(cascaded) != 1 else ''} with no other dependants:</b>",
            f"<br>{casc_str}",
        ]
    lines.append("<br><br>A safety snapshot will be taken first.")
    return "".join(lines)
