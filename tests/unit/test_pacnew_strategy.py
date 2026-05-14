"""Audit G2 + classifier matching against original (.pacnew-stripped) path."""

from __future__ import annotations

from pathlib import Path

import pytest

from archward.config.defaults import default_config
from archward.models.pacnew import PacnewRecommendation
from archward.pacman.pacnew import classify


@pytest.fixture(scope="module")
def pacnew_cfg():
    return default_config().pacnew


@pytest.mark.parametrize(
    "path_str, expected, expected_rule",
    [
        # Universal hardening targets (audit G2).
        ("/etc/systemd/resolved.conf.pacnew", PacnewRecommendation.KEEP_OURS, "*resolved.conf*"),
        ("/etc/security/faillock.conf.pacnew", PacnewRecommendation.KEEP_OURS, "*faillock.conf*"),
        ("/etc/sysctl.d/99-hardening.conf.pacnew", PacnewRecommendation.KEEP_OURS, "*/sysctl.d/*"),
        # Files from the bash baseline.
        ("/etc/ssh/sshd_config.pacnew", PacnewRecommendation.REVIEW_NEEDED, "*sshd_config*"),
        ("/etc/pacman.d/mirrorlist.pacnew", PacnewRecommendation.KEEP_OURS, "*mirrorlist*"),
        ("/etc/pacman.conf.pacnew", PacnewRecommendation.REVIEW_NEEDED, "*pacman.conf*"),
        ("/etc/fstab.pacnew", PacnewRecommendation.REVIEW_NEEDED, "*/fstab*"),
        ("/etc/default/grub.pacnew", PacnewRecommendation.REVIEW_NEEDED, "*/grub*"),
        # .hook rule must match foo.hook.pacnew (classify against original path).
        (
            "/usr/share/libalpm/hooks/something.hook.pacnew",
            PacnewRecommendation.TAKE_NEW,
            "*.hook",
        ),
        # Fallthrough → review_needed.
        ("/etc/anything-unmatched.pacnew", PacnewRecommendation.REVIEW_NEEDED, None),
    ],
)
def test_classify(pacnew_cfg, path_str, expected, expected_rule):
    pf = classify(Path(path_str), pacnew_cfg)
    assert pf.recommendation is expected
    assert pf.rule_pattern == expected_rule


def test_original_path_strips_pacnew(pacnew_cfg):
    pf = classify(Path("/etc/foo.pacnew"), pacnew_cfg)
    assert str(pf.original_path) == "/etc/foo"
    assert str(pf.path) == "/etc/foo.pacnew"


def test_first_rule_wins(pacnew_cfg):
    """Multiple rules could match — first one wins."""
    pf = classify(Path("/etc/pacman.conf.pacnew"), pacnew_cfg)
    assert pf.rule_pattern == "*pacman.conf*"
    assert pf.recommendation is PacnewRecommendation.REVIEW_NEEDED
