# Fleet Next dual-rail composition QA — #480 × #483

- Date: 2026-10-28
- QA branch: `feat/fleet-next-dualrail-composition-qa-20261028`
- Exact #483 head: `644ee4a8e35b770426841929d5989bcfad128bef`
- Exact #480 head: `3777d134be02d896c9e36bc2ffd3a25baf9aaa04`
- Composed source head: `5e17d35e2204d06593bed2266680fa0dc2d576a2`
  (cherry-pick of #480 onto #483; no merge of #477/#471/#469 ancestors)
- Base for the QA PR: `feat/fleet-next-legacy-safety-20261028` at `644ee4a`
- New files: this doc, `integrations/fleet-next-dualrail-composition-qa/`
  (`dualrail_qa.py`, `README.md`) and
  `tests/test_fleet_next_dualrail_composition.py`.
- Neither `integrations/fleet-next-parallel-plugin/**` nor
  `integrations/fleet-next-legacy-safety/**` was edited.

## Why this is independent

The owner suites test each branch alone. This harness puts **one synthetic
Legacy/Next placement** through both public preflights and records both
answers plus the union gate. It calls the functions; it does not grep their
source. It also exercises the #483 survival harness with a fake Legacy
controller, and #480's shadow/update rules against the same placement.

## RED → GREEN

Before the composition oracle existed, the new suite on the composed exact
tree failed at collection:

```text
FileNotFoundError: .../integrations/fleet-next-dualrail-composition-qa/dualrail_qa.py
ERROR tests/test_fleet_next_dualrail_composition.py
1 error in 0.11s
```

After adding `dualrail_qa.py`, the same suite is GREEN. This RED is about the
missing cross-PR oracle, not a producer test failure.

## Fact matrix

| scenario | #480 | #483 | union |
|---|---|---|---|
| distinct placement | PASS | OK | GREEN |
| same backend plugin ID | `LEGACY_ID_UPDATE_FORBIDDEN` | `BLOCKED_LEGACY_COLLISION` | BLOCKED |
| same MCP URL | `NEXT_TAKEOVER_URL` | `BLOCKED_LEGACY_COLLISION` | BLOCKED |
| same port | `NEXT_SHARED_PORT` | `BLOCKED_LEGACY_COLLISION` | BLOCKED |
| same datastore / state root | `NEXT_SHARED_DATASTORE` | `BLOCKED_LEGACY_COLLISION` | BLOCKED |
| same socket / install / lock root / unit | PASS | `BLOCKED_LEGACY_COLLISION` | BLOCKED |
| same audience / credential / namespace | `NEXT_SHARED_*` | OK | BLOCKED |
| same 8-Skill controller / 7-member pin | `NEXT_CONTROLLER_INCOMPLETE` / `MIXED_SOURCE_PIN` | OK | BLOCKED |
| blank Next backend ID | `BLOCKED_NEXT_PLUGIN_NOT_REGISTERED` | `BLOCKED_PLACEMENT_INCOMPLETE` | BLOCKED |
| shadow create/send | `NEXT_SHADOW_WRITE_FORBIDDEN` | `NEXT_SHADOW_WRITE_FORBIDDEN` | BLOCKED, 0 calls |

For the four shared resources (`backend_plugin_id`, `mcp_url`, `port`,
`datastore/state_root`), both producers block on the same placement. For
resources only one producer models, the union gate still blocks and the
coverage difference is named rather than hidden. The matrix has no shared
resource on which the two return contradictory green/blocked results.

## Missing Next ID

The exact owner contract requires `BLOCKED_NEXT_PLUGIN_NOT_REGISTERED`. #480
returns exactly that when `backend_plugin_id` is blank; #483 refuses the same
placement as `BLOCKED_PLACEMENT_INCOMPLETE` with `backend_plugin_id` in
`incomplete`. Both refuse; no synthetic ID is produced. This code difference
is handed to the #483 owner rather than normalized away by QA.

## Skills and source pins

- Legacy controller: exactly the 8 Operator Skills.
- Next controller: those 8 plus `fleet-batch-commander` (9).
- Next pin: `fleet_v1_9skill`, archive
  `7320129e…e12db`, 15 members / 9 skills.
- Standalone `chatgpt_fleet_next_7member`: archive `14b3b4ee…65e2`,
  7 members / 4 skills; passing it as Next is `MIXED_SOURCE_PIN`.
- The two archive digests are distinct; the matrix never substitutes one for
  the other.

## Lifecycle, identity and shadow

- Next install/upgrade/failure/rollback are applied to the Next placement;
  `rollback(upgrade(p, v2, d2)) == p`.
- A fake Legacy controller returns stable `get_run`, `get_session`,
  `sessions_list` and an authorized fake `send`; every lifecycle step reports
  `SAME` and the before/after snapshots are equal.
- Same run ID in another track / instance / boot is
  `CROSS_TRACK` / `FOREIGN_INSTANCE` / `STALE_BOOT`; `same_object` is false.
- Shadow writes are refused before the gateway. A permission-denied read is
  `SHADOW_NOT_GRANTED` with `gateway_called=False`; the fake call log contains
  only the one granted read.

## Test result

```sh
timeout 300 taskset -c 0 env PYTHONDONTWRITEBYTECODE=1 \
  python3 -m pytest -q -p no:cacheprovider \
  tests/test_fleet_next_legacy_survival.py \
  tests/test_fleet_next_parallel_plugin.py \
  tests/test_fleet_next_dualrail_composition.py
```

- Composition suite: **20 passed, 10 subtests**.
- Both owner suites + composition: **106 passed, 21 subtests**.

Caps: 300 s, one CPU unit, `PYTHONDONTWRITEBYTECODE=1`,
`pytest -p no:cacheprovider`.

## Owner handoffs

- #483: use the named `BLOCKED_NEXT_PLUGIN_NOT_REGISTERED` for a blank Next
  ID, and keep audience/credential/namespace/Skill pin checks composed with
  #480.
- #480: socket/install/lock/unit are outside its model; keep them composed
  with #483.
- These are notes only. No producer source, existing test, or known failure
  was changed.

## NOT_RUN

Real Legacy/Next devices, Creator update, plugin install/update, old gateway
restart, service switch, OAuth/credential, real send/stop, Feishu/IM, paid
model, GPU and main merge: **NOT_RUN**. Real-device evidence remains with
#478/#425/#434. Hosted CI is pending and not claimed green.

Cross: #480 #483 #478 #425 #434 #477 #471 #469.
