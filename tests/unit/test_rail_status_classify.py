"""Rail status classification from PHASE_RESULT messages (v0.4.16 fix).

The old substring match ("fail" in msg) turned the verify rail red on every
run because "verify: 0 FAIL, 0 WARN" contains "fail".
"""

from archward.ui.main_window import _classify_result_message


class TestVerifyCounts:
    def test_zero_fail_zero_warn_is_pass(self):
        assert _classify_result_message("verify: 0 FAIL, 0 WARN") == "pass"

    def test_zero_fail_nonzero_warn_is_warn(self):
        assert _classify_result_message("verify: 0 FAIL, 2 WARN") == "warn"

    def test_nonzero_fail_is_fail(self):
        assert _classify_result_message("verify: 1 FAIL, 0 WARN") == "fail"

    def test_nonzero_fail_and_warn_is_fail(self):
        assert _classify_result_message("verify: 3 FAIL, 2 WARN") == "fail"


class TestPipelineMessages:
    def test_pacman_completed(self):
        assert _classify_result_message("pacman -Syu completed") == "pass"

    def test_pacman_failed(self):
        assert _classify_result_message("pacman -Syu FAILED (exit 1)") == "fail"

    def test_preflight_ok(self):
        assert _classify_result_message("pre-flight OK") == "pass"

    def test_preflight_failed(self):
        assert _classify_result_message("pre-flight FAILED") == "fail"

    def test_gates_passed(self):
        assert _classify_result_message("gates passed") == "pass"

    def test_gates_failed(self):
        assert _classify_result_message("gates failed") == "fail"

    def test_snapshot_complete(self):
        assert _classify_result_message("Snapshot complete: 2026-07-31T12-00-00") == "pass"

    def test_risk_summary(self):
        assert _classify_result_message("12 pending: 1 HIGH, 3 MEDIUM, 8 LOW") == "pass"

    def test_pacnew_none(self):
        assert _classify_result_message("No new .pacnew files") == "pass"


class TestAurMessages:
    def test_skipped(self):
        assert _classify_result_message("skipped") == "skipped"

    def test_skipped_no_helper(self):
        assert _classify_result_message("skipped (no helper)") == "skipped"

    def test_no_updates_pending(self):
        assert _classify_result_message("no AUR updates pending") == "pass"

    def test_completed(self):
        assert _classify_result_message("AUR updates completed") == "pass"

    def test_build_failures(self):
        assert _classify_result_message("completed with 2 build failure(s)") == "fail"

    def test_helper_failed_exit(self):
        # Old message "helper exited 1" matched no keyword -> green rail on a
        # failed helper; the message now says FAILED.
        assert _classify_result_message("helper FAILED (exit 1)") == "fail"

    def test_all_quarantined_skipped(self):
        assert (
            _classify_result_message("all 2 pending update(s) quarantined (skipped)")
            == "skipped"
        )

    def test_review_aborted(self):
        assert (
            _classify_result_message("AUR phase aborted (user cancelled PKGBUILD review)")
            == "fail"
        )


class TestHookMessages:
    def test_hooks_passed(self):
        assert _classify_result_message("2 hook(s) passed") == "pass"

    def test_hooks_warnings(self):
        assert _classify_result_message("2 hook(s); 1 warning(s)") == "warn"

    def test_hook_failed_abort(self):
        assert (
            _classify_result_message("hook FAILED, pipeline aborted (1 of 2 failing)")
            == "fail"
        )
