"""`archward risk` — risk-classify pending updates without running an update.

Calls `checkupdates` and classifies each pending package as HIGH, MEDIUM,
or LOW using the same rules as the update pipeline. Also shows pending AUR
updates (unclassified) if a helper is available. Always exits 0.
"""

from __future__ import annotations

from pathlib import Path

from archward.app import build_config
from archward.events import EventBus
from archward.models.update import RiskLevel
from archward.pipeline.risk import classify_pending, preview_transaction


def cmd_risk(args, config_path: Path | None) -> int:
    cfg = build_config(config_path)
    bus = EventBus()  # no subscriber — output formatted below

    outcome = classify_pending(cfg, bus)
    updates = outcome.updates

    if not outcome.check_ok:
        print(f"WARNING: {outcome.check_error} — pending-update list unavailable.")
    elif not updates:
        print("No pending official updates.")
    else:
        for level in (RiskLevel.HIGH, RiskLevel.MEDIUM, RiskLevel.LOW):
            group = [u for u in updates if u.risk is level]
            if not group:
                continue
            print(f"\n{level.upper()}:")
            for u in group:
                reason = f"  ({u.reason})" if u.reason else ""
                print(f"  {u.name:<30} {u.old_version} → {u.new_version}{reason}")

        preview = preview_transaction(bus)
        if preview.replacements:
            print("\nreplacements:")
            for old, new in preview.replacements:
                print(f"  {old} → {new}")
        if preview.conflicts:
            print("\nconflicts/warnings:")
            for c in preview.conflicts:
                print(f"  {c}")

    if not getattr(args, "no_aur", False) and cfg.aur.enabled:
        from archward.aur.helper import discover
        helper = discover(tuple(cfg.aur.helper_preference))
        if helper:
            try:
                aur_pending = helper.list_pending()
            except Exception:
                aur_pending = []
            if aur_pending:
                print(f"\nAUR ({helper.name}):")
                for name, old, new in sorted(aur_pending):
                    print(f"  {name:<30} {old} → {new}")

    return 0
