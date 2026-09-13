#!/usr/bin/env python3
"""Layer B records — the ledger's admission rule and its supersession algebra.

    from record import validate, audit, current_value
    python3 record.py --validate ledger/*.yaml
    python3 record.py --subject project/serveros/SRV-0007 ledger/*.yaml

`memory/record-contract.yaml` declares eight kinds, seven required fields, a
forbidden-content list, and a supersession rule that says a record "is never
edited to say something different" and that "the current value of a subject is
the newest record in its supersession chain". It also says, in `rules`, that
"an observation without confidence is refused".

Nothing refused anything. The contract was a description of a ledger that did
not exist, and a description cannot fail.

Two functions, and the split between them is the design:

    validate(record)   one record against one record's rules. It takes a
                       single record on purpose, because the caller that
                       matters is an author checking a record before it is
                       added to a ledger it is not yet part of.
    audit(records)     the whole-ledger properties that no single record can
                       have: duplicate ids, supersession cycles, a `supersedes`
                       naming a record that is not here, a correction of a
                       different subject.

Both are pure. Neither reads a file, a clock or an environment: a ledger is a
list of records and a ledger revision is an argument, so the answer is the same
on three devices.

What a schema cannot check
--------------------------
The contract's forbidden list has four entries and only some are checkable. A
program can recognise a PEM block, a cloud key prefix, a JWT, or a `password=`
assignment; it cannot tell a sentence somebody wrote from a sentence somebody
pasted out of a transcript. `audit()` therefore returns a `not_checked` list
naming the rules it did not enforce, rather than reporting a clean ledger it
has no basis to report. The contract already says the schema's job is to force
the claim out loud — "a missing field forces the author to make the claim out
loud, which is all a schema can do" — and the same honesty applies to the rules
it is enforcing only partly.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SCHEMA = "serveros/memory-record.v1"

# Transcribed from `record-contract.yaml#kinds`. Asserted against the file by
# test, because this round has now found the same closed vocabulary written
# twice in five different places and disagreeing at least twice.
KINDS = ("decision", "observation", "revision", "handoff", "finding", "question",
         "checkpoint", "incident")

REQUIRED = ("id", "kind", "subject", "at", "by", "source", "revision")
OPTIONAL = ("body", "supersedes", "related", "device_ref", "to_device_ref",
            "confidence", "note")

# Required only for some kinds. `confidence` is here rather than in `REQUIRED`
# because the contract scopes it: "high | medium | low — for observations
# only", and then makes it a refusal in `rules` rather than a suggestion.
#
# `to_device_ref` is required for `handoff` for the reason the contract gives:
# a handoff nobody is addressed by is a handoff nobody receives, and nothing
# downstream can tell an unread record from a read one. See
# `memory/handoff.py` — `pending()` is the consumer that makes the field
# load-bearing rather than decorative.
REQUIRED_FOR_KIND = {"observation": ("confidence",), "handoff": ("to_device_ref",)}
CONFIDENCE_VALUES = ("high", "medium", "low")

ID_RE = re.compile(r"^mem:[a-z0-9]+(?:-[a-z0-9]+)*$")
# ISO-8601 UTC. The contract says "When the fact became true, not when
# written", and requires the zone, because an offset-less timestamp about a
# three-device fleet is a fact with an unstated premise.
AT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")
SUBJECT_RE = re.compile(r"^(?:device:[a-z0-9][a-z0-9._-]*|[A-Za-z0-9][\w.-]*/"
                        r"[\w.-]+/[\w.-]+)$")

# ---------------------------------------------------------------------------
# The checkable half of the forbidden-content list.
#
# Deliberately prefix-anchored rather than entropy-based. An entropy heuristic
# flags a git SHA, which every `revision` field holds and which is not a
# secret; a check that refuses legitimate records gets switched off, and this
# round has already paid for that lesson once.
# ---------------------------------------------------------------------------
_SECRET_PATTERNS = (
    ("a private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("an AWS access key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("a GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}")),
    ("a Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}")),
    ("an API key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}")),
    ("a JSON web token", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.")),
    ("a MAC address", re.compile(r"\b(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}\b")),
    # A value assigned to a credential-shaped name. The placeholder forms are
    # excluded because `token: ${VAULT_TOKEN}` and `token: <from vault>` are
    # references, and refusing them would make the rule unusable in precisely
    # the records that are being careful.
    ("a credential written as a value",
     re.compile(r"(?i)\b(?:password|passwd|secret|token|api[_-]?key|credential)"
                r"\b\s*[:=]\s*(?!\$\{?|\$[A-Z_]|<|env:|vault:)\S{8,}")),
)

# The part of the forbidden list no program here can decide. Reported rather
# than assumed clean, for the same reason `authority_conflict` in
# `fleet/state-root-contract.yaml` carries a `does_not_detect` block.
NOT_CHECKED = (
    "a raw transcript body — prose cannot be told from a transcript",
    "a message body from a chat or meeting — same",
)


def canonical_at(value) -> str | None:
    """`at` as the contract's canonical ISO-8601 UTC string, or None.

    YAML parses an unquoted `2026-09-13T06:00:00Z` into a `datetime`, not a
    string. The first live run of this validator refused all seven baseline
    records for it — every one of them correct, every one of them written in
    the format the contract itself uses.

    The tempting fix is to require authors to quote the value. That is the
    wrong fix: the input was unambiguous, YAML's reading of it is unambiguous,
    and a schema that demands quoting to work around a parser blames the author
    for something they did not do wrong. A check that refuses correct input is
    a check that gets switched off.

    So both readings are accepted, and what is NOT accepted is unchanged: a
    datetime with no timezone. That is the case the rule exists for — an
    offset-less timestamp about a three-device fleet is a fact with an unstated
    premise — and it stays refused.
    """
    from datetime import datetime, timezone

    if isinstance(value, datetime):
        if value.tzinfo is None:
            return None
        return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(value, str) and AT_RE.match(value):
        return value
    return None


def _sort_key(record: dict) -> str:
    """Order records by when the fact became true.

    Through `canonical_at`, so a record whose author left the timestamp
    unquoted sorts against a quoted one rather than next to it — `str()` on a
    datetime gives `2026-09-13 06:00:00+00:00`, which shares its first eight
    characters with the ISO form and then stops matching it.
    """
    return canonical_at(record.get("at")) or ""


class RecordRefused(Exception):
    """A record or a ledger does not satisfy the contract.

    Carries every reason rather than the first, because an author who is told
    about one refusal at a time fixes records one at a time, and the second
    pass has the same cost as the first.
    """

    def __init__(self, reasons: list[str]) -> None:
        self.reasons = list(reasons)
        super().__init__("; ".join(self.reasons))


def _text_fields(record: dict) -> list[tuple[str, str]]:
    """The free-text fields, which are the only ones scanned for content.

    Not `revision`, not `source`, not `at`: those hold SHAs, paths and
    timestamps, and a scanner that reads them is a scanner that refuses the
    ledger for containing git revisions.
    """
    out = []
    for key in ("body", "note"):
        value = record.get(key)
        if isinstance(value, str):
            out.append((key, value))
    return out


def validate(record: dict) -> dict:
    """One record against one record's rules. Returns it unchanged.

    Returns the record rather than a normalized copy, because a validator that
    rewrites its input is a second producer of the ledger's content, and the
    ledger's authority rule is that there is one.
    """
    if not isinstance(record, dict):
        raise RecordRefused([f"a record is a mapping, not {type(record).__name__}"])
    reasons: list[str] = []

    for field in REQUIRED:
        if not record.get(field):
            reasons.append(f"{field} is required and empty")

    record_id = record.get("id")
    if record_id and not ID_RE.match(str(record_id)):
        reasons.append(f"id {record_id!r} is not `mem:<kebab>`; ids are stable "
                       "and never reused, so the shape is what makes one "
                       "recognisable in a citation")

    kind = record.get("kind")
    if kind and kind not in KINDS:
        reasons.append(f"kind {kind!r} is not one of {', '.join(KINDS)}")

    for field in REQUIRED_FOR_KIND.get(str(kind), ()):
        if not record.get(field):
            reasons.append(f"{field} is required for a {kind} record")

    confidence = record.get("confidence")
    if confidence is not None and confidence not in CONFIDENCE_VALUES:
        reasons.append(f"confidence {confidence!r} is not one of "
                       f"{', '.join(CONFIDENCE_VALUES)}")

    at = record.get("at")
    if at is not None and canonical_at(at) is None:
        reasons.append(f"at {at!r} is not ISO-8601 UTC; without the zone an "
                       "ordering across three devices has an unstated premise")

    subject = record.get("subject")
    if subject and not SUBJECT_RE.match(str(subject)):
        reasons.append(
            f"subject {subject!r} is neither `project/<project>/<Item>` "
            "(e.g. `project/serveros/SRV-0007`) nor `device:<id>`. Three "
            "slash-separated parts, and the first two name a project rather "
            "than being the literal words `project` and `repo` — the old "
            "message said `project/repo/Item` and following it literally "
            "produced a string that validated and identified nothing"
        )

    # An addressee is a device, and only a device. `device_ref` may point at
    # anything the fleet registry enrolls; `to_device_ref` is what a receiving
    # device matches against its own `device_ref`, so a value in any other
    # shape is a handoff that no device can receive — which is the failure the
    # field exists to make impossible.
    to_device = record.get("to_device_ref")
    if to_device is not None:
        if not str(to_device).startswith("device:"):
            reasons.append(f"to_device_ref {to_device!r} is not a `device:<id>`; "
                           "a handoff is addressed to a device, because a device "
                           "is the thing that can look for one")
        elif kind != "handoff":
            reasons.append(f"to_device_ref is set on a {kind} record; only a "
                           "handoff has an addressee, and a field that looks "
                           "like one elsewhere is how a reader starts believing "
                           "an observation was sent to it")

    for field in ("supersedes", "related"):
        value = record.get(field)
        if value is None:
            continue
        values = value if isinstance(value, list) else [value]
        for one in values:
            if not ID_RE.match(str(one)):
                reasons.append(f"{field} {one!r} is not a `mem:<id>`")

    for field in record:
        if field not in REQUIRED and field not in OPTIONAL:
            reasons.append(f"{field!r} is not a declared field; an undeclared "
                           "field is a fact the ledger cannot promise anything "
                           "about")

    for name, text in _text_fields(record):
        for what, pattern in _SECRET_PATTERNS:
            if pattern.search(text):
                reasons.append(f"{name} contains {what}; the ledger is "
                               "versioned and synced, and both make a leak "
                               "worse. Point at the object, do not carry it")

    if reasons:
        raise RecordRefused(reasons)
    return record


def audit(records: list[dict]) -> dict:
    """The whole-ledger properties. Returns a report; never raises.

    A ledger with a broken record in it is a normal state — the record is in
    the history and cannot be un-written — so this reports rather than
    refusing. `validate` is the gate on the way in; this is the check on what
    is already there.
    """
    findings: list[dict] = []
    by_id: dict[str, dict] = {}

    for index, record in enumerate(records):
        try:
            validate(record)
        except RecordRefused as exc:
            findings.append({"code": "RECORD_INVALID", "at": index,
                             "id": (record or {}).get("id"),
                             "reasons": exc.reasons})
            continue
        record_id = record["id"]
        if record_id in by_id:
            findings.append({"code": "DUPLICATE_ID", "id": record_id,
                             "reasons": [f"{record_id} appears more than once; "
                                         "an id is stable and never reused"]})
            continue
        by_id[record_id] = record

    for record_id, record in by_id.items():
        target = record.get("supersedes")
        if target and not isinstance(target, list):
            superseded = by_id.get(target)
            if superseded is None:
                if target not in {r.get("id") for r in records}:
                    findings.append({"code": "SUPERSEDES_ABSENT", "id": record_id,
                                     "reasons": [f"supersedes {target}, which is "
                                                 "not in this ledger"]})
            elif superseded.get("subject") != record.get("subject"):
                findings.append({
                    "code": "SUPERSEDES_ANOTHER_SUBJECT", "id": record_id,
                    "reasons": [f"supersedes {target}, which is about "
                                f"{superseded.get('subject')!r} and not "
                                f"{record.get('subject')!r}; a correction of a "
                                "different subject is not a correction, and it "
                                "makes `current value of a subject` incoherent"]})

    for cycle in _cycles(by_id):
        findings.append({"code": "SUPERSESSION_CYCLE", "id": cycle[0],
                         "reasons": [" -> ".join(cycle + [cycle[0]])]})

    return {
        "schema": SCHEMA,
        "records": len(records),
        "valid": len(by_id),
        "findings": findings,
        "not_checked": list(NOT_CHECKED),
        "verdict": "CONFORMANT" if not findings else "DEFECT",
    }


def _cycles(by_id: dict[str, dict]) -> list[list[str]]:
    """Supersession cycles, each reported once.

    The contract resolves a subject by walking the chain to its end. A cycle
    has no end, and the walk in `current_value` would stop at its own guard
    rather than at a fact — which is why this is a finding and not an
    assumption about the data.
    """
    seen_cycles: list[list[str]] = []
    closed: set[str] = set()
    for start in sorted(by_id):
        if start in closed:
            continue
        path: list[str] = []
        node = start
        while node and node not in path:
            path.append(node)
            target = by_id.get(node, {}).get("supersedes")
            node = target if isinstance(target, str) else None
        if node and node in path:
            cycle = path[path.index(node):]
            closed |= set(cycle)
            seen_cycles.append(cycle)
    return seen_cycles


def chain(records: list[dict], record_id: str) -> list[dict]:
    """The supersession chain ending at `record_id`, newest first.

    Newest first because that is the order the question is asked in: "what does
    this subject currently say" is answered by the first element, and "and what
    did it say before" is the rest.
    """
    by_id = {r["id"]: r for r in records if isinstance(r, dict) and r.get("id")}
    out: list[dict] = []
    node = record_id
    while node and node in by_id and node not in {r["id"] for r in out}:
        out.append(by_id[node])
        target = by_id[node].get("supersedes")
        node = target if isinstance(target, str) else None
    return out


def heads(records: list[dict], subject: str, *, at: str | None = None) -> list[dict]:
    """Every record about `subject` that no other record supersedes.

    OFTEN MANY, AND THAT IS ORDINARY. Eight records about one work item — four
    decisions, three findings, an observation — are eight heads when none of
    them corrects another, and nothing is wrong. This docstring said "usually
    one" until the first real ledger put eight records under one subject and
    the projection reported it as a conflict.

    The lesson is worth more than the fix: **the ledger cannot detect a
    conflict.** Two decisions about one subject may answer the same question
    and disagree, or answer two different questions and agree. Nothing in a
    record says WHICH claim it is about, so no field can tell those apart, and
    a check that flagged every repeated kind would be wrong on the ordinary
    case and ignored on the day it was right. Supersession is the only conflict
    mechanism the ledger has, and it is a mechanism the author has to use.

    `at` bounds the window by the time the facts became true, not the time they
    were written, so a caller replaying a subject as of a moment gets the head
    as of that moment.
    """
    mine = [r for r in records
            if isinstance(r, dict) and r.get("subject") == subject
            and (at is None or _sort_key(r) <= at)]
    if not mine:
        return []
    superseded = {r["supersedes"] for r in mine
                  if isinstance(r.get("supersedes"), str)}
    current = [r for r in mine if r.get("id") not in superseded]
    if current:
        return sorted(current, key=_sort_key)
    # Every record is superseded by one of the others, which means the chain
    # contains a cycle and has no head. `audit` reports the cycle as a defect;
    # returning [] here would read as "nothing is known about this subject",
    # which is a different and more misleading answer than "the chain is
    # broken".
    return sorted(mine, key=_sort_key)


def current_value(records: list[dict], subject: str, *, at: str | None = None):
    """The current value of a subject: the newest head, or None.

    Returns the newest of `heads()` — and when there is more than one, the
    caller has already been given the means to notice: `heads()` is the answer
    to "is this subject in conflict", and `memory/projection.py` records the
    count in every subject it writes. Returning None for a conflicted subject
    would hide the conflict behind the same value as "never recorded".

    None for a subject with no records, which is a valid state and not an
    error — most subjects have no memory.
    """
    found = heads(records, subject, at=at)
    if not found:
        return None
    return sorted(found, key=_sort_key)[-1]


def load(paths: list[Path]) -> list[dict]:
    """Read a ledger from YAML or JSON files. One record per file.

    A file holding a list is refused rather than flattened, because a ledger
    whose unit of review is a file inherits the review. A file holding three
    records is a file whose diff cannot say which record changed.

    `at` is canonicalized here and nowhere else. YAML hands an unquoted
    `2026-09-13T06:00:00Z` back as a `datetime`, which is not JSON-serializable
    — so a projection built from such a ledger died in the writer, three layers
    away from the cause, after the validator had already admitted the record.
    Normalizing at the boundary is one fix for every consumer; normalizing in
    `validate` would have been a validator that rewrites its input, and
    normalizing in the writer would have left the next consumer to rediscover
    it.

    A value `canonical_at` cannot read is passed through untouched, so that
    `validate` is still the thing that reports it, with its own reason.
    """
    import yaml

    records = []
    for path in paths:
        text = Path(path).read_text(encoding="utf-8")
        document = (json.loads(text) if str(path).endswith(".json")
                    else yaml.safe_load(text))
        if isinstance(document, list):
            raise RecordRefused([f"{path} holds a list; one record per file, "
                                 "so that a diff names the record that changed"])
        if isinstance(document, dict) and "records" in document:
            raise RecordRefused([f"{path} wraps its record in `records:`; the "
                                 "file is the record"])
        if isinstance(document, dict) and "at" in document:
            canonical = canonical_at(document["at"])
            if canonical is not None:
                document["at"] = canonical
        records.append(document)
    return records


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("ledger", nargs="+", type=Path)
    parser.add_argument("--validate", action="store_true",
                        help="audit the whole ledger and report")
    parser.add_argument("--subject",
                        help="print the current value of one subject")
    parser.add_argument("--chain", help="print the supersession chain of one id")
    args = parser.parse_args(argv)

    records = load(args.ledger)

    if args.chain:
        json.dump(chain(records, args.chain), sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    if args.subject:
        value = current_value(records, args.subject)
        if value is None:
            sys.stderr.write(f"no records for {args.subject}\n")
            return 2
        json.dump(value, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
        return 0

    report = audit(records)
    json.dump(report, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0 if report["verdict"] == "CONFORMANT" else 1


if __name__ == "__main__":
    raise SystemExit(main())
