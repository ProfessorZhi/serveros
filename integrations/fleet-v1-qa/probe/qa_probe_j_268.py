#!/usr/bin/env python3
"""Independent QA Snapshot J — cross-repo work-write resource namespace.

QA_MODE=old QA_TREE=<serveros #264 aee38cb>  -> red: canonical work-write:<Item>
                                                is not found; a naked Item row is.
QA_MODE=new QA_TREE=<serveros #268 f4eade1>  -> green: canonical only, no fallback.

Both use the REAL Work Engine #14 store and `OwnerVerifiedLease`. The verifier
is a TEST-ONLY FakeLeaseAuthorityForQA: it is NOT a production trust source and
does not attest an OS principal; the production boundary remains
BLOCKED_TRUST_SOURCE. Synthetic Items/contexts; no provider/network/credential.
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
MODE = os.environ["QA_MODE"]
RUNTIME = TREE / "runtime"
failures: list[str] = []
sys.path.insert(0, str(WE))
from workengine.authority import (  # noqa: E402
    AuthorityError, LeaseGrant, OwnerVerifiedLease)
from workengine.store import ServerOSStore  # noqa: E402


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
router_mod = load("qa_agent_control_router", TREE / "fleet" / "agent_control_router.py")


def check(label, got, expected, *, security=False):
    ok = got == expected
    tag = "PASS" if ok else ("SECURITY_FAIL" if security else "FAIL")
    print(f"{tag} [{MODE}] {label}: got={got!r} expected={expected!r}")
    if not ok:
        failures.append(f"[{MODE}] {label}")


def stamp(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class FakeLeaseAuthorityForQA:
    """TEST-ONLY fake validator. NOT a production LeaseAuthority.

    It returns a fixed LeaseGrant without resolving a context_ref out of band
    and without kernel-attested identity. The real producer remains
    BLOCKED_TRUST_SOURCE.
    """

    def __init__(self, *, principal="human:A", item="SRV-CANON", epoch=1):
        self.principal = principal
        self.item = item
        self.epoch = epoch
        self.calls = []

    def verify(self, context_ref, *, action, project, item_ref, now):
        self.calls.append((context_ref, action, project, item_ref, now))
        return LeaseGrant(
            principal=self.principal, grant_ref="grant:qa:fake",
            project=project, items=(item_ref,),
            actions=("lease.acquire", "lease.renew", "lease.release",
                     "work.commit"),
            valid_from=now - 3600, expires_at=now + 3600, revoked=False,
            epoch=self.epoch, os_principal=f"uid:{os.getuid()}")


SCRATCH.mkdir(parents=True, exist_ok=True)
state = SCRATCH / "state"
state.mkdir(parents=True, exist_ok=True)
daemon_state = state / "runtime"
daemon_state.mkdir(parents=True, exist_ok=True)
we = ServerOSStore(state)
T = time.time()
NOW = stamp(T + 1)

ITEMS = ("SRV-CANON", "SRV-NAKED", "SRV-NOLEASE", "SRV-EXPIRED",
         "SRV-RELEASED", "SRV-MOVED", "SRV-SPOOF")
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
        "device_ref": "device:qa-j", "resources": []}),
    state_dir=daemon_state, socket_path=SCRATCH / "run/serveros.sock",
    log=lambda message: None)
store = daemon.control_store()


def rows():
    return [(r["resource"], r["owner"], str(r["token"]))
            for r in we.connection().execute(
                "SELECT resource, owner, token FROM leases ORDER BY resource")]


def rpc(method, params):
    return daemon.handle({"method": method, "params": params})["result"]


def create(ref, item, **over):
    rec = {"session_ref": ref, "session_role": "execution",
           "bound_item_ref": item}
    rec.update(over)
    return rpc("agent.binding.record", rec)


def resolve(ref):
    return rpc("agent.binding.resolve", {"session_ref": ref})


class FakeBindings:
    def get(self, ref):
        return resolve(str(ref))


class FakeService:
    """A mocked local provider. It performs no send and records the call."""

    def __init__(self):
        self.calls = []

    def send(self, **kwargs):
        self.calls.append(kwargs)
        return {"mocked_local_provider": True}


def loopback_send(session_ref):
    service = FakeService()
    node = router_mod.LocalLoopbackTransport(
        device_ref="device:x", service=service, bindings=FakeBindings())
    request = router_mod.AgentControlRequest(
        action="agent.session.send", device_ref="device:x",
        agent_runtime="agent-runtime:claude-code",
        params={"instruction": "hi", "turn_idempotency_key": "t-1"},
        session_ref=session_ref, idempotency_key="k-" + session_ref)
    try:
        result = node._dispatch(request)
        return ("SENT", len(service.calls), result)
    except router_mod.TransportFailure as exc:
        return (exc.reason, len(service.calls), None)


# ---------------------------------------------------------------------------
# Real WE #14 producer writes work-write:<Item>; a naked row is a fixture.
# ---------------------------------------------------------------------------
fake = FakeLeaseAuthorityForQA(item="SRV-CANON")
producer = OwnerVerifiedLease(we, fake)
canon = producer.acquire(context_ref="ctx:fake-not-production", project="qa",
                         item_ref="SRV-CANON", ttl_s=600, now=T)
naked = we.acquire_lease("SRV-NAKED", owner="agent:A", ttl_s=600, now=T)
check("J0 canonical writer used work-write namespace",
      (canon["resource"], canon["owner"], str(canon["token"])),
      ("work-write:SRV-CANON", "human:A", "1"))
check("J0 naked fixture row exists",
      (naked["resource"], naked["owner"], str(naked["token"])),
      ("SRV-NAKED", "agent:A", "1"))

roots = resolver.Roots(workspace_root=SCRATCH / "ws",
                       artifacts_root=SCRATCH / "art", state_db=we.db_path)

# ---------------------------------------------------------------------------
# OLD #264 — red control: canonical is invisible, naked is read.
# ---------------------------------------------------------------------------
if MODE == "old":
    check("old canonical work-write lease IS found",
          ((resolver.live_lease(roots, "SRV-CANON", now=T + 1) or {}).get("owner")),
          "human:A", security=True)
    check("old naked Item lease is NOT read",
          resolver.live_lease(roots, "SRV-NAKED", now=T + 1), None,
          security=True)
    create("session:canon", "SRV-CANON")
    check("old canonical create mints a claim",
          ((resolve("session:canon").get("binding") or {}).get("writer") or {}).get("owner"),
          "human:A", security=True)
    check("old canonical send is ADMITTED",
          loopback_send("session:canon")[0], "SENT", security=True)
    create("session:naked", "SRV-NAKED")
    check("old naked create mints no claim",
          (resolve("session:naked").get("binding") or {}).get("writer"), None,
          security=True)
    check("old naked send is refused", loopback_send("session:naked")[0],
          "NOT_CURRENT_WRITER", security=True)
    print("scratch:", SCRATCH)
    print("leases:", json.dumps(rows(), sort_keys=True))
    sys.exit(1 if failures else 0)

# ---------------------------------------------------------------------------
# NEW #268 — canonical only, no fallback.
# ---------------------------------------------------------------------------
check("new work_write_resource is the one derivation",
      resolver.work_write_resource("SRV-CANON"), "work-write:SRV-CANON")
check("new reverse derivation round-trips",
      resolver.item_from_work_write_resource("work-write:SRV-CANON"),
      "SRV-CANON")
check("new canonical work-write lease IS found",
      ((resolver.live_lease(roots, "SRV-CANON", now=T + 1) or {}).get("owner")),
      "human:A")
check("new naked Item lease is NOT read (no fallback)",
      resolver.live_lease(roots, "SRV-NAKED", now=T + 1), None,
      security=True)

w = create("session:canon", "SRV-CANON")
check("new canonical create mints from the canonical lease",
      (((w.get("binding") or {}).get("writer") or {}).get("owner"),
       str(((w.get("binding") or {}).get("writer") or {}).get("token"))),
      ("human:A", "1"))
check("new canonical send reaches the mocked local provider",
      loopback_send("session:canon")[0:2], ("SENT", 1))

w = create("session:naked", "SRV-NAKED")
check("new naked create mints no claim",
      (w.get("recorded"), (w.get("binding") or {}).get("writer")), (True, None))
check("new naked send is refused",
      loopback_send("session:naked")[0], "NOT_CURRENT_WRITER", security=True)

w = create("session:nolease", "SRV-NOLEASE")
check("new no-lease create mints no claim",
      (w.get("recorded"), (w.get("binding") or {}).get("writer")), (True, None))
check("new no-lease send is refused", loopback_send("session:nolease")[0],
      "NOT_CURRENT_WRITER", security=True)

# expired canonical
producer.acquire(context_ref="ctx:fake-not-production", project="qa",
                 item_ref="SRV-EXPIRED", ttl_s=10, now=T - 100)
check("new expired canonical lease is not live",
      resolver.live_lease(roots, "SRV-EXPIRED", now=T + 1), None)
# released canonical
rel = producer.acquire(context_ref="ctx:fake-not-production", project="qa",
                       item_ref="SRV-RELEASED", ttl_s=600, now=T)
producer.release(context_ref="ctx:fake-not-production", project="qa",
                 item_ref="SRV-RELEASED", token=rel["token"], now=T)
check("new released canonical lease is not live",
      resolver.live_lease(roots, "SRV-RELEASED", now=T + 1), None)
# moved canonical: A then B token 2; the old claim is stale.
producer.acquire(context_ref="ctx:fake-not-production", project="qa",
                 item_ref="SRV-MOVED", ttl_s=600, now=T)
create("session:moved", "SRV-MOVED")
producer.release(context_ref="ctx:fake-not-production", project="qa",
                 item_ref="SRV-MOVED",
                 token=we.active_lease("work-write:SRV-MOVED", now=T)["token"], now=T)
fake_b = FakeLeaseAuthorityForQA(principal="human:B", item="SRV-MOVED")
producer_b = OwnerVerifiedLease(we, fake_b)
moved = producer_b.acquire(context_ref="ctx:fake-b", project="qa",
                           item_ref="SRV-MOVED", ttl_s=600, now=T + 1)
check("new moved canonical token advanced", str(moved["token"]), "2")
check("new moved lease makes the old claim stale",
      loopback_send("session:moved")[0], "NOT_CURRENT_WRITER", security=True)

# explicit caller writer
w = create("session:spoof", "SRV-SPOOF",
           writer={"owner": "agent:EVIL", "epoch": 999, "token": 999})
check("new explicit caller writer is refused", w.get("recorded"), False,
      security=True)
check("new explicit writer reason", w.get("reason"), "UNTRUSTED_ACTOR")

# ---------------------------------------------------------------------------
# S2 crossed actor / terminal / unknown effect on the same merged daemon.
# ---------------------------------------------------------------------------
def trusted_context(actor_ref):
    now_s = daemon_mod._iso(daemon._now())
    rec = actor.build({
        "human_control_provider": "provider:botmux-human-control",
        "transport": "feishu",
        "transport_identity": "botmux-verified-enterprise-user",
        "conversation_ref": "oc_qa_j", "message_ref": "om_qa_j",
        "observed_at": now_s, "provider_status": "VERIFIED",
        "provenance": "FACT", "actor_ref": actor_ref, "provider_pid": 4242,
    }, now=now_s)
    store.put_context(rec)
    return rec


CTX_A = trusted_context("human:A")
CTX_B = trusted_context("human:B")


def dg(instruction="more", principal="human:A", session="session:one",
       runtime="agent-runtime:claude-code"):
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


def drec(ctx, rec):
    return rpc("agent.dispatch.record",
               {"context_ref": ctx["context_ref"], "attempt": rec})


base = dg()
w = drec(CTX_A, attempt("k1", base, attempts.CALL_NOT_SENT))
check("S2 authentic attempt recorded", w.get("recorded"), True)
w = drec(CTX_B, attempt("k1", base, attempts.SETTLED,
                        result={"ok": True, "provider_result": "fabricated"}))
check("S2 crossed-actor SETTLED refused", w.get("recorded"), False,
      security=True)
w = drec(CTX_A, attempt("k1", base, attempts.SETTLED, result={"ok": True}))
check("S2 authentic SETTLED recorded", w.get("recorded"), True)
w = drec(CTX_A, attempt("k1", base, attempts.CALL_NOT_SENT))
check("S2 terminal SETTLED cannot reopen", w.get("recorded"), False,
      security=True)
w = drec(CTX_A, attempt("k3", dg(instruction="k3"), attempts.EFFECT_UNKNOWN))
check("S2 unknown-effect recorded", w.get("recorded"), True)
check("S2 unknown effect cannot retry",
      attempts.reconcile(store.get_dispatch_attempt("k3"),
                         digest=dg(instruction="k3"))["verdict"],
      attempts.REFUSE_UNKNOWN, security=True)
settled = store.get_dispatch_attempt("k1")
v = attempts.reconcile_observation(
    settled, observation={"intent_digest": base, "state": "APPLIED",
                          "source": "provider:late"},
    reconciler={"principal": "human:A", "owner": "human:A", "epoch": 1,
                "token": "1"},
    current_writer={"owner": "human:A", "epoch": 1, "token": "1"})
check("S2 late observation cannot override SETTLED",
      v["verdict"], attempts.REFUSE_NOT_UNKNOWN)

# ---------------------------------------------------------------------------
# SOURCE — the production trusted source is still absent.
# ---------------------------------------------------------------------------
resolver_src = (TREE / "context/resolver.py").read_text(encoding="utf-8")
check("S1 the canonical prefix is the only lookup key in live_lease",
      '(f"{WORK_WRITE_PREFIX}{item}", expires_after)' in resolver_src, True)
check("S1 live_lease has no naked-Item fallback query",
      "WHERE resource=? AND expires_at" in resolver_src, True)
serveros_py = [p for p in TREE.rglob("*.py") if "/tests/" not in p.as_posix()]
check("S1 serveros has no acquire_lease",
      any("acquire_lease(" in p.read_text(encoding="utf-8") for p in serveros_py),
      False)
check("S1 serveros has no executable lease.* / work.commit actions",
      any(("lease.acquire" in p.read_text(encoding="utf-8")
           or "work.commit" in p.read_text(encoding="utf-8"))
          for p in serveros_py), False)
check("S1 serveros does not import workengine.authority",
      any("workengine.authority" in p.read_text(encoding="utf-8")
          for p in serveros_py), False)
we_cli = (WE / "workengine/cli.py").read_text(encoding="utf-8")
we_mcp = (WE / "workengine/mcp.py").read_text(encoding="utf-8")
check("WE #14 CLI exposes no lease producer",
      "OwnerVerifiedLease" in we_cli or "acquire_lease" in we_cli, False)
check("WE #14 MCP exposes no lease producer",
      "OwnerVerifiedLease" in we_mcp or "acquire_lease" in we_mcp, False)
authority_src = (WE / "workengine/authority.py").read_text(encoding="utf-8")
check("WE #14 has no-authority refusal",
      "BLOCKED_TRUST_SOURCE" in authority_src, True)
check("WE #14 acquire takes no owner argument",
      "def acquire(self, *, context_ref" in authority_src
      and "owner" not in inspect.signature(OwnerVerifiedLease.acquire).parameters,
      True)
check("WE #14 requires grant epoch / OS principal / revoked checks",
      all(token in authority_src for token in
          ("EPOCH_MISSING", "OS_PRINCIPAL_MISMATCH", "GRANT_REVOKED",
           "ACTION_NOT_GRANTED")), True)
contract = (TREE / "fleet/agent-control-contract.yaml").read_text(encoding="utf-8")
check("serveros contract records the blocked producer bridge",
      "verified_producer_bridge_blocked" in contract, True)
store_src = (WE / "workengine/store.py").read_text(encoding="utf-8")
check("WE #14 finalize/authority refuse without receipt authority",
      all(token in (store_src + authority_src) for token in
          ("RUNNER_RECEIPT_REQUIRED", "RECEIPT_INCONSISTENT",
           "BLOCKED_TRUST_SOURCE")), True)

print()
print("scratch:", SCRATCH)
print("leases:", json.dumps(rows(), sort_keys=True))
if failures:
    print(f"PROBE FAILED: {len(failures)} check(s): {failures}")
    sys.exit(1)
print("PROBE OK: canonical namespace only; fake validator is test-only; "
      "BLOCKED_TRUST_SOURCE remains")
