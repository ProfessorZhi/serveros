#!/usr/bin/env python3
"""Independent QA Snapshot G (A) — S1 #261 live-lease send gate.

Run twice, one tree per process:
  QA_MODE=old QA_TREE=<#241 d0754ed>  -> red control: caller writer EVIL/999 is
                                         ADMITTED while the WE lease is A/1.
  QA_MODE=new QA_TREE=<#261 a6bed15>  -> the same spoof must be refused; the
                                         lease must be the only authority.

Both trees use a REAL Work Engine store (#13 40b0622) in the SAME state dir the
serveros daemon reads, so `live_lease` is a real cross-repo read, not a fixture.

LOCAL only. Synthetic items/claims; no provider, network, credential or login.
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
from workengine.store import LeaseError, ServerOSStore  # noqa: E402


def load(name: str, path: Path):
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


def check(label: str, got, expected, *, security=False) -> None:
    ok = got == expected
    tag = "PASS" if ok else ("SECURITY_FAIL" if security else "FAIL")
    print(f"{tag} [{MODE}] {label}: got={got!r} expected={expected!r}")
    if not ok:
        failures.append(f"[{MODE}] {label}")


def rpc(method: str, params: dict) -> dict:
    return daemon.handle({"method": method, "params": params})["result"]


SCRATCH.mkdir(parents=True, exist_ok=True)
state = SCRATCH / "state"
state.mkdir(parents=True, exist_ok=True)
daemon_state = state / "runtime"
daemon_state.mkdir(parents=True, exist_ok=True)
we = ServerOSStore(state)
T = time.time()


def stamp(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


NOW = stamp(T + 1)

ITEMS = ("SRV-LIVE", "SRV-NOLEASE", "SRV-EXPIRED", "SRV-DELETED",
         "SRV-WRONG", "SRV-T3", "SRV-T2")
conn = sqlite3.connect(state / "serveros.db")
conn.execute("INSERT OR IGNORE INTO projects(project, prefix) VALUES ('qa', 'SRV')")
for item in ITEMS:
    conn.execute(
        "INSERT OR REPLACE INTO items_index"
        "(item_id, project, title, kind, card_locator, state, outcome,"
        " created_at, updated_at) VALUES (?, 'qa', 'qa item', 'test', 'card',"
        " 'ACTIVE', NULL, ?, ?)",
        (item, stamp(T), stamp(T)))
conn.commit()
conn.close()

daemon = daemon_mod.Daemon(
    registry_mod.Registry(document={
        "schema": "serveros/managed-runtime-registry.v1",
        "device_ref": "device:qa-g", "resources": []}),
    state_dir=daemon_state, socket_path=SCRATCH / "run/serveros.sock",
    log=lambda message: None)

# ---------------------------------------------------------------------------
# Old #241 — red control: a caller-chosen claim is the authority.
# ---------------------------------------------------------------------------
if MODE == "old":
    lease = we.acquire_lease("SRV-LIVE", owner="agent:A", ttl_s=600, now=T)
    check("old WE lease is A/1", (lease["owner"], str(lease["token"])), ("agent:A", "1"))
    w = rpc("agent.binding.record", {
        "session_ref": "session:evil", "session_role": "execution",
        "bound_item_ref": "SRV-LIVE",
        "writer": {"owner": "agent:EVIL", "epoch": 1, "token": 999,
                   "expires_at": "2099-12-31T00:00:00Z"}})
    check("old explicit writer recorded", w.get("recorded"), True)
    read = rpc("agent.binding.resolve", {"session_ref": "session:evil"})
    admitted = coordination.admit_work(
        binding_record=read.get("binding"), work_item=None,
        item_state=(read.get("item") or {}).get("state"),
        outcome=(read.get("item") or {}).get("outcome"),
        current_writer=read.get("current_writer"), now=NOW)
    check("old spoof is refused despite the live lease",
          admitted["state"], "REFUSED", security=True)
    live = we.active_lease("SRV-LIVE", now=T + 1)
    check("old admission does not match the lease",
          ((read.get("current_writer") or {}).get("owner"),
           str((read.get("current_writer") or {}).get("token"))),
          (live["owner"], str(live["token"])), security=True)
    print("scratch:", SCRATCH)
    sys.exit(1 if failures else 0)

# ---------------------------------------------------------------------------
# New #261 — the lease is the only authority.
# ---------------------------------------------------------------------------
has_authority = "authority" in inspect.signature(coordination.admit_work).parameters
check("new admit_work accepts authority", has_authority, True)
check("new daemon.exposes live lease reader",
      hasattr(resolver, "live_lease"), True)
check("new serveros has no acquire_lease (production create does not acquire)",
      any("acquire_lease(" in p.read_text(encoding="utf-8")
          for p in TREE.rglob("*.py")
          if "/tests/" not in p.as_posix()),
      False)

lease = we.acquire_lease("SRV-LIVE", owner="agent:A", ttl_s=600, now=T)

# G1 positive: a new execution binding is minted FROM the lease.
w = rpc("agent.binding.record", {"session_ref": "session:p1",
                                 "session_role": "execution",
                                 "bound_item_ref": "SRV-LIVE"})
check("G1 binding recorded", w.get("recorded"), True)
claim = (w.get("binding") or {}).get("writer") or {}
check("G1 claim is minted from the lease",
      (claim.get("owner"), str(claim.get("token"))), ("agent:A", "1"))
read = rpc("agent.binding.resolve", {"session_ref": "session:p1"})
check("G1 resolve reports the lease as the authority",
      ((read.get("write_authority") or {}).get("lease") or {}).get("owner"),
      "agent:A")
check("G1 legitimate lease holder is ADMITTED",
      coordination.admit_work(
          binding_record=read.get("binding"), work_item=None,
          item_state=(read.get("item") or {}).get("state"),
          outcome=(read.get("item") or {}).get("outcome"),
          current_writer=read.get("current_writer"),
          authority=read.get("write_authority"),
          now=NOW)["state"], "ADMITTED")

# G2 spoof explicit writer is refused at the daemon write point.
w = rpc("agent.binding.record", {
    "session_ref": "session:spoof", "session_role": "execution",
    "bound_item_ref": "SRV-LIVE",
    "writer": {"owner": "agent:EVIL", "epoch": 1, "token": 999}})
check("G2 explicit caller writer is refused", w.get("recorded"), False,
      security=True)
check("G2 reason is UNTRUSTED_ACTOR", w.get("reason"), "UNTRUSTED_ACTOR")

# G3 no lease -> no claim -> refusal.
w = rpc("agent.binding.record", {"session_ref": "session:nolease",
                                 "session_role": "execution",
                                 "bound_item_ref": "SRV-NOLEASE"})
check("G3 binding without a lease recorded (no claim)",
      (w.get("recorded"), (w.get("binding") or {}).get("writer")), (True, None))
read = rpc("agent.binding.resolve", {"session_ref": "session:nolease"})
check("G3 no lease reported", (read.get("write_authority") or {}).get("lease"), None)
try:
    coordination.admit_work(
        binding_record=read.get("binding"), work_item=None, item_state="ACTIVE",
        current_writer=read.get("current_writer"),
        authority=read.get("write_authority"), now=NOW)
    g3 = "ADMITTED"
except coordination.ThreadRefused as exc:
    g3 = exc.reason
check("G3 send with no lease is refused", g3, "NOT_CURRENT_WRITER", security=True)

# G4 an expired row is not a lease (row stays as evidence).
we.acquire_lease("SRV-EXPIRED", owner="agent:A", ttl_s=10, now=T - 100)
check("G4 live_lease excludes the expired row",
      resolver.live_lease(resolver.Roots(workspace_root=SCRATCH / "ws",
                                         artifacts_root=SCRATCH / "art",
                                         state_db=we.db_path),
                          "SRV-EXPIRED"), None)
with we.connection() as c:
    rows = c.execute("SELECT COUNT(*) FROM leases WHERE resource='SRV-EXPIRED'").fetchone()[0]
check("G4 the expired row is still on disk", rows, 1)

# G5 a deleted (released) lease is not a lease.
rel = we.acquire_lease("SRV-DELETED", owner="agent:A", ttl_s=600, now=T)
we.release_lease("SRV-DELETED", owner="agent:A", token=rel["token"], now=T)
check("G5 released lease is absent",
      we.active_lease("SRV-DELETED", now=T + 1), None)
w = rpc("agent.binding.record", {"session_ref": "session:deleted",
                                 "session_role": "execution",
                                 "bound_item_ref": "SRV-DELETED"})
check("G5 released scope mints no claim",
      (w.get("recorded"), (w.get("binding") or {}).get("writer")), (True, None))

# G6 wrong owner/token claim against a live lease is refused.
we.acquire_lease("SRV-WRONG", owner="agent:A", ttl_s=600, now=T)
fabricated = coordination.binding(
    session_role="execution", bound_item_ref="SRV-WRONG",
    writer={"owner": "agent:B", "epoch": 1, "token": "2"})
fabricated["session_ref"] = "session:wrong"
daemon.control_store().put_binding(fabricated)
read = rpc("agent.binding.resolve", {"session_ref": "session:wrong"})
try:
    coordination.admit_work(
        binding_record=read.get("binding"), work_item=None, item_state="ACTIVE",
        current_writer=read.get("current_writer"),
        authority=read.get("write_authority"), now=NOW)
    g6 = "ADMITTED"
except coordination.ThreadRefused as exc:
    g6 = exc.reason
check("G6 wrong owner/token claim is refused", g6, "NOT_CURRENT_WRITER",
      security=True)

# G7 T1: a closed source thread takes no work, even with a live lease.
closed = rpc("agent.binding.close", {"session_ref": "session:p1",
                                     "reason": "handoff"})
check("G7 source closed", closed.get("closed"), True)
try:
    coordination.admit_work(
        binding_record=rpc("agent.binding.resolve",
                           {"session_ref": "session:p1"}).get("binding"),
        work_item=None, item_state="ACTIVE", current_writer=None,
        authority=rpc("agent.binding.resolve",
                      {"session_ref": "session:p1"}).get("write_authority"),
        now=NOW)
    g7 = "ADMITTED"
except coordination.ThreadRefused as exc:
    g7 = exc.reason
check("G7 closed source is refused", g7, "THREAD_CLOSED_FOR_NEW_WORK")

# G8 T2: two live bindings on one leased Item; only the lease holder is current.
we.acquire_lease("SRV-T2", owner="agent:A", ttl_s=600, now=T)
first = rpc("agent.binding.record", {"session_ref": "session:t2a",
                                     "session_role": "execution",
                                     "bound_item_ref": "SRV-T2"})
second = rpc("agent.binding.record", {"session_ref": "session:t2b",
                                      "session_role": "execution",
                                      "bound_item_ref": "SRV-T2"})
check("G8 first claims the lease", (first.get("binding") or {}).get("writer") is not None, True)
check("G8 second becomes a claimless thread",
      (second.get("binding") or {}).get("writer"), None)
try:
    we.acquire_lease("SRV-T2", owner="agent:B", ttl_s=60, now=T + 1)
    g8 = "ACQUIRED"
except LeaseError as exc:
    g8 = exc.reason_code
check("G8 WE refuses a second live writer", g8, "LEASE_HELD_BY_OTHER")

# G9 T3: the token moves; the old claim is stale.
we.acquire_lease("SRV-T3", owner="agent:A", ttl_s=600, now=T)
rpc("agent.binding.record", {"session_ref": "session:t3",
                             "session_role": "execution",
                             "bound_item_ref": "SRV-T3"})
we.release_lease("SRV-T3", owner="agent:A",
                 token=we.active_lease("SRV-T3", now=T)["token"], now=T)
moved = we.acquire_lease("SRV-T3", owner="agent:B", ttl_s=600, now=T + 1)
read = rpc("agent.binding.resolve", {"session_ref": "session:t3"})
try:
    coordination.admit_work(
        binding_record=read.get("binding"), work_item=None, item_state="ACTIVE",
        current_writer=read.get("current_writer"),
        authority=read.get("write_authority"), now=NOW)
    g9 = "ADMITTED"
except coordination.ThreadRefused as exc:
    g9 = exc.reason
check("G9 old token claim is refused after the lease moves", g9,
      "NOT_CURRENT_WRITER", security=True)
check("G9 the lease's token advanced", str(moved["token"]), "2")

print()
print("scratch:", SCRATCH)
print("leases:", json.dumps(
    {k: {"owner": v["owner"], "token": v["token"]}
     for k, v in ((r["resource"], r) for r in
                  we.connection().execute(
                      "SELECT resource, owner, token, expires_at FROM leases"))},
    sort_keys=True))
if failures:
    print(f"PROBE FAILED: {len(failures)} check(s): {failures}")
    sys.exit(1)
print("PROBE OK: every check held")
