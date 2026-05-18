"""Audit C4 + general risk classifier coverage."""

from __future__ import annotations

import pytest

from archward.config.defaults import default_config
from archward.models.update import RiskLevel
from archward.pipeline.risk import classify_one


@pytest.fixture(scope="module")
def cfg():
    return default_config()


@pytest.mark.parametrize(
    "pkg, expected_risk, expected_is_kernel",
    [
        # Audit C4 — kernel + headers must classify HIGH with is_kernel=True.
        ("linux", RiskLevel.HIGH, True),
        ("linux-headers", RiskLevel.HIGH, True),
        ("linux-lts", RiskLevel.HIGH, True),
        ("linux-lts-headers", RiskLevel.HIGH, True),
        ("linux-zen-headers", RiskLevel.HIGH, True),
        ("linux-hardened-headers", RiskLevel.HIGH, True),
        ("linux-cachyos-bore", RiskLevel.HIGH, True),
        ("linux-cachyos-bore-headers", RiskLevel.HIGH, True),
        ("linux-cachyos-zfs", RiskLevel.HIGH, True),
        ("linux-api-headers", RiskLevel.HIGH, True),
        # Excludes — firmware and docs are NOT kernel-risk.
        ("linux-firmware", RiskLevel.LOW, False),
        ("linux-docs", RiskLevel.LOW, False),
        # Explicit HIGH list.
        ("glibc", RiskLevel.HIGH, False),
        ("openssh", RiskLevel.HIGH, False),
        ("mesa", RiskLevel.HIGH, False),
        # MEDIUM patterns.
        ("docker", RiskLevel.MEDIUM, False),
        ("docker-compose", RiskLevel.MEDIUM, False),
        ("qemu-base", RiskLevel.MEDIUM, False),
        ("libvirt-glib", RiskLevel.MEDIUM, False),
        ("nginx-mainline", RiskLevel.MEDIUM, False),
        # LOW fallthrough.
        ("vim", RiskLevel.LOW, False),
        ("htop", RiskLevel.LOW, False),
        ("python-requests", RiskLevel.LOW, False),
    ],
)
def test_classify_one(cfg, pkg, expected_risk, expected_is_kernel):
    result = classify_one(pkg, "1.0", "1.1", cfg)
    assert result.risk is expected_risk, f"{pkg}: got {result.risk}, want {expected_risk}"
    assert result.is_kernel is expected_is_kernel, (
        f"{pkg}: got is_kernel={result.is_kernel}, want {expected_is_kernel}"
    )


def test_high_takes_priority_over_kernel_pattern(cfg):
    """A package in risk.high (e.g. glibc) should not be re-classified by kernel patterns."""
    result = classify_one("glibc", "2.40", "2.41", cfg)
    assert result.risk is RiskLevel.HIGH
    assert result.reason == "in risk.high"
    assert result.is_kernel is False


def test_excluded_pattern_falls_through(cfg):
    """linux-firmware matches kernel_pattern_exclude — must NOT be HIGH."""
    result = classify_one("linux-firmware", "20260501", "20260514", cfg)
    assert result.risk is RiskLevel.LOW
    assert result.is_kernel is False


# ── Major version bump feature ────────────────────────────────────────────


def test_major_version_bump_appends_warning(cfg):
    """HIGH-risk package with differing leading integer → reason gets ⚠ MAJOR VERSION."""
    result = classify_one("openssl", "1.1.1-1", "3.5.0-1", cfg)
    assert result.risk is RiskLevel.HIGH
    assert "⚠ MAJOR VERSION" in result.reason
    assert "in risk.high" in result.reason


def test_minor_bump_no_major_version_warning(cfg):
    """Bump within same major — no warning appended."""
    result = classify_one("openssl", "3.4.0-1", "3.5.0-1", cfg)
    assert result.risk is RiskLevel.HIGH
    assert "⚠ MAJOR VERSION" not in result.reason


def test_kernel_pattern_no_major_version_flag(cfg):
    """Kernel packages go through the kernel-pattern branch — not the risk.high branch."""
    result = classify_one("linux", "6.12.1.arch1-1", "7.0.0.arch1-1", cfg)
    assert result.is_kernel is True
    assert "⚠ MAJOR VERSION" not in result.reason
