"""Distro detection — parses /etc/os-release."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

_OS_RELEASE = Path("/etc/os-release")

# Arch-based distros archward officially supports.
_NAMED_ARCH_DERIVATIVES = frozenset(
    {"arch", "endeavouros", "manjaro", "cachyos", "garuda", "artix"}
)


@dataclass(frozen=True)
class DistroInfo:
    id: str
    pretty_name: str
    is_arch_based: bool
    detected_via: str  # "ID" or "ID_LIKE" or "unknown"
    raw: dict[str, str]


def _parse_os_release(path: Path = _OS_RELEASE) -> dict[str, str]:
    """Parse /etc/os-release into a flat dict, stripping quotes from values."""
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        val = val.strip().strip('"').strip("'")
        out[key.strip()] = val
    return out


def detect_distro(path: Path = _OS_RELEASE) -> DistroInfo:
    raw = _parse_os_release(path)
    distro_id = raw.get("ID", "").strip().lower()
    pretty = raw.get("PRETTY_NAME", distro_id or "unknown")

    if distro_id in _NAMED_ARCH_DERIVATIVES:
        return DistroInfo(distro_id, pretty, True, "ID", raw)

    # Per audit B2 — fall back to ID_LIKE so newer Arch derivatives (SteamOS 3,
    # RebornOS, ArcoLinux, BlendOS, ...) work without an explicit allow-list.
    id_like = raw.get("ID_LIKE", "").split()
    if "arch" in id_like:
        return DistroInfo(distro_id or "unknown", pretty, True, "ID_LIKE", raw)

    return DistroInfo(distro_id or "unknown", pretty, False, "unknown", raw)
