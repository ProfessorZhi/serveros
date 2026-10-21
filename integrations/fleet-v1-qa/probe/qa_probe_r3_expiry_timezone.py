#!/usr/bin/env python3
"""Q1 final-gate probe: mixed-timezone `expires_at` in the handoff close path.

The close/claim routes compute `source_claim_expired` with
`str(expires) <= _iso(now)`, a text comparison. This probe fixes the daemon
clock (`now=` injection) and uses five spellings of the same or neighbouring
instants to record expected vs actual:

  past +08:00   -> expected expired True
  past Z        -> expected True
  same-second Z -> expected True at the boundary (<=)
  future +08:00 -> expected False
  future Z      -> expected False

It also calls `coordination.writer_axis` with the fixed-width `_writer_now`
stamp, which is what admission uses, to show whether the same claim reads as
expired there.

Run: QA_TREE=<candidate> python3 qa_probe_r3_expiry_timezone.py
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
import runtime.coordination as coordination  # noqa: E402
import runtime.daemon as daemon_mod  # noqa: E402
import runtime.registry as registry_mod  # noqa: E402

results = []
FIXED = 1700000000.0  # 2023-11-14T22:13:20Z
NOW_Z = datetime.datetime.fromtimestamp(
    FIXED, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
PLUS8 = datetime.timezone(datetime.timedelta(hours=8))


def check(name, condition, detail=""):
    results.append((name, bool(condition), detail))
    print(f"{'PASS' if condition else 'FAIL'} {name}"
          + (f" :: {detail}" if detail else ""))


def iso(epoch):
    return daemon_mod._iso(epoch)


def stamp(epoch):
    return datetime.datetime.fromtimestamp(
        epoch, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def at_plus8(epoch, second=None):
    dt = datetime.datetime.fromtimestamp(epoch, PLUS8)
    if second is not None:
        dt = dt.replace(second=second)
    return dt.isoformat()


with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    (root / "run").mkdir()
    registry = registry_mod.Registry(document={
        "schema": "serveros/managed-runtime-registry.v1",
        "device_ref": "device:q1", "resources": []})
    daemon = daemon_mod.Daemon(
        registry, state_dir=root / "state",
        socket_path=root / "run/serveros.sock", log=lambda m: None,
        now=lambda: FIXED)
    now = iso(FIXED)
    store = daemon.control_store()
    context = actor_mod.build({
        "human_control_provider": "provider:q1",
        "transport": "feishu", "transport_identity": "q1",
        "conversation_ref": "oc_q1_expiry", "message_ref": "om_q1_expiry",
        "observed_at": now, "provider_status": "VERIFIED",
        "provenance": "FACT", "actor_ref": "human:A",
        "provider_pid": os.getpid(),
    }, now=now)
    store.put_context(context)
    print(f"OBSERVE fixed now={FIXED} iso={now} z={NOW_Z}")

    cases = {
        "past_plus8": at_plus8(FIXED - 3600),          # past, +08:00
        "past_z": stamp(FIXED - 3600),                 # past, Z
        "same_second_z": stamp(FIXED),                 # same instant, Z
        "future_plus8": at_plus8(FIXED + 3600),        # future, +08:00
        "future_z": stamp(FIXED + 3600),               # future, Z
    }
    expected = {
        "past_plus8": True,
        "past_z": True,
        "same_second_z": True,
        "future_plus8": False,
        "future_z": False,
    }

    counter = [0]
    for label, expires in cases.items():
        counter[0] += 1
        item = f"SRV-{counter[0]:04d}"
        source = f"session:q1:src:{label}"
        target = f"session:q1:dst:{label}"
        conn = sqlite3.connect(root / "serveros.db")
        conn.execute("CREATE TABLE IF NOT EXISTS leases (resource TEXT PRIMARY KEY, "
                     "owner TEXT NOT NULL, token TEXT NOT NULL, "
                     "expires_at TEXT NOT NULL)")
        # `resolver.live_lease` reads the real wall clock, not the injected
        # daemon clock; keep the lease live in real time. The claim's own
        # `expires_at` is the fixed-clock instant under test.
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
            "writer": {"owner": "agent:A", "epoch": 1, "token": "1",
                       "expires_at": expires},
        })
        store.put_binding({
            "schema": "serveros/thread-binding.v1",
            "session_ref": target, "session_role": "execution",
            "bound_item_ref": item, "bound_item_ref_frozen": True,
            "state": "OPEN",
        })
        answer = daemon.handle({"method": "agent.binding.close", "params": {
            "session_ref": source, "successor_ref": target,
            "context_ref": context["context_ref"]}})
        result = answer.get("result") or answer
        actual = result.get("source_claim_expired")
        print(f"STATE {label}: expires={expires!r} expected_expired={expected[label]!r} "
              f"actual={actual!r} closed={result.get('closed')!r} "
              f"reason={result.get('reason')!r}")
        check(f"close {label}: source_claim_expired expected {expected[label]!r}",
              actual is expected[label],
              f"expires={expires!r} actual={actual!r}")

        # The admission-side reader uses the fixed-width Z stamp.
        record = store.get_binding(source)
        axis = coordination.writer_axis(
            record, current_writer=(record or {}).get("writer"), now=NOW_Z)
        print(f"STATE {label}: writer_axis(now={NOW_Z})={axis!r}")
        if expected[label]:
            check(f"admission {label}: an expired claim is EXPIRED",
                  axis == "EXPIRED", f"axis={axis!r} expires={expires!r}")
        else:
            check(f"admission {label}: a future claim is not EXPIRED",
                  axis != "EXPIRED", f"axis={axis!r} expires={expires!r}")

failed = [n for n, ok, _ in results if not ok]
print(json.dumps({"checks": len(results), "failed": failed},
                 ensure_ascii=False))
sys.exit(1 if failed else 0)
