"""P4 — 维护契约能不能被读、能不能被问，以及它腐坏时说的是什么。

这一组守的是 `intake/managed_documents.py` 的四件事，每一件都在活体那一份真实
契约上被真的碰到过：

1. **腐坏要有名有姓。** 真实那份 profile 带着八个派生状态字段，两个能力动词都
   只答「契约已腐坏」而**不说是哪几个**（能力自己的原话是不打印字段表的，只看
   `dynamic_state_fields` 的返回值）。这里钉的是：ServerOS 那一侧把字段路径打出来。
2. **第二个缺陷是被第一个挡住的，不是不存在的。** 同一份 profile 的 sections
   没有 `key`，而能力的 `derive_sections` 与 `detect_drift` 都按 `key` 索引 ——
   删掉动态字段之后它们抛 `KeyError`。这一组用**真的能力脚本**（在本机存在时）
   钉住这条，并明确它在脚本不在时的取值。
3. **交付形态是声明的，也是要对的。** `FOLLOW_MIRROR` 配 `standing: CANONICAL`
   是文档词表点名过的那一种错读；`UNDECLARED` 与 `INCONSISTENT` 是两件事。
4. **一份对象两份物化要合起来报。** 两行指同一个 locator 不是一个对象两次。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]

#: 本机装着的那份能力。**不是依赖**：它不在本仓，装不装是节点的事。
INSTALLED_ABILITY = Path.home() / ".claude" / "skills" / "document-stewardship" / "scripts" / "maintain.py"


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(
        "serveros_intake_" + name, REPO_ROOT / "intake" / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules["serveros_intake_" + name] = module
    spec.loader.exec_module(module)
    return module


managed = _load("managed_documents", "managed_documents.py")

WIKI = "https://fjdynamics.feishu.cn/wiki/XQErwPZLXivD8dkvwR0cZ42VnRd"
VOCAB = REPO_ROOT / "space" / "vocabulary.json"


#: 一份**真的能被 load_ability 加载**的替身能力。
#:
#: 之所以是文件而不是一个 Python 对象：`read_root` 走的是「从调用方给的路径把能力
#: 加载进来」这条路，而那条路本身是要验的（能力不在时是 `ABILITY_NOT_FOUND`，
#: 不是异常）。三个名字与三个关键字是这一组要钉的接口：
#: `dynamic_state_fields(profile)`、`derive_sections(profile, headings)`、
#: `authorize_edit(profile, section_key, change_class, *, authority,
#: original_text, proposed_text)`。真能力的 `dynamic_state_fields` 递归下钻，
#: 所以父字段与它内部的字段**都会**被报出来 —— 替身照抄这一点，因为活体那份
#: profile 的八个字段正是四个 block_id 加上 last_verified 这一家三口。
STUB_ABILITY = '''
DYNAMIC_PROFILE_KEYS = ("revision", "block_id", "resolution", "last_verified")


def dynamic_state_fields(profile):
    hits = []

    def walk(node, path):
        if isinstance(node, dict):
            for key, value in node.items():
                here = f"{path}.{key}" if path else str(key)
                if str(key) in DYNAMIC_PROFILE_KEYS:
                    hits.append(here)
                walk(value, here)
        elif isinstance(node, list):
            for i, value in enumerate(node):
                walk(value, f"{path}[{i}]")

    walk(profile, "")
    return hits


def derive_sections(profile, headings):
    derived = {}
    for section in profile.get("sections") or []:
        want = (section.get("match") or {}).get("heading")
        if not want:
            continue
        for level, block_id, text in headings:
            if text == want:
                derived[section["key"]] = {"heading": text, "level": level,
                                           "block_id": block_id}
                break
    return derived


def authorize_edit(profile, section_key, change_class, *, authority=None,
                   original_text="", proposed_text=""):
    return {"decision": "preview", "reason": "stub"}
'''


class StubAbility:
    """直接拿在手里的替身，用来量 `gate` 转发了什么。

    它只答一件事：`gate` 是把这三个关键字**原样**交出去的，还是自己改写过。
    """

    def __init__(self, decision="preview"):
        self._decision = decision
        self.calls: list[tuple] = []

    def authorize_edit(self, profile, section_key, change_class, *,
                       authority=None, original_text="", proposed_text=""):
        self.calls.append((section_key, change_class, authority,
                           original_text, proposed_text))
        return {"decision": self._decision, "reason": "stub"}


def a_profile(**over) -> dict:
    profile = {
        "id": "doc-a",
        "title": "一篇文档",
        "standing": "CANONICAL",
        "body_authority": {"surface": "wiki", "locator": WIKI},
        "sync_mode": "NONE",
        "sections": [{"key": "one", "match": {"heading": "一"}, "register": "runbook"}],
        "edit_policy": {"factual_patch": "preview",
                        "section_refactor": "preview",
                        "structural_rewrite": "approval"},
    }
    profile.update(over)
    return profile


def a_tree(tmp_path: Path, *, registry: dict | None = None, profile: dict | None = None,
           state: dict | None = None, ability: Path | None = None):
    """一份最小的真实形状：registry + profiles/<id>.yaml + state/<id>.json。"""
    root = tmp_path / "managed-documents"
    (root / "profiles").mkdir(parents=True)
    registry = registry if registry is not None else {
        "schema": "managed-documents/registry/v1",
        "documents": [{"id": "doc-a", "title": "一篇文档", "locator": WIKI,
                       "status": "managed", "profile": "profiles/doc-a.yaml",
                       "relation": "OWNED"}],
    }
    (root / "registry.yaml").write_text(
        yaml.safe_dump(registry, allow_unicode=True, sort_keys=False), encoding="utf-8")
    profile = profile if profile is not None else a_profile()
    (root / "profiles" / "doc-a.yaml").write_text(
        yaml.safe_dump(profile, allow_unicode=True, sort_keys=False), encoding="utf-8")
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    if state is not None:
        (state_dir / "doc-a.json").write_text(json.dumps(state), encoding="utf-8")
    if ability is None:
        path = tmp_path / "stub.py"
        path.write_text(STUB_ABILITY, encoding="utf-8")
        ability = path
    return root / "registry.yaml", state_dir, ability


def read(tmp_path, **kwargs):
    registry, state_dir, ability = a_tree(tmp_path, **kwargs)
    return managed.read_root(registry, state_dir, ability, VOCAB)


class TestTheRegistrysOwnHardRules:
    """registry 自己写下的三条硬规则，逐条有一个读数。"""

    def test_a_status_that_is_not_written_is_not_read_as_managed(self, tmp_path):
        registry = {"documents": [{"id": "doc-a", "locator": WIKI,
                                   "profile": "profiles/doc-a.yaml"}]}
        report = read(tmp_path, registry=registry)
        assert report["documents"][0]["contract"] == "STATUS_UNDECLARED"

    def test_a_status_that_is_not_declared_is_refused_not_defaulted(self, tmp_path):
        registry = {"documents": [{"id": "doc-a", "locator": WIKI, "status": "adopted",
                                   "profile": "profiles/doc-a.yaml"}]}
        entry = read(tmp_path, registry=registry)["documents"][0]
        assert entry["contract"] == "STATUS_UNKNOWN"
        assert "adopted" in entry["reasons"][0]

    def test_managed_without_a_profile_is_refused(self, tmp_path):
        # 规则 2 的原话：「没有契约就不能改」。
        registry = {"documents": [{"id": "doc-a", "locator": WIKI, "status": "managed"}]}
        assert read(tmp_path, registry=registry)["documents"][0]["contract"] == "PROFILE_MISSING"

    def test_a_profile_whose_id_is_a_different_document_is_refused(self, tmp_path):
        report = read(tmp_path, profile=a_profile(id="doc-b"))
        entry = report["documents"][0]
        assert entry["contract"] == "PROFILE_ID_MISMATCH"
        assert "doc-b" in entry["reasons"][0]


class TestCorruptionIsReportedByName:
    def test_every_dynamic_field_is_named_not_counted(self, tmp_path):
        # 报的是路径而不是个数：「契约已腐坏」不说是哪几个字段，读的人只能回去猜，
        # 而活体那一份的答案是八个，读的人自己数是四个。
        profile = a_profile(last_verified={"revision": 1},
                            sections=[{"key": "one", "block_id": "b1",
                                       "match": {"heading": "一"}}])
        entry = read(tmp_path, profile=profile)["documents"][0]
        assert entry["contract"] == "DYNAMIC_FIELDS_IN_PROFILE"
        assert set(entry["reasons"]) == {"sections[0].block_id", "last_verified",
                                         "last_verified.revision"}
        # 父字段与它内部的字段都报 —— 这是真能力的走法，不是这里的双重计数。
        assert entry["reasons"].count("last_verified") == 1
        assert len(entry["reasons"]) == 3

    def test_a_section_without_a_key_is_its_own_state(self, tmp_path):
        # 与「有动态字段」不同的读数：两种腐坏要能分开，因为修法不同。
        profile = a_profile(sections=[{"match": {"heading": "一"}}])
        entry = read(tmp_path, profile=profile)["documents"][0]
        assert entry["contract"] == "SECTION_WITHOUT_KEY"
        assert entry["reasons"] == ["sections[0]"]

    def test_a_missing_ability_is_an_answer_and_not_a_crash(self, tmp_path):
        report = read(tmp_path, ability=tmp_path / "nowhere.py")
        assert report["ability_loaded"] is False
        assert report["documents"][0]["contract"] == "ABILITY_NOT_FOUND"

    def test_a_registry_that_does_not_parse_says_where(self, tmp_path):
        registry, state_dir, ability = a_tree(tmp_path)
        registry.write_text("documents:\n  - id: a\n   bad: indent\n", encoding="utf-8")
        report = managed.read_root(registry, state_dir, ability, VOCAB)
        assert report["contract"] == "REGISTRY_UNREADABLE"
        assert "line" in report["why"] and "column" in report["why"]
        # 报告不是转载文档的地方：解析器的消息带着一个代码框，那框就是文档正文。
        assert "bad: indent" not in report["why"]


class TestTheDeliveryForm:
    def test_an_owned_document_reads_consistent(self, tmp_path):
        delivery = read(tmp_path)["documents"][0]["delivery"]
        assert delivery["state"] == "CONSISTENT"
        assert delivery["form"] == "OWNED"

    def test_no_relation_declared_is_undeclared_and_not_a_default(self, tmp_path):
        registry = {"documents": [{"id": "doc-a", "locator": WIKI, "status": "managed",
                                   "profile": "profiles/doc-a.yaml"}]}
        delivery = read(tmp_path, registry=registry)["documents"][0]["delivery"]
        assert delivery["state"] == "UNDECLARED" and delivery["form"] is None
        assert "nobody decided" in delivery["why"]

    def test_a_mirror_that_calls_itself_the_authority_is_a_contradiction(self, tmp_path):
        # 文档词表点名的那个方向：把副本读成权威，副本就会被当权威引用出去。
        registry = {"documents": [{"id": "doc-a", "locator": WIKI, "status": "managed",
                                   "profile": "profiles/doc-a.yaml",
                                   "relation": "FOLLOW_MIRROR"}]}
        entry = read(tmp_path, registry=registry,
                     profile=a_profile(standing="CANONICAL"))["documents"][0]
        assert entry["contract"] == "CHECKED"          # 契约本身是好的
        assert entry["delivery"]["state"] == "INCONSISTENT"
        assert "cannot both be true" in entry["delivery"]["why"]

    def test_a_mirror_with_no_source_is_undeclared_not_consistent(self, tmp_path):
        registry = {"documents": [{"id": "doc-a", "locator": WIKI, "status": "managed",
                                   "profile": "profiles/doc-a.yaml",
                                   "relation": "FOLLOW_MIRROR"}]}
        entry = read(tmp_path, registry=registry,
                     profile=a_profile(standing="DERIVATIVE"))["documents"][0]
        assert entry["delivery"]["state"] == "UNDECLARED"
        assert "copied_from" in entry["delivery"]["why"]

    def test_an_undeclared_surface_is_refused_by_name(self, tmp_path):
        profile = a_profile(body_authority={"surface": "feishu", "locator": WIKI})
        entry = read(tmp_path, profile=profile)["documents"][0]
        assert entry["delivery"]["state"] == "INCONSISTENT"
        assert "'feishu'" in entry["delivery"]["why"] and "wiki" in entry["delivery"]["why"]

    def test_an_authority_naming_a_surface_but_no_object_is_undeclared(self, tmp_path):
        profile = a_profile(body_authority={"surface": "wiki"})
        entry = read(tmp_path, profile=profile)["documents"][0]
        assert entry["delivery"]["state"] == "UNDECLARED"
        assert "which one" in entry["delivery"]["why"]

    def test_a_sync_mode_outside_the_declared_three_is_refused(self, tmp_path):
        entry = read(tmp_path, profile=a_profile(sync_mode="REALTIME"))["documents"][0]
        assert entry["delivery"]["state"] == "INCONSISTENT"
        assert "REALTIME" in entry["delivery"]["why"]

    def test_the_surface_set_comes_from_the_repos_own_vocabulary(self, tmp_path):
        # 表是从 space/vocabulary.json 读的，不是在这里抄一份；读不到就是空集，
        # 于是一切声明都被拒 —— 闭词表这条纪律对装着词表的那个文件自己成立。
        registry, state_dir, ability = a_tree(tmp_path)
        report = managed.read_root(registry, state_dir, ability,
                                   tmp_path / "no-vocabulary.json")
        assert report["surfaces"] == []
        entry = report["documents"][0]
        assert entry["delivery"]["state"] == "INCONSISTENT"
        assert "wiki" in entry["delivery"]["why"]      # 拒绝里列出了声明里有哪几个


class TestTheWritePolicyAndTheReach:
    def test_each_change_class_has_its_own_tier_and_the_gaps_are_named(self, tmp_path):
        profile = a_profile(edit_policy={"factual_patch": "preview"})
        policy = read(tmp_path, profile=profile)["documents"][0]["write_policy"]
        assert policy["tiers"] == {"PATCH": "preview", "REFINE": None,
                                   "RESTRUCTURE": None}
        assert policy["undeclared"] == ["REFINE", "RESTRUCTURE"]

    def test_never_observed_is_not_the_same_as_observed_and_unchanged(self, tmp_path):
        reach = read(tmp_path)["documents"][0]["reach"]     # state 不存在
        assert reach["state_present"] is False
        assert reach["state_revision"] is None

    def test_a_declared_section_the_state_never_derived_is_named(self, tmp_path):
        state = {"revision": 5, "last_observed_at": "2026-09-14T00:00:00+00:00",
                 "derived": {"sections": {"one": {"block_id": "b1"}}},
                 "unresolved_section_keys": []}
        profile = a_profile(sections=[{"key": "one", "match": {"heading": "一"}},
                                      {"key": "two", "match": {"heading": "二"}}])
        reach = read(tmp_path, profile=profile, state=state)["documents"][0]["reach"]
        assert reach["declared_never_derived"] == ["two"]
        assert reach["derived_never_declared"] == []

    def test_a_derived_section_the_profile_never_declared_is_named(self, tmp_path):
        # 活体那一份真实契约上就是这一条：文档有「五、已完成」，profile 没有。
        state = {"revision": 5, "last_observed_at": "2026-09-14T00:00:00+00:00",
                 "derived": {"sections": {"one": {"block_id": "b1"},
                                          "completed": {"block_id": "b9"}}},
                 "unresolved_section_keys": []}
        reach = read(tmp_path, state=state)["documents"][0]["reach"]
        assert reach["derived_never_declared"] == ["completed"]


class TestOneObjectHoweverManyMaterializations:
    def test_two_rows_naming_one_locator_are_one_object(self, tmp_path):
        registry = {"documents": [
            {"id": "doc-a", "locator": WIKI, "status": "managed",
             "profile": "profiles/doc-a.yaml", "relation": "OWNED"},
            {"id": "doc-a", "locator": WIKI, "status": "managed",
             "profile": "profiles/doc-a.yaml", "relation": "OWNED"},
        ]}
        report = read(tmp_path, registry=registry)
        assert len(report["documents"]) == 2
        assert report["objects"] == [{"locator": WIKI, "documents": ["doc-a", "doc-a"],
                                      "materializations": 2}]

    def test_a_single_materialization_is_still_reported_as_one(self, tmp_path):
        assert read(tmp_path)["objects"] == [
            {"locator": WIKI, "documents": ["doc-a"], "materializations": 1}]


class TestTheGateIsDelegatedAndNeverReplaced:
    def test_the_gate_is_the_abilitys_and_is_asked_with_its_own_keywords(self, tmp_path):
        stub = StubAbility()
        decision = managed.gate(stub, a_profile(), "one", "PATCH",
                                authority="runtime://prod",
                                original_text="旧", proposed_text="新")
        assert decision == {"decision": "preview", "reason": "stub"}
        assert stub.calls == [("one", "PATCH", "runtime://prod", "旧", "新")]

    def test_a_missing_ability_blocks_and_is_not_a_decision_the_gate_made(self, tmp_path):
        decision = managed.gate(None, a_profile(), "one", "PATCH")
        assert decision["decision"] == "blocked"
        assert "ABILITY_NOT_FOUND" in decision["reason"]

    def test_an_unknown_change_class_is_refused_before_the_gate(self, tmp_path):
        stub = StubAbility()
        managed.gate(stub, a_profile(), "one", "REWRITE_EVERYTHING")
        assert stub.calls == []      # 没问过能力，因为问题本身不成立


@pytest.mark.skipif(not INSTALLED_ABILITY.is_file(),
                    reason="本机没装 document-stewardship 能力脚本")
class TestAgainstTheInstalledAbility:
    """本机装着那份能力时，这一组量的是**真的它**，不是替身。"""

    def test_the_two_names_this_module_loads_are_the_abilitys_own(self):
        ability = managed.load_ability(INSTALLED_ABILITY)
        assert ability is not None
        for name in ("dynamic_state_fields", "derive_sections", "authorize_edit"):
            assert callable(getattr(ability, name, None)), name

    def test_the_ability_indexes_sections_by_key_and_raises_without_one(self):
        # **这一条是 P4 第二个缺陷的判据。** 真 profile 的 sections 没有 `key`，
        # 而两个动词都按 `key` 索引 —— 它们**抛**，不是拒绝。今天看不出来，
        # 只因为动态字段那一关先返回了。
        ability = managed.load_ability(INSTALLED_ABILITY)
        profile = a_profile(sections=[{"match": {"heading": "一"}}])
        with pytest.raises(KeyError):
            ability.derive_sections(profile, [(1, "b1", "一")])
        assert managed.contract_health(profile, ability)[0] == "SECTION_WITHOUT_KEY"

    def test_the_gate_decision_vocabulary_is_the_four_this_module_reports(self):
        ability = managed.load_ability(INSTALLED_ABILITY)
        profile = a_profile(sections=[{"key": "one", "match": {"heading": "一"},
                                       "require_fact_verification": True}])
        decisions = {
            ability.authorize_edit(profile, "one", "PATCH")["decision"],       # 无证据
            ability.authorize_edit(profile, "one", "PATCH",
                                   authority="runtime://prod")["decision"],
            ability.authorize_edit(profile, "one", "RESTRUCTURE",
                                   authority="runtime://prod")["decision"],
            ability.authorize_edit(profile, "nope", "PATCH")["decision"],
        }
        assert decisions == {"blocked", "preview", "approval", "blocked"}

    def test_a_change_class_outside_the_abilitys_own_three_is_blocked(self):
        ability = managed.load_ability(INSTALLED_ABILITY)
        decision = ability.authorize_edit(a_profile(), "one", "REWRITE_EVERYTHING")
        assert decision["decision"] == "blocked"
        assert "unknown_change_class" in decision["reason"]
