"""Tests for v0.4.3 part 5 — pacnew subcommands."""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from archward.cli_subcommands import pacnew as cmd
from archward.models.pacnew import PacnewAction, PacnewFile, PacnewRecommendation


def _patch_cfg(monkeypatch) -> None:
    cfg = MagicMock()
    cfg.pacnew = MagicMock()
    monkeypatch.setattr(cmd, "build_config", lambda *a, **k: cfg)
    monkeypatch.setattr(cmd, "build_sudo_strategy", lambda *a, **k: MagicMock())


# ── list ──────────────────────────────────────────────────────────────────


def test_list_empty(monkeypatch, capsys) -> None:
    _patch_cfg(monkeypatch)
    monkeypatch.setattr(cmd, "find_pacnew_files", lambda: [])
    args = Namespace()
    code = cmd.cmd_list(args, None)
    assert code == 0
    assert "no .pacnew files" in capsys.readouterr().out


def test_list_with_files(monkeypatch, capsys) -> None:
    from datetime import datetime
    _patch_cfg(monkeypatch)
    monkeypatch.setattr(
        cmd, "find_pacnew_files",
        lambda: [Path("/etc/sshd_config.pacnew"), Path("/etc/pacman.conf.pacnew")],
    )

    def fake_classify(path, cfg):
        return PacnewFile(
            path=path,
            original_path=Path(str(path).removesuffix(".pacnew")),
            recommendation=PacnewRecommendation.REVIEW_NEEDED,
            rule_pattern="*sshd_config*",
            note="SSH daemon config — review carefully",
            detected_at=datetime.now(),
        )

    monkeypatch.setattr(cmd, "classify", fake_classify)

    args = Namespace()
    code = cmd.cmd_list(args, None)
    assert code == 0
    out = capsys.readouterr().out
    assert "2 .pacnew file(s)" in out
    assert "sshd_config.pacnew" in out
    assert "review_needed" in out


# ── diff ──────────────────────────────────────────────────────────────────


def test_diff_pacnew_path_form(tmp_path, monkeypatch, capsys) -> None:
    """Pass the .pacnew path — diff resolves the live sibling automatically."""
    live = tmp_path / "sshd_config"
    pacnew = tmp_path / "sshd_config.pacnew"
    live.write_text("Port 22\n")
    pacnew.write_text("Port 22\nPermitRootLogin no\n")

    args = Namespace(path=str(pacnew))
    code = cmd.cmd_diff(args, None)
    assert code == 0
    out = capsys.readouterr().out
    assert "PermitRootLogin" in out


def test_diff_live_path_form(tmp_path, capsys) -> None:
    """Pass the LIVE path — same result, diff finds the .pacnew sibling."""
    live = tmp_path / "config"
    pacnew = tmp_path / "config.pacnew"
    live.write_text("a=1\n")
    pacnew.write_text("a=2\n")

    args = Namespace(path=str(live))
    code = cmd.cmd_diff(args, None)
    assert code == 0


def test_diff_missing_pacnew_returns_3(tmp_path, capsys) -> None:
    """A path with no matching .pacnew returns 3."""
    args = Namespace(path=str(tmp_path / "nonexistent"))
    code = cmd.cmd_diff(args, None)
    assert code == 3
    assert "does not exist" in capsys.readouterr().err


def test_diff_identical_files_reports_so(tmp_path, capsys) -> None:
    live = tmp_path / "same"
    pacnew = tmp_path / "same.pacnew"
    live.write_text("identical\n")
    pacnew.write_text("identical\n")

    args = Namespace(path=str(pacnew))
    code = cmd.cmd_diff(args, None)
    assert code == 0
    assert "no differences" in capsys.readouterr().out


# ── apply ─────────────────────────────────────────────────────────────────


def test_apply_keep_ours(tmp_path, monkeypatch, capsys) -> None:
    """Apply keep_ours → delegates to apply_action with KEEP_OURS."""
    _patch_cfg(monkeypatch)
    live = tmp_path / "config"
    pacnew = tmp_path / "config.pacnew"
    live.write_text("ours\n")
    pacnew.write_text("theirs\n")

    from datetime import datetime

    def fake_classify(path, cfg):
        return PacnewFile(
            path=path, original_path=path.with_suffix(""),
            recommendation=PacnewRecommendation.REVIEW_NEEDED,
            rule_pattern=None, note=None,
            detected_at=datetime.now(),
        )

    monkeypatch.setattr(cmd, "classify", fake_classify)

    called_with = {}

    def fake_apply(pf, action, strategy):
        called_with["action"] = action
        called_with["path"] = pf.path

    monkeypatch.setattr(cmd, "apply_action", fake_apply)

    args = Namespace(path=str(pacnew), strategy="keep_ours")
    code = cmd.cmd_apply(args, None)
    assert code == 0
    assert called_with["action"] is PacnewAction.KEEP_OURS


def test_apply_failure_surfaces_exit_1(tmp_path, monkeypatch, capsys) -> None:
    _patch_cfg(monkeypatch)
    pacnew = tmp_path / "config.pacnew"
    pacnew.write_text("x\n")

    from datetime import datetime

    monkeypatch.setattr(cmd, "classify", lambda p, c: PacnewFile(
        path=p, original_path=p.with_suffix(""),
        recommendation=PacnewRecommendation.TAKE_NEW,
        rule_pattern=None, note=None,
        detected_at=datetime.now(),
    ))

    def raising_apply(pf, action, strategy):
        raise RuntimeError("mv failed: permission denied")

    monkeypatch.setattr(cmd, "apply_action", raising_apply)

    args = Namespace(path=str(pacnew), strategy="take_new")
    code = cmd.cmd_apply(args, None)
    assert code == 1
    assert "mv failed" in capsys.readouterr().err


def test_apply_invalid_strategy_caught_by_argparse() -> None:
    """argparse rejects unknown --strategy values BEFORE cmd_apply is called.

    cmd_apply itself only sees the four valid choices, so we don't need a
    runtime check here — the dispatch tests already cover the argparse
    refusal path. This is a smoke that the conversion to enum works.
    """
    args = Namespace(path="/tmp/whatever", strategy="leave")
    action = PacnewAction(args.strategy)
    assert action is PacnewAction.LEAVE
