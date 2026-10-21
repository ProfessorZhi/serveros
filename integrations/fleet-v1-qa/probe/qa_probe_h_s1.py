#!/usr/bin/env python3
"""Independent QA Snapshot H (A) — S1 #262 create-time write_authority.

#262 is stacked on #261. It claims: create reports whether the thread can send
(write_authority), and the lease producer is BLOCKED_PRODUCER_BOUNDARY — no
acquire, no fake actor/grant, no lease write.

Uses a REAL Work Engine store (#13 40b0622) in the same state dir the serveros
daemon reads. LOCAL only; synthetic Items/claims; no provider/network/credential.
"""
from __future__ import annotations

import importlib.util
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
load("serveros_context_actor", TREE / "context" / "actor.py")


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

ITEMS = ("SRV-LIVE", "SRV-NOLEASE", "SRV-T2", "SRV-FORGED")
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
        "device_ref": "device:qa-h", "resources": []}),
    state_dir=daemon_state, socket_path=SCRATCH / "run/serveros.sock",
    log=lambda message: None)


def lease_rows():
    return we.connection().execute(
        "SELECT resource, owner, token, expires_at FROM leases ORDER BY resource"
    ).fetchall()


def sends(binding, write_authority):
    try:
        return coordination.admit_work(
            binding_record=binding, work_item=None, item_state="ACTIVE",
            current_writer=None, authority=write_authority,
            now=NOW)["state"]
    except coordination.ThreadRefused as exc:
        return exc.reason


# A1 — live lease: create reports the authority and mints from it.
lease = we.acquire_lease("SRV-LIVE", owner="agent:A", ttl_s=600, now=T)
w = rpc("agent.binding.record", {"session_ref": "session:live",
                                 "session_role": "execution",
                                 "bound_item_ref": "SRV-LIVE"})
wa = w.get("write_authority") or {}
check("A1 create reports work-engine source", wa.get("source"), "work-engine:leases")
check("A1 create reports the live lease",
      ((wa.get("lease") or {}).get("owner"), str((wa.get("lease") or {}).get("token"))),
      ("agent:A", "1"))
check("A1 binding claim is minted from the lease",
      (((w.get("binding") or {}).get("writer") or {}).get("owner"),
       str(((w.get("binding") or {}).get("writer") or {}).get("token"))),
      ("agent:A", "1"))

# A2 — no lease: create says so; it must not create or mutate a lease.
before = [(r["resource"], r["owner"], r["token"], r["expires_at"]) for r in lease_rows()]
w2 = rpc("agent.binding.record", {"session_ref": "session:nolease",
                                  "session_role": "execution",
                                  "bound_item_ref": "SRV-NOLEASE"})
wa2 = w2.get("write_authority") or {}
check("A2 no-lease create is recorded (no claim)",
      (w2.get("recorded"), (w2.get("binding") or {}).get("writer")), (True, None))
check("A2 create reports lease=None", wa2.get("lease"), None)
after = [(r["resource"], r["owner"], r["token"], r["expires_at"]) for r in lease_rows()]
check("A2 create did not acquire or mutate a lease", after, before, security=True)
check("A2 no grant was fabricated",
      len(daemon.control_store().grants()) if hasattr(daemon.control_store(), "grants") else 0, 0)

# A3 — no lease: the actual send gate rejects.
read2 = rpc("agent.binding.resolve", {"session_ref": "session:nolease"})
check("A3 send with no lease is refused",
      sends(read2.get("binding"), read2.get("write_authority")),
      "NOT_CURRENT_WRITER", security=True)

# A4 — pre-acquired A/1: create→send works.
read1 = rpc("agent.binding.resolve", {"session_ref": "session:live"})
check("A4 pre-acquired owner A/1 is admitted",
      sends(read1.get("binding"), read1.get("write_authority")), "ADMITTED")

# A5 — a caller-supplied writer/epoch is refused.
w5 = rpc("agent.binding.record", {
    "session_ref": "session:spoof", "session_role": "execution",
    "bound_item_ref": "SRV-LIVE",
    "writer": {"owner": "agent:EVIL", "epoch": 999, "token": 999}})
check("A5 forged caller owner/epoch refused", w5.get("recorded"), False,
      security=True)
check("A5 reason is UNTRUSTED_ACTOR", w5.get("reason"), "UNTRUSTED_ACTOR")

# A6 — two competing claimants on one leased Item.
we.acquire_lease("SRV-T2", owner="agent:A", ttl_s=600, now=T)
first = rpc("agent.binding.record", {"session_ref": "session:t2a",
                                     "session_role": "execution",
                                     "bound_item_ref": "SRV-T2"})
second = rpc("agent.binding.record", {"session_ref": "session:t2b",
                                      "session_role": "execution",
                                      "bound_item_ref": "SRV-T2"})
check("A6 first claimant holds the lease",
      ((first.get("binding") or {}).get("writer") or {}).get("owner"), "agent:A")
check("A6 second claimant has no claim",
      (second.get("binding") or {}).get("writer"), None)
read_t2b = rpc("agent.binding.resolve", {"session_ref": "session:t2b"})
check("A6 second claimant send is refused",
      sends(read_t2b.get("binding"), read_t2b.get("write_authority")),
      "NOT_CURRENT_WRITER", security=True)

# A7 — a fabricated claim written behind the boundary still loses to the lease.
we.acquire_lease("SRV-FORGED", owner="agent:A", ttl_s=600, now=T)
fabricated = coordination.binding(
    session_role="execution", bound_item_ref="SRV-FORGED",
    writer={"owner": "agent:B", "epoch": 2, "token": "2"})
fabricated["session_ref"] = "session:forged"
daemon.control_store().put_binding(fabricated)
readf = rpc("agent.binding.resolve", {"session_ref": "session:forged"})
check("A7 fabricated claim loses to the live lease",
      sends(readf.get("binding"), readf.get("write_authority")),
      "NOT_CURRENT_WRITER", security=True)

# A8 — SOURCE: no producer boundary, no lease writes, no actor/grant authority.
serveros_py = [p for p in TREE.rglob("*.py") if "/tests/" not in p.as_posix()]
check("A8 serveros has no acquire_lease",
      any("acquire_lease(" in p.read_text(encoding="utf-8") for p in serveros_py),
      False)
check("A8 serveros has no direct lease write",
      any(any(token in p.read_text(encoding="utf-8") for token in
              ("INSERT INTO leases", "UPDATE leases", "DELETE FROM leases"))
          for p in serveros_py), False)
we_cli = (WE / "workengine/cli.py").read_text(encoding="utf-8")
we_mcp = (WE / "workengine/mcp.py").read_text(encoding="utf-8")
we_schema = (WE / "workengine/schema.sql").read_text(encoding="utf-8")
check("A8 WE CLI has no lease producer", "acquire_lease" in we_cli, False)
check("A8 WE MCP has no lease producer", "acquire_lease" in we_mcp, False)
check("A8 WE has no actor/grant authority table",
      ("CREATE TABLE IF NOT EXISTS grants" in we_schema
       or "CREATE TABLE IF NOT EXISTS actors" in we_schema), False)
contract = (TREE / "fleet/agent-control-contract.yaml").read_text(encoding="utf-8")
check("A8 contract declares BLOCKED_PRODUCER_BOUNDARY",
      "BLOCKED_PRODUCER_BOUNDARY" in contract, True)
check("A8 contract declares create_reports_authority",
      "create_reports_authority" in contract, True)

print()
print("scratch:", SCRATCH)
print("leases:", json.dumps([{ "resource": r["resource"], "owner": r["owner"],
                                "token": r["token"]} for r in lease_rows()],
                             sort_keys=True))
if failures:
    print(f"PROBE FAILED: {len(failures)} check(s): {failures}")
    sys.exit(1)
print("PROBE OK: every S1 #262 check held; classification=BLOCKED_PRODUCER_BOUNDARY")
