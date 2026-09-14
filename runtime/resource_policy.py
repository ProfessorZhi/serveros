#!/usr/bin/env python3
"""§22 · A governance document, read as a policy input rather than obeyed.

The change this module makes
----------------------------
`RESOURCE-GOVERNANCE.yaml` began life as a cleanup DECISION document: it says
which resources may be deleted, in what order, and under what conditions. That
is a perfectly good document and it is not what §22 asks for. §22 asks for a
policy INPUT — a formal declaration ServerOS reads, on which the observation
layer can rely, and which it can never mistake for an instruction.

The difference is not stylistic. A decision document is read once, by the
person executing it, and it says what to do. A policy input is read every
tick, by code, and it says what the world is like. The same file can serve
both, and the half that has to change is on the reading side.

So this module reads a governance document and produces four things, which are
§22's closed list:

    capacity_thresholds    numbers a detector compares a measurement against
    protection_rules       what an owner said must not be reclaimed
    retention_metadata     when a decision becomes available, and its gates
    classification_rules   the vocabulary the document uses for "what is this"

What it does NOT produce
------------------------
No action. There is no function in this file with a verb in it, and
`destructive_reconcile` is a REPORT: the contract sets it `false`, and a
document that asks for reconciliation appears under
`destructive_reconcile.requested_but_not_enabled` with the ask quoted. There
is no input to this module that changes that field.

Absence is a finding
--------------------
Three states, kept apart, because collapsing them is how a governance file
becomes a permission:

    DECLARED    the document names this resource, and says something about it
    NOT_DECLARED the document was read and does not mention it
    UNREADABLE  no document was read at all

`NOT_DECLARED` is not `allowed`. A resource a governance document does not
mention has no protection FROM THAT DOCUMENT, which is not the same as having
no protection: the registry, the device policy and the container labels are
other sources §17 already names, and this module adds one input to that set
rather than replacing it. The reverse error is the same size and easier to
make — reading `deny` for a resource that no longer exists as protection of
something — so a rule naming a resource the host does not have is reported as
`DANGLING` rather than counted.

Reused, not rebuilt
-------------------
The vocabulary and the boundary live in `resource-governance-contract.yaml`.
This module is the reader. A test asserts every field it reads is declared
there, so the two cannot drift into the state where the contract describes a
policy input nobody parses.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import yaml

_HERE = Path(__file__).resolve().parent


def _load(name: str, filename: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _HERE / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


anomaly = _load("serveros_runtime_anomaly", "anomaly.py")

CONTRACT = _HERE / "resource-governance-contract.yaml"

# The canonical location of the document as of this round. Recorded in the
# contract too, and read from there rather than restated here — a path in two
# places is a path that will be wrong in one of them.
def default_location() -> str | None:
    return (contract().get("current_document") or {}).get("location")


def contract() -> dict:
    try:
        return yaml.safe_load(CONTRACT.read_text()) or {}
    except OSError:
        return {}


def input_classes() -> tuple:
    """The four §22 names, from the contract. The closed list."""
    return tuple((contract().get("inputs") or {}).keys())


# ---------------------------------------------------------------------------
# Reading the document
# ---------------------------------------------------------------------------

# `cleanup_policy` values that mean an owner said no, in a strength order.
# `deny` is the only one that maps to `protected`: the others are "not now",
# and promoting "not now" to "never" is how a temporary block becomes a
# permanent one nobody remembers writing.
PROTECTIVE_POLICIES = {
    "deny": "protected",
    "blocked": "blocked",
    "blocked_until_retention_window": "blocked",
    "blocked_no_formal_mechanism": "blocked",
    "defer": "deferred",
}
# A policy value nothing here recognises is NOT read as permission. It is
# carried through as UNRECOGNISED, which is the safe direction: the failure
# mode of guessing wrong here is a resource that gets reclaimed because a
# word was misspelled.
UNRECOGNISED = "UNRECOGNISED"


def _entries(document: dict) -> list[tuple[str, dict]]:
    """The document's resources, as (name, body) pairs.

    Handles the `resources:` mapping the current document uses. A document
    with no `resources:` key is not an empty governance file — it is a file
    this reader does not understand, and the caller says so.
    """
    resources = document.get("resources")
    if not isinstance(resources, dict):
        return []
    return [(name, body if isinstance(body, dict) else {})
            for name, body in resources.items()]


def _protection_rule(name: str, body: dict) -> dict:
    policy = body.get("cleanup_policy")
    level = PROTECTIVE_POLICIES.get(policy, UNRECOGNISED if policy else UNRECOGNISED)
    return {
        "resource": name,
        "cleanup_policy": policy,
        "level": level,
        "lifecycle": body.get("lifecycle"),
        "owner": body.get("owner"),
        "criticality": body.get("criticality"),
        "role": body.get("role"),
        # Read as well as the level. A protection whose mechanism is a
        # container reference lapses when the container is removed, and the
        # document states that correctly for the Gold rollback anchor —
        # carrying the mechanism through is what lets that fact travel.
        "mechanism": (body.get("protection") or {}).get("mechanism")
        if isinstance(body.get("protection"), dict) else None,
        "removal_preconditions": body.get("removal_preconditions"),
        "source": "governance-document",
        "provenance": "FACT",
        "why": (
            "a declaration, not a measurement: it says what an owner decided "
            "about this resource, and it does not say the resource exists"
        ),
    }


def _retention_row(name: str, body: dict) -> dict | None:
    stopped = body.get("stopped_at")
    eligible = body.get("earliest_removal_eligibility")
    preconditions = body.get("removal_preconditions")
    if not (stopped or eligible or preconditions):
        return None
    return {
        "resource": name,
        "stopped_at": stopped,
        "earliest_removal_eligibility": eligible,
        "removal_preconditions": (
            [preconditions] if isinstance(preconditions, str) else preconditions),
        "source": "governance-document",
        "note": (
            "eligibility is a TIMESTAMP plus its gates, which is the shape a "
            "single age never has. `stopped > N days` cannot be the variable "
            "behind CLEANUP_CANDIDATE (§16), and a document that states a "
            "date and a re-check list has already said so"
        ),
    }


def _classifications(document: dict) -> dict:
    """The vocabulary the document uses, taken from the document itself.

    Derived from the entries rather than transcribed from the contract: the
    contract records what the 2026-09-13 revision used, and a later revision
    that adds a word must show up here without an edit. The contract's list is
    then a claim about the document, and a test compares the two.
    """
    lifecycles: dict[str, int] = {}
    policies: dict[str, int] = {}
    for _name, body in _entries(document):
        lifecycle = body.get("lifecycle")
        if lifecycle:
            lifecycles[lifecycle] = lifecycles.get(lifecycle, 0) + 1
        policy = body.get("cleanup_policy")
        if policy:
            policies[policy] = policies.get(policy, 0) + 1
    return {"lifecycle": lifecycles, "cleanup_policy": policies}


def _capacity_thresholds(document: dict) -> dict:
    """Numbers a detector could compare against, if the document has any.

    Only a top-level `thresholds:` mapping is read, and nothing else is
    inferred from a size. `29.72GB` is a measurement of one buildkit instance;
    a threshold is a policy about all of them, and reading the first as the
    second would silently adopt one host's state as global policy.
    """
    declared = document.get("thresholds")
    if not isinstance(declared, dict) or not declared:
        return {}
    known = {key: value for key, value in declared.items()
             if key in anomaly.DEFAULT_THRESHOLDS}
    return known


def _unread_keys(document: dict) -> list[str]:
    """Top-level keys this reader did not use, so the ignoring is visible."""
    read = {"schema", "revision", "resources", "thresholds", "generated_at",
            "previous_revision", "scope", "authorization", "batch2_outcome",
            "removed", "forbidden_in_batch2", "confirmed_no_deletion"}
    return sorted(key for key in document if key not in read)


# ---------------------------------------------------------------------------

def read(path=None) -> dict:
    """The document, with the three-way absence answer §22 needs."""
    location = path or default_location()
    if not location:
        return {"state": "ABSENT", "location": None, "document": None,
                "why": "no governance document location is declared in the contract"}
    target = Path(location)
    if not target.exists():
        return {"state": "ABSENT", "location": str(target), "document": None,
                "why": (f"no governance document at {target}. An absent policy "
                        f"input is not an empty one: nothing here establishes "
                        f"any protection, and nothing here withdraws any")}
    try:
        document = yaml.safe_load(target.read_text()) or {}
    except (OSError, yaml.YAMLError) as exc:
        return {"state": "UNREADABLE", "location": str(target), "document": None,
                "why": f"{type(exc).__name__}: {exc}"}
    return {"state": "DECLARED", "location": str(target), "document": document,
            "why": None}


def resolve(path=None) -> dict:
    """§22. The four input classes, plus the boundary, plus what was ignored.

    The cross-check against what the host actually has is a separate call —
    `protection_input(payload, known_resources=...)` — because this function
    must not need the host in order to answer, and a resolver that reached for
    one would be the MCP layer doing its own measuring (§28) one step earlier.
    """
    read_result = read(path)
    contract_doc = contract()
    boundary = dict(contract_doc.get("destructive_reconcile") or {})
    boundary.setdefault("enabled", False)
    payload = {
        "state": read_result["state"],
        "location": read_result["location"],
        "why": read_result["why"],
        "schema": None,
        "revision": None,
        "capacity_thresholds": {},
        "protection_rules": [],
        "retention": [],
        "classification_rules": {"lifecycle": {}, "cleanup_policy": {}},
        "unread_keys": [],
        "destructive_reconcile": boundary,
        "advisory": (
            "Everything here is a DECLARATION. The host is the fact. A rule "
            "in this payload does not establish that its resource exists, and "
            "a resource missing from this payload is not thereby unprotected "
            "— the registry, the device policy and the container labels are "
            "other sources, and this is one more input beside them."
        ),
    }
    document = read_result.get("document")
    if not document:
        # Not an empty policy. The four classes are reported as what they are
        # — unestablished — rather than as empty collections a caller would
        # iterate over and find nothing in.
        for name in input_classes():
            payload[f"{name}_state"] = "UNESTABLISHED"
        payload["protective_names"] = []
        payload["dangling_rules"] = []
        return payload

    payload["schema"] = document.get("schema")
    payload["revision"] = document.get("revision")
    payload["capacity_thresholds"] = _capacity_thresholds(document)
    payload["protection_rules"] = [_protection_rule(name, body)
                                   for name, body in _entries(document)]
    payload["retention"] = [row for row in
                            (_retention_row(name, body)
                             for name, body in _entries(document)) if row]
    payload["classification_rules"] = _classifications(document)
    payload["unread_keys"] = _unread_keys(document)

    payload["capacity_thresholds_state"] = (
        "DECLARED" if payload["capacity_thresholds"] else "ABSENT")
    payload["protection_rules_state"] = (
        "DECLARED" if payload["protection_rules"] else "ABSENT")
    # Keyed by the CONTRACT'S class name, not by this module's field name.
    # They differed by one word (`retention` vs `retention_metadata`) and the
    # class reported UNESTABLISHED — a filled class reading as one nobody
    # read, which is the exact confusion this three-way vocabulary exists to
    # prevent. `coverage()` looks the class up by the contract's own spelling.
    payload["retention_metadata_state"] = (
        "DECLARED" if payload["retention"] else "ABSENT")
    payload["classification_rules_state"] = (
        "DECLARED" if payload["classification_rules"]["lifecycle"] else "ABSENT")

    payload["protective_names"] = [row["resource"] for row in payload["protection_rules"]
                                   if row["level"] == "protected"]
    payload["unrecognised_policies"] = [
        {"resource": row["resource"], "cleanup_policy": row["cleanup_policy"]}
        for row in payload["protection_rules"] if row["level"] == UNRECOGNISED
    ]
    requested = _requested_reconcile(document)
    payload["destructive_reconcile"]["requested_but_not_enabled"] = bool(requested)
    payload["destructive_reconcile"]["requested_where"] = requested
    return payload


def _requested_reconcile(document: dict) -> list[str]:
    """Where the document asks for reconciliation, if anywhere.

    Reported and not honoured. §22 says destructive reconcile must not be
    enabled this round, and the way to be sure a document cannot enable it is
    to have the only field that mentions the request be a report.
    """
    found = []
    for key in ("reconcile", "destructive_reconcile", "auto_reconcile"):
        if document.get(key):
            found.append(key)
    for name, body in _entries(document):
        if body.get("reconcile") or body.get("auto_remove"):
            found.append(f"resources.{name}")
    return sorted(set(found))


def declared_objects(document: dict | None = None, *, path=None) -> dict:
    """The host objects a governance document actually names, by group.

    A governance document's top-level keys are GROUP names —
    `container0-gold-image`, `old-lit-req-envs`, `stale-runs` — and only
    occasionally the name of a thing Docker knows about. Treating a key as a
    resource ref is therefore wrong in the ordinary case and reports the whole
    document as dangling, which is a finding about the reader rather than
    about the document.

    This unwraps the second level instead: the `containers:` / `networks:` /
    `images:` / `objects:` fields inside each group, which DO name objects.
    That is the correlation §23 asks for — "can this container be attributed
    to a run" — expressed as a map from an object name to the group that
    declares it.

    Returns `{containers: {name: group}, networks: {...}, images: {...},
    volumes: {...}, groups: [...]}`. Values that are not parseable as names
    are skipped rather than guessed at.
    """
    state = "DECLARED"
    if document is None:
        read_result = read(path)
        state = read_result["state"]
        document = read_result.get("document")
    if not document:
        return {"state": state, "containers": {}, "networks": {},
                "images": {}, "volumes": {}, "groups": [],
                "why": "no governance document was read"}

    out: dict = {"state": state, "containers": {}, "networks": {},
                 "images": {}, "volumes": {}, "groups": []}
    for group, body in _entries(document):
        out["groups"].append(group)
        for spec in body.get("containers") or []:
            name = spec.get("name") if isinstance(spec, dict) else spec
            if isinstance(name, str) and name:
                out["containers"].setdefault(name, group)
        for spec in body.get("networks") or []:
            name = spec.get("name") if isinstance(spec, dict) else spec
            if isinstance(name, str) and name:
                out["networks"].setdefault(name, group)
        for spec in body.get("images") or []:
            if not isinstance(spec, str):
                continue
            # `"sha256:2d24eb505ff5  litellm-lit-0101-c93bb20-full:latest"` —
            # an id and a tag on one line. The tag is what a person searches
            # for and the id is what an action needs, so both are kept, under
            # the same group.
            for token in spec.split():
                out["images"].setdefault(token, group)
        for spec in body.get("volumes") or []:
            if isinstance(spec, str) and spec:
                out["volumes"].setdefault(spec, group)
    return out


def coverage(payload: dict) -> dict:
    """How much of the four-class list this document actually fills."""
    classes = input_classes()
    states = {name: payload.get(f"{name}_state", "UNESTABLISHED")
              for name in classes}
    return {
        "classes": states,
        "declared": sorted(n for n, s in states.items() if s == "DECLARED"),
        "absent": sorted(n for n, s in states.items() if s == "ABSENT"),
        "unestablished": sorted(n for n, s in states.items()
                                if s == "UNESTABLISHED"),
        "note": (
            "an ABSENT class is a fact about the document — it does not carry "
            "that input. An UNESTABLISHED class is a fact about this call: "
            "there was no document to read. Neither is an empty one"
        ),
    }


def protection_input(payload: dict, *, known_containers=None, path=None) -> dict:
    """The `protection_rules` class, in the shape a protection resolver wants.

    `known_containers` is the container names the host actually has. Two
    different lookups are done with it, and keeping them apart is the point:

      by group    the document's own key (`old-lit-req-envs`) declares a
                  cleanup policy, and the containers listed inside it inherit
                  that group's answer
      by object   the document's `containers:` entries name containers
                  directly, so a container can be matched without a key ever
                  being read as a name

    A container the host has that neither lookup covers is `NOT_DECLARED` — no
    protection from THIS document, which is not the same as no protection. A
    name the document declares that the host does not have is `DANGLING`: a
    statement about the document, and nothing about the resource.
    """
    rules = payload.get("protection_rules") or []
    if payload.get("state") != "DECLARED":
        return {
            "state": payload.get("state") or "ABSENT",
            "protections": [],
            "blocked": [],
            "deferred": [],
            "unrecognised": [],
            "by_container": {},
            "dangling": [],
            "not_declared": sorted(known_containers or []),
            "known_containers_supplied": known_containers is not None,
            "why": (
                "no governance document was read, so it establishes nothing. "
                "It also withdraws nothing: every container is reported as "
                "NOT_DECLARED rather than as unprotected"
            ),
        }

    # Falls back to the location this payload was resolved FROM, so the
    # cross-check reads the same document the rules came out of. Reaching for
    # the default here instead would compare one document's rules against
    # another document's object list, and every name would be dangling.
    objects = declared_objects(path=path or payload.get("location"))
    by_group = {row["resource"]: row for row in rules}
    by_container: dict[str, dict] = {}
    for name, group in objects["containers"].items():
        rule = by_group.get(group)
        if rule is None:
            continue
        by_container[name] = {
            "container": name,
            "group": group,
            "level": rule["level"],
            "cleanup_policy": rule["cleanup_policy"],
            "mechanism": rule["mechanism"],
            "provenance": "FACT",
            "why": (
                "named inside the group's own container list. The group's "
                "policy is what carries, and that is a declaration about a "
                "resource, not a measurement of one"
            ),
        }

    dangling, not_declared = [], []
    if known_containers is not None:
        present = set(known_containers)
        dangling = sorted(set(objects["containers"]) - present)
        not_declared = sorted(present - set(by_container))
    return {
        "state": "DECLARED",
        "protections": [row for row in rules if row["level"] == "protected"],
        "blocked": [row for row in rules if row["level"] == "blocked"],
        "deferred": [row for row in rules if row["level"] == "deferred"],
        "unrecognised": payload.get("unrecognised_policies") or [],
        "groups": objects["groups"],
        "by_container": by_container,
        "dangling": dangling,
        # A group key is not a container name, and the document says which is
        # which by listing containers inside the group. Reported so that a
        # reader does not conclude the document is stale because none of its
        # top-level keys matches anything Docker knows.
        "group_keys_are_not_object_names": True,
        "not_declared": not_declared,
        "known_containers_supplied": known_containers is not None,
        "why": (
            "a name the document declares that the host does not have is "
            "DANGLING: it says the document is out of date and says nothing "
            "about the resource. A container the document does not name is "
            "NOT_DECLARED: no protection from THIS document, which is not the "
            "same as no protection"
        ),
    }


if __name__ == "__main__":  # pragma: no cover - manual inspection only
    import json

    print(json.dumps({"contract": str(CONTRACT), "classes": list(input_classes()),
                      "document": default_location()},
                     indent=2, ensure_ascii=False))
