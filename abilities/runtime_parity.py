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
  2. Every device that claims to run an agent names one that exists.
     `fleet/registry.yaml` has three devices each listing two agents, and
     nothing compared those lists against the closed `ids` set.
  3. On this host, each declared surface is either present or reported absent.
     Read-only; nothing is created.

Why the compared fields are global
----------------------------------
`capabilities.consumable_provider_kinds`, `skill_discovery` and
`session_lifecycle` are single values, not per-runtime maps. That looks like
the comparison is vacuous — two records compared, and the states are shared —
and it is worth being explicit that it is not an oversight: the contract's own
rule says adding a runtime means adding an `instruction_surfaces` and an
`mcp_surfaces` entry "and nothing else". The shared values ARE the claim, and
the per-runtime parts are exactly `allowed_differences`.

So parity reduces to key-set agreement, and the check is that the key sets
agree — not a field-by-field comparison of two records, because there is only
one record's worth of shared state and that is the design.
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
    return findings


def check_registry(registry: dict, contract: dict) -> list[dict]:
    """Does every device that claims an agent claim one that exists?"""
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
    return findings


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
    """
    path = finding.get("path")
    for gap in gaps:
        surface = gap.get("surface") or ""
        if not path or not surface:
            continue
        # The declaration is written `<runtime> <path_role> <relative>`, as in
        # `codex workspace AGENTS.md`; the observation is an absolute path. The
        # match is on the role and the relative part joined by a slash — the
        # first version compared the tail as prose and so never matched a path
        # at all, which made the acknowledgement silently dead while every
        # test of `_matches_a_declared_gap` in isolation would have passed.
        runtime, _, rest = surface.partition(" ")
        relative = "/".join(rest.split()) if rest else ""
        if finding.get("runtime") == runtime and relative and path.endswith(relative):
            return gap
    return None


def report(contract: dict, registry: dict | None = None,
           observed: dict | None = None) -> dict:
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
    if registry is not None:
        findings += check_registry(registry, contract)
    else:
        unknowns.append(_finding(
            "REGISTRY_NOT_SUPPLIED",
            "no fleet registry was given, so which devices claim which agents "
            "was not checked"))
    if observed is not None:
        for item in check_observed(observed, contract):
            if item.get("severity") == "UNKNOWN":
                unknowns.append(item)
                continue
            gap = _matches_a_declared_gap(item, gaps)
            if gap is not None:
                acknowledged.append({**item, "declared_gap": gap})
            else:
                findings.append(item)
    else:
        unknowns.append(_finding(
            "SURFACES_NOT_OBSERVED",
            "no surfaces were observed on this device, so whether either "
            "adapter is installed here was not checked"))

    stale_gaps = [g for g in gaps
                  if not any(a.get("declared_gap") is g for a in acknowledged)]

    return {
        "schema": SCHEMA,
        "runtimes": list(contract.get("ids") or ()),
        "compared_fields": list((contract.get("parity") or {}).get("compared_fields") or ()),
        "allowed_differences": list(
            (contract.get("parity") or {}).get("allowed_differences") or ()),
        "findings": findings,
        "unknowns": unknowns,
        "acknowledged": acknowledged,
        # The other direction, and the one that keeps a recorded gap honest: a
        # gap written down and never observed again is either fixed or noise.
        "unobserved_declared_gaps": [g.get("surface") for g in stale_gaps],
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
    args = parser.parse_args(argv)

    import yaml
    contract = yaml.safe_load(Path(args.contract).read_text(encoding="utf-8"))
    registry = (yaml.safe_load(Path(args.registry).read_text(encoding="utf-8"))
                if args.registry else None)
    observed = (json.loads(Path(args.observed).read_text(encoding="utf-8"))
                if args.observed else None)

    result = report(contract, registry, observed)
    json.dump(result, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0 if result["verdict"] == "CONFORMANT" else 1


if __name__ == "__main__":
    raise SystemExit(main())
