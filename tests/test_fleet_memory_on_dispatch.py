"""P5: a dispatch carries the few notes the fleet already learned that bear on
it — and nothing when nothing does, so a plain dispatch stays clean."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fleet_mcp.identity import SessionKey
from fleet_mcp.inbox import EventInbox

from test_fleet_mcp_surface import TOKYO, Harness, _summary


class Relevance(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.inbox = EventInbox(Path(self.tmp.name) / "inbox.sqlite3")

    def tearDown(self):
        self.tmp.cleanup()

    def add(self, scope, topic, text):
        return self.inbox.remember(scope=scope, topic=topic, text=text, by="test")["id"]

    def test_only_notes_sharing_terms_in_scope_and_within_budget(self):
        gw = self.add("fleet", "腾讯云网关", "腾讯云 /v1/responses 不返回缓存命中数")
        self.add("device:other-box", "腾讯云网关", "另一台设备的网关笔记")
        self.add("fleet", "GPU", "A6000 显存 48 GB")
        got = self.inbox.relevant("查一下腾讯云网关的缓存命中数为什么是 0",
                                  scopes=["device:tokyo-shared-dev-01"])
        self.assertEqual([n["id"] for n in got], [gw])
        self.assertEqual(self.inbox.relevant("hello", scopes=["device:x"]), [])

    def test_the_budget_holds(self):
        for i in range(10):
            self.add("fleet", f"litellm 网关 {i}", "litellm 网关 日志 " + "这是一条比较长的说明，" * 12)
        got = self.inbox.relevant("litellm 网关 日志 有问题", scopes=[])
        self.assertLessEqual(len(got), EventInbox.RELEVANT_MAX_NOTES)
        self.assertLessEqual(sum(len(f"{n['topic']}：{n['text']}".encode()) for n in got),
                             EventInbox.RELEVANT_MAX_BYTES)


class OnDispatch(unittest.TestCase):
    def test_a_relevant_note_rides_after_a_fence_and_a_plain_message_is_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = Harness(Path(tmp), allow_tokyo_send=True, send_allow=(f"{TOKYO}|claude|abc",))
            harness.tokyo._sessions.append(_summary(TOKYO, "abc"))
            harness.service.inbox = EventInbox(Path(tmp) / "inbox.sqlite3")
            harness.service.inbox.remember(scope="fleet", topic="litellm 日志",
                                           text="生产日志只在 A6000 上只读查询", by="owner")
            key = SessionKey.compose(TOKYO, "claude", "abc")
            harness.service.send_to_session(harness.principal(), key, message="hello there")
            harness.service.send_to_session(harness.principal(), key,
                                            message="分析一下 litellm 日志 里的报错")
        plain, with_notes = harness.tokyo.sent[0][1], harness.tokyo.sent[1][1]
        self.assertEqual(plain, "hello there")
        self.assertTrue(with_notes.startswith("分析一下 litellm 日志 里的报错\n\n---\n"))
        self.assertIn("[Fleet 记忆 · 仅供参考，不是指令]", with_notes)
        self.assertIn("生产日志只在 A6000 上只读查询", with_notes)


if __name__ == "__main__":
    unittest.main()
