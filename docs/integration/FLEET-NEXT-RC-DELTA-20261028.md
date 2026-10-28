# Fleet Next RC delta acceptance — #479 + #471 + #472

- Date: 2026-10-28
- Base: #479 exact `d037588723172816b8af12cefb2fffe6afe09f79`
- Main RC combination:
  - `d037588` (#479)
  - `debea46` — cherry-pick of #471 `7b4b9d6`
  - `2fd55ae` — cherry-pick of #472 `0c58889`
- Branch: `feat/fleet-next-rc-delta-acceptance-20261028`
- Head: `2fd55ae` (QA combination; not a merge to main)
- Scope: new acceptance harness/tests/docs only. #479/#471/#472 branches and
  worktrees were not edited.

## Original two red ids

On the exact #479 tree:

```text
FAILED tests/test_fleet_v1_milestone_batch_registration.py::
  TheLiveCommanderContractIsConsumedAsIs::
  test_a_plan_the_live_contract_accepts_becomes_a_registration
  RegistrationRefused: COMMANDER_REFUSED: #456 refused this plan
  (SESSION_NOT_FOUND)

FAILED tests/test_fleet_next_plugin_package.py::
  ThisIsNotTheNativeServerOSPlugin::
  test_the_clean_package_is_not_caught_by_the_layout_rule
  AssertionError: 7 != 6
```

The first is #472's missing/empty live-session snapshot. The second is #471's
hardcoded clean-package member count; the seven members are `plugin.json`,
`icon.svg`, `README.md` and the four Skills, not contamination.

## Combined result

Same two ids on `2fd55ae`: **57 passed, 22 subtests** for the two full files;
the exact two ids pass in the acceptance harness. Broader #479-replay set
(milestone batch unit/integration/registration/e2e, plugin package, RC
packaging/notifier/wave7 ledger): **154 passed, 30 subtests**.

Actual new failure ids in that targeted set after combining: **none**.

## Separate replayable candidate — #477/#480

#477 (`be2c1cc`) and #480 (`3777d134`) depend on #471, so they are a **separate**
candidate tree, not the main RC:

- `a19b5e5` — cherry-pick of #477
- `c84f3a4` — cherry-pick of #480
- Branch `feat/fleet-next-rc-delta-candidate-20261028`
- Tests: `test_fleet_v1_l4_model_evidence.py`,
  `test_fleet_chatgpt_batch_commander.py`,
  `test_fleet_next_parallel_plugin.py`,
  `test_fleet_next_plugin_package.py` → **87 passed, 58 subtests**.

This candidate is diagnostic only; it is not the main RC and does not merge
either PR.

## #478 Legacy gate

The acceptance harness re-checks:

- Legacy `backend_plugin_id`, release, MCP URL, port, datastore and tool
  namespace unchanged;
- the Legacy 8-Skill controller exactly 8;
- Next shadow read-only (`NEXT_SHADOW_WRITE_FORBIDDEN` on create/send/stop/
  restart/wake).

A failed/rolled-back Next install does not modify the Legacy plugin; the gate
is independent of the #480 candidate.

## Conditional sources

`#481` CLI and `#473` Hub sidecar are recorded as
`NOT_RUN_NOT_DEPLOYED`. They are not used as evidence for deployment, routing or
IM delivery, and no real IM is sent.

## Dependency graph

```text
main RC:        #479 d037 -> +#471 7b4b9d6 -> +#472 0c58889  (2fd55ae)
candidate:      main RC -> +#477 be2c1cc -> +#480 3777d134    (c84f3a4)
conditional:    #481 CLI, #473 Hub sidecar — NOT_RUN_NOT_DEPLOYED
V2 #407:        not merged
Legacy:         untouched, 8-Skill controller required
```

## Commands

```sh
python -m pytest -q -p no:cacheprovider \
  tests/test_fleet_next_rc_delta_acceptance.py
# 11 passed, 11 subtests; includes the original two ids as child pytest runs

python -m pytest -q -p no:cacheprovider \
  tests/test_fleet_v1_milestone_batch.py \
  tests/test_fleet_v1_milestone_batch_integration.py \
  tests/test_fleet_v1_milestone_batch_registration.py \
  tests/test_fleet_v1_milestone_batch_e2e_integration.py \
  tests/test_fleet_next_plugin_package.py \
  tests/test_fleet_v1_rc_next_packaging.py \
  tests/test_fleet_v1_rc_next_notifier_seam.py \
  tests/test_fleet_v1_rc_wave7_ledger.py
# 154 passed, 30 subtests
```

Caps: 2 GiB, 300 s, `taskset -c 0`, artifact TMPDIR,
`PYTHONDONTWRITEBYTECODE=1`, `pytest -p no:cacheprovider`.

## NOT_RUN / BLOCKED

- Hosted CI on this QA branch: pending / not claimed green.
- Real device, Creator, service switch, OAuth, credential, Feishu/IM, paid
  Session, GPU: NOT_RUN.
- Legacy plugin ownership/deployment: unchanged.
- #481/#473 deployment: NOT_RUN_NOT_DEPLOYED.
