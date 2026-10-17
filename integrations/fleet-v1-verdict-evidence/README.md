# Fleet V1 verdict/evidence seam — #237 T10

**Status:** `SOURCE / OFFLINE`. The admitted `work dispatch` producer now
records the canonical T10 binding and a `receipt_sha256` anchor; the consumer
reconciles them with the real runner receipt. The continuation engineering path
and Work Engine consumption remain owner-blocked.
**T10 production closure:** **BLOCKED_WIRING — not closed**
(`work_engine_consumption=NOT_WIRED`).
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
  providers or another Agent's worktree. The only runtime file edited by this
  milestone is `runtime/cli.py`, under the explicit T10 producer grant; the
  S1/S2 owners' files are untouched.

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
└── fixtures/
    └── negative_cases.json    # Work Engine negative matrix
tests/test_fleet_v1_verdict_evidence.py       # consumer matrix
tests/test_fleet_v1_t10_producer_binding.py   # real CLI + invoke_once producer
```
