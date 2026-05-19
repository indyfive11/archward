"""Tests for PipelineResult dataclass (v0.4.12) — snapshot_id field."""

from __future__ import annotations

from archward.pipeline.pipeline import PipelineResult


def test_snapshot_id_defaults_none() -> None:
    r = PipelineResult()
    assert r.snapshot_id is None


def test_snapshot_id_can_be_set() -> None:
    r = PipelineResult(snapshot_id="2026-05-19_110726")
    assert r.snapshot_id == "2026-05-19_110726"
