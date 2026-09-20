#!/usr/bin/env python3
"""SYS-10 文档治理：一份 canonical 文档的稳定身份，以及它是不是派生物。

    python3 document_governance.py --request identity.json

本模块回答四个问题，而且是同一份记录里的四个字段，不是四次查找：

    这份文档是谁的        → record.owner
    属于哪个项目/Item     → record.identity.project / record.item
    权威版本在哪          → record.authority.at
    它是不是派生物        → record.standing / record.decision

为什么需要它
------------
一份文档的身份如果就是它的路径，那么移动文件就是换了一份文档：owner 换了、权威
版本换了、引用它的地方全部变成指向别的东西的引用。所以 ref 是
`doc:<project>/<kind>/<name>`，它不含路径；"权威版本在哪"是**源**给出的一个字段，
内核逐字携带，从不构造、从不解析、从不打开。

两条被结构性地分开的事
----------------------
1. **源答了没有，与源答了什么。** 源说"没有这样一份文档"是**发现**（NOT_DECLARED）；
   源没有回答是**缺口**（SOURCE_UNAVAILABLE）。前者是关于这份文档的一条结论，后者
   一条结论都没有建立。把它们并成一个 `UNKNOWN` 是这条线上最容易犯、也最难事后
   发现的错：缺口会被读成结论，而缺口的正常归宿是"再去问一次"，不是"记一笔"。
   记录里 `source_answered` / `source_declared` / `is_gap` / `is_finding` 四个布尔
   把这件事说死，而不是留给读的人从一句话里猜。

2. **权威版本与副本。** `standing=DERIVATIVE` 的 ref 永远不会得到
   `is_the_authority=True`。这条不是靠"我们记得不要写错"维持的：`copied_from` 与
   `authoritative_at` 上的两条相反约束使它机械成立 —— 一份自称权威的文档不能同时
   声明自己抄自某处，一份派生物不能让它的权威版本落在它自己的位置上。

内核的性质
----------
纯函数：给定同样的 (ref, source, vocabulary)，返回逐字节相同的记录。不读盘、不读
网络、不读时钟、不读环境变量。词表是显式传入的参数（`load_vocabulary()` 只是给
调用方读文件的便利函数），所以判定过程里没有一处 I/O。

闭词表：`documents/document-vocabulary.json` 声明了判断可以落在哪些值上。遇到未声明
的值，内核**拒绝**并同时说出"给的是哪一个、声明里有哪几个"，不会落到兜底类别 ——
兜底会把每一个还没想过的类别静默收下，而收下之后没有一处能说出它是什么。

一个常量兜底，且必须报出来
--------------------------
`SOURCE_FALLBACK`：源的应答没有署名时用它，并且它一定出现在记录的 `fallbacks` 里。
两种更省事的写法都是错的 —— 把没署名的应答记在某个具体源头上，是让一个没有说过话
的源头替这条结论负责；因为没署名就拒绝回答，则是把一份真实读数藏起来。所以兜底照用，
但必须报出来。

请求与应答的形状
----------------
请求（`answer()` 的入参，也是 `--request` 读的那个 JSON）：

    {"ref": "doc:<project>/<kind>/<name>",
     "schema": "serveros/document-governance-request.v1",      # 可选，给了就必须对
     "source": {"source_ref": …, "answered": true|false,
                "declared": true|false, "record": {…}},
     "sources": {"<ref>": <同样的 source 对象>, …},             # 可选：给了就顺带走链
     "chain_limit": 8}                                          # 可选

`source` 是"这份文档"的应答，`sources` 是"沿链每一环"的应答；两者是两份输入，
所以同一个 ref 可以在这里是"没有声明在管它"、在那里是"链走到权威版本"—— 那是
两个问题，不是一个矛盾。

应答：`{"schema": …, "record": <身份记录>, "citation": <引用行>}`，带 `sources` 时
再加一个 `"chain"`。

出口状态：判定返回 0，REJECTED（请求畸形）返回 2。NOT_DECLARED 与 SOURCE_UNAVAILABLE
都是成功的判定，返回 0 —— "没有"是一个答案，不是一次失败。
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

SCHEMA = "serveros/document-identity.v1"
REQUEST_SCHEMA = "serveros/document-governance-request.v1"
CHAIN_SCHEMA = "serveros/document-authority-chain.v1"
VOCABULARY_SCHEMA = "serveros/document-vocabulary.v1"

DECISIONS = (
    "CANONICAL",
    "DERIVATIVE",
    "NOT_DECLARED",
    "SOURCE_UNAVAILABLE",
    "REJECTED",
)

STANDINGS = ("CANONICAL", "DERIVATIVE")

SOURCE_STATES = ("ANSWERED_DECLARED", "ANSWERED_ABSENT", "UNANSWERED")

# 一条链走到头时停在哪。CYCLE 与 DEPTH_LIMIT 是值而不是异常：见 terminus 的词表注释。
TERMINI = ("CANONICAL", "NOT_DECLARED", "SOURCE_UNAVAILABLE", "CYCLE", "DEPTH_LIMIT")

REF_SHAPE = "doc:<project>/<kind>/<name>"

# ref 的合法形状。三段都是小写：ref 是逐字符比较的身份，而只差大小写的两个拼写
# 就是一份文档的两个身份 —— 那正是"稳定身份"要消掉的东西，所以这里拒绝而不是
# 归一化。归一化会让 `doc:p/report/R` 和 `doc:p/report/r` 静默变成同一个 ref，
# 于是同一条引用在两个调用点上指向两份记录。
_REF = re.compile(r"^doc:([a-z0-9][a-z0-9._-]*)/([a-z0-9][a-z0-9._-]*)/([a-z0-9][a-z0-9._-]*)$")

# 通配 ref 会让"引用一份文档"变成"引用任意一份文档"。与 meeting_relevance 的
# `_stable_id` 同一条禁令：一个能匹配全部的引用不是宽泛的引用，是没有引用。
WILDCARDS = ("*", "?", "%", ".*", "all", "any", "…")

#: 源没有署名时的常量兜底。它必须出现在返回记录的 `fallbacks` 里；静默使用它
#: 会把一条结论记在一个没有说过话的源头上。
SOURCE_FALLBACK = "source:unstated"

#: 沿 copied_from 往回流时的默认步数上限。它存在是因为一个畸形的声明可以让链
#: 无限长；到达上限时报 DEPTH_LIMIT 而不是返回一个不完整的链 —— 一个被截断的链
#: 看起来就像走到了头。
DEFAULT_CHAIN_LIMIT = 8


class Rejected(ValueError):
    """请求无法回答 —— 与"答案是：没有"是两件完全不同的事。

    畸形请求（调用方的 bug）抛这个；源答"没有这样一份文档"返回 NOT_DECLARED。
    把后者也做成异常，会让一次正常的发现看起来像一次崩溃，于是调用方会去
    修一段本来没有问题的代码。
    """


def default_vocabulary_path() -> Path:
    return Path(__file__).resolve().parent / "document-vocabulary.json"


def load_vocabulary(path: Path | str | None = None) -> dict:
    """读词表。这是本模块唯一的读盘入口，而它不在任何判定函数里。"""
    source = Path(path) if path else default_vocabulary_path()
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except OSError as exc:
        raise Rejected(f"读不到文档治理词表：{exc}") from exc
    except json.JSONDecodeError as exc:
        raise Rejected(f"文档治理词表不是合法 JSON：{exc}") from exc
    if not isinstance(data, dict) or data.get("schema") != VOCABULARY_SCHEMA:
        raise Rejected(
            f"不支持的文档治理词表 schema：{data.get('schema')!r}，"
            f"而本内核认的是 {VOCABULARY_SCHEMA!r}")
    return data


# --------------------------------------------------------------------------
# 词表读取
# --------------------------------------------------------------------------

def _values(vocabulary: dict, dimension: str) -> dict:
    raw = ((vocabulary.get("dimensions") or {}).get(dimension) or {}).get("values")
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, list):
        return {value: "" for value in raw}
    return {}


def _kind_scope(vocabulary: dict) -> dict:
    """每种文档属于 Item 还是节点。

    缺了这张表就拒绝：一份文档没有归属粒度时，"它属于哪个 Item"这个问题没有
    答案，而返回 None 会让它看起来像"还没有 Item"。
    """
    raw = (vocabulary.get("dimensions") or {}).get("kind_scope")
    if not isinstance(raw, dict) or not raw:
        raise Rejected("文档治理词表没有声明 kind_scope：无法判定一份文档属于 Item 还是节点")
    return {key: value for key, value in raw.items() if not key.startswith("_")}


# --------------------------------------------------------------------------
# 原语
# --------------------------------------------------------------------------

def _require(value, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise Rejected(f"{label} 给了 {value!r}（{type(value).__name__}），"
                       f"而要求是一个非空字符串")
    return value.strip()


def _declared(value, dimension: str, vocabulary: dict, label: str) -> str:
    """闭集合里的一个值，否则拒绝 —— 并且两个值都要说出来。

    只说"未知的类别"是不够的：调用方手里拿着一个具体拼写，需要知道的是它错在
    哪里、以及正确的拼写长什么样。所以消息里同时出现给的值和声明的全部值。
    """
    text = _require(value, label)
    declared = _values(vocabulary, dimension)
    if text not in declared:
        raise Rejected(f"{label} 给的是 {text!r}，而声明的 {dimension} 只有 "
                       f"{sorted(declared)}")
    return text


def _at(value, label: str, vocabulary: dict) -> dict:
    """一个位置：哪一种表面 + 该表面上的定位串。

    内核不解释 locator —— 它不是路径、不是 URL、不是任何它自己会去碰的东西，
    只是源给出的一个字符串，被逐字携带。任何"顺手把它规范化一下"的写法都会
    让内核变成第二个定位权威，而空间解析器已经把这条边界定在适配器那边。
    """
    if not isinstance(value, dict):
        raise Rejected(f"{label} 给了 {value!r}（{type(value).__name__}），"
                       f"而要求是一个 {{surface, locator}} 对象")
    surface = _declared(value.get("surface"), "surface", vocabulary, f"{label}.surface")
    locator = _require(value.get("locator"), f"{label}.locator")
    return {"surface": surface, "locator": locator}


def parse_ref(value, label: str = "ref") -> dict:
    """把 `doc:<project>/<kind>/<name>` 拆成三段，或者拒绝它。"""
    text = _require(value, label)
    if text.strip().lower() in WILDCARDS or any(w in text for w in WILDCARDS):
        raise Rejected(f"{label} 给的是 {text!r}，而通配符不允许出现在 ref 里："
                       f"一个能匹配任何文档的 ref 不能替任何一份文档负责")
    match = _REF.match(text)
    if not match:
        raise Rejected(f"{label} 给的是 {text!r}，而合法形状是 {REF_SHAPE}"
                       f"（三段全小写，允许 0-9 a-z . _ -）")
    project, kind, name = match.groups()
    return {"ref": text, "project": project, "kind": kind, "name": name}


# --------------------------------------------------------------------------
# 源应答：三种状态之一，畸形在这里被拒，不进入判断
# --------------------------------------------------------------------------

def read_source(answer, vocabulary: dict) -> dict:
    """把一个源应答规整成三种状态之一。

    `answered` 与 `declared` 都不允许缺省，两个拒绝各自防住一种把缺口读成结论
    的写法：

      * `answered` 缺省为 True，会把"源没有回答"读成"源答了：没有这样一份文档"，
        也就是把缺口变成一条关于文档的发现。
      * `declared` 缺省为 False，会在源其实答了"有"的时候报 NOT_DECLARED ——
        一条关于文档不存在的结论，而源从来没有说过这句话。

    两种状态各自要求源说出它查的是什么（`because` / `unanswered_because`）：
    一个不说查了什么的"没有"，与"没查"在下游看起来一模一样。
    """
    if not isinstance(answer, dict):
        raise Rejected(f"source 给了 {answer!r}（{type(answer).__name__}），而要求是一个对象"
                       f"（含 source_ref、answered，以及答过之后才读的 declared）")

    fallbacks: list[dict] = []
    source_ref = answer.get("source_ref")
    if source_ref is None or (isinstance(source_ref, str) and not source_ref.strip()):
        source_ref = SOURCE_FALLBACK
        fallbacks.append({
            "field": "source.source_ref",
            "used": SOURCE_FALLBACK,
            "because": "应答没有署名：不把它记在某个具体源头上，也不因为没署名就不回答",
        })
    else:
        source_ref = _require(source_ref, "source.source_ref")

    answered = answer.get("answered")
    if not isinstance(answered, bool):
        raise Rejected(f"source.answered 给的是 {answered!r}，而只接受 true / false："
                       f"缺省会让\"没有应答\"和\"答了没有\"变成同一个结论")

    if answered is False:
        return {
            "source_ref": source_ref,
            "source_state": "UNANSWERED",
            "because": _require(answer.get("unanswered_because"), "source.unanswered_because"),
            "record": None,
            "fallbacks": fallbacks,
        }

    declared = answer.get("declared")
    if not isinstance(declared, bool):
        raise Rejected(f"source.declared 给的是 {declared!r}，而只接受 true / false："
                       f"源答了之后必须说出它手里有没有这样一份文档")

    if declared is False:
        return {
            "source_ref": source_ref,
            "source_state": "ANSWERED_ABSENT",
            "because": _require(answer.get("because"), "source.because"),
            "record": None,
            "fallbacks": fallbacks,
        }

    return {
        "source_ref": source_ref,
        "source_state": "ANSWERED_DECLARED",
        "record": answer.get("record"),
        "fallbacks": fallbacks,
    }


def _declared_record(parsed: dict, record, vocabulary: dict) -> dict:
    """校验并规整源给出的那份记录。任何一条自相矛盾都在这里被拒绝。

    这一层的判断都是"两份声明互相打架，内核不替它们挑一个"：ref 里的 kind 与
    record 里的 kind、record 的 scope 与词表为这个 kind 声明的粒度、standing 与
    copied_from、standing 与两个位置。挑一个的写法会让记录看起来是干净的，而
    真正发生的是其中一份声明被悄悄丢掉了。
    """
    if not isinstance(record, dict):
        raise Rejected(f"source.record 给了 {record!r}（{type(record).__name__}），"
                       f"而要求是一个对象")

    standing = _declared(record.get("standing"), "standing", vocabulary,
                         "source.record.standing")
    kind = _declared(record.get("kind"), "kind", vocabulary, "source.record.kind")
    if kind != parsed["kind"]:
        raise Rejected(f"ref 里的 kind 是 {parsed['kind']!r}，而 source.record.kind 声明的是 "
                       f"{kind!r}：两者必须一致，内核不替它们挑一个 —— 挑一个会让引用一份"
                       f"文档，和读到另一份文档，看起来是同一次成功")
    scope = _declared(record.get("scope"), "scope", vocabulary, "source.record.scope")
    kind_scope = _kind_scope(vocabulary)
    expected_scope = kind_scope.get(kind)
    if expected_scope is None:
        # 词表没为这个 kind 声明粒度时不能让检查静默跳过：跳过的结果是"它属于
        # 哪个 Item"这个问题没人回答，而没人回答会被下游读成"不属于任何 Item"。
        raise Rejected(f"词表没有为 {kind} 声明归属粒度（kind_scope 里有 "
                       f"{sorted(kind_scope)}），无法判定它属于 Item 还是节点")
    if scope != expected_scope:
        raise Rejected(f"{kind} 在词表里声明为 {expected_scope!r}，而 source.record.scope "
                       f"给的是 {scope!r}：把一份 {kind} 声明成 {scope} 级文档会让它同时"
                       f"属于所有人，也就是不属于任何一次验收")

    owner = _require(record.get("owner"), "source.record.owner")
    item = record.get("item")
    if scope == "item":
        item = _require(item, "source.record.item")
    elif item is not None:
        raise Rejected(f"scope 是 {scope!r}（不属于任何 Item），而 source.record.item 给了 "
                       f"{item!r}：声明了 Item 就等于说它属于一次工作，而这份记录说它不属于")

    copy_at = _at(record.get("copy_at"), "source.record.copy_at", vocabulary)
    authoritative_at = _at(record.get("authoritative_at"),
                           "source.record.authoritative_at", vocabulary)
    copied_from = record.get("copied_from")

    if standing == "CANONICAL":
        if copied_from is not None:
            raise Rejected(
                f"standing 给的是 {standing!r}（权威版本），而 copied_from 给了 "
                f"{copied_from!r}：一份说得出自己抄自哪里的文档就不是权威版本；"
                f"保留这个矛盾会让读者同时相信两件相反的事")
        if authoritative_at != copy_at:
            raise Rejected(
                f"standing 给的是 {standing!r}，而 authoritative_at 是 {authoritative_at!r}、"
                f"copy_at 是 {copy_at!r}：两者不同意味着这个 ref 指的是副本、权威版本在别处，"
                f"那它应当声明为 DERIVATIVE")
    else:
        if copied_from is None:
            raise Rejected(
                f"standing 给的是 {standing!r}（派生物），而 copied_from 是空的："
                f"派生物不指向它抄自的那份文档时，没有任何字段能说出权威版本在哪")
        ancestor = parse_ref(copied_from, "source.record.copied_from")
        if ancestor["ref"] == parsed["ref"]:
            raise Rejected(f"copied_from 给的是 {ancestor['ref']!r}，与 ref 本身相同："
                           f"一份文档不能是自己的副本，否则权威版本就是它自己")
        if authoritative_at == copy_at:
            raise Rejected(
                f"standing 给的是 {standing!r}，而 authoritative_at 与 copy_at 都是 "
                f"{copy_at!r}：副本把权威版本指到了自己身上 —— 这正是要拦下的那次误判")

    return {
        "standing": standing,
        "kind": kind,
        "scope": scope,
        "owner": owner,
        "item": item,
        "copy_at": copy_at,
        "authoritative_at": authoritative_at,
        "copied_from": copied_from,
    }


# --------------------------------------------------------------------------
# 判定
# --------------------------------------------------------------------------

def resolve(ref, source, vocabulary: dict) -> dict:
    """一份文档的身份记录。四个问题一次答完，且答案的形状不随结果改变。

    记录里每个字段在任何一种判定下都存在（未判定时为 None），因为一个"缺少
    字段"的记录会被读成"这个字段还没有人说过"，而实际发生的是"这次判定没有
    走到那里"。两者对调用方的处置完全不同。
    """
    parsed = parse_ref(ref)
    answer = read_source(source, vocabulary)
    state = answer["source_state"]

    record = {
        "schema": SCHEMA,
        "ref": parsed["ref"],
        "identity": {"project": parsed["project"], "kind": parsed["kind"],
                     "name": parsed["name"]},
        "decision": None,
        "source_state": state,
        "source_answered": state != "UNANSWERED",
        "source_declared": state == "ANSWERED_DECLARED",
        "is_gap": False,
        "is_finding": False,
        "is_the_authority": False,
        "reason": "",
        "authority": {
            "source_ref": answer["source_ref"],
            "answered": state != "UNANSWERED",
            "declared": state == "ANSWERED_DECLARED",
            "at": None,
        },
        "standing": None,
        "owner": None,
        "scope": None,
        "item": None,
        "copy_at": None,
        "copied_from": None,
        "fallbacks": list(answer["fallbacks"]),
    }

    if state == "UNANSWERED":
        record["decision"] = "SOURCE_UNAVAILABLE"
        record["is_gap"] = True
        record["reason"] = (
            f"{answer['source_ref']} 没有回答（{answer['because']}）：这份文档是权威版本还是"
            f"派生物、归谁、权威版本在哪，一条都没有判定。这是缺口，不是关于这份文档的结论；"
            f"下游该做的是再去问一次，而不是把它记成一条发现。")
        return record

    if state == "ANSWERED_ABSENT":
        record["decision"] = "NOT_DECLARED"
        record["is_finding"] = True
        record["reason"] = (
            f"{answer['source_ref']} 答：{answer['because']}，它手里没有 {parsed['ref']}。"
            f"这是发现 —— 被引用的文档没有任何声明在管它。它不等于这份文档不存在，"
            f"也不等于它派生自别处；它等于没有人对它的权威版本负责。")
        return record

    declared = _declared_record(parsed, answer["record"], vocabulary)
    standing = declared["standing"]
    record.update({
        "standing": standing,
        "decision": standing,          # 闭集合的 standing 与 decision 同名，不是巧合：
                                       # 这是"这一维不能有第三种结果"的唯一一处编码。
        "is_the_authority": standing == "CANONICAL",
        "owner": declared["owner"],
        "scope": declared["scope"],
        "item": declared["item"],
        "copy_at": declared["copy_at"],
        "copied_from": declared["copied_from"],
        "authority": {
            "source_ref": answer["source_ref"],
            "answered": True,
            "declared": True,
            "at": declared["authoritative_at"],
        },
    })
    where = f"{declared['authoritative_at']['surface']} {declared['authoritative_at']['locator']}"
    who = f"{declared['owner']}"
    if declared["item"]:
        who += f"／{declared['item']}"
    if standing == "CANONICAL":
        record["reason"] = (
            f"{answer['source_ref']} 声明 {parsed['ref']} 就是这份文档的权威版本，位于 {where}，"
            f"归属 {who}。引用它的人手里的就是权威版本本身。")
    else:
        record["reason"] = (
            f"{answer['source_ref']} 声明 {parsed['ref']} 是派生物，不是权威版本：它抄自 "
            f"{declared['copied_from']}，权威版本位于 {where}，归属 {who}。引用它时必须连 "
            f"copied_from 一起带上 —— 只给 ref 会让读的人以为手里的是权威版本。")
    return record


def is_authoritative_reference(record: dict) -> bool:
    """这一条引用能不能当作权威版本来用。

    两个条件都读，而不是只读 decision：decision 是判定，is_the_authority 是
    "这个 ref 是不是权威版本"这个问题的答案。同时要求两者，是为了让一次记录被
    局部改写（或一次判定被改坏）时，这里不会只剩一个条件在说话。
    """
    return record.get("decision") == "CANONICAL" and record.get("is_the_authority") is True


def citation(record: dict) -> str:
    """引用这份文档时该写出来的那一行。

    派生物的那一行必须说出"这不是权威版本"，否则引用它的人无法从这一行看出
    区别 —— 而引用一行与实际去读的那份文档，是两件会被分开复制的东西。
    """
    decision = record.get("decision")
    ref = record.get("ref")
    if decision == "CANONICAL" and record.get("is_the_authority") is not True:
        # 一份记录可以是被拼出来的，而这一行会被复制进别的文档里当引用用。
        # 一处自相矛盾在这里挡住，比在读者的印象里挡住便宜得多。
        raise Rejected(f"{ref} 的记录自相矛盾：decision 是 CANONICAL，"
                       f"而 is_the_authority 是 {record.get('is_the_authority')!r}")
    if decision == "CANONICAL":
        at = record["authority"]["at"]
        line = f"{ref} —— 权威版本：{at['surface']} {at['locator']}"
    elif decision == "DERIVATIVE":
        at = record["authority"]["at"]
        line = (f"{ref} —— 派生，不是权威版本；权威版本是 {record['copied_from']}，"
                f"位于 {at['surface']} {at['locator']}")
    elif decision == "NOT_DECLARED":
        line = (f"{ref} —— 未被声明：{record['authority']['source_ref']} 答"
                f"\"没有这样一份文档\"。（发现，不是缺口）")
    elif decision == "SOURCE_UNAVAILABLE":
        line = (f"{ref} —— 无法判定：{record['authority']['source_ref']} 没有回答。"
                f"（缺口，不是关于这份文档的结论）")
    else:
        # REJECTED 走的是异常而不是记录，所以这里不是兜底类别，是"没有记录"。
        raise Rejected(f"没有可引用的记录：decision 是 {decision!r}")
    return line


# --------------------------------------------------------------------------
# 沿 copied_from 往回流
# --------------------------------------------------------------------------

def authority_chain(ref, sources: dict, vocabulary: dict,
                    limit: int = DEFAULT_CHAIN_LIMIT) -> dict:
    """顺着 copied_from 走到权威版本，走不到就说出停在哪里。

    这份函数存在的理由只有一条：`resolve` 只管一跳。一跳之外，一份"派生物的
    派生物"仍然是一个 DERIVATIVE 记录，而每一跳都看起来没问题 —— 于是一条
    A←B←C 的链可以让 A 被读成"权威版本在后面某一处"，而下游照抄 A。所以这里
    走完整条链，并且只有当真的走到一条 CANONICAL 记录时 `root_is_canonical`
    才是 True。

    停住的四种原因分别报告，不合并：CYCLE 是声明的缺陷，DEPTH_LIMIT 是查找的
    边界（缺口），NOT_DECLARED 是发现，SOURCE_UNAVAILABLE 是缺口。合并它们会
    让"数据有问题"和"我还没查完"变成同一句话。
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise Rejected(f"limit 给的是 {limit!r}，而要求是一个正整数：0 步的链"
                       f"什么都走不到，却会返回一个看起来正常的空结果")

    parsed = parse_ref(ref)
    chain: list[str] = []
    decisions: list[str] = []
    seen: set[str] = set()
    current = parsed["ref"]

    while len(chain) < limit:
        if current in seen:
            return _chain_record(parsed["ref"], chain, decisions, "CYCLE", current,
                                 f"{current} 在链上出现了第二次：声明说它抄自自己，"
                                 f"权威版本因此不存在于这条链的任何一环")
        seen.add(current)

        answer = sources.get(current)
        if answer is None:
            # 调用方没有给出这一环的应答。这是一个缺口，而且是三段状态里那种
            # "源没有回答"的缺口；把它当成"没有这样一份文档"会把一次没问过
            # 变成一条关于文档的发现。
            answer = {"answered": False,
                      "unanswered_because": f"调用方在 sources 里没有给出 {current} 的应答"}

        record = resolve(current, answer, vocabulary)
        chain.append(current)
        decisions.append(record["decision"])

        if record["decision"] in ("NOT_DECLARED", "SOURCE_UNAVAILABLE"):
            return _chain_record(
                parsed["ref"], chain, decisions, record["decision"], current,
                f"链断在 {current} 上（{record['decision']}）：{record['reason']}")
        if record["decision"] == "CANONICAL":
            return _chain_record(parsed["ref"], chain, decisions, "CANONICAL", current,
                                 f"链走到 {current} 就是权威版本；上面的每一环都是它的副本")
        current = record["copied_from"]

    return _chain_record(parsed["ref"], chain, decisions, "DEPTH_LIMIT", chain[-1],
                         f"走到第 {limit} 步（{chain[-1]}）仍然没有到权威版本。"
                         f"停止的是这次查找，不是这条链；这是缺口")


def _chain_record(ref: str, chain: list[str], decisions: list[str], terminus: str,
                  terminus_ref: str, reason: str) -> dict:
    return {
        "schema": CHAIN_SCHEMA,
        "ref": ref,
        "chain": list(chain),
        "decisions": list(decisions),
        "depth": max(len(chain) - 1, 0),
        "terminus": terminus,
        "terminus_ref": terminus_ref,
        "root_is_canonical": terminus == "CANONICAL",
        "is_gap": terminus in ("SOURCE_UNAVAILABLE", "DEPTH_LIMIT"),
        "is_finding": terminus in ("NOT_DECLARED", "CYCLE"),
        "reason": reason,
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def answer(request, vocabulary: dict) -> dict:
    """一份请求 → 一份答案。畸形的请求在这里变成 Rejected。"""
    if not isinstance(request, dict):
        raise Rejected(f"request 给了 {request!r}（{type(request).__name__}），"
                       f"而要求是一个对象")
    schema = request.get("schema")
    if schema is not None and schema != REQUEST_SCHEMA:
        raise Rejected(f"request.schema 给的是 {schema!r}，而本内核认的是 {REQUEST_SCHEMA!r}")
    if "ref" not in request:
        raise Rejected(f"request 里没有 ref，而要求是形如 {REF_SHAPE} 的一个字符串")
    if "source" not in request:
        raise Rejected("request 里没有 source，而要求是一个应答对象"
                       "（含 source_ref、answered）—— 没有源就没有可判定的东西")

    record = resolve(request["ref"], request["source"], vocabulary)
    out = {"schema": "serveros/document-governance-answer.v1", "record": record,
           "citation": citation(record)}
    sources = request.get("sources")
    if sources is not None:
        if not isinstance(sources, dict):
            raise Rejected(f"request.sources 给了 {sources!r}（{type(sources).__name__}），"
                           f"而要求是一个 {{ref: 应答}} 的对象")
        limit = request.get("chain_limit", DEFAULT_CHAIN_LIMIT)
        out["chain"] = authority_chain(request["ref"], sources, vocabulary, limit)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="SYS-10 文档治理：一份文档的身份、权威版本与它是不是派生物")
    parser.add_argument("--request", required=True, help="请求 JSON 的路径")
    parser.add_argument("--vocabulary", default=None, help="词表路径（默认随模块）")
    parser.add_argument("--json", action="store_true", help="输出完整记录而不是引用行")
    args = parser.parse_args(argv)

    try:
        vocabulary = load_vocabulary(args.vocabulary)
        request = json.loads(Path(args.request).read_text(encoding="utf-8"))
        out = answer(request, vocabulary)
    except (Rejected, json.JSONDecodeError, OSError) as exc:
        # 畸形请求的出口是 2，与判定失败（1）区分开：一次调用方的拼写错误
        # 不该看起来像一次答案。
        print(f"REJECTED {exc}")
        return 2

    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        record = out["record"]
        print(f"{record['decision']:17s} {out['citation']}")
        print(f"  {record['reason']}")
        for fallback in record["fallbacks"]:
            print(f"  FALLBACK {fallback['field']} → {fallback['used']}：{fallback['because']}")
        if "chain" in out:
            chain = out["chain"]
            print(f"  CHAIN {' -> '.join(chain['chain'])} :: {chain['terminus']}")
            print(f"  {chain['reason']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
