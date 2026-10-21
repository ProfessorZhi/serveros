"""The owner-policy gate at the two real daemon boundaries (import-minimal).

This file deliberately does not import `runtime/owner_policy.py`, so it can be
run against the PRE-FIX base (RC-v6 `8620a90`) to show the RED: the base's
verify route admits a live grant with no owner binding, and its issue route
reaches the store with no owner policy.

On the fixed branch, a daemon with no owner resolver refuses both
`BLOCKED_OWNER_POLICY` and writes no grant.

EVIDENCE: LOCAL. The peer is supplied to `handle(peer=...)`, the same seam the
Unix socket fills from `SO_PEERCRED`; the real-socket positive is in
`tests/test_owner_policy_binding.py` (REAL_TEMP).
"""
from __future__ import annotations

import os

import pytest

# POSIX-ONLY, and before any POSIX import: this module loads
# `runtime/daemon.py` at module scope, which imports `fcntl`; the tests use
# uid/peer semantics. On Windows the module cannot be collected at all. Only
# `os.name == "nt"` is skipped; Linux keeps every assertion below.
if os.name == "nt":
    pytest.skip(
        "POSIX_ONLY: runtime/daemon.py imports fcntl and these tests use "
        "uid/peer semantics; this module cannot be collected on Windows",
        allow_module_level=True)

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
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


daemon_mod = _load("serveros_runtime_daemon", REPO / "runtime" / "daemon.py")
registry_mod = _load("serveros_runtime_registry", REPO / "runtime" / "registry.py")
actor_mod = _load("_serveros_context_actor", REPO / "context" / "actor.py")


def _registry():
    return registry_mod.Registry(document={
        "schema": "serveros/managed-runtime-registry.v1",
        "device_ref": "device:server-beijing",
        "resources": [],
    })


def _daemon(tmp_path):
    # No `owner_policy_resolver` argument: the base constructor does not have
    # it, and the production default on the fixed branch is None.
    return daemon_mod.Daemon(
        _registry(), state_dir=tmp_path / "state",
        socket_path=tmp_path / "run/serveros.sock", log=lambda message: None)


def _peer(uid=None):
    return {"pid": os.getpid(), "uid": os.getuid() if uid is None else uid,
            "gid": os.getgid()}


def _context(daemon):
    now = daemon_mod._iso(daemon._now())
    record = actor_mod.build({
        "human_control_provider": "provider:botmux-human-control",
        "transport": "feishu",
        "transport_identity": "botmux-verified-enterprise-user",
        "conversation_ref": "oc_reduced_gate",
        "message_ref": "om_reduced_gate",
        "observed_at": now,
        "provider_status": "VERIFIED",
        "provenance": "FACT",
        "actor_ref": "human:A",
        "provider_pid": 4242,
    }, now=now)
    daemon.control_store().put_context(record)
    return record


def _grant(daemon, context):
    store = daemon.control_store()
    now = daemon_mod._iso(daemon._now())
    record = {
        "grant_ref": "grant:1",
        "delegated_by": context["context_ref"],
        "valid_from": daemon_mod._iso(daemon._now() - 60),
        "expires_at": daemon_mod._iso(daemon._now() + 600),
        "projects": [PROJECT], "items": [ITEM], "actions": [ACTION],
        "revoked": False, "epoch": 1,
        "os_principal": f"uid:{os.getuid()}",
        "actor_ref": "human:A",
        # The owner binding a trusted issue path writes. The pre-fix base
        # ignores these fields; the fixed branch refuses without a resolver.
        "owner_policy_ref": "policy:test",
        "owner_policy_epoch": 1,
        "owner_policy_issued_by": "owner:test",
        "owner_policy_issued_at": now,
    }
    with store._mutation():
        store._data["grants"]["grant:1"] = record
        store._flush()
    return record


def test_the_verify_route_refuses_without_an_owner_resolver(tmp_path):
    daemon = _daemon(tmp_path)
    context = _context(daemon)
    _grant(daemon, context)
    answer = daemon.handle(
        {"method": "agent.lease.authority.verify",
         "params": {"context_ref": context["context_ref"],
                    "grant_ref": "grant:1", "action": ACTION,
                    "project": PROJECT, "item_ref": ITEM}},
        peer=_peer())["result"]
    assert answer["verified"] is False, (
        "the pre-fix base admitted a live grant with no owner-to-uid binding")
    assert answer["reason"] == "BLOCKED_OWNER_POLICY"


def test_the_issue_route_refuses_without_an_owner_resolver(tmp_path):
    daemon = _daemon(tmp_path)
    answer = daemon.handle(
        {"method": "agent.grant.issue", "params": {"evidence": {}}},
        peer=_peer())
    result = answer.get("result") or {}
    assert result.get("issued") is False, (
        "the pre-fix base reached the grant store with no owner policy")
    assert result.get("reason") == "BLOCKED_OWNER_POLICY"
    assert daemon.control_store().grants() == {}
