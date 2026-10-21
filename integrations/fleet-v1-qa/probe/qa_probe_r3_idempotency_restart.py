#!/usr/bin/env python3
"""Q1 final-gate probe: idempotency, repeat close/claim, restart.

After one successful transfer A->B:
  * a second close of the (now CLOSED) source must be a non-transfer, not a
    second mint or a false success;
  * a second claim for the target must be refused (it already has a writer);
  * a repeated handoff must not create a second claim or move the writer;
  * a daemon restart over the same state must preserve the transferred claim,
    keep the source CLOSED, fence the stale source and admit the target.

Cross-device is NOT_RUN (no second enrolled device / no real provider).

Run: QA_TREE=<candidate> python3 qa_probe_r3_idempotency_restart.py
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
import fleet.agent_control_router as router_mod  # noqa: E402
import mcp.agent_mcp as agent_mcp  # noqa: E402
import runtime.agent_sessions as sessions_mod  # noqa: E402
import runtime.coordination as coordination  # noqa: E402
import runtime.daemon as daemon_mod  # noqa: E402
import runtime.registry as registry_mod  # noqa: E402

LOCAL = "device:server-beijing"
RUNTIME = "agent-runtime:codex"
PROVIDER = "provider:q1-harness"
ITEM = "LIT-0101"
results = []


def check(name, condition, detail=""):
    results.append((name, bool(condition), detail))
    print(f"{'PASS' if condition else 'FAIL'} {name}"
          + (f" :: {detail}" if detail else ""))


class Provider:
    def __init__(self):
        self.provider_id = PROVIDER
        self._support = {a: {"state": "SUPPORTED"}
                         for a in sessions_mod.contract()["support"]["actions"]}

    def actions(self):
        return dict(self._support)


class Service:
    def __init__(self):
        self.calls = []
        self.providers = {PROVIDER: Provider()}
        self._seq = 0

    def readiness(self, provider_id):
        now = datetime.datetime.now(datetime.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
        return {"schema": "serveros/provider-instance.v1",
                "provider": provider_id, "device_ref": LOCAL,
                "observed_at": now, "status": "AVAILABLE",
                "status_provenance": "INFERRED", "reason": None,
                "reason_text": None}

    def _record(self, name, **kwargs):
        self.calls.append((name, kwargs))
        if name == "create":
            self._seq += 1
            return {"session_ref": kwargs.get("session_ref_value")
                    or f"session:q1:{self._seq}", "action": name}
        return {"session_ref": kwargs.get("session_ref_value") or "session:q1:0",
                "action": name}

    def create(self, **kwargs):
        return self._record("create", **kwargs)

    def send(self, **kwargs):
        return self._record("send", **kwargs)

    def result(self, **kwargs):
        return self._record("result", **kwargs)

    def session_inspect(self, ref):
        return self._record("session_inspect", session_ref_value=ref)

    def resume(self, **kwargs):
        return self._record("resume", **kwargs)

    def interrupt(self, **kwargs):
        return self._record("interrupt", **kwargs)

    def stop(self, **kwargs):
        return self._record("stop", **kwargs)

    def handoff(self, **kwargs):
        return self._record("handoff", **kwargs)


class CallAdapter:
    def __init__(self, daemon):
        self._daemon = daemon

    def call(self, method, params):
        answer = self._daemon.handle({"method": method, "params": params})
        if answer.get("ok") is False:
            raise RuntimeError(json.dumps(answer.get("error")))
        return answer.get("result") or {}


def stamp(epoch):
    return datetime.datetime.fromtimestamp(
        epoch, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def set_lease(root, owner, token, ttl=600):
    conn = sqlite3.connect(root / "serveros.db")
    conn.execute("CREATE TABLE IF NOT EXISTS leases (resource TEXT PRIMARY KEY, "
                 "owner TEXT NOT NULL, token TEXT NOT NULL, "
                 "expires_at TEXT NOT NULL)")
    conn.execute("INSERT OR REPLACE INTO leases VALUES (?,?,?,?)",
                 (f"work-write:{ITEM}", owner, str(token),
                  stamp(time.time() + ttl)))
    conn.commit()
    conn.close()


def build(root):
    registry = registry_mod.Registry(document={
        "schema": "serveros/managed-runtime-registry.v1",
        "device_ref": LOCAL, "resources": []})
    daemon = daemon_mod.Daemon(
        registry, state_dir=root / "state",
        socket_path=root / "run/serveros.sock", log=lambda m: None)
    bridge = CallAdapter(daemon)
    now = daemon_mod._iso(daemon._now())
    context = actor_mod.build({
        "human_control_provider": "provider:botmux-human-control",
        "transport": "feishu",
        "transport_identity": "botmux-verified-enterprise-user",
        "conversation_ref": "oc_q1_idem", "message_ref": "om_q1_idem",
        "observed_at": now, "provider_status": "VERIFIED",
        "provenance": "FACT", "actor_ref": "human:q1",
        "provider_pid": 4242,
    }, now=now)
    daemon.control_store().put_context(context)
    bindings = agent_mcp.DaemonBindings(bridge)
    loopback = router_mod.LocalLoopbackTransport(
        device_ref=LOCAL, service=Service(), bindings=bindings)
    router = router_mod.FleetAgentControlRouter(
        local_device_ref=LOCAL, transports=[loopback],
        context_store=agent_mcp.DaemonContexts(bridge))
    return daemon, bridge, bindings, router, context["context_ref"]


def request(action, **extra):
    payload = {"action": action, "device_ref": LOCAL, "agent_runtime": RUNTIME}
    payload.update(extra)
    return router_mod.request_from_payload(payload)


def create(router, ctx, key):
    return router.route(request(
        "agent.session.create", context_ref=ctx, session_role="execution",
        bound_item_ref=ITEM,
        params={"agent_id": "agent:q1", "instruction": "start",
                "idempotency_key": key}))


def send(router, ctx, ref, key):
    return router.route(request(
        "agent.session.send", context_ref=ctx, session_ref=ref,
        params={"instruction": "more", "turn_idempotency_key": key}))


def resolve(bridge, ref):
    return bridge.call("agent.binding.resolve", {"session_ref": ref})


with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    (root / "run").mkdir()
    set_lease(root, "agent:A", "1")
    daemon, bridge, bindings, router, ctx = build(root)
    a = create(router, ctx, "a")
    b = create(router, ctx, "b")
    a_ref = (a.result or {}).get("session_ref")
    b_ref = (b.result or {}).get("session_ref")
    set_lease(root, "agent:B", "2")
    handoff = request("agent.session.handoff", context_ref=ctx,
                      session_ref=a_ref, params={"checkpoint": f"{ITEM}#1"})
    resume = request("agent.session.resume", context_ref=ctx,
                     session_ref=b_ref)
    first = router.handoff(handoff, create_request=resume)
    check("first handoff succeeds", first.ok, first.reason or "")
    b_claim = (resolve(bridge, b_ref).get("binding") or {}).get("writer")
    cw = (resolve(bridge, b_ref).get("current_writer") or {})
    check("after transfer target is current", cw == b_claim,
          f"current={cw!r} target={b_claim!r}")

    # 1. Repeat close of the CLOSED source.
    again = bridge.call("agent.binding.close", {
        "session_ref": a_ref, "successor_ref": b_ref,
        "context_ref": ctx})
    check("repeat close is a non-transfer", again.get("closed") is not True,
          f"closed={again.get('closed')!r} reason={again.get('reason')!r}")
    a_state = (resolve(bridge, a_ref).get("binding") or {}).get("state")
    b_claim2 = (resolve(bridge, b_ref).get("binding") or {}).get("writer")
    check("repeat close leaves source CLOSED and target claim unchanged",
          a_state == "CLOSED" and b_claim2 == b_claim,
          f"source={a_state} target={b_claim2!r}")

    # 2. Repeat claim for a target that already has a writer.
    claim_again = bridge.call("agent.binding.claim", {
        "session_ref": b_ref, "from_ref": a_ref, "context_ref": ctx})
    check("repeat claim is refused", claim_again.get("claimed") is not True,
          f"claimed={claim_again.get('claimed')!r} "
          f"reason={claim_again.get('reason')!r}")

    # 3. Repeat handoff after the source is CLOSED.
    try:
        second = router.handoff(handoff, create_request=resume)
        second_state = {"ok": second.ok, "reason": second.reason,
                        "completed": list(second.completed)}
    except Exception as exc:  # noqa: BLE001
        second_state = {"raised": type(exc).__name__}
    check("repeated handoff does not report success",
          second_state.get("ok") is not True, repr(second_state)[:220])
    cw2 = (resolve(bridge, b_ref).get("current_writer") or {})
    check("repeated handoff keeps one current writer", cw2 == b_claim,
          f"current={cw2!r} target={b_claim!r}")

    # 4. Restart over the same state.
    daemon2, bridge2, bindings2, router2, ctx2 = build(root)
    a_state2 = (resolve(bridge2, a_ref).get("binding") or {}).get("state")
    b_claim3 = (resolve(bridge2, b_ref).get("binding") or {}).get("writer")
    cw3 = (resolve(bridge2, b_ref).get("current_writer") or {})
    check("restart preserves source CLOSED", a_state2 == "CLOSED", str(a_state2))
    check("restart preserves the transferred target claim",
          b_claim3 == b_claim, f"{b_claim3!r} != {b_claim!r}")
    check("restart keeps the target current", cw3 == b_claim,
          f"current={cw3!r}")
    stale = send(router2, ctx2, a_ref, "stale-after-restart")
    check("restart fences the stale source", not stale.ok,
          f"ok={stale.ok} reason={stale.reason}")
    target = send(router2, ctx2, b_ref, "target-after-restart")
    check("restart admits the target", target.ok, target.reason or "")

    print("OBSERVE cross-device: NOT_RUN (no second enrolled device/provider)")

failed = [n for n, ok, _ in results if not ok]
print(json.dumps({"checks": len(results), "failed": failed,
                  "cross_device": "NOT_RUN"}, ensure_ascii=False))
sys.exit(1 if failed else 0)
