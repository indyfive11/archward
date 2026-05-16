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
from archward.system import security_advisories as sa

PLUGIN_ENTRY_POINT_GROUP = "archward.verify_checks"

# Per-plugin timeout — see run_verify's plugin loop (v0.4.1 F4).
PLUGIN_TIMEOUT_S = 30

# Filesystem-stat timeout for the reboot-log check (v0.4.1 F6).
# Path.exists() / Path.stat() will block indefinitely on a stuck NFS
# mount; this caps that wait at a few seconds.
REBOOT_LOG_STAT_TIMEOUT_S = 3

# Cache-dir scan timeout for the rollback-cache check (v0.4.4 F2).
# /var/cache/pacman/pkg is normally local, but it can be a bind/overlay
# mount; cap the iterdir() the same way the reboot-log probe is capped.
CACHE_SCAN_TIMEOUT_S = 5

# Boot-fs probe timeout for the boot-integrity check (v0.4.4 F3).
# /boot (or the ESP) may be a slow/unmounted/auto-mounted FAT volume;
# cap the stat()/glob() walk so verify never hangs on it.
BOOT_PROBE_TIMEOUT_S = 5
_BOOT_DIR = Path("/boot")

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


def _aur_helper_cache_roots() -> list[Path]:
    """Return existing cache root dirs for known AUR helpers.

    AUR packages built by yay/paru are stored in the helper's own cache
    directory, not in pacman's CacheDir — so the standard pacman cache scan
    misses them entirely.  Layout (one subdirectory per package):
      yay:  $XDG_CACHE_HOME/yay/<pkg>/*.pkg.tar.*
      paru: $XDG_CACHE_HOME/paru/clone/<pkg>/*.pkg.tar.*
    """
    import os
    xdg_cache = Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache"))
    candidates = [xdg_cache / "yay", xdg_cache / "paru" / "clone"]
    return [p for p in candidates if p.is_dir()]


def _cache_safety_check(snapshot_path: Path) -> VerifyCheck:
    """Did this update's rollback substrate survive the transaction?

    archward's headline promise is a recoverable update: if it goes
    bad, downgrade the offending package from the cached old
    `.pkg.tar.*`. A post-transaction cleaning hook (paccache / pacman
    -Sc) runs *inside* the same `pacman -Syu` archward just ran, so it
    can delete exactly the pre-update files the downgrade path needs.

    We compare the snapshot's recorded package versions against what's
    installed now and, for everything that changed, check the pacman
    cache AND the AUR helper caches for the *old* file. If the pre-update
    files are gone from all of them, the safety net failed for this run.
    The v0.4.0 "What to do?" button surfaces the archive.archlinux.org
    remediation.
    """
    from archward.system import cache_policy as cp

    all_txt = snapshot_path / "packages" / "all.txt"
    try:
        snap_lines = all_txt.read_text(
            encoding="utf-8", errors="replace"
        ).splitlines()
    except OSError:
        # No snapshot package list to compare against — don't manufacture
        # a false positive; this isn't the check's failure mode.
        return VerifyCheck(
            bucket="universal",
            name="rollback-cache",
            status=CheckStatus.PASS,
            message="no snapshot package list to compare (skipped)",
        )

    snap_versions: dict[str, str] = {}
    for line in snap_lines:
        parts = line.split()
        if len(parts) == 2:
            snap_versions[parts[0]] = parts[1]

    current = dict(pq.list_all())
    updated = [
        (n, ov)
        for n, ov in snap_versions.items()
        if n in current and current[n] != ov
    ]
    if not updated:
        return VerifyCheck(
            bucket="universal",
            name="rollback-cache",
            status=CheckStatus.PASS,
            message="no package versions changed since snapshot",
        )

    # Pacman cache (official packages; flat layout: one .pkg.tar.* per file).
    # Honour pacman.conf CacheDir — it can be relocated or multiple.
    cache_dirs = cp.read_cache_dirs()
    # AUR helper caches (yay/paru): one subdirectory per package, files inside.
    aur_roots = _aur_helper_cache_roots()
    all_scanned = list(cache_dirs) + aur_roots

    def _scan() -> set[str]:
        names: set[str] = set()
        for d in cache_dirs:
            try:
                names.update(p.name for p in d.iterdir() if p.is_file())
            except OSError:
                continue
        for root in aur_roots:
            try:
                for pkg_dir in root.iterdir():
                    if pkg_dir.is_dir():
                        names.update(
                            p.name for p in pkg_dir.iterdir()
                            if p.is_file() and ".pkg.tar." in p.name
                        )
            except OSError:
                continue
        return names

    try:
        cache_names = _call_with_timeout(_scan, CACHE_SCAN_TIMEOUT_S)
    except (TimeoutError, OSError) as e:
        # Couldn't read the cache → we can't prove rollback is gone.
        # A false FAIL here is worse than a missed check (same stance
        # as boot-integrity): SKIP, don't FAIL.
        log.warning("rollback-cache: cache scan failed (%s) — skipping", e)
        return VerifyCheck(
            bucket="universal",
            name="rollback-cache",
            status=CheckStatus.PASS,
            message=(
                "cache scan failed/timed out — rollback-cache skipped "
                f"(checked: {', '.join(str(d) for d in all_scanned)})"
            ),
        )

    # The pacman cache filename embeds the full version (epoch:pkgver-rel)
    # exactly as `pacman -Q` prints it, e.g. `foo-2:1.2.3-4-x86_64.pkg.tar.zst`.
    # `{name}-{version}-` is therefore a safe prefix for both epoch and
    # non-epoch packages.
    missing = [
        f"{n} {ov}"
        for n, ov in updated
        if not any(fn.startswith(f"{n}-{ov}-") for fn in cache_names)
    ]
    if not missing:
        return VerifyCheck(
            bucket="universal",
            name="rollback-cache",
            status=CheckStatus.PASS,
            message=(
                f"pre-update package(s) still cached for all "
                f"{len(updated)} updated package(s) — rollback available"
            ),
        )

    hooks = cp.scan_cleaning_hooks()
    cause = (
        f"A cache-cleaning pacman hook ({', '.join(h.name for h in hooks)}) "
        "ran during this update — that is what removed them. Remove the "
        "hook so future updates stay recoverable."
        if hooks
        else "The pre-update package files are no longer in any cache "
        "(paccache, a manual pacman -Sc, or yay/paru cache pruning removed them)."
    )
    shown = ", ".join(missing[:20]) + (" …" if len(missing) > 20 else "")
    return VerifyCheck(
        bucket="universal",
        name="rollback-cache",
        status=CheckStatus.FAIL,
        message=(
            f"rollback unavailable for {len(missing)} of {len(updated)} "
            "just-updated package(s) — pre-update version gone from cache"
        ),
        detail=(
            "archward's downgrade path needs the old .pkg.tar.* in "
            f"{', '.join(str(d) for d in all_scanned)}. {cause} "
            f"Affected: {shown}"
        ),
    )


def _boot_integrity_check(boot_dir: Path = _BOOT_DIR) -> VerifyCheck:
    """Will the machine actually boot after this update?

    The classic silent killer: a kernel package upgraded but the
    initramfs generator (mkinitcpio or dracut) didn't regenerate the
    initramfs — its pacman hook failed or was removed. pacman exits 0,
    verify is otherwise green, and the box fails to boot on the next
    reboot, exactly when the user is least able to fix it.

    We FAIL on exactly one unambiguous signal: an
    `initramfs-<flavour>.img` that is OLDER than its
    `vmlinuz-<flavour>`. With stable kernel image filenames (the Arch
    default) the initramfs MUST be rewritten in lockstep with the
    kernel, so older-than is a hard contradiction.

    We deliberately do NOT check grub.cfg mtime. With stable kernel
    filenames `grub.cfg` references a fixed path (`/boot/vmlinuz-linux`)
    and is NOT regenerated on a routine kernel update — it legitimately
    predates the kernel by months on a perfectly bootable system.
    There is no cheap, false-positive-free bootloader-staleness signal,
    so we don't invent one.

    Every indeterminate case (no matching initramfs → dracut-with-kver
    naming / UKI / exotic, /boot absent or unmounted) is SKIPPED as a
    PASS-with-note — a false FAIL on a working setup is worse than a
    missed check. The v0.4.0 "What to do?" button surfaces the regen
    commands.
    """
    name = "boot-integrity"

    def _probe() -> tuple[str, str, str | None]:
        if not boot_dir.is_dir():
            return ("pass", f"{boot_dir} not present — boot-integrity skipped", None)

        # Unified Kernel Image setups bundle the initramfs inside the
        # .efi. A standalone initramfs-<flavour>.img may still be lying
        # around (leftover / dual) and could be stale while the box
        # boots fine from the UKI — checking the standalone would be a
        # false FAIL. If any UKI exists, the standalone images are not
        # authoritative: skip the whole check.
        for ud in (
            boot_dir / "EFI" / "Linux",
            Path("/efi/EFI/Linux"),
            Path("/boot/efi/EFI/Linux"),
        ):
            try:
                if ud.is_dir() and any(ud.glob("*.efi")):
                    return (
                        "pass",
                        "Unified Kernel Image present — boot-integrity "
                        "skipped (standalone initramfs not authoritative)",
                        None,
                    )
            except OSError:
                continue

        kernels = sorted(boot_dir.glob("vmlinuz-*"))
        if not kernels:
            return ("pass", "no vmlinuz-* kernel image — boot-integrity skipped", None)

        problems: list[str] = []
        assessed = 0
        for k in kernels:
            try:
                kmt = k.stat().st_mtime
            except OSError:
                continue
            flavour = k.name[len("vmlinuz-"):]
            img = boot_dir / f"initramfs-{flavour}.img"
            if not img.exists():
                # No flavour-named initramfs for this kernel — likely
                # dracut-with-kver naming / UKI / exotic. Can't
                # conclude broken: skip it.
                continue
            assessed += 1
            try:
                imt = img.stat().st_mtime
            except OSError:
                continue
            if kmt > imt:
                problems.append(
                    f"{k.name} is newer than {img.name} — initramfs not "
                    "regenerated (the mkinitcpio/dracut pacman hook didn't "
                    "run or failed)"
                )

        if problems:
            head = problems[0]
            extra = f" (+{len(problems) - 1} more)" if len(problems) > 1 else ""
            return ("fail", f"boot may be broken — {head}{extra}", "\n".join(problems))
        if assessed == 0:
            return (
                "pass",
                "no flavour-named initramfs to assess (dracut/UKI?) — skipped",
                None,
            )
        return (
            "pass",
            f"initramfs newer than kernel for {assessed} kernel(s)",
            None,
        )

    try:
        verdict, message, detail = _call_with_timeout(_probe, BOOT_PROBE_TIMEOUT_S)
    except TimeoutError:
        log.warning("boot-integrity probe timed out")
        return VerifyCheck(
            bucket="universal",
            name=name,
            status=CheckStatus.PASS,
            message=f"{boot_dir} fs probe timed out — boot-integrity skipped",
            detail="Check /boot is on a responsive filesystem.",
        )
    except Exception as e:  # noqa: BLE001 — probe must never crash verify
        log.warning("boot-integrity probe error: %s", e)
        return VerifyCheck(
            bucket="universal",
            name=name,
            status=CheckStatus.PASS,
            message="boot-integrity skipped (probe error)",
        )

    status = CheckStatus.FAIL if verdict == "fail" else CheckStatus.PASS
    return VerifyCheck(
        bucket="universal", name=name, status=status, message=message, detail=detail
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


def _orphan_check() -> VerifyCheck:
    """Report packages installed as dependencies that are no longer required.

    Uses `pacman -Qdtq` (list orphans: installed as deps with no dependents).
    WARN (not FAIL) — users intentionally keep some orphans. The WARN row
    points them at the right pacman commands to investigate + clean up.
    """
    import subprocess

    try:
        r = subprocess.run(
            ["pacman", "-Qdtq"],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except subprocess.TimeoutExpired:
        log.warning("_orphan_check: pacman -Qdtq timed out")
        return VerifyCheck(
            bucket="universal",
            name="orphans",
            status=CheckStatus.WARN,
            message="orphan check timed out",
        )
    except FileNotFoundError:
        return VerifyCheck(
            bucket="universal",
            name="orphans",
            status=CheckStatus.PASS,
            message="pacman not found — orphan check skipped",
        )

    orphans = [p for p in r.stdout.splitlines() if p.strip()]
    if not orphans:
        return VerifyCheck(
            bucket="universal",
            name="orphans",
            status=CheckStatus.PASS,
            message="No orphaned packages",
        )
    n = len(orphans)
    return VerifyCheck(
        bucket="universal",
        name="orphans",
        status=CheckStatus.WARN,
        message=f"{n} orphaned package{'s' if n != 1 else ''} — installed as deps but no longer required",
        detail="\n".join(orphans),
    )


_STALE_LIBS_TIMEOUT_S = 20.0

# Candidate paths for the stale_libs_scan helper (installed → dev tree).
_SCAN_SCRIPT_CANDIDATES = [
    Path("/usr/share/archward/stale_libs_scan"),
    Path(__file__).parent.parent / "data" / "stale_libs_scan",
]


def _parse_cgroup(cgroup_path: Path) -> str | None:
    """Extract the systemd unit name from a /proc/<pid>/cgroup file."""
    try:
        for line in cgroup_path.read_text().splitlines():
            parts = line.split(":", 2)
            if len(parts) == 3 and parts[2].strip():
                unit = parts[2].strip().split("/")[-1]
                if unit:
                    return unit
    except OSError:
        pass
    return None


def _pid_to_unit(pid: int) -> str:
    return _parse_cgroup(Path(f"/proc/{pid}/cgroup")) or f"pid:{pid}"


def _user_visible_scan(proc_dir: Path = Path("/proc")) -> list[dict]:
    """Scan processes readable without root. Catches user-session services."""
    by_unit: dict[str, set] = {}
    for pid_dir in sorted(proc_dir.iterdir()):
        if not pid_dir.name.isdigit():
            continue
        try:
            content = (pid_dir / "maps").read_text(errors="replace")
        except (PermissionError, FileNotFoundError, OSError):
            continue
        deleted: set[str] = set()
        for line in content.splitlines():
            if "(deleted)" not in line:
                continue
            # /proc/<pid>/maps format: addr perms offset dev inode [pathname]
            # Deleted files have " (deleted)" appended to the pathname.
            # Split with maxsplit=5 so the pathname field (index 5) is intact.
            parts = line.split(None, 5)
            if len(parts) < 6:
                continue
            path = parts[5].replace(" (deleted)", "").strip()
            if not path.startswith("/") or ".so" not in path:
                continue
            if not (path.startswith("/usr/") or path.startswith("/lib")):
                continue
            deleted.add(path)
        if deleted:
            # Read cgroup from the same pid_dir so fake /proc trees work in tests.
            unit = _parse_cgroup(pid_dir / "cgroup") or f"pid:{pid_dir.name}"
            by_unit.setdefault(unit, set()).update(deleted)
    return [{"unit": u, "deleted": sorted(libs)} for u, libs in sorted(by_unit.items())]


def _sudo_scan(script: Path) -> list[dict] | None:
    """Try full scan as root using the helper script via sudo -n.

    Uses the existing sudo timestamp (non-interactive). Returns parsed
    JSON on success, None if sudo is not available or the script errors.

    Uses /usr/bin/python3 explicitly — the helper script has no archward
    imports so any system Python 3 works, and the sudoers NOPASSWD entry
    is pinned to that path.
    """
    import json as _json
    import subprocess

    try:
        r = subprocess.run(
            ["sudo", "-n", "/usr/bin/python3", str(script)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if r.returncode == 0:
            return _json.loads(r.stdout)
    except Exception:
        pass
    return None


def _stale_libs_check(cfg: ConfigModel) -> VerifyCheck:
    """Detect services running against deleted shared library versions.

    After a package update, long-running processes still map the old .so files
    from disk (link count drops to 0 but the fd stays open). This check surfaces
    them so the user knows which services need a restart.

    Tries a full scan via sudo -n (uses the existing sudo timestamp) first.
    Falls back to user-visible processes (KDE, pipewire, browsers, user units)
    if the sudoers NOPASSWD entry is absent.
    """
    if not cfg.verify.stale_libs:
        return VerifyCheck(
            bucket="universal",
            name="stale-libs",
            status=CheckStatus.PASS,
            message="stale library check disabled",
        )

    script = next((p for p in _SCAN_SCRIPT_CANDIDATES if p.exists()), None)
    full_coverage = False
    entries: list[dict] = []

    if script is not None:
        result = _sudo_scan(script)
        if result is not None:
            entries = result
            full_coverage = True

    if not full_coverage:
        try:
            entries = _call_with_timeout(_user_visible_scan, _STALE_LIBS_TIMEOUT_S)
            if entries is None:
                entries = []
        except TimeoutError:
            log.warning("_stale_libs_check: user-visible scan timed out")
            return VerifyCheck(
                bucket="universal",
                name="stale-libs",
                status=CheckStatus.WARN,
                message="stale library scan timed out",
            )

    if not entries:
        suffix = "" if full_coverage else " (user-visible processes only)"
        return VerifyCheck(
            bucket="universal",
            name="stale-libs",
            status=CheckStatus.PASS,
            message=f"No services running deleted library versions{suffix}",
        )

    n = len(entries)
    lines = [f"  {e['unit']}: {', '.join(e['deleted'])}" for e in entries]
    if not full_coverage:
        lines.append(
            "  (system services not scanned — add sudoers entry for full coverage,"
            " see docs/development.md)"
        )
    return VerifyCheck(
        bucket="universal",
        name="stale-libs",
        status=CheckStatus.WARN,
        message=f"{n} service{'s' if n != 1 else ''} running deleted library versions"
                " — restart recommended",
        detail="\n".join(lines),
    )


def _security_advisory_check(cfg: ConfigModel) -> VerifyCheck:
    """Cross-reference installed packages against Arch Security Advisories.

    Skips silently when arch-audit is installed (avoids double-reporting)
    or when the network is unreachable. Severity mapping:
    Critical/High → FAIL; Medium/Low → WARN.
    """
    if not cfg.verify.security_advisories:
        return VerifyCheck(
            bucket="universal",
            name="security-advisories",
            status=CheckStatus.PASS,
            message="security advisory check disabled",
        )

    if sa.arch_audit_present():
        return VerifyCheck(
            bucket="universal",
            name="security-advisories",
            status=CheckStatus.PASS,
            message="arch-audit present — ASA check deferred to arch-audit",
        )

    advisories = sa.fetch_advisories()
    if not advisories:
        # Network failure or empty feed — SKIP (do not FAIL)
        return VerifyCheck(
            bucket="universal",
            name="security-advisories",
            status=CheckStatus.PASS,
            message="ASA check skipped (network unavailable or feed empty)",
        )

    installed = list(pq.list_all())
    open_advisories = sa.open_for_installed(advisories, installed)

    if not open_advisories:
        return VerifyCheck(
            bucket="universal",
            name="security-advisories",
            status=CheckStatus.PASS,
            message="No open Arch Security Advisories affect installed packages",
        )

    n = len(open_advisories)
    severities = {a.severity for a in open_advisories}
    is_critical = bool(severities & {"Critical", "High"})
    detail_lines = "\n".join(
        f"• {a.name} ({a.severity}) — {', '.join(a.packages)} — {', '.join(a.issues) or 'no CVE'}"
        for a in open_advisories
    )
    return VerifyCheck(
        bucket="universal",
        name="security-advisories",
        status=CheckStatus.FAIL if is_critical else CheckStatus.WARN,
        message=f"{n} open Arch Security Advisory{'s' if n == 1 else 'ies'} affect installed packages",
        detail=detail_lines,
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
    checks.append(_cache_safety_check(snapshot_path))
    checks.append(_boot_integrity_check())
    checks.append(_disk_check())
    checks.append(_pacman_log_check())
    checks.append(_orphan_check())
    checks.append(_stale_libs_check(cfg))
    checks.append(_security_advisory_check(cfg))
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
