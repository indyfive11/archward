"""XDG paths for archward state and config."""

from __future__ import annotations

import os
import re
from pathlib import Path

# Profile names: alphanumeric + dash + underscore, 1-64 chars. Excludes any
# path separators, leading dots, and shell-meaningful characters so a
# malicious / typo'd `--profile` argument can't escape the profile dir.
_PROFILE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


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


def profile_dir() -> Path:
    """Directory holding named profile configs: ~/.config/archward/profiles/."""
    return config_dir() / "profiles"


def valid_profile_name(name: str) -> bool:
    """Whether `name` is a safe profile identifier (no path traversal, no
    leading dot, no shell-meaningful chars)."""
    return bool(_PROFILE_NAME_RE.match(name))


def profile_config_path(name: str) -> Path:
    """Return the config-file path for profile `name`. Raises ValueError on
    invalid names (path traversal / non-portable characters)."""
    if not valid_profile_name(name):
        raise ValueError(
            f"invalid profile name {name!r}: must match [A-Za-z0-9][A-Za-z0-9_-]{{0,63}}"
        )
    return profile_dir() / f"{name}.toml"


def iter_profiles() -> list[str]:
    """Sorted profile names found under profile_dir().

    Files whose stem fails valid_profile_name() (e.g. someone copied a
    file in by hand with a bad name) are silently ignored — they stay on
    disk but don't appear in the GUI list.
    """
    pdir = profile_dir()
    if not pdir.exists():
        return []
    return sorted(
        p.stem for p in pdir.glob("*.toml")
        if valid_profile_name(p.stem)
    )
