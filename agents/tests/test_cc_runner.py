"""No external models or facility operations: all execution boundaries are stubbed."""
import contextlib
import datetime
import importlib.machinery
import io
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

ROOT = pathlib.Path(__file__).resolve().parents[1]
r = importlib.machinery.SourceFileLoader("kt66_runner", str(ROOT / "cc-runner")).load_module()
ALARM = {"id": "TEST", "level": 7, "scope": "test", "msg": "test", "metric": "x", "value": 1}
W = {"id": "test-worker", "name": "test", "runtime": "claude", "autonomy": "L2",
     "model": "cc-haiku", "loops": ["test-loop"]}
APPROVER = {"id": "ops-lead", "name": "lead", "runtime": "claude",
            "autonomy": "approver", "model": "cc-haiku"}
LOOP = {"id": "test-loop", "owner": W["id"], "cadence": "*/5 * * * *",
        "triggers": {"alarms": ["TEST"]}}


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        for key, value in {"TICKETS": self.root, "STATE": self.root / ".state.json",
                           "GRAPH": self.root / "experience.json"}.items():
            self.stack.enter_context(patch.object(r, key, value))
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.stack.enter_context(patch.object(r.agentctl, "load_roster", return_value=[dict(W), dict(APPROVER)]))
        self.stack.enter_context(patch.object(r, "load_loops", return_value={"test-loop": dict(LOOP)}))
        self.stack.enter_context(patch.object(r.instructor_events, "active", return_value=[]))
        self.stack.enter_context(patch.object(r.instructor_events, "siem_records", return_value=[]))
        self.alarms = [dict(ALARM)]
        self.stack.enter_context(patch.object(r, "envsim", side_effect=lambda path:
            {"active": self.alarms} if path == "/alarms" else
            {"events": []} if path.startswith("/events") else {}))
        self.stack.enter_context(patch.object(r.ccs, "model_alias", return_value=("haiku", "")))

    def digest(self):
        return {str(p.relative_to(self.root)): p.read_bytes()
                for p in self.root.rglob("*") if p.is_file()}

    def test_dry_new_incident_does_not_write_or_call_model(self):
        before = self.digest()
        with patch.object(r.subprocess, "run") as process, patch.object(r.ccs, "render_seat") as render:
            r.sweep(True)
        self.assertEqual(before, self.digest())
        process.assert_not_called()
        render.assert_not_called()

    def test_dry_existing_incident_with_cleared_alarm_preserves_files(self):
        ticket = self.root / "existing.md"
        ticket.write_text("# existing\n- 상태: open\n")
        r.save_state({"incident": {"ticket": str(ticket), "engaged": [], "verdicts": 0}})
        self.alarms = []
        before = self.digest()
        r.sweep(True)
        self.assertEqual(before, self.digest())

    def test_dry_existing_active_incident_preserves_files(self):
        ticket = self.root / "existing.md"
        ticket.write_text("# existing\n- 상태: open\n")
        r.save_state({"incident": {"ticket": str(ticket), "engaged": [], "verdicts": 0}})
        before = self.digest()
        r.sweep(True)
        self.assertEqual(before, self.digest())

    def test_single_l2_worker_reaches_approval(self):
        with patch.object(r, "run_session", return_value=("haiku", "opinion")) as call:
            r.sweep()
        self.assertEqual([x.args[0]["id"] for x in call.call_args_list],
                         ["test-worker", "ops-lead"])
        self.assertEqual(r.load_state()["incident"]["verdicts"], 1)

    def test_failed_worker_is_not_recorded_as_engaged(self):
        with patch.object(r, "run_session", return_value=("haiku", None)):
            r.sweep()
        inc = r.load_state()["incident"]
        self.assertEqual(inc["engaged"], [])
        self.assertEqual(inc["roles"], {})
        self.assertEqual(inc["verdicts"], 0)
        self.assertEqual(inc["attempts"], {"test-worker": 1})

    def test_failure_retry_is_bounded_and_budgeted(self):
        with patch.object(r, "run_session", return_value=("haiku", None)) as call:
            r.sweep()
            st = r.load_state()
            st["incident"]["last_attempt"] = {}
            r.save_state(st)
            r.sweep()
            st = r.load_state()
            st["incident"]["last_attempt"] = {}
            r.save_state(st)
            r.sweep()
        self.assertEqual(call.call_count, 2)

    def test_orphan_owner_does_not_swallow_alarm(self):
        matched, unmatched = r.fast_path([ALARM], {"x": {**LOOP, "owner": "absent"}}, [W])
        self.assertEqual(matched, {})
        self.assertEqual(unmatched, ["TEST"])








    def test_cron_weekly_daily_and_interval(self):
        monday = datetime.datetime(2026, 9, 14, 6, 0)
        self.assertTrue(r.cron_matches("0 6 * * 1", monday))
        self.assertTrue(r.cron_matches("*/5 * * * *", monday))
        self.assertFalse(r.cron_matches("0 9 * * *", monday))
        self.assertTrue(r.cron_matches("0 6 1 * 1", monday))  # day-of-month OR day-of-week
        self.assertTrue(r.cron_matches("0 6 * * 0,7", datetime.datetime(2026, 9, 13, 6, 0)))
        for expr in ("* *", "*/0 * * * *", "99 * * * *"):
            with self.assertRaises(ValueError):
                r.cron_matches(expr, monday)

    def test_all_roster_cadences_parse(self):
        import yaml
        for p in (ROOT / "loops").glob("*.yaml"):
            cadence = yaml.safe_load(p.read_text())["cadence"]
            r.cron_matches(cadence, datetime.datetime(2026, 9, 14, 6, 0))

    def test_schedule_once_per_slot_across_restarts(self):
        now = datetime.datetime(2026, 9, 14, 6, 0, tzinfo=ZoneInfo("Asia/Seoul"))
        with patch.object(r, "run_session", return_value=("haiku", "review")) as call:
            r.scheduled_sweep(now=now)
            r.scheduled_sweep(now=now)
        self.assertEqual(call.call_count, 1)
        self.assertEqual(r.load_state()["scheduled"]["test-loop"]["status"], "succeeded")

    def test_failed_schedule_does_not_claim_success_or_replay_slot(self):
        now = datetime.datetime(2026, 9, 14, 6, 0, tzinfo=ZoneInfo("Asia/Seoul"))
        with patch.object(r, "run_session", return_value=("haiku", None)) as call:
            r.scheduled_sweep(now=now)
            r.scheduled_sweep(now=now)
        self.assertEqual(call.call_count, 1)
        self.assertEqual(r.load_state()["scheduled"]["test-loop"]["status"], "failed")

    def test_unsupported_schedule_is_explicitly_blocked(self):
        now = datetime.datetime(2026, 9, 14, 6, 0, tzinfo=ZoneInfo("Asia/Seoul"))
        with patch.object(r.agentctl, "load_roster", return_value=[{**W, "runtime": "bastion"}]), \
             patch.object(r, "run_session") as call:
            r.scheduled_sweep(now=now)
        call.assert_not_called()
        self.assertEqual(r.load_state()["scheduled"]["test-loop"]["reason"], "runtime_not_supported")

    def test_scheduled_preview_does_not_write(self):
        before = self.digest()
        with patch.object(r.subprocess, "run") as call:
            r.scheduled_sweep(True, datetime.datetime(2026, 9, 14, 6, 0))
        call.assert_not_called()
        self.assertEqual(before, self.digest())

    def test_overlapping_sweep_does_not_execute(self):
        with (self.root / ".runner.lock").open("a") as lock:
            r.fcntl.flock(lock, r.fcntl.LOCK_EX | r.fcntl.LOCK_NB)
            with patch.object(r, "run_session") as call:
                r.sweep()
            call.assert_not_called()

    def test_unbound_loop_does_not_route_alarm(self):
        matched, unmatched = r.fast_path([ALARM], {"test-loop": LOOP}, [{**W, "loops": []}])
        self.assertFalse(matched)
        self.assertEqual(unmatched, ["TEST"])

    def test_scheduled_daily_budget_blocks_extra_calls(self):
        now = datetime.datetime(2026, 9, 14, 6, 0, tzinfo=ZoneInfo("Asia/Seoul"))
        with patch.dict(r.os.environ, {"KT66_SCHEDULED_DAILY_LIMIT": "1"}), \
             patch.object(r, "run_session", return_value=("haiku", "review")) as call:
            r.scheduled_sweep(now=now)
            r.scheduled_sweep(now=now + datetime.timedelta(minutes=5))
        self.assertEqual(call.call_count, 1)
        self.assertEqual(r.load_state()["scheduled"]["test-loop"]["reason"], "daily_budget_exhausted")





    def test_instructor_event_waits_for_actual_siem_evidence(self):
        injection={"handle":"abcd1234","id":"sec_alertstorm","domain":"security",
                   "target":"kt66-attacker","name":"test","started":1}
        with patch.object(r.instructor_events,"active",return_value=[injection]), \
             patch.object(r,"run_session") as call:
            r.sweep()
        call.assert_not_called()



if __name__ == "__main__":
    unittest.main()
