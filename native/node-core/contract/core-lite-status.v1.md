# `serveros/core-lite-status.v1` — one read-only status, three machines

**Nothing in this document has been deployed.** No unit is installed, enabled or
lingered anywhere; no Fleet reader consumes this yet. What it describes is a
*builder* (`core-lite/src/status.cpp`) and a *front door*
(`core-lite-status`), both of which only read.

## 1. Why one document

Tokyo (Linux), the FJ Windows box and the Dell box must be described by the SAME
shape, so a Fleet reader needs one parser and cannot compare three vocabularies.
The alternative — three documents that grew three ideas of what "unknown" means —
is the failure this contract exists to prevent, and it fails quietly: the reader
that receives `0` for an unmeasurable value has no way to tell it from a measured
zero.

## 2. The document

```json
{
  "schema": "serveros/core-lite-status.v1",
  "node": "device:tokyo-shared-dev-01",
  "principal": "vince",
  "platform": "linux",
  "generation": 1,
  "mode": "observe-only",
  "observed_at": "2026-10-15T15:15:00+00:00",
  "principal_identity": { "state": "MATCHED", "claimed": "vince", "observed": "vince" },
  "device_identity":    { "state": "UNKNOWN", "claimed": "device:tokyo-shared-dev-01",
                          "reason": "the managed Node identity was not established; a profile must not claim a device's health from its own text" },
  "live": false,
  "observations": [
    { "name": "cpu", "state": "PRESENT", "source": "nodecore/NodeResourceSnapshot v1",
      "observed_at": "2026-10-15T15:14:58.466061+00:00", "value": { "used_percent": 9.1 } },
    { "name": "swap", "state": "UNAVAILABLE", "reason": "NOT_IMPLEMENTED",
      "source": "nodecore/NodeResourceSnapshot v1" }
  ]
}
```

Members are emitted in sorted order by the writer. A reader must not depend on
order, and must not depend on a member being present: **absent is not a value**.

## 3. The rules, each one a refusal

| rule | why it is not merely a convention |
|---|---|
| `mode` is ALWAYS `observe-only` | V1 has no acting mode. A client that had to *infer* whether the Core is willing to act would infer wrong, so it is stated |
| an observation the profile did not grant **does not appear** | not `UNAVAILABLE`, not `null`, not an empty string. A present-but-blank field reads as «measured nothing», which is a different claim, and its mere presence discloses that the capability exists |
| a fact nobody measured is `UNAVAILABLE` + a `reason` | **never `0`, never `null`** — the collector's own invariant (`node-resource-snapshot.v1`), inherited rather than restated |
| `reason` comes from a CLOSED set | a typo must not become a new vocabulary word a Fleet reader has never seen. An unrecognised reason is reported as `NO_SOURCE` |
| `live` requires BOTH identity verdicts to be `MATCHED` | JSON cannot make a device healthy by asserting it |
| no `argv`, no environment, no token, no credential, no config path | the builder never opens a file; values are handed in opaquely |

The closed `reason` set:

| reason | means | sends a reader to |
|---|---|---|
| `UNSUPPORTED_PLATFORM` | this platform cannot provide it, **ever** | the design — a different OS |
| `NOT_IMPLEMENTED` | the platform could, but no collector exists **yet** | the backlog — a missing program |
| `NO_SOURCE` | the adapter was asked and had nothing | the host — a failed read |
| `STALE` | a reading exists but is past its freshness bound | the clock — a producer that stopped |
| `IDENTITY_UNKNOWN` | could not establish which account this is | the environment |

`UNSUPPORTED_PLATFORM` and `NOT_IMPLEMENTED` are deliberately two words. On
Windows, `psi.*` is the first — `/proc/pressure` does not exist there and never
will. On Linux, `psi.*` is the second — `/proc/pressure` exists and the collector
simply has no block for it yet. Reporting the second as the first would blame the
platform for a missing program, and the operator would go looking in the wrong
place.

## 4. Identity: what `live` does and does not mean

Two verdicts, each `MATCHED` / `MISMATCH` / `UNKNOWN`:

- **`principal_identity`** — the profile's `principal` against the OS readback of
  the account this process actually runs as (`getuid()` + `getpwuid_r` on Linux;
  the Windows adapter elsewhere — there is no `getuid()` there and this file
  never pretends otherwise).
- **`device_identity`** — the profile's `node` against the managed Node identity
  read from this node's own declaration. An absent declaration is `UNKNOWN`, not
  a default device id.

**`live` means the identity is established. It does NOT mean healthy.** A Core
whose identity matched and which then measured nothing is `live: true` with every
observation `UNAVAILABLE`. Those are two different questions and the document
keeps them apart on purpose.

Deliberate consequence, pinned by a test
(`test_an_unknown_device_still_reports_this_accounts_readings`): when the
principal matches but the device declaration is missing, the **readings are still
emitted** and `live` is `false`. Readings are this account's measurements of the
machine it is on — proven by the principal gate — while `live` is the claim that
this really is the declared device. Suppressing telemetry because one line of
config is missing would take a monitor dark for a reason that has nothing to do
with what it monitors.

What is NOT negotiable: a principal that is not `MATCHED` emits **no
observations at all**. The readings would be some *other* account's, and filing
them under the claimed principal is exactly the confusion the gate exists to
prevent. Running Tokyo's `core-lite-status` with Dell's profile yields
`principal_identity.state = MISMATCH` and `observations: []`.

## 5. Who measures what (`core-lite/src/collector_readings.cpp`)

The Linux path does **not** read `/proc` again. It reads the
`serveros/node-resource-snapshot.v1` the native collector already produced, so
the numbers a Fleet reader sees here are the collector's own — including its
counter-delta arithmetic, its zero-interval refusal and its reboot-across-samples
refusal. A second reader of `/proc` would be a second opinion about what a
percentage is, and the two would drift the first time one of them learned about a
new counter.

Measured today, against the collector this repository ships:

| observation | on Linux | why |
|---|---|---|
| `cpu` | **PRESENT**, from `/proc/stat` | the collector's `cpu` block |
| `memory` | **PRESENT**, from `/proc/meminfo` | the collector's `memory` block |
| `storage.readonly` | **PRESENT** | assembled from the `filesystems` and `disk_io` blocks together |
| `swap` | `NOT_IMPLEMENTED` | no block exists yet |
| `psi.cpu` / `psi.memory` / `psi.io` | `NOT_IMPLEMENTED` | no block exists yet — **not** `UNSUPPORTED_PLATFORM`, see §3 |
| `services` | `NOT_IMPLEMENTED` | the supervisor's `systemd_user.hpp` observation is not wired in yet |
| `connector` | `NOT_IMPLEMENTED` | read by a Node adapter that does not exist yet |
| `gpu.readonly` | `NOT_IMPLEMENTED` | read-only by declaration; not implemented |

A granted observation is **never silently omitted**: the adapter answers every
one of them, so «nobody asked» and «asked and got nothing» cannot look the same.

The adapter refuses a profile whose `platform` is not `linux`. This is the Linux
adapter; a Windows Core's status comes from the Windows adapter
(`core-lite/windows/README.md`). Running this one there would make a Linux
program claim a Windows host's health, which is the substitution the `platform`
field exists to prevent.

## 6. What is NOT in this contract

- **No network surface.** No socket, no HTTP, no port. The CLI prints to stdout.
  The supervisor's existing `GET /status` socket contract is **unchanged** by
  this document; a C++ Core does not open a second status surface.
- **No acting capability**, no field that could name one.
- **No aggregation.** A Fleet summary is a consumer of this document and is out
  of scope for this branch.
- **No freshness policy enforced here.** `observed_at` is carried; deciding that
  a reading is `STALE` is the reader's bound, not the builder's, and the builder
  will carry a `STALE` reason it was handed.

## 7. How to run it

```bash
cmake -S native/node-core -B build/node-core && cmake --build build/node-core
build/node-core/core-lite/core-lite-status \
    --profile native/node-core/core-lite/profiles/tokyo-node.profile.json \
    --snapshot /tmp/live-snapshot.json \
    --observed-at "$(date -u +%Y-%m-%dT%H:%M:%S+00:00)"
```

Exit `0` means a document was printed — **including `live: false`**, because a
refusal to claim an identity is a result and not a program failure. Exit `1`
means no document could be produced at all; nothing is printed, because half a
status is worse than none.
