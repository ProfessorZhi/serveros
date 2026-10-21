#!/usr/bin/env python3
"""Independent QA Snapshot M — WE #15 production-facing finalize entry.

QA_TREE=<WE #15 4bd9e8a> read-only; SERVEROS_TREE=<#271 composed preview> for the
downstream-consumer source check. Drives the REAL `workengine.cli.main` ->
`_cmd_run` -> `ServerOSStore.finalize_run`, using a hermetic SQLite store and a
TEST DOUBLE receipt authority (NOT a production parent).

LOCAL only; no provider/network/credential/login. A T10_VERIFIED result is a
store-side label, not independent QA or a release.
"""
from __future__ import annotations

import json
import os
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

TREE = Path(os.environ["QA_TREE"]).resolve()
SERVEROS = Path(os.environ["SERVEROS_TREE"]).resolve()
SCRATCH = Path(os.environ["QA_SCRATCH"]).resolve()
import sys
sys.path.insert(0, str(TREE))

from workengine import WorkItem  # noqa: E402
from workengine.authority import (  # noqa: E402
    ReceiptError, T10Binding, VerifiedRunReceipt)
import workengine.cli as cli_mod  # noqa: E402
from workengine.model import (Activity, ContextMode, ExecutionContext,  # noqa: E402
                              RunState)
from workengine.store import ServerOSStore  # noqa: E402

ITEM = "DEM-0001"
ACTION = "action:test:1"
ACTOR = "human:hwz"
OTHER_ACTOR = "human:other"
DIGEST = "a" * 64
SHA = "b" * 64
COMMIT = "c" * 40
STALE_COMMIT = "d" * 40
RECEIPT_REF = "trusted:receipt:1"
failures: list[str] = []


def check(label, got, expected, *, security=False):
    ok = got == expected
    tag = "PASS" if ok else ("SECURITY_FAIL" if security else "FAIL")
    print(f"{tag} {label}: got={got!r} expected={expected!r}")
    if not ok:
        failures.append(label)


def receipt(**overrides):
    values = dict(item_ref=ITEM, run_ref="RUN-0001", action_key=ACTION,
                  input_digest=DIGEST, receipt_sha256=SHA,
                  process_exit_code=0, verdict="PASS",
                  runner_identity="runner:test", commit_sha=COMMIT,
                  actor_ref=ACTOR)
    values.update(overrides)
    return VerifiedRunReceipt(**values)


class StubReceiptAuthority:
    """TEST DOUBLE. Not a production RunnerReceiptAuthority."""

    def __init__(self, result=None, *, accept=(RECEIPT_REF,), error=None):
        self.result = result
        self.accept = set(accept)
        self.error = error

    def verify(self, receipt_ref, *, item_ref, run_ref, now):
        if self.error is not None:
            raise self.error
        if receipt_ref not in self.accept:
            raise ValueError("not a receipt this authority issued")
        return self.result


def make_store(root):
    store = ServerOSStore(root / "state")
    store.register_project("demo", "DEM")
    item = store.create_item(WorkItem(ITEM, "demo", "x", "test", "/card"))
    store.add_activity(Activity(item.id, "test", "test"))
    store.attach_context(ExecutionContext(
        item.id, "test", "repo", ContextMode.TEST, "base"))
    run = store.begin_run(item.id, "test", "worker", "key", root / "artifacts")
    for state in (RunState.PREPARING, RunState.RUNNING, RunState.FINALIZING):
        run = store.advance_run(item.id, run.id, state)
    return store, item, run


def binding(run, **over):
    values = dict(item_ref=ITEM, run_ref=run.id, commit_sha=COMMIT,
                  actor_ref=ACTOR)
    values.update(over)
    return T10Binding(**values)


def clamped(store, item, run, *, authority=None, bind=None, receipt_ref=RECEIPT_REF,
            verdict="PASS", exit_code=0):
    return store.finalize_run(
        item.id, run.id, verdict=verdict, exit_code=exit_code, command="pytest",
        cases=["one"], revision="r", dirty=False, t10_required=True,
        t10_binding=bind, receipt_ref=receipt_ref, receipt_authority=authority)


def refused(call, codes):
    try:
        call()
        return "ACCEPTED"
    except ReceiptError as exc:
        return exc.reason_code if exc.reason_code in codes else f"other:{exc.reason_code}"


def evidence_count(store, item, run):
    with store.connection() as c:
        return c.execute("SELECT COUNT(*) FROM evidence WHERE item_id=? AND run_id=?",
                         (item.id, run.id)).fetchone()[0]


def finalize_payload(store, item, run):
    loc = Path(store.get_run(item.id, run.id).artifact_dir) / "finalize.json"
    return json.loads(loc.read_text(encoding="utf-8"))


SCRATCH.mkdir(parents=True, exist_ok=True)
base = Path(tempfile.mkdtemp(dir=SCRATCH, prefix="m-we15-"))

# M1 — the ACTUAL CLI legacy finalize stays SUCCEEDED, but is labelled UNSIGNED.
root1 = base / "m1"
store1, item1, run1 = make_store(root1)
captured = []
old_emit = cli_mod.emit
cli_mod.emit = lambda value, stream=None: captured.append((value, stream))
try:
    code = cli_mod.main(["run", "finalize", item1.id, run1.id, "--verdict", "PASS",
                         "--exit-code", "0", "--command", "pytest", "--revision", "r",
                         "--case", "one", "--state-dir", str(root1 / "state")])
finally:
    cli_mod.emit = old_emit
answer = captured[-1][0]
check("M1 CLI legacy exit", code, 0)
check("M1 legacy state SUCCEEDED", answer.get("state"), "SUCCEEDED")
check("M1 legacy authorization UNSIGNED", answer.get("authorization"), "UNSIGNED")
check("M1 legacy t10_acceptable false", answer.get("t10_acceptable"), False)
check("M1 durable finalize.json is UNSIGNED",
      finalize_payload(store1, item1, run1).get("authorization"), "UNSIGNED")

# M2 — ACTUAL CLI --t10-required without a trusted parent: exit 2, no evidence.
root2 = base / "m2"
store2, item2, run2 = make_store(root2)
captured2 = []
old_emit = cli_mod.emit
cli_mod.emit = lambda value, stream=None: captured2.append((value, stream))
try:
    code = cli_mod.main(["run", "finalize", item2.id, run2.id, "--verdict", "PASS",
                         "--exit-code", "0", "--command", "pytest", "--revision", "r",
                         "--case", "one", "--t10-required", "--receipt", RECEIPT_REF,
                         "--state-dir", str(root2 / "state")])
finally:
    cli_mod.emit = old_emit
check("M2 CLI t10-required exit", code, 2)
check("M2 error code BLOCKED_TRUST_SOURCE",
      captured2[-1][0].get("error", {}).get("code"), "BLOCKED_TRUST_SOURCE")
check("M2 run stays FINALIZING",
      store2.get_run(item2.id, run2.id).state, RunState.FINALIZING)
check("M2 no evidence", evidence_count(store2, item2, run2), 0)

# M3/M4/M5 — direct store refusals before any write.
root3 = base / "m3"
store3, item3, run3 = make_store(root3)
check("M3 t10_required without authority",
      refused(lambda: clamped(store3, item3, run3), {"BLOCKED_TRUST_SOURCE"}),
      "BLOCKED_TRUST_SOURCE")
check("M3 run stays FINALIZING",
      store3.get_run(item3.id, run3.id).state, RunState.FINALIZING)
check("M3 no evidence", evidence_count(store3, item3, run3), 0)
auth = StubReceiptAuthority(receipt(run_ref=run3.id, item_ref=item3.id))
check("M4 t10_required without binding",
      refused(lambda: clamped(store3, item3, run3, authority=auth,
                              bind=None), {"T10_BINDING_REQUIRED"}),
      "T10_BINDING_REQUIRED")
check("M5 t10_required without receipt ref",
      refused(lambda: clamped(store3, item3, run3, authority=auth,
                              bind=binding(run3), receipt_ref=None),
              {"RUNNER_RECEIPT_REQUIRED"}), "RUNNER_RECEIPT_REQUIRED")

# M6/M14 — caller path/JSON is not the opaque receipt_ref.
root6 = base / "m6"
store6, item6, run6 = make_store(root6)
auth6 = StubReceiptAuthority(receipt(item_ref=item6.id, run_ref=run6.id))
for label, ref in (("path", str(base / "m6" / "receipt.json")),
                   ("json", json.dumps({"receipt_sha256": SHA}))):
    check(f"M6 caller {label} is not a receipt_ref",
          refused(lambda ref=ref: clamped(store6, item6, run6, authority=auth6,
                                          bind=binding(run6), receipt_ref=ref),
                  {"UNTRUSTED_RECEIPT"}), "UNTRUSTED_RECEIPT")
check("M6 no evidence", evidence_count(store6, item6, run6), 0)

# M7/M13 — child prints PASS but the receipt says exit 1: never SUCCEEDED, even on retry.
root7 = base / "m7"
store7, item7, run7 = make_store(root7)
bad = StubReceiptAuthority(receipt(item_ref=item7.id, run_ref=run7.id,
                                   process_exit_code=1, verdict="FAIL"))
for attempt in (1, 2):
    check(f"M7 attempt {attempt} PASS/exit0 vs receipt exit1",
          refused(lambda: clamped(store7, item7, run7, authority=bad,
                                  bind=binding(run7), verdict="PASS",
                                  exit_code=0),
                  {"EXIT_CODE_DISAGREEMENT", "RECEIPT_VERDICT_DISAGREEMENT"}),
          "EXIT_CODE_DISAGREEMENT")
    check(f"M7 attempt {attempt} run still FINALIZING",
          store7.get_run(item7.id, run7.id).state, RunState.FINALIZING)
check("M7 no evidence after retries", evidence_count(store7, item7, run7), 0)

# M8 — cross Item / Run / commit / actor binding.
root8 = base / "m8"
store8, item8, run8 = make_store(root8)
good = receipt(item_ref=item8.id, run_ref=run8.id)
auth8 = StubReceiptAuthority(good)
for label, b, code in (
        ("item", binding(run8, item_ref="DEM-9999"), "T10_ITEM_MISMATCH"),
        ("run", binding(run8, run_ref="RUN-9999"), "T10_RUN_MISMATCH"),
        ("commit", binding(run8, commit_sha=STALE_COMMIT), "T10_COMMIT_MISMATCH"),
        ("actor", binding(run8, actor_ref=OTHER_ACTOR), "T10_ACTOR_MISMATCH")):
    check(f"M8 cross-{label} refused",
          refused(lambda b=b: clamped(store8, item8, run8, authority=auth8,
                                      bind=b), {code}), code)
check("M8 no evidence", evidence_count(store8, item8, run8), 0)

# M9 — a revoked grant is the authority's refusal, propagated.
root9 = base / "m9"
store9, item9, run9 = make_store(root9)
revoked = StubReceiptAuthority(error=ReceiptError("AUTHORITY_REVOKED", "revoked"))
check("M9 revoked grant refused",
      refused(lambda: clamped(store9, item9, run9, authority=revoked,
                              bind=binding(run9)), {"AUTHORITY_REVOKED"}),
      "AUTHORITY_REVOKED")
check("M9 no evidence", evidence_count(store9, item9, run9), 0)

# M10/M11 — test-double verified exit0: T10_VERIFIED only; idempotent.
root10 = base / "m10"
store10, item10, run10 = make_store(root10)
auth10 = StubReceiptAuthority(receipt(item_ref=item10.id, run_ref=run10.id))
out10 = clamped(store10, item10, run10, authority=auth10, bind=binding(run10))
check("M10 verified exit0 state", out10.get("state"), "SUCCEEDED")
check("M10 authorization T10_VERIFIED", out10.get("authorization"), "T10_VERIFIED")
check("M10 t10_acceptable true", out10.get("t10_acceptable"), True)
check("M10 durable label T10_VERIFIED",
      finalize_payload(store10, item10, run10).get("authorization"), "T10_VERIFIED")
check("M10 one evidence row", evidence_count(store10, item10, run10), 1)
out10b = clamped(store10, item10, run10, authority=auth10, bind=binding(run10))
check("M11 second T10 finalize is idempotent",
      (out10b.get("state"), out10b.get("authorization")),
      ("SUCCEEDED", "T10_VERIFIED"))
check("M11 no extra evidence row", evidence_count(store10, item10, run10), 1)

# M12 — a terminal UNSIGNED run cannot be relabelled.
root12 = base / "m12"
store12, item12, run12 = make_store(root12)
store12.finalize_run(item12.id, run12.id, verdict="PASS", exit_code=0,
                     command="pytest", cases=["one"], revision="r", dirty=False)
auth12 = StubReceiptAuthority(receipt(item_ref=item12.id, run_ref=run12.id))
check("M12 terminal UNSIGNED cannot be T10-relabelled",
      refused(lambda: clamped(store12, item12, run12, authority=auth12,
                              bind=binding(run12)), {"T10_UNSIGNED_RUN"}),
      "T10_UNSIGNED_RUN")
check("M12 no extra evidence", evidence_count(store12, item12, run12), 1)
check("M12 durable label stays UNSIGNED",
      finalize_payload(store12, item12, run12).get("authorization"), "UNSIGNED")

# ---------------------------------------------------------------------------
# SOURCE — entry route, labels, no production parent, downstream blocker.
# ---------------------------------------------------------------------------
cli_src = (TREE / "workengine/cli.py").read_text(encoding="utf-8")
store_src = (TREE / "workengine/store.py").read_text(encoding="utf-8")
mcp_src = (TREE / "workengine/mcp.py").read_text(encoding="utf-8")
authority_src = (TREE / "workengine/authority.py").read_text(encoding="utf-8")
engine_src = (TREE / "workengine/engine.py").read_text(encoding="utf-8")
check("S1 CLI passes t10_required to the store",
      "t10_required=args.t10_required" in cli_src, True)
check("S1 CLI passes no receipt_authority (no verifier)",
      "receipt_authority" in cli_src, False)
check("S1 store has the t10_required branch",
      "if not t10_required:" in store_src
      and "t10_required=True" not in store_src.split("if not t10_required:")[0][-200:],
      True)
check("S1 store writes durable authorization labels",
      '"authorization": ("T10_VERIFIED" if receipt is not None' in store_src, True)
check("S1 legacy path is labelled UNSIGNED",
      '"t10_acceptable": authorization == "T10_VERIFIED"' in store_src, True)
check("S1 MCP exposes no finalize verb", "finalize" in mcp_src, False)
check("S1 ServerOS facade has no finalize method",
      "def finalize" in engine_src, False)
check("S1 no production RunnerReceiptAuthority implementation",
      "class " in authority_src and
      "class RunnerReceiptAuthority(Protocol)" in authority_src and
      authority_src.count("RunnerReceiptAuthority") <= 4, True)
doc_src = (TREE / "docs/t10-finalize-entry.md").read_text(encoding="utf-8")
check("S1 docs separate T10_VERIFIED from independent QA",
      ("T10_VERIFIED" in doc_src and "independent QA" in doc_src
       and "not" in doc_src), True)

# Downstream blocker: V1 consumers read state/verdict but not the new label.
verdict_src = (SERVEROS / "integrations/fleet-v1-verdict-evidence/verdict_evidence.py").read_text(encoding="utf-8")
bounded_src = (SERVEROS / "integrations/fleet-v1-verdict-evidence/bounded_provenance.py").read_text(encoding="utf-8")
check("S2 V1 consumer reads runs.state/verdict",
      "state" in verdict_src and "verdict" in verdict_src, True)
check("S2 V1 consumer ignores t10_acceptable (downstream blocker)",
      ("t10_acceptable" in verdict_src or "t10_acceptable" in bounded_src), False)

print()
print("scratch:", base)
if failures:
    print(f"PROBE FAILED: {len(failures)} check(s): {failures}")
    sys.exit(1)
print("PROBE OK: WE #15 finalize entry checks held; legacy UNSIGNED; T10_VERIFIED test-double only")
