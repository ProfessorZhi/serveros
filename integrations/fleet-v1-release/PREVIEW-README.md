# INTEGRATION_PREVIEW — 2026-10-18

**Label: `INTEGRATION_PREVIEW` / `BLOCKED_SEMANTIC` / `NOT_RELEASE`.**
This is a source composition, not a release candidate, not a deployment, and not
an authorization to merge anything.

## What was composed

| Step | Commit | Parents | Content |
|---|---|---|---|
| base | `4a31628` | — | serveros `main` |
| 1 | `67bd3d5` | `4a31628`, `aee38cb` | security tree #264 (`#262` + `#252`) |
| 2 | `cda4915` | `67bd3d5`, `c9fb802` | T10 chain #263 (`#258` → `#260` → `#263`) |

Both merges were `--no-ff` into this isolated worktree. Original commits,
authorship and branches are preserved. No owner branch, no Work Engine source and
no protected branch was modified; nothing was force-pushed.

## Targeted negative tests (LOCAL, composed tree)

| Set | Command | Result |
|---|---|---|
| A/B/C | `pytest -q tests/test_v1_lease_authority_gate.py tests/test_v1_lease_acquire_producer.py tests/test_dispatch_attempts.py` | **63 passed**, exit 0 |
| A/B/C named | 9 named negative tests (spoofed claim vs live lease, explicit writer refused, no-lease create reports no authority, unknown outcome never retried, settled not reopened, unknown cannot become call-not-sent) | **9 passed**, exit 0 |
| D | `pytest -q tests/test_fleet_v1_t10_continuation_provenance.py tests/test_fleet_v1_t10_producer_binding.py` | **6 passed**, exit 0 |
| D named | child `PASS` text with true exit 1 denied; exit 0 only `ELIGIBLE`; forged anchor denied; missing anchor `BLOCKED_WIRING` | **6 passed**, exit 0 |

All runs used `systemd-run --user` with `MemoryMax=1G`, `MemorySwapMax=0`,
`CPUQuota=25%`, `timeout 600`, and a non-blocking `flock`. No real provider send,
account operation, credential, network call or device was touched.

## What remains blocked

- **S1**: no authenticated Work Engine lease producer. `create` reports
  `write_authority` but records no claim without a lease; the producer is
  `BLOCKED_PRODUCER_BOUNDARY` (WE exposes only Python methods; no CLI/MCP; no
  actor/grant).
- **S2**: no provider-owned idempotency readback — `BLOCKED_PROVIDER_SOURCE`;
  not exactly-once.
- **T10**: the local producer/consumer path is fail-closed, but Work Engine
  `finalize_run` does not ingest the proof (`BLOCKED_WIRING`).
- G1 present-null unchanged (prior dispatch `BLOCKED_ACTION`, not retried).
- Real Dell/Windows device and authenticated CC client E2E absent.

Independent QA Snapshot I (`997a8f5`) measured this merged tree: 41 checks exit 0,
`QA_PARTIAL`, no signature. `all_release_gates=false`.
