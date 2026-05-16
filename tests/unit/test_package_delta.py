"""Tests for _package_delta helper (v0.4.7)."""

from __future__ import annotations

from archward.ui.dialogs.snapshot_browser import _package_delta

_PRE = "foo 1.0\nbar 2.0\nbaz 3.0\n"
_POST = "foo 1.0\nbar 2.1\nqux 4.0\n"  # bar upgraded, baz removed, qux added


def test_package_added() -> None:
    lines = _package_delta(_PRE, _POST)
    assert any(l.startswith("+ qux") for l in lines)


def test_package_removed() -> None:
    lines = _package_delta(_PRE, _POST)
    assert any(l.startswith("- baz") for l in lines)


def test_package_upgraded() -> None:
    lines = _package_delta(_PRE, _POST)
    assert any("bar" in l and "2.0" in l and "2.1" in l for l in lines)


def test_unchanged_packages_omitted() -> None:
    lines = _package_delta(_PRE, _POST)
    assert not any("foo" in l for l in lines)


def test_empty_inputs_return_empty() -> None:
    assert _package_delta("", "") == []


def test_identical_inputs_return_empty() -> None:
    assert _package_delta(_PRE, _PRE) == []
