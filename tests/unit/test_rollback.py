"""Rollback primitives — parsers, cache lookup, perm-preserving restore."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from archward.pipeline.rollback import (
    RollbackOp,
    find_package_in_cache,
    list_snapshot_configs,
    parse_critical_packages,
)


# ── parse_critical_packages ──────────────────────────────────────────────


def test_parse_critical_packages_handles_real_format(tmp_path: Path) -> None:
    pkgs_dir = tmp_path / "packages"
    pkgs_dir.mkdir()
    (pkgs_dir / "critical.txt").write_text(
        "=== Critical package versions pre-update ===\n"
        "linux: 7.0.5.arch1-1\n"
        "glibc: 2.40-1\n"
        "openssl: 3.5.4-1\n"
        "missing-pkg: not installed\n"
        "\n"
        "=== AUR / foreign packages ===\n"
        "radarr 6.0.4.10291-1\n"
    )
    result = parse_critical_packages(tmp_path)
    assert ("linux", "7.0.5.arch1-1") in result
    assert ("glibc", "2.40-1") in result
    assert ("openssl", "3.5.4-1") in result
    # "not installed" entries skipped.
    assert all(name != "missing-pkg" for name, _ in result)
    # AUR section (space-separated, not colon-separated) skipped.
    assert all(name != "radarr" for name, _ in result)


def test_parse_critical_packages_missing_file(tmp_path: Path) -> None:
    assert parse_critical_packages(tmp_path) == []


# ── list_snapshot_configs ─────────────────────────────────────────────────


def test_list_snapshot_configs_maps_filenames(tmp_path: Path) -> None:
    configs = tmp_path / "configs"
    configs.mkdir()
    (configs / "pacman.conf").write_text("[options]\n")
    (configs / "grub-default").write_text('GRUB_TIMEOUT="5"\n')
    (configs / "sshd_config").write_text("Port 22\n")
    # Missing files don't fail.
    out = list_snapshot_configs(tmp_path)
    targets = {rel for rel, _ in out}
    assert "etc/pacman.conf" in targets
    assert "etc/default/grub" in targets
    assert "etc/ssh/sshd_config" in targets
    # Files we didn't create aren't reported.
    assert "etc/fstab" not in targets


def test_list_snapshot_configs_empty_dir(tmp_path: Path) -> None:
    assert list_snapshot_configs(tmp_path) == []


# ── find_package_in_cache ─────────────────────────────────────────────────


def test_find_package_in_cache_exact_match(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    # Real cache filenames have arch suffixes.
    (cache / "linux-7.0.5.arch1-1-x86_64.pkg.tar.zst").write_bytes(b"")
    (cache / "linux-7.0.6.arch1-1-x86_64.pkg.tar.zst").write_bytes(b"")
    found = find_package_in_cache("linux", "7.0.5.arch1-1", cache_dir=cache)
    assert found is not None
    assert "7.0.5.arch1-1" in found.name


def test_find_package_in_cache_version_missing(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "linux-7.0.5.arch1-1-x86_64.pkg.tar.zst").write_bytes(b"")
    assert find_package_in_cache("linux", "9.0.0", cache_dir=cache) is None


def test_find_package_in_cache_no_cache_dir(tmp_path: Path) -> None:
    assert find_package_in_cache("linux", "7.0.5", cache_dir=tmp_path / "nonexistent") is None


def test_find_package_doesnt_match_partial_name(tmp_path: Path) -> None:
    """`linux` must not match `linux-headers` or `linux-firmware`."""
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "linux-headers-7.0.5.arch1-1-x86_64.pkg.tar.zst").write_bytes(b"")
    (cache / "linux-firmware-20260501-1-any.pkg.tar.zst").write_bytes(b"")
    # Looking for "linux" at version "7.0.5.arch1-1" — no exact-name match exists.
    assert find_package_in_cache("linux", "7.0.5.arch1-1", cache_dir=cache) is None


def test_find_package_handles_zst_xz_gz(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "foo-1.0-1-any.pkg.tar.xz").write_bytes(b"")
    assert find_package_in_cache("foo", "1.0-1", cache_dir=cache) is not None


# ── RollbackOp dataclass ──────────────────────────────────────────────────


def test_rollback_op_is_frozen() -> None:
    op = RollbackOp(
        kind="restore_config",
        target="/etc/pacman.conf",
        from_version=None,
        to_version=None,
        snapshot_path=Path("/tmp/snapshot"),
    )
    with pytest.raises(Exception):  # FrozenInstanceError
        op.target = "/etc/other"  # type: ignore[misc]
