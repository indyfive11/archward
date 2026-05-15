"""Auto-detection for first-run config bootstrap and Preferences "Re-detect".

Runs in 3 contexts only:
  1. First-launch bootstrap (when no config file exists)
  2. Preferences "Re-detect" button (v2/Phase 6)
  3. `archward --detect` CLI flag (this phase)

Never runs on the hot pipeline path.

Merge semantics (per PLAN.md §Auto-detection):
  - risk.high — UNION with detected kernels; never remove user entries.
  - services.to_verify — proposed as a diff; user opts in.
  - aur.helper_preference — left alone; flip aur.enabled=false only if no helper
    found AND user hasn't explicitly enabled.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from archward.system import services as system_services
from archward.models.config import (
    AurConfig,
    ConfigModel,
    RiskConfig,
    ServicesConfig,
)
from archward.pacman.pacnew import find_pacnew_files
from archward.system.distro import DistroInfo, detect_distro

log = logging.getLogger(__name__)

# Kernel packages: pacman -Qq filtered to ^linux(-|$), excluding non-bootable variants.
# Distros ship firmware as `linux-firmware`, `linux-firmware-amdgpu`, etc — split packages
# share the prefix but aren't kernels. _KERNEL_EXCLUDE_RE catches `-firmware`, `-firmware-*`,
# `-docs`, `-doc`, `-tools`, `-tools-meta` anywhere in the suffix chain.
_KERNEL_NAME_RE = re.compile(r"^linux(?:-|$)")
_KERNEL_EXCLUDE_RE = re.compile(r"-(firmware|docs?|tools(?:-meta)?)(?:-|$)")

# Services worth surfacing for the user to opt into verifying. Anything in user
# scope (UID-prefixed templates, getty@, dbus per-user) is filtered out.
_SERVICE_FILTERS = (
    re.compile(r"^getty@"),
    re.compile(r"^systemd-"),
    re.compile(r"^dbus-org\."),
    re.compile(r"^user@"),
    re.compile(r"@\.service$"),  # template services (no instance)
)


@dataclass(frozen=True)
class DetectionResult:
    distro: DistroInfo
    kernels: tuple[str, ...]
    helper: str | None
    enabled_services: tuple[str, ...]
    pacnew_baseline: tuple[Path, ...]


def detect_kernels() -> tuple[str, ...]:
    """Return installed kernel package names (e.g. ('linux', 'linux-lts'))."""
    try:
        r = subprocess.run(
            ["pacman", "-Qq"], check=False, capture_output=True, text=True
        )
    except FileNotFoundError:
        log.warning("pacman binary not found — cannot detect kernels")
        return ()
    if r.returncode != 0:
        return ()
    kernels: list[str] = []
    for line in r.stdout.splitlines():
        name = line.strip()
        if not _KERNEL_NAME_RE.match(name):
            continue
        if _KERNEL_EXCLUDE_RE.search(name):
            continue
        # linux-headers IS a kernel-adjacent package — we want it in risk.high too.
        kernels.append(name)
    return tuple(kernels)


def detect_aur_helper(preference: tuple[str, ...] = ("yay", "paru", "aurutils")) -> str | None:
    """First binary in `preference` that's on PATH wins, else None."""
    for helper in preference:
        if shutil.which(helper):
            return helper
    return None


def detect_active_enabled_services() -> tuple[str, ...]:
    """Return services that are enabled AND currently active, minus noise.

    Filters out user-scope, getty@*, systemd-internal*, and unparameterized templates.
    The returned list is what archward proposes to verify after updates — the user
    opts in to specific entries via Preferences (or `--detect --yes`).
    """
    try:
        enabled = subprocess.run(
            [
                "systemctl",
                "list-unit-files",
                "--state=enabled",
                "--type=service",
                "--no-pager",
                "--no-legend",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return ()
    if enabled.returncode != 0:
        return ()

    candidates: list[str] = []
    for line in enabled.stdout.splitlines():
        parts = line.split()
        if not parts:
            continue
        unit = parts[0]
        if not unit.endswith(".service"):
            continue
        if any(filt.search(unit) for filt in _SERVICE_FILTERS):
            continue
        candidates.append(unit)

    # Intersect with currently-active state — surface only running services so
    # the user's opt-in list doesn't include things that are enabled-but-failed.
    active: list[str] = []
    for unit in candidates:
        r = subprocess.run(
            ["systemctl", "is-active", "--quiet", unit], check=False, capture_output=True
        )
        if r.returncode == 0:
            active.append(unit)

    return tuple(active)


def detect_pacnew_baseline() -> tuple[Path, ...]:
    """Pre-existing .pacnew files (so the first verify ignores stale ones)."""
    return tuple(find_pacnew_files())


def run_full_detection() -> DetectionResult:
    """Run all detectors. Pure observation — does not modify config."""
    info = detect_distro()
    kernels = detect_kernels()
    helper = detect_aur_helper()
    services = detect_active_enabled_services()
    baseline = detect_pacnew_baseline()
    return DetectionResult(
        distro=info,
        kernels=kernels,
        helper=helper,
        enabled_services=services,
        pacnew_baseline=baseline,
    )


@dataclass(frozen=True)
class ConfigDiff:
    """What apply_detection() proposes to change. Empty tuples mean no change."""

    kernel_additions: tuple[str, ...]
    service_additions: tuple[str, ...]
    aur_disable: bool  # True if aur.enabled should flip to False
    helper_set_to: str | None  # informational — never auto-changes preference
    # v0.3.3: stale entries currently in cfg.services.to_verify whose unit
    # files no longer resolve. Opt-in removal mirrors the additive flow.
    service_removals: tuple[str, ...] = ()


def detect_stale_services(cfg: ConfigModel) -> tuple[str, ...]:
    """Return entries in cfg.services.to_verify whose unit no longer exists.

    Uses `systemctl cat <unit>` for the existence check (see
    archward.system.services.unit_exists). Defensive: if systemctl is
    not available the helper returns True for all units, so this
    function yields an empty tuple and no removals are proposed.
    """
    return tuple(
        u for u in cfg.services.to_verify
        if not system_services.unit_exists(u)
    )


def diff_against(cfg: ConfigModel, det: DetectionResult) -> ConfigDiff:
    """Compute the proposed changes without applying them."""
    # risk.high additions: detected kernels (and their headers) not already covered.
    # Note: many kernel package names are matched by `risk.kernel_patterns` via fnmatch,
    # which classifies them HIGH at runtime without being in `risk.high`. The detection
    # output is mainly to surface what's installed so the user *sees* their kernels;
    # adding them to risk.high is belt-and-suspenders. We only propose adding names
    # NOT already covered by kernel_patterns OR risk.high.
    import fnmatch

    def matches_any(name: str, patterns: tuple[str, ...]) -> bool:
        return any(fnmatch.fnmatch(name, pat) for pat in patterns)

    kernel_additions = tuple(
        k
        for k in det.kernels
        if k not in cfg.risk.high
        and not matches_any(k, cfg.risk.kernel_patterns)
        and not matches_any(k, cfg.risk.kernel_pattern_exclude)
    )

    # services.to_verify additions: services that are enabled+active, not yet in the list.
    service_additions = tuple(
        s for s in det.enabled_services if s not in cfg.services.to_verify
    )

    # AUR: flip disabled only if we found no helper AND user hadn't explicitly
    # disabled (i.e. enabled=True default). If user has already set enabled=False,
    # no change. If a helper IS detected, no change.
    aur_disable = det.helper is None and cfg.aur.enabled and not cfg.aur.skip

    service_removals = detect_stale_services(cfg)

    return ConfigDiff(
        kernel_additions=kernel_additions,
        service_additions=service_additions,
        aur_disable=aur_disable,
        helper_set_to=det.helper,
        service_removals=service_removals,
    )


def apply_detection(
    cfg: ConfigModel,
    det: DetectionResult,
    diff: ConfigDiff,
    *,
    accept_services: bool = False,
    accept_service_removals: bool = False,
) -> ConfigModel:
    """Return an updated ConfigModel applying the proposed diff.

    `accept_services` controls whether to add detected services to to_verify.
    `accept_service_removals` controls whether to drop stale entries from
    to_verify (units whose file no longer resolves). Both default off so
    `--detect` never silently mutates the services list.

    The kernel and AUR changes always apply (they're additive/safe);
    service additions and removals are opt-in independently.
    """
    from archward.config.loader import merge_partial

    overrides: dict[str, object] = {}

    if diff.kernel_additions:
        new_high = tuple(cfg.risk.high) + diff.kernel_additions
        overrides["risk"] = RiskConfig(
            high=new_high,
            medium_patterns=cfg.risk.medium_patterns,
            kernel_patterns=cfg.risk.kernel_patterns,
            kernel_pattern_exclude=cfg.risk.kernel_pattern_exclude,
        )

    apply_additions = accept_services and bool(diff.service_additions)
    apply_removals = accept_service_removals and bool(diff.service_removals)
    if apply_additions or apply_removals:
        current = tuple(cfg.services.to_verify)
        if apply_removals:
            removals = set(diff.service_removals)
            current = tuple(u for u in current if u not in removals)
        if apply_additions:
            current = current + diff.service_additions
        overrides["services"] = ServicesConfig(
            to_verify=current,
            severity=dict(cfg.services.severity),
        )

    if diff.aur_disable:
        overrides["aur"] = AurConfig(
            enabled=False,
            helper_preference=cfg.aur.helper_preference,
            skip=True,
        )

    if not overrides:
        return cfg
    return merge_partial(cfg, **overrides)
