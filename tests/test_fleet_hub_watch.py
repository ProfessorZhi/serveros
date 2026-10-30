"""M2: the hub watcher finds running runs, classifies them with the Agent
Monitor's own rule, and records each outcome once.

Offline: fake devices behind the real FleetService and registry, a temporary
inbox and watch list, and a hand-driven clock.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for extra in (ROOT, ROOT / "integrations" / "fleet-next-agent-monitor",
              ROOT / "integrations" / "fleet-hub-watch"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from fleet_hub_watch.watch import BACKOFF_SECONDS, Watcher, WatchStore  # noqa: E402
from fleet_mcp.errors import FleetRefusal  # noqa: E402
from fleet_mcp.inbox import EventInbox  # noqa: E402
from test_fleet_mcp_surface import FakeProvider, Harness, TOKYO, WINDOWS, _summary  # noqa: E402


class RunDevice(FakeProvider):
    """A device whose sessions each carry a current run with a scripted status."""

    def __init__(self, device_ref):
        super().__init__(device_ref)
        self.runs = {}         # native session id -> run id
        self.answers = {}      # run id -> run_get payload, or a FleetRefusal
        self.run_reads = []

    def start(self, native, run_id, *, project=None, answer=None):
        self._sessions = [s for s in self._sessions if s.key.native_session_id != native]
        self._sessions.append(_summary(self.device_ref, native, running=True,
                                       projectId=project))
        self.runs[native] = run_id
        self.answers[run_id] = answer or {"status": "running", "runId": run_id}

    def finish(self, native):
        self._sessions = [s for s in self._sessions if s.key.native_session_id != native]
        self._sessions.append(_summary(self.device_ref, native, running=False))

    def get_session(self, key):
        detail = super().get_session(key)
        run_id = self.runs.get(key.native_session_id)
        if run_id is not None:
            detail["current_run"] = {"id": run_id}
        return detail

    def get_run(self, key, run_id, *, wait=False, wait_seconds=0.0):
        self.run_reads.append(run_id)
        answer = self.answers[run_id]
        if isinstance(answer, Exception):
            raise answer
        return dict(answer)


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class WatcherTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        tmp = Path(self._td.name)
        self.harness = Harness(tmp)
        self.tokyo = RunDevice(TOKYO)
        self.windows = RunDevice(WINDOWS)
        self.harness.service.providers[TOKYO] = self.tokyo
        self.harness.service.providers[WINDOWS] = self.windows
        self.inbox = EventInbox(tmp / "inbox.sqlite")
        self.store = WatchStore(tmp / "watch.sqlite")
        self.clock = Clock()
        self.watcher = Watcher(self.harness.service, self.harness.principal("fleet:read", name="hub-watch"),
                               self.inbox, self.store, clock=self.clock,
                               max_age=3600, stall_after=600)

    def tearDown(self):
        self._td.cleanup()

    def events(self, **kw):
        return self.inbox.read("test", limit=200, **kw)["events"]

    def settle(self, seconds=BACKOFF_SECONDS[-1]):
        self.clock.advance(seconds)
        return self.watcher.tick()


class Discovery(WatcherTest):
    def test_a_running_session_is_watched_with_its_current_run(self):
        self.tokyo.start("s-1", "run-1", project="serveros")
        result = self.watcher.discover()
        self.assertEqual(result["added"], [{"session_ref": f"{TOKYO}|claude|s-1", "run_id": "run-1"}])
        (watch,) = self.store.all()
        self.assertEqual((watch["device_ref"], watch["project"]), (TOKYO, "serveros"))

    def test_idle_sessions_are_not_watched(self):
        self.tokyo.finish("s-idle")
        self.assertEqual(self.watcher.discover()["added"], [])

    def test_runs_on_every_device_are_found(self):
        self.tokyo.start("s-1", "run-t")
        self.windows.start("s-2", "run-w")
        found = {w["run_id"] for w in self.watcher.discover()["added"]}
        self.assertEqual(found, {"run-t", "run-w"})

    def test_a_personal_device_is_never_asked(self):
        asked = []
        listing = self.harness.service.list_sessions

        def recording(principal, *, device=None, **kw):
            asked.append(device)
            return listing(principal, device=device, **kw)

        self.harness.service.list_sessions = recording
        self.watcher.discover()
        self.assertIn(TOKYO, asked)
        self.assertNotIn("device:personal-dell", asked)

    def test_discovering_the_same_run_again_adds_nothing(self):
        self.tokyo.start("s-1", "run-1")
        self.watcher.discover()
        self.assertEqual(self.watcher.discover()["added"], [])
        self.assertEqual(len(self.store.all()), 1)

    def test_one_unreachable_device_does_not_hide_the_others(self):
        self.windows._fail = FleetRefusal("PROVIDER_UNREACHABLE", "down")
        self.tokyo.start("s-1", "run-1")
        result = self.watcher.discover()
        self.assertEqual([a["run_id"] for a in result["added"]], ["run-1"])
        self.assertTrue(result["unreachable"])


class Classification(WatcherTest):
    def test_completed_with_output_is_recorded_once(self):
        self.tokyo.start("s-1", "run-1", project="serveros")
        self.watcher.tick()
        self.tokyo.answers["run-1"] = {"status": "completed", "runId": "run-1",
                                       "lastAssistantMessage": {"content": "done: 3 files changed"}}
        self.tokyo.finish("s-1")
        self.settle()
        self.settle()
        (event,) = self.events()
        self.assertEqual((event["kind"], event["level"]), ("run.completed", "notify"))
        self.assertEqual(event["project"], "serveros")
        self.assertNotIn("3 files changed", event["summary"], "no transcript text in the inbox")
        self.assertEqual(self.store.all(), [])

    def test_completed_without_output_is_unknown_not_success(self):
        self.tokyo.start("s-1", "run-1")
        self.watcher.tick()
        self.tokyo.answers["run-1"] = {"status": "completed", "runId": "run-1"}
        self.settle()
        (event,) = self.events()
        self.assertEqual((event["kind"], event["level"]), ("run.unknown", "decide"))
        self.assertIn("NO_NATIVE_ACTIVITY", event["summary"])

    def test_a_failed_run_is_recorded_as_failed(self):
        self.tokyo.start("s-1", "run-1")
        self.watcher.tick()
        self.tokyo.answers["run-1"] = {"status": "failed", "runId": "run-1"}
        self.settle()
        self.assertEqual([e["kind"] for e in self.events()], ["run.failed"])

    def test_a_vanished_run_is_unknown_not_failed(self):
        self.tokyo.start("s-1", "run-1")
        self.watcher.tick()
        self.tokyo.answers["run-1"] = FleetRefusal("RUN_NOT_FOUND", "gone")
        self.settle()
        self.assertEqual([e["kind"] for e in self.events()], ["run.unknown"])

    def test_an_offline_route_keeps_watching(self):
        self.tokyo.start("s-1", "run-1")
        self.watcher.tick()
        self.tokyo.answers["run-1"] = FleetRefusal("PROVIDER_UNREACHABLE", "down")
        self.settle(BACKOFF_SECONDS[0])
        self.assertEqual(self.events(), [])
        self.assertEqual(len(self.store.all()), 1)

    def test_a_native_state_field_is_read_as_status(self):
        self.tokyo.start("s-1", "run-1")
        self.watcher.tick()
        self.tokyo.answers["run-1"] = {"state": "completed", "runId": "run-1",
                                       "sessionId": "s-1",
                                       "lastAssistantMessage": {"content": "ok"}}
        self.settle()
        self.assertEqual([e["kind"] for e in self.events()], ["run.completed"])

    def test_an_answer_about_another_session_is_not_this_runs_result(self):
        self.tokyo.start("s-1", "run-1")
        self.watcher.tick()
        self.tokyo.answers["run-1"] = {"status": "completed", "runId": "run-1",
                                       "sessionId": "someone-else",
                                       "lastAssistantMessage": {"content": "ok"}}
        self.settle()
        (event,) = self.events()
        self.assertEqual(event["kind"], "run.unknown")
        self.assertIn("IDENTITY_MISMATCH", event["summary"])


class TimeAndRestart(WatcherTest):
    def test_backoff_spaces_the_reads(self):
        self.tokyo.start("s-1", "run-1")
        self.watcher.tick()                      # discovered and read once
        reads = len(self.tokyo.run_reads)
        self.clock.advance(BACKOFF_SECONDS[0] - 1)
        self.watcher.observe()
        self.assertEqual(len(self.tokyo.run_reads), reads, "not due yet")
        self.clock.advance(1)
        self.watcher.observe()
        self.assertEqual(len(self.tokyo.run_reads), reads + 1)

    def test_a_long_run_is_reported_stalled_once_and_still_watched(self):
        self.tokyo.start("s-1", "run-1")
        self.watcher.tick()
        for _ in range(4):
            self.settle(600)
        kinds = [e["kind"] for e in self.events()]
        self.assertEqual(kinds, ["run.stalled"])
        self.assertEqual(len(self.store.all()), 1)

    def test_past_the_window_a_running_run_becomes_unknown(self):
        self.tokyo.start("s-1", "run-1")
        self.watcher.tick()
        for _ in range(8):
            self.settle(600)
        self.assertEqual([e["kind"] for e in self.events()], ["run.stalled", "run.unknown"])
        self.assertEqual(self.store.all(), [])

    def test_an_ended_run_still_shown_as_current_is_not_watched_again(self):
        self.tokyo.start("s-1", "run-1")
        self.watcher.tick()
        self.tokyo.answers["run-1"] = {"status": "completed", "runId": "run-1",
                                       "lastAssistantMessage": {"content": "ok"}}
        self.settle()                      # recorded and dropped; session still "running"
        reads = len(self.tokyo.run_reads)
        for _ in range(3):
            self.settle()
        self.assertEqual(len(self.tokyo.run_reads), reads, "a finished run is not re-read")
        self.assertEqual(self.store.all(), [])
        self.tokyo.start("s-1", "run-2")   # the next run in the same session is new
        self.assertEqual([a["run_id"] for a in self.watcher.discover()["added"]], ["run-2"])

    def test_a_restart_keeps_the_watch_list_and_does_not_rerecord(self):
        self.tokyo.start("s-1", "run-1")
        self.watcher.tick()
        self.tokyo.answers["run-1"] = {"status": "completed", "runId": "run-1",
                                       "lastAssistantMessage": {"content": "ok"}}
        restarted = Watcher(self.harness.service, self.watcher.principal,
                            EventInbox(self.inbox.path), WatchStore(self.store.path),
                            clock=self.clock)
        self.clock.advance(BACKOFF_SECONDS[-1])
        restarted.tick()
        restarted.tick()
        self.assertEqual([e["kind"] for e in self.events()], ["run.completed"])

    def test_the_watcher_only_reads(self):
        self.tokyo.start("s-1", "run-1")
        self.watcher.tick()
        self.settle()
        self.assertEqual(self.tokyo.sent, [])
        self.assertEqual(self.watcher.principal.scopes, frozenset({"fleet:read"}))


if __name__ == "__main__":
    unittest.main()


class TheEntryPoint(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.dir = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def binding(self, *, inbox=True):
        from fleet_mcp.config import SCHEMA
        text = f"schema: {SCHEMA}\ndevice_ref: {TOKYO}\n"
        if inbox:
            text += f"inbox:\n  path: {self.dir / 'inbox.sqlite'}\n"
        path = self.dir / "binding.yaml"
        path.write_text(text, encoding="utf-8")
        return str(path)

    def test_no_inbox_in_the_binding_refuses(self):
        from fleet_hub_watch.__main__ import EXIT_NO_INBOX, main
        self.assertEqual(main(["--config", self.binding(inbox=False), "--once"]), EXIT_NO_INBOX)

    def test_a_second_watcher_on_the_same_inbox_refuses(self):
        import fcntl
        from fleet_hub_watch.__main__ import EXIT_ANOTHER_WATCHER, main
        config = self.binding()
        with open(self.dir / "watch.lock", "a") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(main(["--config", config, "--once"]), EXIT_ANOTHER_WATCHER)

    def test_one_tick_writes_a_heartbeat_and_the_shared_inbox(self):
        import json
        from fleet_hub_watch.__main__ import main
        self.assertEqual(main(["--config", self.binding(), "--once"]), 0)
        beat = json.loads((self.dir / "watch.heartbeat.json").read_text())
        self.assertIn("last_tick_at", beat)
        self.assertTrue((self.dir / "inbox.sqlite").is_file())
        self.assertTrue((self.dir / "watch.sqlite").is_file())
