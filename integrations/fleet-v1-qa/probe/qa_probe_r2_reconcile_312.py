#!/usr/bin/env python3
"""Q1 independent review of #312 `agent.dispatch.reconcile`.

Questions:
  * does the route resolve the principal from a trusted, FRESH context and the
    writer from the attempt's own binding (not from the request)?
  * does it refuse caller-supplied authority/observation/result fields?
  * does it have any send/retry effect? (no source → BLOCKED_PROVIDER_SOURCE;
    the answer says `dispatching: False`; the driver has no send API)
  * does a refusal leave the attempt exactly EFFECT_UNKNOWN?

A CLIENT_TURN_ID source used below is a TEST DOUBLE. It proves the gate and the
kernel path only; it is NOT provider evidence and NOT a positive production
observation. Production passes no source and the answer is
BLOCKED_PROVIDER_SOURCE.

Run: QA_TREE=<tree with #312> python3 qa_probe_r2_reconcile_312.py
"""
from __future__ import annotations

import importlib.util
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


daemon_mod = load("q1_r2_daemon", REPO / "runtime" / "daemon.py")
driver_mod = load(
    "q1_r2_reconciler",
    REPO / "integrations" / "v1-effect-reconciliation" / "effect_reconciliation.py")
attempts = daemon_mod.dispatch_attempts_mod
coordination = daemon_mod.coordination_mod
actor = daemon_mod.actor_mod

ITEM = "SRV-0001"
KEY = "q1-effect-1"
PRINCIPAL = "human:A"
OTHER = "human:B"
SESSION = "session:q1:A"
WRITER = {"owner": "agent:A", "epoch": 3, "token": "3"}
results = []


def check(name, condition, detail=""):
    results.append((name, bool(condition), detail))
    print(f"{'PASS' if condition else 'FAIL'} {name}"
          + (f" :: {detail}" if detail else ""))


class Source:
    """A test double with an explicit capability. Not a provider adapter."""

    def __init__(self, capability, observation=None):
        self.capability = capability
        self.observation = observation
        self.calls = []

    def observe(self, recorded, *, key):
        self.calls.append(key)
        return self.observation


def context(now, actor_ref=PRINCIPAL):
    return actor.build({
        "human_control_provider": "provider:q1",
        "transport": "feishu",
        "transport_identity": "q1",
        "conversation_ref": "oc_q1",
        "message_ref": "om_q1",
        "observed_at": now,
        "provider_status": "VERIFIED",
        "provenance": "FACT",
        "actor_ref": actor_ref,
        "provider_pid": os.getpid(),
    }, now=now)


def digest():
    return attempts.intent_digest(
        action="agent.session.send", device_ref="device:q1",
        verb="session.send", params={"instruction": "do it"},
        work_item=ITEM, session_ref=SESSION, principal=PRINCIPAL)


def observation(state="APPLIED"):
    return {"state": state, "source": "test-double:journal",
            "intent_digest": digest()}


def seed(daemon, *, principal=PRINCIPAL, grant_ref=None, writer=WRITER,
         phase=attempts.EFFECT_UNKNOWN):
    now = daemon_mod._iso(daemon._now())
    ctx = context(now)
    store = daemon.control_store()
    store.put_context(ctx)
    binding = coordination.binding(
        session_role="execution", bound_item_ref=ITEM,
        device_ref="device:q1", recorded_at=now, writer=writer)
    binding["session_ref"] = SESSION
    store.put_binding(binding)
    record = attempts.attempt_record(
        key=KEY, action="agent.session.send", device_ref="device:q1",
        verb="session.send", intent_digest=digest(), phase=phase,
        work_item=ITEM, principal=principal, session_ref=SESSION,
        grant_ref=grant_ref, result=None)
    store.put_dispatch_attempt(record, actor=principal)
    return ctx, store


def daemon_at(root, *, source=None, verifier=None):
    registry = daemon_mod.registry_mod.Registry(document={
        "schema": "serveros/managed-runtime-registry.v1",
        "device_ref": "device:q1", "resources": []})
    return daemon_mod.Daemon(
        registry, state_dir=root / "state",
        socket_path=root / "run/serveros.sock", log=lambda m: None,
        dispatch_observation_source=source,
        dispatch_grant_verifier=verifier)


def reconcile(daemon, ctx, **extra):
    params = {"context_ref": ctx["context_ref"], "key": KEY}
    params.update(extra)
    answer = daemon.handle({"method": "agent.dispatch.reconcile",
                            "params": params})
    return answer


with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    (root / "run").mkdir()

    # 1. Production: no source.
    d = daemon_at(root)
    ctx, store = seed(d)
    answer = reconcile(d, ctx)
    result = answer.get("result") or {}
    check("no source -> BLOCKED_PROVIDER_SOURCE",
          result.get("status") == "BLOCKED_PROVIDER_SOURCE", repr(result)[:220])
    check("no source -> source_called=False, dispatching=False",
          result.get("source_called") is False
          and result.get("dispatching") is False, repr(result)[:220])
    check("refusal leaves the attempt EFFECT_UNKNOWN",
          store.get_dispatch_attempt(KEY)["phase"] == attempts.EFFECT_UNKNOWN,
          store.get_dispatch_attempt(KEY)["phase"])

    # 2. Caller-supplied authority/observation is refused, not ignored.
    for forbidden, value in (
            ("principal", OTHER), ("writer", WRITER),
            ("current_writer", WRITER), ("source", "caller"),
            ("observation", observation()), ("grant_ref", "grant:x"),
            ("result", {"ok": True}), ("phase", "SETTLED")):
        answer = reconcile(d, ctx, **{forbidden: value})
        result = answer.get("result") or {}
        check(f"caller field {forbidden!r} is INVALID_CONTEXT",
              result.get("status") == "INVALID_CONTEXT", repr(result)[:180])

    # 3. A RESPONSE_ID_ONLY source is refused before any read.
    source = Source(driver_mod.RESPONSE_ID_ONLY, observation())
    d2 = daemon_at(root / "d2")
    ctx2, store2 = seed(d2)
    d2._dispatch_observation_source = source
    result = (reconcile(d2, ctx2).get("result") or {})
    check("RESPONSE_ID_ONLY -> BLOCKED_PROVIDER_SOURCE",
          result.get("status") == "BLOCKED_PROVIDER_SOURCE", repr(result)[:220])
    check("RESPONSE_ID_ONLY source is never called", source.calls == [],
          repr(source.calls))

    # 4. Cross-principal is refused before the source.
    source_b = Source(driver_mod.CLIENT_TURN_ID, observation())
    d3 = daemon_at(root / "d3")
    ctx3, store3 = seed(d3, principal=PRINCIPAL)
    other_ctx = context(daemon_mod._iso(d3._now()), actor_ref=OTHER)
    store3.put_context(other_ctx)
    d3._dispatch_observation_source = source_b
    result = (reconcile(d3, other_ctx).get("result") or {})
    check("cross-principal -> REFUSE_PRINCIPAL",
          result.get("status") == "REFUSE_PRINCIPAL", repr(result)[:220])
    check("cross-principal never reads the source", source_b.calls == [],
          repr(source_b.calls))
    check("cross-principal leaves EFFECT_UNKNOWN",
          store3.get_dispatch_attempt(KEY)["phase"] == attempts.EFFECT_UNKNOWN,
          store3.get_dispatch_attempt(KEY)["phase"])

    # 5. Grant ref without a verifier is blocked before the source.
    source_g = Source(driver_mod.CLIENT_TURN_ID, observation())
    d4 = daemon_at(root / "d4")
    ctx4, store4 = seed(d4, grant_ref="grant:q1")
    d4._dispatch_observation_source = source_g
    result = (reconcile(d4, ctx4).get("result") or {})
    check("grant ref without verifier -> BLOCKED_GRANT_UNKNOWN",
          result.get("status") == "BLOCKED_GRANT_UNKNOWN", repr(result)[:220])
    check("grant refusal never reads the source", source_g.calls == [],
          repr(source_g.calls))

    # 5b. A stale context is not a trusted actor; no source read.
    source_s = Source(driver_mod.CLIENT_TURN_ID, observation())
    d6 = daemon_at(root / "d6")
    ctx6, store6 = seed(d6)
    stale = context(daemon_mod._iso(d6._now() - 7200))
    store6.put_context(stale)
    d6._dispatch_observation_source = source_s
    result = (reconcile(d6, stale).get("result") or {})
    check("stale context -> UNTRUSTED_ACTOR",
          result.get("status") == "UNTRUSTED_ACTOR", repr(result)[:200])
    check("stale context never reads the source", source_s.calls == [],
          repr(source_s.calls))

    # 5c. A terminal attempt is not reconciled and the source is not read.
    source_t = Source(driver_mod.CLIENT_TURN_ID, observation())
    d7 = daemon_at(root / "d7")
    ctx7, store7 = seed(d7, phase=attempts.SETTLED)
    d7._dispatch_observation_source = source_t
    result = (reconcile(d7, ctx7).get("result") or {})
    check("terminal attempt -> NOT_UNKNOWN",
          result.get("status") == "NOT_UNKNOWN", repr(result)[:200])
    check("terminal attempt never reads the source", source_t.calls == [],
          repr(source_t.calls))

    # 5d. A missing key / no attempt is an answer, not a settle.
    d8 = daemon_at(root / "d8")
    ctx8, store8 = seed(d8)
    answer = d8.handle({"method": "agent.dispatch.reconcile", "params": {
        "context_ref": ctx8["context_ref"]}})
    result = answer.get("result") or {}
    check("missing key -> INVALID_CONTEXT",
          result.get("status") == "INVALID_CONTEXT", repr(result)[:200])
    answer = d8.handle({"method": "agent.dispatch.reconcile", "params": {
        "context_ref": ctx8["context_ref"], "key": "no-such-key"}})
    result = answer.get("result") or {}
    check("unknown key -> NO_ATTEMPT", result.get("status") == "NO_ATTEMPT",
          repr(result)[:200])

    # 6. A TEST-DOUBLE CLIENT_TURN_ID source settles via the kernel. This is
    #    not provider evidence; it proves the path has no send and records one
    #    settle write against the same ledger.
    source_ok = Source(driver_mod.CLIENT_TURN_ID, observation("APPLIED"))
    d5 = daemon_at(root / "d5")
    ctx5, store5 = seed(d5)
    d5._dispatch_observation_source = source_ok
    result = (reconcile(d5, ctx5).get("result") or {})
    check("test-double CLIENT_TURN_ID -> SETTLED",
          result.get("status") == "SETTLE_APPLIED"
          and result.get("phase") == attempts.SETTLED, repr(result)[:260])
    check("settle answer still says dispatching=False",
          result.get("dispatching") is False, repr(result)[:220])
    check("test-double source was read exactly once", source_ok.calls == [KEY],
          repr(source_ok.calls))
    check("settle writes the same attempt ledger",
          store5.get_dispatch_attempt(KEY)["phase"] == attempts.SETTLED,
          store5.get_dispatch_attempt(KEY)["phase"])

    # 7. Static: no send/retry API on the driver.
    reconciler = driver_mod.EffectReconciler
    forbidden_api = [n for n in ("send", "retry", "dispatch", "resend")
                     if hasattr(reconciler, n)]
    check("driver exposes no send/retry/dispatch API", forbidden_api == [],
          repr(forbidden_api))

failed = [n for n, ok, _ in results if not ok]
print(json.dumps({"checks": len(results), "failed": failed},
                 ensure_ascii=False))
sys.exit(1 if failed else 0)
