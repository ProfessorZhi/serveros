# The Core-Lite readback seam: who establishes what, and who may not

`fleet_mcp/core_lite_readback.py` is a **pure** reader for
`serveros/core-lite-status.v1`. It is delivered on its own, before anything
calls it, because the question it answers — *may this reading be shown as this
device's?* — has to be settled before a caller exists that would like the answer
to be yes.

## The one thing this module refuses

A Core-Lite document names its own `node`, its own `principal` and its own
`live` flag. None of those may be believed. A document that can name itself can
name itself healthy, and the source contract says as much: nothing in V1 can
attest a **device** reference, so every document it emits today is `live: false`
with `device_identity.basis = NONE` or `LOCAL_DECLARATION`.

That is why the reader's inputs are split the way they are:

| input | who supplies it | why it cannot come from the document |
|---|---|---|
| `expected_device_ref` | the adapter, from the route it already holds | the document's `node` is a claim; the route is the fact |
| `binding_account` | the Fleet binding's OS account | the document's `principal` is a claim; the binding is the owner truth |
| `verified_route` | the adapter, after it actually authenticated | defaults to `False`; with it false the summary is `NOT_CONNECTED` whatever the document says |
| `received_at` | the caller's clock | so freshness is reproducible and the module never reads a clock |

The document's own values are still reported — under `device.declared`,
`principal.declared`, `source.declared_device_basis`, `live.claimed` — as
**claims to compare**. They never reach the identity fields of the row.

## What the verdict can be, and what it cannot

`connected` is one of `CONNECTED` / `NOT_CONNECTED` / `UNKNOWN`. There is no
healthy. `CONNECTED` requires all three of: a verified route, a device
declaration matching the route, and a principal matching the binding, on a fresh
stamp. A `MISMATCH` anywhere is `NOT_CONNECTED`; anything unestablished is
`UNKNOWN`.

A connected device whose every observation is `UNAVAILABLE` is a normal result.
Established identity and healthy readings are different questions, and the
source contract separates them deliberately.

## Where each question is verified — the layer that owns it

| question | verified by | this module's part |
|---|---|---|
| does this route reach this device? | the **device Node Adapter**, over the authenticated provider route | receives `expected_device_ref` and `verified_route` |
| is this OS account the one the binding owns? | the **binding loader** (`fleet_mcp/config.py`, `provider.account`) | compares `binding_account` against the document |
| is the transport actually the Core and not something else on the path? | the **provider**, before it returns a document | receives only what the provider already authenticated |
| is this reading fresh? | the **caller**, by passing `received_at` and a bound | computes the age; enforces nothing itself |
| may this be displayed as this device's? | **this module** | the whole of its job |

Nothing here opens a socket, reads a file, or starts a process. A static test
walks the module's syntax tree and asserts the imports and calls that would let
it — so a later change that tries to grow an egress fails a test rather than a
review.

## Why the FleetService constructor is NOT wired in this PR

The obvious next step is an explicitly injected `core_lite_source` callable on
`FleetService.__init__`, used only by the display layer of `list_devices` and
`status`. It is **not** done here, and the reason is a contract one rather than
a code one:

- `fleet_list_devices` and `fleet_status` are two of the nine hand-written tools
  behind `TOOL_SURFACE_REVISION = "fleet-mcp-3"`. Adding a `core_lite` key to
  every device row changes the shape of what those tools return, and this
  repository's own rule is that a tool's contract changing shape is what moves
  the surface revision — a client that cached `tools/list` and its annotations
  has to be told.
- Doing it quietly inside a module-addition PR would be exactly the kind of
  shape change a caching client discovers by breaking.

So the seam is delivered as a pure function with its own tests, and the wiring
is a separate change that can carry the surface-revision decision openly. The
proposed signature, for that change:

```python
def __init__(self, config, *, registry=None, providers=None,
             core_lite_source: Callable[[str], dict | None] | None = None):
```

with two invariants stated up front, because they are the ones that would be
tempting to break:

1. **`None` is the default and means UNKNOWN, not healthy.** A device with no
   source reports `unconnected_summary(...)`, which says `UNKNOWN` and lists no
   observations. It never renders as an empty-but-fine row.
2. **The callable must not be a transport.** It is handed a `device_ref` and
   returns a document the *provider* already fetched over the route it owns. A
   source that opens its own socket would be a second management protocol
   wearing a callable's clothes, and the Fleet side would have no way to tell.

## What this does not do

No MCP tool is added; the count stays 9 + 22. No OAuth scope changes; the
implemented set is untouched. No shell, SSH or generic proxy surface appears.
No `CoreTelemetry` is fabricated for any real enrolled device, and no second
administrative protocol is opened.
