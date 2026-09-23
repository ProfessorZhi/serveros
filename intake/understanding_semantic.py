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
#: Bumped when the receipt's SHAPE changed, and bumped for that reason alone.
#: Three times, so far, and the second bump is the interesting one:
#:
#:   * v1 → v2: `written[i].claims` used to hold a count and now holds the ids.
#:     A v1 receipt handed to a reader that expects ids would report a count as
#:     if it were a list.
#:   * v2 → v3: `written[i].claims` used to hold THE IDS THIS COMMIT WROTE and
#:     now holds THE IDS THE FILE CARRIES, and each entry gained `removed`. The
#:     change is not cosmetic: a card accumulates the claims committed into it,
#:     so a v2 entry for a card with two commits names one of them, and a reader
#:     that trusts it would report a claim that is in the card as absent from it.
#:     A fact about a file that has to be read as "everything the file holds" is
#:     a different field from one read as "everything this run put there".
#:   * v3 → v4: each `written` entry gained `role`, which says whether that card
#:     OWNS the claims or merely REFERENCES them. A claim is committed into one
#:     owning card and referenced from every view that mentions it, and both
#:     writes produce an entry of the same shape with the same `claims` list —
#:     so a v3 receipt read as "the cards holding this claim" reports one claim
#:     living in a person's card and in every project view at once. This was not
#:     a reading error anybody had to make: an A2 recall filter, reading v3 as
#:     written, reported exactly that.
#:   * v4 → v5: the receipt gained `baseline` and `retracted`, and a withdrawal
#:     became a thing a receipt can BE (state `RETRACTION_ONLY`). v4 could not
#:     record one: a retraction commits nowhere, so a reading that was only a
#:     retraction produced an empty commit, `write` left no receipt for it, and
#:     the local effect the contract gives a withdrawal — that it takes hold at
#:     once — had nothing durable to be read back from. A2's recall filter said
#:     so in its `unobserved` table rather than answering `CURRENT`, which was
#:     honest and was not a product. `baseline` came with it because on a
#:     `RETRACTION_ONLY` receipt it is the only record of the card the reader
#:     was looking at, and a withdrawal whose durable evidence names a claim and
#:     a source revision but not the text it was reasoned against is evidence
#:     nobody can re-examine.
RECEIPT_SCHEMA = "serveros/understanding-receipt.v5"

#: The receipt schemas this version can READ, newest first. v4 is on the list
#: and v1–v3 are not, and the difference is what each bump changed:
#:
#:   * `written[i].claims` gained its meaning in v2 and changed it in v3 (a
#:     count became the ids, then the ids this commit wrote became the ids the
#:     file carries). A reader that has only ever known v4's reading of that
#:     field cannot read a v1–v3 receipt without inventing one, so it refuses
#:     them by name — see `_read_receipt`.
#:   * v5 ADDED a field and changed none. Every field v4 has, v5 has with the
#:     same meaning, so a v4 receipt is read directly and its absent `retracted`
#:     reads as the empty list — which is not a guess about what it meant, it is
#:     what a v4 receipt says: v4 had no way to record a withdrawal that wrote
#:     no file, so it recorded none. An old receipt is read as what it is and is
#:     never rewritten into the newer shape; nothing here migrates a file on
#:     disk.
READABLE_RECEIPT_SCHEMAS = (RECEIPT_SCHEMA, "serveros/understanding-receipt.v4")

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

#: The two roles a card can have in a claim's life, recorded per write because
#: the write itself cannot tell them apart. A claim has exactly one OWNER — the
#: card it is committed into — and any number of VIEWS, which are derived and
#: carry a reference to it rather than the claim.
OWNER = "OWNER"
VIEW = "VIEW"

#: A line this module wrote, read back: the claim id it carries comes first,
#: alone, in backticks. ONE pattern for both lines the module writes — the claim
#: line in an owning card and the reference line in a view — because the id is
#: what the next commit merges on, and two spellings of "here is the id" would
#: let a card hold a line the merge cannot see.
#:
#: Read back from the LINE and from nowhere else, which is a real change of
#: position from the earlier version of this file. While the writer replaced its
#: whole block on every commit, a card held only the lines of the last commit and
#: the ids could be carried on the side; now a card accumulates, so the file is
#: the only record of what is in it, and the id has to be in the file. The
#: identity of the claim is still not a rendering: the id comes from the reading,
#: is validated there, and a line whose first token is not a claim id is refused
#: rather than guessed at.
WRITTEN_LINE = re.compile(r"^-\s+`([^`\s]+)`\s")


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
    # Checked here rather than where the ids are used, for the reason this
    # function exists: a supersede names a line that is already in a card, and an
    # id that could not be a claim id names a line no card can hold. Left to the
    # write it would remove nothing and read, from the receipt alone, exactly
    # like a supersede whose target was never committed.
    for superseded in claim["supersedes"]:
        if not isinstance(superseded, str) or not CLAIM_ID.match(superseded):
            raise Malformed(
                f"{target}: claim {number}: `supersedes` names {superseded!r}, "
                f"which is not a claim id. An id is what a line in a card "
                f"carries, so this names nothing a card could hold and would "
                f"remove nothing")
    for field in ("subject", "card"):
        _require(claim[field], f"{target}: claim {number}: {field}")


# --------------------------------------------------------------------------
# the acceptance: what a person said yes to, and to which revision of it
# --------------------------------------------------------------------------

#: The fields that say WHERE an acceptance is about, in order.
ACCEPTANCE_PLACES = ("card", "manifest", "from_position", "to_position")

#: The fields that say WHICH REVISION of that place, and what a person is told
#: when each of them moved. The sentence lives beside the field rather than in a
#: table keyed by name, so that a field cannot be added to the identity without
#: somebody having to decide what to say when it moves — the alternative is a
#: version field whose movement is reported as nothing, which reads exactly like
#: an acceptance that was never supplied.
ACCEPTANCE_VERSIONS = (
    ("card_digest",
     "the understanding card has been rewritten since it was accepted"),
    ("content_identity",
     "the content this reading is about has changed since it was accepted"),
    ("reading_digest",
     "the claims this reading produced are not the ones that were accepted"),
)

#: The fields an acceptance's identity is made of, in order. One list read by
#: both halves — the key a person copies off a resolution and the key built from
#: a reading — because two lists would agree until the first time either changed,
#: and the symptom is silent: the acceptance simply stops applying and nothing
#: says why.
#:
#: The places come first and the versions after, and the versions are the reason
#: this list is longer than a path and a span. `card_digest` is the understanding
#: baseline the person read, `content_identity` is the source revision the Agent
#: read, and `reading_digest` is the accepted patch itself — the claims somebody
#: said yes to rather than the span they came from. Any of the three moving makes
#: a different acceptance subject: a message rewritten under an unchanged path
#: and span, or a card rewritten under its own path, must not inherit a person's
#: yes.
ACCEPTANCE_IDENTITY = ACCEPTANCE_PLACES + tuple(
    field for field, _ in ACCEPTANCE_VERSIONS)

ACCEPTANCE_SCHEMA = "serveros/understanding-acceptance.v1"

ACCEPTANCE_PRODUCED = {
    "card": "the card the accepted proposal was about, copied from the "
            "resolution",
    "manifest": "the capture object it was about, copied from the resolution",
    "from_position": "the first position it was about, copied from the resolution",
    "to_position": "the last position it was about, copied from the resolution",
    "card_digest": "the card's text the person read, copied from the reading. "
                   "Required, and required to be present rather than merely "
                   "non-null: an acceptance that does not say which revision of "
                   "the card it was about is a yes to a path, and the card "
                   "somebody edits next week would inherit it",
    "content_identity": "the content the Agent read, copied from the reading. "
                        "Required and required present, for the same reason: it "
                        "is what makes a message rewritten inside an unchanged "
                        "span a different subject rather than the same one",
    "reading_digest": "the reading whose claims these are, copied from the "
                      "resolution. Required: a person accepts the claims a "
                      "particular reading produced, and a second reading of the "
                      "same span that says something else must not inherit the "
                      "acceptance",
    "claims": "the claim ids the person accepted, non-empty. An acceptance that "
              "names no claim accepted nothing, and a record of it would read as "
              "a signature on whatever the reading happened to contain",
    "by": "who accepted, as they wrote it. Required: a confirmation nobody is "
          "named for is an assertion by the tool. This module does not "
          "AUTHENTICATE it — a name in a file is not a verified identity, and "
          "reading one as the other is how a tool's own text acquires a person's "
          "authority. It is carried into the receipt as written, so that the "
          "question 'who said this was fine' is answerable by looking rather "
          "than by trusting this run",
    "at": "when they accepted, as they wrote it",
}


def acceptance_key(record: dict) -> tuple:
    """An acceptance's identity, read off the acceptance in one place only."""
    return tuple(record.get(field) for field in ACCEPTANCE_IDENTITY)


def reading_acceptance_key(reading: dict, *, digest: str) -> tuple:
    """The identity an acceptance must carry to be about THIS reading.

    Built from the reading rather than from the resolution, so that the two
    halves of the identity are read from the same material the commit will use.
    """
    source = reading.get("source") or {}
    read = reading.get("read") or {}
    return (source.get("card"), source.get("manifest"),
            source.get("from_position"), source.get("to_position"),
            read.get("card_digest"), read.get("content_identity"), digest)


def read_acceptances(path: Path | str) -> dict[tuple, dict]:
    """Every authored acceptance, keyed by the proposal it was made about.

    Read-only, deliberately: this module has no writer for this file. A record
    here says a PERSON accepted something, and a tool that could write it would
    be signing its own resolution — the same reason `understanding_patch` has no
    writer for its decisions file.
    """
    acceptances: dict[tuple, dict] = {}
    text = Path(path).read_text(encoding="utf-8")
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError as exc:
            raise Malformed(f"{path}:{number}: invalid JSON") from exc
        if not isinstance(entry, dict):
            raise Malformed(f"{path}:{number}: an acceptance must be an object")
        unexpected = sorted(set(entry) - set(ACCEPTANCE_PRODUCED))
        if unexpected:
            raise Malformed(
                f"{path}:{number}: unexpected field(s) "
                f"{', '.join(f'`{key}`' for key in unexpected)}; an acceptance "
                f"records what was accepted about which proposal, by whom, and "
                f"nothing else")
        # PRESENT, not merely non-null. `entry.get` returns `None` for a field
        # that is absent and for one written as null, and null is a legitimate
        # value for a proposal whose content the reader could not identify.
        # An absent field is an acceptance that did not say what it was about.
        for field in ("card_digest", "content_identity"):
            if field not in entry:
                raise Malformed(
                    f"{path}:{number}: `{field}` is absent, so this acceptance "
                    f"names a place and not a revision. A message rewritten "
                    f"under an unchanged path and span, or a card rewritten "
                    f"under its own path, would inherit it and record somebody "
                    f"as having accepted text they never read. Copy the field "
                    f"from the resolution; null is accepted only when the "
                    f"reading itself carries null")
        claims = entry.get("claims")
        if not isinstance(claims, list) or not claims:
            raise Malformed(
                f"{path}:{number}: `claims` is {claims!r}; an acceptance names "
                f"the claim ids it accepts, and one that names none accepted "
                f"nothing")
        for claim_id in claims:
            if not isinstance(claim_id, str) or not CLAIM_ID.match(claim_id):
                raise Malformed(
                    f"{path}:{number}: {claim_id!r} is not a claim id. An id is "
                    f"what a retraction and a supersede cite, so an acceptance "
                    f"that mangles one accepts nothing and says so nowhere")
        record = no_unsourced({
            "card": _require(entry.get("card"), f"{path}:{number}: card"),
            "manifest": entry.get("manifest"),
            "from_position": entry.get("from_position"),
            "to_position": entry.get("to_position"),
            "card_digest": entry.get("card_digest"),
            "content_identity": entry.get("content_identity"),
            "reading_digest": _require(entry.get("reading_digest"),
                                       f"{path}:{number}: reading_digest"),
            "claims": list(claims),
            "by": _require(entry.get("by"), f"{path}:{number}: by"),
            "at": entry.get("at"),
        }, ACCEPTANCE_PRODUCED, "acceptance")
        key = acceptance_key(record)
        if key in acceptances:
            raise Malformed(
                f"{path}:{number}: two acceptances about one proposal "
                f"({key[0]} {key[1]} {key[2]}–{key[3]}); which claims a person "
                f"accepted is not something this module can choose between")
        acceptances[key] = record
    return acceptances


def acceptance_digest(entry: dict | None) -> str | None:
    """One acceptance's own bytes, canonically. None for "no acceptance"."""
    if entry is None:
        return None
    canonical = json.dumps(entry, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def acceptance_for(reading: dict, accepted: dict[tuple, dict] | None) -> dict | None:
    """The acceptance about this reading, or None. A lookup, never a fallback.

    There is no "closest" acceptance. One made about the same place at another
    revision is reported by `_acceptance_report` rather than applied, because a
    yes to text that has since changed is a yes to text that is not there.
    """
    if not accepted:
        return None
    key = reading_acceptance_key(reading, digest=reading_digest(reading))
    return accepted.get(key)


def _accepted_as_written(entry: dict | None) -> dict | None:
    """The acceptance in the form the commit and the receipt carry it.

    Copied and not summarised, for the same reason the delegation is: an
    acceptance restated in this module's words would be an acceptance this
    module invented, and the digest is what lets a later run say the acceptance
    it holds is not the one the write recorded.
    """
    if entry is None:
        return None
    return {"digest": acceptance_digest(entry),
            "by": entry["by"],
            "at": entry.get("at"),
            "claims": list(entry["claims"]),
            "identity": list(acceptance_key(entry))}


def _reading_mismatch(where) -> str:
    """Why one span cannot have two committed readings, and what actually replaces one.

    The remedy is not "supersede the first one", because nothing in this module
    supersedes anything: there is no verb, and a phrase naming a verb that does
    not exist sends a person looking for it. What does work is said instead.
    """
    return (f"this proposal already has a receipt for a different reading "
            f"({where}). Two readings of one span cannot both be the committed "
            f"one, and choosing between them is not this module's decision to "
            f"make. A source read again under a new revision is a new proposal; "
            f"to replace this one, a person removes this receipt deliberately — "
            f"this module has no verb for it, because choosing which of two "
            f"readings of one span is the true one is not its decision either")


def _acceptance_mismatch(prior: dict, accepted: dict | None, where) -> str | None:
    """Why the acceptance this run holds is not the one the receipt recorded.

    The remedy named is deliberately NOT "read the source again". Re-reading does
    not help here: a proposal's identity is its place and its source revision, so
    an unchanged source read again produces the same identity, finds this same
    receipt, and is refused again. The span is written a second time only if a
    person removes the receipt on purpose — and that is a deliberate act this
    module has no verb for, because "write the same span twice" is not a decision
    it makes. Naming a procedure that fails would be worse than naming none.

    One sentence for both call sites. Two sentences about one question drift, and
    the second one to be written is the one nobody re-reads.
    """
    if prior.get("accepted") == accepted:
        return None
    held = ("no acceptance" if not prior.get("accepted")
            else f"an acceptance ({_who(prior['accepted'])}, "
                 f"{_short(prior['accepted'].get('digest'))})")
    now = "an acceptance" if accepted else "no acceptance"
    return (f"this proposal already has a receipt ({where}) and it was written "
            f"for {held}, while this run holds {now}. Returning the receipt "
            f"would report a person's yes that is not in it, and writing again "
            f"would make a second commitment of one span. Reading the source "
            f"again does not help: the proposal is the place and the source "
            f"revision, so an unchanged source re-read is the same proposal and "
            f"finds this receipt. The span is written again only if a person "
            f"removes this receipt deliberately")


def _version_difference(mine: tuple, other: tuple) -> list[str]:
    """Which of the three revisions differ, in words a person can act on."""
    offset = len(ACCEPTANCE_PLACES)
    return [sentence for index, (_, sentence) in enumerate(ACCEPTANCE_VERSIONS)
            if mine[offset + index] != other[offset + index]]


def _who(entry: dict | None) -> str:
    """An acceptance named by its writer, for a message. Never for a decision.

    A string out of a file is fine in a sentence and is not an identity; nothing
    is branched on this, and `by`'s own table entry says why.
    """
    if not entry:
        return "nobody"
    claims = entry.get("claims") or []
    return (f"{entry.get('by')!r} over {len(claims)} "
            f"claim{'s' if len(claims) != 1 else ''}")


def _stale_acceptances(accepted: dict[tuple, dict] | None,
                       key: tuple) -> list[dict]:
    """Acceptances about this place at another revision, each with what moved.

    Reported rather than silently ignored, and the reason is the whole point of
    binding a confirmation to a revision: a person who accepted this span, edited
    the card afterwards, and then watched a run commit nothing has no way to tell
    "your acceptance was not needed" from "your acceptance was for the text that
    was there before you edited the card". The second is the one they must be
    told, and it is only visible if the acceptance is still on the table to be
    compared with the revision this run has.
    """
    if not accepted:
        return []
    stale = []
    place = len(ACCEPTANCE_PLACES)
    for other, entry in accepted.items():
        if other == key or other[:place] != key[:place]:
            continue
        stale.append({"identity": list(other), "by": entry["by"],
                      "at": entry.get("at"),
                      "differs": _version_difference(key, other)})
    return sorted(stale, key=lambda row: [str(part) for part in row["identity"]])


def _acceptance_report(accepted: dict[tuple, dict] | None, key: tuple,
                       mine: dict | None, *, applied: list[str],
                       covered: list[str], not_committable: list[dict],
                       reading_ids: set[str], digest: str) -> dict:
    """What happened to every acceptance supplied, including the ones that did not apply.

    Written as a report of FAILURES as much as successes, because an acceptance
    file is authored by hand and the ways it can miss are all silent: an id that
    is not in this reading, a claim the delegation had already committed, a
    retraction that cannot be committed anywhere, and — the one this exists for —
    an acceptance made about the same span at an earlier revision. A run that
    reported only `applied` would leave a person to conclude the tool ignored
    them.
    """
    named = list(mine["claims"]) if mine else []
    return {
        "supplied": len(accepted) if accepted is not None else None,
        "identity": list(key),
        "reading_digest": digest,
        "as_written": _accepted_as_written(mine),
        "applied": list(applied),
        "covered_by_the_delegation_already": list(covered),
        "not_in_this_reading": sorted(set(named) - reading_ids),
        "not_committable": not_committable,
        "for_other_revisions": _stale_acceptances(accepted, key),
    }


# --------------------------------------------------------------------------
# the delegation: what a machine may commit without asking
# --------------------------------------------------------------------------

def resolve(reading: dict, current: dict, delegation: dict | None,
            accepted: dict[tuple, dict] | None = None) -> dict:
    """Decide, per claim, whether it commits now or waits for a person.

    `current` is the truth about the material right now — `{card_digest,
    content_identity, from_position, to_position}` — measured by the caller from
    the same tree the reading names. `delegation` is the standing grant, if the
    caller has one: `{covers: [subject prefixes], until: <as written>}`.
    `accepted` is `read_acceptances`'s output, or None when no acceptance file
    was supplied; an acceptance is matched by the FULL identity, digests
    included, so one written for this span under an earlier revision matches
    nothing and is reported as such rather than quietly applied.

    An acceptance is a person's ANSWER to a proposal, not an authority this
    module grants: it can turn a claim the machine could not commit into a
    committed one, and it cannot make a retraction commit anywhere or make the
    delegation cover a subject it does not name. The reason both directions
    matter is the same: `user_confirmed` in a card must mean a person was
    required and said yes. Recording it wherever a person happened to agree would
    make the marker say nothing.

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
    mine = acceptance_for(reading, accepted)
    accepting = set(mine["claims"]) if mine else set()

    committed: list[dict] = []
    awaiting: list[dict] = []
    retracted: list[str] = []
    applied: list[str] = []
    covered: list[str] = []
    not_committable: list[dict] = []
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
            if claim["id"] in accepting:
                # Accepting a retraction is not a way to commit it, and saying so
                # is not pedantry: whoever wrote this id into the file expected
                # something to change in a card, and nothing will.
                not_committable.append({
                    "id": claim["id"], "by": mine["by"],
                    "why": "a retraction takes effect locally at once and is not "
                           "committed into a card; accepting it changes nothing "
                           "in this run. If the withdrawal is to be visible in "
                           "the card, the card is edited"})
            awaiting.append({**claim, "confirmation": AWAITING_DECISION,
                             "confirmation_why":
                                 "a retraction takes effect locally at once; "
                                 "committing it into a card is a separate, "
                                 "visible act a person performs"})
            continue
        confirmation, why = _confirmation_for(claim, delegation, delegable)
        if claim["id"] in accepting:
            if confirmation == AGENT_RECONCILED:
                # The delegation had already committed this one on its own
                # authority. Reported as not needed rather than upgraded to
                # `user_confirmed`: a marker that appears wherever a person
                # agreed with the machine stops meaning "this needed a person",
                # which is the one thing it is there to say.
                covered.append(claim["id"])
            else:
                applied.append(claim["id"])
                confirmation = USER_CONFIRMED
                why = (f"accepted by {mine['by']}"
                       + (f" at {mine['at']}" if mine.get("at") else "")
                       + f", for this revision of the source and of the card. It "
                         f"was not the delegation's to commit: {why}")
        record = {**claim, "confirmation": confirmation,
                  "confirmation_why": why}
        # Asked as "is this one decided" rather than "is this one the machine's".
        # The two read the same while `agent_reconciled` was the only way a claim
        # could commit, and they stopped reading the same the moment a person
        # could confirm one: the question the lists answer is which claims are
        # still waiting, and every named confirmation that is not
        # `awaiting_decision` is one that is not.
        if confirmation == AWAITING_DECISION:
            awaiting.append(record)
        else:
            committed.append(record)

    key = reading_acceptance_key(reading, digest=reading_digest(reading))
    return no_unsourced({
        "schema": SCHEMA,
        "reading": reading["source"],
        "read": read,
        # On the resolution and not only on the commit, because an acceptance is
        # authored by copying this value off a resolution — and a proposal whose
        # every claim awaits is exactly the one a person is being asked about, so
        # a digest that appeared only once something committed would be missing
        # every time it was needed.
        "reading_digest": key[-1],
        "committed": committed,
        "awaiting": awaiting,
        "retracted": sorted(set(retracted)),
        "delegation": _delegation_as_written(delegation),
        "acceptance": _acceptance_report(
            accepted, key, mine, applied=applied, covered=covered,
            not_committable=not_committable,
            reading_ids={claim["id"] for claim in reading["claims"]},
            digest=key[-1]),
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
    "reading_digest": "this reading's own bytes as a digest — the third half of "
                      "an acceptance's identity, and the value a person copies "
                      "onto the resolution they are answering",
    "committed": "the claims that commit now, each with the confirmation and why",
    "awaiting": "the claims a person decides, each with the confirmation and why",
    "retracted": "the claim ids a RETRACTION in this reading names. Local "
                 "eligibility acts on these before any remote store is told "
                 "anything, because a withdrawn fact must not stay recallable "
                 "while an update is in flight",
    "delegation": "the standing grant as it was written, or null when there is "
                  "none. Copied and never paraphrased: a grant restated in this "
                  "module's words would be a grant this module invented",
    "acceptance": "what became of every acceptance supplied, including the ones "
                  "that did not apply and why — an id not in this reading, a "
                  "claim the delegation had already committed, a retraction that "
                  "cannot be committed anywhere, and an acceptance made about "
                  "this span at an earlier revision. That last one is the reason "
                  "the section exists: a person's yes is bound to the text they "
                  "read, and a run that reported only what applied would leave "
                  "them to guess why nothing changed",
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
    "accepted": "the acceptance this commit applied, as written, or null when "
                "there was none — copied from the resolution's "
                "`acceptance.as_written`. Carried into the receipt so that a "
                "later run holding a different acceptance is refused by name "
                "instead of being handed a receipt that does not mention it",
    "baseline": "the card the reading was made against and the digest it had "
                "then, copied from `reading.read`. Checked again at the moment "
                "of writing: the check in `resolve` and the check in `write` are "
                "the same question asked at two instants, and the gap between "
                "them is a person's editing session",
    "lines": "what each committed claim becomes in its card, `{card, text}` per "
             "claim. The card is a FIELD and not something read back out of the "
             "text: a line parsed for its owner would make the ownership of a "
             "claim depend on how it was rendered, and rendering changes",
    "views": "the view cards the committed claims are references from, "
             "`{view: {claim id: reference line}}`. Keyed by claim id rather than "
             "held as a list of lines, because the receipt of a view update has "
             "to be able to say WHICH claims that card is now a route to. A "
             "count of lines records that a card was touched and not whose fact "
             "can be reached through it",
    "superseded": "the claim ids the COMMITTED claims of this reading replace, "
                  "from each committed claim's own `supersedes` — the claim ids "
                  "of earlier readings that were written into cards before this "
                  "one. Carried in the commit rather than derived at the write, "
                  "because taking a line out of a card is part of what a person "
                  "is shown before anything happens, and `render_resolution` "
                  "reads it from here. Ids named by a RETRACTION are not in it: "
                  "a retraction is a local withdrawal that is not committed "
                  "anywhere, and it is carried in `retracted` instead",
    "retracted": "the claim ids this reading's RETRACTIONs withdraw, from the "
                 "resolution's own `retracted`. A separate field from "
                 "`superseded` because the two are different acts: a supersede "
                 "takes a line out of a card this commit writes, a retraction "
                 "takes a claim out of what may be recalled and leaves every "
                 "line where it is. This is what makes a withdrawal DURABLE — "
                 "the contract's §6 says it takes hold locally and at once, and "
                 "until this field existed there was nothing to hold it: the "
                 "receipt is the durable record, and a retraction that wrote no "
                 "file left no receipt",
}

RECEIPT_PRODUCED = {
    "schema": "this module's own schema string",
    "key": "the proposal identity the receipt is for, copied from the commit",
    "reading_digest": "the reading that produced it, copied from the commit",
    "accepted": "the acceptance the write was made under, as written, or null "
                "when there was none — copied from the commit. It is compared "
                "on every later run of this proposal: a receipt returned to a "
                "caller holding a different acceptance would report a person's "
                "yes that is not in the write, and the comparison is done by "
                "value so that an acceptance edited after the fact is caught "
                "rather than matched",
    "baseline": "the card the reading was made against and the digest it had "
                "then, copied from the commit's own `baseline`. The commit "
                "checks it again at the moment of writing, and the receipt "
                "keeps it because the commit's file does not survive the run: "
                "on a `RETRACTION_ONLY` receipt this is the ONLY record of which "
                "text the withdrawal was reasoned against, and without it the "
                "durable evidence would name a claim and a source revision and "
                "say nothing about the card the reader was looking at",
    "written": "the files this write actually changed, with the digest each one "
               "had before and after, whether it OWNS the claims or only "
               "REFERENCES them, and the CLAIM IDS that file now carries "
               "(`{card, role, before, after, claims, removed}`). `role` is not "
               "derivable from the other fields — an owning card and a view "
               "produce entries of the same shape, and a reader that took every "
               "entry for an owner would have one claim living in every card "
               "that mentions it. Facts about the disk, "
               "read back after the write and not computed before it. `claims` is "
               "what the file CARRIES when the write returns — this commit's "
               "claims plus every earlier commit's that are still there — and "
               "`removed` is what this write took out. Both are read back out of "
               "the file that was just written rather than taken from the "
               "commit's keys: a card accumulates, so the ids a commit put in it "
               "stopped being the ids the file holds. Deliberately not the NUMBER "
               "of lines, which named no claim at all and left no later reader "
               "able to ask whether a particular claim was still there to be "
               "recalled",
    "pending_views": "the views whose update did not complete, each with the "
                     "error and the references it was to receive, "
                     "`{claim id: reference line}`. Present means the commit is "
                     "PARTIAL: the claim is in its owning card and some "
                     "reference to it is missing. The contract's §5 is explicit "
                     "that this must not be reported as all consumers aligned. "
                     "The REFERENCES are carried here because a resume cannot "
                     "re-derive them: by the time a view is retried, the first "
                     "write has moved the card the reading was reasoned against, "
                     "and re-running the reading against the new baseline is a "
                     "different reading",
    "state": "COMPLETE, PARTIAL, RETRACTION_ONLY, or NOTHING_TO_WRITE. The "
             "first two are decided by `pending_views` and nothing else. The "
             "last two are both receipts with no `written` entry, and they are "
             "NOT the same thing: `NOTHING_TO_WRITE` is RETURNED AND NOT "
             "WRITTEN — a commit whose every claim awaits a person changed no "
             "file, so there is nothing to record, and the proposal stays open "
             "for the acceptance that can commit it — while `RETRACTION_ONLY` IS "
             "written, because a withdrawal has already taken effect locally and "
             "this receipt is the only durable record of it. A later run holding "
             "the acceptance is what tells the two apart, and `write` reads it "
             "for what it is rather than refusing the acceptance on the strength "
             "of a receipt with no write in it",
    "reused": "true when this receipt is an earlier write returned again. A "
              "replay that wrote a second time would be a duplicate commitment "
              "that no reader could tell from a second real change",
    "committed_at": "the time the caller supplied. Never this process's clock: a "
                    "receipt whose time came from the machine that wrote it "
                    "cannot be compared with one from another machine",
    "superseded": "the ids this commit set out to take out of the cards, copied "
                  "from the commit. Copied rather than recomputed so that a "
                  "resumed PARTIAL write removes the same lines it would have "
                  "removed the first time — the intent of a commit is not "
                  "reconstructed from whichever of its files happened to be "
                  "writable",
    "superseded_unmet": "the ids from `superseded` that no line in any file this "
                        "commit wrote carried, so nothing was taken out for "
                        "them. Reported rather than dropped because the claim "
                        "they name is still in a card somewhere — a supersede "
                        "removes where this commit writes, and finding a claim "
                        "across cards is what the receipts are read for. An "
                        "empty list is the ordinary case and says so",
    "retracted": "the claim ids this write's reading WITHDREW, copied from the "
                 "commit's own `retracted`. This is the durable half of a "
                 "retraction: the contract gives a withdrawal effect locally and "
                 "at once, and `resolve`'s `retracted` is a decision inside one "
                 "run that nothing outside that run could read. Recorded "
                 "whether or not this write also committed claims, so that a "
                 "reading that both asserts and withdraws keeps both facts — "
                 "and recorded on a receipt whose `state` is `RETRACTION_ONLY`, "
                 "which is a receipt with no `written` entry at all. Read by "
                 "`intake/claim_recall`, which reads ALL receipts and not just "
                 "the latest, so a withdrawal cannot be undone by a later "
                 "reading that does not mention it. Named limitation: nothing "
                 "UN-retracts a claim. A source that comes back does not revive "
                 "the withdrawn conclusion — it supports a new claim, with its "
                 "own id, its own revision and its own receipt, and this id "
                 "stays withdrawn. That is the contract's §9 (the audit keeps "
                 "'this was believed then' and 'this may not be used now' as two "
                 "facts) and it also means there is no way to say the withdrawal "
                 "itself was a mistake",
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
    """The write this resolution implies, before anything is written.

    The supersede set is collected from the COMMITTED claims only, and that is
    not an omission: a retraction's `supersedes` is a withdrawal that `resolve`
    applies locally and does not commit anywhere, while a committed claim's
    `supersedes` means "this claim replaces that one" — which until now had no
    effect on any file, so a card could hold the replaced claim's line forever
    beside the line that replaced it, both reading as current.

    A retraction's ids go into `retracted`, which is a DIFFERENT field from
    `superseded` and is not merged into it. The two are different acts on a
    claim and a record that collapsed them could not say which happened: a
    supersede takes a line out of the cards this commit writes, and a
    retraction takes a claim out of what may be RECALLED while leaving every
    line where it is. Merging them would also lose the case the contract
    requires to be possible — one claim withdrawn and disputed at the same time
    — because `superseded` is built from committed claims and `retracted` from
    claims that commit nowhere.
    """
    lines = {claim["id"]: {"card": claim["card"], "text": _claim_line(claim)}
             for claim in resolution["committed"]}
    views: dict[str, dict[str, str]] = {}
    superseded: set[str] = set()
    for claim in resolution["committed"]:
        superseded.update(claim["supersedes"])
        for view in claim["views"]:
            views.setdefault(view, {})[claim["id"]] = _view_reference(claim)
    return no_unsourced({
        "schema": SCHEMA,
        "key": list(commit_key(reading)),
        "reading_digest": reading_digest(reading),
        "accepted": resolution["acceptance"]["as_written"],
        "baseline": {"card": reading["source"].get("card"),
                     "card_digest": reading["read"].get("card_digest")},
        "lines": lines,
        "views": views,
        "superseded": sorted(superseded),
        "retracted": list(resolution["retracted"]),
    }, COMMIT_PRODUCED, "commit")


def _claim_line(claim: dict) -> str:
    """One committed claim as the line a person reads in the card.

    The kind, the assertion and the support are all on the line because a reader
    who has to open the source to find out what a sentence is based on will not
    open it. The excerpt is the Agent's quotation and is carried as a quotation.

    The confirmation is the claim's own and not a constant. It used to be
    hardcoded to `agent_reconciled`, which was invisible while nothing else could
    commit and would have gone on saying a machine reconciled a claim that a
    person had signed.

    WHO accepted is deliberately not on the line. The line lives in a card a
    person edits, and the receipt — which is written once and not edited by hand
    into a claim — is where the acceptance is recorded as written. A card line
    naming a person is a claim about that person authored by the tool.

    The id IS on the line, first, and it is not decoration: a card holds the
    claims of every commit that has landed in it, and the id is the only thing
    that lets the next commit find this line again — replace it rather than add a
    second line to it, and take it out when a later claim says it replaces it.
    """
    was = claim["assertion"]["was"]
    change = (f"{was} → {claim['assertion']['now']}" if was
              else claim["assertion"]["now"])
    where = ", ".join(str(position)
                      for position in claim["support"]["positions"]) or "无位置"
    return (f"- `{claim['id']}` **[{claim['kind']}]** {claim['subject']}：{change}"
            f"（{claim['confirmation']}；来源 pos {where}；"
            f"excerpt: {claim['support']['excerpt']}）")


def _view_reference(claim: dict) -> str:
    """One committed claim as the line a view card routes to it with.

    Same shape as the claim line at the front — `- \\`id\\` ` — because the
    merge reads both kinds of line with one pattern, and a view is a card that
    accumulates routes the same way an owning card accumulates claims.
    """
    owner = claim["card"]
    return f"- `{claim['id']}` → `{owner}`（{claim['subject']}）"


def _line_id(line: str) -> str | None:
    """The claim id a line this module wrote carries, or None when it carries none.

    None is not "no claim" and is never read as one. The callers are all in the
    merge, where a line with no readable id is the difference between a claim a
    person can still read in the card and a claim nothing can name — so it is
    refused there rather than skipped.
    """
    match = WRITTEN_LINE.match(line)
    if match is None or not CLAIM_ID.match(match.group(1)):
        return None
    return match.group(1)


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

    A commit with nothing in it writes NOTHING and leaves NO receipt — with one
    exception, and the exception is a withdrawal. The rule for the first case is
    load-bearing rather than tidy: a receipt is the record that a write happened,
    one written for a run that changed no file would answer a later run with
    "already written" and refuse the very acceptance that could commit the claims
    — and the order of events would be the ordinary one, a proposal prepared
    before anybody was asked. The proposal stays open until something is
    committed, and an acceptance arriving afterwards is then exactly what commits
    it.

    A WITHDRAWAL is the exception because it is not nothing. A retraction commits
    nowhere and takes a claim out of what may be recalled at once, and a local
    effect with no durable record of it is a fact that exists only as long as the
    process that computed it. So a run whose only effect is a withdrawal writes a
    receipt with state `RETRACTION_ONLY` — and the receipt it writes is exactly
    what would have locked out the acceptance, which is why the lookup above
    reads a `RETRACTION_ONLY` receipt for what it is: a record of a withdrawal
    with no write in it, so a later run holding the person's answer continues
    instead of being refused.
    """
    if not committed_at or not isinstance(committed_at, str):
        raise Refused(
            "committed_at must be supplied by the caller. A receipt stamped "
            "from this process's clock cannot be compared with one from another "
            "machine, and the comparison is what makes a replay detectable")
    base = _understanding_root(records_root)
    receipt_path = base / ".serveros" / f"receipt-{_key_slug(commit_document['key'])}.json"
    retracted = list(commit_document.get("retracted") or [])
    prior = _read_receipt(receipt_path)
    if prior is not None:
        if prior.get("reading_digest") != commit_document["reading_digest"]:
            raise Refused(_reading_mismatch(receipt_path))
        # A receipt whose only content is a withdrawal is not the record of a
        # write, and the acceptance comparison is carved out for it. That
        # comparison exists so a receipt is never handed back reporting a
        # person's yes that is not in the write; this receipt holds no write, so
        # there is nothing in it for the comparison to protect — and everything
        # for it to block, because the run that took the withdrawal could not
        # commit what was waiting for a person and that person's answer arrives
        # as a later run of the SAME proposal. The withdrawal is not lost by
        # continuing: this run's own `retracted` is written into the receipt it
        # replaces it with.
        withdrawal_only = prior.get("state") == "RETRACTION_ONLY"
        mismatch = _acceptance_mismatch(prior, commit_document.get("accepted"),
                                        receipt_path)
        if mismatch is not None:
            if not withdrawal_only or prior.get("accepted") is not None:
                raise Refused(mismatch)
        elif withdrawal_only or prior.get("state") == "COMPLETE":
            return {**prior, "reused": True}
        elif not withdrawal_only:
            return _resume(prior, base, receipt_path)

    if not commit_document["lines"]:
        receipt = no_unsourced({
            "schema": RECEIPT_SCHEMA,
            "key": commit_document["key"],
            "reading_digest": commit_document["reading_digest"],
            "accepted": commit_document.get("accepted"),
            "baseline": commit_document.get("baseline"),
            "written": [],
            "pending_views": [],
            "state": "RETRACTION_ONLY" if retracted else "NOTHING_TO_WRITE",
            "reused": False,
            "committed_at": committed_at,
            "superseded": [],
            "superseded_unmet": [],
            "retracted": retracted,
        }, RECEIPT_PRODUCED, "receipt")
        # `NOTHING_TO_WRITE` is RETURNED and not written; `RETRACTION_ONLY` is
        # written, and the difference is the whole of the paragraph above.
        if retracted:
            _write_receipt(receipt_path, receipt)
        return receipt

    baseline = commit_document["baseline"]
    superseded = list(commit_document.get("superseded") or [])
    written = []
    # Group the lines by the card that owns them, carrying each claim's id
    # alongside its text. The id is not decoration: it is the key the merge
    # matches on, so that a card carrying an earlier commit's claims gets the
    # line of this one replaced and not a second line beside it. Sorted, so that
    # two runs over one commit produce the same file and the same receipt.
    by_card: dict[str, list[tuple[str, str]]] = {}
    for claim_id, entry in sorted(commit_document["lines"].items()):
        by_card.setdefault(entry["card"], []).append((claim_id, entry["text"]))
    # An owning card that cannot be written is NOT a partial commit: a claim
    # whose owner did not take it was never committed, and reporting it as a
    # missing reference would say the claim is in a card it is not in.
    #
    # The baseline check belongs here and only here. It is the question "is this
    # still the prose the reading was made against", which is a question about
    # the card the reading was ABOUT. A view is not interpreted, only referenced
    # from, so a view edited since the read is a view whose text this write still
    # does not touch.
    for card, claimed in sorted(by_card.items()):
        written.append(_write_fence(records_root, base, card, claimed, baseline,
                                    superseded=superseded, role=OWNER))

    pending = []
    for view, references in sorted(commit_document["views"].items()):
        ordered = sorted(references.items())
        try:
            written.append(_write_fence(records_root, base, view, ordered,
                                        superseded=superseded, role=VIEW))
        except Refused as exc:
            pending.append({"view": view, "why": str(exc),
                            "references": dict(ordered)})

    receipt = no_unsourced({
        "schema": RECEIPT_SCHEMA,
        "key": commit_document["key"],
        "reading_digest": commit_document["reading_digest"],
        "accepted": commit_document.get("accepted"),
        "baseline": baseline,
        "written": written,
        "pending_views": pending,
        "state": "PARTIAL" if pending else "COMPLETE",
        "reused": False,
        "committed_at": committed_at,
        "superseded": superseded,
        "superseded_unmet": _superseded_unmet(superseded, written),
        "retracted": retracted,
    }, RECEIPT_PRODUCED, "receipt")
    _write_receipt(receipt_path, receipt)
    return receipt


def _superseded_unmet(superseded: list[str], written: list[dict]) -> list[str]:
    """The ids a commit set out to remove and found no line for.

    Which file a claim is in is not recorded by this module: a claim has one
    owning card, and an id arriving from an earlier reading names a claim whose
    card this commit may not be writing at all. So the honest answer is not "it
    is gone" but "no line this write carried it" — and it is reported, because a
    supersede that quietly removed nothing leaves the replaced claim in a card,
    reading as current.

    A view whose write is pending is not written yet, so an id that lives only
    there is unmet at this instant and stops being unmet when the resume writes
    it. Recomputed on every receipt rather than carried forward, which is what
    makes that sentence true rather than intended.
    """
    found = {claim_id
             for entry in written
             for claim_id in entry.get("removed") or []}
    return sorted(set(superseded) - found)


def _resume(prior: dict, records_root: Path | str, receipt_path: Path) -> dict:
    """Retry the views a PARTIAL receipt left out, and only those.

    The owning cards are NOT rewritten: those writes already happened, and doing
    them again would rewrite a fence that may have been edited since — the
    module does not get a second opinion about text it has already committed.
    """
    base = _understanding_root(records_root)
    # The supersede set is the commit's INTENT and is copied rather than
    # recomputed: a resume cannot re-derive it, because the first write moved
    # the card the reading was reasoned against.
    superseded = list(prior.get("superseded") or [])
    written = list(prior.get("written") or [])
    pending = []
    for entry in prior.get("pending_views") or []:
        ordered = sorted((entry.get("references") or {}).items())
        try:
            # A pending view is a view by construction — the owning cards are
            # written first and a failure there is not a partial commit — so the
            # role is known here and is not re-derived from the entry.
            written.append(_write_fence(records_root, base, entry["view"],
                                        ordered, superseded=superseded,
                                        role=VIEW))
        except Refused as exc:
            pending.append({**entry, "why": str(exc)})
    receipt = no_unsourced({
        # Copied by name and read with `.get`, not `[]`: a v4 receipt is
        # readable by this version and has no `baseline`, and a resume of one
        # would be a `KeyError` where it should be a finished write. What such a
        # receipt says about the card it was reasoned against is that it does
        # not record one, which is what `None` here means.
        **{key: prior.get(key) for key in ("schema", "key", "reading_digest",
                                           "accepted", "baseline",
                                           "committed_at")},
        "written": written,
        "pending_views": pending,
        "state": "PARTIAL" if pending else "COMPLETE",
        "reused": True,
        "superseded": superseded,
        "superseded_unmet": _superseded_unmet(superseded, written),
        # Carried forward and never recomputed, for the reason `superseded` is:
        # a resume has no reading to re-derive a withdrawal from, and a resume
        # that dropped it would take a claim that has been withdrawn and put it
        # back into what may be recalled — the one thing the contract says a
        # withdrawal must survive.
        "retracted": list(prior.get("retracted") or []),
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
          delegation: dict | None = None,
          accepted: dict[tuple, dict] | None = None, committed_at: str) -> dict:
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

    `accepted` is read before any of this and used twice: `_replay` compares it
    against what the receipt recorded, and `resolve` matches it against the
    reading. Passing it only to `resolve` would make a replay of an accepted
    write return the receipt as though the acceptance were still in play.
    """
    base = _understanding_root(records_root)
    document = read_reading(reading_path)
    prior = _read_receipt(_receipt_path(base, document))
    if prior is not None:
        resumed = _replay(prior, document, records_root, accepted=accepted)
        if resumed is not None:
            return resumed
    result = resolve(document, measure(), delegation, accepted)
    plan = commit(result, document)
    return write(plan, records_root, committed_at=committed_at)


def _replay(prior: dict, reading: dict, records_root: Path | str, *,
            accepted: dict[tuple, dict] | None = None) -> dict | None:
    """An earlier write for this proposal, returned or finished — never redone.

    `None` means there is something for this run to do after all, and there is
    exactly one way that happens: the receipt found records a WITHDRAWAL and no
    write, and this run holds an acceptance the earlier one did not. A withdrawal
    takes effect at once and is not committed anywhere, so the run that performed
    one had nothing to write for the claims that were waiting for a person —
    and that person's answer arrives as a later run of the SAME proposal, which
    the receipt would refuse if it were read as a write. `write` replaces it with
    a receipt carrying the same withdrawal.
    """
    if prior.get("reading_digest") != reading_digest(reading):
        raise Refused(_reading_mismatch(
            _receipt_path(_understanding_root(records_root), reading)))
    mismatch = _acceptance_mismatch(
        prior, _accepted_as_written(acceptance_for(reading, accepted)),
        _receipt_path(_understanding_root(records_root), reading))
    if prior.get("state") == "RETRACTION_ONLY":
        if mismatch is None:
            return {**prior, "reused": True}
        if prior.get("accepted") is None:
            return None
        raise Refused(mismatch)
    if mismatch is not None:
        raise Refused(mismatch)
    if prior.get("state") == "COMPLETE":
        return {**prior, "reused": True}
    base = _understanding_root(records_root)
    return _resume(prior, records_root, _receipt_path(base, reading))


def _write_fence(records_root: Path | str, base: Path, card: str,
                 claimed: list[tuple[str, str]], baseline: dict | None = None,
                 *, superseded: list[str] | None = None, role: str) -> dict:
    """Merge this commit's claims into one card's fence, and touch nothing else.

    `claimed` is `(claim id, line)` pairs and not two parallel lists: the id is
    the key the merge matches on, and a pair cannot be half-supplied.

    `role` is `OWNER` or `VIEW`, and it is recorded in the receipt rather than
    inferred from the shape of the write. Both a claim's owning card and a view
    it is referenced from get an entry in `written`, carrying the same `claims`
    list, and there is nothing about the two writes a later reader could tell
    apart: a claim has ONE owner and N views, so a record that does not say which
    is which makes a claim read as living in every card that mentions it. That is
    not a hypothetical — it is what an A2 recall filter reported before this
    field existed, with the whole of the durable record to read.
    """
    if role not in (OWNER, VIEW):
        raise Refused(
            f"{card}: a write is either a claim's owning card or a view it is "
            f"referenced from; {role!r} is neither, and a receipt entry whose "
            f"role nobody can name is one a later reader has to guess at")
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
    superseded = list(superseded or [])
    # The merge is computed twice, and that is the design rather than waste. The
    # FIRST computation is the write. The SECOND, below, is from the file on
    # disk, and it is what the splice is checked against — so a `_replace_fence`
    # that returned something else, however plausible, is caught here rather
    # than written. `test_the_guard_on_the_splice_is_a_second_opinion` reaches
    # past the first computation to make sure this one is doing the work.
    try:
        body = _replace_fence(before, claimed, superseded=superseded)
    except Refused as exc:
        # Every refusal about one card names that card. The merge's own
        # refusals are about a line inside a file and cannot name it; a person
        # reading one has to know which card to open.
        raise Refused(f"{target}: {exc}") from None
    merged, lines, removed = _merge_claims(_existing(body_before), claimed,
                                           superseded)
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
    # `claims` is every id the file now carries and `removed` is what this write
    # took out — both read back from the file, not taken from the caller. The
    # caller's list is what this COMMIT wanted in the card; a card holds the
    # claims of every commit that landed in it, and a receipt that reported the
    # caller's list would say a claim this file still carries is not in it.
    return {"card": card, "role": role, "before": card_digest(before),
            "after": card_digest(after), "claims": merged, "removed": removed}


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


def _existing(body: str) -> str:
    """The lines in a fence body that the merge has to read back.

    The module's own region when the body carries one, and otherwise the whole
    body — because with no region the only writer of that body was this module.
    An empty body and no fence at all both come out empty here, which is the
    same answer: nothing to merge with.

    Called from the write AND from the check of the write, so that the two
    cannot disagree about which bytes are being merged over. A second reading of
    the same body written out twice is how the check would come to bless a
    splice the write did not perform.
    """
    region = claims_region(body)
    return region[1] if region is not None else body


def _merge_claims(region: str, claimed: list[tuple[str, str]],
                  superseded: list[str]) -> tuple[list[str], list[str], list[str]]:
    """The lines a card's block holds after this commit, as `(ids, lines, removed)`.

    Three effects, and a card needs all three to stay readable:

      * this commit's claims REPLACE the lines of the same id. A claim read
        again over a new revision is a new proposal, but a claim re-committed
        into a card — after a resume, or from a commit that was refused and
        retried — must not appear twice, because two lines for one id are two
        statements a reader has no way to order;
      * the ids a committed claim supersedes are REMOVED. Without this, adding
        accumulation to the writer would turn a lost-line bug into a
        contradictory-card bug: the replaced claim and the claim replacing it
        would both sit there reading as current;
      * every other readable line is KEPT. This is the whole point of the
        merge: a card is a person's view of everything understood about a
        subject, so a commit about one span must not delete what an earlier
        commit about another span put there.

    A line with no readable id is REFUSED, not skipped and not kept. Skipping it
    drops a claim out of the card; keeping it leaves a line nothing can ever
    match on again, so the next commit adds a second line beside it and the card
    holds the same fact twice. Neither is a shape this module ever wrote — it
    writes every line with its id — so the module cannot say which claim such a
    line is, and the one thing it must not do is decide. Two lines carrying one
    id are refused for the same reason: which of them is the claim is not
    answerable from the file.

    Canonical id order, so that a card's block is a function of the claims in it
    rather than of the order they arrived in, and two runs over one commit
    produce the same bytes.
    """
    both = sorted(set(superseded) & {claim_id for claim_id, _ in claimed})
    if both:
        raise Refused(
            f"claim {', '.join(both)} is both committed here and named as "
            f"replaced by this same reading. A claim cannot take its own line "
            f"out of a card, and which of the two the reading meant is not "
            f"something this module can choose")
    kept: dict[str, str] = {}
    for line in region.splitlines():
        if not line.strip():
            continue
        claim_id = _line_id(line)
        if claim_id is None:
            raise Refused(
                f"a line inside this module's own block carries no claim id "
                f"this module can read, so a commit here cannot tell which "
                f"claim it is ({line!r}). Keeping it and writing another line "
                f"beside it would show one claim twice; dropping it would take "
                f"a claim out of this card. A person has to look at this card")
        if claim_id in kept:
            raise Refused(
                f"this module's block holds two lines for claim {claim_id}, so "
                f"which of them is the claim cannot be read off the card. This "
                f"is not a shape this module writes — every line it writes "
                f"carries its id once — so somebody has edited or merged this "
                f"card by hand")
        kept[claim_id] = line
    removed = sorted(claim_id for claim_id in superseded if claim_id in kept)
    for claim_id in removed:
        del kept[claim_id]
    for claim_id, line in claimed:
        kept[claim_id] = line
    merged = sorted(kept)
    return merged, [kept[claim_id] for claim_id in merged], removed


def _replace_fence(text: str, claimed: list[tuple[str, str]], *,
                   superseded: list[str] | None = None) -> str:
    """The card with this commit's claims merged in, and everything else kept.

    Three cases, and the difference between them is the point:

      * no fence — appended. A card nobody has written to has no fence, and
        there the module's lines go after the prose rather than in place of it,
        because the prose is a person's;
      * a fence whose body carries no claims region — the merge runs over the
        whole body. That body is the module's and not a person's, so ownership
        is not in question; identity is. Lines written before the module marked
        its own claims carry no id, and a line with no id cannot be merged, so
        such a body is refused unless it is empty;
      * a fence with the region — the merge runs over the region alone. Every
        other byte inside the fence, and everything outside it, is what it was
        before.

    A card holding one marker of a pair and not its counterpart is REFUSED, not
    repaired — both pairs, for the same reason and with different consequences.
    A half FENCE leaves the stale half in the card and puts a second copy of the
    lines beside it, and `fence_parts` calls such a card all head: the markers
    are the guard that says a person's prose is not this module's, so a card
    with one of them has no guard at all. A half CLAIMS region is read as no
    region, which puts a person's lines into the merge and takes the refusal
    above. Neither half is a shape this module ever wrote, so in either the
    module cannot say which lines are its own.
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
        # Merged against nothing, rather than written in the caller's order: the
        # check below re-runs this same merge, and a card whose block came out
        # in a different order here would be refused by a splice that had in
        # fact done what it was asked.
        _, lines, _ = _merge_claims("", claimed, list(superseded or []))
        return text + separator + FENCE_BEGIN + _fence_body(lines) + FENCE_END
    head, body, tail = fence_parts(text)
    if claims_region(body) is None and (CLAIMS_BEGIN in body
                                        or CLAIMS_END in body):
        which = "begin" if CLAIMS_BEGIN in body else "end"
        raise Refused(
            f"this card holds the claims region's {which} marker and not its "
            f"counterpart, so the lines this module wrote cannot be told from "
            f"the ones a person added next to them. An unmarked fence is merged "
            f"wholesale only because nothing else could have written in it — "
            f"and this card says somebody did. A person has to look at this "
            f"card")
    _, lines, _ = _merge_claims(_existing(body), claimed, list(superseded or []))
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
    if not isinstance(document, dict):
        return None
    # A receipt this module does not recognise is not an absent one. Every
    # question a caller asks of a receipt — was this proposal already written,
    # which claims went into which card, under which acceptance — is answered
    # from its shape, and one shape is not another. Refusing by name is the only
    # reading that cannot be wrong: the alternative is a v1 receipt, whose
    # `written[i].claims` was a COUNT, being read by something that expects the
    # claim ids and reporting a number as a list.
    #
    # The list of shapes it DOES recognise is `READABLE_RECEIPT_SCHEMAS`, and a
    # schema on it is read directly rather than migrated: a receipt is the
    # record of a write that happened, and rewriting one into a newer shape
    # would make a file that says it was written a way it was not.
    if document.get("schema") not in READABLE_RECEIPT_SCHEMAS:
        raise Refused(
            f"{path} is a receipt this module does not recognise: its schema is "
            f"{document.get('schema')!r}, and this version reads "
            f"{' and '.join(READABLE_RECEIPT_SCHEMAS)}. It may be the record of "
            f"a commit of this proposal made by a version whose `written[i]` "
            f"means something else, and this module cannot tell which claims it "
            f"wrote or under which acceptance. Writing again on top of it would "
            f"duplicate whatever it holds; inspect the file before retrying")
    return document


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
        if claim["supersedes"]:
            # On the screen because it is a change to a card a person may be
            # looking at: this commit takes those lines out. A person told only
            # that a new claim is being written would find a line gone later and
            # have nothing to connect it to.
            lines.append(f"    取代 {', '.join(str(i) for i in claim['supersedes'])}"
                         f"（这些行会从它所在的卡片移出）")
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
    lines.extend(_acceptance_lines(resolution["acceptance"]))
    return lines


def _acceptance_lines(report: dict) -> list[str]:
    """The acceptance section of a resolution, the failures at the same weight.

    A person who supplied a file and watches a run commit nothing has to be able
    to read why off the screen, and every way an acceptance can miss is
    invisible in a report that lists only what applied: an id that is not in the
    reading, a claim the delegation had already committed, a retraction that
    cannot be committed anywhere, and an acceptance made about this span at the
    text that was there before somebody edited the card. The last one is the
    reason this section exists at all.
    """
    supplied = report["supplied"]
    if supplied is None:
        return []
    who = report["as_written"]
    lines = [f"acceptance  收到 {supplied} 条；"
             + (f"1 条匹配本次读数（{who['by']}，{len(who['claims'])} 条主张）"
                if who else "没有一条匹配本次读数")]
    if not who:
        lines.append(f"            本次读数的身份  card "
                     f"{_short(report['identity'][len(ACCEPTANCE_PLACES)])} · "
                     f"content {_short(report['identity'][len(ACCEPTANCE_PLACES) + 1])} · "
                     f"reading {_short(report['reading_digest'])}")
    if report["applied"]:
        lines.append(f"  + 采纳 {len(report['applied'])} 条："
                     + ", ".join(report["applied"]))
    for claim_id in report["covered_by_the_delegation_already"]:
        lines.append(f"  · {claim_id} 常设委托已自行提交，无需确认"
                     f"（记录为机器自洽，而不是人工确认）")
    for claim_id in report["not_in_this_reading"]:
        lines.append(f"  ! {claim_id} 不在本次读数里")
    for entry in report["not_committable"]:
        lines.append(f"  ! {entry['id']} 无法被接受：{entry['why']}")
    for entry in report["for_other_revisions"]:
        lines.append(f"  ! 另一版本上的确认（{entry['by']}"
                     + (f"，{entry['at']}" if entry.get("at") else "")
                     + "）：" + "；".join(entry["differs"])
                     + "。确认是对文本的，而文本已经不是这份文本")
    return lines


def render_receipt(receipt: dict) -> list[str]:
    """A receipt as lines. PARTIAL is the line that matters, so it carries why."""
    lines = [
        _state_line(receipt),
        f"proposal    {_short(receipt['key'][0])}  "
        f"pos {receipt['key'][2]}–{receipt['key'][3]}  "
        f"content {_short(receipt['key'][4])}",
        f"reading     {_short(receipt['reading_digest'])}",
        f"written     {len(receipt['written'])} 个文件",
    ]
    if receipt.get("accepted"):
        lines.append(f"accepted    {_who(receipt['accepted'])}  "
                     f"{_short(receipt['accepted']['digest'])}")
    for entry in receipt["written"]:
        # The role is printed because it changes what the line MEANS. An owning
        # card holds the claim; a view holds a reference to it, and a reader who
        # took the second for the first would go looking for a claim in a card
        # that never had it.
        described = "持有" if entry.get("role") == OWNER else "引用"
        lines.append(f"  · [{described}] {entry['card']}  {_short(entry['before'])} → "
                     f"{_short(entry['after'])}  {_claim_ids_text(entry['claims'])}"
                     + (f"  移出 {', '.join(entry['removed'])}"
                        if entry.get("removed") else ""))
    if receipt.get("retracted"):
        # Printed for the same reason `superseded_unmet` is, and more so: this
        # list is the whole of what a withdrawal leaves behind. A receipt read
        # without it says a run changed nothing, when what it did was take
        # claims out of use.
        lines.append(f"  撤回 {', '.join(receipt['retracted'])}"
                     f"（这些主张不得再作为当前事实召回；卡片里的行仍在）")
    if receipt.get("superseded_unmet"):
        # Reported because it is the one outcome of a supersede that looks like
        # success and is not: nothing was taken out, and the claim the commit
        # meant to replace is still in a card this write did not open.
        lines.append(f"  ! 取代的目标在本次写入的卡片里没有对应行："
                     f"{', '.join(receipt['superseded_unmet'])}"
                     f"（该主张仍在它自己的卡片里）")
    for entry in receipt["pending_views"]:
        lines.append(f"  ! {entry['view']}  {entry['why']}"
                     + (f"  {_claim_ids_text(sorted(entry.get('references') or {}))}"
                        if entry.get("references") else ""))
    lines.append(f"committed   {receipt['committed_at']}"
                 + ("  （重放：未再次写入）" if receipt["reused"] else ""))
    return lines


def _claim_ids_text(claim_ids) -> str:
    """Which claims one written card now carries, in a person's words.

    The ids are listed and not only counted. "2 条" tells a reader that
    something was written; it does not tell them whether the fact they were
    looking for is in there, and it is the second question a receipt is read to
    answer — the first being whether anything was written at all.
    """
    ids = list(claim_ids or [])
    return f"{len(ids)} 条" + (f"：{', '.join(str(i) for i in ids)}" if ids else "")


def _state_line(receipt: dict) -> str:
    """The state, and what it means for a person — one gloss per state.

    NOTHING_TO_WRITE reads like a failure and is not one: nothing was committed
    because nothing could be, no file was touched, and no receipt was left, which
    is what keeps the proposal open for the acceptance that can commit it.

    RETRACTION_ONLY reads like NOTHING_TO_WRITE — no file, nothing written — and
    is not the same thing at all: a withdrawal has already taken hold, and this
    receipt is the only durable record of it. The gloss says so, because the two
    states differ in the one way a person is looking at this list to find out.
    """
    gloss = {
        "PARTIAL": "  —— 主张已在归属卡片，引用未补齐",
        "NOTHING_TO_WRITE": "  —— 本轮无可提交的条目，未写任何文件、也未留收据；"
                            "提案仍然开着，等确认到达",
        "RETRACTION_ONLY": "  —— 本轮只有撤回，没有写入任何文件；撤回已在本轮即时"
                           "生效，本收据是它唯一的持久记录",
    }
    return f"state       {receipt['state']}{gloss.get(receipt['state'], '')}"


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
