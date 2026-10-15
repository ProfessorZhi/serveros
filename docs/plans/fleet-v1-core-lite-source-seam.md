# Admitting a Core-Lite reading into Fleet

`fleet_mcp/core_lite_readback.py` (PR #214) reads one document and decides
whether it may be shown as one device's. `fleet_mcp/core_lite_source.py` decides
something further upstream: **may this document be attributed to that device at
all?** This note records where the boundary sits and why the wiring stops here.

## The three questions that must not be collapsed

| question | answered by | never by |
|---|---|---|
| is the provider's transport up? | `ProviderHealth.reachable` | — |
| did a Core-Lite document arrive over an authenticated route? | `CoreLiteFetch.route`, set by the provider | the health probe |
| does that document describe this device? | `core_lite_readback`, from the route's `device_ref` | the document's `node` |

Collapsing the first into the second is how a device comes to be reported as
Core-connected because a TCP connection succeeded. `route.authenticated` is
therefore an explicit field a source must set, and the module never derives it —
a test asserts that a provider whose `health().reachable` is `True` still
produces `NOT_CONFIGURED` / `NOT_CONNECTED` when no route was authenticated.

## Two kinds of "no"

`UNKNOWN` is *nothing was established about this device*. `NOT_CONNECTED` is
*something was established, and it was not this*. The route states keep them
apart:

| state | `connected` | means |
|---|---|---|
| `NOT_CONFIGURED` | UNKNOWN | no source is wired on this surface |
| `SOURCE_FAILED` | UNKNOWN | the source raised — a Fleet-side fault, not a device one |
| `UNAUTHENTICATED` | NOT_CONNECTED | no authenticated route to this device |
| `DEVICE_MISMATCH` | NOT_CONNECTED | the route reaches a different device |
| `NO_DOCUMENT` | UNKNOWN | authenticated, right device, nothing came back |
| `VERIFIED` | *from the reader* | the document may be read |

Reporting `DEVICE_MISMATCH` as merely `UNKNOWN` would invite a retry that can
only fail the same way, so the distinction is load-bearing rather than cosmetic.

## Where the wiring stops, and why

`FleetService` gains one optional constructor argument (`core_lite_source=None`)
and one method (`core_lite_status`), which is **not an MCP tool**. Nothing in
`tools.py` names it, `list_devices` and `status` do not call it, and no route is
declared for it. The served surface is unchanged: 9 hand-written tools plus the
22 inherited verbs, six scopes, `TOOL_SURFACE_REVISION = "fleet-mcp-3"`.

Adding a `core_lite` field to every `fleet_list_devices` row would be a change to
a tool's contract shape — which `fleet_mcp/version.py` says is exactly what
`TOOL_SURFACE_REVISION` exists to signal to a client that cached `tools/list`.
That decision belongs in the change that makes it, openly. A regression test
compares the full `list_devices` and `status` output with and without a
configured source and requires them to be identical.

**And there is nothing to connect it to.** No live Node exposes a trusted Core
API, so a source today could only be one that makes a device look Core-connected
without an authenticated route — which is the thing this module refuses. The
seam is delivered so that the day a provider can state a route, the change is
one constructor argument and no contract edit.

## What a source must never be

The protocol takes one argument — the device the caller already has a route to —
and returns a document plus the route it arrived on. No URL, no command, no
options: a source that needs those is a transport, and a second transport is a
second management protocol wearing this seam's clothes. A static test walks the
module's syntax tree and asserts it imports nothing that can leave and calls
nothing that can execute.
