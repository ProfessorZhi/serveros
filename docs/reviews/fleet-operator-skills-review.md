# Fleet Operator Skills and package/update review

Reviewer scope: this branch's Operator Skills and packaging changes only.
Baseline `597f7b8b5507ec10435b75fd4658b922bcf5c56a`. No control code
(`fleet_mcp/`, `runtime/`, `fleet/`, WorkEngine or Core) was edited.

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
  bytes of the file the `apps` reference names are unchanged; version strictly
  advances; no previous Skill removed; archive hash matches record.
- Readback: plugin id, new/different release id, version, Skills and archive
  hash must agree; missing icons are `PARTIAL`, not PASS; `LIVE` is refused.
- Rollback: previous archive hash, plugin id and version ordering are checked;
  it is a target only, with no execution path.

## Verification actually run

All commands were executed with `ulimit -v 2097152` (2 GiB), `ulimit -t 300`
(300 CPU seconds), `timeout 300`, and `taskset -c 0` (one CPU). Evidence is in
`/data/home/vince/artifacts/work-items/serveros/SRV-0011/RUN-0042/operator-skills-20261021/`.

| check | command | result |
|---|---|---|
| targeted test union | `python -m pytest -q` over the 8 operator/package modules | **224 passed, 120 subtests, exit 0** |
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

The WorkEngine allocated `RUN-0042`, but
`/data/home/vince/artifacts/work-items/serveros/SRV-0011/RUN-0042/` already held
files dated 2026-10-08 from an earlier use of that Run id. Those files were not
read, modified or deleted. This lane's evidence is namespaced under
`RUN-0042/operator-skills-20261021/`.

## Scope check

Changed paths are limited to:

- `integrations/fleet-v1-first-install/plugin/skills/**`
- `integrations/fleet-cc-dev-plugin/**`
- `integrations/fleet-app-overlay/**`
- related operator/package tests
- this lane's proposal, plan and review documents

No canonical `src/` checkout, root control worktree, other actor's WIP,
`fleet_mcp/`, `runtime/`, `fleet/`, WorkEngine or Core file was edited.
