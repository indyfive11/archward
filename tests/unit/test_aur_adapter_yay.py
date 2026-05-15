"""yay adapter — parsing -Qua output."""

from __future__ import annotations

import subprocess

from archward.aur.adapters import _pacman_like
from archward.aur.adapters.yay import YayAdapter


def test_list_pending_parses_yay_output(monkeypatch) -> None:
    fake_stdout = (
        "jellyfin-git 10.12.0.r123.gabc 10.12.0.r130.gdef -> 10.12.0.r130.gdef\n"
        # yay -Qua actually outputs the "pkg old -> new" form (3 fields after split,
        # arrow separator). Use the canonical line shape:
        "radarr 5.27.0.10122-1 -> 5.28.0.10200-1\n"
        "sonarr 4.0.16.2967-1 -> 4.1.0.3015-1\n"
        "coolercontrol-bin 2.3.0-1 -> 2.4.0-1\n"
    )

    class FakeResult:
        returncode = 0
        stdout = fake_stdout

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeResult())

    pending = YayAdapter().list_pending()
    # The first line is malformed (5 fields) so it's skipped; the other 3 parse.
    assert ("radarr", "5.27.0.10122-1", "5.28.0.10200-1") in pending
    assert ("sonarr", "4.0.16.2967-1", "4.1.0.3015-1") in pending
    assert ("coolercontrol-bin", "2.3.0-1", "2.4.0-1") in pending
    assert len(pending) == 3


def test_list_pending_empty_output(monkeypatch) -> None:
    class FakeResult:
        returncode = 0
        stdout = ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeResult())
    assert YayAdapter().list_pending() == []


def test_list_pending_missing_binary(monkeypatch) -> None:
    def raise_fnf(*a, **k):
        raise FileNotFoundError()

    monkeypatch.setattr(subprocess, "run", raise_fnf)
    assert YayAdapter().list_pending() == []


def _capture_argv(monkeypatch) -> dict:
    """Stub run_streaming to capture the argv it was called with."""
    captured = {}

    def fake(argv, **kwargs):
        captured["argv"] = list(argv)
        captured["kwargs"] = kwargs
        return 0, []

    monkeypatch.setattr(_pacman_like, "run_streaming", fake)
    return captured


def test_run_update_default_noconfirm_present(monkeypatch) -> None:
    captured = _capture_argv(monkeypatch)
    YayAdapter().run_update(ignore=[], strategy=None, bus=None, cancel_event=None)
    assert "--noconfirm" in captured["argv"]
    assert "--editmenu=false" not in captured["argv"]
    assert captured["kwargs"].get("prompt_provider") is None


def test_run_update_interactive_drops_noconfirm_and_suppresses_menus(monkeypatch) -> None:
    captured = _capture_argv(monkeypatch)

    def fake_provider(line, kind):  # noqa: ARG001
        return "Y"

    YayAdapter().run_update(
        ignore=[],
        strategy=None,
        bus=None,
        cancel_event=None,
        noconfirm=False,
        prompt_provider=fake_provider,
    )
    argv = captured["argv"]
    assert "--noconfirm" not in argv
    # F3 handles PKGBUILD review in-GUI; yay's $EDITOR menus stay suppressed.
    assert "--editmenu=false" in argv
    assert "--diffmenu=false" in argv
    assert "--cleanmenu=false" in argv
    assert captured["kwargs"].get("prompt_provider") is fake_provider


def test_run_update_ignore_packages_passes_through(monkeypatch) -> None:
    captured = _capture_argv(monkeypatch)
    YayAdapter().run_update(
        ignore=["foo", "bar"], strategy=None, bus=None, cancel_event=None
    )
    argv = captured["argv"]
    # Two --ignore/value pairs interleaved
    assert argv.count("--ignore") == 2
    assert "foo" in argv
    assert "bar" in argv


def test_paru_uses_skipreview_in_interactive_mode(monkeypatch) -> None:
    """paru's PKGBUILD-review menu flag is --skipreview, NOT yay's
    three-flag set. Regression guard so we don't accidentally pass yay
    flags to paru when one of these helpers is the resolved one."""
    from archward.aur.adapters.paru import ParuAdapter
    captured = _capture_argv(monkeypatch)
    ParuAdapter().run_update(
        ignore=[],
        strategy=None,
        bus=None,
        cancel_event=None,
        noconfirm=False,
    )
    argv = captured["argv"]
    assert "--skipreview" in argv
    # The yay-specific flags must NOT leak into the paru invocation.
    assert "--editmenu=false" not in argv
    assert "--diffmenu=false" not in argv
    assert "--cleanmenu=false" not in argv
