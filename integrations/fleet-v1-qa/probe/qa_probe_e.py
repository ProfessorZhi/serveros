#!/usr/bin/env python3
"""Independent QA Snapshot E — cross-repo S1 pair.

serveros #241 d0754ed (reader/admission) + work-engine #13 40b0622 (lease writer).
Both trees are read-only archives; this probe writes only into QA_SCRATCH.

Question under test: does the Session-send admission (LocalLoopbackTransport
`_admit` -> coordination.admit_work) read the Work Engine lease authority, or a
caller-provided binding claim? The probe builds the REAL serveros daemon, writes
a binding with an explicit caller-supplied writer, and compares the admission
decision with the LIVE Work Engine lease on the same resource.

LOCAL only: no provider, network, credential or production call. Synthetic
actors/claims are not REAL_DEVICE evidence.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

S1 = Path(os.environ["S1_TREE"]).resolve()
WE = Path(os.environ["WE_TREE"]).resolve()
SCRATCH = Path(os.environ["QA_SCRATCH"]).resolve()
RUNTIME = S1 / "runtime"

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
resolver = load("serveros_context_resolver", S1 / "context" / "resolver.py")
store_mod = load("serveros_runtime_agent_control_store", RUNTIME / "agent_control_store.py")
daemon_mod = load("serveros_runtime_daemon", RUNTIME / "daemon.py")
registry_mod = load("serveros_runtime_registry", RUNTIME / "registry.py")
load("serveros_context_actor", S1 / "context" / "actor.py")


def check(label: str, got, expected, *, security=False) -> None:
    ok = got == expected
    tag = "PASS" if ok else ("SECURITY_FAIL" if security else "FAIL")
    print(f"{tag} {label}: got={got!r} expected={expected!r}")
    if not ok:
        failures.append(label)


def stamp(epoch: float) -> str:
    import datetime
    return datetime.datetime.fromtimestamp(
        epoch, datetime.timezone.utc).strftime(coordination.WRITER_STAMP)


# ---------------------------------------------------------------------------
# The two stores, in one scratch area.
# ---------------------------------------------------------------------------
SCRATCH.mkdir(parents=True, exist_ok=True)
we_state = SCRATCH / "we-state"
serveros_state = SCRATCH / "serveros-state"
serveros_state.mkdir(parents=True, exist_ok=True)
we = ServerOSStore(we_state)
T = time.time()

# An items_index so the daemon's binding.resolve can answer an Item state.
conn = sqlite3.connect(serveros_state / "serveros.db")
conn.execute("CREATE TABLE IF NOT EXISTS items_index "
             "(item_id TEXT, state TEXT, outcome TEXT)")
conn.execute("INSERT OR REPLACE INTO items_index VALUES ('SRV-0001','ACTIVE',NULL)")
conn.execute("INSERT OR REPLACE INTO items_index VALUES ('SRV-0003','ACTIVE',NULL)")
conn.execute("INSERT OR REPLACE INTO items_index VALUES ('SRV-0004','ACTIVE',NULL)")
conn.commit()
conn.close()

daemon = daemon_mod.Daemon(
    registry_mod.Registry(document={
        "schema": "serveros/managed-runtime-registry.v1",
        "device_ref": "device:qa-e", "resources": []}),
    state_dir=serveros_state, socket_path=SCRATCH / "run/serveros.sock",
    log=lambda message: None)


def rpc(method: str, params: dict) -> dict:
    return daemon.handle({"method": method, "params": params})["result"]


# ---------------------------------------------------------------------------
# E1 — the Work Engine authority is real and exclusive.
# ---------------------------------------------------------------------------
lease = we.acquire_lease("SRV-0001", owner="agent:A", ttl_s=600, now=T)
check("E1 WE lease minted", (lease["owner"], str(lease["token"])),
      ("agent:A", "1"))
live = we.active_lease("SRV-0001", now=T + 1)
check("E1 WE reports A as the live writer", (live["owner"], str(live["token"])),
      ("agent:A", "1"))
try:
    we.acquire_lease("SRV-0001", owner="agent:B", ttl_s=60, now=T + 2)
    second = "ACQUIRED"
except LeaseError as exc:
    second = exc.reason_code
check("E1 a second WE writer is refused", second, "LEASE_HELD_BY_OTHER")

# E2 — serveros CAN read that authority from the shared table.
roots = resolver.Roots(workspace_root=SCRATCH / "ws",
                       artifacts_root=SCRATCH / "art", state_db=we.db_path)
check("E2 serveros sees the WE lease in the shared table",
      "SRV-0001" in resolver.held_leases(roots, now=T + 1), True)

# E3 — an expired WE row is excluded by the reader but still on disk.
exp = we.acquire_lease("SRV-0002", owner="agent:B", ttl_s=10, now=T)
check("E3 expired WE lease is not active",
      we.active_lease("SRV-0002", now=T + 20), None)
check("E3 expired WE lease is not held",
      "SRV-0002" in resolver.held_leases(roots, now=T + 20), False)
with we.connection() as c:
    rows = c.execute("SELECT COUNT(*) FROM leases WHERE resource='SRV-0002'").fetchone()[0]
check("E3 the expired row is still evidence on disk", rows, 1)

# ---------------------------------------------------------------------------
# E4 — the daemon accepts a CALLER-SUPPLIED writer claim, without an actor.
# ---------------------------------------------------------------------------
claim = {"owner": "agent:EVIL", "epoch": 1, "token": 999,
         "expires_at": stamp(T + 600)}
w = rpc("agent.binding.record", {
    "session_ref": "session:evil", "session_role": "execution",
    "bound_item_ref": "SRV-0001", "device_ref": "device:x", "writer": claim})
check("E4 binding with a caller-supplied writer recorded",
      w.get("recorded"), True)
check("E4 the stored claim is the caller's claim",
      ((w.get("binding") or {}).get("writer") or {}).get("owner"), "agent:EVIL")
check("E4 no trusted context was required by the write point",
      "context_ref" in json.dumps(w), False)

read = rpc("agent.binding.resolve", {"session_ref": "session:evil"})
check("E4 resolve names the caller's claim as current writer",
      (read.get("current_writer") or {}).get("owner"), "agent:EVIL")

# E5 — THE QUESTION: admission vs the live WE lease.
admitted = coordination.admit_work(
    binding_record=read.get("binding"), work_item=None,
    item_state=(read.get("item") or {}).get("state"),
    outcome=(read.get("item") or {}).get("outcome"),
    current_writer=read.get("current_writer"), now=stamp(T + 1))
check("E5 admission accepts the caller-claimed writer", admitted["state"], "ADMITTED")
live = we.active_lease("SRV-0001", now=T + 1)
check("E5 admission current writer equals the live WE writer",
      ((read.get("current_writer") or {}).get("owner"),
       str((read.get("current_writer") or {}).get("token"))),
      (live["owner"], str(live["token"])), security=True)

# E6 — T1: a closed source thread takes no work after a handoff.
closed = rpc("agent.binding.close", {"session_ref": "session:evil",
                                     "reason": "handoff"})
check("E6 source binding closed", closed.get("closed"), True)
try:
    coordination.admit_work(
        binding_record=rpc("agent.binding.resolve",
                           {"session_ref": "session:evil"}).get("binding"),
        work_item=None, item_state="ACTIVE", outcome=None,
        current_writer=None, now=stamp(T + 2))
    e6 = "ADMITTED"
except coordination.ThreadRefused as exc:
    e6 = exc.reason
check("E6 closed source is refused", e6, "THREAD_CLOSED_FOR_NEW_WORK")

# E7 — T2: two claims on one Item; the higher token wins, and the old is stale.
for ref, owner, token in (("session:old", "agent:OLD", 1),
                          ("session:new", "agent:NEW", 2)):
    rpc("agent.binding.record", {
        "session_ref": ref, "session_role": "execution",
        "bound_item_ref": "SRV-0003", "writer": {
            "owner": owner, "epoch": 1, "token": token,
            "expires_at": stamp(T + 600)}})
new_read = rpc("agent.binding.resolve", {"session_ref": "session:new"})
old_read = rpc("agent.binding.resolve", {"session_ref": "session:old"})
check("E7 one claim is current", (new_read.get("current_writer") or {}).get("owner"),
      "agent:NEW")
try:
    coordination.admit_work(
        binding_record=old_read.get("binding"), work_item=None,
        item_state="ACTIVE", outcome=None,
        current_writer=old_read.get("current_writer"), now=stamp(T + 2))
    e7 = "ADMITTED"
except coordination.ThreadRefused as exc:
    e7 = exc.reason
check("E7 the stale same-Item claim is refused", e7, "NOT_CURRENT_WRITER")

# E8 — T3: an expired claim is refused, whatever its token.
rpc("agent.binding.record", {
    "session_ref": "session:expired", "session_role": "execution",
    "bound_item_ref": "SRV-0004", "writer": {
        "owner": "agent:EXPIRED", "epoch": 1, "token": 7,
        "expires_at": stamp(T - 60)}})
exp_read = rpc("agent.binding.resolve", {"session_ref": "session:expired"})
try:
    coordination.admit_work(
        binding_record=exp_read.get("binding"), work_item=None,
        item_state="ACTIVE", outcome=None,
        current_writer=exp_read.get("current_writer"), now=stamp(T + 1))
    e8 = "ADMITTED"
except coordination.ThreadRefused as exc:
    e8 = exc.reason
check("E8 expired writer claim is refused", e8, "NOT_CURRENT_WRITER")
check("E8 the axis says EXPIRED",
      coordination.writer_axis(exp_read.get("binding"),
                               current_writer=exp_read.get("current_writer"),
                               now=stamp(T + 1)), "EXPIRED")

# E9 — WE renewal fences by token and by expiry.
try:
    we.renew_lease("SRV-0001", owner="agent:A", token=999, ttl_s=60, now=T + 3)
    e9a = "RENEWED"
except LeaseError as exc:
    e9a = exc.reason_code
check("E9 wrong token cannot renew", e9a, "STALE_TOKEN")
try:
    we.renew_lease("SRV-0001", owner="agent:A", token=lease["token"],
                   ttl_s=60, now=T + 601)
    e9b = "RENEWED"
except LeaseError as exc:
    e9b = exc.reason_code
check("E9 a lapsed lease cannot be renewed", e9b, "LEASE_EXPIRED")

# E10 — T6/revocation: WE deletes on release; there is no revoked column.
we.release_lease("SRV-0002", owner="agent:B", token=exp["token"], now=T + 20)
with we.connection() as c:
    gone = c.execute("SELECT COUNT(*) FROM leases WHERE resource='SRV-0002'").fetchone()[0]
check("E10 release removes the row (no revoked tombstone)", gone, 0)
with we.connection() as c:
    cols = [r[1] for r in c.execute("PRAGMA table_info(leases)").fetchall()]
check("E10 the leases schema has no revocation column", "revoked" in cols, False)

# E11 — the serveros reader is state-dir bound, not a query to another repo.
other = resolver.Roots(workspace_root=SCRATCH / "ws2",
                       artifacts_root=SCRATCH / "art2",
                       state_db=SCRATCH / "other/serveros.db")
check("E11 a different state_db sees no WE lease",
      resolver.held_leases(other, now=T + 1), [])

print()
print("scratch:", SCRATCH)
print("WE lease row:", json.dumps(we.active_lease("SRV-0001", now=T + 1),
                                 sort_keys=True))
print("serveros current_writer:", json.dumps(read.get("current_writer"),
                                             sort_keys=True))
if failures:
    print(f"PROBE FAILED: {len(failures)} check(s): {failures}")
    sys.exit(1)
print("PROBE OK: every reproduction held")
