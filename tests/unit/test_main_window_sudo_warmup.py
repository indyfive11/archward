"""Tests for v0.4.2 hotfix — sudo warmup at run start in the GUI.

Regression: pre-v0.4.2, MainWindow built the sudo strategy but never called
strategy.warmup() — only the CLI setup_app() path did. That meant the first
sudo call inside _gather_configs (cp / tar / chown for non-NOPASSWD targets
like /etc/pacman.conf, /etc/ssh/sshd_config.d, /etc/sudoers.d) was what
triggered the askpass dialog, mid-snapshot. Users who looked away after
clicking Run Update would miss the prompt, and if ksshaskpass intermittently
failed to parse the prompt, sudo retried per file → multiple password
prompts in one run.

The fix is a single call to `self._warmup_sudo_for_run()` at the top of
`_start_run`. Tests:

  1. The warmup method calls strategy.warmup() and returns its result.
  2. The warmup method handles strategy.warmup() raising (warmup must
     never crash the run).
  3. _start_run actually invokes _warmup_sudo_for_run before creating
     the worker.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

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
    """Build a MainWindow without doing any disk I/O or sudo calls."""
    # Patch config loading + sudo strategy creation BEFORE the import.
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
    # setup_logging touches disk; stub it.
    monkeypatch.setattr("archward.ui.main_window.setup_logging", lambda *a, **k: None)

    from archward.ui.main_window import MainWindow
    win = MainWindow()
    win._strategy_mock = fake_strategy  # type: ignore[attr-defined]
    yield win
    win.close()


def test_warmup_method_calls_strategy_warmup(main_window) -> None:
    """_warmup_sudo_for_run returns True when strategy.warmup() returns True."""
    main_window._strategy_mock.warmup.reset_mock()
    main_window._strategy_mock.warmup.return_value = True

    ok = main_window._warmup_sudo_for_run()

    assert ok is True
    main_window._strategy_mock.warmup.assert_called_once_with()


def test_warmup_method_handles_failure(main_window) -> None:
    """When strategy.warmup() returns False, _warmup_sudo_for_run returns False
    (and a non-fatal status message is shown)."""
    main_window._strategy_mock.warmup.reset_mock()
    main_window._strategy_mock.warmup.return_value = False

    ok = main_window._warmup_sudo_for_run()

    assert ok is False
    # The status bar message should mention the failure so the user can
    # interpret a subsequent mid-pipeline prompt.
    assert "askpass" in main_window._status.currentMessage().lower()


def test_warmup_method_swallows_exceptions(main_window) -> None:
    """A raising warmup must not crash _warmup_sudo_for_run."""
    main_window._strategy_mock.warmup.reset_mock()
    main_window._strategy_mock.warmup.side_effect = RuntimeError("simulated")

    ok = main_window._warmup_sudo_for_run()

    assert ok is False  # treat as failure, not crash


def test_start_run_invokes_warmup_before_worker(main_window, monkeypatch) -> None:
    """_start_run() must call _warmup_sudo_for_run before creating the worker.

    This is the actual regression guard for the v0.4.2 bug.
    """
    # Spy on the warmup call by wrapping the real method.
    call_order: list[str] = []

    original_warmup = main_window._warmup_sudo_for_run

    def spied_warmup(*args, **kwargs):
        call_order.append("warmup")
        return original_warmup(*args, **kwargs)

    monkeypatch.setattr(main_window, "_warmup_sudo_for_run", spied_warmup)

    # Stub the worker class so _start_run doesn't actually spawn a pipeline.
    fake_worker_class = MagicMock()
    fake_worker_instance = MagicMock()
    fake_worker_instance.isRunning.return_value = False
    fake_worker_class.return_value = fake_worker_instance

    def fake_worker_creation(*args, **kwargs):
        call_order.append("worker_created")
        return fake_worker_instance

    monkeypatch.setattr(
        "archward.ui.main_window.PipelineWorker", fake_worker_creation
    )

    main_window._start_run(Mode.DRY_RUN)

    # warmup MUST come before worker creation.
    assert call_order == ["warmup", "worker_created"], (
        f"warmup should run before PipelineWorker is constructed; got {call_order}"
    )
