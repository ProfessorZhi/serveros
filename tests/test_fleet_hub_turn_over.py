"""When is a dispatched run over, and what did it answer (owner, 2026-11-12:
the 目录盘点 batch called A6000 finished on its opening plan, and showed
Tokyo as 没返回文字 while its reply was kept)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for extra in (ROOT, ROOT / "integrations" / "fleet-hub-watch",
              ROOT / "integrations" / "fleet-next-agent-monitor"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from fleet_hub_watch import notify  # noqa: E402
from fleet_hub_watch.watch import ends_with_answer  # noqa: E402

PLAN = "我先做只读元数据采集：顶层结构、项目根目录、磁盘占用与最近活动。"


def assistant(*blocks):
    return {"type": "assistant", "message": {"role": "assistant", "content": list(blocks)}}


class TheTurnIsOverOnlyWhenTheLastEntryIsTheAnswer(unittest.TestCase):
    def test_a_plan_before_a_command_is_not_the_end(self):
        self.assertFalse(ends_with_answer([assistant({"type": "text", "text": PLAN},
                                                     {"type": "tool_use", "name": "Bash"})]))
        self.assertFalse(ends_with_answer([assistant({"type": "text", "text": PLAN}),
                                           assistant({"type": "tool_use", "name": "Bash"})]))
        self.assertFalse(ends_with_answer([
            assistant({"type": "text", "text": PLAN}),
            {"type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "content": "ok"}]}}]))

    def test_the_answer_last_is_the_end(self):
        self.assertTrue(ends_with_answer([assistant({"type": "tool_use", "name": "Bash"}),
                                          assistant({"type": "text", "text": "结论：规整。"}),
                                          {"type": "summary", "summary": "x"}]))

    def test_plain_text_transcripts(self):
        self.assertTrue(ends_with_answer("[10:00] tool: Bash ls\n[10:01] assistant: 结论：混乱。"))
        self.assertFalse(ends_with_answer("[10:00] assistant: 我先看看。\n[10:01] tool: Bash ls"))


class TheKeptReplyComesFirst(unittest.TestCase):
    def test_an_unreadable_run_receipt_does_not_hide_the_kept_reply(self):
        class Service:
            def get_run(self, *a, **k):
                raise RuntimeError("BAD_REQUEST")

        class Inbox:
            def read_trace(self, run_id=None, limit=0):
                return [{"step": "result", "run_id": run_id, "detail": {"text": "结论先行：是混乱的。"}}]
        read = notify.run_excerpt(Service(), None, 200, Inbox())
        self.assertEqual(read("device:tokyo-shared-dev-01|claude|s1", "r1"), "结论先行：是混乱的。")


if __name__ == "__main__":
    unittest.main()
