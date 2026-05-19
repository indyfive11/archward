"""Tests for AUR result models (v0.4.12) — QuarantineActiveEntry.last_error
and the _build_quarantine_snapshot producer."""

from __future__ import annotations

import time

from archward.models.aur import QuarantineActiveEntry, QuarantineSnapshot
from archward.models.config import AurConfig
from archward.pipeline.update_aur import _build_quarantine_snapshot


# ── QuarantineActiveEntry ─────────────────────────────────────────────────────

def test_quarantine_active_entry_carries_last_error() -> None:
    e = QuarantineActiveEntry(
        package="radarr",
        version="4.0.0.1-1",
        status="quarantined",
        failure_count=3,
        retry_after="2026-05-27T00:00:00+00:00",
        last_error="==> ERROR: NU1902 detected",
    )
    assert e.last_error == "==> ERROR: NU1902 detected"


def test_quarantine_active_entry_last_error_nullable() -> None:
    e = QuarantineActiveEntry(
        package="pkg",
        version="1.0-1",
        status="counting",
        failure_count=1,
        retry_after=None,
        last_error=None,
    )
    assert e.last_error is None


# ── _build_quarantine_snapshot ────────────────────────────────────────────────

def _make_quarantine_with_entry(last_error: str | None, *, failure_count: int = 3):
    from archward.aur.quarantine import AurQuarantine, QuarantineEntry

    cfg = AurConfig(
        enabled=True,
        quarantine_enabled=True,
        quarantine_min_failures=3,
        quarantine_initial_days=7,
        quarantine_max_days=28,
    )
    q = AurQuarantine(cfg)
    now = time.time()
    q._data["mypkg"] = QuarantineEntry(
        version="1.0-1",
        status="quarantined",
        first_failure_at=now - 7 * 86_400,
        last_failure_at=now - 86_400,
        failure_count=failure_count,
        retry_after=now + 7 * 86_400,
        retry_interval_days=7,
        last_error=last_error,
        resolved_at=None,
        resolved_reason=None,
    )
    return q


def test_snapshot_carries_last_error() -> None:
    q = _make_quarantine_with_entry("==> ERROR: build failed (NU1902)")
    snap = _build_quarantine_snapshot(q)
    assert snap is not None
    assert len(snap.active) == 1
    assert snap.active[0].last_error == "==> ERROR: build failed (NU1902)"


def test_snapshot_truncates_long_last_error() -> None:
    long_err = "x" * 200
    q = _make_quarantine_with_entry(long_err)
    snap = _build_quarantine_snapshot(q)
    assert snap is not None
    assert len(snap.active[0].last_error) == 80


def test_snapshot_null_last_error() -> None:
    q = _make_quarantine_with_entry(None)
    snap = _build_quarantine_snapshot(q)
    assert snap is not None
    assert snap.active[0].last_error is None


def test_snapshot_returns_none_when_no_active() -> None:
    from archward.aur.quarantine import AurQuarantine
    from archward.models.config import AurConfig

    cfg = AurConfig(
        enabled=True,
        quarantine_enabled=True,
        quarantine_min_failures=3,
        quarantine_initial_days=7,
        quarantine_max_days=28,
    )
    q = AurQuarantine(cfg)
    assert _build_quarantine_snapshot(q) is None
