"""pacman vercmp wrapper — handles epoch, pkgrel, alpha/beta tokens."""

from __future__ import annotations

import shutil

import pytest

from archward.pacman.query import vercmp


pytestmark = pytest.mark.skipif(
    shutil.which("vercmp") is None,
    reason="vercmp binary not on PATH (ships with pacman; skip on non-Arch CI)",
)


@pytest.mark.parametrize(
    "a, b, expected",
    [
        ("7.0.5.arch1-1", "7.0.6.arch1-1", -1),   # current vs newer
        ("7.0.6.arch1-1", "7.0.5.arch1-1", 1),    # current vs older
        ("7.0.5.arch1-1", "7.0.5.arch1-1", 0),    # equal
        ("7.0.5.arch1-1", "7.0.5.arch1-2", -1),   # pkgrel bump
        ("1:7.0.5-1", "7.0.6-1", 1),              # epoch trumps version
        ("2.40-1", "2.41-1", -1),                 # plain dotted versions
    ],
)
def test_vercmp(a: str, b: str, expected: int) -> None:
    assert vercmp(a, b) == expected
