#!/usr/bin/env python3
"""Q1 independent probe for #237 T1/T2/T3 on PR #241 (no product edits).

Checks, against the REAL in-process Daemon over a temp state dir:

  1. a first execution thread mints a writer claim;
  2. a second thread on the owned scope mints none;
  3. the handoff transfer is reproduced by closing the incumbent binding;
  4. AFTER the transfer the target has NO writer claim / NO epoch;
  5. a later-created session can therefore mint the scope and fence the target;
  6. an expired claim is refused;
  7. a corrupt control store fails closed (no admission, no silent empty read).

Run: QA_TREE=<candidate worktree> python3 qa_probe_q1_s1.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(os.environ["QA_TREE"]).resolve()
RUNTIME = REPO / "runtime"


def load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


daemon_mod = load("serveros_runtime_daemon", RUNTIME / "daemon.py")
registry_mod = load("serveros_runtime_registry", RUNTIME / "registry.py")
coordination = load("serveros_runtime_coordination", RUNTIME / "coordination.py")

ITEM = "LIT-0101"
results = []


def check(name, condition, detail=""):
    results.append((name, bool(condition), detail))
    print(f"{'PASS' if condition else 'FAIL'} {name}"
          + (f" :: {detail}" if detail else ""))


def build(tmp: Path):
    registry = registry_mod.Registry(document={
        "schema": "serveros/managed-runtime-registry.v1",
        "device_ref": "device:tokyo-qa",
        "resources": [],
    })
    return daemon_mod.Daemon(
        registry, state_dir=tmp / "state", socket_path=tmp / "run/serveros.sock",
        log=lambda message: None)


def call(daemon, method, **params):
    answer = daemon.handle({"method": method, "params": params})
    if answer.get("ok") is False:
        raise AssertionError(f"{method} -> {answer}")
    return answer["result"]


with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)
    daemon = build(tmp)

    # 1. The first execution thread claims the scope.
    a = call(daemon, "agent.binding.record", session_ref="session:A",
             session_role="execution", bound_item_ref=ITEM)
    a_writer = (a.get("binding") or {}).get("writer")
    check("A mints a writer claim", bool(a_writer), repr(a_writer))

    # 2. A create on an owned scope is not a transfer: B gets no claim.
    b = call(daemon, "agent.binding.record", session_ref="session:B",
             session_role="execution", bound_item_ref=ITEM)
    b_writer = (b.get("binding") or {}).get("writer")
    check("B (second thread) mints no claim", b_writer in (None, {}), repr(b_writer))

    read_a = call(daemon, "agent.binding.resolve", session_ref="session:A")
    read_b = call(daemon, "agent.binding.resolve", session_ref="session:B")
    check("current writer before transfer is A",
          (read_a.get("current_writer") or {}).get("owner") == "session:A",
          repr(read_a.get("current_writer")))

    # 3. Reproduce the handoff transfer exactly as the router does: close the
    #    incumbent's binding once the target is established.
    closed = call(daemon, "agent.binding.close", session_ref="session:A",
                  reason="Q1 probe: handoff moved write authority")
    check("A binding is CLOSED", (closed.get("binding") or {}).get("state") == "CLOSED",
          repr(closed))

    # 4. The target is NOT recorded as the writer of the scope.
    after_b = call(daemon, "agent.binding.resolve", session_ref="session:B")
    target_writer = (after_b.get("binding") or {}).get("writer")
    current_after = after_b.get("current_writer")
    check("target B has NO writer claim after the transfer",
          target_writer in (None, {}), repr(target_writer))
    check("scope current_writer after transfer is None (freed, not granted)",
          current_after is None, repr(current_after))

    # 5. A later-created session mints the freed scope, and the handoff target
    #    is then fenced. This is the T1 gap: B is authorized by "nobody owns
    #    it", not by an active epoch/token.
    c = call(daemon, "agent.binding.record", session_ref="session:C",
             session_role="execution", bound_item_ref=ITEM)
    c_writer = (c.get("binding") or {}).get("writer")
    check("later session C mints the freed scope", bool(c_writer), repr(c_writer))
    after_c = call(daemon, "agent.binding.resolve", session_ref="session:B")
    check("B now sees C as current writer",
          (after_c.get("current_writer") or {}).get("owner") == "session:C",
          repr(after_c.get("current_writer")))

    try:
        coordination.admit_work(
            binding_record=after_c.get("binding"),
            item_state="ACTIVE",
            current_writer=after_c.get("current_writer"),
            now="2099-01-01T00:00:00Z")
        admitted = True
        refusal = ""
    except coordination.ThreadRefused as exc:
        admitted = False
        refusal = exc.reason
    check("handoff target B is fenced by later session C", not admitted,
          f"admitted={admitted} reason={refusal}")

    # 6. An expired claim is refused rather than admitted.
    x = call(daemon, "agent.binding.record", session_ref="session:X",
             session_role="execution", bound_item_ref="LIT-0202",
             writer={"owner": "session:X", "epoch": 1, "token": 1,
                     "expires_at": "2000-01-01T00:00:00Z"})
    read_x = call(daemon, "agent.binding.resolve", session_ref="session:X")
    try:
        coordination.admit_work(
            binding_record=read_x.get("binding"), item_state="ACTIVE",
            current_writer=read_x.get("current_writer"),
            now="2026-10-20T00:00:00Z")
        expired_admitted = True
        expired_reason = ""
    except coordination.ThreadRefused as exc:
        expired_admitted = False
        expired_reason = exc.reason
    check("expired claim is refused", not expired_admitted,
          f"admitted={expired_admitted} reason={expired_reason}")

    # 7. Corrupt control store: a NEW daemon over the same state must fail
    #    closed. It must not answer a resolve as if the thread were unbound and
    #    then admit it (or answer as if the store were empty).
    store_file = tmp / "state" / "agent-control" / "store.json"
    store_file.parent.mkdir(parents=True, exist_ok=True)
    store_file.write_text("{ not json", encoding="utf-8")
    broken = build(tmp)
    try:
        answer = broken.handle({"method": "agent.binding.resolve",
                                "params": {"session_ref": "session:B"}})
        corrupt_fail_closed = answer.get("ok") is False
        corrupt_answer = json.dumps(answer)[:300]
    except Exception as exc:  # noqa: BLE001 - the probe records the type
        corrupt_fail_closed = True
        corrupt_answer = f"RAISED:{type(exc).__name__}"
    # A raised error or an explicit {ok: false} is fail-closed. An {ok: true}
    # answer with a fabricated binding would be fail-open.
    check("corrupt control store is not served as an empty/valid read",
          corrupt_fail_closed, corrupt_answer)

failed = [name for name, ok, _ in results if not ok]
print(json.dumps({"checks": len(results), "failed": failed}, ensure_ascii=False))
sys.exit(1 if failed else 0)
