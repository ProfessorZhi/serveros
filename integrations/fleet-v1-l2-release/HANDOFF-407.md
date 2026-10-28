# Handoff to #407 · four edges keep the node release inside the hub's release

Written by the D7 L2 release work (#429). This file only **measures**; the four
edges are in files #407 owns (`fleet_mcp/node/connector.py`,
`fleet_mcp/providers/**`, `fleet_mcp/config.py`, `fleet_mcp/registry.py`) and
none of them was edited here.

Every number below is the output of `l2_release.closure()` on `main` @ `e69fc03`,
printing the file set the node artifact would carry. Reproduce with:

```
python integrations/fleet-v1-l2-release/l2_release.py build \
  --kind l2-node --version 1.4.0+probe --output /tmp/n.tar.gz
```

## The measurement

`l2-node` and `l2-hub` are both 30 files today. Node-only:
`fleet_mcp/node/{__init__,connector}.py`. Hub-only:
`fleet_mcp/{server,tools}.py`. **28 modules are in both** — the package is the
release surface, not a shared corner of it. Of those 28, 11 are declared node
leaves and 17 are the pinned shared surface (`NODE_SHARED_SURFACE`); the pin is
smaller than the intersection because a module the node already declares as its
own needs no pin to be legal.

The connector's closure also leaves the `fleet_mcp` package entirely and needs
the repository-root `fleet/` namespace package. So the node artifact cannot be
installed without a checkout of this repository — i.e. it is not yet a device
release.

## The four edges

**A. `fleet_mcp/node/connector.py` → `fleet_mcp.fleet`** — `1124`, `1317`, `1384`
(`from ..fleet import FleetService`). This is the one that matters most: it drags
in 12 modules *and* `fleet_mcp.registry`, and `fleet_mcp/registry.py:33` is the
only path to the repository root:

```python
    from fleet.transport import (  # type: ignore
```

**B. `fleet_mcp/providers/__init__.py:18` → `.fleet_node`** — a package
`__init__` that imports a sibling provider for its re-export, so every consumer of
`fleet_mcp.providers` gets the hub-facing provider whether or not it asked.

**C. `fleet_mcp/config.py`** — `31` `feishu_sso`, `36` `runtime_create_policy`,
`38` `scopes`, `39` `owner_access`, and `431` / `1275` `discovery` (function-level).
`fleet_mcp/providers/__init__.py:4` and `connector.py:106` both import `config`,
so the SSO/whitelist/discovery surface arrives with the config dataclass.

**D. `fleet_mcp/registry.py:33` → `fleet.transport`** (repo root) — not an edge you
cut so much as the one that disappears when A is cut, because nothing else in the
node closure reaches `registry`.

## What each cut buys, measured

Each row was measured by copying `fleet_mcp/` and `fleet/` to a temporary
directory, deleting the `ImportFrom` statements the edge consists of (whole
statement, via `ast` `lineno`/`end_lineno`, so the file still parses), and
re-running `closure()` on the copy. Nothing in this working tree was modified:

| scenario | files in `l2-node` | needs repo-root `fleet/` | modules in both | pinned modules left |
|---|---|---|---|---|
| as-is | 30 | **yes** | 28 | 17 |
| cut A | 25 | no | 23 | 12 |
| cut A + B | 21 | no | 18 | 8 |
| cut A + B + C | **17** | no | **14** | **4** |

The last column is the pin; the one before it is the intersection with the hub's
payload. They differ because 13 of the node's modules are declared node leaves
and never needed a pin.

After A+B+C the node artifact is 17 files, `standalone: true`, and the four
remaining **pinned** modules are ones it genuinely uses: `cloudcli_native`
(`connector.py:975`, `1059`, and `providers/cloudcli.py`), `owner_access`
(`connector.py:331`, `710`, `1266`, `1316`), `mcp_http` and `disclosure` (via
`providers/cloudcli.py`).

One side effect worth knowing before choosing an order: B and C shrink the **hub**
as well (30 → 28 → 27 files), because `fleet_mcp/providers/__init__.py` and
`fleet_mcp/config.py` are hub modules too. A changes only the node's payload.

A alone is worth doing first: it is one file, it removes the repository-root
dependency, and it makes "the node artifact installs on a device" a true sentence
instead of a conditional one.

## A, measured end to end — and it is enough on its own

The row above says cut A leaves the artifact not needing `fleet/`. What that buys
is now stated in the two fields the manifest carries, and the measurement was run
for real: copy `fleet_mcp/` and `fleet/` to a temporary tree, delete the three
`ImportFrom`s whose module is `fleet` from `connector.py`, trim the pin by exactly
the modules the closure lost, build, verify. Nothing in this working tree was
modified.

| | today | after A |
|---|---|---|
| files in `l2-node` | 30 | **25** |
| `external_in_repo` | `["fleet"]` | `[]` |
| `import_standalone` | true | true |
| `runtime_standalone` | **false** | **true** |
| `standalone` (legacy alias) | false | true |
| `install.independently_installable` | **false** | **true** |
| `install.blocked_by` | `["fleet_mcp.registry -> fleet.transport (7 symbols)"]` | `[]` |
| pinned modules left | 17 | **12** |

The pin trim is not optional and is not a new rule: the build refuses a pin entry
the payload no longer reaches, and it names the five that leave —
`audit`, `dispatch`, `fleet`, `privacy`, `registry`. That refusal is the ratchet
working; trim the tuple in the same PR as the cut.

Important: A does **not** change `import_standalone`, which is already true. The
node imports without a checkout today because the connector defers
`from ..fleet import FleetService` into function bodies. What A changes is that it
**runs** without one — `fleet_mcp/registry.py:33` is the only path to the
repository root, and nothing else in the node closure reaches `registry`.

This whole paragraph is executable:
`tests/test_fleet_l2_release_separation.py::test_deleting_the_lazy_imports_on_a_copy_flips_the_packaging_flags`
runs it and asserts the numbers above. If #407 changes A's shape, that test fails
with "edge A has moved; re-measure HANDOFF-407 before trusting its table" rather
than letting this document quietly become wrong.

## Retraction, and the real patch — measured on your head `9538238d`

**The table above measures packaging, and this document used to imply it
measured the cut.** It does not. Deleting the three
`from ..fleet import FleetService` statements leaves three call sites that name
`FleetService` with nothing to resolve them, and I measured what that does:

```
$ git -C <copy at 9538238d> apply   # three statements deleted, nothing else
$ python -m pytest -q tests/test_fleet_mcp_node.py::DeleteProjectHere
8 failed  — NameError: name 'FleetService' is not defined  (connector.py:1372)
```

The artifact still builds, still verifies, `runtime_standalone` still flips to
true — and the product raises on the first project delete. **A cut that only
deletes imports is a packaging change that breaks the connector.** The rest of
this section is the patch that is behaviour-preserving, measured the same way.

### What the three call sites actually need

| line at `9538238d` | call | what it needs |
|---|---|---|
| `1163-1164` | `FleetService._verify_owner_project(self.provider, project)` | one static helper |
| `1374` | `FleetService._project_catalogue_row(self.provider, project_id, include_archived=True)` | one static helper |
| `1423-1424` | `FleetService._verify_owner_project(self.provider, project)` | one static helper |

Those three helpers are `FleetService._project_row`, `_project_catalogue_row` and
`_verify_owner_project` (`fleet.py:358-408`). They take a provider, call
`native_call("projects_list", …)`, and raise `refuse(WRITE_NOT_GRANTED, …)` —
they touch no `FleetService` state and import nothing outside `.errors`, which
both closures already carry.

### The patch — three files, +70/−52, no new module, no new pin entry

```
integrations/fleet-v1-l2-release/L2-CUT-A.patch     (193 lines, `git apply --check` clean on 9538238d)
```

1. **`fleet_mcp/owner_access.py`** — the three helpers land here as module-level
   functions (`project_row`, `project_catalogue_row`, `verify_owner_project`),
   docstrings verbatim, plus `from .errors import WRITE_NOT_GRANTED, refuse`.
   `owner_access` is already in the node's closure *and* already pinned, so this
   adds **no module and no pin entry**.
2. **`fleet_mcp/fleet.py`** — the three statics become one-line delegations, so
   the four internal call sites (`1875`, `1923`, `2173`, `2505`) do not move.
3. **`fleet_mcp/node/connector.py`** — the three lazy imports point at
   `..owner_access`, and the three uses call the functions directly.

### Measured on a copy at your head, with the patch applied

| | `9538238d` as-is | + this patch |
|---|---|---|
| `l2-node` closure | 31 files | **26 files** |
| `l2-hub` closure | 31 files | 31 files (unchanged — the hub keeps the control plane) |
| modules in both | 29 | **24** |
| node `external_in_repo` | `["fleet"]` | **`[]`** |
| node external edges | `yaml`, `fleet.transport` (7 names) | **`yaml` only** |
| `import_standalone` / `runtime_standalone` | true / **false** | true / **true** |
| `install.independently_installable` | false | **true** |
| pinned modules | 18 (17 + `principals`) | **13** |

The pin movement is two separate things and both are mechanical: `+1`
`fleet_mcp.principals` (new on your head, reached through `config.py:40` — the
ratchet refuses to build until it is named), and `−5` `audit`, `dispatch`,
`fleet`, `privacy`, `registry`, which the cut closure no longer reaches. The
build names the five that leave; trim them in the same change.

### Behaviour, measured

| test file | as-is at `9538238d` | + patch |
|---|---|---|
| `test_fleet_mcp_node.py`, `test_fleet_node.py`, `test_fleet_node_owner_floor.py`, `test_fleet_refusal_audit.py`, `test_fleet_principal_context.py`, `test_fleet_v1_t10_legacy_compatibility.py` | 226 passed, 24 skipped | **226 passed, 24 skipped** |
| 121 `test_fleet_*` / `test_node_*` / `test_v1_*` files (335 pre-existing platform failures on this Windows box) | 335 failed | **the same 335, byte-identical set** |
| `DeleteProjectHere` (the falsifier) | 8 pass | **8 pass** |

The 335 are the same failures with and without the patch — compared as sorted
node-id lists, not as counts — so the patch changes no test outcome on the
observable surface. `tests/test_l2_source_standalone_contract.py` turns those
three properties into a RED → GREEN ratchet: three `xfail(strict=True)` tests
that fail today, XPASS the moment the cut lands, and thereby force the marker
removal in the same change.

### What I did **not** do

I did not apply any of this to `fleet_mcp/**`. Your comment on this handoff asked
for an explicit per-file scope before that happens and it has not been given, so
the state is **BLOCKED_KEEP_AND_GUARD**: the patch is published, checkable, and
unapplied. If you want it, the smallest thing that works is **two commits**:

1. `fleet_mcp/owner_access.py` + `fleet_mcp/fleet.py` — lift the three helpers,
   leave the delegations. Behaviour-neutral on its own; the 226-test battery is
   the acceptance and it is green before and after.
2. `fleet_mcp/node/connector.py` — repoint the three call sites, plus the pin
   trim in this directory (`NODE_SHARED_SURFACE`: `+fleet_mcp.principals`,
   `−audit, −dispatch, −fleet, −privacy, −registry`) and deleting the three
   `xfail` markers in `tests/test_l2_source_standalone_contract.py`.

Commit 1 alone cannot break the node; commit 2 is the one whose blast radius is
the node closure, and the contract tests name it precisely.

## What D7 asks of the change, in this directory's terms

- `NODE_SHARED_SURFACE` in `l2_release.py` shrinks by exactly the modules that
  left. `tests/test_fleet_l2_release_separation.py::test_the_shared_surface_is_pinned_and_exact`
  will fail until the tuple is trimmed — that failure is the ratchet working, not
  a regression. Trim it in the same PR as the cut.
- If a cut deletes the last reference to a module, delete the module. The
  measured `edges` map (`Closure.edges`) says which package pulled which.
- `fleet_mcp/providers/fleet_node.py` is hub-facing and is *not* hidden behind a
  lazy import; if a device provider needs a shared piece of it, move the piece,
  not the import.
- The hub's own closure does not carry `fleet_mcp/node/**` — that half of the
  separation already holds and must keep holding
  (`test_the_hub_artifact_carries_no_node`).

## Do not

Do not make `l2_release.py` tolerate these edges. It refuses them by default on
purpose: the only way to build a non-standalone artifact is to pass
`--allow-external-in-repo fleet`, which writes `"standalone": false`,
`"runtime_standalone": false`, `"install": {"independently_installable": false}`
and the blocked-by list into the manifest where a reviewer will see them. The
allowance buys a build and nothing else: no field a build can write makes a
payload that needs a checkout installable on its own, and the verifier re-measures
the claim instead of reading it.
