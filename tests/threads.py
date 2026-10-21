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

import importlib.util
import sys
from pathlib import Path

#: The kernel, loaded the way every other test loads a runtime module: under the
#: synthetic name the daemon uses, so the `ThreadRefused` class caught here is
#: the SAME class the daemon raises.
_COORDINATION_NAME = "serveros_runtime_coordination"


def _coordination():
    if _COORDINATION_NAME in sys.modules:
        return sys.modules[_COORDINATION_NAME]
    path = Path(__file__).resolve().parent.parent / "runtime" / "coordination.py"
    spec = importlib.util.spec_from_file_location(_COORDINATION_NAME, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[_COORDINATION_NAME] = module
    spec.loader.exec_module(module)
    return module


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
        """The scope's current writer, derived by the KERNEL's own function.

        Delegated rather than re-derived. A second derivation of "who may write
        this scope" is a second answer to it, and this double exists to mirror
        the daemon — not to invent a rule the daemon does not have.
        """
        if not record or not record.get("bound_item_ref"):
            return None
        return _coordination().current_writer(self._records,
                                              record["bound_item_ref"])

    def put(self, record):
        ref = str(record["session_ref"])
        existing = self._records.get(ref)
        if existing is not None:
            return existing
        self._records[ref] = dict(record)
        self.puts.append(dict(record))
        return self._records[ref]

    def close(self, ref, *, at="2026-09-14T00:00:00+00:00", reason=None,
              successor_ref=None, context_ref=None):
        """Close the binding for `ref`. A ref with nothing to close is a no-op.

        Matches `AgentControlStore.close_binding`'s intent — closing a thread
        that was never bound is not an error, there is simply nothing to
        revoke — WITHOUT its fail-loud refusal, because a test double that
        raised here would make a handoff unreachable from every routing test
        (they run `assume-bound`, where `get` answers for a ref that was never
        recorded). A ref that is neither recorded nor assumed has no binding and
        this returns None.

        `successor_ref` mirrors the daemon's write-authority transfer: the
        target must be a recorded binding for the same Item, the source must
        record a writer, and the target receives the kernel's next writer claim.
        A cross-Item or writer-less transfer returns None, which the router
        treats as a fail-closed non-transfer.
        """
        key = str(ref)
        record = self._records.get(key)
        if record is None:
            record = (self.get(key) or {}).get("binding")
            if record is None:
                return None
        record = dict(record)
        if successor_ref and not (record.get("writer") or {}).get("owner"):
            return None
        record["state"] = "CLOSED"
        record["closed_at"] = at
        if reason:
            record["closed_reason"] = reason
        self._records[key] = record
        result = dict(record)
        if successor_ref:
            target_key = str(successor_ref)
            target = self._records.get(target_key)
            if target is None:
                target = (self.get(target_key) or {}).get("binding")
            if (target is None
                    or str(target.get("bound_item_ref") or "")
                    != str(record.get("bound_item_ref") or "")):
                return None
            target = dict(target)
            if not target.get("writer"):
                source_writer = record.get("writer") or {}
                writer = _coordination().mint_writer(
                    self._records, record.get("bound_item_ref"),
                    owner=source_writer.get("owner") or target_key)
                target["schema"] = "serveros/thread-binding.v2"
                target["writer"] = writer
                self._records[target_key] = target
            result["successor"] = target
            result["current_writer"] = target["writer"]
        return result

    def claim(self, ref, *, from_ref, context_ref=None):
        """Mirror the daemon's pre-close writer claim for a bound target.

        The real route mints from the LIVE Work lease. This double has no lease
        store, so it advances the SOURCE writer's owner and token — the same
        shape, with the authority supplied by the test. A missing source, a
        cross-Item target, or a source that is not the current writer answers
        None, which the router turns into a fail-closed handoff.
        """
        key = str(ref)
        source_key = str(from_ref)
        source = self._records.get(source_key)
        target = self._records.get(key)
        if source is None or target is None:
            return None
        if str(source.get("bound_item_ref") or "") != str(
                target.get("bound_item_ref") or ""):
            return None
        if target.get("writer"):
            return target
        source_writer = source.get("writer") or {}
        if not source_writer.get("owner"):
            return None
        current = _coordination().current_writer(
            self._records, source.get("bound_item_ref"))
        if (not isinstance(current, dict)
                or any(str(current.get(field)) != str(source_writer.get(field))
                       for field in ("owner", "epoch", "token"))):
            return None
        writer = {"owner": source_writer["owner"],
                  "epoch": int(source_writer.get("epoch") or 1),
                  "token": str(int(source_writer.get("token") or 0) + 1)}
        target = dict(target)
        target["schema"] = "serveros/thread-binding.v2"
        target["writer"] = writer
        self._records[key] = target
        return target

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
