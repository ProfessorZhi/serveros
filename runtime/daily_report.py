#!/usr/bin/env python3
"""SYS-23 — 真实的差量进，一份 canonical 文档和一份投递回执出。

WHY THIS EXISTS
---------------
`intake/document-skeletons.yaml` 里写着 `daily-report` 这一份文档的形状，并且在同一段注释里
写明它「还没有生成器」。这个模块就是那个生成器：它读本周期**真实发生的事**——工作树上的提交、
Quay 自己的闸门记录——写成一份文档，再附一份回执，说明这份文档投给了谁、什么时候、内容是
哪一版。

WHAT IT IS NOT ALLOWED TO DO
---------------------------
三件事它一次都不做。这三件事是同一类故障的三个名字：一份读起来正常的文档，和一份真的正常的
文档，长得一模一样。

  1. **没有进展时不说「没有进展」。** 两个源都答了「窗口内什么都没有」时，文档必须把这句
     话写出来。不写的结果不是「空文档」——是一份看起来正常的空文档。
  2. **没跑过/没测过的东西不省略。** 一份被静默省略的失败项，在文档里和「通过」没有区别。
     所以未运行的项进「卡住」，缺覆盖的项带上它缺的是哪一段。
  3. **「源没答」不当成「源答了：没有」。** 工作树不是 git 检出、Quay 目录不存在，都是缺口。
     缺口不能渲染成零：零是一个发现，缺口是「没人问过」。这条决定了 `progress` 有三个值而
     不是两个——`NONE`（问过了，是零）和 `UNKNOWN`（没问出来）必须分开。

THE INPUT BOUNDARY, AND WHY 无关纪要 DOES NOT REACH THE DOCUMENT
----------------------------------------------------------------
这个 producer 的输入只有四类，写在 `runtime/periodic-report-contract.yaml#inputs.accepted`。
**不在名单上的输入被具名拒绝**，不是静默忽略。SYS-23 那条红线正是靠这条表达的：会议纪要
没有对应的输入类，于是它在边界上就被拒了，拒绝时说出它是什么、为什么不在边界上。这里没有
一份「会议纪要」黑名单——黑名单只挡住它列出的那一个名字，而边界挡的是「不在边界上」这个
性质。理由写在那个 YAML 里，此处不重复。

这一点也解释了为什么这个模块不去读 `space/` 下的任何东西：读取是一种接受。一个读了会议
目录再「过滤掉」的实现，它的边界是「先全收进来再说」，而过滤规则一旦漏一条就没有第二道闸。
不读，就没有漏的可能。

SOURCE ANSWERED vs SOURCE DID NOT ANSWER
----------------------------------------
每个测量函数返回的都是一个带 `answered` 的块，而不是一个列表：

    {"answered": True,  "why": None,       "commits": [...]}   # 源答了：答的是「有这些」
    {"answered": True,  "why": None,       "commits": []}      # 源答了：答的是「没有」——一个发现
    {"answered": False, "why": "…不是 git 检出…", "commits": []}  # 源没答——一个缺口

第三种情况下 `commits` 同样为空，所以**调用方绝不能只看列表是否为空**。这是本模块里最容易
写错的一处，也是唯一一处用结构而不是用约定来防的：列表为空时 `why` 一定在场。

THE RECEIPT DOES NOT LIE ABOUT DELIVERY
---------------------------------------
本 producer **不投递**。它组合文档、算出文档的 sha256、写盘。投递是别人的事，投递的结果只能
由调用方带进来。调用方什么都没说时，回执写 `UNKNOWN` 且 `delivered: false`，理由写明「没有人
告诉过这份文档投没投出去」——绝不会因为「文档写成功了」就写成 `DELIVERED`。同一个道理，
一份没有收件人的回执不允许声称 `DELIVERED`：说不清投给谁的投递，不是一个投递。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - 环境保证
    yaml = None

_HERE = Path(__file__).resolve().parent
ROOT = _HERE.parent

CONTRACT = _HERE / "periodic-report-contract.yaml"
CONTRACT_SCHEMA = "serveros/periodic-report-contract.v1"
SCHEMA = "serveros/periodic-report.v1"
RECEIPT_SCHEMA = "serveros/publication-receipt.v1"

#: 文档形状的 authority。这不是本模块的常量，是一份别人写的、可以被改的声明，所以它读过来
#: 而不是抄一份。抄一份的后果是：有人改了 skeleton，文档的形状不再跟着变，而两边都不报错。
SKELETONS = ROOT / "intake" / "document-skeletons.yaml"
SKELETON_ID = "daily-report"

#: 兜底。仓库里已有一个同样的用法：`runtime/alert_digest.py` 的 `DEVICE_FALLBACK`。
#: 这里的规则与那里一样——**兜底必须是报出来的**。一个静默的兜底是缺陷，因为读者会把
#: `actor:unknown` 当成一个真的叫 unknown 的人，而不是当成「这一行没人署名」。
ACTOR_FALLBACK = "actor:unknown"
ITEM_FALLBACK = "item:unknown"

#: `progress` 的三个值。三分而不是两分，是因为「问过了，是零」和「没问出来」必须分开；
#: 合成一个 `NONE` 会让一次没跑成的测量读起来像一句「本周无事发生」。
SOME, NONE, UNKNOWN = "SOME", "NONE", "UNKNOWN"

#: 每一条 `run-status` 记录被判定成的状态。`NOT_RUN` 和 `UNOBSERVED` 分开，理由同上：
#: 「源说了它没跑」是源答了，「源什么都没说」是源没答。
#:
#: `COVERED` 和 `COVERAGE_MISSING` 是覆盖率这一类的两个端子，而 `COVERED` **不在**「卡住」
#: 里：一个量出来的覆盖率读数不是一件卡住的事。第一版把有读数的覆盖率判成 `HALF_ANSWERED`
#: 并塞进「卡住」，于是一份 95% 覆盖的日报里，「卡住」那一栏会出现一行覆盖率——那一栏的
#: 每一行都在说「这里需要有人看一眼」，而 95% 不需要。读数与缺口必须分开，否则整栏失去意义。
PASSED, FAILED = "PASSED", "FAILED"
NOT_RUN, UNOBSERVED, HALF_ANSWERED = "NOT_RUN", "UNOBSERVED", "HALF_ANSWERED"
COVERED, COVERAGE_MISSING = "COVERED", "COVERAGE_MISSING"

#: 被允许进入 `progress` 的判定为「有进展」的闸门判定词。`verdict` 是 Quay 写的，不是这里
#: 判的；认不出来的判定词按 UNKNOWN 处理并报出来，而不是当成 pass——把没见过的词读成通过，
#: 是这一整类缺陷里最安静的一种。
GATE_PASS = "pass"
GATE_FAIL = "fail"


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _alerting():
    # 投递结果的词汇只有一个来源：`runtime/notification-contract.yaml#alerts.delivery.values`，
    # 而读它的唯一读者是 `runtime/alerting.py`。这里走它的 `policy()` 而不是自己再读一遍那个
    # 文件——同一份文件有两个读者，就是两个会各自漂移的地方，而漂移的那一个一定是不常跑的那个。
    return _load("serveros_runtime_alerting", _HERE / "alerting.py")


def _store_io():
    # 同一个原子写：`_atomic_write` 已经是这棵树上「写一份不会被读到一半的 JSON」的实现。
    # 再写一个第二版就是给两份实现各留一次出错的机会。
    return _load("serveros_runtime_agent_control_store",
                 _HERE / "agent_control_store.py")


class Refused(Exception):
    """这份文档被要求组合成一件它不是的东西。"""


# ---------------------------------------------------------------------------
# 契约。数字与边界都从这里来。
# ---------------------------------------------------------------------------

def contract(path: Path | str = CONTRACT) -> dict:
    if yaml is None:  # pragma: no cover - 环境保证
        raise Refused("PyYAML 不可用，读不了本 producer 的契约；"
                      "没有契约就没有输入边界，也就没有这条红线")
    path = Path(path)
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except OSError as exc:
        raise Refused(f"读不了 {path}：{exc}。这份契约是输入边界的唯一载体；"
                      f"读不到它的时候放行任何输入，等于这条红线不存在") from exc
    if document.get("schema") != CONTRACT_SCHEMA:
        raise Refused(
            f"{path} 声明的是 {document.get('schema')!r}，不是 {CONTRACT_SCHEMA!r}。"
            f"从一份不是它自称的那份文档里读出来的边界，没有任何权威性")
    return document


def accepted_kinds(document: dict | None = None) -> tuple:
    block = (document or contract()).get("inputs") or {}
    listed = block.get("accepted")
    if not isinstance(listed, list) or not listed:
        raise Refused(
            f"{CONTRACT}#inputs.accepted 是 {listed!r}。空名单的两种读法——"
            f"「什么都不收」和「什么都收」——都说得通，而其中一种会让这份文档收下任何东西。"
            f"一份讲不出自己收什么的边界不是边界")
    return tuple(str(entry.get("kind")) for entry in listed)


def top_n(document: dict | None = None) -> int:
    value = ((document or contract()).get("listing") or {}).get("top_n")
    if not isinstance(value, int) or value <= 0:
        raise Refused(
            f"{CONTRACT}#listing.top_n 是 {value!r}。没有它就要么全列（一份长到没人读完的"
            f"文档）要么截断（被丢掉的那些在文档里消失得没有痕迹）——两者都是缺陷")
    return value


def refuse_unaccepted(named_inputs, document: dict | None = None) -> None:
    """名单之外的输入，逐个具名拒绝。

    `named_inputs` 是一份 `{kind: payload}`——用字典而不是列表，因为一个匿名列表里的
    `{"kind": "meeting-note"}` 可以被调用方顺手漏掉一个字段，而拒绝的理由需要说出**是
    哪一个**被拒了。静默忽略是这里唯一的错误答案：被忽略的那份输入会让人以为它进去了，
    而它没有；两边的账都记错。
    """
    accepted = accepted_kinds(document)
    offending = sorted(str(k) for k in (named_inputs or {}) if str(k) not in accepted)
    if not offending:
        return
    listed = "、".join(offending)
    raise Refused(
        f"这些输入不在本 producer 的边界上，因此被拒绝：{listed}。"
        f"接受的只有 {list(accepted)}（{CONTRACT}#inputs.accepted）。"
        f"边界挡的是「不在名单上」这个性质，不是某几个名字：一份与工作无关的会议纪要没有"
        f"变成差量、也没有变成闸门记录的可能，把它写进来会让日报出现一条无法追溯的推进，"
        f"而无法追溯的推进与编造出来的推进在读者眼里是同一件事。")


# ---------------------------------------------------------------------------
# 窗口
# ---------------------------------------------------------------------------

def _parse_instant(text) -> datetime | None:
    if not isinstance(text, str) or not text.strip():
        return None
    stamp = text.strip()
    if stamp.endswith("Z"):
        stamp = stamp[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    # 无时区的时间戳是一个不知道在说哪个钟的读数。把它当成 UTC 是最省事的写法，也是最坏
    # 的写法：日报的窗口会因此平移几个小时，而平移之后它依然是一个格式正确的时间戳。
    return parsed if parsed.tzinfo else None


def period_window(period) -> dict:
    """`{"start", "end"}`，两个都必须带时区，且 start <= end。

    窗口是必填的，没有默认值。一个自己决定窗口的生成器会写出一份关于「某个时间段」的
    日报，而没有人选过那个时间段——这正是「没人看 ≠ 健康」在时间轴上的样子。
    """
    if not isinstance(period, dict):
        raise Refused(
            f"没有给出统计窗口（period）。这一份文档是关于一个时间段的，而本 producer 不"
            f"替任何人选那个时间段：默认成「今天」会写出一份关于某个没人选过的窗口的日报，"
            f"它读起来和一份正常的日报完全一样")
    start, end = _parse_instant(period.get("start")), _parse_instant(period.get("end"))
    for name, value, raw in (("start", start, period.get("start")),
                             ("end", end, period.get("end"))):
        if value is None:
            raise Refused(
                f"窗口的 {name} 是 {raw!r}，不是一个带时区的 ISO-8601 时刻。"
                f"不带时区的读数无法判断它落在哪个钟上，而这份文档要拿它去和 git 提交、"
                f"Quay 闸门记录做比较")
    if start > end:
        raise Refused(f"窗口的 start（{period.get('start')}）晚于 end（{period.get('end')}）。")
    return {"start": start.isoformat(), "end": end.isoformat()}


def _in_window(instant: datetime | None, window: dict) -> bool:
    if instant is None:
        return False
    return _parse_instant(window["start"]) <= instant <= _parse_instant(window["end"])


# ---------------------------------------------------------------------------
# 兜底登记簿
# ---------------------------------------------------------------------------

class Fallbacks:
    """每一处「本该是测出来的值，实际用了一个常量」的登记。

    存在的理由只有一条：兜底读起来和事实一样。一份写着 `actor:unknown` 的提交记录，和一份
    写着真的作者名的提交记录，在表格里是同一种东西，除非有人特意把前者数出来。所以这里
    不只是记下来，还要在文档的抬头里说「本周期有 N 处兜底」，因为一个读者不会翻到表格里
    去找哪一格长了另一副样子。
    """

    def __init__(self) -> None:
        self.entries: list[dict] = []

    # 有 `__len__` 就**不能**写 `fallbacks = fallbacks or Fallbacks()`：调用方传进来的登记簿
    # 在还没记下任何东西时长度是 0，于是它是假值，于是那一行会把它换成一个新的空登记簿——
    # 之后每一条兜底都记在那个马上被丢掉的副本里，而文档抬头照常写「本周期没有兜底」。
    # 这就是本模块最不该犯的那个错（静默的兜底）以另一种形式回来了：丢失发生在**登记**这一步，
    # 而不是渲染那一步。所以三处入口都写成 `if fallbacks is None`。
    def record(self, what: str, value: str, why: str) -> str:
        self.entries.append({"what": what, "value": value, "why": why})
        return value

    def __len__(self) -> int:
        return len(self.entries)

    def summary(self) -> str:
        if not self.entries:
            # 「没有兜底」是一句答案，不是一句沉默。一个空列表和一次没跑成的登记在同一份
            # 文档里长得一样，所以这句话必须写出来。
            return "本周期没有兜底：下面每一个值都是测出来的。"
        kinds = {}
        for entry in self.entries:
            kinds[entry["value"]] = kinds.get(entry["value"], 0) + 1
        detail = "、".join(f"`{v}` × {n}" for v, n in sorted(kinds.items()))
        return (f"**本周期有 {len(self.entries)} 处兜底：{detail}。** "
                f"这些位置本该是测出来的值。它们不是错误，但它们不是事实，"
                f"看到它们的地方不要当成有人在署名。")


# ---------------------------------------------------------------------------
# 源一：工作树的真实差量
# ---------------------------------------------------------------------------

#: `%x1f` 作字段分隔、`%x1e` 作记录分隔。不用 `%n`+行解析，是因为提交标题里可以有任何
#: 字符（包括制表符），按行解析会把一条提交拆成两条形态正确的记录。
_FIELD, _RECORD = "\x1f", "\x1e"

#: 记录分隔符**前后各放一次**，于是切分的结果是 `[空, 元信息, numstat, 元信息, numstat, …]`。
#: 只在元信息末尾放一次是不够的：`--numstat` 的输出排在元信息**之后**，只放一次会让每一条
#: 提交的文件变动落进下一条提交的记录里，而症状是提交数少一半、每条的「文件数」来自隔壁。
#: 这是量出来的——用一次两笔提交的仓库跑出来的就是这个结果。
_LOG_FORMAT = (f"{_RECORD}%H{_FIELD}%an{_FIELD}%cI{_FIELD}%s{_RECORD}")


def _git(worktree: Path, *args: str) -> tuple[int, str]:
    """一条 git 只读命令。路径一律绝对。

    `-C` 后面给绝对路径，且其后不再给任何文件参数——`git -C <dir> <verb> <相对路径>` 会把
    那个相对路径相对**当前工作目录**再拼一次，症状是 `hash-object` 之类静默返回空值。这里
    不需要文件参数，所以把这条规则写死成「不给」比「记得给对」可靠。
    """
    try:
        proc = subprocess.run(["git", "-C", str(worktree), *args],
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 128, f"{type(exc).__name__}: {exc}"
    return proc.returncode, proc.stdout.decode(errors="replace")


def measure_worktree(worktree, window: dict, fallbacks: Fallbacks | None = None) -> dict:
    """工作树在窗口内的提交与文件变动。

    返回值一定带 `answered`。三种情况各有各的答案，见模块抬头：
    不是检出 → `answered: False`；是检出但没有提交 → `answered: True` 且提交为空（一个
    发现，不是缺口）；正常 → 有提交。

    merge 提交的 numstat 是空的，所以它会出现成一条「没有文件变动」的提交。这是 git 的
    行为而不是这里的丢数据：把它写成「改了 0 个文件」也是错的，所以 `files` 为空时
    `file_count` 是 `None` 而不是 0——0 和「数不出来」不能是同一个读数。
    """
    if fallbacks is None:   # 不是 `or`：见 Fallbacks 上的注释，空登记簿是假值
        fallbacks = Fallbacks()
    path = Path(worktree).resolve()
    block = {"path": str(path), "answered": False, "why": None,
             "commits": [], "uncommitted": [], "notes": []}

    code, _ = _git(path, "rev-parse", "--git-dir")
    if code != 0:
        block["why"] = (f"{path} 不是一个 git 检出（`git rev-parse --git-dir` 退出 {code}）。"
                        f"没有工作树可读，本周期的工作差量因此是**缺口**而不是零。")
        return block

    code, _ = _git(path, "rev-parse", "--verify", "HEAD")
    if code != 0:
        block["answered"] = True
        block["notes"].append("这个检出还没有任何提交（HEAD 无法解析）。"
                              "读到这里是「空仓库」，不是「读不了」。")
        return block

    code, out = _git(
        path, "log",
        f"--since={window['start']}", f"--until={window['end']}",
        f"--pretty=format:{_LOG_FORMAT}", "--numstat")
    if code != 0:
        block["why"] = f"`git log` 退出 {code}：{out.strip()[:400]}"
        return block

    chunks = out.split(_RECORD)
    commits = []
    for head, body in zip(chunks[1::2], chunks[2::2]):
        fields = head.strip("\n").split(_FIELD)
        if len(fields) < 4:
            # 一条解析不出四个字段的记录既不能当成提交也不能当成空。丢掉它就是一个静默的
            # 缺口，所以它进 notes 被报出来。
            block["notes"].append(f"一条解析不出的提交记录：{head[:200]!r}")
            continue
        revision, author, committed_at, subject = fields[0], fields[1], fields[2], fields[3]
        if not author.strip():
            author = fallbacks.record(
                "提交的作者", ACTOR_FALLBACK,
                f"{revision[:12]} 的作者字段是空的；这一行没有署名，"
                f"而不是有一个叫 unknown 的人")
        files = []
        for line in body.strip("\n").split("\n"):
            if not line.strip():
                continue
            parts = line.split("\t")
            if len(parts) == 3:
                files.append({"added": parts[0], "deleted": parts[1], "path": parts[2]})
            else:
                block["notes"].append(f"{revision[:12]} 有一条解析不出的变动行：{line[:160]!r}")
        commits.append({
            "revision": revision, "subject": subject, "actor": author,
            "committed_at": committed_at,
            # 窗口是按提交时间（committer date）卡的，所以这里也用提交时间；用作者时间会
            # 让一条被列出来的提交带着一个落在窗口外的时刻。
            "in_window": _in_window(_parse_instant(committed_at), window),
            "files": files,
            # 空不是零。merge 提交的 numstat 就是空的——它既不是「改了 0 个文件」也不是
            # 「数不出来」，这里给 None 是为了让渲染层必须选一个说法，而不是顺手印一个 0。
            "file_count": len(files) if files else None,
        })
    commits.reverse()  # git 默认倒序；日报顺读

    code, status = _git(path, "status", "--porcelain")
    if code == 0:
        uncommitted = [line for line in status.splitlines() if line.strip()]
        block["uncommitted"] = uncommitted
    else:
        block["notes"].append(f"`git status` 退出 {code}，未提交改动这一项没有答案。")

    block["answered"] = True
    block["commits"] = commits
    return block


# ---------------------------------------------------------------------------
# 源二：Quay 自己的记录
# ---------------------------------------------------------------------------

def _quay_layout(run_dir: Path) -> tuple[Path, Path]:
    """账本与任务目录的实际位置，连同它是怎么被找到的。

    两个候选布局是这台机器上真实存在的两种：一轮的工作区把 `.quay/` 放在根上，而
    `runtime/workbench.py` 的 `doc_quay` 读的是 `<run>/ws/.quay/`。找到哪一个要报出来，
    因为「读的是哪个目录」决定了这份文档说的是哪一轮的事。
    """
    for prefix in (run_dir, run_dir / "ws"):
        ledger = prefix / ".quay" / "gate-events.jsonl"
        if ledger.is_file():
            return prefix / ".quay" / "gate-events.jsonl", prefix / "tasks"
    return run_dir / ".quay" / "gate-events.jsonl", run_dir / "tasks"


def measure_quay(run_dir, window: dict, fallbacks: Fallbacks | None = None) -> dict:
    """Quay 的闸门账本与任务状态。

    账本不存在是**缺口**（没人写下来），账本存在但是空的、或者窗口内没有条目，是**发现**。
    两者都返回 `answered`，但只有前者是 False。混掉的后果和读 Git 那一侧一样：一个还没跑过
    任务的轮次会被写成「跑过了，没有失败」。
    """
    if fallbacks is None:   # 不是 `or`：见 Fallbacks 上的注释，空登记簿是假值
        fallbacks = Fallbacks()
    root = Path(run_dir).resolve()
    ledger_path, tasks_dir = _quay_layout(root)
    block = {"root": str(root), "ledger": str(ledger_path),
             "tasks_dir": str(tasks_dir), "answered": False, "why": None,
             "gates": [], "tasks": [], "problems": [], "notes": [],
             "out_of_window": 0, "unknown_verdicts": []}

    if not ledger_path.is_file():
        block["why"] = (
            f"{ledger_path} 不存在。Quay 的闸门记录在 Quay 自己的工作区里，本仓不含它们；"
            f"没有指向它就没有本周期的执行结果。这是**缺口**，不是「本周期没有闸门记录」。")
        return block

    try:
        raw = ledger_path.read_text(encoding="utf-8")
    except OSError as exc:
        block["why"] = f"读不了 {ledger_path}：{type(exc).__name__}: {exc}"
        return block

    gates = []
    for number, line in enumerate(raw.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError as exc:
            # 一行解析不出的账本既不是「有这条记录」也不是「没有这条记录」。静默跳过它，
            # 一份截断的账本会读成一份完整的账本。
            block["problems"].append({"line": number, "why": str(exc),
                                      "text": line[:200]})
            continue
        if not isinstance(row, dict):
            block["problems"].append({"line": number, "why": "这一行不是对象",
                                      "text": line[:200]})
            continue
        item = row.get("item_id") or row.get("record")
        if not item:
            item = fallbacks.record(
                "闸门记录的任务号", ITEM_FALLBACK,
                f"账本第 {number} 行的 `item_id` 是空的；这条记录因此无法归到任何一个任务上，"
                f"而它看上去和一条有归属的记录没有区别")
        verdict = str(row.get("verdict") or "")
        if verdict and verdict not in (GATE_PASS, GATE_FAIL):
            # 一个没见过的判定词不读成通过，也不读成失败：两种读法都是替 Quay 下结论。
            block["unknown_verdicts"].append({"line": number, "verdict": verdict})
        at = _parse_instant(row.get("timestamp"))
        if not _in_window(at, window):
            # 窗口外的条目**数出来**，不列出来。丢掉它们会让「窗口内 3 条」看起来像
            # 「账本里一共 3 条」，而两个数回答的是不同的问题。
            block["out_of_window"] += 1
            continue
        gates.append({
            "item": item, "gate": row.get("gate"), "actor": row.get("actor"),
            "verdict": verdict, "timestamp": row.get("timestamp"),
            "reason": ((row.get("payload") or {}).get("reason") or ""),
        })

    block["gates"] = gates
    block["answered"] = True

    if tasks_dir.is_dir():
        for path in sorted(tasks_dir.glob("*.md")):
            card = _front_matter(path, block)
            if card is not None:
                block["tasks"].append(card)
    else:
        block["notes"].append(
            f"{tasks_dir} 不存在，因此「任务声称了什么」这一项没有答案——"
            f"账本里的闸门记录仍然是读到的。")
    return block


def _front_matter(path: Path, block: dict) -> dict | None:
    """`tasks/QUAY-XXXX.md` 的 front matter。读不了就说读不了。"""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        block["problems"].append({"file": str(path), "why": f"{type(exc).__name__}: {exc}"})
        return None
    if not text.startswith("---"):
        block["problems"].append({"file": str(path), "why": "没有 front matter"})
        return None
    end = text.find("\n---", 3)
    if end < 0:
        block["problems"].append({"file": str(path), "why": "front matter 没有结束标记"})
        return None
    try:
        head = yaml.safe_load(text[3:end]) or {}
    except Exception as exc:  # noqa: BLE001 - yaml 的异常类型随版本变，但都要报出来
        block["problems"].append({"file": str(path), "why": f"front matter 解析失败：{exc}"})
        return None
    if not isinstance(head, dict):
        block["problems"].append({"file": str(path), "why": "front matter 不是对象"})
        return None
    return {"id": head.get("id") or path.stem, "title": head.get("title") or "",
            "status": head.get("status"), "path": str(path)}


# ---------------------------------------------------------------------------
# 未运行 / 未测 / 缺覆盖
# ---------------------------------------------------------------------------

def classify_run_status(records) -> list[dict]:
    """把调用方给的观察判成六种状态之一。

    判据是**观察的形状**，不是记录的名字：

        observed 缺席                     → UNOBSERVED      源没答
        observed.ran is False             → NOT_RUN         源答了：没跑
        kind == coverage 且没有读数        → COVERAGE_MISSING 源答了：没有覆盖数据
        ran 为真但没有退出码               → HALF_ANSWERED   答了一半
        ran 为真且 exit_code == 0          → PASSED
        ran 为真且 exit_code != 0          → FAILED

    这六条里只有两条是「好」的，而其余四条**都必须出现在文档里**。一条被省略的 `NOT_RUN`
    在读者的眼里和 `PASSED` 是同一个东西，这就是为什么省略在这里不是压缩，是撒谎。
    """
    out = []
    for record in records or ():
        if not isinstance(record, dict) or not record.get("name"):
            raise Refused(f"一条 run-status 记录没有名字：{record!r}。"
                          f"没有名字的记录在文档里无法被指认，也无法被追问")
        kind = str(record.get("kind") or "run")
        observed = record.get("observed")
        if observed is None:
            state, why = UNOBSERVED, (record.get("why")
                                      or "调用方没有给出观察：这一项没人报过结果")
        elif not isinstance(observed, dict):
            raise Refused(f"{record['name']} 的 observed 是 {observed!r}，不是一份观察")
        elif kind == "coverage":
            percent = observed.get("percent")
            if percent is None:
                # 「缺覆盖」和「没跑」不是同一件事，所以它是自己的一个状态，而不是 NOT_RUN。
                state, why = COVERAGE_MISSING, (observed.get("why")
                                                or "没有覆盖读数：这一项缺覆盖")
            else:
                state, why = COVERED, f"覆盖率 {percent}（一个读数，不是通过与否）"
        elif observed.get("ran") is False:
            state, why = NOT_RUN, (observed.get("why") or "源答了：这一项没有运行")
        elif not observed.get("ran"):
            state, why = UNOBSERVED, (observed.get("why")
                                      or "观察里没有 `ran`：不知道它跑没跑")
        else:
            code = observed.get("exit_code")
            if code is None:
                state, why = HALF_ANSWERED, "源答了「跑了」但没有退出码"
            elif code == 0:
                state, why = PASSED, "退出码 0"
            else:
                state, why = FAILED, f"退出码 {code}"
        out.append({"name": str(record["name"]), "kind": kind,
                    "state": state, "why": why,
                    "ok": state == PASSED})
    return out


# ---------------------------------------------------------------------------
# 组合
# ---------------------------------------------------------------------------

def progress_verdict(work: dict, quay: dict) -> tuple[str, str]:
    """本周期到底有没有进展。三个值，不是两个。

    `NONE` 要求**至少有一个源答了**且两个源都没给出任何一条东西。这是这个函数存在的全部
    理由：把「没答」也算成 `NONE`，一次没跑成的测量就会渲染成一句「本周无事发生」。反过来
    把「答了零」算成 `UNKNOWN` 同样坏——那会让一份真的没有进展的日报读起来像一次故障。
    """
    findings = len(work.get("commits") or ()) + len(quay.get("gates") or ())
    if findings:
        return SOME, (f"{len(work.get('commits') or ())} 笔提交、"
                      f"{len(quay.get('gates') or ())} 条 Quay 闸门记录落在窗口内。")
    answered = [name for name, block in (("工作树", work), ("Quay", quay))
                if block.get("answered")]
    if answered:
        return NONE, (f"{'、'.join(answered)}答了：窗口内一条记录都没有。"
                      f"这不是「没查」——是查了，结果是零。")
    return UNKNOWN, ("没有任何一个源答上来：工作树与 Quay 都返回了缺口。"
                     "**这不是「没有进展」。** 没答和答了零是两件事，"
                     "把前者写成后者就是这份文档最不该犯的那个错。")


def compose(*, period, work_delta, quay, run_status=(), focus=None,
            generated_at=None, fallbacks=None, document=None) -> dict:
    """把三个已测量的块组合成一份文档的机器可读形态。

    这个函数不测量、不读文件、不碰网络。它的输入已经是一份份「源答了什么」的块，所以它
    能做的判断只有组合。这一层分开的价值在于测试可以钉住判断本身，而不用先造一个 git 仓库。
    """
    contract_doc = document or contract()
    if fallbacks is None:   # 不是 `or`：见 Fallbacks 上的注释，空登记簿是假值
        fallbacks = Fallbacks()
    window = period_window(period)
    if generated_at is None:
        # 一个被默认的时钟也要报出来。这份文档的读者要能分辨「这行时间是调用方给的」和
        # 「这行时间是生成它的那台机器上当时读的」——两者的可信度不一样。
        generated_at = datetime.now(timezone.utc).isoformat()
        clock = "host"
    else:
        clock = "caller"

    verdict, because = progress_verdict(work_delta, quay)
    statuses = classify_run_status(run_status)

    blocking = [row for row in statuses if row["state"] in
                (FAILED, NOT_RUN, UNOBSERVED, HALF_ANSWERED, COVERAGE_MISSING)]

    claimed = _claimed_done_without_a_pass(quay)

    return {
        "schema": SCHEMA,
        "period": window,
        "generated_at": generated_at,
        "clock": clock,
        "progress": verdict,
        "progress_because": because,
        "focus": _focus_block(focus),
        "work": work_delta,
        "quay": quay,
        "run_status": statuses,
        "blocking": blocking,
        "claimed_done_without_a_pass": claimed,
        "fallbacks": list(fallbacks.entries),
        "contract": str(CONTRACT),
        "skeleton": str(SKELETONS),
    }


def _focus_block(focus) -> dict:
    if focus is None:
        return {"named": False, "items": [],
                "why": ("调用方没有给出本周期选定的重点。这不是「没有重点」——"
                        "是没有人把重点写进这份输入，而这份文档不会替谁回忆一个。")}
    if isinstance(focus, str):
        focus = [focus]
    return {"named": bool(focus), "items": [str(item) for item in focus],
            "why": None}


def _claimed_done_without_a_pass(quay: dict) -> list[dict]:
    """声称完成、但本周期没有任何一条通过的闸门记录。

    这不是在怀疑谁说谎，是在报告两个计数器不一致：`tasks/` 里的 `status: done` 是**声称**，
    `.quay/gate-events.jsonl` 里的 `verdict: pass` 是**执行结果**。全仓的规矩是后者才算数，
    所以两者不一致时唯一诚实的做法是把不一致本身写出来，而不是挑一边信。
    """
    passed = {gate["item"] for gate in (quay.get("gates") or ())
              if str(gate.get("verdict")) == GATE_PASS}
    out = []
    for task in quay.get("tasks") or ():
        if str(task.get("status")) == "done" and task["id"] not in passed:
            out.append({"id": task["id"], "title": task.get("title"),
                        "why": "任务是 `done`，但窗口内没有它的通过记录"})
    return out


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------

def _table(headers, rows) -> str:
    rows = [[("" if c is None else str(c)).replace("|", "\\|").replace("\n", " ")
             for c in row] for row in rows]
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join(out)


def _section(title: str, body: str, level: int = 2) -> str:
    return f"{'#' * level} {title}\n\n{body.rstrip()}\n"


def skeleton(path: Path | str = SKELETONS, doc_id: str = SKELETON_ID) -> dict:
    """本份文档的形状，从 `intake/document-skeletons.yaml` 读。

    这是**别人**写的 authority。这里不抄一份标题列表的原因很直接：抄一份之后，有人改了
    skeleton，文档的形状就不再跟着变，而两边都不会报错——文档照样生成，只是它已经不再是
    那份文档了。
    """
    path = Path(path)
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except OSError as exc:
        raise Refused(
            f"读不了 {path}：{exc}。这份文档的形状只有一个来源；读不到它就只剩两个选择——"
            f"拒绝生成，或者自己编一套标题。编出来的那份和真的长得一样，所以这里拒绝。"
        ) from exc
    for entry in loaded.get("documents") or ():
        if entry.get("id") == doc_id:
            return entry
    raise Refused(f"{path} 里没有 id 为 {doc_id!r} 的文档。"
                  f"要生成一份 skeleton 没有声明的文档，得先有人声明它")


#: skeleton 的标题 → 本 producer 能不能填、从哪填。**没有列进来的标题不会被省略**，它会被
#: 渲染成「这一节本 producer 没有来源」并说出它的来源可能是什么。省略一节在读者眼里和
#: 「这一节没事可说」是同一件事，而这两件事完全不同。
_SECTION_SOURCES = {
    "今日重点": "focus",
    "实际推进": "work-delta + quay-record",
    "卡住": "run-status + quay-record",
}


def render(document: dict, shape: dict | None = None) -> str:
    """文档的 markdown。标题与顺序来自 skeleton，内容来自已经组合好的判断。"""
    shape = shape or skeleton()
    lines = [
        _stamp(document),
        "",
        f"# 日报 · {document['period']['start']} → {document['period']['end']}",
        "",
    ]
    for entry in shape.get("sections") or ():
        heading = entry.get("heading") or ""
        source = _SECTION_SOURCES.get(heading)
        if source is None:
            body = _no_source(heading, entry.get("because"))
        elif heading == "今日重点":
            body = _body_focus(document)
        elif heading == "实际推进":
            body = _body_progress(document)
        else:
            body = _body_blocked(document)
        lines.append(_section(heading, body))
    # 这一节不在 skeleton 里，是有意的：skeleton 描述的是**这份报告**的形状，而兜底清单与
    # 「本 producer 不投递」这两件事是关于**这份产出**的交代，不是报告内容的一部分。它们
    # 必须落在文档正文里而不是只落在回执里——回执是给机器核对的，而读到兜底的人往往只是
    # 在翻这份 markdown。
    lines.append(_section("投递与缺口", _body_receipt_gaps(document), 2))
    return "\n".join(lines)


def _stamp(document: dict) -> str:
    return (f"<!-- ⟦produced⟧ {document['generated_at']} "
            f"(clock={document['clock']}) by `runtime/daily_report.py` · "
            f"schema={document['schema']} · contract={document['contract']} · "
            f"skeleton={document['skeleton']} -->")


def _no_source(heading: str, because) -> str:
    return (f"**这一节本 producer 没有来源。** 它要的是：{because}\n\n"
            f"这个 producer 的输入只有工作差量、Quay 记录、运行结果与重点四类"
            f"（`{CONTRACT}#inputs.accepted`），而这一节的来源不在其中。"
            f"写一句听起来合理的话把这里填上，比留空更坏：读者分不出哪一句是测出来的。")


def _body_focus(document: dict) -> str:
    focus = document["focus"]
    if not focus["named"]:
        return f"{focus['why']}\n\n本节因此是**缺口**，不是「本周期没有重点」。"
    return "\n".join([f"- {item}" for item in focus["items"]])


def _body_progress(document: dict) -> str:
    work, quay = document["work"], document["quay"]
    limit = top_n()
    parts = [f"**本周期进展：{document['progress']}。** {document['progress_because']}", ""]

    # 未提交的改动不参与 progress 的判定：它们没有时间信息，归不到任何一个窗口。
    # 但把它们藏起来更坏——它们是工作树此刻真实的形状，只是不能算作本周期记录的进展。
    if work.get("uncommitted"):
        parts += [f"工作树此刻有 {len(work['uncommitted'])} 个未提交的改动。"
                  f"**它们不计入上面的进展判定**：`git status` 没有时间信息，"
                  f"无法判断这些改动是不是本周期做的。", ""]

    parts += ["### 提交", ""]
    commits = work.get("commits") or []
    if not work.get("answered"):
        parts += [f"**没有答案。** {work.get('why')}", ""]
    elif not commits:
        parts += ["窗口内没有提交。这条是问出来的：检出读得到，窗口是给定的，结果是零。", ""]
    else:
        shown = commits[:limit]
        parts += [_table(
            ["提交", "何时", "谁", "标题", "文件"],
            [[c["revision"][:12], (c["committed_at"] or "")[:19], c["actor"],
              (c["subject"] or "")[:80],
              "数不出来" if c["file_count"] is None else str(c["file_count"])]
             for c in shown]), ""]
        if len(commits) > len(shown):
            parts += [f"另有 {len(commits) - len(shown)} 笔提交未列出"
                      f"（共 {len(commits)} 笔，本表列出 {len(shown)} 笔）。", ""]
    for note in work.get("notes") or ():
        parts += [f"- 注：{note}"]
    if work.get("notes"):
        parts += [""]

    parts += ["### Quay 闸门记录", ""]
    gates = quay.get("gates") or []
    if not quay.get("answered"):
        parts += [f"**没有答案。** {quay.get('why')}", ""]
    elif not gates:
        parts += [f"账本读到了（`{quay['ledger']}`），但窗口内没有条目。"
                  f"这是问出来的零，不是没问。", ""]
    else:
        shown = gates[:limit]
        parts += [_table(
            ["任务", "闸门", "判定", "何时", "理由"],
            [[g["item"], g["gate"], g["verdict"], (g["timestamp"] or "")[:19],
              (g["reason"] or "")[:90]] for g in shown]), ""]
        if len(gates) > len(shown):
            parts += [f"另有 {len(gates) - len(shown)} 条未列出"
                      f"（窗口内共 {len(gates)} 条，本表列出 {len(shown)} 条）。", ""]
    if quay.get("out_of_window"):
        parts += [f"账本里另有 {quay['out_of_window']} 条落在窗口外，未计入本周期。", ""]
    for problem in quay.get("problems") or ():
        where = problem.get("file") or f"第 {problem.get('line')} 行"
        parts += [f"- **账本有一行读不了**（{where}）：{problem['why']}"
                  f"；原文 {problem.get('text', '')[:120]!r}。"
                  f"这一行既不算有记录也不算没有记录。"]
    for unknown in quay.get("unknown_verdicts") or ():
        parts += [f"- **认不出的判定词**（第 {unknown['line']} 行）："
                  f"`{unknown['verdict']}`。它既不算通过也不算失败。"]
    if quay.get("problems") or quay.get("unknown_verdicts"):
        parts += [""]
    return "\n".join(parts)


def _body_blocked(document: dict) -> str:
    parts = []
    blocking = document["blocking"]
    if not blocking:
        parts += ["本周期没有未运行、未测或失败的项。这一句的前提是下面的输入确实给出了观察——"
                  "没有给出观察的项会出现在这里，而不是消失。", ""]
    else:
        parts += [_table(
            ["项", "类", "状态", "为什么"],
            [[row["name"], row["kind"], row["state"], row["why"][:110]]
             for row in blocking]), ""]
    for row in document["claimed_done_without_a_pass"]:
        parts += [f"- **{row['id']}**（{row.get('title') or ''}）：{row['why']}。"
                  f"`status: done` 是声称，闸门记录是执行结果；两者不一致时这里报不一致，"
                  f"不替任何一边说话。"]
    if document["claimed_done_without_a_pass"]:
        parts += [""]
    return "\n".join(parts)


def _body_receipt_gaps(document: dict) -> str:
    return "\n".join([
        _fallback_lines(document) if document["fallbacks"] else _no_fallback_line(),
        "",
        "本 producer **不投递**。文档写到哪里、投给了谁、投没投到，是调用方带进来的事实；"
        "这份文档不声称任何一次投递发生过。回执见同目录的 `publication-receipt.json`。",
    ])


def _fallback_lines(document: dict) -> str:
    lines = [f"**本周期有 {len(document['fallbacks'])} 处兜底。** "
             f"这些位置本该是测出来的值："]
    for entry in document["fallbacks"]:
        lines.append(f"- {entry['what']} → `{entry['value']}`：{entry['why']}")
    return "\n".join(lines)


def _no_fallback_line() -> str:
    """「没有兜底」是一句答案，不是一句沉默。

    空列表和一次没跑成的登记在同一份文档里长得一样，所以这句话必须写出来；`Fallbacks.summary`
    是同一个判断的另一半，两处共用一种说法。
    """
    return Fallbacks().summary()


# ---------------------------------------------------------------------------
# 回执
# ---------------------------------------------------------------------------

def publication_states() -> tuple:
    """投递结果的四个词，从通知契约读。

    这里不自己定义一份。这四个词已经存在于 `runtime/notification-contract.yaml` 的
    `alerts.delivery.values`，而「到达了没有」在所有地方都该是同一组词——第二组词迟早会
    在某一边多出一个来，而那时没有任何东西会报错。
    """
    delivery = (_alerting().policy() or {}).get("delivery") or {}
    values = delivery.get("values")
    if not isinstance(values, list) or not values:
        raise Refused(
            f"通知契约里读不到 `alerts.delivery.values`（读到的是 {values!r}）。"
            f"没有一个词汇表，回执就只剩一个「成功/失败」的布尔，"
            f"而把「没人试过」压进「失败」会让人以为有人试过")
    return tuple(str(word) for word in values)


def receipt(document: dict, text: str, *, out_dir, recipient=None, channel=None,
            state=None, attempted_at=None, why=None) -> dict:
    """投递给谁、什么时候、内容是哪一版。

    三个问题各有各的字段，没有一个是推算出来的：`audience` 答第一个，`attempted_at` 与
    `composed_at` 答第二个，`document.sha256` 答第三个。`sha256` 是对**写下去的那串字节**
    算的，所以「回执说的是哪一版」不是一句承诺，是一个可复核的读数。

    这个函数最重要的一行是 `delivered` 的算法：它只在 `state == DELIVERED` 时为真，而
    `state` 在调用方什么都没说时是 `UNKNOWN`。写盘成功与投递成功在这里没有任何关系。
    """
    states = publication_states()
    state = state if state is not None else "UNKNOWN"
    if state not in states:
        raise Refused(
            f"{state!r} 不是 {list(states)} 里的任何一个（来自 "
            f"`runtime/notification-contract.yaml#alerts.delivery.values`）。"
            f"一个没人能叫出名字的结果，会被读成一次投递")
    recipient = (str(recipient).strip() or None) if recipient is not None else None
    if state == "DELIVERED" and recipient is None:
        # 这条不是格式检查。一份说不清投给谁的「已投递」，本 producer 无从核实，
        # 而它会被下游当成一份已送达的日报。
        raise Refused(
            "回执声称 DELIVERED，但没有收件人。说不清投给谁的投递不是一个投递；"
            "如果确实投了，请把收件人带进来；如果没有，state 应当是 UNKNOWN")

    payload = text.encode("utf-8")
    return {
        "schema": RECEIPT_SCHEMA,
        "report": {"schema": document["schema"], "period": document["period"],
                   "progress": document["progress"],
                   "generated_at": document["generated_at"]},
        "document": {
            "file": None,   # 由 `publish` 填：写在哪里是调用方决定的
            "sha256": hashlib.sha256(payload).hexdigest(),
            "bytes": len(payload),
        },
        "audience": {"recipient": recipient, "channel": channel},
        "publication": {
            "state": state,
            "delivered": state == "DELIVERED",
            "composed_at": document["generated_at"],
            "attempted_at": attempted_at,
            "why": why or _publication_reason(state, recipient),
        },
        "out_dir": str(out_dir),
        "counts": {"commits": len(document["work"].get("commits") or ()),
                   "quay_gates": len(document["quay"].get("gates") or ()),
                   "blocking": len(document["blocking"]),
                   "fallbacks": len(document["fallbacks"])},
    }


def _publication_reason(state: str, recipient) -> str:
    if state == "DELIVERED":
        return "调用方报告这次投递被接受了。本 producer 没有经手这次投递，"
    if state == "UNKNOWN":
        return ("没有人告诉过这份文档投没投出去：本 producer 不投递，"
                "投递的结果只能由调用方带进来。它**不是**「没投」")
    return f"调用方报告的投递结果是 {state}。"


# ---------------------------------------------------------------------------
# 落盘
# ---------------------------------------------------------------------------

def _atomic_write_text(path: Path, text: str) -> None:
    """先写同目录的临时文件再 rename。

    `agent_control_store._atomic_write` 是 JSON 的，这里要写的是 markdown。同一个决定的
    第二半——临时文件必须和目标同一个目录，`os.replace` 才是一次原子替换——照抄那份实现，
    因为回执里的 sha256 说的是「完整的那一版」：一个被读到一半的 .md 与被它背书的 sha256
    对不上，而读者无从知道该信哪一个。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=str(path.parent),
        prefix=path.name + ".", suffix=".tmp", delete=False)
    try:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        os.replace(handle.name, path)
    except BaseException:
        try:
            os.unlink(handle.name)
        except OSError:
            pass
        raise


def publish(document: dict, text: str, receipt_payload: dict, out_dir) -> dict:
    """把文档与回执写到 `out_dir`，返回两个路径。

    `out_dir` 必须是绝对的，且**不在本仓内**。后者不是洁癖：一份写进 revision 的日报会
    被下一次更新整份换掉，而它读起来和一份留在人找得到的地方的日报一模一样——这与
    `runtime/serveros-daily.service` 的抬头写下的那条理由是同一条。
    """
    directory = Path(out_dir)
    if not directory.is_absolute():
        raise Refused(f"out_dir 是 {out_dir!r}，不是绝对路径。相对路径的含义取决于"
                      f"谁在什么时候从哪个目录调用它，而日报不能写到一个没人选过的位置上")
    resolved = directory.resolve()
    if resolved == ROOT or ROOT in resolved.parents:
        raise Refused(
            f"out_dir（{resolved}）在本仓内。写进 revision 的日报会被下一次更新整份换掉，"
            f"而它读起来和一份留着的一样；`runtime/serveros-daily.service` 已经有这条理由")

    # 回执背书的字节必须**就是**写下去的那串字节。`receipt()` 算的 sha256 是调用方交给它的
    # 那份文本的，而这里写的是调用方交给 `publish` 的那份文本；两者是同一个参数的两个副本，
    # 只在调用方把它们传岔了的时候不同。传岔了的结果是一份指向另一版内容的回执——它读起来
    # 完全正常，且没有任何后续步骤能发现它。所以这里当场比一次，不一致就拒绝。
    backed = (receipt_payload.get("document") or {}).get("sha256")
    actual = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if backed != actual:
        raise Refused(
            f"回执声称的内容版本是 {backed!r}，而这次要写下去的字节的 sha256 是 {actual!r}。"
            f"回执指向的是另一份内容。继续写下去，读者会拿到一份背书了别的版本的回执")

    # 文件名来自声明这份文档形状的那份 skeleton（`daily-report.md`），不是这里编的。
    filename = _skeleton_filename()
    doc_path = resolved / filename
    receipt_path = resolved / "publication-receipt.json"

    _atomic_write_text(doc_path, text)
    receipt_payload = dict(receipt_payload)
    receipt_payload["document"] = dict(receipt_payload["document"], file=str(doc_path))
    _store_io()._atomic_write(receipt_path, receipt_payload)
    return {"document": str(doc_path), "receipt": str(receipt_path),
            "sha256": receipt_payload["document"]["sha256"]}


def _skeleton_filename() -> str:
    name = skeleton().get("file")
    if not name:
        raise Refused(f"{SKELETONS} 里 {SKELETON_ID} 没有声明 `file`。"
                      f"文件名是这份文档身份的一半；没有它，回执说的「哪一版」就只剩下"
                      f"一个 sha256，而没人能凭它找到文件")
    return str(name)
