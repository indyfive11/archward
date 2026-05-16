"""Tests for preflight gate checks.

Covers:
- v0.4.1 F13 — stale-lock UX clarity
- v0.4.4 F2 — cache-safety WARN
- v0.4.5 F1 — Arch News pre-flight WARN
"""

from __future__ import annotations

from pathlib import Path

import pytest

from archward.config.defaults import default_config
from archward.events import EventBus
from archward.models.gate import GateStatus
from archward.pipeline import gates
from archward.system import arch_news as an
from archward.system import cache_policy as cp

# ── shared stubs ──────────────────────────────────────────────────────


def _balanced_policy():
    return cp.CachePolicy(
        timer_state="enabled",
        paccache_args="-rk3",
        effective_keep=3,
        clean_method=("KeepInstalled",),
        cleaning_hooks=(),
        cache_size_bytes=0,
        cache_file_count=0,
        safety=cp.RollbackSafety.BALANCED,
        explanation="balanced",
    )


@pytest.fixture(autouse=True)
def _stub_external(monkeypatch):
    """Keep all preflight tests hermetic: stub cache policy + news fetch."""
    monkeypatch.setattr(gates.cp, "detect_cache_policy", _balanced_policy)
    # News fetch returns [] by default (no items → PASS).
    monkeypatch.setattr(gates.an, "fetch_news", lambda: [])
    # No snapshots → uses first_run_since() window; with no items it's PASS.
    monkeypatch.setattr(gates, "latest_snapshot", lambda d: None)


# ── lock tests ────────────────────────────────────────────────────────


def test_preflight_unlocked_db(monkeypatch) -> None:
    """Sanity check the happy path still passes."""
    monkeypatch.setattr(gates, "check_pacman_db_lock", lambda: (False, None))
    results = gates.preflight_checks(default_config(), EventBus())
    assert all(r.status is GateStatus.PASS for r in results)
    assert any(r.name == "cache-safety" for r in results)
    assert any(r.name == "arch-news" for r in results)


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


# ── arch-news tests ───────────────────────────────────────────────────


def _news_item(title: str, days_ago: int) -> an.NewsItem:
    from datetime import datetime, timedelta, timezone
    pub = datetime.now(tz=timezone.utc) - timedelta(days=days_ago)
    return an.NewsItem(title=title, link=f"http://x/{title}", published=pub)


def test_preflight_no_news_is_pass(monkeypatch) -> None:
    """Zero unread items → arch-news PASS."""
    monkeypatch.setattr(gates, "check_pacman_db_lock", lambda: (False, None))
    monkeypatch.setattr(gates.an, "fetch_news", lambda: [])
    results = gates.preflight_checks(default_config(), EventBus())
    news = next(r for r in results if r.name == "arch-news")
    assert news.status is GateStatus.PASS


def test_preflight_unread_news_is_warn(monkeypatch, tmp_path) -> None:
    """Unread Arch News items → overridable WARN with titles in detail."""
    monkeypatch.setattr(gates, "check_pacman_db_lock", lambda: (False, None))
    items = [_news_item("Big breakage announcement", 5)]
    monkeypatch.setattr(gates.an, "fetch_news", lambda: items)
    # Snapshot was 10 days ago → the 5-day-old item is unread.
    from datetime import datetime, timedelta, timezone
    snap_time = datetime.now(tz=timezone.utc) - timedelta(days=10)
    monkeypatch.setattr(gates, "latest_snapshot", lambda d: (tmp_path, 864000))
    monkeypatch.setattr(gates.an, "since_from_snapshot", lambda p: snap_time)

    results = gates.preflight_checks(default_config(), EventBus())
    news = next(r for r in results if r.name == "arch-news")
    assert news.status is GateStatus.WARN
    assert news.can_override is True
    assert "Big breakage announcement" in news.detail
    assert "1 Arch News item" in news.message


def test_preflight_news_multiple_items_plural(monkeypatch, tmp_path) -> None:
    """Two unread items → plural wording in message."""
    monkeypatch.setattr(gates, "check_pacman_db_lock", lambda: (False, None))
    items = [_news_item("Item A", 3), _news_item("Item B", 2)]
    monkeypatch.setattr(gates.an, "fetch_news", lambda: items)
    from datetime import datetime, timedelta, timezone
    snap_time = datetime.now(tz=timezone.utc) - timedelta(days=10)
    monkeypatch.setattr(gates, "latest_snapshot", lambda d: (tmp_path, 864000))
    monkeypatch.setattr(gates.an, "since_from_snapshot", lambda p: snap_time)

    results = gates.preflight_checks(default_config(), EventBus())
    news = next(r for r in results if r.name == "arch-news")
    assert "2 Arch News items" in news.message


def test_preflight_network_error_skips_news_check(monkeypatch) -> None:
    """fetch_news() returning [] (offline) → no arch-news WARN, no crash."""
    monkeypatch.setattr(gates, "check_pacman_db_lock", lambda: (False, None))
    monkeypatch.setattr(gates.an, "fetch_news", lambda: [])
    results = gates.preflight_checks(default_config(), EventBus())
    # Should still have an arch-news PASS result (0 unread), no crash.
    news = next((r for r in results if r.name == "arch-news"), None)
    assert news is not None
    assert news.status is GateStatus.PASS


def test_preflight_skip_news_check_config(monkeypatch) -> None:
    """skip_news_check=True → no arch-news result at all."""
    monkeypatch.setattr(gates, "check_pacman_db_lock", lambda: (False, None))
    cfg = default_config()
    from archward.models.config import GatesConfig
    new_gates = GatesConfig(
        snapshot_max_age_minutes=cfg.gates.snapshot_max_age_minutes,
        min_disk_gb=cfg.gates.min_disk_gb,
        allow_override=cfg.gates.allow_override,
        skip_news_check=True,
    )
    cfg = cfg.model_copy(update={"gates": new_gates})
    results = gates.preflight_checks(cfg, EventBus())
    assert not any(r.name == "arch-news" for r in results)
