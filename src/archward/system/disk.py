"""Disk-space helpers."""

from __future__ import annotations

import os
from pathlib import Path


def free_gb(path: Path | str = "/") -> int:
    """Return free space on the filesystem holding `path`, as integer GB (1 GB = 1e9)."""
    st = os.statvfs(str(path))
    free_bytes = st.f_bavail * st.f_frsize
    return int(free_bytes // 1_000_000_000)
