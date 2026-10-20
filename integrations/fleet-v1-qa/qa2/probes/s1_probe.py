#!/usr/bin/env python3
"""QA2 independent probe for S1 #358 @ b183aab.

Read-only against an exported snapshot of the exact PR head. It imports the
production daemon/store/owner_policy modules from that snapshot, creates real
store files in a temp directory, and uses the real `Daemon.handle` routes.

Evidence levels:
  * LOCAL_SYNTHETIC_PEER: the peer record is test data, never SO_PEERCRED.
  * LOCAL_SYNTHETIC_POLICY: OwnerPolicy objects are test fixtures.
  * REAL_TEMP (only where a real AF_UNIX socket is used): not used here; the
    author's test covers it.
  * No real provider, model, credential, network, daemon or system service.

Usage:
  python3 s1_probe.py --mode controls
  python3 s1_probe.py --mode findings

Exit code:
  controls: 0 iff every safe-behavior control holds.
  findings: 1 iff at least one adversarial witness confirms a gap.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

SNAPSHOT = Path(os.environ.get(
    "S1_SNAPSHOT",
    "/data/home/vince/artifacts/skills/qa2-independent-security/"
    "2026-10-20/checkouts/s1-358-b183aab")).resolve()
REPO = SNAPSHOT
ITEM = "SRV-0001"
PROJECT = "serveros"
ACTION = "lease.acquire"


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


daemon_mod = _load("serveros_runtime_daemon", REPO / "runtime/daemon.py")
registry_mod = _load("serveros_runtime_registry", REPO / "runtime/registry.py")
actor_mod = _load("_serveros_context_actor", REPO / "context/actor.py")
owner_policy_mod = _load("serveros_runtime_owner_policy",
                         REPO / "runtime/owner_policy.py")
store_mod = daemon_mod.agent_control_store_mod
OwnerPolicy = owner_policy_mod.OwnerPolicy


def a_registry():
    return registry_mod.Registry(document={
        "schema": "serveros/managed-runtime-registry.v1",
        "device_ref": "device:qa2",
        "resources": [],
    })


def iso(daemon, offset_s: float = 0.0) -> str:
    return daemon_mod._iso(daemon._now() + offset_s)


def a_context(daemon, *, actor="human:A", status="VERIFIED",
              provenance="FACT") -> dict:
    now = iso(daemon)
    record = actor_mod.build({
        "human_control_provider": "provider:qa2-fixture",
        "transport": "feishu",
        "transport_identity": "qa2-fixed-identity",
        "conversation_ref": "oc_qa2",
        "message_ref": "om_qa2",
        "observed_at": now,
        "provider_status": status,
        "provenance": provenance,
        "actor_ref": actor,
        "provider_pid": 4242,
    }, now=now)
    daemon.control_store().put_context(record)
    return record


def a_policy(**over):
    base = dict(
        policy_ref="policy:test",
        os_principal=f"uid:{os.getuid()}",
        actor_ref="human:A",
        projects=(PROJECT,),
        items=(ITEM,),
        actions=(ACTION,),
        valid_from="2000-01-01T00:00:00Z",
        expires_at="2999-01-01T00:00:00Z",
        revoked=False,
        epoch=1,
        issued_by="owner:test",
        issued_at="2026-01-01T00:00:00Z",
    )
    base.update(over)
    return OwnerPolicy(**base)


class StaticResolver:
    def __init__(self, policies=None, *, default=None):
        self.policies = dict(policies or {})
        self.default = default

    def resolve(self, *, policy_ref=None, grant=None, context=None, peer=None,
                project=None, item_ref=None, action=None, now=None):
        if policy_ref is None and self.default is not None:
            return self.default
        return self.policies.get(str(policy_ref))


class WrongRefResolver:
    """Returns its policy for every ref, to exercise the ref-mismatch check."""

    def __init__(self, policy):
        self.policy = policy

    def resolve(self, **_):
        return self.policy


class RevokingResolver:
    """A concurrent owner revoke lands while the route is resolving policy."""

    def __init__(self, store, grant_ref, policy):
        self.store = store
        self.grant_ref = grant_ref
        self.policy = policy

    def resolve(self, *, now=None, **kwargs):
        self.store.revoke_grant(self.grant_ref, at=now, reason="qa2 race")
        return self.policy


def a_daemon(tmpdir: Path, resolver=None):
    return daemon_mod.Daemon(
        a_registry(),
        state_dir=tmpdir / "state",
        socket_path=tmpdir / "run" / "serveros.sock",
        log=lambda message: None,
        owner_policy_resolver=resolver,
    )


def a_bound_grant(daemon, context, policy, *, ref="grant:1", uid=None,
                  with_binding=True, **over):
    store = daemon.control_store()
    record = {
        "grant_ref": ref,
        "delegated_by": context["context_ref"],
        "valid_from": iso(daemon, -60),
        "expires_at": iso(daemon, 600),
        "projects": [PROJECT],
        "items": [ITEM],
        "actions": [ACTION],
        "revoked": False,
        "epoch": store._next_grant_epoch(),
        "os_principal": f"uid:{os.getuid() if uid is None else uid}",
        "actor_ref": context["actor_ref"],
    }
    if with_binding:
        record.update({
            "owner_policy_ref": policy.policy_ref,
            "owner_policy_epoch": policy.epoch,
            "owner_policy_issued_by": policy.issued_by,
            "owner_policy_issued_at": policy.issued_at,
        })
    record.update(over)
    with store._mutation():
        store._data["grants"][ref] = record
        store._flush()
    return record


def a_peer(uid=None):
    return {"pid": os.getpid(), "uid": os.getuid() if uid is None else uid,
            "gid": os.getgid()}


def verify(daemon, *, context_ref, grant_ref="grant:1", action=ACTION,
           project=PROJECT, item_ref=ITEM, peer_value=None, **extra):
    params = {"context_ref": context_ref, "grant_ref": grant_ref,
              "action": action, "project": project, "item_ref": item_ref}
    params.update(extra)
    return daemon.handle(
        {"method": "agent.lease.authority.verify", "params": params},
        peer=a_peer() if peer_value is None else peer_value)["result"]


def issue(daemon, *, evidence=None, params=None):
    return daemon.handle(
        {"method": "agent.grant.issue",
         "params": params if params is not None else {"evidence": evidence or {}}},
        peer=a_peer())["result"]


def world(policy=None, *, resolver=None, with_binding=True, grant_over=None,
          context_status="VERIFIED", context_provenance="FACT"):
    """A fresh temp daemon/store/context/grant. Caller owns the TempDirectory."""
    td = tempfile.TemporaryDirectory(prefix="qa2-s1-")
    root = Path(td.name)
    policy = policy or a_policy()
    resolver = resolver if resolver is not None else StaticResolver(
        {policy.policy_ref: policy})
    daemon = a_daemon(root, resolver=resolver)
    context = a_context(daemon, status=context_status,
                        provenance=context_provenance)
    grant = a_bound_grant(daemon, context, policy, with_binding=with_binding,
                          **(grant_over or {}))
    return td, daemon, context, grant, policy


RESULTS = []


def record(name, level, expected, actual, *, ok, note="", severity=None):
    RESULTS.append({
        "name": name, "level": level, "expected": expected, "actual": actual,
        "ok": bool(ok), "note": note, "severity": severity,
    })
    print(json.dumps(RESULTS[-1], ensure_ascii=False, sort_keys=True))


# ---------------------------------------------------------------------------
# Controls: the safe behavior the PR claims. Each must hold.
# ---------------------------------------------------------------------------

def controls():
    # 1. no resolver: issue writes nothing, verify refuses.
    td = tempfile.TemporaryDirectory(prefix="qa2-s1-")
    d = a_daemon(Path(td.name), resolver=None)
    out = issue(d)
    record("no-resolver-issue-zero-grant", "LOCAL_SYNTHETIC_PEER",
           {"issued": False, "reason": "BLOCKED_OWNER_POLICY",
            "grants": {}},
           {"issued": out.get("issued"), "reason": out.get("reason"),
            "grants": d.control_store().grants()},
           ok=(out.get("issued") is False
               and out.get("reason") == "BLOCKED_OWNER_POLICY"
               and d.control_store().grants() == {}))
    policy = a_policy()
    td2, d2, ctx2, grant2, _ = world(policy)
    # Re-point the same state at a no-resolver daemon: verify must refuse.
    d2b = daemon_mod.Daemon(a_registry(), state_dir=Path(td2.name) / "state",
                            socket_path=Path(td2.name) / "run" / "b.sock",
                            log=lambda m: None, owner_policy_resolver=None)
    out = verify(d2b, context_ref=ctx2["context_ref"], grant_ref=grant2["grant_ref"])
    record("no-resolver-verify-refuses", "LOCAL_SYNTHETIC_PEER",
           {"verified": False, "reason": "BLOCKED_OWNER_POLICY"},
           {"verified": out.get("verified"), "reason": out.get("reason")},
           ok=(out.get("verified") is False
               and out.get("reason") == "BLOCKED_OWNER_POLICY"))
    td.cleanup(); td2.cleanup()

    # 2. valid policy: exactly the 10-field LeaseGrant + owner audit.
    td, d, ctx, grant, pol = world()
    out = verify(d, context_ref=ctx["context_ref"], grant_ref=grant["grant_ref"])
    fields = set(out.get("grant") or {})
    expected_fields = {"principal", "grant_ref", "project", "items", "actions",
                       "valid_from", "expires_at", "revoked", "epoch",
                       "os_principal"}
    record("valid-policy-10-field-dto", "LOCAL_SYNTHETIC_POLICY",
           {"verified": True, "grant_fields": sorted(expected_fields),
            "owner_policy_ref": pol.policy_ref},
           {"verified": out.get("verified"), "grant_fields": sorted(fields),
            "owner_policy_ref": (out.get("owner_policy") or {}).get("policy_ref")},
           ok=(out.get("verified") is True and fields == expected_fields
               and (out.get("owner_policy") or {}).get("policy_ref")
               == pol.policy_ref))
    td.cleanup()

    # 3. revoked=True, expired, not-yet-valid, stale epoch, wrong uid/actor,
    #    out-of-scope: the documented refusals.
    variants = [
        ("revoked-true", a_policy(revoked=True), "POLICY_DENIED"),
        ("expired", a_policy(expires_at="2000-01-01T00:00:00Z"),
         "POLICY_DENIED"),
        ("not-yet-valid", a_policy(valid_from="2999-01-01T00:00:00Z"),
         "POLICY_DENIED"),
        ("stale-epoch", a_policy(epoch=2), "BLOCKED_OWNER_POLICY"),
        ("wrong-uid", a_policy(os_principal="uid:99999"), "UNTRUSTED_ACTOR"),
        ("wrong-actor", a_policy(actor_ref="human:B"), "UNTRUSTED_ACTOR"),
        ("out-of-scope", a_policy(projects=("other",)), "POLICY_DENIED"),
        ("empty-issued-by", a_policy(issued_by=""), "BLOCKED_OWNER_POLICY"),
        ("bad-issued-at", a_policy(issued_at="not-a-time"),
         "BLOCKED_OWNER_POLICY"),
        ("epoch-zero", a_policy(epoch=0), "BLOCKED_OWNER_POLICY"),
        ("epoch-bool", a_policy(epoch=True), "BLOCKED_OWNER_POLICY"),
    ]
    for name, policy, reason in variants:
        if name == "stale-epoch":
            # The grant is issued under epoch 1; the live policy has moved to
            # epoch 2. Building both from `policy` would hide the move.
            base = a_policy()
            td, d, ctx, grant, _ = world(base)
            d._owner_policy_resolver = StaticResolver({policy.policy_ref: policy})
        else:
            td, d, ctx, grant, _ = world(policy)
        out = verify(d, context_ref=ctx["context_ref"],
                     grant_ref=grant["grant_ref"])
        record(f"policy-{name}", "LOCAL_SYNTHETIC_POLICY",
               {"verified": False, "reason": reason},
               {"verified": out.get("verified"), "reason": out.get("reason")},
               ok=(out.get("verified") is False
                   and out.get("reason") == reason))
        td.cleanup()

    # 4. grant-side binding mismatches.
    variants = [
        ("no-binding", dict(with_binding=False), "BLOCKED_OWNER_POLICY"),
        ("policy-ref-mismatch",
         dict(grant_over={"owner_policy_ref": "policy:other"}),
         "BLOCKED_OWNER_POLICY"),
        ("owner-epoch-mismatch",
         dict(grant_over={"owner_policy_epoch": 2}),
         "BLOCKED_OWNER_POLICY"),
        ("owner-epoch-missing",
         dict(grant_over={"owner_policy_epoch": None}),
         "BLOCKED_OWNER_POLICY"),
        ("owner-epoch-bool",
         dict(grant_over={"owner_policy_epoch": True}),
         "BLOCKED_OWNER_POLICY"),
    ]
    for name, kw, reason in variants:
        policy = a_policy()
        # A wrong-ref resolver is needed to reach the ref-mismatch branch.
        resolver = (WrongRefResolver(policy) if name == "policy-ref-mismatch"
                    else StaticResolver({policy.policy_ref: policy}))
        td, d, ctx, grant, _ = world(policy, resolver=resolver, **kw)
        out = verify(d, context_ref=ctx["context_ref"],
                     grant_ref=grant["grant_ref"])
        record(f"grant-{name}", "LOCAL_SYNTHETIC_POLICY",
               {"verified": False, "reason": reason},
               {"verified": out.get("verified"), "reason": out.get("reason")},
               ok=(out.get("verified") is False
                   and out.get("reason") == reason))
        td.cleanup()

    # 5. caller-supplied owner_policy_ref/actor_ref in the verify request are
    #    ignored: the stored binding and the stored context remain the source.
    strict = a_policy(policy_ref="policy:strict", projects=("other",))
    permissive = a_policy(policy_ref="policy:permissive")
    resolver = StaticResolver({"policy:strict": strict,
                               "policy:permissive": permissive})
    td, d, ctx, grant, _ = world(strict, resolver=resolver)
    out = verify(d, context_ref=ctx["context_ref"], grant_ref=grant["grant_ref"],
                 owner_policy_ref="policy:permissive")
    record("caller-owner-policy-ref-ignored", "LOCAL_SYNTHETIC_POLICY",
           {"verified": False, "reason": "POLICY_DENIED"},
           {"verified": out.get("verified"), "reason": out.get("reason")},
           ok=(out.get("verified") is False
               and out.get("reason") == "POLICY_DENIED"),
           note="the request names a permissive policy; the stored strict one "
                "must still govern")
    td.cleanup()
    td, d, ctx, grant, _ = world(a_policy(), resolver=StaticResolver(
        {"policy:test": a_policy()}))
    out = verify(d, context_ref=ctx["context_ref"], grant_ref=grant["grant_ref"],
                 actor_ref="human:B")
    record("caller-actor-ref-ignored", "LOCAL_SYNTHETIC_POLICY",
           {"verified": True},
           {"verified": out.get("verified"), "reason": out.get("reason")},
           ok=out.get("verified") is True,
           note="the request adds actor_ref=human:B; the stored context actor "
                "human:A must still be the one checked")
    td.cleanup()

    # 6. put_grant refuses each new owner-written field.
    td = tempfile.TemporaryDirectory(prefix="qa2-s1-")
    d = a_daemon(Path(td.name), resolver=StaticResolver({}))
    ctx = a_context(d)
    base = {
        "grant_ref": "grant:put",
        "delegated_by": ctx["context_ref"],
        "valid_from": iso(d, -60),
        "expires_at": iso(d, 600),
        "projects": [PROJECT], "items": [ITEM], "actions": [ACTION],
    }
    fields = ("owner_policy_ref", "owner_policy_epoch",
              "owner_policy_issued_by", "owner_policy_issued_at", "actor_ref")
    outcomes = {}
    for field in fields:
        try:
            d.control_store().put_grant({**base, field: "forged"})
            outcomes[field] = "STORED"
        except store_mod.StoreError as exc:
            outcomes[field] = str(exc).split(":", 1)[0]
    record("put-grant-refuses-owner-fields", "LOCAL_SYNTHETIC_POLICY",
           {f: "StoreError" for f in fields}, outcomes,
           ok=all(v != "STORED" for v in outcomes.values())
           and d.control_store().get_grant("grant:put") is None)
    td.cleanup()

    # 7. issue path: no resolver, no peer, resolver None/raises, and the
    #    documented mismatch refusals write nothing.
    def fake_verifier(approved, context):
        def _verify(evidence, *, now):
            return dict(approved), dict(context)
        return _verify

    def issue_case(name, *, policy, os_principal, resolver, approved, context,
                   expect_store_error=True):
        td = tempfile.TemporaryDirectory(prefix="qa2-s1-")
        d = a_daemon(Path(td.name), resolver=resolver)
        ctx = a_context(d)
        appr = dict(approved)
        appr["delegated_by"] = ctx["context_ref"]
        real = actor_mod.verify_delegation
        actor_mod.verify_delegation = fake_verifier(appr, ctx)
        try:
            try:
                rec = d.control_store().issue_grant(
                    {}, now=iso(d), os_principal=os_principal,
                    owner_policy_resolver=resolver)
                actual = {"issued": True,
                          "owner_policy_ref": rec.get("owner_policy_ref")}
                ok = not expect_store_error
            except store_mod.StoreError as exc:
                actual = {"store_error": str(exc).split(":", 1)[0]}
                ok = expect_store_error
        finally:
            actor_mod.verify_delegation = real
        stored = d.control_store().get_grant(appr["grant_ref"])
        if expect_store_error:
            ok = ok and stored is None
        record(name, "LOCAL_SYNTHETIC_POLICY",
               {"store_error": expect_store_error},
               {**actual, "stored": stored is not None},
               ok=ok)
        td.cleanup()

    base_approved = {
        "grant_ref": "grant:issued",
        "valid_from": None, "expires_at": None,
        "projects": [PROJECT], "items": [ITEM], "actions": [ACTION],
    }
    # use a daemon only to get a clock for the window
    td_clock = tempfile.TemporaryDirectory(prefix="qa2-s1-")
    clock = a_daemon(Path(td_clock.name), resolver=StaticResolver({}))
    base_approved["valid_from"] = iso(clock, -60)
    base_approved["expires_at"] = iso(clock, 600)
    td_clock.cleanup()

    policy = a_policy()
    issue_case("issue-valid", policy=policy, os_principal=f"uid:{os.getuid()}",
               resolver=StaticResolver({}, default=policy),
               approved=base_approved, context=None, expect_store_error=False)
    issue_case("issue-no-peer", policy=policy, os_principal=None,
               resolver=StaticResolver({}, default=policy),
               approved=base_approved, context=None)
    issue_case("issue-resolver-none", policy=policy,
               os_principal=f"uid:{os.getuid()}",
               resolver=StaticResolver({}), approved=base_approved, context=None)

    class Raising:
        def resolve(self, **_):
            raise RuntimeError("qa2 resolver failure")

    issue_case("issue-resolver-raises", policy=policy,
               os_principal=f"uid:{os.getuid()}", resolver=Raising(),
               approved=base_approved, context=None)

    issue_variants = [
        ("issue-revoked", a_policy(revoked=True)),
        ("issue-expired", a_policy(expires_at="2000-01-01T00:00:00Z")),
        ("issue-not-yet-valid", a_policy(valid_from="2999-01-01T00:00:00Z")),
        ("issue-wrong-uid", a_policy(os_principal="uid:99999")),
        ("issue-wrong-actor", a_policy(actor_ref="human:B")),
        ("issue-out-of-scope", a_policy(projects=("other",))),
        ("issue-epoch-zero", a_policy(epoch=0)),
        ("issue-epoch-bool", a_policy(epoch=True)),
        ("issue-empty-issued-by", a_policy(issued_by="")),
        ("issue-bad-issued-at", a_policy(issued_at="not-a-time")),
    ]
    for name, pol in issue_variants:
        issue_case(name, policy=pol, os_principal=f"uid:{os.getuid()}",
                   resolver=StaticResolver({}, default=pol),
                   approved=base_approved, context=None)


# ---------------------------------------------------------------------------
# Findings: adversarial witnesses. A confirmed gap prints here.
# ---------------------------------------------------------------------------

def findings():
    # F1. `revoked is True` recognizes only the exact bool. A policy source
    #     that yields 1 or "yes" is not treated as revoked.
    for value in (1, "yes"):
        td, d, ctx, grant, _ = world(a_policy(revoked=value))
        out = verify(d, context_ref=ctx["context_ref"],
                     grant_ref=grant["grant_ref"])
        record(f"f1-revoked-{type(value).__name__}", "LOCAL_SYNTHETIC_POLICY",
               {"verified": False, "reason": "POLICY_DENIED"},
               {"verified": out.get("verified"), "reason": out.get("reason")},
               ok=out.get("verified") is False,
               note="revoked is only recognized when it is exactly True",
               severity="P1")
        td.cleanup()

    # F2. issued_at in the future is never compared to now. An owner policy
    #     that has not been issued yet still authorizes.
    td, d, ctx, grant, _ = world(
        a_policy(issued_at="2999-01-01T00:00:00Z"))
    out = verify(d, context_ref=ctx["context_ref"], grant_ref=grant["grant_ref"])
    record("f2-policy-issued-in-future", "LOCAL_SYNTHETIC_POLICY",
           {"verified": False, "reason": "BLOCKED_OWNER_POLICY"},
           {"verified": out.get("verified"), "reason": out.get("reason")},
           ok=out.get("verified") is False,
           note="issued_at is parsed for shape but never ordered against now",
           severity="P2")
    td.cleanup()

    # F3. verify -> revoke -> return: the route reads the grant before the
    #     resolver, never re-reads revocation, and returns verified=True.
    policy = a_policy()
    td, d, ctx, grant, _ = world(policy)
    revoking = RevokingResolver(d.control_store(), grant["grant_ref"], policy)
    d._owner_policy_resolver = revoking
    out = verify(d, context_ref=ctx["context_ref"], grant_ref=grant["grant_ref"])
    stored = d.control_store().get_grant(grant["grant_ref"])
    record("f3-revoke-after-verify-before-return", "LOCAL_SYNTHETIC_POLICY",
           {"verified": False, "stored_revoked": True},
           {"verified": out.get("verified"), "reason": out.get("reason"),
            "stored_revoked": bool((stored or {}).get("revoked"))},
           ok=(out.get("verified") is False),
           note="a revoke that lands during resolver.resolve is invisible to "
                "the returned verdict; no atomic verify+commit seam is added",
           severity="P1")
    td.cleanup()

    # F4. issue_grant without a resolver stores owner fields present in
    #     `approved`. The real actor verifier's allowlist blocks them today;
    #     this witness bypasses only that verifier to show the store seam.
    td = tempfile.TemporaryDirectory(prefix="qa2-s1-")
    forgery = a_policy(policy_ref="policy:forged")
    d = a_daemon(Path(td.name), resolver=StaticResolver(
        {"policy:forged": forgery}))
    ctx = a_context(d)
    approved = {
        "grant_ref": "grant:forged",
        "delegated_by": ctx["context_ref"],
        "valid_from": iso(d, -60), "expires_at": iso(d, 600),
        "projects": [PROJECT], "items": [ITEM], "actions": [ACTION],
        "owner_policy_ref": "policy:forged",
        "owner_policy_epoch": 1,
        "owner_policy_issued_by": "owner:forged",
        "owner_policy_issued_at": "2026-01-01T00:00:00Z",
        "actor_ref": ctx["actor_ref"],
    }
    real = actor_mod.verify_delegation
    actor_mod.verify_delegation = (
        lambda evidence, *, now: (dict(approved), dict(ctx)))
    try:
        rec = d.control_store().issue_grant(
            {}, now=iso(d), os_principal=f"uid:{os.getuid()}",
            owner_policy_resolver=None)
        stored = d.control_store().get_grant("grant:forged")
    finally:
        actor_mod.verify_delegation = real
    out = verify(d, context_ref=ctx["context_ref"], grant_ref="grant:forged")
    record("f4-issue-without-resolver-stores-approved-owner-fields",
           "LOCAL_SYNTHETIC_VERIFIER",
           {"store_error": True, "owner_policy_ref": None},
           {"stored_owner_policy_ref": (stored or {}).get("owner_policy_ref"),
            "verified_after_forgery": out.get("verified")},
           ok=(stored or {}).get("owner_policy_ref") is None,
           note="issue_grant relies on verify_delegation's allowlist; it does "
                "not apply OWNER_WRITTEN_GRANT_FIELDS to `approved` itself. "
                "The real verifier does block this today (see s1_signed_probe).",
           severity="P2")
    td.cleanup()

    # F5. The idempotent readback path returns the existing grant BEFORE the
    #     owner policy is consulted. A policy revoked after issuance is not
    #     re-checked; the route still answers issued=True.
    td = tempfile.TemporaryDirectory(prefix="qa2-s1-")
    policy = a_policy()
    d = a_daemon(Path(td.name), resolver=StaticResolver({}, default=policy))
    ctx = a_context(d)
    approved = {
        "grant_ref": "grant:readback",
        "delegated_by": ctx["context_ref"],
        "valid_from": iso(d, -60), "expires_at": iso(d, 600),
        "projects": [PROJECT], "items": [ITEM], "actions": [ACTION],
    }
    evidence = {"payload": {"grant": {"grant_ref": "grant:readback"}}}
    real = actor_mod.verify_delegation
    actor_mod.verify_delegation = (
        lambda evidence, *, now: (dict(approved), dict(ctx)))
    try:
        first = issue(d, evidence=evidence)
        d._owner_policy_resolver = StaticResolver(
            {}, default=a_policy(revoked=True))
        second = issue(d, evidence=evidence)
        stored = d.control_store().get_grant("grant:readback")
    finally:
        actor_mod.verify_delegation = real
    record("f5-idempotent-issue-readback-skips-revoked-policy",
           "LOCAL_SYNTHETIC_VERIFIER",
           {"issued": False, "reason": "POLICY_DENIED"},
           {"first_issued": first.get("issued"),
            "second_issued": second.get("issued"),
            "second_reason": second.get("reason"),
            "stored_owner_policy_ref": (stored or {}).get("owner_policy_ref")},
           ok=(second.get("issued") is False),
           note="the existing-evidence readback runs before verify_delegation "
                "and before the owner resolver; a revoked policy is invisible "
                "and `issued: True` can be read as current authorization",
           severity="P2")
    td.cleanup()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("controls", "findings"), required=True)
    args = parser.parse_args()
    if args.mode == "controls":
        controls()
        failed = [r for r in RESULTS if not r["ok"]]
    else:
        findings()
        failed = [r for r in RESULTS if not r["ok"]]
    print(json.dumps({"mode": args.mode, "total": len(RESULTS),
                      "failed": len(failed),
                      "failed_names": [r["name"] for r in failed]},
                     ensure_ascii=False, sort_keys=True))
    if args.mode == "controls":
        return 1 if failed else 0
    # findings: rc 1 means the adversarial expectation was violated, i.e. a
    # gap is confirmed. rc 0 means the gate refused as it should.
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
