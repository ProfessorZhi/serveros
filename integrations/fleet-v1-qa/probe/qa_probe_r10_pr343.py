#!/usr/bin/env python3
"""Independent probe for PR #343 (public in-tree Work Engine T10 fixtures).

Checks that the two fixtures read the in-tree package, leave one module
identity, and that the real T10 refusals still raise the SAME ReceiptError
class (not a private re-import or a caller label).
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

TREE = Path(os.environ["QA_TREE"]).resolve()
sys.path.insert(0, str(TREE))

FAIL = 0
TOTAL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global FAIL, TOTAL
    TOTAL += 1
    if cond:
        print(f"PASS {name}")
    else:
        FAIL += 1
        print(f"FAIL {name} {detail}")


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


import workengine  # noqa: E402
import workengine.authority as authority  # noqa: E402
import workengine.store as store  # noqa: E402

repo = TREE.resolve()
authority_file = Path(authority.__file__).resolve()
store_file = Path(store.__file__).resolve()
check("import/in-tree-authority", authority_file.is_relative_to(repo), str(authority_file))
check("import/in-tree-store", store_file.is_relative_to(repo), str(store_file))
check("import/one-package", sys.modules["workengine"] is workengine)

# A real refusal raised inside the public store (the class is imported locally).
def store_refusal() -> BaseException:
    try:
        store._read_finalize_record(
            {"locator": "/tmp/q1-r10-missing-finalize.json",
             "sha256": "0" * 64})
    except BaseException as exc:  # noqa: BLE001
        return exc
    raise AssertionError("the store accepted a missing finalize record")


exc_store = store_refusal()
check("identity/store-refusal-is-authority-class",
      type(exc_store) is authority.ReceiptError,
      f"{type(exc_store).__name__}: {exc_store}")
check("identity/store-refusal-code",
      getattr(exc_store, "reason_code", "") == "EVIDENCE_MISSING",
      repr(exc_store))

bridge = load("qa_t10_bridge_mod", TREE / "tests" / "test_fleet_v1_t10_authority_bridge.py")
signed = load("qa_t10_signed_mod", TREE / "tests" / "test_fleet_v1_t10_signed_finalization.py")
ns14 = bridge.we14.__wrapped__()
ns15 = signed.we15.__wrapped__()

check("fixture/bridge-same-class", ns14.ReceiptError is authority.ReceiptError)
check("fixture/signed-same-class", ns15.ReceiptError is authority.ReceiptError)
check("fixture/bridge-same-verified-receipt",
      ns14.VerifiedRunReceipt is authority.VerifiedRunReceipt)
check("fixture/signed-same-verified-receipt",
      ns15.VerifiedRunReceipt is authority.VerifiedRunReceipt)
check("fixture/bridge-same-t10-binding",
      ns15.T10Binding is authority.T10Binding)
check("fixture/module-identity-kept",
      sys.modules["workengine.authority"] is authority)

# A real refusal from the public validator, not a test double.
def refusal() -> BaseException:
    receipt = authority.VerifiedRunReceipt(
        item_ref="SRV-0001", run_ref="run-1", action_key="act-1",
        input_digest="a" * 64, receipt_sha256="not-hex",
        process_exit_code=0, verdict="PASS", runner_identity="claude-code",
        commit_sha="0" * 40, actor_ref="ctx-delegated-1")
    try:
        authority.validate_verified_receipt(
            receipt, item_id="SRV-0001", run_id="run-1",
            verdict="PASS", exit_code=0)
    except BaseException as exc:  # noqa: BLE001
        return exc
    raise AssertionError("the public validator accepted a non-hex anchor")


exc = refusal()
check("refusal/is-receipt-error", type(exc) is authority.ReceiptError,
      f"{type(exc).__name__}: {exc}")
check("refusal/is-not-private-copy",
      type(exc).__module__ == "workengine.authority",
      type(exc).__module__)


def binding_refusal() -> BaseException:
    receipt = authority.VerifiedRunReceipt(
        item_ref="SRV-0001", run_ref="run-1", action_key="act-1",
        input_digest="a" * 64, receipt_sha256="b" * 64,
        process_exit_code=0, verdict="PASS", runner_identity="claude-code",
        commit_sha="0" * 40, actor_ref="ctx-delegated-1")
    binding = authority.T10Binding(
        item_ref="SRV-0001", run_ref="run-1", commit_sha="0" * 40,
        actor_ref="someone-else")
    try:
        authority.validate_t10_binding(
            binding, item_id="SRV-0001", run_id="run-1", receipt=receipt)
    except BaseException as exc:  # noqa: BLE001
        return exc
    raise AssertionError("a mismatched actor binding was accepted")


exc2 = binding_refusal()
check("binding-refusal/is-receipt-error",
      type(exc2) is authority.ReceiptError,
      f"{type(exc2).__name__}: {exc2}")
check("binding-refusal/actor-mismatch",
      getattr(exc2, "reason_code", "") == "T10_ACTOR_MISMATCH",
      repr(exc2))

# Static: no private checkout read, no module purge.
for mod, label in ((bridge, "bridge"), (signed, "signed")):
    src = Path(mod.__file__).read_text(encoding="utf-8")
    check(f"static/{label}/no-private-path",
          "/data/home/vince/workspace/src/work-engine" not in src)
    check(f"static/{label}/no-tarfile", "tarfile" not in src)
    check(f"static/{label}/no-workengine-purge",
          "del sys.modules" not in src
          and 'for name in [name for name in sys.modules' not in src)
    check(f"static/{label}/no-WE_REPO-name",
          "WE_REPO" not in src and "WE_HEAD" not in src)
    check(f"static/{label}/historical-label-only",
          f"HISTORICAL_WE{'14' if label == 'bridge' else '15'}_HEAD" in src)

print(f"TOTAL {TOTAL} FAIL {FAIL}")
raise SystemExit(1 if FAIL else 0)
