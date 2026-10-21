#!/usr/bin/env python3
"""Q1 independent harness: REAL DaemonBindings + Router + temp Daemon/SQLite.

Does not import any author test module. It builds its own fake provider
service, request payloads and context record, and wires the production
`mcp.agent_mcp.DaemonBindings` / `DaemonContexts` into the production
`LocalLoopbackTransport` and `FleetAgentControlRouter`, with a real `Daemon`
over a temporary state directory and its real JSON/SQLite stores.

Two fixture shapes are supported, detected from the candidate tree:

  * OLD (#241): the writer claim is minted by `coordination.mint_writer` with
    owner = session_ref; no Work lease is required.
  * LEASE-BACKED (new S1): the claim is minted from a live `work-write:<Item>`
    row in `<root>/serveros.db`, and admission checks the claim against that
    lease; the owner must move the lease to the target before the handoff.

Named checks:

  WIRE  the production bindings handle exposes `close`
  T1    after handoff A->B, a send on the stale source A is refused
  T1b   the target B has its own claim, is current_writer, and may send
  T2    while A owns the scope, a send on B is refused; A is admitted
  T7    the production handle / record RPC does not mint a caller-chosen writer
  S2    a handoff to a new create target reaches context_handed_off and
        transfers the same way

Run: QA_TREE=<candidate worktree> python3 qa_probe_r2_handoff.py
Exit 0 only when every expected-safe assertion holds; a non-zero exit is the
RED receipt, not a harness failure.
"""
from __future__ import annotations

import datetime
import inspect
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
ITEM2 = "LIT-0202"
results = []


def check(name, condition, detail=""):
    results.append((name, bool(condition), detail))
    print(f"{'PASS' if condition else 'FAIL'} {name}"
          + (f" :: {detail}" if detail else ""))


class Provider:
    def __init__(self, provider_id):
        self.provider_id = provider_id
        self._support = {
            action: {"state": "SUPPORTED"}
            for action in sessions_mod.contract()["support"]["actions"]}

    def actions(self):
        return dict(self._support)


class Service:
    def __init__(self):
        self.calls = []
        self.providers = {PROVIDER: Provider(PROVIDER)}
        self._seq = 0

    def readiness(self, provider_id):
        now = datetime.datetime.now(datetime.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
        return {
            "schema": "serveros/provider-instance.v1",
            "provider": provider_id,
            "device_ref": LOCAL,
            "observed_at": now,
            "status": "AVAILABLE",
            "status_provenance": "INFERRED",
            "reason": None,
            "reason_text": None,
        }

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


def lease_stamp(epoch):
    return datetime.datetime.fromtimestamp(
        epoch, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def set_lease(root, item, owner, token, ttl=600):
    conn = sqlite3.connect(root / "serveros.db")
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS leases (resource TEXT PRIMARY KEY, "
            "owner TEXT NOT NULL, token TEXT NOT NULL, expires_at TEXT NOT NULL)")
        conn.execute(
            "INSERT OR REPLACE INTO leases VALUES (?,?,?,?)",
            (f"work-write:{item}", owner, str(token),
             lease_stamp(time.time() + ttl)))
        conn.commit()
    finally:
        conn.close()


def build(tmp):
    registry = registry_mod.Registry(document={
        "schema": "serveros/managed-runtime-registry.v1",
        "device_ref": LOCAL,
        "resources": [],
    })
    daemon = daemon_mod.Daemon(
        registry, state_dir=tmp / "state",
        socket_path=tmp / "run/serveros.sock", log=lambda message: None)
    bridge = CallAdapter(daemon)
    now = daemon_mod._iso(daemon._now())
    context = actor_mod.build({
        "human_control_provider": "provider:botmux-human-control",
        "transport": "feishu",
        "transport_identity": "botmux-verified-enterprise-user",
        "conversation_ref": "oc_q1_harness",
        "message_ref": "om_q1_harness",
        "observed_at": now,
        "provider_status": "VERIFIED",
        "provenance": "FACT",
        "actor_ref": "human:q1",
        "provider_pid": 4242,
    }, now=now)
    daemon.control_store().put_context(context)
    service = Service()
    bindings = agent_mcp.DaemonBindings(bridge)
    loopback = router_mod.LocalLoopbackTransport(
        device_ref=LOCAL, service=service, bindings=bindings)
    router = router_mod.FleetAgentControlRouter(
        local_device_ref=LOCAL, transports=[loopback],
        context_store=agent_mcp.DaemonContexts(bridge))
    return daemon, bridge, bindings, router, context["context_ref"]


def request(action, **extra):
    payload = {"action": action, "device_ref": LOCAL, "agent_runtime": RUNTIME}
    payload.update(extra)
    return router_mod.request_from_payload(payload)


def create(router, ctx, session_role="execution", item=ITEM, key="k"):
    return router.route(request(
        "agent.session.create", context_ref=ctx, session_role=session_role,
        bound_item_ref=item,
        params={"agent_id": "agent:q1", "instruction": "start",
                "idempotency_key": key}))


def send(router, ctx, ref, key="t"):
    return router.route(request(
        "agent.session.send", context_ref=ctx, session_ref=ref,
        params={"instruction": "more", "turn_idempotency_key": key}))


def resume_request(ctx, ref):
    return request("agent.session.resume", context_ref=ctx, session_ref=ref)


def resolve(bridge, ref):
    return bridge.call("agent.binding.resolve", {"session_ref": ref})


def current_of(bridge, ref):
    return (resolve(bridge, ref).get("current_writer") or {})


with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)
    daemon, bridge, bindings, router, ctx = build(tmp)
    lease_backed = ("authority" in inspect.signature(
        coordination.admit_work).parameters)
    print(f"OBSERVE admission authority parameter: {lease_backed}")

    has_close = hasattr(bindings, "close")
    check("WIRE production DaemonBindings exposes close", has_close,
          f"close={has_close}")

    # ---- Scenario 1: handoff to an existing target (resume) ----
    if lease_backed:
        set_lease(tmp, ITEM, "agent:A", "1")
    a_resp = create(router, ctx, key="a")
    check("create A succeeds", a_resp.ok, a_resp.reason or "")
    a_ref = (a_resp.result or {}).get("session_ref")
    b_resp = create(router, ctx, key="b")
    check("create B succeeds", b_resp.ok, b_resp.reason or "")
    b_ref = (b_resp.result or {}).get("session_ref")

    a_claim = (resolve(bridge, a_ref).get("binding") or {}).get("writer")
    b_claim = (resolve(bridge, b_ref).get("binding") or {}).get("writer")
    check("A records a writer claim", bool(a_claim), repr(a_claim))
    check("B records no claim on an owned scope", b_claim in (None, {}),
          repr(b_claim))

    b_send = send(router, ctx, b_ref, key="b-before")
    check("T2 B is refused while A owns the scope",
          (not b_send.ok) and b_send.reason == "NOT_CURRENT_WRITER",
          f"ok={b_send.ok} reason={b_send.reason}")
    a_send = send(router, ctx, a_ref, key="a-before")
    check("T2 A is admitted while it owns the scope", a_send.ok,
          a_send.reason or "")

    if lease_backed:
        # The owner moves the Work lease to the target principal before the
        # handoff; the daemon mints the target's claim from THIS lease.
        set_lease(tmp, ITEM, "agent:B", "2")
    handoff_req = request(
        "agent.session.handoff", context_ref=ctx, session_ref=a_ref,
        params={"checkpoint": f"{ITEM}#1"})
    outcome = router.handoff(handoff_req,
                             create_request=resume_request(ctx, b_ref))
    check("handoff A->B completes", outcome.ok,
          f"ok={outcome.ok} reason={outcome.reason} "
          f"completed={getattr(outcome,'completed',None)}")

    a_state = ((resolve(bridge, a_ref).get("binding") or {}).get("state"))
    check("T1 source A binding is CLOSED after handoff", a_state == "CLOSED",
          f"state={a_state}")
    stale = send(router, ctx, a_ref, key="a-after")
    check("T1 stale A send is refused after handoff", not stale.ok,
          f"ok={stale.ok} reason={stale.reason}")

    b_after = resolve(bridge, b_ref)
    b_claim_after = (b_after.get("binding") or {}).get("writer")
    b_current = b_after.get("current_writer") or {}
    check("T1b target B has its own writer claim after handoff",
          bool(b_claim_after), repr(b_claim_after))
    check("T1b target B is the current writer after handoff",
          bool(b_claim_after)
          and str(b_current.get("owner")) == str(b_claim_after.get("owner"))
          and str(b_current.get("token")) == str(b_claim_after.get("token")),
          f"b_ref={b_ref} claim={b_claim_after!r} current={b_current!r}")
    b_after_send = send(router, ctx, b_ref, key="b-after")
    check("T1b target B may send after handoff", b_after_send.ok,
          b_after_send.reason or "")

    # ---- T7: caller-chosen writer ----
    try:
        bindings.put({"session_ref": "session:q1:forged",
                      "session_role": "execution",
                      "bound_item_ref": "LIT-0777",
                      "writer": {"owner": "victim", "epoch": 99, "token": 999}})
    except Exception:
        pass
    forged_claim = (resolve(bridge, "session:q1:forged").get("binding")
                    or {}).get("writer") or {}
    check("T7 production handle does not forward a caller-chosen writer",
          forged_claim.get("owner") != "victim"
          and str(forged_claim.get("token")) != "999",
          f"claim={forged_claim!r}")
    direct = bridge.call("agent.binding.record", {
        "session_ref": "session:q1:direct", "session_role": "execution",
        "bound_item_ref": "LIT-0888",
        "writer": {"owner": "victim", "epoch": 99, "token": 999}})
    direct_claim = (direct.get("binding") or {}).get("writer")
    print("OBSERVE direct agent.binding.record =",
          json.dumps({k: direct.get(k) for k in
                      ("recorded", "reason", "binding")})[:400])
    if lease_backed:
        check("T7 record RPC refuses a caller-chosen writer",
              direct.get("recorded") is False
              and direct.get("reason") == "UNTRUSTED_ACTOR",
              repr(direct.get("reason")))

    # ---- Scenario 2: handoff to a NEW create target ----
    if lease_backed:
        set_lease(tmp, ITEM2, "agent:C", "1")
    c_resp = create(router, ctx, item=ITEM2, key="c")
    check("S2 create source C succeeds", c_resp.ok, c_resp.reason or "")
    c_ref = (c_resp.result or {}).get("session_ref")
    c_claim = ((resolve(bridge, c_ref).get("binding") or {}).get("writer"))
    check("S2 source C owns the scope", bool(c_claim), repr(c_claim))

    if lease_backed:
        set_lease(tmp, ITEM2, "agent:D", "2")
    handoff2 = request(
        "agent.session.handoff", context_ref=ctx, session_ref=c_ref,
        params={"checkpoint": f"{ITEM2}#1"})
    target_create = request(
        "agent.session.create", context_ref=ctx, session_role="execution",
        bound_item_ref=ITEM2,
        params={"agent_id": "agent:q1:target", "instruction": "take over",
                "idempotency_key": "handoff-target"})
    out2 = router.handoff(handoff2, create_request=target_create)
    target_ref = ((out2.detail or {}).get("target") or {}).get("session_ref")
    check("S2 handoff C->new target reaches context_handed_off",
          out2.ok and "context_handed_off" in out2.completed,
          f"ok={out2.ok} reason={out2.reason} completed={out2.completed}")
    check("S2 a target session ref is named", bool(target_ref), repr(target_ref))

    if target_ref:
        c_state = (resolve(bridge, c_ref).get("binding") or {}).get("state")
        c_current = current_of(bridge, c_ref)
        check("S2 source C binding is CLOSED after handoff", c_state == "CLOSED",
              f"state={c_state}")
        check("S2 source C is no longer current_writer",
              str(c_current.get("owner")) != str((c_claim or {}).get("owner")),
              f"current={c_current!r}")
        c_send = send(router, ctx, c_ref, key="c-after")
        check("S2 stale C send is refused after handoff", not c_send.ok,
              f"ok={c_send.ok} reason={c_send.reason}")

        t_after = resolve(bridge, target_ref)
        t_claim = (t_after.get("binding") or {}).get("writer")
        t_current = t_after.get("current_writer") or {}
        check("S2 target has its own writer claim after handoff",
              bool(t_claim), repr(t_claim))
        check("S2 target is the current_writer after handoff",
              bool(t_claim)
              and str(t_current.get("owner")) == str(t_claim.get("owner"))
              and str(t_current.get("token")) == str(t_claim.get("token")),
              f"target={target_ref} claim={t_claim!r} current={t_current!r}")
        t_send = send(router, ctx, target_ref, key="target-after")
        check("S2 target may send after handoff", t_send.ok, t_send.reason or "")

failed = [name for name, ok, _ in results if not ok]
print(json.dumps({"checks": len(results), "failed": failed,
                  "lease_backed": lease_backed}, ensure_ascii=False))
sys.exit(1 if failed else 0)
