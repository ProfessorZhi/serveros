#!/usr/bin/env python3
"""Negative controls for the RC tree — the refusals the V1 set depends on.

WHY THIS FILE EXISTS
--------------------
#430, #431, #432 and #423 each ship their own tests. Those tests prove their
module works. What none of them can show is the property the *combination* has to
hold: that the guards are still **fail-closed once the pieces sit together**, and
that the tree is not quietly claiming something about a device it has not seen.

So every check here is written as a pair: a valid document must be accepted, and
a deliberately broken one must be refused. A check that only ever sees the good
input cannot tell "the guard works" from "the guard is unreachable".

Covered:

* **#430** — the `serverosd` consumer matrix: every program that can mutate names
  its gate sources, and the tree does not claim the two programs contend on one
  file by construction.
* **#431** — D7: an L2 artifact whose closure leaves `fleet_mcp` must be refused
  rather than shipped, and if it is allowed through explicitly it must declare
  itself non-standalone.
* **#432** — D6: nothing is retirable yet, and the local admission paths are
  retained with a named gap rather than deleted.
* **#423** — two different runs on one device must not share the transport key
  after truncation.
* **#420** — the contract that guards the announcer is itself collected by the
  sharded suite.

No network, no build, no credential, no service. Everything here reads the tree.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

CONSUMERS = ROOT / "serverosd" / "consumers.v1.json"
SINGLE_WRITER_DOC = ROOT / "serverosd" / "contract" / "single-writer.v1.md"
L2_RELEASE = ROOT / "integrations" / "fleet-v1-l2-release" / "l2_release.py"
RETIREMENT = ROOT / "fleet" / "old-control-retirement.yaml"
NOTIFY = ROOT / "integrations" / "fleet-v1-milestone-notify" / "milestone_notify.py"
SOURCES = ROOT / "integrations" / "fleet-v1-milestone-sources" / "milestone_sources.py"
HERMETIC = ROOT / "scripts" / "hermetic_tests.py"
BASELINE = ROOT / "tests" / "hermetic-known-failures.txt"
CONTRACT_TEST = ROOT / "tests" / "test_fleet_milestone_architecture_contract.py"

CONSUMERS_SCHEMA = "serveros/native-core-consumers.v1"
RETIREMENT_SCHEMA = "serveros/fleet-old-control-retirement.v1"
HARNESS_SHARDS = ("1/4", "2/4", "3/4", "4/4")

#: The 50-character budget `runtime/message_transport.py` gives the CLI flag.
TRANSPORT_KEY_LIMIT = 50


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------- #
# #430 — one writer per unit
# --------------------------------------------------------------------------- #

def problems_with_consumers(doc: dict) -> list[str]:
    """Every way the consumer matrix stops being a checkable claim."""
    problems: list[str] = []
    if doc.get("schema") != CONSUMERS_SCHEMA:
        problems.append(f"schema must be {CONSUMERS_SCHEMA!r}")

    writers = doc.get("writers")
    if not isinstance(writers, list) or not writers:
        return problems + ["writers must be a non-empty list"]

    for index, writer in enumerate(writers):
        if not isinstance(writer, dict):
            problems.append(f"writers[{index}] is not an object")
            continue
        name = writer.get("id") or f"writers[{index}]"
        # A program that can change the host must name what gates it. This is the
        # hole the matrix exists to close: "a third program that can mutate and
        # is gated by nothing".
        if writer.get("mutates") and not writer.get("gate_sources"):
            problems.append(f"{name} mutates but names no gate_sources")

    # The claim "they contend on one file by construction" is only true if both
    # programs default the same lease path. If either leaves it to the operator,
    # saying `shared_by_declaration: true` would be an unearned guarantee.
    single = doc.get("single_writer")
    if isinstance(single, dict):
        leaves_default_to_operator = any(
            not (writer.get("lease") or {}).get("default_path")
            for writer in writers if isinstance(writer, dict))
        if single.get("shared_by_declaration") and leaves_default_to_operator:
            problems.append(
                "shared_by_declaration is true while a writer has no default "
                "lease path, so the programs do not contend by construction")
        if single.get("shared_by_declaration") is False and not single.get("why_not"):
            problems.append("a false shared_by_declaration needs a why_not")
    else:
        problems.append("single_writer must be an object")

    if not doc.get("rename_complete_when"):
        problems.append("rename_complete_when must not be empty")
    return problems


class ServerosdHasOneWriter(unittest.TestCase):
    def setUp(self) -> None:
        self.doc = json.loads(CONSUMERS.read_text(encoding="utf-8"))

    def test_the_committed_matrix_satisfies_the_contract(self) -> None:
        self.assertEqual(problems_with_consumers(self.doc), [])

    def test_a_writer_that_mutates_without_a_gate_is_refused(self) -> None:
        doc = json.loads(json.dumps(self.doc))
        doc["writers"][0]["gate_sources"] = []
        self.assertTrue(any("names no gate_sources" in p
                            for p in problems_with_consumers(doc)),
                        "an ungated mutator is the exact thing the matrix exists "
                        "to make visible")

    def test_claiming_shared_contention_without_a_default_lease_is_refused(self) -> None:
        doc = json.loads(json.dumps(self.doc))
        doc["single_writer"]["shared_by_declaration"] = True
        doc["single_writer"].pop("why_not", None)
        doc["writers"][0].setdefault("lease", {})["default_path"] = None
        self.assertTrue(any("do not contend by construction" in p
                            for p in problems_with_consumers(doc)),
                        "'they share one file' must not be asserted when one "
                        "program takes whatever path the operator passed")

    def test_the_tree_does_not_claim_a_device_is_installed(self) -> None:
        """A tree claim is not a device fact, and this document says so."""
        text = SINGLE_WRITER_DOC.read_text(encoding="utf-8")
        self.assertIn("NOT_INSTALLED", text,
                      "the contract describes what is installed; without this it "
                      "reads as a claim that a device runs the C++ Core")
        self.assertIn("what is actually installed", text.lower())


# --------------------------------------------------------------------------- #
# #431 — D7: an L2 artifact that is not standalone must not ship silently
# --------------------------------------------------------------------------- #

class L2ArtifactsFailClosed(unittest.TestCase):
    def setUp(self) -> None:
        if not L2_RELEASE.is_file():
            self.skipTest("the L2 release module is not in this tree")
        self.l2 = _load("rc_l2_release", L2_RELEASE)

    def test_neither_l2_artifact_is_independently_installable_here(self) -> None:
        """The measured state, not an assumption.

        Both closures leave `fleet_mcp` and need `fleet` through
        `fleet_mcp.registry`. Until that edge is cut, an L2 payload installed on a
        machine without this checkout would raise at import — so the module
        refuses instead of producing it.
        """
        for kind in self.l2.L2_KINDS:
            with self.subTest(kind=kind):
                with self.assertRaises(self.l2.Refused) as caught:
                    self.l2.manifest_for(kind, "1.0.0", root=ROOT)
                message = str(caught.exception)
                self.assertIn("does not carry", message)
                self.assertIn("non-standalone", message,
                              "the refusal must name the way out, or it reads as "
                              "a build error rather than a packaging decision")

    def test_an_explicitly_allowed_artifact_declares_itself_non_standalone(self) -> None:
        for kind in self.l2.L2_KINDS:
            with self.subTest(kind=kind):
                manifest, members = self.l2.manifest_for(
                    kind, "1.0.0", root=ROOT, allow_external_in_repo=("fleet",))
                self.assertTrue(members)
                self.assertIn("fleet", manifest["l2"]["external_in_repo"])
                self.assertFalse(manifest["l2"]["standalone"],
                                 "an artifact that needs an in-repo package it "
                                 "does not carry is not standalone")

    def test_a_declared_dependency_also_blocks_standalone(self) -> None:
        manifest, _ = self.l2.manifest_for(
            "l2-hub", "1.0.0", root=ROOT,
            allow_external_in_repo=("fleet",), depends_on=("l2-node",))
        self.assertFalse(manifest["l2"]["standalone"])
        self.assertEqual(manifest["l2"]["depends_on"], ["l2-node"])


# --------------------------------------------------------------------------- #
# #432 — D6: nothing is deleted, and no path is kept by silence
# --------------------------------------------------------------------------- #

def problems_with_retirement(doc: dict) -> list[str]:
    problems: list[str] = []
    if doc.get("schema") != RETIREMENT_SCHEMA:
        problems.append(f"schema must be {RETIREMENT_SCHEMA!r}")
    if doc.get("decision") != "BLOCKED_KEEP_AND_GUARD":
        problems.append("decision must be BLOCKED_KEEP_AND_GUARD while D6 is open")

    entries = doc.get("entries")
    if not isinstance(entries, list) or not entries:
        return problems + ["entries must be a non-empty list"]

    for entry in entries:
        if not isinstance(entry, dict):
            problems.append("an entry is not an object")
            continue
        path = entry.get("path", "<unnamed>")
        disposition = str(entry.get("disposition") or "")
        replacement = entry.get("replacement") or {}
        if disposition.startswith("RETAIN"):
            # Retained must never be silent: a named gap is what makes it a
            # decision rather than an omission.
            if replacement.get("kind") != "none":
                problems.append(f"{path} is RETAIN_* but claims a replacement")
            if not entry.get("why_not_retired"):
                problems.append(f"{path} is retained with no stated reason")
        elif disposition == "RETIRED":
            if replacement.get("kind") == "none":
                problems.append(f"{path} is RETIRED with no replacement")
            if not replacement.get("fleet_mcp_tools"):
                problems.append(f"{path} is RETIRED without a served replacement tool")
        else:
            problems.append(f"{path} has an unknown disposition {disposition!r}")
    return problems


class OldControlIsKeptAndGuarded(unittest.TestCase):
    def setUp(self) -> None:
        try:
            import yaml  # noqa: F401
        except ImportError:
            self.skipTest("PyYAML is not installed on this host")
        self.doc = self._read()

    def _read(self) -> dict:
        import yaml
        return yaml.safe_load(RETIREMENT.read_text(encoding="utf-8"))

    def test_the_committed_decision_retires_nothing(self) -> None:
        self.assertEqual(problems_with_retirement(self.doc), [])
        retired = [e["path"] for e in self.doc["entries"]
                   if e.get("disposition") == "RETIRED"]
        self.assertEqual(retired, [], "D6 is blocked; nothing may be retired yet")

    def test_local_admission_is_retained_not_deleted(self) -> None:
        """The specific thing D6 would have deleted and could not justify."""
        by_path = {e["path"]: e for e in self.doc["entries"]}
        self.assertIn("mcp/agent_mcp.py", by_path,
                      "the local admission surface exists and is not retirable")
        entry = by_path["mcp/agent_mcp.py"]
        self.assertTrue(entry["disposition"].startswith("RETAIN"))
        self.assertEqual(entry["replacement"]["kind"], "none")
        self.assertIn("agent.", entry["why_not_retired"])

    def test_a_retained_path_with_no_reason_is_refused(self) -> None:
        doc = json.loads(json.dumps(self.doc))
        doc["entries"][0].pop("why_not_retired", None)
        self.assertTrue(any("no stated reason" in p
                            for p in problems_with_retirement(doc)),
                        "keeping a path by silence is what the guard forbids")

    def test_a_retirement_without_a_served_replacement_is_refused(self) -> None:
        doc = json.loads(json.dumps(self.doc))
        doc["entries"][0]["disposition"] = "RETIRED"
        doc["entries"][0].setdefault("replacement", {})["fleet_mcp_tools"] = []
        self.assertTrue(any("without a served replacement tool" in p
                            for p in problems_with_retirement(doc)))

    def test_a_decision_without_the_blocked_marker_is_refused(self) -> None:
        doc = json.loads(json.dumps(self.doc))
        doc["decision"] = "CONVERGED"
        self.assertTrue(any("BLOCKED_KEEP_AND_GUARD" in p
                            for p in problems_with_retirement(doc)))


# --------------------------------------------------------------------------- #
# #423 — two runs must not share a transport key
# --------------------------------------------------------------------------- #

class TwoRunsDoNotShareATransportKey(unittest.TestCase):
    """The regression that made the ledger record a receipt for a lost message.

    The transport passes `idempotency_key[:50]` to the CLI. The ledger identity
    is far longer than that and leads with the device, so two runs on one device
    used to become one key on the wire: the provider deduped the second milestone
    away while the ledger recorded the returned id as DELIVERED.
    """

    def setUp(self) -> None:
        if not NOTIFY.is_file() or not SOURCES.is_file():
            self.skipTest("the milestone modules are not in this tree")
        self.notify = _load("rc_key_notify", NOTIFY)
        self.sources = _load("rc_key_sources", SOURCES)
        self.clock = lambda: 1_761_566_400.0
        self.limit = getattr(self.notify, "TRANSPORT_KEY_LENGTH", TRANSPORT_KEY_LIMIT)

    def _identity(self, run_id: str, device: str) -> str:
        binding = self.sources.RunBinding(
            device_ref=device,
            session_ref=f"{device}|claude-code|sess_1",
            run_id=run_id, bound_boot_id="boot-A")
        adapter = self.sources.CloudCliRunSource(
            lambda *, run_id, timeout_seconds: {
                "idKind": "run", "runId": run_id, "sessionId": "sess_1",
                "status": "completed", "bootId": "boot-A",
                "lastAssistantMessage": {"content": "done"}},
            {run_id: binding},
            policy=self.sources.MilestonePolicy(
                source_trusted=True, milestone=True,
                gates_required=("native-output", "boot-identity"), summary="s"),
            clock=self.clock, monotonic=lambda: 0.0)
        decision = self.notify.decide(
            self.notify.normalize("cloudcli", adapter.read("cloudcli", run_id)))
        return self.notify.delivery_key_for(decision)

    def test_two_runs_on_one_device_get_different_transport_keys(self) -> None:
        device = "device:tokyo-shared-dev-01"
        first = self.notify.transport_key_for(self._identity(
            "run_a1b1c1840000000000000000", device))
        second = self.notify.transport_key_for(self._identity(
            "run_ffffffff0000000000000000", device))
        self.assertNotEqual(first, second,
                            "two runs produced one transport key")
        self.assertLessEqual(len(first), self.limit)
        self.assertLessEqual(len(second), self.limit)

    def test_a_bare_device_name_still_distinguishes(self) -> None:
        for device in ("device:work-fj", "device:tokyo-vince"):
            with self.subTest(device=device):
                left = self.notify.transport_key_for(self._identity(
                    "run_a1b1c1840000000000000000", device))
                right = self.notify.transport_key_for(self._identity(
                    "run_ffffffff0000000000000000", device))
                self.assertNotEqual(left, right,
                                    f"{device} is short enough that the run id "
                                    "must still survive")

    def test_the_legacy_prefix_key_really_does_collide(self) -> None:
        """The negative control: why the digest exists at all.

        If this ever stops colliding the digest is unnecessary; if it collides
        and nothing uses the digest, a second milestone is lost with a receipt.
        """
        device = "device:tokyo-shared-dev-01"
        left = self.notify.legacy_transport_key(self._identity(
            "run_a1b1c1840000000000000000", device))[:self.limit]
        right = self.notify.legacy_transport_key(self._identity(
            "run_ffffffff0000000000000000", device))[:self.limit]
        self.assertEqual(left, right,
                         "the legacy prefix key no longer collides; either the "
                         "identity format changed or this control has gone stale")


# --------------------------------------------------------------------------- #
# #420 — the contract that guards the announcer is itself collected
# --------------------------------------------------------------------------- #

class TheContractIsCollected(unittest.TestCase):
    def setUp(self) -> None:
        if not HERMETIC.is_file() or not CONTRACT_TEST.is_file():
            self.skipTest("the harness or the contract test is not in this tree")
        self.harness = _load("rc_hermetic", HERMETIC)

    def test_the_contract_test_is_scheduled_by_exactly_one_shard(self) -> None:
        rel = "tests/test_fleet_milestone_architecture_contract.py"
        counts = [sum(1 for name in self.harness.shard_files(spec)
                      if name.replace("\\", "/") == rel)
                  for spec in HARNESS_SHARDS]
        self.assertEqual(sum(counts), 1,
                         f"the contract test must be collected once, saw {counts}; "
                         "a presence gate nobody runs is the silence it forbids")

    def test_the_contract_test_is_not_baselined(self) -> None:
        listed = [line.strip() for line in
                  BASELINE.read_text(encoding="utf-8").splitlines()
                  if line.strip() and not line.strip().startswith("#")]
        self.assertEqual([line for line in listed
                          if "test_fleet_milestone_architecture_contract" in line],
                         [], "the contract itself may not be baselined")


if __name__ == "__main__":
    unittest.main()
