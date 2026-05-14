"""Snapshot-based rollback primitives.

Two action types in v0.2.0:
  - restore_config(snapshot, relpath)  — copy a /etc file back from a snapshot,
                                          preserving the *currently-live* file's
                                          ownership and mode (so a snapshot
                                          taken when the file was 644 doesn't
                                          loosen a since-hardened 600 file).
  - downgrade_package(pkg, version)    — install an older version from the
                                          local pacman cache via `pacman -U`.

Both are best-effort building blocks; the SnapshotBrowser UI is the supervised
front-end. Bulk variants (`restore_all_configs`, `downgrade_critical`) land
in v0.2.1; the data model is shaped so bulk is just iteration over these.

Safety invariants:
  - Every restore_config writes a `<file>.pre-rollback.bak` next to the original
    before overwriting. The user can compare or revert.
  - downgrade_package refuses to act if the requested version isn't already in
    `/var/cache/pacman/pkg/` (no network fetch — that's pacman's job, and we
    don't want to silently move beyond cached state).
  - Kernel / glibc / systemd downgrades are *allowed* but the UI is expected
    to warn explicitly (bulk variants will refuse by default in v0.2.1).
"""

from __future__ import annotations

import logging
import re
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

    kind: Literal["restore_config", "downgrade_package"]
    target: str                 # file path (for restore_config) or pkg name (for downgrade)
    from_version: str | None    # current version (informational)
    to_version: str | None      # target version (for downgrade)
    snapshot_path: Path


@dataclass(frozen=True)
class RollbackResult:
    op: RollbackOp
    success: bool
    message: str


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
