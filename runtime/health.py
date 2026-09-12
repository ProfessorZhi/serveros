#!/usr/bin/env python3
"""Four axes, never one boolean — and never a health claim without provenance.

The failure this module exists for
----------------------------------
On 2026-09-12 this host ran an endpoint that was, by every process-level
measure, fine:

    liveness   RUNNING       the PID existed for the whole 54 minutes
    transport  CONNECTED     ESTABLISHED to the relay on 443 throughout
    functional ???           it could not serve a single request

`docker ps`, `systemctl status`, `ps` and a TCP table would all have reported
something reassuring. The service was in a device-authorization wait loop
because its refresh token had been consumed, and `Restart=always` restarted it
every ~24 minutes as each successive device code expired — a restart loop that
looked, from the outside, exactly like a healthy service with occasional
hiccups.

So: **`process running = HEALTHY` is not a simplification, it is the bug.**
The four axes are separate because they fail separately, and the axis that
matters — `functional` — is the one nothing local was measuring.

Rules this module holds
-----------------------
1.  No observation without provenance. An axis with no `observed_by` cannot
    be OK; the constructor refuses to build one.
2.  UNKNOWN is never rendered as the healthy value. Not having asked is not
    the same as having found it fine.
3.  A journal line is a diagnostic, never an authority. A probe may refine a
    `reason` from journal evidence; it may not decide a state from it. The
    record carries `authoritative: false` so a reader can tell.
4.  The derived state has no fallback. Rules are ordered and total; an
    unclassified combination RAISES rather than returning the nearest word.

The last one is not defensive coding. It is the specific defect found in
`fleet/transport-contract.yaml` one layer down, where a partial table's
fallback emitted `DEVICE_UNOBSERVED` — the one word the contract forbade —
for a device that was merely offline. A wrong answer that looks like an answer
is worse than a crash.
"""
from __future__ import annotations

import importlib.util
import sys
import datetime
from dataclasses import dataclass, field
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _load(name: str, filename: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _HERE / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


registry = _load("serveros_runtime_registry", "registry.py")

RegistryError = registry.RegistryError


class HealthError(RegistryError):
    """Base class for health-model failures."""


class UnprovenancedObservation(HealthError):
    """A health claim was made without saying who made it.

    Raised at construction rather than dropped at render time, because a
    default provenance ("assumed", "self") is exactly how an unmeasured axis
    becomes indistinguishable from a measured one.
    """


class HealthUndetermined(HealthError):
    """The axis combination matches no rule.

    Deliberately fatal. See the module docstring, rule 4.
    """


# ---------------------------------------------------------------------------
# Reasons, and how strongly each is held
# ---------------------------------------------------------------------------

# `proven_by` records the strength of the evidence, not the confidence of the
# author. A reason backed only by a journal pattern is a HYPOTHESIS and says
# so in the record; the day a provider API can answer the question, the same
# reason becomes `provider-response` and nothing above this line changes.
EVIDENCE_STRENGTH = {
    # A provider or the resource itself, answering a direct question.
    "provider-response": True,
    # Something measurable on this host that is not the resource's own report:
    # a socket that carried a real request, a process that is genuinely gone.
    "host-measurement": True,
    # The image's own HEALTHCHECK, or a service manager's own verdict.
    "declared-healthcheck": True,
    # A log line. Corroborating only. Never authority.
    "journal-diagnostic": False,
    # Nothing measured it.
    "none": False,
}

REASONS = {
    "AUTH_REAUTH_REQUIRED": (
        "The resource's credential is consumed or expired and only a person "
        "can complete the flow that replaces it. Established by a provider "
        "response, not by a log line."
    ),
    "AUTH_FAILURE_SUSPECTED": (
        "The same situation as AUTH_REAUTH_REQUIRED, held on weaker evidence. "
        "Used while no provider probe can prove it. Carries `authoritative: "
        "false` so a reader does not treat a hypothesis as a finding."
    ),
    "PROCESS_DOWN": "The resource is not running, and its desired state is running.",
    "CHANNEL_DISCONNECTED": (
        "The process is up and the channel it depends on is not carrying "
        "anything. Different from AUTH_REAUTH_REQUIRED: a network fault looks "
        "the same from here and calls for a different response."
    ),
    "HEALTHCHECK_FAILING": "The resource's own declared healthcheck reports failure.",
    "NOT_OBSERVED": (
        "A probe was declared for this axis and produced no answer. We asked "
        "and got nothing, which is a different fact from never having asked."
    ),
    "NOT_DECLARED": (
        "No probe answers this axis for this resource. Not a fault and not "
        "reassurance: the axis is simply outside what this resource declares "
        "it can be asked."
    ),
    "NOT_OBSERVABLE": "Nothing on this node can observe this resource at all.",
    "DECLARED_SURFACE_ABSENT": (
        "The resource declares a callable surface and the probe that would "
        "exercise it cannot run here. Reported instead of a fabricated OK."
    ),
}


# ---------------------------------------------------------------------------
# One axis
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Observation:
    """One axis of one resource, at one moment, with its evidence."""

    axis: str
    state: str
    observed_by: dict
    observed_at: str | None
    evidence: dict = field(default_factory=dict)
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.state not in registry._axis_values(self.axis):
            raise HealthError(
                f"{self.axis}: {self.state!r} is not one of "
                f"{list(registry._axis_values(self.axis))}"
            )
        if self.reason is not None and self.reason not in REASONS:
            raise HealthError(
                f"{self.axis}: unknown reason {self.reason!r}. Add it to REASONS "
                f"with the evidence strength it requires, or do not claim it."
            )
        provenance = self.observed_by or {}
        if not provenance.get("type") or not provenance.get("id"):
            raise UnprovenancedObservation(
                f"{self.axis}={self.state}: an observation must name its observer "
                f"(`observed_by: {{type, id}}`). An axis nobody measured must be "
                f"reported as UNKNOWN, not as an observation with an invented "
                f"observer."
            )
        # The load-bearing check: a state that is not UNKNOWN has to have been
        # established by evidence strong enough to carry it.
        if self.state != "UNKNOWN" and self.state != "NOT_APPLICABLE":
            kind = (self.evidence or {}).get("type", "none")
            if kind not in EVIDENCE_STRENGTH:
                raise HealthError(
                    f"{self.axis}={self.state}: unknown evidence type {kind!r}. "
                    f"Known: {sorted(EVIDENCE_STRENGTH)}"
                )
            if not EVIDENCE_STRENGTH[kind] and provenance.get("type") != "diagnostic":
                raise HealthError(
                    f"{self.axis}={self.state}: evidence {kind!r} cannot carry a "
                    f"non-UNKNOWN state. A journal line corroborates a reason; it "
                    f"does not decide a state. Report UNKNOWN, or find a probe "
                    f"that measures the thing."
                )

    @property
    def authoritative(self) -> bool:
        kind = (self.evidence or {}).get("type", "none")
        return bool(EVIDENCE_STRENGTH.get(kind, False))

    def show(self) -> dict:
        out = {
            "state": self.state,
            "observed_by": dict(self.observed_by),
            "observed_at": self.observed_at,
        }
        if self.evidence:
            out["evidence"] = dict(self.evidence)
            out["evidence_authoritative"] = self.authoritative
        if self.reason:
            out["reason"] = self.reason
        return out


def unknown(axis: str, *, why: str = "NOT_OBSERVED") -> Observation:
    """An axis nobody measured. The honest default, and never OK."""
    return Observation(
        axis=axis,
        state="UNKNOWN",
        observed_by={"type": "none", "id": "no-probe"},
        observed_at=None,
        evidence={"type": "none", "why": why},
        reason=why,
    )


def not_applicable(axis: str, *, why: str) -> Observation:
    return Observation(
        axis=axis,
        state="NOT_APPLICABLE",
        observed_by={"type": "declaration", "id": "resource-record"},
        observed_at=None,
        evidence={"type": "none", "why": why},
        reason=None,
    )


# ---------------------------------------------------------------------------
# The four axes together
# ---------------------------------------------------------------------------

AXES = ("liveness", "transport", "functional", "readiness")


@dataclass(frozen=True)
class Health:
    """The four axes of one resource, plus the state they imply."""

    liveness: Observation
    transport: Observation
    functional: Observation
    readiness: Observation
    desired: str
    # A hypothesis, held separately from the axes and never merged into them.
    #
    # `reason` answers "what does this runtime know". `diagnostic` answers
    # "what does the log suggest", and the two are routinely different: for
    # this host's endpoint the honest pair is
    #
    #     reason      DECLARED_SURFACE_ABSENT   (no probe here can answer)
    #     diagnostic  AUTH_FAILURE_SUSPECTED    (corroborated, not proven)
    #
    # Folding the second into the first would have made a log line look like a
    # measurement, which is exactly the promotion this architecture forbids.
    # Dropping it would have discarded the only actionable signal available.
    # So it is reported beside the axes, labelled, and never authoritative.
    diagnostic: dict | None = None

    def axis(self, name: str) -> Observation:
        return getattr(self, name)

    @property
    def state(self) -> str:
        """The derived state. Raises if no rule covers the combination.

        The rules come from `managed-runtime-contract.yaml` and are ordered.
        There is no `else` branch and no default: an uncovered combination is
        a defect in the contract, and a wrong-for-this-resource fallback
        word is more expensive than an exception nobody expected.
        """
        cells = {
            "liveness": self.liveness.state,
            "transport": self.transport.state,
            "functional": self.functional.state,
            "readiness": self.readiness.state,
            "desired": self.desired,
        }
        for rule in registry.contract()["derived_states"]["rules"]:
            if all(cells.get(k) == v for k, v in rule["when"].items()):
                return rule["state"]
        raise HealthUndetermined(
            f"no rule classifies {cells}. Every combination of the declared "
            f"axis values must match exactly one rule; an uncovered one must "
            f"not fall back to a word that means something narrower."
        )

    @property
    def reason(self) -> str | None:
        """The most specific reason any axis carries, in failure-first order.

        Ordered by axis severity rather than by declaration order, because a
        resource that is up-but-broken and also not-ready should report the
        first, and a caller reading one field should get the actionable one.
        """
        for axis in ("functional", "transport", "liveness", "readiness"):
            obs = self.axis(axis)
            if obs.reason:
                return obs.reason
        return None

    @property
    def reason_is_authoritative(self) -> bool:
        for axis in ("functional", "transport", "liveness", "readiness"):
            obs = self.axis(axis)
            if obs.reason:
                return obs.authoritative
        return False

    @property
    def observed_at(self) -> str | None:
        stamps = [self.axis(a).observed_at for a in AXES if self.axis(a).observed_at]
        return max(stamps) if stamps else None

    @property
    def diagnostic_reason(self) -> str | None:
        return (self.diagnostic or {}).get("reason")

    def show(self) -> dict:
        return {
            "state": self.state,
            "axes": {axis: self.axis(axis).show() for axis in AXES},
            "reason": self.reason,
            "reason_authoritative": self.reason_is_authoritative,
            "diagnostic": dict(self.diagnostic) if self.diagnostic else None,
            "observed_at": self.observed_at,
        }


# ---------------------------------------------------------------------------
# Action — a separate axis from state, deliberately
# ---------------------------------------------------------------------------

AUTH_REASONS = frozenset({"AUTH_REAUTH_REQUIRED", "AUTH_FAILURE_SUSPECTED"})

HEALTHY_STATES = frozenset({"HEALTHY", "STOPPED"})
FAULT_STATES = frozenset(
    {"FUNCTIONALLY_FAILED", "NOT_RUNNING", "DEGRADED", "NOT_READY"}
)


def action_for(resource, health: Health) -> dict:
    """What may be done, given what is wrong and who owns it.

    `state` and `action` are different questions and are kept apart. A
    protected resource that is functionally failed has a real fault and no
    permitted automatic response — collapsing that into `action: NONE` would
    read as "nothing to do", and collapsing it into `action: RECONCILE` would
    put the baseline database behind a low-disk trigger.
    """
    state = health.state
    if state in HEALTHY_STATES:
        return {"action": "NONE", "because": f"state={state}"}

    if state not in FAULT_STATES:
        # UNKNOWN / UNAVAILABLE / HEALTHY-adjacent. Nothing has established
        # that anything is wrong, so there is nothing to act on — but it is
        # DEFER rather than NONE, because it is also not established that
        # everything is fine.
        return {
            "action": "DEFER",
            "because": f"state={state}, and an unobserved resource is not a healthy one",
        }

    blockers = resource.why_not_automatable()
    # Either the model concluded it, or the log corroborates it. Both must
    # route to NEEDS_HUMAN, and the record says which of the two it was — a
    # person acting on `AUTH_FAILURE_SUSPECTED` is acting on a hypothesis and
    # is entitled to know that.
    auth_reason = health.reason if health.reason in AUTH_REASONS else None
    source = "reason" if auth_reason else None
    if auth_reason is None and health.diagnostic_reason in AUTH_REASONS:
        auth_reason = health.diagnostic_reason
        source = "diagnostic"
    if auth_reason:
        return {
            "action": "NEEDS_HUMAN",
            "because": (
                "the failure is a consumed or expired authorization. A restart "
                "policy supervises a process; it cannot renew a session. Only "
                "a person completing the authorization changes this."
            ),
            "reason": auth_reason,
            "held_on": source,
            "authoritative": source == "reason" and health.reason_is_authoritative,
            "restarting_is_the_wrong_response": (
                "this host's endpoint restarted 5 times in ~2 hours as each "
                "device code expired; each restart issued a new code nobody "
                "could authorize"
            ),
        }
    if blockers:
        return {
            "action": "NEEDS_HUMAN",
            "because": "fault present and automatic action is not permitted",
            "blocked_by": blockers,
        }
    return {
        "action": "RECONCILE",
        "because": f"registered, owned, policy-allowed, desired={resource.desired}",
        "budget": resource.record.get("reconcile", {}),
    }
