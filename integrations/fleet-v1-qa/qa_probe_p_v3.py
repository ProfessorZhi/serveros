#!/usr/bin/env python3
"""Independent QA Snapshot P — V3 #276 combined tree + original WE15 authority.

QA_TREE=<V3 1bf20fe>, WE_TREE=<WE15 4bd9e8a>. One real Work Engine store,
one real serveros daemon from the COMBINED tree, one offline real
`bounded_invocation.invoke_once` receipt. LOCAL only: synthetic Item/context/
runner, no provider/network/login/credential, no production daemon.

Checks: S1 lease/writer/peer, grant epoch/unbound/revocation, S2 terminal/
unknown, T10 strict consumer vs legacy consumer on WE15 durable records,
manifest forgery, and source wiring.
"""
from __future__ import annotations

import base64
import copy
import datetime
import hashlib
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import timezone
from pathlib import Path

TREE = Path(os.environ["QA_TREE"]).resolve()
WE = Path(os.environ["WE_TREE"]).resolve()
SCRATCH = Path(os.environ["QA_SCRATCH"]).resolve()
RUNTIME = TREE / "runtime"
failures: list[str] = []
sys.path.insert(0, str(WE))

from workengine import WorkItem  # noqa: E402
from workengine.authority import (  # noqa: E402
    LeaseGrant, ReceiptError, T10Binding, VerifiedRunReceipt)
from workengine.model import (Activity, ContextMode, ExecutionContext,  # noqa: E402
                              RunState)
from workengine.store import ServerOSStore  # noqa: E402


def load(name, path):
    if name in sys.modules:
        return sys.modules[name]
    parent = str(path.parent)
    if parent not in sys.path:
        sys.path.insert(0, parent)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


coordination = load("p_coordination", RUNTIME / "coordination.py")
daemon_mod = load("p_daemon", RUNTIME / "daemon.py")
registry_mod = load("p_registry", RUNTIME / "registry.py")
store_mod = load("p_store", RUNTIME / "agent_control_store.py")
attempts = load("p_attempts", RUNTIME / "dispatch_attempts.py")
actor = load("p_actor", TREE / "context" / "actor.py")
continuation = load("p_continuation", RUNTIME / "continuation.py")
wake = load("p_wake", RUNTIME / "wake.py")
executor = load("p_executor", RUNTIME / "bounded_executor.py")
bounded = load("p_bounded", RUNTIME / "bounded_invocation.py")
GATE = load("p_finalization_result",
            TREE / "integrations/fleet-v1-verdict-evidence/finalization_result.py")
LEGACY = load("p_verdict_evidence",
              TREE / "integrations/fleet-v1-verdict-evidence/verdict_evidence.py")

ITEM = "SRV-0001"
CONTEXT = "ctx-delegated-1"
GRANT = "grant-1"
RUNNER = "claude-code"
ACTIVITY = "implement"
EPOCH = 1
BUDGET = {"wall_seconds": 45, "max_attempts": 2, "max_output_bytes": 200000}
RUNNER_SOURCE = '''#!/usr/bin/env python3
import sys
sys.stdin.read()
print("ALL TESTS PASSED - child says SUCCESS")
print("synthetic failing assertion detail", file=sys.stderr)
sys.exit(int(sys.argv[1]))
'''


def check(label, got, expected, *, security=False):
    ok = got == expected
    tag = "PASS" if ok else ("SECURITY_FAIL" if security else "FAIL")
    print(f"{tag} {label}: got={got!r} expected={expected!r}")
    if not ok:
        failures.append(label)


def stamp(epoch):
    return datetime.datetime.fromtimestamp(
        epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def now_iso(offset=0.0):
    return (datetime.datetime.now(timezone.utc)
            + datetime.timedelta(seconds=offset)).isoformat()


def minutes(n):
    return (datetime.datetime.now(timezone.utc)
            + datetime.timedelta(minutes=n)).isoformat()


SCRATCH.mkdir(parents=True, exist_ok=True)
state = SCRATCH / "state"
state.mkdir(parents=True, exist_ok=True)
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

daemon_state = state / "runtime"
daemon_state.mkdir(parents=True, exist_ok=True)
daemon = daemon_mod.Daemon(
    registry_mod.Registry(document={
        "schema": "serveros/managed-runtime-registry.v1",
        "device_ref": "device:qa-p", "resources": []}),
    state_dir=daemon_state, socket_path=SCRATCH / "run/serveros.sock",
    log=lambda message: None)
store = daemon.control_store()


def rpc(method, params, *, peer=None):
    answer = daemon.handle({"method": method, "params": params}, peer=peer)
    if "result" in answer:
        return answer["result"]
    return answer


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
        "conversation_ref": "oc_qa_p", "message_ref": "om_qa_p",
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


# ===========================================================================
# S1 — lease/writer authority (combined V3 daemon + WE15 store).
# ===========================================================================
lease = we.acquire_lease("work-write:SRV-LIVE", owner="agent:A", ttl_s=600, now=T)
w = create("session:live", "SRV-LIVE")
wa = w.get("write_authority") or {}
check("P1 live lease is the reported authority",
      ((wa.get("lease") or {}).get("owner"), str((wa.get("lease") or {}).get("token"))),
      ("agent:A", "1"))
check("P1 claim minted from the live lease",
      (((w.get("binding") or {}).get("writer") or {}).get("owner"),
       str(((w.get("binding") or {}).get("writer") or {}).get("token"))),
      ("agent:A", "1"))
read = resolve("session:live")
check("P1 live A/1 send ADMITTED",
      send(read.get("binding"), read.get("write_authority")), "ADMITTED")

before = live_lease_rows()
w = create("session:nolease", "SRV-NOLEASE")
check("P1 no-lease create records no claim",
      (w.get("recorded"), (w.get("binding") or {}).get("writer")), (True, None))
check("P1 create did not acquire/mutate a lease", live_lease_rows(), before,
      security=True)
read = resolve("session:nolease")
check("P1 no-lease send refused",
      send(read.get("binding"), read.get("write_authority")),
      "NOT_CURRENT_WRITER", security=True)

we.acquire_lease("work-write:SRV-EXPIRED", owner="agent:A", ttl_s=10, now=T - 100)
w = create("session:expired", "SRV-EXPIRED")
check("P1 expired lease is not authority",
      (w.get("write_authority") or {}).get("lease"), None)
rel = we.acquire_lease("work-write:SRV-DELETED", owner="agent:A", ttl_s=600, now=T)
we.release_lease("work-write:SRV-DELETED", owner="agent:A", token=rel["token"], now=T)
w = create("session:deleted", "SRV-DELETED")
check("P1 released lease is not authority",
      (w.get("write_authority") or {}).get("lease"), None)

we.acquire_lease("work-write:SRV-MOVED", owner="agent:A", ttl_s=600, now=T)
create("session:moved", "SRV-MOVED")
we.release_lease("work-write:SRV-MOVED", owner="agent:A",
                 token=we.active_lease("work-write:SRV-MOVED", now=T)["token"], now=T)
we.acquire_lease("work-write:SRV-MOVED", owner="agent:B", ttl_s=600, now=T + 1)
read = resolve("session:moved")
check("P1 moved lease makes the old claim stale",
      send(read.get("binding"), read.get("write_authority")),
      "NOT_CURRENT_WRITER", security=True)

w = create("session:spoof", "SRV-LIVE",
           writer={"owner": "agent:EVIL", "epoch": 999, "token": 999})
check("P1 cross-actor spoofed writer refused", w.get("recorded"), False,
      security=True)
check("P1 spoof reason UNTRUSTED_ACTOR", w.get("reason"), "UNTRUSTED_ACTOR")

we.acquire_lease("work-write:SRV-T2", owner="agent:A", ttl_s=600, now=T)
first = create("session:t2a", "SRV-T2")
second = create("session:t2b", "SRV-T2")
check("P1 first claimant holds A/1",
      ((first.get("binding") or {}).get("writer") or {}).get("owner"), "agent:A")
check("P1 second claimant has no claim",
      (second.get("binding") or {}).get("writer"), None)
read = resolve("session:t2b")
check("P1 second claimant send refused",
      send(read.get("binding"), read.get("write_authority")),
      "NOT_CURRENT_WRITER", security=True)

we.acquire_lease("work-write:SRV-FORGED", owner="agent:A", ttl_s=600, now=T)
fabricated = coordination.binding(
    session_role="execution", bound_item_ref="SRV-FORGED",
    writer={"owner": "agent:B", "epoch": 2, "token": "2"})
fabricated["session_ref"] = "session:forged"
store.put_binding(fabricated)
read = resolve("session:forged")
check("P1 fabricated claim loses to the lease",
      send(read.get("binding"), read.get("write_authority")),
      "NOT_CURRENT_WRITER", security=True)

we.acquire_lease("work-write:SRV-CLOSED", owner="agent:A", ttl_s=600, now=T)
create("session:closed", "SRV-CLOSED")
rpc("agent.binding.close", {"session_ref": "session:closed", "reason": "handoff"})
read = resolve("session:closed")
check("P1 closed source refused",
      send(read.get("binding"), read.get("write_authority")),
      "THREAD_CLOSED_FOR_NEW_WORK")

# ===========================================================================
# S2 — dispatch attempts (same daemon/store).
# ===========================================================================
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


def drec(ctx, rec):
    return rpc("agent.dispatch.record",
               {"context_ref": ctx["context_ref"], "attempt": rec})


base = dispatch_digest()
w = drec(CTX_A, attempt("k1", base, attempts.CALL_NOT_SENT))
check("P2 authentic attempt recorded", w.get("recorded"), True)
check("P2 principal from trusted context",
      (w.get("attempt") or {}).get("principal"), "human:A")

forged = attempt("k1", base, attempts.SETTLED,
                 result={"ok": True, "provider_result": "fabricated"})
w = drec(CTX_B, forged)
check("P2 cross-actor forged SETTLED refused", w.get("recorded"), False,
      security=True)
check("P2 cross-actor reason UNTRUSTED_ACTOR", w.get("reason"),
      "UNTRUSTED_ACTOR")

w = drec(CTX_A, attempt("k1", base, attempts.SETTLED, result={"ok": True}))
check("P2 authentic settle recorded", w.get("recorded"), True)
w = drec(CTX_A, attempt("k1", base, attempts.CALL_NOT_SENT))
check("P2 terminal SETTLED cannot reopen", w.get("recorded"), False,
      security=True)
check("P2 stored phase stays SETTLED",
      (store.get_dispatch_attempt("k1") or {}).get("phase"), attempts.SETTLED)

rec = attempt("k2", dispatch_digest(instruction="k2"), attempts.SETTLED,
              result={"ok": True})
for label, digest in (
        ("session", dispatch_digest(instruction="k2", session="session:two")),
        ("runtime", dispatch_digest(instruction="k2",
                                    runtime="agent-runtime:codex")),
        ("principal", dispatch_digest(instruction="k2", principal="human:B"))):
    check(f"P2 same key different {label} CONFLICT",
          attempts.reconcile(rec, digest=digest)["verdict"],
          attempts.CONFLICT)

w = drec(CTX_A, attempt("k3", dispatch_digest(instruction="k3"),
                        attempts.EFFECT_UNKNOWN))
check("P2 unknown-effect attempt recorded", w.get("recorded"), True)
check("P2 unknown effect cannot retry",
      attempts.reconcile(store.get_dispatch_attempt("k3"),
                         digest=dispatch_digest(instruction="k3"))["verdict"],
      attempts.REFUSE_UNKNOWN)

store2 = store_mod.AgentControlStore(store.path)
check("P2 restart reader sees SETTLED",
      (store2.get_dispatch_attempt("k1") or {}).get("phase"), attempts.SETTLED)
check("P2 restart retry is REPLAY",
      attempts.reconcile(store2.get_dispatch_attempt("k1"),
                         digest=base)["verdict"], attempts.REPLAY)
check("P2 restart unknown is REFUSE_UNKNOWN",
      attempts.reconcile(store2.get_dispatch_attempt("k3"),
                         digest=dispatch_digest(instruction="k3"))["verdict"],
      attempts.REFUSE_UNKNOWN)

settled = store.get_dispatch_attempt("k1")
v = attempts.reconcile_observation(
    settled, observation={"intent_digest": base, "state": "APPLIED",
                          "source": "provider:late"},
    reconciler={"principal": "human:A", "owner": "human:A", "epoch": 1,
                "token": "1"},
    current_writer={"owner": "human:A", "epoch": 1, "token": "1"})
check("P2 late observation cannot override SETTLED",
      v["verdict"], attempts.REFUSE_NOT_UNKNOWN)

noprincipal = attempts.attempt_record(
    key="kn", action="agent.session.send", device_ref="device:x",
    verb="session.send", intent_digest=dispatch_digest(instruction="kn"),
    phase=attempts.EFFECT_UNKNOWN, work_item="SRV-LIVE")
v = attempts.reconcile_observation(
    noprincipal, observation={"intent_digest": dispatch_digest(instruction="kn"),
                              "state": "APPLIED", "source": "provider:readback"},
    reconciler={"principal": "human:A"}, current_writer={})
check("P2 no-principal attempt refused", v["verdict"],
      attempts.REFUSE_PRINCIPAL, security=True)

# ===========================================================================
# P3 — grant: signed issuance, exact LeaseGrant, kernel peer, epoch, unbound.
# ===========================================================================
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from cryptography.hazmat.primitives.serialization import (  # noqa: E402
    Encoding, PublicFormat)

PROJECT = "serveros"
ACTIONS = ("lease.acquire", "lease.renew", "lease.release", "work.commit")
private = Ed25519PrivateKey.generate()
public = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
trust_path = SCRATCH / "operator-trust.json"
trust_path.write_text(json.dumps({
    "schema": "serveros/human-control-trust.v1", "audience": "device:qa-p",
    "providers": {"provider:fixture-human-control": {"keys": {"test-key": {
        "enabled": True,
        "public_key": base64.b64encode(public).decode()}}}}}))
store_actor = store_mod._actor_module()
old_trust = store_actor.DELEGATION_TRUST
store_actor.DELEGATION_TRUST = trust_path


def signed_evidence(*, grant_over=None):
    now = now_iso()
    report = {
        "human_control_provider": "provider:fixture-human-control",
        "transport": "fixture", "transport_identity": "fixture-verified-user",
        "conversation_ref": "chat:fixture",
        "message_ref": "message:fixture-explicit-approval",
        "observed_at": now, "provider_status": "VERIFIED",
        "provenance": "FACT", "actor_ref": "human:A"}
    grant = {
        "grant_ref": "grant:signed", "projects": [PROJECT], "items": [ITEM],
        "actions": list(ACTIONS),
        "scope_digests": {ITEM: "sha256:" + "a" * 64},
        "valid_from": now_iso(-60), "expires_at": now_iso(600),
        "budget": {"wall_seconds": 600, "max_attempts": 2,
                   "max_output_bytes": 65536, "exhausted": False}}
    if grant_over:
        grant.update(grant_over)
    payload = {"schema": actor.DELEGATION_EVIDENCE_SCHEMA,
               "provider_id": "provider:fixture-human-control",
               "key_id": "test-key", "audience": "device:qa-p",
               "issued_at": now, "actor_report": report, "grant": grant}
    sig = private.sign(actor.signed_bytes(payload))
    return {"payload": payload,
            "signature": base64.b64encode(sig).decode()}


PEER_A = {"pid": 9001, "uid": 4242, "gid": 4242}
PEER_B = {"pid": 9002, "uid": 9999, "gid": 9999}
issued = rpc("agent.grant.issue", {"evidence": signed_evidence()}, peer=PEER_A)
G = issued.get("grant") or {}
ctx_ref = G.get("delegated_by")
check("P3 signed grant issued with a store epoch", G.get("epoch"), 1)


def verify(*, action="lease.acquire", project=PROJECT, item_ref=ITEM,
           expected_epoch=None, peer=PEER_A, grant_ref="grant:signed"):
    params = {"context_ref": ctx_ref, "grant_ref": grant_ref, "action": action,
              "project": project, "item_ref": item_ref}
    if expected_epoch is not None:
        params["expected_epoch"] = expected_epoch
    return rpc("agent.lease.authority.verify", params, peer=peer)


pos = verify()
check("P3 positive verify with the issuing peer", pos.get("verified"), True)
result_grant = pos.get("grant") or {}
missing = sorted({f for f in LeaseGrant.__dataclass_fields__} - set(result_grant))
check("P3 result is the exact WE LeaseGrant field set", missing, [])
check("P3 project/items/actions are the stored authorized scope",
      (result_grant.get("project"), result_grant.get("items"),
       result_grant.get("actions")),
      (PROJECT, (ITEM,), ("lease.acquire",)))
check("P3 window is epoch floats",
      (isinstance(result_grant.get("valid_from"), float),
       isinstance(result_grant.get("expires_at"), float)), (True, True))
try:
    constructed = LeaseGrant(**result_grant)
    check("P3 real LeaseGrant constructs", constructed.os_principal, "uid:4242")
except TypeError as exc:
    check("P3 real LeaseGrant constructs", f"TypeError:{exc}", True)

wrong = verify(peer=PEER_B)
check("P3 source/os-peer mismatch refused", wrong.get("verified"), False,
      security=True)
check("P3 mismatch reason UNTRUSTED_ACTOR", wrong.get("reason"),
      "UNTRUSTED_ACTOR")
check("P3 altered expected_epoch refused",
      verify(expected_epoch=2).get("reason"), "INVALID_CONTEXT", security=True)
check("P3 scope outside the grant refused",
      verify(action="lease.delete").get("reason"), "POLICY_DENIED")

# Unbound holder (put_grant records no os_principal).
unbound = store.put_grant({
    "grant_ref": "grant:unbound", "delegated_by": ctx_ref,
    "valid_from": now_iso(-60), "expires_at": now_iso(600),
    "projects": [PROJECT], "items": [ITEM], "actions": list(ACTIONS)})
check("P3 put_grant leaves the holder unbound", unbound.get("os_principal"), None)
check("P3 unbound grant refused",
      verify(grant_ref="grant:unbound").get("reason"), "UNTRUSTED_ACTOR",
      security=True)
try:
    store.put_grant({"grant_ref": "grant:smuggle", "delegated_by": ctx_ref,
                     "valid_from": now_iso(-60), "expires_at": now_iso(600),
                     "projects": [PROJECT], "items": [ITEM],
                     "actions": list(ACTIONS), "os_principal": "uid:4242"})
    smuggle = "ACCEPTED"
except Exception as exc:  # the daemon's store class is a second module instance
    smuggle = ("REFUSED" if "os_principal" in str(exc)
               else f"OTHER:{type(exc).__name__}:{exc}")
check("P3 caller-supplied os_principal refused", smuggle, "REFUSED",
      security=True)

# Legacy grant without an epoch is UNKNOWN, never backfilled.
with store._mutation():
    rec = dict(store.get_grant("grant:unbound"))
    rec["grant_ref"] = "grant:legacy"
    rec["os_principal"] = "uid:4242"
    rec.pop("epoch", None)
    store._data["grants"]["grant:legacy"] = rec
    store._flush()
check("P3 missing grant epoch refused",
      verify(grant_ref="grant:legacy").get("reason"), "INVALID_CONTEXT",
      security=True)

store.revoke_grant("grant:signed", at=now_iso(), reason="qa")
check("P3 revocation refused",
      verify().get("reason"), "POLICY_DENIED", security=True)
store_actor.DELEGATION_TRUST = old_trust

# ===========================================================================
# P4 — T10: WE15 durable records through the V3 strict and legacy consumers.
# ===========================================================================
def git_tree(root):
    tree = root / "workspace" / "worktrees" / "demo" / "item"
    tree.mkdir(parents=True)
    for a in (("git", "init", "-q"),
              ("git", "config", "user.email", "t10@example.invalid"),
              ("git", "config", "user.name", "T10")):
        subprocess.run(a, cwd=tree, check=True, capture_output=True)
    (tree / "README").write_text("signed finalization fixture\n", encoding="utf-8")
    subprocess.run(("git", "add", "README"), cwd=tree, check=True,
                   capture_output=True)
    subprocess.run(("git", "commit", "-qm", "initial"), cwd=tree, check=True,
                   capture_output=True)
    head = subprocess.run(("git", "rev-parse", "HEAD"), cwd=tree, check=True,
                          capture_output=True, text=True).stdout.strip()
    return tree, head


def control_grant(tree):
    return {"delegated_by": CONTEXT, "items": [ITEM],
            "actions": ["engineering.execute"], "budget": dict(BUDGET),
            "valid_from": minutes(-1), "expires_at": minutes(60),
            "revoked": False,
            "engineering": {"schema": executor.SCHEMA, "worktree": str(tree),
                            "image": "sha256:" + "1" * 64,
                            "memory_bytes": 64 * 1024 ** 2, "cpus": 1,
                            "pids_limit": 16}}


def checkpoint(tree):
    return continuation.build_checkpoint(
        work_item=ITEM, epoch=EPOCH, parent_owner="parent-a",
        next_safe_action="run the offline engineering check",
        waiting_on={"kind": "window", "until": minutes(10)}, now=now_iso(),
        requirement_revision="req-1", write_scope=[str(tree)])


def envelope(record, g):
    return wake.capability_envelope(
        record, g, GRANT, attempt=1, key="continuation-wake-SRV-0001-e1-a1",
        spend={"wall_seconds": BUDGET["wall_seconds"],
               "attempts_allowed": BUDGET["max_attempts"]}, now=now_iso())


def make_store(root, head, tree):
    st = ServerOSStore(root / "we-state")
    st.register_project("demo", "SRV")
    item = st.create_item(WorkItem(ITEM, "demo", "x", "test", "/card"))
    st.add_activity(Activity(item.id, ACTIVITY, "test"))
    st.attach_context(ExecutionContext(
        item.id, ACTIVITY, "repo", ContextMode.TEST, head,
        worktree=str(tree), dirty=False))
    run = st.begin_run(item.id, ACTIVITY, RUNNER, "signed-finalize-1",
                       root / "artifacts")
    for s in (RunState.PREPARING, RunState.RUNNING, RunState.FINALIZING):
        run = st.advance_run(item.id, run.id, s)
    return st, item, run


def real_receipt(root, st, tree, exit_code):
    g = control_grant(tree)
    cp = checkpoint(tree)
    env = envelope(cp, g)
    resolved = executor.binding(envelope=env, grant=g, checkpoint=cp,
                                worktrees_root=tree.parents[1])
    receipts = root / "receipts"
    receipts.mkdir(mode=0o700)
    os.chmod(receipts, 0o700)
    runner = root / "synthetic_runner.py"
    runner.write_text(RUNNER_SOURCE, encoding="utf-8")
    real = bounded.invoke_once

    def fake_invoke(*, key, argv, cwd, receipts, input_text, timeout,
                    readonly, max_output_bytes, env=None):
        return real(key=key, argv=[sys.executable, str(runner), str(exit_code)],
                    cwd=cwd, receipts=receipts, input_text=input_text,
                    timeout=timeout, readonly=readonly,
                    max_output_bytes=max_output_bytes, env=env)

    old_invoke = executor.bounded.invoke_once
    old_settle = executor._settle
    executor.bounded.invoke_once = fake_invoke
    executor._settle = lambda config: {"worker_stopped": True,
                                       "effect": "EXECUTED_UNVERIFIED"}
    try:
        return executor.execute(
            envelope=env, grant=g, resolved=resolved, key="verify",
            argv=["python3", "-c", "ignored"], receipts=receipts,
            state_db=st.db_path)
    finally:
        executor.bounded.invoke_once = old_invoke
        executor._settle = old_settle


class TestReceiptAuthority:
    """TEST DOUBLE around a real invoke_once receipt. NOT a production parent."""

    def __init__(self, receipt, ref="opaque:receipt:1"):
        self.receipt = receipt
        self.ref = ref

    def verify(self, receipt_ref, *, item_ref, run_ref, now):
        if receipt_ref != self.ref:
            raise ReceiptError("UNTRUSTED_RECEIPT", "not this authority's ref")
        return self.receipt


def we15_receipt(fx, item, run, head):
    r = fx["receipt"]
    return VerifiedRunReceipt(
        item_ref=item.id, run_ref=run.id, action_key=r["action_key"],
        input_digest=r["input_digest"], receipt_sha256=fx["receipt_sha256"],
        process_exit_code=r["process_exit_code"],
        verdict="PASS" if r["process_exit_code"] == 0 else "FAIL",
        runner_identity=RUNNER, commit_sha=head, actor_ref=CONTEXT)


def expected_for(item, run, head, receipt):
    return GATE.ExpectedFinalization(
        item_ref=item.id, run_ref=run.id, commit_sha=head, actor_ref=CONTEXT,
        action_key=receipt.action_key, input_digest=receipt.input_digest,
        receipt_sha256=receipt.receipt_sha256,
        process_exit_code=receipt.process_exit_code, runner_identity=RUNNER)


def rewrite_finalize(st, item, run, mutate, *, update_sha):
    loc = Path(st.get_run(item.id, run.id).artifact_dir) / "finalize.json"
    payload = json.loads(loc.read_text(encoding="utf-8"))
    mutate(payload)
    body = json.dumps(payload) + "\n"
    loc.write_text(body, encoding="utf-8")
    if update_sha:
        digest = hashlib.sha256(body.encode()).hexdigest()
        with st.connection() as c:
            c.execute("UPDATE evidence SET sha256=? WHERE item_id=? AND run_id=? "
                      "AND kind='finalize'", (digest, item.id, run.id))


base = Path(tempfile.mkdtemp(dir=SCRATCH, prefix="p-v3-"))
tree, head = git_tree(base / "tree")

# Legacy SUCCEEDED / UNSIGNED.
legacy_root = base / "legacy"
legacy_store, legacy_item, legacy_run = make_store(legacy_root, head, tree)
legacy_store.finalize_run(legacy_item.id, legacy_run.id, verdict="PASS",
                          exit_code=0, command="pytest", cases=["one"],
                          revision=head, dirty=False)
legacy_read = GATE.read_work_engine_finalization(
    legacy_store.db_path, legacy_item.id, legacy_run.id)
legacy_expected = GATE.ExpectedFinalization(
    item_ref=legacy_item.id, run_ref=legacy_run.id, commit_sha=head,
    actor_ref=CONTEXT, action_key="action:any", input_digest="a" * 64,
    receipt_sha256="b" * 64, process_exit_code=0, runner_identity=RUNNER)
legacy_decision = GATE.evaluate_signed_finalization(legacy_expected, legacy_read)
check("P4 legacy SUCCEEDED/UNSIGNED strict DENY", legacy_decision.decision,
      GATE.DECISION_DENY, security=True)
check("P4 legacy UNSIGNED reason",
      "UNSIGNED_FINALIZATION" in legacy_decision.reasons, True)
check("P4 legacy readback state/authorization",
      (legacy_read.authorization, legacy_read.run_state), ("UNSIGNED", "SUCCEEDED"))

# Pre-#15 unlabeled record: remove `authorization`, re-anchor the evidence sha.
rewrite_finalize(legacy_store, legacy_item, legacy_run,
                 lambda p: p.pop("authorization", None), update_sha=True)
unlabeled_read = GATE.read_work_engine_finalization(
    legacy_store.db_path, legacy_item.id, legacy_run.id)
unlabeled_strict = GATE.evaluate_signed_finalization(
    legacy_expected, unlabeled_read)
check("P4 pre-#15 unlabeled strict DENY", unlabeled_strict.decision,
      GATE.DECISION_DENY, security=True)
check("P4 strict reason AUTHORIZATION_NOT_T10_VERIFIED",
      "AUTHORIZATION_NOT_T10_VERIFIED" in unlabeled_strict.reasons, True)

# The legacy acceptance seam on the same durable record.
row = dict(legacy_store.connection().execute(
    "SELECT * FROM runs WHERE item_id=? AND run_id=?",
    (legacy_item.id, legacy_run.id)).fetchone())
erow = dict(legacy_store.connection().execute(
    "SELECT * FROM evidence WHERE item_id=? AND run_id=? AND kind='finalize'",
    (legacy_item.id, legacy_run.id)).fetchone())
loc = Path(erow["locator"])
snapshot = LEGACY.AuthoritySnapshot(
    run=row, finalize_evidence=(erow,), artifacts={erow["locator"]: loc.read_bytes()})
legacy_accept = LEGACY.evaluate_acceptance(
    LEGACY.ExpectedAcceptance(
        item_id=legacy_item.id, run_id=legacy_run.id, revision=head,
        principal=CONTEXT, command="pytest",
        recognized_runners={CONTEXT: (RUNNER,)}), snapshot)
check("P4 legacy consumer ACCEPTS pre-#15 unlabeled (fail-open)",
      legacy_accept.decision, LEGACY.DECISION_ELIGIBLE, security=True)
check("P4 legacy acceptance carries no label reason",
      [r for r in legacy_accept.reasons
       if r in ("UNSIGNED_FINALIZATION", "AUTHORIZATION_NOT_T10_VERIFIED",
                "T10_NOT_ACCEPTABLE")], [])

# Negative fixture: same legacy consumer sees a labelled UNSIGNED record.
legacy_store2, legacy_item2, legacy_run2 = make_store(base / "legacy2", head, tree)
legacy_store2.finalize_run(legacy_item2.id, legacy_run2.id, verdict="PASS",
                           exit_code=0, command="pytest", cases=["one"],
                           revision=head, dirty=False)
row2 = dict(legacy_store2.connection().execute(
    "SELECT * FROM runs WHERE item_id=? AND run_id=?",
    (legacy_item2.id, legacy_run2.id)).fetchone())
erow2 = dict(legacy_store2.connection().execute(
    "SELECT * FROM evidence WHERE item_id=? AND run_id=? AND kind='finalize'",
    (legacy_item2.id, legacy_run2.id)).fetchone())
legacy_accept2 = LEGACY.evaluate_acceptance(
    LEGACY.ExpectedAcceptance(
        item_id=legacy_item2.id, run_id=legacy_run2.id, revision=head,
        principal=CONTEXT, command="pytest",
        recognized_runners={CONTEXT: (RUNNER,)}),
    LEGACY.AuthoritySnapshot(
        run=row2, finalize_evidence=(erow2,),
        artifacts={erow2["locator"]: Path(erow2["locator"]).read_bytes()}))
check("P4 legacy consumer denies labelled UNSIGNED",
      legacy_accept2.decision, LEGACY.DECISION_DENY)

# T10_VERIFIED from a real receipt + WE15 finalize + test-double authority.
t10_root = base / "t10"
t10_store, t10_item, t10_run = make_store(t10_root, head, tree)
fx = real_receipt(t10_root, t10_store, tree, 0)
fx_ref = (fx,)
receipt = we15_receipt(fx, t10_item, t10_run, head)
authority = TestReceiptAuthority(receipt)
t10_store.finalize_run(
    t10_item.id, t10_run.id, verdict="PASS", exit_code=0, command="pytest",
    cases=["one"], revision=head, dirty=False, t10_required=True,
    t10_binding=T10Binding(item_ref=t10_item.id, run_ref=t10_run.id,
                           commit_sha=head, actor_ref=CONTEXT),
    receipt_ref=authority.ref, receipt_authority=authority)
t10_read = GATE.read_work_engine_finalization(
    t10_store.db_path, t10_item.id, t10_run.id)
t10_decision = GATE.evaluate_signed_finalization(
    expected_for(t10_item, t10_run, head, receipt), t10_read)
check("P4 T10_VERIFIED exit0 ELIGIBLE only", t10_decision.decision,
      GATE.DECISION_ELIGIBLE)
check("P4 ELIGIBLE carries no parent acceptance/signature",
      (t10_decision.parent_accepted, t10_decision.qa_signature), (False, False))
check("P4 production remains BLOCKED_TRUST_SOURCE",
      t10_decision.production_status, "BLOCKED_TRUST_SOURCE")

# Fake child PASS text + exit1 must not finalize.
fake_root = base / "fake"
fake_store, fake_item, fake_run = make_store(fake_root, head, tree)
fx1 = real_receipt(fake_root, fake_store, tree, 1)
fake_receipt = we15_receipt(fx1, fake_item, fake_run, head)
try:
    fake_store.finalize_run(
        fake_item.id, fake_run.id, verdict="PASS", exit_code=0,
        command="pytest", cases=["one"], revision=head, dirty=False,
        t10_required=True,
        t10_binding=T10Binding(item_ref=fake_item.id, run_ref=fake_run.id,
                               commit_sha=head, actor_ref=CONTEXT),
        receipt_ref="opaque:fake",
        receipt_authority=TestReceiptAuthority(fake_receipt, ref="opaque:fake"))
    fake = "ACCEPTED"
except ReceiptError as exc:
    fake = exc.reason_code
check("P4 fake child PASS/exit1 refused", fake in {
      "INVALID_RECEIPT", "RECEIPT_INCONSISTENT", "EXIT_CODE_DISAGREEMENT"}, True,
      security=True)
check("P4 fake child Run stays FINALIZING",
      fake_store.get_run(fake_item.id, fake_run.id).state, RunState.FINALIZING)
check("P4 fake child writes no evidence",
      fake_store.connection().execute(
          "SELECT COUNT(*) c FROM evidence WHERE item_id=? AND run_id=?",
          (fake_item.id, fake_run.id)).fetchone()["c"], 0)

# Tampered evidence bytes and cross-run against the T10 record.
tamper_store, tamper_item, tamper_run = make_store(base / "tamper", head, tree)
fx_t = real_receipt(base / "tamper", tamper_store, tree, 0)
tamper_receipt = we15_receipt(fx_t, tamper_item, tamper_run, head)
tamper_store.finalize_run(
    tamper_item.id, tamper_run.id, verdict="PASS", exit_code=0,
    command="pytest", cases=["one"], revision=head, dirty=False,
    t10_required=True,
    t10_binding=T10Binding(item_ref=tamper_item.id, run_ref=tamper_run.id,
                           commit_sha=head, actor_ref=CONTEXT),
    receipt_ref="opaque:t",
    receipt_authority=TestReceiptAuthority(tamper_receipt, ref="opaque:t"))
rewrite_finalize(tamper_store, tamper_item, tamper_run,
                 lambda p: p["cases"].append("injected"), update_sha=False)
tamper_read = GATE.read_work_engine_finalization(
    tamper_store.db_path, tamper_item.id, tamper_run.id)
tamper_decision = GATE.evaluate_signed_finalization(
    expected_for(tamper_item, tamper_run, head, tamper_receipt), tamper_read)
check("P4 tampered evidence bytes DENY", tamper_decision.decision,
      GATE.DECISION_DENY, security=True)
check("P4 tamper reason EVIDENCE_TAMPERED",
      "EVIDENCE_TAMPERED" in tamper_decision.reasons, True)

cross = GATE.ExpectedFinalization(
    **{**expected_for(t10_item, t10_run, head, receipt).__dict__,
       "run_ref": "RUN-9999"})
cross_decision = GATE.evaluate_signed_finalization(cross, t10_read)
check("P4 cross-run DENY", cross_decision.decision, GATE.DECISION_DENY,
      security=True)
check("P4 cross-run reason RUN_MISMATCH",
      "RUN_MISMATCH" in cross_decision.reasons, True)

# ===========================================================================
# P5 — source audit: legacy acceptance reachability + manifest forgery.
# ===========================================================================
vsrc = (TREE / "integrations/fleet-v1-verdict-evidence/verdict_evidence.py").read_text(encoding="utf-8")
psrc = (TREE / "integrations/fleet-v1-verdict-evidence/bounded_provenance.py").read_text(encoding="utf-8")
fsrc = (TREE / "integrations/fleet-v1-verdict-evidence/finalization_result.py").read_text(encoding="utf-8")
check("P5 legacy consumer keeps the unlabeled compatibility return",
      "authorization is None:\n        return" in vsrc, True)
check("P5 dispatch bridge calls the legacy acceptance seam",
      "verdict.evaluate_acceptance(" in psrc, True)
check("P5 continuation path does NOT call legacy acceptance",
      "evaluate_acceptance(" in psrc.split("def evaluate_continuation")[1]
      .split("def _check_continuation_authority")[0], False)
check("P5 continuation checks grant revocation",
      "authority.grant_revoked" in psrc, True)
check("P5 continuation authority carries no epoch field",
      any("epoch" in line for line in
          psrc.split("class ContinuationAuthority")[1]
          .split("class ")[0].splitlines()), False)
check("P5 strict gate has no grant/epoch input",
      [n for n in ("grant_ref", "grant_epoch", "epoch")
       if n in fsrc.split("class ExpectedFinalization")[1].split("@dataclass")[0]],
      [])

prod_py = [p for p in TREE.rglob("*.py")
           if "/tests/" not in p.as_posix()
           and "fleet-v1-verdict-evidence" not in p.as_posix()
           and "fleet-v1-release" not in p.as_posix()]
check("P5 no production module imports finalization_result",
      any("finalization_result" in p.read_text(encoding="utf-8") for p in prod_py),
      False)
check("P5 no production module imports verdict_evidence",
      any("verdict_evidence" in p.read_text(encoding="utf-8") for p in prod_py),
      False)
release_py = [p for p in TREE.glob("integrations/fleet-v1-release/**/*.py")]
release_py += [p for p in TREE.glob("bin/**/*.py")]
check("P5 no V1/release code path reads finalize.json or t10_acceptable",
      any(t in p.read_text(encoding="utf-8")
          for p in release_py
          for t in ("finalize.json", "t10_acceptable")), False)

checker = TREE / "integrations/fleet-v1-release/check_manifest.py"
manifest = TREE / "integrations/fleet-v1-release/PREVIEW-MANIFEST-2026-10-18.json"


def run_checker(path):
    done = subprocess.run([sys.executable, str(checker), str(path)],
                          capture_output=True, text=True, timeout=90)
    return done.returncode, done.stdout


code, out = run_checker(manifest)
check("P5 V3 preview manifest passes the checker", code, 0)
check("P5 V3 preview declares not_release/all_release_gates false",
      ('"not_release": true' in manifest.read_text(encoding="utf-8")
       and '"all_release_gates": false' in manifest.read_text(encoding="utf-8")),
      True)
code, out = subprocess.run([sys.executable, str(checker), "--self-test"],
                           capture_output=True, text=True, timeout=90).returncode, ""
check("P5 checker self-test passes", code, 0)

doc = json.loads(manifest.read_text(encoding="utf-8"))
forged = copy.deepcopy(doc)
forged["claims"]["final_acceptance"] = True
forged["qa"]["signature"] = "forged"
fpath = SCRATCH / "forged-manifest.json"
fpath.write_text(json.dumps(forged), encoding="utf-8")
code, out = run_checker(fpath)
check("P5 forged final acceptance is refused", code, 1, security=True)
check("P5 forged acceptance names the over-claim",
      "E082" in out or "final_acceptance" in out, True)

forged2 = copy.deepcopy(doc)
forged2["preview"]["not_release"] = False
f2 = SCRATCH / "forged-not-release.json"
f2.write_text(json.dumps(forged2), encoding="utf-8")
code, out = run_checker(f2)
check("P5 forged not_release=false is refused", code, 1, security=True)

manifest_doc = json.loads(manifest.read_text(encoding="utf-8"))
check("P5 manifest pins the composed #271/#275/#274 SHAs",
      manifest_doc["preview"]["composed_from"],
      ["68e470f98add3587fc26ef355f7f0026761832a0",
       "524d35ce559002bb33ef0d4941c13f8b6b5fd283",
       "f8246cedb51621e6b6111672567067c2fcef828a"])
check("P5 manifest QA pin is Snapshot N, not the live Snapshot O/P head",
      (manifest_doc["qa_triage"]["head"],
       manifest_doc["qa_triage"]["head"] == "1222c74654f1209193be4570c4b1c6a2b09ceca3"),
      ("1222c74654f1209193be4570c4b1c6a2b09ceca3", True))

print()
print("scratch:", base)
if failures:
    print(f"PROBE FAILED: {len(failures)} check(s): {failures}")
    sys.exit(1)
print("PROBE OK: combined V3 checks held")
