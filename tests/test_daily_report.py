#!/usr/bin/env python3
"""SYS-23 — 日报从真实差量生成，且三句话必须是假的。

每一节让哪一句保持为假
----------------------
    real delta      「报告里写的是想象出来的东西」——用一次真的 `git commit` 钉住
    zero progress   「没有进展」被静默跳过，文档看起来正常
    did-not-answer  「源没答」被当成「源答了：没有」——缺口渲染成零
    unrun           「没跑过」被省略——省略过的失败项在文档里和通过长得一样
    refusal         「无关纪要进去了」——而且是整份输入被拒，不是过滤掉那一条
    receipt         「投过了」——写盘成功被读成投递成功
    skeleton        「文档少了一节」——少掉的一节读起来和「没事可说」一样
    fallback        「兜底被当成事实」——一个叫 unknown 的人
    placement       「报告写进了 revision」——下一次更新把它整份换掉

WHY THE ASSERTIONS ARE ON THE DOCUMENT DICT AND NOT ON SENTENCES
----------------------------------------------------------------
钉的是判断。一句「本周期没有可核对的进展」的措辞随时可以改，而 `document["progress"] == NONE`
只有在那次判断真的做了「问过了、是零」这一步时才成立。所以凡是能读结构化字段的地方都读
结构化字段；只有「这句话必须被写出来」这类要求才碰渲染出来的文本，而那时也断言的是判定词
本身（`NONE` / `UNKNOWN`），不是它周围的措辞。
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

# -- 声明过的数字，写成字面量 ------------------------------------------------
# 与 `tests/test_alert_digest.py` 同样的理由：读取被测契约的 fixture 只能证明契约等于它自己。
# 把 `top_n` 改成 0，一个读契约的测试照样通过。所以这里是字面量，
# `TheContractOwnsTheNumbers` 是那个在两者不再相等时会红的接头。
TOP_N = 8
ACCEPTED = ("focus", "work-delta", "quay-record", "run-status")
# `runtime/notification-contract.yaml#alerts.delivery.values`
PUBLICATION_STATES = ("DELIVERED", "FAILED", "UNSUPPORTED", "UNKNOWN")


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


report = _load("serveros_runtime_daily_report", ROOT / "runtime" / "daily_report.py")

WINDOW = {"start": "2026-09-20T00:00:00+00:00", "end": "2026-09-21T00:00:00+00:00"}
GENERATED_AT = "2026-09-20T12:00:00+00:00"


# ---------------------------------------------------------------------------
# 夹具：一个真的 git 仓库，和一份真的 Quay 账本
# ---------------------------------------------------------------------------

def _env() -> dict:
    """一个不受这台机器上任何人配置影响的 git 环境。

    全局或系统级的 `commit.gpgsign`、`init.defaultBranch`、`core.hooksPath` 都会把
    「造一个两笔提交的仓库」变成一件成功与否取决于别人配置的事，而失败的形态是测试红了、
    原因和被测代码无关。
    """
    env = dict(os.environ)
    env.update({"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
                "GIT_AUTHOR_NAME": "Test Actor", "GIT_AUTHOR_EMAIL": "t@example.invalid",
                "GIT_COMMITTER_NAME": "Test Actor", "GIT_COMMITTER_EMAIL": "t@example.invalid"})
    return env


def _git_repo(directory: Path, commits) -> Path:
    """`commits` 是 `[(subject, when, {path: content}), …]`，按顺序提交。"""
    directory.mkdir(parents=True, exist_ok=True)
    env = _env()

    def run(*args):
        return subprocess.run(["git", *args], cwd=str(directory), env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              timeout=60, check=False)

    run("init", "-q", "-b", "main")
    for subject, when, files in commits:
        for name, content in files.items():
            target = directory / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        run("add", "-A")
        stamped = dict(env, GIT_AUTHOR_DATE=when, GIT_COMMITTER_DATE=when)
        subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", subject],
                       cwd=str(directory), env=stamped, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, timeout=60, check=False)
    return directory


def _quay_dir(directory: Path, events=(), tasks=()) -> Path:
    """一个 Quay 工作区。`events` 是账本行，`tasks` 是 `(id, status, title)`。"""
    (directory / ".quay").mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(event, ensure_ascii=False) for event in events]
    (directory / ".quay" / "gate-events.jsonl").write_text(
        "\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    task_dir = directory / "tasks"
    task_dir.mkdir(parents=True, exist_ok=True)
    for identifier, status, title in tasks:
        (task_dir / f"{identifier}.md").write_text(
            f"---\nid: {identifier}\ntitle: {title}\nstatus: {status}\n---\n",
            encoding="utf-8")
    return directory


def _gate(item, verdict, when, gate="acceptance", reason=""):
    return {"id": f"evt-{item}-{when}", "item_id": item, "pipeline_id": item,
            "gate": gate, "actor": "quay-cli", "verdict": verdict,
            "timestamp": when, "payload": {"reason": reason}}


class Fixture(unittest.TestCase):
    """一个临时目录。没有别的。"""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="sys23-test-"))
        self.out = self.dir / "out"

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    # -- 夹具助手 ---------------------------------------------------------

    def repo(self, name="repo", commits=()):
        return _git_repo(self.dir / name, commits)

    def quay(self, name="quay", events=(), tasks=()):
        return _quay_dir(self.dir / name, events, tasks)

    def measure(self, *, worktree=None, quay=None, window=WINDOW):
        fallbacks = report.Fallbacks()
        work = report.measure_worktree(worktree if worktree is not None
                                       else self.dir / "no-such-repo", window, fallbacks)
        records = report.measure_quay(quay if quay is not None
                                      else self.dir / "no-such-quay", window, fallbacks)
        return work, records, fallbacks

    def compose(self, *, worktree=None, quay=None, run_status=(), focus=None,
                window=WINDOW, fallbacks=None):
        work, records, collected = self.measure(worktree=worktree, quay=quay, window=window)
        document = report.compose(
            period=window, work_delta=work, quay=records, run_status=run_status,
            focus=focus, generated_at=GENERATED_AT,
            fallbacks=collected if fallbacks is None else fallbacks)
        return document, report.render(document)

    def empty_both(self):
        """两个源都答了，都答的是「窗口内什么都没有」。"""
        repo = self.repo(commits=[("上一周期的提交", "2026-09-19T10:00:00+00:00",
                                   {"a.txt": "old\n"})])
        quay = self.quay()
        return repo, quay


# ---------------------------------------------------------------------------
# 真实的差量
# ---------------------------------------------------------------------------

class ARealDeltaAppearsInTheReport(Fixture):
    def test_a_real_commit_reaches_the_document(self):
        """一次真的 `git commit`，提交号与标题都要出现在文档里。

        用真的仓库而不是一份手写的差量，是因为这一条要钉的正是「差量是从工作树读出来的」
        ——一份手写的输入只能证明渲染函数会印字符串。
        """
        repo = self.repo(commits=[
            ("SYS-23 实现 producer", "2026-09-20T05:00:00+00:00",
             {"runtime/daily_report.py": "x = 1\n"}),
            ("SYS-23 补测试", "2026-09-20T06:00:00+00:00",
             {"tests/test_daily_report.py": "y = 2\n"}),
        ])
        quay = self.quay(events=[_gate("QUAY-0001", "pass", "2026-09-20T07:00:00.000Z",
                                      reason="acceptance passed (exit 0)")],
                         tasks=[("QUAY-0001", "done", "SYS-23")])

        document, text = self.compose(worktree=repo, quay=quay)

        self.assertEqual(document["progress"], report.SOME)
        self.assertEqual(len(document["work"]["commits"]), 2)
        self.assertTrue(document["work"]["answered"])
        subjects = [c["subject"] for c in document["work"]["commits"]]
        self.assertIn("SYS-23 实现 producer", subjects)
        self.assertIn("SYS-23 补测试", subjects)
        # 顺读：最早的一笔在前。git 默认倒序，渲染层直接印会把日报的第一行放成最后一笔。
        self.assertEqual(subjects[0], "SYS-23 实现 producer")
        # 两笔提交都在文档里，且都带上文件数。
        for commit in document["work"]["commits"]:
            self.assertIn(commit["revision"][:12], text)
            self.assertEqual(commit["file_count"], 1)
        # Quay 那一侧的进展也进来了。
        self.assertEqual(len(document["quay"]["gates"]), 1)
        self.assertIn("QUAY-0001", text)
        self.assertEqual(document["progress_because"],
                         "2 笔提交、1 条 Quay 闸门记录落在窗口内。")

    def test_a_commit_outside_the_window_is_not_this_periods_progress(self):
        """窗口外的一笔提交不是本周期的进展，但它也不是「不存在」。"""
        repo = self.repo(commits=[("上个周期做的", "2026-09-01T05:00:00+00:00",
                                   {"a.txt": "x\n"})])
        document, _ = self.compose(worktree=repo, quay=self.quay())

        self.assertEqual(document["work"]["commits"], [])
        self.assertTrue(document["work"]["answered"], "窗口外不等于读不到")


# ---------------------------------------------------------------------------
# 零进展
# ---------------------------------------------------------------------------

class ZeroProgressIsSaidOutLoud(Fixture):
    def test_zero_is_a_finding_and_is_written_down(self):
        """两个源都答了、都答的是零 → `NONE`，而且这句话必须出现在文档里。

        这一条挡的是那种最坏的产物：一份什么都不说的日报。它不是空的——它有抬头、有小节、
        有一张空表——所以它读起来和一份正常的日报没有区别，而这一整个周期其实什么都没动。
        """
        repo, quay = self.empty_both()
        document, text = self.compose(worktree=repo, quay=quay)

        self.assertEqual(document["progress"], report.NONE)
        self.assertTrue(document["work"]["answered"])
        self.assertTrue(document["quay"]["answered"])
        # 判定词本身在文档里，不是靠一段可能被改写的措辞。
        self.assertIn(report.NONE, text)
        self.assertIn("查了，结果是零", document["progress_because"])

    def test_zero_is_not_rendered_as_some_or_unknown(self):
        """三个值里的另外两个都不许被选中——这一步是这次判断的全部内容。"""
        repo, quay = self.empty_both()
        document, _ = self.compose(worktree=repo, quay=quay)

        self.assertNotEqual(document["progress"], report.SOME)
        self.assertNotEqual(document["progress"], report.UNKNOWN)


# ---------------------------------------------------------------------------
# 源没答 ≠ 源答了：零
# ---------------------------------------------------------------------------

class NotAnsweredIsNotZero(Fixture):
    def test_a_gap_is_not_a_zero(self):
        """两个源都返回了缺口 → `UNKNOWN`，绝不是 `NONE`。

        这是本模块里最容易写错、也最难被发现的一处：缺口和零在数据结构上都是「空列表」，
        所以只看列表是否为空的调用方会把一次没跑成的测量写成一句「本周无事发生」。
        """
        document, text = self.compose(worktree=self.dir / "no-such-repo",
                                      quay=self.dir / "no-such-quay")
        self.assertEqual(document["progress"], report.UNKNOWN)
        self.assertNotEqual(document["progress"], report.NONE)
        self.assertFalse(document["work"]["answered"])
        self.assertFalse(document["quay"]["answered"])
        self.assertIn(report.UNKNOWN, text)
        self.assertIn("没有进展", document["progress_because"])

    def test_one_source_answering_is_enough_to_say_none(self):
        """只有一个源答了，而它答的是零：这仍然是「问过了」，是 `NONE`。

        反过来写（要求两个源都答才算数）会让一份工作树干净、Quay 目录还没建起来的日报
        永远停在 UNKNOWN，而它其实知道一半的事实。
        """
        repo = self.repo(commits=[("更早的事", "2026-09-19T05:00:00+00:00", {"a.txt": "x\n"})])
        document, _ = self.compose(worktree=repo, quay=self.dir / "no-such-quay")
        self.assertEqual(document["progress"], report.NONE)

    def test_an_unreadable_source_says_why(self):
        """缺口必须带着理由。没有理由的缺口和「没有」长得一样。"""
        document, _ = self.compose(worktree=self.dir / "no-such-repo",
                                   quay=self.dir / "no-such-quay")
        self.assertIn("不是一个 git 检出", document["work"]["why"])
        self.assertIn("不存在", document["quay"]["why"])

    def test_an_empty_repository_is_an_answer_not_a_gap(self):
        """一个还没有任何提交的检出答的是「空仓库」，不是「读不了」。"""
        empty = self.repo(commits=[])
        work = report.measure_worktree(empty, WINDOW, report.Fallbacks())
        self.assertTrue(work["answered"])
        self.assertEqual(work["commits"], [])
        self.assertIn("空仓库", " ".join(work["notes"]))


# ---------------------------------------------------------------------------
# 未运行 / 未测 / 缺覆盖
# ---------------------------------------------------------------------------

class UnrunAndUntestedAreNamed(Fixture):
    def test_an_unrun_item_is_named_and_not_omitted(self):
        """源说了「没跑」→ `NOT_RUN`，出现在「卡住」里，不是消失。"""
        document, text = self.compose(run_status=[
            {"name": "tests/test_daily_report.py", "kind": "test",
             "observed": {"ran": False, "why": "本机没装 pytest"}},
        ])
        row = document["run_status"][0]
        self.assertEqual(row["state"], report.NOT_RUN)
        self.assertFalse(row["ok"])
        self.assertIn(row, document["blocking"])
        self.assertIn("tests/test_daily_report.py", text)
        self.assertIn(report.NOT_RUN, text)

    def test_an_unobserved_item_is_not_read_as_passed(self):
        """源什么都没说 → `UNOBSERVED`。缺席既不是通过，也不是「没跑」。"""
        document, _ = self.compose(run_status=[{"name": "lint", "kind": "run"}])
        row = document["run_status"][0]
        self.assertEqual(row["state"], report.UNOBSERVED)
        self.assertNotEqual(row["state"], report.PASSED)
        self.assertIn(row, document["blocking"])

    def test_missing_coverage_is_named_as_missing(self):
        """缺覆盖是它自己的一个状态，不是「通过」也不是「未运行」。"""
        document, text = self.compose(run_status=[
            {"name": "runtime/", "kind": "coverage", "observed": {"percent": None}},
        ])
        row = document["run_status"][0]
        self.assertEqual(row["state"], report.COVERAGE_MISSING)
        self.assertIn(row, document["blocking"])
        self.assertIn(report.COVERAGE_MISSING, text)

    def test_a_measured_coverage_reading_is_not_a_blocker(self):
        """量出来的覆盖率不是一件卡住的事。

        覆盖率缺读数才进「卡住」。「卡住」那一栏的每一行都在说「这里需要有人看一眼」，
        把 95% 放进去会让那一栏变得不值得看——而一栏不值得看的「卡住」，等于没有「卡住」。
        """
        document, _ = self.compose(run_status=[
            {"name": "runtime/", "kind": "coverage", "observed": {"percent": 95}},
        ])
        row = document["run_status"][0]
        self.assertEqual(row["state"], report.COVERED)
        self.assertNotIn(row, document["blocking"])

    def test_a_failure_is_kept_and_a_pass_stays_out_of_the_way(self):
        """失败的项进「卡住」，通过的项不进——「卡住」里全是通过是另一种读不懂。"""
        document, _ = self.compose(run_status=[
            {"name": "a", "kind": "test", "observed": {"ran": True, "exit_code": 0}},
            {"name": "b", "kind": "test", "observed": {"ran": True, "exit_code": 7}},
            {"name": "c", "kind": "test", "observed": {"ran": True}},
        ])
        states = {row["name"]: row["state"] for row in document["run_status"]}
        self.assertEqual(states["a"], report.PASSED)
        self.assertEqual(states["b"], report.FAILED)
        self.assertEqual(states["c"], report.HALF_ANSWERED)
        blocked = {row["name"] for row in document["blocking"]}
        self.assertEqual(blocked, {"b", "c"})


# ---------------------------------------------------------------------------
# 边界：与工作无关的会议纪要不进来
# ---------------------------------------------------------------------------

class AnUnrelatedMeetingNoteCannotEnter(Fixture):
    def test_it_is_refused_by_name_with_a_reason(self):
        """整份输入被拒，消息里说出是哪一个被拒了、边界是什么。"""
        with self.assertRaises(report.Refused) as caught:
            report.refuse_unaccepted({
                "work-delta": {"answered": True, "commits": []},
                "meeting-note": {"title": "周会", "attendees": ["a"]},
            })
        message = str(caught.exception)
        self.assertIn("meeting-note", message)
        self.assertIn("work-delta", message)   # 边界本身被念出来
        self.assertIn("边界", message)          # 判据是边界，不是一份名单

    def test_the_whole_input_is_refused_and_nothing_is_partially_accepted(self):
        """「先收进来再过滤掉那一条」是另一种实现，而它的漏洞在于过滤规则会漏。

        这里钉住的是：一份混着会议纪要的输入不会走到组合那一步。
        """
        with self.assertRaises(report.Refused):
            report.refuse_unaccepted({"quay-record": {"answered": True},
                                      "meeting-note": {"title": "周会"}})
        # 干净的四类输入不被拒。
        report.refuse_unaccepted({kind: {} for kind in ACCEPTED})

    def test_the_boundary_is_a_whitelist_not_a_blacklist(self):
        """判据是「不在名单上」，所以一个没人预见过的新名字同样被拒。"""
        with self.assertRaises(report.Refused) as caught:
            report.refuse_unaccepted({"work-delta": {}, "standup-notes": {}})
        self.assertIn("standup-notes", str(caught.exception))

    def test_the_accepted_set_is_the_contracts_not_a_literal_in_the_module(self):
        """接不接受的名单在契约里，不在代码里。"""
        self.assertEqual(report.accepted_kinds(), ACCEPTED)

    def test_the_api_has_no_parameter_for_anything_else(self):
        """边界就是签名：`compose` 收不下第五类东西，因为没有能放下它的参数。"""
        import inspect
        allowed = {"period", "work_delta", "quay", "run_status", "focus",
                   "generated_at", "fallbacks", "document"}
        parameters = set(inspect.signature(report.compose).parameters)
        self.assertTrue(parameters <= allowed,
                        f"compose 多出了参数：{sorted(parameters - allowed)}")

    def test_the_module_does_not_read_the_meeting_namespace(self):
        """不读，就没有「漏掉一条过滤规则」的可能。

        读取是一种接受。一个读了会议目录再过滤的实现，它的边界是「先全收进来再说」；
        这里没有任何一条通往那个目录的路径。
        """
        source = (ROOT / "runtime" / "daily_report.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        self.assertFalse([name for name in imported if "meeting" in name.lower()],
                         "这个 producer 不该知道会议纪要这个模块存在")
        self.assertFalse([name for name in imported if name.split(".")[0] == "space"])


# ---------------------------------------------------------------------------
# 投递回执
# ---------------------------------------------------------------------------

class TheReceiptAnswersWhoWhenWhichVersion(Fixture):
    def _published(self, **kwargs):
        repo = self.repo(commits=[("一笔真的进展", "2026-09-20T05:00:00+00:00",
                                   {"a.txt": "x\n"})])
        document, text = self.compose(worktree=repo, quay=self.quay())
        payload = report.receipt(document, text, out_dir=self.out, **kwargs)
        return document, text, report.publish(document, text, payload, self.out)

    def test_the_receipt_carries_all_three_answers(self):
        document, text, written = self._published(
            recipient="vince", channel="feishu", state="DELIVERED",
            attempted_at="2026-09-20T12:05:00+00:00")
        receipt = json.loads(Path(written["receipt"]).read_text(encoding="utf-8"))

        self.assertEqual(receipt["audience"]["recipient"], "vince")          # 投给谁
        self.assertEqual(receipt["audience"]["channel"], "feishu")
        self.assertEqual(receipt["publication"]["attempted_at"],             # 什么时候
                         "2026-09-20T12:05:00+00:00")
        self.assertEqual(receipt["publication"]["composed_at"], GENERATED_AT)
        self.assertEqual(receipt["publication"]["state"], "DELIVERED")
        self.assertTrue(receipt["publication"]["delivered"])
        # 内容是哪一版：sha256 是对写下去的那串字节算的，所以它不是一句承诺，是一个读数。
        self.assertEqual(receipt["document"]["sha256"], written["sha256"])
        self.assertEqual(
            hashlib.sha256(Path(written["document"]).read_bytes()).hexdigest(),
            receipt["document"]["sha256"])
        self.assertEqual(receipt["document"]["bytes"], len(text.encode("utf-8")))
        self.assertEqual(receipt["document"]["file"], written["document"])
        self.assertEqual(receipt["report"]["period"], document["period"])
        self.assertEqual(receipt["report"]["progress"], document["progress"])

    def test_writing_the_file_is_not_a_delivery(self):
        """调用方什么都没说时，回执写 UNKNOWN / delivered=false。

        这一条是整个回执里最重要的一条：文档写盘成功是一个事实，投递是另一个事实，而
        代码里唯一的 `delivered` 只由 `state == DELIVERED` 决定。
        """
        _, _, written = self._published()
        receipt = json.loads(Path(written["receipt"]).read_text(encoding="utf-8"))
        self.assertEqual(receipt["publication"]["state"], "UNKNOWN")
        self.assertFalse(receipt["publication"]["delivered"])
        self.assertIsNone(receipt["publication"]["attempted_at"])
        self.assertIn("没有人告诉过", receipt["publication"]["why"])

    def test_a_delivery_with_no_recipient_is_refused(self):
        """说不清投给谁的投递不是一个投递。"""
        document, text = self.compose()
        with self.assertRaises(report.Refused) as caught:
            report.receipt(document, text, out_dir=self.out, state="DELIVERED")
        self.assertIn("收件人", str(caught.exception))

    def test_an_unnamed_outcome_is_refused(self):
        document, text = self.compose()
        with self.assertRaises(report.Refused):
            report.receipt(document, text, out_dir=self.out, state="PROBABLY_FINE")

    def test_the_states_are_the_notification_contracts(self):
        self.assertEqual(report.publication_states(), PUBLICATION_STATES)

    def test_a_receipt_that_backs_other_bytes_is_refused(self):
        """回执背书的版本必须就是写下去的那串字节。

        传岔这两个参数的结果是一份指向另一版内容的回执，而它读起来完全正常——没有任何
        后续步骤能发现它。所以这一条当场比一次。
        """
        document, text = self.compose()
        payload = report.receipt(document, text, out_dir=self.out)
        with self.assertRaises(report.Refused) as caught:
            report.publish(document, text + "\n改动过的内容\n", payload, self.out)
        self.assertIn("sha256", str(caught.exception))


# ---------------------------------------------------------------------------
# 文档形状
# ---------------------------------------------------------------------------

class TheDocumentKeepsTheSkeletonShape(Fixture):
    def test_every_declared_heading_is_rendered_in_order(self):
        """skeleton 声明了几节，文档就有几节，顺序一致。

        少掉的一节在读者眼里和「这一节没事可说」是同一件事，而这两件事完全不同。
        """
        document, text = self.compose()
        headings = [entry["heading"] for entry in report.skeleton()["sections"]]
        self.assertTrue(headings)
        positions = [text.index(f"## {heading}") for heading in headings]
        self.assertEqual(positions, sorted(positions))

    def test_a_heading_this_producer_cannot_fill_says_so(self):
        """填不了的节必须写成缺口，不是写成一句听起来合理的话。"""
        document, text = self.compose()
        unfilled = [entry["heading"] for entry in report.skeleton()["sections"]
                    if entry["heading"] not in report._SECTION_SOURCES]
        self.assertTrue(unfilled, "这条测试的前提是 skeleton 里有本 producer 填不了的节")
        for heading in unfilled:
            self.assertIn(f"## {heading}", text)
        self.assertIn("没有来源", text)

    def test_the_filename_comes_from_the_skeleton(self):
        self.assertEqual(report._skeleton_filename(), "daily-report.md")


# ---------------------------------------------------------------------------
# 兜底必须是报出来的
# ---------------------------------------------------------------------------

class FallbacksAreReported(Fixture):
    def test_a_clean_run_says_there_were_none(self):
        """「没有兜底」是一句答案。空列表和一次没跑成的登记不能长得一样。"""
        document, text = self.compose()
        self.assertEqual(document["fallbacks"], [])
        self.assertIn("没有兜底", text)

    def test_an_unattributed_row_is_reported_not_hidden(self):
        """一个没有 `item_id` 的闸门记录被兜成 `item:unknown`，并且被数出来。

        兜底读起来和事实一样：`item:unknown` 在表格里和一个真的任务号没有区别，除非有人
        特意把它数出来。
        """
        quay = self.quay(events=[
            {"gate": "acceptance", "verdict": "pass",
             "timestamp": "2026-09-20T07:00:00.000Z", "payload": {"reason": "ok"}},
        ])
        document, text = self.compose(quay=quay)

        self.assertEqual(len(document["fallbacks"]), 1)
        self.assertEqual(document["fallbacks"][0]["value"], report.ITEM_FALLBACK)
        self.assertIn("1 处兜底", text)
        self.assertIn(report.ITEM_FALLBACK, text)
        # 兜底没有把这条记录变成「没发生」：它仍然算本周期的一条闸门记录。
        self.assertEqual(len(document["quay"]["gates"]), 1)


# ---------------------------------------------------------------------------
# 写到哪里
# ---------------------------------------------------------------------------

class ReportsAreNotWrittenIntoTheRevision(Fixture):
    def test_a_relative_destination_is_refused(self):
        document, text = self.compose()
        payload = report.receipt(document, text, out_dir=ROOT)
        with self.assertRaises(report.Refused):
            report.publish(document, text, payload, Path("workbench"))

    def test_a_destination_inside_the_repository_is_refused(self):
        """写进 revision 的日报会被下一次更新整份换掉，而它读起来和留着的一份一样。"""
        document, text = self.compose()
        payload = report.receipt(document, text, out_dir=ROOT)
        for target in (ROOT, ROOT / "workbench", ROOT / "docs"):
            with self.assertRaises(report.Refused):
                report.publish(document, text, payload, target)


# ---------------------------------------------------------------------------
# 契约拥有那些数字
# ---------------------------------------------------------------------------

class TheContractOwnsTheNumbers(unittest.TestCase):
    """上面写死的字面量与被测契约之间的接头。不相等时红的应该是这里。"""

    def test_top_n_matches(self):
        self.assertEqual(report.top_n(), TOP_N)

    def test_accepted_kinds_match(self):
        self.assertEqual(report.accepted_kinds(), ACCEPTED)

    def test_the_contract_names_a_meeting_note_as_refused(self):
        """「无关纪要不进来」的理由写在契约里——它是判据的例子，不是代码里的分支。"""
        block = report.contract()["inputs"]["examples_of_refused"]
        named = " ".join(str(entry["what"]) for entry in block)
        self.assertIn("会议纪要", named)
        for entry in block:
            self.assertTrue(entry.get("why"), "一条没有理由的拒绝等于没写")


if __name__ == "__main__":
    unittest.main(verbosity=2)
