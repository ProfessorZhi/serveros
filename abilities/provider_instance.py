#!/usr/bin/env python3
"""Build the provider instance record — the one the contract declared and
nothing produced.

Why this file exists
--------------------
`abilities/provider-contract.yaml` has declared an `instance:` record since it
was written:

    instance:
      required_fields: [provider, device_ref, observed_at, status]

A repo-wide search for a writer finds none. The shape exists in two test
fixtures and in `resolver.py`, which reads `request["instances"]` and looks only
at whether an entry is present — it never reads `status` or `observed_at`. So
`observed_at` was declared required by a record that no code ever wrote, which
is the same defect as an unread rule with a timestamp on it.

What it is NOT
--------------
It is not a second health system. `runtime/health.py` already owns the
observation shape — `Observation` with an enforced `observed_by {type, id}`, an
`observed_at`, an evidence type whose strength is declared in `EVIDENCE_STRENGTH`,
and a `reason` from a closed list. This module owns nothing of that. It imports
`Observation`, builds the axes, and returns one status in the vocabulary
`abilities/provider-contract.yaml` already declares. There is no new store, no
new file on disk, and no new status word.

The separation this exists to make
----------------------------------
The vertical slice that forced this found a provider that was reachable in every
sense ServerOS could express:

    /api/sessions   listed
    /api/bots       listed
    /api/trigger    accepted, with a real sessionId
    action_support  session.create = SUPPORTED, session.send = SUPPORTED

and every turn it accepted failed, because the execution backend underneath it
was gone. Nothing in ServerOS could say so, because there was only one axis and
the provider passed it. "It answers" and "it works" are different questions, and
a system that can only ask the first will keep reporting the second.

The 2026-09-12 endpoint in `runtime/health.py`'s docstring is the same shape one
plane over, which is why the axes below are that module's own words and not new
ones:

    liveness    -> control-plane-reachability   does it answer at all
    functional  -> execution-readiness          can it do the thing it accepted

Why persistence is `none`
-------------------------
Readiness derived from a stored value is a memory of a past resolution read as a
current one, and a stored NEGATIVE is worse than a stored positive: it keeps
refusing a provider that has since been repaired. The freshness window below
ages an observation out and falls back to UNKNOWN, never to AVAILABLE — ageing a
negative into a positive is the one way this could start lying.
"""
from __future__ import annotations

import datetime
import importlib.util
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_RUNTIME = _HERE.parent / "runtime"

SCHEMA = "serveros/provider-instance.v1"

# The axes, named as the questions rather than as the managed-runtime fields, so
# that a reader of a readiness record does not have to know that `functional` is
# what `runtime/health.py` calls "can it serve a request it claims to serve".
AXIS_CONTROL_PLANE = "control-plane-reachability"
AXIS_EXECUTION = "execution-readiness"

# Which managed-runtime axis each of ours is observed on. Kept as a mapping
# rather than copied value lists, so there is exactly one place where
# RUNNING/STOPPED/UNKNOWN/UNAVAILABLE and OK/FAILED/NOT_APPLICABLE/UNKNOWN live.
MANAGED_AXIS = {AXIS_CONTROL_PLANE: "liveness", AXIS_EXECUTION: "functional"}

# One status vocabulary, from the provider contract. Asserted against the file
# by test, because two lists of status values is a defect this round has already
# found four times.
STATUSES = ("AVAILABLE", "DEGRADED", "UNAVAILABLE", "INCOMPATIBLE", "UNKNOWN")

# The provider contract's own reason tokens, same discipline.
REASONS = ("AGENT_UNSUPPORTED", "DEVICE_DEPENDENCY_MISSING", "RUNTIME_UNHEALTHY",
           "POLICY_DENIED", "NETWORK_UNAVAILABLE", "PROVIDER_NOT_INSTALLED",
           "EXECUTION_UNVERIFIED", "EXECUTION_FAILED")

# The default, and the contract's number. Read from the file where a caller has
# not overridden it; the constant exists so a test can assert they agree.
DEFAULT_FRESHNESS_SECONDS = 900

CONTRACT = _HERE / "provider-contract.yaml"
_SESSION_CONTRACT = _RUNTIME / "agent-session-contract.yaml"


class ReadinessError(RuntimeError):
    """The record is malformed. Never a readiness verdict."""


def _load_health():
    name = "serveros_runtime_health"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _RUNTIME / "health.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


health = _load_health()
Observation = health.Observation


def _contract() -> dict:
    import yaml
    data = yaml.safe_load(CONTRACT.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ReadinessError(f"{CONTRACT} must contain a mapping")
    return data


def freshness_seconds() -> int:
    block = (_contract().get("instance") or {}).get("readiness") or {}
    return int((block.get("freshness") or {}).get("window_seconds")
               or DEFAULT_FRESHNESS_SECONDS)


# ---------------------------------------------------------------------------
# Building observations
# ---------------------------------------------------------------------------

def now_utc(clock=None) -> str:
    moment = clock() if clock else datetime.datetime.now(datetime.timezone.utc)
    return moment.astimezone(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def observation(*, axis: str, state: str, observer_type: str, observer_id: str,
                observed_at: str, evidence_type: str, reason: str | None = None,
                extra: dict | None = None) -> "Observation":
    """One axis, in `runtime/health.py`'s own record.

    Constructed through the dataclass rather than assembled as a dict so that
    that module's checks apply here unchanged: an observation with no
    `observed_by` is refused, a reason outside its closed list is refused, and a
    non-UNKNOWN state carried by `journal-diagnostic` or `none` is refused. A
    readiness value that skips those is a readiness value with nothing behind
    it, which is the failure this whole round is about.
    """
    if axis not in MANAGED_AXIS:
        raise ReadinessError(f"unknown readiness axis {axis!r}; known={sorted(MANAGED_AXIS)}")
    evidence = {"type": evidence_type}
    evidence.update(extra or {})
    return Observation(
        axis=MANAGED_AXIS[axis],
        state=state,
        observed_by={"type": observer_type, "id": observer_id},
        observed_at=observed_at,
        evidence=evidence,
        reason=reason,
    )


def unknown_execution(*, observed_at: str, provider_id: str) -> "Observation":
    """The honest pre-action answer for a provider with no readiness surface.

    Probed and confirmed for BotMux 3.18.14: `/api/health`, `/api/status`,
    `/api/ready`, `/api/readiness`, `/api/doctor`, `/api/version` and
    `/api/openapi.json` all answer 404. So there is no documented way to ask
    this question, and the answer is UNKNOWN — not AVAILABLE. A provider that
    cannot be asked has not been found healthy.
    """
    return observation(
        axis=AXIS_EXECUTION, state="UNKNOWN",
        observer_type="none", observer_id="no-readiness-surface",
        observed_at=observed_at, evidence_type="none",
        reason="NOT_DECLARED",
        extra={"why": "the provider publishes no readiness endpoint; probed 404",
               "provider": provider_id},
    )


# ---------------------------------------------------------------------------
# Freshness
# ---------------------------------------------------------------------------

def age_seconds(observation_record, reference: str | None) -> float | None:
    """How old is this observation, in seconds? None when it cannot be said.

    None is not zero. An observation with no `observed_at` has an unknown age,
    and an unknown age must not be treated as fresh — that is the same default a
    missing `observed_at` would get if this returned 0, and it is the wrong one.
    """
    if reference is None or not getattr(observation_record, "observed_at", None):
        return None
    try:
        then = datetime.datetime.fromisoformat(
            str(observation_record.observed_at).replace("Z", "+00:00"))
        now = datetime.datetime.fromisoformat(str(reference).replace("Z", "+00:00"))
    except ValueError:
        return None
    if then.tzinfo is None or now.tzinfo is None:
        return None
    return (now - then).total_seconds()


def is_stale(observation_record, reference: str | None, window: int | None = None) -> bool:
    age = age_seconds(observation_record, reference)
    if age is None:
        # Cannot be said to be fresh. Treated as stale so that a record with no
        # clock is not silently promoted to a current verdict.
        return True
    return age > (window if window is not None else freshness_seconds()) or age < 0


# ---------------------------------------------------------------------------
# The derivation
# ---------------------------------------------------------------------------

def _by_axis(observations) -> dict:
    out: dict[str, object] = {}
    for record in observations:
        for name, managed in MANAGED_AXIS.items():
            if record.axis == managed and name not in out:
                out[name] = record
    return out


def derive_status(*, observations, reference: str | None,
                  window: int | None = None) -> tuple[str, str, str]:
    """Return (status, reason, why) for one device's view of one provider.

    Ordered, and total. `why` is an explanation for a person; `reason` is the
    contract's closed token, and they are separate fields because a status whose
    only justification is a sentence cannot be compared against another status.
    """
    axes = _by_axis(observations)
    control = axes.get(AXIS_CONTROL_PLANE)
    execution = axes.get(AXIS_EXECUTION)

    if control is None and execution is None:
        return ("UNKNOWN", "EXECUTION_UNVERIFIED",
                "nothing was observed: no control-plane answer and no execution result")

    # -- the control plane, first, because nothing else can be read without it --
    if control is not None:
        if control.state in ("STOPPED", "UNAVAILABLE"):
            return ("UNAVAILABLE", "RUNTIME_UNHEALTHY",
                    f"the provider control plane is {control.state}: "
                    f"{(control.evidence or {}).get('why') or 'it did not answer'}")
        if control.state == "UNKNOWN":
            return ("UNKNOWN", "EXECUTION_UNVERIFIED",
                    "the control plane was never observed to answer, so readiness "
                    "cannot be established from it")

    # -- now the execution question, which is the one the slice could not ask --
    if execution is None:
        return ("UNKNOWN", "EXECUTION_UNVERIFIED",
                "the control plane answers and no execution result has been observed")

    stale = is_stale(execution, reference, window)
    if execution.state == "OK":
        if stale:
            # An old success is not a current success. Falling back to AVAILABLE
            # here would make this the one mechanism that ages into optimism.
            return ("UNKNOWN", "EXECUTION_UNVERIFIED",
                    "the last observed execution succeeded and is older than the "
                    "freshness window; nothing establishes the current state")
        return ("AVAILABLE", None, "the last observed execution succeeded and is current")
    if execution.state == "FAILED":
        if stale:
            return ("UNKNOWN", "EXECUTION_UNVERIFIED",
                    "an execution failure was observed and is older than the freshness "
                    "window; the provider is not left permanently poisoned by it, and "
                    "it is not reported as working either")
        return ("UNAVAILABLE", "EXECUTION_FAILED",
                f"the provider accepted the action and it failed: "
                f"{(execution.evidence or {}).get('error_code') or 'no error code reported'}")
    return ("UNKNOWN", "EXECUTION_UNVERIFIED",
            f"execution readiness is {execution.state}, which is not an answer")


def instance(*, provider: str, device_ref: str, observations,
             reference: str | None = None, window: int | None = None) -> dict:
    """One device's readiness for one provider, with its evidence attached."""
    if not provider or not device_ref:
        raise ReadinessError("an instance names one provider on one device")
    records = list(observations)
    status, reason, why = derive_status(observations=records, reference=reference,
                                        window=window)
    stale_axes = [name for name, record in _by_axis(records).items()
                  if is_stale(record, reference, window)]
    return {
        "schema": SCHEMA,
        "provider": provider,
        "device_ref": device_ref,
        "observed_at": reference,
        "status": status,
        # A derived verdict is INFERRED even when every observation under it is a
        # FACT: the facts are the provider's, the aggregation is ours, and a
        # reader deciding whether to trust this is entitled to know which part
        # came from where. UNKNOWN gets its own provenance because "we derived
        # unknown from facts" and "we know nothing" are different records.
        "status_provenance": "UNKNOWN" if status == "UNKNOWN" else "INFERRED",
        "reason": reason,
        "reason_text": why,
        "observations": [record.show() for record in records],
        "stale_axes": stale_axes,
        "freshness_window_seconds": (window if window is not None
                                     else freshness_seconds()),
        "persistence": "none",
    }


def refusal(instance_record: dict) -> dict | None:
    """Should a call be refused before it is made? None means: make the call.

    Only a KNOWN NEGATIVE refuses. UNKNOWN does not, and that is the whole
    reason the pre-action answer can be UNKNOWN without making the provider
    undrivable — a provider with no readiness surface would otherwise be
    permanently refused by a check that learned nothing.
    """
    status = instance_record.get("status")
    if status in ("UNAVAILABLE", "INCOMPATIBLE"):
        return {"status": status, "reason": instance_record.get("reason"),
                "reason_text": instance_record.get("reason_text")}
    return None


def main(argv=None) -> int:
    import argparse
    import json
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--provider", required=True)
    parser.add_argument("--device", required=True)
    parser.add_argument("--status", choices=list(STATUSES),
                        help="an operator's observation, for rehearsal only")
    args = parser.parse_args(argv)

    observations = []
    reference = now_utc()
    if args.status:
        # Named `--status` and not measured: this CLI cannot probe anything, and
        # a flag that looks like it probed would be the defect this file was
        # written to remove. It exists so the derivation can be rehearsed.
        observations.append(unknown_execution(observed_at=reference,
                                              provider_id=args.provider))
    json.dump(instance(provider=args.provider, device_ref=args.device,
                       observations=observations, reference=reference),
              sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
