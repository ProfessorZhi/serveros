#!/usr/bin/env python3
"""§17 · what a thread is for, and the work it refuses to take on.

THE FAILURE THIS EXISTS FOR
---------------------------
A real one, on this machine, and it is why this module is not speculative:

    LIT-0101   DELIVERED / SUCCESS
            |
            +-- the SAME execution conversation began preparing LIT-0205

Two Items, both real, both in `items_index` right now. The work of the first
was finished and closed. The thread that did it did not close with it, and
nothing in this tree could tell it to — `grep -rn "session_role|bound_item_ref|
THREAD_ITEM_MISMATCH|READ_ONLY_CLOSURE"` over this repository returned zero
hits before this module existed. An execution thread had no Item, so no
boundary could be drawn, so the next thing to hand it work handed it work.

WHAT A THREAD IS FOR IS NOT A SESSION FIELD
-------------------------------------------
`session_role` is not another optional key on a session record. It is the
answer to "what is this conversation for", and the whole point is that it is
supplied at creation by the caller rather than inferred later. It is required,
with no default, because a default would be a guess about somebody's intent
recorded as a fact — and the record would then be evidence for that guess.

A coordinator is the one role that is legitimately unbound: it coordinates
across Items and therefore has no single Item. An execution is the one role
that is never legitimate without an Item, because that is the case that
produced the failure above.

§19's "a coordinator only coordinates" is not enforced here. It is a rule about
what a coordinator DOES, and this module decides what work a thread may TAKE.
Enforcing the first requires judging an instruction's content, which is exactly
the "infer it from prose" move this repository keeps paying for.

PURITY
------
Every decision in this file is a pure function of its arguments. There is no
clock, no filesystem and no store: the binding arrives as a dict and the Item's
state arrives as a string. The reader that goes and gets an Item's state lives
in `runtime/daemon.py`, and it is kept out of here so that these decisions can
be tested without a state root, a database, or a machine.

THE FOUR REFUSALS, AND WHY EACH HAS ITS OWN WORD
------------------------------------------------
§17 names three of them. The fourth is here because the alternative was to
report it as one of the other three, and it is not any of them.

  INVALID_CONTEXT          no binding at all. ServerOS was never told what this
                           thread is for. The repair is to say so — bind the
                           thread, or start one that carries a role.
                           NOT a new word: the call names a session whose
                           purpose this system does not hold, which is the same
                           shape as `_device_for`'s refusal for a request that
                           contradicts the ref it was addressed to.

  THREAD_ITEM_MISMATCH     the caller named one Item and this thread is bound
                           to another. The repair is in the caller's payload.
                           NEW WORD.

  READ_ONLY_CLOSURE        the bound Item is DELIVERED or CLOSED. The thread is
                           not broken and its provider is perfectly able to
                           accept a turn; the work it was for is finished, so
                           there is nothing here to do. The repair is a new
                           thread, not a new Item name — which is why reporting
                           this as THREAD_ITEM_MISMATCH would send the caller
                           to fix the wrong field.
                           NEW WORD.

  THREAD_CLOSED_FOR_NEW_WORK  the thread itself was closed, while its provider
                           session is still alive and accepting input. This is
                           the state the failure above leaves behind, and it is
                           the reason this one is not ACTION_UNSUPPORTED: the
                           provider has every way to accept this turn, so
                           telling the caller "unsupported" would say something
                           false about the provider and stop it from ever
                           retrying.
                           NEW WORD.

Why the three new words and not `POLICY_DENIED` + `ACTION_UNSUPPORTED`: this
repository's own bar for adding a word to the closed vocabulary is that a
kernel produces a verdict whose REPAIR differs from every word already in it.
All three differ, in the field the caller has to change. That bar is stated in
`abilities/target-contract.yaml`, which is where the words are added.

A FIFTH WORD, ONE PLANE OVER (#237 B1/B3)
-----------------------------------------
NOT_CURRENT_WRITER is not a fifth thread refusal. It is what `admit_work`
produces when the SAME checks pass but the thread's RECORDED WRITE AUTHORITY is
not the current one — the scope moved to another owner/epoch/token, or the
thread's own claim expired, or no current-writer record was supplied to check
the claim against. Its repair differs from all four above: not "bind this
thread", not "start a thread that carries a role", not "name the right Item",
not "start a thread for this work", but "re-read the current writer from its
owner and stop writing here". A thread that lost the scope does not get it back
by writing again.

It is gated by a CLAIM: a binding that records no writer is not gated, so the
threads that existed before write authority was recorded keep working. That is
deliberate and it is the difference between arming a fence and breaking every
path that predates it.
"""
from __future__ import annotations

SCHEMA = "serveros/thread-binding.v1"

# A binding that RECORDS a writer is versioned separately (#237 B1). The schema
# name is the version, and the distinction it carries is load bearing: a v1
# thread records what it is FOR and claims no write authority, while a v2 thread
# additionally claims to be the CURRENT writer of its Item's write scope at an
# epoch with a fencing token. A reader that flattened the two would have to
# guess, from a missing field, whether the thread never claimed write authority
# or lost it — and "never had it" and "had it and was superseded" are opposite
# readings with the same bytes. `writer_axis` answers UNKNOWN for the first and
# STALE for the second.
SCHEMA_WRITER = "serveros/thread-binding.v2"

# The two roles. Closed, and the closure is load bearing: a third role would
# need a rule about whether it may be bound, and "there is no third role" is
# cheaper to keep true than a rule nobody remembers.
ROLES = ("coordinator", "execution")

# The state of an Item at which its thread stops accepting new work.
#
# CITED, NOT INVENTED. These are the values the live `items_index` holds for
# Items whose work is over — `LIT-0101 = DELIVERED/SUCCESS` among them — and
# the state machine that writes them is `workengine/model.py`, which is in
# another repository and not reachable from this one. So this is a citation of
# what the writer emits, not a second definition of it, and
# `tests/test_coordination.py` checks it against the live table when the table
# is there to be read.
#
# NEEDS_HUMAN is deliberately NOT here. An Item parked on a person is still
# open, and refusing work in its thread would strand the thread that a human is
# about to unblock.
READ_ONLY_ITEM_STATES = ("DELIVERED", "CLOSED")

# The three axes a caller can be on. Named so that a caller reading a record
# can tell "the Item is open" from "nothing could tell us about the Item".
ITEM_AXIS = ("OPEN", "READ_ONLY", "UNEVALUABLE")

# ---------------------------------------------------------------------------
# The writer axis (#237 B1/T3). What a thread's CLAIM of write authority is
# worth, from what was read of the current writer.
#
# Four values, and the fourth is the one that matters — same reasoning as
# ITEM_AXIS one screen up. A thread that records no writer is not a thread whose
# writer is UNKNOWN; it is a thread that never claimed write authority, and a
# `writer_axis` of UNKNOWN for it is a reading, not a refusal. But a thread that
# DOES record a writer and has no verifiable current-writer source to compare
# against has established nothing, and `admit_work` refuses that one rather than
# admitting it on a claim it could not check.
#
#   CURRENT   the binding's writer IS the current writer (owner, epoch and token
#             all match, and it has not expired)
#   STALE     the binding claims a writer, and the current writer is a DIFFERENT
#             owner/epoch/token — it was superseded
#   EXPIRED   the binding's own writer TTL has passed, whatever the current
#             writer says
#   UNKNOWN   either nothing is recorded (no claim) or no current-writer source
#             was supplied (nothing to check the claim against)
# ---------------------------------------------------------------------------
WRITER_AXIS = ("CURRENT", "STALE", "EXPIRED", "UNKNOWN")

# The writer TTL is compared as a STRING against a fixed-width UTC stamp:
# `YYYY-MM-DDTHH:MM:SSZ`. The same format the exclusive Work lease uses in
# `work-engine/workengine/store.py`, and pinned for the same reason — an
# `isoformat()` that drops the microsecond field sorts `...:00Z` and
# `...:00.123456Z` wrongly at the same second. A fixed-width `Z` stamp is a
# contract between whoever writes it and whoever compares it, and this module is
# the comparer.
WRITER_STAMP = "%Y-%m-%dT%H:%M:%SZ"

# The fields a writer tuple carries. `owner` is who holds the write scope, and
# `epoch` and `token` are the fence: epoch moves when authority changes hands,
# token moves when the SAME holder re-takes the scope (a promotion). Either
# moving makes the old tuple STALE.
WRITER_FIELDS = ("owner", "epoch", "token", "expires_at")


class ThreadRefused(Exception):
    """A refusal with one word from the closed vocabulary and one sentence.

    An exception rather than a return value for the same reason
    `TransportFailure` is: a caller that forgets to look at a returned refusal
    gets `{"ok": false}` and reads the rest of the dict as a success. The
    router catches this and turns it into a typed refusal, which is where a
    caller that is not in this process gets to see it.
    """

    def __init__(self, reason: str, detail: str) -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {detail}")


def parse_role(value) -> str:
    """The role a caller declared, or a refusal naming what is allowed."""
    role = str(value or "").strip()
    if role not in ROLES:
        raise ThreadRefused(
            "INVALID_CONTEXT",
            f"{value!r} is not a session role; a session is created with one of "
            f"{list(ROLES)}. There is no default: a session whose role nobody "
            f"stated is a thread whose purpose ServerOS would be guessing at, "
            f"and a guess recorded as a field reads afterwards like a fact")
    return role


def binding(*, session_role, bound_item_ref=None, work_item=None,
            device_ref=None, recorded_at=None, writer=None) -> dict:
    """The record that says what a thread is for. Raises `ThreadRefused`.

    Returned WITHOUT `session_ref`. A binding is a statement about a session
    that exists, and the session's ref is minted by the provider during create
    — so this function cannot produce a complete record and does not pretend
    to. The caller adds the ref from the provider's answer, and
    `AgentControlStore.put_binding` refuses a record that has none. A ref
    invented here to make the record look finished would be the one field in it
    that no provider ever said.
    """
    role = parse_role(session_role)
    item = str(bound_item_ref or "").strip() or None
    named = str(work_item or "").strip() or None

    if role == "execution" and not item:
        raise ThreadRefused(
            "INVALID_CONTEXT",
            "an execution session requires bound_item_ref. This is the field the "
            "failure above was missing: without it the thread has no Item, no "
            "boundary can be drawn around it, and the next thing to hand it work "
            "hands it work. Name the Item this thread is for, or create the "
            "session as a coordinator — which is the role for a thread that is "
            "legitimately about no single Item")
    if role == "coordinator" and item:
        raise ThreadRefused(
            "INVALID_CONTEXT",
            f"a coordinator session may not be bound to {item!r}. A coordinator "
            f"crosses Items by definition; a coordinator with one Item is an "
            f"execution that has not said so, and the two behave differently "
            f"under every rule that reads this field")
    if item and named and item != named:
        # The same disagreement `admit_work` refuses, caught at the door
        # instead of on the first turn. Both are needed: this one catches a
        # caller that contradicts itself inside a single payload, and that one
        # catches a caller that contradicts a decision made days ago.
        raise ThreadRefused(
            "THREAD_ITEM_MISMATCH",
            f"this call names work_item {named!r} and asks for a session bound "
            f"to {item!r}. A session is created for one Item, and the payload "
            f"that says otherwise is asking for a thread that would start life "
            f"already pointing at two")

    record = {
        "schema": SCHEMA_WRITER if writer is not None else SCHEMA,
        "session_role": role,
        "bound_item_ref": item,
        # Immutable, and the store is what makes that more than a comment:
        # there is no method that can change this field after the write.
        "bound_item_ref_frozen": True,
        "state": "OPEN",
    }
    if writer is not None:
        # A writer is a CLAIM of write authority, recorded with the thread. It
        # is present only on an execution thread — a coordinator has no Item and
        # therefore no write scope to claim — and it is what `admit_work` checks
        # when it is given a current-writer record to check against.
        if role != "execution":
            raise ThreadRefused(
                "INVALID_CONTEXT",
                f"a {role} thread may not record a writer. A coordinator "
                f"crosses Items and has no single write scope to own; a writer "
                f"on one would be a claim of authority nobody can check")
        record["writer"] = normalize_writer(writer)
    if device_ref:
        record["device_ref"] = str(device_ref)
    if recorded_at:
        record["recorded_at"] = str(recorded_at)
    return record


def item_axis(state, *, outcome=None) -> str:
    """Which of the three worlds an Item is in, from what was read of it.

    Three values, not two, and the third is the one that matters. This
    repository's most expensive recurring defect is a claim wider than its
    evidence, and "the Item is open" is a claim — the absence of an answer is
    not an open Item. A reader that could not reach `items_index` has
    established nothing, and the record says so rather than defaulting to the
    permissive branch.

    UNEVALUABLE does not refuse. It cannot: a machine with no `serveros.db`
    would then refuse every call, which would make this axis a gate rather than
    a reading, and would turn "nobody asked work-engine" into "the work is
    forbidden". It is reported, and the caller is told which input was missing.
    """
    text = str(state or "").strip().upper()
    if not text or text == "UNKNOWN":
        return "UNEVALUABLE"
    if text in READ_ONLY_ITEM_STATES:
        return "READ_ONLY"
    return "OPEN"


def normalize_writer(writer) -> dict:
    """One writer claim, with every field a comparison will need. Raises.

    `owner` is who holds the write scope (an agent/session identity), `epoch` is
    the Work epoch at which they hold it, and `token` is the fencing token that
    moves when the SAME owner re-takes the scope. `expires_at`, when present, is
    the fixed-width UTC stamp the TTL is compared as a string against — the same
    format the exclusive Work lease uses in `work-engine/workengine/store.py`.
    """
    if not isinstance(writer, dict):
        raise ThreadRefused(
            "INVALID_CONTEXT",
            f"a writer is a record with owner/epoch/token; got "
            f"{type(writer).__name__}. A writer that is not a record cannot be "
            f"compared against a current one")
    owner = str(writer.get("owner") or "").strip()
    if not owner:
        raise ThreadRefused(
            "INVALID_CONTEXT",
            "a writer needs an owner. Without one there is nothing to compare, "
            "and a writer nobody is would be admitted for everybody")
    epoch = writer.get("epoch")
    if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < 1:
        raise ThreadRefused(
            "INVALID_CONTEXT",
            f"writer epoch {epoch!r} is not a positive integer. The epoch is the "
            f"half of the fence that moves when authority changes hands; a "
            f"boolean is not an epoch and `isinstance(True, int)` is why this "
            f"check is spelled out")
    token = writer.get("token")
    if token is None or str(token).strip() == "":
        raise ThreadRefused(
            "INVALID_CONTEXT",
            "a writer needs a token. The epoch moves when authority changes "
            "hands and the token moves when the same owner re-takes the scope; "
            "without a token the second case is indistinguishable from the first")
    out = {"owner": owner, "epoch": int(epoch), "token": str(token)}
    if writer.get("expires_at"):
        out["expires_at"] = str(writer["expires_at"])
    return out


def writer_axis(binding_record, *, current_writer=None, now=None) -> str:
    """Which world a thread's recorded writer is in — one of `WRITER_AXIS`.

    Pure. `current_writer` is the authoritative current writer for the binding's
    write scope, read from its owner (the Agent Control / continuation store,
    joined where the scope's epoch lives). This function does not go and get it:
    a kernel that fetched its own inputs could not be tested without a store,
    which is the same reason `admit_work` takes an Item's state as a string.

    A binding that records NO writer answers UNKNOWN and is not a claim at all —
    see `SCHEMA_WRITER`. A binding that records one and is given no current
    writer to compare against also answers UNKNOWN, and `admit_work` refuses
    that case: a claim nobody checked is not an authorization.
    """
    writer = (binding_record or {}).get("writer") if binding_record else None
    if not writer:
        return "UNKNOWN"
    expires = writer.get("expires_at")
    if expires and now is not None and str(expires) <= str(now):
        return "EXPIRED"
    if current_writer is None:
        return "UNKNOWN"
    current = current_writer or {}
    if (str(writer.get("owner")) == str(current.get("owner"))
            and str(writer.get("epoch")) == str(current.get("epoch"))
            and str(writer.get("token")) == str(current.get("token"))):
        return "CURRENT"
    return "STALE"


def admit_work(*, binding_record, work_item=None, item_state=None,
               outcome=None, current_writer=None, now=None) -> dict:
    """Whether this call may hand work to this thread. Raises `ThreadRefused`.

    The order below is the order the answers matter in, and it is not the order
    the checks were written. A caller told THREAD_ITEM_MISMATCH would go and
    fix the Item name, which would not help: if the thread is closed, or its
    Item is delivered, then naming the right Item changes nothing. So the two
    checks that decide whether ANY work belongs in this thread run first, and
    the one that is merely about the caller's payload runs last.
    """
    named = str(work_item or "").strip() or None

    if not binding_record:
        raise ThreadRefused(
            "INVALID_CONTEXT",
            f"this session has no recorded session_role, so ServerOS does not "
            f"know what it is for and will not hand it work. Threads created "
            f"through `session.create` have one; a thread without one was "
            f"created before this record existed or through a path that never "
            f"stated a purpose. Bind it (`serverosctl coordination bind`) or "
            f"start a thread that carries a role")

    if str(binding_record.get("state") or "").upper() == "CLOSED":
        raise ThreadRefused(
            "THREAD_CLOSED_FOR_NEW_WORK",
            f"this thread was closed"
            + (f" at {binding_record['closed_at']}"
               if binding_record.get("closed_at") else "")
            + f" and will not take new work. Its provider session may still be "
              f"alive and perfectly able to accept a turn — that is the state "
              f"this refusal exists for, and it is why this is not reported as "
              f"ACTION_UNSUPPORTED, which would say something false about the "
              f"provider. Start a thread for the work in hand")

    bound = str(binding_record.get("bound_item_ref") or "").strip() or None
    axis = item_axis(item_state, outcome=outcome)
    if bound and axis == "READ_ONLY":
        raise ThreadRefused(
            "READ_ONLY_CLOSURE",
            f"{bound} is {str(item_state).strip().upper()}"
            + (f"/{str(outcome).strip().upper()}" if outcome else "")
            + f", so the work this thread was for is finished and it takes no "
              f"more. The thread is not broken and nothing is wrong with the "
              f"provider: there is simply nothing here to do. A new Item gets a "
              f"new thread — naming a different Item inside this one is the "
              f"move this refusal exists to stop")

    if named and bound and named != bound:
        raise ThreadRefused(
            "THREAD_ITEM_MISMATCH",
            f"this call names {named!r} and the thread is bound to {bound!r}. "
            f"{bound!r} is still open, so this is not a closure — it is a "
            f"different Item's work arriving in a thread that was created for "
            f"another one. Start a thread for {named!r}; a thread does not "
            f"change which Item it is for")

    # §17 + #237 B1/T3. The writer gate. Checked LAST, and deliberately so: a
    # caller whose thread is closed, or whose Item is delivered, has a DIFFERENT
    # field to change, and reporting that as "not the current writer" would send
    # them to re-acquire authority in a thread that takes no work at all.
    #
    # Only a thread that RECORDS a writer is gated. A v1 binding records none —
    # it never claimed write authority — and refusing it would break every
    # thread created before this field existed. Gating is armed by the claim,
    # and it is `admit_work`'s callers that decide which threads make one.
    claim = (binding_record or {}).get("writer") if binding_record else None
    writer_state = writer_axis(binding_record, current_writer=current_writer,
                               now=now)
    if claim and writer_state != "CURRENT":
        current = current_writer or {}
        if writer_state == "EXPIRED":
            why = "its claim has expired, so the scope must be re-acquired"
        elif writer_state == "STALE":
            why = ("the scope has moved to a different owner, epoch or token, "
                   "so this thread was superseded")
        else:
            why = ("no current-writer record was available to check the claim "
                   "against, and a write nobody could authorize is refused "
                   "rather than admitted on the claim alone")
        raise ThreadRefused(
            "NOT_CURRENT_WRITER",
            f"this thread claims the write scope {bound or '(unbound)'} as "
            f"{claim.get('owner')!r} at epoch {claim.get('epoch')!r} token "
            f"{claim.get('token')!r}, and it is not the current writer "
            f"({writer_state}): {why}. The current writer is "
            f"{current.get('owner')!r} at epoch {current.get('epoch')!r} token "
            f"{current.get('token')!r}. Read the current writer from its owner "
            f"and stop writing here; a thread that lost the scope does not get "
            f"it back by writing again")

    return {"state": "ADMITTED", "session_role": binding_record.get("session_role"),
            "bound_item_ref": bound, "item_axis": axis,
            "item_state": (str(item_state).strip().upper() if item_state else None),
            "work_item_named": named, "writer_axis": writer_state}


def tree_shape() -> dict:
    """What this module declares, for a caller that reads contracts.

    A session role is not a session field and does not live in
    `agent-session-contract.yaml#session_fields`: that block lists what a
    provider session record carries, and this is a decision ServerOS made about
    the conversation. Kept here, beside the rules that read it, so that the
    vocabulary and its enforcement cannot be two documents.
    """
    return {
        "schema": SCHEMA,
        "roles": list(ROLES),
        "required_at_create": ["session_role"],
        "required_when": {"execution": ["bound_item_ref"]},
        "forbidden_when": {"coordinator": ["bound_item_ref"]},
        "immutable": ["bound_item_ref"],
        "item_axis": list(ITEM_AXIS),
        "read_only_item_states": list(READ_ONLY_ITEM_STATES),
        "refusals": ["INVALID_CONTEXT", "THREAD_ITEM_MISMATCH",
                     "READ_ONLY_CLOSURE", "THREAD_CLOSED_FOR_NEW_WORK",
                     "NOT_CURRENT_WRITER"],
        "writer_axis": list(WRITER_AXIS),
        "schema_versions": [SCHEMA, SCHEMA_WRITER],
    }
