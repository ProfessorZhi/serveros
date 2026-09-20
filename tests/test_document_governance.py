#!/usr/bin/env python3
"""SYS-10 文档治理 —— 一份 canonical 文档的稳定身份，以及它是不是派生物。

这里的用例是被"这一族最容易被写坏的地方"塑形的，不是被覆盖率目标塑形的。
五条线各有一组用例，其中最要紧的是第二条：

  1. canonical 文档能被引用，而引用带着它的权威来源（谁担保的 + 权威版本在哪）；
  2. **派生物不会被误当成 canonical** —— 这是最要紧的一条，所以它有四个用例：
     一跳的判定、多跳的链、副本自称权威、权威自称抄自别处；
  3. "源答了没有"（缺口）与"源答了什么"（发现）走向不同结果，而不是同一个 UNKNOWN；
  4. 畸形请求被拒，而且消息里同时出现"给的是哪个"与"允许的是哪几个"；
  5. 闭词表：未声明的类别被拒，不落到任何兜底类别上。

另外两组读的不是行为而是结构：

  * 词表对照真实声明 —— kind 与 kind_scope 逐项对照 `document-skeletons.yaml`
    与 `workchain-contract.yaml`，surface 对照 `space/vocabulary.json`。一份词表
    自己跟自己一致没有意义，它必须跟 fabric 已经做出的声明一致。
  * 内核的 AST —— 判定函数不读文件、不读时钟；全模块只有一处读盘入口。

最后是一个变异用例：把内核里的一行判断改坏，本文件里那两条头条断言必须变红。
它证明这些用例不是"无论如何都会绿"的。

本文件里的标识符全是合成的：没有一份真实文档、真实项目或真人出现在这里。
"""
from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
DOCUMENTS = ROOT / "documents"
KERNEL_PATH = DOCUMENTS / "document_governance.py"
VOCABULARY_PATH = DOCUMENTS / "document-vocabulary.json"
SKELETONS_PATH = ROOT / "intake" / "document-skeletons.yaml"
WORKCHAIN_PATH = ROOT / "intake" / "workchain-contract.yaml"
SPACE_VOCABULARY_PATH = ROOT / "space" / "vocabulary.json"

_spec = importlib.util.spec_from_file_location("serveros_document_governance", KERNEL_PATH)
governance = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(governance)

VOCABULARY = governance.load_vocabulary(VOCABULARY_PATH)

# 合成的标识符。项目名刻意用一个不存在的名字，这样任何一处把测试数据带进
# 产出的写法都会立刻显形。
REF = "doc:demo/report/r1"
ANCESTOR = "doc:demo/report/r0"
KIND = "report"
ITEM = "item:DEMO-0001"
OWNER = "owner:demo"
GIT_AT = {"surface": "git", "locator": "0000000:items/DEMO-0001/report.md"}
RENDERED_AT = {"surface": "local", "locator": "/tmp/derived/report.txt"}


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #

def source(record=None, **over):
    answer = {"source_ref": "registry:demo-index", "answered": True,
              "declared": record is not None}
    if record is not None:
        answer["record"] = record
    else:
        answer["because"] = "查过 demo 的文档索引，没有这个 ref"
    answer.update(over)
    return answer


def canonical(**over):
    record = {"standing": "CANONICAL", "kind": KIND, "scope": "item",
              "owner": OWNER, "item": ITEM,
              "copy_at": GIT_AT, "authoritative_at": GIT_AT}
    record.update(over)
    return source(record)


def derivative(**over):
    record = {"standing": "DERIVATIVE", "kind": KIND, "scope": "item",
              "owner": OWNER, "item": ITEM,
              "copy_at": RENDERED_AT, "authoritative_at": GIT_AT,
              "copied_from": ANCESTOR}
    record.update(over)
    return source(record)


def resolve(ref=REF, answer=None):
    return governance.resolve(ref, canonical() if answer is None else answer, VOCABULARY)


# --------------------------------------------------------------------------- #
# 红线 1：canonical 文档能被引用，且引用携带它的权威来源
# --------------------------------------------------------------------------- #

def test_a_canonical_document_answers_all_four_questions_at_once():
    """谁是它的、属于哪个项目/Item、权威版本在哪、是不是派生物 —— 四条一起答。

    四条分四次查找的写法会让它们互相矛盾：两次查找之间源可以变，而引用出去的
    那一行只会带上其中一条。
    """
    record = resolve()
    assert record["decision"] == "CANONICAL"
    assert record["is_the_authority"] is True
    assert record["identity"] == {"project": "demo", "kind": KIND, "name": "r1"}
    assert record["owner"] == OWNER
    assert record["item"] == ITEM
    assert record["standing"] == "CANONICAL"
    assert record["authority"]["at"] == GIT_AT


def test_the_reference_carries_the_source_that_vouches_for_it():
    """引用带着权威来源：source_ref 是担保这条结论的那个源，不是内核自己。"""
    record = resolve()
    assert record["authority"]["source_ref"] == "registry:demo-index"
    assert record["authority"]["answered"] is True
    assert record["authority"]["declared"] is True
    assert record["source_declared"] is True
    assert governance.is_authoritative_reference(record) is True


def test_the_citation_of_a_canonical_document_names_the_authority():
    line = governance.citation(resolve())
    assert REF in line
    assert f"git {GIT_AT['locator']}" in line
    assert "派生" not in line


def test_every_decision_carries_a_non_empty_reason_and_a_rendered_line():
    """每一条判断都要说出为什么。"为什么"为空时，读的人无法分辨"没有原因"
    与"原因没有被写下来"，而这两件事的下场完全不同。"""
    answers = {
        "CANONICAL": canonical(),
        "DERIVATIVE": derivative(),
        "NOT_DECLARED": source(None),
        "SOURCE_UNAVAILABLE": {"source_ref": "registry:demo-index", "answered": False,
                               "unanswered_because": "索引服务连不上"},
    }
    for decision, answer in answers.items():
        record = resolve(answer=answer)
        assert record["decision"] == decision
        assert record["reason"].strip()
        assert governance.citation(record).strip()


# --------------------------------------------------------------------------- #
# 红线 2：派生物不会被误当成 canonical（最要紧的一条）
# --------------------------------------------------------------------------- #

def test_a_derivative_is_never_the_authority():
    record = resolve(answer=derivative())
    assert record["decision"] == "DERIVATIVE"
    assert record["is_the_authority"] is False
    assert governance.is_authoritative_reference(record) is False
    assert record["copied_from"] == ANCESTOR
    # 权威版本是祖先的位置，不是这份副本自己的位置。
    assert record["authority"]["at"] == GIT_AT
    assert record["authority"]["at"] != record["copy_at"]


def test_the_derivative_citation_says_it_is_not_the_authority():
    """引用一行是会被复制进别的文档的东西。它必须自己说出"这不是权威版本"，
    否则读那一行的人没有别的办法知道。"""
    line = governance.citation(resolve(answer=derivative()))
    assert "派生，不是权威版本" in line
    assert ANCESTOR in line
    assert f"git {GIT_AT['locator']}" in line


def test_a_derivative_that_puts_its_authority_on_itself_is_rejected():
    """副本把 authoritative_at 指到自己的位置上 —— 这正是要拦下的那次误判。

    接受它会得到一份 standing=DERIVATIVE、却处处指向自身的记录：读的人会以为
    手里的是权威版本，而记录里没有任何一处说了相反的话。
    """
    with pytest.raises(governance.Rejected) as excinfo:
        resolve(answer=derivative(copy_at=GIT_AT, authoritative_at=GIT_AT))
    message = str(excinfo.value)
    assert RENDERED_AT["locator"] in message or GIT_AT["locator"] in message


def test_a_canonical_document_that_names_something_it_copied_is_rejected():
    """自称权威又自称抄自某处，是两个相反的声明。内核不替它们挑一个。"""
    with pytest.raises(governance.Rejected) as excinfo:
        resolve(answer=canonical(copied_from=ANCESTOR))
    assert ANCESTOR in str(excinfo.value)
    assert "CANONICAL" in str(excinfo.value)


def test_a_canonical_whose_authority_sits_elsewhere_is_told_to_declare_itself_derivative():
    """权威版本不在这个 ref 上，那这个 ref 就是副本 —— 记录应当改说 DERIVATIVE，
    而不是让内核把两个位置对齐。"""
    with pytest.raises(governance.Rejected) as excinfo:
        resolve(answer=canonical(authoritative_at=RENDERED_AT))
    message = str(excinfo.value)
    assert RENDERED_AT["locator"] in message and GIT_AT["locator"] in message


def test_a_derivative_that_names_nothing_it_copied_from_is_rejected():
    """派生物不指向它抄自的那份文档时，"权威版本在哪"这个问题就没有答案，
    而一个空字段会被读成"还没有填"。"""
    with pytest.raises(governance.Rejected) as excinfo:
        resolve(answer=derivative(copied_from=None))
    assert "copied_from" in str(excinfo.value)


def test_a_document_cannot_be_its_own_copy():
    with pytest.raises(governance.Rejected) as excinfo:
        resolve(answer=derivative(copied_from=REF))
    assert REF in str(excinfo.value)


def test_the_standing_to_decision_mapping_is_total_over_the_closed_set():
    """闭集合里每一个 standing 都映射到一个 decision，且没有第三种结果。

    穷举而不是举两个例子：一个漏在 else 分支里的 standing 会让某一类文档
    悄悄走进"派生物"或"权威"的默认那一侧。
    """
    standings = sorted(governance._values(VOCABULARY, "standing"))
    assert standings == ["CANONICAL", "DERIVATIVE"]
    seen = set()
    for standing in standings:
        answer = canonical() if standing == "CANONICAL" else derivative()
        record = resolve(answer=answer)
        assert record["standing"] == standing
        assert record["decision"] == standing
        assert record["is_the_authority"] is (standing == "CANONICAL")
        seen.add(record["decision"])
    assert seen == set(standings)


def test_a_derivative_of_a_derivative_never_reports_a_canonical_root():
    """一跳的判定看起来永远是对的。所以链要走到头：
    r1 ← r2 ← r0，只有 r0 是权威版本。"""
    sources = {
        "doc:demo/report/r2": derivative(copied_from=ANCESTOR),
        ANCESTOR: canonical(),
        REF: derivative(copied_from="doc:demo/report/r2"),
    }
    chain = governance.authority_chain(REF, sources, VOCABULARY)
    assert chain["chain"] == [REF, "doc:demo/report/r2", ANCESTOR]
    assert chain["depth"] == 2
    assert chain["terminus"] == "CANONICAL"
    assert chain["root_is_canonical"] is True
    assert chain["is_gap"] is False


def test_a_chain_that_ends_outside_a_canonical_record_is_not_a_canonical_root():
    """链走到一份没人声明在管的文档上就断了 —— 断在一份派生物上更是如此。
    root_is_canonical 只有在真的走到一条 CANONICAL 记录时才为真。"""
    sources = {REF: derivative(),
               ANCESTOR: derivative(copied_from="doc:demo/report/absent")}
    chain = governance.authority_chain(REF, sources, VOCABULARY)
    assert chain["terminus"] == "SOURCE_UNAVAILABLE"
    assert chain["root_is_canonical"] is False
    assert chain["is_gap"] is True


def test_a_chain_that_loops_reports_a_cycle_rather_than_looping():
    sources = {REF: derivative(copied_from=ANCESTOR),
               ANCESTOR: derivative(copied_from=REF)}
    chain = governance.authority_chain(REF, sources, VOCABULARY)
    assert chain["terminus"] == "CYCLE"
    assert chain["root_is_canonical"] is False
    assert REF in chain["reason"]


def test_every_terminus_the_vocabulary_declares_is_reachable_and_explained():
    """五种终点都要能真的走一遍，而且每一种都要说出为什么停在那里。
    声明了却走不到的一种，会一直读起来像一条已经被支持的能力。"""
    loop = {REF: derivative(copied_from=ANCESTOR), ANCESTOR: derivative(copied_from=REF)}
    # 一条比 limit 更长的链：每一步都指向下一步，最后一步没有应答。
    deep = {f"doc:demo/report/r{i}": derivative(copied_from=f"doc:demo/report/r{i + 1}")
            for i in range(5)}
    cases = {
        "CANONICAL": (REF, {REF: canonical()}),
        "NOT_DECLARED": (REF, {REF: source(None)}),
        "SOURCE_UNAVAILABLE": (REF, {REF: derivative()}),
        "CYCLE": (REF, loop),
        "DEPTH_LIMIT": ("doc:demo/report/r0", deep),
    }
    assert set(cases) == set(governance.TERMINI)
    for terminus, (ref, sources) in cases.items():
        chain = governance.authority_chain(ref, sources, VOCABULARY, limit=3)
        assert chain["terminus"] == terminus, terminus
        assert chain["reason"].strip(), terminus
        assert chain["root_is_canonical"] is (terminus == "CANONICAL"), terminus


def test_the_answer_shape_carries_the_record_the_citation_and_the_chain():
    """调用方拿到的是三件东西：完整记录、可以直接抄走的引用行、以及链。
    引用行必须在应答里，因为引用它的人抄走的是这一行，不是整份记录。"""
    request = {"schema": governance.REQUEST_SCHEMA, "ref": REF,
               "source": derivative(),
               "sources": {REF: derivative(), ANCESTOR: canonical()},
               "chain_limit": 4}
    out = governance.answer(request, VOCABULARY)
    assert out["schema"] == "serveros/document-governance-answer.v1"
    assert out["record"]["decision"] == "DERIVATIVE"
    assert out["citation"] == governance.citation(out["record"])
    assert out["chain"]["terminus"] == "CANONICAL"
    assert out["chain"]["root_is_canonical"] is True


def test_a_chain_that_hits_the_limit_says_it_stopped_looking():
    """被截断的链看起来跟走到头一模一样，所以上限要报出来，而且是缺口。"""
    sources = {}
    ref = ANCESTOR
    for i in range(6):
        sources[ref] = derivative(copied_from=f"doc:demo/report/r{i + 10}")
        ref = f"doc:demo/report/r{i + 10}"
    chain = governance.authority_chain(ANCESTOR, sources, VOCABULARY, limit=3)
    assert chain["terminus"] == "DEPTH_LIMIT"
    assert chain["root_is_canonical"] is False
    assert chain["is_gap"] is True
    assert len(chain["chain"]) == 3


# --------------------------------------------------------------------------- #
# 红线 3：源答了没有 ≠ 源答了什么
# --------------------------------------------------------------------------- #

def test_an_absent_document_is_a_finding_and_an_unanswered_source_is_a_gap():
    """"源说没有这样一份文档"是发现；"源没有回答"是缺口。两者都缺一个具体值，
    但下游要做的处置完全不同：一个是记一笔，另一个是再去问一次。"""
    absent = resolve(answer=source(None))
    gap = resolve(answer={"source_ref": "registry:demo-index", "answered": False,
                          "unanswered_because": "索引服务连不上"})

    assert absent["decision"] == "NOT_DECLARED"
    assert gap["decision"] == "SOURCE_UNAVAILABLE"
    assert absent["decision"] != gap["decision"]

    assert (absent["source_answered"], absent["source_declared"]) == (True, False)
    assert (gap["source_answered"], gap["source_declared"]) == (False, False)

    assert absent["is_finding"] is True and absent["is_gap"] is False
    assert gap["is_gap"] is True and gap["is_finding"] is False

    assert "未被声明" in governance.citation(absent)
    assert "无法判定" in governance.citation(gap)
    # 缺口不携带任何关于文档的结论：归属、权威版本一个都不能有。
    for field in ("standing", "owner", "item", "copied_from", "copy_at"):
        assert gap[field] is None
        assert absent[field] is None
    assert gap["authority"]["at"] is None


def test_an_unanswered_source_must_say_what_it_could_not_answer():
    """不说原因的缺口与"没问过"在下游看起来一样。"""
    with pytest.raises(governance.Rejected) as excinfo:
        resolve(answer={"source_ref": "registry:demo-index", "answered": False})
    assert "unanswered_because" in str(excinfo.value)


def test_an_absent_answer_must_say_what_it_looked_for():
    with pytest.raises(governance.Rejected) as excinfo:
        resolve(answer={"source_ref": "registry:demo-index", "answered": True,
                        "declared": False})
    assert "because" in str(excinfo.value)


def test_a_missing_answer_for_one_hop_is_a_gap_not_an_absence():
    """链上某一环调用方没有给出应答 —— 那是没问过，不是"没有这样一份文档"。"""
    chain = governance.authority_chain(REF, {REF: derivative()}, VOCABULARY)
    assert chain["terminus"] == "SOURCE_UNAVAILABLE"
    assert chain["terminus_ref"] == ANCESTOR
    assert chain["is_gap"] is True and chain["is_finding"] is False


# --------------------------------------------------------------------------- #
# 红线 4：畸形请求被拒，且两个值都出现
# --------------------------------------------------------------------------- #

def test_a_malformed_ref_names_what_was_given_and_what_is_allowed():
    with pytest.raises(governance.Rejected) as excinfo:
        resolve(ref="doc:demo/report")
    message = str(excinfo.value)
    assert "'doc:demo/report'" in message
    assert governance.REF_SHAPE in message


def test_a_uppercase_ref_is_rejected_rather_than_normalised():
    """归一化会让只差大小写的两个拼写静默变成同一条引用，于是一份文档有两个
    身份 —— 那正是"稳定身份"要消掉的东西。"""
    with pytest.raises(governance.Rejected) as excinfo:
        resolve(ref="doc:demo/report/R1")
    message = str(excinfo.value)
    assert "'doc:demo/report/R1'" in message
    assert governance.REF_SHAPE in message


def test_a_wildcard_ref_is_refused_because_it_can_stand_for_every_document():
    with pytest.raises(governance.Rejected) as excinfo:
        resolve(ref="doc:demo/report/*")
    message = str(excinfo.value)
    assert "'doc:demo/report/*'" in message
    assert "通配" in message


def test_a_request_without_a_source_is_rejected_naming_both_sides():
    with pytest.raises(governance.Rejected) as excinfo:
        governance.answer({"schema": governance.REQUEST_SCHEMA, "ref": REF}, VOCABULARY)
    message = str(excinfo.value)
    assert "source" in message and "ref" in message


def test_a_request_whose_schema_is_something_else_is_rejected_naming_both():
    with pytest.raises(governance.Rejected) as excinfo:
        governance.answer({"schema": "serveros/something-else.v1", "ref": REF,
                           "source": canonical()}, VOCABULARY)
    message = str(excinfo.value)
    assert "'serveros/something-else.v1'" in message
    assert governance.REQUEST_SCHEMA in message


def test_an_answered_field_that_is_missing_is_not_read_as_yes():
    """缺省 answered 会把"源没答"读成"源答了：没有这样一份文档"，也就是把
    缺口变成一条关于文档的发现 —— 本模块要分开的正是这两件事。"""
    with pytest.raises(governance.Rejected) as excinfo:
        resolve(answer={"source_ref": "registry:demo-index"})
    message = str(excinfo.value)
    assert "None" in message and "true" in message and "false" in message


def test_a_declared_field_that_is_missing_is_not_read_as_absent():
    with pytest.raises(governance.Rejected) as excinfo:
        resolve(answer={"source_ref": "registry:demo-index", "answered": True})
    message = str(excinfo.value)
    assert "None" in message and "true" in message and "false" in message


def test_a_source_that_says_it_holds_the_document_must_say_what_it_holds():
    """declared=true 而没有 record，是源答了"有"却说不出来是什么。把它当成
    "没有"会捏造一条发现，把它当成"有"会让归属与权威版本全部空着。"""
    with pytest.raises(governance.Rejected) as excinfo:
        resolve(answer={"source_ref": "registry:demo-index", "answered": True,
                        "declared": True})
    message = str(excinfo.value)
    assert "source.record" in message            # 给的是哪一个
    assert "NoneType" in message                 # 给的是什么
    assert "对象" in message                      # 允许的是什么


def test_a_copied_from_that_is_a_list_is_rejected():
    """一份文档只有一个权威版本。接受一张列表会把这个选择留给内核，而
    "哪一份是权威"是治理决定，不是本模块能替人做的决定。"""
    with pytest.raises(governance.Rejected) as excinfo:
        resolve(answer=derivative(copied_from=[ANCESTOR]))
    message = str(excinfo.value)
    assert ANCESTOR in message
    assert "非空字符串" in message


def test_a_ref_whose_kind_disagrees_with_the_record_is_rejected_naming_both():
    with pytest.raises(governance.Rejected) as excinfo:
        resolve(answer=canonical(kind="brief"))
    message = str(excinfo.value)
    assert "'report'" in message and "'brief'" in message


def test_an_item_scope_document_must_name_its_item():
    with pytest.raises(governance.Rejected) as excinfo:
        resolve(answer=canonical(item=None))
    assert "source.record.item" in str(excinfo.value)


def test_a_node_scope_document_that_names_an_item_is_rejected():
    with pytest.raises(governance.Rejected) as excinfo:
        resolve(ref="doc:demo/understanding/u1",
                answer=canonical(kind="understanding", scope="node", item=ITEM))
    message = str(excinfo.value)
    assert "'node'" in message and ITEM in message


def test_a_kind_cannot_be_declared_at_the_wrong_scope():
    """brief 是 Item 文档，这是两份契约已经做出的声明。让源把它说成节点级，
    会让它同时属于所有人，也就是不属于任何一次验收。"""
    with pytest.raises(governance.Rejected) as excinfo:
        resolve(ref="doc:demo/brief/b1",
                answer=canonical(kind="brief", scope="node", item=None))
    message = str(excinfo.value)
    assert "'item'" in message and "'node'" in message


# --------------------------------------------------------------------------- #
# 红线 5：闭词表 —— 未声明的类别被拒，而不是落到兜底
# --------------------------------------------------------------------------- #

def test_an_undeclared_standing_is_rejected_naming_the_given_and_the_declared():
    with pytest.raises(governance.Rejected) as excinfo:
        resolve(answer=canonical(standing="COPY"))
    message = str(excinfo.value)
    assert "'COPY'" in message
    assert "'CANONICAL'" in message and "'DERIVATIVE'" in message


def test_an_undeclared_kind_is_rejected_naming_the_given_and_the_declared():
    with pytest.raises(governance.Rejected) as excinfo:
        resolve(answer=canonical(kind="memo"))
    message = str(excinfo.value)
    assert "'memo'" in message
    assert "'report'" in message and "'brief'" in message


def test_an_undeclared_surface_is_rejected_naming_the_given_and_the_declared():
    with pytest.raises(governance.Rejected) as excinfo:
        resolve(answer=canonical(copy_at={"surface": "dropbox", "locator": "x"}))
    message = str(excinfo.value)
    assert "'dropbox'" in message
    assert "'git'" in message and "'local'" in message


def test_no_dimension_declares_a_fallback_category():
    """兜底类别是未声明的值会静默落进去的地方，而落进去之后没有一处能说出
    它是什么。闭词表的价值全在这条上，所以它被断言，而不是靠读的人记得。"""
    banned = {"OTHER", "UNKNOWN", "MISC", "N/A", "NONE", "DEFAULT", "OTHER_KIND"}
    for dimension in VOCABULARY["dimensions"]:
        declared = set(governance._values(VOCABULARY, dimension))
        assert not (declared & banned), f"{dimension} 声明了兜底类别"


def test_every_decision_the_kernel_can_return_is_declared():
    """内核能返回而词表没有声明的判断，是一个没有人同意过的值。"""
    declared = set(governance._values(VOCABULARY, "decision"))
    assert declared == set(governance.DECISIONS)
    for name in ("STANDINGS", "SOURCE_STATES", "TERMINI"):
        values = set(getattr(governance, name))
        declared_values = set(governance._values(
            VOCABULARY, {"STANDINGS": "standing", "SOURCE_STATES": "source_state",
                         "TERMINI": "terminus"}[name]))
        assert declared_values == values, name


def test_the_declared_values_are_the_ones_the_kernel_actually_returns():
    """词表不是注释：每一个声明过的值都要在内核的返回值里真的出现过，
    否则它会一直读起来像一条已经被支持的能力。"""
    produced = set()
    for answer in (canonical(), derivative(), source(None),
                   {"source_ref": "registry:demo-index", "answered": False,
                    "unanswered_because": "连不上"}):
        produced.add(resolve(answer=answer)["decision"])
        produced.add(resolve(answer=answer)["source_state"])
    assert produced == set(governance.DECISIONS) - {"REJECTED"} | set(governance.SOURCE_STATES)


# --------------------------------------------------------------------------- #
# 词表对照真实声明：自己跟自己一致是没有意义的
# --------------------------------------------------------------------------- #

def _declared_kinds() -> tuple[set[str], set[str]]:
    """fabric 已经做出的两份声明：哪些文档存在、每一种属于谁。"""
    skeletons = yaml.safe_load(SKELETONS_PATH.read_text(encoding="utf-8"))
    workchain = yaml.safe_load(WORKCHAIN_PATH.read_text(encoding="utf-8"))
    item_documents = {Path(entry["name"]).stem for entry in workchain["item_documents"]}
    document_kinds = set()
    node_scope = set()
    for entry in skeletons["documents"]:
        document_kinds.add(entry["id"])
        if entry.get("scope") == "node":
            node_scope.add(entry["id"])
    return document_kinds | item_documents, node_scope


def test_the_declared_kinds_are_exactly_the_documents_the_fabric_declares():
    """双向：词表里多一个 kind 是凭空发明了一种文档，少一个则是有一份文档
    无法被治理 —— 两种都要红，而不是靠读的人碰巧发现。"""
    declared_documents, _ = _declared_kinds()
    assert set(governance._values(VOCABULARY, "kind")) == declared_documents


def test_the_scope_of_each_kind_follows_the_contracts():
    """哪些文档属于一个 Item、哪些属于整个节点，是两份契约已经写下的区分；
    词表只是把它写成可判定的形式。"""
    declared_documents, node_scope = _declared_kinds()
    kind_scope = governance._kind_scope(VOCABULARY)
    assert set(kind_scope) == declared_documents
    for kind in sorted(declared_documents):
        expected = "node" if kind in node_scope else "item"
        assert kind_scope[kind] == expected, kind
        assert expected in governance._values(VOCABULARY, "scope")


def test_every_surface_this_vocabulary_declares_still_exists_in_space():
    """单向包含，方向是刻意的：这里凭空多出一种表面一定是我写错了，而 Space
    新增一种表面不会使这里出错 —— 它还只是"暂时无法被声明的表面"。"""
    space = json.loads(SPACE_VOCABULARY_PATH.read_text(encoding="utf-8"))
    surfaces = set(space["dimensions"]["surface"]["values"])
    declared = set(governance._values(VOCABULARY, "surface"))
    assert declared, "词表没有声明任何表面"
    assert declared <= surfaces, sorted(declared - surfaces)


def test_every_kind_scope_value_is_one_of_the_two_declared_scopes():
    for kind, scope in governance._kind_scope(VOCABULARY).items():
        assert scope in governance._values(VOCABULARY, "scope"), kind


# --------------------------------------------------------------------------- #
# 常量兜底必须报出来
# --------------------------------------------------------------------------- #

def test_an_unsigned_answer_uses_the_constant_fallback_and_reports_it():
    """没署名的应答：记在某个具体源头上是错的，因为不回答也是错的。
    所以兜底照用，但必须出现在记录的 fallbacks 里。"""
    record = resolve(answer={"answered": False, "unanswered_because": "连不上"})
    assert record["authority"]["source_ref"] == governance.SOURCE_FALLBACK
    assert [f["field"] for f in record["fallbacks"]] == ["source.source_ref"]
    assert record["fallbacks"][0]["used"] == governance.SOURCE_FALLBACK
    assert record["fallbacks"][0]["because"].strip()


def test_a_signed_answer_reports_no_fallback():
    """没有用到兜底时 fallbacks 是空的 —— 于是"用了兜底"永远看得见。"""
    assert resolve()["fallbacks"] == []


# --------------------------------------------------------------------------- #
# 结构：判定函数不读文件、不读时钟
# --------------------------------------------------------------------------- #

def _tree():
    return ast.parse(KERNEL_PATH.read_text(encoding="utf-8"))


def test_the_module_imports_only_the_standard_decision_surface():
    names = set()
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    assert names - {"__future__"} == {"argparse", "json", "re", "pathlib"}


def test_only_the_two_edges_read_a_file():
    """读盘只允许发生在两个边上：读词表，和读调用方指定的请求文件。
    判定函数读盘会把一次判定变成一次 I/O，而一次失败的 I/O 与一个判定结果
    在返回值里长得一样。"""
    readers = set()
    for node in ast.walk(_tree()):
        if not isinstance(node, ast.FunctionDef):
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Attribute) and inner.attr in (
                    "read_text", "read_bytes", "open"):
                readers.add(node.name)
            elif isinstance(inner, ast.Name) and inner.id == "open":
                readers.add(node.name)
    assert readers == {"load_vocabulary", "main"}


def test_the_kernel_takes_no_clock_and_reaches_no_process():
    """不读时钟：一条记录必须能被重放。不起进程：本模块没有要跑的东西。"""
    names = set()
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    assert not (names & {"time", "datetime", "random", "subprocess", "socket",
                         "os", "shutil", "urllib", "sqlite3"})


def test_resolve_is_deterministic_across_calls():
    """两次同样的输入必须得到同一份记录：否则"引用一行"在两次生成之间会变。"""
    first, second = resolve(), resolve()
    assert first == second
    assert json.dumps(first, ensure_ascii=False, sort_keys=True) == \
        json.dumps(second, ensure_ascii=False, sort_keys=True)


def test_the_record_shape_does_not_change_with_the_decision():
    """"缺字段"会被读成"这个字段还没有人说过"，而实际发生的是"这次判定没有
    走到那里"。两者对调用方的处置完全不同，所以形状不变。"""
    canonical_record = resolve()
    for answer in (derivative(), source(None),
                   {"source_ref": "registry:demo-index", "answered": False,
                    "unanswered_because": "连不上"}):
        assert set(resolve(answer=answer)) == set(canonical_record)


# --------------------------------------------------------------------------- #
# 变异：把内核改坏，头条断言必须变红
# --------------------------------------------------------------------------- #

def _mutant(tmp_path, before: str, after: str):
    source_text = KERNEL_PATH.read_text(encoding="utf-8")
    assert source_text.count(before) == 1, f"变异锚点不唯一：{before!r}"
    path = tmp_path / "mutated_document_governance.py"
    path.write_text(source_text.replace(before, after), encoding="utf-8")
    spec = importlib.util.spec_from_file_location("document_governance_mutant", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_mutation_flipping_is_the_authority_turns_the_headline_assertion_red(tmp_path):
    """把 `is_the_authority` 从判断改成常量 True，等价于"派生物也是权威版本"。

    下面那条断言与 `test_a_derivative_is_never_the_authority` 里的逐字相同：
    内核被改坏之后它必须红，这里以变异体的形式让它红一次，证明那条断言不是
    无论如何都会绿的。
    """
    mutant = _mutant(tmp_path, '"is_the_authority": standing == "CANONICAL",',
                     '"is_the_authority": True,')
    record = mutant.resolve(REF, derivative(), VOCABULARY)
    # 这两条断言与 test_a_derivative_is_never_the_authority 里的逐字相同。
    assert record["decision"] == "DERIVATIVE"          # 变异体上仍然通过
    with pytest.raises(AssertionError):
        assert record["is_the_authority"] is False     # 变异体上必须红
    # 对照：原内核上两条都通过。
    real = resolve(answer=derivative())
    assert real["decision"] == "DERIVATIVE" and real["is_the_authority"] is False
    assert governance.is_authoritative_reference(real) is False


def test_mutation_flipping_the_decision_turns_the_headline_assertion_red(tmp_path):
    """把判定本身改成常量 CANONICAL，等价于"每一份文档都是权威版本"。

    这一处与上一处不同：is_the_authority 仍然算得对，所以挡下它的只能是
    decision 那一条 —— 两次变异各自证明一条断言不是白写的。
    """
    mutant = _mutant(tmp_path, '"decision": standing,', '"decision": "CANONICAL",')
    record = mutant.resolve(REF, derivative(), VOCABULARY)
    assert record["is_the_authority"] is False         # 变异体上仍然通过
    with pytest.raises(AssertionError):
        assert record["decision"] == "DERIVATIVE"      # 变异体上必须红


def test_mutation_flipping_is_gap_turns_the_gap_assertion_red(tmp_path):
    """把缺口那一支的 `is_gap` 改成 False，等价于"没有回答也是一条发现"。"""
    mutant = _mutant(tmp_path, 'record["is_gap"] = True', 'record["is_gap"] = False')
    gap = mutant.resolve(REF, {"source_ref": "registry:demo-index", "answered": False,
                               "unanswered_because": "连不上"}, VOCABULARY)
    with pytest.raises(AssertionError):
        assert gap["is_gap"] is True and gap["is_finding"] is False
    assert resolve(answer={"source_ref": "registry:demo-index", "answered": False,
                           "unanswered_because": "连不上"})["is_gap"] is True
