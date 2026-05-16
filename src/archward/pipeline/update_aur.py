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
from datetime import datetime, timezone
from typing import Protocol

from archward.aur.helper import AurHelper, discover
from archward.aur.quarantine import AurQuarantine, QuarantineAction, _classify_error
from archward.events import EventBus
from archward.models.aur import AurResult, BuildFailure, QuarantineSnapshot
from archward.models.config import ConfigModel
from archward.pacman.runner import PromptProvider
from archward.privilege.sudo import SudoStrategy


class PkgbuildReviewer(Protocol):
    """Callback signature for the PKGBUILD review modal flow.

    Called once per pending AUR package (when noconfirm=False).
    Returns True to approve building the package; False to skip it.
    `cancel_all_requested` short-circuits the loop without further calls.
    """

    def review(self, pkg: str) -> bool: ...
    def cancel_all_requested(self) -> bool: ...
    def reset(self) -> None: ...

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
    prompt_provider: PromptProvider | None = None,
    pkgbuild_reviewer: PkgbuildReviewer | None = None,
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

    # Load quarantine state and announce any active entries upfront so the
    # user always knows what's being skipped and why, even before the build.
    q = AurQuarantine(cfg.aur)
    q.load()
    for ae_pkg, ae_entry in q.active_entries():
        if ae_entry.status == "quarantined" and ae_entry.retry_after is not None:
            retry_date = datetime.fromtimestamp(
                ae_entry.retry_after, tz=timezone.utc
            ).date()
            bus.emit_log(
                PHASE,
                f"[quarantine] {ae_pkg} {ae_entry.version}: active "
                f"({ae_entry.failure_count} failures) — skipping until {retry_date}",
            )
        elif ae_entry.status == "counting":
            bus.emit_log(
                PHASE,
                f"[quarantine] {ae_pkg} {ae_entry.version}: counting "
                f"({ae_entry.failure_count}/{cfg.aur.quarantine_min_failures} failures) — will try",
            )

    # Determine which pending packages are quarantined (skip) vs retry window.
    quarantine_ignored: list[str] = []
    for pkg, _old, new in pending:
        action, entry = q.check(pkg, new)
        if action is QuarantineAction.SKIP and entry is not None:
            quarantine_ignored.append(pkg)
            retry_date = (
                datetime.fromtimestamp(entry.retry_after, tz=timezone.utc).date()
                if entry.retry_after is not None else "?"
            )
            bus.emit_log(
                PHASE,
                f"Skipping {pkg} {entry.version} (quarantined — "
                f"retry window opens {retry_date})",
            )
        elif action is QuarantineAction.RETRY and entry is not None:
            bus.emit_log(
                PHASE,
                f"Retrying {pkg} {entry.version} (quarantine retry window opened)",
            )

    # If everything is quarantined, skip the helper entirely.
    pending_pkgs = {pkg for pkg, _, _ in pending}
    if pending_pkgs and pending_pkgs == set(quarantine_ignored):
        bus.emit_log(PHASE, "All pending AUR updates are quarantined — skipping update run")
        q.save()
        bus.emit_result(
            PHASE,
            f"all {len(quarantine_ignored)} pending update(s) quarantined (skipped)",
        )
        return AurResult(
            exit_code=0,
            failures=(),
            skipped=False,
            quarantine=_build_quarantine_snapshot(q),
        )

    # F3 — PKGBUILD review modal. When interactive AUR is requested and a
    # reviewer callback is wired, ask the user per package; rejected
    # packages get added to the --ignore list so yay/paru skip them.
    review_ignored: list[str] = []
    if not cfg.pacman.noconfirm and pkgbuild_reviewer is not None and pending:
        pkgbuild_reviewer.reset()
        bus.emit_log(PHASE, "Reviewing PKGBUILDs (one modal per package)…")
        for pkg, _old, _new in pending:
            if pkg in quarantine_ignored:
                continue  # already handled
            if pkgbuild_reviewer.cancel_all_requested():
                bus.emit_log(PHASE, "PKGBUILD review cancelled by user — aborting AUR phase.")
                bus.emit_result(PHASE, "AUR phase aborted (user cancelled PKGBUILD review)")
                return AurResult(
                    exit_code=130,
                    failures=(),
                    skipped=True,
                    skip_reason="user cancelled PKGBUILD review",
                )
            approved = pkgbuild_reviewer.review(pkg)
            if not approved and not pkgbuild_reviewer.cancel_all_requested():
                review_ignored.append(pkg)
                bus.emit_log(PHASE, f"  rejected: {pkg} (added to --ignore)")
        if pkgbuild_reviewer.cancel_all_requested():
            bus.emit_log(PHASE, "PKGBUILD review cancelled by user — aborting AUR phase.")
            bus.emit_result(PHASE, "AUR phase aborted (user cancelled PKGBUILD review)")
            return AurResult(
                exit_code=130,
                failures=(),
                skipped=True,
                skip_reason="user cancelled PKGBUILD review",
            )
        if review_ignored:
            bus.emit_log(
                PHASE,
                f"Skipping {len(review_ignored)} rejected package(s): {', '.join(review_ignored)}",
            )

    effective_ignore = list(ignore or []) + review_ignored + quarantine_ignored

    exit_code, captured = helper.run_update(
        ignore=effective_ignore,
        strategy=strategy,
        bus=bus,
        cancel_event=cancel_event,
        noconfirm=cfg.pacman.noconfirm,
        prompt_provider=prompt_provider if not cfg.pacman.noconfirm else None,
    )

    failures = scan_build_failures(captured)
    if failures:
        bus.emit_log(PHASE, f"WARN: {len(failures)} package(s) failed to build:")
        for f in failures:
            bus.emit_log(PHASE, f"  - {f.package}")

    # Record quarantine state for all attempted packages.
    pending_versions = {pkg: new for pkg, _old, new in pending}
    failed_pkgs = {f.package for f in failures}
    skipped_pkgs = set(quarantine_ignored) | set(review_ignored)

    for f in failures:
        version = pending_versions.get(f.package, "unknown")
        just_activated = q.record_failure(f.package, version, f.last_lines)
        if just_activated:
            hint = _classify_error(f.last_lines)
            if hint:
                bus.emit_log(PHASE, f"  Hint: {hint}")

    for pkg, _old, new in pending:
        if pkg not in failed_pkgs and pkg not in skipped_pkgs:
            q.record_success(pkg)

    q.save()

    # Build result string — reflect quarantine in the phase rail.
    n_quarantined = len(quarantine_ignored)
    qsuffix = f" — {n_quarantined} quarantined (skipped)" if n_quarantined else ""
    if exit_code == 0 and not failures:
        bus.emit_result(PHASE, f"AUR updates completed{qsuffix}")
    elif failures:
        bus.emit_result(PHASE, f"completed with {len(failures)} build failure(s){qsuffix}")
    else:
        bus.emit_result(PHASE, f"helper exited {exit_code}{qsuffix}")

    return AurResult(
        exit_code=exit_code,
        failures=tuple(failures),
        skipped=False,
        quarantine=_build_quarantine_snapshot(q),
    )


def _build_quarantine_snapshot(q: AurQuarantine) -> QuarantineSnapshot | None:
    active = q.active_entries()
    if not active:
        return None
    rows: list[tuple[str, str, str, int, str | None]] = []
    for pkg, entry in active:
        retry_iso = (
            datetime.fromtimestamp(entry.retry_after, tz=timezone.utc).isoformat()
            if entry.retry_after is not None else None
        )
        rows.append((pkg, entry.version, entry.status, entry.failure_count, retry_iso))
    return QuarantineSnapshot(active=tuple(rows))
