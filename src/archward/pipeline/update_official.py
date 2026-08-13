"""Official-repo update phase — wraps `sudo pacman -Syu`."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

from archward.events import EventBus, PhaseStatus
from archward.models.config import ConfigModel
from archward.pacman.runner import PromptProvider, pacman_argv, run_streaming
from archward.privilege.sudo import SudoStrategy

log = logging.getLogger(__name__)

PHASE = "update_official"


@dataclass(frozen=True)
class OfficialUpdateOutcome:
    """Exit code plus, on failure, pacman's last error lines (C locale) so
    the result surfaces can say WHY instead of pointing at the raw log."""

    exit_code: int
    error_lines: tuple[str, ...] = ()


def extract_error_lines(captured: list[str], limit: int = 5) -> tuple[str, ...]:
    """Pull the most relevant failure lines from captured pacman output.

    Prefers lines starting with "error" (pacman's C-locale marker, matched
    case-insensitively); falls back to the last non-empty lines when pacman
    died without one (e.g. killed, OOM).
    """
    errors = [ln.strip() for ln in captured if ln.lstrip().lower().startswith("error")]
    if errors:
        return tuple(errors[-limit:])
    tail = [ln.strip() for ln in captured if ln.strip()]
    return tuple(tail[-3:])


def run_official_update(
    cfg: ConfigModel,
    strategy: SudoStrategy,
    bus: EventBus,
    ignore: list[str] | None = None,
    cancel_event: threading.Event | None = None,
    *,
    prompt_provider: PromptProvider | None = None,
) -> OfficialUpdateOutcome:
    """Stream `pacman -Syu`. Returns the exit code + error excerpt on failure.

    When `cfg.pacman.noconfirm=False`, the caller must supply a
    `prompt_provider` so interactive prompts reach the user — the GUI wires
    its inline input row, the CLI (v0.4.18) wires a TTY provider. Without a
    provider the pipe path runs with stdin closed and pacman's prompts get
    their default answers.
    """
    bus.emit_start(PHASE, "Running pacman -Syu")
    argv = pacman_argv(
        list(cfg.pacman.extra_args),
        noconfirm=cfg.pacman.noconfirm,
        ignore=ignore or [],
    )
    code, captured = run_streaming(
        argv,
        strategy=strategy,
        bus=bus,
        phase=PHASE,
        cancel_event=cancel_event,
        prompt_provider=prompt_provider if not cfg.pacman.noconfirm else None,
    )
    if code == 0:
        bus.emit_result(PHASE, "pacman -Syu completed", PhaseStatus.PASS)
        return OfficialUpdateOutcome(exit_code=0)
    bus.emit_result(PHASE, f"pacman -Syu FAILED (exit {code})", PhaseStatus.FAIL)
    return OfficialUpdateOutcome(exit_code=code, error_lines=extract_error_lines(captured))
