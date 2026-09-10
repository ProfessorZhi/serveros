# Repository Roles

## The core distinction

> **A repo *relationship* is not workspace *ownership*.**

A project may be associated with several repos. Association does **not** mean
every associated repo gets a worktree, a directory tree, or a long-lived
development branch.

## Roles

Every project declares a role per repo:

| Role | Meaning | Default worktree |
|---|---|---|
| `development` | carries the Item's default development worktree | **yes** — `worktrees/<repo>/<ITEM>/` |
| `delivery-target` | enterprise / upstream delivery surface | **no** — on-demand only |
| `analysis` | read-only analysis repo | none |
| `supporting` | supporting material | none |

Declaration lives in the project registry as `repo_roles` (see
`ops/workspace/serveros/projects.yaml`). The registry annotates repos already
listed as `canonical_repos` / `enterprise_source`; it does not create a second
repo list.

## Rules

- A business Item's default development worktree is created **only** under the
  `development` repo named by `worktrees_under`.
- A `delivery-target` repo (enterprise original / upstream) gets **no** Item
  worktree by default. Only when a real PR or integration validation needs a
  local checkout does a **temporary** PR integration context appear — and it is
  retired once used.
- It remains structurally legal for one Item to have worktrees in several
  repos. That is an on-demand exception, not a rule to replicate a full set
  into every associated repo.
- Never copy an Item's directory (card / brief / design / plan / Run) into an
  enterprise or upstream repo, and never create a long-lived development branch
  there.
- An Item with no Git involvement has **0** worktrees.
- `worktrees/<repo>/<ITEM>` and `test-envs/<repo>/<ITEM>` are dynamic canonical
  Item children, **not** drift. New Items are discovered through state, Git and
  inventory; they do not require a prior static map edit.

## Example

```yaml
# project: litellm
canonical_repos: [litellm-bridge]
enterprise_source: litellm-fjd
worktrees_under: litellm-bridge
repo_roles:
  litellm-bridge:      {role: development,     worktree: item}
  litellm-fjd:         {role: delivery-target, worktree: on-demand}
  litellm-log-analysis: {role: analysis,        worktree: none}
```

`litellm-fjd` is a real relationship — it is where the change must ultimately
land — but it owns no worktree on this server.
