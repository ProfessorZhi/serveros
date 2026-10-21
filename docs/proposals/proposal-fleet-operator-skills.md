# Fleet Operator Skills and offline package/update workflow

## Problem Statement

The shared Fleet Skills teach discovery, session reuse, creation, evidence
reading and safety, but they do not teach a commander how to run several
independent tasks without becoming a polling loop. An assistant that dispatches
one short task and waits has stopped coordinating. After a create/send ACK it
must preserve the exact execution coordinates, return, and inspect later; a
timeout is not evidence that the work failed and is never permission to resend.
A resumed session needs the original coordinates rather than a fresh guess.

The package side has a related gap. There is one canonical Skills directory,
but the same-ID update, readback and rollback checks are described in prose and
are not executable offline. An operator cannot prove locally that an update
retains the plugin id, advances the version, keeps the old Skills and restores
the previous release if readback disagrees.

## Goals

- Keep **one shared Skills source** at
  `integrations/fleet-v1-first-install/plugin/skills/`; the portable
  ChatGPT/Codex first-install package and the Claude Code development adapter
  both consume those bytes.
- Add commander guidance that:
  - gathers the minimum facts and coordinates people/models;
  - builds a task dependency graph;
  - dispatches several independent complete long tasks, or a batch of
    independent short tasks, rather than repeatedly dispatching one short task
    and waiting;
  - prefers Fleet, uses RDC only for a necessary GUI/fallback path, asks
    targeted questions instead of forcing a global status read, and lets one
    slow node finish without blocking other nodes.
- Add an async-dispatch contract that preserves
  `device / runtime / session_ref / run_id / idempotency_key` in the existing
  Work/checkpoint, returns promptly after ACK, and later inspects the run.
  Explicitly distinguish delivery ACK from business completion. A timeout or
  `EFFECT_UNKNOWN` does not permit a fresh-key resend.
- Add resume guidance: a new session resumes from existing execution
  references; a Skill is not a persistent scheduler, and a dependency exists
  only when an actual continuing Agent or verified mechanism carries it.
- Add an offline package/update workflow that builds a synthetic local test
  package, demonstrates a same-plugin-id version update, verifies a simulated
  readback, and prepares a rollback check. It never uploads, installs, changes
  OAuth/services/routes, or claims a Creator operation.
- Keep App-backed **Skill/version** updates separate from **avatar/display-name**
  changes, which are owned by the App/Creator path and remain UNVERIFIED here.

## Non-Goals

- No change to `fleet_mcp/`, `runtime/`, `fleet/`, WorkEngine or Core.
- No new MCP tool name, batch verb, scheduler, automatic dependency runner or
  durable execution claim.
- No plugin publication, installation, OAuth consent, service/route change,
  paid model probe, merge or deployment.
- No claim that a Skill keeps running after its host exits, or that a timeout
  is a terminal state.
- No invented plugin/App id or release id. Local fixture identities are
  synthetic and are labelled as such.

## Design

### Shared Skills source

`plugin/skills/` is the only authored copy. The CC builder already reads it and
the first-install builder packages the surrounding `plugin/` tree. Tests will
pin both properties: every generated `SKILL.md` is byte-identical, and the
portable package's skill list equals the shared source's immediate children.
The source must remain shallow (`skills/<name>/SKILL.md`), because neither
client searches deeper.

Three Operator Skills are added:

| Skill | Responsibility |
|---|---|
| `fleet-commander-orchestration` | Gather only the facts needed, build a dependency graph, dispatch independent complete work in parallel or short independent work as a batch, route Fleet-first, use RDC only as a GUI fallback, and keep slow nodes isolated. |
| `fleet-async-dispatch` | Define the ACK/checkpoint record, return promptly, inspect later, treat timeout/UNKNOWN as unresolved rather than failed, forbid fresh-key resend, and resume from execution references. |
| `fleet-plugin-update` | Separate App-owned avatar/display changes from Skill/version updates; require same plugin id, version advance, Skills continuity, readback and a rollback target. |

The existing five Skills keep their names and package membership, and remain the
authority for discovery, reuse, creation, evidence and safety. Their text is
corrected where it contradicts the async/commander semantics: reuse preserves
the ACK and inspects later rather than waiting for settlement; discovery uses a
targeted read when the target is known and only diagnoses a failure; one
message may carry a complete long delivery or a batch of independent short
tasks; ability authority is the shared `ability-management` catalogue plus a
per-device installed/ready/granted projection, not a per-device registry; and
model/mode are effective values read back per owner/runtime/project, never a
fleet default. New Skills link to the old ones instead of restating their
gates.

### ACK and checkpoint contract

A create/send ACK is a **delivery receipt**, not a result. The checkpoint is an
existing Work item/checkpoint record, not a new database. It preserves at
least:

```text
device_ref
runtime
session_ref          # when create returns one
run_id               # when the provider returned a run
queued_message_uuid  # when the provider returned one
idempotency_key      # exactly the value sent, never regenerated on retry
idempotency_durability # PROCESS_LOCAL | AGENT_CONTROL_STORE | NOT_REQUESTED;
                       # absent means UNKNOWN on an older service
verb                 # create | send
intent_digest        # if the caller has one from the Fleet receipt
accepted_at
last_known_state      # ACKED | RUN_UNSETTLED | EFFECT_UNKNOWN | SETTLED_*
```

The commander returns after the ACK. Later inspection uses `fleet_get_run`
and `fleet_read_session`; it does not spin, and a still-unsettled run is not a
failure. A timeout, transport error, malformed ACK or missing receipt leaves
the state `EFFECT_UNKNOWN` and preserves the original key. The same key is
necessary, not a cross-restart guarantee: `PROCESS_LOCAL` is lost on restart,
`AGENT_CONTROL_STORE` may replay from a verified owner record, and absent means
`UNKNOWN`. Only the owner's reconciliation path may resolve an unknown effect;
a retry with a fresh key is a
second delivery. A `RUN_NOT_FOUND` or `never_issued` answer from a run read is
not proof that the task did not run: the native history or artefact may show
completion, so reconciliation reads those before any retry.

### Dependency graph

The graph is expressed in the existing Work/Parent structure. An edge is
admissible only when:

- the downstream task explicitly lists the upstream execution reference; and
- a continuing Agent or a verified mechanism is responsible for observing the
  upstream terminal state; and
- the downstream dispatch has a trigger that survives the commander's turn.

A Skill can describe this graph but cannot be the trigger. A dependency with no
continuing agent is a plan, not an executable edge; the commander says so.

### Package/update rehearsal

The overlay builder remains the release builder. A new offline checker reads
two overlay archives/release records and performs:

1. **same-ID plan** — equal `plugin_id`, `name`, `displayName` and `apps`;
   a complete protected manifest projection (root and `.codex-plugin`
   repeated effective fields), `mcp.json`/`.mcp.json` structure and referenced
   files, and every non-Skill member byte; strictly increasing semver; the
   previous Skills are all still present; the expected archive hashes match
   the records. App-owned title, descriptions, prompts, presentation, MCP and
   connection data are not overlay-updatable; backend/tool changes remain
   server deployment.
2. **readback check** — compare an explicitly labelled readback receipt
   (plugin id, previous release id, new release id, version, Skills and archive
   hash) against the plan. A missing or contradictory field is a refusal, not a
   pass. The receipt is marked `production_verified: false` because no Creator
   call is made here.
3. **rollback check** — prove the previous archive still has the same plugin
   id, a lower version and its recorded hash, so it is a valid rollback target.
   It emits a plan only; it does not execute a rollback.

A local rehearsal generator uses the synthetic overlay fixture, builds two
versions, synthesizes a readback receipt from the second archive, and runs the
three checks. Before any copy or write it validates the source tree and shared
Skills for symlinks, including every existing ancestor path, forbidden members
and boundaries, and it requires a fresh empty output whose path (including
existing parents) has no symlink and does not overlap the source's **real
resolved** path in either direction. It never removes a caller-owned directory.
MCP-referenced local files are POSIX-normalized; dot segments are resolved,
traversal/escape is refused, and the normalized target bytes are compared even
when the target lives under a mutable skill. The output is local evidence; it
discharges no live update.

## Alternatives

- A new scheduler or a batch MCP verb would duplicate Work ownership and is
  outside the Operator Skills lane.
- A second copy of the Skills under the CC plugin would drift; the builder
  already supports one source.
- Treating a readback receipt as a live Creator observation would overclaim.
  The checker records the provenance and refuses contradictory fields, but
  cannot authenticate the operator's claim.

## Open Questions / Unverified

- The private Creator's exact same-ID update and rollback behaviour remains
  **UNVERIFIED**; this work produces only local, deterministic checks and
  operator-facing steps.
- Avatar/display-name update on the App-backed path remains **UNVERIFIED** and
  is not repairable by an overlay ZIP alone.
- Real three-device long-task/update acceptance remains an integration gate,
  not something a Skill or a local fixture can discharge.
