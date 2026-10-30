"""M3 + M4: resource levels from Core-Lite readings, the three Feishu cards, and
the rules for when a card is sent, patched, or left alone.

Offline: a stub service, a temporary inbox and ledger, a recording transport and
a hand-set clock. Nothing is sent anywhere.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
for extra in (ROOT, ROOT / "integrations" / "fleet-next-agent-monitor",
              ROOT / "integrations" / "fleet-hub-watch"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from fleet_hub_watch import cards, resources  # noqa: E402
from fleet_hub_watch.levels import ALERT, OK, UNCONFIRMED, WARN  # noqa: E402
from fleet_hub_watch.notify import (  # noqa: E402
    MAX_SENDS_PER_TICK, JournalTransport, Notifier, _key, _version,
)
from fleet_mcp.inbox import EventInbox  # noqa: E402

THRESHOLDS = resources.load_thresholds(ROOT / "runtime" / "attribution-contract.yaml")
TOKYO, DELL, WORK = "device:tokyo-shared-dev-01", "device:llm-lab-dell", "device:work-fj"
GiB = 1024 ** 3
# Thursday 2026-10-29, 10:00 in Beijing.
THURSDAY_10 = datetime(2026, 10, 29, 2, 0, tzinfo=timezone.utc)


def summary(*, memory=50.0, disk=40.0, psi_some=0.0, psi_full=0.0, swap=None,
            service_state=None, connected=True, fresh=True, gpu=None, no_disk=False):
    observations = {
        "cpu": {"state": "PRESENT", "value": {"used_percent": 12.0}},
        "memory": {"state": "PRESENT", "value": {"used_percent": memory, "total_bytes": 64 * GiB}},
        "psi.memory": {"state": "PRESENT", "value": {"some": {"avg60": psi_some},
                                                     "full": {"avg60": psi_full}}},
        "storage.readonly": {"state": "PRESENT", "value": {"filesystems": [
            {"role": "DATA", "total_bytes": 1000 * GiB,
             "available_bytes": (1000 - 10 * disk) * GiB, "used_bytes": 10 * disk * GiB}]}},
    }
    if no_disk:
        observations["storage.readonly"] = {"state": "UNAVAILABLE", "reason": "NO_SOURCE"}
    if swap is not None:
        observations["swap"] = {"state": "PRESENT", "value": {
            "total_bytes": 100 * GiB, "used_bytes": swap * GiB, "free_bytes": (100 - swap) * GiB}}
    if service_state:
        observations["services"] = {"state": "PRESENT", "value": {"services": [
            {"id": "serverosd", "unit": "serverosd.service", "state": service_state}]}}
    if gpu is not None:
        observations["gpu.readonly"] = {"state": "PRESENT", "value": {"gpus": [
            {"index": 0, "label": "A6000", "memory_used_bytes": gpu * GiB,
             "memory_total_bytes": 48 * GiB}]}}
    return {"connected": "CONNECTED" if connected else "NOT_CONNECTED",
            "freshness": {"state": "FRESH" if fresh else "STALE"},
            "source": {"received_at": "2026-10-29T01:59:00Z"},
            "observations": observations}


def levels_of(view):
    return {r["key"]: r["level"] for r in view["readings"]}


class TheThresholdsAreTheNodesOwn(unittest.TestCase):
    def test_they_come_from_the_attribution_contract(self):
        self.assertEqual(THRESHOLDS["filesystem_percent_high"], 85.0)
        self.assertEqual(THRESHOLDS["filesystem_percent_critical"], 92.0)
        self.assertEqual(THRESHOLDS["memory_percent_high"], 90.0)


class DeviceLevels(unittest.TestCase):
    def view(self, previous=None, **kw):
        return resources.device_view(DELL, "Dell A6000", summary(**kw), THRESHOLDS, previous)

    def test_disk_has_two_bars(self):
        self.assertEqual(levels_of(self.view(disk=80))["fs:DATA"], OK)
        self.assertEqual(levels_of(self.view(disk=86))["fs:DATA"], WARN)
        self.assertEqual(levels_of(self.view(disk=93))["fs:DATA"], ALERT)

    def test_a_level_is_left_only_past_the_recovery_margin(self):
        # 92 - 3 = 89: still ALERT at 90, WARN at 88; 85 - 3 = 82: still WARN at 83.
        self.assertEqual(levels_of(self.view({"fs:DATA": ALERT}, disk=90))["fs:DATA"], ALERT)
        self.assertEqual(levels_of(self.view({"fs:DATA": ALERT}, disk=88))["fs:DATA"], WARN)
        self.assertEqual(levels_of(self.view({"fs:DATA": WARN}, disk=83))["fs:DATA"], WARN)
        self.assertEqual(levels_of(self.view({"fs:DATA": WARN}, disk=81))["fs:DATA"], OK)

    def test_memory_swap_psi_and_services(self):
        self.assertEqual(levels_of(self.view(memory=91))["memory"], WARN)
        self.assertEqual(levels_of(self.view(swap=95))["swap"], WARN)
        self.assertEqual(levels_of(self.view(psi_some=2.0))["psi"], WARN)
        self.assertEqual(levels_of(self.view(psi_full=0.6))["psi"], ALERT)
        self.assertEqual(levels_of(self.view(service_state="FAILED"))["svc:serverosd"], ALERT)

    def test_gpu_memory_is_shown_and_never_judged(self):
        view = self.view(gpu=47)
        self.assertEqual(levels_of(view)["gpu:0"], OK)
        self.assertIn("47/48 GiB", next(r["text"] for r in view["readings"] if r["key"] == "gpu:0"))

    def test_missing_is_unconfirmed_never_ok_or_alarm(self):
        never_seen = resources.device_view(DELL, "Dell", None, THRESHOLDS, {})
        self.assertEqual(never_seen["level"], UNCONFIRMED)
        stale = self.view(fresh=False)
        self.assertEqual(levels_of(stale)["reach"], UNCONFIRMED)

    def test_a_device_seen_before_and_now_gone_is_an_alarm(self):
        gone = resources.device_view(DELL, "Dell", summary(connected=False), THRESHOLDS,
                                     {"reach": OK})
        self.assertEqual(gone["level"], ALERT)


class StubService:
    def __init__(self, summaries):
        self.summaries = summaries
        self.registry = SimpleNamespace(all=lambda: [
            SimpleNamespace(device_ref=SimpleNamespace(ref=ref), ownership=own, display_name=name)
            for ref, own, name in ((TOKYO, "company", "Tokyo"), (DELL, "company", "Dell A6000"),
                                   ("device:personal-dell", "personal", "personal"))])

    def core_lite_status(self, principal, device):
        if device == "device:personal-dell":
            raise AssertionError("a personal device is never read")
        return self.summaries.get(device)


class Sweep(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.dir = Path(self._td.name)
        self.inbox = EventInbox(self.dir / "inbox.sqlite")
        self.store = resources.LevelStore(self.dir / "resources.sqlite")

    def tearDown(self):
        self._td.cleanup()

    def sweep(self, at, **by_device):
        return resources.sweep(StubService(by_device), None, self.inbox, self.store, THRESHOLDS,
                               now_iso=at)

    def kinds(self):
        return [(e["kind"], e["level"]) for e in self.inbox.read("t", limit=200)["events"]]

    def test_an_all_normal_first_sweep_records_nothing(self):
        self.sweep("2026-10-29T02:00:00Z", **{TOKYO: summary(), DELL: summary()})
        self.assertEqual(self.kinds(), [])

    def test_two_readings_changing_together_are_two_events(self):
        self.sweep("2026-10-29T02:00:00Z", **{DELL: summary()})
        self.sweep("2026-10-29T02:30:00Z", **{DELL: summary(memory=91, disk=93)})
        self.assertEqual(sorted(self.kinds()),
                         [("node.pressure", "decide"), ("node.pressure", "notify")])

    def test_recovery_is_recorded_and_a_missing_reading_clears_nothing(self):
        self.sweep("2026-10-29T02:00:00Z", **{DELL: summary(disk=93)})
        self.sweep("2026-10-29T02:30:00Z", **{DELL: summary(no_disk=True)})  # disk unread: no change
        self.sweep("2026-10-29T03:00:00Z", **{DELL: summary(disk=50)})
        kinds = [k for k, _ in self.kinds()]
        self.assertEqual(kinds.count("node.recovered"), 1)
        self.assertEqual(kinds[0], "node.pressure")

    def test_the_personal_device_is_not_read(self):
        self.sweep("2026-10-29T02:00:00Z", **{TOKYO: summary()})


class Recorder:
    def __init__(self):
        self.ops = []
        self.fail_sends = False

    def send(self, card, *, idempotency_key):
        if self.fail_sends:
            return None
        message_id = f"m{len(self.ops)}"
        self.ops.append(("send", message_id, card, idempotency_key))
        return message_id

    def patch(self, message_id, card, *, sent_at):
        self.ops.append(("patch", message_id, card, None))
        return True

    def headers(self):
        return [(op, mid, c["header"]["template"], c["header"]["title"]["content"])
                for op, mid, c, _ in self.ops]


def view(level, name="Tokyo", ref=TOKYO, text="91% / 64 GiB"):
    return {"device_ref": ref, "name": name, "level": level, "observed_at": None,
            "readings": [{"key": "memory", "label": "内存", "text": text, "level": level}]}


class TheCardsFollowFeishuRules(unittest.TestCase):
    def test_every_card_is_patchable_valid_and_small(self):
        now = THURSDAY_10
        built = [
            cards.resource_card([view(OK), view(WARN, "Dell", DELL)], now=now),
            cards.alert_card({"episode": "e", "key": "fs:DATA", "subject": "Dell · 磁盘 DATA",
                              "level": ALERT, "text": "93%", "first_seen": "2026-10-29T01:00:00Z"},
                             now=now),
            cards.completion_card([{"run_id": "r1", "project": "serveros"}], now=now),
        ]
        for card in built:
            with self.subTest(title=card["header"]["title"]["content"]):
                self.assertEqual(card["schema"], "2.0")
                self.assertIs(card["config"]["update_multi"], True)
                self.assertLess(len(json.dumps(card, ensure_ascii=False).encode()), 30000)

    def test_three_levels_three_colours(self):
        self.assertEqual(cards.resource_card([view(OK)], now=THURSDAY_10)["header"]["template"], "green")
        self.assertEqual(cards.resource_card([view(WARN)], now=THURSDAY_10)["header"]["template"], "yellow")
        self.assertEqual(cards.resource_card([view(ALERT)], now=THURSDAY_10)["header"]["template"], "red")
        self.assertEqual(cards.resource_card([view(UNCONFIRMED)], now=THURSDAY_10)["header"]["template"], "grey")

    def test_what_needs_attention_is_on_the_face(self):
        card = cards.resource_card([view(OK), view(ALERT, "Dell", DELL, "93%")], now=THURSDAY_10)
        face = card["body"]["elements"][0]["content"]
        self.assertIn("🚨 报警 1", face)
        self.assertIn("需要关注", card["body"]["elements"][1]["content"])

    def test_an_alert_card_says_impact_is_unconfirmed_and_suggests(self):
        card = cards.alert_card({"episode": "e", "key": "fs:DATA", "subject": "Dell · 磁盘 DATA",
                                 "level": WARN, "text": "86%"}, now=THURSDAY_10)
        face = card["body"]["elements"][0]["content"]
        self.assertIn("**影响** 待确认", face)
        self.assertIn("**建议**", face)
        self.assertEqual(card["header"]["template"], "yellow")

    def test_a_sample_says_so(self):
        card = cards.resource_card([view(OK)], now=THURSDAY_10, sample=True)
        self.assertIn("[示例]", card["header"]["title"]["content"])
        self.assertIn("示例数据", card["body"]["elements"][0]["content"])


class NotifierTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.dir = Path(self._td.name)
        self.inbox = EventInbox(self.dir / "inbox.sqlite")
        self.transport = Recorder()
        self.notifier = Notifier(self.inbox, self.dir / "notify.sqlite", self.transport,
                                 device_names={TOKYO: "Tokyo", DELL: "Dell A6000"},
                                 sleep=lambda _s: None)

    def tearDown(self):
        self._td.cleanup()

    def node(self, level, at, *, key="fs:DATA", recovered=False):
        self.inbox.record({"kind": "node.recovered" if recovered else "node.pressure",
                           "level": "auto" if recovered else "notify", "device_ref": DELL,
                           "occurred_at": at, "summary": f"Dell A6000 · 磁盘 DATA：{level}",
                           "evidence": f"{key}|{'OK' if recovered else level}",
                           "event_id": f"{key}{level}{at}{recovered}"})

    def run_event(self, kind, run_id="run-1", at="2026-10-29T02:00:00Z"):
        self.inbox.record({"kind": kind, "level": "notify", "device_ref": TOKYO,
                           "session_ref": f"{TOKYO}|claude|s-1", "run_id": run_id,
                           "project": "serveros", "occurred_at": at,
                           "summary": f"serveros: run {run_id} failed (RUN_FAILED)"})


class TheDailyStatusCard(NotifierTest):
    def test_nothing_outside_the_window_or_at_the_weekend(self):
        night = datetime(2026, 10, 29, 13, 0, tzinfo=timezone.utc)      # 21:00 Beijing
        saturday = datetime(2026, 10, 31, 2, 0, tzinfo=timezone.utc)
        self.assertEqual(self.notifier.status([view(OK)], night), "OUTSIDE_WINDOW")
        self.assertEqual(self.notifier.status([view(OK)], saturday), "OUTSIDE_WINDOW")
        self.assertEqual(self.transport.ops, [])

    def test_one_card_a_day_patched_at_the_next_slot(self):
        self.assertEqual(self.notifier.status([view(OK)], THURSDAY_10), "SENT")
        later = THURSDAY_10 + timedelta(minutes=10)
        self.assertEqual(self.notifier.status([view(WARN)], later), "WAITING_FOR_SLOT")
        slot = THURSDAY_10 + timedelta(minutes=30)
        self.assertEqual(self.notifier.status([view(WARN)], slot), "PATCHED")
        self.assertEqual(self.notifier.status([view(WARN)], slot + timedelta(minutes=30)), "UNCHANGED")
        self.assertEqual([op for op, *_ in self.transport.ops], ["send", "patch"])

    def test_an_alarm_does_not_wait_for_the_slot(self):
        self.notifier.status([view(OK)], THURSDAY_10)
        self.assertEqual(self.notifier.status([view(ALERT)], THURSDAY_10 + timedelta(minutes=2)),
                         "PATCHED")

    def test_the_next_workday_is_a_new_card(self):
        self.notifier.status([view(OK)], THURSDAY_10)
        self.notifier.status([view(OK)], THURSDAY_10 + timedelta(days=1))
        self.assertEqual([op for op, *_ in self.transport.ops], ["send", "send"])


class AlertEpisodes(NotifierTest):
    def tick(self, at):
        return self.notifier.tick([], at)

    def test_yellow_then_red_then_yellow_then_recovered(self):
        self.node(WARN, "2026-10-29T02:00:00Z")
        self.tick(THURSDAY_10)
        self.node(ALERT, "2026-10-29T02:10:00Z")
        self.tick(THURSDAY_10 + timedelta(minutes=10))
        self.node(WARN, "2026-10-29T02:20:00Z")
        self.tick(THURSDAY_10 + timedelta(minutes=20))
        self.node(OK, "2026-10-29T02:30:00Z", recovered=True)
        self.tick(THURSDAY_10 + timedelta(minutes=30))
        self.assertEqual([(op, mid, colour) for op, mid, colour, _ in self.transport.headers()], [
            ("send", "m0", "yellow"),     # opened: 注意
            ("patch", "m0", "grey"),      # escalated: the old card says 已升级
            ("send", "m2", "red"),        # and a new red card interrupts
            ("patch", "m2", "yellow"),    # lowered in place, no new message
            ("patch", "m2", "green"),     # 已恢复, in place
        ])

    def test_a_reopened_episode_is_a_new_card(self):
        self.node(WARN, "2026-10-29T02:00:00Z")
        self.node(OK, "2026-10-29T02:10:00Z", recovered=True)
        self.node(WARN, "2026-10-29T02:20:00Z")
        self.tick(THURSDAY_10)
        self.assertEqual([op for op, *_ in self.transport.ops], ["send", "patch", "send"])

    def test_a_card_past_the_patch_window_is_continued_on_a_new_one(self):
        self.node(WARN, "2026-10-29T02:00:00Z")
        self.tick(THURSDAY_10)
        self.inbox.record({"kind": "node.pressure", "level": "notify", "device_ref": DELL,
                           "occurred_at": "2026-11-12T02:00:00Z",
                           "summary": "Dell A6000 · 磁盘 DATA：still 86%", "evidence": "fs:DATA|WARN"})
        self.tick(THURSDAY_10 + timedelta(days=14))
        # The first card can no longer be patched, so the same episode continues
        # on a second card rather than failing to update the first.
        self.assertEqual([(op, mid) for op, mid, *_ in self.transport.ops],
                         [("send", "m0"), ("send", "m1")])

    def test_a_failed_run_is_red_and_says_why(self):
        self.run_event("run.failed")
        self.tick(THURSDAY_10)
        (op, _mid, card, _key) = self.transport.ops[0]
        self.assertEqual(card["header"]["template"], "red")
        self.assertIn("任务失败 · serveros", card["header"]["title"]["content"])
        self.assertIn("执行失败（RUN_FAILED）", card["body"]["elements"][0]["content"])

    def test_a_stalled_run_that_completes_is_closed_and_counted(self):
        self.run_event("run.stalled")
        self.tick(THURSDAY_10)
        self.run_event("run.completed", at="2026-10-29T02:40:00Z")
        self.tick(THURSDAY_10 + timedelta(minutes=1))
        self.assertEqual(self.transport.headers()[-1][2], "green")
        self.notifier.tick([], THURSDAY_10 + timedelta(minutes=7))
        self.assertIn("任务完成 · 1 个", self.transport.headers()[-1][3])


class CompletionsAndPacing(NotifierTest):
    def test_completions_in_five_minutes_are_one_card(self):
        for i in range(3):
            self.run_event("run.completed", run_id=f"run-{i}")
        self.notifier.tick([], THURSDAY_10)
        self.assertEqual(self.transport.ops, [], "the batch waits for its window")
        self.notifier.tick([], THURSDAY_10 + timedelta(minutes=5))
        (header,) = self.transport.headers()
        self.assertIn("任务完成 · 3 个", header[3])

    def test_a_tick_sends_at_most_the_cap_and_keeps_the_rest(self):
        for i in range(MAX_SENDS_PER_TICK + 5):
            self.run_event("run.failed", run_id=f"run-{i}")
        self.notifier.tick([], THURSDAY_10)
        self.assertEqual(len(self.transport.ops), MAX_SENDS_PER_TICK)
        self.notifier.tick([], THURSDAY_10 + timedelta(seconds=30))
        self.assertEqual(len(self.transport.ops), MAX_SENDS_PER_TICK + 5)

    def test_a_failed_send_is_retried_not_lost(self):
        self.transport.fail_sends = True
        self.run_event("run.failed")
        self.notifier.tick([], THURSDAY_10)
        self.transport.fail_sends = False
        self.inbox.record({"kind": "run.failed", "level": "notify", "device_ref": TOKYO,
                           "session_ref": f"{TOKYO}|claude|s-1", "run_id": "run-1",
                           "project": "serveros", "occurred_at": "2026-10-29T02:05:00Z",
                           "summary": "serveros: run run-1 failed (RUN_FAILED)",
                           "event_id": "retry-path"})
        self.notifier.tick([], THURSDAY_10 + timedelta(seconds=30))
        self.assertEqual([op for op, *_ in self.transport.ops], ["send"])

    def test_the_send_key_is_stable_for_the_same_card(self):
        card = cards.completion_card([{"run_id": "r1"}], now=THURSDAY_10)
        again = cards.completion_card([{"run_id": "r1"}], now=THURSDAY_10 + timedelta(minutes=1))
        self.assertEqual(_key("k", _version(card)), _key("k", _version(again)))
        self.assertLessEqual(len(_key("k", _version(card))), 50)


class TheJournalSendsNothing(unittest.TestCase):
    def test_it_writes_what_it_would_send(self):
        with tempfile.TemporaryDirectory() as td:
            journal = JournalTransport(Path(td) / "j.jsonl")
            message_id = journal.send({"x": 1}, idempotency_key="abc")
            self.assertTrue(message_id.startswith("journal-"))
            row = json.loads((Path(td) / "j.jsonl").read_text().splitlines()[0])
            self.assertEqual(row["op"], "send")


if __name__ == "__main__":
    unittest.main()


class TheCommittedSamplesAreCurrent(unittest.TestCase):
    def test_each_sample_is_what_the_renderer_produces_and_says_it_is_a_sample(self):
        import importlib.util
        here = ROOT / "integrations" / "fleet-hub-watch" / "samples"
        spec = importlib.util.spec_from_file_location("hub_samples", here / "build_samples.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for name, card in module.SAMPLES.items():
            with self.subTest(sample=name):
                committed = json.loads((here / f"{name}.json").read_text(encoding="utf-8"))
                self.assertEqual(committed, card, "run samples/build_samples.py and commit")
                self.assertIn("[示例]", card["header"]["title"]["content"])
