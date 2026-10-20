"""Shared public-safe harness for the X2 portability suites.

Everything here is either in-tree serveros code (`workengine/`, `runtime/`,
`integrations/`) or a pinned fixture under `tests/v1_portability/fixtures/`.
It never reads an owner-private Work Engine checkout.

Evidence labels (kept explicit in every producer test):

* **PUBLIC_REIMPLEMENTATION_LOCAL** — the public in-tree
  `workengine/authority.py` dataclasses (`LeaseGrant`, `VerifiedRunReceipt`,
  `T10Binding`) plus a locally controlled `RunnerReceiptAuthority` test
  producer. This proves the public API and its linkage to the consumer gate;
  it is NOT a production trusted parent.
* **HISTORICAL_PRIVATE_SOURCE_UNVERIFIED** — the old owner-private Work Engine
  #15 producer. It is not read, extracted or copied here, and no claim is made
  that it ever produced the same record.

Two kinds of fixture data are also kept apart:

* **EXECUTED** — the real in-tree `workengine.ServerOSStore` writes the
  finalize (legacy `UNSIGNED` or public `T10_VERIFIED`), and the real
  `runtime.bounded_invocation.invoke_once` produces a runner receipt.
* **PINNED_PUBLIC_FIXTURE** — the durable `T10_VERIFIED` / explicit `UNSIGNED`
  consumer shapes under `fixtures/`, used where the producer is not the point.
"""
from __future__ import annotations

import codecs
import dataclasses
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

PUBLIC_REIMPLEMENTATION_LOCAL = "PUBLIC_REIMPLEMENTATION_LOCAL"
HISTORICAL_PRIVATE_SOURCE_UNVERIFIED = "HISTORICAL_PRIVATE_SOURCE_UNVERIFIED"

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
    """The CURRENT in-tree workengine, never an external checkout.

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
    """The public signed-authority module, if this tree ships one.

    RC-v2 (`ec43f714`) does; the pre-#304 tree did not.  Callers on a tree
    without it must classify the proof, not skip it silently.
    """
    path = REPO / "workengine" / "authority.py"
    if not path.is_file():
        return None
    workengine()
    importlib.invalidate_caches()
    try:
        module = importlib.import_module("workengine.authority")
    except ImportError:
        return None
    location = Path(module.__file__).resolve()
    if REPO not in location.parents:
        raise RuntimeError(
            f"workengine.authority was imported from {location}, not {REPO}")
    return module


def now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class FixtureBytesError(ValueError):
    """A pinned fixture is not in a canonical, faithful byte form."""


_BOMS = (
    codecs.BOM_UTF8,
    codecs.BOM_UTF16_LE,
    codecs.BOM_UTF16_BE,
    codecs.BOM_UTF32_LE,
    codecs.BOM_UTF32_BE,
)


def canonical_json_bytes(raw: bytes) -> bytes:
    """Return the canonical LF bytes, or refuse an unfaithful representation.

    Windows `core.autocrlf` may hand the suite a PURE CRLF transcription of the
    same file.  That one transformation is accepted, and only that one:

    * no BOM of any encoding;
    * every CR is part of a CRLF and every LF is part of a CRLF (no lone CR,
      no mixed endings);
    * the result is valid UTF-8 JSON.

    A changed field or an extra byte is NOT detected here; it is detected by
    the pinned SHA-256 over the returned canonical bytes.  This function only
    removes a line-ending transcription, never a semantic difference.
    """
    if raw.startswith(_BOMS):
        raise FixtureBytesError("BOM: a pinned fixture must not carry a byte-order mark")
    cr = raw.count(b"\r")
    lf = raw.count(b"\n")
    crlf = raw.count(b"\r\n")
    if cr or crlf:
        if cr != crlf:
            raise FixtureBytesError(
                "LONE_CR: every CR in a pinned fixture must be part of CRLF")
        if lf != crlf:
            raise FixtureBytesError(
                "MIXED_EOL: a pinned fixture must be all-LF or all-CRLF")
        normalized = raw.replace(b"\r\n", b"\n")
    else:
        normalized = raw
    try:
        text = normalized.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FixtureBytesError("UTF8: a pinned fixture must be UTF-8") from exc
    try:
        json.loads(text)
    except ValueError as exc:
        raise FixtureBytesError("JSON: a pinned fixture must be valid JSON") from exc
    return normalized


def canonical_fixture_bytes(name: str) -> bytes:
    """The canonical bytes the suite actually consumes, independent of EOL."""
    return canonical_json_bytes((FIXTURES / name).read_bytes())


def fixture_bytes(name: str) -> bytes:
    """Canonical LF bytes, so every consumer hashes the same contract."""
    return canonical_fixture_bytes(name)


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


def prepare_run(root) -> SimpleNamespace:
    """A real in-tree Item/Activity/Context/Run in the FINALIZING state."""
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    package = workengine()
    worktree = root / "worktree"
    worktree.mkdir(parents=True, exist_ok=True)
    store = package.ServerOSStore(root / "state")
    store.register_project("demo", "SRV")
    item = store.create_item(
        package.WorkItem(ITEM, "demo", "x2 portability", "test", "/card"))
    store.add_activity(package.Activity(item.id, ACTIVITY, "test"))
    store.attach_context(package.ExecutionContext(
        item.id, ACTIVITY, "repo", package.ContextMode.TEST, HEAD,
        str(worktree), False))
    run = store.begin_run(
        item.id, ACTIVITY, RUNNER, "x2-run-1", root / "artifacts")
    for state in (package.RunState.PREPARING, package.RunState.RUNNING,
                  package.RunState.FINALIZING):
        run = store.advance_run(item.id, run.id, state)
    return SimpleNamespace(
        store=store, item=item, run=run, worktree=worktree, head=HEAD,
        root=root)


def legacy_scenario(tmp_path: Path) -> SimpleNamespace:
    """The REAL in-tree workengine writes a legacy finalize (`UNSIGNED`)."""
    sc = prepare_run(Path(tmp_path) / "legacy")
    result = sc.store.finalize_run(
        sc.item.id, sc.run.id, verdict="PASS", exit_code=0,
        command="pytest", cases=["one"], revision=HEAD, dirty=False)
    readback = FINAL.read_work_engine_finalization(
        sc.store.db_path, sc.item.id, sc.run.id)
    return SimpleNamespace(
        **vars(sc), result=result, readback=readback,
        expected=expected_finalization(),
        finalize_path=Path(readback.evidence_locator))


def public_readback(tmp_path: Path, *, fixture_name: str = T10_FIXTURE):
    """A PINNED public fixture of the durable finalize shape.

    The bytes are written to a Run-shaped `artifact_dir/finalize.json` and the
    readback is stamped `source="public-canonical-fixture:v1"`.  This exercises
    the consumer gate; it does not claim a producer ran.
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


def bounded_receipt(tmp_path: Path, *, exit_code: int = 1,
                    key: str | None = None) -> SimpleNamespace:
    """A REAL receipt from the in-tree bounded runner (no Docker, no network)."""
    bounded = runtime(
        "serveros_runtime_bounded_invocation", "bounded_invocation.py")
    executor = runtime(
        "serveros_runtime_bounded_executor", "bounded_executor.py")
    root = Path(tmp_path).resolve()
    root.mkdir(parents=True, exist_ok=True)
    receipts = root / "receipts"
    receipts.mkdir(mode=0o700, exist_ok=True)
    receipts.chmod(0o700)
    runner = root / "synthetic_runner.py"
    runner.write_text(RUNNER_SOURCE, encoding="utf-8")
    key = key or f"x2-child-exit-{exit_code}"
    record = bounded.invoke_once(
        key=key,
        argv=[sys.executable, str(runner), str(exit_code)],
        cwd=root,
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


def t10_inputs(tmp_path: Path, *, exit_code: int = 0, verdict: str = "PASS",
               receipt_overrides: dict | None = None,
               binding_overrides: dict | None = None,
               root_name: str = "t10") -> SimpleNamespace:
    """The exact public T10 inputs, without finalizing yet.

    The `VerifiedRunReceipt` is built from a REAL in-tree bounded-runner
    receipt.  The `T10Binding` and the dataclasses are the public in-tree
    `workengine.authority` definitions.  What is local is only the
    `RunnerReceiptAuthority` implementation below; the label says so.
    """
    authority = in_tree_authority()
    if authority is None:
        raise RuntimeError(
            "the public in-tree workengine.authority is absent; this tree "
            "cannot run the public T10 conformance path")
    root = (Path(tmp_path) / root_name).resolve()
    sc = prepare_run(root)
    receipt = bounded_receipt(
        root, exit_code=exit_code, key=f"x2-t10-exit-{exit_code}")
    verified = authority.VerifiedRunReceipt(
        item_ref=sc.item.id,
        run_ref=sc.run.id,
        action_key=receipt.key,
        input_digest=receipt.input_digest,
        receipt_sha256=receipt.anchor,
        process_exit_code=receipt.record["process_exit_code"],
        verdict=verdict,
        runner_identity=RUNNER,
        commit_sha=HEAD,
        actor_ref=CONTEXT,
    )
    if receipt_overrides:
        verified = dataclasses.replace(verified, **receipt_overrides)
    binding = authority.T10Binding(
        item_ref=sc.item.id, run_ref=sc.run.id,
        commit_sha=HEAD, actor_ref=CONTEXT)
    if binding_overrides:
        binding = dataclasses.replace(binding, **binding_overrides)
    expected = FINAL.ExpectedFinalization(
        item_ref=verified.item_ref,
        run_ref=verified.run_ref,
        commit_sha=verified.commit_sha,
        actor_ref=verified.actor_ref,
        action_key=verified.action_key,
        input_digest=verified.input_digest,
        receipt_sha256=verified.receipt_sha256,
        process_exit_code=verified.process_exit_code,
        runner_identity=verified.runner_identity,
    )
    return SimpleNamespace(
        **vars(sc), authority=authority, receipt=receipt,
        verified=verified, binding=binding, expected=expected,
        receipt_ref="local-test-receipt:" + receipt.key)


def local_receipt_authority(inputs: SimpleNamespace, *,
                            label: str = PUBLIC_REIMPLEMENTATION_LOCAL):
    """A LOCALLY CONTROLLED `RunnerReceiptAuthority`, clearly labelled.

    It answers only for the exact `receipt_ref` this harness issued.  It is not
    a production trusted parent and cannot become one: the consumer gate still
    stops at `ELIGIBLE_FOR_QA` with `production_status=BLOCKED_TRUST_SOURCE`.
    """
    class LocalReceiptAuthority:
        EVIDENCE_LABEL = label

        def verify(self, receipt_ref, *, item_ref, run_ref, now):
            if receipt_ref != inputs.receipt_ref:
                raise inputs.authority.ReceiptError(
                    "UNKNOWN_RECEIPT",
                    f"this local producer has no receipt {receipt_ref!r}")
            if item_ref != inputs.item.id or run_ref != inputs.run.id:
                raise inputs.authority.ReceiptError(
                    "RECEIPT_IDENTITY_MISMATCH",
                    "the local producer answers only for its own exact Item/Run")
            return inputs.verified

    return LocalReceiptAuthority()


def t10_scenario(tmp_path: Path, *, exit_code: int = 0,
                 verdict: str = "PASS", root_name: str = "t10") -> SimpleNamespace:
    """The real public T10 finalize path with a local test producer."""
    inputs = t10_inputs(tmp_path, exit_code=exit_code, verdict=verdict,
                        root_name=root_name)
    producer = local_receipt_authority(inputs)
    result = inputs.store.finalize_run(
        inputs.item.id, inputs.run.id,
        verdict=verdict, exit_code=exit_code,
        command="pytest", cases=["one"], revision=HEAD, dirty=False,
        t10_required=True, t10_binding=inputs.binding,
        receipt_ref=inputs.receipt_ref, receipt_authority=producer)
    readback = FINAL.read_work_engine_finalization(
        inputs.store.db_path, inputs.item.id, inputs.run.id)
    return SimpleNamespace(
        **vars(inputs), producer=producer, result=result, readback=readback)
