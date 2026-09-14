#!/usr/bin/env python3
"""One runtime family, on one device — the record the contract said existed.

    python3 runtime_binding.py --registry fleet/registry.yaml --device device:work-fj

WHY THIS FILE EXISTS
--------------------
`abilities/agent-runtime-contract.yaml:23-27` has said since it was written that
a runtime record is device-independent "so that one runtime be described once
and installed on three machines with three different versions of it". The second
half of that sentence had nothing behind it. `fleet/registry.yaml` carried

    agents: [claude-code, codex]

on each of three devices, which is MEMBERSHIP: it can say a family is expected
here, and it cannot say which installation, on which surface, at which version,
measured when, or with which features actually probed. `docs/agent-runtime-model.md`
recorded the gap as `binding: declared as already existing; absent`, and the
contract at `:37` and `:193` asserted that installations were "already
modelled — one per device, in the registry, with an owner". They were not.

This module is the binding. It is the one place a caller asks what a device's
runtime relationship actually is, so that the Ability resolver, the fleet view
and the CLI do not each grow a private reading of the registry.

WHAT IT IS NOT
--------------
It is not a store. There is no `bindings.json`, no writer, no cache and nothing
to go stale on disk. A binding is resolved from two inputs the caller already
has — the authored registry and an observation — and returned. That is
`provider_instance.py`'s `persistence: none` for the same reason: a stored
observation is a memory of a past machine being read as a statement about the
present one, and the cheaper and more honest answer is to measure again.

It is also not a health system. The staleness arithmetic below is imported from
`abilities/provider_instance.py` rather than written a second time, because "an
old success is not a current success" is one rule and two implementations of it
are two rules.

THREE ANSWERS, AND A FOURTH
---------------------------
A feature state is not a boolean. `fleet/transport.py:433` solved this exact
problem one plane over with a four-valued `CapabilityAvailability`, and the
states here are that object's, with `INCOMPATIBLE` added for a question that
does not apply to a family at all:

    AVAILABLE      the binding has it, and that was established
    UNAVAILABLE    the binding does not have it, and that was established
    UNKNOWN        nobody established it — NOT a no
    INCOMPATIBLE   this runtime FAMILY has no such axis

The fourth is why `if runtime == "codex": app_server = True` is not merely
discouraged here but unrepresentable: applicability comes from
`runtime_features.axes` in the contract, and a state beyond applicability comes
from an observation or is `UNKNOWN`. `feature()` is the only function that
answers the question, and it is the symbol `product_invariants` names.
"""
from __future__ import annotations

import argparse
import datetime
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent

SCHEMA = "serveros/runtime-binding.v1"

BINDING_PREFIX = "binding:"
BINDING_SEPARATOR = "/"

# The four answers, in the contract's own order. Cross-checked against
# `runtime_features.states` by test, because two lists of state values is a
# defect this tree has found more than once.
FEATURE_STATES = ("AVAILABLE", "UNAVAILABLE", "UNKNOWN", "INCOMPATIBLE")

# What an observation of a binding is. Not the same axis as a feature state:
# this says whether anyone reached the device, and the difference it preserves
# is between a device that is offline and one that was never enrolled.
OBSERVATION_STATES = ("OBSERVED", "UNKNOWN")

SURFACE_UNKNOWN = "UNKNOWN"

CONTRACT = _HERE / "agent-runtime-contract.yaml"
DEFAULT_FRESHNESS_SECONDS = 900


class BindingError(RuntimeError):
    """The input is malformed, or it breaks a rule the contract states.

    Never a binding verdict. A probe that could not be run produces `UNKNOWN` —
    an answer. A version carrying no `observed_at` produces this — a refusal —
    because that is a claim dressed as a measurement and there is no honest
    record to return for it.
    """


# ---------------------------------------------------------------------------
# Loading. `target.py` is loaded for the runtime ref shape rather than the
# prefix being spelled a second time here: an `agent-runtime:` ref has one
# minter and one parser, and a second reading of that string is how a ref family
# forks. `provider_instance.py` is loaded for the freshness arithmetic.
# ---------------------------------------------------------------------------

def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


target = _load("serveros_abilities_target", _HERE / "target.py")
_provider_instance = _load("serveros_provider_instance", _HERE / "provider_instance.py")


def contract() -> dict:
    import yaml
    data = yaml.safe_load(CONTRACT.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise BindingError(f"{CONTRACT} must contain a mapping")
    return data


def runtime_ids() -> tuple[str, ...]:
    return tuple(target.runtime_ids())


def feature_names() -> tuple[str, ...]:
    """The closed vocabulary, in the order the contract declares it."""
    return tuple((contract().get("runtime_features") or {}).get("order") or ())


def feature_states() -> tuple[str, ...]:
    return tuple((contract().get("runtime_features") or {}).get("states") or ())


def declared_axes(runtime_id: str) -> tuple[str, ...]:
    """Which features this runtime FAMILY has an axis for.

    A declaration with authority, and the only lawful source of applicability.
    A feature absent here is `INCOMPATIBLE` for every binding of that family,
    and no amount of familiarity with what the product usually does changes it.
    """
    axes = (contract().get("runtime_features") or {}).get("axes") or {}
    if runtime_id not in axes:
        raise BindingError(
            f"{runtime_id!r} declares no `runtime_features.axes` entry; a family "
            "with no declared axes would answer INCOMPATIBLE for every feature, "
            "which reads as a finding rather than as a missing declaration")
    declared = axes[runtime_id]
    unknown = [name for name in declared if name not in feature_names()]
    if unknown:
        raise BindingError(
            f"{runtime_id} declares axes that are not in the vocabulary: {unknown}")
    return tuple(declared)


def surfaces() -> tuple[str, ...]:
    return tuple((contract().get("bindings") or {}).get("surfaces", {}).get("order") or ())


def version_probe_for(runtime_id: str) -> str | None:
    """Which probe is lawful for this family, or None when nothing declares one."""
    declared = ((contract().get("version_observation") or {})
                .get("version_observed_by") or {})
    return declared.get(runtime_id)


def accepted_probes() -> tuple[str, ...]:
    return tuple((contract().get("version_observation") or {}).get("accepted_probes") or ())


def implemented_probes() -> tuple[str, ...]:
    """The accepted probes something can actually run, as opposed to accept."""
    return tuple((contract().get("version_observation") or {})
                 .get("implemented_probes") or ())


def cli_program_for(runtime_id: str) -> str | None:
    """The program the `cli-version` probe asks, by name, or None."""
    declared = (contract().get("version_observation") or {}).get("cli_program") or {}
    return declared.get(runtime_id)


def product_invariants() -> dict[str, dict]:
    """The features declared true of every supported runtime, by name."""
    return {row.get("name"): row
            for row in (contract().get("product_invariants") or ())
            if isinstance(row, dict) and row.get("name")}


def freshness_seconds() -> int:
    block = ((contract().get("bindings") or {}).get("observation") or {})
    return int((block.get("freshness") or {}).get("window_seconds")
               or DEFAULT_FRESHNESS_SECONDS)


# ---------------------------------------------------------------------------
# Identity. `device_ref + runtime_ref` and nothing else — the contract's
# `bindings` block names each excluded candidate and why. A version upgrade or a
# surface change is a new OBSERVATION of the same binding, and an identity that
# moved with either would strand every observation attached to it.
# ---------------------------------------------------------------------------

def binding_ref(device_ref: str, runtime_ref: str) -> str:
    """The stable identity of one runtime family on one device."""
    device = _require_ref(device_ref, "device:")
    runtime = _require_ref(runtime_ref, "agent-runtime:")
    return f"{BINDING_PREFIX}{device}{BINDING_SEPARATOR}{runtime}"


def _require_ref(value: str, prefix: str) -> str:
    text = str(value or "")
    if not text.startswith(prefix) or len(text) == len(prefix):
        raise BindingError(
            f"{value!r} is not a typed {prefix}<id> ref. A binding is built from "
            "two refs, and neither a bare id nor a display name is one")
    return text


def parse_binding_ref(ref: str) -> tuple[str, str]:
    """(device_ref, runtime_ref), or a refusal. Round-trips with `binding_ref()`.

    The two halves are the TYPED refs joined by a slash, not a new coordinate
    system: each half already has its own parser and its own meaning, and a
    binding is the pair rather than a third namespace. So this returns the two
    strings exactly as they were written and refuses anything that is not two
    typed refs — an earlier version stripped the prefixes on the way in and
    re-added them on the way out, which round-tripped only for inputs that had
    never been minted and produced `agent-runtime:agent-runtime:codex` for one
    that had. The bug was invisible in every hand-written example and immediate
    under a round-trip test, which is why the test exists.
    """
    text = str(ref or "")
    if not text.startswith(BINDING_PREFIX):
        raise BindingError(
            f"{text!r} is not a binding ref; a binding is named "
            f"`{BINDING_PREFIX}<device-ref>{BINDING_SEPARATOR}<runtime-ref>`")
    body = text[len(BINDING_PREFIX):]
    device, separator, runtime = body.partition(BINDING_SEPARATOR)
    if not separator:
        raise BindingError(
            f"{text!r} does not name both halves; a binding is "
            f"`{BINDING_PREFIX}<device-ref>{BINDING_SEPARATOR}<runtime-ref>`")
    if BINDING_SEPARATOR in runtime:
        raise BindingError(
            f"{text!r} names more than two halves. `partition` splits on the "
            "first separator, so a third one lands inside the runtime ref and "
            "the result passes the prefix check while naming nothing")
    return (_require_ref(device, "device:"), _require_ref(runtime, "agent-runtime:"))


# ---------------------------------------------------------------------------
# The authored half, read from the registry.
# ---------------------------------------------------------------------------

def devices_of(registry) -> list[dict]:
    """The device list, from either the document or a bare list or mapping."""
    if registry is None:
        return []
    if isinstance(registry, dict):
        devices = registry.get("devices")
        if devices is None:
            return [{"device_id": key, **(value or {})}
                    for key, value in registry.items()]
        registry = devices
    if isinstance(registry, dict):
        return [{"device_id": key, **(value or {})} for key, value in registry.items()]
    return list(registry)


def declared_bindings(registry, device_ref: str | None = None) -> list[dict]:
    """Every authored binding, optionally for one device.

    A device with no `runtime_bindings` key has NO declared bindings, and that
    is a finding about the registry rather than about the machine. It is not the
    same as a device whose observation could not be taken, and only the caller
    knows which of the two it is holding.
    """
    found: list[dict] = []
    for device in devices_of(registry):
        this_ref = device.get("device_ref") or (
            f"device:{device.get('device_id')}" if device.get("device_id") else None)
        if device_ref is not None and this_ref != device_ref:
            continue
        for entry in device.get("runtime_bindings") or ():
            runtime_ref = entry.get("runtime_ref")
            found.append({
                "binding_ref": (binding_ref(this_ref, runtime_ref)
                                if this_ref and runtime_ref else None),
                "device_ref": this_ref,
                "runtime_ref": runtime_ref,
                "runtime_id": runtime_id_of(runtime_ref),
                "declared_at": entry.get("declared_at"),
                "declared_provenance": "fleet/registry.yaml",
            })
    return found


def runtime_id_of(runtime_ref) -> str | None:
    """The id inside a runtime ref, or None when it is not one.

    None rather than a raise: a registry entry naming something else is a
    finding with its own code, and a scanner that stopped at the first
    malformed line would report one problem and hide the rest.
    """
    try:
        return target.parse_runtime_ref(runtime_ref)
    except Exception:  # noqa: BLE001 - a bad ref is data, not a crash
        return None


def membership_findings(registry, declared=None) -> list[dict]:
    """`BINDING_MEMBERSHIP_MISMATCH`, in both directions, one finding per pair.

    `runtime_bindings` is authoritative and `agents` is a projection of it. Two
    lists that disagree are two statements about one device, so this reports the
    disagreement rather than picking a winner: a check that silently preferred
    the binding would make the legacy list unfalsifiable, and one that preferred
    the legacy list would undo the migration.

    Both directions are reported because they mean different things. An agent
    with no binding is a runtime the device claims and cannot resolve; a binding
    with no agent is a runtime this device resolves and the older view does not
    know about.

    Called by `abilities/runtime_parity.py — check_registry()`, which is where a
    finding becomes a failed check.
    """
    known = set(declared if declared is not None else runtime_ids())
    findings: list[dict] = []
    for device in devices_of(registry):
        device_id = device.get("device_id") or device.get("device_ref") or "?"
        bound: list[str] = []
        for entry in device.get("runtime_bindings") or ():
            runtime_ref = entry.get("runtime_ref")
            runtime_id = runtime_id_of(runtime_ref)
            if runtime_id is None or runtime_id not in known:
                findings.append(_finding(
                    "BINDING_NAMES_UNKNOWN_RUNTIME",
                    f"device {device_id} binds {runtime_ref!r}, which is not an "
                    "Agent Runtime this contract declares; the binding has no "
                    "runtime to attach to",
                    device=device_id, runtime_ref=runtime_ref))
                continue
            bound.append(runtime_id)
        legacy = [str(name) for name in device.get("agents") or ()]
        for runtime_id in sorted(set(legacy) - set(bound)):
            findings.append(_finding(
                "BINDING_MEMBERSHIP_MISMATCH",
                f"device {device_id} lists agent {runtime_id!r} and declares no "
                "runtime binding for it; `agents` is a projection of "
                "`runtime_bindings` and there is nothing here to project",
                device=device_id, runtime=runtime_id, direction="binding_missing"))
        for runtime_id in sorted(set(bound) - set(legacy)):
            findings.append(_finding(
                "BINDING_MEMBERSHIP_MISMATCH",
                f"device {device_id} binds {runtime_id!r} and its `agents` list "
                "does not name it; the older view does not see a runtime this "
                "device resolves",
                device=device_id, runtime=runtime_id, direction="legacy_missing"))
    return findings


def _finding(code: str, detail: str, **extra) -> dict:
    return {"code": code, "detail": detail, **extra}


# ---------------------------------------------------------------------------
# The observed half.
# ---------------------------------------------------------------------------

def now_utc(clock=None) -> str:
    moment = clock() if clock else datetime.datetime.now(datetime.timezone.utc)
    return moment.astimezone(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def version_observation(*, runtime_id: str, observed_at: str | None,
                        probe: str | None, version: str | None) -> dict:
    """The version half of an observation, refused here if it breaks the rule.

    `version_observation.rule` in the contract says a version with no
    `observed_at` is refused, and this is that refusal. The block spent its
    first life as a required record field no code read, so the rule was a
    sentence two documents agreed on; it is now enforced where it is stated.
    Two refusals, and they are the same mistake one step apart:

    * a version or a probe with no `observed_at` — a fact about a machine with
      no time on it, which is how "it worked yesterday" and "it works today"
      become statements about two different programs;
    * a probe that is not the one this family declares — a version observed by
      another means is a different fact wearing the same field name.
    """
    if observed_at is None:
        if version is not None or probe is not None:
            raise BindingError(
                "a version or a probe with no `observed_at` is refused: 'the "
                "version is X' written from memory is indistinguishable in the "
                "record from 'the version is X' measured a minute ago")
        return {"version": None, "probe": None, "version_state": "UNKNOWN"}
    if probe is not None:
        if probe not in accepted_probes():
            raise BindingError(
                f"{probe!r} is not an accepted probe; the contract accepts "
                f"{list(accepted_probes())}")
        declared = version_probe_for(runtime_id)
        if declared is not None and probe != declared:
            raise BindingError(
                f"a {runtime_id} version observed by {probe!r} is refused; this "
                f"family's `version_observed_by` is {declared!r}")
    if version is None:
        return {"version": None, "probe": probe, "version_state": "UNKNOWN"}
    return {"version": version, "probe": probe, "version_state": "FACT"}


def observation(*, runtime_id: str, observed_at: str | None = None,
                probe: str | None = None, version: str | None = None,
                source: str | None = None, surface: str | None = None,
                features: dict | None = None) -> dict:
    """One measurement of one binding, refused here if it breaks a stated rule.

    `UNAVAILABLE` and `UNKNOWN` are different answers, and so are a missing
    observation and an empty one: `features: {}` means a probe ran and
    established nothing about any feature, which is still a probe that ran.

    A version with no `observed_at` is refused rather than downgraded, which is
    `version_observation()`'s job and is why the two are separate functions.
    """
    if runtime_id not in runtime_ids():
        raise BindingError(f"{runtime_id!r} is not a declared Agent Runtime")
    version_part = version_observation(runtime_id=runtime_id, observed_at=observed_at,
                                       probe=probe, version=version)
    if observed_at is None and surface is not None:
        raise BindingError(
            "a surface with no `observed_at` is refused for the same reason a "
            "version is: nobody established it, on this device, at any time")
    if surface is not None and surface not in surfaces() + (SURFACE_UNKNOWN,):
        raise BindingError(
            f"{surface!r} is not a declared surface; expected one of "
            f"{list(surfaces())} or {SURFACE_UNKNOWN}")
    built = {
        "state": "OBSERVED" if observed_at is not None else "UNKNOWN",
        "observed_at": observed_at,
        "source": source,
        "surface": surface,
        "features": {},
        **version_part,
    }
    for name, entry in (features or {}).items():
        if name not in feature_names():
            raise BindingError(
                f"{name!r} is not a runtime feature; the vocabulary is closed "
                f"and is {list(feature_names())}")
        state = (entry or {}).get("state") if isinstance(entry, dict) else entry
        if state not in FEATURE_STATES:
            raise BindingError(
                f"{name}: {state!r} is not a feature state; expected one of "
                f"{list(FEATURE_STATES)}. A boolean here would make 'we did not "
                "ask' and 'no' the same value")
        built["features"][name] = ({"state": state, **entry}
                                   if isinstance(entry, dict) else {"state": state})
    return built


def _age(observed_at: str | None, reference: str | None) -> float | None:
    """Seconds old, or None when it cannot be said. None is not zero."""
    if observed_at is None:
        return None
    return _provider_instance.age_seconds(
        SimpleNamespace(observed_at=observed_at), reference)


def _as_text(value):
    """A record field as text, whatever YAML handed back.

    `fleet/registry.yaml` writes `declared_at: 2026-09-14` unquoted, and YAML
    resolves that to a `datetime.date`. Every other field of every other record
    in this tree is a string, and the ones that are not are the ones that crash
    on the single path that serialises them — the CLI's `--json`. Coerced at the
    boundary the record is built, so no reader has to know.
    """
    if value is None or isinstance(value, str):
        return value
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def is_stale(observed_at: str | None, reference: str | None,
             window: int | None = None) -> bool:
    """Has this observation aged out of being a statement about now?

    The window is resolved here rather than left to the helper's default,
    because that default reads the PROVIDER contract's freshness window — the
    right number for a provider and the wrong one for a runtime binding.
    """
    if observed_at is None:
        return True
    return _provider_instance.is_stale(
        SimpleNamespace(observed_at=observed_at), reference,
        window if window is not None else freshness_seconds())


def feature(runtime_id: str, name: str, observed: dict | None,
            *, runtime_ref: str | None = None) -> tuple[str, str | None]:
    """(state, evidence) for one feature of one binding.

    Ordered, and the order is the design: applicability first, then the
    observation, then UNKNOWN.

    * A feature the family has no axis for is `INCOMPATIBLE` whether or not
      anything was observed. A probe that answered about an axis the family does
      not have therefore cannot promote it.
    * An axis nobody answered about is `UNKNOWN`, never `UNAVAILABLE`.
    * An axis the contract declares a PRODUCT INVARIANT is `AVAILABLE` for
      every declared runtime, because that is what the contract says of the
      product, and `product_invariants` names this function as what enforces
      it. A binding observed to contradict one is a finding about the contract,
      and it is not resolved here by quietly preferring the observation: a
      declaration-with-authority is a lawful source of a feature state (§6), and
      a function that reversed one on the strength of a single device would make
      the invariant unenforceable by construction.

    Every branch returns a sentence, including the one that reads a probe's
    answer: `evidence` is what makes a state auditable, and a field that is a
    string for three states and `None` for the fourth is a field the consumer
    has to know the states to read.

    Nothing here reads a runtime's NAME. There is no branch that consults
    `runtime_id` to decide a state — it is used only to find the declared axes,
    the invariant list and the ref to name in the evidence, which is what makes
    "Codex Desktop should have a GUI" unrepresentable rather than merely
    disallowed.
    """
    if name not in feature_names():
        raise BindingError(
            f"{name!r} is not a runtime feature; the vocabulary is closed and is "
            f"{list(feature_names())}")
    if name not in declared_axes(runtime_id):
        return ("INCOMPATIBLE",
                f"{runtime_ref or target.runtime_ref(runtime_id)} declares no "
                f"`{name}` axis")
    invariant = product_invariants().get(name)
    if invariant is not None:
        return ("AVAILABLE",
                "declared true of every supported runtime in "
                "`agent-runtime-contract.yaml#product_invariants`; a feature "
                "state that depends on nothing per device is a statement about "
                "the product and is answered as one")
    entry = ((observed or {}).get("features") or {}).get(name)
    if entry is None:
        return ("UNKNOWN",
                "no observation of this binding established it; not observed is "
                "not the same as absent")
    return (entry["state"], entry.get("evidence") or _observed_evidence(observed))


def _observed_evidence(observed: dict | None) -> str:
    """A sentence for a state a probe established without saying any more.

    Every other branch of `feature()` produces a sentence — which axis is
    missing, which declaration the invariant came from, that nobody asked. A
    probe that reports `AVAILABLE` and nothing else left this one `None`, so
    `features[x]["evidence"]` was "a string, except here", and a reader
    auditing a state had to know which states came with a reason. It is filled
    from the observation's own provenance rather than left to the consumer.
    """
    source = (observed or {}).get("source")
    at = (observed or {}).get("observed_at")
    if source and at:
        return f"observed by {source} at {at}"
    if source:
        return f"observed by {source}"
    return "established by an observation of this binding"


# ---------------------------------------------------------------------------
# The record.
# ---------------------------------------------------------------------------

def binding(declaration: dict, *, observed: dict | None = None,
            reference: str | None = None, window: int | None = None,
            unobserved_because: str | None = None) -> dict:
    """One binding: what was authored, what was observed, and which is which.

    The declared half survives an unreachable device. That is the difference
    this record exists to carry — a device whose observation is `UNKNOWN` is a
    device with a binding and no measurement, and reporting it as having no
    binding at all is how "offline" becomes "never enrolled".

    `unobserved_because` lets a CALLER that knows why nothing was measured say
    so, instead of being given the generic sentence — "nobody asked", "this node
    has no route to that machine" and "the last answer aged out" are three
    different findings that a single `UNKNOWN` would serve equally badly. It is
    a parameter rather than a field the caller overwrites afterwards so that the
    top-level field and `levels.L3` cannot come to disagree.
    """
    device_ref = declaration.get("device_ref")
    runtime_ref = declaration.get("runtime_ref")
    runtime_id = declaration.get("runtime_id") or runtime_id_of(runtime_ref)
    if not device_ref or runtime_id is None:
        raise BindingError(
            "a binding names one device and one declared runtime; got "
            f"{device_ref!r} and {runtime_ref!r}")

    observed_at = (observed or {}).get("observed_at")
    stale = is_stale(observed_at, reference, window)
    window_seconds = window if window is not None else freshness_seconds()

    # The dynamic half ages out; the declared half does not. Ageing a POSITIVE
    # into a claim about now is the one direction this could start lying, so an
    # aged observation stops being evidence rather than becoming weak evidence.
    current: dict | None = None
    if observed is not None:
        current = observed if not stale else {
            **observed,
            "features": {name: {**entry, "state": "UNKNOWN",
                                "evidence": _aged(entry.get("evidence"), observed_at)}
                         for name, entry in (observed.get("features") or {}).items()}}

    features = {}
    for name in feature_names():
        state, evidence = feature(runtime_id, name, current, runtime_ref=runtime_ref)
        features[name] = {
            "state": state,
            "evidence": evidence,
            "observed_at": None if state == "INCOMPATIBLE" else observed_at,
            "source": (observed or {}).get("source"),
        }

    if observed is None or stale:
        observation_state = "UNKNOWN"
        observation_because = _unobserved_because(
            observed, observed_at, window_seconds,
            unobserved_because if observed is None else None)
        surface = version = version_state = probe = None
        probe_state = "UNKNOWN"
    else:
        observation_state = observed.get("state", "UNKNOWN")
        observation_because = (None if observation_state == "OBSERVED"
                               else (observed.get("source")
                                     or "an observation was supplied with no time "
                                        "on it, so it says nothing about this device"))
        surface = observed.get("surface")
        version = observed.get("version")
        version_state = observed.get("version_state")
        probe = observed.get("probe")
        probe_state = ("OBSERVED" if version_state == "FACT"
                       else "UNKNOWN" if probe is None else "OBSERVED_NO_VERSION")

    record = {
        "schema": SCHEMA,
        "binding_ref": declaration.get("binding_ref") or binding_ref(device_ref,
                                                                     runtime_ref),
        "device_ref": device_ref,
        "runtime_ref": runtime_ref,
        "runtime_id": runtime_id,
        "declared_at": _as_text(declaration.get("declared_at")),
        "declared_provenance": declaration.get("declared_provenance"),
        "surface": surface,
        "surface_state": "OBSERVED" if surface else SURFACE_UNKNOWN,
        "observation": observation_state,
        "observation_because": observation_because,
        "observed_at": observed_at if not stale else None,
        "probe": probe,
        "probe_state": probe_state,
        "version": version,
        "version_state": version_state or "UNKNOWN",
        "freshness_window_seconds": window_seconds,
        "age_seconds": _age(observed_at, reference),
        "features": features,
        "provenance": {
            "declared": declaration.get("declared_provenance"),
            "observed": (observed or {}).get("source"),
            "observed_at": observed_at,
        },
    }
    record["levels"] = _levels(record)
    return record


def _aged(evidence: str | None, observed_at: str | None) -> str:
    suffix = f"aged out: last observed {observed_at}" if observed_at else "aged out"
    return f"{evidence} ({suffix})" if evidence else suffix


def _unobserved_because(observed, observed_at, window_seconds,
                        override: str | None = None) -> str:
    if observed is None:
        return override or (
            "no observation of this binding exists; nothing measured this "
            "device, which is not the same as measuring that it is absent")
    if observed.get("state") != "OBSERVED":
        return (observed.get("source")
                or "an observation was supplied with no time on it")
    return (f"the last observation is older than the freshness window "
            f"({window_seconds}s), so nothing in it is a statement about now")


def _levels(record: dict) -> dict:
    """The disclosure levels, attached to the record rather than to a UI.

    Three, and the split is the one the disclosure contract already uses: L1 is
    what costs nothing, L2 needs a device to have been reached, and L3 is how a
    reader audits the answer. A flat record would have made every future L1 read
    carry provenance it does not render.
    """
    bound = record["observation"] == "OBSERVED"
    return {
        "L1": {
            "binding_ref": record["binding_ref"],
            "runtime_ref": record["runtime_ref"],
            "state": "BOUND" if bound else "BOUND_UNOBSERVED",
        },
        "L2": {
            "surface": record["surface"],
            "surface_state": record["surface_state"],
            "features": {name: entry["state"]
                         for name, entry in record["features"].items()},
        },
        "L3": {
            "probe": record["probe"],
            "version": record["version"],
            "version_state": record["version_state"],
            "observed_at": record["observed_at"],
            "declared_at": record["declared_at"],
            "provenance": record["provenance"],
            "observation_because": record["observation_because"],
        },
    }


def list_bindings(registry, device_ref: str, *, observed=None,
                  reference: str | None = None, window: int | None = None,
                  unobserved_because: str | None = None) -> dict:
    """Every binding on one device, each with its observation if it has one.

    `observed` is keyed by runtime ref or by bare runtime id. A device with
    declarations and no observations returns bindings whose observation is
    `UNKNOWN`, which is the honest shape for a machine nobody reached: the
    binding exists.
    """
    declarations = declared_bindings(registry, device_ref)
    observations = observed or {}
    rows = []
    for declaration in declarations:
        runtime_ref = declaration.get("runtime_ref")
        entry = (observations.get(runtime_ref)
                 or observations.get(declaration.get("runtime_id")))
        rows.append(binding(declaration, observed=entry, reference=reference,
                            window=window, unobserved_because=unobserved_because))
    if rows:
        because = None
    elif device_ref in _device_refs(registry):
        because = ("the registry declares no runtime binding for this device; "
                   "this is a statement about the registry and not about the "
                   "machine")
    else:
        # Two ways to get an empty list, and collapsing them would make a
        # typo'd device ref read as a device nobody bound.
        because = (f"{device_ref!r} is not a device in this registry, so there "
                   "is nothing to ask about; this is not a device with no "
                   "bindings")
    return {
        "schema": SCHEMA,
        "device_ref": device_ref,
        "count": len(rows),
        "state": "BOUND" if rows else "NOT_DECLARED",
        "state_because": because,
        "bindings": rows,
    }


def _device_refs(registry) -> set[str]:
    refs = set()
    for device in devices_of(registry):
        ref = device.get("device_ref") or (
            f"device:{device.get('device_id')}" if device.get("device_id") else None)
        if ref:
            refs.add(ref)
    return refs


def refusal(record: dict, required_features) -> dict | None:
    """Should a call be refused for this binding's features? None means no.

    Ordered the way the resolver's inputs are: a definite no refuses, an
    unestablished answer makes the caller's result UNKNOWN rather than a no, and
    `INCOMPATIBLE` is a definite no about a family rather than about an install.
    The reason tokens belong to the caller; this returns the state and the
    sentence.
    """
    for name in required_features:
        entry = (record.get("features") or {}).get(name)
        if entry is None:
            return {"state": "UNKNOWN", "feature": name,
                    "detail": f"{name} is not in the feature vocabulary"}
        if entry["state"] == "UNAVAILABLE":
            return {"state": "INCOMPATIBLE", "feature": name,
                    "detail": f"this binding does not have {name}"
                              + (f": {entry['evidence']}"
                                 if entry.get("evidence") else "")}
        if entry["state"] == "INCOMPATIBLE":
            return {"state": "INCOMPATIBLE", "feature": name,
                    "detail": f"{name} is not an axis of {record.get('runtime_ref')}"}
        if entry["state"] == "UNKNOWN":
            return {"state": "UNKNOWN", "feature": name,
                    "detail": f"whether this binding has {name} was never established"}
    return None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--registry", required=True)
    parser.add_argument("--device", required=True)
    parser.add_argument("--observed", help="a JSON file of observations by runtime ref")
    args = parser.parse_args(argv)

    import yaml
    registry = yaml.safe_load(Path(args.registry).read_text(encoding="utf-8"))
    observed = (json.loads(Path(args.observed).read_text(encoding="utf-8"))
                if args.observed else None)
    json.dump(list_bindings(registry, args.device, observed=observed,
                            reference=now_utc()),
              sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
