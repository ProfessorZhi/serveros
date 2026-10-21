#!/usr/bin/env python3
"""Independent QA Snapshot I — merged S1+S2 #264 aee38cb.

True combined boundary: one real Work Engine store (#13 40b0622), one real
serveros daemon from the merged tree, one shared state dir. Exercises the S1
send admission (lease authority) and the S2 dispatch-attempt store/write path in
the same process, plus cross-boundary principal checks.

LOCAL only: synthetic Items/contexts/claims; no provider, network, credential,
login or live store. No exactly-once claim (provider readback is BLOCKED).
"""
from __future__ import annotations

import importlib.util
import inspect
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

TREE = Path(os.environ["QA_TREE"]).resolve()
WE = Path(os.environ["WE_TREE"]).resolve()
SCRATCH = Path(os.environ["QA_SCRATCH"]).resolve()
RUNTIME = TREE / "runtime"
failures: list[str] = []
sys.path.insert(0, str(WE))
from workengine.store import LeaseError, ServerOSStore  # noqa: E402


def load(name, path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


coordination = load("serveros_runtime_coordination", RUNTIME / "coordination.py")
resolver = load("serveros_context_resolver", TREE / "context" / "resolver.py")
daemon_mod = load("serveros_runtime_daemon", RUNTIME / "daemon.py")
registry_mod = load("serveros_runtime_registry", RUNTIME / "registry.py")
store_mod = load("serveros_runtime_agent_control_store", RUNTIME / "agent_control_store.py")
attempts = load("serveros_runtime_dispatch_attempts", RUNTIME / "dispatch_attempts.py")
actor = load("serveros_context_actor", TREE / "context" / "actor.py")


def check(label, got, expected, *, security=False):
    ok = got == expected
    tag = "PASS" if ok else ("SECURITY_FAIL" if security else "FAIL")
    print(f"{tag} {label}: got={got!r} expected={expected!r}")
    if not ok:
        failures.append(label)


def rpc(method, params):
    return daemon.handle({"method": method, "params": params})["result"]


def stamp(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


SCRATCH.mkdir(parents=True, exist_ok=True)
state = SCRATCH / "state"
state.mkdir(parents=True, exist_ok=True)
daemon_state = state / "runtime"
daemon_state.mkdir(parents=True, exist_ok=True)
we = ServerOSStore(state)
T = time.time()
NOW = stamp(T + 1)

ITEMS = ("SRV-LIVE", "SRV-NOLEASE", "SRV-EXPIRED", "SRV-DELETED",
         "SRV-MOVED", "SRV-T2", "SRV-FORGED", "SRV-CLOSED")
conn = sqlite3.connect(state / "serveros.db")
conn.execute("INSERT OR IGNORE INTO projects(project, prefix) VALUES ('qa', 'SRV')")
for item in ITEMS:
    conn.execute(
        "INSERT OR REPLACE INTO items_index"
        "(item_id, project, title, kind, card_locator, state, outcome,"
        " created_at, updated_at) VALUES (?, 'qa', 'qa item', 'test', 'card',"
        " 'ACTIVE', NULL, ?, ?)", (item, stamp(T), stamp(T)))
conn.commit()
conn.close()

daemon = daemon_mod.Daemon(
    registry_mod.Registry(document={
        "schema": "serveros/managed-runtime-registry.v1",
        "device_ref": "device:qa-i", "resources": []}),
    state_dir=daemon_state, socket_path=SCRATCH / "run/serveros.sock",
    log=lambda message: None)
store = daemon.control_store()


def live_lease_rows():
    return [(r["resource"], r["owner"], str(r["token"]), r["expires_at"])
            for r in we.connection().execute(
                "SELECT resource, owner, token, expires_at FROM leases "
                "ORDER BY resource")]


def trusted_context(actor_ref):
    now_s = daemon_mod._iso(daemon._now())
    rec = actor.build({
        "human_control_provider": "provider:botmux-human-control",
        "transport": "feishu",
        "transport_identity": "botmux-verified-enterprise-user",
        "conversation_ref": "oc_qa_i", "message_ref": "om_qa_i",
        "observed_at": now_s, "provider_status": "VERIFIED",
        "provenance": "FACT", "actor_ref": actor_ref, "provider_pid": 4242,
    }, now=now_s)
    store.put_context(rec)
    return rec


CTX_A = trusted_context("human:A")
CTX_B = trusted_context("human:B")


def send(binding, write_authority):
    try:
        return coordination.admit_work(
            binding_record=binding, work_item=None, item_state="ACTIVE",
            current_writer=None, authority=write_authority,
            now=NOW)["state"]
    except coordination.ThreadRefused as exc:
        return exc.reason


def create(ref, item, **over):
    rec = {"session_ref": ref, "session_role": "execution",
           "bound_item_ref": item}
    rec.update(over)
    return rpc("agent.binding.record", rec)


def resolve(ref):
    return rpc("agent.binding.resolve", {"session_ref": ref})


def dispatch_digest(*, session="session:one", runtime="agent-runtime:claude-code",
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


# ===========================================================================
# S1 — lease authority through the MERGED daemon/router boundary.
# ===========================================================================
lease = we.acquire_lease("SRV-LIVE", owner="agent:A", ttl_s=600, now=T)
w = create("session:live", "SRV-LIVE")
wa = w.get("write_authority") or {}
check("S1-A1 create reports the live lease",
      ((wa.get("lease") or {}).get("owner"), str((wa.get("lease") or {}).get("token"))),
      ("agent:A", "1"))
check("S1-A1 claim minted from the lease",
      (((w.get("binding") or {}).get("writer") or {}).get("owner"),
       str(((w.get("binding") or {}).get("writer") or {}).get("token"))),
      ("agent:A", "1"))
read = resolve("session:live")
check("S1-A1 send with A/1 is ADMITTED",
      send(read.get("binding"), read.get("write_authority")), "ADMITTED")

before = live_lease_rows()
w = create("session:nolease", "SRV-NOLEASE")
check("S1-A2 no-lease create records no claim",
      (w.get("recorded"), (w.get("binding") or {}).get("writer")), (True, None))
check("S1-A2 create reports lease=None",
      (w.get("write_authority") or {}).get("lease"), None)
check("S1-A2 create did not acquire/mutate a lease",
      live_lease_rows(), before, security=True)
read = resolve("session:nolease")
check("S1-A3 no-lease send is refused",
      send(read.get("binding"), read.get("write_authority")),
      "NOT_CURRENT_WRITER", security=True)

we.acquire_lease("SRV-EXPIRED", owner="agent:A", ttl_s=10, now=T - 100)
w = create("session:expired", "SRV-EXPIRED")
check("S1-A4 expired lease is not authority",
      (w.get("write_authority") or {}).get("lease"), None)
rel = we.acquire_lease("SRV-DELETED", owner="agent:A", ttl_s=600, now=T)
we.release_lease("SRV-DELETED", owner="agent:A", token=rel["token"], now=T)
w = create("session:deleted", "SRV-DELETED")
check("S1-A4 released lease is not authority",
      (w.get("write_authority") or {}).get("lease"), None)

we.acquire_lease("SRV-MOVED", owner="agent:A", ttl_s=600, now=T)
create("session:moved", "SRV-MOVED")
we.release_lease("SRV-MOVED", owner="agent:A",
                 token=we.active_lease("SRV-MOVED", now=T)["token"], now=T)
we.acquire_lease("SRV-MOVED", owner="agent:B", ttl_s=600, now=T + 1)
read = resolve("session:moved")
check("S1-A5 moved lease makes the old claim stale",
      send(read.get("binding"), read.get("write_authority")),
      "NOT_CURRENT_WRITER", security=True)

w = create("session:spoof", "SRV-LIVE",
           writer={"owner": "agent:EVIL", "epoch": 999, "token": 999})
check("S1-A6 forged caller writer refused", w.get("recorded"), False,
      security=True)
check("S1-A6 reason UNTRUSTED_ACTOR", w.get("reason"), "UNTRUSTED_ACTOR")

we.acquire_lease("SRV-T2", owner="agent:A", ttl_s=600, now=T)
first = create("session:t2a", "SRV-T2")
second = create("session:t2b", "SRV-T2")
check("S1-A7 first claimant holds A/1",
      ((first.get("binding") or {}).get("writer") or {}).get("owner"), "agent:A")
check("S1-A7 second claimant has no claim",
      (second.get("binding") or {}).get("writer"), None)
read = resolve("session:t2b")
check("S1-A7 second claimant send refused",
      send(read.get("binding"), read.get("write_authority")),
      "NOT_CURRENT_WRITER", security=True)

we.acquire_lease("SRV-FORGED", owner="agent:A", ttl_s=600, now=T)
fabricated = coordination.binding(
    session_role="execution", bound_item_ref="SRV-FORGED",
    writer={"owner": "agent:B", "epoch": 2, "token": "2"})
fabricated["session_ref"] = "session:forged"
store.put_binding(fabricated)
read = resolve("session:forged")
check("S1-A8 fabricated claim loses to the lease",
      send(read.get("binding"), read.get("write_authority")),
      "NOT_CURRENT_WRITER", security=True)

create("session:closed", "SRV-CLOSED") if we.active_lease("SRV-CLOSED", now=T) else None
we.acquire_lease("SRV-CLOSED", owner="agent:A", ttl_s=600, now=T)
create("session:closed", "SRV-CLOSED")
rpc("agent.binding.close", {"session_ref": "session:closed", "reason": "handoff"})
read = resolve("session:closed")
check("S1-A9 closed source is refused",
      send(read.get("binding"), read.get("write_authority")),
      "THREAD_CLOSED_FOR_NEW_WORK")

# ===========================================================================
# S2 — dispatch-attempt boundary on the same merged daemon/store.
# ===========================================================================
def dg(**kw):
    return dispatch_digest(**kw)


def drec(ctx, rec):
    return rpc("agent.dispatch.record",
               {"context_ref": ctx["context_ref"], "attempt": rec})


base = dg()
rec = attempt("k1", base, attempts.CALL_NOT_SENT)
w = drec(CTX_A, rec)
check("S2-B1 authentic attempt recorded", w.get("recorded"), True)
check("S2-B1 principal comes from the trusted context",
      (w.get("attempt") or {}).get("principal"), "human:A")

forged = attempt("k1", base, attempts.SETTLED,
                 result={"ok": True, "provider_result": "fabricated"})
w = drec(CTX_B, forged)
check("S2-B2 cross-actor forged SETTLED refused", w.get("recorded"), False,
      security=True)
check("S2-B2 reason UNTRUSTED_ACTOR", w.get("reason"), "UNTRUSTED_ACTOR")

w = drec(CTX_A, attempt("k1", base, attempts.SETTLED, result={"ok": True}))
check("S2-B3 authentic settle recorded", w.get("recorded"), True)
w = drec(CTX_A, attempt("k1", base, attempts.CALL_NOT_SENT))
check("S2-B4 terminal SETTLED cannot reopen", w.get("recorded"), False,
      security=True)
check("S2-B4 stored phase stays SETTLED",
      (store.get_dispatch_attempt("k1") or {}).get("phase"), attempts.SETTLED)

rec = attempt("k2", dg(instruction="k2"), attempts.SETTLED, result={"ok": True})
for label, digest in (
        ("session", dg(instruction="k2", session="session:two")),
        ("runtime", dg(instruction="k2", runtime="agent-runtime:codex")),
        ("principal", dg(instruction="k2", principal="human:B"))):
    check(f"S2-B5 same key different {label} is CONFLICT",
          attempts.reconcile(rec, digest=digest)["verdict"],
          attempts.CONFLICT)

w = drec(CTX_A, attempt("k3", dg(instruction="k3"),
                        attempts.EFFECT_UNKNOWN))
check("S2-B6 unknown-effect attempt recorded", w.get("recorded"), True)
check("S2-B6 unknown effect cannot retry",
      attempts.reconcile(store.get_dispatch_attempt("k3"),
                         digest=dg(instruction="k3"))["verdict"],
      attempts.REFUSE_UNKNOWN)

store2 = store_mod.AgentControlStore(store.path)
check("S2-B7 restart reader sees SETTLED",
      (store2.get_dispatch_attempt("k1") or {}).get("phase"), attempts.SETTLED)
check("S2-B7 restart retry is REPLAY",
      attempts.reconcile(store2.get_dispatch_attempt("k1"),
                         digest=base)["verdict"], attempts.REPLAY)
check("S2-B7 restart unknown is REFUSE_UNKNOWN",
      attempts.reconcile(store2.get_dispatch_attempt("k3"),
                         digest=dg(instruction="k3"))["verdict"],
      attempts.REFUSE_UNKNOWN)

settled = store.get_dispatch_attempt("k1")
v = attempts.reconcile_observation(
    settled, observation={"intent_digest": base, "state": "APPLIED",
                          "source": "provider:late"},
    reconciler={"principal": "human:A", "owner": "human:A", "epoch": 1,
                "token": "1"},
    current_writer={"owner": "human:A", "epoch": 1, "token": "1"})
check("S2-B8 late observation cannot override SETTLED",
      v["verdict"], attempts.REFUSE_NOT_UNKNOWN)

noprincipal = attempts.attempt_record(
    key="kn", action="agent.session.send", device_ref="device:x",
    verb="session.send", intent_digest=dg(instruction="kn"),
    phase=attempts.EFFECT_UNKNOWN, work_item="SRV-LIVE")
v = attempts.reconcile_observation(
    noprincipal, observation={"intent_digest": dg(instruction="kn"),
                              "state": "APPLIED", "source": "provider:readback"},
    reconciler={"principal": "human:A"}, current_writer={})
check("S2-B9 no-principal attempt is refused",
      v["verdict"], attempts.REFUSE_PRINCIPAL, security=True)

# ===========================================================================
# SOURCE — producer/readback limits and merged structure.
# ===========================================================================
serveros_py = [p for p in TREE.rglob("*.py") if "/tests/" not in p.as_posix()]
check("I-S1 serveros has no acquire_lease",
      any("acquire_lease(" in p.read_text(encoding="utf-8") for p in serveros_py),
      False)
check("I-S1 serveros has no direct lease write",
      any(any(t in p.read_text(encoding="utf-8") for t in
              ("INSERT INTO leases", "UPDATE leases", "DELETE FROM leases"))
          for p in serveros_py), False)
we_cli = (WE / "workengine/cli.py").read_text(encoding="utf-8")
we_mcp = (WE / "workengine/mcp.py").read_text(encoding="utf-8")
check("I-S1 WE has no lease CLI producer", "acquire_lease" in we_cli, False)
check("I-S1 WE has no lease MCP producer", "acquire_lease" in we_mcp, False)
check("I-S2 daemon dispatch write requires a context_ref",
      "context_ref" in (RUNTIME / "daemon.py").read_text(encoding="utf-8"), True)
check("I-S2 transition table is present",
      "DISPATCH_TRANSITIONS" in (RUNTIME / "dispatch_attempts.py").read_text(encoding="utf-8"),
      True)
check("I-S2 no provider-owned idempotency readback in the merged tree",
      any("provider" in p.read_text(encoding="utf-8").lower()
          and "readback" in p.read_text(encoding="utf-8").lower()
          for p in [RUNTIME / "dispatch_attempts.py"]), False)

print()
print("scratch:", SCRATCH)
print("leases:", json.dumps([{"resource": r[0], "owner": r[1], "token": r[2]}
                             for r in live_lease_rows()], sort_keys=True))
if failures:
    print(f"PROBE FAILED: {len(failures)} check(s): {failures}")
    sys.exit(1)
print("PROBE OK: merged S1+S2 boundary checks held")
