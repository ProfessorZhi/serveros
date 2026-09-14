#!/usr/bin/env python3
"""A bindings handle with no filesystem, for tests that need one.

WHY THIS IS A SHARED MODULE AND NOT A FIXTURE IN EACH FILE
----------------------------------------------------------
`LocalLoopbackTransport` requires a bindings handle and has no default for it,
which is deliberate: a loopback that could be built without one would dispatch
every create and every send with no thread check at all, and it would do so
silently. Four test files build a loopback, and four copies of a handle is four
places for the handling of "a thread nobody bound" to be written differently.

THE TWO MODES, AND WHY NEITHER IS THE DEFAULT
---------------------------------------------
`unknown` is required and has no default, because the default would be a
decision this module has no business making:

    MemoryBindings(unknown="assume-bound")  every session ref that was never
        recorded is treated as an execution thread bound to an OPEN Item. This
        is what a test about ROUTING wants — the test is not about §17, and
        making it construct a thread first would say it was.

    MemoryBindings(unknown="refuse")  a ref nobody recorded is a ref nobody
        recorded: `get` returns no binding and `admit_work` refuses with
        INVALID_CONTEXT. This is what a test about §17 wants, and it is the
        only mode in which the refusal is reachable.

Naming the mode at every call site is the point. `unknown="assume-bound"` in a
test says, in the test, that this test is not the one guarding the boundary —
and a reader can grep for it.
"""
from __future__ import annotations

ASSUME_BOUND = "assume-bound"
REFUSE = "refuse"
MODES = (ASSUME_BOUND, REFUSE)


class MemoryBindings:
    """`get`/`put`, backed by a dict. Mirrors the daemon's two methods.

    It does NOT mirror the daemon's validation, and that is deliberate: the
    rules live in `runtime/coordination.py` and the daemon is where they are
    applied to a write. A test double that re-applied them would be a second
    implementation of the rule, and the second implementation is the one that
    disagrees.
    """

    def __init__(self, *, unknown: str, items: dict | None = None,
                 records: dict | None = None) -> None:
        if unknown not in MODES:
            raise ValueError(
                f"{unknown!r} is not a bindings mode; a test must say whether "
                f"an unrecorded session is assumed bound or refused. One of "
                f"{list(MODES)}")
        self.unknown = unknown
        # `item_ref -> (state, outcome)`. An Item nobody listed is UNKNOWN,
        # which `item_axis` reads as UNEVALUABLE and therefore does not refuse
        # — so a test that means "this Item is delivered" has to say so.
        self.items = dict(items or {})
        self._records = dict(records or {})
        self.puts: list[dict] = []

    def get(self, ref):
        key = str(ref)
        record = self._records.get(key)
        if record is None and self.unknown == ASSUME_BOUND:
            record = {"schema": "serveros/thread-binding.v1", "session_ref": key,
                      "session_role": "execution",
                      "bound_item_ref": _assumed_item(key),
                      "bound_item_ref_frozen": True, "state": "OPEN"}
        item = None
        if record and record.get("bound_item_ref"):
            state, outcome = self.items.get(
                record["bound_item_ref"], ("UNKNOWN", None))
            item = {"state": state, "outcome": outcome,
                    "item_ref": record["bound_item_ref"]}
        return {"binding": record, "item": item}

    def put(self, record):
        ref = str(record["session_ref"])
        existing = self._records.get(ref)
        if existing is not None:
            return existing
        self._records[ref] = dict(record)
        self.puts.append(dict(record))
        return self._records[ref]

    def close(self, ref, *, at="2026-09-14T00:00:00+00:00", reason=None):
        record = dict(self._records[str(ref)])
        record["state"] = "CLOSED"
        record["closed_at"] = at
        if reason:
            record["closed_reason"] = reason
        self._records[str(ref)] = record
        return record

    def bind(self, ref, *, session_role="execution",
             bound_item_ref="LIT-0101", state="OPEN") -> dict:
        """Record a binding directly, for a test that needs one in place."""
        record = {"schema": "serveros/thread-binding.v1", "session_ref": str(ref),
                  "session_role": session_role,
                  "bound_item_ref": bound_item_ref,
                  "bound_item_ref_frozen": True, "state": state}
        self._records[str(ref)] = record
        return record


def _assumed_item(session_ref: str) -> str:
    """A deterministic Item ref per session, so two threads are two Items.

    Derived rather than fixed, because a fixed one would make every assumed
    thread bound to the same Item — and a test that sent one thread's work into
    another's would then be silently admitted. This way the mismatch refusal
    still fires in `assume-bound` mode when the test actually names an Item.
    """
    return "ASSUMED-" + str(session_ref).replace(":", "-")
