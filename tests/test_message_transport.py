"""P5 —— 把一份日报送到一个人手上的那半边。

这一层要回答的问题：**「送到了」这句话，是通道说的，还是我们自己想的。**

它容易出错的五个地方，每一条都对应这里的一组判据：

- **收件人被猜出来**。这个通道有两个互斥的目的地参数（一个人 / 一个群），
  不加前缀就等于让适配器替调用方选，而选错的后果收不回来；
- **把「受理」说成「看到」**。这条通道没有第二个回执可等，所以受理**就是**
  投递；但不能因此编一个通道没给过的确认 —— 回执要带通道自己给的 message id；
- **字节对不上还发**。报告在请求之后被重新生成过时，发出去的那一版和回执背书的
  那一版不是同一份，这正是让投递回执变得一文不值的那个失败；
- **对不上的旧尝试被当成可以重发**。这条通道的去重只在一个窗口内成立，窗口之外
  同一个键会真的再发一封；所以「问一句」只能在窗口内问，窗口外只能留待发；
- **把兼容当替代**。这一层不自己实现 `ok: true` 的判读 —— 那条规则只有一份。
"""
from __future__ import annotations

import datetime
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, relative: str):
    registered = "serveros_test_" + name
    if registered in sys.modules:
        return sys.modules[registered]
    spec = importlib.util.spec_from_file_location(registered, REPO_ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[registered] = module
    spec.loader.exec_module(module)
    return module


mt = _load("message_transport", "runtime/message_transport.py")

REPORT = "一、今日重点\n\n把投递这一环闭上了\n"
RECIPIENT = "user:ou_0000000000000000000000000000"


def a_report(tmp_path, text=REPORT):
    path = tmp_path / "daily-report.md"
    path.write_text(text, encoding="utf-8")
    return {"file": str(path), "sha256": hashlib.sha256(text.encode()).hexdigest()}


@pytest.fixture
def calls(monkeypatch):
    """What the CLI was asked to do, without a subprocess."""
    seen = []

    def fake_run(argv, *, timeout=120, cwd=None):
        seen.append({"argv": list(argv), "timeout": timeout, "cwd": cwd})
        return {"ok": True, "data": {"message_id": "om_test_1",
                                     "chat_id": "oc_test_1"}}

    monkeypatch.setattr(mt, "run", fake_run)
    return seen


def a_request(tmp_path, intent="i" * 64, **overrides):
    request = {"intent": intent, "report": a_report(tmp_path),
               "audience": {"recipient": RECIPIENT}, "previous": {}}
    request.update(overrides)
    return request


# ────────────────────────────────────────────────────────────────────────────
# 收件人：谁，以及哪一种目的地
# ────────────────────────────────────────────────────────────────────────────

class TestTheRecipient:
    def test_a_bare_identifier_is_refused_rather_than_guessed(self):
        with pytest.raises(mt.TransportFault) as caught:
            mt.split_recipient("ou_0000000000000000000000000000")
        assert "user:" in str(caught.value) and "chat:" in str(caught.value)

    def test_the_refusal_names_what_a_wrong_guess_would_cost(self):
        with pytest.raises(mt.TransportFault) as caught:
            mt.split_recipient("ou_0000000000000000000000000000")
        assert "收不回来" in str(caught.value), (
            "「必须带前缀」如果不说为什么，下一个人会把它当成一个多余的约定"
            "而不是一次不可逆的选择")

    def test_a_person_maps_to_the_user_flag(self):
        assert mt.split_recipient("user:ou_1") == ("--user-id", "ou_1")

    def test_a_group_maps_to_the_chat_flag(self):
        assert mt.split_recipient("chat:oc_1") == ("--chat-id", "oc_1")

    def test_an_unknown_kind_is_refused(self):
        with pytest.raises(mt.TransportFault):
            mt.split_recipient("team:oc_1")

    def test_an_empty_value_is_refused(self):
        with pytest.raises(mt.TransportFault):
            mt.split_recipient("user:")

    def test_nothing_at_all_is_refused(self):
        with pytest.raises(mt.TransportFault):
            mt.split_recipient("")

    def test_the_description_carries_no_part_of_the_identifier(self):
        described = mt.describe(mt.sender(RECIPIENT))
        assert "ou_" not in described and "0000" not in described


# ────────────────────────────────────────────────────────────────────────────
# 一次投递：argv 的形状
# ────────────────────────────────────────────────────────────────────────────

class TestTheCall:
    def test_it_sends_over_the_message_verb_as_the_bot(self, tmp_path, calls):
        mt.sender(RECIPIENT)(a_request(tmp_path))
        argv = calls[0]["argv"]
        assert argv[1:3] == ["im", "+messages-send"]
        assert argv[argv.index("--as") + 1] == "bot"

    def test_the_recipient_goes_in_the_flag_its_kind_names(self, tmp_path, calls):
        mt.sender("chat:oc_42")(a_request(tmp_path))
        argv = calls[0]["argv"]
        assert argv[argv.index("--chat-id") + 1] == "oc_42"
        assert "--user-id" not in argv

    def test_the_report_travels_as_markdown(self, tmp_path, calls):
        mt.sender(RECIPIENT)(a_request(tmp_path))
        argv = calls[0]["argv"]
        assert argv[argv.index("--markdown") + 1] == REPORT
        assert "--text" not in argv, (
            "日报走 markdown，是通道自己给报告类内容定的路线；走 --text 会把标题"
            "当成字面量，收件人看到的和这份日报不是一回事")

    def test_the_idempotency_key_is_the_intent_within_the_cli_ceiling(self, tmp_path, calls):
        mt.sender(RECIPIENT)(a_request(tmp_path, intent="ab" * 32))
        argv = calls[0]["argv"]
        key = argv[argv.index("--idempotency-key") + 1]
        assert key == ("ab" * 32)[:50] and len(key) <= 50

    def test_the_same_intent_asks_for_the_same_key(self, tmp_path, calls):
        request = a_request(tmp_path)
        mt.sender(RECIPIENT)(request)
        mt.sender(RECIPIENT)(request)
        keys = [c["argv"][c["argv"].index("--idempotency-key") + 1] for c in calls]
        assert keys[0] == keys[1], (
            "两次同一个意图必须是同一个键：这是通道那边去重的依据，也是窗口内"
            "「再问一次」之所以安全的全部理由")


# ────────────────────────────────────────────────────────────────────────────
# 答案：受理就是投递，但不许替通道说话
# ────────────────────────────────────────────────────────────────────────────

class TestTheAnswer:
    def test_an_accepted_message_is_delivered_and_carries_its_remote_id(self, tmp_path, calls):
        answer = mt.sender(RECIPIENT)(a_request(tmp_path))
        assert answer["state"] == "DELIVERED"
        assert answer["provider"]["remote_id"] == "om_test_1"
        assert answer["provider"]["transport"] == "im +messages-send"

    def test_an_accepted_message_says_so_when_it_has_no_id(self, tmp_path, monkeypatch):
        monkeypatch.setattr(mt, "run", lambda argv, **kw: {"ok": True, "data": {}})
        answer = mt.sender(RECIPIENT)(a_request(tmp_path))
        assert answer["state"] == "DELIVERED"
        assert answer["provider"]["remote_id"] is None
        assert "指不到" in answer["why"], (
            "受理了但答不出是哪一条，和受理并且指得出来是两件事；不说出来，"
            "回执会让两者读起来一样")

    def test_a_cli_fault_is_not_turned_into_an_answer_of_failed(self, tmp_path, monkeypatch):
        def refuse(argv, **kw):
            raise mt.TransportFault("the CLI refused")
        monkeypatch.setattr(mt, "run", refuse)
        with pytest.raises(mt.TransportFault):
            mt.sender(RECIPIENT)(a_request(tmp_path))
        # 这一条是给调用方看的：`deliver` 会把抛出的异常记成 UNKNOWN 而不是
        # FAILED，因为「调用过程中断了」和「通道说没送到」不是同一句话。

    def test_bytes_that_moved_since_the_request_are_not_sent(self, tmp_path, calls):
        request = a_request(tmp_path)
        Path(request["report"]["file"]).write_text("换过了\n", encoding="utf-8")
        answer = mt.sender(RECIPIENT)(request)
        assert answer["state"] == "FAILED"
        assert calls == [], (
            "报告在请求之后被重新生成过时，发出去的那一版和回执背书的那一版不是"
            "同一份——这正是让投递回执变得一文不值的那个失败")

    def test_a_missing_report_file_is_failed_not_a_traceback(self, tmp_path, calls):
        request = a_request(tmp_path)
        Path(request["report"]["file"]).unlink()
        answer = mt.sender(RECIPIENT)(request)
        assert answer["state"] == "FAILED" and calls == []


# ────────────────────────────────────────────────────────────────────────────
# 中断之后：这一条通道上，「问」就是「再调一次」
# ────────────────────────────────────────────────────────────────────────────

class TestTheQuestion:
    def at(self, seconds_ago):
        moment = (datetime.datetime.now(datetime.timezone.utc)
                  - datetime.timedelta(seconds=seconds_ago))
        return moment.isoformat(timespec="seconds")

    def test_inside_the_window_the_question_is_the_same_call(self, tmp_path, calls):
        request = a_request(tmp_path)
        request["previous"] = {"intent": request["intent"],
                               "delivery": {"state": "UNKNOWN",
                                            "attempted_at": self.at(60)}}
        answer = mt.sender(RECIPIENT).reconcile(request)
        assert answer["state"] == "DELIVERED"
        assert len(calls) == 1, (
            "窗口内这个通道按幂等键去重：要么第一次其实到了（这次不会再发一封），"
            "要么它没到（这次把它补上）。两种答案都是真话")
        assert calls[0]["argv"][calls[0]["argv"].index("--idempotency-key") + 1] \
            == request["intent"][:50]

    def test_outside_the_window_it_refuses_to_ask(self, tmp_path, calls):
        request = a_request(tmp_path)
        request["previous"] = {"delivery": {"attempted_at": self.at(7200)}}
        assert mt.sender(RECIPIENT).reconcile(request) is None
        assert calls == [], (
            "窗口之外同一个键会真的再发一封给一个人。这时「问」不是问，是重发")

    def test_a_timestamp_that_cannot_be_placed_makes_the_question_unanswerable(self, tmp_path, calls):
        request = a_request(tmp_path)
        request["previous"] = {"delivery": {"attempted_at": "2026-09-25 12:00:00"}}
        assert mt.sender(RECIPIENT).reconcile(request) is None
        assert calls == [], (
            "没有时区的时间戳落不到时间轴上，落不到就说不出去重窗口还在不在；"
            "说不出来的时候不能假设「刚刚」，只能不问了")

    def test_a_receipt_with_no_attempt_time_is_unanswerable(self, tmp_path, calls):
        assert mt.sender(RECIPIENT).reconcile(a_request(tmp_path)) is None
        assert calls == []

    def test_the_window_is_the_cli_s_own_number(self):
        assert mt.IDEMPOTENCY_WINDOW_SECONDS == 3600, (
            "这个数是通道的性质，不是这里定的策略。改它之前要先去读通道自己的说法")


# ────────────────────────────────────────────────────────────────────────────
# 改一张已经发出去的卡：同一个 message id 上的 patch，而不是第二条消息
# ────────────────────────────────────────────────────────────────────────────

def a_card(**over):
    payload = {"schema": "serveros/card.v1",
               "header": {"title": {"tag": "plain_text", "content": "本机 · 状态"}},
               "elements": [{"tag": "markdown", "content": "**mine** OK"}]}
    payload.update(over)
    return payload


#: The card's send time, anchored to the RUN rather than written as a fixed date.
#: The patch window is 14 days wide and measured against the clock, so an
#: absolute timestamp silently expires: 2026-09-26 was comfortably inside the
#: window when this line was written and is months outside it now, which turned
#: every assertion below red for a reason none of them is about. Relative to now
#: by an hour, the tests measure the window rule and not how old the file is.
SENT_AT = (datetime.datetime.now(datetime.timezone.utc)
           - datetime.timedelta(hours=1)).isoformat()


def an_update(**over):
    request = {"message_id": "om_1", "card": a_card(), "sent_at": SENT_AT}
    request.update(over)
    return request


class TestTheCardUpdate:
    """«只 patch 一张» 的通道半边：改的是那条消息，不是再发一条。

    四个拒绝都在本地发生，因为留到服务端拒绝时，调用方读到的是一句「卡没变」，
    而它其实是一次没有到达的更新。
    """

    def test_the_verb_is_the_one_the_cli_sends_cards_to(self, calls):
        """`+messages-edit` 是文本/post 的编辑器，CLI 自己的帮助把交互卡片指向
        patch；用 post 那条路改出来的卡片不是卡片。"""
        mt.sender(RECIPIENT).update(an_update())
        argv = calls[0]["argv"]
        assert argv[1:4] == ["im", "messages", "patch"]
        assert "+messages-edit" not in argv

    def test_the_body_carries_the_card_as_a_string_and_not_as_an_object(self, calls):
        """两个动词包同一个对象的包法不同：发送时卡片**就是**消息内容
        （`--content '<card json>'`），patch 时它在请求体里，而请求体的
        `content` 是那份 JSON **序列化成字符串**。弄错不会响亮地失败——服务端
        收下请求，把这个卡片当文本发出去，人看到的是一张坏掉的卡。"""
        card = a_card()
        mt.sender(RECIPIENT).update(an_update(card=card))
        argv = calls[0]["argv"]
        body = json.loads(argv[argv.index("--data") + 1])
        assert isinstance(body["content"], str)
        assert json.loads(body["content"]) == card

    def test_the_message_it_names_is_the_one_it_asks_about(self, calls):
        mt.sender(RECIPIENT).update(an_update(message_id="om_named"))
        argv = calls[0]["argv"]
        assert argv[argv.index("--message-id") + 1] == "om_named"
        assert argv[argv.index("--as") + 1] == "bot", (
            "卡片是 app 发的，改它的也必须是 app 身份")

    def test_the_answer_names_the_message_that_was_updated(self, calls):
        answer = mt.sender(RECIPIENT).update(an_update())
        assert answer["state"] == "DELIVERED"
        assert answer["provider"]["remote_id"] == "om_test_1"
        assert answer["provider"]["transport"] == "im messages patch"

    def test_an_answer_with_no_echo_still_names_the_message_it_asked_about(
            self, monkeypatch):
        monkeypatch.setattr(mt, "run", lambda argv, **kw: {"ok": True, "data": {}})
        answer = mt.sender(RECIPIENT).update(an_update(message_id="om_asked"))
        assert answer["provider"]["remote_id"] == "om_asked"
        assert "没有回显" in answer["why"], (
            "「没有回显」和「回显了另一条」不是同一件事，回执不许把前者说成后者")

    def test_a_patch_with_no_message_id_is_refused_without_a_call(self, calls):
        answer = mt.sender(RECIPIENT).update(an_update(message_id=""))
        assert answer["state"] == "FAILED"
        assert "message id" in answer["why"]
        assert answer["provider"]["remote_id"] is None
        assert calls == []

    def test_a_send_time_that_cannot_be_placed_is_refused(self, calls):
        for sent_at in (None, "", "上周二", "2026-09-26T09:00:00"):
            answer = mt.sender(RECIPIENT).update(an_update(sent_at=sent_at))
            assert answer["state"] == "FAILED", sent_at
            assert "无法判断" in answer["why"], sent_at
        assert calls == [], (
            "落不到时间轴上的发出时间让 14 天窗口无法判断；无法判断不等于刚发过")

    def test_a_message_past_the_window_is_refused(self, calls):
        answer = mt.sender(RECIPIENT).update(
            an_update(sent_at="2026-09-01T09:00:00+08:00"))
        assert answer["state"] == "FAILED"
        assert str(mt.CARD_UPDATE_WINDOW_DAYS) in answer["why"]
        assert calls == []

    def test_content_over_the_ceiling_is_refused_on_either_reading(self, calls):
        """「30 KB」没说 KB 是字节还是字符，所以两种读法都要拒：错在宽的那一边
        时，人看着上一版卡，而调用方以为更新已经发出去了。"""
        def sized(text):
            card = a_card(elements=[{"tag": "markdown", "content": text}])
            content = mt.card_content(card)
            return card, len(content.encode("utf-8")), len(content)

        # 只有字节超：一万一千个汉字，字符数远在 30000 以下
        card, nbytes, nchars = sized("状" * 11_000)
        assert nbytes > mt.CARD_CONTENT_LIMIT_BYTES
        assert nchars < mt.CARD_CONTENT_LIMIT_CHARS
        answer = mt.sender(RECIPIENT).update(an_update(card=card))
        assert answer["state"] == "FAILED" and calls == []

        # 只有字符超：三万零五百个 ASCII，字节数在 30720 以下
        card, nbytes, nchars = sized("a" * 30_500)
        assert nbytes < mt.CARD_CONTENT_LIMIT_BYTES
        assert nchars > mt.CARD_CONTENT_LIMIT_CHARS
        answer = mt.sender(RECIPIENT).update(an_update(card=card))
        assert answer["state"] == "FAILED" and calls == []

    def test_a_refusal_never_falls_back_to_sending(self, calls):
        """被拒的 patch 让人看着上一版卡，而补发一条会留下两张卡——那正是 V28
        点名不许发生的事。要不要第二张卡只有调用方有资格决定。"""
        refusals = [an_update(message_id=""),
                    an_update(sent_at="上周二"),
                    an_update(sent_at="2026-01-01T09:00:00+08:00"),
                    an_update(card=a_card(elements=[{"tag": "markdown",
                                                     "content": "x" * 40_000}]))]
        for request in refusals:
            answer = mt.sender(RECIPIENT).update(request)
            assert answer["state"] == "FAILED"
        assert calls == []

    def test_the_body_is_built_by_the_one_helper(self, calls):
        """发送与 patch 共用同一份序列化；多一份就是多一个会漂的地方。

        断言的是「请求体等于那个助手函数的输出」，而不是把实现抄一遍——
        抄一遍的话，这里改坏了它自己也跟着改坏，两边一起错就都不响了。
        """
        card = a_card()
        mt.sender(RECIPIENT).update(an_update(card=card))
        argv = calls[0]["argv"]
        body = json.loads(argv[argv.index("--data") + 1])
        assert body["content"] == mt.card_content(card)

    def test_the_serialisation_is_the_compact_one(self):
        """服务声明的 30 KB 是在这个函数产出的字节上量的，所以缩进与空格不是
        风格问题：一份带默认分隔符的序列化会让同样一张卡多吃几成字节，而
        上限是照着紧凑形态定的——多出来的部分是从余额里扣的，且没有任何
        报错会指向这里。"""
        assert mt.card_content({"a": 1, "b": [2, 3]}) == '{"a":1,"b":[2,3]}'


# ────────────────────────────────────────────────────────────────────────────
# 一条规则只有一份
# ────────────────────────────────────────────────────────────────────────────

class TestOneReader:
    def test_the_envelope_rule_is_borrowed_not_restated(self):
        borrowed = mt._document_transport()
        assert borrowed.run.__module__ == "_serveros_message_transport_document"
        source = (REPO_ROOT / "runtime" / "message_transport.py").read_text(encoding="utf-8")
        assert "answer.get(\"ok\")" not in source, (
            "`ok: true` 而不是 `code == 0` 这条判读只该有一份实现；这里出现第二份，"
            "变异掉其中一份时另一份会看起来还活着")


class TestTheNameIsOneCopyToo:
    """上一条钉的是**判读**只有一份，这条钉的是**名字**。

    `mt.run()` 借调 `document_transport.run()`，而后者只在 `argv[0]` 恰好等于
    `document_transport.LARK_CLI` 时才把它解析成本机的文件。两个文件各写一遍这个
    字面量：谁改了一个而没人改另一个，这条链就会**静默**地退回「按调用方的 PATH
    找」——而那正是这次修掉的那个缺陷，且它不会报错，只会让单元里的投递再次失败。
    """

    def test_both_adapters_name_the_same_cli(self):
        dt = _load("document_transport", "runtime/document_transport.py")
        assert mt.LARK_CLI == dt.LARK_CLI

    def test_the_senders_default_is_the_name_that_gets_resolved(self):
        dt = _load("document_transport", "runtime/document_transport.py")
        assert mt.LarkMessageSender(RECIPIENT).cli == dt.LARK_CLI
