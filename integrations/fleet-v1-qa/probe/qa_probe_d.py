#!/usr/bin/env python3
"""Independent QA Snapshot D — S2 #252 at QA_TREE's exact commit.

Same MUST-NOT-PASS suite as Snapshot C, corrected where C's D4 precondition
depended on D3's forged settle (D4 now performs an AUTHENTIC settle first).

Run against TWO trees in one command:
  * fdc223f (previous head) -> expected exit 1 (red control: D3/D4/D10 accepted)
  * 7a87e5c (claimed fix)   -> expected exit 0 (all negatives refused)

LOCAL only. Contexts are synthesized through actor.build; no provider, device,
network, credential or production call. Exit 1 means a MUST-NOT-PASS was
accepted (or a positive control failed).
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
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


SCRATCH.mkdir(parents=True, exist_ok=True)
daemon = daemon_mod.Daemon(
    registry_mod.Registry(document={
        "schema": "serveros/managed-runtime-registry.v1",
        "device_ref": "device:qa-d", "resources": []}),
    state_dir=SCRATCH / "state", socket_path=SCRATCH / "run/serveros.sock",
    log=lambda message: None)


def trusted_context(actor_ref: str, *, age_seconds: int = 0):
    now_s = daemon_mod._iso(daemon._now() - age_seconds)
    record = actor.build({
        "human_control_provider": "provider:botmux-human-control",
        "transport": "feishu",
        "transport_identity": "botmux-verified-enterprise-user",
        "conversation_ref": "oc_qa_d", "message_ref": "om_qa_d",
        "observed_at": now_s, "provider_status": "VERIFIED",
        "provenance": "FACT", "actor_ref": actor_ref, "provider_pid": 4242,
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

# D2 — same key, different session/runtime/principal at the daemon write point.
w = record(CTX_A["context_ref"],
           attempt("k1", dg(session="session:two"), attempts.CALL_NOT_SENT))
check("D2 same key + different session refused", w.get("recorded"), False)
check("D2 refusal names the conflict", "conflict" in str(w.get("detail", "")).lower(), True)
w = record(CTX_A["context_ref"],
           attempt("k1", dg(runtime="agent-runtime:codex"), attempts.CALL_NOT_SENT))
check("D2b same key + different runtime refused", w.get("recorded"), False)
w = record(CTX_A["context_ref"],
           attempt("k1", dg(principal="human:B"), attempts.CALL_NOT_SENT))
check("D2c same key + different principal refused", w.get("recorded"), False)

# D3 — MUST-NOT-PASS: actor B settles actor A's attempt with a fabricated result.
# B copies A's digest; the settler is not the dispatcher.
w = record(CTX_B["context_ref"],
           attempt("k1", dg(), attempts.SETTLED,
                   result={"ok": True, "provider_result": "fabricated"}))
check("D3 cross-actor forged SETTLED refused", w.get("recorded"), False,
      security=True)
check("D3 reason is UNTRUSTED_ACTOR", w.get("reason"), "UNTRUSTED_ACTOR")
check("D3 attempt unchanged after refusal",
      (store.get_dispatch_attempt("k1") or {}).get("phase"),
      attempts.CALL_NOT_SENT)
check("D3 attempt principal unchanged",
      (store.get_dispatch_attempt("k1") or {}).get("principal"), "human:A")

# D4 — terminal is terminal, on an AUTHENTIC settle (independent of D3).
record(CTX_A["context_ref"], attempt("k1b", dg(instruction="k1b"),
                                     attempts.CALL_NOT_SENT))
w = record(CTX_A["context_ref"], attempt("k1b", dg(instruction="k1b"),
                                         attempts.SETTLED, result={"ok": True}))
check("D4a authentic settle recorded", w.get("recorded"), True)
w = record(CTX_A["context_ref"], attempt("k1b", dg(instruction="k1b"),
                                         attempts.CALL_NOT_SENT))
check("D4b SETTLED -> CALL_NOT_SENT refused", w.get("recorded"), False,
      security=True)
w = record(CTX_A["context_ref"], attempt("k1b", dg(instruction="k1b"),
                                         attempts.EFFECT_UNKNOWN))
check("D4c SETTLED -> EFFECT_UNKNOWN refused", w.get("recorded"), False,
      security=True)
record(CTX_A["context_ref"], attempt("k1u", dg(instruction="k1u"),
                                     attempts.EFFECT_UNKNOWN))
w = record(CTX_A["context_ref"], attempt("k1u", dg(instruction="k1u"),
                                         attempts.CALL_NOT_SENT))
check("D4d EFFECT_UNKNOWN -> CALL_NOT_SENT refused", w.get("recorded"), False,
      security=True)
check("D4e terminal still replays, not proceeds",
      attempts.reconcile(store.get_dispatch_attempt("k1b"),
                         digest=dg(instruction="k1b"))["verdict"],
      attempts.REPLAY)

# D5 — a settle with no provider answer / no prior attempt is refused.
record(CTX_A["context_ref"], attempt("k2", dg(instruction="k2"),
                                     attempts.CALL_NOT_SENT))
w = record(CTX_A["context_ref"], attempt("k2", dg(instruction="k2"),
                                         attempts.SETTLED))
check("D5 settle without result refused", w.get("recorded"), False)
check("D5 reason is INVALID_CONTEXT", w.get("reason"), "INVALID_CONTEXT")
w = record(CTX_A["context_ref"], attempt("knever", dg(instruction="knever"),
                                         attempts.SETTLED, result={"ok": True}))
check("D5b settle with no prior attempt refused", w.get("recorded"), False)

# D6/D7/D8 — no context, expired context, self-asserted principal.
w = record(None, attempt("k3", dg(instruction="k3"), attempts.CALL_NOT_SENT))
check("D6 no trusted context refused", w.get("reason"), "UNTRUSTED_ACTOR")
window = int(actor.contract()["freshness"]["window_seconds"])
old = trusted_context("human:OLD", age_seconds=window + 60)
w = record(old["context_ref"], attempt("k4", dg(instruction="k4"),
                                       attempts.CALL_NOT_SENT))
check("D7 expired context refused", w.get("reason"), "UNTRUSTED_ACTOR")
w = record(CTX_A["context_ref"], attempt("k5", dg(instruction="k5"),
                                         attempts.CALL_NOT_SENT,
                                         principal="human:B"))
check("D8 self-asserted principal refused", w.get("reason"), "UNTRUSTED_ACTOR")

# D9 — cross-router/restart retry reads the durable record and fences.
record(CTX_A["context_ref"], attempt("k6", dg(instruction="k6"), attempts.CALL_NOT_SENT))
record(CTX_A["context_ref"], attempt("k6", dg(instruction="k6"), attempts.SETTLED,
                                     result={"ok": True}))
record(CTX_A["context_ref"], attempt("k7", dg(instruction="k7"),
                                     attempts.EFFECT_UNKNOWN))
store2 = store_mod.AgentControlStore(store.path)
check("D9a restarted reader sees SETTLED",
      (store2.get_dispatch_attempt("k6") or {}).get("phase"), attempts.SETTLED)
check("D9b SETTLED retry replays",
      attempts.reconcile(store2.get_dispatch_attempt("k6"),
                         digest=dg(instruction="k6"))["verdict"], attempts.REPLAY)
check("D9c EFFECT_UNKNOWN retry refuses",
      attempts.reconcile(store2.get_dispatch_attempt("k7"),
                         digest=dg(instruction="k7"))["verdict"],
      attempts.REFUSE_UNKNOWN)

# D10 — a missing writer identity is not a match, and no-principal is nobody.
rec = attempts.attempt_record(
    key="kw", action="agent.session.send", device_ref="device:x",
    verb="session.send", intent_digest=dg(instruction="kw"),
    phase=attempts.EFFECT_UNKNOWN, work_item="SRV-0001", principal="human:A")
v = attempts.reconcile_observation(
    rec, observation={"intent_digest": dg(instruction="kw"), "state": "APPLIED",
                      "source": "provider:readback"},
    reconciler={"principal": "human:A"}, current_writer={})
check("D10a empty current writer refused", v["verdict"],
      attempts.REFUSE_STALE_WRITER, security=True)
noprincipal = attempts.attempt_record(
    key="kn", action="agent.session.send", device_ref="device:x",
    verb="session.send", intent_digest=dg(instruction="kn"),
    phase=attempts.EFFECT_UNKNOWN, work_item="SRV-0001")
v = attempts.reconcile_observation(
    noprincipal, observation={"intent_digest": dg(instruction="kn"),
                              "state": "APPLIED", "source": "provider:readback"},
    reconciler={"principal": "human:A"}, current_writer={})
check("D10b no-principal attempt refused", v["verdict"],
      attempts.REFUSE_PRINCIPAL, security=True)
v = attempts.reconcile_observation(
    rec, observation={"intent_digest": dg(instruction="kw"), "state": "APPLIED",
                      "source": "provider:readback"},
    reconciler={}, current_writer={})
check("D10c empty reconciler refused", v["verdict"],
      attempts.REFUSE_PRINCIPAL, security=True)

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

# D12/D13 — target, principal and runtime are conflicts, not replays.
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
check("D14 late observation after SETTLED refused", v["verdict"],
      attempts.REFUSE_NOT_UNKNOWN)

print()
print("tree:", TREE)
print("scratch:", SCRATCH)
print("stored attempts:", json.dumps(
    {k: {"phase": v.get("phase"), "principal": v.get("principal")}
     for k, v in store.dispatch_attempts().items()}, sort_keys=True))
if failures:
    print(f"PROBE FAILED: {len(failures)} check(s): {failures}")
    sys.exit(1)
print("PROBE OK: every negative refused and every positive control held")
