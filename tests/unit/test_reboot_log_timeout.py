"""Tests for v0.4.1 F6 — reboot-log stat timeout.

Regression: `Path(cfg.verify.reboot_log).stat()` (or .exists()) on a stuck
NFS mount would freeze verify forever. The check now wraps fs probes in
a daemon-thread + join-timeout helper; on timeout the check returns a
WARN row pointing at the misconfiguration.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from archward.config.defaults import default_config
from archward.models.verify import CheckStatus
from archward.pipeline import verify_phase


def test_reboot_log_check_hanging_path_returns_warn(monkeypatch, tmp_path) -> None:
    """If Path.exists() blocks past REBOOT_LOG_STAT_TIMEOUT_S, return WARN within bound."""
    # Patch the timeout small so the test runs fast.
    monkeypatch.setattr(verify_phase, "REBOOT_LOG_STAT_TIMEOUT_S", 0.3)

    # Replace _call_with_timeout's threaded path is messy — easier to
    # patch Path.exists at the class level.
    proceed = []

    def hanging_exists(self):
        time.sleep(2)  # exceeds the patched timeout
        return False

    monkeypatch.setattr(Path, "exists", hanging_exists)

    cfg = default_config()
    # Force a non-empty reboot_log path so the check actually runs.
    new_verify = cfg.verify.model_copy(update={
        "reboot_log": "/tmp/archward-test-reboot-log"
    })
    cfg = cfg.model_copy(update={"verify": new_verify})

    t0 = time.monotonic()
    result = verify_phase._reboot_log_check(cfg, tmp_path)
    elapsed = time.monotonic() - t0

    assert elapsed < 1.0, f"_reboot_log_check hung for {elapsed:.1f}s"
    assert result is not None
    assert result.status is CheckStatus.WARN
    assert "timed out" in result.message


def test_call_with_timeout_returns_value_for_fast_fn() -> None:
    """The helper passes through return values when the call is fast."""
    result = verify_phase._call_with_timeout(lambda: 42, timeout_s=1.0)
    assert result == 42


def test_call_with_timeout_raises_on_slow_fn() -> None:
    """Slow callables raise TimeoutError within the timeout window."""
    t0 = time.monotonic()
    with pytest.raises(TimeoutError):
        verify_phase._call_with_timeout(lambda: time.sleep(5), timeout_s=0.3)
    elapsed = time.monotonic() - t0
    assert elapsed < 1.0


def test_call_with_timeout_propagates_exceptions() -> None:
    """If the callable raises, the helper re-raises the same exception."""
    class MyError(RuntimeError):
        pass

    def boom():
        raise MyError("hi")

    with pytest.raises(MyError, match="hi"):
        verify_phase._call_with_timeout(boom, timeout_s=1.0)
