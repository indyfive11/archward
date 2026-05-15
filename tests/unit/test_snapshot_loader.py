"""Tests for v0.4.3 part 1 — load_snapshot_from_disk.

The CLI's `archward verify` / `archward rollback ...` subcommands all
need a Snapshot object reconstructed from an existing on-disk dir.
This module verifies the loader handles:
  - Happy path with all per-section files present.
  - Missing `.timestamp` marker (treat as not-a-snapshot).
  - Partial dir (some sections missing).
  - Single-line .timestamp with whitespace / trailing newline.
  - Bad .timestamp content.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from archward.models.snapshot import Snapshot, SnapshotMeta
from archward.pipeline.snapshot import load_snapshot_from_disk


def _build_full_snapshot(root: Path, ts_epoch: int = 1747200000) -> Path:
    """Create a complete on-disk snapshot fixture and return its path."""
    snap = root / "2026-05-15_134116"
    snap.mkdir()
    (snap / ".timestamp").write_text(f"{ts_epoch}\n")
    (snap / ".human-timestamp").write_text(
        datetime.fromtimestamp(ts_epoch).isoformat() + "\n"
    )

    sysd = snap / "system"
    sysd.mkdir()
    (sysd / "kernel-running.txt").write_text("6.13.4-arch1-1\n")
    (sysd / "helper.txt").write_text("yay\n")
    (sysd / "os-release.txt").write_text(
        'NAME="EndeavourOS Linux"\nID=endeavouros\nID_LIKE=arch\n'
    )

    pkgd = snap / "packages"
    pkgd.mkdir()
    (pkgd / "explicit.txt").write_text("base\nlinux\n")
    (pkgd / "all.txt").write_text("base 1.0\nlinux 6.13.4-arch1-1\n")
    (pkgd / "aur.txt").write_text("yay 12.4.2\n")
    (pkgd / "critical.txt").write_text("glibc 2.42-3\nlinux 6.13.4-arch1-1\n")

    svcd = snap / "services"
    svcd.mkdir()
    (svcd / "running.txt").write_text("sshd.service running\n")
    (svcd / "enabled.txt").write_text("sshd.service enabled\n")
    (svcd / "to-verify-status.txt").write_text("sshd.service active\n")

    cfgd = snap / "configs"
    cfgd.mkdir()
    (cfgd / "pacman.conf").write_text("# stub\n")
    (cfgd / "fstab").write_text("# stub\n")
    (cfgd / "sshd_config.d.tar.gz").write_bytes(b"\x1f\x8b\x08")

    return snap


def test_load_full_snapshot_round_trip(tmp_path: Path) -> None:
    """All fields reconstruct correctly when every per-section file is present."""
    ts = int(datetime.now().timestamp()) - 600  # 10 minutes ago
    snap_path = _build_full_snapshot(tmp_path, ts_epoch=ts)

    snap = load_snapshot_from_disk(snap_path)

    assert snap is not None
    assert isinstance(snap, Snapshot)
    assert isinstance(snap.meta, SnapshotMeta)
    assert snap.meta.snapshot_id == "2026-05-15_134116"
    assert snap.meta.path == snap_path
    assert snap.meta.distro_id == "endeavouros"
    assert snap.meta.kernel_release == "6.13.4-arch1-1"
    assert snap.meta.helper_detected == "yay"
    assert snap.meta.free_disk_gb == 0  # not captured as a typed value
    # Age should be ~600s, allow a small wiggle (test wall-clock).
    assert 580 <= snap.age_seconds <= 620

    # Package files mapping
    assert set(snap.package_files.keys()) == {"explicit", "all", "aur", "critical"}
    assert snap.package_files["critical"].name == "critical.txt"

    # Service files mapping
    assert set(snap.service_files.keys()) == {"running", "enabled", "to-verify-status"}

    # Config files — sorted tuple of every file in configs/
    assert len(snap.config_files) == 3
    assert all(p.parent.name == "configs" for p in snap.config_files)


def test_missing_timestamp_returns_none(tmp_path: Path) -> None:
    """A directory without .timestamp isn't a complete snapshot — load returns None."""
    half_built = tmp_path / "2026-05-15_140000"
    half_built.mkdir()
    (half_built / "configs").mkdir()
    # No .timestamp marker — this is what partial-failure cleanup leaves behind
    # (or what we'd see if someone manually rm-fed the marker).

    assert load_snapshot_from_disk(half_built) is None


def test_unreadable_timestamp_returns_none(tmp_path: Path) -> None:
    """A .timestamp containing garbage falls back to None rather than crashing."""
    snap = tmp_path / "bad_timestamp"
    snap.mkdir()
    (snap / ".timestamp").write_text("not a number\n")

    assert load_snapshot_from_disk(snap) is None


def test_partial_snapshot_with_missing_sections(tmp_path: Path) -> None:
    """Missing system/* files yield empty strings, not crashes."""
    snap = tmp_path / "2026-05-15_141100"
    snap.mkdir()
    (snap / ".timestamp").write_text(f"{int(datetime.now().timestamp())}\n")
    # Deliberately omit system/, packages/, services/, configs/.

    loaded = load_snapshot_from_disk(snap)

    assert loaded is not None
    assert loaded.meta.kernel_release == ""
    assert loaded.meta.distro_id == ""
    assert loaded.meta.helper_detected is None
    assert loaded.package_files == {}
    assert loaded.service_files == {}
    assert loaded.config_files == ()


def test_timestamp_with_whitespace_parsed_correctly(tmp_path: Path) -> None:
    """A .timestamp file with leading/trailing whitespace still parses."""
    snap = tmp_path / "2026-05-15_142200"
    snap.mkdir()
    (snap / ".timestamp").write_text("  1747200000  \n\n")

    loaded = load_snapshot_from_disk(snap)
    assert loaded is not None
    assert int(loaded.meta.created_at.timestamp()) == 1747200000


def test_os_release_with_quoted_id(tmp_path: Path) -> None:
    """ID=\"value\" (quoted) is unwrapped; ID=value (unquoted) also works."""
    snap = tmp_path / "2026-05-15_143300"
    snap.mkdir()
    (snap / ".timestamp").write_text(f"{int(datetime.now().timestamp())}\n")
    sysd = snap / "system"
    sysd.mkdir()
    (sysd / "os-release.txt").write_text('ID="arch"\nID_LIKE=arch\n')

    loaded = load_snapshot_from_disk(snap)
    assert loaded is not None
    assert loaded.meta.distro_id == "arch"


def test_age_is_non_negative_on_future_timestamp(tmp_path: Path) -> None:
    """A .timestamp in the future (clock skew) yields age 0, not negative."""
    snap = tmp_path / "2026-05-15_144400"
    snap.mkdir()
    future = int(datetime.now().timestamp()) + 3600  # 1h in future
    (snap / ".timestamp").write_text(f"{future}\n")

    loaded = load_snapshot_from_disk(snap)
    assert loaded is not None
    assert loaded.age_seconds == 0
