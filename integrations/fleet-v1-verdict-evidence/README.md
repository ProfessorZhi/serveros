# Fleet V1 verdict/evidence seam — #237 T10

**Status:** `SOURCE / OFFLINE` plus `LOCAL / PREVIEW_ONLY` authority and
finalization gates. The admitted `work dispatch` producer and the continuation
engineering executor record/reconcile the canonical T10 binding and a
`receipt_sha256` anchor. A PREVIEW-only `RunnerReceiptAuthority` proves a real
local `invoke_once` receipt can serve Work Engine #15 `finalize_run` — and that
a child printing `ALL TESTS PASSED` while exiting 1 cannot conclude success.
The new `finalization_result.py` gate refuses a downstream `runs.state ==
SUCCEEDED` unless the durable finalize evidence is **both** `T10_VERIFIED` and
`t10_acceptable=true` with a verified zero-exit receipt. The existing
`verdict_evidence.py` consumer is hardened for Work Engine #15's labelled
`finalize.json`; a legacy `UNSIGNED` `state=SUCCEEDED` is `DENY`. V1 acceptance
is now a **single strict path** (`v1_acceptance.py`); the legacy receipt-vs-
prose compatibility mode is isolated (`v1_acceptable=False`) and a
`v1_required=True` call refuses (`V1_REQUIRES_STRICT_PATH`).
**Production is still blocked:** there is no production Work Engine finalize
call site, no OS-attested cross-process parent/verifier boundary, and no
independent QA signature. Continuation production also remains
`BLOCKED_ORIGINAL_OWNER` because the wake/checkpoint does not create or attach
the Work Engine Run + `execution_contexts` row the binding requires.
**T10 production closure:** **BLOCKED_WIRING / BLOCKED_ORIGINAL_OWNER /
BLOCKED_TRUST_SOURCE — not closed** (`work_engine_consumption=NOT_WIRED`).
**May this sign a V1 QA verdict?** **No.** Every decision returns
`qa_signature=False`, `parent_accepted=False`, `requires_independent_qa=True`.

Canonical requirement: [serveros #237](https://github.com/ProfessorZhi/serveros/issues/237),
row T10:

> Child self-reports success while tests fail → Parent not ACCEPTED.

This package answers that row without creating a second Work Engine, a second
Board, a ledger, a grant store or a daemon. It is a pure decision surface over
read-only records that already exist.

## 1. The evidence chain

Three existing owners, joined by the receipt's `action_key` and the dispatch
binding:

| Fact | Existing owner | Source |
| --- | --- | --- |
| Run identity, runner identity, state, artifact directory | Work Engine `runs` | `workengine/store.py::begin_run` |
| Final verdict, command, exit code, revision, dirty, artifact digest | Work Engine `evidence` | `workengine/store.py::finalize_run`; `record_evidence` |
| Which Run may be concluded | Work Engine | `advance_run` refuses `SUCCEEDED` as a bare transition |
| Dispatch identity, grant, reservation, lease, selection, admitted time | Agent Control `admissions` | `runtime/agent_control_store.py::admit` |
| The DISPATCHED phase evidence | Agent Control `confirmations["DISPATCHED"]` | `confirm_admission`; `runtime/cli.py::_dispatch_answer` |
| The runner's actual process exit and stdout/stderr digests | `runtime/bounded_invocation.invoke_once` | receipt `<key>.json` + `<key>.stdin/.stdout/.stderr` |

The decisive Work Engine property is:

```python
state = 'SUCCEEDED' if verdict == 'PASS' and exit_code == 0 else 'FAILED'
```

The decisive bounded-invocation property is that `process_exit_code` is recorded
from `proc.returncode`, never from the child's text. The receipt's
`input_digest` is:

```python
sha256(json.dumps({
    "argv": argv,
    "cwd": str(cwd.resolve()),
    "input": input_text,
    "readonly": readonly,
    "timeout": timeout,
    "max_output_bytes": max_output_bytes,
    "env": env or {},
}, sort_keys=True).encode()).hexdigest()
```

## 2. Two gates, one authority chain

### 2.1 `verdict_evidence.py` — receipt versus prose

`evaluate_acceptance(expected, authority, untrusted_child_report=...)` reads a
Work Engine `runs` row and its `kind='finalize'` evidence row, and decides
`DENY / UNKNOWN / ELIGIBLE`. It checks the exact Item/Run, exact 40-hex SHA,
command, runner identity (`runs.worker`), terminal state, exit code 0, `PASS`,
clean worktree, locator containment, artifact digest and artifact/row agreement.
The child's report is never parsed.

`ELIGIBLE` is eligibility for independent QA review only. It is not a Parent
acceptance and never a QA signature.

### 2.2 `bounded_provenance.py` — runner process versus caller-supplied exit code

`evaluate_dispatch(expected, admission, grant, receipt, work_authority, ...)`
reads the real `<action_key>.json/.stdin/.stdout/.stderr` files and:

1. recomputes the receipt's `input_digest` from the expected dispatch spec
   (argv, cwd, stdin, readonly, timeout, max_output_bytes, env);
2. recomputes `sha256(<key>.stdout)` and `sha256(<key>.stderr)` and compares
   them to the receipt's `output_sha256`;
3. requires the receipt's `process_exit_code` to be an integer, and `0` for
   eligibility — a child's `"ALL TESTS PASSED"` on stdout is hash material, not
   a verdict;
4. parses the T10 dispatch binding from `<key>.stdin` and requires the exact
   `cycle_ref / action_key / work_item / work_run / commit_SHA / principal /
   grant_ref / runner_identity`;
5. requires the Agent Control admission to agree on cycle, action key, grant,
   non-retirement, `DISPATCHED` phase, reservation bounds, selection item and
   the recorded DISPATCHED evidence;
6. requires the grant to be unrevoked and to cover the receipt's execution
   window (`valid_from <= started_at`, `finished_at < expires_at`);
7. requires the Work Engine Run/evidence to agree on the same Item/Run, exact
   SHA, command, runner identity and exit code;
8. requires the recorded `receipt_sha256` anchor to equal the anchor of the
   receipt and artifact bytes.

A nonzero receipt exit is `DENY` even when the child's text says `PASS` and even
when a forged Work Engine row says exit 0 (`PROCESS_EXIT_NONZERO`,
`EXIT_CODE_DISAGREEMENT`).

## 3. Producer wiring: what is now real, and what is still BLOCKED_WIRING

The admitted `work dispatch` path is now wired. When the four T10 flags are
supplied, `runtime/cli.py`:

* requires all four `--t10-work-item / --t10-work-run / --t10-commit-sha /
  --t10-runner-identity` flags and refuses a partial binding before spawning;
* takes `cycle_ref`, `action_key` and `grant_ref` from the admission, the
  principal from `grant.delegated_by`, and the Item from the admission's
  selection;
* requires `--t10-commit-sha` to equal the `--cwd` worktree's HEAD and the
  tracked tree to be clean;
* writes the canonical binding as the dispatch stdin and as
  `env["SERVEROS_DISPATCH_BINDING"]`, so it is inside `invoke_once`'s
  `input_digest`;
* records `receipt_sha256` (receipt + stdin/stdout/stderr bytes) in the
  DISPATCHED evidence through the fields the store already accepts.

The real end-to-end fixture is:

```bash
python -m pytest tests/test_fleet_v1_t10_producer_binding.py -q
```

It drives the real CLI, the real `runtime/bounded_invocation.invoke_once`, and a
real offline runner. The runner prints `ALL TESTS PASSED` and exits 1; the
producer records exit 1 and the consumer returns `DENY / PROCESS_EXIT_NONZERO`.
The exit-0 case is `ELIGIBLE` for independent QA review only.

What is **still** blocked:

* **Non-T10 dispatch** keeps the legacy stdin/env and therefore answers
  `BLOCKED_WIRING` (`MISSING_DISPATCH_BINDING`). The old fixture
  `test_current_production_receipt_is_blocked_wiring` still pins that.
* **`continuation.execute` engineering** cannot be wired from
  `mcp/continuation_mcp.py` alone: its `invoke_once` call is inside
  `runtime/bounded_executor.execute`, which accepts only `key/argv/receipts`,
  and the wake envelope carries `work_item_ref`, `grant_ref` and
  `delegation_ref` but not `work_run_id`, `commit_sha` or `runner_identity`.
  This is `BLOCKED_ORIGINAL_OWNER`; the exact patch is in §4.5.
* **Work Engine finalization does not consume the producer proof.** Even the
  local producer/consumer pair that reaches `ELIGIBLE` reports
  `t10_status=BLOCKED_WIRING` and `work_engine_consumption=NOT_WIRED`. A real
  `finalize_run` that refuses an absent/contradictory linked receipt is the
  remaining owner change in §4.6.

## 4. Producer contract and exact owner patch

### 4.1 Canonical dispatch binding

The trusted dispatcher (never the child) must put this JSON into the dispatch's
stdin and `env["SERVEROS_DISPATCH_BINDING"]`:

```json
{
  "action_key": "...",
  "commit_sha": "<exact full 40-hex git SHA>",
  "cycle_ref": "...",
  "grant_ref": "...",
  "principal": "...",
  "runner_identity": "...",
  "work_item_id": "...",
  "work_run_id": "..."
}
```

with `json.dumps(payload, sort_keys=True, separators=(",", ":"))`. Extra keys
are allowed; the eight above are required. The binding must come from the
Parent's accepted scope and the live grant/envelope, not from the child's
report. The adapter compares it to `ExpectedDispatch` and to the admission,
grant and Work Engine rows.

### 4.2 Receipt anchor

The producer must compute this exact value from the files it just read and
store it as `admission["confirmations"]["DISPATCHED"]["receipt_sha256"]`:

```python
digest = hashlib.sha256()
for label, data in (
    ("receipt", receipt_bytes),
    ("stdin", stdin_bytes),
    ("stdout", stdout_bytes),
    ("stderr", stderr_bytes),
):
    digest.update(label.encode("utf-8"))
    digest.update(b"\x00")
    digest.update(data)
    digest.update(b"\x00")
anchor = digest.hexdigest()
```

This is the only value that detects a receipt edited after the phase was
confirmed. Without it, a forger who rewrites `process_exit_code` and the
artifacts consistently cannot be distinguished from an honest runner.

### 4.3 `runtime/cli.py::_dispatch_answer` — implemented in this milestone

`_receipt_sha256(path, key)` computes the anchor above from the receipt file and
the three sibling artifacts; `_dispatch_answer` records it beside the three
fields the phase already compares on:

```python
return {"evidence": {
    "action_key": receipt.get("action_key"),
    "status": receipt.get("status"),
    "input_digest": receipt.get("input_digest"),
    "receipt_sha256": _receipt_sha256(path, admission["action_key"]),
    "store": str(Path(receipts_root)),
    "receipt": str(path),
}, "receipt": receipt}
```

It returns `None` when any artifact is missing or is a symlink: an anchor over
invented bytes is worse than no anchor. The existing store accepts the extra
key without a schema change.

### 4.4 `runtime/agent_control_store.py::dispatch_evidence_conflict` — still an owner patch

Store-side validation of the anchor is **not applied** in this milestone
(`agent_control_store.py` is outside the authorized file set and the S1 owner is
concurrently working there). The store records the extra key as-is; the
consumer enforces it. The stronger store-side check would be:

When `receipt_sha256` is present, refuse anything that is not a 64-hex digest.
Keep the field optional at the store layer so existing admissions remain
readable; the consumer requires it for T10 eligibility:

```python
anchor = evidence.get("receipt_sha256")
if anchor is not None and (
        not isinstance(anchor, str)
        or not re.fullmatch(r"[0-9a-fA-F]{64}", anchor)):
    return ("the DISPATCHED evidence carries a receipt_sha256 that is not a "
            "64-hex digest; a value a consumer cannot check is not an anchor")
```

### 4.5 Dispatchers

#### `runtime/cli.py::cmd_work_dispatch` — implemented in this milestone

The four new flags are opt-in: no `--t10-*` flag keeps the legacy dispatch
unchanged (and therefore `BLOCKED_WIRING` for T10). Supplying any flag requires
all four and builds:

```python
binding = {
    "cycle_ref": admission["cycle_ref"],
    "action_key": admission["action_key"],
    "work_item_id": args.t10_work_item,       # must be in admission.selected
    "work_run_id": args.t10_work_run,         # Parent accepted scope
    "commit_sha": args.t10_commit_sha,        # must equal `git rev-parse HEAD`
    "principal": grant["delegated_by"],       # never a flag
    "grant_ref": admission["grant_ref"],
    "runner_identity": args.t10_runner_identity,
}
canonical = json.dumps(binding, sort_keys=True, separators=(",", ":"))
invoke_once(..., input_text=canonical,
            env={"SERVEROS_DISPATCH_BINDING": canonical})
```

The commit SHA is checked against the real worktree HEAD and the tracked tree
must be clean; the Item must be one the admission selected; a partial binding or
`--input-file` alongside it refuses before the spawn. The producer never takes
`work_run_id` or `runner_identity` from the child's argv/env; they come from the
operator's accepted-scope flags.

#### `mcp/continuation_mcp.py` engineering path — BLOCKED_ORIGINAL_OWNER

`continuation.execute` does not call `invoke_once` directly. It calls
`runtime/bounded_executor.execute`, which accepts only `key`, `argv` and
`receipts` and constructs its own `input_text=json.dumps(config)`. The wake
envelope carries `work_item_ref`, `grant_ref` and `delegation_ref`, but not
`work_run_id`, `commit_sha` or `runner_identity`. Binding from
`continuation_mcp.py` alone would therefore require guessing three trust
operands or changing a file outside this milestone's ownership. It is not
faked. The exact owner patch is:

1. `runtime/wake.py::capability_envelope` — carry `work_run_ref`, the exact
   `commit_sha` and `runner_identity` from the trusted wake record.
2. `runtime/bounded_executor.execute` — accept an optional trusted `binding`
   and pass it into `invoke_once` as `input_text` plus
   `env["SERVEROS_DISPATCH_BINDING"]`.
3. `mcp/continuation_mcp.py::execute` — pass the envelope-derived binding to
   `executor.execute`; refuse if any of the three is absent (fail closed).

The control-verb `_mutate` path is a different surface: it dispatches
`serverosctl work …`, not a test runner, and its envelope does not carry a Work
Engine Run id or SHA. It is not test evidence and is not admitted by this gate.

### 4.6 Work Engine (owner patch D, optional for this consumer)

This consumer seam reconciles the Work Engine row with the receipt; it does not
require a Work Engine schema change. For store-side enforcement (the stronger
guarantee), the Work Engine owner can add a `runner_receipt` link to the
finalize evidence — `action_key`, `input_digest`, `receipt_sha256`,
`process_exit_code` — and refuse `finalize_run` when the linked receipt is
absent, replayed with different input, or contradicts the requested verdict.
That is a Work Engine API/schema change and is not made by this PR.

### 4.7 Continuation engineering — implemented, then BLOCKED_ORIGINAL_OWNER

`continuation.execute` now builds the same canonical binding in the trusted
executor, `runtime/bounded_executor.py`:

* `state_db` is `<state>/serveros.db`, read **read-only** with the same
  `mode=ro` connection `context/resolver.py` already uses;
* `runs` must contain exactly one non-terminal Run for the envelope's
  `work_item_ref`; zero means there is no canonical Run, more than one means the
  binding would have to choose, and both refuse before the receipt lock or any
  spawn;
* that Run's `activity_id` must have exactly one `execution_contexts` row whose
  `worktree` resolves to the granted engineering worktree;
* its `revision` must be an exact full 40-hex SHA equal to the worktree's real
  `HEAD`; `dirty` must be 0 and the tracked tree must be clean;
* `principal` is `grant.delegated_by`, and the envelope's `delegation_ref` must
  equal it; the envelope's `checkpoint_ref`, `work_item_ref` and `grant_ref`
  are the other trusted refs;
* `work_run_id` and `runner_identity` are that Run's `run_id` and `worker`;
* the eight-field binding is added to the config that becomes `invoke_once`'s
  stdin, so it is inside `input_digest`;
* `receipt_sha256` over the receipt and its stdin/stdout/stderr is written into
  the existing private `<action>.container` report — no new ledger.

`runtime/wake.py` is deliberately unchanged. Its existing envelope fields
(`checkpoint_ref`, `work_item_ref`, `grant_ref`, `delegation_ref`) are the
trusted references; the Run id, exact SHA and runner identity are Work Engine
store facts, not envelope fields. Adding caller-supplied versions of them to
the envelope would be the false authority this milestone refuses.

`mcp/continuation_mcp.py` passes the state DB path from `Paths()` and adds no
new caller/child argument. `evaluate_continuation` in `bounded_provenance.py`
reconciles the receipt, binding, envelope/grant/checkpoint, Work Run and
execution_context. A real exit 1 with `ALL TESTS PASSED` is
`DENY / PROCESS_EXIT_NONZERO`; exit 0 is `ELIGIBLE` for independent QA only,
always with `t10_status=BLOCKED_WIRING` and
`work_engine_consumption=NOT_WIRED`.

**Refusals before spawn** (no receipt, no private directory): Work Engine store
missing/unreadable; no open Run; more than one open Run; no/ambiguous
execution_context at the granted worktree; revision not 40-hex; revision not
the worktree HEAD; dirty tracked tree; revoked/expired grant; envelope
`delegation_ref` not the grant's `delegated_by`; missing envelope refs.

**Remaining original-owner blocker.** The current wake/checkpoint path does not
create or attach a Work Engine Run + `execution_contexts` row. `runtime/wake.py`
plans from the checkpoint/grant and `context/resolver.py::_runs` only reads the
store; nothing produces the row this binding requires. Until an owner adds
"begin/attach the Run (exact revision, worktree, dirty=0) before
`continuation.execute`" — or an equivalent recorded step — every real
continuation engineering execution refuses. This is fail-closed by design, not
a green T10. The optional Work Engine store-side link in §4.6 is the other
remaining owner change.

### 4.8 `RunnerReceiptAuthority` preview bridge — LOCAL / PREVIEW_ONLY

`authority_bridge.LocalRunnerReceiptAuthority` is the T10 side of Work Engine
#14 (`e4c4a2b`) `RunnerReceiptAuthority.verify(receipt_ref, *, item_ref,
run_ref, now)`. It is not imported by a production path.

The trusted parent supplies, in-process:

* the `ExpectedContinuation` (exact dispatch spec plus the eight binding
  fields), so `input_digest` can be recomputed;
* the `ContinuationAuthority` readback (wake envelope, grant/checkpoint, unique
  open Work Engine Run, execution_context revision/worktree/dirty, `runs.worker`);
* an **opaque** `receipt_ref` minted for the admitted action — never a path, a
  hash, a caller JSON object or child text;
* the `receipt_sha256` anchor it recalled from the `invoke_once` result;
* the Work Engine `VerifiedRunReceipt` class and `ReceiptError` class (so this
  module does not import the other repository).

`verify` resolves the opaque ref under `expected.cwd` (the private receipts
directory) and `expected.action_key`, then:

1. requires the exact Item/Run, unrevoked grant, grant window and wake-envelope
   expiry at `now`;
2. requires the unique Run, its `worker`, the exact execution_context
   revision, the granted worktree and `dirty=0`;
3. requires the grant's `delegated_by` and the envelope's `delegation_ref` to
   be the expected principal;
4. reads the real receipt with the safe reader (no symlinks, private 0700 dir)
   and recomputes `input_digest`, `receipt_sha256` over receipt + stdin/stdout/
   stderr, and both `output_sha256` values;
5. parses the eight-field binding from the receipt's stdin and compares every
   field to the parent's expectation (`RECEIPT_ITEM_MISMATCH`,
   `RECEIPT_RUN_MISMATCH`, `STALE_SHA`, `CROSS_PRINCIPAL`, ...);
6. returns a `VerifiedRunReceipt` whose `verdict` is `PASS` iff
   `process_exit_code == 0`. The child's `ALL TESTS PASSED` text is hashed,
   never parsed.

The constructor refuses `BLOCKED_TRUST_SOURCE` and names the missing operand if
the parent cannot supply the receipt anchor, the Work Engine
Run/execution_context snapshot, the opaque ref, the factory, or if the grant/
envelope window is unreadable.

`STATUS = "PREVIEW_ONLY"` and `PRODUCTION_STATUS = "BLOCKED_ORIGINAL_OWNER"`.
The remaining original-owner API is explicit:

1. **A production finalize call site.** Work Engine #14 exposes
   `finalize_run_verified` only as an importable store method; it registers no
   CLI verb or MCP tool (`tests/test_runner_receipt.py` asserts the absence).
   ServerOS has no production caller. The preview test calls the store method
   directly.
2. **An OS-attested cross-process parent.** The preview bridge is constructed
   in-process by the trusted parent that ran `invoke_once`. A production
   boundary needs a trusted daemon (serverosd or equivalent) that owns the
   private receipts directory, resolves the opaque ref itself, and calls the
   finalize API through an authorized path. A `receipt_ref` supplied by a child
   or a caller path is refused; there is currently no such production caller.
3. **Independent QA.** The verified receipt is eligibility for review, not a QA
   signature. No independent signature exists.

The legacy `finalize_run` is not switched and no permission is elevated. This
is a provenance proof, not a production acceptance.

### 4.9 Signed finalization-result gate (P0)

`finalization_result.py` is the strict downstream gate for a Work Engine
finalization result:

* `read_work_engine_finalization(state_db, item_ref, run_ref)` opens
  `serveros.db` read-only, reads the exact `runs` row and the last
  `kind='finalize'` evidence row, and requires the evidence locator to be the
  Run's own `<artifact_dir>/finalize.json` (no caller path, no symlink). It
  re-hashes the file and compares it to the evidence row's `sha256`.
* `evaluate_signed_finalization(expected, readback)` requires **both**
  `authorization == "T10_VERIFIED"` and `t10_acceptable == true` in the durable
  readback, `run_state == SUCCEEDED`, the evidence bytes still matching the
  evidence row, and the exact Item / Run / commit SHA / actor / action /
  `input_digest` / `receipt_sha256` / `runner_identity` / `process_exit_code ==
  0` in the `runner_receipt`. The expected binding is the parent-held #269
  offline receipt and Work Run facts.
* A caller JSON label (`{"authorization": "T10_VERIFIED", ...}`) is `DENY`;
  an absent/unreadable authoritative readback is `BLOCKED_TRUST_SOURCE`; a
  tampered finalize file, `UNSIGNED`, missing `t10_acceptable`, a weaker label,
  an inconsistent Item/Run/commit/actor, a missing/contradictory receipt block,
  a non-zero process exit, or a `SUCCEEDED` state with no finalize evidence is
  `DENY`.
* `ELIGIBLE` is for independent QA only: `parent_accepted=False`,
  `qa_signature=False`, `requires_independent_qa=True`,
  `production_status="BLOCKED_TRUST_SOURCE"`, `evidence_level="LOCAL_HERMETIC"`.

The existing lower-level `verdict_evidence.evaluate_acceptance` consumer is
hardened too: when a finalize artifact carries Work Engine #15's
`authorization` label, `UNSIGNED` / a weaker label / a `T10_VERIFIED` label
without a matching `runner_receipt`, non-zero process exit, or mismatched
commit/actor/runner is `DENY`. Pre-#15 fixtures with no `authorization` key
remain compatible with the historical receipt-vs-prose tests; the strict
`finalization_result` gate is the current finalization-result path.

**What is still blocked.** Work Engine #15 leaves the production
`RunnerReceiptAuthority` / OS-attested parent unbound
(`BLOCKED_TRUST_SOURCE`). The positive test uses the exact Work Engine #15
source with a clearly labelled **test-double** wrapper around the #269 PREVIEW
receipt verifier and a real offline `invoke_once` receipt; it is LOCAL/HERMETIC,
not production. No production finalize call site consumes the gate, the legacy
`finalize_run` is not switched, no independent QA signature exists, and no
second ledger/signature is created.

### 4.10 V1 strict acceptance and legacy isolation (P1)

`verdict_evidence.evaluate_acceptance` is a **LEGACY / UNSIGNED compatibility**
gate, not the V1 acceptance path. It exists so pre-#15 fixtures with no
`authorization` marker keep exercising the historical receipt-vs-prose
behaviour. Every decision it returns now carries `v1_acceptable=False` and
`v1_mode="LEGACY_COMPATIBILITY"`; a `v1_required=True` call adds
`V1_REQUIRES_STRICT_PATH` and is `DENY` (`v1_mode="V1_REFUSED_LEGACY_PATH"`).
There is no permissive V1 legacy override.

`v1_acceptance.evaluate_v1_acceptance` is the **single V1 positive path**. It
composes:

1. `finalization_result.evaluate_signed_finalization` over the authoritative
   Work Engine exact Run and durable `finalize.json` (`T10_VERIFIED` +
   `t10_acceptable=true` + a matching `runner_receipt` with a verified exit 0);
2. a grant/epoch freshness **answer from a trusted parent**. This consumer does
   not compute grant freshness itself and never fabricates it:
   - no parent → `BLOCKED_WIRING` (`NO_TRUSTED_PARENT_FOR_GRANT_EPOCH`);
   - `verified` not true / `fresh` not true / `epoch` missing or non-positive →
     `BLOCKED_WIRING` (`GRANT_FRESHNESS_UNKNOWN`, `STALE_GRANT_UNKNOWN`,
     `GRANT_EPOCH_UNKNOWN`);
   - revoked → `DENY`; wrong grant/principal → `DENY`;
   - a caller JSON object instead of a `GrantFreshnessSnapshot` → `DENY`
     (`CALLER_JSON_NOT_AUTHORITY`).

A clearly labelled `"test-double:"` parent answer around the real offline
exit-0 receipt is `ELIGIBLE_FOR_QA` only: `eligible_for_qa_review=True`,
`v1_acceptable=False`, `production_status="BLOCKED_TRUST_SOURCE"`,
`parent_accepted=False`, `qa_signature=False`. A `"trusted-parent:"` marker is
not authenticated in this milestone and remains `BLOCKED_WIRING`
(`V1_ACCEPTANCE_NOT_WIRED`); an unknown source is
`BLOCKED_WIRING` (`UNRECOGNIZED_TRUST_SOURCE`). `v1_required=True` is required;
a call with it false is `BLOCKED_WIRING` (`V1_CONTEXT_REQUIRED`).

`bounded_provenance` decisions also carry `v1_acceptable=False` /
`v1_mode="LEGACY_COMPATIBILITY"`: it is a legacy/pre-finalization consumer and
cannot be read as V1 acceptance.

**Remaining blocker.** The production trusted parent / grant-epoch verifier is
still absent (`BLOCKED_TRUST_SOURCE`). The test double is not a production
parent; no production finalize call site consumes the V1 path and no
independent QA signature exists.

## 5. What the gate never does

* It never parses the child's stdout/stderr as a verdict; it hashes them.
* It never treats a supplied JSON `exit_code` as sufficient without the receipt
  and its anchor.
* It never turns `ELIGIBLE` into a Parent acceptance or a QA signature.
* It never writes a Work Engine board, ledger, admission, grant or receipt.
* It never edits Work Engine, `runtime/agent_control_store.py`,
  `runtime/daemon.py`, `runtime/coordination.py`, `context/resolver.py`,
  `fleet/agent_control_router.py`, `fleet_mcp/**`,
  `integrations/fleet-v1-qa/**`, Native, F1, the I1 manifest, credentials,
  providers or another Agent's worktree. The runtime files edited are
  `runtime/cli.py` (admitted `work dispatch` producer),
  `runtime/bounded_executor.py` (continuation binding/executor) and
  `mcp/continuation_mcp.py` (state DB pass-through), under the explicit T10
  grants; `runtime/wake.py` was inspected but not edited. The S1/S2 owners'
  files are untouched. The signed-finalization milestone edits only this
  integration package and its new test; no runtime file is touched. The V1
  strict-acceptance milestone likewise edits only this package and its new
  test; no runtime or Work Engine file is touched.

## 6. Test evidence

Bounded by cgroup v2 (not just a ulimit), plus a 10-minute timeout:

```bash
cd /data/home/vince/workspace/worktrees/serveros/fleet-v1-t10-verdict-evidence-20261018
timeout 620 systemd-run --user --scope \
  -p MemoryMax=1G -p MemorySwapMax=0 -p CPUQuota=25% -- \
  /data/home/vince/toolchains/serveros-test/bin/python -m pytest \
  tests/test_fleet_v1_t10_producer_binding.py -q --no-header -p no:cacheprovider
```

Measured on Tokyo, 2026-10-18: **9 passed in 40.41s**, exit 0, `pswpout` delta 0
(no swap). The same cgroup, on the focused regression
`tests/test_admission.py::TestTheDispatchVerb` plus
`tests/test_fleet_v1_verdict_evidence.py`: **64 passed in 90.60s**, exit 0,
`pswpout` delta 0.

The producer tests drive the real CLI `work dispatch` and the real unmodified
`runtime/bounded_invocation.invoke_once`. The runner prints `ALL TESTS PASSED`
and exits 1 in the denial case; the producer records exit 1, the canonical
binding in stdin/env and `receipt_sha256` in the DISPATCHED evidence; the
consumer returns `DENY / PROCESS_EXIT_NONZERO`. The exit-0 case returns
`ELIGIBLE` for independent QA only, with `t10_status=BLOCKED_WIRING` and
`work_engine_consumption=NOT_WIRED`.

Continuation evidence, same cgroup and `timeout 600`:

```bash
python -m pytest tests/test_fleet_v1_t10_continuation_provenance.py -q
python -m pytest tests/test_bounded_executor.py \
  tests/test_fleet_v1_t10_continuation_provenance.py -q
python -m pytest tests/test_continuation_mcp.py \
  tests/test_fleet_v1_t10_producer_binding.py \
  tests/test_fleet_v1_verdict_evidence.py -q
```

Measured on Tokyo, 2026-10-18: continuation **10 passed in 1.49s**; executor +
continuation **17 passed, 3 skipped in 2.00s**; continuation MCP + prior T10
**106 passed, 1 skipped in 25.10s**; every run exit 0 and `pswpout` delta 0.
The continuation tests seed a real temporary Work Engine SQLite store
(`runs`/`execution_contexts`), a real temporary git worktree and use the real
unmodified `invoke_once`; the Docker worker argv is replaced by a real offline
synthetic runner, so the evidence level is
`LOCAL_OFFLINE_SYNTHETIC`, not REAL_CONTAINER or CLIENT_E2E. The opt-in
`SERVEROS_TEST_DOCKER_IMAGE` tests (3 + 1 skipped) now also need a Work Engine
Run/execution_context; they are an owner follow-up, not a green T10.

Authority bridge, same cgroup and a non-blocking `flock -n`:

```bash
flock -n /tmp/serveros-t10-authority-bridge.lock \
  systemd-run --user --scope -q \
    -p MemoryMax=1G -p MemorySwapMax=0 -p CPUQuota=25% -- \
    timeout 600 python -m pytest \
    tests/test_fleet_v1_t10_authority_bridge.py -q
```

Measured on Tokyo, 2026-10-18: **10 passed in 2.71s**, exit 0, `pswpout` delta
0. The test extracts the exact Work Engine #14 source with
`git -C /data/home/vince/workspace/src/work-engine archive e4c4a2b` into a
temporary directory (read-only; the Work Engine repo is not edited), imports
its real `ServerOSStore.finalize_run_verified`, and uses the real offline
`invoke_once` with a synthetic runner. It covers: exit 1 + `ALL TESTS PASSED`
→ Work Engine refuses success; exact exit 0 → `SUCCEEDED` with the
`runner_receipt` block in `finalize.json`; child text path → refused; missing
trusted context / missing parent anchor → `BLOCKED_TRUST_SOURCE`; stale SHA,
revoked grant, cross-run, cross-principal and checksum tampering → refused.
Evidence level: **LOCAL / PREVIEW_ONLY**, not production.

Signed finalization gate, same cgroup and non-blocking `flock -n`:

```bash
flock -n /tmp/serveros-t10-signed-finalization.lock \
  systemd-run --user --scope -q \
    -p MemoryMax=1G -p MemorySwapMax=0 -p CPUQuota=25% -- \
    timeout 600 python -m pytest \
    tests/test_fleet_v1_t10_signed_finalization.py -q
```

Measured on Tokyo, 2026-10-18: **13 passed in 3.10s**, exit 0, `pswpout` delta
0. Focused T10 regression (signed gate + prior verdict/producer/continuation/
authority-bridge suites): **77 passed in 45.61s**, exit 0, `pswpout` delta 0.

The test extracts the exact Work Engine #15 source (`4bd9e8a`) with
`git archive` (read-only; the Work Engine repo is not edited), uses its real
`ServerOSStore.finalize_run` to write both a legacy `UNSIGNED` `SUCCEEDED`
result and a `T10_VERIFIED`/`t10_acceptable=true` result, and uses the real
#269 offline `invoke_once` receipt through a clearly labelled #15 test-double
verifier. It pins: legacy `SUCCEEDED`/`UNSIGNED` → `DENY`; exact
`T10_VERIFIED` → `ELIGIBLE` for QA only; missing `t10_acceptable`, caller JSON
label, tampered finalize bytes, inconsistent commit/actor/run, non-zero process
exit, weaker authorization, missing receipt anchor/source, and a `SUCCEEDED`
state without finalize evidence → `DENY` or `BLOCKED_TRUST_SOURCE`. The
existing `verdict_evidence` consumer is also tested directly against the same
real #15 evidence: `UNSIGNED` → `DENY`, `T10_VERIFIED` → `ELIGIBLE`.
Evidence level: **LOCAL / HERMETIC**, not production.

V1 strict acceptance / legacy isolation, same cgroup and non-blocking
`flock -n`:

```bash
flock -n /tmp/serveros-t10-v1-strict.lock \
  systemd-run --user --scope -q \
    -p MemoryMax=1G -p MemorySwapMax=0 -p CPUQuota=25% -- \
    timeout 600 python -m pytest \
    tests/test_fleet_v1_t10_legacy_compatibility.py -q
```

Measured on Tokyo, 2026-10-18: **13 passed in 3.20s**, exit 0, `pswpout` delta
0. Focused T10 regression (V1/legacy + prior verdict/producer/continuation/
authority-bridge/signed-finalization suites): **90 passed in 48.99s**, exit 0,
`pswpout` delta 0.

The tests reuse the exact Work Engine #15 source and the #274 real
`finalize_run`/offline `invoke_once` helpers. They pin: pre-#15 compatibility is
ELIGIBLE only in the legacy mode with `v1_acceptable=False`; `v1_required=True`
refuses; no trusted parent → `BLOCKED_WIRING`; a labelled test-double grant
answer + real exit 0 → `ELIGIBLE_FOR_QA` with `v1_acceptable=False`; stale/
unknown/revoked/epoch-less grant, absent authorization marker, legacy
`SUCCEEDED`/`UNSIGNED`, caller JSON fake `T10_VERIFIED`, missing durable
evidence, tampered bytes, cross-grant/actor/run/SHA, and child text PASS with a
real exit 1 → no acceptance. Evidence level: **LOCAL / HERMETIC**, not
production.

The fixture suite covers:

* text-only success without a receipt → `UNKNOWN / MISSING_RECEIPT`;
* real runner exit 1 with `ALL TESTS PASSED` text → `DENY / PROCESS_EXIT_NONZERO`;
* real `work dispatch` exit 1 with `ALL TESTS PASSED` text → `DENY`, and the
  DISPATCHED evidence carries the canonical binding plus `receipt_sha256`;
* real `work dispatch` exit 0 with the exact binding → `ELIGIBLE` for QA review
  only, `t10_status=BLOCKED_WIRING`;
* partial T10 binding, changed SHA, revoked grant → refused **before spawn**
  (`Nothing was run`, no receipt);
* non-T10 dispatch → `BLOCKED_WIRING / MISSING_DISPATCH_BINDING`;
* forged Work Engine exit 0 against a real exit 1 receipt → `DENY / EXIT_CODE_DISAGREEMENT`;
* forged receipt edited to exit 0 after the producer anchor → `DENY / RECEIPT_ANCHOR_MISMATCH`;
* output digest tampering → `DENY / OUTPUT_DIGEST_MISMATCH`;
* stale receipt, expired/revoked grant, cross-principal, cross-run, stale SHA,
  cross-grant, runner mismatch, unrecognized Work Engine runner → `DENY`;
* missing receipt, non-EXITED receipt → `UNKNOWN`;
* a real exact-SHA exit 0 with the full binding and anchor → `ELIGIBLE`, with
  `parent_accepted=False` and `qa_signature=False`.

## 7. Layout

```text
integrations/fleet-v1-verdict-evidence/
├── README.md
├── verdict_evidence.py        # Work Engine receipt vs prose
├── bounded_provenance.py      # bounded-invocation receipt + admission + Work Engine
├── authority_bridge.py        # PREVIEW RunnerReceiptAuthority for WE #14/#15
├── finalization_result.py     # strict signed Work Engine finalization gate
├── v1_acceptance.py           # single strict V1 acceptance path
└── fixtures/
    └── negative_cases.json    # Work Engine negative matrix
tests/test_fleet_v1_verdict_evidence.py       # consumer matrix
tests/test_fleet_v1_t10_producer_binding.py   # real CLI + invoke_once producer
tests/test_fleet_v1_t10_continuation_provenance.py
                                              # wake/grant/checkpoint + Work Run
                                              # + execution_context binding
tests/test_fleet_v1_t10_authority_bridge.py   # real WE #14 finalize_run_verified
                                              # + real offline receipt
tests/test_fleet_v1_t10_signed_finalization.py
                                              # real WE #15 finalize_run
                                              # + durable T10_VERIFIED gate
tests/test_fleet_v1_t10_legacy_compatibility.py
                                              # legacy isolation + strict V1
                                              # acceptance path
```
