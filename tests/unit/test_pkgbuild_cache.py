"""Tests for PkgbuildCache (v0.4.7)."""

from __future__ import annotations

import hashlib
import time

from archward.aur.pkgbuild_cache import PkgbuildCache

_CONTENT = "pkgname=foo\npkgver=1.0\n"
_HASH = hashlib.sha256(_CONTENT.encode()).hexdigest()


def test_get_missing_returns_none() -> None:
    cache = PkgbuildCache()
    assert cache.get("foo") is None


def test_store_and_get_roundtrip() -> None:
    cache = PkgbuildCache()
    cache.store("foo", _CONTENT)
    entry = cache.get("foo")
    assert entry is not None
    assert entry.content == _CONTENT


def test_store_computes_hash() -> None:
    cache = PkgbuildCache()
    cache.store("foo", _CONTENT)
    entry = cache.get("foo")
    assert entry is not None
    assert entry.content_hash == _HASH


def test_store_sets_approved_at() -> None:
    before = time.time()
    cache = PkgbuildCache()
    cache.store("foo", _CONTENT)
    after = time.time()
    entry = cache.get("foo")
    assert entry is not None
    assert before <= entry.approved_at <= after


def test_save_load_roundtrip(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    cache = PkgbuildCache()
    cache.store("foo", _CONTENT)
    cache.save()

    cache2 = PkgbuildCache()
    cache2.load()
    entry = cache2.get("foo")
    assert entry is not None
    assert entry.content == _CONTENT
    assert entry.content_hash == _HASH


def test_corrupt_json_starts_fresh(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    state_file = tmp_path / "archward" / "pkgbuild_cache.json"
    state_file.parent.mkdir(parents=True)
    state_file.write_text("not valid json")

    cache = PkgbuildCache()
    cache.load()
    assert cache.get("anything") is None


def test_remove_existing_returns_true() -> None:
    cache = PkgbuildCache()
    cache.store("foo", _CONTENT)
    assert cache.remove("foo") is True
    assert cache.get("foo") is None


def test_remove_missing_returns_false() -> None:
    cache = PkgbuildCache()
    assert cache.remove("nonexistent") is False


def test_clear_returns_count() -> None:
    cache = PkgbuildCache()
    cache.store("foo", _CONTENT)
    cache.store("bar", "pkgname=bar\n")
    count = cache.clear()
    assert count == 2
    assert cache.get("foo") is None
    assert cache.get("bar") is None
