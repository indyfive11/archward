"""Tests for v0.4.5 F3 — security_advisories module + verify check."""

from __future__ import annotations

import io
import json
import time
import urllib.error
import urllib.request

import pytest

from archward.config.defaults import default_config
from archward.models.verify import CheckStatus
from archward.pipeline import verify_phase
from archward.system import security_advisories as sa
from archward.system.security_advisories import Advisory, fetch_advisories, open_for_installed

# ── test data ─────────────────────────────────────────────────────────

_SAMPLE = [
    {
        "name": "AVG-0001",
        "packages": ["libfoo"],
        "status": "Vulnerable",
        "severity": "Critical",
        "type": "Remote code execution",
        "affected": ">0",
        "fixed": "2.0.0-1",
        "issues": ["CVE-2025-0001"],
    },
    {
        "name": "AVG-0002",
        "packages": ["libbar"],
        "status": "Vulnerable",
        "severity": "Medium",
        "type": "Information disclosure",
        "affected": ">0",
        "fixed": "1.5.0-1",
        "issues": ["CVE-2025-0002"],
    },
    {
        "name": "AVG-0003",
        "packages": ["libbaz"],
        "status": "Fixed",
        "severity": "High",
        "type": "Buffer overflow",
        "affected": ">0",
        "fixed": "3.1.0-1",
        "issues": ["CVE-2025-0003"],
    },
    {
        "name": "AVG-0004",
        "packages": ["libqux"],
        "status": "Vulnerable",
        "severity": "Low",
        "type": "Denial of service",
        "affected": ">0",
        "fixed": None,
        "issues": [],
    },
]

_FEED_BYTES = json.dumps(_SAMPLE).encode()


def _fake_urlopen(url, timeout):
    return io.BytesIO(_FEED_BYTES)


# ── _parse_asa_json ───────────────────────────────────────────────────


def test_parse_asa_json_parses_entries() -> None:
    entries = sa._parse_asa_json(_FEED_BYTES)
    assert len(entries) == 4
    assert entries[0].name == "AVG-0001"
    assert entries[0].severity == "Critical"
    assert entries[0].fixed == "2.0.0-1"
    assert "CVE-2025-0001" in entries[0].issues


def test_parse_asa_json_fixed_none() -> None:
    entries = sa._parse_asa_json(_FEED_BYTES)
    qux = next(e for e in entries if e.name == "AVG-0004")
    assert qux.fixed is None


def test_parse_asa_json_empty() -> None:
    assert sa._parse_asa_json(b"[]") == []


# ── fetch_advisories ──────────────────────────────────────────────────


def test_fetch_advisories_from_network(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(sa, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
    entries = fetch_advisories()
    assert len(entries) == 4


def test_fetch_advisories_network_error_returns_empty(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(sa, "state_dir", lambda: tmp_path)

    def boom(url, timeout):
        raise urllib.error.URLError("no route")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    assert fetch_advisories() == []


def test_fetch_advisories_timeout_returns_empty(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(sa, "state_dir", lambda: tmp_path)

    def boom(url, timeout):
        raise TimeoutError

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    assert fetch_advisories() == []


def test_fetch_advisories_writes_cache(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(sa, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
    fetch_advisories()
    assert (tmp_path / sa._CACHE_FILE).exists()


def test_fetch_advisories_cache_hit_avoids_network(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(sa, "state_dir", lambda: tmp_path)
    calls = []

    def counting(url, timeout):
        calls.append(1)
        return io.BytesIO(_FEED_BYTES)

    monkeypatch.setattr(urllib.request, "urlopen", counting)
    fetch_advisories()
    fetch_advisories()
    assert len(calls) == 1


def test_fetch_advisories_stale_cache_refetches(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(sa, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
    cache = tmp_path / sa._CACHE_FILE
    cache.write_text(json.dumps({"fetched_at": time.time() - 5 * 3600, "advisories": []}))
    entries = fetch_advisories()
    assert len(entries) == 4


# ── open_for_installed ────────────────────────────────────────────────


def _adv(name, pkg, status, severity, fixed) -> Advisory:
    return Advisory(
        name=name,
        packages=(pkg,),
        status=status,
        severity=severity,
        advisory_type="test",
        affected=">0",
        fixed=fixed,
        issues=(),
    )


def test_open_for_installed_vulnerable_old_version(monkeypatch) -> None:
    advisories = [_adv("AVG-X", "libfoo", "Vulnerable", "Critical", "2.0.0-1")]
    installed = [("libfoo", "1.9.0-1")]
    # vercmp("1.9.0-1", "2.0.0-1") → -1 = installed < fixed = vulnerable
    monkeypatch.setattr(sa.pq, "vercmp", lambda a, b: -1)
    result = open_for_installed(advisories, installed)
    assert len(result) == 1
    assert result[0].name == "AVG-X"


def test_open_for_installed_already_fixed(monkeypatch) -> None:
    advisories = [_adv("AVG-X", "libfoo", "Vulnerable", "Critical", "2.0.0-1")]
    installed = [("libfoo", "2.0.0-1")]
    monkeypatch.setattr(sa.pq, "vercmp", lambda a, b: 0)
    result = open_for_installed(advisories, installed)
    assert result == []


def test_open_for_installed_not_installed(monkeypatch) -> None:
    advisories = [_adv("AVG-X", "libfoo", "Vulnerable", "Critical", "2.0.0-1")]
    result = open_for_installed(advisories, [("otherpkg", "1.0")])
    assert result == []


def test_open_for_installed_fixed_status_skipped(monkeypatch) -> None:
    advisories = [_adv("AVG-X", "libfoo", "Fixed", "Critical", "2.0.0-1")]
    installed = [("libfoo", "1.0.0-1")]
    result = open_for_installed(advisories, installed)
    assert result == []


def test_open_for_installed_no_fixed_version(monkeypatch) -> None:
    advisories = [_adv("AVG-X", "libqux", "Vulnerable", "Low", None)]
    installed = [("libqux", "1.0.0-1")]
    result = open_for_installed(advisories, installed)
    assert len(result) == 1


# ── _security_advisory_check (verify integration) ─────────────────────


def test_advisory_check_disabled(monkeypatch, tmp_path) -> None:
    """security_advisories=False → PASS without network hit."""
    from archward.models.config import VerifyConfig
    cfg = default_config()
    new_verify = VerifyConfig(
        enabled=cfg.verify.enabled,
        reboot_log=cfg.verify.reboot_log,
        security_advisories=False,
    )
    cfg = cfg.model_copy(update={"verify": new_verify})
    result = verify_phase._security_advisory_check(cfg)
    assert result.status is CheckStatus.PASS
    assert "disabled" in result.message


def test_advisory_check_arch_audit_present(monkeypatch) -> None:
    """arch-audit installed → PASS (defer to it)."""
    monkeypatch.setattr(verify_phase.sa, "arch_audit_present", lambda: True)
    result = verify_phase._security_advisory_check(default_config())
    assert result.status is CheckStatus.PASS
    assert "arch-audit" in result.message


def test_advisory_check_network_failure_skip(monkeypatch) -> None:
    """fetch_advisories returning [] → PASS (skip gracefully)."""
    monkeypatch.setattr(verify_phase.sa, "arch_audit_present", lambda: False)
    monkeypatch.setattr(verify_phase.sa, "fetch_advisories", lambda: [])
    result = verify_phase._security_advisory_check(default_config())
    assert result.status is CheckStatus.PASS
    assert "skipped" in result.message


def test_advisory_check_no_open_advisories(monkeypatch) -> None:
    monkeypatch.setattr(verify_phase.sa, "arch_audit_present", lambda: False)
    monkeypatch.setattr(verify_phase.sa, "fetch_advisories",
                        lambda: [_adv("AVG-X", "libfoo", "Fixed", "High", "2.0")])
    monkeypatch.setattr(verify_phase.pq, "list_all", lambda: [("libfoo", "2.0.0-1")])
    monkeypatch.setattr(verify_phase.sa, "open_for_installed", lambda a, i: [])
    result = verify_phase._security_advisory_check(default_config())
    assert result.status is CheckStatus.PASS


def test_advisory_check_critical_is_fail(monkeypatch) -> None:
    crit = _adv("AVG-C", "libfoo", "Vulnerable", "Critical", "2.0")
    monkeypatch.setattr(verify_phase.sa, "arch_audit_present", lambda: False)
    monkeypatch.setattr(verify_phase.sa, "fetch_advisories", lambda: [crit])
    monkeypatch.setattr(verify_phase.sa, "open_for_installed", lambda a, i: [crit])
    monkeypatch.setattr(verify_phase.pq, "list_all", lambda: [("libfoo", "1.0")])
    result = verify_phase._security_advisory_check(default_config())
    assert result.status is CheckStatus.FAIL
    assert "AVG-C" in result.detail


def test_advisory_check_medium_is_warn(monkeypatch) -> None:
    med = _adv("AVG-M", "libbar", "Vulnerable", "Medium", "1.5")
    monkeypatch.setattr(verify_phase.sa, "arch_audit_present", lambda: False)
    monkeypatch.setattr(verify_phase.sa, "fetch_advisories", lambda: [med])
    monkeypatch.setattr(verify_phase.sa, "open_for_installed", lambda a, i: [med])
    monkeypatch.setattr(verify_phase.pq, "list_all", lambda: [("libbar", "1.0")])
    result = verify_phase._security_advisory_check(default_config())
    assert result.status is CheckStatus.WARN
