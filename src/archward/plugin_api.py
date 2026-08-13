"""Frozen public API for archward verify-check plugins (v0.5).

Import plugin-facing symbols from here, not from archward's internal module
paths::

    from archward.plugin_api import CheckStatus, VerifyCheck

Everything in ``__all__`` is a stability promise: these names re-export the
*same objects* archward uses internally (``VerifyCheck`` identity matters —
the plugin loader ``isinstance``-checks returned rows), and they only change
compatibly within an ``API_VERSION``. The old internal import paths
(``archward.models.verify`` etc.) continue to work but are considered
deprecated for plugin use.

See docs/plugins.md for the full plugin author guide.
"""

from __future__ import annotations

from archward.models.config import ConfigModel
from archward.models.snapshot import Snapshot
from archward.models.verify import CheckStatus, VerifyCheck

#: Incremented only on a breaking change to the plugin contract.
API_VERSION = 1

#: The entry-point group plugins register under (canonical definition —
#: the verify phase imports it from here).
PLUGIN_ENTRY_POINT_GROUP = "archward.verify_checks"

#: The bucket plugin-contributed checks land in. Plugins should use this
#: for their VerifyCheck.bucket rather than the literal string.
PLUGIN_BUCKET = "plugin"

__all__ = [
    "API_VERSION",
    "PLUGIN_BUCKET",
    "PLUGIN_ENTRY_POINT_GROUP",
    "CheckStatus",
    "ConfigModel",
    "Snapshot",
    "VerifyCheck",
]
