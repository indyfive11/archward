"""TOML config loader and writer.

Behavior:
- Missing file → write defaults to ~/.config/archward/config.toml, return defaults.
- File present, valid → load and return.
- File present, top-level TOML parse error → log + return defaults (do NOT overwrite the
  broken file; the user may want to fix it by hand).
- File present, individual section fails Pydantic validation → log, fall back to that
  section's defaults, keep other sections. `load_config_report()` exposes which
  sections fell back so front-ends can tell the user before a save replaces them.
- On-disk schema_version older than current → migrated in memory via `_MIGRATIONS`.
  NEWER than current → loaded best-effort read-only: every writer refuses rather
  than silently downgrading the file.

Write discipline (v0.5):
- `write_config()` — explicit user saves (Preferences, wizard, `--write-config`,
  `--detect` apply). Read-modify-write: unknown top-level sections (plugin-owned /
  future) are preserved from disk; the model's own sections are written as shown
  to the user — including sections that fell back at load (the visible state wins
  on an explicit save).
- `update_config_sections()` — unattended writers (auto-prune, one-shot aur.skip
  reset). Surgically replaces ONLY the named sections in the on-disk document;
  everything else — unknown sections, sections whose on-disk text failed
  validation — is left byte-for-byte untouched.
TOML comments do not survive either writer (tomllib cannot round-trip them).
"""

from __future__ import annotations

import fcntl
import logging
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import tomli_w
from pydantic import BaseModel, ValidationError

from archward.config import paths
from archward.config.defaults import default_config
from archward.models.config import (
    AurConfig,
    CacheConfig,
    ConfigModel,
    GatesConfig,
    GeneralConfig,
    HooksConfig,
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
    ("hooks", HooksConfig),
    ("cache", CacheConfig),
)

_CURRENT_SCHEMA_VERSION = 1

# Migration table: from-version → fn(raw_dict) -> raw_dict, applied in
# sequence until _CURRENT_SCHEMA_VERSION. A future rename ships as
#   _MIGRATIONS[1] = _migrate_v1_to_v2
# alongside a version bump — never as a bare model change.
_MIGRATIONS: dict[int, Callable[[dict[str, Any]], dict[str, Any]]] = {}


class ConfigReadOnlyError(OSError):
    """The on-disk config has a NEWER schema_version than this archward
    understands — writing would silently downgrade it. Refused."""


@dataclass(frozen=True)
class LoadReport:
    """What load_config actually did — for front-ends that must be honest
    about substituted defaults before persisting anything."""

    fallback_sections: tuple[str, ...] = ()
    unknown_sections: tuple[str, ...] = ()
    schema_version: int = _CURRENT_SCHEMA_VERSION
    read_only: bool = False  # on-disk version newer than supported
    # Whole-file TOML parse failure: EVERY value shown is a default, and an
    # explicit save replaces the entire file (nothing can be preserved from
    # an unparseable document).
    parse_error: bool = False


def default_config_path() -> Path:
    return paths.config_dir() / "config.toml"


def load_config(path: Path | None = None) -> ConfigModel:
    """Load config from `path` (default ~/.config/archward/config.toml).

    First-run bootstraps by writing defaults to the file. Subsequent runs read
    and validate per-section.
    """
    return load_config_report(path)[0]


def load_config_report(path: Path | None = None) -> tuple[ConfigModel, LoadReport]:
    """Like load_config, but also reports fallbacks / unknown sections /
    schema version so callers can surface them."""
    if path is None:
        path = default_config_path()

    defaults = default_config()

    if not path.exists():
        log.info("config %s does not exist; writing defaults", path)
        try:
            write_config(defaults, path)
        except OSError as e:
            log.warning("could not write default config to %s: %s", path, e)
        return defaults, LoadReport()

    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as e:
        log.error("could not read %s: %s — using defaults", path, e)
        return defaults, LoadReport()

    try:
        raw = tomllib.loads(raw_text)
    except tomllib.TOMLDecodeError as e:
        log.error("config TOML parse error at %s: %s — using defaults (file left untouched)", path, e)
        return defaults, LoadReport(parse_error=True)

    return _merge_with_defaults(raw, defaults)


def _apply_migrations(raw: dict[str, Any], version: int) -> tuple[dict[str, Any], int]:
    while version < _CURRENT_SCHEMA_VERSION:
        fn = _MIGRATIONS.get(version)
        if fn is None:
            log.warning(
                "no migration from config schema_version=%d — loading as-is", version
            )
            break
        raw = fn(raw)
        version += 1
        log.info("migrated config to schema_version=%d (in memory)", version)
    return raw, version


def _merge_with_defaults(
    raw: dict[str, Any], defaults: ConfigModel
) -> tuple[ConfigModel, LoadReport]:
    """Per-section validation; failures fall back to the default for that section."""
    disk_version = raw.get("schema_version", _CURRENT_SCHEMA_VERSION)
    read_only = False
    if isinstance(disk_version, int) and disk_version > _CURRENT_SCHEMA_VERSION:
        log.warning(
            "config schema_version=%d is NEWER than this archward understands (%d) "
            "— loading best-effort READ-ONLY; config writes are disabled so the "
            "file is never downgraded",
            disk_version,
            _CURRENT_SCHEMA_VERSION,
        )
        read_only = True
    elif isinstance(disk_version, int) and disk_version < _CURRENT_SCHEMA_VERSION:
        raw, disk_version = _apply_migrations(raw, disk_version)

    known = {key for key, _ in _SECTIONS} | {"schema_version"}
    unknown = tuple(sorted(k for k in raw if k not in known))

    fallbacks: list[str] = []
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
            fallbacks.append(key)

    cfg = ConfigModel(schema_version=_CURRENT_SCHEMA_VERSION, **resolved)
    report = LoadReport(
        fallback_sections=tuple(fallbacks),
        unknown_sections=unknown,
        schema_version=disk_version if isinstance(disk_version, int) else _CURRENT_SCHEMA_VERSION,
        read_only=read_only,
    )
    return cfg, report


def _read_raw_doc(path: Path) -> dict[str, Any] | None:
    """Best-effort raw TOML read for write-time preservation. None when the
    file is absent or unreadable/unparseable (callers decide semantics)."""
    if not path.exists():
        return {}
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return None


def _refuse_if_newer(raw: dict[str, Any] | None, path: Path) -> None:
    if raw is None:
        return
    version = raw.get("schema_version", _CURRENT_SCHEMA_VERSION)
    if isinstance(version, int) and version > _CURRENT_SCHEMA_VERSION:
        raise ConfigReadOnlyError(
            f"{path} has schema_version={version}, newer than this archward "
            f"understands ({_CURRENT_SCHEMA_VERSION}) — refusing to write "
            "(a write would downgrade the file)."
        )


from contextlib import contextmanager


@contextmanager
def _config_lock(path: Path):
    """flock serializing all config writers (explicit + surgical) on the
    same target path. The `.lock` sidecar persists — flock has no safe
    unlink protocol; it is tiny and not matched by profile globbing."""
    lock_path = path.with_suffix(path.suffix + ".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _atomic_write(data: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        with open(tmp_path, "wb") as f:
            tomli_w.dump(data, f)
            # fsync the data + close before the rename so the new file is
            # durable. Otherwise a power-loss between rename and fsync
            # could leave us with the rename committed but the file empty.
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        # Best-effort cleanup of the temp file on failure.
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def write_config(cfg: ConfigModel, path: Path | None = None) -> Path:
    """Serialize `cfg` to TOML at `path` — the EXPLICIT-save writer.

    Read-modify-write (v0.5): unknown top-level sections on disk (plugin-
    owned / future) are carried through; the model's own sections are
    written as-is — on an explicit save, what the user saw wins, including
    over an on-disk section that failed validation at load. Raises
    ConfigReadOnlyError when the on-disk file has a newer schema_version.

    The write is atomic: serialize to `<path>.tmp` then `os.replace()` it
    onto the live path; a crash mid-write leaves the original intact.
    """
    if path is None:
        path = default_config_path()

    with _config_lock(path):
        on_disk = _read_raw_doc(path)
        _refuse_if_newer(on_disk, path)

        data = cfg.model_dump(mode="json", exclude_none=True)
        if on_disk:
            known = {key for key, _ in _SECTIONS} | {"schema_version"}
            for key, value in on_disk.items():
                if key not in known:
                    data[key] = value

        _atomic_write(data, path)
    log.info("wrote config to %s", path)
    return path


def update_config_sections(
    path: Path | None = None, **sections: BaseModel
) -> bool:
    """Surgically replace only the named sections — the UNATTENDED writer
    (auto-prune, one-shot aur.skip reset).

    Everything else in the document is preserved verbatim: unknown sections
    AND sections whose on-disk text failed validation at load (an unattended
    writer must never launder substituted defaults onto disk). Returns True
    when written; False when refused — unparseable file, unreadable file, or
    newer schema_version — with the reason logged. A missing file is created
    holding just schema_version + the named sections. Serialized against
    concurrent surgical writers via flock on `<path>.lock` (section-level
    last-writer-wins remains the semantic across processes).
    """
    if path is None:
        path = default_config_path()
    known = {key for key, _ in _SECTIONS}
    for key in sections:
        if key not in known:
            raise ValueError(f"unknown config section: {key}")

    try:
        with _config_lock(path):
            raw = _read_raw_doc(path)
            if raw is None:
                log.warning(
                    "%s is unreadable or not valid TOML — refusing surgical update "
                    "(fix or remove the file)", path,
                )
                return False
            try:
                _refuse_if_newer(raw, path)
            except ConfigReadOnlyError as e:
                log.warning("%s", e)
                return False

            # NOTE: an older schema_version is kept as-is here (surgical
            # writers never migrate the file); once _MIGRATIONS gains a real
            # entry, this writer must refuse older docs too, or migrate.
            raw.setdefault("schema_version", _CURRENT_SCHEMA_VERSION)
            for key, model in sections.items():
                raw[key] = model.model_dump(mode="json", exclude_none=True)

            _atomic_write(raw, path)
    except OSError as e:
        log.warning("surgical config update of %s failed: %s", path, e)
        return False

    log.info("updated config section(s) %s in %s", ", ".join(sections), path)
    return True


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
