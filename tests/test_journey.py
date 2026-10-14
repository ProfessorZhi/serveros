#!/usr/bin/env python3
"""§C — 一条来源修订走到投递回执，沿途那几个必须停下来的点。

WHY THIS FILE EXISTS
--------------------
C 线的验收不是「二十一职责都装了」，是一条**可重放的收据链**：合成的一条飞书来源观察，
沿着 B1→B3→B4→A7→B8→B10 走一遍，在三个地方必须停下来问人、在两个地方必须拒绝把
「自称」读成「证据」、以及一次投递失败必须**留在待发**而不是变成「失败」。

这条链上的每一节都已经有 producer 了（`runtime/daily_report.py`、
`runtime/report_delivery.py`）。所以这里断言的不是那些 producer 的行为——它们各自有
自己的测试——而是**转接处**的行为，以及这条链自己是不是诚实：

  正控（少了它，负控是没有意义的）
    一条来源 → 候选 → 人类决定 → 工作 → 真实观察 → 文档落盘 → 一次投递，
    每一节都留下一条收据，且 `stop` 是通道自己报告的那个词。

  负控（这七条是这个文件真正的用途）
    1. 同一条消息读两遍          → 不是第二条事实（记录不变）
    2. 同一条消息被改了          → 不是第二项工作（同一个 requirement_ref）
    3. 一个模型的决定            → 不产生授权，且**具名**说出它为什么不算
    4. 一个子任务的 DONE         → 同上，逐字是 `child-done` 那一条
    5. 自称 DONE 而没有观察      → 不是证据（UNOBSERVED）
    6. 没有收件人                → 待发，而不是「已安排投递」
    7. 通道抛异常 / 答一个没声明过的词 → 不是失败，是 UNKNOWN

WHAT IS DELIBERATELY NOT MOCKED
-------------------------------
B8 与 B10 两节走的是真的 `daily_report.compose/render/receipt/publish` 与真的
`report_delivery.request/deliver`：只有**通道**是注入的（`sender` 参数，就是
`report_delivery` 自己设计的那条缝），所以「投递」那一段是真的在按它自己的纪律走
outbox→intent→一次尝试→回执。测试注入一个会记账的 `Sender`，而不是把这一段短路掉。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
RUNTIME = REPO / "runtime"


def _load(name: str, filename: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, RUNTIME / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


journey = _load("serveros_runtime_journey", "journey.py")


# ---------------------------------------------------------------------------
# 注入的通道：就是 `report_delivery` 自己设计的那条缝
# ---------------------------------------------------------------------------

class Sender:
    """Records every call and answers with what it is told."""

    def __init__(self, state="DELIVERED", why="收件端接受了这条消息"):
        self.calls = []
        self.state = state
        self.why = why

    def __call__(self, request):
        self.calls.append(request)
        return {"state": self.state, "why": self.why,
                "provider": {"id": "test-transport", "transport": "test",
                             "remote_id": "t-1"}}


class Raising:
    def __init__(self):
        self.calls = []

    def __call__(self, request):
        self.calls.append(request)
        raise TimeoutError("the transport did not answer")


# ---------------------------------------------------------------------------
# 合成的一条来源：一条飞书群消息，以及它的一个修订
# ---------------------------------------------------------------------------

CHAT = "feishu:chat:oc_sample"
MESSAGE = "om_sample_0001"
REVISION = "sha256rev0001"
AT = "2026-10-14T10:00:00+09:00"


def observation(**over):
    spec = {"source_id": CHAT, "message_id": MESSAGE, "revision": REVISION,
            "captured_at": AT, "trust": "authorized-follow"}
    spec.update(over)
    return spec


def decision(**over):
    spec = {"kind": "human", "by": "person:vince", "at": AT}
    spec.update(over)
    return spec


def journey_input(**over):
    spec = {"observations": [observation()],
            "goal": "把这次改动落成一次有测试的交付",
            "acceptance": ["测试退出码为 0"],
            "sources": [journey.message_ref(CHAT, MESSAGE)],
            "decision": decision(),
            "run": {"name": "tiny-tests", "kind": "run",
                    "observed": {"ran": True, "exit_code": 0, "at": AT}},
            "period": {"start": "2026-10-14T00:00:00+09:00",
                       "end": "2026-10-14T23:59:59+09:00"}}
    spec.update(over)
    # 决定必须是对**这一版来源**作出的；来源由 observations 决定，所以这里补上。
    if spec.get("decision") is not None and "for_revision" not in spec["decision"]:
        store = journey.capture(journey.empty_store(), spec["observations"])["store"]
        spec["decision"] = dict(spec["decision"], for_revision=journey.source_revision(
            store, spec["sources"] or sorted(store["messages"])))
    return spec


def full(**over):
    """The whole chain, with the delivery leg wired. `delivery=None` takes that
    leg out without taking the rest of the input away."""
    spec = {"delivery": {"recipient": "chat:oc_receiver",
                         "requested_by": "person:vince",
                         "why": "验收通过，按约定回报",
                         "authorization": {"authorized": True,
                                           "because": "刚刚复查过"}}}
    spec.update(over)
    return journey_input(**spec)


def walk(tmp_path, sender=None, **over):
    spec = dict(full())
    spec.update(over)
    out_dir = tmp_path / "report"
    out_dir.mkdir(parents=True, exist_ok=True)
    return journey.walk(spec, out_dir=str(out_dir), sender=sender, at=AT)


def sha_of(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# 正控：整条链
# ---------------------------------------------------------------------------

class TestTheWholeChain:
    def test_a_source_with_no_goal_stops_at_capture(self):
        record = journey.walk({"observations": [observation()]})
        assert record["stop"] == "CAPTURED_ONLY"
        assert [step["kind"] for step in record["receipts"]] == ["capture"]
        assert "requirement_ref" not in record["refs"]

    def test_a_candidate_without_a_decision_needs_a_human_and_has_no_work(self):
        record = journey.walk(journey_input(decision=None))
        assert record["stop"] == "NEEDS_HUMAN"
        assert record["refs"]["requirement_ref"].startswith("requirement:")
        # 这一条是整份测试里最要紧的一句：候选**没有** work_ref。
        assert "work_ref" not in record["refs"]
        assert record["receipts"][-1]["kind"] == "requirement"

    def test_the_accepted_chain_archives_a_report_and_stops_pending_without_a_receiver(
            self, tmp_path):
        record = journey.walk(full(delivery=None), out_dir=str(tmp_path / "report"),
                              sender=None, at=AT)
        assert record["stop"] == "PENDING"
        kinds = [step["kind"] for step in record["receipts"]]
        assert kinds == ["capture", "requirement", "decision", "work",
                         "acceptance", "report"]
        written = tmp_path / "report" / "daily-report.md"
        assert written.is_file()
        receipt = json.loads((tmp_path / "report" / "publication-receipt.json")
                             .read_text(encoding="utf-8"))
        assert receipt["document"]["sha256"] == sha_of(written)

    def test_a_human_decision_and_a_real_observation_reach_the_transport(
            self, tmp_path):
        sender = Sender()
        record = walk(tmp_path, sender=sender)
        assert record["stop"] == "DELIVERED"
        assert [step["kind"] for step in record["receipts"]][-1] == "delivery"
        assert record["receipts"][-1]["state"] == "DELIVERED"
        # 通道**真的**被问了一次，而且问的是这一版报告。
        assert len(sender.calls) == 1
        assert sender.calls[0]["report"]["sha256"] == record["refs"]["report_sha256"]

    def test_the_same_input_twice_produces_the_same_content(self, tmp_path):
        """Determinism is a claim about the CONTENT, not about the paths.

        The two runs write to different directories and each receipt names the
        document it is about, so the `ref` of the report receipt — an absolute
        path — legitimately differs. What must not differ is the document's own
        sha256: two runs of one input have to compose the same bytes, or "which
        version did the receiver get" stops being answerable from the input.
        """
        first = walk(tmp_path / "a", sender=Sender())
        second = walk(tmp_path / "b", sender=Sender())
        assert first["refs"]["report_sha256"] == second["refs"]["report_sha256"]
        shape = lambda record: [(s["kind"], s["state"], s["revision"])
                                for s in record["receipts"]]
        assert shape(first) == shape(second)


# ---------------------------------------------------------------------------
# 负控 1–2：重复与编辑
# ---------------------------------------------------------------------------

class TestDuplicatesAndEdits:
    def test_reading_the_same_message_twice_is_not_a_second_fact(self):
        got = journey.capture(journey.empty_store(),
                              [observation(), observation()])
        states = [step["state"] for step in got["outcomes"]]
        assert states == ["NEW", "ALREADY_CAPTURED"]
        stored = got["store"]["messages"][journey.message_ref(CHAT, MESSAGE)]
        assert len(stored["revisions"]) == 1

    def test_an_edit_is_a_new_revision_of_one_message(self):
        got = journey.capture(journey.empty_store(), [observation()])
        got = journey.capture(got["store"], [observation(revision="sha256rev0002")])
        assert got["outcomes"][0]["state"] == "NEW_REVISION"
        assert len(got["store"]["messages"]) == 1
        stored = got["store"]["messages"][journey.message_ref(CHAT, MESSAGE)]
        assert [r["revision"] for r in stored["revisions"]] == [REVISION, "sha256rev0002"]

    def test_an_edit_does_not_produce_a_second_requirement(self):
        first = journey.capture(journey.empty_store(), [observation()])["store"]
        edited = journey.capture(first, [observation(revision="sha256rev0002")])["store"]
        refs = [journey.message_ref(CHAT, MESSAGE)]
        a = journey.propose(first, goal="同一个目标", sources=refs)
        b = journey.propose(edited, goal="同一个目标", sources=refs)
        assert a["requirement_ref"] == b["requirement_ref"]
        # 但那一版变了 —— 这正是「旧决定不再覆盖新内容」能被判出来的原因。
        assert a["source_revision"] != b["source_revision"]

    def test_a_decision_made_for_an_earlier_revision_needs_the_human_again(self):
        store = journey.capture(journey.empty_store(), [observation()])["store"]
        refs = [journey.message_ref(CHAT, MESSAGE)]
        proposal = journey.propose(store, goal="目标", sources=refs)
        edited = journey.capture(store, [observation(revision="sha256rev0002")])["store"]
        moved = journey.propose(edited, goal="目标", sources=refs)
        stale = journey.authorize(moved, decision=decision(
            for_revision=proposal["source_revision"]))
        assert stale["state"] == "NEEDS_HUMAN"
        assert "重新问一次" in stale["why"]


# ---------------------------------------------------------------------------
# 负控 3–4：什么不算授权
# ---------------------------------------------------------------------------

class TestWhatDoesNotAuthorize:
    @pytest.mark.parametrize("kind", ["model", "child-done", "task-done",
                                      "repo-approve-text"])
    def test_the_named_impostors_are_refused_by_name(self, kind):
        store = journey.capture(journey.empty_store(), [observation()])["store"]
        refs = [journey.message_ref(CHAT, MESSAGE)]
        proposal = journey.propose(store, goal="目标", sources=refs)
        with pytest.raises(journey.Refused) as caught:
            journey.authorize(proposal, decision=decision(
                kind=kind, for_revision=proposal["source_revision"]))
        # 具名，不是「不合法」：收到的人要知道自己做错了什么、什么才算对。
        assert f"`{kind}`" in str(caught.value)

    def test_an_unrecognised_kind_is_not_read_as_yes(self):
        store = journey.capture(journey.empty_store(), [observation()])["store"]
        proposal = journey.propose(store, goal="目标",
                                   sources=[journey.message_ref(CHAT, MESSAGE)])
        with pytest.raises(journey.Refused):
            journey.authorize(proposal, decision=decision(
                kind="lgtm", for_revision=proposal["source_revision"]))

    def test_a_decision_with_no_author_is_not_a_decision(self):
        store = journey.capture(journey.empty_store(), [observation()])["store"]
        proposal = journey.propose(store, goal="目标",
                                   sources=[journey.message_ref(CHAT, MESSAGE)])
        with pytest.raises(journey.Refused):
            journey.authorize(proposal, decision={"kind": "human", "at": AT,
                                                  "for_revision":
                                                      proposal["source_revision"]})

    def test_work_is_never_minted_from_a_candidate_alone(self):
        store = journey.capture(journey.empty_store(), [observation()])["store"]
        proposal = journey.propose(store, goal="目标",
                                   sources=[journey.message_ref(CHAT, MESSAGE)])
        with pytest.raises(journey.Refused):
            journey.open_work(proposal, {"state": "NEEDS_HUMAN", "by": None})
        assert proposal["work_ref"] is None


# ---------------------------------------------------------------------------
# 负控 5：自称不算证据
# ---------------------------------------------------------------------------

class TestClaimsAreNotEvidence:
    def test_a_claim_of_done_with_no_observation_is_not_evidence(self):
        verdict = journey.run_verdict({"name": "tiny-tests", "claim": "DONE"})
        assert verdict["state"] == "UNOBSERVED"
        assert verdict["accepted"] is False
        assert "没有被观察到的结论不是证据" in verdict["why"]

    def test_a_nonzero_exit_code_is_a_failure_not_a_pass(self):
        verdict = journey.run_verdict(
            {"name": "tiny-tests", "observed": {"ran": True, "exit_code": 1}})
        assert verdict["state"] == "FAILED"
        assert verdict["accepted"] is False

    def test_a_run_that_did_not_run_is_not_a_pass(self):
        verdict = journey.run_verdict(
            {"name": "tiny-tests", "observed": {"ran": False}})
        assert verdict["state"] == "NOT_RUN"
        assert verdict["accepted"] is False

    def test_a_claimed_done_run_stops_the_chain_before_any_report(self, tmp_path):
        record = walk(tmp_path, sender=Sender(),
                      run={"name": "tiny-tests", "claim": "DONE"})
        assert record["stop"] == "NOT_ACCEPTED"
        kinds = [step["kind"] for step in record["receipts"]]
        assert "report" not in kinds and "delivery" not in kinds

    def test_the_run_is_classified_by_the_report_modules_own_vocabulary(self):
        """Two vocabularies for "did it pass" would be two answers, and the one
        that drifts is the one nobody runs."""
        report = journey._daily_report()
        for run in ({"name": "x", "claim": "DONE"},
                    {"name": "x", "observed": {"ran": True, "exit_code": 0}},
                    {"name": "x", "observed": {"ran": True, "exit_code": 3}}):
            assert journey.run_verdict(run)["state"] == \
                report.classify_run_status([run])[0]["state"]


# ---------------------------------------------------------------------------
# 负控 6–7：投递
# ---------------------------------------------------------------------------

class TestDeliveryIsNotAYesOrNo:
    def test_no_recipient_means_pending_and_nothing_is_attempted(self, tmp_path):
        sender = Sender()
        record = journey.walk(full(delivery=None), out_dir=str(tmp_path / "report"),
                              sender=sender, at=AT)
        assert record["stop"] == "PENDING"
        assert sender.calls == []
        assert not (tmp_path / "report" / "delivery-receipt.json").exists()

    def test_a_transport_that_says_failed_leaves_the_entry_pending(self, tmp_path):
        record = walk(tmp_path, sender=Sender(state="FAILED", why="收件端拒绝了"))
        assert record["stop"] == "UNKNOWN"
        # 回执记 FAILED，但**待发记录**仍然是 PENDING：失败不settle，下次还该试。
        outbox = json.loads((tmp_path / "report" / "delivery-outbox.json")
                            .read_text(encoding="utf-8"))
        assert [entry["state"] for entry in outbox["entries"]] == ["PENDING"]
        assert record["receipts"][-1]["state"] == "FAILED"

    def test_a_transport_that_raises_is_unknown_not_failed(self, tmp_path):
        record = walk(tmp_path, sender=Raising())
        assert record["stop"] == "UNKNOWN"
        assert record["receipts"][-1]["state"] == "UNKNOWN_OUTCOME"

    def test_a_transport_word_nobody_declared_is_not_read_as_delivered(self, tmp_path):
        record = walk(tmp_path, sender=Sender(state="SENT", why="发好了"))
        assert record["stop"] == "UNKNOWN"
        assert record["receipts"][-1]["state"] == "UNKNOWN"

    def test_no_consumer_is_pending_and_writes_no_receipt(self, tmp_path):
        record = walk(tmp_path, sender=None)
        assert record["stop"] == "PENDING"
        assert record["receipts"][-1]["state"] == "PENDING_CONSUMER"
        assert not (tmp_path / "report" / "delivery-receipt.json").exists()

    def test_the_delivery_words_are_read_from_the_report_module_not_restated(self):
        delivery = journey._report_delivery()
        for word in delivery.delivery_states():
            assert word in journey._DELIVERY_STOP
        assert set(journey._DELIVERY_STOP) - set(delivery.delivery_states()) == {
            "PENDING_CONSUMER", "UNKNOWN_OUTCOME", "ALREADY_DELIVERED", "REFUSED"}


# ---------------------------------------------------------------------------
# 契约本身：词表读来的，收据字段齐的
# ---------------------------------------------------------------------------

class TestTheContractIsReadNotRestated:
    def test_the_module_and_the_contract_agree_on_every_closed_list(self):
        document = journey.contract()
        assert journey.capture_states() == tuple(
            row["id"] for row in document["capture_states"])
        assert journey.stops() == tuple(row["id"] for row in document["stops"])
        assert journey.receipt_kinds() == tuple(
            row["id"] for row in document["receipt_kinds"])
        assert journey.decided_by_humans() == tuple(
            row["id"] for row in document["decision_kinds"]["accepted"])

    def test_every_receipt_carries_the_declared_fields(self, tmp_path):
        required = set(journey.contract()["required_fields"])
        record = walk(tmp_path, sender=Sender())
        assert record["receipts"]
        for step in record["receipts"]:
            assert required <= set(step), step

    def test_a_receipt_from_an_undeclared_step_cannot_be_written(self):
        """The closed set on a receipt is its `kind`, not its `state`: a step
        nobody declared means somebody added one without editing the contract.
        States are per-kind and open on purpose — `work` records `OPEN@1`."""
        with pytest.raises(journey.Refused):
            journey._receipt("teleport", "ref", "rev", "OK", "why", AT)

    def test_a_source_nobody_captured_cannot_be_referenced(self):
        store = journey.capture(journey.empty_store(), [observation()])["store"]
        with pytest.raises(journey.Refused):
            journey.propose(store, goal="目标",
                            sources=[journey.message_ref("feishu:chat:oc_other", "om_x")])

    def test_an_observation_without_trust_is_not_a_reading(self):
        with pytest.raises(journey.Refused):
            journey.capture(journey.empty_store(), [observation(trust="")])


# ---------------------------------------------------------------------------
# 落盘那条界：与 daily_report.publish 同一条理由
# ---------------------------------------------------------------------------

class TestTheReportLandsOutsideTheRepo:
    def test_an_out_dir_inside_the_repo_is_refused(self, tmp_path):
        with pytest.raises(Exception) as caught:
            journey.walk(full(delivery=None), out_dir=str(RUNTIME), sender=None, at=AT)
        assert "本仓内" in str(caught.value)

    def test_a_relative_out_dir_is_refused(self):
        with pytest.raises(Exception) as caught:
            journey.walk(full(delivery=None), out_dir="report", sender=None, at=AT)
        assert "绝对路径" in str(caught.value)
