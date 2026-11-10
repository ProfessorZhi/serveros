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
        self.assertEqual(got, {"written": 1, "pushed": True})
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
        self.assertIn("fleet: 1 条任务记录", log)
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

    def test_the_cursor_is_its_own(self):
        a = f"{TOKYO}|claude|s-1"
        self.inbox.note_dispatch(a, "r1", label="x")
        self.finish(a, "r1")
        self.journal.tick(NOW)
        self.assertGreater(self.inbox.cursor(SUBSCRIBER), 0)
        self.assertEqual(self.inbox.cursor("hub-watch-notify"), 0)


if __name__ == "__main__":
    unittest.main()
