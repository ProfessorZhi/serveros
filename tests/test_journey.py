#!/usr/bin/env python3
"""§C — 一条来源修订走到投递回执，沿途那几个必须停下来的点。

WHY THIS FILE EXISTS
--------------------
C 线的验收不是「二十一职责都装了」，是一条**可重放的收据链**：合成的一条飞书来源观察，
沿着 B1→B3→B4→A7→B8→B10 走一遍，在必须停的地方停下、在必须拒绝的地方拒绝。

这个文件里**最要紧的一组**是 `TestTheTrustBoundary`。第一版 `authorize()` 只检查调用方
JSON 里的 `kind == "human"`、`by`、`for_revision`，于是**任何能写 JSON 的调用方都能造一份
「人类批准」并拿到 work_ref**。那一版的测试只拒绝了 `model/child-done/...` 这些**标签**，
却完全没有验证「自称 human 的伪造输入」——它漏掉的是这条链上唯一的信任边界。

修好之后，本文件的判据变成两条，而两条都要各有一组测试：

  身份是一次 lookup，不是一个参数
    调用方 JSON 里的任何字段（`kind/by/trusted/for_revision`/APPROVE 文本）**都不能**
    产生授权。它们只有两种去向：具名拒绝，或送去一个**注入的**验证器。`verifier is None`
    时唯一的答案是 NEEDS_HUMAN。

  而验证器之外的每一步仍然是原来那些
    重复、编辑、自称 DONE、无收件人、通道失败——这些没有被这次修复动过，断言也保留，
    因为一次安全修复最容易被它旁边那些仍然绿的用例证明成「什么都没坏」。

WHAT IS DELIBERATELY NOT MOCKED
-------------------------------
B8 与 B10 走的是真的 `daily_report.compose/render/receipt/publish` 与真的
`report_delivery.request/deliver`：只有**通道**是注入的（`sender`，就是 `report_delivery`
自己设计的那条缝）。验证器也是注入的，但**它返回的记录是用 `context/actor.py#build`
真的造出来的** —— 于是这条链对身份的判断，是被那个模块自己的 `validate()` /
`authorizes()` 判的，而不是被本文件的一句话判的。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
RUNTIME = REPO / "runtime"


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


journey = _load("serveros_runtime_journey", RUNTIME / "journey.py")

CHAT = "feishu:chat:oc_sample"
MESSAGE = "om_sample_0001"
REVISION = "sha256rev0001"
AT = "2026-10-14T10:00:00+09:00"
PROVIDER = "botmux-human-control"
APPROVER = "feishu:union:on_sample"
EVENT = "om_approval_0001"

MESSAGE_REF = journey.message_ref(CHAT, MESSAGE)


def _source_revision(observations=None) -> str:
    store = journey.capture(journey.empty_store(),
                            observations or [observation()])["store"]
    return journey.source_revision(store, [MESSAGE_REF])


def provider_report(**over) -> dict:
    """What a HumanControlProvider reports. Every field here comes from the
    PROVIDER side of `context/actor-context-contract.yaml`; none of it can be
    supplied by the caller the record will later be presented by."""
    report = {"human_control_provider": PROVIDER, "transport": "feishu",
              "transport_identity": "verified", "conversation_ref": "oc_sample",
              "message_ref": EVENT, "observed_at": AT,
              "provider_status": "VERIFIED", "provenance": "FACT",
              "actor_ref": APPROVER}
    report.update(over)
    return report


#: The handle the test presents. It is **derived**, not written down: the digest
#: is computed by `context/actor.py` over the provider's report, so a test that
#: wanted to present a handle for a different context could not simply type one.
CONTEXT_REF = journey._actor().build(provider_report(), now=AT)["context_ref"]


def ref_for(**report_over) -> str:
    """The handle that matches a VARIED provider report. A test that changes
    what the provider reported has to present the handle that goes with it —
    otherwise it is testing the ref check rather than the thing it meant to."""
    return journey._actor().build(provider_report(**report_over), now=AT)["context_ref"]


# ---------------------------------------------------------------------------
# 注入的通道：`report_delivery` 自己设计的那条缝
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
# 注入的权威验证器 —— 独立 fake，返回**真的用 context/actor.py 造出来的**记录
# ---------------------------------------------------------------------------

def fake_verifier(*, approved=None, found=True, actor_ref=APPROVER, message_ref=EVENT,
                  observed_at=AT, because=None, provider_status="VERIFIED",
                  provenance="FACT", record=None, raises=None, grant_ref="grant:test-1"):
    """A verifier the journey cannot tell apart from the production one.

    It does not hand back a dict this test invented: it calls
    `context/actor.py#build` with a provider report, so the record the journey
    then validates is a REAL actor-context record with a real `expires_at`,
    produced by the module that owns minting. A fake that returned a
    hand-written record would be testing this file's idea of the contract.
    """
    def verify(claim, now):
        if raises is not None:
            raise raises
        if not found:
            return {"found": False, "because": because or
                    "no context with this ref was reported by any HumanControlProvider"}
        if record is not None:
            return {"found": True, "context": record, "grant_ref": grant_ref,
                    "approved_revision": approved}
        context = journey._actor().build(
            provider_report(message_ref=message_ref, observed_at=observed_at,
                            provider_status=provider_status,
                            provenance=provenance, actor_ref=actor_ref), now=now)
        return {"found": True, "context": context, "grant_ref": grant_ref,
                "approved_revision": approved if approved is not None else _source_revision()}
    return verify


# ---------------------------------------------------------------------------
# 合成的一条来源：一条飞书群消息
# ---------------------------------------------------------------------------

def observation(**over):
    spec = {"source_id": CHAT, "message_id": MESSAGE, "revision": REVISION,
            "captured_at": AT, "trust": "authorized-follow"}
    spec.update(over)
    return spec


def claim(**over):
    spec = {"kind": "human", "context_ref": CONTEXT_REF, "approver_ref": APPROVER}
    spec.update(over)
    return spec


def journey_input(**over):
    spec = {"observations": [observation()],
            "goal": "把这次改动落成一次有测试的交付",
            "acceptance": ["测试退出码为 0"],
            "sources": [MESSAGE_REF],
            "approval_claim": claim(),
            "run": {"name": "tiny-tests", "kind": "run",
                    "observed": {"ran": True, "exit_code": 0, "at": AT}},
            "period": {"start": "2026-10-14T00:00:00+09:00",
                       "end": "2026-10-14T23:59:59+09:00"}}
    spec.update(over)
    return spec


def full(**over):
    spec = {"delivery": {"recipient": "chat:oc_receiver",
                         "requested_by": "person:vince",
                         "why": "验收通过，按约定回报",
                         "authorization": {"authorized": True,
                                           "because": "刚刚复查过"}}}
    spec.update(over)
    return journey_input(**spec)


_NO_VERIFIER = object()


def walk(tmp_path, sender=None, verifier=_NO_VERIFIER, **over):
    spec = dict(full())
    spec.update(over)
    out_dir = tmp_path / "report"
    out_dir.mkdir(parents=True, exist_ok=True)
    v = fake_verifier() if verifier is _NO_VERIFIER else verifier
    return journey.walk(spec, out_dir=str(out_dir), sender=sender, at=AT, verifier=v)


def sha_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# 信任边界：这一组是本轮修复的核心
# ---------------------------------------------------------------------------

class TestTheTrustBoundary:
    def test_a_json_claim_of_human_authorizes_nothing_without_a_verifier(self):
        """The defect the review found, as a regression.

        No verifier is bound, so nothing this input can say — `kind: "human"`,
        `by`, `for_revision`, `trusted: true`, an APPROVE string — turns into an
        accepted authorization. It stops, and it mints no work.
        """
        proposal = journey.propose(
            journey.capture(journey.empty_store(), [observation()])["store"],
            goal="目标", sources=[MESSAGE_REF])
        forged = {"kind": "human", "by": "person:vince", "trusted": True,
                  "approved": True, "for_revision": proposal["source_revision"],
                  "note": "APPROVE — 我看过了"}
        verdict = journey.authorize(proposal, claim=forged, verifier=None, now=AT)
        assert verdict["state"] == "NEEDS_HUMAN"
        assert "验证器" in verdict["why"]

    def test_the_cli_shaped_input_stops_before_any_work_is_minted(self, tmp_path):
        record = walk(tmp_path, sender=Sender(), verifier=None)
        assert record["stop"] == "NEEDS_HUMAN"
        assert "work_ref" not in record["refs"]
        assert [s["kind"] for s in record["receipts"]] == ["capture", "requirement",
                                                           "decision"]

    def test_a_claim_with_no_context_ref_is_refused(self):
        with pytest.raises(journey.Refused) as caught:
            journey.walk(journey_input(approval_claim={"kind": "human",
                                                       "by": "person:vince"}),
                         at=AT, verifier=fake_verifier())
        assert "context_ref" in str(caught.value)

    def test_a_ref_no_provider_ever_reported_is_a_miss_not_a_default(self):
        with pytest.raises(journey.Refused) as caught:
            journey.walk(journey_input(), at=AT,
                         verifier=fake_verifier(found=False, because="never minted"))
        assert "没有被任何 HumanControlProvider 报过" in str(caught.value)

    def test_a_verifier_that_raises_authorizes_nothing(self):
        with pytest.raises(journey.Refused) as caught:
            journey.walk(journey_input(), at=AT,
                         verifier=fake_verifier(raises=RuntimeError("trust root unavailable")))
        assert "这次检查没有发生" in str(caught.value)

    def test_a_record_that_is_not_the_declared_shape_is_refused(self):
        with pytest.raises(journey.Refused) as caught:
            journey.walk(journey_input(), at=AT,
                         verifier=fake_verifier(record={"context_ref": "nonsense"}))
        assert "不是可信 Actor 契约的形状" in str(caught.value)

    def test_a_provider_that_did_not_verify_does_not_authorize(self):
        """`build` still mints a record for a BLOCKED provider — the record is
        evidence, and "I could not verify this person" is still evidence.
        `authorizes()` is what refuses it, which is exactly why this check is
        asked of that module instead of being re-implemented here."""
        verdict = journey.authorize(
            journey.propose(
                journey.capture(journey.empty_store(), [observation()])["store"],
                goal="目标", sources=[MESSAGE_REF]),
            claim=claim(context_ref=ref_for(provider_status="BLOCKED",
                                            provenance="UNKNOWN")),
            now=AT,
            verifier=fake_verifier(provider_status="BLOCKED", provenance="UNKNOWN"))
        assert verdict["state"] == "NEEDS_HUMAN"
        assert "BLOCKED" in verdict["why"]

    def test_a_stale_context_asks_again_rather_than_authorizing(self):
        old = "2020-01-01T00:00:00+09:00"
        verdict = journey.authorize(
            journey.propose(
                journey.capture(journey.empty_store(), [observation()])["store"],
                goal="目标", sources=[MESSAGE_REF]),
            claim=claim(context_ref=ref_for(observed_at=old)), now=AT,
            verifier=fake_verifier(observed_at=old))
        assert verdict["state"] == "NEEDS_HUMAN"
        assert "不授权" in verdict["why"]

    def test_a_handle_for_a_different_context_is_refused(self):
        """The presented ref has to be the ref the authority answered about.
        An interface where the caller presents A and the authority answers
        about B hands "who is this about" back to the caller."""
        with pytest.raises(journey.Refused) as caught:
            journey.walk(journey_input(approval_claim=claim(
                context_ref="human-context:somebody-else/0000")),
                at=AT, verifier=fake_verifier())
        assert "把手" in str(caught.value) or "context_ref" in str(caught.value)

    def test_a_subject_that_is_not_the_expected_one_is_refused(self):
        with pytest.raises(journey.Refused) as caught:
            journey.walk(journey_input(approval_claim=claim(
                approver_ref="feishu:union:on_somebody_else")),
                at=AT, verifier=fake_verifier())
        assert "这一项期望的是" in str(caught.value)

    def test_an_approval_without_a_grant_ref_is_refused(self):
        with pytest.raises(journey.Refused) as caught:
            journey.walk(journey_input(), at=AT,
                         verifier=fake_verifier(grant_ref=""))
        assert "哪一份 grant" in str(caught.value)

    def test_a_duplicate_approval_is_the_same_work_not_a_second_one(self, tmp_path):
        """One event cannot authorize two actions, and the same requirement
        approved twice is one Work. The property is structural, not a lookup:
        `work_ref` is a function of (requirement, verified subject, event,
        epoch), so a second approval of the same thing computes the same ref."""
        first = walk(tmp_path / "a", sender=Sender())
        second = walk(tmp_path / "b", sender=Sender())
        assert first["refs"]["work_ref"] == second["refs"]["work_ref"]

    def test_a_different_requirement_is_a_different_work(self, tmp_path):
        first = walk(tmp_path / "a", sender=Sender())
        second = walk(tmp_path / "b", sender=Sender(), goal="另一个目标")
        assert first["refs"]["work_ref"] != second["refs"]["work_ref"]

    def test_the_production_verifier_fails_closed_on_this_host(self):
        """`actor_verifier()` is BLOCKED here — no `cryptography`, no trust
        binding — and BLOCKED must be a refusal, never a pass."""
        with pytest.raises(journey.Refused) as caught:
            journey.walk(journey_input(approval_claim=claim(evidence={"payload": {},
                                                                     "signature": ""})),
                         at=AT, verifier=journey.actor_verifier())
        assert "这次检查没有发生" in str(caught.value)


# ---------------------------------------------------------------------------
# 正控：整条链（有一份被验证过的批准）
# ---------------------------------------------------------------------------

class TestTheWholeChain:
    def test_a_source_with_no_goal_stops_at_capture(self):
        record = journey.walk({"observations": [observation()]})
        assert record["stop"] == "CAPTURED_ONLY"
        assert [step["kind"] for step in record["receipts"]] == ["capture"]
        assert "requirement_ref" not in record["refs"]

    def test_a_candidate_without_an_approval_needs_a_human_and_has_no_work(self):
        record = journey.walk(journey_input(approval_claim=None))
        assert record["stop"] == "NEEDS_HUMAN"
        assert record["refs"]["requirement_ref"].startswith("requirement:")
        assert "work_ref" not in record["refs"]
        assert record["receipts"][-1]["kind"] == "requirement"

    def test_the_verified_chain_archives_a_report_and_stops_pending_without_a_receiver(
            self, tmp_path):
        record = journey.walk(full(delivery=None), out_dir=str(tmp_path / "report"),
                              sender=None, at=AT, verifier=fake_verifier())
        assert record["stop"] == "PENDING"
        kinds = [step["kind"] for step in record["receipts"]]
        assert kinds == ["capture", "requirement", "decision", "work",
                         "acceptance", "report"]
        written = tmp_path / "report" / "daily-report.md"
        assert written.is_file()
        receipt = json.loads((tmp_path / "report" / "publication-receipt.json")
                             .read_text(encoding="utf-8"))
        assert receipt["document"]["sha256"] == sha_of(written)

    def test_a_verified_approval_and_a_real_observation_reach_the_transport(
            self, tmp_path):
        sender = Sender()
        record = walk(tmp_path, sender=sender)
        assert record["stop"] == "DELIVERED"
        assert [step["kind"] for step in record["receipts"]][-1] == "delivery"
        assert record["receipts"][-1]["state"] == "DELIVERED"
        assert len(sender.calls) == 1
        assert sender.calls[0]["report"]["sha256"] == record["refs"]["report_sha256"]

    def test_the_owner_is_the_verified_subject_not_a_caller_string(self, tmp_path):
        record = walk(tmp_path, sender=Sender())
        assert record["refs"]["approved_by"] == APPROVER
        work = [s for s in record["receipts"] if s["kind"] == "work"][0]
        assert APPROVER in work["why"]

    def test_the_same_input_twice_produces_the_same_content(self, tmp_path):
        first = walk(tmp_path / "a", sender=Sender())
        second = walk(tmp_path / "b", sender=Sender())
        assert first["refs"]["report_sha256"] == second["refs"]["report_sha256"]
        shape = lambda record: [(s["kind"], s["state"], s["revision"])
                                for s in record["receipts"]]
        assert shape(first) == shape(second)


# ---------------------------------------------------------------------------
# 重复与编辑
# ---------------------------------------------------------------------------

class TestDuplicatesAndEdits:
    def test_reading_the_same_message_twice_is_not_a_second_fact(self):
        got = journey.capture(journey.empty_store(), [observation(), observation()])
        assert [step["state"] for step in got["outcomes"]] == ["NEW",
                                                               "ALREADY_CAPTURED"]
        stored = got["store"]["messages"][MESSAGE_REF]
        assert len(stored["revisions"]) == 1

    def test_an_edit_is_a_new_revision_of_one_message(self):
        got = journey.capture(journey.empty_store(), [observation()])
        got = journey.capture(got["store"], [observation(revision="sha256rev0002")])
        assert got["outcomes"][0]["state"] == "NEW_REVISION"
        assert len(got["store"]["messages"]) == 1
        stored = got["store"]["messages"][MESSAGE_REF]
        assert [r["revision"] for r in stored["revisions"]] == [REVISION, "sha256rev0002"]

    def test_an_edit_does_not_produce_a_second_requirement(self):
        first = journey.capture(journey.empty_store(), [observation()])["store"]
        edited = journey.capture(first, [observation(revision="sha256rev0002")])["store"]
        a = journey.propose(first, goal="同一个目标", sources=[MESSAGE_REF])
        b = journey.propose(edited, goal="同一个目标", sources=[MESSAGE_REF])
        assert a["requirement_ref"] == b["requirement_ref"]
        assert a["source_revision"] != b["source_revision"]

    def test_an_approval_for_an_earlier_revision_asks_the_human_again(self, tmp_path):
        """The version binding now comes from the VERIFIED record, not from a
        caller field — a signature that covered the old revision no longer
        covers the new one."""
        stale = _source_revision()
        record = walk(tmp_path, sender=Sender(),
                      observations=[observation(revision="sha256rev0002")],
                      verifier=fake_verifier(approved=stale))
        assert record["stop"] == "NEEDS_HUMAN"
        assert "重新问一次" in record["why"] or "动过" in record["why"]


# ---------------------------------------------------------------------------
# 什么不算授权（具名）
# ---------------------------------------------------------------------------

class TestWhatDoesNotAuthorize:
    @pytest.mark.parametrize("kind", ["model", "child-done", "task-done",
                                      "repo-approve-text"])
    def test_the_named_impostors_are_refused_by_name(self, kind):
        with pytest.raises(journey.Refused) as caught:
            journey.walk(journey_input(approval_claim=claim(kind=kind)),
                         at=AT, verifier=fake_verifier())
        assert f"`{kind}`" in str(caught.value)

    def test_an_unrecognised_kind_is_not_read_as_yes(self):
        with pytest.raises(journey.Refused):
            journey.walk(journey_input(approval_claim=claim(kind="lgtm")),
                         at=AT, verifier=fake_verifier())

    def test_work_is_never_minted_from_a_candidate_alone(self):
        proposal = journey.propose(
            journey.capture(journey.empty_store(), [observation()])["store"],
            goal="目标", sources=[MESSAGE_REF])
        with pytest.raises(journey.Refused):
            journey.open_work(proposal, {"state": "NEEDS_HUMAN", "actor_ref": None})
        assert proposal["work_ref"] is None


# ---------------------------------------------------------------------------
# 自称不算证据
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
        assert verdict["state"] == "FAILED" and verdict["accepted"] is False

    def test_a_run_that_did_not_run_is_not_a_pass(self):
        verdict = journey.run_verdict(
            {"name": "tiny-tests", "observed": {"ran": False}})
        assert verdict["state"] == "NOT_RUN" and verdict["accepted"] is False

    def test_a_claimed_done_run_stops_the_chain_before_any_report(self, tmp_path):
        record = walk(tmp_path, sender=Sender(),
                      run={"name": "tiny-tests", "claim": "DONE"})
        assert record["stop"] == "NOT_ACCEPTED"
        kinds = [step["kind"] for step in record["receipts"]]
        assert "report" not in kinds and "delivery" not in kinds

    def test_the_run_is_classified_by_the_report_modules_own_vocabulary(self):
        report = journey._daily_report()
        for run in ({"name": "x", "claim": "DONE"},
                    {"name": "x", "observed": {"ran": True, "exit_code": 0}},
                    {"name": "x", "observed": {"ran": True, "exit_code": 3}}):
            assert journey.run_verdict(run)["state"] == \
                report.classify_run_status([run])[0]["state"]


# ---------------------------------------------------------------------------
# 投递不是一个是/否
# ---------------------------------------------------------------------------

class TestDeliveryIsNotAYesOrNo:
    def test_no_recipient_means_pending_and_nothing_is_attempted(self, tmp_path):
        sender = Sender()
        record = journey.walk(full(delivery=None), out_dir=str(tmp_path / "report"),
                              sender=sender, at=AT, verifier=fake_verifier())
        assert record["stop"] == "PENDING"
        assert sender.calls == []
        assert not (tmp_path / "report" / "delivery-receipt.json").exists()

    def test_a_transport_that_says_failed_leaves_the_entry_pending(self, tmp_path):
        record = walk(tmp_path, sender=Sender(state="FAILED", why="收件端拒绝了"))
        assert record["stop"] == "UNKNOWN"
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
# 契约本身
# ---------------------------------------------------------------------------

class TestTheContractIsReadNotRestated:
    def test_the_module_and_the_contract_agree_on_every_closed_list(self):
        document = journey.contract()
        assert journey.capture_states() == tuple(
            row["id"] for row in document["capture_states"])
        assert journey.stops() == tuple(row["id"] for row in document["stops"])
        assert journey.receipt_kinds() == tuple(
            row["id"] for row in document["receipt_kinds"])
        assert journey.claim_kinds_that_proceed() == tuple(
            row["id"] for row in document["claim_kinds"]["proceeds_to_verification"])

    def test_the_authorization_block_names_the_actor_module_as_the_verifier(self):
        block = journey.contract()["authorization"]
        assert block["verifier"]["verified_by"] == "context/actor.py"
        assert block["unbound_verifier_stops"] == "NEEDS_HUMAN"
        assert "context_ref" in block["caller_supplies"]

    def test_every_receipt_carries_the_declared_fields(self, tmp_path):
        required = set(journey.contract()["required_fields"])
        record = walk(tmp_path, sender=Sender())
        assert record["receipts"]
        for step in record["receipts"]:
            assert required <= set(step), step

    def test_a_receipt_from_an_undeclared_step_cannot_be_written(self):
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
# 落盘那条界
# ---------------------------------------------------------------------------

class TestTheReportLandsOutsideTheRepo:
    def test_an_out_dir_inside_the_repo_is_refused(self):
        with pytest.raises(Exception) as caught:
            journey.walk(full(delivery=None), out_dir=str(RUNTIME), sender=None,
                         at=AT, verifier=fake_verifier())
        assert "本仓内" in str(caught.value)

    def test_a_relative_out_dir_is_refused(self):
        with pytest.raises(Exception) as caught:
            journey.walk(full(delivery=None), out_dir="report", sender=None,
                         at=AT, verifier=fake_verifier())
        assert "绝对路径" in str(caught.value)
