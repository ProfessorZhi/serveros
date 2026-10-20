"""The owner verdict must not become a lease write without a commit fence.

WHY THIS FILE EXISTS
--------------------
#363 returns the owner-backed verify answer with `verified: true` and a
`BLOCKED_OWNER_COMMIT_SEAM` label. The real consumer
(`workengine/serveros_authority.py`) only reads `verified`, builds a
`LeaseGrant`, and `OwnerVerifiedLease.acquire` writes the lease. A revoke that
lands after the daemon's last policy read but before that write is therefore
invisible to the writer.

The tests below exercise the real chain:

  real temp Daemon (production runtime/daemon.py) + real temp AF_UNIX socket
  + kernel SO_PEERCRED
  -> UnixSocketVerifyClient
  -> ServerOSLeaseAuthority
  -> OwnerVerifiedLease
  -> real temp ServerOSStore

The owner policy, trusted context and grant are TEST FIXTURES; there is no
production owner and no production resolver. EVIDENCE: REAL_TEMP socket/peer
and production store/daemon code, synthetic owner facts.
"""
from __future__ import annotations

import os

import pytest

# POSIX-ONLY, and before any POSIX import: this module loads
# `runtime/daemon.py` at module scope, which imports `fcntl`; the tests use
# AF_UNIX, `SO_PEERCRED` and `uid`. On Windows the module cannot be collected
# at all. Only `os.name == "nt"` is skipped; Linux keeps every assertion below.
if os.name == "nt":
    pytest.skip(
        "POSIX_ONLY: runtime/daemon.py imports fcntl and these tests use "
        "AF_UNIX/SO_PEERCRED/uid; this module cannot be collected on Windows",
        allow_module_level=True)

import importlib.util
import json
import socket
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import owner_policy_fixtures as opf
from workengine import WorkItem
from workengine.authority import AuthorityError, OwnerVerifiedLease
from workengine.serveros_authority import (
    ServerOSLeaseAuthority,
    UnixSocketVerifyClient,
)
from workengine.store import ServerOSStore

REPO = Path(__file__).resolve().parent.parent
ITEM = "SRV-0001"
PROJECT = "serveros"
ACTION = "lease.acquire"
RESOURCE = "work-write:SRV-0001"


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


def _iso(daemon, offset_s: float = 0.0) -> str:
    return daemon_mod._iso(daemon._now() + offset_s)


def _daemon(tmp_path, resolver):
    return daemon_mod.Daemon(
        registry_mod.Registry(document={
            "schema": "serveros/managed-runtime-registry.v1",
            "device_ref": "device:server-beijing",
            "resources": [],
        }),
        state_dir=tmp_path / "state",
        socket_path=tmp_path / "run" / "serveros.sock",
        log=lambda message: None,
        owner_policy_resolver=resolver,
    )


def _context(daemon) -> dict:
    now = _iso(daemon)
    record = actor_mod.build({
        "human_control_provider": "provider:botmux-human-control",
        "transport": "feishu",
        "transport_identity": "botmux-verified-enterprise-user",
        "conversation_ref": "oc_owner_commit_fence",
        "message_ref": "om_owner_commit_fence",
        "observed_at": now,
        "provider_status": "VERIFIED",
        "provenance": "FACT",
        "actor_ref": "human:A",
        "provider_pid": 4242,
    }, now=now)
    daemon.control_store().put_context(record)
    return record


def _bound_grant(daemon, context, policy, *, ref="grant:1") -> dict:
    store = daemon.control_store()
    record = {
        "grant_ref": ref,
        "delegated_by": context["context_ref"],
        "valid_from": _iso(daemon, -60),
        "expires_at": _iso(daemon, 600),
        "projects": [PROJECT],
        "items": [ITEM],
        "actions": [ACTION],
        "revoked": False,
        "epoch": store._next_grant_epoch(),
        "os_principal": f"uid:{os.getuid()}",
        "actor_ref": context["actor_ref"],
        "owner_policy_ref": policy.policy_ref,
        "owner_policy_epoch": policy.epoch,
        "owner_policy_issued_by": policy.issued_by,
        "owner_policy_issued_at": policy.issued_at,
    }
    with store._mutation():
        store._data["grants"][ref] = record
        store._flush()
    return record


@contextmanager
def _serve_one(daemon):
    """One real AF_UNIX connection served by the production daemon."""
    path = daemon.socket_path
    path.parent.mkdir(parents=True, exist_ok=True)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.settimeout(5)
    server.bind(str(path))
    server.listen(1)

    def serve_once():
        try:
            connection, _ = server.accept()
        except OSError:
            return
        try:
            daemon._serve_connection(connection)
        finally:
            connection.close()

    thread = threading.Thread(target=serve_once, daemon=True)
    thread.start()
    try:
        yield path
    finally:
        thread.join(timeout=5)
        server.close()
        try:
            path.unlink()
        except OSError:
            pass


def _verify_over_socket(daemon, params) -> dict:
    with _serve_one(daemon):
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.settimeout(5)
        try:
            connection.connect(str(daemon.socket_path))
            connection.sendall((json.dumps({
                "method": "agent.lease.authority.verify",
                "params": params,
            }) + "\n").encode())
            buffer = b""
            while b"\n" not in buffer:
                chunk = connection.recv(65536)
                if not chunk:
                    break
                buffer += chunk
            return json.loads(buffer.split(b"\n", 1)[0].decode())["result"]
        finally:
            connection.close()


class _RevokeAfterRead:
    """A client that revokes the owner policy after the daemon answered.

    This is the exact seam the review names: the daemon has finished its last
    resolver read, and the mutating `OwnerVerifiedLease.acquire` has not yet
    written. The answer it returns is therefore no longer current.
    """

    def __init__(self, client, resolver, policy_ref, revoked):
        self._client = client
        self._resolver = resolver
        self._policy_ref = policy_ref
        self._revoked = revoked

    def verify(self, context_ref, *, grant_ref, action, project, item_ref):
        answer = self._client.verify(
            context_ref, grant_ref=grant_ref, action=action,
            project=project, item_ref=item_ref)
        self._resolver.policies[self._policy_ref] = self._revoked
        return answer


def _revoked(policy):
    return opf.a_policy(policy_ref=policy.policy_ref,
                        os_principal=policy.os_principal,
                        actor_ref=policy.actor_ref, revoked=True)


def _world(tmp_path):
    policy = opf.a_policy(os_principal=f"uid:{os.getuid()}",
                          actor_ref="human:A")
    resolver = opf.StaticOwnerPolicyResolver({policy.policy_ref: policy})
    daemon = _daemon(tmp_path, resolver)
    context = _context(daemon)
    grant = _bound_grant(daemon, context, policy)
    store = ServerOSStore(tmp_path / "workengine")
    store.register_project(PROJECT, "SRV")
    store.create_item(WorkItem(ITEM, PROJECT, "x", "test", "/card"))
    return policy, resolver, daemon, context, grant, store


def _params(context, grant) -> dict:
    return {"context_ref": context["context_ref"],
            "grant_ref": grant["grant_ref"],
            "action": ACTION, "project": PROJECT, "item_ref": ITEM}


def test_the_owner_route_never_returns_verified_without_a_commit_fence(tmp_path):
    policy, _resolver, daemon, context, grant, _store = _world(tmp_path)
    answer = _verify_over_socket(daemon, _params(context, grant))
    assert answer["verified"] is False, answer
    assert answer["reason"] == "BLOCKED_OWNER_COMMIT_SEAM", answer
    assert answer.get("point_in_time") is True, answer
    assert (answer.get("owner_policy") or {}).get("policy_ref") \
        == policy.policy_ref
    assert "grant" not in answer, (
        "a refused mutation-eligibility answer must not carry a positive grant")


def test_a_revoke_after_the_last_read_cannot_acquire_a_lease(tmp_path):
    """The review's controlled race: no commit fence, no lease."""
    policy, resolver, daemon, context, grant, store = _world(tmp_path)
    with _serve_one(daemon):
        client = _RevokeAfterRead(
            UnixSocketVerifyClient(daemon.socket_path), resolver,
            policy.policy_ref, _revoked(policy))
        lease = OwnerVerifiedLease(
            store, ServerOSLeaseAuthority(client,
                                          grant_ref=grant["grant_ref"]))
        with pytest.raises(AuthorityError) as caught:
            lease.acquire(context_ref=context["context_ref"],
                          project=PROJECT, item_ref=ITEM, ttl_s=30.0,
                          now=time.time())
    assert caught.value.reason_code == "BLOCKED_OWNER_COMMIT_SEAM", \
        str(caught.value)
    assert store.active_lease(RESOURCE, now=time.time()) is None, \
        "a lease was written from an owner verdict that is no longer current"
