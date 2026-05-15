"""Pipeline orchestrator.

Phase 1 modes:
  - interactive: prompts on HIGH RISK before running pacman
  - auto:        aborts if HIGH RISK packages present
  - dry-run:     runs snapshot + gates + risk classification, then exits
"""

from __future__ import annotations

import logging
import sys
import threading
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from archward.events import EventBus
from archward.models.aur import AurResult
from archward.models.config import ConfigModel
from archward.models.gate import GateStatus
from archward.models.hook import HookResult
from archward.models.update import PendingUpdate, RiskLevel
from archward.models.verify import VerifyResult
from archward.pacman import query as pq
from archward.pacman.runner import PromptProvider
from archward.pipeline.update_aur import PkgbuildReviewer
from archward.pipeline import gates as gates_phase
from archward.pipeline import pacnew_phase
from archward.pipeline import retention
from archward.pipeline import risk as risk_phase
from archward.pipeline import snapshot as snapshot_phase
from archward.pipeline import update_aur
from archward.pipeline import update_official
from archward.pipeline import verify_phase
from archward.pipeline.hooks import HookRunner
from archward.pipeline.prompter import AutoNoPrompter, AutoYesPrompter, CliPrompter, Prompter
from archward.pipeline.report import ReportSummary, derive_result
from archward.privilege.sudo import SudoStrategy

log = logging.getLogger(__name__)


class Mode(StrEnum):
    INTERACTIVE = "interactive"
    DRY_RUN = "dry-run"
    AUTO = "auto"


@dataclass
class PipelineResult:
    preflight_failed: bool = False
    update_exit_code: int | None = None
    pending: list[PendingUpdate] = field(default_factory=list)
    deselected_packages: tuple[str, ...] = ()
    aur: AurResult | None = None
    verify: VerifyResult | None = None
    pacnew_count: int = 0
    pre_hook_results: tuple[HookResult, ...] = ()
    post_hook_results: tuple[HookResult, ...] = ()
    summary: ReportSummary | None = None
    aborted_reason: str | None = None


def _ask_yes_no(prompt: str) -> bool:
    """Read a y/N answer from stdin; default No."""
    try:
        answer = input(prompt).strip().lower()
    except EOFError:
        return False
    return answer in ("y", "yes")


def _dry_run_show_aur_pending(cfg: ConfigModel, bus: EventBus, *, no_aur: bool) -> None:
    """Surface AUR pending updates in dry-run without running the helper update."""
    from archward.aur.helper import discover  # local import to avoid cycle

    if no_aur or not cfg.aur.enabled or cfg.aur.skip:
        bus.emit_log("risk", "AUR: skipped (--no-aur or aur.enabled=false)")
        return
    helper = discover(tuple(cfg.aur.helper_preference))
    if helper is None:
        bus.emit_log(
            "risk",
            f"AUR: no helper detected from {list(cfg.aur.helper_preference)}",
        )
        return
    aur_pending = helper.list_pending()
    if not aur_pending:
        bus.emit_log("risk", f"AUR helper {helper.name}: 0 updates pending")
        return
    bus.emit_log("risk", f"AUR helper {helper.name}: {len(aur_pending)} update(s) pending:")
    for pkg, old, new in aur_pending:
        bus.emit_log("risk", f"  {pkg:36s} {old} -> {new}  [AUR]")


def _default_prompter(mode: Mode, auto_yes: bool) -> Prompter:
    """Pick a sensible prompter when the caller didn't supply one."""
    if auto_yes:
        return AutoYesPrompter()
    if mode is Mode.AUTO:
        return AutoNoPrompter()
    return CliPrompter()


def run_pipeline(
    cfg: ConfigModel,
    strategy: SudoStrategy,
    bus: EventBus,
    mode: Mode,
    *,
    auto_yes: bool = False,
    no_aur: bool = False,
    cancel_event: threading.Event | None = None,
    prompter: Prompter | None = None,
    config_path: Path | None = None,
    prompt_provider: PromptProvider | None = None,
    pkgbuild_reviewer: PkgbuildReviewer | None = None,
) -> PipelineResult:
    """Run the full pipeline. Never raises on update failure — see PipelineResult."""
    result = PipelineResult()
    hooks = HookRunner(cfg.hooks, bus)
    if prompter is None:
        prompter = _default_prompter(mode, auto_yes)

    # ── Pre-flight ──────────────────────────────────────────────────────────
    preflight = gates_phase.preflight_checks(cfg, bus)
    if gates_phase.any_fail(preflight):
        result.preflight_failed = True
        result.aborted_reason = "pre-flight failed"
        result.summary = derive_result(
            preflight_failed=True,
            update_exit_code=None,
            pending=[],
            verify=None,
            pacnew_count=0,
            was_dry_run=(mode is Mode.DRY_RUN),
        )
        return result

    # A pre-flight WARN (cache-safety, v0.4.4 F2) never hard-aborts, but
    # in an interactive run we give the user an explicit chance to bail
    # before we touch the system — rollback for THIS update may not work.
    # In auto/dry-run we don't prompt (AutoNoPrompter would spuriously
    # abort a WARN); it was already logged loudly.
    if mode is Mode.INTERACTIVE:
        warn = next(
            (
                g
                for g in preflight
                if g.status is GateStatus.WARN and g.can_override
            ),
            None,
        )
        if warn is not None and not prompter.confirm_gate_override(warn):
            result.aborted_reason = f"{warn.name}: {warn.message}"
            result.summary = derive_result(
                preflight_failed=True,
                update_exit_code=None,
                pending=[],
                verify=None,
                pacnew_count=0,
                was_dry_run=(mode is Mode.DRY_RUN),
            )
            return result

    # ── Snapshot ────────────────────────────────────────────────────────────
    snapshot = snapshot_phase.take_snapshot(cfg, strategy, bus)

    # ── Gates ───────────────────────────────────────────────────────────────
    gate_results = gates_phase.run_gates(cfg, snapshot, bus)
    if gates_phase.any_fail(gate_results):
        fail = next(g for g in gate_results if g.status is GateStatus.FAIL)
        if fail.can_override and mode is Mode.INTERACTIVE and prompter.confirm_gate_override(fail):
            pass  # user accepted the override
        else:
            result.aborted_reason = f"gate {fail.name} failed: {fail.message}"
            result.summary = derive_result(
                preflight_failed=True,
                update_exit_code=None,
                pending=[],
                verify=None,
                pacnew_count=0,
            )
            return result

    # ── Risk + transaction preview ──────────────────────────────────────────
    pending = risk_phase.classify_pending(cfg, bus)
    result.pending = pending

    if not pending:
        bus.emit_log("risk", "No official-repo updates pending.")
        if mode is Mode.DRY_RUN:
            result.summary = derive_result(
                preflight_failed=False,
                update_exit_code=None,
                pending=[],
                verify=None,
                pacnew_count=0,
                was_dry_run=True,
            )
            return result
    else:
        # Print HIGH risk packages prominently.
        high = [p for p in pending if p.risk is RiskLevel.HIGH]
        medium = [p for p in pending if p.risk is RiskLevel.MEDIUM]
        low = [p for p in pending if p.risk is RiskLevel.LOW]
        bus.emit_log(
            "risk",
            f"Pending: {len(pending)} total — {len(high)} HIGH, {len(medium)} MEDIUM, {len(low)} LOW",
        )
        if high:
            bus.emit_log("risk", "HIGH RISK (may need reboot, config merge, or session restart):")
            for p in high:
                tag = " [kernel]" if p.is_kernel else ""
                bus.emit_log("risk", f"  {p.name:36s} {p.old_version} -> {p.new_version}{tag}")
        if medium:
            bus.emit_log("risk", "MEDIUM RISK (service packages — verify after update):")
            for p in medium:
                bus.emit_log("risk", f"  {p.name:36s} {p.old_version} -> {p.new_version}")

        # Transaction preview (audit C3).
        try:
            preview = risk_phase.preview_transaction(bus)
            if preview.replacements:
                bus.emit_log(
                    "risk",
                    f"NOTE: pacman would perform {len(preview.replacements)} package replacement(s); "
                    "see lines above. --noconfirm defaults these to 'No' — manual run may be safer.",
                )
        except Exception as e:  # noqa: BLE001 — defensive; preview is informational
            log.warning("transaction preview failed: %s", e)

        if mode is Mode.DRY_RUN:
            # Surface AUR pending for visibility — does not run the AUR phase.
            _dry_run_show_aur_pending(cfg, bus, no_aur=no_aur)
            bus.emit_log("pipeline", "Dry-run complete; no update executed.")
            result.summary = derive_result(
                preflight_failed=False,
                update_exit_code=None,
                pending=pending,
                verify=None,
                pacnew_count=0,
                was_dry_run=True,
            )
            return result

        # HIGH-risk gating.
        if high:
            if mode is Mode.AUTO:
                bus.emit_log("pipeline", "HIGH RISK present and mode=auto — aborting.")
                result.aborted_reason = "HIGH RISK packages present in auto mode"
                result.summary = derive_result(
                    preflight_failed=True,
                    update_exit_code=None,
                    pending=pending,
                    verify=None,
                    pacnew_count=0,
                )
                return result
            proceed, ignored = prompter.decide_high_risk(list(high))
            if not proceed:
                result.aborted_reason = "user declined HIGH RISK update"
                result.summary = derive_result(
                    preflight_failed=True,
                    update_exit_code=None,
                    pending=pending,
                    verify=None,
                    pacnew_count=0,
                )
                return result
            if ignored:
                bus.emit_log(
                    "risk",
                    f"User deselected {len(ignored)} package(s): {', '.join(ignored)}",
                )
                result.deselected_packages = tuple(ignored)

    # ── Pre-update hooks ────────────────────────────────────────────────────
    pre_outcome = hooks.run_pre_update(None)
    result.pre_hook_results = tuple(pre_outcome.results)
    if not pre_outcome.proceed:
        result.aborted_reason = "pre_update hook failed (fail_pipeline_on_error=true)"
        result.summary = derive_result(
            preflight_failed=True,
            update_exit_code=None,
            pending=pending,
            verify=None,
            pacnew_count=0,
        )
        return result

    # ── Update official ─────────────────────────────────────────────────────
    if pending:
        update_code = update_official.run_official_update(
            cfg, strategy, bus,
            ignore=list(result.deselected_packages),
            cancel_event=cancel_event,
            prompt_provider=prompt_provider,
        )
        result.update_exit_code = update_code
        if update_code != 0:
            result.summary = derive_result(
                preflight_failed=False,
                update_exit_code=update_code,
                pending=pending,
                verify=None,
                pacnew_count=0,
            )
            return result

    # ── Update AUR ──────────────────────────────────────────────────────────
    result.aur = update_aur.run_aur_update(
        cfg, strategy, bus,
        cancel_event=cancel_event,
        force_skip=no_aur,
        prompt_provider=prompt_provider,
        pkgbuild_reviewer=pkgbuild_reviewer,
    )

    # ── Pacnew scan ─────────────────────────────────────────────────────────
    pacnew_files = pacnew_phase.scan_pacnew(cfg, snapshot.meta.path, bus)
    result.pacnew_count = len(pacnew_files)

    # ── Verify ──────────────────────────────────────────────────────────────
    if cfg.verify.enabled:
        verify = verify_phase.run_verify(cfg, snapshot, bus, config_path=config_path)
        result.verify = verify
    else:
        result.verify = None

    # ── Report ──────────────────────────────────────────────────────────────
    # Computed BEFORE post_verify hooks so the result tag is available to
    # user scripts as `$ARCHWARD_RESULT` (v0.4.1 F9). docs/hooks.md
    # documented this env var since v0.3.1 but it was never set.
    result.summary = derive_result(
        preflight_failed=False,
        update_exit_code=result.update_exit_code,
        pending=pending,
        verify=result.verify,
        pacnew_count=result.pacnew_count,
    )

    # ── Post-verify hooks ───────────────────────────────────────────────────
    # Run after both verify and the summary computation so hooks see the
    # full RESULT tag via `$ARCHWARD_RESULT`.
    if cfg.verify.enabled:
        post_outcome = hooks.run_post_verify(
            None, result.verify,
            result_tag=result.summary.tag if result.summary else None,
        )
        result.post_hook_results = tuple(post_outcome.results)

    # ── Snapshot retention ──────────────────────────────────────────────────
    # Honor cfg.general.keep_snapshots — wired in v0.4.0 (F6). Failures here
    # are non-fatal: the run is otherwise complete.
    try:
        removed = retention.prune_snapshots(cfg)
        if removed:
            bus.emit_log(
                "pipeline",
                f"Pruned {len(removed)} old snapshot(s); kept newest {cfg.general.keep_snapshots}.",
            )
    except Exception:  # noqa: BLE001
        log.exception("snapshot retention pass failed; non-fatal")

    return result
