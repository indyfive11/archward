"""Shared base for pacman-wrapper helpers (yay, paru).

yay and paru implement near-identical CLI: `-Qua` to list AUR updates,
`-Sua --noconfirm` to update. They diverge in flag preferences and prompt
behavior, but for archward's invocation those differences are immaterial.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import threading

from archward.events import EventBus
from archward.pacman.runner import PromptProvider, run_streaming
from archward.privilege.sudo import SudoStrategy

log = logging.getLogger(__name__)

# Output of `yay -Qua` / `paru -Qua`: "pkgname old_version -> new_version"
# (same format as checkupdates, identical regex).
_PENDING_RE = re.compile(r"^(\S+)\s+(\S+)\s+->\s+(\S+)\s*$")

# Output flags shared with pacman.runner (audit B1 + A4):
# - --noprogressbar: ASCII progress bars don't render in a non-TTY log pane
# - --color=never:   prevent ANSI escape codes from polluting the log stream
_OUTPUT_FLAGS = ("--noprogressbar", "--color=never")


class _PacmanLikeAdapter:
    """Shared implementation; subclasses set `name` + optionally override
    `interactive_extra_flags` for helper-specific 'skip the built-in review
    menus' flags (yay uses three; paru uses one)."""

    name: str  # "yay" or "paru"
    # Flags appended when running interactively (noconfirm=False). F3's
    # PKGBUILD modal does its own review, so we suppress the helper's
    # built-in $EDITOR-based menus. Override per helper.
    interactive_extra_flags: tuple[str, ...] = ()

    @classmethod
    def is_available(cls) -> bool:
        return shutil.which(cls.name) is not None

    def list_pending(self) -> list[tuple[str, str, str]]:
        """Return [(pkg, old, new), ...] for AUR updates the helper sees."""
        try:
            r = subprocess.run(
                [self.name, "-Qua"],
                check=False,
                capture_output=True,
                text=True,
                env={**__import__("os").environ, "LANG": "C"},
                timeout=60,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            log.warning("%s: list_pending timed out or binary not found", self.name)
            return []
        # `-Qua` returns 0 with no output if nothing's pending, or 0 with lines
        # if there are updates. Some helpers may return 1 for "no updates" —
        # treat both as "nothing failing".
        pending: list[tuple[str, str, str]] = []
        for line in r.stdout.splitlines():
            m = _PENDING_RE.match(line.strip())
            if m:
                pending.append((m.group(1), m.group(2), m.group(3)))
        return pending

    def run_update(
        self,
        ignore: list[str],
        strategy: SudoStrategy,
        bus: EventBus,
        cancel_event: threading.Event | None,
        *,
        noconfirm: bool = True,
        prompt_provider: PromptProvider | None = None,
    ) -> tuple[int, list[str]]:
        argv = [self.name, "-Sua", *_OUTPUT_FLAGS]
        if noconfirm:
            argv.append("--noconfirm")
        elif self.interactive_extra_flags:
            # F3's modal handles PKGBUILD review; suppress the helper's own
            # $EDITOR-based menus. Flag set is helper-specific.
            argv.extend(self.interactive_extra_flags)
        for pkg in ignore:
            argv.extend(["--ignore", pkg])
        # use_sudo=False: helpers MUST run as user. Sudo prompts inside the
        # helper inherit our SUDO_ASKPASS.
        return run_streaming(
            argv,
            strategy=strategy,
            bus=bus,
            phase="update_aur",
            cancel_event=cancel_event,
            use_sudo=False,
            prompt_provider=prompt_provider,
        )
