#!/usr/bin/env python3
"""Is the "one capability system, two adapters" claim actually true here?

    python3 runtime_parity.py --contract abilities/agent-runtime-contract.yaml \
        --registry fleet/registry.yaml
    python3 runtime_parity.py --contract ... --observed observations.json

`docs/claude-codex-parity.md` states the principle — "same capability, same
semantics, same context; different runtime adapter only" — and
`abilities/agent-runtime-contract.yaml` declares the record shape that would
make it checkable. Both are prose to every program in this repository: the
contract was read by nothing at all until this module, so its parity block was
a claim two documents agreed on.

This module is the reader. It answers the question the parity document poses,
in the only form a program can answer it, and it is deliberately narrow about
which part of that document is machine-checkable and which part is not.

What is checkable, and why the rest is not
------------------------------------------
`docs/claude-codex-parity.md` lists ten smoke items. Eight of them are about
what an Agent session can *see* — the same Item, the same worktree, the same
milestone — and establishing one requires being that session. This module does
not pretend to run them; a check that inferred them from configuration would
be a claim about configuration wearing a claim about behaviour.

What is checkable is the *structural* half, and it is the half where the
defect would actually hide:

  1. Every runtime id declares both adapter surfaces, and no surface is
     declared for a runtime that does not exist. A runtime named in `ids` with
     no `instruction_surfaces` entry is an adapter that was promised and never
     written — and nothing else in the repository would notice.
  2. Every device that claims to run an agent names one that exists, and every
     device's `runtime_bindings` and its legacy `agents` list agree.
     `fleet/registry.yaml` has three devices each listing two agents, and
     nothing compared those lists against the closed `ids` set.
  3. On this host, each declared surface is either present or reported absent.
     Read-only; nothing is created.
  4. On this host, each declared driver is either registered or reported
     unregistered — a question the three above cannot be asked, and the reason a
     third `*_surfaces` entry would not have been enough. A hook's file is
     always present; what is missing when a driver is unwired is the
     registration inside it.

What the fourth check is for
----------------------------
`records/information/raw/drift_sync.py` describes itself as a SessionStart hook
driver, and on 2026-09-22 nothing on this node registered it. The collector was
installed, ran by hand, and fired nowhere — and no surface in the product could
say so, because "installed" and "wired" had never been separated. The reading
that made it visible came from the other direction:
`serverosctl information watched` reported the one followed document as
`OVERDUE — last re-check 2026-09-16, cutoff 2026-09-22`, which is what an
unwired collector and a quiet source both look like. Two readings, one fact.

Why this docstring changed, and what was wrong with it
------------------------------------------------------
This file used to carry a section headed "Why the compared fields are global".
It argued that `capabilities.consumable_provider_kinds`, `skill_discovery` and
`session_lifecycle` being single values rather than per-runtime maps was not an
oversight, because "the shared values ARE the claim". The sentence is true and
it was doing the wrong work: it made a vacuous comparison read as a designed
one. A field with one value on both sides of a comparison cannot differ, so
`compared_fields` was a list of three names and zero checks, and the argument
in this docstring was the reason nobody looked again.

The three fields are now resolved rather than argued away. Two
(`skill_discovery`, `session_lifecycle`) are gone from the parity block and are
`runtime_features` axes — per-binding answers, in a vocabulary where `UNKNOWN`
exists, instead of a global scalar offered as a comparison. The third became a
per-runtime map, so the entry that remains names two values that can actually
differ. And the shape is enforced rather than trusted:
`PARITY_COMPARES_A_SHARED_VALUE` below fails any `compared_fields` entry whose
value is not keyed by every declared runtime id — so restoring `skill_discovery`
to that list is a red check rather than a quiet regression.

The lesson kept here on purpose: a comment explaining why an empty check is
correct is the most effective way to keep it empty.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCHEMA = "serveros/runtime-parity.v1"

# The observations a caller may supply about one runtime on one device. Named
# as a closed set so a new one is an edit here rather than a quiet extra key.
OBSERVABLE_SURFACES = ("instruction_surfaces", "mcp_surfaces")


def _finding(code: str, detail: str, **extra) -> dict:
    return {"code": code, "detail": detail, **extra}


def observed_surface_rows(observed: dict, runtimes) -> list:
    """The measured surfaces, flattened into rows a caller can read.

    This exists because the rows used to be built inside one renderer, and a
    second renderer that needed the same facts had no way to get them: the
    JSON record carried a verdict with no observation behind it, so the
    machine-readable answer to "what did this look at" was silence. Two
    renderers deriving one fact independently is how they drift while each
    stays defensible, so the derivation lives here and both read it.

    A row says `present: False` when the path was read and is not there. That
    is a finding. `observed` naming no entry for a runtime at all is a gap,
    and it produces no row — an absence of measurement must not be rendered
    as a measurement of absence.
    """
    rows = []
    for runtime in runtimes or ():
        for field in OBSERVABLE_SURFACES:
            for surface in (observed.get(runtime) or {}).get(field) or []:
                present = bool(surface.get("present"))
                rows.append({
                    "runtime": runtime,
                    "field": field,
                    "present": present,
                    "path": surface.get("path"),
                    # Carried so a caller can answer a per-feature question
                    # without pairing this list against the contract by index.
                    "role": surface.get("role"),
                    "relative": surface.get("relative"),
                    # A bare `present: False` cannot tell "read it and it is
                    # not there" from "did not read it", and those are a
                    # finding and a gap respectively.
                    "because": None if present else "the path was read and is not there",
                })
    return rows


def check_contract(contract: dict) -> list[dict]:
    """Is the contract internally consistent?

    Three key sets must agree: `ids`, `capabilities.instruction_surfaces`, and
    `capabilities.mcp_surfaces`. They are written by hand in one file and were
    compared by nobody, which is the arrangement that produces a runtime id
    with an instruction surface and no way to declare its MCP servers.
    """
    findings: list[dict] = []
    ids = list(contract.get("ids") or ())
    if not ids:
        return [_finding("NO_RUNTIMES_DECLARED",
                         "the contract names no runtime ids, so parity is a "
                         "comparison between nothing and nothing")]

    capabilities = contract.get("capabilities") or {}
    for field in OBSERVABLE_SURFACES:
        surfaces = capabilities.get(field) or {}
        for runtime in ids:
            if runtime not in surfaces:
                findings.append(_finding(
                    "SURFACE_NOT_DECLARED",
                    f"{runtime} is a declared runtime with no `{field}` entry; "
                    "an adapter was promised and not written",
                    runtime=runtime, field=field))
        for runtime in surfaces:
            if runtime not in ids:
                findings.append(_finding(
                    "SURFACE_FOR_UNKNOWN_RUNTIME",
                    f"`{field}` declares {runtime}, which is not in `ids`; the "
                    "adapter has no runtime to attach to",
                    runtime=runtime, field=field))

    # `allowed_differences` must name real fields, or it is exempting nothing
    # while reading as though it exempted something.
    parity = contract.get("parity") or {}
    compared = set(parity.get("compared_fields") or ())
    allowed = set(parity.get("allowed_differences") or ())
    capabilities_keys = set(capabilities)
    record_keys = set((contract.get("record") or {}).get("optional_fields") or ())
    record_keys |= set((contract.get("record") or {}).get("required_fields") or ())
    for name in sorted(compared | allowed):
        if name not in capabilities_keys and name not in record_keys:
            findings.append(_finding(
                "PARITY_NAMES_AN_ABSENT_FIELD",
                f"parity names `{name}`, which is neither a capability nor a "
                "record field; it can never differ and so is never checked",
                field=name))
    overlap = compared & allowed
    if overlap:
        findings.append(_finding(
            "FIELD_BOTH_COMPARED_AND_ALLOWED",
            f"{sorted(overlap)} is compared and excused in the same breath",
            fields=sorted(overlap)))

    # The comparison has to be able to come out either way. A compared field
    # whose value is one list rather than one list per runtime is a comparison
    # between one thing and itself: every run of this check would report
    # agreement, and it would report it for any pair of runtimes, including two
    # that had nothing in common. That is the shape this whole module exists to
    # catch, so it is caught here rather than left to a comment.
    for name in sorted(compared):
        if name not in capabilities_keys:
            continue  # a record field; `PARITY_NAMES_AN_ABSENT_FIELD` owns it
        value = capabilities.get(name)
        if not isinstance(value, dict):
            findings.append(_finding(
                "PARITY_COMPARES_A_SHARED_VALUE",
                f"parity compares `{name}`, whose value is not keyed by runtime "
                "id; one value offered as a field two runtimes are compared on "
                "cannot differ, so the comparison is vacuous",
                field=name, value_type=type(value).__name__))
            continue
        missing = sorted(set(ids) - set(value))
        if missing:
            findings.append(_finding(
                "PARITY_COMPARES_A_SHARED_VALUE",
                f"parity compares `{name}`, which declares no entry for "
                f"{missing}; the field cannot differ for a runtime that has no "
                "value in it",
                field=name, runtimes=missing))
    return findings


def check_registry(registry: dict, contract: dict) -> list[dict]:
    """Does every device that claims an agent claim one that exists?

    And, since W7: does every device's `runtime_bindings` agree with its legacy
    `agents` list? The second question is `BINDING_MEMBERSHIP_MISMATCH`, and the
    computation lives in `abilities/runtime_binding.py` rather than here,
    because the fleet view and the CLI need to ask it too and a second
    implementation would be a second answer.

    The first question is now the weaker of the two — an agent name is checked
    against the closed `ids` set, while a binding is checked against that set
    AND against the list beside it — and it is kept because a device could
    carry `agents: [codex]` with no `runtime_bindings` key at all, which is a
    registry that has not started the migration rather than one that disagrees
    with itself.
    """
    ids = set(contract.get("ids") or ())
    findings: list[dict] = []
    devices = registry.get("devices") or []
    if isinstance(devices, dict):  # tolerate the mapping form
        devices = [{"device_id": k, **(v or {})} for k, v in devices.items()]
    for device in devices:
        device_id = device.get("device_id") or device.get("id") or "?"
        for agent in device.get("agents") or ():
            if agent not in ids:
                findings.append(_finding(
                    "DEVICE_NAMES_UNKNOWN_AGENT",
                    f"device {device_id} lists agent {agent!r}, which the "
                    "runtime contract does not declare",
                    device=device_id, agent=agent))
    findings += _binding().membership_findings(registry, ids)
    return findings


def _binding():
    """`abilities/runtime_binding.py`, loaded the way this repository loads.

    Deferred rather than imported at module scope so that this file stays
    answerable on its own: `main()` can check a contract for a caller that has
    no registry and no binding code on disk, and a hard import would make the
    contract check fail for a reason that has nothing to do with the contract.
    """
    global _BINDING_MODULE
    if _BINDING_MODULE is None:
        import importlib.util
        path = Path(__file__).resolve().parent / "runtime_binding.py"
        spec = importlib.util.spec_from_file_location(
            "serveros_runtime_binding_from_parity", path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        _BINDING_MODULE = module
    return _BINDING_MODULE


_BINDING_MODULE = None


def check_observed(observed: dict, contract: dict) -> list[dict]:
    """`observed` is what a caller measured, not what it believes.

    Shape: {"<runtime>": {"instruction_surfaces": [{"path": ..., "present": bool}],
                          "mcp_surfaces": [...]}}

    A surface the caller did not observe is reported as UNOBSERVED and is not
    counted as present. Silence about a file is not evidence that it is there,
    and this is the one place where assuming otherwise would make the parity
    claim true by default — which is the failure mode of every check like this.
    """
    findings: list[dict] = []
    for runtime in contract.get("ids") or ():
        for field in OBSERVABLE_SURFACES:
            entry = (observed.get(runtime) or {}).get(field)
            if entry is None:
                findings.append(_finding(
                    "SURFACE_UNOBSERVED",
                    f"{runtime}.{field} was not observed on this device, so "
                    "whether the adapter is installed here is unknown",
                    runtime=runtime, field=field, severity="UNKNOWN"))
                continue
            for surface in entry:
                if not surface.get("present"):
                    findings.append(_finding(
                        "SURFACE_MISSING_ON_DEVICE",
                        f"{runtime} declares {surface.get('path')} and it is "
                        "not present on this device",
                        runtime=runtime, field=field,
                        path=surface.get("path")))
    return findings


def declared_drivers(contract: dict) -> list[dict]:
    """The drivers that must fire, as the contract declares them.

    Most fire at session start and one fires at the start of a turn; the
    question is the same for both, which is why they share a register.

    A list rather than the per-runtime map the two `*_surfaces` blocks are,
    because a driver belongs to the runtime that fires it and to nothing else.
    See `capabilities.session_drivers` for why a third surface map would have
    asked the wrong question: a hook's file is always there, so a presence probe
    answers yes while the driver fires nowhere.
    """
    return list(((contract.get("capabilities") or {}).get("session_drivers")) or ())


def observed_driver_rows(observed_drivers: dict, contract: dict) -> list:
    """The measured drivers, flattened into rows a caller can read.

    `observed_driver_rows` and not `observed_surface_rows` with a third field,
    for the reason the two are separate questions: a row here answers "is this
    driver registered", which is not "is this path there". The keys are named so
    that a reader cannot mistake one for the other — `registered` and not
    `present`, `state` and not `present: bool`.

    A row is produced only for a driver that was observed. A declared driver
    nobody measured is a gap, and produces no row, because an absence of
    measurement must not be rendered as a measurement of absence.
    """
    rows = []
    for driver in declared_drivers(contract):
        name = driver.get("name")
        entry = (observed_drivers or {}).get(name)
        if entry is None:
            continue
        rows.append({
            "driver": name,
            "runtime": driver.get("runtime"),
            "event": entry.get("event") or driver.get("event"),
            "state": entry.get("state"),
            "surface": entry.get("surface"),
            "registered": (entry.get("state") == "REGISTERED"
                           if entry.get("state") else None),
            "registrations": entry.get("registrations"),
            "program": entry.get("program"),
            "program_present": entry.get("program_present"),
            "because": entry.get("because"),
        })
    return rows


def check_drivers(observed_drivers: dict, contract: dict) -> list[dict]:
    """Is each declared driver actually registered where it has to be?

    Three answers, kept apart, because they call for three different actions:

      `DRIVER_NOT_REGISTERED`  the surface was read and the marker is not on it.
                               The driver does not fire; somebody has to author
                               the registration, or record the absence.
      `DRIVER_SURFACE_UNREADABLE`
                               the file is there and could not be read or
                               parsed. Whether the driver is registered is
                               unknown, and a report that called this a
                               missing registration would be inventing a fact
                               about a file it could not read.
      `DRIVER_PROGRAM_ABSENT`  the driver's own program is not on disk. This is
                               not about the registration: it says the thing
                               being registered is not installed here. Carried
                               with `registered`, because a marker pointing at
                               nothing and a program nobody registered are
                               opposite failures with one cause between them.

    A driver nobody observed is `DRIVER_NOT_OBSERVED` at severity UNKNOWN, which
    the caller separates from a defect. Assuming a driver fires because nobody
    looked is the way this check would be true by default.
    """
    findings: list[dict] = []
    for driver in declared_drivers(contract):
        name = driver.get("name")
        entry = (observed_drivers or {}).get(name)
        if entry is None:
            findings.append(_finding(
                "DRIVER_NOT_OBSERVED",
                f"driver {name} was not observed on this device, so whether it "
                "fires here is unknown",
                driver=name, runtime=driver.get("runtime"), severity="UNKNOWN"))
            continue
        state = entry.get("state")
        if state == "UNREADABLE":
            findings.append(_finding(
                "DRIVER_SURFACE_UNREADABLE",
                f"{entry.get('surface')} was found and could not be read, so "
                f"whether {name} is registered on it is unknown: "
                f"{entry.get('because') or 'no reason given'}",
                driver=name, runtime=driver.get("runtime"),
                path=entry.get("surface"), severity="UNKNOWN"))
            continue
        if state != "REGISTERED":
            findings.append(_finding(
                "DRIVER_NOT_REGISTERED",
                f"{name} must fire on {driver.get('event')} for "
                f"{driver.get('runtime')}, and no command on "
                f"{entry.get('surface')} under that event names "
                f"{driver.get('marker')!r}",
                driver=name, runtime=driver.get("runtime"),
                path=entry.get("surface"), event=driver.get("event"),
                marker=driver.get("marker")))
        if entry.get("program_present") is False:
            findings.append(_finding(
                "DRIVER_PROGRAM_ABSENT",
                f"{name}'s program {entry.get('program')} is not on this "
                "device, so "
                + ("the registration on the surface points at a script that "
                   "is not there"
                   if state == "REGISTERED" else
                   "the driver is not installed here at all, which is a "
                   "different fact from not being registered"),
                driver=name, runtime=driver.get("runtime"),
                path=entry.get("program"),
                registered=state == "REGISTERED"))
    return findings


def declared_gaps(contract: dict) -> list[dict]:
    return list(((contract.get("capabilities") or {}).get("known_gaps")) or ())


def _matches_a_declared_gap(finding: dict, gaps: list[dict]) -> dict | None:
    """Is this defect one the contract already records?

    A check that is red on the day it is written gets turned off. This one is
    red from the moment the Codex workspace entry is observed missing — a real
    gap, and one that cannot be closed from inside this round, because closing
    it means writing into the user's Agent configuration.

    So a defect the contract has already recorded, with a reason and a closing
    action, is moved to `acknowledged` rather than dropped. It stays in the
    record and stays visible; it stops being the reason nobody reads the
    output. A finding with no matching entry is still a finding.

    Two kinds of entry are matched, because the contract records two kinds of
    gap. A surface gap names a path, and the finding is matched on the path's
    tail. A driver gap names a driver, and the finding is matched on the name —
    a path match cannot work there, because the driver's surface file exists
    either way and so is never the thing that is missing.

    A driver gap must also name the FINDING it records, in `acknowledges`. A
    surface gap does not have to, because a surface that is missing produces
    exactly one code, so naming the surface names the finding. A driver can
    fail two ways — unwired, or wired to a program that is not on the disk —
    and a gap that named only the driver would acknowledge both, which is a
    recorded gap becoming a silencer for a defect nobody recorded.
    """
    driver = finding.get("driver")
    if driver:
        for gap in gaps:
            if (gap.get("driver") == driver
                    and finding.get("code") in (gap.get("acknowledges") or ())):
                return gap
        return None
    path = finding.get("path")
    for gap in gaps:
        surface = gap.get("surface") or ""
        if not path or not surface:
            continue
        # The declaration is written `<runtime> <path_role> <relative>`. The
        # one that used to sit in the contract read `codex workspace AGENTS.md`
        # and was retired 2026-09-28; the format is unchanged, so that is still
        # the shape to write and the shape these tests inject. The observation
        # is an absolute path. The match is on the role and the relative part
        # joined by a slash — the first version compared the tail as prose and
        # so never matched a path at all, which made the acknowledgement
        # silently dead while every test of `_matches_a_declared_gap` in
        # isolation would have passed.
        runtime, _, rest = surface.partition(" ")
        relative = "/".join(rest.split()) if rest else ""
        if finding.get("runtime") == runtime and relative and path.endswith(relative):
            return gap
    return None


def _subject_was_observed(gap: dict, observed: dict | None,
                          observed_drivers: dict | None) -> bool:
    """Was the thing this gap is about actually looked at?

    Without this, an unobserved subject makes its recorded gap read as stale —
    "recorded but not observed; either it was fixed or the record is wrong" —
    when the truth is that nobody looked. That is the same collapse this module
    separates everywhere else (a finding is not a gap, and UNKNOWN is not a
    no), and it was live: `report(contract, observed={"codex": {}})` put the
    AGENTS.md gap in the stale list, on a run that read no surface at all.
    """
    if gap.get("driver"):
        if observed_drivers is None:
            return False
        return gap["driver"] in observed_drivers
    surface = gap.get("surface") or ""
    runtime, _, rest = surface.partition(" ")
    relative = "/".join(rest.split()) if rest else ""
    if not relative or not observed:
        return False
    for entry in (observed.get(runtime) or {}).values():
        for row in entry or ():
            if str(row.get("path") or "").endswith(relative):
                return True
    return False


def report(contract: dict, registry: dict | None = None,
           observed: dict | None = None,
           observed_drivers: dict | None = None) -> dict:
    """Everything above, in one record, with what could not be established
    kept separate from what was found wrong.

    The separation is the whole point. `findings` are defects in the
    declaration; `unknowns` are questions this node could not answer; and
    `acknowledged` are defects the contract already records. Merging any two
    of them would make "we did not look" or "we know" read as "we looked and
    it is fine" — the same collapse the capability resolver refuses between
    UNKNOWN and UNAVAILABLE, and between a finding and an exemption.
    """
    gaps = declared_gaps(contract)
    findings = check_contract(contract)
    unknowns: list[dict] = []
    acknowledged: list[dict] = []

    def route(items) -> None:
        """One measurement, sorted into the three places it can belong.

        Written once because the surface half and the driver half must not
        disagree about what an UNKNOWN is or about when a defect is
        acknowledged — two copies of this is how the driver half would quietly
        become the one that turns a gap into a finding.
        """
        for item in items:
            if item.get("severity") == "UNKNOWN":
                unknowns.append(item)
                continue
            gap = _matches_a_declared_gap(item, gaps)
            if gap is not None:
                acknowledged.append({**item, "declared_gap": gap})
            else:
                findings.append(item)

    if registry is not None:
        findings += check_registry(registry, contract)
    else:
        unknowns.append(_finding(
            "REGISTRY_NOT_SUPPLIED",
            "no fleet registry was given, so which devices claim which agents "
            "was not checked"))
    if observed is not None:
        route(check_observed(observed, contract))
    else:
        unknowns.append(_finding(
            "SURFACES_NOT_OBSERVED",
            "no surfaces were observed on this device, so whether either "
            "adapter is installed here was not checked"))
    if observed_drivers is not None:
        route(check_drivers(observed_drivers, contract))
    else:
        unknowns.append(_finding(
            "DRIVERS_NOT_OBSERVED",
            "no declared drivers were observed on this device, so whether "
            "anything runs at session start or turn start here was not checked"))

    stale_gaps = [g for g in gaps
                  if _subject_was_observed(g, observed, observed_drivers)
                  and not any(a.get("declared_gap") is g for a in acknowledged)]

    return {
        "schema": SCHEMA,
        "runtimes": list(contract.get("ids") or ()),
        "compared_fields": list((contract.get("parity") or {}).get("compared_fields") or ()),
        "allowed_differences": list(
            (contract.get("parity") or {}).get("allowed_differences") or ()),
        "drivers": [d.get("name") for d in declared_drivers(contract)],
        "findings": findings,
        "unknowns": unknowns,
        "acknowledged": acknowledged,
        # The other direction, and the one that keeps a recorded gap honest: a
        # gap written down and never observed again is either fixed or noise.
        # Only gaps whose subject was actually measured can be either.
        "unobserved_declared_gaps": [g.get("surface") or g.get("driver")
                                     for g in stale_gaps],
        "verdict": "CONFORMANT" if not findings else "DEFECT",
        # Stated in the record, because a reader who takes CONFORMANT as "the
        # two runtimes behave the same" has taken it as something this check
        # cannot establish.
        "scope": ("structural: declared ids, adapter surfaces, and what is "
                  "present on this device. The behavioural smoke in "
                  "`docs/claude-codex-parity.md` requires a session in each "
                  "runtime and is not inferred here."),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--contract", required=True)
    parser.add_argument("--registry")
    parser.add_argument("--observed")
    parser.add_argument("--observed-drivers", dest="observed_drivers",
                        help="what a caller measured about the declared "
                             "drivers; a separate file from "
                             "--observed because it answers a separate "
                             "question")
    args = parser.parse_args(argv)

    import yaml
    contract = yaml.safe_load(Path(args.contract).read_text(encoding="utf-8"))
    registry = (yaml.safe_load(Path(args.registry).read_text(encoding="utf-8"))
                if args.registry else None)
    observed = (json.loads(Path(args.observed).read_text(encoding="utf-8"))
                if args.observed else None)
    observed_drivers = (
        json.loads(Path(args.observed_drivers).read_text(encoding="utf-8"))
        if args.observed_drivers else None)

    result = report(contract, registry, observed, observed_drivers)
    json.dump(result, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0 if result["verdict"] == "CONFORMANT" else 1


if __name__ == "__main__":
    raise SystemExit(main())
