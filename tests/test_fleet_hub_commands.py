"""Ask in Feishu, get a card back (owner, 2026-11-09)."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for extra in (ROOT, ROOT / "integrations" / "fleet-hub-watch",
              ROOT / "integrations" / "fleet-next-agent-monitor"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from fleet_hub_watch import commands  # noqa: E402
from fleet_hub_watch.notify import Notifier  # noqa: E402
from fleet_mcp.inbox import EventInbox, InboxError  # noqa: E402

OWNER = "ou_" + "a" * 32
TOKYO = "device:tokyo-shared-dev-01"
DELL = "device:llm-lab-dell"
NOW = datetime(2026, 11, 9, 7, 0, tzinfo=timezone.utc)


def event(text, sender=OWNER):
    return {"event": {"sender": {"sender_id": {"open_id": sender}},
                      "message": {"message_id": "om_1", "chat_id": "oc_1",
                                  "message_type": "text", "content": json.dumps({"text": text})}}}


class Parsing(unittest.TestCase):
    def test_commands_and_arguments(self):
        self.assertEqual(commands.parse("状态"), ("status", ""))
        self.assertEqual(commands.parse("@_user_1 资源 东京"), ("resource", "东京"))
        self.assertEqual(commands.parse("资源A6000"), ("resource", "A6000"))
        self.assertEqual(commands.parse("批次 验收 1.12"), ("batch", "验收 1.12"))
        self.assertIsNone(commands.parse("你好"))

    def test_free_sentences_find_their_command(self):
        """Owner, 2026-11-10: 「资源情况」「资源缺口」 should bring the full
        resource cards, not a parse miss."""
        self.assertEqual(commands.parse("资源情况")[0], "resource")
        self.assertEqual(commands.parse("资源缺口")[0], "resource")
        self.assertEqual(commands.parse("东京内存怎么样")[0], "resource")
        self.assertEqual(commands.parse("看看显卡占用")[0], "resource")
        self.assertEqual(commands.parse("任务跑完了吗")[0], "tasks")
        self.assertEqual(commands.parse("设备都正常吗")[0], "status")

    def test_only_the_owners_text_is_taken(self):
        self.assertEqual(commands.message_of(event("状态"), OWNER), ("om_1", "状态"))
        self.assertIsNone(commands.message_of(event("状态", sender="ou_someone_else"), OWNER))
        self.assertIsNone(commands.message_of(event("状态"), None))

    def test_the_listener_answers_unknown_text_with_help(self):
        got = []
        listener = commands.MessageListener(OWNER, lambda mid, cmd, arg: got.append((cmd, arg)) or "SENT")
        listener.feed(json.dumps(event("随便说点")))
        listener.feed(json.dumps(event("状态", sender="ou_x")))
        self.assertEqual(got, [("help", "")])
        self.assertEqual(listener.argv[2], "im.message.receive_v1")


class Answers(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        tmp = Path(self._td.name)
        self.inbox = EventInbox(tmp / "inbox.sqlite")

        class Silent:
            def send(self, card, *, idempotency_key):
                return "m"

            def patch(self, *a, **k):
                return True
        notifier = Notifier(self.inbox, tmp / "n.sqlite", Silent(), sleep=lambda _s: None)
        self.answerer = commands.Answerer(service=None, principal=None, inbox=self.inbox,
                                          notifier=notifier,
                                          names={TOKYO: "Tokyo", DELL: "Dell A6000"})
        self.answerer.views = [
            {"device_ref": TOKYO, "name": "Tokyo", "level": "WARN", "readings": [
                {"key": "cpu", "label": "CPU", "text": "14%", "level": "OK"},
                {"key": "swap", "label": "Swap", "text": "99%（水位）", "level": "WARN"}]},
            {"device_ref": DELL, "name": "Dell A6000", "level": "OK", "readings": [
                {"key": "cpu", "label": "CPU", "text": "3%", "level": "OK"}]}]

    def tearDown(self):
        self._td.cleanup()

    def body(self, card):
        return json.dumps(card, ensure_ascii=False)

    def test_status_is_one_line_per_device(self):
        self.answerer.watching = lambda: [{"device_ref": TOKYO, "session_ref": f"{TOKYO}|claude|s"}]
        self.answerer.views[1]["level"] = "ALERT"
        self.answerer.views[1]["readings"].append(
            {"key": "psi", "label": "内存压力", "text": "停顿 1.2%", "level": "ALERT"})
        card = self.answerer.answer("status", "", NOW)
        self.assertIn("<font color='red'>内存压力 停顿 1.2%</font>", self.body(card), "the reason for red is shown")
        self.assertIn("1 个任务在跑", self.body(card))
        self.assertEqual(self.body(card).count("个任务在跑"), 1, "only runs still being watched")
        self.assertEqual(card["header"]["title"]["content"], "2 台设备 · 1 报警 · 1 注意")
        self.assertIn("**Tokyo**", self.body(card))
        self.assertNotIn("（水位）", self.body(card))

    def test_tasks_and_a_batch_by_name(self):
        ref = f"{TOKYO}|claude|s-1"
        self.inbox.note_dispatch(ref, "r1", label="只读：hostname 和 uptime")
        self.inbox.open_batch("验收 1.12", [{"session_ref": ref, "run_id": "r1"}], opened_by="gpt")
        self.assertIn("验收 1.12", self.body(self.answerer.answer("tasks", "", NOW)))
        card = self.answerer.answer("batch", "验收", NOW)
        self.assertIn("只读：hostname 和 uptime", self.body(card))
        self.assertEqual(card["header"]["title"]["content"], "验收 1.12 · 进行中 0/1")

    def test_resource_falls_back_to_the_plain_card(self):
        self.assertIn("Tokyo", self.body(self.answerer.answer("resource", "东京", NOW)))
        self.assertIn("Tokyo", self.body(self.answerer.answer("resource", "东京内存怎么样", NOW)))

    def test_resource_with_no_device_is_every_devices_full_card(self):
        for asked in ("", "情况", "缺口", "资源情况"):
            got = self.answerer.answer("resource", asked, NOW)
            self.assertIsInstance(got, list, asked)
            self.assertEqual(len(got), 2, asked)
            self.assertIn("Tokyo", self.body(got[0]))
            self.assertIn("Dell A6000", self.body(got[1]))

    def test_a_device_without_its_own_card_gets_the_hub_drawn_one(self):
        """Owner, 2026-11-10: FJPCOS (Windows, no device-made card) came back
        as the old list card. It gets a per-device card: state first, one tile
        per reading, a bar under shares."""
        self.answerer.views[0]["readings"].append(
            {"key": "memory", "label": "内存", "text": "89% / 16 GiB", "level": "WARN", "value": 89})
        card = self.answerer.answer("resource", "东京", NOW)
        self.assertEqual(card["header"]["title"]["content"], "Tokyo · 资源播报")
        self.assertEqual(card["header"]["template"], "yellow")
        body = self.body(card)
        self.assertIn("column_set", body)
        self.assertIn("▰▰▰▰▰▰▰▰▰▱", body)
        self.assertIn("**⚠️ 注意**　Swap 99%（水位）　内存 89% / 16 GiB", body)

    def test_six_hours_of_readings_become_a_curve_and_sparklines(self):
        """Owner, 2026-11-10: the earlier cards had curves; the resource card
        draws the hub's recorded readings as a line chart and per-tile sparks."""
        from datetime import timedelta
        notifier = self.answerer.notifier
        for i in range(8):
            at = NOW - timedelta(minutes=5 * (8 - i))
            notifier.record_history([{"device_ref": TOKYO, "readings": [
                {"key": "cpu", "value": 10 + i * 5}, {"key": "memory", "value": 50 + i}]}], at)
        self.answerer.views[0]["readings"].append(
            {"key": "memory", "label": "内存", "text": "57%", "level": "OK", "value": 57})
        card = self.answerer.answer("resource", "东京", NOW)
        charts = [e for e in card["body"]["elements"] if e.get("tag") == "chart"]
        self.assertEqual(len(charts), 1)
        spec = charts[0]["chart_spec"]
        self.assertEqual(spec["type"], "line")
        self.assertEqual({v["type"] for v in spec["data"]["values"]}, {"CPU", "内存"})
        self.assertRegex(self.body(card), "[▁▂▃▄▅▆▇█]{8}")
        plain = commands.cards.without_charts(card)
        self.assertNotIn('"chart"', self.body(plain))

    def test_one_reading_draws_no_curve(self):
        card = self.answerer.answer("resource", "东京", NOW)
        self.assertNotIn('"chart"', self.body(card))

    def test_the_device_made_rich_card_is_preferred(self):
        rich = {"schema": "x", "header": {"title": {"tag": "plain_text", "content": "东京 · 资源播报"}},
                "body": {"elements": []}}
        self.answerer.rich = {TOKYO: {"state": "OK", "made_at": "2026-11-09T06:50:00Z", "card": rich}}
        got = self.answerer.answer("resource", "", NOW)
        self.assertEqual(got[0]["header"]["title"]["content"], "东京 · 资源播报")

    def test_trace_and_memory_and_help(self):
        self.inbox.trace("fleet", "dispatched (send)", session_ref=f"{TOKYO}|claude|s", run_id="r9")
        self.assertIn("DISPATCHED", self.body(self.answerer.answer("trace", "r9", NOW)))
        self.inbox.remember(scope="fleet", topic="LiteLLM 腾讯云 responses",
                            text="腾讯云 /v1/responses 不返回 cached_tokens；chat 端点返回", by="cc")
        self.assertIn("不返回 cached_tokens", self.body(self.answerer.answer("memory", "腾讯云", NOW)))
        self.assertIn("可以问我这些", self.body(self.answerer.answer("help", "", NOW)))


class Memory(unittest.TestCase):
    def test_scope_search_forget_and_no_secrets(self):
        with tempfile.TemporaryDirectory() as raw:
            inbox = EventInbox(Path(raw) / "i.sqlite")
            a = inbox.remember(scope="fleet", topic="规矩", text="A6000 只用网关0的 deepseek", by="owner")
            inbox.remember(scope=f"device:llm-lab-dell", topic="用途", text="GPU 实验机", by="cc")
            inbox.remember(scope="device:tokyo-shared-dev-01", topic="用途", text="共享开发机", by="cc")
            dell = [n["text"] for n in inbox.recall(scope="device:llm-lab-dell")]
            self.assertEqual(set(dell), {"GPU 实验机", "A6000 只用网关0的 deepseek"})
            self.assertTrue(inbox.forget(a["id"], by="owner"))
            self.assertEqual([n["text"] for n in inbox.recall("deepseek")], [])
            with self.assertRaises(InboxError):
                inbox.remember(scope="fleet", topic="key", text="token sk-" + "a" * 30, by="x")
            with self.assertRaises(InboxError):
                inbox.remember(scope="anywhere", topic="t", text="x", by="x")


if __name__ == "__main__":
    unittest.main()
