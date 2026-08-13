"""`archward history` — list and inspect run-history records.

Run records are written by every pipeline run (GUI or CLI) to
``~/.local/state/archward/runs/<run_id>.json``; see docs/cli.md for the
document schema and its stability contract. This module is the TTY surface:

    archward history [list] [--limit N | --all] [--json]
    archward history show <run_id|latest> [--json]

`--json` on list emits summary rows only; `show --json` dumps the full raw
document. Bare `archward history` defaults to list — the one deliberate
exception to the missing-action convention (documented in docs/cli.md).
"""

from __future__ import annotations

import json
import shutil as _shutil
import sys
from datetime import datetime
from pathlib import Path

from archward.cli_subcommands.snapshot import _human_age
from archward.pipeline import run_record


def _fmt_duration(seconds: float | None) -> str:
    if seconds is None:
        return "?"
    if seconds < 60:
        return f"{seconds:.0f}s"
    minutes = int(seconds // 60)
    return f"{minutes}m{seconds - minutes * 60:02.0f}s"


def _age_of(doc: dict) -> str:
    raw = doc.get("started")
    if not raw:
        return "?"
    try:
        started = datetime.fromisoformat(raw)
    except ValueError:
        return "?"
    now = datetime.now(started.tzinfo) if started.tzinfo else datetime.now()
    return _human_age(int((now - started).total_seconds()))


# ── list ──────────────────────────────────────────────────────────────────


def cmd_list(args, config_path: Path | None) -> int:
    files = run_record.list_run_files()
    docs = [d for d in (run_record.load_run(p) for p in files) if d is not None]

    limit = None if getattr(args, "all", False) else max(0, getattr(args, "limit", 20))
    visible = docs if limit is None else docs[:limit]

    if getattr(args, "json", False):
        print(json.dumps([run_record.summary_row(d) for d in visible], indent=2))
        return 0

    if not docs:
        print(f"no run history in {run_record.runs_dir()}")
        return 0

    cols = _shutil.get_terminal_size((100, 24)).columns
    header = (
        f"{'run':22}  {'age':>9}  {'result':26}  {'duration':>8}  "
        f"{'mode':11}  snapshot"
    )
    print(header)
    print("-" * min(cols - 1, len(header) + 20))
    for doc in visible:
        result = doc.get("result") or {}
        tag = result.get("tag") or "(crashed)"
        print(
            f"{doc.get('run_id', '?'):22}  "
            f"{_age_of(doc):>9}  "
            f"{tag:26}  "
            f"{_fmt_duration(doc.get('duration_s')):>8}  "
            f"{doc.get('mode') or '?':11}  "
            f"{doc.get('snapshot_id') or '-'}"
        )

    if limit is not None and len(docs) > limit:
        print()
        print(f"... and {len(docs) - limit} older run(s). Use --all to see them.")
    return 0


# ── show ──────────────────────────────────────────────────────────────────


def _resolve_run_file(run_id: str) -> Path | None:
    files = run_record.list_run_files()
    if run_id == "latest":
        return files[0] if files else None
    for p in files:
        if p.stem == run_id:
            return p
    return None


def cmd_show(args, config_path: Path | None) -> int:
    path = _resolve_run_file(args.run_id)
    if path is None:
        print(
            f"archward history show: run not found: {args.run_id} "
            f"(see `archward history list`)",
            file=sys.stderr,
        )
        return 3

    doc = run_record.load_run(path)
    if doc is None:
        print(f"archward history show: unreadable run file: {path}", file=sys.stderr)
        return 3

    if getattr(args, "json", False):
        # Raw document dump — the full-detail scripting surface. Validated
        # above so a corrupt/unreadable file exits 3 instead of piping
        # garbage to a consumer with exit 0.
        try:
            sys.stdout.write(path.read_text(encoding="utf-8"))
        except OSError:
            print(f"archward history show: unreadable run file: {path}", file=sys.stderr)
            return 3
        return 0

    result = doc.get("result") or {}
    print(f"Run: {doc.get('run_id', '?')}")
    print(f"  Started:   {doc.get('started', '?')}  ({_age_of(doc)})")
    print(f"  Duration:  {_fmt_duration(doc.get('duration_s'))}")
    print(f"  Mode:      {doc.get('mode', '?')}" + ("  (--no-aur)" if doc.get("no_aur") else ""))
    print(f"  Version:   archward {doc.get('archward_version', '?')}")
    tag = result.get("tag")
    if tag:
        extra = ""
        if result.get("secondary_tags"):
            extra = "  (+ " + ", ".join(result["secondary_tags"]) + ")"
        print(f"  Result:    {tag}{extra}")
        print(
            f"  Counts:    {result.get('fail_count', 0)} FAIL, "
            f"{result.get('warn_count', 0)} WARN"
            + ("  — reboot needed" if result.get("reboot_needed") else "")
        )
    else:
        print("  Result:    (none recorded — run crashed)")
    if doc.get("aborted_reason"):
        print(f"  Aborted:   {doc['aborted_reason']}")
    snap = doc.get("snapshot_id")
    print(
        f"  Snapshot:  {snap or '(none taken)'}"
        + ("  (may be pruned — check `archward snapshot list`)" if snap else "")
    )
    print()

    phases = doc.get("phases") or []
    print(f"Phases ({len(phases)}):")
    if phases:
        for ph in phases:
            status = ph.get("status") or "…"
            dur = _fmt_duration(ph.get("duration_s"))
            msg = ph.get("message") or ""
            print(f"  {ph.get('phase', '?'):16}  {status:8}  {dur:>8}  {msg}")
    else:
        print("  (none recorded)")

    err_lines = doc.get("update_error_lines") or []
    if err_lines:
        print()
        print("Update errors:")
        for line in err_lines:
            print(f"  {line}")

    pending = doc.get("pending") or []
    if pending:
        high = sum(1 for p in pending if p.get("risk") == "high")
        print()
        print(f"Packages in transaction: {len(pending)} ({high} HIGH risk)")

    aur = doc.get("aur")
    if aur:
        if aur.get("failures"):
            print()
            print(f"AUR build failures: {', '.join(f['package'] for f in aur['failures'])}")
        if aur.get("quarantine"):
            print(f"AUR quarantine active: {', '.join(q['package'] for q in aur['quarantine'])}")

    if doc.get("pacnew_count"):
        print()
        print(f"Pacnew files pending: {doc['pacnew_count']}")

    return 0
