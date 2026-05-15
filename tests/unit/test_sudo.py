"""Tests for archward.privilege.sudo — askpass discovery (v0.4.1 F11)."""

from __future__ import annotations

import logging

import pytest

from archward.privilege import sudo


def test_discover_askpass_uses_explicit_override() -> None:
    """A valid absolute, executable override path wins immediately.

    Use a known-existing executable (/usr/bin/true) so the test doesn't
    depend on tmp filesystem semantics (some test sandboxes strip +x).
    """
    import os
    candidate = "/usr/bin/true"
    if not (os.path.isabs(candidate) and os.access(candidate, os.X_OK)):
        pytest.skip(f"{candidate} not available on this system")
    result = sudo.discover_askpass(candidate)
    assert result == candidate


def test_discover_askpass_invalid_override_falls_back(monkeypatch, caplog) -> None:
    """v0.4.1 F11: invalid override logs warning and falls back to auto-detect.

    Pre-fix the function silently returned None when the override was
    bogus, which left users with sudo blocking on a TTY they don't have.
    """
    # Force the auto-detect chain to find a known binary so the fallback
    # has something to return.
    monkeypatch.setattr(sudo, "_ASKPASS_CANDIDATES", ("test",))
    # `test` is a builtin shell command but is also a binary at /usr/bin/test
    # on every Linux. shutil.which("test") will find it.

    with caplog.at_level(logging.WARNING, logger="archward.privilege.sudo"):
        result = sudo.discover_askpass("/no/such/path")

    # Auto-detect chain ran; the warning was logged.
    assert any(
        "not found" in rec.message for rec in caplog.records
    ), f"expected warning about invalid override; got {[r.message for r in caplog.records]}"
    assert result is not None  # auto-detect picked something


def test_discover_askpass_full_failure_logs_error(monkeypatch, caplog) -> None:
    """When both override AND auto-detect chain fail, an error is logged."""
    monkeypatch.setattr(sudo, "_ASKPASS_CANDIDATES", ("/nope/not/here",))
    with caplog.at_level(logging.ERROR, logger="archward.privilege.sudo"):
        result = sudo.discover_askpass("/another/nope")
    assert result is None
    assert any(
        "TTY input" in rec.message for rec in caplog.records
    )


def test_discover_askpass_no_override_no_warning(monkeypatch, caplog) -> None:
    """If no override is set, no 'invalid override' warning fires."""
    monkeypatch.setattr(sudo, "_ASKPASS_CANDIDATES", ("/nope/not/here",))
    with caplog.at_level(logging.WARNING, logger="archward.privilege.sudo"):
        sudo.discover_askpass()  # no override
    assert not any(
        "not found" in rec.message for rec in caplog.records
    )
