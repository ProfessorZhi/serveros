#!/usr/bin/env python3
"""Independent critical-seam probe for S1 #363: point-in-time verify -> lease.

Uses the real daemon AF_UNIX route, the real ServerOSAuthority
UnixSocketVerifyClient/ServerOSLeaseAuthority, a real OwnerVerifiedLease and a
temp WorkEngine ServerOSStore. The owner resolver is a LOCAL synthetic policy
(no production owner, no credential, no provider, no send).

Scenario:
  1. positive control: a live policy acquires one lease (the harness works);
  2. race: after the daemon's last policy read/response, revoke the policy in
     the resolver; the client must NOT accept the stale LeaseGrant and must
     write ZERO new leases. If it writes one, the probe reports RED.
  3. no owner resolver -> BLOCKED_OWNER_POLICY, zero leases;
  4. no trusted authority -> BLOCKED_TRUST_SOURCE, zero leases.

This is a QA-only artifact; it does not modify product source.
"""
from __future__ import annotations

import dataclasses
import importlib.util
import json
import os
import socket
import sys
import tempfile
import threading
from pathlib import Path

TREE = Path(os.environ["QA_TREE"]).resolve()
sys.path.insert(0, str(TREE))
sys.path.insert(0, str(TREE / "tests"))

FAIL = 0
TOTAL = 0


def check(name, cond, detail=""):
    global FAIL, TOTAL
    TOTAL += 1
    if cond:
        print("PASS", name)
    else:
        FAIL += 1
        print("FAIL", name, detail)


def load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# Load the daemon's own module names so its isinstance checks use these classes.
actor_mod = load("_serveros_context_actor", TREE / "context" / "actor.py")
registry_mod = load("serveros_runtime_registry", TREE / "runtime" / "registry.py")
daemon_mod = load("serveros_runtime_daemon", TREE / "runtime" / "daemon.py")
owner_mod = sys.modules["serveros_runtime_owner_policy"]

import workengine  # noqa: E402
from workengine.authority import AuthorityError, OwnerVerifiedLease  # noqa: E402
from workengine.serveros_authority import (  # noqa: E402
    ServerOSLeaseAuthority, UnixSocketVerifyClient)
from workengine.store import ServerOSStore  # noqa: E402

ITEM = "SRV-0001"
PROJECT = "serveros"
ACTION = "lease.acquire"


def a_registry():
    return registry_mod.Registry(document={
        "schema": "serveros/managed-runtime-registry.v1",
        "device_ref": "device:q1-seam",
        "resources": [],
    })


class MutableResolver:
    """LOCAL synthetic owner source; revocable between daemon read and commit."""

    def __init__(self, policy):
        self.policy = policy

    def resolve(self, *, policy_ref=None, grant=None, context=None, peer=None,
                project=None, item_ref=None, action=None, now=None):
        return self.policy


def make_world(root: Path, *, resolver=True):
    daemon = daemon_mod.Daemon(
        a_registry(), state_dir=root / "agent-state",
        socket_path=root / "run" / "serveros.sock",
        log=lambda message: None,
        owner_policy_resolver=MutableResolver(None) if resolver else None)
    now = daemon_mod._iso(daemon._now())
    record = actor_mod.build({
        "human_control_provider": "provider:q1-seam",
        "transport": "local",
        "transport_identity": "q1-seam-fixture",
        "conversation_ref": "oc_q1_seam",
        "message_ref": "om_q1_seam",
        "observed_at": now,
        "provider_status": "VERIFIED",
        "provenance": "FACT",
        "actor_ref": "human:A",
        "provider_pid": os.getpid(),
    }, now=now)
    daemon.control_store().put_context(record)
    policy = owner_mod.OwnerPolicy(
        policy_ref="policy:q1-seam",
        os_principal=f"uid:{os.getuid()}",
        actor_ref="human:A",
        projects=(PROJECT,),
        items=(ITEM,),
        actions=(ACTION,),
        valid_from=daemon_mod._iso(daemon._now() - 60),
        expires_at=daemon_mod._iso(daemon._now() + 600),
        revoked=False,
        epoch=1,
        issued_by="owner:q1-seam",
        issued_at=daemon_mod._iso(daemon._now() - 30),
    )
    if resolver:
        daemon._owner_policy_resolver.policy = policy
    store = daemon.control_store()
    grant = {
        "grant_ref": "grant:q1-seam",
        "delegated_by": record["context_ref"],
        "valid_from": policy.valid_from,
        "expires_at": policy.expires_at,
        "projects": [PROJECT],
        "items": [ITEM],
        "actions": [ACTION],
        "revoked": False,
        "epoch": store._next_grant_epoch(),
        "os_principal": f"uid:{os.getuid()}",
        "actor_ref": "human:A",
        "owner_policy_ref": policy.policy_ref,
        "owner_policy_epoch": policy.epoch,
        "owner_policy_issued_by": policy.issued_by,
        "owner_policy_issued_at": policy.issued_at,
    }
    with store._mutation():
        store._data["grants"][grant["grant_ref"]] = grant
        store._flush()
    return daemon, record, grant, policy


def serve_once(daemon, server):
    try:
        connection, _ = server.accept()
    except OSError:
        return
    try:
        daemon._serve_connection(connection)
    finally:
        connection.close()


def open_server(daemon):
    path = daemon.socket_path
    path.parent.mkdir(parents=True, exist_ok=True)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.settimeout(5)
    server.bind(str(path))
    server.listen(1)
    thread = threading.Thread(target=serve_once, args=(daemon, server), daemon=True)
    thread.start()
    return server, thread, path


class RecordingClient(UnixSocketVerifyClient):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.last = None

    def verify(self, context_ref, *, grant_ref, action, project, item_ref):
        self.last = super().verify(context_ref, grant_ref=grant_ref,
                                   action=action, project=project,
                                   item_ref=item_ref)
        return self.last


class RevokingClient(UnixSocketVerifyClient):
    """After the daemon's answer, revoke the policy and return the stale answer."""

    def __init__(self, *args, resolver, policy, **kwargs):
        super().__init__(*args, **kwargs)
        self.resolver = resolver
        self.policy = policy
        self.last = None

    def verify(self, context_ref, *, grant_ref, action, project, item_ref):
        answer = super().verify(context_ref, grant_ref=grant_ref, action=action,
                                project=project, item_ref=item_ref)
        self.last = answer
        self.resolver.policy = dataclasses.replace(self.policy, revoked=True)
        return answer


def lease_count(store):
    with store.connection() as conn:
        return conn.execute("SELECT COUNT(*) FROM leases").fetchone()[0]


def run_positive(root: Path):
    daemon, context, grant, policy = make_world(root)
    server, thread, path = open_server(daemon)
    store = ServerOSStore(root / "work")
    try:
        client = RecordingClient(path)
        authority = ServerOSLeaseAuthority(client, grant_ref=grant["grant_ref"])
        owner = OwnerVerifiedLease(store, authority)
        result = owner.acquire(context_ref=context["context_ref"],
                               project=PROJECT, item_ref=ITEM, ttl_s=60)
        return client.last, result, lease_count(store)
    finally:
        server.close()
        thread.join(timeout=5)
        try:
            path.unlink()
        except OSError:
            pass


def run_race(root: Path):
    daemon, context, grant, policy = make_world(root)
    server, thread, path = open_server(daemon)
    store = ServerOSStore(root / "work")
    try:
        client = RevokingClient(path, resolver=daemon._owner_policy_resolver,
                                policy=policy)
        authority = ServerOSLeaseAuthority(client, grant_ref=grant["grant_ref"])
        owner = OwnerVerifiedLease(store, authority)
        error = None
        result = None
        try:
            result = owner.acquire(context_ref=context["context_ref"],
                                   project=PROJECT, item_ref=ITEM, ttl_s=60)
        except Exception as exc:  # noqa: BLE001 - record the refusal
            error = exc
        return client.last, result, error, lease_count(store)
    finally:
        server.close()
        thread.join(timeout=5)
        try:
            path.unlink()
        except OSError:
            pass


def run_no_resolver(root: Path):
    daemon, context, grant, _ = make_world(root, resolver=False)
    server, thread, path = open_server(daemon)
    store = ServerOSStore(root / "work")
    try:
        client = UnixSocketVerifyClient(path)
        authority = ServerOSLeaseAuthority(client, grant_ref=grant["grant_ref"])
        owner = OwnerVerifiedLease(store, authority)
        error = None
        try:
            owner.acquire(context_ref=context["context_ref"], project=PROJECT,
                          item_ref=ITEM, ttl_s=60)
        except Exception as exc:  # noqa: BLE001
            error = exc
        return error, lease_count(store)
    finally:
        server.close()
        thread.join(timeout=5)
        try:
            path.unlink()
        except OSError:
            pass


def main():
    root = Path(tempfile.mkdtemp(prefix="q1-seam-"))

    answer, result, leases = run_positive(root / "positive")
    check("positive/verified", answer.get("verified") is True, repr(answer))
    check("positive/lease-written", leases == 1 and result is not None,
          f"leases={leases} result={result}")

    answer, result, error, leases = run_race(root / "race")
    check("race/daemon-said-point-in-time",
          answer.get("verified") is True and
          answer.get("atomicity") == "BLOCKED_OWNER_COMMIT_SEAM",
          repr(answer))
    check("race/no-new-lease", leases == 0 and result is None,
          f"leases={leases} result={result} error={error!r} "
          f"atomicity={answer.get('atomicity') if answer else None}")

    error, leases = run_no_resolver(root / "nores")
    check("noresolver/refused",
          isinstance(error, AuthorityError) and
          getattr(error, "reason_code", "") == "BLOCKED_OWNER_POLICY",
          repr(error))
    check("noresolver/no-lease", leases == 0, f"leases={leases}")

    store = ServerOSStore(root / "noauthority")
    try:
        OwnerVerifiedLease(store, None).acquire(
            context_ref="ctx:whatever", project=PROJECT, item_ref=ITEM,
            ttl_s=60)
        blocked = False
        reason = ""
    except AuthorityError as exc:
        blocked = True
        reason = exc.reason_code
    check("noauthority/refused-blocked-trust-source",
          blocked and reason == "BLOCKED_TRUST_SOURCE", reason)
    check("noauthority/no-lease", lease_count(store) == 0)

    print(f"TOTAL {TOTAL} FAIL {FAIL}")
    print("RED_CONFIRMED" if FAIL else "ALL_GREEN")
    raise SystemExit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
