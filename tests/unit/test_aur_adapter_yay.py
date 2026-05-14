"""yay adapter — parsing -Qua output."""

from __future__ import annotations

import subprocess

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
