# Fleet L4 Operator Skills / Plugin / MCP quality review

- Date: 2026-10-28
- Item: `SRV-0011`
- Lane: L4 commander-side Operator Skills, packaged plugin, MCP contract
- Branch: `feat/fleet-l4-quality-20261028`
- Base: `d546fd3b8d9bec9a4fa5143b2cc80a7d45ab2d09` (stacked on Draft #410
  `feat/l4-commander-skills-v1-20261027`)
- Worktree: `worktrees/serveros/SRV-0011-fleet-l4-quality-20261028`
- Ownership: no `fleet_mcp/**`, `runtime/**`, `native/**`, `integrations/
  fleet-v1-milestone-notify/**`, `integrations/fleet-v1-device-ability-local/**`
  edits. #410's pin map is touched only as a stacked re-pin proposal.

## Authoring references actually used

| reference | local state | version/hash | use |
|---|---|---|---|
| Superpowers `writing-skills` | **USED_INSTALLED** via the Skill tool | `/data/home/vince/.claude/plugins/cache/claude-plugins-official/superpowers/b36e0829c6d0/skills/writing-skills/SKILL.md`, plugin `6.3.0`, sha256 `d34db5c8…` | RED/GREEN structure, trigger-only descriptions, rationalization/duplication check |
| Superpowers `test-driven-development` | **USED_INSTALLED** via the Skill tool | same plugin, sha256 `bf1b8216…` | failing test first, then minimal edit |
| Anthropic `skill-creator` | **USED_INSTALLED (read; marketplace plugin not enabled)** | `/data/home/vince/.claude/plugins/marketplaces/claude-plugins-official/plugins/skill-creator/skills/skill-creator/SKILL.md`, sha256 `dcd4803e…`; identical to pinned `dbd4588…` | trigger/eval framing, iterate, progressive disclosure |
| OpenAI Codex `skill-creator` | **USED_INSTALLED (read)** | `/data/home/vince/.codex/skills/.system/skill-creator/SKILL.md`, Codex `0.160.0`, sha256 `6656e547…` | concise context, progressive disclosure, Codex compatibility |
| pinned Superpowers commit `2ce8aa15…` | **REFERENCED_ONLY** | downloaded to the Run artifact, sha256 `8cb3ac68…`; differs from installed `6.3.0` | cross-check the fixed version |
| pinned Anthropic commit `dbd4588f…` | **REFERENCED_ONLY** | downloaded, sha256 `dcd4803e…` (byte-identical to installed) | fixed-version reference |
| pinned OpenAI commit `49f948fa…` | **REFERENCED_ONLY** | downloaded, sha256 `a17383bf…`; differs from installed Codex `0.160.0` | fixed-version cross-check |

No install, `curl | bash`, runtime change, credential read or global plugin
mutation was performed. Reference copies live under
`RUN-0058/l4-quality-20261028/references/`, not in the repo.

## Findings and changes

### 1. Trigger descriptions were workflow summaries (fixed)

`fleet-commander-orchestration`, `fleet-create-with-grants` and
`fleet-plugin-update` began with what the skill does, not the situation that
should load it. The other four non-async Skills also led with a summary. Seven
descriptions now begin `Use when …` and name only triggers/symptoms. This is a
routing contract, not a rewrite of the bodies.

`fleet-async-dispatch` is owned by #410 and was not edited; its description
still summarizes a workflow, and its owner can apply the same rule.

### 2. Overlong Skills and repeated owner-max scope (fixed)

Measured words before → after:

| Skill | before | after |
|---|---:|---:|
| `fleet-safety-boundaries` | 1969 | 1476 |
| `fleet-create-with-grants` | 1800 | 1442 |
| `fleet-commander-orchestration` | 1257 | 1238 |

- `fleet-safety-boundaries` dropped the duplicated "Skills route / second
  registry" and "what this release is not" restatements, and replaced the
  long non-bounded-recovery catalogue with a short "do not restart what you
  are not recovering" rule. The unique login, scope, runtime and bounded
  self-healing sections remain.
- `fleet-create-with-grants` replaced the duplicated owner-max/model/scope
  sections with one "effective model and scope are read back" section that
  points to `fleet-safety-boundaries` for the full boundary.
- `fleet-commander-orchestration` points to the safety Skill instead of
  restating "not Linux root / not a universal bypass".

The duplicate sentence `not a universal bypass` now has exactly one owner:
`fleet-safety-boundaries`.

### 3. Cross-runtime discovery named Codex but not Claude (fixed)

`fleet-device-and-session-discovery` now says "Claude and Codex are different
runtimes, and a Codex alias runtime is its own address" before the
"don't infer from the base runtime" rule. The base skill already separated
device, OS user, credential instance and runtime.

### 4. Tool-count drift (partly fixed; one owner handoff)

Actual source at this base:

- `SERVED_TOOLS` = **44** distinct tools
- `TOOLS` (authored) = **11**
- `NATIVE_MIRROR_TOOLS` = **31**
- `TOOL_SURFACE_REVISION` = `fleet-mcp-7`

Stale **current** claims found and fixed in this lane:

- `integrations/fleet-cc-dev-plugin/fixture_stdio_server.py` said "all 34
  tools" / "two of the 22 native verbs" → now reads the served tuple and says
  "44 at fleet-mcp-7, read it".
- `integrations/fleet-v1-first-install/ACCEPTANCE-CHECKLIST.md` said "34 tools
  is true and measured" → now says the count is read, not frozen, and names 44
  at `fleet-mcp-7`.
- `integrations/fleet-v1-first-install/INSTALL-TEST-PLAN.md` T1 expected 34 →
  now expects the current `tools/list` count, not a frozen number.
- `tests/test_fleet_cc_dev_plugin_offline_stack.py` docstring "all 34" → "every
  served tool".

**Still blocked:** `fleet_mcp/tools.py` still has current schema text that says
"22 inherited verbs" (capability tool) and "(22 verbs)" (manifest title), plus
comments "hand-written nine" / "22 generated verbs" / "serves all 31". The
surface is 11/31/44. `fleet_mcp/**` is outside this lane's writable ownership.
The exact patch is `docs/proposals/fleet-mcp-mirror-count.patch`; the finding
is recorded as `BLOCKED_OWNER_HANDOFF` to the root/#407 owner. The audit script
`docs/audit/fleet_l4_mcp_drift.py` fails today with rc 1 on those two
descriptions; it is not a CI gate because the owning file cannot be edited here.

### 5. Dispatch semantics stayed correct

The new quality gate confirms no Skill:

- turns `UNKNOWN` into success;
- instructs a positive wait/poll loop until settlement;
- hardcodes 22/33/34/44 tool counts.

`fleet-async-dispatch` retains ACK→return and next-turn `get_run`; the
commander/reuse Skills cross-reference it. No text was changed to weaken these.

### 6. Plugin identity and MCP composition

The gate checks source ↔ packaged Skills byte identity, manifest
`name`/`version` equality, `SERVED_TOOLS` uniqueness, revision `fleet-mcp-7`,
11 authored + 31 mirror + 2 descriptors = 44. These pass.

## TDD evidence

RED (before edits), command and exact log:

```sh
TMPDIR=<run>/tmp PYTHONDONTWRITEBYTECODE=1 \
  python -m pytest -q -p no:cacheprovider tests/test_fleet_l4_quality.py
# 12 failed, 10 passed, 30 subtests passed
```

The failures were trigger descriptions (3), the duplicated owner-max sentence
(1), word budgets (2), the stale MCP descriptions (1), the fixture's "all 34"
(1), and the missing explicit Claude/Codex discovery sentence (1, plus
subtests). The Run artifact has `baseline-red.log`.

GREEN (after minimal edits):

```sh
python -m pytest -q -p no:cacheprovider tests/test_fleet_l4_quality.py
# 12 passed, 47 subtests passed
```

Broader targeted suite (Skill/plugin/release/packaging/offline stack, resource
caps 2 GiB / 300 s / taskset -c 0):

```sh
python -m pytest -q -p no:cacheprovider \
  tests/test_fleet_l4_quality.py tests/test_fleet_operator_skills.py \
  tests/test_fleet_plugin_independence.py tests/test_fleet_plugin_release_lifecycle.py \
  tests/test_fleet_plugin_client_smoke.py tests/test_fleet_v1_first_install.py \
  tests/test_fleet_v1_stage_manifest.py tests/test_fleet_cc_dev_plugin_client_e2e_gate.py \
  tests/test_fleet_cc_dev_plugin.py tests/test_fleet_codex_plugin.py \
  tests/test_fleet_app_overlay.py tests/test_fleet_cc_dev_plugin_offline_stack.py
# 270 passed, 229 subtests passed
```

MCP surface/policy subset (read-only source verification):

```sh
python -m pytest -q -p no:cacheprovider \
  tests/test_fleet_mcp_surface.py tests/test_fleet_multi_runtime_routing.py \
  tests/test_fleet_create_policy.py tests/test_fleet_owner_max.py
# 167 passed, 26 subtests passed
```

## Ownership and handoff

- **#410** owns `tests/test_fleet_cc_dev_plugin_client_e2e_gate.py` and
  `fleet-async-dispatch` + its packaged copy. This branch is **stacked on
  #410** and updates the pin map for the seven edited Skills; that one-line
  map is a proposal to #410's author, not a change pushed into #410. The async
  Skill is untouched.
- **#423** (`integrations/fleet-v1-milestone-notify/**`) and **#437**
  (`integrations/fleet-v1-device-ability-local/**`) are untouched.
- `fleet_mcp/**` is untouched; the mirror-count description fix is a patch
  proposal to its owner (`BLOCKED_OWNER_HANDOFF`).

## NOT_RUN / unverified

- Model pressure scenarios / trigger-accuracy eval on a live model: **NOT_RUN**.
  The static trigger contract and source tests ran; they are not a model
  behaviour result.
- Real client install/discovery/version/rollback on a device: **NOT_RUN**; the
  offline packaging/lifecycle tests ran, and device install remains UNKNOWN.
- Real MCP `tools/list`/error/timeout/grants/scope/ready/effective against a
  live credential: **NOT_RUN**; the source surface/policy tests ran. No MOCK is
  reported as real.
- Device authorization/effective permission: **UNKNOWN**.

## Files

- `tests/test_fleet_l4_quality.py` (new gate)
- `docs/audit/fleet_l4_mcp_drift.py` (owner-handoff finding)
- `docs/proposals/fleet-mcp-mirror-count.patch`
- seven shared Skills + packaged copies
- `integrations/fleet-cc-dev-plugin/fixture_stdio_server.py`
- two release docs' stale counts
- `tests/test_fleet_cc_dev_plugin_client_e2e_gate.py` (stacked re-pin proposal)
- `tests/test_fleet_cc_dev_plugin_offline_stack.py` docstring
