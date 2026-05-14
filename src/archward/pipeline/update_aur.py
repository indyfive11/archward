"""AUR update phase.

Flow:
  1. Resolve helper from cfg.aur.helper_preference.
  2. If no helper: emit `pacman -Qm` info (audit G5) and return SKIPPED.
  3. Run helper update; stream output.
  4. Scan captured stdout for build failures; capture last 50 lines of each.

The phase is non-fatal — AUR build failures are reported as warnings, never
escalate to RESULT:UPDATE_FAILED. The official-update phase already succeeded
by the time AUR runs.
"""

from __future__ import annotations

import logging
import re
import subprocess
import threading

from archward.aur.helper import AurHelper, discover
from archward.events import EventBus
from archward.models.aur import AurResult, BuildFailure
from archward.models.config import ConfigModel
from archward.privilege.sudo import SudoStrategy

log = logging.getLogger(__name__)

PHASE = "update_aur"

# Build failure markers — emitted by makepkg under both yay and paru.
_ERROR_MARKERS = (
    re.compile(r"==> ERROR:"),
    re.compile(r"failed to build", re.IGNORECASE),
    re.compile(r"could not satisfy dependencies", re.IGNORECASE),
)

# Package-context markers — used to attribute a failure to a specific package.
_PKG_CONTEXT_RE = re.compile(r"==>\s+(?:Building|Making package:)\s+(\S+)")
_ALT_PKG_CONTEXT_RE = re.compile(r"^:: building (\S+)")


def _list_installed_aur() -> list[tuple[str, str]]:
    """`pacman -Qm` — installed AUR / foreign packages, used when no helper exists."""
    try:
        r = subprocess.run(
            ["pacman", "-Qm"], check=False, capture_output=True, text=True
        )
    except FileNotFoundError:
        return []
    pairs: list[tuple[str, str]] = []
    for line in r.stdout.splitlines():
        parts = line.split(maxsplit=1)
        if len(parts) == 2:
            pairs.append((parts[0], parts[1]))
    return pairs


def scan_build_failures(captured: list[str], tail_lines: int = 50) -> list[BuildFailure]:
    """Walk the captured helper output, attributing failures to package context."""
    failures: list[BuildFailure] = []
    current_pkg: str | None = None
    # Track which packages we've already reported, to avoid duplicates when a
    # failed build emits multiple ERROR lines.
    reported: set[str] = set()

    for idx, line in enumerate(captured):
        m = _PKG_CONTEXT_RE.search(line) or _ALT_PKG_CONTEXT_RE.search(line)
        if m:
            current_pkg = m.group(1)
            continue
        if any(rx.search(line) for rx in _ERROR_MARKERS):
            if current_pkg is None:
                current_pkg = "(unknown)"
            if current_pkg in reported:
                continue
            reported.add(current_pkg)
            # Capture the last `tail_lines` lines up to and including this one.
            start = max(0, idx - tail_lines + 1)
            tail = tuple(captured[start : idx + 1])
            failures.append(BuildFailure(package=current_pkg, last_lines=tail))
    return failures


def _resolve_helper(cfg: ConfigModel) -> AurHelper | None:
    return discover(tuple(cfg.aur.helper_preference))


def run_aur_update(
    cfg: ConfigModel,
    strategy: SudoStrategy,
    bus: EventBus,
    *,
    ignore: list[str] | None = None,
    cancel_event: threading.Event | None = None,
    force_skip: bool = False,
) -> AurResult:
    """Run the AUR phase. `force_skip` is set by `--no-aur` or `aur.skip=true`."""
    bus.emit_start(PHASE, "AUR phase")

    if force_skip or not cfg.aur.enabled or cfg.aur.skip:
        reason = "skipped by --no-aur / cfg.aur.skip" if force_skip or cfg.aur.skip else "cfg.aur.enabled=false"
        bus.emit_log(PHASE, f"AUR phase skipped: {reason}")
        bus.emit_result(PHASE, "skipped")
        return AurResult(exit_code=0, failures=(), skipped=True, skip_reason=reason)

    helper = _resolve_helper(cfg)
    if helper is None:
        # Audit G5: still surface installed AUR list when no helper is available.
        installed = _list_installed_aur()
        bus.emit_log(
            PHASE,
            "No AUR helper detected. Install one of "
            f"{', '.join(cfg.aur.helper_preference)} to enable AUR updates.",
        )
        if installed:
            bus.emit_log(PHASE, f"Currently installed AUR / foreign packages ({len(installed)}):")
            for name, version in installed:
                bus.emit_log(PHASE, f"  {name:36s} {version}")
        bus.emit_result(PHASE, "skipped (no helper)")
        return AurResult(
            exit_code=0,
            failures=(),
            skipped=True,
            skip_reason="no AUR helper found on PATH",
        )

    bus.emit_log(PHASE, f"Using AUR helper: {helper.name}")
    pending = helper.list_pending()
    bus.emit_log(PHASE, f"{len(pending)} AUR update(s) pending")
    if not pending:
        bus.emit_result(PHASE, "no AUR updates pending")
        return AurResult(exit_code=0, failures=(), skipped=False)

    for pkg, old, new in pending:
        bus.emit_log(PHASE, f"  {pkg:36s} {old} -> {new}")

    exit_code, captured = helper.run_update(
        ignore=ignore or [], strategy=strategy, bus=bus, cancel_event=cancel_event
    )

    failures = scan_build_failures(captured)
    if failures:
        bus.emit_log(PHASE, f"WARN: {len(failures)} package(s) failed to build:")
        for f in failures:
            bus.emit_log(PHASE, f"  - {f.package}")

    if exit_code == 0 and not failures:
        bus.emit_result(PHASE, "AUR updates completed")
    elif failures:
        bus.emit_result(PHASE, f"completed with {len(failures)} build failure(s)")
    else:
        bus.emit_result(PHASE, f"helper exited {exit_code}")

    return AurResult(
        exit_code=exit_code,
        failures=tuple(failures),
        skipped=False,
    )
