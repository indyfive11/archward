"""Pre-flight + gate checks."""

from __future__ import annotations

import logging
from pathlib import Path

from archward.events import EventBus
from archward.models.config import ConfigModel
from archward.models.gate import GateResult, GateStatus
from archward.models.snapshot import Snapshot
from archward.pacman.runner import check_pacman_db_lock
from archward.system import disk

log = logging.getLogger(__name__)

PHASE_PREFLIGHT = "preflight"
PHASE_GATES = "gates"


def preflight_checks(bus: EventBus) -> list[GateResult]:
    """Pre-flight: pacman db.lck. (Single-instance lock is handled in app.py.)"""
    bus.emit_start(PHASE_PREFLIGHT, "Pre-flight checks")
    results: list[GateResult] = []

    locked, owner = check_pacman_db_lock()
    if locked:
        msg = f"pacman database is locked by {owner}"
        bus.emit_log(PHASE_PREFLIGHT, f"FAIL {msg}")
        # v0.4.1 (F13): give a concrete recovery hint for stale locks
        # (no live holder) — most users don't know the exact path or
        # that it's safe to remove once they've confirmed no pacman is
        # running. Live-lock case still asks them to wait.
        is_stale = owner is not None and "stale" in owner
        if is_stale:
            detail = (
                "/var/lib/pacman/db.lck is present but no live pacman "
                "process holds it (likely a previous run was killed). "
                "After confirming no pacman / pacman-key / makepkg is "
                "running, remove it manually: "
                "`sudo rm /var/lib/pacman/db.lck`. "
                "(archward never auto-removes the lock — a stale lock "
                "can indicate a corrupted transaction.)"
            )
        else:
            detail = (
                "/var/lib/pacman/db.lck is present and held by a live "
                "process. Wait for it to finish, then re-run archward."
            )
        results.append(
            GateResult(
                name="pacman-db-lock",
                status=GateStatus.FAIL,
                message=msg,
                detail=detail,
            )
        )
    else:
        bus.emit_log(PHASE_PREFLIGHT, "PASS pacman db is unlocked")
        results.append(
            GateResult(
                name="pacman-db-lock",
                status=GateStatus.PASS,
                message="pacman db is unlocked",
            )
        )

    bus.emit_result(
        PHASE_PREFLIGHT,
        "pre-flight OK" if not locked else "pre-flight FAILED",
        payload={"results": [r.model_dump(mode="json") for r in results]},
    )
    return results


def run_gates(cfg: ConfigModel, snapshot: Snapshot, bus: EventBus) -> list[GateResult]:
    """v1 gates: snapshot age + disk space."""
    bus.emit_start(PHASE_GATES, "Gate checks")
    results: list[GateResult] = []

    # Gate: snapshot age (Phase 1 — snapshot was just taken so age is ~0;
    # this gate matters when the user pre-snapshotted and is now invoking
    # the update phase separately).
    max_age = cfg.gates.snapshot_max_age_minutes * 60
    age = snapshot.age_seconds
    if age <= max_age:
        bus.emit_log(PHASE_GATES, f"PASS snapshot age {age // 60}m (max {cfg.gates.snapshot_max_age_minutes}m)")
        results.append(
            GateResult(
                name="snapshot-age",
                status=GateStatus.PASS,
                message=f"Snapshot {age // 60}m old",
            )
        )
    else:
        bus.emit_log(PHASE_GATES, f"FAIL snapshot age {age // 60}m exceeds max {cfg.gates.snapshot_max_age_minutes}m")
        results.append(
            GateResult(
                name="snapshot-age",
                status=GateStatus.FAIL,
                message=f"Snapshot is {age // 60}m old (max {cfg.gates.snapshot_max_age_minutes}m)",
                detail="Take a fresh snapshot before updating.",
            )
        )

    # Gate: disk space on /.
    free = disk.free_gb("/")
    if free >= cfg.gates.min_disk_gb:
        bus.emit_log(PHASE_GATES, f"PASS disk {free}GB free on / (min {cfg.gates.min_disk_gb}GB)")
        results.append(
            GateResult(
                name="disk-space",
                status=GateStatus.PASS,
                message=f"{free}GB free on /",
            )
        )
    else:
        bus.emit_log(PHASE_GATES, f"FAIL disk {free}GB free on / (min {cfg.gates.min_disk_gb}GB)")
        results.append(
            GateResult(
                name="disk-space",
                status=GateStatus.FAIL,
                message=f"Only {free}GB free on / (min {cfg.gates.min_disk_gb}GB)",
                detail="Run: sudo paccache -rk3",
                can_override=cfg.gates.allow_override,
            )
        )

    bus.emit_result(
        PHASE_GATES,
        "gates passed" if not any_fail(results) else "gates failed",
        payload={"results": [r.model_dump(mode="json") for r in results]},
    )
    return results


def any_fail(results: list[GateResult]) -> bool:
    return any(r.status is GateStatus.FAIL for r in results)
