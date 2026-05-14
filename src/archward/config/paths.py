"""XDG paths for archward state and config."""

from __future__ import annotations

import os
from pathlib import Path


def _xdg(env_var: str, default: str) -> Path:
    raw = os.environ.get(env_var)
    if raw:
        return Path(raw).expanduser()
    return Path.home() / default


def config_dir() -> Path:
    return _xdg("XDG_CONFIG_HOME", ".config") / "archward"


def state_dir() -> Path:
    return _xdg("XDG_STATE_HOME", ".local/state") / "archward"


def data_dir() -> Path:
    return _xdg("XDG_DATA_HOME", ".local/share") / "archward"


def cache_dir() -> Path:
    return _xdg("XDG_CACHE_HOME", ".cache") / "archward"


def snapshots_dir() -> Path:
    return state_dir() / "snapshots"


def logs_dir() -> Path:
    return state_dir() / "logs"


def lock_file() -> Path:
    return state_dir() / "archward.lock"
