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
        card = self.answerer.answer("status", "", NOW)
        self.assertEqual(card["header"]["title"]["content"], "Fleet 状态 · 1 台需要注意")
        self.assertIn("**Tokyo**", self.body(card))
        self.assertIn("Swap 99%（水位）（注意）", self.body(card))

    def test_tasks_and_a_batch_by_name(self):
        ref = f"{TOKYO}|claude|s-1"
        self.inbox.note_dispatch(ref, "r1", label="只读：hostname 和 uptime")
        self.inbox.open_batch("验收 1.12", [{"session_ref": ref, "run_id": "r1"}], opened_by="gpt")
        self.assertIn("验收 1.12", self.body(self.answerer.answer("tasks", "", NOW)))
        card = self.answerer.answer("batch", "验收", NOW)
        self.assertIn("只读：hostname 和 uptime", self.body(card))
        self.assertEqual(card["header"]["title"]["content"], "验收 1.12 · 进行中 0/1")

    def test_resource_falls_back_to_the_plain_card_and_asks_which_device(self):
        self.assertIn("Tokyo", self.body(self.answerer.answer("resource", "东京", NOW)))
        self.assertEqual(self.answerer.answer("resource", "", NOW)["header"]["title"]["content"],
                         "哪台设备？")

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
