# Fleet Next dual-rail composition QA

This is an independent QA composition of two owner branches, not a production
change:

- **#480** `3777d134be02d896c9e36bc2ffd3a25baf9aaa04` — parallel plugin
  preflight (backend plugin ID, MCP URL, OAuth audience, credential anchor,
  port, datastore, tool namespace, 8/9-Skill pins, shadow writes).
- **#483** `644ee4a8e35b770426841929d5989bcfad128bef` — Legacy safety
  (placement collisions, per-track upgrade/rollback, startup preflight,
  shadow reads, scoped ids, Legacy survival harness).

The composed source tree is #483 plus a cherry-pick of exact #480. Neither
producer source file was edited. `dualrail_qa.py` loads both modules by path,
maps one synthetic Legacy/Next placement into each input shape, and calls
their public functions.

## What the matrix proves

| scenario | #480 | #483 | combined |
|---|---|---|---|
| distinct placement | PASS | OK | GREEN |
| same backend plugin ID | BLOCKED | BLOCKED | BLOCKED |
| same MCP URL | BLOCKED | BLOCKED | BLOCKED |
| same port | BLOCKED | BLOCKED | BLOCKED |
| same datastore / state root | BLOCKED | BLOCKED | BLOCKED |
| same socket / install / lock root / unit | PASS | BLOCKED | BLOCKED |
| same OAuth audience / credential / namespace | BLOCKED | OK | BLOCKED |
| same 8-Skill controller or 7-member pin | BLOCKED | OK | BLOCKED |
| missing real Next backend ID | `BLOCKED_NEXT_PLUGIN_NOT_REGISTERED` | `BLOCKED_PLACEMENT_INCOMPLETE` | BLOCKED |
| shadow create/send | `NEXT_SHADOW_WRITE_FORBIDDEN` | `NEXT_SHADOW_WRITE_FORBIDDEN` | BLOCKED, 0 gateway calls |

The last three rows are deliberate **coverage differences**, not a claim that
one producer checks what it does not. `combined_gate` is BLOCKED when either
producer blocks. A shared resource where the two disagree would be a real
contract incompatibility; no such row exists in this matrix.

## Lifecycle and identity

- Next install, upgrade, simulated failure and rollback are applied to the
  Next placement only.
- A fake Legacy controller answers `get_run`, `get_session`, `sessions_list`
  and an authorized fake `send`; `snapshot`/`run_lifecycle` show `SAME` around
  every Next step, and the fake send is unchanged.
- A same `runId` in another track, instance or boot is
  `CROSS_TRACK` / `FOREIGN_INSTANCE` / `STALE_BOOT`, not a hit.
- Shadow write and permission-denied reads are refused before the gateway:
  `gateway_called=False` and the fake call log stays empty for refusals.

## Commands

```sh
timeout 300 taskset -c 0 env PYTHONDONTWRITEBYTECODE=1 \
  python3 -m pytest -q -p no:cacheprovider \
  tests/test_fleet_next_legacy_survival.py \
  tests/test_fleet_next_parallel_plugin.py \
  tests/test_fleet_next_dualrail_composition.py
```

- Composition suite alone: **20 passed, 10 subtests**.
- Both owner suites + composition: **106 passed, 21 subtests**.

## Owner handoffs

- **#483 owner**: a blank Next backend plugin ID returns the generic
  `BLOCKED_PLACEMENT_INCOMPLETE`; the owner contract names
  `BLOCKED_NEXT_PLUGIN_NOT_REGISTERED`. Both block, but the named code is not
  shared. #483 also does not model audience / credential anchor / tool
  namespace / Skill pins; it must be composed with #480 for those.
- **#480 owner**: it does not model socket path, install root, lock root or
  unit. It must be composed with #483 for those. This is a coverage boundary,
  not a claimed pass.
- No owner source was changed; these are handoff notes only.

## NOT_RUN

Real Legacy/Next devices, Creator update, plugin install, gateway restart,
service switch, OAuth/credential use, real send/stop, Feishu/IM, paid model,
GPU and main merge are **NOT_RUN**. Real-device evidence remains with
#478/#425/#434. Hosted CI is reported separately and is not claimed green
before it completes.
