# work-engine

Autonomous Delivery Workflow v0 — **C1 Work Engine**: a persistent Work Item
model + state machine + cross-session `resume`.

**First goal = reliable cross-session resume.** A session can die at any time;
a brand-new session resolving the same Work Item id must be able to find the
worktree, read the evidence pointers, and know the legal next transition.

Pure Python (>= 3.11), cross-platform, **no third-party dependencies**.

---

## What this engine does

- Persists a **Work Item** to local JSON (per-id files in a `state/` directory,
  located by absolute path).
- Enforces a strict **state machine** — illegal transitions are rejected.
- Provides a small CLI: `create / status / advance / resume / list` (+ `evidence`).
- Exposes stable seams for the future server façade (`WorkRequest`) and the
  Ability Resolver (`resolve_capability`) — **without hardcoding any skill names**.

It does **not** run real skills, take Feishu intake, or open PRs. Those are
later vertical slices; C1 is only the lifecycle + state machine + resume.

---

## Quick start

```bash
# Create a Work Item in a state directory
python -m workengine --state-dir ./state create \
  --title "Ship C1" \
  --requirement "Work Engine must survive a session crash." \
  --repo work-engine --worktree /abs/path/to/worktree \
  --plan "create scaffold" --plan "add state machine"

# Advance through the chain
python -m workengine advance WI-XXXX --state-dir ./state --to TRIAGED --note "triaged"
python -m workengine advance WI-XXXX --state-dir ./state --to READY

# Resume for a fresh session (new process) — recover worktree, evidence, next step
python -m workengine resume WI-XXXX --state-dir ./state
```

`--state-dir` and `--json` may appear **before or after** the subcommand.

Grab an id from `list`. `--json` emits machine-readable output.

---

## State machine

```
NEW → TRIAGED → READY → IMPLEMENTING → VERIFYING → PR_OPEN
    → REVIEWING → READY_TO_MERGE → MERGED
```

Forward moves are **one step only** (no skipping). Bypass states `BLOCKED` /
`NEEDS_HUMAN` may be entered from any live state and re-entered into any live
forward-chain state, or into each other / `CANCELLED`. `MERGED` and `CANCELLED`
are terminal (no outgoing transitions). An illegal move raises
`workengine.state_machine.IllegalTransition`.

See `workengine/state_machine.py` (`TRANSITIONS` rule table) and
`tests/test_state_machine.py` for the full allowed/forbidden matrix.

---

## Package layout

```
workengine/
  __init__.py            public exports (stable interfaces)
  model.py               State enum + WorkItem dataclass + now_iso
  state_machine.py       FORWARD_CHAIN, TRANSITIONS, can/transition, IllegalTransition
  store.py               WorkItemStore (atomic JSON persistence, absolute-path dir)
  engine.py              WorkEngine (create/advance/resume/list), default_state_dir
  resolver.py            AbilityResolver.resolve_capability
  request.py             WorkRequest.submit(requirement) -> WorkItem
  cli.py                 argparse CLI
  __main__.py            python -m workengine entry
tests/                   unittest suite (37 cases, hermetic)
demo/resume_demo.py      re-runnable cross-session resume demo
```

State directory resolution order:

1. `WORKENGINE_STATE_DIR` environment variable (absolute path).
2. CLI `--state-dir`.
3. Default: `<repo>/state/` (gitignored; runtime data, not source).

---

## Cross-session resume

The demo `demo/resume_demo.py` (run from repo root) verifies this in the
strongest way — each CLI call is a **separate OS process**:

1. Session A: `create`, advance to `IMPLEMENTING`, attach 2 evidence pointers.
2. Session A ends (no in-memory state survives).
3. Session B (fresh process): `resume <id>` → prints worktree path, evidence
   pointers, and `next transitions`.
4. Session B continues the chain (`IMPLEMENTING → VERIFYING`).

```bash
python demo/resume_demo.py
```

---

## Stable interfaces (future façade / resolver)

- `WorkRequest` — `submit(requirement, *, title, repo, worktree, plan, item_id) -> WorkItem`.
  The seam a server façade uses to turn a requirement into a Work Item.
- `AbilityResolver` — `resolve_capability(capability)` (and `register(...)`).
  The registry starts empty; callers wire in capabilities. **No concrete skill
  names appear anywhere in this crate.**

---

## Tests

```bash
python -m unittest discover -s tests -v
```

37 tests, all green: model serialization, the full state-machine rule table
(forward steps, no-skip, terminal, bypass in/out), store round-trips across
fresh instances, cross-session resume, the WorkRequest/Resolver seams, and CLI
(argparse + `--state-dir` isolation + illegal-advance rejection).

---

## Boundaries (C1)

- No Feishu intake, no requirement understanding, no E2E leasing, no
  multi-round review, no learning.
- No real skill execution — lifecycle + state machine + resume only.
- No dependency on ability-management / workspace-management internals.
- No push. Committed locally to `main` only.
