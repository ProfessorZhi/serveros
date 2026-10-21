#!/usr/bin/env python3
"""Q1 final-gate probe: fresh non-FACT contexts must not claim/close authority.

Expected-safe: a context with provenance UNKNOWN, or provider_status
BLOCKED/UNSUPPORTED (including provenance UNKNOWN), must NOT:
  * mint a successor writer claim (`agent.binding.claim`), or
  * close the source and transfer write authority (`agent.binding.close`).
A VERIFIED/FACT context is the positive control. Stale/missing contexts must
also refuse.

Run: QA_TREE=<candidate> python3 qa_probe_r3_context_authority.py
"""
from __future__ import annotations

import datetime
import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(os.environ["QA_TREE"]).resolve()
sys.path.insert(0, str(REPO))

import context.actor as actor_mod  # noqa: E402
import runtime.daemon as daemon_mod  # noqa: E402
import runtime.registry as registry_mod  # noqa: E402

results = []


def check(name, condition, detail=""):
    results.append((name, bool(condition), detail))
    print(f"{'PASS' if condition else 'FAIL'} {name}"
          + (f" :: {detail}" if detail else ""))


def stamp(epoch):
    return datetime.datetime.fromtimestamp(
        epoch, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_context(now, *, status="VERIFIED", provenance="FACT", tag="base"):
    return actor_mod.build({
        "human_control_provider": "provider:q1",
        "transport": "feishu", "transport_identity": "q1",
        "conversation_ref": f"oc_q1_{tag}", "message_ref": f"om_q1_{tag}",
        "observed_at": now, "provider_status": status,
        "provenance": provenance, "actor_ref": "human:A",
        "provider_pid": os.getpid(),
    }, now=now)


with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    (root / "run").mkdir()
    registry = registry_mod.Registry(document={
        "schema": "serveros/managed-runtime-registry.v1",
        "device_ref": "device:q1", "resources": []})
    daemon = daemon_mod.Daemon(
        registry, state_dir=root / "state",
        socket_path=root / "run/serveros.sock", log=lambda m: None)
    now = daemon_mod._iso(daemon._now())
    store = daemon.control_store()

    contexts = {}
    contexts["GOOD"] = build_context(now, tag="good")
    contexts["BLOCKED/INFERRED"] = build_context(
        now, status="BLOCKED", provenance="INFERRED", tag="blocked")
    contexts["UNSUPPORTED/INFERRED"] = build_context(
        now, status="UNSUPPORTED", provenance="INFERRED", tag="unsupported")
    contexts["BLOCKED/UNKNOWN"] = build_context(
        now, status="BLOCKED", provenance="UNKNOWN", tag="blocked_unknown")
    contexts["UNSUPPORTED/UNKNOWN"] = build_context(
        now, status="UNSUPPORTED", provenance="UNKNOWN", tag="unsup_unknown")
    # `build` refuses VERIFIED+UNKNOWN; store the same-shaped record directly.
    verified_unknown = build_context(now, tag="verified_unknown")
    verified_unknown = dict(verified_unknown)
    verified_unknown["provenance"] = "UNKNOWN"
    contexts["VERIFIED/UNKNOWN"] = verified_unknown
    stale = build_context(
        daemon_mod._iso(daemon._now() - 7200), tag="stale")
    contexts["STALE"] = stale
    for record in contexts.values():
        store.put_context(record)

    counter = [0]

    def new_item(tag):
        counter[0] += 1
        item = f"SRV-{counter[0]:04d}"
        conn = sqlite3.connect(root / "serveros.db")
        conn.execute("CREATE TABLE IF NOT EXISTS leases (resource TEXT PRIMARY KEY, "
                     "owner TEXT NOT NULL, token TEXT NOT NULL, "
                     "expires_at TEXT NOT NULL)")
        conn.execute("INSERT OR REPLACE INTO leases VALUES (?,?,?,?)",
                     (f"work-write:{item}", "agent:B", "2",
                      stamp(time.time() + 600)))
        conn.commit()
        conn.close()
        return item

    def seed(source, target, item):
        store.put_binding({
            "schema": "serveros/thread-binding.v2",
            "session_ref": source, "session_role": "execution",
            "bound_item_ref": item, "bound_item_ref_frozen": True,
            "state": "OPEN",
            "writer": {"owner": "agent:A", "epoch": 1, "token": "1"},
        })
        store.put_binding({
            "schema": "serveros/thread-binding.v1",
            "session_ref": target, "session_role": "execution",
            "bound_item_ref": item, "bound_item_ref_frozen": True,
            "state": "OPEN",
        })

    def try_claim(label, context_ref):
        item = new_item(f"claim-{label}")
        source = f"session:q1:src:claim:{label}"
        target = f"session:q1:dst:claim:{label}"
        seed(source, target, item)
        answer = daemon.handle({"method": "agent.binding.claim", "params": {
            "session_ref": target, "from_ref": source,
            "context_ref": context_ref}})
        return answer.get("result") or answer

    def try_close(label, context_ref):
        item = new_item(f"close-{label}")
        source = f"session:q1:src:close:{label}"
        target = f"session:q1:dst:close:{label}"
        seed(source, target, item)
        answer = daemon.handle({"method": "agent.binding.close", "params": {
            "session_ref": source, "successor_ref": target,
            "context_ref": context_ref}})
        return answer.get("result") or answer

    good_claim = try_claim("GOOD", contexts["GOOD"]["context_ref"])
    check("claim control GOOD/VERIFIED/FACT claims",
          good_claim.get("claimed") is True, repr(good_claim)[:240])
    good_close = try_close("GOOD", contexts["GOOD"]["context_ref"])
    check("close control GOOD/VERIFIED/FACT closes",
          good_close.get("closed") is True, repr(good_close)[:240])

    for label, record in contexts.items():
        if label in ("GOOD", "STALE"):
            continue
        claim = try_claim(label, record["context_ref"])
        check(f"claim {label} must NOT claim",
              claim.get("claimed") is not True,
              f"expected claimed is not true, actual={claim.get('claimed')!r} "
              f"reason={claim.get('reason')!r}")
        close = try_close(label, record["context_ref"])
        check(f"close {label} must NOT close/transfer",
              close.get("closed") is not True,
              f"expected closed is not true, actual={close.get('closed')!r} "
              f"reason={close.get('reason')!r}")

    for label in ("STALE",):
        claim = try_claim(label, contexts[label]["context_ref"])
        check(f"claim {label} must NOT claim", claim.get("claimed") is not True,
              f"actual={claim.get('claimed')!r} reason={claim.get('reason')!r}")
        close = try_close(label, contexts[label]["context_ref"])
        check(f"close {label} must NOT close", close.get("closed") is not True,
              f"actual={close.get('closed')!r} reason={close.get('reason')!r}")

    for label in ("MISSING",):
        claim = try_claim(label, None)
        check(f"claim {label} must NOT claim", claim.get("claimed") is not True,
              f"actual={claim.get('claimed')!r} reason={claim.get('reason')!r}")
        close = try_close(label, None)
        check(f"close {label} must NOT close", close.get("closed") is not True,
              f"actual={close.get('closed')!r} reason={close.get('reason')!r}")

failed = [n for n, ok, _ in results if not ok]
print(json.dumps({"checks": len(results), "failed": failed},
                 ensure_ascii=False))
sys.exit(1 if failed else 0)
