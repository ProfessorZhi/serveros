"""§4/§5/§6/§11 of `docs/EXTERNAL-REFERENCE-LIBRARY.md` — 外部参考对象的读数。

WHY THIS FILE EXISTS
--------------------
那份合同里有五条规则，每一条都写着「读者不能自己补」，而在此之前三棵树里没有
任何读者：

  * §4「HTTP 200但得到登录页、验证码、脚本壳、截断正文**不能算捕获成功**」——
    `capture_coverage._web_components` 明确拒绝读采集器自己的 `page_kind.state`
    标签，并把这条判断推给了 `intake/external_reference.py`。推过去之后没人接，
    这条规则就等于不存在。
  * §11「只有本地snapshot不算『存进飞书』；有飞书链接但全文/附件没取不能说
    『本地完整同步』」——manifest 里四个 DONE/NOT_DONE 并排摆着，而并排本身就在
    暗示一条链。
  * §11「旧hash不允许绕撤权」「来源404不自动把自己的批注删除」。
  * §5 一份笔记该有什么在首层、什么必须折叠，以及「不能把模型生成的采用建议写成
    官方原文或已接受规范」。
  * §4「相同标题不是同对象」与多发现出处指向同一个对象。

WHAT IS ASSERTED
----------------
四条不变量，每条都构造出来而不靠算术：

  1. 「问过，没有」与「没人问过」永远不是同一个取值。`EVIDENCED_ABSENT` 只在采集器
     确实查过这项标记时出现；`UNCHECKED` 只在没有时出现；一条 `UNCHECKED` 就足以让
     整体不是 `CAPTURED`，无论采集器自己的标签写了什么。
  2. 一个阶段的状态不是从另一个推出来的。`states` 逐字等于 manifest 写的；每个
     DONE 的阶段旁边都带着它不蕴含的那句话，而一个连状态都没有的阶段是 `UNDECLARED`
     加一句「不叫排队」。
  3. `permits()` 没有任何一条路径返回允许。不是今天写成了 false，是这个词表上的
     每一个动作 × 每一种来源都只能得到 false，而词表外的动作得到的是拒绝作答。
  4. 分组永远按 provider + 稳定标识。同一个题名落在两个地址上是两个对象，而只给标题
     的发现出处被拒绝，不会被退回成标题分组。

WHAT IS NOT ASSERTED
--------------------
不断言示例文章的读数应该是什么。活体树那几条只断言与树无关的关系（分母恒等式、
两条形状判定的等价），所以树动了它们仍然成立，而一个不再尊重它们的读者仍然会被
抓住。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

#: 与 `tests/test_capture_coverage.py` / `test_source_policy.py` / `test_material_index.py`
#: 同一个 records 检出。
from live_checkouts import readable_dir  # noqa: E402

LIVE_RECORDS = Path("/data/home/vince/workspace/records")


def _load(name: str, relative: str):
    registered = "serveros_intake_" + name
    if registered in sys.modules:
        return sys.modules[registered]
    spec = importlib.util.spec_from_file_location(
        registered, REPO_ROOT / "intake" / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[registered] = module
    spec.loader.exec_module(module)
    return module


reference = _load("external_reference", "external_reference.py")
capture = _load("capture_coverage", "capture_coverage.py")
policy = _load("source_policy", "source_policy.py")

URL = "https://developers.openai.com/blog/rethinking-skills-and-prompts-for-gpt-6-astra"
OTHER_URL = "https://developers.openai.com/blog/a-different-post-with-the-same-title"
TITLE = "Rethinking skills and prompts for GPT-6 Astra"

#: 采集器实际查过的那六个标记，取自 2026-09-23 那份 manifest。四条 §4 问题里三条
#: 有证据，`truncated_body` 一条都没有——这正是活体树上的真实形态，留在这里是因为
#: 「四条问题各查一次」这句话最容易在样例里被写成四条都有。
CHECKED = ["sign in", "Sign in", "log in", "captcha", "enable JavaScript", "subscribe"]


def a_manifest(**over) -> dict:
    document = {
        "schema": "serveros/web-capture.v1",
        "source": {"provider": "web", "requested_url": URL, "final_url": URL,
                   "redirected": False, "publisher": "OpenAI Developers"},
        "identity_observed": {"title": TITLE, "author": "Eric Provencher",
                              "published": "2026-09-11"},
        "fetch": {"at": "2026-09-23T18:31+08:00", "http_status": 200,
                  "content_type": "text/html", "bytes": 317035,
                  "sha256": "5d81cdb0" * 8, "captured_by": "curl"},
        "page_kind": {"state": "ARTICLE", "shell_markers_found": [],
                      "checked_markers": list(CHECKED)},
        "effects": {"local_snapshot": "DONE", "reference_published": "NOT_DONE",
                    "reference_published_why": "创建需要单独授权（§12 REF01 后置）",
                    "understanding": "NOT_DONE",
                    "understanding_why": "没有进入 understanding 的任何结论",
                    "git_metadata": "DONE"},
        "discovery_edges": [{"found_via": "user_named", "at": "2026-09-23",
                             "note": "用户点名这个题名"}],
    }
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(document.get(key), dict):
            document[key] = {**document[key], **value}
        else:
            document[key] = value
    return document


def a_note(**over) -> dict:
    note = {
        "title": TITLE,
        "original_author": "Eric Provencher / OpenAI Developers",
        "original_entry": URL,
        "saved_because": "用户 2026-09-23 点名，用途是与本仓现有 Skill 指引比较",
        "source_points": [
            {"point": "主张更强的模型需要更少的手把手指令", "from": "原文引言与结论段"},
            {"point": "建议标题/描述短而不含糊、渐进披露", "from": "原文 §… 小节"},
        ],
        "applicability": "适用范围：外部参考。未验证：本仓哪些 Skill 适用尚未评估",
        "related": ["serveros/intake"],
        "our_annotation": "与现有指引的关系需要单独比较，不是直接照做",
        "last_checked": "2026-09-23 抓到，未复核",
        "detail": {"source_ids": ["openai-astra-rethinking-skills-and-prompts"],
                   "content_sha256": "5d81cdb0" * 8,
                   "parser_version": "none (curl + markdown)",
                   "page_kind": "ARTICLE"},
    }
    note.update(over)
    return note


# ---------------------------------------------------------------------------
# §4 捕获质量
# ---------------------------------------------------------------------------
class TestTheFourQuestionsAreAskedApartAndAnsweredApart:
    def test_the_questions_are_section_4s_own_four(self):
        assert [q[0] for q in reference.SHELL_QUESTIONS] == [
            "login_shell", "captcha", "script_shell", "truncated_body"]

    def test_every_question_states_which_section_it_comes_from(self):
        for _, source, markers in reference.SHELL_QUESTIONS:
            assert "§4" in source
            assert markers, "一条没有标记的问题永远只能是 UNCHECKED"

    def test_all_four_evidenced_absent_is_captured(self):
        document = a_manifest(page_kind={
            "state": "ARTICLE", "shell_markers_found": [],
            "checked_markers": list(CHECKED) + ["read the rest"]})
        quality = reference.page_quality(document)
        assert quality["state"] == "CAPTURED"
        assert {q["state"] for q in quality["facts"]["questions"]} == {
            "EVIDENCED_ABSENT"}

    def test_a_found_login_shell_is_not_a_capture_however_good_the_status_is(self):
        document = a_manifest(page_kind={"shell_markers_found": ["Sign in"]})
        quality = reference.page_quality(document)
        assert quality["state"] == "NOT_CAPTURED"
        assert document["fetch"]["http_status"] == 200, (
            "这份 manifest 的 status 是 200，而 §4 说的正是 200 不改变这件事")
        login = next(q for q in quality["facts"]["questions"]
                     if q["id"] == "login_shell")
        assert login["state"] == "FOUND"
        assert "Sign in" in login["why"]

    def test_an_unasked_question_is_undecidable_and_not_an_absence(self):
        """这一条是这个模块存在的全部理由。"""
        document = a_manifest()
        quality = reference.page_quality(document)
        assert quality["state"] == "UNDECIDABLE"
        truncation = next(q for q in quality["facts"]["questions"]
                          if q["id"] == "truncated_body")
        assert truncation["state"] == "UNCHECKED"
        assert "没人问过" in truncation["why"]
        assert truncation["markers_asked"] == []
        assert truncation["markers_hit"] == []

    def test_every_question_asked_and_none_found_is_captured(self):
        document = a_manifest(page_kind={
            "state": "ARTICLE", "shell_markers_found": [],
            "checked_markers": list(CHECKED) + ["read the rest"]})
        assert reference.page_quality(document)["state"] == "CAPTURED"

    def test_nothing_checked_at_all_says_the_zero_over_zero_out_loud(self):
        document = a_manifest(page_kind={"state": "ARTICLE",
                                         "shell_markers_found": [],
                                         "checked_markers": []})
        quality = reference.page_quality(document)
        assert quality["state"] == "UNDECIDABLE"
        assert "0/0" in quality["why"][0]
        assert quality["facts"]["markers_checked"] == 0
        assert quality["facts"]["markers_found"] == 0

    def test_a_missing_page_kind_is_undecidable_rather_than_assumed(self):
        document = a_manifest()
        del document["page_kind"]
        quality = reference.page_quality(document)
        assert quality["state"] == "UNDECIDABLE"
        assert quality["facts"]["collector_label"] is None

    def test_the_collectors_label_is_reported_beside_and_never_decides(self):
        document = a_manifest()
        quality = reference.page_quality(document)
        assert quality["facts"]["collector_label"] == "ARTICLE"
        assert quality["state"] == "UNDECIDABLE", (
            "标签是 ARTICLE，判定必须仍然只看证据")

    def test_a_label_that_exceeds_its_evidence_is_named(self):
        document = a_manifest()
        exceeds = reference.page_quality(document)["facts"]["label_exceeds_evidence"]
        assert exceeds is not None
        assert "ARTICLE" in exceeds and "UNDECIDABLE" in exceeds

    def test_a_label_that_agrees_is_not_flagged(self):
        document = a_manifest(page_kind={
            "state": "ARTICLE", "shell_markers_found": [],
            "checked_markers": list(CHECKED) + ["read the rest"]})
        assert reference.page_quality(document)["facts"]["label_exceeds_evidence"] is None

    def test_a_label_saying_shell_is_not_flagged_as_exceeding(self):
        document = a_manifest(page_kind={"state": "SHELL",
                                         "shell_markers_found": ["captcha"]})
        facts = reference.page_quality(document)["facts"]
        assert facts["label_exceeds_evidence"] is None

    def test_markers_match_case_insensitively_so_one_spelling_is_enough(self):
        document = a_manifest(page_kind={"shell_markers_found": [],
                                         "checked_markers": ["Sign In", "CAPTCHA",
                                                             "Enable JavaScript",
                                                             "Read More"]})
        assert reference.page_quality(document)["state"] == "CAPTURED"

    def test_every_quality_fact_has_a_named_producer(self):
        facts = reference.page_quality(a_manifest())["facts"]
        assert set(facts) == set(reference.QUALITY_PRODUCED)

    def test_an_unknown_quality_fact_is_refused_rather_than_emitted(self):
        with pytest.raises(reference.Refused):
            reference.no_unsourced({"invented": 1}, reference.QUALITY_PRODUCED,
                                   "quality")


# ---------------------------------------------------------------------------
# §11 效果分层
# ---------------------------------------------------------------------------
class TestNoEffectImpliesAnother:
    def test_every_effect_carries_what_it_does_not_imply(self):
        layers = reference.read_effects(a_manifest())["layers"]
        assert set(layers) == set(reference.EFFECTS)
        for sentence in layers.values():
            assert "§" in sentence and "不" in sentence

    def test_the_states_are_the_manifests_own_and_nothing_is_derived(self):
        states = reference.read_effects(a_manifest())["states"]
        assert states == {"local_snapshot": "DONE",
                          "reference_published": "NOT_DONE",
                          "understanding": "NOT_DONE", "git_metadata": "DONE"}

    def test_a_done_snapshot_refuses_the_conclusion_it_was_published(self):
        refused = reference.read_effects(a_manifest())["refused_conclusions"]
        local = next(r for r in refused if "local_snapshot" in r["conclusion"])
        assert "存进飞书" in local["why"]

    def test_only_a_done_effect_gets_the_layering_refusal(self):
        """一条没有完成的阶段不会被说成「它不蕴含别的」——那是在给一个不存在的
        事实配一句免责。分层句只挂在真的发生了的事情旁边。"""
        effects = reference.read_effects(a_manifest())
        done = {name for name, state in effects["states"].items() if state == "DONE"}
        assert done == {"local_snapshot", "git_metadata"}
        for refusal in effects["refused_conclusions"]:
            if "排队" in refusal["conclusion"]:
                continue
            assert any(name in refusal["conclusion"] for name in done), (
                f"分层句挂在一个没有完成的阶段上：{refusal['conclusion']}")
        assert len([r for r in effects["refused_conclusions"]
                    if "排队" not in r["conclusion"]]) == len(done)

    def test_an_effect_with_no_state_is_undeclared_and_not_queued(self):
        document = a_manifest()
        del document["effects"]["understanding"]
        del document["effects"]["understanding_why"]
        effects = reference.read_effects(document)
        assert effects["states"]["understanding"] == "UNDECLARED"
        queued = next(r for r in effects["refused_conclusions"]
                      if "排队" in r["conclusion"])
        assert "不存在的下一consumer不叫排队" in queued["why"]

    def test_a_not_done_keeps_the_reason_the_collector_wrote(self):
        effects = reference.read_effects(a_manifest())
        assert effects["reasons"]["reference_published"] == \
            "创建需要单独授权（§12 REF01 后置）"

    def test_a_not_done_with_no_reason_has_an_empty_reason_not_a_sentence(self):
        document = a_manifest()
        del document["effects"]["git_metadata"]
        effects = reference.read_effects(document)
        assert effects["states"]["git_metadata"] == "UNDECLARED"
        assert effects["reasons"]["git_metadata"] == ""

    def test_the_layering_names_section_11_and_section_5(self):
        layers = reference.read_effects(a_manifest())["layers"]
        assert "§11" in layers["local_snapshot"]
        assert "§11" in layers["reference_published"]
        assert "§5" in layers["understanding"]

    def test_no_effect_block_is_an_undeclared_four(self):
        effects = reference.read_effects({})
        assert set(effects["states"].values()) == {"UNDECLARED"}


# ---------------------------------------------------------------------------
# §4 发现出处
# ---------------------------------------------------------------------------
class TestADiscoveryEdgeIsNotAnIdentity:
    def test_two_edges_on_one_identity_are_one_object_with_two_edges(self):
        edges = [{"found_via": "user_named", "identity": {"provider": "web",
                                                          "url": URL}},
                 {"found_via": "browser", "identity": {"provider": "web",
                                                       "url": URL}}]
        found = reference.discoveries(edges, policy.key_of)
        assert found["object_count"] == 1 and found["edge_count"] == 2

    def test_the_same_title_on_two_addresses_is_two_objects(self):
        edges = [{"found_via": "user_named", "title": TITLE,
                  "identity": {"provider": "web", "url": URL}},
                 {"found_via": "browser", "title": TITLE,
                  "identity": {"provider": "web", "url": OTHER_URL}}]
        found = reference.discoveries(edges, policy.key_of)
        assert found["object_count"] == 2
        assert found["titles_shared_by_several_objects"] == {TITLE: sorted(
            [f"web:doc:{URL}", f"web:doc:{OTHER_URL}"])}

    def test_a_title_only_edge_is_refused_rather_than_grouped_by_title(self):
        with pytest.raises(reference.Refused) as exc:
            reference.discoveries([{"found_via": "user_named", "title": TITLE,
                                    "identity": {"title": TITLE}}],
                                  policy.key_of)
        assert "相同标题不是同对象" in str(exc.value)

    def test_the_key_is_the_one_source_policy_composes(self):
        document = a_manifest()
        found = reference.discoveries(
            [{"found_via": "user_named",
              "identity": reference.identity_of(document)}], policy.key_of)
        assert found["objects"][0]["key"] == policy.key_of(
            policy.identity_of(document, "web_capture", "x/manifest.json"))

    def test_a_key_function_is_required_and_never_defaulted(self):
        with pytest.raises(reference.Refused):
            reference.discoveries([], None)

    def test_the_module_says_titles_are_not_the_key(self):
        assert "标题从来不是键" not in reference.discoveries(
            [], policy.key_of)["_why_titles_are_not_here"]
        assert "provider + 稳定标识" in reference.discoveries(
            [], policy.key_of)["_why_titles_are_not_here"]


# ---------------------------------------------------------------------------
# §5 一份笔记的首层
# ---------------------------------------------------------------------------
class TestANoteHasAFirstLayerAndAFoldedLayer:
    def test_a_complete_note_passes(self):
        checked = reference.check_note(a_note())
        assert checked["checked"] is True
        assert checked["first_layer"] == list(reference.NOTE_FIRST_LAYER)

    def test_the_machine_detail_is_reported_as_folded(self):
        checked = reference.check_note(a_note())
        assert "content_sha256" in checked["folded"]
        assert "parser_version" in checked["folded"]

    def test_missing_a_first_layer_field_names_it_and_what_it_is(self):
        note = a_note()
        del note["saved_because"]
        with pytest.raises(reference.Refused) as exc:
            reference.check_note(note)
        assert "`saved_because`（保存理由）" in str(exc.value)

    def test_a_hash_in_the_first_layer_is_refused(self):
        with pytest.raises(reference.Refused) as exc:
            reference.check_note(a_note(content_sha256="5d81cdb0" * 8))
        assert "不让首屏变台账" in str(exc.value)

    def test_a_point_without_a_source_is_refused(self):
        with pytest.raises(reference.Refused) as exc:
            reference.check_note(a_note(source_points=[{"point": "模型建议这样做"}]))
        assert "不能把模型生成的采用建议写成官方原文" in str(exc.value)

    def test_an_empty_point_list_is_refused(self):
        with pytest.raises(reference.Refused):
            reference.check_note(a_note(source_points=[]))

    def test_adopted_without_a_person_is_refused(self):
        with pytest.raises(reference.Refused) as exc:
            reference.check_note(a_note(proposed_change={
                "status": "ADOPTED", "what": "改 AGENTS.md"}))
        assert "收藏行为本身不批准" in str(exc.value)

    def test_adopted_with_a_person_is_allowed(self):
        checked = reference.check_note(a_note(proposed_change={
            "status": "ADOPTED", "what": "改 AGENTS.md",
            "decided_by_user": "2026-09-23"}))
        assert checked["proposal"] == "ADOPTED"

    def test_a_proposal_state_outside_the_vocabulary_is_refused(self):
        with pytest.raises(reference.Refused):
            reference.check_note(a_note(proposed_change={"status": "DONE"}))

    def test_a_full_text_snapshot_without_a_version_is_refused(self):
        with pytest.raises(reference.Refused) as exc:
            reference.check_note(a_note(full_text_snapshot={"body": "…"}))
        assert "源文快照" in str(exc.value)

    def test_a_full_text_snapshot_with_a_version_passes(self):
        checked = reference.check_note(a_note(full_text_snapshot={
            "body": "…", "version_label": "源文快照：2026-09-23 所见版本"}))
        assert checked["checked"] is True

    def test_an_unnamed_first_layer_field_is_refused(self):
        with pytest.raises(reference.Refused) as exc:
            reference.check_note(a_note(summary="我自己写的一段"))
        assert "summary" in str(exc.value)

    def test_a_non_object_note_is_malformed_not_refused(self):
        with pytest.raises(reference.Malformed):
            reference.check_note(["not", "a", "note"])


# ---------------------------------------------------------------------------
# §11 撤销
# ---------------------------------------------------------------------------
class TestRevocationAndNotFoundAreDifferentReadings:
    def test_revoked_stops_access_and_restricts_recall_and_propagation(self):
        effects = reference.revocation_effects("REVOKED")
        assert effects["new_access"] == "STOPPED"
        assert effects["recall"] == "RESTRICTED"
        assert effects["propagation"] == "RESTRICTED"
        assert effects["mark_affected"] is True

    def test_a_not_found_is_not_a_revocation(self):
        effects = reference.revocation_effects("NOT_FOUND")
        assert effects["new_access"] == "STOPPED"
        assert effects["recall"] == "UNRESTRICTED"
        assert effects["propagation"] == "UNRESTRICTED"
        assert effects["mark_affected"] is False

    def test_a_not_found_says_why_it_is_not_a_revocation(self):
        why = " ".join(reference.revocation_effects("NOT_FOUND")["why"])
        assert "来源404不自动把自己的批注删除" in why
        assert "可达性" in why

    def test_nor_the_revoked_reading_names_the_old_hash_rule(self):
        why = " ".join(reference.revocation_effects("REVOKED")["why"])
        assert "旧hash不允许绕撤权" in why

    def test_neither_state_lets_a_snapshot_restore_access(self):
        for state in ("REVOKED", "NOT_FOUND"):
            assert reference.revocation_effects(state)["snapshot_restores_access"] is False

    def test_neither_state_deletes_our_own_annotation(self):
        for state in reference.REVOCATION_STATES:
            assert reference.revocation_effects(state)["keeps_own_annotation"] is True

    def test_active_restricts_nothing(self):
        effects = reference.revocation_effects("ACTIVE")
        assert effects["recall"] == "UNRESTRICTED"
        assert effects["new_access"] == "ALLOWED"

    def test_a_state_outside_the_vocabulary_is_refused(self):
        with pytest.raises(reference.Refused):
            reference.revocation_effects("DELETED")


# ---------------------------------------------------------------------------
# §4 外部正文不产生执行许可
# ---------------------------------------------------------------------------
class TestAnExternalBodyAuthorisesNothing:
    def test_every_named_verb_gets_a_no_on_every_source_kind(self):
        for kind in ("external_body", "collected_note"):
            for action in reference.NEVER_AUTHORIZING:
                assert reference.permits(action, kind)["permitted"] is False

    def test_the_verbs_are_the_ones_section_4_names(self):
        assert {"run_script", "install_skill", "modify_agents_md"} <= set(
            reference.NEVER_AUTHORIZING)

    def test_the_answer_names_section_4_and_section_5(self):
        why = reference.permits("modify_agents_md")["why"]
        assert "§4" in why and "§5" in why

    def test_an_unnamed_verb_is_refused_rather_than_answered_no(self):
        with pytest.raises(reference.Refused) as exc:
            reference.permits("delete_everything")
        assert "不回答不认识的动词" in str(exc.value)

    def test_a_source_kind_this_module_does_not_answer_about_is_refused(self):
        with pytest.raises(reference.Refused):
            reference.permits("run_script", "our_canon")

    def test_the_only_answer_this_module_can_give_is_no(self):
        assert reference.PERMISSION_ANSWER == "no"
        source = (REPO_ROOT / "intake" / "external_reference.py").read_text(
            encoding="utf-8")
        assert '"permitted": True' not in source

    def test_a_filename_cannot_name_a_write_path(self):
        for bad in ("/etc/passwd", "a/b", "..", "~/notes", "a\0b"):
            with pytest.raises(reference.Refused):
                reference.local_segment(bad)

    def test_a_plain_name_is_returned_unchanged(self):
        assert reference.local_segment("rethinking-skills-and-prompts") == \
            "rethinking-skills-and-prompts"


# ---------------------------------------------------------------------------
# 活体树
# ---------------------------------------------------------------------------
class TestTheLiveTree:
    @pytest.mark.skipif(not readable_dir(LIVE_RECORDS), reason="records 检出不在本机")
    def test_the_denominator_adds_up(self):
        document = reference.read_root(
            reference.raw_root(LIVE_RECORDS), policy.key_of)
        assert document["manifests"] == (document["not_reference_objects"]
                                        + document["unreadable"]
                                        + len(document["objects"]))
        assert document["manifests"] > 0

    @pytest.mark.skipif(not readable_dir(LIVE_RECORDS), reason="records 检出不在本机")
    def test_a_captured_object_has_no_unasked_question(self):
        document = reference.read_root(
            reference.raw_root(LIVE_RECORDS), policy.key_of)
        for record in document["objects"]:
            questions = record["quality"]["facts"]["questions"]
            if record["quality"]["state"] == "CAPTURED":
                assert all(q["state"] != "UNCHECKED" for q in questions)

    @pytest.mark.skipif(not readable_dir(LIVE_RECORDS), reason="records 检出不在本机")
    def test_a_label_meeting_a_weaker_reading_is_always_flagged(self):
        document = reference.read_root(
            reference.raw_root(LIVE_RECORDS), policy.key_of)
        for record in document["objects"]:
            facts = record["quality"]["facts"]
            if facts["collector_label"] and record["quality"]["state"] != "CAPTURED":
                assert facts["label_exceeds_evidence"] is not None

    @pytest.mark.skipif(not readable_dir(LIVE_RECORDS), reason="records 检出不在本机")
    def test_every_reference_object_can_be_rendered(self):
        document = reference.read_root(
            reference.raw_root(LIVE_RECORDS), policy.key_of)
        assert reference.render(document)


class TestTheShapeQuestionHasOneAnswer:
    """「这份 manifest 是不是参考对象」由 `capture_coverage` 定，不由这里再定一次。"""

    def test_the_reference_test_is_exactly_the_web_capture_shape(self):
        web = a_manifest()
        chat = {"chat": {"chat_id": "oc_x"}, "sync": {"msg_count": 1}}
        document = {"url": URL, "content_sha256": "a" * 64, "type": "docx"}
        for manifest, shape in ((web, "web_capture"), (chat, "chat_capture"),
                                (document, "document_capture")):
            assert capture.shape_of(manifest) == shape
            assert reference.is_reference_object(manifest) is (shape == "web_capture")

    def test_the_identity_keys_are_the_same_two_source_policy_uses(self):
        expected = policy.identity_of(a_manifest(), "web_capture", "w/manifest.json")
        observed = reference.identity_of(a_manifest())
        assert observed == {"provider": expected["provider"], "url": expected["url"]}
        assert observed["url"] == expected["url"]

    def test_a_page_with_no_address_composes_an_identity_with_a_null(self):
        document = a_manifest(source={"provider": "web", "requested_url": None})
        assert reference.identity_of(document)["url"] is None
        assert policy.key_of(reference.identity_of(document)) is None


# ---------------------------------------------------------------------------
# 读一份真实形状的 manifest
# ---------------------------------------------------------------------------
class TestTheLiveShapedManifest:
    def test_the_real_marker_list_reads_as_three_answered_and_one_unasked(self):
        quality = reference.page_quality(a_manifest())
        states = {q["id"]: q["state"] for q in quality["facts"]["questions"]}
        assert states == {"login_shell": "EVIDENCED_ABSENT",
                          "captcha": "EVIDENCED_ABSENT",
                          "script_shell": "EVIDENCED_ABSENT",
                          "truncated_body": "UNCHECKED"}

    def test_that_is_why_the_overall_reading_is_undecidable(self):
        assert reference.page_quality(a_manifest())["state"] == "UNDECIDABLE"

    def test_the_summary_counts_per_question_and_not_per_object(self):
        summary = reference.summarize([
            {"readable": True, "path": "w/manifest.json",
             "quality": reference.page_quality(a_manifest()),
             "effects": reference.read_effects(a_manifest())}])
        assert summary["denominator"] == 1
        assert summary["quality"] == {"UNDECIDABLE": 1}
        assert summary["effects"]["local_snapshot"] == {"DONE": 1}

    def test_a_json_file_that_is_not_a_manifest_is_not_a_reference_object(self, tmp_path):
        root = tmp_path / "raw"
        (root / "web" / "x").mkdir(parents=True)
        (root / "web" / "x" / "manifest.json").write_text(
            json.dumps({"nothing": "here"}), encoding="utf-8")
        document = reference.read_root(root, policy.key_of)
        assert document["manifests"] == 1
        assert document["objects"] == []
        assert document["not_reference_objects"] == 1

    def test_an_unreadable_manifest_is_counted_and_does_not_abort_the_walk(self, tmp_path):
        root = tmp_path / "raw"
        (root / "web" / "x").mkdir(parents=True)
        (root / "web" / "x" / "manifest.json").write_text("{not json",
                                                          encoding="utf-8")
        document = reference.read_root(root, policy.key_of)
        assert document["unreadable"] == 1 and document["objects"] == []

    def test_an_absent_root_is_refused_rather_than_read_as_empty(self, tmp_path):
        with pytest.raises(reference.Refused):
            reference.read_root(tmp_path / "nope", policy.key_of)

    def test_a_reference_object_in_the_tree_carries_its_discoveries(self, tmp_path):
        root = tmp_path / "raw"
        (root / "web" / "x").mkdir(parents=True)
        (root / "web" / "x" / "manifest.json").write_text(
            json.dumps(a_manifest()), encoding="utf-8")
        document = reference.read_root(root, policy.key_of)
        found = document["objects"][0]["discoveries"]
        assert found["objects"][0]["key"] == f"web:doc:{URL}"
        assert found["objects"][0]["edges"][0]["found_via"] == "user_named"
