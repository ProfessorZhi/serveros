"""The board a producer renders, read as the rows the daily profile selects from.

WHY THIS MODULE EXISTS
----------------------
`intake/daily_cycle.select_focus` is written against a row shape carrying
`worker_stopped`, `resources_released`, `focus_disposition`,
`parking_acknowledged`, `scope_digest`, `grant_ref`, `next_action` and
`blocked_by`. Read on 2026-09-22, **no producer on this node emits any of
them**. The two halves had therefore never been in the same room: the policy
had only its own tests, and those tests supply rows from a factory that
hard-codes every one of those fields, so the gap was invisible from both ends.
Pointed at the real 17-row personal board, the policy reported all 17 rows as
unresolved work — 7 CLOSED and 4 DELIVERED among them.

This module is the one place the policy and a real projection meet. It reads
the personal board (`serverosctl work board --json`, composed by
`intake/workchain.py` from `intake/workchain-contract.yaml#personal_board`) and
states field by field what the board answers and what it does not.

WHAT IT REFUSES TO DO
---------------------
It invents nothing. `PRODUCED` maps every key it emits to the producer field
the value came from, and `compose` raises if a row would carry a key outside
it, so a field added here without a producer is a failure rather than a
plausible-looking default. The fields the policy needs and the board does not
have are reported in `not_produced` with the reason, and they are *absent* from
the rows rather than present-and-empty — `daily_cycle.liveness` reads an absent
field as `UNEVALUABLE`, which is what a silent projection deserves.

WHAT CHANGED ON 2026-09-25, AND WHAT DID NOT
--------------------------------------------
`worker_stopped` now has a producer, and it is NOT the board. The field's
correct owner is the process, and the only record of a process in this fabric
is the receipt `runtime/bounded_invocation.invoke_once` writes — so `compose`
walks the chain Item → the admission naming it (`selection.selected`) → that
admission's `action_key` → `<receipts>/<action_key>.json` → `worker_stopped`,
and puts the receipt's own answer on the row. The board is still asked for
nothing it does not carry: the rows come from `work board` exactly as before,
and the liveness half is a second, separately named source in the document
(`liveness`), whose every link is reported whether it held or broke.

`resources_released`, the field that reads as its pair, has a producer too, and
it is a different one: the resource-lease namespace
(`state/serveros/resource-leases/`, produced by `runtime/lease_store.py`, read
through `runtime/environment.effective`). Its join to an Item is the contract's
own `work-item` scope, and `resources_released_by_item` says why that and not a
name match. What stands between the two fields is NOT one gap measured twice —
it was, until 2026-09-25, and this paragraph said so: "still has no producer
anywhere — not even on that receipt". That sentence was wrong about the lease
store, which records a reference being retired every time one is retired. It is
kept here as the correction rather than deleted, because the shape it had is the
shape to watch for: one sentence covering two fields, written from the field
that had no source and applied to the pair.

Two derivations it declines, both because a nearer-looking answer would be a
wronger one:

  * **Run state is not worker liveness.** The board row carries `runs` with
    their states, so `worker_stopped` could be synthesized as "no Run is
    RUNNING". A Run recorded as finished is a database row; whether the process
    it names has stopped is a different fact, and `docs/daily-work-cycle.md`
    §11 is explicit that after `STARTED` the actual effect must be checked
    rather than assumed. Synthesizing the field would put that assumption in
    the one place that is supposed to be evidence. This is still the rule after
    the receipt was wired: a board row whose Run is terminal and whose receipt
    cannot say the worker stopped stays HELD, which is the case
    `daily_cycle.classify` orders its branches for — and until the receipt was
    read, that branch could not be reached on this node at all.
  * **The projection's age is not the render's age.** `work board` composes
    live, so its own `at` is this second; the `current.json` files it reads were
    last written on 2026-09-10. Freshness is a property of the facts, so
    `observed_at` is the OLDEST underlying projection and not the composition
    time. `read_projections` reads those times; composing a 12-day-old
    projection and calling it fresh is the failure this avoids.
"""
from __future__ import annotations

import json
from pathlib import Path

# Every key this module may put on a focus row, and the producer field it comes
# from. A value of `None` in the producer column would mean a key with no
# producer, which is why there is no such entry: `compose` raises on a row key
# that is not here, so this table is the check rather than a comment about one.
PRODUCED = {
    "grant_ref": "unique verified grant naming this Item, joined by control_join",
    "scope_digest": "digest of this Item's current accepted card scope, independent of grant",
    "next_action": "unique READY Activity kind from the Work Engine projection",
    "id": "board row `id` (canonical Item id, work-engine's own)",
    "project": "board row `project`",
    "state": "board row `state` (work-engine ItemState)",
    "title": "board row `title` — for a person reading `work focus`; no policy reads it",
    "kind": "board row `kind` — same",
    "priority": "board row `priority.personal` then `priority.source`, and only when "
                "the card declared a literal level. UNPRODUCED stays absent; see "
                "`priority_level`.",
    "completion": "board row `completion`, put there by `workchain.completion_of` "
                  "from the contract's boundary and the row's own documents. Read, "
                  "not re-derived: the daily cycle asks whether an Item still owes "
                  "something, and a second derivation here would be a second answer",
    "worker_stopped": "the `worker_stopped` field of the receipt "
                      "`runtime/bounded_invocation.invoke_once` wrote for this "
                      "Item's admission, reached by `worker_stopped_by_item` "
                      "below. Present only when the whole chain held; a receipt "
                      "that exists but cannot claim the worker stopped is `False`, "
                      "which is what keeps a terminal Run from releasing. See "
                      "`liveness` in the composed document for every link",
    "resources_released": "the `state` of every reference the resource-lease "
                          "namespace holds for this Item under the contract's "
                          "`work-item` scope, reached by "
                          "`resources_released_by_item` below. Present only when "
                          "the namespace was read; a live or an EXPIRED reference "
                          "is `False`, and EXPIRED is `False` on the store's own "
                          "reasoning about an unbacked claim. It is NOT `not "
                          "worker_stopped`: a cycle can have stopped its process "
                          "and still be holding a lease, which is why the two "
                          "fields have two readers and two reasons",
}

# The policy's inputs the board does not carry. Each is stated here rather than
# left for a reader to notice, because the absence is what made the policy
# unpointable and a reader of the composed rows cannot tell a field the fabric
# has not built from one this adapter forgot.
NOT_PRODUCED = {
    "focus_disposition": "the field that would mark an Item PARKED is part of the "
                         "same proposed card extension as the priorities",
    "parking_acknowledged": "same",
    "blocked_by": "the board carries no dependency edge",
}

# Facts the policy needs that are absent for a reason outside this adapter.
NOT_PRODUCED_ELSEWHERE = {
    "grants": "`select_focus(grants=...)` takes the envelope "
              "`context/delegation.py` derives from the delegations recorded in "
              "`state/serveros/runtime/agent-control/store.json`. A row's "
              "`grant_ref` is not a field anything authors, so it cannot be a "
              "field anything failed to build: `control_join` below DERIVES it, "
              "naming a grant exactly when one authorizing delegation in that "
              "envelope covers both the row's Item and its project, and reporting "
              "every row it could not name in the `control_join` list's own "
              "`gaps`. What a reader sees when no row is named is therefore one "
              "absence and not two: the envelope is empty because no recorded "
              "delegation authorizes, which is a fact about the store and is "
              "reported beside this line as the recorded and authorizing counts. "
              "An earlier version of this text called the unnamed row a second, "
              "independent gap — a card field nobody built — and a reader "
              "repairing that would have gone looking for a producer that does "
              "not exist; `tests/test_focus_source.py` pins the direction now.",
    "current_priority": "`intake/workchain-contract.yaml#card_extension` proposes "
                        "`source_priority`/`personal_priority` and is "
                        "PROPOSED_NOT_APPROVED; no card carries either, so every "
                        "live row reads PRIORITY_NOT_ESTABLISHED.",
}

PRIORITY_LEVELS = ("P0", "P1", "P2", "P3", "P4")


class Refused(RuntimeError):
    """The board cannot be read as focus rows, and guessing is not an option."""


def priority_level(row: dict) -> tuple[str | None, dict]:
    """The declared level, or `None` with the board's own words for why not.

    The board publishes `priority` as a two-cell structure — `personal` and
    `source` — each either a level or the state `UNPRODUCED` with a `why`. This
    returns the level only when a cell is a literal level, and otherwise
    `None` plus that `why`, so the reason travels with the absence instead of
    being replaced by one of this module's own.
    """
    cell = row.get("priority")
    if not isinstance(cell, dict):
        return None, {"state": "NOT_A_PRIORITY_CELL",
                      "why": f"the row's `priority` is {type(cell).__name__}, not the "
                             f"board's `{{personal, source}}` structure"}
    if not any(name in cell for name in ("personal", "source")):
        return None, {"state": "NOT_A_PRIORITY_CELL",
                      "why": f"the row's `priority` object has neither the "
                             f"`personal` nor the `source` cell: {sorted(cell)}"}
    for name in ("personal", "source"):
        part = cell.get(name)
        if part is not None and not isinstance(part, dict):
            return None, {"state": "NOT_A_PRIORITY_CELL",
                          "why": f"the row's `priority.{name}` is "
                                 f"{type(part).__name__}, not the board's cell"}
        if isinstance(part, dict) and part.get("state") in PRIORITY_LEVELS:
            return part["state"], {"state": "PRODUCED", "cell": name}
    reasons = {name: (cell.get(name) or {}).get("why", "")
               for name in ("personal", "source")}
    return None, {"state": "UNPRODUCED", "why": " | ".join(
        f"{name}: {text}" for name, text in reasons.items() if text)}


def rows_from_board(board: dict) -> list[dict]:
    """One focus row per board row, carrying only what the producer answered."""
    composed = board.get("sections")
    if not isinstance(composed, list):
        raise Refused("not a personal board: no `sections` list")
    rows: list[dict] = []
    for section in composed:
        for row in section.get("rows", []):
            for field in ("id", "project", "state"):
                if not row.get(field):
                    raise Refused(
                        f"a board row has no `{field}` ({sorted(row)[:8]}…); the "
                        f"canonical id is what a selection is recorded against, "
                        f"and a row without one cannot be selected or reported")
            item = {k: row[k] for k in ("id", "project", "state", "title", "kind")
                    if row.get(k) is not None}
            level, _why = priority_level(row)
            if level is not None:
                item["priority"] = level
            # Absent when the board cannot say, and absent is a third answer
            # rather than a favourable one — see `classify` on this node.
            if isinstance(row.get("completion"), dict):
                item["completion"] = row["completion"]
            rows.append(item)
    _check_declared(rows)
    return rows


def _check_declared(rows: list[dict]) -> None:
    """`PRODUCED` is the one place that says what a row may carry.

    ONE function, called from `rows_from_board` AND from `compose` after the
    liveness merge. Two copies of this rule is the shape this tree's ledger
    records as unkillable-by-mutation: the copy that drifts is the one nobody
    reads, and the second copy would be the one guarding the keys added after
    the first check had already run.
    """
    undeclared = {row.get("id"): sorted(set(row) - set(PRODUCED))
                  for row in rows if set(row) - set(PRODUCED)}
    if undeclared:
        raise Refused(
            f"this adapter would emit {undeclared}, which `PRODUCED` does not name "
            f"a producer for; add the producer or drop the field")


def read_projections(projects: list[dict]) -> dict:
    """Each project's own `generated_at`, keyed by project — the module's only IO.

    An unreadable or undated projection is reported as `None` rather than
    omitted, because a project whose age is unknown makes the whole
    composition's age unknown, and a caller that treats an absent key as
    "fine" would be reading silence as freshness.
    """
    times: dict[str, str | None] = {}
    for entry in projects or []:
        name = entry.get("project") or entry.get("path") or "?"
        path = entry.get("path")
        try:
            document = json.loads(Path(path).read_text(encoding="utf-8"))
            times[name] = document.get("generated_at")
        except (OSError, ValueError, TypeError):
            times[name] = None
    return times


def oldest(times: dict) -> str | None:
    """The earliest of the projections' ages, compared as instants, or `None`.

    Compared as parsed instants and not as strings, because the projections are
    written by different runs and an offset other than the first one's would
    make a lexicographic minimum pick the wrong one — `2026-09-10T07:28+00:00`
    sorts before `2026-09-10T15:00+08:00` and names an earlier instant, and the
    pair only has to differ once for the comparison to be silently inverted.
    """
    parsed: list[tuple[object, str]] = []
    for name, stamp in (times or {}).items():
        if not stamp:
            return None
        try:
            when = instant_time(stamp)
        except ValueError:
            raise Refused(
                f"projection `{name}` has generated_at {stamp!r}, which is not a "
                f"timestamp with an offset; an age that cannot be read is not an "
                f"age, and reading it as one would report a stale board as fresh")
        parsed.append((when, stamp))
    if not parsed:
        return None
    return min(parsed, key=lambda pair: pair[0])[1]


def instant_time(value: str):
    """`daily_cycle.instant`'s rule, kept local so the adapter stays loadable alone."""
    from datetime import datetime
    when = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if when.tzinfo is None:
        raise ValueError("timestamp requires an explicit timezone")
    return when


#: The chain a liveness answer travels, in order, and every link it can break at.
#: Kept as data because each link's absence is a DIFFERENT answer, and the single
#: sentence this replaces ("the board carries no such field") was true of all of
#: them while pointing at none of them.
LIVENESS_CHAIN = (
    "an entry in `state/serveros/runtime/agent-control/store.json#admissions` "
    "whose `selection.selected` names the Item",
    "that entry's `action_key`",
    "`<receipts>/<action_key>.json`, written by "
    "`runtime/bounded_invocation.invoke_once`",
)

#: What each of that writer's `status` values means for whether anything is still
#: held, read off the writer rather than off the field name. The table is
#: exhaustive over the statuses `invoke_once` can write, and it exists because
#: `worker_stopped` on a receipt is NOT a general-purpose answer: the record is
#: created with `worker_stopped: False` before the spawn and only ever set to
#: `True` on the `START_FAILED` path where no process was started at all. On
#: every other path `False` is the value it was initialized with, and reading an
#: initializer as a verdict is how a receipt that says "I cannot claim the
#: worker stopped" becomes "the worker stopped".
#:
#:   True   nothing was started here, so nothing can still be running
#:   False  a process was started and this receipt does not record it gone, or
#:          records it gone while saying the effect needs reconciliation
#:   None   the direct process exited and descendant termination was never
#:          verified, or nothing was started by this call. Undecidable HERE, so
#:          the field is left off the row and `daily_cycle` decides by the
#:          Item's state and reports that it did
#:
#: One row per status, so each of the three answers above is reachable by a
#: receipt the writer really produces. `STARTED` is the record as first written,
#: which is also what a call killed mid-flight leaves behind: the process was
#: started and nothing has recorded it gone, which is `False`. `START_FAILED`
#: reaches its row only for a receipt that does NOT claim `worker_stopped`,
#: which its own writer always sets on that path — so the row answers a receipt
#: that contradicts its writer, and it answers `False` as every other
#: unexplainable receipt in this function is answered, rather than `True`, which
#: would release on evidence nobody can read. `REFUSED` starts nothing at all
#: and leaves the field off rather than answering, because a refused dispatch
#: means the admission was never carried out and "the worker stopped" is a claim
#: this receipt cannot make.
RECEIPT_LIVENESS = {
    "EXITED": (None, "RECEIPT:EXITED:DESCENDANTS_UNVERIFIED"),
    "TIMED_OUT": (False, "RECEIPT:TIMED_OUT:REQUIRES_RECONCILIATION"),
    "OUTPUT_LIMIT": (False, "RECEIPT:OUTPUT_LIMIT:REQUIRES_RECONCILIATION"),
    "IO_FAILED": (False, "RECEIPT:IO_FAILED:REQUIRES_RECONCILIATION"),
    "START_FAILED": (False, "RECEIPT:START_FAILED:CONTRADICTS_ITS_WRITER"),
    "STARTED": (False, "RECEIPT:STARTED:PROCESS_NOT_RECORDED_GONE"),
    "REFUSED": (None, "RECEIPT:REFUSED:NOTHING_STARTED_BY_THIS_CALL"),
}


def _receipt_liveness(receipt: dict) -> tuple[bool | None, str]:
    """One receipt's answer about its own process, or `None` for undecidable.

    The writer's one positive claim is consulted first, because `worker_stopped`
    is the field being asked about and a receipt that states it is answering this
    question directly. Everything else is `RECEIPT_LIVENESS` reading the
    receipt's own `status`.

    `direct_process_exited` is deliberately NOT consulted as a second
    discriminator, and that is a change: it is `False` on the record as first
    written, so it is `False` for `STARTED`, `START_FAILED` and `REFUSED` alike,
    and a check ordered before the table answered all three with one reason —
    `PROCESS_NOT_RECORDED_GONE`, which is false of a `REFUSED` call that started
    nothing. One fact, one place that reads it.
    """
    if receipt.get("worker_stopped") is True:
        return True, "RECEIPT:NO_PROCESS_STARTED"
    status = str(receipt.get("status") or "")
    if status not in RECEIPT_LIVENESS:
        return None, f"RECEIPT:STATUS_NOT_KNOWN_HERE:{status or 'none'}"
    return RECEIPT_LIVENESS[status]


def worker_stopped_by_item(admissions: dict | None, *, receipts_root, item_ids) -> dict:
    """Whether a worker is still held on each Item, from the receipt that knows.

    THE CORRECT OWNER, and there is no second one. `worker_stopped` is a claim
    about a process, and the only record of a process in this fabric is the
    receipt `runtime/bounded_invocation.invoke_once` writes. The chain is
    `LIVENESS_CHAIN`, walked per Item, and every link that broke is named in that
    Item's `because` rather than folded into one sentence.

    Nothing here is derived from a Run's state, and the module docstring says why
    (a finished Run is a row; whether the process stopped is a different fact).
    What it DOES do is the thing that branch was written for: `False` — a process
    started and this receipt does not record it gone — is what keeps an Item's
    slot occupied after its Run reached a terminal state.

    Undecidable is NOT `False`, and that distinction is the one this function can
    most easily get wrong in a way nobody notices: `daily_cycle.liveness` reads an
    absent field as "the projection is silent about this", and the whole point of
    that third value is that a silent source must not read as a held worker.
    Reading every unreadable receipt as `False` would put every EXITED dispatch
    permanently in flight, which is the OVER_CAPACITY false alarm this fabric has
    already paid for once.

    Returns `{"status", "because", "items"}`. `items` carries one entry per Item
    an admission NAMES; an Item no admission names has no entry, because "nobody
    dispatched anything for this Item" is a different fact from "a dispatch
    cannot say whether its worker stopped", and only one of the two is about a
    worker.
    """
    items_out: dict[str, dict] = {}
    by_item: dict[str, list[dict]] = {}
    for cycle_ref, admission in sorted((admissions or {}).items()):
        if not isinstance(admission, dict):
            continue
        selection = admission.get("selection")
        selected = selection.get("selected") if isinstance(selection, dict) else None
        if not isinstance(selected, list):
            continue
        for item_id in selected:
            if item_id in item_ids:
                by_item.setdefault(str(item_id), []).append(dict(admission, cycle_ref=cycle_ref))
    if not receipts_root:
        return {"status": "RECEIPTS_ROOT_NOT_NAMED",
                "because": "no receipts directory was named for this read, so no "
                           "receipt could be opened. There is no default: the one "
                           "on this node is chosen by whoever dispatched, and "
                           "guessing it would read receipts from a place nobody "
                           "ran this. The field is left OFF the rows rather than "
                           "reported `False`, which would blame the worker for a "
                           "missing flag",
                "items": {}}
    root = Path(receipts_root)
    if not root.is_absolute():
        raise Refused(
            f"the receipts root {receipts_root!r} is not absolute. A relative one "
            f"would be resolved against whatever directory the caller stood in, "
            f"and a receipt read from the wrong directory is not a near miss — it "
            f"is an answer about a different dispatch")
    if not by_item:
        return {"status": "NO_ADMISSION_NAMES_AN_ITEM",
                "because": "the store holds no admission whose selection names any "
                           "Item on this board, so there is no action key to look "
                           "a receipt up by. This is the node's normal state and "
                           "not a fault: nothing has been admitted, so nothing has "
                           "a worker",
                "items": {}}
    for item_id, found in sorted(by_item.items()):
        verdicts = []
        for admission in found:
            key = str(admission.get("action_key") or "")
            if not key:
                # The store requires one of every admission it accepts, so this
                # is a record that did not come through `admit`. Undecidable, not
                # held: a missing key is not a running process.
                verdicts.append((None, "ADMISSION_NAMES_NO_ACTION_KEY", None))
                continue
            path = root / f"{key}.json"
            if path.is_symlink():
                verdicts.append((None, "RECEIPT_IS_A_SYMLINK", key))
                continue
            if not path.is_file():
                verdicts.append((False, "NO_RECEIPT_FOR_ACTION_KEY", key))
                continue
            try:
                receipt = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                verdicts.append((False, "RECEIPT_UNREADABLE", key))
                continue
            if not isinstance(receipt, dict):
                verdicts.append((False, "RECEIPT_IS_NOT_AN_OBJECT", key))
                continue
            if str(receipt.get("action_key") or "") != key:
                # `bounded_invocation.dispatch_evidence_conflict` refuses this
                # pairing for the same reason: one receipt carries the key it was
                # written under, and a file naming another is not this dispatch's.
                verdicts.append((False, "RECEIPT_NAMES_ANOTHER_ACTION_KEY", key))
                continue
            held, because = _receipt_liveness(receipt)
            verdicts.append((held, because, key))
        # Held wins over undecidable, and undecidable wins over released: the
        # question is whether anything is STILL held, so one dispatch that cannot
        # be shown stopped is enough, and a `True` from one call says nothing
        # about the process another call started.
        if any(value is False for value, _b, _k in verdicts):
            answer, reasons = False, [b for v, b, _k in verdicts if v is False]
        elif any(value is None for value, _b, _k in verdicts):
            answer, reasons = None, [b for v, b, _k in verdicts if v is None]
        else:
            answer, reasons = True, [b for v, b, _k in verdicts if v is True]
        entry = {"cycle_refs": sorted(a["cycle_ref"] for a in found),
                 "action_keys": sorted({k for _v, _b, k in verdicts if k}),
                 "because": "+".join(sorted(set(reasons)))}
        if answer is not None:
            entry["worker_stopped"] = answer
        items_out[item_id] = entry
    return {"status": "PRODUCED",
            "because": f"{len(items_out)} Item(s) are named by an admission and "
                       f"their receipts were read; a link that broke is named per "
                       f"Item rather than summed here",
            "items": items_out}


#: Which scope's references are the Item's own.
#:
#: Read from `runtime/environment-contract.yaml#scopes`, whose `work-item` entry
#: is "一个 Work Item 持有它；Item 关闭时回收" — a scope whose holder IS a Work
#: Item. That is the whole reason the join may be exact: a `session`- or
#: `project`-scoped reference whose holder happens to be spelled like an Item is
#: not that Item holding a resource, and nothing in the store validates the
#: spelling of a holder. Matching on a name without this scope would be the
#: substring guess this file refuses elsewhere, and it would answer HELD for an
#: Item because somebody typed its id into an unrelated field.
ITEM_SCOPE = "work-item"

#: Where the lease half of a row comes from, link by link, in the same shape as
#: `LIVENESS_CHAIN` and for the same reason: a reader of the composed document
#: has to be able to see which fact was read, not just that one was.
LEASES_CHAIN = (
    "one file per resource under `state/serveros/resource-leases/`, written by "
    "`runtime/lease_store.py`",
    "that record's `references[]`, each a `(scope, holder)` pair whose state is "
    "derived by `runtime/environment.reference_state`",
    "`runtime/environment.effective`'s per-resource rows — the document "
    "`lease environment` prints and `work focus` now composes",
)


def resources_released_by_item(environment: dict | None, *, item_ids) -> dict:
    """Whether each Item still holds something, from the store that knows.

    THE CORRECT OWNER, and there is no second one. "Has this been given back" is
    a question about occupancy, occupancy is what `state/serveros/resource-leases/`
    is authority for (`fleet/state-root-contract.yaml`: `class: authority`,
    `holds: current resource occupancy`), and the namespace's own producer
    records a retirement every time one happens. Nothing here is derived from a
    Run's state, from a card field, or from the Item's own state.

    `environment` is the DERIVED document, not the raw records: it is the
    `runtime/environment.effective` output whose `resources[].references[]` rows
    already carry a `state`. Taking the raw files instead would mean deciding
    LIVE vs EXPIRED here, which is `runtime/environment.reference_state`'s rule —
    a second copy of it would agree until one of them was edited, and the copy
    that drifts is the one nobody reads.

    That means the whole answer rests on two strings the owner produced: the
    reference's `scope` and its `state`. Neither is re-derived here.

    An EMPTY namespace is an answer, not a gap: the contract declares
    `absent_means: NO_LEASES_HELD`, so a store that was read and holds nothing
    naming this Item answers `True` — the Item released what it held, because it
    held nothing. `None` is reserved for the case where the store could not be
    read at all, and the two must not be conflated in either direction: reading
    an unreadable namespace as "nothing held" is the claim
    `view.environment_leases` refuses to make on the node's behalf, and reading
    an empty one as "undecidable" would put every row in flight forever.

    Returns `{"status", "because", "items"}`. `items` answers per Item the
    document speaks about; an Item the document names nowhere gets `True` with a
    reason saying so, because that absence is the store's positive statement
    that nothing is held on its behalf.
    """
    if environment is None:
        return {"status": "LEASES_NOT_READ",
                "because": "no lease reading was supplied for this composition, "
                           "so the namespace was not consulted and no row carries "
                           "the field. Unlike `--receipts` there is nothing to "
                           "name here — the namespace is resolved by the root "
                           "contract, not chosen by whoever dispatched — so this "
                           "is a caller that did not read it, and the field is "
                           "left OFF rather than reported `True`, which would "
                           "release an Item on a fact nobody looked at",
                "items": {}}
    if isinstance(environment, dict) and environment.get("status") == "UNREADABLE":
        return {"status": "LEASES_UNREADABLE",
                "because": str(environment.get("because")
                               or "the lease namespace could not be read"),
                "items": {}}
    rows = environment.get("resources") if isinstance(environment, dict) else None
    if not isinstance(rows, list):
        return {"status": "LEASES_DOCUMENT_UNEXPECTED",
                "because": "the lease reading carries no `resources` list, so it "
                           "is not the document `runtime/environment.effective` "
                           "produces and nothing can be read off it. Reported "
                           "rather than treated as empty: an empty list is the "
                           "claim `NO_LEASES_HELD`",
                "items": {}}

    # `held` wins over undecidable, and undecidable wins over released — the same
    # order as `worker_stopped_by_item`, and for the same reason: the question is
    # whether anything is STILL held, so one reference that cannot be shown given
    # back is enough, and a released reference says nothing about the next one.
    per_item: dict[str, list[tuple[bool | None, str, str]]] = {}
    elsewhere: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        resource = str(row.get("resource") or "")
        for reference in row.get("references") or []:
            if not isinstance(reference, dict):
                continue
            holder = reference.get("holder")
            if not isinstance(holder, str) or holder not in item_ids:
                continue
            if reference.get("scope") != ITEM_SCOPE:
                # An Item id appearing as a holder under another scope. Not this
                # Item's occupancy, and worth counting: if this ever fires, the
                # scope vocabulary and how holders are spelled have drifted
                # apart, and a silent skip would read as "nothing held".
                elsewhere.add(f"{resource}:{reference.get('scope')}:{holder}")
                continue
            state = str(reference.get("state") or "")
            if state == "LIVE":
                per_item.setdefault(holder, []).append(
                    (False, "REFERENCE_IS_LIVE", resource))
            elif state == "EXPIRED":
                # `runtime/environment._reference_row` is explicit that an
                # expired reference is "no longer backed by anything" and is
                # reported rather than dropped: its holder may have crashed, and
                # a crashed holder is not a holder that gave the resource back.
                # Answering `True` here would free a slot on a claim that
                # stopped being backed — the crash case answered with silence.
                per_item.setdefault(holder, []).append(
                    (False, "REFERENCE_EXPIRED_UNRESOLVED", resource))
            else:
                per_item.setdefault(holder, []).append(
                    (None, f"REFERENCE_STATE_NOT_KNOWN_HERE:{state or 'none'}",
                     resource))

    # Two names for one answer, because the two are different findings: a
    # namespace holding nothing at all is `fleet/state-root-contract.yaml`'s
    # `absent_means: NO_LEASES_HELD` read one size up, and a namespace holding
    # other things while naming no `work-item` reference of this Item says
    # something narrower. The first word is NOT reused here: it belongs to
    # `view.environment_leases`, whose envelope carries it, and this function is
    # handed the derived document instead. Spelling a second time, without the
    # reader that owns it, is how one fact acquires two spellings and a person
    # learns to compare them.
    nothing = "NO_REFERENCE_NAMED_BY_THIS_ITEM"
    if not rows:
        nothing = "LEASES_NAMESPACE_EMPTY"

    items_out: dict[str, dict] = {}
    for item_id in sorted(item_ids):
        found = per_item.get(item_id)
        if not found:
            items_out[item_id] = {
                "resources_released": True,
                "resources": [],
                "because": nothing,
            }
            continue
        if any(value is False for value, _b, _r in found):
            answer, reasons = False, [b for v, b, _r in found if v is False]
        elif any(value is None for value, _b, _r in found):
            answer, reasons = None, [b for v, b, _r in found if v is None]
        else:
            answer, reasons = True, [b for v, b, _r in found if v is True]
        entry = {"resources": sorted({r for _v, _b, r in found}),
                 "because": "+".join(sorted(set(reasons)))}
        if answer is not None:
            entry["resources_released"] = answer
        items_out[item_id] = entry

    unheld = sum(1 for entry in items_out.values()
                 if entry["because"] == nothing)
    return {"status": "PRODUCED",
            "because": (f"{len(rows)} resource record(s) were read; {unheld} of "
                        f"{len(item_ids)} Item(s) are named by no `{ITEM_SCOPE}` "
                        f"reference, which is the store saying they hold "
                        f"nothing. A reference whose state this function cannot "
                        f"read is named per Item rather than summed here"),
            # An Item id used as a holder under some other scope. Empty on every
            # namespace seen so far, and it is reported rather than dropped
            # because a silent skip and "nothing held" render identically.
            "held_by_another_scope": sorted(elsewhere),
            "items": items_out}


def scope_digest(card: dict) -> str:
    """Stable accepted scope, independent of grant contents and live Git state."""
    import hashlib
    import json
    if not card.get("id") or not card.get("project") or not card.get("acceptance"):
        raise Refused("Item card must declare identity, project and acceptance")
    fields = ("id", "project", "kind", "title", "source_refs", "acceptance",
              "scope", "non_goals", "non-goals", "expected_deliverables")
    value = {key: card[key] for key in fields if key in card}
    return "sha256:" + hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                                 separators=(",", ":")).encode()).hexdigest()


def control_join(composed: dict, board: dict, *, records_root, grants: dict) -> dict:
    """Join planned READY Activities and accepted scope to verified grant refs.

    Work Engine supplies the action kind. The records card supplies its scope.
    A grant is selected only when exactly one names the Item; its own digest is
    never copied as evidence of the current scope. Ambiguity stays visible.
    """
    import yaml
    root = Path(records_root).resolve()
    items = {}
    for section in board.get("sections", []):
        for item in section.get("rows", []):
            items[(item.get("project"), item.get("id"))] = item
    rows, joins = [], []
    for original in composed["rows"]:
        row = dict(original)
        why = []
        item = items.get((row["project"], row["id"]), {})
        ready = item.get("ready_activities", [])
        if len(ready) == 1 and isinstance(ready[0].get("kind"), str) and ready[0]["kind"].strip():
            row["next_action"] = ready[0]["kind"]
        else:
            why.append("no unique READY Activity declares the next action")
        path = root / "projects" / row["project"] / "items" / row["id"] / "card.yaml"
        try:
            if not path.resolve().is_relative_to(root):
                raise Refused("Item card escapes records root")
            card = yaml.safe_load(path.read_text())
            if card.get("id") != row["id"] or card.get("project") != row["project"]:
                raise Refused("Item card identity differs from the Work owner")
            row["scope_digest"] = scope_digest(card)
        except (OSError, ValueError, AttributeError, yaml.YAMLError, Refused) as exc:
            why.append(str(exc))
        refs = [ref for ref, grant in grants.items() if row["id"] in grant.get("items", [])
                and row["project"] in grant.get("projects", [])]
        if len(refs) == 1:
            row["grant_ref"] = refs[0]
        else:
            why.append("no unique authorizing grant names this Item")
        joins.append({"id": row["id"], "activity": ready[0].get("id") if len(ready) == 1 else None,
                      "card": str(path), "gaps": why})
        rows.append(row)
    _check_declared(rows)
    return {**composed, "rows": rows, "control_join": joins,
            "not_produced": {key: value for key, value in composed["not_produced"].items()
                             if key not in ("grant_ref", "scope_digest", "next_action")}}


def compose(board: dict, *, projection_times: dict | None = None,
            admissions: dict | None = None, receipts_root=None,
            environment: dict | None = None) -> dict:
    """The board as focus rows, with the age of the FACTS it was composed from.

    `observed_at` is the oldest `generated_at` among the projections, and it is
    `None` when any projection's age is unknown. Both are answers a caller must
    handle: `select_focus` reads `None` as not fresh, which is the right
    outcome for an age nobody can state.

    Two of a row's keys do not come from the board, and each comes from a
    different store: `admissions` + `receipts_root` give `worker_stopped`, and
    `environment` gives `resources_released`. Both default to "not read", which
    leaves the field OFF the row — `daily_cycle.liveness` reads that as silence,
    and silence is the correct rendering of a source nobody consulted. The two
    are reported in separate blocks of the document — `liveness` and `leases` —
    rather than one, so a status cannot be attributed to the wrong chain.
    """
    if projection_times is None:
        projection_times = read_projections(board.get("projects") or [])
    observed_at = oldest(projection_times)
    rows = rows_from_board(board)
    item_ids = {row["id"] for row in rows}
    liveness = worker_stopped_by_item(
        admissions, receipts_root=receipts_root, item_ids=item_ids)
    leases = resources_released_by_item(environment, item_ids=item_ids)
    for row in rows:
        entry = liveness["items"].get(row["id"]) or {}
        if "worker_stopped" in entry:
            # The TWO keys this function adds to a row, from two different
            # stores, and each is added only when ITS source answered. Absent
            # otherwise, which `daily_cycle.liveness` reads as "this projection
            # is silent" — the third value, and the reason neither field is
            # defaulted here. They are kept out of the row loop's head because
            # they are separate facts: a row may carry one and not the other.
            row["worker_stopped"] = entry["worker_stopped"]
        lease_entry = leases["items"].get(row["id"]) or {}
        if "resources_released" in lease_entry:
            row["resources_released"] = lease_entry["resources_released"]
    _check_declared(rows)
    return {
        "rows": rows,
        "observed_at": observed_at,
        "board_at": board.get("at"),
        "projection_times": projection_times,
        "projects": [p.get("project") for p in (board.get("projects") or [])],
        "liveness": {"chain": list(LIVENESS_CHAIN),
                     "status": liveness["status"],
                     "because": liveness["because"],
                     "receipts_root": str(receipts_root) if receipts_root else None,
                     "items": liveness["items"]},
        # A SECOND LIVENESS SOURCE, named apart from the first because it is one:
        # `worker_stopped` is read off a process receipt and this off the lease
        # namespace, and a document with one `liveness` key holding two chains
        # would let a reader take the second's status for the first's.
        "leases": {"chain": list(LEASES_CHAIN),
                   "status": leases["status"],
                   "because": leases["because"],
                   "held_by_another_scope": leases.get("held_by_another_scope", []),
                   "items": leases["items"]},
        "not_produced": dict(NOT_PRODUCED),
        "not_produced_elsewhere": dict(NOT_PRODUCED_ELSEWHERE),
    }
