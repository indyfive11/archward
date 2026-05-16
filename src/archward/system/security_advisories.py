"""Arch Security Advisory (ASA) check (v0.4.5 F3).

Fetches the public ASA JSON feed and cross-references installed packages
to find open vulnerabilities. Used by the verify phase to surface
unpatched CVEs on the system being updated.

Pure Python, Qt-free, no new dependencies (urllib + json stdlib).
See: https://security.archlinux.org/
"""

from __future__ import annotations

import json
import logging
import shutil
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from archward.config.paths import state_dir
from archward.pacman import query as pq

log = logging.getLogger(__name__)

_ASA_URL = "https://security.archlinux.org/all.json"
_CACHE_FILE = "asa_cache.json"
_CACHE_TTL_S = 4 * 3600  # 4 hours


@dataclass(frozen=True)
class Advisory:
    name: str                    # "AVG-2345"
    packages: tuple[str, ...]
    status: str                  # "Vulnerable" | "Fixed" | "Unknown"
    severity: str                # "Critical" | "High" | "Medium" | "Low"
    advisory_type: str
    affected: str                # raw specifier from the feed
    fixed: str | None            # first fixed version, or None if unfixed
    issues: tuple[str, ...]      # CVE IDs


# ── cache ─────────────────────────────────────────────────────────────


def _cache_path() -> Path:
    return state_dir() / _CACHE_FILE


def _load_cache() -> tuple[float, list[Advisory]] | None:
    path = _cache_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        fetched_at: float = data["fetched_at"]
        advisories = [
            Advisory(
                name=a["name"],
                packages=tuple(a["packages"]),
                status=a["status"],
                severity=a["severity"],
                advisory_type=a["advisory_type"],
                affected=a["affected"],
                fixed=a.get("fixed"),
                issues=tuple(a["issues"]),
            )
            for a in data.get("advisories", [])
        ]
        return fetched_at, advisories
    except Exception:  # noqa: BLE001
        return None


def _save_cache(advisories: list[Advisory]) -> None:
    path = _cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "fetched_at": time.time(),
            "advisories": [
                {
                    "name": a.name,
                    "packages": list(a.packages),
                    "status": a.status,
                    "severity": a.severity,
                    "advisory_type": a.advisory_type,
                    "affected": a.affected,
                    "fixed": a.fixed,
                    "issues": list(a.issues),
                }
                for a in advisories
            ],
        }
        path.write_text(json.dumps(payload, indent=2))
    except OSError as exc:
        log.warning("security_advisories: could not write cache: %s", exc)


# ── parsing ───────────────────────────────────────────────────────────


def _parse_asa_json(raw: bytes) -> list[Advisory]:
    entries = json.loads(raw)
    advisories: list[Advisory] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        try:
            advisories.append(Advisory(
                name=entry.get("name", ""),
                packages=tuple(entry.get("packages", [])),
                status=entry.get("status", ""),
                severity=entry.get("severity", ""),
                advisory_type=entry.get("type", ""),
                affected=entry.get("affected", ""),
                fixed=entry.get("fixed") or None,
                issues=tuple(entry.get("issues", [])),
            ))
        except Exception:  # noqa: BLE001
            continue
    return advisories


# ── public API ────────────────────────────────────────────────────────


def fetch_advisories(timeout: float = 10.0) -> list[Advisory]:
    """Return all Arch Security Advisories from the public feed.

    Checks the on-disk cache first (TTL: 4 hours). Returns [] on any
    network or parse failure so callers can treat an offline system as a
    SKIP.
    """
    cached = _load_cache()
    if cached is not None:
        fetched_at, advisories = cached
        if time.time() - fetched_at < _CACHE_TTL_S:
            log.debug("security_advisories: cache hit (%d entries)", len(advisories))
            return advisories

    log.debug("security_advisories: fetching %s", _ASA_URL)
    try:
        with urllib.request.urlopen(_ASA_URL, timeout=timeout) as resp:
            raw = resp.read()
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        log.info("security_advisories: fetch skipped (%s)", exc)
        return []

    try:
        advisories = _parse_asa_json(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        log.warning("security_advisories: JSON parse error: %s", exc)
        return []

    _save_cache(advisories)
    log.debug("security_advisories: fetched %d entries", len(advisories))
    return advisories


def open_for_installed(
    advisories: list[Advisory],
    installed: list[tuple[str, str]],
) -> list[Advisory]:
    """Filter advisories to those affecting the given installed packages.

    An advisory is included when:
    1. status == "Vulnerable" (unfixed in the repos), AND
    2. At least one package in the advisory is installed, AND
    3. Installed version < fixed version (checked via `vercmp`), or
       fixed is None (no fix available yet — trust the status field).

    `installed` is a list of (name, version) pairs as returned by
    `pq.list_all()`.
    """
    installed_map: dict[str, str] = dict(installed)
    result: list[Advisory] = []

    for adv in advisories:
        if adv.status != "Vulnerable":
            continue
        for pkg in adv.packages:
            inst_ver = installed_map.get(pkg)
            if inst_ver is None:
                continue
            if adv.fixed is None:
                # No fix yet — status alone says vulnerable
                result.append(adv)
                break
            try:
                if pq.vercmp(inst_ver, adv.fixed) < 0:
                    result.append(adv)
                    break
            except Exception:  # noqa: BLE001
                # vercmp failure: conservatively include the advisory
                result.append(adv)
                break

    return result


def arch_audit_present() -> bool:
    """Return True if the `arch-audit` tool is installed.

    When arch-audit is available, we skip our own advisory check to
    avoid double-reporting the same CVEs.
    """
    return shutil.which("arch-audit") is not None
