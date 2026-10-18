# INTEGRATION_PREVIEW_V3 — 2026-10-18

**Label: `INTEGRATION_PREVIEW_V3` / `NOT_RELEASE` / `BLOCKED_SEMANTIC`.**
This is a source composition, not a release candidate, not a deployment, and not
an authorization to merge anything.

## Composition

| Step | Commit | Parent(s) | Content |
|---|---|---|---|
| base | `68e470f` | — | preview V2 (#264 + #268 + #263 + #269 + I1 manifest) |
| 1 | `6edd902` | `68e470f`, `524d35c` | grant epoch + kernel-peer authority **#275** (brings #272) |
| 2 | `a4d8538` | `6edd902`, `f8246ce` | T10 UNSIGNED denial **#274** |
| 3 | (this commit) | `a4d8538` | V3 manifest / checker / readme |

Both merges were `--no-ff` into a new isolated worktree. Original commits,
authorship and branches are preserved. No owner/product/protected branch and no
Work Engine source was modified; nothing was force-pushed. **#271 and #254 are
untouched.**

## Pinned external dependency — not merged, not installed

Work Engine chain **#14 `e4c4a2b` → #15 `4bd9e8a` → #16 `8b32aab`** is pinned
read-only. The serveros reader derivation (`WORK_WRITE_PREFIX = "work-write:"`)
still matches #14 `authority.py::_resource`. Nothing is installed and no
production `acquire_lease` or finalization caller exists.

## Focused regressions (LOCAL, composed tree)

| Suite | Command | Result |
|---|---|---|
| S1/S2 + peer/grant | `pytest -q test_v1_grant_os_principal_schema test_v1_grant_epoch_authority test_v1_lease_authority_gate test_v1_lease_acquire_producer test_dispatch_attempts test_v1_work_engine_resource_contract` | **100 passed**, exit 0 |
| T10 | `pytest -q test_fleet_v1_t10_signed_finalization test_fleet_v1_t10_authority_bridge test_fleet_v1_t10_continuation_provenance test_fleet_v1_t10_producer_binding` | **42 passed**, exit 0 |

Both under `systemd-run --user` with `MemoryMax=1G`, `MemorySwapMax=0`,
`CPUQuota=25%`, `timeout 600`, non-blocking `flock`. Coverage: OS peer/grant
mismatch and explicit schema denial; S1 live canonical lease; S2 cross-actor and
`EFFECT_UNKNOWN` fencing; T10 UNSIGNED `runs.state=SUCCEEDED` not accepted; fake
child `PASS` with true exit 1 → `DENY`.

No real session create/send, provider call, credential, network, GPU or device.

## QA status — not a signature

- **Snapshot N `1222c74`** (independent): #272 `fe4738a` gave **33 PASS + 2 SECURITY_FAIL, exit 1**. P0: the verify result was not an exact `LeaseGrant` (`project`/`items`/`actions` missing; ISO vs float) → `LeaseGrant(**grant)` `TypeError`. P1: the OS peer uid was recorded but not enforced (4242 and 9999 both verified).
- **#275 `524d35c` and #274 `f8246ce` are UNVERIFIED.** QA is testing those exact SHAs; the author's claims are not receipts.
- **The composed V3 SHA `a4d8538` has no independent QA.**

## Still blocked

`BLOCKED_TRUST_SOURCE` (no production authority; #275 pending), `BLOCKED_PRODUCER_BOUNDARY`
(no production `acquire_lease` and no production finalization caller), provider
result unknown (`BLOCKED_PROVIDER_SOURCE`), G1 #255 present-null with the earlier
`BLOCKED_ACTION` **not retried or rerouted**, Dell/fjd restricted permissions,
Windows native E2E and legitimate client OAuth E2E `NOT_RUN`.
