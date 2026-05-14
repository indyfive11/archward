"""Helper discovery — first available in preference wins."""

from __future__ import annotations

import shutil

from archward.aur.helper import discover


def test_discover_returns_yay_when_available(monkeypatch) -> None:
    def fake_which(name: str) -> str | None:
        return f"/usr/bin/{name}" if name == "yay" else None

    monkeypatch.setattr(shutil, "which", fake_which)
    helper = discover(("yay", "paru", "aurutils"))
    assert helper is not None
    assert helper.name == "yay"


def test_discover_falls_back_to_paru(monkeypatch) -> None:
    def fake_which(name: str) -> str | None:
        return f"/usr/bin/{name}" if name == "paru" else None

    monkeypatch.setattr(shutil, "which", fake_which)
    helper = discover(("yay", "paru", "aurutils"))
    assert helper is not None
    assert helper.name == "paru"


def test_discover_returns_none_when_no_helper_available(monkeypatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _: None)
    assert discover(("yay", "paru", "aurutils")) is None


def test_discover_respects_preference_order(monkeypatch) -> None:
    """If yay and paru are both installed but paru is listed first, paru wins."""

    def fake_which(name: str) -> str | None:
        if name in ("yay", "paru"):
            return f"/usr/bin/{name}"
        return None

    monkeypatch.setattr(shutil, "which", fake_which)
    helper = discover(("paru", "yay"))
    assert helper is not None
    assert helper.name == "paru"


def test_discover_ignores_unknown_names(monkeypatch) -> None:
    """Unknown names in preference are skipped — don't error."""
    monkeypatch.setattr(shutil, "which", lambda _: None)
    assert discover(("nothere", "alsomissing")) is None
