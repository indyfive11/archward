"""Tests for v0.4.1 F8 — partial snapshot cleanup on failure.

Regression: if step 3 of 6 (network gather) raised, the snap_root dir
was left half-populated with packages + configs but no `.timestamp`
marker. Retention couldn't prune it, so disk leaked. Snapshot is now
all-or-nothing — on any gather failure the partial dir is removed
before the exception propagates.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from archward.config.defaults import default_config
from archward.events import EventBus
from archward.pipeline import snapshot as snap_mod


def test_partial_failure_cleans_up_snapshot_dir(tmp_path, monkeypatch) -> None:
    """If a gather step raises, snap_root is removed before re-raise."""
    cfg = default_config()
    new_general = cfg.general.model_copy(update={"snapshot_dir": tmp_path / "snapshots"})
    cfg = cfg.model_copy(update={"general": new_general})

    def boom(*args, **kwargs):
        raise RuntimeError("simulated step-3 failure")

    # Mock the network gather to blow up.
    monkeypatch.setattr(snap_mod, "_gather_network", boom)
    # Mock the earlier (working) gathers so the snapshot dir gets some
    # content before the failure point.
    monkeypatch.setattr(snap_mod, "_gather_packages", lambda *a, **k: {})
    monkeypatch.setattr(snap_mod, "_gather_configs", lambda *a, **k: [])

    bus = EventBus()
    with pytest.raises(RuntimeError, match="simulated step-3 failure"):
        snap_mod.take_snapshot(cfg, MagicMock(), bus)

    # The snapshot_dir parent exists; the per-run subdir is gone.
    assert (tmp_path / "snapshots").exists()
    children = list((tmp_path / "snapshots").iterdir())
    assert children == [], f"orphan snapshot dirs left behind: {children}"


def test_successful_snapshot_keeps_dir(tmp_path, monkeypatch) -> None:
    """Happy path: gathers all succeed → snap_root has .timestamp + is preserved."""
    cfg = default_config()
    new_general = cfg.general.model_copy(update={"snapshot_dir": tmp_path / "snapshots"})
    cfg = cfg.model_copy(update={"general": new_general})

    # Mock all gathers as no-ops returning empty.
    monkeypatch.setattr(snap_mod, "_gather_packages", lambda *a, **k: {})
    monkeypatch.setattr(snap_mod, "_gather_configs", lambda *a, **k: [])
    monkeypatch.setattr(snap_mod, "_gather_network", lambda *a, **k: None)
    monkeypatch.setattr(snap_mod, "_gather_services", lambda *a, **k: {})
    monkeypatch.setattr(snap_mod, "_gather_system", lambda *a, **k: None)
    monkeypatch.setattr(snap_mod, "_capture_pacnew_baseline", lambda *a, **k: None)

    bus = EventBus()
    result = snap_mod.take_snapshot(cfg, MagicMock(), bus)

    assert result.meta.path.exists()
    assert (result.meta.path / ".timestamp").exists()
