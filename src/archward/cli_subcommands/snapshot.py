"""`archward snapshot {list,show,prune}` — snapshot inspection + retention.

All three commands work in a non-interactive TTY (no askpass, no GUI).
`list` and `show` are read-only. `prune` confirms before deleting unless
--yes is passed.
"""

from __future__ import annotations

import shutil as _shutil
import sys
from datetime import datetime
from pathlib import Path

from archward.app import build_config
from archward.pipeline.retention import prune_snapshots
from archward.pipeline.rollback import (
    critical_packages_with_kernel_fallback,
    list_snapshot_configs,
)
from archward.pipeline.snapshot import load_snapshot_from_disk


def _human_age(seconds: int) -> str:
    if seconds < 0:
        return "in the future"
    days = seconds // 86400
    if days > 0:
        return f"{days}d ago"
    hours = seconds // 3600
    if hours > 0:
        return f"{hours}h ago"
    minutes = seconds // 60
    if minutes > 0:
        return f"{minutes}m ago"
    return "just now"


def _list_snapshot_paths(snap_dir: Path) -> list[Path]:
    """Newest-first list of directories carrying a `.timestamp` marker."""
    if not snap_dir.exists():
        return []
    candidates = [
        p for p in snap_dir.iterdir()
        if p.is_dir() and (p / ".timestamp").exists()
    ]
    candidates.sort(key=lambda p: p.name, reverse=True)
    return candidates


# ── list ──────────────────────────────────────────────────────────────────


def cmd_list(args, config_path: Path | None) -> int:
    cfg = build_config(config_path)
    paths = _list_snapshot_paths(cfg.general.snapshot_dir)
    if not paths:
        print(f"no snapshots in {cfg.general.snapshot_dir}")
        return 0

    limit = None if args.all else args.limit
    visible = paths if limit is None else paths[:limit]

    # Column widths sized to terminal; truncate long kernel-release strings.
    cols = _shutil.get_terminal_size((100, 24)).columns
    kernel_w = max(12, min(28, cols - 60))

    header = f"{'snapshot':22}  {'age':>9}  {'distro':12}  {'kernel':{kernel_w}}  configs"
    print(header)
    print("-" * min(cols - 1, len(header)))

    for p in visible:
        snap = load_snapshot_from_disk(p)
        if snap is None:
            print(f"{p.name:22}  (incomplete — no .timestamp marker)")
            continue
        kernel = snap.meta.kernel_release[:kernel_w] or "unknown"
        distro = (snap.meta.distro_id or "?")[:12]
        n_configs = len(snap.config_files)
        print(
            f"{snap.meta.snapshot_id:22}  "
            f"{_human_age(snap.age_seconds):>9}  "
            f"{distro:12}  "
            f"{kernel:{kernel_w}}  "
            f"{n_configs}"
        )

    if not args.all and len(paths) > limit:
        print()
        print(f"... and {len(paths) - limit} older snapshot(s). Use --all to see them.")
    return 0


# ── show ──────────────────────────────────────────────────────────────────


def cmd_show(args, config_path: Path | None) -> int:
    cfg = build_config(config_path)
    snap_path = cfg.general.snapshot_dir / args.snapshot_id
    snap = load_snapshot_from_disk(snap_path)
    if snap is None:
        print(
            f"archward snapshot show: snapshot not found or incomplete: {snap_path}",
            file=sys.stderr,
        )
        return 3

    print(f"Snapshot: {snap.meta.snapshot_id}")
    print(f"  Path:        {snap.meta.path}")
    print(f"  Taken:       {snap.meta.created_at.isoformat(timespec='seconds')}  ({_human_age(snap.age_seconds)})")
    print(f"  Distro:      {snap.meta.distro_id or 'unknown'}")
    print(f"  Kernel:      {snap.meta.kernel_release or 'unknown'}")
    print(f"  AUR helper:  {snap.meta.helper_detected or '(none captured)'}")
    print()

    # Configs captured: list the relpath (suitable for `archward rollback config`).
    configs = list_snapshot_configs(snap_path)
    print(f"Configs captured ({len(configs)}):")
    if configs:
        for relpath, snap_file in configs:
            size = snap_file.stat().st_size if snap_file.exists() else 0
            print(f"  {relpath:40}  ({size:>7} B)")
    else:
        print("  (none)")
    print()

    # Critical packages with rollback-target detail.
    pkgs = critical_packages_with_kernel_fallback(
        snap_path,
        kernel_patterns=tuple(cfg.risk.kernel_patterns),
        kernel_pattern_exclude=tuple(cfg.risk.kernel_pattern_exclude),
    )
    print(f"Critical packages snapshotted ({len(pkgs)}):")
    if pkgs:
        for name, version in pkgs:
            print(f"  {name:30}  {version}")
    else:
        print("  (none — likely a pre-v0.2.0 snapshot)")

    return 0


# ── prune ─────────────────────────────────────────────────────────────────


def cmd_prune(args, config_path: Path | None) -> int:
    cfg = build_config(config_path)
    keep = args.keep if args.keep is not None else cfg.general.keep_snapshots

    snap_dir = cfg.general.snapshot_dir
    paths = _list_snapshot_paths(snap_dir)
    surplus = max(0, len(paths) - keep)

    print(f"snapshot dir: {snap_dir}")
    print(f"snapshots present: {len(paths)}")
    print(f"keep: {keep}")
    print(f"would delete: {surplus}")

    if surplus == 0:
        print("nothing to prune.")
        return 0

    print()
    print("to be deleted (oldest first):")
    for p in paths[keep:][::-1]:
        print(f"  {p.name}")

    if not args.yes:
        print()
        try:
            answer = input("proceed? [y/N] ").strip().lower()
        except EOFError:
            answer = ""
        if not answer.startswith("y"):
            print("aborted.")
            return 0

    removed = prune_snapshots(cfg, keep=keep)
    print(f"removed {len(removed)} snapshot(s).")
    return 0
