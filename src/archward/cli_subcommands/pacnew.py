"""`archward pacnew {list,diff,apply}` — manual .pacnew resolution.

Mirrors the GUI's Pacnew view per-row actions for users in a TTY who
can't reach the Snapshot Browser. Reuses the pure-Python primitives:
  - find_pacnew_files() to scan /etc
  - classify() to apply the rule table → recommendation
  - render_diff() for stdout-friendly unified diffs
  - apply_action() for the Keep Ours / Take New / Edit / Leave verbs
"""

from __future__ import annotations

import sys
from pathlib import Path

from archward.app import build_config, build_sudo_strategy
from archward.models.pacnew import PacnewAction
from archward.pacman.pacnew import (
    apply_action,
    classify,
    find_pacnew_files,
    render_diff,
)


def _resolve_pacnew_pair(raw: str) -> tuple[Path, Path] | None:
    """Given either a live /etc path or a .pacnew path, return (live, pacnew).

    Returns None and prints an error if neither file exists.
    """
    p = Path(raw)
    if p.suffix == ".pacnew":
        live = p.with_suffix("")
        pacnew = p
    else:
        live = p
        pacnew = p.with_suffix(p.suffix + ".pacnew")

    if not pacnew.exists():
        print(
            f"archward pacnew: {pacnew} does not exist. "
            "(Pass the live path or its .pacnew sibling — both forms work.)",
            file=sys.stderr,
        )
        return None
    return live, pacnew


# ── list ──────────────────────────────────────────────────────────────────


def cmd_list(args, config_path: Path | None) -> int:
    cfg = build_config(config_path)
    files = find_pacnew_files()
    if not files:
        print("no .pacnew files under /etc.")
        return 0
    print(f"{len(files)} .pacnew file(s):")
    print()
    print(f"{'path':50}  strategy        note")
    print("-" * 100)
    for p in files:
        pf = classify(p, cfg.pacnew)
        strategy = pf.recommendation.value
        note = pf.note or ""
        print(f"{str(p):50}  {strategy:14}  {note}")
    return 0


# ── diff ──────────────────────────────────────────────────────────────────


def cmd_diff(args, config_path: Path | None) -> int:
    pair = _resolve_pacnew_pair(args.path)
    if pair is None:
        return 3
    live, pacnew = pair
    diff = render_diff(live, pacnew)
    if not diff.strip():
        print("(no differences — live and .pacnew are identical)")
        return 0
    sys.stdout.write(diff)
    if not diff.endswith("\n"):
        sys.stdout.write("\n")
    return 0


# ── apply ─────────────────────────────────────────────────────────────────


def cmd_apply(args, config_path: Path | None) -> int:
    cfg = build_config(config_path)
    strategy = build_sudo_strategy(cfg)

    pair = _resolve_pacnew_pair(args.path)
    if pair is None:
        return 3
    live, pacnew = pair

    pf = classify(pacnew, cfg.pacnew)
    action = PacnewAction(args.strategy)

    print(f"applying {action.value} to {pacnew}")
    print(f"  live target: {live}")
    print(f"  rule-classified strategy: {pf.recommendation.value}")
    if pf.note:
        print(f"  note: {pf.note}")

    try:
        apply_action(pf, action, strategy)
    except RuntimeError as e:
        print(f"FAIL: {e}", file=sys.stderr)
        return 1
    except ValueError as e:
        print(f"FAIL: {e}", file=sys.stderr)
        return 2

    print(f"applied: {action.value}")
    return 0
