"""v0.5 config write discipline — single-source defaults, migration
machinery, surgical vs explicit writers, read-only-newer protection.
"""

from __future__ import annotations

import tomllib

import pytest

from archward.config import loader
from archward.config.defaults import default_config
from archward.config.loader import (
    ConfigReadOnlyError,
    load_config_report,
    update_config_sections,
    write_config,
)
from archward.models.config import AurConfig, ConfigModel


# ── single-source defaults ────────────────────────────────────────────────


def test_default_config_matches_model_defaults() -> None:
    """Drift guard: default_config() may only differ from bare model
    defaults on the fields it exists to supply (paths, risk lists, pacnew
    rules). Everything else must equal the model default — the
    keep_snapshots 10-vs-40 class of bug."""
    dc = default_config()
    for section_name in (
        "gates", "services", "aur", "pacman", "verify", "privilege", "hooks", "cache"
    ):
        section = getattr(dc, section_name)
        assert section == type(section)(), f"[{section_name}] diverges from model defaults"
    # And the one historically-divergent defaulted field:
    assert dc.risk.kernel_pattern_exclude == ("linux-firmware*", "linux-docs*")


def test_partial_risk_section_gets_exclusion_default(tmp_path) -> None:
    """A user [risk] with the three required lists but no exclude key gets
    the real exclusion, not () (the pre-v0.5 divergence)."""
    p = tmp_path / "config.toml"
    p.write_text(
        'schema_version = 1\n[risk]\nhigh = ["glibc"]\n'
        'medium_patterns = []\nkernel_patterns = ["linux"]\n'
    )
    cfg, report = load_config_report(p)
    assert report.fallback_sections == ()
    assert cfg.risk.kernel_pattern_exclude == ("linux-firmware*", "linux-docs*")


# ── migration machinery ───────────────────────────────────────────────────


def test_migration_applied_in_sequence(tmp_path, monkeypatch) -> None:
    p = tmp_path / "config.toml"
    p.write_text('schema_version = 0\n[gates]\nmax_age = 90\n')

    def _v0_to_v1(raw: dict) -> dict:
        gates = raw.get("gates", {})
        if "max_age" in gates:
            gates["snapshot_max_age_minutes"] = gates.pop("max_age")
        return raw

    monkeypatch.setitem(loader._MIGRATIONS, 0, _v0_to_v1)
    cfg, report = load_config_report(p)
    assert cfg.gates.snapshot_max_age_minutes == 90
    assert report.schema_version == 1
    assert report.fallback_sections == ()


def test_missing_migration_loads_as_is(tmp_path) -> None:
    p = tmp_path / "config.toml"
    p.write_text("schema_version = 0\n")
    cfg, report = load_config_report(p)  # must not raise
    assert isinstance(cfg, ConfigModel)


# ── read-only-newer protection ────────────────────────────────────────────


def _newer_file(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('schema_version = 99\n[aur]\nenabled = false\n')
    return p


def test_newer_version_loads_read_only(tmp_path) -> None:
    p = _newer_file(tmp_path)
    cfg, report = load_config_report(p)
    assert report.read_only is True
    assert cfg.aur.enabled is False  # best-effort load still applies values


def test_newer_version_refuses_explicit_write(tmp_path) -> None:
    p = _newer_file(tmp_path)
    with pytest.raises(ConfigReadOnlyError):
        write_config(default_config(), p)
    assert "schema_version = 99" in p.read_text()


def test_newer_version_refuses_surgical_write(tmp_path) -> None:
    p = _newer_file(tmp_path)
    assert update_config_sections(p, aur=AurConfig(skip=False)) is False
    assert "schema_version = 99" in p.read_text()


# ── surgical writer semantics ─────────────────────────────────────────────


def test_surgical_write_preserves_everything_else(tmp_path) -> None:
    p = tmp_path / "config.toml"
    p.write_text(
        "schema_version = 1\n"
        '[gates]\nmin_disk_gb = "BROKEN"\n'      # fails validation at load
        "[myplugin]\ncustom = 42\n"              # unknown / plugin-owned
        "[aur]\nskip = true\n"
    )
    assert update_config_sections(p, aur=AurConfig(skip=False)) is True
    raw = tomllib.loads(p.read_text())
    assert raw["aur"]["skip"] is False                 # the named section changed
    assert raw["gates"]["min_disk_gb"] == "BROKEN"     # broken section untouched
    assert raw["myplugin"]["custom"] == 42             # unknown section preserved


def test_surgical_write_creates_minimal_missing_file(tmp_path) -> None:
    p = tmp_path / "config.toml"
    assert update_config_sections(p, aur=AurConfig(skip=False)) is True
    raw = tomllib.loads(p.read_text())
    assert raw["schema_version"] == 1
    assert raw["aur"]["skip"] is False
    assert "gates" not in raw  # minimal doc — no synthesized full defaults


def test_surgical_write_refuses_unparseable_file(tmp_path) -> None:
    p = tmp_path / "config.toml"
    p.write_text("this is [not toml")
    assert update_config_sections(p, aur=AurConfig()) is False
    assert p.read_text() == "this is [not toml"


def test_surgical_write_rejects_unknown_section() -> None:
    with pytest.raises(ValueError):
        update_config_sections(None, nonsense=AurConfig())


# ── explicit writer semantics ─────────────────────────────────────────────


def test_explicit_write_preserves_unknown_sections(tmp_path) -> None:
    p = tmp_path / "config.toml"
    p.write_text("schema_version = 1\n[myplugin]\ncustom = 42\n")
    write_config(default_config(), p)
    raw = tomllib.loads(p.read_text())
    assert raw["myplugin"]["custom"] == 42
    assert raw["gates"]["min_disk_gb"] == 5  # model sections fully written


def test_explicit_write_overwrites_fallback_section(tmp_path) -> None:
    """On an EXPLICIT save the visible state wins — a broken on-disk
    section is replaced (the user was warned by the Preferences notice)."""
    p = tmp_path / "config.toml"
    p.write_text('schema_version = 1\n[gates]\nmin_disk_gb = "BROKEN"\n')
    cfg, report = load_config_report(p)
    assert report.fallback_sections == ("gates",)
    write_config(cfg, p)
    raw = tomllib.loads(p.read_text())
    assert raw["gates"]["min_disk_gb"] == 5


# ── load report ───────────────────────────────────────────────────────────


def test_load_report_names_fallbacks_and_unknowns(tmp_path) -> None:
    p = tmp_path / "config.toml"
    p.write_text(
        'schema_version = 1\n[gates]\nmin_disk_gb = "BROKEN"\n[myplugin]\nx = 1\n'
    )
    _cfg, report = load_config_report(p)
    assert report.fallback_sections == ("gates",)
    assert report.unknown_sections == ("myplugin",)
    assert report.read_only is False
