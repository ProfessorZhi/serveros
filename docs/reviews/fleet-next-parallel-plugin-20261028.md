# Fleet Next parallel plugin — source-only preflight review

- Date: 2026-10-28
- Item: `SRV-0011`
- Basis: #478 (dual-track supersession), #456/#471/#477 (L4 author chain)
- Branch: `feat/fleet-next-parallel-plugin-20261028`
- Scope: new `integrations/fleet-next-parallel-plugin/**`,
  `tests/test_fleet_next_parallel_plugin.py`, docs; no current plugin or
  existing branch changed.

## What is enforced

`parallel_plugin.preflight` is a pure function. It separates:

- legacy backend plugin ID/release/MCP URL/audience/credential/port/datastore/
  namespace and its 8-Skill controller;
- next backend plugin ID/release/MCP URL/audience/credential/port/datastore/
  namespace and its 9-Skill controller.

A missing next ID is `BLOCKED_NEXT_PLUGIN_NOT_REGISTERED`; there is no synthetic
fallback. Using the legacy ID is always `LEGACY_ID_UPDATE_FORBIDDEN`, even with
consent. A same-ID update of the **Next** plugin requires explicit consent, the
exact previous next `release_id`, and an advancing semver. Shadow is read-only.

The two source pins are exact:

- 15-member / 9-Skill `fleet_v1_9skill` (archive `7320129e…`);
- 7-member / 4-Skill `chatgpt_fleet_next_7member` (archive `14b3b4ee…`).

A standalone 7-member digest under a Next install is `MIXED_SOURCE_PIN`; a
different digest under the 9-Skill pin is `SOURCE_PIN_MISMATCH`; a legacy/next
shared digest is `MIXED_SOURCE_ARCHIVE`.

## Tests

`tests/test_fleet_next_parallel_plugin.py`:

- 16 passed, 11 subtests;
- positive distinct-track plan with 8/9 controllers;
- missing next ID, legacy ID reuse, shared URL/audience/credential/port/
  datastore/namespace, legacy rename/resource change/controller shrink,
  next controller incomplete;
- mixed/stale/mismatched source pins and shared archive;
- same-ID Next update consent/stale expected release/version advance;
- shadow write refusals and read-only pass;
- offline `MockCreator` refuses missing/legacy IDs and never sends.

RED baseline: the test file against `be2c1cc` without the new module fails at
collection (`FileNotFoundError`), recorded in the Run artifact.

## Layers

- SOURCE_CODE / UNIT: PASS.
- HOSTED_CI: pending on the Draft PR; not claimed green.
- REAL CREATOR / install / OAuth / service / paid model / IM: **NOT_RUN**.
- LEGACY plugin: untouched; no install, rename, redirect, uninstall or merge.

## Ownership

New files only. No current installed/active plugin, no existing worktree, no
#456/#471/#477 source changed; no `fleet_mcp/**`, `runtime/**`, `native/**`.
