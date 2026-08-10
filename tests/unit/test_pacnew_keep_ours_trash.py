"""v0.4.18 — Keep Ours parks the .pacnew in a trash dir; Edit uses sudoedit.

Pre-v0.4.18, Keep Ours was an unrecoverable one-click `sudo rm -f`, and the
edit strategy ran $EDITOR unprivileged (could read but not write /etc).
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import archward.config.paths as paths_mod
from archward.models.pacnew import PacnewAction, PacnewFile, PacnewRecommendation
from archward.pacman import pacnew as pacnew_mod


@pytest.fixture(autouse=True)
def _tmp_state(tmp_path, monkeypatch):
    monkeypatch.setattr(paths_mod, "state_dir", lambda: tmp_path / "state")


def _make_pacnew(tmp_path: Path) -> PacnewFile:
    original = tmp_path / "etc-resolved.conf"
    pacnew_path = tmp_path / "etc-resolved.conf.pacnew"
    original.write_text("live\n")
    pacnew_path.write_text("new stuff\n")
    return PacnewFile(
        path=pacnew_path,
        original_path=original,
        recommendation=PacnewRecommendation.KEEP_OURS,
        rule_pattern=None,
        note=None,
        detected_at=datetime.now(),
    )


def _real_fs_run_capture():
    """run_capture stub that actually performs cp/rm on the test filesystem."""
    recorded: list[list[str]] = []

    def stub(argv, *, strategy):
        recorded.append(list(argv))
        if argv[0] == "cp":
            shutil.copyfile(argv[2], argv[3])
            return 0, "", ""
        if argv[0] == "rm":
            Path(argv[2]).unlink(missing_ok=True)
            return 0, "", ""
        return 0, "", ""

    return stub, recorded


def test_keep_ours_parks_copy_in_trash(tmp_path, monkeypatch) -> None:
    pf = _make_pacnew(tmp_path)
    stub, recorded = _real_fs_run_capture()
    monkeypatch.setattr("archward.pacman.runner.run_capture", stub)

    info = pacnew_mod.apply_action(pf, PacnewAction.KEEP_OURS, MagicMock())

    # The .pacnew is gone from /etc...
    assert not pf.path.exists()
    # ...but its content survives in the trash dir, and the caller is told where.
    trash = pacnew_mod.pacnew_trash_dir()
    parked = list(trash.iterdir())
    assert len(parked) == 1
    assert parked[0].read_text() == "new stuff\n"
    assert info is not None and str(parked[0]) in info
    # cp ran before rm.
    assert [argv[0] for argv in recorded] == ["cp", "rm"]


def test_keep_ours_aborts_if_trash_copy_fails(tmp_path, monkeypatch) -> None:
    """If the backup copy fails, the .pacnew must NOT be removed."""
    pf = _make_pacnew(tmp_path)

    def stub(argv, *, strategy):
        if argv[0] == "cp":
            return 1, "", "cp: simulated failure"
        raise AssertionError("rm must not run after a failed trash copy")

    monkeypatch.setattr("archward.pacman.runner.run_capture", stub)

    with pytest.raises(RuntimeError, match="trash backup"):
        pacnew_mod.apply_action(pf, PacnewAction.KEEP_OURS, MagicMock())
    assert pf.path.exists()


def test_edit_uses_sudoedit_with_pinned_editor(tmp_path, monkeypatch) -> None:
    pf = _make_pacnew(tmp_path)
    runs: list[tuple[list[str], dict]] = []

    monkeypatch.setenv("VISUAL", "micro")
    monkeypatch.setattr(
        pacnew_mod.subprocess,
        "run",
        lambda argv, check, env: runs.append((argv, env)),
    )

    pacnew_mod.apply_action(pf, PacnewAction.EDIT, MagicMock())

    assert len(runs) == 1
    argv, env = runs[0]
    assert argv[0] == "sudoedit"
    assert str(pf.original_path) in argv and str(pf.path) in argv
    assert env["SUDO_EDITOR"] == "micro"
