# Wave 16 · Legacy/Next dual-track release-safety preflight (offline)

Date: 2026-10-28
Scope: `ProfessorZhi/serveros` source and offline tests only.
Role: independent release-safety QA. This document is **not** an RC
integration, **not** the #495 QA pass, and **not** a Legacy production
operation.

Base: `main @ e69fc0337ae5d33000478e3d5162077f525bc8cc`

## 0. Read this first

Everything below was produced against fakes. No install was run, no service
manager was addressed, no port was bound, no socket was opened, no credential
was read, no Feishu message was sent, no device was contacted, and no routing
was changed. **A green result here is not a real-device pass.** Where a claim
could only be settled on a host, it is listed as `NOT_RUN` with the reason.

Real co-install of Next beside Legacy still requires **separate user
permission**. This gate only refuses shapes that cannot be safe; it cannot
authorise one that might be.

## 1. Verdict summary

| # | Item | Verdict |
|---|---|---|
| 1 | Eight-resource collision veto (path/port/socket/backend id/audience/cred state root/lock/unit) | **VERIFIED (offline)** |
| 2 | Next shadow `create` / `send` effects | **VERIFIED = 0 (offline)** |
| 3 | Legacy `get_session` / `get_run` / `send` across Next upgrade, failed install, rollback | **VERIFIED unchanged (fake, offline)** |
| 4 | New Timer not activated, no side effect | **VERIFIED (offline gate); host state `NOT_RUN`** |
| 5 | Hub as independent release unit | **BLOCKED** — imports the Node package |
| 6 | Node as independent release unit | **BLOCKED** — imports 10 Hub modules |
| 7 | Sidecar as independent release unit | **BLOCKED** — import-independent, but no revoke entrypoint |
| 8 | Reproducible release unit (byte-identical manifest, twice) | **VERIFIED for all three** |

The one finding that changes a release decision: **there is no independently
releasable unit today.** Hub and Node import each other
(`fleet_mcp/owner_setup.py` → `fleet_mcp.node.connector`, and
`fleet_mcp/node/connector.py` → seven `fleet_mcp.*` modules + `fleet_mcp`
itself), so "release the Node on its own" is currently the whole repository
wearing a smaller name. The Sidecar's *bytes* are independent (stdlib only) but
undoing it is a manual act with no reviewed entrypoint in the tree.

## 2. Inputs and what they were used for

| Input | Used as |
|---|---|
| Legacy Fleet v0.1.0 keeps serving ChatGPT control | The invariant under test — it must not move |
| #478 dual-track Owner rules | Two tracks, two placements, two answers |
| #483 `644ee4a8` placement guard / per-track rollback / shadow refusal | Vocabulary and refusal codes reused verbatim |
| #480 `3777d134` Next-plugin separate id / shadow refusal | Code spelling (`NEXT_SHADOW_WRITE_FORBIDDEN`) |
| #486 `94937dd7` offline composition, 106 suites | Composition pattern (importlib from path, no package needed) |
| #494 `00503f91` RC 4/5 | Context only; nothing here re-scores it |
| #497 / #498 / #499 fix candidates | Not touched, not merged, not re-verified here |
| #431 node/hub artifacts | Motivated the Hub/Node/Sidecar axis |
| #473 / #474 ⇒ V2 #407 ancestry | Not mergeable directly; no assumption made that it is |

Constraint honoured: only `tests/`-level and `integrations/`-level additions in
this PR. **No other author's file was edited or deleted.**

## 3. Resource collision veto — offline proof

`resource_matrix(legacy, next)` returns `OK` or `BLOCKED` with every shared
resource reported at once. A representative takeover attempt:

```json
{
  "decision": "BLOCKED",
  "code": "BLOCKED_RESOURCE_COLLISION",
  "collisions": [
    ["port", "both tracks listen on port 8101"],
    ["backend_id", "both tracks answer as plugin_legacy_000000001"],
    ["oauth_audience", "both tracks are the protected resource https://legacy.example.invalid/mcp"],
    ["cred_state_root", "next cred_state_root /srv/legacy/state overlaps legacy install_path /srv/legacy"],
    ["unit", "both tracks are supervised as serveros-fleet-mcp.service"]
  ]
}
```

Two properties that a single-field check would miss, both verified:

* **Cross-class containment.** `cred_state_root` inside `install_path` is
  reported even though the field names differ.
* **Inverted polarity on identity.** A differing `device_ref` yields
  `BLOCKED_DEVICE_DISAGREEMENT` with an empty collision list — it is a *wrong
  host*, not a shared resource:

```json
{"decision": "BLOCKED", "code": "BLOCKED_DEVICE_DISAGREEMENT", "collisions": []}
```

This matches `fleet_mcp/deploy/next_install.py`, which refuses a canary start
when the Next binding's `device_ref` differs from live V1's.

## 4. Next shadow: `create` and `send` write nothing

```json
{"decision": "OK", "code": "OK", "writes": 0, "observed": 1, "refused": 2,
 "gateway_calls": ["get_session"],
 "codes": ["NEXT_SHADOW_WRITE_FORBIDDEN", "NEXT_SHADOW_WRITE_FORBIDDEN"]}
```

`create` and `send` were refused **before** the gateway was reached — the
gateway's own call log shows only `get_session`. Granting the write verbs
explicitly (`granted=["create","send",...]`) does not change the outcome; that
case is a test, not a comment.

## 5. Legacy contract across Next lifecycle (fake, idempotent)

Steps driven against one Legacy fake: `next-install-failure`, `next-upgrade`,
`next-rollback`.

```json
{"steps": [["next-install-failure","OK","OK",[]],
           ["next-upgrade","OK","OK",[]],
           ["next-rollback","OK","OK",[]]],
 "deliveries": ["intent-1"],
 "pointer_after_failure": "/rev/next@1111111"}
```

Reading: no step moved any of the three answers; one intent produced **one**
delivery across the whole lifecycle; a failed install left the `current`
pointer where it was; upgrade recorded `rollback_version 1.0.0` and rollback
restored it exactly.

This is a fake, and the fake is idempotent **by key** — that is the point.
`send` is compared by "one receipt per intent", not by "the string did not
change", because a string comparison would pass on a gateway that quietly sent
twice with identical bytes.

### Positive control

A gate that can only say `SAME` is indistinguishable from no gate. `survival()`
is therefore driven against a fake whose `get_run` answer really does change:

```
drift detected -> decision BLOCKED, code LEGACY_CONTRACT_DRIFT, drifted ("get_run",)
```

## 6. Timer

Every `*.timer` in the tree is found (6):

```
serveros-alert-sync  serveros-continuation-wake  serveros-daily
serveros-evening-alignment  v13-live-heartbeat  v13-report-period
```

Gate requires `enabled == False` **and** `active == False` for each — an
enabled template timer starts itself at the next login with no approval, so
"not started now" is the wrong property. Arming either flag blocks; a plan
that would `enable` or `start` one blocks before anything runs. The gate only
ever asks (`show`); `writes` and `executes` are constants, not measurements.

Host-side state of these timers: `NOT_RUN` (would require a real service
manager session; out of scope and explicitly forbidden this round).

## 7. Hub / Node / Sidecar — reproducible, but not independent

Ref `e69fc0337ae5d33000478e3d5162077f525bc8cc`. Manifests are git **blob
SHAs**, so they are identical on any checkout of the commit (a worktree hash
would move with `core.autocrlf`).

```bash
python integrations/fleet-next-wave16-release-preflight/wave16_preflight.py --resolve
```

| component | files | tree_sha256 | reproducible | revocable | independent | verdict |
|---|---:|---|---|---|---|---|
| Hub | 49 | `sha256:19e276ab8c454eb42abb8426011bce854348bed345d85d42afaad03060f2eddd` | yes | yes (`install.py`, `next_install.py`) | **no** | **BLOCKED** |
| Node | 180 | `sha256:581034a4be7966678c8669d374509466fbbefff6d094e3aad2e09482464d4688` | yes | yes (`uninstall.sh`, `uninstall.ps1`) | **no** | **BLOCKED** |
| Sidecar | 2 | `sha256:03a3c1fa30dd487230fdfa4cb16aa83ef3b154f22edc21e9d9e917325ba57916` | yes | **no** | yes | **BLOCKED** |

Foreign imports, exactly as resolved from the AST (relative imports resolved,
not grepped):

* **Hub → Node (1):** `fleet_mcp.node.connector` — from
  `fleet_mcp/owner_setup.py`.
* **Node → Hub (10):** `fleet_mcp`, `fleet_mcp.config`, `fleet_mcp.errors`,
  `fleet_mcp.fleet`, `fleet_mcp.identity`, `fleet_mcp.local_roots`,
  `fleet_mcp.node_protocol`, `fleet_mcp.owner_access`,
  `fleet_mcp.providers.cloudcli`, `fleet_mcp.runtime_identity`.
* **Sidecar → anything first-party:** none (stdlib only).

File lists are reproducible via the command above (`file_list` per component);
they are 231 entries total at this ref and are not inlined here because they
change with every merge — a copied list would be the thing this very check
exists to prevent.

### What that means for a real install

* Hub and Node must be released **together** today. Any plan that ships one
  without the other is a plan with an unresolvable import.
* The Sidecar's staged-only posture is right (no node created), but revoking it
  is `rm -rf` by hand. Until a revoke entrypoint exists in the tree, "we can
  undo the sidecar" is a sentence about a runbook, not about an artifact.

## 8. Source / CI / device — three separate readings

### Source — **VERIFIED (this PR)**

```
$ python -m pytest tests/test_fleet_next_wave16_release_preflight.py -q
60 passed in 3.44s

$ python -m unittest discover -s tests -p 'test_fleet_next_wave16_release_preflight.py'
Ran 60 tests in 2.610s
OK

$ python -m py_compile integrations/fleet-next-wave16-release-preflight/wave16_preflight.py
compile OK
```

Mutation probes, to prove the gate can flip in both directions (baseline run
first: `testsRun=60 flipped=0`):

| rule disabled | tests flipped |
|---|---:|
| containment always `False` | 6 |
| audience normalisation collapsed | 18 |
| timer arming ignored | 4 |
| cross-component imports hidden | 2 |
| shadow writes ignored | 4 |

### Pre-existing failures — **not introduced here**

`tests/test_fleet_next_dual_stack_preflight.py` + `tests/test_fleet_next_install.py`
report `48 failed, 95 passed` in this worktree. The same two files at the same
commit in a clean worktree (`D:/scratch-fleet-d/wt-base-v13`, also
`e69fc033`) report **the identical `48 failed, 95 passed`**, and the
bidirectional difference of failure IDs is empty (33 distinct IDs each side
after stripping `FAILED`/`ERROR`, `comm -23` and `comm -13` both empty).
This PR adds two files and edits none, which `git status --porcelain` confirms:

```
?? integrations/fleet-next-wave16-release-preflight/
?? tests/test_fleet_next_wave16_release_preflight.py
```

### CI — **NOT_RUN at authoring; read the PR run**

* `tests.yml` has no path filter, so it will produce a run on this PR.
* `fleet-next-isolated-contracts.yml` **will not run**: its `paths:` list does
  not include this directory or this test file. That workflow being absent is
  `NOT_RUN`, **not** green. If the reviewer wants the isolated-contract job to
  cover this file, the workflow's path list has to be extended — which is
  outside this PR's exclusive scope and is left as a handoff item.

### Device — **NOT_RUN**

No real install, no service manager, no port, no socket, no OAuth round trip,
no Tailscale, no Feishu. Explicitly out of scope for this round and forbidden
by the task.

## 9. What a real install still needs from the owner

`owner_inputs()` names these; none of them can be measured offline and none may
be invented:

1. `next.plugin_id` — the registered ChatGPT plugin id, observed.
2. `next.public_mcp_url` — the dedicated HTTPS `/mcp` origin for Next.
3. `oauth audience` — the protected resource Next will mint and verify.
4. `pat_anchor` — the owner's PAT anchor **path** (mode 0600). This tool never
   reads its contents, and cannot tell a copied V1 token from a fresh one.
5. `device_ref` — the reviewed identity of the host both tracks run on.
6. `attestations` — owner readback: V1 identity observed, binding reviewed,
   rollback ready.
7. `rollback digest` — the artifact a Next rollback must land on.
8. **co-install consent** — separate user permission, not this gate.

## 10. Handoff items (not done here, deliberately)

| item | owner | why not here |
|---|---|---|
| Extend `fleet-next-isolated-contracts.yml` `paths:` to include this test file | workflow owner | outside this PR's exclusive file scope |
| Break Hub ↔ Node import cycle, or declare them one release unit | Hub/Node owner | source change to files this QA does not own |
| Add a reviewed Sidecar revoke entrypoint | Sidecar owner | new tooling; needs its own review |
| Real co-install on a device | Owner, with separate permission | requires live cgroup, PSI, OAuth and routing changes |
