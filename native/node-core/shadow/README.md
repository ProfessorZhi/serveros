# shadow — the Phase 3 seam between the native envelope and the Fleet view

`native_shadow.py` is the seam that Phase 3 adds between two branches that
already exist and that must not be merged into each other:

| | branch | what it owns |
|---|---|---|
| PR#161 | `feat/native-node-core-cpp-20261013` @ `9dca6fd` | the C++20 collector and the bounded Python adapter that emits `serveros/native-node-measurement.v1` |
| PR#160 | `feat/serveros-fleet-card-v1-20261013` @ `106d0f73` | the Fleet monitor card and `accept_native_measurement`, which turns that envelope into a device entry |

Neither file is reinvented here. The collector and the adapter are used
unchanged; the card and its renderer are not touched at all.

## The flag is off, and has no default

```python
shadow.feature_enabled()                       # (False, "…off…")
shadow.feature_enabled(enabled=True)           # (True, None)
shadow.feature_enabled(env={"SERVEROS_NATIVE_NODE_CORE_SHADOW": "1"})   # (True, None)
```

`ENABLED_BY_DEFAULT` is `False` and `shadow()`'s `enabled` has no default,
mirroring the adapter's own `collect(..., enabled=True)`. A disabled pass opens
nothing, starts no child and writes no file — a test asserts the receipt
directory is not even created on the way to the refusal.

## What a pass produces

```python
document = shadow.shadow(enabled=True, envelope=parsed_envelope,   # or an executable
                         python_reading=inventory_reading,
                         now=..., within_seconds=300)              # §18's window
served   = shadow.served(python_reading=inventory_reading, document=document)
```

`served()["values"]` **is** the Python inventory, unchanged. The native reading
lives under `served()["shadow"]` and `document["parity"]`, and there is no code
path from the collector to a served value. `document["state"]` is one of:

| state | meaning |
|---|---|
| `DISABLED` | the flag was off; nothing ran |
| `SHADOW` | a reading came back and conforms to the seam contract |
| `UNKNOWN` | no reading came back — the reason is the reading's own |
| `REFUSED` | it claims a reading but breaks a rule the Fleet bridge enforces |

## Parity, and the one join that would be silently wrong

`parity()` compares the native reading against the Python inventory, quantity by
quantity, with verdicts `EXACT` / `WITHIN` / `DIFFERS` / `NOT_COMPARABLE`. A
quantity one side did not answer is `NOT_COMPARABLE` with a reason — never a
delta computed against a zero.

The one join that survives review while being wrong by 22 GiB is named in the
row itself:

| | field | means |
|---|---|---|
| Python `runtime/inventory.py` role row | `free_bytes` | df's **Avail** column, `statvfs f_bavail` |
| collector | `available_bytes` | `statvfs f_bavail` — the same number |
| collector | `free_bytes` | `statvfs f_bfree` — **includes the blocks reserved for root** |

So the room-left row joins Python `free_bytes` to native `available_bytes`, and a
test refuses any row that names native `free_bytes` as that side. On `/data`
(xfs, which reserves nothing) the wrong join gives the same answer, which is
exactly why a test is needed and a reading is not.

`MemAvailable` is the one moving gauge, and its row is marked
`informational: true`: it churns between two instants by design, so a byte-exact
rule would call both producers wrong on a host where both are right.

## The frozen contract, and the fixture it is checked against

`contract/native-to-fleet-contract.v1.json` is the set of rules PR#160's bridge
enforces, transcribed so this branch can prove the envelope it produces is one
that bridge will accept. It is a document rather than code because the bridge it
describes is on another branch: a copy in code here and a copy there would
disagree the first time either moved.

`fixtures/pr160/` holds two files recorded **read-only** from PR#160's own blobs
(`git show 106d0f73:<path>`) — the envelope it consumes and the device entry it
produces. Neither was edited, and neither PR#160's branch nor its working tree
was touched. `reconciliation` inside the contract names the step that collapses
the transcription once the branches meet.

### A seam finding this turned up

PR#161's `_failure_envelope` builds `provenance` from the invocation receipt and
never sets `live`; only `translate()` sets it. PR#160's bridge checks
`provenance.live` is a boolean **before** its own `readings_available is not True`
branch. So the envelope the adapter actually emits when the collector exits 2
raises `NativeMeasurementRefused` instead of reaching the fail-closed device
entry that branch exists to produce. PR#160's own test misses it because it flips
a *success* envelope's `readings_available` rather than using one the adapter
emits.

The seam reports this rather than repairing it — repairing it means editing a
file PR#161 owns, or a file on another branch.

## What this does not do

* No daemon, service or timer, and no schedule. It is called by a caller.
* No second subprocess runner: when it needs the collector it calls PR#161's
  `collect()`, which calls `runtime/bounded_invocation.invoke_once`. A test
  parses this module's AST to prove it contains no exec call of its own, which is
  also what keeps `mutation_guard.py` from reporting a new unreviewed actuator.
* No reachability of its own: nothing under `runtime/`, `mcp/`, `context/`,
  `fleet/`, `abilities/`, `bin/`, `scripts/` or `intake/` mentions this module,
  so it cannot enter `serverosd`'s loader closure that
  `tests/test_core_freeze.py` asserts.
* No per-account division of a host-wide figure. Every measurement here is
  machine-wide; per-UID attribution is a different producer (ServerOS issue
  #167) and is never inferred from the collector's own uid.

## Tests

```sh
( ulimit -v 8000000; timeout 300 \
  /data/home/vince/toolchains/serveros-test/bin/pytest \
  tests/test_native_node_core_shadow.py -q -p no:cacheprovider )
```

34 tests. The process half runs a real child through
`tests/fake_native_collector.py` (`ok` / `exit2` / `hang` / `badjson`), because
what the seam does with a collector that exits 2 or hangs is the whole of what it
is for and is not a property of a mock. The clock is injected everywhere it can
be. Three are architectural: the AST check above, the wired-into-nothing check,
and one that refuses a second copy of `runtime/graph.py`'s freshness window
table.
