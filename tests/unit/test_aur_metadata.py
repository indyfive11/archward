"""Tests for AurPackageInfo + risk signals (v0.4.7)."""

from __future__ import annotations

import time
import urllib.error
from unittest.mock import MagicMock, patch

from archward.aur.metadata import AurPackageInfo, aur_risk_signals, fetch_aur_info

_NOW = time.time()

_HEALTHY = AurPackageInfo(
    name="foo",
    maintainer="alice",
    submitter="alice",
    num_votes=42,
    first_submitted=_NOW - 86400 * 365,
    last_modified=_NOW - 86400 * 30,  # 30 days ago — not recent
    out_of_date=False,
)


def test_aur_risk_signals_clean() -> None:
    assert aur_risk_signals(_HEALTHY) == []


def test_aur_risk_signals_orphaned() -> None:
    info = AurPackageInfo(**{**_HEALTHY.__dict__, "maintainer": None})
    signals = aur_risk_signals(info)
    assert any(level == "danger" and "rphan" in msg for level, msg in signals)


def test_aur_risk_signals_out_of_date() -> None:
    info = AurPackageInfo(**{**_HEALTHY.__dict__, "out_of_date": True})
    signals = aur_risk_signals(info)
    assert any(level == "danger" and "out-of-date" in msg for level, msg in signals)


def test_aur_risk_signals_recent_modification() -> None:
    info = AurPackageInfo(**{**_HEALTHY.__dict__, "last_modified": _NOW - 86400 * 2})
    signals = aur_risk_signals(info)
    assert any(level == "warn" for level, msg in signals)


def test_aur_risk_signals_low_votes() -> None:
    info = AurPackageInfo(**{**_HEALTHY.__dict__, "num_votes": 2})
    signals = aur_risk_signals(info)
    assert any(level == "info" and "vote" in msg for level, msg in signals)


def test_fetch_aur_info_network_failure() -> None:
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("timeout")):
        result = fetch_aur_info("ghost-package")
    assert result is None


def test_fetch_aur_info_empty_results() -> None:
    mock_resp = MagicMock()
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    mock_resp.read.return_value = b'{"results": [], "type": "multiinfo"}'
    with patch("urllib.request.urlopen", return_value=mock_resp):
        result = fetch_aur_info("nonexistent")
    assert result is None


def test_fetch_aur_info_parses_fields() -> None:
    payload = b"""{
        "results": [{
            "Name": "yay",
            "Maintainer": "Jguer",
            "Submitter": "Jguer",
            "NumVotes": 3421,
            "FirstSubmitted": 1500000000,
            "LastModified": 1700000000,
            "OutOfDate": null
        }],
        "type": "multiinfo"
    }"""
    mock_resp = MagicMock()
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    mock_resp.read.return_value = payload
    with patch("urllib.request.urlopen", return_value=mock_resp):
        info = fetch_aur_info("yay")
    assert info is not None
    assert info.maintainer == "Jguer"
    assert info.num_votes == 3421
    assert info.out_of_date is False
