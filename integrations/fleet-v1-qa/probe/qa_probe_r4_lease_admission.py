#!/usr/bin/env python3
"""Q1 round-4 probe: aware-instant admission for claim and Work Lease expiry.

Two sections:

A. `coordination.writer_axis` with a fixed-width Z clock and a claim whose
   `expires_at` is: past +08:00, past Z, future +08:00, future Z, malformed,
   naive-with-a-later-text. Expected-safe: expired/malformed/naive => EXPIRED;
   future => CURRENT.

B. The REAL send admission (DaemonBindings + router + fake provider): the
   `leases` row is written with the same spellings; the binding claim is
   agent:A/1. Expected-safe: an expired/malformed/unreadable lease => send
   refused, provider.send NOT called; a live lease => send admitted once.

Run: QA_TREE=<candidate> python3 qa_probe_r4_lease_admission.py
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
NOW_Z = "2026-10-20T06:55:00Z"
NOW_EPOCH = datetime.datetime(
    2026, 10, 20, 6, 55, 0, tzinfo=datetime.timezone.utc).timestamp()
PLUS8 = datetime.timezone(datetime.timedelta(hours=8))
results = []


def check(name, condition, detail=""):
    results.append((name, bool(condition), detail))
    print(f"{'PASS' if condition else 'FAIL'} {name}"
          + (f" :: {detail}" if detail else ""))


def z(epoch):
    return datetime.datetime.fromtimestamp(
        epoch, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def plus8(epoch):
    return datetime.datetime.fromtimestamp(epoch, PLUS8).isoformat()


# ---------------------------------------------------------------------------
# A. writer_axis
# ---------------------------------------------------------------------------
CLAIM_CASES = {
    "past_plus8": (plus8(NOW_EPOCH - 3600), "EXPIRED"),
    "past_z": (z(NOW_EPOCH - 3600), "EXPIRED"),
    "future_plus8": (plus8(NOW_EPOCH + 3600), "CURRENT"),
    "future_z": (z(NOW_EPOCH + 3600), "CURRENT"),
    "malformed": ("not-a-time", "EXPIRED"),
    "naive_later_text": ("2026-10-20T13:55:00", "EXPIRED"),
}
for label, (expires, expected) in CLAIM_CASES.items():
    record = {"schema": "serveros/thread-binding.v2",
              "session_ref": "session:q1:A", "session_role": "execution",
              "bound_item_ref": ITEM, "bound_item_ref_frozen": True,
              "state": "OPEN",
              "writer": {"owner": "agent:A", "epoch": 1, "token": "1",
                         "expires_at": expires}}
    axis = coordination.writer_axis(
        record, current_writer=record["writer"], now=NOW_Z)
    check(f"writer_axis {label}: expected {expected}", axis == expected,
          f"expires={expires!r} axis={axis!r}")


# ---------------------------------------------------------------------------
# B. real send admission through the daemon/router
# ---------------------------------------------------------------------------
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


def set_lease(root, expires, owner="agent:A", token="1"):
    conn = sqlite3.connect(root / "serveros.db")
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS leases (resource TEXT PRIMARY KEY, "
                     "owner TEXT NOT NULL, token TEXT NOT NULL, "
                     "expires_at TEXT NOT NULL)")
        conn.execute("INSERT OR REPLACE INTO leases VALUES (?,?,?,?)",
                     (f"work-write:{ITEM}", owner, str(token), expires))
        conn.commit()
    finally:
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
        "conversation_ref": "oc_q1_lease_admission",
        "message_ref": "om_q1_lease_admission",
        "observed_at": now, "provider_status": "VERIFIED",
        "provenance": "FACT", "actor_ref": "human:q1",
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
    return daemon, bridge, service, router, context["context_ref"]


def request(action, **extra):
    payload = {"action": action, "device_ref": LOCAL, "agent_runtime": RUNTIME}
    payload.update(extra)
    return router_mod.request_from_payload(payload)


LEASE_CASES = {
    "past_plus8": (plus8(time.time() - 600), "refused"),
    "past_z": (z(time.time() - 600), "refused"),
    "future_plus8": (plus8(time.time() + 600), "admitted"),
    "future_z": (z(time.time() + 600), "admitted"),
    "malformed": ("not-a-time", "refused"),
    "naive_later_text": (datetime.datetime.fromtimestamp(
        time.time() + 3600, datetime.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S"), "refused"),
}

for label, (expires, expected) in LEASE_CASES.items():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "run").mkdir()
        # A live lease first, so create A can mint the claim.
        set_lease(root, z(time.time() + 600))
        daemon, bridge, service, router, ctx = build(root)
        a = router.route(request(
            "agent.session.create", context_ref=ctx, session_role="execution",
            bound_item_ref=ITEM,
            params={"agent_id": "agent:q1", "instruction": "start",
                    "idempotency_key": "a"}))
        a_ref = (a.result or {}).get("session_ref")
        check(f"lease {label}: fixture create A", a.ok and bool(a_ref),
              f"ok={a.ok} reason={a.reason}")
        # Replace the lease row with the case spelling; owner/token unchanged.
        set_lease(root, expires)
        before = len([c for c in service.calls if c[0] == "send"])
        answer = router.route(request(
            "agent.session.send", context_ref=ctx, session_ref=a_ref,
            params={"instruction": "more", "turn_idempotency_key": "t"}))
        after = len([c for c in service.calls if c[0] == "send"])
        if expected == "admitted":
            check(f"lease {label}: send admitted", answer.ok,
                  f"ok={answer.ok} reason={answer.reason}")
            check(f"lease {label}: provider.send called once", after == before + 1,
                  f"before={before} after={after}")
        else:
            check(f"lease {label}: send refused", not answer.ok,
                  f"ok={answer.ok} reason={answer.reason}")
            check(f"lease {label}: provider.send NOT called", after == before,
                  f"before={before} after={after}")

failed = [n for n, ok, _ in results if not ok]
print(json.dumps({"checks": len(results), "failed": failed},
                 ensure_ascii=False))
sys.exit(1 if failed else 0)
