"""SQLite runtime store for ServerOS v2."""

from __future__ import annotations

import datetime
import json
import hashlib
import math
import os
import re
import sqlite3
import time
from contextlib import closing, contextmanager
from pathlib import Path

from .model import (
    Activity,
    ActivityState,
    ContextMode,
    ExecutionContext,
    ItemState,
    Outcome,
    Run,
    RunCancelReason,
    RunState,
    WorkItem,
    now_iso,
)
from .state_machine import RUN_TERMINAL, reopen, transition, transition_run
from .migrations import migrate


_PREFIX = re.compile(r"^[A-Z][A-Z0-9]{1,9}$")
_SECRET = re.compile(r"(token|secret|cookie|password|api[_-]?key)", re.I)

def _reject_secrets(value):
    text=json.dumps(value, ensure_ascii=False)
    if _SECRET.search(text): raise ValueError("secret-like evidence metadata is forbidden")


# ---------------------------------------------------------------------------
# The lease clock. ONE fixed-width UTC spelling, so that two processes can
# compare two `expires_at` values as strings and agree.
#
# `now_iso()` (model.py) is `datetime.isoformat()` and it is NOT fixed width:
# it drops the microsecond field when it happens to be zero, so
# `2026-10-16T14:30:00+00:00` (25 chars) and `2026-10-16T14:30:00.123456+00:00`
# (32 chars) can both appear — and `...:00+00:00` sorts AFTER
# `...:00.123456+00:00` at the same second under a string comparison. A reader
# one repository over (`serveros/context/resolver.py#held_leases`) filters this
# column with SQL, so the format is a cross-repo contract and is pinned here:
# `YYYY-MM-DDTHH:MM:SSZ`, second precision, Z suffix. The expiry is rounded UP
# (`math.ceil`), so a TTL is never shortened by the truncation.
# ---------------------------------------------------------------------------

_LEASE_STAMP = "%Y-%m-%dT%H:%M:%SZ"

#: A lease is bounded. Without an upper bound one acquire can write an expiry
#: far enough ahead that no successor can ever take the scope, and a value that
#: can be arbitrarily large is not a TTL. Seven days is longer than any single
#: Work turn and short enough that a crashed holder's scope is recoverable.
_LEASE_MAX_TTL_S = 7 * 24 * 60 * 60


def _lease_stamp(epoch: float) -> str:
    return datetime.datetime.fromtimestamp(
        math.ceil(epoch), datetime.timezone.utc).strftime(_LEASE_STAMP)


def _lease_epoch(stamp: str) -> float:
    try:
        return datetime.datetime.strptime(
            str(stamp), _LEASE_STAMP).replace(
            tzinfo=datetime.timezone.utc).timestamp()
    except (TypeError, ValueError) as exc:
        raise LeaseError(
            "LEASE_ROW_INVALID",
            f"{stamp!r} is not the pinned lease timestamp {_LEASE_STAMP!r}; "
            f"the row cannot be aged, so it authorizes nothing and may not be "
            f"treated as expired and taken over") from exc


def _lease_ttl(ttl_s) -> float:
    """A TTL is a positive, finite, bounded number of seconds.

    Zero and negative values used to be written as an ALREADY-EXPIRED row: the
    acquire returned normally, `active_lease` immediately reported `None`, and
    the caller had a lease that never was one. NaN/Infinity raised raw
    `ValueError`/`OverflowError` from `math.ceil`, which is neither a refusal
    word nor a rolled-back write. Both are refusals now, before the transaction.
    """
    if isinstance(ttl_s, bool) or not isinstance(ttl_s, (int, float)):
        raise LeaseError(
            "LEASE_TTL_INVALID",
            f"ttl_s must be a number of seconds, not {type(ttl_s).__name__}")
    value = float(ttl_s)
    if not math.isfinite(value) or value <= 0:
        raise LeaseError(
            "LEASE_TTL_INVALID",
            f"ttl_s must be finite and greater than zero, not {ttl_s!r}; a "
            f"non-positive or non-finite TTL is a lease that is expired before "
            f"it is written")
    if value > _LEASE_MAX_TTL_S:
        raise LeaseError(
            "LEASE_TTL_INVALID",
            f"ttl_s {value!r} exceeds the {_LEASE_MAX_TTL_S}s bound; an "
            f"unbounded lease would let one holder deny a scope forever")
    return value


def _lease_expiry(now_e: float, ttl_s: float) -> str:
    try:
        stamp = _lease_stamp(now_e + ttl_s)
    except (OverflowError, OSError, ValueError) as exc:
        raise LeaseError(
            "LEASE_TTL_INVALID",
            f"ttl_s {ttl_s!r} from {now_e!r} is outside the representable "
            f"clock range") from exc
    if _lease_epoch(stamp) <= now_e:
        # `ceil` should make this impossible for a positive TTL; if it ever
        # does happen, writing the row would be a successful acquire of an
        # already-expired lease, which is the failure mode this refuses.
        raise LeaseError(
            "LEASE_TTL_INVALID",
            f"ttl_s {ttl_s!r} does not move the expiry past {now_e!r} at the "
            f"pinned second precision")
    return stamp


def _lease_now(now) -> float:
    """Epoch seconds, finite.

    A caller-supplied `now` is a test seam on the store primitives. What must
    never happen is a NaN clock: every `expires_at > now` comparison is False
    against NaN, so a NaN would let its caller take over a LIVE lease. Infinity
    is refused for the same reason in the other direction — a clock that cannot
    be aged cannot be a fence.
    """
    if isinstance(now, bool):
        raise LeaseError("LEASE_CLOCK_INVALID", "a boolean is not an instant")
    try:
        value = time.time() if now is None else float(now)
    except (TypeError, ValueError) as exc:
        raise LeaseError(
            "LEASE_CLOCK_INVALID",
            f"now must be epoch seconds, not {now!r}") from exc
    if not math.isfinite(value):
        raise LeaseError(
            "LEASE_CLOCK_INVALID",
            f"now must be finite, not {now!r}; a non-finite clock makes the "
            f"expiry comparison meaningless and could take over a live lease")
    return value


def _sha256_path(path: Path) -> str | None:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def _fsync_dir(path: Path) -> None:
    """Make a rename durable. A failure here is not the rename's failure."""
    try:
        descriptor = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _read_finalize_record(evidence) -> dict | None:
    """Read the durable finalize file and require it to match its anchor.

    Returns the stored runner receipt when the file is a T10_VERIFIED record,
    `None` when it is an UNSIGNED record, and raises `ReceiptError` when the
    record is missing, torn, tampered with, or contradicts itself.

    The evidence row's `sha256` is the anchor. A missing file used to be
    RECONSTRUCTED from the row as an UNSIGNED success — inventing a record and
    silently dropping a receipt the row's anchor covered. A `.tmp` left by a
    crash is promoted only when its bytes hash to that anchor. Nothing is ever
    synthesized.
    """
    from .authority import ReceiptError

    locator = Path(evidence["locator"])
    expected = str(evidence["sha256"])
    temporary = Path(str(locator) + ".tmp")
    if not locator.exists():
        if not temporary.exists():
            raise ReceiptError(
                "EVIDENCE_MISSING",
                f"the Run is terminal but its finalize evidence file "
                f"{str(locator)!r} is missing; a conclusion whose only durable "
                f"record is gone is not a conclusion")
        digest = _sha256_path(temporary)
        if digest != expected:
            raise ReceiptError(
                "EVIDENCE_HASH_MISMATCH",
                f"the crash-left {str(temporary)!r} hashes to {digest!r}, not "
                f"the evidence row's anchor {expected!r}; it is not promoted")
        temporary.replace(locator)
        _fsync_dir(locator.parent)
    digest = _sha256_path(locator)
    if digest != expected:
        raise ReceiptError(
            "EVIDENCE_HASH_MISMATCH",
            f"the finalize evidence file {str(locator)!r} hashes to {digest!r}, "
            f"not the evidence row's anchor {expected!r}; the file was changed "
            f"after the row was written")
    try:
        payload = json.loads(locator.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ReceiptError(
            "EVIDENCE_TORN",
            f"the finalize evidence file {str(locator)!r} is not readable "
            f"JSON") from exc
    if not isinstance(payload, dict):
        raise ReceiptError(
            "EVIDENCE_TORN",
            f"the finalize evidence file {str(locator)!r} is not an object")
    authorization = payload.get("authorization")
    receipt = payload.get("runner_receipt")
    if authorization is None and receipt is None:
        # A record written before the label existed. It claims no receipt, so
        # UNSIGNED is the honest reading rather than a guess.
        return None
    if authorization == "T10_VERIFIED":
        if not isinstance(receipt, dict):
            raise ReceiptError(
                "EVIDENCE_TORN",
                "the record says T10_VERIFIED but carries no runner receipt; a "
                "label without the receipt it labels is not evidence")
        return receipt
    if authorization == "UNSIGNED":
        if receipt is not None:
            raise ReceiptError(
                "EVIDENCE_TORN",
                "the record says UNSIGNED but carries a runner receipt; a reader "
                "must not have to guess which half is the record")
        return None
    raise ReceiptError(
        "EVIDENCE_TORN",
        f"the record carries authorization {authorization!r}; only UNSIGNED or "
        f"T10_VERIFIED is a finalize record")


def _verdict_word(verdict) -> str:
    """One judgement, one spelling — the membership is left open on purpose.

    A verdict is judged by its WORD. `finalize_run` concludes a Run SUCCEEDED on
    `PASS` and FAILED on anything else, and `record_evidence` treats a changed
    word as a conflict, because the word is part of the evidence's identity.
    Nothing used to state which spelling was the one that counted, so `pass` and
    `PASS` were two different facts: the first concluded the Run FAILED and the
    second concluded it SUCCEEDED. A caller who wrote the lower-case one got
    back `{"state": "FAILED", "verdict": "pass"}` — a record that contradicts
    itself, produced by a call that reported no error.

    The membership stays open because the corpus is open: `PARTIAL`,
    `INCONCLUSIVE`, `SUCCESS` and `BUDGET_EXHAUSTED` are all recorded on this
    node's Runs, and closing the set would refuse history rather than spell it
    consistently. What is fixed here is the spelling.

    Case-folding is applied to what is compared as well as to what is written,
    so a row recorded before this change still replays as the SAME judgement
    instead of as a conflict about a different one.
    """
    return str(verdict).strip().upper()


_CANDIDATE_CONCLUDED = ("MERGED", "REJECTED", "PROMOTED")


def _refuse_if_concluded(row, action, *, candidate_id):
    """A decision that has been taken is not overwritten by the next one.

    Measured on a fresh store, before this guard existed: `candidate merge cnd-1`
    then `candidate reject cnd-1` both returned 0 and left the row REJECTED — one
    decision silently replacing another, with nothing said. `candidate reject`
    followed by `promote --create-new` then produced a real Item from a
    requirement a human had rejected. Neither call reported a problem.

    The states that conclude a candidate are the three below. `DEFERRED` is not
    one of them on purpose: deferring is how a human says "not yet", so a
    deferred candidate is still decidable. That is also the way to revise a
    candidate without deciding it twice — park it first, then decide.

    Every refusal names the state read off the stored row, not the state this
    call asked for: a caller told the wrong state goes and looks at the wrong
    thing.
    """
    state = row["state"]
    if state in _CANDIDATE_CONCLUDED:
        raise ValueError(
            f"candidate {candidate_id} is {state}: {action} would overwrite a "
            f"decision that has already been taken. A concluded candidate is not "
            f"re-decided; a deferred one still is."
        )
    return state


def _changed_fields(stored, asked):
    """Which of the fields this call supplied the stored row does not already say.

    Only ever called once a row has been found by this call's own key, so an
    empty answer IS the replay and anything in it IS the conflict. Comparing the
    stored row against the arguments is what makes that readable: `INSERT OR
    IGNORE` returns no error whether or not it wrote, so the write cannot tell
    the two cases apart, and the row read back afterwards becomes a statement
    about the reader rather than about the call.

    Measured through the installed CLI before this existed: `observation` with a
    changed `--content-hash` exited 0 and printed the OLD hash, and `source` with
    a changed `--locator` did the same. The caller was told a value they never
    wrote had been accepted, and the call reported no problem. `create_candidate`
    settled the shape of this fix on 2026-09-25; the three writers below are the
    same defect in the three that did not get it.
    """
    return {k: (stored[k], v) for k, v in asked.items() if stored[k] != v}


def _conflict_sentence(kind, key, diff):
    """The refusal, naming BOTH sides of every field that differs.

    A refusal is read as a statement of fact, so it states the stored value first
    and the caller's second, and it spells both out: a caller told only their own
    value has learned nothing about what is already there, which is exactly what
    they need in order to decide whether to reuse it or to record something else.
    The order of `diff` is the order the call site supplies its fields, so the
    field the caller is most likely to have got wrong is the one read first.
    """
    naming = "; ".join(
        f"{k} is {s!r} in the store and this call says {a!r}"
        for k, (s, a) in diff.items())
    return f"{kind} {key} already exists with different content: {naming}; nothing was written."


class ActiveWorkerAckRequired(ValueError):
    """A RUNNING Run was terminated without anyone saying they meant to.

    Its own type because the caller has to be able to tell this refusal from
    every other refusal in the same call path and to act on it — an MCP tool
    that reported it as a generic failure would leave an agent re-reading a
    message to find out whether to retry, and the answer here is not "retry",
    it is "a human has to say the worker may still be running".

    A subclass of `ValueError` so that callers written against the older,
    untyped refusal keep working.
    """

    reason_code = "ACTIVE_WORKER_ACK_REQUIRED"


class EvidenceConflict(ValueError):
    """A registered evidence locator was replayed with different bytes or facts."""

    reason_code = "EVIDENCE_CONFLICT"


class LeaseError(ValueError):
    """The exclusive Work write lease refused: acquisition, renewal or a commit.

    Its own type, and it carries a `reason_code`, because the four refusals here
    are repaired four different ways and a caller that got one generic "lease
    refused" would not know which:

      LEASE_HELD_BY_OTHER  somebody else holds the scope. Repair: wait, or take
                           it over after their TTL — do not write anyway.
      STALE_TOKEN          this caller was superseded (or never held it). Repair:
                           stop; the token moved, the process did not.
      LEASE_EXPIRED        this caller DID hold it, and ran past its TTL. Repair:
                           re-acquire, and do not commit on the old acquisition.
      NOT_HOLDER           there is no lease on the scope at all. Repair: acquire.

    A subclass of `ValueError` so a caller written against the older, untyped
    refusals keeps working, and matching `ActiveWorkerAckRequired` beside it.
    """

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = reason_code
        self.detail = detail
        super().__init__(f"{reason_code}: {detail}")


class ServerOSStore:
    def __init__(self, state_dir: str | Path, *, clock=None, read_only=False) -> None:
        state_dir = Path(state_dir)
        if not state_dir.is_absolute():
            raise ValueError("ServerOS state directory must be absolute")
        if clock is not None and not callable(clock):
            raise TypeError("clock must be a callable returning epoch seconds")
        self.state_dir = state_dir.resolve()
        #: The one clock the commit boundary trusts. Production leaves it as
        #: `time.time`; a caller that needs determinism injects a clock when it
        #: constructs the store, so the fence never reads a per-call clock.
        self._clock = clock or time.time
        self._read_only = read_only
        if read_only:
            self.db_path = self.state_dir / "serveros.db"
            from .owner_fence import file_identity
            self._owner_file_identity = file_identity(self.db_path)
            return
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.state_dir / "serveros.db"
        schema = (Path(__file__).with_name("schema.sql")).read_text(encoding="utf-8")
        with closing(self.connection()) as conn:
            conn.executescript(schema)
            migrate(conn)
            from .owner_fence import migrate as migrate_owner_fence
            migrate_owner_fence(conn)
        # Serialize first creation without replacing an existing actuator inode.
        # Read-only owner clients return above and create no lock/schema files.
        with self.transaction(immediate=True):
            from .migration_lock import initialize
            initialize(self.state_dir)
        from .owner_fence import file_identity
        self._owner_file_identity = file_identity(self.db_path)

    def owner_target_info(self):
        from .owner_fence import file_identity
        identity = file_identity(self.db_path)
        if identity != self._owner_file_identity:
            raise LeaseError("WORK_STORE_MISMATCH", "database replaced; stop and explicitly rebind owner")
        locations = {key: str(getattr(self, key, "") or "") for key in
                     ("owner_artifacts_root", "owner_workspace_root", "owner_continuation_receipts_root", "owner_git_executable")}
        body = {"file_identity": identity, **locations}
        return {**body, "store_binding": hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()}

    def owner_execution_context(self, request, data, grant):
        from .owner_context import execution_context
        return execution_context(self, request, data, grant)

    def clock(self) -> float:
        """The store's trusted instant, validated like every lease clock."""
        return _lease_now(self._clock())

    def _now(self, now) -> float:
        """`now` is an explicit test seam; absent it, use the store's clock."""
        return self.clock() if now is None else _lease_now(now)

    def connection(self) -> sqlite3.Connection:
        if self._read_only:
            from .owner_fence import file_identity
            if file_identity(self.db_path) != self._owner_file_identity:
                raise LeaseError("WORK_STORE_MISMATCH", "selected database changed")
            conn = sqlite3.connect(self.db_path.as_uri() + "?mode=ro", uri=True, timeout=30, isolation_level=None)
        else:
            conn = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.create_function("owner_commit_scope", 1, lambda item: 0)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA recursive_triggers = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn

    @contextmanager
    def transaction(self, immediate: bool = False):
        conn = self.connection()
        try:
            conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def register_project(self, project: str, prefix: str) -> None:
        if not project or not _PREFIX.fullmatch(prefix):
            raise ValueError("invalid project or Item prefix")
        try:
            with self.transaction(immediate=True) as conn:
                existing = conn.execute("SELECT prefix FROM projects WHERE project=?", (project,)).fetchone()
                if existing and existing["prefix"] != prefix:
                    raise ValueError(f"project {project} already uses prefix {existing['prefix']}")
                owner = conn.execute("SELECT project FROM projects WHERE prefix=?", (prefix,)).fetchone()
                if owner and owner["project"] != project:
                    raise ValueError(f"Item prefix already registered: {prefix}")
                # BEFORE INSERT guards run even for INSERT OR IGNORE. An
                # identical registration is a replay, not a project mutation.
                if existing is None:
                    conn.execute("INSERT INTO projects(project,prefix) VALUES (?,?)", (project, prefix))
                conn.execute(
                    "INSERT OR IGNORE INTO id_sequences(scope,kind,next_value) VALUES (?,?,1)",
                    (project, "item"),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"Item prefix already registered: {prefix}") from exc

    def allocate_item_id(self, project: str) -> str:
        with self.transaction(immediate=True) as conn:
            row = conn.execute("SELECT prefix FROM projects WHERE project=?", (project,)).fetchone()
            if row is None:
                raise KeyError(f"unknown project: {project}")
            seq = conn.execute(
                "SELECT next_value FROM id_sequences WHERE scope=? AND kind='item'", (project,)
            ).fetchone()["next_value"]
            conn.execute(
                "UPDATE id_sequences SET next_value=? WHERE scope=? AND kind='item'", (seq + 1, project)
            )
            return f"{row['prefix']}-{seq:04d}"

    # -- the exclusive Work write lease ------------------------------------
    #
    # ONE authoritative writer per (resource, current generation), enforced
    # here rather than by a convention. The `leases` table has been in
    # `schema.sql` since the first migration and until now nothing read or wrote
    # it (issue #237 B3): a mutual-exclusion mechanism that existed only as a
    # CREATE TABLE. This is that mechanism, on the SAME table and the SAME
    # `id_sequences` counter the Item allocator already uses — not a second
    # lease system, which `integration/components.yaml` refuses by name.
    #
    # The `token` is the fence, and it is MONOTONIC per resource (minted from
    # `id_sequences`), so that "is this writer the current one" is decidable from
    # the number alone and survives a restart. An owner+boolean would not: a
    # process that was superseded and is still running would look identical to
    # the one that replaced it. That is issue #237 T3/T6.

    def _lease_row(self, conn, resource: str):
        return conn.execute(
            "SELECT resource, owner, token, expires_at FROM leases WHERE resource=?",
            (resource,),
        ).fetchone()

    def acquire_lease(self, resource: str, *, owner: str, ttl_s: float,
                      now=None) -> dict:
        """Take the exclusive write lease, or refuse because it is held.

        A live lease — one whose `expires_at` is still ahead of `now` — is not
        taken over, whoever asks. An EXPIRED row is: it is a crashed or finished
        holder, and leaving it as a permanent lock would be the opposite failure
        (a scope nobody can ever write again). The successor is minted a HIGHER
        token, so the old holder's token is fenced the moment it is superseded.
        """
        if not isinstance(resource, str) or not resource.strip():
            raise LeaseError("NOT_HOLDER", "a lease names a resource; '' names none")
        if not isinstance(owner, str) or not owner.strip():
            raise LeaseError(
                "LEASE_OWNER_REQUIRED",
                f"a lease names an owner, not {owner!r}; an empty owner is a "
                f"row nobody can be fenced against")
        ttl = _lease_ttl(ttl_s)
        now_e = self._now(now)
        with self.transaction(immediate=True) as conn:
            return self._acquire_lease_tx(conn, resource, owner=owner, ttl=ttl, now_e=now_e)

    def _acquire_lease_tx(self, conn, resource, *, owner, ttl, now_e):
        row = self._lease_row(conn, resource)
        if row is not None and _lease_epoch(row["expires_at"]) > now_e:
            raise LeaseError(
                "LEASE_HELD_BY_OTHER",
                f"{resource!r} is held by {row['owner']!r} (token "
                f"{row['token']}) until {row['expires_at']}. An exclusive "
                f"write scope has one writer at a time; renewed holders call "
                f"renew_lease, and a superseded one has lost the token, not "
                f"just the turn")
        token = self._next_lease_token(conn, resource)
        expires = _lease_expiry(now_e, ttl)
        if row is None:
            conn.execute(
                "INSERT INTO leases(resource,owner,token,expires_at) VALUES (?,?,?,?)",
                (resource, owner, token, expires))
        else:
            conn.execute(
                "UPDATE leases SET owner=?, token=?, expires_at=? WHERE resource=?",
                (owner, token, expires, resource))
        return {"resource": resource, "owner": owner, "token": token,
                "expires_at": expires}

    def renew_lease(self, resource: str, *, owner: str, token: int,
                    ttl_s: float, now=None) -> dict:
        """Extend the TTL of a lease the caller still holds.

        Renewal does NOT mint a new token: the holder is unchanged, so its fence
        must not move. It refuses, in STALE_TOKEN, when the caller is no longer
        the holder — the case where a process kept running after it was
        superseded must not be able to renew authority it has already lost.
        """
        ttl = _lease_ttl(ttl_s)
        now_e = self._now(now)
        with self.transaction(immediate=True) as conn:
            return self._renew_lease_tx(conn, resource, owner=owner, token=token, ttl=ttl, now_e=now_e)

    def _renew_lease_tx(self, conn, resource, *, owner, token, ttl, now_e):
        row = self._lease_row(conn, resource)
        self._require_holder(row, resource, owner=owner, token=token)
        if _lease_epoch(row["expires_at"]) <= now_e:
            raise LeaseError(
                "LEASE_EXPIRED",
                f"{resource!r} lapsed at {row['expires_at']} and cannot be "
                f"renewed by its old holder — re-acquire it, which mints a "
                f"new token and fences the lapsed one")
        expires = _lease_expiry(now_e, ttl)
        conn.execute("UPDATE leases SET expires_at=? WHERE resource=?",
                     (expires, resource))
        return {"resource": resource, "owner": row["owner"],
                "token": int(row["token"]), "expires_at": expires}

    def release_lease(self, resource: str, *, owner: str, token: int,
                      now=None) -> dict:
        """Give the lease back. Only the current holder, by token, may."""
        with self.transaction(immediate=True) as conn:
            return self._release_lease_tx(conn, resource, owner=owner, token=token)

    def _release_lease_tx(self, conn, resource, *, owner, token):
        row = self._lease_row(conn, resource)
        self._require_holder(row, resource, owner=owner, token=token)
        conn.execute("DELETE FROM leases WHERE resource=?", (resource,))
        return {"resource": resource, "owner": owner, "token": int(token),
                "released": True}

    def _owner_scope(self, conn, item_ref):
        from .owner_fence import scope
        return scope(conn, item_ref)

    def _refuse_managed_effect(self, conn, *, item_ref=None, project=None):
        """Pre-effect interlock; caller must already hold BEGIN IMMEDIATE.

        The enrollment receipt uses the same SQLite writer lock, so the check
        cannot race first enrollment. Unsupported filesystem ports never gain
        permission from an owner scope marker.
        """
        if not conn.in_transaction:
            raise LeaseError("OWNER_COMMIT_FENCE_REQUIRED", "Work transaction required")
        row = conn.execute(
            "SELECT 1 FROM owner_operation_receipts r JOIN items_index i ON i.item_id=r.item_ref "
            "WHERE r.verb='lease.acquire' AND (r.item_ref=? OR i.project=?) LIMIT 1",
            (item_ref, project)).fetchone()
        if row:
            raise LeaseError("OWNER_COMMIT_FENCE_REQUIRED", "managed filesystem effect requires its owner port")

    def _require_live_owner_lease(self, conn, *, item_ref, principal, token):
        resource = f"work-write:{item_ref}"
        row = self._lease_row(conn, resource)
        self._require_holder(row, resource, owner=principal, token=token)
        if _lease_epoch(row["expires_at"]) <= self.clock():
            raise LeaseError("LEASE_EXPIRED", "the current Work lease expired")
        return dict(row)

    def _apply_owner_operation(self, conn, *, verb, item_ref, principal, arguments):
        """Closed operations on the owner's already fenced connection.

        This internal entry never opens or commits a second transaction. The
        owner service checks policy/grant/context and persists its receipt in
        this same connection. Arbitrary SQL and callbacks are not RPC inputs.
        """
        if not conn.in_transaction:
            raise LeaseError("OWNER_COMMIT_FENCE_REQUIRED", "an owner transaction is required")
        resource = f"work-write:{item_ref}"
        now = self.clock()
        if verb == "lease.acquire":
            active = conn.execute("SELECT 1 FROM migration_intents m JOIN items_index i ON i.project=m.project WHERE i.item_id=? AND m.status NOT IN ('PLANNED','COMPLETED','COMPENSATED')", (item_ref,)).fetchone()
            if active:
                raise LeaseError("OWNER_MIGRATION_ACTIVE", "project filesystem journal requires reconciliation")
            return self._acquire_lease_tx(conn, resource, owner=principal,
                                         ttl=_lease_ttl(arguments["ttl_s"]), now_e=now)
        token = arguments["token"]
        row = self._lease_row(conn, resource)
        self._require_holder(row, resource, owner=principal, token=token)
        if _lease_epoch(row["expires_at"]) <= now:
            raise LeaseError("LEASE_EXPIRED", "the current Work lease has expired")
        if verb == "lease.renew":
            return self._renew_lease_tx(conn, resource, owner=principal, token=token,
                                       ttl=_lease_ttl(arguments["ttl_s"]), now_e=now)
        if verb == "lease.release":
            return self._release_lease_tx(conn, resource, owner=principal, token=token)
        if verb == "item.transition":
            side = {k: arguments[k] for k in ("reason", "return_state", "unblock_condition") if k in arguments}
            result = self._transition_item_tx(conn, item_ref, arguments["target"], note=arguments.get("note", "owner operation"), **side)
            return {"item_ref": item_ref, **result.to_dict()}
        if verb == "item.reopen":
            return self._reopen_item_tx(conn, item_ref, arguments["reason"], arguments["source"]).to_dict()
        if verb == "item.outcome":
            return self._set_outcome_tx(conn, item_ref, arguments["outcome"]).to_dict()
        from dataclasses import asdict
        if verb == "activity.add":
            result = self._add_activity_tx(conn, Activity(item_ref, arguments["activity_ref"], arguments["kind"]), arguments.get("needs"))
            return asdict(result)
        if verb == "activity.transition":
            return asdict(self._transition_activity_tx(conn, item_ref, arguments["activity_ref"], arguments["target"]))
        if verb == "context.attach":
            return asdict(self._upsert_context_tx(conn, arguments["context"]))
        if verb == "run.begin":
            return asdict(self._begin_run_tx(conn, item_ref, arguments["activity_ref"], arguments["worker_ref"], arguments["idempotency_key"], arguments["artifact_root"], allocation=arguments.get("allocation")))
        if verb == "run.advance":
            return asdict(self._advance_run_tx(conn, item_ref, arguments["run_ref"], arguments["target"]))
        if verb == "run.terminate":
            options = {k: arguments[k] for k in ("reason_code", "target", "evidence", "operation", "acknowledge_active_worker", "dry_run") if k in arguments}
            return self._cancel_run_tx(conn, item_ref, arguments["run_ref"], reason=arguments["reason"], actor=principal, **options)
        raise LeaseError("UNSUPPORTED_OWNER_OPERATION", "the operation has no protected mutation implementation")

    def active_lease(self, resource: str, *, now=None) -> dict | None:
        """The live lease on a resource, or None. An expired row is None.

        The row is left in place when it is expired — the reader excludes it.
        Deleting it here would erase the only evidence that a holder ever
        existed, which is the crash case #237 asks to stay answerable.
        """
        now_e = self._now(now)
        with self.transaction() as conn:
            row = self._lease_row(conn, resource)
        if row is None or _lease_epoch(row["expires_at"]) <= now_e:
            return None
        return {"resource": row["resource"], "owner": row["owner"],
                "token": int(row["token"]), "expires_at": row["expires_at"]}

    def held_leases(self, *, now=None) -> list[str]:
        """Every resource with a LIVE lease, ascending. The T6 reader.

        This is the query `serveros/context/resolver.py#held_leases` means to
        make — resources currently claimed — and it filters `expires_at`, which
        the serveros reader did not (issue #237 B3/T6).
        """
        now_e = self._now(now)
        with self.transaction() as conn:
            rows = conn.execute(
                "SELECT resource, expires_at FROM leases").fetchall()
        return sorted(row["resource"] for row in rows
                      if _lease_epoch(row["expires_at"]) > now_e)

    def check_writer(self, resource: str, *, owner: str, token: int,
                     now=None) -> dict:
        """The fence, at the commit boundary. Raises `LeaseError` if fenced.

        Called by whatever is about to commit a protected write — not only when
        the write began. An authorization checked once before a long turn is not
        an authorization at the moment of the effect (issue #237 B1/T3), and a
        holder that ran past its TTL must be stopped here rather than trusted
        because it held the lease an hour ago.

        This call is a POINT CHECK, not an atomic commit: it releases its read
        transaction before the caller performs the effect, so a lease can expire
        and a successor can acquire in between. A caller that is about to write
        must use `lease_guard`, which holds the write transaction across the
        effect and re-reads the fence before the commit.
        """
        now_e = self._now(now)
        with self.transaction() as conn:
            row = self._lease_row(conn, resource)
        self._require_holder(row, resource, owner=owner, token=token)
        if _lease_epoch(row["expires_at"]) <= now_e:
            raise LeaseError(
                "LEASE_EXPIRED",
                f"{resource!r} lapsed at {row['expires_at']} and this writer "
                f"(token {token}) is past it. A lease authorizes the turn it was "
                f"acquired for, not every turn after")
        return {"authorized": True, "resource": resource, "owner": row["owner"],
                "token": int(row["token"]), "expires_at": row["expires_at"]}

    @contextmanager
    def lease_guard(self, resource: str, *, owner: str, token: int):
        """Hold the write fence across the WHOLE effect, not just a check.

        `check_writer` answers at one instant and the caller's write lands in a
        different transaction. That leaves the TOCTOU #237 B1/T3 names: the old
        writer checks, its lease expires, a successor acquires, and the old
        writer's effect still commits. The fence was never consulted between
        the check and the effect.

        This guard is the atomic form. It opens `BEGIN IMMEDIATE`, validates the
        holder and the expiry inside that transaction, yields the connection the
        effect must use, and re-validates both immediately before the commit.
        Because SQLite's write lock is held for the whole block, a successor
        cannot acquire between the check and the effect; because the expiry is
        re-read at the store's trusted clock (`clock()`), a turn that overran
        its TTL rolls back instead of landing. The block holds SQLite's single
        write lock, so keep it to the write it gates.

        `now` is deliberately NOT a parameter. A commit boundary must not accept
        a caller-chosen clock: passing a past `now` to `check_writer` can make an
        expired lease look live, which is exactly the bypass this method exists
        to remove.
        """
        if not isinstance(resource, str) or not resource.strip():
            raise LeaseError("NOT_HOLDER", "a lease names a resource; '' names none")
        started = self.clock()
        with self.transaction(immediate=True) as conn:
            row = self._lease_row(conn, resource)
            self._require_holder(row, resource, owner=owner, token=token)
            if _lease_epoch(row["expires_at"]) <= started:
                raise LeaseError(
                    "LEASE_EXPIRED",
                    f"{resource!r} lapsed at {row['expires_at']} before this "
                    f"effect started; the fence is read at the effect, not only "
                    f"when the turn began")
            yield conn
            finished = self.clock()
            current = self._lease_row(conn, resource)
            self._require_holder(current, resource, owner=owner, token=token)
            if _lease_epoch(current["expires_at"]) <= finished:
                raise LeaseError(
                    "LEASE_EXPIRED",
                    f"{resource!r} lapsed at {current['expires_at']} while this "
                    f"effect was running (finished at {finished}). The effect is "
                    f"rolled back: a lease authorizes the instant it covers, not "
                    f"the turn that overran it")

    @staticmethod
    def _require_holder(row, resource, *, owner, token) -> None:
        if row is None:
            raise LeaseError(
                "NOT_HOLDER",
                f"no lease is recorded for {resource!r}, so this caller holds "
                f"nothing to renew, release or commit on")
        if not isinstance(owner, str) or not owner.strip():
            raise LeaseError(
                "NOT_HOLDER",
                f"an owner must be a non-empty string, not {owner!r}; an empty "
                f"owner names no holder and cannot be fenced")
        # The token is the fence, so it is compared as the integer the store
        # minted. Coercion (`"1"`, `1.0`, `True`) turns a caller's typing
        # mistake into a match with a token it never received.
        if type(token) is not int:
            raise LeaseError(
                "STALE_TOKEN",
                f"a lease token is an integer, not {type(token).__name__}; a "
                f"coerced value is not the fence that was minted")
        if str(row["owner"]) != owner or int(row["token"]) != token:
            raise LeaseError(
                "STALE_TOKEN",
                f"{resource!r} is held by {row['owner']!r} (token {row['token']}); "
                f"this caller presents owner {owner!r} token {token}. The token "
                f"moved when this caller was superseded, and a process that is "
                f"still running does not still hold the write scope")

    @staticmethod
    def _next_lease_token(conn, resource: str) -> int:
        """A token that only ever increases, per resource, across restarts."""
        row = conn.execute(
            "SELECT next_value FROM id_sequences WHERE scope=? AND kind='lease'",
            (resource,),
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO id_sequences(scope,kind,next_value) VALUES (?, 'lease', 2)",
                (resource,))
            return 1
        token = int(row["next_value"])
        conn.execute(
            "UPDATE id_sequences SET next_value=? WHERE scope=? AND kind='lease'",
            (token + 1, resource))
        return token

    def create_item(self, item: WorkItem) -> WorkItem:
        prefix = item.id.split("-", 1)[0]
        with self.transaction(immediate=True) as conn:
            project = conn.execute("SELECT prefix FROM projects WHERE project=?", (item.project,)).fetchone()
            if project is None or project["prefix"] != prefix:
                raise ValueError("Item id does not match registered project prefix")
            conn.execute(
                """INSERT INTO items_index
                   (item_id,project,title,kind,card_locator,state,outcome,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    item.id,
                    item.project,
                    item.title,
                    item.kind,
                    item.card_locator,
                    item.state.value,
                    item.outcome.value if item.outcome else None,
                    item.created_at,
                    item.updated_at,
                ),
            )
            self._event(conn, item.id, "created", {"state": item.state.value})
        return item

    def _event(self, conn, item_id: str, event_type: str, payload: dict, at: str | None = None) -> None:
        conn.execute(
            "INSERT INTO item_events(item_id,event_type,at,payload_json) VALUES (?,?,?,?)",
            (item_id, event_type, at or now_iso(), json.dumps(payload, sort_keys=True)),
        )

    def get_item(self, item_id: str) -> WorkItem | None:
        with closing(self.connection()) as conn:
            row = conn.execute("SELECT * FROM items_index WHERE item_id=?", (item_id,)).fetchone()
        return self._item(row)

    @staticmethod
    def _item(row):
        if row is None:
            return None
        return WorkItem(
            id=row["item_id"],
            project=row["project"],
            title=row["title"],
            kind=row["kind"],
            card_locator=row["card_locator"],
            state=ItemState(row["state"]),
            outcome=Outcome(row["outcome"]) if row["outcome"] else None,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def list_items(self, project: str | None = None) -> list[WorkItem]:
        sql = "SELECT item_id FROM items_index"
        params: tuple = ()
        if project:
            sql += " WHERE project=?"
            params = (project,)
        sql += " ORDER BY item_id"
        with closing(self.connection()) as conn:
            ids = [row["item_id"] for row in conn.execute(sql, params)]
        return [item for item_id in ids if (item := self.get_item(item_id)) is not None]

    def transition_item(self, item_id: str, target: ItemState | str, note: str = "", **side) -> WorkItem:
        with self.transaction(immediate=True) as conn:
            return self._transition_item_tx(conn, item_id, target, note=note, **side)

    def _transition_item_tx(self, conn, item_id, target, note="", **side):
        row = conn.execute("SELECT state FROM items_index WHERE item_id=?", (item_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown Item: {item_id}")
        current = ItemState(row["state"])
        target = transition(current, target, **side)
        at = now_iso()
        conn.execute("UPDATE items_index SET state=?,updated_at=? WHERE item_id=?", (target.value, at, item_id))
        self._event(
            conn,
            item_id,
            "transitioned",
            {"from": current.value, "to": target.value, "note": note, **{k: str(v) for k, v in side.items()}},
        )
        return self._item(conn.execute("SELECT * FROM items_index WHERE item_id=?", (item_id,)).fetchone())

    def reopen_item(self, item_id: str, reason: str, source: str) -> WorkItem:
        with self.transaction(immediate=True) as conn:
            return self._reopen_item_tx(conn, item_id, reason, source)

    def _reopen_item_tx(self, conn, item_id, reason, source):
        if not reason.strip() or not source.strip():
            raise ValueError("reopen requires reason and source")
        row = conn.execute("SELECT state FROM items_index WHERE item_id=?", (item_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown Item: {item_id}")
        target = reopen(ItemState(row["state"]))
        at = now_iso()
        conn.execute("UPDATE items_index SET state=?,updated_at=? WHERE item_id=?", (target.value, at, item_id))
        self._event(conn, item_id, "reopened", {"reason": reason, "source": source, "to": target.value})
        return self._item(conn.execute("SELECT * FROM items_index WHERE item_id=?", (item_id,)).fetchone())

    def item_events(self, item_id: str) -> list[dict]:
        with closing(self.connection()) as conn:
            rows = conn.execute(
                "SELECT event_type,at,payload_json FROM item_events WHERE item_id=? ORDER BY event_id", (item_id,)
            ).fetchall()
        return [
            {"event_type": row["event_type"], "at": row["at"], "payload": json.loads(row["payload_json"])}
            for row in rows
        ]

    def set_outcome(self, item_id: str, outcome: Outcome | str) -> WorkItem:
        with self.transaction(immediate=True) as conn:
            return self._set_outcome_tx(conn, item_id, outcome)

    def _set_outcome_tx(self, conn, item_id, outcome):
        outcome = Outcome(outcome)
        if conn.execute("SELECT 1 FROM items_index WHERE item_id=?", (item_id,)).fetchone() is None:
            raise KeyError(f"unknown Item: {item_id}")
        at = now_iso()
        conn.execute("UPDATE items_index SET outcome=?,updated_at=? WHERE item_id=?", (outcome.value, at, item_id))
        self._event(conn, item_id, "outcome_recorded", {"outcome": outcome.value})
        return self._item(conn.execute("SELECT * FROM items_index WHERE item_id=?", (item_id,)).fetchone())

    def add_deliverable(self, item_id: str, kind: str, locator: str) -> dict:
        if not kind.strip() or not locator.strip():
            raise ValueError("deliverable requires kind and locator")
        with self.transaction(immediate=True) as conn:
            if conn.execute("SELECT 1 FROM items_index WHERE item_id=?", (item_id,)).fetchone() is None:
                raise KeyError(f"unknown Item: {item_id}")
            at = now_iso()
            cursor = conn.execute(
                "INSERT INTO deliverables(item_id,kind,locator,created_at) VALUES (?,?,?,?)",
                (item_id, kind, locator, at),
            )
            self._event(conn, item_id, "deliverable_added", {"kind": kind, "locator": locator})
            deliverable_id = cursor.lastrowid
        return {"id": deliverable_id, "item": item_id, "kind": kind, "locator": locator, "created_at": at}

    def register_source(self, domain, stable_id, *, revision, locator):
        """One row per (domain, stable_id, revision), and a second address says so.

        The identity is the provenance triple; `locator` is where that revision is
        read from, and it is single-valued — one source cannot be in two places,
        so a second differing value is not a correction the store may make
        silently. The row is looked up FIRST and the two cases are answered
        separately:

          * the same triple and the same locator — a replay. The stored row is
            returned and the call succeeds, which is what makes re-running an
            intake idempotent.
          * the same triple and a different locator — refused by name, with both
            addresses in the sentence. Keeping the old one and reporting it as
            this call's is the one answer that is not available.

        The second case is where a question is left open on purpose: «更正一个
        已登记来源的地址» is not answered here, because this entry point has no
        verb that means "the same source, moved". A caller who needs one needs
        that verb rather than a call that reads as a replay.
        """
        if not all(str(x).strip() for x in (domain, stable_id, revision, locator)): raise ValueError("source requires provenance")
        with self.transaction(immediate=True) as c:
            stored = c.execute("SELECT * FROM source_objects WHERE domain=? AND stable_id=? AND revision=?",(domain,stable_id,revision)).fetchone()
            if stored is None:
                c.execute("INSERT INTO source_objects VALUES (?,?,?,?,?,?)", (f"{domain}:{stable_id}:{revision}",domain,stable_id,revision,locator,now_iso()))
                stored = c.execute("SELECT * FROM source_objects WHERE domain=? AND stable_id=? AND revision=?",(domain,stable_id,revision)).fetchone()
            else:
                diff = _changed_fields(stored, {"locator": locator})
                if diff:
                    raise ValueError(_conflict_sentence("source", stored["source_id"], diff) +
                        " A source keeps the address it was registered at: this entry point does not "
                        "move one, so reuse the stored locator or register the other address as a "
                        "source of its own.")
            return dict(stored)

    def record_observation(self, source_id, observation_id, *, semantic_type, content_hash, source_origin="online", carrier="other", information_form="curated"):
        """One row per observation id, and a second reading says so.

        An observation is one reading of one source at one hash, so every field
        this call supplies is part of what it asserts — the hash of what was
        read, what was made of it, and which source it came from. All six are
        therefore compared, and the row is looked up FIRST: the same six are a
        replay and return the stored row; any one of them differing is refused by
        name, with the stored value beside the caller's.

        Comparing `source_id` as well is not decoration. The primary key is
        `observation_id` while the UNIQUE constraint is `(source_id,
        observation_id)`, so the same id under a different source conflicts with
        the PRIMARY KEY rather than with that constraint — and `INSERT OR IGNORE`
        swallows it exactly as silently as it swallows everything else. This was
        written down as "it comes back as `IntegrityError`" before it was
        measured, and the measurement says otherwise: through the installed CLI
        the call returned 0 and answered with the OLD `source_id`. Which
        constraint decided an answer has to be visible to the caller, so both keys
        are compared here.

        The returned row is the stored one, unchanged, with no field added: a
        caller who replays gets exactly what a caller who wrote it got, and
        `test_v3_information.py` compares that dict whole.
        """
        if not source_id or not observation_id or not semantic_type or not re.fullmatch(r"[0-9a-fA-F]{64}", content_hash): raise ValueError("observation requires provenance and SHA-256")
        with self.transaction(immediate=True) as c:
            if not c.execute("SELECT 1 FROM source_objects WHERE source_id=?",(source_id,)).fetchone(): raise KeyError(f"unknown source: {source_id}")
            stored = c.execute("SELECT * FROM observations WHERE observation_id=?",(observation_id,)).fetchone()
            if stored is None:
                c.execute("INSERT INTO observations VALUES (?,?,?,?,?,?,?,?)",(observation_id,source_id,semantic_type,source_origin,carrier,information_form,content_hash,now_iso()))
                stored = c.execute("SELECT * FROM observations WHERE observation_id=?",(observation_id,)).fetchone()
            else:
                diff = _changed_fields(stored, {
                    "content_hash": content_hash, "semantic_type": semantic_type,
                    "source_id": source_id, "source_origin": source_origin,
                    "carrier": carrier, "information_form": information_form})
                if diff:
                    raise ValueError(_conflict_sentence("observation", observation_id, diff) +
                        " An observation records one reading of one source at one hash, so a "
                        "different reading belongs under a different id; the id that already "
                        "answers keeps answering the same thing.")
            return dict(stored)

    def record_claim(self, claim_id, locator, observation_ids, *, status="ACTIVE"):
        """A claim's single-valued fields do not change; its observations accumulate.

        The two kinds of field are treated differently because they behave
        differently, and the difference is what decides this entry point's two
        answers. `locator` and `status` are single-valued — where a claim is
        written, and whether it stands — so one of two differing values would have
        to lose, and a silent loss is the defect this fix is about. They are
        therefore refused by name, as `register_source` refuses a second locator.
        The observations a claim rests on are a SET: it can grow, and growing it
        loses nothing, so a replay carrying observations the claim does not yet
        rest on EXTENDS the claim. There is no other verb that adds an
        observation to a claim, and refusing here would leave that statement
        unsayable.

        What the extension may not do is go unmentioned. The answer used to be the
        bare `claims` row, which names no observations at all, so a caller passing
        `--observation obs-2` on a claim that already rested on `obs-1` was told
        nothing about either. The stored set is read back and returned as
        `observation_ids`, which is the one field added to this answer: a key that
        was not there before cannot break a caller who did not ask for it.
        """
        if not observation_ids: raise ValueError("claim requires observations")
        with self.transaction(immediate=True) as c:
            missing = [x for x in observation_ids if not c.execute("SELECT 1 FROM observations WHERE observation_id=?",(x,)).fetchone()]
            if missing: raise KeyError(f"unknown observation: {', '.join(missing)}")
            stored = c.execute("SELECT * FROM claims WHERE claim_id=?",(claim_id,)).fetchone()
            if stored is None:
                c.execute("INSERT INTO claims VALUES (?,?,?,?)",(claim_id,locator,status,now_iso()))
            else:
                diff = _changed_fields(stored, {"locator": locator, "status": status})
                if diff:
                    raise ValueError(_conflict_sentence("claim", claim_id, diff) +
                        " A claim's locator and state are single-valued, so a second value is a "
                        "conflict rather than a correction; the observations it rests on are the "
                        "field that grows, and those were not touched.")
            for x in observation_ids: c.execute("INSERT OR IGNORE INTO claim_observations VALUES (?,?)",(claim_id,x))
            answer = dict(c.execute("SELECT * FROM claims WHERE claim_id=?",(claim_id,)).fetchone())
            answer["observation_ids"] = [r[0] for r in c.execute(
                "SELECT observation_id FROM claim_observations WHERE claim_id=? ORDER BY observation_id",(claim_id,))]
        return answer

    def create_candidate(self, candidate_id, project, title, kind, *, dedupe_key, claim_ids=(), domain="", external_id=None, external_revision=None):
        """One candidate per requirement identity, and a collision says so.

        The two UNIQUE constraints on `item_candidates` ARE the idempotence of
        this entry point, and `INSERT OR IGNORE` is how the insert respects
        them. What `OR IGNORE` cannot do is tell apart the two reasons a row can
        be absent afterwards, and until 2026-09-25 it did not have to: the
        `candidate_claims` insert that follows ran anyway, referenced a
        candidate that had just been ignored, and the caller of a duplicate
        requirement was told `sqlite3.IntegrityError: FOREIGN KEY constraint
        failed`. Measured, on a fresh store, both for a repeated identity and
        for a repeated dedupe key.

        That is a wrong answer rather than a missing one: it names a foreign key
        as the problem when the problem is that this requirement already has a
        candidate, and a caller reading it looks at the wrong table. The row is
        therefore looked up FIRST and the two cases are answered separately:

          * the same `candidate_id`, and neither key half held by another row —
            a replay. The stored row is returned, which is what makes a retried
            `candidate create` idempotent. Note what a replay does NOT do: a
            different `title`, `kind` or `dedupe_key` in the second call is not
            written. New evidence does not revise the objective, and this is
            where that is enforced.
          * a different `candidate_id` for one requirement — a collision, and a
            refusal. Returning the other candidate's row would answer a question
            nobody asked (it is a different identity than the caller named), and
            inserting a second row is what the constraint exists to prevent.
            This is `invoke_once`'s rule one file over: same key with different
            input is refused, not reconciled.

        Either refusal names the half of the key that actually matched, read off
        the query that matched it, because a caller who is told the wrong half
        goes and looks at the wrong row.
        """
        if not claim_ids: raise ValueError("candidate requires claim provenance")
        with self.transaction(immediate=True) as c:
            missing = [x for x in claim_ids if not c.execute("SELECT 1 FROM claims WHERE claim_id=?",(x,)).fetchone()]
            if missing: raise KeyError(f"unknown claim: {', '.join(missing)}")
            # The identity half mirrors the INDEX it reports on, and `=` is what
            # mirrors it: SQLite treats every NULL as distinct in a UNIQUE
            # index, and `col = NULL` is false for every row, so a triple with a
            # NULL in it matches nothing here for the same reason it collides
            # with nothing there. Two candidates that both lack an external
            # identity are therefore two requirements, in the store and in this
            # lookup alike.
            #
            # A guard — `if external_id is not None and external_revision is not
            # None:` before adding the clause — was written here first, as the
            # more obvious way to say that. It was removed because mutating it
            # away changed no result: `=` already cannot match a NULL, so the
            # guard was decoration, and decoration is what a later reader
            # mistakes for the rule. `IS ?` is the form that WOULD be wrong —
            # NULL-safe, and therefore stricter than the index — and
            # `test_two_requirements_without_an_identity_are_two_candidates`
            # fails if anyone writes it.
            by_id = c.execute(
                "SELECT * FROM item_candidates WHERE candidate_id = ?", (candidate_id,)
            ).fetchone()
            # One query per UNIQUE constraint, and each a transcription of that
            # constraint. Which of them returns a row IS the reason the caller is
            # about to be refused, so the reason is read off the query that found
            # it rather than reconstructed by comparing the stored row back to
            # this call's arguments. That reconstruction was written here first,
            # as a single `OR`ed query followed by a message that interpolated
            # the CALLER's key: a collision on the identity half was then
            # reported as "already carries dedupe key 'd2'" when the stored row
            # held 'd1', measured on a real store. A refusal is read as a
            # statement of fact, and that one stated a fact that was not so.
            by_dedupe = c.execute(
                "SELECT * FROM item_candidates WHERE dedupe_key = ?", (dedupe_key,)
            ).fetchone()
            by_identity = c.execute(
                "SELECT * FROM item_candidates"
                " WHERE domain = ? AND external_id = ? AND external_revision = ?",
                (domain, external_id, external_revision),
            ).fetchone()
            holders = {}
            if by_dedupe is not None:
                holders.setdefault(by_dedupe["candidate_id"], []).append(
                    f"dedupe key {dedupe_key!r}")
            if by_identity is not None:
                holders.setdefault(by_identity["candidate_id"], []).append(
                    f"requirement identity ({domain!r}, {external_id!r}, "
                    f"{external_revision!r})")
            # This call's own row is not a conflict with itself; what is left in
            # `holders` is by construction carried by someone else.
            holders.pop(candidate_id, None)
            if holders:
                naming = "; ".join(
                    f"candidate {cid} already carries {' and '.join(facts)}"
                    for cid, facts in holders.items())
                if by_id is None:
                    raise ValueError(
                        f"{naming}; this call named {candidate_id}. One "
                        f"requirement has one candidate, so reuse that id, or the "
                        f"requirement is not the one this call claims it is.")
                # Both halves are taken, and not by this call's row. Naming only
                # one of them would send the caller to fix half of a conflict.
                raise ValueError(
                    f"candidate {candidate_id} already exists, and {naming}. Two "
                    f"rows are being named for one requirement, and this call is "
                    f"not a replay of either.")
            if by_id is not None:
                for x in claim_ids: c.execute("INSERT OR IGNORE INTO candidate_claims VALUES (?,?)",(candidate_id,x))
                return dict(c.execute("SELECT * FROM item_candidates WHERE candidate_id=?",(candidate_id,)).fetchone())
            c.execute("INSERT INTO item_candidates(candidate_id,project,title,kind,state,dedupe_key,domain,external_id,external_revision,approved_item_id,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",(candidate_id,project,title,kind,"NEW",dedupe_key,domain,external_id,external_revision,None,now_iso()))
            for x in claim_ids: c.execute("INSERT OR IGNORE INTO candidate_claims VALUES (?,?)",(candidate_id,x))
            return dict(c.execute("SELECT * FROM item_candidates WHERE candidate_id=?",(candidate_id,)).fetchone())

    def decide_candidate(self, candidate_id, decision):
        """Accept, reject or park one requirement — once, and out loud.

        `decision` is the verb the human entrance uses (`merge`/`reject`/`defer`),
        not the state it lands in, so the two spellings of one act cannot drift.

        The candidate is looked up FIRST. Before this method existed the CLI ran
        `UPDATE … WHERE candidate_id=?` and then read the row back to return it;
        for a candidate nobody had, the update matched nothing, the read returned
        nothing, and `dict(None)` raised a `TypeError` that `main`'s
        `(ValueError, KeyError, OSError)` does not catch. Measured: `candidate
        merge cnd-NOPE` exited 1 with a Python traceback, while every other verb
        in this CLI answers with a JSON error and exits 2.
        """
        state = {"merge": "MERGED", "reject": "REJECTED", "defer": "DEFERRED"}[decision]
        with self.transaction(immediate=True) as c:
            row = c.execute(
                "SELECT * FROM item_candidates WHERE candidate_id=?", (candidate_id,)
            ).fetchone()
            if row is None:
                raise KeyError(candidate_id)
            if row["state"] == state:
                # The same decision again is the same decision: a retried call
                # must not be refused for having been tried twice.
                return dict(row)
            _refuse_if_concluded(row, decision, candidate_id=candidate_id)
            c.execute(
                "UPDATE item_candidates SET state=? WHERE candidate_id=?",
                (state, candidate_id),
            )
            return dict(c.execute(
                "SELECT * FROM item_candidates WHERE candidate_id=?", (candidate_id,)
            ).fetchone())

    def match_candidate(self, candidate_id, item_id):
        with self.transaction(immediate=True) as c:
            candidate = c.execute(
                "SELECT * FROM item_candidates WHERE candidate_id=?", (candidate_id,)
            ).fetchone()
            if candidate is None:
                raise KeyError(candidate_id)
            # A concluded candidate is not re-matched: `PROMOTED` read back as
            # `MATCHED` would take an Item off the requirement it already names,
            # and a rejected or merged requirement is not one being matched again.
            _refuse_if_concluded(candidate, "match", candidate_id=candidate_id)
            self._require_item(c, item_id)
            c.execute("UPDATE item_candidates SET state='MATCHED',approved_item_id=? WHERE candidate_id=?",(item_id,candidate_id))
            return dict(c.execute("SELECT * FROM item_candidates WHERE candidate_id=?",(candidate_id,)).fetchone())

    def promote_candidate(self, candidate_id, item_id=None):
        with self.transaction(immediate=True) as c:
            r=c.execute("SELECT * FROM item_candidates WHERE candidate_id=?",(candidate_id,)).fetchone()
            if not r: raise KeyError(candidate_id)
            # An Item that is already named is not silently exchanged for another
            # one. Measured before this guard: `promote --item DEM-0002` on a
            # candidate matched to DEM-0001 returned 0, answered with DEM-0002,
            # and left the row pointing there — the caller's explicit Item
            # replaced a mapping a human had already recorded, and nothing said
            # so. The same call naming the item already recorded is a replay.
            if r['state']=='PROMOTED':
                if item_id is not None and item_id != r['approved_item_id']:
                    raise ValueError(
                        f"candidate {candidate_id} is already PROMOTED to "
                        f"{r['approved_item_id']}, and this call names {item_id}. "
                        f"One requirement has one Item; re-pointing it would move "
                        f"an Item off the requirement it was approved for.")
                return self.get_item(r['approved_item_id'])
            _refuse_if_concluded(r, 'promote', candidate_id=candidate_id)
            if not item_id: raise ValueError("promotion requires approved Item mapping")
            if r['approved_item_id'] is not None and r['approved_item_id'] != item_id:
                raise ValueError(
                    f"candidate {candidate_id} is matched to "
                    f"{r['approved_item_id']}, and this call names {item_id}. "
                    f"Promoting it to another Item would discard that match "
                    f"without saying so.")
            target=item_id
            # The caller supplied this Item; the store's own words for "there is
            # no such Item" are the only ones that fit. Measured before this:
            # `candidate promote cnd-1 --item DEM-9999` answered "approved Item
            # mapping required", which tells a caller who named an Item that
            # they named none.
            self._require_item(c, target)
            c.execute("UPDATE item_candidates SET state='PROMOTED',approved_item_id=? WHERE candidate_id=?",(target,candidate_id))
            if r['domain'] and r['external_id'] and r['external_revision']:
                c.execute("INSERT OR IGNORE INTO external_item_mappings(domain,external_id,revision,item_id) VALUES(?,?,?,?)",(r['domain'],r['external_id'],r['external_revision'],target))
        return self.get_item(target)

    def promote_candidate_to_new_item(self, candidate_id, *, project, prefix, title, kind):
        with self.transaction(immediate=True) as c:
            candidate = c.execute(
                "SELECT * FROM item_candidates WHERE candidate_id=?", (candidate_id,)
            ).fetchone()
            if candidate is None:
                raise KeyError(candidate_id)
            if candidate["state"] == "PROMOTED":
                target = candidate["approved_item_id"]
            else:
                # MERGED and REJECTED stop here too. Measured before this guard:
                # a rejected requirement reached this branch and produced a real
                # Item, because only PROMOTED was special-cased and everything
                # else fell through to creation.
                _refuse_if_concluded(candidate, "promote", candidate_id=candidate_id)
                if candidate["project"] != project:
                    raise ValueError("candidate project does not match new Item project")
                if not project or not _PREFIX.fullmatch(prefix):
                    raise ValueError("invalid project or Item prefix")
                registered = c.execute(
                    "SELECT prefix FROM projects WHERE project=?", (project,)
                ).fetchone()
                if registered and registered["prefix"] != prefix:
                    raise ValueError(f"project {project} already uses prefix {registered['prefix']}")
                owner = c.execute("SELECT project FROM projects WHERE prefix=?", (prefix,)).fetchone()
                if owner and owner["project"] != project:
                    raise ValueError(f"Item prefix already registered: {prefix}")
                c.execute("INSERT OR IGNORE INTO projects(project,prefix) VALUES (?,?)", (project, prefix))
                c.execute(
                    "INSERT OR IGNORE INTO id_sequences(scope,kind,next_value) VALUES (?,?,1)",
                    (project, "item"),
                )
                sequence = c.execute(
                    "SELECT next_value FROM id_sequences WHERE scope=? AND kind='item'", (project,)
                ).fetchone()["next_value"]
                c.execute(
                    "UPDATE id_sequences SET next_value=? WHERE scope=? AND kind='item'",
                    (sequence + 1, project),
                )
                target = f"{prefix}-{sequence:04d}"
                item = WorkItem(
                    target,
                    project,
                    title,
                    kind,
                    f"records/projects/{project}/items/{target}/card.yaml",
                )
                c.execute(
                    """INSERT INTO items_index
                       (item_id,project,title,kind,card_locator,state,outcome,created_at,updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (
                        item.id,
                        item.project,
                        item.title,
                        item.kind,
                        item.card_locator,
                        item.state.value,
                        None,
                        item.created_at,
                        item.updated_at,
                    ),
                )
                self._event(c, target, "created", {"state": item.state.value})
                c.execute(
                    "UPDATE item_candidates SET state='PROMOTED',approved_item_id=? WHERE candidate_id=?",
                    (target, candidate_id),
                )
                if candidate["domain"] and candidate["external_id"] and candidate["external_revision"]:
                    c.execute(
                        "INSERT OR IGNORE INTO external_item_mappings(domain,external_id,revision,item_id) VALUES(?,?,?,?)",
                        (
                            candidate["domain"],
                            candidate["external_id"],
                            candidate["external_revision"],
                            target,
                        ),
                    )
        return self.get_item(target)

    def transition_activity(self, item_id, activity_id, target):
        with self.transaction(immediate=True) as c:
            return self._transition_activity_tx(c, item_id, activity_id, target)

    def _transition_activity_tx(self, c, item_id, activity_id, target):
        allowed={ActivityState.PENDING:{ActivityState.READY,ActivityState.CANCELLED},ActivityState.READY:{ActivityState.RUNNING,ActivityState.CANCELLED},ActivityState.RUNNING:{ActivityState.SUCCEEDED,ActivityState.FAILED,ActivityState.BLOCKED,ActivityState.CANCELLED},ActivityState.BLOCKED:{ActivityState.READY,ActivityState.CANCELLED}}
        # The lookup here raised `KeyError(activity_id)` — the bare id, and
        # never the Item: `activity transition DEM-9999 act-1` answered
        # `'act-1'`, naming the half that exists. `_require_activity` names
        # the half that matched.
        r = self._require_activity(c, item_id, activity_id)
        target=ActivityState(target); current=ActivityState(r['state'])
        if target not in allowed.get(current,set()): raise ValueError(f"illegal activity transition {current} -> {target}")
        c.execute("UPDATE activities SET state=? WHERE item_id=? AND activity_id=?",(target.value,item_id,activity_id))
        return Activity(item_id,activity_id,r['kind'],target,r['graph_revision'])

    def upsert_context(self, context):
        with self.transaction(immediate=True) as c:
            return self._upsert_context_tx(c, context)

    def _upsert_context_tx(self, c, context):
        if not isinstance(context.revision, str) or not context.revision.strip():
            raise ValueError("context requires a non-empty string revision")
        # `execution_contexts` carries a composite foreign key onto
        # `activities(item_id, activity_id)`, so attaching a context to an
        # activity that does not exist used to answer with the constraint's
        # sentence rather than with the activity's name. Attaching to an
        # activity that *does* exist is the upsert this method is named for
        # and is unchanged.
        self._require_activity(c, context.item_id, context.activity_id)
        c.execute("INSERT INTO execution_contexts(item_id,activity_id,repo,mode,revision,worktree,dirty) VALUES(?,?,?,?,?,?,?) ON CONFLICT(item_id,activity_id,repo) DO UPDATE SET mode=excluded.mode,revision=excluded.revision,worktree=excluded.worktree,dirty=excluded.dirty",(context.item_id,context.activity_id,context.repo,context.mode.value,context.revision,context.worktree,int(context.dirty)))
        return context

    def _require_item(self, connection, item_id):
        """The `items_index` row for `item_id`, or a refusal that names it.

        Four CLI paths used to reach the database without this check and let the
        constraint answer for them. `activity add`, `context attach` and
        `run begin` named an Item that does not exist and got back
        `sqlite3.IntegrityError: FOREIGN KEY constraint failed` — a sentence
        about a constraint, in a module whose whole vocabulary is Items and
        Runs. `run begin` was worse: it dereferenced `get_item(item_id).project`
        and answered `AttributeError: 'NoneType' object has no attribute
        'project'`. None of the three shapes is a caller's fact; the caller's
        fact is "there is no Item DEM-9999", and that is what this raises.

        Returning the row rather than `None`/`True` keeps the two callers that
        need the Item's own fields — `begin_run` needs `project` to place the
        artifact directory — from looking it up a second time, which is where
        the `AttributeError` came from.
        """
        row = connection.execute(
            "SELECT * FROM items_index WHERE item_id=?", (item_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown Item: {item_id}")
        return row

    def _require_activity(self, connection, item_id, activity_id):
        """The `activities` row for (item_id, activity_id), or a refusal naming which half is wrong.

        The same three answers as `_require_run`, one plane up: an Item that
        does not exist and an activity that does not exist are different
        repairs, and an activity that exists under some *other* Item is a third
        — the row is real, it is just not this Item's. The database has an
        opinion about all three (`activities.item_id REFERENCES items_index`,
        and `runs`/`execution_contexts`/`activity_dependencies` each hold a
        composite foreign key onto `(item_id, activity_id)`) and its opinion is
        "FOREIGN KEY constraint failed", which does not say which of the three
        it means.
        """
        self._require_item(connection, item_id)
        row = connection.execute(
            "SELECT * FROM activities WHERE item_id=? AND activity_id=?",
            (item_id, activity_id),
        ).fetchone()
        if row is not None:
            return row
        owners = [r["item_id"] for r in connection.execute(
            "SELECT item_id FROM activities WHERE activity_id=? ORDER BY item_id",
            (activity_id,))]
        if owners:
            raise KeyError(
                f"activity {activity_id} belongs to Item"
                f"{'s' if len(owners) > 1 else ''} {', '.join(owners)}, not {item_id}")
        raise KeyError(f"unknown activity: {item_id}/{activity_id}")

    def _require_run(self, connection, item_id, run_id):
        """The `runs` row for (item_id, run_id), or a refusal that names which half is wrong.

        Until 2026-10-08 `run evidence` took an Item and a Run as two bare
        positionals and checked neither. `record_evidence` wrote the pair
        through, so `workengine run evidence SRV-0011 19 --locator …` returned
        `evidence_id 224` for a Run that does not exist — measured on this
        node's production store, exit code 0 — and the ledger held a row
        attributed to a Run nobody can look up. The `evidence` table declares
        no foreign key (v4 SQL, `migrations.py`) while `publication_receipts`
        does, so the database never had an opinion either.

        An unknown Item and an unknown Run are two different answers and are
        reported as two different answers, because a caller who mistyped the
        Item and a caller who mistyped the Run have different repairs to make.
        A Run id that exists under some *other* Item is a third: the row the
        caller named is real, it is just not theirs, and saying "unknown Run"
        would send them looking for a typo that is not there.

        This is the lookup SQLite's own foreign key would have done, said in
        words — the same choice `create_candidate` makes one method above,
        where an `IntegrityError` naming a constraint is rejected in favour of
        naming the table the caller actually has to look at.
        """
        self._require_item(connection, item_id)
        row = connection.execute("SELECT * FROM runs WHERE item_id=? AND run_id=?", (item_id, run_id)).fetchone()
        if row is not None:
            return row
        owners = [r["item_id"] for r in connection.execute(
            "SELECT item_id FROM runs WHERE run_id=? ORDER BY item_id", (run_id,))]
        if owners:
            raise KeyError(
                f"Run {run_id} belongs to Item{'s' if len(owners) > 1 else ''} "
                f"{', '.join(owners)}, not {item_id}")
        raise KeyError(f"unknown Run: {item_id}/{run_id}")

    def record_evidence(self, item_id, run_id, *, kind, locator, command, cases, exit_code, revision, dirty, verdict):
        verdict = _verdict_word(verdict)
        if not Path(locator).is_absolute() or not Path(locator).is_file(): raise ValueError("evidence must be an existing absolute file")
        digest=hashlib.sha256(Path(locator).read_bytes()).hexdigest()
        if not digest: raise ValueError("evidence requires SHA-256")
        _reject_secrets({'kind':kind,'locator':locator,'command':command,'cases':cases,'revision':revision,'verdict':verdict,'exit_code':exit_code,'dirty':dirty})
        with self.transaction(immediate=True) as c:
            self._require_run(c, item_id, run_id)
            previous = c.execute("SELECT * FROM evidence WHERE item_id=? AND run_id=? AND locator=?",
                                 (item_id, run_id, locator)).fetchone()
            requested = dict(kind=kind, sha256=digest, command=command, cases_json=json.dumps(cases),
                             exit_code=exit_code, revision=revision, dirty=int(dirty), verdict=verdict)
            if previous is not None:
                # The stored verdict is folded before comparison for the same
                # reason the arriving one is: a row written as `pass` and a
                # replay written as `PASS` are one judgement, and reporting them
                # as a conflict would refuse a caller who corrected their own
                # spelling. A genuinely different word (`FAIL` for `PASS`) still
                # differs after folding, so the conflict that test pins remains.
                changed = [field for field, value in requested.items()
                           if (_verdict_word(previous[field]) if field == "verdict" else previous[field]) != value]
                if changed:
                    raise EvidenceConflict(
                        f"evidence_id {previous['evidence_id']} already records this locator; "
                        f"changed fields: {', '.join(changed)}. Preserve the existing record and "
                        "register the corrected artifact at a new immutable versioned locator; "
                        "changing --kind does not create a new evidence identity.")
                return dict(previous)
            c.execute("INSERT INTO evidence(item_id,run_id,kind,locator,sha256,command,cases_json,exit_code,revision,dirty,verdict,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(item_id,run_id,kind,locator,digest,command,json.dumps(cases),exit_code,revision,int(dirty),verdict,now_iso()))
            return dict(c.execute("SELECT * FROM evidence WHERE item_id=? AND run_id=? AND locator=?",(item_id,run_id,locator)).fetchone())

    def finalize_run(self, item_id, run_id, *, verdict, exit_code, command, cases,
                     revision, dirty, t10_required=False, t10_binding=None,
                     receipt_ref=None, receipt_authority=None, now=None):
        """Conclude a Run; `t10_required=True` makes runner proof mandatory.

        The CLI's `run finalize` and every existing caller reach this method.
        Without `t10_required` the historical record semantics are preserved,
        but the answer is labelled `authorization: "UNSIGNED"` and
        `t10_acceptable: false`: a caller's `verdict`/`exit_code` is a
        control-plane record, not runner proof, and V1/T10 must not accept it.

        `t10_required=True` is the narrow opt-in. It requires a trusted
        `RunnerReceiptAuthority`, an opaque `receipt_ref`, and an exact
        `T10Binding` (Item, Run, commit, actor). Any missing half refuses with
        `BLOCKED_TRUST_SOURCE` or its specific code; it NEVER falls back to the
        unsigned path. The receipt is verified by the parent and re-checked
        here, and `SUCCEEDED` additionally requires a verified zero exit.

        There is no in-process verifier in the CLI. In production this method
        is called by the out-of-process serverosd/runner parent described in
        `docs/t10-finalize-entry.md`; until that parent exists the CLI refuses
        `BLOCKED_TRUST_SOURCE`.
        """
        from .authority import ReceiptError, validate_t10_binding

        verdict = _verdict_word(verdict)
        if not t10_required:
            result, authorization, _stored = self._write_finalize(
                item_id, run_id, verdict=verdict, exit_code=exit_code,
                command=command, cases=cases, revision=revision, dirty=dirty,
                receipt=None)
            return {**result, "authorization": authorization,
                    "t10_acceptable": authorization == "T10_VERIFIED"}

        if receipt_authority is None:
            raise ReceiptError(
                "BLOCKED_TRUST_SOURCE",
                "no trusted runner-receipt authority is bound; a T10-required "
                "Run cannot fall back to a caller's exit_code and verdict")
        if t10_binding is None:
            raise ReceiptError(
                "T10_BINDING_REQUIRED",
                "a T10-required finalize must name the exact Item, Run, "
                "commit and actor the trusted parent derived")
        receipt = self._verify_runner_receipt(
            item_id, run_id, receipt_ref=receipt_ref,
            receipt_authority=receipt_authority, verdict=verdict,
            exit_code=exit_code, now=now)
        validate_t10_binding(t10_binding, item_id=item_id, run_id=run_id,
                             receipt=receipt)
        result, authorization, stored = self._write_finalize(
            item_id, run_id, verdict=verdict, exit_code=exit_code,
            command=command, cases=cases, revision=revision, dirty=dirty,
            receipt=receipt)
        if authorization != "T10_VERIFIED":
            raise ReceiptError(
                "T10_UNSIGNED_RUN",
                "this Run was already concluded without a verified receipt; "
                "a T10-required finalize cannot relabel an unsigned success")
        if stored is not None:
            if (stored.get("commit_sha") != t10_binding.commit_sha
                    or stored.get("actor_ref") != t10_binding.actor_ref
                    or stored.get("receipt_sha256") != receipt.receipt_sha256):
                raise ReceiptError(
                    "T10_BINDING_MISMATCH",
                    "the Run's stored finalize receipt is for a different "
                    "commit, actor or receipt anchor than this request")
        return {**result, "authorization": "T10_VERIFIED",
                "t10_acceptable": True}

    def _verify_runner_receipt(self, item_id, run_id, *, receipt_ref,
                               receipt_authority, verdict, exit_code, now=None):
        """The one receipt-verification path shared by both finalize routes."""
        from .authority import (
            ReceiptError,
            VerifiedRunReceipt,
            validate_verified_receipt,
        )
        if receipt_authority is None:
            raise ReceiptError(
                "BLOCKED_TRUST_SOURCE",
                "no trusted runner-receipt authority is bound; a caller's "
                "exit_code and verdict are not proof and cannot conclude "
                "SUCCEEDED")
        if not isinstance(receipt_ref, str) or not receipt_ref.strip():
            raise ReceiptError(
                "RUNNER_RECEIPT_REQUIRED",
                "no runner receipt reference was presented; pass the opaque "
                "ref the trusted receipt authority issued, not a hash or an "
                "exit code")
        now_e = time.time() if now is None else float(now)
        try:
            receipt = receipt_authority.verify(
                receipt_ref, item_ref=item_id, run_ref=run_id, now=now_e)
        except ReceiptError:
            raise
        except Exception as exc:
            raise ReceiptError(
                "UNTRUSTED_RECEIPT",
                f"the trusted receipt authority did not answer: "
                f"{type(exc).__name__}") from exc
        if not isinstance(receipt, VerifiedRunReceipt):
            raise ReceiptError(
                "UNTRUSTED_RECEIPT",
                "the authority returned no VerifiedRunReceipt; a reference "
                "that resolves to nothing is not proof")
        validate_verified_receipt(
            receipt, item_id=item_id, run_id=run_id,
            verdict=verdict, exit_code=exit_code)
        return receipt

    def _write_finalize(self, item_id, run_id, *, verdict, exit_code, command,
                        cases, revision, dirty, receipt=None):
        """Write the one finalize conclusion and say whether it was signed.

        Returns `(result, authorization, stored_receipt)`. `authorization` is
        `T10_VERIFIED` only when this write carries a verified receipt, or when
        a terminal Run's existing evidence file already carries one. A terminal
        Run concluded unsigned stays UNSIGNED; a T10-required caller must not
        be able to launder it by asking again.
        """
        with self.transaction(immediate=True) as c:
            r = self._require_run(c, item_id, run_id)
            if r['state'] in ('SUCCEEDED','FAILED','CANCELLED','INVALID'):
                e = c.execute(
                    "SELECT * FROM evidence WHERE item_id=? AND run_id=? "
                    "AND kind='finalize' ORDER BY evidence_id DESC LIMIT 1",
                    (item_id, run_id)
                ).fetchone()
                if e is None:
                    if r['state'] in ('SUCCEEDED', 'FAILED'):
                        from .authority import ReceiptError
                        raise ReceiptError(
                            "EVIDENCE_MISSING",
                            "the Run is terminal but has no finalize evidence "
                            "row; a conclusion with no durable evidence is not "
                            "a conclusion")
                    return ({"state": r['state'], "verdict": verdict},
                            'UNSIGNED', None)
                stored_receipt = _read_finalize_record(e)
                authorization = ('T10_VERIFIED' if stored_receipt is not None
                                 else 'UNSIGNED')
                return ({"state": r['state'],
                         "verdict": e['verdict'] if e['verdict'] else verdict},
                        authorization, stored_receipt)
            if r['state'] != 'FINALIZING':
                raise ValueError("run must be FINALIZING")
            self._refuse_managed_effect(c, item_ref=item_id)
            locator = str(Path(r['artifact_dir']) / 'finalize.json')
            Path(r['artifact_dir']).mkdir(parents=True, exist_ok=True)
            temporary = Path(locator + '.tmp')
            body = {
                "command": command,
                "cases": cases,
                "exit_code": exit_code,
                "revision": revision,
                "dirty": dirty,
                "verdict": verdict,
                # The durable record says what kind of conclusion this is, not
                # only the return value. A reader of finalize.json must not
                # have to infer "unsigned" from a missing receipt.
                "authorization": ("T10_VERIFIED" if receipt is not None
                                  else "UNSIGNED"),
            }
            if receipt is not None:
                body["runner_receipt"] = {
                    "action_key": receipt.action_key,
                    "input_digest": receipt.input_digest,
                    "receipt_sha256": receipt.receipt_sha256,
                    "process_exit_code": receipt.process_exit_code,
                    "runner_identity": receipt.runner_identity,
                    "commit_sha": receipt.commit_sha,
                    "actor_ref": receipt.actor_ref,
                }
            payload = json.dumps(body) + '\n'
            with temporary.open("w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            _reject_secrets(json.loads(payload))
            digest = hashlib.sha256(payload.encode()).hexdigest()
            c.execute(
                "INSERT INTO evidence(item_id,run_id,kind,locator,sha256,command,"
                "cases_json,exit_code,revision,dirty,verdict,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (item_id, run_id, 'finalize', locator, digest, command,
                 json.dumps(cases), exit_code, revision, int(dirty), verdict,
                 now_iso()))
            if receipt is not None:
                state = ('SUCCEEDED'
                         if verdict == 'PASS' and exit_code == 0
                         and receipt.process_exit_code == 0
                         else 'FAILED')
            else:
                state = ('SUCCEEDED' if verdict == 'PASS' and exit_code == 0
                         else 'FAILED')
            c.execute(
                "UPDATE runs SET state=?,ended_at=? WHERE item_id=? AND run_id=?",
                (state, now_iso(), item_id, run_id))
            # Promote the file BEFORE the database commit. If this fails the
            # exception rolls the transaction back and the Run stays FINALIZING;
            # a recoverable orphan file is preferred to a committed evidence row
            # whose locator does not exist. (The reverse order — commit, then
            # rename — is the torn record `_read_finalize_record` refuses.)
            temporary.replace(locator)
            _fsync_dir(Path(locator).parent)
        stored_receipt = body.get('runner_receipt')
        return ({"state": state, "verdict": verdict},
                'T10_VERIFIED' if receipt is not None else 'UNSIGNED',
                stored_receipt)

    def finalize_run_verified(self, item_id, run_id, *, verdict, exit_code, command,
                              cases, revision, dirty, receipt_ref, receipt_authority,
                              now=None):
        """The #14 lower-level seam: verify a receipt, then write it.

        Kept for existing callers that do not have a `T10Binding` yet. The
        production-facing route is `finalize_run(..., t10_required=True, ...)`,
        which adds the exact Item/Run/commit/actor binding. Both paths share
        `_verify_runner_receipt` and `_write_finalize`; there is no second
        evidence store and no permissive default.
        """
        verdict = _verdict_word(verdict)
        receipt = self._verify_runner_receipt(
            item_id, run_id, receipt_ref=receipt_ref,
            receipt_authority=receipt_authority, verdict=verdict,
            exit_code=exit_code, now=now)
        result, _authorization, _stored = self._write_finalize(
            item_id, run_id, verdict=verdict, exit_code=exit_code,
            command=command, cases=cases, revision=revision, dirty=dirty,
            receipt=receipt)
        return result

    def record_feedback(self,item_id,locator,kind,observation_id):
        if not all(str(value).strip() for value in (item_id, locator, kind, observation_id)):
            raise ValueError("feedback requires Item, Observation, kind, and locator")
        with self.transaction(immediate=True) as c:
            self._require_item(c, item_id)
            if not c.execute("SELECT 1 FROM observations WHERE observation_id=?", (observation_id,)).fetchone():
                raise KeyError(f"unknown observation: {observation_id}")
            c.execute(
                """INSERT OR IGNORE INTO feedback(item_id,observation_id,locator,kind,created_at)
                   VALUES(?,?,?,?,?)""",
                (item_id,observation_id,locator,kind,now_iso()),
            )
            row = c.execute(
                """SELECT * FROM feedback
                   WHERE item_id=? AND observation_id=? AND locator=? AND kind=?""",
                (item_id,observation_id,locator,kind),
            ).fetchone()
            return dict(row)

    def resolve_evidence_run(self, evidence_run):
        with closing(self.connection()) as conn:
            return self._resolve_evidence_run_tx(conn, evidence_run)

    def _resolve_evidence_run_tx(self, conn, evidence_run):
        parts = str(evidence_run).split('/', 1)
        if len(parts) != 2 or not parts[0] or not re.fullmatch(r"RUN-[0-9]{4,}", parts[1]):
            raise ValueError("evidence Run must be ITEM/RUN-0001")
        item_id, run_id = parts
        self._require_run(conn, item_id, run_id)
        return item_id, run_id

    def record_publication_receipt(self, domain, locator, sha256, evidence_run):
        with self.transaction(immediate=True) as c:
            return self._record_publication_receipt_tx(c, domain, locator, sha256, evidence_run)

    def _record_publication_receipt_tx(self, c, domain, locator, sha256, evidence_run):
        target = Path(locator)
        if not domain or not target.is_absolute() or not target.is_file():
            raise ValueError("publication receipt requires domain and existing absolute current locator")
        if not re.fullmatch(r"[0-9a-f]{64}", str(sha256)):
            raise ValueError("publication receipt requires SHA-256")
        if hashlib.sha256(target.read_bytes()).hexdigest() != sha256:
            raise ValueError("publication receipt hash does not match current projection")
        item_id, run_id = self._resolve_evidence_run_tx(c, evidence_run)
        existing = c.execute(
            "SELECT * FROM publication_receipts WHERE domain=? AND locator=? AND evidence_run=?",
            (domain, str(target), evidence_run),
        ).fetchone()
        if existing is not None:
            if existing['sha256'] != sha256 or existing['item_id'] != item_id or existing['run_id'] != run_id:
                raise ValueError("publication identity already has a different projection")
            return dict(existing)
        c.execute(
            """INSERT INTO publication_receipts
               (domain,object_id,revision,locator,sha256,evidence_run,item_id,run_id,created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (domain, 'current', sha256, str(target), sha256, evidence_run, item_id, run_id, now_iso()),
        )
        row = c.execute(
            "SELECT * FROM publication_receipts WHERE domain=? AND locator=? AND evidence_run=?",
            (domain, str(target), evidence_run),
        ).fetchone()
        return dict(row)

    def get_publication_receipt(self, domain, locator, evidence_run):
        with closing(self.connection()) as c:
            row = c.execute(
                "SELECT * FROM publication_receipts WHERE domain=? AND locator=? AND evidence_run=?",
                (domain, locator, evidence_run),
            ).fetchone()
        return dict(row) if row is not None else None

    def list_deliverables(self, item_id: str) -> list[dict]:
        with closing(self.connection()) as conn:
            rows = conn.execute(
                "SELECT deliverable_id,kind,locator,created_at FROM deliverables WHERE item_id=? ORDER BY deliverable_id",
                (item_id,),
            ).fetchall()
        return [
            {"id": row["deliverable_id"], "item": item_id, "kind": row["kind"], "locator": row["locator"], "created_at": row["created_at"]}
            for row in rows
        ]

    def add_activity(self, activity: Activity, needs: list[str] | None = None) -> Activity:
        with self.transaction(immediate=True) as conn:
            return self._add_activity_tx(conn, activity, needs)

    def _add_activity_tx(self, conn, activity, needs=None):
        self._require_item(conn, activity.item_id)
        declared = conn.execute(
            "SELECT * FROM activities WHERE item_id=? AND activity_id=?",
            (activity.item_id, activity.id),
        ).fetchone()
        if declared is not None:
            # Measured before this refusal: `activity add` twice for one id
            # answered with `sqlite3.IntegrityError: UNIQUE constraint
            # failed` — the primary key, in the database's words. Nothing is
            # added by a second `add`: the row it would write is the row
            # that is there. Its `kind` is not rewritten by a second add and
            # its `state` belongs to `transition_activity`, so the only
            # thing a re-add could do is disagree silently. It is refused
            # instead, and the refusal says which activity and which kind
            # the store already has.
            raise ValueError(
                f"activity {activity.item_id}/{activity.id} is already declared as "
                f"{declared['kind']}; adding it again would not declare a second one. "
                "Its state is moved by `activity transition`, not by re-adding it."
            )
        for dependency in needs or ():
            self._require_activity(conn, activity.item_id, dependency)
        conn.execute(
            "INSERT INTO activities(item_id,activity_id,kind,state,graph_revision) VALUES (?,?,?,?,?)",
            (activity.item_id, activity.id, activity.kind, activity.state.value, activity.graph_revision),
        )
        for dependency in needs or ():
            conn.execute(
                "INSERT INTO activity_dependencies(item_id,activity_id,needs_activity_id) VALUES (?,?,?)",
                (activity.item_id, activity.id, dependency),
            )
        return activity

    def attach_context(self, context: ExecutionContext) -> ExecutionContext:
        if context.worktree and not Path(context.worktree).is_absolute():
            raise ValueError("worktree path must be absolute")
        return self.upsert_context(context)

    def execution_contexts(self, item_id: str, activity_id: str) -> list[ExecutionContext]:
        with closing(self.connection()) as conn:
            rows = conn.execute(
                "SELECT * FROM execution_contexts WHERE item_id=? AND activity_id=? ORDER BY repo",
                (item_id, activity_id),
            ).fetchall()
        return [
            ExecutionContext(
                item_id=row["item_id"],
                activity_id=row["activity_id"],
                repo=row["repo"],
                mode=ContextMode(row["mode"]),
                revision=row["revision"],
                worktree=row["worktree"],
                dirty=bool(row["dirty"]),
            )
            for row in rows
        ]

    def begin_run(self, item_id: str, activity_id: str, worker: str, key: str, artifact_root: Path) -> Run:
        with self.transaction(immediate=True) as conn:
            self._refuse_managed_effect(conn, item_ref=item_id)
            result = self._begin_run_tx(conn, item_id, activity_id, worker, key, artifact_root)
            Path(result.artifact_dir).mkdir(parents=True, exist_ok=True)
        return result

    def _begin_run_tx(self, conn, item_id, activity_id, worker, key, artifact_root, *, allocation=None):
        from .artifacts import artifact_dir
        if artifact_root is None:
            raise LeaseError("OWNER_ARTIFACT_ROOT_REQUIRED", "explicit owner artifact root required")
        # Both halves of the key `runs` is foreign-keyed on are named before
        # anything is written. Before this, an unknown Item reached
        # `self.get_item(item_id).project` one line below and answered
        # `AttributeError: 'NoneType' object has no attribute 'project'`,
        # and an unknown activity reached the foreign key and answered
        # `FOREIGN KEY constraint failed`. Validating first also removes a
        # second lookup of the same row: the Item is read once, here, and
        # its `project` is used from that read.
        item = self._require_item(conn, item_id)
        self._require_activity(conn, item_id, activity_id)
        existing = conn.execute(
            "SELECT * FROM runs WHERE item_id=? AND activity_id=? AND idempotency_key=?",
            (item_id, activity_id, key),
        ).fetchone()
        if existing:
            if allocation is not None and existing["worker"] != worker:
                raise LeaseError("IDEMPOTENCY_CONFLICT", "Run key has a different worker")
            if allocation is not None:
                from .owner_artifacts import real_path
                expected = artifact_dir(artifact_root, item["project"], item_id, existing["run_id"])
                if real_path(artifact_root) not in real_path(expected).parents or str(expected) != existing["artifact_dir"]:
                    raise LeaseError("OWNER_LOCATION_MISMATCH", "existing Run has another artifact binding")
                marker = real_path(expected / ".owner-allocation.json")
                if not marker.is_file():
                    raise LeaseError("OWNER_ARTIFACT_ORPHAN", "existing Run has no owner allocation manifest")
                recorded = json.loads(marker.read_text(encoding="utf-8"))
                if any(recorded.get(k) != v for k, v in {"item_ref": item_id, "run_ref": existing["run_id"], "idempotency_key": key, "worker_ref": worker}.items()):
                    raise LeaseError("OWNER_ARTIFACT_ORPHAN", "existing allocation manifest differs")
            return self._run(existing)
        sequence = conn.execute("SELECT COUNT(*) AS n FROM runs WHERE item_id=?", (item_id,)).fetchone()["n"] + 1
        run_id = f"RUN-{sequence:04d}"
        attempt = conn.execute(
            "SELECT COUNT(*) AS n FROM runs WHERE item_id=? AND activity_id=?", (item_id, activity_id)
        ).fetchone()["n"] + 1
        path = artifact_dir(artifact_root, item["project"], item_id, run_id)
        if allocation is not None:
            from .owner_artifacts import allocate_directory
            allocate_directory(artifact_root, path, {**allocation, "item_ref": item_id,
                               "run_ref": run_id, "idempotency_key": key, "worker_ref": worker})
        started = now_iso()
        conn.execute(
            """INSERT INTO runs
               (item_id,run_id,activity_id,worker,idempotency_key,state,artifact_dir,attempt,started_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (item_id, run_id, activity_id, worker, key, RunState.ALLOCATED.value, str(path), attempt, started),
        )
        row = conn.execute("SELECT * FROM runs WHERE item_id=? AND run_id=?", (item_id, run_id)).fetchone()
        return self._run(row)

    def get_run(self, item_id: str, run_id: str) -> Run | None:
        with closing(self.connection()) as conn:
            row = conn.execute("SELECT * FROM runs WHERE item_id=? AND run_id=?", (item_id, run_id)).fetchone()
        return self._run(row) if row else None

    def require_run(self, item_id: str, run_id: str) -> Run:
        """`get_run` for the callers that have nowhere to put "it is not there".

        `get_run` answering `None` is a fact about the store and the caller
        decides what it means. A verb handed an Item and a Run by a human has
        already decided: it is going to report on that Run. Two such callers
        (`run show`, which is `engine.resume_run`, and `run_events_summary`)
        wrote the sentence themselves — `unknown Run: {item}/{run}` — and that
        sentence is wrong twice over: a Run that exists under another Item is
        not unknown, and an Item that does not exist at all is not the Run's
        fault. `_require_run` distinguishes all three; this is that boundary,
        offered without a connection so a read-only verb can reach it.
        """
        with closing(self.connection()) as conn:
            return self._run(self._require_run(conn, item_id, run_id))

    def list_runs(self, item_id: str | None = None, *, state: RunState | str | None = None) -> list[Run]:
        """Runs, newest first, optionally narrowed to one Item or one state.

        No default state filter. A caller that asked for "the runs" and got only
        the live ones would read the list as the whole history, and a finished
        Run is exactly the one somebody is usually asking about.
        """
        query = "SELECT * FROM runs"
        clauses: list[str] = []
        params: list = []
        if item_id is not None:
            clauses.append("item_id=?")
            params.append(item_id)
        if state is not None:
            clauses.append("state=?")
            params.append(RunState(state).value)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY started_at DESC, run_id DESC"
        with closing(self.connection()) as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._run(row) for row in rows]

    def run_events(self, item_id: str, run_id: str) -> list[dict]:
        """The Item's events that are about this Run, oldest first.

        Read from `item_events` rather than from a run-scoped table, because
        there is one event ledger and a second one would be a second history of
        the same work that disagrees with the first.

        `run_terminated` is the only run-scoped event type the ledger records
        today. That is a real limit of the evidence, not of this query: a Run's
        forward transitions live in `runs.state` and were never evented, so an
        empty list here means "nothing was recorded about this Run" and not
        "nothing happened to it". `run_events_summary` states the difference for
        callers that would otherwise read the empty list as the second thing.
        """
        return [
            record
            for record in self.item_events(item_id)
            if str((record.get("payload") or {}).get("run") or "") == str(run_id)
        ]

    def run_events_summary(self, item_id: str, run_id: str) -> dict:
        """`run_events`, wrapped with what the empty case does and does not mean.

        The extra keys exist so that a caller cannot read `events: []` as "this
        Run did nothing". An unknown Run is refused rather than answered with an
        empty history, because a Run that does not exist and a Run that nothing
        was recorded about are different findings.
        """
        run = self.require_run(item_id, run_id)
        events = self.run_events(item_id, run_id)
        return {
            "item": item_id,
            "run": run_id,
            "state": run.state.value,
            "events": events,
            "count": len(events),
            "recorded_event_types": sorted({record["event_type"] for record in events}),
            # Stated in the answer rather than left to the caller's memory of the
            # schema, because the wrong reading — "nothing happened" — is the one
            # an empty list invites.
            "ledger_covers": [
                "operator terminations (event_type run_terminated)",
            ],
            "not_recorded": (
                "Forward Run transitions are not evented. This Run's current "
                "state is authoritative in runs.state; its history through "
                "ALLOCATED/PREPARING/RUNNING/FINALIZING was never written to the "
                "event ledger, so an empty list here does not mean the Run did "
                "nothing."
            ),
        }

    def advance_run(self, item_id: str, run_id: str, target: RunState | str) -> Run:
        with self.transaction(immediate=True) as conn:
            return self._advance_run_tx(conn, item_id, run_id, target)

    def _advance_run_tx(self, conn, item_id, run_id, target):
        target = RunState(target)
        if target in {RunState.CANCELLED, RunState.INVALID}:
            # Those two are operator terminations, not forward steps. They owe a
            # reason, an actor, and an audit record, so they belong to cancel_run.
            raise ValueError(
                f"advance_run cannot end a Run as {target.value}; "
                "use cancel_run (CLI: run cancel / run abandon) so the reason, actor, "
                "and audit event are preserved"
            )
        # This lookup used to be written out here, and it answered
        # `unknown Run: {item}/{run}` for every miss — including a Run that
        # exists under another Item and an Item that does not exist at all.
        # `_require_run` answers those three separately; measured on the
        # installed shim, `run advance DEM-0001 RUN-0001` (a Run of
        # DEM-0002) said "unknown Run" while `run evidence` with the same
        # arguments named the Item that owns it.
        row = self._require_run(conn, item_id, run_id)
        current = RunState(row["state"])
        # SUCCEEDED and FAILED are verdicts, not steps. A Run that reaches
        # either one through here would carry a claim — "this run succeeded"
        # — with no evidence row behind it: no verdict, no exit code, no
        # revision. The ledger would say a thing happened and nothing would
        # say on what basis, which is the failure this whole release is
        # about one plane over.
        #
        # FAILED is only refused from FINALIZING, where finalize_run is the
        # producer and records the verdict. From PREPARING or RUNNING it is
        # an abort rather than a judgement, finalize_run has no route to it
        # (it requires FINALIZING), and refusing it would remove a
        # capability instead of redirecting one.
        if target is RunState.SUCCEEDED or (
            target is RunState.FAILED and current is RunState.FINALIZING
        ):
            raise ValueError(
                f"advance_run cannot conclude a Run as {target.value}; a verdict is "
                "not a transition. Use finalize_run (CLI: run finalize), which "
                "records the verdict, exit code and revision the claim rests on. "
                "If the Run is being abandoned rather than judged, use cancel_run "
                "(CLI: run abandon)."
            )
        target = transition_run(current, target)
        ended = now_iso() if target in RUN_TERMINAL else ""
        conn.execute(
            "UPDATE runs SET state=?,ended_at=? WHERE item_id=? AND run_id=?",
            (target.value, ended, item_id, run_id),
        )
        return self._run(conn.execute("SELECT * FROM runs WHERE item_id=? AND run_id=?", (item_id, run_id)).fetchone())

    def cancel_run(
        self,
        item_id: str,
        run_id: str,
        *,
        reason: str,
        actor: str,
        reason_code: RunCancelReason | str | None = None,
        target: RunState | str = RunState.CANCELLED,
        evidence: tuple[str, ...] = (),
        operation: str = "cancel",
        acknowledge_active_worker: bool = False,
        dry_run: bool = False,
    ) -> dict:
        with self.transaction(immediate=True) as conn:
            return self._cancel_run_tx(conn, item_id, run_id, reason=reason, actor=actor, reason_code=reason_code, target=target, evidence=evidence, operation=operation, acknowledge_active_worker=acknowledge_active_worker, dry_run=dry_run)

    def _cancel_run_tx(self, conn, item_id, run_id, *, reason, actor, reason_code=None, target=RunState.CANCELLED, evidence=(), operation="cancel", acknowledge_active_worker=False, dry_run=False):
        """End a Run by explicit operator action, and record why.

        This is the audited way to reach CANCELLED or INVALID.  It moves the Run
        and appends one item_events record.  It deliberately does not touch the
        Item, artifacts, worktrees, or any runtime resource: terminating a Run is
        a control-plane fact, not a cleanup.
        """
        if operation not in {"cancel", "abandon"}:
            raise ValueError(f"unsupported termination operation: {operation}")
        if not str(reason).strip():
            raise ValueError("run termination requires a non-empty reason")
        if not isinstance(actor, str) or not actor.strip():
            raise ValueError("run termination requires a non-empty actor")
        reason_code = (
            RunCancelReason(reason_code)
            if reason_code
            else (RunCancelReason.ABANDONED if operation == "abandon" else RunCancelReason.OPERATOR_CANCELLED)
        )
        target = RunState(target)
        if target not in {RunState.CANCELLED, RunState.INVALID}:
            raise ValueError(f"run termination target must be CANCELLED or INVALID, not {target.value}")
        references = tuple(str(locator) for locator in evidence)
        if any(not Path(locator).is_absolute() for locator in references):
            raise ValueError("termination evidence references must be absolute paths")

        row = self._require_run(conn, item_id, run_id)
        current = RunState(row["state"])
        if current in RUN_TERMINAL:
            if current != target:
                raise ValueError(
                    f"Run {item_id}/{run_id} already ended as {current.value}; "
                    f"refusing to restate it as {target.value}"
                )
            # Nothing is written, so report what the ledger already says
            # rather than what this caller asked for: a reader asking why a
            # Run ended must not be handed a reason that was never recorded.
            recorded = self._recorded_termination(conn, item_id, run_id)
            return self._termination_record(
                item_id, run_id,
                recorded["operation"] if recorded else operation,
                RunCancelReason(recorded["reason_code"]) if recorded else reason_code,
                recorded["reason"] if recorded else "",
                recorded["actor"] if recorded else "",
                tuple(recorded["evidence"]) if recorded else (),
                current, current, row["ended_at"],
                runtime_may_still_be_active=bool(recorded and recorded["runtime_may_still_be_active"]),
                already_terminal=True, dry_run=False,
            )
        # Legality stays with the state machine. cancel_run adds the reason,
        # the actor, and the audit record on top of it and nothing else.
        target = transition_run(current, target)
        # The control plane owns no worker handle: runs.state is a statement
        # of intent, not an observation of a live process. A RUNNING Run may
        # still have a worker executing, so require an explicit extra action.
        active = current == RunState.RUNNING
        if active and not acknowledge_active_worker:
            raise ActiveWorkerAckRequired(
                f"Run {item_id}/{run_id} is {current.value}: the control plane cannot observe or "
                "stop a worker process, so terminating here would not end one. Re-run with "
                "--acknowledge-active-worker to record the control-plane fact anyway."
            )
        at = now_iso()
        if not dry_run:
            conn.execute(
                "UPDATE runs SET state=?,ended_at=? WHERE item_id=? AND run_id=?",
                (target.value, at, item_id, run_id),
            )
            self._event(
                conn,
                item_id,
                "run_terminated",
                {
                    "run": run_id,
                    "operation": operation,
                    "from": current.value,
                    "to": target.value,
                    "reason_code": reason_code.value,
                    "reason": reason,
                    "actor": actor,
                    "evidence": list(references),
                    "runtime_may_still_be_active": active,
                },
                at=at,
            )
        return self._termination_record(
            item_id, run_id, operation, reason_code, reason, actor, references,
            current, target, at,
            runtime_may_still_be_active=active, already_terminal=False, dry_run=dry_run,
        )

    def _recorded_termination(self, conn, item_id: str, run_id: str) -> dict | None:
        """The operator termination already on record for this Run, if any."""
        rows = conn.execute(
            "SELECT payload_json FROM item_events WHERE item_id=? AND event_type='run_terminated' "
            "ORDER BY event_id DESC",
            (item_id,),
        ).fetchall()
        for row in rows:
            payload = json.loads(row["payload_json"])
            if payload.get("run") == run_id:
                return payload
        return None

    @staticmethod
    def _termination_record(
        item_id, run_id, operation, reason_code, reason, actor, references,
        from_state, to_state, ended_at, *, runtime_may_still_be_active, already_terminal, dry_run,
    ) -> dict:
        return {
            "item": item_id,
            "run": run_id,
            "operation": operation,
            "from": from_state.value,
            "to": to_state.value,
            "reason_code": reason_code.value,
            "reason": reason,
            "actor": actor,
            "evidence": list(references),
            "runtime_may_still_be_active": runtime_may_still_be_active,
            "already_terminal": already_terminal,
            "dry_run": dry_run,
            "ended_at": ended_at,
        }

    @staticmethod
    def _run(row) -> Run:
        return Run(
            item_id=row["item_id"],
            activity_id=row["activity_id"],
            id=row["run_id"],
            worker=row["worker"],
            idempotency_key=row["idempotency_key"],
            state=RunState(row["state"]),
            artifact_dir=row["artifact_dir"],
            attempt=row["attempt"],
            started_at=row["started_at"],
            ended_at=row["ended_at"],
        )


# Old name remains as a Python import alias only; storage is SQLite v2.
WorkItemStore = ServerOSStore
