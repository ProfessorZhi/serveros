#!/usr/bin/env python3
"""Q1 round-5 probe: the reconcile caller is inside the Core, not the Fabric.

Expected-safe:
  * `runtime/dispatch_attempts.reconcile_attempt` exists and uses the SAME
    store passed in (no second ledger);
  * `observation_source=None` -> BLOCKED_PROVIDER_SOURCE, no store write;
  * a CLIENT_TURN_ID test double -> SETTLE_APPLIED, one store write;
  * the outer facade delegates to the Core and has no send path;
  * `runtime/daemon.py` has no `integrations/` loader (the #312 edge);
  * the boundary test's CORE_DIRS / KNOWN_CORE_CROSSINGS were not widened.

The test double is synthetic; it is not provider evidence.

Run: QA_TREE=<candidate> python3 qa_probe_r5_core_reconcile.py
"""
from __future__ import annotations

import importlib.util
import inspect
import json
import os
import sys
import tempfile
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


attempts = load("q1r5_attempts", REPO / "runtime" / "dispatch_attempts.py")
store_mod = load("q1r5_store", REPO / "runtime" / "agent_control_store.py")
facade = load("q1r5_facade",
              REPO / "integrations" / "v1-effect-reconciliation"
              / "effect_reconciliation.py")

KEY = "q1-core-effect-1"
PRINCIPAL = "human:A"
ITEM = "SRV-0001"
SESSION = "session:A"
WRITER = {"owner": "agent:A", "epoch": 3, "token": "3"}
results = []


def check(name, condition, detail=""):
    results.append((name, bool(condition), detail))
    print(f"{'PASS' if condition else 'FAIL'} {name}"
          + (f" :: {detail}" if detail else ""))


class Source:
    """A synthetic CLIENT_TURN_ID source. Not a provider adapter."""

    capability = attempts.CLIENT_TURN_ID

    def __init__(self, observation):
        self.observation = observation
        self.calls = []

    def observe(self, recorded, *, key):
        self.calls.append(key)
        return self.observation


def digest():
    return attempts.intent_digest(
        action="agent.session.send", device_ref="device:q1",
        verb="session.send", params={"instruction": "do it"},
        work_item=ITEM, session_ref=SESSION, principal=PRINCIPAL)


def observation(state="APPLIED"):
    return {"state": state, "source": "test-double:journal",
            "intent_digest": digest()}


check("Core caller reconcile_attempt exists",
      callable(getattr(attempts, "reconcile_attempt", None)),
      repr(getattr(attempts, "reconcile_attempt", None)))
check("Core caller exposes no send/retry API",
      not any(hasattr(attempts, n) for n in ("send", "retry", "dispatch")),
      "")
check("facade EffectReconciler exposes no send API",
      not hasattr(facade.EffectReconciler, "send"), "")
facade_source = inspect.getsource(facade.EffectReconciler.reconcile)
check("facade delegates to the Core reconcile_attempt",
      "reconcile_attempt" in facade_source, facade_source[:160])

daemon_src = (REPO / "runtime" / "daemon.py").read_text(encoding="utf-8")
check("runtime/daemon.py has no integrations/ loader",
      "integrations" not in daemon_src, "")
core_src = (REPO / "runtime" / "dispatch_attempts.py").read_text(encoding="utf-8")
check("runtime/dispatch_attempts.py has no integrations/ loader",
      "integrations" not in core_src, "")

sys.path.insert(0, str(REPO / "tests"))
boundary = load("q1r5_boundary", REPO / "tests" / "test_core_ability_boundary.py")
check("CORE_DIRS is still runtime/context/fleet",
      tuple(getattr(boundary, "CORE_DIRS", ())) == ("runtime", "context", "fleet"),
      repr(getattr(boundary, "CORE_DIRS", None)))
crossings = getattr(boundary, "KNOWN_CORE_CROSSINGS", {})
check("no integrations entry in KNOWN_CORE_CROSSINGS",
      "integrations" not in json.dumps(crossings, default=str),
      repr(crossings)[:240])

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    store = store_mod.AgentControlStore(root / "store.json")
    record = attempts.attempt_record(
        key=KEY, action="agent.session.send", device_ref="device:q1",
        verb="session.send", intent_digest=digest(),
        phase=attempts.EFFECT_UNKNOWN, work_item=ITEM,
        principal=PRINCIPAL, session_ref=SESSION, result=None)
    store.put_dispatch_attempt(record, actor=PRINCIPAL)
    before = json.dumps(store.get_dispatch_attempt(KEY), sort_keys=True,
                        default=str)

    blocked = attempts.reconcile_attempt(
        store, KEY, principal=PRINCIPAL, observation_source=None,
        writer=WRITER, current_writer=WRITER)
    after_blocked = json.dumps(store.get_dispatch_attempt(KEY), sort_keys=True,
                               default=str)
    check("source=None -> BLOCKED_PROVIDER_SOURCE",
          blocked.get("status") == "BLOCKED_PROVIDER_SOURCE",
          repr(blocked)[:240])
    check("source=None writes nothing", before == after_blocked,
          f"before==after is {before == after_blocked}")

    source = Source(observation())
    settled = attempts.reconcile_attempt(
        store, KEY, principal=PRINCIPAL, observation_source=source,
        writer=WRITER, current_writer=WRITER)
    after_settled = store.get_dispatch_attempt(KEY) or {}
    check("test-double CLIENT_TURN_ID -> SETTLE_APPLIED",
          settled.get("status") == "SETTLE_APPLIED"
          and settled.get("phase") == attempts.SETTLED, repr(settled)[:240])
    check("one source read", source.calls == [KEY], repr(source.calls))
    check("same store now records SETTLED",
          after_settled.get("phase") == attempts.SETTLED,
          after_settled.get("phase"))
    names = sorted(p.name for p in root.iterdir())
    check("no second ledger file created",
          set(names) <= {"store.json", "store.json.lock"}
          and "store.json" in names,
          repr(names))

failed = [n for n, ok, _ in results if not ok]
print(json.dumps({"checks": len(results), "failed": failed},
                 ensure_ascii=False))
sys.exit(1 if failed else 0)
