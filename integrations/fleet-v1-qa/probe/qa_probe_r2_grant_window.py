#!/usr/bin/env python3
"""Q1 independent RED/GREEN for #307: grant windows are instants, not text.

MODE=red   (unmodified #272 fc93590): an expired grant whose `expires_at` is
           spelled with a +08:00 offset verifies True; a malformed window
           verifies True.
MODE=green (#307 6abefaa): both refuse.

Run: QA_TREE=<tree> QA_MODE=red|green python3 qa_probe_r2_grant_window.py
"""
from __future__ import annotations

import datetime
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(os.environ["QA_TREE"]).resolve()
MODE = os.environ.get("QA_MODE", "green").strip().lower()
sys.path.insert(0, str(REPO))


def load(name, path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


daemon_mod = load("q1_r2g_daemon", REPO / "runtime" / "daemon.py")
actor = load("q1_r2g_actor", REPO / "context" / "actor.py")

ITEM = "SRV-0001"
PROJECT = "serveros"
ACTION = "lease.acquire"
results = []


def check(name, condition, detail=""):
    results.append((name, bool(condition), detail))
    print(f"{'PASS' if condition else 'FAIL'} {name}"
          + (f" :: {detail}" if detail else ""))


def utc(dt):
    return dt.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)
    registry = daemon_mod.registry_mod.Registry(document={
        "schema": "serveros/managed-runtime-registry.v1",
        "device_ref": "device:q1", "resources": []})
    daemon = daemon_mod.Daemon(
        registry, state_dir=tmp / "state", socket_path=tmp / "run/s.sock",
        log=lambda m: None)
    now_dt = datetime.datetime.now(datetime.timezone.utc)
    now = utc(now_dt)
    past = now_dt - datetime.timedelta(minutes=10)
    past_plus8 = past.astimezone(
        datetime.timezone(datetime.timedelta(hours=8))).isoformat()

    report = {
        "human_control_provider": "provider:q1",
        "transport": "feishu", "transport_identity": "q1",
        "conversation_ref": "oc_q1", "message_ref": "om_q1",
        "observed_at": now, "provider_status": "VERIFIED",
        "provenance": "FACT", "actor_ref": "human:A",
        "provider_pid": os.getpid(),
    }
    ctx = actor.build(report, now=now)
    store = daemon.control_store()
    store.put_context(ctx)

    store.put_grant({
        "grant_ref": "grant:offset",
        "delegated_by": ctx["context_ref"],
        "valid_from": utc(now_dt - datetime.timedelta(hours=1)),
        "expires_at": past_plus8,
        "projects": [PROJECT], "items": [ITEM], "actions": [ACTION],
    })
    store.put_grant({
        "grant_ref": "grant:malformed",
        "delegated_by": ctx["context_ref"],
        "valid_from": utc(now_dt - datetime.timedelta(hours=1)),
        "expires_at": "not-a-time",
        "projects": [PROJECT], "items": [ITEM], "actions": [ACTION],
    })

    peer = {"pid": os.getpid(), "uid": os.getuid(), "gid": os.getgid()}

    def verify(ref):
        answer = daemon.handle({"method": "agent.lease.authority.verify",
                                "params": {
                                    "context_ref": ctx["context_ref"],
                                    "grant_ref": ref, "action": ACTION,
                                    "project": PROJECT, "item_ref": ITEM}},
                               peer=peer)
        return answer.get("result") or answer

    offset = verify("grant:offset")
    malformed = verify("grant:malformed")

    if MODE == "red":
        check("RED an expired +08:00 grant verifies (fail-open)",
              offset.get("verified") is True, repr(offset)[:260])
        check("RED a malformed window verifies (fail-open)",
              malformed.get("verified") is True, repr(malformed)[:260])
    else:
        check("GREEN an expired +08:00 grant is refused",
              offset.get("verified") is False
              and offset.get("reason") == "POLICY_DENIED", repr(offset)[:260])
        check("GREEN a malformed window is refused",
              malformed.get("verified") is False
              and malformed.get("reason") == "INVALID_CONTEXT",
              repr(malformed)[:260])

failed = [n for n, ok, _ in results if not ok]
print(json.dumps({"mode": MODE, "checks": len(results), "failed": failed},
                 ensure_ascii=False))
sys.exit(1 if failed else 0)
