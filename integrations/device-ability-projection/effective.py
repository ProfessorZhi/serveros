#!/usr/bin/env python3
"""G1 — per-device EffectiveAbility: a PROJECTION over the existing resolver.

WHY THIS MODULE EXISTS
----------------------
`integrations/fleet-v1-mcp-conformance/CAPABILITY-PROJECTION.md` names the gap in one
sentence: **"Fleet Next has no projection for Device Ability (G1) at all."** The authority
is `ability-management`'s manifest — one Ability is registered once by source/revision,
while *installation / ready / supported* are observed per device. `runtime/ability_projection.py`
already answers "which Abilities does the authority record as installed on this device, at
which revision" (`installed_abilities`). What it does **not** answer is whether one of them
may be *called right now*, and the architecture is explicit that the answer is a conjunction:

    Ability × Provider × Device Binding × Runtime × Target × Health × Authorization

This module is that projection and nothing else. It owns no state and defines no vocabulary
of its own; every capability decision is delegated to `abilities/resolver.py#resolve`, once
per installed Ability, with only the inputs the device actually reported. What it adds is
the two things the resolver deliberately does not say:

  * **`effective` is null unless the device reported every decisive term.** The resolver may
    answer AVAILABLE from the inputs it had — policy and health are optional by design, and
    `_input_policy` says so ("policy is a filter over an answer, not a capability input").
    A projection that printed that provider id would be read as "callable", which is the
    substitution the architecture forbids. So `effective` is the resolver's provider only
    when the resolver says AVAILABLE/DEGRADED **and** supported/ready/grant are all known.
    The resolver's own `status`/`reason`/`detail` are kept verbatim beside it: nothing is
    discarded, and the stricter word does not overwrite the resolver's.

  * **A term nobody reported is `UNKNOWN`, with the axis named.** `installed` comes from the
    manifest (the authority, not an observation). `supported`, `ready` and `grant` come from
    observations. Absent observation is not "fine" — it is UNKNOWN.

WHAT IT IS NOT
--------------
* **not a registry**: the manifest is read, never written, and no Ability is registered here;
* **not a second resolver**: every decision is `resolve(...)`; this module has no refusal
  vocabulary of its own beyond "the request was malformed";
* **not a store**: the output is a pure dict. Persisting it is the caller's business, and
  `runtime/ability_projection.py` remains the module that writes `effective.json`;
* **not a claim that anything is callable**: see `effective`;
* **not a Fleet tool**: it adds no 34th tool and changes no served surface. `fleet_mcp/` is
  not imported. The route to the 33-tool consumer is stated in README.md.

NO CLOCK, NO IO
---------------
`project()` reads no file, opens no socket and reads no clock. `source_revision` is supplied
by the caller (from `read_source()`, which reads a **git object**, never a working tree) and
`resolved_at` is passed through to the resolver. A module that read its own clock would
produce a record whose "as of" nobody could reproduce.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
ROOT = _HERE.parents[1]          # integrations/device-ability-projection -> repo root

SCHEMA = "serveros/device-ability-projection.v1"
CONTRACT = _HERE / "contract.yaml"

AUTHORITY = "ability-management"
THIS_IS = ("a projection, not a store: `effective` is null unless the device reported "
           "every decisive term")

#: The observations a device (or its operator) may report. Names are the resolver's own
#: input keys — a projection that invented a parallel vocabulary would be the second
#: definition this module exists to avoid.
AXIS_INPUTS = (
    "device_capabilities", "profile", "instances", "runtime_record",
    "runtime_binding", "health", "policy", "task_context", "providers",
)

#: Which observation makes which G1 word knowable. `installed` is not here on purpose:
#: it comes from the authority's manifest, not from anything a device says.
SUPPORT_TERMS = ("device_capabilities", "profile")
READY_TERM = "health"
GRANT_TERM = "policy"

DEFINITE = ("AVAILABLE", "DEGRADED")


class Refused(Exception):
    """The request cannot be projected. 'No' is an answer and is never raised."""


def _load(name: str, path: Path):
    """One canonical name per sibling, as the rest of this tree spells them.

    `runtime/daemon.py`, `runtime/cli.py` and `tests/test_ability_projection.py` all load
    `ability_projection` under `serveros_ability_projection`, and `tests/test_ability_resolver.py`
    loads the resolver under `serveros_ability_resolver`. `tests/load_census.py` measures how
    many times a file's source is executed per process: a second name here would be a second
    copy of the module, and two copies of a `Refused` class with one spelling is the defect
    `context/actor.py` documents.
    """
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _projection():
    return _load("serveros_ability_projection", ROOT / "runtime" / "ability_projection.py")


def _resolver():
    return _load("serveros_ability_resolver", ROOT / "abilities" / "resolver.py")


def read_source(*, repository, revision: str):
    """The authoritative manifest, read from a **git object** at `revision`.

    Delegates to `runtime/ability_projection.py#read_manifest`, whose own docstring gives the
    reason: a dirty checkout must not be able to become what the node believes. Returns
    `(manifest, resolved_sha)`.
    """
    return _projection().read_manifest(Path(repository), revision)


# ---------------------------------------------------------------------------
# The three observed terms, and why each is UNKNOWN rather than a default
# ---------------------------------------------------------------------------

def terms(reported, resolved: dict) -> dict:
    """`supported` / `ready` / `grant`, from what the device REPORTED plus the resolver's reason.

    Keyed on `reported` (the observations the caller actually supplied) rather than on the
    resolver's `inputs_missing`, and that choice is the whole point. The resolver treats an
    absent `policy` and an absent `health` as "nothing to refuse" — correctly, because it must
    be able to answer without them. A projection cannot: G1's rule is that `effective` is null
    unless a device actually reported it, so "nobody told me" has to survive as UNKNOWN instead
    of collapsing into the resolver's silence.
    """
    reported = frozenset(reported)
    reason = resolved.get("reason")
    supported = (
        "UNKNOWN" if not set(SUPPORT_TERMS) <= reported
        else "DENIED" if reason in ("DEVICE_DEPENDENCY_MISSING", "NETWORK_UNAVAILABLE")
        else "SUPPORTED")
    ready = (
        "UNKNOWN" if READY_TERM not in reported
        else "NOT_READY" if reason == "RUNTIME_UNHEALTHY"
        else "READY")
    grant = (
        "UNKNOWN" if GRANT_TERM not in reported
        else "DENIED" if reason == "POLICY_DENIED"
        else "ALLOWED")
    return {"supported": supported, "ready": ready, "grant": grant}


def effective(resolved: dict, words: dict) -> tuple:
    """`(provider_or_None, why)` — the resolver's answer, plus the stricter G1 question.

    The resolver's status is NOT recomputed. `effective` is a second, narrower question asked
    on top of it: *did this device report every term the word "callable" would need?* A
    projection that answered "callable" from the first term alone is precisely what the
    architecture forbids, so the two answers are reported side by side and never merged.
    """
    status = resolved.get("status")
    reason = resolved.get("reason")
    if status not in DEFINITE:
        return None, (f"resolver status is {status}"
                      + (f" ({reason})" if reason else ""))
    unknown = sorted(name for name, value in words.items() if value == "UNKNOWN")
    if unknown:
        return None, (
            f"resolver answered {status} from the inputs it had, but this device reported no "
            f"{', '.join(unknown)} — printing the provider id here would be read as callable")
    return resolved.get("provider"), (f"resolver status is {status}; every decisive term was "
                                      f"reported by this device")


# ---------------------------------------------------------------------------
# The projection
# ---------------------------------------------------------------------------

def _manifest_entry(manifest: dict, name, ability_id):
    for entry in manifest.get("abilities") or []:
        if not isinstance(entry, dict) or entry.get("name") != name:
            continue
        if ability_id is not None and entry.get("id") != ability_id:
            continue
        return entry
    return None


def semantic_family(entry) -> tuple:
    """`(family_or_None, why)`. The manifest's own words; a missing one is stated.

    `family` is a string on some records and `domain.primary` on others (the manifest carries
    both), so both are read. Neither is invented: `domain.primary` values are taxonomy ids the
    manifest already uses, not a family name this module makes up.
    """
    if not isinstance(entry, dict):
        return None, "the manifest entry could not be located for this ability"
    family = entry.get("family")
    if isinstance(family, str) and family.strip():
        return family, None
    domain = entry.get("domain")
    if isinstance(domain, dict):
        primary = domain.get("primary")
        if isinstance(primary, str) and primary.strip():
            return primary, None
    return None, "the manifest declares neither `family` nor `domain.primary` for this ability"


def project_device(*, manifest: dict, source_revision: str, device: dict,
                   resolved_at=None) -> dict:
    """One device's projection. Pure: no IO, no clock, no network."""
    if not isinstance(manifest, dict):
        raise Refused(f"manifest is a {type(manifest).__name__}, not a mapping")
    source_revision = str(source_revision or "").strip()
    if not source_revision:
        raise Refused("source_revision is required: a projection that cannot say which "
                      "authority revision it is about is not reproducible")
    if not isinstance(device, dict):
        raise Refused(f"device is a {type(device).__name__}, not a mapping")

    device_ref = str(device.get("device_ref") or "").strip()
    runtime = str(device.get("runtime") or "").strip()
    principal = str(device.get("principal") or "").strip()
    if not device_ref:
        raise Refused("device.device_ref is required (`device:<id>`)")
    if not runtime:
        raise Refused("device.runtime is required: which runtime a device binding is for is "
                      "not inferable from the device")
    if not principal:
        raise Refused("device.principal is required: a binding with no subject cannot answer "
                      "'whose ability is this', and a projection that guessed one would be "
                      "inventing an actor")

    raw_ids = device.get("device_ids") or [device_ref]
    device_ids = _projection().bare_device_ids(tuple(str(i) for i in raw_ids))
    if not device_ids:
        raise Refused("no usable device id after stripping the `device:` prefix")

    observations = device.get("observations") or {}
    if not isinstance(observations, dict):
        raise Refused(f"device.observations is a {type(observations).__name__}, not a mapping")
    undeclared = sorted(name for name in observations if name not in AXIS_INPUTS)
    if undeclared:
        raise Refused(
            f"observations carry {undeclared}, which are not declared axes. The eight inputs "
            f"are the resolver's own keys ({list(AXIS_INPUTS)}); a parallel vocabulary here "
            f"would be the second definition this module exists to avoid")
    reported = frozenset(observations)

    installed = _projection().installed_abilities(manifest, device_ids)
    catalogue = _projection().resolution_catalogue(installed)

    rows = []
    for record in installed:
        request = {"ability": f"ability:{record['name']}", "device_ref": device_ref,
                   "runtime": runtime, "abilities": catalogue}
        request.update({name: observations[name] for name in AXIS_INPUTS
                        if name in observations})
        if resolved_at is not None:
            request["resolved_at"] = resolved_at
        resolved = _resolver().resolve(request)

        entry = _manifest_entry(manifest, record.get("name"), record.get("id"))
        family, family_why = semantic_family(entry)
        words = terms(reported, resolved)
        provider, effective_why = effective(resolved, words)
        rows.append({
            "ability": record.get("name"),
            "ability_ref": f"ability:{record.get('name')}",
            "id": record.get("id"),
            "type": record.get("type"),
            "path": record.get("path"),
            "semantic_family": family,
            "semantic_family_why": family_why,
            "product_role": record.get("product_role"),
            # The authority's fact, not an observation.
            "installed": True,
            "version": record.get("version"),
            "fingerprint": record.get("fingerprint"),
            "agents": record.get("agents"),
            "revisions_installed": record.get("revisions_installed"),
            "source_revision": source_revision,
            "binding": {"device_ref": device_ref, "device_ids": list(device_ids),
                        "principal": principal, "runtime": runtime},
            # The three observed terms, each with the observation that makes it knowable.
            "supported": words["supported"],
            "ready": words["ready"],
            "grant": words["grant"],
            # The resolver's answer, verbatim. Never recomputed, never overwritten.
            "status": resolved.get("status"),
            "reason": resolved.get("reason"),
            "detail": resolved.get("detail"),
            "operation": resolved.get("operation"),
            "inputs_seen": resolved.get("inputs_seen"),
            "inputs_missing": resolved.get("inputs_missing"),
            "candidates": resolved.get("candidates"),
            # The stricter G1 word.
            "effective": provider,
            "effective_why": effective_why,
        })

    rows.sort(key=lambda row: str(row.get("ability")))
    return {
        "device_ref": device_ref,
        "device_ids": list(device_ids),
        "principal": principal,
        "runtime": runtime,
        "source_revision": source_revision,
        "reported_axes": sorted(reported),
        "unreported_axes": sorted(set(AXIS_INPUTS) - reported),
        "abilities": rows,
        "counts": {"installed": len(rows),
                   "effective": sum(1 for row in rows if row["effective"] is not None),
                   "unknown_terms": sum(1 for row in rows
                                        if row["effective"] is None)},
    }


def project(*, manifest: dict, source_revision: str, devices) -> dict:
    """One projection for every named device, in the order given."""
    if not isinstance(devices, (list, tuple)) or not devices:
        raise Refused("devices must be a non-empty list: a projection about no device is "
                      "not a partial answer, it is an empty one wearing a schema")
    projected = [project_device(manifest=manifest, source_revision=source_revision,
                                device=device) for device in devices]
    return {
        "schema": SCHEMA,
        "authority": AUTHORITY,
        "this_is": THIS_IS,
        "source_revision": source_revision,
        "devices": projected,
        "counts": {
            "devices": len(projected),
            "installed": sum(d["counts"]["installed"] for d in projected),
            "effective": sum(d["counts"]["effective"] for d in projected),
        },
    }


def main(argv=None) -> int:  # pragma: no cover - the CLI is not this milestone
    raise SystemExit("integrations/device-ability-projection/effective.py is a library; "
                     "see README.md for the consumer route")


if __name__ == "__main__":  # pragma: no cover
    main()
