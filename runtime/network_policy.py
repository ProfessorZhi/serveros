#!/usr/bin/env python3
"""Network safety: the decision, and the reason it was reached.

Implements `network-contract.yaml`. Read that file first; the reasoning lives
there and is not repeated here.

    python3 network_policy.py --candidate '{"network_name": "x",
        "subnet": "10.20.0.0/24", "owner_ref": "actor:..."}' --evidence f.json
    python3 network_policy.py --render result.json --level L2

What is pure and what is not
----------------------------
`evaluate` reads nothing. Every input — the candidate, the evidence, the policy
— is a record handed in, exactly as in `approval.py` and
`abilities/resolver.py`. That is not tidiness: this is the function that decides
whether a change to a shared host is permitted, and a decision that reads a
global is a decision that cannot be reproduced in a test. The host reads live in
`network_evidence.py` and the plan/executor live in `network_mutation.py`, so
the three halves can be tested apart and only the last one can write.

The one asymmetry
-----------------
`network-contract.yaml` states it in the header and it is the reason this module
is not the monitor. Evidence that could not be read DENIES here. The same
condition is `NO KNOWN CONFLICT` and no alert in `ops/scripts/monitor`, because
the two are answering different questions: the monitor describes the host, this
authorizes a change to it. An authorization granted without evidence is an
authorization nobody made.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - environment guarantee
    sys.stderr.write("ERROR: PyYAML is required to read the network contract\n")
    raise SystemExit(2)

_HERE = Path(__file__).resolve().parent
CONTRACT = _HERE / "network-contract.yaml"

SCHEMA = "serveros/network.v1"

DECISIONS = ("ALLOW", "REQUIRE_APPROVAL", "DENY")
COVERAGE = ("COMPLETE", "INCOMPLETE", "ABSENT", "UNREADABLE")
LEVELS = ("L0", "L1", "L2", "L3")

# The reserved classes, in the order they are reported. Named here as well as
# in the contract because the contract is prose to a reader and this is the
# list the code iterates; `tests/test_network_policy.py` asserts the two agree,
# so a class added to one and not the other is a failure rather than a
# collision nobody looks for.
RESERVED_CLASSES = (
    ("corporate_reserved_cidrs", "corporate"),
    ("vpn_reserved_cidrs", "vpn"),
    ("infrastructure_reserved_cidrs", "infrastructure"),
)

# The section a host profile keeps this policy under, and the names the policy
# itself declares. Both are named here for the reason `RESERVED_CLASSES` is:
# the contract is prose to a reader and this is what the code reads, and
# `tests/test_network_policy.py` asserts the two agree.
#
# The second of these exists to tell one silence from another. A document with
# no `network_policy` section at all is an operator who has not authored the
# network part yet; a document carrying these names AT THE TOP LEVEL is an
# operator who has authored it a level too high. Both refuse, and the two
# refusals send the reader to two different repairs — so the difference has to
# be visible rather than collapsed into "there is no policy".
POLICY_SECTION = "network_policy"
POLICY_FIELDS = (
    "allowed_pools",
    "corporate_reserved_cidrs",
    "vpn_reserved_cidrs",
    "infrastructure_reserved_cidrs",
    "coverage",
)

# How many addresses one preflight may ask the kernel about. The number is a
# bound on the cost of a decision, not a claim about the host: a candidate
# overlapping a hundred destinations is denied on the FIRST overlap it is shown
# and does not need the other ninety-nine probed to say so. Truncation is
# reported (`probe_targets_dropped`) rather than silent — a cap nobody is told
# about reads as "we looked at everything".
MAX_PROBE_TARGETS = 64

# What a candidate must HAVE to be evaluable at all. This is the one part of
# the field set that stays a Python constant, because it is the only part with
# a behaviour attached: a missing required field is a `Rejected` and not a code,
# and that boundary is the whole shape/verdict distinction.
#
# `subnet` is deliberately not here. A request that names a network and no
# subnet IS a candidate — it is the exact candidate `NETWORK_SUBNET_UNDECLARED`
# was written for — while a request naming no network at all is not a candidate
# and has no code to be refused with. Raising for the first would turn a refusal
# with a repair into a traceback. Which keys beyond these are accepted is read
# from the contract rather than restated; see `candidate_fields`.
CANDIDATE_REQUIRED = ("network_name", "owner_ref")


class Rejected(ValueError):
    """The request or record cannot be used at all — as opposed to a candidate
    that was evaluated and refused, which is an answer and has a code."""


# ---------------------------------------------------------------------------
# The contract
# ---------------------------------------------------------------------------

def contract() -> dict:
    try:
        data = yaml.safe_load(CONTRACT.read_text(encoding="utf-8")) or {}
    except OSError as exc:
        raise Rejected(f"{CONTRACT} could not be read: {exc}") from exc
    if not isinstance(data, dict):
        raise Rejected(f"{CONTRACT} must contain a mapping")
    return data


def code_precedence() -> tuple:
    """The order refusals are reported in, from the contract.

    Read rather than restated: this list is the order of the requester's next
    move, and a second copy in Python would agree with it until the day one of
    them was reordered and the other was not.
    """
    return tuple((contract().get("precedence") or {}).get("order") or ())


def code_rule(name: str) -> dict:
    return (contract().get("codes") or {}).get(name) or {}


# ---------------------------------------------------------------------------
# The candidate
# ---------------------------------------------------------------------------

def _names(value) -> tuple:
    """The keys of a contract section, whether it is a mapping or a list.

    The contract writes the candidate sections as mappings because each key
    carries the sentence explaining why it is where it is (`subnet`'s entry is
    most of the reason that block exists). A section written as a plain list
    still yields its names, so a future edit that drops the prose does not
    silently empty this list and turn every field into an unrecognised one.
    """
    if isinstance(value, dict):
        return tuple(value.keys())
    return tuple(value or ())


def candidate_fields() -> tuple:
    """Every key a candidate may carry, in the contract's order.

    Read rather than restated, for the reason `code_precedence` reads: a second
    copy of the field list in Python agrees with the contract until the day one
    of them gains a field and the other does not — and the failure that produces
    is silent in the worse direction, because the contract would promise a
    field that `candidate()` drops on the floor.
    """
    block = contract().get("candidate") or {}
    fields: tuple = ()
    for section in ("required", "declared", "optional"):
        fields += _names(block.get(section))
    return fields


def candidate(**fields) -> dict:
    """A validated candidate. Raises `Rejected` on anything unusable.

    An unrecognised key is refused rather than dropped. A caller that passes
    `subnet_ip` instead of `subnet` and is quietly given a candidate with no
    subnet gets told `NETWORK_SUBNET_UNDECLARED` — a true statement about the
    candidate this function built and a false one about the request they made.
    """
    known = candidate_fields()
    unknown = sorted(set(fields) - set(known))
    if unknown:
        raise Rejected(f"unrecognised candidate field(s) {unknown}; known: "
                       f"{list(known)}")
    missing = [k for k in CANDIDATE_REQUIRED if not fields.get(k)]
    if missing:
        raise Rejected(f"candidate is missing {missing}")
    return {k: fields.get(k) for k in known}


def network_of(value) -> ipaddress.IPv4Network | ipaddress.IPv6Network | None:
    """`None` for a subnet that is absent or unreadable. Never a guess.

    `strict=False` is deliberate: `10.0.0.5/24` is a subnet with a host bit set,
    which docker accepts and normalises. Refusing it here would be this module
    enforcing a rule the executor does not have.
    """
    if value in (None, ""):
        return None
    try:
        return ipaddress.ip_network(str(value), strict=False)
    except ValueError:
        return None


def gateway_is_inside(candidate_record: dict) -> bool | None:
    """`None` when there is no gateway to check — which is not `False`.

    A gateway outside its own subnet is a request that cannot be honoured, and
    the alternative to reporting it is docker failing later with a message that
    does not say which field was wrong.
    """
    raw = candidate_record.get("gateway")
    if not raw:
        return None
    net = network_of(candidate_record.get("subnet"))
    host = network_of(raw)
    if net is None or host is None:
        return None
    return host.network_address in net


# ---------------------------------------------------------------------------
# The policy input
# ---------------------------------------------------------------------------

def policy_coverage(policy: dict | None) -> str:
    """COMPLETE, INCOMPLETE, ABSENT or UNREADABLE — four states, kept apart.

    `ABSENT` and `INCOMPLETE` deny identically and are reported apart, for the
    reason `resource_policy.py` keeps `NOT_DECLARED` and `UNREADABLE` apart: one
    sends an operator to write a file and the other sends them to fix a machine,
    and a reader told "incomplete" for both looks in the wrong place half the
    time.
    """
    if policy is None:
        return "ABSENT"
    if not isinstance(policy, dict) or policy.get("unreadable"):
        return "UNREADABLE"
    if not policy:
        return "ABSENT"
    if policy.get("no_network_section"):
        # A host profile was read and it carries no network section. NOT
        # ABSENT: `ABSENT` tells the operator to write a file, and the file is
        # already there and already being read — the thing that does not exist
        # is the section. `coverage_of.INCOMPLETE` in the contract is "the
        # document exists and names no pool", which is exactly this.
        return "INCOMPLETE"
    if str(policy.get("coverage") or "").upper() != "COMPLETE":
        return "INCOMPLETE"
    # A document cannot be COMPLETE and name nothing to allocate from: that
    # combination denies every candidate through the pool rule while claiming
    # the policy is finished. The claim is the thing that is wrong.
    if not _cidrs(policy, "allowed_pools"):
        return "INCOMPLETE"
    return "COMPLETE"


def _cidrs(policy: dict, key: str) -> list:
    """The parsed CIDRs under one policy key, skipping anything unreadable.

    Skips rather than raises: one typo in a list of twenty ranges should not
    make the other nineteen stop being considered. What it must NOT do is count
    — the count of what was read is reported beside the count of what was
    written, so a typo is visible instead of silent.
    """
    out = []
    for entry in policy.get(key) or []:
        if isinstance(entry, dict):
            value = entry.get("cidr")
        else:
            value = entry
        net = network_of(value)
        if net is not None:
            out.append((net, entry))
    return out


def policy_from_document(document: dict | None) -> dict:
    """A host profile's network section, as this module reads it.

    The document is the operator's; this function does not add to it. In
    particular it does not fill `allowed_pools` from the docker networks that
    exist — see `policy.derived_from_host_is_forbidden` in the contract.
    """
    if document is None:
        return {}
    if not isinstance(document, dict):
        return {"unreadable": True}
    section = document.get(POLICY_SECTION)
    if section is None:
        if any(name in document for name in POLICY_FIELDS):
            # The policy is present but a level too high. Refusing is
            # unavoidable — reading these keys off the root would mean this
            # function guessing which part of a larger document is meant to be
            # the policy — but refusing QUIETLY is the defect: the operator
            # followed the contract's field list and would be told there is no
            # policy at the very path they passed.
            return {"unreadable": True}
        # A host profile with no network section is a host whose network part
        # has not been authored. Not `{}`: `{}` is "there is no document", and
        # this document exists.
        return {"no_network_section": True}
    if not isinstance(section, dict):
        return {"unreadable": True}
    return dict(section)


def read_policy(path) -> dict:
    """Read a host profile and return its network policy.

    A missing file is `{}` (ABSENT). A file that exists and cannot be parsed is
    `{"unreadable": True}` (UNREADABLE). Both deny. The difference is which
    person is sent to look.
    """
    if not path:
        return {}
    target = Path(path)
    if not target.exists():
        return {}
    try:
        document = yaml.safe_load(target.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {"unreadable": True}
    return policy_from_document(document)


# ---------------------------------------------------------------------------
# Overlap. ONE rule, used by both the conflict report and the probe targets.
# ---------------------------------------------------------------------------
#
# Two functions iterate the same sources, and they are two because a conflict is
# what the requester is told and a probe target is what the kernel is asked.
# They share `_overlaps` so the two cannot come to different conclusions about
# what "overlaps" means.

def _overlaps(candidate_net, evidence: dict) -> list[dict]:
    """Everything the candidate subnet would sit on top of.

    The sources are the contract's `evidence.sources` list. `host_routes` is
    filtered the way the monitor filters it and for the same reason: a route
    with `protocol: kernel` and `scope: link` is the on-link neighbourhood the
    host already has, and a docker network that covers it is not news.
    """
    found: list[dict] = []

    for key, label in RESERVED_CLASSES:
        for net, entry in _cidrs(evidence.get("policy") or {}, key):
            if candidate_net.overlaps(net):
                found.append({
                    "kind": "RESERVED",
                    "class": label,
                    "source": key,
                    "other": str(net),
                    "provenance": (entry.get("provenance") if isinstance(entry, dict) else None),
                    "because": "a reserved range is not this host's to allocate",
                })

    for row in evidence.get("host_iface_cidrs") or []:
        net = network_of(row.get("cidr"))
        if net is not None and candidate_net.overlaps(net):
            found.append({
                "kind": "HOST_INTERFACE",
                "source": "host_iface_cidrs",
                "other": str(net),
                "detail": f"{row.get('dev')} is {row.get('cidr')}",
                "because": "this coverage would shadow the host's own address",
            })

    for row in evidence.get("host_routes") or []:
        net = network_of(row.get("dst"))
        if net is None or not candidate_net.overlaps(net):
            continue
        found.append({
            "kind": "HOST_ROUTE",
            "source": "host_routes",
            "other": str(net),
            "detail": (f"via {row.get('gateway') or 'on-link'} dev {row.get('dev')}"
                       + (" (link down)" if row.get("linkdown") else "")),
            "because": "an explicit route to a real destination",
        })

    for row in evidence.get("docker_subnets") or []:
        net = network_of(row.get("subnet"))
        if net is not None and candidate_net.overlaps(net):
            seen = row.get("containers")
            found.append({
                "kind": "DOCKER_NETWORK",
                "source": "docker_subnets",
                "other": str(net),
                "detail": (f"{row.get('name')} ({row.get('id') or '?'}"
                           f"{', ' + row.get('dev') if row.get('dev') else ''}"
                           f", {seen if seen is not None else '?'} containers)"),
                "because": "the subnet is already allocated to an existing network",
            })

    return found


def witness_addresses(candidate_record: dict, evidence: dict) -> dict:
    """The addresses this preflight must ask the kernel about.

    Two kinds, and the second is the one worth explaining:

      * one representative of the candidate itself, so "the kernel has an
        answer for a fresh range" is established at all;
      * one representative of every overlap `_overlaps` found — plus, for a
        `/32`, its immediate neighbours.

    The neighbours are not decoration. This host carries a hand-written `/32`
    that exempts one address from a docker subnet covering its whole range, and
    the addresses one either side of it are already being routed into a bridge
    that is DOWN. A gate that probed only the `/32` would report the range as
    reachable, which is exactly what a person looking at that route believes.

    Returns `{"targets": [...], "dropped": N}`. `dropped` is non-zero only past
    `MAX_PROBE_TARGETS`, and it is returned rather than logged so that a caller
    cannot read a truncated answer as a complete one.
    """
    net = network_of(candidate_record.get("subnet"))
    if net is None:
        return {"targets": [], "dropped": 0}

    targets: list[str] = []

    def add(value) -> None:
        text = str(value)
        if text not in targets:
            targets.append(text)

    add(net.network_address)
    if net.num_addresses > 1:
        add(net.broadcast_address if net.version == 4 else net[-1])

    for conflict in _overlaps(net, evidence):
        other = network_of(conflict["other"])
        if other is None:
            continue
        add(other.network_address)
        if other.num_addresses <= 2:
            # A /32 or /128 names one host. Its neighbours are not in the
            # conflict and they are the addresses that reveal whether the
            # exemption is narrow or the whole range is a hole.
            for delta in (-1, 1):
                try:
                    add(other.network_address + delta)
                except Exception:  # noqa: BLE001 - past the end of the space
                    pass

    dropped = max(0, len(targets) - MAX_PROBE_TARGETS)
    return {"targets": targets[:MAX_PROBE_TARGETS], "dropped": dropped}


# ---------------------------------------------------------------------------
# The decision
# ---------------------------------------------------------------------------

def evaluate(*, candidate: dict, evidence: dict, policy: dict,
             ownership: dict | None = None, recovery: str | None = None) -> dict:
    """Whether this network may be created. Pure: all three inputs are records.

    `candidate` should come from `candidate()`; a bare dict is accepted so a
    test can drive one malformed field at a time, and the shape rules below
    then fire rather than raising.
    """
    fired: list[str] = []
    detail: dict = {}

    composed = candidate.get("composed_by")
    raw_subnet = candidate.get("subnet")
    net = network_of(raw_subnet)

    # -- shape ------------------------------------------------------------
    if str(composed or "").lower() == "compose" and not raw_subnet:
        fired.append("COMPOSE_IMPLICIT_NETWORK")
    if net is None:
        fired.append("NETWORK_SUBNET_UNDECLARED")
        detail["subnet_as_given"] = raw_subnet

    inside = gateway_is_inside(candidate)
    if inside is False:
        fired.append("NETWORK_SUBNET_UNDECLARED")
        detail["gateway_outside_subnet"] = candidate.get("gateway")

    # -- policy -----------------------------------------------------------
    coverage = policy_coverage(policy)
    pools = _cidrs(policy, "allowed_pools")
    if coverage != "COMPLETE":
        fired.append("NETWORK_POLICY_INCOMPLETE")
        detail["coverage"] = coverage
    elif net is not None and not any(net.subnet_of(p) for p, _ in pools if p.version == net.version):
        # Only reachable with pools declared, which COMPLETE guarantees.
        fired.append("NETWORK_OUTSIDE_ALLOWED_POOL")

    # -- the world --------------------------------------------------------
    conflicts: list[dict] = []
    if net is not None:
        # The policy is handed to `_overlaps` through the evidence record so
        # that one function owns the question "what does this subnet sit on".
        conflicts = _overlaps(net, {**evidence, "policy": policy})

    probes = evidence.get("route_decisions") or {}
    required = witness_addresses(candidate, {**evidence, "policy": policy}) \
        if net is not None else {"targets": [], "dropped": 0}
    unprobed = [a for a in required["targets"] if not (probes.get(a) or {}).get("ok")]

    unknown = list(evidence.get("unknown") or [])
    for source in ("docker_subnets", "host_iface_cidrs", "host_routes"):
        if evidence.get(source) is None:
            unknown.append(f"{source} could not be read")
    if unprobed:
        unknown.append(
            f"the kernel was not asked about {len(unprobed)} of "
            f"{len(required['targets'])} addresses, so whether this range is "
            f"already shadowed was not established")
    if required["dropped"]:
        # Past `MAX_PROBE_TARGETS`. The same fact as `unprobed` one step
        # earlier: the addresses past the cap were never asked about, so the
        # record cannot say they are clear. Unreachable with an empty conflict
        # set — a candidate contributes two representatives and the cap is
        # sixty-four — so this never turns an ALLOW into a DENY, and it is here
        # so that stays a property of the arithmetic rather than of the gate
        # remembering to check.
        unknown.append(
            f"{required['dropped']} further addresses were not asked about "
            f"(the witness list is capped at {MAX_PROBE_TARGETS}), so the "
            f"ranges they stand for were not established")

    if conflicts:
        fired.append("NETWORK_CIDR_COLLISION")
    if unknown:
        fired.append("NETWORK_EVIDENCE_UNAVAILABLE")

    codes = [c for c in code_precedence() if c in set(fired)]
    codes += [c for c in fired if c not in codes]        # a code the contract
    #                                                      does not order yet
    network_decision = "DENY" if codes else "ALLOW"

    result = {
        "schema": SCHEMA,
        "decision": network_decision,
        "code": codes[0] if codes else None,
        "codes": codes,
        "reason": _reason(codes, coverage, conflicts, unknown, net, candidate),
        "subject": {
            "network_name": candidate.get("network_name"),
            "subnet": str(net) if net is not None else raw_subnet,
            "owner_ref": candidate.get("owner_ref"),
        },
        "coverage": coverage,
        "conflicts": conflicts,
        "unknown": unknown,
        "evidence": {
            "docker_subnet_count": len(evidence.get("docker_subnets") or []),
            "host_iface_count": len(evidence.get("host_iface_cidrs") or []),
            "host_route_count": len(evidence.get("host_routes") or []),
            "probe_targets": required["targets"],
            "probe_targets_dropped": required["dropped"],
            "route_decisions": {a: probes.get(a) for a in required["targets"]},
        },
    }
    return compose(network=result, ownership=ownership, recovery=recovery)


def compose(*, network: dict, ownership: dict | None, recovery: str | None) -> dict:
    """The strictest of the three inputs wins.

    `ownership` is C2's verdict record and `recovery` is the action's class from
    the contract. Both are optional, and an absent input is not a vote: a
    network-only caller gets the network answer, and the moment C2's gate is
    wired in the caller passes its record and the strictest of the two is what
    comes out. This is the ONLY producer of REQUIRE_APPROVAL — see the contract's
    `compose` block for why no network rule produces it.
    """
    votes = [network["decision"]]
    sources = [{"input": "network", "decision": network["decision"],
                "code": network.get("code")}]

    if ownership:
        decision = ownership.get("decision")
        if decision in DECISIONS:
            votes.append(decision)
            sources.append({"input": "ownership", "decision": decision,
                            "reason": ownership.get("reason")})
    if recovery == "IRREVERSIBLE":
        votes.append("REQUIRE_APPROVAL")
        sources.append({"input": "recovery", "decision": "REQUIRE_APPROVAL",
                        "reason": "an irreversible action is not the requester's alone"})

    order = {"DENY": 0, "REQUIRE_APPROVAL": 1, "ALLOW": 2}
    final = sorted(votes, key=lambda d: order[d])[0]
    return {**network, "decision": final, "composed_from": sources}


def _reason(codes, coverage, conflicts, unknown, net, candidate) -> str:
    """One sentence, and it names the fact the caller's next move depends on."""
    if not codes:
        return (f"{candidate.get('network_name')} {net} overlaps nothing this host "
                f"knows about, inside a declared pool, with the policy complete")
    if codes[0] == "COMPOSE_IMPLICIT_NETWORK":
        return ("this network is compose-created with no declared subnet, so the "
                "subnet would be chosen by compose at `up` time")
    if codes[0] == "NETWORK_SUBNET_UNDECLARED":
        return "the candidate does not name a usable subnet"
    if codes[0] == "NETWORK_POLICY_INCOMPLETE":
        return (f"this host's network policy is {coverage}, so a collision with "
                f"the ranges it would have named cannot be ruled out")
    if codes[0] == "NETWORK_OUTSIDE_ALLOWED_POOL":
        return "the subnet is outside every declared allowed pool"
    if codes[0] == "NETWORK_CIDR_COLLISION":
        first = conflicts[0]
        return (f"{net} overlaps {first['other']} ({first['kind']}: "
                f"{first.get('detail') or first.get('class')}), which after this "
                f"network exists would be routed into its bridge")
    return f"the host could not be asked: {unknown[0]}"


# ---------------------------------------------------------------------------
# Disclosure. The axis is `authority`; see the contract's `disclosure` block.
# ---------------------------------------------------------------------------

def disclose(result: dict, level: str = None) -> dict:
    """The result at one level, naming the level it came from.

    The default is L1 and a DENY promotes itself to L2. That promotion is the
    only place this module makes a choice on the caller's behalf, and it is the
    one `disclosure-contract.yaml` already argues for: a refusal's conflicts ARE
    its reason, and a refusal without its reason sends the caller to guess.
    """
    if level is None:
        level = "L2" if result.get("decision") == "DENY" else "L1"
    if level not in LEVELS:
        raise Rejected(f"unknown level {level!r}; known: {list(LEVELS)}")

    payload = {
        "L0": {"decision": result.get("decision"), "code": result.get("code")},
        "L1": {"decision": result.get("decision"), "code": result.get("code"),
               "reason": result.get("reason"), "subject": result.get("subject")},
        "L2": {"decision": result.get("decision"), "codes": result.get("codes"),
               "reason": result.get("reason"), "subject": result.get("subject"),
               "coverage": result.get("coverage"),
               "conflicts": result.get("conflicts")},
        "L3": {"decision": result.get("decision"), "codes": result.get("codes"),
               "reason": result.get("reason"), "subject": result.get("subject"),
               "coverage": result.get("coverage"),
               "conflicts": result.get("conflicts"),
               "unknown": result.get("unknown"),
               "evidence": result.get("evidence"),
               "composed_from": result.get("composed_from")},
    }[level]
    return {"axis": "authority", "level": level,
            "state": "DISCLOSED", "value": payload}


def render(result: dict, level: str = None) -> str:
    """The same thing as text, for a person reading a terminal or a card."""
    disclosed = disclose(result, level)
    value = disclosed["value"]
    decision = value.get("decision")
    mark = {"ALLOW": "✅", "REQUIRE_APPROVAL": "🟡", "DENY": "⛔"}.get(decision, "•")
    lines = [f"{mark} {decision}  {value.get('code') or ''}".rstrip()]

    if disclosed["level"] == "L0":
        return lines[0]

    lines.append(f"   {value.get('reason')}")
    subject = value.get("subject") or {}
    lines.append(f"   主体 {subject.get('network_name')} {subject.get('subnet')}")

    if disclosed["level"] in ("L2", "L3"):
        for conflict in value.get("conflicts") or []:
            lines.append(f"   ⚠ {conflict['other']}  {conflict['kind']}"
                         f"{'/' + conflict['class'] if conflict.get('class') else ''}"
                         f"  {conflict.get('detail') or ''}".rstrip())
        if value.get("coverage") != "COMPLETE":
            lines.append(f"   策略覆盖度 {value.get('coverage')}"
                         " —— 不能说明公司网段没被盖住")

    if disclosed["level"] == "L3":
        for line in value.get("unknown") or []:
            lines.append(f"   ? {line}")
        ev = value.get("evidence") or {}
        lines.append(f"   证据 docker 网段 {ev.get('docker_subnet_count')} · "
                     f"主机接口 {ev.get('host_iface_count')} · "
                     f"显式路由 {ev.get('host_route_count')} · "
                     f"内核探测 {len(ev.get('probe_targets') or [])} 个"
                     + (f"（截断 {ev['probe_targets_dropped']}）"
                        if ev.get("probe_targets_dropped") else ""))
        for addr, decision_row in (ev.get("route_decisions") or {}).items():
            row = decision_row or {}
            if row.get("ok"):
                lines.append(f"     {addr} → dev {row.get('dev')}"
                             + (f" via {row['via']}" if row.get("via") else ""))
            else:
                lines.append(f"     {addr} → 没问到")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI. `preflight` and `render` only; the executor is `network_mutation.py`.
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--candidate", help="JSON object")
    parser.add_argument("--evidence", help="JSON file holding the evidence record")
    parser.add_argument("--policy", help="host profile YAML holding network_policy")
    parser.add_argument("--render", help="a result JSON file to render")
    parser.add_argument("--level", choices=LEVELS)
    parser.add_argument("--codes", action="store_true")
    args = parser.parse_args(argv)

    if args.codes:
        for name in code_precedence():
            print(name)
        return 0

    if args.render:
        result = json.loads(Path(args.render).read_text(encoding="utf-8"))
        sys.stdout.write(render(result, args.level) + "\n")
        return 0

    if not args.candidate or not args.evidence:
        parser.error("--candidate and --evidence are required")
    from_candidate = json.loads(args.candidate)
    evidence = json.loads(Path(args.evidence).read_text(encoding="utf-8"))
    policy = read_policy(args.policy) if args.policy else {}
    result = evaluate(candidate=from_candidate, evidence=evidence, policy=policy)
    json.dump(result, sys.stdout, indent=2, sort_keys=True, default=str)
    sys.stdout.write("\n")
    return 0 if result["decision"] != "DENY" else 10


if __name__ == "__main__":
    raise SystemExit(main())
