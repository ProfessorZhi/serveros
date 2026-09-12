# ServerOS

ServerOS is a lightweight **Agent Engineering Workbench** for Claude Code,
Codex, and future coding agents.

Its job is not to prescribe how an agent works.

Its job is to give the agent the right:

- **Environment**
- **Context**
- **Capabilities**

ServerOS optimizes for a small happy-path context and progressive,
on-demand expansion.

> Give the agent the minimum correct context required to start working,
> then let the agent discover and load additional capabilities when needed.

ServerOS is a capability map and workbench, **not a workflow state machine**.

---

## 1. Why ServerOS

A coding agent dropped onto a shared server starts from zero: it does not know
which directory is canonical, where its work belongs, which sibling resources
belong to someone else, or what the delivery target is. The usual fixes —
a giant onboarding prompt, a full filesystem scan, a per-agent private
workspace — all fail the same way: they make the agent's *first minute* slow,
and they let each agent invent its own world.

ServerOS exists so that **any** agent, on **any** runtime, can be handed a
small, correct, task-scoped context and start working — without the human
re-explaining the project.

## 2. Environment / Context / Capabilities

**Environment** — prepare the right place to work: canonical source repo, Item
worktree, test environment, toolchains, ports and runtime resources, evidence
location, delivery target.

> Where do I work, where does output go, and how do I avoid touching someone
> else's resources?

**Context** — hand the agent a small, accurate, task-relevant *Context Capsule*:
project, current Item, objective and acceptance, current milestone,
development repo, owned worktree, test-env, evidence path, relevant source
locators, delivery target, protected concurrent resources.

> Who am I, what am I doing, what is already known, and what must I not touch?

**Capabilities** — supply, on demand, what the current task needs: Skills,
`serverosctl`, Feishu, GitHub, test tools, workspace tools, information tools.

> Which reliable capabilities does this task need?

ServerOS deliberately does **not** decide the agent's internal method — whether
to grep or read docs first, test-first or experiment-first, whether to use
subagents, or in what order to analyze / design / build / verify. That stays
with Claude Code and Codex.

See [`ARCHITECTURE.md`](ARCHITECTURE.md).

## 3. Architecture

ServerOS has a **Core-2**, and then specialist capabilities discovered on demand:

```text
                 ServerOS Core-2
        ┌──────────────────────────────────────────────┐
        │ serveros                                     │
        │ Environment / Context contract               │
        │ bootstrap Skill                              │
        │ Context / Space / Relations kernels          │
        │ hwz-server-os composition + deterministic build│
        ├──────────────────────────────────────────────┤
        │ ability-management                           │
        │ Capability / Ability registry                │
        │ reusable Skill canonical source              │
        │ revision / compatibility / discovery         │
        │ reconcile / installation / distribution      │
        └──────────────────────┬───────────────────────┘
                               │ pinned build / install
                    ┌──────────┴──────────┐
                    ▼                     ▼
              Claude Code               Codex
              adapter/runtime           adapter/runtime
```

`workspace-management` is an important specialist capability, not a third Core
module. `serveros` decides **what composes the product**; `ability-management`
owns the reusable implementations that are composed into it. Generated runtime
payloads never become a reverse authority.

Full component boundaries: [`integration/components.yaml`](integration/components.yaml).

## 4. Context Capsule

The Context Capsule is the contract between ServerOS and any agent. It is a
small, stable schema — not a dump of maps, history, or Skill documentation.

→ [`docs/context-capsule.md`](docs/context-capsule.md)

## 4b. Space Resolver

Where the Capsule answers *"what is my working environment"*, the Space Resolver
answers *"where does this object canonically belong, and does it need to move"*.
It is a deterministic, read-only kernel: it reads no network, no database and no
map, and it invents no cloud path. Applying its decision — the actual MOVE, the
actual index write — is an adapter's job.

→ [`docs/space-resolver.md`](docs/space-resolver.md)

## 4c. Fleet

ServerOS owns stable device identity and fleet topology. Device connection locators
and credentials stay local-only; capabilities installed on each device are owned
by `ability-management` observations and projected back into the Fleet view.

→ [`fleet/README.md`](fleet/README.md)

## 5. Native-first Skill Discovery

ServerOS **strengthens** each runtime's native Skill discovery. It never
replaces it, and it never narrows it.

→ [`docs/capability-discovery.md`](docs/capability-discovery.md)

## 6. Claude / Codex parity

> Same capability, same semantics, same context; different runtime adapter
> only.

→ [`docs/claude-codex-parity.md`](docs/claude-codex-parity.md)

## 7. Workspace layout

```text
HOME=/home/hwz
DATA_ROOT=/opt/applications/data/home-hwz
WORKSPACE_ROOT=$DATA_ROOT/workspace
ARTIFACTS_ROOT=$DATA_ROOT/artifacts
STATE_ROOT=$DATA_ROOT/state/serveros
```

- `WORKSPACE_ROOT` carries only `src/ worktrees/ test-envs/ ops/ knowledge/ records/`.
- `src/` = canonical Git repositories.
- `worktrees/<repo>/<ITEM>/` = per-Item development checkouts.
- `test-envs/<repo>/<ITEM>/` = per-Item test environment definitions.
- Evidence lives under `DATA_ROOT`, never under `WORKSPACE_ROOT`.

## 8. Maps

`HWZ-MAP` (structure) · `TREE-MAP` (semantics) · `FEISHU-MAP` · `GITHUB-MAP`.

Maps are **generated projection views**, not independent authorities. Their
inputs are machine contracts / semantic registries / observed inventory; the
projection compiler renders the human map regions from those inputs. A normal
session does not load maps wholesale: the Context Capsule projects only the
small slice relevant to the current task.

## 9. Safety and concurrency

The server is shared by concurrent agents and Items.

→ [`docs/safety-and-concurrency.md`](docs/safety-and-concurrency.md)

## 10. Repository and component boundaries

| Repository | Role |
|---|---|
| `ProfessorZhi/serveros` | product entry; **hwz-server-os composition canonical** (`plugin/bundle.yaml`); bootstrap Skill; Context/Space/Relations kernels; deterministic build |
| `ProfessorZhi/ability-management` | Capability/Ability registry; **reusable Skill canonical source**; revision/compatibility/discovery/reconcile/installation/distribution |
| `ProfessorZhi/work-engine` | `serverosctl` deterministic control plane for Item/Activity/Run state and projections |
| `ProfessorZhi/hwz-ops` | this machine's topology/policy instance and map projection targets |

`ability-management/plugins/hwz-server-os` is only a transitional/generated
compatibility surface. It is not a second plugin canonical.

Repo *relationships* are not workspace *ownership* — see
[`docs/repository-roles.md`](docs/repository-roles.md).

## 11. Non-goals

ServerOS is not, and must not become:

- a global workflow engine or a forced Activity pipeline
- a daemon, a lease system, an approval layer, or a new directory layer
- a second source of truth for Skills, maps, or Item state
- a document generator that manufactures artifacts to close a schema
- a mechanism that slows an agent down in order to be thorough

Before adding anything, ask: **does this improve Environment, Context, or
Capabilities?** If none of the three, it is probably not ServerOS core.
