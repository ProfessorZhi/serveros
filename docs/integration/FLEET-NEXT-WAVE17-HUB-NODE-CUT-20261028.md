# Wave 17 · Hub / Node independent release cut (offline)

Date: 2026-10-28
Scope: `ProfessorZhi/serveros`, source and offline tests only.
Role: independent release-cut lane. This document is **not** an RC
integration, **not** a Legacy production operation, and **not** a claim that
Next can ship.
Base: #431 head `517ef915074e3b14537b746a07b4dcee63eccfb6`, whose merge-base
with `main` is `e69fc0337ae5d33000478e3d5162077f525bc8cc`.

## 0. Read this first

Everything below was produced against AST reads, fakes and temporary copies.
No install was run, no service manager was addressed, no port was bound, no
socket was opened, no credential was read, no Feishu message was sent, no
device was contacted, and no routing was changed. **A green result here is not
a real-device pass.** Real co-install still requires separate permission and is
`NOT_RUN`.

Only these paths were written:

```
integrations/fleet-next-wave17-hub-node-release-cut/**
tests/test_fleet_next_wave17_hub_node_release_cut.py
docs/integration/FLEET-NEXT-WAVE17-HUB-NODE-CUT-20261028.md
```

`fleet_mcp/**`, `runtime/**`, `scripts/release.py`, #431's source, and every
Legacy configuration stayed read-only. No main merge, no real install, no
gateway/plugin/Creator update, no service or credential change.

## 1. Verdict

| # | Question | Verdict |
|---|---|---|
| 1 | Does #502 reproduce at `main@e69fc03`? | **VERIFIED**: Hub 49 / Node 180 / Sidecar 2 files; Hub→Node 1; Node→Hub 11 (see §4) |
| 2 | Can a strictly two-artifact Hub or Node boot alone today? | **NO**: both fail with `ModuleNotFoundError` |
| 3 | Does the owner handoff make the Hub direction zero? | **VERIFIED in sandbox**: 0 Hub→Node imports |
| 4 | Does it make the Node direction zero? | **VERIFIED in sandbox** after one declared shared Base: 10 → all Base-owned, 0 foreign |
| 5 | Do the four units boot with no checkout and no neighbour? | **VERIFIED in sandbox**: Hub (Base + Authority), Node (Base) |
| 6 | Are the SHA manifests reproducible? | **VERIFIED**: two compositions are byte-identical |
| 7 | Does reverse-delete / Hub rollback move the Legacy fake? | **NO**: answers and deliveries unchanged |
| 8 | Is there a second `DeviceRef` / duplicated identity? | **NO**: one authority, pinned by SHA and ABI |
| 9 | Can this lane release? | **BLOCKED_OWNER**: cuts belong in `fleet_mcp/**` |
| 10 | Real device co-install | **NOT_RUN** |

The one finding that changes a release decision: **the Hub can be made fully
independent of the Node; the Node cannot be made independent of a declared
shared Base without moving or duplicating source.** A three-unit topology is
the minimal honest cut. A two-unit topology is `BLOCKED_OWNER`.

## 2. Read-only reconnaissance (fresh snapshot, not reused)

Taken before writing, on 2026-10-28:

* **Worktrees with uncommitted changes**: only
  `SRV-0011-fleet-model-evidence-20261028` (`integrations/chatgpt-fleet-next/batch_contract.py`
  and its test). No path overlap with this lane.
* **Existing Wave 17 path**: none under any worktree or branch.
* **Other lanes present**: #500 RC (`feat/fleet-next-rc-wave16-20261028`),
  #501 QA (`qa/fleet-next-wave16-crosspr-qa-20261028`), #431
  (`feat/fleet-v1-l2-release-separation-20261028`), #461/#467/#473
  (V2-rooted stack), #474. None was merged, reset, rebased or edited.
* **File owners respected**: `fleet_mcp/**` (owner, incl. #431/#461/#467/#473),
  `runtime/**`, `scripts/release.py`, Legacy live configuration — read only.

## 3. Ancestry, checked rather than assumed

| ref | commit | ancestor of it | usable here |
|---|---|---|---|
| `main` | `e69fc03` | — | baseline |
| #431 | `517ef91` | `main` yes | **base of this worktree**; builder reused read-only |
| #502 | `8e6be6b` | `main` yes | measurement reference |
| #461 | `b042f51` | base `codex/fleet-v1-delivery-20261027` | **V2-rooted; not merged**; patch re-based for proof only |
| #467 | `ccd0452` | base #461 | not merged |
| #473 | `a097b27` | base #467 | not merged |
| #466 | `2634882` | base V2 delivery | not merged |

`git merge-base --is-ancestor origin/main <ref>` is false for #461/#467/#473.
The #407 85-file V2 Draft was **not** merged into Wave16 or Legacy.

## 4. RED · #502 reproduced, and two artifacts that cannot boot

Measured on the live worktree (`517ef91`, whose `fleet_mcp/**` is
`main@e69fc03`), using #502's ownership map and #431's closure:

```text
Hub   49 files   Hub→Node 1 : fleet_mcp.node.connector <- fleet_mcp/owner_setup.py
Node 180 files   Node→Hub 11: fleet_mcp, cloudcli_native, config, errors,
                             fleet, identity, local_roots, node_protocol,
                             owner_access, providers.cloudcli,
                             runtime_identity
Sidecar 2 files
```

The module-target count is 11, not #502's 10: #502's resolver did not expand
`from .. import cloudcli_native`, while #431's AST (reused here) does. The
finding is unchanged; the extra target is real and is reported rather than
rounded down to the earlier number.

Closure of the declared entrypoints (`fleet_mcp.server` +
`fleet_mcp.owner_setup`; `fleet_mcp.node.connector`):

```text
hub_closure 33   node_closure 30   shared 30   hub_own 3   node_own 0
base_exact  FALSE
inadmissible: audit, core_lite_readback, core_lite_source, dispatch, fleet,
              node, node.connector, privacy, providers.fleet_node, registry,
              session_detail
```

`node_own = 0` is the sharpest statement of the defect: **the Node has no
module the Hub does not also require** in the two-artifact model. A strictly
two-artifact build cannot boot either half:

```json
{"hub":  {"imported": false, "error": "ModuleNotFoundError", "files": 31},
 "node": {"imported": false, "error": "ModuleNotFoundError", "files": 2}}
```

(`hub` is given the identity-authority sidecar in this probe, so the failure is
the missing **Node**, not the missing checkout.)

## 5. GREEN · the owner handoff, applied to a copy only

`handoff/cut_a_main_rebase.patch` is #461's CUT-A mechanically re-based onto
`main@e69fc03`. `handoff/cut_b_hub_node_seam.patch` is this lane's request to
the `fleet_mcp/**` owner. `apply_handoff.guard()` refuses a checkout and any
path containing `.git`; the tests assert the live tree is untouched.

After both patches, on a `git archive` copy:

```text
Hub   50 files (providers/build.py added)   Hub→Node 0
Node 180 files                              Node→Hub 10 raw, all Base-admissible
                                            effective foreign 0
hub_closure 32   node_closure 21   shared 19   hub_own 13   node_own 2
base_exact TRUE
```

The 19 shared modules are a **pin**, checked in both directions:

```text
fleet_mcp, cloudcli_native, config, disclosure, discovery, errors,
feishu_sso, identity, local_roots, mcp_http, node_protocol, owner_access,
providers, providers.base, providers.cloudcli, runtime_create_policy,
runtime_identity, scopes, version
```

A shared module outside the list fails the build; a list entry the closures no
longer share also fails it. The Base cannot silently grow into "everything both
halves happen to import".

### 5.1 Four units

| unit | files | tree SHA256 | depends on |
|---|---:|---|---|
| `fleet-runtime-base` | 19 | `sha256:bc8d32d0d32fd9e5609cb661e65f487d4450a5d7e7a80e74c4d0b44c6efd06f1` | — |
| `fleet-authority` | 3 | `sha256:c1fa433fecbaaa47fe0f9e146952e57653d5db53211619a6843911d3c4943625` | — |
| `fleet-hub` | 13 | `sha256:b9fc5a6f768e56175eea6498de261627230c6a94f7ff1bf92efeb6915874bde6` | Base, Authority |
| `fleet-node` | 2 | `sha256:0871f4da77aef4303b1dfc35896c0ecd0fbc47cf481b58a10500360f2325a768` | Base |

The authority is exactly `fleet/transport.py` + `fleet/transport-contract.yaml`
+ its manifest (`serveros/fleet-authority.v1`, ABI
`serveros.fleet.transport/v1`, `single_authority: true`). `fleet/registry.yaml`
is authored data and is **not** bundled. No unit defines a second `DeviceRef`,
`Device`, `DeviceRegistry` or `canonical_level`.

The Node unit is exactly:

```text
fleet_mcp/node/__init__.py
fleet_mcp/node/connector.py
```

It carries no `server.py`, `tools.py`, `fleet.py`, `registry.py`,
`owner_setup.py`, `providers/fleet_node.py`, deploy script or unit file.

### 5.2 Isolation, measured in a fresh interpreter

Each prefix is a complete install of one half: Base plus its own unit. The Hub
also installs the Authority; the Node does not. Neither prefix receives the
other half's file, and the probe asserts **file absence**, not import order.

```json
{"hub":  {"booted": true, "imported": "fleet_mcp.server", "tools": 44,
          "dispatch_refused": true, "authority_symbols": true,
          "node_loader": "NODE_CONFIG_LOADER_UNAVAILABLE"},
 "node": {"booted": true, "imported": "fleet_mcp.node.connector",
          "device_ref": "device:wave17-hermetic"}}
```

The Hub's owner-setup CLI imports without the Node and refuses `--node-config`
by name. The Node loads a hermetic binding; no socket is bound.

### 5.3 Reproducibility

Two compositions from the same tree produce identical `member_sha256`,
identical `tree_sha256` and byte-identical `ARTIFACT.json` /
`wave17-manifest.json`. The sandbox commit id is fixed by
`GIT_AUTHOR_DATE`/`GIT_COMMITTER_DATE`, so the measured tree is named by a
stable SHA on every run.

### 5.4 Reverse delete and rollback

On a fake install prefix and a fake Legacy Fleet 0.1.0 controller:

1. delete every `fleet-node` file from the combined prefix; the Hub still
   boots (`reverse-delete-node: OK`);
2. roll the Hub unit back to `0.9.0+wave17prev`; the Hub still boots
   (`rollback-hub: OK`);
3. Legacy `get_session` / `get_run` / `send` answers and the delivery list are
   unchanged (`legacy_unchanged: true`).

The two Hub versions differ only in their manifest in this lane; the pointer
swap is exercised, and the report says so rather than implying a code delta.

## 6. BLOCKED_OWNER · what must land, where, and in what order

Independent binary release **requires edits to `fleet_mcp/**`**, which this
lane may not write. The precise request is in
`integrations/fleet-next-wave17-hub-node-release-cut/handoff/HANDOFF.md`.

### Phase 1 — the `fleet_mcp/**` owner

| file | interface | change |
|---|---|---|
| `fleet_mcp/owner_setup.py` | `load_node_config(path) -> NodeConfig` | remove the top-level Node import; inject an optional loader; refuse `NODE_CONFIG_LOADER_UNAVAILABLE` when the Node unit is absent |
| `fleet_mcp/providers/__init__.py` | `CloudCliProvider`, `FleetNodeProvider`, `build_provider`, `build_providers` | stop importing adapters eagerly; lazy `__getattr__`; keep the public names |
| `fleet_mcp/providers/build.py` | new | own `build_provider` / `build_providers` |
| `fleet_mcp/fleet.py` | — | import `build_providers` from `.providers.build`, base names from `.providers.base` |
| `fleet_mcp/fleet.py`, `owner_access.py`, `node/connector.py` | — | land #461's CUT-A (three helpers move to `owner_access`) |

Acceptance: Hub→Node 0; Node→Hub 10, all in the pinned Base; existing
`tests/test_fleet_l2_release_separation.py` stays green; no second identity
implementation.

### Phase 2 — this lane, after Phase 1

1. Build the four units with the pinned #431 builder
   (`sha256:7f87a596…`) and the 19-module Base pin.
2. Install Base + Authority once; install Hub and Node independently.
3. Re-run isolation, reverse-delete and rollback with the shipped tests.
4. Real device co-install is a separate, explicitly authorised operation.

The owner must also approve the **Base re-declaration** (19 modules move from
"Hub-owned by directory" to "shared release unit"). That is a release-topology
decision, not a code-ownership change: the `fleet_mcp/**` owner still owns
those files.

## 7. One SHA · what actually ran

Test environment: this worktree, Python 3.12, no network, no services. Commands
and results are recorded with the commit in `evidence/wave17-evidence.json`.

```text
python -m pytest tests/test_fleet_next_wave17_hub_node_release_cut.py -q
13 passed

python -m pytest tests/test_fleet_l2_release_separation.py -q
77 passed, 1 skipped

python integrations/fleet-next-wave17-hub-node-release-cut/wave17_cut.py \
    --root . --sandbox --isolation --json
technical_state GREEN_SANDBOX · decision BLOCKED_OWNER · device NOT_RUN
```

Hosted CI is the PR's own run at the same head; this document does not claim a
hosted result before it exists.

### 7.1 Commits and change surface

| commit | what |
|---|---|
| `8770161` | adapter, tests, handoff patches, this report |
| `f3aa45a` | `evidence/wave17-evidence.json`, produced at `8770161` |

`git diff --stat 517ef91..8770161` — 8 files, +2365 lines, no deletion outside
the new paths. Changed-tree listing digest (SHA256 of `git ls-tree -r` over the
three owned paths at `8770161`):
`sha256:a504be7cb19f6f1aff9c5dce0f4c65666a951c7c0be958cc8e6e9d9f6132e44d`.

## 8. Non-goals honoured

* No `main` merge, no real install, no Legacy gateway/plugin/Creator update.
* No service port, credential, OAuth or Feishu change; no shell touching
  production.
* No second `DeviceRef`; no copied source used to manufacture independence.
* Real device co-install: **NOT_RUN**.
