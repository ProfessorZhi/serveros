#!/usr/bin/env python3
"""§15 · Desired state against actual state — and the two ways it goes wrong.

The question
------------
*Is this resource in the state its own registration declares?* That is a
different question from "is it healthy", and the difference is the whole point:
a container at rest because somebody stopped it deliberately and a container at
rest because it crashed are the same health reading and different answers here.

This module does NOT compare anything
-------------------------------------
The comparison already happened, in `health.Health.state`, whose rule table
distinguishes

    liveness STOPPED + desired stopped   -> STOPPED      "at rest; desired"
    liveness STOPPED + desired running   -> NOT_RUNNING  "desired running, is not"

and `probes.observe()` already fills that `desired` field from
`resource.desired`, which comes from the registration. So the comparison has
one implementation and this module reads its output. A second comparison here
would be a second answer to one question, free to disagree with the first — the
defect §12 named for ownership edges, arriving through a state table instead.

What this module adds is the accounting around it: which resources were
actually compared, which were not and why, and the pipeline that turns a
mismatch into an anomaly signal with a threshold beside it.

The two ways it goes wrong
--------------------------
**A missing declaration becomes a mismatch.** §15 states this directly. If
nothing declares what state a resource should be in, then "it is stopped" is a
report about the world and not a fault. The tempting implementation —
`desired_state or "running"` — turns every intentionally-stopped container into
an alert, one per container, all of them false, and the detector's precision is
gone before it ever had a real source for desired state. On this host that
default would fire on `probes.observe`'s `desired=` argument the moment a
caller had no registration to hand, so BOTH halves are guarded here: no
declaration is UNCHECKED, and so is an unmeasured resource.

**A failed measurement becomes a mismatch.** `UNAVAILABLE` means a probe could
not ask; `UNKNOWN` means it asked and could not tell. Neither is "not running".
Reading either as a mismatch reports a probe failure as a resource failure,
which is the §11 defect with a new route in.

Both are UNCHECKED, and the document counts them. `unchecked` is not a
diagnostic tail: a comparison nobody could make is a fact about this node's
coverage, and a reader that sees `mismatches: 0` without seeing
`unchecked: 6` has been told the host is in order when nothing looked.

The third way, which this host has already paid for
---------------------------------------------------
On a host with `hidepid=invisible` — and inside a systemd unit that lands in a
private user namespace, which `runtime/serverosd.service` documents at length —
a `process-exists` probe whose `pgrep` matches nothing reports STOPPED, and
"nothing matched" has **two readings**: the process is gone, or it is running
behind a visibility boundary this uid cannot cross. The probe cannot tell them
apart and neither can anything built on top of it.

So a mismatch resting only on a process match carries an explicit `caveat`
naming both readings, and the coverage block counts those separately. It is
still an IN_MISMATCH rather than an UNCHECKED, and that is a deliberate
choice: reporting it as "could not be compared" would hide a genuinely stopped
protected persistent host in the half of the cases where it really is stopped.
What the caveat buys is that a reader is never told *which* half they are in.
"""
from __future__ import annotations

import importlib.util
import sys
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
anomaly = _load("serveros_runtime_anomaly", "anomaly.py")

FACT = anomaly.FACT
INFERRED = anomaly.INFERRED
UNKNOWN = anomaly.UNKNOWN

CODE = "DESIRED_ACTUAL_MISMATCH"

# §15's three verdicts. Deliberately not a boolean: `IN_MISMATCH` and
# `UNCHECKED` are both "not in the desired state" and only one of them is a
# finding about the resource.
IN_DESIRED_STATE = "IN_DESIRED_STATE"
IN_MISMATCH = "IN_MISMATCH"
UNCHECKED = "UNCHECKED"

VERDICTS = (IN_DESIRED_STATE, IN_MISMATCH, UNCHECKED)


def contract_section() -> dict:
    """The declared join between a desired word and a measured word, or `{}`.

    `{}` maps nothing, which makes every comparison UNCHECKED. That is the
    strict direction and it is the same rule `ports._confidence_alignment`
    follows: an unreadable declaration must not let a comparison succeed
    because both sides happened to be spelled alike.
    """
    try:
        return registry.contract().get("desired_actual") or {}
    except Exception:  # noqa: BLE001 - an unreadable contract is "no mapping"
        return {}


def mismatch_states() -> dict:
    """Derived states that contradict a declaration, and which one."""
    return contract_section().get("mismatch_states") or {}


def not_comparable() -> tuple:
    """Measured states that no declaration can be compared against."""
    return tuple(contract_section().get("not_comparable") or ())


# ---------------------------------------------------------------------------
# Reading the two sides
# ---------------------------------------------------------------------------


def _declared_of(declared) -> tuple:
    """`(desired_state, where_it_came_from)` from a Resource, a dict, or None.

    `None` is a first-class answer. It is what an observed runtime with no
    registration has, and it is the case §15 is written about.
    """
    if declared is None:
        return None, None
    if isinstance(declared, registry.Resource):
        return declared.desired, f"the registration {declared.id!r}"
    if isinstance(declared, str):
        # A bare word is accepted because a caller holding only the desired
        # value is a real case, and the alternative is that caller writing
        # `{"desired": ...}` and inventing a shape. It carries no provenance,
        # which is said rather than papered over.
        return declared, "a declared value with no registration behind it"
    desired = declared.get("desired")
    if isinstance(desired, dict):
        desired = desired.get("state")
    if not desired:
        return None, None
    return desired, f"the declaration {declared.get('id') or declared.get('ref') or '(unnamed)'}"


def _measured_of(measured) -> tuple:
    """`(derived_state, liveness_state, evidence, observed_at)` from either shape.

    A `health.Health` is preferred — it carries the comparison this module
    reads rather than re-makes — and a plain mapping is accepted so that a
    caller which rendered a health record to JSON can be compared without
    rebuilding the object graph.
    """
    if measured is None:
        return None, None, {}, None
    if hasattr(measured, "state") and hasattr(measured, "liveness"):
        liveness = measured.liveness
        return (measured.state, liveness.state, dict(liveness.evidence or {}),
                liveness.observed_at)
    derived = measured.get("derived_state") or measured.get("state")
    liveness = measured.get("liveness")
    if isinstance(liveness, dict):
        liveness_state = liveness.get("state")
        evidence = liveness.get("evidence") or {}
        observed_at = liveness.get("observed_at")
    else:
        liveness_state = liveness
        evidence = measured.get("evidence") or {}
        observed_at = measured.get("observed_at")
    return derived, liveness_state, dict(evidence), observed_at


# ---------------------------------------------------------------------------
# One comparison
# ---------------------------------------------------------------------------


def comparison(*, declared=None, measured=None, resource_id=None,
               resource_ref=None, observed_at=None) -> dict:
    """One registered resource against what was measured about it.

    Every field is present on every row, including the null ones: a missing
    key and a null key read differently and only one of them is a statement.
    """
    desired, declared_by = _declared_of(declared)
    derived, liveness, evidence, measured_at = _measured_of(measured)
    rid = resource_id
    if rid is None and isinstance(declared, registry.Resource):
        rid = declared.id

    row = {
        "resource_id": rid,
        "resource_ref": resource_ref or (f"runtime:{rid}" if rid else None),
        "desired_state": desired,
        "declared_by": declared_by,
        "desired_source": (
            "the managed-runtime registry" if declared_by and
            "registration" in declared_by else
            UNKNOWN if desired is None else "supplied by the caller"
        ),
        "protection": (declared.protection
                       if isinstance(declared, registry.Resource) else None),
        "actual_state": liveness,
        "actual_derived_state": derived,
        "actual_evidence": evidence,
        # The provider's own word, kept beside the normalized one. `restarting`
        # and `dead` both normalize to STOPPED, and they call for different
        # things from whoever reads this.
        "actual_raw": evidence.get("container_status") or evidence.get("systemd_active_state"),
        "measured_at": measured_at or observed_at,
        # Whether the actual state rests on a process match, and therefore on
        # the visibility of /proc. See the module docstring.
        "rests_on_a_process_match": "match_count" in evidence,
        # Set by the mismatch path. Present on every row so that "no caveat"
        # and "no such field" cannot be read as the same thing.
        "caveat": None,
    }

    if desired is None:
        row.update({
            "verdict": UNCHECKED,
            "mismatch": None,
            "why": (
                "nothing declares what state this resource should be in. §15: "
                "a missing desired state does not produce a mismatch — "
                "`desired_state = None` is the ABSENCE of a comparison, not a "
                "comparison that failed. Defaulting it to `running` here is "
                "how every intentionally-stopped resource becomes an alert"
            ),
        })
        return row

    if liveness is None:
        row.update({
            "verdict": UNCHECKED,
            "mismatch": None,
            "why": (
                "nothing measured this resource, so there is no actual state to "
                "compare the declaration against. Unmeasured is not mismatched"
            ),
        })
        return row

    if liveness in not_comparable():
        row.update({
            "verdict": UNCHECKED,
            "mismatch": None,
            "why": (
                f"the measurement is {liveness}, which is not a state a "
                f"declaration can be compared against: it says the measurement "
                f"did not happen, not that the resource is in the other state. "
                f"Reading it as a mismatch would report a probe failure as a "
                f"resource failure"
            ),
        })
        return row

    section = contract_section()
    if not section.get("satisfies") and not section.get("mismatch_states"):
        # Read once and checked once, because BOTH branches below are decided
        # by this declaration and an unreadable one must not be able to produce
        # a positive verdict through either route. The first version checked
        # only inside the `derived is None` branch, and an unreadable contract
        # then reported IN_DESIRED_STATE for every resource the health model had
        # classified — a whole class of finding silently disappearing because
        # nobody could read the file that defines it.
        row.update({
            "verdict": UNCHECKED,
            "mismatch": None,
            "why": (
                "the contract's `desired_actual` section is unreadable, so "
                "neither the measured words that satisfy a declaration nor the "
                "derived states that contradict one are known. Nothing was "
                "compared. That is a gap in the declaration, not a resource "
                "out of state, and it is the strict direction on purpose: a "
                "missing mapping must not be satisfied by two words that happen "
                "to be spelled alike"
            ),
        })
        return row

    satisfies = section.get("satisfies") or {}
    if derived is None:
        # No derived state to read. The measured word is all there is, and the
        # declared join decides — never string equality on the two vocabularies,
        # which is the comparison that silently stops matching the day either
        # side gains a member.
        comparable = liveness in (satisfies.get(desired) or ())
        row.update({
            "verdict": IN_DESIRED_STATE if comparable else UNCHECKED,
            "mismatch": None if comparable else None,
            "why": (
                f"desired {desired!r} is satisfied by the measured {liveness}"
                if comparable else
                f"the measured state is {liveness} and the contract's "
                f"`desired_actual.satisfies` names no measured state for "
                f"desired {desired!r}, so the two were not compared. That is a "
                f"gap in the declaration, not a resource out of state"
            ),
        })
        return row

    contradicts = mismatch_states().get(derived)
    if contradicts is None:
        row.update({
            "verdict": IN_DESIRED_STATE,
            "mismatch": None,
            "why": (
                f"the health model derived {derived} for a resource that "
                f"declares {desired!r}, and {derived} is not a state the "
                f"contract names as contradicting a declaration"
            ),
        })
        return row

    if contradicts != desired:
        # The derived state means "this contradicts <some other> declaration".
        # A resource whose own declaration is the other one has not
        # contradicted anything.
        row.update({
            "verdict": IN_DESIRED_STATE,
            "mismatch": None,
            "why": (
                f"the health model derived {derived}, which contradicts a "
                f"declaration of {contradicts!r}; this resource declares "
                f"{desired!r}, so nothing was contradicted"
            ),
        })
        return row

    row.update({
        "verdict": IN_MISMATCH,
        "mismatch": {
            "declared": desired,
            "measured": liveness,
            "derived": derived,
        },
        "caveat": _process_match_caveat(row, evidence),
        "why": (
            f"the registration declares {desired!r} and the measurement says "
            f"{liveness} ({derived}). Which of the two is wrong is not "
            f"something this comparison can see — a declaration that is stale "
            f"and a resource that has stopped look the same from here"
        ),
    })
    return row


def _process_match_caveat(row: dict, evidence: dict) -> str | None:
    """The two readings of "the pattern matched no process", or None.

    Attached only to a mismatch that rests on a process match, because that is
    the only measurement on this node whose negative result has two meanings.
    A container's `exited` status does not: Docker resolved the container and
    reported a state, and there is no namespace in which that reading changes.
    """
    if "match_count" not in evidence:
        return None
    return (
        f"the measurement is `pgrep -f` matching {evidence.get('match_count')} "
        f"process(es), and on this host that negative result has two readings "
        f"that this comparison cannot tell apart: the process is gone, or it is "
        f"running behind a visibility boundary this uid cannot cross. "
        f"`runtime/serverosd.service` documents the second case being measured "
        f"on this very node — a healthy supervisor with two days of uptime "
        f"invisible from inside a unit that landed in a private user namespace. "
        f"A positive control (`readlink /proc/self/ns/user` beside this same "
        f"pgrep, from the process that will run the probe) settles which. This "
        f"row does not."
    )


# ---------------------------------------------------------------------------
# The document
# ---------------------------------------------------------------------------


def desired_actual(*, registry_doc=None, measured=None, observed_at=None,
                   device_ref=None) -> dict:
    """§15. Every registration compared against what was measured about it.

    `registry_doc` is the authority for desired state — a `Registry`, a parsed
    document, or a path. `measured` maps a resource id to a `health.Health` (or
    an equivalent mapping), and ids with no registration are reported too: an
    observed runtime nobody declared is exactly what §15's rule about missing
    desired state is for, and dropping it would make the document a report on
    the registry rather than on the node.
    """
    reg = _as_registry(registry_doc)
    measured = measured or {}

    declared_ids = [r.id for r in reg.all()] if reg is not None else []
    rows = []
    for resource in (reg.all() if reg is not None else []):
        rows.append(comparison(
            declared=resource,
            measured=measured.get(resource.id),
            resource_id=resource.id,
            observed_at=observed_at,
        ))

    for rid in sorted(set(measured) - set(declared_ids)):
        rows.append(comparison(
            declared=None,
            measured=measured[rid],
            resource_id=rid,
            observed_at=observed_at,
        ))

    mismatches = [r for r in rows if r["verdict"] == IN_MISMATCH]
    unchecked = [r for r in rows if r["verdict"] == UNCHECKED]

    return {
        "schema": "serveros/desired-actual.v1",
        "device_ref": device_ref,
        "observed_at": observed_at,
        "comparisons": rows,
        "mismatches": mismatches,
        "unchecked": unchecked,
        "signals": [signal_of(r) for r in mismatches],
        "providers": {
            "registry": {
                "state": "AVAILABLE" if reg is not None else "UNAVAILABLE",
                "why": None if reg is not None else (
                    "no registry was supplied, so nothing declares a desired "
                    "state and no comparison is possible"
                ),
                "registrations": len(declared_ids),
            },
            "measurement": {
                "state": "AVAILABLE" if measured else "UNAVAILABLE",
                "why": None if measured else (
                    "no measurement was supplied. This is reported rather than "
                    "rendered as a document full of UNCHECKED rows with no "
                    "reason for the pattern"
                ),
                "resources_measured": len(measured),
            },
        },
        "coverage": {
            "comparisons": len(rows),
            "in_desired_state": sum(1 for r in rows if r["verdict"] == IN_DESIRED_STATE),
            "in_mismatch": len(mismatches),
            "unchecked": len(unchecked),
            # Split out because the two are different problems for different
            # people: an undeclared resource is a registration to write, an
            # unmeasured one is a probe to fix.
            "unchecked_because_nothing_declares_it": sum(
                1 for r in unchecked if r["desired_state"] is None),
            "unchecked_because_nothing_measured_it": sum(
                1 for r in unchecked if r["desired_state"] is not None
                and r["actual_state"] is None),
            "unchecked_because_the_measurement_could_not_be_compared": sum(
                1 for r in unchecked if r["actual_state"] in not_comparable()),
            # A mismatch that rests on `pgrep` matching nothing, counted apart
            # from the rest. Both halves of this number are real: a resource
            # that stopped, and a resource that is running where this uid
            # cannot look. A reader who sees this equal to `in_mismatch` knows
            # that EVERY mismatch on this node came from the one measurement
            # whose negative result has two meanings.
            "mismatches_resting_on_a_process_match": sum(
                1 for r in mismatches if r["rests_on_a_process_match"]),
            "mismatches_from_a_provider_response": sum(
                1 for r in mismatches if not r["rests_on_a_process_match"]),
        },
        "note": (
            "§15: read-only, and the comparison is the health model's, not a "
            "second one made here. A mismatch is a SIGNAL that a declaration "
            "and the world disagree; nothing in this module decides which of "
            "the two is wrong, and nothing here acts on one."
        ),
    }


def signal_of(row: dict) -> dict:
    """A mismatch row as an anomaly signal, with the threshold beside it.

    Built through `anomaly.signal` so the code carries the severity the
    contract assigns it and the `implies_stale: false` every signal in this
    catalogue carries. A detector whose output shape drifts from the rest of
    the catalogue is a detector that gets filtered out of the report.
    """
    return anomaly.signal(
        CODE,
        row["resource_ref"] or row["resource_id"],
        {
            "desired_state": row["desired_state"],
            "actual_state": row["actual_state"],
            "actual_derived_state": row["actual_derived_state"],
            "actual_raw": row["actual_raw"],
            # The declaration is the threshold in this comparison: there is no
            # numeric bound to report, and the thing a reader must be able to
            # check is what the resource was measured against.
            "threshold": row["desired_state"],
            "measured_at": row["measured_at"],
            # The qualifier the comparison earned, carried INSIDE the measured
            # block rather than beside it. `alerting` copies `signal["measured"]`
            # onto the alert record and nothing else from the signal's own keys,
            # so a caveat left at the top level was written by this module and
            # then dropped one hop later -- present in the comparison document,
            # absent from the register, and invisible from either end.
            "caveat": row["caveat"],
        },
        window="the interval between the registration and this observation",
        why=row["why"],
    )


def _as_registry(registry_doc):
    """A `Registry` from a Registry, a parsed document, a path, or None."""
    if registry_doc is None:
        return None
    if isinstance(registry_doc, registry.Registry):
        return registry_doc
    if isinstance(registry_doc, (str, Path)):
        return registry.Registry(path=Path(registry_doc))
    return registry.Registry(document=registry_doc)
