"""`archward aur quarantine {list,clear}` — AUR build quarantine management.

Both commands are read/write on the user-owned state JSON only; no sudo
needed. Works in a plain TTY (Qt-free).
"""

from __future__ import annotations

import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from archward.app import build_config
from archward.aur.quarantine import AurQuarantine


def _fmt_ts(ts: float | None) -> str:
    if ts is None:
        return "—"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def _status_label(status: str) -> str:
    return {"quarantined": "quarantined", "counting": "counting", "resolved": "resolved"}.get(
        status, status
    )


# ── list ──────────────────────────────────────────────────────────────────────


def cmd_quarantine_list(args, config_path: Path | None) -> int:
    """Print a table of all quarantine entries (active + resolved)."""
    cfg = build_config(config_path)
    q = AurQuarantine(cfg.aur)
    q.load()
    entries = q.entries()

    if not entries:
        print("No AUR quarantine entries.")
        return 0

    cols = shutil.get_terminal_size((100, 24)).columns
    err_w = max(20, min(50, cols - 66))

    header = (
        f"{'package':24}  {'version':20}  {'status':12}  "
        f"{'fails':>5}  {'retry/resolved':14}  {'last error':{err_w}}"
    )
    print(header)
    print("-" * min(cols - 1, len(header)))

    for pkg, entry in entries:
        if entry.status == "quarantined":
            date_col = _fmt_ts(entry.retry_after)
        elif entry.status == "resolved":
            date_col = _fmt_ts(entry.resolved_at)
        else:
            date_col = "—"

        err_snippet = (entry.last_error or "")[:err_w]
        print(
            f"{pkg:24}  {entry.version:20}  {_status_label(entry.status):12}  "
            f"{entry.failure_count:>5}  {date_col:14}  {err_snippet}"
        )

    active_count = sum(1 for _, e in entries if e.status != "resolved")
    resolved_count = len(entries) - active_count
    footer_parts = []
    if active_count:
        footer_parts.append(f"{active_count} active")
    if resolved_count:
        footer_parts.append(f"{resolved_count} resolved")
    print(f"\n{', '.join(footer_parts)}")
    return 0


# ── clear ─────────────────────────────────────────────────────────────────────


def cmd_quarantine_clear(args, config_path: Path | None) -> int:
    """Clear one or all active quarantine entries."""
    cfg = build_config(config_path)
    q = AurQuarantine(cfg.aur)
    q.load()

    pkg: str | None = getattr(args, "package", None)

    if pkg is not None:
        entry = q.entry(pkg)
        if entry is None:
            print(f"archward aur quarantine clear: {pkg!r} not found in quarantine state", file=sys.stderr)
            return 2
        if entry.status == "resolved":
            print(f"{pkg}: already resolved (nothing to clear)")
            return 0
        q.clear(pkg)
        q.save()
        print(f"Cleared quarantine for {pkg}.")
        return 0

    # Clear all active entries
    if not getattr(args, "yes", False):
        active = q.active_entries()
        if not active:
            print("No active quarantine entries to clear.")
            return 0
        print(f"Will clear {len(active)} active quarantine entr{'y' if len(active) == 1 else 'ies'}:")
        for p, e in active:
            print(f"  {p} {e.version} ({e.status})")
        try:
            answer = input("proceed? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            return 0
        if answer != "y":
            print("Aborted.")
            return 0

    count = q.clear()
    q.save()
    if count:
        print(f"Cleared {count} quarantine entr{'y' if count == 1 else 'ies'}.")
    else:
        print("No active quarantine entries to clear.")
    return 0


# ── pending ───────────────────────────────────────────────────────────────────


def cmd_pending(args, config_path: Path | None) -> int:
    """List pending AUR updates via the configured helper."""
    cfg = build_config(config_path)
    from archward.aur.helper import discover
    helper = discover(tuple(cfg.aur.helper_preference))
    if helper is None:
        print(
            "No AUR helper found. Install yay, paru, or aurutils and ensure "
            "it's in your PATH.",
            file=sys.stderr,
        )
        return 2
    try:
        pending = helper.list_pending()
    except Exception as exc:
        print(f"Failed to query AUR helper ({helper.name}): {exc}", file=sys.stderr)
        return 1
    if not pending:
        print(f"No pending AUR updates ({helper.name}).")
        return 0
    print(f"{len(pending)} pending AUR update(s) ({helper.name}):")
    for name, old, new in sorted(pending):
        print(f"  {name:<30} {old} → {new}")
    return 0
