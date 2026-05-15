"""Tests for v0.4.4 F3 — verify-phase `_boot_integrity_check`.

Builds a tmp /boot with controlled mtimes. Asserts the check FAILs
only on the one unambiguous signal — an initramfs OLDER than its
kernel — and SKIPs (PASS) every indeterminate case (no /boot, no
kernel, no flavour-named initramfs → dracut-kver/UKI). It does NOT
look at grub.cfg: with stable kernel filenames grub.cfg legitimately
predates the kernel on a perfectly bootable system, so a mtime check
there is a guaranteed false positive (regression: caught on a live
EndeavourOS box where grub.cfg was 2 months older than the kernel and
the machine booted fine).
"""

from __future__ import annotations

import os
from pathlib import Path

from archward.models.verify import CheckStatus
from archward.pipeline import verify_phase


def _touch(p: Path, mtime: float) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")
    os.utime(p, (mtime, mtime))


def test_no_boot_dir_skips(tmp_path) -> None:
    chk = verify_phase._boot_integrity_check(tmp_path / "nope")
    assert chk.status is CheckStatus.PASS
    assert "skipped" in chk.message


def test_no_kernel_image_skips(tmp_path) -> None:
    (tmp_path).mkdir(exist_ok=True)
    chk = verify_phase._boot_integrity_check(tmp_path)
    assert chk.status is CheckStatus.PASS
    assert "skipped" in chk.message


def test_fresh_initramfs_passes(tmp_path) -> None:
    _touch(tmp_path / "vmlinuz-linux", 1000)
    _touch(tmp_path / "initramfs-linux.img", 1005)  # newer than kernel
    chk = verify_phase._boot_integrity_check(tmp_path)
    assert chk.status is CheckStatus.PASS
    assert "initramfs newer than kernel" in chk.message


def test_stale_initramfs_fails(tmp_path) -> None:
    _touch(tmp_path / "vmlinuz-linux", 2000)        # kernel newer
    _touch(tmp_path / "initramfs-linux.img", 1000)  # initramfs older
    chk = verify_phase._boot_integrity_check(tmp_path)
    assert chk.status is CheckStatus.FAIL
    assert "initramfs not regenerated" in chk.detail
    assert chk.name == "boot-integrity"


def test_uki_present_skips(tmp_path) -> None:
    """A UKI bundles the initramfs; a leftover stale standalone
    initramfs-<flavour>.img must NOT trigger a false FAIL."""
    _touch(tmp_path / "vmlinuz-linux", 9000)
    _touch(tmp_path / "initramfs-linux.img", 1)  # ancient/stale standalone
    uki = tmp_path / "EFI" / "Linux"
    uki.mkdir(parents=True)
    (uki / "arch-linux.efi").write_bytes(b"UKI")
    chk = verify_phase._boot_integrity_check(tmp_path)
    assert chk.status is CheckStatus.PASS
    assert "Unified Kernel Image" in chk.message


def test_no_matching_initramfs_skips(tmp_path) -> None:
    # vmlinuz present but no initramfs-linux.img → dracut/UKI/exotic.
    _touch(tmp_path / "vmlinuz-linux", 1000)
    chk = verify_phase._boot_integrity_check(tmp_path)
    assert chk.status is CheckStatus.PASS
    assert "skipped" in chk.message


def test_old_grub_cfg_does_not_fail(tmp_path) -> None:
    """Regression (live EndeavourOS): grub.cfg months older than the
    kernel + a fresh initramfs is a perfectly bootable system. The
    check must NOT look at grub.cfg mtime."""
    _touch(tmp_path / "vmlinuz-linux", 5000)
    _touch(tmp_path / "initramfs-linux.img", 5001)   # initramfs fresh
    _touch(tmp_path / "grub" / "grub.cfg", 1000)     # grub.cfg ancient
    chk = verify_phase._boot_integrity_check(tmp_path)
    assert chk.status is CheckStatus.PASS
    assert "grub" not in (chk.detail or "").lower()


def test_real_multiflavour_layout_passes(tmp_path) -> None:
    """The exact shape of the live box that mis-fired: three stable-name
    kernels, each with a newer initramfs, ancient grub.cfg → PASS."""
    for fl in ("linux", "linux-cachyos", "linux-cachyos-bore"):
        _touch(tmp_path / f"vmlinuz-{fl}", 9000)
        _touch(tmp_path / f"initramfs-{fl}.img", 9010)
        _touch(tmp_path / f"initramfs-{fl}-fallback.img", 9011)
    _touch(tmp_path / "grub" / "grub.cfg", 100)
    chk = verify_phase._boot_integrity_check(tmp_path)
    assert chk.status is CheckStatus.PASS
    assert "3 kernel(s)" in chk.message


def test_multiple_kernels_one_stale_fails(tmp_path) -> None:
    _touch(tmp_path / "vmlinuz-linux", 1000)
    _touch(tmp_path / "initramfs-linux.img", 1001)        # fresh
    _touch(tmp_path / "vmlinuz-linux-lts", 2000)
    _touch(tmp_path / "initramfs-linux-lts.img", 1500)    # stale
    chk = verify_phase._boot_integrity_check(tmp_path)
    assert chk.status is CheckStatus.FAIL
    assert "linux-lts" in chk.detail
