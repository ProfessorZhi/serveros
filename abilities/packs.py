#!/usr/bin/env python3
"""Decide whether a pack names abilities that exist, and say which do not.

    python3 packs.py --contract pack-contract.yaml --catalogue names.txt \\
        --catalogue-revision <sha> [--catalogue-repo <path>]

`abilities/pack-contract.yaml` declared a closure rule and nobody implemented
it:

    every member id must exist in the pinned ability-management manifest at the
    revision the pack declares. A pack naming an ability that does not exist is
    refused at build time, not carried with a marker beside it.

Nothing refused anything, because nothing read the file. A `grep` over the tree
for its name returned one hit: the contract itself. Three of the four packs
below name an ability that resolves to nothing, and the reason nobody noticed
is the reason this round keeps finding — a rule that is only written down is a
rule that has never been tested, and an untested rule has the same failure rate
as no rule with better optics.

This module is the checker. Like `resolver.py` beside it, it is a pure
function: it reads no file, runs no command, opens no socket and looks at no
clock. The catalogue arrives as a set of names. That is what lets the closure of
a three-pack contract be asserted in a unit test, and it is what keeps the
answer reproducible — the same contract against the same catalogue always
produces the same verdict, on any machine.

What it is NOT
--------------
It is not a resolver. `resolver.py` answers "can THIS device do this NOW" and
consults eight inputs; this answers one question with two inputs — is every
name in this pack a name in that catalogue — and it is asked once, at build
time, about no device in particular. A pack is a statement about the catalogue,
not about a machine.

Two questions, kept apart
-------------------------
A pack can fail for two unrelated reasons and they are answered separately,
because they have different repairs:

  * a MEMBER does not resolve  ->  fix the pack or fix the catalogue
  * the PIN does not match     ->  revalidate and repin

Collapsing them is the mistake `publishing.rule` names: a published pack pins
"the ability-management revision its member ids were validated against.
Without that pin the pack's closure was true of a catalogue that no longer
exists." A report that answered only the first question would go green on a
pack whose closure was established against a catalogue nobody can produce.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCHEMA = "serveros/ability-pack.v1"

# Member-level. There is deliberately no "unknown": a name is in the catalogue
# or it is not, and a status that meant "not checked" would let a build report
# a pack as closed without having looked.
MEMBER_STATUS = ("RESOLVED", "MISSING")

# Pack-level. `CLOSED` is the only one a build may ship.
#   PARTIAL      a required member is missing; the pack is usable and incomplete
#   UNAVAILABLE  every required member is missing; there is nothing to use
PACK_STATUS = ("CLOSED", "PARTIAL", "UNAVAILABLE")

# The pin, answered independently of the members.
PIN_STATUS = ("PIN_MATCH", "PIN_MISMATCH", "PIN_ABSENT")

# A member id is a name and nothing else. `rules` in the contract is explicit:
# "A pack names abilities. It never names a provider, a device or a runtime —
# those are resolved, and a pack that names one has decided something it cannot
# know." So the prefix is checkable, and it is checked rather than stripped:
# a member written `provider:feishu-mcp` would otherwise be looked up as an
# ability called `provider:feishu-mcp`, miss, and be reported as a missing
# ability — a wrong answer to a question nobody asked, in place of the refusal
# the rule calls for.
MEMBER_PREFIX = "ability:"

# `record.optional_fields` plus the one field `publishing.rule` needs and the
# record shape had no room for. `version` is in `forbidden_fields` and is
# checked here rather than trusted: a pack with its own version would need
# releasing, and the abilities inside it already carry revisions.
ALLOWED_KEYS = ("id", "name", "purpose", "members", "description", "domain",
                "note", "authority")
FORBIDDEN_KEYS = ("version", "payload", "path", "file", "files")


class ContractInvalid(Exception):
    """The contract, or a pack in it, is malformed. Never a member verdict."""


def member_name(member_id: object) -> str:
    """`ability:test-management` -> `test-management`, or raise.

    Raising rather than returning None, because the caller cannot tell a
    malformed member from a missing one and must not be asked to.
    """
    if not isinstance(member_id, str):
        raise ContractInvalid(f"member id must be a string, got {member_id!r}")
    if not member_id.startswith(MEMBER_PREFIX):
        raise ContractInvalid(
            f"member id {member_id!r} does not start with {MEMBER_PREFIX!r}. A "
            "pack names abilities; a provider, a device or a runtime is "
            "resolved, not named")
    name = member_id[len(MEMBER_PREFIX):]
    if not name:
        raise ContractInvalid(f"member id {member_id!r} names nothing after the prefix")
    return name


def _validate_pack(pack: object) -> dict:
    if not isinstance(pack, dict):
        raise ContractInvalid(f"a pack must be a mapping, got {pack!r}")
    for key in ("id", "name", "purpose", "members"):
        if not pack.get(key):
            raise ContractInvalid(f"pack {pack.get('id')!r} has no {key!r}")
    for key in pack:
        if key in FORBIDDEN_KEYS:
            raise ContractInvalid(
                f"pack {pack['id']!r} carries {key!r}. A pack is names: a pack "
                "with a payload is a composition, and this product already has "
                "one composition authority")
        if key not in ALLOWED_KEYS:
            raise ContractInvalid(
                f"pack {pack['id']!r} carries unknown field {key!r}; "
                f"allowed: {list(ALLOWED_KEYS)}")
    members = pack["members"]
    if not isinstance(members, list) or not members:
        raise ContractInvalid(f"pack {pack['id']!r} has no members")
    for member in members:
        if not isinstance(member, dict):
            raise ContractInvalid(f"pack {pack['id']!r}: a member must be a mapping")
        role = member.get("role")
        if role not in ("required", "optional"):
            raise ContractInvalid(
                f"pack {pack['id']!r}: member {member.get('ability')!r} has role "
                f"{role!r}; the contract allows required | optional, and role is "
                "the ONLY thing a pack may say about criticality")
        member_name(member.get("ability"))
    return pack


def _resolve_pack(pack: dict, catalogue: frozenset[str]) -> dict:
    resolved, unresolved = [], []
    for member in pack["members"]:
        name = member_name(member["ability"])
        line = {"ability": member["ability"], "name": name, "role": member["role"]}
        if name in catalogue:
            resolved.append(line)
        else:
            # Kept in a list of its own rather than filtered out. `resolution`
            # in the contract says what happens to an unresolved member and
            # the first thing it says is `never: silently dropped` — so the
            # count below is asserted against the member count, and a future
            # edit that drops one has to also make that assertion false.
            unresolved.append(line)

    required = [m for m in pack["members"] if m["role"] == "required"]
    required_missing = [u for u in unresolved if u["role"] == "required"]
    if not required:
        status = "CLOSED"
    elif len(required_missing) == len(required):
        status = "UNAVAILABLE"
    elif required_missing:
        status = "PARTIAL"
    else:
        status = "CLOSED"

    total = len(resolved) + len(unresolved)
    if total != len(pack["members"]):
        raise ContractInvalid(
            f"pack {pack['id']!r}: {total} members accounted for, "
            f"{len(pack['members'])} declared. An unresolved member was dropped")

    return {
        "id": pack["id"],
        "name": pack["name"],
        "status": status,
        "members_total": total,
        "resolved": resolved,
        "unresolved": unresolved,
        # Optional members are REPORTED, not enforced — `closure.scope` says
        # so, and it is the right split: a pack is usable without its optional
        # members by definition, so refusing the build would make `optional`
        # mean nothing. They are still listed, so "the pack is closed" never
        # prints beside an unresolved name without saying which.
        "unresolved_required": required_missing,
        "unresolved_optional": [u for u in unresolved if u["role"] == "optional"],
        "refused": bool(required_missing),
    }


def resolve(contract: dict, catalogue, *, catalogue_revision: str,
            catalogue_repo: str | None = None) -> dict:
    """The whole verdict. `catalogue` is any iterable of ability names."""
    if not isinstance(contract, dict):
        raise ContractInvalid("the pack contract must be a mapping")
    names = frozenset(str(n) for n in catalogue)
    if not catalogue_revision:
        raise ContractInvalid(
            "a closure answer must name the catalogue revision it was computed "
            "against; without it the verdict is about a catalogue that cannot "
            "be produced again")

    packs = contract.get("packs")
    if not isinstance(packs, list) or not packs:
        raise ContractInvalid("the pack contract declares no packs")

    results, seen = [], set()
    for raw in packs:
        pack = _validate_pack(raw)
        if pack["id"] in seen:
            raise ContractInvalid(f"duplicate pack id {pack['id']!r}")
        seen.add(pack["id"])
        results.append(_resolve_pack(pack, names))

    pins = _pin_verdicts(contract, catalogue_revision, catalogue_repo)

    return {
        "schema": SCHEMA,
        "catalogue": {"revision": catalogue_revision, "repo": catalogue_repo,
                      "abilities": len(names)},
        "pins": pins,
        "packs": results,
        "refused": [r["id"] for r in results if r["refused"]],
        "closed": [r["id"] for r in results if r["status"] == "CLOSED"],
    }


def _pin_verdicts(contract: dict, catalogue_revision: str,
                  catalogue_repo: str | None) -> dict:
    """Whether each pack's pin is the revision just checked.

    Separate from the member verdict and reported for every pack even when the
    members resolve, because a pack whose members all exist is exactly the pack
    whose stale pin is invisible: the closure answer is CORRECT and it is about
    the wrong catalogue. The name for that in this product is PIN LAG — a pin
    copied from the declaration beside it rather than from the artifact it
    identifies — and the only gate that finds it is one that compares the pin
    to the thing actually read.
    """
    verdicts, declared = [], []
    for pack in contract.get("packs") or []:
        authority = pack.get("authority") or {}
        pin = authority.get("revision") if isinstance(authority, dict) else None
        if not pin:
            status = "PIN_ABSENT"
        elif pin != catalogue_revision:
            status = "PIN_MISMATCH"
        else:
            status = "PIN_MATCH"
        declared.append(pin)
        verdicts.append({
            "pack": pack.get("id"),
            "pinned": pin,
            "checked": catalogue_revision,
            "status": status,
            "because": {
                "PIN_MATCH": "the closure above was computed against the pinned catalogue",
                "PIN_MISMATCH": (
                    f"the pack pins {pin} but this verdict is about "
                    f"{catalogue_revision}. The members below may all resolve "
                    "here and still not resolve at the pinned revision — the "
                    "repair is to revalidate and repin, not to read this as a "
                    "clean result"),
                "PIN_ABSENT": (
                    "the pack pins no revision, so its closure was true of "
                    "whatever catalogue happened to be on disk. Publishing it "
                    "would publish a claim with no subject"),
            }[status],
        })
    return {
        "checked": catalogue_revision,
        "packs": verdicts,
        "all_match": bool(verdicts) and all(v["status"] == "PIN_MATCH" for v in verdicts),
        "distinct_pins": sorted({p for p in declared if p}),
    }


def summarize(report: dict) -> str:
    """One line per pack, and a line for the pin. For a person reading a build."""
    lines = []
    for pack in report["packs"]:
        mark = "OK  " if pack["status"] == "CLOSED" else "FAIL"
        lines.append(f"{mark} {pack['id']:<38} {pack['status']:<12} "
                     f"{len(pack['resolved'])}/{pack['members_total']} resolved")
        for member in pack["unresolved"]:
            lines.append(f"       - {member['ability']} ({member['role']}) "
                         f"does not exist in the catalogue")
    if not report["pins"]["all_match"]:
        for verdict in report["pins"]["packs"]:
            if verdict["status"] != "PIN_MATCH":
                lines.append(f"FAIL {verdict['pack']:<38} {verdict['status']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--contract", type=Path, default=Path("abilities/pack-contract.yaml"))
    parser.add_argument("--catalogue", type=Path,
                        help="file of ability names, one per line")
    parser.add_argument("--catalogue-revision", required=True)
    parser.add_argument("--catalogue-repo")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        import yaml
    except ImportError:  # pragma: no cover - environment guarantee
        sys.stderr.write("ERROR: PyYAML is required to read the pack contract\n")
        return 2

    contract = yaml.safe_load(args.contract.read_text(encoding="utf-8"))
    names = []
    if args.catalogue:
        names = [line.strip() for line in args.catalogue.read_text(encoding="utf-8").splitlines()
                 if line.strip() and not line.startswith("#")]
    report = resolve(contract, names, catalogue_revision=args.catalogue_revision,
                     catalogue_repo=args.catalogue_repo)

    if args.json:
        json.dump(report, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
    else:
        sys.stdout.write(summarize(report) + "\n")
    return 1 if (report["refused"] or not report["pins"]["all_match"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
