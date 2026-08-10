"""run_pipeline takes the single-instance lock itself (v0.4.17).

Previously only the CLI wrapped run_pipeline in acquire_lock(); a GUI run
could race a concurrent CLI update. Now the lock lives inside run_pipeline;
the CLI opts out (acquire_instance_lock=False) because its wrapper already
holds it and preserves the historical exit-code-3 behavior.
"""

from __future__ import annotations

import fcntl
import threading
from contextlib import contextmanager

import pytest

import archward.config.paths as paths_mod
from archward.config.defaults import default_config
from archward.events import EventBus
from archward.pipeline import pipeline as pl


@pytest.fixture(autouse=True)
def _tmp_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(paths_mod, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(paths_mod, "lock_file", lambda: tmp_path / "archward.lock")


class _Strategy:
    def argv_prefix(self):
        return []

    def env(self):
        return {}

    def warmup(self):
        return True


@contextmanager
def _hold_lock(tmp_path):
    """Simulate another instance holding the flock."""
    lock_path = tmp_path / "archward.lock"
    fd = open(lock_path, "w", encoding="utf-8")
    fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        yield
    finally:
        fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
        fd.close()


def _stub_snapshot(monkeypatch, calls: list[str]):
    def _take(cfg, strategy, bus, **kw):
        calls.append("snapshot")
        raise RuntimeError("stop here — lock test only cares about entry")

    monkeypatch.setattr(pl.gates_phase, "preflight_checks", lambda cfg, bus: [])
    monkeypatch.setattr(pl.snapshot_phase, "take_snapshot", _take)


def test_lock_contention_aborts_before_any_phase(tmp_path, monkeypatch):
    calls: list[str] = []
    _stub_snapshot(monkeypatch, calls)

    with _hold_lock(tmp_path):
        result = pl.run_pipeline(
            default_config(), _Strategy(), EventBus(), pl.Mode.DRY_RUN
        )

    assert calls == []
    assert result.preflight_failed is True
    assert result.aborted_reason == "another archward instance is running"
    assert result.summary is not None
    assert result.summary.tag == "RESULT:UPDATE_FAILED"


def test_acquire_instance_lock_false_skips_locking(tmp_path, monkeypatch):
    """CLI path: lock already held by the caller — pipeline must still enter."""
    calls: list[str] = []
    _stub_snapshot(monkeypatch, calls)

    with _hold_lock(tmp_path):
        with pytest.raises(RuntimeError, match="stop here"):
            pl.run_pipeline(
                default_config(),
                _Strategy(),
                EventBus(),
                pl.Mode.DRY_RUN,
                acquire_instance_lock=False,
            )

    assert calls == ["snapshot"]


def test_lock_released_after_run(tmp_path, monkeypatch):
    """A finished run releases the lock so the next run can start."""
    calls: list[str] = []
    _stub_snapshot(monkeypatch, calls)

    for _ in range(2):
        with pytest.raises(RuntimeError, match="stop here"):
            pl.run_pipeline(
                default_config(), _Strategy(), EventBus(), pl.Mode.DRY_RUN
            )

    assert calls == ["snapshot", "snapshot"]
