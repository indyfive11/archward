"""Snapshot browser + granular rollback (v0.2.0).

Reads snapshots from `cfg.general.snapshot_dir`, surfaces each in a list
sorted newest-first, and renders a detail panel showing:

  - Meta (timestamp, distro, kernel, AUR helper, free disk at snapshot time)
  - Configs captured — per-file [View Diff] and [Restore] buttons
  - Critical packages — per-pkg [Downgrade to <ver>] when the version is
    available in `/var/cache/pacman/pkg/`

Every rollback action runs through confirmation modals (QMessageBox) and
hits `pipeline/rollback.py` for the actual mutation. The view never mutates
files directly.

Bulk rollback (restore-all-configs / downgrade-all-critical) is reserved
for v0.2.1 — the data model here is shaped so v0.2.1 is just iteration.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from archward.events import EventBus
from archward.models.config import ConfigModel
from archward.pacman import query as pq
from archward.pipeline.rollback import (
    RollbackOp,
    critical_packages_with_kernel_fallback,
    downgrade_package,
    find_package_in_cache,
    list_snapshot_configs,
    parse_critical_packages,
    restore_config,
)
from archward.privilege.sudo import SudoStrategy
from archward.ui.dialogs.diff_dialog import DiffDialog
from archward.ui.theme import status_palette

log = logging.getLogger(__name__)


def _read_timestamp(snap_dir: Path) -> datetime | None:
    """Parse <snap>/.timestamp (epoch seconds) → datetime, or None."""
    ts_path = snap_dir / ".timestamp"
    if not ts_path.exists():
        return None
    try:
        return datetime.fromtimestamp(int(ts_path.read_text().strip()))
    except (OSError, ValueError):
        return None


def _read_first_line(path: Path) -> str:
    """Return first stripped line of `path`, or empty string."""
    try:
        with open(path, encoding="utf-8") as f:
            return f.readline().strip()
    except OSError:
        return ""


def _capture_status(snap_file: Path, live_path: Path) -> tuple[str, str]:
    """Inline verification for a captured config.

    Returns (display_string, severity_key) where severity_key is one of
    'pass'/'warn'/'fail' — drives the row's foreground color via the theme.

    The check reads both files and byte-compares. For 1-5 KB /etc configs
    this is microseconds; the user sees a definitive "captured = live" tick
    in the browser instead of having to trust the pipeline blindly.
    """
    if not snap_file.exists():
        return ("✗ not captured", "fail")
    try:
        snap_data = snap_file.read_bytes()
    except OSError as e:
        return (f"? unreadable ({e.strerror})", "warn")
    if not snap_data:
        return ("⚠ 0 B (empty)", "warn")

    size = f"{len(snap_data):,} B"
    if not live_path.exists():
        return (f"✓ {size} (live absent)", "pass")
    try:
        live_data = live_path.read_bytes()
    except PermissionError:
        # /etc file may need sudo to read (e.g., 600-mode hardened drop-ins).
        # We can't byte-compare from user-context, so report size and trust
        # that the snapshot was captured under sudo at snapshot time.
        return (f"? {size} (live needs sudo)", "warn")
    except OSError as e:
        return (f"? {size} (live unreadable: {e.strerror})", "warn")

    if snap_data == live_data:
        return (f"✓ {size} identical to live", "pass")
    return (
        f"Δ {size} vs {len(live_data):,} B live  (system changed since snapshot)",
        "warn",
    )


class SnapshotBrowser(QDialog):
    """Modal browser over `cfg.general.snapshot_dir`."""

    def __init__(
        self,
        cfg: ConfigModel,
        strategy: SudoStrategy,
        bus: EventBus | None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Archward — Snapshot Browser")
        self.resize(1100, 700)

        self._cfg = cfg
        self._strategy = strategy
        self._bus = bus
        self._installed_versions: dict[str, str] = {}

        # ── Left: snapshot list ────────────────────────────────────────────
        self._snap_list = QListWidget()
        self._snap_list.setMinimumWidth(280)
        self._snap_list.itemSelectionChanged.connect(self._on_snapshot_selected)

        # ── Right: detail panel ────────────────────────────────────────────
        self._meta_label = QLabel("Select a snapshot to see details.")
        self._meta_label.setStyleSheet("padding: 8px;")
        self._meta_label.setWordWrap(True)

        self._configs_label = QLabel("")
        self._configs_label.setStyleSheet("font-weight: bold; padding: 8px 8px 4px 8px;")
        self._configs_tree = QTreeWidget()
        self._configs_tree.setColumnCount(3)
        self._configs_tree.setHeaderLabels(["Target", "Capture status", "Actions"])
        self._configs_tree.setRootIsDecorated(False)
        self._configs_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._configs_tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._configs_tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)

        self._pkgs_label = QLabel("")
        self._pkgs_label.setStyleSheet("font-weight: bold; padding: 8px 8px 4px 8px;")
        self._pkgs_tree = QTreeWidget()
        self._pkgs_tree.setColumnCount(4)
        self._pkgs_tree.setHeaderLabels(["Package", "Current", "Snapshot", "Action"])
        self._pkgs_tree.setRootIsDecorated(False)
        self._pkgs_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._pkgs_tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._pkgs_tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._pkgs_tree.header().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)

        right_box = QWidget()
        right_layout = QVBoxLayout(right_box)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(self._meta_label)
        right_layout.addWidget(self._configs_label)
        right_layout.addWidget(self._configs_tree, stretch=1)
        right_layout.addWidget(self._pkgs_label)
        right_layout.addWidget(self._pkgs_tree, stretch=1)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._snap_list)
        splitter.addWidget(right_box)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([300, 800])

        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        close.accepted.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.addWidget(splitter, stretch=1)
        layout.addWidget(close)

        self._populate_snapshots()

    # ── Snapshot list population ───────────────────────────────────────────

    def _populate_snapshots(self) -> None:
        self._snap_list.clear()
        snap_dir = self._cfg.general.snapshot_dir
        if not snap_dir.exists():
            self._meta_label.setText(f"No snapshots — {snap_dir} doesn't exist yet.")
            return
        candidates = sorted(
            (p for p in snap_dir.iterdir() if p.is_dir() and (p / ".timestamp").exists()),
            key=lambda p: p.name,
            reverse=True,
        )
        if not candidates:
            self._meta_label.setText(f"No snapshots in {snap_dir}.")
            return
        for snap in candidates:
            ts = _read_timestamp(snap)
            label = f"{snap.name}"
            if ts is not None:
                # ISO-ish + how-long-ago
                age = datetime.now() - ts
                if age.days > 0:
                    age_str = f"{age.days}d ago"
                elif age.seconds > 3600:
                    age_str = f"{age.seconds // 3600}h ago"
                else:
                    age_str = f"{age.seconds // 60}m ago"
                label = f"{snap.name}  ({age_str})"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, snap)
            self._snap_list.addItem(item)
        # Auto-select newest.
        self._snap_list.setCurrentRow(0)

    # ── Selection-driven detail ────────────────────────────────────────────

    def _on_snapshot_selected(self) -> None:
        items = self._snap_list.selectedItems()
        if not items:
            return
        snap_path = items[0].data(Qt.ItemDataRole.UserRole)
        self._render_detail(snap_path)

    def _render_detail(self, snap_path: Path) -> None:
        ts = _read_timestamp(snap_path)
        kernel = _read_first_line(snap_path / "system" / "kernel-running.txt")
        helper = _read_first_line(snap_path / "system" / "helper.txt")
        # Pull distro ID from os-release.txt (KEY=VALUE format).
        distro_id = ""
        osr = snap_path / "system" / "os-release.txt"
        if osr.exists():
            for line in osr.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("ID="):
                    distro_id = line.split("=", 1)[1].strip().strip('"')
                    break
        # Bootable kernel packages installed at snapshot time. v0.2.0+
        # snapshots record these in critical.txt; pre-v0.2.0 snapshots have
        # the data in all.txt and we recover it via fnmatch — same kernel
        # patterns the risk classifier uses.
        installed_at_snap = critical_packages_with_kernel_fallback(
            snap_path,
            kernel_patterns=tuple(self._cfg.risk.kernel_patterns),
            kernel_pattern_exclude=tuple(self._cfg.risk.kernel_pattern_exclude),
        )
        kernel_pkgs = [
            (name, version)
            for name, version in installed_at_snap
            if name.startswith("linux") and "firmware" not in name and "docs" not in name
        ]
        if kernel_pkgs:
            kernel_pkg_str = ", ".join(f"{n} {v}" for n, v in kernel_pkgs)
        else:
            kernel_pkg_str = "(no kernel packages found)"

        meta_lines = [
            f"<b>Snapshot:</b> {snap_path.name}",
            f"<b>Taken:</b> {ts.isoformat(timespec='seconds') if ts else 'unknown'}",
            f"<b>Distro:</b> {distro_id or 'unknown'}",
            f"<b>Kernel running (uname):</b> {kernel or 'unknown'}",
            f"<b>Kernel packages installed:</b> {kernel_pkg_str}",
            f"<b>AUR helper detected:</b> {helper or 'unknown'}",
            f"<b>Path:</b> <code>{snap_path}</code>",
        ]
        self._meta_label.setText("<br>".join(meta_lines))

        self._render_configs(snap_path)
        self._render_critical_packages(snap_path)

    def _render_configs(self, snap_path: Path) -> None:
        self._configs_tree.clear()
        configs = list_snapshot_configs(snap_path)
        self._configs_label.setText(f"Configs captured ({len(configs)}):")
        palette = status_palette()
        for live_rel, snap_file in configs:
            live_target = "/" + live_rel
            status_text, status_kind = _capture_status(snap_file, Path(live_target))
            item = QTreeWidgetItem([live_target, status_text, ""])
            item.setToolTip(1, f"Snapshot file: {snap_file}")
            color = {
                "pass": palette.pass_fg,
                "warn": palette.warn_fg,
                "fail": palette.fail_fg,
            }.get(status_kind)
            if color is not None:
                item.setForeground(1, color)
            self._configs_tree.addTopLevelItem(item)

            actions = self._row_widget()
            view_btn = self._small_btn("View Diff")
            restore_btn = self._small_btn("Restore")
            view_btn.clicked.connect(
                lambda *, lt=live_target, sf=snap_file: self._on_view_config_diff(lt, sf)
            )
            restore_btn.clicked.connect(
                lambda *, lt=live_target, sf=snap_file, sp=snap_path: self._on_restore_config(lt, sf, sp)
            )
            actions.layout().addWidget(view_btn)
            actions.layout().addWidget(restore_btn)
            actions.layout().addStretch(1)
            self._configs_tree.setItemWidget(item, 2, actions)

    def _render_critical_packages(self, snap_path: Path) -> None:
        self._pkgs_tree.clear()
        # Use the fallback-aware reader so old snapshots also surface their
        # kernel packages (recovered from all.txt when critical.txt didn't
        # record them).
        pkgs = critical_packages_with_kernel_fallback(
            snap_path,
            kernel_patterns=tuple(self._cfg.risk.kernel_patterns),
            kernel_pattern_exclude=tuple(self._cfg.risk.kernel_pattern_exclude),
        )
        self._pkgs_label.setText(f"Critical packages at snapshot time ({len(pkgs)}):")

        # Re-query live versions so we can show "current → snapshot" deltas.
        self._installed_versions = {n: v for n, v in pq.list_all()}

        for name, snap_version in pkgs:
            current = self._installed_versions.get(name, "(not installed)")
            same = current == snap_version
            item = QTreeWidgetItem([name, current, snap_version, ""])
            if same:
                item.setForeground(2, status_palette().pass_fg)
                action_widget = QLabel("  (unchanged)")
                action_widget.setStyleSheet("color: palette(text); font-style: italic; padding-left: 6px;")
                self._pkgs_tree.addTopLevelItem(item)
                self._pkgs_tree.setItemWidget(item, 3, action_widget)
                continue

            # Different — offer downgrade if cached.
            cache_path = find_package_in_cache(name, snap_version)
            actions = self._row_widget()
            if cache_path is None:
                lbl = QLabel("not in /var/cache/pacman/pkg/")
                lbl.setStyleSheet("color: palette(text); font-style: italic; padding-left: 6px;")
                actions.layout().addWidget(lbl)
            else:
                btn = self._small_btn(f"Downgrade to {snap_version}")
                btn.clicked.connect(
                    lambda *, n=name, v=snap_version, c=current, sp=snap_path:
                    self._on_downgrade(n, v, c, sp)
                )
                actions.layout().addWidget(btn)
            actions.layout().addStretch(1)
            self._pkgs_tree.addTopLevelItem(item)
            self._pkgs_tree.setItemWidget(item, 3, actions)

    # ── Action handlers ────────────────────────────────────────────────────

    def _on_view_config_diff(self, live_target: str, snap_file: Path) -> None:
        """Show DiffDialog of current /etc file vs snapshot version."""
        dlg = DiffDialog(Path(live_target), snap_file, self._strategy, parent=self)
        dlg.exec()

    def _on_restore_config(self, live_target: str, snap_file: Path, snap_path: Path) -> None:
        confirm = QMessageBox.question(
            self,
            "Restore config",
            f"Restore <b>{live_target}</b> from snapshot?<br><br>"
            f"Current file will be backed up to "
            f"<code>{live_target}.pre-rollback.bak</code> first.<br>"
            f"Ownership and mode of the live file will be preserved on the "
            f"restored copy.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        op = RollbackOp(
            kind="restore_config",
            target=live_target,
            from_version=None,
            to_version=None,
            snapshot_path=snap_path,
        )
        result = restore_config(op, snap_file, self._strategy)
        self._log_action(result.message)
        self._show_outcome("Restore config", result.success, result.message)

    def _on_downgrade(
        self, pkg_name: str, snap_version: str, current: str, snap_path: Path
    ) -> None:
        # Extra-loud warning for boot-critical packages.
        BOOT_CRITICAL = {"glibc", "systemd", "systemd-libs", "openssl"}
        is_kernel = pkg_name.startswith("linux") and not pkg_name.endswith(("firmware", "docs"))
        warning = ""
        if pkg_name in BOOT_CRITICAL:
            warning = (
                f"<br><br><b style='color:#c0392b;'>⚠ {pkg_name} is boot-critical.</b> "
                "If this downgrade leaves the system unbootable you may need "
                "to chroot from a USB to recover."
            )
        elif is_kernel:
            warning = (
                "<br><br><b style='color:#c0392b;'>⚠ Kernel downgrade.</b> "
                "Reboot will be required. If the older kernel fails to boot, "
                "use your bootloader's previous-entry menu to recover."
            )
        confirm = QMessageBox.question(
            self,
            f"Downgrade {pkg_name}",
            f"Downgrade <b>{pkg_name}</b>:<br><br>"
            f"&nbsp;&nbsp;current: <code>{current}</code><br>"
            f"&nbsp;&nbsp;target: <code>{snap_version}</code> (from snapshot)<br>"
            f"&nbsp;&nbsp;source: <code>/var/cache/pacman/pkg/</code><br>"
            f"<br>This runs <code>sudo pacman -U &lt;cached pkg&gt;</code>."
            f"{warning}<br><br>Proceed?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        op = RollbackOp(
            kind="downgrade_package",
            target=pkg_name,
            from_version=current,
            to_version=snap_version,
            snapshot_path=snap_path,
        )
        result = downgrade_package(op, self._strategy)
        self._log_action(result.message)
        self._show_outcome(f"Downgrade {pkg_name}", result.success, result.message)
        if result.success:
            # Re-render so current versions refresh.
            self._render_detail(snap_path)

    # ── Helpers ────────────────────────────────────────────────────────────

    def _row_widget(self) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(2, 2, 2, 2)
        h.setSpacing(4)
        return w

    def _small_btn(self, label: str) -> QPushButton:
        btn = QPushButton(label)
        btn.setStyleSheet("padding: 2px 8px;")
        return btn

    def _log_action(self, message: str) -> None:
        if self._bus is not None:
            self._bus.emit_log("rollback", message)
        log.info(message)

    def _show_outcome(self, title: str, success: bool, message: str) -> None:
        if success:
            QMessageBox.information(self, title, message)
        else:
            QMessageBox.critical(self, title, message)
