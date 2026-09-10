# Context Capsule

The Context Capsule is the contract between ServerOS and any agent runtime. Its
purpose: let an agent start working in **tens of seconds**, without the human
re-explaining project history.

## Design rule

> The Capsule carries the **minimum correct context required to start working**.
> Everything else is discovered and loaded on demand.

## Schema

A small, stable schema. Do not extend it casually.

```yaml
project: litellm

item:
  id: LIT-0101
  title: cache-read token 写入 LiteLLM SpendLogs

objective: ...

development:
  repo: litellm-bridge
  worktree: /.../LIT-0101
  branch: ...

test:
  environment: /.../LIT-0101

evidence:
  root: /.../LIT-0101/

delivery:
  repo: litellm-fjd
  target_branch: update/100-dev

current_milestone:
  locator: ...

relevant_sources:
  - ...

protected:
  - LIT-0103
  - LIT-0104

suggested_capabilities:
  - work-management
  - test-management
```

## What must NOT go in the Capsule

- full history
- all maps
- all Skill documentation
- all Items
- all GitHub repositories
- the ServerOS architecture text
- any secret value

## Field semantics

| Field | Meaning | Source of truth |
|---|---|---|
| `project` | business/governance unit | `records/projects/<project>/` |
| `item` | stable Item identity and title | `items/<ITEM>/card.yaml` |
| `objective` | goal + acceptance, condensed | card / brief |
| `development` | the `development`-role repo and its owned worktree and branch | `projects.yaml` `repo_roles` + Git |
| `test` | this Item's test environment | `test-envs/<repo>/<ITEM>/` |
| `evidence` | absolute evidence root for this Run | `artifacts/work-items/<project>/<ITEM>/<RUN>/` |
| `delivery` | the `delivery-target` repo and target branch | `projects.yaml` `repo_roles` |
| `current_milestone` | stable locator of the latest milestone | records |
| `relevant_sources` | locators worth reading, not bodies | information/understanding |
| `protected` | concurrent resources this agent must not touch | **resolved dynamically from current state** |
| `suggested_capabilities` | advisory hints, never an allowlist | ability registry |

## `protected` is dynamic

Protected concurrent resources are **read from current state at resolve time** —
from Item state, leases, and the protection policy. They are never hardcoded
into a global `CLAUDE.md` / `AGENTS.md`, because a hardcoded list goes stale
silently and then lies to the agent.

## `suggested_capabilities` is a hint

It is advisory. See [`capability-discovery.md`](capability-discovery.md).

## Happy-path budget

Resolving a Capsule for an existing Item must cost:

1. one context resolve
2. a small Capsule
3. native Skill discovery
4. start working

It must **not** trigger a full filesystem scan, a full GitHub or Feishu
inventory, multi-reviewer passes, cleanup scans, or a full map regeneration.
Slow governance runs when needed, or reads an existing current projection.
