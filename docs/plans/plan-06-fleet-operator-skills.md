# Fleet Operator Skills implementation plan

Item SRV-0011; baseline `597f7b8b5507ec10435b75fd4658b922bcf5c56a`.
Follow `docs/proposals/proposal-fleet-operator-skills.md`. This lane owns only
the shared Skills source, the CC/overlay packaging surfaces, package tests and
its own proposal/plan/review documents. Control code remains with the root lane.

## Phase 0 — baseline and boundaries

1. Confirm the worktree is at the baseline and no control files are modified.
2. Record the shared Skills list and the builders that consume it.
3. Allocate a genuine Run under `SRV-0011` via
   `python -m workengine`; put all evidence under its artifact directory.

Acceptance: clean lane, existing Run reference, no edits to `fleet_mcp/`,
`runtime/`, `fleet/`, WorkEngine or Core.

## Phase 1 — failing Skill-contract tests

TDD first. Add focused tests that fail before the new Skills exist:

1. Every shared `SKILL.md` has legal front matter whose `name` equals its
   directory and a non-empty description; no nested `SKILL.md`.
2. The portable first-install builder and the CC builder carry the shared
   bytes; package members retain every shared Skill; the portable builder's
   skill list equals the source directory.
3. Every `fleet_*` backtick name in every Skill resolves in
   `fleet_mcp.tools.SERVED_BY_NAME`.
4. A small set of static consistency regressions, each with a positive and a
   negative control, catches the known contradictions: positive wait-until-
   settled, mandatory global probe, per-device ability registry, hard-coded
   fleet model, and one-message/one-intent restriction.
5. The async/update Skills state the durability enum, the same-key-is-necessary-
   not-a-restart-guarantee rule, `RUN_NOT_FOUND` as non-proof, and the App-owned
   presentation boundary. These are source-text checks, not model behaviour.

Run the focused tests and capture the expected red result before adding the
Skills.

## Phase 2 — implement the shared Skills

Add three immediate-child directories under
`integrations/fleet-v1-first-install/plugin/skills/`:

- `fleet-commander-orchestration/SKILL.md`
- `fleet-async-dispatch/SKILL.md`
- `fleet-plugin-update/SKILL.md`

Preserve the existing five Skill names and package membership; do not remove a
Skill. Correct their text where it contradicts the async/commander semantics:
reuse waits/read-back, forced global discovery, single-intent batch restriction,
per-device ability registry, and a hard-coded fleet model. Update CC dev-plugin
prose/test counts that would otherwise assert a stale count, without changing
generated behaviour. Keep the source shallow and free of credentials.

Acceptance: focused Skill tests green; CC generation still byte-identical;
first-install build still deterministic with the expanded skill set.

## Phase 3 — offline package/update checker

Add to `integrations/fleet-app-overlay/`:

1. `package_update_check.py` with pure functions and a no-network CLI:
   - `plan_update(previous_record, next_record, previous_archive, next_archive)`
   - `verify_readback(plan, observed_receipt)`
   - `check_rollback(previous_record, previous_archive)`
2. A local rehearsal entry point that:
   - builds a synthetic v1 from the existing overlay fixture;
   - builds a v2 with a bumped version and the expanded shared Skills;
   - synthesizes an explicitly labelled local readback receipt;
   - emits plan/readback/rollback JSON under the operator's chosen output.
3. Document the workflow in `UPDATE-MANUAL.md` or a dedicated boundary note,
   keeping Skill/version updates separate from App-owned avatar/display-name
   changes.

TDD first: tests for equal plugin id, changed plugin id refusal, changed
`.app.json` binding bytes refusal, non-advancing version refusal, removed Skill
refusal, archive hash mismatch, contradictory readback, missing release id, and
rollback to a mismatched archive. Add adversarial tests for:

- source file and directory symlinks, a symlinked shared-Skill entry, and
  source/shared-Skills **ancestor** symlinks (including nested parents);
- output path and parent symlinks, non-empty output, lexical and realpath
  source/output overlap;
- a sentinel outside the output remaining unchanged and no `rmtree`;
- protected manifest drift (`shortDescription`, `defaultPrompt`, top-level
  description), root/`.codex-plugin` repeated fields and version sync;
- `mcp.json`/`.mcp.json` URL, added/removed server and referenced-file drift;
- normalized MCP references such as `scripts/../scripts/server.py`, traversal
  escape, changed referenced skill-script bytes, and an unrelated skill edit as
  the positive control;
- added/removed/changed non-Skill members.

The rehearsal test runs entirely offline and asserts `writes: []`, `uploads: []`,
`installs: []`, `production_verified: false`.

Acceptance: a buildable synthetic local package; same-ID update/readback and
rollback verdicts are deterministic; no network, install, Creator or OAuth
operation exists in the code path; unsafe source/output paths are refused
before any write or deletion.

## Phase 4 — bounded verification and delivery

Run the focused tests with the project interpreter and the repository's normal
test environment, capped at 300 seconds, 2 GiB and CPU 100% where a cap is
available. Run the CC builder and overlay rehearsal into the Run's artifact
directory. Record exact commands, exits, revision, dirty state and artifacts.
Do not run a full-repository suite unless the targeted union requires it.

Acceptance: all targeted tests pass; any NOT_RUN/BLOCKED item is named with
its missing input; the test-count change is explained and no baseline test is
removed or skipped; prior artifact revisions are preserved, not overwritten;
the diff stays inside the lane; commit and push the narrow branch (or update
the original Draft PR) so independent QA can reference the exact SHA. No merge,
deploy, publish or global install.

The QA handoff uses an admitted absolute artifacts root, a detached worktree
and empty output directories; it sets `TMPDIR`, `PYTHONDONTWRITEBYTECODE`,
`pytest -p no:cacheprovider` and resource limits. It must not instruct a reader
to use `/tmp`, `git checkout` in a shared checkout, or automatically remove a
shared worktree/config. Host cleanup is proposal-only after a fresh
state/worktree/container/open-file/active-Run snapshot and requires
authorization; evidence is preserved.
