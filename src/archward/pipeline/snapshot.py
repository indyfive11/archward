"""Snapshot phase — capture universal system state.

Universal items per audit G1:
  - configs:  pacman.conf, mirrorlist, fstab, grub-default,
              sshd_config (+ sshd_config.d/ archive),
              resolved.conf, sudoers.d/ archive (chmod 600)
  - network:  ip addr, ss -tlnp, wg status (if wg present)
  - services: running.txt, enabled.txt, to-verify-status.txt
  - system:   kernel-running.txt, cmdline.txt, disk.txt, os-release.txt, helper.txt
  - packages: explicit.txt, all.txt, aur.txt, pending-official.txt, critical.txt
  - pacnew-baseline.txt
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from archward.events import EventBus
from archward.models.config import ConfigModel
from archward.models.snapshot import Snapshot, SnapshotMeta
from archward.pacman import query as pq
from archward.pacman.pacnew import find_pacnew_files
from archward.privilege.sudo import SudoStrategy
from archward.system import disk, distro, kernel, services

log = logging.getLogger(__name__)

PHASE = "snapshot"


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _sudo_copy_if_exists(src: Path, dst: Path, strategy: SudoStrategy, mode: int | None = None) -> bool:
    """Copy a (possibly root-owned) file via sudo if the source exists. Returns success bool."""
    from archward.pacman.runner import run_capture

    if not src.exists():
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    code, _, err = run_capture(["cp", "-a", str(src), str(dst)], strategy=strategy)
    if code != 0:
        log.warning("snapshot cp %s failed: %s", src, err.strip())
        return False
    if mode is not None:
        # Chmod on the snapshot copy — chown to user to avoid sudo on later cleanup.
        try:
            dst.chmod(mode)
        except OSError as e:
            log.warning("snapshot chmod %s failed: %s", dst, e)
    return True


def _sudo_targz_if_exists(src_dir: Path, dst: Path, strategy: SudoStrategy, mode: int | None = None) -> bool:
    """tar czf snapshot of a (possibly root-owned) directory.

    The .tar.gz is created by sudo tar and is initially root-owned. We chown it to
    the invoking user and (optionally) chmod via sudo so the file lives under
    ~/.local with the right ownership and perms.
    """
    from archward.pacman.runner import run_capture

    if not src_dir.exists():
        return False
    rel = str(src_dir).lstrip("/")
    code, _, err = run_capture(
        ["tar", "czf", str(dst), "-C", "/", rel],
        strategy=strategy,
    )
    if code != 0:
        log.warning("snapshot tar %s failed: %s", src_dir, err.strip())
        return False

    import os
    # Reclaim ownership of the root-created archive.
    uid = os.getuid()
    gid = os.getgid()
    code, _, err = run_capture(
        ["chown", f"{uid}:{gid}", str(dst)],
        strategy=strategy,
    )
    if code != 0:
        log.warning("snapshot chown %s failed: %s", dst, err.strip())

    if mode is not None:
        # Use sudo chmod — the archive may have already been chowned, but doing
        # the chmod via sudo means it works even if the chown above failed.
        mode_str = format(mode & 0o7777, "o")
        code, _, err = run_capture(
            ["chmod", mode_str, str(dst)],
            strategy=strategy,
        )
        if code != 0:
            log.warning("snapshot chmod %s failed: %s", dst, err.strip())
    return True


def _gather_packages(snap_root: Path, cfg: ConfigModel) -> dict[str, Path]:
    import fnmatch

    pkg_dir = snap_root / "packages"
    pkg_dir.mkdir(parents=True, exist_ok=True)

    explicit = pq.list_explicit()
    all_pkgs = pq.list_all()
    foreign = pq.list_foreign()
    pending = pq.checkupdates()

    _write_text(pkg_dir / "explicit.txt", "\n".join(explicit) + "\n")
    _write_text(
        pkg_dir / "all.txt",
        "\n".join(f"{n} {v}" for n, v in all_pkgs) + "\n",
    )
    _write_text(
        pkg_dir / "aur.txt",
        "\n".join(f"{n} {v}" for n, v in foreign) + "\n",
    )
    _write_text(
        pkg_dir / "pending-official.txt",
        ("\n".join(f"{p.name} {p.old_version} -> {p.new_version}" for p in pending) + "\n")
        if pending
        else "(no updates pending)\n",
    )

    # critical.txt is the rollback target list. v0.2.0 fix: kernel packages
    # match cfg.risk.kernel_patterns (fnmatch) and were previously omitted —
    # they're matched at runtime by the risk classifier but never recorded
    # here. Without their pre-update versions in critical.txt, the
    # SnapshotBrowser can't offer to downgrade them. Now we also iterate
    # the installed package list and add anything matching a kernel pattern
    # (minus kernel_pattern_exclude — firmware/docs/tools).
    high_set = set(cfg.risk.high)
    kernel_pkgs: list[tuple[str, str]] = []
    for name, version in all_pkgs:
        if name in high_set:
            continue  # already captured in the HIGH iteration below
        if any(fnmatch.fnmatch(name, pat) for pat in cfg.risk.kernel_pattern_exclude):
            continue
        if any(fnmatch.fnmatch(name, pat) for pat in cfg.risk.kernel_patterns):
            kernel_pkgs.append((name, version))

    critical_lines = ["=== Critical package versions pre-update ==="]
    for pkg in cfg.risk.high:
        v = pq.installed_version(pkg)
        critical_lines.append(f"{pkg}: {v if v else 'not installed'}")
    if kernel_pkgs:
        critical_lines.append("")
        critical_lines.append("=== Kernel packages (via kernel_patterns) ===")
        for name, version in sorted(kernel_pkgs):
            critical_lines.append(f"{name}: {version}")
    critical_lines.append("")
    critical_lines.append("=== AUR / foreign packages (not tracked by checkupdates) ===")
    critical_lines.extend(f"{n} {v}" for n, v in foreign)
    _write_text(pkg_dir / "critical.txt", "\n".join(critical_lines) + "\n")

    return {
        "explicit": pkg_dir / "explicit.txt",
        "all": pkg_dir / "all.txt",
        "aur": pkg_dir / "aur.txt",
        "pending-official": pkg_dir / "pending-official.txt",
        "critical": pkg_dir / "critical.txt",
    }


def _gather_configs(snap_root: Path, strategy: SudoStrategy) -> list[Path]:
    cdir = snap_root / "configs"
    cdir.mkdir(parents=True, exist_ok=True)
    captured: list[Path] = []

    # Universal config files. Each is gated on the source existing.
    universal_files = [
        (Path("/etc/pacman.conf"), cdir / "pacman.conf", None),
        (Path("/etc/pacman.d/mirrorlist"), cdir / "mirrorlist", None),
        (Path("/etc/fstab"), cdir / "fstab", None),
        (Path("/etc/default/grub"), cdir / "grub-default", None),
        (Path("/etc/ssh/sshd_config"), cdir / "sshd_config", None),
        (Path("/etc/systemd/resolved.conf"), cdir / "resolved.conf", None),
    ]
    for src, dst, mode in universal_files:
        if _sudo_copy_if_exists(src, dst, strategy, mode):
            captured.append(dst)

    # Universal archives.
    universal_archives = [
        (Path("/etc/ssh/sshd_config.d"), cdir / "sshd_config.d.tar.gz", 0o600),
        (Path("/etc/sudoers.d"), cdir / "sudoers.d.tar.gz", 0o600),
    ]
    for src, dst, mode in universal_archives:
        if _sudo_targz_if_exists(src, dst, strategy, mode):
            captured.append(dst)

    return captured


def _gather_network(snap_root: Path) -> None:
    """Capture interface + listening-port + wireguard state.

    Each subprocess has a short timeout so a broken interface or stuck
    netlink socket can't hang the whole snapshot phase indefinitely. On
    timeout, the section's file is written with a `(timed out)` marker
    and the snapshot continues.
    """
    ndir = snap_root / "network"
    ndir.mkdir(parents=True, exist_ok=True)

    try:
        ip = subprocess.run(
            ["ip", "addr"], check=False, capture_output=True, text=True, timeout=10
        ).stdout
        _write_text(ndir / "ip-addr.txt", ip)
    except FileNotFoundError:
        pass
    except subprocess.TimeoutExpired:
        _write_text(ndir / "ip-addr.txt", "(ip addr timed out after 10s)\n")
        log.warning("snapshot: `ip addr` timed out")

    try:
        ss = subprocess.run(
            ["ss", "-tlnp"], check=False, capture_output=True, text=True, timeout=10
        ).stdout
        _write_text(ndir / "listening-ports.txt", ss)
    except FileNotFoundError:
        pass
    except subprocess.TimeoutExpired:
        _write_text(ndir / "listening-ports.txt", "(ss -tlnp timed out after 10s)\n")
        log.warning("snapshot: `ss -tlnp` timed out")

    if shutil.which("wg"):
        try:
            wg = subprocess.run(
                ["wg", "show"], check=False, capture_output=True, text=True, timeout=5
            )
            if wg.returncode == 0:
                _write_text(ndir / "wg-status.txt", wg.stdout)
        except FileNotFoundError:
            pass
        except subprocess.TimeoutExpired:
            _write_text(ndir / "wg-status.txt", "(wg show timed out after 5s)\n")
            log.warning("snapshot: `wg show` timed out")


def _gather_services(snap_root: Path, cfg: ConfigModel) -> dict[str, Path]:
    sdir = snap_root / "services"
    sdir.mkdir(parents=True, exist_ok=True)

    _write_text(sdir / "running.txt", services.list_running())
    _write_text(sdir / "enabled.txt", services.list_enabled())

    if cfg.services.to_verify:
        rows = []
        for unit in cfg.services.to_verify:
            status = "active" if services.is_active(unit) else "inactive"
            rows.append(f"{unit:30s} {status}")
        _write_text(sdir / "to-verify-status.txt", "\n".join(rows) + "\n")
    else:
        _write_text(sdir / "to-verify-status.txt", "(no services configured)\n")

    return {
        "running": sdir / "running.txt",
        "enabled": sdir / "enabled.txt",
        "to-verify-status": sdir / "to-verify-status.txt",
    }


def _gather_system(snap_root: Path) -> None:
    sysdir = snap_root / "system"
    sysdir.mkdir(parents=True, exist_ok=True)

    _write_text(sysdir / "kernel-running.txt", kernel.running_kernel() + "\n")
    try:
        _write_text(sysdir / "cmdline.txt", Path("/proc/cmdline").read_text())
    except OSError:
        pass
    try:
        df = subprocess.run(
            ["df", "-h"], check=False, capture_output=True, text=True, timeout=5
        ).stdout
        _write_text(sysdir / "disk.txt", df)
    except FileNotFoundError:
        pass
    except subprocess.TimeoutExpired:
        _write_text(sysdir / "disk.txt", "(df -h timed out after 5s)\n")
        log.warning("snapshot: `df -h` timed out (stuck mount?)")
    if Path("/etc/os-release").exists():
        _write_text(sysdir / "os-release.txt", Path("/etc/os-release").read_text())

    # AUR helper detection (informational — Phase 1 doesn't act on it).
    helper = None
    for h in ("yay", "paru", "aurutils"):
        if shutil.which(h):
            helper = h
            break
    _write_text(sysdir / "helper.txt", (helper or "none") + "\n")


def _capture_pacnew_baseline(snap_root: Path) -> None:
    baseline = find_pacnew_files()
    _write_text(
        snap_root / "pacnew-baseline.txt",
        "\n".join(str(p) for p in baseline) + ("\n" if baseline else ""),
    )


def take_snapshot(cfg: ConfigModel, strategy: SudoStrategy, bus: EventBus) -> Snapshot:
    """Take a full snapshot. Emits PHASE_LOG events along the way.

    v0.4.1 (F8): if any gather step raises, the half-populated snapshot
    dir is removed before re-raising. Without this cleanup a partial
    snapshot would leak on disk forever (no `.timestamp` marker means
    retention can't prune it). Snapshot is all-or-nothing.
    """
    bus.emit_start(PHASE, "Capturing system state")

    snapshot_id = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    snap_root = cfg.general.snapshot_dir / snapshot_id
    snap_root.mkdir(parents=True, exist_ok=True)

    bus.emit_log(PHASE, f"Snapshot directory: {snap_root}")

    try:
        bus.emit_log(PHASE, "[1/6] Packages")
        package_files = _gather_packages(snap_root, cfg)

        bus.emit_log(PHASE, "[2/6] Configs")
        config_files = _gather_configs(snap_root, strategy)

        bus.emit_log(PHASE, "[3/6] Network")
        _gather_network(snap_root)

        bus.emit_log(PHASE, "[4/6] Services")
        service_files = _gather_services(snap_root, cfg)

        bus.emit_log(PHASE, "[5/6] System")
        _gather_system(snap_root)

        bus.emit_log(PHASE, "[6/6] Pacnew baseline")
        _capture_pacnew_baseline(snap_root)

        # Timestamp markers — written LAST so a partial dir is never
        # marked as a real snapshot.
        now = datetime.now()
        (snap_root / ".timestamp").write_text(str(int(now.timestamp())) + "\n")
        (snap_root / ".human-timestamp").write_text(now.isoformat() + "\n")
    except BaseException as exc:
        # Tear down the partial dir so retention doesn't have to deal
        # with orphans, and so the user's disk doesn't fill silently.
        log.warning(
            "snapshot phase failed (%s: %s); removing partial dir %s",
            type(exc).__name__, exc, snap_root,
        )
        shutil.rmtree(snap_root, ignore_errors=True)
        raise

    info = distro.detect_distro()
    meta = SnapshotMeta(
        snapshot_id=snapshot_id,
        created_at=now,
        path=snap_root,
        distro_id=info.id,
        kernel_release=kernel.running_kernel(),
        free_disk_gb=disk.free_gb("/"),
        helper_detected=None,
    )

    bus.emit_result(PHASE, f"Snapshot complete: {snapshot_id}")
    return Snapshot(
        meta=meta,
        package_files=package_files,
        config_files=tuple(config_files),
        service_files=service_files,
        age_seconds=0,
    )


def latest_snapshot(snapshot_dir: Path) -> tuple[Path, int] | None:
    """Find the most-recent snapshot subdirectory. Returns (path, age_seconds) or None."""
    if not snapshot_dir.exists():
        return None
    candidates = sorted(
        (p for p in snapshot_dir.iterdir() if p.is_dir() and (p / ".timestamp").exists()),
        key=lambda p: p.name,
    )
    if not candidates:
        return None
    latest = candidates[-1]
    ts_path = latest / ".timestamp"
    try:
        ts = int(ts_path.read_text().strip())
    except (OSError, ValueError):
        return None
    age = int(datetime.now().timestamp()) - ts
    return latest, age


# ── Completeness validation (v0.4.4 F4) ────────────────────────────────────


def validate_snapshot(path: Path) -> list[str]:
    """Return human-readable reasons the snapshot can't back a rollback.

    Empty list ⇒ everything rollback/verify depend on is present.

    `load_snapshot_from_disk` deliberately *tolerates* missing
    per-section files (a snapshot still loads with an empty section).
    That's right for loading but wrong for trusting: a snapshot whose
    `packages/all.txt` or `configs/` is gone will fail cryptically
    half-way through a restore, after pacman state has already been
    touched. This is the up-front gate so the CLI/GUI refuse with a
    clear message *before* acting.

    "Complete enough to roll back" =
      - `.timestamp` present and a valid epoch
      - `packages/all.txt` present and non-empty (the package baseline)
      - `configs/` directory present (pre-update config copies)

    `packages/critical.txt` is deliberately NOT required: the rollback
    path reconstructs the critical/kernel set from `all.txt` + the
    configured kernel patterns when it's absent
    (`critical_packages_with_kernel_fallback`), so pre-v0.2.0 snapshots
    that predate critical.txt are still usable — refusing them here
    would be a false "incomplete".
    """
    problems: list[str] = []

    ts = path / ".timestamp"
    if not ts.exists():
        problems.append(
            ".timestamp marker missing — not a completed snapshot"
        )
    else:
        try:
            int(ts.read_text().strip())
        except (OSError, ValueError):
            problems.append(".timestamp is unreadable or not an epoch")

    all_txt = path / "packages" / "all.txt"
    try:
        if not all_txt.is_file() or not all_txt.read_text(
            encoding="utf-8", errors="replace"
        ).strip():
            problems.append(
                "packages/all.txt missing or empty — no package baseline "
                "to roll back to"
            )
    except OSError:
        problems.append("packages/all.txt unreadable")

    if not (path / "configs").is_dir():
        problems.append(
            "configs/ missing — no pre-update config copies to restore"
        )

    return problems


# ── On-disk reconstruction (v0.4.3) ────────────────────────────────────────


def load_snapshot_from_disk(path: Path) -> Snapshot | None:
    """Reconstruct a Snapshot model from an existing on-disk snapshot dir.

    Mirrors the per-section layout `take_snapshot` writes — see the
    `_gather_*` functions earlier in this module. Returns None if the
    directory doesn't carry a `.timestamp` marker (the canonical
    "this is a complete snapshot" signal that retention.py and
    latest_snapshot also rely on).

    Used by the v0.4.3 CLI subcommands (`archward verify`,
    `archward rollback ...`) to operate on past snapshots without
    taking a new one, and (after the v0.4.3 refactor) by the GUI's
    Snapshot Browser so there's a single source of truth for the
    parsing pattern.

    Missing per-section files are tolerated — a snapshot whose
    `system/os-release.txt` happens to be absent still loads, just
    with an empty `distro_id`. The function never raises; on any
    unexpected I/O error against the `.timestamp` itself it returns
    None and logs a warning.
    """
    ts_path = path / ".timestamp"
    if not ts_path.exists():
        return None
    try:
        ts_epoch = int(ts_path.read_text().strip())
    except (OSError, ValueError) as e:
        log.warning("snapshot %s has unreadable .timestamp: %s", path, e)
        return None

    created_at = datetime.fromtimestamp(ts_epoch)
    age_seconds = max(0, int(datetime.now().timestamp()) - ts_epoch)

    # system/* — single-line files. _read_first_line handles missing paths.
    kernel_release = _read_first_line(path / "system" / "kernel-running.txt")
    helper_detected = _read_first_line(path / "system" / "helper.txt") or None

    # os-release.txt is KEY=VALUE; extract ID=.
    distro_id = ""
    osr = path / "system" / "os-release.txt"
    if osr.exists():
        try:
            for line in osr.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("ID="):
                    distro_id = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
        except OSError as e:
            log.debug("snapshot %s os-release unreadable: %s", path, e)

    meta = SnapshotMeta(
        snapshot_id=path.name,
        created_at=created_at,
        path=path,
        distro_id=distro_id,
        kernel_release=kernel_release,
        free_disk_gb=0,  # not captured as a typed value in the snapshot
        helper_detected=helper_detected,
    )

    # packages/, services/, configs/ — collect file paths that exist.
    pkg_dir = path / "packages"
    package_files: dict[str, Path] = {}
    if pkg_dir.is_dir():
        for key in ("explicit", "all", "aur", "pending-official", "critical"):
            p = pkg_dir / f"{key}.txt"
            if p.exists():
                package_files[key] = p

    svc_dir = path / "services"
    service_files: dict[str, Path] = {}
    if svc_dir.is_dir():
        for key in ("running", "enabled", "to-verify-status"):
            p = svc_dir / f"{key}.txt"
            if p.exists():
                service_files[key] = p

    cfg_dir = path / "configs"
    config_files: list[Path] = []
    if cfg_dir.is_dir():
        config_files = sorted(p for p in cfg_dir.iterdir() if p.is_file())

    return Snapshot(
        meta=meta,
        package_files=package_files,
        config_files=tuple(config_files),
        service_files=service_files,
        age_seconds=age_seconds,
    )


def _read_first_line(p: Path) -> str:
    """Return the first stripped line of `p`, or empty string on any error.

    Mirrors the helper in `ui/dialogs/snapshot_browser.py` so the GUI
    can be refactored to call us instead. Public-private convention is
    leading underscore; module-private utility, not exported via
    __init__.
    """
    try:
        with open(p, encoding="utf-8") as f:
            return f.readline().strip()
    except OSError:
        return ""
