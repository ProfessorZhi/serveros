#!/usr/bin/env python3
"""A question that reached nobody, and the two receipts it needs first.

Why this file exists
--------------------
`docs/daily-work-cycle.md` states the fallback path in one sentence:

    兜底默认交绑定的个人工作 owner，而非擅自指定同事或 SLA：无匹配、证据冲突、
    权限不够、用户要求转人工或预算耗尽时形成小决策包（原消息引用、已查事实、
    尝试、疑问、建议）。先入已有工作/通知队列并得到回执，再按许可给 owner 提醒；
    外发收件人/机器人未绑定时显示待投递，不能告诉求助者“已通知”。

Every ingredient for that sentence already exists in this tree in isolation, and
they are not joined:

  * `intake/support.py` produces a `NO_ENTRY_MATCHES` improvement candidate
    carrying exactly the material a packet needs, and **nothing consumes it**.
    Its only reader is its own renderer; it is printed and dropped.
  * `runtime/notifications.py` can deliver to a person and its capability probe
    answers SUPPORTED / UNSUPPORTED / UNKNOWN, and nothing outside `alerting.py`
    calls it.
  * `memory/handoff.py` is the only durable inbox in the tree, and it is a §59
    DEVICE handoff — not a queue for people.

So this module is the join, and it is deliberately the smallest one that makes
the sentence checkable. It reads a support reading, assembles the packet, and
records what the two anchors answered.

What it refuses to do
---------------------
**It does not invent a queue.** `runtime/notification-contract.yaml:757` states
「No queue, no retry and no scheduler」 as that module's decision, so minting one
here would be a second delivery path with its own opinion about how often a
person is told the same thing. The queue is an injected adapter, and a missing
one is a named state rather than an ImportError.

**It does not choose an owner.** §10 forbids exactly that — 「而非擅自指定同事
或 SLA」. Both anchors are parameters, by the rule `runtime/cli.py` states for
`--kb-root`: a value baked into a module answers for the machine it was written
on.

**It does not send anything.** The one delivery path in this product is
`runtime/notifications.py`, whose event vocabulary is a closed set with no word
for 「escalate to a person」. Widening another module's contract to fit this one
would be this module editing somebody else's closed set to say something it was
not asked to say. So a delivery is REPORTED to this module and recorded here,
and nothing is sent from here.

**It cannot say 「已通知」 on its own.** `notified` is true only when a delivery
was reported DELIVERED — the rule `runtime/alerting.py#record_delivery` states as
「`last_notified_at` is set ONLY for DELIVERED」 and `runtime/daily_report.py`
restates for a publication receipt as 「一份没有收件人的回执不允许声称 DELIVERED」.

**It never reads the clock.** A moment is supplied or it is `None`. A recorded
time that is really the moment the tool ran is not the moment the thing
happened, and the two are compared later.

Read-only. It imports no mutating verb, holds no queue and has no write path.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

import yaml

_HERE = Path(__file__).resolve().parent
CONTRACT = _HERE / "support-handoff-contract.yaml"

#: The redaction patterns live in the SUPPORT contract and are read from there,
#: not restated here. A packet leaves this machine, which is precisely the
#: boundary those patterns were written for, so this is one rule with two
#: readers rather than two rules that will drift.
SUPPORT_CONTRACT = _HERE / "support-contract.yaml"

EXIT_FAULT = 1

#: The four words a TRANSPORT may answer with. `PENDING_DELIVERY` is deliberately
#: not here: it is this module's word for 「nothing was attempted」, and a
#: transport that reports it would be reporting a state only the routing can
#: reach.
REPORTABLE = ("DELIVERED", "FAILED", "UNSUPPORTED", "UNKNOWN")

#: The one outcome this module derives a trigger from. It is one entry on
#: purpose: the other four triggers need a reading this product does not
#: produce, and inventing a derivation would be this module answering a
#: question about the chain that it cannot see.
DERIVED_TRIGGER = {"NO_ENTRY_MATCHES": "NO_MATCH"}

_CONTRACT_CACHE: dict | None = None
_SUPPORT_CACHE: dict | None = None


class Refused(ValueError):
    """A handoff could not be assembled. Argument-level, never a reading.

    Same split as `support.py#Refused`: this is the caller's mistake and it
    exits EXIT_FAULT. A reading that came back with nothing delivered is
    `NOT_NOTIFIED`, which is an answer.
    """


def contract() -> dict:
    global _CONTRACT_CACHE
    if _CONTRACT_CACHE is None:
        _CONTRACT_CACHE = _load(CONTRACT, "serveros/support-handoff-contract.v1")
    return _CONTRACT_CACHE


def support_contract() -> dict:
    global _SUPPORT_CACHE
    if _SUPPORT_CACHE is None:
        _SUPPORT_CACHE = _load(SUPPORT_CONTRACT, "serveros/support-contract.v1")
    return _SUPPORT_CACHE


def _load(path: Path, schema: str) -> dict:
    if not path.exists():
        raise Refused(f"{path.name} is not readable at {path}; this module "
                      f"cannot state its rules without it")
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise Refused(f"{path.name} does not parse as YAML: {exc}")
    if not isinstance(loaded, dict) or loaded.get("schema") != schema:
        raise Refused(f"{path.name} does not declare {schema!r}, so it is not "
                      f"the contract this module was written against")
    return loaded


# ---------------------------------------------------------------------------
# Redaction: read from the support contract, applied here
# ---------------------------------------------------------------------------
def scrub(text: str | None) -> tuple[str | None, list[dict]]:
    """The packet's own copy of the support chain's redaction, and its hits.

    A non-empty hit list here is a FINDING rather than routine defence: the
    caller is expected to hand over text the support chain already redacted on
    the way in, so a hit means something reached this module that the earlier
    pass did not catch. It is reported rather than silently rewritten, because
    a packet that quietly dropped what was asked is a packet that lies about
    the question.
    """
    if text is None:
        return None, []
    spec = support_contract()["answer"]["redaction"]
    out, hits = text, []
    for name, pattern in spec["patterns"].items():
        found = re.findall(pattern, out)
        if found:
            hits.append({"class": name, "count": len(found)})
            out = re.sub(pattern, spec["replaces_with"], out)
    return out, hits


# ---------------------------------------------------------------------------
# The packet. §10's five parts, kept as five.
# ---------------------------------------------------------------------------
def _trigger_for(reading: dict | None, trigger: str | None) -> str:
    spec = contract()["trigger"]["values"]
    if trigger is not None:
        if trigger not in spec:
            raise Refused(
                f"{trigger!r} is not one of {sorted(spec)}. §10 names five and "
                f"they are five on purpose: 「no entry matched」 is a finding "
                f"about the knowledge base and 「the person asked for a human」 "
                f"is a request, and collapsing them would lose which one "
                f"happened")
        return trigger
    if reading is None:
        raise Refused(
            "a fallback needs a trigger, and neither one was supplied nor one "
            "could be derived. Deriving one from nothing is how a caller gets a "
            "handoff for a question nobody asked")
    outcome = reading.get("outcome")
    if outcome in DERIVED_TRIGGER:
        return DERIVED_TRIGGER[outcome]
    raise Refused(
        f"this reading's outcome is {outcome!r}, and no fallback is derived "
        f"from it. A question the knowledge base ANSWERED is not a fallback — "
        f"handing it over would ask a person to redo work that was done. The "
        f"one outcome this module derives a trigger from is "
        f"{sorted(DERIVED_TRIGGER)}; the other four triggers "
        f"{sorted(set(spec) - set(DERIVED_TRIGGER.values()))} have no producer "
        f"in the support chain yet and must be named by a caller")


def _parts(reading: dict | None, trigger: str, question: str | None,
           conversation: str | None) -> tuple[dict, list[dict]]:
    """The five carries, assembled from whatever the caller actually has.

    Never from more than that. Every field below is either lifted from the
    reading or admitted to be absent; a packet that filled a gap with a
    plausible-looking value would be read by an owner as if the chain had
    established it.
    """
    hits: list[dict] = []
    improvement = (reading or {}).get("improvement") or {}

    if improvement:
        shape = improvement.get("question_shape") or {}
        carried, seen = scrub(shape.get("message"))
        hits.extend(seen)
        question_part = {
            "status_code": shape.get("status_code"), "message": carried,
            "source": ("the improvement candidate's own `question_shape`, which "
                       "the support chain redacted on the way in")}
        recommendation = {
            "kind": improvement.get("kind"),
            "id_prefix": improvement.get("id_prefix"),
            "offered_id": improvement.get("offered_id"),
            "kb_file": improvement.get("kb_file"),
            # §J's own rule, made machine-readable: `next_id` OFFERS an ID, and
            # an ID 「分配后不再变更」 is a commitment a person makes.
            "assigned": False,
        }
        attempts = {
            "matched_by": (reading or {}).get("matched_by"),
            "redirects_applied": (reading or {}).get("redirects") or [],
            "candidates": (reading or {}).get("candidates") or [],
            "rules_left_unevaluated": (reading or {}).get("missing") or [],
            "because": ("the knowledge base's own 主匹配 and 辅助匹配 rules were "
                        "evaluated and none of them reached this question; the "
                        "four fields above are what the chain looked at and "
                        "found empty. The per-rule verdicts were not carried by "
                        "this reading, and inventing them here would tell an "
                        "owner a rule was tried that maybe never was"),
        }
        facts = {
            "kb_root": (reading or {}).get("kb_root"),
            "same_repository": ((reading or {}).get("boundary") or {}).get("same_repository"),
            "entries_reached": len(attempts["candidates"]),
            # The count IS carried, as the sentence the measuring module wrote.
            # Re-parsing it out of prose would be this module measuring
            # something it did not measure.
            "measured_by_the_chain": improvement.get("why"),
        }
    else:
        if trigger == "NO_MATCH":
            raise Refused(
                "the trigger is NO_MATCH and the reading carries no improvement "
                "candidate, which is the only thing that makes a NO_MATCH packet "
                "worth sending: without 「已查事实」 and 「建议」 the owner is "
                "asked to redo the search that produced this handoff")
        # A reading can be present with a trigger that did NOT come from it —
        # a caller who knows the person asked for a human about a question the
        # knowledge base also could not reach. The reading's own `question` is
        # the RAW text, so it is taken and scrubbed here rather than dropped:
        # a packet that silently forgot what was asked would send the owner
        # back to the asker to find out, which is the round trip this path
        # exists to save.
        shaped = (reading or {}).get("question") or {}
        supplied = question if question is not None else shaped.get("message")
        raw, seen = scrub(supplied)
        hits.extend(seen)
        question_part = {
            "status_code": shaped.get("status_code") if question is None else None,
            "message": raw,
            "source": ("supplied by the caller, redacted here" if question is not None
                       else "the reading's own `question`, redacted here because "
                            "this trigger did not come from the chain")}
        recommendation = {"kind": None, "id_prefix": None, "offered_id": None,
                          "kb_file": None, "assigned": False,
                          "because": "no improvement candidate was produced for "
                                     "this trigger, so the chain recommends "
                                     "nothing and this module will not invent a "
                                     "recommendation on its behalf"}
        # Carried from the reading rather than assumed empty. A `KB_UNREADABLE`
        # reading is 「权限不够」 as §10 means it, and it names the files it read
        # and the pattern that matched none of them — which is 「已查事实」 for
        # this trigger. An `AMBIGUOUS_MATCH` reading carries candidates. Writing
        # `null` here would be this module discarding a measurement the chain
        # did make, and then telling the owner nothing was looked at.
        attempts = {"matched_by": (reading or {}).get("matched_by"),
                    "redirects_applied": (reading or {}).get("redirects") or [],
                    "candidates": (reading or {}).get("candidates") or [],
                    "rules_left_unevaluated": (reading or {}).get("missing") or [],
                    "because": ("no improvement candidate was produced for this "
                                "trigger. The chain either did not run or stopped "
                                "before it reached a rule; the four fields above "
                                "are what its reading carried, and 「已查事实」 names "
                                "the stop")}
        facts = {
            "kb_root": (reading or {}).get("kb_root"),
            "same_repository": ((reading or {}).get("boundary") or {}).get("same_repository"),
            "entries_reached": None,
            "measured_by_the_chain": (reading or {}).get("unreadable"),
        }

    original_message = {
        "conversation": conversation,
        "message_ref": None,
        "because": ("no reader in this chain carries a message id, and a "
                    "reference invented here would resolve to nothing. A packet "
                    "whose 「原消息引用」 points nowhere is worse than one that "
                    "says it has none — the owner would go looking"),
    }
    return ({"original_message": original_message, "facts_checked": facts,
             "attempts": attempts, "question": question_part,
             "recommendation": recommendation}, hits)


def packet(reading: dict | None = None, *, trigger: str | None = None,
           question: str | None = None, conversation: str | None = None) -> dict:
    """§10's 小决策包, assembled from a support reading and nothing else.

    `reading` is the dict `intake/support.py#answer` returned. It is taken as a
    value rather than re-derived, so this module cannot become a second place
    where the knowledge base is read — and so that the two halves are provably
    about one question.
    """
    spec = contract()
    trigger = _trigger_for(reading, trigger)
    parts, hits = _parts(reading, trigger, question, conversation)
    body = {"schema": "serveros/support-handoff.v1", "trigger": trigger,
            "trigger_says": spec["trigger"]["values"][trigger].strip(),
            **parts}
    if hits:
        # Recorded, not swallowed. See `scrub`.
        body["scrubbed_on_the_way_in"] = hits
    # No timestamp in the body, and that is deliberate: the reference below is
    # then the same for two handoffs of one question, which is what makes them
    # recognisable as one packet instead of two.
    canonical = json.dumps(body, sort_keys=True, ensure_ascii=False,
                           separators=(",", ":")).encode("utf-8")
    body["packet_ref"] = "sha256:" + hashlib.sha256(canonical).hexdigest()[:32]
    return body


# ---------------------------------------------------------------------------
# The anchors. §10's two, in §10's order.
# ---------------------------------------------------------------------------
def _ask_queue(queue, pkt: dict) -> dict:
    if queue is None:
        return {"state": "UNBOUND", "reference": None, "provenance": None,
                "reason": ("no queue was supplied. §10 says 「已有工作/通知队列」 "
                           "and this module will not mint one — a queue invented "
                           "here would be a second delivery path with its own "
                           "opinion about how often a person is told the same "
                           "thing")}
    accept = getattr(queue, "accept", None)
    if not callable(accept):
        raise Refused(
            f"the queue {queue!r} has no callable `accept`. This module's queue "
            f"is an adapter with one method — `accept(packet)` returning the "
            f"reference it will hold the packet under, or a falsey value if it "
            f"will not take it — and an object of any other shape is a caller's "
            f"mistake rather than a reading")
    try:
        reference = accept(pkt)
    except Exception as exc:
        return {"state": "FAILED", "reference": None, "provenance": None,
                "reason": f"{type(exc).__name__}: {exc}"}
    if not reference:
        return {"state": "REFUSED", "reference": None, "provenance": None,
                "reason": ("the queue answered and did not name the packet. "
                           "「先入已有工作/通知队列并得到回执」 — a receipt IS the "
                           "queue's own reference for what it took, so a queue "
                           "that cannot say what it accepted has not been shown "
                           "to have accepted anything")}
    return {"state": "ACCEPTED", "reference": str(reference), "reason": None,
            # Where the reference came from, when the adapter says. An adapter
            # that hands back a reference a CALLER already holds — which is what
            # a command line with no queue client can do, and nothing more —
            # must not be able to pass for one that called a queue.
            "provenance": getattr(queue, "provenance", None)}


def _bind_owner(owner) -> dict:
    if owner is None or not str(owner).strip():
        return {"state": "UNBOUND", "ref": None,
                "reason": ("no owner was bound. §10's fallback goes 「交绑定的"
                           "个人工作 owner，而非擅自指定同事或 SLA」 — choosing "
                           "one here would be 擅自指定同事, so the packet waits "
                           "for a binding instead")}
    return {"state": "BOUND", "ref": str(owner), "reason": None}


def _record_delivery(queue_state: str, owner: dict, delivery: dict | None,
                     packet_ref: str) -> dict:
    """What may be said afterwards, and the one order this module keeps.

    §10's order is 「先入已有工作/通知队列并得到回执，再按许可给 owner 提醒」 —
    the queue first, the reminder second. That is a rule and not a performance
    choice: a reminder that arrives before anything durable is a message nobody
    can act on. So a delivery reported BEFORE the queue took the packet is
    refused rather than recorded, because recording it would make this module
    the thing that agreed a person had been told about nothing.
    """
    if queue_state != "ACCEPTED":
        if delivery is not None:
            raise Refused(
                f"a delivery was reported for a packet the queue did not accept "
                f"(queue state {queue_state!r}). §10 orders 「先入…队列并得到回执，"
                f"再…提醒」, and recording the reminder ahead of the durable copy "
                f"is the one order this module exists to keep")
        return {"state": "PENDING_DELIVERY",
                "reason": {"UNBOUND": "nothing durable holds the packet, so the "
                                      "reminder was not attempted",
                           "REFUSED": "the queue declined the packet, so the "
                                      "reminder was not attempted",
                           "FAILED": "the queue raised, so the reminder was not "
                                     "attempted"}.get(queue_state, "the queue did "
                                                                   "not accept the packet"),
                "at": None, "detail": {}}
    if owner["state"] != "BOUND":
        if delivery is not None:
            raise Refused(
                "a delivery was reported for a packet with no owner bound. A "
                "reminder with no addressee is not a delivery, and recording "
                "one would let 「the transport took it」 stand for 「a person was "
                "told」")
        return {"state": "PENDING_DELIVERY", "at": None, "detail": {},
                "reason": ("the packet is held and no owner is bound, so there "
                           "was nobody to remind")}
    if delivery is None:
        return {"state": "PENDING_DELIVERY", "at": None, "detail": {},
                "reason": ("the packet is held and no delivery was reported. "
                           "This module does not send, so silence here means the "
                           "caller has not yet walked the delivery path")}
    state = str(delivery.get("state") or "")
    if state == "PENDING_DELIVERY":
        raise Refused(
            "PENDING_DELIVERY is this module's word for 「nothing was "
            "attempted」, not a transport's answer. Accepting it here would let "
            "a caller's 「nobody was asked」 be read as 「the transport was asked "
            "and could not」")
    if state not in REPORTABLE:
        raise Refused(f"{state!r} is not one of {sorted(REPORTABLE)}; "
                      f"a transport answers with one of those four, and the "
                      f"fifth word belongs to the routing rather than to a "
                      f"transport")
    return {"state": state, "at": delivery.get("at"),
            "reason": delivery.get("reason") or f"delivery answered {state}",
            "detail": delivery.get("detail") or {}}


def handoff(pkt: dict, *, queue=None, owner=None,
            delivery: dict | None = None) -> dict:
    """Route one packet, and record what each anchor answered.

    `pkt` is a packet from `packet()`. It is a value: this function does not
    rebuild it, so what was routed is what a caller can read.
    """
    if not isinstance(pkt, dict) or "packet_ref" not in pkt:
        raise Refused("`pkt` is not a packet from `packet()`; it carries no "
                      "`packet_ref`, so nothing downstream could say which "
                      "packet it received")
    routing = {"queue": _ask_queue(queue, pkt), "owner": _bind_owner(owner)}
    recorded = _record_delivery(routing["queue"]["state"], routing["owner"],
                                delivery, pkt["packet_ref"])
    notified = recorded["state"] == "DELIVERED"
    handed_over = routing["queue"]["state"] == "ACCEPTED"
    reference, owner_ref = routing["queue"]["reference"], routing["owner"]["ref"]

    if notified:
        statement = (f"the packet is held by {reference} and the reminder was "
                     f"ACCEPTED by the transport for {owner_ref}. That is not "
                     f"the same as {owner_ref} having read it.")
    elif recorded["state"] != "PENDING_DELIVERY":
        statement = (f"the packet is held by {reference}; the reminder to "
                     f"{owner_ref} did not arrive ({recorded['state']}), so "
                     f"{owner_ref} has not been told.")
    else:
        statement = (f"nobody has been told. {recorded['reason']}; the packet "
                     f"is {'held by ' + reference if handed_over else 'not held anywhere'}.")

    return {
        "schema": "serveros/support-handoff.v1",
        "packet_ref": pkt["packet_ref"],
        "trigger": pkt["trigger"],
        "routing": routing,
        "delivery": recorded,
        "handed_over": handed_over,
        # Set ONLY for DELIVERED, and the boolean is derived from the state
        # rather than passed separately so the two cannot disagree.
        "notified": notified,
        "outcome": "NOTIFIED" if notified else "NOT_NOTIFIED",
        "statement": statement,
        "not_claimed": list(contract()["not_claimed"]),
    }


def run(reading: dict | None = None, *, trigger: str | None = None,
        question: str | None = None, conversation: str | None = None,
        queue=None, owner=None, delivery: dict | None = None) -> dict:
    """`packet()` then `handoff()`, returned as ONE document.

    The packet is built once and carried in the result rather than being
    rebuilt by the reader, so that what was routed and what is shown are the
    same object. A caller that wanted only the routing can read `routing` and
    ignore the rest.
    """
    pkt = packet(reading, trigger=trigger, question=question,
                 conversation=conversation)
    result = handoff(pkt, queue=queue, owner=owner, delivery=delivery)
    result["packet"] = pkt
    return result


# ---------------------------------------------------------------------------
# Reading it back
# ---------------------------------------------------------------------------
def exit_code_for(payload: dict) -> int:
    codes = contract()["exit_codes"]
    outcome = payload.get("outcome")
    if outcome not in codes:
        raise Refused(f"{outcome!r} is not an outcome this contract declares; "
                      f"the declared set is {sorted(codes)}")
    return codes[outcome]


def render(payload: dict) -> str:
    """The same reading as lines, for a person rather than for a parser."""
    routing = payload["routing"]
    lines = [f"outcome     {payload['outcome']}  ({payload['statement']})",
             f"packet      {payload['packet_ref']}  trigger {payload['trigger']}"]
    if payload.get("packet"):
        question = payload["packet"]["question"]
        lines.append(f"question    status={question['status_code']} "
                     f"message={question['message']!r}")
        facts = payload["packet"]["facts_checked"]
        lines.append(f"facts       {facts['measured_by_the_chain']}")
        rec = payload["packet"]["recommendation"]
        if rec.get("offered_id"):
            lines.append(f"offered     {rec['offered_id']} under "
                         f"{rec['id_prefix']} ({rec['kb_file']})  NOT assigned")
    queue = routing["queue"]
    lines.append(f"queue       {queue['state']}"
                 + (f" as {queue['reference']}" if queue["reference"] else ""))
    if queue.get("provenance"):
        lines.append(f"            reference {queue['provenance']}")
    if queue["reason"]:
        lines.append(f"            {queue['reason']}")
    owner = routing["owner"]
    lines.append(f"owner       {owner['state']}"
                 + (f" {owner['ref']}" if owner["ref"] else ""))
    if owner["reason"]:
        lines.append(f"            {owner['reason']}")
    recorded = payload["delivery"]
    lines.append(f"delivery    {recorded['state']} at {recorded['at']}")
    if recorded["reason"]:
        lines.append(f"            {recorded['reason']}")
    lines.append(f"notified    {payload['notified']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--trigger", required=True,
                        help="one of the contract's five; required, because "
                             "this tool will not derive a fallback from nothing")
    parser.add_argument("--question",
                        help="the question's own words, if this trigger came "
                             "from outside the support chain. Redacted here")
    parser.add_argument("--conversation",
                        help="a conversation REF, never a body")
    parser.add_argument("--as-json", action="store_true")
    args = parser.parse_args(argv)
    try:
        pkt = packet(None, trigger=args.trigger, question=args.question,
                     conversation=args.conversation)
        # No queue and no owner: this entry point exists to show the packet and
        # the honest reading of an unrouted one, not to route anything.
        payload = handoff(pkt)
        payload["packet"] = pkt
    except Refused as exc:
        sys.stderr.write(f"serveros-support-handoff: {exc}\n")
        return EXIT_FAULT
    if args.as_json:
        json.dump(payload, sys.stdout, indent=2, sort_keys=True, ensure_ascii=False)
        sys.stdout.write("\n")
    else:
        sys.stdout.write(render(payload) + "\n")
    return exit_code_for(payload)


if __name__ == "__main__":
    raise SystemExit(main())
