"""C-locale environment for subprocesses whose output archward parses.

Setting LANG=C alone is not enough: LC_ALL outranks LANG, and LANGUAGE
outranks both for gettext message catalogs — so a user running with
LC_ALL=de_DE.UTF-8 would get localized pacman output and silently break
error-marker and replacement parsing. All three variables are pinned.
"""

from __future__ import annotations

import os
from collections.abc import Mapping


def c_locale_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return a copy of `base` (default: os.environ) with the locale forced to C."""
    env = dict(os.environ if base is None else base)
    env["LANG"] = "C"
    env["LC_ALL"] = "C"
    env["LANGUAGE"] = ""
    return env
