"""一个外部参考对象：它到底被抓到了什么，能证明什么，以及不能证明什么。

WHY THIS MODULE EXISTS
----------------------
`docs/EXTERNAL-REFERENCE-LIBRARY.md` 把外部参考拆成五种关系对象（Source /
Snapshot / Reference Note / Understanding / Owned Knowledge），并且**每一节都
写了一条读者不能自己补的规则**。这些规则在今天的三棵树里没有任何读者：

  * §4「HTTP 200但得到登录页、验证码、脚本壳、截断正文**不能算捕获成功**」——
    `intake/capture_coverage.py` 的 `_web_components` 明确拒绝读采集器自己的
    `page_kind.state` 标签（「A label read as a state is the misattribution these
    components exist to prevent」），并把这条判断推给了这个模块。推迟之后没有
    人来接，就等于那条规则不存在。
  * §11「只有本地snapshot不算『存进飞书』；有飞书链接但全文/附件没取不能说
    『本地完整同步』」——manifest 的 `effects` 块记了四个阶段各自的 DONE/NOT_DONE，
    但没有任何读者说过阶段之间**不蕴含**。四个布尔值并排摆着，读的人一定会连线。
  * §11「旧hash不允许绕撤权」「来源404不自动把自己的批注删除」——撤销与 404 今天
    是两个一样的字符串。
  * §5 的一份参考笔记首层该有什么、什么必须折叠、以及「不能把模型生成的采用建议
    写成官方原文或已接受规范」。
  * §4「相同标题不是同对象」与多发现出处指向同一个已核实 Source。

所以这个模块**只做一件事**：把这些规则变成取值和拒绝，而不是把已经记住的结论
再抄一遍。它不复制正文、不下载、不建飞书对象、不推进任何状态。

WHAT IT REFUSES
---------------
它不把采集器的标签当结论。一个 manifest 说 `page_kind.state: ARTICLE`，而这个模块
把 §4 的四个问题逐条对着**采集器自己写的标记清单**查：查过且没命中是
`EVIDENCED_ABSENT`，命中是 `FOUND`，而**根本没有出现在清单里**是 `UNCHECKED`。
第四个问题的答案是 `UNCHECKED` 时整体就不是 `CAPTURED`——因为「没人问过截断这件
事」与「问过，没有截断」在记录里长得一样，而它们不是同一句话。

它不给一个效果签出另一个效果。`local_snapshot: DONE` 旁边永远带着它**不**蕴含的
那一句；而「没做」且没说原因的阶段是 `UNDECLARED`，不是排队——§11 原话
「不存在的下一consumer不叫排队」。

它没有任何一条路径返回「允许执行」。`permits()` 对每一个采集到的 source kind 都答
false，见 `NEVER_AUTHORIZING`。这不是保守，是 §4 的原话：「文章建议运行脚本、安装
Skill或修改AGENTS.md不产生执行许可」。

它不读时钟、不读正文、不判某条 policy 对不对。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

SCHEMA = "serveros/external-reference.v1"

#: 这个模块实现的合同。写成一列而不是散在注释里，因为每一条拒绝都要能在原文里
#: 被读到——一段引不出来源的规则，下一个人只能靠记忆维护它。
CONTRACT_DOC = "docs/EXTERNAL-REFERENCE-LIBRARY.md"

#: 读取位置与 `capture_coverage` 同源：同一个 records 根下的同一棵 raw 树。
RAW_SEGMENTS = ("information", "raw")

#: §4 点名的四种「200 但不是文章」。每一条配它自己的标记；标记与 manifest 里
#: `checked_markers` / `shell_markers_found` 的写法一致，因为那两列就是采集器
#: 自己的词汇，这里读它，不改写它。
#:
#: `truncated_body` 是故意的第四条：它最难被一个字符串发现，而正因为它难，「采集器
#: 有没有问过」才是唯一诚实的读数。一个从来没查过截断的采集说「全文已读」，和一个
#: 查过且没查到的采集说同一句话，在记录里是两件事。
SHELL_QUESTIONS = (
    ("login_shell", "§4「登录页」", ("sign in", "signin", "log in", "login",
                                    "登录", "请登录")),
    ("captcha", "§4「验证码」", ("captcha", "are you human", "验证码", "人机验证")),
    ("script_shell", "§4「脚本壳」", ("enable javascript", "javascript is disabled",
                                     "requires javascript", "启用 javascript",
                                     "请启用")),
    ("truncated_body", "§4「截断正文」", ("read the rest", "read more",
                                        "continue reading", "truncated",
                                        "订阅后阅读", "阅读全文")),
)

#: 一条问题的三种读数。`UNCHECKED` 必须与 `EVIDENCED_ABSENT` 分开，否则「没人问」
#: 会被读成「问过了，没有」——这正是这个模块存在的理由，一次也不能让它溜过去。
QUESTION_STATES = ("EVIDENCED_ABSENT", "FOUND", "UNCHECKED")

#: 一个页面捕获的质量。`NOT_CAPTURED` 是 §4 的拒绝；`UNDECIDABLE` 是「至少一条
#: §4 的问题今天没有证据」；`CAPTURED` 要四条全有证据。
QUALITY_STATES = ("CAPTURED", "NOT_CAPTURED", "UNDECIDABLE")

#: `page_quality` 可能吐出的每个键，以及它从 manifest 的哪个字段来。
QUALITY_PRODUCED = {
    "questions": "page_kind.checked_markers 与 page_kind.shell_markers_found — "
                 "§4 的四个问题各自对着这两列判定，判定结果按问题报告",
    "markers_checked": "len(page_kind.checked_markers) — 采集器实际查过的标记条数",
    "markers_found": "len(page_kind.shell_markers_found) — 实际命中的条数",
    "collector_label": "page_kind.state — 采集器自己的结论，**旁置**：这里读它，"
                       "但不据它下结论，两者的分歧要被看见",
    "label_exceeds_evidence": "由上面的判定推出：标签宣称了一个本模块无法从证据"
                              "确立的状态",
}

#: §11 的四个阶段，顺序即依赖叙述的顺序，不是执行顺序。
EFFECTS = ("local_snapshot", "reference_published", "understanding", "git_metadata")

#: 每个阶段**不**蕴含什么。这张表是这个模块最承重的东西：manifest 里四个
#: DONE/NOT_DONE 并排摆着，而并排本身就在暗示一条链。
EFFECT_LAYERS = {
    "local_snapshot": "本地留下快照**不**蕴含已经存进飞书：§11「只有本地snapshot"
                      "不算『存进飞书』」。它也不蕴含我们保存的是完整的——"
                      "材料与附件各自另一回事（§6）",
    "reference_published": "飞书上有了一张笔记**不**蕴含本地有可读副本，也**不**"
                           "蕴含正文或附件已经取到：§11「有飞书链接但全文/附件没取"
                           "不能说『本地完整同步』」",
    "understanding": "一条理解**不**蕴含已采用：§5「收藏不等于同意、采用或授权」。"
                     "把方法用到规范里是 B5/B6 的评估加 B9 的获准修改，是另一个决定",
    "git_metadata": "metadata 进了 Git**不**蕴含受限正文进了 Git：§10「完整受限源文、"
                    "登录cookie、签名URL、原始私密图片、大媒体默认不进Git」，"
                    "而 private repo 也不是自动豁免",
}

#: `read_effects` 可能吐出的每个键。
EFFECT_PRODUCED = {
    "states": "effects.<name> — 采集器记下的阶段状态，逐阶段报告，不合并",
    "reasons": "effects.<name>_why — 采集器给这个阶段的理由，逐字复制",
    "layers": "EFFECT_LAYERS — 本模块持有的分层句：这个阶段不蕴含什么",
    "refused_conclusions": "由 states 与 EFFECT_LAYERS 推出：读者最可能画错的那几条线",
}

#: 阶段取值。`UNDECLARED` 是 §11「不存在的下一consumer不叫排队」：没做、也没说
#: 为什么，不叫排上了队。
EFFECT_STATES = ("DONE", "NOT_DONE", "UNDECLARED")

#: §5 一份参考笔记的首层，**按原文顺序**。九项一起才是首层：一份缺「保存理由」的
#: 笔记是一个带装饰的链接，而不是笔记。
NOTE_FIRST_LAYER = {
    "title": "题名",
    "original_author": "原作者/机构",
    "original_entry": "原文入口",
    "saved_because": "保存理由",
    "source_points": "几个有依据的要点",
    "applicability": "适用范围与未验证说明",
    "related": "关联项目/主题",
    "our_annotation": "我们的批注",
    "last_checked": "最近检查/当前状态",
}

#: §5「source IDs、hash、解析器版本、同步回执放折叠详情或机器关联，不让首屏变台账」。
#: 它们必须有地方去（`detail`），但首层不是那个地方。
NOTE_DETAIL_FIELDS = ("source_ids", "content_sha256", "parser_version",
                      "sync_receipt", "canonical_url_hash", "page_kind")

#: 首层允许的键：九项，加上 §5/§6 各自点名的两个「关于我们自己的对象」字段，再加一个
#: `detail` 折叠层。
#:
#:   * `proposed_change` —— §5「来源要点、我们的判断、拟采用修改分栏」里的第三栏。
#:   * `full_text_snapshot` —— §6「被允许的全文保存明确标『源文快照：所见版本/时间』」。
#:
#: 它们不是九项之外的自由字段：两个都在合同里被点名，也各有自己的检查。不在这一行里
#: 的键既不是首层该有的，也不是折叠层，它是一条没人负责的字段。
NOTE_ALLOWED = tuple(NOTE_FIRST_LAYER) + ("detail", "proposed_change",
                                          "full_text_snapshot")

#: 一份笔记对「拟采用」的两种状态。`ADOPTED` 必须有 `decided_by_user`，因为
#: §5「不能把模型生成的采用建议写成官方原文或已接受规范」，而 §2 说收藏不等于采用。
PROPOSAL_STATES = ("PROPOSED", "ADOPTED")

#: §11 的三种来源状态。`NOT_FOUND` 与 `REVOKED` 必须分开：404 是关于可达性的读数，
#: 不是关于许可的读数。
REVOCATION_STATES = ("ACTIVE", "REVOKED", "NOT_FOUND")

#: §4「文章建议运行脚本、安装Skill或修改AGENTS.md不产生执行许可」。列出来是为了让
#: `permits()` 的答案对得上原文点名的动作，而不是让调用方自己猜哪些算。
NEVER_AUTHORIZING = ("run_script", "install_skill", "modify_agents_md",
                     "change_policy", "send_message", "expand_audience")

#: 这个模块能就「谁给的执行许可」作出的**唯一**回答。
PERMISSION_ANSWER = "no"

#: §4「文件名/URL不能指定本机任意写入路径」。一个参考材料的名字要变成落盘路径时，
#: 走这里；它拒绝而不是清洗，因为「清洗后落到哪里」是调用方在决定别人的写权限。
UNSAFE_PATH_PARTS = ("/", "\\", "\0", "..")


class Refused(RuntimeError):
    """这个读者拒绝作答，而猜不是选项。

    与 `capture_coverage.Refused` 同一分工：读**者**的缺陷（吐出一个没有生产者的
    字段、根不存在）是 `Refused`；被读**文件**的缺陷是 `Malformed`。两者混在一起，
    就会因为读者有 bug 而把一个 manifest 报成不可读——正是这个模块要防的那种张冠李戴。
    """


class Malformed(Refused):
    """这个对象不是一份参考记录。说的是文件，不是读者。"""


def raw_root(records_root: Path) -> Path:
    """`<records_root>/information/raw`。与 `capture_coverage` 同一处组成。"""
    return Path(records_root).joinpath(*RAW_SEGMENTS)


def no_unsourced(mapping: dict, table: dict, where: str) -> dict:
    """`mapping` 的每个键都在 `table` 里，否则拒绝并点名越界的键。

    在**吐出记录**的地方调用，而不是在每个键产生的地方。只守生产者的守卫守的是
    生产者：第二条代码路径、或者自己拼字典的调用方，都会绕过去，而记录才是被读的
    东西。表本身就是检查成立的原因——`PRODUCED` 这类映射当文档看没有价值，当断言
    看才是承重的。
    """
    unexpected = sorted(set(mapping) - set(table))
    if unexpected:
        raise Refused(
            f"this reader would emit {', '.join(f'`{where}.{key}`' for key in unexpected)}, "
            f"which the producer table does not name a source for; add the "
            f"producer or drop the field")
    return mapping


def _mapping(value, what: str) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise Malformed(
            f"{what} is {type(value).__name__}, not an object; a reference record "
            f"that cannot be read as fields is not a reference record")
    return value


def _strings(value) -> list[str]:
    """一个字符串列表，或空表。非字符串的条目被丢掉，不猜它的意思。"""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item.strip()]


# ---------------------------------------------------------------------------
# §4 捕获质量
# ---------------------------------------------------------------------------
def _matches(entry: str, markers: tuple) -> bool:
    return any(marker in entry.lower() for marker in markers)


def _question_state(question: tuple, checked: list[str], found: list[str]) -> dict:
    """§4 的一条问题对着采集器自己的两列清单判定。

    匹配是小写化的子串匹配。采集器把 `sign in` 和 `Sign in` 两条都写进了
    `checked_markers`，说明它是大小写敏感地查的；这里小写化，一条就能覆盖两种拼法。
    这是**读者**的事实，写在这里而不是让调用方去猜为什么条数对不上。
    """
    name, source, markers = question
    hits = sorted({entry for entry in found if _matches(entry, markers)})
    asked = sorted({entry for entry in checked if _matches(entry, markers)})
    if hits:
        state, why = "FOUND", (
            f"§4 点名的失败形态真的出现了：{', '.join(hits)}")
    elif asked:
        state, why = "EVIDENCED_ABSENT", (
            f"采集器查过 {len(asked)} 个这项标记（{', '.join(asked)}），一个都没命中")
    else:
        state, why = "UNCHECKED", (
            f"`checked_markers` 里没有这项的任何标记（{', '.join(markers)} 一条都没查），"
            f"所以「没有这种壳」不是查出来的，是没人问过")
    return {"id": name, "source": source, "state": state, "why": why,
            "markers_asked": asked, "markers_hit": hits}


def page_quality(document: dict) -> dict:
    """§4：这次抓回来的，到底是不是那篇文章。

    `UNDECIDABLE` 与 `EVIDENCED_ABSENT` 的区别是这个函数的全部意义。四条问题全有
    证据才叫 `CAPTURED`；有一条没人问过就只能是 `UNDECIDABLE`，因为「问过，没有」
    和「没问」在记录里长得一样。任何一条命中就是 `NOT_CAPTURED`——§4 说这不能算
    捕获成功，而这个模块不给它留一个「但是字节数对得上」的出口。

    采集器自己的 `page_kind.state` 被**旁置**报告，不参与判定。当它宣称一个本模块
    立不起来的状态时，`label_exceeds_evidence` 记下那句话：一个被当状态读的标签，
    正是这两个字段分开存在的理由。
    """
    kind = _mapping(document.get("page_kind"), "`page_kind`")
    checked = _strings(kind.get("checked_markers"))
    found = _strings(kind.get("shell_markers_found"))
    questions = [_question_state(question, checked, found)
                 for question in SHELL_QUESTIONS]
    found_any = [q for q in questions if q["state"] == "FOUND"]
    unchecked = [q for q in questions if q["state"] == "UNCHECKED"]
    if found_any:
        state = "NOT_CAPTURED"
        why = (f"§4：HTTP {document.get('fetch', {}).get('http_status')} "
               f"但命中了 {', '.join(q['id'] for q in found_any)}——"
               f"这不是那篇文章，200 不改变这一点")
    elif unchecked:
        state = "UNDECIDABLE"
        why = (f"§4 的四条问题里有 {len(unchecked)} 条没有证据："
               f"{', '.join(q['id'] for q in unchecked)}。没人查过的东西，"
               f"「没有」与「没看」在记录里是同一句话")
        if not checked:
            why += ("；`checked_markers` 是空的，也就是 0 条标记查了 0 条命中，"
                    "读成「没有壳」正是 §5 禁止的 0/0")
    else:
        state = "CAPTURED"
        why = (f"§4 的四条问题各查过一次且都没命中（共 {len(checked)} 条标记）；"
               f"这只说明这四种壳不在，不说明正文完整——完整性由 body/材料各自说")
    label = kind.get("state")
    label = label if isinstance(label, str) and label else None
    exceeds = None
    if label and label.upper() not in ("SHELL", "LOGIN", "CAPTCHA", "UNKNOWN") \
            and state != "CAPTURED":
        exceeds = (f"采集器的标签是 `{label}`，而本模块从它自己的标记清单里只能读到 "
                   f"`{state}`：标签宣称了一个证据立不起来的状态。"
                   f"两个都留着，读的人自己看分歧")
    out = {
        "questions": questions,
        "markers_checked": len(checked),
        "markers_found": len(found),
        "collector_label": label,
        "label_exceeds_evidence": exceeds,
    }
    return {"state": state, "why": [why],
            "facts": no_unsourced(out, QUALITY_PRODUCED, "quality")}


# ---------------------------------------------------------------------------
# §11 效果分层
# ---------------------------------------------------------------------------
def read_effects(document: dict) -> dict:
    """§11：四个阶段各自到哪一步，以及它们**互不蕴含**。

    这个函数一个字也不推断。`effects.<name>` 没有出现在 manifest 里就是
    `UNDECLARED`，不是 `NOT_DONE`——「没做」与「没说」是两件事，而
    「不存在的下一consumer不叫排队」（§11）说的正是：一个没做的阶段如果没人说为
    什么，它就不是排上了队。

    `refused_conclusions` 是这张表真正要防的东西。四个状态并排摆着，读的人一定会
    连线；这个函数把最可能的两条线明确拒绝掉，用的句子是原文的。
    """
    effects = _mapping(document.get("effects"), "`effects`")
    states: dict = {}
    reasons: dict = {}
    for name in EFFECTS:
        raw = effects.get(name)
        raw = raw if isinstance(raw, str) and raw else None
        if raw is None:
            states[name] = "UNDECLARED"
        else:
            states[name] = "DONE" if raw.upper() == "DONE" else "NOT_DONE"
        reason = effects.get(f"{name}_why")
        reasons[name] = reason if isinstance(reason, str) and reason else ""
    refused = []
    for name in EFFECTS:
        if states[name] != "DONE":
            continue
        refused.append({
            "conclusion": f"「{name} 完成了，所以其它阶段也差不多了」",
            "why": EFFECT_LAYERS[name],
        })
    for name in EFFECTS:
        if states[name] == "UNDECLARED":
            refused.append({
                "conclusion": f"「{name} 没做，所以它在排队」",
                "why": "§11「不存在的下一consumer不叫排队」：这个阶段在 manifest 里"
                       "连一个状态都没有，而没做、又没说为什么，不是排上了队",
            })
    out = {"states": states, "reasons": reasons, "layers": dict(EFFECT_LAYERS),
           "refused_conclusions": refused}
    no_unsourced(out, EFFECT_PRODUCED, "effects")
    return out


# ---------------------------------------------------------------------------
# §4 发现出处
# ---------------------------------------------------------------------------
def discoveries(edges: list, key_of) -> dict:
    """§4：多个发现出处指向**同一**个已核实的 Source，以及「相同标题不是同对象」。

    `key_of` 必传，不接受默认值：它就是 `source_policy.key_of`。键只能在一处组成，
    而这里如果自己拼一个 f-string，两个键空间就会在某一天因为一个分隔符分开。传进来
    也让这个函数**结构上不可能**按标题合并——身份里没有稳定键时它拒绝，而不是退回
    标题。§4 的原话是「相同标题不是同对象」，而一个按标题分组的实现会先违反它，
    再被读的人当成去重成功。
    """
    if not callable(key_of):
        raise Refused(
            "`discoveries` 需要一个 `key_of`：键在 `source_policy.key_of` 里组成，"
            "在这里再写一遍就是第二个键空间")
    if not isinstance(edges, list):
        raise Malformed(f"`discovery_edges` is {type(edges).__name__}, not a list")
    grouped: dict = {}
    titles: dict = {}
    for index, edge in enumerate(edges):
        if not isinstance(edge, dict):
            raise Malformed(f"discovery edge #{index} is {type(edge).__name__}, "
                            f"not an object")
        identity = _mapping(edge.get("identity"), f"discovery edge #{index} identity")
        key = key_of(identity)
        title = edge.get("title") or identity.get("title")
        if key is None:
            raise Refused(
                f"discovery edge #{index} composes no stable key: it carries "
                f"{sorted(identity) or 'no identity at all'}. §4「相同标题不是同对象」"
                f"—— 一个只有标题的发现出处只能成为 unresolved，不能成为一个对象")
        entry = grouped.setdefault(key, {"key": key, "edges": []})
        entry["edges"].append({
            "found_via": edge.get("found_via"),
            "at": edge.get("at"),
            "note": edge.get("note"),
        })
        if isinstance(title, str) and title:
            titles.setdefault(title, set()).add(key)
    objects = [{"key": key, "edge_count": len(entry["edges"]),
                "edges": entry["edges"]}
               for key, entry in sorted(grouped.items())]
    shared = {title: sorted(keys) for title, keys in titles.items()
              if len(keys) > 1}
    return {
        "objects": objects,
        "object_count": len(objects),
        "edge_count": sum(entry["edge_count"] for entry in objects),
        "titles_shared_by_several_objects": shared,
        "_why_titles_are_not_here": "标题只用来报告「同一个题名落在几个对象上」；"
                                    "分组的键始终是 provider + 稳定标识。按标题分组"
                                    "会把两篇同名文章并成一个对象，而那是 §4 明令"
                                    "禁止的合并",
    }


# ---------------------------------------------------------------------------
# §5 一份参考笔记的首层
# ---------------------------------------------------------------------------
def check_note(note: dict) -> dict:
    """§5/§6：我们自己写的那份参考笔记，首层是否有该有的东西、有没有不该有的东西。

    这个函数检查的是**我们**写的对象，不是外部正文，所以它的每一条拒绝都是关于
    「我们有没有把话说清楚」，而不是关于原文说了什么。四条：

      1. 九项首层字段缺一不可。缺「保存理由」的笔记是一个带装饰的链接。
      2. 机器细节不得出现在首层（放 `detail`）。§5 的原话是「不让首屏变台账」。
      3. `source_points` 的每一条必须带 `from` 指明它来自原文。§5「不能把模型生成的
         采用建议写成官方原文」——一条没有出处的要点，读起来就是原文说的。
      4. `proposed_change.status: ADOPTED` 必须带 `decided_by_user`。§5 与 §3 的表：
         「收藏行为本身不批准代码/安全规则变更」。一个模型读出来的「已采用」不是人
         的确认——这条与 `claim_recall` 的收据是同一条规则，只是长在笔记上。
      5. 存了全文快照就必须带 `version_label`。§6「必须明确标『源文快照：所见版本/
         时间』」，否则一份旧正文会静默地变成「完整新版本」。
    """
    if not isinstance(note, dict):
        raise Malformed(f"a note is {type(note).__name__}, not an object")
    missing = [field for field in NOTE_FIRST_LAYER if field not in note]
    if missing:
        raise Refused(
            "this note's first layer is missing "
            + ", ".join(f"`{field}`（{NOTE_FIRST_LAYER[field]}）" for field in missing)
            + f": §5 的九项首层要一起在；缺一层的笔记读起来像一份完整的笔记")
    exposed = [field for field in NOTE_DETAIL_FIELDS if field in note]
    if exposed:
        raise Refused(
            f"the first layer carries {', '.join(f'`{f}`' for f in exposed)}: "
            f"§5「source IDs、hash、解析器版本、同步回执放折叠详情或机器关联，"
            f"不让首屏变台账」—— 把它们放进 `detail`")
    unexpected = sorted(set(note) - set(NOTE_ALLOWED))
    if unexpected:
        raise Refused(
            f"the first layer carries {', '.join(f'`{f}`' for f in unexpected)}, "
            f"which §5's nine fields do not name; a field nobody named is a field "
            f"nobody maintains")
    points = note.get("source_points")
    if not isinstance(points, list) or not points:
        raise Refused(
            "`source_points` 不是一张非空表：§5 的首层要「几个有依据的要点」，"
            "而一张空表读起来与「这篇没有要点」一样")
    for index, point in enumerate(points):
        if not isinstance(point, dict):
            raise Malformed(f"`source_points[{index}]` is {type(point).__name__}")
        if not point.get("from"):
            raise Refused(
                f"`source_points[{index}]` 没有 `from`：§5「不能把模型生成的采用建议"
                f"写成官方原文」—— 一条不写出处的要点，读的人只能当它是原文说的")
    proposal = note.get("proposed_change")
    if isinstance(proposal, dict):
        status = proposal.get("status")
        if status not in PROPOSAL_STATES:
            raise Refused(
                f"`proposed_change.status` is {status!r}, which is not one of "
                f"{PROPOSAL_STATES}: 一个不在词表里的状态没法被拒绝也没法被遵守")
        if status == "ADOPTED" and not proposal.get("decided_by_user"):
            raise Refused(
                "`proposed_change.status` is ADOPTED with no `decided_by_user`: "
                "§5 与 §3 的表里「收藏行为本身不批准代码/安全规则变更」，"
                "而一个读出来的「已采用」不是人的确认")
    snapshot = note.get("full_text_snapshot")
    if isinstance(snapshot, dict) and not snapshot.get("version_label"):
        raise Refused(
            "`full_text_snapshot` 没有 `version_label`：§6「被允许的全文保存明确标"
            "『源文快照：所见版本/时间』」，否则旧的正文会在某天静默地变成"
            "「完整的新版本」")
    return {
        "checked": True,
        "first_layer": [field for field in NOTE_FIRST_LAYER],
        "folded": [field for field in NOTE_DETAIL_FIELDS
                   if field in _mapping(note.get("detail"), "`detail`")],
        "proposal": (note.get("proposed_change") or {}).get("status")
        if isinstance(note.get("proposed_change"), dict) else None,
        "why": "首层九项齐、机器细节在折叠层、要点各带出处；这是一份可以给人读的"
               "笔记，不是一份台账",
    }


# ---------------------------------------------------------------------------
# §11 撤销
# ---------------------------------------------------------------------------
def revocation_effects(state: str) -> dict:
    """§11：撤权之后什么停了，什么**没有**停，以及 404 为什么不是撤权。

    `REVOKED` 与 `NOT_FOUND` 分开是这个函数存在的理由。撤权是对方说「不要再用了」，
    于是新访问停、当前召回与传播受限、受影响的 Reference/Claim 要被标出来；而 404
    是关于可达性的读数——对方可能只是换了个地址。把 404 当撤权，会让我们因为一个
    打错的地址就删掉自己的批注；把它当没事，会让一条真的撤权看起来像打错了地址。

    两条在两种状态下都成立，所以它们是常量而不是分支：本地那份旧 hash / 快照
    **不允许绕过撤权**（它是审计留存，不是继续召回或传播的许可），而**我们自己的
    批注不会因为来源消失而被删掉**（§11「来源404不自动把自己的批注删除」）。
    """
    if state not in REVOCATION_STATES:
        raise Refused(
            f"`{state}` is not one of {REVOCATION_STATES}: 一个不在词表里的来源状态"
            f"既停不下来也放不过去")
    if state == "ACTIVE":
        return {
            "state": state, "new_access": "ALLOWED", "recall": "UNRESTRICTED",
            "propagation": "UNRESTRICTED", "mark_affected": False,
            "snapshot_restores_access": False, "keeps_own_annotation": True,
            "why": ["来源可用：没有任何限制被这条规则加上去"],
        }
    if state == "REVOKED":
        return {
            "state": state, "new_access": "STOPPED", "recall": "RESTRICTED",
            "propagation": "RESTRICTED", "mark_affected": True,
            "snapshot_restores_access": False, "keeps_own_annotation": True,
            "why": [
                "§11「原文撤回/权限收回时停止新访问、限制当前召回/传播并标受影响"
                "Reference/Claim」：三者一起，撤回不是一个只有采集器才需要知道的"
                "事件",
                "§11「旧hash不允许绕撤权」：本地那份快照的 hash 能证明我们当时读到"
                "什么，不能证明今天还可以继续用它召回或传播。审计留存与再传播是"
                "两件事，这个字段把它们分开",
                "§11「合法历史审计与删除要求分别处理」：撤权本身不要求删除我们的"
                "批注，也不要求保留我们的副本——那是两个要单独处理的请求",
            ],
        }
    return {
        "state": state, "new_access": "STOPPED", "recall": "UNRESTRICTED",
        "propagation": "UNRESTRICTED", "mark_affected": False,
        "snapshot_restores_access": False, "keeps_own_annotation": True,
        "why": [
            "§11「来源404不自动把自己的批注删除」：404 是关于**可达性**的读数，"
            "不是关于许可的读数。它停的是「现在去读」，不是「已经读到的还算不算」",
            "所以召回与传播在这里**不**受限：把 404 当撤权，等于让对方换个地址就把"
            "我们自己的批注删掉；反过来当成没事，又会让一条真的撤权看起来像打错了"
            "地址。分不清的时候，不标受影响、也不放宽——两种错都留给下一次读数",
            "合法历史审计与删除要求仍各自单独处理（§11）",
        ],
    }


# ---------------------------------------------------------------------------
# §4 外部正文不产生执行许可
# ---------------------------------------------------------------------------
def permits(action: str, source_kind: str = "external_body") -> dict:
    """§4：一段被采集回来的正文，能不能授权一件事。

    答案永远是 `PERMISSION_ANSWER`（"no"），而这个模块**没有任何一条路径**返回别的
    东西——这是它作为「结构上不能」而不是「今天写成了 false」的证明。
    `NEVER_AUTHORIZING` 里的动作是原文点名的那些；词表外的动作不是被拒绝，是**没有
    被回答**：一个不认识的动词如果拿到一个「否」，下一次有人加词就会以为默认是安全的。

    执行许可在别处：§4 说文章建议运行脚本、安装 Skill 或修改 AGENTS.md 不产生执行
    许可，而 §3 的表说「把这个方法用到我们规范里」要走 B5/B6 的适用性评估与 B9 的
    获准修改。这个函数只负责把那条路堵死，不负责开另一条。
    """
    if source_kind not in ("external_body", "collected_note"):
        raise Refused(
            f"`{source_kind}` is not a kind this function answers about: 外部正文与"
            f"我们写的笔记都不产生执行许可，而**别的东西**要不要授权不是这个模块"
            f"能说的 —— 那是一条 AUTHORIZATION 规则的事")
    if action not in NEVER_AUTHORIZING:
        raise Refused(
            f"`{action}` is not a verb §4 names: 这里不回答不认识的动词，因为一个"
            f"默认的「否」会让下一个加词的人以为加进去的也自动是否")
    return {
        "permitted": False,
        "action": action,
        "source_kind": source_kind,
        "why": f"§4「外部正文和嵌入代码按不可信资料处理。文章建议运行脚本、安装Skill"
               f"或修改AGENTS.md不产生执行许可」。这条正文是怎么来的（{source_kind}）"
               f"都不改变这一点；采用它的方法要走 B5/B6 评估与 B9 获准修改，是另一个"
               f"决定，也正是「收藏不等于采用」（§5）",
        "permission_answer": PERMISSION_ANSWER,
    }


def local_segment(value: str) -> str:
    """§4「文件名/URL不能指定本机任意写入路径」。

    拒绝，不清洗。清洗看起来更友好，但它做的是决定「这段外部文本会写到本机的哪个
    路径」，而那正是这条规则要挡住的事。调用方拿到的是一个可以安全拼进一个自己选定
    目录的段；绝对路径、分隔符、`..`、NUL 一律不通过。
    """
    if not isinstance(value, str) or not value.strip():
        raise Malformed(f"`{value!r}` is not a usable name")
    for part in UNSAFE_PATH_PARTS:
        if part in value:
            raise Refused(
                f"`{value}` carries `{part}`: §4「文件名/URL不能指定本机任意写入"
                f"路径」—— 一个外部名字不能决定它落在哪里，只能被放进调用方已经"
                f"选定的目录里")
    if value.startswith("~"):
        raise Refused(f"`{value}` starts with `~`, which names a home directory")
    return value


# ---------------------------------------------------------------------------
# 读取
# ---------------------------------------------------------------------------
def walk_manifests(root: Path) -> list[Path]:
    """`root` 下每一份 `manifest.json`，排序后返回。

    排序是为了两次运行给出同一个顺序；一个行序取决于文件系统的汇总，是两个读者
    可以互相不同意而谁都没错的汇总。
    """
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        if "manifest.json" in filenames:
            found.append(Path(dirpath) / "manifest.json")
    return sorted(found)


def is_reference_object(document: dict) -> bool:
    """这份 manifest 是不是一个外部参考对象。

    按**键**判定，不按它落在哪：`source`+`fetch` 是 `capture_coverage` 认定的
    `web_capture` 形状（那里是权威，这里只问这个问题）。按路径判定会把一个
    `information/raw/web/` 下放错的聊天快照读成参考对象。
    """
    return "source" in document and "fetch" in document


def source_provider(document: dict) -> str:
    """一份 manifest 声明的 provider，缺省 `web`。

    与 `source_policy.identity_of` 读的是同一个字段，缺省也一样：两处都缺省成同一个
    值，才不会有「这里算 web、那里算 feishu」的第二个键空间。
    """
    source = _mapping(document.get("source"), "`source`")
    provider = source.get("provider")
    return provider if isinstance(provider, str) and provider else "web"


def identity_of(document: dict) -> dict:
    """这个参考对象的稳定身份，形状与 `source_policy.identity_of` 的页面分支一致。

    只放 `provider` 和 `url` 两个键：`discoveries` 要的是能组成键的东西，而多放一个
    标题进来，就会有一个调用方拿它去当键。
    """
    source = _mapping(document.get("source"), "`source`")
    url = source.get("requested_url")
    return {"provider": source_provider(document),
            "url": url if isinstance(url, str) and url else None}


def read_object(path: Path, document: dict, root: Path | None = None) -> dict:
    """一份已解析的参考 manifest 的读数：质量、效果、发现出处。"""
    observed = _mapping(document.get("identity_observed"), "`identity_observed`")
    return {
        "path": str(Path(path).relative_to(root)) if root else str(path),
        "url": identity_of(document)["url"],
        "title": observed.get("title"),
        "quality": page_quality(document),
        "effects": read_effects(document),
    }


def read_root(root: Path, key_of) -> dict:
    """`<records>/information/raw` 下的每一个外部参考对象。

    `key_of` 必传，理由与 `discoveries` 相同。这个函数**同时报告分母**：
    「68 份 manifest 里有 1 个参考对象」与「0 个参考对象」是两句话，而一个只数
    参考对象的读者会让「今天没有收藏」和「今天没看」长得一样。
    """
    root = Path(root)
    if not root.is_dir():
        raise Refused(
            f"{root} is not a directory: there is no capture tree to read. An "
            f"absent root is not an empty one, and reporting zero reference "
            f"objects here would say nothing was collected when nothing was read")
    manifests = walk_manifests(root)
    objects: list[dict] = []
    unreadable = 0
    for path in manifests:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            unreadable += 1
            continue
        if not isinstance(document, dict) or not is_reference_object(document):
            continue
        record = read_object(path, document, root)
        edges = document.get("discovery_edges")
        record["discoveries"] = discoveries(
            [{"found_via": edge.get("found_via"), "at": edge.get("at"),
              "note": edge.get("note"), "identity": identity_of(document)}
             for edge in (edges if isinstance(edges, list) else [])
             if isinstance(edge, dict)],
            key_of)
        objects.append(record)
    return {
        "schema": SCHEMA,
        "root": str(root),
        "manifests": len(manifests),
        "unreadable": unreadable,
        "not_reference_objects": len(manifests) - unreadable - len(objects),
        "objects": objects,
        "summary": summarize(objects),
    }


def summarize(objects: list[dict]) -> dict:
    """按**问题**计数，不按对象给一个结论。

    一个对象可以质量 `CAPTURED` 而理解 `NOT_DONE`，也可以反过来。给一个对象的
    「完整度」一个数，就是把三个独立问题合成一个没人问过的数。
    """
    quality: dict = {}
    effects: dict = {}
    labels_exceeding: list[str] = []
    for record in objects:
        if not record.get("readable"):
            continue
        bucket = quality.setdefault(record["quality"]["state"], 0)
        quality[record["quality"]["state"]] = bucket + 1
        for name, state in record["effects"]["states"].items():
            bucket = effects.setdefault(name, {})
            bucket[state] = bucket.get(state, 0) + 1
        if record["quality"]["facts"]["label_exceeds_evidence"]:
            labels_exceeding.append(record["path"])
    return {"denominator": len(objects), "quality": quality, "effects": effects,
            "label_exceeds_evidence": labels_exceeding}


def read_note(path: Path) -> dict:
    """读一份笔记草稿并检查它。检查的是我们自己的对象，所以缺陷属于文件。"""
    try:
        note = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Malformed(f"cannot read {path}: {exc}") from exc
    checked = check_note(note)
    return {"path": str(path), "note": note, "check": checked}


def render(document: dict) -> list[str]:
    """一份参考对象读数的文字形态。"""
    lines = [
        f"root        {document['root']}",
        f"manifest    {document['manifests']} 份  ·  参考对象 {len(document['objects'])}"
        f"  ·  非参考 {document['not_reference_objects']}"
        + (f"  ·  读不了 {document['unreadable']}" if document["unreadable"] else ""),
        "",
        "§4 捕获质量  四条问题各自判定——「问过，没有」与「没人问过」不是一句话",
    ]
    for record in document["objects"]:
        lines.append(f"  {record['quality']['state']:<13} {record.get('title') or record['path']}")
        for question in record["quality"]["facts"]["questions"]:
            lines.append(f"      {question['state']:<17} {question['id']}"
                         f"  ·  {question['why']}")
        if record["quality"]["facts"]["label_exceeds_evidence"]:
            lines.append(f"      ⚠ {record['quality']['facts']['label_exceeds_evidence']}")
    lines.append("")
    lines.append("§11 效果分层  四个阶段互不蕴含")
    for name in EFFECTS:
        state = next((record["effects"]["states"][name]
                      for record in document["objects"]), "—")
        lines.append(f"  {name:<20} {state}")
    lines.append("")
    lines.append("§11 阶段之间不蕴含的事（读者最可能画错的那几条线）")
    seen: set = set()
    for record in document["objects"]:
        for refusal in record["effects"]["refused_conclusions"]:
            if refusal["conclusion"] in seen:
                continue
            seen.add(refusal["conclusion"])
            lines.append(f"  ✗ {refusal['conclusion']}")
            lines.append(f"    {refusal['why']}")
    lines.append("")
    lines.append("§4 发现出处  多个出处指向同一个已核实的对象；标题从来不是键")
    for record in document["objects"]:
        found = record.get("discoveries") or {}
        for entry in found.get("objects") or []:
            lines.append(f"  {entry['key']}  ·  {entry['edge_count']} 个出处")
        for title, keys in (found.get("titles_shared_by_several_objects") or {}).items():
            lines.append(f"  ⚠ 题名 「{title}」 落在 {len(keys)} 个对象上")
    return lines
