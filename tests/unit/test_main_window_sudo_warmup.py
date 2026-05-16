"""Tests for sudo warmup in the GUI main window.

v0.4.2 hotfix: strategy.warmup() was only called by the CLI path. In the
GUI, the first sudo call mid-snapshot was what triggered the askpass dialog.
Fix: call warmup() before starting the pipeline.

v0.4.5 F4b: warmup moved off the Qt main thread (WarmupWorker QThread) so the
askpass dialog doesn't freeze the event loop. _start_run now starts a
WarmupWorker; _on_warmup_done then calls _launch_pipeline.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QCoreApplication
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
    win._strategy_mock = fake_strategy  # type: ignore[attr-defined]
    yield win
    win.close()


# ── _warmup_sudo_for_run (legacy sync helper, still tested for direct callers) ─


def test_warmup_method_calls_strategy_warmup(main_window) -> None:
    """_warmup_sudo_for_run returns True when strategy.warmup() returns True."""
    main_window._strategy_mock.warmup.reset_mock()
    main_window._strategy_mock.warmup.return_value = True

    ok = main_window._warmup_sudo_for_run()

    assert ok is True
    main_window._strategy_mock.warmup.assert_called_once_with()


def test_warmup_method_handles_failure(main_window) -> None:
    """When strategy.warmup() returns False, _warmup_sudo_for_run returns False."""
    main_window._strategy_mock.warmup.reset_mock()
    main_window._strategy_mock.warmup.return_value = False

    ok = main_window._warmup_sudo_for_run()

    assert ok is False
    assert "askpass" in main_window._status.currentMessage().lower()


def test_warmup_method_swallows_exceptions(main_window) -> None:
    """A raising warmup must not crash _warmup_sudo_for_run."""
    main_window._strategy_mock.warmup.reset_mock()
    main_window._strategy_mock.warmup.side_effect = RuntimeError("simulated")

    ok = main_window._warmup_sudo_for_run()

    assert ok is False


# ── WarmupWorker ──────────────────────────────────────────────────────


def test_warmup_worker_emits_true_on_success(qapp, main_window) -> None:
    """WarmupWorker emits warmup_done(True) when strategy.warmup() returns True."""
    from archward.ui.main_window import WarmupWorker

    fake_strategy = MagicMock()
    fake_strategy.warmup.return_value = True

    results: list[bool] = []
    worker = WarmupWorker(fake_strategy)
    worker.warmup_done.connect(results.append)
    worker.start()
    worker.wait(2000)  # 2s timeout
    QCoreApplication.processEvents()

    assert results == [True]


def test_warmup_worker_emits_false_on_failure(qapp) -> None:
    """WarmupWorker emits warmup_done(False) when strategy.warmup() returns False."""
    from archward.ui.main_window import WarmupWorker

    fake_strategy = MagicMock()
    fake_strategy.warmup.return_value = False

    results: list[bool] = []
    worker = WarmupWorker(fake_strategy)
    worker.warmup_done.connect(results.append)
    worker.start()
    worker.wait(2000)
    QCoreApplication.processEvents()

    assert results == [False]


def test_warmup_worker_swallows_exception(qapp) -> None:
    """WarmupWorker emits warmup_done(False) when strategy.warmup() raises."""
    from archward.ui.main_window import WarmupWorker

    fake_strategy = MagicMock()
    fake_strategy.warmup.side_effect = RuntimeError("boom")

    results: list[bool] = []
    worker = WarmupWorker(fake_strategy)
    worker.warmup_done.connect(results.append)
    worker.start()
    worker.wait(2000)
    QCoreApplication.processEvents()

    assert results == [False]


# ── _start_run → WarmupWorker → _launch_pipeline integration ─────────


def test_start_run_starts_warmup_worker_before_pipeline(main_window, monkeypatch) -> None:
    """_start_run must create a WarmupWorker; _launch_pipeline creates PipelineWorker."""
    call_order: list[str] = []

    # Stub WarmupWorker so it synchronously calls _on_warmup_done(True).
    from archward.ui import main_window as mw_mod

    original_ww = mw_mod.WarmupWorker

    class FakeWarmupWorker:
        def __init__(self, strategy, parent=None):
            self._parent_win = parent
            self._callback = None
            self.warmup_done = self  # acts as signal object

        # Signal-like interface: worker.warmup_done.connect(cb)
        def connect(self, cb):
            self._callback = cb

        def isRunning(self):
            return False

        def start(self):
            call_order.append("warmup_started")
            # Synchronously fire the success callback.
            if self._callback is not None:
                self._callback(True)

        def wait(self, *a):
            pass

    monkeypatch.setattr(mw_mod, "WarmupWorker", FakeWarmupWorker)

    # Also stub _on_warmup_done to track pipeline creation order.
    original_launch = main_window._launch_pipeline

    def spied_launch(mode):
        call_order.append("pipeline_launched")
        # Don't actually start the pipeline in this test.

    monkeypatch.setattr(main_window, "_launch_pipeline", spied_launch)

    main_window._start_run(Mode.DRY_RUN)

    # warmup MUST start before pipeline is launched.
    assert call_order == ["warmup_started", "pipeline_launched"], (
        f"Expected warmup before pipeline; got {call_order}"
    )


def test_on_warmup_done_false_still_launches_pipeline(main_window, monkeypatch) -> None:
    """Warmup failure is non-fatal — pipeline still launches."""
    main_window._pending_mode = Mode.DRY_RUN
    launched: list[Mode] = []

    monkeypatch.setattr(main_window, "_launch_pipeline", launched.append)

    main_window._on_warmup_done(False)

    assert launched == [Mode.DRY_RUN]
    assert "askpass" in main_window._status.currentMessage().lower()
