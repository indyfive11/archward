"""HookRunner — subprocess execution, timeout, abort-on-failure.

The v0.3.1 hook-visibility work changed HookRunner.run_pre_update /
run_post_verify return type from `bool` to `HookRunOutcome(proceed, results)`.
Tests assert against `.proceed` for the abort signal and against
`.results` for the captured per-hook outcomes.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

from archward.models.config import HooksConfig
from archward.models.hook import HookStatus
from archward.pipeline.hooks import HookRunner


def test_empty_pre_update_returns_proceed_true() -> None:
    runner = HookRunner(HooksConfig())
    outcome = runner.run_pre_update(None)
    assert outcome.proceed is True
    assert outcome.results == []


def test_empty_post_verify_returns_proceed_true() -> None:
    runner = HookRunner(HooksConfig())
    outcome = runner.run_post_verify(None, None)
    assert outcome.proceed is True
    assert outcome.results == []


def test_pre_update_success() -> None:
    cfg = HooksConfig(pre_update=("true",))
    outcome = HookRunner(cfg).run_pre_update(None)
    assert outcome.proceed is True
    assert len(outcome.results) == 1
    assert outcome.results[0].status is HookStatus.PASS
    assert outcome.results[0].exit_code == 0


def test_pre_update_failure_no_abort() -> None:
    """Default behavior: hook fails, pipeline continues."""
    cfg = HooksConfig(pre_update=("false",), fail_pipeline_on_error=False)
    outcome = HookRunner(cfg).run_pre_update(None)
    assert outcome.proceed is True  # pipeline still continues
    assert outcome.results[0].status is HookStatus.FAIL
    assert outcome.results[0].exit_code == 1


def test_pre_update_failure_with_abort() -> None:
    """fail_pipeline_on_error=true: hook fails → pipeline aborts."""
    cfg = HooksConfig(pre_update=("false",), fail_pipeline_on_error=True)
    outcome = HookRunner(cfg).run_pre_update(None)
    assert outcome.proceed is False
    assert outcome.results[0].status is HookStatus.FAIL


def test_pre_update_stops_at_first_failure_when_aborting() -> None:
    """If first hook fails and abort_on_failure=True, second hook should NOT run."""
    cfg = HooksConfig(
        pre_update=("false", "touch /tmp/should-not-exist-archward-test-marker"),
        fail_pipeline_on_error=True,
    )
    bus = MagicMock()
    outcome = HookRunner(cfg, bus=bus).run_pre_update(None)
    assert outcome.proceed is False
    # Only one HookResult — the second command was never run.
    assert len(outcome.results) == 1
    # Sanity check via the bus log too.
    second_cmd_logged = any(
        "should-not-exist-archward-test-marker" in str(call.args)
        for call in bus.emit_log.call_args_list
    )
    assert not second_cmd_logged, "second hook ran despite first failure + abort flag"


def test_pre_update_continues_past_failure_when_not_aborting() -> None:
    """fail_pipeline_on_error=false: even if one hook fails, all run."""
    cfg = HooksConfig(
        pre_update=("false", "true"),
        fail_pipeline_on_error=False,
    )
    outcome = HookRunner(cfg).run_pre_update(None)
    assert outcome.proceed is True
    assert len(outcome.results) == 2
    assert outcome.results[0].status is HookStatus.FAIL
    assert outcome.results[1].status is HookStatus.PASS


def test_post_verify_never_aborts_even_on_failure() -> None:
    """Post-verify hooks are always best-effort."""
    cfg = HooksConfig(post_verify=("false",), fail_pipeline_on_error=True)
    outcome = HookRunner(cfg).run_post_verify(None, None)
    # post_verify ignores fail_pipeline_on_error — always proceeds.
    assert outcome.proceed is True
    assert outcome.results[0].status is HookStatus.FAIL


def test_timeout_kills_hung_hook() -> None:
    """A sleep beyond timeout should fail (and shouldn't hang the test)."""
    cfg = HooksConfig(pre_update=("sleep 30",), timeout_seconds=1, fail_pipeline_on_error=True)
    runner = HookRunner(cfg)
    start = time.monotonic()
    outcome = runner.run_pre_update(None)
    elapsed = time.monotonic() - start
    assert outcome.proceed is False
    assert outcome.results[0].status is HookStatus.TIMEOUT
    assert outcome.results[0].exit_code == -1
    assert elapsed < 5, f"timeout didn't fire (took {elapsed:.1f}s)"


def test_shell_features_work() -> None:
    """Verify /bin/sh -c lets us use pipes, env, redirection."""
    cfg = HooksConfig(pre_update=("echo $ARCHWARD_PHASE | grep -q hooks_pre",))
    outcome = HookRunner(cfg).run_pre_update(None)
    assert outcome.proceed is True
    assert outcome.results[0].status is HookStatus.PASS
    assert outcome.results[0].exit_code == 0


def test_hook_result_captures_output() -> None:
    """Stdout from a hook is captured into output_lines for the verify view."""
    cfg = HooksConfig(pre_update=("echo first; echo second",))
    outcome = HookRunner(cfg).run_pre_update(None)
    assert outcome.results[0].output_lines == ("first", "second")


def test_hook_phase_tag_set_correctly() -> None:
    """HookResult.phase distinguishes pre vs post so verify view can label them."""
    cfg = HooksConfig(pre_update=("true",), post_verify=("true",))
    runner = HookRunner(cfg)
    pre = runner.run_pre_update(None)
    post = runner.run_post_verify(None, None)
    assert pre.results[0].phase == "pre_update"
    assert post.results[0].phase == "post_verify"
