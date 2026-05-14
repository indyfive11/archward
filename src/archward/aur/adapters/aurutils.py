"""Best-effort aurutils adapter.

aurutils is a script collection rather than a single drop-in pacman wrapper. A
typical update flow is:

    aur vercmp -q                   # list packages with newer versions in the repo
    aur sync -u --noconfirm         # build + sync into the local repo
    sudo pacman -Syu                # install from the local repo (separate step)

That third step is what `pipeline/update_official` does anyway, so the aurutils
adapter only runs the first two. It assumes:

  - the user has configured a local aurutils repo
  - `aur sync` knows the target repo via /etc/aurutils/sync.cfg or environment

If you rely heavily on aurutils, prefer running `aur sync -u` manually outside
archward and use `--no-aur` here. We ship this adapter so the helper-discovery
flow has somewhere to land when aurutils is the only helper installed; it is
documented as best-effort.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import threading

from archward.events import EventBus
from archward.pacman.runner import run_streaming
from archward.privilege.sudo import SudoStrategy

log = logging.getLogger(__name__)

_VERCMP_RE = re.compile(r"^(\S+)\s+(\S+)\s+(\S+)\s*$")  # "pkg old new"


class AurutilsAdapter:
    name = "aurutils"

    @classmethod
    def is_available(cls) -> bool:
        return shutil.which("aur") is not None

    def list_pending(self) -> list[tuple[str, str, str]]:
        """Use `aur vercmp -q` to list AUR packages with newer versions available."""
        try:
            r = subprocess.run(
                ["aur", "vercmp", "-q"],
                check=False,
                capture_output=True,
                text=True,
                env={**__import__("os").environ, "LANG": "C"},
            )
        except FileNotFoundError:
            return []
        pending: list[tuple[str, str, str]] = []
        for line in r.stdout.splitlines():
            m = _VERCMP_RE.match(line.strip())
            if m:
                pending.append((m.group(1), m.group(2), m.group(3)))
        return pending

    def run_update(
        self,
        ignore: list[str],
        strategy: SudoStrategy,
        bus: EventBus,
        cancel_event: threading.Event | None,
    ) -> tuple[int, list[str]]:
        if ignore:
            bus.emit_log(
                "update_aur",
                f"aurutils: --ignore is not supported by `aur sync` directly; ignoring {len(ignore)} entries.",
            )
        # `aur sync -u --noconfirm` builds+syncs to the local repo. Installation
        # comes via the next pacman -Syu — which pipeline/update_official has
        # already run. This is best-effort; build failures propagate via exit code.
        argv = ["aur", "sync", "-u", "--noconfirm"]
        return run_streaming(
            argv,
            strategy=strategy,
            bus=bus,
            phase="update_aur",
            cancel_event=cancel_event,
            use_sudo=False,
        )
