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
