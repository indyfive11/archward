"""AUR package metadata — RPC v5 fetch + risk signal classification (v0.4.7).

Fetches maintainer info, vote count, and freshness from the AUR RPC API and
surfaces risk signals in the PKGBUILD review modal so users can see package
provenance at the point they're making an approval decision.

Pure Python, Qt-free, no new dependencies (urllib stdlib only).
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

log = logging.getLogger(__name__)

_AUR_RPC = "https://aur.archlinux.org/rpc/v5/info?arg[]={pkg}"
_TIMEOUT = 5.0
_RECENTLY_MODIFIED_DAYS = 7


@dataclass(frozen=True)
class AurPackageInfo:
    name: str
    maintainer: str | None   # None = orphaned
    submitter: str
    num_votes: int
    first_submitted: float   # Unix timestamp
    last_modified: float     # Unix timestamp
    out_of_date: bool        # True when OutOfDate field is non-null


def fetch_aur_info(pkg: str) -> AurPackageInfo | None:
    """Fetch package metadata from AUR RPC v5. Returns None on any failure."""
    url = _AUR_RPC.format(pkg=urllib.request.quote(pkg, safe=""))
    log.debug("aur_metadata: fetching %s", url)
    try:
        with urllib.request.urlopen(url, timeout=_TIMEOUT) as resp:
            data = json.loads(resp.read())
    except (urllib.error.URLError, OSError, TimeoutError, json.JSONDecodeError) as exc:
        log.info("aur_metadata: fetch failed for %r (%s)", pkg, exc)
        return None

    results = data.get("results", [])
    if not results:
        log.debug("aur_metadata: no results for %r", pkg)
        return None

    r = results[0]
    try:
        return AurPackageInfo(
            name=r.get("Name", pkg),
            maintainer=r.get("Maintainer") or None,
            submitter=r.get("Submitter", ""),
            num_votes=int(r.get("NumVotes", 0)),
            first_submitted=float(r.get("FirstSubmitted", 0)),
            last_modified=float(r.get("LastModified", 0)),
            out_of_date=r.get("OutOfDate") is not None,
        )
    except (TypeError, ValueError) as exc:
        log.warning("aur_metadata: parse error for %r: %s", pkg, exc)
        return None


def aur_risk_signals(info: AurPackageInfo) -> list[tuple[str, str]]:
    """Return (level, message) pairs for notable risk indicators.

    Levels: 'danger' | 'warn' | 'info'
    """
    signals: list[tuple[str, str]] = []

    if info.maintainer is None:
        signals.append(("danger", "Orphaned package — no active maintainer"))
    if info.out_of_date:
        signals.append(("danger", "Flagged out-of-date on AUR"))

    age_days = (time.time() - info.last_modified) / 86400
    if age_days < _RECENTLY_MODIFIED_DAYS:
        signals.append((
            "warn",
            f"Recently modified ({age_days:.0f}d ago) — review changes carefully",
        ))

    if info.num_votes < 5:
        signals.append(("info", f"Low vote count ({info.num_votes} votes)"))

    return signals
