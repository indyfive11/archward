"""Tests for the PTY-backed code path in pacman.runner.run_streaming.

Drives the runner against a small `bash -c 'read -p ...'` fixture so the
PTY + prompt detection + stdin write-back can be exercised end-to-end
without invoking pacman.

Linux-only — `pty.openpty()` exists on macOS but the surrounding
behavior (process groups, signals) is what archward actually targets.
"""

from __future__ import annotations

import shutil
import sys
import threading

import pytest

from archward.events import EventBus
from archward.pacman import prompts
from archward.pacman import runner as runner_mod
from archward.pacman.runner import run_streaming


pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or shutil.which("bash") is None,
    reason="PTY tests require Linux + bash",
)


class _NoopSudoStrategy:
    """Stub satisfying the SudoStrategy Protocol without elevating."""

    def env(self) -> dict[str, str]:
        import os
        return os.environ.copy()

    def argv_prefix(self) -> list[str]:
        return []


def _bus_with_capture() -> tuple[EventBus, list[str]]:
    bus = EventBus()
    captured: list[str] = []
    bus.subscribe(lambda ev: captured.append(ev.message or ""))
    return bus, captured


def test_pty_path_answers_yes_no_prompt() -> None:
    """A [Y/n] prompt is detected; the provider's response is written back
    to the subprocess and shows up in its echoed output."""
    bus, log = _bus_with_capture()
    provider_calls: list[tuple[str, prompts.PromptKind]] = []

    def provider(line: str, kind: prompts.PromptKind) -> str:
        provider_calls.append((line, kind))
        return "Y"

    # Bash reads a [Y/n] prompt and echoes the answer.
    argv = ["bash", "-c", 'read -p "[Y/n] " ans; echo "got=$ans"']

    code, captured = run_streaming(
        argv,
        strategy=_NoopSudoStrategy(),
        bus=bus,
        phase="pipeline",
        use_sudo=False,
        prompt_provider=provider,
    )

    assert code == 0
    assert len(provider_calls) == 1
    line, kind = provider_calls[0]
    assert kind is prompts.PromptKind.YES_NO
    # The subprocess echo confirms our "Y" reached its stdin.
    joined = "\n".join(captured)
    assert "got=Y" in joined


def test_pty_path_no_prompt_completes_cleanly() -> None:
    """A subprocess that exits without ever issuing a prompt still works."""
    bus, _ = _bus_with_capture()

    def provider(line: str, kind: prompts.PromptKind) -> str:
        raise AssertionError("provider should not be called when no prompt fires")

    argv = ["bash", "-c", 'echo "hello"; echo "world"']

    code, captured = run_streaming(
        argv,
        strategy=_NoopSudoStrategy(),
        bus=bus,
        phase="pipeline",
        use_sudo=False,
        prompt_provider=provider,
    )

    assert code == 0
    joined = "\n".join(captured)
    assert "hello" in joined
    assert "world" in joined


def test_pty_path_cancel_via_empty_response_sigints_subprocess() -> None:
    """When the provider returns '', SIGINT is sent to the subprocess group.

    Bash with `trap 'exit 130' INT` exits 130 on SIGINT — confirming we
    actually killed it rather than waiting forever.
    """
    bus, _ = _bus_with_capture()

    def cancel_provider(line: str, kind: prompts.PromptKind) -> str:
        return ""  # signals cancel

    argv = [
        "bash",
        "-c",
        "trap 'exit 130' INT; read -p '[Y/n] ' ans; echo done=$ans",
    ]

    code, _captured = run_streaming(
        argv,
        strategy=_NoopSudoStrategy(),
        bus=bus,
        phase="pipeline",
        use_sudo=False,
        prompt_provider=cancel_provider,
    )

    # bash's `trap '... INT'` exit is 130. We just need a non-zero code that
    # proves the subprocess didn't reach the post-read echo.
    assert code != 0


def test_pty_cancel_event_interrupts_via_tty() -> None:
    """Setting cancel_event mid-run delivers SIGINT through the PTY line
    discipline (Ctrl-C write, not killpg — the mechanism that also works
    for root-owned sudo children)."""
    bus, _ = _bus_with_capture()
    cancel = threading.Event()

    def provider(line: str, kind: prompts.PromptKind) -> str:
        raise AssertionError("no prompt expected")

    # Cancel as soon as the child's first output line crosses the bus.
    def watcher(ev) -> None:
        if ev.message and "started" in ev.message:
            cancel.set()

    bus.subscribe(watcher)

    argv = ["bash", "-c", "trap 'exit 130' INT; echo started; sleep 30; echo never"]
    code, captured = run_streaming(
        argv,
        strategy=_NoopSudoStrategy(),
        bus=bus,
        phase="pipeline",
        use_sudo=False,
        cancel_event=cancel,
        prompt_provider=provider,
    )

    assert code != 0
    assert "never" not in "\n".join(captured)


def test_pty_cancel_escalates_to_sigterm_when_sigint_ignored(monkeypatch) -> None:
    """A child that ignores SIGINT is SIGTERMed after the grace period."""
    monkeypatch.setattr(runner_mod, "_CANCEL_TERM_GRACE_S", 0.5)
    bus, _ = _bus_with_capture()
    cancel = threading.Event()

    def provider(line: str, kind: prompts.PromptKind) -> str:
        raise AssertionError("no prompt expected")

    def watcher(ev) -> None:
        if ev.message and "started" in ev.message:
            cancel.set()

    bus.subscribe(watcher)

    # trap '' INT — SIGINT ignored (inherited by sleep); only SIGTERM works.
    argv = ["bash", "-c", "trap '' INT; echo started; sleep 30; echo never"]
    code, captured = run_streaming(
        argv,
        strategy=_NoopSudoStrategy(),
        bus=bus,
        phase="pipeline",
        use_sudo=False,
        cancel_event=cancel,
        prompt_provider=provider,
    )

    assert code != 0
    assert "never" not in "\n".join(captured)


def test_pty_prompt_cancel_keeps_prompt_text_in_capture() -> None:
    """Cancelling at a prompt must not clear the pending buffer — the prompt
    text stays available to the final flush / post-mortem capture."""
    bus, _ = _bus_with_capture()

    def cancel_provider(line: str, kind: prompts.PromptKind) -> str:
        return ""

    argv = [
        "bash",
        "-c",
        "trap 'exit 130' INT; read -p '[Y/n] ' ans; echo done=$ans",
    ]
    code, captured = run_streaming(
        argv,
        strategy=_NoopSudoStrategy(),
        bus=bus,
        phase="pipeline",
        use_sudo=False,
        prompt_provider=cancel_provider,
    )

    assert code != 0
    assert "[Y/n]" in "\n".join(captured)


def test_pipe_path_backward_compatibility_no_prompt_provider() -> None:
    """With prompt_provider=None the runner uses the legacy pipe path —
    same shape as today's non-interactive runs."""
    bus, log = _bus_with_capture()

    argv = ["bash", "-c", 'echo "line one"; echo "line two"']

    code, captured = run_streaming(
        argv,
        strategy=_NoopSudoStrategy(),
        bus=bus,
        phase="pipeline",
        use_sudo=False,
        prompt_provider=None,
    )

    assert code == 0
    joined = "\n".join(captured)
    assert "line one" in joined
    assert "line two" in joined


def test_pty_fake_prompt_mid_stream_held_not_pre_injected() -> None:
    """Hold-and-settle (v0.5): prompt-shaped output followed by MORE output
    must not have the answer written mid-stream (pre-injection class). The
    held answer is delivered once the real prompt is pending / the stream
    settles — the run completes, exactly one answer reaches the child."""
    import time

    bus, log = _bus_with_capture()
    provider_calls: list[str] = []

    def provider(line: str, kind: prompts.PromptKind) -> str:
        provider_calls.append(line)
        if len(provider_calls) == 1:
            # Slow human on the FAKE prompt; the child prints more output in
            # this window, which must force the answer to be held.
            time.sleep(0.8)
        return "Y"

    argv = [
        "bash", "-c",
        # Fake prompt (never read), then more output, then the real prompt.
        'printf "fake danger [Y/n] "; sleep 0.5; echo "more output"; '
        'read -p "real prompt [Y/n] " ans; echo "got=$ans"',
    ]

    code, captured = run_streaming(
        argv,
        strategy=_NoopSudoStrategy(),
        bus=bus,
        phase="pipeline",
        use_sudo=False,
        prompt_provider=provider,
    )

    assert code == 0
    joined = "\n".join(captured)
    assert "got=Y" in joined
    # The interleave was surfaced honestly and the answer was held, not
    # written mid-stream.
    assert any("holding the response" in line for line in log)


def test_pty_real_prompt_with_interleaved_output_does_not_stall() -> None:
    """Regression (v0.5 bug hunt): output arriving AFTER a real prompt while
    the user is answering used to complete the prompt's line, empty the
    buffer, and stall the runner forever with the answer discarded. The
    held answer must be delivered after the stream settles."""
    import time

    bus, log = _bus_with_capture()
    provider_calls: list[str] = []

    def provider(line: str, kind: prompts.PromptKind) -> str:
        provider_calls.append(line)
        time.sleep(0.8)  # slow human; background noise lands in this window
        return "Y"

    argv = [
        "bash", "-c",
        '( sleep 0.4; echo "background noise" ) & '
        'read -p "Proceed? [Y/n] " ans; echo "got=$ans"; wait',
    ]

    code, captured = run_streaming(
        argv,
        strategy=_NoopSudoStrategy(),
        bus=bus,
        phase="pipeline",
        use_sudo=False,
        prompt_provider=provider,
    )

    assert code == 0
    joined = "\n".join(captured)
    assert "got=Y" in joined
    assert len(provider_calls) == 1  # answered once; never re-asked
    assert any("answer sent after output interruption" in line for line in log)
