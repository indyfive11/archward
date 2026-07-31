"""Tests for archward.privilege.askpass_qt — bundled Qt askpass entry point."""

from __future__ import annotations

import pytest

from archward.privilege import askpass_qt


@pytest.fixture
def graphical_env(monkeypatch):
    monkeypatch.setenv("DISPLAY", ":0")


def test_main_no_display_exits_nonzero(monkeypatch, capsys) -> None:
    """Headless invocation fails fast without touching Qt."""
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(
        askpass_qt, "_ask", lambda prompt: pytest.fail("Qt must not be reached headless")
    )
    assert askpass_qt.main(["prompt"]) == 1
    assert "no graphical display" in capsys.readouterr().err


def test_main_prints_password_on_accept(monkeypatch, graphical_env, capsys) -> None:
    monkeypatch.setattr(askpass_qt, "_ask", lambda prompt: "hunter2")
    assert askpass_qt.main(["[sudo] password for rob:"]) == 0
    assert capsys.readouterr().out == "hunter2\n"


def test_main_cancel_exits_nonzero_and_prints_nothing(monkeypatch, graphical_env, capsys) -> None:
    monkeypatch.setattr(askpass_qt, "_ask", lambda prompt: None)
    assert askpass_qt.main(["prompt"]) == 1
    assert capsys.readouterr().out == ""


def test_main_passes_sudo_prompt_through(monkeypatch, graphical_env) -> None:
    seen: list[str] = []

    def fake_ask(prompt: str) -> str:
        seen.append(prompt)
        return "pw"

    monkeypatch.setattr(askpass_qt, "_ask", fake_ask)
    askpass_qt.main(["[sudo] password for rob:"])
    assert seen == ["[sudo] password for rob:"]


def test_main_default_prompt_when_no_args(monkeypatch, graphical_env) -> None:
    seen: list[str] = []

    def fake_ask(prompt: str) -> str:
        seen.append(prompt)
        return "pw"

    monkeypatch.setattr(askpass_qt, "_ask", fake_ask)
    askpass_qt.main([])
    assert seen == [askpass_qt._DEFAULT_PROMPT]


def test_main_pyside_missing_exits_nonzero(monkeypatch, graphical_env, capsys) -> None:
    def raiser(prompt: str) -> str:
        raise ImportError("No module named 'PySide6'")

    monkeypatch.setattr(askpass_qt, "_ask", raiser)
    assert askpass_qt.main(["prompt"]) == 1
    assert "PySide6 not available" in capsys.readouterr().err
