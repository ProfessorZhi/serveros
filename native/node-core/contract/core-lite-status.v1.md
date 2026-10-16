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
  "freshness": "NOT_ASSERTED",
  "principal_identity": { "state": "MATCHED", "basis": "ATTESTED",
                          "claimed": "vince", "observed": "vince" },
  "device_identity":    { "state": "UNKNOWN", "basis": "NONE",
                          "claimed": "device:tokyo-shared-dev-01",
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
| `live` requires the principal to `MATCH` **and** the device to `MATCH` on an `ATTESTED` basis | a string a caller typed, or a file the account wrote about itself, is a claim and not evidence. JSON cannot make a device healthy by asserting it |
| an identity carries the `basis` it was obtained on | `MATCHED` from an argument and `MATCHED` from an authenticated route are the same word and are not the same fact |
| `freshness` is `NOT_ASSERTED` | the builder has no clock it trusts. `observed_at` is the SOURCE's stamp, copied — never a clock read here and never a caller's string |
| no `argv`, no environment, no token, no credential, no config path, no raw device name | the builder never opens a file, and a reading's value is a `Json` the ADAPTER built key by key — not a text fragment re-parsed before emission. There is no round trip through a string for an untrusted document to ride in on |

The closed `reason` set:

| reason | means | sends a reader to |
|---|---|---|
| `UNSUPPORTED_PLATFORM` | this platform cannot provide it, **ever** | the design — a different OS |
| `NOT_IMPLEMENTED` | the platform could, but no collector exists **yet** | the backlog — a missing program |
| `NO_SOURCE` | the adapter was asked and had nothing usable | the host — a failed read, or a block that did not survive the projection |
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

Every verdict also carries a **`basis`**, which is what decides whether the
verdict means anything:

| basis | is | may support `live` |
|---|---|---|
| `ATTESTED` | an answer that came back over an authenticated route this process did not author | **yes**, and it is the only one |
| `LOCAL_DECLARATION` | a file on this host, written by this account about itself | no |
| `CALLER_SUPPLIED` | an argument | no |
| `NONE` | nothing was established | no |

`principal_identity` is `ATTESTED`: this process read its own account from the
OS. **Nothing in V1 can attest a device reference**, so `live` is `false` for
every document this program emits today — deliberately, and stated in
`device_identity.basis` rather than left for a reader to infer. A consumer that
needs to know which device this really is must establish that through its own
authenticated route; this document does not claim it, and a Fleet reader that
trusts this output for device identity is trusting the wrong thing.

A device verdict of `MATCHED` with a basis of `LOCAL_DECLARATION` is still worth
reporting — it says the account's own declaration agrees with the profile — but
it is the account agreeing with itself.

**A value can be partly measured, and says so.** `completeness` is `COMPLETE`
when every projected field of a PRESENT reading is a number, and `PARTIAL` when
at least one is an explicit UNAVAILABLE in place:

```json
{ "name": "psi.io", "state": "PRESENT", "completeness": "PARTIAL",
  "value": { "some": { "avg10": 0.61, "avg60": 0.22, "avg300": 0.67 },
             "full": { "state": "UNAVAILABLE", "reason": "NO_SOURCE" } } }
```

Two real cases produce this: a swap reading whose stock is readable while the
sample interval is not, and a `psi.<resource>` on a kernel that does not report
`full`. Both used to be a choice between two bad answers — call the whole reading
UNAVAILABLE and throw away real measurements, or call it PRESENT and let a
consumer read the absent field as zero. `completeness` says which it is, so
neither has to be guessed and nothing is thrown away.

The per-field `reason` is the adapter's own closed word (`NO_SOURCE`), **never
the producer's prose**: the collector's reasons name `/proc` paths, and a status
is read by people who are not on this host.

**`live` means the identity is established. It does NOT mean healthy.** A Core
whose identity was attested and which then measured nothing is `live: true` with
every observation `UNAVAILABLE`. Those are two different questions and the
document keeps them apart on purpose.

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

**The blocks are PROJECTED, not forwarded.** A snapshot is input — it arrives as
a file and a file can be edited — so every field a status carries is named by the
adapter, and nothing unnamed can appear. A filter has to recognise what to
remove and is wrong the first time something new appears; a projection can only
emit what it was written to emit.

| observation | projected (and nothing else) | never forwarded |
|---|---|---|
| `cpu` | `used_percent`, 0..100 | `counters`, `busy_jiffies`, `total_jiffies`, `sample_seconds`, `state` |
| `memory` | `total_bytes`, `available_bytes`, `free_bytes`, `used_percent` (0..100) | `scope`, `source` |
| `storage.readonly` | per filesystem role: `role`, `total_bytes`, `available_bytes`, `used_bytes`; per device role: `role`, `read_bytes`, `write_bytes` | `path`, `mount_point`, `source`, `fstype`, `inodes`, and the device NAME (`vda2`) — a raw hardware identifier the role already replaces |

The role name is the one free-text field, so it is allowed through only when it
matches `[A-Za-z0-9_-]{1,32}`, and at most 64 rows per block. A block that is
PRESENT but incomplete, out of range, or malformed — a percentage of 340, a byte
count written as `1.5`, one unusable row — makes the WHOLE reading
`UNAVAILABLE` / `NO_SOURCE`. No partial value is ever published: three of four
memory numbers is a value a reader will do arithmetic on and get wrong.

| observation | on Linux | why |
|---|---|---|
| `cpu` | **PRESENT**, from `/proc/stat` | the collector's `cpu` block |
| `memory` | **PRESENT**, from `/proc/meminfo` | the collector's `memory` block |
| `storage.readonly` | **PRESENT** | assembled from the `filesystems` and `disk_io` blocks together |
| `swap` | **PRESENT** — bytes in use, and the paging rate over the window | the collector's `swap` block, from `/proc/meminfo` and `/proc/vmstat` |
| `psi.cpu` / `psi.memory` / `psi.io` | **PRESENT** — the six averages | the collector's `psi` block, from `/proc/pressure`; the kernel already windows them, so one sample is a complete reading |
| `services` | **PRESENT** — this account's declared units, read-only | the supervisor's existing `systemd_user` adapter. See §6 |
| `connector` | `NOT_IMPLEMENTED` | read by a Node adapter that does not exist yet |
| `gpu.readonly` | **PRESENT** on a Linux host with an NVIDIA card | the fixed `nvidia-smi` query, read-only. See §6c |

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

## 6b. `services`: reading units, never touching them

`services` is the one observation that is not a `/proc` fact and not in the
snapshot. It comes from the supervisor's existing read-only `systemd_user`
adapter, through a **one-method interface**: the projection is compiled against
`ServiceQuery`, which has `query` and nothing else. The supervisor's `Provider`
also has `start`/`restart`/`stop`, and the code that decides what a service
reading SAYS cannot reach them, because a type that cannot express a verb cannot
call one.

**The owner gate runs on the RUNTIME account.** A service whose `owner` is not
the account this process actually runs as is **not emitted at all** — not as
`UNAVAILABLE`, because a present-and-blank field reads as «measured nothing» and
its presence alone discloses that the account has a service there. The profile
reader refuses `owner != principal` as well; this is the second gate, and it is
the one that runs where the string would become a query. A unit that is not in
this profile's own allowlist, or an answer about a *different* unit, is refused
rather than relabelled.

### `INACTIVE` and `NOT_INSTALLED` are different facts

Measured on this host: `systemctl --user show` answers
`ActiveState=inactive LoadState=not-found` for a unit name **that does not
exist**, and the same `ActiveState` for one that exists and was stopped. A
projection that printed `INACTIVE` for both would state that a service exists and
is stopped when there is no service at all.

So the observation carries the **load state** as well:

| `state` | means |
|---|---|
| `ACTIVE` / `INACTIVE` / `FAILED` / `ACTIVATING` / `DEACTIVATING` | the unit is installed; the supervisor's own `Active` vocabulary in this document's spelling |
| `NOT_INSTALLED` | the load state says there is no unit behind this name (`not-found`, or `masked` — a mask is the deliberate absence of a unit) |
| `UNAVAILABLE` + `reason` | the state was not read, was not measured, or the load state was **not read at all** |

The last row is why the load state is tri-state: `true` (installed), `false`
(not found), and **refused** when nothing read it. "We did not look" and "it is
not there" are different facts, and only one of them is about the host — so an
answer that omits the load state is `UNAVAILABLE`, never assumed installed and
never assumed absent.

### `connector` stays `NOT_IMPLEMENTED`

There is no trusted typed source for it, and **an HTTP health endpoint is not
one**: a `200` from something that answers says a socket opened, not that a
device is connected or that the thing answering is the thing it claims to be.
Until a typed, authenticated source exists, the honest answer is that nobody has
written the collector — `NOT_IMPLEMENTED` — rather than a status derived from an
unauthenticated request. This is the same rule as #213 §4b: an assertion is not
evidence.

## 6c. `gpu.readonly`: one fixed program, card counters only

**The program is an absolute constant with a fixed argument list.** There is no
flag on `core-lite-status` that names it and no profile field that changes it, so
a caller cannot point this at another program and cannot add an argument to this
one. The field list is the six names in the projection table and nothing else —
there is no `--query-compute-apps`, so no process data is in the answer, which is
why no other account's PID, command line or UID can be published: they were never
asked for.

It runs through the supervisor's existing bounded runner: `execvp` with no shell,
a wall deadline that kills the whole process group, and both streams capped at
`kNvidiaSmiMaxOutputBytes` — well under the runner's own default, because six
short fields per card is all this shape holds. A wedged driver is exactly when a
monitoring program must not sit waiting.

### What refuses, and why each one is a refusal

| refused | why |
|---|---|
| an observation the profile did not grant | **the program is not run at all.** An unauthorised observation costs no process — reading it and then discarding it is the thing the grant exists to prevent |
| an unresolved account | there is no Core to attribute a reading to |
| a non-Linux profile | `/usr/bin/nvidia-smi` is a Linux path and this is a Linux program. **A Windows Core's GPU answer must come from a Windows adapter**, and nothing here may be presented as one |
| the program missing, timing out, exiting non-zero, or exceeding the output cap | `UNAVAILABLE` / `NO_SOURCE`. A host with no card, a wedged driver and a refused binary all land here, and none of them is a `0%` |
| a utilisation outside `[0, 100]`, memory used above total, a negative or non-integer memory figure, an `N/A` in any numeric field | the WHOLE sample becomes `UNAVAILABLE`. One bad card makes the reading untrustworthy; dropping just that row would leave a reader comparing two cards with no way to know one was gone |
| a temperature outside `[-40, 150]` °C | a driver that reports `-273` or `9000` is reporting an error into a field. A wrong number in front of somebody who would act on it is worse than no number |
| two rows with the same index | two answers for one card is a contradiction, and picking one is picking silently |
| more rows than `kMaxGpuRows` | a document that grew to a thousand rows is not this shape |

`N/A` deserves its own line: it is the reason this observation exists. A driver
that cannot answer must never be rendered as an idle card, and `nvidia-smi`
writes `N/A` precisely when it cannot.

### The one free-text field

`label` is the card's **model name** — and only when it matches a bounded
charset **and** contains no run of eight or more hex characters. Character class
alone cannot tell `GPU-1a2b3c4d-…` from `NVIDIA RTX A6000`; both are letters,
digits and hyphens. What separates them is that an identifier carries a long
unbroken hex run and a marketing name does not — the longest in `A6000` is five,
in `RTX 8000` is four. So no UUID, serial or hash can travel in this field, and a
name that fails is a signal the answer is not the answer this program asked for.

The direction of that guess is deliberate: a model name wrongly refused costs a
label; an identifier wrongly published is a hardware identifier leaked.

### `sample_time` is not `observed_at`

`nvidia-smi` stamps nothing, so unlike the snapshot's `observed_at` — which is
the producer's own sample instant, copied — `sample_time` is the instant **this
process asked the question**, read from its own clock. The document says so by
naming it differently, and `freshness` remains `NOT_ASSERTED` either way: the
staleness bound belongs to the reader.

### A GPU figure is not a service health signal

**Utilisation and memory tell you nothing about whether the model server on the
card is working.** A wedged server can sit at 0% with the card idle, and a server
answering nothing can have the card at 100%. A reader that derives service health
from these numbers will report a working service as dead and a dead one as
working. Service health comes from a trusted probe that has the right to ask —
not from this field.

## 6d. The machine-checkable half

`core-lite-status.v1.schema.json` is this document's machine-checkable form,
draft 2020-12, `additionalProperties: false` at every object, every observation
name bound to its own value shape. It exists because this contract was prose, and
a consumer reading prose had to guess — the reader on the Fleet side says so of
its own first revision:

> Note what changed from the first revision of this reader: the shapes below are
> the ones the Linux adapter in `native/node-core` really writes, confirmed
> against its source, not the ones a reader might have guessed.

**That first revision no longer describes the current branches, and neither does
an earlier version of this section.** Checked field by field at
`feat/fleet-v1-core-lite-readback-20261015 @ 08a7413` and
`feat/fleet-v1-core-lite-source-20261016 @ 98cbb80`:

| observation | this producer emits | reader at the current heads | state |
|---|---|---|---|
| `psi.*` | `{"some":{"avg10","avg60","avg300"},"full":{…}}` | `{"some": "psi_line", "full": "psi_line"}`, `PSI_WINDOWS` = all three, a window that is a marker handled per window | **aligned** |
| `swap` | `{total_bytes, free_bytes, used_bytes, pages_in_per_second, pages_out_per_second}` | the same five; the reader's own comment records that `used_percent` is withheld on purpose | **aligned** |
| `storage.readonly` | `{"filesystems":{"roles":[…]},"disk_io":{"roles":[…]}}` | `filesystem_row` = `{role,total_bytes,available_bytes,used_bytes}`, `disk_row` = `{role,read_bytes,write_bytes}` | **aligned** |
| `services` | `{"services":[{id,unit,state,sub_state,observed_at,age_seconds,source}]}` | `{"services": "service_rows"}` — but only from **#216**; `#214` alone still declares `"services": {}` | **aligned on the stack, not on #214 alone** |
| `connector` | no `value` at all | permitted key set is empty | **aligned** |
| row member `completeness` | `COMPLETE`/`PARTIAL` | in `_OBSERVATION_MEMBERS`; `COMPLETENESS_WORDS` matches | **aligned** |
| `gpu.readonly` | `{"gpus":[{index,label,gpu_util_percent,memory_used_bytes,memory_total_bytes,temperature_c}],"sample_time":…}` | `"gpu.readonly": {}` — an empty permitted key set, at **both** current heads | **STILL A GAP** |

**The one remaining gap is `gpu.readonly`.** `#219` added that observation and
the reader has not been extended for it, so a Fleet summary built on either
current head carries an empty permitted key set for it and drops the whole value.
The reader's own docstring says why that is not a silent failure — an unknown key
is counted and refused rather than carried — but the reading is lost either way.
Extending it belongs to that PR's author; nothing in `fleet_mcp/**` is touched
from here, and the schema now states the exact shape to read.

**One deliberate drop, declared rather than accidental.** `sub_state` is in the
reader's `SERVICE_UNPROJECTED_KEYS`, with its reason on the page: it is
`SubState=` copied verbatim out of `systemctl show` — free text with no closed
vocabulary — so it is dropped rather than "bounded-and-hoped". This producer does
emit it. That is a judgement the reader states and owns, not a gap, and it is
recorded here so the difference is visible from both sides.

`tests/test_status_schema.cpp` keeps this schema and the producer from drifting
apart: it READS the schema, drives the real projections to produce a document,
and asserts every key the document carries is declared, every arm the schema
declares is reachable, and every object the schema describes is closed. No key
set is duplicated in the test, because duplicating them would move the drift into
the test rather than catching it.

Validating this schema against real documents produced by this code found one
error in the schema itself: `connector` was pinned to `NOT_IMPLEMENTED`, but a
run with no snapshot legitimately reports `NO_SOURCE` — the adapter was asked and
had nothing. Both are true answers to different questions, and a schema that
allowed only one would have been wrong.

## 6e. What is still missing on this link of the chain

The chain is **trusted Node provider → Core-Lite status readback → Fleet status
presentation**. The middle and right links exist as unmerged drafts (#214, #216).
The left link does not exist at all, and #216's own note says so:

> And there is nothing to connect it to. No live Node exposes a trusted Core API,
> so a source today could only be one that makes a device look Core-connected
> without an authenticated route — which is the thing this module refuses.

This document is obtainable today **only** from `core-lite-status`'s stdout. The
Core does run a trusted read-only route (`supervisor/src/status_socket.hpp`:
`$XDG_RUNTIME_DIR`, mode 0600, `SO_PEERCRED`, refuse-not-unlink, bounded request),
but it answers the **supervisor's** status, not this document. Carrying this
document over that route is a change to the socket's served payload — a contract
that belongs to the resident-Core review and not to a schema slice, so it is
named here rather than improvised.

## 7. How to run it

```bash
cmake -S native/node-core -B build/node-core && cmake --build build/node-core
build/node-core/core-lite/core-lite-status \
    --profile native/node-core/core-lite/profiles/tokyo-node.profile.json \
    --snapshot /tmp/live-snapshot.json
```

There are exactly two arguments. **There is no `--device-ref` and no
`--observed-at`**, and their absence is the point: a caller who could name the
device could make the status report `MATCHED` by typing the profile's own string,
and a caller who could stamp a time could make a week-old document look current.
Both flags existed on the first revision of this branch and both were removed.

The device reference comes from this host's declaration at
`<pw_dir>/.config/serveros/node-identity` — the home **the user database
records**, never `$HOME`, because an environment variable is something the
caller of a program controls. Every component of that path is `lstat`ed: no
symlink at any level, each owned by this uid, none writable by group or other,
the file a regular file no larger than a one-line reference. A failure at any of
those is `UNKNOWN` with the reason, never a default device id.

What that does NOT defeat, stated plainly: an attacker who is already this
account can edit the file between the check and the read, and can do anything
else in the account too. What the checks stop is a REDIRECTION this program
would otherwise follow — an environment variable naming the home, a symlink in
the path, another user's file, a world-writable declaration. Those are the cases
where a program ends up reporting a fact about somewhere else.

Exit `0` means a document was printed — **including `live: false`**, because a
refusal to claim an identity is a result and not a program failure. Exit `1`
means no document could be produced at all; nothing is printed, because half a
status is worse than none.
