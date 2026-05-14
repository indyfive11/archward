"""Risk classification.

Per audit C3, this phase also runs `pacman -Sup` to surface replacements and
conflicts that --noconfirm would silently default through.

Per audit C4, kernel-headers packages match kernel_patterns and classify HIGH
with is_kernel=True.
"""

from __future__ import annotations

import fnmatch
import logging

from archward.events import EventBus
from archward.models.config import ConfigModel
from archward.models.update import PendingUpdate, RiskLevel
from archward.pacman import query as pq

log = logging.getLogger(__name__)

PHASE = "risk"


def _matches_any(pkg: str, patterns: tuple[str, ...]) -> str | None:
    for pat in patterns:
        if fnmatch.fnmatch(pkg, pat):
            return pat
    return None


def classify_one(pkg: str, old_version: str, new_version: str, cfg: ConfigModel) -> PendingUpdate:
    # 1. Exact-match HIGH list.
    if pkg in cfg.risk.high:
        return PendingUpdate(
            name=pkg,
            old_version=old_version,
            new_version=new_version,
            source="official",
            risk=RiskLevel.HIGH,
            is_kernel=False,
            reason="in risk.high",
        )

    # 2. Kernel patterns (with exclude check).
    excl = _matches_any(pkg, cfg.risk.kernel_pattern_exclude)
    if excl is None:
        kpat = _matches_any(pkg, cfg.risk.kernel_patterns)
        if kpat is not None:
            return PendingUpdate(
                name=pkg,
                old_version=old_version,
                new_version=new_version,
                source="official",
                risk=RiskLevel.HIGH,
                is_kernel=True,
                reason=f"kernel pattern {kpat}",
            )

    # 3. Medium patterns.
    mpat = _matches_any(pkg, cfg.risk.medium_patterns)
    if mpat is not None:
        return PendingUpdate(
            name=pkg,
            old_version=old_version,
            new_version=new_version,
            source="official",
            risk=RiskLevel.MEDIUM,
            reason=f"medium pattern {mpat}",
        )

    # 4. Fallthrough.
    return PendingUpdate(
        name=pkg,
        old_version=old_version,
        new_version=new_version,
        source="official",
        risk=RiskLevel.LOW,
    )


def classify_pending(cfg: ConfigModel, bus: EventBus) -> list[PendingUpdate]:
    """Run checkupdates, classify each entry per cfg.risk rules."""
    bus.emit_start(PHASE, "Risk classification")
    pending = pq.checkupdates()
    bus.emit_log(PHASE, f"checkupdates: {len(pending)} pending official updates")

    classified = [classify_one(p.name, p.old_version, p.new_version, cfg) for p in pending]

    high = sum(1 for u in classified if u.risk is RiskLevel.HIGH)
    medium = sum(1 for u in classified if u.risk is RiskLevel.MEDIUM)
    low = sum(1 for u in classified if u.risk is RiskLevel.LOW)
    bus.emit_log(PHASE, f"Classified: {high} HIGH, {medium} MEDIUM, {low} LOW")

    bus.emit_result(
        PHASE,
        f"{len(classified)} pending: {high} HIGH, {medium} MEDIUM, {low} LOW",
        payload={"pending": [u.model_dump(mode="json") for u in classified]},
    )
    return classified


def preview_transaction(bus: EventBus) -> pq.TransactionPreview:
    """Audit C3: surface what `pacman -Syu --noconfirm` would silently decide."""
    preview = pq.preview_transaction()
    bus.emit_log(PHASE, f"Transaction preview: {preview.package_count} packages")
    if preview.replacements:
        for old, new in preview.replacements:
            bus.emit_log(PHASE, f"  WILL REPLACE: {old} -> {new}")
    if preview.conflicts:
        for c in preview.conflicts:
            bus.emit_log(PHASE, f"  ALERT: {c}")

    bus.emit_result(
        PHASE,
        f"preview: {preview.package_count} packages, "
        f"{len(preview.replacements)} replacements, {len(preview.conflicts)} alerts",
        payload={
            "package_count": preview.package_count,
            "replacement_count": len(preview.replacements),
            "conflict_count": len(preview.conflicts),
        },
    )
    return preview
