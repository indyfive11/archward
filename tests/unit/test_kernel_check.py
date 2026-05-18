"""Tests for _kernel_check() and _extract_kernel_flavor()."""

from __future__ import annotations

import pytest

from archward.pipeline import verify_phase
from archward.pipeline.verify_phase import _extract_kernel_flavor
from archward.models.verify import CheckStatus


# ── _extract_kernel_flavor ────────────────────────────────────────────────


@pytest.mark.parametrize("release,expected", [
    ("6.18.26-2-lts",        "lts"),
    ("7.0.9-arch1-1",        ""),
    ("7.0.9-1-cachyos-bore", "cachyos-bore"),
    ("6.6.15-1-zen",         "zen"),
    ("6.6.15-1-hardened",    "hardened"),
    ("6.18.32-1-lts",        "lts"),
    ("6.0.0",                ""),        # no dash → no pkgrel → fallback ""
])
def test_extract_kernel_flavor(release, expected) -> None:
    assert _extract_kernel_flavor(release) == expected


# ── _kernel_check ─────────────────────────────────────────────────────────


def _patch(monkeypatch, running: str, packages: list[tuple[str, str]]) -> None:
    monkeypatch.setattr(verify_phase.kernel, "running_kernel", lambda: running)
    monkeypatch.setattr(verify_phase.pq, "list_all", lambda: packages)


def test_lts_kernel_matches_linux_lts(monkeypatch) -> None:
    """Multi-kernel system booting linux-lts should compare against linux-lts."""
    _patch(monkeypatch,
           running="6.18.26-2-lts",
           packages=[
               ("linux", "7.0.9.arch1-1"),
               ("linux-lts", "6.18.26-1-lts"),
           ])
    result = verify_phase._kernel_check()
    assert result.status is CheckStatus.PASS
    assert "linux-lts" in result.message


def test_lts_kernel_reports_reboot_for_lts_not_mainline(monkeypatch) -> None:
    """After linux-lts update, running=old lts version → WARN against linux-lts."""
    _patch(monkeypatch,
           running="6.18.26-2-lts",
           packages=[
               ("linux", "7.0.9.arch1-1"),
               ("linux-lts", "6.18.32-1-lts"),
           ])
    result = verify_phase._kernel_check()
    assert result.status is CheckStatus.WARN
    assert "linux-lts" in result.message
    assert "linux 7.0.9" not in result.message


def test_mainline_kernel_matches_linux(monkeypatch) -> None:
    """CachyOS bare mainline kernel: no flavor suffix → linux package."""
    _patch(monkeypatch,
           running="7.0.9-arch1-1",
           packages=[("linux", "7.0.9.arch1-1"), ("linux-lts", "6.18.32-1-lts")])
    result = verify_phase._kernel_check()
    assert result.status is CheckStatus.PASS
    assert "linux" in result.message


def test_cachyos_kernel_matches_flavor(monkeypatch) -> None:
    _patch(monkeypatch,
           running="7.0.9-1-cachyos-bore",
           packages=[
               ("linux", "7.0.9.arch1-1"),
               ("linux-cachyos-bore", "7.0.9-1-cachyos-bore"),
           ])
    result = verify_phase._kernel_check()
    assert result.status is CheckStatus.PASS
    assert "linux-cachyos-bore" in result.message


def test_single_kernel_fallback(monkeypatch) -> None:
    """Single kernel installed → match regardless of name."""
    _patch(monkeypatch,
           running="6.6.15-1-zen",
           packages=[("linux-zen", "6.6.15-1")])
    result = verify_phase._kernel_check()
    assert result.status is CheckStatus.PASS


def test_no_candidates_warns(monkeypatch) -> None:
    _patch(monkeypatch, running="7.0.9-arch1-1", packages=[])
    result = verify_phase._kernel_check()
    assert result.status is CheckStatus.WARN
    assert "No linux*" in result.message
