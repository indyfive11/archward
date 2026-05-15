from __future__ import annotations

from archward.aur.adapters._pacman_like import _PacmanLikeAdapter


class YayAdapter(_PacmanLikeAdapter):
    name = "yay"
    interactive_extra_flags = ("--editmenu=false", "--diffmenu=false", "--cleanmenu=false")
