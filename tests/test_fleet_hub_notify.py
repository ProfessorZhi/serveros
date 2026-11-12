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
        # `{"filesystems": {"roles": [...]}}` is the contract's shape
        # (core-lite-status.v1.md); a bare list here would be a second spelling
        # of the same fact, and the one the reader would then be written against.
        "storage.readonly": {"state": "PRESENT", "value": {"filesystems": {"roles": [
            {"role": "DATA", "total_bytes": 1000 * GiB,
             "available_bytes": (1000 - 10 * disk) * GiB, "used_bytes": 10 * disk * GiB}]}}},
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
        # Owner, 2026-11-09: a light full stall is 注意 on 状态; 5% is an alert.
        self.assertEqual(levels_of(self.view(psi_full=0.6))["psi"], WARN)
        self.assertEqual(levels_of(self.view(psi_full=5.5))["psi"], ALERT)
        self.assertEqual(levels_of(self.view(service_state="FAILED"))["svc:serverosd"], ALERT)

    def test_psi_full_holds_until_it_is_past_its_own_margin(self):
        """0.5 opens; with the 0.25 margin it holds at 0.42 and leaves at 0.2.
        Without a margin a host hovering at the line chattered red/green."""
        self.assertEqual(levels_of(self.view({"psi": ALERT}, psi_full=4.9))["psi"], ALERT)
        self.assertEqual(levels_of(self.view({"psi": WARN}, psi_full=0.42))["psi"], WARN)
        self.assertEqual(levels_of(self.view({"psi": ALERT}, psi_full=0.2))["psi"], OK)
        self.assertEqual(levels_of(self.view(psi_full=0.42))["psi"], OK)

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

    def bound_runtimes(self, device):
        return ("claude",)

    def core_lite_status(self, principal, device, *, runtime="claude"):
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


class _Refused(Exception):
    code = "PROVIDER_TIMEOUT"


class Failing(StubService):
    """Answers what it was given, or raises it when the tunnel raised."""

    def core_lite_status(self, principal, device, *, runtime="claude"):
        answer = self.summaries.get(device)
        if isinstance(answer, BaseException):
            raise answer
        return answer


class ReachIsConfirmedBeforeItIsAnOutage(unittest.TestCase):
    """Owner, 2026-11-11: one slow read is not a machine that went away.

    The hub's own session reads share the tunnel with this probe, so a big
    transcript could starve it past the read timeout; the next tick found the
    tunnel free and the pair read as 失联 → 已恢复, twice an hour. Three
    consecutive failures before 失联, and the event names the failure it saw.
    """

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.dir = Path(self._td.name)
        self.inbox = EventInbox(self.dir / "inbox.sqlite")
        self.store = resources.LevelStore(self.dir / "resources.sqlite")

    def tearDown(self):
        self._td.cleanup()

    def sweep(self, at, **by_device):
        return resources.sweep(Failing(by_device), None, self.inbox, self.store, THRESHOLDS,
                               now_iso=at)

    def kinds(self):
        return [(e["kind"], e["evidence"]) for e in self.inbox.read("t", limit=200)["events"]]

    def test_a_failed_read_is_counted_and_holds_the_previous_level(self):
        self.sweep("2026-10-29T02:00:00Z", **{TOKYO: summary()})
        view = self.sweep("2026-10-29T02:00:30Z", **{TOKYO: _Refused("slow")})[0]
        self.assertFalse(view["connected"])
        self.assertEqual(view["level"], OK, "the level it had before the miss")
        reach = [r for r in view["readings"] if r["key"] == "reach"][0]
        self.assertEqual(reach["text"], f"读数中断（1/{resources.REACH_CONFIRMATIONS} 次）")
        self.assertEqual(self.kinds(), [], "one miss is counted, not carded")

    def test_three_in_a_row_are_an_outage_that_names_the_reason(self):
        self.sweep("2026-10-29T02:00:00Z", **{TOKYO: summary()})
        for i in range(resources.REACH_CONFIRMATIONS):
            self.sweep(f"2026-10-29T02:0{i}:30Z", **{TOKYO: _Refused("slow")})
        events = self.kinds()
        self.assertEqual(len(events), 1, "one outage, told once")
        self.assertEqual(events[0][0], "node.unreachable")
        self.assertEqual(events[0][1], "reach|ALERT|PROVIDER_TIMEOUT")
        self.sweep("2026-10-29T02:09:00Z", **{TOKYO: _Refused("slow")})
        self.assertEqual(len(self.kinds()), 1, "a continuing outage does not page again")

    def test_a_read_that_comes_back_clears_the_streak(self):
        self.sweep("2026-10-29T02:00:00Z", **{TOKYO: summary()})
        self.sweep("2026-10-29T02:00:30Z", **{TOKYO: _Refused("slow")})
        self.sweep("2026-10-29T02:01:00Z", **{TOKYO: summary()})
        self.sweep("2026-10-29T02:01:30Z", **{TOKYO: _Refused("slow")})
        self.sweep("2026-10-29T02:02:00Z", **{TOKYO: _Refused("slow")})
        self.assertEqual(self.kinds(), [], "the streak restarted at the good read")


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

    def test_an_alert_card_is_what_happened_and_what_to_do_in_two_lines(self):
        card = cards.alert_card({"episode": "e", "key": "fs:DATA", "subject": "Dell · 磁盘 DATA",
                                 "level": WARN, "text": "86%"}, now=THURSDAY_10)
        face = card["body"]["elements"][0]["content"]
        self.assertEqual(face.split("\n")[0], "86%")
        self.assertIn("清理或扩容", face)
        self.assertNotIn("影响", face, "no 待确认 filler (owner, 2026-11-09)")
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

    def run_event(self, kind, run_id="run-1", at="2026-10-29T02:00:00Z", dispatched=True):
        if dispatched:
            self.inbox.note_dispatch(f"{TOKYO}|claude|s-1", run_id, "serveros")
        self.inbox.record({"kind": kind, "level": "notify", "device_ref": TOKYO,
                           "session_ref": f"{TOKYO}|claude|s-1", "run_id": run_id,
                           "project": "serveros", "occurred_at": at,
                           "summary": f"serveros: run {run_id} failed (RUN_FAILED)"})


class OldTaskAlertsExpire(NotifierTest):
    def test_an_open_task_alert_older_than_six_hours_leaves_the_open_list_quietly(self):
        self.run_event("run.failed", run_id="old", at="2026-10-29T02:00:00Z")
        self.notifier.tick([], THURSDAY_10)
        sent = len(self.transport.ops)
        self.assertEqual(self.notifier.expire_runs(THURSDAY_10 + timedelta(hours=7)), 1)
        self.assertEqual(len(self.transport.ops), sent, "no message, no patch")
        self.assertEqual(self.notifier.day_summary("2026-10-29")["open_now"], [])


class ResourceCardsAreAskedFor(NotifierTest):
    """Owner, 2026-11-09: 资源 cards come when asked (「资源 东京」), not every
    half hour; alerts, batch completions and the 日报 still push."""

    def test_by_default_nothing_is_pushed(self):
        self.assertEqual(self.notifier.status([view(WARN)], THURSDAY_10), "ON_DEMAND")
        self.assertEqual(self.transport.ops, [])


class TheDailyStatusCard(NotifierTest):
    def setUp(self):
        super().setUp()
        self.notifier.spec["schedule"]["resource_push"] = True     # the opt-in push mode

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

    def test_red_that_holds_opens_one_card_then_lowers_and_recovers_in_place(self):
        self.node(ALERT, "2026-10-29T02:00:00Z")
        self.tick(THURSDAY_10)
        self.assertEqual(self.transport.ops, [], "a red waits out the debounce")
        self.tick(THURSDAY_10 + timedelta(minutes=5))
        self.node(WARN, "2026-10-29T02:10:00Z")
        self.tick(THURSDAY_10 + timedelta(minutes=10))
        self.node(OK, "2026-10-29T02:20:00Z", recovered=True)
        self.tick(THURSDAY_10 + timedelta(minutes=20))
        self.assertEqual([(op, mid, colour) for op, mid, colour, _ in self.transport.headers()], [
            ("send", "m0", "red"),        # held five minutes: one card interrupts
            ("patch", "m0", "yellow"),    # lowered in place, no new message
            ("patch", "m0", "green"),     # 已恢复, in place
        ])

    def test_a_red_blip_shorter_than_the_debounce_sends_nothing(self):
        self.node(ALERT, "2026-10-29T02:00:00Z")
        self.tick(THURSDAY_10)
        self.node(OK, "2026-10-29T02:02:00Z", recovered=True)
        self.tick(THURSDAY_10 + timedelta(minutes=2))
        self.tick(THURSDAY_10 + timedelta(minutes=10))
        self.assertEqual(self.transport.ops, [])
        # It is still counted for the 日报.
        day = self.notifier.day_summary("2026-10-29")
        self.assertTrue(day, "the blip is recorded for the daily report")

    def test_a_warning_never_opens_a_card_of_its_own(self):
        self.node(WARN, "2026-10-29T02:00:00Z")
        self.tick(THURSDAY_10)
        self.tick(THURSDAY_10 + timedelta(hours=1))
        self.node(OK, "2026-10-29T03:30:00Z", recovered=True)
        self.tick(THURSDAY_10 + timedelta(hours=2))
        self.assertEqual(self.transport.ops, [])

    def test_a_red_that_lowers_before_the_debounce_is_not_carded(self):
        self.node(ALERT, "2026-10-29T02:00:00Z")
        self.tick(THURSDAY_10)
        self.node(WARN, "2026-10-29T02:03:00Z")
        self.tick(THURSDAY_10 + timedelta(minutes=3))
        self.tick(THURSDAY_10 + timedelta(minutes=10))
        self.assertEqual(self.transport.ops, [])

    def test_a_reopened_episode_soon_after_is_the_same_card(self):
        """Owner, 2026-11-12: back within FLAP_WINDOW is flapping — one card."""
        self.node(ALERT, "2026-10-29T02:00:00Z")
        self.tick(THURSDAY_10 + timedelta(minutes=5))
        self.node(OK, "2026-10-29T02:10:00Z", recovered=True)
        self.tick(THURSDAY_10 + timedelta(minutes=10))
        self.node(ALERT, "2026-10-29T02:20:00Z")
        self.tick(THURSDAY_10 + timedelta(minutes=25))
        self.assertEqual([op for op, *_ in self.transport.ops], ["send", "patch", "patch"])

    def test_a_reopened_episode_long_after_is_a_new_card(self):
        self.node(ALERT, "2026-10-29T02:00:00Z")
        self.tick(THURSDAY_10 + timedelta(minutes=5))
        self.node(OK, "2026-10-29T02:10:00Z", recovered=True)
        self.tick(THURSDAY_10 + timedelta(minutes=10))
        self.node(ALERT, "2026-10-29T06:20:00Z")
        self.tick(THURSDAY_10 + timedelta(hours=4, minutes=25))
        self.assertEqual([op for op, *_ in self.transport.ops], ["send", "patch", "send"])

    def test_a_card_past_the_patch_window_is_continued_on_a_new_one(self):
        self.node(ALERT, "2026-10-29T02:00:00Z")
        self.tick(THURSDAY_10 + timedelta(minutes=5))
        self.inbox.record({"kind": "node.pressure", "level": "notify", "device_ref": DELL,
                           "occurred_at": "2026-11-12T02:00:00Z",
                           "summary": "Dell A6000 · 磁盘 DATA：still 93%", "evidence": "fs:DATA|ALERT"})
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
        self.assertEqual("Tokyo · 任务失败", card["header"]["title"]["content"],
                         "the device and what happened; the task is the first line")
        self.assertIn("任务</font>　serveros", card["body"]["elements"][0]["content"])
        self.assertIn("**执行出错，没有完成**", card["body"]["elements"][0]["content"])
        self.assertNotIn("RUN_FAILED", card["body"]["elements"][0]["content"], "codes stay in 技术详情")

    def test_a_task_name_has_no_markdown_marks(self):
        """Owner, 2026-11-11: a title read «…收窄为**缓存命中率的计算口径是…»."""
        from fleet_hub_watch.notify import clean_label
        self.assertEqual(clean_label("继续审计，但这次把问题收窄为**缓存命中率的计算口径是否有误**。"),
                         "继续审计，但这次把问题收窄为缓存命中率的计算口径是否有误")
        self.assertTrue(clean_label("一" * 40).endswith("…"))
        self.assertEqual(clean_label("`hostname`"), "hostname")

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

    def test_a_run_that_joins_a_batch_after_queueing_leaves_the_card(self):
        """Owner, 2026-11-10: a batch opened after its run finished got a
        任务完成 card as well as the batch card."""
        for i in range(2):
            self.run_event("run.completed", run_id=f"run-{i}")
        self.notifier.tick([], THURSDAY_10)
        self.inbox.batch_of = lambda _s, run_id: "b1" if run_id == "run-0" else None
        self.notifier.tick([], THURSDAY_10 + timedelta(minutes=5))
        (header,) = self.transport.headers()
        self.assertIn("任务完成 · 1 个", header[3])

    def test_a_queue_of_only_late_batch_members_sends_nothing(self):
        self.run_event("run.completed", run_id="run-0")
        self.notifier.tick([], THURSDAY_10)
        self.inbox.batch_of = lambda *_a: "b1"
        self.notifier.tick([], THURSDAY_10 + timedelta(minutes=5))
        self.notifier.tick([], THURSDAY_10 + timedelta(minutes=10))
        self.assertEqual(self.transport.ops, [])

    def test_the_completion_card_carries_each_devices_answer(self):
        """Owner, 2026-11-10: the 任务完成 card named the task but not what the
        device answered."""
        self.notifier.excerpt = lambda session_ref, run_id: f"答复-{run_id}"
        self.run_event("run.completed", run_id="run-0")
        self.notifier.tick([], THURSDAY_10)
        self.notifier.tick([], THURSDAY_10 + timedelta(minutes=5))
        (op, _mid, card, _key) = self.transport.ops[0]
        self.assertIn('"content": "答复-run-0"', json.dumps(card, ensure_ascii=False))

    def test_a_refused_styled_card_is_sent_plain(self):
        sent = []
        real = self.transport.send

        def picky(card, *, idempotency_key):
            if '"column_set"' in json.dumps(card):
                return None
            sent.append(card)
            return real(card, idempotency_key=idempotency_key)
        self.transport.send = picky
        self.notifier.excerpt = lambda ref, rid: "OK"
        self.run_event("run.completed", run_id="run-0")
        self.notifier.tick([], THURSDAY_10)
        self.notifier.tick([], THURSDAY_10 + timedelta(minutes=5))
        self.assertEqual(len(sent), 1)
        self.assertIn("任务完成 · 1 个", sent[0]["header"]["title"]["content"])

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


class BatchesReportOnce(NotifierTest):
    """Owner, 2026-11-11: one dispatched batch is ONE Feishu card — sent when
    the batch opens, patched in place as each of its agents settles. (Before
    this, the card only appeared once every member was done.)

    What is still true from 2026-11-07: a member's outcome never becomes a
    second message of its own.
    """

    def test_the_batch_card_opens_and_every_settle_patches_it_in_place(self):
        runs = [{"session_ref": f"{TOKYO}|claude|s-1", "run_id": f"run-{i}"} for i in range(3)]
        self.inbox.open_batch("回归 x3", runs, opened_by="commander")
        self.notifier.tick([], THURSDAY_10)
        (op, mid, card, _key) = self.transport.ops[-1]
        self.assertEqual(op, "send", "the card exists before anything is done")
        self.assertEqual(card["header"]["title"]["content"], "回归 x3 · 进行中 0/3")
        self.assertEqual(card["header"]["template"], "blue")

        self.run_event("run.completed", run_id="run-0")
        self.run_event("run.completed", run_id="run-1")
        self.notifier.tick([], THURSDAY_10 + timedelta(minutes=10))
        self.assertTrue(self.transport.ops[1:], "the two settled members do move the card")
        self.assertTrue(all(o[0] == "patch" and o[1] == mid for o in self.transport.ops[1:]),
                        "patched on the batch's own message, never sent again")

        self.run_event("run.completed", run_id="run-2")
        self.notifier.tick([], THURSDAY_10 + timedelta(minutes=11))
        self.assertEqual(len([o for o in self.transport.ops if o[0] == "send"]), 1,
                         "one message for the whole batch")
        header = self.transport.headers()[-1]
        self.assertEqual("回归 x3 · 全部完成", header[3])
        self.assertEqual(header[2], "green")

    def test_a_failed_member_patches_the_card_and_the_last_settle_closes_it(self):
        runs = [{"session_ref": f"{TOKYO}|claude|s-1", "run_id": f"run-{i}"} for i in range(2)]
        self.inbox.open_batch("部署 x2", runs, opened_by="commander")
        self.run_event("run.failed", run_id="run-0")
        self.notifier.tick([], THURSDAY_10)
        self.assertEqual(len([o for o in self.transport.ops if o[0] == "send"]), 1,
                         "the failure is a line on the batch card, not a second message")
        self.assertIn("1 个失败", self.transport.headers()[-1][3])

        self.run_event("run.completed", run_id="run-1")
        self.notifier.tick([], THURSDAY_10 + timedelta(minutes=1))
        self.assertEqual(len([o for o in self.transport.ops if o[0] == "send"]), 1,
                         "still the same one message")
        header = self.transport.headers()[-1]
        self.assertEqual(header[3], "部署 x2 · 1 个失败")
        self.assertEqual(header[2], "red")


class TheBatchCardDoesNotWaitForTheNextTick(NotifierTest):
    """Owner, 2026-11-11: a pong-sized batch opened and settled inside one 30 s
    tick, so the only card the owner ever saw was already finished.

    The inbox is read between ticks; the batch card appears while it runs.
    """

    def test_a_pulse_sends_the_card_before_the_tick_that_would_see_it_done(self):
        runs = [{"session_ref": f"{TOKYO}|claude|s-1", "run_id": f"run-{i}"} for i in range(3)]
        self.inbox.open_batch("冒烟 x3", runs, opened_by="commander")
        self.notifier.pulse(THURSDAY_10)
        (op, mid, card, _key) = self.transport.ops[-1]
        self.assertEqual(op, "send")
        self.assertEqual(card["header"]["title"]["content"], "冒烟 x3 · 进行中 0/3")
        self.assertEqual(card["header"]["template"], "blue",
                         "the first card the owner sees is a running one")

        self.run_event("run.completed", run_id="run-0")
        self.notifier.pulse(THURSDAY_10 + timedelta(seconds=2))
        self.assertTrue(self.transport.ops[1:])
        self.assertTrue(all(o[0] == "patch" and o[1] == mid for o in self.transport.ops[1:]),
                        "still the same message, patched between ticks")


class AResidentSessionsOwnTurnsAreNotTasks(NotifierTest):
    """Owner, 2026-11-09: every turn of a long-running session was carded."""

    def test_an_unreadable_or_failed_turn_nobody_dispatched_is_not_carded(self):
        self.run_event("run.unknown", run_id="turn-2", dispatched=False)
        self.run_event("run.failed", run_id="turn-3", dispatched=False)
        self.notifier.tick([], THURSDAY_10 + timedelta(minutes=10))
        self.assertEqual(self.transport.ops, [])

    def test_a_turn_nobody_dispatched_is_counted_not_carded(self):
        self.run_event("run.completed", run_id="turn-1", dispatched=False)
        self.notifier.tick([], THURSDAY_10 + timedelta(minutes=10))
        self.assertEqual(self.transport.ops, [])
        self.assertEqual(self.notifier.day_summary("2026-10-29")["runs"]["completed"], 1)


class TheBatchCardCarriesTheResults(NotifierTest):
    """Owner, 2026-11-09: GPT does not wait; the monitor reports the batch, so
    the card itself says what each task found."""

    def test_each_task_has_a_line_with_its_reply_and_keys_are_masked(self):
        replies = {"run-0": "Swap 99% used; top user: java (42 GiB)",
                   "run-1": {"content": "token sk-abcdefghijklmnopqrstuv leaked? no"}}
        from fleet_hub_watch.notify import clip_excerpt
        self.notifier.excerpt = lambda ref, rid: clip_excerpt(replies[rid], 200)
        runs = [{"session_ref": f"{TOKYO}|claude|s-1", "run_id": f"run-{i}"} for i in range(2)]
        self.inbox.open_batch("东京排查", runs, opened_by="commander")
        self.run_event("run.completed", run_id="run-0")
        self.run_event("run.completed", run_id="run-1")
        self.notifier.tick([], THURSDAY_10)
        (op, _mid, card, _) = self.transport.ops[-1]
        body = json.dumps(card, ensure_ascii=False)
        self.assertEqual("东京排查 · 全部完成", card["header"]["title"]["content"])
        self.assertIn("Swap 99% used", body)
        self.assertIn("**Tokyo**　<text_tag color='green'>完成</text_tag>", body)
        self.assertIn("Tokyo", body)
        self.assertNotIn("run-0", body, "no run ids on the face")
        self.assertNotIn("sk-abcdefghijklmnop", body)
        self.assertIn("‹已遮蔽›", body)

    def test_an_unreadable_reply_still_sends_the_card(self):
        def broken(ref, rid):
            raise RuntimeError("device gone")
        self.notifier.excerpt = broken
        runs = [{"session_ref": f"{TOKYO}|claude|s-1", "run_id": "run-0"}]
        self.inbox.open_batch("one", runs, opened_by="commander")
        self.run_event("run.completed", run_id="run-0")
        self.notifier.tick([], THURSDAY_10)
        self.assertEqual("one · 全部完成", self.transport.headers()[-1][3])

    def test_excerpts_are_one_line_and_bounded(self):
        from fleet_hub_watch.notify import clip_excerpt
        self.assertEqual(clip_excerpt("a\n\n b", 10), "a b")
        self.assertEqual(len(clip_excerpt("x " * 300, 50)), 50)
        self.assertIsNone(clip_excerpt("text", 0))
        self.assertEqual(clip_excerpt([{"type": "text", "text": "hi"}, {"text": "there"}], 20), "hi there")


class TheInboxClosesABatchOnce(unittest.TestCase):
    def test_runs_already_finished_settle_at_open_and_close_once(self):
        with tempfile.TemporaryDirectory() as d:
            inbox = EventInbox(Path(d) / "i.sqlite")
            run = {"kind": "run.completed", "level": "notify", "device_ref": TOKYO,
                   "session_ref": f"{TOKYO}|claude|s-1", "run_id": "r1", "summary": "x"}
            inbox.record(run)
            answer = inbox.open_batch("one", [{"session_ref": run["session_ref"], "run_id": "r1"}],
                                      opened_by="c")
            self.assertTrue(answer["closed"])
            inbox.record(run)
            closed = [e for e in inbox.read("x", limit=50)["events"] if e["kind"] == "batch.completed"]
            self.assertEqual(len(closed), 1)


class TheOwnerIsFoundWithoutAnId(unittest.TestCase):
    def test_owner_resolves_from_lark_cli(self):
        from fleet_hub_watch.notify import resolve_recipient
        answer = SimpleNamespace(returncode=0, stdout=json.dumps(
            {"ok": True, "data": {"user": {"name": "x", "open_id": "ou_" + "a" * 32}}}))
        self.assertEqual(resolve_recipient("owner", run=lambda argv: answer),
                         "user:ou_" + "a" * 32)

    def test_explicit_recipients_pass_through(self):
        from fleet_hub_watch.notify import resolve_recipient
        self.assertEqual(resolve_recipient("chat:oc_1"), "chat:oc_1")

    def test_unresolvable_owner_is_an_error_not_a_silent_fallback(self):
        from fleet_hub_watch.notify import resolve_recipient
        failed = SimpleNamespace(returncode=1, stdout="")
        with self.assertRaises(RuntimeError):
            resolve_recipient("owner", run=lambda argv: failed)


class EveryDispatchedTaskHasACard(NotifierTest):
    """Owner, 2026-11-11: «分派任务时根本没有飞书卡片». A task dispatched on its own
    gets one card while it runs, patched in place when it ends."""

    def _now(self):
        return datetime.now(timezone.utc)

    def test_a_lone_task_gets_a_card_when_dispatched_and_it_is_patched_at_the_end(self):
        ref = f"{TOKYO}|claude|s-1"
        self.inbox.note_dispatch(ref, "run-9", "serveros", "只回答 pong")
        self.notifier.excerpt = lambda r, rid: "pong 16:01"
        now = self._now()
        self.assertEqual(self.notifier.announce_dispatches(now), 0, "a batch may still claim it")
        later = now + timedelta(seconds=50)
        self.assertEqual(self.notifier.announce_dispatches(later), 1)
        self.assertEqual(self.notifier.announce_dispatches(later), 0, "once")
        (op, _mid, card, _key) = self.transport.ops[-1]
        self.assertEqual(op, "send")
        self.assertIn("进行中", card["header"]["title"]["content"])
        self.inbox.record({"kind": "run.completed", "level": "notify", "device_ref": TOKYO,
                           "session_ref": ref, "run_id": "run-9", "project": "serveros",
                           "occurred_at": later.strftime("%Y-%m-%dT%H:%M:%SZ"),
                           "summary": "serveros: run run-9 completed"})
        self.notifier.tick([], later + timedelta(minutes=1))
        self.notifier.tick([], later + timedelta(minutes=7))
        ops = [op for op, *_ in self.transport.ops]
        self.assertEqual(ops.count("send"), 1, "no second 任务完成 card")
        self.assertEqual(ops[-1], "patch")
        self.assertIn("pong 16:01", json.dumps(self.transport.ops[-1][2], ensure_ascii=False))

    def test_a_task_in_a_batch_is_told_on_the_batch_card_only(self):
        ref = f"{TOKYO}|claude|s-1"
        self.inbox.note_dispatch(ref, "run-8", "serveros", "x")
        self.inbox.open_batch("一批", [{"session_ref": ref, "run_id": "run-8"}], opened_by="gpt")
        self.assertEqual(self.notifier.announce_dispatches(self._now() + timedelta(seconds=30)), 0)
        self.assertEqual(self.transport.ops, [])


class TheNoiseOfTheEleventh(NotifierTest):
    """Owner, 2026-11-12: what the journal showed after the 11-11 deploy."""

    def test_a_restart_does_not_announce_tasks_that_already_ended(self):
        ref = f"{TOKYO}|claude|s-1"
        self.inbox.note_dispatch(ref, "done-1", "serveros", "早就做完了")
        self.inbox.record({"kind": "run.completed", "level": "notify", "device_ref": TOKYO,
                           "session_ref": ref, "run_id": "done-1", "project": "serveros",
                           "occurred_at": "2026-10-29T02:00:00Z", "summary": "done"})
        now = datetime.now(timezone.utc) + timedelta(minutes=5)
        self.assertEqual(self.notifier.announce_dispatches(now), 0)

    def test_a_task_put_in_a_batch_after_its_own_card_says_so_on_that_card(self):
        ref = f"{TOKYO}|claude|s-1"
        self.inbox.note_dispatch(ref, "late-1", "serveros", "先等 10 秒再回答")
        now = datetime.now(timezone.utc) + timedelta(minutes=1)
        self.assertEqual(self.notifier.announce_dispatches(now), 1)
        self.inbox.open_batch("ChatGPT 验收", [{"session_ref": ref, "run_id": "late-1"}],
                              opened_by="gpt")
        self.notifier.pulse(now)
        patched = [card for op, _m, card, _k in self.transport.ops if op == "patch"]
        self.assertTrue(any("已并入批次" in c["header"]["title"]["content"] for c in patched))

    def test_a_connection_that_flaps_reopens_its_own_card_quietly(self):
        def reach(level, at):
            self.inbox.record({"kind": "node.recovered" if level == "OK" else "node.unreachable",
                               "level": "auto" if level == "OK" else "notify", "device_ref": DELL,
                               "occurred_at": at, "summary": "Dell A6000 · 连接：失联",
                               "evidence": f"reach|{level}", "event_id": f"r{level}{at}"})
        reach(ALERT, "2026-10-29T02:00:00Z")
        self.notifier.tick([], THURSDAY_10)
        self.notifier.tick([], THURSDAY_10 + timedelta(minutes=5))
        reach("OK", "2026-10-29T02:10:00Z")
        self.notifier.tick([], THURSDAY_10 + timedelta(minutes=10))
        sends = lambda: [op for op, *_ in self.transport.ops].count("send")
        first = sends()
        reach(ALERT, "2026-10-29T02:40:00Z")
        self.notifier.tick([], THURSDAY_10 + timedelta(minutes=40))
        self.notifier.tick([], THURSDAY_10 + timedelta(minutes=45))
        self.assertEqual(sends(), first, "no second message for the same flapping connection")
        self.assertIn("反复：第 2 次", json.dumps(self.transport.ops[-1][2], ensure_ascii=False))
