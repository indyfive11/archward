"""QSettings-backed UI state.

Separate from per-profile config.toml because this is global GUI state
that must survive profile switches and live somewhere the CLI doesn't
touch. Lands in ~/.config/archward/archward.conf on Linux (managed by
QSettings).

Requires `QApplication.setOrganizationName("archward")` +
`QApplication.setApplicationName("archward")` to be called BEFORE any
helper here — those calls determine the file QSettings reads/writes.
`main_gui()` does this immediately after creating the QApplication.

Current keys:
- `profiles/remember_last_used` (bool) — opt-in toggle for the next
  feature below.
- `profiles/last_used_path` (str) — absolute path of the most recent
  active profile, or "" for the default config.
- `wizard/completed` (bool) — set after the welcome wizard finishes so it
  does not auto-show on subsequent launches.

Future keys can be added here without churning the config schema.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings

_KEY_REMEMBER = "profiles/remember_last_used"
_KEY_LAST_PATH = "profiles/last_used_path"
_KEY_WIZARD_DONE = "wizard/completed"


def _settings() -> QSettings:
    # Default constructor uses the running QApplication's org/app names.
    return QSettings()


def get_remember_last_profile() -> bool:
    return bool(_settings().value(_KEY_REMEMBER, False, type=bool))


def set_remember_last_profile(enabled: bool) -> None:
    s = _settings()
    s.setValue(_KEY_REMEMBER, bool(enabled))
    s.sync()


def get_last_used_profile_path() -> Path | None:
    """Return the last-used profile path if the toggle is on AND the
    file still exists. Returns None for any reason the value can't be
    used (toggle off, key missing, file deleted, etc.) so callers can
    fall back to the default config without extra checks."""
    if not get_remember_last_profile():
        return None
    raw = str(_settings().value(_KEY_LAST_PATH, "", type=str))
    if not raw:
        return None
    p = Path(raw)
    if not p.exists():
        return None
    return p


def set_last_used_profile_path(path: Path | None) -> None:
    """Persist the active profile path. Pass None to record "default config"
    (stored as empty string so a future toggle-on reads it correctly)."""
    s = _settings()
    s.setValue(_KEY_LAST_PATH, str(path) if path is not None else "")
    s.sync()


def clear_last_used_profile_path() -> None:
    """Remove the last-used path key. Use when the user turns the toggle
    off so a future toggle-on doesn't read a stale value."""
    s = _settings()
    s.remove(_KEY_LAST_PATH)
    s.sync()


def get_wizard_completed() -> bool:
    return bool(_settings().value(_KEY_WIZARD_DONE, False, type=bool))


def set_wizard_completed() -> None:
    s = _settings()
    s.setValue(_KEY_WIZARD_DONE, True)
    s.sync()


def save_column_widths(key: str, widths: list[int]) -> None:
    _settings().setValue(key, ",".join(str(w) for w in widths))


def load_column_widths(key: str, defaults: list[int]) -> list[int]:
    raw = str(_settings().value(key, "", type=str))
    if not raw:
        return defaults
    try:
        return [int(x) for x in raw.split(",")]
    except ValueError:
        return defaults
