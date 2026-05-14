"""Streaming pacman runner.

Implements the audit's subprocess buffering recipe (A4):
- bufsize=1, text=True for line-buffered stdout
- stderr merged into stdout so a single stream represents progress
- LANG=C, --noprogressbar --color=never to keep output parse-stable
- ANSI escapes stripped via logging_setup.strip_ansi before emitting
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading

from archward.events import EventBus
from archward.logging_setup import strip_ansi
from archward.privilege.sudo import SudoStrategy

log = logging.getLogger(__name__)


# Flags appended to every pacman invocation so output renders cleanly in a
# non-TTY log stream. Helpers (yay/paru) accept the same flags and pass them
# through to their inner pacman call.
_PACMAN_OUTPUT_FLAGS = ("--noprogressbar", "--color=never")


def pacman_argv(extra: list[str], noconfirm: bool, ignore: list[str]) -> list[str]:
    """Construct the pacman argv for an update. Does NOT include sudo prefix."""
    argv = ["pacman", "-Syu", *_PACMAN_OUTPUT_FLAGS]
    if noconfirm:
        argv.append("--noconfirm")
    for pkg in ignore:
        argv.extend(["--ignore", pkg])
    argv.extend(extra)
    return argv


def run_streaming(
    argv: list[str],
    *,
    strategy: SudoStrategy,
    bus: EventBus,
    phase: str,
    cancel_event: threading.Event | None = None,
    use_sudo: bool = True,
) -> tuple[int, list[str]]:
    """Run `argv`, stream stdout into the EventBus, return (exit_code, captured_lines).

    `use_sudo=True` (default) prefixes the strategy's sudo argv (e.g. `sudo -A`)
    and is correct for pacman. AUR helpers (yay/paru) refuse to run as root, so
    pass `use_sudo=False`: the helper inherits SUDO_ASKPASS via env and prompts
    for sudo internally when it needs to install built packages.

    pacman/AUR helpers are never killed mid-flight. `cancel_event` only suppresses
    further log emission — see the cancellation contract in PLAN.md.
    """
    full = [*strategy.argv_prefix(), *argv] if use_sudo else list(argv)
    env = strategy.env()
    env["LANG"] = "C"

    log.info("running: %s", " ".join(full))
    bus.emit_log(phase, "$ " + " ".join(full))

    proc = subprocess.Popen(
        full,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
        text=True,
        env=env,
    )

    assert proc.stdout is not None
    captured: list[str] = []
    cancelled = False
    for line in proc.stdout:
        cleaned = strip_ansi(line.rstrip())
        captured.append(cleaned)
        if cancel_event is not None and cancel_event.is_set():
            # We don't terminate the subprocess — pacman half-transactions corrupt
            # the database. We only stop emitting log events to the bus (still
            # capture them so post-mortem failure scanning works).
            if not cancelled:
                bus.emit_log(phase, "(cancellation requested — subprocess will be allowed to finish)")
                cancelled = True
            continue
        bus.emit_log(phase, cleaned)

    code = proc.wait()
    log.info("exited %d: %s", code, " ".join(full))
    return code, captured


def run_capture(
    argv: list[str],
    *,
    strategy: SudoStrategy,
) -> tuple[int, str, str]:
    """Run a privileged command, capture stdout/stderr, return (code, out, err)."""
    full = [*strategy.argv_prefix(), *argv]
    env = strategy.env()
    env["LANG"] = "C"
    try:
        r = subprocess.run(
            full,
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )
    except FileNotFoundError as e:
        return 127, "", str(e)
    return r.returncode, r.stdout, r.stderr


def check_pacman_db_lock() -> tuple[bool, str | None]:
    """Return (locked, owning_process_name_or_None).

    Does not auto-remove the lock — stale locks from killed pacman may indicate
    a corrupted transaction that needs manual investigation.
    """
    lock_path = "/var/lib/pacman/db.lck"
    if not os.path.exists(lock_path):
        return False, None
    try:
        # Try to read the holding PID from the lockfile. pacman 6.x writes the
        # PID; older versions write nothing. Either way fall through gracefully.
        with open(lock_path, "r", encoding="utf-8", errors="replace") as f:
            pid_str = f.read().strip()
        pid = int(pid_str) if pid_str.isdigit() else 0
        if pid > 0 and os.path.isdir(f"/proc/{pid}"):
            try:
                with open(f"/proc/{pid}/comm", "r", encoding="utf-8") as f:
                    comm = f.read().strip()
                return True, f"{comm} (pid {pid})"
            except OSError:
                return True, f"pid {pid}"
        return True, "stale lock (no live process)"
    except OSError:
        return True, "unreadable"
