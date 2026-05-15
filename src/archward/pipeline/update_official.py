"""Official-repo update phase — wraps `sudo pacman -Syu`."""

from __future__ import annotations

import logging
import threading

from archward.events import EventBus
from archward.models.config import ConfigModel
from archward.pacman.runner import PromptProvider, pacman_argv, run_streaming
from archward.privilege.sudo import SudoStrategy

log = logging.getLogger(__name__)

PHASE = "update_official"


def run_official_update(
    cfg: ConfigModel,
    strategy: SudoStrategy,
    bus: EventBus,
    ignore: list[str] | None = None,
    cancel_event: threading.Event | None = None,
    *,
    prompt_provider: PromptProvider | None = None,
) -> int:
    """Stream `pacman -Syu`. Returns the pacman exit code.

    When `cfg.pacman.noconfirm=False`, the caller must supply a
    `prompt_provider` so interactive prompts surface in the GUI; otherwise
    pacman will hang on the first [Y/n]. The default GUI flow wires this
    automatically; CLI runs pin noconfirm=True so the parameter is unused.
    """
    bus.emit_start(PHASE, "Running pacman -Syu")
    argv = pacman_argv(
        list(cfg.pacman.extra_args),
        noconfirm=cfg.pacman.noconfirm,
        ignore=ignore or [],
    )
    code, _captured = run_streaming(
        argv,
        strategy=strategy,
        bus=bus,
        phase=PHASE,
        cancel_event=cancel_event,
        prompt_provider=prompt_provider if not cfg.pacman.noconfirm else None,
    )
    if code == 0:
        bus.emit_result(PHASE, "pacman -Syu completed")
    else:
        bus.emit_result(PHASE, f"pacman -Syu FAILED (exit {code})", payload={"exit_code": code})
    return code
