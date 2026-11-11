"""The fleet journal: finished batches and tasks as files in a private repo (owner, 2026-11-10)."""
from __future__ import annotations

import subprocess
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

from fleet_hub_watch.journal import SUBSCRIBER, Journal  # noqa: E402
from fleet_mcp.inbox import EventInbox  # noqa: E402

TOKYO = "device:tokyo-shared-dev-01"
DELL = "device:llm-lab-dell"
NOW = datetime.now(timezone.utc)
KEYISH = "sk-" + "A1b2C3d4" * 4


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)


class TheJournal(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        tmp = Path(self._td.name)
        self.inbox = EventInbox(tmp / "inbox.sqlite")
        remote = tmp / "remote.git"
        git(tmp, "init", "-q", "--bare", str(remote))
        self.clone = tmp / "clone"
        git(tmp, "clone", "-q", str(remote), str(self.clone))
        git(self.clone, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q",
            "--allow-empty", "-m", "start")
        git(self.clone, "push", "-q", "-u", "origin", "HEAD")
        self.remote = remote
        self.replies = {"r1": "结论：东京正常\n第二行", "r2": f"A6000 正常，token {KEYISH}"}
        self.journal = Journal(self.inbox, self.clone,
                               reply=lambda ref, rid: self.replies.get(rid),
                               names={TOKYO: "Tokyo", DELL: "Dell A6000"}, push_every=0)

    def tearDown(self):
        self._td.cleanup()

    def finish(self, ref, run_id):
        self.inbox.record({"kind": "run.completed", "level": "notify",
                           "device_ref": ref.split("|")[0], "session_ref": ref, "run_id": run_id,
                           "occurred_at": NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
                           "summary": f"run {run_id} completed"})

    def test_a_batch_becomes_one_file_with_the_exchange_and_is_pushed(self):
        a, b = f"{TOKYO}|claude|s-1", f"{DELL}|claude|s-2"
        self.inbox.note_dispatch(a, "r1", label="看东京")
        self.inbox.note_dispatch(b, "r2", label="看 A6000")
        batch = self.inbox.open_batch("两台体检", [{"session_ref": a, "run_id": "r1"},
                                                  {"session_ref": b, "run_id": "r2"}],
                                      opened_by="gpt")
        self.inbox.trace("commander", "owner request", batch_id=batch["batch_id"],
                         text="帮我看看东京和 A6000 是否正常", skills="fleet-commander-orchestration")
        self.inbox.trace("fleet", "prompt sent", session_ref=a, run_id="r1", text="只读检查东京")
        self.inbox.trace("device", "device finished", session_ref=a, run_id="r1",
                         turns=3, tools="Bash×2", skills="fleet-self-check")
        self.finish(a, "r1")
        self.finish(b, "r2")
        got = self.journal.tick(NOW)
        self.assertEqual(got, {"written": 1, "pushed": True, "commander": 1},
                         "the batch file, and the owner's words on the commander page")
        (path,) = list((self.clone / "runs").rglob("*-两台体检-*.md"))
        text = path.read_text(encoding="utf-8")
        self.assertIn("# 两台体检 · 全部完成", text)
        self.assertIn("帮我看看东京和 A6000 是否正常", text)
        self.assertIn("指挥侧用的 skill：fleet-commander-orchestration", text)
        self.assertIn("只读检查东京", text)
        self.assertIn("结论：东京正常\n第二行", text, "the final answer in full")
        self.assertIn("turns 3 · tools Bash×2 · skills fleet-self-check", text)
        self.assertIn(f"会话 `{a}`", text, "the session where the work itself stays")
        self.assertNotIn(KEYISH, text)
        self.assertIn("‹已遮蔽›", text)
        self.assertIn("两台体检", (path.parent / "INDEX.md").read_text(encoding="utf-8"))
        log = git(self.remote, "log", "--oneline").stdout
        self.assertRegex(log, r"fleet: \d+ 条记录")
        self.assertEqual(self.journal.tick(NOW), {"written": 0, "pushed": False}, "each once")

    def test_a_stand_alone_task_is_its_own_file_and_undispatched_turns_are_not(self):
        a = f"{TOKYO}|claude|s-1"
        self.inbox.note_dispatch(a, "r1", label="只输出 OK")
        self.finish(a, "r1")
        self.finish(f"{DELL}|claude|s-9", "own-turn")
        self.assertEqual(self.journal.tick(NOW)["written"], 1)
        files = [p for p in (self.clone / "runs").rglob("*.md") if p.name != "INDEX.md"]
        self.assertEqual(len(files), 1)
        self.assertIn("# 只输出 OK · ✅ 完成", files[0].read_text(encoding="utf-8"))
        self.assertTrue((self.clone / "README.md").exists())

    def test_a_failed_push_is_kept_and_retried(self):
        a = f"{TOKYO}|claude|s-1"
        self.inbox.note_dispatch(a, "r1", label="只输出 OK")
        self.finish(a, "r1")
        good = self.journal._git
        self.journal._git = lambda *args: (subprocess.CompletedProcess(args, 1, "", "")
                                           if args[0] == "push" else good(*args))
        self.assertEqual(self.journal.tick(NOW), {"written": 1, "pushed": False})
        self.journal._git = good
        self.assertTrue(self.journal.flush(force=True))
        self.assertIn("fleet:", git(self.remote, "log", "--oneline").stdout)

    def test_the_first_push_to_an_empty_repository_creates_main(self):
        """Owner, 2026-11-10: fleet-journal was created empty; a clone of it has
        no upstream, so the first push has to create `main`."""
        tmp = Path(self._td.name)
        empty = tmp / "empty.git"
        git(tmp, "init", "-q", "--bare", str(empty))
        clone = tmp / "empty-clone"
        git(tmp, "clone", "-q", str(empty), str(clone))
        journal = Journal(self.inbox, clone, reply=lambda ref, rid: "OK", push_every=0)
        a = f"{TOKYO}|claude|s-1"
        self.inbox.note_dispatch(a, "r1", label="只输出 OK")
        self.finish(a, "r1")
        self.assertEqual(journal.tick(NOW), {"written": 1, "pushed": True})
        self.assertIn("main", git(empty, "branch", "--list").stdout)
        self.assertIn("fleet:", git(empty, "log", "--oneline", "main").stdout)
        self.inbox.note_dispatch(a, "r2", label="再来一次")
        self.finish(a, "r2")
        self.assertEqual(journal.tick(NOW), {"written": 1, "pushed": True}, "then tracks main")

    def test_the_answer_is_this_runs_even_when_the_session_moved_on(self):
        """Owner, 2026-11-10: a back-filled batch showed A6000 answering 「OK」 —
        the session's latest reply, from a later task — instead of its hostname."""
        a = f"{DELL}|claude|s-2"
        self.inbox.note_dispatch(a, "r1", label="只输出 hostname")
        self.inbox.trace("device", "result", session_ref=a, run_id="r1", text="FJ-NJ-SER-1009")
        self.finish(a, "r1")
        self.replies["r1"] = "OK"                    # what the session says now
        self.journal.tick(NOW)
        (path,) = [p for p in (self.clone / "runs").rglob("*.md") if p.name != "INDEX.md"]
        text = path.read_text(encoding="utf-8")
        self.assertIn("FJ-NJ-SER-1009", text)
        self.assertNotIn("```text\nOK\n```", text)

    def test_a_long_run_keeps_its_prompt_and_folds_repeated_observations(self):
        a = f"{TOKYO}|claude|s-1"
        self.inbox.note_dispatch(a, "r1", label="长任务")
        self.inbox.trace("fleet", "prompt sent", session_ref=a, run_id="r1", text="请做一件很长的事")
        for _ in range(400):
            self.inbox.trace("monitor", "observed", session_ref=a, run_id="r1",
                             status="RUNNING", age_s=1)
        self.finish(a, "r1")
        self.journal.tick(NOW)
        (path,) = [p for p in (self.clone / "runs").rglob("*.md") if p.name != "INDEX.md"]
        text = path.read_text(encoding="utf-8")
        self.assertIn("请做一件很长的事", text)
        self.assertIn("…同样的观测又 399 次", text)
        self.assertIn("北京时间", text)

    def test_nothing_written_means_no_push_attempt(self):
        tmp = Path(self._td.name)
        empty = tmp / "e.git"
        git(tmp, "init", "-q", "--bare", str(empty))
        clone = tmp / "e-clone"
        git(tmp, "clone", "-q", str(empty), str(clone))
        calls = []
        journal = Journal(self.inbox, clone, push_every=0)
        real = journal._git
        journal._git = lambda *args: (calls.append(args[0]), real(*args))[1]
        self.assertEqual(journal.tick(NOW), {"written": 0, "pushed": False})
        self.assertNotIn("push", calls)

    def test_the_batch_card_excerpt_is_this_runs_reply(self):
        """Owner, 2026-11-10: the batch card showed FJPCOS «answering» with the
        owner's later conversation in the same session."""
        from fleet_hub_watch.notify import run_excerpt
        a = f"{TOKYO}|claude|s-1"
        self.inbox.trace("device", "result", session_ref=a, run_id="r1", text="hostname: VM-16-5")

        class Service:
            def get_run(self, principal, key, run_id):
                return {"status": "completed"}

            def read_session(self, principal, key, limit):
                return {"transcript": {"messages": [{"role": "assistant",
                                                      "content": "1) chart refused — 没有"}]}}
        read = run_excerpt(Service(), None, 200, self.inbox)
        self.assertEqual(read(a, "r1"), "hostname: VM-16-5")
        self.assertEqual(read(a, "r-unkept"), "1) chart refused — 没有", "the session only as fallback")

    def test_the_cursor_is_its_own(self):
        a = f"{TOKYO}|claude|s-1"
        self.inbox.note_dispatch(a, "r1", label="x")
        self.finish(a, "r1")
        self.journal.tick(NOW)
        self.assertGreater(self.inbox.cursor(SUBSCRIBER), 0)
        self.assertEqual(self.inbox.cursor("hub-watch-notify"), 0)


if __name__ == "__main__":
    unittest.main()


class BothSidesOfEveryConversationAreKept(TheJournal):
    test_a_batch_becomes_one_file_with_the_exchange_and_is_pushed = None
    """Owner, 2026-11-11: what GPT told the owner was nowhere; what the cards
    said was not kept; and a device's reply was the session's other talk."""

    def test_the_commanders_reply_about_a_batch_is_on_its_page_and_in_the_batch_file(self):
        a = f"{TOKYO}|claude|s-1"
        self.inbox.note_dispatch(a, "r1", label="看东京")
        batch = self.inbox.open_batch("一台体检", [{"session_ref": a, "run_id": "r1"}], opened_by="gpt")
        self.inbox.trace("commander", "owner request", batch_id=batch["batch_id"], text="东京还好吗",
                         by="oauth:c1", client="ChatGPT")
        self.finish(a, "r1")
        self.journal.tick(NOW)
        self.inbox.trace("commander", "reply to owner", batch_id=batch["batch_id"],
                         text=f"东京正常，CPU 11%。token {KEYISH}", by="oauth:c1", client="ChatGPT")
        got = self.journal.tick(NOW)
        self.assertEqual(got.get("commander"), 1)
        (page,) = list((self.clone / "commander" / "chat").rglob("*.md"))
        talk = page.read_text(encoding="utf-8")
        self.assertIn("聊天类（ChatGPT、Grok 等）指挥端", talk)
        self.assertIn("主人 → ChatGPT · 批次「一台体检」", talk)
        self.assertIn("ChatGPT → 主人 · 批次「一台体检」", talk)
        self.assertIn("东京正常，CPU 11%", talk)
        self.assertNotIn(KEYISH, talk)
        (path,) = list((self.clone / "runs").rglob("*-一台体检-*.md"))
        text = path.read_text(encoding="utf-8")
        self.assertIn("## 指挥端给主人的回复", text, "the batch file was rewritten with the reply")
        self.assertIn("东京正常，CPU 11%", text)
        self.assertEqual(self.journal.tick(NOW).get("commander"), None, "each line once")
        self.assertFalse(list(self.clone.rglob("fleet-commander.json"))
                         and git(self.clone, "ls-files", "--", "*fleet-commander.json").stdout.strip())

    def test_an_exchange_with_no_batch_is_kept_too(self):
        self.inbox.trace("commander", "owner request", text="Fleet 现在有几台设备")
        self.inbox.trace("commander", "reply to owner", text="三台：东京、Dell、FJPCOS。")
        self.journal.tick(NOW)
        (page,) = list((self.clone / "commander").rglob("*.md"))
        talk = page.read_text(encoding="utf-8")
        self.assertLess(talk.index("Fleet 现在有几台设备"), talk.index("三台：东京、Dell、FJPCOS。"))

    def test_chat_and_agent_commanders_have_their_own_pages(self):
        """Owner, 2026-11-11: commanders are two kinds — chat (GPT, Grok) and
        agent (Claude Code, Codex)."""
        self.inbox.trace("commander", "owner request", text="查一下东京", by="oauth:c1", client="ChatGPT")
        self.inbox.trace("commander", "owner request", text="改一下 hub", by="oauth:c2",
                         client="Claude Code (fleet)")
        self.inbox.trace("commander", "owner request", text="看看 Dell", by="local-owner", client="local")
        self.inbox.trace("commander", "owner request", text="帮我查", by="oauth:c3", client="Grok")
        self.journal.tick(NOW)
        chat = "".join(p.read_text(encoding="utf-8") for p in (self.clone / "commander" / "chat").rglob("*.md"))
        agent = "".join(p.read_text(encoding="utf-8") for p in (self.clone / "commander" / "agent").rglob("*.md"))
        self.assertIn("主人 → ChatGPT", chat)
        self.assertIn("主人 → Grok", chat)
        self.assertIn("主人 → Claude Code (fleet)", agent)
        self.assertIn("本机 agent", agent)
        self.assertNotIn("改一下 hub", chat)

    def test_commander_kind(self):
        kind = Journal.commander_kind
        self.assertEqual(kind("ChatGPT")[0], "chat")
        self.assertEqual(kind("Codex")[0], "agent")
        self.assertEqual(kind("claude-code")[0], "agent")
        self.assertEqual(kind(None, "local-owner")[0], "agent")
        self.assertEqual(kind("Mystery")[0], "other")

    def test_every_card_sent_is_kept_as_text(self):
        from fleet_hub_watch import cards
        from fleet_hub_watch.notify import ArchivingTransport

        class Feishu:
            def __init__(self):
                self.sent = 0

            def send(self, card, *, idempotency_key):
                self.sent += 1
                return f"om_{self.sent}" if idempotency_key != "refused" else None

            def patch(self, message_id, card, *, sent_at):
                return True
        feishu = Feishu()
        transport = ArchivingTransport(feishu, self.journal.note_card)
        card = cards.with_words(cards.fleet_resource_card(
            [{"device_ref": TOKYO, "name": "Tokyo", "level": "WARN", "readings": [
                {"key": "swap", "label": "交换区", "text": "100%", "level": "WARN", "value": 100},
                {"key": "cpu", "label": "CPU", "text": "11%", "level": "OK", "value": 11}]}],
            now=NOW), f"东京交换区满了，key {KEYISH}")
        self.assertEqual(transport.send(card, idempotency_key="ask:om_9:resource"), "om_1")
        self.assertIsNone(transport.send(card, idempotency_key="refused"))
        self.assertTrue(transport.patch("om_1", card, sent_at="2026-11-11T01:00:00Z"))
        (page,) = list((self.clone / "cards").rglob("*.md"))
        text = page.read_text(encoding="utf-8")
        self.assertEqual(text.count("Fleet 资源 · 1 台"), 2, "sent and patched; the refused one is not kept")
        self.assertIn("发送 · Fleet 资源 · 1 台 【1 台要看】", text)
        self.assertIn("更新 · Fleet 资源", text)
        self.assertIn("Tokyo　交换区用了 100%", text)
        self.assertIn("东京交换区满了", text)
        self.assertNotIn("<font", text)
        self.assertNotIn(KEYISH, text)

    def test_an_archive_that_fails_never_stops_a_card(self):
        from fleet_hub_watch.notify import ArchivingTransport

        class Feishu:
            def send(self, card, *, idempotency_key):
                return "om_1"

        def broken(*args, **kwargs):
            raise OSError("disk full")
        self.assertEqual(ArchivingTransport(Feishu(), broken).send({}, idempotency_key="k"), "om_1")

