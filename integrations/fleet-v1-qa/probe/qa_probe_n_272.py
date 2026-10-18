#!/usr/bin/env python3
"""Independent QA Snapshot N — serveros #272 grant epoch + trusted verify read.

QA_TREE=<#272 fe4738a> read-only; WE_TREE=<WE14 e4c4a2b> for the LeaseGrant
schema comparison. Drives the REAL AgentControlStore + Daemon RPC
`agent.lease.authority.verify`, with the peer supplied to `handle(peer=...)`
(the in-process seam the Unix socket fills from SO_PEERCRED). LOCAL fixture,
NOT REAL_DEVICE OS attestation.

No grant widening, no lease acquire, no provider/network/credential.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from dataclasses import fields
from pathlib import Path

TREE = Path(os.environ["QA_TREE"]).resolve()
WE = Path(os.environ["WE_TREE"]).resolve()
SCRATCH = Path(os.environ["QA_SCRATCH"]).resolve()
RUNTIME = TREE / "runtime"
sys.path.insert(0, str(WE))
from workengine.authority import LeaseGrant  # noqa: E402

failures: list[str] = []
ITEM = "SRV-0001"
PROJECT = "serveros"
ACTIONS = ("lease.acquire", "lease.renew", "lease.release", "work.commit")


def load(name, path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


daemon_mod = load("serveros_runtime_daemon", RUNTIME / "daemon.py")
registry_mod = load("serveros_runtime_registry", RUNTIME / "registry.py")
store_mod = load("serveros_runtime_agent_control_store", RUNTIME / "agent_control_store.py")
actor_mod = load("serveros_context_actor", TREE / "context" / "actor.py")


def check(label, got, expected, *, security=False):
    ok = got == expected
    tag = "PASS" if ok else ("SECURITY_FAIL" if security else "FAIL")
    print(f"{tag} {label}: got={got!r} expected={expected!r}")
    if not ok:
        failures.append(label)


def a_registry():
    return registry_mod.Registry(document={
        "schema": "serveros/managed-runtime-registry.v1",
        "device_ref": "device:qa-n", "resources": []})


SCRATCH.mkdir(parents=True, exist_ok=True)
daemon = daemon_mod.Daemon(
    a_registry(), state_dir=SCRATCH / "state",
    socket_path=SCRATCH / "run/serveros.sock", log=lambda message: None)
store = daemon.control_store()


def iso(offset=0.0):
    return daemon_mod._iso(daemon._now() + offset)


def a_context(actor, *, pid=4242):
    now = iso()
    rec = actor_mod.build({
        "human_control_provider": "provider:botmux-human-control",
        "transport": "feishu",
        "transport_identity": "botmux-verified-enterprise-user",
        "conversation_ref": "oc_qa_n", "message_ref": "om_qa_n",
        "observed_at": now, "provider_status": "VERIFIED",
        "provenance": "FACT", "actor_ref": actor, "provider_pid": pid,
    }, now=now)
    store.put_context(rec)
    return rec


CTX_A = a_context("human:A")
CTX_B = a_context("human:B")


def a_grant(ref, ctx, *, projects=(PROJECT,), items=(ITEM,), actions=ACTIONS,
            expires_in=600, valid_from_offset=-60):
    return store.put_grant({
        "grant_ref": ref, "delegated_by": ctx["context_ref"],
        "valid_from": iso(valid_from_offset), "expires_at": iso(expires_in),
        "projects": list(projects), "items": list(items),
        "actions": list(actions)})


PEER = {"pid": 9001, "uid": 4242, "gid": 4242}


def verify(ctx_ref, *, grant_ref="grant:1", action="lease.acquire",
           project=PROJECT, item_ref=ITEM, expected_epoch=None, peer=PEER):
    params = {"grant_ref": grant_ref, "action": action, "project": project,
              "item_ref": item_ref}
    if ctx_ref is not None:
        params["context_ref"] = ctx_ref
    if expected_epoch is not None:
        params["expected_epoch"] = expected_epoch
    answer = daemon.handle({"method": "agent.lease.authority.verify",
                            "params": params}, peer=peer)
    return answer.get("result", answer)


G1 = a_grant("grant:1", CTX_A)
G2 = a_grant("grant:2", CTX_A)
check("N1 epoch minted by the store", (G1.get("epoch"), G2.get("epoch")), (1, 2))

# Restart monotonicity: a new store instance reads the durable counter.
store2 = store_mod.AgentControlStore(store.path)
G3 = store2.put_grant({
    "grant_ref": "grant:3", "delegated_by": CTX_A["context_ref"],
    "valid_from": iso(-60), "expires_at": iso(600),
    "projects": [PROJECT], "items": [ITEM], "actions": list(ACTIONS)})
check("N2 epoch survives restart and stays monotonic", G3.get("epoch"), 3)

# Caller-supplied epoch is refused.
try:
    store.put_grant({"grant_ref": "grant:epoch", "delegated_by": CTX_A["context_ref"],
                     "valid_from": iso(-60), "expires_at": iso(600),
                     "projects": [PROJECT], "items": [ITEM],
                     "actions": list(ACTIONS), "epoch": 99})
    caller_epoch = "ACCEPTED"
except store_mod.StoreError:
    caller_epoch = "REFUSED"
check("N3 caller-supplied epoch refused", caller_epoch, "REFUSED", security=True)

# Positive read.
before = store.get_grant("grant:1")
pos = verify(CTX_A["context_ref"])
check("N4 positive verify", pos.get("verified"), True)
check("N4 principal is the context actor",
      (pos.get("grant") or {}).get("principal"), "human:A")
check("N4 os_principal is the peer uid",
      (pos.get("grant") or {}).get("os_principal"), "uid:4242")
check("N4 epoch is the store epoch", (pos.get("grant") or {}).get("epoch"), 1)
check("N4 read mutates no grant", store.get_grant("grant:1"), before)

# Old epoch, revoked, expired, not-yet-valid.
check("N5 old expected epoch refused",
      verify(CTX_A["context_ref"], grant_ref="grant:2",
             expected_epoch=1).get("reason"), "INVALID_CONTEXT")
store.revoke_grant("grant:1", at=iso(), reason="qa")
check("N6 revoked grant refused",
      verify(CTX_A["context_ref"]).get("reason"), "POLICY_DENIED")
EXP = a_grant("grant:exp", CTX_A, expires_in=-1)
check("N7 expired grant refused",
      verify(CTX_A["context_ref"], grant_ref="grant:exp").get("reason"),
      "POLICY_DENIED")
FUT = a_grant("grant:future", CTX_A, valid_from_offset=600)
check("N8 not-yet-valid grant refused",
      verify(CTX_A["context_ref"], grant_ref="grant:future").get("reason"),
      "POLICY_DENIED")

# Context / peer / identity.
check("N9 absent context refused", verify(None).get("reason"), "UNTRUSTED_ACTOR")
check("N9 unknown context refused",
      verify("ctx:invented").get("reason"), "UNTRUSTED_ACTOR")
check("N10 absent peer refused",
      verify(CTX_A["context_ref"], grant_ref="grant:2", peer=None).get("reason"),
      "UNTRUSTED_ACTOR")
check("N11 cross-actor context refused",
      verify(CTX_B["context_ref"], grant_ref="grant:2").get("reason"),
      "UNTRUSTED_ACTOR")

# Wrong Item / project / action.
check("N12 wrong item refused",
      verify(CTX_A["context_ref"], grant_ref="grant:2",
             item_ref="SRV-9999").get("reason"), "POLICY_DENIED")
check("N12 wrong project refused",
      verify(CTX_A["context_ref"], grant_ref="grant:2",
             project="other").get("reason"), "POLICY_DENIED")
check("N12 action outside grant refused",
      verify(CTX_A["context_ref"], grant_ref="grant:2",
             action="lease.delete").get("reason"), "POLICY_DENIED")

# Mismatched OS peer: the grant has no bound uid, so a different uid still verifies.
p4242 = verify(CTX_A["context_ref"], grant_ref="grant:2", peer=PEER)
p9999 = verify(CTX_A["context_ref"], grant_ref="grant:2",
               peer={"pid": 9002, "uid": 9999, "gid": 9999})
check("N13 a different OS peer uid is refused",
      p9999.get("verified"), False, security=True)
check("N13 peer mismatch is visible in os_principal",
      ((p4242.get("grant") or {}).get("os_principal"),
       (p9999.get("grant") or {}).get("os_principal")),
      ("uid:4242", "uid:9999"))

# Tampered peer string at the in-process seam.
str_peer = verify(CTX_A["context_ref"], grant_ref="grant:2",
                  peer={"pid": 9003, "uid": "999", "gid": 999})
check("N14 numeric-string peer is coerced, not refused",
      (str_peer.get("verified"), (str_peer.get("grant") or {}).get("os_principal")),
      (True, "uid:999"))
bad_peer = verify(CTX_A["context_ref"], grant_ref="grant:2",
                  peer={"pid": 9004, "uid": "not-a-uid", "gid": 999})
check("N14 non-numeric peer is a typed error, not a crash",
      bad_peer.get("ok") is False and bad_peer.get("error", {}).get("code") == "ValueError",
      True)

# Duplicate / altered re-report.
again = store.put_grant({k: G2[k] for k in (
    "grant_ref", "delegated_by", "valid_from", "expires_at",
    "projects", "items", "actions")})
check("N15 identical re-report is idempotent (same epoch)",
      again.get("epoch"), 2)
try:
    store.put_grant({"grant_ref": "grant:2", "delegated_by": CTX_A["context_ref"],
                     "valid_from": iso(-60), "expires_at": iso(600),
                     "projects": [PROJECT], "items": ["SRV-OTHER"],
                     "actions": list(ACTIONS)})
    altered = "ACCEPTED"
except store_mod.StoreError:
    altered = "REFUSED"
check("N15 altered re-report refused", altered, "REFUSED", security=True)

# Legacy grant without epoch is UNKNOWN, never backfilled.
legacy = {"grant_ref": "grant:legacy", "delegated_by": CTX_A["context_ref"],
          "valid_from": iso(-60), "expires_at": iso(600),
          "projects": [PROJECT], "items": [ITEM], "actions": list(ACTIONS),
          "revoked": False}
with store._mutation():
    store._data["grants"]["grant:legacy"] = legacy
    store._flush()
check("N16 legacy grant without epoch refused",
      verify(CTX_A["context_ref"], grant_ref="grant:legacy").get("reason"),
      "INVALID_CONTEXT")

# ---------------------------------------------------------------------------
# Schema: serveros result vs WE14 LeaseGrant.
# ---------------------------------------------------------------------------
result_grant = pos.get("grant") or {}
we_fields = {f.name for f in fields(LeaseGrant)}
missing = sorted(we_fields - set(result_grant))
check("N17 WE14 LeaseGrant fields missing from the serveros result",
      missing, ["actions", "items", "project"])
check("N17 timestamps are ISO strings, not epoch floats",
      (isinstance(result_grant.get("valid_from"), str),
       isinstance(result_grant.get("expires_at"), str)), (True, True))
try:
    LeaseGrant(**result_grant)
    construct = "CONSTRUCTED"
except TypeError as exc:
    construct = "TYPE_ERROR" if "required" in str(exc) else f"other:{exc}"
check("N17 result can construct a WE14 LeaseGrant", construct, "CONSTRUCTED",
      security=True)

# ---------------------------------------------------------------------------
# SOURCE — real socket fills the peer; the read is read-only; no widening.
# ---------------------------------------------------------------------------
daemon_src = (RUNTIME / "daemon.py").read_text(encoding="utf-8")
store_src = (RUNTIME / "agent_control_store.py").read_text(encoding="utf-8")
check("S1 handle(peer=...) sets the caller peer contextvar",
      "_CALLER_PEER.set(peer)" in daemon_src, True)
check("S1 the Unix serve loop fills the peer from SO_PEERCRED",
      "handle(request, peer=_peer_credentials(connection))" in daemon_src
      and "SO_PEERCRED" in daemon_src, True)
check("S1 verify_live_grant is read-only",
      all(token not in store_src.split("def verify_live_grant")[1]
          .split("def _intent_digest")[0]
          for token in ("_mutation", "_flush", "put_grant")), True)
check("S1 no acquire_lease in serveros",
      any("acquire_lease(" in p.read_text(encoding="utf-8")
          for p in TREE.rglob("*.py") if "/tests/" not in p.as_posix()), False)
check("S1 positive fixture has exactly the granted actions",
      sorted(G1["actions"]), sorted(ACTIONS))
check("S1 grant is not widened by the read", store.get_grant("grant:2")["actions"],
      list(ACTIONS))

print()
print("scratch:", SCRATCH)
if failures:
    print(f"PROBE FAILED: {len(failures)} check(s): {failures}")
    sys.exit(1)
print("PROBE OK: #272 epoch/verify checks held")
