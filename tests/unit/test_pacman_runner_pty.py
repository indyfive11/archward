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
        phase="test",
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
        phase="test",
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
        phase="test",
        use_sudo=False,
        prompt_provider=cancel_provider,
    )

    # bash's `trap '... INT'` exit is 130. We just need a non-zero code that
    # proves the subprocess didn't reach the post-read echo.
    assert code != 0


def test_pipe_path_backward_compatibility_no_prompt_provider() -> None:
    """With prompt_provider=None the runner uses the legacy pipe path —
    same shape as today's non-interactive runs."""
    bus, log = _bus_with_capture()

    argv = ["bash", "-c", 'echo "line one"; echo "line two"']

    code, captured = run_streaming(
        argv,
        strategy=_NoopSudoStrategy(),
        bus=bus,
        phase="test",
        use_sudo=False,
        prompt_provider=None,
    )

    assert code == 0
    joined = "\n".join(captured)
    assert "line one" in joined
    assert "line two" in joined
