"""Unit tests for archward-verify-zerotier.

Every test stubs `subprocess.run` so nothing hits a real zerotier-cli.
Verifies the four reachable code paths:

  1. zerotier-cli not on PATH → empty list (silent skip).
  2. zerotier-cli present but no per-user authtoken → one actionable WARN row.
  3. Daemon online, no networks → daemon PASS + networks WARN.
  4. Daemon online, networks present → daemon PASS + one row per network
     (PASS for OK, WARN for REQUESTING_CONFIGURATION, FAIL for everything else).
"""

from __future__ import annotations

import json
import subprocess
from typing import Any
from unittest.mock import MagicMock

import pytest

from archward.models.verify import CheckStatus
import archward_verify_zerotier as plugin


# ── helpers ──────────────────────────────────────────────────────────────


class _FakeResult:
    def __init__(self, stdout: str = "", stderr: str = "", returncode: int = 0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def _stub_run(monkeypatch, response_map: dict[tuple[str, ...], _FakeResult]):
    """Patch subprocess.run; dispatch on the argv tuple (excluding the
    binary path) so each call gets its own fixture response."""
    def fake(argv, **kwargs):
        key = tuple(argv[1:])
        if key in response_map:
            return response_map[key]
        # Default: empty success — keeps unrelated tests resilient.
        return _FakeResult()
    monkeypatch.setattr(plugin.subprocess, "run", fake)


def _cfg() -> Any:
    return MagicMock()


def _snapshot() -> Any:
    return MagicMock()


# ── 1. CLI not on PATH ──────────────────────────────────────────────────


def test_missing_cli_returns_empty_list(monkeypatch) -> None:
    monkeypatch.setattr(plugin.shutil, "which", lambda name: None)
    assert plugin.verify(_cfg(), _snapshot()) == []


# ── 2. authtoken not readable ───────────────────────────────────────────


def test_authtoken_missing_returns_actionable_warn(monkeypatch) -> None:
    monkeypatch.setattr(plugin.shutil, "which", lambda name: "/usr/bin/zerotier-cli")
    _stub_run(monkeypatch, {
        ("info", "-j"): _FakeResult(
            stderr=("zerotier-cli: authtoken.secret not found or readable in "
                    "/var/lib/zerotier-one (try again as root)\n"),
            returncode=2,
        ),
    })
    checks = plugin.verify(_cfg(), _snapshot())
    assert len(checks) == 1
    c = checks[0]
    assert c.status is CheckStatus.WARN
    assert "authtoken" in c.message.lower()
    # The detail field must include the actual recovery command so the
    # GUI's "What to do?" hint (or the CLI report) is useful.
    assert "sudo cp /var/lib/zerotier-one/authtoken.secret" in c.detail


# ── 3. Daemon online, no networks ───────────────────────────────────────


def test_online_no_networks_yields_two_checks(monkeypatch) -> None:
    monkeypatch.setattr(plugin.shutil, "which", lambda name: "/usr/bin/zerotier-cli")
    _stub_run(monkeypatch, {
        ("info", "-j"): _FakeResult(stdout=json.dumps({
            "online": True,
            "address": "abc123def4",
            "version": "1.16.0",
        })),
        ("listnetworks", "-j"): _FakeResult(stdout="[]"),
    })
    checks = plugin.verify(_cfg(), _snapshot())
    assert len(checks) == 2
    daemon, nets = checks
    assert daemon.name == "zerotier-daemon"
    assert daemon.status is CheckStatus.PASS
    assert "abc123def4" in daemon.message
    assert nets.status is CheckStatus.WARN
    assert "no networks" in nets.message.lower()


# ── 4. Daemon online with networks ──────────────────────────────────────


def test_online_with_mixed_networks(monkeypatch) -> None:
    monkeypatch.setattr(plugin.shutil, "which", lambda name: "/usr/bin/zerotier-cli")
    networks = [
        {
            "nwid": "1111111111111111",
            "name": "homemesh",
            "status": "OK",
            "assignedAddresses": ["192.168.192.50/24"],
        },
        {
            "nwid": "2222222222222222",
            "name": "labmesh",
            "status": "REQUESTING_CONFIGURATION",
            "assignedAddresses": [],
        },
        {
            "nwid": "3333333333333333",
            "name": "deadmesh",
            "status": "ACCESS_DENIED",
            "assignedAddresses": [],
        },
    ]
    _stub_run(monkeypatch, {
        ("info", "-j"): _FakeResult(stdout=json.dumps({
            "online": True,
            "address": "abc123def4",
            "version": "1.16.0",
        })),
        ("listnetworks", "-j"): _FakeResult(stdout=json.dumps(networks)),
    })
    checks = plugin.verify(_cfg(), _snapshot())

    # daemon + 3 networks = 4 rows
    assert len(checks) == 4
    daemon, ok_net, pending_net, denied_net = checks

    assert daemon.status is CheckStatus.PASS

    assert ok_net.name == "zt:homemesh"
    assert ok_net.status is CheckStatus.PASS
    assert "192.168.192.50/24" in ok_net.message

    assert pending_net.name == "zt:labmesh"
    assert pending_net.status is CheckStatus.WARN
    assert "awaiting" in pending_net.message.lower()

    assert denied_net.name == "zt:deadmesh"
    assert denied_net.status is CheckStatus.FAIL
    assert "ACCESS_DENIED" in denied_net.message


# ── 5. Offline daemon ───────────────────────────────────────────────────


def test_offline_daemon_emits_fail(monkeypatch) -> None:
    monkeypatch.setattr(plugin.shutil, "which", lambda name: "/usr/bin/zerotier-cli")
    _stub_run(monkeypatch, {
        ("info", "-j"): _FakeResult(stdout=json.dumps({
            "online": False,
            "address": "abc123def4",
            "version": "1.16.0",
        })),
        ("listnetworks", "-j"): _FakeResult(stdout="[]"),
    })
    checks = plugin.verify(_cfg(), _snapshot())
    daemon = checks[0]
    assert daemon.status is CheckStatus.FAIL
    assert "offline" in daemon.message.lower()


# ── 6. Malformed JSON ───────────────────────────────────────────────────


def test_garbage_info_output_yields_fail(monkeypatch) -> None:
    monkeypatch.setattr(plugin.shutil, "which", lambda name: "/usr/bin/zerotier-cli")
    _stub_run(monkeypatch, {
        ("info", "-j"): _FakeResult(stdout="not json {"),
    })
    checks = plugin.verify(_cfg(), _snapshot())
    assert len(checks) == 1
    assert checks[0].status is CheckStatus.FAIL
    assert "invalid JSON" in checks[0].message


# ── 7. Timeout ──────────────────────────────────────────────────────────


def test_cli_timeout_treated_as_failure(monkeypatch) -> None:
    monkeypatch.setattr(plugin.shutil, "which", lambda name: "/usr/bin/zerotier-cli")

    def raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=5)

    monkeypatch.setattr(plugin.subprocess, "run", raise_timeout)
    checks = plugin.verify(_cfg(), _snapshot())
    assert len(checks) == 1
    assert checks[0].status is CheckStatus.FAIL
