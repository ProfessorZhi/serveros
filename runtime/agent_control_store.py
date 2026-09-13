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
            return {"schema": SCHEMA, "contexts": {}, "approvals": {}}
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

    def replace(self, record: dict, *, kind: str) -> dict:
        """Overwrite one record by ref. Used by consumption, and only by it."""
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
        return {"schema": SCHEMA, "path": str(self.path),
                "contexts": len(self._data["contexts"]),
                "approvals": len(approvals),
                "approvals_by_state": by_state}
