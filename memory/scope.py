#!/usr/bin/env python3
"""§18 — who may recall a record, decided in one place.

    from scope import thread, OPERATOR_READING, require, recall
    python3 scope.py --subject project/serveros/SRV-0007 --as operator ledger/*.yaml

`docs/memory-fabric.md` has stated the rule since §53, in prose, and nothing
implemented it:

    "**Cross-Item recall is refused by default.** A fact that should be visible
    to more than one Item must be promoted to a project-level object first —
    the promotion is the consent, and it is recorded."

A rule assigned to whoever gets there first, and nobody does, is the defect
this round keeps finding. The prose is also the reason the fix is small: the
consent was already specified as *recorded*, which means it can live in the
record — and a fact that lives in the record is one `validate()` can see, one
reviewer reads in the change, and one `audit()` can diff.

Why this is not in `record.py`
------------------------------
`record.py` is the ledger's admission rule and its supersession algebra, and
both are questions about a record on its own terms. Recall is not: it is a
question about a READER, and a reader is not a record. Putting it there would
put the one rule that depends on who is asking inside the module whose whole
design is that the answer is the same on three devices. `record.py` loads this
module, never the other way round.

The three scopes
----------------
    item      the default. Recallable from a thread bound to this subject's
              own Item, and nowhere else.
    project   promoted. Recallable from any Item of the same project.
    user      promoted further. Recallable from any Item on the node.

Two promotions, one mechanism: the field. `scope: project` on a record IS the
promotion and IS the consent, and it sits beside `by`, `at` and `revision`
like every other authored claim — so a promotion is a reviewed line in a diff
rather than a side effect of some `promote()` call that wrote a second copy of
the fact somewhere else.

There is deliberately no permission store for `user`. The scope model names
"permitted user memory", and the permission IS the promotion: a record somebody
raised to node-wide reach, with their name in `by`, is the record of that
permission. A second store would be a second authority with no producer — the
pathology this round exists to close — and would put the answer to "may this be
recalled" in a file no reviewer of the fact will ever open.

What is refused, and each refuses DIFFERENTLY
---------------------------------------------
Three words, because the three repairs are three different actions:

    CROSS_ITEM_RECALL    the fact belongs to another Item and was never
                         promoted. Repair: bind a thread to that Item, or
                         promote the fact if it is genuinely project-wide.
    UNBOUND_READER       the reader names no Item — a coordinator, or a session
                         from before §17. Repair: bind the thread.
    UNPROMOTED_SUBJECT   the subject is not on the Item axis at all (a
                         `device:` fact) and was never promoted. Repair:
                         promote it if it is a fleet fact, or address it to a
                         device with a handoff — which is a stronger claim
                         than a scope, because it names its recipient.

An operator is not a thread
---------------------------
`OPERATOR_READING` admits everything, and it is a NAMED value rather than the
default so the admission is visible in the call and in the reading that comes
back. A person reading their own ledger is the ledger's author, and no program
can draw a recall boundary inside one person's own git repository. The boundary
is where a THREAD's context is assembled. What keeps that boundary from being
walked around is that the reader must say which it is: there is no default, and
`runtime/cli.py` requires `--as`, so a thread that shells out has to write down
the Item it is reading as. Same shape as §17's `session_role` — no default,
because a default would be a guess about somebody's intent recorded as a fact.

Reads leave a trace HERE and nowhere else
-----------------------------------------
`docs/memory-fabric.md`: "the ledger records what was **written**, never what
was **read**." That stays true — nothing in this module writes to the ledger.
What changed is that a read now RETURNS a reading: the subject, the reader,
what was admitted and what was refused, with the reason. Before this a refusal
was a shorter list, and a shorter list is indistinguishable from "nothing was
ever recorded here" — the exact confusion `audit()`'s `not_checked` discipline
exists to avoid. `audit()` still cannot see a read that happened yesterday, and
the honest statement of that limit is that reads are reported to the reader and
never journaled, rather than that they are audited.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path

SCHEMA = "serveros/memory-scope.v1"

# The closed vocabulary. Transcribed from `record-contract.yaml#scope.values`
# and asserted against the file by test, because this round has found the same
# closed vocabulary written twice and disagreeing.
ITEM = "item"
PROJECT = "project"
USER = "user"
SCOPES = (ITEM, PROJECT, USER)
DEFAULT_SCOPE = ITEM

# A reader. `thread` is a session with a purpose (§17); `operator` is a person
# holding the ledger. Only the first is a boundary.
THREAD = "thread"
OPERATOR = "operator"
READER_KINDS = (THREAD, OPERATOR)

# The word that selects the operator, on the command line and in `--as`.
OPERATOR_WORD = "operator"

CROSS_ITEM_RECALL = "CROSS_ITEM_RECALL"
UNBOUND_READER = "UNBOUND_READER"
UNPROMOTED_SUBJECT = "UNPROMOTED_SUBJECT"

REASONS = {
    CROSS_ITEM_RECALL: (
        "the fact belongs to another Item and was never promoted. Repair: open "
        "a thread bound to that Item, or promote the fact if it is genuinely "
        "project-wide"),
    UNBOUND_READER: (
        "the reader names no Item, so only promoted facts are open to it. "
        "Repair: bind the thread to the Item it is working on"),
    UNPROMOTED_SUBJECT: (
        "the subject is not on the Item axis (a `device:` fact) and was never "
        "promoted, so no scope names an audience for it. Repair: promote it, "
        "or address it to a device with a handoff"),
}

# `project/<project>/<Item>`. Mirrored from `record.SUBJECT_RE` rather than
# imported: this module is loaded BY `record.py`, so the load direction forbids
# the import, and the second transcription is what the contract test checks
# instead of checking `record.py` against itself.
SUBJECT_RE = re.compile(
    r"^(?:device:[a-z0-9][a-z0-9._-]*|[A-Za-z0-9][\w.-]*/[\w.-]+/[\w.-]+)$")
_ITEM_TOKEN_RE = re.compile(r"^[A-Za-z0-9][\w.-]*$")


class ScopeRefused(Exception):
    """A scope value or a reader is not one this contract declares."""


class RecallRefused(Exception):
    """A read was refused, and the refusal names which read and why.

    Raised rather than returned as an empty list. An empty list is a legal
    answer — it means nothing is known about the subject — and a refusal
    arriving in that shape tells a caller the ledger is empty about an Item it
    is not allowed to see. The two call for opposite responses, so they cannot
    share a representation.
    """

    def __init__(self, subject: str, reader: dict, refusals: list[dict]) -> None:
        self.subject = subject
        self.reader = dict(reader or {})
        self.refusals = list(refusals)
        self.reason = refusals[0]["reason"] if refusals else UNPROMOTED_SUBJECT
        # The repair, on the exception as well as in the message. A caller that
        # reports this to a person needs both halves, and a caller that
        # re-implements the lookup is a second place the vocabulary is written.
        self.why = REASONS.get(self.reason)
        # The FIRST reason, and refusals agree in practice because the scope
        # that decides this is a property of the subject. When they do not
        # agree, the ids are here too, so nothing hides behind the summary.
        ids = ", ".join(str(r.get("id")) for r in self.refusals)
        super().__init__(
            f"{subject} was not recalled for {describe(reader)}: "
            f"{self.reason} — {REASONS.get(self.reason, '')} "
            f"(refused {len(self.refusals)} record(s): {ids})")


def describe(reader: dict) -> str:
    """A reader in words, for a message. Never a decision."""
    kind = (reader or {}).get("kind")
    if kind == OPERATOR:
        return "an operator"
    item = (reader or {}).get("item_ref")
    return f"a thread bound to {item}" if item else "an unbound thread"


def _axis(subject: str) -> tuple[str, str] | None:
    """`(project, Item)` for a subject on the Item axis, else None.

    A `device:<id>` subject has no slash at all, which is why the length check
    is the whole test and why it does not need a second one for the prefix.
    """
    parts = str(subject or "").split("/")
    if len(parts) != 3 or not parts[1] or not parts[2]:
        return None
    return parts[1], parts[2]


def project_of(subject: str) -> str | None:
    """The project a subject belongs to, or None when it has no Item axis."""
    axis = _axis(subject)
    return axis[0] if axis else None


def item_of(subject: str) -> str | None:
    """The Item a subject belongs to, or None when it has no Item axis."""
    axis = _axis(subject)
    return axis[1] if axis else None


def scoped_items(records: list[dict]) -> dict[str, set[str]]:
    """Item -> the projects the ledger records it under.

    The reader's project is resolved from this and not from the node's live
    state, because this module is pure. It is also why it can be wrong in a
    way worth naming: a ledger that only ever mentions `LIT-0101` under
    `litellm` resolves unambiguously, and one that mentions it under two
    projects resolves to neither.
    """
    out: dict[str, set[str]] = {}
    for record in records or []:
        if not isinstance(record, dict):
            continue
        axis = _axis(record.get("subject"))
        if axis:
            out.setdefault(axis[1], set()).add(axis[0])
    return out


def scope_of(record: dict) -> str:
    """A record's scope, defaulting to `item`.

    The default is the narrow one, and it is a default rather than a required
    field for a reason that is the opposite of §17's. A record written before
    this module existed is `item`-scoped, and it stays recallable exactly where
    it always was. Requiring the field would not have made anything safer; it
    would have made every existing record unreadable until somebody restated
    what was already true. §17's `session_role` has no default because nothing
    was there before it and any value would be an invention; here the value is
    already implicit and the default names it.
    """
    value = record.get("scope")
    if value is None:
        return DEFAULT_SCOPE
    if value not in SCOPES:
        raise ScopeRefused(
            f"scope {value!r} is not one of {', '.join(SCOPES)}; an "
            "unrecognised scope is refused rather than treated as the default, "
            "because a typo'd promotion would otherwise be a silent demotion")
    return value


def thread(item_ref: str, *, session_ref: str | None = None,
           project_ref: str | None = None) -> dict:
    """A reader that is a session with a purpose. `item_ref` is required.

    `item_ref` is a bare Item id — the same token §17 freezes in
    `bound_item_ref` and that `context/resolver.py#item_state` queries
    `items_index` with. A caller that already knows the project may write it
    as `project/<project>/<Item>` or pass `project_ref`; a caller that does
    not may leave it out, and `recall()` will resolve it from the ledger it was
    handed rather than from a clock or a state root.
    """
    token = str(item_ref or "").strip()
    if token.startswith("item:"):
        token = token[len("item:"):]
    if token.count("/") == 2:
        parts = token.split("/")
        if parts[1]:
            project_ref = project_ref or parts[1]
        token = parts[2]
    if not _ITEM_TOKEN_RE.match(token):
        raise ScopeRefused(
            f"{item_ref!r} is not an Item ref. A thread's reader names the Item "
            "it is bound to (`LIT-0101`, `item:LIT-0101`, or "
            "`project/litellm/LIT-0101`); `operator` is the other value, for a "
            "person reading the ledger directly")
    return {"kind": THREAD, "item_ref": token, "session_ref": session_ref,
            "project_ref": project_ref or None}


OPERATOR_READING = {"kind": OPERATOR, "item_ref": None, "session_ref": None,
                    "project_ref": None}


def parse_reader(text: str) -> dict:
    """`operator`, or an Item ref, into a reader. There is no third value."""
    if str(text or "").strip() == OPERATOR_WORD:
        return dict(OPERATOR_READING)
    return thread(text)


def _reader_with_project(reader: dict, records: list[dict]) -> dict:
    """The reader, with `project_ref` filled in from the ledger when it can be.

    Only ever fills a gap. A reader that stated its project keeps it, so the
    answer never depends on which ledger the call happened to be given.
    """
    if reader.get("kind") != THREAD or reader.get("project_ref"):
        return reader
    known = scoped_items(records).get(str(reader.get("item_ref")), set())
    if len(known) != 1:
        return reader
    return {**reader, "project_ref": next(iter(known))}


def verdict(record: dict, reader: dict) -> dict:
    """May this record be recalled by this reader? Refusal is an answer here.

    `verdict` returns a refusal; `require` raises one. The split is what lets
    `recall()` report every record it considered while a caller who would
    otherwise read an empty list as a real answer is stopped.
    """
    scope = scope_of(record)
    kind = (reader or {}).get("kind")
    if kind not in READER_KINDS:
        raise ScopeRefused(
            f"{kind!r} is not a reader kind; a reader is one of "
            f"{', '.join(READER_KINDS)}, and a reader this module cannot name "
            "is a reader whose admission cannot be stated")
    if kind == OPERATOR:
        return _admits(scope, None, None)
    if scope == USER:
        # Promoted to the node. The promotion is the permission, and it
        # carries the name of whoever made it.
        return _admits(scope, None, None)
    item = (reader or {}).get("item_ref")
    if not item:
        return _refuses(scope, UNBOUND_READER)
    subject_item = item_of(record.get("subject"))
    if subject_item is None:
        return _refuses(
            scope, UNPROMOTED_SUBJECT,
            detail=("a `scope: project` on a subject that names no project. "
                    "`validate()` refuses this record at admission; reaching "
                    "here means it was read without being validated")
            if scope == PROJECT else None)
    if subject_item == item:
        return _admits(scope, None, None)
    if scope == PROJECT:
        subject_project = project_of(record.get("subject"))
        reader_project = (reader or {}).get("project_ref")
        if reader_project and reader_project == subject_project:
            return _admits(scope, None, None)
        return _refuses(
            scope, CROSS_ITEM_RECALL,
            detail=None if reader_project else
            f"the reader did not name a project and this ledger does not place "
            f"{item} under exactly one; write the reader as "
            f"project/{subject_project}/{item} if {subject_project} is where "
            f"it works")
    return _refuses(scope, CROSS_ITEM_RECALL)


def _admits(scope: str, reason, detail) -> dict:
    return {"admitted": True, "scope": scope, "reason": reason,
            "why": None, "detail": detail}


def _refuses(scope: str, reason: str, *, detail=None) -> dict:
    return {"admitted": False, "scope": scope, "reason": reason,
            "why": REASONS[reason], "detail": detail}


def recall(records: list[dict], subject: str, *, reader: dict) -> dict:
    """Which of this subject's records this reader may have, and which not.

    `records` is the WINDOW the caller chose — a subject and a moment, filtered
    by whoever holds the clock. Time is not decided here: `at` is the ledger's
    ordering and belongs to `record.py`, the one place that knows a record's
    `at` sorts as when a fact became true and not as when it was written. A
    scope kernel that also resolved `at` would be a second answer to "as of
    when".
    """
    reader = _reader_with_project(dict(reader or {}), records)
    admitted, refused = [], []
    for record in records or []:
        if not isinstance(record, dict) or record.get("subject") != subject:
            continue
        answer = verdict(record, reader)
        entry = {"id": record.get("id"), "scope": answer["scope"],
                 "reason": answer["reason"], "why": answer["why"],
                 "detail": answer["detail"]}
        if answer["admitted"]:
            admitted.append(record)
        else:
            refused.append(entry)
    return {
        "schema": SCHEMA,
        "subject": subject,
        "reader": reader,
        "considered": len(admitted) + len(refused),
        "admitted": [r.get("id") for r in admitted],
        "refused": refused,
        "recalled": bool(admitted),
    }


def require(records: list[dict], subject: str, *, reader: dict) -> list[dict]:
    """The admitted records, or `RecallRefused`.

    Refuses only when NOTHING under the subject is admissible. A subject is not
    all-or-nothing in principle — a promoted record and an Item-scoped one can
    sit under one subject — and there the reader gets what it is entitled to
    and the rest is simply not returned, which is the ordinary meaning of a
    scope. What cannot be ordinary is a subject closed to the reader entirely:
    that is the cross-Item read this firewall exists to refuse, and it must not
    arrive looking like an empty ledger.
    """
    reading = recall(records, subject, reader=dict(reader or {}))
    if not reading["recalled"] and reading["refused"]:
        raise RecallRefused(subject, reading["reader"], reading["refused"])
    return _admitted_records(records, subject, reading)


def _admitted_records(records: list[dict], subject: str, reading: dict) -> list[dict]:
    """The admitted records themselves, in ledger order.

    Re-derived from the ids rather than carried on the reading, so that the
    document `recall()` returns stays a report about ids and pointers — the
    same choice `projection.py` makes when it cites a record rather than
    copying it.
    """
    wanted = set(reading["admitted"])
    return [r for r in records or []
            if isinstance(r, dict) and r.get("subject") == subject
            and r.get("id") in wanted]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("ledger", nargs="+", type=Path)
    parser.add_argument("--subject", required=True)
    parser.add_argument("--as", dest="reader", required=True,
                        help="`operator`, or the Item ref a thread is bound to")
    args = parser.parse_args(argv)

    name = "serveros_memory_record"
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).resolve().parent / "record.py")
    record_mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = record_mod
    spec.loader.exec_module(record_mod)

    try:
        reader = parse_reader(args.reader)
    except ScopeRefused as exc:
        parser.error(str(exc))

    records = record_mod.load(args.ledger)
    reading = recall(records, args.subject, reader=reader)
    json.dump(reading, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0 if reading["recalled"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
