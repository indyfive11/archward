"""Loader + writer round-trip and per-section fallback."""

from __future__ import annotations

from pathlib import Path

import pytest

from archward.config.defaults import default_config
from archward.config.loader import load_config, merge_partial, write_config
from archward.models.config import GatesConfig, RiskConfig


def test_load_missing_file_writes_defaults(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    assert not cfg_path.exists()
    cfg = load_config(cfg_path)
    # Defaults returned, file created.
    assert cfg.gates.snapshot_max_age_minutes == 60
    assert cfg_path.exists()
    # File should be a valid TOML reload-able into the same shape.
    cfg2 = load_config(cfg_path)
    assert cfg2.gates.snapshot_max_age_minutes == 60


def test_round_trip(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg = default_config()
    write_config(cfg, cfg_path)
    loaded = load_config(cfg_path)
    # Compare a few representative fields rather than full equality (Path expansion
    # may differ between the in-memory and loaded forms).
    assert loaded.risk.high == cfg.risk.high
    assert loaded.pacnew.rules == cfg.pacnew.rules
    assert loaded.aur.helper_preference == cfg.aur.helper_preference
    assert loaded.gates.snapshot_max_age_minutes == cfg.gates.snapshot_max_age_minutes


def test_invalid_top_level_toml_falls_back(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text("this is not valid toml = = =\n")
    cfg = load_config(cfg_path)
    # File is left untouched (don't overwrite the user's broken file).
    assert "= = =" in cfg_path.read_text()
    # Defaults returned.
    assert cfg.gates.snapshot_max_age_minutes == 60


def test_invalid_section_falls_back_just_that_section(tmp_path: Path) -> None:
    """A broken [gates] should not nuke [risk]."""
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        """
schema_version = 1

[gates]
# wrong type for snapshot_max_age_minutes
snapshot_max_age_minutes = "not a number"

[risk]
high = ["glibc", "openssh"]
medium_patterns = ["docker*"]
kernel_patterns = ["linux", "linux-lts"]
kernel_pattern_exclude = []

[general]
snapshot_dir = "/tmp/test-snapshots"
log_dir = "/tmp/test-logs"

[pacnew]
default_strategy = "review_needed"
rules = []

[services]

[aur]

[pacman]

[verify]

[privilege]
"""
    )
    cfg = load_config(cfg_path)
    # gates fell back to defaults.
    assert cfg.gates.snapshot_max_age_minutes == 60
    # risk loaded from file.
    assert cfg.risk.high == ("glibc", "openssh")
    assert cfg.risk.medium_patterns == ("docker*",)
    # general loaded from file (paths expanded).
    assert str(cfg.general.snapshot_dir) == "/tmp/test-snapshots"


def test_missing_section_uses_default(tmp_path: Path) -> None:
    """A TOML file that omits [gates] entirely should use default gates."""
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        """
schema_version = 1
[risk]
high = ["glibc"]
medium_patterns = []
kernel_patterns = ["linux"]

[general]
snapshot_dir = "/tmp/test-snapshots"
log_dir = "/tmp/test-logs"

[pacnew]
default_strategy = "review_needed"
rules = []
"""
    )
    cfg = load_config(cfg_path)
    assert cfg.gates.snapshot_max_age_minutes == 60  # default
    assert cfg.risk.high == ("glibc",)


def test_path_expansion(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        """
schema_version = 1
[general]
snapshot_dir = "~/snaps"
log_dir = "~/logs"

[risk]
high = []
medium_patterns = []
kernel_patterns = []

[pacnew]
default_strategy = "review_needed"
rules = []
"""
    )
    cfg = load_config(cfg_path)
    assert str(cfg.general.snapshot_dir) == str(tmp_path / "snaps")
    assert str(cfg.general.log_dir) == str(tmp_path / "logs")


def test_merge_partial(tmp_path: Path) -> None:
    cfg = default_config()
    new_gates = GatesConfig(snapshot_max_age_minutes=120, min_disk_gb=10)
    new_cfg = merge_partial(cfg, gates=new_gates)
    assert new_cfg.gates.snapshot_max_age_minutes == 120
    assert new_cfg.gates.min_disk_gb == 10
    # other sections preserved
    assert new_cfg.risk.high == cfg.risk.high


def test_merge_partial_rejects_unknown_section() -> None:
    cfg = default_config()
    with pytest.raises(ValueError, match="unknown config section"):
        merge_partial(cfg, nope=cfg.risk)  # type: ignore[arg-type]
