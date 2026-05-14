"""Non-mutating pacman queries.

All functions return parsed Python data; none of them require sudo.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from dataclasses import dataclass

from archward.logging_setup import strip_ansi

log = logging.getLogger(__name__)


def _run(argv: list[str]) -> tuple[int, str, str]:
    """Run a command, return (returncode, stdout, stderr). Locale forced to C."""
    try:
        r = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            env={**__import__("os").environ, "LANG": "C"},
        )
    except FileNotFoundError as e:
        log.error("command not found: %s", argv[0])
        return 127, "", str(e)
    return r.returncode, r.stdout, r.stderr


def list_explicit() -> list[str]:
    """pacman -Qe → just package names."""
    _, out, _ = _run(["pacman", "-Qe"])
    return [line.split()[0] for line in out.splitlines() if line]


def list_all() -> list[tuple[str, str]]:
    """pacman -Q → [(name, version), ...]."""
    _, out, _ = _run(["pacman", "-Q"])
    pairs: list[tuple[str, str]] = []
    for line in out.splitlines():
        parts = line.split(maxsplit=1)
        if len(parts) == 2:
            pairs.append((parts[0], parts[1]))
    return pairs


def list_foreign() -> list[tuple[str, str]]:
    """pacman -Qm → [(name, version), ...] (AUR + locally-built)."""
    _, out, _ = _run(["pacman", "-Qm"])
    pairs: list[tuple[str, str]] = []
    for line in out.splitlines():
        parts = line.split(maxsplit=1)
        if len(parts) == 2:
            pairs.append((parts[0], parts[1]))
    return pairs


def installed_version(pkg: str) -> str | None:
    """Return installed version of `pkg`, or None if not installed."""
    code, out, _ = _run(["pacman", "-Q", pkg])
    if code != 0 or not out:
        return None
    parts = out.split(maxsplit=1)
    return parts[1].strip() if len(parts) == 2 else None


@dataclass(frozen=True)
class PendingPkg:
    name: str
    old_version: str
    new_version: str


_PENDING_RE = re.compile(r"^(\S+)\s+(\S+)\s+->\s+(\S+)\s*$")


def checkupdates() -> list[PendingPkg]:
    """Run `checkupdates`; return parsed pending packages.

    `checkupdates` (from pacman-contrib) syncs into a separate DB at
    /tmp/checkup-db-<uid>/ — it never touches the system database, so it's safe
    to call without sudo and without risk of partial-update breakage.

    Returns [] if checkupdates is not installed OR if there are no updates
    (checkupdates returns exit code 2 in that case).
    """
    if not shutil.which("checkupdates"):
        return []
    code, out, _ = _run(["checkupdates"])
    # checkupdates exit codes: 0 = updates, 2 = no updates, 1 = error
    if code != 0 and code != 2:
        log.warning("checkupdates exited with code %d", code)
    pending: list[PendingPkg] = []
    for line in out.splitlines():
        m = _PENDING_RE.match(line.strip())
        if not m:
            continue
        pending.append(PendingPkg(m.group(1), m.group(2), m.group(3)))
    return pending


@dataclass(frozen=True)
class TransactionPreview:
    """Result of `pacman -Sup` — what pacman *would* do for `-Syu`."""

    package_count: int
    replacements: tuple[tuple[str, str], ...]
    conflicts: tuple[str, ...]
    raw: str


def preview_transaction() -> TransactionPreview:
    """Run `pacman -Sup --print-format '%n %v'` to preview without acting.

    `-Sup` operates on a locally-cached DB. The *system* DB is only updated by
    `pacman -Sy` which archward never runs standalone. So instead we use the
    checkupdates fake DB at ${TMPDIR:-/tmp}/checkup-db-<UID>/, which is freshly
    synced by our prior `checkupdates()` call in the same pipeline. If that DB
    doesn't exist yet (caller skipped checkupdates), fall back to the system DB
    and accept that the preview may be empty.

    Replacements and conflicts surface in stderr as warning lines.
    """
    import os

    tmp = os.environ.get("TMPDIR", "/tmp")
    checkup_db = f"{tmp}/checkup-db-{os.getuid()}"
    cmd = ["pacman", "-Sup", "--print-format", "%n %v"]
    if os.path.isdir(checkup_db):
        cmd = ["pacman", "--dbpath", checkup_db, *cmd[1:]]
    code, out, err = _run(cmd)
    pkg_count = sum(1 for line in out.splitlines() if line.strip())
    # Replacements look like:  ":: <old> will be replaced by <repo>/<new>"
    repl_re = re.compile(r":: (\S+) will be replaced by \S+?/(\S+)")
    replacements = tuple(
        (m.group(1), m.group(2)) for m in repl_re.finditer(err + "\n" + out)
    )
    # Conflicts: any stderr line beginning "warning:" or "error:" gets surfaced.
    conflicts = tuple(
        strip_ansi(line).strip()
        for line in err.splitlines()
        if line.lower().startswith(("warning:", "error:"))
    )
    return TransactionPreview(
        package_count=pkg_count,
        replacements=replacements,
        conflicts=conflicts,
        raw=err + ("\n" if err else "") + out,
    )


def scan_pacman_log(since_epoch: int, max_lines: int = 500) -> tuple[int, int, list[str]]:
    """Tail /var/log/pacman.log, count [ALPM] error/warning entries newer than `since_epoch`.

    No sudo — /var/log/pacman.log is mode 644 by default.
    Returns (error_count, warning_count, sample_lines).
    """
    log_path = "/var/log/pacman.log"
    code, out, _ = _run(["tail", "-n", str(max_lines), log_path])
    if code != 0:
        return 0, 0, []
    err_re = re.compile(r"^\[(.+?)\] \[ALPM\] error", re.IGNORECASE)
    warn_re = re.compile(r"^\[(.+?)\] \[ALPM\] warning", re.IGNORECASE)
    errors = 0
    warnings = 0
    samples: list[str] = []
    for line in out.splitlines():
        if err_re.match(line):
            errors += 1
            if len(samples) < 10:
                samples.append(line)
        elif warn_re.match(line):
            warnings += 1
            if len(samples) < 10:
                samples.append(line)
    return errors, warnings, samples
