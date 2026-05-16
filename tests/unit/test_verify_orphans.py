"""Tests for v0.4.5 F2 — verify phase _orphan_check."""

from __future__ import annotations

import subprocess

import pytest

from archward.models.verify import CheckStatus
from archward.pipeline import verify_phase


def test_orphan_check_no_orphans(monkeypatch) -> None:
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: type("R", (), {"returncode": 1, "stdout": "", "stderr": ""})(),
    )
    result = verify_phase._orphan_check()
    assert result.status is CheckStatus.PASS
    assert "No orphaned" in result.message


def test_orphan_check_finds_orphans(monkeypatch) -> None:
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: type("R", (), {
            "returncode": 0,
            "stdout": "lhasa\npython-deprecated\n",
            "stderr": "",
        })(),
    )
    result = verify_phase._orphan_check()
    assert result.status is CheckStatus.WARN
    assert "2 orphaned packages" in result.message
    assert "lhasa" in result.detail
    assert "python-deprecated" in result.detail


def test_orphan_check_single_orphan_singular(monkeypatch) -> None:
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: type("R", (), {
            "returncode": 0,
            "stdout": "lhasa\n",
            "stderr": "",
        })(),
    )
    result = verify_phase._orphan_check()
    assert "1 orphaned package" in result.message
    assert "packages" not in result.message


def test_orphan_check_timeout(monkeypatch) -> None:
    def boom(*a, **k):
        raise subprocess.TimeoutExpired(["pacman"], 15)

    monkeypatch.setattr(subprocess, "run", boom)
    result = verify_phase._orphan_check()
    assert result.status is CheckStatus.WARN
    assert "timed out" in result.message


def test_orphan_check_pacman_not_found(monkeypatch) -> None:
    def boom(*a, **k):
        raise FileNotFoundError

    monkeypatch.setattr(subprocess, "run", boom)
    result = verify_phase._orphan_check()
    assert result.status is CheckStatus.PASS
