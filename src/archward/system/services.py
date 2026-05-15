"""systemctl wrappers."""

from __future__ import annotations

import subprocess


def is_active(unit: str) -> bool:
    """Return True if `systemctl is-active <unit>` reports active."""
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "--quiet", unit],
            check=False,
            capture_output=True,
        )
    except FileNotFoundError:
        return False
    return result.returncode == 0


def unit_exists(unit: str) -> bool:
    """Return True iff a unit file for `unit` resolves on disk.

    Uses `systemctl cat --no-pager <unit>` which exits 0 when the unit
    file (or any drop-in) resolves and exits non-zero when no file is
    found. Used by --detect to surface stale entries in
    services.to_verify whose backing package has been removed.
    """
    try:
        result = subprocess.run(
            ["systemctl", "cat", "--no-pager", unit],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        # systemctl missing on this host (e.g. running in a container without
        # systemd). Assume the unit cannot be verified one way or the other —
        # don't propose removal in that environment.
        return True
    return result.returncode == 0


def list_running() -> str:
    """Return raw `systemctl list-units --state=running` output."""
    return subprocess.run(
        ["systemctl", "list-units", "--type=service", "--state=running", "--no-pager", "--plain"],
        check=False,
        capture_output=True,
        text=True,
    ).stdout


def list_enabled() -> str:
    return subprocess.run(
        ["systemctl", "list-unit-files", "--state=enabled", "--type=service", "--no-pager"],
        check=False,
        capture_output=True,
        text=True,
    ).stdout
