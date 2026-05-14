"""Bulk rollback planner — change-set computation + boot-critical refusal."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from archward.pipeline.rollback import (
    BOOT_CRITICAL,
    apply_all_packages,
    plan_bulk_package_apply,
)


def _make_snapshot(tmp_path: Path, all_pkgs: dict[str, str], critical: list[str]) -> Path:
    """Build a minimal snapshot dir with packages/all.txt and critical.txt."""
    snap = tmp_path / "snap-1"
    (snap / "packages").mkdir(parents=True)
    (snap / "packages" / "all.txt").write_text(
        "\n".join(f"{n} {v}" for n, v in all_pkgs.items()) + "\n"
    )
    (snap / "packages" / "critical.txt").write_text(
        "=== Critical package versions pre-update ===\n"
        + "\n".join(f"{name}: {all_pkgs[name]}" for name in critical if name in all_pkgs)
        + "\n"
    )
    return snap


def test_plan_skips_unchanged(tmp_path: Path) -> None:
    snap = _make_snapshot(
        tmp_path, {"glibc": "2.40-1", "vim": "9.1-1"}, ["glibc", "vim"]
    )
    # Current installed matches snapshot — no changes.
    with patch(
        "archward.pacman.query.list_all",
        return_value=[("glibc", "2.40-1"), ("vim", "9.1-1")],
    ):
        changes, skipped = plan_bulk_package_apply(snap, (), ())
    assert changes == []
    assert skipped == []


def test_plan_skips_not_in_cache(tmp_path: Path) -> None:
    snap = _make_snapshot(tmp_path, {"glibc": "2.40-1"}, ["glibc"])
    with patch(
        "archward.pacman.query.list_all", return_value=[("glibc", "2.41-1")]
    ), patch(
        "archward.pipeline.rollback.find_package_in_cache", return_value=None
    ):
        changes, skipped = plan_bulk_package_apply(snap, (), ())
    assert changes == []
    assert len(skipped) == 1
    assert skipped[0][0] == "glibc"
    assert "not in" in skipped[0][1]


def test_plan_finds_real_change(tmp_path: Path) -> None:
    snap = _make_snapshot(tmp_path, {"glibc": "2.40-1"}, ["glibc"])
    fake_cache = tmp_path / "glibc-2.40-1.pkg.tar.zst"
    fake_cache.touch()
    with patch(
        "archward.pacman.query.list_all", return_value=[("glibc", "2.41-1")]
    ), patch(
        "archward.pipeline.rollback.find_package_in_cache", return_value=fake_cache
    ):
        changes, skipped = plan_bulk_package_apply(snap, (), ())
    assert len(changes) == 1
    name, current, target, cache_path = changes[0]
    assert name == "glibc"
    assert current == "2.41-1"
    assert target == "2.40-1"
    assert cache_path == fake_cache


def test_apply_all_refuses_boot_critical_without_override(tmp_path: Path) -> None:
    snap = _make_snapshot(tmp_path, {"glibc": "2.40-1"}, ["glibc"])
    fake_cache = tmp_path / "glibc-2.40-1.pkg.tar.zst"
    fake_cache.touch()

    # Mock current state + cache + the actual pacman -U call.
    with patch(
        "archward.pacman.query.list_all", return_value=[("glibc", "2.41-1")]
    ), patch(
        "archward.pipeline.rollback.find_package_in_cache", return_value=fake_cache
    ), patch(
        "archward.pipeline.rollback.run_capture"
    ) as mock_run:
        result = apply_all_packages(
            snap, strategy=None, kernel_patterns=(), kernel_pattern_exclude=(),
            include_boot_critical=False,
        )
    assert result.success is False
    assert "boot-critical" in result.message.lower()
    # pacman was NEVER invoked.
    mock_run.assert_not_called()


def test_apply_all_proceeds_when_override(tmp_path: Path) -> None:
    snap = _make_snapshot(tmp_path, {"glibc": "2.40-1"}, ["glibc"])
    fake_cache = tmp_path / "glibc-2.40-1.pkg.tar.zst"
    fake_cache.touch()
    with patch(
        "archward.pacman.query.list_all", return_value=[("glibc", "2.41-1")]
    ), patch(
        "archward.pipeline.rollback.find_package_in_cache", return_value=fake_cache
    ), patch(
        "archward.pipeline.rollback.run_capture", return_value=(0, "", "")
    ) as mock_run:
        result = apply_all_packages(
            snap, strategy=None, kernel_patterns=(), kernel_pattern_exclude=(),
            include_boot_critical=True,
        )
    assert result.success is True
    assert "applied 1" in result.message
    # pacman -U was invoked with the cached package.
    assert mock_run.call_count == 1
    args = mock_run.call_args[0][0]
    assert args[:3] == ["pacman", "-U", "--noconfirm"]
    assert str(fake_cache) in args


def test_apply_all_does_not_invoke_pacman_for_non_critical_no_changes(tmp_path: Path) -> None:
    """If nothing needs changing, pacman -U should not be invoked at all."""
    snap = _make_snapshot(tmp_path, {"vim": "9.1-1"}, ["vim"])
    with patch(
        "archward.pacman.query.list_all", return_value=[("vim", "9.1-1")]
    ), patch(
        "archward.pipeline.rollback.run_capture"
    ) as mock_run:
        result = apply_all_packages(snap, None, (), ())
    assert result.success is True
    assert "nothing to apply" in result.message
    mock_run.assert_not_called()


def test_boot_critical_set_contains_expected_names() -> None:
    """Sanity check on the constant — these are the names the UI gates on."""
    assert "glibc" in BOOT_CRITICAL
    assert "systemd" in BOOT_CRITICAL
    assert "systemd-libs" in BOOT_CRITICAL
    assert "openssl" in BOOT_CRITICAL
    # Things NOT in the set:
    assert "linux" not in BOOT_CRITICAL  # kernel — handled by kernel_patterns
    assert "vim" not in BOOT_CRITICAL
