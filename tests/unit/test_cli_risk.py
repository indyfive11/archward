"""Tests for `archward risk` — standalone risk classification."""

from __future__ import annotations

from argparse import Namespace
from unittest.mock import MagicMock

import pytest

from archward.cli import _build_parser
from archward.cli_subcommands import risk as cmd
from archward.models.update import PendingUpdate, RiskLevel


# ── parser ────────────────────────────────────────────────────────────────


def test_risk_parser() -> None:
    args = _build_parser().parse_args(["risk"])
    assert args.command == "risk"
    assert args.no_aur is False


def test_risk_parser_no_aur_flag() -> None:
    args = _build_parser().parse_args(["risk", "--no-aur"])
    assert args.no_aur is True


# ── helpers ───────────────────────────────────────────────────────────────


def _fake_cfg(aur_enabled: bool = True) -> MagicMock:
    cfg = MagicMock()
    cfg.aur.enabled = aur_enabled
    cfg.aur.helper_preference = ["yay"]
    return cfg


def _pkg(name: str, risk: RiskLevel, reason: str | None = None) -> PendingUpdate:
    return PendingUpdate(
        name=name, old_version="1-1", new_version="2-1",
        source="official", risk=risk, reason=reason,
    )


def _fake_preview(replacements=(), conflicts=()):
    p = MagicMock()
    p.replacements = replacements
    p.conflicts = conflicts
    return p


# ── cmd_risk output ───────────────────────────────────────────────────────


def test_risk_no_updates(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cmd, "build_config", lambda *a, **k: _fake_cfg())
    monkeypatch.setattr(cmd, "classify_pending", lambda cfg, bus: [])
    monkeypatch.setattr(cmd, "preview_transaction", lambda bus: _fake_preview())

    args = Namespace(no_aur=True)
    code = cmd.cmd_risk(args, None)
    assert code == 0
    assert "No pending" in capsys.readouterr().out


def test_risk_groups_by_level_high_before_low(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cmd, "build_config", lambda *a, **k: _fake_cfg())
    monkeypatch.setattr(cmd, "classify_pending", lambda cfg, bus: [
        _pkg("vim", RiskLevel.LOW),
        _pkg("glibc", RiskLevel.HIGH, reason="in high-risk list"),
    ])
    monkeypatch.setattr(cmd, "preview_transaction", lambda bus: _fake_preview())

    args = Namespace(no_aur=True)
    cmd.cmd_risk(args, None)
    out = capsys.readouterr().out
    assert out.index("HIGH") < out.index("LOW")
    assert "glibc" in out
    assert "in high-risk list" in out


def test_risk_shows_aur_pending(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cmd, "build_config", lambda *a, **k: _fake_cfg())
    monkeypatch.setattr(cmd, "classify_pending", lambda cfg, bus: [])
    monkeypatch.setattr(cmd, "preview_transaction", lambda bus: _fake_preview())

    fake_helper = MagicMock()
    fake_helper.name = "yay"
    fake_helper.list_pending.return_value = [("myaur-pkg", "1.0-1", "2.0-1")]

    import archward.aur.helper as helper_mod
    monkeypatch.setattr(helper_mod, "discover", lambda prefs: fake_helper)

    args = Namespace(no_aur=False)
    cmd.cmd_risk(args, None)
    out = capsys.readouterr().out
    assert "AUR" in out
    assert "myaur-pkg" in out


def test_risk_no_aur_flag_skips_helper(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cmd, "build_config", lambda *a, **k: _fake_cfg())
    monkeypatch.setattr(cmd, "classify_pending", lambda cfg, bus: [])
    monkeypatch.setattr(cmd, "preview_transaction", lambda bus: _fake_preview())

    import archward.aur.helper as helper_mod
    called = []
    monkeypatch.setattr(helper_mod, "discover", lambda prefs: called.append(1) or MagicMock())

    args = Namespace(no_aur=True)
    cmd.cmd_risk(args, None)
    assert not called, "discover() should not be called when --no-aur is set"


def test_risk_always_exits_0(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cmd, "build_config", lambda *a, **k: _fake_cfg())
    monkeypatch.setattr(cmd, "classify_pending", lambda cfg, bus: [
        _pkg("glibc", RiskLevel.HIGH),
    ])
    monkeypatch.setattr(cmd, "preview_transaction", lambda bus: _fake_preview())

    args = Namespace(no_aur=True)
    assert cmd.cmd_risk(args, None) == 0
