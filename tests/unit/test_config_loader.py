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


# ── v0.4.1 F1: atomic write_config ────────────────────────────────────


def test_atomic_write_preserves_original_on_mid_write_failure(tmp_path: Path, monkeypatch) -> None:
    """If tomli_w.dump raises mid-write, the original config.toml stays intact.

    Regression for v0.4.1 F1 — pre-fix the writer used a direct
    `open(path, "wb")` so a crash mid-write truncated the live file.
    The atomic-rename implementation writes to `<path>.tmp` then
    `os.replace()`s, so a mid-write failure leaves the original
    untouched and the temp file cleaned up.
    """
    import tomli_w as _tomli_w
    from archward.config import loader

    cfg_path = tmp_path / "config.toml"
    cfg = default_config()
    # Seed with a sentinel write so we can detect truncation.
    write_config(cfg, cfg_path)
    original_bytes = cfg_path.read_bytes()
    assert b"snapshot_max_age_minutes" in original_bytes

    # Make the next tomli_w.dump raise after the temp file is opened
    # (the failure happens during serialization, mirroring a disk-full).
    def boom(*args, **kwargs):
        raise OSError("simulated disk full")

    monkeypatch.setattr(loader.tomli_w, "dump", boom)

    with pytest.raises(OSError, match="simulated disk full"):
        write_config(cfg, cfg_path)

    # Live file is intact; temp file is cleaned up.
    assert cfg_path.read_bytes() == original_bytes
    assert not (tmp_path / "config.toml.tmp").exists()


def test_atomic_write_replaces_existing_file(tmp_path: Path) -> None:
    """Sanity check the happy path: a second write actually updates the file."""
    cfg_path = tmp_path / "config.toml"
    cfg1 = default_config()
    write_config(cfg1, cfg_path)
    first = cfg_path.read_bytes()

    # Modify a field, write again.
    cfg2 = cfg1.model_copy(update={
        "gates": cfg1.gates.model_copy(update={"snapshot_max_age_minutes": 999})
    })
    write_config(cfg2, cfg_path)
    second = cfg_path.read_bytes()
    assert first != second
    assert b"999" in second
