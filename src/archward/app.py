"""Composition root: wire up EventBus, sudo strategy, config, and lock file."""

from __future__ import annotations

import logging
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from archward.config import paths
from archward.config.loader import load_config
from archward.events import EventBus, PhaseEvent, PhaseEventKind
from archward.logging_setup import setup_logging
from archward.models.config import ConfigModel
from archward.privilege.sudo import SudoStrategy, pick_strategy
from archward.system.distro import detect_distro

log = logging.getLogger(__name__)


def _console_subscriber(event: PhaseEvent) -> None:
    """Print PHASE_LOG, PHASE_START, PHASE_RESULT events to stdout."""
    if event.kind is PhaseEventKind.PHASE_LOG:
        print(event.message or "", flush=True)
    elif event.kind is PhaseEventKind.PHASE_START:
        msg = event.message or ""
        print(f"\n[{event.phase}] {msg}", flush=True)
    elif event.kind is PhaseEventKind.PHASE_RESULT:
        msg = event.message or ""
        print(f"  → {msg}", flush=True)


def build_event_bus(*, console: bool = True) -> EventBus:
    bus = EventBus()
    if console:
        bus.subscribe(_console_subscriber)
    return bus


def build_config(config_path: Path | None = None) -> ConfigModel:
    """Load the archward config, writing defaults on first run.

    If `config_path` is None, the default ~/.config/archward/config.toml is
    used; otherwise the given path is loaded (e.g. a `--profile NAME` path
    under ~/.config/archward/profiles/).

    Per-section validation errors fall back to that section's defaults; the
    broken file is left untouched for the user to inspect.
    """
    cfg = load_config(config_path)
    # Ensure snapshot/log dirs exist before any phase uses them.
    cfg.general.snapshot_dir.mkdir(parents=True, exist_ok=True)
    cfg.general.log_dir.mkdir(parents=True, exist_ok=True)
    return cfg


def build_sudo_strategy(cfg: ConfigModel) -> SudoStrategy:
    return pick_strategy(mode=cfg.privilege.mode, askpass_override=cfg.privilege.askpass)


def check_distro_or_exit(bus: EventBus) -> None:
    info = detect_distro()
    if not info.is_arch_based:
        bus.emit_log(
            "preflight",
            f"FAIL distro {info.id!r} is not Arch-based — refusing to run.",
        )
        print("RESULT:UPDATE_FAILED", flush=True)
        sys.exit(2)
    bus.emit_log(
        "preflight",
        f"Distro: {info.pretty_name} (id={info.id}, detected via {info.detected_via})",
    )


@contextmanager
def acquire_lock() -> Iterator[None]:
    """Acquire ~/.local/state/archward/archward.lock; raise if another instance holds it.

    Phase 1 uses a simple advisory flock — Qt's QLockFile (planned for the GUI
    in Phase 4) provides cross-process detection with stale handling, but for
    CLI-only Phase 1 a basic POSIX flock is sufficient.
    """
    import fcntl

    paths.state_dir().mkdir(parents=True, exist_ok=True)
    lock_path = paths.lock_file()
    fd = open(lock_path, "w", encoding="utf-8")
    try:
        try:
            fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print(
                f"Another archward instance is running (lock: {lock_path}). Refusing to start.",
                file=sys.stderr,
            )
            print("RESULT:UPDATE_FAILED", flush=True)
            sys.exit(3)
        fd.write(str(__import__("os").getpid()) + "\n")
        fd.flush()
        yield
    finally:
        try:
            fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        fd.close()
        try:
            lock_path.unlink()
        except OSError:
            pass


def setup_app(
    *,
    warmup_sudo: bool = True,
    config_path: Path | None = None,
) -> tuple[ConfigModel, SudoStrategy, EventBus]:
    """Build the standard three-piece app context: config, sudo strategy, event bus.

    If `warmup_sudo` is True, calls strategy.warmup() so the sudo timestamp is hot
    before any phase tries to use it — this consolidates the askpass prompt into a
    single early dialog instead of one per privileged command.

    `config_path` overrides the default config location (used by `--profile`).
    """
    cfg = build_config(config_path)
    setup_logging(cfg.general.log_dir)
    bus = build_event_bus()
    strategy = build_sudo_strategy(cfg)
    if warmup_sudo:
        ok = strategy.warmup()
        bus.emit_log("preflight", f"sudo warmup: {'ready' if ok else 'deferred — askpass will prompt at first sudo call'}")
    return cfg, strategy, bus
