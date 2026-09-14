#!/usr/bin/env python3
"""The durable half of Agent Control: trusted contexts and approvals at rest.

Everything that decides lives in `context/actor.py` and `runtime/approval.py`,
and both are pure. This module is the other half — the part that has a
filesystem and a clock — kept apart so the decisions can be tested without
either.

Why it is durable, and what that does NOT make it
-------------------------------------------------
§23 requires that a restart not lose control-plane state, and §25(I) asks for
that to be shown rather than asserted. So a context or an approval that has
been recorded survives `systemctl restart serverosd`.

Durability is not authority. A record loaded from disk is not re-verified by
being loaded: `expires_at` is read back and re-checked against the clock, so a
process that starts after a two-day outage finds its approvals expired rather
than intact. The rule the state-root contract states for every other namespace
holds here too — the file is what ServerOS recorded, and every reader is told
when.

Why it is not a HumanIdentity DB
--------------------------------
It is keyed by `context_ref` and `approval_ref`, never by actor. There is no
`get(actor)` in this file, and adding one would be the change that turns an
evidence log into a directory of people. An actor appears on records as a
field; it is never a key.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

SCHEMA = "serveros/agent-control-store.v1"


class StoreError(RuntimeError):
    pass


def _atomic_write(path: Path, payload: dict) -> None:
    """Write via a temporary file in the same directory, then rename.

    A half-written store is worse than no store: every later read would parse
    it, fail, and — in the version of this that reads a corrupt file as empty —
    silently forget every approval that had been granted. `os.replace` is
    atomic within a filesystem, and the temp file is created beside the target
    so that it is the same one.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=str(path.parent),
        prefix=path.name + ".", suffix=".tmp", delete=False)
    try:
        json.dump(payload, handle, indent=2, sort_keys=True, default=str)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        os.replace(handle.name, path)
    except BaseException:
        try:
            os.unlink(handle.name)
        except OSError:
            pass
        raise


class AgentControlStore:
    """Trusted contexts and approvals, persisted as one small document.

    One file, not two, because the two collections are written in the same
    transaction when an approval is consumed — and two files that are written
    together are two files that can disagree after a crash.
    """

    def __init__(self, path: Path | str, *, now=None) -> None:
        self.path = Path(path)
        self._now = now
        self._data = self._load()

    # -- loading -----------------------------------------------------------

    def _load(self) -> dict:
        if not self.path.exists():
            return {"schema": SCHEMA, "contexts": {}, "approvals": {},
                    "bindings": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            # Refused rather than replaced with an empty store. An unreadable
            # control-plane store means every approval that existed is no
            # longer known, and a process that starts serving anyway would
            # grant nothing while reporting itself healthy.
            raise StoreError(
                f"{self.path} exists and cannot be read ({type(exc).__name__}: "
                f"{exc}). Refusing to start with an empty control store: every "
                "approval it held would be silently withdrawn.") from exc
        if not isinstance(data, dict):
            raise StoreError(f"{self.path} is not a JSON object")
        data.setdefault("contexts", {})
        data.setdefault("approvals", {})
        data.setdefault("bindings", {})
        data["schema"] = SCHEMA
        return data

    def _flush(self) -> None:
        _atomic_write(self.path, self._data)

    # -- contexts ----------------------------------------------------------

    def put_context(self, record: dict) -> dict:
        """Record a trusted context. Idempotent on `context_ref`.

        Re-reporting the same event overwrites it with an equal record: the
        ref is a hash of the provider's own fields, so two reports of one event
        produce one context. Overwriting is therefore not a way to replace a
        context — a different event has a different ref and lands beside it.
        """
        ref = record.get("context_ref")
        if not ref:
            raise StoreError("a trusted context without a context_ref cannot be stored")
        self._data["contexts"][ref] = record
        self._flush()
        return record

    def get_context(self, ref: str) -> dict | None:
        return self._data["contexts"].get(str(ref))

    def contexts(self) -> dict:
        return dict(self._data["contexts"])

    def put_approval(self, record: dict) -> dict:
        ref = record.get("approval_ref")
        if not ref:
            raise StoreError("an approval without an approval_ref cannot be stored")
        self._data["approvals"][ref] = record
        self._flush()
        return record

    def get_approval(self, ref: str) -> dict | None:
        return self._data["approvals"].get(str(ref))

    def approvals(self) -> dict:
        return dict(self._data["approvals"])

    def for_actor_action(self, *, actor_ref: str, action: str) -> list[dict]:
        """Every approval held for one actor and one action.

        The only query in this module, and it takes an actor as a FILTER rather
        than as a key. There is deliberately no `approvals_for_actor()` and no
        `contexts_for_actor()`: those two signatures are the whole difference
        between asking "was this approved" and asking "what may this person
        do", and the second question belongs to policy, which this module is
        not.
        """
        return [a for a in self._data["approvals"].values()
                if a.get("actor_ref") == actor_ref and a.get("action") == action]

    # -- thread bindings (§17) ---------------------------------------------

    def put_binding(self, record: dict) -> dict:
        """Record what a thread is for. Write-once, on `session_ref`.

        A binding is the one part of this store that is deliberately NOT
        updatable, and the reason is that its central field is the thing §17
        requires to be immutable. `bound_item_ref` cannot be changed by a later
        write because there is no method here that can change it: a second put
        for the same session is either an idempotent re-report of an equal
        record — which is what a retried create looks like — or a refusal.

        Making immutability structural rather than a rule is the whole point.
        A `put_binding` that overwrote would be a way for a thread to change
        which Item it is for, and the caller who wants that does not want this
        store to stop them; they want a new thread, which is what the refusal
        tells them.

        The exception is `state`, which `close_binding` owns. A thread closing
        is a fact about the thread and not a change of mind about its Item.
        """
        ref = record.get("session_ref")
        if not ref:
            raise StoreError(
                "a thread binding without a session_ref cannot be stored: it is "
                "a statement about a session, and a binding whose session nobody "
                "named is a rule with no subject")
        existing = self._data["bindings"].get(str(ref))
        if existing is not None:
            mine = {k: v for k, v in record.items()
                    if k not in ("recorded_at", "state", "closed_at", "closed_reason")}
            theirs = {k: v for k, v in existing.items()
                      if k not in ("recorded_at", "state", "closed_at", "closed_reason")}
            if mine == theirs:
                return existing
            raise StoreError(
                f"{ref} already has a binding and it cannot be replaced. A "
                f"thread is created for one Item; changing which Item it is for "
                f"in place would rewrite the record that says what the turns "
                f"already taken were about. Create a session for the Item you "
                f"mean")
        self._data["bindings"][str(ref)] = record
        self._flush()
        return record

    def get_binding(self, ref: str) -> dict | None:
        """What this thread is for, or None if nothing ever said.

        None rather than an empty record, and the caller must not read it as
        "unconstrained". `runtime/coordination.py` refuses on None, because
        "nobody stated a purpose" and "any purpose is allowed" are opposite
        readings and only one of them is safe.
        """
        return self._data["bindings"].get(str(ref))

    def bindings(self) -> dict:
        return dict(self._data["bindings"])

    def close_binding(self, ref: str, *, at: str, reason: str | None = None) -> dict:
        """Close a thread for new work. Never a deletion, and never a rebind.

        The record stays. Closing is what makes
        `THREAD_CLOSED_FOR_NEW_WORK` answerable months later, and a store that
        dropped the binding on closure would leave the closed thread looking
        exactly like a thread nobody ever created one for.
        """
        existing = self._data["bindings"].get(str(ref))
        if existing is None:
            raise StoreError(
                f"{ref} has no binding to close; closing a thread that was never "
                f"bound would record a closure of something ServerOS does not "
                f"hold a statement about")
        updated = dict(existing)
        updated["state"] = "CLOSED"
        updated["closed_at"] = str(at)
        if reason:
            updated["closed_reason"] = str(reason)
        self._data["bindings"][str(ref)] = updated
        self._flush()
        return updated

    def replace(self, record: dict, *, kind: str) -> dict:
        """Overwrite one record by ref. Used by consumption, and only by it.

        `binding` is deliberately absent from `collections` below: this is the
        one method in this store that overwrites a record wholesale, and a
        binding that could be handed to it would have an immutability rule that
        a caller reaches around.
        """
        collections = {"context": "contexts", "approval": "approvals"}
        if kind not in collections:
            raise StoreError(f"unknown record kind {kind!r}")
        key = {"context": "context_ref", "approval": "approval_ref"}[kind]
        ref = record.get(key)
        if not ref:
            raise StoreError(f"a {kind} without a {key} cannot be replaced")
        self._data[collections[kind]][ref] = record
        self._flush()
        return record

    # -- maintenance -------------------------------------------------------

    def sweep(self, *, before: str) -> dict:
        """Drop records whose window closed before `before`.

        Called by the daemon rather than on every read, and reported rather
        than silent. A store that prunes on read has a read whose cost depends
        on how long the machine has been up, and a prune nobody is told about
        is a deletion that appears in no log.

        Bindings are never swept, and that is not an oversight. Contexts and
        approvals are windows; a binding has no window, because "what was this
        thread for" is a question asked about a conversation that already
        happened. Sweeping it would make the closures this store exists to
        remember indistinguishable from threads nobody ever bound — which is
        precisely the reading that let the failure §17 was written for happen.
        """
        gone = {"contexts": [], "approvals": []}
        for ref, record in list(self._data["contexts"].items()):
            if str(record.get("expires_at") or "") and str(record["expires_at"]) < before:
                gone["contexts"].append(ref)
                del self._data["contexts"][ref]
        for ref, record in list(self._data["approvals"].items()):
            # A consumed approval is kept past its window: it is the audit
            # record of a decision somebody made, and deleting it would make
            # "who approved this" unanswerable for every action older than the
            # window.
            if record.get("state") == "CONSUMED":
                continue
            if str(record.get("expires_at") or "") and str(record["expires_at"]) < before:
                gone["approvals"].append(ref)
                del self._data["approvals"][ref]
        if gone["contexts"] or gone["approvals"]:
            self._flush()
        return gone

    def summary(self) -> dict:
        """Counts, for a status surface. Never the records themselves."""
        approvals = self._data["approvals"]
        by_state: dict[str, int] = {}
        for record in approvals.values():
            state = str(record.get("state") or "UNKNOWN")
            by_state[state] = by_state.get(state, 0) + 1
        bindings = self._data["bindings"]
        return {"schema": SCHEMA, "path": str(self.path),
                "contexts": len(self._data["contexts"]),
                "approvals": len(approvals),
                "approvals_by_state": by_state,
                "bindings": len(bindings),
                "bindings_open": sum(
                    1 for b in bindings.values()
                    if str(b.get("state") or "OPEN").upper() != "CLOSED"),
                "bindings_by_role": {
                    role: sum(1 for b in bindings.values()
                              if b.get("session_role") == role)
                    for role in sorted({str(b.get("session_role") or "UNKNOWN")
                                        for b in bindings.values()})}}
