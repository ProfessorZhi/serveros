# ChatGPT L4 batch-commander playbook and plugin-update contract

- Date: 2026-10-28
- Item: `SRV-0011`
- Branch: `feat/fleet-chatgpt-operator-20261028`
- Base: `08365429fdbaf8007a7bd2806eaa3dc4464452ae` (stacked on #453)
- Scope: new files only. No #410/#453 existing files touched; #423/#437 untouched.
- Source level: code + docs + tests. **No Creator call, upload, install, OAuth
  or user-plugin change.**

## 1. ChatGPT batch-commander Skill

`integrations/chatgpt-fleet-next/skills/fleet-batch-commander/SKILL.md` is the
cloud-side playbook for a complex goal:

- read live device role/ownership, runtime bindings, installed/ready/granted/
  effective ability, Session occupancy, model list and grant;
- split the goal into independently acceptable tasks, each with its own
  `task_id`, device/runtime/project/Session, worktree, scope, AC, tests, report
  and idempotency key;
- select a fitting device/model/idle Session; no fallback by familiar name;
- persist a Batch ID with per-task identifiers; dispatch; treat ACK as delivery
  only; return to chat; do not poll;
- later `fleet_get_run`/readback; ACK-only / bare completed / RUN_NOT_FOUND are
  `UNKNOWN`, never success;
- observer/IM later; `OBSERVER_NOT_AVAILABLE` when there is none;
- typed refusals: `MISSELECTED_DEVICE`, `STALE_CAPABILITY`,
  `MODEL_NOT_AVAILABLE`, `GRANT_DENIED`, `SESSION_BUSY`, `WORKTREE_CONFLICT`,
  `ACK_ONLY`, `FALSE_COMPLETION`.

The Skill refers to the shared 8 Operator Skills instead of copying their
policy text. It is a new file in the ChatGPT candidate lane, not a second V1
publisher.

### Executable contract

`integrations/chatgpt-fleet-next/batch_contract.py` is a pure, offline decision
helper used by tests:

- `validate_batch(batch, snapshot)` checks device role/readiness, runtime,
  ability readiness/effective/grant, model list, grant, Session state/project,
  unique worktrees and Batch ID;
- `completion_verdict(run)` classifies ACK-only, bare `completed`,
  `RUN_NOT_FOUND`, running, failed and evidence-read;
- `batch_verdict(results)` keeps the batch `UNKNOWN` if any task is unverified.

No Fleet call, model call, network, device or credential is used.

### RED/GREEN

- RED: `tests/test_fleet_chatgpt_batch_commander.py` first run → **3 failed,
  18 passed, 24 subtests** (ACK phrase, `RUNNING_AGENT` not treated as busy,
  model-limit fixture used an available model).
- GREEN: same file → **21 passed, 28 subtests**.
- Logs are in the Run artifact (`batch-tests.log`, `batch-tests-green-3.log`).

## 2. Official ChatGPT update contract

`docs/research/chatgpt-plugin-update-contract-20261028.md` records the exact
public evidence retrieved on 2026-10-28:

- `update_plugin` arguments (`plugin_id`, `expected_release_id`, `archive`) are
  reported by `openai/codex` issue #49957 / Plugin Creator `0.1.22`, not a
  public developer-doc page.
- Official submission docs describe a **complete ZIP**; no public evidence for
  `tar.gz`. The issue reports overlay/omitted-file preservation, but the
  official instruction is to include all components to keep. This repo takes
  the stricter complete-ZIP rule.
- `expected_release_id` is the **current release ID guard**, not a semver.
- Version monotonicity is an **owner/repository policy**, not a quoted public
  rule; the local preflight enforces it.
- App ID and backend plugin ID are **distinct**. The enterprise page says to use
  the app ID for app linking; a `plugin_asdk_app_…` URL represents an
  `asdk_app_…` app. It does not license deriving the backend plugin ID.
- App-backed `displayName`/avatar updates are **UNKNOWN/BLOCKED** until a test
  plugin readback proves them. A local icon check does not prove rendering.
- No documented Creator rollback-by-version-decrease; the managed/GitHub path
  retains the last working version on a bad update.

## 3. Preflight and presentation matrix

- `integrations/fleet-app-overlay/chatgpt_update_preflight.py` — dry-run only:
  exact `plugin_id`, `expected_release_id`, ZIP-only, complete previous member
  set, advancing semver, root manifest name, icon file inspection, App-backed
  marker. It calls no network and sets `creator_called=false`,
  `uploaded=false`, `production_verified=false`.
- `integrations/fleet-app-overlay/chatgpt_presentation_matrix.json` — support
  matrix for portable, App-backed, app-ID-as-plugin-ID and Claude Code cases.
  App-backed avatar/display rows are `UNKNOWN`/`BLOCKED`, never `SUPPORTED`.
- `tests/test_fleet_chatgpt_plugin_update_contract.py` — 14 tests / 8 subtests
  covering the positive path and wrong plugin ID, app ID, stale release, tar.gz,
  version decrease, omitted member, tiny icon, App-backed presentation and the
  matrix honesty rules.

## 4. Source-level package

`integrations/chatgpt-fleet-next/plugin.json` was bumped `0.1.0 → 0.1.1` for the
new Skill. `build_zip.py --check` built the same package twice:

```text
archive_sha256 14b3b4ee84a7be5428d29939e6a7297ec0aed34395f0267e74c9f18ef0bd65e2
content_sha256 df15ee3779678ac80c54ab8891b6f6597f4c902f1fe3fc9e09566b99a1b3a7e1
members: README.md, icon.svg, plugin.json, 4 × skills/<name>/SKILL.md
```

The dry-run preflight against that ZIP and a synthetic previous record returned
`PASS`, with `creator_called=false`, `uploaded=false`,
`production_verified=false`. The previous record is synthetic and labelled
`DRYRUN`; it is not a live plugin identity.

## 5. Verification

- `tests/test_fleet_chatgpt_batch_commander.py`: 21 passed, 24 subtests.
- `tests/test_fleet_chatgpt_plugin_update_contract.py`: 14 passed, 8 subtests.
- Combined targeted union with L4/operator/overlay gate: 212 passed, 210
  subtests.
- `build_zip.py --check`: stable (7 files).
- Preflight dry-run: PASS (synthetic identity).
- Resource caps: 2 GiB / 300 s / `taskset -c 0` / artifact TMPDIR /
  `PYTHONDONTWRITEBYTECODE=1`.

## 6. NOT_RUN / unverified

- Live ChatGPT Plugin Creator `update_plugin`: **NOT_RUN**; no upload, no
  Creator call, no existing plugin change.
- Real App-backed avatar/display/name update: **UNKNOWN/BLOCKED**; no test
  plugin readback.
- Real Fleet batch dispatch / Session selection / observer IM: **NOT_RUN**; the
  contract tests are offline.
- Device ability/model/grant readback against a live device: **NOT_RUN**.
- `tar.gz` support: **NOT_ESTABLISHED** (public evidence is ZIP only).

## 7. Ownership / conflicts

- New files only under `integrations/chatgpt-fleet-next/`,
  `integrations/fleet-app-overlay/`, `docs/`, `tests/`. The 8 V1 Operator Skills
  remain #410/#453-owned and were not edited; packing this playbook into the
  existing V1 ChatGPT plugin is **BLOCKED_OWNER_HANDOFF**.
  `integrations/fleet-app-overlay/`, `docs/`, `tests/`.
- No edits to `fleet_mcp/**`, `runtime/**`, `native/**`, #410/#453 existing
  files, #423 milestone-notify, or #437 device-ability-local.
- The branch is stacked on #453; it does not merge main or publish a plugin.

## Sources

- [OpenAI Plugins submission](https://developers.openai.com/plugins/deploy/submission)
- [ChatGPT Enterprise plugin management](https://learn.chatgpt.com/docs/enterprise/plugin-management)
- [openai/codex issue #49957](https://github.com/openai/codex/issues/49957)
