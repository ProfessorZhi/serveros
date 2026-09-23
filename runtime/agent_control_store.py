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
which is the reason every collection here shares a file.

Why standing delegations are stored here (§12, P6)
--------------------------------------------------
`intake/focus_source.py` records in its own words that nothing on this node
mints a grant and that "there is no grant store under `state/serveros/`". That
sentence was true until this collection existed, and it is why every candidate
is excluded as `MISSING_OR_REVOKED_GRANT`: an objective nobody authorized may
not be admitted, and there was nowhere for an authorization to be.

The map `intake/daily_cycle.select_focus` is handed is the thing that decides
whether a cycle may carry an objective, so it cannot be a file somebody edited
— that is the one shape of authority this fabric refuses to accept, because
nothing distinguishes it from a file somebody edited. A grant recorded here is
the only thing that supplies one. `context/delegation.py` builds that map and
drops every grant whose `delegated_by` does not resolve to a trusted context
that `authorizes` right now, so a grant cannot outlive the delegation it
derives from whatever its own `expires_at` says.

Why a collection here and not a store of its own: a grant is the same kind of
thing as the other four — control-plane state a restart must not lose. A
delegation that vanished on restart would silently withdraw authority, and the
alternative to that is not "no authority" but "authority re-minted on demand",
which is authority nobody granted. This is §M's argument for selections, one
step further out.

The identity rule holds here too: the collection is keyed by `grant_ref`, and
the actor a grant was given to appears on the record as a field. There is no
`grants_for_actor()` and the omission is the same one as above — that method
would answer "what may this person do", which is policy.

Why admissions are stored here (P6, "原子准入")
-----------------------------------------------
P6 asks for one thing this store is uniquely able to give: "把相同 cycle/目标/版本
的选择、预算预留、lease/dispatch intent 放到现有权威事务/互斥范围" — the selection, the
budget it was admitted under, and the lease and dispatch intent it is about to
produce belong in ONE transaction.

They are three facts that only mean something together, and this is the only
scope in the fabric that already has a real transaction over durable state: one
document, one exclusive sidecar lock, one write. `runtime/lease_store.py` has
its own file per resource and its own `os.replace`; `runtime/bounded_invocation`
writes a receipt per action key. P6's own words are the rule for what to do
about that — "多系统间用可恢复幂等关系，不假装跨库事务" — so `confirm_admission`
records what the other two stores ANSWERED and never claims to have committed
anything on their behalf. The relation is resumable because each phase is
idempotent and `admit` replayed with the same inputs hands the caller back the
phase it left off at; it is not atomic across stores because it cannot be, and
a record that said otherwise would be the interesting kind of lie.

What the admission is NOT
-------------------------
Not a scheduler and not an executor. It records an intent, and every module
that could act on one disclaims acting in its own docstring for the same
reason: `runtime/bounded_invocation` is "not a scheduler", `intake/daily_cycle`
performs "no IO, model calls, Item writes or authorization minting". This
method mints nothing either — the grant it names must already be recorded, and
the selection it carries is the one `intake/daily_cycle.select_focus` produced
from an envelope `context/delegation.py` derived.

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
import hashlib
import json
import os
import re
import tempfile
from contextlib import contextmanager
from datetime import datetime
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

# The fields `revoke_grant` owns, and the reason `put_grant` refuses them is the
# reason `put_binding` refuses `state`: a grant ends by being revoked, and a
# caller who could write `revoked` on a put could also write it back to False
# and un-revoke a delegation by re-reporting it. The store writes the only value
# a new grant can have and nothing else may move it.
REVOCATION_FIELDS = ("revoked", "revoked_at", "revoked_reason")

# The three phases of one admission, in the only order they can happen. Each is
# a DIFFERENT store's answer being reported back, which is why they are three
# and not a boolean `done`:
#
#   INTENT_RECORDED  this store committed. Nothing else has happened yet.
#   LEASE_HELD       `runtime/lease_store.py#declare` answered, and its answer
#                    is what is recorded — not a claim that a lease exists.
#   DISPATCHED       `runtime/bounded_invocation.invoke_once` wrote a receipt
#                    under the action key, and the receipt's own fields are what
#                    is recorded.
#
# A phase is not a permission. `LEASE_HELD` does not authorize a dispatch; it
# records that the lease store answered one way rather than another, and the
# reader that wants to act on that reads the lease store itself.
ADMISSION_PHASES = ("INTENT_RECORDED", "LEASE_HELD", "DISPATCHED")

# The three budget keys `intake/daily_cycle.grant_reason` requires, cited rather
# than re-defined: that function is the authority on what a bounded budget IS,
# and a second list here would be a second definition that can disagree with it.
BUDGET_KEYS = ("wall_seconds", "max_attempts", "max_output_bytes")

# The three fields `runtime/lease_store.py#declare` is idempotent on, plus the
# resource it declares against. An admission intent that names fewer than these
# cannot be resumed, because a resume that guessed the missing one would declare
# a lease beside the one it meant.
LEASE_INTENT_FIELDS = ("resource", "scope", "holder")

# The phases a CALLER may confirm. `INTENT_RECORDED` is not among them: it is
# the phase `admit` itself writes, so a caller confirming it would be reporting
# this store's own act back to this store.
CONFIRMABLE_PHASES = ADMISSION_PHASES[1:]

# Every status `runtime/bounded_invocation.invoke_once` writes on its receipt,
# cited from that module rather than chosen here: the dispatch phase records a
# receipt the invoking helper wrote, so the only vocabulary it may accept is
# that helper's. `tests/test_admission.py` reads the writer and fails if this
# tuple and the statuses in that file stop agreeing — the same treatment
# `BINDING_ONLY_FIELDS` gets, for the same reason: a copy nobody checks is a
# copy that is wrong by the time somebody reads it.
RECEIPT_STATUSES = ("STARTED", "EXITED", "REFUSED", "TIMED_OUT", "OUTPUT_LIMIT",
                    "START_FAILED", "IO_FAILED")


class StoreError(RuntimeError):
    pass


def _intent_digest(record: dict, *, selection_digest: str, grant_ref: str,
                   reservation: dict, lease: dict, action_key: str) -> str:
    """The identity of one admission: cycle, objective, version and bounds.

    The four inputs are P6's own list — "相同 cycle/目标/版本" plus the budget the
    plan puts in the same transaction. Only these four go in, so a caller
    cannot change the identity by adding a field, and a resume that reproduces
    the same decision reproduces the same digest.

    `admitted_at` is deliberately NOT an input. A retried admission happens at
    a later instant and is still the same admission; including the time would
    make every retry a different intent and turn idempotency into a refusal.
    """
    return fingerprint({"selection_digest": str(selection_digest),
                        "grant_ref": str(grant_ref),
                        "reservation": {k: reservation[k] for k in BUDGET_KEYS},
                        "lease": {f: str(lease[f]) for f in LEASE_INTENT_FIELDS},
                        "action_key": str(action_key)})


def _instant(value) -> datetime | None:
    """A timestamp with a timezone, or None.

    Parsed here rather than imported from `intake/daily_cycle.instant`, which
    states the same rule: this module is the durable half of Agent Control and
    is loaded by path by callers that have no `intake` on their path, and the
    dozen other instant comparisons in this tree each parse their own for that
    reason. A stamp with no timezone yields None rather than being assumed into
    one: the window it would be compared against is a real interval, and the
    guess that made it fit would leave no trace of having been made.
    """
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo is not None else None


def _authorizing_grant(grants: dict, *, cycle: str, grant_ref: str,
                       admitted_at: str, reservation: dict, selection: dict) -> None:
    """Refuse an admission this store's own record of authority does not cover.

    Called from inside `admit`'s single mutation, with the `grants` mapping that
    is on disk under the lock. That placement is the whole point: every layer
    above reads the envelope OUTSIDE the lock. `work admit` reads
    `control.grants()`, composes a plan from it, and then writes — and a
    `work revoke` in another process lands in exactly that gap. A cycle admitted
    under a delegation that ended a moment ago is the state this closes, and no
    reader above could close it, because none of them is holding the lock.

    Five answers to one question: are the facts this record asserts still this
    store's own at the instant it is written? The grant exists HERE, it is not
    revoked, the instant is inside its window, the selection is inside the items
    it names, and the reservation is the bounds it declares.

    The first of those deliberately shares a word with
    `intake/daily_cycle.admission_plan`, which also refuses a missing grant, and
    the sentence says which operand each is about: the plan checks the envelope
    the CALLER handed it, this checks the store's record. Two refusals about
    different operands that read the same would send a reader to the wrong one.

    What it does NOT check, and why. The grant's `projects` and `scope_digests`
    are checked by `intake/daily_cycle.grant_reason` against the board's rows
    and this record carries neither; checking them here would mean persisting a
    board snapshot as authority, which is the second copy an admission exists to
    avoid. The `action_key` is not one of the grant's `actions` either — it is
    the receipt and lease key, and its vocabulary belongs to
    `runtime/bounded_invocation`.

    And this is not a second authorization decision:
    `context/delegation.derivation` says whether the delegation authorizes and
    `grant_reason` says whether the row is in scope. This says only that what
    the record names is what the store holds.
    """
    grant = grants.get(grant_ref)
    if not isinstance(grant, dict):
        raise StoreError(
            f"{cycle} is admitted under {grant_ref!r} and no grant by that name "
            f"has been recorded in this store. `intake/daily_cycle.admission_plan` "
            f"checks the envelope it is HANDED; this is the store's own record of "
            f"what was delegated, and an admission naming authority this store has "
            f"never held is one nobody granted")
    if grant.get("revoked") is not False:
        # `is not False`, the same reading `intake/daily_cycle.grant_reason`
        # takes: a grant whose revocation field is missing is not a grant known
        # to be live, and the permissive reading of an absent field is how a
        # revocation becomes invisible.
        raise StoreError(
            f"{grant_ref} was revoked at "
            f"{grant.get('revoked_at') or 'an unrecorded instant'} and {cycle} is "
            f"admitted after that. The revocation is this store's own record; a "
            f"cycle admitted under a delegation that has ended would be work "
            f"running on authority the store says is gone")
    admitted = _instant(admitted_at)
    if admitted is None:
        raise StoreError(
            f"{cycle} does not say when it was admitted, so the window it is "
            f"admitted under cannot be read against it. {grant_ref}'s window is "
            f"what makes 'was this authorized when it ran' answerable afterwards, "
            f"and an admission with no instant is one no window can be checked "
            f"for")
    valid_from, expires_at = _instant(grant.get("valid_from")), _instant(grant.get("expires_at"))
    if valid_from is None or expires_at is None:
        raise StoreError(
            f"{grant_ref} carries no readable window ({grant.get('valid_from')!r} "
            f"to {grant.get('expires_at')!r}), so there is nothing for {cycle} to "
            f"be inside. `intake/daily_cycle.grant_reason` answers the same grant "
            f"INVALID_GRANT_TIME for the same reason: a delegation with no interval "
            f"authorizes at every instant or at none, and neither is what was "
            f"delegated")
    if not valid_from <= admitted < expires_at:
        raise StoreError(
            f"{cycle} is admitted at {admitted_at}, which is outside {grant_ref}'s "
            f"window ({grant['valid_from']} to {grant['expires_at']}). A cycle "
            f"admitted before its delegation began or after it expired runs on "
            f"authority that was not current when it started")
    authorized = grant.get("items")
    outside = [str(item_id) for item_id in (selection.get("selected") or [])
               if not isinstance(authorized, list) or item_id not in authorized]
    if outside:
        raise StoreError(
            f"{cycle} selects {', '.join(outside)}, which {grant_ref} does not name "
            f"among its items. `intake/daily_cycle.grant_reason` refuses the same "
            f"row as ITEM_NOT_AUTHORIZED; a selection outside the delegation would "
            f"record a reservation for work the delegation does not cover")
    declared = grant.get("budget")
    declared = ({key: declared.get(key) for key in BUDGET_KEYS}
                if isinstance(declared, dict) else {})
    reserved = {key: reservation.get(key) for key in BUDGET_KEYS}
    if declared != reserved:
        raise StoreError(
            f"{cycle} reserves {json.dumps(reserved, sort_keys=True)} and "
            f"{grant_ref} declares {json.dumps(declared, sort_keys=True)}. The "
            f"reservation is the bounds this cycle runs under, COPIED from its "
            f"grant, and this is the line that keeps the copy from being a second "
            f"bound nobody declared: it is checked against its source at the "
            f"moment it is taken")


def fingerprint(value) -> str:
    """A stable digest of a JSON value, with no clock and no path in it.

    The same construction `intake/daily_cycle.fingerprint` uses, and for the
    same reason: two of these are compared as strings in a refusal message, so
    a digest that varied run to run would make the message unreadable exactly
    when somebody is reading it to find out what changed.
    """
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def lease_evidence_conflict(record: dict, evidence: dict) -> str | None:
    """Why this lease answer cannot belong to this admission, or None.

    The lease store's own answer is `{"act": ..., "record": ...}`, and the two
    things checked are the two that would make the answer about a DIFFERENT
    declaration: the act must be one that helper produces, and the resource the
    answer names must be the resource the intent named.
    """
    act = evidence.get("act")
    if act not in ("DECLARE", "REFRESH"):
        return (f"the lease evidence names act {act!r}. "
                f"`runtime/lease_store.declare` returns DECLARE or REFRESH, and "
                f"anything else is an answer about something other than a "
                f"declared reference")
    returned = evidence.get("resource")
    if str(returned or "") != record["lease"]["resource"]:
        return (f"the lease evidence names resource {returned!r} and this "
                f"admission's intent names {record['lease']['resource']!r}. A "
                f"lease held on one resource is not evidence about another")
    return None


def dispatch_evidence_conflict(record: dict, evidence: dict) -> str | None:
    """Why this receipt cannot belong to this admission, or None.

    The receipt's `action_key` is the join, and it is the only join there is:
    `runtime/bounded_invocation.invoke_once` writes one receipt per key, so a
    receipt under a different key is a different dispatch that happens to have
    been offered as evidence for this one.
    """
    returned = str(evidence.get("action_key") or "")
    if returned != record["action_key"]:
        return (f"the receipt names action key {returned!r} and this admission's "
                f"dispatch intent names {record['action_key']!r}. One receipt "
                f"per action key is that helper's rule, so this receipt is "
                f"evidence about a different dispatch")
    status = evidence.get("status")
    if status not in RECEIPT_STATUSES:
        return (f"the receipt's status is {status!r}; this records a receipt "
                f"`runtime/bounded_invocation.invoke_once` wrote, and a status "
                f"it does not write would be a claim about an execution nobody "
                f"performed. The ones it writes are {list(RECEIPT_STATUSES)}")
    return None


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
    """Control-plane state at rest, persisted as one small document.

    One file, not a directory of them, because more than one collection is
    written in the same transaction — an approval consumed and the context it
    was decided from, a binding and the selection that pointed at it — and two
    files that are written together are two files that can disagree after a
    crash. The six collections are `contexts`, `approvals`, `bindings`,
    `selections`, `grants` and `admissions`; each says in its own section above
    why it is here rather than in a store of its own.
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
                    "bindings": {}, "selections": {}, "grants": {},
                    "admissions": {}}
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
        data.setdefault("grants", {})
        data.setdefault("admissions", {})
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

    # -- standing delegations (§12, P6) ------------------------------------

    def put_grant(self, record: dict) -> dict:
        """Record a standing delegation. Write-once, on `grant_ref`.

        The scope, the budget and the window are the caller's; what this method
        decides is whether there is anything for them to be a scope ON. A grant
        names the trusted context it was delegated in — `delegated_by` — and a
        grant whose `delegated_by` names no context this store holds is refused.
        That refusal is the whole reason this collection exists rather than a
        directory of JSON files: authority with no root is authority nobody
        granted, and no later clock can give it one.

        What it does NOT check, deliberately: whether that context is still
        fresh, and whether the scope is one a cycle may carry. Both are
        questions about now, and this store has no clock — `authorizes` is
        asked by the reader that has one (`context/delegation.py`), and the
        scope is `intake/daily_cycle.grant_reason`'s. A grant recorded against
        a context that has since gone stale is therefore STORED and reported as
        not authorizing, which is a different answer from a refusal here and
        the one that preserves the record of what was delegated.

        Write-once for `put_binding`'s reason, arrived at from the other side:
        a grant is the envelope a cycle was admitted under, and replacing it in
        place would rewrite what work already done was authorized by. Extending
        a delegation is a new grant; ending one is `revoke_grant`.
        """
        ref = str(record.get("grant_ref") or "").strip()
        if not ref:
            raise StoreError(
                "a standing delegation without a grant_ref cannot be stored: it is "
                "what a row names to be admitted under, and a grant nobody can name "
                "is one no row can be admitted by")
        delegated_by = str(record.get("delegated_by") or "").strip()
        if not delegated_by:
            raise StoreError(
                "a standing delegation without a delegated_by cannot be stored. It "
                "is a scope on a trusted context, and a grant that does not say "
                "whose delegation it is is one that no longer has a root — which is "
                "the state this collection exists to keep out")
        smuggled = [f for f in REVOCATION_FIELDS
                    if f in record and record[f] != (False if f == "revoked" else None)]
        if smuggled:
            raise StoreError(
                f"a grant may not be stored carrying {', '.join(smuggled)}. A "
                f"delegation ends by revocation (`revoke_grant`), and a grant whose "
                f"revocation a put could write is one a second put could un-revoke")
        staged = {k: v for k, v in record.items() if k not in REVOCATION_FIELDS}
        with self._mutation():
            # Under the lock, so a context another writer reported a moment ago
            # is seen. Checked against the document on disk rather than against
            # what this instance loaded: a grant refused because its delegation
            # arrived since would be a refusal that depends on who wrote first.
            if str(delegated_by) not in self._data["contexts"]:
                raise StoreError(
                    f"no trusted context {delegated_by!r} has been reported to this "
                    f"store, so {ref!r} has nothing to derive from. A standing "
                    f"delegation is a scope on a context somebody was verified in; "
                    f"minting one against a context this store has never held is "
                    f"authority with no root")
            existing = self._data["grants"].get(ref)
            if existing is not None:
                theirs = {k: v for k, v in existing.items()
                          if k not in REVOCATION_FIELDS}
                # Compared WITHOUT the revocation fields on both sides: a
                # re-report of a delegation that has since been revoked is
                # still the same delegation, so it returns the record as it
                # stands rather than either refusing or un-revoking it.
                if theirs == staged:
                    return existing
                raise StoreError(
                    f"{ref} already has a grant and it cannot be replaced. A "
                    f"delegation is the envelope a cycle was admitted under; "
                    f"changing its scope in place would rewrite what the work "
                    f"already done was authorized by. Revoke it and record the "
                    f"one you mean")
            recorded = {**staged, "revoked": False}
            self._data["grants"][ref] = recorded
            self._flush()
            return recorded

    def get_grant(self, ref: str) -> dict | None:
        """One recorded delegation, or None if nothing ever said.

        None is not "ungranted authority for everything". The reader that hands
        this to a cycle (`context/delegation.py`) reports a miss as
        `MISSING_OR_REVOKED_GRANT`, because "nobody authorized this" and "any
        authority will do" are opposite readings and only one of them is safe.
        """
        return self._read()["grants"].get(str(ref))

    def grants(self) -> dict:
        return dict(self._read()["grants"])

    def revoke_grant(self, ref: str, *, at: str, reason: str | None = None) -> dict:
        """End a standing delegation. Never a deletion, and never an extension.

        The record stays, and it stays saying what it said. A revoked grant is
        what makes "this was authorized when the work started and is not now"
        answerable, and a store that dropped it on revocation would leave a
        withdrawal indistinguishable from a delegation that never existed —
        which is the reading that makes an audit of "who let this run"
        impossible.

        Revoking twice is idempotent and keeps the FIRST revocation time. The
        window a cycle's after-the-fact review is computed from is when the
        authority ended, not when somebody last mentioned it.
        """
        key = str(ref or "").strip()
        if not key:
            raise StoreError("revoking a grant needs the grant's ref")
        with self._mutation():
            existing = self._data["grants"].get(key)
            if existing is None:
                raise StoreError(
                    f"{key} has no grant to revoke; recording a revocation of "
                    f"something this store does not hold would make the withdrawal "
                    f"of a delegation look the same as a delegation that was never "
                    f"made")
            if existing.get("revoked") is True:
                return existing
            updated = dict(existing)
            updated["revoked"] = True
            updated["revoked_at"] = str(at)
            if reason:
                updated["revoked_reason"] = str(reason)
            self._data["grants"][key] = updated
            self._flush()
            return updated

    # -- admissions (P6, 原子准入) ------------------------------------------

    def admit(self, record: dict) -> dict:
        """Record one cycle's admission: selection, budget, lease and dispatch
        intent, in a single mutation.

        ONE `_mutation()` for all three, and that is the entire point of this
        method. P6 asks for the selection, the budget reservation and the
        lease/dispatch intent to be in one authoritative transaction, and the
        reason is what the three are for: an admission that recorded a
        selection and no intent would be a cycle nothing dispatches, and one
        that recorded an intent and no selection would be a dispatch of nothing
        in particular. Written as three mutations, the window between them is
        where a crash leaves a reservation nobody holds.

        Idempotent on the INTENT, not on the cycle. `intent_digest` is computed
        here from the four things the plan names — cycle, objective, version
        and bounds — so a retried admission is the same admission and returns
        the stored record with its phase. A second admission for the same
        `cycle_ref` with a DIFFERENT intent is refused rather than replacing
        it: the caller is asking to make the cycle about something else, and
        the record being replaced is the only evidence of what it was admitted
        for. That refusal is also what makes two concurrent schedulers safe —
        the loser is told it lost, instead of both running.

        The action key is unique among OPEN admissions, for a reason that comes
        from `runtime/bounded_invocation` rather than from taste: that helper
        keys one receipt by `action_key` and REFUSES a second call under the
        same key with different input while REPLAYING the first when the input
        matches. So two open admissions sharing a key would not produce two
        dispatches — the second would silently receive the first's result and
        the receipt would say `replayed: True` about work it never did.
        """
        cycle = str(record.get("cycle_ref") or "").strip()
        if not cycle:
            raise StoreError(
                "an admission without a cycle_ref cannot be stored: the cycle is "
                "what a resume names, and an admission nobody can name again is a "
                "reservation that can never be released")

        selection = record.get("selection")
        if not isinstance(selection, dict):
            raise StoreError(
                f"{cycle} carries no selection. An admission is a decision about "
                f"what a cycle is for; without one it records that something was "
                f"admitted and not what")
        if not str(selection.get("selection_digest") or "").strip():
            raise StoreError(
                f"{cycle} names no selection_digest. That value comes from "
                f"`intake/daily_cycle.select_focus`, which computes it over the "
                f"selected rows and the observation they came from — it is the "
                f"idempotency key and this store will not mint one, because a "
                f"digest computed here would be a digest of whatever the caller "
                f"handed over")
        selected = selection.get("selected")
        if not isinstance(selected, list) or not selected:
            raise StoreError(
                f"{cycle} selected nothing, so there is no cycle to admit. "
                f"`select_focus` returns an empty `selected` for a stale board "
                f"and for a board whose every row was excluded; admitting that "
                f"would record a reservation for work no objective was chosen "
                f"for")

        grant_ref = str(record.get("grant_ref") or "").strip()
        if not grant_ref:
            raise StoreError(
                f"{cycle} names no grant_ref. Every admitted cycle carries the "
                f"delegation it runs under; a cycle admitted without one is work "
                f"nobody authorized, which is the state this store exists to make "
                f"impossible to record")
        reservation = record.get("reservation")
        if not isinstance(reservation, dict):
            raise StoreError(
                f"{cycle} carries no reservation. The bounds a cycle runs under "
                f"are read from its grant at admission time and recorded with the "
                f"selection that was admitted under them")
        missing = [k for k in BUDGET_KEYS
                   if type(reservation.get(k)) is not int or reservation[k] <= 0]
        if missing:
            raise StoreError(
                f"{cycle}'s reservation is missing a positive "
                f"{', '.join(missing)}. `intake/daily_cycle.grant_reason` requires "
                f"the same three keys of a grant, and a reservation that bounded "
                f"less than the grant would admit a cycle under limits nobody "
                f"declared")

        lease = record.get("lease")
        if not isinstance(lease, dict):
            raise StoreError(
                f"{cycle} carries no lease intent. The resource this cycle is "
                f"about to declare, and the scope and holder it will declare it "
                f"under, are what a resume needs — `runtime/lease_store.declare` "
                f"is idempotent on (scope, holder), so a resume that knew all "
                f"three would refresh its own reference and one that guessed "
                f"would declare beside it")
        lease_missing = [f for f in LEASE_INTENT_FIELDS
                         if not str(lease.get(f) or "").strip()]
        if lease_missing:
            raise StoreError(
                f"{cycle}'s lease intent is missing {', '.join(lease_missing)}")

        action_key = str(record.get("action_key") or "").strip()
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", action_key):
            raise StoreError(
                f"{action_key!r} is not an action key. "
                f"`runtime/bounded_invocation.invoke_once` accepts "
                f"[a-zA-Z0-9_-]{{1,100}} and refuses anything else, so an "
                f"admission naming a key that helper will reject is an intent "
                f"nothing can dispatch")

        intent_digest = _intent_digest(record, selection_digest=selection["selection_digest"],
                                       grant_ref=grant_ref, reservation=reservation,
                                       lease=lease, action_key=action_key)

        with self._mutation():
            existing = self._data["admissions"].get(cycle)
            if existing is not None:
                if existing.get("intent_digest") == intent_digest:
                    return {**existing, "replayed": True}
                raise StoreError(
                    f"{cycle} is already admitted under a different intent "
                    f"({existing.get('intent_digest')} recorded, {intent_digest} "
                    f"offered). A cycle whose objective, version or bounds changed "
                    f"is a different admission, and replacing this one would "
                    f"discard the only record of what was admitted and of the "
                    f"reservation it holds. Retire it first, or admit the new "
                    f"intent under its own cycle_ref")

            # AFTER the replay branch, and BEFORE the action key. The order is
            # the message's. A replay returns the record of what was admitted at
            # the time, so re-judging it against a grant revoked since would
            # answer "this is not authorized" to a caller who asked whether it
            # had already been admitted — a different question with a different
            # answer. And authority comes before the key because a caller told
            # its key was taken would go and change the key, then come back with
            # authority that is still gone.
            _authorizing_grant(self._data["grants"], cycle=cycle, grant_ref=grant_ref,
                               admitted_at=str(record.get("admitted_at") or ""),
                               reservation=reservation, selection=selection)

            holder = next((ref for ref, other in self._data["admissions"].items()
                           if other.get("action_key") == action_key
                           and not other.get("retired_at")), None)
            if holder is not None:
                raise StoreError(
                    f"action key {action_key!r} is already held by the open "
                    f"admission {holder!r}. `runtime/bounded_invocation` keys one "
                    f"receipt by that value and replays it for equal input, so a "
                    f"second admission under it would not dispatch anything — it "
                    f"would receive the first cycle's result and the receipt would "
                    f"say `replayed: True` about work it never did")

            stored = {
                "cycle_ref": cycle,
                "intent_digest": intent_digest,
                "selection_digest": str(selection["selection_digest"]),
                "selection": dict(selection),
                "grant_ref": grant_ref,
                "reservation": {k: reservation[k] for k in BUDGET_KEYS},
                "lease": {f: str(lease[f]) for f in LEASE_INTENT_FIELDS},
                "action_key": action_key,
                "phase": ADMISSION_PHASES[0],
                "admitted_at": str(record.get("admitted_at") or ""),
                "confirmations": {},
                "retired_at": None,
                "outcome": None,
            }
            self._data["admissions"][cycle] = stored
            self._flush()
            return {**stored, "replayed": False}

    def get_admission(self, cycle_ref: str) -> dict | None:
        """One cycle's admission as it stands, or None if it was never made.

        None is a state and not a failure: most cycles have no admission,
        because most of what a board shows is neither authorized nor eligible.
        A caller that cannot tell that from a corrupted record would report
        both as a store problem.
        """
        return self._read()["admissions"].get(str(cycle_ref))

    def admissions(self) -> dict:
        return dict(self._read()["admissions"])

    def confirm_admission(self, cycle_ref: str, *, phase: str, evidence: dict,
                          at: str) -> dict:
        """Record one other store's answer, and advance one phase.

        This is the "可恢复幂等关系" and not a distributed commit. The lease
        and the receipt live in stores this one cannot lock and must not
        pretend to: `runtime/lease_store.py` writes one file per resource under
        its own namespace and `runtime/bounded_invocation` writes one per action
        key, each with its own `os.replace`. So the caller performs the step
        in the other store and reports back what THAT store said, and this
        records it. A crash between the two leaves an admission whose phase
        names exactly which half is done, and the resume is `admit` replayed
        (which is idempotent) followed by the same step again (which is
        idempotent in both other stores).

        Phases advance one at a time and never backwards. A re-report of the
        current phase is idempotent when the evidence is equal and refused when
        it is not — two dispatches under one action key with different inputs
        is the case `invoke_once` itself refuses, and this is the same refusal
        one layer up.
        """
        key = str(cycle_ref or "").strip()
        if phase not in CONFIRMABLE_PHASES:
            raise StoreError(
                f"{phase!r} is not a phase a caller confirms; the two are "
                f"{list(CONFIRMABLE_PHASES)}, in that order, and each is a "
                f"different store's answer. {ADMISSION_PHASES[0]} is what `admit` "
                f"itself writes — a caller reporting this store's own act back to "
                f"this store is not evidence of anything")
        if not isinstance(evidence, dict) or not evidence:
            raise StoreError(
                f"confirming {key!r} at {phase} needs the evidence that store "
                f"produced. A phase advanced on a caller's word alone would "
                f"record that something happened without recording what said so")
        with self._mutation():
            record = self._data["admissions"].get(key)
            if record is None:
                raise StoreError(
                    f"{key} has no admission. A confirmation is a report about "
                    f"an admission this store made; recording one for a cycle it "
                    f"never admitted would put a lease and a receipt on a cycle "
                    f"with no selection behind them")
            if record.get("retired_at"):
                raise StoreError(
                    f"{key} was retired at {record['retired_at']}. A retired "
                    f"admission is what the cycle did, and a confirmation "
                    f"arriving after that would rewrite the ending of a cycle "
                    f"already accounted for")
            current = record.get("phase")
            index = ADMISSION_PHASES.index(phase)
            current_index = ADMISSION_PHASES.index(current)
            if index < current_index:
                raise StoreError(
                    f"{key} is at {current} and this reports {phase}. Phases do "
                    f"not move backwards: the intent was already recorded, and "
                    f"accepting this would drop the evidence that came with it")
            if index > current_index + 1:
                raise StoreError(
                    f"{key} is at {current} and this reports {phase}, one phase "
                    f"past the next. The phase in between is another store's "
                    f"answer and nobody has reported it, so this would record a "
                    f"dispatch under a lease this store was never told about")
            recorded = record.setdefault("confirmations", {}).get(phase)
            if recorded is not None:
                if recorded == evidence:
                    return {**record, "replayed": True}
                raise StoreError(
                    f"{key} already reports {phase} with different evidence. "
                    f"Two answers from one store about one step is a question "
                    f"for a person, and picking the newer one here would hide "
                    f"the disagreement rather than record it")
            conflict = {"LEASE_HELD": lease_evidence_conflict,
                        "DISPATCHED": dispatch_evidence_conflict}[phase](record, evidence)
            if conflict:
                raise StoreError(f"{key} reports {phase} but {conflict}")
            record["confirmations"][phase] = dict(evidence)
            record["phase"] = phase
            record["confirmed_at"] = str(at)
            self._data["admissions"][key] = record
            self._flush()
            return {**record, "replayed": False}

    def retire_admission(self, cycle_ref: str, *, at: str,
                         outcome: str | None = None) -> dict:
        """Close an admission, releasing the reservation it held.

        Idempotent, and it keeps the FIRST retirement. The question a retired
        admission answers is when the cycle stopped holding its reservation,
        and a second call that moved that instant would move it to whenever
        somebody last mentioned the cycle — the same defect `revoke_grant` is
        written to avoid, one collection over.

        Retiring is not un-admitting. The record stays, and it stays saying
        what it was admitted for: `intake/daily_cycle.select_focus` computes a
        cycle's after-the-fact review from what was selected, and a store that
        dropped the record would make a cycle that ran indistinguishable from
        one that was never admitted.
        """
        key = str(cycle_ref or "").strip()
        if not key:
            raise StoreError("retiring an admission needs the cycle's ref")
        with self._mutation():
            record = self._data["admissions"].get(key)
            if record is None:
                raise StoreError(
                    f"{key} has no admission to retire; closing one this store "
                    f"does not hold would make a released reservation look like "
                    f"a reservation that was never taken")
            if record.get("retired_at"):
                return record
            record["retired_at"] = str(at)
            if outcome:
                record["outcome"] = str(outcome)
            self._data["admissions"][key] = record
            self._flush()
            return record

    def replace(self, record: dict, *, kind: str) -> dict:
        """Overwrite one record by ref. Used by consumption, and only by it.

        `binding` and `grant` are deliberately absent from `collections` below:
        this is the one method in this store that overwrites a record
        wholesale, and either of those handed to it would have an immutability
        rule that a caller reaches around.
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

        Grants are never swept, and this one is the sharpest of the three. A
        grant whose window has closed is the record of what work was delegated,
        by whom, and until when — and a cycle's envelope is read back later
        precisely to answer that. Sweeping it would leave a grant that expired
        and a grant that never existed looking the same, and the reader that
        drops it would report the same reason for both. Nothing is lost by
        keeping it: `context/delegation.py` re-checks the window and the
        delegation on every read, so an old grant authorizes nothing.

        Admissions are never swept either, and the reason is sharper still:
        `retired_at` is the field that frees a reservation, so a sweep that
        dropped a retired admission would drop a released reservation — which
        is the state nothing is waiting on — while keeping every open one, if it
        swept by age at all. A sweep that dropped an OPEN admission would free
        a reservation whose cycle may still be running, and the action key it
        holds would become available to a second admission that would then
        dispatch a different cycle under a key `invoke_once` has already
        answered for. Release here is `retire_admission` and nothing else,
        because it is an act with a caller, not an age.
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
                "selections": len(data["selections"]),
                "grants": len(data["grants"]),
                # Counted rather than listed, and counted HERE rather than in
                # the reader: how many delegations have been withdrawn is a
                # fact about the register. Which of them still authorize is a
                # question for the clock, and this store has none.
                "grants_revoked": sum(1 for g in data["grants"].values()
                                      if g.get("revoked") is True),
                "admissions": len(data["admissions"]),
                # Open means holding a reservation and an action key. Reported
                # as a count and not as a list, like everything else here: which
                # cycles are open is a question for a reader that can name them,
                # and `serverosctl work admissions` is that reader.
                "admissions_open": sum(1 for a in data["admissions"].values()
                                       if not a.get("retired_at")),
                "admissions_by_phase": {
                    phase: sum(1 for a in data["admissions"].values()
                               if a.get("phase") == phase and not a.get("retired_at"))
                    for phase in ADMISSION_PHASES}}
