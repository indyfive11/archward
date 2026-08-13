"""The frozen plugin facade (v0.5) — identity and stability contract.

Plugins isinstance-depend on VerifyCheck identity (verify_phase's loader
rejects structurally-identical foreign classes), so the facade must
re-export the *same objects*, never re-declarations.
"""

from __future__ import annotations

from archward import plugin_api
from archward.models.config import ConfigModel
from archward.models.snapshot import Snapshot
from archward.models.verify import CheckStatus, VerifyCheck
from archward.pipeline import verify_phase


def test_facade_reexports_identical_objects() -> None:
    assert plugin_api.VerifyCheck is VerifyCheck
    assert plugin_api.CheckStatus is CheckStatus
    assert plugin_api.ConfigModel is ConfigModel
    assert plugin_api.Snapshot is Snapshot


def test_api_version_and_constants() -> None:
    assert plugin_api.API_VERSION == 1
    assert plugin_api.PLUGIN_ENTRY_POINT_GROUP == "archward.verify_checks"
    assert plugin_api.PLUGIN_BUCKET == "plugin"


def test_verify_phase_uses_facade_constants() -> None:
    """The loader and the facade must never drift apart."""
    assert verify_phase.PLUGIN_ENTRY_POINT_GROUP is plugin_api.PLUGIN_ENTRY_POINT_GROUP
    assert verify_phase.PLUGIN_BUCKET is plugin_api.PLUGIN_BUCKET


def test_all_names_resolve() -> None:
    for name in plugin_api.__all__:
        assert getattr(plugin_api, name) is not None
