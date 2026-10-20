#!/usr/bin/env python3
"""Q1 independent probe for #237 B3/T7 on PR #272 (no product edits).

Against the REAL in-process Daemon over a temp state dir:

  1. a live grant with a matching epoch verifies;
  2. the peer's OS uid is NOT compared to anything: a *different* uid still
     verifies (the read asserts an OS principal, it does not authorize one);
  3. caller-supplied identity fields (`actor`, `os_user`) are ignored;
  4. revoked / epoch-less / wrong-delegator grants refuse;
  5. a malformed expected_epoch does not become a positive verification;
  6. the read mutates no grant.

Run: QA_TREE=<candidate worktree> python3 qa_probe_q1_272.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(os.environ["QA_TREE"]).resolve()
RUNTIME = REPO / "runtime"


def load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


daemon_mod = load("serveros_runtime_daemon", RUNTIME / "daemon.py")
registry_mod = load("serveros_runtime_registry", RUNTIME / "registry.py")
actor_mod = load("serveros_context_actor", REPO / "context" / "actor.py")

ITEM = "SRV-0001"
PROJECT = "serveros"
ACTION = "lease.acquire"
results = []


def check(name, condition, detail=""):
    results.append((name, bool(condition), detail))
    print(f"{'PASS' if condition else 'FAIL'} {name}"
          + (f" :: {detail}" if detail else ""))


def build(tmp: Path):
    registry = registry_mod.Registry(document={
        "schema": "serveros/managed-runtime-registry.v1",
        "device_ref": "device:tokyo-qa",
        "resources": [],
    })
    return daemon_mod.Daemon(
        registry, state_dir=tmp / "state", socket_path=tmp / "run/serveros.sock",
        log=lambda message: None)


def iso(daemon, offset_s=0.0):
    return daemon_mod._iso(daemon._now() + offset_s)


def make_context(daemon, actor="human:A"):
    now = iso(daemon)
    record = actor_mod.build({
        "human_control_provider": "provider:botmux-human-control",
        "transport": "feishu",
        "transport_identity": "botmux-verified-enterprise-user",
        "conversation_ref": "oc_q1_probe",
        "message_ref": "om_q1_probe",
        "observed_at": now,
        "provider_status": "VERIFIED",
        "provenance": "FACT",
        "actor_ref": actor,
        "provider_pid": 4242,
    }, now=now)
    daemon.control_store().put_context(record)
    return record


def make_grant(daemon, context, *, ref="grant:q1", epoch=None, delegated_by=None):
    record = {
        "grant_ref": ref,
        "delegated_by": delegated_by or context["context_ref"],
        "valid_from": iso(daemon, -60),
        "expires_at": iso(daemon, 600),
        "projects": [PROJECT],
        "items": [ITEM],
        "actions": [ACTION],
    }
    if epoch is not None:
        record["epoch"] = epoch
    return daemon.control_store().put_grant(record)


def verify(daemon, context_ref, *, peer, grant_ref="grant:q1",
           expected_epoch=None, extra=None):
    params = {"context_ref": context_ref, "grant_ref": grant_ref,
              "action": ACTION, "project": PROJECT, "item_ref": ITEM}
    if expected_epoch is not None:
        params["expected_epoch"] = expected_epoch
    if extra:
        params.update(extra)
    answer = daemon.handle(
        {"method": "agent.lease.authority.verify", "params": params}, peer=peer)
    return answer


with tempfile.TemporaryDirectory() as td:
    daemon = build(Path(td))
    ctx = make_context(daemon)
    grant = make_grant(daemon, ctx)
    own = {"pid": os.getpid(), "uid": os.getuid(), "gid": os.getgid()}

    # 1. Positive control.
    answer = verify(daemon, ctx["context_ref"], peer=own,
                    expected_epoch=grant["epoch"])
    result = answer.get("result") or {}
    check("live grant + matching epoch verifies",
          result.get("verified") is True, repr(result)[:300])

    # 2. A DIFFERENT kernel uid is not compared to the context. The read
    #    reports uid:<peer> and still verifies: SO_PEERCRED is asserted, not
    #    authorized against the grant/context principal.
    foreign = {"pid": 1, "uid": 0 if os.getuid() != 0 else 65534,
               "gid": 0}
    answer = verify(daemon, ctx["context_ref"], peer=foreign,
                    expected_epoch=grant["epoch"])
    foreign_result = answer.get("result") or {}
    check("a foreign peer uid still verifies (OS principal not authorized)",
          foreign_result.get("verified") is True,
          f"peer={foreign} verified={foreign_result.get('verified')} "
          f"os_principal={((foreign_result.get('grant') or {}).get('os_principal'))}")

    # 3. Caller-supplied identity fields are ignored.
    answer = verify(daemon, ctx["context_ref"], peer=own,
                    expected_epoch=grant["epoch"],
                    extra={"actor": "human:FORGED", "os_user": "root",
                           "principal": "human:FORGED"})
    forged_result = answer.get("result") or {}
    check("caller identity fields are ignored",
          (forged_result.get("grant") or {}).get("principal") == "human:A",
          repr((forged_result.get("grant") or {}).get("principal")))

    # 4a. Absent epoch on a legacy record.
    legacy_ctx = make_context(daemon, actor="human:LEGACY")
    store = daemon.control_store()
    with store._mutation():
        store._data["grants"]["grant:legacy"] = {
            "grant_ref": "grant:legacy",
            "delegated_by": legacy_ctx["context_ref"],
            "valid_from": iso(daemon, -60), "expires_at": iso(daemon, 600),
            "projects": [PROJECT], "items": [ITEM], "actions": [ACTION],
            "revoked": False,
        }
        store._flush()
    answer = verify(daemon, legacy_ctx["context_ref"], peer=own,
                    grant_ref="grant:legacy")
    legacy_result = answer.get("result") or {}
    check("epoch-less grant is refused",
          legacy_result.get("verified") is False
          and legacy_result.get("reason") == "INVALID_CONTEXT",
          repr(legacy_result))

    # 4b. Revoked grant.
    daemon.control_store().revoke_grant("grant:q1", at=iso(daemon),
                                        reason="Q1 probe")
    answer = verify(daemon, ctx["context_ref"], peer=own)
    revoked_result = answer.get("result") or {}
    check("revoked grant is refused",
          revoked_result.get("verified") is False
          and revoked_result.get("reason") == "POLICY_DENIED",
          repr(revoked_result))

    # 5. Malformed expected_epoch must never become a positive answer.
    #    A fresh live grant, so this check is not confounded by the revocation.
    live_ctx = make_context(daemon, actor="human:LIVE")
    make_grant(daemon, live_ctx, ref="grant:q2")
    answer = verify(daemon, live_ctx["context_ref"], peer=own,
                    grant_ref="grant:q2", expected_epoch="not-an-int")
    malformed_result = answer.get("result") or {}
    check("malformed expected_epoch is not a positive verification",
          malformed_result.get("verified") is not True,
          repr(answer)[:300])

    # 6. The read mutated no grant.
    before = dict(daemon.control_store().grants())
    verify(daemon, ctx["context_ref"], peer=own)
    check("verify read mutates no grant",
          daemon.control_store().grants() == before,
          f"grants={sorted(before)}")

failed = [name for name, ok, _ in results if not ok]
print(json.dumps({"checks": len(results), "failed": failed}, ensure_ascii=False))
sys.exit(1 if failed else 0)
