"""Tests for `archward cache` CLI subcommand — parser + dispatch + commands."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from archward.cli import _build_parser
from archward.system import cache_policy as cp
from archward.system.cache_policy import CachePolicy, RollbackSafety


# ── parser ────────────────────────────────────────────────────────────────────


def test_cache_status_parser() -> None:
    parser = _build_parser()
    args = parser.parse_args(["cache", "status"])
    assert args.command == "cache"
    assert args.cache_action == "status"


def test_cache_gaps_parser() -> None:
    parser = _build_parser()
    args = parser.parse_args(["cache", "gaps"])
    assert args.command == "cache"
    assert args.cache_action == "gaps"


def test_set_keep_parser() -> None:
    parser = _build_parser()
    args = parser.parse_args(["cache", "set-keep", "3"])
    assert args.command == "cache"
    assert args.cache_action == "set-keep"
    assert args.n == 3
    assert args.force is False


def test_set_keep_force_flag() -> None:
    parser = _build_parser()
    args = parser.parse_args(["cache", "set-keep", "1", "--force"])
    assert args.n == 1
    assert args.force is True


def test_cache_clean_parser() -> None:
    parser = _build_parser()
    args = parser.parse_args(["cache", "clean"])
    assert args.command == "cache"
    assert args.cache_action == "clean"


# ── set-keep guard: refuses < 2 without --force ───────────────────────────────


def test_set_keep_refuses_below_2_without_force(monkeypatch, capsys) -> None:
    from archward.cli_subcommands.cache import cmd_set_keep

    parser = _build_parser()
    args = parser.parse_args(["cache", "set-keep", "1"])
    monkeypatch.setattr(cp, "paccache_timer_state", lambda: "enabled")

    rc = cmd_set_keep(args, None)

    assert rc == 2
    out = capsys.readouterr()
    assert "Refusing" in out.err
    assert "--force" in out.err


def test_set_keep_allows_1_with_force(monkeypatch, capsys) -> None:
    from archward.cli_subcommands.cache import cmd_set_keep

    parser = _build_parser()
    args = parser.parse_args(["cache", "set-keep", "1", "--force"])

    monkeypatch.setattr(cp, "paccache_timer_state", lambda: "enabled")

    fake_cfg = MagicMock()
    fake_strategy = MagicMock()

    def fake_run_capture(argv, strategy=None, input_text=None):
        return 0, "", ""

    with (
        patch("archward.cli_subcommands.cache.build_config", return_value=fake_cfg),
        patch("archward.cli_subcommands.cache.build_sudo_strategy", return_value=fake_strategy),
        patch("archward.cli_subcommands.cache.run_capture", side_effect=fake_run_capture),
    ):
        rc = cmd_set_keep(args, None)

    assert rc != 2


# ── set-keep: non-systemd guard ───────────────────────────────────────────────


def test_set_keep_bails_on_missing_timer(monkeypatch, capsys) -> None:
    from archward.cli_subcommands.cache import cmd_set_keep

    parser = _build_parser()
    args = parser.parse_args(["cache", "set-keep", "3"])
    monkeypatch.setattr(cp, "paccache_timer_state", lambda: "not-installed")

    rc = cmd_set_keep(args, None)

    assert rc == 2
    out = capsys.readouterr()
    assert "paccache.timer is not available" in out.err
    assert "systemd" in out.err


# ── cmd_status smoke ──────────────────────────────────────────────────────────


def _fake_policy(**overrides) -> CachePolicy:
    defaults = dict(
        timer_state="enabled",
        paccache_args="-rk3",
        effective_keep=3,
        clean_method=("KeepInstalled",),
        cleaning_hooks=(),
        cache_size_bytes=500 * 1024 * 1024,
        cache_file_count=120,
        safety=RollbackSafety.BALANCED,
        explanation="Balanced policy.",
    )
    defaults.update(overrides)
    return CachePolicy(**defaults)


def test_cmd_status_prints_verdict(monkeypatch, capsys) -> None:
    from archward.cli_subcommands.cache import cmd_status

    parser = _build_parser()
    args = parser.parse_args(["cache", "status"])
    monkeypatch.setattr(cp, "detect_cache_policy", lambda: _fake_policy())

    rc = cmd_status(args, None)

    assert rc == 0
    out = capsys.readouterr().out
    assert "BALANCED" in out
    assert "enabled" in out
    assert "3 versions" in out


def test_cmd_status_shows_hook_as_dangerous(monkeypatch, capsys) -> None:
    from archward.cli_subcommands.cache import cmd_status

    parser = _build_parser()
    args = parser.parse_args(["cache", "status"])
    hook = MagicMock()
    hook.name = "zz-paccache.hook"
    monkeypatch.setattr(
        cp, "detect_cache_policy",
        lambda: _fake_policy(cleaning_hooks=(hook,), safety=RollbackSafety.DANGEROUS),
    )

    rc = cmd_status(args, None)

    assert rc == 0
    out = capsys.readouterr().out
    assert "DANGEROUS" in out
    assert "zz-paccache.hook" in out


# ── cmd_gaps ──────────────────────────────────────────────────────────────────


def test_cmd_gaps_no_gaps(monkeypatch, capsys) -> None:
    from archward.cli_subcommands import cache as mod

    parser = _build_parser()
    args = parser.parse_args(["cache", "gaps"])

    monkeypatch.setattr("archward.pacman.query.list_all", lambda: [("foo", "2-1")])
    monkeypatch.setattr(cp, "read_cache_dirs", lambda: (Path("/fake"),))
    monkeypatch.setattr(mod, "_scan_cache", lambda dirs: {"foo-1-1-x86_64.pkg.tar.zst"})
    monkeypatch.setattr(cp, "rollback_gaps", lambda installed, names: [])

    rc = mod.cmd_gaps(args, None)

    assert rc == 0
    out = capsys.readouterr().out
    assert "No rollback gaps" in out


def test_cmd_gaps_with_gaps(monkeypatch, capsys) -> None:
    from archward.cli_subcommands import cache as mod

    parser = _build_parser()
    args = parser.parse_args(["cache", "gaps"])

    monkeypatch.setattr("archward.pacman.query.list_all", lambda: [("foo", "2-1"), ("bar", "1-1")])
    monkeypatch.setattr(cp, "read_cache_dirs", lambda: (Path("/fake"),))
    monkeypatch.setattr(mod, "_scan_cache", lambda dirs: set())
    monkeypatch.setattr(cp, "rollback_gaps", lambda installed, names: [("bar", "1-1"), ("foo", "2-1")])

    rc = mod.cmd_gaps(args, None)

    assert rc == 0
    out = capsys.readouterr().out
    assert "2 package(s)" in out
    assert "bar" in out
    assert "foo" in out
