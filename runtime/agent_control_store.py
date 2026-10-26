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
checks legacy grants against their live actor context. New provider-signed
grants instead retain the exact approval and issuance time; their own scope,
window, revocation and execution budgets remain binding after actor expiry.

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

import errno
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import stat
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "serveros/agent-control-store.v1"


def _dispatch_attempts():
    """The dispatch-attempt kernel, under the name every sibling loader uses.

    Loaded lazily rather than imported at module scope: this store is imported by
    the kernel's own tests and by the daemon, and a module-level import here
    would make one of those the other's import-time dependency for no gain.
    """
    name = "serveros_runtime_dispatch_attempts"
    if name in sys.modules:
        return sys.modules[name]
    path = Path(__file__).resolve().with_name("dispatch_attempts.py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module

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
def _exclusive(lock: Path, *, owner_only: bool = False):
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
    handle = os.open(lock, os.O_CREAT | os.O_RDWR | (os.O_NOFOLLOW if owner_only else 0), 0o600)
    try:
        if owner_only:
            _validate_owner_lock(lock, handle)
            deadline = time.monotonic() + 2.0
            while True:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise LockUnavailable("owner delivery ledger is busy; no effect was admitted") from None
                    time.sleep(0.005)
        else:
            fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            if owner_only:
                _validate_owner_lock(lock, handle)
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
    finally:
        os.close(handle)


class LockUnavailable(RuntimeError):
    """Another holder has the lock. The answer about that lock, not a wait on it.

    Deliberately NOT a `StoreError`, and the difference is what a caller is
    supposed to do next. A `StoreError` is a fact about the request — the
    record conflicts, the authority is gone, the phase is wrong — and retrying
    it unchanged is a mistake. This is a fact about the moment: the caller
    asked a question whose answer is "someone else is inside", and asking again
    after they leave is exactly right. Folding the two together would give one
    exception a retry instruction that is right for half of its raisers.

    It is a `RuntimeError` because it is neither the caller's argument nor the
    filesystem being wrong: the arguments are fine and the file is fine, and
    the only thing standing in the way is somebody else's work.
    """


@contextmanager
def _exclusive_nowait(lock: Path):
    """Take the write lock, or report who has it — never wait for it.

    `_exclusive` above is right for a store mutation, and this variant exists
    because that one is wrong for the receipt lock in
    `runtime/bounded_invocation.invoke_once`. The two critical sections are not
    comparable: a mutation holds its lock for a read-modify-write of one small
    JSON file, so waiting for it is bounded by the time it takes to write JSON,
    while the receipt lock is held across the whole life of the command it
    guards — up to that admission's `wall_seconds`, plus the group kill and the
    reap. A caller that waits there is not waiting for a file; it is waiting for
    another scope's work to end, and the wait buys nothing, because the
    receipt's own `input_digest` check would give the same answer the instant
    the lock was granted. So the contention is reported and the caller turns it
    into a refusal it can explain and a person can act on.

    J07's fifth criterion is the requirement: 一项等待不冻其他合法 scope. The
    freeze in this fabric had exactly one mechanism and it was this one.

    `LOCK_NB` is the whole difference from `_exclusive`: with it the call raises
    instead of sleeping. Everything else is unchanged, including the property
    that matters most — the kernel drops the lock when the holder exits, however
    it exits, so a holder that was killed does not leave a lock anybody has to
    clear (`runtime/state_root.py` warns about the other reading, where a lock
    file's mere EXISTENCE is taken as an occupied resource).
    """
    lock.parent.mkdir(parents=True, exist_ok=True)
    handle = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            # EWOULDBLOCK and EAGAIN are the same number on Linux; EACCES is
            # what a lock held by another process reports in some other
            # filesystems. Both mean the same thing here and nothing else does,
            # so anything else is re-raised rather than renamed.
            if exc.errno not in (errno.EAGAIN, errno.EACCES):
                raise
            raise LockUnavailable(
                f"{lock} is held by a writer that has not finished") from None
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

#: Fields the STORE writes and a caller may never supply. `epoch` joins the
#: revocation fields for the same reason: an epoch a caller could choose is an
#: epoch a caller could choose to keep, which is exactly the generation fence
#: Work Engine #14's `OwnerVerifiedLease` asks for. Excluded from the
#: write-once comparison on both sides, so a re-report stays idempotent.
OWNER_WRITTEN_GRANT_FIELDS = REVOCATION_FIELDS + (
    "epoch", "os_principal",
    # The owner-policy binding is written by `issue_grant` from the trusted
    # owner resolver and the kernel peer of the issuing call. A standing
    # delegation (`put_grant`) cannot name it, for the same reason it cannot
    # name `os_principal`: a binding a caller could choose is one the caller
    # could claim for somebody else.
    "owner_policy_ref", "owner_policy_epoch",
    "owner_policy_issued_by", "owner_policy_issued_at",
    "actor_ref",
)

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

# The fields the STORE adds to a stored checkpoint, and therefore not part of what
# a caller wrote: the clock `put_checkpoint` was handed, the two fields a later
# epoch's write leaves behind on the record it takes over, and the six that a park
# and a lift write on the record they set down and take back up.
#
# `put_checkpoint` compares a caller's record against the stored one to decide
# whether a second call is a retry, and these are excluded from that comparison.
# Not a convenience: a superseded record must still answer a retry of the write
# that created it. Comparing them would make every replay of a superseded write a
# refusal — the caller would be told its own accepted write is not there, which is
# the one thing a replay must never say.
#
# THE PARK FIELDS ARE HERE FOR THAT SAME REASON AND ARRIVED LATER THAN THE OTHER
# THREE, which is why they are named rather than folded into a rule about «fields
# the writer did not write». A Parent that wrote a checkpoint and retried it after
# somebody had parked that record is retrying an ACCEPTED write, and a comparison
# that included `parked_at` would answer it with «this ref already holds a live
# checkpoint and it cannot be replaced» — a sentence about a second writer, sent
# to the writer whose record is the one being held.
CHECKPOINT_STORE_FIELDS = ("written_at", "superseded_by", "superseded_at",
                           "parked_at", "parked_by", "parked_reason",
                           "unparked_at", "unparked_by", "unparked_reason")


def is_parked(record: dict) -> bool:
    """Whether a checkpoint was set down by a person and not taken back up.

    TWO FIELDS AND ONE RULE. `parked_at` is on a record somebody set down;
    `unparked_at` is on one they lifted again. A record is parked exactly when the
    first is there and the second is not — which is why lifting a park WRITES a
    field instead of removing one. A store that deleted `parked_at` would leave a
    record indistinguishable from one nobody ever set down, and «why was this set
    down» is the part a later reader cannot reconstruct from the record's present
    state; both fields stay and the disposition is the reading of the pair.

    IT IS READ IN TWO MODULES AND THE FIELD NAMES ARE THE CONTRACT. This module
    reads it through this function — `live_checkpoints` to answer «who may be
    woken», `runtime/wake.plan` to say WHY a record is not a candidate. And
    `runtime/continuation.continuation_state` reads the same two names literally,
    because that module imports no other layer of this tree and
    `tests/test_continuation.py` asserts it. A rule in two copies is a rule that
    drifts, so the copies are not trusted to agree: the field names are the
    contract, and that same test file pins that the store's live set and the
    reader's answer agree for every record.
    """
    return bool(record.get("parked_at")) and not record.get("unparked_at")

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


def _validate_owner_lock(path: Path, handle=None):
    observed = path.lstat()
    if (not stat.S_ISREG(observed.st_mode) or observed.st_uid != os.geteuid()
            or observed.st_mode & 0o022 or observed.st_nlink != 1):
        raise StoreError("the owner ledger lock must be an unshared owner file")
    if handle is not None:
        opened = os.fstat(handle)
        if (not stat.S_ISREG(opened.st_mode) or opened.st_uid != os.geteuid()
                or opened.st_mode & 0o022 or opened.st_nlink != 1
                or (opened.st_ino, opened.st_dev) != (observed.st_ino, observed.st_dev)):
            raise StoreError("the owner ledger lock changed while it was opened")


class AttemptRefused(StoreError):
    """A dispatch attempt the store will not write, and the word for why.

    Its own type because the daemon has to return a reason the caller can act
    on: an identity failure (`UNTRUSTED_ACTOR`) and a transition failure
    (`INVALID_CONTEXT`) are different repairs, and reporting both as one would
    send the caller to fix the wrong thing.
    """

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code)
        self.detail = str(detail)
        super().__init__(detail)


def _actor_module():
    """`context/actor.py`, under the name the rest of ServerOS loads it as.

    `issue_grant` catches this module's `Rejected` two hundred lines below, and
    a second name for this file would be a second `Rejected` class: the refusal
    raised through the other copy would arrive as its parent rather than being
    caught. Named once, for every loader of this file -- see
    `context/delegation.py` for the measurement that motivated it.
    """
    name = "_serveros_context_actor"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            name, Path(__file__).resolve().parents[1] / "context/actor.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


def _owner_policy_module():
    """`runtime/owner_policy.py`, under the daemon's loader name."""
    name = "serveros_runtime_owner_policy"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            name, Path(__file__).with_name("owner_policy.py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


def os_principal_of(peer) -> str | None:
    """`uid:<n>` from a KERNEL peer record, or None when it cannot be read.

    The only evidence accepted here is the peer the socket read with
    `SO_PEERCRED`. A uid on the request is a claim about the caller written by
    the caller, and a `getuid()` is this process's principal even when someone
    else made the request — which is why the argument is the peer record and not
    a number.

    None is not "unbound is fine": every reader treats it as a refusal.
    """
    if not isinstance(peer, dict):
        return None
    uid = peer.get("uid")
    if isinstance(uid, bool) or not isinstance(uid, int) or uid < 0:
        return None
    return f"uid:{uid}"


def _grant_seconds(value) -> float | None:
    """A grant window stamp as epoch seconds, or None.

    Work Engine #14's `LeaseGrant` carries `valid_from` / `expires_at` as float
    epoch seconds; this store writes them as ISO-8601. Converting is a READ
    concern, and `float(<an ISO string>)` is precisely the failure the
    independent QA measured. A stamp with no timezone, or one nothing can parse,
    yields None: the window it would be compared against is a real interval, and
    an assumed offset would be a guess recorded as a fact.
    """
    parsed = _instant(value)
    return None if parsed is None else parsed.timestamp()


def verify_live_grant(grants, *, grant_ref, context, peer, action, project,
                      item_ref, expected_epoch=None, now=None) -> dict:
    """May `context`, from `peer`, act as this grant's principal, right now?

    PURE, and every check is a refusal when it cannot be answered. This is the
    trusted-parent read Work Engine #14's `OwnerVerifiedLease` needs: the context
    is resolved OUT OF BAND by the caller (never taken from a request field), the
    peer is the kernel's `SO_PEERCRED`, and the grant is re-read live.

    On success the `grant` is exactly one Work Engine #14 `LeaseGrant` — the ten
    fields, `items`/`actions` as tuples, an int epoch and epoch-seconds floats
    for the window — so a consumer constructs the dataclass from it rather than
    reshaping the answer at each call site.

    Two fields are never derivable at read time:

    * NO epoch is UNKNOWN and authorizes nothing; a record written before the
      field existed is not backfilled, because an epoch minted after the fact
      would be authority nobody granted.
    * NO `os_principal` is UNKNOWN for the same reason. The binding is written
      only by `issue_grant`, from the kernel peer of THAT call; this read
      compares it and never invents it.
    """
    def refuse(reason, because):
        return {"verified": False, "reason": reason, "because": because}

    if not isinstance(context, dict) or not str(context.get("context_ref") or "").strip():
        return refuse("UNTRUSTED_ACTOR",
                      "no trusted context was resolved for this call")
    os_principal = os_principal_of(peer)
    if os_principal is None:
        return refuse("UNTRUSTED_ACTOR",
                      "this call did not arrive with a kernel peer credential "
                      "(SO_PEERCRED), so the caller cannot be bound to an OS "
                      "principal. A uid in the payload is a claim, not evidence")
    now_text = str(now or "").strip()
    # RC-v2 merge resolution (#275 x #318/#307): keep #307's instant parse of
    # the verification time. The text comparison was a measured fail-open; an
    # unreadable instant is INVALID_CONTEXT, never a default.
    now_instant = _instant(now_text)
    if now_instant is None:
        return refuse(
            "INVALID_CONTEXT",
            f"the verification instant {now!r} is not a zoned timestamp; a "
            f"window cannot be checked against an instant that is not one")
    verdict = _actor_module().authorizes(context, now_text)
    if not verdict.get("authorizes"):
        return refuse("UNTRUSTED_ACTOR",
                      f"the presented context does not authorize a mutation: "
                      f"{verdict.get('because')}")

    key = str(grant_ref or "").strip()
    if not key:
        return refuse("INVALID_CONTEXT", "no grant_ref was named")
    grant = (grants or {}).get(key)
    if grant is None:
        return refuse("INVALID_CONTEXT", f"no grant {key!r} is recorded")
    if str(grant.get("delegated_by")) != str(context["context_ref"]):
        return refuse(
            "UNTRUSTED_ACTOR",
            f"grant {key!r} was delegated by {grant.get('delegated_by')!r}, and "
            f"this context is {context['context_ref']!r}. Authority is rooted in "
            f"the context that was verified, not in whoever presents the ref")
    if grant.get("revoked") is True:
        return refuse("POLICY_DENIED",
                      f"grant {key!r} was revoked at {grant.get('revoked_at')}")
    # RC-v2 merge resolution (#275 x #318/#307): parse each bound once as an
    # instant, compare instants (not strings), and keep the epoch-seconds
    # window required by Work Engine #14's LeaseGrant. Dropping either half
    # would lose a safety property: text comparison is a measured fail-open,
    # and a non-LeaseGrant return breaks the trusted-parent consumer.
    window = {}
    bounds = {}
    for field in ("valid_from", "expires_at"):
        if not grant.get(field):
            return refuse("INVALID_CONTEXT", f"grant {key!r} has no {field}")
        instant = _instant(grant[field])
        if instant is None:
            return refuse(
                "INVALID_CONTEXT",
                f"grant {key!r} has an unreadable {field} {grant[field]!r}. The "
                f"window must be a timezone-bearing ISO-8601 instant; a stamp "
                f"this read cannot place in time cannot be checked, and Work "
                f"Engine #14 consumes the window as epoch seconds")
        bounds[field] = instant
        window[field] = instant.timestamp()
    if now_instant < bounds["valid_from"]:
        return refuse("POLICY_DENIED",
                      f"grant {key!r} is not valid until {grant['valid_from']}")
    if now_instant >= bounds["expires_at"]:
        return refuse("POLICY_DENIED",
                      f"grant {key!r} expired at {grant['expires_at']}")

    epoch = grant.get("epoch")
    if epoch is None or isinstance(epoch, bool) or not isinstance(epoch, int):
        return refuse(
            "INVALID_CONTEXT",
            f"grant {key!r} records no epoch, so its authority is UNKNOWN. A "
            f"record written before the epoch existed is not backfilled: an epoch "
            f"minted after the fact would be authority nobody granted")
    if expected_epoch is not None:
        if isinstance(expected_epoch, bool) or not isinstance(expected_epoch, int):
            return refuse(
                "INVALID_CONTEXT",
                f"expected_epoch {expected_epoch!r} is not an integer, so there is "
                f"nothing to compare the grant's epoch with")
        if expected_epoch != epoch:
            return refuse("INVALID_CONTEXT",
                          f"grant {key!r} is at epoch {epoch} and this call "
                          f"expected {expected_epoch}; the grant has moved")

    # The OS principal was BOUND when this grant was issued, from the kernel
    # peer of that call, and is compared here with the peer of THIS call. A
    # grant with no binding is UNKNOWN and authorizes nothing: backfilling one
    # at read time would mint an identity nobody attested, which is the very
    # thing the binding exists to prevent. It is also why a bearer
    # `context_ref` is not enough on its own — QA measured the same context and
    # grant verifying for uid 4242 AND uid 9999.
    bound = str(grant.get("os_principal") or "").strip()
    if not bound:
        return refuse(
            "UNTRUSTED_ACTOR",
            f"grant {key!r} records no os_principal, so the caller would be "
            f"identified by a bearer ref alone. A grant issued without a kernel "
            f"peer is not backfilled: that would be authority nobody attested")
    if bound != os_principal:
        return refuse(
            "UNTRUSTED_ACTOR",
            f"grant {key!r} is bound to os_principal {bound!r} and this call "
            f"arrived as {os_principal!r}. A context ref is presented by a "
            f"bearer; the kernel peer is who is actually calling")

    if str(action or "") not in (grant.get("actions") or []):
        return refuse("POLICY_DENIED",
                      f"grant {key!r} does not carry action {action!r}")
    if str(project or "") not in (grant.get("projects") or []):
        return refuse("POLICY_DENIED",
                      f"grant {key!r} does not cover project {project!r}")
    if str(item_ref or "") not in (grant.get("items") or []):
        return refuse("POLICY_DENIED",
                      f"grant {key!r} does not cover Item {item_ref!r}")

    # EXACTLY Work Engine #14's `LeaseGrant`, field for field: the ten fields,
    # tuples for `items`/`actions`, an int epoch and float epoch-seconds for the
    # window. The requested project/Item/action passed the membership checks
    # above against the STORED scope, so the value returned is the intersection
    # of what was asked and what the grant truly carries — never the caller's
    # field echoed back and never the grant's whole scope widened into it.
    return {"verified": True, "grant": {
        "principal": str(context["actor_ref"]),
        "grant_ref": key,
        "project": str(project),
        "items": (str(item_ref),),
        "actions": (str(action),),
        "valid_from": window["valid_from"],
        "expires_at": window["expires_at"],
        "revoked": False,
        "epoch": int(epoch),
        "os_principal": bound,
    }}


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

    SERIALISE FIRST, THEN OPEN. `json.dump` to a text handle does not produce
    one write: it hands the document over in as many chunks as the encoder
    yields, and every chunk pays the encoder's and the wrapper's per-call cost
    again. On the live alert register on 2026-10-04 (27,597,335 B, 13,166 rows,
    11,805 of them RESOLVED) that was 2,522,746 such writes per dump, and the
    daemon dumps twice a minute — measured as the single largest cost inside
    `alerting.evaluate`, which is what made `serverosctl` calls time out against
    the daemon's own tick. `json.dumps` produces the same bytes and hands them
    over once: 0.579 s → 0.315 s per write of that register.

    This is a SPEED change, not a format change. `indent=2`, `sort_keys=True`
    and `default=str` are all still here, so the file on disk is byte-for-byte
    what it was, and every store that shares this function — the control store,
    the digest cursor, the card ledgers, the bounded-invocation receipts — keeps
    the shape its diffs and its readers were written against.

    A second, smaller consequence of serialising first: a payload the encoder
    refuses now raises BEFORE a temporary file exists, so it cannot leave one
    behind. Two of those (`.tmp` files from 2026-09-27, 1.2 MB and 1.6 MB) are
    still sitting beside the live register.
    """
    body = json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=str(path.parent),
        prefix=path.name + ".", suffix=".tmp", delete=False)
    try:
        handle.write(body)
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

    def __init__(self, path: Path | str, *, now=None, require_existing: bool = False) -> None:
        self.path = Path(path)
        self._now = now
        if type(require_existing) is not bool:
            raise StoreError("require_existing must be a boolean")
        self.require_existing = require_existing
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
        strict_data = self._existing_owner_document() if self.require_existing else None
        if not self.path.exists():
            if self._wrote or self.require_existing:
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
                    "admissions": {}, "checkpoints": {},
                    "dispatch_attempts": {}, "grant_epoch": 0,
                    "owner_policies": {}, "owner_policy_receipts": {},
                    "owner_control_receipts": {}}
        try:
            data = strict_data if strict_data is not None else json.loads(self.path.read_text(encoding="utf-8"))
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
        data.setdefault("checkpoints", {})
        # #245. Durable dispatch attempts, added the same additive way every
        # other section was: an older store document simply lacks the key and
        # gets an empty section, so this is a forward-compatible read of an
        # existing file rather than a migration.
        data.setdefault("dispatch_attempts", {})
        # A durable, monotonic grant generation. Additive like every other key:
        # an older document reads as 0 and the FIRST new grant gets 1. Existing
        # grants are NOT backfilled (see `verify_live_grant`).
        data.setdefault("grant_epoch", 0)
        data.setdefault("owner_policies", {})
        data.setdefault("owner_policy_receipts", {})
        data.setdefault("owner_control_receipts", {})
        data["schema"] = SCHEMA
        return data

    def _existing_owner_document(self) -> dict:
        """Keep a configured owner anchor existing and attributable on every IO.

        The owner normally atomically replaces the file; no inode is pinned.
        This mode never bootstraps an empty register after disappearance.
        """
        if not self.path.is_absolute():
            raise StoreError("the existing owner store requires an absolute anchor")
        try:
            if self.lock_path.exists() or self.lock_path.is_symlink():
                _validate_owner_lock(self.lock_path)
            for ancestor in reversed(self.path.parents):
                observed = ancestor.lstat()
                if stat.S_ISLNK(observed.st_mode) or not stat.S_ISDIR(observed.st_mode):
                    raise StoreError("owner store ancestors must be real directories")
                if observed.st_uid not in (0, os.geteuid()):
                    raise StoreError("owner store ancestor belongs to another account")
            observed = self.path.lstat()
            if (not stat.S_ISREG(observed.st_mode) or observed.st_uid != os.geteuid()
                    or observed.st_mode & 0o022 or observed.st_nlink != 1):
                raise StoreError("owner store must be an existing, unshared owner file")
            handle = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(handle, encoding="utf-8") as stream:
                opened = os.fstat(stream.fileno())
                if (opened.st_ino, opened.st_dev) != (observed.st_ino, observed.st_dev):
                    raise StoreError("owner anchor changed while it was opened")
                data = json.load(stream)
        except (OSError, ValueError) as exc:
            raise StoreError("the configured existing owner store is unavailable or invalid") from exc
        if not isinstance(data, dict) or data.get("schema") != SCHEMA:
            raise StoreError("the configured owner anchor is not an Agent Control store")
        for collection in ("contexts", "approvals", "bindings", "selections", "grants",
                           "admissions", "checkpoints", "dispatch_attempts",
                           "owner_policies", "owner_policy_receipts", "owner_control_receipts"):
            if collection in data and not isinstance(data[collection], dict):
                raise StoreError("the owner store contains an invalid collection")
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
        # A read must not replace a mutation's in-memory snapshot while that
        # mutation is paused before flush. The file lock serializes writers,
        # not readers sharing this Python instance.
        return self._load()

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
        if self.require_existing:
            self._existing_owner_document()
        with _exclusive(self.lock_path, owner_only=self.require_existing):
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
        if self.require_existing:
            self._existing_owner_document()
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

    # -- durable dispatch attempts (#245 / S2) ------------------------------
    #
    # The record `runtime/dispatch_attempts.py#reconcile` reads. It lives HERE,
    # in the existing Agent Control store, because #245 names Agent Control as
    # its owner and because a second attempt store is the "new-lease-system"
    # shape `integration/components.yaml` refuses by name.

    def claim_dispatch_attempt(self, intent: dict, *, actor: str) -> dict:
        """Atomically latch one Fleet delivery before IO, in the existing collection.

        The private marker correlates the claimant's later ACK. It is not an
        execution grant. No generic UNKNOWN transition is relaxed.
        """
        fields = {"key", "action", "device_ref", "verb", "intent_digest",
                  "principal", "runtime", "session_ref", "expected_project_path"}
        metadata_fields = {"principal_subject", "principal_kind", "project", "work_ref", "parent_ref", "agent_ref"}
        fields |= metadata_fields
        required = fields - {"session_ref", "expected_project_path"} - metadata_fields
        if (not isinstance(intent, dict) or set(intent) - fields
                or not required.issubset(intent)
                or any(not isinstance(intent[k], str) or not intent[k].strip()
                       for k in required)):
            raise StoreError("invalid delivery claim")
        metadata = {k: intent[k] for k in metadata_fields if k in intent}
        if (any(not isinstance(value, str) or len(value) > 200
                or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:|\-]*", value) is None
                for value in metadata.values())
                or ("principal_kind" in metadata and metadata["principal_kind"] not in {"owner", "client", "agent"})
                or (("principal_kind" in metadata) != ("principal_subject" in metadata))):
            raise StoreError("invalid delivery context")
        if not isinstance(actor, str) or not actor.strip() or intent["principal"] != actor:
            raise AttemptRefused("UNTRUSTED_ACTOR", "claim principal differs from its actor")
        if (not re.fullmatch(r"[a-f0-9]{64}", intent["intent_digest"])
                or len(intent["key"]) > 256):
            raise StoreError("invalid delivery claim identity")
        project = intent["verb"] == "project.create"
        expected = intent.get("expected_project_path")
        if (project and (not isinstance(expected, str) or not expected.strip()
                         or len(expected) > 4096 or "session_ref" in intent)
                or not project and expected is not None):
            raise StoreError("invalid project delivery target")
        kernel = _dispatch_attempts()
        with self._mutation():
            section = self._data.setdefault("dispatch_attempts", {})
            existing = section.get(intent["key"])
            if existing is not None:
                if existing.get("principal") != actor:
                    raise AttemptRefused("UNTRUSTED_ACTOR", "attempt belongs to another actor")
                public = {k: v for k, v in existing.items() if k != "_dispatch_claim"}
                return {"claimed": False, "record": json.loads(json.dumps(public))}
            marker = uuid.uuid4().hex
            record = kernel.attempt_record(
                **{k: v for k, v in intent.items() if k != "expected_project_path" and k not in metadata_fields},
                phase=kernel.EFFECT_UNKNOWN, at=datetime.now(timezone.utc).isoformat())
            # Correlation labels only; work_item/grant/lease ownership stays in
            # the existing owner APIs and is never inferred from these refs.
            record.update(metadata)
            if project:
                record["expected_project_path"] = expected
            record["_dispatch_claim"] = marker
            section[intent["key"]] = record
            self._flush()
            return {"claimed": True, "claim_id": marker,
                    "record": {k: v for k, v in record.items() if k != "_dispatch_claim"}}

    def complete_dispatch_claim(self, key, *, digest, claim_id, actor, receipt, project=None) -> dict:
        """Record only the original invocation's ACK; unknown retries cannot reopen it."""
        if (not isinstance(receipt, dict)
                or receipt.get("accepted") is not True
                or len(json.dumps(receipt, allow_nan=False).encode("utf-8")) > 65536):
            raise StoreError("invalid closed delivery receipt")
        if project is not None and (not isinstance(project, str) or len(project) > 200
                or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:|\-]*", project) is None):
            raise StoreError("invalid delivery project")
        for name in ("run_id", "delivery_id", "queued_message_uuid"):
            value = receipt.get(name)
            if value is not None and (not isinstance(value, str) or not value.strip()
                                      or len(value) > 512 or any(ord(c) < 32 for c in value)):
                raise StoreError("invalid receipt identifier")
        with self._mutation():
            section = self._data.setdefault("dispatch_attempts", {})
            record = section.get(str(key))
            if (not record or record.get("principal") != actor
                    or record.get("intent_digest") != digest
                    or not claim_id or record.get("_dispatch_claim") != claim_id
                    or record.get("phase") != "EFFECT_UNKNOWN"):
                raise AttemptRefused("INVALID_CONTEXT", "not the original unresolved delivery claim")
            if record["verb"] == "project.create":
                if (set(receipt) != {"project_id", "project_path", "accepted"}
                        or not isinstance(receipt["project_id"], str)
                        or not receipt["project_id"].strip()
                        or receipt["project_path"] != record.get("expected_project_path")):
                    raise StoreError("invalid or misbound project receipt")
            else:
                if (set(receipt) - {"session_ref", "run_id", "delivery_id", "queued_message_uuid", "accepted"}
                        or not isinstance(receipt.get("session_ref"), str)):
                    raise StoreError("invalid session receipt")
                parts = receipt["session_ref"].split("|")
                if (len(parts) != 3 or not parts[2]
                        or parts[:2] != [record["device_ref"], record["runtime"]]
                        or (record.get("session_ref")
                            and record["session_ref"] != receipt["session_ref"])):
                    raise StoreError("receipt addresses a different target")
                if (record["verb"] == "session.send" and not receipt.get("run_id")
                        and not receipt.get("queued_message_uuid") and not receipt.get("delivery_id")):
                    raise StoreError("send acknowledgement has no execution reference")
            updated = {k: v for k, v in record.items() if k != "_dispatch_claim"}
            if project is not None:
                if record["verb"] == "project.create" or record.get("project", project) != project:
                    raise StoreError("delivery project differs from original intent")
                updated["project"] = project
            # This timestamp belongs to the original ACK commit, never to a
            # claimant's input, recovery observation, or an accepted replay.
            updated.update(phase="ACCEPTED", result=dict(receipt),
                           accepted_at=datetime.now(timezone.utc).isoformat())
            section[str(key)] = updated
            self._flush()
            return json.loads(json.dumps(updated))

    def put_dispatch_attempt(self, record: dict, *, context_ref=None,
                             actor=None) -> dict:
        """Record one dispatch attempt, durably, keyed by its idempotency key.

        The record is written BEFORE the effect is attempted (phase
        CALL_NOT_SENT), because "we crashed before sending" and "we crashed
        after sending and do not know" are the same bytes on disk and opposite
        answers — and only a record that exists first can tell them apart.

        NOT write-once: an attempt MOVES through phases. Three rules are
        enforced HERE, under the mutation lock, so the check and the write are
        one step rather than a read a second writer can slip between:

        * a change of INTENT under one key is refused (different
          `intent_digest`) — the same-key-different-payload conflict;
        * QA D3 — only the actor that DISPATCHED an attempt may mutate it.
          Knowing the digest is not owning the attempt, so `actor` is compared
          with the stored principal and a mismatch is `UNTRUSTED_ACTOR`. A
          stored record with no principal cannot be mutated by anybody;
        * QA D4 — only the moves in `dispatch_transitions` are allowed. A
          terminal SETTLED is not reopened and EFFECT_UNKNOWN is not turned back
          into a sendable CALL_NOT_SENT, because either would make the next
          retry PROCEED and send the same external effect twice.

        `context_ref` is accepted for interface uniformity with the daemon
        handle and is not read here; `actor` is the resolved principal the
        daemon derived from it, and is what the identity rule compares.
        """
        key = str(record.get("key") or "")
        if not key:
            raise StoreError(
                "a dispatch attempt without a key cannot be stored: there is "
                "nothing for a retry to be matched to")
        kernel = _dispatch_attempts()
        with self._mutation():
            section = self._data.setdefault("dispatch_attempts", {})
            existing = section.get(key)
            if existing is not None:
                if actor is not None:
                    owner = str(existing.get("principal") or "").strip()
                    if not owner or owner != str(actor).strip():
                        raise AttemptRefused(
                            "UNTRUSTED_ACTOR",
                            f"dispatch key {key!r} was dispatched by {owner or 'nobody'} "
                            f"and {actor!r} is not that actor. An attempt is mutated "
                            f"by the actor that made it; knowing the digest is not "
                            f"owning it")
                if (str(existing.get("intent_digest"))
                        != str(record.get("intent_digest"))):
                    raise AttemptRefused(
                        "INVALID_CONTEXT",
                        f"dispatch key {key!r} already records a DIFFERENT intent "
                        f"(payload digest {existing.get('intent_digest')!r} vs "
                        f"{record.get('intent_digest')!r}). A key identifies one "
                        f"intent, and a different payload under it is a conflict, "
                        f"not an update")
                if not kernel.dispatch_transition_allowed(
                        existing.get("phase"), record.get("phase")):
                    raise AttemptRefused(
                        "INVALID_CONTEXT",
                        f"dispatch key {key!r} is {existing.get('phase')!r} and the "
                        f"dispatch side may not move it to {record.get('phase')!r}. "
                        f"A terminal outcome is not reopened, and an unknown one is "
                        f"not turned back into a sendable attempt")
            stored = dict(record)
            section[key] = stored
            self._flush()
            return stored

    def reconcile_dispatch_attempt(self, key: str, *, observation, reconciler,
                                   current_writer=None, now=None) -> dict:
        """Resolve an EFFECT_UNKNOWN attempt from an OWNER's observation. #245.

        Read, decide and write under ONE mutation lock, so the decision cannot be
        made about a record that changed between the read and the write — which
        is what `_mutation` exists to prevent, and the reason this is a method
        here rather than two calls from outside.

        A REFUSED observation writes nothing: the attempt stays EFFECT_UNKNOWN,
        which is what refuses a retry. Only a definite observation, from the
        recorded principal, holding the current writer scope, about THIS intent,
        moves it — to SETTLED (`SETTLE_APPLIED`) or back to CALL_NOT_SENT
        (`SETTLE_NOT_APPLIED`).

        Returns the kernel's verdict, plus the updated record when it settled.
        """
        kernel = _dispatch_attempts()
        with self._mutation():
            section = self._data.setdefault("dispatch_attempts", {})
            recorded = section.get(str(key))
            verdict = kernel.reconcile_observation(
                recorded, observation=observation, reconciler=reconciler,
                current_writer=current_writer, now=now)
            if verdict["verdict"] not in (kernel.SETTLE_APPLIED,
                                          kernel.SETTLE_NOT_APPLIED):
                return verdict
            updated = dict(recorded)
            updated.pop("_dispatch_claim", None)
            updated["phase"] = verdict["phase"]
            updated["reconciled_by"] = str((reconciler or {}).get("principal") or "")
            if now:
                updated["reconciled_at"] = str(now)
            if verdict.get("result") is not None:
                updated["result"] = verdict["result"]
            section[str(key)] = updated
            self._flush()
            return dict(verdict, attempt=self._dispatch_view(updated))

    def get_dispatch_attempt(self, key: str) -> dict | None:
        """The recorded attempt for a key, or None if nothing was recorded."""
        return self._dispatch_view(self._read().get("dispatch_attempts", {}).get(str(key)))

    def dispatch_attempts(self) -> dict:
        return {key: self._dispatch_view(value) for key, value in
                self._read().get("dispatch_attempts", {}).items()}

    @staticmethod
    def _dispatch_view(record):
        if record is None:
            return None
        return json.loads(json.dumps({k: v for k, v in record.items()
                                      if k != "_dispatch_claim"}))

    def close_binding(self, ref: str, *, at: str, reason: str | None = None,
                      successor: dict | None = None,
                      actor: str | None = None) -> dict:
        """Close a thread for new work. Never a deletion, and never a rebind.

        The record stays. Closing is what makes
        `THREAD_CLOSED_FOR_NEW_WORK` answerable months later, and a store that
        dropped the binding on closure would leave the closed thread looking
        exactly like a thread nobody ever created one for.

        TRANSFER (#237 T1 / #310 F1). A handoff closes the source AND gives the
        established target the scope's writer claim in the SAME mutation. Two
        separate writes would leave a window with no authorized writer — the
        source closed, the target not yet minted — which is the one state a
        handoff cannot recover from. `successor` is a full binding record the
        trusted daemon route built from `coordination.mint_writer`; the store
        validates its identity against the source and writes both records under
        one lock. A writer supplied by an untrusted request never reaches here.
        """
        with self._mutation():
            bindings = self._data["bindings"]
            existing = bindings.get(str(ref))
            if existing is None:
                raise StoreError(
                    f"{ref} has no binding to close; closing a thread that was never "
                    f"bound would record a closure of something ServerOS does not "
                    f"hold a statement about")
            updated_source = dict(existing)
            updated_source["state"] = "CLOSED"
            updated_source["closed_at"] = str(at)
            if reason:
                updated_source["closed_reason"] = str(reason)
            if actor:
                updated_source["closed_by"] = str(actor)

            updated_successor = None
            if successor is not None:
                if str(existing.get("state") or "").upper() == "CLOSED":
                    raise StoreError(
                        f"{ref} is already CLOSED; a write-authority transfer "
                        f"must start from an open source, or it would mint a "
                        f"second claim from a closure that already happened")
                if not isinstance(successor, dict):
                    raise StoreError(
                        "a successor is a binding record; nothing here names the "
                        "thread that would receive the write scope")
                target_ref = str(successor.get("session_ref") or "").strip()
                if not target_ref:
                    raise StoreError(
                        "a write-authority transfer must name the successor "
                        "session; without one the scope would be left unowned")
                if target_ref == str(ref):
                    raise StoreError(
                        "a thread cannot be its own successor; a transfer names "
                        "two different sessions")
                target = bindings.get(target_ref)
                if target is None:
                    raise StoreError(
                        f"successor {target_ref!r} has no binding; a session with "
                        f"no recorded purpose is not an established writer")
                if str(target.get("state") or "").upper() == "CLOSED":
                    raise StoreError(
                        f"successor {target_ref!r} is CLOSED; a closed thread "
                        f"takes no new work and cannot receive write authority")
                source_item = str(existing.get("bound_item_ref") or "")
                if (not source_item
                        or str(target.get("bound_item_ref") or "") != source_item):
                    raise StoreError(
                        f"successor {target_ref!r} is not bound to the source's "
                        f"Item {source_item!r}; write authority cannot cross an "
                        f"Item boundary")
                if target.get("writer"):
                    raise StoreError(
                        f"successor {target_ref!r} already records a writer; a "
                        f"transfer cannot overwrite a claim that is already there")
                writer = successor.get("writer")
                if not isinstance(writer, dict):
                    raise StoreError(
                        "a successor carries the writer claim it received; none "
                        "was supplied")
                owner = str(writer.get("owner") or "").strip()
                epoch = writer.get("epoch")
                token = str(writer.get("token") or "").strip()
                if (not owner or isinstance(epoch, bool)
                        or not isinstance(epoch, int) or epoch < 1 or not token):
                    raise StoreError(
                        f"successor writer {writer!r} is not an owner/epoch/token "
                        f"claim; an unshaped writer is not a fence")
                updated_successor = dict(target)
                schema = str(successor.get("schema") or "").strip()
                if schema:
                    updated_successor["schema"] = schema
                updated_successor["writer"] = dict(writer)
                if successor.get("writer_recorded_at"):
                    updated_successor["writer_recorded_at"] = str(
                        successor["writer_recorded_at"])

            bindings[str(ref)] = updated_source
            if updated_successor is not None:
                bindings[str(updated_successor["session_ref"])] = updated_successor
            self._flush()
            result = dict(updated_source)
            if updated_successor is not None:
                # Returned, not persisted: the source record stays a closure,
                # and the successor is read back by its own session_ref.
                result["successor"] = updated_successor
            return result

    def claim_writer(self, ref: str, *, writer: dict, schema: str | None,
                     at: str) -> dict:
        """Attach a writer claim to an OPEN binding that has none (#237 T1).

        This is the handoff's pre-close half: a target that is already bound to
        the source's Item must become the current writer BEFORE its resume/send
        is admitted, because admission fences a no-claim thread on an owned
        scope. The daemon mints the claim with `coordination.mint_writer`; this
        store only checks the shape and that it is not overwriting an existing
        claim. It is not a general "set writer" method: an existing writer is a
        refusal, and a closed binding is a refusal.
        """
        with self._mutation():
            bindings = self._data["bindings"]
            existing = bindings.get(str(ref))
            if existing is None:
                raise StoreError(
                    f"{ref} has no binding to claim; a session with no recorded "
                    f"purpose is not an established writer")
            if str(existing.get("state") or "").upper() == "CLOSED":
                raise StoreError(
                    f"{ref} is CLOSED; a closed thread takes no new work and "
                    f"cannot receive a writer claim")
            if existing.get("writer"):
                raise StoreError(
                    f"{ref} already records a writer; a claim cannot overwrite "
                    f"an existing claim")
            if not isinstance(writer, dict):
                raise StoreError("a writer claim is a record; none was supplied")
            owner = str(writer.get("owner") or "").strip()
            epoch = writer.get("epoch")
            token = str(writer.get("token") or "").strip()
            if (not owner or isinstance(epoch, bool)
                    or not isinstance(epoch, int) or epoch < 1 or not token):
                raise StoreError(
                    f"writer {writer!r} is not an owner/epoch/token claim; an "
                    f"unshaped writer is not a fence")
            updated = dict(existing)
            if schema:
                updated["schema"] = str(schema)
            updated["writer"] = dict(writer)
            updated["writer_recorded_at"] = str(at)
            bindings[str(ref)] = updated
            self._flush()
            return updated

    # -- Parent checkpoints (P7 continuation) ------------------------------

    def put_checkpoint(self, record: dict) -> dict:
        """Record where a logical Parent got to before it stopped.

        WRITE-ONCE PER SCOPE WHILE LIVE, where the scope is the Item at one epoch
        of itself and not the owner. `put_binding` one screen up is immutable
        because its central field must never change; this one is immutable
        because a second Parent writing over a first one's record is how a
        continuation is lost, and because the two rules it replaces are worded as
        prose in two documents — «同一个scope中只有一个父继续owner» and «旧结果晚到
        覆盖新epoch» in `docs/COLLABORATION-CONTRACT.md` and
        `docs/SCENARIO-ACCEPTANCE.md` — and a rule that lives only in prose is a
        rule a store cannot enforce.

        THREE REFUSALS, and each is one of those sentences made mechanical:

          * a SECOND LIVE checkpoint for the same (Item, epoch) — the scope
            already has an owner and the message names it;
          * an epoch BELOW the highest already recorded for that Item — a Parent
            from an earlier epoch cannot write into the current one, which is what
            stops a late result from a replaced Parent from landing on the new
            epoch's state. The epoch in the key is what makes this answerable at
            all: without it the two records would be one key and the second write
            would silently be the only one;
          * a record with no `checkpoint_ref` — the key is derived by
            `runtime/continuation.build_checkpoint`, and a record that arrived
            without one did not come through it.

        A WRITE THAT MEETS A PARKED RECORD IS THE FIRST OF THOSE, WORDED AS WHAT IT
        IS. It refuses, as it always did, and the sentence names the park instead of
        calling the record live: it is not live — `live_checkpoints` stopped
        answering for it the moment somebody set it down — and what the writer needs
        is the reason its replacement is refused. The two repairs it names are the
        two that exist: `unpark_checkpoint`, or the epoch that supersedes it.

        AND ONE THING THIS DOES THAT IS NOT A REFUSAL. A write at a HIGHER epoch
        than the live record for the same Item supersedes that record in the same
        transaction. The scope is the Item; the epoch is only how it changes hands,
        and «one continuation owner per scope» would otherwise have a moment with
        two. Recorded on the OLD record, so the Parent that was replaced keeps its
        state and loses only the claim to own it — and so that superseding stays a
        statement about a scope rather than a deletion.

        A REPLAY IS NOT A SECOND WRITE. An equal record minus
        `CHECKPOINT_STORE_FIELDS` answers with the stored one, which is what a
        retried write looks like and the same shape `put_binding` uses. Asked
        BEFORE the two refusals, because a retry of an accepted write is a retry
        whatever the scope's history has done since. Nothing here deletes: a
        superseded record stays and says so.
        """
        ref = str(record.get("checkpoint_ref") or "")
        if not ref:
            raise StoreError(
                "a checkpoint arrived with no checkpoint_ref. The key is derived "
                "from the Item and the epoch by `runtime/continuation."
                "build_checkpoint`, and a record that named its own key could be "
                "filed over another Parent's")
        item = str(record.get("work_item") or "")
        epoch = record.get("epoch")
        with self._mutation():
            existing = self._data["checkpoints"].get(ref)
            if existing is not None:
                mine = {k: v for k, v in record.items()
                        if k not in CHECKPOINT_STORE_FIELDS}
                theirs = {k: v for k, v in existing.items()
                          if k not in CHECKPOINT_STORE_FIELDS}
                if mine == theirs:
                    # On the returned copy and not on the stored record, which is
                    # `admit`'s arrangement: a replayed write is a fact about this
                    # call, and a record that carried it would make the second
                    # replay differ from the first by that field alone.
                    #
                    # Asked BEFORE the two refusals below, and the order is
                    # deliberate. A retry of an accepted write is a retry whatever
                    # the scope's history has done since, and answering it with
                    # «a later epoch carries this Item» would refuse a write the
                    # store already accepted — the caller would be told its own
                    # accepted record is not there.
                    return {**existing, "replayed": True}
                if existing.get("superseded_by"):
                    raise StoreError(
                        f"{ref} was taken over at {existing.get('superseded_at')} "
                        f"by {existing.get('superseded_by')}, and this write is not "
                        f"the record that is stored. {item} is carried by a later "
                        f"epoch now, so a Parent writing different state into this "
                        f"one would be the replaced owner writing over what "
                        f"replaced it — which is the «旧结果晚到覆盖新epoch» failure "
                        f"stated as a store rule instead of as prose")
                if is_parked(existing):
                    # NOT «holds a live checkpoint»: a person set this one down,
                    # and the sentence a writer needs is the one that says so —
                    # they may be looking at exactly this record wondering why it
                    # was not resumed.
                    raise StoreError(
                        f"{ref} was parked at {existing.get('parked_at')} by "
                        f"{existing.get('parked_by')} for "
                        f"{existing.get('parked_reason')!r}, and a different record "
                        f"cannot be written over it. The park is why the work was set "
                        f"down, and replacing this record would remove the only copy "
                        f"of it — `unpark_checkpoint` puts this record back in the "
                        f"set a wake reads and keeps both halves, and a write at a "
                        f"higher epoch supersedes it and keeps it readable")
                raise StoreError(
                    f"{ref} already holds a live checkpoint and it cannot be "
                    f"replaced. The scope is this Item at this epoch, one "
                    f"continuation owner at a time, and the record already there "
                    f"was written by {existing.get('parent_owner')!r} at "
                    f"{existing.get('written_at')}. Carry the Item on under a new "
                    f"epoch, which takes the scope over and keeps this record "
                    f"readable — overwriting would make the two Parents' states one "
                    f"record and the earlier one unreadable")
            # A new record for this ref. The Item is the scope and the epoch is
            # only how it changes hands, so a write at a HIGHER epoch supersedes
            # the live lower-epoch record in the same transaction.
            #
            # Not a refusal that asks the caller to supersede first, and that is
            # the difference this loop makes: between the two writes there would
            # be a moment with two live owners for one Item, and
            # `docs/COLLABORATION-CONTRACT.md` says there is one. Doing it here
            # also means the handover is recorded on the OLD record, so the Parent
            # that was replaced keeps its state and loses only the claim to own it.
            for other_ref, other in sorted(self._data["checkpoints"].items()):
                if other_ref == ref or other.get("work_item") != item:
                    continue
                if other.get("superseded_by"):
                    continue
                other_epoch = other.get("epoch")
                if not isinstance(other_epoch, int) or not isinstance(epoch, int):
                    continue
                if other_epoch > epoch:
                    raise StoreError(
                        f"{item} has a checkpoint at epoch {other_epoch} and this "
                        f"one is epoch {epoch}. A Parent from an earlier epoch "
                        f"cannot write into a later one: the Item has been carried "
                        f"on since, and a record from the Parent that was replaced "
                        f"would be read as the current state of work it no longer "
                        f"owns. If this epoch is the current one, the later record "
                        f"is the one to supersede — and superseding it is a "
                        f"statement that this write is the successor, not a way to "
                        f"quietly become it")
                if other_epoch < epoch:
                    replaced = dict(other)
                    replaced["superseded_by"] = ref
                    replaced["superseded_at"] = record.get("written_at")
                    self._data["checkpoints"][other_ref] = replaced
            self._data["checkpoints"][ref] = record
            self._flush()
            return {**record, "replayed": False}

    def get_checkpoint(self, ref: str) -> dict | None:
        """One checkpoint by its ref, or None if nothing was filed under it.

        None rather than an empty record. «nobody checkpointed this» and «a
        Parent checkpointed that it had nothing to say» are opposite readings,
        and a caller that collapsed them would resume from an absence.
        """
        return self._read()["checkpoints"].get(str(ref))

    def checkpoints(self) -> dict:
        return dict(self._read()["checkpoints"])

    def live_checkpoints(self) -> dict:
        """Every checkpoint that is still a candidate to be woken.

        A filter rather than a separate collection: both ways out are a field on
        the record they apply to — a later epoch's takeover, a person's park — so
        there is one list of checkpoints and this is the reading of it that answers
        «who owns what now». A second list would be a second thing to keep
        consistent after a restart, and the store's own contract already refuses a
        second store for exactly this reason.

        TWO WAYS OUT OF THIS SET, AND THEY ARE NOT THE SAME FACT. A superseded
        record's Item is carried by a later epoch: somebody has it. A parked
        record's Item is carried by nobody: it was set down on purpose and no later
        epoch exists. Both stop a wake, and a reader that could only see the set
        would have to guess which of the two it was looking at — which is why every
        caller that prints a reason asks the record (or `parked_checkpoints`)
        instead of reporting «taken over» for both.
        """
        return {ref: record for ref, record in self._read()["checkpoints"].items()
                if not record.get("superseded_by") and not is_parked(record)}

    def parked_checkpoints(self) -> dict:
        """Every checkpoint that was set down and not taken back up.

        Its callers are the ones that must not be handed a park as «taken over»:
        `runtime/wake.plan` asks it to say why a record is not a candidate, and
        `work continuations` counts the parked ones apart from the live ones. A
        park that made a record invisible instead would be a deletion with a
        reason attached, and the reason is the part somebody needs later.

        NOT THE COMPLEMENT OF `live_checkpoints`, AND THE DIFFERENCE HAS A CASE. A
        parked record can be walked over afterwards by a later epoch — the Item is
        carried again, which is the later and stronger fact — and then it is in
        this set AND out of the live one for a different reason. Nothing lifted
        that park, so it belongs here. Every reader that must print ONE reason
        therefore checks supersession first: `wake.plan` and
        `continuation_state` both do, and `tests/test_continuation.py` pins that
        the gate and the answer agree about every record.
        """
        return {ref: record for ref, record in self._read()["checkpoints"].items()
                if is_parked(record)}

    def park_checkpoint(self, ref: str, *, at: str, by: str, reason: str) -> dict:
        """Set a checkpoint down on purpose: keep the record and the reason, and
        stop everything that would wake it.

        THE PRODUCT NAMES FOUR WAYS WORK LEAVES FOCUS AND THIS IS THE ONE THAT HAD
        NO IMPLEMENTATION: «Completed / Safely Parked / Cancelled + reconciled /
        Superseded + transferred». Completion is a board state, superseding is
        `supersede_checkpoint` — which requires a real successor, so it says
        «somebody else has this» — and cancelling is the board's reconciliation.
        «Nobody is waiting for this, and that is a decision rather than an
        accident» had no field, no verb and no reader: a checkpoint that had been
        looked into and deliberately set down was, to every surface in this tree,
        still a live continuation waiting for a Parent, and the wake routine
        evaluated it once a minute for the rest of the node's life. That is not
        noise because it is verbose; it is noise because «refused, no authorizing
        grant» reads exactly like an Item blocked on authority.

        NOT A DELETION AND NOT A SUPERSESSION. The record stays, keeps every field
        it had, and gains three — who set it down, when, and why. Nothing is
        removed and no successor is required: the whole point of this disposition
        is that there IS no successor, so both repairs the other dispositions offer
        («write the record that takes it over», «ask the person who owns it») would
        be wrong here.

        WHY THE REASON IS REQUIRED AND A TRUSTED CONTEXT IS NOT. A park mints no
        authority — it takes a wake away — so demanding a verified turn for it
        would make the easier act the harder one to reach, the asymmetry
        `revoke_grant` is built on. The reason is required for a reason of the
        opposite kind to `revoke_grant`'s: «it stopped» and «somebody decided it
        should stop» are the two readings a later audit has to tell apart, and the
        sentence that tells them apart is the one this call is handed. The
        signature is required for the same purpose at a smaller scale — a park
        nobody signed is a park nobody can be asked about.

        `by` IS WHO IS SPEAKING AND NOT A VERIFIED IDENTITY, which is the same
        standing `parent_owner` has on the record this one is about: the writer of
        a record is the authority for who wrote it, nothing here checks the name,
        and nothing needs to. It is deliberately NOT a `context_ref`: a park is not
        an authority act, so there is no verified turn for it to be resolved from,
        and a field that looked like one would invite a reader to treat a typed
        login as provenance. What cannot lie here is the reason and the pair of
        instants, and those are the parts an audit reads.

        ONCE PER RECORD, LIKE EVERY OTHER STATEMENT THIS STORE TAKES. A second park
        with the SAME reason is a retry and answers with the record; one with a
        DIFFERENT reason is refused rather than absorbed, because the record would
        then say it was set down for a reason other than the one it was set down
        for. Parking again after a lift is refused the same way and for a sharper
        reason: `parked_at` is when this record was set down, it holds one instant,
        and a second park would overwrite why it was set down the first time. An
        Item that has to be set down twice was carried on in between, and the
        repair is the one every refusal here names — carry it under a new epoch,
        which supersedes this record and keeps it readable.
        """
        target = str(ref or "").strip()
        if not target:
            raise StoreError(
                "parking a checkpoint needs the ref of the one being set down")
        who = str(by or "").strip()
        if not who:
            raise StoreError(
                f"parking {target} needs who is setting it down. A park nobody "
                f"signed is one nobody can be asked about later, and the record "
                f"would say the work stopped without saying who decided it should")
        why = str(reason or "").strip()
        if not why:
            raise StoreError(
                f"parking {target} needs a reason. The record of WHY is the part a "
                f"later reader cannot reconstruct from the record itself, and it is "
                f"what tells «the work stopped» apart from «somebody decided the "
                f"work should stop» — a park that says nothing is a deletion with "
                f"paperwork")
        with self._mutation():
            existing = self._data["checkpoints"].get(target)
            if existing is None:
                raise StoreError(
                    f"{target} has no checkpoint to park; this store holds nothing "
                    f"under that ref. Parking a record nobody wrote would make "
                    f"«this was set down» indistinguishable from «this never "
                    f"existed», and `work continuations` lists what is here")
            if is_parked(existing):
                if str(existing.get("parked_reason") or "") == why:
                    return existing
                raise StoreError(
                    f"{target} was parked at {existing.get('parked_at')} by "
                    f"{existing.get('parked_by')} for "
                    f"{existing.get('parked_reason')!r}, and this parks it for "
                    f"{why!r}. Why a record was set down is what a person reads "
                    f"when deciding whether to lift the park, and a second park "
                    f"would leave the record saying one of the two")
            if existing.get("unparked_at"):
                raise StoreError(
                    f"{target} was parked at {existing.get('parked_at')} and taken "
                    f"back up at {existing.get('unparked_at')}. A park is a "
                    f"statement about ONE setting-down and this record has already "
                    f"had its own; a second one would overwrite why it was first "
                    f"set down. An Item that has to be set down again is an Item "
                    f"carried on in between — write the checkpoint that carries it "
                    f"under a new epoch, which supersedes this record and keeps it "
                    f"readable")
            if existing.get("superseded_by"):
                raise StoreError(
                    f"{target} was taken over at {existing.get('superseded_at')} by "
                    f"{existing.get('superseded_by')}, so it is already not a "
                    f"candidate to be woken and parking it would say it was left "
                    f"waiting when in fact the Item changed hands. Park the record "
                    f"that carries the Item now, or leave this one: a later epoch "
                    f"owns it and `live_checkpoints` has already stopped answering "
                    f"for it")
            updated = dict(existing)
            updated["parked_at"] = str(at)
            updated["parked_by"] = who
            updated["parked_reason"] = why
            self._data["checkpoints"][target] = updated
            self._flush()
            return updated

    def unpark_checkpoint(self, ref: str, *, at: str, by: str, reason: str) -> dict:
        """Lift a park: the record is a candidate to be woken again, and says it
        was set down and taken back up rather than never touched.

        LIFTING IS NOT RETRACTING. Nothing is removed from the record and the
        park's own three fields stay exactly as the park wrote them, so what a
        later reader gets is the whole sentence — set down at X by Y because Z,
        taken back up at A by B because C — and a store that dropped the park
        fields on the way back up would leave the second half of it with nothing
        to refer to.

        WHAT THIS DOES NOT DO IS AUTHORIZE ANYTHING. Lifting a park puts the record
        back in `live_checkpoints`, which makes it a candidate again; whether
        anything can wake it is still decided by a grant that names the Item and a
        context that is fresh, and neither of those is reachable from here. So this
        is not a way to work around a missing authority — it is how a person says
        «the Item I set down is one we are carrying again», and the wake half still
        refuses it until it has an authority of its own.

        ONCE, AND BY THE SAME RULES AS THE PARK ABOVE: a second lift with the same
        reason is a retry and answers with the record, one with a different reason
        is refused rather than absorbed, and a record that was never parked cannot
        be lifted at all — putting a record back in the wake set on the strength of
        a call that never took it out would be this verb inventing the park it is
        undoing.
        """
        target = str(ref or "").strip()
        if not target:
            raise StoreError(
                "lifting a park needs the ref of the checkpoint to take back up")
        who = str(by or "").strip()
        if not who:
            raise StoreError(
                f"lifting the park on {target} needs who is lifting it. The record "
                f"keeps both signatures — who set the work down and who took it "
                f"back up — and the second one is what a reader has when asking why "
                f"an Item that was parked is being carried again")
        why = str(reason or "").strip()
        if not why:
            raise StoreError(
                f"lifting the park on {target} needs a reason. «It is being carried "
                f"again» and «somebody decided it should be» are the two readings a "
                f"later audit has to tell apart, and the record already carries the "
                f"reason it was set down, so this call is the other half of that "
                f"sentence")
        with self._mutation():
            existing = self._data["checkpoints"].get(target)
            if existing is None:
                raise StoreError(
                    f"{target} has no checkpoint to take back up; this store holds "
                    f"nothing under that ref")
            if not is_parked(existing):
                if existing.get("unparked_at"):
                    if str(existing.get("unparked_reason") or "") == why:
                        return existing
                    raise StoreError(
                        f"{target} was taken back up at "
                        f"{existing.get('unparked_at')} by "
                        f"{existing.get('unparked_by')} for "
                        f"{existing.get('unparked_reason')!r}, and this lifts it "
                        f"for {why!r}. When a park was lifted is written once: the "
                        f"record says whether this Item is being carried again, and "
                        f"accepting this would move that answer to whenever somebody "
                        f"last mentioned it")
                raise StoreError(
                    f"{target} is not parked, so there is nothing to take back up. "
                    f"Lifting a park nobody made would put this record back in the "
                    f"set a wake reads on the strength of a call that never took it "
                    f"out of it")
            updated = dict(existing)
            updated["unparked_at"] = str(at)
            updated["unparked_by"] = who
            updated["unparked_reason"] = why
            self._data["checkpoints"][target] = updated
            self._flush()
            return updated

    def supersede_checkpoint(self, ref: str, *, at: str, by: str) -> dict:
        """Hand a scope to a later epoch. Idempotent, and the record stays.

        `by` is the ref of what took over — a later epoch's checkpoint of the same
        Item. Requiring it is the point: superseding without naming the successor
        would leave a scope with no owner at all, and «nobody owns this» is a
        different fact from «someone else does». **AND THE NAME HAS TO RESOLVE.**
        A successor this store does not hold is the same «no owner at all» with a
        ref in it — the record would say a Parent carries the Item and no such
        Parent would exist anywhere — so the three things the sentence above says
        about `by` are checked rather than assumed: this store holds it, it is a
        checkpoint of the SAME Item, and its epoch is HIGHER than the one it
        replaces. `put_checkpoint` already enforces exactly these three when it
        supersedes in the same transaction; the same field has two writers and
        they must agree about what may be written into it.

        Idempotent on the FIRST supersession, like `retire_admission`, and by the
        same rule applied to both of its facts: the question this answers is WHEN
        the scope changed hands and TO WHOM, a second call that moved either would
        move it to whenever somebody last mentioned it, and a second call naming a
        DIFFERENT successor is refused rather than absorbed. That last half is the
        one that used to be silent — a third Parent could take a scope this record
        says a second one holds, and the record would go on saying the second —
        and it is a question for a person for a sharper reason than `outcome` is:
        two successors for one scope is two owners, which
        `docs/COLLABORATION-CONTRACT.md` does not allow even for an instant.

        The three checks above run AFTER the idempotent branch and not before, for
        the reason `put_checkpoint` asks its replay question before its two
        refusals: a replay of what the store already says is a replay whatever the
        record says, and a store written before these checks existed must still be
        able to answer the call that wrote it. They govern what may be written
        NEXT, and they do not turn a record already on disk into a refusal of its
        own history.
        """
        target = str(ref or "")
        successor = str(by or "").strip()
        if not successor:
            raise StoreError(
                f"superseding {target or 'a checkpoint'} needs the ref of what took "
                f"the scope over. A supersession that names no successor leaves the "
                f"Item looking carried by nobody")
        with self._mutation():
            existing = self._data["checkpoints"].get(target)
            if existing is None:
                raise StoreError(
                    f"{target or 'that ref'} has no checkpoint to supersede; "
                    f"recording a handover of a scope this store does not hold would "
                    f"make a scope that was never checkpointed look like one that was "
                    f"taken over")
            if existing.get("superseded_by"):
                if existing["superseded_by"] == successor:
                    return existing
                raise StoreError(
                    f"{target} was taken over at {existing.get('superseded_at')} by "
                    f"{existing['superseded_by']} and this hands the same scope to "
                    f"{successor}. A supersession is written once: the record says "
                    f"which Parent carries this Item now, and accepting this would "
                    f"leave two records each claiming to have succeeded the same one "
                    f"while the store named only one of them. Which Parent carries "
                    f"the Item is a question for a person and not a later write")
            # The three things the docstring's first paragraph says `by` IS,
            # checked in the order a caller would repair them.
            carried_by = self._data["checkpoints"].get(successor)
            if carried_by is None:
                raise StoreError(
                    f"{target} would be handed to {successor}, and this store holds "
                    f"no checkpoint under that ref. That is «nobody owns this» with "
                    f"a name in it: the record would say a Parent carries the Item, "
                    f"and `live_checkpoints` would answer for it while nothing that "
                    f"could be resumed exists. Name the checkpoint that took the "
                    f"scope over, and write it before handing the scope to it")
            if str(carried_by.get("work_item") or "") != str(existing.get("work_item")
                                                            or ""):
                raise StoreError(
                    f"{target} carries "
                    f"{existing.get('work_item') or 'an unnamed Item'} and "
                    f"{successor} is a checkpoint of "
                    f"{carried_by.get('work_item') or 'an unnamed Item'}. A scope "
                    f"changes hands inside one Item — «the scope is the Item; the "
                    f"epoch is only how it changes hands» — so this would say one "
                    f"Item is carried by a record of another")
            mine, theirs = existing.get("epoch"), carried_by.get("epoch")
            if not isinstance(mine, int) or not isinstance(theirs, int):
                raise StoreError(
                    f"{target} or {successor} carries an epoch this store cannot "
                    f"compare (it writes integers), so whether {successor} is later "
                    f"than the scope it is being handed cannot be established. A "
                    f"handover whose direction is unknown is not one this store can "
                    f"record")
            if successor == target:
                raise StoreError(
                    f"{target} cannot be handed to itself. A supersession names "
                    f"what took the scope over, and a record that replaced itself "
                    f"took nothing over — it would write `superseded_by` onto the "
                    f"record it points at, which is a scope nobody owns looking "
                    f"exactly like one that was handed on")
            if theirs <= mine:
                raise StoreError(
                    f"{successor} is epoch {theirs} and {target} is epoch {mine}: a "
                    f"supersession goes to a LATER epoch of the same Item, and this "
                    f"would hand the current scope to a record from before it. The "
                    f"two records would then name each other as the successor — each "
                    f"claiming to have replaced the other, and no live owner at all")
            updated = dict(existing)
            updated["superseded_by"] = successor
            updated["superseded_at"] = str(at)
            self._data["checkpoints"][target] = updated
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

    def _next_grant_epoch(self) -> int:
        """The next grant generation, from the store's own durable counter.

        Called ONLY from inside a `_mutation()`, so read-increment-write is one
        step under the lock. Monotonic across restarts because the counter lives
        in the document, and never derived from a grant COUNT: revoking a grant
        must not let a later grant reuse its number.
        """
        current = int(self._data.get("grant_epoch") or 0) + 1
        self._data["grant_epoch"] = current
        return current

    def issue_grant(self, evidence: dict, *, now: str,
                    os_principal: str | None = None,
                    owner_policy_resolver=None) -> dict:
        """Persist a provider-approved scope and its verified issuance atomically.

        `os_principal` is the OS principal the calling process was attested as at
        the socket — `uid:<n>` derived from `SO_PEERCRED` by `os_principal_of`,
        never a field of the request or of the signed payload. The daemon passes
        it from the kernel peer of the call that asked for the grant; a caller
        that has no peer (an in-process call) stores the grant UNBOUND, and the
        read refuses it. That is the fail-closed side: an unbound grant
        authorizes nothing, and nothing here backfills one.

        `owner_policy_resolver` is the OWNER'S uid -> actor -> scope binding.
        When it is supplied, the grant is issued only if the live policy covers
        the issuing kernel uid, the verified context's actor, and the whole
        approved scope; the grant stores the policy ref, policy epoch and the
        audited actor. A resolver that returns None/raises, or a policy that
        does not match, refuses `BLOCKED_OWNER_POLICY` and writes nothing. The
        resolver is a read; it never mints a grant. A daemon with no resolver
        refuses at the route before calling here.
        """
        actor = _actor_module()
        with self._mutation():
            payload = evidence.get("payload") if isinstance(evidence, dict) else None
            scope = payload.get("grant") if isinstance(payload, dict) else None
            ref = scope.get("grant_ref") if isinstance(scope, dict) else None
            existing = self._data["grants"].get(ref) if isinstance(ref, str) else None
            if existing is not None:
                issuance = existing.get("issuance") or {}
                if issuance.get("evidence") == evidence:
                    # Readback only: a retry never replaces scope or clears
                    # revocation. It is NOT a fresh authorization, so the live
                    # owner policy is re-read before the old grant is returned:
                    # a policy revoked/expired since issuance makes a replay
                    # `issued: false`, never `issued: true`.
                    if owner_policy_resolver is not None:
                        policy_mod = _owner_policy_module()
                        peer = ({"uid": int(str(os_principal).split(":", 1)[1])}
                                if os_principal is not None
                                and re.fullmatch(r"uid:\d+", str(os_principal))
                                else None)
                        context = self._data["contexts"].get(
                            existing.get("delegated_by"))
                        try:
                            policy = owner_policy_resolver.resolve(
                                policy_ref=existing.get("owner_policy_ref"),
                                grant=existing, context=context, peer=peer,
                                project=None, item_ref=None, action=None,
                                now=now)
                        except Exception as exc:  # noqa: BLE001 - a refusal
                            raise StoreError(
                                f"BLOCKED_OWNER_POLICY: the owner-policy "
                                f"resolver did not answer "
                                f"({type(exc).__name__})") from exc
                        refusal = policy_mod.check_policy_binding(
                            grant=existing, context=context, peer=peer,
                            project=None, item_ref=None, action=None, now=now,
                            policy=policy)
                        if refusal is not None:
                            raise StoreError(
                                f"{refusal['reason']}: {refusal['because']}")
                    return json.loads(json.dumps(existing))
                raise StoreError(f"{ref} already has a grant; issuance cannot replace or upgrade it")
            try:
                approved, context = actor.verify_delegation(evidence, now=now)
            except actor.Rejected as exc:
                raise StoreError(str(exc)) from exc
            if "os_principal" in approved:
                raise StoreError(
                    "os_principal is bound from the kernel peer credential of the "
                    "issuing call, never taken from the signed payload. A document "
                    "that names its own OS principal is one the signer could have "
                    "written for somebody else")
            # The verifier's allowlist is not the only guard. The provider
            # approval may not name any field the OWNER binding writes: the
            # store drops them and records the refusal, so a future/relaxed
            # verifier cannot smuggle `owner_policy_*`/`actor_ref`/`epoch`/
            # revocation into a grant. `os_principal` is refused above.
            smuggled_owner_fields = sorted(
                set(approved) & (set(OWNER_WRITTEN_GRANT_FIELDS)
                                 - {"os_principal"}))
            if smuggled_owner_fields:
                approved = {
                    key: value for key, value in approved.items()
                    if key not in smuggled_owner_fields}
            policy_fields = {}
            if owner_policy_resolver is not None:
                if (os_principal is None
                        or not re.fullmatch(r"uid:\d+", str(os_principal))):
                    raise StoreError(
                        "BLOCKED_OWNER_POLICY: a grant with an owner-policy "
                        "binding must come from a call with a kernel peer; an "
                        "in-process call has no uid to bind")
                policy_mod = _owner_policy_module()
                peer = {"uid": int(str(os_principal).split(":", 1)[1])}
                try:
                    policy = owner_policy_resolver.resolve(
                        policy_ref=None, grant=approved, context=context,
                        peer=peer, project=None, item_ref=None, action=None,
                        now=now)
                except Exception as exc:  # noqa: BLE001 - a resolver failure is a refusal
                    raise StoreError(
                        f"BLOCKED_OWNER_POLICY: the owner-policy resolver did "
                        f"not answer ({type(exc).__name__})") from exc
                refusal = policy_mod.check_issue_policy(
                    grant=approved, context=context, peer=peer, now=now,
                    policy=policy)
                if refusal is not None:
                    raise StoreError(
                        f"{refusal['reason']}: {refusal['because']}")
                policy_fields = {
                    "owner_policy_ref": policy.policy_ref,
                    "owner_policy_epoch": int(policy.epoch),
                    "owner_policy_issued_by": str(policy.issued_by),
                    "owner_policy_issued_at": str(policy.issued_at),
                    "actor_ref": str(context["actor_ref"]),
                }
            recorded = {**approved, **policy_fields, "revoked": False,
                        # Minted HERE, by the store, from a durable monotonic
                        # counter — never supplied by a caller.
                        "epoch": self._next_grant_epoch(),
                        "issuance": {
                            "schema": actor.ISSUANCE_SCHEMA,
                            "accepted_at": now, "evidence": evidence,
                            **({"refused_owner_fields": smuggled_owner_fields}
                               if smuggled_owner_fields else {}),
                        }}
            if os_principal is not None:
                if not re.fullmatch(r"uid:\d+", str(os_principal)):
                    raise StoreError(
                        "os_principal must be `uid:<non-negative integer>`, the "
                        "shape `os_principal_of` derives from a kernel peer")
                recorded["os_principal"] = str(os_principal)
            # Own a snapshot, not a caller's mutable dictionary.
            recorded = json.loads(json.dumps(recorded))
            self._data["contexts"][context["context_ref"]] = context
            self._data["grants"][approved["grant_ref"]] = recorded
            self._flush()
            return json.loads(json.dumps(recorded))

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
        if "issuance" in record:
            raise StoreError("issuance is owner-written; use issue_grant with signed provider evidence")
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
        if "epoch" in record:
            raise StoreError(
                "a grant may not be stored carrying an `epoch`. The epoch is minted "
                "by the store from a durable monotonic counter; an epoch a caller "
                "could choose is one a caller could keep after it was superseded")
        if "os_principal" in record:
            raise StoreError(
                "a standing delegation may not name its own `os_principal`. The "
                "binding is written by `issue_grant` from the kernel peer of the "
                "issuing call; a uid a caller could choose is an identity the "
                "caller could claim for somebody else. A grant stored here is "
                "UNBOUND, and the verify read refuses it until it is issued "
                "through a call that has a peer")
        for field in ("owner_policy_ref", "owner_policy_epoch",
                      "owner_policy_issued_by", "owner_policy_issued_at",
                      "actor_ref"):
            if field in record:
                raise StoreError(
                    f"a standing delegation may not name its own `{field}`. The "
                    f"owner binding is written by `issue_grant` from the owner "
                    f"policy resolver and the verified context; a binding a "
                    f"caller could choose is one the caller could claim for "
                    f"somebody else")
        smuggled = [f for f in REVOCATION_FIELDS
                    if f in record and record[f] != (False if f == "revoked" else None)]
        if smuggled:
            raise StoreError(
                f"a grant may not be stored carrying {', '.join(smuggled)}. A "
                f"delegation ends by revocation (`revoke_grant`), and a grant whose "
                f"revocation a put could write is one a second put could un-revoke")
        staged = {k: v for k, v in record.items() if k not in OWNER_WRITTEN_GRANT_FIELDS}
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
                          if k not in OWNER_WRITTEN_GRANT_FIELDS}
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
            recorded = {**staged, "revoked": False,
                        "epoch": self._next_grant_epoch()}
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
                # Declared here and not merely absent, for the reason `retired_at`
                # is: `runtime/continuation.attempts_state` reads these three to
                # answer «was this result taken up», and on a record written
                # before `accept_admission` existed the honest reading of a
                # missing key would be indistinguishable from a store that
                # answered None. One shape, written once, at admission.
                "accepted_at": None,
                "accepted_by": None,
                "accepted_locator": None,
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

        Written once, and the FIRST call is the one that writes it. Two facts
        are fixed by it and both, not one: WHEN the cycle stopped holding its
        reservation, and HOW it ended. A second call that moved the instant
        would move it to whenever somebody last mentioned the cycle — the same
        defect `revoke_grant` is written to avoid, one collection over. A second
        call that moved the OUTCOME is the same defect in the other field, and
        it is refused rather than absorbed for the reason `confirm_admission`
        refuses a second answer about one phase: «picking the newer one here
        would hide the disagreement rather than record it».

        That second field is why this rule is not merely tidy. `outcome` was
        defended as «the first instant is kept» and never argued about on its own
        terms, and it had no reader that decided anything — until
        `runtime/continuation.attempts_state`, which renders it as the answer to
        «did this child attempt finish», on the line a resuming Parent reads
        before deciding whether to dispatch a second child. A record that says
        `DELIVERED` while the store was also told `ABANDONED` is a disagreement
        somebody has to resolve, and a store that answered with the first one and
        said nothing about the second would be answering from a choice nobody
        made.

        «No outcome» is a value here and not an absent one, so a later call may
        neither fill it in nor replace it: what the first call recorded is how
        the cycle ended, and a retirement that left the outcome unnamed recorded
        exactly that. A later call that names NO outcome makes no claim about the
        ending at all, so it is a retry and it is idempotent — it changes nothing,
        and refusing it would tell a caller it disagreed when it said nothing. A
        later call that names one must name the recorded one; naming a different
        one, or naming one where the first call left none, is a change and is
        refused by name, with the recorded value in the refusal.

        Retiring is not un-admitting. The record stays, and it stays saying
        what it was admitted for: `intake/daily_cycle.select_focus` computes a
        cycle's after-the-fact review from what was selected, and a store that
        dropped the record would make a cycle that ran indistinguishable from
        one that was never admitted.
        """
        key = str(cycle_ref or "").strip()
        if not key:
            raise StoreError("retiring an admission needs the cycle's ref")
        named = str(outcome) if outcome else None
        with self._mutation():
            record = self._data["admissions"].get(key)
            if record is None:
                raise StoreError(
                    f"{key} has no admission to retire; closing one this store "
                    f"does not hold would make a released reservation look like "
                    f"a reservation that was never taken")
            if record.get("retired_at"):
                if named is None or record.get("outcome") == named:
                    return record
                recorded = (f"the record says {record['outcome']!r}"
                            if record.get("outcome")
                            else "the record names no outcome")
                says = f"with outcome {named!r}"
                raise StoreError(
                    f"{key} was retired at {record['retired_at']} and this retires "
                    f"it again {says}, where {recorded}. A retirement is written "
                    f"once and the first call fixes both the instant the "
                    f"reservation was released and how the cycle ended; accepting "
                    f"this would move the ending to whenever somebody last "
                    f"mentioned the cycle. A different ending is a question for a "
                    f"person and not a later write")
            record["retired_at"] = str(at)
            if named:
                record["outcome"] = named
            self._data["admissions"][key] = record
            self._flush()
            return record

    def accept_admission(self, cycle_ref: str, *, at: str, by: str,
                         locator: str) -> dict:
        """Record that this cycle's result was taken up. Written once.

        THE OTHER HALF OF «LOOK UP THE ATTEMPT». `retire_admission` records that
        a cycle ended and how; it says nothing about whether what the cycle
        produced reached the Item, and on this node nothing else did either — so
        a resuming Parent could read «this attempt finished» and still not know
        whether to take its result up or to dispatch a second child.
        `docs/ABILITY-OPERATING-MODEL.md` asks every asynchronous edge to answer
        «是否已被接受» and names the state a result waits in when no receiver is
        available («结果durable保存为待接收»); `docs/daily-work-cycle.md` puts
        «一次child结果只被接受一次» in B4's acceptance list; and the handoff
        object has a row for it — `accepted_by`, «接收者及其验证结果，所接受的确切
        输入输出版本». This is where that fact is written, once.

        WHY THE EDGE AND NOT THE RECORD. A checkpoint restates the Item's state:
        `evidence_locators` accumulates what has been accepted and every epoch
        rewrites it, so it can say what is accepted now and cannot say that an
        act happened ONCE. The admission is the edge — the cycle, its grant, its
        reservation, its lease, its receipt — and a take-up belongs to it. Two
        levels, two facts, neither a copy of the other.

        WHY NOT THE RECEIPT. `runtime/bounded_invocation.invoke_once` writes one
        receipt per action key and replays it verbatim for the same input, and its
        `status` is how the EXECUTION ended. Acceptance is not part of an
        execution, and a Parent writing into that store's namespace would be a
        second writer for a record whose whole rule is one writer.

        THE TWO GUARDS ARE ABOUT WHAT CAN EXIST. `retired_at` is this store's word
        for «this cycle's outcome is recorded», and until it is set there is
        nothing to take up. `DISPATCHED` is its word for «the invocation's receipt
        was reported», and a cycle that never reached it has nothing that could
        produce a result.

        THERE IS NO CATCH-UP FOR THE SECOND ONE, AND THE REFUSAL SAYS SO RATHER
        THAN INVENTING ONE. A crash between performing the step and reporting it
        leaves exactly that state, and the obvious remedy — report the receipt
        now, then accept — does not exist: `confirm_admission` refuses a cycle
        that has already been retired, which is the same rule that keeps a
        second confirmation from rewriting a settled phase. So a cycle retired
        below `DISPATCHED` was recorded as abandoned, and this store will not
        take that record apart afterwards to make a result appear. If the
        invocation really did happen, the ending is wrong and a person has to
        settle which of the two records is the mistake. Naming a remedy that the
        next verb would refuse is worse than naming none.

        WRITTEN ONCE, AND THE FIRST CALL WRITES BOTH FACTS. Who took the result up
        and which locator was taken up, both, not one. A later call that repeats
        both is a retry and is idempotent, including one arriving at a later
        instant: it is the same statement. A later call naming a different Parent,
        or a different locator, is refused by name with the record's own values in
        the refusal, for the reason `confirm_admission` gives about one step
        reported twice — «picking the newer one here would hide the disagreement
        rather than record it» — and «一次child结果只被接受一次» has no exception
        for a second Parent that did not look first.

        WHAT IT DOES NOT DO: it does not decide the verdict.
        `docs/COLLABORATION-CONTRACT.md` gives the receiving side three answers
        about a producer's work — accept it, require a specific revision, register
        the accurate gap — and this verb performs the FIRST. The other two are the
        Parent's next `work checkpoint`, where a required revision becomes a
        remaining result and a gap becomes an unresolved effect; `outcome` is free
        text on this store and a verdict vocabulary invented here would be a
        second definition of what that record says.
        """
        key = str(cycle_ref or "").strip()
        who = str(by or "").strip()
        where = str(locator or "").strip()
        if not who:
            raise StoreError(
                "an acceptance names the Parent that took the result up. The "
                "handoff object's `accepted_by` is «接收者及其验证结果，所接受的"
                "确切输入输出版本», and a take-up that cannot be attributed to a "
                "Parent is not that record — the next reader could not tell "
                "whether the Item's owner or a stranger accepted it")
        if not where:
            raise StoreError(
                "an acceptance names the locator of what was taken up. The same "
                "field carries «所接受的确切输入输出版本», so an acceptance with no "
                "locator would claim a result was accepted while saying nothing "
                "about which one — the fact a resuming Parent needs in order to "
                "see it in the record")
        with self._mutation():
            record = self._data["admissions"].get(key)
            if record is None:
                raise StoreError(
                    f"{key} has no admission. An acceptance is a report about a "
                    f"result taken up from a delegated cycle, and recording one "
                    f"for a cycle this store never admitted would put an accepted "
                    f"result on a cycle with no grant behind it")
            if not record.get("retired_at"):
                raise StoreError(
                    f"{key} is still open at {record.get('phase')}. `retired_at` "
                    f"is this store's word for «this cycle's outcome is "
                    f"recorded», and until it is set there is no result to take "
                    f"up: an acceptance now would name a result nothing has "
                    f"produced yet. A cycle that ran and did not retire is a "
                    f"cycle whose ending nobody has written down, which is a "
                    f"different state from a result that arrived")
            if record.get("phase") != ADMISSION_PHASES[-1]:
                raise StoreError(
                    f"{key} was retired at {record['retired_at']} while at "
                    f"{record.get('phase')} and never reached "
                    f"{ADMISSION_PHASES[-1]}. A result can only come from an "
                    f"invocation whose receipt was reported, and that phase is "
                    f"this store's word for it — so this cycle was recorded as "
                    f"abandoned rather than finished, and there is no result to "
                    f"take up. `confirm_admission` refuses a retired cycle too, "
                    f"and deliberately: reporting the receipt now would rewrite "
                    f"the ending this store has already accounted for. So there "
                    f"is no catch-up for a later write to make here. If the "
                    f"invocation did happen, this ending is wrong — it recorded "
                    f"{record.get('outcome')!r} — and which of the two records is "
                    f"the mistake is a person's to settle")
            recorded = record.get("accepted_at")
            if recorded:
                if (record.get("accepted_by") == who
                        and record.get("accepted_locator") == where):
                    return record
                raise StoreError(
                    f"{key} was taken up at {recorded} by "
                    f"{record.get('accepted_by')!r} as "
                    f"{record.get('accepted_locator')!r}, and this takes it up by "
                    f"{who!r} as {where!r}. A result is accepted once and the "
                    f"first call is the one that writes it: a second acceptance "
                    f"is either the same Parent retrying, which is idempotent "
                    f"above, or a disagreement about who holds this Item and "
                    f"which result is in it. That is a question for a person and "
                    f"not a later write")
            record["accepted_at"] = str(at)
            record["accepted_by"] = who
            record["accepted_locator"] = where
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
