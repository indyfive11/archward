"""AUR helper protocol + factory.

Helpers run as the invoking user (NOT root — yay/paru refuse to run under sudo).
They prompt for sudo internally when installing built packages; SUDO_ASKPASS is
inherited via the env so the prompt routes to the user's askpass binary.
"""

from __future__ import annotations

import logging
import shutil
import threading
from typing import Protocol

from archward.events import EventBus
from archward.privilege.sudo import SudoStrategy

log = logging.getLogger(__name__)


class AurHelper(Protocol):
    """Common interface that every concrete adapter implements."""

    name: str  # binary name, e.g. "yay"

    @classmethod
    def is_available(cls) -> bool: ...

    def list_pending(self) -> list[tuple[str, str, str]]:
        """Return [(name, old_version, new_version), ...] for AUR updates."""
        ...

    def run_update(
        self,
        ignore: list[str],
        strategy: SudoStrategy,
        bus: EventBus,
        cancel_event: threading.Event | None,
    ) -> tuple[int, list[str]]:
        """Run the AUR update. Returns (exit_code, captured_lines)."""
        ...


def discover(preference: tuple[str, ...]) -> AurHelper | None:
    """First helper in `preference` whose binary is on PATH, else None."""
    # Local import to avoid cycle (adapters import from helper indirectly via models).
    from archward.aur.adapters.aurutils import AurutilsAdapter
    from archward.aur.adapters.paru import ParuAdapter
    from archward.aur.adapters.yay import YayAdapter

    adapters: dict[str, type[AurHelper]] = {
        "yay": YayAdapter,
        "paru": ParuAdapter,
        "aurutils": AurutilsAdapter,
    }
    for name in preference:
        adapter_cls = adapters.get(name)
        if adapter_cls is None:
            log.debug("unknown helper in preference: %s", name)
            continue
        if shutil.which(name):
            return adapter_cls()
    return None
