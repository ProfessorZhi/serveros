"""WHY THIS MODULE EXISTS

`runtime/report_delivery.py` holds the discipline of getting one version of one
report to one receiver, and deliberately knows nothing about how a message is
carried. This is the other half: the surface adapter that carries one.

It is the sibling of `runtime/document_transport.py` and has the same shape, for
the same reason. That file's header says a kernel primitive that learns one
tenant's cloud paths stops being a kernel primitive; the corollary is that the
one file which knows what `lark-cli` is must not also be the file that decides
whether a delivery is allowed. So the rules live in `runtime/`, and the file that
runs the already-authenticated CLI is here.

It exists because the transport this product had declared for reaching a person
does not reach one. `mcp/human_control.py#notify` posts to BotMux `/api/trigger`
with no `target.chatId`, and an unaddressed trigger is a SYNTHETIC session: the
instruction is handed to a model, the model's answer is returned over HTTP to the
caller, and the prompt the service builds for it says in as many words that it is
"not shown in a chat" and that the model must not post to Feishu. A receipt read
off that path says a model was asked. It does not say a person received anything.
This module is the path that can say the second thing.

WHAT IT REFUSES TO DO

- **It does not pick a receiver.** The recipient is the caller's, and it must
  say which kind it is — `user:<open_id>` or `chat:<chat_id>` — because the CLI
  makes the two mutually exclusive. An unprefixed recipient would make this
  module guess, and a guess that lands a report in a group chat instead of one
  person's direct messages cannot be taken back.
- **It does not read `ok: true` as "a person received it".** The service
  accepting the message IS the delivery on this transport — there is no second
  receipt to wait for — so that is what the state means, and the answer's own
  message id is carried through as the remote id. What is NOT done here is
  inventing a confirmation the service never gave.
- **It does not shorten a report to fit.** A message the service refuses as too
  long is FAILED. A trimmed report delivered under a receipt naming the whole one
  is a receipt for something nobody received.
- **It does not invent a reconcile.** `reconcile` here is exactly one thing: the
  same call again, under the same idempotency key, which the service deduplicates
  rather than sending twice. Because that dedupe is time-boxed, the question can
  only be asked inside the window; outside it the answer is `None` — "cannot be
  asked" — and the entry stays pending rather than becoming a second message.
- **It does not send a second message when it cannot change the first.**
  `update` patches the card at the message id it is given, and every refusal
  names which one it is: no id, a send time that cannot be placed on a timeline,
  past the service's 14-day window, or over its content ceiling. None of them
  falls back to sending, because a fallback there leaves two cards in the chat —
  the thing V28 forbids by name — and the caller is the only one who can decide
  that a second card is the right answer.
- **It does not hold a credential and reads none.** It runs an
  already-authenticated CLI and passes through what that CLI returns.
"""
from __future__ import annotations

import datetime
import hashlib
import importlib.util
import json
from pathlib import Path

SCHEMA = "serveros/message-transport.v1"

#: The CLI this adapter drives, named rather than assumed on PATH so a host
#: without it fails with a sentence instead of a `FileNotFoundError` traceback.
LARK_CLI = "lark-cli"

#: How long the message service deduplicates on an idempotency key, per the
#: CLI's own documentation ("the same key sends only one message within 1 hour").
#: This is the transport's property and not a policy chosen here; it is the
#: reason `reconcile` can ask its question at all, and the reason it refuses to
#: after this long. Read as a bound on an answer, never as a licence to resend.
IDEMPOTENCY_WINDOW_SECONDS = 3600

#: The prefix a recipient has to carry. Two flags, mutually exclusive at the
#: CLI, and the choice between them is the caller's.
RECIPIENT_KINDS = {"user": "--user-id", "chat": "--chat-id"}

#: How long a sent card stays updatable, and how large its content may be. Both
#: are the service's bounds and not policies chosen here — they are quoted from
#: the CLI's own description of `im messages patch` («the message must have been
#: sent within the last 14 days, and `content` must be a JSON-serialized string
#: no larger than 30 KB»). A caller that wants a different number is arguing
#: with the service, not with this module.
CARD_UPDATE_WINDOW_DAYS = 14
CARD_CONTENT_LIMIT_BYTES = 30 * 1024
#: «30 KB» does not say whether KB counts bytes or characters, so this module
#: refuses on EITHER reading rather than picking one and being quietly lenient
#: on the other. The cost of the stricter reading is one refusal with a
#: sentence; the cost of the looser one is a person looking at the previous
#: card while the caller believes the update went out.
CARD_CONTENT_LIMIT_CHARS = 30 * 1000


class TransportFault(Exception):
    """The CLI could not be run, or answered with a failure. Nothing implied."""


_DOCUMENT_TRANSPORT = None


def _document_transport():
    """`runtime/document_transport.py`, loaded by path.

    Borrowed rather than copied on purpose: the rule that a write command's
    success is `ok: true` and not `code == 0` is one rule, and the CLI's own
    shared documentation names reading it the other way as the dangerous
    mistake. Two implementations of it would be two places to get it wrong, and
    a mutation in one would leave the other looking alive.
    """
    global _DOCUMENT_TRANSPORT
    if _DOCUMENT_TRANSPORT is None:
        path = Path(__file__).resolve().parent / "document_transport.py"
        spec = importlib.util.spec_from_file_location(
            "_serveros_message_transport_document", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _DOCUMENT_TRANSPORT = module
    return _DOCUMENT_TRANSPORT


def run(argv: list[str], *, timeout: int = 120, cwd: str | None = None) -> dict:
    """One CLI call, through the same reader every other CLI call goes through.

    A named seam rather than a direct call, so a test can say what the CLI
    answered without a subprocess. What it is NOT is a second implementation:
    the envelope rule stays in one place, and this delegates to it.
    """
    return _document_transport().run(argv, timeout=timeout, cwd=cwd)


def split_recipient(recipient: str) -> tuple[str, str]:
    """`user:ou_…` or `chat:oc_…` → the CLI flag and the value it takes."""
    kind, separator, value = str(recipient or "").partition(":")
    if not separator or kind not in RECIPIENT_KINDS or not value:
        raise TransportFault(
            f"接收人必须写成 user:<open_id> 或 chat:<chat_id>，收到的是 {recipient!r}。"
            f"两种目的地在这个通道上是互斥的两个参数，不加前缀就等于让这里替调用方猜；"
            f"猜错的后果是一份日报落进一个群，而不是一个人的私聊，这个动作收不回来")
    return RECIPIENT_KINDS[kind], value


def card_content(card) -> str:
    """A card as the message content the service wants: a JSON string.

    The two verbs wrap the same object differently and this is the one place
    that difference is written down. `+messages-send` takes the card AS the
    message content (`--content '<card json>'`, `--msg-type interactive`);
    `messages patch` takes it inside a request body whose `content` is that same
    JSON **serialized into a string**. Getting this wrong does not fail loudly:
    the service accepts the request and puts the card's text on the screen as
    text, which the receiver reads as a card that broke.
    """
    return json.dumps(card, ensure_ascii=False, separators=(",", ":"))


def _age_seconds(attempted_at) -> float | None:
    """How long ago the recorded attempt was, or `None` if that cannot be read.

    `None` is not zero. A timestamp this module cannot place on a timeline makes
    the dedupe window unanswerable, and an unanswerable window means the question
    must not be asked — not that it is safe to assume the answer is "just now".
    """
    try:
        moment = datetime.datetime.fromisoformat(str(attempted_at))
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        return None
    return (datetime.datetime.now(datetime.timezone.utc) - moment).total_seconds()


class LarkMessageSender:
    """A `sender` for `runtime/report_delivery.py`, over one human's messages.

    Constructed with the receiver and the sending identity, both the caller's.
    `identity` defaults to `bot` because that is the identity the node's CLI is
    configured for and the one the report is meant to come from; a report sent as
    a person would be a report this system attributed to somebody who did not
    write it.
    """

    def __init__(self, recipient: str, *, identity: str = "bot",
                 cli: str = LARK_CLI, timeout: int = 120):
        self.recipient = recipient
        self.flag, self.value = split_recipient(recipient)
        self.identity = identity
        self.cli = cli
        self.timeout = timeout

    # -- the shape report_delivery calls ----------------------------------

    def __call__(self, request: dict) -> dict:
        """Carry one report. The answer is what the CLI said about the message.

        The bytes are re-read and re-hashed HERE as well as at the top of
        `deliver`. The window between that check and this call is the one that
        matters: a report re-composed in between would go out under a receipt
        naming the previous version, and a receipt that backs the wrong bytes is
        the single failure that makes a delivery receipt worthless.
        """
        report = request["report"]
        try:
            text = Path(report["file"]).read_text(encoding="utf-8")
        except OSError as exc:
            return {"state": "FAILED",
                    "why": f"报告文件读不出来（{exc}）；没有发送任何内容"}
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if digest != report["sha256"]:
            return {"state": "FAILED",
                    "why": (f"要发出去的字节的 sha256 是 {digest}，待发记录里记的是 "
                            f"{report['sha256']}——报告在请求之后被重新生成过。"
                            f"没有发送任何内容；回执会为一份没有被人要求过的版本背书")}

        argv = [self.cli, "im", "+messages-send", "--as", self.identity,
                self.flag, self.value,
                # `--markdown` and not `--text`: this is a report, and the CLI's
                # own rule is that reports take the Markdown path. What the
                # receiver sees is therefore the service's rendering of the
                # report's text, not those bytes — which is why the receipt ties
                # to the report's VERSION and not to a byte comparison of what
                # arrived. Nothing in the report is an image or a table for the
                # renderer to drop.
                "--markdown", text,
                # The intent, truncated to the CLI's 50-character ceiling. It is
                # a sha256 in hex, so 50 characters is still 200 bits; two
                # different intents sharing a prefix is not a risk worth a
                # second key format.
                "--idempotency-key", str(request["intent"])[:50],
                "--format", "json"]
        answer = run(argv, timeout=self.timeout)
        message = answer.get("data") or {}
        remote_id = message.get("message_id")
        return {"state": "DELIVERED",
                "why": ("通道接受了这条消息"
                        + ("" if remote_id else
                           "，但回答里没有 message id——消息已受理，只是这个回执"
                           "指不到具体哪一条")),
                "provider": {"id": "lark-cli",
                             "transport": "im +messages-send",
                             "remote_id": remote_id,
                             "identity": self.identity,
                             "recipient": self.recipient}}

    def update(self, request: dict) -> dict:
        """Make the card already at `request["message_id"]` say `request["card"]`.

        This is the other half of V28's «只 patch 一张»: the decision about the
        day's card says WHICH card, and this says how the chat message showing
        it changes without a second one appearing. The verb is
        `im messages patch` and not `im +messages-edit`, and that is the CLI's
        own division rather than a preference: its help sends «Editing an
        interactive card» to the first and calls the second the text and
        post-rich editor. A card edited through the post path is not a card.

        Four things are refused HERE, each with its own sentence, because the
        alternative is a service error the caller reads as «the card did not
        change»:

          * no message id — a patch names one message, and «the most recent
            one» is not an identity;
          * a send that cannot be placed on a timeline — the 14-day window is
            then unanswerable, and an unanswerable window is not a fresh one;
          * a message outside that window — the service would refuse it;
          * content over the ceiling, measured exactly as `card_content`
            produces it.

        Nothing here falls back to sending when it cannot patch. A refused patch
        leaves the previous card on screen and says so; a send would leave TWO
        cards, which is the one thing V28 forbids by name.
        """
        transport = "im messages patch"

        def refused(why: str) -> dict:
            return {"state": "FAILED", "why": why,
                    "provider": {"id": "lark-cli", "transport": transport,
                                 "remote_id": None, "identity": self.identity,
                                 "recipient": self.recipient}}

        message_id = str(request.get("message_id") or "")
        if not message_id:
            return refused("没有 message id：patch 必须指名改哪一条，"
                           "「最近那条」不是一个身份。没有改动任何东西，也没有"
                           "改发一条新的——那会变成第二张卡")
        age = _age_seconds(request.get("sent_at"))
        if age is None:
            return refused(
                f"这条消息是什么时候发的读不出来（sent_at="
                f"{request.get('sent_at')!r}），{CARD_UPDATE_WINDOW_DAYS} 天可改"
                f"窗口因此无法判断；无法判断就不据此改一张可能已经改不动的卡，"
                f"也没有改发一条新的")
        if age > CARD_UPDATE_WINDOW_DAYS * 86400:
            return refused(
                f"这条消息已经发出约 {int(age // 86400)} 天，超过飞书 "
                f"{CARD_UPDATE_WINDOW_DAYS} 天的可改窗口：改不动了，只能新发一条，"
                f"而新发一条是另一张卡——这个判断属于调用方，不属于这里")
        content = card_content(request.get("card"))
        size, chars = len(content.encode("utf-8")), len(content)
        if size > CARD_CONTENT_LIMIT_BYTES or chars > CARD_CONTENT_LIMIT_CHARS:
            return refused(
                f"要 patch 的卡片内容是 {size} 字节 / {chars} 字符，超过服务声明的"
                f"上限（{CARD_CONTENT_LIMIT_BYTES} 字节 或 "
                f"{CARD_CONTENT_LIMIT_CHARS} 字符，哪个先到算哪个）。"
                f"没有改动任何东西")

        argv = [self.cli, "im", "messages", "patch", "--as", self.identity,
                "--message-id", message_id,
                # The request body, and the reason `card_content` exists: this
                # `content` is the card JSON as a STRING inside the body.
                "--data", json.dumps({"content": content}, ensure_ascii=False),
                "--format", "json"]
        answer = run(argv, timeout=self.timeout)
        echoed = (answer.get("data") or {}).get("message_id")
        return {"state": "DELIVERED",
                "why": ("通道接受了这次卡片更新"
                        + ("" if echoed else
                           "，但回答里没有回显 message id——更新已受理，回执按请求"
                           "里指名的那一条记，而不是猜一条")),
                "provider": {"id": "lark-cli", "transport": transport,
                             "remote_id": echoed or message_id,
                             "identity": self.identity,
                             "recipient": self.recipient}}

    def send_text(self, text: str, *, idempotency_key: str) -> dict:
        """Carry one short sentence to the receiver, as a NEW message.

        This is the notification half of the transport — the shape an alert
        takes, as opposed to `__call__`'s report. It shares everything that
        matters with the rest of the class (`run`, the resolved CLI, the
        identity, the recipient flags, the idempotency ceiling) so there is no
        second way of talking to the service, and it never patches: a
        notification that failed is a notification that did not arrive, not a
        reason to edit some other message.

        `--markdown` rather than `--text`, matching `__call__`: the alert detail
        is a short block of labelled lines and the CLI's own rule is that
        structured text takes the Markdown path. What the receiver sees is the
        service's rendering of the sentence, so the receipt ties to the send and
        not to a byte comparison of what arrived.
        """
        argv = [self.cli, "im", "+messages-send", "--as", self.identity,
                self.flag, self.value,
                "--markdown", str(text),
                "--idempotency-key", str(idempotency_key)[:50],
                "--format", "json"]
        answer = run(argv, timeout=self.timeout)
        remote_id = (answer.get("data") or {}).get("message_id")
        return {"state": "DELIVERED",
                "why": ("通道接受了这条消息"
                        + ("" if remote_id else
                           "，但回答里没有 message id——消息已受理，只是这个回执"
                           "指不到具体哪一条")),
                "provider": {"id": "lark-cli",
                             "transport": "im +messages-send (markdown)",
                             "remote_id": remote_id, "identity": self.identity,
                             "recipient": self.recipient}}

    def send_card(self, card, *, idempotency_key: str) -> dict:
        """Create ONE interactive card as a new message. The other half of `update`.

        `update` patches a card at a message id; this produces the message id.
        The two are deliberately kept apart rather than fused into a
        send-or-patch, because «is there a card to change, or does one have to be
        created» is `runtime/card_delivery.py`'s decision and not a transport's —
        a transport that guessed would leave the second card V28 forbids.

        The verb is `im +messages-send` with `--msg-type interactive` and the
        card as the message content, which is the CLI's own documented way to
        send a card, and the size ceiling is enforced on exactly the bytes
        `card_content` produces so a card too large to send is refused here
        rather than by the service after a partial effect.
        """
        content = card_content(card)
        size, chars = len(content.encode("utf-8")), len(content)
        if size > CARD_CONTENT_LIMIT_BYTES or chars > CARD_CONTENT_LIMIT_CHARS:
            return {"state": "FAILED",
                    "why": (f"要发的卡片内容是 {size} 字节 / {chars} 字符，超过服务"
                            f"声明的上限（{CARD_CONTENT_LIMIT_BYTES} 字节 或 "
                            f"{CARD_CONTENT_LIMIT_CHARS} 字符，哪个先到算哪个）。"
                            f"没有发送任何内容"),
                    "provider": {"id": "lark-cli",
                                 "transport": "im +messages-send (interactive)",
                                 "remote_id": None, "identity": self.identity,
                                 "recipient": self.recipient}}
        argv = [self.cli, "im", "+messages-send", "--as", self.identity,
                self.flag, self.value,
                "--msg-type", "interactive", "--content", content,
                "--idempotency-key", str(idempotency_key)[:50],
                "--format", "json"]
        answer = run(argv, timeout=self.timeout)
        remote_id = (answer.get("data") or {}).get("message_id")
        return {"state": "DELIVERED",
                "why": ("通道接受了这张卡片"
                        + ("" if remote_id else
                           "，但回答里没有 message id——卡片已受理，只是这个回执"
                           "指不到具体哪一条，下一次改不动它")),
                "provider": {"id": "lark-cli",
                             "transport": "im +messages-send (interactive)",
                             "remote_id": remote_id, "identity": self.identity,
                             "recipient": self.recipient}}

    def reconcile(self, request: dict) -> dict | None:
        """Ask the service whether an interrupted attempt arrived.

        On this transport the question IS the action. The message service
        deduplicates on the idempotency key within its window, so repeating the
        same call with the same key either does nothing — the first attempt
        landed — or sends the message that never went out. Both answers are the
        truth, and neither is a second copy.

        Outside the window that stops being true: the same call would send a
        second message to a person. So the only thing this method does with a
        clock is refuse — `None`, which the caller turns into "still pending",
        not into "no".
        """
        previous = request.get("previous") or {}
        age = _age_seconds((previous.get("delivery") or {}).get("attempted_at"))
        if age is None:
            return None
        if age > IDEMPOTENCY_WINDOW_SECONDS:
            return None
        return self(request)


def sender(recipient: str, **kwargs) -> LarkMessageSender:
    """The transport, ready to hand to `runtime/report_delivery.py#deliver`."""
    return LarkMessageSender(recipient, **kwargs)


def describe(transport: "LarkMessageSender") -> str:
    """One line for a run log, with no part of the receiver's identifier in it.

    The kind is the fact worth logging — a report that went to a chat instead of
    a person is the mistake the prefix rule exists to prevent, and a fragment of
    the id would not tell a reader that any better than the kind does.
    """
    return (f"{transport.cli} im +messages-send --as {transport.identity} "
            f"{transport.flag} <{transport.flag.lstrip('-')}> "
            f"(dedupe window {IDEMPOTENCY_WINDOW_SECONDS}s)")
