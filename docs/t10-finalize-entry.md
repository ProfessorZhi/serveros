# T10-required finalize entry: exact parent contract

**Status (this milestone): `BLOCKED_TRUST_SOURCE`.**

| Fact | State |
|---|---|
| Work Engine finalize entry | `finalize_run(..., t10_required=True, ...)` refuses without a trusted parent |
| Production parent / verifier | **not bound** — no in-process verifier, no CLI verifier |
| Legacy non-T10 finalization | compatible, labelled `authorization: "UNSIGNED"`, `t10_acceptable: false` |
| T10-verified result | `authorization: "T10_VERIFIED"`, `t10_acceptable: true` — **not** an independent QA verdict |
| serveros #269 | `PREVIEW_ONLY`; it does not bind this parent |

This document is the exact integration contract for the original owner of the
out-of-process parent (serverosd peer-grant boundary + runner). It is a
contract, not a claim that the parent exists.

## 1. The entry route that was made safe

The production-facing Work Engine finalize consumers are:

| Consumer | Path |
|---|---|
| CLI `workengine run finalize` | `workengine/cli.py::_cmd_run` -> `ServerOSStore.finalize_run` |
| Python callers | `ServerOSStore.finalize_run` (the store is the public facade) |
| `ServerOS` facade | no finalize method |
| MCP `serveros-work-mcp` | no finalize tool |

There is one method, not a parallel helper. `finalize_run` now takes:

```python
finalize_run(
    item_id, run_id,
    *, verdict, exit_code, command, cases, revision, dirty,
    t10_required=False,
    t10_binding=None,
    receipt_ref=None,
    receipt_authority=None,
    now=None,
)
```

* `t10_required=False`: historical behaviour. The conclusion is written to the
  existing `finalize.json` and `evidence` row, and the answer is **explicitly**
  `authorization: "UNSIGNED"` / `t10_acceptable: false`. Ordinary non-T10
  compatibility survives, but it is not V1/T10 evidence.
* `t10_required=True`: the narrow opt-in. `receipt_authority` must be a trusted
  `RunnerReceiptAuthority` supplied by the parent; `t10_binding` must be an
  exact `T10Binding`; `receipt_ref` must be the opaque ref the parent issued.
  Missing any half refuses; there is **no fallback** to the unsigned path.

The CLI exposes `run finalize --t10-required [--receipt <ref>]`. The CLI holds
no trusted verifier, so it refuses `BLOCKED_TRUST_SOURCE` (exit 2) rather than
pretending. That refusal is the only correct CLI behaviour until the parent is
bound.

## 2. Exact parent caller

The parent must call the store in-process with the verifier it owns; it must not
pass an actor, commit, receipt hash or exit code from the child or from argv.

```python
from workengine.authority import T10Binding
from workengine.store import ServerOSStore

binding = T10Binding(
    item_ref=item_id,          # the Item the Run belongs to
    run_ref=run_id,            # the exact Work Engine Run
    commit_sha=exact_head,     # full 40-hex worktree HEAD
    actor_ref=principal,       # the grant principal, from the trusted context
)

store.finalize_run(
    item_id,
    run_id,
    verdict=runner_verdict,
    exit_code=runner_exit_code,
    command=command,
    cases=cases,
    revision=revision,
    dirty=dirty,
    t10_required=True,
    t10_binding=binding,
    receipt_ref=opaque_receipt_ref,
    receipt_authority=trusted_receipt_authority,
)
```

The parent must derive the binding from its own stores, not from the request:

1. **WorkRun / Item**: exactly one non-terminal Run for the Item; zero or more
   than one refuses before the finalize call.
2. **commit**: the worktree's actual full 40-hex `HEAD`; dirty or short SHA
   refuses.
3. **actor**: the principal resolved from the trusted context
   (`serverosd agent.context.resolve` + the peer-grant boundary), never a
   caller field.
4. **receipt**: the real `runtime/bounded_invocation.invoke_once` record for
   that action key, with `receipt_sha256` recomputed over the receipt and its
   stdin/stdout/stderr. Child text is hashed, never believed.

`receipt_authority` is responsible for revocation and expiry. It must raise
`ReceiptError("AUTHORITY_REVOKED", ...)` (or another refusal) rather than return
a receipt for a revoked grant.

## 3. What the store re-checks

Even a parent bug cannot conclude a different Run:

* `VerifiedRunReceipt.item_ref` / `run_ref` must equal the request;
* `input_digest` and `receipt_sha256` must be 64-hex;
* `commit_sha` must be a full 40-hex SHA;
* `actor_ref` and `runner_identity` must be non-empty;
* `process_exit_code` must equal the caller's `exit_code`;
* the receipt verdict must equal the caller's verdict;
* a `PASS` with nonzero `process_exit_code` is `RECEIPT_INCONSISTENT`;
* `T10Binding` must name the same Item, Run, commit and actor as the receipt;
* `SUCCEEDED` additionally requires `verdict == PASS`, `exit_code == 0` and
  `receipt.process_exit_code == 0`;
* a terminal Run that was concluded unsigned cannot be relabelled:
  `T10_UNSIGNED_RUN`;
* a terminal Run's stored receipt for another commit/actor/anchor is
  `T10_BINDING_MISMATCH`.

The receipt binding is stored in the existing `finalize.json` evidence file and
anchored by the existing evidence row's `sha256`. The file also carries
`authorization: "UNSIGNED" | "T10_VERIFIED"`, so a reader of the durable record
does not have to infer the label from the return value or from a missing
receipt. No new table, no second evidence store, no schema change, no new grant
source.

## 4. Refusal matrix

| Case | Reason code |
|---|---|
| no trusted verifier | `BLOCKED_TRUST_SOURCE` |
| no `T10Binding` | `T10_BINDING_REQUIRED` |
| no receipt ref / caller JSON hash | `RUNNER_RECEIPT_REQUIRED` |
| authority cannot answer | `UNTRUSTED_RECEIPT` |
| authority says revoked | parent's code, e.g. `AUTHORITY_REVOKED` |
| child PASS with exit 1 | `RECEIPT_INCONSISTENT` |
| caller exit disagrees with receipt | `EXIT_CODE_DISAGREEMENT` |
| receipt verdict disagrees | `RECEIPT_VERDICT_DISAGREEMENT` |
| forged/ non-hex digest or anchor | `INVALID_RECEIPT` |
| receipt for another Run / Item | `RECEIPT_RUN_MISMATCH` / `RECEIPT_ITEM_MISMATCH` |
| binding Item / Run mismatch | `T10_ITEM_MISMATCH` / `T10_RUN_MISMATCH` |
| binding commit / actor mismatch | `T10_COMMIT_MISMATCH` / `T10_ACTOR_MISMATCH` |
| T10 asked on an unsigned terminal Run | `T10_UNSIGNED_RUN` |
| stored receipt differs from the request | `T10_BINDING_MISMATCH` |

## 5. What this does not claim

* It does not bind a production parent. `BLOCKED_TRUST_SOURCE` remains open.
* It does not make the CLI a verifier. `--t10-required` is an executable refusal
  until the out-of-process parent exists.
* It does not turn `T10_VERIFIED` into an independent QA acceptance. The
  `T10_VERIFIED` label means the store consumed a receipt the parent verified;
  independent QA and the V1 release gate still decide acceptance.
* It does not migrate legacy callers. A legacy finalize stays `UNSIGNED` and
  must not be cited as V1/T10 evidence.
* It does not modify the serveros repo, Agent Control, G1, release, QA or Core.

## 6. Owner integration to-dos

1. Bind `RunnerReceiptAuthority` to the real `invoke_once` receipt and the
   serverosd peer-grant boundary; pass it only from the trusted parent.
2. Derive the exact `T10Binding` (unique Run, full HEAD, grant principal).
3. Call `finalize_run(..., t10_required=True, ...)` from the parent's finalize
   path; keep the CLI `--t10-required` refusal until the parent is real.
4. Add the cross-repo E2E: real principal + grant `create -> lease -> send`;
   T10 child self-reports PASS while exiting 1; verified exit 0 only with the
   parent-supplied receipt; post-revocation deny.
5. Keep `work_engine_consumption` honest: this store-side gate is
   `SOURCE / LOCAL`, not `LIVE`.

## 7. Verification (this milestone)

Run from `worktrees/work-engine/we-finalize-t10-required-20261018` under
`systemd-run --user --scope` (`MemoryMax=1G`, `MemorySwapMax=0`,
`CPUQuota=25%`, `TasksMax=64`), `timeout 300`, and a non-blocking `flock`:

```text
pytest tests/test_finalize_entry.py tests/test_runner_receipt.py \
       tests/test_v3_finalize.py tests/test_v2_execution.py \
       tests/test_v3_run_cancel.py -q
```

Result: `92 passed`. Proof level: `LOCAL / HERMETIC`; no real parent, no
production DB/service, no provider, no network.
