# Handoff to the `fleet_mcp/**` owner · Wave 17 Hub/Node release cut

This is a **request**, published unapplied. Nothing in this directory has been
written into `fleet_mcp/**`; the patches are applied only to a `git archive`
copy, and `apply_handoff.guard()` refuses a checkout.

## Why the owner is needed

Independent binary release is not a packaging problem on this tree. The
ownership map (#502) and the release map (#431's import closure) disagree in
exactly two places:

| direction | edge | holder | what it costs |
|---|---|---|---|
| Hub -> Node | `fleet_mcp.node.connector` | `fleet_mcp/owner_setup.py:17` | a Hub-only payload cannot import its own owner-setup CLI |
| Node -> Hub | 10 modules | `fleet_mcp/node/connector.py:89-106` + deferred sites | a Node-only payload cannot import at all |

After CUT-A and CUT-B the Hub direction is **zero** and the Node direction is
**nine modules, all of which are shared runtime primitives** that a third
`fleet-runtime-base` unit can own. That is the difference between "the Node is
the repository" and "the Node needs a declared base, not the Hub".

## CUT-A · provenance and rebase

CUT-A is **#461's** authored change (`b042f515`, base
`codex/fleet-v1-delivery-20261027`). That branch is V2-rooted and is not an
ancestor of `main`; it must not be merged into Legacy/Wave16. #431 also
publishes `L2-CUT-A.patch` unapplied, but it was cut against #407's head and
**does not apply to `main@e69fc03`**:

```
$ git apply --check integrations/fleet-v1-l2-release/L2-CUT-A.patch
error: patch failed: fleet_mcp/fleet.py:60
error: fleet_mcp/fleet.py: patch does not apply
```

`cut_a_main_rebase.patch` in this directory is the same change (move
`_project_row`, `_project_catalogue_row`, `_verify_owner_project` into
`fleet_mcp/owner_access.py`; leave one-line delegations in `fleet.py`; call the
`owner_access` functions from `connector.py`) **mechanically rebased onto
`main@e69fc03` for the sandbox proof only**. It is not proposed as a
replacement for #461: the owner should land #461's own rebased commit.

Files: `fleet_mcp/fleet.py`, `fleet_mcp/owner_access.py`,
`fleet_mcp/node/connector.py`.

Effect measured: Node -> Hub imports drop from 10 to 9 (`fleet_mcp.fleet`
leaves the closure).

## CUT-B · this lane's request

### 1. `fleet_mcp/owner_setup.py` — the Hub's optional Node seam

Remove the module-level `from .node.connector import load_node_config`. Replace
it with an injected loader whose default is resolved dynamically and refuses by
name when the Node unit is not installed:

```python
def _node_config_loader():
    try:
        module = importlib.import_module(".node.connector", __package__)
    except ImportError as exc:
        raise ValueError(
            "NODE_CONFIG_LOADER_UNAVAILABLE: this Hub was installed without the "
            "Node artifact, so `--node-config` cannot be prepared here; install "
            "fleet-node or omit the flag") from exc
    return module.load_node_config


def prepare(fleet_source, request, output, *, node_sources=None,
            node_config_loader=None):
    ...
    loader = node_config_loader or _node_config_loader()
    node = loader(source)
```

Interface the Hub needs from the Node: **`load_node_config(path) -> NodeConfig`**,
where `NodeConfig.device_ref: str` and `NodeConfig.runtime: str` are the fields
`prepare()` compares against the selected binding. Nothing else crosses.

Why dynamic and not a top-level import: owner setup's Node-config preparation
is an **optional feature**. The Hub's ingress, tools and OAuth never call it. A
declared optional feature must not be a static edge that makes the Hub artifact
unimportable without its neighbour. The refusal is by name, not an ImportError
five frames down.

### 2. `fleet_mcp/providers/__init__.py` — stop selecting every adapter

The package currently imports both `cloudcli` and `fleet_node` eagerly, and
`fleet_node` -> `registry` -> `fleet.transport`. That is why a Node payload that
only wants `CloudCliProvider` still needed a checkout. Split construction out:

* new `fleet_mcp/providers/build.py` owns `build_provider` / `build_providers`
  and imports `cloudcli` + `fleet_node`;
* `fleet_mcp/providers/__init__.py` re-exports the base names and resolves
  `CloudCliProvider`, `FleetNodeProvider`, `build_provider`, `build_providers`
  lazily through module `__getattr__`;
* `fleet_mcp/fleet.py` imports `build_providers` from `.providers.build` and
  `DeviceProvider` / `ProviderHealth` from `.providers.base`.

Interfaces unchanged: `build_provider(binding) -> DeviceProvider`,
`build_providers(config) -> dict[str, DeviceProvider]`, and the package's
`__all__` remains import-compatible.

Effect measured: `fleet_mcp.providers.fleet_node`, `fleet_mcp.registry` and
`fleet_mcp.fleet` leave the Node closure; the Hub direction is zero.

## What the release lane will do after Phase 1 lands

1. Build four units with the adapter, pinned to #431's builder SHA
   `7f87a596…` and the Base allowlist of 19 modules.
2. Install `fleet-runtime-base` and `fleet-authority` once; install Hub and
   Node independently. Neither carries the other's file.
3. Verify the SHA manifests twice, boot both units in temporary prefixes with
   no checkout, reverse-delete the Node unit, roll the Hub back, and assert the
   Legacy Fleet 0.1.0 fake does not move.
4. A real device co-install still requires **separate permission** and remains
   `NOT_RUN` in this lane.

## Acceptance for Phase 1

* `git apply --check` is clean on `main` after the CUT-B commit.
* `python -m pytest tests/test_fleet_l2_release_separation.py -q` stays green.
* Hub -> Node imports: 0.
* Node -> Hub imports: 9, each one in `BASE_ADMISSIBLE`.
* No second `DeviceRef`, `Device`, `DeviceRegistry` or `canonical_level` is
  introduced anywhere.

## What this handoff does NOT ask for

* No change to `scripts/release.py`, `runtime/**`, Legacy live configuration,
  the gateway, the plugin, or the Creator surface.
* No new device identity, no copied `fleet/transport.py` inside `fleet_mcp`, no
  fallback registry data.
* No merge of #407's V2 draft, #461/#467/#473's V2-rooted stack, or #466.
