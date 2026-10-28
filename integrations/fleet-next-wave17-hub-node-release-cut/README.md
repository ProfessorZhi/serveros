# Wave 17 · Hub / Node independent release cut

Status: **Draft · SOURCE/OFFLINE only · BLOCKED_OWNER for a real release**

This directory measures whether the Fleet **Hub** and **Node** can be released,
installed and rolled back separately, and produces the exact patch handoff that
would make the answer yes. It does not modify `fleet_mcp/**`, `runtime/**`,
`scripts/release.py`, #431's source, or any Legacy configuration.

## What is here

| path | what it is |
|---|---|
| `wave17_cut.py` | the measurement and packaging adapter; reuses #431's builder by SHA pin |
| `handoff/apply_handoff.py` | applies the owner handoff to a **copy**; refuses a checkout |
| `handoff/cut_a_main_rebase.patch` | #461's CUT-A, mechanically rebased to `main@e69fc03` for the proof |
| `handoff/cut_b_hub_node_seam.patch` | this lane's CUT-B request to the `fleet_mcp/**` owner |
| `handoff/HANDOFF.md` | the exact files, interfaces, two-phase plan and acceptance |
| `evidence/wave17-evidence.json` | the RED -> GREEN evidence pack, reproducible from the commit it names |
| `tests/test_fleet_next_wave17_hub_node_release_cut.py` | 13 offline tests (RED baseline, GREEN sandbox, manifests, isolation, rollback) |
| `docs/integration/FLEET-NEXT-WAVE17-HUB-NODE-CUT-20261028.md` | the report |

## The verdict in one table

| | live tree (`main` + #431) | sandbox with handoff |
|---|---|---|
| Hub files owned | 49 | 50 (`providers/build.py` added) |
| Node files owned | 180 | 180 |
| Hub -> Node imports | 1 (`owner_setup` -> `node.connector`) | **0** |
| Node -> Hub imports | 10 | 9, all satisfiable by the pinned Base |
| Base / Hub / Node closure | 30 / 3 / 0 | 19 / 13 / 2 |
| Two-artifact Hub boot | fails (`ModuleNotFoundError`) | — |
| Two-artifact Node boot | fails (`ModuleNotFoundError`) | — |
| Three-unit Hub boot (Base + Authority) | — | **passes** |
| Three-unit Node boot (Base only) | — | **passes** |
| `technical_state` | `BLOCKED_OWNER` | `GREEN_SANDBOX` |
| `decision` | `BLOCKED_OWNER` | `BLOCKED_OWNER` |
| Real device co-install | `NOT_RUN` | `NOT_RUN` |

`GREEN_SANDBOX` is a statement about release units, not a release. The cuts are
not landed, and no device was touched.

## Why three units and not two

A strictly two-artifact split cannot be honest on today's source: the Node
genuinely needs `config`, `errors`, `identity`, `local_roots`, `node_protocol`,
`owner_access`, `providers.cloudcli` and `runtime_identity` at runtime, and the
Hub needs them too. Copying those into both artifacts would be duplication
dressed as independence; moving them into one half would be a takeover.

So the adapter declares a **third, shared unit**:

```
fleet-runtime-base   19 modules, pinned by `BASE_ADMISSIBLE` in both directions
fleet-authority      fleet/transport.py + its contract; the ONE DeviceRef
fleet-hub            13 modules, no Node file
fleet-node           2 modules, no Hub file
```

The Base is a pin, not a dumping ground: a shared module not on the list fails,
and a list entry the closures no longer share fails too. The only identity
authority is `fleet/transport.py`; no unit defines a second `DeviceRef`.

## Reproduce

```bash
# RED on the live tree, no writes, no services
python3 -I integrations/fleet-next-wave17-hub-node-release-cut/wave17_cut.py --root . --json

# GREEN on a throwaway copy with the owner handoff applied
python3 -I integrations/fleet-next-wave17-hub-node-release-cut/wave17_cut.py \
    --root . --sandbox --isolation --json

# the tests
python3 -m pytest tests/test_fleet_next_wave17_hub_node_release_cut.py -q
```

Nothing above installs, starts, binds a port, opens a socket, reads a
credential, sends a message or changes the repository.
