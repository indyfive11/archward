"""Tests for archward.aur.prefetch.fetch_pkgbuild.

Stubs subprocess.run so the tests don't actually hit aur.archlinux.org.
The fetch path itself is small (clone → read file); the test surface is
mostly: argv shape, failure modes, content round-trip.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from archward.aur import prefetch


def test_fetch_pkgbuild_returns_content_on_success(monkeypatch, tmp_path) -> None:
    captured: dict = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = list(argv)
        # Simulate git clone by creating the target dir + PKGBUILD.
        target = Path(argv[-1])
        target.mkdir(parents=True)
        (target / "PKGBUILD").write_text("pkgname=foo\nversion=1.0\n", encoding="utf-8")

        class R:
            returncode = 0
            stdout = ""
            stderr = ""
        return R()

    monkeypatch.setattr(prefetch.subprocess, "run", fake_run)

    content = prefetch.fetch_pkgbuild("foo")
    assert content is not None
    assert "pkgname=foo" in content
    # argv shape: git clone --depth=1 --quiet <url> <target>
    assert captured["argv"][:4] == ["git", "clone", "--depth=1", "--quiet"]
    assert captured["argv"][4] == "https://aur.archlinux.org/foo.git"


def test_fetch_pkgbuild_handles_clone_failure(monkeypatch) -> None:
    def fake_run(argv, **kwargs):
        raise subprocess.CalledProcessError(
            128, argv, stderr="fatal: repository 'doesnotexist' not found"
        )

    monkeypatch.setattr(prefetch.subprocess, "run", fake_run)
    assert prefetch.fetch_pkgbuild("doesnotexist") is None


def test_fetch_pkgbuild_handles_timeout(monkeypatch) -> None:
    def fake_run(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs.get("timeout", 30))

    monkeypatch.setattr(prefetch.subprocess, "run", fake_run)
    assert prefetch.fetch_pkgbuild("slow-pkg") is None


def test_fetch_pkgbuild_handles_missing_git_binary(monkeypatch) -> None:
    def fake_run(argv, **kwargs):
        raise FileNotFoundError("git not found")

    monkeypatch.setattr(prefetch.subprocess, "run", fake_run)
    assert prefetch.fetch_pkgbuild("foo") is None


def test_fetch_pkgbuild_missing_pkgbuild_in_repo(monkeypatch) -> None:
    """A successful clone with no PKGBUILD file (malformed AUR repo) returns None."""
    def fake_run(argv, **kwargs):
        target = Path(argv[-1])
        target.mkdir(parents=True)
        # NO PKGBUILD created.

        class R:
            returncode = 0
            stdout = ""
            stderr = ""
        return R()

    monkeypatch.setattr(prefetch.subprocess, "run", fake_run)
    assert prefetch.fetch_pkgbuild("malformed") is None
