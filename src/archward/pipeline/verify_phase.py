"""Verify phase — universal bucket only for Phase 1.

Bucket A (universal):
  1. Kernel match  — running kernel string contains installed kernel pkg version
  2. New .pacnew files since snapshot
  3. Disk space
  4. Pacman log scan
  5. Reboot-recommended log (if configured)

Bucket B (services): single check per cfg.services.to_verify entry.

Per audit G3: the pacman.log scan does NOT use sudo (log is mode 644 by default).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from archward.events import EventBus
from archward.models.config import ConfigModel
from archward.models.verify import CheckStatus, VerifyCheck, VerifyResult
from archward.pacman import query as pq
from archward.pacman.pacnew import find_pacnew_files
from archward.system import disk, kernel, services

log = logging.getLogger(__name__)

PHASE = "verify"


def _kernel_check() -> VerifyCheck:
    running = kernel.running_kernel()
    # Heuristic: pull the first installed `linux*` package (excluding -firmware/-docs)
    # and compare its version against the running kernel string. If multiple kernels are
    # installed (linux + linux-lts), we check the one whose name appears in the running
    # kernel release.
    candidates = []
    for name, version in pq.list_all():
        if name.startswith("linux") and not name.endswith(("-firmware", "-docs", "-headers")):
            candidates.append((name, version))

    if not candidates:
        return VerifyCheck(
            bucket="universal",
            name="kernel",
            status=CheckStatus.WARN,
            message="No linux* package detected — kernel match skipped",
        )

    # Try to find the kernel matching the running release; else compare first.
    best = candidates[0]
    for name, version in candidates:
        if name in running or running.startswith(re.sub(r"-.*$", "", version)):
            best = (name, version)
            break
    name, version = best
    # The package version may include a pkgrel like "7.0.6.arch1-1"; the running
    # kernel release like "7.0.6-arch1-1-cachyos-bore". Look for substring of the
    # leading version triplet.
    base = version.split("-")[0]
    if base in running:
        return VerifyCheck(
            bucket="universal",
            name="kernel",
            status=CheckStatus.PASS,
            message=f"running={running}, pkg={name} {version}",
        )
    return VerifyCheck(
        bucket="universal",
        name="kernel",
        status=CheckStatus.WARN,
        message=f"running={running} doesn't match {name} {version} — reboot likely needed",
    )


def _pacnew_check(cfg: ConfigModel, snapshot_path: Path) -> VerifyCheck:
    ts_path = snapshot_path / ".timestamp"
    since: int | None = None
    if ts_path.exists():
        try:
            since = int(ts_path.read_text().strip())
        except (OSError, ValueError):
            since = None
    new = find_pacnew_files(since_epoch=since)
    if not new:
        return VerifyCheck(
            bucket="universal", name="pacnew", status=CheckStatus.PASS, message="No new .pacnew files"
        )
    return VerifyCheck(
        bucket="universal",
        name="pacnew",
        status=CheckStatus.WARN,
        message=f"{len(new)} new .pacnew file(s) need merging",
        detail="\n".join(str(p) for p in new),
    )


def _disk_check() -> VerifyCheck:
    free = disk.free_gb("/")
    if free < 2:
        return VerifyCheck(
            bucket="universal",
            name="disk",
            status=CheckStatus.FAIL,
            message=f"Only {free}GB free on / — run sudo paccache -rk3",
        )
    if free < 5:
        return VerifyCheck(
            bucket="universal",
            name="disk",
            status=CheckStatus.WARN,
            message=f"{free}GB free on / — run sudo paccache -rk3 soon",
        )
    return VerifyCheck(
        bucket="universal",
        name="disk",
        status=CheckStatus.PASS,
        message=f"{free}GB free on /",
    )


def _pacman_log_check() -> VerifyCheck:
    err, warn, _samples = pq.scan_pacman_log(0, max_lines=500)
    if err > 0:
        return VerifyCheck(
            bucket="universal",
            name="pacman-log",
            status=CheckStatus.WARN,
            message=f"pacman.log: {err} error(s), {warn} warning(s) in last 500 lines",
        )
    if warn > 0:
        return VerifyCheck(
            bucket="universal",
            name="pacman-log",
            status=CheckStatus.WARN,
            message=f"pacman.log: {warn} warning(s) in last 500 lines",
        )
    return VerifyCheck(
        bucket="universal",
        name="pacman-log",
        status=CheckStatus.PASS,
        message="pacman.log: no recent errors/warnings",
    )


def _reboot_log_check(cfg: ConfigModel, snapshot_path: Path) -> VerifyCheck | None:
    log_path = cfg.verify.reboot_log
    if not log_path:
        return None
    p = Path(log_path)
    if not p.exists():
        return VerifyCheck(
            bucket="universal",
            name="reboot-log",
            status=CheckStatus.PASS,
            message=f"No reboot-recommended notification ({log_path} absent)",
        )
    ts_path = snapshot_path / ".timestamp"
    if not ts_path.exists():
        return None
    try:
        snap_ts = int(ts_path.read_text().strip())
        log_ts = int(p.stat().st_mtime)
    except (OSError, ValueError):
        return None
    if log_ts > snap_ts:
        return VerifyCheck(
            bucket="universal",
            name="reboot-log",
            status=CheckStatus.WARN,
            message=f"Reboot recommended (log updated since snapshot)",
            detail=f"See: {log_path}",
        )
    return VerifyCheck(
        bucket="universal",
        name="reboot-log",
        status=CheckStatus.PASS,
        message="Reboot-recommended log unchanged since snapshot",
    )


def _service_check(unit: str, severity_map: dict[str, str]) -> VerifyCheck:
    active = services.is_active(unit)
    sev = severity_map.get(unit, "critical")
    if active:
        return VerifyCheck(bucket="services", name=unit, status=CheckStatus.PASS, message="active")
    if sev == "watch":
        return VerifyCheck(
            bucket="services",
            name=unit,
            status=CheckStatus.WARN,
            message="not active (non-critical)",
        )
    return VerifyCheck(
        bucket="services",
        name=unit,
        status=CheckStatus.FAIL,
        message="not active",
    )


def _kernel_reboot_needed(check: VerifyCheck) -> bool:
    return check.name == "kernel" and check.status is CheckStatus.WARN


def run_verify(cfg: ConfigModel, snapshot_path: Path, bus: EventBus) -> VerifyResult:
    bus.emit_start(PHASE, "Verifying post-update state")
    checks: list[VerifyCheck] = []

    checks.append(_kernel_check())
    checks.append(_pacnew_check(cfg, snapshot_path))
    checks.append(_disk_check())
    checks.append(_pacman_log_check())
    reboot = _reboot_log_check(cfg, snapshot_path)
    if reboot is not None:
        checks.append(reboot)

    for unit in cfg.services.to_verify:
        checks.append(_service_check(unit, dict(cfg.services.severity)))

    for c in checks:
        bus.emit_log(PHASE, f"{c.status.value.upper():4s} {c.bucket}/{c.name}: {c.message}")

    fail_count = sum(1 for c in checks if c.status is CheckStatus.FAIL)
    warn_count = sum(1 for c in checks if c.status is CheckStatus.WARN)
    reboot_needed = (
        any(_kernel_reboot_needed(c) for c in checks)
        or any(c.name == "reboot-log" and c.status is CheckStatus.WARN for c in checks)
    )

    result = VerifyResult(
        checks=tuple(checks),
        fail_count=fail_count,
        warn_count=warn_count,
        reboot_needed=reboot_needed,
    )
    bus.emit_result(
        PHASE,
        f"verify: {fail_count} FAIL, {warn_count} WARN",
        payload={"result": result.model_dump(mode="json")},
    )
    return result
