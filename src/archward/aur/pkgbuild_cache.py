"""PKGBUILD approval-history cache (v0.4.7).

Stores the full PKGBUILD text approved for each AUR package so that
subsequent reviews can show a diff against the previously-approved version,
making malicious hook additions immediately visible.

State: ~/.local/state/archward/pkgbuild_cache.json
Pattern: identical to aur/quarantine.py — graceful load failure, warn on
save failure.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

from archward.config.paths import state_dir

log = logging.getLogger(__name__)

_STATE_FILE = "pkgbuild_cache.json"
_MAX_CONTENT_BYTES = 512 * 1024  # 512 KB guard — PKGBUILDs are typically 1–5 KB


def _state_path() -> Path:
    return state_dir() / _STATE_FILE


@dataclass
class PkgbuildCacheEntry:
    content: str
    content_hash: str   # SHA-256 hex digest — fast equality check
    approved_at: float  # Unix timestamp


def _entry_from_dict(d: dict) -> PkgbuildCacheEntry:
    return PkgbuildCacheEntry(
        content=str(d["content"]),
        content_hash=str(d["content_hash"]),
        approved_at=float(d["approved_at"]),
    )


def _entry_to_dict(e: PkgbuildCacheEntry) -> dict:
    return {
        "content": e.content,
        "content_hash": e.content_hash,
        "approved_at": e.approved_at,
    }


class PkgbuildCache:
    """Per-package PKGBUILD approval cache.

    Lifecycle:
        cache = PkgbuildCache()
        cache.load()
        entry = cache.get(pkg)          # None on first review
        # … show modal …
        cache.store(pkg, content)
        cache.save()
    """

    def __init__(self) -> None:
        self._data: dict[str, PkgbuildCacheEntry] = {}

    # ── I/O ───────────────────────────────────────────────────────────────────

    def load(self) -> None:
        path = _state_path()
        if not path.exists():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            self._data = {pkg: _entry_from_dict(d) for pkg, d in raw.items()}
        except Exception as exc:
            log.warning("pkgbuild_cache: could not load state (%s) — starting fresh", exc)
            self._data = {}

    def save(self) -> None:
        path = _state_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {pkg: _entry_to_dict(e) for pkg, e in self._data.items()}
            path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError as exc:
            log.warning("pkgbuild_cache: could not save state: %s", exc)

    # ── query ─────────────────────────────────────────────────────────────────

    def get(self, pkg: str) -> PkgbuildCacheEntry | None:
        return self._data.get(pkg)

    # ── mutation ──────────────────────────────────────────────────────────────

    def store(self, pkg: str, content: str) -> None:
        if len(content.encode()) > _MAX_CONTENT_BYTES:
            log.warning(
                "pkgbuild_cache: %s PKGBUILD exceeds %d bytes — truncating",
                pkg, _MAX_CONTENT_BYTES,
            )
            content = content.encode()[:_MAX_CONTENT_BYTES].decode(errors="replace")
        digest = hashlib.sha256(content.encode()).hexdigest()
        self._data[pkg] = PkgbuildCacheEntry(
            content=content,
            content_hash=digest,
            approved_at=time.time(),
        )

    def remove(self, pkg: str) -> bool:
        """Remove a package's cache entry. Returns True if an entry existed."""
        if pkg in self._data:
            del self._data[pkg]
            return True
        return False

    def clear(self) -> int:
        """Remove all entries. Returns count cleared."""
        count = len(self._data)
        self._data.clear()
        return count
