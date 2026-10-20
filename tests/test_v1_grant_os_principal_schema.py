#!/usr/bin/env python3
"""The verify read must be a Work Engine #14 `LeaseGrant`, from a bound OS peer.

TWO FAILURES FROM INDEPENDENT QA SNAPSHOT N (#256 @ 1222c74) ON #272
-------------------------------------------------------------------
P0 — the positive `grant` carried only
`{grant_ref, principal, os_principal, epoch, valid_from, expires_at, revoked}`:
no `project`/`items`/`actions`, and the times were ISO STRINGS. Work Engine #14's
`LeaseGrant` requires all ten fields with floating-point times, so
`LeaseGrant(**grant)` raised TypeError and `float(<ISO>)` failed. The read was
fail-closed but not consumable.

P1 — `verify_live_grant` read `peer.uid` and only COPIED it into
`os_principal`; it never compared it with anything. The same bearer context and
grant verified for uid 4242 AND uid 9999. A bearer `context_ref` is not OS
principal authorization.

WHAT THIS FILE PINS
-------------------
* the returned grant constructs the real `LeaseGrant` dataclass (shape + types);
* strict UTC ISO -> epoch float, and an unreadable stamp refuses;
* the grant is bound to the OS principal at the trusted issuance boundary and the
  verify compares `uid:<n>` — a different uid, or no binding at all, refuses;
* revoked / old epoch / wrong scope still refuse.

LOCAL only: the peer is supplied to `handle(peer=…)`, the seam the Unix socket
fills from `SO_PEERCRED`. That is not REAL_DEVICE evidence.

X2 conformance at rc/i1-v2-review-20261020 @ `ec43f714`: the REAL public
in-tree `workengine.authority.LeaseGrant` (#304) is imported and instantiated
from the daemon's verified wire. The old owner-private class is labeled
`HISTORICAL_PRIVATE_SOURCE_UNVERIFIED`; it is not read, extracted or copied.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
RUNTIME = REPO / "runtime"
ITEM = "SRV-0001"
PROJECT = "serveros"
ACTION = "lease.acquire"

sys.path.insert(0, str(Path(__file__).resolve().parent / "v1_portability"))
import v1_portability_harness as H  # noqa: E402

pytestmark = pytest.mark.skipif(
    sys.platform != "linux",
    reason="X2 evidence scope: Linux (Tokyo / Dell fjd) only")


def _load_at(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load(name: str, filename: str):
    return _load_at(name, RUNTIME / filename)


daemon_mod = _load("serveros_runtime_daemon", "daemon.py")
registry_mod = _load("serveros_runtime_registry", "registry.py")
actor_mod = _load_at("serveros_context_actor", REPO / "context" / "actor.py")


def a_registry():
    return registry_mod.Registry(document={
        "schema": "serveros/managed-runtime-registry.v1",
        "device_ref": "device:server-beijing",
        "resources": [],
    })


@pytest.fixture
def daemon(tmp_path):
    return daemon_mod.Daemon(
        a_registry(), state_dir=tmp_path / "state",
        socket_path=tmp_path / "run/serveros.sock", log=lambda message: None)


def iso(daemon, offset_s: float = 0.0) -> str:
    return daemon_mod._iso(daemon._now() + offset_s)


def a_context(daemon, *, actor="human:A", pid=4242) -> dict:
    now = iso(daemon)
    record = actor_mod.build({
        "human_control_provider": "provider:botmux-human-control",
        "transport": "feishu",
        "transport_identity": "botmux-verified-enterprise-user",
        "conversation_ref": "oc_grant_test",
        "message_ref": "om_grant_test",
        "observed_at": now,
        "provider_status": "VERIFIED",
        "provenance": "FACT",
        "actor_ref": actor,
        "provider_pid": pid,
    }, now=now)
    daemon.control_store().put_context(record)
    return record


def a_bound_grant(daemon, context, *, ref="grant:1", uid=None,
                  projects=(PROJECT,), items=(ITEM,), actions=(ACTION,),
                  expires_in=600, valid_from_offset=-60, os_principal="auto"):
    """Simulate a grant written at the TRUSTED boundary with a peer binding.

    Production binds this at `agent.grant.issue` from `SO_PEERCRED`; the signed
    evidence that path needs is not available to this test, so the fixture
    writes the same record the boundary would. `os_principal=None` omits the
    binding, which is the legacy/unbound case.
    """
    store = daemon.control_store()
    record = {
        "grant_ref": ref,
        "delegated_by": context["context_ref"],
        "valid_from": iso(daemon, valid_from_offset),
        "expires_at": iso(daemon, expires_in),
        "projects": list(projects),
        "items": list(items),
        "actions": list(actions),
        "revoked": False,
        "epoch": 1,
    }
    bound = (f"uid:{os.getuid() if uid is None else uid}"
             if os_principal == "auto" else os_principal)
    if bound is not None:
        record["os_principal"] = bound
    with store._mutation():
        store._data["grants"][ref] = record
        store._flush()
    return record


def peer(*, uid=None):
    return {"pid": 9001, "uid": os.getuid() if uid is None else uid,
            "gid": os.getgid()}


def verify(daemon, *, context_ref, grant_ref="grant:1", action=ACTION,
           project=PROJECT, item_ref=ITEM, expected_epoch=None, peer_value="default"):
    params = {"context_ref": context_ref, "grant_ref": grant_ref,
              "action": action, "project": project, "item_ref": item_ref}
    if expected_epoch is not None:
        params["expected_epoch"] = expected_epoch
    return daemon.handle(
        {"method": "agent.lease.authority.verify", "params": params},
        peer=peer() if peer_value == "default" else peer_value)["result"]


# ---------------------------------------------------------------------------
# P0 — the shape Work Engine #14 consumes.
# ---------------------------------------------------------------------------

LEASE_GRANT_FIELDS = ("principal", "grant_ref", "project", "items", "actions",
                      "valid_from", "expires_at", "revoked", "epoch",
                      "os_principal")


def test_the_positive_result_is_the_full_lease_grant_shape(daemon):
    ctx = a_context(daemon)
    a_bound_grant(daemon, ctx)
    answer = verify(daemon, context_ref=ctx["context_ref"])
    assert answer["verified"] is True
    grant = answer["grant"]
    assert set(grant) == set(LEASE_GRANT_FIELDS), sorted(grant)
    assert grant["principal"] == "human:A"
    assert grant["project"] == PROJECT
    assert grant["items"] == (ITEM,)
    assert grant["actions"] == (ACTION,)
    assert isinstance(grant["valid_from"], float)
    assert isinstance(grant["expires_at"], float)
    assert isinstance(grant["epoch"], int) and grant["revoked"] is False
    assert grant["os_principal"] == f"uid:{os.getuid()}"


def test_the_positive_result_matches_the_pinned_public_contract(daemon):
    """The returned grant is checked against a PINNED public fixture.

    The fixture is a contract description (field names and wire types), not a
    copy of any private class definition. It proves the daemon's answer is the
    shape a Work Engine #14 consumer expects without reading another user's
    checkout.
    """
    contract = H.lease_grant_contract()
    fields = contract["fields"]
    assert set(fields) == set(LEASE_GRANT_FIELDS)

    ctx = a_context(daemon)
    a_bound_grant(daemon, ctx)
    grant = verify(daemon, context_ref=ctx["context_ref"])["grant"]
    assert set(grant) == set(fields), sorted(grant)

    for name, kind in fields.items():
        value = grant[name]
        if kind == "string":
            assert isinstance(value, str) and value, (name, value)
        elif kind == "array[string]":
            assert isinstance(value, (list, tuple)), (name, value)
            assert all(isinstance(item, str) for item in value), (name, value)
        elif kind == "number":
            assert isinstance(value, float), (name, value)
        elif kind == "integer":
            assert isinstance(value, int) and not isinstance(value, bool)
        elif kind == "boolean":
            assert isinstance(value, bool), (name, value)
        else:  # pragma: no cover - a fixture typo must be loud
            raise AssertionError(f"unknown contract type {kind!r} for {name}")
    assert grant["items"] == (ITEM,)
    assert grant["os_principal"] == f"uid:{os.getuid()}"


def test_the_real_public_work_engine_lease_grant_accepts_the_verified_wire(daemon):
    """Instantiate the REAL public in-tree `LeaseGrant` from the daemon's answer.

    RC-v2 (`ec43f714`) ships `workengine/authority.py` (#304), so this no longer
    needs an owner-private checkout. The class definition is imported from the
    public tree; only the daemon's verified wire is an input.
    """
    authority = H.in_tree_authority()
    assert authority is not None, (
        "rc/i1-v2-review-20261020 must ship public workengine/authority.py")
    assert set(authority.LeaseGrant.__dataclass_fields__) == set(
        LEASE_GRANT_FIELDS)

    ctx = a_context(daemon)
    a_bound_grant(daemon, ctx)
    grant = verify(daemon, context_ref=ctx["context_ref"])["grant"]
    built = authority.LeaseGrant(**grant)      # the TypeError the QA measured
    assert built.principal == "human:A"
    assert built.grant_ref == "grant:1"
    assert built.project == PROJECT
    assert built.items == (ITEM,)
    assert built.actions == (ACTION,)
    assert built.valid_from == grant["valid_from"]
    assert built.expires_at == grant["expires_at"]
    assert built.revoked is False
    assert built.epoch == grant["epoch"]
    assert built.os_principal == f"uid:{os.getuid()}"


def test_the_public_authority_module_exposes_the_exact_seams():
    """The positive T10/grant work depends on these public names existing."""
    authority = H.in_tree_authority()
    assert authority is not None
    for name in ("LeaseGrant", "ReceiptError", "T10Binding",
                 "VerifiedRunReceipt", "OwnerVerifiedLease"):
        assert hasattr(authority, name), name
    assert authority.ReceiptError.__name__ == "ReceiptError"


def test_an_unreadable_time_refuses(daemon):
    ctx = a_context(daemon)
    a_bound_grant(daemon, ctx)
    store = daemon.control_store()
    with store._mutation():
        store._data["grants"]["grant:1"]["valid_from"] = "not-a-time"
        store._flush()
    answer = verify(daemon, context_ref=ctx["context_ref"])
    assert answer["verified"] is False
    assert answer["reason"] == "INVALID_CONTEXT"
    assert "time" in answer["because"]


def test_a_naive_time_without_utc_offset_refuses(daemon):
    ctx = a_context(daemon)
    a_bound_grant(daemon, ctx)
    store = daemon.control_store()
    with store._mutation():
        store._data["grants"]["grant:1"]["expires_at"] = "2026-10-18T12:00:00"
        store._flush()
    answer = verify(daemon, context_ref=ctx["context_ref"])
    assert answer["verified"] is False
    assert answer["reason"] == "INVALID_CONTEXT"


# ---------------------------------------------------------------------------
# P1 — the OS principal is BOUND at issuance, COMPARED at verify.
# ---------------------------------------------------------------------------

def test_a_different_uid_is_refused(daemon):
    ctx = a_context(daemon)
    a_bound_grant(daemon, ctx, uid=4242)
    denied = verify(daemon, context_ref=ctx["context_ref"], peer_value=peer(uid=9999))
    assert denied["verified"] is False
    assert denied["reason"] == "UNTRUSTED_ACTOR"
    assert "os_principal" in denied["because"]


def test_the_bound_uid_is_admitted(daemon):
    ctx = a_context(daemon)
    a_bound_grant(daemon, ctx, uid=4242)
    allowed = verify(daemon, context_ref=ctx["context_ref"], peer_value=peer(uid=4242))
    assert allowed["verified"] is True


def test_a_grant_with_no_os_principal_binding_is_refused(daemon):
    ctx = a_context(daemon)
    a_bound_grant(daemon, ctx, os_principal=None)
    answer = verify(daemon, context_ref=ctx["context_ref"])
    assert answer["verified"] is False
    assert answer["reason"] == "UNTRUSTED_ACTOR"
    assert "os_principal" in answer["because"]


def test_an_absent_peer_is_refused(daemon):
    ctx = a_context(daemon)
    a_bound_grant(daemon, ctx)
    answer = verify(daemon, context_ref=ctx["context_ref"], peer_value=None)
    assert answer["verified"] is False
    assert answer["reason"] == "UNTRUSTED_ACTOR"


def test_a_malformed_peer_uid_refuses_cleanly(daemon):
    ctx = a_context(daemon)
    a_bound_grant(daemon, ctx)
    answer = verify(daemon, context_ref=ctx["context_ref"],
                    peer_value={"pid": 1, "uid": "not-a-uid", "gid": 1})
    assert answer["verified"] is False
    assert answer["reason"] == "UNTRUSTED_ACTOR"


# ---------------------------------------------------------------------------
# Preserved refusals.
# ---------------------------------------------------------------------------

def test_revoked_and_old_epoch_still_refuse(daemon):
    ctx = a_context(daemon)
    a_bound_grant(daemon, ctx, uid=os.getuid())
    denied = verify(daemon, context_ref=ctx["context_ref"], expected_epoch=0)
    assert denied["reason"] == "INVALID_CONTEXT"
    daemon.control_store().revoke_grant("grant:1", at=iso(daemon), reason="withdrawn")
    denied = verify(daemon, context_ref=ctx["context_ref"])
    assert denied["reason"] == "POLICY_DENIED"


def test_wrong_scope_still_refuses(daemon):
    ctx = a_context(daemon)
    a_bound_grant(daemon, ctx, uid=os.getuid())
    assert verify(daemon, context_ref=ctx["context_ref"],
                  item_ref="SRV-9999")["reason"] == "POLICY_DENIED"
    assert verify(daemon, context_ref=ctx["context_ref"],
                  project="another")["reason"] == "POLICY_DENIED"
    assert verify(daemon, context_ref=ctx["context_ref"],
                  action="lease.release")["reason"] == "POLICY_DENIED"
