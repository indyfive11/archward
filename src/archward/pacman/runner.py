"""Streaming pacman runner.

Implements the audit's subprocess buffering recipe (A4):
- bufsize=1, text=True for line-buffered stdout
- stderr merged into stdout so a single stream represents progress
- C locale (LANG/LC_ALL/LANGUAGE), --noprogressbar --color=never to keep output parse-stable
- ANSI escapes stripped via logging_setup.strip_ansi before emitting
"""

from __future__ import annotations

import fcntl
import logging
import os
import pty
import select
import signal
import subprocess
import termios
import threading
import time
from typing import Callable

from archward.events import EventBus
from archward.locale_env import c_locale_env
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
    returned empty string signals cancellation — ETX (Ctrl-C) is written to
    the PTY so the line discipline delivers SIGINT to the child's foreground
    process group (works even for root-owned sudo children), which pacman
    handles cleanly between transactions (no DB damage).
    """
    full = [*strategy.argv_prefix(), *argv] if use_sudo else list(argv)
    env = c_locale_env(strategy.env())

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
        stdin=subprocess.DEVNULL,
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
        session (preexec_fn=os.setsid) with the PTY as controlling terminal;
        user cancel writes ETX to the master fd so the line discipline
        raises SIGINT in the child's group (see _CancelLadder).
      - Reader loop: select on master_fd with _PROMPT_IDLE_S timeout. On
        data, accumulate into a line buffer; emit each complete line. On
        idle, check the buffer against PROMPT_PATTERNS; on match, invoke
        prompt_provider and write the response (or SIGINT on empty).
    """
    master_fd, slave_fd = pty.openpty()

    def _child_setup() -> None:  # pragma: no cover — runs in the forked child
        os.setsid()
        # Make the PTY the child's controlling terminal so the line
        # discipline delivers SIGINT to its (foreground) process group when
        # ETX arrives on the master fd. A setsid'd child has no controlling
        # terminal by default, which would leave Ctrl-C inert. fd 0 is the
        # slave — subprocess dup2s the std fds before calling preexec_fn.
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)

    try:
        proc = subprocess.Popen(
            full,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            preexec_fn=_child_setup,
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

    ladder = _CancelLadder(proc, master_fd, bus, phase)

    try:
        while True:
            try:
                r, _, _ = select.select([master_fd], [], [], _PROMPT_IDLE_S)
            except (OSError, ValueError):
                break

            # External cancel (GUI Cancel button / window close). Checked on
            # every tick — including while the child streams output — so the
            # ladder starts promptly and keeps escalating on a stuck child.
            if cancel_event is not None and cancel_event.is_set():
                if not cancelled:
                    bus.emit_log(phase, "(cancellation requested)")
                    cancelled = True
                ladder.escalate()

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
                    if not cancelled:
                        bus.emit_log(phase, cleaned)
            else:
                # idle — partial-line buffer is the prompt candidate
                if cancelled:
                    # Already interrupting; keep escalating, never re-prompt.
                    ladder.escalate()
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
                if response == "":
                    # The buffer is deliberately NOT cleared: if the interrupt
                    # doesn't take the child down, the pending prompt text must
                    # stay visible to the final flush / post-mortem capture.
                    bus.emit_log(phase, "(user cancelled at prompt — interrupting subprocess)")
                    cancelled = True
                    ladder.escalate()
                    continue
                buffer = ""
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
                if not cancelled:
                    bus.emit_log(phase, cleaned)
    finally:
        try:
            os.close(master_fd)
        except OSError:
            pass

    code = proc.wait()
    log.info("exited %d (pty): %s", code, " ".join(full))
    return code, captured


# After the line-discipline interrupt, how long to wait for the child to exit
# before escalating to SIGTERM on its process group.
_CANCEL_TERM_GRACE_S = 10.0


class _CancelLadder:
    """Escalating cancel for the PTY child. `escalate()` is called on every
    reader-loop tick once cancellation is requested; each stage fires once.

    Stage 1 — write ETX (0x03) to the master fd. The PTY line discipline
    delivers SIGINT to the child's foreground process group. Unlike
    os.killpg, this works when the child runs as root (sudo pacman): the
    kernel raises the signal, so there is no EPERM to swallow. pacman
    handles SIGINT cleanly between transactions.

    Stage 2 — if the child is still alive _CANCEL_TERM_GRACE_S later,
    SIGTERM its process group. Effective for unprivileged helpers
    (yay/paru); impossible for a root-owned group (EPERM), in which case
    we keep waiting.

    There is deliberately no SIGKILL stage: killing pacman mid-transaction
    corrupts the package database. Worst case the child is left to finish.
    """

    def __init__(
        self, proc: subprocess.Popen, master_fd: int, bus: EventBus, phase: str
    ) -> None:
        self._proc = proc
        self._master_fd = master_fd
        self._bus = bus
        self._phase = phase
        self._stage = 0
        self._interrupted_at = 0.0

    def escalate(self) -> None:
        if self._stage == 0:
            self._stage = 1
            self._interrupted_at = time.monotonic()
            self._bus.emit_log(
                self._phase,
                "(sending Ctrl-C — the subprocess aborts or finishes its current transaction)",
            )
            try:
                os.write(self._master_fd, b"\x03")
            except OSError:
                pass
        elif (
            self._stage == 1
            and time.monotonic() - self._interrupted_at >= _CANCEL_TERM_GRACE_S
        ):
            self._stage = 2
            self._bus.emit_log(
                self._phase,
                f"(still running {int(_CANCEL_TERM_GRACE_S)}s after Ctrl-C — "
                "sending SIGTERM to the process group)",
            )
            try:
                os.killpg(os.getpgid(self._proc.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                # Root-owned group (sudo pacman) — cannot signal it; keep waiting.
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
    env = c_locale_env(strategy.env())
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
