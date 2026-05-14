"""Official-repo update phase — wraps `sudo pacman -Syu`."""

from __future__ import annotations

import logging
import threading

from archward.events import EventBus
from archward.models.config import ConfigModel
from archward.pacman.runner import pacman_argv, run_streaming
from archward.privilege.sudo import SudoStrategy

log = logging.getLogger(__name__)

PHASE = "update_official"


def run_official_update(
    cfg: ConfigModel,
    strategy: SudoStrategy,
    bus: EventBus,
    ignore: list[str] | None = None,
    cancel_event: threading.Event | None = None,
) -> int:
    """Stream `pacman -Syu`. Returns the pacman exit code."""
    bus.emit_start(PHASE, "Running pacman -Syu")
    argv = pacman_argv(
        list(cfg.pacman.extra_args),
        noconfirm=cfg.pacman.noconfirm,
        ignore=ignore or [],
    )
    code, _captured = run_streaming(argv, strategy=strategy, bus=bus, phase=PHASE, cancel_event=cancel_event)
    if code == 0:
        bus.emit_result(PHASE, "pacman -Syu completed")
    else:
        bus.emit_result(PHASE, f"pacman -Syu FAILED (exit {code})", payload={"exit_code": code})
    return code
