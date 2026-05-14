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


def test_include_all_env_var_bypasses_since_filter(tmp_path, monkeypatch):
    """ARCHWARD_PACNEW_INCLUDE_ALL=1 ignores the since_epoch mtime filter.

    Without the env var, a .pacnew created BEFORE since_epoch is filtered out.
    With it set, the file is returned regardless.
    """
    from archward.pacman.pacnew import find_pacnew_files

    # Create an old .pacnew (mtime way before "now").
    old = tmp_path / "etc"
    old.mkdir()
    sample = old / "stale.conf.pacnew"
    sample.write_text("# stale\n")
    import os
    very_old = 1_000_000_000  # year 2001 in epoch
    os.utime(sample, (very_old, very_old))

    # Without override: filtered out (mtime <= since_epoch).
    monkeypatch.delenv("ARCHWARD_PACNEW_INCLUDE_ALL", raising=False)
    no_override = find_pacnew_files(roots=(old,), since_epoch=very_old + 100)
    assert sample not in no_override

    # With override: included regardless of mtime.
    monkeypatch.setenv("ARCHWARD_PACNEW_INCLUDE_ALL", "1")
    with_override = find_pacnew_files(roots=(old,), since_epoch=very_old + 100)
    assert sample in with_override


def test_include_all_env_other_values_dont_trigger(tmp_path, monkeypatch):
    """Only ARCHWARD_PACNEW_INCLUDE_ALL=1 triggers — '0', 'true', etc. don't."""
    from archward.pacman.pacnew import find_pacnew_files

    old = tmp_path / "etc"
    old.mkdir()
    sample = old / "stale.conf.pacnew"
    sample.write_text("# stale\n")
    import os
    very_old = 1_000_000_000
    os.utime(sample, (very_old, very_old))

    for not_one in ("0", "true", "yes", ""):
        monkeypatch.setenv("ARCHWARD_PACNEW_INCLUDE_ALL", not_one)
        result = find_pacnew_files(roots=(old,), since_epoch=very_old + 100)
        assert sample not in result, f"value {not_one!r} should not trigger override"
