# INTEGRATION_PREVIEW_V2 — 2026-10-18

**Label: `INTEGRATION_PREVIEW_V2` / `NOT_RELEASE` / `BLOCKED_SEMANTIC`.**
This is a source composition, not a release candidate, not a deployment, and not
an authorization to merge anything.

## Composition

| Step | Commit | Parent(s) | Content |
|---|---|---|---|
| base | `905c472` | — | preview V1 (#264 security + #263 T10 + I1 manifest) |
| 1 | `5ea53b3` | `905c472`, `f4eade1` | resource-key fix **#268** |
| 2 | `fb411de` | `5ea53b3`, `322a8ce` | T10 receipt bridge **#269** |
| 3 | (this commit) | `fb411de` | V2 manifest / checker / readme |

Both merges were `--no-ff` into a new isolated worktree. Original commits,
authorship and branches are preserved. No owner branch, no product branch, no
Work Engine source and no protected branch was modified; nothing was force-pushed.
`#267` and `#254` are untouched.

## Pinned external dependency (not merged)

**work-engine #14 `e4c4a2b`** is pinned read-only. The cross-repo contract is
asserted at source:

- WE `workengine/authority.py::OwnerVerifiedLease._resource` → `f"work-write:{item}"`, strips, refuses empty.
- serveros `context/resolver.py::work_write_resource` → `WORK_WRITE_PREFIX = "work-write:"` + item, strips, refuses empty.

The string is identical. **The verifier is still a test-only fake**, so this is a
source-level contract, not a production authority.

## Focused regressions (LOCAL, composed tree)

| Suite | Command | Result |
|---|---|---|
| S1/S2 | `pytest -q test_v1_lease_authority_gate test_v1_lease_acquire_producer test_dispatch_attempts test_v1_work_engine_resource_contract` | **76 passed**, exit 0 |
| T10 | `pytest -q test_fleet_v1_t10_authority_bridge test_fleet_v1_t10_continuation_provenance test_fleet_v1_t10_producer_binding` | **29 passed**, exit 0 |

Both under `systemd-run --user` with `MemoryMax=1G`, `MemorySwapMax=0`,
`CPUQuota=25%`, `timeout 600`, non-blocking `flock`. No real session send,
provider call, credential, network or device.

## QA status (not a signature)

- **Snapshot J `769991f`** (landed during this milestone; the brief said "running"): old #264 exit 1 with 6 `SECURITY_FAIL`, new #268 exit 0 with 37 checks. `QA_PARTIAL`, no signature.
- **#269 has no independent QA at its SHA.** The author reports 10+77 focused checks; that is not a receipt.
- The composed V2 SHA itself has **no independent QA**.

## Still blocked

`BLOCKED_TRUST_SOURCE` (no OS-attested LeaseAuthority parent; grant lacks epoch and `lease.*` actions; no out-of-process verifier), `BLOCKED_PRODUCER_BOUNDARY`, no production finalization caller, unknown provider result (`BLOCKED_PROVIDER_SOURCE`), G1 present-null (prior dispatch `BLOCKED_ACTION`, not retried), Windows/Dell native E2E and owner OAuth client E2E absent.
