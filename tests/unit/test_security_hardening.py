"""v0.5 security hardening batch."""

from __future__ import annotations

import io
from argparse import Namespace

import pytest

from archward import netutil


class _FakeResp(io.BytesIO):
    pass


def test_bounded_read_passes_small_bodies() -> None:
    assert netutil.bounded_read(_FakeResp(b"x" * 100), 1000) == b"x" * 100


def test_bounded_read_raises_on_oversize() -> None:
    with pytest.raises(OSError):
        netutil.bounded_read(_FakeResp(b"x" * 2000), 1000)


def test_advisories_cap_stays_generous() -> None:
    """all.json is a cumulative archive already ~1 MiB (measured 900 KB,
    2026-08). A cap below 4 MiB would silently kill the security check —
    this is the guard against someone 'tidying' it down."""
    assert netutil.ADVISORIES_MAX_BYTES >= 4 * 1024 * 1024


def test_fetch_sites_use_bounded_read() -> None:
    """The three remote fetchers must read through the bounded helper."""
    import inspect

    from archward.aur import metadata
    from archward.system import arch_news, security_advisories

    for mod, cap in (
        (arch_news, "NEWS_MAX_BYTES"),
        (security_advisories, "ADVISORIES_MAX_BYTES"),
        (metadata, "AUR_METADATA_MAX_BYTES"),
    ):
        src = inspect.getsource(mod)
        assert "bounded_read" in src and cap in src, mod.__name__


def test_set_keep_zero_refused_even_with_force(capsys) -> None:
    from archward.cli_subcommands import cache as cmd

    args = Namespace(n=0, force=True)
    assert cmd.cmd_set_keep(args, None) == 2
    assert "entire rollback cache" in capsys.readouterr().err


def test_set_keep_negative_refused_even_with_force() -> None:
    from archward.cli_subcommands import cache as cmd

    assert cmd.cmd_set_keep(Namespace(n=-5, force=True), None) == 2


def test_env_askpass_outside_usr_warns_but_is_honored(monkeypatch, caplog, tmp_path) -> None:
    import logging
    import os

    from archward.privilege import sudo as sudo_mod

    fake = tmp_path / "my-askpass"
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)
    # Sandbox tmp may be noexec — force the X_OK check to pass (existing
    # pattern from test_sudo.py).
    real_access = os.access
    monkeypatch.setattr(
        os, "access", lambda p, m: True if str(p) == str(fake) else real_access(p, m)
    )
    monkeypatch.setenv("SUDO_ASKPASS", str(fake))
    with caplog.at_level(logging.WARNING, logger="archward.privilege.sudo"):
        found = sudo_mod.discover_askpass()
    assert found == str(fake)                      # honored, not rejected
    assert any("outside /usr" in r.message for r in caplog.records)


def test_env_askpass_in_usr_no_warning(monkeypatch, caplog) -> None:
    import logging
    import os

    from archward.privilege import sudo as sudo_mod

    monkeypatch.setenv("SUDO_ASKPASS", "/usr/bin/ksshaskpass")
    real_access = os.access
    monkeypatch.setattr(
        os, "access",
        lambda p, m: True if str(p) == "/usr/bin/ksshaskpass" else real_access(p, m),
    )
    with caplog.at_level(logging.WARNING, logger="archward.privilege.sudo"):
        found = sudo_mod.discover_askpass()
    assert found == "/usr/bin/ksshaskpass"
    assert not any("outside /usr" in r.message for r in caplog.records)
