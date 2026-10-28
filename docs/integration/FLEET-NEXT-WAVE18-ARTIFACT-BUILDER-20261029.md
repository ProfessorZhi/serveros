# Wave 18 · Next release artifact builder (offline)

Date: 2026-10-29
Scope: `ProfessorZhi/serveros`, source and offline tests only.
Role: owner of #505, implementing the release-cut lane as buildable artifacts.
Base: #505 head `3512c551574a94ad360f8bde6435bdf07c729167`, stacked on
#431 `517ef915074e3b14537b746a07b4dcee63eccfb6` (`main@e69fc03`).

## 0. Read this first

Everything below is produced from `git archive`, reviewed patches, temporary
roots and a fake controller. No install was run, no service manager was
addressed, no port or socket was bound, no credential was read, no OAuth or
Tailscale was touched, no Feishu message was sent, and no Legacy configuration
was changed. **A green artifact is not a real-device pass.** `REAL_DEVICE
NOT_RUN`; a production install remains `BLOCKED_OWNER` because the source cuts
have not been landed.

Only these paths were written:

```
integrations/fleet-next-wave18-artifact-builder/**
tests/test_fleet_next_wave18_artifact_builder.py
docs/integration/FLEET-NEXT-WAVE18-ARTIFACT-BUILDER-20261029.md
```

`fleet_mcp/**`, `runtime/**`, `scripts/release.py`, `fleet/**`, the Legacy
live configuration and the V2-rooted #461/#467/#473 files stayed read-only. No
main merge, no real install, no service/OAuth/Tailscale/Feishu change.

## 1. Verdict

| # | Question | Verdict |
|---|---|---|
| 1 | Does the unpatched tree form two independent artifacts? | **NO**: Hub carries 2 Node files, Node carries 9 Hub files; both own-files-only payloads fail to boot |
| 2 | Do four units build from a commit? | **YES**: Base 19, Authority 3, Hub 13, Node 2, Composition 0 |
| 3 | Are two builds at the same commit byte-identical? | **YES**, all five tarballs |
| 4 | Do Hub and Node boot with no checkout and no neighbour? | **YES**, in fresh `-I -B` interpreters |
| 5 | Does removing a shared primitive fail? | **YES**: `errors.py`, `config.py`, `fleet/transport.py` |
| 6 | Does independent rollback move the Legacy fake? | **NO**: `get_session` / `get_run` / `send` and identities unchanged |
| 7 | Are install root/port/lock/backend id/audience published and non-colliding? | **YES**, #483/#480 vocabulary |
| 8 | Is there a second `DeviceRef` or duplicated identity? | **NO**: one authority, `single_authority: true`, `signed: false` |
| 9 | Can this lane release? | **NO**: `BLOCKED_OWNER`; the cuts are not landed |
| 10 | Real device co-install | **NOT_RUN** |

## 2. What the D7 contract actually says

Read from `integrations/fleet-v1-l2-release/l2_release.py` and its README at
#431 `517ef91`:

* two artifact kinds, **two versions**, two manifests, two verifiers;
* membership is the import closure of one entrypoint, computed by AST with
  relative imports resolved;
* archives are deterministic: USTAR, gzip `mtime=0`, empty filename, uid/gid 0,
  sorted members, mode 0644; two builds of the same tree are byte-identical;
* `pair()` reads two revisions of one kind and reports what a rollback would
  restore, verifying both before naming either;
* "signed artifacts: **NOT DONE** — digests are recorded, nothing is signed."

Wave 18 therefore does **not** claim a cryptographic signature. The authority
sidecar carries the canonical `fleet/transport.py`, its contract, and a manifest
with ABI `serveros.fleet.transport/v1`, per-file SHA256 and
`single_authority: true`; `signed` is explicitly `false`. No verifier is
bypassed, and no second `DeviceRef` is minted.

D7 does **not** require Hub and Node to share a version. Wave 18 keeps
independent versions (`same_version_required: false`) and publishes an optional
composition that pins the four units with a compensating rollback order. It
does not call that composition atomic.

## 3. Architecture: four units, and why not two

The measured tree shares 19 modules between the Hub and Node closures. Two
honest options exist:

1. duplicate those 19 modules into both artifacts — independence by copying;
2. declare a shared Base unit, installed once per prefix.

This lane chooses (2), with the Base as a **pin** checked in both directions:

```text
fleet_mcp, cloudcli_native, config, disclosure, discovery, errors,
feishu_sso, identity, local_roots, mcp_http, node_protocol, owner_access,
providers, providers.base, providers.cloudcli, runtime_create_policy,
runtime_identity, scopes, version
```

A shared module outside the list fails; a list entry the closures no longer
share also fails. A Node artifact containing any Hub-only file is refused by
name, and a Hub artifact containing `fleet_mcp/node/**` is refused by name.

Two 180-file packages are never built. The Node artifact is exactly:

```text
fleet_mcp/node/__init__.py
fleet_mcp/node/connector.py
```

## 4. Provenance: every transform is auditable

`evidence/PROVENANCE.json` records:

| field | meaning |
|---|---|
| `source.commit`, `source.tree` | the commit the units were built from |
| `source.archive_sha256` | the `git archive --format=tar` bytes |
| `source.built_from` | always `git archive`, never a worktree |
| `source.workdir_used_as_artifact` | `false` |
| `transform.patches[].sha256` | the reviewed patch file |
| `transform.patches[].owner_commit` | CUT-A `b042f515…` (#461), CUT-B `3512c55…` (#505) |
| `transform.patches[].rebased_to` | CUT-A `main@e69fc03`; CUT-B `null` |
| `targets[].original_blob_sha` / `original_sha256` | the owner's file before the cut |
| `targets[].result_sha256` / `result_blob_sha` | the file after the cut |
| `applied_in` | `isolated git-archive copy` |
| `live_tree_modified` | `false` |

The builder refuses a dirty `fleet_mcp/` or `fleet/`; it never treats the
working directory as a release input. A missing patch is
`BLOCKED_INPUT_ABSENT`, not a reason to re-implement the cut.

## 5. RED · the unpatched tree

`--mode baseline` on `main@e69fc03`-equivalent source:

```json
{
  "state": "RED_BASELINE",
  "decision": "BLOCKED_CROSS_COMPONENT",
  "partition": {"hub_closure": 33, "node_closure": 30, "shared": 30,
                "hub_own": 3, "node_own": 0, "base_exact": false},
  "leaks": {
    "hub": ["fleet_mcp/node/__init__.py", "fleet_mcp/node/connector.py"],
    "node": ["fleet_mcp/audit.py", "fleet_mcp/core_lite_readback.py",
             "fleet_mcp/core_lite_source.py", "fleet_mcp/dispatch.py",
             "fleet_mcp/fleet.py", "fleet_mcp/privacy.py",
             "fleet_mcp/providers/fleet_node.py", "fleet_mcp/registry.py",
             "fleet_mcp/session_detail.py"]
  },
  "two_artifact_boot": {"hub": {"booted": false}, "node": {"booted": false}}
}
```

`node_own = 0` is the defect: the Node has no module the Hub does not also
require. A two-artifact build is refused before a tarball exists.

## 6. GREEN · four units, deterministic and isolated

| unit | files | sha256 (tarball) | tree sha256 |
|---|---:|---|---|
| `fleet-runtime-base` | 19 | see `ARTIFACTS.sha256` | see `PROVENANCE.json` |
| `fleet-authority` | 3 | see `ARTIFACTS.sha256` | see `PROVENANCE.json` |
| `fleet-hub` | 13 | see `ARTIFACTS.sha256` | see `PROVENANCE.json` |
| `fleet-node` | 2 | see `ARTIFACTS.sha256` | see `PROVENANCE.json` |
| `fleet-next-composition` | 0 | see `ARTIFACTS.sha256` | empty-tree digest |

Two builds of the same commit produce byte-identical tarballs and identical
member/tree digests. The `git archive` input is also identical across the three
builds the evidence pack runs.

**Isolation.** Each half is extracted into a fresh root with its declared
dependencies only. Hub gets Base + Authority + Hub; Node gets Base + Node.
Neither root receives the other half's file (asserted by exact path), and the
probe runs in a fresh interpreter with `-I -B`, cwd elsewhere, only the root on
`sys.path`.

```json
{"hub":  {"booted": true, "node_loader": "NODE_CONFIG_LOADER_UNAVAILABLE",
          "authority_symbols": true, "dispatch_refused": true},
 "node": {"booted": true, "device_ref": "device:wave18-hermetic"}}
```

The post-import audit refuses a `fleet_mcp` module that is not declared for
that component, or whose `__file__` is outside the root.

**Negative control.** Removing `fleet_mcp/errors.py`, `fleet_mcp/config.py` or
`fleet/transport.py` makes the corresponding boot fail. The isolation claim is
therefore measuring the shared primitive, not an empty import.

## 7. Recovery: independent and composition rollback

Each prefix has its own `current.json`. On a fake install:

1. **Node-only rollback** — the Node prefix moves from `1.0.1+…` to
   `1.0.0+…`; the Hub prefix's bytes are unchanged; the Node boots.
2. **Hub-only rollback** — the Hub prefix moves; the Node prefix's bytes are
   unchanged; the Hub boots.
3. **Mixed versions** — Hub on the newer version and Node on the older one
   coexist, because D7 gives each kind its own version.
4. **Composition rollback** — reverse order `Node → Hub → Authority → Base`,
   marked `compensating` because the Base is shared.

In every step the Legacy 0.1.0 fake's `get_session`, `get_run`, `send`,
identities and delivery list are compared before/after and are unchanged.

## 8. Release footprints and collision veto

`evidence/RELEASE-PLAN.json` publishes both footprints. The eight resources are
reported together, with `device_ref` inverted (it must be equal):

| resource | Hub | Node |
|---|---|---|
| install_path | `/opt/fleet-next/hub` | `/opt/fleet-next/node` |
| port | 8101 | 3402 |
| socket | `/run/fleet-next/hub.sock` | `/run/fleet-next/node.sock` |
| backend_id | `next-hub` | `next-node` |
| oauth_audience | `https://next.invalid/mcp` | `https://next-node.invalid/mcp` |
| cred_state_root | `/var/lib/fleet-next/hub` | `/var/lib/fleet-next/node` |
| lock | `/run/lock/fleet-next-hub.lock` | `/run/lock/fleet-next-node.lock` |
| unit | `fleet-next-hub.service` | `fleet-next-node.service` |
| device_ref | `device:tokyo-shared-dev-01` | same |

Codes are the #483/#480 spellings: `BLOCKED_RESOURCE_COLLISION`,
`BLOCKED_DEVICE_DISAGREEMENT`, `NEXT_SHADOW_WRITE_FORBIDDEN`,
`NEXT_SHADOW_NOT_GRANTED`, `SHADOW_VERB_UNKNOWN`. A shadow `send` is refused
**even when the verb is granted**; `get_session` is allowed; an unknown verb is
`SHADOW_VERB_UNKNOWN`. `get_session` without a read grant is
`NEXT_SHADOW_NOT_GRANTED`.

## 9. Why this is still `BLOCKED_OWNER`

The CUT-A/CUT-B transforms live in an isolated copy; `fleet_mcp/**` is
unchanged. The exact owner request, files and interfaces are in
`integrations/fleet-next-wave17-hub-node-release-cut/handoff/HANDOFF.md`:

* `fleet_mcp/owner_setup.py` — inject the optional Node loader, refuse
  `NODE_CONFIG_LOADER_UNAVAILABLE`;
* `fleet_mcp/providers/__init__.py` + new `fleet_mcp/providers/build.py` — stop
  importing every adapter eagerly;
* `fleet_mcp/fleet.py` — import construction from `.providers.build`;
* CUT-A — move the three `FleetService` helpers into `owner_access.py`.

Phase 1 is the owner's; Phase 2 is this builder run against the landed tree,
followed by a separately authorised device co-install. Until Phase 1 lands, the
artifacts are evidence, not a release.

## 10. What ran

```text
python -m pytest tests/test_fleet_next_wave18_artifact_builder.py -q
15 passed

python -m pytest tests/test_fleet_next_wave17_hub_node_release_cut.py -q
13 passed

python -m pytest tests/test_fleet_l2_release_separation.py -q
77 passed, 1 skipped
```

Hosted CI is the PR's own run at the same head; this document does not claim a
hosted result before it exists. The evidence pack is reproduced with:

```bash
python3 -I integrations/fleet-next-wave18-artifact-builder/wave18_builder.py \
  --repo . --commit <head> --version 1.0.0+wave18 \
  --out /tmp/w18-evidence --json
```

## 11. Non-goals honoured

* No `main` merge, no real install, no Legacy gateway/plugin/Creator update.
* No service port, credential, OAuth, Tailscale or Feishu change.
* No second `DeviceRef`; no source duplication used to manufacture
  independence; no signed authority claimed or bypassed.
* No V2-rooted #461/#467/#473 merge; CUT-A's provenance names #461 and the
  rebase to `main`.
* Real device co-install: **NOT_RUN**.
