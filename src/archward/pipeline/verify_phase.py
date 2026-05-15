"""Verify phase.

Bucket A (universal):
  1. Kernel match  — running kernel string contains installed kernel pkg version
  2. New .pacnew files since snapshot
  3. Disk space
  4. Pacman log scan
  5. Reboot-recommended log (if configured)

Bucket B (services): single check per cfg.services.to_verify entry.

Bucket C (plugin): third-party checks discovered via the
`archward.verify_checks` entry-point group (v0.3.3+). Each entry point
is a callable `(cfg: ConfigModel, snapshot: Snapshot) -> list[VerifyCheck]`.
A plugin that raises is contained: the failure becomes a synthetic
FAIL VerifyCheck so the user sees it without crashing verify.

Per audit G3: the pacman.log scan does NOT use sudo (log is mode 644 by default).
"""

from __future__ import annotations

import logging
import re
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Callable

from archward.events import EventBus
from archward.models.config import ConfigModel
from archward.models.snapshot import Snapshot
from archward.models.verify import CheckStatus, VerifyCheck, VerifyResult
from archward.pacman import query as pq
from archward.pacman.pacnew import find_pacnew_files
from archward.system import disk, kernel, services

PLUGIN_ENTRY_POINT_GROUP = "archward.verify_checks"

# Per-plugin timeout — see run_verify's plugin loop (v0.4.1 F4).
PLUGIN_TIMEOUT_S = 30

# Filesystem-stat timeout for the reboot-log check (v0.4.1 F6).
# Path.exists() / Path.stat() will block indefinitely on a stuck NFS
# mount; this caps that wait at a few seconds.
REBOOT_LOG_STAT_TIMEOUT_S = 3

log = logging.getLogger(__name__)

PHASE = "verify"


def _call_with_timeout(fn, timeout_s: float):
    """Run `fn()` on a daemon thread, return result or raise TimeoutError.

    Used for filesystem calls that may hang (Path.exists() / stat() on
    a stuck NFS mount, e.g.). The daemon thread keeps running on
    timeout but doesn't block interpreter exit.
    """
    import threading
    result_box: list = []
    exc_box: list = []

    def runner():
        try:
            result_box.append(fn())
        except BaseException as e:  # noqa: BLE001
            exc_box.append(e)

    t = threading.Thread(target=runner, daemon=True, name="verify-fs-stat")
    t.start()
    t.join(timeout=timeout_s)
    if t.is_alive():
        raise TimeoutError(f"call exceeded {timeout_s}s")
    if exc_box:
        raise exc_box[0]
    return result_box[0] if result_box else None


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
    # v0.4.1 (F6): wrap fs probes in a timeout. A reboot_log on a stuck
    # NFS mount would otherwise hang verify forever. On timeout we emit
    # a WARN row pointing at the misconfiguration; user can fix and re-run.
    try:
        exists = _call_with_timeout(p.exists, REBOOT_LOG_STAT_TIMEOUT_S)
    except TimeoutError:
        log.warning("reboot-log path %s exists() timed out", log_path)
        return VerifyCheck(
            bucket="universal",
            name="reboot-log",
            status=CheckStatus.WARN,
            message=f"reboot-log path {log_path} unreachable (stat timed out)",
            detail="Check the path is on a responsive filesystem, or clear cfg.verify.reboot_log.",
        )
    if not exists:
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
        log_ts = int(_call_with_timeout(
            lambda: p.stat().st_mtime, REBOOT_LOG_STAT_TIMEOUT_S
        ))
    except TimeoutError:
        log.warning("reboot-log path %s stat() timed out", log_path)
        return VerifyCheck(
            bucket="universal",
            name="reboot-log",
            status=CheckStatus.WARN,
            message=f"reboot-log path {log_path} unreachable (stat timed out)",
            detail="Check the path is on a responsive filesystem, or clear cfg.verify.reboot_log.",
        )
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


_STALE_MARKER = "no such unit"  # message prefix used by run_verify's auto-prune


def _service_check(unit: str, severity_map: dict[str, str]) -> VerifyCheck:
    # Distinguish "unit is gone" from "unit exists but is stopped". The
    # former is a config-drift problem (file removed by package uninstall
    # or hand-deletion); the latter is the runtime problem severity is
    # designed for. Mixing them under "not active" makes stale entries
    # invisible until the user runs --detect manually.
    if not services.unit_exists(unit):
        return VerifyCheck(
            bucket="services",
            name=unit,
            status=CheckStatus.WARN,
            message=f"{_STALE_MARKER} (file removed/uninstalled) — run `archward --detect` to clean up",
        )

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


def _discover_plugin_checkers() -> list[tuple[str, Callable]]:
    """Discover third-party verify checks via the entry-point group.

    Each entry point is a callable with the contract
    `(cfg: ConfigModel, snapshot: Snapshot) -> list[VerifyCheck]`.

    Failures during *discovery* (e.g. plugin module import error) are
    caught and logged; that entry point is silently dropped. Failures
    during *invocation* are handled by run_verify().
    """
    discovered: list[tuple[str, Callable]] = []
    try:
        eps = importlib_metadata.entry_points(group=PLUGIN_ENTRY_POINT_GROUP)
    except Exception:  # noqa: BLE001
        log.exception("could not enumerate entry points for %s", PLUGIN_ENTRY_POINT_GROUP)
        return discovered
    for ep in eps:
        try:
            fn = ep.load()
        except Exception:  # noqa: BLE001
            log.exception("failed to load verify-check plugin %s", ep.name)
            continue
        if not callable(fn):
            log.warning("verify-check plugin %s is not callable (%r); skipping", ep.name, type(fn))
            continue
        discovered.append((ep.name, fn))
    return discovered


def _auto_prune_stale(
    cfg: ConfigModel,
    config_path: Path | None,
    bus: EventBus,
) -> tuple[ConfigModel, VerifyCheck | None]:
    """If cfg.services.auto_prune is enabled and config_path is provided,
    silently drop stale entries from to_verify and persist the pruned cfg.

    Returns (possibly-updated cfg, summary check). The summary check is
    None when no pruning happens; otherwise a PASS row recording what
    was removed so the user has audit-trail visibility.
    """
    from archward.config.detect import detect_stale_services
    from archward.config.loader import merge_partial, write_config
    from archward.models.config import ServicesConfig

    if not cfg.services.auto_prune or config_path is None:
        return cfg, None
    stale = detect_stale_services(cfg)
    if not stale:
        return cfg, None
    pruned = merge_partial(
        cfg,
        services=ServicesConfig(
            to_verify=tuple(u for u in cfg.services.to_verify if u not in set(stale)),
            severity=dict(cfg.services.severity),
            auto_prune=cfg.services.auto_prune,
        ),
    )
    try:
        write_config(pruned, config_path)
    except OSError as e:
        bus.emit_log(PHASE, f"auto-prune: could not write {config_path}: {e}")
        return cfg, VerifyCheck(
            bucket="universal",
            name="auto-prune",
            status=CheckStatus.WARN,
            message=f"failed to persist auto-prune to {config_path}: {e}",
            detail=", ".join(stale),
        )
    return pruned, VerifyCheck(
        bucket="universal",
        name="auto-prune",
        status=CheckStatus.PASS,
        message=f"auto-pruned {len(stale)} stale unit(s) from services.to_verify",
        detail=", ".join(stale),
    )


def run_verify(
    cfg: ConfigModel,
    snapshot: Snapshot,
    bus: EventBus,
    *,
    config_path: Path | None = None,
) -> VerifyResult:
    bus.emit_start(PHASE, "Verifying post-update state")
    checks: list[VerifyCheck] = []
    snapshot_path = snapshot.meta.path

    # Auto-prune runs *before* the per-service checks so the pruned cfg
    # drives the rest of verify. Without config_path (e.g. test harness)
    # this is a no-op; staleness still surfaces via the per-unit WARN
    # rows from _service_check.
    cfg, prune_check = _auto_prune_stale(cfg, config_path, bus)
    if prune_check is not None:
        checks.append(prune_check)

    checks.append(_kernel_check())
    checks.append(_pacnew_check(cfg, snapshot_path))
    checks.append(_disk_check())
    checks.append(_pacman_log_check())
    reboot = _reboot_log_check(cfg, snapshot_path)
    if reboot is not None:
        checks.append(reboot)

    for unit in cfg.services.to_verify:
        checks.append(_service_check(unit, dict(cfg.services.severity)))

    # Plugin probes: each contained — a raising plugin becomes one
    # synthetic FAIL row, other plugins still run. v0.4.1 (F4): each
    # plugin also gets a per-call timeout (PLUGIN_TIMEOUT_S). Without
    # this, a misbehaving plugin (e.g. network call with no timeout,
    # infinite loop) would freeze the entire verify phase.
    #
    # Implementation: a daemon thread runs the plugin and writes into
    # result/exc boxes; the main thread joins with a timeout. We use a
    # raw daemon thread (not ThreadPoolExecutor) because the executor's
    # context-manager exit blocks on shutdown(wait=True) which would
    # negate the timeout. A hung plugin's daemon thread continues
    # running but won't block interpreter exit.
    import threading
    for name, fn in _discover_plugin_checkers():
        bus.emit_log(PHASE, f"running plugin check: {name}")
        result_box: list = []
        exc_box: list = []

        def _runner(fn=fn, cfg=cfg, snapshot=snapshot, rb=result_box, eb=exc_box):
            try:
                rb.append(fn(cfg, snapshot))
            except BaseException as e:  # noqa: BLE001
                eb.append(e)

        t = threading.Thread(
            target=_runner,
            name=f"verify-plugin-{name}",
            daemon=True,
        )
        t.start()
        t.join(timeout=PLUGIN_TIMEOUT_S)
        if t.is_alive():
            log.warning(
                "verify-check plugin %s timed out after %ss (thread left running)",
                name, PLUGIN_TIMEOUT_S,
            )
            checks.append(VerifyCheck(
                bucket="plugin",
                name=f"plugin:{name}",
                status=CheckStatus.FAIL,
                message=f"plugin timed out after {PLUGIN_TIMEOUT_S}s",
            ))
            continue
        if exc_box:
            e = exc_box[0]
            log.exception("verify-check plugin %s raised", name, exc_info=e)
            checks.append(VerifyCheck(
                bucket="plugin",
                name=f"plugin:{name}",
                status=CheckStatus.FAIL,
                message=f"plugin raised {type(e).__name__}: {e}",
            ))
            continue
        produced = result_box[0] if result_box else []
        for c in produced or []:
            if not isinstance(c, VerifyCheck):
                checks.append(VerifyCheck(
                    bucket="plugin",
                    name=f"plugin:{name}",
                    status=CheckStatus.FAIL,
                    message=f"plugin yielded non-VerifyCheck: {type(c).__name__}",
                ))
                continue
            checks.append(c)

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
