"""Tests for _PacnewTab — the editable pacnew rules table in Preferences.

Uses a module-scoped QApplication fixture (Qt forbids more than one per
process). Tests instantiate _PacnewTab directly and drive load() / dump()
plus the internal _add_rule_row() / _remove_selected_rules() helpers; no
pixels are rendered.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from archward.config.defaults import default_config
from archward.models.config import PacnewConfig, PacnewRule
from archward.models.pacnew import PacnewRecommendation


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture
def tab(qapp):
    # Defer import until QApplication exists.
    from archward.ui.dialogs.preferences import _PacnewTab
    return _PacnewTab()


def test_load_round_trip_default_rules(tab):
    """load(default_config) → dump() preserves the 9 shipped rules verbatim."""
    cfg = default_config()
    tab.load(cfg)
    result = tab.dump()
    assert isinstance(result, PacnewConfig)
    assert result.default_strategy == cfg.pacnew.default_strategy
    assert result.rules == cfg.pacnew.rules


def test_add_rule_then_dump_includes_new_rule(tab):
    """A row added via _add_rule_row appears in dump() output."""
    tab.load(default_config())
    original_count = len(default_config().pacnew.rules)
    tab._add_rule_row(
        pattern="*/my-custom.conf",
        strategy="keep_ours",
        note="my override",
    )
    result = tab.dump()
    assert len(result.rules) == original_count + 1
    new_rule = result.rules[-1]
    assert new_rule.pattern == "*/my-custom.conf"
    assert new_rule.strategy == PacnewRecommendation.KEEP_OURS
    assert new_rule.note == "my override"


def test_blank_pattern_row_is_dropped(tab):
    """A row whose pattern is blank or whitespace is silently dropped on save."""
    tab.load(default_config())
    original_count = len(default_config().pacnew.rules)
    tab._add_rule_row(pattern="", strategy="take_new", note="should be dropped")
    tab._add_rule_row(pattern="   ", strategy="keep_ours", note="also dropped")
    result = tab.dump()
    assert len(result.rules) == original_count  # no blanks survived


def test_dump_with_empty_note_returns_none(tab):
    """A row with no Note becomes PacnewRule(note=None), not note=''."""
    tab.load(default_config())
    tab._add_rule_row(pattern="*.bak", strategy="take_new", note="")
    result = tab.dump()
    new_rule = next(r for r in result.rules if r.pattern == "*.bak")
    assert new_rule.note is None


def test_remove_defaults_yields_default_strategy_only(tab):
    """Empty rules tuple is allowed; default_strategy still round-trips."""
    tab.load(default_config())
    tab._rules.setRowCount(0)  # nuke all rules
    tab._default.setCurrentText("keep_ours")
    result = tab.dump()
    assert result.rules == ()
    assert result.default_strategy == PacnewRecommendation.KEEP_OURS


def test_restore_defaults_resets_rules_from_empty(tab, monkeypatch):
    """Calling _restore_defaults() with an empty table re-populates from defaults
    without prompting (the empty-list confirm is skipped)."""
    tab.load(default_config())
    tab._rules.setRowCount(0)
    # _restore_defaults skips its QMessageBox.question when the table is
    # already empty (no user state to lose). Confirm that path.
    tab._restore_defaults()
    result = tab.dump()
    assert result.rules == default_config().pacnew.rules
