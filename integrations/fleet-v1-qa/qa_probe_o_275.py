#!/usr/bin/env python3
"""Independent QA Snapshot O (P0-A) — #272 red vs #275 green, real signed issue.

QA_MODE=old QA_TREE=<#272 fe4738a>  -> reproduce Snapshot N's 2 SECURITY_FAILs
QA_MODE=new QA_TREE=<#275 524d35c>  -> exact LeaseGrant + kernel-peer binding

Real `Daemon` `agent.grant.issue` with a REAL Ed25519-signed test delegation
(LOCAL fixture key; not a production provider), then
`agent.lease.authority.verify` with a peer supplied to `handle(peer=...)` (the
in-process seam the Unix socket fills from SO_PEERCRED). NOT REAL_DEVICE.
"""
from __future__ import annotations

import base64
import copy
import importlib.util
import json
import os
import sys
import tempfile
from dataclasses import fields
from pathlib import Path

TREE = Path(os.environ["QA_TREE"]).resolve()
WE = Path(os.environ["WE_TREE"]).resolve()
SCRATCH = Path(os.environ["QA_SCRATCH"]).resolve()
MODE = os.environ["QA_MODE"]
RUNTIME = TREE / "runtime"
sys.path.insert(0, str(WE))
from workengine.authority import LeaseGrant  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from cryptography.hazmat.primitives.serialization import (  # noqa: E402
    Encoding, PublicFormat)

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
    print(f"{tag} [{MODE}] {label}: got={got!r} expected={expected!r}")
    if not ok:
        failures.append(f"[{MODE}] {label}")


SCRATCH.mkdir(parents=True, exist_ok=True)
state = SCRATCH / "state"
state.mkdir(parents=True, exist_ok=True)
daemon = daemon_mod.Daemon(
    registry_mod.Registry(document={
        "schema": "serveros/managed-runtime-registry.v1",
        "device_ref": "device:qa-o", "resources": []}),
    state_dir=state, socket_path=SCRATCH / "run/serveros.sock",
    log=lambda message: None)
store = daemon.control_store()


def iso(offset=0.0):
    return daemon_mod._iso(daemon._now() + offset)


# A real signed delegation with a LOCAL test key; the provider is a fixture.
private = Ed25519PrivateKey.generate()
public = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
trust_path = SCRATCH / "operator-trust.json"
trust_path.write_text(json.dumps({
    "schema": "serveros/human-control-trust.v1", "audience": "device:qa-o",
    "providers": {"provider:fixture-human-control": {"keys": {"test-key": {
        "enabled": True,
        "public_key": base64.b64encode(public).decode()}}}}}))

store_actor = store_mod._actor_module()
old_trust = store_actor.DELEGATION_TRUST
store_actor.DELEGATION_TRUST = trust_path


def signed_evidence(*, grant_over=None, issued_at=None):
    now = issued_at or iso()
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
        "valid_from": iso(-60), "expires_at": iso(600),
        "budget": {"wall_seconds": 600, "max_attempts": 2,
                   "max_output_bytes": 65536, "exhausted": False}}
    if grant_over:
        grant.update(grant_over)
    payload = {"schema": actor_mod.DELEGATION_EVIDENCE_SCHEMA,
               "provider_id": "provider:fixture-human-control",
               "key_id": "test-key", "audience": "device:qa-o",
               "issued_at": now, "actor_report": report, "grant": grant}
    sig = private.sign(actor_mod.signed_bytes(payload))
    return {"payload": payload,
            "signature": base64.b64encode(sig).decode()}, grant


def issue(evidence, peer, *, os_principal=None):
    params = {"evidence": evidence}
    if os_principal is not None:
        # old/new stores may reject this; only used in the explicit negative.
        pass
    answer = daemon.handle({"method": "agent.grant.issue",
                            "params": params}, peer=peer)
    return answer.get("result", answer)


def verify(ctx_ref, *, grant_ref="grant:signed", action="lease.acquire",
           project=PROJECT, item_ref=ITEM, expected_epoch=None, peer=None):
    params = {"context_ref": ctx_ref, "grant_ref": grant_ref, "action": action,
              "project": project, "item_ref": item_ref}
    if expected_epoch is not None:
        params["expected_epoch"] = expected_epoch
    answer = daemon.handle({"method": "agent.lease.authority.verify",
                            "params": params}, peer=peer)
    return answer.get("result", answer)


PEER_A = {"pid": 9001, "uid": 4242, "gid": 4242}
PEER_B = {"pid": 9002, "uid": 9999, "gid": 9999}
evidence, grant_payload = signed_evidence()
issued = issue(evidence, PEER_A)
check("O1 signed grant issued", "grant" in issued, True)
G = issued.get("grant") or {}
ctx_ref = G.get("delegated_by")
check("O1 epoch minted", G.get("epoch"), 1)

pos = verify(ctx_ref, peer=PEER_A)
check("O2 positive verify", pos.get("verified"), True)
grant = pos.get("grant") or {}

# The two Snapshot N failures, re-checked.
we_fields = {f.name for f in fields(LeaseGrant)}
missing = sorted(we_fields - set(grant))
check("O3 the result carries all WE14 LeaseGrant fields", missing, [])
check("O3 project comes from the stored/granted scope",
      grant.get("project"), PROJECT)
check("O3 items is a tuple of the requested Item",
      grant.get("items"), (ITEM,))
check("O3 actions is a tuple of the requested action",
      grant.get("actions"), ("lease.acquire",))
check("O3 window is epoch floats",
      (isinstance(grant.get("valid_from"), float),
       isinstance(grant.get("expires_at"), float)), (True, True))
try:
    constructed = LeaseGrant(**grant)
    ok = (constructed.principal == "human:A"
          and constructed.os_principal == "uid:4242")
except TypeError as exc:
    constructed, ok = None, f"TypeError:{exc}"
check("O3 the real WE14 LeaseGrant constructs", ok, True)

# P1: the bound OS principal is compared, not copied.
wrong = verify(ctx_ref, peer=PEER_B)
check("O4 a different OS uid refuses", wrong.get("verified"), False,
      security=True)
check("O4 reason is UNTRUSTED_ACTOR", wrong.get("reason"), "UNTRUSTED_ACTOR")

# Unbound legacy grant (os_principal omitted) refuses.
with store._mutation():
    rec = dict(store.get_grant("grant:signed"))
    rec.pop("os_principal", None)
    store._data["grants"]["grant:unbound"] = rec
    store._flush()
check("O5 unbound grant refuses",
      verify(ctx_ref, grant_ref="grant:unbound", peer=PEER_A).get("reason"),
      "UNTRUSTED_ACTOR", security=True)

# Revoked / expired / old epoch still refuse.
store.revoke_grant("grant:signed", at=iso(), reason="qa")
check("O6 revoked grant refuses",
      verify(ctx_ref, peer=PEER_A).get("reason"), "POLICY_DENIED")

# A signed payload that names os_principal is refused at issuance.
ev2, _ = signed_evidence()
ev2["payload"]["grant"]["os_principal"] = "uid:4242"
sig = private.sign(actor_mod.signed_bytes(ev2["payload"]))
ev2["signature"] = base64.b64encode(sig).decode()
answer2 = issue(ev2, PEER_A)
smuggled = ("REFUSED" if (not isinstance(answer2, dict)
                           or answer2.get("ok") is False
                           or "grant" not in answer2) else "ACCEPTED")
check("O7 signed payload may not name os_principal", smuggled, "REFUSED",
      security=True)

# put_grant may not name one either.
try:
    store.put_grant({"grant_ref": "grant:put", "delegated_by": ctx_ref,
                     "valid_from": iso(-60), "expires_at": iso(600),
                     "projects": [PROJECT], "items": [ITEM],
                     "actions": list(ACTIONS), "os_principal": "uid:4242"})
    put_smuggled = "ACCEPTED"
except store_mod.StoreError:
    put_smuggled = "REFUSED"
check("O7 put_grant may not name os_principal", put_smuggled, "REFUSED",
      security=True)

# Malformed peer / epoch inputs refuse instead of raising.
for label, peer in (("bool", {"pid": 1, "uid": True, "gid": 1}),
                    ("string", {"pid": 1, "uid": "4242", "gid": 1}),
                    ("negative", {"pid": 1, "uid": -1, "gid": 1})):
    check(f"O8 malformed peer uid {label} refuses",
          verify(ctx_ref, peer=peer).get("reason"), "UNTRUSTED_ACTOR")
check("O8 non-int expected_epoch refuses",
      verify(ctx_ref, grant_ref="grant:unbound", peer=PEER_A,
             expected_epoch="1").get("reason"),
      "INVALID_CONTEXT")

# Unreadable/naive grant window refuses.
with store._mutation():
    rec = dict(store.get_grant("grant:unbound"))
    rec["grant_ref"] = "grant:naive"
    rec["os_principal"] = "uid:4242"
    rec["valid_from"] = "2026-10-18T08:00:00"
    store._data["grants"]["grant:naive"] = rec
    store._flush()
check("O9 naive timestamp is INVALID_CONTEXT",
      verify(ctx_ref, grant_ref="grant:naive", peer=PEER_A).get("reason"),
      "INVALID_CONTEXT")

# SOURCE: issuance binds the kernel peer; socket fills peer; no widening.
daemon_src = (RUNTIME / "daemon.py").read_text(encoding="utf-8")
store_src = (RUNTIME / "agent_control_store.py").read_text(encoding="utf-8")
check("S1 grant issue binds os_principal from the kernel peer",
      "os_principal=agent_control_store_mod.os_principal_of(_CALLER_PEER.get())"
      in daemon_src, True)
check("S1 socket serve loop fills peer from SO_PEERCRED",
      "handle(request, peer=_peer_credentials(connection))" in daemon_src
      and "SO_PEERCRED" in daemon_src, True)
check("S1 verify returns the granted scope, not the whole grant",
      '"items": (str(item_ref),)' in store_src
      and '"actions": (str(action),)' in store_src, True)

store_actor.DELEGATION_TRUST = old_trust
print()
print("scratch:", SCRATCH)
if failures:
    print(f"PROBE FAILED: {len(failures)} check(s): {failures}")
    sys.exit(1)
print("PROBE OK: #275 exact LeaseGrant + bound kernel peer")
