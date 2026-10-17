#!/usr/bin/env python3
"""Independent QA Snapshot C — #252 fdc223f at the REAL daemon boundary.

Loads every module from the exact extracted tree (QA_TREE). Builds the daemon
in-process with a throwaway state dir (no socket, no network). Exercises
`agent.dispatch.record` / the store / the kernel with MUST-NOT-PASS negatives.

Exit 0 = every negative case was correctly refused (and positives held).
Exit 1 = at least one MUST-NOT-PASS was violated. The report says which.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

TREE = Path(os.environ["QA_TREE"]).resolve()
SCRATCH = Path(os.environ["QA_SCRATCH"]).resolve()
RUNTIME = TREE / "runtime"

failures: list[str] = []


def load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


attempts = load("serveros_runtime_dispatch_attempts", RUNTIME / "dispatch_attempts.py")
store_mod = load("serveros_runtime_agent_control_store", RUNTIME / "agent_control_store.py")
daemon_mod = load("serveros_runtime_daemon", RUNTIME / "daemon.py")
registry_mod = load("serveros_runtime_registry", RUNTIME / "registry.py")
actor = load("serveros_context_actor", TREE / "context" / "actor.py")


def check(label: str, got, expected, *, security=False) -> None:
    ok = got == expected
    tag = "PASS" if ok else ("SECURITY_FAIL" if security else "FAIL")
    print(f"{tag} {label}: got={got!r} expected={expected!r}")
    if not ok:
        failures.append(label)


def a_registry():
    return registry_mod.Registry(document={
        "schema": "serveros/managed-runtime-registry.v1",
        "device_ref": "device:qa-c",
        "resources": [],
    })


SCRATCH.mkdir(parents=True, exist_ok=True)
daemon = daemon_mod.Daemon(
    a_registry(), state_dir=SCRATCH / "state",
    socket_path=SCRATCH / "run/serveros.sock", log=lambda message: None)


def trusted_context(actor_ref: str, *, age_seconds: int = 0):
    now_epoch = daemon._now() - age_seconds
    now_s = daemon_mod._iso(now_epoch)
    record = actor.build({
        "human_control_provider": "provider:botmux-human-control",
        "transport": "feishu",
        "transport_identity": "botmux-verified-enterprise-user",
        "conversation_ref": "oc_qa_c",
        "message_ref": "om_qa_c",
        "observed_at": now_s,
        "provider_status": "VERIFIED",
        "provenance": "FACT",
        "actor_ref": actor_ref,
        "provider_pid": 4242,
    }, now=now_s)
    daemon.control_store().put_context(record)
    return record


def record(context_ref, attempt):
    params = {"attempt": attempt}
    if context_ref is not None:
        params["context_ref"] = context_ref
    return daemon.handle({"method": "agent.dispatch.record",
                          "params": params})["result"]


def dg(*, session="session:one", runtime="agent-runtime:claude-code",
       principal="human:A", instruction="more"):
    return attempts.intent_digest(
        action="agent.session.send", device_ref="device:x", verb="session.send",
        params={"instruction": instruction}, workspace=None, work_item=None,
        session_ref=session, agent_runtime=runtime, principal=principal)


def attempt(key, digest, phase, **over):
    rec = attempts.attempt_record(
        key=key, action="agent.session.send", device_ref="device:x",
        verb="session.send", intent_digest=digest, phase=phase,
        session_ref="session:one", runtime="agent-runtime:claude-code")
    rec.update(over)
    return rec


CTX_A = trusted_context("human:A")
CTX_B = trusted_context("human:B")
store = daemon.control_store()

# D1 — positive control: the authentic dispatcher writes.
w = record(CTX_A["context_ref"], attempt("k1", dg(), attempts.CALL_NOT_SENT))
check("D1 authentic CALL_NOT_SENT recorded", w.get("recorded"), True)
check("D1 stored principal is the resolved actor",
      (w.get("attempt") or {}).get("principal"), "human:A")
check("D1 stored target is part of the record",
      ((w.get("attempt") or {}).get("session_ref"),
       (w.get("attempt") or {}).get("runtime")),
      ("session:one", "agent-runtime:claude-code"))

# D2 — same key, different session: the target is now part of the digest.
w = record(CTX_A["context_ref"],
           attempt("k1", dg(session="session:two"), attempts.CALL_NOT_SENT))
check("D2 same key + different session is refused", w.get("recorded"), False)
check("D2 refusal names the conflict", "conflict" in str(w.get("detail", "")).lower(), True)

# D3 — MUST-NOT-PASS: actor B settles actor A's attempt with a fake result.
# It copies A's digest; the daemon takes the principal from B's context and does
# not require the settler to be the original dispatcher.
forged = attempt("k1", dg(), attempts.SETTLED,
                 result={"ok": True, "provider_result": "fabricated"})
w = record(CTX_B["context_ref"], forged)
check("D3 cross-actor forged SETTLED is refused", w.get("recorded"), False,
      security=True)
if w.get("recorded"):
    check("D3 record now claims actor B and a fabricated result",
          ((w.get("attempt") or {}).get("principal"),
           (w.get("attempt") or {}).get("result")),
          ("human:B", {"ok": True, "provider_result": "fabricated"}))

# D4 — MUST-NOT-PASS: a terminal SETTLED can be moved back to CALL_NOT_SENT,
# after which the kernel says PROCEED (a retry is a second effect).
w = record(CTX_B["context_ref"], attempt("k1", dg(), attempts.CALL_NOT_SENT))
check("D4 terminal SETTLED cannot be reopened", w.get("recorded"), False,
      security=True)
if w.get("recorded") and w.get("attempt", {}).get("phase") == attempts.CALL_NOT_SENT:
    v = attempts.reconcile(store.get_dispatch_attempt("k1"), digest=dg())
    check("D4 reopened attempt authorizes PROCEED", v["verdict"], attempts.PROCEED,
          security=True)

# D5 — a settle with no provider answer is refused (positive).
record(CTX_A["context_ref"], attempt("k2", dg(instruction="k2"),
                                     attempts.CALL_NOT_SENT))
w = record(CTX_A["context_ref"], attempt("k2", dg(instruction="k2"),
                                         attempts.SETTLED))
check("D5 settle without a result is refused", w.get("recorded"), False)
check("D5 reason is INVALID_CONTEXT", w.get("reason"), "INVALID_CONTEXT")

# D6/D7/D8 — no context, expired context, self-asserted principal.
w = record(None, attempt("k3", dg(instruction="k3"), attempts.CALL_NOT_SENT))
check("D6 no trusted context is refused", w.get("reason"), "UNTRUSTED_ACTOR")
window = int(actor.contract()["freshness"]["window_seconds"])
old = trusted_context("human:OLD", age_seconds=window + 60)
w = record(old["context_ref"], attempt("k4", dg(instruction="k4"),
                                       attempts.CALL_NOT_SENT))
check("D7 expired context is refused", w.get("reason"), "UNTRUSTED_ACTOR")
w = record(CTX_A["context_ref"], attempt("k5", dg(instruction="k5"),
                                         attempts.CALL_NOT_SENT,
                                         principal="human:B"))
check("D8 self-asserted principal is refused", w.get("reason"), "UNTRUSTED_ACTOR")

# D9 — cross-router/restart retry reads the durable record and fences.
record(CTX_A["context_ref"], attempt("k6", dg(instruction="k6"), attempts.CALL_NOT_SENT))
record(CTX_A["context_ref"], attempt("k6", dg(instruction="k6"), attempts.SETTLED,
                                     result={"ok": True}))
record(CTX_A["context_ref"], attempt("k7", dg(instruction="k7"),
                                     attempts.EFFECT_UNKNOWN))
store2 = store_mod.AgentControlStore(store.path)
settled = store2.get_dispatch_attempt("k6")
unknown = store2.get_dispatch_attempt("k7")
check("D9a a restarted reader sees SETTLED",
      (settled or {}).get("phase"), attempts.SETTLED)
check("D9b SETTLED retry replays",
      attempts.reconcile(settled, digest=dg(instruction="k6"))["verdict"],
      attempts.REPLAY)
check("D9c EFFECT_UNKNOWN retry refuses",
      attempts.reconcile(unknown, digest=dg(instruction="k7"))["verdict"],
      attempts.REFUSE_UNKNOWN)

# D10 — MUST-NOT-PASS: missing/empty current writer must not settle.
rec = attempts.attempt_record(
    key="kw", action="agent.session.send", device_ref="device:x",
    verb="session.send", intent_digest=dg(instruction="kw"),
    phase=attempts.EFFECT_UNKNOWN, work_item="SRV-0001", principal="human:A")
v = attempts.reconcile_observation(
    rec, observation={"intent_digest": dg(instruction="kw"), "state": "APPLIED",
                      "source": "provider:readback"},
    reconciler={"principal": "human:A"}, current_writer={})
check("D10 empty current writer is refused", v["verdict"],
      attempts.REFUSE_STALE_WRITER, security=True)

# D11 — an ambiguous provider outcome stays EFFECT_UNKNOWN.
check("D11 timeout becomes EFFECT_UNKNOWN",
      attempts.phase_after_transport_failure("TIMEOUT"), attempts.EFFECT_UNKNOWN)
rec = attempts.attempt_record(
    key="kx", action="agent.session.send", device_ref="device:x",
    verb="session.send", intent_digest=dg(instruction="kx"),
    phase=attempts.EFFECT_UNKNOWN, principal="human:A")
check("D11 unknown effect refuses a retry",
      attempts.reconcile(rec, digest=dg(instruction="kx"))["verdict"],
      attempts.REFUSE_UNKNOWN)

# D12/D13 — the target and principal axes are conflicts, not replays.
rec = attempts.attempt_record(
    key="ky", action="agent.session.send", device_ref="device:x",
    verb="session.send", intent_digest=dg(instruction="ky"),
    phase=attempts.SETTLED, principal="human:A", result={"ok": True})
check("D12 same key + different session is CONFLICT",
      attempts.reconcile(rec, digest=dg(instruction="ky", session="session:two"))["verdict"],
      attempts.CONFLICT)
check("D13 same key + different principal is CONFLICT",
      attempts.reconcile(rec, digest=dg(instruction="ky", principal="human:B"))["verdict"],
      attempts.CONFLICT)
check("D13b same key + different runtime is CONFLICT",
      attempts.reconcile(rec, digest=dg(instruction="ky",
                                        runtime="agent-runtime:codex"))["verdict"],
      attempts.CONFLICT)

# D14 — a delayed observation cannot override a terminal answer.
v = attempts.reconcile_observation(
    rec, observation={"intent_digest": dg(instruction="ky"), "state": "APPLIED",
                      "source": "provider:late"},
    reconciler={"principal": "human:A", "owner": "human:A", "epoch": 1, "token": "1"},
    current_writer={"owner": "human:A", "epoch": 1, "token": "1"})
check("D14 late observation after SETTLED is refused", v["verdict"],
      attempts.REFUSE_NOT_UNKNOWN)

print()
print("scratch:", SCRATCH)
print("stored attempts:", json.dumps(
    {k: {"phase": v.get("phase"), "principal": v.get("principal")}
     for k, v in store.dispatch_attempts().items()}, sort_keys=True))
if failures:
    print(f"PROBE FAILED: {len(failures)} check(s): {failures}")
    sys.exit(1)
print("PROBE OK: every negative case refused and every positive control held")
