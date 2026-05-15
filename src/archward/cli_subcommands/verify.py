"""`archward verify [--snapshot ID]` — re-run verify against an existing snapshot.

The post-reboot diagnostic: catches failures that only surface at next boot
(DKMS modules that didn't rebuild, mkinitcpio hooks that didn't fire, pacnew
left unmerged so daemons read stale config, systemd unit syntax changes, etc.).

Reuses `pipeline.verify_phase.run_verify` — same checks as the full pipeline.
Including plugins discovered via the `archward.verify_checks` entry-point
group (e.g. the bundled ZeroTier example), so the verify view's plugin
bucket also lights up here.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from archward.app import build_config, build_sudo_strategy
from archward.events import EventBus
from archward.pipeline.report import derive_result
from archward.pipeline.snapshot import (
    latest_snapshot,
    load_snapshot_from_disk,
    validate_snapshot,
)
from archward.pipeline.verify_phase import run_verify
from archward.system import notify

log = logging.getLogger(__name__)


def cmd_verify(args, config_path: Path | None) -> int:
    """Resolve the snapshot, run verify, print result, emit notification.

    Exit codes:
      0  RESULT:SUCCESS / RESULT:NEEDS_REVIEW / RESULT:PACNEW_MERGE_NEEDED
      1  RESULT:VERIFY_FAILED
      2  RESULT:REBOOT_NEEDED  (informational; user must reboot)
      3  snapshot not found
    """
    cfg = build_config(config_path)

    # Resolve snapshot.
    if args.snapshot:
        snap_path = cfg.general.snapshot_dir / args.snapshot
        if not snap_path.is_dir():
            print(f"archward verify: snapshot not found: {snap_path}", file=sys.stderr)
            return 3
    else:
        latest = latest_snapshot(cfg.general.snapshot_dir)
        if latest is None:
            print(
                "archward verify: no snapshots in "
                f"{cfg.general.snapshot_dir}. Run `archward --dry-run` to take one.",
                file=sys.stderr,
            )
            return 3
        snap_path, _age = latest

    # Refuse an incomplete snapshot up front (v0.4.4 F4) rather than
    # producing partial/confusing verify output against a half-snapshot.
    problems = validate_snapshot(snap_path)
    if problems:
        print(
            f"archward verify: snapshot at {snap_path} is incomplete:",
            file=sys.stderr,
        )
        for prob in problems:
            print(f"  - {prob}", file=sys.stderr)
        print(
            "Pick another with `archward snapshot list`.",
            file=sys.stderr,
        )
        return 3

    snapshot = load_snapshot_from_disk(snap_path)
    if snapshot is None:
        print(
            f"archward verify: snapshot at {snap_path} is incomplete "
            "(missing .timestamp). Pick another with `archward snapshot list`.",
            file=sys.stderr,
        )
        return 3

    print(f"verifying against snapshot {snapshot.meta.snapshot_id}")
    print(f"  taken: {snapshot.meta.created_at.isoformat(timespec='seconds')}")
    print(f"  kernel at snapshot: {snapshot.meta.kernel_release}")
    print()

    bus = EventBus()
    # Subscribe a simple printer so check progress appears as the phase
    # runs. Verify is fast (~1-2s) so streaming isn't critical, but the
    # output helps a TTY user see the plugins running.
    bus.subscribe(lambda ev: _print_event(ev))

    # Need a sudo strategy because some checks (services list, etc.) may
    # shell out. We DON'T warm it here — verify checks are read-only and
    # NOPASSWD-friendly; on this codepath we shouldn't pop askpass.
    strategy = build_sudo_strategy(cfg)

    verify_result = run_verify(cfg, snapshot, bus, config_path=config_path)

    summary = derive_result(
        preflight_failed=False,
        update_exit_code=None,
        pending=[],
        verify=verify_result,
        pacnew_count=0,
    )

    print()
    print("=== archward verify result ===")
    print(summary.tag)
    for sec in summary.secondary_tags:
        print(f"  + {sec}")
    if summary.fail_count or summary.warn_count:
        print(f"  verify: {summary.fail_count} FAIL / {summary.warn_count} WARN")
    if summary.reboot_needed:
        print("  ACTION: Reboot to activate the new kernel.")

    # Build a minimal PipelineResult-shaped object for notify so the
    # existing notify_completion call signature works.
    class _Result:
        pass
    r = _Result()
    r.summary = summary
    r.verify = verify_result
    r.aur = None
    r.aborted_reason = None
    r.pacnew_count = 0
    notify.notify_completion(r, cfg)

    if summary.tag == "RESULT:VERIFY_FAILED":
        return 1
    if summary.tag == "RESULT:REBOOT_NEEDED":
        return 2
    return 0


def _print_event(ev) -> None:
    """Lightweight bus subscriber — prints each phase event to stdout."""
    from archward.events import PhaseEventKind

    if ev.kind is PhaseEventKind.PHASE_START:
        print(f"[{ev.phase}] {ev.message or ''}")
    elif ev.kind is PhaseEventKind.PHASE_LOG:
        if ev.message:
            print(f"  {ev.message}")
    elif ev.kind is PhaseEventKind.PHASE_RESULT:
        print(f"  → {ev.message or ''}")
