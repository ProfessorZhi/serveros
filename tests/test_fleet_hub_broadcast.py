"""The broadcast spec (what cards say and when) and the daily report."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for extra in (ROOT, ROOT / "integrations" / "fleet-next-agent-monitor",
              ROOT / "integrations" / "fleet-hub-watch"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from fleet_hub_watch import cards, spec as broadcast_spec  # noqa: E402
from fleet_hub_watch.__main__ import SpecWatcher  # noqa: E402
from fleet_hub_watch.levels import ALERT, WARN  # noqa: E402
from test_fleet_hub_notify import DELL, THURSDAY_10, NotifierTest, view  # noqa: E402

SPEC_FILE = ROOT / "integrations" / "fleet-hub-watch" / "card-spec.yaml"
THURSDAY_1830 = datetime(2026, 10, 29, 10, 30, tzinfo=timezone.utc)   # 18:30 Beijing
SATURDAY_1830 = datetime(2026, 10, 31, 10, 30, tzinfo=timezone.utc)


def title(card):
    return card["header"]["title"]["content"]


class TheSpec(unittest.TestCase):
    def test_the_committed_spec_is_the_default(self):
        self.assertEqual(broadcast_spec.load(SPEC_FILE), broadcast_spec.load(None))

    def test_an_override_changes_only_what_it_names(self):
        spec = broadcast_spec.build({"resource_card": {"title": "{icon} 资源 · {word}"}})
        self.assertEqual(spec["resource_card"]["attention_heading"], "需要关注")
        card = cards.resource_card([view(ALERT)], now=THURSDAY_10, spec=spec)
        self.assertTrue(title(card).endswith("资源 · 报警"))

    def test_bad_specs_are_refused_by_name(self):
        cases = [
            ({"nope": 1}, "unknown key nope"),
            ({"resource_card": {"titel": "x"}}, "unknown key resource_card.titel"),
            ({"schedule": {"daily_report_at": "25:00"}}, "HH:MM"),
            ({"schedule": {"window": "20:00-08:00"}}, "START before END"),
            ({"schedule": {"workdays": [0]}}, "ISO weekdays"),
            ({"daily_report": {"sections": ["runs", "gossip"]}}, "unknown ['gossip']"),
            ({"resource_card": {"title": "{icon} {nonexistent}"}}, "does not render"),
            ({"completion_card": {"max_rows": "ten"}}, "must be int"),
            ({"schema": "other/v9"}, "schema"),
        ]
        for override, words in cases:
            with self.subTest(override=override):
                with self.assertRaises(broadcast_spec.SpecError) as caught:
                    broadcast_spec.build(override)
                self.assertIn(words, str(caught.exception))

    def test_a_spec_that_makes_a_card_too_big_is_refused(self):
        with self.assertRaises(broadcast_spec.SpecError):
            broadcast_spec.build({"resource_card": {"footer": "长" * 40000}})

    def test_suggestions_can_be_reworded_one_at_a_time(self):
        spec = broadcast_spec.build({"alert_card": {"suggestions": {"fs": "先看日志目录"}}})
        self.assertEqual(spec["alert_card"]["suggestions"]["fs"], "先看日志目录")
        self.assertIn("memory", spec["alert_card"]["suggestions"])


class CheckSpecCommand(unittest.TestCase):
    def run_check(self, path):
        env = {**os.environ, "PYTHONPATH": os.pathsep.join(
            [str(ROOT), str(ROOT / "integrations/fleet-next-agent-monitor"),
             str(ROOT / "integrations/fleet-hub-watch")])}
        return subprocess.run([sys.executable, "-m", "fleet_hub_watch", "--check-spec", str(path)],
                              capture_output=True, text=True, env=env, timeout=60)

    def test_good_and_bad(self):
        self.assertEqual(self.run_check(SPEC_FILE).returncode, 0)
        with tempfile.TemporaryDirectory() as td:
            bad = Path(td) / "bad.yaml"
            bad.write_text("schedule:\n  daily_report_at: '7pm'\n", encoding="utf-8")
            result = self.run_check(bad)
            self.assertEqual(result.returncode, 2)
            self.assertIn("REFUSED", result.stderr)


class HotReload(unittest.TestCase):
    def test_a_bad_edit_keeps_the_last_good_spec(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "card-spec.yaml"
            path.write_text("daily_report:\n  title: '日报 {date}'\n", encoding="utf-8")
            watcher = SpecWatcher(path)
            self.assertEqual(watcher.spec["daily_report"]["title"], "日报 {date}")
            time.sleep(0.01)
            path.write_text("daily_report:\n  title: '{oops}'\n", encoding="utf-8")
            os.utime(path, (time.time() + 5, time.time() + 5))
            self.assertFalse(watcher.refresh())
            self.assertTrue(watcher.state.startswith("REFUSED"))
            self.assertEqual(watcher.spec["daily_report"]["title"], "日报 {date}")

    def test_no_file_means_the_default(self):
        watcher = SpecWatcher(Path("/nonexistent/card-spec.yaml"))
        self.assertEqual(watcher.state, "DEFAULT")
        self.assertEqual(watcher.spec, broadcast_spec.load(None))


class TheScheduleComesFromTheSpec(NotifierTest):
    def test_window_and_workdays(self):
        self.notifier.spec = broadcast_spec.build({"schedule": {"window": "09:00-10:00",
                                                                "workdays": [1, 2, 3, 4, 5, 6]}})
        self.assertTrue(self.notifier.in_window(THURSDAY_10))            # 10:00 Beijing: the edge is in
        self.assertFalse(self.notifier.in_window(THURSDAY_10.replace(minute=30)))
        self.assertTrue(self.notifier.in_window(THURSDAY_10.replace(minute=0, hour=1, second=0)))
        self.assertTrue(self.notifier.workday(SATURDAY_1830))

    def test_slot_length(self):
        self.notifier.spec = broadcast_spec.build({"schedule": {"slot_minutes": 15}})
        self.assertEqual(self.notifier.slot(THURSDAY_10.replace(minute=40)), "2026-10-29 10:30")


class TheDailyReport(NotifierTest):
    def test_not_before_the_report_time_and_not_at_the_weekend(self):
        self.assertEqual(self.notifier.daily(THURSDAY_10), "NOT_YET")
        self.assertEqual(self.notifier.daily(SATURDAY_1830), "NOT_A_WORKDAY")
        self.assertEqual(self.transport.ops, [])

    def test_once_a_day_with_the_days_counts(self):
        self.run_event("run.failed", run_id="run-1", at="2026-10-29T02:00:00Z")
        self.inbox.record({"kind": "run.completed", "level": "auto", "device_ref": DELL,
                           "session_ref": f"{DELL}|claude|s-2", "run_id": "run-2",
                           "project": "lab", "occurred_at": "2026-10-29T03:00:00Z",
                           "summary": "lab: run run-2 completed"})
        self.node(ALERT, "2026-10-29T04:00:00Z")
        self.node(ALERT, "2026-10-29T05:00:00Z", recovered=True)
        self.notifier.consume(THURSDAY_1830)
        day = self.notifier.day_summary("2026-10-29")
        self.assertEqual(day["runs"]["failed"], 1)
        self.assertEqual(day["runs"]["completed"], 1)
        self.assertEqual(len(day["problems"]), 1)
        self.assertTrue(day["resources"][0]["recovered"])

        sends_before = len([op for op in self.transport.ops if op[0] == "send"])
        self.assertEqual(self.notifier.daily(THURSDAY_1830), "SENT")
        self.assertEqual(self.notifier.daily(THURSDAY_1830.replace(minute=59)), "ALREADY_SENT")
        daily = [op for op in self.transport.ops if op[0] == "send"][sends_before:]
        self.assertEqual(len(daily), 1)
        body = str(daily[0][2])
        self.assertIn("失败 1", body)
        self.assertIn("已恢复", body)
        # The run failure is still open, so the report is not green.
        self.assertNotEqual(daily[0][2]["header"]["template"], "green")

    def test_disabled_and_reordered_sections(self):
        self.notifier.spec = broadcast_spec.build({"daily_report": {"enabled": False}})
        self.assertEqual(self.notifier.daily(THURSDAY_1830), "DISABLED")
        spec = broadcast_spec.build({"daily_report": {"sections": ["open_now", "runs"]}})
        card = cards.daily_card({"runs": {}, "open_now": []}, now=THURSDAY_1830, spec=spec)
        texts = [e.get("content", "") for e in card["body"]["elements"]]
        self.assertTrue(texts[0].startswith("**到现在仍未结束**"))
        self.assertTrue(texts[1].startswith("**今日任务**"))
        self.assertEqual(card["header"]["template"], "green")

    def test_a_recovered_red_is_a_line_not_a_red_report(self):
        card = cards.daily_card({"runs": {"completed": 2},
                                 "resources": [{"subject": "X", "text": "93%", "level": ALERT,
                                                "recovered": True}],
                                 "open_now": []}, now=THURSDAY_1830)
        self.assertEqual(card["header"]["template"], "green")
        warn = cards.daily_card({"open_now": [{"subject": "Y", "text": "t", "level": WARN}]},
                                now=THURSDAY_1830)
        self.assertEqual(warn["header"]["template"], "yellow")


if __name__ == "__main__":
    unittest.main()
