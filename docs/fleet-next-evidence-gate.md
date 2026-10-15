# Fleet Next release evidence: do not equate code, tools, or login with readiness

This is an **offline audit tool**, not an OAuth issuer, a remote shell, a
production deployer, or a substitute for owner consent.

Run from the repository root:

    python -m fleet_mcp.release_evidence --evidence ./reviewed-next-evidence.json

The input is a **non-secret**, separately collected, reviewed JSON document
with schema `serveros/fleet-next-release-evidence/v1`. A positive control, with
every receipt a placeholder, is checked in at
`tests/fixtures/fleet_next/release_evidence.candidate.json`; it proves the gate
accepts a well-formed manifest and proves nothing about any machine.

## What Fleet product V1 actually promises

The release claim is narrow and this gate is written to it:

- 33 declared MCP tools and exactly 6 OAuth scopes
  (`fleet:read|send|create|control|approve|navigate`);
- for an owner-managed **Claude or Codex runtime**: `list`, `read`, `create`,
  `send`, `get_run`, `interrupt`, `resume`, and recovery after a disconnect;
- provider-native `approval`, `reconfigure` and `close` **only where the
  provider has them**.

## Two things the first revision got wrong

**It required every operation to be `PASS` everywhere.** That turned a provider
that genuinely has no interactive approval channel into a permanent release
block, and the only way past it was to write `PASS` — so the same rule also
produced the second fault: an operation reported as supported that the provider
does not support, which the gate then treated as its strongest evidence.

The operations are therefore split:

| Group | Members | Rule |
|---|---|---|
| promised | `list` `read` `create` `send` `get_run` `interrupt` `resume` `recover_after_disconnect` | `PASS`, or the release is blocked. `UNSUPPORTED` is refused **by name** — the promise is not the provider's to waive |
| provider-gated | `approval` `reconfigure` `close` | `PASS`, or `UNSUPPORTED` **with its own capability receipt**. `UNKNOWN`, or a field that is merely absent, blocks |

A declared-unsupported operation that also reports `PASS` is refused as a
contradiction in both directions: the gate does not pick a side between two
receipts, because picking one would be inventing the evidence it is meant to
read.

**It named a runtime by `device × runtime_id × family`.** That is one dimension
short of the two machines this release is about. Two Claude accounts under one
OS UID collapse into one row; Dell's `vince` (uid 1002) and `fjd` (uid 1000)
collapse into one row; and which project was actually exercised is not asked.
The census is now the six-dimensional managed matrix:

    Device × OSUser × family × credentialInstance × Runtime × Project

`uid` is carried beside `os_user`, and two rows claiming one OS account under
two different uids on one device are refused as a contradiction.

## What the checker refuses a manifest for

- independent V1 and Next Plugin IDs, release/backend identities and distinct
  HTTPS MCP endpoints; no accidental overwrite during side-by-side operation;
- actual MCP tool discovery of 33 distinct tool names and exactly the 6 scopes.
  A registered tool name is **not** a usable verb on a device;
- independently referenced **LIVE** receipts for full regression, the Next
  service cgroup readback, V1 health before and after, Next health,
  **Next-only rollback**, public HTTPS, OAuth discovery, **DCR + PKCE**,
  Feishu SSO login, token refresh, revocation, scope grants and refusals, tool
  exercise, Skills ZIP first install, approved avatar, **same-Plugin-ID ZIP
  update**, **backend-only update without plugin reinstall**, and short-window
  PSI with real per-project caps;
- a nonempty **dynamic** list of the managed runtimes the owner requires, each
  matched to a live observation with owner consent, credential isolation,
  reachability, effective model and permission mode, the projects it was
  exercised on, and every operation judged by the rule above.

A Codex runtime must name its **provider** and carry a receipt for it: a model
string alone is a claim, and Codex standing on a runtime with no provider is
not a Codex runtime. A profile kind of `other` is refused by name, so declining
to say what a runtime is cannot be a way to skip the model and permission rules.
Missing/unavailable Codex on Dell, or a second Claude account under one UID, is
not turned into PASS by another runtime's successful connection.

## What is deliberately not a V1 precondition

OpenAgents layering, long-horizon autonomous-agent self-governance, and
resource-trend forecasting or self-healing. Those are V2 questions, and a V1
gate that required them would hold the release to a roadmap. The resource half
that *is* a precondition is the one named check above — a short-window PSI
figure and real per-project caps — and the admission side of it lives in
`fleet_mcp/health_admission.py`.

## Admission advice is not authorization

`health_admission.assess` answers one question: may **one more** bounded run
start on this runtime, from measurements taken within the last 60 seconds. It
keys an admission by the same six dimensions, requires the owner grant and
credential state to be reported, requires Codex to have its provider, and
counts a **stuck session** as both an occupied slot and its own refusal reason —
counting it as a free slot is how a wedged runtime gets one more run, and
counting it as an ordinary running session is how a runtime at its ceiling goes
unexplained. Sessions whose liveness is not reported are `unknown`, never
zero.

Its `writes` is always empty and it has no path to starting anything. A
measurement with `source: LIVE` is a field this module requires; a fixture
cannot be field evidence, and nothing here claims it is.

The `REVIEW_CANDIDATE_ONLY` decision is **not** `PRODUCTION_READY`. This tool
cannot verify the authenticity of claimed receipts or the identity of the
person signing off. An independent human reviewer must inspect real
ChatGPT/Feishu/CloudCLI readbacks, cgroup limits, owner grants and failover
traces. A mock claiming `provenance: LIVE` is **not** authenticated evidence;
never release automatically based on this manifest. See
[`fleet-next-acceptance-runbook.md`](fleet-next-acceptance-runbook.md) for what
a human actually goes and looks at.

## Stable update policy

Register the independent private Next Plugin ID and HTTPS issuer **once**, keep
them stable for backend/runtime updates, and use a guarded **same Plugin ID**
ZIP Release update for Skills/support resources. Tool schema changes may require
tool refresh and additional scope consent; none of that makes new plugin
installation a routine deployment step.

This quality branch is separate from #192/#198 and must not modify V1 or
merge the main branch without review. All tests run offline, no Feishu/Tailscale
tokens or other OS accounts are consulted.
