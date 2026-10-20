# QA2 Round 4 — test-only #374

**Verdict: #374 PASS at the declared test-only scope (LOCAL + SYNTHETIC_WIN32);
REAL_WINDOWS_NOT_RUN. Overall QA_PARTIAL / NOT_RELEASE. No P0/P1. P2×3
(probe process-cache limitation, SYNTHETIC_WIN32 boundary, 117P source not
reproduced). No QA/V1 signature.**

## Pinned refs

| Item | Value |
|---|---|
| #374 exact head | `9e84be33fb29628fa70d63aa52bac950f436feb1` |
| base RC-v9 / #372 exact | `31bfc28d9ff4a1d0a56914fb8e4b82ec6ebf85fd` |
| Diff | 3 test/probe files, +155/−18; no product runtime/WorkEngine/Grant/owner policy |

## Results

| Evidence | Result |
|---|---|
| #372 epoch single, SYNTHETIC_WIN32 | `ERROR ModuleNotFoundError: fcntl`, rc1 |
| #372 old probe repo-only | `summary 0/0/0`, **rc0 vacuous green** (the defect #374 fixes) |
| #372 old probe no args | `IndexError`, rc1 (no reason) |
| #374 probe no args / repo only / missing / non-.py | `ERROR ... usage`, rc2 |
| #374 probe epoch only (1 skip / 0 collected) | `ERROR zero modules collected`, rc2 |
| #374 probe 3 POSIX + adapter | 3 SKIPPED / 1 COLLECTED / 0 ERROR, rc0 |
| #374 probe epoch + adapter | 1 SKIPPED / 1 COLLECTED, rc0 |
| #374 probe changed set (epoch + 3 POSIX + adapter) | 4 SKIPPED / 1 COLLECTED, rc0 |
| Linux #372 epoch | 13 passed |
| Linux #374 epoch | 13 passed |
| Linux #374 changed set | 46 passed, 1 skipped |
| Linux #374 probe regression | 5 passed |
| Linux broad 9-file, #372 and #374 | 97 passed, 23 skipped, 23 subtests each |
| Real Windows | `REAL_WINDOWS_NOT_RUN` |

The only Linux skip is `BLOCKED_MULTIPRINCIPAL_NOT_RUN` (uid 1002). The 23
broad skips are 22 `#272 source` skips (`SERVEROS_LEASE_AUTHORITY_REF` /
`fc935900…` absent) plus the two-uid skip. The author receipt 117P/1S/23 is
with the #272 source present; it was not independently reproduced here, but
#372 and #374 are identical at 97P/23S/23, the 23 subtests match, and there is
no assertion reduction.

## Gate / cross-platform assertion check

- The epoch gate is at `tests/test_v1_grant_epoch_authority.py:33`, before the
  module-level `daemon.py` load at line 66; `runtime/agent_control_store.py`
  is imported by daemon. `os`/`pytest` are the only imports before the gate;
  no try/except swallowing.
- All 13 epoch tests use the daemon or the agent-control store; there is no
  pure cross-platform security assertion to split out. The #371
  `test_owner_commit_fence_adapter.py` remains the Windows-executable
  COMMIT_FENCED refusal assertion. No P1.
- The probe pops `fcntl` before installing its blocker: full/partial `fcntl`
  preload still yields `ERROR fcntl` rc1. A fully preloaded
  `serveros_runtime_daemon` can make the probe report COLLECTED/rc0 for an
  ungated old module (#372 epoch), because the test modules' `_load`
  short-circuits on `sys.modules`. This is a P2 process-cache limitation of
  the test-only probe; the documented/regression invocation is a fresh
  subprocess, and on #374 the gate runs before the daemon, so a cached daemon
  still yields SKIPPED + zero-collected rc2. If CI invokes `main()`
  in-process as a gate, treat this as P1 and fail closed on preloaded daemon/
  store modules.

## Layout / run

```text
QA2-ROUND4-374-WINDOWS-EPOCH-PROBE-2026-10-21.md   full report
probes/s1_win32_collection_probe.py                copied from #374 exact head
probes/cache_contamination_wrapper.py              QA-only cache witness
logs/                                              exact logs
```

```sh
# #372 RED
python <372>/tests/probes/s1_win32_collection_probe.py <372>
python <372>/tests/probes/s1_win32_collection_probe.py <372> \
  tests/test_v1_grant_epoch_authority.py

# #374 probe guards / GREEN
python <374>/tests/probes/s1_win32_collection_probe.py <374>
python <374>/tests/probes/s1_win32_collection_probe.py <374> \
  tests/test_v1_grant_epoch_authority.py
python <374>/tests/probes/s1_win32_collection_probe.py <374> \
  tests/test_owner_policy_binding.py tests/test_owner_commit_fence.py \
  tests/test_owner_policy_reduced_gate.py \
  tests/test_owner_commit_fence_adapter.py

# Linux
cd <snapshot> && PYTHONPATH=$PWD pytest -q -rs -p no:cacheprovider \
  tests/test_v1_grant_epoch_authority.py ...
```

All runs were bounded with `MemoryMax=1G / MemorySwapMax=0 / CPUQuota=25% /
nice 10 / timeout`, `PYTHONDONTWRITEBYTECODE=1`, `-p no:cacheprovider`; no full
pytest, provider, credential or production service.

**REAL_WINDOWS_NOT_RUN.** FJPCOS must run the real RC-v9 tree after #371 + this
PR; no result here is a real Windows pass.
