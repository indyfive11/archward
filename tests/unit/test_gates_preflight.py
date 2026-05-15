"""Tests for v0.4.1 F13 — stale-lock UX clarity in preflight.

Regression: the FAIL detail for a held pacman.lck used to be a single
generic blurb. Users had to figure out on their own whether the lock
was stale (safe to remove) or live (wait). The new detail message
differentiates the two cases and tells stale-lock users the exact
recovery command.
"""

from __future__ import annotations

import pytest

from archward.config.defaults import default_config
from archward.events import EventBus
from archward.models.gate import GateStatus
from archward.pipeline import gates
from archward.system import cache_policy as cp


@pytest.fixture(autouse=True)
def _stub_cache_policy(monkeypatch):
    """Pre-flight now also runs cache-policy detection (v0.4.4 F2).

    Stub it to a BALANCED (PASS) verdict so these lock tests stay
    hermetic and don't touch the real /etc, /var/cache, or systemctl.
    """
    monkeypatch.setattr(
        gates.cp,
        "detect_cache_policy",
        lambda: cp.CachePolicy(
            timer_state="enabled",
            paccache_args="-rk3",
            effective_keep=3,
            clean_method=("KeepInstalled",),
            cleaning_hooks=(),
            cache_size_bytes=0,
            cache_file_count=0,
            safety=cp.RollbackSafety.BALANCED,
            explanation="balanced",
        ),
    )


def test_preflight_unlocked_db(monkeypatch) -> None:
    """Sanity check the happy path still passes."""
    monkeypatch.setattr(gates, "check_pacman_db_lock", lambda: (False, None))
    results = gates.preflight_checks(default_config(), EventBus())
    assert all(r.status is GateStatus.PASS for r in results)
    assert any(r.name == "cache-safety" for r in results)


def test_preflight_stale_lock_gives_recovery_hint(monkeypatch) -> None:
    """Stale-lock FAIL must mention `sudo rm /var/lib/pacman/db.lck`."""
    monkeypatch.setattr(
        gates, "check_pacman_db_lock",
        lambda: (True, "stale lock (no live process)"),
    )
    results = gates.preflight_checks(default_config(), EventBus())
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
    results = gates.preflight_checks(default_config(), EventBus())
    failed = next(r for r in results if r.status is GateStatus.FAIL)
    assert "Wait" in failed.detail
    # Live-lock message should NOT recommend rm — that's only safe for
    # the stale case.
    assert "sudo rm" not in failed.detail


def test_preflight_dangerous_cache_warns_overridable(monkeypatch) -> None:
    """A cleaning hook → cache-safety WARN that is overridable."""
    monkeypatch.setattr(gates, "check_pacman_db_lock", lambda: (False, None))
    from pathlib import Path

    monkeypatch.setattr(
        gates.cp,
        "detect_cache_policy",
        lambda: cp.CachePolicy(
            timer_state="disabled",
            paccache_args="",
            effective_keep=3,
            clean_method=("KeepInstalled",),
            cleaning_hooks=(Path("/etc/pacman.d/hooks/zz-clean.hook"),),
            cache_size_bytes=0,
            cache_file_count=0,
            safety=cp.RollbackSafety.DANGEROUS,
            explanation="a hook will eat your rollback",
        ),
    )
    results = gates.preflight_checks(default_config(), EventBus())
    cs = next(r for r in results if r.name == "cache-safety")
    assert cs.status is GateStatus.WARN
    assert cs.can_override is True  # default_config gates.allow_override
    assert "zz-clean.hook" in cs.message
    # Not a hard FAIL — pre-flight as a whole still "passes".
    assert not gates.any_fail(results)
