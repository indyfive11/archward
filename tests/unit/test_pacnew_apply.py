"""Tests for v0.4.1 F3 — atomic pacnew Take-New with recovery path.

Regression: if chown or chmod failed AFTER the `.pacnew → original` mv
already succeeded, the target file was left with the .pacnew's default
perms (typically 644 root:root). For files like sshd_config (mode 600)
this is a silent permission downgrade — a clear security regression.
The recovery path copies the `.pre-archward.bak` over the live target
on partial failure, restoring the pre-op state.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from archward.models.pacnew import PacnewAction, PacnewFile, PacnewRecommendation
from archward.pacman import pacnew as pacnew_mod


def _make_pacnew(tmp_path: Path) -> PacnewFile:
    original = tmp_path / "etc-sshd-config"
    pacnew_path = tmp_path / "etc-sshd-config.pacnew"
    original.write_text("Port 22\nPermitRootLogin no\n")
    pacnew_path.write_text("Port 22\nPermitRootLogin prohibit-password\n")
    return PacnewFile(
        path=pacnew_path,
        original_path=original,
        recommendation=PacnewRecommendation.REVIEW_NEEDED,
        rule_pattern=None,
        note=None,
        detected_at=datetime.now(),
    )


def _make_run_capture_stub(failures: dict[str, tuple[int, str]]):
    """Build a fake run_capture that returns (code, '', err) per command prefix.

    `failures` maps argv[0] (e.g. 'chmod') → (returncode, stderr). Anything
    not in `failures` returns (0, '', '').
    """
    recorded: list[list[str]] = []

    def stub(argv, *, strategy):
        recorded.append(list(argv))
        cmd = argv[0]
        # 'cp -a backup target' is the recovery path — distinguish it from
        # the initial backup cp by checking whether target ends with .bak.
        if cmd == "cp":
            # backup direction: cp -a <orig> <orig.bak>
            # recovery direction: cp -a <orig.bak> <orig>
            src, dst = argv[2], argv[3]
            if dst.endswith(".pre-archward.bak"):
                key = "cp-backup"
            else:
                key = "cp-recover"
            if key in failures:
                code, err = failures[key]
                return code, "", err
            # Real file copy so the test can verify recovery semantics.
            import shutil as _shutil
            _shutil.copyfile(src, dst)
            return 0, "", ""
        if cmd == "mv":
            src, dst = argv[1], argv[2]
            if cmd in failures:
                code, err = failures[cmd]
                return code, "", err
            import shutil as _shutil
            _shutil.move(src, dst)
            return 0, "", ""
        if cmd in failures:
            code, err = failures[cmd]
            return code, "", err
        return 0, "", ""

    return stub, recorded


def test_chmod_failure_restores_from_backup(tmp_path, monkeypatch) -> None:
    """If chmod returns non-zero, the original file is restored from .bak.

    The error message must mention recovery so the user knows their file
    is back to its pre-op state."""
    pf = _make_pacnew(tmp_path)
    original_content = pf.original_path.read_text()

    stub, recorded = _make_run_capture_stub(
        failures={"chmod": (1, "chmod: simulated failure")}
    )
    monkeypatch.setattr(
        "archward.pacman.runner.run_capture", stub
    )

    with pytest.raises(RuntimeError, match="chmod failed") as excinfo:
        pacnew_mod._apply_take_new(pf, strategy=MagicMock())
    assert "restored from" in str(excinfo.value)

    # The live original file is back to the pre-op content (because the
    # recovery cp -a backup target overwrote the moved-in .pacnew).
    assert pf.original_path.read_text() == original_content
    # The backup file still exists (it's the recovery source, retained
    # for forensic / further-restore needs).
    backup = pf.original_path.with_suffix(pf.original_path.suffix + ".pre-archward.bak")
    assert backup.exists()


def test_chown_failure_restores_from_backup(tmp_path, monkeypatch) -> None:
    """Same shape as chmod failure but on the chown step (earlier in the chain)."""
    pf = _make_pacnew(tmp_path)
    original_content = pf.original_path.read_text()

    stub, _ = _make_run_capture_stub(
        failures={"chown": (1, "chown: simulated failure")}
    )
    monkeypatch.setattr("archward.pacman.runner.run_capture", stub)

    with pytest.raises(RuntimeError, match="chown failed") as excinfo:
        pacnew_mod._apply_take_new(pf, strategy=MagicMock())
    assert "restored from" in str(excinfo.value)

    assert pf.original_path.read_text() == original_content


def test_recovery_failure_surfaces_both_errors(tmp_path, monkeypatch) -> None:
    """If chmod fails AND the recovery cp also fails, the error message warns
    the user that the file may have wrong mode and points at the backup."""
    pf = _make_pacnew(tmp_path)

    stub, _ = _make_run_capture_stub(
        failures={
            "chmod": (1, "chmod: simulated failure"),
            "cp-recover": (1, "cp -a recovery: simulated failure"),
        }
    )
    monkeypatch.setattr("archward.pacman.runner.run_capture", stub)

    with pytest.raises(RuntimeError, match="Recovery .* ALSO FAILED") as excinfo:
        pacnew_mod._apply_take_new(pf, strategy=MagicMock())
    msg = str(excinfo.value)
    assert "restore manually from" in msg
    assert ".pre-archward.bak" in msg


def test_happy_path_no_recovery_attempt(tmp_path, monkeypatch) -> None:
    """If everything succeeds, the recovery cp is NEVER invoked."""
    pf = _make_pacnew(tmp_path)

    stub, recorded = _make_run_capture_stub(failures={})
    monkeypatch.setattr("archward.pacman.runner.run_capture", stub)

    pacnew_mod._apply_take_new(pf, strategy=MagicMock())

    # No recovery cp invocation (target wouldn't end with .bak in any cp call).
    cps = [argv for argv in recorded if argv[0] == "cp"]
    # Only the initial backup cp should fire (target ends with .bak).
    assert all(argv[3].endswith(".pre-archward.bak") for argv in cps)
