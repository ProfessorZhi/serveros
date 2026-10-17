#!/usr/bin/env python3
"""#245 / S2 — the dispatch attempt, and what its outcome authorizes next.

THE FAILURE THIS EXISTS FOR
---------------------------
`fleet/agent_control_router.py#_replay` guards a retried mutation, and it says
of itself (in `fleet/agent-control-contract.yaml#idempotency.durability`) that it
is in-process, per router instance. Two holes follow, and #245 names both:

  * it compares the `action` and the `device_ref` and NOT the payload, so a
    retry that kept the key but changed the instruction was answered with the
    first call's result — a second intent reported as a replay of the first
    (#237 T5);
  * it is a dict in one process, so a restart forgets every attempt, and a retry
    after a lost response becomes a SECOND effect — with no record of whether
    the first one happened (#237 T4/T9).

THE ONE THING THIS MODULE OWNS
------------------------------
Not a store, and not a queue. It owns the DECISION: given what was RECORDED
about an attempt, what may an arriving intent do? That is a pure function
(`reconcile`), plus the shape of the record it reads (`attempt_record`) and the
identity of an intent (`intent_digest`). The record is durable because it lives
in the EXISTING Agent Control store (`agent_control_store.py#dispatch_attempts`),
which #245 names as the owner — this module never opens a file, a socket or a
database of its own. `integration/components.yaml` refuses `new-lease-system`
and the same rule applies to a second attempt store: refused by name.

WHY THE PHASES ARE FIVE, AND NOT A BOOLEAN
------------------------------------------
`idempotency_key` alone answers "is this the same intent". It cannot answer "did
it happen", and those are different questions:

    CALL_NOT_SENT     recorded before the send, and the send never started —
                      safe to proceed. A record written BEFORE dispatch is the
                      only way "we crashed before sending" is distinguishable
                      from "we crashed after sending and do not know".
    ACCEPTED          the provider took it; the turn is in flight. A retry must
                      NOT dispatch again — the first one is still running.
    EFFECT_UNKNOWN    the send went out and the answer was lost. This is the
                      state the whole module exists for: a retry must NEVER
                      blind-retry, and the honest answer is to reconcile with
                      the owner, not to try again.
    SETTLED           a definitive answer was recorded. A retry is a REPLAY of
                      that answer, and the effect is not produced twice.
    SUPERSEDED        a newer decision replaced this attempt; a late retry of it
                      may not act. (The consumer in `#237`'s design: a newer
                      input or owner epoch replaces an older request, and the
                      late old response stays historical.)

A durable idempotency key is an ATTEMPT identity, and it is not proof that the
remote effect happened exactly once. Where a provider cannot prove it, the only
honest outcome is EFFECT_UNKNOWN plus reconciliation — never "retry and hope".
"""
from __future__ import annotations

import hashlib
import json

SCHEMA = "serveros/dispatch-attempt.v1"

# The five phases. Closed, and each names a different thing a retry may do:
# see the module docstring. A sixth would need a sixth answer, and there is no
# sixth answer that is not one of these five said less precisely.
CALL_NOT_SENT = "CALL_NOT_SENT"
ACCEPTED = "ACCEPTED"
EFFECT_UNKNOWN = "EFFECT_UNKNOWN"
SETTLED = "SETTLED"
SUPERSEDED = "SUPERSEDED"
PHASES = (CALL_NOT_SENT, ACCEPTED, EFFECT_UNKNOWN, SETTLED, SUPERSEDED)

# The verdicts `reconcile` returns. They are what a caller branches on, and
# they are deliberately finer than a boolean: "do not send" is one answer with
# several reasons, and a caller told only "no" cannot tell a settled replay
# (answer from the record) from an unknown outcome (go and reconcile).
PROCEED = "PROCEED"
REPLAY = "REPLAY"
CONFLICT = "CONFLICT"
IN_FLIGHT = "IN_FLIGHT"
REFUSE_UNKNOWN = "REFUSE_UNKNOWN"
REFUSE_SUPERSEDED = "REFUSE_SUPERSEDED"
VERDICTS = (PROCEED, REPLAY, CONFLICT, IN_FLIGHT, REFUSE_UNKNOWN,
            REFUSE_SUPERSEDED)


class AttemptRefused(ValueError):
    """An attempt record that cannot be built, or a phase nobody declared."""


def intent_digest(*, action, device_ref, verb, params=None, workspace=None,
                  work_item=None) -> str:
    """The identity of an INTENT — the payload, not just the key.

    Canonical and order-insensitive (`sort_keys`), so two spellings of the same
    payload are the same intent and a changed VALUE is a different one. This is
    what makes "same key, different payload" a CONFLICT rather than a replay,
    and it is the half `_replay` never had: it compared `action` and
    `device_ref`, which two different sends on one session share.

    `context_ref` is deliberately NOT part of it. The trusted context is who is
    asking, not what is being asked, and a retry that re-presents a refreshed
    context for the SAME intent must not become a conflict.
    """
    canonical = json.dumps(
        {"action": str(action), "device_ref": str(device_ref), "verb": str(verb),
         "params": dict(params or {}), "workspace": workspace,
         "work_item": work_item},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def attempt_record(*, key, action, device_ref, verb, intent_digest, phase,
                   work_item=None, principal=None, runtime=None, grant_ref=None,
                   provider_locator=None, result=None, because=None,
                   at=None) -> dict:
    """One durable dispatch attempt. Raises `AttemptRefused` on a bad phase.

    The fields are the ones #245 lists — work/item identity, the intent digest,
    the device/principal/runtime, the grant reference and, when the provider has
    one, the provider-native locator — plus the phase that says what the record
    currently means. A field nobody has yet is simply absent: an unknown
    locator is not an empty string, because "we have not been told" and "we were
    told it is nothing" are different, and the second is a claim.

    NOTE the direction of `grant_ref`: it is a REFERENCE to a grant that lives
    in its own authority, never a copy of the grant. `#245` says to keep grants
    and Work state in their existing owner, and a record that copied a scope or
    an expiry would be a second answer to whether the caller is still allowed.
    """
    phase = str(phase or "").strip().upper()
    if phase not in PHASES:
        raise AttemptRefused(
            f"{phase!r} is not a dispatch phase; one of {list(PHASES)}. A "
            f"boolean 'sent' cannot say which of five things a retry may do")
    if not str(key or "").strip():
        raise AttemptRefused(
            "a dispatch attempt is identified by the key it was dispatched "
            "under; without one there is nothing a retry can be matched to")
    if not str(intent_digest or "").strip():
        raise AttemptRefused(
            "a dispatch attempt carries the digest of the intent it was for; "
            "without it a retry under the same key cannot be told from a "
            "different intent")
    record = {
        "schema": SCHEMA,
        "key": str(key),
        "action": str(action),
        "device_ref": str(device_ref),
        "verb": str(verb),
        "intent_digest": str(intent_digest),
        "phase": phase,
    }
    for field, value in (("work_item", work_item), ("principal", principal),
                         ("runtime", runtime), ("grant_ref", grant_ref),
                         ("provider_locator", provider_locator),
                         ("because", because)):
        if value:
            record[field] = str(value)
    if result is not None:
        record["result"] = result
    if at:
        record["recorded_at"] = str(at)
    return record


def reconcile(recorded, *, digest) -> dict:
    """What an arriving intent may do, from what was RECORDED. Pure.

    `recorded` is the stored attempt for this key, or None. `digest` is the
    arriving intent's own digest. The order of the checks is the order the
    answers matter in: WHICH intent this is comes first, because every answer
    below is about that intent and answering "in flight" about a different
    payload would describe somebody else's call.
    """
    if recorded is None:
        return {"verdict": PROCEED,
                "because": ("nothing is recorded under this key, so this is the "
                            "first dispatch of this intent")}
    if str(recorded.get("intent_digest") or "") != str(digest):
        # #237 T5. The key was reused for a different intent. Answering with
        # the other call's result would report an effect that never happened
        # for this request, so this is refused rather than replayed.
        return {"verdict": CONFLICT,
                "because": ("this key was already used for a different intent "
                            "(a different payload). A key identifies one "
                            "intent, not one caller")}
    phase = str(recorded.get("phase") or "").strip().upper()
    if phase == SETTLED:
        return {"verdict": REPLAY, "result": recorded.get("result"),
                "because": "this intent settled; answer from the record"}
    if phase == EFFECT_UNKNOWN:
        return {"verdict": REFUSE_UNKNOWN,
                "because": ("this intent was dispatched and its outcome is not "
                            "known. Retrying it would be a blind second effect; "
                            "reconcile with the provider/owner and record what "
                            "it says before dispatching anything")}
    if phase == ACCEPTED:
        return {"verdict": IN_FLIGHT,
                "because": ("this intent is accepted and still in flight; a "
                            "second dispatch is a second effect")}
    if phase == SUPERSEDED:
        return {"verdict": REFUSE_SUPERSEDED,
                "because": ("this intent was superseded by a newer decision; a "
                            "late retry of it may not act")}
    if phase == CALL_NOT_SENT:
        return {"verdict": PROCEED,
                "because": ("the send never started, so proceeding is safe and "
                            "does not risk a second effect")}
    return {"verdict": REFUSE_UNKNOWN,
            "because": (f"{phase!r} is not a phase this kernel knows, and an "
                        f"unreadable outcome is not permission to dispatch")}


#: The transport reasons that PROVE nothing reached the provider. A transport
#: that could not deliver has learned nothing about the session on the far side,
#: so for every OTHER reason the attempt stays ambiguous — and ambiguity is
#: EFFECT_UNKNOWN, which refuses a retry. This list is deliberately SHORT and
#: conservative: a reason wrongly placed here would let a possibly-sent effect be
#: re-sent, and a reason wrongly left out only makes a retry refuse (and ask for
#: reconciliation), which is the recoverable direction.
NEVER_REACHED_THE_PROVIDER = ("DEVICE_UNREACHABLE", "NODE_RUNTIME_UNAVAILABLE")


def phase_after_transport_failure(reason) -> str:
    """Which phase a failed dispatch leaves the attempt in. Pure.

    `DEVICE_UNREACHABLE` / `NODE_RUNTIME_UNAVAILABLE` mean the request never left
    the control plane, so the attempt is still `CALL_NOT_SENT` and a retry is
    safe. ANY other failure — a timeout, a provider error, a turn that failed —
    leaves us unable to say whether the effect happened, and the honest phase is
    `EFFECT_UNKNOWN`: the retry must not launch a second turn, and the answer is
    to reconcile with the owner rather than to try again.
    """
    if str(reason or "").strip().upper() in NEVER_REACHED_THE_PROVIDER:
        return CALL_NOT_SENT
    return EFFECT_UNKNOWN


# ---------------------------------------------------------------------------
# OBSERVATIONAL RECONCILIATION — turning EFFECT_UNKNOWN into a decision.
#
# EFFECT_UNKNOWN means "we sent it and did not see the answer". It is resolved
# only by an INDEPENDENT observation from the party that owns the effect (the
# provider or its owner), and never by re-sending. The observation is
# three-valued on purpose:
#
#   APPLIED       the effect is there — settle the attempt
#   NOT_APPLIED   the effect is provably NOT there — the scope is free again
#   UNKNOWN       the observation decided nothing, which is not the same as
#                 either of the above and leaves the attempt where it was
#
# A reconciler is not whoever happens to call: it must be the principal that
# dispatched the attempt AND the current writer of its write scope, because
# reconciliation is a protected write. A stale writer and a different principal
# are refused in their own words — collapsing either into "unknown" would hide
# an authorization failure inside a data-quality one.
# ---------------------------------------------------------------------------

OBSERVED_APPLIED = "APPLIED"
OBSERVED_NOT_APPLIED = "NOT_APPLIED"
#: The words an owner may use for the two definite answers. Kept slightly wider
#: than our own spelling because the observer is another subsystem describing
#: its own state, and a synonym is not a different fact — while an unrecognised
#: word IS undecidable, and falls through to UNKNOWN below.
_APPLIED_WORDS = (OBSERVED_APPLIED, "COMPLETED", "PRESENT", "DONE")
_NOT_APPLIED_WORDS = (OBSERVED_NOT_APPLIED, "ABSENT", "NOT_FOUND", "MISSING")

SETTLE_APPLIED = "SETTLE_APPLIED"
SETTLE_NOT_APPLIED = "SETTLE_NOT_APPLIED"
REFUSE_NOT_UNKNOWN = "REFUSE_NOT_UNKNOWN"
REFUSE_PRINCIPAL = "REFUSE_PRINCIPAL"
REFUSE_STALE_WRITER = "REFUSE_STALE_WRITER"
REFUSE_UNKNOWN = "REFUSE_UNKNOWN"
OBSERVATION_VERDICTS = (SETTLE_APPLIED, SETTLE_NOT_APPLIED, REFUSE_NOT_UNKNOWN,
                        REFUSE_PRINCIPAL, REFUSE_STALE_WRITER, REFUSE_UNKNOWN)


def reconcile_observation(recorded, *, observation, reconciler,
                          current_writer=None, now=None) -> dict:
    """What an owner's observation authorizes for an EFFECT_UNKNOWN attempt.

    PURE. The checks run in the order the answers matter in: whether there is
    anything to reconcile, whether this caller may, whether it still holds the
    write scope, whether the observation is even about THIS intent, and only
    then what the observation says.

    Fail closed at every step. An observation that cannot be tied to this
    attempt, from a caller that cannot be authorized, settles nothing — and the
    attempt stays EFFECT_UNKNOWN, which is the state that refuses a retry.
    """
    if recorded is None:
        return {"verdict": REFUSE_UNKNOWN,
                "because": ("no attempt is recorded under this key, so there is "
                            "no unknown outcome to reconcile")}
    phase = str(recorded.get("phase") or "").strip().upper()
    if phase != EFFECT_UNKNOWN:
        return {"verdict": REFUSE_NOT_UNKNOWN,
                "because": (f"this attempt is {phase or 'unphased'}, not "
                            f"EFFECT_UNKNOWN. Only an outcome nobody knows is "
                            f"reconciled; a settled, in-flight or superseded "
                            f"attempt has its own answer — and a LATE reply to "
                            f"one of those must not overwrite it")}

    reconciler = dict(reconciler or {})
    recorded_principal = recorded.get("principal")
    if recorded_principal and str(recorded_principal) != str(reconciler.get("principal")):
        return {"verdict": REFUSE_PRINCIPAL,
                "because": (f"this attempt was dispatched by {recorded_principal!r} "
                            f"and {reconciler.get('principal')!r} is not that "
                            f"principal. An observation from a different principal "
                            f"is not evidence about this effect")}

    # Reconciliation is a protected write, so it needs the writer gate. A scope
    # with no Item has no write authority to hold (a coordinator send), so the
    # gate does not apply to it; every scope WITH an Item does.
    if recorded.get("work_item"):
        if current_writer is None:
            return {"verdict": REFUSE_UNKNOWN,
                    "because": ("no current-writer record was available, so the "
                                "caller cannot be shown to still hold the write "
                                "scope. Reconciliation is a write, and a write "
                                "nobody could authorize is refused")}
        if not _same_writer(reconciler, current_writer):
            return {"verdict": REFUSE_STALE_WRITER,
                    "because": (f"the current writer of {recorded.get('work_item')!r} "
                                f"is {current_writer.get('owner')!r} at epoch "
                                f"{current_writer.get('epoch')!r} token "
                                f"{current_writer.get('token')!r}; this caller "
                                f"presents {reconciler.get('owner')!r} at epoch "
                                f"{reconciler.get('epoch')!r} token "
                                f"{reconciler.get('token')!r}. A superseded writer "
                                f"does not get to settle the successor's scope")}

    # The observation must be about THIS intent. The digest proves it directly;
    # a matching provider-native locator proves it when the observer knows the
    # locator and not our digest. Neither present (or neither matching) means
    # the observation could be about any call at all.
    observation = dict(observation or {})
    observed_digest = str(observation.get("intent_digest") or "")
    observed_locator = str(observation.get("provider_locator") or "")
    recorded_locator = str(recorded.get("provider_locator") or "")
    same_intent = (observed_digest and
                   observed_digest == str(recorded.get("intent_digest") or ""))
    same_locator = (observed_locator and recorded_locator
                    and observed_locator == recorded_locator)
    if not (same_intent or same_locator):
        return {"verdict": REFUSE_UNKNOWN,
                "because": ("the observation cannot be tied to THIS intent — its "
                            "digest and its provider locator both differ from the "
                            "recorded attempt's. An observation about another call "
                            "proves nothing about this one")}

    state = str(observation.get("state") or "").strip().upper()
    if state in _APPLIED_WORDS:
        return {"verdict": SETTLE_APPLIED, "phase": SETTLED,
                "result": observation.get("result"),
                "because": "the owner observes the effect applied; the attempt settles"}
    if state in _NOT_APPLIED_WORDS:
        return {"verdict": SETTLE_NOT_APPLIED, "phase": CALL_NOT_SENT,
                "because": ("the owner observes the effect absent, so the scope is "
                            "free and a retry is safe")}
    return {"verdict": REFUSE_UNKNOWN,
            "because": (f"the observation says {state!r}, which decides nothing. An "
                        f"effect nobody proved is left EFFECT_UNKNOWN, which is "
                        f"what refuses a retry")}


def _same_writer(reconciler, current_writer) -> bool:
    """The reconciler IS the current writer — owner, epoch and token alike."""
    current_writer = current_writer or {}
    return (str(reconciler.get("owner")) == str(current_writer.get("owner"))
            and str(reconciler.get("epoch")) == str(current_writer.get("epoch"))
            and str(reconciler.get("token")) == str(current_writer.get("token")))


def tree_shape() -> dict:
    """What this module declares, for a caller that reads contracts."""
    return {
        "schema": SCHEMA,
        "phases": list(PHASES),
        "verdicts": list(VERDICTS),
        "identity": "intent_digest(action, device_ref, verb, params, workspace, work_item)",
        "reconciliation": "reconcile_observation(recorded, observation, reconciler, current_writer)",
        "observation_verdicts": list(OBSERVATION_VERDICTS),
        "observed_states": list(_APPLIED_WORDS) + list(_NOT_APPLIED_WORDS) + ["UNKNOWN"],
        "owner": "runtime/agent_control_store.py#dispatch_attempts",
        "not_claimed": ("a durable key is an attempt identity, not proof the "
                        "remote effect happened exactly once; an unknown outcome "
                        "is EFFECT_UNKNOWN and is never blind-retried"),
    }
