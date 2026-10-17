#!/usr/bin/env python3
"""Independent QA probe at exact SHAs (reproduction copy).

Read-only: reads git blobs from QA_REPO, writes nothing. Usage:

    QA_REPO=/path/to/serveros-worktree python3 qa_probe.py

S252 = serveros #252 head dac3088 (observation-to-SETTLED trust decision)
S255 = serveros #255 head 619cf63 (per-device EffectiveAbility projection)

Exit 0 only when every reproduction holds. See QA-TRIAGE-2026-10-17.md §3.2.
"""
from __future__ import annotations

import inspect
import os
import subprocess
import sys
import types

WT = os.environ.get("QA_REPO", "").strip()
if not WT:
    raise SystemExit("QA_REPO must name a checkout of ProfessorZhi/serveros")
S252 = "dac308801b16db18dd01c5e6e316fd3fd546178b"
S255 = "619cf637cbdba3020f989cee3d9f72b7f2b25ce5"

failures: list[str] = []


def blob(sha: str, path: str) -> str:
    return subprocess.check_output(
        ["git", "-C", WT, "show", f"{sha}:{path}"], text=True)


def load(name: str, src: str, filename: str) -> types.ModuleType:
    mod = types.ModuleType(name)
    mod.__file__ = filename
    exec(compile(src, filename, "exec"), mod.__dict__)
    return mod


def check(label: str, got, expected) -> None:
    ok = got == expected
    print(f"{'PASS' if ok else 'FAIL'} {label}: got={got!r} expected={expected!r}")
    if not ok:
        failures.append(label)


# ---------------------------------------------------------------------------
# A. #252 — the observation-to-SETTLED decision
# ---------------------------------------------------------------------------
da = load("qa_dispatch_attempts", blob(S252, "runtime/dispatch_attempts.py"),
          "/qa/dispatch_attempts.py")

digest = da.intent_digest(
    action="agent.session.send", device_ref="device:d1", verb="session.send",
    params={"instruction": "hi", "turn_idempotency_key": "k"},
    workspace=None, work_item=None)
observation = {"intent_digest": digest, "state": "APPLIED",
               "result": {"turn": "forged-or-other"}}

router_shaped = da.attempt_record(
    key="k", action="agent.session.send", device_ref="device:d1",
    verb="session.send", intent_digest=digest, phase=da.EFFECT_UNKNOWN,
    work_item=None, at="2026-10-17T00:00:00Z")
check("A0 router-shaped record carries no principal", "principal" in router_shaped,
      False)

v1 = da.reconcile_observation(
    router_shaped, observation=observation,
    reconciler={"principal": "agent:INTRUDER"}, current_writer=None)
check("A1 cross-principal settles an unowned router record", v1["verdict"],
      da.SETTLE_APPLIED)

rec_principal = da.attempt_record(
    key="k2", action="agent.session.send", device_ref="device:d1",
    verb="session.send", intent_digest=digest, phase=da.EFFECT_UNKNOWN,
    work_item="SRV-0001", principal="agent:A")
v2 = da.reconcile_observation(
    rec_principal, observation=observation,
    reconciler={"principal": "agent:A"}, current_writer={})
check("A2 empty current_writer matches an empty reconciler", v2["verdict"],
      da.SETTLE_APPLIED)

late = dict(rec_principal, phase=da.SETTLED)
writer = {"owner": "agent:A", "epoch": 1, "token": "1"}
v3 = da.reconcile_observation(
    late, observation=observation,
    reconciler={**writer, "principal": "agent:A"}, current_writer=writer)
check("A3 late reply after SETTLED is refused", v3["verdict"],
      da.REFUSE_NOT_UNKNOWN)

sig = inspect.signature(da.intent_digest)
omitted = [name for name in ("session_ref", "agent_runtime", "runtime_id")
           if name not in sig.parameters]
check("A4 intent_digest omits target/runtime", omitted,
      ["session_ref", "agent_runtime", "runtime_id"])
router_src = blob(S252, "fleet/agent_control_router.py")
body = router_src.split("def _intent_digest")[1].split("def _attempt_record")[0]
mentions = [name for name in ("session_ref", "agent_runtime", "runtime_id")
            if name in body]
check("A5 router _intent_digest passes no target/runtime", mentions, [])

daemon_src = blob(S252, "runtime/daemon.py")
rec_body = daemon_src.split("def _r_agent_dispatch_record")[1].split(
    "def _r_agent_binding_list")[0]
check("A6 daemon record takes caller principal",
      'principal=incoming.get("principal")' in rec_body, True)
check("A6 daemon record takes caller phase",
      'phase=incoming.get("phase")' in rec_body, True)
check("A6 daemon record never reconciles", "reconcile_observation" in rec_body,
      False)

# ---------------------------------------------------------------------------
# B. #255 — UNKNOWN invariant when health/grant are missing vs present-but-null
# ---------------------------------------------------------------------------
g1 = load("qa_effective",
          blob(S255, "integrations/device-ability-projection/effective.py"),
          "/qa/integrations/device-ability-projection/effective.py")

full_absent = frozenset({"device_capabilities", "profile", "instances",
                         "runtime_record", "runtime_binding"})
observed_ok = {"status": "AVAILABLE", "reason": None, "provider": "provider:alpha"}

words_absent = g1.terms(full_absent, observed_ok)
check("B1 absent health/policy -> UNKNOWN",
      (words_absent["ready"], words_absent["grant"]), ("UNKNOWN", "UNKNOWN"))
p_absent, _ = g1.effective(observed_ok, words_absent)
check("B2 absent health/policy -> effective is None", p_absent, None)

full_null = full_absent | {"health", "policy"}   # same keys, null values
words_null = g1.terms(full_null, observed_ok)
check("B3 null health/policy -> READY/ALLOWED",
      (words_null["ready"], words_null["grant"]), ("READY", "ALLOWED"))
p_null, _ = g1.effective(observed_ok, words_null)
check("B4 null health/policy -> effective becomes a provider id", p_null,
      "provider:alpha")

print()
if failures:
    print(f"PROBE FAILED: {len(failures)} check(s): {failures}")
    sys.exit(1)
print("PROBE OK: every reproduction held")
