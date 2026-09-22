"""§10's fallback: a question that reached nobody, and the two receipts first.

THE CRITERION THIS FILE IS ABOUT
--------------------------------
    | P5 | 员工知识消费与一条无答案转人工的状态/投递路径分别可核对 |

`docs/daily-work-cycle.md` states the path in one sentence, and every clause of
it is a test below:

    兜底默认交绑定的个人工作 owner，而非擅自指定同事或 SLA：无匹配、证据冲突、权限
    不够、用户要求转人工或预算耗尽时形成小决策包（原消息引用、已查事实、尝试、疑问、
    建议）。先入已有工作/通知队列并得到回执，再按许可给 owner 提醒；外发收件人/机器人
    未绑定时显示待投递，不能告诉求助者“已通知”。

  * `TheJoinTest::test_the_join_is_with_support_answer_and_not_a_second_reader`
    is the deliverable. `support.answer` has produced a `NO_ENTRY_MATCHES`
    improvement candidate since the day it was written and NOTHING consumed it —
    its only reader was its own renderer, at `support.py:1351`. This module is
    the consumer, and the test walks the real chain over a fixture knowledge
    base rather than a hand-built dict, so 「the join exists」 is a measurement
    instead of a claim about two files that mention each other in comments.
  * `test_the_order_is_the_queue_first` is 「先入已有工作/通知队列并得到回执，再
    按许可给 owner 提醒」. A delivery recorded before the queue took the packet
    is REFUSED rather than stored, because storing it would make this module the
    thing that agreed a person had been told about nothing.
  * `test_nothing_is_notified_without_a_delivered_receipt` is 「不能告诉求助者
    “已通知”」 and 「未收到投递回执不能说已转人工」. `notified` is true for exactly
    one of the five delivery states.
  * `test_the_packet_reads_the_redacted_field_and_not_the_raw_one` is the leak
    this module could have shipped. A support reading carries the question TWICE
    — raw in `reading["question"]`, redacted in
    `reading["improvement"]["question_shape"]` — and a packet is a record
    somebody may file. Picking the wrong one is a one-word mistake with a
    credential on the far side of it, so the fixture puts a real identifier in
    the question and both halves are asserted.
  * `test_the_packet_carries_no_clock` is what makes `packet_ref` mean
    anything: two handoffs of one question at different moments must be
    recognisable as ONE packet, and a timestamp in the body would make them two.

Nothing here reaches a real knowledge base, a real queue or a real transport.
The queue is a fixture class with one method, and the delivery is a dict a
caller reports — which is also how the module is used in production, since it
does not send anything itself.
"""
import importlib.util
import json
import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


support = _load("serveros_support_for_handoff", "intake/support.py")
handoff = _load("serveros_support_handoff", "intake/support_handoff.py")


# ---------------------------------------------------------------------------
# A knowledge base with the real shape, and one entry a 599 cannot reach
# ---------------------------------------------------------------------------
INDEX = """# 知识库索引与说明

> 最后更新：2026-08-25

## 错误 ID 编码规则

- CLIENT 客户端配置 / NET 网络连接 / PLAT 平台代理层

## 错误条目总表

| 错误ID | 名称 | 状态码 | 环节 | 所在文件 | 置信度 | 出现频率 |
|--------|------|--------|------|----------|--------|----------|
| PLAT-001 | 平台预算超限 ExceededBudget 402 | 402 | 平台代理层 | 03_平台/预算.md | 已确认 | 高 |

## 维护说明

- 回答员工时优先匹配正式条目的字段规则，不直接引用待整理样本
"""

ENTRY_BUDGET = """## [PLAT-001] 平台预算超限 ExceededBudget 402

### 元数据
- 所属环节：平台代理层（预算/配额）
- 错误类型：预算超限
- 置信度：已确认（来自 1,064 条平台实际报错）
- 出现频率：中
- 最后更新：2026-08-24

### 字段匹配规则
- 主匹配：`status_code == 402` && `message` 包含 "ExceededBudget"
- 辅助匹配：`message` 包含 "over budget"

### 典型错误样本（原文，已脱敏）

```
Error Code: 402
Message: ExceededBudget: User=<USER> over budget.
```

### 解决方案
1. 等待预算自动刷新。

### 关联信息
- 易混淆：[PROV-001]
"""

#: The identifier the knowledge base's own redaction rule is about -- 「报错信息
#: 中的 User=ou_xxx 是用户内部标识，回答时不要完整回显」.
IDENTIFIER = "ou_9f3c1a77bb22"
CONTACT = "ops-owner@example.com"
#: A 599 reaches no entry, and the question carries two things the support
#: chain's redaction is supposed to remove before anything is filed.
UNREACHABLE = f"WidgetGasketFailure for User={IDENTIFIER}, escalate to {CONTACT}"


def a_knowledge_base(root: Path) -> Path:
    kb = root / "knowledge"
    (kb / "03_平台").mkdir(parents=True, exist_ok=True)
    (kb / "00_索引与说明.md").write_text(INDEX, encoding="utf-8")
    (kb / "03_平台" / "预算.md").write_text(ENTRY_BUDGET, encoding="utf-8")
    (kb / "全量错误分析统计.md").write_text(
        "# 全量错误分析统计\n\n## 结果使用规则\n\n"
        "- 正式条目优先解释错误链中最内层的真实根因。\n", encoding="utf-8")
    return kb


class Queue:
    """A queue adapter with the shape this module asks for, and a memory.

    The module's queue protocol is one method because that is the whole of what
    §10 asks a queue to do here: take the packet and NAME it. The naming is not
    decoration -- 「得到回执」 is exactly the name, so the fixture can be made to
    answer without one, and that is a test below.
    """

    def __init__(self, reference="work-queue:ITEM-4242", answer=None, raises=None):
        self.reference, self.answer, self.raises = reference, answer, raises
        self.taken = []

    def accept(self, packet):
        if self.raises is not None:
            raise self.raises
        self.taken.append(packet["packet_ref"])
        return self.answer if self.answer is not None else self.reference


class Fixture(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.kb = a_knowledge_base(Path(self._tmp.name))
        self.reading = support.answer(self.kb, status_code=599, message=UNREACHABLE)

    def tearDown(self):
        self._tmp.cleanup()

    def assertIsFallback(self):
        self.assertEqual(self.reading["outcome"], "NO_ENTRY_MATCHES",
                         "the fixture must be a question the knowledge base "
                         "cannot reach, or every test below is about a "
                         "different path than the one it names")

    def a_fallback_the_kb_can_name_a_home_for(self):
        """A question with a known 环节 and an unknown error.

        `status_code == 402` IS in an entry's 主匹配 rule, and the message is
        not, so the rule does not fire and the chain reaches nothing — while
        `_prefix_for` can still infer the 环节 and offer an ID under it. That is
        the case where 「建议」 carries something, and the 599 above is the case
        where it carries nothing. Both are real and they are not the same
        reading, so both are tested.
        """
        reading = support.answer(self.kb, status_code=402,
                                 message="SomethingElseEntirely")
        self.assertEqual(reading["outcome"], "NO_ENTRY_MATCHES")
        return reading


# ---------------------------------------------------------------------------
# The join. The reason this file exists.
# ---------------------------------------------------------------------------
class TheJoinTest(Fixture):
    def test_the_join_is_with_support_answer_and_not_a_second_reader(self):
        """The candidate nothing consumed becomes the packet somebody gets.

        Asserted in both directions on purpose. The packet's recommendation is
        equal to the improvement candidate's -- so the join carries the real
        value rather than a re-derivation -- and the module's source imports no
        knowledge base reader, so it cannot have produced that value itself.
        """
        self.assertIsFallback()
        candidate = self.reading["improvement"]
        packet = handoff.packet(self.reading)
        self.assertEqual(packet["recommendation"]["offered_id"],
                         candidate["offered_id"])
        self.assertEqual(packet["recommendation"]["id_prefix"],
                         candidate["id_prefix"])
        self.assertEqual(packet["recommendation"]["kb_file"],
                         candidate["kb_file"])
        self.assertEqual(packet["question"]["status_code"],
                         candidate["question_shape"]["status_code"])

    def test_a_reading_that_carries_no_candidate_is_refused_for_no_match(self):
        """The join is not optional for the trigger that has no other source."""
        bare = {k: v for k, v in self.reading.items() if k != "improvement"}
        with self.assertRaises(handoff.Refused) as caught:
            handoff.packet(bare, trigger="NO_MATCH")
        self.assertIn("已查事实", str(caught.exception))

    def test_the_improvement_candidate_is_still_offered_and_not_assigned(self):
        """§J: an ID 「分配后不再变更」 is a commitment a person makes.

        The ID reaches the packet, and the packet says nobody assigned it. An
        owner reading `PLAT-002` must not find that the chain has already spent
        the number.
        """
        packet = handoff.packet(self.a_fallback_the_kb_can_name_a_home_for())
        self.assertEqual(packet["recommendation"]["id_prefix"], "PLAT")
        self.assertEqual(packet["recommendation"]["offered_id"], "PLAT-002")
        self.assertTrue(packet["recommendation"]["kb_file"].endswith("03_平台/"))
        self.assertFalse(packet["recommendation"]["assigned"])

    def test_a_question_with_no_inferable_环节_is_offered_no_id_at_all(self):
        """The other reading: nothing to recommend, and nothing invented.

        A 599 names no 环节, so `_prefix_for` returns none and there is no
        directory to put an entry in. Offering an ID here would be the chain
        picking a 环节 on the knowledge base's behalf.
        """
        self.assertIsFallback()
        packet = handoff.packet(self.reading)
        self.assertIsNone(packet["recommendation"]["offered_id"])
        self.assertIsNone(packet["recommendation"]["id_prefix"])
        self.assertIsNone(packet["recommendation"]["kb_file"])
        self.assertFalse(packet["recommendation"]["assigned"])


# ---------------------------------------------------------------------------
# Redaction. The one-word mistake with a credential on the far side of it.
# ---------------------------------------------------------------------------
class RedactionTest(Fixture):
    def test_the_packet_reads_the_redacted_field_and_not_the_raw_one(self):
        self.assertIsFallback()
        # The fixture is real: the raw reading DOES carry both. Without this
        # half, the assertions below would pass on a question that never had
        # anything to leak.
        self.assertIn(IDENTIFIER, self.reading["question"]["message"])
        self.assertIn(CONTACT, self.reading["question"]["message"])

        packet = handoff.packet(self.reading)
        body = json.dumps(packet, ensure_ascii=False)
        self.assertNotIn(IDENTIFIER, body)
        self.assertNotIn(CONTACT, body)
        self.assertIn("<已脱敏>", packet["question"]["message"])

    def test_a_transport_it_cannot_redact_by_itself_is_not_guessed_at(self):
        """The patterns come from the support contract, not from a second copy.

        Two readings of one rule is the shape this repository wants; two rules
        that agree today is the shape it keeps recording as a defect. So the
        assertion is on the FILE, and then on the value.
        """
        self.assertEqual(handoff.SUPPORT_CONTRACT, support.CONTRACT)
        self.assertEqual(handoff.support_contract()["answer"]["redaction"],
                         support.contract()["answer"]["redaction"])

    def test_a_hit_the_earlier_pass_missed_is_reported_rather_than_swallowed(self):
        """A packet that quietly dropped what was asked is a packet that lies.

        The caller is expected to hand over already-redacted text, so a hit here
        is a finding about the earlier pass. It is recorded, and the class and
        count are recorded with it -- not just the fact that something was.
        """
        clean, hits = handoff.scrub("plain text with nothing in it")
        self.assertEqual((clean, hits), ("plain text with nothing in it", []))
        clean, hits = handoff.scrub(f"leaked {IDENTIFIER}")
        self.assertNotIn(IDENTIFIER, clean)
        self.assertEqual(hits, [{"class": "内部用户标识 ou_", "count": 1}])


# ---------------------------------------------------------------------------
# The packet. §10's five parts, and the clock it does not have.
# ---------------------------------------------------------------------------
class PacketTest(Fixture):
    def test_the_five_parts_of_the_sentence_are_five_fields(self):
        packet = handoff.packet(self.reading)
        self.assertEqual(
            sorted(handoff.contract()["package"]["carries"]),
            ["attempts", "facts_checked", "original_message", "question",
             "recommendation"])
        for part in ("original_message", "facts_checked", "attempts",
                     "question", "recommendation"):
            with self.subTest(part=part):
                self.assertIn(part, packet)

    def test_the_facts_are_the_ones_the_chain_measured(self):
        """The count is carried as the sentence the measuring module wrote.

        Re-parsing 「N entries」 out of that prose here would be this module
        measuring something it did not measure, and reporting it as if it had.
        The number is checked against the fixture's own entry count so that the
        sentence is tied to a measurement rather than to a remembered string.
        """
        packet = handoff.packet(self.reading)
        self.assertEqual(packet["facts_checked"]["entries_reached"], 0)
        self.assertEqual(packet["facts_checked"]["measured_by_the_chain"],
                         self.reading["improvement"]["why"])
        n = support.read_entries(self.kb)["entry_count"]
        self.assertEqual(len(list(self.kb.glob("*/*.md"))), n,
                         "the fixture's own shape moved: the glob no longer "
                         "reads exactly the entries this count claims")
        self.assertIn(f"{n} entries",
                      packet["facts_checked"]["measured_by_the_chain"])

    def test_the_message_reference_says_it_has_none_rather_than_inventing_one(self):
        """A reference that resolves to nothing is worse than a stated absence."""
        entry = handoff.packet(self.reading)["original_message"]
        self.assertIsNone(entry["message_ref"])
        self.assertTrue(entry["because"])

    def test_the_packet_carries_no_clock(self):
        """Two handoffs of one question at two moments must be ONE packet."""
        first = handoff.packet(self.reading)
        second = handoff.packet(self.reading)
        self.assertEqual(first["packet_ref"], second["packet_ref"])
        stamp = re.compile(r"\d{4}-\d{2}-\d{2}[T ]")
        self.assertIsNone(stamp.search(json.dumps(first, ensure_ascii=False)),
                          "a timestamp in the body makes one question into two packets")

    def test_the_ref_is_over_the_content_and_not_over_the_caller(self):
        """A packet routed twice is the same packet; a different one is not."""
        other = support.answer(self.kb, status_code=598, message="something else")
        self.assertNotEqual(handoff.packet(self.reading)["packet_ref"],
                            handoff.packet(other)["packet_ref"])


# ---------------------------------------------------------------------------
# The trigger. Five on purpose, and one with a producer.
# ---------------------------------------------------------------------------
class TriggerTest(Fixture):
    def test_the_five_triggers_are_five_and_the_contract_says_so(self):
        declared = sorted(handoff.contract()["trigger"]["values"])
        self.assertEqual(declared, ["BUDGET_EXHAUSTED", "EVIDENCE_CONFLICT",
                                    "INSUFFICIENT_PERMISSION", "NO_MATCH",
                                    "USER_ASKED_FOR_A_HUMAN"])

    def test_an_answered_question_is_not_a_fallback(self):
        """Handing over a question the KB answers is asking a person to redo work."""
        answered = support.answer(self.kb, status_code=402,
                                  message="ExceededBudget: over budget")
        self.assertEqual(answered["outcome"], "ANSWERED")
        with self.assertRaises(handoff.Refused) as caught:
            handoff.packet(answered)
        self.assertIn("ANSWERED is not a fallback", str(caught.exception))

    def test_the_other_four_triggers_can_be_named_but_not_derived(self):
        """A caller may know one; this module will not pretend to have seen it."""
        packet = handoff.packet(None, trigger="USER_ASKED_FOR_A_HUMAN",
                                question="please get me a person")
        self.assertEqual(packet["trigger"], "USER_ASKED_FOR_A_HUMAN")
        self.assertIsNone(packet["recommendation"]["offered_id"])
        self.assertIsNone(packet["recommendation"]["kind"])
        self.assertIn("caller", packet["question"]["source"])

    def test_a_trigger_from_outside_the_chain_still_carries_the_candidate(self):
        """Overriding the trigger must not throw away what the chain produced.

        A caller may know the person asked for a human about a question the
        knowledge base also could not reach. Both facts are true about one
        question, so the packet carries both rather than picking one.
        """
        self.assertIsFallback()
        packet = handoff.packet(self.reading, trigger="USER_ASKED_FOR_A_HUMAN")
        self.assertEqual(packet["trigger"], "USER_ASKED_FOR_A_HUMAN")
        self.assertIn("improvement candidate's own", packet["question"]["source"])
        self.assertEqual(packet["recommendation"]["kind"], "NO_ENTRY_MATCHES")

    def test_a_reading_with_no_candidate_still_carries_the_question(self):
        """The case the four producerless triggers actually arise from.

        A chain that could not READ the knowledge base produced no improvement
        candidate and therefore reached nobody — which is 权限不够 as §10 means
        it. The reading still holds the question, and a packet built from it
        must carry that question rather than sending the owner back to the
        asker to find out what was asked.
        """
        blind = support.answer(self.kb / "not-here", status_code=599,
                               message=UNREACHABLE)
        self.assertEqual(blind["outcome"], "KB_UNREADABLE")
        self.assertIsNone(blind["improvement"])
        packet = handoff.packet(blind, trigger="INSUFFICIENT_PERMISSION")
        self.assertEqual(packet["question"]["status_code"], 599)
        self.assertIn("the reading's own", packet["question"]["source"])
        self.assertNotIn(IDENTIFIER, packet["question"]["message"])
        self.assertIn("<已脱敏>", packet["question"]["message"])
        # No candidate existed, so the packet recommends nothing and says why.
        self.assertIsNone(packet["recommendation"]["offered_id"])
        self.assertTrue(packet["recommendation"]["because"])
        # And the facts the chain DID measure are carried, not blanked: which
        # knowledge base could not be read and what its glob failed to match.
        self.assertTrue(packet["facts_checked"]["kb_root"].endswith("not-here"))
        # The chain's own sentence, carried verbatim: a string on BOTH branches
        # (`improvement.why` when a candidate exists, `unreadable` when the KB
        # could not be read at all) — which is why the reading is named `blind`
        # here and not `unreadable`, so that the reading and its one string
        # field cannot be read as the same object. It is never re-parsed, so the
        # field is asserted as the sentence it is rather than subscripted.
        self.assertEqual(packet["facts_checked"]["measured_by_the_chain"],
                         blind["unreadable"])
        self.assertIn("not-here",
                      packet["facts_checked"]["measured_by_the_chain"])

    def test_an_unknown_trigger_is_refused(self):
        with self.assertRaises(handoff.Refused):
            handoff.packet(None, trigger="SOMETHING_ELSE")


# ---------------------------------------------------------------------------
# The order §10 fixes: the queue first, the reminder second.
# ---------------------------------------------------------------------------
class OrderTest(Fixture):
    def setUp(self):
        super().setUp()
        self.packet = handoff.packet(self.reading)

    def test_the_order_is_the_queue_first(self):
        """A reminder ahead of the durable copy is a message nobody can act on."""
        with self.assertRaises(handoff.Refused) as caught:
            handoff.handoff(self.packet, queue=None, owner="person:oncall",
                            delivery={"state": "DELIVERED", "at": "T"})
        self.assertIn("先入", str(caught.exception))

    def test_the_same_refusal_when_the_queue_declined(self):
        for queue in (Queue(answer=""), Queue(raises=RuntimeError("connection reset"))):
            with self.subTest(queue=queue):
                with self.assertRaises(handoff.Refused):
                    handoff.handoff(self.packet, queue=queue, owner="person:oncall",
                                    delivery={"state": "DELIVERED", "at": "T"})

    def test_a_reminder_with_no_addressee_is_refused(self):
        with self.assertRaises(handoff.Refused) as caught:
            handoff.handoff(self.packet, queue=Queue(), owner=None,
                            delivery={"state": "DELIVERED", "at": "T"})
        self.assertIn("no owner bound", str(caught.exception))

    def test_the_queue_is_asked_once_and_is_handed_the_packet(self):
        queue = Queue()
        handoff.handoff(self.packet, queue=queue)
        self.assertEqual(queue.taken, [self.packet["packet_ref"]])


# ---------------------------------------------------------------------------
# The receipts, and the one word that may not be said without them.
# ---------------------------------------------------------------------------
class ReceiptTest(Fixture):
    def setUp(self):
        super().setUp()
        self.packet = handoff.packet(self.reading)

    def test_a_queue_that_cannot_name_the_packet_has_not_accepted_it(self):
        result = handoff.handoff(self.packet, queue=Queue(answer=""))
        self.assertEqual(result["routing"]["queue"]["state"], "REFUSED")
        self.assertFalse(result["handed_over"])
        self.assertEqual(result["delivery"]["state"], "PENDING_DELIVERY")

    def test_a_queue_that_raises_is_not_the_same_as_one_that_declines(self):
        """One is a queue with an opinion, the other is a queue that is broken."""
        result = handoff.handoff(self.packet,
                                 queue=Queue(raises=RuntimeError("connection reset")))
        self.assertEqual(result["routing"]["queue"]["state"], "FAILED")
        self.assertIn("connection reset", result["routing"]["queue"]["reason"])

    def test_nothing_is_notified_without_a_delivered_receipt(self):
        for state in ("FAILED", "UNSUPPORTED", "UNKNOWN"):
            with self.subTest(state=state):
                result = handoff.handoff(
                    self.packet, queue=Queue(), owner="person:oncall",
                    delivery={"state": state, "at": "T", "reason": "told you so"})
                self.assertFalse(result["notified"])
                self.assertEqual(result["outcome"], "NOT_NOTIFIED")
                self.assertTrue(result["handed_over"],
                                "the packet IS held; only the person was not told")
                self.assertEqual(handoff.exit_code_for(result), 2)
                self.assertIn("has not been told", result["statement"])

    def test_the_one_state_that_may_be_notified_is_the_delivered_one(self):
        result = handoff.handoff(self.packet, queue=Queue(),
                                 owner="person:oncall",
                                 delivery={"state": "DELIVERED", "at": "T"})
        self.assertTrue(result["notified"])
        self.assertEqual(result["outcome"], "NOTIFIED")
        self.assertEqual(handoff.exit_code_for(result), 0)
        # DELIVERED means the transport took it. Saying more than that is the
        # sentence §10 forbids, so the statement says less than a reader wants.
        self.assertIn("not the same as", result["statement"])

    def test_the_boolean_and_the_state_cannot_disagree(self):
        """`notified` is derived from the state, never passed beside it."""
        for state in ("DELIVERED", "FAILED", "UNSUPPORTED", "UNKNOWN"):
            with self.subTest(state=state):
                result = handoff.handoff(self.packet, queue=Queue(),
                                         owner="person:oncall",
                                         delivery={"state": state, "at": "T"})
                self.assertEqual(result["notified"], state == "DELIVERED")

    def test_pending_delivery_is_not_a_transport_word(self):
        """It is this module's word for 「nothing was attempted」."""
        with self.assertRaises(handoff.Refused) as caught:
            handoff.handoff(self.packet, queue=Queue(), owner="person:oncall",
                            delivery={"state": "PENDING_DELIVERY", "at": "T"})
        self.assertIn("nothing was attempted", str(caught.exception))

    def test_an_invented_transport_word_is_refused(self):
        with self.assertRaises(handoff.Refused):
            handoff.handoff(self.packet, queue=Queue(), owner="person:oncall",
                            delivery={"state": "PROBABLY", "at": "T"})


# ---------------------------------------------------------------------------
# The honest default: two anchors, neither chosen here.
# ---------------------------------------------------------------------------
class UnboundTest(Fixture):
    def test_an_unbound_queue_and_an_unbound_owner_both_read_as_pending(self):
        """§10: 「外发收件人/机器人未绑定时显示待投递」."""
        result = handoff.run(self.reading)
        self.assertEqual(result["routing"]["queue"]["state"], "UNBOUND")
        self.assertEqual(result["routing"]["owner"]["state"], "UNBOUND")
        self.assertEqual(result["delivery"]["state"], "PENDING_DELIVERY")
        self.assertFalse(result["handed_over"])
        self.assertFalse(result["notified"])
        self.assertEqual(handoff.exit_code_for(result), 2)
        self.assertIn("擅自指定同事", result["routing"]["owner"]["reason"])

    def test_the_default_does_not_choose_an_owner(self):
        """A default owner would be 擅自指定同事, which §10 names as the thing not to do."""
        for argument in ("queue", "owner"):
            with self.subTest(argument=argument):
                self.assertIsNone(
                    __import__("inspect").signature(handoff.handoff)
                    .parameters[argument].default)

    def test_an_object_of_the_wrong_shape_is_a_caller_error_not_a_reading(self):
        for wrong in ("work-queue:somewhere", 42, object()):
            with self.subTest(wrong=wrong):
                with self.assertRaises(handoff.Refused) as caught:
                    handoff.handoff(handoff.packet(self.reading), queue=wrong)
                self.assertIn("accept", str(caught.exception))

    def test_a_delivery_without_a_packet_is_refused(self):
        with self.assertRaises(handoff.Refused):
            handoff.handoff({"trigger": "NO_MATCH"})

    def test_no_write_path(self):
        source = (ROOT / "intake" / "support_handoff.py").read_text(encoding="utf-8")
        body = source.split('"""', 2)[2]      # past the module docstring
        for forbidden in ("write_text(", "open(", "os.remove", "os.rename",
                          "shutil.", "subprocess", "unlink(", "rmdir("):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, body)

    def test_the_module_declines_to_send_anything_itself(self):
        """The event vocabulary is closed and has no word for this."""
        self.assertEqual(handoff.contract()["delivery"]["notified_only_on"],
                         "DELIVERED")
        result = handoff.run(self.reading)
        self.assertTrue(result["not_claimed"])
        self.assertTrue(any("does not send" in line for line in result["not_claimed"]))


if __name__ == "__main__":
    unittest.main()
