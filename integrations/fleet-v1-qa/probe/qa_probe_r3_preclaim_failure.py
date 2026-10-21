#!/usr/bin/env python3
"""Q1 final-gate probe: preclaim succeeds, then the target route or the close
fails. What state is left, and is the current writer unique?

Scenarios on the real graph (real DaemonBindings + DaemonContexts + loopback +
router + temp Daemon/SQLite; own fake provider):

  A. target provider resume returns no session_ref (handoff fails after
     `target_ready`, before `context_handed_off`);
  B. target provider resume raises an exception;
  C. the close call disconnects after the target was preclaimed.

For each: handoff ok/reason/completed, source/target binding state and claim,
the unique current writer, and whether source/target sends are admitted.
A failed handoff must not report success; the probe also records whether it
left an orphan target claim that moved authority anyway.

Run: QA_TREE=<candidate> python3 qa_probe_r3_preclaim_failure.py
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
    def __init__(self, provider_id):
        self.provider_id = provider_id
        self._support = {a: {"state": "SUPPORTED"}
                         for a in sessions_mod.contract()["support"]["actions"]}

    def actions(self):
        return dict(self._support)


class Service:
    def __init__(self, *, resume_mode="ok"):
        self.calls = []
        self.providers = {PROVIDER: Provider(PROVIDER)}
        self.resume_mode = resume_mode
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
        self.calls.append(("resume", kwargs))
        if self.resume_mode == "empty":
            return {}
        if self.resume_mode == "raise":
            raise RuntimeError("provider resume failed")
        return {"session_ref": kwargs.get("session_ref_value") or "session:q1:0",
                "action": "resume"}

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


class FlakyClose:
    """Delegates everything except close(), which disconnects."""

    def __init__(self, inner):
        self._inner = inner

    def get(self, ref):
        return self._inner.get(ref)

    def put(self, record):
        return self._inner.put(record)

    def claim(self, *args, **kwargs):
        return self._inner.claim(*args, **kwargs)

    def close(self, *args, **kwargs):
        raise RuntimeError("daemon disconnected during close")


def stamp(epoch):
    return datetime.datetime.fromtimestamp(
        epoch, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def set_lease(root, item, owner, token, ttl=600):
    conn = sqlite3.connect(root / "serveros.db")
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS leases (resource TEXT PRIMARY KEY, "
                     "owner TEXT NOT NULL, token TEXT NOT NULL, "
                     "expires_at TEXT NOT NULL)")
        conn.execute("INSERT OR REPLACE INTO leases VALUES (?,?,?,?)",
                     (f"work-write:{item}", owner, str(token),
                      stamp(time.time() + ttl)))
        conn.commit()
    finally:
        conn.close()


def build(root, *, resume_mode="ok", flaky_close=False):
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
        "conversation_ref": "oc_q1_preclaim",
        "message_ref": "om_q1_preclaim",
        "observed_at": now, "provider_status": "VERIFIED",
        "provenance": "FACT", "actor_ref": "human:q1",
        "provider_pid": 4242,
    }, now=now)
    daemon.control_store().put_context(context)
    bindings = agent_mcp.DaemonBindings(bridge)
    service = Service(resume_mode=resume_mode)
    handle = FlakyClose(bindings) if flaky_close else bindings
    loopback = router_mod.LocalLoopbackTransport(
        device_ref=LOCAL, service=service, bindings=handle)
    router = router_mod.FleetAgentControlRouter(
        local_device_ref=LOCAL, transports=[loopback],
        context_store=agent_mcp.DaemonContexts(bridge))
    return daemon, bridge, router, context["context_ref"]


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


def current(bridge, ref):
    return resolve(bridge, ref).get("current_writer") or {}


def run_scenario(label, *, resume_mode="empty", flaky_close=False):
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "run").mkdir()
        set_lease(root, ITEM, "agent:A", "1")
        daemon, bridge, router, ctx = build(
            root, resume_mode=resume_mode, flaky_close=flaky_close)
        a = create(router, ctx, "a")
        a_ref = (a.result or {}).get("session_ref")
        b = create(router, ctx, "b")
        b_ref = (b.result or {}).get("session_ref")
        check(f"{label}: fixture source/target created", a.ok and b.ok,
              f"a={a.reason} b={b.reason}")
        # The owner moves the Work lease to the target principal.
        set_lease(root, ITEM, "agent:B", "2")

        handoff = request(
            "agent.session.handoff", context_ref=ctx, session_ref=a_ref,
            params={"checkpoint": f"{ITEM}#1"})
        resume = request("agent.session.resume", context_ref=ctx,
                         session_ref=b_ref)
        try:
            outcome = router.handoff(handoff, create_request=resume)
            handled = {"ok": outcome.ok, "reason": outcome.reason,
                       "completed": list(outcome.completed),
                       "stopped_at": outcome.stopped_at,
                       "source_preserved": outcome.source_preserved,
                       "detail": outcome.detail}
        except Exception as exc:  # noqa: BLE001
            outcome = None
            handled = {"raised": type(exc).__name__, "message": str(exc)[:160]}

        a_read = resolve(bridge, a_ref)
        b_read = resolve(bridge, b_ref)
        a_state = (a_read.get("binding") or {}).get("state")
        a_writer = (a_read.get("binding") or {}).get("writer")
        b_state = (b_read.get("binding") or {}).get("state")
        b_writer = (b_read.get("binding") or {}).get("writer")
        cw = current(bridge, a_ref)
        a_send = send(router, ctx, a_ref, "a-after")
        b_send = send(router, ctx, b_ref, "b-after")

        print(f"STATE {label}: " + json.dumps({
            "handoff": handled,
            "source": {"state": a_state, "writer": a_writer,
                       "send_ok": a_send.ok, "send_reason": a_send.reason},
            "target": {"state": b_state, "writer": b_writer,
                       "send_ok": b_send.ok, "send_reason": b_send.reason},
            "current_writer": cw,
        }, ensure_ascii=False, default=str)[:1600])

        check(f"{label}: handoff does not report success",
              not (handled.get("ok") is True), repr(handled)[:200])
        check(f"{label}: current writer is unique (one owner/token)",
              bool(cw.get("owner")) and bool(cw.get("token")),
              repr(cw))
        if handled.get("raised"):
            # A provider exception after the pre-claim must be reported as a
            # typed outcome, not escape the handoff.
            check(f"{label}: a provider exception is a typed BLOCKED_ATOMICITY "
                  f"outcome, not an escaping exception", False,
                  f"raised={handled.get('raised')!r} "
                  f"current={cw!r} target_writer={b_writer!r}")
        else:
            moved = (str(cw.get("owner")) == str((b_writer or {}).get("owner"))
                     and str(cw.get("token")) == str((b_writer or {}).get("token")))
            detail = handled.get("detail") or {}
            check(f"{label}: a post-preclaim failure reports BLOCKED_ATOMICITY",
                  handled.get("reason") == "BLOCKED_ATOMICITY",
                  f"reason={handled.get('reason')!r}")
            check(f"{label}: the report does not claim the source was preserved "
                  f"while it is not the current writer",
                  moved and handled.get("source_preserved") is not True,
                  f"moved={moved} source_preserved="
                  f"{handled.get('source_preserved')!r}")
            check(f"{label}: the stalled scope is reported",
                  detail.get("scope_stalled") is True,
                  f"scope_stalled={detail.get('scope_stalled')!r} "
                  f"detail_keys={sorted(detail)}")
            check(f"{label}: failure leaves one current writer (the target)",
                  moved, f"current={cw!r} target_writer={b_writer!r}")


run_scenario("A-empty-resume", resume_mode="empty")
run_scenario("B-resume-raises", resume_mode="raise")
run_scenario("C-close-disconnects", resume_mode="ok", flaky_close=True)

failed = [n for n, ok, _ in results if not ok]
print(json.dumps({"checks": len(results), "failed": failed},
                 ensure_ascii=False))
sys.exit(1 if failed else 0)
