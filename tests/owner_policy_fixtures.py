"""Test fixtures for the owner-policy gate. NOT a production owner source.

`PermissiveOwnerPolicyResolver` derives a matching policy from the grant, the
verified context and the kernel peer. It exists so the pre-existing #272 tests
(which test the grant's own epoch/window/os_principal/scope checks) keep their
meaning with the new owner gate in place. It is a TEST DOUBLE: it does not
prove an owner exists, and it is never bound by `serverosd`.

`StaticOwnerPolicyResolver` returns exactly the policies a test puts in its
dict, so the new owner-policy negatives can exercise a revoked, expired, stale,
wrong-uid, wrong-actor or out-of-scope policy.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


owner_policy = _load("serveros_runtime_owner_policy",
                     REPO / "runtime" / "owner_policy.py")
OwnerPolicy = owner_policy.OwnerPolicy


class PermissiveOwnerPolicyResolver:
    """TEST DOUBLE: a matching policy for whatever the grant/context says."""

    def __init__(self, *, policy_ref: str = "policy:test", epoch: int = 1,
                 issued_by: str = "owner:test", revoked: bool = False) -> None:
        self.policy_ref = policy_ref
        self.epoch = epoch
        self.issued_by = issued_by
        self.revoked = revoked

    def resolve(self, *, policy_ref=None, grant=None, context=None, peer=None,
                project=None, item_ref=None, action=None, now=None):
        grant = grant or {}
        uid = None
        if isinstance(peer, dict) and isinstance(peer.get("uid"), int):
            uid = f"uid:{peer['uid']}"
        return OwnerPolicy(
            policy_ref=str(policy_ref or self.policy_ref),
            os_principal=str(grant.get("os_principal") or uid or ""),
            actor_ref=str((context or {}).get("actor_ref") or ""),
            projects=tuple(grant.get("projects") or ()),
            items=tuple(grant.get("items") or ()),
            actions=tuple(grant.get("actions") or ()),
            valid_from=str(grant.get("valid_from") or "2000-01-01T00:00:00Z"),
            expires_at=str(grant.get("expires_at") or "2999-01-01T00:00:00Z"),
            revoked=self.revoked,
            epoch=int(self.epoch),
            issued_by=self.issued_by,
            issued_at="2026-01-01T00:00:00Z",
        )


class StaticOwnerPolicyResolver:
    """Return exactly the policy for `policy_ref`, or None.

    `default` is returned when the issuance path asks with `policy_ref=None`
    (the owner source resolves by the facts, not by a pre-existing ref). It is
    a test fixture; production has no resolver at all.
    """

    def __init__(self, policies=None, *, default=None) -> None:
        self.policies = dict(policies or {})
        self.default = default

    def resolve(self, *, policy_ref=None, grant=None, context=None, peer=None,
                project=None, item_ref=None, action=None, now=None):
        # A missing ref falls back to the fixture's default. The production
        # resolver has no such fallback; this is a test double only.
        return self.policies.get(str(policy_ref), self.default)


def a_policy(*, policy_ref="policy:test", os_principal, actor_ref,
             projects=("serveros",), items=("SRV-0001",),
             actions=("lease.acquire",), valid_from="2000-01-01T00:00:00Z",
             expires_at="2999-01-01T00:00:00Z", revoked=False, epoch=1,
             issued_by="owner:test", issued_at="2026-01-01T00:00:00Z"):
    return OwnerPolicy(
        policy_ref=policy_ref, os_principal=os_principal,
        actor_ref=actor_ref, projects=tuple(projects), items=tuple(items),
        actions=tuple(actions), valid_from=valid_from, expires_at=expires_at,
        revoked=revoked, epoch=epoch, issued_by=issued_by,
        issued_at=issued_at)
