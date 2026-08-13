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
def try_acquire_lock() -> Iterator[bool]:
    """Try to acquire ~/.local/state/archward/archward.lock without exiting.

    Yields True when the lock was acquired (released on context exit) or
    False when another instance holds it. This is the primitive run_pipeline
    uses (v0.4.17) so BOTH front-ends are covered — previously only the CLI
    locked, and a GUI run could race a concurrent CLI `archward` update.
    """
    import fcntl
    import os

    state_root = paths.state_dir()
    state_root.mkdir(parents=True, exist_ok=True)
    # v0.5 hardening: the state root holds snapshots of /etc content and
    # cached feeds — keep it private. One-time chmod so existing installs
    # converge too; ONLY the state root, never the user-configurable
    # snapshot/log dirs (which may deliberately live elsewhere).
    try:
        if (state_root.stat().st_mode & 0o777) != 0o700:
            state_root.chmod(0o700)
    except OSError:
        pass
    lock_path = paths.lock_file()
    fd = open(lock_path, "w", encoding="utf-8")
    acquired = False
    try:
        try:
            fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except OSError:
            pass
        if acquired:
            fd.write(str(os.getpid()) + "\n")
            fd.flush()
        yield acquired
    finally:
        if acquired:
            try:
                fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        fd.close()
        if acquired:
            try:
                lock_path.unlink()
            except OSError:
                pass


@contextmanager
def acquire_lock() -> Iterator[None]:
    """CLI-facing wrapper around try_acquire_lock(): exits 3 on contention.

    Phase 1 used a simple advisory flock; the primitive now lives in
    try_acquire_lock() so run_pipeline can share it without the sys.exit.
    """
    with try_acquire_lock() as acquired:
        if not acquired:
            print(
                f"Another archward instance is running (lock: {paths.lock_file()}). Refusing to start.",
                file=sys.stderr,
            )
            print("RESULT:UPDATE_FAILED", flush=True)
            sys.exit(3)
        yield


def setup_app(
    *,
    warmup_sudo: bool = True,
    console: bool = True,
    config_path: Path | None = None,
) -> tuple[ConfigModel, SudoStrategy, EventBus]:
    """Build the standard three-piece app context: config, sudo strategy, event bus.

    If `warmup_sudo` is True, calls strategy.warmup() so the sudo timestamp is hot
    before any phase tries to use it — this consolidates the askpass prompt into a
    single early dialog instead of one per privileged command. `console=False`
    (v0.5) skips the stdout event subscriber for front-ends with their own sink
    (a GUI attaches a Qt bridge; sudo warmup stays the caller's business there —
    pass warmup_sudo=False and keep the async worker).

    `config_path` overrides the default config location (used by `--profile`).
    """
    cfg = build_config(config_path)
    setup_logging(cfg.general.log_dir, keep_logs=cfg.general.keep_logs)
    bus = build_event_bus(console=console)
    strategy = build_sudo_strategy(cfg)
    if warmup_sudo:
        ok = strategy.warmup()
        bus.emit_log("preflight", f"sudo warmup: {'ready' if ok else 'deferred — askpass will prompt at first sudo call'}")
    return cfg, strategy, bus
