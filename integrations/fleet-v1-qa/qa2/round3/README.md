# QA2 Round 3 — S1 Windows collection fix #371

**Verdict: #371 PASS at the declared test-only collection scope (LOCAL +
SYNTHETIC_WIN32); REAL_WINDOWS_NOT_RUN. Overall QA_PARTIAL / NOT_RELEASE. No
P0/P1. No QA/V1 signature.**

## Pinned refs

| Item | Value |
|---|---|
| #371 exact head | `dbb1f9f1f7ca5fb38a1591b4171504b21478e71a` |
| #369 exact base | `a6663075a886093b5ea2e28cd6baf8ca63365f2f` |
| Diff | 5 test/probe files, +197/−43; no runtime/workengine product code |

## Result

| Evidence | Result |
|---|---|
| #369 RED, SYNTHETIC_WIN32 | 3×`ModuleNotFoundError: fcntl`, 0 skip / 3 error, rc1 |
| #371 GREEN, SYNTHETIC_WIN32 | 3×`SKIPPED POSIX_ONLY`, 0 error; adapter `COLLECTED`; rc0 |
| Linux #369 three files | 28 passed, 1 skipped |
| Linux #371 three POSIX files | 27 passed, 1 skipped |
| Linux #371 adapter | 1 passed |
| Combined #371 | 28 passed, 1 skipped |
| Broad same-set, #369 and #371 | 97 passed, 23 skipped, 23 subtests each; 29 tests collected |
| Probe classification | skip rc0; RuntimeError rc1; ImportError rc1 (no error→skip) |
| Real Windows | `REAL_WINDOWS_NOT_RUN` — FJPCOS must run it |

The 23 broad skips are 22 `#272 source` skips (`SERVEROS_LEASE_AUTHORITY_REF` /
`fc935900…` absent) plus the existing two-uid `BLOCKED_MULTIPRINCIPAL_NOT_RUN`.
The author receipt is 117P/1S/23 with the #272 source present; the exact 117
was not independently reproduced, but #369 and #371 are identical at
97P/23S/23, the 23 subtests match, and `--collect-only` is 29 tests on both.
This is `SOURCE_NOT_PRESENT`, not an assertion reduction.

## Gate / adapter checks

- The `if os.name == "nt": pytest.skip(..., allow_module_level=True)` is at
  line 35/25/25, before the module-level `runtime/daemon.py` load at line
  67/76/51. `fcntl` is imported only by daemon; the gate is before it.
- No try/except around imports; other errors propagate. The probe reports
  RuntimeError/ImportError as ERROR, not SKIP.
- The adapter test imports no daemon/socket/fcntl/SO_PEERCRED/uid; it uses a
  fixed `uid:4242` string and only checks the adapter's COMMIT_FENCED refusal.
  It runs on Linux (1 passed) and is collected under synthetic Windows.
- `test_a_revoke_after_the_last_read_cannot_acquire_a_lease` remains in
  `test_owner_commit_fence.py:255` with `active_lease(...) is None` at line
  271; the adapter refusal assertion is at
  `test_owner_commit_fence_adapter.py:48`.

## Layout

```text
QA2-ROUND3-371-WINDOWS-COLLECTION-2026-10-21.md   full report
probes/s1_win32_collection_probe.py               copied from #371 exact head
logs/                                              exact logs + rc files
run.sh                                             export + bounded runs
```

## Run

```sh
cd integrations/fleet-v1-qa/qa2/round3
./run.sh all
```

`run.sh` exports #369/#371 with `git archive`, verifies `.snapshot-sha`, and
bounds each run with `MemoryMax=1G / MemorySwapMax=0 / CPUQuota=25% / nice 10`
when `systemd-run --user --scope` is available. It never enters author
worktrees and makes no provider/network/credential call.

Expected rcs: Linux tests and GREEN probe 0; RED probe 1; probe masking skip 0,
RuntimeError/ImportError 1. The summary prints every rc; a different value is
evidence of a changed snapshot or a changed probe, not silently a pass.

**REAL_WINDOWS_NOT_RUN.** No synthetic or Linux result in this directory is a
real Windows pass; the FJPCOS Agent must run the real pytest.
