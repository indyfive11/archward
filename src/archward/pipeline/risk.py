"""Risk classification.

Per audit C3, this phase also runs `pacman -Sup` to surface replacements and
conflicts that --noconfirm would silently default through.

Per audit C4, kernel-headers packages match kernel_patterns and classify HIGH
with is_kernel=True.
"""

from __future__ import annotations

import fnmatch
import logging
import re
from dataclasses import dataclass, field

from archward.events import (
    EventBus,
    PhaseStatus,
    RiskPendingPayload,
    TransactionPreviewPayload,
)
from archward.models.config import ConfigModel
from archward.models.update import PendingUpdate, RiskLevel
from archward.pacman import query as pq

log = logging.getLogger(__name__)

PHASE = "risk"


def _major_version(v: str) -> int | None:
    m = re.match(r"(\d+)", v)
    return int(m.group(1)) if m else None


def _matches_any(pkg: str, patterns: tuple[str, ...]) -> str | None:
    for pat in patterns:
        if fnmatch.fnmatch(pkg, pat):
            return pat
    return None


def classify_one(pkg: str, old_version: str, new_version: str, cfg: ConfigModel) -> PendingUpdate:
    # 1. Exact-match HIGH list.
    if pkg in cfg.risk.high:
        reason = "in risk.high"
        old_maj = _major_version(old_version)
        new_maj = _major_version(new_version)
        if old_maj is not None and new_maj is not None and new_maj != old_maj:
            reason += " ⚠ MAJOR VERSION"
        return PendingUpdate(
            name=pkg,
            old_version=old_version,
            new_version=new_version,
            source="official",
            risk=RiskLevel.HIGH,
            is_kernel=False,
            reason=reason,
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


@dataclass(frozen=True)
class ClassifiedPending:
    """Classified pending updates plus whether the checkupdates call worked.

    check_ok=False means `updates` is empty because the check FAILED (error,
    timeout, binary missing) — not because nothing is pending.
    """

    updates: list[PendingUpdate] = field(default_factory=list)
    check_ok: bool = True
    check_error: str | None = None


def classify_pending(cfg: ConfigModel, bus: EventBus) -> ClassifiedPending:
    """Run checkupdates, classify each entry per cfg.risk rules."""
    bus.emit_start(PHASE, "Risk classification")
    cu = pq.checkupdates()
    if not cu.ok:
        bus.emit_log(PHASE, f"WARN: {cu.error} — pending-update list unavailable")
        bus.emit_result(
            PHASE,
            f"WARN: pending-update check unavailable ({cu.error})",
            PhaseStatus.WARN,
            payload=RiskPendingPayload(pending=(), check_error=cu.error),
        )
        return ClassifiedPending(check_ok=False, check_error=cu.error)
    pending = cu.pending
    bus.emit_log(PHASE, f"checkupdates: {len(pending)} pending official updates")

    classified = [classify_one(p.name, p.old_version, p.new_version, cfg) for p in pending]

    high = sum(1 for u in classified if u.risk is RiskLevel.HIGH)
    medium = sum(1 for u in classified if u.risk is RiskLevel.MEDIUM)
    low = sum(1 for u in classified if u.risk is RiskLevel.LOW)
    bus.emit_log(PHASE, f"Classified: {high} HIGH, {medium} MEDIUM, {low} LOW")

    bus.emit_result(
        PHASE,
        f"{len(classified)} pending: {high} HIGH, {medium} MEDIUM, {low} LOW",
        PhaseStatus.PASS,
        payload=RiskPendingPayload(pending=tuple(classified)),
    )
    return ClassifiedPending(updates=classified)


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

    # Status pinned PASS even with replacements/conflicts present — the
    # preview is informational; the WARN surface is the risk log lines.
    bus.emit_result(
        PHASE,
        f"preview: {preview.package_count} packages, "
        f"{len(preview.replacements)} replacements, {len(preview.conflicts)} alerts",
        PhaseStatus.PASS,
        payload=TransactionPreviewPayload(
            package_count=preview.package_count,
            replacement_count=len(preview.replacements),
            conflict_count=len(preview.conflicts),
        ),
    )
    return preview
