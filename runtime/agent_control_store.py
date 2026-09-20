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

Selections are keyed by `conversation_ref` and that is not an exception to the
rule above, for the reason the rule exists: a conversation is not a person. The
conversation is a place a message arrives, several people can be in one, and
reading it as an identity would be the same mistake one step removed. The
identity a selection would carry is `UNVERIFIED` today — there is nothing
verified to key on, and the store says so rather than leaving the field out.

Why selections are stored here (§M)
-----------------------------------
§M's `session.select` is the one console operation no provider serves: ServerOS
decides which session a conversation is currently addressing, and no BotMux
session has an opinion about it. It has to live somewhere that survives a
restart, and the two candidate homes were a fourth store or this one. This one,
because a selection is the same kind of thing as the other three collections —
control-plane state that a restart must not lose — and a second store written
by the same code path would be a second thing that can disagree after a crash,
which is the reason the first three share a file.

Several writers, one record at a time
-------------------------------------
`serverosd` holds one of these for its whole lifetime and serves one connection
at a time — it accepts, serves and returns before it accepts again — so the
ordinary deployment has one writer and never has to think about this. The store
does not depend on that being true. Two properties make a second writer safe
rather than merely unlikely:

  every mutation takes an exclusive lock on a sidecar file and RE-READS the
  document under it, so a mutation is applied to what is on disk and not to
  whatever this process last happened to see. Without the re-read, a writer
  that loaded before another wrote overwrites the other's record with a
  document that never contained it.

  every read goes back to the file, so a record another writer committed is
  visible here. A reader that answered from memory would not see it, and for
  the reader an unseen write is a lost write.

Neither property held before, and the gap was not visible as a race: it shows
up as a CONSUMED approval — the audit record of what a person approved —
disappearing, because a second writer's document did not carry it. `_atomic_write`
guarantees that one write is all-or-nothing, which is a different property from
two writers not losing each other's records.

The other half of the boundary is stated rather than assumed: the lock excludes
writers that come through this module, and `fleet/state-root-contract.yaml`
names this module as the namespace's only producer — so that is every writer
there is. A program that edited the JSON itself would not be excluded by any
lock this file could take.
"""
from __future__ import annotations

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

SCHEMA = "serveros/agent-control-store.v1"

# The sidecar a write lock is taken on: the store's own name with this appended,
# beside it rather than in a `locks/` directory. `locks/` already means something
# else in this root — a HOLDER'S CLAIM about a lease, recorded in a file — and a
# reader who found a lock file there would read this one as that claim. It is a
# kernel lock, it exists only while a process is inside a mutation, and its
# standing in the file tree says nothing.
WRITE_LOCK_SUFFIX = ".lock"


def lock_path(path: Path | str) -> Path:
    """Where the write lock for `path` lives. Never `path` itself."""
    return Path(str(path) + WRITE_LOCK_SUFFIX)


@contextmanager
def _exclusive(lock: Path):
    """Hold the write lock for the duration of a mutation.

    Why the lock is not taken on the store file, which is the obvious target and
    the wrong one:

    `_atomic_write` finishes with `os.replace`, so the store is a NEW inode after
    every write. A lock is held on an inode. Two writers that each locked the
    store file could therefore each be holding a lock on a different inode and
    neither would be excluded — the exclusion would look like it worked and would
    be absent exactly when two writers overlapped.

    Why the sidecar is never unlinked: a writer that removed it while another
    held the lock would let a third writer create a fresh file and lock that, and
    two writers would be inside this store at once. The file staying is cheap;
    the race is not.

    Why a leftover sidecar does not block anybody: the kernel drops this lock
    when the holder exits, however it exits. That is the whole difference from a
    lock file whose EXISTENCE is the lock — the reading `runtime/state_root.py`
    warns about, where "a lock that outlives its holder then reads as an occupied
    resource". Here a file left by a killed writer is a file and nothing more.
    """
    lock.parent.mkdir(parents=True, exist_ok=True)
    handle = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
    finally:
        os.close(handle)


# The fields that make a record a THREAD BINDING and never a selection. §M says
# selecting a session is not moving a thread to another Item, and this tuple is
# how that sentence is spent: `put_selection` refuses a record carrying one.
# Kept equal to `runtime/human_console.py#THREAD_BINDING_FIELDS` by a test.
BINDING_ONLY_FIELDS = ("session_role", "bound_item_ref")


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
        # Whether THIS instance has written the store. It is what separates a
        # file that was never there — an ordinary node where nothing has been
        # recorded yet — from a file that was there and is gone. See `_load`.
        self._wrote = False
        # Whether a mutation is in progress. `_flush` refuses without it, which
        # is what makes "every write holds the lock" structural.
        self._writing = False
        self._data = self._load()

    @property
    def lock_path(self) -> Path:
        """The sidecar this store takes its write lock on. Not the store."""
        return lock_path(self.path)

    # -- loading -----------------------------------------------------------

    def _load(self) -> dict:
        if not self.path.exists():
            if self._wrote:
                # An absent file is not read as an empty store once this process
                # has put something in it. The two are the same bytes on disk and
                # opposite answers: "nothing has been recorded here" versus "what
                # was recorded here has been removed". Answering the first would
                # report every approval and every binding as never having
                # existed, which is the reading the corrupt-file branch below
                # already refuses one step earlier.
                raise StoreError(
                    f"{self.path} has been removed. This process has written it, "
                    f"so its absence is not a node where nothing is recorded — it "
                    f"is a register that was taken away while ServerOS was "
                    f"running. An empty store would say every approval it held "
                    f"had never been granted.")
            return {"schema": SCHEMA, "contexts": {}, "approvals": {},
                    "bindings": {}, "selections": {}}
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
        data.setdefault("selections", {})
        data["schema"] = SCHEMA
        return data

    def _read(self) -> dict:
        """The document as it is on disk now, which is what every reader gets.

        Not the copy this instance loaded earlier. `serverosd` holds one store
        for its whole lifetime, so its memory is precisely the copy that goes
        stale the moment anything else writes — and with the write side now
        serialized, a write a reader cannot see is a write that did not happen
        as far as that reader is concerned. The document is small and this is
        one `json.loads`; there is no cache here to invalidate, and so none to
        be wrong.
        """
        self._data = self._load()
        return self._data

    @contextmanager
    def _mutation(self):
        """Hold the write lock, re-read, and let the caller change the document.

        The re-read is the fix rather than a precaution. A mutation applied to
        whatever copy this instance was holding is a mutation that silently
        deletes every record written since that copy was taken: the caller
        writes a document that never contained them. Under the lock, what the
        caller changes is what is on disk.

        The lock is released by leaving the block, including by an exception —
        a refusal reached after the mutation started must not leave the store
        locked for the rest of the process's life.
        """
        with _exclusive(self.lock_path):
            self._data = self._load()
            self._writing = True
            try:
                yield
            finally:
                self._writing = False

    def _flush(self) -> None:
        """Write the document. Only from inside a mutation.

        Structural rather than a rule a later method has to remember, because
        the failure of forgetting is invisible: a write that skipped the lock
        would quietly drop another writer's records, and nobody sees that until
        the action an approval authorized is asked about and the approval is
        not there.
        """
        if not self._writing:
            raise StoreError(
                "this store was written outside a mutation. Every write takes "
                "the write lock and re-reads first (`_mutation`), because a "
                "write that skips either one drops whatever another writer "
                "recorded in between")
        _atomic_write(self.path, self._data)
        self._wrote = True

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
        with self._mutation():
            self._data["contexts"][ref] = record
            self._flush()
        return record

    def get_context(self, ref: str) -> dict | None:
        return self._read()["contexts"].get(str(ref))

    def contexts(self) -> dict:
        return dict(self._read()["contexts"])

    def put_approval(self, record: dict) -> dict:
        ref = record.get("approval_ref")
        if not ref:
            raise StoreError("an approval without an approval_ref cannot be stored")
        with self._mutation():
            self._data["approvals"][ref] = record
            self._flush()
        return record

    def get_approval(self, ref: str) -> dict | None:
        return self._read()["approvals"].get(str(ref))

    def approvals(self) -> dict:
        return dict(self._read()["approvals"])

    def for_actor_action(self, *, actor_ref: str, action: str) -> list[dict]:
        """Every approval held for one actor and one action.

        The only query in this module, and it takes an actor as a FILTER rather
        than as a key. There is deliberately no `approvals_for_actor()` and no
        `contexts_for_actor()`: those two signatures are the whole difference
        between asking "was this approved" and asking "what may this person
        do", and the second question belongs to policy, which this module is
        not.
        """
        return [a for a in self._read()["approvals"].values()
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
        with self._mutation():
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
        return self._read()["bindings"].get(str(ref))

    def bindings(self) -> dict:
        return dict(self._read()["bindings"])

    def close_binding(self, ref: str, *, at: str, reason: str | None = None) -> dict:
        """Close a thread for new work. Never a deletion, and never a rebind.

        The record stays. Closing is what makes
        `THREAD_CLOSED_FOR_NEW_WORK` answerable months later, and a store that
        dropped the binding on closure would leave the closed thread looking
        exactly like a thread nobody ever created one for.
        """
        with self._mutation():
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

    # -- conversation selections (§M) --------------------------------------

    def put_selection(self, record: dict) -> dict:
        """Record which session a conversation is currently addressing.

        **This one is deliberately NOT write-once**, and the difference from
        `put_binding` one screen up is the whole reason both can live in the
        same file. A binding says what a thread is FOR, and §17 requires that
        to be immutable, so a second put is a refusal. A selection says what a
        person is looking at right now, and moving it is the operation: §M's
        `session.select` exists precisely so that choosing a different session
        is possible, and a store that refused the second one would make the
        operation a one-shot and leave the console unable to follow a person
        who switched.

        Immutability is not lost by this; it is somewhere else. The fields
        §M forbids a selection to carry — an Item, or a role — are not in the
        record and are not in any record this method can be handed, because
        `runtime/human_console.py#selection_record` builds them and its
        `SELECTION_FIELDS` and `THREAD_BINDING_FIELDS` are disjoint. So the
        overwrite here can move a person's attention and cannot move a thread.
        """
        conversation = str(record.get("conversation_ref") or "").strip()
        if not conversation:
            raise StoreError(
                "a selection without a conversation_ref cannot be stored: it is "
                "a pointer from a conversation, and a pointer from nowhere can "
                "never be found by the reader who needs it")
        if not str(record.get("session_ref") or "").strip():
            raise StoreError(
                "a selection without a session_ref cannot be stored; storing it "
                "would make 'this conversation selected nothing' and 'this "
                "conversation selected something unnamed' the same record")
        # Refused rather than stripped. A caller that put an Item on a
        # selection is trying to do the thing §M forbids, and silently dropping
        # the field would let them believe it was recorded.
        #
        # The two names are spelled here rather than imported from
        # `runtime/human_console.py`, because the store is the lower layer and
        # importing upward to validate would invert that. The copy is not left
        # to drift: `tests/test_human_console.py` asserts this tuple EQUALS
        # `human_console.THREAD_BINDING_FIELDS`, so a field added to the console
        # makes this gate red rather than quietly weaker.
        smuggled = [f for f in BINDING_ONLY_FIELDS if f in record]
        if smuggled:
            raise StoreError(
                f"a selection may not carry {', '.join(smuggled)}. §M: selecting "
                f"a session is choosing which session this conversation "
                f"addresses, and it is not changing a thread's Item or its role "
                f"— those belong to the binding, which this record cannot reach")
        with self._mutation():
            self._data["selections"][conversation] = record
            self._flush()
        return record

    def get_selection(self, conversation_ref: str) -> dict | None:
        """What this conversation last selected, or None if it never has.

        None is a state and not a failure: a conversation that has selected
        nothing is where every console starts. The caller distinguishes it from
        a selection that has gone stale by asking
        `runtime/human_console.py#selection_axis`, which is the only reader that
        compares a stored selection against live sessions — this method does
        not, because a store that re-checked the world on every read would need
        the world.
        """
        return self._read()["selections"].get(str(conversation_ref))

    def selections(self) -> dict:
        return dict(self._read()["selections"])

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
        with self._mutation():
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

        Selections are never swept either, for a smaller version of the same
        reason. A selection is a pointer, and a pointer is not a window: the
        session it names may be long gone and the record is still the true
        answer to "what did this conversation last address". Sweeping it would
        make a conversation that once chose something indistinguishable from
        one that never did, and `human_console.selection_axis` reports that
        difference as `STALE` versus `NONE` — which it cannot do if this method
        has already deleted the evidence.
        """
        gone = {"contexts": [], "approvals": []}
        # Under the lock, and that matters more here than anywhere else in this
        # file: this method deletes. Applied to a stale copy it would not only
        # drop the records it means to drop, it would write back a document in
        # which every record another writer added since is missing.
        with self._mutation():
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
        data = self._read()
        approvals = data["approvals"]
        by_state: dict[str, int] = {}
        for record in approvals.values():
            state = str(record.get("state") or "UNKNOWN")
            by_state[state] = by_state.get(state, 0) + 1
        bindings = data["bindings"]
        return {"schema": SCHEMA, "path": str(self.path),
                "contexts": len(data["contexts"]),
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
                                        for b in bindings.values()})},
                "selections": len(data["selections"])}
