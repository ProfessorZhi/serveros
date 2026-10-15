# NodeResourceSnapshot v1

`schema: serveros/node-resource-snapshot.v1`

The machine-readable form of this contract is
`node-resource-snapshot.v1.schema.json` (JSON Schema draft 2020-12). This document
states what the schema cannot: the guarantees, and how this interface is allowed
to change.

## What a snapshot is

One reading of one node, taken as two samples a measured interval apart, with
every derived number computed from the difference of the two. It describes
**this machine**, not an account's share of it, except in `attribution`, which
describes only the collector's own uid and cgroup.

A snapshot is evidence. It is not a policy, a verdict, a health state or a
permission. Nothing in it authorizes an effect, and nothing in it should be read
as one.

## The invariant

> A fact that could not be measured is `{"state": "UNAVAILABLE", "reason": "..."}`
> and is never `0` and never `null`.

Zero and unknown are different facts and the document keeps them apart:

* `"read_bytes": 0` means two readings of a real counter were equal — measured,
  and true.
* `"read_bytes": {"state": "UNAVAILABLE", "reason": "counter decreased between
  samples (device reset or counter wrap); the wrapped value is not
  reconstructed"}` means the question could not be answered.

Any consumer that treats the second as the first is misreading the contract.

Every unavailable field is additionally listed in `unknowns[]` as
`{field, reason}`, so "what does this snapshot not know" is answerable without
walking the document.

## Per-field guarantees

| Field | Guarantee |
|---|---|
| `collector.live` | `false` means the facts came from a fixture. A document with `live: false` is never a reading of a host and must not be presented as one. |
| `observed_at`, `sample_started_at` | ISO-8601 UTC, microsecond precision, explicit `+00:00`. |
| `sample_seconds` | Measured from `/proc/uptime`, not from wall time, and refused when the two readings are not separated or the boot id changed. |
| `boot_id` | Evidence that both samples describe the same uptime. A changed boot id refuses every delta in the document. |
| `sample_seconds` on any delta | The window the delta was taken over. A delta is never reported without it. |
| any counter delta | Non-negative when measured. A counter that went backwards yields `UNAVAILABLE`, never a wrapped or clamped value. |
| any `*_per_second` | Present only when both the delta and the interval are known. A rate with no denominator is not a rate. |
| `cpu.used_percent` | `busy / total`, with `iowait` counted as idle, and refused when the total delta is zero — a window that caught no ticks is not an idle machine. |
| `cpu.counters.*` | `guest` and `guest_nice` are excluded from every sum: the kernel already counts them inside `user` and `nice`. |
| `memory.scope` | Always `host-wide`. These are the machine's figures and are never divided by account. |
| `swap.state` | `PRESENT` when **either** half was readable. The two halves come from two files and are reported independently; one being unreadable does not blank the other. |
| `swap.total_bytes` / `free_bytes` / `used_bytes` | From `/proc/meminfo`'s `SwapTotal`/`SwapFree`, in bytes. `used_bytes` is `total - free`, derived the same way `memory`'s used figure is. A machine with no swap reports `0`/`0` — that is a measurement, not an absence. |
| `swap.used_percent` | `used / total`, refused when `SwapTotal` is 0. **This is a STOCK, not a signal.** It falls when a process exits and says nothing about whether the machine is paging. |
| `swap.pages_in` / `pages_out` | The `pswpin`/`pswpout` delta over the window, from `/proc/vmstat`. Refused when either sample is unreadable, when the interval is not measurable, or when either counter went backwards. |
| `swap.pages_in_per_second` / `pages_out_per_second` | **The paging signal.** Present only when both the delta and the interval are known. A `0` here is a measured zero — no page moved in a measurable window — and is not the same as `UNAVAILABLE`. |
| `psi.resources[]` | One row per resource the kernel exposes (`cpu`, `memory`, `io`), always all of them. A resource whose file is unreadable is present with `state: UNAVAILABLE` and a reason, so "this kernel has no PSI" and "nobody looked" cannot look the same. |
| `psi.resources[].some` / `full` | `avg10`/`avg60`/`avg300` are percentages in `[0,100]` — the share of that window in which at least one task (`some`) or every task (`full`) was stalled. The kernel windows them, so **one sample is a complete reading and no second sample is needed**. |
| `psi.resources[].full` | `UNAVAILABLE` with a reason on kernels that do not provide it for a resource (`cpu` on older kernels). Never `0`: "no task was ever fully stalled" and "this kernel does not report `full`" are different facts, and a zero would state the first. |
| `psi.resources[].*.total_microseconds` | Cumulative since boot, a raw monotonic counter with no window. The averages above are the kernel's own and are **not** derived from it here. |
| `filesystems.roles[].role` | A role (`ROOT_FS`, `DATA_FS`, …), never a device name — `/dev/vda2` means nothing to a policy that says "protect the system disk". |
| `filesystems.roles[].mount_point` | The mount the path lands on, which is not always the path asked about. |
| `filesystems.roles[].free_bytes` | `statvfs f_bfree`: every free block, **including** those reserved for root. **This is not `runtime/inventory.py`'s role-row `free_bytes`**, which is df's Avail column (`f_bavail`). Measured on this host: 22.1 GB apart on `/`, identical on `/data` (xfs reserves nothing) — a join by name is therefore wrong only on filesystems that reserve blocks, which is the shape of a defect that survives review. Join on `available_bytes`. |
| `filesystems.roles[].available_bytes` | `statvfs f_bavail`: the free blocks a non-root writer can use, and the field that does line up with `runtime/inventory.py`'s role-row `free_bytes`. |
| `filesystems.roles[].used_percent` | `used / (used + available)`: df's arithmetic, excluding blocks reserved for root. |
| `filesystems.roles[].inodes` | A separate exhaustion axis, with its own state. "No inode budget" (`tmpfs`) and "an empty inode budget" are different facts. |
| `disk_io.roles[]` | Only for mounts whose source is a real kernel block device name. Mounts that are not (overlay, tmpfs, bind, `/dev/mapper/*`, `/dev/disk/by-*/*`) appear in `unmapped[]` with a reason. |
| `disk_io.sector_size_bytes` | Always `512`. The kernel's fixed unit for `/proc/diskstats`, **not** the device's logical sector size. |
| `disk_io.devices_without_a_role` | Devices the kernel lists that this snapshot has no role for. Named, so a missing row is never read as a missing device. |
| `interfaces[]` | Every interface `/sys/class/net` lists, in sorted order, deterministic across runs. |
| `interfaces[].kind` | `physical` or `virtual`, reported with `kind_source`, the sysfs path it was decided from, so the judgement can be disagreed with. |
| `interfaces[].active` | `operstate == "up"`. |
| `interfaces[].speed_mbps` | From `/sys/class/net/<if>/speed`. `-1` (virtio_net reports this) and a failed read (`lo` returns `EINVAL`) are both `UNAVAILABLE`, never `0`. |
| `interfaces[].*_utilisation_percent` | Present only when the link speed is known. No denominator, no utilisation figure. |
| `attribution` | Only this process's uid, its own cgroup path, and that cgroup's own `memory.current`. No other account's processes, argv, environment or files are read anywhere in this collector. |
| `unknowns[]` | Sorted by field name. Contains one entry per unavailable field. |

## Identity and byte-stability

Keys are emitted in lexicographic order. With `--compact` (no whitespace at all,
`": "` becoming `":"`) the output reproduces the Python core's canonical payload
form — `sort_keys=True, ensure_ascii=False, separators=(",", ":")`, as at
`runtime/card_delivery.py:232` and `runtime/alert_digest.py:1029` — so a hash
taken over it is stable and a reader who re-indents the document does not thereby
give the same facts a different identity.

`ensure_ascii=False` is matched: bytes above ASCII pass through as UTF-8, so a
mount path is quoted verbatim and never transliterated.

## Swap and PSI (added within `v1`)

Both blocks are **optional** in the schema and additive: a consumer that does not
know them ignores them, and a document produced by a build from before this
change is still valid — verified by validating the committed
`adapter/fixtures/snapshot.v1.example.json` (which has neither) against the
current schema.

A build that DOES have them always emits both, with `UNAVAILABLE` and a reason
when the host gave nothing. Omitting a block is therefore not a state this
collector produces, so "not measured" can never be read as "not asked for".

**The one distinction worth reading twice.** `swap.used_percent` and
`swap.pages_out_per_second` answer different questions, and only the second one
is about paging:

| | what it is | needs two samples | answers |
|---|---|---|---|
| `used_percent` (and the byte totals) | a **stock** | no | how much swap is in use |
| `pages_*` / `pages_*_per_second` | a **flow** | yes | is this machine paging right now |

Swap in use can fall while paging continues, and can sit at 85% on a machine
that has not paged in an hour. The document carries a `note` saying so, because a
number that is correct but read as the wrong signal is the failure this collector
exists to prevent.

Everything in the swap block is refused by the same rules as the rest of the
document: a counter that went backwards, an interval that was not measurable, a
reading that could not be taken. Nothing here is clamped, and nothing is
defaulted to zero.

## How this interface may change

* **`.v1` is additive.** New optional properties may appear within `v1`; a
  consumer must ignore properties it does not know. `swap` and `psi` are the
  worked example: they were added without moving `required`, without changing any
  existing field, and without invalidating a document that predates them.
* **A change to the meaning of an existing field, a removed property, or a
  changed type is `.v2`.** The `schema` string is the signal, and the core already
  treats it as one: `runtime/registry.py:1058` refuses a mismatched document
  schema rather than coercing it.
* **`collector.version` moves independently** of the schema. It names the build,
  not the contract.
* Nothing in this contract may be relaxed into "unknown reported as zero". If a
  future revision cannot measure something, it says so.

## Integration (next phase, not this slice)

This document is reached by executing the binary, not by importing it — the same
way a Provider is reached. It does not enter the core's Python loader closure,
which `tests/test_core_freeze.py` asserts.

The intended path is a `serverosd` adapter that spawns the collector, validates
stdout against the JSON Schema above, and hands the measurements to the existing
node surfaces. The adapter is the authority for freshness, scope and admission;
this document supplies only measurements.

Until that adapter exists and has been verified against a live host, **no part of
this contract is a deployed capability.** It is a buildable, tested slice.
