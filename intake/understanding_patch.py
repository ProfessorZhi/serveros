#!/usr/bin/env python3
"""The write half of B2: a patch a person confirms, never a card rewritten.

    serverosctl work understanding-patch [--decisions F] --write OUT.json
    serverosctl work understanding-patch [--decisions F] --verify OUT.json

`intake/understanding_delta.py` reads the understanding layer and answers one
question per card: does what the card says it read match what its sources now
hold. It writes nothing, and it proposes a range — a span to re-read — and never
a statement about what that span says, because it opens no message body and no
document text.

This module takes that answer and turns it into the thing a person acts on, and
the boundary is the whole point of the file:

    THE PATCH IS A PROPOSAL. IT IS NOT A CONFIRMATION, AND THIS MODULE CANNOT
    PRODUCE ONE ON ITS OWN AUTHORITY.

Three rules enforce that rather than recommend it.

  1. **A confirmation is authored input.** Confirmation states are read from a
     decisions file that this module reads and never writes. With no decisions
     file, every item is `AWAITING` — there is no code path from a derived
     record to `CONFIRMED`. The `ast` property test is the evidence: no writer
     for the decisions path exists in this module, only a reader.

  2. **A confirmation covers the revision it was made about, and nothing else.**
     An item is identified by (card, capture object, kind, from, to) TOGETHER
     WITH two digests — the card's own text, and the content the span names. A
     source that moves again produces a different range, a message rewritten
     under an unchanged span produces a different CONTENT, and a card rewritten
     under its own path produces a different baseline. Each of the three makes a
     new item, and the decision about the old one does not come along: the item
     returns to `AWAITING` and the decision is reported in `stale_decisions`
     with the part that moved named. A confirmation that followed a card
     automatically is not a confirmation of anything a person read — and neither
     is one that outlives the text it was about. A decisions file that names no
     content is refused rather than accepted as a decision about a place.

  3. **Every item carries the version pair it was computed from.** Where the
     card had got to, and where the source had got to, at the moment of
     comparison: positions for a chat, content digests for a document. A
     proposal without them is a proposal a reader cannot check, and "the card
     is behind its source by 644" is exactly the sentence that gets believed
     without being checked. The identity digests of rule 2 are one-way and
     carry no text; they answer "is this the same" and never "what does it say",
     which is the only reason this module can hold them without breaking the
     rule below.

What the design calls a *Claim* patch is realised here as version-anchored
*items*, and deliberately not as anything named `claim`. A claim reads as a
statement about content, and this module — like the reader it composes — opens
no content. Whoever writes what the new material MEANS is the semantic layer,
and it inherits the same rule: it proposes, a person confirms.

No clock, no network, no database. The patch is a pure function of the delta
report and the decisions file, so two runs over the same inputs produce
identical bytes and `--verify` can prove it.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

SCHEMA = "serveros/understanding-patch.v1"
DECISIONS_SCHEMA = "serveros/understanding-decisions.v1"

AWAITING, CONFIRMED, REJECTED = "AWAITING", "CONFIRMED", "REJECTED"
CONFIRMATIONS = (AWAITING, CONFIRMED, REJECTED)

#: States whose cards this module proposes work for. The rest are counted and
#: named in `no_item` — a patch that lists three items must not read as a layer
#: of three cards.
ACTIONABLE = ("BEHIND", "STALE_IN_PLACE")

#: Why a card in each non-actionable state produces no item. A constant and not
#: the card's own `why`: the reason there is nothing to do about an unmeasurable
#: card is a fact about this module's rules, and copying a card's prose here
#: would make the patch carry text it did not compute.
NO_ITEM_BECAUSE = {
    "CURRENT": "every citation that names a reach is at the source's newest "
               "position, so there is nothing to re-read",
    "UNMEASURABLE": "the card names sources without naming how far into them it "
                    "read, so no lag exists to act on — reading further is not "
                    "something the records say is needed",
    "UNTRACEABLE": "the card cites no raw path at all, so there is no source to "
                   "compare it against; the defect is in the card, and the fix "
                   "is to write a citation, not to re-read one",
    "UNREADABLE": "the card could not be read, so nothing about it is known",
}

ITEM_PRODUCED = {
    "card": "the understanding card, relative to the records root, echoed from "
            "the range",
    # The understanding baseline. A card rewritten in place keeps its path and
    # can keep every span it cites, so the path alone does not name the text a
    # proposal was computed against.
    "card_digest": "the card's own text as a digest, from the reader's "
                   "`read_card` — the baseline this proposal is about",
    "state": "the card's verdict from the reader, copied",
    "state_why": "the verdict's own reason, copied verbatim",
    "source": "the source path as the CARD wrote it, echoed from the range",
    "manifest": "the capture object the range was measured against, from the "
                "reader's capture index — the disambiguator when one cited path "
                "matched several",
    "review": "which of the reader's range kinds this is: BEYOND_APPLIED, "
              "REVISITED_IN_PLACE or WHOLE_DOCUMENT",
    "from_position": "first position to re-read, from the range; null when the "
                     "range is not positional",
    "to_position": "last position to re-read, from the range; null when the "
                   "range is not positional",
    "applied": "the newest position the card cites — the CARD's side of the "
               "comparison, from the range",
    "applied_low": "the oldest position the card cites, from the range",
    "source_max": "the newest position the source now holds — the SOURCE's side, "
                  "from the range",
    "source_count": "`sync.msg_count`, the collector's count, from the range",
    "cited_digest": "the digest prefix the card quoted, for a document range",
    "content_sha256": "the digest the manifest records now, for a document range",
    # The standing, copied from the range. `cited_digest` and `content_sha256`
    # say the card is not at the manifest's version; they cannot say whether the
    # document moved away from the card or the citation names a digest this
    # manifest does not account for. A person confirming this proposal is
    # confirming one of those two propositions and not the other, so the item
    # has to carry which one it is.
    "cited_digest_standing": "one of the reader's CITED_STANDINGS, copied from "
                             "the range: which of the two versions the manifest "
                             "keeps the cited digest is, or that there is no "
                             "previous version to compare against",
    "prev_content_sha256": "the digest the manifest records as its predecessor, "
                           "copied from the range",
    # The version half, copied from the range. `content_sha256` is what the
    # DOCUMENT now holds and `content_identity` is what the RANGE is about;
    # they are the same value for a document and only the second exists for a
    # chat span. A decision that did not carry this would be a decision about a
    # place, and the content at that place can be rewritten under it.
    "content_identity": "a digest of the records this range names, from the "
                        "reader — the version this proposal is about",
    "content_identity_why": "why `content_identity` is null, from the reader; "
                            "null when the digest is present, because then the "
                            "question does not arise. Every key this writer "
                            "emits is present on every item — a key that "
                            "appeared only for the items it applies to would "
                            "make a consumer's field access a question about "
                            "which item it is holding",
    "revisions": "edited/deleted counts inside the range, when the range is one "
                 "the card already read",
    "latest_create_time": "`sync.latest_create_time`, from the range",
    "last_message_id": "`sync.last_message_id`, from the range",
    "derived_from": "the canonical objects this item was computed from: the card "
                    "and the capture object's manifest",
    "confirmation": "one of CONFIRMATIONS. AWAITING unless an authored decision "
                    "says otherwise — this module cannot mark an item CONFIRMED "
                    "on its own authority",
    "decided_by": "the actor named in the decisions file, when a decision applied",
    "decided_at": "the time named in the decisions file, when a decision applied; "
                  "authored input, never this process's clock",
}

PATCH_PRODUCED = {
    "schema": "this module's own schema string",
    "root": "the understanding root the cards were read from, from the reader",
    "cards_read": "how many cards the reader judged — the denominator",
    "states": "how many cards are in each of the reader's states",
    "no_item": "per state, how many cards produced no item and why, so a patch "
               "of three items cannot read as a layer of three cards",
    "awaiting": "items whose confirmation is AWAITING",
    "confirmed": "items an authored decision marked CONFIRMED",
    "rejected": "items an authored decision marked REJECTED",
    "decisions_path": "the decisions file this patch was built against, or null",
    "stale_decisions": "decisions whose exact proposal is no longer in this "
                       "patch, each with the identity it was made against and "
                       "why it no longer applies. The reason names which part "
                       "moved — the range, the content under it, or the card — "
                       "because those call for different next actions. Reported "
                       "rather than dropped: a confirmation that silently stops "
                       "applying is worse than one that was never made",
    "skipped": "what this module declined to emit or folded into another item, "
               "each with why — so the item count never differs from the ranges "
               "the reader printed without a line here saying so",
    "items": "the proposals, sorted by (card, manifest, review, from, to)",
}

DECISION_PRODUCED = {
    "card": "the card the decision was made about",
    "card_digest": "the card's text the decision was made against. Required, "
                   "and required to be present rather than merely non-null: a "
                   "decision that does not say which revision of the card it "
                   "was about is a decision about a path",
    "manifest": "the capture object the decision was made about",
    "review": "the range kind the decision was made about",
    "from_position": "the first position the decision was made about",
    "to_position": "the last position the decision was made about",
    "content_identity": "the content the decision was made about, as the "
                        "reader measured it. Required and required present, for "
                        "the same reason as `card_digest`; null is a legitimate "
                        "value only when the proposal itself carried null and "
                        "said why, which is a decision about a span whose "
                        "content could not be identified — not a decision about "
                        "whatever is there now",
    "decision": "CONFIRMED or REJECTED — never AWAITING, which is the absence "
                "of a decision rather than one",
    "by": "who decided. Required: a confirmation nobody is named for is an "
          "assertion by the tool",
    "at": "when they decided, as they wrote it",
}


class Refused(RuntimeError):
    """This writer will not answer.

    Raised for a defect in the WRITER — an emitted key no producer table names,
    a destination that is not absolute — and for an input that would make the
    patch say something untrue. It is deliberately not the exception for one
    unreadable card: that is a fact about the card, and it is reported.
    """


class Malformed(Refused):
    """A fact about the FILE that was read. A decision line with no author."""


def _require(value, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise Malformed(f"{label} must be a non-empty string")
    return value.strip()


def no_unsourced(mapping: dict, table: dict, where: str) -> dict:
    """Refuse to emit a key whose value the caller cannot name a source for.

    The same guard the reader carries, for the same reason: it is applied where
    the record is EMITTED, so a field cannot be added to one branch and be
    sourced in none.
    """
    unexpected = sorted(set(mapping) - set(table))
    if unexpected:
        raise Refused(
            f"this writer would emit "
            f"{', '.join(f'`{where}.{key}`' for key in unexpected)}, which the "
            f"producer table does not name a source for; add the producer or "
            f"drop the field")
    return mapping


#: The fields a proposal's identity is made of, in order. One list, and both
#: halves read it: a reader building this tuple and a writer building it
#: separately would agree until the first time either changed, and the symptom
#: would be a confirmation that no longer matches the proposal it was made for.
#: That failure is silent, which is why the list is here rather than twice.
#:
#: Every index into the identity tuple below is taken through `place_of` and
#: `version_of` rather than written as `key[5]`. The two halves mean different
#: things — one is what a person read, the other is which revision of it — and
#: `key[5]` reads the second only until somebody inserts a field.
IDENTITY_FIELDS = ("card", "manifest", "review", "from_position",
                   "to_position", "card_digest", "content_identity")

#: The half a person can read aloud and repeat in a message.
PLACE_FIELDS = IDENTITY_FIELDS[:5]

#: The half nobody can read aloud and every decision depends on.
VERSION_FIELDS = IDENTITY_FIELDS[5:]


def place_of(record: dict) -> tuple:
    """Where a proposal is: the card, the capture object, the kind, the span."""
    return tuple(record.get(field) for field in PLACE_FIELDS)


def version_of(record: dict) -> tuple:
    """Which revision of that place: the card's own text, the content's."""
    return tuple(record.get(field) for field in VERSION_FIELDS)


def identity_of(record: dict) -> tuple:
    """The identity fields of an item or of a decision, in one order."""
    return place_of(record) + version_of(record)


def item_key(item: dict) -> tuple:
    """What a decision is about: the exact proposal, not the card.

    The capture object is in the key and not just the cited path, because one
    cited path can match several captures; the positions are in it because a
    range is what a person read; and the two digests are in it because a range
    is not a revision. `content_identity` covers the content the range names and
    `card_digest` covers the understanding baseline it was judged against — so a
    message rewritten under an unchanged span, or a card rewritten under an
    unchanged path, makes a DIFFERENT proposal, and a decision about the older
    one stops applying rather than being inherited by text nobody confirmed.
    """
    return identity_of(item)


def read_decisions(path: Path | str) -> dict[tuple, dict]:
    """Every authored decision, keyed by the proposal it was made about.

    Read-only, deliberately: this module has no writer for this file. A record
    here says a PERSON decided something, and a tool that could write it would
    be marking its own proposal confirmed.
    """
    decisions: dict[tuple, dict] = {}
    text = Path(path).read_text(encoding="utf-8")
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError as exc:
            raise Malformed(f"{path}:{number}: invalid JSON") from exc
        if not isinstance(entry, dict):
            raise Malformed(f"{path}:{number}: a decision must be an object")
        unexpected = sorted(set(entry) - set(DECISION_PRODUCED))
        if unexpected:
            raise Malformed(
                f"{path}:{number}: unexpected field(s) "
                f"{', '.join(f'`{key}`' for key in unexpected)}; a decision "
                f"records what was decided about what, by whom, and nothing else")
        state = entry.get("decision")
        if state not in (CONFIRMED, REJECTED):
            raise Malformed(
                f"{path}:{number}: `decision` is {state!r}; a decision is "
                f"CONFIRMED or REJECTED. AWAITING is the absence of a decision, "
                f"so writing it down would be recording that somebody decided "
                f"nothing")
        # PRESENT, not merely non-null. `entry.get` returns `None` for a field
        # that is absent and for one written as null, and the difference is the
        # whole rule: null is the honest value for a proposal whose content the
        # reader could not identify, while an absent field is a decision that
        # did not say what it was about. Only the first may inherit.
        for field in ("card_digest", "content_identity"):
            if field not in entry:
                raise Malformed(
                    f"{path}:{number}: `{field}` is absent, so this decision "
                    f"names a place and not a revision. A message rewritten "
                    f"under an unchanged path and span, or a card rewritten "
                    f"under its own path, would inherit the decision and record "
                    f"somebody as having confirmed text they never read. Copy "
                    f"the field from the item in the patch; null is accepted "
                    f"only when the item itself carries null and says why")
        record = no_unsourced({
            "card": _require(entry.get("card"), f"{path}:{number}: card"),
            "card_digest": entry.get("card_digest"),
            "manifest": entry.get("manifest"),
            "review": _require(entry.get("review"), f"{path}:{number}: review"),
            "from_position": entry.get("from_position"),
            "to_position": entry.get("to_position"),
            "content_identity": entry.get("content_identity"),
            "decision": state,
            "by": _require(entry.get("by"), f"{path}:{number}: by"),
            "at": entry.get("at"),
        }, DECISION_PRODUCED, "decision")
        key = identity_of(record)
        if key in decisions:
            raise Malformed(
                f"{path}:{number}: two decisions about one proposal "
                f"({key[0]} {key[2]} {key[3]}–{key[4]}); which one a reader "
                f"should act on is not something this module can choose")
        decisions[key] = record
    return decisions


def _locator(relative: str) -> str:
    return f"records://{relative}"


def item_for(card: dict, range_: dict, state: str, state_why: str) -> dict:
    """One actionable range as a proposal, with the versions it rests on.

    Three of them, not one: where the card had got to, where the source has got
    to, and WHICH content the range is about. The first two are a lag; the third
    is what makes a confirmation mean a revision rather than a place.
    """
    manifest = range_.get("manifest")
    derived = [_locator(card["card"])]
    if manifest:
        # The capture object's own manifest is a canonical object; the reader
        # took this key from the capture index, so it names something on disk.
        derived.append(_locator(f"information/raw/{manifest}"))
    item = {
        "card": card["card"],
        "card_digest": card.get("card_digest"),
        "state": state,
        "state_why": state_why,
        "source": range_["source"],
        "manifest": manifest,
        "review": range_["kind"],
        "from_position": range_.get("from_position"),
        "to_position": range_.get("to_position"),
        "applied": range_.get("applied"),
        "applied_low": range_.get("applied_low"),
        "source_max": range_.get("source_max"),
        "source_count": range_.get("source_count"),
        "cited_digest": range_.get("cited_digest"),
        "content_sha256": range_.get("content_sha256"),
        "cited_digest_standing": range_.get("cited_digest_standing"),
        "prev_content_sha256": range_.get("prev_content_sha256"),
        "content_identity": range_.get("content_identity"),
        "content_identity_why": range_.get("content_identity_why"),
        "revisions": range_.get("revisions"),
        "latest_create_time": range_.get("latest_create_time"),
        "last_message_id": range_.get("last_message_id"),
        "derived_from": derived,
        "confirmation": AWAITING,
        "decided_by": None,
        "decided_at": None,
    }
    return no_unsourced(item, ITEM_PRODUCED, "item")


def _stale_why(decision: dict, current: dict | None) -> str:
    """Why a decision no longer applies, named by which part of it moved.

    "The proposal changed" is true of every case here and useful for none of
    them. A person whose confirmation lapsed needs to know whether the source
    moved past the range, whether the content UNDER the same range was
    rewritten, or whether the card they were reading was rewritten — those are
    three different next actions, and a single sentence covering all three
    would be the kind of broad statement this module refuses elsewhere.
    """
    if current is None:
        return ("the card no longer proposes this exact range at all — the "
                "source moved past it, or the card stopped citing it — so a "
                "decision about it is not a decision about what is proposed now")
    moved = []
    if current.get("card_digest") != decision.get("card_digest"):
        moved.append("the card's own text changed")
    if current.get("content_identity") != decision.get("content_identity"):
        moved.append("the content this range names changed")
    if not moved:
        # Unreachable while the identity is a function of the range, and left
        # as a sentence rather than an assertion: a stale decision that cannot
        # explain itself must still be reported, not dropped.
        return ("this proposal was replaced by one carrying the same identity "
                "fields, which this module cannot account for")
    return ("the same card, capture and span are now proposed as a different "
            "revision: " + " and ".join(moved) + " — the decision was about a "
            "revision that is no longer the one on disk")


def items_for(report: dict, decisions: dict[tuple, dict]) -> tuple[list, list, dict]:
    """Every item the report implies, with decisions applied.

    Returns (items, stale_decisions, no_item, skipped). `no_item` counts the
    cards that produced nothing, per state — the denominator, kept beside the
    proposals; `skipped` says what was folded or declined, so a count that
    differs from the ranges printed is explained rather than noticed.
    """
    items: list[dict] = []
    no_item: dict[str, int] = {}
    skipped: list[str] = []
    seen: dict[tuple, dict] = {}
    # The same proposals keyed by the legible half of their identity, so a
    # superseded decision can say WHICH of its two version halves moved. A
    # decision reported as stale without that is a decision a person has to
    # re-derive by hand.
    by_place: dict[tuple, dict] = {}
    for card in report["cards"]:
        verdict = card["judged"]
        state = verdict["state"]
        ranges = verdict.get("review_ranges") or []
        if state not in ACTIONABLE or not ranges:
            reason = NO_ITEM_BECAUSE.get(state)
            if reason is None:
                reason = ("the reader reports this card as needing work and "
                          "proposes no range for it, which is a gap in the "
                          "reader and not a settled card")
            no_item.setdefault(state, 0)
            no_item[state] += 1
            continue
        for range_ in ranges:
            item = item_for(card, range_, state, verdict["why"])
            key = item_key(item)
            known = seen.get(key)
            if known is not None:
                # A card that cites one source at one reach twice has proposed
                # one thing to read, not two. Folded, and said out loud: a
                # duplicate that vanished would make the item count disagree
                # with the ranges the reader printed, and the count is the
                # number a person checks.
                if known != item:
                    raise Refused(
                        f"two proposals for {key[0]} share an identity "
                        f"({key[2]} {key[3]}–{key[4]} against {key[1]}) and "
                        f"differ in what they say. A decision about that "
                        f"identity could not be attributed to one of them, so "
                        f"neither is emitted")
                skipped.append(
                    f"{key[0]} cites {key[1] or key[0]} at {key[2]} "
                    f"{key[3]}–{key[4]} more than once; one proposal is emitted "
                    f"for it")
                continue
            seen[key] = item
            by_place[place_of(item)] = item
            decision = decisions.get(key)
            if decision:
                item["confirmation"] = decision["decision"]
                item["decided_by"] = decision["by"]
                item["decided_at"] = decision["at"]
            items.append(item)
    # Built from the decision RECORD rather than from its key: the key is a
    # tuple whose positions mean nothing on their own, and a stale record that
    # said `key[5]` would be the one place a reader could not check the two
    # digests against the decision they came from.
    stale = [
        {**{field: record.get(field) for field in IDENTITY_FIELDS},
         "decision": record["decision"], "by": record["by"],
         "why": _stale_why(record, by_place.get(place_of(record)))}
        for record in sorted(decisions.values(),
                             key=lambda r: repr(identity_of(r)))
        if identity_of(record) not in seen
    ]
    items.sort(key=item_key)
    return items, stale, dict(sorted(no_item.items())), sorted(set(skipped))


def build(report: dict, decisions: dict[tuple, dict] | None = None,
          decisions_path: str | None = None) -> dict:
    """The patch: proposals, the census beside them, and the decisions applied."""
    items, stale, no_item, skipped = items_for(report, decisions or {})
    states: dict[str, int] = {}
    for card in report["cards"]:
        state = card["judged"]["state"]
        states[state] = states.get(state, 0) + 1
    return no_unsourced({
        "schema": SCHEMA,
        "root": report["root"],
        "cards_read": len(report["cards"]),
        "states": dict(sorted(states.items())),
        "no_item": no_item,
        "awaiting": sum(1 for item in items if item["confirmation"] == AWAITING),
        "confirmed": sum(1 for item in items if item["confirmation"] == CONFIRMED),
        "rejected": sum(1 for item in items if item["confirmation"] == REJECTED),
        "decisions_path": decisions_path,
        "stale_decisions": stale,
        "skipped": skipped,
        "items": items,
    }, PATCH_PRODUCED, "patch")


def render(patch: dict) -> list[str]:
    """The patch as a person reads it. The terminal is the surface there is.

    Nothing here is a claim about content: each line names what moved and what
    a person would have to read to say anything about it.
    """
    lines = [
        f"根        {patch['root']}",
        f"对账      {patch['cards_read']} 份卡  ·  "
        + " · ".join(f"{name} {count}" for name, count in patch["states"].items()),
        f"提议      {len(patch['items'])} 条  ·  待确认 {patch['awaiting']}"
        + (f"  ·  已确认 {patch['confirmed']}" if patch["confirmed"] else "")
        + (f"  ·  已否决 {patch['rejected']}" if patch["rejected"] else ""),
        "",
    ]
    for state, count in patch["no_item"].items():
        lines.append(f"  {count} 份 {state} 无提议 —— {NO_ITEM_BECAUSE.get(state, '')}")
    if patch["no_item"]:
        lines.append("")
    for item in patch["items"]:
        lines.append(f"{item['card'].split('/', 3)[-1]}  {item['state']}  "
                     f"{item['confirmation']}")
        lines.append("    " + _item_line(item))
        if item["decided_by"]:
            lines.append(f"    由 {item['decided_by']} 决定"
                         + (f"（{item['decided_at']}）" if item["decided_at"] else ""))
    for decision in patch["stale_decisions"]:
        lines.append(f"! 失效决定  {decision['card'].split('/', 3)[-1]}  "
                     f"{decision['review']} {decision['from_position']}–"
                     f"{decision['to_position']}  {decision['decision']} "
                     f"by {decision['by']} —— {decision['why']}")
    for line in patch["skipped"]:
        lines.append(f"! 合并      {line}")
    return lines


def _item_line(item: dict) -> str:
    """One proposal, with the two versions it was computed from."""
    source = item["source"].rsplit("/", 2)[-2:]
    where = "/".join(source)
    if item["review"] == "BEYOND_APPLIED":
        return (f"{where}  卡读到 {item['applied']} / 源现在 {item['source_max']}"
                f"（{item['source_count']} 条）  重读 {item['from_position']}"
                f"–{item['to_position']}")
    if item["review"] == "REVISITED_IN_PLACE":
        revisions = item.get("revisions") or {}
        return (f"{where}  卡读到 {item['applied']} / 源现在 {item['source_max']}"
                f"  已读区间被改：改 {revisions.get('edited', 0)} "
                f"删 {revisions.get('deleted', 0)}  重读 {item['from_position']}"
                f"–{item['to_position']}")
    # The standing decides the sentence, and the arrow is only one of the three
    # answers. The names are literals the way this file already names the range
    # kinds: the source of truth is `understanding_delta.CITED_STANDINGS`, and
    # this module copies the value rather than defining it.
    if item.get("cited_digest_standing") == "CITED_IS_PREVIOUS_VERSION":
        # The one standing that supports the arrow: the cited digest IS the
        # version the manifest records as this document's predecessor, so the
        # transition is one that was measured.
        span = f"摘要 {item['cited_digest']} → {item['content_sha256']}"
    elif not item.get("content_sha256"):
        # Nothing on the manifest records the digest now, so there is nothing to
        # compare the citation against and no arrow to draw.
        span = f"摘要 {item['cited_digest']}"
    elif item.get("prev_content_sha256"):
        span = (f"引的 {item['cited_digest']} ≠ 现版 {item['content_sha256']}、"
                f"≠ 上一版 {item['prev_content_sha256']}")
    else:
        span = (f"引的 {item['cited_digest']} ≠ 现版 {item['content_sha256']}；"
                f"manifest 未记录上一版")
    return f"{where}  {span}  整份重读"


def dumps(patch: dict) -> str:
    """Serialise deterministically: sorted keys, indented, one file."""
    return json.dumps(patch, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def write(patch: dict, path: Path | str) -> Path:
    """Write the patch atomically, to a path the caller names and this process checks.

    Absolute, because a proposal whose location depends on the directory the
    caller happened to stand in is a proposal nobody else can find; and atomic,
    because a reader must never see half a patch and decide on it.
    """
    target = Path(path)
    if not target.is_absolute():
        raise Refused(
            f"{target} is not absolute; a patch is a proposal a person acts on, "
            f"and one written relative to this process's working directory is "
            f"not findable by the person it is for")
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = dumps(patch)
    handle, temporary = tempfile.mkstemp(dir=str(target.parent), prefix=".patch-")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except BaseException:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise
    return target


def verify(report: dict, decisions: dict[tuple, dict] | None, decisions_path: str | None,
           path: Path | str) -> list[str]:
    """The reasons the patch on disk is NOT this projection of the records.

    Empty is the only acceptable answer for a derived proposal file: an item
    added by hand, a superseded item that stayed, or an item whose version pair
    changed all surface here by name.
    """
    expected = build(report, decisions, decisions_path)
    try:
        actual = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"cannot read {path}: {exc}"]
    if not isinstance(actual, dict):
        return [f"{path}: the patch must be an object"]
    if actual.get("schema") != SCHEMA:
        return [f"{path}: schema is {actual.get('schema')!r}, not {SCHEMA!r}"]
    reasons: list[str] = []
    for field in ("cards_read", "states", "no_item", "awaiting", "confirmed",
                  "rejected", "stale_decisions", "skipped", "decisions_path",
                  "root"):
        if actual.get(field) != expected[field]:
            reasons.append(f"`{field}` differs: on disk {actual.get(field)!r}, "
                           f"from the records {expected[field]!r}")
    expected_items = {item_key(item): item for item in expected["items"]}
    try:
        actual_items = {item_key(item): item for item in actual.get("items") or []}
    except (TypeError, KeyError) as exc:
        return reasons + [f"{path}: an item is not shaped like an item ({exc})"]
    for key in sorted(set(actual_items) - set(expected_items), key=repr):
        reasons.append(f"the patch holds a proposal no record derives: "
                       f"{key[0]} {key[2]} {key[3]}–{key[4]}")
    for key in sorted(set(expected_items) - set(actual_items), key=repr):
        reasons.append(f"the patch is missing a proposal the records derive: "
                       f"{key[0]} {key[2]} {key[3]}–{key[4]}")
    for key in sorted(set(expected_items) & set(actual_items), key=repr):
        if expected_items[key] != actual_items[key]:
            differing = sorted(
                field for field in set(expected_items[key]) | set(actual_items[key])
                if expected_items[key].get(field) != actual_items[key].get(field))
            reasons.append(f"{key[0]} {key[2]} {key[3]}–{key[4]}: "
                           f"{', '.join(differing)} differ from the records")
    return reasons


# No `main()` and no `if __name__ == "__main__":` — not by omission. The verb
# is `serverosctl work understanding-patch`, and a second argparse surface over
# the same operation is a second set of flags to drift. Every `intake/` module
# the CLI loads holds this absence, and `tests/test_capture_coverage.py` is
# where the rule was first written down.
