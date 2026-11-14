"""O-3: the watcher's watchdog tells the owner once when the watcher stops or
keeps failing, reminds while it stays broken, and says when it recovers.

2026-11-12 19:03: every tick failed for a day, the unit stayed active, and the
watcher could not report its own failure.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for extra in (ROOT, ROOT / "integrations" / "fleet-next-agent-monitor",
              ROOT / "integrations" / "fleet-hub-watch"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from fleet_hub_watch import watchdog  # noqa: E402

NOW = 1_800_000_000.0


class Harness(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        d = Path(self._td.name)
        self.beat, self.state = d / "watch.heartbeat.json", d / "watchdog.state.json"
        self.sent, self.accept = [], True

    def tearDown(self):
        self._td.cleanup()

    def send(self, card, key):
        self.sent.append(card["header"]["title"]["content"])
        return self.accept

    def tick(self, at, *, ok=True, error=None, beat_at=None):
        doc = {"last_tick_at": at if beat_at is None else beat_at, "ok": ok}
        if error:
            doc["error"] = error
        self.beat.write_text(json.dumps(doc))

    def check(self, at):
        return watchdog.run_once(self.beat, self.state, self.send, now=at)


class TheWatcherStops(Harness):
    def test_a_stale_heartbeat_is_told_once_then_reminded_then_cleared(self):
        self.tick(NOW, beat_at=NOW - 3600)
        self.check(NOW)
        self.assertEqual(self.sent, ["Fleet 监控停了"])
        self.check(NOW + 300)
        self.assertEqual(len(self.sent), 1, "no second card for the same problem")
        self.check(NOW + watchdog.REMIND_SECONDS + 1)
        self.assertEqual(self.sent[-1], "Fleet 监控停了（仍未恢复）")
        self.tick(NOW + watchdog.REMIND_SECONDS + 300)
        self.check(NOW + watchdog.REMIND_SECONDS + 310)
        self.assertEqual(self.sent[-1], "Fleet 监控已恢复")
        self.tick(NOW + watchdog.REMIND_SECONDS + 600)
        self.check(NOW + watchdog.REMIND_SECONDS + 610)
        self.assertEqual(len(self.sent), 3, "a healthy watcher sends nothing")

    def test_no_heartbeat_at_all_is_a_problem(self):
        self.check(NOW)
        self.assertEqual(self.sent, ["Fleet 监控没有心跳"])


class TheWatcherKeepsFailing(Harness):
    def test_three_failing_checks_in_a_row_open_a_problem_with_the_reason(self):
        for i in range(watchdog.FAIL_STREAK - 1):
            self.tick(NOW + 300 * i, ok=False, error="TransportFault: the CLI refused")
            self.check(NOW + 300 * i)
        self.assertEqual(self.sent, [], "one or two bad ticks are not yet a problem")
        self.tick(NOW + 900, ok=False, error="TransportFault: the CLI refused")
        state = self.check(NOW + 900)
        self.assertEqual(self.sent, ["Fleet 监控每轮都在失败"])
        self.assertEqual(state["open"], "failing")
        self.assertIn("TransportFault", state["error"])

    def test_a_good_tick_resets_the_streak(self):
        self.tick(NOW, ok=False, error="x")
        self.check(NOW)
        self.tick(NOW + 300)
        self.check(NOW + 300)
        self.tick(NOW + 600, ok=False, error="x")
        self.assertEqual(self.check(NOW + 600)["streak"], 1)


class ASendThatFails(Harness):
    def test_is_tried_again_next_time(self):
        self.accept = False
        self.check(NOW)
        self.assertIsNone(json.loads(self.state.read_text())["open"])
        self.accept = True
        self.check(NOW + 300)
        self.assertEqual(json.loads(self.state.read_text())["open"], "missing")
        self.assertEqual(len(self.sent), 2)


class TheKitInstallsIt(unittest.TestCase):
    def test_the_hub_kit_pins_the_watchdog_unit_and_timer(self):
        kit = json.loads((ROOT / "distribution/devices/tokyo/DEVICE.json").read_text())
        dog = kit["layers"]["monitor"]["hub_watch"]["watchdog"]
        self.assertEqual(dog["unit"], "serveros-fleet-watchdog.timer")
        self.assertTrue(dog["template"]["digest"].startswith("sha256:"))
        self.assertTrue(dog["timer"]["digest"].startswith("sha256:"))


if __name__ == "__main__":
    unittest.main()


class TheSignInIsWatched(Harness):
    """D-5: refused tokens and a regenerated signing key reach the owner."""

    def setUp(self):
        super().setUp()
        self.store = Path(self._td.name) / "oauth"
        self.store.mkdir()
        self.tick(NOW)

    def counts(self, pid, **counts):
        (self.store / "auth-failures.json").write_text(json.dumps(
            {"pid": pid, "updated": NOW,
             "counts": {k.replace("_", " "): {"count": v, "last_at": NOW} for k, v in counts.items()}}))

    def check(self, at):
        return watchdog.run_once(self.beat, self.state, self.send, now=at, store_root=self.store)

    def test_refusals_with_no_success_open_a_problem_and_a_success_clears_it(self):
        self.counts(1, signature_does_not_verify=0, ok=10)
        self.check(NOW)
        self.assertEqual(self.sent, [])
        self.counts(1, signature_does_not_verify=7, ok=10)
        self.tick(NOW + 300)
        self.check(NOW + 300)
        self.assertEqual(self.sent, ["Fleet MCP 认证一直失败"])
        self.counts(1, signature_does_not_verify=9, ok=12)
        self.tick(NOW + 600)
        self.check(NOW + 600)
        self.assertEqual(self.sent[-1], "Fleet MCP 认证已恢复")

    def test_expired_tokens_are_not_a_problem(self):
        self.counts(1, ok=1)
        self.check(NOW)
        self.counts(1, token_expired=50, ok=1)
        self.tick(NOW + 300)
        self.check(NOW + 300)
        self.assertEqual(self.sent, [])

    def test_a_restarted_server_does_not_count_as_a_drop(self):
        self.counts(1, signature_does_not_verify=100, ok=0)
        self.check(NOW)
        self.sent.clear()
        self.counts(2, signature_does_not_verify=2, ok=0)
        self.tick(NOW + 300)
        self.check(NOW + 300)
        self.assertNotIn("Fleet MCP 认证一直失败", self.sent[1:])

    def test_a_regenerated_key_is_told_once(self):
        (self.store / "key-regenerated.json").write_text(json.dumps(
            {"at": NOW - 60, "registered_clients": 3, "refresh_chains": 2}))
        self.check(NOW)
        self.assertEqual(self.sent, ["Fleet 签名密钥被重新生成"])
        self.tick(NOW + 300)
        self.check(NOW + 300)
        self.assertEqual(len(self.sent), 1)
