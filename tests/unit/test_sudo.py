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
    """When override, auto-detect chain, and the bundled askpass all fail, an error is logged."""
    monkeypatch.setattr(sudo, "_ASKPASS_CANDIDATES", ("/nope/not/here",))
    monkeypatch.setattr(sudo, "_bundled_askpass", lambda: None)
    monkeypatch.delenv("SUDO_ASKPASS", raising=False)
    with caplog.at_level(logging.ERROR, logger="archward.privilege.sudo"):
        result = sudo.discover_askpass("/another/nope")
    assert result is None
    assert any(
        "TTY input" in rec.message for rec in caplog.records
    )


def test_discover_askpass_env_var_wins_over_candidates(monkeypatch) -> None:
    """A valid $SUDO_ASKPASS set by the desktop session is honoured."""
    monkeypatch.setattr(sudo, "_ASKPASS_CANDIDATES", ("/nope/not/here",))
    monkeypatch.setenv("SUDO_ASKPASS", "/usr/bin/true")
    assert sudo.discover_askpass() == "/usr/bin/true"


def test_discover_askpass_falls_back_to_bundled(monkeypatch, tmp_path) -> None:
    """With no override, env var, or native candidate, the bundled Qt askpass is used."""
    fake = tmp_path / "archward-askpass"
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)
    monkeypatch.setattr(sudo, "_ASKPASS_CANDIDATES", ("/nope/not/here",))
    monkeypatch.setattr(sudo, "_bundled_askpass", lambda: str(fake))
    monkeypatch.delenv("SUDO_ASKPASS", raising=False)
    assert sudo.discover_askpass() == str(fake)


def test_bundled_askpass_prefers_interpreter_sibling(monkeypatch, tmp_path) -> None:
    """The script next to sys.executable wins over a PATH lookup."""
    sibling = tmp_path / "archward-askpass"
    sibling.write_text("#!/bin/sh\n")
    sibling.chmod(0o755)
    # Some test sandboxes mount tmp noexec, which fails access(X_OK) even
    # with the +x bit set — bypass the permission check, not the existence one.
    monkeypatch.setattr(sudo.os, "access", lambda _p, _m: True)
    monkeypatch.setattr(sudo.sys, "executable", str(tmp_path / "python"))
    monkeypatch.setattr(sudo.shutil, "which", lambda _: "/should/not/be/used")
    assert sudo._bundled_askpass() == str(sibling)


def test_bundled_askpass_falls_back_to_path(monkeypatch, tmp_path) -> None:
    """No interpreter sibling → PATH lookup result is returned (may be None)."""
    monkeypatch.setattr(sudo.sys, "executable", str(tmp_path / "python"))
    monkeypatch.setattr(sudo.shutil, "which", lambda _: None)
    assert sudo._bundled_askpass() is None


def test_discover_askpass_no_override_no_warning(monkeypatch, caplog) -> None:
    """If no override is set, no 'invalid override' warning fires."""
    monkeypatch.setattr(sudo, "_ASKPASS_CANDIDATES", ("/nope/not/here",))
    with caplog.at_level(logging.WARNING, logger="archward.privilege.sudo"):
        sudo.discover_askpass()  # no override
    assert not any(
        "not found" in rec.message for rec in caplog.records
    )
