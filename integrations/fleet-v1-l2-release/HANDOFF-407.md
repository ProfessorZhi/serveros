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
`fleet_mcp/{server,tools}.py`. **17 modules are in both**, and the connector's
closure leaves the `fleet_mcp` package entirely and needs the repository-root
`fleet/` namespace package. So the node artifact cannot be installed without a
checkout of this repository — i.e. it is not yet a device release.

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

| scenario | files in `l2-node` | needs repo-root `fleet/` | shared modules |
|---|---|---|---|
| as-is | 30 | **yes** | 17 |
| cut A | 25 | no | 12 |
| cut A + B | 21 | no | 8 |
| cut A + B + C | **17** | no | **4** |

After A+B+C the node artifact is 17 files, `standalone: true`, and the four
remaining shared modules are ones it genuinely uses: `cloudcli_native`
(`connector.py:975`, `1059`, and `providers/cloudcli.py`), `owner_access`
(`connector.py:331`, `710`, `1266`, `1316`), `mcp_http` and `disclosure` (via
`providers/cloudcli.py`).

A alone is worth doing first: it is one file, it removes the repository-root
dependency, and it makes "the node artifact installs on a device" a true sentence
instead of a conditional one.

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
`--allow-external-in-repo fleet`, which writes `"standalone": false` into the
manifest where a reviewer will see it.
