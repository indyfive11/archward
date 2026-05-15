"""Tests for v0.4.1 F5 — systemctl wrappers with timeouts.

Regression: a hung systemd manager (broken NFS, DBus deadlock) used to
freeze the verify phase indefinitely. Each wrapper now has a timeout
and a documented safe-default return.
"""

from __future__ import annotations

import subprocess

from archward.system import services


def _raise_timeout(*args, **kwargs):
    raise subprocess.TimeoutExpired(cmd=args[0] if args else "cmd",
                                    timeout=kwargs.get("timeout", 1))


def test_is_active_timeout_returns_false(monkeypatch) -> None:
    """Hung systemctl is-active → False (treat as inactive — FAIL in verify)."""
    monkeypatch.setattr(subprocess, "run", _raise_timeout)
    assert services.is_active("sshd.service") is False


def test_unit_exists_timeout_returns_true(monkeypatch) -> None:
    """Hung systemctl cat → True (don't propose pruning what we can't verify)."""
    monkeypatch.setattr(subprocess, "run", _raise_timeout)
    assert services.unit_exists("sshd.service") is True


def test_list_running_timeout_returns_marker(monkeypatch) -> None:
    monkeypatch.setattr(subprocess, "run", _raise_timeout)
    out = services.list_running()
    assert "timed out" in out


def test_list_enabled_timeout_returns_marker(monkeypatch) -> None:
    monkeypatch.setattr(subprocess, "run", _raise_timeout)
    out = services.list_enabled()
    assert "timed out" in out


def test_is_active_uses_timeout_kwarg(monkeypatch) -> None:
    """Confirm subprocess.run is invoked with timeout= present."""
    captured: dict = {}

    def fake(*args, **kwargs):
        captured.update(kwargs)

        class R:
            returncode = 0
        return R()

    monkeypatch.setattr(subprocess, "run", fake)
    services.is_active("foo.service")
    assert "timeout" in captured
    assert captured["timeout"] > 0


def test_unit_exists_uses_timeout_kwarg(monkeypatch) -> None:
    captured: dict = {}

    def fake(*args, **kwargs):
        captured.update(kwargs)

        class R:
            returncode = 0
        return R()

    monkeypatch.setattr(subprocess, "run", fake)
    services.unit_exists("foo.service")
    assert "timeout" in captured
    assert captured["timeout"] > 0
