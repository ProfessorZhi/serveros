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

`resources_released`, the field that reads as its pair, still has no producer
anywhere — not even on that receipt — so it stays in `not_produced`. Two fields
that read as a pair are one field and one gap, and one sentence covering both
is how the gap stays invisible.

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
}

# The policy's inputs the board does not carry. Each is stated here rather than
# left for a reader to notice, because the absence is what made the policy
# unpointable and a reader of the composed rows cannot tell a field the fabric
# has not built from one this adapter forgot.
NOT_PRODUCED = {
    "resources_released": "the other half of what used to be one entry. "
                          "`worker_stopped` gained a producer on 2026-09-25 (the "
                          "invocation receipt, via `worker_stopped_by_item`); this "
                          "one has no producer anywhere — not on that receipt, not "
                          "on the board, not in the lease store, which records "
                          "which holder holds a resource and says nothing about "
                          "whether it was given back. It is NOT `not "
                          "worker_stopped`: a cycle can have stopped its process and "
                          "still be holding a lease, and one sentence covering both "
                          "fields would answer about the wrong one",
    "focus_disposition": "the field that would mark an Item PARKED is part of the "
                         "same proposed card extension as the priorities",
    "parking_acknowledged": "same",
    "scope_digest": "a grant-scoped digest, and no card carries one. It stopped "
                    "being unproducible when the grant store landed "
                    "(`runtime/agent_control_store.py#put_grant` takes "
                    "`scope_digests`), so what is missing is the CARD field: a "
                    "row that named a grant and no scope digest would be "
                    "admitted under a grant whose scope was never checked, so "
                    "`grant_reason` refuses it as SCOPE_CHANGED",
    "grant_ref": "the card carries no such field. The other end of this join "
                 "exists now — a grant can be recorded and the envelope is "
                 "derived from it (`context/delegation.py`) — but until a card "
                 "names a grant, `select_focus` reads `grants.get(None)` and "
                 "reports every candidate MISSING_OR_REVOKED_GRANT. The gap is "
                 "the board's producer, not the store's",
    "next_action": "the card carries no such field, and since 2026-09-25 a silent "
                   "row is an exact answer rather than a default: `classify` "
                   "refuses it as NEXT_ACTION_NOT_ESTABLISHED instead of asking a "
                   "grant about an action nobody named. The version that defaulted "
                   "to `research` hid this absence behind a sentence about a grant",
    "blocked_by": "the board carries no dependency edge",
}

# Facts the policy needs that are absent for a reason outside this adapter.
NOT_PRODUCED_ELSEWHERE = {
    "grants": "`select_focus(grants=...)` takes the envelope "
              "`context/delegation.py` derives from the delegations recorded in "
              "`state/serveros/runtime/agent-control/store.json`. This node has "
              "recorded none, and — separately — a board row cannot name one, so "
              "every candidate is reported MISSING_OR_REVOKED_GRANT. That is the "
              "true answer: no objective has been authorized, so none may be "
              "admitted. The two gaps are named apart on purpose: an empty store "
              "is a delegation nobody made, and a row without a `grant_ref` is a "
              "card field nobody built.",
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


def compose(board: dict, *, projection_times: dict | None = None,
            admissions: dict | None = None, receipts_root=None) -> dict:
    """The board as focus rows, with the age of the FACTS it was composed from.

    `observed_at` is the oldest `generated_at` among the projections, and it is
    `None` when any projection's age is unknown. Both are answers a caller must
    handle: `select_focus` reads `None` as not fresh, which is the right
    outcome for an age nobody can state.
    """
    if projection_times is None:
        projection_times = read_projections(board.get("projects") or [])
    observed_at = oldest(projection_times)
    rows = rows_from_board(board)
    liveness = worker_stopped_by_item(
        admissions, receipts_root=receipts_root,
        item_ids={row["id"] for row in rows})
    for row in rows:
        entry = liveness["items"].get(row["id"]) or {}
        if "worker_stopped" in entry:
            # The ONE key this function adds to a row, and it adds it only when
            # the receipt answered. Absent otherwise, which `daily_cycle.liveness`
            # reads as "this projection is silent" — the third value, and the
            # reason the field is not defaulted here.
            row["worker_stopped"] = entry["worker_stopped"]
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
        "not_produced": dict(NOT_PRODUCED),
        "not_produced_elsewhere": dict(NOT_PRODUCED_ELSEWHERE),
    }
