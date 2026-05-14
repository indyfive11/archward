"""TOML config loader and writer.

Behavior:
- Missing file → write defaults to ~/.config/archward/config.toml, return defaults.
- File present, valid → load and return.
- File present, top-level TOML parse error → log + return defaults (do NOT overwrite the
  broken file; the user may want to fix it by hand).
- File present, individual section fails Pydantic validation → log, fall back to that
  section's defaults, keep other sections.

Hand-edited files are never silently rewritten. `write_config()` is only called by:
  - First-run bootstrap (no file existed)
  - Explicit user action (Preferences dialog save, `--write-config` flag, `--detect` apply)
"""

from __future__ import annotations

import logging
import tomllib
from pathlib import Path
from typing import Any

import tomli_w
from pydantic import BaseModel, ValidationError

from archward.config import paths
from archward.config.defaults import default_config
from archward.models.config import (
    AurConfig,
    ConfigModel,
    GatesConfig,
    GeneralConfig,
    PacmanConfig,
    PacnewConfig,
    PrivilegeConfig,
    RiskConfig,
    ServicesConfig,
    VerifyConfig,
)

log = logging.getLogger(__name__)

# Maps top-level TOML key → (sub-model class, attribute name on ConfigModel).
# Keeping this explicit (rather than introspecting ConfigModel) makes the
# loader's behavior obvious and keeps schema_version handling separate.
_SECTIONS: tuple[tuple[str, type[BaseModel]], ...] = (
    ("general", GeneralConfig),
    ("gates", GatesConfig),
    ("risk", RiskConfig),
    ("services", ServicesConfig),
    ("pacnew", PacnewConfig),
    ("aur", AurConfig),
    ("pacman", PacmanConfig),
    ("verify", VerifyConfig),
    ("privilege", PrivilegeConfig),
)

_CURRENT_SCHEMA_VERSION = 1


def default_config_path() -> Path:
    return paths.config_dir() / "config.toml"


def load_config(path: Path | None = None) -> ConfigModel:
    """Load config from `path` (default ~/.config/archward/config.toml).

    First-run bootstraps by writing defaults to the file. Subsequent runs read
    and validate per-section.
    """
    if path is None:
        path = default_config_path()

    defaults = default_config()

    if not path.exists():
        log.info("config %s does not exist; writing defaults", path)
        try:
            write_config(defaults, path)
        except OSError as e:
            log.warning("could not write default config to %s: %s", path, e)
        return defaults

    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as e:
        log.error("could not read %s: %s — using defaults", path, e)
        return defaults

    try:
        raw = tomllib.loads(raw_text)
    except tomllib.TOMLDecodeError as e:
        log.error("config TOML parse error at %s: %s — using defaults (file left untouched)", path, e)
        return defaults

    return _merge_with_defaults(raw, defaults)


def _merge_with_defaults(raw: dict[str, Any], defaults: ConfigModel) -> ConfigModel:
    """Per-section validation; failures fall back to the default for that section."""
    version = raw.get("schema_version", _CURRENT_SCHEMA_VERSION)
    if version != _CURRENT_SCHEMA_VERSION:
        log.warning(
            "config schema_version=%d, archward expects %d — newer/older fields may be ignored",
            version,
            _CURRENT_SCHEMA_VERSION,
        )

    resolved: dict[str, BaseModel] = {}
    for key, model_cls in _SECTIONS:
        default_for_section = getattr(defaults, key)
        if key not in raw:
            resolved[key] = default_for_section
            continue
        try:
            resolved[key] = model_cls.model_validate(raw[key])
        except ValidationError as e:
            # Log just the first error to keep noise down; the message includes
            # the offending field path.
            first = e.errors()[0] if e.errors() else None
            field_path = ".".join(str(p) for p in first.get("loc", ())) if first else "?"
            log.warning(
                "config section [%s] invalid at %s: %s — using defaults for this section",
                key,
                field_path,
                first.get("msg", e) if first else e,
            )
            resolved[key] = default_for_section

    return ConfigModel(schema_version=_CURRENT_SCHEMA_VERSION, **resolved)


def write_config(cfg: ConfigModel, path: Path | None = None) -> Path:
    """Serialize `cfg` to TOML at `path`. Creates parent dirs as needed.

    Returns the written path. Paths and enums are serialized as strings.
    Missing-value fields (None) are dropped — TOML cannot represent null.
    """
    if path is None:
        path = default_config_path()

    path.parent.mkdir(parents=True, exist_ok=True)
    data = cfg.model_dump(mode="json", exclude_none=True)
    # tomli_w writes binary; serialize via dump(...) to a file-like object.
    with open(path, "wb") as f:
        tomli_w.dump(data, f)
    log.info("wrote config to %s", path)
    return path


def merge_partial(cfg: ConfigModel, **section_overrides: BaseModel) -> ConfigModel:
    """Return a new ConfigModel with selected sub-models replaced.

    Used by detect.apply_detection() to produce an updated config without
    mutating the (frozen) original.
    """
    current = cfg.model_dump()
    for key, value in section_overrides.items():
        if key not in {k for k, _ in _SECTIONS}:
            raise ValueError(f"unknown config section: {key}")
        current[key] = value.model_dump()
    return ConfigModel.model_validate(current)
