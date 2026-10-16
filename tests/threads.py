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
        # #237 B1. The CURRENT writer for this thread's write scope, joined at
        # the same moment as the binding — the same two-facts-in-one-read shape
        # the daemon's `agent.binding.resolve` provides. A test double that
        # omitted it would make the writer gate unreachable from every routing
        # test, which is the shape `tests/threads.py` exists to prevent one
        # plane over.
        return {"binding": record, "item": item,
                "current_writer": self._current_writer(record)}

    def _current_writer(self, record):
        """The open binding for the same Item with the highest writer epoch.

        Mirrors what the production handle computes from the scope's owner: the
        current writer is the newest claim on the Item, and a thread whose claim
        is older is STALE. Returns None when nothing claims a writer, which the
        kernel reads as UNKNOWN and does not gate on.
        """
        if not record or not record.get("bound_item_ref"):
            return None
        item = str(record["bound_item_ref"])
        best = None
        for other in list(self._records.values()):
            if str(other.get("bound_item_ref")) != item:
                continue
            if str(other.get("state") or "").upper() == "CLOSED":
                continue
            claim = other.get("writer")
            if not claim:
                continue
            if best is None or int(claim.get("epoch", 0)) > int(best.get("epoch", 0)):
                best = claim
        return dict(best) if best is not None else None

    def put(self, record):
        ref = str(record["session_ref"])
        existing = self._records.get(ref)
        if existing is not None:
            return existing
        self._records[ref] = dict(record)
        self.puts.append(dict(record))
        return self._records[ref]

    def close(self, ref, *, at="2026-09-14T00:00:00+00:00", reason=None):
        """Close the binding for `ref`. A ref with nothing to close is a no-op.

        Matches `AgentControlStore.close_binding`'s intent — closing a thread
        that was never bound is not an error, there is simply nothing to
        revoke — WITHOUT its fail-loud refusal, because a test double that
        raised here would make a handoff unreachable from every routing test
        (they run `assume-bound`, where `get` answers for a ref that was never
        recorded). A ref that is neither recorded nor assumed has no binding and
        this returns None.
        """
        key = str(ref)
        record = self._records.get(key)
        if record is None:
            record = (self.get(key) or {}).get("binding")
            if record is None:
                return None
        record = dict(record)
        record["state"] = "CLOSED"
        record["closed_at"] = at
        if reason:
            record["closed_reason"] = reason
        self._records[key] = record
        return record

    def bind(self, ref, *, session_role="execution",
             bound_item_ref="LIT-0101", state="OPEN", writer=None) -> dict:
        """Record a binding directly, for a test that needs one in place.

        `writer` records a claim of write authority (#237 B1), which is what
        makes the binding a v2 one and what the writer gate checks. Omitting it
        leaves a v1 binding, which claims nothing and is not gated.
        """
        record = {"schema": ("serveros/thread-binding.v2" if writer
                             else "serveros/thread-binding.v1"),
                  "session_ref": str(ref), "session_role": session_role,
                  "bound_item_ref": bound_item_ref,
                  "bound_item_ref_frozen": True, "state": state}
        if writer:
            record["writer"] = dict(writer)
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
