# Fleet V1: owner access, project creation and durable delivery

## Problem Statement

The owner explicitly requests maximum native Agent permissions, configured once
for the managed accounts, including creating projects and sessions without a
second permission ceremony. Existing project-ID allowlists cannot admit a newly
created project. Local stdio currently grants only read/send/create although
the surface implements six scopes. Fleet's retry guard has a tested existing
AgentControlStore adapter, but production `build_service` never injects it.

This is an authorized implementation for SRV-0011, not a deployment receipt.
It builds on control/routing revision 69a18bc. Client Skills/package updates,
CloudCLI project MCP adaptation, Linux resident recovery and Windows native
adaptation have separate file owners and are integrated after review.

## Goals

- One explicit owner-max configuration for exact authenticated principals and
  device/runtime namespaces. It admits all implemented verbs, all real projects
  owned by that runtime, and its maximum supported native execution mode.
- Both Claude and Codex use the existing provider `bypassPermissions` mapping;
  Codex maps this to danger-full-access/never. Models retain the selected
  runtime's own defaults unless explicitly requested.
- New `fleet_create_project` tool, using named configured roots and a safe
  project name, followed by session creation without editing project allowlists.
- Production-owned durable delivery wiring that uses the existing Agent Control
  collection and atomic claim/completion, including a separate project receipt.
- Existing non-owner callers, account separation, unknown-outcome fencing and
  offline accepted replay remain valid.

## Non-Goals

No sudo/root elevation, credential transfer, new Work/queue/Ability database,
cross-account runtime fallback, automatic login, or production network changes.
Maximum native task permissions do not make an execution ACK a completed task.
ChatGPT registration follows local CC/Codex acceptance. Real deployment and
native self-healing receipts remain separately reported gates.

## Design

### 1. Explicit owner-max policy

`fleet_mcp/config.py` gains an optional `ProviderBinding.owner_access` profile
and `PolicyBinding.owner_max`, a mapping of exact principal names to explicit
device/runtime namespace keys. The new pure `fleet_mcp/owner_access.py` parses a
profile with `owner_approved: true`, a nonempty `project_roots` map and maximum
`permission_mode: bypassPermissions`. It validates safe host paths and project
names; it does not make directories or authorize a caller.

There are no principal/device/runtime wildcard grants. A Codex namespace served
by an explicitly multiplexed base binding still needs its own named owner-max
grant. Owner-max applies only to an enabled, configured, eligible device and its
declared profile. Existing independent profiles never inherit it.

For create, the authorization namespace is resolved BEFORE grant lookup:
an explicitly multiplexed base with `provider=codex` checks the Codex namespace,
not the actual Claude gateway tag. The actual binding continues to select the
account, project catalogue and family/model policy. The namespace and actual
binding runtime are separate variables through every handwritten/native path.

`FleetService._operator_grant_verbs` derives the five implemented operator verbs
for an owner-max entry. Existing explicit operator grants remain unchanged.
Local `owner_principal(config)` and HTTP owner-PAT carry all six implemented
scopes only when their respective exact principal (`local-owner` / `owner-pat`)
has an explicit owner-max entry. Their legacy defaults remain three scopes.
Other remote tokens still carry what their actual authorization issued. The
owner setup generator requests all six for the selected client rather than
silently changing tokens.

`_may_create` is made principal-aware. Owner-max no longer requires another
static project grant. For a new effect, the native project ID is verified against
that runtime's actual listing AFTER checking an accepted replay, so a provider
outage does not prevent replaying a recorded ACK. Existing local scope/profile
revocation checks remain before replay. Other principals keep the current project
ceiling.
The same effective policy is used by handwritten create, native create and
reconfigure. The maximum permission default is supplied before provider IO;
requested/resolved/effective remain different reported facts.

Policy precedence is explicit. The named owner-max profile replaces the static
project allowlist and its execution-mode restriction for that owner only; an
omitted mode becomes `bypassPermissions`. An existing exact runtime/project
rule still validates the requested native family and model, and supplies its
configured default model. Its old mode does not override the newly approved
owner-max mode. Otherwise an existing project's model ceiling remains, and a
new project with no model declaration accepts an omitted model (the real
runtime default), not an invented fleet-wide model. Reconfigure validates only
fields actually requested: it never fills an omitted model or mode.
An owner namespace multiplexed through a base gateway reads only its exact
typed-namespace model rule. Codex never borrows the base Claude model default;
without a Codex declaration it omits model and retains the native default and
any explicit project model ceiling. This does not broaden runtime selectors.

The Node Connector has its own local `owner_access` profile. It opens the
existing owner-session floors and implemented verbs once, verifies project IDs
against its actual provider, and permits newly created projects. A Fleet-side
grant does not override a closed Node. Node and Fleet share the pure parser;
their authorizing configuration remains local to each owner.

### 2. Project creation, not a shell

CloudCLI upstream v1.39.0 has no project-create MCP tool, but its authenticated
REST handler already calls `services/project-management.service.js#createProject`.
The independent CloudCLI adapter adds
`project_create(projectPath, customName, requireFresh=false)`
under its existing `cloudcli:session:create` scope, reusing that service and the
existing projects DB. No UI JWT or browser cookie is borrowed.

Fleet adds one authored tool: `fleet_create_project(device, root, name,
display_name?, runtime?, idempotency_key?)`. The catalogue becomes 34 tools;
the 22 captured native mirrors and two descriptors retain their existing source
contract. The six Fleet scopes remain unchanged: project creation uses
`fleet:create` and the exact owner's create grant.

The configured profile resolves `root` and the safe single-component `name` to
a host path. A caller cannot provide a path or command. CloudCLIProvider gains
an explicit `create_project` operation. FleetNodeProvider uses a new closed,
signed `POST /v1/projects` route; the Node independently selects its configured
root and performs local symlink/reparse checks. Native passthrough does not
acquire an arbitrary project-create exception.

The result carries a real project ID/path, creation outcome and the selected
runtime. Unsupported old providers report a capability gap before another
authentication route is attempted. Fleet requires a fresh destination: it sends
`requireFresh: true` and requires target-local exclusive directory creation
plus a `freshDirectoryCreated: true` ACK. A provider without that advertised
schema is explicitly unsupported. A missing project-list row is never proof
that a remote directory is absent. The Node independently enforces its local
root too. Destination-exists/symlink checks happen only for a new effect, after
accepted replay. Fleet does not reactivate an archived existing directory.
CloudCLI's default `requireFresh: false` separately preserves native archived
reactivation and its actual SQLite outcome; Fleet does not claim that separate
path was reached by its fresh-project API.

### 3. Durable owner wiring

Prefer the existing injection seam over new daemon mutation RPCs. A new
ServerOS-owned factory/launcher, `runtime/fleet_mcp_owner.py`, resolves the
existing control-store path from the ServerOS state-root producer and validates
an explicitly selected existing owner anchor. It constructs
`FleetService(config, attempts=AgentControlStore(existing_path))` and passes that
service to the existing HTTP/stdio entry points. The standard Fleet-only import
continues to work without importing ServerOS runtime modules.

The launcher must not create a new empty owner store on a typo, follow a symlink
or silently fall back to process-local state. It verifies ownership/access and
the store schema. An opt-in `require_existing` mode in the existing store retains
those checks on subsequent loads/mutations, including deletion before its first
write. Its resolved path agrees with `Daemon.control_store()` and
`runtime.cli._agent_control_store_path`. AgentControlStore already serializes
and reloads cross-process mutations under its sidecar lock. No new database or
grant producer is introduced. Packaging and service invocation explicitly opt
into this ServerOS-owned mode; status reports actual durability.

The current Fleet installer ships only `fleet_mcp` and `fleet`; it cannot run
this owner factory. A new `bin/serveros-fleet-owned` entry ships with the full
ServerOS runtime. Both `scripts/build.py`'s `CORE_MODULES` and
`plugin/bundle.yaml`'s module path include `fleet_mcp` alongside the existing
`fleet`/`runtime` dependency closure, and build acceptance starts the entry from
the built artifact outside the source checkout. No live service is pointed at a
factory absent from its actual release. The Fleet-only entry remains standalone
and reports PROCESS_LOCAL; the selected owner configuration uses the owned
entry and must fail explicitly when its existing store is unavailable.

`DispatchGuard` and `AgentControlStore.complete_dispatch_claim` add a closed
project receipt (`project_id`, `project_path`, `accepted`) for `project.create`.
DispatchGuard explicitly emits `project.create`, rather than constructing a
`session.*` spelling for it. Its owner claim accepts `expected_project_path`
only for that verb. The owner validates and retains this field under the same
mutation lock, separately from the unchanged `kernel.attempt_record` arguments.
Completion selects the receipt type from the STORED verb, and requires its path
to equal the recorded expected path. Session receipts remain unchanged and
cannot be completed or replayed with a project receipt. Both
types are validated against the original target; neither persists instructions
or raw provider payload. Claim occurs before remote effect, unknown stays
fenced, and accepted replay returns without provider IO after restart.

### 4. Setup and integration

A non-secret owner configuration generator creates a reviewable Fleet/Node
configuration update from explicit observed principals, runtime bindings and
roots. It sets the selected owner floors and maximum native mode once, preserves
other accounts, and does not fabricate login, consent or model availability.
The user's authorization covers applying these selected owner settings after
the concrete candidate is tested. Production rollout remains observable and
separate from source tests.

## Alternatives

- Removing all existing defaults globally would expand other callers and would
  not fix Node floors; explicit owner-max is narrower and complete.
- A generic shell or REST proxy would duplicate transport/authentication and is
  unnecessary because a native project service exists.
- New daemon claim/complete RPCs need a separate trusted actor producer. The
  existing owner injection and cross-process store lock avoid that expansion.
- A second retry file would split attempt ownership. The existing collection
supports both receipt types without changing its phase transition rules.

## Open Questions

The three actual owner-root/model/profile settings and deployment anchors are
read from devices before application. They are not guessed from hostnames.
CloudCLI's project outcome for archived paths is retained and tested; an
archived project is not claimed ready for sessions without an actual readback.
Client behavior, real long-task disconnect recovery, and real same-ID update
remain actual acceptance steps after the code paths are integrated.
