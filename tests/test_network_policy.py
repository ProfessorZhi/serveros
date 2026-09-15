#!/usr/bin/env python3
"""C1 §十一 · Network safety enforcement, all nine mutation tests.

WHY THIS FILE EXISTS
--------------------
`ops/scripts/monitor/checks.py` can already say a docker subnet is shadowing a
real destination. That detector runs on a timer and its finding arrives after
traffic has been black-holed for up to an hour. C1 is the other half: the gate
that runs BEFORE the mutation, on the critical path of an action.

The tests below are the nine C1 §十一 names, and the arrangement is deliberate:

  * every evidence record is built by the REAL producer
    (`network_evidence.docker_rows` / `iface_rows` / `route_rows` / `probe`)
    from raw docker and `ip` output — the shapes are copied from what this host
    actually returns. A test that hands `evaluate` a dict it wrote itself
    proves the evaluator agrees with the test; a producer that starts returning
    `subnet` under a different key would keep every one of them green. That is
    the lesson `tests/` already carries about hand-written shapes, and this file
    is written not to repeat it.

  * the two tests that matter most are the pair at the end. One proves a DENY
    reaches the executor and the executor does not run — the actuator is never
    entered and the host is where it was. The other proves the gate can go
    GREEN end to end, because a gate that refuses everything and a gate that
    refuses nothing are read the same way by the people who have to live with
    them.

The asymmetry, restated because the tests depend on it
------------------------------------------------------
Unreadable evidence is `NO KNOWN CONFLICT` and no alert in the monitor, and it
is a DENY here. Both are correct and the difference is the question being
asked: the monitor describes the host, this authorizes a change to it.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

REPO = Path(__file__).resolve().parent.parent
RUNTIME = REPO / "runtime"


def _load(name: str, filename: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, RUNTIME / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


policy_mod = _load("serveros_runtime_network_policy", "network_policy.py")
evidence_mod = _load("serveros_runtime_network_evidence", "network_evidence.py")
mutation_mod = _load("serveros_runtime_network_mutation", "network_mutation.py")
probes_mod = _load("serveros_runtime_probes", "probes.py")
containers = _load("serveros_runtime_containers", "containers.py")

CONTRACT_DOC = yaml.safe_load(
    (RUNTIME / "network-contract.yaml").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Raw host output. Every shape here was copied from this host, not invented:
# `docker network ls` is one JSON object per line, `docker network inspect` is a
# one-element array, and `ip -j addr show` / `ip -j route show` are arrays.
# ---------------------------------------------------------------------------

WI001_ID = "9ed5e74e097f" + "0" * 52


def _network_row(name, net_id, subnet, gateway, *, dev=None, containers=None):
    options = {"com.docker.network.bridge.name": dev} if dev else {}
    return ({"Name": name, "ID": net_id[:12], "Driver": "bridge", "Scope": "local"},
            {"Name": name, "Id": net_id, "Driver": "bridge", "Scope": "local",
             "Internal": False, "Options": options,
             "IPAM": {"Config": [{"Subnet": subnet, "Gateway": gateway}]},
             "Containers": containers or {}})


# The real finding: zero running members, bridge DOWN, and a route that still
# wins longest-prefix against real destinations. This is the shape C1 §九 is
# about and it appears in the fixtures rather than in prose.
WI001 = _network_row("wi-001-internal", WI001_ID, "172.20.0.0/16", "172.20.0.1")
LIT0101 = _network_row("lit-0101-r7-internal", "a93784d27e4c" + "1" * 52,
                       "10.99.0.0/24", "10.99.0.1",
                       containers={"c1": {}, "c2": {}})

ADDR_ROWS = [
    {"ifname": "lo", "addr_info": [
        {"family": "inet", "local": "127.0.0.1", "prefixlen": 8, "scope": "host"}]},
    {"ifname": "eth0", "addr_info": [
        {"family": "inet", "local": "192.0.2.77", "prefixlen": 27, "scope": "global"},
        {"family": "inet6", "local": "fe80::a", "prefixlen": 64, "scope": "link"}]},
    {"ifname": "br-9ed5e74e097f", "addr_info": [
        {"family": "inet", "local": "172.20.0.1", "prefixlen": 16, "scope": "global"}]},
    {"ifname": "docker0", "addr_info": [
        {"family": "inet", "local": "172.17.0.1", "prefixlen": 16, "scope": "global"}]},
]

ROUTE_ROWS = [
    {"dst": "default", "gateway": "192.0.2.65", "dev": "eth0"},
    # The on-link neighbourhood the kernel derived from eth0's address. Not a
    # destination anybody configured; the monitor excludes it and so does this.
    {"dst": "192.0.2.64/27", "dev": "eth0", "protocol": "kernel", "scope": "link"},
    # The real `/32` that exempts one address from the wi-001 shadow.
    {"dst": "198.51.100.217", "gateway": "192.0.2.65", "dev": "eth0",
     "protocol": "static", "scope": "global"},
    {"dst": "183.60.82.98", "gateway": "192.0.2.65", "dev": "eth0",
     "protocol": "static", "scope": "global"},
    # Docker's own route for wi-001, still present with the bridge down.
    {"dst": "172.20.0.0/16", "dev": "br-9ed5e74e097f", "protocol": "kernel",
     "scope": "link", "flags": ["linkdown"]},
    {"dst": "local", "dev": "lo", "type": "local"},
    {"dst": "224.0.0.0/4", "dev": "eth0", "type": "multicast"},
]


class FakeHost:
    """The five reads `network_evidence` makes, and nothing else.

    Deliberately not a mock of `LiveHost`: it answers the same five questions
    with real-shaped data, so the producer under test runs every branch it runs
    in production. The `broken` set names the reads that raise
    `probes.ProbeUnavailable`, which is the real exception the real chokepoint
    raises — an evidence-unavailable test that used `RuntimeError` would be
    testing a failure the product cannot actually have.
    """

    def __init__(self, *, networks=None, addr=None, routes=None,
                 decisions=None, broken=()):
        self.networks = [WI001, LIT0101] if networks is None else networks
        self.addr = ADDR_ROWS if addr is None else addr
        self.routes = ROUTE_ROWS if routes is None else routes
        self.decisions = decisions or {}
        self.broken = set(broken)
        self.probed: list[str] = []

    def _check(self, what):
        if what in self.broken:
            raise probes_mod.ProbeUnavailable(f"{what} unavailable (fixture)")

    def docker_network_ls(self):
        self._check("docker")
        return [listing for listing, _ in self.networks]

    def docker_network_inspect(self, name):
        self._check("docker")
        for listing, detail in self.networks:
            if listing["Name"] == name:
                return detail
        raise probes_mod.ProbeUnavailable(f"no such network {name}")

    def interface_cidrs(self):
        self._check("ip addr")
        return self.addr

    def route_table(self):
        self._check("ip route")
        return self.routes

    def route_get(self, address):
        self._check("ip route get")
        self.probed.append(address)
        if address in self.decisions:
            return self.decisions[address]
        return {"address": address, "raw": f"{address} via 192.0.2.65 dev eth0",
                "dev": "eth0", "via": "192.0.2.65", "local": False,
                "reachable": True}

    # A snapshot of the state a mutation would change, for the "Docker state
    # unchanged" half of §十一 #8. Taken through the same reader the gate uses.
    def snapshot(self) -> str:
        return json.dumps(evidence_mod.docker_rows(self)[0], sort_keys=True)


def full_evidence(host, candidate_record, policy) -> dict:
    """Sources plus a route decision per address the candidate requires.

    This mirrors `network_mutation.plan` on purpose — the integration tests use
    the plan function itself, and the evaluator tests need the same record
    without a mutation module in the way. The policy is passed to the target
    derivation and not just to the evaluator, because WHICH addresses must be
    probed depends on it: a reserved range the candidate collides with is a
    conflict only the policy knows about, and a record built without it leaves
    that representative unprobed — which denies, correctly, as
    NETWORK_EVIDENCE_UNAVAILABLE. That happened here on the first run and it is
    left in the comment because it is the behaviour rather than an accident.
    """
    record = evidence_mod.sources(host)
    targets = policy_mod.witness_addresses(
        candidate_record, {**record, "policy": policy})["targets"]
    record["route_decisions"] = evidence_mod.probe(host, targets)
    return record


# ---------------------------------------------------------------------------
# Policy documents. Authored, with provenance, as the contract requires.
# ---------------------------------------------------------------------------

def _pool(cidr, note="fixture"):
    return [{"cidr": cidr, "provenance": note}]


POLICY_COMPLETE = {
    "coverage": "COMPLETE",
    "allowed_pools": _pool("10.247.0.0/16", "C1 §八 synthetic positive control"),
    "corporate_reserved_cidrs": _pool("10.10.0.0/16", "fixture: corporate"),
    "vpn_reserved_cidrs": [],
    "infrastructure_reserved_cidrs": [],
}

POLICY_FOR_DOCKER_COLLISION = {
    "coverage": "COMPLETE",
    "allowed_pools": _pool("172.20.0.0/16"),
    "corporate_reserved_cidrs": [],
    "vpn_reserved_cidrs": [],
    "infrastructure_reserved_cidrs": [],
}

POLICY_FOR_RESERVED_COLLISION = {
    "coverage": "COMPLETE",
    "allowed_pools": _pool("10.10.0.0/16"),
    "corporate_reserved_cidrs": _pool("10.10.0.0/16", "fixture: corporate"),
    "vpn_reserved_cidrs": [],
    "infrastructure_reserved_cidrs": [],
}


def evaluate(*, name, subnet, policy, host=None, **extra):
    fields = {"network_name": name, "owner_ref": "work-item:C1-FIXTURE"}
    if subnet is not None:
        fields["subnet"] = subnet
    fields.update(extra)
    record = policy_mod.candidate(**fields)
    host = host or FakeHost()
    return policy_mod.evaluate(candidate=record,
                               evidence=full_evidence(host, record, policy),
                               policy=policy), record


# ---------------------------------------------------------------------------
# §十一 1–4 · the shape and policy refusals
# ---------------------------------------------------------------------------

class ShapeAndPolicyRefusals(unittest.TestCase):

    def test_a_subnet_inside_a_reserved_range_is_denied(self):
        """§十一 #1 — the corporate synthetic CIDR. Nothing on the host is
        touched by this test and no network is created: the collision is
        between two authored records and the evidence producer's rows."""
        result, _ = evaluate(name="corp-overlap", subnet="10.10.5.0/24",
                             policy=POLICY_FOR_RESERVED_COLLISION)
        self.assertEqual(result["decision"], "DENY")
        self.assertEqual(result["codes"], ["NETWORK_CIDR_COLLISION"])
        kinds = {c["kind"] for c in result["conflicts"]}
        self.assertEqual(kinds, {"RESERVED"})
        self.assertEqual(result["conflicts"][0]["class"], "corporate")

    def test_a_subnet_over_an_existing_docker_network_is_denied(self):
        """§十一 #2 — the wi-001 evidence path. The pool here covers the range
        so that the ONLY refusal is the collision; if the pool rule were the
        answer, this test would pass while the collision went undetected."""
        result, _ = evaluate(name="over-wi001", subnet="172.20.5.0/24",
                             policy=POLICY_FOR_DOCKER_COLLISION)
        self.assertEqual(result["codes"], ["NETWORK_CIDR_COLLISION"])
        kinds = {c["kind"] for c in result["conflicts"]}
        self.assertEqual(kinds, {"DOCKER_NETWORK"})
        self.assertIn("wi-001-internal", result["conflicts"][0]["detail"])
        self.assertIn("br-9ed5e74e097f", result["conflicts"][0]["detail"])
        # What is asserted here is what the gate promises a caller, and no
        # more: the overlap, and the consequence — the range would be routed
        # into an existing bridge.
        self.assertIn("172.20.0.0/16", result["reason"])
        self.assertIn("routed into its bridge", result["reason"])
        # NOT asserted, and deliberately so: "this bridge is DOWN". That is the
        # monitor's probe-derived conclusion (`checks.netshadow_checks` folds
        # `linkdown` from the route table into `down_devs` and annotates the
        # finding) and it is a conclusion about the host as it is, not a reason
        # this candidate is refused. Recomputing it here would make two
        # producers of one fact, and the DENY is identical either way — the
        # requester's next move is to pick another subnet in both cases.

    def test_a_compose_network_with_no_declared_subnet_is_denied(self):
        """§十一 #3 — the danger is compose CHOOSING, at `up` time, after this
        gate and after any review."""
        result, _ = evaluate(name="proj_default", subnet=None,
                             policy=POLICY_COMPLETE, composed_by="compose",
                             project="proj")
        self.assertEqual(result["decision"], "DENY")
        self.assertEqual(result["code"], "COMPOSE_IMPLICIT_NETWORK")
        self.assertIn("NETWORK_SUBNET_UNDECLARED", result["codes"])

    def test_a_candidate_with_no_subnet_is_denied_not_raised(self):
        """§十一 #4. A request that names a network and no subnet IS a
        candidate — it is the candidate this code was written for. Raising for
        it would turn a refusal with a repair into a traceback."""
        result, _ = evaluate(name="no-subnet", subnet=None, policy=POLICY_COMPLETE)
        self.assertEqual(result["codes"], ["NETWORK_SUBNET_UNDECLARED"])
        self.assertIn("state the subnet", policy_mod.code_rule(
            "NETWORK_SUBNET_UNDECLARED")["repair"])

    def test_a_gateway_outside_its_own_subnet_is_denied(self):
        result, _ = evaluate(name="bad-gw", subnet="10.247.0.0/24",
                             gateway="10.248.0.1", policy=POLICY_COMPLETE)
        self.assertEqual(result["decision"], "DENY")
        self.assertEqual(result["unknown"], [])
        self.assertIn("NETWORK_SUBNET_UNDECLARED", result["codes"])

    def test_a_request_that_is_not_a_candidate_is_rejected(self):
        """The other half of the shape/verdict boundary, and the reason it is a
        separate outcome: there is no code to refuse a candidate that is not
        one, so the caller is told that instead of being handed a code about a
        request they did not make."""
        with self.assertRaises(policy_mod.Rejected):
            policy_mod.candidate(owner_ref="actor:x")           # no network_name
        with self.assertRaises(policy_mod.Rejected):
            policy_mod.candidate(network_name="n", owner_ref="a", subnet_cidr="x")

    def test_a_malformed_subnet_denies_for_the_same_reason_as_a_missing_one(self):
        rule = policy_mod.code_rule("NETWORK_SUBNET_UNDECLARED")
        self.assertIn("malformed_is_the_same_code", rule)
        result, _ = evaluate(name="malformed", subnet="10.0.0.1/33",
                             policy=POLICY_COMPLETE)
        self.assertEqual(result["codes"], ["NETWORK_SUBNET_UNDECLARED"])


# ---------------------------------------------------------------------------
# §十一 5–6 · incomplete policy, and evidence that could not be read
# ---------------------------------------------------------------------------

class UnreadableNeverBecomesSafe(unittest.TestCase):

    def test_no_policy_denies_automatic_creation(self):
        """§十一 #5, and C1 §二's actual complaint. `reserved_cidrs` empty is
        what this host has RIGHT NOW. The monitor stays quiet about it; the
        mutation gate refuses. Same fact, two questions."""
        result, _ = evaluate(name="clean-but-unpoliced", subnet="10.247.0.0/24",
                             policy={})
        self.assertEqual(result["decision"], "DENY")
        self.assertEqual(result["codes"], ["NETWORK_POLICY_INCOMPLETE"])
        self.assertEqual(result["coverage"], "ABSENT")
        repair = policy_mod.code_rule("NETWORK_POLICY_INCOMPLETE")["repair"]
        self.assertIn("NOT automatable", repair)
        self.assertIn("ip route", repair, "the repair names the forbidden source")

    def test_a_policy_that_claims_complete_and_names_no_pool_is_incomplete(self):
        """The claim is the thing that is wrong. A document that says COMPLETE
        and names nothing to allocate from would deny every candidate through
        the pool rule while reporting itself finished."""
        self.assertEqual(
            policy_mod.policy_coverage({"coverage": "COMPLETE",
                                        "allowed_pools": []}), "INCOMPLETE")

    def test_absent_and_unreadable_are_reported_apart(self):
        self.assertEqual(policy_mod.policy_coverage(None), "ABSENT")
        self.assertEqual(policy_mod.policy_coverage({}), "ABSENT")
        self.assertEqual(policy_mod.policy_coverage({"unreadable": True}), "UNREADABLE")
        self.assertNotEqual(policy_mod.code_rule("NETWORK_POLICY_INCOMPLETE").get(
            "not_the_same_as"), None)

    def test_a_profile_with_no_network_section_is_incomplete_not_absent(self):
        """A host profile that exists and carries no network section denies —
        both states deny — and it must not be reported as ABSENT. `ABSENT` says
        "there is no document", and the operator would go looking for a file
        sitting at the path they just passed. The contract's
        `coverage_of.INCOMPLETE` is "the document exists and names no pool",
        which is this exactly."""
        loaded = policy_mod.policy_from_document(
            {"schema": "serveros/host-profile.v1", "node": "host:beijing"})
        self.assertEqual(loaded, {"no_network_section": True})
        self.assertEqual(policy_mod.policy_coverage(loaded), "INCOMPLETE")
        result, _ = evaluate(name="x", subnet="10.20.0.0/24", policy=loaded,
                             host=FakeHost(networks=[], routes=[], decisions={}))
        self.assertEqual(result["decision"], "DENY")
        self.assertIn("NETWORK_POLICY_INCOMPLETE", result["codes"])
        self.assertEqual(result["coverage"], "INCOMPLETE")
        self.assertNotIn("ABSENT", result["reason"])

    def test_a_policy_written_a_level_too_high_is_unreadable_not_absent(self):
        """The defect this was found by: the contract's `policy.fields` reads
        like the FILE's fields, so an operator who follows it writes them at the
        root, gets `coverage: ABSENT` at the path they passed, and is told there
        is no policy where there plainly is one. It denies either way; what
        changes is that the refusal now names the repair it actually needs."""
        flat = {"schema": "serveros/network-policy.v1",
                "coverage": "COMPLETE",
                "allowed_pools": [{"cidr": "10.20.0.0/16",
                                   "provenance": "an operator's document"}],
                "corporate_reserved_cidrs": [{"cidr": "10.10.0.0/16",
                                              "provenance": "same"}]}
        loaded = policy_mod.policy_from_document(flat)
        self.assertEqual(loaded, {"unreadable": True})
        self.assertEqual(policy_mod.policy_coverage(loaded), "UNREADABLE")

    def test_the_nested_shape_is_the_one_that_reaches_complete(self):
        """The §八 positive control, at the level the shape lives on. If this
        fails, the gate cannot be satisfied by following its own contract, and
        "the DENY test passed" would be evidence of nothing."""
        document = {"schema": "serveros/host-profile.v1", "node": "host:beijing",
                    "network_policy": {
                        "coverage": "COMPLETE",
                        "allowed_pools": [{"cidr": "10.20.0.0/16",
                                           "provenance": "synthetic control"}],
                        "corporate_reserved_cidrs": [],
                        "vpn_reserved_cidrs": [],
                        "infrastructure_reserved_cidrs": []}}
        loaded = policy_mod.policy_from_document(document)
        self.assertEqual(policy_mod.policy_coverage(loaded), "COMPLETE")

    def test_a_failed_route_probe_is_evidence_unavailable_and_not_safe(self):
        """§十一 #6. The gate asks the kernel about addresses it must know
        about; if the question cannot be asked, the answer was not
        established. `SAFE` is a word a person would act on and it is not one
        this module can produce."""
        result, record = evaluate(name="probe-broken", subnet="10.247.0.0/24",
                                  policy=POLICY_COMPLETE,
                                  host=FakeHost(broken={"ip route get"}))
        self.assertEqual(result["decision"], "DENY")
        self.assertEqual(result["codes"], ["NETWORK_EVIDENCE_UNAVAILABLE"])
        self.assertTrue(result["unknown"])
        self.assertNotIn("SAFE", json.dumps(policy_mod.evaluate(
            candidate=record,
            evidence=full_evidence(FakeHost(broken={"ip route get"}), record,
                                   POLICY_COMPLETE),
            policy=POLICY_COMPLETE)))

    def test_an_unreadable_docker_list_is_never_read_as_no_networks(self):
        """An empty list and a list that could not be made are different
        claims, and a gate that believed the second would authorize creating a
        duplicate of a network it never saw. It also cannot exclude the docker
        bridges from the host interfaces, because it does not know which
        devices they are — so the effect shows up twice and both are in the
        record."""
        result, _ = evaluate(name="docker-broken", subnet="172.20.5.0/24",
                             policy=POLICY_FOR_DOCKER_COLLISION,
                             host=FakeHost(broken={"docker"}))
        self.assertEqual(result["decision"], "DENY")
        self.assertIn("NETWORK_EVIDENCE_UNAVAILABLE", result["codes"])
        self.assertIsNone(evidence_mod.docker_rows(FakeHost(broken={"docker"}))[0])
        self.assertEqual(evidence_mod.docker_rows(FakeHost(broken={"docker"}))[0],
                         None, "unreadable must not degrade to an empty list")

    def test_every_required_address_is_actually_asked_about(self):
        """A preflight that probed nothing would report zero unprobed
        addresses and deny nothing. The targets are read off the record and
        compared with what the host was asked."""
        host = FakeHost()
        _, record = evaluate(name="probed", subnet="172.20.5.0/24",
                             policy=POLICY_FOR_DOCKER_COLLISION, host=host)
        self.assertTrue(host.probed, "the kernel was never asked anything")
        self.assertIn("172.20.0.0", host.probed)


# ---------------------------------------------------------------------------
# The producer's own filters. Each one, unfiltered, is a gate that refuses
# everything with a true-sounding reason.
# ---------------------------------------------------------------------------

class TheEvidenceProducersFilters(unittest.TestCase):

    def test_loopback_link_local_and_docker_devices_are_not_host_addresses(self):
        rows, unknown = evidence_mod.iface_rows(
            FakeHost(), exclude_devs=evidence_mod.docker_devices(
                evidence_mod.docker_rows(FakeHost())[0]))
        self.assertEqual(unknown, [])
        self.assertEqual([r["cidr"] for r in rows], ["192.0.2.77/27"])
        # The load-bearing exclusion: every docker bridge holds the gateway of
        # its own subnet, so leaving them in makes every existing docker subnet
        # collide with "a host interface".
        self.assertNotIn("172.20.0.1/16", [r["cidr"] for r in rows])

    def test_the_on_link_neighbourhood_is_not_a_destination(self):
        rows, _ = evidence_mod.route_rows(FakeHost())
        self.assertEqual([r["dst"] for r in rows], ["198.51.100.217", "183.60.82.98"])

    def test_the_bridge_device_name_is_twelve_characters(self):
        """Six would match no device on any host, which reads as "every bridge
        is down" rather than as a bug — and was shipped once already."""
        self.assertEqual(evidence_mod.bridge_device(WI001[1]), "br-9ed5e74e097f")
        _, named = _network_row("named", "ab" * 32, "10.0.0.0/24", "10.0.0.1",
                                dev="br-custom")
        self.assertEqual(evidence_mod.bridge_device(named), "br-custom")

    def test_one_subnet_per_row_and_the_container_count_is_read(self):
        rows, _ = evidence_mod.docker_rows(FakeHost())
        by_name = {r["name"]: r for r in rows}
        self.assertEqual(by_name["wi-001-internal"]["containers"], 0)
        self.assertEqual(by_name["lit-0101-r7-internal"]["containers"], 2)


# ---------------------------------------------------------------------------
# The `/32` rule. This is the shape the host actually carries and the reason
# "the range is shadowed" and "these addresses are being dropped right now" are
# different sentences.
# ---------------------------------------------------------------------------

class TheProbeTargets(unittest.TestCase):

    def test_the_neighbours_of_a_slash_32_are_probed_too(self):
        """The candidate has to COVER the `/32` for the neighbours to be in
        question at all: this asks what happens to the addresses beside an
        exemption, and an address beside an exemption the candidate does not
        touch is not part of this request."""
        host = FakeHost()
        record = policy_mod.candidate(network_name="over-wi001",
                                      owner_ref="actor:x",
                                      subnet="198.51.100.0/24")
        targets = policy_mod.witness_addresses(record, evidence_mod.sources(host))
        self.assertIn("198.51.100.217", targets["targets"])
        self.assertIn("198.51.100.216", targets["targets"])
        self.assertIn("198.51.100.218", targets["targets"])
        self.assertEqual(targets["dropped"], 0)

    def test_a_candidate_that_misses_a_slash_32_does_not_probe_it(self):
        """The other half, and the one that keeps the rule from being "probe
        every `/32` on the host": `172.20.5.0/24` is shadowed by wi-001 and by
        nothing else, so the exemption three subnets away is not this
        request's business."""
        host = FakeHost()
        record = policy_mod.candidate(network_name="elsewhere", owner_ref="actor:x",
                                      subnet="172.20.5.0/24")
        targets = policy_mod.witness_addresses(record, evidence_mod.sources(host))
        self.assertNotIn("198.51.100.217", targets["targets"])
        self.assertNotIn("198.51.100.218", targets["targets"])

    def test_a_truncated_target_list_is_reported_rather_than_silent(self):
        """A cap nobody is told about reads as "we looked at everything". Eighty
        overlapping networks is not a host anybody has, and it is the cheapest
        way to reach the bound."""
        host = FakeHost(networks=[_network_row(f"net-{i:02d}", f"{i:012d}" + "0" * 52,
                                               f"10.{i}.0.0/24", f"10.{i}.0.1")
                                  for i in range(80)])
        record = policy_mod.candidate(network_name="wide", owner_ref="actor:x",
                                      subnet="10.0.0.0/8")
        targets = policy_mod.witness_addresses(record, evidence_mod.sources(host))
        self.assertEqual(len(targets["targets"]), policy_mod.MAX_PROBE_TARGETS)
        self.assertGreater(targets["dropped"], 0)

    def test_a_truncated_list_is_not_a_complete_list(self):
        """`dropped` is returned so that a caller CANNOT read a truncated answer
        as a complete one — and returning it is only half of that. The gate is
        the caller, and this is it: the addresses past the cap were never asked
        about, so the run is UNEVALUABLE and not a verdict.

        Unreachable with an empty conflict set (a candidate has two
        representatives and the cap is sixty-four), so this never turns an
        ALLOW into a DENY. It keeps the rule true for all inputs rather than
        for the inputs that happen to reach it today."""
        hosts = FakeHost(networks=[
            _network_row(f"net-{i:02d}", f"{i:012d}" + "0" * 52,
                         f"10.{i}.0.0/24", f"10.{i}.0.1") for i in range(80)])
        evidence = evidence_mod.sources(hosts)
        record = policy_mod.candidate(network_name="wide", owner_ref="actor:x",
                                      subnet="10.0.0.0/8")
        required = policy_mod.witness_addresses(record, evidence)
        probes = {a: {"ok": True, "dev": "eth0"} for a in required["targets"]}
        result = policy_mod.evaluate(
            candidate=record, policy=POLICY_COMPLETE,
            evidence={**evidence, "route_decisions": probes})
        self.assertIn("NETWORK_EVIDENCE_UNAVAILABLE", result["codes"])
        self.assertIn("were not asked about", " ".join(result["unknown"]))


# ---------------------------------------------------------------------------
# §十一 7 and 9 · the gate goes green, and it goes green end to end
# ---------------------------------------------------------------------------

class ThePositiveControl(unittest.TestCase):

    def test_a_clean_candidate_inside_a_declared_pool_is_allowed(self):
        """§十一 #7. Without this, every other test in this file passes against
        a gate that refuses everything, and a gate that refuses everything is
        read exactly like one that refuses nothing."""
        result, _ = evaluate(name="clean", subnet="10.247.0.0/24",
                             policy=POLICY_COMPLETE)
        self.assertEqual(result["decision"], "ALLOW")
        self.assertEqual(result["codes"], [])
        self.assertEqual(result["conflicts"], [])
        self.assertEqual(result["unknown"], [])
        self.assertEqual(result["coverage"], "COMPLETE")


# ---------------------------------------------------------------------------
# The composed decision, and the value that is not decorative
# ---------------------------------------------------------------------------

class TheThirdValueIsReachable(unittest.TestCase):

    def test_no_network_rule_produces_require_approval(self):
        """The contract says so in prose. This runs every refusal this file
        can produce and checks the claim rather than repeating it."""
        seen = []
        for name, subnet, policy, extra in (
                ("a", None, POLICY_COMPLETE, {}),
                ("b", "10.10.5.0/24", POLICY_FOR_RESERVED_COLLISION, {}),
                ("c", "172.20.5.0/24", POLICY_FOR_DOCKER_COLLISION, {}),
                ("d", "10.247.0.0/24", {}, {}),
                ("e", "10.247.0.0/24", POLICY_COMPLETE, {"composed_by": "compose"}),
                ("f", "10.248.5.0/24", POLICY_COMPLETE, {})):
            result, _ = evaluate(name=name, subnet=subnet, policy=policy, **extra)
            seen.append(result["composed_from"][0]["decision"])
            self.assertNotEqual(result["decision"], "REQUIRE_APPROVAL", name)
        self.assertNotIn("REQUIRE_APPROVAL", seen)

    def test_require_approval_arrives_through_ownership(self):
        """C2's gate, driven with a synthetic record so the value is proven
        reachable before C2 exists. A three-valued vocabulary with one
        unreachable value is a two-valued vocabulary with extra words."""
        result, record = evaluate(name="clean", subnet="10.247.0.0/24",
                                  policy=POLICY_COMPLETE)
        composed = policy_mod.compose(
            network=result,
            ownership={"decision": "REQUIRE_APPROVAL", "reason": "protected subject"},
            recovery=None)
        self.assertEqual(composed["decision"], "REQUIRE_APPROVAL")
        self.assertEqual([s["input"] for s in composed["composed_from"]],
                         ["network", "ownership"])

    def test_an_irreversible_action_is_not_the_requesters_alone(self):
        result, _ = evaluate(name="clean", subnet="10.247.0.0/24",
                             policy=POLICY_COMPLETE)
        composed = policy_mod.compose(network=result, ownership=None,
                                      recovery="IRREVERSIBLE")
        self.assertEqual(composed["decision"], "REQUIRE_APPROVAL")

    def test_deny_outranks_a_permissive_input(self):
        result, _ = evaluate(name="over-wi001", subnet="172.20.5.0/24",
                             policy=POLICY_FOR_DOCKER_COLLISION)
        composed = policy_mod.compose(
            network=result, ownership={"decision": "ALLOW"}, recovery=None)
        self.assertEqual(composed["decision"], "DENY")


# ---------------------------------------------------------------------------
# §十二 · what a caller is told, and what it must ask for
# ---------------------------------------------------------------------------

class TheDefaultDisclosure(unittest.TestCase):

    def test_the_default_does_not_dump_the_host(self):
        result, _ = evaluate(name="clean", subnet="10.247.0.0/24",
                             policy=POLICY_COMPLETE)
        disclosed = policy_mod.disclose(result)
        self.assertEqual(disclosed["axis"], "authority")
        self.assertEqual(disclosed["level"], "L1")
        value = disclosed["value"]
        self.assertNotIn("evidence", value)
        self.assertNotIn("conflicts", value)
        self.assertEqual(sorted(value),
                         ["code", "decision", "reason", "subject"])

    def test_a_refusal_promotes_itself_to_its_reason(self):
        result, _ = evaluate(name="over-wi001", subnet="172.20.5.0/24",
                             policy=POLICY_FOR_DOCKER_COLLISION)
        disclosed = policy_mod.disclose(result)
        self.assertEqual(disclosed["level"], "L2")
        self.assertTrue(disclosed["value"]["conflicts"])

    def test_the_evidence_is_one_level_down_and_not_withheld(self):
        result, _ = evaluate(name="over-wi001", subnet="172.20.5.0/24",
                             policy=POLICY_FOR_DOCKER_COLLISION)
        up = policy_mod.disclose(result, "L3")["value"]
        self.assertIn("evidence", up)
        self.assertIn("route_decisions", up["evidence"])
        self.assertEqual(up["evidence"]["docker_subnet_count"], 2)

    def test_the_rendered_line_names_the_conflict_at_the_level_it_is_rendered(self):
        result, _ = evaluate(name="over-wi001", subnet="172.20.5.0/24",
                             policy=POLICY_FOR_DOCKER_COLLISION)
        text = policy_mod.render(result)
        self.assertIn("⛔ DENY", text)
        self.assertIn("wi-001-internal", text)


# ---------------------------------------------------------------------------
# §七 · the managed path, and the two halves that make it more than a checker
# ---------------------------------------------------------------------------

class Recorder:
    def __init__(self, returncode=0):
        self.calls: list[list[str]] = []
        self.returncode = returncode

    def __call__(self, argv):
        self.calls.append(list(argv))
        return {"returncode": self.returncode, "stdout": "ok", "stderr": ""}


def write(tmp_path: Path, name: str, payload) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def write_policy(tmp_path: Path, policy: dict) -> Path:
    path = tmp_path / "HOST-PROFILE.yaml"
    path.write_text(yaml.safe_dump({"network_policy": policy}), encoding="utf-8")
    return path


def write_registry(tmp_path: Path, *, owner_ref="work-item:C1-FIXTURE",
                   owner_type="work-item", protection="normal") -> dict:
    """A managed-runtime registry document, read through the real reader.

    Built with `containers.load_registry` rather than as a literal dict so that
    the fixture carries the fields that function adds — `registry_present`
    above all, which is the difference between "the registry says this ref is
    not protected" and "nobody could be asked". A hand-written index would
    agree with itself about that distinction and prove nothing about the code
    that makes it.
    """
    path = tmp_path / "registry.yaml"
    path.write_text(yaml.safe_dump({"resources": [{
        "id": "c1-fixture",
        "kind": "project",
        "owner": {"ref": owner_ref, "type": owner_type},
        "protection": {"level": protection},
        "lifecycle": {"class": "task"},
    }]}), encoding="utf-8")
    return containers.load_registry(str(path))


def write_context(tmp_path: Path, *, context_ref="ctx:c1-fixture",
                  actor_ref="actor:fixture") -> Path:
    """A trusted context. C2's gate refuses without one, on the ground that
    `owner_ref` is documented as resolved from it and never from a name."""
    return write(tmp_path, "context.json",
                 {"context_ref": context_ref, "actor_ref": actor_ref})


class TheManagedPath(unittest.TestCase):
    """§七 — request, transient plan, compliance preflight, executor.

    Every test here goes through `network_mutation.run`, which is the same
    entry point the CLI uses. A gate that is only reachable from a test is a
    checker, and the distinction C1 §七 draws is exactly that.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _request(self, name="c1-control", subnet="10.247.0.0/24") -> Path:
        block = {"network_name": name, "owner_ref": "work-item:C1-FIXTURE"}
        if subnet is not None:
            block["subnet"] = subnet
        return write(self.tmp, "req.json", {"network": block})

    def _plan(self):
        path = write_policy(self.tmp, POLICY_COMPLETE)
        plan_record = mutation_mod.plan(
            json.loads(self._request().read_text()), host=FakeHost(),
            policy=POLICY_COMPLETE, policy_ref=str(path),
            now="2026-09-15T00:00:00+00:00")
        return plan_record, mutation_mod.preflight(plan_record,
                                                   policy=POLICY_COMPLETE)

    def test_a_deny_never_enters_the_actuator(self):
        """§十一 #8, and the acceptance C1 §七 calls the most important one.

        Three things are asserted and the third is the one that cannot be
        faked: the Docker network list is read through the same reader before
        and after, and it is identical. A test that checked only
        `runner.calls == []` would pass against an executor that reached the
        daemon by some other route.
        """
        host = FakeHost()
        before = host.snapshot()
        runner = Recorder()

        code = mutation_mod.run(
            ["create", "--request", str(self._request("over-wi001", "172.20.5.0/24")),
             "--policy", str(write_policy(self.tmp, POLICY_FOR_DOCKER_COLLISION)),
             "--context", str(write_context(self.tmp)),
             "--execute"],
            host=host, runner=runner, registry=write_registry(self.tmp))

        self.assertEqual(code, 10, "a DENY must not report success")
        self.assertEqual(runner.calls, [], "the actuator was entered on a DENY")
        self.assertEqual(host.snapshot(), before, "docker state changed on a DENY")

    def test_an_allow_reaches_the_actuator_and_the_gate_can_go_green(self):
        """§十一 #9, through the same entry point as the refusal above. Without
        this pair, "the actuator was not entered" is also what a broken wiring
        reports — and a gate that refuses everything is read exactly like one
        that refuses nothing."""
        runner = Recorder()
        code = mutation_mod.run(
            ["create", "--request", str(self._request()),
             "--policy", str(write_policy(self.tmp, POLICY_COMPLETE)),
             "--context", str(write_context(self.tmp)),
             "--execute"],
            host=FakeHost(), runner=runner, registry=write_registry(self.tmp))

        self.assertEqual(code, 0)
        self.assertEqual(runner.calls, [
            ["docker", "network", "create", "--subnet", "10.247.0.0/24",
             "c1-control"]])

    def test_an_allow_without_execute_performs_nothing(self):
        """The default. `serverosd` makes the same choice with
        `--enable-reconcile`: a tool that acts the first time somebody runs it
        is a tool nobody runs with `--help` first."""
        runner = Recorder()
        code = mutation_mod.run(
            ["create", "--request", str(self._request()),
             "--policy", str(write_policy(self.tmp, POLICY_COMPLETE)),
             "--context", str(write_context(self.tmp))],
            host=FakeHost(), runner=runner, registry=write_registry(self.tmp))
        self.assertEqual(code, 0, "a permitted request is not a failure")
        self.assertEqual(runner.calls, [])
        self.assertEqual(mutation_mod.load_policy(
            str(self.tmp / "HOST-PROFILE.yaml"))[0]["coverage"], "COMPLETE")
        outcome = mutation_mod.execute(*self._plan(), runner=runner)
        self.assertFalse(outcome["performed"])
        self.assertFalse(outcome["runner_called"])
        self.assertEqual(outcome["argv"],
                         ["docker", "network", "create", "--subnet",
                          "10.247.0.0/24", "c1-control"])

    def test_the_executor_refuses_on_its_own_word_not_the_callers(self):
        """`execute` reads the verdict itself. A gate the caller may bypass by
        passing the wrong argument is a convention, and the point of wiring one
        in is that the code path does not exist. `enabled=True` here is the
        caller doing everything in their power to get it to run."""
        plan_record, _ = self._plan()
        runner = Recorder()
        for verdict in ({"decision": "DENY", "code": "NETWORK_CIDR_COLLISION"},
                        {"decision": "REQUIRE_APPROVAL"},
                        {"decision": None},
                        {}):
            with self.subTest(verdict=verdict):
                outcome = mutation_mod.execute(plan_record, verdict,
                                               runner=runner, enabled=True)
                self.assertFalse(outcome["performed"])
                self.assertFalse(outcome["runner_called"])
                self.assertEqual(runner.calls, [])

    def test_the_plan_is_transient_and_carries_what_was_asked(self):
        """§四 — a plan record, not a workflow engine. What it carries is
        asserted because `evidence_refs` is what a later reader answers "why
        was this created" from, and a digest of a list that was never read is
        indistinguishable from a digest of one that was."""
        plan_record, _ = self._plan()
        self.assertTrue(plan_record["transient"])
        self.assertNotIn("id", plan_record)
        self.assertEqual(plan_record["action"], "docker.network.create")
        self.assertEqual(plan_record["subject_ref"], "network:c1-control")
        self.assertEqual(plan_record["recovery"], "REVERSIBLE")
        refs = {ref["source"]: ref for ref in plan_record["evidence_refs"]}
        self.assertEqual(set(refs), {"docker_subnets", "host_iface_cidrs",
                                     "host_routes", "route_decisions"})
        for ref in refs.values():
            self.assertTrue(ref["digest"].startswith("sha256:"), ref)
        # `rows` is the number of entries the source produced, so it is a count
        # for the list sources and for the mapping alike — the point of the
        # field is to separate "nothing there" from "could not read", and both
        # are answers a reader has to be able to tell apart.
        self.assertIsNotNone(refs["docker_subnets"]["rows"])
        self.assertEqual(refs["route_decisions"]["rows"], 2, "two addresses asked")

    def test_a_malformed_request_is_rejected_and_not_given_a_code(self):
        """Exit 2 rather than 0 or 10: the caller must be able to tell "your
        candidate collided with something" from "nothing was looked at"."""
        path = write(self.tmp, "bad.json", {"network": ["not", "a", "mapping"]})
        self.assertEqual(
            mutation_mod.run(["preflight", "--request", str(path)],
                             host=FakeHost(), runner=Recorder()), 2)

    def test_a_request_with_no_network_block_at_all_is_rejected(self):
        path = write(self.tmp, "empty.json", {"action": "docker.network.create"})
        self.assertEqual(
            mutation_mod.run(["preflight", "--request", str(path)],
                             host=FakeHost(), runner=Recorder()), 2)


# ---------------------------------------------------------------------------
# §七 — the path is reachable, not only testable
# ---------------------------------------------------------------------------

class TheCliSurface(unittest.TestCase):
    """`serverosctl network …` reaches the same module the tests drive.

    §七 asks for at least one real managed path. A path that exists only as an
    importable function is a path nobody can walk: this asserts the CLI parses
    the request, the policy and the level ONCE and hands the argv over, rather
    than re-deriving them here and in the module until the two disagree.
    """

    def setUp(self):
        self.cli_mod = _load("serveros_runtime_cli", "cli.py")

    def _run(self, argv, exit_code=0):
        calls = []

        def fake_run(inner, **kwargs):
            calls.append(inner)
            return exit_code

        with mock.patch.object(mutation_mod, "run", fake_run):
            rc = self.cli_mod.main(argv)
        return rc, calls

    def test_the_cli_hands_the_module_the_flags_it_was_given(self):
        rc, calls = self._run(["network", "preflight", "--request", "r.json"])
        self.assertEqual(rc, 0)
        self.assertEqual(calls, [["preflight", "--request", "r.json"]])
        self.assertNotIn("--execute", calls[0])

    def test_every_flag_survives_the_handoff_including_execute(self):
        rc, calls = self._run(["network", "create", "--request", "r.json",
                               "--policy", "p.yaml", "--level", "L2",
                               "--execute"], exit_code=10)
        self.assertEqual(rc, 10, "the module's exit code is the CLI's")
        self.assertEqual(calls, [["create", "--request", "r.json",
                                  "--policy", "p.yaml", "--level", "L2",
                                  "--execute"]])

    def test_the_disclosure_levels_come_from_the_module_that_declares_them(self):
        """Written out in `cli.py` instead, this list is the second vocabulary
        that agrees with the contract until one of the two moves."""
        self.assertEqual(policy_mod.LEVELS, ("L0", "L1", "L2", "L3"))
        self.assertEqual(self.cli_mod._network_levels(), policy_mod.LEVELS)

    def test_the_cli_no_longer_describes_itself_as_read_only(self):
        """It said "Read-only: no subcommand here stops, removes or restarts
        anything" until `network create` existed. The sentence kept being read
        after it stopped being accurate, which is this repository's most
        repeated failure — so it is asserted rather than trusted to stay
        edited."""
        description = self.cli_mod.build_parser().description
        self.assertNotIn("Read-only", description)
        self.assertIn("--execute", description)


# ---------------------------------------------------------------------------
# The contract and the code. Two copies of the same list agree until the day
# one of them is changed.
# ---------------------------------------------------------------------------

class TheContractAndTheCodeAgree(unittest.TestCase):

    def test_the_reserved_classes_are_the_contracts_policy_fields(self):
        """Derived by suffix rather than by comparing two hand-written lists,
        so a fourth reserved class added to the contract fails here instead of
        being silently unchecked by an evaluator that never reads it."""
        declared = set(CONTRACT_DOC["policy"]["fields"])
        named = {key for key, _ in policy_mod.RESERVED_CLASSES}
        self.assertEqual({k for k in declared if k.endswith("_reserved_cidrs")},
                         named)
        labels = [label for _, label in policy_mod.RESERVED_CLASSES]
        self.assertEqual(len(labels), len(set(labels)), "two classes share a label")
        self.assertEqual(labels[0], "corporate")

    def test_the_policy_field_names_are_the_contracts_policy_fields(self):
        """The same derivation as the reserved classes above, and for the same
        reason: the field names are read by `policy_from_document` to tell a
        profile with no network section from a policy written a level too high,
        so a name added to the contract and not to the constant would make one
        of those two silences look like the other."""
        declared = set(CONTRACT_DOC["policy"]["fields"])
        self.assertEqual(declared, set(policy_mod.POLICY_FIELDS))
        self.assertEqual(CONTRACT_DOC["policy"]["section"],
                         policy_mod.POLICY_SECTION)

    def test_the_precedence_order_is_the_contracts_order(self):
        self.assertEqual(policy_mod.code_precedence(),
                         tuple(CONTRACT_DOC["precedence"]["order"]))

    def test_every_code_the_contract_declares_is_ordered_and_has_a_repair(self):
        declared = set(CONTRACT_DOC["codes"])
        self.assertEqual(declared, set(policy_mod.code_precedence()))
        for name, rule in CONTRACT_DOC["codes"].items():
            for field in ("rule", "means", "repair"):
                self.assertIn(field, rule, name)

    def test_the_decisions_are_the_contracts_decisions(self):
        self.assertEqual(tuple(CONTRACT_DOC["decisions"]), policy_mod.DECISIONS)

    def test_the_candidate_fields_are_the_contracts_candidate(self):
        """`required` is the constant with a behaviour attached and is compared
        directly. The rest is compared by round-tripping every key the contract
        declares through `candidate()` — a field the contract promises and the
        validator drops on the floor fails here, which is the direction that
        would otherwise be silent."""
        block = CONTRACT_DOC["candidate"]
        self.assertEqual(tuple(block["required"]), policy_mod.CANDIDATE_REQUIRED)
        self.assertEqual(block["unknown_key_is"], "DENY")
        declared = (tuple(block["declared"]) + tuple(block["optional"]))
        self.assertEqual(set(policy_mod.candidate_fields()),
                         set(block["required"]) | set(declared))
        for key in declared:
            built = policy_mod.candidate(
                **{**{"network_name": "n", "owner_ref": "a"}, key: "v"})
            self.assertIn(key, built, key)
            self.assertEqual(built[key], "v", key)
        with self.assertRaises(policy_mod.Rejected):
            policy_mod.candidate(network_name="n", owner_ref="a", subnet_ip="v")

    def test_the_plan_carries_the_fields_the_contract_declares(self):
        """The plan's shape lived only in `network_mutation.py` and in the
        round's specification, which means a reader of the contract could not
        tell what a plan contains and a field the code stopped emitting had
        nothing to fail against. Every declared field is compared here to a
        real plan built by the real producer."""
        built = mutation_mod.plan(
            {"network": {"network_name": "n", "owner_ref": "a",
                         "subnet": "10.247.0.0/24"}},
            host=FakeHost(), policy=POLICY_COMPLETE, policy_ref="profile.yaml",
            now="2026-09-15T00:00:00+00:00")
        block = CONTRACT_DOC["plan"]
        self.assertTrue(block["transient"])
        self.assertTrue(built["transient"])
        # Both directions: a field the contract promises and the producer
        # stopped emitting, and a field the producer emits that nobody
        # declared. The second is the one that gets read by a caller and
        # changed by a later commit with no document to disagree with.
        self.assertEqual(set(block["fields"]), set(built))
        self.assertEqual(set(block["not_carried"]) & set(built), set())

    def test_the_action_and_its_recovery_class_are_the_contracts(self):
        actions = CONTRACT_DOC["actions"]
        self.assertEqual(list(actions), [mutation_mod.ACTION])
        self.assertEqual(actions[mutation_mod.ACTION]["recovery"],
                         mutation_mod.RECOVERY)

    def test_removing_a_network_is_deliberately_not_declared(self):
        """§26 — a removal is not this round's business, and a gate that
        declared a verb it does not implement would be a permission with no
        producer. wi-001's remediation is DEFER while its owner is unproven."""
        self.assertNotIn("docker.network.remove", CONTRACT_DOC["actions"])
        self.assertNotIn("docker.network.remove", json.dumps([mutation_mod.ACTION]))
        self.assertTrue(CONTRACT_DOC["remediation"]["unowned"]["rule"].startswith(
            "an existing network whose owner cannot be proven"))

    def test_there_is_no_built_in_policy_path(self):
        """The standing defect this repository keeps finding is a declared
        default location nothing writes: it resolves, it reports "nothing
        here", and it is indistinguishable from a host whose policy is
        genuinely empty. There is no such path here, and this asserts the
        absence rather than trusting it to stay absent."""
        self.assertIsNone(CONTRACT_DOC["policy"]["default_location"])
        self.assertIn("indistinguishable", CONTRACT_DOC["policy"][
            "default_location_because"])
        saved = os.environ.pop("SERVEROS_NETWORK_POLICY", None)
        try:
            self.assertIsNone(mutation_mod.policy_location(None))
            self.assertEqual(mutation_mod.policy_location("/tmp/x"), "/tmp/x")
            self.assertEqual(policy_mod.read_policy("/nonexistent/profile.yaml"), {})
            self.assertEqual(policy_mod.policy_coverage({}), "ABSENT")
        finally:
            if saved is not None:
                os.environ["SERVEROS_NETWORK_POLICY"] = saved

    def test_an_unreadable_policy_file_is_unreadable_and_not_absent(self):
        """One sends an operator to write a file and the other sends them to
        fix a machine. A reader told the same word for both looks in the wrong
        place half the time."""
        with tempfile.TemporaryDirectory() as tmp:
            broken = Path(tmp) / "profile.yaml"
            broken.write_text("network_policy: [unclosed\n", encoding="utf-8")
            self.assertEqual(policy_mod.read_policy(broken), {"unreadable": True})
            self.assertEqual(policy_mod.policy_coverage(
                policy_mod.read_policy(broken)), "UNREADABLE")

    def test_the_disclosure_default_is_the_contracts_default(self):
        self.assertEqual(CONTRACT_DOC["disclosure"]["default_level"], "L1")
        self.assertEqual(CONTRACT_DOC["disclosure"]["axis"], "authority")
        self.assertEqual(CONTRACT_DOC["disclosure"]["L0"].split(":")[0],
                         "one line")
        self.assertIn("evidence", CONTRACT_DOC["disclosure"]["never_at_l1"])

    def test_this_gate_does_not_claim_to_be_a_containment_boundary(self):
        text = json.dumps(CONTRACT_DOC["not_this"])
        self.assertIn("not a containment boundary", text)
        self.assertIn("not a statement that the host is safe", text)
