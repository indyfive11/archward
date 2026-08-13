"""Phase 1 hard-coded defaults.

Phase 2 will replace the body of `default_config()` with a TOML loader that reads
~/.config/archward/config.toml, but the loader returns the same `ConfigModel`
structure — every consumer is already TOML-shaped.
"""

from __future__ import annotations

from archward.config import paths
from archward.models.config import (
    AurConfig,
    CacheConfig,
    ConfigModel,
    GatesConfig,
    GeneralConfig,
    HooksConfig,
    PacmanConfig,
    PacnewConfig,
    PacnewRule,
    PrivilegeConfig,
    RiskConfig,
    ServicesConfig,
    VerifyConfig,
)
from archward.models.pacnew import PacnewRecommendation

# Risk classifier defaults — universal HIGH-risk packages on any Arch system.
# Per audit C4: kernel_patterns covers kernel pkg AND its -headers variant.
_HIGH_RISK = (
    "glibc",
    "lib32-glibc",
    "systemd",
    "systemd-libs",
    "openssl",
    "lib32-openssl",
    "mesa",
    "lib32-mesa",
    "pipewire",
    "pipewire-pulse",
    "wireplumber",
    "openssh",
)

_KERNEL_PATTERNS = (
    "linux",
    "linux-headers",
    "linux-lts",
    "linux-lts-headers",
    "linux-zen",
    "linux-zen-headers",
    "linux-hardened",
    "linux-hardened-headers",
    "linux-cachyos*",
    "linux-api-headers",
)

# kernel_pattern_exclude lives on the RiskConfig model default (v0.5).

_MEDIUM_PATTERNS = (
    "*-server",
    "docker*",
    "qemu*",
    "libvirt*",
    "postgresql*",
    "mariadb*",
    "nginx*",
    "apache*",
)

# Default pacnew strategies — universal hardening targets, derived from the bash
# pipeline's strategy table and augmented per audit G2.
_PACNEW_RULES = (
    PacnewRule(
        pattern="*sshd_config*",
        strategy=PacnewRecommendation.REVIEW_NEEDED,
        note="SSH daemon config — review carefully",
    ),
    PacnewRule(
        pattern="*mirrorlist*",
        strategy=PacnewRecommendation.KEEP_OURS,
        note="Keep your rate-tested mirror order",
    ),
    PacnewRule(
        pattern="*pacman.conf*",
        strategy=PacnewRecommendation.REVIEW_NEEDED,
        note="Pacman options — review for new repos / IgnorePkg changes",
    ),
    PacnewRule(
        pattern="*/fstab*",
        strategy=PacnewRecommendation.REVIEW_NEEDED,
        note="Filesystem mounts — review before next boot",
    ),
    PacnewRule(
        pattern="*/grub*",
        strategy=PacnewRecommendation.REVIEW_NEEDED,
        note="Bootloader config — review before next boot",
    ),
    PacnewRule(
        pattern="*resolved.conf*",
        strategy=PacnewRecommendation.KEEP_OURS,
        note="DNS / DoT customizations frequently diverge from upstream",
    ),
    PacnewRule(
        pattern="*faillock.conf*",
        strategy=PacnewRecommendation.KEEP_OURS,
        note="Account lockout policy — preserve tuned values",
    ),
    PacnewRule(
        pattern="*/sysctl.d/*",
        strategy=PacnewRecommendation.KEEP_OURS,
        note="Kernel hardening params — preserve tuned values",
    ),
    PacnewRule(
        pattern="*.hook",
        strategy=PacnewRecommendation.TAKE_NEW,
        note="Pacman hooks usually track upstream",
    ),
)


def default_config() -> ConfigModel:
    """Return the default ConfigModel.

    v0.5 single-sourcing: every simple default lives on the model fields
    (models/config.py) — this function supplies only what the model can't
    default itself: the dynamic XDG paths and the curated risk/pacnew data.
    A drift-guard test asserts nothing here shadows a model default.
    """
    return ConfigModel(
        general=GeneralConfig(
            snapshot_dir=paths.snapshots_dir(),
            log_dir=paths.logs_dir(),
        ),
        risk=RiskConfig(
            high=_HIGH_RISK,
            medium_patterns=_MEDIUM_PATTERNS,
            kernel_patterns=_KERNEL_PATTERNS,
        ),
        pacnew=PacnewConfig(rules=_PACNEW_RULES),
    )
