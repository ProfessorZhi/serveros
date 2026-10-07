"""The operator's trust root: what `work trust` writes, and who refuses to write it.

One test here is not about this module at all. `test_binding_accepts_a_signature_
the_real_verifier_checks` hands a binding written by `bind` to
`context/actor.verify_delegation` and requires it to accept a real signature.
That is the only assertion that the file this verb writes is the file the
durable route reads; everything else about shape could be satisfied by a
document nothing consumes.

The rest are refusals, because that is what this module mostly is: it cannot
create the separation between the signer and the execution, so its contribution
is declining to record a binding that pretends the separation exists.
"""
import base64
import copy
import importlib.util
import json
import os
import pwd
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


trust = load("serveros_context_trust_binding", "context/trust_binding.py")
actor = trust._actor_module()
store_mod = load("serveros_runtime_agent_control_store", "runtime/agent_control_store.py")

OBSERVED = "2026-10-07T08:00:00+00:00"
ISSUED = "2026-10-07T08:01:00+00:00"
ITEM = "SRV-TEST"
AUDIENCE = "device:test"


def other_user() -> str:
    """A local unix user this process is not, or skip. Measured, not assumed."""
    for entry in pwd.getpwall():
        if entry.pw_uid != os.getuid():
            return entry.pw_name
    pytest.skip("this host has one uid, so no separate custodian exists to name")


def me() -> str:
    return pwd.getpwuid(os.getuid()).pw_name


@pytest.fixture
def public_key():
    private = Ed25519PrivateKey.generate()
    raw = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return private, base64.b64encode(raw).decode()


@pytest.fixture
def binding(tmp_path, public_key):
    _, encoded = public_key
    path = tmp_path / "human-control-trust.json"
    trust.bind("provider:test", "key-1", encoded, audience=AUDIENCE,
               custodian_kind="unix-user", custodian_name=other_user(), path=path)
    return path


# ---------------------------------------------------------------- absence

def test_no_binding_is_a_state_not_a_fault(tmp_path):
    result = trust.verdict(tmp_path / "absent.json")
    assert result["binding"] == "ABSENT"
    assert result["durable_route"] == "UNREACHABLE"
    assert result["missing"] == ["TRUST_BINDING_ABSENT"]
    assert result["keys"] == []
    assert not (tmp_path / "absent.json").exists()


def test_legacy_route_numbers_come_from_the_contract(tmp_path):
    """A second copy of 300/900 here is a copy that can drift from the one read."""
    verdict = trust.verdict(tmp_path / "absent.json")
    window = actor.contract()["freshness"]
    assert verdict["legacy_route"]["authorizes_for_seconds"] == int(window["stale_after_seconds"])
    assert verdict["legacy_route"]["displayable_for_seconds"] == int(window["window_seconds"])


def test_malformed_binding_reports_the_verifiers_own_sentence(tmp_path):
    path = tmp_path / "human-control-trust.json"
    path.write_text(json.dumps({"schema": "something-else"}))
    result = trust.verdict(path)
    assert result["binding"] == "INVALID"
    assert "DELEGATION_PROVIDER_UNBOUND" in result["because"]
    assert result["missing"] == ["TRUST_BINDING_INVALID"]


def test_unknown_field_is_refused_rather_than_ignored(tmp_path, public_key):
    """`audiance` is one keystroke from `audience` and would verify nothing."""
    _, encoded = public_key
    path = tmp_path / "human-control-trust.json"
    path.write_text(json.dumps({"schema": trust.TRUST_SCHEMA, "audiance": AUDIENCE,
                                "providers": {}}))
    with pytest.raises(trust.Refused, match="unknown top-level fields"):
        trust.bind("provider:test", "key-1", encoded, audience=AUDIENCE,
                   custodian_kind="unix-user", custodian_name=other_user(), path=path)


# ---------------------------------------------------------------- custody

def test_custodian_that_is_this_execution_is_refused_not_recorded(tmp_path, public_key):
    _, encoded = public_key
    path = tmp_path / "human-control-trust.json"
    with pytest.raises(trust.Refused, match="this execution's own uid"):
        trust.bind("provider:test", "key-1", encoded, audience=AUDIENCE,
                   custodian_kind="unix-user", custodian_name=me(), path=path)
    assert not path.exists()


def test_separate_unix_custodian_is_measured_and_reported(tmp_path, binding):
    result = trust.verdict(binding)
    assert result["binding"] == "PRESENT"
    assert result["durable_route"] == "REACHABLE"
    assert result["keys"][0]["custody"] == trust.SEPARATE
    assert result["missing"] == []


def test_unnameable_custodian_cannot_claim_reachability(tmp_path, public_key):
    """An off-host signer is a claim nothing here can look up, and is not 'separate'."""
    _, encoded = public_key
    path = tmp_path / "human-control-trust.json"
    result = trust.bind("provider:ext", "key-1", encoded, audience=AUDIENCE,
                        custodian_kind="external-system", custodian_name="partner-hsm",
                        path=path)
    assert result["keys"][0]["custody"] == trust.UNVERIFIED
    assert result["durable_route"] == "REACHABLE_IF_CUSTODY_HOLDS"
    assert result["missing"] == ["CUSTODY_UNMEASURED"]


def test_missing_user_is_not_a_separate_one(tmp_path, public_key):
    """A custodian naming nobody resolves to UNVERIFIED, and UNVERIFIED is not
    "separate" — it is a claim, and the route is reported as one that depends on it."""
    _, encoded = public_key
    result = trust.bind("provider:test", "key-1", encoded, audience=AUDIENCE,
                        custodian_kind="unix-user", custodian_name="definitely-not-a-user-x9",
                        path=tmp_path / "t.json")
    assert result["keys"][0]["custody"] == trust.UNVERIFIED
    assert "does not exist on this host" in result["keys"][0]["custody_because"]
    assert result["durable_route"] == "REACHABLE_IF_CUSTODY_HOLDS"


# ---------------------------------------------------------------- writes

def test_bind_merges_rather_than_replacing_the_document(tmp_path, binding, public_key):
    private2 = Ed25519PrivateKey.generate()
    encoded2 = base64.b64encode(private2.public_key().public_bytes(Encoding.Raw,
                                                                   PublicFormat.Raw)).decode()
    trust.bind("provider:second", "key-2", encoded2, audience=AUDIENCE,
               custodian_kind="unix-user", custodian_name=other_user(), path=binding)
    result = trust.verdict(binding)
    assert sorted(row["provider_id"] for row in result["keys"]) == ["provider:second", "provider:test"]


def test_existing_key_id_is_not_overwritten_without_saying_so(tmp_path, binding, public_key):
    _, encoded = public_key
    with pytest.raises(trust.Refused, match="already has key"):
        trust.bind("provider:test", "key-1", encoded, audience=AUDIENCE,
                   custodian_kind="unix-user", custodian_name=other_user(), path=binding)
    assert trust.bind("provider:test", "key-1", encoded, audience=AUDIENCE,
                      custodian_kind="unix-user", custodian_name=other_user(),
                      replace=True, path=binding)["durable_route"] == "REACHABLE"


def test_audience_cannot_be_changed_under_existing_keys(tmp_path, binding, public_key):
    _, encoded = public_key
    with pytest.raises(trust.Refused, match="unbinds every key"):
        trust.bind("provider:other", "key-9", encoded, audience="device:elsewhere",
                   custodian_kind="unix-user", custodian_name=other_user(), path=binding)


def test_disable_is_a_named_verb_and_leaves_the_rest_alone(tmp_path, binding, public_key):
    private2 = Ed25519PrivateKey.generate()
    encoded2 = base64.b64encode(private2.public_key().public_bytes(Encoding.Raw,
                                                                   PublicFormat.Raw)).decode()
    trust.bind("provider:second", "key-2", encoded2, audience=AUDIENCE,
               custodian_kind="unix-user", custodian_name=other_user(), path=binding)
    result = trust.disable("provider:test", "key-1", path=binding)
    states = {(row["provider_id"], row["key_id"]): row["enabled"] for row in result["keys"]}
    assert states == {("provider:test", "key-1"): False, ("provider:second", "key-2"): True}
    assert result["durable_route"] == "REACHABLE"
    only = trust.disable("provider:second", "key-2", path=binding)
    assert only["missing"] == ["NO_ENABLED_KEY"]
    assert only["durable_route"] == "UNREACHABLE"


def test_a_secret_handed_in_as_a_public_key_is_named_and_refused(tmp_path):
    """The two shapes a private half arrives in, each refused with its own sentence.

    64 bytes is libsodium's secret key layout; PEM text is what a key file looks
    like. Neither is a public key, and the refusal says which one it saw rather
    than "invalid base64", which is the sentence that teaches nothing.
    """
    secret = os.urandom(64)
    with pytest.raises(trust.Refused, match="64 bytes"):
        trust.bind("provider:test", "key-1", base64.b64encode(secret).decode(),
                   audience=AUDIENCE, custodian_kind="unix-user",
                   custodian_name=other_user(), path=tmp_path / "t.json")
    with pytest.raises(trust.Refused, match="PEM text"):
        trust.bind("provider:test", "key-1", "-----BEGIN PRIVATE KEY-----\nzz\n",
                   audience=AUDIENCE, custodian_kind="unix-user",
                   custodian_name=other_user(), path=tmp_path / "t.json")
    with pytest.raises(trust.Refused, match="is not base64"):
        trust.bind("provider:test", "key-1", "not base64 at all!!",
                   audience=AUDIENCE, custodian_kind="unix-user",
                   custodian_name=other_user(), path=tmp_path / "t.json")


# ---------------------------------------------------------------- the consumer

def test_binding_accepts_a_signature_the_real_verifier_checks(tmp_path, binding,
                                                              public_key, monkeypatch):
    """The file `bind` writes is the file `verify_delegation` reads.

    Everything else in this module can be self-consistent and still write a
    document no consumer opens. This drives the real verifier over a real
    signature under the key that `bind` recorded.
    """
    private, _ = public_key
    monkeypatch.setattr(actor, "DELEGATION_TRUST", binding)
    report = {"human_control_provider": "provider:test", "transport": "fixture",
              "transport_identity": "fixture-verified-user", "conversation_ref": "chat:fixture",
              "message_ref": "message:fixture", "observed_at": OBSERVED,
              "provider_status": "VERIFIED", "provenance": "FACT",
              "actor_ref": "human:fixture-user"}
    grant = {"grant_ref": "grant:fixture", "projects": ["serveros"], "items": [ITEM],
             "actions": ["research"], "scope_digests": {ITEM: "sha256:" + "a" * 64},
             "valid_from": OBSERVED, "expires_at": "2026-10-08T08:00:00+00:00",
             "budget": {"wall_seconds": 600, "max_attempts": 2,
                        "max_output_bytes": 65536, "exhausted": False}}
    payload = {"schema": actor.DELEGATION_EVIDENCE_SCHEMA, "provider_id": "provider:test",
               "key_id": "key-1", "audience": AUDIENCE, "issued_at": ISSUED,
               "actor_report": report, "grant": grant}
    body = copy.deepcopy(payload)
    signature = private.sign(actor.signed_bytes(body))
    approved, context = actor.verify_delegation(
        {"payload": body, "signature": base64.b64encode(signature).decode()}, now=ISSUED)
    assert approved["delegated_by"] == context["context_ref"]
    store = store_mod.AgentControlStore(tmp_path / "store.json")
    stored = store.issue_grant({"payload": copy.deepcopy(payload),
                                "signature": base64.b64encode(signature).decode()}, now=ISSUED)
    assert stored["issuance"]["schema"] == actor.ISSUANCE_SCHEMA
    assert stored["grant_ref"] == "grant:fixture"


def test_a_key_bound_under_another_audience_does_not_verify(tmp_path, binding,
                                                            public_key, monkeypatch):
    private, _ = public_key
    monkeypatch.setattr(actor, "DELEGATION_TRUST", binding)
    payload = {"schema": actor.DELEGATION_EVIDENCE_SCHEMA, "provider_id": "provider:test",
               "key_id": "key-1", "audience": "device:elsewhere", "issued_at": ISSUED,
               "actor_report": {}, "grant": {}}
    signature = private.sign(actor.signed_bytes(payload))
    with pytest.raises(actor.Rejected, match="DELEGATION_PROVIDER_UNBOUND"):
        actor.verify_delegation({"payload": payload,
                                 "signature": base64.b64encode(signature).decode()},
                                now=ISSUED)
