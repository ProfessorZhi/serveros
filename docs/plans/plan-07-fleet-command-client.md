# Fleet command-side adaptation implementation plan

Item SRV-0011; base `730bdac9ff609ceed0dc636614b20f08b2eabff5`.
Follow `docs/proposals/proposal-fleet-command-client.md`. This lane owns only
the shared Skills, the CC/Codex client packaging, the overlay update checks,
related tests and these documents. Core control code remains root-owned.

## Phase 0 — baseline and boundaries

1. New worktree/branch from `730bdac`; do not reuse the existing operator
   worktree or root `RUN-0046`.
2. Snapshot active worktrees/Runs; do not touch other lanes.
3. Use a new Run/evidence root for execution.

## Phase 1 — failing Skill/owner-max and Codex-format tests

TDD first. Add focused tests that fail before the implementation:

1. Shared source remains eight immediate-child Skills with legal frontmatter,
   package byte identity, served-tool references and no hard-coded tool count.
2. A static consistency test with positive/negative controls for:
   - routine create/send/dispatch inside the owner-max profile is not described
     as requiring per-call human approval;
   - no `sudo`/OS-root/other-OS-account claim;
   - no fixed `33` tool count;
   - project creation uses real capability discovery, not a guessed verb.
3. Codex adapter builder tests: `.codex-plugin/plugin.json`,
   `.codex-mcp.json`, `skills` path, shared bytes, no archive, one-shot output.
4. Codex local fixture builder tests: marketplace `.agents/plugins/marketplace.json`,
   local source path, stdio MCP fixture, shared bytes, no global config.
5. Update package tests: tool count is read from the artifact/surface, and a
   project-create capability absence is reported rather than guessed.

Run and capture the expected red result.

## Phase 2 — correct the eight Skills

- `fleet-safety-boundaries`: owner-max managed profile configured once; routine
  actions inside it are pre-authorized; no sudo/OS root/other account; effective
  model and native permission read back; remove the fixed tool count.
- `fleet-create-with-grants`: create is routine inside the managed profile;
  keep idempotency/UNKNOWN rules; add project-create capability discovery and
  missing-capability reporting; keep effective model/mode read-back. Bind an
  owner-max session to actual selected-runtime project membership and owner
  grant, including existing and newly registered projects without another static
  edit. Keep `create_allow` as the legacy/non-owner alternative. A **new project**
  is bound to `owner_access.project_roots`, not a static row for an absent name.
- `fleet-reuse-idle-session`: prefer reuse but do not treat create as an
  exceptional per-call approval; keep ACK/return/UNKNOWN contract.
- `fleet-device-and-session-discovery`: discover the actual tool list and
  project-create capability; no guessed verb; no global probe prerequisite.
- `fleet-commander-orchestration`: routine dispatch/create within the managed
  profile; capability discovery for new projects; keep DAG/parallel/batch.
- `fleet-async-dispatch`: add one explicit note that owner-max does not change
  ACK-versus-completion or UNKNOWN no-resend.
- `fleet-evidence-review`: keep evidence-closure semantics.
- `fleet-plugin-update`: tool count actual, project entry pending core; keep
  same-ID/App-protected update rules.

Acceptance: tests green; one source; no package member loss.

## Phase 3 — Codex client adapter

Add under `integrations/fleet-cc-dev-plugin/`:

1. `build_codex_plugin.py`: reads the shared Skills, validates frontmatter and
   HTTPS `/mcp` URL, writes `.codex-plugin/plugin.json` + `.codex-mcp.json`,
   refuses existing/in-source output, emits a development-only marker and a
   content-hash record.
2. `build_codex_local_fixture.py`: writes a local plugin with a stdio MCP
   fixture and a `.agents/plugins/marketplace.json`; no global config.
   Both development builders support `--transport stdio-owner` for the local
   trusted `bin/serveros-fleet-owned --stdio --config PATH` entry, with no
   OAuth fields and no config/credential read.
3. `run_codex_client_e2e.py`: isolated `CODEX_HOME`; marketplace add + plugin
   add; app-server initialize, thread start, `mcpServerStatus/list`, and a
   read-only `mcpServer/tool/call`; writes a receipt with separate claims:
   plugin install, MCP connected, tools list, tool call, Skills bytes,
   fixture-vs-real boundary and model-call count.

Acceptance: real Codex CLI 0.160.0 loads the local plugin in an isolated home
and connects to the local fixture MCP; no model turn and no global config
change.

## Phase 4 — update package

Keep the existing same-ID/protected-config checks. Add:
- a test that a package/plan does not assume 33 tools;
- a test that a missing project-create capability is reported as missing, not
  invented (a surface-level capability helper or documentation check).

The complete tree now exposes `fleet_create_project`; read its actual schema
from `tools/list` and do not hardcode a tool count.

## Phase 5 — verification and delivery

Run the targeted union, the CC no-model client checks and the Codex no-model
handshake, package/rehearsal hashes, and the first-install blocked attempt.
Record exact commands/exits/SHA/artifacts; commit and push a narrow branch,
open/update one Draft PR. No merge, deploy, upload, global install, credential
read or paid model call.

## Phase 6 — independent counterexample hardening (2026-10-22)

Accept the independent reviewer's seven synthetic counterexamples unchanged,
reproduce them RED at `f0d54ae`, and fix in this lane:

1. `fleet-create-with-grants`: owner-max existing and newly registered projects
   use the actual runtime owner grant before legacy static `create_allow`;
   new project creation is bound to the owner's `project_roots`.
2. Codex app-server environment: explicit copy, provider credential variables
   removed, evidence `TMPDIR`, no login copy; receipt records names seen and
   removed, never values.
3. CC wire and `fleet_owned_probe` stay separate; Codex's app-server
   `tool_count_actual`/`project_create_capability_actual` are never overwritten
   by the probe.
4. Bounded byte framing for `CodexServer.request` and
   `fleet_owned_probe._call`; burst notification+reply tests for both.
5. Fresh exclusive CC evidence root; no `rmtree` of an existing `plugin/`
   before owned-entry/config validation.
6. Skills source copy rejects symlink ancestors/members and escape before any
   output directory is created; the normal shared eight still copy.
7. Client claims require real client frames; no model turn means model/Skill
   behavior is `NOT_RUN`.

Verify with the seven counterexamples GREEN, the targeted union under the
resource caps, a regenerated v1/v2 overlay and plan/readback/rollback/
rehearsal, and real CC/Codex owned runs. Update the receipts, README, review
and PR body with the exact final SHA.

## Plan review rounds

### Round 1

- **Critical — approval contradiction.** Resolution: make owner-max routine
  authorization explicit in safety/create/reuse/commander while keeping the
  outside-scope refusal path.
- **Warning — capability name not known.** Resolution: no guessed verb; test
  the actual tool list and report absence.
- **Warning — Codex layout.** Resolution: separate builder and marketplace
  fixture; no reuse of the CC ZIP layout.
- **Warning — fixed count.** Resolution: remove `33` from Skills and test that
  the count is read, not assumed.

### Round 2

Re-read the plan against the eight Skills, the package identity rules and the
local Codex CLI. No unresolved critical/warning remains. The real owned runtime
and project-create capability were exercised read-only; a model turn remains
NOT_RUN rather than being inferred.

### Round 3 — independent counterexamples

Seven RED cases at `f0d54ae` are the acceptance gate: project root vs static
allow, explicit credential-free Codex env, buffered burst framing, per-client
wire attribution, fresh output without `rmtree`, symlink-refusing Skills copy,
and real-frame-or-`NOT_RUN`. Resolution is Phase 6. Re-run the same seven
unmodified files; do not replace them with a weaker local test.

### Round 4 — post-fix review

Re-read after the fixes against the counterexamples, the real owned receipts
and the update rehearsal. No unresolved critical/warning. Remaining NOT_RUN:
model/Skill behavior, real project create → session → long task, live Creator/
OAuth, avatar/display-name.

## Phase7 — residual evidence/stream correctness

Base169e50b, independent RUN-0057/client-residuals-20261022 namespace; existing
finalize files remain untouched. First replay the unmodified original seven and
three residual probes on a Git archive of the base. Add actual deterministic
output acquisition race/replacement/exclusive receipt tests, same-connection ACK
positive and error/wrong-id/cross-connection negatives, short-frame count/bytes
flood, unterminated observer input and interrupted/short-write tests. Capture RED.
Then update only create-with-grants, CC/Codex runners, framing/interposer and
necessary tests/docs. Re-run new tests, unchanged original seven/residual probes
and CLI/adapter/package regressions with artifact TMPDIR, no cache/bytecode,
2GiB/no-swap/1CPU bounds. No model, real client turn, device request, global config
or credential read/write, existing output deletion or service change. Freeze one
small source SHA for root cherry-pick and scoped independent review, no new PR.

Plan review round1: preserve independent probe vs client claim distinction and
all old seven cases; require terminal observer budget failure to invalidate ACK
claims. Round2: exclusive receipts must be FD-relative and never overwrite an
existing receipt even after successful directory acquisition; cap pending IDs and
metadata field/log growth as well as line buffers. No unresolved critical/warning.
