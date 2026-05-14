from __future__ import annotations

from archward.aur.adapters._pacman_like import _PacmanLikeAdapter


class ParuAdapter(_PacmanLikeAdapter):
    name = "paru"
