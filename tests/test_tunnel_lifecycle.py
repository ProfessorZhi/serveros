#!/usr/bin/env python3
"""§36–§41, §56 — the tunnel as a first-class runtime, and Case A.

WHAT THIS FILE IS ABOUT
-----------------------
Case A is the incident this round exists for, and it is unusual in that
nothing was wrong. The Linux side was HEALTHY. The port forward was HEALTHY.
The far side was HEALTHY. Every observation from creation to retirement was
correct, because the resource was correct — right up to the instant an
unrelated Codex/VS Code session ended and took it with it, on schedule and by
design.

There is therefore no downstream detector to write. A monitor watching this
tunnel reports success for the entire period that matters. The only two
moments the question can be asked are **registration**, which
`tests/test_registration_admission.py` covers, and **the decision to reclaim**,
which is this file.

The distinction the tests below turn on is the one nobody could state during
the incident:

    a still-open work item means the tunnel is still required, no matter
    which Agent sessions have come and gone.

That is what `reclaim_candidacy` answers. It is given the owner's state as an
input because whether a work item is open is `work-management`'s authority and
not something this daemon can observe — and it answers `DEFER`, never a
guess, when it is not told.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path

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


registry_mod = _load("serveros_runtime_registry", "registry.py")
infra = _load("serveros_runtime_infrastructure", "infrastructure.py")
cli_mod = _load("serveros_runtime_cli", "cli.py")
view_mod = _load("serveros_runtime_view", "view.py")

Registry = registry_mod.Registry
RegistryInvalid = registry_mod.RegistryInvalid


def a_tunnel(tunnel_id="tunnel-lit-0104-api", **overrides):
    """Case A's tunnel, as it should have been registered.

    The owner is the work item and the lifecycle is `task-scoped` — not
    `persistent`, which would claim it outlives everything, and not a session,
    which is what actually killed it.
    """
    record = {
        "id": tunnel_id,
        "kind": "tunnel",
        "owner": {"type": "work-item", "ref": "work-item:LIT-0104"},
        "scope": "user",
        "lifecycle": {"class": "task-scoped"},
        "desired": {"state": "running"},
        "protection": {"level": "normal"},
        "origin_device": "device:work-fj",
        "transport": {"local": "127.0.0.1:26400",
                      "remote": "container:lit-0104-dev100-litellm:4000"},
        "probes": [{"id": "conn", "kind": "socket-connect", "axis": "transport",
                    "path": "/tmp/serveros-probe.sock"}],
        "not_applicable": {"functional": {"because":
            "a port forward has no callable surface; a completed TCP handshake "
            "is the strongest thing measurable here, and it is measured on the "
            "transport axis"}},
    }
    record.update(overrides)
    return record


def a_registry(*records):
    return Registry(document={
        "schema": "serveros/managed-runtime-registry.v1",
        "device_ref": "device:server-beijing",
        "resources": list(records),
    })


def resource_of(record):
    return a_registry(record).get(record["id"])


# ---------------------------------------------------------------------------
# §36 — the tunnel is a declared runtime kind
# ---------------------------------------------------------------------------


class TunnelKindTests(unittest.TestCase):
    def test_a_tunnel_is_a_registrable_kind(self):
        """§36. Before this round `tunnel` existed in the contract with
        requirements and nothing exercised it, so the schema was a promise."""
        tunnel = resource_of(a_tunnel())
        self.assertEqual(tunnel.kind, "tunnel")
        self.assertEqual(tunnel.owner_ref, "work-item:LIT-0104")
        self.assertEqual(tunnel.lifecycle, "task-scoped")

    def test_a_tunnel_without_a_functional_answer_is_refused(self):
        """The structural reason a tunnel is different from every other kind.

        A tunnel that reports `liveness: RUNNING` and cannot say whether it
        forwards anything is DEGRADED forever, and a resource whose report is
        permanently DEGRADED is one people stop reading. The registration is
        refused rather than accepted into that state.
        """
        record = a_tunnel()
        record.pop("not_applicable")
        with self.assertRaises(RegistryInvalid) as caught:
            a_registry(record)
        self.assertIn("functional", str(caught.exception))

    def test_a_tunnel_may_not_claim_permanence_for_a_bounded_task(self):
        """§37: do not register LIT-0104's tunnel as permanent
        infrastructure. `persistent` would claim it outlives the task, and it
        does not — that is what LIT-0104 is."""
        tunnel = resource_of(a_tunnel())
        self.assertNotEqual(tunnel.lifecycle, "persistent")


# ---------------------------------------------------------------------------
# §56 — the lifecycle, in the five states that matter
# ---------------------------------------------------------------------------


class ReclaimCandidacyTests(unittest.TestCase):
    def test_task_active_and_session_gone_keeps_the_tunnel_desired(self):
        """§56's first case, and the whole of Case A.

        The session that created the tunnel has disappeared. The task it
        serves is still open. The answer must be "still wanted" — and note
        that nothing in this call mentions a session at all, because the
        record has no session in it to mention.
        """
        tunnel = resource_of(a_tunnel())
        verdict = infra.reclaim_candidacy(tunnel, infra.OWNER_ACTIVE)
        self.assertEqual(verdict["disposition"], infra.NOT_CANDIDATE)
        self.assertIn("still active", verdict["because"])
        self.assertIn("outlives the session", verdict["because"])

    def test_the_record_names_no_session_anywhere(self):
        """The property that makes the case above structural rather than
        lucky. If a session id appeared anywhere in the record — an owner, a
        note, a `created_by` — a future reclaimer keying off it would
        reintroduce exactly the defect, and it would look like a fix."""
        rendered = json.dumps(a_tunnel())
        for prefix in ("session:", "agent-runtime:", "agent-session:"):
            self.assertNotIn(prefix, rendered)

    def test_the_task_closing_makes_it_a_candidate(self):
        """§56's second case. The contract has said since `lifecycle_classes`
        was written that task-scoped is "reclaimable when that closes"; this is
        the first thing that reads that sentence."""
        tunnel = resource_of(a_tunnel())
        verdict = infra.reclaim_candidacy(tunnel, infra.OWNER_CLOSED)
        self.assertEqual(verdict["disposition"], infra.CANDIDATE)
        self.assertEqual(verdict["authority"], "recommendation")

    def test_a_candidate_is_a_proposal_and_never_an_action(self):
        """§17: ServerOS emits candidate, reason and blast radius; removal is
        a person's decision at every tier. A verdict that carried an actuator
        would be the same rule with a trigger attached."""
        verdict = infra.reclaim_candidacy(resource_of(a_tunnel()), infra.OWNER_CLOSED)
        self.assertEqual(verdict["authority"], "recommendation")
        for field in ("action", "command", "automatic"):
            self.assertNotIn(field, verdict)

    def test_a_persistent_tunnel_does_not_die_with_a_task(self):
        """§56's third case, and §38's shape.

        If the outbound tunnel is genuinely permanent infrastructure, then a
        work item closing says nothing about it. Reading the task's closure as
        its death warrant would remove the transport every session depends on
        — the Case A failure with the direction reversed.
        """
        tunnel = resource_of(a_tunnel(lifecycle={"class": "persistent"},
                                      owner={"type": "serveros",
                                             "ref": "serveros:node"}))
        verdict = infra.reclaim_candidacy(tunnel, infra.OWNER_CLOSED)
        self.assertEqual(verdict["disposition"], infra.NOT_CANDIDATE)
        self.assertIn("not the task", verdict["because"])

    def test_a_protected_tunnel_is_never_a_candidate(self):
        tunnel = resource_of(a_tunnel(protection={"level": "protected"}))
        verdict = infra.reclaim_candidacy(tunnel, infra.OWNER_CLOSED)
        self.assertEqual(verdict["disposition"], infra.NOT_CANDIDATE)
        self.assertIn("protected", verdict["because"])

    def test_a_protected_resource_is_refused_before_its_owner_is_considered(self):
        """The order of the checks is the contract's, and it is load-bearing.
        A protected resource that is also task-scoped with a closed task would
        answer "candidate" if owner state were consulted first — a yes that a
        later check takes back, and a function that can do that will be read at
        the wrong moment."""
        protected = resource_of(a_tunnel(protection={"level": "protected"}))
        self.assertEqual(
            infra.reclaim_candidacy(protected, infra.OWNER_CLOSED)["disposition"],
            infra.NOT_CANDIDATE)

    def test_an_unknown_owner_state_is_a_defer_not_a_guess(self):
        """§56's fourth case, for the registered path.

        Whether a work item is open belongs to `work-management`. Not being
        told is the absence of the one input this decision needs, and the
        contract's rule for an owner that cannot be confirmed is DEFER —
        never a candidate. `UNKNOWN` is deliberately not spelled `CLOSED`:
        those two would be the same string apart from the whole point.
        """
        verdict = infra.reclaim_candidacy(resource_of(a_tunnel()), infra.OWNER_UNKNOWN)
        self.assertEqual(verdict["disposition"], infra.DEFER)
        self.assertNotEqual(infra.OWNER_UNKNOWN, infra.OWNER_CLOSED)

    def test_an_undecidable_owner_state_proposes_nothing(self):
        """Only CANDIDATE proposes. Every other disposition is a reason to
        look, or a reason to leave it alone — not a queue entry."""
        for state in (infra.OWNER_UNKNOWN, infra.OWNER_ACTIVE):
            verdict = infra.reclaim_candidacy(resource_of(a_tunnel()), state)
            self.assertNotEqual(verdict["disposition"], infra.CANDIDATE)

    def test_session_scoped_resources_are_not_proposed_when_a_task_closes(self):
        """They are expected to end on their own. Proposing them would spend a
        person's attention on resources that need no decision, and that is how
        a queue stops being read."""
        tunnel = resource_of(a_tunnel(lifecycle={"class": "session-scoped"}))
        verdict = infra.reclaim_candidacy(tunnel, infra.OWNER_CLOSED)
        self.assertEqual(verdict["disposition"], infra.NOT_CANDIDATE)


class OwnerConfirmationTests(unittest.TestCase):
    """The contract's "unknown owner fails safe" rule, and where it actually
    lives. Worth a test because the branch that implements it inside
    `reclaim_candidacy` is unreachable through the normal path — so if the
    REAL guarantee is the registration refusal, that is what must be asserted,
    or the rule would appear implemented and be unenforced."""

    def test_registration_refuses_an_owner_type_outside_the_declared_four(self):
        record = a_tunnel(owner={"type": "unknown", "ref": "unknown:whatever"})
        with self.assertRaises(RegistryInvalid) as caught:
            a_registry(record)
        self.assertIn("unknown owner.type", str(caught.exception))

    def test_the_defence_in_depth_still_fails_safe_if_it_is_ever_reached(self):
        """Constructed directly, bypassing the registry, which is the only way
        to reach the branch. A `Resource` that skipped registration must still
        not become a removal proposal on the strength of an unconfirmed owner."""
        bypassing_registration = registry_mod.Resource({
            **a_tunnel(),
            "owner": {"type": "nobody-knows", "ref": "nobody-knows:it"},
        })
        verdict = infra.reclaim_candidacy(bypassing_registration, infra.OWNER_CLOSED)
        self.assertEqual(verdict["disposition"], infra.DEFER)

    def test_the_check_reads_the_contract_rather_than_a_local_copy(self):
        """Two copies of "which owners exist" is how this round's other two
        defects happened. Asserted by identity of the source, not by the
        values agreeing today."""
        self.assertEqual(infra._declared_owner_types(),
                         tuple(registry_mod.contract()["owner_types"]))


# ---------------------------------------------------------------------------
# §17 — the report, and the fact that something calls it
# ---------------------------------------------------------------------------
#
# The lesson this project keeps re-learning, in the words of
# `tests/test_action_model.py`: `admission()` was a complete, tested policy
# function with no caller anywhere, and the report said "the tier policy
# decides" while nothing ever asked it. A function that is correct and unused
# passes every test of the function. So these assert the CALL, not the logic.


class ReclaimReportTests(unittest.TestCase):
    def test_silence_about_an_owner_is_a_defer_not_a_removal_proposal(self):
        """The default that matters most. A report given no owner states must
        propose nothing at all — because the alternative is inferring "closed"
        from "not mentioned", which is a proposal to remove live work."""
        report = infra.reclaim_report(a_registry(a_tunnel()))
        self.assertEqual(report["by_disposition"], {"DEFER": 1})
        self.assertEqual(report["candidates"], [])
        self.assertEqual(report["owner_states_supplied"], [])

    def test_a_supplied_owner_state_changes_the_disposition(self):
        registry = a_registry(a_tunnel())
        active = infra.reclaim_report(registry, {"work-item:LIT-0104": infra.OWNER_ACTIVE})
        closed = infra.reclaim_report(registry, {"work-item:LIT-0104": infra.OWNER_CLOSED})
        self.assertEqual(active["by_disposition"], {"NOT_CANDIDATE": 1})
        self.assertEqual(closed["by_disposition"], {"CANDIDATE": 1})

    def test_the_report_is_a_recommendation_and_carries_no_actuator(self):
        """Asserted against the fields rather than a keyword in the prose.
        An earlier version of this test searched the note for the word
        "recommendation" and failed on a note that says "a person's decision"
        — a test that pins wording rather than behaviour is one that gets
        updated to match whatever the code now does."""
        report = infra.reclaim_report(
            a_registry(a_tunnel()), {"work-item:LIT-0104": infra.OWNER_CLOSED})
        self.assertEqual(report["authority"], "recommendation")
        for field in ("action", "command", "automatic", "remove"):
            self.assertNotIn(field, report)
        for candidate in report["candidates"]:
            self.assertEqual(candidate["authority"], "recommendation")

    def test_the_cli_surface_actually_calls_it(self):
        """Asserts the call happens. Without this, deleting the wiring leaves
        every other test in this file green — which is exactly how the
        previous consumerless function survived."""
        import inspect
        source = inspect.getsource(cli_mod.cmd_runtime_infrastructure)
        self.assertIn("reclaim_report", source)
        self.assertIn("reclaim", source)

    def test_the_view_surface_actually_calls_it(self):
        import inspect
        source = inspect.getsource(view_mod.infrastructure_report)
        self.assertIn("reclaim_report", source)

    def test_the_view_surface_exposes_it_end_to_end(self):
        """Through the public surface a report reader would use, so this fails
        if the wiring is present but the key is dropped somewhere between."""
        report = view_mod.infrastructure_report(a_registry(a_tunnel()))
        self.assertIn("reclaim", report)
        self.assertEqual(report["reclaim"]["by_disposition"], {"DEFER": 1})


if __name__ == "__main__":
    unittest.main()
