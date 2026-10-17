# Fleet V1 verdict/evidence seam — #237 T10

**Status:** `SOURCE / OFFLINE` seam implemented and fixture-tested.
**T10 production closure:** **BLOCKED — not closed.**
**May this sign a V1 QA verdict?** **No.** Every decision returns
`qa_signature=False`, `parent_accepted=False`, `requires_independent_qa=True`.

Canonical requirement: [serveros #237](https://github.com/ProfessorZhi/serveros/issues/237),
row T10:

> Child self-reports success while tests fail → Parent not ACCEPTED.

This package answers that row without creating a second Work Engine, a second
Board, a ledger, a grant store or a daemon. It is a pure function over a
read-only snapshot of the records that already exist.

## 1. The existing trusted test evidence authority

The authority is **Work Engine** at
`ProfessorZhi/work-engine@40b062209de4a989a84b04ea57b6cc8657e1be1e`
(PR #13 head). No new persistence is introduced here.

| Fact | Existing owner | Source |
| --- | --- | --- |
| Run identity, runner identity, state, artifact directory | `runs` row | `workengine/schema.sql`; `workengine/store.py::begin_run` |
| Execution receipt: command, exit code, revision, dirty, verdict, artifact digest | `evidence` row | `workengine/migrations.py` (evidence table); `workengine/store.py::record_evidence` |
| The final verdict for a Run | `kind='finalize'` evidence row + `runs.state` | `workengine/store.py::finalize_run` |
| Only route allowed to conclude a Run | `finalize_run` | `workengine/store.py::advance_run` refuses `SUCCEEDED` as a bare transition |

The decisive property is in `finalize_run`:

```python
state = 'SUCCEEDED' if verdict == 'PASS' and exit_code == 0 else 'FAILED'
```

and it writes the receipt at `<artifact_dir>/finalize.json` with
`command`, `cases`, `exit_code`, `revision`, `dirty`, `verdict`, plus the row's
`sha256`, `item_id`, `run_id`, `kind='finalize'`, `locator` and `created_at`.

The seam reads exactly these rows. It does **not** read the child's transcript,
its report, its `"SUCCESS"`, or any other caller-supplied prose.

### Read-only SQL the production adapter must use

```sql
SELECT item_id, run_id, worker, state, artifact_dir
FROM runs
WHERE item_id = ? AND run_id = ?;

SELECT item_id, run_id, kind, locator, sha256, command, cases_json,
       exit_code, revision, dirty, verdict, created_at
FROM evidence
WHERE item_id = ? AND run_id = ? AND kind = 'finalize'
ORDER BY evidence_id DESC;
```

The adapter also reads the artifact bytes at `locator` and puts them in
`AuthoritySnapshot.artifacts`. The gate re-hashes those bytes; the row's
`sha256` is never trusted on its own.

## 2. What the gate proves

`evaluate_acceptance(expected, authority, untrusted_child_report=...)` returns
one of three decisions. Reasons are deterministic facts; any `DENY` fact wins,
then any `UNKNOWN` fact, otherwise `ELIGIBLE`.

### (a) Text cannot advance a Run or a QA gate

A child report with no finalize receipt returns `UNKNOWN / MISSING_RECEIPT`.
A `SUCCEEDED` Run row with no receipt is still `UNKNOWN`. Even the eligible
case returns `run_state_advanced=False`, `qa_gate_advanced=False`,
`parent_accepted=False`, `qa_signature=False`, `requires_independent_qa=True`.
The child's text is never parsed; `child_report_ignored=True` records that.

| Fixture | Decision |
| --- | --- |
| `text_success_without_receipt` | `UNKNOWN` + `MISSING_RECEIPT` |
| `run_succeeded_without_receipt` | `UNKNOWN` + `MISSING_RECEIPT` |

### (b) A nonzero tests command rejects even when the claim or verdict says PASS

| Fixture | Decision |
| --- | --- |
| `tests_fail_child_claims_success` | `DENY` + `TESTS_FAILED` |
| `forged_pass_with_nonzero_exit` | `DENY` + `TESTS_FAILED` (verdict says `PASS`, exit is `1`) |

### (c) Missing, stale, foreign or unattributable evidence denies / stays UNKNOWN

| Fixture | Decision | Reason |
| --- | --- | --- |
| `missing_artifact` | `UNKNOWN` | `MISSING_ARTIFACT` |
| `stale_sha` | `DENY` | `STALE_REVISION` |
| `cross_run_receipt` | `DENY` | `CROSS_RUN_RECEIPT` |
| `cross_principal_receipt` | `DENY` | `CROSS_PRINCIPAL` |
| `unrecognized_runner` | `DENY` | `UNRECOGNIZED_RUNNER` |
| `not_a_finalize_receipt` | `DENY` | `NOT_FINALIZE_RECEIPT` |
| `dirty_revision` | `DENY` | `DIRTY_REVISION` |
| `digest_mismatch` | `DENY` | `DIGEST_MISMATCH` |
| `artifact_contradicts_row` | `DENY` | `ARTIFACT_CONTRADICTION` |
| `evidence_outside_run_artifacts` | `DENY` | `EVIDENCE_OUTSIDE_RUN_ARTIFACTS` |
| `ambiguous_finalize_receipts` | `DENY` | `AMBIGUOUS_RECEIPT` |
| `run_not_terminal` | `UNKNOWN` | `RUN_NOT_TERMINAL` |
| `run_cancelled` | `DENY` | `RUN_FAILED` |
| `invalid_revision_format` | `DENY` | `INVALID_REVISION` |
| `command_mismatch` | `DENY` | `COMMAND_MISMATCH` |

### (d) A verified exit zero at the exact SHA may be *eligible* — never signed

`valid_receipt_is_eligible_but_not_accepted` requires all of:

* `runs.state == SUCCEEDED`;
* the only finalize receipt belongs to the expected `item_id` / `run_id`;
* `runs.worker` is a recognized runner **for the expected principal**;
* `command` is exactly the expected tests command;
* `exit_code == 0` and `verdict == PASS` (folded case-insensitively);
* `revision` is exactly the expected full 40-hex SHA;
* `dirty == 0`;
* `locator` resolves inside this Run's `artifact_dir`;
* artifact bytes hash to the recorded SHA-256;
* the artifact payload agrees with the row.

Result: `ELIGIBLE`, `eligible_for_qa_review=True`. It is still
`parent_accepted=False`, `qa_signature=False`, `requires_independent_qa=True`.

## 3. Residual gap — why T10 is **BLOCKED**, not closed

Work Engine's `finalize_run` receives `exit_code` and `verdict` as **arguments
from the finalizing caller**. The existing runner-produced process fact is
`runtime/bounded_invocation.invoke_once`, whose durable receipt records
`process_exit_code` from `proc.returncode`, `input_digest`, `status`, `effect`,
and `output_sha256`. That receipt is written by the host helper, not by the
child, but it is **not linked into the Work Engine evidence row**.

Therefore this seam guarantees “a structured finalize receipt, not prose”.
It does **not** yet guarantee “a hostile child cannot forge the stored exit
code”. Closing that requires an original-owner integration:

1. **Work Engine owner** — link the `bounded_invocation` receipt
   (`action_key`, `input_digest`, `process_exit_code`, `output_sha256`) into the
   finalize evidence row, and refuse finalization when the linked receipt is
   absent, replayed with different input, or its process exit contradicts the
   requested verdict. Until then, `exit_code` remains a caller assertion.
2. **ServerOS Agent Control / grant owner** — supply the read-only
   principal → runner-identity binding from the live grant/epoch. This package
   accepts it as `ExpectedAcceptance.recognized_runners`; it does not mint,
   widen or persist a grant.
3. **Parent acceptance route** — call this gate with a read-only Work Engine
   adapter. A route that parses child text or trusts a caller-supplied receipt
   dict has not integrated this seam.
4. **Independent QA owner** — consume `ELIGIBLE` and issue the actual signed
   verdict. `ELIGIBLE` is a precondition, not a signature.

Until all four land, the correct status is **T10 BLOCKED / NOT CLOSED**.

## 4. Scope and non-goals

This milestone is exactly:

* new `integrations/fleet-v1-verdict-evidence/**`;
* new `tests/test_fleet_v1_verdict_evidence.py`.

It changes no `runtime/**`, `work-engine/**`, `fleet_mcp/**`,
`integrations/fleet-v1-qa/**`, I1 manifest, credential, provider or another
Agent's worktree. It opens no sqlite connection, writes no file, starts no
process, and creates no second Board/ledger. `AuthoritySnapshot` is a
read-only readback; the production adapter must obtain it from Work Engine, not
from the child.

## 5. Running the bounded synthetic test

```bash
cd /data/home/vince/workspace/worktrees/serveros/fleet-v1-t10-verdict-evidence-20261018
ulimit -v 1048576                    # <= 1 GiB address space
timeout 600 taskset -c 0-31 nice -n 10 \
  /data/home/vince/toolchains/serveros-test/bin/python -m pytest \
  tests/test_fleet_v1_verdict_evidence.py -q --no-header -p no:cacheprovider
```

Measured on Tokyo, 2026-10-18: **29 passed in 0.12 s**, exit 0,
`pswpout` delta 0 (no swap), pinned to 32 of 128 CPUs (25%).

## 6. Layout

```text
integrations/fleet-v1-verdict-evidence/
├── README.md
├── verdict_evidence.py        # pure gate; no IO, no persistence
└── fixtures/
    └── negative_cases.json    # 20 cases, each naming the reason that must fire
tests/test_fleet_v1_verdict_evidence.py
```
