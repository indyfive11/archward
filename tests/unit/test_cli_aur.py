"""Tests for `archward aur` subcommands — quarantine and pending."""

from __future__ import annotations

from argparse import Namespace
from unittest.mock import MagicMock

import pytest

from archward.cli import _build_parser
from archward.cli_subcommands import aur as cmd


# ── parser ────────────────────────────────────────────────────────────────


def test_aur_quarantine_list_parser() -> None:
    args = _build_parser().parse_args(["aur", "quarantine", "list"])
    assert args.command == "aur"
    assert args.aur_action == "quarantine"
    assert args.quarantine_action == "list"


def test_aur_quarantine_clear_parser() -> None:
    args = _build_parser().parse_args(["aur", "quarantine", "clear"])
    assert args.aur_action == "quarantine"
    assert args.quarantine_action == "clear"
    assert args.package is None


def test_aur_pending_parser() -> None:
    args = _build_parser().parse_args(["aur", "pending"])
    assert args.command == "aur"
    assert args.aur_action == "pending"


# ── cmd_pending ───────────────────────────────────────────────────────────


def _fake_cfg() -> MagicMock:
    cfg = MagicMock()
    cfg.aur.helper_preference = ["yay", "paru"]
    return cfg


def test_aur_pending_no_helper(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cmd, "build_config", lambda *a, **k: _fake_cfg())
    import archward.aur.helper as helper_mod
    monkeypatch.setattr(helper_mod, "discover", lambda prefs: None)

    args = Namespace()
    code = cmd.cmd_pending(args, None)
    assert code == 2
    assert "No AUR helper" in capsys.readouterr().err


def test_aur_pending_empty(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cmd, "build_config", lambda *a, **k: _fake_cfg())
    fake_helper = MagicMock()
    fake_helper.name = "yay"
    fake_helper.list_pending.return_value = []
    import archward.aur.helper as helper_mod
    monkeypatch.setattr(helper_mod, "discover", lambda prefs: fake_helper)

    args = Namespace()
    code = cmd.cmd_pending(args, None)
    assert code == 0
    assert "No pending AUR updates" in capsys.readouterr().out


def test_aur_pending_lists_sorted(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cmd, "build_config", lambda *a, **k: _fake_cfg())
    fake_helper = MagicMock()
    fake_helper.name = "yay"
    fake_helper.list_pending.return_value = [
        ("zerotier-gui-git", "1.3-1", "1.4-1"),
        ("android-sdk-platform-tools", "36-1", "37-1"),
        ("jellyfin-git", "10.8-1", "10.9-1"),
    ]
    import archward.aur.helper as helper_mod
    monkeypatch.setattr(helper_mod, "discover", lambda prefs: fake_helper)

    args = Namespace()
    code = cmd.cmd_pending(args, None)
    assert code == 0
    out = capsys.readouterr().out
    pos_android = out.index("android-sdk-platform-tools")
    pos_jellyfin = out.index("jellyfin-git")
    pos_zerotier = out.index("zerotier-gui-git")
    assert pos_android < pos_jellyfin < pos_zerotier, "output should be alphabetically sorted"


def test_aur_pending_helper_exception(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cmd, "build_config", lambda *a, **k: _fake_cfg())
    fake_helper = MagicMock()
    fake_helper.name = "yay"
    fake_helper.list_pending.side_effect = RuntimeError("yay exploded")
    import archward.aur.helper as helper_mod
    monkeypatch.setattr(helper_mod, "discover", lambda prefs: fake_helper)

    args = Namespace()
    code = cmd.cmd_pending(args, None)
    assert code == 1
    assert "Failed to query" in capsys.readouterr().err
