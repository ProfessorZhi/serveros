# Fleet Operator Skills and package/update review

Reviewer scope: this branch's Operator Skills and packaging changes only.
Baseline `597f7b8b5507ec10435b75fd4658b922bcf5c56a`. No control code
(`fleet_mcp/`, `runtime/`, `fleet/`, WorkEngine or Core) was edited. Revisions 2, 3 and 4
review the P1 fixes requested after Draft PR #384. The control candidate
`1011eb56a930b4e29fa165e44bd55d7dd230003f` supplied the durability enum and
`RUN_NOT_FOUND` / `never_issued` semantics, but was not merged into this lane.
The later `6c34efe` control revision is tool-description/boundary text only;
behavior remains `1011eb56`.

## Revision 2 — the P1 corrections

- **Existing Skills no longer contradict the new ones.** Reuse now preserves the
  ACK/references and inspects later instead of reading until settlement;
  discovery starts from a targeted read when the target is known; reuse allows
  complete long deliveries and batches of independent short tasks; discovery
  and safety name the shared `ability-management` authority plus a per-device
  installed/ready/granted projection; create has no hard-coded fleet model.
- **UNKNOWN is not a retry permission.** The async/create Skills state the
  actual `PROCESS_LOCAL / AGENT_CONTROL_STORE / NOT_REQUESTED` enum, treat an
  absent field on an older service as `UNKNOWN`, require a verified owner
  record or explicit replay before relying on the same key, and require
  reading the original session/native history/artefact plus owner reconciliation
  before any retry. `RUN_NOT_FOUND`/`never_issued` is explicitly not proof of
  non-execution; `queued_message_uuid` is preserved when returned.
- **Tests were reduced, not weakened.** The 20-test file is now 15 tests: five
  redundant keyword/length assertions were removed and replaced by five
  static conflict regressions, each with an old positive control and a current
  negative control. Package byte identity, frontmatter, tool resolution and
  member retention remain. Total targeted count moved 224/120 to 219/117; no
  baseline test was removed or skipped.

## Revision 3 — independent-review P1 hardening (two rounds)

### Review round 1 — reproduced findings

The independent probes in `RUN-0041/operator-independent-probes.json` showed:

- **Critical — source symlink dereference before audit.** `rehearse()` used
  `copytree` before any source-tree check, so a `notes.md` symlink to an
  external canary was copied as regular content and packaged; `plan` PASSed.
- **Critical — output-parent symlink and `rmtree`.** An `out/v1` parent symlink
  let `_stage()` remove/rewrite a reviewer-owned directory outside `out`, and
  the eventual icon refusal happened too late.
- **Critical — protected configuration under-compared.** `plan_update()` checked
  a small identity set only. Changed MCP URL/server, `shortDescription` and
  `defaultPrompt` still PASSed.

Round-1 resolution: preflight source, shared Skills and output before any
copy/write; reject all symlinks in the source tree and shared Skills, all
symlink components on the output path, non-empty output, source/output overlap
and escapes; remove `rmtree` from staging; compare the complete protected
manifest projection (root and `.codex-plugin` repeated fields), MCP structures
and referenced files, and every non-Skill member byte.

### Review round 2 — after implementation

Re-read the revised code against the four requested boundaries and the
account-updates reference:

- **Source/shared-tree safety.** `_preflight_tree()` walks with
  `followlinks=False`, rejects symlink files/directories, forbidden members,
  non-regular files and the root symlink case before the first `mkdir` or
  `copytree`. Shared Skills are checked by the same function.
- **Output safety.** `_preflight_output()` rejects every symlink component on
  the path, non-empty output, non-owned/world-writable output and source/output
  overlap. Staging creates fresh `v1`/`v2` directories and never removes a
  caller path. Tests use an external sentinel and patch `rmtree` to prove it is
  not called.
- **Protected App configuration.** The plan compares the full root and compat
  manifest projections after removing only version/keywords/onboarding/
  local-executor; it enforces compat/root version sync. MCP presence,
  structure and referenced-file hashes are compared; non-Skill members cannot
  be added, removed or changed. Adversarial tests cover URL, extra server,
  legacy `.mcp.json`, short description, default prompt, top-level description,
  compat repeated fields and supporting-file drift.
- **QA handoff.** The new handoff uses an admitted artifacts root, a detached
  worktree and empty output dirs, sets `TMPDIR`,
  `PYTHONDONTWRITEBYTECODE`, `pytest -p no:cacheprovider` and resource limits,
  and makes host cleanup proposal-only after a fresh state/worktree/container/
  open-file/active-Run snapshot. It no longer instructs `/tmp`, shared-checkout
  `git checkout`, or automatic worktree/config removal.

Residual limits: the checker is a local file/manifest comparison, not a live
Creator readback; backend/MCP/tool behavior changes remain server deployment;
a local rehearsal cannot prove the private Creator accepts or rolls back an
update.

## Revision 4 — second boundary closure (two rounds)

### Review round 1 — remaining probes

The r3 review probes (`RUN-0041/operator-independent-r3-probes.json`) found two
residual paths:

- **Source/shared ancestor symlink.** A source path under a symlinked parent
  passed the lexical overlap check; the output could be created inside the
  physical source before recursion/copy. The same gap applied to a shared-Skills
  ancestor.
- **MCP normalized reference.** `./skills/.../scripts/../scripts/server.py` was
  not normalized before matching members, so a referenced skill script could
  change while the plan PASSed; skill members were exempt from the member-byte
  check.

Round-1 resolution: reject any existing ancestor symlink for source and shared
Skills; compare real resolved source/output boundaries in both directions before
any `mkdir`; POSIX-normalize MCP references, refuse traversal/escape, and compare
the bytes of every normalized local target — including files under `skills/`.

### Review round 2 — after implementation

- **Ancestor safety.** `_preflight_tree()` now calls
  `_reject_symlink_components()` on source and shared Skills before walking or
  copying. `_preflight_output()` checks lexical and `realpath` overlap in both
  directions before creating anything. Tests cover a parent alias, a nested
  symlink parent, a shared-Skills ancestor alias, source sentinel preservation
  and no output creation.
- **MCP references.** `_mcp_references()` strips `./`/`${PLUGIN_ROOT}/`,
  POSIX-normalizes dot/`..`, refuses escapes/backslashes, and records the
  normalized target hash. The exact `scripts/../scripts/server.py` counterexample
  now refuses changed script bytes; a normal unrelated skill edit still PASSes.
- **No weakening.** Canonical identity/manifest/MCP/member checks remain; the
  change closes only the two reported boundaries. Targeted count moved
  239/117 to 245/117; no baseline test was removed or skipped.

## Design review

The design is in `docs/proposals/proposal-fleet-operator-skills.md` and the
implementation plan is `docs/plans/plan-06-fleet-operator-skills.md`.

- **Critical — none unresolved.** The work does not add a scheduler, a batch
  MCP verb, a durable task store, or a new execution authority. Dependency
  edges are explicitly required to name an actual continuing Agent or verified
  mechanism; a Skill is stated not to be that mechanism.
- **Warning — one source could drift.** The existing CC builder already reads
  the first-install `plugin/skills/` directory. New tests pin every generated
  byte and the portable package's skill list to that directory, so a second
  hand-written copy fails.
- **Warning — ACK can be mistaken for completion.** The async Skill separates
  delivery from business completion, records the exact five coordinates,
  forbids polling until settlement, and makes timeout/UNKNOWN a no-resend state.
- **Warning — a local readback could be reported as a live update.** The
  checker refuses a `LIVE` receipt and always emits
  `production_verified: false`; synthetic and operator receipts are labelled.
- **Warning — avatar/display ownership.** `fleet-plugin-update` and the overlay
  manual keep Skill/version updates separate from App-owned presentation and
  mark the latter `UNVERIFIED`.

## Implementation review

### Finding found and fixed during this lane

`package_update_check.py rehearse --out <dir>` initially wrote its JSON result
to the same path after `rehearse()` had already treated it as a directory,
raising `IsADirectoryError` and exiting 1 although all three checks had passed.
A test (`test_the_cli_rehearsal_treats_out_as_a_directory`) failed first; the
CLI now writes only `rehearsal.json` for the `rehearse` verb and keeps `--out`
as a file for the other verbs. Re-run exit is 0.

### What is enforced

- Shared source: 8 immediate-child Skills; CC adapter bytes and portable
  package skill list both equal the source.
- Every `fleet_*` backtick reference resolves in
  `fleet_mcp.tools.SERVED_BY_NAME`; no batch/scheduler tool is invented.
- Same-ID update: plugin id, name, `displayName` and `apps` unchanged; the
  complete protected root/compat manifest projection, MCP structures and
  normalized referenced-file hashes (including skill files), and non-Skill
  member bytes unchanged; version strictly advances; no previous Skill
  removed; archive hash matches record.
- Readback: plugin id, new/different release id, version, Skills and archive
  hash must agree; missing icons are `PARTIAL`, not PASS; `LIVE` is refused.
- Rollback: previous archive hash, plugin id and version ordering are checked;
  it is a target only, with no execution path.

## Verification actually run

All commands were executed with `ulimit -v 2097152` (2 GiB), `ulimit -t 300`
(300 CPU seconds), `timeout 300`, and `taskset -c 0` (one CPU). Evidence is in
`/data/home/vince/artifacts/work-items/serveros/SRV-0011/RUN-0045/operator-skills-20261021-r4/`.

| check | command | result |
|---|---|---|
| targeted test union | `python -m pytest -q` over the 8 operator/package modules | **245 passed, 117 subtests, exit 0** |
| CC development adapter | `build_cc_plugin.py --out … --next-mcp-url https://fleet-next.example.ts.net/mcp` | exit 0; **8 Skills**, 1 MCP binding |
| local CC fixture | `build_local_fixture.py --out … --json` | exit 0; 8 Skills; `TEST_FIXTURE_ONLY_NOT_A_RELEASE` |
| Claude Code package load | `claude plugin validate … --strict`; `claude --plugin-dir … plugin details` | **exit 0**; **Skills (8) / MCP servers (1)**; local only |
| local CC protocol E2E | `run_cc_client_e2e.py --out …` (no `--model-readbacks`) | exit 0; `PLUGIN_LOAD_OK`, `MCP_INITIALIZE_OK`, `TOOLS_LIST_OK`, `AUTH_SCOPE` PASS; **`CLIENT_E2E` NOT_RUN**; 0 model calls |
| update rehearsal | `package_update_check.py rehearse …` | exit 0; plan PASS 0.1.1→0.1.2; readback PASS; rollback PASS; `production_verified: false` |
| exact update CLI | `plan`, `readback`, `rollback` against rehearsal files | exit 0 each; PASS; `production_verified: false`; `rollback_ready: true` |
| first-install release | `build_first_install.py --next-mcp-url …` (no observation) | **exit 2, `BLOCKED_MCP_ENDPOINT`**, **no archive written** |

## NOT_RUN / BLOCKED

- **Model-follows-Skill behaviour:** NOT_RUN. No model call was made; the text
  is source-reviewed only.
- **Authenticated live `tools/list`:** BLOCKED_BY_OWNER_OAUTH, unchanged.
- **Live device create/send/control:** NOT_RUN; no credential and no device
  action.
- **Real private Creator same-ID update/readback/rollback:** NOT_RUN and
  **UNVERIFIED**. Only local deterministic checks exist.
- **Avatar/display-name update:** NOT_RUN and **UNVERIFIED**; owned by the
  App/Creator path, not this ZIP.
- **First-install release ZIP:** BLOCKED by the absent approved brand assets
  and the unobserved Next endpoint, as before this lane.
- **Three-device long-task acceptance:** NOT_RUN; this lane cannot discharge it.

## Artifact-directory note

The WorkEngine allocated `RUN-0042` and then `RUN-0043`; both artifact
directories already held files dated 2026-10-08 from earlier uses of those Run
ids. Those files were not read, modified or deleted. Revision 1 evidence remains
under `RUN-0043/operator-skills-20261021-r2/`; revision 2 evidence is isolated under
`RUN-0043/operator-skills-20261021-r2/`.

## Scope check

Changed paths are limited to:

- `integrations/fleet-v1-first-install/plugin/skills/**`
- `integrations/fleet-cc-dev-plugin/**`
- `integrations/fleet-app-overlay/**`
- related operator/package tests
- this lane's proposal, plan and review documents

No canonical `src/` checkout, root control worktree, other actor's WIP,
`fleet_mcp/`, `runtime/`, `fleet/`, WorkEngine or Core file was edited.
