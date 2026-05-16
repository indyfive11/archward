"""Tests for archward.system.arch_news (v0.4.5 F1 — news fetch + cache + filter)."""

from __future__ import annotations

import io
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from archward.system import arch_news as an
from archward.system.arch_news import NewsItem, fetch_news, first_run_since, since_from_snapshot, unread_since

# ── test data ─────────────────────────────────────────────────────────

_ATOM_TEMPLATE = """\
<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
{entries}
</feed>
"""

_ENTRY_TEMPLATE = """\
  <entry>
    <title>{title}</title>
    <link href="{link}"/>
    <published>{published}</published>
  </entry>
"""


def _make_atom(*items: tuple[str, str, str]) -> bytes:
    entries = "".join(
        _ENTRY_TEMPLATE.format(title=t, link=l, published=p) for t, l, p in items
    )
    return _ATOM_TEMPLATE.format(entries=entries).encode()


_JAN = "2026-01-15T12:00:00+00:00"
_FEB = "2026-02-20T09:00:00+00:00"
_MAR = "2026-03-10T18:30:00+00:00"

_FEED_BYTES = _make_atom(
    ("March News", "https://archlinux.org/news/march", _MAR),
    ("February News", "https://archlinux.org/news/february", _FEB),
    ("January News", "https://archlinux.org/news/january", _JAN),
)


def _fake_urlopen(url, timeout):
    return io.BytesIO(_FEED_BYTES)


def _dt(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


# ── _parse_atom ───────────────────────────────────────────────────────


def test_parse_atom_returns_items() -> None:
    items = an._parse_atom(_FEED_BYTES)
    assert len(items) == 3
    assert items[0].title == "March News"
    assert items[0].link == "https://archlinux.org/news/march"
    assert items[0].published == _dt(_MAR)


def test_parse_atom_empty_feed() -> None:
    items = an._parse_atom(_make_atom())
    assert items == []


def test_parse_atom_skips_missing_elements() -> None:
    xml = b'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><entry><title>X</title></entry></feed>'
    items = an._parse_atom(xml)
    assert items == []


def test_parse_atom_naive_date_gets_utc() -> None:
    naive = "2026-01-01T00:00:00"
    feed = _make_atom(("T", "http://x", naive))
    items = an._parse_atom(feed)
    assert items[0].published.tzinfo is not None


# ── fetch_news — network path ─────────────────────────────────────────


def test_fetch_news_returns_items_from_network(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(an, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
    items = fetch_news()
    assert len(items) == 3
    assert items[0].title == "March News"


def test_fetch_news_network_error_returns_empty(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(an, "state_dir", lambda: tmp_path)

    def boom(url, timeout):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    assert fetch_news() == []


def test_fetch_news_timeout_returns_empty(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(an, "state_dir", lambda: tmp_path)

    def boom(url, timeout):
        raise TimeoutError("timed out")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    assert fetch_news() == []


def test_fetch_news_writes_cache(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(an, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
    fetch_news()
    cache_file = tmp_path / an._CACHE_FILE
    assert cache_file.exists()
    data = json.loads(cache_file.read_text())
    assert len(data["items"]) == 3


# ── fetch_news — cache path ───────────────────────────────────────────


def test_fetch_news_cache_hit_skips_network(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(an, "state_dir", lambda: tmp_path)

    call_count = 0

    def counting_urlopen(url, timeout):
        nonlocal call_count
        call_count += 1
        return io.BytesIO(_FEED_BYTES)

    monkeypatch.setattr(urllib.request, "urlopen", counting_urlopen)

    fetch_news()              # first call: populates cache
    assert call_count == 1

    fetch_news()              # second call: should hit cache
    assert call_count == 1   # no additional network hit


def test_fetch_news_stale_cache_refetches(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(an, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

    # Write a cache with an old timestamp (2 hours ago).
    cache_path = tmp_path / an._CACHE_FILE
    cache_path.write_text(json.dumps({
        "fetched_at": time.time() - 7200,
        "items": [],
    }))

    items = fetch_news()
    assert len(items) == 3   # came from the network, not the empty cache


def test_fetch_news_fresh_cache_not_refetched(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(an, "state_dir", lambda: tmp_path)

    def should_not_call(url, timeout):
        raise AssertionError("network should not be hit for a fresh cache")

    monkeypatch.setattr(urllib.request, "urlopen", should_not_call)

    # Write a cache with a fresh timestamp (30 seconds ago).
    cache_path = tmp_path / an._CACHE_FILE
    cache_path.write_text(json.dumps({
        "fetched_at": time.time() - 30,
        "items": [
            {
                "title": "Cached",
                "link": "http://x",
                "published": _MAR,
            }
        ],
    }))

    items = fetch_news()
    assert len(items) == 1
    assert items[0].title == "Cached"


# ── unread_since ──────────────────────────────────────────────────────


def test_unread_since_filters_correctly() -> None:
    items = [
        NewsItem("Jan", "http://x", _dt(_JAN)),
        NewsItem("Feb", "http://x", _dt(_FEB)),
        NewsItem("Mar", "http://x", _dt(_MAR)),
    ]
    since = _dt("2026-02-01T00:00:00+00:00")
    result = unread_since(items, since)
    assert len(result) == 2
    assert result[0].title == "Feb"
    assert result[1].title == "Mar"


def test_unread_since_all_old_returns_empty() -> None:
    items = [NewsItem("Jan", "http://x", _dt(_JAN))]
    since = _dt("2026-06-01T00:00:00+00:00")
    assert unread_since(items, since) == []


def test_unread_since_all_new_returns_all() -> None:
    items = [
        NewsItem("Jan", "http://x", _dt(_JAN)),
        NewsItem("Feb", "http://x", _dt(_FEB)),
    ]
    since = _dt("2025-01-01T00:00:00+00:00")
    assert len(unread_since(items, since)) == 2


def test_unread_since_naive_since_treated_as_utc() -> None:
    items = [NewsItem("Jan", "http://x", _dt(_JAN))]
    naive_since = datetime(2025, 6, 1)  # no tzinfo
    result = unread_since(items, naive_since)
    assert len(result) == 1


# ── since_from_snapshot ───────────────────────────────────────────────


def test_since_from_snapshot_reads_timestamp(tmp_path) -> None:
    ts = 1748000000.0
    (tmp_path / ".timestamp").write_text(f"{ts}\n")
    result = since_from_snapshot(tmp_path)
    assert result is not None
    assert result == datetime.fromtimestamp(ts, tz=timezone.utc)


def test_since_from_snapshot_missing_file_returns_none(tmp_path) -> None:
    assert since_from_snapshot(tmp_path) is None


def test_since_from_snapshot_corrupt_file_returns_none(tmp_path) -> None:
    (tmp_path / ".timestamp").write_text("not-a-number")
    assert since_from_snapshot(tmp_path) is None


# ── first_run_since ───────────────────────────────────────────────────


def test_first_run_since_is_30_days_ago() -> None:
    now = datetime.now(tz=timezone.utc)
    result = first_run_since()
    delta = now - result
    # timedelta(days=30) minus a few microseconds has .days == 29
    assert 29 <= delta.days <= 30
