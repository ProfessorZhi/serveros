"""The resident monitor's control surface: Fleet reads its state and queues commands."""
from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for extra in (ROOT, ROOT / "integrations" / "fleet-next-agent-monitor",
              ROOT / "integrations" / "fleet-hub-watch", Path(__file__).resolve().parent):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from fleet_hub_watch.__main__ import SpecWatcher, apply_commands  # noqa: E402
from fleet_mcp import tools as fleet_tools  # noqa: E402
from fleet_mcp.config import InboxBinding  # noqa: E402
from fleet_mcp.monitor_control import ControlError, MonitorControl, validate  # noqa: E402
from test_fleet_hub_notify import DELL, THURSDAY_10, NotifierTest  # noqa: E402
from test_fleet_mcp_surface import Harness  # noqa: E402

NOW = datetime(2026, 11, 3, 2, 0, tzinfo=timezone.utc)


class Validation(unittest.TestCase):
    def test_closed_verbs_and_bounded_arguments(self):
        for verb, args, words in [
            ("reboot", {}, "verb"),
            ("mute", {"minutes": 10, "reason": "x"}, "subject"),
            ("mute", {"subject": DELL, "minutes": 0, "reason": "x"}, "minutes"),
            ("mute", {"subject": DELL, "minutes": 2000, "reason": "x"}, "minutes"),
            ("mute", {"subject": DELL, "minutes": 10}, "reason"),
            ("pause_broadcast", {"minutes": 10, "reason": "x", "url": "http://x"}, "unexpected"),
        ]:
            with self.subTest(verb=verb, args=args):
                with self.assertRaises(ControlError) as caught:
                    validate(verb, args)
                self.assertIn(words, str(caught.exception))

    def test_good_commands(self):
        self.assertEqual(validate("send_daily_now", {}), {})
        self.assertEqual(validate("mute", {"subject": DELL, "minutes": 30, "reason": "maintenance"}),
                         {"target": DELL, "minutes": 30, "reason": "maintenance"})


class Store(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.dir = Path(self._td.name)
        self.control = MonitorControl(self.dir)

    def tearDown(self):
        self._td.cleanup()

    def test_the_store_is_owner_only(self):
        self.assertEqual(oct((self.dir / "monitor-control.sqlite").stat().st_mode & 0o777), "0o600")

    def test_mute_applies_then_expires(self):
        command = self.control.queue("mute", {"subject": DELL, "minutes": 30, "reason": "maint"},
                                     by="owner", now=NOW)
        self.assertFalse(self.control.muted([DELL], NOW))
        self.control.apply(command, now=NOW)
        self.assertTrue(self.control.muted([DELL, "other"], NOW + timedelta(minutes=10)))
        self.assertFalse(self.control.muted([DELL], NOW + timedelta(minutes=31)))

    def test_pause_and_resume(self):
        self.control.apply(self.control.queue("pause_broadcast", {"minutes": 60, "reason": "demo"},
                                              by="owner", now=NOW), now=NOW)
        self.assertEqual(self.control.broadcast_state(NOW + timedelta(minutes=5))["state"], "PAUSED")
        self.control.apply(self.control.queue("resume_broadcast", {}, by="owner", now=NOW), now=NOW)
        self.assertEqual(self.control.broadcast_state(NOW)["state"], "ON")

    def test_status_reports_heartbeat_and_commands(self):
        (self.dir / "watch.heartbeat.json").write_text(json.dumps(
            {"last_tick_at": time.time(), "ok": True, "card_spec": "LOADED card-spec.yaml",
             "devices": {DELL: "WARN"}}))
        self.control.queue("send_daily_now", {}, by="owner")
        status = self.control.status()
        self.assertEqual(status["watcher"]["state"], "RUNNING")
        self.assertEqual(status["watcher"]["devices"], {DELL: "WARN"})
        self.assertEqual(status["recent_commands"][0]["state"], "QUEUED")

    def test_no_heartbeat_is_said(self):
        self.assertEqual(self.control.status()["watcher"]["state"], "NO_HEARTBEAT")


class TheWatcherAppliesCommands(NotifierTest):
    def setUp(self):
        super().setUp()
        self.control = MonitorControl(self.dir)
        self.notifier.control = self.control

    def test_a_muted_device_sends_nothing_but_is_still_counted(self):
        self.control.apply(self.control.queue("mute", {"subject": DELL, "minutes": 600,
                                                       "reason": "disk work"}, by="owner"))
        self.node("ALERT", "2026-10-29T01:00:00Z")
        self.notifier.consume(THURSDAY_10)
        self.assertEqual(self.transport.ops, [])
        self.assertEqual(len(self.notifier.day_summary("2026-10-29")["resources"]), 1)

    def test_a_pause_stops_messages(self):
        self.control.apply(self.control.queue("pause_broadcast", {"minutes": 600, "reason": "x"},
                                              by="owner"))
        self.node("ALERT", "2026-10-29T01:00:00Z")
        self.notifier.consume(THURSDAY_10)
        self.assertEqual(self.transport.ops, [])

    def test_send_daily_now_and_reload_through_apply_commands(self):
        self.control.queue("send_daily_now", {}, by="owner")
        self.control.queue("reload_spec", {}, by="owner")
        applied = apply_commands(self.control, self.notifier, SpecWatcher(None))
        self.assertEqual(applied, 2)
        results = {c["verb"]: c["result"] for c in self.control.status()["recent_commands"]}
        self.assertEqual(results["send_daily_now"], {"daily": "SENT"})
        self.assertEqual(results["reload_spec"], {"card_spec": "DEFAULT"})
        self.assertEqual(apply_commands(self.control, self.notifier, SpecWatcher(None)), 0)


class TheFleetTools(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.harness = Harness(self.tmp)
        self.service = self.harness.service

    def tearDown(self):
        self._td.cleanup()

    def call(self, name, *scopes, **args):
        ctx = fleet_tools.ToolContext(service=self.service,
                                      principal=self.harness.principal(*scopes))
        return fleet_tools.call_tool(ctx, name, args)

    def test_annotations(self):
        by = fleet_tools.SERVED_BY_NAME
        self.assertTrue(by["fleet_monitor_status"].annotations["readOnlyHint"])
        control = by["fleet_monitor_control"].annotations
        self.assertFalse(control["readOnlyHint"])
        self.assertFalse(control["destructiveHint"])

    def test_no_inbox_is_not_configured(self):
        answer = self.call("fleet_monitor_status", "fleet:read")["structuredContent"]
        self.assertEqual(answer["monitor"], "NOT_CONFIGURED")

    def test_control_needs_fleet_control_and_queues(self):
        object.__setattr__(self.service.config, "inbox", InboxBinding(path=self.tmp / "inbox.sqlite"))
        denied = self.call("fleet_monitor_control", "fleet:read", "fleet:send", verb="send_daily_now")
        self.assertTrue(denied["isError"])
        self.assertEqual(denied["structuredContent"]["code"], "SCOPE_DENIED")
        queued = self.call("fleet_monitor_control", "fleet:control", verb="mute", subject=DELL,
                           minutes=30, reason="maintenance")["structuredContent"]
        self.assertEqual(queued["command"]["verb"], "mute")
        status = self.call("fleet_monitor_status", "fleet:read")["structuredContent"]
        self.assertEqual(status["recent_commands"][0]["state"], "QUEUED")
        bad = self.call("fleet_monitor_control", "fleet:control", verb="mute", subject=DELL,
                        minutes=99999, reason="x")
        self.assertTrue(bad["isError"])


if __name__ == "__main__":
    unittest.main()
