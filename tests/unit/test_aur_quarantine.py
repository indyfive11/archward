"""AUR build quarantine — unit tests for core quarantine logic."""

from __future__ import annotations

import json
import time

import pytest

from archward.aur.quarantine import (
    AurQuarantine,
    QuarantineAction,
    _classify_error,
)
from archward.models.config import AurConfig


# ── fixtures ──────────────────────────────────────────────────────────────────

def _cfg(**kwargs) -> AurConfig:
    defaults = dict(
        enabled=True,
        quarantine_enabled=True,
        quarantine_min_failures=3,
        quarantine_initial_days=7,
        quarantine_max_days=28,
    )
    defaults.update(kwargs)
    return AurConfig(**defaults)


def _q(cfg: AurConfig | None = None) -> AurQuarantine:
    q = AurQuarantine(cfg or _cfg())
    return q


# ── check() ───────────────────────────────────────────────────────────────────

def test_not_quarantined_on_empty_state() -> None:
    q = _q()
    action, entry = q.check("radarr", "6.1.1")
    assert action is QuarantineAction.FRESH
    assert entry is None


def test_disabled_always_returns_fresh() -> None:
    q = _q(_cfg(quarantine_enabled=False))
    q._data["radarr"] = _make_quarantined_entry("radarr")
    action, _ = q.check("radarr", "6.1.1")
    assert action is QuarantineAction.FRESH


def test_resolved_entry_returns_fresh() -> None:
    q = _q()
    entry = _make_quarantined_entry("6.1.1")
    entry.status = "resolved"
    q._data["radarr"] = entry
    action, _ = q.check("radarr", "6.1.1")
    assert action is QuarantineAction.FRESH


def test_counting_entry_returns_counting() -> None:
    q = _q()
    entry = _make_counting_entry("6.1.1")
    q._data["radarr"] = entry
    action, ret = q.check("radarr", "6.1.1")
    assert action is QuarantineAction.COUNTING
    assert ret is entry


def test_quarantined_before_retry_returns_skip() -> None:
    q = _q()
    entry = _make_quarantined_entry("6.1.1", retry_after=time.time() + 86_400)
    q._data["radarr"] = entry
    action, _ = q.check("radarr", "6.1.1")
    assert action is QuarantineAction.SKIP


def test_quarantined_after_retry_window_returns_retry() -> None:
    q = _q()
    entry = _make_quarantined_entry("6.1.1", retry_after=time.time() - 1)
    q._data["radarr"] = entry
    action, _ = q.check("radarr", "6.1.1")
    assert action is QuarantineAction.RETRY


def test_version_change_clears_quarantine_and_returns_fresh() -> None:
    q = _q()
    entry = _make_quarantined_entry("6.1.1", retry_after=time.time() + 86_400)
    q._data["radarr"] = entry
    action, resolved = q.check("radarr", "6.2.0")
    assert action is QuarantineAction.FRESH
    assert resolved is not None
    assert resolved.status == "resolved"
    assert resolved.resolved_reason == "new_version"
    # The state entry is now the resolved one
    assert q._data["radarr"].status == "resolved"


# ── record_failure() ──────────────────────────────────────────────────────────

def test_first_failure_creates_counting_entry() -> None:
    q = _q()
    activated = q.record_failure("radarr", "6.1.1", ("==> ERROR: build failed",))
    assert not activated
    entry = q.entry("radarr")
    assert entry is not None
    assert entry.status == "counting"
    assert entry.failure_count == 1


def test_failure_not_counted_within_24h() -> None:
    q = _q()
    q.record_failure("radarr", "6.1.1", ("==> ERROR: build failed",))
    # Second call within 24h — should not increment
    activated = q.record_failure("radarr", "6.1.1", ("==> ERROR: build failed again",))
    assert not activated
    assert q.entry("radarr").failure_count == 1


def test_failure_counted_after_24h(monkeypatch) -> None:
    q = _q()
    q.record_failure("radarr", "6.1.1", ("==> ERROR: build failed",))
    # Simulate 25h passage by backdating last_failure_at
    entry = q.entry("radarr")
    entry.last_failure_at -= 90_000  # 25h ago
    q.record_failure("radarr", "6.1.1", ("==> ERROR: build failed",))
    assert q.entry("radarr").failure_count == 2


def test_quarantine_activates_at_threshold() -> None:
    q = _q(_cfg(quarantine_min_failures=2))
    q.record_failure("radarr", "6.1.1", ("==> ERROR:",))
    entry = q.entry("radarr")
    entry.last_failure_at -= 90_000
    activated = q.record_failure("radarr", "6.1.1", ("==> ERROR:",))
    assert activated
    assert q.entry("radarr").status == "quarantined"
    assert q.entry("radarr").retry_after is not None


def test_not_quarantined_below_threshold() -> None:
    q = _q(_cfg(quarantine_min_failures=3))
    q.record_failure("radarr", "6.1.1", ("==> ERROR:",))
    entry = q.entry("radarr")
    entry.last_failure_at -= 90_000
    activated = q.record_failure("radarr", "6.1.1", ("==> ERROR:",))
    assert not activated
    assert q.entry("radarr").status == "counting"
    assert q.entry("radarr").failure_count == 2


def test_retry_failure_escalates_backoff() -> None:
    q = _q(_cfg(quarantine_min_failures=1, quarantine_initial_days=7, quarantine_max_days=28))
    # Call 1: creates counting entry (failure_count=1)
    q.record_failure("radarr", "6.1.1", ("==> ERROR:",))
    # Call 2 (24h+ later): activates quarantine (failure_count=2 >= min_failures=1)
    q.entry("radarr").last_failure_at -= 90_000
    q.record_failure("radarr", "6.1.1", ("==> ERROR:",))
    assert q.entry("radarr").status == "quarantined"
    assert q.entry("radarr").retry_interval_days == 7
    # Call 3 (24h+ later): retry failure → escalate backoff 7→14
    q.entry("radarr").last_failure_at -= 90_000
    q.record_failure("radarr", "6.1.1", ("==> ERROR:",))
    assert q.entry("radarr").retry_interval_days == 14


def test_escalation_caps_at_max_days() -> None:
    q = _q(_cfg(quarantine_min_failures=1, quarantine_initial_days=7, quarantine_max_days=14))
    q.record_failure("radarr", "6.1.1", ("==> ERROR:",))
    # Two retry failures
    for _ in range(2):
        entry = q.entry("radarr")
        entry.last_failure_at -= 90_000
        q.record_failure("radarr", "6.1.1", ("==> ERROR:",))
    assert q.entry("radarr").retry_interval_days == 14  # capped


def test_new_version_after_quarantine_starts_fresh() -> None:
    q = _q(_cfg(quarantine_min_failures=1))
    q.record_failure("radarr", "6.1.1", ("==> ERROR:",))
    # Now record failure for new version — should start fresh counting entry
    entry = q.entry("radarr")
    entry.last_failure_at -= 90_000
    q.record_failure("radarr", "6.2.0", ("==> ERROR:",))
    new_entry = q.entry("radarr")
    assert new_entry.version == "6.2.0"
    assert new_entry.status == "counting"
    assert new_entry.failure_count == 1


# ── record_success() ─────────────────────────────────────────────────────────

def test_success_clears_quarantine_entry() -> None:
    q = _q()
    q._data["radarr"] = _make_quarantined_entry("6.1.1")
    q.record_success("radarr")
    assert q.entry("radarr").status == "resolved"
    assert q.entry("radarr").resolved_reason == "retry_succeeded"


def test_success_on_unknown_package_is_noop() -> None:
    q = _q()
    q.record_success("not-in-state")  # must not raise


# ── clear() ───────────────────────────────────────────────────────────────────

def test_clear_specific_package() -> None:
    q = _q()
    q._data["radarr"] = _make_quarantined_entry("6.1.1")
    q._data["gossip-bin"] = _make_counting_entry("0.9.2")
    count = q.clear("radarr")
    assert count == 1
    assert q.entry("radarr").status == "resolved"
    assert q.entry("gossip-bin").status == "counting"


def test_clear_all_active() -> None:
    q = _q()
    q._data["radarr"] = _make_quarantined_entry("6.1.1")
    q._data["gossip-bin"] = _make_counting_entry("0.9.2")
    count = q.clear()
    assert count == 2
    assert q.entry("radarr").status == "resolved"
    assert q.entry("gossip-bin").status == "resolved"


def test_clear_does_not_touch_already_resolved() -> None:
    q = _q()
    resolved = _make_quarantined_entry("6.1.1")
    resolved.status = "resolved"
    q._data["radarr"] = resolved
    count = q.clear()
    assert count == 0


# ── save / load ───────────────────────────────────────────────────────────────

def test_save_and_load_roundtrip(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("archward.aur.quarantine._state_path", lambda: tmp_path / "q.json")
    q = _q()
    q._data["radarr"] = _make_quarantined_entry("6.1.1", retry_after=time.time() + 86_400)
    q.save()

    q2 = _q()
    q2._data  # empty
    monkeypatch.setattr("archward.aur.quarantine._state_path", lambda: tmp_path / "q.json")
    q2.load()
    assert "radarr" in q2._data
    assert q2._data["radarr"].status == "quarantined"
    assert q2._data["radarr"].failure_count == q._data["radarr"].failure_count


def test_corrupt_json_handled_gracefully(tmp_path, monkeypatch) -> None:
    bad = tmp_path / "q.json"
    bad.write_text("{corrupt json", encoding="utf-8")
    monkeypatch.setattr("archward.aur.quarantine._state_path", lambda: bad)
    q = _q()
    q.load()  # must not raise
    assert q._data == {}


# ── _classify_error() ─────────────────────────────────────────────────────────

def test_classify_dotnet_nuget() -> None:
    lines = (
        "  Determining projects to restore...",
        "error NU1902: Package 'MailKit' 4.15.1 has a known moderate severity vulnerability",
        "==> ERROR: A failure occurred in build().",
    )
    hint = _classify_error(lines)
    assert hint is not None
    assert "NuGet" in hint or "dotnet" in hint.lower() or "Upstream" in hint


def test_classify_checksum_mismatch() -> None:
    lines = ("==> Validating source files with sha256sums...", "sha256sums FAILED")
    hint = _classify_error(lines)
    assert hint is not None
    assert "checksum" in hint.lower() or "sha256" in hint.lower() or "mismatch" in hint.lower() or "PKGBUILD" in hint


def test_classify_unknown_returns_none() -> None:
    lines = ("something completely different", "no known error pattern here")
    assert _classify_error(lines) is None


def test_classify_disabled_state_is_fresh() -> None:
    q = _q(_cfg(quarantine_enabled=False))
    # record_failure should return False and not create entry
    activated = q.record_failure("radarr", "6.1.1", ("error",))
    assert not activated
    assert q.entry("radarr") is None


# ── active_entries() ─────────────────────────────────────────────────────────

def test_active_entries_excludes_resolved() -> None:
    q = _q()
    q._data["radarr"] = _make_quarantined_entry("6.1.1")
    resolved = _make_counting_entry("0.9.2")
    resolved.status = "resolved"
    q._data["gossip-bin"] = resolved
    active = q.active_entries()
    pkgs = [p for p, _ in active]
    assert "radarr" in pkgs
    assert "gossip-bin" not in pkgs


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_quarantined_entry(version: str, retry_after: float | None = None):
    from archward.aur.quarantine import QuarantineEntry
    now = time.time()
    return QuarantineEntry(
        version=version,
        status="quarantined",
        first_failure_at=now - 7 * 86_400,
        last_failure_at=now - 86_400,
        failure_count=3,
        retry_after=retry_after if retry_after is not None else now + 7 * 86_400,
        retry_interval_days=7,
        last_error="==> ERROR: build failed",
        resolved_at=None,
        resolved_reason=None,
    )


def _make_counting_entry(version: str):
    from archward.aur.quarantine import QuarantineEntry
    now = time.time()
    return QuarantineEntry(
        version=version,
        status="counting",
        first_failure_at=now - 2 * 86_400,
        last_failure_at=now - 86_400,
        failure_count=2,
        retry_after=None,
        retry_interval_days=7,
        last_error="sha256sums FAILED",
        resolved_at=None,
        resolved_reason=None,
    )
