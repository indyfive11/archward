"""Tests for v0.3.3 verify-phase additions.

Layer 1: pure unit tests for _discover_plugin_checkers() with mocked
importlib_metadata.entry_points().

Layer 2: integration tests against run_verify() that mock plugin
discovery and assert on the plugin bucket subset of the resulting
VerifyResult. Built-in checks (kernel, pacnew, disk, pacman-log) are
allowed to run against the host because they're not under test here —
the assertions filter for bucket=='plugin' entries.

Layer 3: tests for the (A) stale-service WARN row and the (B) opt-in
inline auto-prune that mutates the config file when
services.auto_prune is True.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pytest

from archward.config.defaults import default_config
from archward.events import EventBus
from archward.models.snapshot import Snapshot, SnapshotMeta
from archward.models.verify import CheckStatus, VerifyCheck
from archward.pipeline import verify_phase


# ── helpers ──────────────────────────────────────────────────────────────


@dataclass
class _FakeEP:
    """Minimal stand-in for importlib.metadata.EntryPoint."""
    name: str
    target: object
    raises: Exception | None = None

    def load(self):
        if self.raises is not None:
            raise self.raises
        return self.target


def _patch_entry_points(monkeypatch, eps_list):
    """Patch verify_phase.importlib_metadata.entry_points to return eps_list."""
    def fake(group=None):
        assert group == verify_phase.PLUGIN_ENTRY_POINT_GROUP
        return eps_list
    monkeypatch.setattr(verify_phase.importlib_metadata, "entry_points", fake)


def _make_snapshot(tmp_path: Path) -> Snapshot:
    return Snapshot(
        meta=SnapshotMeta(
            snapshot_id="test",
            created_at=datetime(2026, 5, 14),
            path=tmp_path,
            distro_id="endeavouros",
            kernel_release="6.0.0-test",
            free_disk_gb=100,
        ),
        package_files={},
        config_files=(),
        service_files={},
        age_seconds=0,
    )


def _plugin_checks(result) -> list[VerifyCheck]:
    return [c for c in result.checks if c.bucket == "plugin"]


# ── Layer 1: _discover_plugin_checkers ───────────────────────────────────


class TestDiscoverPluginCheckers:
    def test_returns_loaded_callables(self, monkeypatch):
        def plugin_a(cfg, snapshot):
            return []
        def plugin_b(cfg, snapshot):
            return []
        _patch_entry_points(monkeypatch, [
            _FakeEP("a", plugin_a),
            _FakeEP("b", plugin_b),
        ])
        result = verify_phase._discover_plugin_checkers()
        assert [name for name, _ in result] == ["a", "b"]
        assert all(callable(fn) for _, fn in result)

    def test_skips_entry_points_that_fail_to_load(self, monkeypatch):
        def good(cfg, snapshot):
            return []
        _patch_entry_points(monkeypatch, [
            _FakeEP("broken", None, raises=ImportError("boom")),
            _FakeEP("good", good),
        ])
        result = verify_phase._discover_plugin_checkers()
        assert [name for name, _ in result] == ["good"]

    def test_skips_non_callable_entry_points(self, monkeypatch):
        _patch_entry_points(monkeypatch, [
            _FakeEP("not-callable", "this is a string, not a fn"),
        ])
        result = verify_phase._discover_plugin_checkers()
        assert result == []

    def test_no_entry_points_returns_empty(self, monkeypatch):
        _patch_entry_points(monkeypatch, [])
        assert verify_phase._discover_plugin_checkers() == []


# ── Layer 2: run_verify integration ──────────────────────────────────────


class TestRunVerifyPluginIntegration:
    def test_plugin_check_appears_in_result(self, monkeypatch, tmp_path):
        def my_plugin(cfg, snapshot):
            return [VerifyCheck(
                bucket="plugin",
                name="hello",
                status=CheckStatus.PASS,
                message="hello from plugin",
            )]
        monkeypatch.setattr(
            verify_phase, "_discover_plugin_checkers",
            lambda: [("my-plugin", my_plugin)],
        )
        cfg = default_config()
        result = verify_phase.run_verify(cfg, _make_snapshot(tmp_path), EventBus())
        plugin_checks = _plugin_checks(result)
        assert len(plugin_checks) == 1
        assert plugin_checks[0].name == "hello"
        assert plugin_checks[0].status is CheckStatus.PASS

    def test_plugin_raising_becomes_synthetic_fail(self, monkeypatch, tmp_path):
        def boom(cfg, snapshot):
            raise RuntimeError("intentional test failure")
        def healthy(cfg, snapshot):
            return [VerifyCheck(
                bucket="plugin",
                name="healthy",
                status=CheckStatus.PASS,
                message="ok",
            )]
        monkeypatch.setattr(
            verify_phase, "_discover_plugin_checkers",
            lambda: [("boom", boom), ("healthy", healthy)],
        )
        cfg = default_config()
        result = verify_phase.run_verify(cfg, _make_snapshot(tmp_path), EventBus())
        plugin_checks = _plugin_checks(result)
        # 1 synthetic FAIL for boom + 1 PASS for healthy
        assert len(plugin_checks) == 2
        boom_check = next(c for c in plugin_checks if "boom" in c.name)
        assert boom_check.status is CheckStatus.FAIL
        assert "RuntimeError" in boom_check.message
        assert "intentional test failure" in boom_check.message
        # The other plugin still ran
        assert any(c.name == "healthy" and c.status is CheckStatus.PASS for c in plugin_checks)

    def test_plugin_yielding_non_VerifyCheck_becomes_synthetic_fail(self, monkeypatch, tmp_path):
        def bad(cfg, snapshot):
            return ["this is not a VerifyCheck"]
        monkeypatch.setattr(
            verify_phase, "_discover_plugin_checkers",
            lambda: [("bad", bad)],
        )
        cfg = default_config()
        result = verify_phase.run_verify(cfg, _make_snapshot(tmp_path), EventBus())
        plugin_checks = _plugin_checks(result)
        assert len(plugin_checks) == 1
        assert plugin_checks[0].status is CheckStatus.FAIL
        assert "non-VerifyCheck" in plugin_checks[0].message
        assert "str" in plugin_checks[0].message

    def test_no_plugins_means_no_plugin_bucket_entries(self, monkeypatch, tmp_path):
        monkeypatch.setattr(verify_phase, "_discover_plugin_checkers", lambda: [])
        cfg = default_config()
        result = verify_phase.run_verify(cfg, _make_snapshot(tmp_path), EventBus())
        assert _plugin_checks(result) == []


# ── Layer 3: stale-service WARN + auto-prune ─────────────────────────────


def _service_checks(result):
    return [c for c in result.checks if c.bucket == "services"]


def _cfg_with_services(*units, auto_prune=False):
    """Build a default config with services.to_verify pre-populated."""
    from archward.config.loader import merge_partial
    from archward.models.config import ServicesConfig

    cfg = default_config()
    return merge_partial(
        cfg,
        services=ServicesConfig(
            to_verify=tuple(units),
            severity=dict(cfg.services.severity),
            auto_prune=auto_prune,
        ),
    )


class TestStaleServiceWARN:
    """Case (A): _service_check surfaces a stale unit as WARN with a
    'no such unit' message — not the generic FAIL/'not active' that masks
    the difference between 'gone' and 'down'."""

    def test_stale_unit_returns_warn_with_marker(self, monkeypatch, tmp_path):
        # Plugins off; only the service check is being exercised.
        monkeypatch.setattr(verify_phase, "_discover_plugin_checkers", lambda: [])
        # Unit doesn't exist → unit_exists False.
        monkeypatch.setattr(verify_phase.services, "unit_exists", lambda u: False)
        # is_active should never be reached, but stub it to make the test deterministic.
        monkeypatch.setattr(verify_phase.services, "is_active", lambda u: False)

        cfg = _cfg_with_services("ghost.service")
        result = verify_phase.run_verify(cfg, _make_snapshot(tmp_path), EventBus())
        svc = [c for c in _service_checks(result) if c.name == "ghost.service"]
        assert len(svc) == 1
        assert svc[0].status is CheckStatus.WARN
        assert verify_phase._STALE_MARKER in svc[0].message
        assert "archward --detect" in svc[0].message

    def test_existing_inactive_unit_still_uses_severity(self, monkeypatch, tmp_path):
        """A unit that exists but is inactive should keep today's FAIL/WARN
        severity behavior — (A) only changes the gone-unit case."""
        monkeypatch.setattr(verify_phase, "_discover_plugin_checkers", lambda: [])
        monkeypatch.setattr(verify_phase.services, "unit_exists", lambda u: True)
        monkeypatch.setattr(verify_phase.services, "is_active", lambda u: False)

        cfg = _cfg_with_services("down.service")
        result = verify_phase.run_verify(cfg, _make_snapshot(tmp_path), EventBus())
        svc = [c for c in _service_checks(result) if c.name == "down.service"]
        assert len(svc) == 1
        # Default severity is 'critical' → FAIL with "not active".
        assert svc[0].status is CheckStatus.FAIL
        assert svc[0].message == "not active"


class TestInlineAutoPrune:
    """Case (B): when cfg.services.auto_prune is True and config_path is
    provided, run_verify silently drops stale entries from to_verify,
    writes the pruned config to disk, and emits a single summary PASS
    row recording what was removed."""

    def test_auto_prune_disabled_no_mutation(self, monkeypatch, tmp_path):
        monkeypatch.setattr(verify_phase, "_discover_plugin_checkers", lambda: [])
        monkeypatch.setattr(verify_phase.services, "unit_exists", lambda u: u != "ghost.service")
        monkeypatch.setattr(verify_phase.services, "is_active", lambda u: True)

        cfg = _cfg_with_services("good.service", "ghost.service", auto_prune=False)
        config_path = tmp_path / "config.toml"
        result = verify_phase.run_verify(
            cfg, _make_snapshot(tmp_path), EventBus(), config_path=config_path,
        )
        # No mutation file created (auto_prune off).
        assert not config_path.exists()
        # No auto-prune summary check.
        assert not any(c.name == "auto-prune" for c in result.checks)
        # Stale unit still shows up as a WARN per case (A).
        svc = [c for c in _service_checks(result) if c.name == "ghost.service"]
        assert svc and svc[0].status is CheckStatus.WARN

    def test_auto_prune_no_path_no_write(self, monkeypatch, tmp_path):
        """auto_prune=True but config_path=None (e.g. test harness): no write,
        no summary check; stale entries surface as WARN."""
        monkeypatch.setattr(verify_phase, "_discover_plugin_checkers", lambda: [])
        monkeypatch.setattr(verify_phase.services, "unit_exists", lambda u: u != "ghost.service")
        monkeypatch.setattr(verify_phase.services, "is_active", lambda u: True)

        cfg = _cfg_with_services("good.service", "ghost.service", auto_prune=True)
        result = verify_phase.run_verify(cfg, _make_snapshot(tmp_path), EventBus(), config_path=None)
        assert not any(c.name == "auto-prune" for c in result.checks)
        # ghost still surfaces as WARN per (A).
        assert any(
            c.name == "ghost.service" and c.status is CheckStatus.WARN
            for c in _service_checks(result)
        )

    def test_auto_prune_writes_and_summarizes(self, monkeypatch, tmp_path):
        """auto_prune=True + config_path provided: ghost.service is dropped
        from the written file, summary row emitted, and the per-unit WARN
        for the pruned name does not appear (since pruning happens before
        the service-check loop)."""
        monkeypatch.setattr(verify_phase, "_discover_plugin_checkers", lambda: [])
        monkeypatch.setattr(verify_phase.services, "unit_exists", lambda u: u != "ghost.service")
        monkeypatch.setattr(verify_phase.services, "is_active", lambda u: True)

        cfg = _cfg_with_services("good.service", "ghost.service", auto_prune=True)
        config_path = tmp_path / "config.toml"
        result = verify_phase.run_verify(
            cfg, _make_snapshot(tmp_path), EventBus(), config_path=config_path,
        )
        # 1. Config written.
        assert config_path.exists()
        # 2. Summary check present, recording what was removed.
        prune = [c for c in result.checks if c.name == "auto-prune"]
        assert len(prune) == 1
        assert prune[0].status is CheckStatus.PASS
        assert "1 stale" in prune[0].message
        assert prune[0].detail == "ghost.service"
        # 3. ghost.service is NOT in the post-prune service-check loop.
        assert not any(c.name == "ghost.service" for c in _service_checks(result))
        # 4. good.service still gets its normal PASS.
        assert any(
            c.name == "good.service" and c.status is CheckStatus.PASS
            for c in _service_checks(result)
        )
        # 5. Reload from disk to confirm the persisted file has ghost removed.
        from archward.config.loader import load_config
        reloaded = load_config(config_path)
        assert "ghost.service" not in reloaded.services.to_verify
        assert "good.service" in reloaded.services.to_verify
        assert reloaded.services.auto_prune is True  # setting itself survives


# ── Layer 4: v0.4.1 F4 — per-plugin timeout ──────────────────────────────


class TestPluginTimeout:
    """A misbehaving plugin (hangs forever) must NOT freeze verify.

    The timeout wrapper around the plugin call returns a synthetic FAIL
    check identifying the timeout, mirroring the existing exception-
    isolation pattern.
    """

    def test_plugin_that_hangs_yields_synthetic_fail(self, monkeypatch, tmp_path):
        """Hard-hang plugin times out within PLUGIN_TIMEOUT_S (here patched to 0.5s)."""
        import threading
        import time

        proceed = threading.Event()

        def hanging_plugin(cfg, snapshot):
            # Hold here past the timeout window. The executor's daemon
            # thread won't terminate, but the timeout fires and verify
            # moves on.
            proceed.wait(timeout=5)
            return []

        monkeypatch.setattr(verify_phase, "PLUGIN_TIMEOUT_S", 0.5)
        monkeypatch.setattr(
            verify_phase, "_discover_plugin_checkers",
            lambda: [("hanger", hanging_plugin)],
        )
        cfg = default_config()
        t0 = time.monotonic()
        result = verify_phase.run_verify(cfg, _make_snapshot(tmp_path), EventBus())
        elapsed = time.monotonic() - t0
        # Should NOT have taken 5+ seconds.
        assert elapsed < 3.0, f"verify hung for {elapsed:.1f}s — timeout not honored"

        plugin_checks = _plugin_checks(result)
        assert len(plugin_checks) == 1
        c = plugin_checks[0]
        assert c.status is CheckStatus.FAIL
        assert "timed out" in c.message

        # Unblock the dangling thread so we don't leak it across tests.
        proceed.set()

    def test_plugin_that_returns_quickly_runs_normally(self, monkeypatch, tmp_path):
        """The timeout wrapper is invisible to well-behaved plugins."""
        def fast_plugin(cfg, snapshot):
            return [VerifyCheck(
                bucket="plugin",
                name="fast",
                status=CheckStatus.PASS,
                message="ok",
            )]
        monkeypatch.setattr(
            verify_phase, "_discover_plugin_checkers",
            lambda: [("fast", fast_plugin)],
        )
        cfg = default_config()
        result = verify_phase.run_verify(cfg, _make_snapshot(tmp_path), EventBus())
        plugin_checks = _plugin_checks(result)
        assert len(plugin_checks) == 1
        assert plugin_checks[0].status is CheckStatus.PASS
