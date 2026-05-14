"""Prompter.decide_high_risk — return (proceed, ignored_pkgs) for each impl."""

from __future__ import annotations

import io
from unittest.mock import patch

from archward.models.update import PendingUpdate, RiskLevel
from archward.pipeline.prompter import (
    AutoNoPrompter,
    AutoYesPrompter,
    CliPrompter,
)


def _high(name: str) -> PendingUpdate:
    return PendingUpdate(
        name=name,
        old_version="1.0",
        new_version="1.1",
        source="official",
        risk=RiskLevel.HIGH,
    )


def test_auto_yes_prompter_returns_proceed_empty_ignore() -> None:
    p = AutoYesPrompter()
    proceed, ignored = p.decide_high_risk([_high("linux")])
    assert proceed is True
    assert ignored == []


def test_auto_no_prompter_returns_decline_empty_ignore() -> None:
    p = AutoNoPrompter()
    proceed, ignored = p.decide_high_risk([_high("linux"), _high("glibc")])
    assert proceed is False
    assert ignored == []


def test_cli_prompter_proceed_via_y() -> None:
    with patch("builtins.input", return_value="y"):
        p = CliPrompter()
        proceed, ignored = p.decide_high_risk([_high("linux")])
    assert proceed is True
    assert ignored == []  # CLI doesn't support per-row deselect yet


def test_cli_prompter_proceed_via_yes() -> None:
    with patch("builtins.input", return_value="YES"):
        p = CliPrompter()
        proceed, _ = p.decide_high_risk([_high("linux")])
    assert proceed is True


def test_cli_prompter_decline_via_n() -> None:
    with patch("builtins.input", return_value="n"):
        p = CliPrompter()
        proceed, _ = p.decide_high_risk([_high("linux")])
    assert proceed is False


def test_cli_prompter_decline_via_empty() -> None:
    """Empty input = default No, per the [y/N] prompt convention."""
    with patch("builtins.input", return_value=""):
        p = CliPrompter()
        proceed, _ = p.decide_high_risk([_high("linux")])
    assert proceed is False


def test_cli_prompter_decline_on_eof() -> None:
    """Piping nothing into stdin (EOFError) is treated as decline."""
    with patch("builtins.input", side_effect=EOFError()):
        p = CliPrompter()
        proceed, _ = p.decide_high_risk([_high("linux")])
    assert proceed is False
