"""Snapshot browser helpers — capture-status verification."""

from __future__ import annotations

from pathlib import Path


def test_capture_status_pass_when_identical(tmp_path: Path) -> None:
    from archward.ui.dialogs.snapshot_browser import _capture_status

    snap = tmp_path / "snap-fstab"
    live = tmp_path / "live-fstab"
    body = b"# fstab\nUUID=foo / ext4 defaults 0 1\n"
    snap.write_bytes(body)
    live.write_bytes(body)

    text, kind = _capture_status(snap, live)
    assert kind == "pass"
    assert "identical" in text


def test_capture_status_warn_when_different(tmp_path: Path) -> None:
    from archward.ui.dialogs.snapshot_browser import _capture_status

    snap = tmp_path / "snap-fstab"
    live = tmp_path / "live-fstab"
    snap.write_bytes(b"# old fstab\n")
    live.write_bytes(b"# new fstab line\n")

    text, kind = _capture_status(snap, live)
    assert kind == "warn"
    assert "Δ" in text  # delta marker
    assert "system changed" in text


def test_capture_status_fail_when_snapshot_missing(tmp_path: Path) -> None:
    from archward.ui.dialogs.snapshot_browser import _capture_status

    snap = tmp_path / "snap-fstab-missing"
    live = tmp_path / "live-fstab"
    live.write_bytes(b"# fstab\n")

    text, kind = _capture_status(snap, live)
    assert kind == "fail"
    assert "not captured" in text


def test_capture_status_warn_when_empty(tmp_path: Path) -> None:
    from archward.ui.dialogs.snapshot_browser import _capture_status

    snap = tmp_path / "snap-empty"
    live = tmp_path / "live-fstab"
    snap.write_bytes(b"")
    live.write_bytes(b"# anything\n")

    text, kind = _capture_status(snap, live)
    assert kind == "warn"
    assert "empty" in text


def test_capture_status_pass_when_live_absent(tmp_path: Path) -> None:
    from archward.ui.dialogs.snapshot_browser import _capture_status

    snap = tmp_path / "snap-fstab"
    snap.write_bytes(b"# snap\n")

    text, kind = _capture_status(snap, tmp_path / "absent")
    assert kind == "pass"
    assert "live absent" in text
