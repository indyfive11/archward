"""Tests for v0.4.5 F4a — missing timeouts on subprocess calls.

Verifies that each fixed call site handles subprocess.TimeoutExpired
gracefully (no crash, correct safe fallback returned).
"""

from __future__ import annotations

import subprocess

import pytest


# ── pacman/query.py _run() ────────────────────────────────────────────


def test_query_run_timeout_returns_safe_fallback(monkeypatch) -> None:
    """_run() returns (1, "", "timeout") on TimeoutExpired — no crash."""
    from archward.pacman import query as pq

    def boom(*a, **k):
        raise subprocess.TimeoutExpired(["pacman"], pq._QUERY_TIMEOUT_S)

    monkeypatch.setattr(subprocess, "run", boom)
    rc, out, err = pq._run(["pacman", "-Q"])
    assert rc == 1
    assert out == ""
    assert err == "timeout"


def test_query_run_timeout_logged(monkeypatch, caplog) -> None:
    from archward.pacman import query as pq
    import logging

    def boom(*a, **k):
        raise subprocess.TimeoutExpired(["pacman"], pq._QUERY_TIMEOUT_S)

    monkeypatch.setattr(subprocess, "run", boom)
    with caplog.at_level(logging.WARNING, logger="archward.pacman.query"):
        pq._run(["pacman", "-Q"])
    assert any("timed out" in r.message for r in caplog.records)


# ── privilege/sudo.py warmup() ────────────────────────────────────────


def test_sudo_warmup_timeout_returns_false(monkeypatch) -> None:
    """warmup() returns False on TimeoutExpired — no crash."""
    from archward.privilege.sudo import AskpassStrategy

    strategy = AskpassStrategy(None)

    def boom(*a, **k):
        raise subprocess.TimeoutExpired(["sudo"], 5)

    monkeypatch.setattr(subprocess, "run", boom)
    assert strategy.warmup() is False


def test_sudo_warmup_timeout_logged(monkeypatch, caplog) -> None:
    from archward.privilege.sudo import AskpassStrategy
    import logging

    strategy = AskpassStrategy(None)

    def boom(*a, **k):
        raise subprocess.TimeoutExpired(["sudo"], 5)

    monkeypatch.setattr(subprocess, "run", boom)
    with caplog.at_level(logging.WARNING, logger="archward.privilege.sudo"):
        strategy.warmup()
    assert any("timed out" in r.message for r in caplog.records)


def test_persistent_sudo_warmup_has_timeout(monkeypatch) -> None:
    """v0.4.16: PersistentSudoStrategy.warmup ran `sudo -A -v` with NO
    timeout — a wedged askpass hung the WarmupWorker thread forever. It must
    pass a timeout and map TimeoutExpired to False."""
    from archward.privilege.sudo import PersistentSudoStrategy

    strategy = PersistentSudoStrategy(None)
    seen: dict = {}

    def fake_run(argv, **kw):
        seen.update(kw)
        raise subprocess.TimeoutExpired(argv, kw.get("timeout", 0))

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert strategy.warmup() is False
    assert seen.get("timeout"), "sudo -A -v must run with a timeout"
