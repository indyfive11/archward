"""v0.4.17 run-state machine (IDLE/WARMUP/RUNNING) + lifecycle teardown.

Covers: double-launch guard, cancel-during-warmup, heartbeat stop on the
pipeline-raised (None-result) path, and Cancel-button enablement.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from PySide6.QtWidgets import QApplication

from archward.pipeline.pipeline import Mode


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture
def main_window(qapp, monkeypatch):
    from archward.config.defaults import default_config

    fake_strategy = MagicMock()
    fake_strategy.warmup.return_value = True
    fake_strategy.env.return_value = {}
    fake_strategy.argv_prefix.return_value = ["sudo", "-A"]
    fake_strategy.askpass_path = None

    monkeypatch.setattr(
        "archward.ui.main_window.build_config", lambda *a, **k: default_config()
    )
    monkeypatch.setattr(
        "archward.ui.main_window.build_sudo_strategy", lambda *a, **k: fake_strategy
    )
    monkeypatch.setattr("archward.ui.main_window.setup_logging", lambda *a, **k: None)

    from archward.ui.main_window import MainWindow
    win = MainWindow()
    yield win
    win.close()


def test_starts_idle_with_cancel_disabled(main_window) -> None:
    from archward.ui.main_window import RunState

    assert main_window._run_state is RunState.IDLE
    assert main_window._cancel_btn.isEnabled() is False
    assert main_window._dry_btn.isEnabled() is True


def test_start_run_is_noop_when_not_idle(main_window) -> None:
    """The F5-in-warmup-gap double-launch race: a second _start_run while
    WARMUP/RUNNING must not spawn another warmup worker."""
    from archward.ui.main_window import RunState

    main_window._run_state = RunState.WARMUP
    before = main_window._warmup_worker
    main_window._start_run(Mode.DRY_RUN)
    assert main_window._warmup_worker is before
    main_window._run_state = RunState.IDLE


def test_cancel_during_warmup_drops_pending_launch(main_window, monkeypatch) -> None:
    from archward.ui.main_window import RunState

    launched: list[Mode] = []
    monkeypatch.setattr(main_window, "_launch_pipeline", launched.append)

    main_window._run_state = RunState.WARMUP
    main_window._pending_mode = Mode.INTERACTIVE
    main_window._cancel_btn.setEnabled(True)

    main_window._on_cancel_clicked()
    assert main_window._pending_mode is None

    # Warmup thread reports back → state returns to IDLE, nothing launches.
    main_window._on_warmup_done(True)
    assert launched == []
    assert main_window._run_state is RunState.IDLE
    assert main_window._dry_btn.isEnabled() is True
    assert main_window._cancel_btn.isEnabled() is False


def test_pipeline_raised_path_stops_heartbeat_and_idles(main_window) -> None:
    """_on_pipeline_done(None) used to return before heartbeat.stop() —
    leaving a 2 Hz repaint timer running forever."""
    from archward.ui.main_window import RunState

    main_window._run_state = RunState.RUNNING
    main_window._paint_heartbeat.start()

    main_window._on_pipeline_done(None)

    assert main_window._paint_heartbeat.isActive() is False
    assert main_window._run_state is RunState.IDLE
    assert main_window._cancel_btn.isEnabled() is False
    assert "failed" in main_window._status.currentMessage().lower()


def test_cancel_while_running_sets_event_and_cancels_prompts(main_window) -> None:
    from archward.ui.main_window import RunState

    worker = MagicMock()
    main_window.worker = worker
    main_window._run_state = RunState.RUNNING
    main_window._cancel_btn.setEnabled(True)

    prompter = MagicMock()
    update_prompter = MagicMock()
    pkgbuild_prompter = MagicMock()
    main_window.prompter = prompter
    main_window.update_prompter = update_prompter
    main_window.pkgbuild_prompter = pkgbuild_prompter

    main_window._on_cancel_clicked()

    worker.cancel_event.set.assert_called_once()
    prompter.cancel_pending.assert_called_once()
    update_prompter.cancel_pending.assert_called_once()
    pkgbuild_prompter.cancel_pending.assert_called_once()
    assert main_window._cancel_btn.isEnabled() is False
    assert "cancelling" in main_window._status.currentMessage().lower()

    # cleanup so win.close() doesn't see a "running" MagicMock worker
    main_window.worker = None
    main_window._run_state = RunState.IDLE
