"""Tests for PkgbuildReviewDialog — F3, v0.4.0.

Doesn't pixel-render; just instantiates the dialog, simulates button
clicks via the internal _on_* slots, and verifies the result enum.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QLabel, QTabWidget

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


def test_dialog_no_previous_has_no_tabs(qapp) -> None:
    """First review (no cache entry): plain view, no QTabWidget."""
    dlg = PkgbuildReviewDialog("foo", "pkgname=foo\n", previous_content=None)
    assert dlg.findChild(QTabWidget) is None


def test_dialog_changed_content_has_tabs(qapp) -> None:
    """Changed PKGBUILD: QTabWidget with 'Changes' as the first tab."""
    dlg = PkgbuildReviewDialog(
        "foo",
        "pkgname=foo\npkgver=2.0\n",
        previous_content="pkgname=foo\npkgver=1.0\n",
    )
    tabs = dlg.findChild(QTabWidget)
    assert tabs is not None
    assert tabs.tabText(0) == "Changes"
    assert tabs.tabText(1) == "Full PKGBUILD"
    assert tabs.currentIndex() == 0


def test_dialog_identical_content_no_tabs(qapp) -> None:
    """Identical PKGBUILD: no tabs, 'No changes' banner present."""
    content = "pkgname=foo\npkgver=1.0\n"
    dlg = PkgbuildReviewDialog("foo", content, previous_content=content)
    assert dlg.findChild(QTabWidget) is None
    labels = dlg.findChildren(QLabel)
    assert any("No changes" in lbl.text() for lbl in labels)
