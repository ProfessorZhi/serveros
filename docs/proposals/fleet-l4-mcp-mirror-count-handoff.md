# BLOCKED_OWNER_HANDOFF — Fleet MCP mirror-count descriptions

- Date: 2026-10-28
- Item: `SRV-0011` (L4 review)
- Cross-reference: serveros issue #434, ability-management issue #39
- Owner of the affected source: `fleet_mcp/**` (root / #407 lane)
- This lane did **not** modify `fleet_mcp/tools.py`.

## Finding

At `fleet-mcp-7` the served surface is:

- `TOOLS` (authored) = **11**
- `NATIVE_MIRROR_TOOLS` = **31**
- `SERVED_TOOLS` = **44** distinct

Two current tool descriptions and three comments still state the older
mirror value **22** (and one comment says "hand-written nine", "all 31"):

- `fleet_cloudcli_native_capability.description`:
  "Read what ONE device ACTUALLY exposes of the 22 inherited verbs … a count
  of 22 is never evidence that 22 answer."
- `fleet_cloudcli_native_manifest.title`:
  "The inherited CloudCLI surface (22 verbs)"
- `fleet_cloudcli_native_manifest.description`:
  "The 22 CloudCLI-native verbs this surface mirrors …"
- comments around `TOOLS_BY_NAME`:
  "hand-written nine", "enumerating 22 generated verbs", "serves all 31".

This is a real schema/description drift: a client that reads the current
`tools/list` sees 44 tools while the capability tool tells it the mirror is 22.
The source implementation is internally consistent (`SERVED_TOOLS` is built
from the 31 mirror entries); only the human/model-facing text is stale.

## Reproduction (read-only, no device)

```sh
cd <worktree>
PYTHONPATH=. python docs/audit/fleet_l4_mcp_drift.py
# rc 1:
# FAIL: fleet_cloudcli_native_capability still says '22 inherited verbs'
#       while the surface mirrors 31
# FAIL: fleet_cloudcli_native_manifest title still says '(22 verbs)'
#       while the surface mirrors 31
```

The source-level tests `tests/test_fleet_mcp_surface.py` etc. pass; they assert
the composed set, not the stale prose.

## Proposed patch

`docs/proposals/fleet-mcp-mirror-count.patch` replaces the current counts with
"the inherited verbs" / "31" / "44" / "the authored set". It changes no
behaviour, schema or tool name. It is **not applied** here because
`fleet_mcp/tools.py` is outside this lane's write ownership.

## Handoff state

`BLOCKED_OWNER_HANDOFF` — root/#407 owner should apply the patch (or instruct
the L4 lane to do so) and re-run the audit. Until then, the L4 receipt records:

- `MCP_SURFACE_COMPOSITION: PASS` (44/31/11)
- `MCP_DESCRIPTION_COUNTS: FAIL` (22 in two current descriptions)
- `MCP_LIVE_E2E: NOT_RUN`
