# Claude / Codex Parity

## Principle

> **Same capability, same semantics, same context; different runtime adapter
> only.**

ServerOS is *one capability system with two runtime adapters* — not two systems.

## What both runtimes share

- Environment contract
- Context contract (Context Capsule schema)
- maps
- project / Item identity
- worktree / test-env placement
- evidence semantics
- Skill semantics
- concurrency protection

## What may differ

Only adapter files:

```text
.claude-plugin/*      CLAUDE.md adapter
.codex-plugin/*       AGENTS.md adapter
```

If a behavior difference cannot be expressed as an adapter file, it is a bug in
the canonical source, not a legitimate runtime difference.

## Dual-runtime smoke

Run this **once per important ServerOS / plugin release** — not per business
task.

Claude Code and Codex each verify:

1. `hwz-server-os` loads
2. it resolves the **same** Item
3. same `project`
4. same `development` repo
5. same `worktree`
6. same `test-env` / `evidence`
7. current milestone / source locator is visible
8. protected concurrent work is visible
9. native Skill discovery still works
10. ServerOS suggestion does **not** restrict native capability

Passing is sufficient. The two runtimes are **not** required to fully execute
the same business task.

## The real acceptance criterion

> Claude works half-way and exits; Codex can take over on the same Item,
> worktree and evidence. And the reverse holds too.
