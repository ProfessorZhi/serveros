"""A run's reply is the answer to ITS prompt, not the session's last words.

Owner, 2026-11-10/11: a task sent into a session the owner was also talking in
was recorded as «completed» — on the card and in the journal — with the owner's
other conversation as the device's reply.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for extra in (ROOT, ROOT / "integrations" / "fleet-next-agent-monitor",
              ROOT / "integrations" / "fleet-hub-watch"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from fleet_hub_watch import watch  # noqa: E402

PROMPT = "做一次只读 journal 验收探针：获取本机 hostname 和当前系统 RAM 使用率。只回复两项。"
OURS = "hostname: FJPCOS 当前系统内存使用率为 91%"
THEIRS = "## 1) chart refused; sent without it — 没有，但这条不算通过"


def user(text):
    return {"type": "user", "message": {"role": "user", "content": text}}


def assistant(text):
    return {"type": "assistant", "message": {"role": "assistant",
                                             "content": [{"type": "text", "text": text}]}}


def tool_result(text):
    return {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "content": [{"type": "text", "text": text}]}]}}


class Session:
    """Answers `read_session(limit=n)` with the last n entries."""

    def __init__(self, entries):
        self.entries, self.limits = entries, []

    def read_session(self, principal, key, limit=100):
        self.limits.append(limit)
        return {"transcript": self.entries[-limit:]}


class WhoseAnswerIsIt(unittest.TestCase):
    def test_the_answer_after_the_prompt_is_this_runs(self):
        rows = [user("别的事"), assistant("好的"), user(PROMPT), tool_result("FJPCOS"), assistant(OURS)]
        self.assertEqual(watch.reply_to(rows, PROMPT, OURS), ("FOUND", OURS))

    def test_a_tool_result_is_not_somebodys_message(self):
        rows = [user(PROMPT), assistant("我先查一下"), tool_result("16 GiB"), assistant(OURS)]
        self.assertEqual(watch.reply_to(rows, PROMPT, OURS), ("FOUND", OURS))

    def test_the_session_moved_on_and_this_runs_answer_is_still_found(self):
        rows = [user(PROMPT), assistant(OURS), user("飞书那张卡为什么没有图"), assistant(THEIRS)]
        self.assertEqual(watch.reply_to(rows, PROMPT, THEIRS), ("FOUND", OURS))

    def test_the_last_words_answer_someone_else(self):
        rows = [user("飞书那张卡为什么没有图"), assistant(THEIRS)]
        self.assertEqual(watch.reply_to(rows, PROMPT, THEIRS), ("OTHER", None))

    def test_asked_and_not_answered_yet(self):
        rows = [user("别的事"), assistant(THEIRS), user(PROMPT)]
        self.assertEqual(watch.reply_to(rows, PROMPT, THEIRS), ("PENDING", None))

    def test_a_window_with_no_message_in_it_cannot_tell(self):
        self.assertEqual(watch.reply_to([assistant(OURS)], PROMPT, OURS), ("UNSEEN", None))

    def test_a_plain_text_transcript_is_read_the_same_way(self):
        text = (f"[2026-11-10 19:42] user: 飞书那张卡为什么没有图\n[2026-11-10 19:43] assistant: {THEIRS}\n"
                f"[2026-11-10 19:44] user: {PROMPT}\n[2026-11-10 19:45] tool Bash({{}})\n"
                f"[2026-11-10 19:46] assistant: {OURS}\n")
        self.assertEqual(watch.reply_to(text, PROMPT, OURS), ("FOUND", OURS))


class ReadingTheSession(unittest.TestCase):
    def test_the_owners_other_conversation_is_not_this_runs_reply(self):
        """The journal's 「journal 验收 11-10」 case: FJPCOS."""
        session = Session([user("飞书那张卡为什么没有图"), tool_result("…"), assistant(THEIRS)])
        self.assertIsNone(watch.read_reply(session, None, None, PROMPT))
        self.assertEqual(watch.read_reply(session, None, None), THEIRS, "without a prompt: as before")

    def test_this_runs_reply_is_read_even_after_the_session_moved_on(self):
        session = Session([user(PROMPT), assistant(OURS), user("飞书那张卡为什么没有图"),
                           assistant(THEIRS)])
        self.assertEqual(watch.read_reply(session, None, None, PROMPT), OURS)

    def test_a_reader_that_only_ever_shows_the_last_entry_keeps_working(self):
        """A provider that caps the read: nothing shows whose answer it is, so
        the last answer is used, as it was before."""
        class Capped(Session):
            def read_session(self, principal, key, limit=100):
                return {"transcript": self.entries[-1:]}
        session = Capped([user(PROMPT), assistant(OURS)])
        self.assertEqual(watch.read_reply(session, None, None, PROMPT), OURS)

    def test_the_usual_case_costs_no_extra_read(self):
        session = Session([user(PROMPT), assistant(OURS)])
        self.assertEqual(watch.read_reply(session, None, None, PROMPT), OURS)
        self.assertLessEqual(len(session.limits), 2)

    def test_the_prompt_comes_from_the_trace(self):
        class Inbox:
            def read_trace(self, run_id=None, limit=5000):
                return [{"step": "dispatched (send)", "run_id": "r-1"},
                        {"step": "prompt sent", "run_id": "r-1", "detail": {"text": PROMPT}},
                        {"step": "prompt sent", "run_id": "r-2", "detail": {"text": "别的"}}]
        self.assertEqual(watch.recorded_prompt(Inbox(), "r-1"), PROMPT)
        self.assertIsNone(watch.recorded_prompt(Inbox(), "r-9"))
        self.assertIsNone(watch.recorded_prompt(object(), "r-1"))


if __name__ == "__main__":
    unittest.main()


class TheCommanderRecordsWhatItToldTheOwner(unittest.TestCase):
    """`fleet_batch action=reply`: GPT's side of the conversation, into the trace."""

    def _service(self, tmp):
        from fleet_mcp.fleet import FleetService, Principal
        from fleet_mcp.inbox import EventInbox
        service = object.__new__(FleetService)
        service.inbox = EventInbox(Path(tmp) / "inbox.sqlite")
        service.admit = lambda principal: None
        service.audit = type("Audit", (), {"record": lambda self, *a, **k: None})()
        reader = Principal(name="gpt", scopes=frozenset({"fleet:read"}), credential_kind="oauth")
        return service, reader

    def test_a_reply_about_a_batch_and_one_about_anything_else(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            service, gpt = self._service(tmp)
            batch = service.inbox.open_batch("体检", [{"session_ref": "device:x|claude|s", "run_id": "r1"}],
                                             opened_by="gpt")
            got = service.batch(gpt, action="reply", batch_id=batch["batch_id"], reply="已派出 1 个任务。")
            self.assertTrue(got["recorded"])
            service.batch(gpt, action="reply", owner_request="几台设备？", reply="三台。")
            steps = service.inbox.read_trace(minutes=60)
            said = [(s["step"], s.get("batch_id"), s["detail"].get("text")) for s in steps
                    if s["source"] == "commander"]
            self.assertIn(("reply to owner", batch["batch_id"], "已派出 1 个任务。"), said)
            self.assertIn(("owner request", None, "几台设备？"), said)
            self.assertIn(("reply to owner", None, "三台。"), said)

    def test_a_reply_needs_words_and_a_real_batch(self):
        import tempfile
        from fleet_mcp.errors import FleetRefusal
        with tempfile.TemporaryDirectory() as tmp:
            service, gpt = self._service(tmp)
            with self.assertRaises(FleetRefusal):
                service.batch(gpt, action="reply", reply="  ")
            with self.assertRaises(FleetRefusal):
                service.batch(gpt, action="reply", batch_id="nope", reply="x")

    def test_the_tool_offers_it(self):
        from fleet_mcp import tools
        tool = {t.name: t for t in tools.TOOLS}["fleet_batch"]
        self.assertIn("reply", tool.input_schema["properties"]["action"]["enum"])
        self.assertIn("reply", tool.input_schema["properties"])
