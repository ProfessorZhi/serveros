#!/usr/bin/env python3
"""Independent QA probe, second snapshot (2026-10-17 late).

S2 = serveros #252 head f2b89e1 (previous snapshot dac3088)
G1 = serveros #255 head 619cf63 (UNCHANGED; B3/B4 stay against this SHA only)

Read-only: reads git blobs from QA_REPO, writes nothing. Exit 0 only when every
reproduction holds. Each check states whether the prior defect is still present
or has been closed at this exact SHA.
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
S2 = "f2b89e1275a0fdc0b460be0f8c1b82369c75686f"
G1 = "619cf637cbdba3020f989cee3d9f72b7f2b25ce5"

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
# S2 @ f2b89e1
# ---------------------------------------------------------------------------
da = load("qa_dispatch_attempts_f2b", blob(S2, "runtime/dispatch_attempts.py"),
          "/qa/dispatch_attempts.py")

digest = da.intent_digest(
    action="agent.session.send", device_ref="device:d1", verb="session.send",
    params={"instruction": "hi", "turn_idempotency_key": "k"},
    workspace=None, work_item=None)

# Observations now must name a source. That is a real improvement for
# lookup-absence, but `source` is still an unverified caller string.
obs_forged = {"intent_digest": digest, "state": "APPLIED",
              "source": "caller:forged", "result": {"turn": "forged"}}

router_shaped = da.attempt_record(
    key="k", action="agent.session.send", device_ref="device:d1",
    verb="session.send", intent_digest=digest, phase=da.EFFECT_UNKNOWN,
    work_item=None, at="2026-10-17T00:00:00Z")
check("S0 router-shaped record still carries no principal",
      "principal" in router_shaped, False)

v1 = da.reconcile_observation(
    router_shaped, observation=obs_forged,
    reconciler={"principal": "agent:INTRUDER"}, current_writer=None)
check("S1 cross-principal + forged source still settles", v1["verdict"],
      da.SETTLE_APPLIED)

rec_principal = da.attempt_record(
    key="k2", action="agent.session.send", device_ref="device:d1",
    verb="session.send", intent_digest=digest, phase=da.EFFECT_UNKNOWN,
    work_item="SRV-0001", principal="agent:A")
v2 = da.reconcile_observation(
    rec_principal, observation=obs_forged,
    reconciler={"principal": "agent:A"}, current_writer={})
check("S2 empty current_writer still matches empty reconciler", v2["verdict"],
      da.SETTLE_APPLIED)

late = dict(rec_principal, phase=da.SETTLED)
writer = {"owner": "agent:A", "epoch": 1, "token": "1"}
v3 = da.reconcile_observation(
    late, observation=obs_forged,
    reconciler={**writer, "principal": "agent:A"}, current_writer=writer)
check("S3 late reply after SETTLED still refused", v3["verdict"],
      da.REFUSE_NOT_UNKNOWN)

sig = inspect.signature(da.intent_digest)
omitted = [name for name in ("session_ref", "agent_runtime", "runtime_id")
           if name not in sig.parameters]
check("S4 intent_digest still omits target/runtime", omitted,
      ["session_ref", "agent_runtime", "runtime_id"])
router_src = blob(S2, "fleet/agent_control_router.py")
body = router_src.split("def _intent_digest")[1].split("def _attempt_record")[0]
mentions = [name for name in ("session_ref", "agent_runtime", "runtime_id")
            if name in body]
check("S5 router _intent_digest still passes no target/runtime", mentions, [])

daemon_src = blob(S2, "runtime/daemon.py")
rec_body = daemon_src.split("def _r_agent_dispatch_record")[1].split(
    "def _r_agent_binding_list")[0]
check("S6 daemon record still takes caller principal",
      'principal=incoming.get("principal")' in rec_body, True)
check("S6 daemon record still takes caller phase",
      'phase=incoming.get("phase")' in rec_body, True)
check("S6 daemon record still never reconciles",
      "reconcile_observation" in rec_body, False)

# The f2b fix itself: lookup-absence no longer proves NOT_APPLIED.
def verdict(state, *, source="provider:readback"):
    obs = {"intent_digest": digest, "state": state}
    if source is not None:
        obs["source"] = source
    return da.reconcile_observation(
        rec_principal, observation=obs,
        reconciler={**writer, "principal": "agent:A"},
        current_writer=writer)["verdict"]

check("S7a NOT_FOUND is no longer NOT_APPLIED", verdict("NOT_FOUND"),
      da.REFUSE_UNKNOWN)
check("S7b MISSING is no longer NOT_APPLIED", verdict("MISSING"),
      da.REFUSE_UNKNOWN)
check("S7c ABSENT is no longer NOT_APPLIED", verdict("ABSENT"),
      da.REFUSE_UNKNOWN)
check("S7d NOT_APPLIED without source is refused",
      verdict("NOT_APPLIED", source=None), da.REFUSE_UNKNOWN)
check("S7e NOT_APPLIED with a named source still settles",
      verdict("NOT_APPLIED"), da.SETTLE_NOT_APPLIED)

# ---------------------------------------------------------------------------
# G1 @ 619cf63 (unchanged; B3/B4 are the same checks as the first snapshot)
# ---------------------------------------------------------------------------
g1 = load("qa_effective", blob(G1, "integrations/device-ability-projection/effective.py"),
          "/qa/integrations/device-ability-projection/effective.py")
full_absent = frozenset({"device_capabilities", "profile", "instances",
                         "runtime_record", "runtime_binding"})
ok = {"status": "AVAILABLE", "reason": None, "provider": "provider:alpha"}
words_absent = g1.terms(full_absent, ok)
check("B1 absent health/policy -> UNKNOWN",
      (words_absent["ready"], words_absent["grant"]), ("UNKNOWN", "UNKNOWN"))
check("B2 absent health/policy -> effective is None",
      g1.effective(ok, words_absent)[0], None)
words_null = g1.terms(full_absent | {"health", "policy"}, ok)
check("B3 null health/policy -> READY/ALLOWED",
      (words_null["ready"], words_null["grant"]), ("READY", "ALLOWED"))
check("B4 null health/policy -> effective becomes a provider id",
      g1.effective(ok, words_null)[0], "provider:alpha")

print()
if failures:
    print(f"PROBE FAILED: {len(failures)} check(s): {failures}")
    sys.exit(1)
print("PROBE OK: every reproduction held")
