"""The owner's whole conversation with a commander (owner, 2026-11-11 «完整记录»)."""
from __future__ import annotations

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

from fleet_hub_watch import commander_log  # noqa: E402
from fleet_hub_watch.journal import Journal  # noqa: E402
from fleet_mcp.inbox import EventInbox  # noqa: E402

NOW = datetime.now(timezone.utc)
STAMP = NOW.strftime("%Y-%m-%dT%H:%M:%S.000Z")
REF = "device:work-fj|claude-vince.huang|s-1"

JSON = {"content": [
    {"type": "user", "timestamp": STAMP, "message": {"role": "user", "content": "看看三台设备的 hostname"}},
    {"type": "assistant", "timestamp": STAMP, "message": {"role": "assistant", "content": [
        {"type": "tool_use", "name": "mcp__serveros-fleet__fleet_send_to_session", "input": {}}]}},
    {"type": "user", "timestamp": STAMP, "message": {"role": "user", "content": [
        {"type": "tool_result", "content": "ok"}]}},
    {"type": "assistant", "timestamp": STAMP, "message": {"role": "assistant", "content": [
        {"type": "text", "text": "已派出，三台都会回 hostname。"}]}},
]}
PLAIN = {"content": (f"[{STAMP}] user: 东京内存怎么样\n"
                     f"[{STAMP}] tool mcp__serveros-fleet__fleet_status({{}})\n"
                     f"[{STAMP}] assistant: 东京内存 52%，正常。")}


class AnAgentCommandersSession(unittest.TestCase):
    def test_both_transcript_shapes_give_the_exchange_without_tool_traffic(self):
        for transcript in (JSON, PLAIN):
            entries = commander_log.exchange(transcript)
            self.assertTrue(commander_log.is_commander(entries))
            talk = [(e["who"], e["text"]) for e in entries if e["who"] != "tool"]
            self.assertEqual([w for w, _ in talk], ["owner", "agent"], transcript)
            self.assertNotIn("ok", [t for _, t in talk], "a tool result is not the owner")

    def test_a_session_that_never_calls_fleet_is_not_a_commander(self):
        entries = commander_log.exchange({"content": f"[{STAMP}] user: hi\n[{STAMP}] assistant: hello"})
        self.assertFalse(commander_log.is_commander(entries))

    def test_the_agent_is_named_by_its_runtime(self):
        self.assertEqual(commander_log.agent_name(REF), "Claude Code")
        self.assertEqual(commander_log.agent_name("device:x|codex|s"), "Codex")

    def test_every_turn_is_written_once_to_the_agent_page(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "clone"
            root.mkdir()
            journal = Journal(EventInbox(Path(raw) / "i.sqlite"), root, push_every=0)
            entries = commander_log.exchange(JSON)
            self.assertEqual(journal.note_agent(REF, "Claude Code（FJPCOS）", entries, NOW), 2)
            self.assertEqual(journal.note_agent(REF, "Claude Code（FJPCOS）", entries, NOW), 0, "once")
            (page,) = list((root / "commander" / "agent").rglob("*.md"))
            text = page.read_text(encoding="utf-8")
            self.assertIn("主人 → Claude Code（FJPCOS）", text)
            self.assertIn("Claude Code（FJPCOS） → 主人", text)
            self.assertIn("看看三台设备的 hostname", text)
            self.assertIn("已派出，三台都会回 hostname。", text)
            self.assertNotIn("fleet_send_to_session", text)


class TheHubFindsAgentCommanders(unittest.TestCase):
    def test_only_active_sessions_that_call_fleet_and_were_not_dispatched_to(self):
        class Entry:
            def __init__(self, ref):
                self.device_ref = type("R", (), {"ref": ref})()
                self.ownership = "company"

        class Service:
            registry = type("Reg", (), {"all": lambda self: [Entry("device:work-fj")]})()
            reads = []

            def list_sessions(self, principal, **kw):
                return {"sessions": [
                    {"session_ref": REF, "running": True},
                    {"session_ref": "device:work-fj|claude|task", "running": True},
                    {"session_ref": "device:work-fj|claude|old", "running": False,
                     "last_activity": {"at": "2026-01-01T00:00:00Z"}},
                    {"session_ref": "device:work-fj|claude|chat", "running": True}]}

            def read_session(self, principal, key, limit):
                self.reads.append(key.ref)
                if key.ref == REF:
                    return {"transcript": JSON}
                return {"transcript": {"content": f"[{STAMP}] user: hi\n[{STAMP}] assistant: hello"}}
        service = Service()
        scan = commander_log.AgentSessions(service, None, names={"device:work-fj": "FJPCOS"})
        found = list(scan.scan(dispatched=lambda ref: ref.endswith("|task")))
        self.assertEqual([(ref, who) for ref, who, _ in found], [(REF, "Claude Code（FJPCOS）")])
        self.assertNotIn("device:work-fj|claude|task", service.reads, "a dispatched task is not read")
        self.assertNotIn("device:work-fj|claude|old", service.reads, "an idle old session is not read")
        service.reads.clear()
        list(scan.scan())
        self.assertNotIn("device:work-fj|claude|chat", service.reads, "a non-commander is not re-read soon")


class AChatCommanderPassesTheOwnersWords(unittest.TestCase):
    def _service(self, tmp, client):
        from fleet_mcp.fleet import FleetService, Principal
        service = object.__new__(FleetService)
        service.inbox = EventInbox(Path(tmp) / "inbox.sqlite")
        service.client_names = {"oauth:c": client} if client else {}
        return service, Principal(name="oauth:c", scopes=frozenset({"fleet:send"}), credential_kind="oauth")

    def test_a_chat_commander_without_them_is_refused(self):
        from fleet_mcp.errors import FleetRefusal
        with tempfile.TemporaryDirectory() as tmp:
            service, chat = self._service(tmp, "ChatGPT")
            with self.assertRaises(FleetRefusal) as caught:
                service._owner_words_required(chat, None)
            self.assertIn("owner_request", str(caught.exception))
            service._owner_words_required(chat, "帮我看看东京")

    def test_an_agent_or_an_unnamed_client_is_not_asked(self):
        with tempfile.TemporaryDirectory() as tmp:
            for client in ("Codex", "Claude Code", None):
                service, who = self._service(tmp, client)
                service._owner_words_required(who, None)

    def test_the_owners_words_are_kept_with_the_run(self):
        from fleet_mcp.identity import SessionKey
        with tempfile.TemporaryDirectory() as tmp:
            service, chat = self._service(tmp, "ChatGPT")
            service._note_dispatch(SessionKey.parse(REF), "r1", None, "只读查 hostname", actor="oauth:c",
                                   owner_request="三台的 hostname 是什么", client="ChatGPT")
            steps = service.inbox.read_trace(run_id="r1")
            said = {(s["step"], (s.get("detail") or {}).get("client")) for s in steps}
            self.assertIn(("owner request", "ChatGPT"), said)


if __name__ == "__main__":
    unittest.main()
