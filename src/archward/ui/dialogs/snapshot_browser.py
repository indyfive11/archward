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

import difflib
import logging
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QProgressDialog,
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
    BOOT_CRITICAL,
    BulkResult,
    RollbackOp,
    apply_all_packages,
    critical_packages_with_kernel_fallback,
    downgrade_package,
    find_package_in_cache,
    list_snapshot_configs,
    parse_critical_packages,
    plan_bulk_package_apply,
    restore_all_configs,
    restore_config,
)
from archward.pipeline.retention import prune_snapshots
from archward.pipeline.snapshot import take_snapshot
from archward.privilege.sudo import SudoStrategy
from archward.ui.dialogs.diff_dialog import DiffDialog, TextDiffDialog
from archward.ui.theme import status_palette

log = logging.getLogger(__name__)

_DELTA_CAP = 30  # rows shown inline before "View all" button appears


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


class _RollbackWorker(QThread):
    """Runs one rollback callable (restore_config or downgrade_package) off the
    main thread so the GUI stays responsive while `pacman -U` or the file
    operations finish. Emits the function's return value (a `RollbackResult`)
    on completion.

    Cancellation isn't supported — these operations are short (seconds) and
    interrupting `pacman -U` mid-transaction is unsafe by the same logic
    that keeps the main pipeline from killing pacman during updates.
    """

    finished_with_result = Signal(object)

    def __init__(self, fn: Callable[[], object], parent=None) -> None:
        super().__init__(parent)
        self._fn = fn
        self.result = None  # populated before the signal fires

    def run(self) -> None:
        try:
            self.result = self._fn()
        except Exception as e:  # noqa: BLE001 — must catch all so the QThread doesn't die silently
            log.exception("rollback worker raised")
            self.result = e
        self.finished_with_result.emit(self.result)


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


def _package_delta(pre_all: str, post_all: str) -> list[str]:
    """Return sorted change lines comparing two all.txt package lists.

    Lines format: `<name> <version>` (one per line).
    Output: `+ name ver` (added), `- name ver` (removed), `~ name old → new` (changed).
    """
    def _parse(text: str) -> dict[str, str]:
        result = {}
        for line in text.splitlines():
            parts = line.strip().split()
            if len(parts) >= 2:
                result[parts[0]] = parts[1]
        return result

    pre = _parse(pre_all)
    post = _parse(post_all)
    added = [f"+ {k} {post[k]}" for k in post if k not in pre]
    removed = [f"- {k} {pre[k]}" for k in pre if k not in post]
    changed = [
        f"~ {k} {pre[k]} → {post[k]}"
        for k in pre
        if k in post and pre[k] != post[k]
    ]
    return sorted(added + removed + changed)


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

        # Bulk action buttons (each row gets its own granular action; these
        # are the "do everything at once" shortcuts).
        self._bulk_configs_btn = QPushButton("Restore all configs from this snapshot…")
        self._bulk_configs_btn.setToolTip(
            "Restore every captured config to its /etc location. Each file "
            "gets a .pre-rollback.bak so per-file rollback is preserved."
        )
        self._bulk_configs_btn.clicked.connect(self._on_bulk_restore_configs)

        self._bulk_pkgs_btn = QPushButton("Apply all package versions from this snapshot…")
        self._bulk_pkgs_btn.setToolTip(
            "Single atomic `pacman -U` for every package whose snapshot version "
            "differs from current. Refuses boot-critical packages without a "
            "Type-YES override."
        )
        self._bulk_pkgs_btn.clicked.connect(self._on_bulk_apply_packages)

        self._delta_label = QLabel("")
        self._delta_label.setStyleSheet("font-weight: bold; padding: 8px 8px 4px 8px;")
        self._delta_label.hide()
        self._delta_viewer = QPlainTextEdit()
        self._delta_viewer.setReadOnly(True)
        self._delta_viewer.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        _mono = QFont("monospace")
        _mono.setStyleHint(QFont.StyleHint.TypeWriter)
        self._delta_viewer.setFont(_mono)
        self._delta_viewer.setMaximumHeight(160)
        self._delta_viewer.hide()
        self._delta_view_all_btn = QPushButton()
        self._delta_view_all_btn.hide()

        right_box = QWidget()
        right_layout = QVBoxLayout(right_box)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(self._meta_label)
        right_layout.addWidget(self._configs_label)
        right_layout.addWidget(self._configs_tree, stretch=1)
        right_layout.addWidget(self._bulk_configs_btn)
        right_layout.addWidget(self._pkgs_label)
        right_layout.addWidget(self._pkgs_tree, stretch=1)
        right_layout.addWidget(self._bulk_pkgs_btn)
        right_layout.addWidget(self._delta_label)
        right_layout.addWidget(self._delta_viewer)
        right_layout.addWidget(self._delta_view_all_btn)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._snap_list)
        splitter.addWidget(right_box)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([300, 800])

        prune_btn = QPushButton("Prune now…")
        prune_btn.setToolTip(
            "Delete old snapshots, keeping only the N most recent. "
            "Defaults to cfg.general.keep_snapshots."
        )
        prune_btn.clicked.connect(self._on_prune_clicked)

        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        close.accepted.connect(self.accept)

        bottom_row = QHBoxLayout()
        bottom_row.addWidget(prune_btn)
        bottom_row.addStretch(1)
        bottom_row.addWidget(close)

        layout = QVBoxLayout(self)
        layout.addWidget(splitter, stretch=1)
        layout.addLayout(bottom_row)

        self._populate_snapshots()

    # ── Prune action (F6, v0.4.0) ──────────────────────────────────────────

    def _on_prune_clicked(self) -> None:
        default_keep = self._cfg.general.keep_snapshots if self._cfg.general.keep_snapshots > 0 else 10
        keep, ok = QInputDialog.getInt(
            self,
            "archward — prune snapshots",
            "Keep how many of the newest snapshots? (older ones will be deleted)",
            default_keep,
            0,  # 0 = delete all
            10_000,
            1,
        )
        if not ok:
            return
        # Count what would be removed so the confirm shows a real number.
        snap_dir = self._cfg.general.snapshot_dir
        if snap_dir.exists():
            existing = [
                p for p in snap_dir.iterdir()
                if p.is_dir() and (p / ".timestamp").exists()
            ]
        else:
            existing = []
        would_delete = max(0, len(existing) - keep)
        if would_delete == 0:
            QMessageBox.information(
                self,
                "archward",
                f"Nothing to prune — {len(existing)} snapshot(s) present, keeping {keep}.",
            )
            return
        confirm = QMessageBox.question(
            self,
            "archward — confirm prune",
            f"Delete {would_delete} old snapshot(s), keeping the {keep} newest?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        removed = prune_snapshots(self._cfg, keep=keep)
        QMessageBox.information(
            self,
            "archward",
            f"Pruned {len(removed)} snapshot(s).",
        )
        self._populate_snapshots()

    # ── Snapshot list population ───────────────────────────────────────────

    def _populate_snapshots(self) -> None:
        self._snap_list.clear()
        snap_dir = self._cfg.general.snapshot_dir
        if not snap_dir.exists():
            self._meta_label.setText(
                "No snapshots yet.\n\nRun an update to create your first one."
            )
            return
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
            self._meta_label.setText(
                "No snapshots yet.\n\nRun an update to create your first one."
            )
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
        # v0.4.3: single source of truth for snapshot reconstruction.
        # load_snapshot_from_disk parses .timestamp + system/* and yields
        # a populated SnapshotMeta — same parser that the CLI uses.
        from archward.pipeline.snapshot import load_snapshot_from_disk

        snap = load_snapshot_from_disk(snap_path)
        if snap is None:
            ts = None
            kernel = ""
            helper = ""
            distro_id = ""
        else:
            ts = snap.meta.created_at
            kernel = snap.meta.kernel_release
            helper = snap.meta.helper_detected or ""
            distro_id = snap.meta.distro_id
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
        # v0.4.4 F4: surface incompleteness here, before the user tries a
        # rollback action (the action handlers also hard-refuse).
        from archward.pipeline.snapshot import validate_snapshot

        problems = validate_snapshot(snap_path)
        if problems:
            meta_lines.append(
                "<br><b style='color:#c0392b;'>⚠ Incomplete — cannot be "
                "used as a rollback source:</b>"
            )
            meta_lines.extend(
                f"<span style='color:#c0392b;'>&nbsp;&nbsp;&bull;&nbsp;{p}"
                "</span>"
                for p in problems
            )
        self._meta_label.setText("<br>".join(meta_lines))

        self._render_configs(snap_path)
        self._render_critical_packages(snap_path)
        self._render_post_delta(snap_path)

    def _hide_delta(self) -> None:
        self._delta_label.hide()
        self._delta_viewer.hide()
        self._delta_view_all_btn.hide()

    def _render_post_delta(self, snap_path: Path) -> None:
        """Show package delta between this pre-snapshot and its -after sibling."""
        after_path = snap_path.parent / f"{snap_path.name}-after"
        if not after_path.is_dir():
            self._hide_delta()
            return

        pre_all_path = snap_path / "packages" / "all.txt"
        post_all_path = after_path / "packages" / "all.txt"
        if not pre_all_path.exists() or not post_all_path.exists():
            self._hide_delta()
            return

        pre_text = pre_all_path.read_text()
        post_text = post_all_path.read_text()
        delta = _package_delta(pre_text, post_text)
        n = len(delta)

        self._delta_label.setText(
            f"Post-update delta ({n} package change{'s' if n != 1 else ''}):"
        )
        self._delta_label.show()
        self._delta_viewer.setPlainText("\n".join(delta[:_DELTA_CAP]))
        self._delta_viewer.show()

        try:
            self._delta_view_all_btn.clicked.disconnect()
        except RuntimeError:
            pass
        if n > _DELTA_CAP:
            diff_text = "".join(difflib.unified_diff(
                pre_text.splitlines(keepends=True),
                post_text.splitlines(keepends=True),
                fromfile=f"pre-snapshot ({snap_path.name})",
                tofile=f"post-snapshot ({after_path.name})",
                n=3,
            ))
            self._delta_view_all_btn.setText(f"View all {n} changes…")
            self._delta_view_all_btn.clicked.connect(
                lambda: self._on_view_full_delta(diff_text, snap_path.name)
            )
            self._delta_view_all_btn.show()
        else:
            self._delta_view_all_btn.hide()

    def _on_view_full_delta(self, diff_text: str, snap_name: str) -> None:
        TextDiffDialog(
            diff_text=diff_text,
            title=f"Post-update package diff — {snap_name}",
            header_html=f"<b>{snap_name}</b> → <b>{snap_name}-after</b>",
            parent=self,
        ).exec()

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

            # Different — offer the rollback if cached. The verb reflects
            # direction: "Downgrade" when the snapshot's version is older
            # than current, "Upgrade" when it's newer (post-rollback state
            # where you want to restore the current latest from a snapshot
            # taken before the previous rollback). pacman -U handles both.
            cache_path = find_package_in_cache(name, snap_version)
            actions = self._row_widget()
            if cache_path is None:
                lbl = QLabel("not in /var/cache/pacman/pkg/")
                lbl.setStyleSheet("color: palette(text); font-style: italic; padding-left: 6px;")
                actions.layout().addWidget(lbl)
            else:
                cmp = pq.vercmp(current, snap_version)
                verb = "Downgrade" if cmp > 0 else "Upgrade"
                btn = self._small_btn(f"{verb} to {snap_version}")
                btn.clicked.connect(
                    lambda *, n=name, v=snap_version, c=current, sp=snap_path:
                    self._on_apply_pkg_version(n, v, c, sp)
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

    def _refuse_if_incomplete(self, snap_path: Path) -> bool:
        """True (and shows a refusal dialog) when the snapshot can't back
        a rollback. v0.4.4 F4: stop before any pacman/config mutation
        instead of failing cryptically half-way through a restore."""
        from archward.pipeline.snapshot import validate_snapshot

        problems = validate_snapshot(snap_path)
        if not problems:
            return False
        QMessageBox.critical(
            self,
            "Snapshot incomplete",
            f"Snapshot <code>{snap_path.name}</code> cannot be used as a "
            "rollback source:<br><br>"
            + "<br>".join(f"&bull;&nbsp;{p}" for p in problems)
            + "<br><br>Pick another snapshot from the list.",
        )
        return True

    def _on_restore_config(self, live_target: str, snap_file: Path, snap_path: Path) -> None:
        if self._refuse_if_incomplete(snap_path):
            return
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
        self._run_off_thread(
            fn=lambda: restore_config(op, snap_file, self._strategy),
            title="Restore config",
            progress_label=f"Restoring {live_target} from snapshot…",
            on_done=lambda result: self._handle_restore_done(result, snap_path),
        )

    def _handle_restore_done(self, result, snap_path: Path) -> None:
        if isinstance(result, Exception):
            self._show_outcome("Restore config", False, f"Worker error: {result}")
            return
        self._log_action(result.message)
        self._show_outcome("Restore config", result.success, result.message)
        if result.success:
            # Re-render to update the capture-status indicator (file may
            # now be identical/different from live again).
            self._render_detail(snap_path)

    def _on_apply_pkg_version(
        self, pkg_name: str, snap_version: str, current: str, snap_path: Path
    ) -> None:
        """Apply a package version from the snapshot — direction-aware.

        Uses vercmp to decide whether it's a downgrade (current > snap) or
        upgrade (current < snap). The boot-critical / kernel warnings only
        fire for downgrades; upgrading to a newer version is generally safer.
        """
        if self._refuse_if_incomplete(snap_path):
            return
        is_downgrade = pq.vercmp(current, snap_version) > 0
        verb = "Downgrade" if is_downgrade else "Upgrade"
        verb_past = "downgraded" if is_downgrade else "upgraded"

        # Extra-loud warning ONLY for downgrades of boot-critical / kernel
        # packages — upgrading toward current latest is safe in those cases.
        BOOT_CRITICAL = {"glibc", "systemd", "systemd-libs", "openssl"}
        is_kernel = pkg_name.startswith("linux") and not pkg_name.endswith(("firmware", "docs"))
        warning = ""
        if is_downgrade and pkg_name in BOOT_CRITICAL:
            warning = (
                f"<br><br><b style='color:#c0392b;'>⚠ {pkg_name} is boot-critical.</b> "
                "If this downgrade leaves the system unbootable you may need "
                "to chroot from a USB to recover."
            )
        elif is_downgrade and is_kernel:
            warning = (
                "<br><br><b style='color:#c0392b;'>⚠ Kernel downgrade.</b> "
                "Reboot will be required. If the older kernel fails to boot, "
                "use your bootloader's previous-entry menu to recover."
            )

        confirm = QMessageBox.question(
            self,
            f"{verb} {pkg_name}",
            f"{verb} <b>{pkg_name}</b>:<br><br>"
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
            kind="downgrade_package",  # internal kind label — pacman -U covers both directions
            target=pkg_name,
            from_version=current,
            to_version=snap_version,
            snapshot_path=snap_path,
        )
        self._run_off_thread(
            fn=lambda: downgrade_package(op, self._strategy),
            title=f"{verb} {pkg_name}",
            progress_label=f"Running pacman -U on {pkg_name} {snap_version}…",
            on_done=lambda result: self._handle_pkg_apply_done(
                result, pkg_name, snap_version, verb, verb_past, snap_path
            ),
        )

    def _handle_pkg_apply_done(
        self,
        result,
        pkg_name: str,
        snap_version: str,
        verb: str,
        verb_past: str,
        snap_path: Path,
    ) -> None:
        if isinstance(result, Exception):
            self._show_outcome(f"{verb} {pkg_name}", False, f"Worker error: {result}")
            return
        if result.success:
            display_message = f"{verb_past} {pkg_name} to {snap_version}"
        else:
            display_message = result.message
        self._log_action(display_message)
        self._show_outcome(f"{verb} {pkg_name}", result.success, display_message)
        if result.success:
            self._render_detail(snap_path)

    # ── Bulk action handlers ───────────────────────────────────────────────

    def _current_snapshot_path(self) -> Path | None:
        items = self._snap_list.selectedItems()
        if not items:
            return None
        return items[0].data(Qt.ItemDataRole.UserRole)

    def _on_bulk_restore_configs(self) -> None:
        snap_path = self._current_snapshot_path()
        if snap_path is None:
            return
        if self._refuse_if_incomplete(snap_path):
            return
        configs = list_snapshot_configs(snap_path)
        if not configs:
            QMessageBox.information(
                self, "Restore all configs", "No configs captured in this snapshot."
            )
            return

        body_lines = [
            f"Restore <b>{len(configs)}</b> config(s) from snapshot "
            f"<code>{snap_path.name}</code>?<br><br>"
            "Each file will be backed up to "
            "<code>&lt;file&gt;.pre-rollback.bak</code> before overwriting, "
            "so per-file rollback is preserved."
            "<br><br><b>Files:</b>",
        ]
        for live_rel, _ in configs:
            body_lines.append(f"&nbsp;&nbsp;/{live_rel}")
        confirm = QMessageBox.question(
            self,
            "Restore all configs",
            "<br>".join(body_lines),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        self._run_off_thread(
            fn=lambda: restore_all_configs(snap_path, self._strategy),
            title="Restore all configs",
            progress_label=f"Restoring {len(configs)} config(s)…",
            on_done=lambda result: self._handle_bulk_done(
                result, snap_path, kind="Restore all configs"
            ),
        )

    def _on_bulk_apply_packages(self) -> None:
        snap_path = self._current_snapshot_path()
        if snap_path is None:
            return
        if self._refuse_if_incomplete(snap_path):
            return

        changes, skipped = plan_bulk_package_apply(
            snap_path,
            kernel_patterns=tuple(self._cfg.risk.kernel_patterns),
            kernel_pattern_exclude=tuple(self._cfg.risk.kernel_pattern_exclude),
        )
        if not changes:
            QMessageBox.information(
                self,
                "Apply all packages",
                "Nothing to apply — every captured package is already at its "
                "snapshot version.",
            )
            return

        # Compose a preview of what will change.
        boot_critical_in_set = sorted(
            n for n, _c, _t, _p in changes if n in BOOT_CRITICAL
        )
        body_lines: list[str] = [
            f"Apply <b>{len(changes)}</b> package version change(s) "
            f"from snapshot <code>{snap_path.name}</code>?<br><br>"
            "This runs a single atomic <code>pacman -U</code> with every "
            "package as one transaction.<br><br>"
            "A fresh snapshot of the current state will be taken first so "
            "you can rollback this rollback if needed."
            "<br><br><b>Changes:</b>",
        ]
        for name, current, target, _path in changes[:25]:
            body_lines.append(
                f"&nbsp;&nbsp;{name}:&nbsp;&nbsp;<code>{current}</code> → <code>{target}</code>"
            )
        if len(changes) > 25:
            body_lines.append(f"&nbsp;&nbsp;… and {len(changes) - 25} more")
        if skipped:
            body_lines.append(f"<br><b>Skipped ({len(skipped)}):</b>")
            for name, reason in skipped[:10]:
                body_lines.append(f"&nbsp;&nbsp;{name} — {reason}")
            if len(skipped) > 10:
                body_lines.append(f"&nbsp;&nbsp;… and {len(skipped) - 10} more")
        if boot_critical_in_set:
            body_lines.append(
                "<br><b style='color:#c0392b;'>⚠ Boot-critical packages in set:</b>"
            )
            for n in boot_critical_in_set:
                body_lines.append(f"&nbsp;&nbsp;{n}")
            body_lines.append(
                "<br>Downgrading these can leave the system unbootable. "
                "You will be asked to type YES to confirm."
            )

        confirm = QMessageBox.question(
            self,
            "Apply all packages",
            "<br>".join(body_lines),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        # Boot-critical: require Type-YES confirmation as a friction step.
        include_boot_critical = False
        if boot_critical_in_set:
            text, ok = QInputDialog.getText(
                self,
                "Confirm boot-critical downgrade",
                f"You are about to downgrade boot-critical packages:\n"
                f"  {', '.join(boot_critical_in_set)}\n\n"
                "Type YES (uppercase) to confirm:",
            )
            if not ok or text != "YES":
                return
            include_boot_critical = True

        # Auto-snapshot before bulk apply so rollback-of-rollback is possible.
        self._log_action("taking pre-rollback snapshot")
        try:
            pre_snap = take_snapshot(self._cfg, self._strategy, self._bus or EventBus())
            self._log_action(f"pre-rollback snapshot at {pre_snap.meta.path}")
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(
                self,
                "Pre-rollback snapshot failed",
                f"Could not take a fresh snapshot before bulk apply:\n\n{e}\n\n"
                "Aborting — bulk operations require an undo target.",
            )
            return

        self._run_off_thread(
            fn=lambda: apply_all_packages(
                snap_path,
                self._strategy,
                kernel_patterns=tuple(self._cfg.risk.kernel_patterns),
                kernel_pattern_exclude=tuple(self._cfg.risk.kernel_pattern_exclude),
                include_boot_critical=include_boot_critical,
            ),
            title="Apply all packages",
            progress_label=f"Running pacman -U on {len(changes)} package(s)…",
            on_done=lambda result: self._handle_bulk_done(
                result, snap_path, kind="Apply all packages"
            ),
        )

    def _handle_bulk_done(self, result: object, snap_path: Path, kind: str) -> None:
        if isinstance(result, Exception):
            self._show_outcome(kind, False, f"Worker error: {result}")
            return
        assert isinstance(result, BulkResult)
        self._log_action(f"{kind}: {result.message}")

        summary_lines = [result.message]
        if result.changed:
            summary_lines.append(f"<br><b>Applied ({len(result.changed)}):</b>")
            for tup in result.changed[:20]:
                if tup[1] and tup[2]:  # package: (name, from, to)
                    summary_lines.append(
                        f"&nbsp;&nbsp;{tup[0]}: <code>{tup[1]}</code> → <code>{tup[2]}</code>"
                    )
                else:  # config: (path, "", "")
                    summary_lines.append(f"&nbsp;&nbsp;{tup[0]}")
            if len(result.changed) > 20:
                summary_lines.append(f"&nbsp;&nbsp;… and {len(result.changed) - 20} more")
        if result.skipped:
            summary_lines.append(f"<br><b>Skipped ({len(result.skipped)}):</b>")
            for name, reason in result.skipped[:10]:
                summary_lines.append(f"&nbsp;&nbsp;{name} — {reason}")

        self._show_outcome(kind, result.success, "<br>".join(summary_lines))
        if result.success:
            self._render_detail(snap_path)

    # ── Off-thread runner with progress dialog ─────────────────────────────

    def _run_off_thread(
        self,
        *,
        fn: Callable[[], object],
        title: str,
        progress_label: str,
        on_done: Callable[[object], None],
    ) -> None:
        """Run `fn` on a _RollbackWorker, show an indeterminate QProgressDialog
        until it finishes, then dispatch the result to `on_done` on the main
        thread.

        Keeps the GUI responsive while pacman -U or the file ops run. The
        progress dialog has no Cancel button — these operations should not
        be interrupted mid-flight.
        """
        progress = QProgressDialog(progress_label, "", 0, 0, self)
        progress.setWindowTitle(title)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setCancelButton(None)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.show()

        worker = _RollbackWorker(fn, parent=self)

        def _on_finished(result: object) -> None:
            progress.close()
            on_done(result)
            worker.deleteLater()

        worker.finished_with_result.connect(_on_finished)
        worker.start()

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
