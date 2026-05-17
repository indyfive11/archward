"""Snapshot-based rollback primitives.

Granular (v0.2.0):
  - restore_config(snapshot, relpath)  — copy a /etc file back from a snapshot,
                                          preserving the *currently-live* file's
                                          ownership and mode.
  - downgrade_package(pkg, version)    — install a cached version via pacman -U.

Bulk (v0.2.2):
  - restore_all_configs(snapshot, strategy)  — iterate all captured configs.
                                                Each file gets its own
                                                .pre-rollback.bak (per-file
                                                rollback paths preserved).
  - apply_all_packages(snapshot, strategy, cfg, include_boot_critical=False)
                                              — single atomic `pacman -U
                                                pkg1 pkg2 ...` so the
                                                transaction either fully
                                                succeeds or fully rolls back.
                                                Refuses by default when any
                                                boot-critical pkg is in the
                                                set (glibc / systemd / openssl);
                                                caller passes the override flag.

Safety invariants:
  - Every restore_config writes a `<file>.pre-rollback.bak` before overwriting.
  - downgrade_package refuses if the requested version isn't already in
    `/var/cache/pacman/pkg/` (no network fetch).
  - Bulk operations should be preceded by an auto-snapshot (taken by the UI
    layer before invoking the primitives) so rollback-of-rollback works.
"""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from archward.pacman.runner import run_capture
from archward.privilege.sudo import SudoStrategy

log = logging.getLogger(__name__)

PACMAN_CACHE_DIR = Path("/var/cache/pacman/pkg")


@dataclass(frozen=True)
class RollbackOp:
    """A discrete rollback action queued by the UI."""

    kind: Literal["restore_config", "downgrade_package", "reinstall_from_repos", "reinstall_from_aur"]
    target: str                 # file path (for restore_config) or pkg name (for downgrade)
    from_version: str | None    # current version (informational)
    to_version: str | None      # target version (for downgrade)
    snapshot_path: Path


@dataclass(frozen=True)
class RollbackResult:
    op: RollbackOp
    success: bool
    message: str


@dataclass(frozen=True)
class RemovedPackage:
    """A package present in a snapshot's all.txt that is no longer installed."""

    name: str
    snapshot_version: str
    was_explicit: bool   # listed in explicit.txt at snapshot time
    was_aur: bool        # listed in aur.txt at snapshot time
    cache_path: Path | None  # exact snapshot version in /var/cache/pacman/pkg/, or None


# ── Snapshot reading ─────────────────────────────────────────────────────


def parse_critical_packages(snapshot_path: Path) -> list[tuple[str, str]]:
    """Read snapshot_path/packages/critical.txt and return [(name, version), ...].

    The file is human-readable (the snapshot phase writes it). Format:

        === Critical package versions pre-update ===
        linux: 7.0.5.arch1-1
        glibc: 2.40-1
        ...

        === AUR / foreign packages ===
        radarr 6.0.4.10291-1
        ...

    We only parse the colon-separated lines (the "official" section); AUR lines
    use space separators and aren't usable for `pacman -U` cache lookup.
    """
    path = snapshot_path / "packages" / "critical.txt"
    if not path.exists():
        return []
    pairs: list[tuple[str, str]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("=") or line.startswith("(") or ":" not in line:
            continue
        name, _, version = line.partition(":")
        name = name.strip()
        version = version.strip()
        if version in ("", "not installed"):
            continue
        # Skip AUR-section lines, which use space-separated `name version` —
        # versions with epoch (`1:0.13-0`) collide with our colon-split logic
        # and produce bogus names like "gossip-bin 1". Real colon-format
        # entries from the official/kernel sections have no space in the name.
        if " " in name:
            continue
        pairs.append((name, version))
    return pairs


def read_installed_packages_at_snapshot(snapshot_path: Path) -> dict[str, str]:
    """Parse snapshot_path/packages/all.txt → dict of {pkg_name: version}.

    all.txt is the verbatim `pacman -Q` output captured at snapshot time —
    space-separated `<name> <version>` lines. This is the authoritative
    source of "what was installed when the snapshot ran" and is used as a
    fallback when critical.txt didn't track a package (e.g. pre-v0.2.0
    snapshots that missed kernel packages).
    """
    path = snapshot_path / "packages" / "all.txt"
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split(maxsplit=1)
        if len(parts) == 2:
            out[parts[0]] = parts[1]
    return out


def packages_removed_since_snapshot(snapshot_path: Path) -> list[RemovedPackage]:
    """Return packages present in the snapshot but no longer installed.

    Sorted so explicitly-installed packages come before deps, so reinstalling
    a parent package pulls in its deps automatically via pacman's dependency
    resolution.
    """
    from archward.pacman import query as pq

    snap_pkgs = read_installed_packages_at_snapshot(snapshot_path)
    if not snap_pkgs:
        return []

    installed = {name for name, _ in pq.list_all()}
    removed_names = {name for name in snap_pkgs if name not in installed}
    if not removed_names:
        return []

    explicit: set[str] = set()
    explicit_txt = snapshot_path / "packages" / "explicit.txt"
    if explicit_txt.exists():
        explicit = {ln.strip() for ln in explicit_txt.read_text().splitlines() if ln.strip()}

    aur_names: set[str] = set()
    aur_txt = snapshot_path / "packages" / "aur.txt"
    if aur_txt.exists():
        for line in aur_txt.read_text().splitlines():
            parts = line.strip().split()
            if parts:
                aur_names.add(parts[0])

    result: list[RemovedPackage] = []
    for name in sorted(removed_names, key=lambda n: (0 if n in explicit else 1, n)):
        result.append(RemovedPackage(
            name=name,
            snapshot_version=snap_pkgs[name],
            was_explicit=(name in explicit),
            was_aur=(name in aur_names),
            cache_path=find_package_in_cache(name, snap_pkgs[name]),
        ))
    return result


def critical_packages_with_kernel_fallback(
    snapshot_path: Path,
    kernel_patterns: tuple[str, ...] = (),
    kernel_pattern_exclude: tuple[str, ...] = (),
) -> list[tuple[str, str]]:
    """Return rollback candidates for the snapshot.

    Prefers the curated critical.txt entries; falls back to all.txt for
    kernel-pattern matches that critical.txt missed (pre-v0.2.0 snapshots
    didn't record those). Order: critical.txt entries first, then any kernel
    additions sorted alphabetically.
    """
    import fnmatch

    pairs = parse_critical_packages(snapshot_path)
    if not kernel_patterns:
        return pairs

    in_critical = {name for name, _ in pairs}
    installed = read_installed_packages_at_snapshot(snapshot_path)

    extra: list[tuple[str, str]] = []
    for name, version in installed.items():
        if name in in_critical:
            continue
        if any(fnmatch.fnmatch(name, pat) for pat in kernel_pattern_exclude):
            continue
        if any(fnmatch.fnmatch(name, pat) for pat in kernel_patterns):
            extra.append((name, version))
    return pairs + sorted(extra)


def list_snapshot_configs(snapshot_path: Path) -> list[tuple[str, Path]]:
    """Return [(restore_target_relpath, snapshot_file_path), ...] for the snapshot's
    captured configs.

    The relpath maps each snapshot file to its live location. The snapshot phase
    flattens names (`grub-default` rather than `default/grub`) so we map back
    explicitly here — keep this in sync with `pipeline/snapshot.py`.
    """
    configs_dir = snapshot_path / "configs"
    if not configs_dir.is_dir():
        return []

    # Snapshot filename → live /etc relpath. Add new entries here when
    # snapshot.py grows new universal-config captures.
    mapping = {
        "pacman.conf": "etc/pacman.conf",
        "mirrorlist": "etc/pacman.d/mirrorlist",
        "fstab": "etc/fstab",
        "grub-default": "etc/default/grub",
        "sshd_config": "etc/ssh/sshd_config",
        "resolved.conf": "etc/systemd/resolved.conf",
        # tar.gz archives aren't restored as single files — handled separately
        # when bulk-restore lands.
    }
    out: list[tuple[str, Path]] = []
    for snap_name, live_rel in mapping.items():
        snap_file = configs_dir / snap_name
        if snap_file.exists():
            out.append((live_rel, snap_file))
    return out


# ── Restore primitives ───────────────────────────────────────────────────


def restore_config(
    op: RollbackOp,
    snapshot_file: Path,
    strategy: SudoStrategy,
) -> RollbackResult:
    """Copy `snapshot_file` back to `op.target` (an absolute /etc path).

    Sequence:
      1. stat() the live file to capture current owner/mode.
      2. cp -a the live file to <target>.pre-rollback.bak (rollback-of-rollback).
      3. cp -a the snapshot file over the live target.
      4. chown + chmod the restored file back to the live file's owner/mode.
    """
    live = Path(op.target)
    try:
        st = live.stat()
        uid, gid, mode = st.st_uid, st.st_gid, st.st_mode & 0o7777
    except FileNotFoundError:
        # No live file — restore without perm preservation (use snapshot's perms).
        uid = gid = None
        mode = None
    except OSError as e:
        return RollbackResult(op, False, f"could not stat live file: {e}")

    backup = live.with_suffix(live.suffix + ".pre-rollback.bak")
    if live.exists():
        code, _, err = run_capture(["cp", "-a", str(live), str(backup)], strategy=strategy)
        if code != 0:
            return RollbackResult(op, False, f"backup failed: {err.strip()}")

    code, _, err = run_capture(["cp", "-a", str(snapshot_file), str(live)], strategy=strategy)
    if code != 0:
        return RollbackResult(op, False, f"cp from snapshot failed: {err.strip()}")

    if uid is not None and gid is not None:
        code, _, err = run_capture(
            ["chown", f"{uid}:{gid}", str(live)], strategy=strategy
        )
        if code != 0:
            return RollbackResult(op, False, f"chown failed: {err.strip()}")

    if mode is not None:
        code, _, err = run_capture(
            ["chmod", f"{mode & 0o7777:o}", str(live)], strategy=strategy
        )
        if code != 0:
            return RollbackResult(op, False, f"chmod failed: {err.strip()}")

    return RollbackResult(op, True, f"restored {live} (backup at {backup})")


# ── Package downgrade ───────────────────────────────────────────────────


# pacman cache filenames look like:
#   <name>-<version>-<arch>.pkg.tar.zst
#   linux-7.0.5.arch1-1-x86_64.pkg.tar.zst
#   glibc-2.40-1-x86_64.pkg.tar.zst
#
# We can't trivially split on `-` because both `name` and `version` can contain
# dashes (kernel pkgrel `-1`, etc). Strategy: anchor on the literal version we
# want, with the cache filename pattern `^<name>-<version>-<arch>.pkg.tar`.
_CACHE_SUFFIX_RE = re.compile(r"-[^-]+\.pkg\.tar\.(zst|xz|gz)$")


def find_package_in_cache(
    pkg_name: str,
    target_version: str,
    cache_dir: Path = PACMAN_CACHE_DIR,
) -> Path | None:
    """Locate `<pkg_name>-<target_version>-<arch>.pkg.tar.zst` in the pacman cache.

    Returns the first match (any arch suffix), or None if absent.
    """
    if not cache_dir.is_dir():
        return None
    prefix = f"{pkg_name}-{target_version}-"
    for entry in cache_dir.iterdir():
        if not entry.is_file():
            continue
        name = entry.name
        if not name.startswith(prefix):
            continue
        if not _CACHE_SUFFIX_RE.search(name):
            continue
        return entry
    return None


# ── Bulk operations ──────────────────────────────────────────────────────


# Boot-critical packages — downgrading these can leave the system unbootable.
# Bulk operations refuse to include them by default; the UI passes
# `include_boot_critical=True` after a Type-YES confirmation.
BOOT_CRITICAL = frozenset({
    "glibc",
    "lib32-glibc",
    "systemd",
    "systemd-libs",
    "openssl",
    "lib32-openssl",
})


@dataclass(frozen=True)
class BulkResult:
    """Outcome of a bulk rollback operation."""

    success: bool
    message: str
    changed: tuple[tuple[str, str, str], ...]   # (name, from_version, to_version) for packages, or (file_path, "", "") for configs
    skipped: tuple[tuple[str, str], ...]        # (name_or_path, reason)
    per_item_results: tuple[RollbackResult, ...] = ()  # populated for configs (one per file)


def restore_all_configs(
    snapshot_path: Path,
    strategy: SudoStrategy,
) -> BulkResult:
    """Restore every captured config in `snapshot_path` to its /etc location.

    Each file is restored independently with its own `.pre-rollback.bak`.
    Failures don't abort the rest — every file gets a try, and the BulkResult
    lists per-file outcomes.
    """
    files = list_snapshot_configs(snapshot_path)
    if not files:
        return BulkResult(
            success=True,
            message="no configs captured in snapshot",
            changed=(),
            skipped=(),
        )

    changed: list[tuple[str, str, str]] = []
    skipped: list[tuple[str, str]] = []
    results: list[RollbackResult] = []
    for live_rel, snap_file in files:
        live_target = "/" + live_rel
        op = RollbackOp(
            kind="restore_config",
            target=live_target,
            from_version=None,
            to_version=None,
            snapshot_path=snapshot_path,
        )
        result = restore_config(op, snap_file, strategy)
        results.append(result)
        if result.success:
            changed.append((live_target, "", ""))
        else:
            skipped.append((live_target, result.message))

    msg = f"restored {len(changed)}/{len(files)} configs"
    if skipped:
        msg += f" ({len(skipped)} failed)"
    return BulkResult(
        success=not skipped,
        message=msg,
        changed=tuple(changed),
        skipped=tuple(skipped),
        per_item_results=tuple(results),
    )


def plan_bulk_package_apply(
    snapshot_path: Path,
    kernel_patterns: tuple[str, ...],
    kernel_pattern_exclude: tuple[str, ...],
) -> tuple[list[tuple[str, str, str, Path]], list[tuple[str, str]]]:
    """Compute the package-rollback plan against the current installed state.

    Returns (changes, skipped):
      changes: [(name, current_version, target_version, cache_path), ...]
      skipped: [(name, reason), ...]

    A package is in `changes` when:
      - The snapshot lists a different version than what's installed.
      - The target version is found in /var/cache/pacman/pkg/.
    A package is in `skipped` when the cache doesn't have its version.
    """
    from archward.pacman import query as pq

    pairs = critical_packages_with_kernel_fallback(
        snapshot_path,
        kernel_patterns=kernel_patterns,
        kernel_pattern_exclude=kernel_pattern_exclude,
    )
    installed = {n: v for n, v in pq.list_all()}

    changes: list[tuple[str, str, str, Path]] = []
    skipped: list[tuple[str, str]] = []
    for name, snap_version in pairs:
        current = installed.get(name)
        if current is None:
            skipped.append((name, "not installed currently"))
            continue
        if current == snap_version:
            continue  # unchanged — silent skip
        cache_path = find_package_in_cache(name, snap_version)
        if cache_path is None:
            skipped.append((name, f"version {snap_version} not in /var/cache/pacman/pkg/"))
            continue
        changes.append((name, current, snap_version, cache_path))
    return changes, skipped


def apply_all_packages(
    snapshot_path: Path,
    strategy: SudoStrategy,
    kernel_patterns: tuple[str, ...],
    kernel_pattern_exclude: tuple[str, ...],
    *,
    include_boot_critical: bool = False,
) -> BulkResult:
    """Apply all snapshot package versions via a single `pacman -U`.

    Atomic by virtue of pacman's transaction. Refuses if any package in
    the change set is in BOOT_CRITICAL unless `include_boot_critical=True`
    (the UI gates this behind a Type-YES confirmation).
    """
    changes, skipped = plan_bulk_package_apply(
        snapshot_path, kernel_patterns, kernel_pattern_exclude
    )

    if not changes:
        return BulkResult(
            success=True,
            message="nothing to apply (all packages already at snapshot versions)",
            changed=(),
            skipped=tuple(skipped),
        )

    boot_critical_in_set = [name for name, _c, _t, _p in changes if name in BOOT_CRITICAL]
    if boot_critical_in_set and not include_boot_critical:
        return BulkResult(
            success=False,
            message=(
                f"refused: boot-critical packages in set "
                f"({', '.join(boot_critical_in_set)}). Override required."
            ),
            changed=(),
            skipped=tuple(skipped),
        )

    argv = ["pacman", "-U", "--noconfirm"] + [str(p) for _n, _c, _t, p in changes]
    code, _, err = run_capture(argv, strategy=strategy)

    if code != 0:
        return BulkResult(
            success=False,
            message=f"pacman -U failed: {err.strip() or 'exit ' + str(code)}",
            changed=(),
            skipped=tuple(skipped),
        )

    return BulkResult(
        success=True,
        message=f"applied {len(changes)} package(s)",
        changed=tuple((n, c, t) for n, c, t, _p in changes),
        skipped=tuple(skipped),
    )


def downgrade_package(op: RollbackOp, strategy: SudoStrategy) -> RollbackResult:
    """Run `sudo pacman -U <cached package>` to downgrade `op.target` to `op.to_version`.

    Refuses to act if the target version isn't already in the pacman cache —
    we deliberately don't fetch from the network here.
    """
    if op.to_version is None:
        return RollbackResult(op, False, "downgrade requires a target version")

    cache_path = find_package_in_cache(op.target, op.to_version)
    if cache_path is None:
        return RollbackResult(
            op,
            False,
            f"version {op.to_version} not present in {PACMAN_CACHE_DIR}/",
        )

    code, _, err = run_capture(
        ["pacman", "-U", str(cache_path), "--noconfirm"],
        strategy=strategy,
    )
    if code != 0:
        return RollbackResult(op, False, f"pacman -U failed: {err.strip()}")
    return RollbackResult(op, True, f"downgraded {op.target} to {op.to_version}")


def reinstall_from_repos(op: RollbackOp, strategy: SudoStrategy) -> RollbackResult:
    """Reinstall `op.target` from official repos via `pacman -S --noconfirm`."""
    code, _, err = run_capture(
        ["pacman", "-S", "--noconfirm", op.target],
        strategy=strategy,
    )
    if code != 0:
        return RollbackResult(op, False, f"pacman -S failed: {err.strip()}")
    return RollbackResult(op, True, f"reinstalled {op.target} from repos")


def reinstall_from_aur(
    op: RollbackOp, helper_name: str, strategy: SudoStrategy
) -> RollbackResult:
    """Reinstall `op.target` from the AUR via the named helper.

    AUR helpers run as the invoking user (not root) and escalate internally,
    so this uses subprocess directly with the strategy's env (SUDO_ASKPASS) rather
    than run_capture which would add an unwanted sudo prefix.
    """
    try:
        r = subprocess.run(
            [helper_name, "-S", "--noconfirm", op.target],
            capture_output=True,
            text=True,
            env=strategy.env(),
            timeout=300,
        )
    except Exception as e:  # noqa: BLE001
        return RollbackResult(op, False, str(e))
    if r.returncode == 0:
        return RollbackResult(op, True, f"reinstalled {op.target} from AUR via {helper_name}")
    return RollbackResult(op, False, (r.stdout + r.stderr).strip())
