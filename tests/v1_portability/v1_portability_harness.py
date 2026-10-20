"""Shared public-safe harness for the X2 portability suites.

Everything here is either in-tree serveros code (`workengine/`, `runtime/`,
`integrations/`) or a pinned fixture under `tests/v1_portability/fixtures/`.
It never reads an owner-private Work Engine checkout.

Two kinds of evidence are deliberately kept apart:

* **EXECUTED** — the real in-tree `workengine.ServerOSStore` writes a legacy
  `finalize.json`, and the real `runtime.bounded_invocation.invoke_once`
  produces a runner receipt.
* **PINNED_PUBLIC_FIXTURE** — the durable `T10_VERIFIED` finalize shape is a
  contract fixture, not a production verifier's output.  The suite labels it
  `source="public-canonical-fixture:v1"` and never claims the private #15
  verifier ran.  That proof is `PRIVATE_LEGACY_SOURCE_REQUIRED / NOT_RUN`.
"""
from __future__ import annotations

import datetime
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
FIXTURES = HERE / "fixtures"

ITEM = "SRV-0001"
ACTIVITY = "ACT-0001"
CONTEXT = "ctx-delegated-1"
GRANT = "grant-1"
RUNNER = "claude-code"
RUN = "RUN-0001"
HEAD = "0123456789abcdef0123456789abcdef01234567"
ACTION_KEY = "x2-canonical-action"
INPUT_DIGEST = "a" * 64
RECEIPT_SHA = "b" * 64
FIXTURE_SOURCE = "public-canonical-fixture:v1"

T10_FIXTURE = "finalize_t10_verified.v1.json"
UNSIGNED_FIXTURE = "finalize_unsigned.v1.json"
LEASE_GRANT_FIXTURE = "lease_grant.v1.json"


def load_module(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def integration(name: str, filename: str):
    return load_module(
        name, REPO / "integrations" / "fleet-v1-verdict-evidence" / filename)


def runtime(name: str, filename: str):
    return load_module(name, REPO / "runtime" / filename)


VERDICT = integration(
    "serveros_fleet_v1_verdict_evidence", "verdict_evidence.py")
FINAL = integration(
    "serveros_fleet_v1_signed_finalization", "finalization_result.py")
V1 = integration(
    "serveros_fleet_v1_v1_acceptance", "v1_acceptance.py")


def workengine():
    """The CURRENT in-tree sanitized workengine, never an external checkout.

    A shared pytest process may already hold a `workengine` imported from an
    external checkout by another suite.  That binding is dropped rather than
    silently used, so this harness can never inherit a private package.
    """
    cached = sys.modules.get("workengine")
    if cached is not None:
        location = Path(getattr(cached, "__file__", "") or "").resolve()
        if REPO not in location.parents:
            for name in [name for name in sys.modules
                         if name == "workengine" or name.startswith("workengine.")]:
                del sys.modules[name]
    root = str(REPO)
    if root in sys.path:
        sys.path.remove(root)
    sys.path.insert(0, root)
    import workengine as package  # noqa: PLC0415  (path is set immediately above)

    location = Path(package.__file__).resolve()
    if REPO not in location.parents:
        raise RuntimeError(
            f"workengine was imported from {location}, not the in-tree {REPO}")
    return package


def in_tree_authority():
    """The signed authority module, only if the PUBLIC tree ships one.

    `workengine/authority.py` is absent from the sanitized public tree.  When
    it is absent this returns None and the caller classifies the proof
    `PRIVATE_LEGACY_SOURCE_REQUIRED / NOT_RUN` instead of skipping silently.
    """
    path = REPO / "workengine" / "authority.py"
    if not path.is_file():
        return None
    workengine()
    importlib.invalidate_caches()
    try:
        return importlib.import_module("workengine.authority")
    except ImportError:
        return None


def now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def fixture_sha256(name: str) -> str:
    return hashlib.sha256(fixture_bytes(name)).hexdigest()


def lease_grant_contract() -> dict:
    return json.loads(fixture_bytes(LEASE_GRANT_FIXTURE))


def expected_finalization():
    receipt = json.loads(fixture_bytes(T10_FIXTURE))["runner_receipt"]
    return FINAL.ExpectedFinalization(
        item_ref=ITEM,
        run_ref=RUN,
        commit_sha=HEAD,
        actor_ref=CONTEXT,
        action_key=receipt["action_key"],
        input_digest=receipt["input_digest"],
        receipt_sha256=receipt["receipt_sha256"],
        process_exit_code=0,
        runner_identity=RUNNER,
    )


def grant_snapshot(**over):
    values = dict(
        grant_ref=GRANT,
        principal=CONTEXT,
        verified=True,
        fresh=True,
        revoked=False,
        epoch=1,
        verified_by="test-double:t10-local-parent",
        checked_at=now_iso(),
    )
    values.update(over)
    return V1.GrantFreshnessSnapshot(**values)


def legacy_scenario(tmp_path: Path) -> SimpleNamespace:
    """The REAL in-tree workengine writes a legacy finalize (no marker)."""
    package = workengine()
    worktree = (tmp_path / "worktree").resolve()
    worktree.mkdir(parents=True, exist_ok=True)
    store = package.ServerOSStore(tmp_path / "state")
    store.register_project("demo", "SRV")
    item = store.create_item(
        package.WorkItem(ITEM, "demo", "x2 legacy", "test", "/card"))
    store.add_activity(package.Activity(item.id, ACTIVITY, "test"))
    store.attach_context(package.ExecutionContext(
        item.id, ACTIVITY, "repo", package.ContextMode.TEST, HEAD,
        str(worktree), False))
    run = store.begin_run(
        item.id, ACTIVITY, RUNNER, "x2-legacy-1", tmp_path / "artifacts")
    for state in (package.RunState.PREPARING, package.RunState.RUNNING,
                  package.RunState.FINALIZING):
        run = store.advance_run(item.id, run.id, state)
    result = store.finalize_run(
        item.id, run.id, verdict="PASS", exit_code=0,
        command="pytest", cases=["one"], revision=HEAD, dirty=False)
    readback = FINAL.read_work_engine_finalization(
        store.db_path, item.id, run.id)
    expected = expected_finalization()
    return SimpleNamespace(
        store=store, item=item, run=run, worktree=worktree, result=result,
        readback=readback, expected=expected,
        finalize_path=Path(readback.evidence_locator))


def public_readback(tmp_path: Path, *, fixture_name: str = T10_FIXTURE):
    """A PINNED public fixture of the durable finalize shape.

    The bytes are written to a Run-shaped `artifact_dir/finalize.json` and the
    readback is stamped `source="public-canonical-fixture:v1"`.  This exercises
    the consumer gate; it does not claim a private verifier produced it.
    """
    artifact_dir = (tmp_path / "artifact").resolve()
    artifact_dir.mkdir(parents=True, exist_ok=True)
    locator = artifact_dir / "finalize.json"
    data = fixture_bytes(fixture_name)
    locator.write_bytes(data)
    payload = json.loads(data)
    authorization = payload.get("authorization")
    receipt = payload.get("runner_receipt")
    if not isinstance(receipt, dict):
        receipt = None
    readback = FINAL.WorkEngineFinalizeReadback(
        item_ref=ITEM,
        run_ref=RUN,
        run_state="SUCCEEDED",
        run_worker=RUNNER,
        artifact_dir=str(artifact_dir),
        evidence_locator=str(locator),
        evidence_sha256=hashlib.sha256(data).hexdigest(),
        evidence_bytes=data,
        evidence_verified=True,
        authorization=authorization if isinstance(authorization, str) else None,
        t10_acceptable=(
            authorization == "T10_VERIFIED" and receipt is not None),
        verdict=payload.get("verdict"),
        exit_code=payload.get("exit_code"),
        revision=payload.get("revision"),
        dirty=payload.get("dirty"),
        runner_receipt=receipt,
        source=FIXTURE_SOURCE,
        evidence_level="LOCAL_HERMETIC",
    )
    return SimpleNamespace(
        readback=readback, expected=expected_finalization(),
        artifact_dir=artifact_dir, locator=locator, payload=payload)


RUNNER_SOURCE = '''#!/usr/bin/env python3
"""Offline child: prints PASS text and exits with the requested code."""
import sys
sys.stdin.read()
print("ALL TESTS PASSED - child says SUCCESS")
print("synthetic failing assertion detail", file=sys.stderr)
sys.exit(int(sys.argv[1]))
'''


def bounded_receipt(tmp_path: Path, *, exit_code: int = 1) -> SimpleNamespace:
    """A REAL receipt from the in-tree bounded runner (no Docker, no network)."""
    bounded = runtime(
        "serveros_runtime_bounded_invocation", "bounded_invocation.py")
    executor = runtime(
        "serveros_runtime_bounded_executor", "bounded_executor.py")
    receipts = (tmp_path / "receipts").resolve()
    receipts.mkdir(mode=0o700, exist_ok=True)
    receipts.chmod(0o700)
    runner = (tmp_path / "synthetic_runner.py").resolve()
    runner.write_text(RUNNER_SOURCE, encoding="utf-8")
    key = f"x2-child-exit-{exit_code}"
    record = bounded.invoke_once(
        key=key,
        argv=[sys.executable, str(runner), str(exit_code)],
        cwd=tmp_path.resolve(),
        receipts=receipts,
        input_text="",
        timeout=30,
        readonly=False,
        max_output_bytes=1_000_000,
    )
    anchor = executor.receipt_sha256(receipts, key)
    stdout = (receipts / (key + ".stdout")).read_bytes()
    return SimpleNamespace(
        record=record, key=key, anchor=anchor, stdout=stdout,
        input_digest=record["input_digest"], receipts=receipts)
