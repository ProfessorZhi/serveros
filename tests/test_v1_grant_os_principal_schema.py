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
"""
from __future__ import annotations

import dataclasses
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
RUNTIME = REPO / "runtime"
ITEM = "SRV-0001"
PROJECT = "serveros"
ACTION = "lease.acquire"
WE_REPO = Path("/data/home/vince/workspace/src/work-engine")
WE14 = "e4c4a2b2b3fb6fb7a0af564fa30d8bc5eea3043a"


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


def test_the_result_constructs_the_real_work_engine_lease_grant(daemon, tmp_path):
    """Instantiate WE #14's OWN dataclass from the returned value.

    The dataclass is read out of the Work Engine repository at the exact commit
    (read-only `git show`), not restated here: a copy of the field list would
    agree with itself and prove nothing. `workengine.authority` imports only
    `LeaseError` from its package, so the sibling module is stubbed to let the
    REAL class definition load without dragging the whole package in.
    """
    if not (WE_REPO / ".git").exists():
        pytest.skip("the Work Engine checkout is not present on this host")
    source = subprocess.run(
        ["git", "-C", str(WE_REPO), "show", f"{WE14}:workengine/authority.py"],
        check=True, capture_output=True, text=True).stdout
    package = tmp_path / "we14" / "workengine"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "store.py").write_text("class LeaseError(Exception):\n    pass\n",
                                      encoding="utf-8")
    (package / "authority.py").write_text(source, encoding="utf-8")
    sys.path.insert(0, str(package.parent))
    try:
        module = importlib.import_module("workengine.authority")
        assert module.LeaseGrant.__dataclass_fields__.keys() == set(LEASE_GRANT_FIELDS)
        ctx = a_context(daemon)
        a_bound_grant(daemon, ctx)
        grant = verify(daemon, context_ref=ctx["context_ref"])["grant"]
        built = module.LeaseGrant(**grant)      # the TypeError the QA measured
        assert built.valid_from == grant["valid_from"]
        assert built.items == (ITEM,)
        assert built.os_principal == f"uid:{os.getuid()}"
        assert built.epoch == grant["epoch"]
    finally:
        sys.path.remove(str(package.parent))


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
