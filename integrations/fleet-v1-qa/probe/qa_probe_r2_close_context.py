#!/usr/bin/env python3
"""Q1 independent probe: does the handoff close/transfer require a context
that AUTHORIZES a mutation, or merely one that is fresh/well-shaped?

The #272 verified read calls `actor.authorizes` (FRESH + provenance FACT +
provider_status VERIFIED). A handoff's write-authority transfer is the same
kind of protected write. This probe checks whether `agent.binding.close` with
`successor_ref` accepts a BLOCKED/UNSUPPORTED context (non-FACT provenance)
that `validate` + `freshness` alone would pass.

Run: QA_TREE=<S1 candidate> python3 qa_probe_r2_close_context.py
"""
from __future__ import annotations

import datetime
import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(os.environ["QA_TREE"]).resolve()
sys.path.insert(0, str(REPO))


def load(name, path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


daemon_mod = load("q1_cc_daemon", REPO / "runtime" / "daemon.py")
actor = load("q1_cc_actor", REPO / "context" / "actor.py")
coordination = load("q1_cc_coordination", REPO / "runtime" / "coordination.py")

ITEM = "SRV-0001"
SOURCE = "session:q1:src"
TARGET = "session:q1:dst"
results = []


def check(name, condition, detail=""):
    results.append((name, bool(condition), detail))
    print(f"{'PASS' if condition else 'FAIL'} {name}"
          + (f" :: {detail}" if detail else ""))


def stamp(epoch):
    return datetime.datetime.fromtimestamp(
        epoch, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def context(now, *, status="VERIFIED", provenance="FACT", actor_ref="human:A"):
    return actor.build({
        "human_control_provider": "provider:q1",
        "transport": "feishu", "transport_identity": "q1",
        "conversation_ref": "oc_q1", "message_ref": "om_q1",
        "observed_at": now, "provider_status": status,
        "provenance": provenance, "actor_ref": actor_ref,
        "provider_pid": os.getpid(),
    }, now=now)


with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    (root / "run").mkdir()
    registry = daemon_mod.registry_mod.Registry(document={
        "schema": "serveros/managed-runtime-registry.v1",
        "device_ref": "device:q1", "resources": []})
    daemon = daemon_mod.Daemon(
        registry, state_dir=root / "state",
        socket_path=root / "run/serveros.sock", log=lambda m: None)
    now = daemon_mod._iso(daemon._now())
    store = daemon.control_store()

    good = context(now)
    blocked = context(now, status="BLOCKED", provenance="INFERRED",
                      actor_ref="human:B")
    unsupported = context(now, status="UNSUPPORTED", provenance="INFERRED",
                          actor_ref="human:C")
    stale = context(daemon_mod._iso(daemon._now() - 7200))
    for record in (good, blocked, unsupported, stale):
        store.put_context(record)

    # A live Work lease that has moved to the target principal.
    conn = sqlite3.connect(root / "serveros.db")
    conn.execute("CREATE TABLE IF NOT EXISTS leases (resource TEXT PRIMARY KEY, "
                 "owner TEXT NOT NULL, token TEXT NOT NULL, "
                 "expires_at TEXT NOT NULL)")
    conn.execute("INSERT OR REPLACE INTO leases VALUES (?,?,?,?)",
                 (f"work-write:{ITEM}", "agent:B", "2",
                  stamp(time.time() + 600)))
    conn.commit()
    conn.close()

    def try_close(context_ref, tag):
        # A distinct Item per case, so the previous transfer's claim cannot be
        # the current writer of this scope.
        item = f"SRV-{abs(hash(tag)) % 9000 + 1000:04d}"
        source = f"{SOURCE}:{tag}"
        target = f"{TARGET}:{tag}"
        conn = sqlite3.connect(root / "serveros.db")
        conn.execute("INSERT OR REPLACE INTO leases VALUES (?,?,?,?)",
                     (f"work-write:{item}", "agent:B", "2",
                      stamp(time.time() + 600)))
        conn.commit()
        conn.close()
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
        answer = daemon.handle({"method": "agent.binding.close", "params": {
            "session_ref": source, "successor_ref": target,
            "context_ref": context_ref}})
        return answer.get("result") or answer

    ok = try_close(good["context_ref"], "good")
    check("control: VERIFIED/FACT context transfers", ok.get("closed") is True,
          repr(ok)[:260])

    for label, record in (("BLOCKED", blocked), ("UNSUPPORTED", unsupported)):
        answer = try_close(record["context_ref"], label)
        check(f"{label}/INFERRED context must NOT transfer",
              answer.get("closed") is not True,
              f"closed={answer.get('closed')!r} "
              f"reason={answer.get('reason')!r}")

    answer = try_close(stale["context_ref"], "stale")
    check("stale context must NOT transfer", answer.get("closed") is not True,
          f"closed={answer.get('closed')!r} reason={answer.get('reason')!r}")

    answer = try_close(None, "missing")
    check("missing context must NOT transfer", answer.get("closed") is not True,
          f"closed={answer.get('closed')!r} reason={answer.get('reason')!r}")

    # The pre-claim route is the other half of the same transfer; check the
    # same authorization question there.
    def try_claim(context_ref, tag):
        item = f"SRV-{abs(hash('claim-' + tag)) % 9000 + 1000:04d}"
        source = f"{SOURCE}:claim:{tag}"
        target = f"{TARGET}:claim:{tag}"
        conn = sqlite3.connect(root / "serveros.db")
        conn.execute("INSERT OR REPLACE INTO leases VALUES (?,?,?,?)",
                     (f"work-write:{item}", "agent:B", "2",
                      stamp(time.time() + 600)))
        conn.commit()
        conn.close()
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
        answer = daemon.handle({"method": "agent.binding.claim", "params": {
            "session_ref": target, "from_ref": source,
            "context_ref": context_ref}})
        return answer.get("result") or answer

    ok = try_claim(good["context_ref"], "good")
    check("claim control: VERIFIED/FACT context claims",
          ok.get("claimed") is True, repr(ok)[:240])
    for label, record in (("BLOCKED", blocked), ("UNSUPPORTED", unsupported)):
        answer = try_claim(record["context_ref"], label)
        check(f"claim {label}/INFERRED context must NOT claim",
              answer.get("claimed") is not True,
              f"claimed={answer.get('claimed')!r} "
              f"reason={answer.get('reason')!r}")

failed = [n for n, ok_, _ in results if not ok_]
print(json.dumps({"checks": len(results), "failed": failed},
                 ensure_ascii=False))
sys.exit(1 if failed else 0)
