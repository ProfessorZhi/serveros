#!/usr/bin/env python3
"""Decide whether one device can do one thing, right now, and why.

    python3 resolver.py --request request.json
    python3 resolver.py --ability ability:test-management --device device:server-beijing \
        --runtime claude-code --inputs inputs.json
    python3 resolver.py --ability 'ability:information-management#read' --device device:server-beijing \
        --runtime claude-code --inputs inputs.json

The last line quotes its argument for a reason that is easy to lose an hour to:
`#` starts a comment in every shell this runs in, so an unquoted
`ability:x#read` reaches the process as `ability:x`.

This is the ServerOS **Ability Resolver**. It is the third of the read-only
decision kernels, beside Context (what is my environment) and Space (where does
this belong). Ability answers "can this machine do this, and if not, why not".

    EffectiveAbility =  AbilityDefinition
                      ∩ DeviceCapabilities
                      ∩ DeviceProfile
                      ∩ DeviceBinding
                      ∩ AgentRuntimeCapabilities
                      ∩ RuntimeHealth
                      ∩ Policy
                      ∩ TaskContext

What this module is NOT
-----------------------
It does not read a file, run a probe, open a socket, look at a clock, or know
what a Skill or an MCP server is. Everything that touches the machine is a
PROBE, performed by the caller, and passed in as a record. That is not
fastidiousness — it is what makes the resolution of a three-device fleet
assertable in a unit test, and it is the discipline that makes the answer
reproducible.

The intersection is not a union, and the direction is the whole point. The
defect that produced this module was a capability reported as available because
one input mentioned it while seven others were never consulted. An intersection
makes "no" the default and requires every input to agree before the answer is
"yes".

UNKNOWN is a real answer
------------------------
`UNKNOWN` and `UNAVAILABLE` call for opposite actions — go and resolve the
missing input, versus find another provider — so they are never collapsed. But
a definite refusal outranks an indefinite yes: if an input cannot be read and a
LATER input refuses, the refusal is the answer. Otherwise a device with nothing
installed reports UNKNOWN for everything, which reads as "we have not looked"
and hides the finding that nothing is there.

An operation is a property of the reference, not a ninth input
--------------------------------------------------------------
`ability:information-management#read` names one ability AND one thing to do
with it. The operation is decided by what the provider declares about itself
(`provider-contract.yaml#operations`), so it is a provider-intrinsic check like
the others — but it is NOT one of the eight inputs, and it is deliberately not
made into one:

  * The eight inputs are consulted against the DEVICE. An operation is not a
    device fact. Adding a ninth would change `inputs_seen` — the field whose
    whole job is to say how much of the intersection was actually evaluated —
    into a list of nine things where one is not of the same kind.
  * A bare `ability:x` never consults the operation table. If it did, every
    provider that declares no `operations:` would return UNKNOWN for ordinary
    work that resolves today, which is a retraction dressed as a refinement.

Three states, copied verbatim from `target.py#_support_state` and asymmetric on
purpose: a provider that declares no `operations:` key has NOT been asked
(UNKNOWN); a provider that declares a table without the verb has said no
(UNSUPPORTED). Absent from a published closed list is a no; a missing list is
nobody asked.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCHEMA = "serveros/capability-resolution.v1"

STATUSES = ("AVAILABLE", "DEGRADED", "UNAVAILABLE", "INCOMPATIBLE", "UNKNOWN")

REASONS = (
    "AGENT_UNSUPPORTED",
    "DEVICE_DEPENDENCY_MISSING",
    "RUNTIME_UNHEALTHY",
    "POLICY_DENIED",
    "NETWORK_UNAVAILABLE",
    "PROVIDER_NOT_INSTALLED",
    # The seventh, and the only one here that the provider contract does NOT
    # declare — deliberately. `provider_instance.REASONS` is asserted equal to
    # `provider-contract.yaml#status.reasons` by a test, and that vocabulary is
    # about whether a provider is THERE and ANSWERS. "This provider does not
    # implement this operation" is neither: the provider is installed and
    # healthy, and it will not do this thing. `provider-contract.yaml` says so
    # where it declares `operations:`, and the word itself is the one
    # `target-contract.yaml#reasons.values` has carried since an Agent Control
    # request could name an action: it is reused, not minted here.
    #
    # Both the definite (UNSUPPORTED) and the indefinite (no table) case refuse
    # with this word, and that is not a collapse. The distinction lives in the
    # candidate's `operations_state` and in the candidate's status —
    # INCOMPATIBLE versus UNKNOWN — exactly as `target-contract.yaml` rules for
    # the same axis one plane over, where the word is coarse on purpose because
    # a closed vocabulary has no word for "nobody asked".
    "ACTION_UNSUPPORTED",
)

# The operation axis. `SUPPORTED` never appears as a refusal — it is the value
# the axis returns when the verb is declared, and it is named here so the three
# states can be asserted as a closed set rather than compared as strings.
OPERATION_STATES = ("SUPPORTED", "UNSUPPORTED", "UNKNOWN")

# The reference separator. `ability:<kebab-id>#<verb>`; the bare form stays
# legal and means the ability without naming an operation.
OPERATION_SEPARATOR = "#"

# The eight inputs, in evaluation order. Order is what the REASON depends on:
# any input may refuse, and the first to refuse names why. Cheap, local and
# unambiguous inputs are first, so the common refusal is also the clear one.
INPUT_ORDER = (
    "ability",
    "device_capability",
    "device_profile",
    "device_binding",
    "agent_runtime",
    "runtime_health",
    "policy",
    "task_context",
)


class Rejected(ValueError):
    """The request cannot be answered — as opposed to "the answer is no"."""


# ---------------------------------------------------------------------------
# The reference. One syntax, one parser, and it lives here because this module
# owns what an ABILITY reference is. `target.py` calls into this one rather than
# the other way round, so there is exactly one reading of `#` in an ability
# reference.
#
# Stated that narrowly because it is not a tree-wide invariant and should not
# read as one: `context/resolver.py:297` also partitions on `#`, for a SPACE
# locator with a fragment — a different reference family with a different head,
# and folding the two together is not a refactor this file may make.
#
# No regular expressions: this module may import `argparse`, `json`, `sys` and
# `pathlib` and nothing else, which a test enforces. The grammar is one
# separator and a character class, so a regex would be a dependency bought for
# nothing.
# ---------------------------------------------------------------------------

def _is_operation_word(text: str) -> bool:
    """kebab-case: lowercase ASCII letters, digits, single internal hyphens.

    A SHAPE rule, not a vocabulary. There is deliberately no server-side list of
    legal operations: the verbs belong to the providers that implement them, and
    a resolver that owned a verb list would be a second answer to a question the
    provider records already answer. This only refuses things that cannot be a
    verb at all — `#Read`, `#read_me`, `#-read` — so a caller learns they
    mistyped rather than learning that every provider lacks an operation nobody
    could have declared.

    ASCII is spelled out rather than left to `str.islower`, because `islower` is
    true for `é` and for a surprising amount of Unicode — and the contract says
    which characters may appear in an identifier, not which ones happen to have
    a case. `"²".isdigit()` is likewise true, so digits are bounded the same way.
    """
    if not text or text.startswith("-") or text.endswith("-"):
        return False
    if "--" in text:
        return False
    return all(ch.isascii() and (ch.isdigit() or ch.islower() or ch == "-")
               for ch in text)


def ability_ref(ability: str, operation: str | None = None) -> str:
    """Compose a reference. The inverse of `parse_ability_ref`."""
    if not ability:
        raise Rejected("an ability reference needs an ability")
    if operation is None:
        return ability
    if not _is_operation_word(operation):
        raise Rejected(
            f"{operation!r} is not an operation: operations are kebab-case, "
            "and there is no server-side list of them to look one up in")
    return f"{ability}{OPERATION_SEPARATOR}{operation}"


def parse_ability_ref(text: str) -> tuple[str, str | None]:
    """`(ability, operation)` from `ability:<id>[#<verb>]`.

    The bare form returns the text UNCHANGED as the ability. That is not
    leniency — every caller and every fixture in the product writes
    `ability:<kebab-id>` and none of them names an operation, and a parser that
    normalised those would be rewriting inputs it was not asked about.

    Only the first separator splits, and a second one is a rejection rather than
    a verb that happens to contain `#`. Splitting at the last separator would
    accept `ability:x#a#b` and silently drop `a`, which is how a typo becomes a
    resolution about something the caller never asked for.
    """
    if not isinstance(text, str) or not text:
        raise Rejected("request.ability is required")
    head, separator, tail = text.partition(OPERATION_SEPARATOR)
    if not separator:
        return text, None
    if OPERATION_SEPARATOR in tail:
        raise Rejected(
            f"{text!r} names more than one operation; a reference is "
            "`ability:<id>#<verb>` and nothing after the verb")
    if not head:
        raise Rejected(f"{text!r} names no ability")
    if not _is_operation_word(tail):
        raise Rejected(
            f"{tail!r} in {text!r} is not an operation: operations are "
            "kebab-case, and there is no server-side list of them to look one "
            "up in")
    return head, tail


# ---------------------------------------------------------------------------
# Helpers. Small, pure, and named after the contract line each implements.
# ---------------------------------------------------------------------------

def _operation_state(provider: dict, operation: str) -> tuple[str, str]:
    """(SUPPORTED | UNSUPPORTED | UNKNOWN, detail) for one provider.

    `provider-contract.yaml#operations`. Declared, so a list of verbs; the
    asymmetry between the first two returns is the whole point and is copied
    from `target.py#_support_state`, which arrived at it against an observed
    table — the same shape, one axis over.
    """
    table = provider.get("operations")
    if table is None:
        return ("UNKNOWN",
                "the provider declares no operations table, so whether it "
                f"implements {operation} has not been asked")
    if not isinstance(table, list):
        return ("UNKNOWN",
                "the provider's operations table is not a list of verbs, so it "
                f"does not answer whether {operation} is one of them")
    declared = [str(verb) for verb in table]
    if operation not in declared:
        # A published closed list with a hole in it is a "no" for the hole.
        return ("UNSUPPORTED",
                f"{operation} is not among the {len(declared)} operations this "
                "provider declares")
    return "SUPPORTED", f"the provider declares {operation}"


def _path_role_bound(profile: dict | None, role: str) -> bool:
    """Is `role` BOUND on this device?

    Three states, three meanings, and only one of them is "yes". UNBOUND means
    the role applies here and no path is known — a real gap. NOT_APPLICABLE
    means the role has no meaning on this device. Neither is a bound path, and
    collapsing them would turn "nobody has looked" into "there is nothing here".
    """
    if not profile:
        return False
    entry = (profile.get("paths") or {}).get(role)
    return bool(entry) and entry.get("state") == "BOUND" and entry.get("path")


def _refusal(status: str, reason: str, detail: str, **extra) -> dict:
    record = {"status": status, "reason": reason, "detail": detail}
    record.update(extra)
    return record


def _provider_materializable(provider: dict, profile: dict | None) -> tuple[bool, str]:
    """input 3 — DeviceProfile. Where would it be, and is that location known?

    A provider declaring no on-disk footprint (`path_role: none`: a builtin or
    a remote service) has nothing to bind and passes here. Everything else must
    name a role that resolves to a BOUND path on this device.
    """
    materialization = provider.get("materialization") or {}
    role = materialization.get("path_role")
    if role in (None, "none"):
        return True, "the provider declares no on-disk footprint"
    if _path_role_bound(profile, role):
        return True, f"{role} is BOUND on this device"
    return False, (f"path role {role!r} is not BOUND in this device's profile, "
                   "so where the provider would live is not known here")


def _instance_for(instances, provider_id: str, device_ref: str | None) -> dict | None:
    for instance in instances or []:
        if instance.get("provider") != provider_id:
            continue
        # A device-scoped resolution must find a device-scoped instance. An
        # instance with no device_ref is not a match for a device we asked
        # about: it is an observation from somewhere, and reading it as this
        # device's is how one machine's capability becomes another's claim.
        if device_ref is not None and instance.get("device_ref") != device_ref:
            continue
        return instance
    return None


# ---------------------------------------------------------------------------
# Per-input evaluation. Each returns either None (input agreed) or a refusal.
# ---------------------------------------------------------------------------

def _input_ability(request: dict, ability: str) -> dict | None:
    """input 1 — the pinned catalogue.

    Takes the ability as an argument rather than reading `request["ability"]`,
    because that key may carry an operation (`ability:x#read`) and the catalogue
    is keyed by the bare ability. Looking up the compound string would make
    every operation-naming request a catalogue miss — a refusal about the
    catalogue, produced by a question about a verb.

    The catalogue is a statement about abilities, not about verbs: an operation
    is resolved against the provider that implements it, one layer down.
    """
    catalogue = request.get("abilities") or {}
    if ability not in catalogue:
        return _refusal(
            "UNAVAILABLE", "PROVIDER_NOT_INSTALLED",
            f"{ability} is not in the pinned catalogue; a documented ability is "
            "not an available one")
    return None


def _input_device_capability(request: dict, provider: dict) -> dict | None:
    required = provider.get("requirements") or {}
    device = request.get("device_capabilities") or {}
    if not required:
        return None
    if not device:
        return None  # unevaluable; the caller records it in inputs_missing
    missing = sorted(k for k, v in required.items() if device.get(k) != v)
    if missing:
        reason = "NETWORK_UNAVAILABLE" if "network" in missing else "DEVICE_DEPENDENCY_MISSING"
        return _refusal(
            "UNAVAILABLE", reason,
            f"this device does not satisfy {', '.join(missing)}",
            missing=missing)
    return None


def _input_agent_runtime(request: dict, provider: dict) -> dict | None:
    runtime = request.get("runtime_record")
    if runtime is None:
        return None  # unevaluable
    consumable = runtime.get("consumable_provider_kinds") or []
    if provider.get("kind") not in consumable:
        return _refusal(
            "INCOMPATIBLE", "AGENT_UNSUPPORTED",
            f"runtime {runtime.get('id')!r} cannot consume a {provider.get('kind')!r} provider")
    return None


def _input_runtime_health(request: dict, provider: dict) -> dict | None:
    health = (request.get("health") or {}).get(provider["id"])
    if health is None:
        return None  # unevaluable
    if health.get("functional") == "FAILED":
        return _refusal(
            "UNAVAILABLE", "RUNTIME_UNHEALTHY",
            "the provider is installed and fails its functional probe")
    return None


def _input_policy(request: dict, ability: str) -> dict | None:
    policy = request.get("policy")
    if policy is None:
        return None
    denied = set(policy.get("denied_abilities") or ())
    if ability in denied or "*" in denied:
        return _refusal(
            "UNAVAILABLE", "POLICY_DENIED",
            f"policy denies {ability} on this device or for this Item")
    return None


def _evaluate_one(request: dict, ability: str, provider: dict,
                  operation: str | None = None) -> dict:
    """One provider, through the eight inputs in order. First refusal wins.

    `ability` is not evaluated here — it is not a property of a provider, and
    `resolve()` settles it once before any provider is considered. The loop
    still names it, because the ORDER is the contract and skipping a name in
    the loop is how a future edit reorders the rest without noticing.

    An input nobody supplied is not an input that agreed
    ----------------------------------------------------
    This function used to `continue` past every absent input and then, finding
    no refusal, return AVAILABLE. The consequence was measured rather than
    argued: a request carrying only a catalogue entry and a provider declaring
    the ability came back `AVAILABLE` with `inputs_missing` listing seven of
    the eight inputs. `summarize` rendered it as "available via p1".

    That is verbatim the defect this module's own docstring says it was written
    to fix — "a capability reported as available because one input mentioned it
    while seven others were never consulted" — reintroduced one level down. The
    intersection was enforced between the inputs that happened to be present.

    So the inputs that could have refused are now tracked. If none refused and
    any DECISIVE input was never consulted, the honest answer is UNKNOWN: not
    "yes", and not a fabricated "no", but a statement that this could not be
    decided and a naming of what to go and supply.

    UNKNOWN was previously unreachable — declared in `STATUSES`, rendered by
    `summarize`, counted by `count_abilities` as `undecided`, and produced by
    no input at all, because a candidate could only be a refusal or a yes. It
    is now the answer for the case that has no answer.

    The operation, when one was named
    ---------------------------------
    A verb the provider does not implement is a DEFINITE refusal, so it returns
    immediately: the device may or may not have everything else, and it does
    not matter, because this provider will never do this thing. A provider that
    publishes no operations table is an INDEFINITE answer, and it deliberately
    does NOT return here — it is remembered and falls through to the eight
    inputs, because returning early would let "nobody asked about the verb"
    mask a later, definite "this device cannot do it". The definite refusal
    still outranks it. If everything else agrees, `operation_unasked` is what
    makes the answer UNKNOWN instead of AVAILABLE.
    """
    unevaluated = []
    operation_unasked = None

    if operation is not None:
        state, detail = _operation_state(provider, operation)
        if state == "UNSUPPORTED":
            return _refusal(
                "INCOMPATIBLE", "ACTION_UNSUPPORTED", detail,
                refused_by="operation", operations_state=state)
        if state == "UNKNOWN":
            operation_unasked = detail

    for name in INPUT_ORDER:
        if name == "ability":
            continue
        if name == "device_capability":
            # Decisive only when the provider declares requirements. A provider
            # that requires nothing of the device has nothing to check here, so
            # an absent capability record is agreement rather than ignorance —
            # and marking it unevaluated would make every provider that names
            # no dependencies permanently UNKNOWN, which is its own way of
            # saying nothing.
            if not request.get("device_capabilities"):
                if provider.get("requirements"):
                    unevaluated.append(name)
                continue
            refusal = _input_device_capability(request, provider)
        elif name == "device_profile":
            ok, detail = _provider_materializable(provider, request.get("profile"))
            refusal = None if ok else _refusal(
                "UNAVAILABLE", "PROVIDER_NOT_INSTALLED", detail)
        elif name == "device_binding":
            if request.get("instances") is None:
                # The sharpest of the six. Not supplying the instance list is
                # not a statement that the provider is installed here, and
                # reading it as one is how a device claims a capability on the
                # strength of a provider catalogue entry.
                unevaluated.append(name)
                continue
            instance = _instance_for(request.get("instances"), provider["id"],
                                     request.get("device_ref"))
            if instance is None:
                refusal = _refusal(
                    "UNAVAILABLE", "PROVIDER_NOT_INSTALLED",
                    "no instance of this provider is registered on this device")
            else:
                refusal = None
        elif name == "agent_runtime":
            if request.get("runtime_record") is None:
                unevaluated.append(name)
                continue
            refusal = _input_agent_runtime(request, provider)
        elif name == "runtime_health":
            # NOT decisive, and the contract says why: `costs.capability.L0` is
            # "one manifest read, no probes". Health is a probe. If absence
            # here made a candidate UNKNOWN, capability resolution would
            # require live health on every call — and the first caller to hit
            # that would pass `{}` to make it stop, which is worse than the
            # original defect: agreement that was never asked for, wearing the
            # shape of an input that was consulted.
            refusal = _input_runtime_health(request, provider)
        elif name == "policy":
            # Same argument. Policy is a filter over an answer, not a
            # capability input, and the contract prices authority L0 at
            # "none". A resolver that could not answer without it would have
            # made policy a prerequisite for knowing anything.
            refusal = _input_policy(request, ability)
        else:  # task_context
            # Declared in the contract and not yet implemented: there is no
            # evaluator, so this input can never refuse and must not be
            # counted as unevaluated either — counting it would make every
            # resolution UNKNOWN and the status would stop meaning anything.
            # Named here so the gap is visible rather than implied.
            refusal = None

        if refusal is not None:
            refusal["refused_by"] = name
            return refusal

    preferred = provider.get("preference", 100)

    if unevaluated or operation_unasked is not None:
        if unevaluated:
            detail = ("no input refused, and these were never consulted: "
                      + ", ".join(unevaluated))
        else:
            detail = f"no input refused, and {operation_unasked}"
        return _refusal(
            "UNKNOWN", "ACTION_UNSUPPORTED" if operation_unasked else None,
            detail,
            unevaluated=unevaluated, preference=preferred,
            operations_state="UNKNOWN" if operation_unasked else None)

    # -- every decisive input was consulted and none refused. ----------------
    caveats = []
    instance = _instance_for(request.get("instances"), provider["id"],
                             request.get("device_ref"))
    if instance and instance.get("floating_dependency"):
        caveats.append(_refusal(
            "DEGRADED", "RUNTIME_UNHEALTHY",
            "the provider resolves through an unpinned dependency, so it is "
            "not the same provider tomorrow",
            finding="PERSISTENT_RUNTIME_FLOATING_DEPENDENCY"))

    # A checked-and-agreed operation is recorded, not merely passed. The reader
    # of a resolution that came back AVAILABLE is entitled to know whether the
    # verb was part of what was checked or was never in question.
    agreed = "SUPPORTED" if operation is not None else None
    if caveats:
        return {**caveats[0], "preference": preferred, "operations_state": agreed}
    return {"status": "AVAILABLE", "reason": None, "detail": None,
            "preference": preferred, "operations_state": agreed}


# ---------------------------------------------------------------------------
# The public entry point.
# ---------------------------------------------------------------------------

def resolve(request: dict) -> dict:
    """Resolve one ability for one device, runtime and moment.

    Returns the record shape declared in `abilities/capability-contract.yaml`.
    Raises `Rejected` only for a malformed request — "no" is an answer, not an
    error, and it is returned rather than raised.
    """
    for field in ("ability", "device_ref", "runtime"):
        if not request.get(field):
            raise Rejected(f"request.{field} is required")

    # §10 — split the reference once, here, and never again. Everything below
    # works with the BARE ability, because that is the catalogue key, the key
    # providers declare, and the identity that must not change when a caller
    # asks about a verb. The operation travels beside it.
    ability, operation = parse_ability_ref(request["ability"])
    providers = [p for p in (request.get("providers") or [])
                 if p.get("ability") == ability]

    record = {
        "schema": SCHEMA,
        "ability": ability,
        "operation": operation,
        "device_ref": request["device_ref"],
        "runtime": request["runtime"],
        "resolved_at": request.get("resolved_at"),
        "inputs_seen": [],
        "inputs_missing": [],
    }

    # Which of the eight were readable at all. Recorded whether or not they
    # decided anything, because a confident answer computed from the inputs
    # that happened to be readable is the failure this field exists to expose.
    for name, present in (
        ("ability", bool(request.get("abilities"))),
        ("device_capability", bool(request.get("device_capabilities"))),
        ("device_profile", request.get("profile") is not None),
        ("device_binding", request.get("instances") is not None),
        ("agent_runtime", request.get("runtime_record") is not None),
        ("runtime_health", bool(request.get("health"))),
        ("policy", request.get("policy") is not None),
        ("task_context", request.get("task_context") is not None),
    ):
        (record["inputs_seen"] if present else record["inputs_missing"]).append(name)

    # The ability itself is settled once, before any provider is considered.
    # It is not a property of a provider: a provider for an ability that is not
    # in the pinned catalogue is a provider for something that does not exist,
    # and the finding is about the catalogue, not about the provider.
    catalogue_refusal = _input_ability(request, ability)
    if catalogue_refusal is not None:
        return {**record, **catalogue_refusal, "candidates": []}

    if not providers:
        return {**record, "status": "UNAVAILABLE",
                "reason": "PROVIDER_NOT_INSTALLED",
                "detail": f"no provider implements {ability}",
                "candidates": []}

    candidates = []
    for provider in sorted(providers, key=lambda p: (p.get("preference", 100), p["id"])):
        outcome = _evaluate_one(request, ability, provider, operation)
        candidates.append({
            "provider": provider["id"],
            "status": outcome["status"],
            "reason": outcome.get("reason"),
            "detail": outcome.get("detail"),
            "refused_by": outcome.get("refused_by"),
            "unevaluated": outcome.get("unevaluated", []),
            "finding": outcome.get("finding"),
            "preference": outcome.get("preference", provider.get("preference", 100)),
            # Per candidate, and absent when no operation was named — which is
            # the whole rule: a bare `ability:x` never consults the table, so it
            # has no state to report and must not be given a fabricated one.
            "operations_state": outcome.get("operations_state"),
        })

    usable = [c for c in candidates if c["status"] in ("AVAILABLE", "DEGRADED")]
    if usable:
        chosen = usable[0]
        return {**record,
                "status": chosen["status"],
                "reason": chosen["reason"],
                "detail": chosen["detail"],
                "provider": chosen["provider"],
                "finding": chosen.get("finding"),
                "candidates": candidates}

    # Nothing was usable. Prefer a definite refusal over an indefinite one, in
    # the contract's order: a definite "no" outranks "we could not tell".
    definite = [c for c in candidates if c["status"] != "UNKNOWN"]
    if definite:
        chosen = definite[0]
        return {**record,
                "status": chosen["status"],
                "reason": chosen["reason"],
                "detail": chosen["detail"],
                "candidates": candidates}

    # Every candidate was undecidable and none refused. The detail names the
    # inputs the PROVIDERS could not see, which is narrower and more useful
    # than the record's `inputs_missing`: the latter is about the request, and
    # this is about what stood in the way of a decision.
    #
    # `reason` stays None even when every candidate carries one, and that is a
    # ruling copied rather than a convenience. `abilities/target-contract.yaml`
    # settled the same axis one plane over: an UNEVALUABLE verdict is not a
    # REFUSAL, so the word does not reach `record.reason` — it appears only on
    # that step's own entry, which here is the candidate. Letting
    # ACTION_UNSUPPORTED mean "refused" on one record and "nobody asked" on
    # another is one word with two meanings at one field, and a consumer that
    # switches on `reason` would read the second as the first.
    unconsulted = sorted({name for c in candidates for name in c["unevaluated"]})
    return {**record, "status": "UNKNOWN", "reason": None,
            "detail": ("no provider could be resolved and none refused; "
                       "unconsulted inputs: " + (", ".join(unconsulted) or "none")),
            "candidates": candidates}


def summarize(record: dict) -> str:
    """One line, for a disclosure surface. Never a paragraph.

    The verb is part of the question, so it is part of the line. Rendering a
    resolution of `ability:x#read` as `x` would report an answer to a question
    nobody asked, and those two answers are allowed to differ — that is the
    entire reason the operation axis exists.
    """
    ref = ability_ref(record["ability"], record.get("operation"))
    if record["status"] == "AVAILABLE":
        return f"{ref}: available via {record.get('provider')}"
    if record["status"] == "UNKNOWN":
        # An UNKNOWN has no `reason` — see the ruling at the end of `resolve()`.
        # So when a verb was named and no provider was asked about it, the cause
        # is read from the candidates, where the word legitimately lives.
        # Printing `inputs_missing` instead would name the wrong cause and send
        # the caller to supply a record that would not have changed the answer.
        unasked = [c["provider"] for c in record.get("candidates") or ()
                   if c.get("operations_state") == "UNKNOWN"]
        if record.get("operation") and unasked:
            return (f"{ref}: unknown (no provider was asked whether it "
                    f"implements {record['operation']})")
        return (f"{ref}: unknown "
                f"({', '.join(record['inputs_missing']) or 'no inputs'})")
    return f"{ref}: {record['status'].lower()} ({record.get('reason')})"


def count_abilities(requests: list[dict]) -> dict:
    """The capability L0 disclosure: a number, and how many could not be decided.

    Runs no probe — every request carries records the caller already gathered —
    because a number that costs a probe is a number a session stops asking for.
    """
    statuses: dict[str, int] = {}
    for request in requests:
        record = resolve(request)
        statuses[record["status"]] = statuses.get(record["status"], 0) + 1
    return {
        "total": len(requests),
        "by_status": statuses,
        "usable": statuses.get("AVAILABLE", 0) + statuses.get("DEGRADED", 0),
        "undecided": statuses.get("UNKNOWN", 0),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--request", help="a JSON file holding one request")
    parser.add_argument("--ability")
    parser.add_argument("--device")
    parser.add_argument("--runtime", default="claude-code")
    parser.add_argument("--inputs", help="a JSON file holding the input records")
    args = parser.parse_args(argv)

    if args.request:
        request = json.loads(Path(args.request).read_text(encoding="utf-8"))
    else:
        if not (args.ability and args.device):
            parser.error("--ability and --device are required without --request")
        request = {"ability": args.ability, "device_ref": args.device,
                   "runtime": args.runtime}
        if args.inputs:
            request.update(json.loads(Path(args.inputs).read_text(encoding="utf-8")))

    try:
        record = resolve(request)
    except Rejected as exc:
        print(json.dumps({"error": str(exc)}, indent=2), file=sys.stderr)
        return 2

    json.dump(record, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
