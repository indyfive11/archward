"""Tests for archward.ui.persistent_state — the QSettings-backed
remember-last-used-profile toggle and last-used-path persistence.

Uses a per-test temp QSettings file so the user's real archward.conf
is never touched. The QApplication is created once per module and its
org/app names point at a sentinel so the production QSettings file is
never read either.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from archward.ui import persistent_state


@pytest.fixture(scope="module")
def qapp():
    """Single QApplication for the module. Qt forbids more than one."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    # Sentinel names so the production archward.conf is never read.
    app.setOrganizationName("archward-test")
    app.setApplicationName("archward-test")
    yield app


@pytest.fixture(autouse=True)
def isolated_qsettings(qapp, tmp_path, monkeypatch):
    """Redirect QSettings storage to tmp_path so tests don't share state."""
    QSettings.setPath(
        QSettings.Format.IniFormat,
        QSettings.Scope.UserScope,
        str(tmp_path),
    )
    # Default constructor uses IniFormat when the app's QSettings::setDefaultFormat
    # is set; force it so persistent_state.QSettings() honors our path override.
    monkeypatch.setattr(
        persistent_state, "_settings",
        lambda: QSettings(QSettings.Format.IniFormat, QSettings.Scope.UserScope,
                          qapp.organizationName(), qapp.applicationName()),
    )
    yield


class TestRememberFlag:
    def test_default_off(self):
        assert persistent_state.get_remember_last_profile() is False

    def test_set_persists(self):
        persistent_state.set_remember_last_profile(True)
        assert persistent_state.get_remember_last_profile() is True

    def test_round_trip(self):
        persistent_state.set_remember_last_profile(True)
        persistent_state.set_remember_last_profile(False)
        assert persistent_state.get_remember_last_profile() is False


class TestLastUsedPath:
    def test_returns_none_when_remember_off(self, tmp_path):
        # Even if a path is stored, remember=False yields None.
        persistent_state.set_last_used_profile_path(tmp_path / "foo.toml")
        persistent_state.set_remember_last_profile(False)
        assert persistent_state.get_last_used_profile_path() is None

    def test_returns_path_when_remember_on_and_file_exists(self, tmp_path):
        p = tmp_path / "lab.toml"
        p.write_text("schema_version = 1\n")
        persistent_state.set_remember_last_profile(True)
        persistent_state.set_last_used_profile_path(p)
        assert persistent_state.get_last_used_profile_path() == p

    def test_returns_none_when_file_deleted(self, tmp_path):
        p = tmp_path / "ghost.toml"
        p.write_text("")
        persistent_state.set_remember_last_profile(True)
        persistent_state.set_last_used_profile_path(p)
        p.unlink()
        assert persistent_state.get_last_used_profile_path() is None

    def test_none_path_records_default(self, tmp_path):
        """Calling set_last_used_profile_path(None) means 'default config' —
        stored as empty string so the next read returns None gracefully."""
        persistent_state.set_remember_last_profile(True)
        persistent_state.set_last_used_profile_path(None)
        assert persistent_state.get_last_used_profile_path() is None

    def test_clear_drops_the_key(self, tmp_path):
        p = tmp_path / "x.toml"
        p.write_text("")
        persistent_state.set_remember_last_profile(True)
        persistent_state.set_last_used_profile_path(p)
        persistent_state.clear_last_used_profile_path()
        assert persistent_state.get_last_used_profile_path() is None
