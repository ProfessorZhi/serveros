# Fleet V1 verdict/evidence seam — #237 T10

**Status:** `SOURCE / OFFLINE` consumer seam implemented and fixture-tested.
**T10 production closure:** **BLOCKED_WIRING — not closed.**
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

## 3. Current production is BLOCKED_WIRING

The existing producer does **not** record what the consumer needs:

* `runtime/cli.py::_dispatch_answer` records only `action_key`, `status` and
  `input_digest` as the DISPATCHED evidence — no receipt-file/artifact anchor;
* `mcp/continuation_mcp.py::_mutate` uses the work-verb arguments as stdin; it
  does not put the T10 binding (work_run, commit SHA, principal, runner
  identity) into the receipt;
* `runtime/cli.py::cmd_work_dispatch` passes `--input-file` or empty stdin; no
  binding and no anchor.

Therefore a **real** exit 0 receipt produced by current production answers
`BLOCKED_WIRING`, not `ELIGIBLE`. The failing end-to-end fixture is:

```bash
python -m pytest \
  tests/test_fleet_v1_verdict_evidence.py::test_current_production_receipt_is_blocked_wiring \
  -q
```

It calls the real `runtime/bounded_invocation.invoke_once`, gets a real exit 0,
and still gets `BLOCKED_WIRING` with `MISSING_DISPATCH_BINDING` and
`MISSING_RECEIPT_ANCHOR`. That is the precise wiring gap, not a green T10.

The legitimate-eligible fixture (`test_real_runner_exit_zero_with_exact_binding_is_eligible`)
uses the same real executor but supplies the producer-contract shape. It proves
the consumer seam is correct once the producer patch lands.

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

### 4.3 `runtime/cli.py::_dispatch_answer` (owner patch A)

After reading `receipt`, compute the anchor above from `path` and the three
sibling artifacts, and add it to the evidence:

```python
return {"evidence": {
    "action_key": receipt.get("action_key"),
    "status": receipt.get("status"),
    "input_digest": receipt.get("input_digest"),
    "receipt_sha256": _receipt_anchor(path, admission["action_key"]),
    "store": str(Path(receipts_root)),
    "receipt": str(path),
}, "receipt": receipt}
```

`_receipt_anchor` returns `None` when any artifact is missing or is a symlink:
an anchor over invented bytes is worse than no anchor.

### 4.4 `runtime/agent_control_store.py::dispatch_evidence_conflict` (owner patch B)

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

### 4.5 Dispatchers (owner patch C)

`mcp/continuation_mcp.py::_mutate` (`continuation.execute`) and
`runtime/cli.py::cmd_work_dispatch` must build the binding from trusted inputs
and pass it into `invoke_once`:

```python
binding = {
    "cycle_ref": <admission cycle ref>,
    "action_key": key,
    "work_item_id": <envelope work_item_ref / Work Engine item_id>,
    "work_run_id": <Work Engine run_id>,
    "commit_sha": <exact 40-hex revision>,
    "principal": <delegated_by / trusted context>,
    "grant_ref": <envelope grant_ref>,
    "runner_identity": <Work Engine runs.worker / provider runtime identity>,
}
canonical = json.dumps(binding, sort_keys=True, separators=(",", ":"))
invoke_once(..., input_text=canonical,
            env={**existing_env, "SERVEROS_DISPATCH_BINDING": canonical})
```

`work_run_id`, `commit_sha`, `principal` and `runner_identity` must be accepted
from the Parent's accepted scope / the live envelope, never from the child's
report. `continuation.execute`'s schema and `work dispatch`'s CLI therefore
need explicit `work_run` / `commit_sha` / `principal` / `runner_identity`
inputs (or the envelope must carry them). The existing envelope already carries
`work_item_ref`, `grant_ref` and `delegation_ref`; the Run id and exact SHA are
the missing producer inputs.

The seam's admission checks target the existing Agent Control `work dispatch`
admission (the record that owns `action_key`, `grant_ref`, reservation and
selection). `continuation.execute` is a different surface and does not by
itself create that admission; a caller that wants this gate on that surface must
also supply the corresponding admission snapshot, or the integration must add
one. Without it the gate answers `MISSING_ADMISSION / UNKNOWN`, never eligible.

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
* It never edits `runtime/**`, `work-engine/**`, `fleet_mcp/**`,
  `integrations/fleet-v1-qa/**`, the I1 manifest, credentials, providers or
  another Agent's worktree.

## 6. Test evidence

```bash
cd /data/home/vince/workspace/worktrees/serveros/fleet-v1-t10-verdict-evidence-20261018
ulimit -v 1048576                    # <= 1 GiB address space
timeout 600 taskset -c 0-31 nice -n 10 \
  /data/home/vince/toolchains/serveros-test/bin/python -m pytest \
  tests/test_fleet_v1_verdict_evidence.py -q --no-header -p no:cacheprovider
```

Measured on Tokyo, 2026-10-18: **48 passed**, exit 0, `pswpout` delta 0 (no
swap), 32 of 128 CPUs (25%). The T10 product tests spawn a real offline
synthetic runner through the unmodified `runtime/bounded_invocation.invoke_once`;
the runner prints `ALL TESTS PASSED` and exits 1 in the denial case, and exits
0 in the eligible case. Git commits are pinned to the exact 40-hex SHA in the
binding.

The fixture suite covers:

* text-only success without a receipt → `UNKNOWN / MISSING_RECEIPT`;
* real runner exit 1 with `ALL TESTS PASSED` text → `DENY / PROCESS_EXIT_NONZERO`;
* forged Work Engine exit 0 against a real exit 1 receipt → `DENY / EXIT_CODE_DISAGREEMENT`;
* forged receipt edited to exit 0 after the anchor → `DENY / RECEIPT_ANCHOR_MISMATCH`;
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
tests/test_fleet_v1_verdict_evidence.py
```
