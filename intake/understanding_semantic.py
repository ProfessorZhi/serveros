#!/usr/bin/env python3
"""B2's semantic half: a reading becomes committed understanding, or a question.

`intake/understanding_delta.py` says which ranges a card is behind on.
`intake/understanding_patch.py` turns that into a proposal a person confirms.
Neither one opens a message body, so neither one can say what the new material
MEANS. This module is the layer that does, and it is built so that the one thing
it cannot do is pretend.

    THE READING IS AUTHORED INPUT. THIS MODULE DOES NOT INTERPRET ANYTHING.

A *reading* is what a native Agent produced after being handed a fixed-version
material: a list of claims about what changed. It arrives as a file. This module
validates it against the source it names, decides which claims may be committed
without a person, and writes those into the cards they belong to. Every claim
that a person has to decide stays a candidate.

Five rules, enforced rather than recommended.

  1. **A reading is bound to the revision it read, both halves.** It carries the
     source's `content_identity` and the card's `card_digest` — the same two
     digests `understanding_patch` keys its confirmations on. If the source has
     moved or the card has been rewritten since, the reading is refused rather
     than applied: an interpretation of text that is no longer there is not an
     interpretation of what is there now, and the failure is silent if the
     binding is dropped. This is the same defect F04 names one layer down.

  2. **A model cannot confirm its own output.** `user_confirmed` is not a value a
     reading may carry. A reading that writes it is refused. Human acceptance
     arrives through its own authored file, exactly as it does for the patch, so
     there is no code path from a model's text to a person's decision.

  3. **Machine facts commit inside a delegation, and nothing else does.**
     `agent_reconciled` is available only to an observation or a supported
     inference, only when a standing delegation covers the claim's subject, and
     only when the claim does not touch a goal, a responsible person, a
     deadline, an external commitment or a permission. Those five are what a
     person decides even when every fact is already in. Without a delegation
     everything awaits a decision — the honest default, and the one the contract
     asks for: "不能把'防止模型自批'实现成'所有事实都人工签字'" is balanced by
     the converse, that a machine does not acquire the right to sign.

  4. **Support must be inside what was read.** A claim cites positions in the
     source. Those positions have to fall inside the range the reading declares
     it read. A claim about position 900 from a reading of 700–720 is refused,
     because the citation cannot be checked by the person it is handed to — and
     an uncheckable citation is how a plausible sentence becomes a fact.

  5. **Replay writes once.** A commit's identity is the proposal it is about, and
     its receipt records the reading that produced it. Applying the same
     proposal twice returns the first receipt instead of writing again. The same
     proposal with a DIFFERENT reading is refused rather than half-applied: two
     readings of one span cannot both be the committed one, and choosing between
     them is a decision this module does not have the standing to make.

What is NOT here
----------------
No clock, no network, no model client. What the module needs from the world it
takes as arguments. That is what makes the writer testable, and it is also the
honest boundary: this file is the mechanism, and a real native Agent session is
what fills the reading in.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

SCHEMA = "serveros/understanding-semantic.v1"
READING_SCHEMA = "serveros/understanding-reading.v1"
RECEIPT_SCHEMA = "serveros/understanding-receipt.v1"

#: Where the understanding layer sits under the records root. The same two
#: segments `understanding_delta` walks, and repeated here rather than imported
#: because the two modules are loaded independently and neither is the other's
#: dependency. `test_the_two_layers_agree_on_where_the_cards_live` pins them
#: together, which is what makes the repetition safe: the failure mode of a
#: drifted constant is a writer that writes into a directory no reader opens.
UNDERSTANDING_SEGMENTS = ("information", "understanding")


def understanding_root(records_root: Path | str) -> Path:
    """`<records_root>/information/understanding` — the tree the cards live in."""
    return Path(records_root).joinpath(*UNDERSTANDING_SEGMENTS)

#: What a claim can be. These are the contract's own words
#: (`design/STRUCTURED-UNDERSTANDING-ABILITY.md` §3): an observation, an
#: inference the source supports, a proposal, an accepted decision, a conflict
#: between sources, a correction of an earlier claim, a retraction of one.
OBSERVED_FACT = "OBSERVED_FACT"
SUPPORTED_INFERENCE = "SUPPORTED_INFERENCE"
PROPOSAL = "PROPOSAL"
ACCEPTED_DECISION = "ACCEPTED_DECISION"
CONFLICT = "CONFLICT"
CORRECTION = "CORRECTION"
RETRACTION = "RETRACTION"
KINDS = (OBSERVED_FACT, SUPPORTED_INFERENCE, PROPOSAL, ACCEPTED_DECISION,
         CONFLICT, CORRECTION, RETRACTION)

#: The kinds a delegation can carry. An observation and an inference the source
#: supports are facts ABOUT the source; a proposal, a decision and a conflict are
#: about what to do, and what to do is not a machine's to accept.
DELEGABLE_KINDS = (OBSERVED_FACT, SUPPORTED_INFERENCE)

#: The confirmation dimensions, kept apart because they are different facts
#: rather than one enum: a claim can be `agent_reconciled` today and become
#: `user_confirmed` tomorrow, and a `user_confirmed` claim can go `stale`. The
#: contract says so in §2 and the reader here must not collapse them.
AGENT_RECONCILED = "agent_reconciled"
AWAITING_DECISION = "awaiting_decision"
USER_CONFIRMED = "user_confirmed"
CONFIRMATIONS = (AGENT_RECONCILED, AWAITING_DECISION, USER_CONFIRMED)

#: What a machine may not decide, however certain the facts are. Named
#: individually rather than as one "important" flag, because the test that
#: matters is per-category: a claim about a deadline must await a person even
#: when it is an observation, and an observation about a token count must not.
MAJOR_CHANGES = ("goal", "responsible", "deadline", "external_commitment",
                 "permission")

#: The fence this module owns inside a card. Everything outside it is somebody
#: else's prose and is preserved byte for byte — the writer checks that, and a
#: write that would change a byte it does not own, inside the fence or outside
#: it, is refused.
FENCE_BEGIN = "<!-- serveros:semantic:begin -->"
FENCE_END = "<!-- serveros:semantic:end -->"
#: The region INSIDE the fence that holds this module's own lines. The fence
#: says "the module owns this area"; this pair says "these bytes are the
#: module's". They are not the same statement, and what separates them is a
#: person's correction: somebody who disputes a claim writes next to it, inside
#: the fence, because that is where the claim is. Without the inner markers the
#: only way to replace the module's lines is to replace everything between
#: `FENCE_BEGIN` and `FENCE_END`, and that is how a correction was destroyed.
CLAIMS_BEGIN = "<!-- serveros:semantic:claims -->"
CLAIMS_END = "<!-- serveros:semantic:claims:end -->"

#: A claim's id, as a reading writes it: short, stable, and safe to cite in a
#: later retraction. The pattern is enforced because an id is what a supersede,
#: a conflict and a retraction all name, and two shapes of id would make those
#: references unjoinable.
CLAIM_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class Refused(RuntimeError):
    """This module will not answer, or will not write.

    Raised for a defect in the CALLER — a reading whose binding no longer holds,
    a claim citing what was not read, a commit that would overwrite somebody's
    edit — and for an internal inconsistency it will not paper over.
    """


class Malformed(Refused):
    """A fact about the FILE that was read. A claim with no assertion."""


def _require(value, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise Malformed(f"{label} must be a non-empty string")
    return value.strip()


def no_unsourced(mapping: dict, table: dict, where: str) -> dict:
    """Refuse to emit a key whose value the caller cannot name a source for.

    The same guard the reader and the patch carry. Applied where the record is
    EMITTED, so a field cannot be added to one branch and be sourced in none —
    and, in this module specifically, so that no field can appear whose value
    would have to have come from a model's opinion rather than from a stated
    input.
    """
    unexpected = sorted(set(mapping) - set(table))
    if unexpected:
        raise Refused(
            f"this module would emit "
            f"{', '.join(f'`{where}.{key}`' for key in unexpected)}, which the "
            f"producer table does not name a source for; add the producer or "
            f"drop the field")
    return mapping


# --------------------------------------------------------------------------
# the reading: what the Agent produced, and what it must carry
# --------------------------------------------------------------------------

READING_PRODUCED = {
    "schema": "the reading's own schema string",
    "source": "the material this reading is about: the card, the capture object "
              "and the range the Agent was given. The card is records-root "
              "relative (`information/understanding/people/alice.md`) and the "
              "capture object is raw-root relative "
              "(`feishu/chats/people/x/oc_x/manifest.json`) — two roots in one "
              "object, which is what the reader already emits and therefore "
              "what a reading must copy rather than reconstruct. Guessing a "
              "prefix on either one produces a reading that matches no range",
    "read": "the two digests the Agent was handed with that material — the "
            "source's `content_identity` and the card's `card_digest`. Required, "
            "and required to be present: a reading that does not say which "
            "revision it read cannot be checked against what is there now, and "
            "the check is the only thing standing between a stale "
            "interpretation and a committed one",
    "claims": "the claims, each with the fields `CLAIM_PRODUCED` names",
}

CLAIM_PRODUCED = {
    "id": "the reading's own stable id for this claim, from the reading",
    "kind": "one of KINDS, from the reading",
    "subject": "what the claim is about, as the Agent wrote it, from the reading",
    "assertion": "`{was, now}` — the earlier state and the state this claim "
                 "asserts, from the reading. `was` may be null for a claim "
                 "about something not previously recorded; `now` may not, "
                 "because a claim with no content is a heading",
    "support": "`{positions: [...], excerpt: ...}` — where in the source this "
               "claim is supported. The positions are checked against the range "
               "the reading declares it read; the excerpt is a quotation and is "
               "carried as one",
    "applies_to": "the business or time scope this claim is true within, from "
                  "the reading, or null when the Agent did not limit it",
    "touches": "which of MAJOR_CHANGES this claim changes, from the reading. "
               "Empty is a claim about the world; non-empty is a claim a person "
               "decides",
    "supersedes": "claim ids from earlier readings this claim replaces, from the "
                  "reading",
    "conflicts": "claim ids this claim cannot both hold with, from the reading",
    "card": "the understanding card that OWNS this claim, relative to the "
            "understanding root — the one card a commit writes the claim into",
    "views": "the cards this claim is a reference from — people/project/topic "
             "views. References and not owners: the contract's §5 says the claim "
             "has one owner and the views are derived",
}


def read_reading(path: Path | str) -> dict:
    """One reading file, validated as far as its own shape allows.

    Everything that can be checked without the source is checked here, at the
    moment of reading rather than at the moment of use: a claim with no
    assertion is a fact about the file, and it should be reported where the file
    was read rather than surfacing later as a write that did nothing.
    """
    target = Path(path)
    if not target.is_file():
        raise Malformed(f"{target} is not a file")
    try:
        document = json.loads(target.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise Malformed(f"{target}: not JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise Malformed(f"{target}: a reading is an object")
    if document.get("schema") != READING_SCHEMA:
        raise Malformed(
            f"{target}: schema is {document.get('schema')!r}, not "
            f"{READING_SCHEMA!r}. A reading this module does not recognise is "
            f"not one it can validate, and validating it anyway would be the "
            f"kind of near-miss the schema string exists to prevent")
    for field in ("source", "read", "claims"):
        if field not in document:
            raise Malformed(
                f"{target}: `{field}` is absent. A reading carries the material "
                f"it is about, the two digests it was computed against, and its "
                f"claims; a file missing any of the three cannot be checked")
    source = document["source"]
    read = document["read"]
    if not isinstance(source, dict) or not isinstance(read, dict):
        raise Malformed(f"{target}: `source` and `read` are objects")
    for field in ("card", "content_identity", "card_digest"):
        if field not in read:
            raise Malformed(
                f"{target}: `read.{field}` is absent, so this reading does not "
                f"say which revision it read. Copy it from the proposal the "
                f"Agent was handed; a reading that names only a place can be "
                f"committed against text nobody read")
    claims = document["claims"]
    if not isinstance(claims, list):
        raise Malformed(f"{target}: `claims` is a list")
    seen: set[str] = set()
    for number, claim in enumerate(claims, start=1):
        _validate_claim(claim, target, number)
        if claim["id"] in seen:
            raise Malformed(
                f"{target}: claim {number}: id {claim['id']!r} appears twice in "
                f"one reading. A supersede or a retraction names an id, and two "
                f"claims under one id make that name ambiguous")
        seen.add(claim["id"])
    return document


def _validate_claim(claim, target: Path, number: int) -> None:
    if not isinstance(claim, dict):
        raise Malformed(f"{target}: claim {number} is not an object")
    missing = [field for field in CLAIM_PRODUCED if field not in claim]
    if missing:
        raise Malformed(
            f"{target}: claim {number}: {', '.join(f'`{f}`' for f in missing)} "
            f"is absent. Every field `CLAIM_PRODUCED` names is required, "
            f"including the empty ones: a field that appears only when it "
            f"applies makes a reader's check a question about which shape it "
            f"is holding")
    if "confirmation" in claim:
        raise Malformed(
            f"{target}: claim {number}: a reading does not carry "
            f"`confirmation`, and it carried {claim['confirmation']!r}. Whether "
            f"a claim is machine-reconciled or awaits a person is decided here, "
            f"from the standing delegation and from what the claim touches. A "
            f"reading that states its own confirmation is asking this module to "
            f"ratify the answer it wrote, which is the one thing rule 2 exists "
            f"to prevent")
    unexpected = sorted(set(claim) - set(CLAIM_PRODUCED))
    if unexpected:
        raise Malformed(
            f"{target}: claim {number}: unexpected "
            f"{', '.join(f'`{f}`' for f in unexpected)}")
    if not CLAIM_ID.match(str(claim["id"])):
        raise Malformed(
            f"{target}: claim {number}: id {claim['id']!r} is not a citation "
            f"that a later retraction could name")
    if claim["kind"] not in KINDS:
        raise Malformed(
            f"{target}: claim {number}: kind {claim['kind']!r} is not one of "
            f"{', '.join(KINDS)}")
    assertion = claim["assertion"]
    if not isinstance(assertion, dict) or set(assertion) != {"was", "now"}:
        raise Malformed(
            f"{target}: claim {number}: `assertion` is exactly `was` and `now`")
    if not isinstance(assertion["now"], str) or not assertion["now"].strip():
        raise Malformed(
            f"{target}: claim {number}: `assertion.now` is empty; a claim with "
            f"nothing asserted is a heading, and committing one would put a "
            f"heading in a card and count it as understanding")
    support = claim["support"]
    if not isinstance(support, dict) or set(support) != {"positions", "excerpt"}:
        raise Malformed(
            f"{target}: claim {number}: `support` is exactly `positions` and "
            f"`excerpt`")
    if not isinstance(support["positions"], list):
        raise Malformed(f"{target}: claim {number}: `support.positions` is a list")
    for position in support["positions"]:
        if not isinstance(position, int) or isinstance(position, bool):
            raise Malformed(
                f"{target}: claim {number}: support position {position!r} is not "
                f"an integer position")
    touches = claim["touches"]
    if not isinstance(touches, list):
        raise Malformed(f"{target}: claim {number}: `touches` is a list")
    for touch in touches:
        if touch not in MAJOR_CHANGES:
            raise Malformed(
                f"{target}: claim {number}: `touches` names {touch!r}, which is "
                f"not one of {', '.join(MAJOR_CHANGES)}")
    if claim["kind"] == RETRACTION and not claim["supersedes"]:
        raise Malformed(
            f"{target}: claim {number}: a RETRACTION names what it retracts in "
            f"`supersedes`. A retraction of nothing is not a retraction")
    for field in ("supersedes", "conflicts", "views"):
        if not isinstance(claim[field], list):
            raise Malformed(f"{target}: claim {number}: `{field}` is a list")
    for field in ("subject", "card"):
        _require(claim[field], f"{target}: claim {number}: {field}")


# --------------------------------------------------------------------------
# the delegation: what a machine may commit without asking
# --------------------------------------------------------------------------

def resolve(reading: dict, current: dict, delegation: dict | None) -> dict:
    """Decide, per claim, whether it commits now or waits for a person.

    `current` is the truth about the material right now — `{card_digest,
    content_identity, from_position, to_position}` — measured by the caller from
    the same tree the reading names. `delegation` is the standing grant, if the
    caller has one: `{covers: [subject prefixes], until: <as written>}`.

    The order of the checks is the order of the consequences. A reading bound to
    a revision that has moved is refused before any claim is looked at, because
    every claim in it is about text that is no longer there.
    """
    binding = _check_binding(reading, current)
    if binding is not None:
        raise Refused(binding)

    read = reading["read"]
    low = current.get("from_position")
    high = current.get("to_position")
    if low is None or high is None:
        raise Refused(
            f"the range this reading was about was not measured: current has "
            f"from_position={low!r}, to_position={high!r}. Without it no "
            f"citation can be checked, and an unchecked citation is a claim "
            f"nobody can refuse")
    delegable = _delegation_covers(delegation)

    committed: list[dict] = []
    awaiting: list[dict] = []
    retracted: list[str] = []
    for claim in reading["claims"]:
        outside = [position for position in claim["support"]["positions"]
                   if not low <= position <= high]
        if outside:
            raise Refused(
                f"claim {claim['id']} cites {', '.join(str(p) for p in outside)}, "
                f"which is outside the range this reading declares it read "
                f"({low}–{high}). A citation nobody can check is how a "
                f"plausible sentence becomes a fact; read that range first, or "
                f"make the claim without it")
        if claim["kind"] == RETRACTION:
            # A retraction is not committed anywhere: it REMOVES. Its effect is
            # immediate and local, and it is recorded by naming what it retracts
            # so the eligibility filter below can act on it before any remote
            # store is told anything.
            retracted.extend(claim["supersedes"])
            awaiting.append({**claim, "confirmation": AWAITING_DECISION,
                             "confirmation_why":
                                 "a retraction takes effect locally at once; "
                                 "committing it into a card is a separate, "
                                 "visible act a person performs"})
            continue
        confirmation, why = _confirmation_for(claim, delegation, delegable)
        record = {**claim, "confirmation": confirmation,
                  "confirmation_why": why}
        if confirmation == AGENT_RECONCILED:
            committed.append(record)
        else:
            awaiting.append(record)

    return no_unsourced({
        "schema": SCHEMA,
        "reading": reading["source"],
        "read": read,
        "committed": committed,
        "awaiting": awaiting,
        "retracted": sorted(set(retracted)),
        "delegation": _delegation_as_written(delegation),
    }, RESOLUTION_PRODUCED, "resolution")


def _check_binding(reading: dict, current: dict) -> str | None:
    """Why this reading no longer applies, naming which half moved.

    One sentence per case, because the two cases have different remedies: a
    source that moved means read further and read again; a card that was
    rewritten means the baseline it was reasoned against is gone and the
    reasoning has to be redone against the new text.
    """
    read = reading["read"]
    moved = []
    if current.get("content_identity") != read["content_identity"]:
        moved.append("the content this reading is about changed")
    if current.get("card_digest") != read["card_digest"]:
        moved.append("the understanding card was rewritten")
    if not moved:
        return None
    return ("this reading was made against a revision that is no longer the one "
            "on disk: " + " and ".join(moved) + ". Committing it would record "
            "an interpretation of text nobody can now read. Read the current "
            "revision and produce a new reading; the older one is not lost, and "
            "the difference between them is the only evidence that anything "
            "changed")


def _delegation_covers(delegation: dict | None) -> bool:
    """Whether there is a grant at all. Its SCOPE is checked per claim."""
    if not isinstance(delegation, dict):
        return False
    return bool(delegation.get("covers"))


def _delegation_as_written(delegation: dict | None) -> dict | None:
    """The grant copied, never summarised: what it covers is what it says."""
    if not isinstance(delegation, dict):
        return None
    return {key: delegation.get(key)
            for key in ("covers", "until", "granted_by", "source")}


def _confirmation_for(claim: dict, delegation: dict | None,
                      delegable: bool) -> tuple[str, str]:
    """`(confirmation, why)` for one claim. The why is the point of the pair."""
    if claim["touches"]:
        return AWAITING_DECISION, (
            f"this claim changes {', '.join(claim['touches'])}, which is a "
            f"person's to decide however certain the facts are")
    if claim["kind"] not in DELEGABLE_KINDS:
        return AWAITING_DECISION, (
            f"a {claim['kind']} is about what to do, and what to do is not a "
            f"machine's to accept; machine facts commit inside a delegation and "
            f"nothing else does")
    if not delegable:
        return AWAITING_DECISION, (
            "no standing delegation covers this work, so nothing commits on its "
            "own authority — the claims are prepared and a person accepts them")
    covers = (delegation or {}).get("covers") or []
    if not any(str(claim["subject"]).startswith(str(prefix)) for prefix in covers):
        return AWAITING_DECISION, (
            f"the standing delegation covers "
            f"{', '.join(repr(str(c)) for c in covers)}, which does not include "
            f"{claim['subject']!r}")
    return AGENT_RECONCILED, (
        f"a {claim['kind']} inside the standing delegation covering "
        f"{claim['subject']!r}, touching none of {', '.join(MAJOR_CHANGES)}")


RESOLUTION_PRODUCED = {
    "schema": "this module's own schema string",
    "reading": "the material the reading was about, copied from the reading",
    "read": "the two digests the reading was bound to, copied from it",
    "committed": "the claims that commit now, each with the confirmation and why",
    "awaiting": "the claims a person decides, each with the confirmation and why",
    "retracted": "the claim ids a RETRACTION in this reading names. Local "
                 "eligibility acts on these before any remote store is told "
                 "anything, because a withdrawn fact must not stay recallable "
                 "while an update is in flight",
    "delegation": "the standing grant as it was written, or null when there is "
                  "none. Copied and never paraphrased: a grant restated in this "
                  "module's words would be a grant this module invented",
}


# --------------------------------------------------------------------------
# eligibility: what may still be recalled
# --------------------------------------------------------------------------

def eligible(claimed: dict[str, str], retractions: dict[str, str]) -> dict:
    """Split claim ids into what may still be recalled and what may not.

    `claimed` maps a claim id to the committed line it produced; `retractions`
    maps a retracted id to the id of the retraction that withdrew it. A
    retraction takes effect HERE, locally and immediately, and not when a remote
    memory provider acknowledges it — the contract's §6 says the remote can be
    down and the withdrawn fact still must not come back.
    """
    recallable = {claim: line for claim, line in claimed.items()
                  if claim not in retractions}
    withdrawn = {claim: {"line": claimed[claim], "retracted_by": retractions[claim]}
                 for claim in claimed if claim in retractions}
    unknown = sorted(set(retractions) - set(claimed))
    return {"schema": SCHEMA, "recallable": recallable, "withdrawn": withdrawn,
            # A retraction naming a claim this module never held is reported
            # rather than dropped: it means an earlier commit is missing, and
            # silence here would make a lost claim look like a claim that was
            # never made.
            "retract_nothing": unknown}


# --------------------------------------------------------------------------
# the commit: the proposal identity, and the write that happens once
# --------------------------------------------------------------------------

COMMIT_PRODUCED = {
    "schema": "this module's own schema string",
    "key": "the proposal this commit is about: the card, the capture object, the "
           "range and the two digests. The identity of the WRITE, and the reason "
           "a replay returns a receipt instead of writing again",
    "reading_digest": "the reading's own bytes as a digest. Recorded in the "
                      "receipt so that the same proposal arriving with a "
                      "DIFFERENT reading is refused rather than applied twice",
    "baseline": "the card the reading was made against and the digest it had "
                "then, copied from `reading.read`. Checked again at the moment "
                "of writing: the check in `resolve` and the check in `write` are "
                "the same question asked at two instants, and the gap between "
                "them is a person's editing session",
    "lines": "what each committed claim becomes in its card, `{card, text}` per "
             "claim. The card is a FIELD and not something read back out of the "
             "text: a line parsed for its owner would make the ownership of a "
             "claim depend on how it was rendered, and rendering changes",
    "views": "the view cards the committed claims are references from, with the "
             "reference line for each",
}

RECEIPT_PRODUCED = {
    "schema": "this module's own schema string",
    "key": "the proposal identity the receipt is for, copied from the commit",
    "reading_digest": "the reading that produced it, copied from the commit",
    "written": "the files this write actually changed, with the digest each one "
               "had before and after. Facts about the disk, read back after the "
               "write and not computed before it",
    "pending_views": "the views whose update did not complete, each with the "
                     "error and the reference lines it was to receive. Present "
                     "means the commit is PARTIAL: the claim is in its owning "
                     "card and some reference to it is missing. The contract's "
                     "§5 is explicit that this must not be reported as all "
                     "consumers aligned. The LINES are carried here because a "
                     "resume cannot re-derive them: by the time a view is "
                     "retried, the first write has moved the card the reading "
                     "was reasoned against, and re-running the reading against "
                     "the new baseline is a different reading",
    "state": "COMPLETE or PARTIAL, decided by `pending_views` and nothing else",
    "reused": "true when this receipt is an earlier write returned again. A "
              "replay that wrote a second time would be a duplicate commitment "
              "that no reader could tell from a second real change",
    "committed_at": "the time the caller supplied. Never this process's clock: a "
                    "receipt whose time came from the machine that wrote it "
                    "cannot be compared with one from another machine",
}


def commit_key(reading: dict) -> tuple:
    """The proposal identity a write is keyed on: the PLACE and the REVISION.

    The card's own digest is deliberately not in here, and the difference from
    `understanding_patch` is the point rather than an inconsistency. A
    confirmation is keyed on both digests because a confirmation is a person's
    "I have read this text"; the text moving invalidates it. A write is keyed on
    the source revision because the write is what moves the card: if the card's
    digest were part of the key, the very act of committing would make the next
    attempt a new proposal, and the same span read twice with two different
    results would be committed twice — two assertions in one fence, neither of
    which a reader could tell was the later one.

    With the key on the source revision, the second reading of one span finds
    the first receipt and is refused. The card baseline still guards the write,
    as a precondition rather than as an identity.
    """
    source = reading["source"]
    read = reading["read"]
    return (source.get("card"), source.get("manifest"),
            source.get("from_position"), source.get("to_position"),
            read.get("content_identity"))


def reading_digest(reading: dict) -> str:
    """The reading's own bytes, canonically — one-way, and no text leaves."""
    canonical = json.dumps(reading, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def card_digest(text: str) -> str:
    """One card's text as a digest — the same one the reader computes."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def commit(resolution: dict, reading: dict) -> dict:
    """The write this resolution implies, before anything is written."""
    lines = {claim["id"]: {"card": claim["card"], "text": _claim_line(claim)}
             for claim in resolution["committed"]}
    views: dict[str, list[str]] = {}
    for claim in resolution["committed"]:
        reference = _view_reference(claim)
        for view in claim["views"]:
            views.setdefault(view, [])
            if reference not in views[view]:
                views[view].append(reference)
    return no_unsourced({
        "schema": SCHEMA,
        "key": list(commit_key(reading)),
        "reading_digest": reading_digest(reading),
        "baseline": {"card": reading["source"].get("card"),
                     "card_digest": reading["read"].get("card_digest")},
        "lines": lines,
        "views": views,
    }, COMMIT_PRODUCED, "commit")


def _claim_line(claim: dict) -> str:
    """One committed claim as the line a person reads in the card.

    The kind, the assertion and the support are all on the line because a reader
    who has to open the source to find out what a sentence is based on will not
    open it. The excerpt is the Agent's quotation and is carried as a quotation.
    """
    was = claim["assertion"]["was"]
    change = (f"{was} → {claim['assertion']['now']}" if was
              else claim["assertion"]["now"])
    where = ", ".join(str(position)
                      for position in claim["support"]["positions"]) or "无位置"
    return (f"- **[{claim['kind']}]** {claim['subject']}：{change}"
            f"（{AGENT_RECONCILED}；来源 pos {where}；"
            f"excerpt: {claim['support']['excerpt']}）")


def _view_reference(claim: dict) -> str:
    owner = claim["card"]
    return f"- {claim['id']} → `{owner}`（{claim['subject']}）"


def write(commit_document: dict, records_root: Path | str, *,
          committed_at: str) -> dict:
    """Write the committed claims into their cards. Once, and never partially silent.

    `records_root` is the records checkout, the same root the reader and the
    patch name their paths against, so a card path in a reading means here what
    it means everywhere else. The write touches the fence this module owns and
    not one byte outside it, and it checks the owning card's digest against the
    reading's before it writes: a card somebody edited since the Agent read it is
    a card whose new text the Agent never saw, and merging an interpretation into
    prose a person just changed is how an agent's sentence ends up attributed to
    a human.
    """
    if not committed_at or not isinstance(committed_at, str):
        raise Refused(
            "committed_at must be supplied by the caller. A receipt stamped "
            "from this process's clock cannot be compared with one from another "
            "machine, and the comparison is what makes a replay detectable")
    base = _understanding_root(records_root)
    receipt_path = base / ".serveros" / f"receipt-{_key_slug(commit_document['key'])}.json"
    prior = _read_receipt(receipt_path)
    if prior is not None:
        if prior.get("reading_digest") != commit_document["reading_digest"]:
            raise Refused(
                f"this proposal already has a receipt for a different reading "
                f"({receipt_path}). Two readings of one span cannot both be the "
                f"committed one, and choosing between them is not this module's "
                f"decision to make: supersede the first one explicitly, or read "
                f"the source again under a new revision")
        if prior.get("state") == "COMPLETE":
            return {**prior, "reused": True}
        return _resume(prior, base, receipt_path)

    baseline = commit_document["baseline"]
    written = []
    # Group the lines by the card that owns them, in id order, so that two runs
    # over one commit produce the same file and the same receipt.
    by_card: dict[str, list[str]] = {}
    for _, entry in sorted(commit_document["lines"].items()):
        by_card.setdefault(entry["card"], []).append(entry["text"])
    # An owning card that cannot be written is NOT a partial commit: a claim
    # whose owner did not take it was never committed, and reporting it as a
    # missing reference would say the claim is in a card it is not in.
    #
    # The baseline check belongs here and only here. It is the question "is this
    # still the prose the reading was made against", which is a question about
    # the card the reading was ABOUT. A view is not interpreted, only referenced
    # from, so a view edited since the read is a view whose text this write still
    # does not touch.
    for card, texts in sorted(by_card.items()):
        written.append(_write_fence(records_root, base, card, texts, baseline))

    pending = []
    for view, references in sorted(commit_document["views"].items()):
        try:
            written.append(_write_fence(records_root, base, view,
                                        list(references)))
        except Refused as exc:
            pending.append({"view": view, "why": str(exc),
                            "lines": list(references)})

    receipt = no_unsourced({
        "schema": RECEIPT_SCHEMA,
        "key": commit_document["key"],
        "reading_digest": commit_document["reading_digest"],
        "written": written,
        "pending_views": pending,
        "state": "PARTIAL" if pending else "COMPLETE",
        "reused": False,
        "committed_at": committed_at,
    }, RECEIPT_PRODUCED, "receipt")
    _write_receipt(receipt_path, receipt)
    return receipt


def _resume(prior: dict, records_root: Path | str, receipt_path: Path) -> dict:
    """Retry the views a PARTIAL receipt left out, and only those.

    The owning cards are NOT rewritten: those writes already happened, and doing
    them again would rewrite a fence that may have been edited since — the
    module does not get a second opinion about text it has already committed.
    """
    base = _understanding_root(records_root)
    written = list(prior.get("written") or [])
    pending = []
    for entry in prior.get("pending_views") or []:
        try:
            written.append(_write_fence(records_root, base, entry["view"],
                                        list(entry["lines"])))
        except Refused as exc:
            pending.append({**entry, "why": str(exc)})
    receipt = no_unsourced({
        **{key: prior[key] for key in ("schema", "key", "reading_digest",
                                       "committed_at")},
        "written": written,
        "pending_views": pending,
        "state": "PARTIAL" if pending else "COMPLETE",
        "reused": True,
    }, RECEIPT_PRODUCED, "receipt")
    _write_receipt(receipt_path, receipt)
    return receipt


def receipt_for(reading: dict, records_root: Path | str) -> dict | None:
    """This proposal's receipt, if it already has one. Read, not derived."""
    return _read_receipt(_receipt_path(_understanding_root(records_root), reading))


def _receipt_path(base: Path, reading: dict) -> Path:
    return base / ".serveros" / f"receipt-{_key_slug(list(commit_key(reading)))}.json"


def _understanding_root(records_root: Path | str) -> Path:
    """The cards' own directory, refusing a root that is not absolute."""
    base = understanding_root(records_root)
    if not base.is_absolute():
        raise Refused(
            f"{base} is not absolute; a records root relative to whatever "
            f"directory the caller stood in is a root nobody else can find")
    return base


def apply(reading_path: Path | str, records_root: Path | str, *, measure,
          delegation: dict | None = None, committed_at: str) -> dict:
    """Read, resolve, commit and write — in the one order that is correct.

    The order is the whole reason this function exists rather than the caller
    assembling the four steps. A replay has to be recognised BEFORE the reading
    is re-validated against the card, because the thing that moved the card is
    the first commit. Checking the binding first turns every replay into "the
    card was rewritten" — which is true, and is not what happened — and the
    temptation that follows is to loosen the binding check. A receipt lookup
    costs one small file read and leaves the check where it belongs.

    `measure` is called only when there is no receipt, and that is why it is a
    callable and not a dict. Taking the measurement eagerly would make every
    replay pay for a comparison whose answer is already known, and — worse —
    would make the replay DEPEND on that measurement still being possible. A
    range the reader no longer proposes must not stop a write that already
    happened from being reported.
    """
    base = _understanding_root(records_root)
    document = read_reading(reading_path)
    prior = _read_receipt(_receipt_path(base, document))
    if prior is not None:
        return _replay(prior, document, records_root)
    result = resolve(document, measure(), delegation)
    plan = commit(result, document)
    return write(plan, records_root, committed_at=committed_at)


def _replay(prior: dict, reading: dict, records_root: Path | str) -> dict:
    """An earlier write for this proposal, returned or finished — never redone."""
    if prior.get("reading_digest") != reading_digest(reading):
        raise Refused(
            f"this proposal already has a receipt for a different reading. Two "
            f"readings of one span cannot both be the committed one, and "
            f"choosing between them is not this module's decision to make: "
            f"supersede the first one explicitly, or read the source again under "
            f"a new revision")
    if prior.get("state") == "COMPLETE":
        return {**prior, "reused": True}
    base = _understanding_root(records_root)
    return _resume(prior, records_root, _receipt_path(base, reading))


def _write_fence(records_root: Path | str, base: Path, card: str,
                 lines: list[str], baseline: dict | None = None) -> dict:
    """Replace this module's fence in one card, and nothing else in it."""
    target = Path(records_root) / card
    # `card` is records-root-relative, the coordinate system the reader and the
    # patch use, so joining it onto the records root is the only join that does
    # not double a prefix. The guard below is what stops that join from being
    # used to write a claim into the raw layer: everything this module writes
    # goes into the understanding layer, and a card path that points anywhere
    # else is a reading that has asked it to edit a source.
    if not target.is_relative_to(base):
        raise Refused(
            f"{target} is not under {base}: this module writes understanding "
            f"cards and nothing else. A claim about a source is not written "
            f"into the source")
    if not target.is_file():
        raise Refused(
            f"{target} does not exist. A claim is committed into a card that "
            f"already holds the understanding it extends; creating the card here "
            f"would make this module an author of the baseline it was supposed "
            f"to extend")
    before = target.read_text(encoding="utf-8")
    if (baseline and card == baseline.get("card")
            and card_digest(before) != baseline.get("card_digest")):
        raise Refused(
            f"{target} has changed since this reading was made: the digest the "
            f"reading was reasoned against is not the digest on disk. The "
            f"reading is about text that is no longer there. Read the card "
            f"again — the difference between the two versions is the only "
            f"evidence that anything happened to it")
    head_before, body_before, tail_before = fence_parts(before)
    body = _replace_fence(before, lines)
    # Checked BEFORE the write, so that a refusal is a refusal and the file is
    # still the one a person left. The check is on the splice this module is
    # about to perform: the head bytes survive, the tail bytes survive, the
    # only thing added to the head is blank lines before the fence, the bytes
    # inside the fence that are NOT this module's lines are unchanged, and the
    # lines this module does own are the ones it just decided on. The two are
    # asked separately because they are two different promises — the second is
    # what keeps a person's correction, and a single comparison against the
    # whole body would satisfy the second while silently breaking the first.
    head_after, body_after, tail_after = fence_parts(body)
    region_after = claims_region(body_after)
    if (not head_after.startswith(head_before)
            or head_after[len(head_before):].strip("\n")
            or tail_after != tail_before
            or region_after is None
            or _fence_layout(body_after) != _fence_layout(body_before)
            or region_after[1] != _claims_lines(lines)):
        raise Refused(
            f"{target}: this write would change text this module does not own "
            f"— inside its fence or outside it — so it did not happen and the "
            f"file is as it was. Somebody's prose is not this module's to "
            f"rewrite")
    _atomic_write(target, body)
    after = target.read_text(encoding="utf-8")
    if after != body:
        raise Refused(
            f"{target}: the card on disk is not what this module wrote, so this "
            f"write is not reported as having happened")
    return {"card": card, "before": card_digest(before), "after": card_digest(after),
            "claims": len(lines)}


def fence_parts(text: str) -> tuple[str, str, str]:
    """A card as `(head, fence, tail)`: what this module did not write around it.

    With no fence the whole text is the head and the other two are empty. That
    asymmetry is the point: a card nobody has written to has no fence, and
    treating its whole body as a fence to be replaced is how a first write
    becomes a rewrite.
    """
    if FENCE_BEGIN not in text or FENCE_END not in text:
        return text, "", ""
    head, rest = text.split(FENCE_BEGIN, 1)
    body, tail = rest.split(FENCE_END, 1)
    return head, body, tail


def claims_region(body: str) -> tuple[str, str, str] | None:
    """A fence body as `(before, claims, after)`, or None when it carries none.

    None is not "empty". It says this body was written before the module marked
    its own lines — and back then the writer replaced the whole body, so every
    line in it is one the module put there and none of it is a person's. That is
    what makes the migration below exact instead of a guess about which lines
    were somebody else's. The sentinel is the whole tuple rather than a `None`
    inside it, because a region whose `before` is empty is a region, and a
    caller that tested the wrong slot would read "unmarked" where the answer was
    "marked, with nothing before it".
    """
    if CLAIMS_BEGIN not in body or CLAIMS_END not in body:
        return None
    before, rest = body.split(CLAIMS_BEGIN, 1)
    claims, after = rest.split(CLAIMS_END, 1)
    return before, claims, after


def _claims_lines(lines: list[str]) -> str:
    """The bytes between the claims markers: this module's lines, newly lined."""
    return "\n" + "\n".join(lines) + "\n"


def _claims_marked(lines: list[str]) -> str:
    """This module's own lines, between the markers that say they are its own."""
    return CLAIMS_BEGIN + _claims_lines(lines) + CLAIMS_END


def _fence_body(lines: list[str]) -> str:
    """The fence body when this module is the only writer of the card."""
    return "\n" + _claims_marked(lines) + "\n"


def _fence_layout(body: str) -> tuple[str, str]:
    """What a fence body holds AROUND this module's lines.

    An unmarked body is reported as the canonical layout rather than as itself:
    every line in it is the module's, so there is nothing in it that is
    somebody else's to keep, and what the next write lays down around its own
    lines is the canonical shape. Reporting the body itself would make the
    migration off an unmarked fence read as a change outside the fence.
    """
    region = claims_region(body)
    if region is None:
        return "\n", "\n"
    before, _, after = region
    return before, after


def _replace_fence(text: str, lines: list[str]) -> str:
    """The card with this module's own lines replaced, and everything else kept.

    Three cases, and the difference between them is the point:

      * no fence — appended. A card nobody has written to has no fence, and
        there the module's lines go after the prose rather than in place of it,
        because the prose is a person's;
      * a fence with no claims region — a card written before the region
        existed. Its body had been replaced wholesale on every commit, so all
        of it is the module's and replacing all of it again keeps faith with
        what was there;
      * a fence with the region — only the region is replaced. Every other byte
        inside the fence, and everything outside it, is what it was before.

    A card holding one marker of a pair and not its counterpart is REFUSED, not
    repaired — both pairs, for the same reason and with different consequences.
    A half FENCE leaves the stale half in the card and puts a second copy of the
    lines beside it, and `fence_parts` calls such a card all head: the markers
    are the guard that says a person's prose is not this module's, so a card
    with one of them has no guard at all. A half CLAIMS region is read as no
    region, which is the migration case — and that migration replaces the whole
    body, because the writer that produced an unmarked body owned all of it.
    Neither half is a shape this module ever wrote, so in either the module
    cannot say which lines are its own, and the second would take a person's
    lines with them.
    """
    has_begin, has_end = FENCE_BEGIN in text, FENCE_END in text
    if has_begin != has_end:
        which = "begin" if has_begin else "end"
        raise Refused(
            f"this card holds the fence's {which} marker and not its "
            f"counterpart, so this module cannot tell its own region from the "
            f"prose around it. Writing would leave the old lines in the card "
            f"beside the new ones and put them outside any guard. A person has "
            f"to look at this card")
    if not has_begin:
        separator = ("" if text.endswith("\n\n")
                     else ("\n" if text.endswith("\n") else "\n\n"))
        return text + separator + FENCE_BEGIN + _fence_body(lines) + FENCE_END
    head, body, tail = fence_parts(text)
    if claims_region(body) is None and (CLAIMS_BEGIN in body
                                        or CLAIMS_END in body):
        which = "begin" if CLAIMS_BEGIN in body else "end"
        raise Refused(
            f"this card holds the claims region's {which} marker and not its "
            f"counterpart, so the lines this module wrote cannot be told from "
            f"the ones a person added next to them. An unmarked fence is "
            f"migrated by replacing its whole body, which is right only because "
            f"nothing else could have written there — and this card says "
            f"somebody did. A person has to look at this card")
    before, after = _fence_layout(body)
    return (head + FENCE_BEGIN + before + _claims_marked(lines) + after
            + FENCE_END + tail)


def _read_receipt(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        # An unreadable receipt is not "no receipt": writing again on top of a
        # commit whose record cannot be read is how a duplicate gets made. The
        # caller is told, and the fix is a person looking at the file.
        raise Refused(
            f"{path} exists and is not readable JSON. This proposal may already "
            f"have been committed and its record damaged; writing again would "
            f"duplicate it. Inspect the file before retrying") from None
    return document if isinstance(document, dict) else None


def _write_receipt(path: Path, receipt: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(path, json.dumps(receipt, ensure_ascii=False, indent=2,
                                   sort_keys=True) + "\n")


def _key_slug(key: list) -> str:
    canonical = json.dumps(key, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def render_resolution(resolution: dict) -> list[str]:
    """A resolution as lines, in the order a person reads them.

    The claims a person must decide come with their `confirmation_why` attached
    and not as a bare list of headings: "why is this one asking me" is the
    question, and a rendering that dropped the reason would make every
    awaiting claim look like the same unanswered thing.
    """
    source = resolution["reading"]
    read = resolution["read"]
    lines = [
        f"proposal    {source.get('card')}  "
        f"pos {source.get('from_position')}–{source.get('to_position')}",
        f"read        card {_short(read.get('card_digest'))} · "
        f"content {_short(read.get('content_identity'))}",
        f"delegation  " + (_delegation_line(resolution["delegation"])),
        f"commit      {len(resolution['committed'])} 条",
    ]
    for claim in resolution["committed"]:
        lines.append(f"  + [{claim['kind']}] {claim['subject']}："
                     f"{claim['assertion']['now']}")
        lines.append(f"    {claim['confirmation_why']}")
    lines.append(f"awaiting    {len(resolution['awaiting'])} 条")
    for claim in resolution["awaiting"]:
        lines.append(f"  ? [{claim['kind']}] {claim['subject']}："
                     f"{claim['assertion']['now']}")
        lines.append(f"    {claim['confirmation_why']}")
    if resolution["retracted"]:
        lines.append(f"retracted   {len(resolution['retracted'])} 条 —— "
                     f"本地即刻不再可召回，远端失效另行确认")
        for claim_id in resolution["retracted"]:
            lines.append(f"  - {claim_id}")
    return lines


def render_receipt(receipt: dict) -> list[str]:
    """A receipt as lines. PARTIAL is the line that matters, so it carries why."""
    lines = [
        f"state       {receipt['state']}"
        + ("  —— 主张已在归属卡片，引用未补齐" if receipt["state"] == "PARTIAL" else ""),
        f"proposal    {_short(receipt['key'][0])}  "
        f"pos {receipt['key'][2]}–{receipt['key'][3]}  "
        f"content {_short(receipt['key'][4])}",
        f"reading     {_short(receipt['reading_digest'])}",
        f"written     {len(receipt['written'])} 个文件",
    ]
    for entry in receipt["written"]:
        lines.append(f"  · {entry['card']}  {_short(entry['before'])} → "
                     f"{_short(entry['after'])}  {entry['claims']} 条")
    for entry in receipt["pending_views"]:
        lines.append(f"  ! {entry['view']}  {entry['why']}")
    lines.append(f"committed   {receipt['committed_at']}"
                 + ("  （重放：未再次写入）" if receipt["reused"] else ""))
    return lines


def _delegation_line(delegation: dict | None) -> str:
    if not delegation:
        return "无 —— 没有常设委托时机器不自行提交任何一条"
    covers = delegation.get("covers") or []
    return (f"covers {', '.join(repr(str(c)) for c in covers)}"
            + (f"  until {delegation['until']}" if delegation.get("until")
               else "  until 未声明")
            + f"  来源 {delegation.get('source')}")


def _short(digest) -> str:
    """A digest for a person to compare by eye. Never the whole thing."""
    return (str(digest)[:12] + "…") if digest else "无"


def _atomic_write(target: Path, body: str) -> None:
    handle, temporary = tempfile.mkstemp(dir=str(target.parent), prefix=".semantic-")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except BaseException:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise
