"""Shared network-read hygiene (v0.5).

Every remote fetch reads through `bounded_read` so a huge (malicious or
broken) response body can never OOM archward. Caps are per-caller — sized
to the real payload, not one-size-fits-all: the advisories archive
(security.archlinux.org/all.json) is cumulative and already ~1 MiB, so its
cap must stay generous or the security check silently dies.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

#: Arch News Atom feed — measured ~16 KiB.
NEWS_MAX_BYTES = 1024 * 1024
#: AUR RPC info responses — tiny.
AUR_METADATA_MAX_BYTES = 1024 * 1024
#: security.archlinux.org/all.json — cumulative archive, ~1 MiB in 2026 and
#: growing. A regression test pins this at >= 4 MiB so nobody "tidies" it
#: down and silently kills the advisories check.
ADVISORIES_MAX_BYTES = 8 * 1024 * 1024


def bounded_read(resp, limit: int) -> bytes:
    """Read at most `limit` bytes from a urllib response.

    Raises OSError when the body exceeds the limit, so callers' existing
    fetch-failure paths (log + skip) engage instead of an unbounded read.
    """
    data = resp.read(limit + 1)
    if len(data) > limit:
        log.warning(
            "response body exceeded the %d-byte cap — treating as fetch failure",
            limit,
        )
        raise OSError(f"response exceeded {limit} bytes")
    return data
