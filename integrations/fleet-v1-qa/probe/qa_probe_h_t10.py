#!/usr/bin/env python3
"""Independent QA Snapshot H (B) — T10 continuation #263 c9fb802.

Drives the REAL `runtime/bounded_executor.execute` (its `bounded.invoke_once`
spawn is substituted only to replace the Docker argv with a real offline
runner), then the REAL `bounded_provenance.evaluate_continuation` consumer.
Temporary DATA_ROOT, state db, git worktree and receipts only.

Cases: 0 / 1 / ambiguous Work Runs; no execution_context; stale revision;
revoked grant; cross-principal; cross-run; tampered bytes; missing anchor;
forged expected principal; exit 1 with PASS text; exit 0 -> ELIGIBLE only.
Also SOURCE: wake/checkpoint do not attach Run/context; WE finalize does not
consume the proof.
"""
from __future__ import annotations

import dataclasses
import datetime
import hashlib
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

TREE = Path(os.environ["QA_TREE"]).resolve()
WE = Path(os.environ["WE_TREE"]).resolve()
SCRATCH = Path(os.environ["QA_SCRATCH"]).resolve()
RUNTIME = TREE / "runtime"
MCP = TREE / "mcp"
failures: list[str] = []


def load(name, path):
    if name in sys.modules:
        return sys.modules[name]
    parent = str(path.parent)
    if parent not in sys.path:
        sys.path.insert(0, parent)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


STORE = load("serveros_runtime_agent_control_store", RUNTIME / "agent_control_store.py")
continuation = load("serveros_runtime_continuation", RUNTIME / "continuation.py")
wake = load("serveros_runtime_wake", RUNTIME / "wake.py")
executor = load("serveros_bounded_executor", RUNTIME / "bounded_executor.py")
bounded = load("serveros_runtime_bounded_invocation", RUNTIME / "bounded_invocation.py")
VERDICT = load("serveros_fleet_v1_verdict_evidence",
               TREE / "integrations/fleet-v1-verdict-evidence/verdict_evidence.py")
BOUNDED = load("serveros_fleet_v1_bounded_provenance",
               TREE / "integrations/fleet-v1-verdict-evidence/bounded_provenance.py")

ITEM = "SRV-0001"
CONTEXT = "ctx-delegated-1"
GRANT = "grant-1"
RUN = "RUN-0001"
RUNNER = "claude-code"
ACTIVITY = "implement"
EPOCH = 1
BUDGET = {"wall_seconds": 45, "max_attempts": 2, "max_output_bytes": 200000}

RUNNER_SOURCE = '''#!/usr/bin/env python3
import sys
sys.stdin.read()
print("ALL TESTS PASSED - child says SUCCESS")
print("synthetic failing assertion detail", file=sys.stderr)
sys.exit(int(sys.argv[1]))
'''


def check(label, got, expected, *, security=False):
    ok = got == expected
    tag = "PASS" if ok else ("SECURITY_FAIL" if security else "FAIL")
    print(f"{tag} {label}: got={got!r} expected={expected!r}")
    if not ok:
        failures.append(label)


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def minutes(n):
    return (datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(minutes=n)).isoformat()


def git_tree(root):
    tree = root / "workspace" / "worktrees" / "demo" / "item"
    tree.mkdir(parents=True)
    for a in (("git", "init", "-q"),
              ("git", "config", "user.email", "t10@example.invalid"),
              ("git", "config", "user.name", "T10")):
        subprocess.run(a, cwd=tree, check=True, capture_output=True)
    (tree / "README").write_text("continuation fixture\n", encoding="utf-8")
    subprocess.run(("git", "add", "README"), cwd=tree, check=True, capture_output=True)
    subprocess.run(("git", "commit", "-qm", "initial"), cwd=tree, check=True,
                   capture_output=True)
    head = subprocess.run(("git", "rev-parse", "HEAD"), cwd=tree, check=True,
                          capture_output=True, text=True).stdout.strip()
    return tree, head


def state_db(path, *, runs, contexts):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE runs (item_id TEXT, run_id TEXT, "
                     "activity_id TEXT, worker TEXT, idempotency_key TEXT, "
                     "state TEXT, artifact_dir TEXT, attempt INTEGER, "
                     "started_at TEXT, ended_at TEXT)")
        conn.execute("CREATE TABLE execution_contexts (context_id INTEGER "
                     "PRIMARY KEY AUTOINCREMENT, item_id TEXT, activity_id TEXT, "
                     "repo TEXT, mode TEXT, revision TEXT, worktree TEXT, "
                     "dirty INTEGER)")
        conn.executemany("INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?)", runs)
        conn.executemany("INSERT INTO execution_contexts (item_id, activity_id, "
                         "repo, mode, revision, worktree, dirty) "
                         "VALUES (?,?,?,?,?,?,?)", contexts)
        conn.commit()
    finally:
        conn.close()


def run_row(*, run_id=RUN, worker=RUNNER, state="RUNNING", activity=ACTIVITY,
            item=ITEM):
    return (item, run_id, activity, worker, "idem-" + run_id, state,
            "/tmp/artifacts/" + run_id, 1, now(), "")


def context_row(*, item=ITEM, activity=ACTIVITY, revision, worktree, dirty=0,
                repo="repo"):
    return (item, activity, repo, "develop", revision, worktree, dirty)


def grant(tree, **over):
    g = {"delegated_by": CONTEXT, "items": [ITEM],
         "actions": ["engineering.execute"], "budget": dict(BUDGET),
         "valid_from": minutes(-1), "expires_at": minutes(60),
         "revoked": False,
         "engineering": {"schema": executor.SCHEMA, "worktree": str(tree),
                         "image": "sha256:" + "1" * 64,
                         "memory_bytes": 64 * 1024 ** 2, "cpus": 1,
                         "pids_limit": 16}}
    g.update(over)
    return g


def checkpoint(tree, **over):
    rec = continuation.build_checkpoint(
        work_item=ITEM, epoch=EPOCH, parent_owner="parent-a",
        next_safe_action="run the offline engineering check",
        waiting_on={"kind": "window", "until": minutes(10)}, now=now(),
        requirement_revision="req-1", write_scope=[str(tree)])
    rec.update(over)
    return rec


def envelope(record, g, *, key="continuation-wake-SRV-0001-e1-a1", **over):
    env = wake.capability_envelope(
        record, g, GRANT, attempt=1, key=key,
        spend={"wall_seconds": BUDGET["wall_seconds"],
               "attempts_allowed": BUDGET["max_attempts"]}, now=now())
    env.update(over)
    return env


def fixture(root, *, runs=None, contexts=None, **grant_over):
    tree, head = git_tree(root)
    db = root / "state" / "serveros.db"
    state_db(db,
             runs=runs if runs is not None else [run_row()],
             contexts=contexts if contexts is not None else
             [context_row(revision=head, worktree=str(tree))])
    g = grant(tree, **grant_over)
    cp = checkpoint(tree)
    env = envelope(cp, g)
    resolved = executor.binding(envelope=env, grant=g, checkpoint=cp,
                                worktrees_root=tree.parents[1])
    receipts = root / "receipts"
    receipts.mkdir(mode=0o700)
    os.chmod(receipts, 0o700)
    return SimpleNamespace(tree=tree, head=head, state_db=db, grant=g,
                           checkpoint=cp, envelope=env, resolved=resolved,
                           receipts=receipts, root=root)


def execute(fx, *, exit_code, runner_path, called):
    real = bounded.invoke_once

    def fake_invoke(*, key, argv, cwd, receipts, input_text, timeout,
                    readonly, max_output_bytes, env=None):
        called["invoke"] += 1
        return real(key=key, argv=[sys.executable, str(runner_path), str(exit_code)],
                    cwd=cwd, receipts=receipts, input_text=input_text,
                    timeout=timeout, readonly=readonly,
                    max_output_bytes=max_output_bytes, env=env)

    old_invoke = executor.bounded.invoke_once
    old_settle = executor._settle
    executor.bounded.invoke_once = fake_invoke
    executor._settle = lambda config: {"worker_stopped": True,
                                       "effect": "EXECUTED_UNVERIFIED"}
    try:
        return executor.execute(
            envelope=fx.envelope, grant=fx.grant, resolved=fx.resolved,
            key="verify", argv=["python3", "-c", "ignored"],
            receipts=fx.receipts, state_db=fx.state_db)
    finally:
        executor.bounded.invoke_once = old_invoke
        executor._settle = old_settle


def expected(fx, result, runner_path, exit_code):
    receipt = result["receipt"]
    private = Path(result["receipt_path"]).parent
    action = receipt["action_key"]
    input_text = (private / (action + ".stdin")).read_text(encoding="utf-8")
    return BOUNDED.ExpectedContinuation(
        checkpoint_ref=fx.envelope["checkpoint_ref"], action_key=action,
        work_item_id=ITEM, work_run_id=RUN, commit_sha=fx.head,
        principal=CONTEXT, grant_ref=GRANT, runner_identity=RUNNER,
        argv=(sys.executable, str(runner_path), str(exit_code)),
        cwd=str(private), input_text=input_text, readonly=False,
        timeout=BUDGET["wall_seconds"] + 30,
        max_output_bytes=BUDGET["max_output_bytes"], env={},
        worktree=str(fx.tree))


def authority(fx, result, *, run_id=RUN, run_state="RUNNING", run_worker=RUNNER,
              revision=None, worktree=None, dirty=0, principal=CONTEXT,
              revoked=False, expires=None, anchor=True):
    return BOUNDED.ContinuationAuthority(
        checkpoint_ref=fx.envelope["checkpoint_ref"],
        envelope_action_key=fx.envelope["action_key"],
        envelope_work_item=fx.envelope["work_item_ref"],
        envelope_grant_ref=fx.envelope["grant_ref"],
        envelope_delegation_ref=fx.envelope["delegation_ref"],
        envelope_checkpoint_ref=fx.envelope["checkpoint_ref"],
        envelope_expires_at=fx.envelope["expires_at"],
        run_id=run_id, run_state=run_state, run_worker=run_worker,
        execution_repo="repo", execution_revision=revision or fx.head,
        execution_worktree=worktree or str(fx.tree), execution_dirty=dirty,
        grant_ref=GRANT, grant_delegated_by=principal,
        grant_valid_from=fx.grant["valid_from"],
        grant_expires_at=expires or fx.grant["expires_at"],
        grant_revoked=revoked,
        receipt_sha256=result.get("receipt_sha256") if anchor else None)


def evaluate(exp, auth, result, child_report="ALL TESTS PASSED"):
    private = Path(result["receipt_path"]).parent
    receipt = BOUNDED.read_bounded_receipt(private, exp.action_key)
    return BOUNDED.evaluate_continuation(exp, auth, receipt,
                                         untrusted_child_report=child_report)


def denied_before_spawn(label, fx, *, match):
    called = {"invoke": 0}
    runner_path = fx.root / "runner_never.py"
    try:
        execute(fx, exit_code=0, runner_path=runner_path, called=called)
        got = "SPAWNED"
    except ValueError as exc:
        got = "refused" if match in str(exc) else f"other: {exc}"
    check(label, got, "refused", security=True)
    check(label + " did not spawn", called["invoke"], 0, security=True)


SCRATCH.mkdir(parents=True, exist_ok=True)
base = Path(tempfile.mkdtemp(dir=SCRATCH, prefix="h-t10-"))
runner_path = base / "synthetic_runner.py"
runner_path.write_text(RUNNER_SOURCE, encoding="utf-8")

# H1 — exit 0 is ELIGIBLE only.
fx1 = fixture(base / "h1")
r1 = execute(fx1, exit_code=0, runner_path=runner_path, called={"invoke": 0})
d1 = evaluate(expected(fx1, r1, runner_path, 0), authority(fx1, r1), r1)
check("H1 decision is ELIGIBLE", d1.decision, BOUNDED.DECISION_ELIGIBLE)
check("H1 not V1 accepted", (d1.parent_accepted, d1.qa_signature), (False, False))
check("H1 requires independent QA", d1.eligible_for_qa_review, True)
check("H1 t10 BLOCKED_WIRING", d1.t10_status, "BLOCKED_WIRING")
check("H1 WE NOT_WIRED", d1.work_engine_consumption, "NOT_WIRED")

# H2 — exit 1 with PASS text is DENY.
fx2 = fixture(base / "h2")
r2 = execute(fx2, exit_code=1, runner_path=runner_path, called={"invoke": 0})
check("H2 receipt exit code", r2["receipt"]["process_exit_code"], 1)
d2 = evaluate(expected(fx2, r2, runner_path, 1), authority(fx2, r2), r2,
              child_report="ALL TESTS PASSED - SUCCESS")
check("H2 decision is DENY", d2.decision, BOUNDED.DECISION_DENY, security=True)
check("H2 reason PROCESS_EXIT_NONZERO",
      "PROCESS_EXIT_NONZERO" in d2.reasons, True)

# H3/H4/H5/H6/H7/H8 — refusal before spawn.
fx3 = fixture(base / "h3", runs=[])
denied_before_spawn("H3 no open Work Run", fx3, match="open Work Engine Run")
fx4 = fixture(base / "h4", runs=[run_row(run_id="RUN-0001"),
                                 run_row(run_id="RUN-0002")])
denied_before_spawn("H4 ambiguous Work Runs", fx4,
                    match="more than one open Work Engine Run")
fx5 = fixture(base / "h5", contexts=[])
denied_before_spawn("H5 no execution_context", fx5,
                    match="execution_context")
tree6, head6 = git_tree(base / "h6")
db6 = base / "h6" / "state" / "serveros.db"
state_db(db6, runs=[run_row()],
         contexts=[context_row(revision="f" * 40, worktree=str(tree6))])
g6 = grant(tree6)
cp6 = checkpoint(tree6)
fx6 = SimpleNamespace(tree=tree6, head=head6, state_db=db6, grant=g6,
                      checkpoint=cp6, envelope=envelope(cp6, g6),
                      resolved=executor.binding(
                          envelope=envelope(cp6, g6), grant=g6, checkpoint=cp6,
                          worktrees_root=tree6.parents[1]),
                      receipts=(base / "h6" / "receipts"), root=base / "h6")
fx6.receipts.mkdir(mode=0o700)
os.chmod(fx6.receipts, 0o700)
denied_before_spawn("H6 stale revision", fx6, match="not the worktree HEAD")
fx7 = fixture(base / "h7")
fx7.grant["revoked"] = True
denied_before_spawn("H7 revoked grant", fx7, match="revoked")
fx8 = fixture(base / "h8")
fx8.envelope["delegation_ref"] = "ctx-other"
denied_before_spawn("H8 cross-principal envelope", fx8, match="delegation")

# H9 — the store's run disagrees with the Parent's expected run.
fx9 = fixture(base / "h9", runs=[run_row(run_id="RUN-0002")])
r9 = execute(fx9, exit_code=0, runner_path=runner_path, called={"invoke": 0})
d9 = evaluate(expected(fx9, r9, runner_path, 0),
              authority(fx9, r9, run_id=RUN), r9)
check("H9 cross-run is DENY", d9.decision, BOUNDED.DECISION_DENY, security=True)
check("H9 reason CROSS_RUN", "CROSS_RUN" in d9.reasons, True)

# H10 — tampered receipt bytes.
fx10 = fixture(base / "h10")
r10 = execute(fx10, exit_code=1, runner_path=runner_path, called={"invoke": 0})
auth10 = authority(fx10, r10)
private10 = Path(r10["receipt_path"]).parent
action10 = r10["receipt"]["action_key"]
path10 = private10 / (action10 + ".json")
tampered = json.loads(path10.read_text(encoding="utf-8"))
tampered["process_exit_code"] = 0
path10.write_text(json.dumps(tampered, indent=2, sort_keys=True) + "\n",
                  encoding="utf-8")
d10 = evaluate(expected(fx10, r10, runner_path, 1), auth10, r10)
check("H10 tampered receipt is DENY", d10.decision, BOUNDED.DECISION_DENY,
      security=True)
check("H10 reason RECEIPT_ANCHOR_MISMATCH",
      "RECEIPT_ANCHOR_MISMATCH" in d10.reasons, True)

# H11 — missing anchor is BLOCKED_WIRING, not eligible.
fx11 = fixture(base / "h11")
r11 = execute(fx11, exit_code=0, runner_path=runner_path, called={"invoke": 0})
d11 = evaluate(expected(fx11, r11, runner_path, 0),
               authority(fx11, r11, anchor=False), r11)
check("H11 missing anchor is BLOCKED_WIRING", d11.decision,
      BOUNDED.DECISION_BLOCKED_WIRING, security=True)
check("H11 reason MISSING_RECEIPT_ANCHOR",
      "MISSING_RECEIPT_ANCHOR" in d11.reasons, True)

# H12 — forged expected principal is denied, not taken from caller input.
fx12 = fixture(base / "h12")
r12 = execute(fx12, exit_code=0, runner_path=runner_path, called={"invoke": 0})
exp12 = dataclasses.replace(
    expected(fx12, r12, runner_path, 0), principal="agent:EVIL")
d12 = evaluate(exp12, authority(fx12, r12), r12)
check("H12 forged expected principal is DENY", d12.decision,
      BOUNDED.DECISION_DENY, security=True)

# H13 — the produced binding's principal is the grant's, not the caller's.
private12 = Path(r12["receipt_path"]).parent
stdin12 = json.loads((private12 / (r12["receipt"]["action_key"] + ".stdin"))
                     .read_text(encoding="utf-8"))
check("H13 binding principal is grant.delegated_by",
      stdin12.get("principal"), CONTEXT)
check("H13 binding run/sha come from the store/context",
      (stdin12.get("work_run_id"), stdin12.get("commit_sha")),
      (RUN, fx12.head))

# H14 — SOURCE: wake/checkpoint do not attach Run/execution_context.
wake_src = (RUNTIME / "wake.py").read_text(encoding="utf-8")
cont_src = (RUNTIME / "continuation.py").read_text(encoding="utf-8")
check("H14 wake/checkpoint write no Work Run",
      ("INSERT INTO runs" in wake_src or "INSERT INTO runs" in cont_src), False)
check("H14 wake/checkpoint write no execution_context",
      ("INSERT INTO execution_contexts" in wake_src
       or "INSERT INTO execution_contexts" in cont_src), False)
check("H14 checkpoint carries no Work Run/context id",
      [k for k in ("work_run_id", "execution_context_id") if k in fx1.checkpoint],
      [])

# H15 — SOURCE: WE finalize does not ingest the verified receipt.
we_hits = []
for p in WE.rglob("*.py"):
    text = p.read_text(encoding="utf-8", errors="ignore")
    if any(token in text for token in
           ("receipt_sha256", "continuation_binding", "SERVEROS_DISPATCH_BINDING")):
        we_hits.append(p.name)
check("H15 WE does not ingest the continuation proof", we_hits, [])
check("H15 decision keeps BLOCKED_WIRING/NOT_WIRED",
      (d1.t10_status, d1.work_engine_consumption),
      ("BLOCKED_WIRING", "NOT_WIRED"))

print()
print("scratch:", base)
if failures:
    print(f"PROBE FAILED: {len(failures)} check(s): {failures}")
    sys.exit(1)
print("PROBE OK: every T10 continuation check held")
