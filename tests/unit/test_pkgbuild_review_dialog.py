"""Tests for PkgbuildReviewDialog — F3, v0.4.0.

Doesn't pixel-render; just instantiates the dialog, simulates button
clicks via the internal _on_* slots, and verifies the result enum.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from archward.ui.dialogs.pkgbuild_review import (
    PkgbuildReviewDialog,
    PkgbuildReviewResult,
)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def test_approve_button_yields_approve(qapp) -> None:
    dlg = PkgbuildReviewDialog("foo", "pkgname=foo\n")
    dlg._on_approve()
    assert dlg._result is PkgbuildReviewResult.APPROVE


def test_reject_button_yields_reject(qapp) -> None:
    dlg = PkgbuildReviewDialog("bar", "pkgname=bar\n")
    dlg._on_reject()
    assert dlg._result is PkgbuildReviewResult.REJECT


def test_cancel_button_yields_cancel_all(qapp) -> None:
    dlg = PkgbuildReviewDialog("baz", "pkgname=baz\n")
    dlg._on_cancel_all()
    assert dlg._result is PkgbuildReviewResult.CANCEL_ALL


def test_fetch_failed_branch_offers_retry(qapp) -> None:
    """When content is None the dialog shows the failed-fetch UI with Retry."""
    dlg = PkgbuildReviewDialog("ghost", None)
    dlg._on_retry()
    assert dlg._result is PkgbuildReviewResult.RETRY


def test_fetch_failed_branch_skip_yields_reject(qapp) -> None:
    """Skip-this-package in the fetch-failed branch returns REJECT."""
    dlg = PkgbuildReviewDialog("ghost", None)
    dlg._on_reject()
    assert dlg._result is PkgbuildReviewResult.REJECT
