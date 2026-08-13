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
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Protocol

log = logging.getLogger(__name__)

_ASKPASS_CANDIDATES = (
    "ksshaskpass",                          # KDE
    "gnome-ssh-askpass",                    # GNOME / GTK (openssh-askpass)
    "lxqt-openssh-askpass",                 # LXQt
    "ssh-askpass",                          # generic
    "/usr/lib/openssh/gnome-ssh-askpass",   # Debian/Ubuntu path
    "/usr/lib/openssh/ssh-askpass",
    "/usr/lib/ssh/ssh-askpass",
    "x11-ssh-askpass",
)


def _bundled_askpass() -> str | None:
    """Locate archward's own Qt askpass (`archward-askpass` console script).

    Checked next to the running interpreter first so venv installs work even
    when the venv's bin dir isn't on PATH (e.g. launched from a desktop file),
    then on PATH for the normal packaged install (/usr/bin/archward-askpass).
    """
    sibling = Path(sys.executable).parent / "archward-askpass"
    if sibling.is_file() and os.access(sibling, os.X_OK):
        return str(sibling)
    return shutil.which("archward-askpass")


def discover_askpass(override: str = "") -> str | None:
    """Return the first existing askpass binary path, or None if none found.

    Detection order:
    1. config override (if set and valid)
    2. $SUDO_ASKPASS env var (set automatically by KDE and some DEs)
    3. known native askpass programs (_ASKPASS_CANDIDATES)
    4. archward's bundled Qt askpass — always present on a packaged install,
       so any desktop without a native askpass (Cinnamon, GNOME, Xfce, …)
       still gets a working prompt with no extra packages

    v0.4.1 (F11): when `override` is set but invalid, log a clear warning
    and FALL BACK to the auto-detection chain (rather than silently
    returning None). Previously a misconfigured override silently
    disabled askpass — sudo would then block waiting for TTY input which
    is invisible in the GUI session.
    """
    if override:
        if os.path.isabs(override) and os.access(override, os.X_OK):
            return override
        found = shutil.which(override)
        if found:
            return found
        log.warning(
            "Configured askpass %r not found / not executable; "
            "falling back to auto-detection.", override,
        )
    env_askpass = os.environ.get("SUDO_ASKPASS", "")
    if env_askpass and os.access(env_askpass, os.X_OK):
        # v0.5: still honored (rejecting would break the documented
        # user-askpass pattern and enforces no real boundary — a same-uid
        # attacker can edit config.toml anyway), but say so loudly when it
        # points outside the system trees so a poisoned env var is visible.
        if not env_askpass.startswith(("/usr/", "/usr/local/")):
            log.warning(
                "Honoring $SUDO_ASKPASS=%s from outside /usr — this binary "
                "will receive your sudo password. Set privilege.askpass in "
                "config.toml to pin a trusted path instead.",
                env_askpass,
            )
        return env_askpass
    for candidate in _ASKPASS_CANDIDATES:
        path = shutil.which(candidate)
        if path:
            return path
        if os.path.isabs(candidate) and os.access(candidate, os.X_OK):
            return candidate
    bundled = _bundled_askpass()
    if bundled:
        log.info("No native askpass found; using bundled Qt askpass at %s", bundled)
        return bundled
    log.error(
        "No askpass binary available — sudo will block on TTY input. "
        "Install one of %s, or configure NOPASSWD for pacman.",
        ", ".join(_ASKPASS_CANDIDATES[:3]),
    )
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
            r = subprocess.run(
                ["sudo", "-n", "true"],
                check=False,
                capture_output=True,
                timeout=5,
            )
            return r.returncode == 0
        except FileNotFoundError:
            log.error("sudo binary not found")
            return False
        except subprocess.TimeoutExpired:
            log.warning("sudo -n true timed out")
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
            # The askpass dialog legitimately waits on a human, so the timeout
            # is generous (sudo's own passwd_timeout default is 5 minutes) —
            # but it must exist: a wedged/crashed askpass would otherwise hang
            # the WarmupWorker thread forever.
            r = subprocess.run(
                ["sudo", "-A", "-v"], check=False, env=env, capture_output=True,
                timeout=300,
            )
        except FileNotFoundError:
            log.error("sudo binary not found")
            return False
        except subprocess.TimeoutExpired:
            log.warning("sudo -A -v warmup timed out after 300s (askpass wedged?)")
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
