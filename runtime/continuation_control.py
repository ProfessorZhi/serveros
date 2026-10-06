#!/usr/bin/env python3
"""What a woken Parent may do, re-decided from the live store on every call.

`runtime/wake.py` starts a Parent and files a capability envelope beside the
receipt. This module is the other half: it is handed that envelope together with
one operation and its arguments, and it answers ALLOW, DENY or UNASKABLE after
re-reading the store — every check it makes is made now, from records, and none
of them is remembered from the moment the envelope was written.

WHY THE ENVELOPE IS NOT THE AUTHORITY
-------------------------------------
If a filed document were the authority, then a grant revoked one second after a
wake would still authorize everything that wake went on to do, and the only way
to withdraw authority would be to kill the process. So the envelope is a BINDING
— which wake this is, which Item and epoch it is for, which operations that wake
was checked for — and every piece of authority in it is re-read here. The
envelope's own expiry is the one exception, and it is not authority: it is the
smaller of two bounds that were already in force, and re-deriving it is what
this module does anyway.

The consequence a reader should expect: an operation that the envelope lists may
still be refused, and the refusal will name the field that changed. An operation
the envelope does NOT list is refused before anything is read.

THREE ANSWERS, AND THE THIRD IS NOT A SOFT DENY
-----------------------------------------------
`ALLOW` and `DENY` are verdicts about the request. `UNASKABLE` is a verdict about
this module: it could not look, so it has nothing to say. The store would not
open, an instant in a record cannot be read, the Item's completion boundary could
not be composed. Collapsing that into DENY would tell a person their authority
was withdrawn when the truth is that nobody checked — the failure `runtime/
ownership_gate.py` names as `DockerUnavailable` rendered as «no containers», and
`runtime/resource_anomaly.py` names as `codes_not_evaluated`. So a caller that
prints one line per refusal must print this one differently.

WHAT THIS MODULE DOES NOT DECIDE
--------------------------------
It does not judge whether the GRANT's scope suits the work: `projects`, `actions`
and `scope_digests` are `intake/daily_cycle.grant_reason`'s question, asked with
a board row this module has no reason to fetch, and a second judge of one
question is how two answers start disagreeing. It does not invent an operation:
the names it accepts are `runtime/wake.CONTINUATION_OPERATIONS`, imported rather
than restated, because a module that listed a permission its issuer does not have
would be widening the envelope from the reading side.

AND IT DOES NOT MUTATE. Every operation is executed by calling the verb that
already owns it — `serverosctl work checkpoint|park|accept` — so there is one
implementation of each mutation and this module never grows a second. What it
does is decide whether to make that call at all.
"""
from __future__ import annotations

import datetime
import importlib.util
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent

SCHEMA = "serveros/continuation-control.v1"
ENVELOPE_SCHEMA = "serveros/continuation-envelope.v1"

#: The three answers, in the sense `runtime/ownership_gate.py` uses the verbs.
DECISIONS = ("ALLOW", "DENY", "UNASKABLE")

#: Operations, and the one thing each of them CHANGES. The name is the whole
#: contract with the adapter; the module an operation belongs to is named here
#: so that this table can be checked against the verbs that exist rather than
#: believed.
OPERATIONS = {
    "continuation.execute": {
        "mutates": True,
        "owner": "runtime/bounded_executor.py (isolated engineering command)",
        "argv": (),
    },
    "continuation.inspect": {
        "mutates": False,
        "owner": "runtime/continuation.py + runtime/wake.py (read only)",
        "argv": (),
    },
    "child-result.inspect": {
        "mutates": False,
        "owner": "runtime/agent_control_store.AgentControlStore.admissions",
        "argv": (),
    },
    "work.checkpoint": {
        "mutates": True,
        "owner": "runtime/cli.py cmd_work_checkpoint (serverosctl work checkpoint)",
        "argv": ("item", "--epoch", "--parent", "--next-action"),
    },
    "work.park": {
        "mutates": True,
        "owner": "runtime/cli.py cmd_work_park (serverosctl work park)",
        "argv": ("checkpoint_ref", "--reason"),
    },
    "work.accept": {
        "mutates": True,
        "owner": "runtime/cli.py cmd_work_accept (serverosctl work accept)",
        "argv": ("cycle", "--by", "--locator"),
    },
    # Named by the continuation design and unimplementable, kept so that a caller
    # finds an answer instead of a gap. See UNSUPPORTED_BECAUSE.
    "work.complete": {
        "mutates": True,
        "owner": None,
        "argv": (),
    },
}

#: Why `work.complete` is here and does nothing. Measured, not assumed: the
#: `work` verb family (`runtime/cli.py`) has no `complete`; the control store has
#: no completions collection and no `*_complete` method; and completion in this
#: fabric is not a record anybody writes — it is a VERDICT composed from the
#: Item's document chain by `intake/workchain.completion_of`, with the vocabulary
#: COMPLETE / DISCHARGED / OPEN / NOT_ASSESSED, and it is READ by `work item`.
#: So an operation that sets it would be a second completion authority, which
#: §1 of this product's own design forbids. The operation a Parent actually has
#: is `continuation.inspect`, which reports the boundary; there is nothing to
#: call to change it.
UNSUPPORTED_BECAUSE = (
    "no such object exists to write. Completion in this fabric is not a record: "
    "it is a verdict composed from the Item's document chain by "
    "intake/workchain.completion_of (COMPLETE / DISCHARGED / OPEN / "
    "NOT_ASSESSED) and read by `serverosctl work item`. The `work` verb family "
    "has no `complete` verb and the control store has no completions collection, "
    "so implementing this would create a second completion authority. Read the "
    "boundary with `continuation.inspect` instead"
)


class BadEnvelope(RuntimeError):
    """The document this module was handed is not one it can act on."""


def _delegation():
    """`context/delegation.py`, under the ONE name `runtime/wake.py` loads it
    as — the same reason that module gives: two names would be two module
    objects, and `derivation` is one function whose answer both halves must
    reach."""
    name = "serveros_context_delegation"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name, _ROOT / "context" / "delegation.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _wake():
    """`runtime/wake.py`, for `CONTINUATION_OPERATIONS` and nothing else."""
    name = "serveros_runtime_wake"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _HERE / "wake.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def issuer_operations() -> tuple[str, ...]:
    """What the module that FILES an envelope says a wake may carry.

    The permission list belongs to the issuer, and this function exists so that
    the reading side can be checked against it rather than trusted to agree. A
    reader that grew an operation of its own would be widening a capability; a
    reader that lost one would be narrowing authority the issuer granted. Neither
    shows up in `OPERATIONS` alone, because a module cannot see its own drift —
    so the check is a comparison and `tests/test_continuation_control.py` makes
    it: `set(issuer_operations()) | set(issuer_unsupported()) == set(OPERATIONS)`.
    """
    return tuple(_wake().CONTINUATION_OPERATIONS)


def issuer_unsupported() -> tuple[str, ...]:
    """The names the issuer declares and cannot perform. Read for the reason
    above: `work.complete` is in `OPERATIONS` because the design names it, and
    the declaration that it is unimplementable belongs to the envelope, not to
    this table."""
    return tuple(_wake().CONTINUATION_OPERATIONS_UNSUPPORTED)


def _instant(raw) -> datetime.datetime | None:
    """`raw` as an aware datetime, or None. The same three lines, and the same
    refusal to invent a timezone, as `runtime/wake.py#_instant`."""
    try:
        parsed = datetime.datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo is not None else None


def load_envelope(path) -> dict:
    """Read a filed envelope, refusing anything that is not one.

    A missing or malformed envelope is a refusal and not a default: this file is
    the only thing that says WHICH wake is calling, and a module that answered
    without one would be answering any of them.
    """
    path = Path(path)
    if not path.is_absolute():
        raise BadEnvelope(f"{path} is not absolute, so it is not a path this "
                          f"adapter was handed by a receiver")
    if not path.exists():
        raise BadEnvelope(
            f"no capability envelope at {path}. This adapter is started by a "
            f"woken Parent and reads its envelope from ${ENVELOPE_ENV_NAME}; "
            f"without one it cannot say which wake is asking, and it will not "
            f"guess")
    envelope = json.loads(path.read_text())
    if not isinstance(envelope, dict) or envelope.get("schema") != ENVELOPE_SCHEMA:
        raise BadEnvelope(
            f"{path} is not a {ENVELOPE_SCHEMA} document (found "
            f"{(envelope or {}).get('schema') if isinstance(envelope, dict) else type(envelope).__name__!r})")
    return envelope


#: The variable `runtime/wake.py#ENVELOPE_ENV` sets. Restated as a literal
#: because `load_envelope`'s message quotes it and importing `wake` to name a
#: variable in a message would load the receiver to explain a file the receiver
#: is not running. `tests/test_continuation_control.py` asserts the two agree.
ENVELOPE_ENV_NAME = "SERVEROS_CONTINUATION_ENVELOPE"


def _check(kind: str, decision: str, because: str, **extra) -> dict:
    return {"check": kind, "decision": decision, "because": because, **extra}


def verify(*, envelope: dict, store, contexts: dict, operation: str,
           arguments: dict | None = None, now: str,
           completion_of_item=None) -> dict:
    """Decide one operation for one wake, from the store as it stands now.

    EVERY CHECK RUNS, and the first DENY is the verdict. They all run rather than
    short-circuiting only in the sense that the report carries what each one
    found; the decision is the first refusal, so the reason a person reads is the
    most binding one rather than the last one evaluated. Identity before
    authority, and both before the operation: a call naming the wrong Item is
    reported as a call naming the wrong Item, not as a grant that has expired.

    `now` is a parameter and this module holds no clock, for `derivation`'s
    reason: a verdict computed from a clock the caller cannot see is a verdict
    nobody can reproduce.

    `completion_of_item` is REQUIRED and not defaulted. It is the boundary the
    design names as one of the things a mutation re-checks, and a default that
    answered «not assessed» would be this module reporting a check as passed
    without having made it. The caller supplies the reader; the adapter supplies
    the real one.
    """
    arguments = dict(arguments or {})
    when = _instant(now)
    if when is None:
        return {"schema": SCHEMA, "decision": "UNASKABLE", "operation": operation,
                "because": f"`now` is {now!r}, which is not an instant this module "
                           f"can read. A verdict needs two instants and only one "
                           f"of them is in the record",
                "checks": [], "envelope_ref": envelope.get("action_key")}
    if completion_of_item is None:
        return {"schema": SCHEMA, "decision": "UNASKABLE", "operation": operation,
                "because": "no completion reader was supplied, so the Item's "
                           "completion boundary cannot be checked. This is a "
                           "wiring fault in the caller and not a fact about the "
                           "request",
                "checks": [], "envelope_ref": envelope.get("action_key")}

    checks: list[dict] = []
    verdict = _verify_inner(envelope=envelope, store=store, contexts=contexts,
                            operation=operation, arguments=arguments, when=when,
                            now=now, completion_of_item=completion_of_item,
                            checks=checks)
    return {"schema": SCHEMA, "decision": verdict["decision"],
            "operation": operation, "because": verdict["because"],
            "checks": checks, "envelope_ref": envelope.get("action_key"),
            "work_item": envelope.get("work_item_ref"),
            "epoch": envelope.get("epoch"),
            "read_at": now}


def _verify_inner(*, envelope, store, contexts, operation, arguments, when, now,
                  completion_of_item, checks) -> dict:
    def stop(kind, decision, because, **extra):
        checks.append(_check(kind, decision, because, **extra))
        return {"decision": decision, "because": because}

    # -- 1. is this operation in the envelope at all -------------------------
    allowed = list(envelope.get("allowed_operations") or [])
    unsupported = list(envelope.get("unsupported_operations") or [])
    if operation in unsupported:
        return stop("OPERATION_NAMED_UNSUPPORTED", "DENY",
                    f"{operation} is named by this wake and cannot be performed: "
                    f"{UNSUPPORTED_BECAUSE}")
    if operation not in allowed:
        return stop("OPERATION_NOT_IN_ENVELOPE", "DENY",
                    f"{operation} is not one of the operations this wake carries "
                    f"({', '.join(allowed) or 'none'}). A wake carries the verbs "
                    f"it was checked for, and this is not one of them",
                    allowed_operations=allowed)
    checks.append(_check("OPERATION_IN_ENVELOPE", "ALLOW",
                         f"{operation} is one this wake carries"))

    # -- 2. has this wake's own window closed -------------------------------
    expires = _instant(envelope.get("expires_at"))
    if expires is None:
        return stop("ENVELOPE_EXPIRES_UNREADABLE", "UNASKABLE",
                    f"the envelope's `expires_at` is "
                    f"{envelope.get('expires_at')!r}, which is not an instant")
    if when >= expires:
        return stop("ENVELOPE_EXPIRED", "DENY",
                    f"this wake's capability ended at {expires.isoformat()} "
                    f"({envelope.get('expires_because')}) and it is now {now}. "
                    f"The wake's own bound is spent")
    checks.append(_check("ENVELOPE_IN_FORCE", "ALLOW",
                         f"ends {expires.isoformat()} "
                         f"({envelope.get('expires_because')})"))

    # -- 3. is the checkpoint still this wake's -----------------------------
    ref = str(envelope.get("checkpoint_ref") or "")
    holdings = store.checkpoints()
    record = holdings.get(ref)
    if record is None:
        return stop("CHECKPOINT_NOT_IN_STORE", "DENY",
                    f"this wake was issued for {ref} and the store no longer "
                    f"holds that checkpoint. A wake for a record that is gone is "
                    f"a wake nobody can check")
    for field, mine in (("work_item", envelope.get("work_item_ref")),
                        ("epoch", envelope.get("epoch"))):
        if str(record.get(field)) != str(mine):
            return stop("CHECKPOINT_IDENTITY_CHANGED", "DENY",
                        f"{ref} now records {field}={record.get(field)!r} and this "
                        f"wake was issued for {field}={mine!r}. The record moved "
                        f"under the wake, so the wake is not about it any more")
    checks.append(_check("CHECKPOINT_IS_THIS_WAKE", "ALLOW",
                         f"{ref} is still {envelope.get('work_item_ref')} epoch "
                         f"{envelope.get('epoch')}"))
    live = {r: c for r, c in holdings.items()
            if not c.get("superseded_by") and not (
                c.get("parked_at") and not c.get("unparked_at"))}
    if ref not in live:
        reason = ("superseded by " + str(record.get("superseded_by"))
                  if record.get("superseded_by")
                  else f"parked at {record.get('parked_at')}")
        return stop("CHECKPOINT_NOT_LIVE", "DENY",
                    f"{ref} is {reason}, so it is not a record anything may "
                    f"continue from. The same set `runtime/wake.live_checkpoints` "
                    f"answers for")
    checks.append(_check("CHECKPOINT_LIVE", "ALLOW", f"{ref} is still live"))

    # -- 4. does the authority the wake was checked against still stand -----
    grants = store.grants()
    grant_ref = str(envelope.get("grant_ref") or "")
    grant = grants.get(grant_ref)
    if grant is None:
        return stop("GRANT_NOT_IN_STORE", "DENY",
                    f"the grant {grant_ref} this wake was issued under is no "
                    f"longer in the store. Authority is the store's, and the "
                    f"store no longer has this one")
    if grant.get("revoked") is not False:
        return stop("GRANT_REVOKED", "DENY",
                    f"{grant_ref} records revoked={grant.get('revoked')!r}"
                    + (f" at {grant.get('revoked_at')}"
                       + (f": {grant.get('revoked_reason')}"
                          if grant.get('revoked_reason') else "")
                       if grant.get("revoked_at") else "")
                    + ". A revoked grant authorizes nothing, whatever a wake "
                      "filed while it was live still says")
    valid_from, expires_at = _instant(grant.get("valid_from")), _instant(grant.get("expires_at"))
    if valid_from is None or expires_at is None:
        return stop("GRANT_WINDOW_UNREADABLE", "UNASKABLE",
                    f"{grant_ref} carries valid_from={grant.get('valid_from')!r} "
                    f"and expires_at={grant.get('expires_at')!r}, and a grant "
                    f"whose window cannot be read is not one that can be said to "
                    f"be in force")
    if not (valid_from <= when < expires_at):
        return stop("GRANT_NOT_CURRENT", "DENY",
                    f"{grant_ref} is in force from {valid_from.isoformat()} to "
                    f"{expires_at.isoformat()} and it is now {now}")
    checks.append(_check("GRANT_IN_FORCE", "ALLOW",
                         f"{grant_ref} is live and unrevoked"))
    delegated_by = str(grant.get("delegated_by") or "")
    derivation = _delegation().derivation(grant, grant_ref,
                                          contexts.get(delegated_by), now=now)
    if derivation["state"] != "AUTHORIZED":
        return stop("DELEGATION_DOES_NOT_AUTHORIZE", "DENY",
                    f"{grant_ref} derives from {delegated_by} and that delegation "
                    f"no longer authorizes: {derivation['because']}",
                    derivation_state=derivation["state"])
    checks.append(_check("DELEGATION_AUTHORIZES", "ALLOW",
                         f"{delegated_by} authorizes now"))

    # -- 5. the completion boundary, before anything is written -------------
    item_id = str(envelope.get("work_item_ref") or "")
    boundary = completion_of_item(item_id)
    if not isinstance(boundary, dict) or "state" not in boundary:
        return stop("COMPLETION_UNREADABLE", "UNASKABLE",
                    f"the completion boundary of {item_id} could not be read "
                    f"({boundary!r}). This is this adapter's own blindness and "
                    f"not a fact about the Item, so nothing is allowed on the "
                    f"strength of it")
    if boundary["state"] == "COMPLETE":
        return stop("ITEM_COMPLETION_BOUNDARY_REACHED", "DENY",
                    f"{item_id} is COMPLETE ({boundary.get('why')}), and work is "
                    f"not continued on a finished Item. Reopening is a person's "
                    f"decision and not a wake's")
    checks.append(_check("COMPLETION_BOUNDARY_OPEN", "ALLOW",
                         f"{item_id} is {boundary['state']}"
                         + ("" if boundary["state"] == "NOT_ASSESSED"
                            else f" ({boundary.get('why')})")))

    # -- 6. the operation's own target, which is what each one differs by ---
    def child_checks(cycle: str, verb: str):
        """The three answers a child ref gets, the same ones for reading it as
        for receiving it.

        `child-result.inspect` and `work.accept` are held to one predicate on
        purpose. If a ref were readable but not receivable, a Parent would read
        a child's result out of another Item's scope and only then be told the
        ref was never its own — and the reading is the leak. One predicate means
        a reader never learns something a mutator was refused.
        """
        named = [str(r) for r in (envelope.get("allowed_child_refs") or [])]
        if cycle not in named:
            return stop("CHILD_NOT_THIS_WAKE'S", "DENY",
                        f"`{verb}` was aimed at {cycle!r} and this "
                        f"checkpoint names {named or 'no child refs at all'}"
                        + (". A wake receives the results its checkpoint named "
                           "and no others" if named else
                           ". Nothing was named, so there is nothing this wake "
                           "may receive: a result its own record does not "
                           "mention is another scope's"))
        admission = store.admissions().get(cycle)
        if admission is None:
            return stop("CHILD_ADMISSION_NOT_IN_STORE", "DENY",
                        f"{cycle} is named by this checkpoint and the store holds "
                        f"no admission under it. Reading or recording an answer "
                        f"about a dispatch is a claim about that dispatch, and "
                        f"there is no dispatch here to have answered")
        # WHICH ITEM A CYCLE WAS ADMITTED FOR IS `selection.selected`, AND IT IS A
        # LIST. `intake/daily_cycle.select_focus` may select several rows and
        # `AgentControlStore.admit` checks each of them against the grant's
        # `items`, so the question is membership and not equality.
        #
        # The first version of this read `admission["work_item"]`, and an
        # admission record HAS NO SUCH FIELD — measured, not assumed: the store
        # keeps `cycle_ref`, `grant_ref`, `selection`, `reservation`, `lease`,
        # `action_key`, `admitted_at` and the phase fields, and no `work_item`
        # anywhere. So that version refused EVERY child of EVERY wake, and it
        # looked exactly like a boundary while being a constant.
        # `tests/test_continuation_control.py` is what found it, and the test
        # that keeps it found is the positive control beside the four refusals.
        selection = admission.get("selection")
        selected = selection.get("selected") if isinstance(selection, dict) else None
        if not isinstance(selected, list) or not selected:
            return stop("CHILD_ADMISSION_SELECTION_UNREADABLE", "UNASKABLE",
                        f"the admission {cycle} carries no readable "
                        f"`selection.selected`, so which Item it was admitted for "
                        f"cannot be read here. That is this module's own blindness "
                        f"and not a fact about the cycle, so nothing is allowed on "
                        f"the strength of it")
        selected_items = [str(i) for i in selected]
        if item_id not in selected_items:
            return stop("CHILD_ADMISSION_IS_ANOTHER_ITEM", "DENY",
                        f"the admission {cycle} was admitted for "
                        f"{', '.join(selected_items)} and this wake is for "
                        f"{item_id!r}. A checkpoint naming another Item's cycle "
                        f"is a record to look at, not permission to reach it")
        return None

    target = OPERATIONS[operation]
    if operation == "child-result.inspect":
        refused = child_checks(str(arguments.get("cycle") or ""), operation)
        if refused:
            return refused
        checks.append(_check("CHILD_OWNED_BY_THIS_WAKE", "ALLOW",
                             f"{arguments.get('cycle')} is named here and "
                             f"admitted to {item_id}"))
    if not target["mutates"]:
        return stop("READ_OPERATION", "ALLOW",
                    f"{operation} reads; every check above held")

    if operation == "continuation.execute":
        if ("engineering.execute" not in grant.get("actions", [])
                or not isinstance(grant.get("engineering"), dict)
                or item_id not in grant.get("items", [])):
            return stop("ENGINEERING_NOT_GRANTED", "DENY",
                        "this Item requires an explicit engineering.execute grant and binding")
        checks.append(_check("ENGINEERING_GRANTED", "ALLOW",
                             "execution binding is read from the live grant; the executor checks its paths and bounds"))

    elif operation == "work.checkpoint":
        wanted = str(arguments.get("item") or "")
        if wanted != item_id:
            return stop("TARGET_ITEM_IS_NOT_THIS_WAKE", "DENY",
                        f"`work checkpoint` was aimed at {wanted!r} and this wake "
                        f"is for {item_id!r}. A continuation writes its own Item "
                        f"and no other")
        scope = [str(p) for p in (envelope.get("allowed_write_scope") or [])]
        asked = [str(p) for p in (arguments.get("write_scope") or [])]
        def inside(path, root):
            # These are declared scopes, not an OS filesystem fence. Refuse
            # traversal before the segment comparison so a successor cannot
            # acquire a wider declaration through `allowed/../elsewhere`.
            if any(not value or ".." in value.split("/") or "\\" in value
                   or "\x00" in value for value in (path, root)):
                return False
            candidate, parent = Path(path), Path(root)
            return candidate.is_relative_to(parent)

        outside = [p for p in asked if not any(inside(p, s) for s in scope)]
        if outside:
            return stop("WRITE_SCOPE_OUTSIDE_ENVELOPE", "DENY",
                        f"this wake's write scope is {scope or '(none recorded)'} "
                        f"and the checkpoint asks to add {outside}. A scope is the "
                        f"one the person recorded against this Item, and widening "
                        f"it is not a continuation")
        checks.append(_check("TARGET_ITEM_AND_SCOPE", "ALLOW",
                             f"checkpointing {wanted} inside {scope or '(no scope recorded)'}"))

    elif operation == "work.park":
        wanted = str(arguments.get("checkpoint_ref") or "")
        record_of = store.checkpoints().get(wanted)
        if record_of is None:
            return stop("PARK_TARGET_NOT_IN_STORE", "DENY",
                        f"`work park` was aimed at {wanted!r} and the store holds "
                        f"no such checkpoint")
        if str(record_of.get("work_item")) != item_id:
            return stop("PARK_TARGET_IS_NOT_THIS_ITEM", "DENY",
                        f"`work park` was aimed at {wanted}, which belongs to "
                        f"{record_of.get('work_item')!r}; this wake is for "
                        f"{item_id!r}")
        if str(record_of.get("epoch")) != str(envelope.get("epoch")):
            return stop("PARK_TARGET_IS_ANOTHER_EPOCH", "DENY",
                        f"`work park` was aimed at {wanted}, epoch "
                        f"{record_of.get('epoch')}; this wake carries epoch "
                        f"{envelope.get('epoch')}. Setting down another epoch's "
                        f"record takes a wake away from work this wake is not")
        checks.append(_check("PARK_TARGET", "ALLOW",
                             f"{wanted} is this Item at this epoch"))

    elif operation == "work.accept":
        cycle = str(arguments.get("cycle") or "")
        refused = child_checks(cycle, operation)
        if refused:
            return refused
        checks.append(_check("CHILD_OWNED_BY_THIS_WAKE", "ALLOW",
                             f"{cycle} is named here and admitted to {item_id}"))

    else:  # pragma: no cover - OPERATIONS is closed and asserted below
        raise BadEnvelope(f"{operation} is in OPERATIONS and has no target rule")

    return {"decision": "ALLOW",
            "because": f"{operation} is inside this wake's envelope and every "
                       f"check it makes held against the store as it stands"}
