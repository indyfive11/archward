"""Integration tests: quarantine wiring in update_aur.run_aur_update()."""

from __future__ import annotations

import time
from typing import Iterator
from unittest.mock import MagicMock, patch

import pytest

from archward.aur.quarantine import AurQuarantine, QuarantineEntry
from archward.config.defaults import default_config
from archward.events import EventBus
from archward.models.aur import AurResult
from archward.models.config import AurConfig, ConfigModel, PacmanConfig
from archward.pipeline.update_aur import run_aur_update


# ── test doubles ──────────────────────────────────────────────────────────────

class _FakeBus(EventBus):
    def __init__(self):
        super().__init__()
        self.logs: list[str] = []

    def emit_log(self, phase: str, msg: str) -> None:
        self.logs.append(msg)

    def emit_start(self, phase: str, label: str) -> None:
        pass

    def emit_result(self, phase: str, msg: str, *, payload=None) -> None:
        pass


class _FakeHelper:
    def __init__(self, pending=None, exit_code=0, output=None):
        self.name = "yay"
        self._pending = pending or []
        self._exit_code = exit_code
        self._output = output or []

    def list_pending(self):
        return self._pending

    def run_update(self, *, ignore, strategy, bus, cancel_event, noconfirm, prompt_provider):
        return self._exit_code, self._output


class _FakeStrategy:
    def argv_prefix(self): return []
    def env(self): return {}
    def warmup(self): pass


def _cfg_model(**aur_kwargs) -> ConfigModel:
    cfg = default_config()
    aur = cfg.aur.model_copy(update={
        "quarantine_enabled": True,
        "quarantine_min_failures": 3,
        "quarantine_initial_days": 7,
        "quarantine_max_days": 28,
        **aur_kwargs,
    })
    pacman = cfg.pacman.model_copy(update={"noconfirm": True})
    return cfg.model_copy(update={"aur": aur, "pacman": pacman})


# ── quarantine skip ───────────────────────────────────────────────────────────

def test_quarantined_package_skipped(tmp_path, monkeypatch):
    """A SKIP-action package must appear in effective_ignore, not be attempted."""
    cfg = _cfg_model()
    bus = _FakeBus()

    q = AurQuarantine(cfg.aur)
    _seed_quarantined(q, "radarr", "6.1.1", retry_after=time.time() + 86_400)

    with _patch_quarantine(q, tmp_path) as mock_q_cls:
        with _patch_helper([("radarr", "6.1.0", "6.1.1")], exit_code=0) as helper:
            result = run_aur_update(cfg, _FakeStrategy(), bus)

    # Package was skipped — no update ran (pending == quarantine_ignored, so early exit)
    assert result is not None
    assert any("quarantined" in log.lower() or "skipping" in log.lower() for log in bus.logs)


def test_quarantined_package_added_to_effective_ignore(tmp_path, monkeypatch):
    """When other non-quarantined packages are pending, quarantined pkg is in ignore list."""
    cfg = _cfg_model()
    bus = _FakeBus()

    q = AurQuarantine(cfg.aur)
    _seed_quarantined(q, "radarr", "6.1.1", retry_after=time.time() + 86_400)
    # gossip-bin is NOT quarantined — helper should run for it
    captured_ignores: list[list[str]] = []

    class _TrackingHelper(_FakeHelper):
        def run_update(self, *, ignore, **kw):
            captured_ignores.append(list(ignore or []))
            return 0, []

    with _patch_quarantine(q, tmp_path):
        with _patch_helper(
            [("radarr", "6.1.0", "6.1.1"), ("gossip-bin", "0.9.1", "0.9.2")],
            exit_code=0,
            helper_cls=_TrackingHelper,
        ):
            result = run_aur_update(cfg, _FakeStrategy(), bus)

    assert captured_ignores
    assert "radarr" in captured_ignores[0]
    assert "gossip-bin" not in captured_ignores[0]


def test_retry_window_package_not_in_ignore(tmp_path):
    """A package in its retry window (RETRY action) is attempted, not ignored."""
    cfg = _cfg_model()
    bus = _FakeBus()

    q = AurQuarantine(cfg.aur)
    _seed_quarantined(q, "radarr", "6.1.1", retry_after=time.time() - 1)  # window open
    captured_ignores: list[list[str]] = []

    class _TrackingHelper(_FakeHelper):
        def run_update(self, *, ignore, **kw):
            captured_ignores.append(list(ignore or []))
            return 0, []

    with _patch_quarantine(q, tmp_path):
        with _patch_helper([("radarr", "6.1.0", "6.1.1")], helper_cls=_TrackingHelper):
            result = run_aur_update(cfg, _FakeStrategy(), bus)

    assert captured_ignores
    assert "radarr" not in captured_ignores[0]


def test_new_version_clears_quarantine_and_retries(tmp_path):
    """When available version differs from quarantined version, quarantine is cleared."""
    cfg = _cfg_model()
    bus = _FakeBus()

    q = AurQuarantine(cfg.aur)
    _seed_quarantined(q, "radarr", "6.1.1", retry_after=time.time() + 86_400)
    captured_ignores: list[list[str]] = []

    class _TrackingHelper(_FakeHelper):
        def run_update(self, *, ignore, **kw):
            captured_ignores.append(list(ignore or []))
            return 0, []

    with _patch_quarantine(q, tmp_path):
        # New version 6.2.0 — should clear quarantine for 6.1.1
        with _patch_helper([("radarr", "6.1.1", "6.2.0")], helper_cls=_TrackingHelper):
            result = run_aur_update(cfg, _FakeStrategy(), bus)

    assert captured_ignores
    assert "radarr" not in captured_ignores[0]
    # Confirm the quarantine entry was resolved
    assert q.entry("radarr") is None or q.entry("radarr").status == "resolved"


# ── quarantine record ─────────────────────────────────────────────────────────

def test_failure_recorded_after_build_failure(tmp_path):
    """A build failure from the helper triggers quarantine.record_failure()."""
    cfg = _cfg_model()
    bus = _FakeBus()

    q = AurQuarantine(cfg.aur)
    output = [
        "==> Making package: radarr 6.1.1",
        "==> ERROR: A failure occurred in build().",
        "==> Build of radarr failed",
    ]

    with _patch_quarantine(q, tmp_path):
        with _patch_helper([("radarr", "6.1.0", "6.1.1")], exit_code=1, output=output):
            result = run_aur_update(cfg, _FakeStrategy(), bus)

    entry = q.entry("radarr")
    assert entry is not None
    assert entry.failure_count >= 1
    assert entry.version == "6.1.1"


def test_success_clears_quarantine_entry(tmp_path):
    """A successful build triggers quarantine.record_success() and clears the entry."""
    cfg = _cfg_model()
    bus = _FakeBus()

    q = AurQuarantine(cfg.aur)
    _seed_quarantined(q, "radarr", "6.1.1", retry_after=time.time() - 1)  # retry window open

    with _patch_quarantine(q, tmp_path):
        with _patch_helper([("radarr", "6.1.0", "6.1.1")], exit_code=0, output=[]):
            result = run_aur_update(cfg, _FakeStrategy(), bus)

    entry = q.entry("radarr")
    assert entry is not None
    assert entry.status == "resolved"


def test_quarantine_disabled_skips_all_logic(tmp_path):
    """When quarantine_enabled=False, no quarantine checks happen."""
    cfg = _cfg_model(quarantine_enabled=False)
    bus = _FakeBus()

    q = AurQuarantine(cfg.aur)
    _seed_quarantined(q, "radarr", "6.1.1", retry_after=time.time() + 86_400)
    captured_ignores: list[list[str]] = []

    class _TrackingHelper(_FakeHelper):
        def run_update(self, *, ignore, **kw):
            captured_ignores.append(list(ignore or []))
            return 0, []

    with _patch_quarantine(q, tmp_path):
        with _patch_helper([("radarr", "6.1.0", "6.1.1")], helper_cls=_TrackingHelper):
            result = run_aur_update(cfg, _FakeStrategy(), bus)

    assert captured_ignores
    assert "radarr" not in captured_ignores[0]


# ── helpers ───────────────────────────────────────────────────────────────────

def _seed_quarantined(q: AurQuarantine, pkg: str, version: str, retry_after: float) -> None:
    now = time.time()
    q._data[pkg] = QuarantineEntry(
        version=version,
        status="quarantined",
        first_failure_at=now - 7 * 86_400,
        last_failure_at=now - 86_400,
        failure_count=3,
        retry_after=retry_after,
        retry_interval_days=7,
        last_error="==> ERROR: build failed",
        resolved_at=None,
        resolved_reason=None,
    )


from contextlib import contextmanager


@contextmanager
def _patch_quarantine(q: AurQuarantine, tmp_path):
    """Inject `q` as the quarantine instance used by run_aur_update."""
    def _make_q(cfg):
        return q

    # Patch save/load so we don't touch state_dir and pre-seeded _data is preserved
    q.save = lambda: None
    q.load = lambda: None

    with patch("archward.pipeline.update_aur.AurQuarantine", side_effect=_make_q):
        yield q


@contextmanager
def _patch_helper(pending, exit_code=0, output=None, helper_cls=None):
    """Inject a fake AUR helper into update_aur.run_aur_update()."""
    output = output or []
    if helper_cls is None:
        helper_cls = _FakeHelper
    fake = helper_cls(pending=pending, exit_code=exit_code, output=output)

    with patch("archward.pipeline.update_aur.discover", return_value=fake):
        yield fake
