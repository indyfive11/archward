"""Tests for archward.pacman.prompts — pacman/AUR interactive prompt regexes.

The detection runs on a partial-line buffer (no trailing newline). We feed
real captured prompt lines and assert the resulting PromptKind.
"""

from __future__ import annotations

import pytest

from archward.pacman.prompts import (
    PROMPT_PATTERNS,
    PromptKind,
    default_response,
    detect_prompt,
)


# Real prompt lines as pacman / yay / paru emit them — captured from non-noconfirm
# runs with --color=never. Each tuple is (line, expected_kind).
_YES_NO_PROMPTS = (
    ":: Proceed with installation? [Y/n] ",
    ":: Replace base/iptables with core/iptables-nft? [Y/n] ",
    ":: Import PGP key 4096R/abcdef0123456789, \"Foo Bar <foo@example.com>\"? [Y/n] ",
    "[Y/n] ",
    "[y/N] ",
)

_NUMERIC_PROMPTS = (
    ":: There are 3 providers available for jdk:",
    ":: There are 2 providers available for ttf-font:",
    ":: Enter a selection (default=1): ",
)

_FREE_PROMPTS = (
    ":: Enter a selection or packages to clean (eg: 1 2 3): ",
)

_NON_PROMPTS = (
    ":: Synchronizing package databases...",
    " core 142.1 KiB    1.2 MiB/s  00:00 [###############] 100%",
    "warning: foo-bar-1.2-3 is up to date -- reinstalling",
    "",
    "Total Installed Size: 1234.56 MiB",
    "loading packages...",
)


@pytest.mark.parametrize("line", _YES_NO_PROMPTS)
def test_yes_no_prompts_detected(line: str) -> None:
    assert detect_prompt(line) is PromptKind.YES_NO


@pytest.mark.parametrize("line", _NUMERIC_PROMPTS)
def test_numeric_prompts_detected(line: str) -> None:
    assert detect_prompt(line) is PromptKind.NUMERIC


@pytest.mark.parametrize("line", _FREE_PROMPTS)
def test_free_prompts_detected(line: str) -> None:
    assert detect_prompt(line) is PromptKind.FREE


@pytest.mark.parametrize("line", _NON_PROMPTS)
def test_non_prompts_ignored(line: str) -> None:
    assert detect_prompt(line) is None


def test_pattern_table_nonempty() -> None:
    """Guard against accidental table truncation in future refactors."""
    assert len(PROMPT_PATTERNS) >= 5


def test_default_response_yes_no() -> None:
    assert default_response(PromptKind.YES_NO) == "Y"


def test_default_response_numeric() -> None:
    assert default_response(PromptKind.NUMERIC) == "1"


def test_default_response_free() -> None:
    assert default_response(PromptKind.FREE) == ""
