"""D-4 event push: serveros-eventd → spool → connector /v1/events → hub pump.

The point being protected: a finished run reaches the hub in seconds, not on
the next 30-second poll, and nothing is lost when a piece is missing (the
tick remains the safety net, and the heartbeat says which device is slow).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for extra in (ROOT, ROOT / "integrations" / "fleet-hub-watch",
              ROOT / "integrations" / "fleet-next-agent-monitor"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from fleet_mcp.errors import FleetRefusal  # noqa: E402
from fleet_mcp.node import events  # noqa: E402
from fleet_mcp.node_protocol import ROUTES  # noqa: E402
from fleet_hub_watch.events import EventPump  # noqa: E402
from fleet_hub_watch.watch import WatchStore  # noqa: E402

EVENTD = ROOT / "native" / "rust" / "eventd"


def iso(offset: float = 0.0) -> str:
    return datetime.fromtimestamp(time.time() + offset, timezone.utc).isoformat()


def write_spool(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def write_status(spool: Path, *, ok: bool = True, age: float = 0.0) -> None:
    spool.with_name(spool.name + ".status.json").write_text(json.dumps({
        "schema": "serveros/eventd-status.v1", "version": "0.1.0", "ok": ok, "error": None,
        "last_poll_at": iso(-age), "seq": 2, "events": 2}), encoding="utf-8")


class TheSpool(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.spool = Path(self.tmp.name) / "events.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def test_reads_after_a_cursor_and_drops_unknown_shapes(self):
        write_spool(self.spool, [
            {"seq": 1, "kind": "run.started", "session": "s", "run": "r", "at": "t"},
            {"seq": 2, "kind": "run.ended", "session": "s", "run": "r", "status": "completed",
             "at": "t", "text": "never carried"},
            {"seq": 3, "kind": "something.else"},
        ])
        got = events.read_since(self.spool, 1)
        self.assertEqual([e["seq"] for e in got], [2])
        self.assertNotIn("text", got[0])

    def test_long_poll_returns_at_once_when_there_is_news_and_waits_when_not(self):
        write_spool(self.spool, [{"seq": 1, "kind": "run.ended", "session": "s", "run": "r"}])
        write_status(self.spool)
        started = time.monotonic()
        answer = events.wait_since(self.spool, 0, 5)
        self.assertLess(time.monotonic() - started, 1)
        self.assertEqual(answer["next"], 1)
        self.assertEqual(answer["eventd"]["state"], "OK")
        slept = []
        clock = iter([0.0, 0.0, 0.3, 0.6, 9.0])
        answer = events.wait_since(self.spool, 1, 0.5, sleep=slept.append, clock=lambda: next(clock))
        self.assertEqual(answer["events"], [])
        self.assertTrue(slept)

    def test_a_reset_spool_rewinds_the_caller(self):
        write_spool(self.spool, [{"seq": 1, "kind": "run.ended", "session": "s", "run": "r"}])
        answer = events.wait_since(self.spool, 50, 0)
        self.assertTrue(answer["reset"])
        self.assertEqual(answer["next"], 0)

    def test_pusher_liveness(self):
        self.assertEqual(events.eventd_status(self.spool)["state"], "NOT_RUNNING")
        write_status(self.spool, age=300)
        self.assertEqual(events.eventd_status(self.spool)["state"], "STALE")
        write_status(self.spool, ok=False)
        self.assertEqual(events.eventd_status(self.spool)["state"], "DEGRADED")


class TheConnectorRoute(unittest.TestCase):
    def test_route_is_declared_and_takes_two_numbers_only(self):
        from fleet_mcp.node.connector import Connector

        self.assertIn("GET /v1/events", ROUTES)
        with tempfile.TemporaryDirectory() as tmp:
            spool = Path(tmp) / "events.jsonl"
            write_spool(spool, [{"seq": 1, "kind": "run.ended", "session": "s", "run": "r"}])
            connector = Connector.__new__(Connector)
            connector.config = type("C", (), {"device_ref": "device:x", "runtime": "claude",
                                              "events_spool_path": spool})()
            status, body = connector._dispatch("GET", "/v1/events?after=0&wait=0", b"", "device:x")
            self.assertEqual(status, 200)
            self.assertEqual(body["events"][0]["run"], "r")
            for bad in ("/v1/events?after=x", "/v1/events?path=/etc", "/v1/events?after=-1"):
                with self.subTest(bad=bad), self.assertRaises(FleetRefusal):
                    connector._dispatch("GET", bad, b"", "device:x")


class FakeService:
    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    def device_events(self, principal, *, device, after, wait):
        self.calls.append((device, after))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


class FakeWatcher:
    def __init__(self, store):
        self.store = store
        self.adopted = self.discovered = self.observed = 0
        self.traces = []

    def adopt_dispatched(self, now=None):
        self.adopted += 1
        return []

    def discover(self, devices=None):
        self.discovered += 1
        self.devices = devices

    def observe(self, only=None):
        self.observed += 1
        self.only = only
        return {"recorded": []}

    def _trace(self, step, **fields):
        self.traces.append((step, fields))


class TheHubPump(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.store = WatchStore(self.dir / "watch.sqlite3")

    def tearDown(self):
        self.tmp.cleanup()

    def pump(self, answers):
        return EventPump(FakeService(answers), object(), lambda: ["device:a"],
                         self.dir / "events.cursor.json", wait=0)

    def test_a_finished_run_is_observed_at_once(self):
        self.store.add(session_ref="device:a|claude|s", run_id="r1", device_ref="device:a",
                       runtime="claude", project=None, now=time.time())
        self.store.reschedule(self.store.all()[0], time.time(), stall_reported=False)
        self.assertEqual(self.store.due(time.time()), [])
        pump = self.pump([{"events": [{"seq": 4, "kind": "run.ended", "session": "s", "run": "r1",
                                       "status": "completed", "at": iso()}],
                           "next": 4, "runtime": "claude", "eventd": {"state": "OK"}}])
        self.assertEqual(pump.poll_once("device:a"), 0.0)
        self.assertTrue(pump.wake.is_set())
        watcher = FakeWatcher(self.store)
        result = pump.handle(watcher)
        self.assertEqual(result["expedited"], 1)
        self.assertEqual(len(self.store.due(time.time())), 1)
        self.assertEqual(watcher.observed, 1)
        self.assertEqual(pump.cursor["device:a"], 4)
        self.assertEqual(json.loads((self.dir / "events.cursor.json").read_text()), {"device:a": 4})
        self.assertTrue(watcher.traces[0][0].startswith("pushed run.ended"))

    def test_an_unwatched_fresh_run_rediscovers_only_that_device(self):
        pump = self.pump([{"events": [{"seq": 1, "kind": "run.started", "session": "s", "run": "new",
                                       "at": iso()}], "next": 1, "runtime": "claude"}])
        pump.poll_once("device:a")
        watcher = FakeWatcher(self.store)
        result = pump.handle(watcher)
        self.assertEqual(result["rediscovered"], ["device:a"])
        self.assertEqual(watcher.devices, {"device:a"})

    def test_an_old_connector_is_asked_rarely_and_said_plainly(self):
        pump = self.pump([FleetRefusal("BAD_REQUEST", "UNKNOWN_ROUTE: GET /v1/events is not routed")])
        self.assertGreaterEqual(pump.poll_once("device:a"), 600)
        self.assertEqual(pump.health()["device:a"]["state"], "UNSUPPORTED")

    def test_failures_back_off(self):
        pump = self.pump([FleetRefusal("PROVIDER_UNREACHABLE", "x")] * 3)
        waits = [pump.poll_once("device:a") for _ in range(3)]
        self.assertEqual(waits, [5, 10, 30])



class ThePushedEndDecides(unittest.TestCase):
    """11-19: work-fj pushed run.ended (completed) at 15:12:25, the hub's own
    run read said UNKNOWN, and the batch closed at 15:17:57 — 5.5 minutes."""

    def test_the_fast_lane_runs_on_the_event_thread(self):
        calls = []
        with tempfile.TemporaryDirectory() as tmp:
            pump = EventPump(FakeService([{"events": [{"seq": 1, "kind": "run.ended", "run": "r",
                                                       "session": "s", "at": iso()}],
                                           "next": 1, "runtime": "claude"}]),
                             object(), lambda: ["device:a"], Path(tmp) / "c.json", wait=0,
                             on_events=lambda: calls.append(1))
            pump.poll_once("device:a")
        self.assertEqual(calls, [1])

    def test_a_watched_run_the_device_reported_completed_is_completed(self):
        from fleet_hub_watch import watch as watch_mod

        with tempfile.TemporaryDirectory() as tmp:
            store = WatchStore(Path(tmp) / "w.sqlite3")
            store.add(session_ref="device:a|claude|s", run_id="r1", device_ref="device:a",
                      runtime="claude", project=None, now=time.time())
            recorded = []
            watcher = watch_mod.Watcher.__new__(watch_mod.Watcher)
            watcher.store, watcher.clock = store, time.time
            watcher.max_age, watcher.stall_after = 3600, 7200
            watcher.pushed_end = {}
            watcher.inbox = type("I", (), {
                "record": lambda self, e: recorded.append(e) or type("R", (), {"seq": 1, "created": True})(),
                "trace": lambda self, *a, **k: None,
                "was_dispatched": lambda self, *a: False})()
            watcher._observe = lambda w: {"status": watch_mod.STATUS_UNKNOWN,
                                          "reason": "NO_NATIVE_ACTIVITY"}
            pump = EventPump(FakeService([{"events": [{"seq": 1, "kind": "run.ended", "run": "r1",
                                                       "session": "s", "status": "completed",
                                                       "at": iso()}],
                                           "next": 1, "runtime": "claude"}]),
                             object(), lambda: ["device:a"], Path(tmp) / "c.json", wait=0)
            pump.poll_once("device:a")
            watcher.adopt_dispatched = lambda now=None: []
            pump.handle(watcher)
            self.assertEqual(store.all(), [])
        self.assertEqual([e["kind"] for e in recorded], ["run.completed"])
        self.assertIn("device reported", recorded[0]["summary"])

class TheHubKnowsAtOnce(unittest.TestCase):
    """11-19: dispatch → "pushed run.started" took 7–19 s. The events were
    heard in time; they waited for the lane while three runs were read one
    after another. Now the hearing is traced on the polling thread, the lane
    reads only what was pushed, and devices are read side by side."""

    def _watcher(self, store, delay=0.0):
        from fleet_hub_watch import watch as watch_mod
        watcher = watch_mod.Watcher.__new__(watch_mod.Watcher)
        watcher.store, watcher.clock = store, time.time
        watcher.max_age, watcher.stall_after = 3600, 7200
        watcher.pushed_end = {}
        watcher.read = []
        watcher.inbox = type("I", (), {
            "record": lambda self, e: type("R", (), {"seq": 1, "created": True})(),
            "trace": lambda self, *a, **k: None,
            "was_dispatched": lambda self, *a: False})()

        def observe(w):
            time.sleep(delay)
            watcher.read.append(w["run_id"])
            return {"status": watch_mod.STATUS_UNKNOWN, "reason": "NO_NATIVE_ACTIVITY"}
        watcher._observe = observe
        watcher.adopt_dispatched = lambda now=None: []
        return watcher

    def test_heard_is_traced_on_the_polling_thread_with_its_lag(self):
        traces = []
        with tempfile.TemporaryDirectory() as tmp:
            pump = EventPump(FakeService([{"events": [{"seq": 1, "kind": "run.started", "run": "r",
                                                       "session": "s", "at": iso()}],
                                           "next": 1}]),
                             object(), lambda: ["device:a"], Path(tmp) / "c.json", wait=0,
                             trace=lambda step, **f: traces.append((step, f)))
            pump.poll_once("device:a")
        self.assertEqual(traces[0][0], "heard run.started")
        self.assertLess(traces[0][1]["lag_s"], 2)

    def test_the_lane_reads_only_the_pushed_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = WatchStore(Path(tmp) / "w.sqlite3")
            for run in ("r1", "r2"):
                store.add(session_ref=f"device:a|claude|{run}", run_id=run, device_ref="device:a",
                          runtime="claude", project=None, now=time.time())
            watcher = self._watcher(store)
            pump = EventPump(FakeService([{"events": [{"seq": 1, "kind": "run.started", "run": "r2",
                                                       "session": "s", "at": iso()}], "next": 1}]),
                             object(), lambda: ["device:a"], Path(tmp) / "c.json", wait=0)
            pump.poll_once("device:a")
            pump.handle(watcher)
        self.assertEqual(watcher.read, ["r2"])

    def test_devices_are_read_side_by_side(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = WatchStore(Path(tmp) / "w.sqlite3")
            for device in ("device:a", "device:b", "device:c"):
                store.add(session_ref=f"{device}|claude|s", run_id=device[-1], device_ref=device,
                          runtime="claude", project=None, now=time.time() - 1)
            watcher = self._watcher(store, delay=0.4)
            started = time.monotonic()
            watcher.observe()
            took = time.monotonic() - started
        self.assertEqual(sorted(watcher.read), ["a", "b", "c"])
        self.assertLess(took, 1.0)


class FakeGateway(BaseHTTPRequestHandler):
    """A CloudCLI MCP gateway that answers initialize and `changes`."""
    seen_auth: list = []
    pages: list = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeGateway.seen_auth.append(self.headers.get("Authorization"))
        if body.get("method") == "initialize":
            reply = {"jsonrpc": "2.0", "id": body["id"], "result": {"serverInfo": {"name": "fake"}}}
            self._send(reply, session="sess-1")
        elif body.get("method") == "notifications/initialized":
            self.send_response(202)
            self.end_headers()
        else:
            page = FakeGateway.pages.pop(0) if FakeGateway.pages else {"cursor": "c-end", "events": []}
            self._send({"jsonrpc": "2.0", "id": body["id"],
                        "result": {"structuredContent": page, "content": []}})

    def _send(self, payload, session=None):
        raw = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        if session:
            self.send_header("Mcp-Session-Id", session)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        pass


@unittest.skipUnless(shutil.which("cargo"), "needs cargo to build serveros-eventd")
class TheRustPusherEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        done = subprocess.run(["cargo", "build", "--release", "--quiet"], cwd=EVENTD,
                              capture_output=True, text=True, timeout=600)
        if done.returncode != 0:
            raise AssertionError(done.stderr[-2000:])
        cls.binary = EVENTD / "target" / "release" / ("serveros-eventd.exe" if os.name == "nt"
                                                       else "serveros-eventd")

    def test_once_spools_run_events_and_keeps_the_token_out_of_everything(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), FakeGateway)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        FakeGateway.seen_auth = []
        FakeGateway.pages = [{"cursor": "c-1", "events": [
            {"kind": "run", "at": {"iso": "2026-11-19T01:00:00Z"},
             "ref": {"runId": "r-9", "sessionId": "s-9"},
             "summary": "Run r-9 ended (completed) in session s-9."},
            {"kind": "quay_task", "at": {"iso": "x"}, "ref": {"taskId": "t"}, "summary": "ignored"}]}]
        try:
            with tempfile.TemporaryDirectory() as tmp:
                token = Path(tmp) / "pat"
                token.write_text("secret-token-value\n", encoding="utf-8")
                spool = Path(tmp) / "events.jsonl"
                cmd = [str(self.binary), "--endpoint", f"http://127.0.0.1:{server.server_port}/mcp",
                       "--credential-file", str(token), "--spool", str(spool), "--once"]
                first = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                self.assertEqual(first.returncode, 0, first.stderr)
                second = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                self.assertEqual(second.returncode, 0, second.stderr)
                lines = [json.loads(x) for x in spool.read_text().splitlines()]
                state = json.loads((Path(tmp) / "events.jsonl.state.json").read_text())
                status = events.eventd_status(spool)
                files = "".join(p.read_text() for p in Path(tmp).iterdir() if p.name != "pat")
            self.assertEqual([(e["kind"], e["run"], e["status"]) for e in lines],
                             [("run.ended", "r-9", "completed")])
            self.assertEqual(state["cursor"], "c-end")
            self.assertEqual(status["state"], "OK")
            self.assertIn("Bearer secret-token-value", FakeGateway.seen_auth)
            for text in (first.stdout, first.stderr, second.stdout, second.stderr, files):
                self.assertNotIn("secret-token-value", text)
        finally:
            server.shutdown()

    def test_refuses_anything_but_loopback(self):
        done = subprocess.run([str(self.binary), "--endpoint", "http://10.1.2.3:3001/mcp",
                               "--spool", "/tmp/never", "--once"], capture_output=True, text=True)
        self.assertEqual(done.returncode, 2)
        self.assertIn("NOT_LOOPBACK", done.stderr)


if __name__ == "__main__":
    unittest.main()
