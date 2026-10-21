#!/usr/bin/env python3
"""Independent QA Snapshot G (B) — T10 producer #260 ca1c05e.

Drives the REAL `bin/serverosctl work dispatch` from the #260 tree with a real
offline runner spawned by `runtime/bounded_invocation.invoke_once`, then the
REAL consumer `bounded_provenance.evaluate_dispatch`. Temporary DATA_ROOT and
git worktree only. No provider, network, credential, login or live store.

Asserts:
  * child prints PASS text + exits 1 -> DENY / PROCESS_EXIT_NONZERO;
  * exit 0 with the exact binding -> at most ELIGIBLE_FOR_QA (never PASS);
  * incomplete flags / wrong SHA / dirty tree / revoked grant -> refuse before
    spawn, no receipt;
  * tampered receipt bytes -> DENY / RECEIPT_ANCHOR_MISMATCH;
  * forged work_run / principal -> DENY via input-digest mismatch;
  * non-T10 dispatch -> BLOCKED_WIRING;
  * the decision keeps t10_status=BLOCKED_WIRING and
    work_engine_consumption=NOT_WIRED (WE finalize does not consume the proof).
"""
from __future__ import annotations

import datetime
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

TREE = Path(os.environ["QA_TREE"]).resolve()
SCRATCH = Path(os.environ["QA_SCRATCH"]).resolve()
CLI = TREE / "bin" / "serverosctl"
BOUNDED_PATH = TREE / "integrations/fleet-v1-verdict-evidence/bounded_provenance.py"
VERDICT_PATH = TREE / "integrations/fleet-v1-verdict-evidence/verdict_evidence.py"
STORE_PATH = TREE / "runtime/agent_control_store.py"

failures: list[str] = []


def load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


BOUNDED = load("qa_bounded_provenance", BOUNDED_PATH)
VERDICT = load("qa_verdict_evidence", VERDICT_PATH)
STORE = load("qa_agent_control_store_t10", STORE_PATH)
AgentControlStore = STORE.AgentControlStore

CONTEXT = "ctx-delegated-1"
GRANT = "grant-1"
ITEM = "SRV-1"
RUNNER = "claude-code"
DIGEST = "a" * 64
BUDGET = {"wall_seconds": 60, "max_attempts": 2, "max_output_bytes": 200000}
FINALIZE_COMMAND = "python -m pytest synthetic"

RUNNER_SOURCE = '''#!/usr/bin/env python3
import os, sys
sys.stdin.read()
print("ENV:" + os.environ.get("SERVEROS_DISPATCH_BINDING", ""))
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


def window():
    n = datetime.datetime.now(datetime.timezone.utc)
    return {"valid_from": (n - datetime.timedelta(hours=1)).isoformat(),
            "expires_at": (n + datetime.timedelta(days=1)).isoformat()}


def grant(**over):
    g = {"grant_ref": GRANT, "delegated_by": CONTEXT, "items": [ITEM],
         "budget": dict(BUDGET), **window()}
    g.update(over)
    return g


def record(*, cycle, action, holder, resource):
    return {
        "cycle_ref": cycle, "grant_ref": GRANT,
        "selection": {"selection_digest": DIGEST, "observed_at": now(),
                      "selected": [ITEM], "carryover": []},
        "reservation": dict(BUDGET),
        "lease": {"resource": resource, "scope": "session", "holder": holder},
        "action_key": action, "admitted_at": now(),
    }


def data_root(root: Path):
    data = root / "data"
    for area in ("state/serveros/runtime/agent-control", "state/boards",
                 "workspace/records", "artifacts/work-items"):
        (data / area).mkdir(parents=True, exist_ok=True)
    return {"DATA_ROOT": str(data), "HOME": str(data),
            "WORKSPACE_ROOT": str(data / "workspace"),
            "ARTIFACTS_ROOT": str(data / "artifacts"),
            "SERVEROS_STATE_DIR": str(data / "state" / "serveros")}


def cli(env, *args):
    e = dict(os.environ)
    e.update(env)
    return subprocess.run([sys.executable, str(CLI), *args],
                          capture_output=True, text=True, cwd=str(TREE), env=e)


def store_path(env):
    return Path(env["DATA_ROOT"]) / "state/serveros/runtime/agent-control/store.json"


def seed(env):
    store = AgentControlStore(store_path(env))
    store.put_context({"context_ref": CONTEXT})
    store.put_grant(grant())
    return store


def hold(env, *, cycle, resource, holder):
    d = cli(env, "lease", "declare", resource, "--scope", "session",
            "--holder", holder, "--sharing", "shared")
    assert d.returncode == 0, d.stderr
    c = cli(env, "work", "confirm", cycle, "--phase", "LEASE_HELD")
    assert c.returncode == 0, c.stderr


def git_worktree(root: Path):
    wt = root / "worktree"
    wt.mkdir(parents=True)
    for a in (("git", "init", "-q"),
              ("git", "config", "user.email", "t10@example.invalid"),
              ("git", "config", "user.name", "T10 Producer")):
        subprocess.run(a, cwd=wt, check=True, capture_output=True)
    (wt / "README").write_text("t10 producer fixture\n", encoding="utf-8")
    subprocess.run(("git", "add", "README"), cwd=wt, check=True, capture_output=True)
    subprocess.run(("git", "commit", "-qm", "initial"), cwd=wt, check=True,
                   capture_output=True)
    head = subprocess.run(("git", "rev-parse", "HEAD"), cwd=wt, check=True,
                          capture_output=True, text=True).stdout.strip()
    return wt, head


def runner(root: Path):
    p = root / "synthetic_runner.py"
    p.write_text(RUNNER_SOURCE, encoding="utf-8")
    return p


def receipts(root: Path):
    p = root / "receipts"
    p.mkdir(mode=0o700)
    os.chmod(p, 0o700)
    return p


def dispatch(env, wt, rcpt, run, *, cycle, action, sha, exit_code,
             t10=True, extra=(), command=None, runner_path=None):
    args = ["work", "dispatch", cycle, "--cwd", str(wt), "--receipts", str(rcpt)]
    if t10:
        args += ["--t10-work-item", ITEM, "--t10-work-run", run,
                 "--t10-commit-sha", sha, "--t10-runner-identity", RUNNER]
    args += list(extra)
    cmd = (list(command) if command is not None
           else [sys.executable, str(runner_path), str(exit_code)])
    args += ["--", *cmd]
    return cli(env, *args)


def binding(*, cycle, action, run, sha, item=ITEM, principal=CONTEXT,
            grant_ref=GRANT, runner_id=RUNNER):
    return {"cycle_ref": cycle, "action_key": action, "work_item_id": item,
            "work_run_id": run, "commit_sha": sha, "principal": principal,
            "grant_ref": grant_ref, "runner_identity": runner_id}


def canonical(b):
    return json.dumps(b, sort_keys=True, separators=(",", ":"))


def expected(b, *, runner_path, wt, exit_code, readonly=False,
             finalize_command=FINALIZE_COMMAND):
    return BOUNDED.ExpectedDispatch(
        cycle_ref=b["cycle_ref"], action_key=b["action_key"],
        work_item_id=b["work_item_id"], work_run_id=b["work_run_id"],
        commit_sha=b["commit_sha"], principal=b["principal"],
        grant_ref=b["grant_ref"], runner_identity=b["runner_identity"],
        argv=(sys.executable, str(runner_path), str(exit_code)),
        cwd=str(wt), input_text=canonical(b), readonly=readonly,
        timeout=BUDGET["wall_seconds"],
        max_output_bytes=BUDGET["max_output_bytes"],
        env={"SERVEROS_DISPATCH_BINDING": canonical(b)},
        finalize_command=finalize_command, selection_digest=DIGEST)


def admission(store, cycle):
    r = store.get_admission(cycle)
    return BOUNDED.AdmissionSnapshot(
        cycle_ref=r["cycle_ref"], action_key=r["action_key"],
        grant_ref=r["grant_ref"], phase=r["phase"], admitted_at=r["admitted_at"],
        retired_at=r.get("retired_at"), reservation=r["reservation"],
        lease=r["lease"], selection=r["selection"],
        confirmations=r.get("confirmations") or {})


def grant_snapshot(store):
    g = store.grants()[GRANT]
    return BOUNDED.GrantSnapshot(
        grant_ref=GRANT, delegated_by=g["delegated_by"],
        valid_from=g["valid_from"], expires_at=g["expires_at"],
        revoked=bool(g.get("revoked")))


def work_authority(root, *, run, sha, exit_code, verdict):
    d = (root / ("work-" + run)).resolve()
    d.mkdir(parents=True, exist_ok=True)
    loc = d / "finalize.json"
    payload = {"command": FINALIZE_COMMAND, "cases": ["synthetic"],
               "exit_code": exit_code, "revision": sha, "dirty": False,
               "verdict": verdict}
    data = (json.dumps(payload, sort_keys=True) + "\n").encode()
    loc.write_bytes(data)
    run_row = {"item_id": ITEM, "run_id": run, "worker": RUNNER,
               "state": "SUCCEEDED" if verdict == "PASS" and exit_code == 0 else "FAILED",
               "artifact_dir": str(d)}
    ev = {"item_id": ITEM, "run_id": run, "kind": "finalize", "locator": str(loc),
          "sha256": hashlib.sha256(data).hexdigest(), "command": FINALIZE_COMMAND,
          "cases_json": json.dumps(payload["cases"]), "exit_code": exit_code,
          "revision": sha, "dirty": 0, "verdict": verdict}
    return VERDICT.AuthoritySnapshot(run=run_row, finalize_evidence=(ev,),
                                     artifacts={str(loc): data})


SCRATCH.mkdir(parents=True, exist_ok=True)
root = Path(tempfile.mkdtemp(dir=SCRATCH, prefix="t10-"))
env = data_root(root)
store = seed(env)
wt, head = git_worktree(root)
runner_path = runner(root)
rcpt = receipts(root)

# B1 — exit 1 with PASS text is DENY.
store.admit(record(cycle="cyc-1", action="act-1", holder="cyc-1",
                   resource="browser:chromium"))
hold(env, cycle="cyc-1", resource="browser:chromium", holder="cyc-1")
d = dispatch(env, wt, rcpt, "RUN-0001", cycle="cyc-1", action="act-1",
             sha=head, exit_code=1, runner_path=runner_path)
check("B1 producer exit", d.returncode, 0)
r1 = BOUNDED.read_bounded_receipt(rcpt, "act-1")
check("B1 receipt exit code", r1.receipt["process_exit_code"], 1)
check("B1 receipt status", r1.receipt["status"], "EXITED")
check("B1 child PASS text is present but not authoritative",
      b"ALL TESTS PASSED" in r1.stdout_bytes, True)
b1 = binding(cycle="cyc-1", action="act-1", run="RUN-0001", sha=head)
check("B1 stdin is the canonical binding", r1.stdin_bytes,
      canonical(b1).encode())
dec1 = BOUNDED.evaluate_dispatch(
    expected(b1, runner_path=runner_path, wt=wt, exit_code=1),
    admission(store, "cyc-1"), grant_snapshot(store), r1,
    work_authority(root, run="RUN-0001", sha=head, exit_code=1, verdict="FAIL"),
    untrusted_child_report="ALL TESTS PASSED - SUCCESS")
check("B1 decision is DENY", dec1.decision, BOUNDED.DECISION_DENY, security=True)
check("B1 reason is PROCESS_EXIT_NONZERO",
      "PROCESS_EXIT_NONZERO" in dec1.reasons, True)
check("B1 no QA signature", (dec1.parent_accepted, dec1.qa_signature), (False, False))

# B2 — exit 0 is at most ELIGIBLE_FOR_QA; T10 remains BLOCKED_WIRING.
store.admit(record(cycle="cyc-2", action="act-2", holder="cyc-2",
                   resource="browser:firefox"))
hold(env, cycle="cyc-2", resource="browser:firefox", holder="cyc-2")
d = dispatch(env, wt, rcpt, "RUN-0002", cycle="cyc-2", action="act-2",
             sha=head, exit_code=0, runner_path=runner_path)
check("B2 producer exit", d.returncode, 0)
r2 = BOUNDED.read_bounded_receipt(rcpt, "act-2")
b2 = binding(cycle="cyc-2", action="act-2", run="RUN-0002", sha=head)
dec2 = BOUNDED.evaluate_dispatch(
    expected(b2, runner_path=runner_path, wt=wt, exit_code=0),
    admission(store, "cyc-2"), grant_snapshot(store), r2,
    work_authority(root, run="RUN-0002", sha=head, exit_code=0, verdict="PASS"),
    untrusted_child_report="ALL TESTS PASSED - SUCCESS")
check("B2 decision is ELIGIBLE", dec2.decision, BOUNDED.DECISION_ELIGIBLE)
check("B2 eligible for QA only", dec2.eligible_for_qa_review, True)
check("B2 not parent-accepted / not signed",
      (dec2.parent_accepted, dec2.qa_signature), (False, False))
check("B2 t10_status stays BLOCKED_WIRING", dec2.t10_status, "BLOCKED_WIRING")
check("B2 WE consumption is NOT_WIRED", dec2.work_engine_consumption, "NOT_WIRED")

# B8 — a forged work run in the expected binding is DENIED.
forged_run = dict(b2, work_run_id="RUN-9999")
dec8 = BOUNDED.evaluate_dispatch(
    expected(forged_run, runner_path=runner_path, wt=wt, exit_code=0),
    admission(store, "cyc-2"), grant_snapshot(store), r2,
    work_authority(root, run="RUN-0002", sha=head, exit_code=0, verdict="PASS"),
    untrusted_child_report="ALL TESTS PASSED - SUCCESS")
check("B8 forged work run is DENIED", dec8.decision, BOUNDED.DECISION_DENY,
      security=True)
check("B8 reason is input digest mismatch",
      "INPUT_DIGEST_MISMATCH" in dec8.reasons, True)

# B9 — a forged principal is DENIED; there is no --t10-principal flag.
forged_principal = dict(b2, principal="agent:EVIL")
dec9 = BOUNDED.evaluate_dispatch(
    expected(forged_principal, runner_path=runner_path, wt=wt, exit_code=0),
    admission(store, "cyc-2"), grant_snapshot(store), r2,
    work_authority(root, run="RUN-0002", sha=head, exit_code=0, verdict="PASS"),
    untrusted_child_report="ALL TESTS PASSED - SUCCESS")
check("B9 forged principal is DENIED", dec9.decision, BOUNDED.DECISION_DENY,
      security=True)
store.admit(record(cycle="cyc-8", action="act-8", holder="cyc-8",
                   resource="browser:brave"))
hold(env, cycle="cyc-8", resource="browser:brave", holder="cyc-8")
cli_src = (TREE / "runtime" / "cli.py").read_text(encoding="utf-8")
check("B9 parser has no --t10-principal flag", '"--t10-principal"' in cli_src, False)
check("B9 producer binds principal from the grant",
      'grant.get("delegated_by")' in cli_src, True)
bad = dispatch(env, wt, rcpt, "RUN-0008", cycle="cyc-8", action="act-8",
               sha=head, exit_code=0, runner_path=runner_path,
               extra=("--t10-principal", "agent:EVIL"))
if bad.returncode == 0:
    r8 = BOUNDED.read_bounded_receipt(rcpt, "act-8")
    stdin8 = json.loads(r8.stdin_bytes) if r8 and r8.stdin_bytes else {}
    check("B9 supplied flag cannot change the binding principal",
          stdin8.get("principal"), CONTEXT, security=True)
else:
    check("B9 unknown principal flag refused", bad.returncode != 0, True)

# B3 — incomplete T10 flags refuse before spawn, no receipt.
store.admit(record(cycle="cyc-3", action="act-3", holder="cyc-3",
                   resource="browser:webkit"))
hold(env, cycle="cyc-3", resource="browser:webkit", holder="cyc-3")
d3 = dispatch(env, wt, rcpt, "RUN-0003", cycle="cyc-3", action="act-3",
              sha=head, exit_code=0, t10=False,
              extra=("--t10-work-item", ITEM), runner_path=runner_path)
check("B3 incomplete flags refused", d3.returncode != 0, True)
check("B3 says Nothing was run", "Nothing was run" in d3.stderr, True)
check("B3 no receipt written", (rcpt / "act-3.json").exists(), False)

# B4 — a SHA that is not HEAD refuses before spawn.
store.admit(record(cycle="cyc-4", action="act-4", holder="cyc-4",
                   resource="browser:edge"))
hold(env, cycle="cyc-4", resource="browser:edge", holder="cyc-4")
d4 = dispatch(env, wt, rcpt, "RUN-0004", cycle="cyc-4", action="act-4",
              sha="f" * 40, exit_code=0, runner_path=runner_path)
check("B4 wrong SHA refused", d4.returncode != 0, True)
check("B4 says not the worktree HEAD", "not the worktree HEAD" in d4.stderr, True)
check("B4 no receipt written", (rcpt / "act-4.json").exists(), False)

# B6 — non-T10 dispatch is BLOCKED_WIRING, never ELIGIBLE.
store.admit(record(cycle="cyc-6", action="act-6", holder="cyc-6",
                   resource="browser:safari"))
hold(env, cycle="cyc-6", resource="browser:safari", holder="cyc-6")
cmd = ["/bin/sh", "-c", "printf 'ALL TESTS PASSED\\n'; exit 0"]
d6 = dispatch(env, wt, rcpt, "RUN-0006", cycle="cyc-6", action="act-6",
              sha=head, exit_code=0, t10=False, command=cmd)
check("B6 non-T10 dispatch ran", d6.returncode, 0)
r6 = BOUNDED.read_bounded_receipt(rcpt, "act-6")
non_t10 = BOUNDED.ExpectedDispatch(
    cycle_ref="cyc-6", action_key="act-6", work_item_id=ITEM,
    work_run_id="RUN-0006", commit_sha=head, principal=CONTEXT,
    grant_ref=GRANT, runner_identity=RUNNER, argv=tuple(cmd), cwd=str(wt),
    input_text="", readonly=False, timeout=BUDGET["wall_seconds"],
    max_output_bytes=BUDGET["max_output_bytes"], env={},
    finalize_command=FINALIZE_COMMAND, selection_digest=DIGEST)
dec6 = BOUNDED.evaluate_dispatch(
    non_t10, admission(store, "cyc-6"), grant_snapshot(store), r6,
    work_authority(root, run="RUN-0006", sha=head, exit_code=0, verdict="PASS"),
    untrusted_child_report="ALL TESTS PASSED")
check("B6 non-T10 is BLOCKED_WIRING", dec6.decision,
      BOUNDED.DECISION_BLOCKED_WIRING, security=True)

# B11 — a dirty tracked worktree refuses before spawn.
wt2, head2 = git_worktree(root / "dirty")
store.admit(record(cycle="cyc-7", action="act-7", holder="cyc-7",
                   resource="browser:opera"))
hold(env, cycle="cyc-7", resource="browser:opera", holder="cyc-7")
(wt2 / "README").write_text("dirty tracked change\n", encoding="utf-8")
d11 = dispatch(env, wt2, rcpt, "RUN-0007", cycle="cyc-7", action="act-7",
               sha=head2, exit_code=0, runner_path=runner_path)
check("B11 dirty tree refused", d11.returncode != 0, True)
check("B11 says clean tracked worktree", "clean tracked worktree" in d11.stderr, True)

# B7 — tampered receipt bytes are DENIED by the anchor.
before = (rcpt / "act-2.json").read_bytes()
(rcpt / "act-2.json").write_bytes(before + b" ")
r2t = BOUNDED.read_bounded_receipt(rcpt, "act-2")
dec7 = BOUNDED.evaluate_dispatch(
    expected(b2, runner_path=runner_path, wt=wt, exit_code=0),
    admission(store, "cyc-2"), grant_snapshot(store), r2t,
    work_authority(root, run="RUN-0002", sha=head, exit_code=0, verdict="PASS"),
    untrusted_child_report="ALL TESTS PASSED - SUCCESS")
check("B7 tampered receipt is DENIED", dec7.decision, BOUNDED.DECISION_DENY,
      security=True)
check("B7 reason is RECEIPT_ANCHOR_MISMATCH",
      "RECEIPT_ANCHOR_MISMATCH" in dec7.reasons, True)

# B5 — a revoked grant refuses before spawn (last: it revokes the shared grant).
store.admit(record(cycle="cyc-5", action="act-5", holder="cyc-5",
                   resource="browser:webkit2"))
hold(env, cycle="cyc-5", resource="browser:webkit2", holder="cyc-5")
store.revoke_grant(GRANT, at=now(), reason="t10 producer refusal")
d5 = dispatch(env, wt, rcpt, "RUN-0005", cycle="cyc-5", action="act-5",
              sha=head, exit_code=0, runner_path=runner_path)
check("B5 revoked grant refused", d5.returncode != 0, True)
check("B5 says Nothing was run", "Nothing was run" in d5.stderr, True)
check("B5 no receipt written", (rcpt / "act-5.json").exists(), False)

# B10 — source: Work Engine finalize cannot consume the proof.
we_hits = []
for p in Path(os.environ["WE_TREE"]).rglob("*.py"):
    text = p.read_text(encoding="utf-8", errors="ignore")
    if any(token in text for token in
           ("receipt_sha256", "SERVEROS_DISPATCH_BINDING", "t10_binding")):
        we_hits.append(p.name)
check("B10 Work Engine does not consume the T10 proof", we_hits, [])
check("B10 decision keeps BLOCKED_WIRING/NOT_WIRED",
      (dec2.t10_status, dec2.work_engine_consumption),
      ("BLOCKED_WIRING", "NOT_WIRED"))

print()
print("root:", root)
if failures:
    print(f"PROBE FAILED: {len(failures)} check(s): {failures}")
    sys.exit(1)
print("PROBE OK: every T10 check held")
