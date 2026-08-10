"""checkupdates error/timeout must not read as "no updates" (v0.4.16 fix).

checkupdates exit codes: 0 = updates available, 2 = none pending, 1 = error.
The old code returned [] for every non-zero case, so an error or timeout
flowed through risk classification as "0 pending" and a dry-run reported
RESULT:SUCCESS.
"""

from unittest.mock import MagicMock

from archward.events import EventBus, PhaseEventKind
from archward.pacman import query as pq
from archward.pipeline import risk as risk_phase
from archward.pipeline.report import derive_result


def _patch_run(monkeypatch, code: int, out: str = "", err: str = ""):
    monkeypatch.setattr(pq.shutil, "which", lambda _: "/usr/bin/checkupdates")
    monkeypatch.setattr(pq, "_run", lambda argv: (code, out, err))


class TestCheckupdatesResult:
    def test_exit_0_parses_pending(self, monkeypatch):
        _patch_run(monkeypatch, 0, out="vim 1-1 -> 2-1\nglibc 2.39-1 -> 2.40-1\n")
        r = pq.checkupdates()
        assert r.ok is True
        assert [p.name for p in r.pending] == ["vim", "glibc"]

    def test_exit_2_is_trustworthy_empty(self, monkeypatch):
        _patch_run(monkeypatch, 2)
        r = pq.checkupdates()
        assert r.ok is True
        assert r.pending == ()

    def test_exit_1_is_error_not_empty(self, monkeypatch):
        _patch_run(monkeypatch, 1)
        r = pq.checkupdates()
        assert r.ok is False
        assert "exited 1" in r.error

    def test_timeout_is_error(self, monkeypatch):
        # _run maps TimeoutExpired to (1, "", "timeout")
        _patch_run(monkeypatch, 1, err="timeout")
        r = pq.checkupdates()
        assert r.ok is False
        assert "timed out" in r.error

    def test_missing_binary_is_error(self, monkeypatch):
        monkeypatch.setattr(pq.shutil, "which", lambda _: None)
        r = pq.checkupdates()
        assert r.ok is False
        assert "not installed" in r.error


class TestClassifyPendingPropagates:
    def _cfg(self):
        cfg = MagicMock()
        cfg.risk.high_risk_packages = []
        cfg.risk.kernel_patterns = []
        cfg.risk.medium_patterns = []
        return cfg

    def test_check_failure_emits_warn_result(self, monkeypatch):
        monkeypatch.setattr(
            pq, "checkupdates", lambda: pq.CheckupdatesResult(ok=False, error="checkupdates exited 1")
        )
        bus = EventBus()
        events = []
        bus.subscribe(events.append)
        outcome = risk_phase.classify_pending(self._cfg(), bus)
        assert outcome.check_ok is False
        assert outcome.updates == []
        results = [e for e in events if e.kind is PhaseEventKind.PHASE_RESULT]
        assert len(results) == 1
        assert "WARN" in results[0].message
        assert "fail" not in results[0].message.lower()  # rail must show warn, not fail

    def test_check_ok_empty_is_normal(self, monkeypatch):
        monkeypatch.setattr(pq, "checkupdates", lambda: pq.CheckupdatesResult())
        bus = EventBus()
        events = []
        bus.subscribe(events.append)
        outcome = risk_phase.classify_pending(self._cfg(), bus)
        assert outcome.check_ok is True
        assert outcome.updates == []
        results = [e for e in events if e.kind is PhaseEventKind.PHASE_RESULT]
        assert "0 pending" in results[0].message


class TestDryRunResultTag:
    def test_dry_run_check_failed_is_needs_review(self):
        summary = derive_result(
            preflight_failed=False,
            update_exit_code=None,
            pending=[],
            verify=None,
            pacnew_count=0,
            was_dry_run=True,
            pending_check_ok=False,
        )
        assert summary.tag == "RESULT:NEEDS_REVIEW"

    def test_dry_run_check_ok_empty_is_success(self):
        summary = derive_result(
            preflight_failed=False,
            update_exit_code=None,
            pending=[],
            verify=None,
            pacnew_count=0,
            was_dry_run=True,
            pending_check_ok=True,
        )
        assert summary.tag == "RESULT:SUCCESS"


class TestCLocaleEnv:
    def test_lc_all_overridden(self, monkeypatch):
        # LC_ALL outranks LANG — the original bug: only LANG was pinned.
        from archward.locale_env import c_locale_env

        env = c_locale_env({"LC_ALL": "de_DE.UTF-8", "LANGUAGE": "de", "HOME": "/x"})
        assert env["LANG"] == "C"
        assert env["LC_ALL"] == "C"
        assert env["LANGUAGE"] == ""
        assert env["HOME"] == "/x"
