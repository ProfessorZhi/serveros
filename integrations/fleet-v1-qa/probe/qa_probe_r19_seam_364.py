#!/usr/bin/env python3
"""Independent GREEN re-test of the S1 commit-fence fix (#364).

Same real chain as the RED probe:
  real Daemon AF_UNIX + synthetic owner resolver + ServerOSAuthority
  UnixSocketVerifyClient/ServerOSLeaseAuthority + OwnerVerifiedLease.acquire
  + temp WorkEngine ServerOSStore.

Required GREEN (no real atomic fence exists):
  * an owner-backed positive read must be refused, not written as a lease;
  * revoke-after-last-read must produce zero new leases;
  * adapter must refuse owner markers unless atomicity == COMMIT_FENCED;
  * legacy/no-resolver and no-authority controls stay fail-closed.

No product source, production owner, credential, provider or send.
"""
from __future__ import annotations

import dataclasses
import importlib.util
import os
import socket
import sys
import tempfile
import threading
from pathlib import Path

TREE = Path(os.environ["QA_TREE"]).resolve()
sys.path.insert(0, str(TREE))
FAIL = TOTAL = 0


def check(name, cond, detail=""):
    global FAIL, TOTAL
    TOTAL += 1
    if cond:
        print("PASS", name)
    else:
        FAIL += 1
        print("FAIL", name, detail)


def load(name, path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


actor_mod = load("_serveros_context_actor", TREE / "context" / "actor.py")
registry_mod = load("serveros_runtime_registry", TREE / "runtime" / "registry.py")
daemon_mod = load("serveros_runtime_daemon", TREE / "runtime" / "daemon.py")
owner_mod = sys.modules["serveros_runtime_owner_policy"]

import workengine  # noqa: E402
from workengine.authority import AuthorityError, OwnerVerifiedLease  # noqa: E402
from workengine.serveros_authority import (  # noqa: E402
    ServerOSLeaseAuthority, UnixSocketVerifyClient)
from workengine.store import ServerOSStore  # noqa: E402

ITEM, PROJECT, ACTION = "SRV-0001", "serveros", "lease.acquire"


def registry():
    return registry_mod.Registry(document={
        "schema": "serveros/managed-runtime-registry.v1",
        "device_ref": "device:q1-seam-364",
        "resources": [],
    })


class MutableResolver:
    def __init__(self, policy):
        self.policy = policy

    def resolve(self, **kwargs):
        return self.policy


def make_world(root, *, resolver=True):
    daemon = daemon_mod.Daemon(
        registry(), state_dir=root / "agent-state",
        socket_path=root / "run" / "serveros.sock",
        log=lambda message: None,
        owner_policy_resolver=MutableResolver(None) if resolver else None)
    now = daemon_mod._iso(daemon._now())
    context = actor_mod.build({
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
    daemon.control_store().put_context(context)
    policy = owner_mod.OwnerPolicy(
        policy_ref="policy:q1-seam", os_principal=f"uid:{os.getuid()}",
        actor_ref="human:A", projects=(PROJECT,), items=(ITEM,),
        actions=(ACTION,), valid_from=daemon_mod._iso(daemon._now() - 60),
        expires_at=daemon_mod._iso(daemon._now() + 600), revoked=False,
        epoch=1, issued_by="owner:q1-seam",
        issued_at=daemon_mod._iso(daemon._now() - 30))
    if resolver:
        daemon._owner_policy_resolver.policy = policy
    store = daemon.control_store()
    grant = {
        "grant_ref": "grant:q1-seam", "delegated_by": context["context_ref"],
        "valid_from": policy.valid_from, "expires_at": policy.expires_at,
        "projects": [PROJECT], "items": [ITEM], "actions": [ACTION],
        "revoked": False, "epoch": store._next_grant_epoch(),
        "os_principal": f"uid:{os.getuid()}", "actor_ref": "human:A",
        "owner_policy_ref": policy.policy_ref,
        "owner_policy_epoch": policy.epoch,
        "owner_policy_issued_by": policy.issued_by,
        "owner_policy_issued_at": policy.issued_at,
    }
    with store._mutation():
        store._data["grants"][grant["grant_ref"]] = grant
        store._flush()
    return daemon, context, grant, policy


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


class Recorder(UnixSocketVerifyClient):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.last = None

    def verify(self, context_ref, *, grant_ref, action, project, item_ref):
        self.last = super().verify(context_ref, grant_ref=grant_ref,
                                   action=action, project=project,
                                   item_ref=item_ref)
        return self.last


class Revoker(Recorder):
    def __init__(self, *args, resolver, policy, **kwargs):
        super().__init__(*args, **kwargs)
        self.resolver, self.policy = resolver, policy

    def verify(self, context_ref, **kwargs):
        answer = super().verify(context_ref, **kwargs)
        self.resolver.policy = dataclasses.replace(self.policy, revoked=True)
        return answer


def leases(store):
    with store.connection() as conn:
        return conn.execute("SELECT COUNT(*) FROM leases").fetchone()[0]


def acquire(root, *, revoke=False):
    daemon, context, grant, policy = make_world(root)
    server, thread, path = open_server(daemon)
    store = ServerOSStore(root / "work")
    try:
        client = (Revoker(path, resolver=daemon._owner_policy_resolver,
                          policy=policy) if revoke else Recorder(path))
        authority = ServerOSLeaseAuthority(client, grant_ref=grant["grant_ref"])
        error = result = None
        try:
            result = OwnerVerifiedLease(store, authority).acquire(
                context_ref=context["context_ref"], project=PROJECT,
                item_ref=ITEM, ttl_s=60)
        except Exception as exc:  # noqa: BLE001
            error = exc
        return client.last, result, error, leases(store)
    finally:
        server.close()
        thread.join(timeout=5)
        try:
            path.unlink()
        except OSError:
            pass


def main():
    root = Path(tempfile.mkdtemp(prefix="q1-seam-364-"))

    answer, result, error, count = acquire(root / "positive")
    check("positive/no-lease", count == 0 and result is None,
          f"leases={count} result={result} error={error!r}")
    check("positive/refused-commit-seam",
          isinstance(error, AuthorityError) and
          error.reason_code == "BLOCKED_OWNER_COMMIT_SEAM",
          f"error={error!r} answer={answer!r}")

    answer, result, error, count = acquire(root / "race", revoke=True)
    check("race/no-lease", count == 0 and result is None,
          f"leases={count} result={result} error={error!r}")
    check("race/refused-commit-seam",
          isinstance(error, AuthorityError) and
          error.reason_code == "BLOCKED_OWNER_COMMIT_SEAM",
          f"error={error!r} answer={answer!r}")

    # Adapter contract: an owner marker without COMMIT_FENCED is refused.
    grant = {
        "principal": "human:A", "grant_ref": "grant:q1-seam",
        "project": PROJECT, "items": [ITEM], "actions": [ACTION],
        "valid_from": "2026-10-20T00:00:00Z",
        "expires_at": "2999-01-01T00:00:00Z", "revoked": False,
        "epoch": 1, "os_principal": f"uid:{os.getuid()}",
    }

    class Fake:
        def __init__(self, answer):
            self.answer = answer

        def verify(self, *args, **kwargs):
            return dict(self.answer)

    stale = {"verified": True, "atomicity": "BLOCKED_OWNER_COMMIT_SEAM",
             "point_in_time": True, "owner_policy": {}, "grant": grant}
    try:
        ServerOSLeaseAuthority(Fake(stale), grant_ref="grant:q1-seam").verify(
            "ctx", action=ACTION, project=PROJECT, item_ref=ITEM, now=0)
        refused = False
    except AuthorityError as exc:
        refused = exc.reason_code == "BLOCKED_OWNER_COMMIT_SEAM"
    check("adapter/refuses-unfenced", refused)

    fenced = {"verified": True, "atomicity": "COMMIT_FENCED",
              "point_in_time": True, "owner_policy": {}, "grant": grant}
    try:
        lease = ServerOSLeaseAuthority(Fake(fenced),
                                       grant_ref="grant:q1-seam").verify(
            "ctx", action=ACTION, project=PROJECT, item_ref=ITEM, now=0)
        accepted = lease.grant_ref == "grant:q1-seam"
    except Exception:  # noqa: BLE001
        accepted = False
    check("adapter/accepts-explicit-commit-fence", accepted)

    # No resolver and no trusted authority remain fail-closed.
    daemon, context, grant2, _ = make_world(root / "nores", resolver=False)
    server, thread, path = open_server(daemon)
    store = ServerOSStore(root / "nores" / "work")
    try:
        authority = ServerOSLeaseAuthority(
            UnixSocketVerifyClient(path), grant_ref=grant2["grant_ref"])
        try:
            OwnerVerifiedLease(store, authority).acquire(
                context_ref=context["context_ref"], project=PROJECT,
                item_ref=ITEM, ttl_s=60)
            blocked = False
            reason = ""
        except AuthorityError as exc:
            blocked = True
            reason = exc.reason_code
        check("noresolver/refused", blocked and reason == "BLOCKED_OWNER_POLICY",
              reason)
        check("noresolver/no-lease", leases(store) == 0, str(leases(store)))
    finally:
        server.close()
        thread.join(timeout=5)
        try:
            path.unlink()
        except OSError:
            pass

    store = ServerOSStore(root / "noauthority")
    try:
        OwnerVerifiedLease(store, None).acquire(
            context_ref="ctx", project=PROJECT, item_ref=ITEM, ttl_s=60)
        blocked, reason = False, ""
    except AuthorityError as exc:
        blocked, reason = True, exc.reason_code
    check("noauthority/refused", blocked and reason == "BLOCKED_TRUST_SOURCE",
          reason)
    check("noauthority/no-lease", leases(store) == 0)

    print(f"TOTAL {TOTAL} FAIL {FAIL}")
    print("GREEN" if not FAIL else "RED")
    raise SystemExit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
