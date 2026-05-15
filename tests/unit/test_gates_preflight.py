"""Tests for v0.4.1 F13 — stale-lock UX clarity in preflight.

Regression: the FAIL detail for a held pacman.lck used to be a single
generic blurb. Users had to figure out on their own whether the lock
was stale (safe to remove) or live (wait). The new detail message
differentiates the two cases and tells stale-lock users the exact
recovery command.
"""

from __future__ import annotations

from archward.events import EventBus
from archward.models.gate import GateStatus
from archward.pipeline import gates


def test_preflight_unlocked_db(monkeypatch) -> None:
    """Sanity check the happy path still passes."""
    monkeypatch.setattr(gates, "check_pacman_db_lock", lambda: (False, None))
    results = gates.preflight_checks(EventBus())
    assert all(r.status is GateStatus.PASS for r in results)


def test_preflight_stale_lock_gives_recovery_hint(monkeypatch) -> None:
    """Stale-lock FAIL must mention `sudo rm /var/lib/pacman/db.lck`."""
    monkeypatch.setattr(
        gates, "check_pacman_db_lock",
        lambda: (True, "stale lock (no live process)"),
    )
    results = gates.preflight_checks(EventBus())
    failed = next(r for r in results if r.status is GateStatus.FAIL)
    assert "sudo rm /var/lib/pacman/db.lck" in failed.detail
    # Mention that archward doesn't auto-remove (so a curious user knows
    # this isn't a bug they should report).
    assert "never auto-removes" in failed.detail


def test_preflight_live_lock_says_wait(monkeypatch) -> None:
    """Live-lock FAIL must tell the user to wait, NOT to rm."""
    monkeypatch.setattr(
        gates, "check_pacman_db_lock",
        lambda: (True, "pacman (pid 12345)"),
    )
    results = gates.preflight_checks(EventBus())
    failed = next(r for r in results if r.status is GateStatus.FAIL)
    assert "Wait" in failed.detail
    # Live-lock message should NOT recommend rm — that's only safe for
    # the stale case.
    assert "sudo rm" not in failed.detail
