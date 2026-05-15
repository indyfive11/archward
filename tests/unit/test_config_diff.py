"""Tests for archward.config.diff — pure-logic config-vs-default unified diff."""

from __future__ import annotations

from archward.config.defaults import default_config
from archward.config.diff import unified_diff
from archward.config.loader import merge_partial
from archward.models.config import ServicesConfig


class TestUnifiedDiff:
    def test_identical_configs_yield_empty_diff(self) -> None:
        cfg = default_config()
        assert unified_diff(cfg, cfg) == []

    def test_change_surfaces_as_diff_lines(self) -> None:
        cfg_a = default_config()
        # Modify a single field; the diff should show that field changing.
        cfg_b = merge_partial(
            cfg_a,
            services=ServicesConfig(
                to_verify=("sshd.service",),
                severity=dict(cfg_a.services.severity),
                auto_prune=cfg_a.services.auto_prune,
            ),
        )
        diff = unified_diff(cfg_a, cfg_b, a_label="defaults", b_label="custom")
        joined = "".join(diff)
        assert joined != ""
        # Header lines included
        assert any(line.startswith("---") and "defaults" in line for line in diff)
        assert any(line.startswith("+++") and "custom" in line for line in diff)
        # The addition shows up
        assert "sshd.service" in joined

    def test_diff_labels_appear_in_header(self) -> None:
        cfg_a = default_config()
        cfg_b = merge_partial(
            cfg_a,
            services=ServicesConfig(
                to_verify=("foo.service",),
                severity=dict(cfg_a.services.severity),
                auto_prune=cfg_a.services.auto_prune,
            ),
        )
        diff = unified_diff(cfg_a, cfg_b, a_label="archward-defaults", b_label="mylab")
        assert any("archward-defaults" in line for line in diff if line.startswith("---"))
        assert any("mylab" in line for line in diff if line.startswith("+++"))

    def test_removal_shows_minus_line(self) -> None:
        """If b drops fields that a has, the diff should show `-` lines."""
        # Start with a config that has a service entry; default removes it.
        cfg_with_service = merge_partial(
            default_config(),
            services=ServicesConfig(
                to_verify=("zombied.service",),
                severity={},
                auto_prune=False,
            ),
        )
        diff = unified_diff(cfg_with_service, default_config(), a_label="custom", b_label="defaults")
        joined = "".join(diff)
        # The removal direction means "zombied.service" appears on a `-` line.
        assert any(line.startswith("-") and "zombied.service" in line for line in diff)

    def test_diff_returns_list_of_strings_with_newlines(self) -> None:
        """All non-empty diff lines should end with a newline so they can
        be ''.join()ed into a coherent multi-line block."""
        cfg_a = default_config()
        cfg_b = merge_partial(
            cfg_a,
            services=ServicesConfig(
                to_verify=("a.service",),
                severity=dict(cfg_a.services.severity),
                auto_prune=cfg_a.services.auto_prune,
            ),
        )
        diff = unified_diff(cfg_a, cfg_b)
        # difflib emits unified_diff lines with embedded newlines for content
        # lines; header lines end with \n too. Verify every non-empty line
        # is terminated so the join produces a well-formed block.
        for line in diff:
            assert line.endswith("\n"), f"line missing terminator: {line!r}"
