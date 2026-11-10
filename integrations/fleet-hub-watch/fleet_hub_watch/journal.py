"""The fleet journal: every finished batch and task as one Markdown file in a
private Git repository, pushed from the hub.

What a file holds — the exchange between the commander (GPT) and the devices,
not the devices' own working:

    主人的原话            what the owner asked (the commander's batch note)
    发给设备的提示词       what each device was sent
    设备的最终回复         the device's final answer, in full — what the
                          commander read back
    做了什么              metadata only: turns, tools, skills, MCP, model, time
    过程                  the trace lines, metadata only

The device's own step-by-step work stays where it ran, in its session: the file
names that session (`session_ref`) so it can be opened there. Nothing secret is
written: text passes the inbox's key-shaped mask, and no transcript is copied.

It is a subscriber of the hub inbox with its own cursor, so it sees each
finished batch and each finished stand-alone task once, and a restart picks up
where it stopped. Commits are gathered and pushed at most every `push_every`
seconds; a push that fails is retried on a later tick, never lost.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

SUBSCRIBER = "fleet-journal"
CN = ZoneInfo("Asia/Shanghai")
#: On the first tick only events this recent are written; older history is not back-filled.
FIRST_RUN_WINDOW = timedelta(days=1)
RUN_KINDS = ("run.completed", "run.failed", "run.unknown")
OUTCOME = {"completed": "✅ 完成", "failed": "❌ 失败", "unknown": "⚪ 结果不明", None: "⏳ 未结束"}
MAX_REPLY = 60_000          # a final answer longer than this is cut, and says so

README = """# Fleet journal

ServerOS Fleet 的任务记录，由东京 hub 自动写入（`fleet_hub_watch.journal`），不要手改。

- `runs/YYYY/MM/DD/` 每个批次或单个任务一个文件：主人的原话、发给设备的提示词、
  设备的最终回复（原文）、设备做了什么（只有元数据），以及过程的时间线。
- 每天的 `INDEX.md` 一行一条。
- 设备的具体执行过程留在设备本地的会话里；文件里写了会话的 `session_ref`。
- 不含任何密钥：文本写入前按密钥的样子遮蔽，不复制会话原文。
"""


def _mask(text: Any) -> str:
    from fleet_mcp.inbox import SECRETISH
    return SECRETISH.sub("‹已遮蔽›", str(text or ""))


def _slug(text: str) -> str:
    text = re.sub(r"[\\/:*?\"<>|\s]+", "-", text or "").strip("-.")
    return text[:40] or "task"


def _local(moment: str | None) -> str:
    if not moment:
        return "—"
    try:
        at = datetime.fromisoformat(moment.replace("Z", "+00:00"))
    except ValueError:
        return moment
    return at.astimezone(CN).strftime("%Y-%m-%d %H:%M:%S")


def _fence(text: str) -> str:
    longest = max([len(m) for m in re.findall(r"`+", text)] + [2])
    ticks = "`" * (longest + 1)
    return f"{ticks}text\n{text}\n{ticks}"


class Journal:
    def __init__(self, inbox: Any, root: Path, *,
                 reply: Callable[[str, str], str | None] | None = None,
                 names: dict[str, str] | None = None,
                 push_every: float = 600.0, ssh_key: str | None = None,
                 clock: Callable[[], float] | None = None,
                 git: Callable[..., subprocess.CompletedProcess] | None = None) -> None:
        self.inbox, self.root = inbox, Path(root)
        self.reply = reply
        self.names = names or {}
        self.push_every = push_every
        self.ssh_key = ssh_key
        self.clock = clock or __import__("time").time
        self._git = git or self._run_git
        self._last_push = 0.0
        self._pending = 0
        #: Events older than a day before this process started are not back-filled.
        self._born = datetime.now(timezone.utc)

    # -- reading what happened -------------------------------------------------

    def _steps(self, **where: Any) -> list[dict[str, Any]]:
        try:
            return self.inbox.read_trace(**where, limit=5000)
        except Exception:  # noqa: BLE001 - a file without a timeline is still a record
            return []

    @staticmethod
    def _text(steps: list[dict], name: str, run_id: str | None = None) -> str | None:
        found = None
        for step in steps:
            if step["step"] == name and (run_id is None or step.get("run_id") == run_id):
                text = (step.get("detail") or {}).get("text")
                if text:
                    found = text
        return found

    @staticmethod
    def _detail(steps: list[dict], name: str, run_id: str | None = None) -> dict:
        found: dict = {}
        for step in steps:
            if step["step"] == name and (run_id is None or step.get("run_id") == run_id):
                found = step.get("detail") or {}
        return found

    def _answer(self, session_ref: str, run_id: str, steps: list[dict]) -> str | None:
        """This run's final answer. The trace kept it when the run finished; it
        is clipped at the trace's limit, so a reply at that length is read again
        from the session — and used only if it is the same answer (the session
        may have moved on to a later task since)."""
        from fleet_mcp.inbox import TRACE_TEXT_MAX
        kept = self._text(steps, "result", run_id)
        text = kept
        if (kept is None or len(kept) >= TRACE_TEXT_MAX) and self.reply is not None:
            try:
                live = self.reply(session_ref, run_id)
            except Exception:  # noqa: BLE001 - the trace keeps a clipped copy
                live = None
            if live and (kept is None or _mask(live).startswith(kept[:200])):
                text = live
        if text and len(text) > MAX_REPLY:
            text = text[:MAX_REPLY] + f"\n…（超过 {MAX_REPLY} 字，后面截掉了；全文在设备会话里）"
        return text

    def _device(self, session_ref: str) -> str:
        ref = session_ref.split("|", 1)[0]
        return self.names.get(ref) or ref.removeprefix("device:")

    # -- writing ----------------------------------------------------------------

    def _run_section(self, member: dict, steps: list[dict], heading: str = "###") -> list[str]:
        ref, run_id = member["session_ref"], member["run_id"]
        work = self._detail(steps, "device finished", run_id)
        out = [f"{heading} {self._device(ref)} · {OUTCOME.get(member.get('outcome'), member.get('outcome'))}",
               "",
               f"- run `{run_id}` · 会话 `{ref}`（具体执行过程在这台设备的会话里）"]
        facts = [f"{k} {work[k]}" for k in ("turns", "tools", "skills", "mcp", "model") if work.get(k)]
        if work.get("waiting_for_person"):
            facts.append("停下来等人回答")
        if facts:
            out.append("- 做了什么：" + " · ".join(str(f) for f in facts))
        prompt = self._text(steps, "prompt sent", run_id)
        if prompt:
            out += ["", "**发给设备的提示词**", "", _fence(_mask(prompt))]
        answer = self._answer(ref, run_id, steps)
        out += ["", "**设备的最终回复（原文）**", "",
                _fence(_mask(answer)) if answer else "_没读到回复_", ""]
        return out

    def _timeline(self, steps: list[dict]) -> list[str]:
        """The trace in Beijing time; a run of 'observed' lines that say the same
        thing is folded into its first line and a count."""
        from fleet_mcp.trace import line
        if not steps:
            return []
        rows, last_key, folded = [], None, 0
        for step in steps:
            detail = step.get("detail") or {}
            key = ((step.get("run_id"), detail.get("status"), detail.get("reason"))
                   if step["step"] == "observed" else None)
            if key is not None and key == last_key:
                folded += 1
                continue
            if folded:
                rows.append(f"          …同样的观测又 {folded} 次")
                folded = 0
            last_key = key
            local = _local(step.get("at"))[11:19]
            rows.append(local + line(step)[8:])
        if folded:
            rows.append(f"          …同样的观测又 {folded} 次")
        return ["## 过程（元数据，北京时间）", "", "<details><summary>时间线</summary>", "",
                _fence(_mask("\n".join(rows))), "", "</details>", ""]

    def write_batch(self, batch_id: str, event: dict) -> Path | None:
        head = self.inbox.batch(batch_id)
        steps = self._steps(batch_id=batch_id)
        opened = self._detail(steps, "owner request")
        counts: dict[str, int] = {}
        for run in head["runs"]:
            counts[run.get("outcome") or "pending"] = counts.get(run.get("outcome") or "pending", 0) + 1
        verdict = ("全部完成" if counts.get("completed") == head["total"]
                   else "，".join(f"{OUTCOME.get(k, k)} {v}" for k, v in counts.items()))
        lines = [f"# {_mask(head['name'])} · {verdict}", "",
                 f"- 批次 `{batch_id}` · 派发者 {head.get('opened_by') or '—'} · {head['total']} 个任务",
                 f"- 开始 {_local(head.get('opened_at'))} · 结束 {_local(event.get('occurred_at'))}（北京时间）"]
        if opened.get("skills"):
            lines.append(f"- 指挥侧用的 skill：{opened['skills']}")
        commander = [s for s in steps if s.get("source") == "commander"
                     and s["step"] != "owner request"]
        if commander:
            calls: dict[str, int] = {}
            for s in commander:
                calls[s["step"]] = calls.get(s["step"], 0) + 1
            lines.append("- 指挥侧调用：" + " · ".join(f"{k}×{v}" for k, v in calls.items()))
        request = self._text(steps, "owner request")
        lines += ["", "## 主人的原话", "", _fence(_mask(request)) if request else "_没有记录_", "",
                  "## 每台设备", ""]
        for run in head["runs"]:
            lines += self._run_section(run, steps)
        lines += self._timeline(steps)
        stamp = head.get("opened_at") or event.get("occurred_at")
        return self._save(stamp, f"{_slug(head['name'])}-{batch_id[:8]}", head["name"], verdict,
                          "\n".join(lines))

    def write_run(self, event: dict) -> Path | None:
        ref, run_id = event["session_ref"], event["run_id"]
        steps = self._steps(run_id=run_id)
        label = None
        finder = getattr(self.inbox, "label_of", None)
        if finder:
            label = finder(ref, run_id)
        label = label or event.get("project") or "一个任务"
        outcome = event["kind"].split(".", 1)[1]
        verdict = OUTCOME.get(outcome, outcome)
        lines = [f"# {_mask(label)} · {verdict}", "",
                 f"- 单个任务（不在批次里）· 结束 {_local(event.get('occurred_at'))}（北京时间）"]
        request = self._text(steps, "owner request")
        if request:
            lines += ["", "## 主人的原话", "", _fence(_mask(request))]
        lines += ["", "## 设备", ""]
        lines += self._run_section({"session_ref": ref, "run_id": run_id, "outcome": outcome},
                                   steps)
        lines += self._timeline(steps)
        return self._save(event.get("occurred_at"), f"{_slug(label)}-{run_id[:8]}", label, verdict,
                          "\n".join(lines))

    def _save(self, stamp: str | None, stem: str, title: str, verdict: str, body: str) -> Path:
        try:
            at = datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).astimezone(CN)
        except ValueError:
            at = datetime.now(CN)
        day = self.root / "runs" / f"{at:%Y}" / f"{at:%m}" / f"{at:%d}"
        day.mkdir(parents=True, exist_ok=True)
        path = day / f"{at:%H%M}-{stem}.md"
        path.write_text(body.rstrip() + "\n", encoding="utf-8")
        index = day / "INDEX.md"
        entry = f"- {at:%H:%M} [{_mask(title)}]({path.name}) · {verdict}\n"
        existing = index.read_text(encoding="utf-8") if index.exists() else f"# {at:%Y-%m-%d}\n\n"
        if entry not in existing:
            index.write_text(existing + entry, encoding="utf-8")
        readme = self.root / "README.md"
        if not readme.exists():
            readme.write_text(README, encoding="utf-8")
        self._pending += 1
        return path

    # -- the tick ---------------------------------------------------------------

    def tick(self, now: datetime) -> dict[str, Any]:
        page = self.inbox.read(SUBSCRIBER, limit=100)
        written, handled_to = 0, page["cursor"]
        oldest = min(self._born, now) - FIRST_RUN_WINDOW
        for event in page["events"]:
            handled_to = event["seq"]
            if event.get("occurred_at"):
                try:
                    if datetime.fromisoformat(event["occurred_at"].replace("Z", "+00:00")) < oldest:
                        continue
                except ValueError:
                    pass
            try:
                if event["kind"] == "batch.completed":
                    batch_id = str(event.get("work_ref") or "").removeprefix("batch:")
                    if batch_id and self.write_batch(batch_id, event):
                        written += 1
                elif event["kind"] in RUN_KINDS:
                    ref, run_id = event.get("session_ref"), event.get("run_id")
                    in_batch = self.inbox.batch_of(ref, run_id) is not None
                    dispatched = self.inbox.was_dispatched(ref, run_id)
                    if ref and run_id and dispatched and not in_batch and self.write_run(event):
                        written += 1
            except Exception as exc:  # noqa: BLE001 - one bad record does not stop the rest
                print(f"journal: {event.get('kind')} not written ({type(exc).__name__})",
                      file=sys.stderr)
        if handled_to > page["cursor"]:
            self.inbox.ack(SUBSCRIBER, handled_to)
        pushed = self.flush()
        return {"written": written, "pushed": pushed}

    # -- git ------------------------------------------------------------------

    def _run_git(self, *args: str) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        if self.ssh_key:
            env["GIT_SSH_COMMAND"] = (f"ssh -i {self.ssh_key} -o IdentitiesOnly=yes "
                                      "-o StrictHostKeyChecking=accept-new")
        return subprocess.run(["git", *args], cwd=self.root, env=env, capture_output=True,
                              text=True, timeout=120)

    def flush(self, *, force: bool = False) -> bool:
        """Commit what was written and push, at most every `push_every` seconds."""
        if not (self.root / ".git").exists():
            return False
        if not force and self.clock() - self._last_push < self.push_every:
            return False
        status = self._git("status", "--porcelain")
        if status.returncode != 0:
            return False
        if not status.stdout.strip() and self._git("rev-parse", "--verify", "-q", "HEAD").returncode != 0:
            return False                    # nothing written yet: nothing to push
        if status.stdout.strip():
            self._git("add", "-A")
            done = self._git("-c", "user.name=fleet-hub", "-c", "user.email=fleet-hub@localhost",
                             "commit", "-q", "-m", f"fleet: {self._pending or 1} 条任务记录")
            if done.returncode != 0:
                return False
            self._pending = 0
        ahead = self._git("rev-list", "--count", "@{u}..HEAD")
        if ahead.returncode == 0 and ahead.stdout.strip() == "0":
            self._last_push = self.clock()
            return False
        upstream = self._git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
        if ahead.returncode == 0 and upstream.returncode == 0 and "/" in upstream.stdout:
            # Explicitly to the tracked branch: the local branch may be named
            # differently (an empty clone's `master` tracking `origin/main`).
            remote, _, branch = upstream.stdout.strip().partition("/")
            pushed = self._git("push", "-q", remote, f"HEAD:{branch}")
        else:
            # No upstream yet: the repository was empty when it was cloned. The
            # first push creates `main` there and tracks it from then on.
            pushed = self._git("push", "-q", "-u", "origin", "HEAD:main")
        if pushed.returncode != 0:
            print("journal: push failed, kept locally; retried next time", file=sys.stderr)
            self._last_push = self.clock()          # the next try waits the same interval
            return False
        self._last_push = self.clock()
        return True
