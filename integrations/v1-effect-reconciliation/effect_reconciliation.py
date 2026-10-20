#!/usr/bin/env python3
"""#245 / S2 — the RECONCILIATION DRIVER for an EFFECT_UNKNOWN dispatch.

WHAT IS MISSING WITHOUT THIS FILE
---------------------------------
`runtime/dispatch_attempts.py` owns the DECISION (what an arriving intent may
do, and what an owner observation authorizes). `runtime/agent_control_store.py
#dispatch_attempts` owns the DURABLE RECORD. Nothing in between ASKS the party
that owns the effect, after a timeout or a reconnect, and then records the
answer. That is this file, and it is deliberately the smallest thing that can
do it.

WHAT THIS FILE DOES NOT OWN
---------------------------
No store, no queue, no second ledger, no send path. Every durable read and
write goes through the existing Agent Control store — the owner #245 names —
and every decision is delegated to `dispatch_attempts.reconcile_observation`.
`integration/components.yaml` refuses `new-lease-system` and
`second-source-of-truth` by name; a second attempt store would be both. This
module therefore holds no state of its own and cannot dispatch anything: it can
only turn a recorded EFFECT_UNKNOWN into a recorded SETTLED / CALL_NOT_SENT, or
leave it exactly where it was.

THE PROVIDER CAPABILITY DISTINCTION
-----------------------------------
After a lost response, we still hold the CLIENT-side turn id we sent under. We
do NOT hold the provider-native id that would have arrived in the response we
lost. So a source is usable for reconciliation only if it can be asked by the
client id:

    CLIENT_TURN_ID      the provider can be asked by the id we still hold
    RESPONSE_ID_ONLY    the provider can only be asked by the id we lost
    NO_SOURCE           there is no read path at all

`RESPONSE_ID_ONLY` is `BLOCKED_PROVIDER_SOURCE`, not an observation. A lookup
miss is not an absence: `TURN_NOT_FOUND`, `ABSENT`, `MISSING` and their
siblings are normalised to `UNKNOWN`, which settles nothing and keeps the retry
refused. `NOT_APPLIED` is accepted only when the observation names its source
and can be tied to THIS intent — the kernel enforces both, and this driver does
not relax either.

NOT CLAIMED
-----------
An attempt reconciled with an authoritative APPLIED observation is SETTLED; an
attempt whose owner proves NOT_APPLIED is CALL_NOT_SENT and safe to retry.
Where the provider cannot prove either state, the honest outcome is
EFFECT_UNKNOWN and exactly-once is never claimed.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

#: The source can be asked by the client turn id we still hold after a lost
#: response. This is the ONLY capability that makes reconciliation meaningful.
CLIENT_TURN_ID = "CLIENT_TURN_ID"
#: The source needs a provider-native id that arrived only in the lost response.
RESPONSE_ID_ONLY = "RESPONSE_ID_ONLY"
#: There is no read path at all.
NO_SOURCE = "NO_SOURCE"

#: A source answering any of these has established a LOOKUP MISS, not an
#: absence. Normalised to UNKNOWN before the kernel sees it, so the effect stays
#: fenced even if an adapter is careless about its own words.
LOOKUP_MISS_WORDS = ("ABSENT", "NOT_FOUND", "MISSING", "TURN_NOT_FOUND",
                     "LOOKUP_MISS")

BLOCKED_PROVIDER_SOURCE = "BLOCKED_PROVIDER_SOURCE"
NO_ATTEMPT = "NO_ATTEMPT"
NOT_UNKNOWN = "NOT_UNKNOWN"
NO_OBSERVATION = "NO_OBSERVATION"
SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"


def _kernel():
    """The decision module, loaded the way every sibling loader loads it.

    Loaded by path rather than imported as a package because `runtime/` is not
    a Python package in this repository, and because reusing the module already
    in `sys.modules` keeps this driver on the SAME rules the router uses.
    """
    name = "serveros_runtime_dispatch_attempts"
    if name in sys.modules:
        return sys.modules[name]
    path = Path(__file__).resolve().parents[2] / "runtime" / "dispatch_attempts.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def source_capability(source) -> str:
    """What a source can be asked by, declared by the source itself.

    A source that does not declare a capability is `NO_SOURCE`: the driver will
    not infer one from the presence of an `observe` method, because an object
    that can be called is not the same thing as one that can answer after the
    response was lost.
    """
    if source is None:
        return NO_SOURCE
    capability = str(getattr(source, "capability", "") or "").strip().upper()
    if capability in (CLIENT_TURN_ID, RESPONSE_ID_ONLY, NO_SOURCE):
        return capability
    return NO_SOURCE


def normalize_observation(observation) -> dict:
    """A copy of the source's answer, with lookup misses demoted to UNKNOWN.

    The demotion is the safety property, not a style choice. `SETTLE_NOT_APPLIED`
    sets the phase to CALL_NOT_SENT, which makes the next retry PROCEED; a
    provider that merely failed to FIND a turn has not proved that the effect
    was never applied, and freeing the scope for it would be the blind retry
    this whole line exists to prevent.
    """
    if not isinstance(observation, dict):
        return {"state": "UNKNOWN"}
    out = dict(observation)
    state = str(out.get("state") or "").strip().upper()
    if state in LOOKUP_MISS_WORDS:
        out["state"] = "UNKNOWN"
        out["lookup_miss"] = True
    return out


class EffectReconciler:
    """Ask the owner what happened, then record the answer in the existing store.

    Constructed with an `attempts` handle — the existing Agent Control store
    satisfies `get_dispatch_attempt`, `reconcile_dispatch_attempt`,
    `dispatch_attempts` — and an optional observation source. There is no
    `send` method and no transport: a reconciler that could dispatch is a
    reconciler that can blind-retry, which is the failure it exists to stop.
    """

    def __init__(self, *, attempts, observation_source=None, now=None) -> None:
        self._attempts = attempts
        self._source = observation_source
        self._now = now

    # -- the decision the router uses on a retry ---------------------------

    def retry_verdict(self, key: str, *, digest: str) -> dict:
        """What a retry of this key may do, read from the durable record.

        Pure read. Delegates to `dispatch_attempts.reconcile`, so the driver
        cannot grow a second opinion about CALL_NOT_SENT / EFFECT_UNKNOWN /
        SETTLED — the kernel is the one place that answer lives.
        """
        recorded = self._attempts.get_dispatch_attempt(str(key))
        return _kernel().reconcile(recorded, digest=digest)

    # -- one attempt -------------------------------------------------------

    def reconcile(self, key: str, *, principal, writer=None,
                  current_writer=None) -> dict:
        """Reconcile ONE recorded attempt. Never dispatches anything.

        The order is the order the answers matter in: is there an attempt, is
        it unknown, can the source be asked by the id we still hold, and only
        then what did it say. A phase other than EFFECT_UNKNOWN is not offered
        to the source at all, so a late reply cannot overwrite a settled,
        in-flight or superseded attempt.
        """
        key = str(key)
        kernel = _kernel()
        recorded = self._attempts.get_dispatch_attempt(key)
        if recorded is None:
            return self._result(key, NO_ATTEMPT, None,
                                because=("no attempt is recorded under this "
                                         "key, so there is no unknown outcome "
                                         "to reconcile"))
        phase = str(recorded.get("phase") or "").strip().upper()
        if phase != kernel.EFFECT_UNKNOWN:
            return self._result(
                key, NOT_UNKNOWN, phase, attempt=recorded,
                because=(f"this attempt is {phase or 'unphased'}, not "
                         f"EFFECT_UNKNOWN. A late reply does not overwrite an "
                         f"attempt that already has its own answer"))

        capability = source_capability(self._source)
        if self._source is None or capability != CLIENT_TURN_ID:
            return self._result(
                key, BLOCKED_PROVIDER_SOURCE, phase, attempt=recorded,
                source_capability=capability,
                because=(f"the observation source is {capability}, so it "
                         f"cannot be asked by the client turn id this attempt "
                         f"still holds. An answer keyed by the lost response "
                         f"id would be a guess, and the effect stays "
                         f"EFFECT_UNKNOWN"))

        try:
            observation = self._source.observe(recorded, key=key)
        except Exception as exc:  # the source failing is not an outcome
            return self._result(
                key, SOURCE_UNAVAILABLE, phase, attempt=recorded,
                source_capability=capability, source_called=True,
                because=(f"the observation source failed "
                         f"({type(exc).__name__}: {exc}); an unanswered "
                         f"question is not an answer, so the effect stays "
                         f"EFFECT_UNKNOWN"))
        if observation is None:
            return self._result(
                key, NO_OBSERVATION, phase, attempt=recorded,
                source_capability=capability, source_called=True,
                because=("the source returned no observation; silence is not "
                         "evidence that the effect was or was not applied"))

        normalized = normalize_observation(observation)
        reconciler = {"principal": principal}
        reconciler.update(dict(writer or {}))
        verdict = self._attempts.reconcile_dispatch_attempt(
            key, observation=normalized, reconciler=reconciler,
            current_writer=current_writer, now=self._now)
        updated = verdict.get("attempt") or self._attempts.get_dispatch_attempt(key)
        return {
            "key": key,
            "status": verdict["verdict"],
            "verdict": verdict["verdict"],
            "phase": (updated or {}).get("phase"),
            "attempt": updated,
            "observation": normalized,
            "source_called": True,
            "source_capability": capability,
            "because": verdict.get("because"),
        }

    # -- a reconnect -------------------------------------------------------

    def reconcile_unknown(self, *, principal, writer=None, current_writer=None,
                          work_item=None) -> dict:
        """Reconcile every EFFECT_UNKNOWN attempt, in key order. Never replays.

        This is the reconnect sweep #237 T12 asks for: a control plane that was
        gone comes back and asks the owner about what it could not see. It
        never re-dispatches, and an attempt whose owner cannot answer stays
        EFFECT_UNKNOWN — which keeps the retry door closed.
        """
        kernel = _kernel()
        records = self._attempts.dispatch_attempts()
        results = []
        for key in sorted(records):
            recorded = records[key]
            if str(recorded.get("phase") or "").strip().upper() != kernel.EFFECT_UNKNOWN:
                continue
            if work_item is not None and recorded.get("work_item") != work_item:
                continue
            results.append(self.reconcile(key, principal=principal, writer=writer,
                                          current_writer=current_writer))
        return {
            "results": results,
            "settled": sum(1 for r in results
                           if r["status"] in (kernel.SETTLE_APPLIED,
                                              kernel.SETTLE_NOT_APPLIED)),
            "unknown": sum(1 for r in results
                           if r["status"] == kernel.REFUSE_UNKNOWN),
            "source_capability": source_capability(self._source),
        }

    @staticmethod
    def _result(key, status, phase, *, attempt=None, source_called=False,
                source_capability=None, because=None) -> dict:
        return {
            "key": key,
            "status": status,
            "verdict": None,
            "phase": phase,
            "attempt": attempt,
            "observation": None,
            "source_called": bool(source_called),
            "source_capability": source_capability,
            "because": because,
        }


def tree_shape() -> dict:
    """What this driver declares, for a contract reader."""
    return {
        "capabilities": [CLIENT_TURN_ID, RESPONSE_ID_ONLY, NO_SOURCE],
        "blocked": BLOCKED_PROVIDER_SOURCE,
        "lookup_misses": list(LOOKUP_MISS_WORDS),
        "owner": "runtime/agent_control_store.py#dispatch_attempts",
        "decision": "runtime/dispatch_attempts.py#reconcile_observation",
        "not_claimed": ("the driver cannot verify that a source is truthful; it "
                        "enforces provenance and capability, and an unproven "
                        "outcome stays EFFECT_UNKNOWN"),
    }
