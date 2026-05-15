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
import pty
import select
import signal
import subprocess
import threading
from typing import Callable

from archward.events import EventBus
from archward.logging_setup import strip_ansi
from archward.pacman.prompts import PromptKind, detect_prompt
from archward.privilege.sudo import SudoStrategy

# Idle threshold (seconds) before a partial-line buffer is checked against
# PROMPT_PATTERNS. Pacman flushes prompts immediately; 200ms is comfortable
# headroom over the worst-case stdio latency without making the UI laggy.
_PROMPT_IDLE_S = 0.2

# Type of the optional GUI callback that resolves prompts. Signature is
# (line, kind) → response_string. Returning "" cancels the subprocess.
PromptProvider = Callable[[str, PromptKind], str]

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
    prompt_provider: PromptProvider | None = None,
) -> tuple[int, list[str]]:
    """Run `argv`, stream stdout into the EventBus, return (exit_code, captured_lines).

    `use_sudo=True` (default) prefixes the strategy's sudo argv (e.g. `sudo -A`)
    and is correct for pacman. AUR helpers (yay/paru) refuse to run as root, so
    pass `use_sudo=False`: the helper inherits SUDO_ASKPASS via env and prompts
    for sudo internally when it needs to install built packages.

    When `prompt_provider is None` (default), uses the legacy pipe-based path:
    pacman runs with --noconfirm, output streams one-way, cancel_event only
    suppresses further log emission (subprocess is never killed mid-flight to
    avoid half-transactions corrupting the pacman DB).

    When `prompt_provider` is set, uses a PTY-backed path so pacman flushes
    interactive prompts. Buffered partial lines are matched against
    `prompts.PROMPT_PATTERNS`; on a match, `prompt_provider(line, kind)` is
    invoked and its return string is written to the subprocess stdin. A
    returned empty string signals cancellation — the subprocess group gets
    SIGINT, which pacman handles cleanly between transactions (no DB damage).
    """
    full = [*strategy.argv_prefix(), *argv] if use_sudo else list(argv)
    env = strategy.env()
    env["LANG"] = "C"

    log.info("running: %s", " ".join(full))
    bus.emit_log(phase, "$ " + " ".join(full))

    if prompt_provider is None:
        return _run_pipe(full, env, bus, phase, cancel_event)
    return _run_pty(full, env, bus, phase, cancel_event, prompt_provider)


def _run_pipe(
    full: list[str],
    env: dict[str, str],
    bus: EventBus,
    phase: str,
    cancel_event: threading.Event | None,
) -> tuple[int, list[str]]:
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


def _run_pty(
    full: list[str],
    env: dict[str, str],
    bus: EventBus,
    phase: str,
    cancel_event: threading.Event | None,
    prompt_provider: PromptProvider,
) -> tuple[int, list[str]]:
    """Interactive path: PTY-backed subprocess with prompt detection.

    Layout:
      - pty.openpty() yields (master_fd, slave_fd).
      - Subprocess gets slave_fd as stdin/stdout/stderr and runs in its own
        session (preexec_fn=os.setsid) so SIGINT can be sent to the process
        group cleanly on user cancel.
      - Reader loop: select on master_fd with _PROMPT_IDLE_S timeout. On
        data, accumulate into a line buffer; emit each complete line. On
        idle, check the buffer against PROMPT_PATTERNS; on match, invoke
        prompt_provider and write the response (or SIGINT on empty).
    """
    master_fd, slave_fd = pty.openpty()
    try:
        proc = subprocess.Popen(
            full,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            preexec_fn=os.setsid,
            close_fds=True,
            env=env,
        )
    except Exception:
        # Popen failed (e.g. argv[0] missing). Release both PTY fds.
        os.close(slave_fd)
        os.close(master_fd)
        raise
    # Parent doesn't need the slave fd; the child has its own dup.
    os.close(slave_fd)

    captured: list[str] = []
    buffer = ""
    cancelled = False
    decoder_errors = "replace"

    try:
        while True:
            try:
                r, _, _ = select.select([master_fd], [], [], _PROMPT_IDLE_S)
            except (OSError, ValueError):
                break

            if master_fd in r:
                try:
                    chunk = os.read(master_fd, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                buffer += chunk.decode("utf-8", errors=decoder_errors)
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    cleaned = strip_ansi(line.rstrip("\r"))
                    captured.append(cleaned)
                    if cancel_event is not None and cancel_event.is_set():
                        if not cancelled:
                            bus.emit_log(phase, "(cancellation requested)")
                            cancelled = True
                    else:
                        bus.emit_log(phase, cleaned)
            else:
                # idle — partial-line buffer is the prompt candidate
                if cancel_event is not None and cancel_event.is_set() and not cancelled:
                    bus.emit_log(phase, "(cancellation requested)")
                    cancelled = True
                    _send_sigint(proc)
                    continue
                if not buffer:
                    continue
                cleaned_buf = strip_ansi(buffer.rstrip("\r"))
                kind = detect_prompt(cleaned_buf)
                if kind is None:
                    continue
                # Surface the prompt line itself in the log so the user sees
                # what they're answering, then call the provider (blocking).
                bus.emit_log(phase, cleaned_buf)
                try:
                    response = prompt_provider(cleaned_buf, kind)
                except Exception:  # noqa: BLE001 — provider must never crash the runner
                    log.exception("prompt_provider raised; treating as cancel")
                    response = ""
                buffer = ""
                if response == "":
                    bus.emit_log(phase, "(user cancelled at prompt — sending SIGINT)")
                    _send_sigint(proc)
                    continue
                payload = response if response.endswith("\n") else response + "\n"
                try:
                    os.write(master_fd, payload.encode("utf-8"))
                except OSError:
                    break

        # Flush any final partial-line buffer
        if buffer:
            cleaned = strip_ansi(buffer.rstrip("\r\n"))
            if cleaned:
                captured.append(cleaned)
                if not (cancel_event is not None and cancel_event.is_set()):
                    bus.emit_log(phase, cleaned)
    finally:
        try:
            os.close(master_fd)
        except OSError:
            pass

    code = proc.wait()
    log.info("exited %d (pty): %s", code, " ".join(full))
    return code, captured


def _send_sigint(proc: subprocess.Popen) -> None:
    """Best-effort SIGINT to the subprocess group; ignored if already gone."""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGINT)
    except (ProcessLookupError, PermissionError):
        pass


def run_capture(
    argv: list[str],
    *,
    strategy: SudoStrategy,
    input_text: str | None = None,
) -> tuple[int, str, str]:
    """Run a privileged command, capture stdout/stderr, return (code, out, err).

    `input_text` (v0.4.4) is fed to the process's stdin. Used by the
    Cache tab to write `/etc/conf.d/pacman-contrib` via the allowlisted
    `sudo tee` (tee reads the new file content from stdin). When None
    (every pre-v0.4.4 caller), behavior is unchanged.
    """
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
            input=input_text,
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
