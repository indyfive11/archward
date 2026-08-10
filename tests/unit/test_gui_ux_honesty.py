"""v0.4.18 "UX honesty" — GUI surface tests.

Covers:
- wizard Finish turns on remember-last-used so its answers persist (item 1)
- profile import warns on embedded [hooks] shell commands (item 7)
- profile import confirms before overwriting a same-name profile (item 5)
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture(autouse=True)
def _tmp_xdg(tmp_path, monkeypatch):
    """Point all config paths at the test tmp dir."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


# ── wizard persistence ────────────────────────────────────────────────────


def test_wizard_finish_persists_profile_choice(qapp, monkeypatch, tmp_path) -> None:
    import archward.ui.dialogs.welcome_wizard as ww

    written: list[Path] = []
    recorded: list = []
    monkeypatch.setattr(ww, "write_config", lambda cfg, path: written.append(path))
    monkeypatch.setattr(ww, "set_wizard_completed", lambda: recorded.append("completed"))
    monkeypatch.setattr(
        ww, "set_remember_last_profile", lambda v: recorded.append(("remember", v))
    )
    monkeypatch.setattr(
        ww, "set_last_used_profile_path", lambda p: recorded.append(("last_path", p))
    )

    wiz = ww.WelcomeWizard()
    wiz._page_profile._name_edit.setText("mybox")
    wiz._on_finish()

    assert len(written) == 1
    profile_path = written[0]
    assert profile_path.name == "mybox.toml"
    assert ("remember", True) in recorded
    assert ("last_path", profile_path) in recorded
    assert wiz.result_path == profile_path


# ── profile import: hooks warning ────────────────────────────────────────


def _profiles_tab(qapp):
    from archward.ui.dialogs.preferences import _ProfilesTab

    return _ProfilesTab(config_path=None)


def _patch_file_dialog(monkeypatch, src: Path) -> None:
    monkeypatch.setattr(
        "archward.ui.dialogs.preferences.QFileDialog.getOpenFileName",
        staticmethod(lambda *a, **k: (str(src), "")),
    )


def test_import_with_hooks_warns_and_no_declines(qapp, monkeypatch, tmp_path) -> None:
    src = tmp_path / "evil.toml"
    src.write_text('[hooks]\npre_update = ["echo pwned"]\n')

    warnings: list[str] = []

    def fake_warning(parent, title, text, *a, **k):
        warnings.append(text)
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(
        "archward.ui.dialogs.preferences.QMessageBox.warning",
        staticmethod(fake_warning),
    )
    _patch_file_dialog(monkeypatch, src)

    tab = _profiles_tab(qapp)
    tab._on_import()

    # The warning listed the actual command and the decline stopped the import.
    assert warnings and "echo pwned" in warnings[0]
    from archward.config import paths as config_paths
    assert config_paths.iter_profiles() == []


def test_import_without_hooks_does_not_warn(qapp, monkeypatch, tmp_path) -> None:
    src = tmp_path / "clean.toml"
    src.write_text("[general]\n")

    warnings: list[str] = []
    monkeypatch.setattr(
        "archward.ui.dialogs.preferences.QMessageBox.warning",
        staticmethod(lambda *a, **k: warnings.append(a) or QMessageBox.StandardButton.No),
    )
    monkeypatch.setattr(
        "archward.ui.dialogs.preferences.QInputDialog.getText",
        staticmethod(lambda *a, **k: ("clean", True)),
    )

    _patch_file_dialog(monkeypatch, src)
    tab = _profiles_tab(qapp)
    tab._on_import()

    assert warnings == []
    from archward.config import paths as config_paths
    assert config_paths.iter_profiles() == ["clean"]


# ── profile import: overwrite confirm ────────────────────────────────────


def _existing_profile(name: str, content: str) -> Path:
    from archward.config import paths as config_paths

    p = config_paths.profile_config_path(name)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return p


def _patch_name_prompt(monkeypatch, name: str) -> None:
    monkeypatch.setattr(
        "archward.ui.dialogs.preferences.QInputDialog.getText",
        staticmethod(lambda *a, **k: (name, True)),
    )


def test_import_same_name_asks_before_overwrite(qapp, monkeypatch, tmp_path) -> None:
    existing = _existing_profile("myprof", "[general]\nkeep_days = 111\n")
    src = tmp_path / "myprof.toml"
    src.write_text("[general]\nkeep_days = 222\n")

    questions: list[str] = []

    def fake_question(parent, title, text, *a, **k):
        questions.append(text)
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(
        "archward.ui.dialogs.preferences.QMessageBox.question",
        staticmethod(fake_question),
    )
    _patch_name_prompt(monkeypatch, "myprof")
    _patch_file_dialog(monkeypatch, src)

    tab = _profiles_tab(qapp)
    tab._on_import()

    # Declined → the existing profile is untouched.
    assert questions and "myprof" in questions[0]
    assert "111" in existing.read_text()


def test_import_same_name_overwrites_on_yes(qapp, monkeypatch, tmp_path) -> None:
    existing = _existing_profile("myprof", "[general]\nkeep_days = 111\n")
    src = tmp_path / "myprof.toml"
    src.write_text("[general]\nkeep_days = 222\n")

    monkeypatch.setattr(
        "archward.ui.dialogs.preferences.QMessageBox.question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes),
    )
    _patch_name_prompt(monkeypatch, "myprof")
    _patch_file_dialog(monkeypatch, src)

    tab = _profiles_tab(qapp)
    tab._on_import()

    assert "222" in existing.read_text()
