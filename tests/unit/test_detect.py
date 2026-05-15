"""Detection module — pure-logic surfaces (diff_against, apply_detection)."""

from __future__ import annotations

from pathlib import Path

from archward.config.defaults import default_config
from archward.config.detect import (
    ConfigDiff,
    DetectionResult,
    apply_detection,
    diff_against,
)
from archward.system.distro import DistroInfo


def _det(
    *,
    kernels: tuple[str, ...] = (),
    helper: str | None = None,
    services: tuple[str, ...] = (),
) -> DetectionResult:
    return DetectionResult(
        distro=DistroInfo(
            id="arch",
            pretty_name="Arch Linux",
            is_arch_based=True,
            detected_via="ID",
            raw={"ID": "arch"},
        ),
        kernels=kernels,
        helper=helper,
        enabled_services=services,
        pacnew_baseline=(),
    )


def test_diff_empty_when_aligned() -> None:
    cfg = default_config()
    # Pretend we found exactly what's covered by kernel_patterns + risk.high already.
    det = _det(kernels=("linux", "linux-headers"), helper="yay", services=())
    diff = diff_against(cfg, det)
    assert diff.kernel_additions == ()
    assert diff.service_additions == ()
    assert diff.aur_disable is False


def test_unknown_kernel_proposes_addition() -> None:
    cfg = default_config()
    # A made-up kernel name that's not matched by kernel_patterns.
    det = _det(kernels=("linux-rt-custom",), helper="yay")
    diff = diff_against(cfg, det)
    assert "linux-rt-custom" in diff.kernel_additions


def test_no_helper_flips_aur_disable() -> None:
    cfg = default_config()
    # Phase 3 default: aur.enabled=True. No helper detected → propose disabling.
    det = _det(kernels=(), helper=None)
    diff = diff_against(cfg, det)
    assert diff.aur_disable is True


def test_helper_found_no_change_to_aur() -> None:
    """When a helper exists and aur.enabled is already True, no diff is proposed."""
    cfg = default_config()
    det = _det(kernels=(), helper="yay")
    diff = diff_against(cfg, det)
    assert diff.aur_disable is False


def test_apply_detection_unions_kernels() -> None:
    cfg = default_config()
    det = _det(kernels=("linux-rt-custom",))
    diff = ConfigDiff(
        kernel_additions=("linux-rt-custom",),
        service_additions=(),
        aur_disable=False,
        helper_set_to=None,
    )
    new_cfg = apply_detection(cfg, det, diff, accept_services=False)
    assert "linux-rt-custom" in new_cfg.risk.high
    # Original high entries preserved.
    for pkg in cfg.risk.high:
        assert pkg in new_cfg.risk.high


def test_apply_detection_services_opt_in() -> None:
    cfg = default_config()
    det = _det(services=("sshd.service", "NetworkManager.service"))
    diff = ConfigDiff(
        kernel_additions=(),
        service_additions=("sshd.service", "NetworkManager.service"),
        aur_disable=False,
        helper_set_to=None,
    )

    # Opt-out: no services added.
    no_change = apply_detection(cfg, det, diff, accept_services=False)
    assert no_change.services.to_verify == ()

    # Opt-in: services added.
    accepted = apply_detection(cfg, det, diff, accept_services=True)
    assert "sshd.service" in accepted.services.to_verify
    assert "NetworkManager.service" in accepted.services.to_verify


def test_apply_detection_no_diff_returns_same() -> None:
    cfg = default_config()
    det = _det()
    diff = ConfigDiff(
        kernel_additions=(), service_additions=(), aur_disable=False, helper_set_to=None
    )
    out = apply_detection(cfg, det, diff)
    assert out is cfg  # identity — no copy made


def test_detect_kernels_excludes_split_firmware(monkeypatch) -> None:
    """linux-firmware-amdgpu etc. are firmware blobs, NOT kernels — must be filtered out."""
    import subprocess

    from archward.config import detect as detect_mod

    fake_stdout = "\n".join([
        "linux",
        "linux-api-headers",
        "linux-cachyos-bore",
        "linux-cachyos-bore-headers",
        "linux-firmware",
        "linux-firmware-amdgpu",
        "linux-firmware-atheros",
        "linux-firmware-radeon",
        "linux-headers",
        "linux-docs",
        "linux-tools",
        "linux-tools-meta",
    ])

    class FakeResult:
        returncode = 0
        stdout = fake_stdout

    def fake_run(*args, **kwargs):
        return FakeResult()

    monkeypatch.setattr(subprocess, "run", fake_run)
    kernels = detect_mod.detect_kernels()
    assert "linux" in kernels
    assert "linux-cachyos-bore" in kernels
    assert "linux-cachyos-bore-headers" in kernels
    assert "linux-headers" in kernels
    assert "linux-api-headers" in kernels
    # Firmware (all variants), docs, tools — excluded.
    for excluded in (
        "linux-firmware",
        "linux-firmware-amdgpu",
        "linux-firmware-atheros",
        "linux-firmware-radeon",
        "linux-docs",
        "linux-tools",
        "linux-tools-meta",
    ):
        assert excluded not in kernels, f"{excluded} should be filtered out"


def test_diff_respects_kernel_pattern_exclude() -> None:
    """Even if a package slips through detect_kernels, the diff must respect the exclude list."""
    cfg = default_config()
    det = _det(kernels=("linux-firmware-amdgpu",))
    diff = diff_against(cfg, det)
    # Default kernel_pattern_exclude includes linux-firmware* — must not propose adding.
    assert "linux-firmware-amdgpu" not in diff.kernel_additions


# ── v0.3.3: auto-prune stale services on --detect ────────────────────────


def _cfg_with_services(*units: str):
    """Return a default_config() with services.to_verify populated to `units`."""
    from archward.config.loader import merge_partial
    from archward.models.config import ServicesConfig

    cfg = default_config()
    return merge_partial(
        cfg,
        services=ServicesConfig(to_verify=tuple(units), severity=dict(cfg.services.severity)),
    )


def test_detect_stale_services_filters_via_unit_exists(monkeypatch) -> None:
    """detect_stale_services should call unit_exists for each to_verify entry
    and return only the ones that don't exist."""
    from archward.config import detect as detect_mod

    existing = {"good.service", "another-good.service"}
    monkeypatch.setattr(
        detect_mod.system_services, "unit_exists",
        lambda u: u in existing,
    )

    cfg = _cfg_with_services("good.service", "stale.service", "another-good.service", "also-stale.service")
    stale = detect_mod.detect_stale_services(cfg)
    assert set(stale) == {"stale.service", "also-stale.service"}


def test_diff_against_populates_service_removals(monkeypatch) -> None:
    """diff_against should set ConfigDiff.service_removals."""
    from archward.config import detect as detect_mod

    monkeypatch.setattr(
        detect_mod.system_services, "unit_exists",
        lambda u: u == "kept.service",
    )

    cfg = _cfg_with_services("kept.service", "ghost.service")
    diff = diff_against(cfg, _det())
    assert diff.service_removals == ("ghost.service",)


def test_apply_detection_service_removals_opt_in(monkeypatch) -> None:
    """accept_service_removals=True must drop the stale entries."""
    from archward.config import detect as detect_mod

    monkeypatch.setattr(
        detect_mod.system_services, "unit_exists",
        lambda u: u == "kept.service",
    )

    cfg = _cfg_with_services("kept.service", "ghost.service")
    diff = diff_against(cfg, _det())

    pruned = apply_detection(cfg, _det(), diff, accept_service_removals=True)
    assert pruned.services.to_verify == ("kept.service",)


def test_apply_detection_service_removals_off_by_default(monkeypatch) -> None:
    """Default accept_service_removals=False keeps stale entries in place."""
    from archward.config import detect as detect_mod

    monkeypatch.setattr(
        detect_mod.system_services, "unit_exists",
        lambda u: u == "kept.service",
    )

    cfg = _cfg_with_services("kept.service", "ghost.service")
    diff = diff_against(cfg, _det())

    # Default (no kwarg) — removals are not applied.
    unchanged = apply_detection(cfg, _det(), diff)
    assert "ghost.service" in unchanged.services.to_verify
    assert "kept.service" in unchanged.services.to_verify

    # Explicit False — same outcome.
    unchanged2 = apply_detection(cfg, _det(), diff, accept_service_removals=False)
    assert unchanged2.services.to_verify == unchanged.services.to_verify


def test_apply_detection_additions_and_removals_compose(monkeypatch) -> None:
    """Additions and removals can land in a single apply_detection call."""
    from archward.config import detect as detect_mod

    monkeypatch.setattr(
        detect_mod.system_services, "unit_exists",
        lambda u: u in {"kept.service"},
    )

    cfg = _cfg_with_services("kept.service", "ghost.service")
    det = _det(services=("new-one.service",))
    diff = diff_against(cfg, det)
    assert diff.service_removals == ("ghost.service",)
    assert diff.service_additions == ("new-one.service",)

    applied = apply_detection(
        cfg, det, diff,
        accept_services=True, accept_service_removals=True,
    )
    assert set(applied.services.to_verify) == {"kept.service", "new-one.service"}
