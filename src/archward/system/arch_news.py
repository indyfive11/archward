"""Arch News RSS pre-flight check (v0.4.5).

Fetches the Arch Linux news Atom feed and surfaces items published since
the user's last archward update run. Used by the preflight gate to WARN
before an update if news exists that the user may not have seen.

Pure Python, Qt-free, no new dependencies (urllib + xml.etree stdlib).
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from archward.config.paths import state_dir

log = logging.getLogger(__name__)

_FEED_URL = "https://archlinux.org/feeds/news/"
_NS = "{http://www.w3.org/2005/Atom}"
_CACHE_FILE = "news_cache.json"
_CACHE_TTL_S = 3600  # 1 hour


@dataclass(frozen=True)
class NewsItem:
    title: str
    link: str
    published: datetime  # UTC-aware


# ── parsing ───────────────────────────────────────────────────────────


def _parse_atom(xml_bytes: bytes) -> list[NewsItem]:
    root = ET.fromstring(xml_bytes)
    items: list[NewsItem] = []
    for entry in root.findall(f"{_NS}entry"):
        title_el = entry.find(f"{_NS}title")
        link_el = entry.find(f"{_NS}link")
        pub_el = entry.find(f"{_NS}published")
        if title_el is None or link_el is None or pub_el is None:
            continue
        title = (title_el.text or "").strip()
        link = link_el.get("href", "").strip()
        pub_str = (pub_el.text or "").strip()
        try:
            pub = datetime.fromisoformat(pub_str)
        except ValueError:
            log.debug("arch_news: unparseable date %r", pub_str)
            continue
        if pub.tzinfo is None:
            pub = pub.replace(tzinfo=timezone.utc)
        items.append(NewsItem(title=title, link=link, published=pub))
    return items


# ── cache ─────────────────────────────────────────────────────────────


def _cache_path() -> Path:
    return state_dir() / _CACHE_FILE


def _load_cache() -> tuple[float, list[NewsItem]] | None:
    path = _cache_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        fetched_at: float = data["fetched_at"]
        items = [
            NewsItem(
                title=i["title"],
                link=i["link"],
                published=datetime.fromisoformat(i["published"]),
            )
            for i in data.get("items", [])
        ]
        return fetched_at, items
    except Exception:  # noqa: BLE001
        return None


def _save_cache(items: list[NewsItem]) -> None:
    path = _cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "fetched_at": time.time(),
            "items": [
                {
                    "title": item.title,
                    "link": item.link,
                    "published": item.published.isoformat(),
                }
                for item in items
            ],
        }
        path.write_text(json.dumps(payload, indent=2))
    except OSError as exc:
        log.warning("arch_news: could not write cache: %s", exc)


# ── public API ────────────────────────────────────────────────────────


def fetch_news(timeout: float = 8.0) -> list[NewsItem]:
    """Return the latest Arch News items.

    Checks the on-disk cache first (TTL: 1 hour). Falls back to a live
    fetch if the cache is stale or missing. Returns [] on any network or
    parse failure so callers can treat an offline system as a SKIP.
    """
    cached = _load_cache()
    if cached is not None:
        fetched_at, items = cached
        if time.time() - fetched_at < _CACHE_TTL_S:
            log.debug("arch_news: cache hit (%d items)", len(items))
            return items

    log.debug("arch_news: fetching %s", _FEED_URL)
    try:
        with urllib.request.urlopen(_FEED_URL, timeout=timeout) as resp:
            xml_bytes = resp.read()
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        log.info("arch_news: fetch skipped (%s)", exc)
        return []

    try:
        items = _parse_atom(xml_bytes)
    except ET.ParseError as exc:
        log.warning("arch_news: XML parse error: %s", exc)
        return []

    _save_cache(items)
    log.debug("arch_news: fetched %d items", len(items))
    return items


def unread_since(items: list[NewsItem], since: datetime) -> list[NewsItem]:
    """Return items with a published date strictly after `since`.

    `since` should be UTC-aware. If it is naive, it is treated as UTC.
    """
    if since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    return [item for item in items if item.published > since]


def since_from_snapshot(snapshot_dir: Path) -> datetime | None:
    """Read the Unix epoch from a snapshot's .timestamp file.

    Returns a UTC-aware datetime, or None if the file is missing/corrupt.
    """
    ts_file = snapshot_dir / ".timestamp"
    if not ts_file.exists():
        return None
    try:
        ts = float(ts_file.read_text().strip())
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    except (ValueError, OSError):
        return None


def first_run_since() -> datetime:
    """Fallback window when no prior snapshot exists: 30 days back."""
    return datetime.now(tz=timezone.utc) - timedelta(days=30)
