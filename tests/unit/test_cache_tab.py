"""Tests for v0.4.4 F1b — the Preferences Cache tab.

Bare-QApplication fixture (no pixels). Detection is stubbed via the
cache_policy module so the tab renders against a known verdict without
touching the real system, and the apply path is verified to (a) require
confirmation and (b) route through run_capture with the right argv +
input_text.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from archward.config.defaults import default_config
from archward.system import cache_policy as cp


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def _balanced_policy() -> cp.CachePolicy:
    return cp.CachePolicy(
        timer_state="enabled",
        paccache_args="-rk3",
        effective_keep=3,
        clean_method=("KeepInstalled",),
        cleaning_hooks=(),
        cache_size_bytes=4 * 1024 * 1024 * 1024,
        cache_file_count=312,
        safety=cp.RollbackSafety.BALANCED,
        explanation="balanced explanation",
    )


def _dangerous_policy() -> cp.CachePolicy:
    return cp.CachePolicy(
        timer_state="disabled",
        paccache_args="",
        effective_keep=3,
        clean_method=("KeepInstalled",),
        cleaning_hooks=(Path("/etc/pacman.d/hooks/clean.hook"),),
        cache_size_bytes=0,
        cache_file_count=0,
        safety=cp.RollbackSafety.DANGEROUS,
        explanation="a cleaning hook will eat your rollback",
    )


def test_cache_tab_renders_balanced(qapp, monkeypatch) -> None:
    monkeypatch.setattr(cp, "detect_cache_policy", _balanced_policy)
    from archward.ui.dialogs.preferences import _CacheTab
    tab = _CacheTab(default_config())
    # The verdict banner text should reflect the stubbed verdict.
    found = tab.findChildren(type(tab))  # smoke; just ensure construction
    assert tab is not None


def test_cache_tab_renders_dangerous_hook_warning(qapp, monkeypatch) -> None:
    monkeypatch.setattr(cp, "detect_cache_policy", _dangerous_policy)
    from PySide6.QtWidgets import QLabel
    from archward.ui.dialogs.preferences import _CacheTab
    tab = _CacheTab(default_config())
    texts = " ".join(
        w.text() for w in tab.findChildren(QLabel) if w.text()
    )
    assert "DANGEROUS" in texts
    assert "clean.hook" in texts


def test_apply_preset_requires_confirmation_and_runs_commands(qapp, monkeypatch) -> None:
    """Confirm dialog → Yes → tee (with input_text) + systemctl enable."""
    monkeypatch.setattr(cp, "detect_cache_policy", _balanced_policy)
    from archward.ui.dialogs import preferences as prefs
    from archward.ui.dialogs.preferences import _CacheTab

    tab = _CacheTab(default_config())

    # User clicks Yes on the confirm dialog.
    monkeypatch.setattr(
        prefs.QMessageBox, "question",
        lambda *a, **k: QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr(
        prefs.QMessageBox, "information", lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "archward.app.build_sudo_strategy", lambda *a, **k: MagicMock(),
    )

    calls: list[tuple] = []

    def fake_run_capture(argv, *, strategy, input_text=None):
        calls.append((tuple(argv), input_text))
        return 0, "", ""

    monkeypatch.setattr("archward.pacman.runner.run_capture", fake_run_capture)

    home = next(p for p in cp.CACHE_PRESETS if p.key == "home")
    tab._apply_preset(home)

    # First call: tee /etc/conf.d/pacman-contrib with the conf content on stdin.
    tee_call = next(c for c in calls if c[0][0] == "tee")
    assert tee_call[0] == ("tee", "/etc/conf.d/pacman-contrib")
    assert "PACCACHE_ARGS='-rk3'" in tee_call[1]
    # Second call: systemctl enable --now paccache.timer (home enables timer).
    assert any(
        c[0] == ("systemctl", "enable", "--now", "paccache.timer")
        for c in calls
    )


def test_apply_preset_aborts_on_no(qapp, monkeypatch) -> None:
    monkeypatch.setattr(cp, "detect_cache_policy", _balanced_policy)
    from archward.ui.dialogs import preferences as prefs
    from archward.ui.dialogs.preferences import _CacheTab

    tab = _CacheTab(default_config())
    monkeypatch.setattr(
        prefs.QMessageBox, "question",
        lambda *a, **k: QMessageBox.StandardButton.No,
    )

    def boom(*a, **k):
        raise AssertionError("run_capture must not run when user declines")

    monkeypatch.setattr("archward.pacman.runner.run_capture", boom)

    home = next(p for p in cp.CACHE_PRESETS if p.key == "home")
    tab._apply_preset(home)  # should return without raising


def test_mission_critical_disables_timer_via_apply(qapp, monkeypatch) -> None:
    monkeypatch.setattr(cp, "detect_cache_policy", _balanced_policy)
    from archward.ui.dialogs import preferences as prefs
    from archward.ui.dialogs.preferences import _CacheTab

    tab = _CacheTab(default_config())
    monkeypatch.setattr(prefs.QMessageBox, "question",
                        lambda *a, **k: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(prefs.QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr("archward.app.build_sudo_strategy", lambda *a, **k: MagicMock())

    calls: list[tuple] = []
    monkeypatch.setattr(
        "archward.pacman.runner.run_capture",
        lambda argv, *, strategy, input_text=None: (calls.append(tuple(argv)) or (0, "", "")),
    )

    mc = next(p for p in cp.CACHE_PRESETS if p.key == "mission-critical")
    tab._apply_preset(mc)
    assert ("systemctl", "disable", "--now", "paccache.timer") in calls
