#!/usr/bin/env python3
"""§59 — the cross-device round trip, over the Git ledger.

    from handoff import checkpoint, handoff, pending, answers, round_trip
    python3 handoff.py --ledger <files...> --device device:work-fj

Why a module and not just a fixture
-----------------------------------
§59 asks for a fixture in which Beijing hands work to another device and
receives a result back. The fixture is the easy half. The half that decides
whether the round trip works at all is a single question:

    given a ledger and a device, which handoffs are addressed to that device
    and have not been answered?

Nothing in the ledger answered it before this file. `record.py` can say what a
subject currently says (`current_value`) and what stands unchallenged (`heads`),
and neither is the same question: a handoff is addressed to a device that is
not the one reading, and it stays open until a DIFFERENT device writes a
checkpoint that names it.

The four steps, and where each one is decided
---------------------------------------------
    Beijing   creates checkpoint + handoff   `checkpoint()`, `handoff()`
    FJ/Dell   reads the same handoff         `pending(records, device_ref)`
    Windows   writes the result checkpoint   `result()`
    Beijing   retrieves the result           `answers(records, handoff_id)`

Each is a pure function of a list of records. There is no clock, no file, no
network and no ordering guarantee: a device that is offline for a day gets the
same answer as one that is online, which is what makes this an *async* handoff
rather than an RPC with a queue bolted on. §59's fallback for an unavailable
remote actuator is exactly this — the ledger is Git, and Git is already the
transport.

What this module deliberately does not do
-----------------------------------------
It does not write to the ledger. A record is authored, reviewed and committed
like any other file; the ledger's substrate being Git is the contract's choice
and it is load-bearing ("the ledger inherits blame, diff, revert"). So
`checkpoint()` and `handoff()` RETURN a record for an author to write, rather
than appending one — the same reason `record.validate()` returns the record it
was given instead of a normalized copy.

It does not order handoffs by wall clock. `at` is when the fact became true,
which is the only ordering the contract admits, and two devices that disagree
about the time still agree about the ledger.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _record_module():
    """`memory/record.py`, loaded by path — the idiom used by `projection.py`.

    A bare `import record` would work only when the caller happened to have
    this directory on `sys.path`, which is to say it would work from the CLI
    and fail from a test, or the other way round.
    """
    name = "serveros_memory_record"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _HERE / "record.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


SCHEMA = "serveros/memory-handoff.v1"

# The two states a handoff can be in, and there is no third. A handoff with no
# answering checkpoint is OPEN; one with an answering checkpoint is ANSWERED.
# "EXPIRED" is deliberately absent: the ledger has no clock and no session, so
# a handoff nobody picked up is not a failure — it is work waiting, which is
# the normal state of a fleet where the two devices are rarely awake together.
OPEN = "OPEN"
ANSWERED = "ANSWERED"


class HandoffRefused(Exception):
    """A checkpoint or handoff could not be constructed as asked."""


def _stamp(*, subject: str, at: str, by: str, source: str, revision: str) -> dict:
    """The fields every record carries. One place, so the constructors below
    cannot drift from the schema by forgetting one."""
    return {"subject": subject, "at": at, "by": by, "source": source,
            "revision": revision}


def checkpoint(record_id: str, *, subject: str, device_ref: str, at: str, by: str,
               source: str, revision: str, body: str, supersedes: str | None = None,
               related=None, note: str | None = None) -> dict:
    """Where the work stopped, written so another device can continue it.

    `body` is required and `supersedes` is not for a reason: a checkpoint
    describes a MOVING state, so a chain of them is a history of the work
    rather than a set of competing claims, and the newest one is simply the
    current one. That is the opposite of `decision`, where two unchallenged
    records are two live answers and `heads()` reports the count.

    The `body` is mandatory because an empty checkpoint resumes nothing. The
    test the contract states for the field is whether a different device can
    continue from it alone, and a checkpoint with no body fails that test while
    still passing every structural check — which is the shape of defect this
    round keeps finding.
    """
    if not body or not str(body).strip():
        raise HandoffRefused(
            f"{record_id}: a checkpoint with no body tells the next device that "
            "work stopped and nothing else. It passes every structural check "
            "and resumes nothing")
    record = {
        "id": record_id,
        "kind": "checkpoint",
        **_stamp(subject=subject, at=at, by=by, source=source, revision=revision),
        "device_ref": device_ref,
        "body": body,
    }
    if supersedes:
        record["supersedes"] = supersedes
    if related:
        record["related"] = list(related)
    if note:
        record["note"] = note
    return _record_module().validate(record)


def handoff(record_id: str, *, subject: str, to_device_ref: str, device_ref: str,
            at: str, by: str, source: str, revision: str, body: str,
            related=None, note: str | None = None) -> dict:
    """The work moved, and this is the device it moved to.

    `to_device_ref` is what makes the record findable by the party it is for.
    A handoff without one is a note to self, and `validate` refuses it rather
    than letting it sit in the ledger looking like a message that was sent.

    `device_ref` here is the SENDER — where the work came from. The two are not
    derivable from each other and neither field defaults to the other, because
    a default would silently produce a handoff a device sent to itself.
    """
    if device_ref == to_device_ref:
        raise HandoffRefused(
            f"{record_id}: {device_ref} cannot hand work to itself. A handoff is "
            "what crosses a device boundary; within one device the ledger already "
            "has the record, and a self-addressed handoff would appear in that "
            "device's inbox forever")
    record = {
        "id": record_id,
        "kind": "handoff",
        **_stamp(subject=subject, at=at, by=by, source=source, revision=revision),
        "device_ref": device_ref,
        "to_device_ref": to_device_ref,
        "body": body,
    }
    if related:
        record["related"] = list(related)
    if note:
        record["note"] = note
    return _record_module().validate(record)


def result(record_id: str, *, subject: str, device_ref: str, at: str, by: str,
           source: str, revision: str, body: str, handoff_id: str,
           note: str | None = None) -> dict:
    """The answering checkpoint — step three, and the step with a link in it.

    It is a `checkpoint`, not a new kind: what comes back is work state, which
    is what a checkpoint is. What makes it an ANSWER rather than just the next
    checkpoint is `related`, which names the handoff. That link is the whole
    difference between "the ledger has another checkpoint in it" and "the
    handoff I sent has been answered", and only the second one closes an inbox
    entry.
    """
    return checkpoint(record_id, subject=subject, device_ref=device_ref, at=at,
                      by=by, source=source, revision=revision, body=body,
                      related=[handoff_id], note=note)


# ---------------------------------------------------------------------------
# Reading side
# ---------------------------------------------------------------------------

def _answerers(records: list[dict], *, at: str | None = None) -> dict[str, list[str]]:
    """handoff id → the ids of checkpoints that name it in `related`.

    `related` is a list, so one checkpoint may answer several handoffs and one
    handoff may be answered more than once. The second case is not an error —
    two devices picking up the same handoff is a thing that happens in a fleet
    — and this returns every answer rather than the first, so a caller can see
    that it happened instead of being handed a choice made on its behalf.

    `at` bounds the window by when the answering checkpoint became true, the
    same idiom as `record.heads(at=...)`. It is not a convenience: §59's second
    step — the receiving device reading its inbox — happens BEFORE the third,
    and without a bound the only way to ask that question is to hand the
    function a ledger with the answer deleted from it. A reader that has to
    edit the ledger to ask a question about it will eventually ask the wrong
    one.
    """
    found: dict[str, list[str]] = {}
    for record in records:
        if not isinstance(record, dict) or record.get("kind") != "checkpoint":
            continue
        if at is not None and (_record_module().canonical_at(record.get("at")) or "") > at:
            continue
        related = record.get("related")
        for one in (related if isinstance(related, list) else [related]):
            if isinstance(one, str):
                found.setdefault(one, []).append(record.get("id"))
    return found


def _handoffs(records: list[dict]) -> list[dict]:
    return [r for r in records
            if isinstance(r, dict) and r.get("kind") == "handoff"]


def answers(records: list[dict], handoff_id: str, *, at: str | None = None) -> list[dict]:
    """The checkpoints that answer one handoff, in the order they became true.

    Empty means the handoff is still open. That is the same answer as "the
    answering device has not been awake yet", and this function does not
    distinguish them, because the ledger cannot and guessing would invent a
    fact about a device this one cannot see.
    """
    ids = set(_answerers(records, at=at).get(handoff_id, ()))
    by_id = {r.get("id"): r for r in records if isinstance(r, dict)}
    return sorted((by_id[i] for i in ids if i in by_id),
                  key=lambda r: _record_module().canonical_at(r.get("at")) or "")


def pending(records: list[dict], device_ref: str, *, at: str | None = None) -> dict:
    """What is addressed to this device and unresolved, as of `at`.

    The receiving side of §59, and the only question the fixture cannot be
    written without. Note what it is NOT: it is not `current_value(subject)`.
    A device's inbox holds records about subjects it has never seen, sent by a
    device it may never have contacted, and asking "what does this subject
    currently say" returns the newest record — which is the handoff the READER
    sent, not the one addressed to it.

    `at` is what makes the inbox readable at a past moment. Asked without it,
    the answer is the present, and a handoff that was answered three hours ago
    has left the inbox — correctly, but it means the only way to observe the
    receiving device finding its work is to read the ledger before the answer
    is written to it.
    """
    answered = _answerers(records, at=at)
    open_ones, done = [], []
    for record in _handoffs(records):
        entry = {
            "id": record.get("id"),
            "subject": record.get("subject"),
            "from": record.get("device_ref"),
            "at": _record_module().canonical_at(record.get("at")),
            "body": record.get("body"),
        }
        if record.get("to_device_ref") != device_ref:
            continue
        # An open handoff names itself once per answering checkpoint. The
        # inbox entry says how many answers there are rather than which, so a
        # device that has been answered twice can notice.
        if record.get("id") in answered:
            done.append({**entry, "answer_count": len(answered[record["id"]]),
                         "state": ANSWERED})
        else:
            open_ones.append({**entry, "answer_count": 0, "state": OPEN})
    key = lambda e: (e["at"] or "", e["id"] or "")
    return {
        "schema": SCHEMA,
        "device_ref": device_ref,
        "as_of": at,
        "open": sorted(open_ones, key=key),
        "answered": sorted(done, key=key),
        "counts": {OPEN: len(open_ones), ANSWERED: len(done)},
    }


def round_trip(records: list[dict], *, at: str | None = None) -> dict:
    """Every handoff in the ledger and where it stands, for both ends.

    §59's fixture asserts on this because the interesting property is not that
    one device can read its inbox — it is that the SENDER can, without asking
    anyone, determine that its handoff was picked up. That is what makes the
    handoff async: the sender does not wait, does not poll a service, and does
    not need the receiving device to be up at any point it is.
    """
    answered = _answerers(records, at=at)
    rows = []
    for record in sorted(_handoffs(records),
                         key=lambda r: (_record_module().canonical_at(r.get("at")) or "")):
        answers_here = answers(records, record["id"], at=at)
        rows.append({
            "id": record.get("id"),
            "subject": record.get("subject"),
            "from": record.get("device_ref"),
            "to": record.get("to_device_ref"),
            "at": _record_module().canonical_at(record.get("at")),
            "state": ANSWERED if record["id"] in answered else OPEN,
            "answers": [{"id": a.get("id"), "device_ref": a.get("device_ref"),
                         "at": _record_module().canonical_at(a.get("at"))}
                        for a in answers_here],
        })
    return {
        "schema": SCHEMA,
        "as_of": at,
        "handoffs": rows,
        "counts": {OPEN: sum(1 for r in rows if r["state"] == OPEN),
                   ANSWERED: sum(1 for r in rows if r["state"] == ANSWERED)},
        "note": (
            "state is decided by the presence of an answering checkpoint that "
            "names this handoff in `related`, and by nothing else. A handoff "
            "that is still OPEN is work waiting, not a failure: the two devices "
            "in a fleet are rarely awake together, and the ledger is what makes "
            "that survivable"),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("ledger", nargs="+", type=Path)
    parser.add_argument("--device", help="print this device's inbox")
    parser.add_argument("--round-trip", action="store_true",
                        help="print every handoff and whether it was answered")
    parser.add_argument("--at", help="ISO-8601 UTC; read the ledger as of then. "
                                     "A handoff answered since then is open at "
                                     "that moment, which is when the receiving "
                                     "device would have seen it")
    args = parser.parse_args(argv)

    record_mod = _record_module()
    records = record_mod.load(args.ledger)

    if args.device:
        result_doc = pending(records, args.device, at=args.at)
    else:
        result_doc = round_trip(records, at=args.at)
    json.dump(result_doc, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
