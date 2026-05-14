"""Theme detection — YIQ luminance threshold + palette selection."""

from __future__ import annotations

import pytest
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from archward.ui.theme import _DARK, _LIGHT, is_dark_theme, status_palette


@pytest.fixture(scope="module")
def qapp():
    """Single QApplication for all theme tests — Qt forbids creating two."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def _set_window_color(app: QApplication, color: QColor) -> None:
    palette = app.palette()
    palette.setColor(QPalette.ColorRole.Window, color)
    app.setPalette(palette)


def test_dark_window_color_detected(qapp) -> None:
    _set_window_color(qapp, QColor(35, 38, 41))  # Breeze Dark window approx
    assert is_dark_theme() is True


def test_light_window_color_detected(qapp) -> None:
    _set_window_color(qapp, QColor(252, 252, 252))  # Breeze light window approx
    assert is_dark_theme() is False


def test_threshold_boundary_just_below(qapp) -> None:
    """Luminance 127 — just below the 128 threshold — should be dark."""
    _set_window_color(qapp, QColor(127, 127, 127))
    assert is_dark_theme() is True


def test_threshold_boundary_just_above(qapp) -> None:
    """Luminance 128 — at the threshold — should be light."""
    _set_window_color(qapp, QColor(128, 128, 128))
    assert is_dark_theme() is False


def test_palette_selection_dark(qapp) -> None:
    _set_window_color(qapp, QColor(35, 38, 41))
    palette = status_palette()
    # Spot-check a known-different value: dark PASS fg is brighter (>200 in G)
    # than light PASS fg (~180).
    assert palette is _DARK
    assert palette.pass_fg.green() > 200


def test_palette_selection_light(qapp) -> None:
    _set_window_color(qapp, QColor(252, 252, 252))
    palette = status_palette()
    assert palette is _LIGHT


def test_dark_and_light_palettes_have_same_attribute_set() -> None:
    """Light and dark palettes must declare the exact same fields."""
    light_fields = set(_LIGHT.__dataclass_fields__.keys())
    dark_fields = set(_DARK.__dataclass_fields__.keys())
    assert light_fields == dark_fields


def test_no_qapplication_returns_light(monkeypatch) -> None:
    """If QApplication isn't constructed yet, fall back to light theme rather than crash."""
    from archward.ui import theme

    monkeypatch.setattr(QApplication, "instance", staticmethod(lambda: None))
    assert theme.is_dark_theme() is False
