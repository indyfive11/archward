"""Tests for v0.4.1 F2 — subprocess timeouts in the snapshot phase.

Regression: a broken interface or stuck mount used to hang `ip addr` /
`ss -tlnp` / `wg show` / `df -h` indefinitely, freezing the entire
pipeline. Each call now has a short timeout; on TimeoutExpired the
section's output is replaced with a "(timed out)" marker so the
snapshot continues.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from archward.pipeline.snapshot import _gather_network


def _raise_timeout(*args, **kwargs):
    raise subprocess.TimeoutExpired(cmd=args[0] if args else "cmd",
                                    timeout=kwargs.get("timeout", 1))


def test_ip_addr_timeout_doesnt_hang(tmp_path: Path, monkeypatch) -> None:
    """If `ip addr` hangs past its timeout, _gather_network continues."""
    monkeypatch.setattr(subprocess, "run", _raise_timeout)
    # Block shutil.which("wg") path so we don't also fire wg show.
    import shutil as _shutil
    monkeypatch.setattr(_shutil, "which", lambda name: None)

    _gather_network(tmp_path)

    ndir = tmp_path / "network"
    assert (ndir / "ip-addr.txt").exists()
    assert "timed out" in (ndir / "ip-addr.txt").read_text()


def test_ss_timeout_doesnt_hang(tmp_path: Path, monkeypatch) -> None:
    """`ss -tlnp` timeout falls through cleanly to next command."""
    monkeypatch.setattr(subprocess, "run", _raise_timeout)
    import shutil as _shutil
    monkeypatch.setattr(_shutil, "which", lambda name: None)

    _gather_network(tmp_path)

    ndir = tmp_path / "network"
    assert (ndir / "listening-ports.txt").exists()
    assert "timed out" in (ndir / "listening-ports.txt").read_text()


def test_wg_timeout_handled(tmp_path: Path, monkeypatch) -> None:
    """When `wg show` is available but times out, we mark it and continue."""
    monkeypatch.setattr(subprocess, "run", _raise_timeout)
    import shutil as _shutil
    monkeypatch.setattr(_shutil, "which", lambda name: "/usr/bin/wg")

    _gather_network(tmp_path)

    ndir = tmp_path / "network"
    assert (ndir / "wg-status.txt").exists()
    assert "timed out" in (ndir / "wg-status.txt").read_text()


def test_ip_addr_uses_timeout_kwarg(monkeypatch) -> None:
    """Confirm subprocess.run is invoked with timeout=, not bare."""
    captured: dict = {}

    def fake_run(*args, **kwargs):
        captured["kwargs"] = kwargs

        class R:
            stdout = ""
            returncode = 0
        return R()

    import shutil as _shutil
    monkeypatch.setattr(_shutil, "which", lambda name: None)
    monkeypatch.setattr(subprocess, "run", fake_run)

    from tempfile import TemporaryDirectory
    with TemporaryDirectory() as td:
        _gather_network(Path(td))

    assert "timeout" in captured["kwargs"]
    assert captured["kwargs"]["timeout"] > 0
