#!/usr/bin/env python3
"""The signed delegation path, run for real against a faked world.

WHY THIS FILE EXISTS
--------------------
`runtime/journey.py#signed_delegation_provider` is the only production entry that can
turn a person's approval into a `work_ref`. On this host it has never executed: no
`cryptography`, no operator trust binding. That makes it the highest-value untested code
on the C line, and "it is only a thin adapter over `context/actor.py`" is exactly the
kind of claim a test exists to stop being a claim.

So these tests do NOT stub the verifier. They install a deterministic world
(`tests/journey_provider_fixture.py`) and then call the REAL
`context/actor.py#verify_delegation` through the REAL provider. What is faked is the
signature PRIMITIVE and the trust ROOT's location — never the protocol.

WHAT EACH GROUP PROVES
----------------------
  positive
    A provider-signed approval walks B3→B4→A7→B8→B10 and the work's owner is the
    VERIFIED subject — not a string the caller typed.

  fail-closed (the reason this file exists)
    Every way a trust decision can go wrong ends without a `work_ref`: forged signature,
    unknown issuer, disabled key, wrong audience, an issuance clock ahead of the reader,
    an expired grant window, a grant that does not cover this revision, a handle for a
    different context, a missing trust binding, and no evidence at all. Not one of them
    may fall back to anything.

WHAT THIS FILE DOES NOT PROVE
-----------------------------
That Ed25519 works, that a real HumanControlProvider signs these payloads, or that a
real person approved anything. `tests/journey_provider_fixture.py` says so at length;
this file is where it is exercised.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
RUNTIME = REPO / "runtime"
HERE = Path(__file__).resolve().parent


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


journey = _load("serveros_runtime_journey", RUNTIME / "journey.py")
fixture = _load("journey_provider_fixture", HERE / "journey_provider_fixture.py")
actor = journey._actor()

CHAT = "feishu:chat:oc_sample"
MESSAGE = "om_sample_0001"
REVISION = "sha256rev0001"
MESSAGE_REF = journey.message_ref(CHAT, MESSAGE)
AT = "2026-10-14T10:00:00+09:00"
ISSUED = "2026-10-14T10:00:00+09:00"
GOAL = "把这次改动落成一次有测试的交付"
APPROVER = "feishu:union:on_fixture"


class Sender:
    """The injected transport — the same seam `report_delivery` designs for."""

    def __init__(self, state="DELIVERED", why="收件端接受了这条消息"):
        self.calls, self.state, self.why = [], state, why

    def __call__(self, request):
        self.calls.append(request)
        return {"state": self.state, "why": self.why,
                "provider": {"id": "fixture-transport", "transport": "fixture"}}


def observation(**over):
    spec = {"source_id": CHAT, "message_id": MESSAGE, "revision": REVISION,
            "captured_at": AT, "trust": "authorized-follow"}
    spec.update(over)
    return spec


def _proposal_and_revision(observations):
    store = journey.capture(journey.empty_store(), observations)["store"]
    return (journey.propose(store, goal=GOAL, sources=[MESSAGE_REF]),
            journey.source_revision(store, [MESSAGE_REF]))


def signed_claim(issuer, proposal, revision, *, report_over=None, grant_over=None,
                 evidence_over=None, context_ref=None, kind="human"):
    """The handle plus the evidence a caller would present. Neither is trusted."""
    report = issuer.report(**(report_over or {}))
    grant = issuer.grant(covers=[proposal["requirement_ref"]],
                         scope_digests={proposal["requirement_ref"]: revision},
                         **(grant_over or {}))
    evidence = issuer.evidence(actor_report=report, grant=grant, issued_at=ISSUED,
                               **(evidence_over or {}))
    return {"kind": kind,
            "context_ref": (context_ref if context_ref is not None
                            else issuer.context_ref(report, issued_at=ISSUED)),
            "approver_ref": report["actor_ref"], "evidence": evidence}


def journey_input(issuer, *, observations=None, approval=None, **unused):
    observations = observations or [observation()]
    proposal, revision = _proposal_and_revision(observations)
    return {
        "observations": observations, "goal": GOAL, "sources": [MESSAGE_REF],
        "approval_claim": (approval if approval is not None
                           else signed_claim(issuer, proposal, revision)),
        "run": {"name": "tiny-tests", "kind": "run",
                "observed": {"ran": True, "exit_code": 0, "at": AT}},
        "period": {"start": "2026-10-14T00:00:00+09:00",
                   "end": "2026-10-14T23:59:59+09:00"},
        "delivery": {"recipient": "chat:oc_receiver", "requested_by": "person:vince",
                     "why": "验收通过，按约定回报",
                     "authorization": {"authorized": True, "because": "刚刚复查过"}},
    }


def walk(tmp_path, issuer, spec=None, *, sender=None):
    out_dir = tmp_path / "report"
    out_dir.mkdir(parents=True, exist_ok=True)
    return journey.walk(spec if spec is not None else journey_input(issuer),
                        out_dir=str(out_dir), sender=sender or Sender(), at=AT,
                        verifier=journey.signed_delegation_provider())


# ---------------------------------------------------------------------------
# 正控：真实 verify_delegation 跑通了
# ---------------------------------------------------------------------------

class TestTheSignedPathRuns:
    def test_a_provider_signed_approval_walks_the_whole_chain(self, tmp_path):
        with fixture.provider_world(actor, tmp_path / "world") as issuer:
            record = walk(tmp_path, issuer)
        assert record["stop"] == "DELIVERED"
        assert [s["kind"] for s in record["receipts"]] == [
            "capture", "requirement", "decision", "work", "acceptance",
            "report", "delivery"]

    def test_the_work_owner_is_the_verified_subject(self, tmp_path):
        with fixture.provider_world(actor, tmp_path / "world") as issuer:
            record = walk(tmp_path, issuer)
        assert record["refs"]["approved_by"] == APPROVER
        work = [s for s in record["receipts"] if s["kind"] == "work"][0]
        assert APPROVER in work["why"]

    def test_the_version_binding_comes_from_the_signed_payload(self, tmp_path):
        """`approved_revision` is read out of the signed `scope_digests`, so the
        chain accepts exactly the revision the provider signed for."""
        with fixture.provider_world(actor, tmp_path / "world") as issuer:
            observations = [observation()]
            proposal, revision = _proposal_and_revision(observations)
            claim = signed_claim(issuer, proposal, revision)
        assert claim["evidence"]["payload"]["grant"]["scope_digests"][
            proposal["requirement_ref"]] == revision

    def test_the_double_is_not_left_installed(self):
        """The double must not outlive its context manager: a leaked `cryptography`
        would make every later test's signature check meaningless, and the resulting
        failures would point at whichever test ran next.

        The assertion is about THIS FILE's modules rather than about
        `"cryptography" in sys.modules`, because the latter is a claim about the host —
        it is True on a python that ships the real package, and would make this test a
        statement about the interpreter instead of about the fixture.
        """
        for name, module in list(sys.modules.items()):
            if name == "cryptography" or name.startswith("cryptography."):
                assert not getattr(module, fixture.FIXTURE_MARK, False), name


# ---------------------------------------------------------------------------
# 负控：每一种信任判定失败都不铸造 work
# ---------------------------------------------------------------------------

class TestEveryTrustFailureFailsClosed:
    def _refused(self, tmp_path, *, report_over=None, grant_over=None,
                 evidence_over=None, context_ref=None, binding=True, **binding_over):
        """Walk with one thing wrong, and return the refusal text."""
        world = tmp_path / "world"
        with fixture.provider_world(actor, world, binding=binding, **binding_over) as issuer:
            observations = [observation()]
            proposal, revision = _proposal_and_revision(observations)
            claim = signed_claim(issuer, proposal, revision, report_over=report_over,
                                 grant_over=grant_over, evidence_over=evidence_over,
                                 context_ref=context_ref)
            with pytest.raises(journey.Refused) as caught:
                walk(tmp_path, issuer, journey_input(issuer, observations=observations,
                                                     approval=claim))
        return str(caught.value)

    def test_a_forged_signature_is_refused(self, tmp_path):
        why = self._refused(tmp_path, evidence_over={"signature": "Z2FyYmFnZQ=="})
        assert "DELEGATION_SIGNATURE_INVALID" in why

    def test_an_unknown_issuer_is_refused(self, tmp_path):
        why = self._refused(tmp_path, report_over={"human_control_provider": "somebody"},
                            evidence_over={"provider_id": "somebody"})
        assert "DELEGATION_PROVIDER_UNBOUND" in why

    def test_a_disabled_key_is_refused(self, tmp_path):
        why = self._refused(tmp_path, enabled=False)
        assert "DELEGATION_PROVIDER_UNBOUND" in why

    def test_a_wrong_audience_is_refused(self, tmp_path):
        why = self._refused(tmp_path, audience="some-other-audience")
        assert "DELEGATION_PROVIDER_UNBOUND" in why

    def test_an_issuance_clock_ahead_of_the_reader_is_refused(self, tmp_path):
        why = self._refused(tmp_path, evidence_over={"issued_at": "2026-10-15T10:00:00+09:00"})
        assert "DELEGATION_EVIDENCE_INVALID" in why

    def test_an_expired_grant_window_is_refused(self, tmp_path):
        why = self._refused(tmp_path, grant_over={"valid_from": "2026-10-14T07:00:00+09:00",
                                                  "expires_at": "2026-10-14T08:00:00+09:00"})
        assert "DELEGATION_SCOPE_INVALID" in why

    def test_a_grant_with_no_actions_is_refused(self, tmp_path):
        why = self._refused(tmp_path, grant_over={"actions": []})
        assert "DELEGATION_SCOPE_INVALID" in why

    def test_a_handle_for_a_different_context_is_refused(self, tmp_path):
        why = self._refused(tmp_path, context_ref="human-context:somebody-else/0000")
        assert "PRESENTED_REF_MISMATCH" in why

    def test_no_trust_binding_is_refused(self, tmp_path):
        why = self._refused(tmp_path, binding=False)
        assert "DELEGATION_PROVIDER_UNBOUND" in why

    def test_a_claim_with_no_evidence_is_refused(self, tmp_path):
        """A handle with nothing behind it. `verify_delegation` refuses the envelope
        shape before it reads any trust root, which is the right order: an
        unbound host and a missing signature are different facts."""
        with fixture.provider_world(actor, tmp_path / "world") as issuer:
            observations = [observation()]
            proposal, revision = _proposal_and_revision(observations)
            claim = signed_claim(issuer, proposal, revision)
            del claim["evidence"]
            with pytest.raises(journey.Refused) as caught:
                walk(tmp_path, issuer, journey_input(issuer, observations=observations,
                                                     approval=claim))
        assert "DELEGATION_EVIDENCE_INVALID" in str(caught.value)

    def test_a_grant_covering_an_older_revision_asks_the_human_again(self, tmp_path):
        """Not a REFUSED — the approval is genuine, it just does not cover the
        revision the source is now at. That distinction is the whole point of
        binding the version inside the signature."""
        with fixture.provider_world(actor, tmp_path / "world") as issuer:
            observations = [observation(revision="sha256rev0002")]
            proposal, revision = _proposal_and_revision(observations)
            stale = signed_claim(issuer, proposal, "a-different-revision")
            record = walk(tmp_path, issuer,
                          journey_input(issuer, observations=observations,
                                        approval=stale))
        assert record["stop"] == "NEEDS_HUMAN"
        assert "work_ref" not in record["refs"]
        assert "动过" in record["why"] or "重新问一次" in record["why"]

    def test_a_signed_approval_with_no_verifier_still_authorizes_nothing(self, tmp_path):
        """The boundary again, from the other side: real evidence, no bound
        authority. Evidence is not authority until something is asked to read it."""
        with fixture.provider_world(actor, tmp_path / "world") as issuer:
            spec = journey_input(issuer)
            record = journey.walk(spec, at=AT, verifier=None)
        assert record["stop"] == "NEEDS_HUMAN"
        assert "work_ref" not in record["refs"]


# ---------------------------------------------------------------------------
# 生产条件：本机的真实失败码
# ---------------------------------------------------------------------------

class TestTheRealHostCondition:
    def test_without_the_trust_binding_the_provider_names_the_gap(self):
        """No monkeypatching at all: the operator's binding genuinely does not exist
        on this host, and the provider must say so rather than fall back.

        The payload is deliberately well-SHAPED, so the refusal comes from the trust
        root rather than from a shape complaint — `DELEGATION_PROVIDER_UNBOUND` here
        (and `DELEGATION_VERIFIER_UNAVAILABLE` on a host with a binding but no
        `cryptography`). Both are the same answer to the caller: not bound, not read,
        not authorized."""
        verifier = journey.signed_delegation_provider()
        payload = {"schema": fixture.EVIDENCE_SCHEMA, "provider_id": fixture.PROVIDER_ID,
                   "key_id": fixture.KEY_ID, "audience": fixture.AUDIENCE,
                   "issued_at": AT, "actor_report": {}, "grant": {}}
        resolution = verifier({"evidence": {"payload": payload, "signature": ""}}, AT)
        assert resolution["found"] is False
        assert "DELEGATION_" in resolution["because"]

    def test_the_production_provider_never_reads_a_trust_root_from_the_claim(self):
        """`signed_delegation_provider()` takes no binding and no path. A caller
        that could point the trust root would be choosing its own authority."""
        import inspect
        assert list(inspect.signature(
            journey.signed_delegation_provider).parameters) == []
