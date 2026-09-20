#!/usr/bin/env python3
"""workbench — render START-HERE and the documents behind it.

WHAT THIS IS
------------
One entry point a person can open to inspect this round's deliverable set. It is
a RENDERER, not a store: every measured line comes from running a command, and
every decided line comes from `runtime/workbench_sources.py`, which is where a
person wrote it.

WHY IT RUNS THE ENTRY POINT INSTEAD OF IMPORTING
------------------------------------------------
The documents say what a reader would see if they ran the command themselves, so
the renderer runs the same entry point a reader would — `bin/serverosctl` — as a
subprocess, and prints the exit code beside the claim. An import would answer
"does the function work"; the question here is "can a person reach it", and those
differ the moment a verb is unreachable from the CLI.

WHAT IT IS NOT
--------------
Not a second source of truth. Nothing here decides anything; `--check` exists so
that the rendered set can be shown to still match the commands that produced it,
and its output says which values it refuses to compare.

WHOLE-DOCUMENT HONESTY
----------------------
A section whose command failed renders as FAILED with the exit code and the
first line of stderr. It is never omitted and never softened: a document that
quietly drops a broken section is a document that reads as complete.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

import workbench_sources as SRC  # noqa: E402  (path inserted above, deliberately)

ENTRY = ROOT / "bin" / "serverosctl"
OUT_DEFAULT = ROOT / "workbench"
SNAPSHOT = "⟦snapshot⟧"

# Values that change between two renders of unchanged reality. `--check` strips
# them from both sides before comparing, because a check that failed on the clock
# would be a check nobody could ever pass — and a disabled check is worse than an
# absent one, since it still reads as a guarantee. Each entry says what it hides.
VOLATILE = [
    (re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[+-]\d{2}:\d{2}|Z)?"),
     "一个时钟读数，不是事实"),
    (re.compile(r"\b\d+(?:\.\d+)?\s*(?:seconds|秒)\b"), "从上面那个时钟读数派生的间隔"),
    (re.compile(r'"pid":\s*\d+'), "本进程的 pid，每次渲染都不同"),
    (re.compile(r"\bpid\s+\d+\b"), "同上"),
]


# ── running the real entry point ──────────────────────────────────────────────
#
# EVERY COMMAND A BUILDER RUNS IS RECORDED, AND THE RECORD IS WHAT `--check`
# RE-RUNS. That is how a READING becomes checkable without re-measuring it: the
# numbers may not be compared, but "these commands answered" can be, and it is
# the claim a reader actually relies on. The alternative — an authored list of
# commands per document — would be a second copy of the builders, and the copy
# that drifts is always the one nobody runs.
#
# THE RULE THIS DEPENDS ON: a builder must run ONLY READ-ONLY commands, because
# `--check` runs them again. Every verb any builder reaches for today is a
# reader (`ability tree`, `taxonomy tree`, `device show`, `card *`, `work board`,
# `work coverage`, `work skeleton *`, `requirement board`, `work item`, plus
# `git cat-file` in the manifest). A builder that needs a writing verb must not
# record it — and must say so where it runs it, since `work today` is one verb
# away in the same group and it writes.
RUNLOG = []


def argv_for(command):
    """`serverosctl ...` means THIS repository's entry point; anything else runs
    as written, from the repository root — so a row about the build or the
    release mechanism names its own script rather than pretending to be a verb."""
    if command[0] == "serverosctl":
        return [sys.executable, str(ENTRY), *command[1:]]
    return list(command)


def run(command, timeout=120):
    return run_full(command, timeout=timeout)[:3]


def run_full(command, timeout=120):
    """Run one command. Returns (exit_code, parsed_json_or_None, first_stderr_line,
    full_stdout).

    A failure is DATA here, not an exception: the whole point of the state matrix
    is to show a failed row as failed, so raising would delete the thing the
    document exists to display."""
    proc = subprocess.run(argv_for(command), cwd=str(ROOT), capture_output=True,
                          text=True, timeout=timeout, env=_env())
    RUNLOG.append((" ".join(command), proc.returncode))
    err = next((ln for ln in proc.stderr.splitlines()
                if ln.strip() and "MODULE_TYPELESS" not in ln
                and "Reparsing as ES module" not in ln
                and "trace-warnings" not in ln), "")
    try:
        data = json.loads(proc.stdout)
    except Exception:
        data = None
    return proc.returncode, data, err, proc.stdout


def _env():
    import os
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def out(command):
    """The text a reader would see, verbatim. Recorded like `run_full`, and for
    the same reason: a document rendered through this path is checked through it."""
    proc = subprocess.run(argv_for(command), cwd=str(ROOT), capture_output=True,
                          text=True, timeout=120, env=_env())
    RUNLOG.append((" ".join(command), proc.returncode))
    return proc.returncode, proc.stdout, proc.stderr


# ── small rendering helpers ───────────────────────────────────────────────────

def table(headers, rows):
    rows = [[("" if c is None else str(c)) for c in r] for r in rows]
    out_rows = ["| " + " | ".join(headers) + " |",
                "|" + "|".join("---" for _ in headers) + "|"]
    for r in rows:
        out_rows.append("| " + " | ".join(c.replace("|", "\\|").replace("\n", " ")
                                          for c in r) + " |")
    return "\n".join(out_rows)


def code_block(text, lang=""):
    return f"```{lang}\n{text.rstrip()}\n```"


def stamp(when, inputs=None):
    tail = f" · inputs: {inputs}" if inputs is not None else ""
    return (f"<!-- {SNAPSHOT} rendered {when.isoformat(timespec='seconds')} by "
            f"`runtime/workbench.py` from {ROOT}{tail} -->")


def section(title, body, level=2):
    return f"{'#' * level} {title}\n\n{body.rstrip()}\n"


def failed(what, code, err):
    """A section whose command did not answer. Never silently dropped."""
    return (f"**{what}：没有答案。** 退出码 `{code}`。\n\n"
            f"{code_block(err or '(stderr 为空)')}\n"
            f"这一节因此是 FAILED，不是空：没有答案和答案是空的是两件事。\n")


# ── the documents ─────────────────────────────────────────────────────────────

def row_verdict(row):
    """Is the claim true, as of now.

    A row may assert more than an exit code. `expect` accepts one code or several,
    and `contains` requires a substring of stdout — because for some verbs the
    exit code alone is the wrong claim: `ability resolve` exits 1 to say
    UNAVAILABLE, and a matrix that recorded "exit 1" as failure would call a
    working resolver broken. A row that needs `contains` is a row whose question
    is "did it answer, and did it name what it could not see", and the exit code
    is the wrong instrument for that.
    """
    code, _, err, text = run_full(row["command"])
    want = row["expect"]
    codes = want if isinstance(want, (list, tuple, set)) else [want]
    good = code in codes
    if good and row.get("contains"):
        good = row["contains"] in text
    return code, good, err, text


def doc_start_here(when):
    rows, ok, bad, verdicts = [], 0, 0, []
    for row in SRC.STATE_MATRIX:
        code, good, err, _ = row_verdict(row)
        ok, bad = ok + (1 if good else 0), bad + (0 if good else 1)
        verdicts.append(f"{row['id']}={'OK' if good else 'FAILING'}")
        rows.append([row["id"], row["claim"], "`" + " ".join(row["command"]) + "`",
                     f"exit {code}", "OK" if good else "**FAILING**"])

    # The verdicts, in a form `--check` can compare without reading the table
    # back out of prose. This is the block that makes START-HERE a CHECKABLE
    # page rather than a screenshot of one: everything else on it is either
    # authored or derived from these twenty-three exit codes — which is why the
    # block is emitted here, from the same loop that built the table, and not
    # recomputed by the checker from a second reading of the same commands.
    body = [
        "<!-- ⟦verdicts⟧", *verdicts, "-->",
        "这是本轮骨架的检查入口。它不介绍架构，只回答一个问题：**哪些东西现在可以"
        "从这台机器的入口真正走到，哪些只是存在。**",
        "",
        "每一节都是跑一条命令得到的。命令和退出码一并印出，所以任何一行都可以自己复算；"
        f"不一致的行渲染成 **FAILING** 而不是被删掉。",
        "",
        section("怎么读", "\n".join([
            "1. 先看下面的状态矩阵 —— 它是这一轮「已实现」这个词的实际含义。",
            "2. 再看 [DEPLOYMENT.md](DEPLOYMENT.md) —— §8 的最小变更清单。**没有任何一条已执行。**",
            "3. 然后按需展开其余文档。每份文档顶部都写了它是哪条命令渲染的。",
        ]), 3),
        section("入口清单", table(
            ["文档", "它是什么", "渲染自"],
            [[f"[{n}]({n}.md)", d, f"`{c}`"] for n, d, c in DOC_INDEX]), 3),
        section(f"状态矩阵（{ok} 条 OK，{bad} 条 FAILING）",
                table(["id", "主张", "命令", "实测", ""], rows), 3),
        section("已实现 / 样例 / live 未验证 / 待部署",
                "\n".join([
                    "**已实现**：上面 exit 0 的行。它们跑在这台机器上，读的是这台机器的真相。",
                    "",
                    "**样例**：由固定输入产生的产物 —— 见下方清单。样例通过说明离线路径"
                    "通过，**不说明任何连接可用**。",
                    "",
                    "**live 未验证**：代码在，但从未在真实链路上走过。这一栏是这一轮最该被"
                    "盯着看的地方。",
                    "",
                    "**待部署**：需要单独批准的动作，见 [DEPLOYMENT.md](DEPLOYMENT.md)。",
                ]), 3),
        section("样例", "\n".join(_samples()), 3),
        section("live 未验证", "\n".join(
            f"- **{s['what']}** — {s['why']}" for s in SRC.LIVE_UNVERIFIED), 3),
        section("未完成", "\n".join(
            f"- **{s['what']}** — {s['why']}" for s in SRC.NOT_DELIVERED) or
            "没有登记未完成项。", 3),
        section("待部署", table(
            ["id", "动作", "回退"],
            [[d["id"], d["target"], d["rollback"]] for d in SRC.DEPLOYMENT]), 3),
    ]
    return "\n".join(body)


def _samples():
    """Each sample is checked for existence before it is claimed.

    A sample list that names a path somebody deleted is a list of assurances, so
    the path is stat'd and a missing one renders as MISSING rather than being
    quietly dropped — the same rule the rest of this module follows."""
    lines = []
    for s in SRC.SAMPLES:
        path = Path(s["path"])
        state = "存在" if path.exists() else "**MISSING**"
        lines.append(f"- `{s['path']}`（{state}）— {s['what']}。{s['how']}")
    return lines


def doc_environment(when, ability_repo):
    code, data, err = run(["serverosctl", "ability", "tree", "--json"])
    parts = []
    if data:
        placements = data.get("placements") or {}
        by_node = {}
        for ability, node in placements.items():
            by_node.setdefault(node, []).append(ability)
        unplaced = data.get("unplaced") or []
        parts.append(section("能力树（本节点的投影）", "\n".join([
            f"来源 `{data.get('source')}` @ `{data.get('source_revision')}`，"
            f"权威是 `{data.get('authority')}`，基数 `{data.get('basis')}`。",
            f"能力 {data.get('ability_count')} 个，已归类 {data.get('classified_count')} 个；"
            f"这个读数的年龄 {data.get('age_seconds')} 秒。",
            "",
            "**这份投影可能过期。** 它不自己去读权威；`serverosctl ability refresh` 才会。",
            "",
            table(["node", "abilities"],
                  [[node, "、".join(sorted(by_node.get(node, []))) or "—"]
                   for node in (data.get("nodes") or [])]),
            "",
            f"**没有归类的能力（{len(unplaced)} 个）**："
            + ("、".join(unplaced) if unplaced else "无")
            + "。",
            "",
            "这一行是刻意留着的：一个「已归类 19/30」的读数如果不同时说出剩下 11 个是"
            "谁，读起来就像那 11 个不存在。",
        ]), 3))
    else:
        parts.append(section("能力树", failed("`ability tree`", code, err), 3))

    code, data, err = run(["serverosctl", "taxonomy", "tree", "--json"])
    if data:
        parts.append(section("声明的导航 DAG", "\n".join([
            f"基数 `{data.get('basis')}`；家族 {len(data.get('families') or [])} 个，"
            f"容器 {len(data.get('containers') or [])} 个，"
            f"catalogue {((data.get('catalogue') or {}).get('ability_count'))} 项"
            f"（{(data.get('catalogue') or {}).get('status')}）。",
            "",
            "这棵树由**分类元数据**派生，不由文件位置派生 —— 所以它可以被重新组织"
            "而不需要移动任何文件。",
            "",
            table(["family"], [[f] for f in (data.get("families") or [])]),
        ]), 3))
    else:
        parts.append(section("导航 DAG", failed("`taxonomy tree`", code, err), 3))

    parts.append(section("两大家族视图", _families(ability_repo), 3))

    code, data, err = run(["serverosctl", "device", "show", "--json"])
    parts.append(section("这台设备", json.dumps(data, ensure_ascii=False, indent=2)
                         if data else failed("`device show`", code, err), 3))
    return "\n".join(parts)


def _families(ability_repo):
    """The two-family navigation view, rendered by ability-management.

    It is not reimplemented here. The families are a property of the catalogue,
    and a second renderer would be a second answer to 'what belongs where' — the
    drift this repository removes rather than tolerates."""
    if not ability_repo:
        return ("未提供 `--ability-repo`，因此这一节没有答案。它**不是空的**：家族视图由"
                "ability-management 渲染，ServerOS 侧不复制那份逻辑。用 "
                "`--ability-repo <canonical clone>` 重新渲染以获得答案。")
    script = Path(ability_repo) / "scripts" / "render_capability_families.py"
    if not script.is_file():
        return (f"`{script}` 不存在，因此这一节没有答案。家族视图的渲染器归 "
                f"ability-management，没有它就没有这一节。")
    proc = subprocess.run([sys.executable, str(script), "--stdout"], cwd=str(ability_repo),
                          capture_output=True, text=True, timeout=120, env=_env())
    if proc.returncode != 0:
        return failed(f"`{script} --stdout`", proc.returncode,
                      proc.stderr.splitlines()[0] if proc.stderr.splitlines() else "")
    return (f"渲染自 `{ability_repo}` 的 `scripts/render_capability_families.py`。"
            f"该渲染器有 `--check`，会因漂移而失败。\n\n{code_block(proc.stdout)}")


def _card(verb, title, what):
    code, data, err = run(["serverosctl", "card", verb, "--json"])
    if not data:
        return section(title, failed(f"`card {verb}`", code, err), 2)
    _, text, _ = out(["serverosctl", "card", verb])
    return section(title, "\n".join([
        what,
        "",
        "**人看到的（窄屏）**", "",
        code_block(text or "(文本预览为空)"),
        "",
        "**同源的飞书 card JSON** —— `schema: "
        f"{data.get('schema')}`，标题模板 `{(data.get('header') or {}).get('template')}`，"
        f"元素 {len(((data.get('body') or {}).get('elements') or []))} 个。",
        "",
        code_block(json.dumps(data, ensure_ascii=False, indent=2), "json"),
        "",
        "**未投递。** 这一节由 `card` 渲染，而 `card` 不持有任何 transport。"
        "真实投递是 [DEPLOYMENT.md](DEPLOYMENT.md) 里的 P3/P5，需要单独批准。",
    ]), 2)


def doc_status(when):
    return _card("status", "状态卡预览",
                 "三块加一行 env-health。共享网关只在有 service binding 指名时才出现，"
                 "所以没出现不等于它健康。")


def doc_alert(when):
    return _card("alert", "告警卡预览",
                 "最多三条事件，每条带「没放进来多少」的计数。一个未放进的计数比一条"
                 "被挤掉的告警更有用 —— 后者看起来像没有发生。")


def doc_recovery(when):
    return _card("recovery", "恢复卡预览",
                 "只讲**被通知过**、且此后恢复了的。一次没人被告知过的解决不出现在这里："
                 "那不是恢复，那是没发生过。")


def doc_coverage(when, projects):
    code, board, err = run(["serverosctl", "work", "board", "--json"])
    parts = [
        "磁盘三级地图、三个飞书面、关注源清单，各带分母。分母是这一页最重要的部分："
        "「13/17」和「13」是不同的两句话。", ""]
    for project in projects:
        c, d, e = run(["serverosctl", "work", "coverage", "--project", project, "--json"])
        if not d:
            parts.append(section(project, failed(f"`work coverage --project {project}`", c, e), 3))
            continue
        disk = d.get("disk") or {}
        cov = disk.get("coverage") or {}
        fs = d.get("feishu_spaces") or {}
        fol = d.get("followed_sources") or {}
        und = d.get("understanding") or {}
        parts.append(section(f"{project}", "\n".join([
            f"**磁盘**：{cov.get('present')}/{cov.get('names')} 存在，"
            f"缺 {cov.get('missing')}，不可读 {cov.get('unreadable')}，未绑定 {cov.get('unbound')}"
            f"（状态 `{disk.get('state')}`）。",
            "",
            table(["kind", "name", "path", "state"],
                  [[x.get("kind"), x.get("name"), f"`{x.get('path')}`", x.get("state")]
                   for x in (disk.get("entries") or [])]),
            "",
            f"**三个飞书面**：分母 {fs.get('denominator')}，状态 `{fs.get('state')}`，"
            f"authored sources 存在：`{fs.get('authored_sources_present')}`。",
            "",
            table(["surface", "state", "why"],
                  [[s.get("surface") or s.get("id") or s.get("name"), s.get("state"),
                    (s.get("why") or "")[:120]] for s in (fs.get("surfaces") or [])]),
            "",
            f"**关注源**：状态 `{fol.get('state')}`，"
            f"清单文件 `{d.get('sources_file')}`（`{d.get('sources_state')}`）。",
            "",
            f"> {d.get('sources_why')}",
            "",
            f"**认识层**：根 `{und.get('root')}`，分母「{und.get('denominator')}」，"
            f"状态 `{und.get('state')}`。",
            "",
            table(["scope", "documents", "带 Sources", "state"],
                  [[s.get("scope"), s.get("documents"), s.get("with_sources"), s.get("state")]
                   for s in (und.get("scopes") or [])]),
        ]), 3))
    if board:
        t = board.get("today") or {}
        parts.append(section("覆盖页读出来的一个事实", "\n".join([
            f"`work board` 报 `today.state = {t.get('state')}` —— {t.get('why')}。",
            "",
            f"看板计数：{json.dumps(board.get('counts') or {}, ensure_ascii=False)}。",
        ]), 3))
    return "\n".join(parts)


def doc_understanding(when, projects):
    code, data, err = run(["serverosctl", "work", "skeleton", "understanding", "--json"])
    parts = [
        "关注源和认识变化。这一页把两件事分开：**关注什么**（源清单）和**知道了什么**"
        "（认识层的四类卡片）。", ""]
    if data:
        parts.append(section("认识层的四个 scope（骨架声明的形状）",
                             code_block(json.dumps(data, ensure_ascii=False, indent=2), "json"), 3))
    else:
        parts.append(section("认识层骨架", failed("`work skeleton understanding`", code, err), 3))
    parts.append(section("变化", "\n".join([
        "**这一栏目前是 PARTIAL，原因具名：** 没有一处在读「今天知道了什么、昨天不知道」。",
        "认识层有卡片和 Sources 溯源，但**没有 delta 产生者** —— 一份卡片被改动过，"
        "没有一个东西会把它读成一次变化。",
        "",
        "要把它做成真的，需要的是：读 `records/information/understanding/` 下每份文件的"
        "当前内容，和一个在此之前被固定下来的快照做差。前者存在，后者不存在。",
        "",
        "在这一栏变成一张真实列表之前，它不假装自己有内容。",
    ]), 3))
    return "\n".join(parts)


def doc_board(when):
    code, data, err = run(["serverosctl", "requirement", "board", "--json"])
    parts = []
    if data:
        c = data.get("counts") or {}
        parts.append(section("跨项目需求看板", "\n".join([
            f"Item {c.get('items')} 个：活跃 {c.get('active')}，已完成 {c.get('completed')}，"
            f"没有卡的 {c.get('without_a_card')}。",
            f"优先级：`{(data.get('priority') or {}).get('state')}` —— "
            f"{(data.get('priority') or {}).get('because')}",
            "",
            table(["id", "project", "kind", "outcome", "priority", "runs"],
                  [[i.get("id"), i.get("project"), i.get("kind"), i.get("outcome"),
                    i.get("priority"), i.get("run_count")] for i in (data.get("items") or [])]),
        ]), 3))
    else:
        parts.append(section("需求看板", failed("`requirement board`", code, err), 3))

    code, data, err = run(["serverosctl", "work", "board", "--json"])
    if data:
        parts.append(section("个人看板（§6 的分节与顺序）", "\n".join([
            f"读数时间 `{data.get('at')}`。",
            "",
            table(["section", "rows", "划线", "为什么在这一节"],
                  [[s.get("title"), len(s.get("rows") or []),
                    "是" if s.get("struck_through") else "否",
                    s.get("because")] for s in (data.get("sections") or [])]),
            "",
            "**顺序是判据的一部分。** 看板先于单项 brief：一个候选必须先进看板，之后才"
            "可能有一份 brief。反过来做，看板就成了「已经有人开始做的事」的列表。",
        ]), 3))
    else:
        parts.append(section("个人看板", failed("`work board`", code, err), 3))
    return "\n".join(parts)


def doc_today(when):
    code, data, err = run(["serverosctl", "work", "board", "--json"])
    t = (data or {}).get("today") or {}
    body = [
        "今天实际在推的东西。这一页的输入是 `today.yaml`，而它**不是从 Item 状态猜出来的** ——"
        "它是一个人的选择。", "",
        section("读数", "\n".join([
            f"`today.state = {t.get('state')}`",
            f"`today.why = {t.get('why')}`",
            f"`today.items = {json.dumps(t.get('items'), ensure_ascii=False)}`",
            f"按项目：{json.dumps(t.get('by_project') or {}, ensure_ascii=False)}",
            "",
            "**这个读数是这一轮的一个真实缺口，不掩饰：** 有四个 ACTIVE 的 Item 在飞，"
            "而 `today.yaml` 里没有任何一次选择被记下来。看板不能代替选择 —— 一页列出"
            "所有在做的事，和「今天做这两件」是不同的两句话。",
        ]), 3),
        section("为什么这一页现在写成选择", "\n".join([
            "`serverosctl work today` 是这组动词里**唯一会写**的一个，而它写的就是一次选择："
            "不碰任何 Item 状态。这是刻意的 —— 一个由状态派生出来的「今天」，会在状态变化的"
            "那一刻悄悄变掉，而人已经照着它安排了一天。",
        ]), 3),
    ]
    return "\n".join(body)


def doc_item_chain(when, project, item_id, item_arg):
    code, data, err = run(["serverosctl", "work", "item", item_arg,
                           "--project", project, "--json"])
    parts = []
    if data:
        parts.append(section(f"{item_id} 的文档链",
                             code_block(json.dumps(data, ensure_ascii=False, indent=2), "json"), 3))
    else:
        parts.append(section(f"{item_id} 的文档链",
                             failed(f"`work item {item_arg} --project {project}`", code, err), 3))
    for name in ("brief", "design", "plan", "report"):
        c, d, e = run(["serverosctl", "work", "skeleton", name, "--json"])
        if d:
            parts.append(section(f"{name} 的形状", "\n".join([
                "这是**提议的形状**，不是对任何现有文档的判决：检查器报的是 DIVERGENCE，"
                "带文档自己的小节标题，而不是因为没用这些标题就说文档坏了。",
                "",
                code_block(json.dumps(d, ensure_ascii=False, indent=2), "json"),
            ]), 3))
        else:
            parts.append(section(f"{name} 的形状", failed(f"`work skeleton {name}`", c, e), 3))
    return "\n".join(parts)


def doc_decisions(when):
    rows = [[d["id"], d["state"], d["chose"], d["rejected"], d["why"]] for d in SRC.DECISIONS]
    return "\n".join([
        "这一轮的取舍。`state` 为 OPEN 的条目会出现在 [REPORTS.md](REPORTS.md) 的 "
        "Decision Packet 里 —— 一个还开着的决定，是外面的人唯一能真正帮上忙的东西。", "",
        table(["id", "state", "选了", "没选", "为什么"], rows), "",
        "*本页内容是作者的输入，不是任何命令的产物。见 `runtime/workbench_sources.py`。*",
    ])


def doc_reports(when, quay_run, reports_dirs=None):
    open_decisions = [d for d in SRC.DECISIONS if d["state"] == "OPEN"]
    parts = [
        "日报，以及给外部读者的 Decision Packet。", "",
        section("日报", "\n".join(_reports(quay_run, reports_dirs)), 3),
        section("Decision Packet", "\n".join(_packet(open_decisions)), 3),
    ]
    return "\n".join(parts)


def _packet(open_decisions):
    lines = []
    for d in open_decisions:
        lines += [f"**{d['id']}** — {d['chose']}", "",
                  f"- 为什么它还没定：{d['why']}",
                  f"- 被否掉的另一条：{d['rejected']}",
                  f"- 定了会改什么：这一条一旦定下，`runtime/workbench_sources.py` 里对应"
                  f"条目的 `state` 从 OPEN 变成 CLOSED，本页随之少一条 —— 没有别的动作。",
                  ""]
    return lines or ["没有开着的决定。一个「都定了」的清单如果不是真的，比一条开着的决定"
                     "更贵：它让下一个人以为不用再问。"]


def _reports(quay_run, reports_dirs=None):
    """Every `*daily-report*` file under the places a run leaves one.

    `--reports-dir` is an explicit input rather than a default, for the reason
    every other input here is: a default naming one round's scratch tree renders
    a section out of whatever happens to still be on this disk, on this node
    only. Without it the section says it found nothing and names where the
    round's report actually went, which is the honest rendering of not having
    been told — the section is empty, not green."""
    found = []
    roots = [ROOT / "docs", ROOT / "workbench"]
    if quay_run:
        roots.append(Path(quay_run))
    roots += [Path(d) for d in (reports_dirs or [])]
    for root in roots:
        if not root.is_dir():
            continue
        for p in sorted(root.rglob("*daily-report*")):
            if p.is_file() and p.suffix in (".md", ".yaml", ".json"):
                found.append(f"- `{p}` （{p.stat().st_size} bytes）")
    if not found:
        # No round's filename here. A shipped renderer that points at one round's
        # scratch file is a pointer that is wrong on every other node and after
        # every other round — the same defect as a shipped default naming a run
        # directory, and it reads as help rather than as a stale string.
        return ["没有找到任何日报文件。**这一栏因此是空的，不是绿的。**",
                "",
                "日报由每一轮留在它自己的运行目录里，本仓不含它们，所以这里搜的是 "
                "`<repo>/docs`、`<repo>/workbench` 与 `--quay-run`。把某一轮的目录用 "
                "`--reports-dir <dir>` 指过来即可；搜的是其中名为 `*daily-report*` 的文件。"]
    return found


def doc_quay(when, quay_run, quay_bin=None):
    """Quay Goal/Task correspondence and the execution results read back."""
    if not quay_run:
        return ("未提供 `--quay-run`，因此这一节没有答案。它**不是空的**：Quay 的结果"
                "在 Quay 的 store 里，不在本仓。用 `--quay-run <dir>` 重新渲染。")
    qrun = Path(quay_run)
    ws = qrun / "ws"
    parts = [
        "父工作 → Goal → AC → Task → 执行 → 结果。这一页只印**回读**到的东西："
        "status、GateEvent、退出码。没有「创建成功」这种话。", "",
    ]
    # NO EARLY RETURN when the binary is missing. The ledger and the logs below
    # are FILES in the run directory; they need no Quay CLI, and returning here
    # would drop two sections a person can still read. A page that omits what it
    # could not reach reads as a page with nothing to report — the failure this
    # module's own docstring refuses, committed here in the first version of the
    # flag that removed the baked-in path.
    Q = _quay_binary(quay_bin)
    if not Q:
        parts.append(failed(
            "定位 quay 可执行文件", 127,
            "没有指定 Quay CLI。这一页问的是 Provider 的 store，而 Provider 的构建产物"
            "在哪是这一台机器的事，不是本仓的事——所以这里不猜。用 "
            "`--quay-bin <packages/quay/dist/quay.js>`（或 `SERVEROS_QUAY_BIN`）指一下。"))
        return "\n".join(parts + _quay_run_dir_sections(ws, qrun))

    def q(args):
        """One of the goal store's readings, asked for as JSON.

        `--json` is passed explicitly, and that is the whole of the fix: the first
        version did not pass it and then did `json.loads(proc.stdout)` on the TSV
        `goal list` prints by default, so a store that answered perfectly well
        rendered as "没有答案" in both sections. The page was right that this is
        not the same as empty — the cause was here, and it is the defect this
        round keeps recording under other names: a reader assuming a shape it
        never checked.

        The failure branch reports stdout as well as stderr, for the same reason.
        When the parse fails, the thing that failed to parse is in STDOUT; a
        message built from stderr alone would print the node banner and nothing
        about the cause.
        """
        proc = subprocess.run(["node", str(Q), *args, "--json", "--root", str(ws)],
                              capture_output=True, text=True, timeout=180, env=_env())
        try:
            return proc.returncode, json.loads(proc.stdout), proc.stderr
        except Exception as exc:
            return proc.returncode, None, (
                f"{exc}；stdout 开头 {proc.stdout[:200]!r}；"
                f"stderr 末尾 {proc.stderr[-200:]!r}")

    code, goals, err = q(["goal", "list"])
    if goals is None:
        parts.append(section("Goal 记录", failed("`quay goal list`", code, err), 3))
    else:
        goals = goals if isinstance(goals, list) else goals.get("goals") or []
        parts.append(section("Goal / AC 记录", table(
            ["id", "kind", "status", "goal", "title / criterion"],
            [[g.get("id"), g.get("kind"), g.get("status"), g.get("goal"),
              (g.get("title") or g.get("criterion") or "")[:110]] for g in goals]), 3))

    code, tasks, err = q(["task", "list"])
    if tasks is None:
        parts.append(section("Task 记录", failed("`quay task list`", code, err), 3))
    else:
        items = tasks if isinstance(tasks, list) else tasks.get("tasks") or []
        parts.append(section("Task 记录", table(
            ["id", "status", "goal-ac", "title"],
            [[t.get("id"), t.get("status"), t.get("goal_ac") or t.get("goalAc"),
              (t.get("title") or "")[:90]] for t in items]), 3))

    return "\n".join(parts + _quay_run_dir_sections(ws, qrun))


def _quay_run_dir_sections(ws, qrun):
    """The two sections that read FILES in the run directory.

    Split out because they need no Quay CLI, and the first version of
    `doc_quay` returned before reaching them when `--quay-bin` was absent —
    dropping a readable ledger and readable logs because a different section
    could not be filled. Which section could not be filled is a fact about that
    section; this is the same rule as `failed` and `unreadable`, applied to the
    page rather than to a file."""
    parts = []

    ledger = ws / ".quay" / "gate-events.jsonl"
    if ledger.is_file():
        try:
            events = [json.loads(ln) for ln in ledger.read_text().splitlines() if ln.strip()]
        except (OSError, ValueError) as exc:
            # An unreadable ledger and a malformed one are different findings
            # and neither is "the ledger is empty". Both render here, on this
            # page, rather than as a traceback that leaves the other fourteen
            # documents unwritten because the fifteenth could not open a file.
            events = None
            parts.append(section("GateEvent 账本", unreadable("GateEvent 账本", exc), 3))
        if events is not None:
            # The columns are the ledger row's OWN fields. The first version
            # printed `at` and `exit`, which no row has — a table of two empty
            # columns beside three populated ones. The row carries `timestamp`
            # and, under `payload`, the `reason`; `reason` is the column that
            # matters, because this shell requires a failing criterion to say
            # why on the same line, so a red row is readable here without the
            # log beside it.
            parts.append(section(f"GateEvent 账本（{len(events)} 条）", table(
                ["at", "record", "gate", "actor", "verdict", "reason"],
                [[(e.get("timestamp") or "")[:19], e.get("item_id") or e.get("record"),
                  e.get("gate"), e.get("actor"), e.get("verdict"),
                  ((e.get("payload") or {}).get("reason") or "")[:140]] for e in events]), 3))
    else:
        parts.append(section("GateEvent 账本", f"`{ledger}` 不存在。", 3))

    for extra in sorted(qrun.glob("*.log")):
        try:
            tail = extra.read_text()[-6000:]
        except OSError as exc:
            # `qrun` is a directory somebody points this at, and a plain
            # directory on a shared host holds other actors' files. Refusing to
            # render the whole set because one of them is mode 600 would make
            # this page's usefulness depend on the permissions of a directory
            # it does not own.
            parts.append(section(f"运行日志 {extra.name}",
                                 unreadable(f"运行日志 {extra.name}", exc), 3))
            continue
        parts.append(section(f"运行日志 {extra.name}", code_block(tail), 3))
    return parts


def unreadable(what, exc):
    """A file this page could not read.

    Never a traceback, never a dropped section, and never an empty one: a page
    that silently omits what it could not read is indistinguishable from a page
    with nothing to report, which is the one confusion every document here is
    written to prevent."""
    return (f"**{what}：读不了。** `{type(exc).__name__}: {exc}`\n\n"
            f"这一节因此是 FAILED，不是空：读不了和读到空的是两件事。\n")


def _quay_binary(given=None):
    """Where the Quay CLI is, from the caller or from `SERVEROS_QUAY_BIN`.

    NO SEARCH, AND NO DEFAULT PATH. The first version of this function listed
    two absolute paths under one home directory — the Quay worktrees this round
    happened to build in. That is the defect `--quay-run`'s own comment refuses
    forty lines up ("a shipped default naming one round's scratch tree is a path
    that means nothing on any other node"), committed in the same file, inside
    the surface that ships.

    AC-004 does not catch it, and that is worth being precise about rather than
    quietly widening: AC-004's criterion is "产物的 `runtime/` 与 `bin/` 里没有
    出现**构建它的**工作树路径", which is the ServerOS build worktree — and a
    Quay worktree is a different path. The rule it is an instance of is the one
    being applied here, and a criterion that names one path cannot enforce it.

    So which binary answers is a fact about THIS NODE, and it is supplied the way
    every other node-specific input in this module is: by a person. Absent
    means the section renders as FAILED with the flag that would fill it, which
    is the same shape as `--quay-run` and `--ability-repo`."""
    for cand in (given, os.environ.get("SERVEROS_QUAY_BIN")):
        if cand and Path(cand).is_file():
            return Path(cand)
    return None


def doc_manifest(when, release_root, pins):
    """RELEASE-MANIFEST — the pins, the digests, and whether each pin is reachable.

    §6 of the plan puts the reachability check in the same sentence as the pins
    ("要验证所有 pin 可达"), because a manifest naming a revision nobody can fetch
    is a manifest that reads as a release and resolves as nothing.

    §24 of ACCEPTANCE asks for more than that in one sentence, and this page
    used to deliver only its first third:

        release manifest 固定所有代码/能力/策略/adapter revision 与 hash、
        schema 版本和恢复信息。

    Read as a list, that is four things: every class of revision (a manifest
    that pins the code and silently omits the ability source, the ops repo and
    the adapter it ships is COMPLETE-LOOKING and incomplete), a hash per
    revision, the schema versions the release speaks, and what recovery
    actually restores. Three of the four were absent, and their absence was
    invisible for the same reason every absence here is: a five-row table with
    every row green does not look like a table with three classes missing from
    it. The missing rows are the ones nobody could see were missing."""
    rows = []
    seen: dict = {}
    for pin in pins:
        rows.append(_reachable(pin, seen=seen))
    body = [
        "产物摘要与代码/能力/**策略**/**adapter** revision 固定在这里，并且**每一条 pin 都被验证过可达**。",
        "",
        "跨 repo 不是原子事务：这个文件不声称它们是。它声称的是，每一半单独可达，"
        "并且没有任何一半是在未验证的情况下被写进 active pointer 的。",
        "",
        section("pin 与可达性（两列，两个问题，互不冒充）", "\n".join([
            table(["名字", "repo", "revision", "本地对象",
                   "revision_remote_reachable", "designated_release_ref"], rows), "",
            "**左边一列问「这个 revision 从远端取不取得到」** —— 注意它问的是"
            "revision，不是分支名。它把远端 advertise 的**全部 ref** 取回来"
            "（`git ls-remote --heads --tags`），先用 `git cat-file --batch-check` 留下"
            "本地**真的持有**的那些，再用一次 "
            "`git rev-list <pin> --not <所有能判定的 ref>` 定案：空输出就是可达。"
            "**它不读任何手写字段**，也不打印 ref 名 —— 这一页是"
            "整页比对的，而「包含某个 revision 的 ref 集合」会随别人建分支而增长，"
            "ref 名写进格子里会让这一页因为一件与 release 无关的事变红。"
            "ref 的归属是右边那一列的事。",
            "",
            "这一列**刻意不用 `--ignore-missing`**，理由是本机实测的：对一个克隆里"
            "没有的对象，`git rev-list --ignore-missing <sha>` **退出码 0 且输出为空**"
            "（那个 flag 的意思是「打不开的跳过」），而空输出正是本列读作可达的东西。"
            "那个 flag 看起来像宽容，实际是一处假绿：一个**哪里都不存在**的 revision "
            "会被印成同一行里「本地对象=不存在」旁边写着「可达」——这不是舍入误差，"
            "是一行内部自相矛盾。去掉它，同一条命令对同一个输入退出 128，拒绝作答"
            "才是这里诚实的答案；而 `--not` 名单里的 ref 已经预先筛成本地持有的对象，"
            "那个 flag 在这条路径上本来也没买到任何东西。",
            "",
            "**右边一列问「这个 release 声明自己在哪个 ref 发布，那个 ref 含不含它」**"
            " —— 它是关于**声明**的一句话，不是关于 release 真假的一句话。"
            "没声明发布点的行写 `未指定` 而不是画一个 `—`：**未声明是一个结论**，"
            "而 `—` 是「没有结论」被渲染成「没有发现」的样子。",
            "",
            "把这两问压进同一格，两个方向会同时出错，而本轮两个都发生了："
            "一个真的发布在别的 ref 上的 revision 会被读成**悬空**（四仓里有三仓的 "
            "pin 一直在各自默认分支上，列却写 `远端不存在`，因为那个 pin 点名的分支"
            "那些仓从来没建过）；而**事后补一个同名分支**就能让它变绿、release 本身"
            "什么都没变 —— 本轮正是这么做的，`serveros/system-v1-20260920` 在四个"
            "远端被创建，其中三个的 tip 本来就是那个远端的 HEAD，于是页面读成"
            "「五条 pin 全部可达」，那是一句关于**名字**的话被当成了一句关于**发行**的话。",
            "",
            "判读：`不可达` / `未判定` / `远端未答` 是这一页的 FAIL —— 最后一个尤其"
            "不要读成前两个，**远端没答和远端答了「没有」是两条不同的发现**。"
            "`未指定` 是缺口，不是 FAIL。`不含 pin` 也不是 FAIL：它是"
            "「可达，但发布点在别处」，而这件事只有在两列分开之后才首次说得出来。",
        ]), 3),
        section("revision 与 hash（每一类都要有）", "\n".join([
            table(["名字", "类别", "revision", "树 hash"], _identity_rows(pins)), "",
            "**hash 是 git 自己对那棵树的散列**（`git rev-parse <rev>^{tree}`），"
            "不是这里另算的第二个数。两个人为同一次 revision 各算一个摘要，"
            "迟早会有一个不再匹配，而那时没有任何东西会报错 —— 所以这里印的是 git 的。",
            "",
            "树 hash 覆盖的是**树**：它证明两个 revision 的内容相同或不同，"
            "它不覆盖 packfile、远端配置或工作树里未提交的东西。一行写着 hash 的"
            "清单很容易被读成「整台机器都被验证过了」，所以这一行说的正是它不覆盖什么。",
            "",
            "**类别这一列是这一节存在的理由。** 一份只固定了 code 的清单，"
            "和一份固定了全部四类的清单，在没有这一列的时候长得一模一样："
            "读者只能看见表里有什么，看不见表里**该有什么**。策略与 adapter 不是"
            "代码的附属物 —— 它们各自有自己的 revision，也各自能单独地过时。",
        ]), 3),
        section("schema 版本", "\n".join([
            _schema_versions(release_root), "",
            "从 shipped tree 里**读出来**的，不是在这里重复一遍的：每个 "
            "`runtime/*.yaml` 抬头声明的 `schema:`，加上 plugin composition 声明的 "
            "bundle schema。一份手抄的 schema 清单会在有人改 schema 的那一天"
            "开始说旧话，而那一天不会有任何东西变红。",
        ]), 3),
        section("隔离安装（本轮的 promote 目标）", "\n".join([
            f"runtime root：`{release_root}`",
            "",
            code_block(_release_status(release_root)),
            "",
            "这两行就是 AC-003 断言的那一条：`previous` 不是一句承诺，而是一个**真的还能启动**"
            "的 revision。本页读的是这个隔离 root **自己的结构**，没有去调构建工作树里的 "
            "`release.py` —— 那个脚本不进产物，shipped renderer 依赖它，会让这一页在源码目录里"
            "好看、在任何已安装节点上抛错。",
            "",
            "**这不是线上 runtime root。** 这一轮的 promote 落在运行产物目录下的隔离 root，"
            "线上指针没有动过。线上切换是 [DEPLOYMENT.md](DEPLOYMENT.md) 的 P1。",
        ]), 3),
        section("恢复信息", "\n".join([
            "回滚**恢复的是 revision 指针，不是状态**。这两件事必须分开写下来，"
            "因为一句「可以回滚」在读者那里同时意味着两件事，而其中只有一件是真的。",
            "",
            code_block("\n".join([
                "回滚恢复：",
                "  <runtime root>/current  →  <runtime root>/previous",
                "  以及随它一起被引用的那棵 shipped tree",
                "",
                "回滚不恢复、也不该恢复：",
                "  state root       —— 授权、工作记录、receipt、观察结果都在这里",
                "  worktree / 制品   —— 回滚不动任何人的工作树",
                "  Quay 的工作区     —— 闸门账本属于那一轮，不属于这次发行",
            ])),
            "",
            "**状态不在 revision 里，这正是回滚能救回工作的前提。** 如果授权与 receipt "
            "是随 revision 一起发的，那么一次回滚就会把它们一起倒回去 —— 于是"
            "「回滚到上一个可用版本」会顺手丢掉期间产生的批准，而这件事不会表现为"
            "一次失败，只会表现为批准变少了。`state` 目录落在 runtime root 之外，"
            "由 `SERVEROS_STATE_DIR` 指向，回滚不碰它。",
            "",
            "所以恢复的完整性检查不是「指针动没动」，而是回滚之后去 state root 里"
            "读一次授权、读一次 receipt：**它们还在，才叫恢复了。**",
        ]), 3),
    ]
    return "\n".join(body)


def _identity_rows(pins):
    """One row per pin: what class it is, and git's own hash of its tree.

    A pin whose revision cannot be resolved gets `—` in the hash column rather
    than an empty cell, for the reason `_reachable` gives: an empty cell and a
    hash that could not be computed look the same in a table.
    """
    import subprocess as sp
    rows = []
    for pin in pins:
        src = Path(pin["repo"])
        kind = pin.get("kind") or "—"
        tree = "**读不了 repo**"
        if (src / ".git").exists() or (src / "HEAD").exists():
            c = sp.run(["git", "-C", str(src), "rev-parse",
                        f"{pin['revision']}^{{tree}}"],
                       capture_output=True, text=True)
            tree = c.stdout.strip()[:12] if c.returncode == 0 else "**该 revision 不在这个 repo 里**"
        rows.append([pin["name"], kind, pin["revision"], tree])
    return rows


def _schema_versions(release_root):
    """Every `schema:` the shipped release declares, read from the artifact.

    Two sources, both of them the artifact's own words: the shipped
    `runtime/*.yaml` headers, and the plugin composition's `bundle.v1`.

    When the release root is not there, this says so. It does not fall back to
    reading the CHECKOUT this renderer is running from — that would report the
    schemas of the tree being built as the schemas of the tree being shipped,
    which are the same thing right up until the build is stale, and then it is
    a wrong answer wearing a right one's clothes.
    """
    # `--release-root` has no default, so "not given" is the ordinary case and
    # it has to render like every other absent input rather than raise. This
    # reached `Path(None)` on the first run and took the whole document set down
    # with it — one input's absence is never a reason to stop writing the set.
    if not release_root:
        return ("没有给 `--release-root`，所以这一节**没有读**。"
                "这不是「这个发行版没有 schema」——是没有人指出要读哪一个产物。")
    root = Path(release_root)
    current = root / "current"
    if not current.exists():
        return (f"`{current}` 不存在，所以这一节**读不到 shipped tree 的 schema**。"
                f"这不是「这个发行版没有 schema」，是这一次没有读成 —— "
                f"没读成和读到零在这一页里必须是两句话。")
    try:
        shipped = current.resolve()
    except OSError as exc:
        return unreadable("shipped tree", exc)
    rows = []
    for path in sorted((shipped / "runtime").glob("*.yaml")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            rows.append([path.name, unreadable("这一份", exc).splitlines()[0]])
            continue
        declared = _top_level(text, "schema")
        if declared:
            rows.append([path.name, declared])
    # The composition files are listed whether or not they are there. They were
    # skipped when absent, and that is the defect this whole page is about: a
    # section that silently omits a source it did not find is indistinguishable
    # from a section whose source had nothing to declare. `plugin/` is NOT in
    # the runtime artifact — the plugin is a separate build from the same tree
    # (`plugin/bundle.yaml` says so, and `~/.claude/plugins/` holds the other
    # one) — so this row is absent on every correct install, and the page has to
    # say that rather than look like it never looked.
    for rel in ("plugin/bundle.yaml", "plugin/profiles/linux-work.yaml"):
        path = shipped / rel
        if not path.is_file():
            rows.append([rel, ("**不在这个产物里** —— `plugin/` 是与 runtime "
                               "分开的另一次构建，不随本产物发行")])
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            rows.append([rel, unreadable("这一份", exc).splitlines()[0]])
            continue
        composition = _top_level(text, "composition") or "?"
        rows.append([f"{rel}（composition {composition}）",
                     _top_level(text, "schema") or "**没有声明**"])
    if not rows:
        return f"`{shipped}` 里没有读到任何 `schema:` 声明。"
    return table(["文件", "schema"], sorted(rows))


def _top_level(text: str, key: str):
    """A `key: value` at column 0, without a YAML parser.

    Read with a regex on purpose. This renderer ships inside the artifact, and
    two of the things it must not do are: depend on a module the artifact may
    not carry, and depend on one whose VERSION changes what parses. PyYAML is
    both. `schema:` and `composition:` are single scalars at column 0 in every
    file this reads — a mapping or a list would need a parser, and would also
    be a different claim from the one this section makes.
    """
    match = re.search(rf"^{re.escape(key)}:[ \t]*(\S.*?)[ \t]*$", text, re.MULTILINE)
    return match.group(1) if match else None


def _reachable(pin, *, seen=None):
    """One pin's row: TWO columns, because there are two questions.

    This row used to answer one question and print it as the other. The single
    evidence column ran `git ls-remote --heads origin <pin["branch"]>` — it
    asked whether a branch NAME, typed by hand into `workbench_sources.PINS`,
    existed on the remote, and the page read the answer as "this pin is
    reachable". Two failures followed, in opposite directions, and this round
    produced both:

      * A revision genuinely published under another ref read as 悬空. Three of
        the four repositories in this round had their pin on the remote's own
        default branch the whole time; the column said `远端不存在` because the
        pin named a branch those repositories had never created.
      * Creating the missing branch turned the column green while publishing
        nothing. This round did exactly that — `serveros/system-v1-20260920`
        was created on all four remotes and on three of them its tip was
        already that remote's HEAD. The page then read「五条 refs pin 全部
        ⊇ pin」, which is a statement about a NAME presented as a statement
        about a release.

    So the two questions are separated, and neither can impersonate the other.
    A branch created after the fact can now change only the column that is
    about that branch.

    Column 1, `revision_remote_reachable` — a property of the REVISION. Asked
    against the refs the remote ADVERTISES, never against a field somebody
    typed. It prints a verdict and never a ref name: this page is compared in
    full, and the set of refs containing a revision grows, so a ref name in
    the cell would rewrite it without anything about the release changing. Ref
    identity belongs to column 2, where a human declared it.

    Column 2, `designated_release_ref` — a property of the DECLARATION. Which
    ref this release says it publishes at, and whether that ref carries the
    pin. A pin that designates nothing says 未指定 instead of rendering a
    dash: an undeclared publication point is a finding, and a dash is how the
    absence of a finding gets rendered as though it were one.

    `seen` memoises the remote answers across pins, for the reason given in
    `_advertised_refs`.
    """
    import subprocess as sp
    src = Path(pin["repo"])
    if not (src / ".git").exists() and not (src / "HEAD").exists():
        # `rel_home` here too. This branch returned the raw path, and the test
        # that renders the whole page and asserts no home prefix passes only
        # because no pin currently names an unreadable repository — the hole
        # was latent, not absent.
        unreadable = "**repo 不可读**"
        return [pin["name"], rel_home(pin["repo"]), f"`{pin['revision'][:12]}`",
                unreadable, unreadable, rel_home(str(src))]
    c = sp.run(["git", "-C", str(src), "cat-file", "-e", f"{pin['revision']}^{{commit}}"],
               capture_output=True, text=True)
    local = "存在" if c.returncode == 0 else "**不存在**"
    reach = _remote_reachable(sp, src, pin, present=c.returncode == 0, seen=seen)
    designated = _designated_ref(sp, src, pin, seen=seen)
    # `rel_home` and not an inline `.replace(<home> + "/", "~/")`, which is what
    # stood here first: the redaction was already written as a function for the
    # whole set, and spelling the home directory out to redo it locally is the
    # same defect the function removes — one string, two copies. The literal is
    # not repeated here even in a comment, because the test below cannot tell a
    # comment from code and a rule that has to be read is not a rule a scanner
    # can hold.
    return [pin["name"], rel_home(pin["repo"]),
            f"`{pin['revision'][:12]}`", local, reach, designated]


def _advertised_refs(sp, src, seen):
    """Every ref the remote advertises, or None when it did not answer.

    `ls-remote` and not `fetch`: this asks the remote a question and writes
    nothing, which is what a check inside a shipped renderer is allowed to do.
    A remote that did not answer and a remote that answered "nothing contains
    it" are different findings, and returning None for the first is what stops
    them from being rendered the same way.

    Memoised per render on the repository, because several pins legitimately
    name the same one — the adapter is built from the revision it adapts, and
    two design packages live in a single repository. Asking once per row is how
    a page that renders in five seconds starts rendering in forty. The memo is
    passed in, never module-global: a cached "this is not on origin" that
    outlived the push that fixed it would be a wrong answer that never expires,
    and this page's whole subject is answers that stopped being true without
    saying so.
    """
    key = ("refs", str(src))
    if seen is not None and key in seen:
        return seen[key]
    r = sp.run(["git", "-C", str(src), "ls-remote", "--heads", "--tags", "origin"],
               capture_output=True, text=True, timeout=180)
    refs = None
    if r.returncode == 0:
        refs = []
        for line in r.stdout.splitlines():
            parts = line.split()
            # `^{}` peels a tag to the object it names. Keeping both would ask
            # the identical ancestry question twice for one tag.
            if len(parts) == 2 and not parts[1].endswith("^{}"):
                refs.append((parts[0], parts[1]))
    if seen is not None:
        seen[key] = refs
    return refs


def _refs_we_hold(sp, src, refs, seen):
    """The advertised refs whose objects this clone actually has.

    Ancestry is a LOCAL computation. A remote can advertise a ref this node has
    never fetched, and `git rev-list --ignore-missing` drops such a ref
    silently — which would turn "no ref we can check contains it" into "no ref
    contains it". Counting the refs we CAN decide against is what keeps those
    two apart.
    """
    key = ("held", str(src))
    if seen is not None and key in seen:
        return seen[key]
    held = []
    if refs:
        p = sp.run(["git", "-C", str(src), "cat-file", "--batch-check"],
                   input="\n".join(sha for sha, _ in refs) + "\n",
                   capture_output=True, text=True)
        present = {f[0] for f in (line.split() for line in p.stdout.splitlines())
                   if len(f) > 1 and f[1] == "commit"}
        held = [(sha, ref) for sha, ref in refs if sha in present]
    if seen is not None:
        seen[key] = held
    return held


def _remote_reachable(sp, src, pin, *, present, seen):
    """Column 1 — is this REVISION obtainable from the remote at all.

    One `rev-list <pin> --not <every ref we hold>` answers it for the whole
    set: empty output means the pin is reachable from at least one of them.
    The per-ref `merge-base --is-ancestor` loop this replaces was correct and
    paid a subprocess per advertised ref, on a page that already shells out
    once per pin.

    **`--ignore-missing` must not be used here, and this is the reason it is
    absent.** Measured on this node: `git rev-list --ignore-missing <sha>` for
    an object the clone does not have exits **0 with empty output**, because
    the flag means "skip what you cannot open" — and empty output is exactly
    what this function reads as 可达. The flag looks like tolerance and is a
    false green: a revision that exists nowhere would have been printed as
    `本地对象=不存在` beside `revision_remote_reachable=可达`, which is not a
    rounding error but a contradiction inside one row. Without the flag the
    same command exits 128 for the same input, and refusal is the honest
    answer. Every ref in the `--not` list is pre-filtered to objects this
    clone holds, so the flag was never buying anything here either.

    `present` is the readability of the PIN's own object, already computed for
    the row's `本地对象` cell. Passing it in keeps this function from having to
    re-ask, and it lets the refusal name the real cause.
    """
    refs = _advertised_refs(sp, src, seen)
    if refs is None:
        return "**远端未答** —— 不是「不可达」，是这次没问到"
    held = _refs_we_hold(sp, src, refs, seen)
    if not held:
        return "**未判定** —— 远端答了，但它 advertise 的对象本地一个都没有"
    if not present:
        return ("**未判定** —— 本地没有这个 revision 的对象，"
                "「它是不是被某个 ref 包含」无从问起")
    r = sp.run(["git", "-C", str(src), "rev-list",
                pin["revision"], "--not", *[sha for sha, _ in held]],
               capture_output=True, text=True)
    if r.returncode != 0:
        return "**未判定** —— rev-list 拒绝了这个 revision，可达性无从判定"
    if not r.stdout.strip():
        # A positive needs no caveat: containment in ANY held ref is a proof of
        # reachability, and consulting fewer refs cannot weaken a proof. The
        # caveat belongs on the refusal, where the missing refs could have
        # overturned it.
        return "可达"
    if len(held) < len(refs):
        return (f"**不可达** —— 远端答了，本地持有的 {len(held)} 个 ref 没有一个"
                f"包含它；另 {len(refs) - len(held)} 个 ref 的对象本地没有，"
                f"这一次没判")
    return "**不可达** —— 远端答了，没有任何 ref 包含它"


def _designated_ref(sp, src, pin, *, seen):
    """Column 2 — which ref this release SAYS it publishes at, and does that
    ref carry the pin.

    All three verdicts are about the declaration rather than about whether the
    release is real: `未发布` means the named ref is not on the remote, `⊇ pin`
    means it is and carries this revision, `不含 pin` means it is and does not.
    The last is the state the old single column could not express at all —
    reachable through some other ref while the designated one does not carry it
    — and it is now readable as exactly that, because column 1 answers the
    reachability half independently.
    """
    ref = pin.get("release_ref")
    if not ref:
        if pin.get("publishes") is False:
            return "—（本节点持有，不声称发布）"
        return "**未指定** —— 这一行没有声明发布点"
    refs = _advertised_refs(sp, src, seen)
    if refs is None:
        return f"`{ref}` **远端未答**"
    wanted = {ref, f"refs/heads/{ref}", f"refs/tags/{ref}"}
    head = next((sha for sha, name in refs if name in wanted), None)
    if head is None:
        return f"`{ref}` **未发布**"
    anc = sp.run(["git", "-C", str(src), "merge-base", "--is-ancestor",
                  pin["revision"], head], capture_output=True, text=True)
    if anc.returncode == 0:
        return f"`{ref}` ⊇ pin"
    if anc.returncode == 1:
        return (f"`{ref}` **不含 pin** —— 作为发布点的这个 ref 在远端，"
                f"但这个 revision 从它取不到")
    return (f"`{ref}` 存在（远端 head 的对象不在本地，包含关系未验证）")


def _release_status(release_root):
    """The isolated runtime root, read from its own structure.

    This used to shell out to `scripts/release.py status`. The loader-closure
    gate went red on that, correctly: `runtime/workbench.py` ships inside the
    artifact and `scripts/` does not, so the panel worked from a source checkout
    and would have raised on every installed node — the C4 defect again, one
    directory over, found this time by a gate rather than by an operator.

    Shipping `release.py` INTO the artifact would be the wrong repair. It is the
    tool that swaps `current`, and putting it inside the revision it might have
    to roll back means the broken revision carries the only copy of the way out.
    The release tool belongs beside the root it manages, not inside it.

    Two of `cmd_status`'s readings are deliberately absent here. `unit installed`
    and `cli shim -> ...` name this HOST's systemd unit and bin shim; under a
    caption that says 隔离安装 they would print a live-node fact in the place a
    reader is looking for a fact about the isolated root. Whether the live node
    agrees with itself is a question for a node-level status verb, and it is not
    this page's. What is left is what the root itself can be asked, and it is the
    same pair the round's AC-003 asserts: `current` and `previous` are symlinks,
    and `previous` resolves to a revision that can actually start.
    """
    if not release_root or not Path(release_root).exists():
        return "(隔离 release root 不存在)"
    comp = Path(release_root)

    def reading(name):
        link = comp / name
        if not link.is_symlink():
            return f"{name:<12}  （不是符号链接 —— 没有可回退的 revision）"
        target = link.resolve()
        if not target.is_dir():
            return f"{name:<12}  {target.name}  **指向的目录不存在**"
        daemon = target / "bin" / "serverosd"
        return (f"{name:<12}  {target.name}"
                + ("" if daemon.is_file() else "  **该 revision 里没有 bin/serverosd**"))

    return "\n".join([reading("current"), reading("previous")])


def doc_deployment(when):
    parts = [
        "§8 的最小变更清单。**本页没有任何一条已执行**，措辞上也不允许把待部署写成已上线。", ""]
    for d in SRC.DEPLOYMENT:
        parts.append(section(f"{d['id']} · {d['target']}", "\n".join([
            f"**影响**：{d['effect']}",
            "",
            f"**执行前**：{d['before']}",
            "",
            f"**执行后怎么知道成了**：{d['after']}",
            "",
            f"**回退**：{d['rollback']}",
        ]), 3))
    parts.append(section("这一轮做了什么、没做什么", "\n".join([
        "- **做了**：在隔离 runtime root 上跑通了 promote / status / rollback（见 "
        "[RELEASE-MANIFEST.md](RELEASE-MANIFEST.md)），入口从 `/tmp` 走通。",
        "- **没做**：线上 promote/restart、插件/Hook 覆盖、timer 切换、真实发卡、"
        "启用定时任务、云端写、网络/隧道/容器变更、秘密变更。",
        "- 这条界线由 §8 划定，不由本页自行放宽。",
    ]), 3))
    return "\n".join(parts)


DOC_INDEX = [
    ("START-HERE", "这一页。状态矩阵与四栏判定", "本页"),
    ("ENVIRONMENT", "环境/能力地图", "serverosctl ability tree, taxonomy tree"),
    ("STATUS-PREVIEW", "状态卡预览", "serverosctl card status"),
    ("ALERT-PREVIEW", "告警卡预览", "serverosctl card alert"),
    ("RECOVERY-PREVIEW", "恢复卡预览", "serverosctl card recovery"),
    ("COVERAGE", "磁盘与三个飞书面覆盖页", "serverosctl work coverage"),
    ("UNDERSTANDING-DELTA", "关注源与认识变化", "serverosctl work skeleton understanding"),
    ("BOARD", "跨项目看板", "serverosctl requirement board, work board"),
    ("TODAY", "今日重点：一次选择，不是一次派生", "serverosctl work board"),
    ("ITEM-CHAIN", "单项 brief/design/plan", "serverosctl work item, work skeleton"),
    ("DECISIONS", "本轮的取舍", "作者输入 runtime/workbench_sources.py"),
    ("REPORTS", "日报与 Decision Packet", "运行产物目录里的日报文件"),
    ("QUAY-RESULTS", "Quay Goal/Task 对应与执行结果", "quay goal list, task list, GateEvent 账本"),
    ("RELEASE-MANIFEST", "pin、摘要与可达性", "git cat-file, git ls-remote, 隔离 root 自身的符号链接结构"),
    ("DEPLOYMENT", "待部署清单（未执行）", "作者输入 runtime/workbench_sources.py"),
]

DOCS = {
    "START-HERE": doc_start_here,
    "STATUS-PREVIEW": doc_status,
    "ALERT-PREVIEW": doc_alert,
    "RECOVERY-PREVIEW": doc_recovery,
    "DECISIONS": doc_decisions,
    "DEPLOYMENT": doc_deployment,
}


def render_inputs(args):
    """The render inputs that change what a page SAYS, in one comparable line.

    Not every flag. `--out` is where the set goes and changes nothing it says;
    `--documents` restricts which pages this run produces, which shows up as a
    missing file rather than a different page. These seven are the inputs that
    make two renders of one revision differ while both are correct: a render
    with `--quay-run` produces whole sections that a render without it reports
    as 「没有答案」.

    Separated by `; ` rather than a space because a path may contain a space,
    and a value a human typed is not a value this may silently split.

    `SERVEROS_WORKBENCH_SOURCES` is not in the list: it is resolved once at
    import, it is not a flag, and a set rendered under a different one is a
    different revision rather than a different invocation."""
    return "; ".join([
        f"projects={','.join(args.projects or [])}",
        f"project={args.project or ''}",
        f"item={args.item or ''}",
        f"quay-run={args.quay_run or ''}",
        f"quay-bin={args.quay_bin or ''}",
        f"ability-repo={args.ability_repo or ''}",
        f"reports-dir={','.join(args.reports_dir or [])}",
        f"release-root={args.release_root or ''}",
    ])


INPUT_KEYS = ("projects", "project", "item", "quay-run", "quay-bin",
              "ability-repo", "reports-dir", "release-root")


def inputs_dict(line):
    """Parse one `k=v; k=v` line back into a dict. Unknown keys are kept: a set
    rendered by a NEWER revision may carry a key this one does not know, and
    that mismatch has to be visible rather than dropped."""
    out = {}
    for part in (line or "").split("; "):
        if "=" in part:
            key, _, value = part.partition("=")
            out[key.strip()] = value
    return out


_SNAPSHOT_INPUTS = re.compile(r"inputs:\s*(.*?)\s*-->")


def recorded_inputs(text):
    """What the set on disk says it was rendered with, or None when it does not
    say. None is an answer — it is an older set — and is not read as "matched"."""
    match = _SNAPSHOT_INPUTS.search(text.split("\n", 1)[0])
    return match.group(1) if match else None


def inputs_differ(recorded, current):
    """The keys whose values differ, named. A count of differences would turn
    "you gave the wrong --quay-run" into "something differs"."""
    a, b = inputs_dict(recorded), inputs_dict(current)
    return [k for k in sorted(set(a) | set(b) | set(INPUT_KEYS)) if a.get(k) != b.get(k)]


def render_all(args, only=None):
    """Render the set, or just the members named in `only`.

    `only` filters BEFORE rendering rather than after, which is the difference
    between reading one document and paying for fifteen: each document runs its
    own commands, so a filter applied to finished output would still have run all
    of them.
    """
    when = datetime.now(timezone.utc)
    builders = {
        "START-HERE": lambda: doc_start_here(when),
        "ENVIRONMENT": lambda: doc_environment(when, args.ability_repo),
        "STATUS-PREVIEW": lambda: doc_status(when),
        "ALERT-PREVIEW": lambda: doc_alert(when),
        "RECOVERY-PREVIEW": lambda: doc_recovery(when),
        "COVERAGE": lambda: doc_coverage(when, args.projects),
        "UNDERSTANDING-DELTA": lambda: doc_understanding(when, args.projects),
        "BOARD": lambda: doc_board(when),
        "TODAY": lambda: doc_today(when),
        "ITEM-CHAIN": lambda: doc_item_chain(when, args.project, args.item, args.item),
        "DECISIONS": lambda: doc_decisions(when),
        "REPORTS": lambda: doc_reports(when, args.quay_run, args.reports_dir),
        "QUAY-RESULTS": lambda: doc_quay(when, args.quay_run, args.quay_bin),
        "RELEASE-MANIFEST": lambda: doc_manifest(when, args.release_root, SRC.PINS),
        "DEPLOYMENT": lambda: doc_deployment(when),
    }
    names = sorted(only) if only else sorted(builders)
    unknown = [n for n in names if n not in builders]
    if unknown:
        raise SystemExit(f"no such document: {', '.join(unknown)}")
    # Every document declares whether it is a claim or a reading. An
    # unclassified document is refused rather than defaulted, because either
    # default is wrong: defaulting to `claim` makes a live-reading page flap,
    # defaulting to `reading` makes an authored page silently uncheckable, and
    # the whole point of the split is that somebody decided.
    missing = [n for n in names if n not in SRC.DOC_KINDS]
    if missing:
        raise SystemExit(
            "these documents have no entry in runtime/workbench_sources.py#DOC_KINDS, "
            f"so whether they can be re-checked is undecided: {', '.join(missing)}")
    out = {}
    for name in names:
        RUNLOG.clear()
        body = builders[name]()
        ran = list(RUNLOG)
        out[name] = rel_home(
            f"{stamp(when, render_inputs(args))}\n\n# {name}\n\n{kind_note(name, ran)}\n\n"
            f"{answers_block(ran)}\n\n{body}")
    return out


def rel_home(text):
    """Render this node's home as `~`.

    The set is COMMITTED — `workbench/` is the reviewed snapshot — and a
    committed surface that spells out one node's absolute home paths publishes
    that node's layout to every reader, which is the same defect the artifact's
    AC-004 is about, in the other published surface. Redaction is safe here
    because the `⟦answers⟧` commands hold no absolute path (they are recorded in
    the `serverosctl ...` form and resolved through `argv_for`), so nothing that
    `--check` re-runs is affected by this substitution.

    It is a display transform, not a claim: the documents still say which node
    they came from, and `~` is what every path in them already expands from on
    the node that produced them. `RELEASE-MANIFEST` has rendered repo paths this
    way since it was written; this applies the same rule to the rest."""
    home = str(Path.home())
    return text.replace(home + "/", "~/").replace(home, "~")


def answers_block(ran):
    """What this document was rendered FROM, in a form `--check` can re-run."""
    if not ran:
        return ("<!-- ⟦answers⟧\n(none) 本页不读任何命令\n-->")
    return ("<!-- ⟦answers⟧\n" + "".join(f"{cmd}\t{code}\n" for cmd, code in ran) + "-->")


def kind_note(name, ran=()):
    """One line, in the reader's language, saying what kind of page this is.

    It goes directly under the title and not in a colophon, because the reader's
    question when they see `内存 9.5 GiB` is whether it is a fact or a reading
    from a moment, and that question is asked at the top of the page.

    `ran` is what the page actually recorded, and the reading branch needs it:
    the sentence "只核对这些命令今天仍然回答" was printed on every reading page,
    including the two that record no commands at all (REPORTS and QUAY-RESULTS —
    they read a run directory and a Quay store, and the Quay commands carry this
    node's absolute paths, which this committed snapshot must not name). On those
    two the check compares section headings and nothing else, so the colophon
    said the page was re-checked when it was not. A page whose own header
    overstates its check is the one thing a reviewer cannot catch from the page."""
    kind, because = SRC.DOC_KINDS[name]
    if kind == "claim":
        return (f"> **主张** — 本页内容由命令的判定产生，重跑应当复现。{because}。"
                f"`serverosctl workbench check` 会整页比对。")
    tail = ("" if ran else
            " 本页**没有记录可重跑的命令**，所以 `check` 对它只比对小节结构，"
            "不重新核对任何来源——它读的东西在本仓之外。")
    return (f"> **读数** — 本页内容是这台机器在一个时刻的测量值，重跑会得到**新的**读数，"
            f"不应当相等。{because}。`serverosctl workbench check` 因此只核对这些命令"
            f"今天仍然回答、页面结构没有塌陷，不比对任何数字。{tail}")


def headings(text):
    """Section headings with every digit run collapsed.

    Shape, not numbers. A document whose headings changed has lost or gained a
    section, which is a structural fact worth going red over; a document whose
    `### 磁盘` section went from 812 to 813 files has not."""
    return [re.sub(r"\d+", "<n>", line) for line in text.splitlines()
            if line.startswith("## ") or line.startswith("### ")]


def _verdict_block(text):
    """The ⟦verdicts⟧ block, or None where a document carries none."""
    m = re.search(r"<!-- ⟦verdicts⟧\n(.*?)\n-->", text, re.S)
    return m.group(1) if m else None


def _answers_block(text):
    """The ⟦answers⟧ rows of a document: (command, recorded exit code).

    Re-running these is the check for a page whose body cannot be compared. A
    command that no longer answers, or answers differently than the page says it
    did, is a page that has stopped describing this node — and that is a fact a
    reviewer can act on, unlike "the load average moved"."""
    m = re.search(r"<!-- ⟦answers⟧\n(.*?)\n-->", text, re.S)
    if not m or m.group(1).startswith("(none)"):
        return []
    rows = []
    for line in m.group(1).splitlines():
        cmd, _, code = line.rpartition("\t")
        if cmd:
            rows.append((cmd, int(code)))
    return rows


def _answers_moved(rows):
    """Re-run a document's recorded commands. Returns a defect string or "".

    The recorded argv is the `serverosctl ...` form, so it goes through the same
    `argv_for` a fresh render uses and lands on the same entry point — this
    document's own, not whichever `serverosctl` is on PATH."""
    for cmd, was in rows:
        argv = cmd.split()
        try:
            now = subprocess.run(argv_for(argv), cwd=str(ROOT), capture_output=True,
                                 text=True, timeout=120, env=_env()).returncode
        except subprocess.TimeoutExpired:
            return f"`{cmd}` 记录着 exit {was}，现在不再回答（超时）"
        if now != was:
            verb = "已不再回答" if now != 0 else "现在才回答"
            return (f"`{cmd}` 记录着 exit {was}，重新执行得到 exit {now}"
                    f"（{'这一页读的命令当时没答上来，现在答上来了' if now == 0 else verb}）")
    return ""


def _verdict_rows(block):
    """`row=VERDICT` lines as an ordered id -> verdict mapping.

    Ordered because `_first_verdict_change` owes the reader the FIRST row that
    moved, and "first" has to mean first in the block as printed."""
    rows = {}
    for line in block.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            rows[key] = value
    return rows


def _first_verdict_change(older, fresh):
    """Name the FIRST row that moved, not a count of moves.

    The verdicts are a list of independent claims about independent verbs; "3 rows
    changed" turns four separate findings into one number, and the row that went
    red is the thing somebody has to go and look at.

    Paired by ROW ID, not by position — `zip` was the first version, and it named
    a row that had not moved. Inserting one row shifts every row below it, so each
    of them is compared against its neighbour; an insertion in the middle of the
    block came out as `registration-discover: 盘中 OK → 现在 OK`, a sentence that
    contradicts itself, about a row whose verdict never changed. Measured on this
    round's own START-HERE, whose block gained a row. The `行数不同` branch that
    was supposed to catch added and removed rows was reachable only when the row
    was appended or dropped at the END, which is also the only shape its test
    exercised."""
    if older is None or fresh is None:
        return "一侧没有判定块"
    a, b = _verdict_rows(older), _verdict_rows(fresh)
    added = [k for k in b if k not in a]
    removed = [k for k in a if k not in b]
    if added or removed:
        parts = ([f"新增 {', '.join(added)}"] if added else []) + \
                ([f"移除 {', '.join(removed)}"] if removed else [])
        return f"行数不同（{len(a)} → {len(b)}）：" + "；".join(parts)
    for key in a:
        if a[key] != b[key]:
            return f"{key}: 盘中 {a[key]} → 现在 {b[key]}"
    return "块相同"




def normalize(text):
    for pattern, _ in VOLATILE:
        text = pattern.sub("<volatile>", text)
    # NOT `[^>]*`, which is what stood here. The VOLATILE pass above runs FIRST,
    # so by the time this line sees the header its timestamp has already become
    # `<volatile>` — a `>` the old class refused to cross, which meant `-->` was
    # never reached and the substitution matched NOTHING AT ALL. Measured on a
    # rendered START-HERE: the header survived normalisation whole, worktree path
    # and all, inside the one function whose entire job is to make two renders
    # comparable. `--check` then compared a line it had declared it strips.
    #
    # `[^\n]*?` rather than `.*?` with DOTALL: a header whose `-->` is missing
    # must leave its own line in place, not swallow the document up to the next
    # `-->` somewhere below. The comment that reads the inputs one function down
    # already matched this way; only the one that removes it did not.
    return re.sub(r"\A<!--\s*⟦snapshot⟧[^\n]*?-->\n?", "", text, count=1)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=OUT_DEFAULT)
    ap.add_argument("--write", action="store_true", help="write the documents")
    ap.add_argument("--check", action="store_true",
                    help="fail if the rendered set differs from what is on disk "
                         "(clock-derived values are not compared; see VOLATILE)")
    ap.add_argument("--stdout", metavar="NAME", help="print one document, write nothing")
    ap.add_argument("--documents", nargs="*", default=None, metavar="NAME",
                    help="restrict the set to these documents. Loading one page "
                         "costs the commands THAT page reads, which is what makes a "
                         "targeted test of `check` affordable — the full set re-runs "
                         "every command every page reads from")
    ap.add_argument("--projects", nargs="*", default=["serveros", "litellm", "multi-user-claw"])
    ap.add_argument("--project", default="litellm", help="the project the Item chain is read from")
    ap.add_argument("--item", default="LIT-0101")
    # No default points at a run directory, on purpose. A shipped default naming
    # one round's scratch tree is a path that means nothing on any other node,
    # and it would render a Quay section out of a store that happens to still be
    # on this disk. Absent means the section says it has no answer.
    ap.add_argument("--quay-run", default=None,
                    help="a directory holding a quay workspace (`ws/`) and logs")
    ap.add_argument("--quay-bin", default=None,
                    help="the Quay CLI to ask for the goal store. A fact about THIS "
                         "node, so it has no default: the first version baked in two "
                         "paths under one home directory, which is the defect this "
                         "flag exists to remove. Absent means QUAY-RESULTS renders "
                         "as FAILED and says which flag would fill it")
    ap.add_argument("--ability-repo", default=None,
                    help="the ability-management clone to render the family view "
                         "from. This module does not reimplement that renderer")
    ap.add_argument("--reports-dir", nargs="*", default=None, metavar="DIR",
                    help="directories holding a run's daily reports. Searched in "
                         "addition to <repo>/docs, <repo>/workbench and "
                         "--quay-run, for files named *daily-report*")
    ap.add_argument("--release-root", default=None,
                    help="an isolated runtime root's COMPOSITION directory (the one "
                         "holding `current` and `previous`), for the manifest's "
                         "install section. Never the live one by default. Not the "
                         "`current` symlink: this panel prints the runtime root and "
                         "then reads `current`/`previous` beneath it, so handing it "
                         "`current` would make it look for `current/current` and "
                         "print a symlink as though it were the root")
    args = ap.parse_args(argv)

    only = {args.stdout} if args.stdout else (set(args.documents) if args.documents else None)

    # THE TWO REFUSALS THAT NEED NO RENDERING, TAKEN BEFORE IT. Both are facts
    # about this invocation rather than about the documents, and each would
    # otherwise cost the full set's forty-five commands to discover — the
    # harness would run every command on this node to learn that it had been
    # asked the wrong question.
    if args.check:
        # An absent OUTPUT DIRECTORY and fifteen drifted documents are different
        # situations and only one of them is a defect. Inside an installed
        # artifact the default directory does not exist ON PURPOSE — see the
        # `--write` guard below — so reporting it as fifteen missing files would
        # make the normal case of a fresh install read as a broken workbench.
        if not args.out.is_dir():
            sys.stderr.write(
                f"serverosctl: workbench check: {args.out} does not exist, so there "
                f"is nothing to compare against and nothing is claimed about it. "
                f"Render one with `workbench render --out {args.out}`.\n")
            return 2
        # WHAT THE SET ON DISK WAS RENDERED WITH. A set rendered with
        # `--quay-run` carries whole sections a render without it reports as
        # 「没有答案」, so comparing the two reports drift on pages that are
        # individually correct. That verdict names the wrong cause — the failure
        # this whole verb exists to avoid — so the inputs are compared first and
        # a mismatch is stated as a fact about THIS CHECK. Exit 2, the same
        # answer an absent directory gets: nothing was compared, and saying so
        # is not the same as saying something passed.
        recorded = None
        for path in sorted(args.out.glob("*.md")):
            recorded = recorded_inputs(path.read_text())
            if recorded is not None:
                break
        current = rel_home(render_inputs(args))
        if recorded is None:
            sys.stderr.write(
                f"serverosctl: workbench check: {args.out} 里的文档没有记录它们是"
                f"用哪一组输入渲染的（更早的格式），所以这次比对的是两组未知输入"
                f"——没有比对任何东西。重新渲染一次这一集，它会带上这行记录：\n"
                f"    workbench render --out {args.out} <与当初相同的输入>\n")
            return 2
        if inputs_differ(recorded, current):
            sys.stderr.write(
                f"serverosctl: workbench check: 这一集不是用这组输入渲染的，"
                f"所以差异会落在输入上，不是落在文档上。没有比对任何东西。\n"
                f"  集里记录：{recorded}\n"
                f"  这次给的：{current}\n"
                f"  不同的输入：{'、'.join(inputs_differ(recorded, current))}\n"
                f"  `--out` 之外，只有这些输入会改变输出；照集里记录的那一组重跑。\n")
            return 2

    rendered = render_all(args, only=only)

    if args.stdout:
        sys.stdout.write(rendered.get(args.stdout, f"no such document: {args.stdout}\n"))
        return 0

    if args.check:
        bad, skipped = [], []
        for name, text in sorted(rendered.items()):
            path = args.out / f"{name}.md"
            if not path.is_file():
                bad.append(f"{name}: 不存在")
                continue
            on_disk = path.read_text()
            kind, _ = SRC.DOC_KINDS[name]

            # The verdict block is compared wherever a document carries one, and
            # only START-HERE does: its content IS the matrix, so the block is
            # the machine-comparable form of the page. A document with no block
            # compares equal to itself here and is checked by kind below — which
            # is stated in the summary line rather than left implicit, because
            # "compared" and "not compared" must not look the same.
            fresh, older = _verdict_block(text), _verdict_block(on_disk)
            if fresh != older:
                bad.append(f"{name}: 判定块与当前不符 — {_first_verdict_change(older, fresh)}")
                continue

            # Re-run what this page was rendered FROM. For a reading this is the
            # whole check: the numbers are not compared, the commands are.
            moved = _answers_moved(_answers_block(on_disk))
            if moved:
                bad.append(f"{name}: {moved}")
                continue

            if kind == "reading":
                skipped.append(name)
                fresh_h, older_h = headings(text), headings(on_disk)
                if fresh_h != older_h:
                    bad.append(f"{name}: 小节结构变了 — "
                               f"{'+'.join(sorted(set(fresh_h) - set(older_h))) or ''}"
                               f"{'-'.join(sorted(set(older_h) - set(fresh_h))) or ''}")
                continue

            if normalize(text) != normalize(on_disk):
                bad.append(f"{name}: 与命令的当前输出不一致")
        for line in bad:
            print(f"DRIFT {line}", file=sys.stderr)
        if bad:
            print(f"\n{len(bad)} 份文档与产生它们的命令不再一致。", file=sys.stderr)
            return 1
        n_cmd = sum(len(_answers_block(rendered[n])) for n in sorted(rendered))
        print(f"checked {len(rendered)} 份文档：{len(rendered) - len(skipped)} 份主张整页比对；"
              f"{len(skipped)} 份读数比不了整页——重跑得到的是**新读数**，相等不是判据——"
              f"改为重跑它们各自记录的来源命令（共 {n_cmd} 条）并核对小节结构。"
              f"分类与理由在 runtime/workbench_sources.py#DOC_KINDS")
        return 0

    if not args.write:
        ap.error("需要 --write 或 --check 或 --stdout")

    # The default output is the REPOSITORY's `workbench/` — the directory the
    # reviewed snapshot is committed to. An installation has no such directory
    # and must not acquire one:
    #
    #   * the documents are a snapshot of ONE NODE at one moment (this node's
    #     state roots, this node's coverage denominators, this node's pin
    #     locations). A distribution is not a node, so shipping them would put
    #     one machine's readings inside every install;
    #   * an artifact directory is replaced wholesale on the next update — the
    #     same reason `bundle.yaml` sets PYTHONDONTWRITEBYTECODE for the MCP
    #     adapters — so anything written here is destroyed by the event it was
    #     meant to serve;
    #   * and `runtime/` and `bin/` in an artifact are the staged revision that
    #     `release.py` verified by hash. A write would leave RELEASE.json
    #     describing a tree that no longer exists.
    #
    # So the default is refused there, by name, telling the operator to name a
    # destination rather than following them into it.
    if args.out == OUT_DEFAULT and not (ROOT / ".git").exists():
        sys.stderr.write(
            f"serverosctl: {ROOT} is an installed artifact, not a checkout, so it "
            f"has no workbench/ of its own and must not be given one — the "
            f"documents describe one node, and this directory is a hash-verified "
            f"revision that the next update replaces wholesale. "
            f"Pass --out <a directory outside the artifact>.\n")
        return 2

    args.out.mkdir(parents=True, exist_ok=True)
    for name, text in sorted(rendered.items()):
        (args.out / f"{name}.md").write_text(text)
    print(f"wrote {len(rendered)} 份文档到 {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
