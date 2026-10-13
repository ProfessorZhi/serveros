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
* The test suite is deterministic unit coverage over fixtures. It is not a
  live-host conformance run against the Python core, which is the next step.

## Tests

Five suites, 247 checks, no external test framework. Every system read goes
through a `FileSource`, and the fixture implementation answers `ENOENT` for any
path nobody registered — so a test cannot pass because a fake quietly supplied
something plausible. No test sleeps: a delta is two `Sample` values a test states
by hand.

```sh
"$BUILD/tests/test_snapshot" /tmp/emitted-docs   # also writes its documents out
```

The documents `test_snapshot` writes are what `contract/node-resource-snapshot.v1.schema.json`
is checked against, including the unavailable-heavy shapes a healthy host never
produces on demand.
