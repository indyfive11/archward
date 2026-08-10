"""Shared off-thread runner for privileged / slow operations (v0.4.17).

Extracted from SnapshotBrowser's `_run_off_thread`. Any GUI-thread slot that
runs sudo subprocesses (pacnew actions, DiffDialog's sudo cat, the
Verify/Cache preference writes, detect scans, bulk rollback) must route
through this: a cold sudo timestamp pops the askpass dialog, and blocking
the Qt event loop on it freezes the whole GUI — including the dialog the
user needs to answer.
"""

from __future__ import annotations

import logging
from typing import Callable

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import QProgressDialog, QWidget

log = logging.getLogger(__name__)


class FnWorker(QThread):
    """Runs one callable off the main thread; emits its return value.

    An exception raised by the callable is caught and emitted as the result
    object (callers check `isinstance(result, Exception)`) so the QThread
    never dies silently.

    Cancellation isn't supported — these operations are short (seconds) and
    interrupting `pacman -U` or a sudo write mid-flight is unsafe by the
    same logic that keeps the main pipeline from killing pacman.
    """

    finished_with_result = Signal(object)

    def __init__(self, fn: Callable[[], object], parent=None) -> None:
        super().__init__(parent)
        self._fn = fn
        self.result = None  # populated before the signal fires

    def run(self) -> None:
        try:
            self.result = self._fn()
        except Exception as e:  # noqa: BLE001 — must catch all so the QThread doesn't die silently
            log.exception("off-thread worker raised")
            self.result = e
        self.finished_with_result.emit(self.result)


def run_off_thread(
    parent: QWidget,
    *,
    fn: Callable[[], object],
    title: str,
    progress_label: str,
    on_done: Callable[[object], None],
) -> FnWorker:
    """Run `fn` on a FnWorker, show an indeterminate QProgressDialog until it
    finishes, then dispatch the result to `on_done` on the main thread.

    The progress dialog has no Cancel button — these operations should not
    be interrupted mid-flight. Returns the worker (kept alive by parent and
    released via deleteLater once finished).
    """
    progress = QProgressDialog(progress_label, "", 0, 0, parent)
    progress.setWindowTitle(title)
    progress.setWindowModality(Qt.WindowModality.WindowModal)
    progress.setMinimumDuration(0)
    progress.setCancelButton(None)
    progress.setAutoClose(False)
    progress.setAutoReset(False)
    progress.show()

    worker = FnWorker(fn, parent=parent)

    def _on_finished(result: object) -> None:
        progress.close()
        progress.deleteLater()
        on_done(result)
        worker.deleteLater()

    worker.finished_with_result.connect(_on_finished)
    worker.start()
    return worker
