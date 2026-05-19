"""`archward gates` — run pre-flight and gate checks standalone.

Runs the same checks the pipeline would run before any update: pacman DB
lock, cache-safety, Arch News, AUR quarantine FYI, snapshot age, and disk
space. Does NOT take a new snapshot or run any updates.
"""

from __future__ import annotations

from pathlib import Path

from archward.app import build_config
from archward.events import EventBus, PhaseEventKind
from archward.models.gate import GateResult, GateStatus
from archward.pipeline.gates import preflight_checks, run_gates
from archward.pipeline.snapshot import load_snapshot_from_disk
from archward.cli_subcommands.snapshot import _list_snapshot_paths


def _print_event(ev) -> None:
    if ev.kind == PhaseEventKind.PHASE_LOG and ev.message:
        print(f"  {ev.message}")


def cmd_gates(args, config_path: Path | None) -> int:
    cfg = build_config(config_path)
    bus = EventBus()
    bus.subscribe(_print_event)

    # ── Pre-flight (db.lck, cache-safety, arch-news, AUR quarantine FYI) ──
    print("pre-flight:")
    pre_results = preflight_checks(cfg, bus)
    for r in pre_results:
        print(f"  {r.name:<22} {r.status.upper():<6}  {r.message}")

    # ── Gates (snapshot age + disk space) ──
    print()
    print("gates:")
    snap_dir = cfg.general.snapshot_dir

    snap = None
    if getattr(args, "snapshot", None):
        snap_path = snap_dir / args.snapshot
        if snap_path.is_dir():
            snap = load_snapshot_from_disk(snap_path)
    if snap is None:
        paths = _list_snapshot_paths(snap_dir)
        if paths:
            snap = load_snapshot_from_disk(paths[0])

    if snap is None:
        # No snapshots — skip age check, inline the disk check.
        print(f"  {'snapshot-age':<22} {'SKIP':<6}  no snapshots found")
        from archward.system import disk as _disk
        free = _disk.free_gb("/")
        min_gb = cfg.gates.min_disk_gb
        disk_status = GateStatus.PASS if free >= min_gb else GateStatus.FAIL
        disk_msg = f"{free} GB free" if free >= min_gb else f"{free} GB free (need {min_gb} GB)"
        disk_result = GateResult(name="disk-space", status=disk_status, message=disk_msg)
        print(f"  {'disk-space':<22} {disk_status.upper():<6}  {disk_msg}")
        gate_results = [disk_result]
    else:
        gate_results = run_gates(cfg, snap, bus)
        for r in gate_results:
            print(f"  {r.name:<22} {r.status.upper():<6}  {r.message}")

    all_results = list(pre_results) + list(gate_results)
    if any(r.status is GateStatus.FAIL for r in all_results):
        return 1
    if any(r.status is GateStatus.WARN for r in all_results):
        return 2
    return 0
