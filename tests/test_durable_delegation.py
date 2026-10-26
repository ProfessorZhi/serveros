"""Synthetic signing fixture; no real human event, provider key or live store."""
import base64
import copy
import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

import owner_policy_fixtures as opf


ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


store_mod = load("serveros_runtime_agent_control_store", "runtime/agent_control_store.py")
delegation = load("serveros_context_delegation", "context/delegation.py")
actor = delegation._actor_module()
cycle = load("serveros_intake_daily_cycle", "intake/daily_cycle.py")
wake = load("durable_wake", "runtime/wake.py")
continuation = load("durable_continuation", "runtime/continuation.py")
control = load("durable_continuation_control", "runtime/continuation_control.py")

OBSERVED = "2026-10-06T08:00:00+00:00"
ISSUED = "2026-10-06T08:01:00+00:00"
LATE = "2026-10-06T12:00:00+00:00"
EXPIRED = "2026-10-07T08:00:00+00:00"
ITEM = "SRV-TEST"


@pytest.fixture
def signing(tmp_path, monkeypatch):
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    trust_path = tmp_path / "operator-trust.json"
    trust = {"schema": "serveros/human-control-trust.v1", "audience": "device:test",
             "providers": {"provider:fixture-human-control": {"keys": {"test-key": {
                 "enabled": True, "public_key": base64.b64encode(public).decode()}}}}}
    trust_path.write_text(json.dumps(trust))
    monkeypatch.setattr(actor, "DELEGATION_TRUST", trust_path)
    report = {"human_control_provider": "provider:fixture-human-control", "transport": "fixture",
              "transport_identity": "fixture-verified-user", "conversation_ref": "chat:fixture",
              "message_ref": "message:fixture-explicit-approval", "observed_at": OBSERVED,
              "provider_status": "VERIFIED", "provenance": "FACT", "actor_ref": "human:fixture-user"}
    grant = {"grant_ref": "grant:fixture", "projects": ["serveros"], "items": [ITEM],
             "actions": ["research"], "scope_digests": {ITEM: "sha256:" + "a" * 64},
             "valid_from": OBSERVED, "expires_at": EXPIRED,
             "budget": {"wall_seconds": 600, "max_attempts": 2,
                        "max_output_bytes": 65536, "exhausted": False}}
    payload = {"schema": actor.DELEGATION_EVIDENCE_SCHEMA,
               "provider_id": "provider:fixture-human-control", "key_id": "test-key",
               "audience": "device:test", "issued_at": ISSUED, "actor_report": report, "grant": grant}

    def sign(value=None, key=None):
        body = copy.deepcopy(payload if value is None else value)
        signature = (key or private).sign(actor.signed_bytes(body))
        return {"payload": body, "signature": base64.b64encode(signature).decode()}

    return payload, sign, trust_path


def issue(tmp_path, signing):
    _, sign, _ = signing
    store = store_mod.AgentControlStore(tmp_path / "state/store.json")
    evidence = sign()
    grant = store.issue_grant(evidence, now=ISSUED)
    return store, grant, evidence


def test_persisted_issuance_survives_restart_and_actor_sweep(tmp_path, signing):
    store, grant, _ = issue(tmp_path, signing)
    context = store.get_context(grant["delegated_by"])
    assert not actor.authorizes(context, LATE)["authorizes"]
    store.sweep(before=LATE)
    reopened = store_mod.AgentControlStore(store.path)
    assert reopened.contexts() == {}
    report = delegation.envelopes(reopened.grants(), reopened.contexts(), now=LATE)
    assert report["envelopes"][grant["grant_ref"]]["items"] == [ITEM]
    assert report["rows"][0]["delegation"]["basis"] == "provider-signed-delegation"
    assert wake.authority(reopened.grants(), {}, item=ITEM, now=LATE)["authorizing"] == [grant["grant_ref"]]


@pytest.mark.parametrize("change,clock,reason", [
    ({}, "2026-10-06T08:05:01+00:00", "NOT_FRESH"),
    ({"issued_at": LATE}, ISSUED, "future"),
    ({"audience": "device:other"}, ISSUED, "UNBOUND"),
    ({"key_id": "unbound"}, ISSUED, "UNBOUND"),
])
def test_issuance_requires_fresh_bound_evidence(tmp_path, signing, change, clock, reason):
    payload, sign, _ = signing
    payload.update(change)
    store = store_mod.AgentControlStore(tmp_path / "store.json")
    with pytest.raises(store_mod.StoreError, match=reason):
        store.issue_grant(sign(), now=clock)
    assert store.grants() == {}


def test_self_signed_and_unsigned_pid_reports_cannot_issue(tmp_path, signing):
    payload, sign, _ = signing
    store = store_mod.AgentControlStore(tmp_path / "store.json")
    with pytest.raises(store_mod.StoreError, match="SIGNATURE_INVALID"):
        store.issue_grant(sign(key=Ed25519PrivateKey.generate()), now=ISSUED)
    forged = {**payload["actor_report"], "provider_pid": 1234}
    with pytest.raises(store_mod.StoreError, match="signed provider evidence"):
        store.issue_grant(forged, now=ISSUED)
    proof = sign()
    proof["public_key"] = "caller-selected"
    with pytest.raises(store_mod.StoreError, match="signed provider evidence"):
        store.issue_grant(proof, now=ISSUED)


def test_signed_report_must_identify_verified_human_and_exact_scope(tmp_path, signing):
    payload, sign, _ = signing
    payload["actor_report"].update(provider_status="BLOCKED", provenance="UNKNOWN")
    with pytest.raises(store_mod.StoreError, match="NOT_FRESH"):
        store_mod.AgentControlStore(tmp_path / "store.json").issue_grant(sign(), now=ISSUED)
    payload["actor_report"].update(provider_status="VERIFIED", provenance="FACT")
    proof = sign()
    proof["payload"]["grant"]["items"].append("SRV-OTHER")
    with pytest.raises(store_mod.StoreError, match="SIGNATURE_INVALID"):
        store_mod.AgentControlStore(tmp_path / "store.json").issue_grant(proof, now=ISSUED)


@pytest.mark.parametrize("field,value", [("expires_at", "2027-01-01T00:00:00Z"),
                                         ("items", [ITEM, "SRV-OTHER"]),
                                         ("actions", ["production-change"]),
                                         ("budget", {"wall_seconds": 999999})])
def test_late_use_detects_scope_tamper(tmp_path, signing, field, value):
    store, grant, _ = issue(tmp_path, signing)
    grant[field] = value
    verdict = delegation.derivation(grant, grant["grant_ref"], None, now=LATE)
    assert verdict["state"] == "DELEGATION_DOES_NOT_AUTHORIZE"
    assert "SCOPE_CHANGED" in verdict["because"]


def test_revocation_retry_and_key_withdrawal_do_not_restore_authority(tmp_path, signing):
    store, grant, evidence = issue(tmp_path, signing)
    store.revoke_grant(grant["grant_ref"], at=LATE, reason="fixture withdrawn")
    assert store.issue_grant(evidence, now=LATE)["revoked"] is True
    assert wake.authority(store.grants(), store.contexts(), item=ITEM, now=LATE)["authorizing"] == []
    _, _, trust_path = signing
    trust = json.loads(trust_path.read_text())
    trust["providers"]["provider:fixture-human-control"]["keys"]["test-key"]["enabled"] = False
    trust_path.write_text(json.dumps(trust))
    verdict = delegation.derivation(grant, grant["grant_ref"], None, now=LATE)
    assert "UNBOUND" in verdict["because"]


def test_legacy_grants_cannot_be_upgraded_or_reanimated(tmp_path, signing):
    payload, sign, _ = signing
    store = store_mod.AgentControlStore(tmp_path / "store.json")
    context = actor.build(payload["actor_report"], now=ISSUED)
    store.put_context(context)
    legacy = store.put_grant({**payload["grant"], "delegated_by": context["context_ref"]})
    assert delegation.derivation(legacy, legacy["grant_ref"], context, now=LATE)["state"] == "DELEGATION_DOES_NOT_AUTHORIZE"
    with pytest.raises(store_mod.StoreError, match="cannot replace or upgrade"):
        store.issue_grant(sign(), now=ISSUED)
    with pytest.raises(store_mod.StoreError, match="owner-written"):
        store.put_grant({**legacy, "issuance": {"accepted_at": ISSUED}})


def test_execution_retains_scope_budget_and_expiry_checks(tmp_path, signing):
    store, grant, _ = issue(tmp_path, signing)
    envelope = delegation.envelopes(store.grants(), {}, now=LATE)["envelopes"][grant["grant_ref"]]
    item = {"id": ITEM, "project": "serveros", "scope_digest": "sha256:" + "a" * 64}
    assert cycle.grant_reason(envelope, item=item, action="research", now=datetime.fromisoformat(LATE)) is None
    for changed, action, reason in [({"id": "SRV-OTHER"}, "research", "ITEM_NOT_AUTHORIZED"),
                                    ({"project": "other"}, "research", "PROJECT_OUT_OF_SCOPE"),
                                    ({}, "deploy", "ACTION_NOT_AUTHORIZED"),
                                    ({"scope_digest": "changed"}, "research", "SCOPE_CHANGED")]:
        assert cycle.grant_reason(envelope, item={**item, **changed}, action=action,
                                  now=datetime.fromisoformat(LATE)) == reason
    assert wake.spending(grant["budget"], item=ITEM, grant_ref=grant["grant_ref"])["wall_seconds"] == 600
    assert wake.authority(store.grants(), {}, item=ITEM, now=EXPIRED)["authorizing"] == []


def test_durable_grant_does_not_bypass_checkpoint_epoch(tmp_path, signing):
    store, grant, _ = issue(tmp_path, signing)
    def checkpoint(epoch, now):
        return continuation.build_checkpoint(work_item=ITEM, epoch=epoch, parent_owner="parent:fixture",
                    next_safe_action="continue fixture research", waiting_on={"kind": "window", "until": EXPIRED},
                    write_scope=["workspace/records"], now=now)
    first = store.put_checkpoint(checkpoint(1, ISSUED))
    envelope = wake.capability_envelope(first, grant, grant["grant_ref"], attempt=1,
                    key="fixture-wake", spend=wake.spending(grant["budget"], item=ITEM,
                                                          grant_ref=grant["grant_ref"]), now=LATE)
    def check():
        return control.verify(envelope=envelope, store=store, contexts={}, operation="continuation.inspect",
                              now=LATE, completion_of_item=lambda _: {"state": "NOT_ASSESSED"})
    assert check()["decision"] == "ALLOW"
    store.put_checkpoint(checkpoint(2, LATE))
    assert check()["decision"] == "DENY"
    assert any(row["check"] == "CHECKPOINT_NOT_LIVE" for row in check()["checks"])


def test_daemon_issuance_uses_owner_clock_and_persists_signed_scope(tmp_path, signing):
    daemon_mod = load("durable_daemon", "runtime/daemon.py")
    class Registry:
        def all(self): return []
        def __len__(self): return 0
    # The owner-policy gate was added after this test: with no resolver bound,
    # `_r_agent_grant_issue` answers BLOCKED_OWNER_POLICY and mints nothing, so
    # the assertion below would be reading a refusal. A TEST DOUBLE, the same
    # one tests/test_v1_grant_epoch_authority.py uses — the gate has its own
    # test, and what this one is about is the owner's clock and the stored scope.
    daemon = daemon_mod.Daemon(Registry(), state_dir=tmp_path / "daemon",
                               now=lambda: datetime.fromisoformat(ISSUED).timestamp(),
                               owner_policy_resolver=opf.PermissiveOwnerPolicyResolver())
    _, sign, _ = signing
    # An in-process call has no uid, and the owner-policy gate mints nothing for
    # one: the principal on a bound grant is the kernel's peer, not a field in
    # the request. This is the peer a real socket would hand the daemon.
    peer = {"pid": 4242, "uid": 1000, "gid": 1000}
    response = daemon.handle({"method": "agent.grant.issue", "params": {"evidence": sign()}},
                             peer=peer)
    assert response["ok"], response
    grant = response["result"]["grant"]
    assert grant["issuance"]["accepted_at"] == ISSUED
    assert daemon.control_store().get_grant(grant["grant_ref"]) == grant
    refused = daemon.handle({"method": "agent.grant.issue", "params": {"evidence": sign(), "now": OBSERVED}})
    assert refused["ok"] is False


def test_engineering_binding_is_part_of_approved_scope(tmp_path, signing):
    payload, sign, _ = signing
    payload["grant"]["actions"].append("engineering.execute")
    store = store_mod.AgentControlStore(tmp_path / "store.json")
    with pytest.raises(store_mod.StoreError, match="signed engineering binding"):
        store.issue_grant(sign(), now=ISSUED)
    payload["grant"]["engineering"] = {
        "schema": "serveros/engineering-binding.v1", "worktree": "/workspace/worktrees/project/item",
        "image": "sha256:" + "b" * 64, "memory_bytes": 1024**3, "cpus": 1, "pids_limit": 64}
    grant = store.issue_grant(sign(), now=ISSUED)
    assert delegation.derivation(grant, grant["grant_ref"], None, now=LATE)["state"] == "AUTHORIZED"
    grant["engineering"]["image"] = "sha256:" + "c" * 64
    assert "SCOPE_CHANGED" in delegation.derivation(grant, grant["grant_ref"], None, now=LATE)["because"]


def test_missing_owner_binding_cannot_issue(tmp_path, signing):
    _, sign, trust_path = signing
    trust_path.unlink()
    store = store_mod.AgentControlStore(tmp_path / "store.json")
    with pytest.raises(store_mod.StoreError, match="UNBOUND"):
        store.issue_grant(sign(), now=ISSUED)
    assert store.grants() == {}
