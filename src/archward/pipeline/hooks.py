"""v2 hook seam — no-op stub for v1.

Pipeline already calls these at the right points; v2 fills in the body and adds
[hooks] to ConfigModel.
"""

from __future__ import annotations


class HookRunner:
    def __init__(self, cfg=None) -> None:
        self.cfg = cfg

    def run_pre_update(self, ctx) -> None:
        return

    def run_post_verify(self, ctx, result) -> None:
        return
