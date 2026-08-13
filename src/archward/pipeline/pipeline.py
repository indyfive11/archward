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

from archward.events import EventBus, PhaseStatus
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
    snapshot_id: str | None = None
    # Last error lines from a failed `pacman -Syu`, for the result banner /
    # notification / CLI report (v0.4.18 — previously buried in the raw log).
    update_error_lines: tuple[str, ...] = ()
    # True when the pipeline rewrote the active config file (one-shot
    # aur.skip reset) so the GUI can reload its in-memory ConfigModel.
    config_rewritten: bool = False


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


def _reset_aur_skip(
    cfg: ConfigModel, config_path: Path | None, bus: EventBus
) -> tuple[ConfigModel, bool]:
    """Persist aur.skip=False after the one-shot skip was consumed.

    Returns (updated cfg, wrote_config). The in-memory cfg is consumed
    either way — a run must never re-apply a skip it already honored —
    but when the surgical write is refused (read-only newer config,
    unparseable file) the on-disk flag stays set, and we say so.
    `config_path=None` targets the default config.toml.
    """
    from archward.config.loader import merge_partial, update_config_sections

    new_aur = cfg.aur.model_copy(update={"skip": False})
    new_cfg = merge_partial(cfg, aur=new_aur)
    if not update_config_sections(config_path, aur=new_aur):
        bus.emit_log(
            "update_aur",
            "WARN: could not reset one-shot aur.skip in the config file (see "
            "log) — it stays set on disk and will skip AUR again next run.",
        )
        return new_cfg, False
    bus.emit_log(
        "update_aur",
        "aur.skip (one-shot) consumed — reset to off for the next run.",
    )
    return new_cfg, True


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
    acquire_instance_lock: bool = True,
) -> PipelineResult:
    """Run the full pipeline. Never raises on update failure — see PipelineResult.

    `acquire_instance_lock` (v0.4.17): run_pipeline itself takes the
    single-instance lock so every front-end is covered — previously only the
    CLI locked and a GUI run could race a concurrent CLI update. The CLI
    passes False because it already holds the lock (its wrapper preserves
    the historical exit-code-3 contention behavior).

    Run history: every run through here — BOTH lock arms — writes a
    run-history record via RunRecorder, including crash paths (partial record
    with the phases captured so far). Lock-contention aborts are the one
    exception: nothing ran, the other instance owns the real run's record.
    """
    from archward.pipeline import run_record

    recorder = run_record.RunRecorder(bus)
    result: PipelineResult | None = None
    error: BaseException | None = None
    try:
        if not acquire_instance_lock:
            result = _run_pipeline_impl(
                cfg, strategy, bus, mode,
                auto_yes=auto_yes, no_aur=no_aur, cancel_event=cancel_event,
                prompter=prompter, config_path=config_path,
                prompt_provider=prompt_provider, pkgbuild_reviewer=pkgbuild_reviewer,
            )
            return result

        from archward.app import try_acquire_lock  # composition root; no import cycle

        with try_acquire_lock() as acquired:
            if not acquired:
                recorder.discard()
                result = PipelineResult()
                bus.emit_start("preflight", "Pre-flight")
                bus.emit_log(
                    "preflight",
                    "FAIL: another archward instance is running (instance lock held) "
                    "— refusing to start.",
                )
                bus.emit_result(
                    "preflight",
                    "FAIL: another archward instance is running",
                    PhaseStatus.FAIL,
                )
                result.preflight_failed = True
                result.aborted_reason = "another archward instance is running"
                result.summary = derive_result(
                    preflight_failed=True,
                    update_exit_code=None,
                    pending=[],
                    verify=None,
                    pacnew_count=0,
                    was_dry_run=(mode is Mode.DRY_RUN),
                    aborted=True,
                )
                return result
            result = _run_pipeline_impl(
                cfg, strategy, bus, mode,
                auto_yes=auto_yes, no_aur=no_aur, cancel_event=cancel_event,
                prompter=prompter, config_path=config_path,
                prompt_provider=prompt_provider, pkgbuild_reviewer=pkgbuild_reviewer,
            )
            return result
    except BaseException as exc:
        error = exc
        raise
    finally:
        recorder.finalize(
            cfg=cfg, mode=mode, no_aur=no_aur, result=result, error=error, bus=bus
        )


def _run_pipeline_impl(
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
    result = PipelineResult()
    hooks = HookRunner(cfg.hooks, bus)
    if prompter is None:
        prompter = _default_prompter(mode, auto_yes)

    def _cancelled() -> bool:
        return cancel_event is not None and cancel_event.is_set()

    def _cancel_abort() -> PipelineResult:
        """Short-circuit at a phase boundary after the user cancelled.

        The subprocess of the phase that was running was allowed to finish
        (never killed mid-transaction); everything after it is skipped.
        """
        bus.emit_log("pipeline", "Cancelled by user — skipping remaining phases.")
        result.aborted_reason = "cancelled by user"
        result.summary = derive_result(
            preflight_failed=False,
            update_exit_code=result.update_exit_code,
            pending=result.pending,
            verify=None,
            pacnew_count=result.pacnew_count,
            was_dry_run=(mode is Mode.DRY_RUN),
            aborted=True,
        )
        return result

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
            aborted=True,
        )
        return result

    # A pre-flight WARN (cache-safety, Arch News) never hard-aborts, but in
    # an interactive run we give the user an explicit chance to bail before
    # we touch the system. Every overridable WARN gets its own prompt
    # (v0.4.18 — previously only the first was surfaced, so an Arch News
    # WARN could be silently swallowed by a cache-safety one). In auto/
    # dry-run we don't prompt (AutoNoPrompter would spuriously abort a
    # WARN); they were already logged loudly.
    if mode is Mode.INTERACTIVE:
        for warn in preflight:
            if warn.status is not GateStatus.WARN or not warn.can_override:
                continue
            if not prompter.confirm_gate_override(warn):
                result.aborted_reason = f"{warn.name}: {warn.message}"
                result.summary = derive_result(
                    preflight_failed=True,
                    update_exit_code=None,
                    pending=[],
                    verify=None,
                    pacnew_count=0,
                    was_dry_run=(mode is Mode.DRY_RUN),
                    aborted=True,
                )
                return result

    # ── Snapshot ────────────────────────────────────────────────────────────
    snapshot = snapshot_phase.take_snapshot(cfg, strategy, bus)
    result.snapshot_id = snapshot.meta.snapshot_id
    if _cancelled():
        return _cancel_abort()

    # ── Gates ───────────────────────────────────────────────────────────────
    gate_results = gates_phase.run_gates(cfg, snapshot, bus)
    # Every FAIL gate is inspected (v0.4.18 — previously only the first, so
    # overriding a snapshot-age FAIL silently skipped a disk-space FAIL).
    # Each one must be individually overridable AND overridden to proceed.
    for fail in gate_results:
        if fail.status is not GateStatus.FAIL:
            continue
        if fail.can_override and mode is Mode.INTERACTIVE and prompter.confirm_gate_override(fail):
            continue  # user accepted the override
        result.aborted_reason = f"gate {fail.name} failed: {fail.message}"
        result.summary = derive_result(
            preflight_failed=True,
            update_exit_code=None,
            pending=[],
            verify=None,
            pacnew_count=0,
            aborted=True,
        )
        return result

    if _cancelled():
        return _cancel_abort()

    # ── Risk + transaction preview ──────────────────────────────────────────
    risk_outcome = risk_phase.classify_pending(cfg, bus)
    pending = risk_outcome.updates
    result.pending = pending

    if not pending:
        if risk_outcome.check_ok:
            bus.emit_log("risk", "No official-repo updates pending.")
        else:
            bus.emit_log(
                "risk",
                f"WARN: could not determine pending updates ({risk_outcome.check_error}).",
            )
        if mode is Mode.DRY_RUN:
            result.summary = derive_result(
                preflight_failed=False,
                update_exit_code=None,
                pending=[],
                verify=None,
                pacnew_count=0,
                was_dry_run=True,
                pending_check_ok=risk_outcome.check_ok,
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
                    aborted=True,
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
                    aborted=True,
                )
                return result
            if ignored:
                bus.emit_log(
                    "risk",
                    f"User deselected {len(ignored)} package(s): {', '.join(ignored)}",
                )
                result.deselected_packages = tuple(ignored)

    if _cancelled():
        return _cancel_abort()

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
            aborted=True,
        )
        return result

    # ── Update official ─────────────────────────────────────────────────────
    if pending:
        outcome = update_official.run_official_update(
            cfg, strategy, bus,
            ignore=list(result.deselected_packages),
            cancel_event=cancel_event,
            prompt_provider=prompt_provider,
        )
        result.update_exit_code = outcome.exit_code
        result.update_error_lines = outcome.error_lines
        if outcome.exit_code != 0:
            # Honesty on failure (v0.4.18): a failed -Syu may still have
            # installed packages and dropped .pacnew files before dying, so
            # the pacnew scan still runs. The error excerpt travels in the
            # result so the banner/notification/CLI can show WHY it failed
            # instead of burying pacman's message in the raw log.
            pacnew_files = pacnew_phase.scan_pacnew(cfg, snapshot.meta.path, bus)
            result.pacnew_count = len(pacnew_files)
            result.summary = derive_result(
                preflight_failed=False,
                update_exit_code=outcome.exit_code,
                pending=pending,
                verify=None,
                pacnew_count=result.pacnew_count,
            )
            return result

    # A cancel that arrived while pacman was running (the subprocess is
    # allowed to finish its transaction) must not launch the AUR helper,
    # pacnew scan, verify, or hooks — new sudo escalations included.
    if _cancelled():
        return _cancel_abort()

    # ── Update AUR ──────────────────────────────────────────────────────────
    aur_skip_was_set = cfg.aur.skip
    result.aur = update_aur.run_aur_update(
        cfg, strategy, bus,
        cancel_event=cancel_event,
        force_skip=no_aur,
        prompt_provider=prompt_provider,
        pkgbuild_reviewer=pkgbuild_reviewer,
    )
    if aur_skip_was_set:
        # aur.skip is documented as a one-shot override ("skip AUR just for
        # this run") but was never reset (v0.4.18) — it silently skipped AUR
        # forever. Consume it now that the skip has been applied.
        cfg, reset_ok = _reset_aur_skip(cfg, config_path, bus)
        result.config_rewritten = result.config_rewritten or reset_ok

    if _cancelled():
        return _cancel_abort()

    # ── Pacnew scan ─────────────────────────────────────────────────────────
    pacnew_files = pacnew_phase.scan_pacnew(cfg, snapshot.meta.path, bus)
    result.pacnew_count = len(pacnew_files)

    if _cancelled():
        return _cancel_abort()

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

    # ── After-snapshot ──────────────────────────────────────────────────────
    # Optional post-update snapshot taken after a clean verify pass.
    # Pairs with the pre-snapshot as a "known-good post-update baseline".
    if cfg.general.after_snapshot and result.verify is not None and result.verify.fail_count == 0:
        after_id = f"{snapshot.meta.snapshot_id}-after"
        try:
            snapshot_phase.take_snapshot(cfg, strategy, bus, snapshot_id=after_id)
        except Exception:  # noqa: BLE001
            log.exception("after-snapshot failed; non-fatal")

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
