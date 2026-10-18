#!/usr/bin/env python3
"""Trusted-parent grant authority: a durable monotonic epoch, read fail-closed.

WHY THIS EXISTS
---------------
Work Engine #14's `OwnerVerifiedLease` requires a `LeaseAuthority.verify` that
resolves `context_ref` out of band, re-reads the grant's `revoked` / `expires_at`
/ `epoch` at `now`, and binds the actor to an OS principal. serveros HAS the
context store, the kernel `SO_PEERCRED` peer, and live grants, but grants carried
NO epoch and there was no verification read.

This adds exactly that one missing original-authority piece: an `epoch` minted by
the store (durable, monotonic, never caller-supplied, backfilled as UNKNOWN for
records written before it existed) and a verification read at the REAL daemon
entrypoint that refuses when the identity, the context, the grant or the epoch
cannot be proved. It registers NO lease verb and accepts NO caller-supplied
grant.

LOCAL only: the peer is supplied to `handle(peer=…)`, the seam the Unix socket
fills from `SO_PEERCRED`. That is not REAL_DEVICE evidence.
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


def a_grant(daemon, context, *, ref="grant:1", projects=(PROJECT,),
            items=(ITEM,), actions=(ACTION,), expires_in=600,
            valid_from_offset=-60) -> dict:
    return daemon.control_store().put_grant({
        "grant_ref": ref,
        "delegated_by": context["context_ref"],
        "valid_from": iso(daemon, valid_from_offset),
        "expires_at": iso(daemon, expires_in),
        "projects": list(projects),
        "items": list(items),
        "actions": list(actions),
    })


def a_legacy_grant(daemon, context, *, ref="grant:legacy") -> dict:
    """A record written before the epoch existed: UNKNOWN, never backfilled."""
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
    }
    with store._mutation():
        store._data["grants"][ref] = record
        store._flush()
    return record


def peer(*, pid=9001, uid=None, gid=None) -> dict:
    return {"pid": pid, "uid": os.getuid() if uid is None else uid,
            "gid": os.getgid() if gid is None else gid}


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
# The positive path.
# ---------------------------------------------------------------------------

def test_a_live_grant_with_a_matching_epoch_verifies(daemon):
    ctx = a_context(daemon)
    grant = a_grant(daemon, ctx)
    assert isinstance(grant["epoch"], int) and grant["epoch"] >= 1

    answer = verify(daemon, context_ref=ctx["context_ref"],
                    expected_epoch=grant["epoch"])
    assert answer["verified"] is True
    assert answer["grant"]["principal"] == "human:A"
    assert answer["grant"]["epoch"] == grant["epoch"]
    assert answer["grant"]["os_principal"] == f"uid:{os.getuid()}"


# ---------------------------------------------------------------------------
# Missing/unknown epoch — backfilled as UNKNOWN, never minted retroactively.
# ---------------------------------------------------------------------------

def test_a_grant_without_an_epoch_is_unknown_and_refused(daemon):
    ctx = a_context(daemon)
    a_legacy_grant(daemon, ctx)
    answer = verify(daemon, context_ref=ctx["context_ref"], grant_ref="grant:legacy")
    assert answer["verified"] is False
    assert answer["reason"] == "INVALID_CONTEXT"
    assert "epoch" in answer["because"]


def test_an_old_epoch_is_refused(daemon):
    ctx = a_context(daemon)
    grant = a_grant(daemon, ctx)
    answer = verify(daemon, context_ref=ctx["context_ref"],
                    expected_epoch=grant["epoch"] - 1)
    assert answer["verified"] is False
    assert answer["reason"] == "INVALID_CONTEXT"


# ---------------------------------------------------------------------------
# Revoked / expired grants refuse before anything is mutated.
# ---------------------------------------------------------------------------

def test_a_revoked_grant_is_refused(daemon):
    ctx = a_context(daemon)
    a_grant(daemon, ctx)
    daemon.control_store().revoke_grant("grant:1", at=iso(daemon), reason="withdrawn")
    answer = verify(daemon, context_ref=ctx["context_ref"])
    assert answer["verified"] is False
    assert answer["reason"] == "POLICY_DENIED"


def test_an_expired_grant_is_refused(daemon):
    ctx = a_context(daemon)
    a_grant(daemon, ctx, expires_in=-60, valid_from_offset=-600)
    answer = verify(daemon, context_ref=ctx["context_ref"])
    assert answer["verified"] is False
    assert answer["reason"] == "POLICY_DENIED"


# ---------------------------------------------------------------------------
# Identity: B cannot claim A; wrong Item/project; absent peer.
# ---------------------------------------------------------------------------

def test_principal_b_cannot_claim_principal_a(daemon):
    a = a_context(daemon, actor="human:A")
    b = a_context(daemon, actor="human:B")
    a_grant(daemon, a)
    answer = verify(daemon, context_ref=b["context_ref"])
    assert answer["verified"] is False
    assert answer["reason"] == "UNTRUSTED_ACTOR"


def test_a_wrong_item_is_refused(daemon):
    ctx = a_context(daemon)
    a_grant(daemon, ctx)
    answer = verify(daemon, context_ref=ctx["context_ref"], item_ref="SRV-9999")
    assert answer["verified"] is False
    assert answer["reason"] == "POLICY_DENIED"


def test_a_wrong_project_is_refused(daemon):
    ctx = a_context(daemon)
    a_grant(daemon, ctx)
    answer = verify(daemon, context_ref=ctx["context_ref"], project="another")
    assert answer["verified"] is False
    assert answer["reason"] == "POLICY_DENIED"


def test_an_absent_peer_is_refused(daemon):
    ctx = a_context(daemon)
    a_grant(daemon, ctx)
    answer = verify(daemon, context_ref=ctx["context_ref"], peer_value=None)
    assert answer["verified"] is False
    assert answer["reason"] == "UNTRUSTED_ACTOR"
    assert "peer" in answer["because"]


def test_an_unknown_context_is_refused(daemon):
    answer = verify(daemon, context_ref="ctx:invented:deadbeef")
    assert answer["verified"] is False
    assert answer["reason"] == "UNTRUSTED_ACTOR"


# ---------------------------------------------------------------------------
# The epoch is durable and monotonic; the read mutates no lease.
# ---------------------------------------------------------------------------

def test_the_epoch_is_monotonic_across_grants(daemon):
    ctx = a_context(daemon)
    first = a_grant(daemon, ctx, ref="grant:1")
    second = a_grant(daemon, ctx, ref="grant:2")
    assert second["epoch"] > first["epoch"]


def test_the_epoch_survives_a_restart(tmp_path):
    d1 = daemon_mod.Daemon(a_registry(), state_dir=tmp_path / "state",
                           socket_path=tmp_path / "run/a.sock", log=lambda m: None)
    ctx = a_context(d1)
    grant = a_grant(d1, ctx)
    d2 = daemon_mod.Daemon(a_registry(), state_dir=tmp_path / "state",
                           socket_path=tmp_path / "run/b.sock", log=lambda m: None)
    assert d2.control_store().get_grant("grant:1")["epoch"] == grant["epoch"]


def test_the_read_mutates_no_lease_and_no_grant(daemon, tmp_path):
    import sqlite3
    ctx = a_context(daemon)
    grant = a_grant(daemon, ctx)
    before = dict(daemon.control_store().grants())
    verify(daemon, context_ref=ctx["context_ref"], expected_epoch=grant["epoch"])
    assert daemon.control_store().grants() == before
    # No `leases` table was created by a read either.
    assert not (tmp_path / "serveros.db").exists()
