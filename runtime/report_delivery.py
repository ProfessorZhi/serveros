"""WHY THIS MODULE EXISTS

`runtime/daily_report.py` composes one period's report, hashes it, and writes two
files. Its own CLI line says the rest: *"This command writes two files and sends
nothing."* That is a correct producer and an incomplete loop — 计划 §P5 asks for
"一个周期报告完成适用的 compose/publish/send", and J10 asks that a real
receiver's receipt be tied to the exact version of the document. Without this
module the third verb does not exist, and a report that was never delivered reads
exactly like one that was, because both are a file on a disk.

So this is the delivery discipline: an INTENT is recorded before anything is
sent, ONE attempt is made, and the answer the transport gave is recorded against
the report's own sha256.

WHAT IT REFUSES TO DO

- **It does not send anything nobody asked for.** A delivery needs an outbox
  entry, and an outbox entry needs a named requester. "没要求通知不强发" is not a
  policy this module consults; it is the shape of its only entry point.
- **It does not deliver to nobody.** An intent with no recipient is refused when
  it is created, because an outbox that already exists is indistinguishable from
  a request that was accepted, and "无人 receiver 不冒充待办已被接受".
- **It does not call a missing consumer a delivery result.** With no consumer the
  answer is that the entry is still pending — 待续 — and no receipt is written,
  because no attempt happened. `FAILED` would say somebody tried.
- **It does not re-send an attempt whose outcome nobody established.** A previous
  attempt recorded as UNKNOWN is reconciled against the transport first; a
  transport that cannot be asked leaves the entry pending rather than producing a
  second message.
- **It does not keep a second vocabulary of its own.** The four delivery words
  are read from the notification contract, through the same reader the publication
  receipt uses. A restated copy would, on the day one side gains a fifth word,
  disagree with the other in silence.
- **It does not carry the message.** The transport is a `sender` the caller
  injects. Nothing here knows what Feishu is, and nothing here holds a token.
"""
from __future__ import annotations

import datetime
import hashlib
import importlib.util
import json
import tempfile
from pathlib import Path

SCHEMA = "serveros/report-delivery.v1"
OUTBOX_SCHEMA = "serveros/delivery-outbox.v1"
RECEIPT_SCHEMA = "serveros/delivery-receipt.v1"

#: Where the two records live. They sit in the same directory as the report and
#: its publication receipt, because that is the store this producer already owns
#: — ARCHITECTURE.md §23.2 says an outbox/receipt table *in the existing owning
#: store* is enough, and a second store for two files would be a second thing to
#: keep consistent with them.
OUTBOX_FILE = "delivery-outbox.json"
RECEIPT_FILE = "delivery-receipt.json"

#: The outbox entry's own lifecycle. This is NOT the delivery vocabulary and the
#: two are kept in separate words deliberately.
#:
#: `PENDING` means the request stands and nothing has settled it. `WITHDRAWN`
#: means somebody stopped asking. Neither is a statement about a transport, so
#: neither is `DELIVERED`, `FAILED`, `UNSUPPORTED` or `UNKNOWN` — and in
#: particular there is no `PENDING_CONSUMER` state here: "no consumer yet" is a
#: *reason* an entry is still `PENDING`, not a fifth outcome for an attempt that
#: did not happen.
OUTBOX_STATES = ("PENDING", "WITHDRAWN")

#: The publication-receipt-shaped record this module borrows the report's version
#: identity from. Named rather than assumed: the intent must point at the version
#: a reader could actually have received.
PUBLICATION_RECEIPT = "publication-receipt.json"

#: The other file a directory can hold its version in. A report states it in a
#: publication receipt; anything else — the 19:20 alignment brief is the one
#: that exists — states it here. `held_version` reads one or the other through
#: one resolver, so the delivery discipline below stays about delivery.
VERSION_FILE = "version.json"

#: What a caller says when it has just re-checked that this delivery is still
#: authorized. Required, and the module will not invent a yes: authorization is a
#: property of the moment of sending, not of the moment of asking — "静默期间可
#: 生成待发 intent，醒来重查 receiver/内容版本/授权".
AUTHORIZATION_KEYS = ("authorized", "because")


class Refused(Exception):
    """A delivery that must not be attempted, with the reason naming itself."""


# ────────────────────────────────────────────────────────────────────────────
# where the two records are
# ────────────────────────────────────────────────────────────────────────────

def _dir(out_dir) -> Path:
    """The owning store, resolved and required to be absolute.

    A relative `out_dir` means a different place for every caller and every
    working directory, and a receipt written to one of those is a receipt nobody
    can find again.
    """
    path = Path(out_dir)
    if not path.is_absolute():
        raise Refused(
            f"out_dir 是 {out_dir!r}，不是绝对路径。相对路径的含义取决于谁在什么时候从哪个"
            f"目录调用它，而投递回执必须能被人按同一条路径找回来")
    return path


def outbox_file(out_dir) -> Path:
    return _dir(out_dir) / OUTBOX_FILE


def receipt_file(out_dir) -> Path:
    return _dir(out_dir) / RECEIPT_FILE


def _write(path: Path, payload: dict) -> None:
    """Atomic, so an outbox or a receipt is never half-recorded."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
    handle, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with open(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
        Path(tmp).replace(path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _read_json(path: Path) -> dict | None:
    """A mapping, or `None` for "there is no readable record here".

    An unreadable file is reported to the caller as its own case rather than
    being flattened into "absent": a receipt that exists and cannot be parsed is
    evidence that an attempt happened, and treating it as never-having-happened
    is how a second message gets sent.
    """
    if not path.is_file():
        return None
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Refused(
            f"{path} 存在但读不出来：{exc}。它记着已经发生过的事，"
            f"把它当成「没有记录」会让人再发一次") from exc
    if not isinstance(loaded, dict):
        raise Refused(f"{path} 不是一个映射，读不出任何一条已知的字段")
    return loaded


def read_outbox(out_dir) -> dict:
    """The outbox, or an empty one. Never invented, never defaulted to a send."""
    loaded = _read_json(outbox_file(out_dir))
    if loaded is None:
        return {"schema": OUTBOX_SCHEMA, "entries": []}
    if loaded.get("schema") != OUTBOX_SCHEMA:
        raise Refused(
            f"outbox 声明的 schema 是 {loaded.get('schema')!r}，不是 {OUTBOX_SCHEMA!r}。"
            f"认不出的 outbox 会被读成一份空的，而一份空的 outbox 恰好是「没有任何请求」，"
            f"于是本该待发的投递会安静地不发生")
    entries = loaded.get("entries")
    if not isinstance(entries, list):
        raise Refused(f"outbox 的 entries 是 {type(entries).__name__}，不是列表")
    return loaded


def read_receipt(out_dir) -> dict | None:
    """The last delivery recorded here, or `None` for never.

    `None` means nothing was attempted from this directory. It does not mean a
    delivery failed, and it does not mean one succeeded.
    """
    return _read_json(receipt_file(out_dir))


# ────────────────────────────────────────────────────────────────────────────
# the vocabulary, borrowed
# ────────────────────────────────────────────────────────────────────────────

def delivery_states() -> tuple:
    """The delivery vocabulary, from the notification contract.

    Read through `runtime/daily_report.py#publication_states`, which already
    reads `runtime/notification-contract.yaml#alerts.delivery.values`. Two
    readers of one list is one list; a second copy here would be a second list
    that nobody would think to check the day the first gained a word.

    The module is loaded fresh and NOT registered under a shared name. One name
    (`serveros_runtime_daily_report`) is already used by
    `tests/test_daily_report.py` and `runtime/recipes.py`, both of which cache:
    whichever loaded first answers for the rest of the process, so a test that
    loads a copy from somewhere else hands the whole run the wrong vocabulary.
    That is the hazard ledger §9.22 records for the test loader, and the way to
    not be part of it is to not take part in the cache.
    """
    spec = importlib.util.spec_from_file_location(
        "_serveros_report_delivery_daily_report",
        Path(__file__).resolve().parent / "daily_report.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.publication_states()


# ────────────────────────────────────────────────────────────────────────────
# what this delivery is about
# ────────────────────────────────────────────────────────────────────────────

def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _now(at=None) -> str:
    if at:
        return str(at)
    return datetime.datetime.now(datetime.timezone.utc).isoformat(
        timespec="seconds")


def held_version(out_dir) -> dict:
    """The version this directory is holding, and the file that says so.

    Two kinds of directory state their version and this is the one place that
    difference is written down, because the discipline below is about putting
    ONE version in front of ONE receiver and does not care what the bytes are:

      * a **report** directory states it in `publication-receipt.json`, whose
        `document.file` + `document.sha256` are the report's own publication
        record;
      * a directory holding anything else — the 19:20 alignment brief is the
        one that exists — states it in `version.json`, whose `file` +
        `sha256` name the artefact the same way.

    What is NOT different between them is the rule: the bytes are hashed HERE
    and compared to what the record claims. A record that names a hash the file
    no longer has is the one failure mode that makes a delivery receipt
    worthless — it would tie a receiver's copy to a version that was never
    published. Reading the two kinds through one resolver is also what keeps
    `request`/`deliver` (and therefore `report request`/`report deliver`, and
    the idempotency and receipt discipline they carry) usable for both: a
    second module for the second kind would be a second copy of everything
    below, and the copies would drift the day one gained a state.

    `published_version` is this function's old name and its report-only body;
    it was renamed rather than kept beside this one because two functions that
    answer "which version" are two things a caller can pick wrong.
    """
    directory = _dir(out_dir)
    receipt = _read_json(directory / PUBLICATION_RECEIPT)
    if receipt is not None:
        where = PUBLICATION_RECEIPT
        record = receipt.get("document") or {}
        if not record.get("file"):
            raise Refused(
                f"{PUBLICATION_RECEIPT} 里没有 document.file，所以它没有指明是哪一份报告。"
                f"一份不指明对象的回执不能给另一次投递背书")
        period = (receipt.get("report") or {}).get("period")
        schema = receipt.get("schema")
    else:
        statement = _read_json(directory / VERSION_FILE)
        if statement is None:
            raise Refused(
                f"{directory} 里既没有 {PUBLICATION_RECEIPT} 也没有 {VERSION_FILE}："
                f"这份目录没有发布过任何报告，也没有写下过任何版本。"
                f"没有发布过的东西不能被投递，也没法说投递的是哪一版")
        where = VERSION_FILE
        record = statement
        if not record.get("file"):
            raise Refused(
                f"{VERSION_FILE} 里没有 file，所以它没有指明是哪一份东西。"
                f"一份不指明对象的版本声明不能给一次投递背书")
        period = statement.get("period")
        schema = statement.get("schema")
    path = Path(record["file"])
    if not path.is_file():
        raise Refused(
            f"{where} 指向 {path}，而这份文件不在那里。要么文件被移走了，要么记录指向了"
            f"别处；两种情况下这次投递都不是「投递这一版」")
    actual = _sha(path.read_bytes())
    claimed = record.get("sha256")
    if claimed != actual:
        raise Refused(
            f"{where} 声称这一版的 sha256 是 {claimed!r}，而 {path} 现在的 sha256 是 "
            f"{actual!r}。文件在记录之后被改过，记录说的那一版已经不在这里了——"
            f"投出去的话，收件人拿到的是一份没有任何记录为它背书的内容")
    return {"file": str(path), "sha256": actual, "bytes": len(path.read_bytes()),
            "period": period, "schema": schema}


def intent_key(version: dict, recipient: str, channel: str | None) -> str:
    """What "the same delivery" means.

    Content-addressed rather than time-addressed, for the reason
    `intake/document_publish.py#intent_key` gives: two runs of the same request
    have to be recognisable as the same request, or idempotency quietly becomes
    "we have not done this recently". The report VERSION is part of it — a
    re-composed report is a different thing to deliver and must not be swallowed
    by the previous delivery's receipt.
    """
    material = {"report": version["sha256"], "recipient": recipient,
                "channel": channel}
    return _sha(json.dumps(material, ensure_ascii=False, sort_keys=True)
                .encode("utf-8"))


# ────────────────────────────────────────────────────────────────────────────
# the intent
# ────────────────────────────────────────────────────────────────────────────

def request(out_dir, *, recipient, requested_by, why, channel=None,
            at=None) -> dict:
    """Record that somebody asked for this report to reach this receiver.

    Every field here is required because each one is a question a reader of the
    outbox will ask, and a missing answer is not an answer:

      * `recipient` — an outbox entry with no receiver would sit there looking
        like an accepted request while being deliverable to nobody;
      * `requested_by` — a request with no requester is indistinguishable from
        one this system invented for itself, which is exactly the unrequested
        notification the plan forbids;
      * `why` — same reason one step down: "we are holding this" is only honest
        if the holder can say who wanted it and what for.

    Writing an intent sends nothing. That is the point of an outbox.
    """
    recipient = str(recipient).strip() if recipient else ""
    if not recipient:
        raise Refused(
            "没有收件人。无人 receiver 的待发不是「排队中」——它会以一条待办的样子留在"
            "outbox 里，而没有任何人会被打扰，也没有任何人会因此知道它还没发出去。"
            "要先有收件人（计划原文：无人 receiver 不冒充待办已被接受）")
    requester = str(requested_by).strip() if requested_by else ""
    if not requester:
        raise Refused(
            "没有说这份报告是谁要发的。一个没有请求者的待发，和系统自己决定要发的"
            "通知，在 outbox 里长得一模一样；而「没要求通知不强发」正是要靠这一栏成立")
    reason = str(why).strip() if why else ""
    if not reason:
        raise Refused("没有说为什么要发。留在 outbox 里的每一条都要能回答这个问题")

    version = held_version(out_dir)
    key = intent_key(version, recipient, channel)
    outbox = read_outbox(out_dir)
    entries = list(outbox.get("entries") or [])

    # `WITHDRAWN` is the one state that is not deduplicated: a withdrawal stops
    # the NEXT attempt (it cannot un-send anything), so asking again afterwards
    # is a person changing their mind about the same version. That is a real new
    # intent and not a duplicate of the one they withdrew.
    for entry in entries:
        if entry.get("intent") == key and entry.get("state") == "PENDING":
            # Asking twice for the same thing is one thing. This is not a
            # deduplication convenience: a second entry with the same key is a
            # second message waiting to happen.
            return {"outbox": outbox_file(out_dir), "state": "ALREADY_REQUESTED",
                    "intent": key, "entry": entry,
                    "why": "同样的收件人对同样的这一版已经有一条待发记录了；再记一条会变成"
                           "第二封消息"}
        if entry.get("intent") == key and entry.get("state") == "DELIVERED":
            # The same request, already settled. Appending another entry would
            # send nothing — the receipt for this key is read first and answers
            # ALREADY_DELIVERED — but it would leave a row saying "waiting" for
            # a delivery that already arrived, and every reader of the outbox
            # (`pending_count`, a health table, the next attempt) would count it
            # as work outstanding. §P5 is about a register being read as a
            # register; a phantom pending row is the quietest way to break that.
            return {"outbox": outbox_file(out_dir), "state": "ALREADY_REQUESTED",
                    "intent": key, "entry": entry,
                    "why": "这一版给这个收件人已经有一条落定为 DELIVERED 的记录：再记一条"
                           "待发不会让它再发一次（回执会先被读到并回答 ALREADY_DELIVERED），"
                           "只会留下一条永远等不到投递的「待发」，"
                           "让 outbox 看上去还有事在排队"}
    entry = {"intent": key, "report": version,
             "audience": {"recipient": recipient, "channel": channel},
             "requested_by": requester, "requested_at": _now(at), "why": reason,
             "state": "PENDING", "attempts": 0}
    entries.append(entry)
    outbox = dict(outbox, schema=OUTBOX_SCHEMA, entries=entries)
    _write(outbox_file(out_dir), outbox)
    return {"outbox": str(outbox_file(out_dir)), "state": "REQUESTED",
            "intent": key, "entry": entry,
            "why": "记下了待发意图。这一步没有发送任何东西"}


def withdraw(out_dir, *, because, at=None) -> dict:
    """Stop asking. Idempotent, and it says whether anything was still pending.

    A withdrawal does not un-send a message that already arrived — nothing can,
    and pretending otherwise is how "we withdrew it" becomes a claim about
    somebody else's inbox. It stops the NEXT attempt.
    """
    outbox = read_outbox(out_dir)
    entries = list(outbox.get("entries") or [])
    pending = [entry for entry in entries if entry.get("state") == "PENDING"]
    for entry in pending:
        entry["state"] = "WITHDRAWN"
        entry["withdrawn_at"] = _now(at)
        entry["withdrawn_because"] = str(because or "").strip() or None
    if pending:
        _write(outbox_file(out_dir),
               dict(outbox, schema=OUTBOX_SCHEMA, entries=entries))
    return {"state": "WITHDRAWN" if pending else "NOTHING_PENDING",
            "withdrawn": len(pending),
            "why": ("撤下了待发的请求。已经发出去的消息不因此撤回——没有任何东西能撤回"
                    "别人的收件箱" if pending else "没有待发的请求")}


# ────────────────────────────────────────────────────────────────────────────
# the attempt
# ────────────────────────────────────────────────────────────────────────────

def _receipt(entry, state, *, why, at, provider=None, attempts=None) -> dict:
    """One delivery record, tied to the version the intent named.

    `attempts` is a parameter rather than always `entry.attempts + 1` because a
    reconciliation is not a new attempt — it is the answer to an old one, and
    counting it would make "nobody could tell us what happened four times" look
    like five tries.
    """
    count = (int(entry.get("attempts") or 0) + 1 if attempts is None
             else int(attempts))
    return {"schema": RECEIPT_SCHEMA, "intent": entry["intent"],
            "report": entry["report"], "audience": entry["audience"],
            "delivery": {"state": state, "delivered": state == "DELIVERED",
                         "attempted_at": at, "why": why},
            "provider": provider or {},
            "attempts": count}


def _authorized(authorization) -> dict:
    """Whether the caller just re-checked, and it refuses to guess.

    A delivery is authorized at the moment it is sent, not at the moment it was
    asked for. A caller that cannot say it re-checked cannot have this module
    deliver on the strength of an older decision, so the answer is a refusal
    naming the missing check rather than a default.
    """
    if not isinstance(authorization, dict) or not authorization:
        raise Refused(
            "调用方没有说这次投递现在是否仍然获授权。授权是发送那一刻的性质，不是请求"
            "那一刻的性质：在中间这段时间里，收件人、内容版本或授权本身都可能已经变了")
    missing = [key for key in AUTHORIZATION_KEYS if key not in authorization]
    if missing:
        raise Refused(
            f"授权里缺少 {missing}。只说「可以」而不说是谁复查的、复查的是什么，"
            f"与没有复查过没有区别")
    if not authorization.get("authorized"):
        raise Refused(
            f"调用方复查后认为这次投递现在不获授权：{authorization.get('because')}。"
            f"这一条不是失败——没有任何东西被发出去")
    return authorization


def deliver(out_dir, *, sender, authorization=None, at=None) -> dict:
    """One attempt at one pending entry, and what the transport said about it.

    The order below is the whole discipline and is not rearrangeable:

      1. **the version is re-read, not remembered** — the report on disk must
         still be the version the intent named;
      2. **an existing receipt is read first** — a settled delivery is not
         repeated, and an interrupted one is reconciled rather than re-sent;
      3. **authorization is re-checked now**;
      4. **the attempt is marked UNKNOWN before it is made** — so a crash
         between sending and recording leaves the evidence that something might
         have gone out;
      5. **the transport answers**, and only its answer decides the state.
    """
    directory = _dir(out_dir)
    outbox = read_outbox(out_dir)
    all_entries = list(outbox.get("entries") or [])
    entries = [entry for entry in all_entries if entry.get("state") == "PENDING"]
    if not entries:
        return _nothing_pending(directory, out_dir, all_entries)

    entry = entries[-1]
    version = held_version(out_dir)
    key = intent_key(version, entry["audience"]["recipient"],
                     entry["audience"].get("channel"))
    if key != entry["intent"]:
        return {"state": "REFUSED", "directory": str(directory),
                "intent": entry["intent"],
                "why": (f"这一版报告的 sha256 是 {version['sha256']}，与待发记录里记的 "
                        f"{entry['report'].get('sha256')} 不是同一版。报告在请求之后被重"
                        f"新生成过——投出去的话，回执会为一份没有被人要求过的内容背书。"
                        f"要么按新的这一版重新记一条待发，要么撤回：不投这一条")}

    previous = read_receipt(out_dir)
    if previous and previous.get("intent") == key:
        state = (previous.get("delivery") or {}).get("state")
        if state == "DELIVERED":
            return {"state": "ALREADY_DELIVERED", "directory": str(directory),
                    "intent": key, "receipt": str(receipt_file(out_dir)),
                    "why": ("这一版给这个收件人的投递已经落下了一条 DELIVERED 回执。"
                            "再发一次就是第二封消息，而收件人会收到两遍同一份日报"),
                    "delivery": previous.get("delivery")}
        if state == "UNKNOWN":
            # The one case that must not be resolved by sending again. Ask the
            # transport what it did with the attempt; a transport that cannot be
            # asked leaves this pending, which is the honest answer.
            reconciled = _reconcile(sender, entry, previous)
            if reconciled is None:
                return {"state": "UNKNOWN_OUTCOME", "directory": str(directory),
                        "intent": key, "receipt": str(receipt_file(out_dir)),
                        "delivery": previous.get("delivery"),
                        "why": ("上一次尝试的结果没有人确定过（回执记的是 UNKNOWN），"
                                "而这个通道不能回答「那一次到底发出去没有」。"
                                "再发一次有可能就是第二封——所以这一条保持待发，"
                                "等一个能回答这个问题的通道")}
            # The answer becomes a record. Without this the reconciliation
            # would be an opinion held only by the process that asked for it,
            # the entry would stay pending, and the next run would ask the same
            # question forever while the receipt went on saying UNKNOWN.
            answer_state, answer_why, answer_provider = _read_answer(reconciled)
            receipt = _receipt(entry, answer_state, at=_now(at), why=answer_why,
                               provider=answer_provider,
                               attempts=entry.get("attempts"))
            _write(receipt_file(out_dir), receipt)
            if answer_state == "DELIVERED":
                _settle(out_dir, outbox, entry)
            return {"state": answer_state, "directory": str(directory),
                    "intent": key, "receipt": str(receipt_file(out_dir)),
                    "reconciled": True, "delivery": receipt["delivery"],
                    "provider": answer_provider, "why": answer_why}

    if sender is None:
        # No consumer. Nothing is attempted, so nothing is recorded as an
        # outcome — the entry stays pending and the answer says so. §P5:
        # "有 outbox 但没有消费者只称待续".
        #
        # Checked BEFORE authorization on purpose: with no consumer there is no
        # attempt for an authorization to govern, and a refusal about authority
        # would be a worse answer than the true one — the entry is waiting, and
        # it would wait whatever the authorization said.
        return {"state": "PENDING_CONSUMER", "directory": str(directory),
                "intent": key, "attempts": int(entry.get("attempts") or 0),
                "why": ("这一条已经记在 outbox 里，但没有任何消费者来接它："
                        "本次运行没有可用的投递通道。它仍然是待发，"
                        "和「发失败了」不是同一件事——没有任何人试过")}

    _authorized(authorization)

    attempt_at = _now(at)
    mark = _receipt(entry, "UNKNOWN", at=attempt_at,
                    why="尝试进行中或中断：这条记录先落盘，"
                        "这样一次在发送和记录之间崩掉的运行会留下痕迹")
    _write(receipt_file(out_dir), mark)

    try:
        answer = sender({"intent": key, "report": entry["report"],
                         "audience": entry["audience"],
                         "requested_by": entry["requested_by"],
                         "why": entry["why"]})
    except Exception as exc:
        # A sender that raised while being asked is a check that did not
        # happen, not a delivery that failed: the request may well have gone
        # out. §23.2 — timeout after send means effect unknown, not
        # safe-to-repeat.
        why = (f"投递通道在回答之前抛出了 {type(exc).__name__}: {exc}。"
               f"这次尝试是否已经发出去，从这里看不出来——按 UNKNOWN 记，"
               f"下一次会先向通道核对，而不是直接再发一次")
        receipt = _receipt(entry, "UNKNOWN", at=attempt_at, why=why)
        _write(receipt_file(out_dir), receipt)
        _record_attempt(out_dir, outbox, entry, attempt_at, "UNKNOWN")
        return {"state": "UNKNOWN_OUTCOME", "directory": str(directory),
                "intent": key, "receipt": str(receipt_file(out_dir)),
                "delivery": receipt["delivery"], "why": why}

    state, why, provider = _read_answer(answer)
    known = delivery_states()
    if state not in known:
        # A transport word nobody declared. Refusing to map it is the point:
        # reading an unrecognised word as success is the quietest defect in
        # this whole class, and reading it as failure is the second quietest.
        why = (f"投递通道答了一个没有声明过的结果 {state!r}（词汇表是 {list(known)}），"
               f"所以这次投递的结果没有被记为任何确定的值。" + why)
        state = "UNKNOWN"
    receipt = _receipt(entry, state, at=attempt_at, why=why, provider=provider)
    _write(receipt_file(out_dir), receipt)
    _record_attempt(out_dir, outbox, entry, attempt_at, state)
    if state == "DELIVERED":
        _settle(out_dir, outbox, entry)
    return {"state": state, "directory": str(directory), "intent": key,
            "receipt": str(receipt_file(out_dir)), "delivery": receipt["delivery"],
            "provider": provider, "why": why}


def _read_answer(answer) -> tuple:
    """A transport's answer, read as three separate things.

    A sender is required to return a mapping with `state` and `why`; a missing
    `why` is not a formatting complaint — a state without a reason is a verdict
    nobody can argue with, which is the thing every other module in this tree
    refuses to produce. `provider` is free-form and travels into the receipt
    unchanged, because the transport's own ids are what a later reconciliation
    would need.
    """
    if not isinstance(answer, dict):
        return ("UNKNOWN",
                f"投递通道答了一个 {type(answer).__name__}，不是一份带 state 的映射", {})
    state = answer.get("state")
    why = str(answer.get("why") or "").strip()
    if not why:
        why = ("投递通道给了结果但没有给理由；一个没有理由的结果无从争辩，"
               "所以这里按 UNKNOWN 记")
        state = "UNKNOWN"
    provider = answer.get("provider") if isinstance(answer.get("provider"), dict) else {}
    return (str(state) if state else "UNKNOWN", why, provider)


def _nothing_pending(directory: Path, out_dir, entries: list) -> dict:
    """Nothing is queued. Which is two different sentences.

    "There is no request" and "the request that was here is finished" are both
    quiet, and only one of them means there is nothing left to do. Reporting the
    second as the first would tell a caller that nobody ever asked — and the
    next thing a caller does on hearing that is ask again.
    """
    previous = read_receipt(out_dir)
    if entries and previous and previous.get("intent") == entries[-1].get("intent"):
        state = (previous.get("delivery") or {}).get("state")
        if state == "DELIVERED":
            return {"state": "ALREADY_DELIVERED", "directory": str(directory),
                    "intent": entries[-1].get("intent"),
                    "receipt": str(receipt_file(out_dir)),
                    "delivery": previous.get("delivery"),
                    "recorded": len(entries),
                    "why": ("这一版给这个收件人的投递已经完成，待发队列里没有剩下东西。"
                            "再发一次就是第二封消息")}
    return {"state": "NOTHING_REQUESTED", "directory": str(directory),
            "recorded": len(entries),
            "why": (f"没有待发的请求（outbox 里有 {len(entries)} 条记录，都不是 PENDING）。"
                    f"投递只能执行已经记下的意图；一条都没有的时候，"
                    f"这里不会自己造一条出来")}


def _reconcile(sender, entry, previous) -> dict | None:
    """Ask the transport whether the interrupted attempt arrived.

    `None` means the question could not be asked — which is not the same as an
    answer of "no", and the caller turns it into "still pending" rather than into
    a second message. A sender that does not implement `reconcile` is one that
    cannot be asked. The raw answer comes back unread so that `_read_answer`
    reads it exactly once, in the same place every other transport answer is
    read.

    The whole previous RECEIPT is handed over, not just its `provider` block. A
    reconciler has to be able to say "this question can no longer be asked
    safely" — one whose only safe way of asking is a time-boxed call needs the
    time of the attempt, and the time of the attempt is in the receipt. Handing
    over the provider's answer alone would leave that unanswerable and turn
    "cannot ask" into "asked anyway", which on a transport that deduplicates by
    a window means a second message to a person.
    """
    ask = getattr(sender, "reconcile", None)
    if not callable(ask):
        return None
    try:
        answer = ask({"intent": entry["intent"], "report": entry["report"],
                      "audience": entry["audience"],
                      "previous": previous or {}})
    except Exception:
        return None
    if not isinstance(answer, dict) or not answer.get("state"):
        return None
    return answer


def pending_count(out_dir) -> int:
    """How much is actually queued. A quiet period with 0 here is 0 inbound."""
    return len([entry for entry in (read_outbox(out_dir).get("entries") or [])
                if entry.get("state") == "PENDING"])


def _record_attempt(out_dir, outbox, entry, at, state) -> None:
    """Count the attempt on the entry. The entry stays PENDING unless settled.

    Counted rather than overwritten: "this is the fourth time nobody could tell
    us what happened" is a different situation from the first, and a field that
    only ever holds 1 would hide it.
    """
    entries = list(outbox.get("entries") or [])
    for candidate in entries:
        if candidate.get("intent") == entry["intent"]:
            candidate["attempts"] = int(candidate.get("attempts") or 0) + 1
            candidate["last_attempt_at"] = at
            candidate["last_attempt_state"] = state
    _write(outbox_file(out_dir),
           dict(outbox, schema=OUTBOX_SCHEMA, entries=entries))


def _settle(out_dir, outbox, entry) -> None:
    entries = list(outbox.get("entries") or [])
    for candidate in entries:
        if candidate.get("intent") == entry["intent"]:
            candidate["state"] = "DELIVERED"
    _write(outbox_file(out_dir),
           dict(outbox, schema=OUTBOX_SCHEMA, entries=entries))


# ────────────────────────────────────────────────────────────────────────────
# rendering
# ────────────────────────────────────────────────────────────────────────────

def render(result: dict) -> list[str]:
    """The outcome as lines. Every path gets a line, including the quiet ones."""
    state = result.get("state")
    lines = [f"state       {state}"]
    if result.get("intent"):
        lines.append(f"intent      {result['intent']}")
    if result.get("receipt"):
        lines.append(f"receipt     {result['receipt']}")
    if result.get("recorded") is not None:
        lines.append(f"outbox      {result['recorded']} 条记录，没有待发的")
    delivery = result.get("delivery") or {}
    if delivery:
        lines.append(f"delivered   {delivery.get('delivered')}")
    if result.get("why"):
        lines.append(f"because     {result['why']}")
    return lines


def main(argv=None) -> int:  # pragma: no cover - the CLI lives in runtime/cli.py
    # Not phrased as a command on purpose: `tests/test_declared_commands.py`
    # reads `serverosctl <word> <word>` out of every product `.py` and asks the
    # parser whether it exists, and it read the sentence that used to stand here
    # as the subcommand `is the`. The scanner is right to be that literal — it
    # was written after a comment naming a verb nobody built — so the sentence
    # moves rather than the scanner.
    raise SystemExit("runtime/cli.py is the entry point; this is a library")
