# node-core — native resource collector (C++20)

A read-only, on-demand reading of one machine, printed as one
`serveros/node-resource-snapshot.v1` JSON document to stdout.

This is the first working slice of a native resource collector for the existing
ServerOS Node Core. It is **not** a second daemon, a second state authority, or a
second OS. It samples, it prints, it exits.

## What it is not

The architecture's resident surface is frozen and unchanged
(`ARCHITECTURE.md` §21 "The resident surface"): exactly one thing is resident and
it is `serverosd`. This program is not resident and is not a refused-daemon name
wearing a binary. It holds no state between runs, writes no file, opens nothing
for writing, takes no lock, and starts and stops no service.

It also does not restate the core's high-level policy. There is no four-axis
health model here, no managed-runtime identity, no admission, no ownership
judgement and no work authority. Those stay where they are. This program's whole
job is to state what the kernel said, and to say plainly what it could not read.

## Build

Requires only a C++20 compiler and a standard library. There is no third-party
dependency: the JSON writer, the parsers and the test harness are all in-tree, and
the JSON writer is in-tree on purpose — the document's byte-identity is a contract
with the Python adapter, and a third-party serialiser's key order and float
formatting are not ours to promise.

```sh
# CMake is not installed system-wide on this host, so this tree uses the
# user-space copy under TOOLCHAINS_ROOT. Any CMake >= 3.20 will do.
CMAKE=/data/home/vince/toolchains/cmake-native/cmake/data/bin/cmake

# Build somewhere that is not the source tree.
BUILD=/data/home/vince/artifacts/work-items/serveros/native-node-core-cpp-20261013/run-20261013/build
"$CMAKE" -S native/node-core -B "$BUILD" -DCMAKE_BUILD_TYPE=Release
"$CMAKE" --build "$BUILD" -j4
```

`ctest` from the same installation:

```sh
/data/home/vince/toolchains/cmake-native/cmake/data/bin/ctest --test-dir "$BUILD" --output-on-failure
```

## Run

```sh
"$BUILD/serveros-node-resource-snapshot"                 # 500 ms between the two samples
"$BUILD/serveros-node-resource-snapshot" --interval-ms 0 # every delta will be UNAVAILABLE
"$BUILD/serveros-node-resource-snapshot" --compact       # canonical single-line form
"$BUILD/serveros-node-resource-snapshot" --filesystem ROOT_FS=/ --filesystem DATA_FS=/data
"$BUILD/serveros-node-resource-snapshot" --interface eth0
```

Measured on this host (128 vCPU, shared, other Agents active): **4.1 MB peak RSS,
below 1% CPU, wall time = the interval**. It reads six `/proc` files, one flat
`/sys/class/net` listing, and a handful of sysfs attributes per interface. It does
not walk the tree, scan `/proc/*/cmdline`, or read any file for writing.

## The one rule that governs every field

> A fact that could not be measured is `{"state": "UNAVAILABLE", "reason": "..."}`.
> It is never `0` and never `null`.

This mirrors `runtime/inventory.py:93` `_unavailable()` exactly, and the reason is
stated at `runtime/inventory.py:6`: an unobservable value reported as zero is a
claim the host cannot support. A `0` in `read_bytes` reads as "this device did no
IO"; on this host that is often true and is therefore *measured*, and the two must
be distinguishable.

Every unavailable field is also listed, with its reason, in the document's
`unknowns` array — so a reader does not have to walk the document to find out what
is missing.

Consequences worth knowing:

* **A counter that went backwards is refused, not rewound.** A reset, a recreated
  interface and a 32-bit wrap all look identical from here, so the delta is
  `UNAVAILABLE`. Reconstructing a wrapped value would invent a large, plausible,
  wrong number of bytes. (This is deliberately stricter than
  `runtime/inventory.py:985`, which computes `after - before` for disk and paging
  with no negative guard.)
* **A zero-length window is not an idle machine.** Zero interval, or two reads
  whose `/proc/uptime` did not advance, refuses every delta. Busy and total jiffy
  deltas of zero are refused for the same reason, matching
  `runtime/inventory.py:696`.
* **A reboot between the two samples invalidates every counter in the document.**
  The boot id is the evidence, and a changed one refuses all deltas.

## What it reports

| Block | Content |
|---|---|
| `cpu` | whole-machine `/proc/stat` deltas: busy/total jiffies, `used_percent`, and the eight counters. `iowait` counts as idle. |
| `memory` | `/proc/meminfo` `MemTotal` / `MemAvailable` / `MemFree`, in bytes, labelled `scope: host-wide`. |
| `filesystems.roles` | per **role** (`ROOT_FS`, `DATA_FS`, …), not per device name: the path asked about, the mount it lands on, bytes, and inodes as a separate exhaustion axis. |
| `disk_io.roles` | per role whose mount source is a real block device: read/write byte deltas and rates. Unmapped mounts and devices with no role are named rather than dropped. |
| `interfaces[]` | every interface `/sys/class/net` lists, sorted: `active` from `operstate`, virtual/physical with the sysfs path it was decided from, rx/tx byte deltas and rates, `speed_mbps`, and per-direction link utilisation. |
| `attribution` | this process's uid, its own cgroup path, and that cgroup's own `memory.current`. |
| `unknowns[]` | every field above that could not be measured, with its reason. |

### Two things stated rather than implied

**`sector_size_bytes: 512` is in the document.** The kernel counts
`/proc/diskstats` in fixed 512-byte units no matter what the device's logical
sector size is. A reader who assumes the device's own sector size is wrong by a
factor of eight on a 4K device, so the constant travels with the number rather
than being left to a reader's habit.

**`speed_mbps` is new vocabulary.** There is no NIC-speed field anywhere in the
Python core — `docs/NODE-OBSERVABILITY.md:35` names link speed as the missing
denominator and forbids reporting link utilisation without one. This collector
supplies the denominator, so it is inventing this field, and it says so here
rather than implying a precedent that does not exist. On this host `eth0` is
virtio_net, which answers `-1`; that is a readable attribute whose value means
"no speed reported", so it becomes `UNAVAILABLE` and the utilisation figures
become `UNAVAILABLE` too, rather than `0%`.

## Sharing with the Python core

The names are chosen to match what the Python core already does, so a future
adapter is a translation and not a reconciliation:

* `state` / `reason` objects, from `runtime/inventory.py:93`.
* `observed_at` in ISO-8601 UTC with an explicit `+00:00` offset.
* monotonic seconds read from `/proc/uptime`, the same source and unit as
  `runtime/inventory.py:1122`, so an interval measured here and one measured there
  are the same number.
* `*_bytes` and `*_per_second` unit-in-name fields; `sample_seconds` on every delta.
* filesystem **roles** (`ROOT_FS`, `DATA_FS`, …) from `runtime/inventory.py:434`,
  because `/dev/vda2` means nothing to a policy that says "protect the system disk".
* `"schema": "serveros/<kebab-name>.v1"`, and keys emitted in lexicographic order,
  which reproduces the `sort_keys=True, ensure_ascii=False` canonical payload form
  the core uses for its content hashes (`runtime/card_delivery.py:232`).
  `--compact` emits exactly that form.

**`serverosd` and the Python core remain the authority.** This program supplies
measurements; a measurement is not a policy. It is reached by spawning it, the
way a Provider is, so it does not enter the core's loader closure that
`tests/test_core_freeze.py` asserts.

## The Python adapter

`adapter/native_node_core.py` runs the compiled collector under a bound and
translates its document into a measurement envelope. It is a **library**
(mode 0644 on purpose — see below), it is invoked and never imported, and it
schedules nothing.

```sh
python3 native/node-core/adapter/native_node_core.py --enabled \
  --executable "$BUILD/serveros-node-resource-snapshot" \
  --receipts /a/private/0700/dir --key "$(date -u +%Y%m%dT%H%M%SZ)" \
  --within-seconds 300 --filesystem ROOT_FS=/ --filesystem DATA_FS=/data
```

**Opt-in, and no default.** `collect()` requires `enabled=True`; the CLI requires
`--enabled`. A caller that has not decided to measure the host cannot measure it
by omission, and a module-level side effect cannot measure anything.
`tests/test_native_node_core_adapter.py` also asserts the second half of that:
nothing under `runtime/`, `mcp/`, `context/`, `fleet/`, `abilities/`, `bin/`,
`scripts/` or `intake/` mentions this module, so it cannot enter `serverosd`'s
loader closure that `tests/test_core_freeze.py` asserts.

**The bound is the tree's, not a new one.** It calls
`runtime/bounded_invocation.invoke_once`: an argv **list** (so there is no shell
and no `shell=True` to disable), a wall timeout, a per-stream byte cap, a
process-group kill on timeout, and one receipt shape for every outcome. This
module grew no second subprocess runner, and it contains no exec call of its own —
which a test asserts by parsing its AST.

**The actuator contract is satisfied by delegation.** `runtime/actuator-contract.yaml`
plus `skills/serveros/scripts/mutation_guard.py` check in both directions that
every place this product can run a command is declared once. Measured, not
assumed: the guard scans this file (it walks `root.rglob("*.py")`, 311 sources on
this tree), finds **0** exec hits in it and status `CHECKED` with 0 findings,
because the reachability lives in `runtime/bounded_invocation.invoke_once`, which
is already declared. Adding a declaration for this adapter would therefore
produce a declaration with no hit — `STALE_ACTUATOR`, "a permission that outlived
the code it was granted for". So it has none, on purpose.

### The envelope

`schema: serveros/native-node-measurement.v1`

| Key | Content |
|---|---|
| `provenance` | collector name/version/language/`live`, the executable, the argv, the action key, the receipt's `status`/`process_exit_code`/`direct_process_exited`/`effect`/`output_sha256`, `executed_now`, `replayed`, and `stdout_bytes`. |
| `sample` | `observed_at`, `sample_started_at`, `sample_seconds`, `monotonic_seconds`, `boot_id`, and `enveloped_at` — collection time is not render time is not the time a person read it. |
| `freshness` | `runtime/graph.py`'s own `freshness_of`, unchanged: `FRESH`/`STALE`/`UNKNOWN` with `age_seconds` and `within_seconds`. |
| `measurements` | `cpu`, `memory`, `filesystems`, `disk_io`, `interfaces` — carried through **unchanged**, nested `UNAVAILABLE` objects and all. Nothing is renamed, because a rename is an interpretation. |
| `attribution` | Kept out of `measurements` and marked `scope: "self"`: it is the only part of the document about one account. |
| `unknowns` | The collector's list, plus `freshness` when it genuinely is unknown — and not when it was measured. |
| `refusals` | What this envelope must not be read as. |
| `field_semantics` | Where a consumer joining these fields to the Python inventory by name will get the wrong number. |

`readings_available` is `false` for a child that exited nonzero, hung, printed
something that is not JSON, exceeded a bound, or returned a document under a
`schema` this adapter does not implement. A failure is returned in the **same
shape** as a success, with the reason in `measurements`, so a caller reads one
key set either way.

### Freshness windows are not invented here

`runtime/graph.py`'s `FRESH_WITHIN_SECONDS` already declares §18's
resource-observation windows (`host-filesystem`, `host-memory`: 300 s). This
adapter declares no window of its own — a test asserts it restates none of that
table — so with no window declared for `native-node-core`, freshness comes back
`UNKNOWN` with the reason rather than `FRESH`. The caller declares the window, and
§18's 300 s is the number to pass.

### Reading versus replay

`invoke_once` is idempotent per action key, and this adapter takes the key from
its caller rather than content-addressing it. That is deliberate: a
content-addressed key would replay the **first** reading forever, because a
snapshot's argv does not vary — every later call would return a stale document
labelled as the current one. The caller decides, and the envelope always reports
which happened (`executed_now` / `replayed`), so a replay is never passed off as
a new reading.

## Shadow parity with the existing Python core

Run on `device:tokyo-shared-dev-01` against `runtime/inventory.py`'s default
`LiveHost` (its own docstring: "Read-only by construction: no method here has any
means of writing"). Read-only in both directions; nothing on a production path.
`Inventory.disk_io()` was **skipped** — it reaches `docker system df`, ~22 s on
this host, and the question asked here does not need it.

| Quantity | Native | Python inventory | Delta |
|---|---|---|---|
| `ROOT_FS.total_bytes` | 540987379712 | 540987379712 | **0 (exact)** |
| `DATA_FS.total_bytes` | 4395909496832 | 4395909496832 | **0 (exact)** |
| `ROOT_FS` usable-free | 480523239424 | 480523239424 | **0 (exact)** |
| `DATA_FS` usable-free | 3234976690176 | 3234976665600 | 24576 (one block) |
| `ROOT_FS.used_percent` | 7.4 | 7.4 | **0.0** |
| `DATA_FS.used_percent` | 26.4 | 26.4 | **0.0** |
| `memory.total_bytes` | 265182715904 | 265182715904 | **0 (exact)** |
| `role.source`, `role.fstype` | `/dev/vda2` `ext4`, `/dev/vdb` `xfs` | same | **equal** |
| `cpu.used_percent` | 11.7 (0.300 s window) | 12.2 (0.150 s window) | informational, different windows |

The one moving gauge, `memory.available_bytes`, was attributed rather than waved
at. Read **7.2 ms** apart on an otherwise unchanged host, the two producers agree
to **2.59 MB** on a 265 GB figure (0.0014%). Read **1.16 s** apart — which is
what `Inventory()`'s own construction and shell-outs cost — they differ by 59 MB,
and the host's own churn rate across that gap (51 MB/s, measured) accounts for it
exactly. Every quantity that cannot move between two instants agrees exactly.

### A real difference this found

The two producers spell "room left" differently, and a consumer joining them by
field name would be wrong silently:

| | meaning | on `/` |
|---|---|---|
| `inventory.py` role row `free_bytes` | df's **Avail** column = `statvfs f_bavail` | 480.5 GB |
| collector `free_bytes` | `statvfs f_bfree` — **including** blocks reserved for root | 502.6 GB |
| collector `available_bytes` | `statvfs f_bavail` — what a non-root writer can use | 480.5 GB |

22.1 GB apart on `/`, and identical on `/data` (xfs reserves nothing), which is
exactly the shape of a bug that survives review. Neither name is wrong — the
collector uses the POSIX names and df's column is the odd one — so nothing is
renamed. Instead the envelope carries a `field_semantics` block naming the
collision and the join that is right.

## Coverage boundaries of this slice

Not implemented, and not claimed:

* No per-process or per-account attribution beyond the collector's own uid and
  cgroup. Dividing a host-wide figure up by account is not done, because no
  readable fact on this shared host would support it.
* No reading of any other account's processes, argv, environment or files.
* No GPU, sensors, thermal, routes, socket state, Docker stats, log sizes,
  `systemd` unit state, or inode-exhaustion *policy* (the inode counts are
  reported; judging them is the core's).
* No delta for a device or interface that appeared mid-window, and no
  reconstruction of a wrapped counter.
* **Nothing consumes the envelope yet.** There is no `serverosd` wiring, no
  schedule, and no card. The shadow parity below is a one-off recorded
  verification against the Python inventory, not an integration.

## Tests

**C++ — five suites, 247 checks, no external test framework.** Every system read
goes through a `FileSource`, and the fixture implementation answers `ENOENT` for
any path nobody registered — so a test cannot pass because a fake quietly
supplied something plausible. No test sleeps: a delta is two `Sample` values a
test states by hand.

```sh
"$BUILD/tests/test_snapshot" /tmp/emitted-docs   # also writes its documents out
```

The documents `test_snapshot` writes are what `contract/node-resource-snapshot.v1.schema.json`
is checked against, including the unavailable-heavy shapes a healthy host never
produces on demand.

**Python — `tests/test_native_node_core_adapter.py`, 38 tests.** The process half
runs a real child (`tests/fake_native_collector.py`, mode-switched by
`SERVEROS_FAKE_NATIVE_MODE`) through `invoke_once`, because what the adapter does
with a child that exits 2, hangs, or prints half a document is not a property of
a mock. The pure half exercises `translate()` against
`adapter/fixtures/snapshot.v1.example.json` — a real reading from the real binary
with every host-identifying field replaced — so shape compatibility is asserted
without a compiler at test time. The clock is injected in every test that can
take one.

```sh
uv run --with pytest --no-project pytest tests/test_native_node_core_adapter.py -q
```

Two of those tests are architectural rather than behavioural: one parses the
adapter's AST to prove it runs nothing itself, and one scans the other product
directories to prove it is wired into nothing.
