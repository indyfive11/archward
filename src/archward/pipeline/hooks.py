"""User hook runner.

Executes shell commands listed in `cfg.hooks.pre_update` and
`cfg.hooks.post_verify` at the appropriate pipeline checkpoints. Commands
run via `/bin/sh -c <cmd>` so shell features (pipes, env vars, redirection)
work without quoting gymnastics.

Default behavior:
  - Per-hook timeout (cfg.hooks.timeout_seconds; 60s default).
  - Non-zero exit logs a WARN line via the event bus but the pipeline
    continues. post_verify is always best-effort.
  - For pre_update only, `cfg.hooks.fail_pipeline_on_error = true` upgrades
    a failing hook from warning to fatal — the update aborts before pacman.

Environment passed to hooks: parent process env plus ARCHWARD_PHASE.

v0.3.1+ captures per-hook outcomes into HookResult records so the
SnapshotBrowser / Verify view can render them. Result list is returned
from each run method so the pipeline can plumb them into PipelineResult.
"""

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
from typing import Any, Literal

from archward.events import EventBus
from archward.models.config import HooksConfig
from archward.models.hook import HookResult, HookStatus

log = logging.getLogger(__name__)


@dataclass
class HookRunOutcome:
    """Return value from HookRunner.run_*. Carries both the abort signal
    (for fail_pipeline_on_error) and the per-hook results for the UI."""

    proceed: bool
    results: list[HookResult]


class HookRunner:
    def __init__(self, cfg: HooksConfig | None = None, bus: EventBus | None = None) -> None:
        self.cfg = cfg if cfg is not None else HooksConfig()
        self.bus = bus

    # ── Pipeline-facing API ────────────────────────────────────────────────

    def run_pre_update(self, ctx: Any = None) -> HookRunOutcome:
        if not self.cfg.pre_update:
            return HookRunOutcome(proceed=True, results=[])
        return self._run_set(
            self.cfg.pre_update,
            phase="pre_update",
            event_phase="hooks_pre",
            abort_on_failure=self.cfg.fail_pipeline_on_error,
        )

    def run_post_verify(self, ctx: Any = None, result: Any = None) -> HookRunOutcome:
        if not self.cfg.post_verify:
            return HookRunOutcome(proceed=True, results=[])
        return self._run_set(
            self.cfg.post_verify,
            phase="post_verify",
            event_phase="hooks_post",
            abort_on_failure=False,  # post-verify hooks never abort
        )

    # ── Internals ──────────────────────────────────────────────────────────

    def _run_set(
        self,
        commands: tuple[str, ...],
        *,
        phase: Literal["pre_update", "post_verify"],
        event_phase: str,
        abort_on_failure: bool,
    ) -> HookRunOutcome:
        self._emit_start(event_phase, f"Running {len(commands)} hook(s)")
        results: list[HookResult] = []
        proceed = True
        for i, cmd in enumerate(commands, start=1):
            result = self._run_one(cmd, phase, event_phase, i, len(commands))
            results.append(result)
            if result.status is not HookStatus.PASS and abort_on_failure:
                self._emit_log(event_phase, "hook failure aborts pipeline (fail_pipeline_on_error=true)")
                proceed = False
                break

        # Emit a PHASE_RESULT carrying the per-hook list so the GUI can render
        # them as a Verify view bucket, AND record a summary line to the
        # rotating Python log so post-mortems work without the GUI session.
        warn_count = sum(1 for r in results if r.status is not HookStatus.PASS)
        if warn_count == 0:
            msg = f"{len(results)} hook(s) passed"
        elif proceed:
            msg = f"{len(results)} hook(s); {warn_count} warning(s)"
        else:
            msg = f"hook FAILED, pipeline aborted ({warn_count} of {len(results)} failing)"
        log.info("[%s] %s", event_phase, msg)
        if self.bus is not None:
            payload = {"hook_results": [r.model_dump(mode="json") for r in results]}
            self.bus.emit_result(event_phase, msg, payload=payload)

        return HookRunOutcome(proceed=proceed, results=results)

    def _run_one(
        self,
        cmd: str,
        phase: Literal["pre_update", "post_verify"],
        event_phase: str,
        idx: int,
        total: int,
    ) -> HookResult:
        env = {**os.environ, "ARCHWARD_PHASE": event_phase}
        prefix = f"[{idx}/{total}]"
        self._emit_log(event_phase, f"{prefix} $ {cmd}")

        try:
            result = subprocess.run(
                ["/bin/sh", "-c", cmd],
                check=False,
                capture_output=True,
                text=True,
                timeout=self.cfg.timeout_seconds,
                env=env,
            )
        except subprocess.TimeoutExpired:
            self._emit_log(event_phase, f"{prefix} TIMEOUT after {self.cfg.timeout_seconds}s")
            return HookResult(
                command=cmd,
                phase=phase,
                status=HookStatus.TIMEOUT,
                exit_code=-1,
                output_lines=(),
            )
        except OSError as e:
            self._emit_log(event_phase, f"{prefix} OS error: {e}")
            return HookResult(
                command=cmd,
                phase=phase,
                status=HookStatus.FAIL,
                exit_code=-1,
                output_lines=(str(e),),
            )

        output_lines: list[str] = []
        if result.stdout:
            for line in result.stdout.splitlines():
                output_lines.append(line)
                self._emit_log(event_phase, f"  {line}")
        if result.stderr:
            for line in result.stderr.splitlines():
                output_lines.append(line)
                self._emit_log(event_phase, f"  {line}")

        if result.returncode == 0:
            self._emit_log(event_phase, f"{prefix} ok (exit 0)")
            status = HookStatus.PASS
        else:
            self._emit_log(event_phase, f"{prefix} FAILED (exit {result.returncode})")
            status = HookStatus.FAIL

        return HookResult(
            command=cmd,
            phase=phase,
            status=status,
            exit_code=result.returncode,
            output_lines=tuple(output_lines),
        )

    def _emit_start(self, event_phase: str, message: str) -> None:
        # Always log to the rotating file so post-mortems can see hook
        # execution without the GUI session; ALSO emit to the bus when
        # present so the live GUI log pane streams it.
        log.info("[%s] %s", event_phase, message)
        if self.bus is not None:
            self.bus.emit_start(event_phase, message)

    def _emit_log(self, event_phase: str, message: str) -> None:
        log.info("[%s] %s", event_phase, message)
        if self.bus is not None:
            self.bus.emit_log(event_phase, message)
