#!/usr/bin/env python3
"""§3 — the version of a card that was sent, and the receipt that says so.

WHY THIS IS NOT `alert_digest`
------------------------------
§3 names two rhythms over one set of facts:

    Status Broadcast   定时状态摘要 — a clock produces the message
    Alert Digest       NEW / ESCALATED / RECOVERED / REOPENED — a DELTA does

They share the facts (the register, the observation, the presentation rules)
and they do not share the send trigger. A status broadcast that has nothing new
to say is still a status broadcast; a digest with nothing new to say is not a
digest, and the whole reason `alert_digest.decision` exists is to stop it being
one. Putting the two on one ledger would make the hourly card advance the alert
delta window — every broadcast would silently consume the escalations that
happened in the same hour, which is the failure the digest was written to
prevent, arriving through its own receipt.

So the ledger here is a SECOND document with its own file, and what it shares
with the digest is only the shape of the claim: a send is recorded against a
VERSION, not against a clock.

WHAT A VERSION IS
-----------------
`freeze_card` hashes the card that was rendered — the exact payload a transport
would carry — and names it `<kind>@<hash12>`. Nothing else goes in. A timestamp
cannot be the identity of a payload, because two cards an hour apart can be
byte-identical and a re-render a second later can differ; the hash answers the
only question a receipt is asked, which is "is this the card the person saw".
`payload_bytes` is carried beside it so that "the version changed" and "the
version changed by four bytes" are different sentences.

THE IDEMPOTENCY KEY IS NOT DERIVED HERE
---------------------------------------
The window a broadcast dedupes within is the CADENCE, and the cadence belongs
to whoever runs the clock — a systemd timer in this node's case, whose catch-up
behaviour is a property of the timer and not of ServerOS. So the key defaults
to a value derived from the kind and the instant the card was rendered at, and
the caller may override it. What this module refuses to do is invent a cadence:
a `top of the hour` rule in here would be a second clock beside the one that
actually drives the send, and the two would disagree the first time a timer ran
late.

THE OTHER HALF OF A RECEIPT: WHICH MESSAGE, AND MAY IT STILL BE CHANGED
-----------------------------------------------------------------------
A receipt that records an id and nothing else answers one question. The two
card families ask a second one — "the card is still about the same thing, may
I change the message that is already in the chat instead of sending another" —
and they ask it about different identities:

    Status Broadcast    the WORKDAY's card, bound in the day document
    Incident Card       one per EPISODE, bound here, because only a receipt
                        survives the day boundary

`binding_verdict` is that question answered once for both, and `incident_verdict`
is the incident's caller: it looks the episode up in this ledger and says
whether the next send is the first card, a patch of the message already there,
or — only when that message has aged out of the service's window and the episode
is still going — a successor that is explicitly marked as the same episode.

WHAT IT IS NOT ALLOWED TO DO
----------------------------
It does not render, it does not send, and it holds no transport. It reads the
card it is handed and writes one line about what happened to it. It does not
decide whether a finding is worth saying: that is `alert_digest.decision`'s
question, and an incident with nothing to say is suppressed there rather than
here.
"""
from __future__ import annotations

import datetime
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent

SCHEMA = "serveros/card-delivery.v1"
FROZEN_SCHEMA = "serveros/card-frozen.v1"

#: The card kinds §3 and §1 name, plus the one the notice added. Read as a
#: closed set rather than as a free string so that a receipt cannot be filed
#: under a kind no renderer produces — which is how "the status card has not
#: been sent in three days" becomes unanswerable.
STATUS = "status"
ALERT = "alert"
RECOVERY = "recovery"
#: A Status Update Notice. Its own kind rather than a second `status` receipt,
#: because it is a different message about the same card and the ledger's whole
#: job is to keep «which message carries this card» answerable: two kinds that
#: shared a name would let a later patch aim at whichever of the two the lookup
#: happened to return. It is NOT per-episode — see `PER_EPISODE_KINDS` — and
#: what it is bound to is the card VERSION it was composed against, which the
#: `version` field already carries for every kind.
NOTICE = "notice"
#: The message that says a card did NOT arrive. §7① requires one — «PATCH FAILED
#: 不得发送「状态已更新」，应形成投递 finding/incident» — and it needs its own kind
#: rather than riding on `notice` for the reason `KEEP` is per kind: a receipt
#: filed under a name that does not describe it is a receipt the next reader
#: finds while looking for something else. What this message is about is not a
#: card version (it exists precisely because the version did not arrive); it is
#: about one FAILED attempt, so its key is the failed attempt's instant and its
#: `version` is the hash of its own words.
FAULT = "fault"
#: A PERIOD card — §11½'s finer-grained sibling of `status`. One message per
#: named period (上午/下午/夜间/节假日), patched silently through the period and
#: archived and frozen at its end. Its own kind rather than a second `status`
#: receipt for the reason `NOTICE` gives above: two kinds sharing a name would
#: let a later patch aim at whichever of the two the lookup happened to return,
#: and the DAY's card and the SHIFT's card are different messages about the same
#: machine. The ruling that separates them — §10's 08:00–20:00 constrains the
#: routine BROADCAST, not resource sampling nor the silent patch of a card that
#: already exists — is written down in `runtime/card_period.py`.
PERIOD = "period"
KINDS = (STATUS, ALERT, RECOVERY, NOTICE, FAULT, PERIOD)

#: How many sends of EACH KIND are kept. A broadcast ledger is a receipt, not a
#: historian: its job is to answer "what did the last send carry and did it
#: arrive", and an unbounded list would put a growing document on the path of a
#: command that runs every hour forever.
#:
#: Per kind, and it was not, and the day this changed is worth writing down.
#: `notice` adds thirteen receipts a workday, so a single bound over the whole
#: document would evict the day's own `status` receipts — the one that carries
#: `remote_id`, which is the message the next patch has to aim at — and the
#: failure mode is not a missing receipt but a SECOND card: `binding_verdict`
#: finds nothing to patch and a new message is the only option left. A bound on
#: the shared list is a bound that one chatty kind can spend on another kind's
#: behalf.
KEEP = 20

DELIVERED = "DELIVERED"
OUTCOMES = (DELIVERED, "FAILED", "UNSUPPORTED", "UNKNOWN")

#: What a receipt's state amounts to when the question is not «what does this
#: row say» but «was the reader told». One finding per non-arrival, DERIVED from
#: `OUTCOMES` rather than transcribed beside it: a fifth outcome added to that
#: tuple would otherwise be a state the ledger can write and no finding can
#: name, which is the silent case §7① is about — a send that did not arrive and
#: that nothing in the vocabulary can describe.
DELIVERY_NOT_ATTEMPTED = "DELIVERY_NOT_ATTEMPTED"
FAULTS = tuple(f"DELIVERY_{state}" for state in OUTCOMES if state != DELIVERED)
FINDINGS = FAULTS + (DELIVERY_NOT_ATTEMPTED,)

#: What each finding says, in the reader's words. Here rather than in the
#: sender because «this card did not arrive» is a statement about a measurement,
#: and every consumer composing its own sentence is a consumer whose sentence
#: can disagree with the ledger it was read from.
#:
#: None of them names a cause. A FAILED send is a fact about arrival and the
#: reasons for it — a dead token, a rate limit, a network — are not in this
#: document, so a sentence that supplied one would be inventing the half of the
#: answer that has to be measured.
SAYS = {
    "DELIVERY_FAILED":
        "这一版卡片没有到达：投递台账里最后一次投递记着 FAILED。读者手里那张"
        "停在上一版，不是这一版。",
    "DELIVERY_UNKNOWN":
        "这一版卡片到没到无法确认：投递台账里最后一次投递记着 UNKNOWN。不能当"
        "作已送达，也不能当作没发生 —— 而这两种读法给出的是相反的处置。",
    "DELIVERY_UNSUPPORTED":
        "这一版卡片没能送出去：投递台账里最后一次投递记着 UNSUPPORTED，传输侧"
        "不接受这种消息。它不是延迟，是这条路对这张卡不成立。",
    DELIVERY_NOT_ATTEMPTED:
        "台账里没有这一版的投递记录：不是没送到，是没有人试过。这一拍没有跑，"
        "所以没有可报的失败。",
}

#: Ids that are NOT message ids, by the prefix Lark gives them. `oc_` names a
#: conversation, `ou_` a user and `on_` a union identity; only `om_` names one
#: message in one conversation. The list is a deny list rather than an `om_`
#: allow list because a receipt is written from what the transport answered:
#: refusing a form this tree has not seen would throw away a real send, while
#: accepting a chat id would file the receipt against a message that does not
#: exist and make every later patch aim at a conversation.
NOT_MESSAGE_PREFIXES = ("oc_", "ou_", "on_")

#: Kinds whose card is about an EPISODE rather than about a workday. An incident
#: card is one per episode, and across hours and days the same episode keeps the
#: same card, so the episode is the only name a later reader can look the
#: message up by. The rule in `record` is symmetric — an incident receipt has to
#: carry one, a status or notice receipt must not — because a word that can mean
#: nothing stops being a name.
#:
#: `notice` is on the refusing side of that rule for a reason that is easy to
#: get backwards: a notice IS raised by an incident as often as by a good hour,
#: and it still carries no episode. What it is about is the CARD (the `version`
#: field names which one), and the hour it rode in on. An episode here would
#: make `carries` answer «the incident's card» with a notice's receipt, and the
#: next incident patch would aim at the notice.
PER_EPISODE_KINDS = (ALERT, RECOVERY)

#: The message half of a card decision: what happens to the CHAT MESSAGE that
#: shows a card. Closed set for the same reason `KINDS` is — a caller branching
#: on a fourth spelling of "send it" is a caller that will send twice.
NEW_MESSAGE = "NEW_MESSAGE"
PATCH_MESSAGE = "PATCH_MESSAGE"
#: The third one, and the day's card has no equivalent. When the message
#: carrying an episode has aged out of the service's window and the episode is
#: still going, the documents ask for a successor card marked as the same
#: episode continuing. That is neither a first send nor a patch, and reporting
#: it as NEW would lose the continuity the criterion is about.
SUCCESSOR_MESSAGE = "SUCCESSOR_MESSAGE"
MESSAGE_ACTIONS = (NEW_MESSAGE, PATCH_MESSAGE, SUCCESSOR_MESSAGE)

#: Why the message half answered what it did. «SEND, and the reason is that no
#: message was ever recorded» and «SEND, and the reason is that the recorded one
#: is too old to change» are different findings with different fixes, so a
#: single SEND would hide the first one.
NEVER_SENT = "NEVER_SENT"
IN_HAND = "IN_HAND"
IDENTITY_MISSING = "IDENTITY_MISSING"
IDENTITY_EXPIRED = "IDENTITY_EXPIRED"
IDENTITY_UNDATED = "IDENTITY_UNDATED"
MESSAGE_REASONS = (NEVER_SENT, IN_HAND, IDENTITY_MISSING, IDENTITY_EXPIRED,
                   IDENTITY_UNDATED)


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _store_io():
    # The same atomic write every other durable document in this tree uses —
    # one implementation of "write JSON without a torn read", not two.
    return _load("serveros_runtime_agent_control_store",
                 _HERE / "agent_control_store.py")


class CardDeliveryError(RuntimeError):
    pass


def canonical(blob) -> bytes:
    """One payload, one byte string, whatever the serialiser felt like."""
    return json.dumps(blob, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")


def check_kind(kind: str) -> str:
    kind = str(kind or "")
    if kind not in KINDS:
        raise CardDeliveryError(
            f"{kind!r} is not one of {', '.join(KINDS)} — a receipt filed "
            f"under a kind no renderer produces cannot be read back")
    return kind


def check_remote_id(value) -> str | None:
    """Which message this receipt is for, or `None` for "the sender has none".

    The receipt is the only place this id is kept, so it is also the place the
    two ways of getting it wrong have to be caught. An empty value is the
    transport answering without one — a fact the ledger is required to hold, so
    it is returned as `None` and not as `''`. A value that is present but is
    not a message id is REFUSED rather than stored: it is what a later patch
    would be aimed at, and a receipt pointing at a conversation or at a typo is
    a receipt for a send nobody can reach. Nothing raised here repeats the
    value; it is an identifier like any other.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        # Refused rather than stringified. `str(b"om_x")` is `"b'om_x'"`, which
        # is a well-formed-looking id naming nothing — the exact quiet failure
        # this function exists to stop, arriving through a missing `.decode()`
        # somewhere upstream.
        raise CardDeliveryError(
            f"a message id arrives as text, and this one is a "
            f"{type(value).__name__}. A receipt that stringified it would name "
            f"a message nobody can reach. The value is not repeated here")
    message_id = value.strip()
    if not message_id:
        return None
    if any(character.isspace() for character in message_id):
        raise CardDeliveryError(
            "a message id is one token, and this one contains whitespace, so "
            "it names no message. The value is not repeated here")
    for prefix in NOT_MESSAGE_PREFIXES:
        if message_id.startswith(prefix):
            raise CardDeliveryError(
                f"`{prefix}` names an identity that is not a message — `oc_` a "
                f"conversation, `ou_` and `on_` a person. Only `om_` names one "
                f"message, so a receipt filed against this value would make "
                f"every later update aim at the wrong thing. The value is not "
                f"repeated here")
    return message_id


def check_episode(value) -> str | None:
    """Which episode this receipt is for, or `None` for "this kind has none".

    An episode is a token and nothing here interprets it: it is derived
    elsewhere (`alert_digest.episode_of`, over the alert ref and the open
    period) and this module only has to keep it whole. What it must not do is
    lose it — see the kind rule in `record`, which is what makes «只 patch 一张»
    answerable for an incident across days instead of merely intended.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise CardDeliveryError(
            f"an episode arrives as text, and this one is a "
            f"{type(value).__name__}. A receipt that stringified it would name "
            f"an episode nobody can look the message up by. The value is not "
            f"repeated here")
    episode = value.strip()
    if not episode:
        return None
    if any(character.isspace() for character in episode):
        raise CardDeliveryError(
            "an episode is one token, and this one contains whitespace, so it "
            "names no episode. The value is not repeated here")
    return episode


def _moment(value) -> datetime.datetime | None:
    """An instant this module can place on a timeline, or `None`.

    A naive value gives `None` rather than being read as UTC: the window is
    fourteen days wide, so assuming a zone for a value that carries none can
    move the answer by nearly a day in either direction, which is the difference
    between patching a message and refusing to.
    """
    try:
        moment = datetime.datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo is not None else None


def _transport():
    """`runtime/message_transport.py`, for the one number that is its property.

    Loaded under the SAME module name `runtime/cli.py` loads it as, so this is
    one copy and not two: a test that pinned the window in one of them would
    otherwise be pinning a document nobody reads. What is read from it is a
    constant — the service's own update window — and not a call: neither this
    module nor its callers send anything on account of it.
    """
    return _load("serveros_runtime_message_transport",
                 _HERE / "message_transport.py")


def update_window_days() -> int:
    """How long the service lets a sent card be changed. Read, not copied.

    The number lives in `message_transport`, written down there beside the
    service's own description of `im messages patch`. A second copy here would
    be the two-numbers-one-rule this tree keeps refusing, and it would disagree
    with the transport the first time the service moved its window.
    """
    return _transport().CARD_UPDATE_WINDOW_DAYS


def binding_verdict(binding, *, at: str, window_days: int) -> dict:
    """Whether a RECORDED message may still be patched at `at`, and why not.

    The one judgment both card families make: a card remembers the message it
    went out in, and every later update has to decide whether that message is
    still changeable. `card_day` asks it about the message bound to the day's
    card; `incident_verdict` asks it about the message bound to an episode. Two
    answers to it would be two places for "the window still holds" to be got
    wrong, and the day's card would promise a patch the transport refuses.

    `binding` is `{"remote_id": ..., "at": ...}` as it was recorded, or `None`
    for "a card exists and no message was ever recorded for it". Both instants
    are arguments — this instant and when that message was sent — so nothing
    here reads a clock. The SENTENCES are deliberately not built here: what a
    day's card and an incident should say about the same finding differ, and one
    shared sentence would be wrong in one of the two places.
    """
    verdict = {"action": NEW_MESSAGE, "reason": None, "remote_id": None,
               "window_days": window_days, "age_days": None}
    # Stripped, the way the row writer strips it: `"   "` is not a message id
    # there and it is not one here either, and a reader that treated the two
    # readings differently would answer «a card is in hand» about a binding the
    # writer refused to file.
    remote_id = str(binding.get("remote_id") or "").strip() \
        if isinstance(binding, dict) else ""
    if not remote_id:
        # Nothing was recorded, which is the state every later update has to be
        # able to tell from "we recorded one and it is still usable".
        verdict["reason"] = IDENTITY_MISSING
        return verdict
    verdict["remote_id"] = remote_id
    sent, now = _moment(binding.get("at")), _moment(at)
    if sent is None or now is None:
        verdict["reason"] = IDENTITY_UNDATED
        return verdict
    age_days = (now - sent).total_seconds() / 86400
    verdict["age_days"] = age_days
    if age_days > window_days:
        verdict["reason"] = IDENTITY_EXPIRED
        return verdict
    verdict.update({"action": PATCH_MESSAGE, "reason": IN_HAND})
    return verdict


def incident_verdict(ledger, *, kind: str, episode: str, at: str) -> dict:
    """What the next send about ONE episode does to the message carrying it.

    The incident twin of §11's «只 patch 一张»: ``同 episode 跨小时/跨天只 patch
    一张 Incident Card，不重复 NEW``. What makes it answerable is that the
    question is asked of what WAS SENT — this ledger is the only document that
    survives the day boundary — rather than of a second copy of the fact.

    The answer is about the MESSAGE, not about whether the finding is worth
    saying. «Nothing worth a card this hour» is `alert_digest.decision`'s
    answer, and V35's suppression lives there; a verdict that also decided
    relevance would be a second gate in front of the one that exists.

    Refusals rather than guesses: a kind whose card is about a workday, and a
    missing episode, are both refused instead of being read as "probably the
    only one".
    """
    kind = check_kind(kind)
    if kind not in PER_EPISODE_KINDS:
        raise CardDeliveryError(
            f"`{kind}` is a card about a workday and not about an episode; the "
            f"episodes belong to {', '.join(PER_EPISODE_KINDS)}. A verdict "
            f"keyed on one would aim a patch at whatever it found first")
    episode = check_episode(episode)
    if not episode:
        raise CardDeliveryError(
            "an incident verdict is about one episode, and this call named "
            "none: «the episode» is not an episode")
    window = update_window_days()
    row = ledger.carries(kind, episode)
    if row is None:
        return {"kind": kind, "episode": episode, "at": at,
                "action": NEW_MESSAGE, "reason": NEVER_SENT, "remote_id": None,
                "version": None, "window_days": window, "age_days": None,
                "says": ("这个 episode 还没有发出过的卡：本次新发一条，发送之后"
                         "把返回的消息 id 连同这个 episode 一起记在回执上 —— "
                         "下一次才会是 patch 而不是第二张卡")}
    verdict = binding_verdict({"remote_id": row.get("remote_id"),
                               "at": row.get("at")}, at=at,
                              window_days=window)
    out = {"kind": kind, "episode": episode, "at": at,
           "action": verdict["action"], "reason": verdict["reason"],
           "remote_id": verdict["remote_id"], "version": row.get("version"),
           "window_days": window, "age_days": verdict["age_days"], "says": ""}
    if verdict["reason"] == IDENTITY_EXPIRED:
        out["action"] = SUCCESSOR_MESSAGE
        out["says"] = (
            f"承载这个 episode 的消息已经发出约 {int(verdict['age_days'])} 天，"
            f"超过 {window} 天可改窗口：patch 不到了。只有这时才允许新建一张，"
            f"而且新卡必须显式标注它接续的是同一个 episode（continuity）——"
            f"不能就此发一张与它无关的新卡")
    elif verdict["reason"] == IDENTITY_MISSING:
        # Kept apart from NEVER_SENT on purpose: here a card WAS sent for this
        # episode, and sending another one now makes a second card for it. What
        # is broken is the receipt, and that is a different repair.
        out["says"] = (
            "这个 episode 发出过卡，但那一行回执没有记下消息 id，因此无从 patch。"
            "这时新发一条就是同一 episode 的第二张卡；要改的是那条回执 —— "
            "发出时把 `--message-id` 一起记下")
    elif verdict["reason"] == IDENTITY_UNDATED:
        out["says"] = (
            f"记下了承载这个 episode 的消息，但它的发出时间（或这一刻）读不出来，"
            f"{window} 天可改窗口因此无法判断。无法判断就不声称可以 patch，"
            f"也不据此改发一张新的")
    else:
        since = (f"发出约 {verdict['age_days']:.1f} 天"
                 if verdict["age_days"] > 0 else "记下的发出时间不早于这一拍")
        out["says"] = (f"承载这个 episode 的消息在手（{since}，在 {window} 天可改"
                       f"窗口内）：本次 patch 这一条，不新发")
    return out


def freeze_card(kind: str, card: dict, *, at: str,
                idempotency_key: str | None = None) -> dict:
    """The version of this rendered card, and what a receipt will name.

    The payload is the card and only the card. A version computed over the
    model, or over the model plus the card, would change when a field the
    transport never carries changed — and a receipt whose version moves for a
    reason nobody can see is a receipt nobody trusts.
    """
    kind = check_kind(kind)
    if not isinstance(card, dict) or not card:
        raise CardDeliveryError(
            f"a card version is a hash of the card, and this one is "
            f"{type(card).__name__}"
            + ("" if card else " and empty"))
    blob = canonical(card)
    payload_hash = hashlib.sha256(blob).hexdigest()
    return {
        "schema": FROZEN_SCHEMA,
        "kind": kind,
        "at": at,
        "payload_hash": payload_hash,
        "payload_bytes": len(blob),
        "version": f"{kind}@{payload_hash[:12]}",
        "idempotency_key": idempotency_key or f"card-{kind}-{at}",
    }


class CardLedger:
    """What this node has sent, by version. One document, one atomic write.

    An unreadable ledger is REFUSED rather than replaced, the same rule the
    alert store and the delivery cursor follow: starting from an empty receipt
    list would make every past send invisible, and the next diagnosis would
    conclude the broadcast had never run.
    """

    def __init__(self, path) -> None:
        self.path = Path(path)
        self._data = self._load()

    def _load(self) -> dict:
        if not self.path.exists():
            return {"schema": SCHEMA, "entries": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CardDeliveryError(
                f"{self.path} exists and cannot be read ({type(exc).__name__}: "
                f"{exc}). Refusing to start with an empty ledger: every send "
                f"this node has recorded would become invisible, and the next "
                f"reader would conclude nothing had ever been sent.") from exc
        if not isinstance(data, dict):
            raise CardDeliveryError(f"{self.path} is not a JSON object")
        if not isinstance(data.get("entries"), list):
            raise CardDeliveryError(
                f"{self.path} has no `entries` list; a ledger that cannot say "
                f"what was sent is not one this module will overwrite")
        for index, row in enumerate(data["entries"]):
            # A row written before episodes existed has no key at all, and that
            # absence is left alone: this refuses a MALFORMED episode rather
            # than inventing one for rows that predate the field.
            if isinstance(row, dict) and "episode" in row:
                try:
                    check_episode(row["episode"])
                except CardDeliveryError as exc:
                    raise CardDeliveryError(
                        f"{self.path} entry {index} carries an episode this "
                        f"module cannot read: {exc}. Refusing to serve the "
                        f"ledger, because a row whose episode cannot be "
                        f"compared is a row no lookup will find — and the "
                        f"verdict would answer that nothing was ever sent for "
                        f"that episode, which is how one episode gets two cards"
                    ) from exc
        data["schema"] = SCHEMA
        return data

    # -- reads ------------------------------------------------------------

    def entries(self, kind: str | None = None) -> list[dict]:
        rows = [dict(row) for row in self._data["entries"]
                if isinstance(row, dict)]
        if kind is None:
            return rows
        return [row for row in rows if row.get("kind") == check_kind(kind)]

    def last(self, kind: str | None = None) -> dict | None:
        rows = self.entries(kind)
        return rows[-1] if rows else None

    def last_delivered(self, kind: str | None = None) -> dict | None:
        for row in reversed(self.entries(kind)):
            if row.get("state") == DELIVERED:
                return row
        return None

    def carries(self, kind: str, episode: str) -> dict | None:
        """The message this ledger says carries ONE episode, or `None`.

        The last row for this episode that NAMES a message, rather than simply
        the last row: a refused patch is a statement about an attempt and not
        about which message carries the episode, and its `remote_id` is empty
        because nothing went out. Reading the last row outright would answer
        «a card was sent and the receipt lost its id» immediately after a patch
        the service declined — a repair aimed at a receipt that is intact — and
        the reader would be one step from sending a second card for one episode.

        A row that arrived without an id is still a row (that absence is
        recorded rather than filled in) and the fallback returns it, so
        «recorded, and names no message» stays distinct from «nothing recorded».
        """
        episode = check_episode(episode)
        if not episode:
            raise CardDeliveryError(
                "which message carries an episode is a question about one "
                "episode, and this call named none")
        rows = [row for row in self.entries(kind)
                if row.get("episode") == episode]
        if not rows:
            return None
        naming = [row for row in rows if str(row.get("remote_id") or "")]
        return naming[-1] if naming else rows[-1]

    def summary(self, kind: str | None = None) -> dict:
        rows = self.entries(kind)
        return {
            "schema": SCHEMA,
            "path": str(self.path),
            "kind": kind,
            "entries": len(rows),
            "last": rows[-1] if rows else None,
            "last_delivered": self.last_delivered(kind),
        }

    def delivery_finding(self, kind: str = STATUS) -> dict:
        """§7①. What happened to the NEWEST attempt to put this card in the chat.

        This is not `last_delivered` and the difference is the whole reason the
        function exists. `last_delivered` answers «did a card of this kind ever
        arrive», and after a failed patch the row it finds is the PREVIOUS
        hour's — same version, because the card had not changed — so a reader
        that asked it would be told that something arrived and would conclude
        the reader is up to date. The question a finding has to ask is what
        happened to the attempt that was just made, and only the newest row of
        ANY state can answer it. That is why this reads `last`.

        The finding is about the newest attempt and NOT about a named episode;
        «which message carries THIS incident» is `carries`, a lookup over one
        episode's rows. Two questions, two readers, and folding them into one
        would make an incident card's receipt speak for a day's card.

        `fault` is not severity. It separates «the transport did not do the
        thing» from «nobody asked it to» — a kind whose card has never been
        attempted has no arrival to report and must not be counted as a failure,
        while a kind that was attempted and did not arrive is one. That
        distinction cannot be recovered from the finding word alone by a reader
        in a hurry, which is the sort of reader this document is written for.
        """
        kind = check_kind(kind)
        rows = self.entries(kind)
        newest = rows[-1] if rows else None
        state = str((newest or {}).get("state") or "").strip()
        if newest is None:
            finding = DELIVERY_NOT_ATTEMPTED
        elif state == DELIVERED:
            finding = None
        elif state in OUTCOMES:
            finding = f"DELIVERY_{state}"
        else:
            # A row whose state is not one of the four this module declares.
            # Refused rather than reported as a fifth: a ledger that acquired a
            # state no reader has a sentence for would otherwise be summarised
            # as «not DELIVERED», which is true and useless — the difference
            # between a refused send and an unreadable row is the difference
            # between a transport problem and a document problem.
            raise CardDeliveryError(
                f"the newest {kind} receipt carries state {state!r}, which is "
                f"not one of {', '.join(OUTCOMES)} — a finding cannot be "
                f"stated about it, and reporting it as «not DELIVERED» would "
                f"file an unreadable row as a transport failure")
        return {
            "kind": kind,
            "finding": finding,
            "fault": finding in FAULTS,
            "state": state or None,
            "version": str((newest or {}).get("version") or "") or None,
            "at": (newest or {}).get("at"),
            "remote_id": (newest or {}).get("remote_id"),
            "says": SAYS.get(finding) if finding else None,
            "note": ("About the NEWEST attempt of this kind, not about the "
                     "newest one that arrived: after a failed patch the two are "
                     "different rows and only the first one can say whether the "
                     "reader is up to date."),
        }

    # -- the one write ----------------------------------------------------

    def record(self, kind: str, *, at: str, state: str, version: str,
               payload_hash: str | None = None, payload_bytes: int | None = None,
               idempotency_key: str | None = None, remote_id: str | None = None,
               episode: str | None = None, reason: str | None = None) -> dict:
        """One line: which version this send carried, and what happened to it.

        A FAILED send is recorded WITH its version rather than without one. The
        version is what was attempted and that is a true statement about it; a
        null would make "the card that failed to arrive" and "a send nobody
        composed a card for" read the same, and only one of those is worth
        waking somebody about.

        `remote_id` is the message the send produced — the transport answers
        with one, and it is what makes a later update of THAT message possible
        instead of a second message. It is `None` when the transport answered
        without one, and that absence is kept rather than filled in: a receipt
        that guessed an id would point a later patch at a message nobody can
        name. It is not required for DELIVERED, because the transport itself
        reports that case rather than refusing it.

        `episode` is which incident this send was about, and the rule is
        symmetric: a `alert`/`recovery` receipt must name one and a `status`
        receipt must not. The first half is what makes «同 episode 跨小时、跨天
        只 patch 一张» answerable at all — without it the ledger can say which
        card went out but not that it was the same incident as this hour's, and
        the only way forward is a second card. The second half is not tidiness:
        a status card has no episode, and a receipt that carried one would make
        that word name two different things at once.
        """
        kind = check_kind(kind)
        # Checked here and not at the call sites: this is the only door the row
        # goes through, so one rule covers every caller — including the daemon,
        # which is the only writer over the socket.
        remote_id = check_remote_id(remote_id)
        episode = check_episode(episode)
        if kind in PER_EPISODE_KINDS and not episode:
            raise CardDeliveryError(
                f"a `{kind}` receipt is about ONE episode, and this one names "
                f"none. An episode is derived from the alert it came from "
                f"(`alert_digest.episode_of`), so there is nothing here to "
                f"derive it from: a receipt filed under a made-up one would "
                f"answer «which card carries this episode» with a patch aimed "
                f"at another incident's card")
        if kind not in PER_EPISODE_KINDS and episode:
            raise CardDeliveryError(
                f"a `{kind}` receipt is about a workday and not about an "
                f"episode, and this one carries `{episode}`. Recording it would "
                f"make that episode name two things at once: the incident that "
                f"has one card, and a day whose card has an episode of its own")
        if state not in OUTCOMES:
            raise CardDeliveryError(
                f"{state!r} is not one of {', '.join(OUTCOMES)} — an outcome "
                f"nobody can name is an outcome that will be read as a delivery")
        row = {
            "kind": kind,
            # Which incident, for the kinds that have one: this is the field a
            # later hour or a later day looks itself up by. Without it in the
            # row, `carries` has nothing to match and the answer is a new card.
            "episode": episode,
            "at": at,
            "state": state,
            "version": version,
            "payload_hash": payload_hash,
            "payload_bytes": payload_bytes,
            "idempotency_key": idempotency_key,
            # The message this send produced, so that the NEXT update of the
            # same card can be a patch of it. Without this the identity is
            # dropped here and the only way forward is a second message.
            "remote_id": remote_id,
            "delivered": state == DELIVERED,
        }
        if state != DELIVERED:
            row["reason"] = reason or f"delivery answered {state}"
        entries = [dict(item) for item in self._data["entries"]
                   if isinstance(item, dict)]
        entries.append(row)
        self._data["entries"] = _trim(entries)
        _store_io()._atomic_write(self.path, self._data)
        return {"recorded": True, "entry": row, "ledger": self.summary(kind)}


def _trim(entries: list[dict]) -> list[dict]:
    """Keep the last `KEEP` rows of each kind, in one chronological list.

    Walked backwards so that "the last twenty of this kind" is decided by
    position alone and does not need a sort key — rows are appended in order and
    the file's order IS the order, so re-sorting by `at` would introduce a
    second opinion about it for the benefit of nothing.

    A row whose kind is unreadable is kept rather than dropped. Every writer
    goes through `check_kind`, so such a row can only come from a file this
    module did not write, and silently discarding evidence is the one thing a
    receipt store must not do — `_load` refuses those files outright, which is
    the loud answer, and this function is not the place to make it quiet.
    """
    kept: list[dict] = []
    seen: dict = {}
    for row in reversed(entries):
        kind = row.get("kind")
        seen[kind] = seen.get(kind, 0) + 1
        if seen[kind] <= KEEP:
            kept.append(row)
    kept.reverse()
    return kept


def ledger_path(state_path) -> Path:
    """The ledger beside the register and the alert cursor, never inside them.

    Sibling documents rather than keys in one: the alert register is rewritten
    whole on every evaluation pass, and a card receipt that lived inside it
    would be erased by a pass that has nothing to do with cards.
    """
    return Path(state_path).parent.parent / "cards" / "delivery.json"


def _require_frozen(document: dict, kind: str) -> dict:
    """A record must name the version the card it was handed actually is.

    The whole value of the receipt is that its version and the payload that
    went out are the same thing, so they are compared here rather than trusted.
    A caller that hands over a card and a version that does not hash to it is
    recording something nobody rendered.

    A card with NO frozen block is refused too, unlike the digest's legacy
    path: `card status` has always emitted one, so a missing block is not an
    older producer but a caller that built the document itself, and a receipt
    for that would name a version of nothing.
    """
    frozen = document.get("frozen") if isinstance(document, dict) else None
    if not isinstance(frozen, dict):
        raise CardDeliveryError(
            "the card handed over carries no `frozen` block. A receipt names "
            "the version that was sent, and a version cannot be recovered from "
            "a document that does not state one — re-render it with "
            "`serverosctl card <kind> --json`")
    if check_kind(kind) != frozen.get("kind"):
        raise CardDeliveryError(
            f"the card is a {frozen.get('kind')!r} and the receipt is being "
            f"filed under {kind!r}")
    return frozen


def verify(frozen: dict, card: dict) -> None:
    """The frozen block must be the version of THIS card.

    §3: every send saves the actual frozen payload, hash and version. That is
    only meaningful if the three belong to one another, so the hash is
    recomputed from the card and compared. A mismatch is a delivery being
    recorded against a card that was never rendered, and the receipt for it
    would be a receipt for nothing.
    """
    recomputed = hashlib.sha256(canonical(card)).hexdigest()
    if recomputed != frozen.get("payload_hash"):
        raise CardDeliveryError(
            f"the frozen block names version {frozen.get('version')} and the "
            f"card hashes to {recomputed[:12]}. These are two different "
            f"payloads, and a receipt naming the wrong one is a receipt for a "
            f"card nobody rendered.")


def main(argv: list[str] | None = None) -> int:
    """Read one ledger. A diagnostic, not the product surface."""
    import argparse
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--state", required=True, help="the alerts state file")
    parser.add_argument("--kind", default=None, choices=list(KINDS))
    args = parser.parse_args(argv)
    json.dump(CardLedger(ledger_path(args.state)).summary(args.kind),
              sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
