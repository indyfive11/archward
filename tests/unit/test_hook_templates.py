"""Tests for hook_templates (F4, v0.4.0)."""

from __future__ import annotations

from archward.ui.dialogs.hook_templates import (
    HOOK_TEMPLATES,
    format_template_for_insertion,
)


def test_template_dict_nonempty() -> None:
    assert len(HOOK_TEMPLATES) >= 4


def test_every_template_has_valid_kind() -> None:
    for label, (kind, body) in HOOK_TEMPLATES.items():
        assert kind in ("pre", "post"), f"{label!r} has bad kind {kind!r}"


def test_every_template_body_ends_with_newline() -> None:
    """Append-on-select assumes each body terminates cleanly."""
    for label, (_kind, body) in HOOK_TEMPLATES.items():
        assert body.endswith("\n"), f"{label!r} body must end with newline"


def test_format_for_insertion_includes_header_comment() -> None:
    label = next(iter(HOOK_TEMPLATES))
    out = format_template_for_insertion(label)
    assert out.startswith(f"# template: {label}\n")
    # Should end with a separator blank line so consecutive inserts stay readable.
    assert out.endswith("\n\n")


def test_format_for_insertion_unknown_label_returns_empty() -> None:
    assert format_template_for_insertion("nope-not-a-template") == ""


def test_pre_and_post_kinds_both_have_at_least_one() -> None:
    """The combobox split (pre vs post) requires at least one of each kind."""
    kinds = {kind for kind, _body in HOOK_TEMPLATES.values()}
    assert "pre" in kinds
    assert "post" in kinds
