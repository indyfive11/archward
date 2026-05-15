"""`archward rollback {config,package,all-configs,all-packages}`.

The TTY recovery toolset. The use case: a broken desktop after an update
left the user in tty1; they need to roll back to the last-known-good state
without the GUI's Snapshot Browser.

All four commands reuse the pure-Python rollback primitives in
`pipeline.rollback`. Boot-critical safety mirrors the GUI's Type-YES gate
via a `--confirm-boot-critical` flag PLUS a case-sensitive stdin YES
prompt. (`--yes` does NOT auto-confirm boot-critical — that's an
intentionally separate gate.)

Bulk variants auto-take a pre-rollback snapshot first, matching the
GUI's v0.2.2 rollback-of-rollback behavior.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from archward.app import build_config, build_sudo_strategy
from archward.events import EventBus
from archward.pipeline.rollback import (
    BOOT_CRITICAL,
    RollbackOp,
    apply_all_packages,
    critical_packages_with_kernel_fallback,
    downgrade_package,
    list_snapshot_configs,
    plan_bulk_package_apply,
    restore_all_configs,
    restore_config,
)
from archward.pipeline.snapshot import take_snapshot

log = logging.getLogger(__name__)


def _resolve_snapshot_path(cfg, snapshot_id: str) -> Path | None:
    """Resolve snapshot_id → existing snapshot dir, or print error + return None."""
    p = cfg.general.snapshot_dir / snapshot_id
    if not p.is_dir() or not (p / ".timestamp").exists():
        print(
            f"archward rollback: snapshot not found or incomplete: {p}",
            file=sys.stderr,
        )
        return None
    return p


def _stdin_yes(prompt: str = "Type YES (case-sensitive) to proceed: ") -> bool:
    """Block on stdin, return True only if user typed exactly 'YES'."""
    try:
        answer = input(prompt)
    except EOFError:
        return False
    return answer == "YES"


def _stdin_y_n(prompt: str) -> bool:
    """Casual y/N confirm; default no."""
    try:
        answer = input(prompt).strip().lower()
    except EOFError:
        return False
    return answer.startswith("y")


# ── config: restore one captured config ──────────────────────────────────


def cmd_config(args, config_path: Path | None) -> int:
    cfg = build_config(config_path)
    snap_path = _resolve_snapshot_path(cfg, args.snapshot_id)
    if snap_path is None:
        return 3
    strategy = build_sudo_strategy(cfg)

    # Resolve `filename` → (live target, snapshot file) via the canonical mapping.
    configs = list_snapshot_configs(snap_path)
    match = None
    for live_rel, snap_file in configs:
        # Accept either the bare snapshot filename ("mirrorlist") or the
        # full relpath ("etc/pacman.d/mirrorlist") — both should "just work."
        if snap_file.name == args.filename or live_rel == args.filename:
            match = (live_rel, snap_file)
            break

    if match is None:
        print(
            f"archward rollback config: no captured file matches {args.filename!r}. "
            "Use `archward snapshot show <id>` to see captured filenames.",
            file=sys.stderr,
        )
        return 2

    live_rel, snap_file = match
    live_target = "/" + live_rel
    op = RollbackOp(
        kind="restore_config",
        target=live_target,
        from_version=None,
        to_version=None,
        snapshot_path=snap_path,
    )

    print(f"restoring {live_target} from {snap_path.name}")
    result = restore_config(op, snap_file, strategy)
    if result.success:
        print(result.message)
        return 0
    print(f"FAIL: {result.message}", file=sys.stderr)
    return 1


# ── package: downgrade one package ───────────────────────────────────────


def cmd_package(args, config_path: Path | None) -> int:
    cfg = build_config(config_path)
    snap_path = _resolve_snapshot_path(cfg, args.snapshot_id)
    if snap_path is None:
        return 3
    strategy = build_sudo_strategy(cfg)

    # Find the package's snapshot version.
    pairs = critical_packages_with_kernel_fallback(
        snap_path,
        kernel_patterns=tuple(cfg.risk.kernel_patterns),
        kernel_pattern_exclude=tuple(cfg.risk.kernel_pattern_exclude),
    )
    snap_version = next((v for n, v in pairs if n == args.package), None)
    if snap_version is None:
        print(
            f"archward rollback package: {args.package!r} was not captured in "
            f"snapshot {args.snapshot_id}. Use `archward snapshot show <id>` to "
            "see captured packages.",
            file=sys.stderr,
        )
        return 2

    # Boot-critical gate.
    if args.package in BOOT_CRITICAL:
        if not args.confirm_boot_critical:
            print(
                f"archward rollback package: {args.package!r} is boot-critical. "
                f"Downgrading it can leave the system unbootable. Pass "
                f"--confirm-boot-critical and type YES to proceed.",
                file=sys.stderr,
            )
            return 2
        print(
            f"⚠ {args.package} is boot-critical. Downgrading to "
            f"{snap_version} could leave the system unbootable."
        )
        if not _stdin_yes():
            print("aborted.")
            return 0

    op = RollbackOp(
        kind="downgrade_package",
        target=args.package,
        from_version=None,
        to_version=snap_version,
        snapshot_path=snap_path,
    )

    print(f"downgrading {args.package} to {snap_version} ...")
    result = downgrade_package(op, strategy)
    if result.success:
        print(result.message)
        return 0
    print(f"FAIL: {result.message}", file=sys.stderr)
    return 1


# ── all-configs: bulk restore ────────────────────────────────────────────


def cmd_all_configs(args, config_path: Path | None) -> int:
    cfg = build_config(config_path)
    snap_path = _resolve_snapshot_path(cfg, args.snapshot_id)
    if snap_path is None:
        return 3
    strategy = build_sudo_strategy(cfg)

    configs = list_snapshot_configs(snap_path)
    if not configs:
        print(f"snapshot {args.snapshot_id} captured no restorable configs.")
        return 0

    print(f"will restore {len(configs)} config(s) from {args.snapshot_id}:")
    for live_rel, _ in configs:
        print(f"  /{live_rel}")

    if not args.yes:
        if not _stdin_y_n("\nproceed? [y/N] "):
            print("aborted.")
            return 0

    # Auto-pre-rollback snapshot (mirror GUI v0.2.2 behavior). Failures
    # here are logged but don't abort the rollback — the user explicitly
    # asked for it.
    print()
    print("taking pre-rollback snapshot ...")
    try:
        pre_snap = take_snapshot(cfg, strategy, EventBus())
        print(f"pre-rollback snapshot: {pre_snap.meta.snapshot_id}")
    except Exception as e:  # noqa: BLE001
        log.warning("pre-rollback snapshot failed: %s", e)
        print(f"warning: pre-rollback snapshot failed ({e}); continuing anyway.")

    print()
    print("restoring configs ...")
    result = restore_all_configs(snap_path, strategy)
    print(result.message)
    for live_target, reason in result.skipped:
        print(f"  SKIPPED {live_target}: {reason}", file=sys.stderr)
    return 0 if result.success else 1


# ── all-packages: bulk downgrade ─────────────────────────────────────────


def cmd_all_packages(args, config_path: Path | None) -> int:
    cfg = build_config(config_path)
    snap_path = _resolve_snapshot_path(cfg, args.snapshot_id)
    if snap_path is None:
        return 3
    strategy = build_sudo_strategy(cfg)

    changes, skipped = plan_bulk_package_apply(
        snap_path,
        kernel_patterns=tuple(cfg.risk.kernel_patterns),
        kernel_pattern_exclude=tuple(cfg.risk.kernel_pattern_exclude),
    )

    if not changes:
        print("nothing to apply — all packages already match snapshot.")
        if skipped:
            print("(some packages were skipped:)")
            for name, reason in skipped:
                print(f"  {name}: {reason}")
        return 0

    print(f"plan ({len(changes)} package(s)):")
    for name, current, target, _cache_path in changes:
        flag = "  ⚠ BOOT-CRITICAL" if name in BOOT_CRITICAL else ""
        print(f"  {name:30}  {current} → {target}{flag}")
    if skipped:
        print()
        print("skipped:")
        for name, reason in skipped:
            print(f"  {name}: {reason}")

    boot_critical_in_set = [n for n, _, _, _ in changes if n in BOOT_CRITICAL]
    if boot_critical_in_set and not args.confirm_boot_critical:
        print()
        print(
            "archward rollback all-packages: boot-critical packages in plan "
            f"({', '.join(boot_critical_in_set)}). Pass --confirm-boot-critical "
            "AND type YES to proceed.",
            file=sys.stderr,
        )
        return 2

    if boot_critical_in_set:
        print()
        print(
            f"⚠ Boot-critical packages will be downgraded: "
            f"{', '.join(boot_critical_in_set)}."
        )
        if not _stdin_yes():
            print("aborted.")
            return 0
    else:
        print()
        if not _stdin_y_n("proceed? [y/N] "):
            print("aborted.")
            return 0

    # Auto-pre-rollback snapshot.
    print()
    print("taking pre-rollback snapshot ...")
    try:
        pre_snap = take_snapshot(cfg, strategy, EventBus())
        print(f"pre-rollback snapshot: {pre_snap.meta.snapshot_id}")
    except Exception as e:  # noqa: BLE001
        log.warning("pre-rollback snapshot failed: %s", e)
        print(f"warning: pre-rollback snapshot failed ({e}); continuing anyway.")

    print()
    print(f"running pacman -U over {len(changes)} package(s) ...")
    result = apply_all_packages(
        snap_path, strategy,
        kernel_patterns=tuple(cfg.risk.kernel_patterns),
        kernel_pattern_exclude=tuple(cfg.risk.kernel_pattern_exclude),
        include_boot_critical=bool(boot_critical_in_set),
    )
    print(result.message)
    return 0 if result.success else 1
