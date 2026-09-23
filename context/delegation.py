#!/usr/bin/env python3
"""Standing delegation: which recorded grants still authorize, and why not.

A grant is a scope — Items, actions, projects, a budget — written down so that
a cycle can carry an objective without a person approving it again. The one
sentence this module implements:

    a grant is a scope on a trusted context, and it authorizes a cycle only
    while that context still IS one — whatever window the grant carries.

The second half is the load-bearing half, and it is the half nothing else
checks. `intake/daily_cycle.grant_reason` validates a grant's own scope, time
and budget and says in its docstring that it does not validate "the
authenticity of the supplied grant". That is this module's job: `delegated_by`
must resolve to a context `context/actor.py#authorizes` accepts *now*, so a
grant cannot outlive the delegation it derives from, cannot be extended by
editing its own `expires_at`, and cannot be invented by writing a file. This
module returns the map `intake/daily_cycle.select_focus` is handed; nothing
else on this node builds one.

Where the grants live
---------------------
`runtime/agent_control_store.py` holds them, in the store's `grants`
collection, for the reason that module's own docstring gives: a delegation a
restart lost would silently withdraw authority, and re-minting one on demand is
authority nobody granted. This module reads that collection and writes nothing
— there is no `main()`, no `--write`, and no way to reach the store through it.

Four states, and the fourth is the point
----------------------------------------
`AUTHORIZED` is the only one that puts a grant in the map. The other three are
kept apart because they are different facts about different things:

  DELEGATION_UNRESOLVED          the store holds no context under
                                 `delegated_by`. Its root is gone — the context
                                 window closed and `sweep` dropped it, or the
                                 record was hand-edited — so what was delegated
                                 can no longer be established. Not a revocation:
                                 nothing was withdrawn, the evidence expired.
  DELEGATION_DOES_NOT_AUTHORIZE  the root is there and refuses. The context is
                                 STALE or EXPIRED, or its provider did not
                                 verify, or its provenance is not FACT. The
                                 reason is `context/actor.py`'s own sentence,
                                 copied rather than re-worded, because there is
                                 one module that decides this and a second
                                 phrasing of it would be a second opinion.
  GRANT_MALFORMED                the record cannot be judged at all — it names
                                 no delegation, or its own two names for its ref
                                 disagree.

A collapsed version of this reads "no grant", and the collapse is the failure
§5.0 is about: a person asking why the cycle carried nothing would be told the
same sentence whether nobody ever delegated, authority was withdrawn, or the
evidence went stale — three answers with three different next actions.

`GRANT_NOT_RECORDED` is a fifth state and it is about a different object: a ref
some row names that this store has never heard of. It is reported against the
board rather than against the store, and it is here because the two absences
must not print as one: "the board names no grant" and "the store holds none"
are different gaps with different producers.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent

SCHEMA = "serveros/delegation.v1"

DERIVATION_STATES = (
    "AUTHORIZED",
    "DELEGATION_UNRESOLVED",
    "DELEGATION_DOES_NOT_AUTHORIZE",
    "GRANT_MALFORMED",
    "GRANT_NOT_RECORDED",
)

# Every field a reported row can carry, and the record it came from. Enforced
# by `no_unsourced` below, so a field whose producer is missing is a refusal
# rather than an extra column — the reading a person cannot tell from a fact.
ROW_PRODUCED = {
    "grant_ref": "the store's `grants` key, or the row's own `grant_ref` for a "
                 "ref nothing recorded",
    "delegated_by": "the stored grant's `delegated_by`",
    "state": "this module's `derivation`",
    "because": "this module's `derivation`",
    "delegation": "`context/actor.py#authorizes`, copied whole",
    "revoked": "the stored grant's `revoked` — written by the store, never here",
    "revoked_at": "the stored grant's `revoked_at`",
    "revoked_reason": "the stored grant's `revoked_reason`",
    "valid_from": "the stored grant's `valid_from`",
    "expires_at": "the stored grant's `expires_at`",
    "projects": "the stored grant's `projects`",
    "items": "the stored grant's `items`",
    "actions": "the stored grant's `actions`",
    # Copied because a second reader now decides authority by it:
    # `understanding_semantic` lets a machine reconcile a claim about a subject
    # only where a delegation covers that subject, and a grant whose `covers`
    # cannot be seen in this report is one whose refusals a person would have to
    # guess at.
    "covers": "the stored grant's `covers` — the subject prefixes it delegates",
    "scope_digests": "the stored grant's `scope_digests`",
    "budget": "the stored grant's `budget`",
    "in_envelope": "whether this module put the grant in the map",
}

# The two names a grant has for its own ref: the store's key and the field
# `intake/daily_cycle.grant_reason` reads. A record whose two names disagree
# means two things, and a reader that picked one would be choosing which of the
# record's own statements to believe.
REF_FIELD = "ref"
REF_KEY = "grant_ref"
DELEGATION_FIELD = "delegated_by"


class Refused(RuntimeError):
    """This module was handed something it cannot report on honestly."""


def _actor_module():
    """`context/actor.py`, loaded by path — the same idiom its siblings use."""
    name = "serveros_context_actor"
    if name in sys.modules:
        return sys.modules[name]
    path = _HERE / "actor.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def no_unsourced(mapping: dict, table: dict, where: str) -> dict:
    """Refuse a row carrying a key nothing in `table` produced.

    The same guard the other readers in this tree apply at the point a record
    is EMITTED rather than at the point it is built: `work focus` prints these
    rows, and a printed field with no source is indistinguishable from a
    measured one.
    """
    unknown = sorted(k for k in mapping if k not in table)
    if unknown:
        raise Refused(
            f"{where} carries keys this module has no producer for: {unknown}. "
            f"Every emitted field names the record it came from; a field that "
            f"cannot is a value somebody invented.")
    return mapping


def shape_reason(grant: object, ref: str) -> str | None:
    """Why this record cannot be judged at all, or None.

    Deliberately narrow. Everything else a grant must carry — a window, a
    budget, a scope — is `intake/daily_cycle.grant_reason`'s to validate, and
    checking those here would be a second judge of one question. What is
    checked here is only what THAT function cannot see: whether there is a
    delegation at the root, and whether the record agrees with itself.
    """
    if not isinstance(grant, dict):
        return f"{ref} is not a mapping, so it is not a grant"
    if not str(grant.get(DELEGATION_FIELD) or "").strip():
        return (f"{ref} names no {DELEGATION_FIELD}, so there is no delegation "
                f"for its scope to be a scope on")
    named = grant.get(REF_FIELD)
    if named is not None and str(named) != str(ref):
        return (f"{ref} carries {REF_FIELD}={named!r}. The store's key and the "
                f"field the cycle reads are two names for one ref, and a record "
                f"whose own two names disagree is a record that means two things")
    return None


def derivation(grant: object, ref: str, context_record: dict | None, *,
               now: str) -> dict:
    """Does this grant derive from a delegation that authorizes right now?

    `now` is a parameter and this module holds no clock: a verdict computed
    from a clock the caller cannot see is a verdict nobody can reproduce, and
    the record a reader is checking it against was minted under the same rule.
    """
    malformed = shape_reason(grant, ref)
    if malformed:
        return {"state": "GRANT_MALFORMED", "because": malformed}
    delegated_by = str(grant[DELEGATION_FIELD])
    if context_record is None:
        return {
            "state": "DELEGATION_UNRESOLVED",
            "because": (f"no trusted context {delegated_by!r} is held by this "
                        f"store. Nothing was withdrawn — the context window "
                        f"closed, or the record is not one this node reported — "
                        f"so what {ref} was delegated by cannot be established "
                        f"any more"),
        }
    verdict = _actor_module().authorizes(context_record, now)
    if not verdict.get("authorizes"):
        return {
            "state": "DELEGATION_DOES_NOT_AUTHORIZE",
            "because": (f"the delegation {ref} derives from does not authorize "
                        f"a mutation now: {verdict.get('because')}"),
            "delegation": verdict,
        }
    return {
        "state": "AUTHORIZED",
        "because": (f"{delegated_by} authorizes now ({verdict.get('because')}); "
                    f"the grant's own scope, window and budget are judged by "
                    f"intake/daily_cycle.grant_reason, not here"),
        "delegation": verdict,
    }


def envelope_of(grant: dict, ref: str) -> dict:
    """The grant as `select_focus` reads it, with its two names for the ref made one.

    `intake/daily_cycle.grant_reason` reads `grant["ref"]`; the store keys the
    collection by `grant_ref`. The map is built from the key, so the value
    inside it cannot disagree with it — a grant stored without a `ref` field is
    not a grant with a missing name, it is one whose name is its key.

    The record is copied rather than rebuilt. A field list here would be a
    second statement of what `grant_reason` requires, and the day the two
    disagreed the envelope would be silently narrower than the policy.
    """
    return {**grant, REF_FIELD: str(ref)}


def envelopes(grants: dict, contexts: dict, *, now: str, named_by=()) -> dict:
    """The map a cycle may be admitted under, and the report of every refusal.

    `named_by` is the set of `grant_ref`s the board's rows name — what the
    other end of this join looks like. A ref in it that the store has never
    been told about is reported as `GRANT_NOT_RECORDED`, which is a fact about
    the board and not about the store; reporting it as a missing grant would
    point a reader at the wrong producer.

    Only `AUTHORIZED` grants enter the map. Everything else is in `rows` with
    its reason, because the caller prints them and an empty selection with no
    reason beside it reads as "nothing to do".
    """
    rows: list[dict] = []
    envelopes: dict[str, dict] = {}
    for ref, grant in sorted(grants.items()):
        context_record = None
        if isinstance(grant, dict):
            delegated_by = grant.get(DELEGATION_FIELD)
            if isinstance(delegated_by, str) and delegated_by:
                context_record = contexts.get(delegated_by)
        verdict = derivation(grant, ref, context_record, now=now)
        row = {"grant_ref": str(ref), "state": verdict["state"],
               "because": verdict["because"], "in_envelope": False}
        if isinstance(grant, dict):
            # Copied only when present: a row that carried `revoked: None` for
            # a grant that records no such field would be this module inventing
            # one, and `revoked` is the field a reader looks at first.
            for field in ("delegated_by", "revoked", "revoked_at",
                          "revoked_reason", "valid_from", "expires_at",
                          "projects", "items", "actions", "scope_digests",
                          "budget", "covers"):
                if field in grant:
                    row[field] = grant[field]
        if "delegation" in verdict:
            # The same dict `derivation` reached its state from, not a second
            # call that would be a second verdict about one record.
            row["delegation"] = verdict["delegation"]
        if verdict["state"] == "AUTHORIZED":
            row["in_envelope"] = True
            envelopes[str(ref)] = envelope_of(grant, ref)
        rows.append(no_unsourced(row, ROW_PRODUCED, f"the row for {ref!r}"))

    for ref in sorted({str(r) for r in named_by or () if str(r).strip()}):
        if ref in grants:
            continue
        row = {
            "grant_ref": ref,
            "state": "GRANT_NOT_RECORDED",
            "because": (f"a board row names {ref} and this store has never been "
                        f"told about it. That is a gap in the board's producer, "
                        f"not a delegation that was withdrawn"),
            "in_envelope": False,
        }
        rows.append(no_unsourced(row, ROW_PRODUCED, f"the row for {ref!r}"))

    by_state: dict[str, int] = {}
    for row in rows:
        by_state[row["state"]] = by_state.get(row["state"], 0) + 1
    return {
        "schema": SCHEMA,
        "now": now,
        "rows": rows,
        "envelopes": envelopes,
        "summary": {"recorded": len(grants), "authorizing": len(envelopes),
                    "states": by_state},
    }
