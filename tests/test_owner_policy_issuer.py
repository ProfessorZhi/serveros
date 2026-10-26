"""Portable shape checks for the explicitly configured owner issuer."""
import pytest

import owner_policy_fixtures as fixtures

policy = fixtures.owner_policy
NOW = "2026-10-27T04:00:00+00:00"


def issuer_document(**changes):
    return {"schema": "serveros/owner-policy-issuer.v1", "os_principal": "uid:123",
            "actor_ref": "human:owner", "human_control_provider": "provider:fixture",
            **changes}


def policy_document(**changes):
    return {"policy_ref": "policy:fixture", "os_principal": "uid:456",
            "actor_ref": "human:worker", "projects": ["serveros"],
            "items": ["SRV-0001"], "actions": ["lease.acquire", "work.commit"],
            "valid_from": NOW, "expires_at": "2026-10-27T05:00:00+00:00",
            **changes}


def test_explicit_issuer_has_no_inferred_identity():
    issuer = policy.OwnerPolicyIssuer.from_document(issuer_document())
    assert issuer.os_principal == "uid:123"
    assert issuer.actor_ref == "human:owner"


@pytest.mark.parametrize("changes", [
    {"os_principal": "uid:-1"}, {"os_principal": "uid:True"},
    {"actor_ref": "*"}, {"human_control_provider": ""},
    {"schema": "wrong"}, {"enabled": True},
])
def test_invalid_or_open_issuer_schema_refuses(changes):
    with pytest.raises(ValueError):
        policy.OwnerPolicyIssuer.from_document(issuer_document(**changes))


def test_policy_scope_is_explicit_and_server_audit_is_not_input():
    parsed = policy.validate_policy_request(policy_document(), now=NOW)
    assert parsed["items"] == ["SRV-0001"]
    assert "epoch" not in parsed


@pytest.mark.parametrize("changes", [
    {"projects": ["*"]}, {"items": []}, {"actions": "work.commit"},
    {"items": ["SRV-0001", "SRV-0001"]}, {"os_principal": 456},
    {"expires_at": NOW}, {"expires_at": "2026-10-28T00:00:00"},
    {"epoch": 99}, {"issued_by": "human:owner"}, {"revoked": False},
])
def test_policy_shape_and_window_fail_closed(changes):
    with pytest.raises(ValueError):
        policy.validate_policy_request(policy_document(**changes), now=NOW)


def test_private_issuer_file_refuses_relative_path():
    with pytest.raises(ValueError, match="absolute"):
        policy.load_issuer("caller-selected.json")


def test_point_read_cannot_claim_commit_fence():
    from workengine.authority import AuthorityError
    from workengine.serveros_authority import ServerOSLeaseAuthority

    class Snapshot:
        def verify(self, *args, **kwargs):
            return {"verified": True, "atomicity": "COMMIT_FENCED", "grant": {
                "grant_ref": "grant:1", "principal": "human:owner", "os_principal": "uid:123",
                "epoch": 1, "valid_from": 1, "expires_at": 9999999999, "revoked": False}}

    with pytest.raises(AuthorityError, match="BLOCKED_OWNER_COMMIT_SEAM"):
        ServerOSLeaseAuthority(Snapshot(), grant_ref="grant:1").verify(
            "context:fixture", action="lease.acquire", project="serveros", item_ref="SRV-0001", now=10)
