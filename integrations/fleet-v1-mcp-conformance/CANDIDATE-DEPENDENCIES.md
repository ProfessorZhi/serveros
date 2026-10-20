# Fleet Next — unified candidate dependency graph (GitHub-only, rollback-able)

Read at **2026-10-17T00:2x+0800** from the live repo (`git fetch origin`), not from
notes. Every SHA below was read back from GitHub in this run.

This file is orchestration only. It merges nothing into `main` or into A, and it
is the record of what a candidate contains and what it deliberately does not.

## The four things that are NOT the same thing

| | what it is | value now |
|---|---|---|
| `origin/main` | the default branch | `e29c788b4d3a` |
| A — `feat/fleet-v1-integration-20261015` | the non-production integration branch | `008a3360be19` |
| **installed Next canary** | the revision the running unit serves | `fleet-mcp-20261016@008a336` (previous `@0e3b55b`) |
| **PUBLIC Next on 3411** | a live surface, reachable | unit `active`, loopback `/health` 200 |
| old Fleet 0.1.0 | a different surface on :3401 | `active`, 200 — *not evidence about Next* |

**The installed revision equals A.** No PR below is installed anywhere. A green
call against the old Fleet says nothing about Next's write permission, and a
mock in a test says nothing about either.

## Dependencies, by SHA

```
A 008a336
├── #214  08a7413   core-lite readback seam
│    └── #216  98cbb80   core-lite source seam
│         └── #228  f08f903   gpu reader: the rows the producer emits
│              └── #229  b0f5480   MCP JSON-RPC conformance candidate (19 checks)
│                   └── [#231 stack marker] a11a9dd
│                        └── #231  7e75455   signed Core route + fleet-mcp-4
└── #225  69a9d0a   mirror runtime addressing + blank-runtime fail-closed
                        │
                        └── unified candidate  integration/fleet-v1-candidate-20261017
```

* **#225 is already in this lineage** — verified by CONTENT, not by ancestry
  alone: the mirror's `runtime=args.get("runtime")`, the four
  `_validate_runtime_selector(runtime)` call sites and the blank-runtime refusal
  are all present in the tree. The merge of it reports "Already up to date".
* `#214 → #216 → #228 → #229 → #231` is a real chain: each base is the previous
  head, so a candidate built on #231 already contains all of them.
* **Not merged, deliberately:**
  * **#233** `36a206d3ac42` — base is `feat/v13-two-surface-integration-candidate-20261014`,
    which is D's line, not B's. It is a consumer of this work, and pulling it in
    would mean touching another owner's branch.
  * **#230** `def1b96a7d57` (base #227 `7712e64d9409`) — the plugin lane is F's,
    and it is read-only from here.

## File-conflict audit for this candidate

| file | who else owns it | conflict |
|---|---|---|
| `fleet_mcp/fleet.py`, `tools.py`, `core_lite_readback.py`, `core_lite_source.py`, `node_protocol.py`, `providers/fleet_node.py`, `node/connector.py` | B | none |
| `runtime/coordination.py`, `fleet/agent_control_router.py` | **A (#237)** — present on A and on main | **B does not touch these** |
| `context/resolver.py`, work-engine | A | not touched |
| `native/**` | C (Dell) | not touched |
| monitoring / intake / journey / plugin Skills | D / E / F | not touched |

The merge was **conflict-free**. Whether it stays so is a function of A's second
round landing in files this branch also carries — `fleet_mcp/**` is where that
would show, and this document is the place it would be recorded.

## Gap status, from source

| gap | status |
|---|---|
| **1.** 33 tools / 6 scopes advertised vs the OAuth grant actually issued; 22 mirrors routed by device × runtime × project × verb | **correct in source.** `scopes_supported` (the six) and `scopes_grantable` (the binding's ceiling) are separate keys, and issuance is `requested ∩ granted ∩ grantable`. `_gate_native_write` passes `runtime` into `_may_create(device, project, runtime)` and `_require_operator_verb(..., runtime=)`. |
| **2.** signed Core source — Linux verifiable; Windows has no `O_NOFOLLOW` | **explicit `UNSUPPORTED`**, not a silent weakening: the reader refuses by name on a platform without `O_NOFOLLOW`, because Windows connectors are a real target and a route that reads an unverifiable path is the failure it exists to prevent. |
| **3.** `requested` vs `resolved` vs `effective` for model / `permissionMode` | **closed for `fleet_session_create`** in #231 `7e75455`: the three are kept apart and `effective` is null ALWAYS, never inferred. **Still open for the native MIRROR**, whose create branch has the same loss; closing it needs a decision — the mirror returns the device payload verbatim, so the triple would have to go to the audit record, and naming new audit fields is a disclosure-table change. |

## Rollback

Every branch here is a Draft. Nothing is installed. Rolling this candidate back
is: stop using the branch. Rolling back an *installed* revision is the existing
`previous` pointer (`@0e3b55b`), which this run did not touch.
