#!/usr/bin/env python3
"""C 线 —— 一条可重放的收据链：从飞书来源修订到投递回执，接在既有 producer 上。

WHY THIS MODULE EXISTS
----------------------
B1 的捕获、B3 的需求、A7 的验收、B8 的报告、B10 的投递，各自的 producer 都已经存在，
而且每一个单独看都拒绝得正确。缺的不是其中任何一个，是**它们之间那几处转接**：一条来源
修订走完全程时，谁记住它、在什么地方必须停下来问人、以及一次自称的 DONE 凭什么不算证据。

三件已经存在的 producer 各自把一件事做对了，而把它们接起来的那条缝没有人负责：

    runtime/daily_report.py      组合与落盘；它自己不投递，也不测量来源
    runtime/report_delivery.py   投递纪律；它不生成、不组合、不知道飞书是什么
    intake/workchain.py          看板与 Item；它不判来源修订

所以这个模块**不**重新实现其中任何一件，也不新增第二个 registry 或调度器。它是那条缝。
它的产物是一条有序的收据链（`receipts`），外加它**必须停在哪儿**——每一个停法都在
`runtime/journey-contract.yaml` 里是一个闭集里的词。

WHAT IT REFUSES TO DO
---------------------
- **它不把一条消息读成一条需求。** 「源的 revision 动了」是一件关于来源的事实；开一项
  工作是从一个被接受的目标作出的**决定**。这两者之间的那一步必须有人。所以 `propose()`
  产出的东西叫候选，`work_ref` 是 `None`，而 `open_work()` 没有一次**被验证过的**人类
  授权就拒绝。
- **它不把调用方自己说的「我是人」当身份。** `context/actor-context-contract.yaml` 的第一句
  就是这件事：**identity is a lookup, not an argument**。一个能宣称自己背后有人的 Agent
  运行时，可以为此后的每一次变更背书，而它背后的每一道准入闸门都变成了装饰。所以
  `authorize()` **不读** JSON 里的 kind/by/trusted/APPROVE 文本——那些只被用来**拒绝**，
  从不被用来接受。身份只能来自一次 lookup：调用方呈上一个 `context_ref`，由注入的
  权威验证器把它换成一条 `context/actor.py` 形状的可信记录，再交给**那个模块自己的**
  `validate()` 与 `authorizes()` 判。验证器没有绑定时，答案是 `NEEDS_HUMAN`——不是
  「先信着」，也不是一个默认通过的兜底。
- **它不把重复或编辑读成第二项工作。** 收据的身份是消息身份 `(source_id, message_id)`，
  不是 revision。同一条消息被改了是**同一条消息的新修订**，不是第二条消息。
- **它不接受自称。** 一次运行的结论来自**观察**（真的跑了、退出码是多少），用的是
  `runtime/daily_report.py#classify_run_status` 的同一套词。调用方说 DONE 而没有观察，
  得到的是 `UNOBSERVED`——而那正是它该得到的。
- **它不把「没人试过」压进「失败」。** 投递的四个词从 `runtime/report_delivery.py` 读，
  这里不定义第五个；一次没有消费者的投递仍然叫待发。
- **它不读时钟、不读环境变量、不碰网络。** 时间由调用方带进来，与
  `abilities/resolver.py#resolve` 和 `runtime/daily_report.py#compose` 同一条纪律：
  一个自己挑时间的模块写出的记录，读起来和一次有人选过时间的记录一模一样。
- **它不发明路径。** `out_dir` 由调用方给，且必须是绝对路径、不在本仓内——理由与
  `runtime/daily_report.py#publish` 逐字相同。
"""
from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - environment guarantee
    sys.stderr.write("ERROR: PyYAML is required to read the journey contract\n")
    raise SystemExit(2)

_HERE = Path(__file__).resolve().parent
ROOT = _HERE.parent

SCHEMA = "serveros/journey.v1"
CONTRACT = _HERE / "journey-contract.yaml"
CONTRACT_SCHEMA = "serveros/journey-contract.v1"


def _load(name: str, path: Path):
    """One canonical name per sibling, cached.

    `runtime/recipes.py` and `runtime/cli.py` already load `daily_report` under
    `serveros_runtime_daily_report`, and `tests/load_census.py` measures how many
    times a file's source is executed in one process. A second name for the same
    file is how two `Refused` classes with one spelling come to exist — the defect
    `context/actor.py`'s docstring records. So these two names are the only ones
    this module takes.
    """
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _daily_report():
    return _load("serveros_runtime_daily_report", _HERE / "daily_report.py")


def _report_delivery():
    return _load("serveros_runtime_report_delivery", _HERE / "report_delivery.py")


def _actor():
    """The trusted actor plane, loaded under its one name.

    This module does NOT re-implement any of the checks below. It presents a ref,
    and the answers come from `context/actor.py` — `validate()`, `authorizes()`,
    `normalize_actor_ref()`, `verify_delegation()`. A second copy of "may this
    context authorize a mutation" is how two answers to one security question
    come to exist, and the one that drifts is the one nobody re-reads.
    """
    return _load("serveros_context_actor", ROOT / "context" / "actor.py")


class Refused(Exception):
    """这次输入不能变成一条收据，理由随消息一起说出来。"""


# ---------------------------------------------------------------------------
# 词表：只从契约读，不在代码里抄第二份
# ---------------------------------------------------------------------------

def contract(path: Path | str = CONTRACT) -> dict:
    path = Path(path)
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise Refused(f"读不到 journey 契约 {path}：{exc}") from exc
    if not isinstance(document, dict):
        raise Refused(f"{path} 不是一份映射，读到的是 {type(document).__name__}")
    if document.get("schema") != CONTRACT_SCHEMA:
        raise Refused(
            f"{path} 的 schema 是 {document.get('schema')!r}，期望 {CONTRACT_SCHEMA!r}。"
            f"词表换了名字而读者不知道，是这一类比「文件不在」更安静的错")
    return document


def _closed_ids(document: dict, block: str) -> tuple:
    """A closed list of `id:`s. A block that is missing or empty is a refusal, not
    an empty vocabulary — the same rule `daily_report.publication_states` gives:
    an empty word list collapses every answer into one word."""
    rows = document.get(block)
    if not isinstance(rows, list) or not rows:
        raise Refused(f"契约里读不到 `{block}`（读到的是 {rows!r}）")
    ids = tuple(str(row["id"]) for row in rows if isinstance(row, dict) and row.get("id"))
    if len(ids) != len(rows):
        raise Refused(f"`{block}` 里有一条没有 id 的条目：{rows!r}")
    return ids


def capture_states(document: dict | None = None) -> tuple:
    return _closed_ids(document or contract(), "capture_states")


def receipt_kinds(document: dict | None = None) -> tuple:
    return _closed_ids(document or contract(), "receipt_kinds")


def stops(document: dict | None = None) -> tuple:
    return _closed_ids(document or contract(), "stops")


def claim_kinds_that_proceed(document: dict | None = None) -> tuple:
    """声称自己是人类入口的 kind —— 它们**不因此获得任何东西**。

    这个列表里的一切只说明「这条输入可以被送去验证」。名字就是它的含义：一个 claim
    是字符串，不是一个身份。
    """
    block = (document or contract()).get("claim_kinds") or {}
    rows = block.get("proceeds_to_verification")
    if not isinstance(rows, list) or not rows:
        raise Refused("契约里读不到 `claim_kinds.proceeds_to_verification`："
                      "没有它，就没有任何声称能被送去验证")
    return tuple(str(row["id"]) for row in rows if isinstance(row, dict) and row.get("id"))


def refused_claim_kinds(document: dict | None = None) -> dict:
    """`{id: because}` —— 不能产生授权的来源，**具名**。

    一个只有「不合法」的拒绝，收到的人不知道自己做错了什么，也不知道什么才算对。
    具名之后，答复是「子任务 DONE 不产生授权」这一句。
    """
    block = (document or contract()).get("claim_kinds") or {}
    rows = block.get("refused_by_name") or []
    return {str(row["id"]): str(row.get("because") or "")
            for row in rows if isinstance(row, dict) and row.get("id")}


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

def _sha12(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _need(record, key, what: str) -> str:
    if not isinstance(record, dict):
        raise Refused(f"{what} 是一个 {type(record).__name__}，不是一份带着 {key} 的记录")
    value = record.get(key)
    if value is None or not str(value).strip():
        raise Refused(f"{what} 没有给出 {key}。缺这一栏的记录无法被核对，也无法被追问")
    return str(value)


def message_ref(source_id: str, message_id: str) -> str:
    """A message's identity, as one string. Identity is the MESSAGE, not the
    revision — that is the whole reason an edit is not a second Work."""
    return f"{source_id}|{message_id}"


def _receipt(kind: str, ref: str, revision, state: str, why: str, at) -> dict:
    kinds = receipt_kinds()
    if kind not in kinds:
        raise Refused(f"{kind!r} 不是一个声明过的收据环节（词表是 {list(kinds)}）")
    return {"kind": kind, "ref": ref, "revision": revision,
            "state": state, "why": why, "at": at}


# ---------------------------------------------------------------------------
# B1 —— 捕获：消息身份 + 修订
# ---------------------------------------------------------------------------

def empty_store() -> dict:
    return {"schema": SCHEMA, "messages": {}}


def capture(store: dict, observations) -> dict:
    """把一批来源观察折进以**消息身份**建索引的捕获记录。

    三种结局在 `capture_states` 里，判据是 (消息身份, 修订)：

        未见过                  → NEW
        同身份、同 revision      → ALREADY_CAPTURED   记录不变
        同身份、不同 revision    → NEW_REVISION        追加一条修订，身份不变

    第二种与第三种的区别是这份模块存在的一半理由：一个把到达次数当事实的系统，会在
    源没有变的时候越长越厚（那条曲线是「读了几次」的函数，不是「源改过几次」的函数），
    而一个把每条到达都当新消息的系统，会把一条被改过的消息变成两项工作。
    """
    if not isinstance(store, dict):
        raise Refused(f"store 是 {type(store).__name__}，不是一份捕获记录")
    messages = {str(k): dict(v) for k, v in (store.get("messages") or {}).items()}
    outcomes = []
    for obs in observations or ():
        source_id = _need(obs, "source_id", "一条来源观察")
        message_id = _need(obs, "message_id", f"{source_id} 的一条观察")
        revision = _need(obs, "revision", f"{message_ref(source_id, message_id)}")
        captured_at = _need(obs, "captured_at", f"{message_ref(source_id, message_id)}")
        trust = _need(obs, "trust", f"{message_ref(source_id, message_id)}")
        key = message_ref(source_id, message_id)
        previous = messages.get(key)

        if previous is None:
            state = "NEW"
            why = "这条消息第一次被捕获"
        elif str(previous.get("revision")) == revision:
            state = "ALREADY_CAPTURED"
            why = (f"同一个 revision（{revision}）又答了一次：这是同一件事实被读了第二遍。"
                   f"捕获记录不变——按到达次数增长的那条曲线是「读了几次」的函数，"
                   f"不是「源改过几次」的函数")
        else:
            state = "NEW_REVISION"
            why = (f"同一条消息从 {previous.get('revision')} 变成了 {revision}："
                   f"这是**同一条消息被改了**，不是第二条消息。身份仍是 {key}")

        record = previous or {"source_id": source_id, "message_id": message_id,
                              "revisions": [], "first_captured_at": captured_at}
        if state != "ALREADY_CAPTURED":
            record["revisions"] = list(record.get("revisions") or []) + [
                {"revision": revision, "captured_at": captured_at, "trust": trust}]
            record["revision"] = revision
            record["captured_at"] = captured_at
            record["trust"] = trust
        messages[key] = record
        outcomes.append(_receipt("capture", key, revision, state, why, captured_at))

    return {"store": {"schema": SCHEMA, "messages": messages}, "outcomes": outcomes}


def source_revision(store: dict, refs) -> str:
    """A digest over the CURRENT revision of each named message, order-independent.

    This is the thing a human decision is made *about*. It exists so that an edit
    arriving after a decision can be told apart from the version that decision
    covered — otherwise "already approved" becomes a property of the requirement's
    name rather than of the content nobody re-read.
    """
    parts = []
    for ref in sorted(set(str(r) for r in refs)):
        record = (store.get("messages") or {}).get(ref)
        if record is None:
            raise Refused(
                f"{ref} 不在捕获记录里。一个没有被捕获过的来源不能被引用——"
                f"引用它会造出一条指向不存在的事实的收据")
        parts.append(f"{ref}@{record.get('revision')}")
    if not parts:
        raise Refused("没有任何来源。一条不指向任何来源的需求不是需求，是一句话")
    return _sha12("|".join(parts))


# ---------------------------------------------------------------------------
# B3 —— 需求候选，以及「接受」这一步
# ---------------------------------------------------------------------------

def propose(store: dict, *, goal, acceptance=(), sources) -> dict:
    """从已捕获的消息形成一条需求**候选**。它不是 Item，也不会有 work_ref。

    候选的身份是 (goal, 消息身份集合)。**修订不在身份里** —— 这正是「编辑不产生
    第二项工作」在数据上的样子：同一批消息、同一个目标，无论它们的正文改过几次，
    拿到的都是同一个 `requirement_ref`；变的只有 `source_revision`。
    """
    goal = str(goal or "").strip()
    if not goal:
        raise Refused(
            "没有给出目标。捕获到的消息本身不是需求——「源变了」是关于来源的事实，"
            "而一个目标是一个人从这些事实里读出来的东西。这一步不会替谁读")
    refs = [str(r) for r in (sources or ())]
    if not refs:
        raise Refused("一条候选没有指向任何来源：它就无法被追溯，而不可追溯的推进"
                      "与编造出来的推进在读者眼里是同一件事")
    revision = source_revision(store, refs)
    requirement_ref = "requirement:" + _sha12(goal + "||" + "|".join(sorted(set(refs))))
    criteria = [str(item) for item in (acceptance or ())]
    return {
        "requirement_ref": requirement_ref,
        "goal": goal,
        "acceptance": criteria,
        "sources": [{"ref": ref,
                     "revision": (store["messages"][ref] or {}).get("revision")}
                    for ref in sorted(set(refs))],
        "source_revision": revision,
        "state": "PROPOSED",
        "work_ref": None,
        "why": ("这是一条候选。它没有 work_ref，因为在有人对它作出决定之前，"
                "它还不是一项工作——把「源变了」接成「新工作」正是这一步必须拦住的事"),
    }


def authorize(proposal: dict, *, claim=None, verifier=None, now=None) -> dict:
    """一次**被验证过的**人类授权，或者一个说得清楚的停法。

    这是整条链上唯一的信任边界，所以它读什么、不读什么必须逐字说清楚。

    **不读的** —— 调用方 JSON 里的这些只被用来**拒绝**，从不被用来接受：

        `kind: "human"`   一句话，不是身份
        `by: "..."`      一个字符串，不是主体
        `for_revision`   一个自称，不是版本绑定
        `trusted: true`  更糟：它看起来像证明，而它什么都不是
        `APPROVE` 文本    一份产物，不是一个人

    **读的** —— `claim["context_ref"]`，而且它只是一个**把手**。
    `context/actor.py#resolve` 的注释写了这条协议的两半：调用方呈上一个 ref，拿到一条
    记录或一次 miss，而 miss 不是要被抹平的异常，它就是答案。

    `verifier` 把那个把手换成一条可信记录。它是**注入**的，因为「谁是权威」是部署的事实
    而不是一个模块常量：生产入口是 `signed_delegation_provider()`，测试注入一个独立的 fake。
    **`verifier is None` 时答案是 `NEEDS_HUMAN`**，没有别的分支 —— 一个没有验证器的进程
    不能因为调用方在 JSON 里写了什么就产出授权。

    停法（`REFUSED` 与 `NEEDS_HUMAN` 的分界是「这次输入不成立」对「只差一个人的答复」）：

        调用方声称一个具名的非人类来源 → REFUSED，具名说出它为什么不产生授权
        没有 context_ref              → REFUSED：声称有人，却拿不出任何把手
        验证器没有绑定                 → NEEDS_HUMAN
        验证器抛错 / 答一次 miss       → REFUSED：它断言的授权没有任何 provider 报过
        记录不是声明的形状             → REFUSED
        记录不是 FRESH/VERIFIED/FACT   → NEEDS_HUMAN：可能只是过期，再问一次
        批准覆盖的不是当前这一版来源    → NEEDS_HUMAN：它是对的，只是不再覆盖眼前这一版
        主体不是这一项期望的人          → REFUSED
    """
    claim = claim if isinstance(claim, dict) else {}
    claimed_kind = str(claim.get("kind") or "").strip()
    refused = refused_claim_kinds()
    humans = claim_kinds_that_proceed()
    if claimed_kind and claimed_kind not in humans:
        because = refused.get(claimed_kind)
        if because:
            raise Refused(f"一个 `{claimed_kind}` 的决定不产生授权。{because}")
        raise Refused(
            f"`{claimed_kind}` 不是一个能产生授权的来源（会的是 {list(humans)}）。"
            f"认不出的来源按不产生授权处理——把没见过的词读成「可以」是这一类缺陷里最安静的一种")

    if verifier is None:
        return {"state": "NEEDS_HUMAN", "context_ref": None,
                "why": ("这次输入声称有人同意了，但没有任何可信的 Actor 验证器被绑定，"
                        "所以那个声称无法被换成一条记录。身份是一次 lookup 而不是一个"
                        "参数——一个能宣称自己背后有人的进程，可以为它此后的每一次变更"
                        "背书。要接受这一条，需要把验证器接上：生产入口是 "
                        "`runtime/journey.py#signed_delegation_provider`，它只认 "
                        "`context/actor.py#verify_delegation` 验过的签名证据")}

    ref = str(claim.get("context_ref") or "").strip()
    if not ref:
        raise Refused(
            "这次输入声称有人同意了，却没有呈上任何 `context_ref`。身份是一次 lookup 而"
            "不是一个参数：拿不出把手的『有人同意了』与没有同意没有区别")

    try:
        resolution = verifier(claim, now)
    except Exception as exc:
        # A verifier that raised is a check that did not happen, and a check that
        # did not happen authorizes nothing. Reading it as a yes is the same
        # defect §23.2 names for a transport: unknown is not safe-to-repeat.
        raise Refused(
            f"验证器在回答之前抛出了 {type(exc).__name__}: {exc}。这次检查没有发生，"
            f"而一次没有发生的检查不授权任何东西") from exc
    if not isinstance(resolution, dict):
        raise Refused(f"验证器答了一个 {type(resolution).__name__}，不是一份带 found 的解析结果")
    if not resolution.get("found"):
        raise Refused(
            f"`{ref}` 没有被任何 HumanControlProvider 报过："
            f"{resolution.get('because') or '（验证器没有给理由）'}。"
            f"呈上一个自己编的 ref 得到的不是一条默认记录——它就是一次 miss")

    actor = _actor()
    record = resolution.get("context")
    try:
        actor.validate(record)
    except Exception as exc:
        raise Refused(f"验证器给的记录不是可信 Actor 契约的形状：{exc}") from exc

    # The handle has to be the handle. Without this the presented `context_ref`
    # is decoration — an interface where the caller presents A and the authority
    # answers about B hands "who is this about" back to the caller, which is the
    # whole thing the lookup exists to prevent.
    resolved_ref = str(record.get("context_ref") or "").strip()
    if resolved_ref != ref:
        raise Refused(
            f"调用方呈上的把手是 {ref}，而验证器给出的记录自称 {resolved_ref}。"
            f"两者不是同一条上下文——一个把 ref 当摆设的接口，等于把「这是关于谁的」"
            f"交回给调用方")

    verdict = actor.authorizes(record, now)
    if not verdict["authorizes"]:
        return {"state": "NEEDS_HUMAN", "context_ref": ref,
                "actor_ref": record.get("actor_ref"),
                "why": (f"这条记录的形状成立，但它现在不授权一次变更："
                        f"{verdict['because']}。过期的证据不是假的证据——它是需要"
                        f"再问一次的证据")}

    approved = str(resolution.get("approved_revision") or "").strip()
    if approved != proposal["source_revision"]:
        return {"state": "NEEDS_HUMAN", "context_ref": ref,
                "actor_ref": record.get("actor_ref"),
                "why": (f"被验证过的批准覆盖的是来源版本 {approved or '（验证器没有给出这一版）'}，"
                        f"而 {proposal['requirement_ref']} 现在是 "
                        f"{proposal['source_revision']}。来源在批准之后动过——"
                        f"这一版需要重新问一次，而不是把旧批准套在新内容上")}

    expected = str(claim.get("approver_ref") or "").strip()
    if expected:
        try:
            got = actor.normalize_actor_ref(record["actor_ref"])
            want = actor.normalize_actor_ref(expected)
        except Exception as exc:
            raise Refused(f"主体引用读不了：{exc}") from exc
        if got != want:
            raise Refused(
                f"这次批准来自 {got}，而这一项期望的是 {want}。说得出是谁、与说得出"
                f"是**谁**，是两件事——前者是身份，后者是这一项的授权范围")

    grant_ref = str(resolution.get("grant_ref") or "").strip()
    if not grant_ref:
        raise Refused(
            "这次批准没有说它依据的是哪一份 grant。一个说不出自己凭什么的批准，"
            "在它被复查的那天无从被复查")

    return {"state": "ACCEPTED", "context_ref": ref,
            "actor_ref": record["actor_ref"], "message_ref": record["message_ref"],
            "grant_ref": grant_ref, "approved_revision": approved, "at": now,
            "freshness": verdict["state"],
            "why": (f"来自可信人类入口 {record['actor_ref']} 的批准"
                    f"（{record['human_control_provider']} 经 {record['transport']} 验证，"
                    f"新鲜度 {verdict['state']}），依据 grant {grant_ref}，"
                    f"覆盖来源版本 {approved}")}


def open_work(proposal: dict, authorization: dict, *, epoch: int = 1) -> dict:
    """B4：只有被接受之后，才铸造工作引用与它的 owner。

    这里**不**做 owner/epoch 的 fencing —— 那件事的权威是
    `runtime/agent_control_store.py`（它按 (Item, epoch) 拒绝旧结果晚到覆盖新 epoch）。
    这里保证两件事：

    1. 一个没有被**验证过**的批准，拿不到 work_ref；
    2. **重复批准不产生第二项工作。** work_ref 是
       `(requirement_ref, 被验证的主体, 那次批准的事件, epoch)` 的纯函数，而
       `context/actor-context-contract.yaml` 说 `message_ref` 是「这条上下文所关于的
       那**一个**事件，所以一条消息不能授权两个动作」。于是同一件事被批两次得到的是
       **同一个** work_ref，而不是两项工作 —— 这一条不需要任何账本就能成立，
       因为它是身份的函数，不是一次查表的结果。
    """
    if (authorization or {}).get("state") != "ACCEPTED":
        raise Refused(
            "没有一次被**验证过**的批准，就不能铸造工作引用。候选不是工作："
            "把 `propose` 的产物直接当成 Item，正是「没有人的批准就没有工作」"
            "这条规则唯一可能失效的地方")
    if not isinstance(epoch, int) or epoch < 1:
        raise Refused(f"epoch 是 {epoch!r}，不是一个 >= 1 的整数")
    owner = _need(authorization, "actor_ref", "一次被接受的批准")
    event = _need(authorization, "message_ref", "一次被接受的批准")
    work_ref = "work:" + _sha12(
        f"{proposal['requirement_ref']}|{owner}|{event}|{epoch}")
    return {"work_ref": work_ref, "requirement_ref": proposal["requirement_ref"],
            "owner": owner, "approved_by_event": event,
            "grant_ref": authorization.get("grant_ref"), "epoch": epoch,
            "source_revision": proposal["source_revision"],
            "why": (f"由 {owner} 通过 {authorization.get('grant_ref')} 批准的候选铸造出来的"
                    f"工作引用（依据的事件 {event}）。epoch={epoch}；写者交接的 fencing 在 "
                    f"runtime/agent_control_store.py，不在这里")}


def signed_delegation_provider():
    """生产入口：把调用方呈现的**签名证据**交给 Actor 契约自己的验证器。

    这是 `authorize(verifier=...)` 应当被接上的那一个 —— 「谁是权威」是部署的事实，
    所以它是一个**可注入的 Provider**，而不是本模块里写死的一个函数。它只做三件事：

      1. 取 `claim["evidence"]` —— 调用方呈现的材料，**不可信**；
      2. 交给 `context/actor.py#verify_delegation` —— issuer、公钥、受众、Ed25519 签名、
         主体、时间窗、grant 窗口与预算**全部由那个模块判**，这里一个字都不重写
         （第二份实现就是第二个会漂移的地方，而漂移的那一个一定是不常跑的）；
      3. 把结果翻译成本模块的解析形状。

    第 3 步里最关键的是 `approved_revision`：它取自**签名载荷**里的 `scope_digests`
    （`covers` 里的引用为键），不是调用方 JSON 里的任何字段。取不到就是 `None`，
    而 `authorize` 对 `None` 的答案是 `NEEDS_HUMAN` —— 拿不出被签名的版本绑定时，
    fail-closed。

    刻意**没有** `trust` 参数：`context/actor.py#delegation_trust` 写了为什么——一个
    准入路径可以指向别处的信任根，就是一个由调用方选择的信任根。

    **信任问题返回 `found: False`，而不是抛异常。** Actor 契约的每一个
    `DELEGATION_*` code 都被**原样**放进 `because`，于是「为什么没通过」不是一个说法，
    而是一个可以 grep 的判据：`DELEGATION_PROVIDER_UNBOUND`（信任根没绑定）、
    `DELEGATION_SIGNATURE_INVALID`（签名不对）、`DELEGATION_SCOPE_INVALID`（grant 越界）、
    `DELEGATION_VERIFIER_UNAVAILABLE`（没有 Ed25519 支持）。两种表达都是 fail-closed，
    但这一种留下了理由。

    在这台机器上这个入口是 **BLOCKED**，原因是可核对的：项目 venv 里没有 `cryptography`
    （答 `DELEGATION_VERIFIER_UNAVAILABLE`），且 `~/.config/serveros/human-control-trust.json`
    不存在（答 `DELEGATION_PROVIDER_UNBOUND`）。这条路径的**可执行性**由
    `tests/journey_provider_fixture.py` + `tests/test_journey_provider.py` 在测试侧提供，
    而那两个文件从不进入任何生产导入路径。
    """
    def verify(claim, now):
        actor = _actor()
        evidence = (claim or {}).get("evidence")
        try:
            grant, context = actor.verify_delegation(evidence, now=now)
        except actor.Rejected as exc:
            # Fail closed, and keep the contract's own word for why.
            return {"found": False, "because": str(exc)}
        presented = str((claim or {}).get("context_ref") or "").strip()
        if presented and context["context_ref"] != presented:
            return {"found": False,
                    "because": ("PRESENTED_REF_MISMATCH: 呈上的 context_ref 不是这份委派"
                                "被签发给的那一条")}
        digests = grant.get("scope_digests") or {}
        covers = [str(c) for c in (grant.get("covers") or [])]
        approved = next((digests[c] for c in covers if c in digests), None)
        return {"found": True, "context": context,
                "grant_ref": grant.get("grant_ref"),
                "approved_revision": approved,
                "because": "provider-signed delegation verified"}
    return verify


# ---------------------------------------------------------------------------
# A7 —— 一次运行值多少，从观察读
# ---------------------------------------------------------------------------

def run_verdict(run) -> dict:
    """一次运行的结果，用 `runtime/daily_report.py#classify_run_status` 的同一套词。

    同一套词这一点是可核对的，不是一句好话：这个函数不自己判退出码，它把观察交给那个
    已经存在、已经被六条判据钉住的函数，再读它的回答。两个判据会让「通过」有两层意思，
    而哪天它们分歧，分歧的那一处一定是不常跑的那一处。

    一次**自称**（`claim: DONE` 而没有 `observed`）得到的是 `UNOBSERVED`。这不是刁难：
    `docs/ABILITY-OPERATING-MODEL.md` §9 把「child 自报成功」与 A7 验收分得很开。
    """
    if not isinstance(run, dict):
        raise Refused(f"run 是一个 {type(run).__name__}，不是一份带着 name 的记录")
    name = _need(run, "name", "一次运行")
    claim = str(run.get("claim") or "").strip()
    try:
        classified = _daily_report().classify_run_status([run])[0]
    except Exception as exc:  # daily_report.Refused 是另一个类；这里翻成自己的
        raise Refused(f"{name} 的观察读不了：{exc}") from exc
    accepted = classified["state"] == _daily_report().PASSED
    why = classified["why"]
    if claim and not accepted:
        why = (f"调用方报告的是 `{claim}`，而观察的是 `{classified['state']}`：{why}。"
               f"一次没有被观察到的结论不是证据——自己的结论不算，跑出来的才算")
    return {"kind": "acceptance", "ref": name, "state": classified["state"],
            "accepted": accepted, "why": why, "at": (run.get("observed") or {}).get("at")
            if isinstance(run.get("observed"), dict) else None}


# ---------------------------------------------------------------------------
# B8 —— 组合与落盘（不投递）
# ---------------------------------------------------------------------------

def _gap_work(because: str) -> dict:
    """An honest "not measured" work block. The shape `daily_report.render` reads.

    Not an empty measurement: `answered: False` is what makes the document say
    "nobody looked", which is a different sentence from "nothing happened".
    """
    return {"root": None, "answered": False, "commits": [], "uncommitted": [],
            "notes": [], "window": None, "why": because}


def _gap_quay(because: str) -> dict:
    """Verbatim the shape `runtime/cli.py` uses when `--run-dir` is absent."""
    return {"root": None, "ledger": None, "tasks_dir": None, "answered": False,
            "gates": [], "tasks": [], "problems": [], "notes": [],
            "out_of_window": 0, "unknown_verdicts": [], "why": because}


def archive(*, period, work_delta, quay, run_status, focus, at, out_dir,
            recipient=None, channel=None) -> dict:
    """B8：组合 → 渲染 → 回执 → 落盘。四步按顺序，且这一步**不投递**。

    它把 `runtime/daily_report.py` 的全部四步原样串起来，一个字都不重写。这里存在的
    理由只有一个：让这条链有一个地方说得出「文档是哪一版」，而 B10 的投递要的正是它。
    """
    report = _daily_report()
    document = report.compose(period=period, work_delta=work_delta, quay=quay,
                              run_status=run_status, focus=focus, generated_at=at)
    text = report.render(document, report.skeleton())
    payload = report.receipt(document, text, out_dir=out_dir, recipient=recipient,
                             channel=channel)
    written = report.publish(document, text, payload, out_dir)
    return {"document": document, "written": written,
            "sha256": written["sha256"],
            "state": "ARCHIVED",
            "why": (f"文档已落盘（{written['document']}）并带上一份指向它自己 sha256 的"
                    f"回执。这一步没有发送任何东西")}


# ---------------------------------------------------------------------------
# B10 —— 投递（用既有纪律）
# ---------------------------------------------------------------------------

def deliver(out_dir, *, recipient, requested_by, why, sender, authorization=None,
            at=None) -> dict:
    """B10：记一条待发意图，然后做一次尝试。纪律全部来自 `report_delivery`。

    两个词在这条链上必须分开，而它们很容易被读成一个：

        outbox 的 `PENDING`    「这件事还没落定」
        投递的 `DELIVERED/FAILED/UNSUPPORTED/UNKNOWN`   「这一次尝试的结果是什么」

    `sender=None` 是**没有消费者**：entry 保持 `PENDING`，答案是 `PENDING_CONSUMER`，
    而**不写任何回执**——因为没有任何人试过，写一条回执会让一次没有发生的尝试留下痕迹。
    """
    delivery = _report_delivery()
    asked = delivery.request(out_dir, recipient=recipient, requested_by=requested_by,
                             why=why)
    if asked.get("state") == "ALREADY_REQUESTED":
        # The intent already stands. `deliver` below still runs on it — the
        # pending entry is the same one, and attempting again is exactly what a
        # second call is for. Recorded here so the chain's own receipt says why
        # no new intent appeared.
        pass
    result = delivery.deliver(out_dir, sender=sender, authorization=authorization, at=at)
    return {"intent": asked.get("intent"), "intent_state": asked.get("state"),
            "intent_why": asked.get("why"), "result": result,
            "state": result.get("state"), "why": result.get("why")}


# ---------------------------------------------------------------------------
# 把上面几步串成一条链
# ---------------------------------------------------------------------------

def _stop(receipts: list, stop: str, why: str, refs: dict) -> dict:
    if stop not in stops():
        raise Refused(f"{stop!r} 不是一个声明过的停法（词表是 {list(stops())}）")
    return {"schema": SCHEMA, "refs": refs, "receipts": receipts,
            "stop": stop, "why": why}


def walk(journey: dict, *, out_dir=None, sender=None, at=None, verifier=None) -> dict:
    """一条旅程：从一批来源观察，走到它该停的地方。

    它**按需**走，不是一条固定流水线 —— `docs/ABILITY-OPERATING-MODEL.md` §12 的
    「不把接缝变成固定流水线」。一个只给 observations 的调用，在 B1 之后停成
    `CAPTURED_ONLY`，而这是对的：一条会议消息可以捕获完就结束，不创建 Work Item。

    每一步都会往 `receipts` 里追加一条，然后在**必须停**的地方停下。停下来的位置就是
    这条链最有信息的输出：它说的是「下一步归谁」。
    """
    if not isinstance(journey, dict):
        raise Refused(f"journey 是一个 {type(journey).__name__}，不是一份输入")

    got = capture(empty_store(), journey.get("observations") or [])
    receipts = list(got["outcomes"])
    store = got["store"]
    refs = {"messages": sorted(store["messages"])}

    goal = str(journey.get("goal") or "").strip()
    if not goal:
        return _stop(receipts, "CAPTURED_ONLY",
                     "来源已捕获，但没有给出目标。这条链到 B1 为止——"
                     "捕获不是立项，而一个系统自己从消息里读出一个目标，"
                     "正是 B3 的边界要挡住的事", refs)

    sources = journey.get("sources") or sorted(store["messages"])
    proposal = propose(store, goal=goal, acceptance=journey.get("acceptance") or (),
                       sources=sources)
    refs["requirement_ref"] = proposal["requirement_ref"]
    refs["source_revision"] = proposal["source_revision"]
    receipts.append(_receipt("requirement", proposal["requirement_ref"],
                             proposal["source_revision"], "PROPOSED",
                             proposal["why"], at))

    claim = journey.get("approval_claim")
    if not claim:
        return _stop(receipts, "NEEDS_HUMAN",
                     f"{proposal['requirement_ref']} 已成为候选，但没有人的批准。"
                     f"没有批准就没有工作——这一条停在这里，等一个可信人类入口的答复",
                     refs)

    if at is None:
        raise Refused(
            "走到授权这一步需要一个时钟（at）。新鲜度是关于「现在」的判断，而一个自己读"
            "时钟的模块会产出一条没人能复现其窗口的记录——`context/actor.py#build` 对 "
            "`now` 立过同一条规矩")

    authorization = authorize(proposal, claim=claim, verifier=verifier, now=at)
    receipts.append(_receipt("decision", proposal["requirement_ref"],
                             proposal["source_revision"], authorization["state"],
                             authorization["why"], at))
    if authorization["state"] != "ACCEPTED":
        return _stop(receipts, "NEEDS_HUMAN", authorization["why"], refs)

    work = open_work(proposal, authorization)
    refs["work_ref"] = work["work_ref"]
    refs["approved_by"] = authorization["actor_ref"]
    receipts.append(_receipt("work", work["work_ref"], work["source_revision"],
                             f"OPEN@{work['epoch']}", work["why"], at))

    run = journey.get("run")
    if run is None:
        return _stop(receipts, "NOT_ACCEPTED",
                     f"{work['work_ref']} 已开工，但没有任何运行结果。"
                     f"没有观察就没有验收——一次没有跑过的东西不能算通过，"
                     f"也不能算没通过：它是没跑", refs)

    verdict = run_verdict(run)
    receipts.append(_receipt("acceptance", verdict["ref"], work["source_revision"],
                             verdict["state"], verdict["why"], verdict["at"]))
    if not verdict["accepted"]:
        return _stop(receipts, "NOT_ACCEPTED", verdict["why"], refs)

    period = journey.get("period")
    if not period:
        raise Refused(
            "要走 B8，就必须给出 period（{start, end}）。一个自己挑窗口的报告写出的"
            "记录，读起来和一次有人选过窗口的记录一模一样——`daily_report` 对 "
            "`--start/--end` 已经立过同一条规矩")
    work_delta = journey.get("work_delta") or _gap_work(
        "这条旅程没有测量工作树：调用方没有给 work_delta。这是缺口，"
        "不是「本周期没有提交」")
    quay = journey.get("quay") or _gap_quay(
        "这条旅程没有读 Quay 的账本：调用方没有给 quay。这是缺口，"
        "不是「本周期没有闸门记录」")
    if out_dir is None:
        raise Refused(
            "走到 B8 需要 out_dir。报告与它的回执要落在一个调用方选定的绝对路径上，"
            "而且不在本仓内——理由与 `runtime/daily_report.py#publish` 逐字相同")

    delivery_journey = journey.get("delivery") or {}
    recipient = delivery_journey.get("recipient")
    archived = archive(period=period, work_delta=work_delta, quay=quay,
                       run_status=[run], focus=journey.get("focus"), at=at,
                       out_dir=out_dir, recipient=recipient,
                       channel=delivery_journey.get("channel"))
    refs["report_sha256"] = archived["sha256"]
    receipts.append(_receipt("report", archived["written"]["document"],
                             archived["sha256"], "ARCHIVED", archived["why"], at))

    if not recipient:
        return _stop(receipts, "PENDING",
                     "报告已归档，但没有收件人，所以没有任何待发意图被记下。"
                     "无人 receiver 不冒充「已经安排投递」", refs)

    delivered = deliver(out_dir, recipient=recipient,
                        requested_by=delivery_journey.get("requested_by"),
                        why=delivery_journey.get("why"), sender=sender,
                        authorization=delivery_journey.get("authorization"), at=at)
    refs["delivery_intent"] = delivered["intent"]
    receipts.append(_receipt("delivery", delivered["intent"],
                             archived["sha256"], delivered["state"],
                             delivered["why"], at))
    return _stop(receipts, _delivery_stop(delivered["state"]), delivered["why"], refs)


#: 投递的四个词，每个落到这条链自己的停法上。这是一张**映射**，不是第二份词表：
#: 左边从 `report_delivery` 读（它自己又从通知契约读），右边从 `stops` 读。
_DELIVERY_STOP = {
    "DELIVERED": "DELIVERED",
    "FAILED": "UNKNOWN",
    "UNSUPPORTED": "UNKNOWN",
    "UNKNOWN": "UNKNOWN",
    "PENDING_CONSUMER": "PENDING",
    "UNKNOWN_OUTCOME": "UNKNOWN",
    "ALREADY_DELIVERED": "DELIVERED",
    "REFUSED": "REFUSED",
}


def _delivery_stop(state: str) -> str:
    """A delivery word becomes a journey stop, and an unrecognised one is a
    refusal rather than a default.

    `FAILED` maps to `UNKNOWN` and not to a stop of its own on purpose: at this
    level the reader's question is not "what did the transport say" — it is
    "who owns the next step", and for both `FAILED` and `UNKNOWN` the answer is
    the same person. The transport's own word is kept verbatim in the receipt
    above, so nothing is lost by not inventing a second vocabulary here.
    """
    if state not in _DELIVERY_STOP:
        raise Refused(
            f"投递给了一个这张表不认识的答案 {state!r}。认不出的答案既不能读成到达，"
            f"也不能读成失败——它只能是一个没人处理过的状态")
    return _DELIVERY_STOP[state]


# ---------------------------------------------------------------------------
# 给人读的一条链
# ---------------------------------------------------------------------------

def render(record: dict) -> list:
    """The chain as lines. `why` is printed in full: it is the only field that
    says who owns the next step, and truncating it is how a refusal reads as a
    shrug."""
    lines = [f"journey      {record['schema']}",
             f"stop         {record['stop']}"]
    for key in sorted(record.get("refs") or {}):
        lines.append(f"  {key:<18} {record['refs'][key]}")
    lines.append("")
    for step in record.get("receipts") or ():
        lines.append(f"- {step['kind']:<11} {step['state']:<18} "
                     f"{str(step['ref'])[:40]:<40} {step['why']}")
    lines.append("")
    lines.append(f"            {record['why']}")
    return lines


def main(argv=None) -> int:  # pragma: no cover - the CLI lives in runtime/cli.py
    raise SystemExit("runtime/journey.py is a library; use `serverosctl journey walk`")


if __name__ == "__main__":  # pragma: no cover
    main()
