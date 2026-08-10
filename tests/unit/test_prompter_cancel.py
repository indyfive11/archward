"""v0.4.17 uniform prompter-cancel contract.

Every worker-thread blocking call in the GUI prompters must be unblockable
from the main thread via cancel_pending() — the BlockingQueuedConnection
paths (gate override, PKGBUILD review) deadlocked on window close because
the closing main thread could never deliver the queued slot.
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

import pytest
from PySide6.QtWidgets import QApplication

from archward.models.gate import GateResult, GateStatus


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def _wait_blocked(thread: threading.Thread, timeout: float = 0.3) -> None:
    """Give the worker thread time to reach its Event.wait()."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not thread.is_alive():
        time.sleep(0.01)
    time.sleep(0.05)


def test_gate_override_cancel_unblocks_worker(qapp):
    """confirm_gate_override from a worker thread returns False on cancel —
    without the main thread ever processing the queued modal request."""
    from archward.ui.prompter import GuiPrompter
    from archward.ui.views.risk_view import RiskView

    view = RiskView()
    prompter = GuiPrompter(risk_view=view)
    gate = GateResult(
        name="snapshot-age", status=GateStatus.FAIL, message="too old", can_override=True
    )

    answer: dict[str, bool] = {}

    def worker() -> None:
        answer["value"] = prompter.confirm_gate_override(gate)

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    _wait_blocked(t)

    prompter.cancel_pending()
    t.join(2.0)

    assert not t.is_alive(), "worker still blocked after cancel_pending()"
    assert answer["value"] is False


def test_pkgbuild_review_cancel_unblocks_worker(qapp, monkeypatch):
    """review() from a worker thread returns False (CANCEL_ALL) on cancel —
    the old BlockingQueuedConnection could never be cancelled."""
    import archward.ui.prompter as prompter_mod
    from archward.ui.prompter import PkgbuildPrompter

    monkeypatch.setattr(prompter_mod, "fetch_aur_info", lambda pkg: None)
    monkeypatch.setattr(prompter_mod, "fetch_pkgbuild", lambda pkg: "pkgname=x")
    cache = MagicMock()
    cache.get.return_value = None
    monkeypatch.setattr(prompter_mod, "PkgbuildCache", lambda: cache)

    prompter = PkgbuildPrompter(main_window=None)

    answer: dict[str, bool] = {}

    def worker() -> None:
        answer["value"] = prompter.review("some-pkg")

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    _wait_blocked(t)

    prompter.cancel_pending()
    t.join(2.0)

    assert not t.is_alive(), "worker still blocked after cancel_pending()"
    assert answer["value"] is False
    assert prompter.cancel_all_requested() is True


def test_high_risk_decision_cancel_alias_still_works(qapp):
    """The pre-v0.4.17 name cancel_pending_decision remains callable."""
    from archward.ui.prompter import GuiPrompter
    from archward.ui.views.risk_view import RiskView

    prompter = GuiPrompter(risk_view=RiskView())
    prompter.cancel_pending_decision()  # must not raise
