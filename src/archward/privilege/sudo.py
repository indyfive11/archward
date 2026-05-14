"""Sudo strategy abstraction.

Phase 1 ships two strategies:

- AskpassStrategy   — sets SUDO_ASKPASS to a discovered askpass binary, uses `sudo -A`.
- PersistentSudoStrategy — wraps Askpass and runs `sudo -A -v` upfront so the timestamp
                            stays warm for the whole pipeline.

Headless / no-DISPLAY fallback is a no-op for Phase 1 — the user is expected to have
either (a) an askpass binary installed, or (b) NOPASSWD configured for pacman. Pkexec
fallback is reserved for Phase 4.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from collections.abc import Iterable
from typing import Protocol

log = logging.getLogger(__name__)

_ASKPASS_CANDIDATES = (
    "ksshaskpass",
    "lxqt-openssh-askpass",
    "ssh-askpass",
    "/usr/lib/openssh/ssh-askpass",
    "/usr/lib/ssh/ssh-askpass",
    "x11-ssh-askpass",
)


def discover_askpass(override: str = "") -> str | None:
    """Return the first existing askpass binary path, or None if none found."""
    if override:
        if os.path.isabs(override) and os.access(override, os.X_OK):
            return override
        found = shutil.which(override)
        if found:
            return found
        return None
    for candidate in _ASKPASS_CANDIDATES:
        path = shutil.which(candidate)
        if path:
            return path
        if os.path.isabs(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


class SudoStrategy(Protocol):
    askpass_path: str | None

    def warmup(self) -> bool:
        ...

    def env(self) -> dict[str, str]:
        ...

    def argv_prefix(self) -> list[str]:
        ...


class AskpassStrategy:
    """Standard askpass strategy — every sudo call uses `sudo -A`."""

    def __init__(self, askpass_path: str | None) -> None:
        self.askpass_path = askpass_path

    def warmup(self) -> bool:
        # Askpass has no separate warmup step; the askpass binary is invoked when
        # sudo asks for a password. Calling `sudo -n true` here lets us notice if
        # the NOPASSWD path is already available.
        try:
            r = subprocess.run(["sudo", "-n", "true"], check=False, capture_output=True)
            return r.returncode == 0
        except FileNotFoundError:
            log.error("sudo binary not found")
            return False

    def env(self) -> dict[str, str]:
        env = {**os.environ}
        if self.askpass_path:
            env["SUDO_ASKPASS"] = self.askpass_path
        return env

    def argv_prefix(self) -> list[str]:
        # -A → use askpass when password needed; -n is NOT set so it falls through
        # to askpass if the timestamp is cold.
        return ["sudo", "-A"]


class PersistentSudoStrategy:
    """Askpass + a background refresh of the sudo timestamp.

    Phase 1 implementation is single-shot: a `sudo -A -v` at warmup. A future
    refresh loop would be a daemon thread that re-runs `-v` every ~4 min; in
    practice a typical update completes well inside the sudo timeout, so the
    upfront warmup is sufficient for v1.
    """

    def __init__(self, askpass_path: str | None) -> None:
        self.askpass_path = askpass_path
        self._inner = AskpassStrategy(askpass_path)

    def warmup(self) -> bool:
        env = self._inner.env()
        try:
            r = subprocess.run(
                ["sudo", "-A", "-v"], check=False, env=env, capture_output=True
            )
        except FileNotFoundError:
            log.error("sudo binary not found")
            return False
        return r.returncode == 0

    def env(self) -> dict[str, str]:
        return self._inner.env()

    def argv_prefix(self) -> list[str]:
        return self._inner.argv_prefix()


def pick_strategy(mode: str = "auto", askpass_override: str = "") -> SudoStrategy:
    """Resolve a concrete strategy by config mode."""
    askpass_path = discover_askpass(askpass_override)

    if mode in ("auto", "persistent_sudo"):
        return PersistentSudoStrategy(askpass_path)
    if mode == "askpass":
        return AskpassStrategy(askpass_path)
    # pkexec mode is reserved for Phase 4 — fall through to askpass for now.
    log.warning("sudo mode %r not yet implemented; using askpass strategy", mode)
    return AskpassStrategy(askpass_path)


def build_argv(strategy: SudoStrategy, argv: Iterable[str]) -> list[str]:
    """Prepend the strategy's sudo prefix to `argv`."""
    return [*strategy.argv_prefix(), *argv]
