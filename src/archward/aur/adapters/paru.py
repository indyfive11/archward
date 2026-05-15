from __future__ import annotations

from archward.aur.adapters._pacman_like import _PacmanLikeAdapter


class ParuAdapter(_PacmanLikeAdapter):
    name = "paru"
    # paru's PKGBUILD-review menu is suppressed by --skipreview (one flag
    # covers the same ground as yay's three separate menu flags).
    interactive_extra_flags = ("--skipreview",)
