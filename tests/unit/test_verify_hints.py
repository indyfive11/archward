"""Tests for VerifyView's 'What to do?' remediation-hint buttons (F5, v0.4.0).

The renderer attaches a button via setItemWidget on FAIL rows that have a
registered hint key. PASS/WARN rows and FAIL rows with unknown check
names get nothing. The hint text comes from help_text.HELP under the
verify_hint section.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QPushButton

from archward.models.verify import CheckStatus, VerifyCheck, VerifyResult
from archward.ui.dialogs import help_text
from archward.ui.views.verify_view import VerifyView, _hint_key_for


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def _view_with_checks(checks: list[VerifyCheck]) -> VerifyView:
    v = VerifyView()
    result = VerifyResult(
        checks=tuple(checks),
        fail_count=sum(1 for c in checks if c.status is CheckStatus.FAIL),
        warn_count=sum(1 for c in checks if c.status is CheckStatus.WARN),
        reboot_needed=False,
    )
    v.set_result(result)
    return v


def _iter_child_widgets(view: VerifyView, column: int):
    """Yield the widget in `column` for each (non-group) child row."""
    tree = view._tree
    for i in range(tree.topLevelItemCount()):
        group = tree.topLevelItem(i)
        for j in range(group.childCount()):
            child = group.child(j)
            yield child, tree.itemWidget(child, column)


def test_fail_kernel_check_gets_hint_button(qapp) -> None:
    view = _view_with_checks([
        VerifyCheck(
            bucket="universal",
            name="kernel",
            status=CheckStatus.FAIL,
            message="kernel mismatch",
        ),
    ])
    widgets = list(_iter_child_widgets(view, 3))
    assert len(widgets) == 1
    _, btn = widgets[0]
    assert isinstance(btn, QPushButton)
    assert btn.text() == "What to do?"


def test_pass_check_gets_no_button(qapp) -> None:
    view = _view_with_checks([
        VerifyCheck(
            bucket="universal",
            name="kernel",
            status=CheckStatus.PASS,
            message="kernel match",
        ),
    ])
    widgets = list(_iter_child_widgets(view, 3))
    assert len(widgets) == 1
    _, btn = widgets[0]
    assert btn is None


def test_warn_check_gets_no_button(qapp) -> None:
    view = _view_with_checks([
        VerifyCheck(
            bucket="universal",
            name="pacnew",
            status=CheckStatus.WARN,
            message="3 .pacnew files present",
        ),
    ])
    widgets = list(_iter_child_widgets(view, 3))
    assert len(widgets) == 1
    _, btn = widgets[0]
    assert btn is None


def test_unknown_check_name_no_button(qapp) -> None:
    """A FAIL row whose check has no registered hint key gets no button."""
    view = _view_with_checks([
        VerifyCheck(
            bucket="universal",
            name="completely-unknown-check-name-xyz",
            status=CheckStatus.FAIL,
            message="???",
        ),
    ])
    widgets = list(_iter_child_widgets(view, 3))
    assert len(widgets) == 1
    _, btn = widgets[0]
    assert btn is None


def test_service_bucket_uses_service_hint(qapp) -> None:
    """Any service FAIL (unit name varies) keys onto verify_hint.service."""
    view = _view_with_checks([
        VerifyCheck(
            bucket="services",
            name="sshd.service",
            status=CheckStatus.FAIL,
            message="inactive",
        ),
    ])
    widgets = list(_iter_child_widgets(view, 3))
    assert len(widgets) == 1
    _, btn = widgets[0]
    assert isinstance(btn, QPushButton)


def test_plugin_bucket_uses_plugin_hint(qapp) -> None:
    view = _view_with_checks([
        VerifyCheck(
            bucket="plugin",
            name="plugin:archward-verify-zfs",
            status=CheckStatus.FAIL,
            message="pool degraded",
        ),
    ])
    widgets = list(_iter_child_widgets(view, 3))
    assert len(widgets) == 1
    _, btn = widgets[0]
    assert isinstance(btn, QPushButton)


def test_hint_key_function_normalizes_hyphens() -> None:
    """pacman-log → pacman_log; reboot-log → reboot_log."""
    assert _hint_key_for("universal", "pacman-log") == "pacman_log"
    assert _hint_key_for("universal", "reboot-log") == "reboot_log"
    assert _hint_key_for("universal", "kernel") == "kernel"


def test_hint_key_function_bucket_override() -> None:
    """services/plugin always map to their bucket name, ignoring the check name."""
    assert _hint_key_for("services", "anything.service") == "service"
    assert _hint_key_for("plugin", "anything") == "plugin"


def test_all_documented_hints_resolve_to_strings() -> None:
    """Every shipped verify_hint entry returns a non-empty string."""
    for key in ("kernel", "pacnew", "disk", "pacman_log", "reboot_log",
                "service", "plugin"):
        text = help_text.get("verify_hint", key)
        assert text, f"verify_hint.{key} should have non-empty text"
