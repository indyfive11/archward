"""Kernel version helpers."""

from __future__ import annotations

import os


def running_kernel() -> str:
    """Return the running kernel release (uname -r)."""
    return os.uname().release
