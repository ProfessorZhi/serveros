#!/usr/bin/env python3
"""S1 owner-policy/epoch -> lease commit atomicity probe.

This probe answers two different questions, with two different exit codes:

  --mode controls   every fail-closed control holds and NO lease is written.
                    rc=0 when all hold; rc=1 if a control fails.
  --mode positive   can a real owner-issued commit fence authorize a lease?
                    Today: no producer exists, so rc=2 with
                    BLOCKED_OWNER_ATOMIC_AUTHORITY and the missing dependencies.
                    rc=0 only if a real producer is found; rc=1 on a bypass.

It is deliberately NOT a synthetic COMMIT_FENCED producer. The owner policy,
context and grant are test fixtures; the daemon, store, adapter,
`OwnerVerifiedLease`, AF_UNIX socket and `SO_PEERCRED` are production code.

EVIDENCE: REAL_TEMP socket/peer + production daemon/store/adapter code;
synthetic owner facts; one uid (two-real-uid is BLOCKED_MULTIPRINCIPAL_NOT_RUN).
No production daemon, provider, credential, network or OS grant is touched.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import socket
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import owner_policy_fixtures as opf  # noqa: E402
from workengine import WorkItem  # noqa: E402
from workengine.authority import AuthorityError, OwnerVerifiedLease  # noqa: E402
from workengine.serveros_authority import (  # noqa: E402
    ServerOSLeaseAuthority,
    UnixSocketVerifyClient,
)
from workengine.store import ServerOSStore  # noqa: E402

ITEM = "SRV-0001"
PROJECT = "serveros"
ACTION = "lease.acquire"
RESOURCE = "work-write:SRV-0001"

_MODULES: dict[str, object] = {}


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


daemon_mod = _load("serveros_runtime_daemon", ROOT / "runtime" / "daemon.py")
registry_mod = _load("serveros_runtime_registry", ROOT / "runtime" / "registry.py")
actor_mod = _load("_serveros_context_actor", ROOT / "context" / "actor.py")

RESULTS: list[dict] = []


def record(name, level, expected, actual, ok, note=""):
    RESULTS.append({"name": name, "level": level, "expected": expected,
                    "actual": actual, "ok": bool(ok), "note": note})


def a_registry():
    return registry_mod.Registry(document={
        "schema": "serveros/managed-runtime-registry.v1",
        "device_ref": "device:server-beijing",
        "resources": [],
    })


def a_daemon(tmp: Path, resolver):
    return daemon_mod.Daemon(
        a_registry(), state_dir=tmp / "state",
        socket_path=tmp / "run" / "serveros.sock",
        log=lambda message: None, owner_policy_resolver=resolver)


def iso(daemon, offset_s: float = 0.0) -> str:
    return daemon_mod._iso(daemon._now() + offset_s)


def a_context(daemon) -> dict:
    now = iso(daemon)
    record = actor_mod.build({
        "human_control_provider": "provider:botmux-human-control",
        "transport": "feishu",
        "transport_identity": "botmux-verified-enterprise-user",
        "conversation_ref": "oc_owner_atomic_probe",
        "message_ref": "om_owner_atomic_probe",
        "observed_at": now,
        "provider_status": "VERIFIED",
        "provenance": "FACT",
        "actor_ref": "human:A",
        "provider_pid": 4242,
    }, now=now)
    daemon.control_store().put_context(record)
    return record


def a_bound_grant(daemon, context, policy, *, ref="grant:1") -> dict:
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


def a_work_store(tmp: Path) -> ServerOSStore:
    store = ServerOSStore(tmp / "workengine")
    store.register_project(PROJECT, "SRV")
    store.create_item(WorkItem(ITEM, PROJECT, "x", "test", "/card"))
    return store


@contextmanager
def serve_one(daemon):
    """One real AF_UNIX listener served by the production daemon."""
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


def call_socket(daemon, request) -> dict:
    with serve_one(daemon):
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.settimeout(5)
        try:
            connection.connect(str(daemon.socket_path))
            connection.sendall((json.dumps(request) + "\n").encode())
            buffer = b""
            while b"\n" not in buffer:
                chunk = connection.recv(65536)
                if not chunk:
                    break
                buffer += chunk
            return json.loads(buffer.split(b"\n", 1)[0].decode())
        finally:
            connection.close()


def verify_socket(daemon, params) -> dict:
    answer = call_socket(daemon, {
        "method": "agent.lease.authority.verify", "params": params})
    if answer.get("ok") is not True:
        return {"verified": False, "reason": (answer.get("error") or {}).get("code"),
                "because": (answer.get("error") or {}).get("message")}
    return answer["result"]


def params(context, grant) -> dict:
    return {"context_ref": context["context_ref"],
            "grant_ref": grant["grant_ref"],
            "action": ACTION, "project": PROJECT, "item_ref": ITEM}


class BlockingResolver:
    """The policy read blocks once, so a revoke can land mid-read."""

    def __init__(self, policy):
        self.policy = policy
        self.calls = 0
        self.entered = threading.Event()
        self.release = threading.Event()

    def resolve(self, **_):
        self.calls += 1
        if self.calls == 1:
            self.entered.set()
            self.release.wait(5)
        return self.policy


def _world(tmp: Path, resolver):
    daemon = a_daemon(tmp, resolver)
    context = a_context(daemon)
    if isinstance(resolver, opf.StaticOwnerPolicyResolver):
        policy = (resolver.policies.get("policy:test")
                  or resolver.default)
    else:
        policy = getattr(resolver, "policy", None)
    grant = a_bound_grant(daemon, context, policy)
    return daemon, context, grant, policy


def controls(tmp: Path) -> None:
    live = opf.StaticOwnerPolicyResolver({}, default=opf.a_policy(
        os_principal=f"uid:{os.getuid()}", actor_ref="human:A"))

    # 1. no resolver: issue writes nothing; verify refuses.
    bare = a_daemon(tmp / "no-resolver", None)
    bare_ctx = a_context(bare)
    bare_policy = opf.a_policy(os_principal=f"uid:{os.getuid()}",
                               actor_ref="human:A")
    bare_grant = a_bound_grant(bare, bare_ctx, bare_policy)
    out = verify_socket(bare, params(bare_ctx, bare_grant))
    record("no-resolver-verify", "REAL_TEMP",
           {"verified": False, "reason": "BLOCKED_OWNER_POLICY"},
           {"verified": out.get("verified"), "reason": out.get("reason")},
           out.get("verified") is False
           and out.get("reason") == "BLOCKED_OWNER_POLICY")
    issue_daemon = a_daemon(tmp / "no-resolver-issue", None)
    issue = call_socket(issue_daemon, {
        "method": "agent.grant.issue", "params": {"evidence": {}}})
    result = issue.get("result") or {}
    record("no-resolver-issue-zero-grant", "REAL_TEMP",
           {"issued": False, "reason": "BLOCKED_OWNER_POLICY", "grants": {}},
           {"issued": result.get("issued"), "reason": result.get("reason"),
            "grants": issue_daemon.control_store().grants()},
           result.get("issued") is False
           and result.get("reason") == "BLOCKED_OWNER_POLICY"
           and issue_daemon.control_store().grants() == {})

    # 2. owner live, no commit fence: no positive and no lease.
    daemon, context, grant, policy = _world(tmp / "owner-live", live)
    out = verify_socket(daemon, params(context, grant))
    record("owner-live-verify-blocks-commit-seam", "REAL_TEMP",
           {"verified": False, "reason": "BLOCKED_OWNER_COMMIT_SEAM",
            "grant": None},
           {"verified": out.get("verified"), "reason": out.get("reason"),
            "grant": out.get("grant")},
           out.get("verified") is False
           and out.get("reason") == "BLOCKED_OWNER_COMMIT_SEAM"
           and "grant" not in out)

    we = a_work_store(tmp / "owner-live")
    with serve_one(daemon):
        lease = OwnerVerifiedLease(
            we, ServerOSLeaseAuthority(
                UnixSocketVerifyClient(daemon.socket_path),
                grant_ref=grant["grant_ref"]))
        try:
            lease.acquire(context_ref=context["context_ref"], project=PROJECT,
                          item_ref=ITEM, ttl_s=30.0, now=time.time())
            refused, code = False, None
        except AuthorityError as exc:
            refused, code = True, exc.reason_code
    record("owner-live-lease-no-write", "REAL_TEMP",
           {"refused": True, "reason": "BLOCKED_OWNER_COMMIT_SEAM",
            "lease": None},
           {"refused": refused, "reason": code,
            "lease": we.active_lease(RESOURCE, now=time.time())},
           refused and code == "BLOCKED_OWNER_COMMIT_SEAM"
           and we.active_lease(RESOURCE, now=time.time()) is None)

    # 3. revoke during the resolver read: no lease.
    resolver = BlockingResolver(opf.a_policy(
        os_principal=f"uid:{os.getuid()}", actor_ref="human:A"))
    daemon, context, grant, _ = _world(tmp / "race", resolver)
    we = a_work_store(tmp / "race")
    outcome = {}

    def acquire():
        try:
            with serve_one(daemon):
                OwnerVerifiedLease(
                    we, ServerOSLeaseAuthority(
                        UnixSocketVerifyClient(daemon.socket_path),
                        grant_ref=grant["grant_ref"])
                ).acquire(context_ref=context["context_ref"], project=PROJECT,
                          item_ref=ITEM, ttl_s=30.0, now=time.time())
            outcome["refused"], outcome["reason"] = False, None
        except AuthorityError as exc:
            outcome["refused"], outcome["reason"] = True, exc.reason_code

    thread = threading.Thread(target=acquire, daemon=True)
    thread.start()
    entered = resolver.entered.wait(5)
    if entered:
        daemon.control_store().revoke_grant(
            grant["grant_ref"], at=iso(daemon), reason="probe race")
    resolver.release.set()
    thread.join(timeout=10)
    record("race-revoke-before-acquire-no-lease", "REAL_TEMP",
           {"entered": True, "refused": True, "lease": None},
           {"entered": entered, "refused": outcome.get("refused"),
            "reason": outcome.get("reason"),
            "lease": we.active_lease(RESOURCE, now=time.time())},
           entered and outcome.get("refused") is True
           and we.active_lease(RESOURCE, now=time.time()) is None)


def positive(tmp: Path) -> dict:
    daemon, context, grant, _policy = _world(
        tmp / "positive",
        opf.StaticOwnerPolicyResolver({}, default=opf.a_policy(
            os_principal=f"uid:{os.getuid()}", actor_ref="human:A")))
    methods = tuple(daemon._methods())
    fence_methods = [m for m in methods
                     if "commit" in m or "fence" in m
                     or "atomic" in m]
    probe = call_socket(daemon, {
        "method": "agent.lease.authority.commit", "params": {}})
    code = (probe.get("error") or {}).get("code")
    out = verify_socket(daemon, params(context, grant))
    return {
        "status": "BLOCKED_OWNER_ATOMIC_AUTHORITY",
        "commit_fence_methods": fence_methods,
        "hypothetical_commit_method": code,
        "owner_live_answer": {"verified": out.get("verified"),
                              "reason": out.get("reason"),
                              "grant_present": "grant" in out},
        "missing": [
            "D1 owner-issued policy/epoch authority (durable, authenticated "
            "issuance/revocation)",
            "D2 lease commit in the same serializable transaction as the "
            "authority-epoch check (or durable two-phase commit)",
            "D3 a COMMIT_FENCED producer with a policy/grant/uid/actor/scope "
            "and epoch commit receipt",
            "D4 a second real OS uid test environment "
            "(BLOCKED_MULTIPRINCIPAL_NOT_RUN)",
            "D5 owner approval to bind a production resolver/fence",
        ],
        "positive_authority": "NOT_RUN",
        "note": ("No synthetic COMMIT_FENCED, HMAC, legacy readback or "
                 "skipped check is used to manufacture this positive."),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("controls", "positive", "all"),
                        default="all")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="s1-owner-atomic-") as td:
        tmp = Path(td)
        controls(tmp)
        report = {"mode": args.mode, "controls": RESULTS,
                  "failed": sum(1 for r in RESULTS if not r["ok"])}
        if args.mode in ("positive", "all"):
            report["positive"] = positive(tmp)
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    if args.mode == "controls":
        return 0 if report["failed"] == 0 else 1
    if args.mode == "positive":
        return 0 if report["positive"].get("commit_fence_methods") else 2
    if report["failed"]:
        return 1
    return 0 if report["positive"].get("commit_fence_methods") else 2


if __name__ == "__main__":
    raise SystemExit(main())
