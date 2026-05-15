"""Compute a unified diff between two configs as TOML text.

Used by the Profiles tab's "Diff vs default…" affordance to show users
what a profile actually changes relative to archward defaults. Kept
separate from the Qt dialog so the diff computation is pure-Python and
trivially unit-testable.
"""

from __future__ import annotations

import difflib

import tomli_w

from archward.models.config import ConfigModel


def _cfg_to_toml_lines(cfg: ConfigModel) -> list[str]:
    """Serialize a ConfigModel to TOML lines with trailing newlines.

    Matches how `loader.write_config` shapes the file (mode='json',
    exclude_none=True) so the diff lines line up with what users see
    when they `cat config.toml`.
    """
    data = cfg.model_dump(mode="json", exclude_none=True)
    text = tomli_w.dumps(data)
    return text.splitlines(keepends=True)


def unified_diff(
    a: ConfigModel,
    b: ConfigModel,
    *,
    a_label: str = "defaults",
    b_label: str = "profile",
) -> list[str]:
    """Return a unified diff (a → b) as a list of lines, each ending in \\n.

    Empty list means the two configs are equivalent at the TOML
    serialization level. Header lines (`--- a_label`, `+++ b_label`,
    hunk markers) are included so the output is a complete unified diff
    suitable for display in a monospace viewer.
    """
    return list(difflib.unified_diff(
        _cfg_to_toml_lines(a),
        _cfg_to_toml_lines(b),
        fromfile=a_label,
        tofile=b_label,
        n=3,
    ))
