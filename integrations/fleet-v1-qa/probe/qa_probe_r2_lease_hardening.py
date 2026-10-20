#!/usr/bin/env python3
"""Q1 independent RED/GREEN for #304 lease hardening (T2/T3).

MODE=red   (#298 e1f783f, unhardened): zero/negative TTL writes an
           already-expired row and returns; a NaN commit instant authorizes;
           coerced tokens match; an empty owner is written; a malformed row
           raises an untyped ValueError.
MODE=green (#304 776cd98): each is a typed refusal and writes nothing.

Run: QA_TREE=<tree> QA_MODE=red|green python3 qa_probe_r2_lease_hardening.py
"""
from __future__ import annotations

import json
import math
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(os.environ["QA_TREE"]).resolve()
MODE = os.environ.get("QA_MODE", "green").strip().lower()
sys.path.insert(0, str(REPO))

from workengine.store import LeaseError, ServerOSStore  # noqa: E402

RESOURCE = "work-write:SRV-Q1"
RESOURCE2 = "work-write:SRV-Q2"
RESOURCE3 = "work-write:SRV-Q3"
results = []


def check(name, condition, detail=""):
    results.append((name, bool(condition), detail))
    print(f"{'PASS' if condition else 'FAIL'} {name}"
          + (f" :: {detail}" if detail else ""))


def reason(exc):
    return getattr(exc, "reason_code", type(exc).__name__)


def drop(store, resource):
    with store.transaction() as conn:
        conn.execute("DELETE FROM leases WHERE resource=?", (resource,))


with tempfile.TemporaryDirectory() as td:
    store = ServerOSStore(Path(td) / "state")

    # 1. Zero / negative / non-finite / unbounded / mistyped TTL.
    for ttl, old_returns in (
            (0, True), (-1, True), (float("nan"), False),
            (float("inf"), False), (10 ** 12, False), ("30", True),
            (True, True)):
        try:
            got = store.acquire_lease(RESOURCE, owner="agent:A", ttl_s=ttl,
                                      now=1000.0)
            outcome = f"RETURNED:{got.get('expires_at')}"
        except LeaseError as exc:
            outcome = reason(exc)
        except Exception as exc:  # noqa: BLE001
            outcome = f"RAW:{type(exc).__name__}"
        if MODE == "red":
            check(f"RED ttl={ttl!r} is not a typed refusal",
                  outcome.startswith("RETURNED") if old_returns
                  else outcome.startswith("RAW"), outcome)
        else:
            check(f"GREEN ttl={ttl!r} is LEASE_TTL_INVALID",
                  outcome == "LEASE_TTL_INVALID", outcome)
        drop(store, RESOURCE)

    # 2. A NaN clock cannot take over a live lease and is a typed refusal.
    a_lease = store.acquire_lease(RESOURCE, owner="agent:A", ttl_s=30,
                                  now=2000.0)
    a_token = a_lease["token"]
    try:
        got = store.acquire_lease(RESOURCE, owner="agent:B", ttl_s=30,
                                  now=float("nan"))
        nan_takeover = got.get("owner") == "agent:B"
        nan_outcome = f"RETURNED:{got.get('owner')}"
    except Exception as exc:  # noqa: BLE001
        nan_takeover = False
        nan_outcome = reason(exc)
    live = store.active_lease(RESOURCE, now=2001.0) or {}
    if MODE == "red":
        check("RED NaN clock is not a typed refusal",
              nan_outcome != "LEASE_CLOCK_INVALID", nan_outcome)
    else:
        check("GREEN NaN clock is LEASE_CLOCK_INVALID",
              nan_outcome == "LEASE_CLOCK_INVALID", nan_outcome)
        check("GREEN NaN clock leaves A as the holder",
              live.get("owner") == "agent:A", repr(live))

    # 3. A NaN commit instant must not authorize.
    try:
        verdict = store.check_writer(RESOURCE, owner="agent:A", token=a_token,
                                     now=float("nan"))
        nan_check = verdict.get("authorized") is True
        nan_check_outcome = f"RETURNED:{verdict}"
    except Exception as exc:  # noqa: BLE001
        nan_check = False
        nan_check_outcome = reason(exc)
    if MODE == "red":
        check("RED NaN commit instant authorizes", nan_check,
              nan_check_outcome)
    else:
        check("GREEN NaN commit instant is LEASE_CLOCK_INVALID",
              nan_check_outcome == "LEASE_CLOCK_INVALID", nan_check_outcome)

    # 4. Coerced tokens on a freshly minted token (token == 1).
    fresh = store.acquire_lease(RESOURCE2, owner="agent:A", ttl_s=30,
                                now=2000.0)
    token1 = fresh["token"]
    if token1 != 1:
        print(f"NOTE fresh token is {token1}, not 1; coercion still tested")
    for label, value in (("string", str(token1)), ("float", float(token1)),
                         ("bool", True)):
        try:
            out = store.check_writer(RESOURCE2, owner="agent:A", token=value,
                                     now=2005.0)
            accepted = out.get("authorized") is True
            outcome = f"RETURNED:{out}"
        except Exception as exc:  # noqa: BLE001
            accepted = False
            outcome = reason(exc)
        if MODE == "red":
            check(f"RED {label} token is accepted as the holder", accepted,
                  outcome)
        else:
            check(f"GREEN {label} token is refused", not accepted, outcome)

    # 5. An empty owner is refused at acquire, not written.
    try:
        got = store.acquire_lease(RESOURCE3, owner="", ttl_s=30, now=2000.0)
        empty_ok = got.get("owner") == ""
        empty_outcome = f"RETURNED:{got}"
    except Exception as exc:  # noqa: BLE001
        empty_ok = False
        empty_outcome = reason(exc)
    if MODE == "red":
        check("RED empty owner is written", empty_ok, empty_outcome)
    else:
        check("GREEN empty owner is LEASE_OWNER_REQUIRED",
              empty_outcome == "LEASE_OWNER_REQUIRED", empty_outcome)

    # 6. A malformed expiry row cannot be aged into "expired".
    bad = "work-write:SRV-BAD"
    with store.transaction() as conn:
        conn.execute(
            "INSERT INTO leases(resource,owner,token,expires_at) "
            "VALUES(?,?,?,?)", (bad, "agent:A", "1", "not-a-time"))
    try:
        out = store.acquire_lease(bad, owner="agent:B", ttl_s=30, now=3000.0)
        bad_outcome = f"RETURNED:{out.get('expires_at')}"
    except Exception as exc:  # noqa: BLE001
        bad_outcome = reason(exc)
    if MODE == "red":
        check("RED malformed row is raw or taken over",
              bad_outcome != "LEASE_ROW_INVALID", bad_outcome)
    else:
        check("GREEN malformed row is LEASE_ROW_INVALID",
              bad_outcome == "LEASE_ROW_INVALID", bad_outcome)

failed = [n for n, ok, _ in results if not ok]
print(json.dumps({"mode": MODE, "checks": len(results), "failed": failed},
                 ensure_ascii=False))
sys.exit(1 if failed else 0)
