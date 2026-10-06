"""P5 —— 报告投递这一层的承重规矩。

这一层要回答的问题只有一个：**「发出去没有」这句话，凭什么说得出来。**

它容易出错的四个地方，每一条都对应这里的一组判据：

- **没人要求就发**。outbox 里没有记录时最容易的做法是「顺手发一份」；这里钉的是相反
  的方向——没有待发记录时连发送函数都不该被调用到；
- **没有消费者被说成失败**。「这次没有可用的通道」和「通道拒绝了」在报告里长得一样，
  而前者没有任何人试过。所以 PENDING_CONSUMER 不写回执，也不改状态；
- **重发**。一次中断的尝试之后，最容易的动作就是再发一次，而收件人会收到两遍。
  这里钉的是：结果不明先向通道核对，核对不了就保持待发；
- **词表有两份**。投递的四个词来自通知契约，这里不自己定义第五个，也不把认不出的
  词读成成功。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(
        "serveros_runtime_" + name, REPO_ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules["serveros_runtime_" + name] = module
    spec.loader.exec_module(module)
    return module


delivery = _load("report_delivery", "runtime/report_delivery.py")

REPORT = "一、今日重点\n\n把投递这一环闭上了\n"


# ────────────────────────────────────────────────────────────────────────────
# fixtures
# ────────────────────────────────────────────────────────────────────────────

def published(tmp_path: Path, text: str = REPORT) -> tuple:
    """一份已经发布过的报告目录，形状与 `daily_report.publish` 写出来的一致。

    形状照抄而不是简化：如果这里的夹具比真 producer 少一个字段，那么「回执指向的版本
    与文件不一致」这类判据就永远触发不了，而它们正是这份文档存在的理由。
    """
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    document = out / "daily-report.md"
    document.write_text(text, encoding="utf-8")
    payload = text.encode("utf-8")
    receipt = {
        "schema": "serveros/publication-receipt.v1",
        "report": {"schema": "serveros/periodic-report.v1",
                   "period": {"start": "2026-09-25", "end": "2026-09-25"},
                   "progress": "SOME",
                   "generated_at": "2026-09-25T12:00:00+00:00"},
        "document": {"file": str(document),
                     "sha256": hashlib.sha256(payload).hexdigest(),
                     "bytes": len(payload)},
    }
    (out / "publication-receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False), encoding="utf-8")
    return out, document, receipt


def republished(out: Path, text: str) -> str:
    """Re-compose the report in place. Returns the new sha256."""
    document = out / "daily-report.md"
    document.write_text(text, encoding="utf-8")
    payload = text.encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    path = out / "publication-receipt.json"
    receipt = json.loads(path.read_text(encoding="utf-8"))
    receipt["document"] = {"file": str(document), "sha256": digest,
                           "bytes": len(payload)}
    path.write_text(json.dumps(receipt, ensure_ascii=False), encoding="utf-8")
    return digest


class Sender:
    """An injected transport. Records every call and answers with what it is told."""

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


class Reconciler(Sender):
    """A transport that CAN be asked what it did with an earlier attempt."""

    def __init__(self, state="DELIVERED", why="上一次其实已经到达"):
        super().__init__(state=state, why=why)
        self.asked = []

    def reconcile(self, request):
        self.asked.append(request)
        return {"state": self.state, "why": self.why,
                "provider": {"id": "test-transport", "transport": "test"}}


def asked(out, **overrides):
    spec = {"recipient": "conv-1", "requested_by": "有人问过",
            "why": "要一份当天的日报"}
    spec.update(overrides)
    return delivery.request(out, **spec)


# ────────────────────────────────────────────────────────────────────────────
# 意图：什么可以进 outbox
# ────────────────────────────────────────────────────────────────────────────

class TestTheIntent:
    def test_a_request_with_no_receiver_is_refused_by_name(self, tmp_path):
        out, _, _ = published(tmp_path)
        with pytest.raises(delivery.Refused) as raised:
            asked(out, recipient="")
        assert "无人 receiver" in str(raised.value)

    def test_a_request_with_no_requester_is_refused(self, tmp_path):
        # 没有请求者的待发，和系统自己决定要发的通知，在 outbox 里长得一样。
        out, _, _ = published(tmp_path)
        with pytest.raises(delivery.Refused) as raised:
            asked(out, requested_by="")
        assert "谁要发的" in str(raised.value)

    def test_a_request_with_no_reason_is_refused(self, tmp_path):
        out, _, _ = published(tmp_path)
        with pytest.raises(delivery.Refused):
            asked(out, why="   ")

    def test_a_request_about_a_report_that_was_never_published_is_refused(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        with pytest.raises(delivery.Refused) as raised:
            asked(empty)
        assert "没有发布过" in str(raised.value)

    def test_a_request_that_names_a_version_the_file_no_longer_has_is_refused(self, tmp_path):
        # 发布回执说 A 版，文件现在是 B 版：投出去的话，收件人拿到的是一份没有任何
        # 回执为它背书的内容。
        out, document, _ = published(tmp_path)
        document.write_text("换过的正文\n", encoding="utf-8")
        with pytest.raises(delivery.Refused) as raised:
            asked(out)
        assert "sha256" in str(raised.value)

    def test_a_relative_out_dir_is_refused(self, tmp_path):
        with pytest.raises(delivery.Refused) as raised:
            delivery.request("out", recipient="c", requested_by="我", why="看")
        assert "绝对路径" in str(raised.value)

    def test_asking_twice_for_the_same_version_is_one_entry(self, tmp_path):
        out, _, _ = published(tmp_path)
        first = asked(out)
        second = asked(out)
        assert first["state"] == "REQUESTED"
        assert second["state"] == "ALREADY_REQUESTED"
        assert len(delivery.read_outbox(out)["entries"]) == 1

    def test_a_new_version_is_a_new_intent(self, tmp_path):
        out, _, _ = published(tmp_path)
        asked(out)
        republished(out, "一、今日重点\n\n又推进了一件事\n")
        second = asked(out)
        assert second["state"] == "REQUESTED"
        assert len(delivery.read_outbox(out)["entries"]) == 2

    def test_requesting_sends_nothing(self, tmp_path):
        out, _, _ = published(tmp_path)
        sender = Sender()
        asked(out)
        assert sender.calls == []
        assert delivery.pending_count(out) == 1


# ────────────────────────────────────────────────────────────────────────────
# 不发：哪些情况一条消息都不该出去
# ────────────────────────────────────────────────────────────────────────────

class TestWhatIsNotSent:
    def test_delivering_with_no_request_attempts_nothing(self, tmp_path):
        out, _, _ = published(tmp_path)
        sender = Sender()
        result = delivery.deliver(out, sender=sender,
                                  authorization={"authorized": True, "because": "有人问了"})
        assert result["state"] == "NOTHING_REQUESTED"
        assert sender.calls == []

    def test_no_consumer_is_pending_and_not_a_failure(self, tmp_path):
        # 「没有任何人试过」不能被读成「试过但失败了」。
        out, _, _ = published(tmp_path)
        asked(out)
        result = delivery.deliver(out, sender=None,
                                  authorization={"authorized": True, "because": "有人问了"})
        assert result["state"] == "PENDING_CONSUMER"
        assert "没有任何人试过" in result["why"]

    def test_no_consumer_writes_no_delivery_receipt(self, tmp_path):
        # 没有尝试，就没有结果可记。写一条 FAILED 会让人以为有人试过。
        out, _, _ = published(tmp_path)
        asked(out)
        delivery.deliver(out, sender=None,
                         authorization={"authorized": True, "because": "有人问了"})
        assert not delivery.receipt_file(out).exists()
        assert delivery.pending_count(out) == 1

    def test_a_delivery_with_no_authorization_is_refused(self, tmp_path):
        out, _, _ = published(tmp_path)
        asked(out)
        sender = Sender()
        with pytest.raises(delivery.Refused) as raised:
            delivery.deliver(out, sender=sender, authorization=None)
        assert "发送那一刻" in str(raised.value)
        assert sender.calls == []

    def test_an_authorization_without_a_reason_is_refused(self, tmp_path):
        out, _, _ = published(tmp_path)
        asked(out)
        with pytest.raises(delivery.Refused):
            delivery.deliver(out, sender=Sender(), authorization={"authorized": True})

    def test_an_authorization_that_rechecked_and_said_no_stops_the_send(self, tmp_path):
        # 静默期里记下的意图，醒来要重查授权；重查的结果可以是「不发了」。
        out, _, _ = published(tmp_path)
        asked(out)
        sender = Sender()
        with pytest.raises(delivery.Refused) as raised:
            delivery.deliver(out, sender=sender,
                             authorization={"authorized": False,
                                            "because": "收件人已经不在这个范围里了"})
        assert "已经不在这个范围里" in str(raised.value)
        assert sender.calls == []

    def test_a_report_that_moved_since_the_request_is_not_delivered(self, tmp_path):
        out, _, _ = published(tmp_path)
        asked(out)
        republished(out, "一、今日重点\n\n换了一份内容\n")
        sender = Sender()
        result = delivery.deliver(out, sender=sender,
                                  authorization={"authorized": True, "because": "有人问了"})
        assert result["state"] == "REFUSED"
        assert "重新" in result["why"] or "背书" in result["why"]
        assert sender.calls == []

    def test_a_failed_delivery_can_be_tried_again(self, tmp_path):
        # FAILED 是有人试过且知道结果，所以再试一次是有依据的；UNKNOWN 不是。
        out, _, _ = published(tmp_path)
        asked(out)
        outcome = {"authorized": True, "because": "有人问了"}
        delivery.deliver(out, sender=Sender(state="FAILED", why="通道拒了"),
                         authorization=outcome)
        assert delivery.pending_count(out) == 1
        retry = Sender()
        result = delivery.deliver(out, sender=retry, authorization=outcome)
        assert result["state"] == "DELIVERED"
        assert len(retry.calls) == 1


# ────────────────────────────────────────────────────────────────────────────
# 尝试：通道答的话怎么记
# ────────────────────────────────────────────────────────────────────────────

class TestTheAttempt:
    def test_a_delivered_attempt_records_the_reports_own_hash(self, tmp_path):
        out, _, receipt = published(tmp_path)
        asked(out)
        result = delivery.deliver(
            out, sender=Sender(),
            authorization={"authorized": True, "because": "有人问了"})
        assert result["state"] == "DELIVERED"
        recorded = delivery.read_receipt(out)
        assert recorded["report"]["sha256"] == receipt["document"]["sha256"]
        assert recorded["delivery"]["delivered"] is True

    def test_the_receipt_carries_the_transports_own_id(self, tmp_path):
        # 「回执丢了但远端已经接受」时要去问的那个 id，就是这一栏。
        out, _, _ = published(tmp_path)
        asked(out)
        delivery.deliver(out, sender=Sender(),
                         authorization={"authorized": True, "because": "有人问了"})
        assert delivery.read_receipt(out)["provider"]["remote_id"] == "t-1"

    def test_a_sender_that_raises_is_unknown_not_failed(self, tmp_path):
        # 抛异常的时候请求可能已经发出去了。记成 FAILED 会让人放心地再发一次。
        out, _, _ = published(tmp_path)
        asked(out)
        result = delivery.deliver(
            out, sender=Raising(),
            authorization={"authorized": True, "because": "有人问了"})
        assert result["state"] == "UNKNOWN_OUTCOME"
        assert delivery.read_receipt(out)["delivery"]["state"] == "UNKNOWN"
        assert delivery.read_receipt(out)["delivery"]["delivered"] is False

    def test_a_sender_that_raises_leaves_the_entry_pending(self, tmp_path):
        out, _, _ = published(tmp_path)
        asked(out)
        delivery.deliver(out, sender=Raising(),
                         authorization={"authorized": True, "because": "有人问了"})
        assert delivery.pending_count(out) == 1

    def test_a_transport_word_nobody_declared_is_not_read_as_success(self, tmp_path):
        out, _, _ = published(tmp_path)
        asked(out)
        result = delivery.deliver(
            out, sender=Sender(state="ACCEPTED", why="通道自己发明的词"),
            authorization={"authorized": True, "because": "有人问了"})
        assert result["state"] == "UNKNOWN"
        assert delivery.read_receipt(out)["delivery"]["delivered"] is False
        assert "ACCEPTED" in delivery.read_receipt(out)["delivery"]["why"]

    def test_a_sender_answer_with_no_reason_is_not_a_verdict(self, tmp_path):
        out, _, _ = published(tmp_path)
        asked(out)
        result = delivery.deliver(
            out, sender=lambda request: {"state": "DELIVERED"},
            authorization={"authorized": True, "because": "有人问了"})
        assert result["state"] == "UNKNOWN"

    def test_a_second_delivery_of_a_settled_intent_does_not_send_again(self, tmp_path):
        out, _, _ = published(tmp_path)
        asked(out)
        authorization = {"authorized": True, "because": "有人问了"}
        first = Sender()
        delivery.deliver(out, sender=first, authorization=authorization)
        second = Sender()
        result = delivery.deliver(out, sender=second, authorization=authorization)
        assert result["state"] == "ALREADY_DELIVERED"
        assert second.calls == []
        assert len(first.calls) == 1

    def test_a_settled_entry_is_no_longer_pending(self, tmp_path):
        out, _, _ = published(tmp_path)
        asked(out)
        delivery.deliver(out, sender=Sender(),
                         authorization={"authorized": True, "because": "有人问了"})
        assert delivery.pending_count(out) == 0

    def test_asking_again_after_a_delivery_leaves_no_phantom_waiter(self, tmp_path):
        """A request that follows a settled delivery is one thing, not two.

        Appending a second entry sends nothing — `deliver` reads the receipt for
        that key first and answers ALREADY_DELIVERED — but it leaves a row in the
        outbox that says "waiting", and every reader of that file (the pending
        count, a health table, the next run) counts it as work outstanding. The
        evening brief is where this became routine rather than rare: its version
        is now stable for a whole day, so a runner that fires twice — a retry, a
        manual re-run after configuring a recipient — asks twice about exactly
        the same bytes.
        """
        out, _, _ = published(tmp_path)
        asked(out)
        delivery.deliver(out, sender=Sender(),
                         authorization={"authorized": True, "because": "有人问了"})

        again = asked(out)

        assert again["state"] == "ALREADY_REQUESTED"
        entries = json.loads(
            Path(delivery.outbox_file(out)).read_text())["entries"]
        assert len(entries) == 1
        assert delivery.pending_count(out) == 0

    def test_a_withdrawn_request_can_be_asked_again(self, tmp_path):
        """Withdrawal is not a settled outcome, so it is the one state that must
        not be deduplicated: a person who withdrew and then changed their mind
        about the same version needs a pending entry, not a refusal.
        """
        out, _, _ = published(tmp_path)
        asked(out)
        delivery.withdraw(out, because="先不发了")

        again = asked(out)

        assert again["state"] == "REQUESTED"
        assert delivery.pending_count(out) == 1

    def test_an_interrupted_attempt_is_not_re_sent_when_the_transport_cannot_be_asked(self, tmp_path):
        # 回执丢失但远端可能已经接受：核对不了就不重发，保持待发。
        out, _, _ = published(tmp_path)
        asked(out)
        authorization = {"authorized": True, "because": "有人问了"}
        delivery.deliver(out, sender=Raising(), authorization=authorization)
        second = Sender()
        result = delivery.deliver(out, sender=second, authorization=authorization)
        assert result["state"] == "UNKNOWN_OUTCOME"
        assert second.calls == []
        assert delivery.pending_count(out) == 1

    def test_an_interrupted_attempt_the_transport_can_answer_is_settled_with_no_second_send(self, tmp_path):
        out, _, _ = published(tmp_path)
        asked(out)
        authorization = {"authorized": True, "because": "有人问了"}
        delivery.deliver(out, sender=Raising(), authorization=authorization)
        reconciler = Reconciler()
        result = delivery.deliver(out, sender=reconciler, authorization=authorization)
        assert result["state"] == "DELIVERED"
        assert reconciler.calls == []          # 没有重发
        assert len(reconciler.asked) == 1      # 但是问了
        assert delivery.pending_count(out) == 0

    def test_the_question_carries_the_previous_receipt_and_not_only_its_answer(self, tmp_path):
        """问的那一方要能说出「这个问题现在还能不能安全地问」。

        一条通道如果只能靠「同一个键再调一次」来回答，那么它的答案是有时效的，
        而时效要跟尝试发生的时间比。只把上一次的 `provider` 块交出去，时间就没了，
        「问不出口」会被当成「可以问」——在那种通道上就是第二封消息。
        """
        out, _, _ = published(tmp_path)
        asked(out)
        authorization = {"authorized": True, "because": "有人问了"}
        delivery.deliver(out, sender=Raising(), authorization=authorization)
        reconciler = Reconciler()
        delivery.deliver(out, sender=reconciler, authorization=authorization)
        handed = reconciler.asked[0]["previous"]
        assert handed.get("delivery", {}).get("attempted_at"), (
            "上一次尝试发生在什么时候，必须在被问的那一方手里")
        assert handed.get("report"), "要核对的是哪一版报告，也必须在一起"
        assert handed.get("intent")

    def test_a_reconciliation_that_cannot_confirm_leaves_it_pending(self, tmp_path):
        out, _, _ = published(tmp_path)
        asked(out)
        authorization = {"authorized": True, "because": "有人问了"}
        delivery.deliver(out, sender=Raising(), authorization=authorization)
        result = delivery.deliver(
            out, sender=Reconciler(state="UNKNOWN", why="查不到"),
            authorization=authorization)
        assert result["state"] == "UNKNOWN"
        assert result["delivery"]["delivered"] is False

    def test_the_attempt_is_marked_before_the_send_not_only_after(self, tmp_path):
        """一次在「发出去」和「记下来」之间崩掉的运行，必须留下痕迹。"""
        out, _, _ = published(tmp_path)
        asked(out)
        seen = {}

        def look(request):
            recorded = delivery.read_receipt(out)
            seen["state"] = (recorded or {}).get("delivery", {}).get("state")
            return {"state": "DELIVERED", "why": "到了"}

        delivery.deliver(out, sender=look,
                         authorization={"authorized": True, "because": "有人问了"})
        assert seen["state"] == "UNKNOWN"

    def test_an_unreadable_receipt_is_refused_not_read_as_absent(self, tmp_path):
        # 「没有回执」和「回执在那儿但读不出来」是两句话，后者说明有人试过。
        out, _, _ = published(tmp_path)
        asked(out)
        delivery.receipt_file(out).write_text("{ 这不是 JSON", encoding="utf-8")
        with pytest.raises(delivery.Refused):
            delivery.deliver(out, sender=Sender(),
                             authorization={"authorized": True, "because": "有人问了"})

    def test_an_unreadable_outbox_is_refused_not_read_as_empty(self, tmp_path):
        # 一份读不出来的 outbox 被当成空的话，本该待发的投递会安静地不发生。
        out, _, _ = published(tmp_path)
        delivery.outbox_file(out).write_text("{ 也不是 JSON", encoding="utf-8")
        with pytest.raises(delivery.Refused):
            delivery.pending_count(out)

    def test_an_outbox_with_a_foreign_schema_is_refused(self, tmp_path):
        out, _, _ = published(tmp_path)
        delivery.outbox_file(out).write_text(
            json.dumps({"schema": "something/else", "entries": []}), encoding="utf-8")
        with pytest.raises(delivery.Refused) as raised:
            delivery.read_outbox(out)
        assert "schema" in str(raised.value)


# ────────────────────────────────────────────────────────────────────────────
# 撤回
# ────────────────────────────────────────────────────────────────────────────

class TestWithdraw:
    def test_withdrawing_stops_the_next_attempt(self, tmp_path):
        out, _, _ = published(tmp_path)
        asked(out)
        result = delivery.withdraw(out, because="不需要了")
        assert result["state"] == "WITHDRAWN"
        sender = Sender()
        outcome = delivery.deliver(
            out, sender=sender,
            authorization={"authorized": True, "because": "有人问了"})
        assert outcome["state"] == "NOTHING_REQUESTED"
        assert sender.calls == []

    def test_withdrawing_does_not_claim_to_unsend(self, tmp_path):
        # 撤回的是「下一次」，不是「已经到达的那一封」。
        out, _, _ = published(tmp_path)
        asked(out)
        delivery.deliver(out, sender=Sender(),
                         authorization={"authorized": True, "because": "有人问了"})
        result = delivery.withdraw(out, because="不需要了")
        assert result["state"] == "NOTHING_PENDING"
        assert delivery.read_receipt(out)["delivery"]["state"] == "DELIVERED"


# ────────────────────────────────────────────────────────────────────────────
# 词表
# ────────────────────────────────────────────────────────────────────────────

class TestTheVocabulary:
    def test_the_delivery_words_come_from_the_notification_contract(self):
        import yaml
        contract = yaml.safe_load(
            (REPO_ROOT / "runtime" / "notification-contract.yaml")
            .read_text(encoding="utf-8"))
        declared = contract["alerts"]["delivery"]["values"]
        assert list(delivery.delivery_states()) == list(declared)

    def test_the_outbox_has_no_delivery_word_in_it(self):
        # 待发的生命周期与投递的结果是两套词。混在一起的那天，「还没有消费者」
        # 会开始读起来像「送失败了」。
        assert set(delivery.OUTBOX_STATES) & set(delivery.delivery_states()) == set()
        assert "PENDING_CONSUMER" not in delivery.OUTBOX_STATES

    def test_pending_is_a_reason_an_entry_has_not_settled_not_a_fifth_outcome(self):
        assert delivery.OUTBOX_STATES == ("PENDING", "WITHDRAWN")
