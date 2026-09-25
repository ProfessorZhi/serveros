"""P4 — 发布一条正文时，五个后果各记一笔。

这一组守的是「正文成功、媒体失败，下一次只补媒体」这类事实能被表示，以及
几条不能靠记性维持的规则：

- base 动了就不许写（不是「警告一下」），而且**不许调 writer**；
- patch 的范围是命名的那一节，范围之外的任何块变了都算失败 —— 人改就活在这条；
- 权限不扩大不是写在注释里的政策：这个模块**没有**能扩大受众的动词，请求里
  出现受众字段就按名拒绝；而正文写完之后受众指纹变了，算失败；
- 不认识的东西不过 Markdown 往返：范围里有图片/富表就拒绝，不是序列化再解析；
- 上一次中断的那条边不许盲重建：先读回，读不回就不写。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(
        "serveros_intake_" + name, REPO_ROOT / "intake" / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules["serveros_intake_" + name] = module
    spec.loader.exec_module(module)
    return module


publish = _load("document_publish", "document_publish.py")


# ────────────────────────────────────────────────────────────────────────────
# 一台假文档。读与写都由它来，纪律由模块来。
# ────────────────────────────────────────────────────────────────────────────

HEADING = "三、已完成"


class FakeDoc:
    """一篇块列表构成的文档：`fetch` 读它，body writer 改它。"""

    def __init__(self, blocks=None, revision=1684, audience=("me", "reviewer")):
        self.blocks = blocks if blocks is not None else [
            {"id": "b0", "type": "heading1", "text": "一、背景"},
            {"id": "b1", "type": "text", "text": "背景正文"},
            {"id": "b2", "type": "heading2", "text": HEADING},
            {"id": "b3", "type": "text", "text": "旧的一节正文"},
            {"id": "b4", "type": "heading2", "text": "四、待办"},
            {"id": "b5", "type": "text", "text": "人写的待办，谁都不许动"},
        ]
        self.revision = revision
        self.audience = None if audience is None else list(audience)
        self.fetches = 0
        self.writes: list[tuple] = []

    def fetch(self, document, scope=None):
        self.fetches += 1
        return {"revision": self.revision,
                "blocks": [dict(block) for block in self.blocks],
                "audience": None if self.audience is None else list(self.audience)}

    def _index(self, block_id):
        return next(index for index, block in enumerate(self.blocks)
                    if block["id"] == block_id)

    def body_writer(self, document, section, content, revision):
        """Write the section's body, keeping its heading where it is.

        两个形状，与 transport 一致：有正文就整段替换，没有就插在标题之后。标题
        从来不在这两种形状里 —— 一个把标题替换掉的 patch 会把下一次要找的锚点
        挪走，而这件事要到下一轮才表现为「找不到标题」。
        """
        self.writes.append((document, dict(section), content, revision))
        self.revision += 1
        fresh = [{"id": f"new{len(self.writes)}-{index}", "type": "text",
                  "text": text}
                 for index, text in enumerate(paragraphs(content))]
        if section["body_ids"]:
            head = self._index(section["body_ids"][0])
            tail = self._index(section["body_ids"][-1])
            self.blocks = self.blocks[:head] + fresh + self.blocks[tail + 1:]
        else:
            head = self._index(section["heading_id"])
            self.blocks = self.blocks[:head + 1] + fresh + self.blocks[head + 1:]
        return {"revision": self.revision}


def paragraphs(content: str) -> list[str]:
    """一段一个块，与真服务存法一致。

    不是把所有段落压进一个块：压进去之后，「读回比对分不清段落边界」这类缺陷
    就永远测不出来 —— 而真机上第一次写就撞上了它，读回说文档不对，文档是对的。
    """
    import re
    pieces = [re.sub(r"<[^>]+>", "", chunk).strip()
              for chunk in re.split(r"(?=<p>)|(?=<h1>)|(?=<h2>)", content)]
    return [piece for piece in pieces if piece]


CONTENT = "<p>新的一节正文</p>"


def a_request(doc=None, **over):
    request = {"schema": publish.REQUEST_SCHEMA, "document": doc or "doc-a",
               "section": "completed", "heading": HEADING,
               "base_revision": 1684, "change_class": "PATCH",
               "content": CONTENT}
    request.update(over)
    return request


def a_contract(**over):
    contract = {"contract": "CHECKED", "reasons": [],
                "delivery": {"state": "CONSISTENT", "why": "relation OWNED "
                                                           "agrees with the profile"},
                "write_policy": {"tiers": {"PATCH": "preview",
                                           "REFINE": "preview",
                                           "RESTRUCTURE": "approval"}}}
    contract.update(over)
    return contract


def a_run(tmp_path, doc=None, request=None, contract=None, writers=None,
          gate_decision=None, live_gate=None, approvals=None):
    doc = doc if doc is not None else FakeDoc()
    request = request if request is not None else a_request()
    contract = contract if contract is not None else a_contract()
    writers = writers if writers is not None else {"body": doc.body_writer}
    return doc, publish.apply(
        request, contract,
        gate_decision=(gate_decision if gate_decision is not None
                       else {"decision": "preview", "reason": "policy=preview"}),
        fetch=doc.fetch, writers=writers,
        receipts_dir=tmp_path / "receipts", approvals=approvals,
        gate=live_gate)


def rows(report):
    return {row["effect"]: row for row in report["effects"]}


# ────────────────────────────────────────────────────────────────────────────

class TestWhatTheRequestCannotAskFor:
    def test_a_request_that_names_an_audience_is_refused_by_name(self):
        refused = publish.check_request(a_request(audience=["me", "someone"]))
        assert len(refused) == 1 and refused[0].startswith("audience:")
        assert "no verb" in refused[0]

    def test_markdown_is_refused_rather_than_warned_about(self):
        refused = publish.check_request(a_request(doc_format="markdown"))
        assert any("doc_format" in one for one in refused)

    def test_no_base_revision_is_a_refusal_not_a_default_to_latest(self):
        refused = publish.check_request(a_request(base_revision=None))
        assert any("base_revision" in one for one in refused)

    def test_a_missing_section_key_is_refused_because_scope_is_the_point(self):
        request = a_request()
        del request["section"]
        assert any(one.startswith("section:") for one in publish.check_request(request))

    def test_an_unknown_change_class_is_refused_rather_than_tiered(self):
        refused = publish.check_request(a_request(change_class="REWRITE"))
        assert any("change_class" in one for one in refused)

    def test_every_refusal_is_reported_not_just_the_first(self):
        request = a_request()
        del request["document"]
        del request["content"]
        assert len(publish.check_request(request)) == 2


class TestTheRunDoesNotStartUnlessTheContractHolds:
    def test_a_corrupt_contract_refuses_and_names_the_reasons(self, tmp_path):
        contract = a_contract(contract="DYNAMIC_FIELDS_IN_PROFILE",
                              reasons=["last_verified", "sections[0].block_id"])
        doc, report = a_run(tmp_path, contract=contract)
        assert report["planned"] is False
        assert "DYNAMIC_FIELDS_IN_PROFILE" in report["refusals"][0]
        assert "last_verified" in report["refusals"][0]
        assert doc.writes == []          # 契约不该读的时候，一次都没写过

    def test_an_inconsistent_delivery_refuses_and_carries_its_why(self, tmp_path):
        contract = a_contract(delivery={"state": "INCONSISTENT",
                                        "why": "relation is FOLLOW_MIRROR and "
                                               "the profile says standing: "
                                               "CANONICAL"})
        _, report = a_run(tmp_path, contract=contract)
        assert any("FOLLOW_MIRROR" in one for one in report["refusals"])

    def test_writing_a_mirror_needs_saying_so(self, tmp_path):
        # 副本的正文是抄来的：在这里改，就是把「一份文档两个物化」变成两份。
        # 默认不问，但也不默认允许。
        contract = a_contract(delivery={
            "state": "CONSISTENT", "form": "FOLLOW_MIRROR",
            "why": "relation FOLLOW_MIRROR agrees with the profile"},
            copied_from="https://example.invalid/wiki/source")
        doc, report = a_run(tmp_path, contract=contract)
        assert report["planned"] is False
        assert any(one.startswith("mirror_ack:") for one in report["refusals"])
        assert "https://example.invalid/wiki/source" in report["refusals"][0]
        assert doc.writes == []

    def test_a_mirror_write_that_was_acknowledged_runs(self, tmp_path):
        contract = a_contract(delivery={
            "state": "CONSISTENT", "form": "FOLLOW_MIRROR",
            "why": "relation FOLLOW_MIRROR agrees with the profile"},
            copied_from="https://example.invalid/wiki/source")
        doc, report = a_run(tmp_path, contract=contract,
                            request=a_request(mirror_ack="分叉是有意的"))
        assert report["planned"] is True

    def test_a_blocked_gate_stops_the_run_and_carries_the_abilitys_own_reason(self, tmp_path):
        doc, report = a_run(tmp_path, gate_decision={"decision": "blocked",
                                            "reason": "no_authority: 该节要求事实核实"})
        assert report["planned"] is False
        assert any("no_authority" in one for one in report["refusals"])
        assert doc.writes == []

    def test_the_approval_tier_needs_an_approval_and_says_so(self, tmp_path):
        request = a_request(change_class="RESTRUCTURE")
        doc, report = a_run(tmp_path, request=request,
                            gate_decision={"decision": "approval",
                                           "reason": "结构重写必须人工批准"})
        assert report["planned"] is False
        assert any("approval" in one for one in report["refusals"])

    def test_an_approval_that_is_present_lets_the_run_plan(self, tmp_path):
        request = a_request(change_class="RESTRUCTURE")
        doc, report = a_run(tmp_path, request=request, approvals={"RESTRUCTURE": "human-1"},
                            gate_decision={"decision": "approval",
                                           "reason": "结构重写必须人工批准"})
        assert report["planned"] is True


class TestTheBodyWrite:
    def test_a_happy_path_writes_reads_back_and_settles(self, tmp_path):
        doc, report = a_run(tmp_path)
        body = rows(report)["body"]
        assert body["state"] == "OK"
        assert len(doc.writes) == 1
        assert report["settled"] is True

    def test_the_revision_id_handed_to_the_service_is_the_one_we_read(self, tmp_path):
        # 服务端的乐观并发检查是这里唯一的并发控制 —— 不是本地锁。
        doc, _ = a_run(tmp_path)
        assert doc.writes[0][3] == 1684

    def test_a_moved_base_is_refused_and_the_writer_is_never_called(self, tmp_path):
        doc = FakeDoc(revision=1720)          # 有人在我们读完之后编辑了
        doc, report = a_run(tmp_path, doc=doc)
        body = rows(report)["body"]
        assert body["state"] == "REFUSED"
        assert "1684 -> 1720" in body["why"]
        assert "do not write this one" in body["why"]
        assert doc.writes == []

    def test_a_heading_that_is_not_there_is_refused(self, tmp_path):
        request = a_request(heading="九、并不存在")
        doc, report = a_run(tmp_path, request=request)
        assert rows(report)["body"]["state"] == "REFUSED"
        assert doc.writes == []

    def test_two_headings_with_one_name_are_refused_not_guessed(self, tmp_path):
        doc = FakeDoc(blocks=[
            {"id": "h1", "type": "heading2", "text": "三、已完成"},
            {"id": "t1", "type": "text", "text": "第一处"},
            {"id": "h2", "type": "heading2", "text": "三、已完成"},
            {"id": "t2", "type": "text", "text": "第二处"},
        ])
        doc, report = a_run(tmp_path, doc=doc)
        assert rows(report)["body"]["state"] == "REFUSED"
        assert "2 headings match" in rows(report)["body"]["why"]
        assert doc.writes == []

    def test_an_image_inside_the_range_is_refused_not_round_tripped(self, tmp_path):
        doc = FakeDoc(blocks=[
            {"id": "b2", "type": "heading2", "text": HEADING},
            {"id": "b3", "type": "image", "text": ""},
        ])
        doc, report = a_run(tmp_path, doc=doc)
        body = rows(report)["body"]
        assert body["state"] == "REFUSED"
        assert "b3:image" in body["why"]
        assert doc.writes == []

    def test_a_rich_block_outside_the_range_is_protected_by_the_digest(self, tmp_path):
        # 范围之外的富内容不是被「不敢动」，是被**证明**没动。
        # 一节的范围从它的标题到下一个同级或更高级的标题 —— 这才是 patch 的边界，
        # 也正是人改能活下来的原因。
        doc = FakeDoc(blocks=[
            {"id": "b0", "type": "table", "text": "一张富表，在我们要改的那节之前"},
            {"id": "b1", "type": "heading2", "text": HEADING},
            {"id": "b2", "type": "text", "text": "旧的一节正文"},
            {"id": "b3", "type": "heading2", "text": "四、待办"},
            {"id": "b4", "type": "image", "text": ""},
        ])
        doc, report = a_run(tmp_path, doc=doc)
        assert rows(report)["body"]["state"] == "OK"
        assert [(b["id"], b["type"]) for b in doc.blocks
                if b["type"] in ("table", "image")] == [("b0", "table"), ("b4", "image")]

    def test_a_human_edit_outside_the_patch_range_survives(self, tmp_path):
        doc, report = a_run(tmp_path)
        assert rows(report)["body"]["state"] == "OK"
        assert any(block["text"] == "人写的待办，谁都不许动" for block in doc.blocks)

    def test_the_heading_is_not_in_the_patched_range_and_survives(self, tmp_path):
        # 标题是下游认这一节的锚点：patch 换掉它，就是挪走了下一次要找的东西。
        doc, report = a_run(tmp_path)
        assert rows(report)["body"]["state"] == "OK"
        assert [b["id"] for b in doc.blocks
                if b.get("text") == HEADING] == ["b2"]
        assert doc.writes[0][1]["heading_id"] == "b2"
        assert doc.writes[0][1]["body_ids"] == ["b3"]

    def test_a_section_with_no_body_is_filled_by_inserting_after_its_heading(self, tmp_path):
        doc = FakeDoc(blocks=[
            {"id": "b2", "type": "heading2", "text": HEADING},
            {"id": "b4", "type": "heading2", "text": "四、待办"},
        ])
        doc, report = a_run(tmp_path, doc=doc)
        assert rows(report)["body"]["state"] == "OK"
        assert doc.writes[0][1] == {"heading_id": "b2", "body_ids": []}
        assert doc.blocks[0] == {"id": "b2", "type": "heading2", "text": HEADING}

    def test_a_write_that_touched_an_outside_block_is_a_failure(self, tmp_path):
        doc = FakeDoc()

        def sloppy(document, section, content, revision):
            doc.blocks[5]["text"] = "顺手改了别人的一节"      # 越界
            return doc.body_writer(document, section, content, revision)

        doc, report = a_run(tmp_path, doc=doc, writers={"body": sloppy})
        body = rows(report)["body"]
        assert body["state"] == "FAILED"
        assert "outside the patched range changed" in body["why"]

    def test_a_write_that_widened_the_audience_is_a_failure(self, tmp_path):
        doc = FakeDoc()

        def widening(document, ids, content, revision):
            doc.audience = doc.audience + ["everyone"]
            return doc.body_writer(document, ids, content, revision)

        doc, report = a_run(tmp_path, doc=doc, writers={"body": widening})
        body = rows(report)["body"]
        assert body["state"] == "FAILED"
        assert "audience changed" in body["why"]

    def test_an_unobserved_audience_is_a_check_that_did_not_happen(self, tmp_path):
        # 「没人能读」与「我们没看」不能比较相等 —— 否则权限这条会在没做的
        # 情况下通过，比没有这条检查更坏。
        doc = FakeDoc(audience=None)
        doc, report = a_run(tmp_path, doc=doc)
        body = rows(report)["body"]
        assert body["state"] == "OK"           # 另外两条查过了
        assert body["audience_checked"] is False
        assert "受众未观测" in body["why"]

    def test_an_observed_and_unchanged_audience_says_it_was_checked(self, tmp_path):
        doc, report = a_run(tmp_path)
        body = rows(report)["body"]
        assert body["audience_checked"] is True
        assert "受众未观测" not in body["why"]

    def test_the_gate_is_asked_against_the_text_the_document_actually_holds(self, tmp_path):
        # 能力的三个保护里有两个是「与原文比计数」。拿空的 original 去问，它们
        # **空过** —— 一个靠「没看」满足的保护，比没有这条保护更坏，因为它报的是
        # 「已满足」。所以闸门在读到文档之后、拿着真原文才问。
        doc = FakeDoc(blocks=[
            {"id": "b2", "type": "heading2", "text": HEADING},
            {"id": "b3", "type": "text", "text": "~已完成的那一项~"},
        ])
        asked: list[str] = []

        def gate(original_text):
            asked.append(original_text)
            if "~已完成的那一项~" not in original_text:
                return {"decision": "preview", "reason": "空过"}
            return {"decision": "blocked",
                    "reason": "completed_item_removed: 完成标记在候选编辑中消失"}

        doc, report = a_run(tmp_path, doc=doc, live_gate=gate, request=a_request())
        body = rows(report)["body"]
        # 注意 plan 已经用传进来的 gate_decision 过了；这里问的是 apply 里那一次。
        assert body["state"] == "REFUSED"
        assert "completed_item_removed" in body["why"]
        assert doc.writes == []
        assert asked and "~已完成的那一项~" in asked[0]

    def test_a_gate_that_allows_against_the_live_text_lets_the_write_through(self, tmp_path):
        doc = FakeDoc()
        doc, report = a_run(tmp_path, doc=doc,
                            live_gate=lambda text: {"decision": "preview",
                                                    "reason": "policy=preview"})
        assert rows(report)["body"]["state"] == "OK"

    def test_a_write_that_did_not_land_is_a_failure_rather_than_a_pass(self, tmp_path):
        doc = FakeDoc()
        doc, report = a_run(tmp_path, doc=doc,
                            writers={"body": lambda *a: {"revision": 9}})
        body = rows(report)["body"]
        assert body["state"] == "FAILED"
        assert "does not match what was written" in body["why"]

    def test_the_same_intent_twice_is_one_write_not_two(self, tmp_path):
        doc, first = a_run(tmp_path)
        assert rows(first)["body"]["state"] == "OK"
        doc, second = a_run(tmp_path, doc=doc)
        body = rows(second)["body"]
        assert body["state"] == "ALREADY_EFFECTED"
        assert len(doc.writes) == 1          # 重复不双份

    def test_a_different_intent_is_not_the_same_intent(self, tmp_path):
        doc, _ = a_run(tmp_path)
        doc, second = a_run(tmp_path, doc=doc,
                            request=a_request(content="<p>换一段</p>",
                                              base_revision=1685))
        assert rows(second)["body"]["state"] == "OK"
        assert len(doc.writes) == 2

    def test_a_rerun_whose_base_moved_still_writes_nothing(self, tmp_path):
        # 真机上量到的：同一段正文，中间别人改过文档（base 动了），重跑又写了一遍
        # —— 内容一字不差，却凭空多一个修订、块 id 全换，锚在这些块上的东西全断。
        # 「没事可做」比「base 动了」优先：base 陈旧只对**写**是问题，而没有写。
        doc, first = a_run(tmp_path)
        assert rows(first)["body"]["state"] == "OK"
        doc.blocks.insert(4, {"id": "human", "type": "text", "text": "别人插的一句"})
        doc.revision += 1                                    # 别人的编辑抬了 base
        doc, second = a_run(tmp_path, doc=doc,
                            request=a_request(base_revision=1684))
        body = rows(second)["body"]
        assert body["state"] == "ALREADY_EFFECTED"
        assert len(doc.writes) == 1
        assert [block["text"] for block in doc.blocks][4] == "别人插的一句"

    def test_a_stale_base_still_stops_a_write_that_is_still_to_make(self, tmp_path):
        doc = FakeDoc(revision=1690)
        doc, report = a_run(tmp_path, doc=doc)
        body = rows(report)["body"]
        assert body["state"] == "REFUSED"
        assert "base moved" in body["why"]
        assert doc.writes == []

    def test_the_intent_key_does_not_move_when_the_base_does(self):
        first = a_request()
        again = a_request(base_revision=9999)
        assert publish.intent_key("body", first) == publish.intent_key("body", again)
        moved = a_request(content="<p>换了内容</p>")
        assert publish.intent_key("body", first) != publish.intent_key("body", moved)

    def test_an_interrupted_attempt_whose_work_is_there_is_not_re_issued(self, tmp_path):
        # 上一次尝试没来得及闭合。规矩不是「先读回」这句话，而是：**看文档**。
        # 要的东西已经在那一节里了，就没有可写的；不在，才写。
        key = publish.intent_key("body", a_request())
        publish.write_receipt(tmp_path / "receipts", "doc-a", "body",
                              {"effect": "body", "intent": key,
                               "state": "UNKNOWN_OUTCOME",
                               "why": "attempt in progress or interrupted",
                               "at": "2026-09-25T00:00:00+00:00"})
        already = FakeDoc(blocks=[
            {"id": "b0", "type": "heading1", "text": "一、背景"},
            {"id": "b1", "type": "heading2", "text": HEADING},
            {"id": "b2", "type": "text", "text": "新的一节正文"},
        ])
        already, report = a_run(tmp_path, doc=already)
        body = rows(report)["body"]
        assert body["state"] == "ALREADY_EFFECTED"
        assert body["previous_interrupted"] is True
        assert already.writes == []

    def test_an_interrupted_attempt_whose_work_is_not_there_is_finished(self, tmp_path):
        key = publish.intent_key("body", a_request())
        publish.write_receipt(tmp_path / "receipts", "doc-a", "body",
                              {"effect": "body", "intent": key,
                               "state": "UNKNOWN_OUTCOME",
                               "why": "attempt in progress or interrupted",
                               "at": "2026-09-25T00:00:00+00:00"})
        doc = FakeDoc()
        doc, report = a_run(tmp_path, doc=doc)
        assert rows(report)["body"]["state"] == "OK"
        assert len(doc.writes) == 1


    def test_a_write_whose_readback_cannot_find_the_section_is_unknown_not_a_crash(self, tmp_path):
        # 这一条来自真实事故：patch 把节标题一起换掉之后，读回找不到那一节，
        # `find_section` 抛出去，整轮以 traceback 收场 —— 而正文其实已经写进去了。
        # 「写了但没验成」必须当成 UNKNOWN_OUTCOME 说出来，不能报成崩溃。
        class EatsItsAnchor(FakeDoc):
            def body_writer(self, document, section, content, revision):
                result = super().body_writer(document, section, content, revision)
                self.blocks = [block for block in self.blocks
                               if block["text"] != HEADING]
                return result

        doc = EatsItsAnchor()
        doc, report = a_run(tmp_path, doc=doc)
        body = rows(report)["body"]
        assert body["state"] == "UNKNOWN_OUTCOME"
        assert "could not be read back" in body["why"]
        assert doc.writes                                  # 它确实写了
        receipt = publish.read_receipt(tmp_path / "receipts", "doc-a", "body")
        assert receipt["state"] == "UNKNOWN_OUTCOME"


class TestTheReadbackAgainstHowTheServiceStoresIt:
    """真服务一段一个块。假文档照做，否则比对的缺陷测不出来。"""

    def test_two_paragraphs_written_and_read_back_agree(self, tmp_path):
        # 第一次真写撞上的就是这个：内容两个段落，读回说「文档和我写的不一样」，
        # 而文档一字不差。原因是比对把段落之间的断行抹掉了，于是写出来的每一行
        # 都成了比整篇更长的字符串里找不到的子串。
        content = "<p>第一段。</p><p>第二段。</p>"
        doc = FakeDoc()
        doc, report = a_run(tmp_path, doc=doc,
                            request=a_request(content=content))
        assert rows(report)["body"]["state"] == "OK"

    def test_the_readback_still_notices_a_paragraph_that_is_missing(self, tmp_path):
        # 放宽到「按行包含」之后不能松成「随便含一点就算数」。
        content = "<p>第一段。</p><p>第二段。</p>"
        assert publish.section_matches(
            {"blocks": [{"type": "text", "text": "第一段。"},
                        {"type": "text", "text": "第二段。"}]}, content) is True
        assert publish.section_matches(
            {"blocks": [{"type": "text", "text": "第一段。"}]}, content) is False

    def test_the_receipt_exists_before_the_write_not_only_after(self, tmp_path):
        # 「写之前先记一笔」是有意为之，不是顺手：**写入过程中断电**，回执是唯一
        # 留下的东西，而它的作用正是让下一轮去读、而不是重发。所以它必须在写入
        # **进行时**就已经在盘上 —— 这一条只有站在 writer 里面才量得到。
        doc = FakeDoc()
        during: dict = {}

        def writer(document, section, content, revision):
            during.update(publish.read_receipt(tmp_path / "receipts", "doc-a", "body"))
            return doc.body_writer(document, section, content, revision)

        doc, report = a_run(tmp_path, doc=doc, writers={"body": writer})
        assert during.get("state") == "UNKNOWN_OUTCOME"
        assert during.get("intent") == publish.intent_key("body", a_request())
        assert rows(report)["body"]["state"] == "OK"
        assert publish.read_receipt(tmp_path / "receipts", "doc-a",
                                    "body")["state"] == "OK"


class TestTheFourEdgesThatFollowTheBody:
    def test_a_run_that_does_that_kind_of_thing_at_all_says_not_applicable(self, tmp_path):
        # 「这个对象没有这件事」与「这一轮没轮到」是两句不同的话，不能合并成
        # 一个「跳过」—— 合并之后，「媒体从来没人配」会被读成「媒体这次没跑」。
        doc = FakeDoc()
        doc, report = a_run(tmp_path, doc=doc, writers={"body": doc.body_writer})
        table = rows(report)
        for effect in ("media", "index", "git", "delivery"):
            assert table[effect]["state"] == "NOT_APPLICABLE", effect
        assert "no media writer" in table["media"]["why"]

    def test_an_effect_that_applies_but_was_not_reached_names_what_stopped_it(self, tmp_path):
        doc = FakeDoc()
        seen: list[str] = []

        def broken_media(*_args):
            raise RuntimeError("上传超时")

        def noted(effect):
            def run(*_args):
                seen.append(effect)
                return {"ok": True}
            return run

        writers = {"body": doc.body_writer, "media": broken_media,
                   "index": noted("index"), "git": noted("git"),
                   "delivery": noted("delivery")}
        doc, report = a_run(tmp_path, doc=doc, writers=writers)
        table = rows(report)
        assert table["media"]["state"] == "FAILED"
        for effect in ("index", "git", "delivery"):
            assert table[effect]["state"] == "NOT_ATTEMPTED", effect
            assert table[effect]["why"] == "not reached: media is FAILED"
        assert seen == []           # 前面那条边断了，后面一条都没动过

    def test_a_declared_none_is_not_applicable_with_its_own_reason(self, tmp_path):
        doc = FakeDoc()
        request = a_request(media="NONE")
        doc, report = a_run(tmp_path, doc=doc, request=request,
                            writers={"body": doc.body_writer,
                                     "media": lambda *a: {"ok": True}})
        assert rows(report)["media"]["state"] == "NOT_APPLICABLE"
        assert "media=NONE" in rows(report)["media"]["why"]

    def test_body_ok_and_media_failed_then_only_media_is_retried(self, tmp_path):
        doc = FakeDoc()
        attempts = {"n": 0}

        def flaky(document, request):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise RuntimeError("上传超时")
            return {"uploaded": 1}

        writers = {"body": doc.body_writer, "media": flaky}
        doc, first = a_run(tmp_path, doc=doc, writers=writers)
        table = rows(first)
        assert table["body"]["state"] == "OK"
        assert table["media"]["state"] == "FAILED"
        assert table["git"]["state"] == "NOT_APPLICABLE"

        doc, second = a_run(tmp_path, doc=doc, writers=writers)
        table = rows(second)
        assert table["body"]["state"] == "ALREADY_EFFECTED"   # 不写第二遍
        assert table["media"]["state"] == "OK"                # 只补缺的那条边
        assert len(doc.writes) == 1

    def test_a_settled_first_run_reports_settled(self, tmp_path):
        doc = FakeDoc()
        doc, report = a_run(tmp_path, doc=doc,
                            writers={"body": doc.body_writer,
                                     "media": lambda *a: {"ok": True}})
        assert report["settled"] is True
        assert rows(report)["media"]["state"] == "OK"
        assert rows(report)["index"]["state"] == "NOT_APPLICABLE"


class TestTheReceipts:
    def test_each_effect_gets_its_own_receipt_file(self, tmp_path):
        doc = FakeDoc()
        a_run(tmp_path, doc=doc, writers={"body": doc.body_writer,
                                          "media": lambda *a: {"ok": True}})
        written = sorted(path.name for path in (tmp_path / "receipts" / "doc-a").iterdir())
        assert written == ["body.json", "media.json"]

    def test_a_receipt_that_does_not_parse_reads_as_an_unknown_outcome(self, tmp_path):
        path = tmp_path / "receipts" / "doc-a" / "body.json"
        path.parent.mkdir(parents=True)
        path.write_text("{not json", encoding="utf-8")
        receipt = publish.read_receipt(tmp_path / "receipts", "doc-a", "body")
        assert receipt["state"] == "UNKNOWN_OUTCOME"
        assert "could not be read" in receipt["why"]

    def test_never_attempted_is_none_and_not_an_empty_receipt(self, tmp_path):
        assert publish.read_receipt(tmp_path / "receipts", "doc-a", "body") is None

    def test_a_receipt_records_the_intent_it_belongs_to(self, tmp_path):
        doc, _ = a_run(tmp_path)
        receipt = publish.read_receipt(tmp_path / "receipts", "doc-a", "body")
        assert receipt["intent"] == publish.intent_key("body", a_request())
        assert receipt["state"] == "OK"


class TestRendering:
    def test_a_refused_run_renders_its_refusals_and_no_effect_marks(self, tmp_path):
        contract = a_contract(contract="PROFILE_MISSING", reasons=["profile"])
        _, report = a_run(tmp_path, contract=contract)
        lines = publish.render(report)
        assert any(line.startswith("✗ 没有开始") for line in lines)
        assert any("PROFILE_MISSING" in line for line in lines)

    def test_a_run_renders_one_line_per_effect_even_when_nothing_happened(self, tmp_path):
        doc, report = a_run(tmp_path)
        lines = publish.render(report)
        for effect in publish.EFFECTS:
            assert any(effect in line for line in lines)


class TestTheRequestLoader:
    def test_a_missing_request_file_is_refused_with_its_path(self, tmp_path):
        with pytest.raises(publish.Refused) as caught:
            publish.read_request(tmp_path / "nowhere.json")
        assert "nowhere.json" in str(caught.value)

    def test_a_request_with_the_wrong_schema_is_malformed(self, tmp_path):
        path = tmp_path / "r.json"
        path.write_text(json.dumps({"schema": "something/else"}), encoding="utf-8")
        with pytest.raises(publish.Malformed):
            publish.read_request(path)

    def test_a_request_that_is_not_json_is_refused_not_crashed(self, tmp_path):
        path = tmp_path / "r.json"
        path.write_text("{oops", encoding="utf-8")
        with pytest.raises(publish.Refused):
            publish.read_request(path)

    def test_a_valid_request_round_trips(self, tmp_path):
        path = tmp_path / "r.json"
        path.write_text(json.dumps(a_request()), encoding="utf-8")
        assert publish.read_request(path)["document"] == "doc-a"
