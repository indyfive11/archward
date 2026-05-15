"""systemctl wrappers.

v0.4.1 (F5): every systemctl subprocess call has a short timeout so a
hung systemd manager (e.g. blocked on a broken NFS mount, or DBus
deadlock) can't freeze the verify phase. On timeout the call returns
its "negative" answer (is_active → False, unit_exists → True), which
surfaces as either a FAIL row in verify or a no-prune decision in
--detect. Neither outcome corrupts state.
"""

from __future__ import annotations

import logging
import subprocess

log = logging.getLogger(__name__)

_SYSTEMCTL_TIMEOUT_S = 5


def is_active(unit: str) -> bool:
    """Return True if `systemctl is-active <unit>` reports active.

    Timeout: `_SYSTEMCTL_TIMEOUT_S`. On timeout, returns False (so the
    unit shows as inactive in verify — which is the safe-default
    outcome; the user sees a FAIL row and investigates).
    """
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "--quiet", unit],
            check=False,
            capture_output=True,
            timeout=_SYSTEMCTL_TIMEOUT_S,
        )
    except FileNotFoundError:
        return False
    except subprocess.TimeoutExpired:
        log.warning("systemctl is-active %s timed out after %ss", unit, _SYSTEMCTL_TIMEOUT_S)
        return False
    return result.returncode == 0


def unit_exists(unit: str) -> bool:
    """Return True iff a unit file for `unit` resolves on disk.

    Uses `systemctl cat --no-pager <unit>` which exits 0 when the unit
    file (or any drop-in) resolves and exits non-zero when no file is
    found. Used by --detect to surface stale entries in
    services.to_verify whose backing package has been removed.

    Timeout: `_SYSTEMCTL_TIMEOUT_S`. On timeout, returns True (do NOT
    propose pruning a unit we can't reach systemd to verify — the user
    might be on a temporarily-broken systemd state, and silently
    dropping their config is worse than missing the prune signal).
    """
    try:
        result = subprocess.run(
            ["systemctl", "cat", "--no-pager", unit],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=_SYSTEMCTL_TIMEOUT_S,
        )
    except FileNotFoundError:
        # systemctl missing on this host (e.g. running in a container without
        # systemd). Assume the unit cannot be verified one way or the other —
        # don't propose removal in that environment.
        return True
    except subprocess.TimeoutExpired:
        log.warning("systemctl cat %s timed out after %ss", unit, _SYSTEMCTL_TIMEOUT_S)
        return True
    return result.returncode == 0


def list_running() -> str:
    """Return raw `systemctl list-units --state=running` output. Empty string on timeout."""
    try:
        return subprocess.run(
            ["systemctl", "list-units", "--type=service", "--state=running", "--no-pager", "--plain"],
            check=False,
            capture_output=True,
            text=True,
            timeout=_SYSTEMCTL_TIMEOUT_S,
        ).stdout
    except subprocess.TimeoutExpired:
        log.warning("systemctl list-units timed out after %ss", _SYSTEMCTL_TIMEOUT_S)
        return "(systemctl list-units timed out)\n"


def list_enabled() -> str:
    """Return raw `systemctl list-unit-files --state=enabled` output. Empty on timeout."""
    try:
        return subprocess.run(
            ["systemctl", "list-unit-files", "--state=enabled", "--type=service", "--no-pager"],
            check=False,
            capture_output=True,
            text=True,
            timeout=_SYSTEMCTL_TIMEOUT_S,
        ).stdout
    except subprocess.TimeoutExpired:
        log.warning("systemctl list-unit-files timed out after %ss", _SYSTEMCTL_TIMEOUT_S)
        return "(systemctl list-unit-files timed out)\n"
