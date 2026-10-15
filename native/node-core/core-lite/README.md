# Core-Lite — the V1 per-user observation profile

**V1 is observation only.** There is no acting mode: a running CC or GPU is not a
service to be bounced, and nothing here restarts, recovers or self-heals
anything. That is a product rule encoded in the reader, not a promise in prose —
see §2.

## 1. What a Core-Lite deployment is

One **ordinary Linux user's** Core, with its own profile, its own state and its
own runtime directory. Two users on one device get two deployments with two
independent UIDs and neither can see or act on the other's.

| observed | how |
|---|---|
| CPU, memory | the EXISTING `nodecore` collector (`native/node-core/src/linux_readers.cpp`) — **no second monitoring library** |
| swap | the collector's `swap` block: bytes in use from `/proc/meminfo`, and the paging rate from two `/proc/vmstat` samples. The **stock** and the **flow** are separate fields, because only the flow says whether the machine is paging |
| PSI (`psi.cpu` / `psi.memory` / `psi.io`) | the collector's `psi` block, from `/proc/pressure`. The kernel windows the averages itself, so ONE sample is a complete reading. A line the kernel does not provide (`full` on some kernels) is `UNAVAILABLE` in place, never zero |
| service state | the supervisor's `systemd_user.hpp` observation, unchanged |
| connector health | declared as an observation; read by the Node adapter, never by the remote side |
| GPU / VRAM / Strata storage (Dell) | **read-only**, and only where the profile says `authorized: true` |

Every reading carries `observed_at` and either a value or an UNKNOWN with a
reason. The collector's own invariant — *a fact that could not be measured is
`UNAVAILABLE` with a reason, never `0` and never `null`* — is inherited, not
restated.

## 2. The profile, and what the reader refuses

`profiles/tokyo-node.profile.json` and `profiles/dell-fjd.profile.json` are the
V1 declarations. The reader (`src/profile.cpp`) refuses, BY NAME:

| refused | why |
|---|---|
| `uid`, `user_id`, `uid_min`, `uid_max` | **the UID is resolved from the running user**, never written down. One profile installs on any account; a file carrying an id would put Tokyo's `vince` profile on Dell's `fjd` account with one `scp`, and still look correct |
| `observe_only: false` | V1 has no acting mode. The value is refused rather than absent-and-assumed |
| `restart`, `recover`, `recovery`, `actions`, `restart_allowed`, `enable_actions`, `self_heal` | they name an EFFECT. A profile copied from a V2 draft fails loudly instead of quietly carrying a capability this stage does not have |
| an observation outside the closed vocabulary | a capability nobody can name is a capability nobody reviewed |
| an observation with no `authorized` field | **absent must not mean granted** |
| a service whose `owner` ≠ `principal` | refused, not filtered: a reader would otherwise believe the account had been considered |
| a `unit` that `valid_unit_id` refuses | the SAME function the supervisor uses, so reader and executor cannot drift |
| a duplicate service id | two entries under one id is two entries one of which is unreachable |

This is the **same house style** as `supervisor/manifest.hpp`: strict, closed
vocabularies, named refusals, never a half-built document.

## 3. Evidence, layer by layer — and what is NOT there

| layer | status | evidence |
|---|---|---|
| **Source** | **REAL** | the reader, the status builder, the runtime identity readback and the Linux adapter — compiled by `cmake --build` over `native/node-core` with GNU 13.3, `-std=c++20 -Wall -Wextra -Wpedantic`, **zero warnings** |
| **Test** | **REAL, EXECUTED** | `ctest` over the whole project: **19/19 passed**, of which 4 are Core-Lite's — `core_lite_profile` **46 checks**, `core_lite_status` **99 checks**, `core_lite_readings` **226 checks**, plus the collector's new `test_swap_psi` **137 checks**. The three pre-existing collector suites are unchanged at 72 / 54 / 68. All 0 failures. Includes both SHIPPED profiles parsing (`vince` 8 observations/1 service, `fjd` 8/0). Run under `systemd-run --scope -p MemoryMax=1G -p MemorySwapMax=0 -p CPUQuota=25% -p TasksMax=64` |
| **Live observation** | **REAL, on Tokyo, not deployed** | the collector sampled this host twice into `node-resource-snapshot.v1`; the projection read it and the status reported `cpu`, `memory`, `swap` (32 GiB total, 27.1 GiB used, `pages_out_per_second 0.0`) and `psi.cpu/memory/io` — all `PRESENT`/`COMPLETE` — from those real counters. `services` and `connector` remain `NOT_IMPLEMENTED`. Nothing was installed, enabled or started. Evidence under `ARTIFACTS_ROOT/work-items/serveros/CORE-LITE-SWAP-PSI/20261015/` |
| **RealUserStaged** | **NOT DONE — BLOCKED** | no unit installed, none enabled, no linger. The Tokyo account would need the owner to install it; **Dell's `fjd` is not this account and only `fjd` may install there** |
| **Running** | **NOT DONE — BLOCKED** | nothing has ever executed on a real host. No `systemd --user` unit for Core-Lite exists yet |

**A green Test layer is not a Running layer.** The tests exercise a *reader*;
they say nothing about a deployment, and nothing here may be reported as
«running on Tokyo» or «running on Dell».

## 4. Install and rollback (IF and WHEN an owner authorises it)

**Nothing in this repository installs, enables, starts or lingers anything.**
The steps below are what an owner would run on their OWN account.

```bash
# 0) build from the node-core ROOT. `-S native/node-core/core-lite` does NOT
#    configure: that file is a subdirectory, and it links `nodecore_supervisor`,
#    which a standalone scope never defines. See §8.
cmake -S native/node-core -B build/node-core && cmake --build build/node-core
# 1) install to a stable path, NOT a worktree
install -D -m 0755 build/node-core/core-lite/core-lite-profile-check \
        ~/.local/lib/serveros-core-lite/core-lite-profile-check
# 2) validate the profile BEFORE anything is installed
~/.local/lib/serveros-core-lite/core-lite-profile-check \
        ~/.config/serveros/core-lite/profile.json
# 3) only then, a user unit (not shipped enabled, not shipped started)
systemctl --user daemon-reload
# systemctl --user enable --now serveros-core-lite.service   # ← the owner's decision
```

Rollback:

```bash
systemctl --user disable --now serveros-core-lite.service
rm -f ~/.config/systemd/user/serveros-core-lite.service
rm -rf ~/.local/lib/serveros-core-lite
systemctl --user daemon-reload
```

Rollback touches no production timer, no `monitoring-policy.yaml`, and no
message already sent.

## 5. Resource safety

- **One process, periodic, bounded**: observe-only, no daemon that acts, no
  unbounded work per tick, and backoff belongs to the supervisor's existing
  recovery budget (which V1 does not even use, since it takes no action).
- **cgroup limits** for anything run on this host:
  `systemd-run --user --scope -p MemoryMax=1G -p MemorySwapMax=0 -p CPUQuota=25%`.
- **No sudo, no privilege**: `NoNewPrivileges=yes`, user scope only.
- **Socket 0600, same-UID `SO_PEERCRED`**: inherited from the supervisor's
  `status_socket.hpp`; a C++ Core does not open a second status surface.
- **One writer**: **Python `serverosd` remains the ONLY production writer.**
  A C++ Core-Lite observes. Until that split is formally changed, no C++ path
  writes any state the Python side also writes.

## 6. What is BLOCKED, stated plainly

- **CMake**: `cmake` is not on `PATH` on this host. It IS available at
  `TOOLCHAINS_ROOT/cmake-native`, whose launcher needs `PYTHONPATH` pointed at
  its own root; configure and build were run that way and the whole project built
  clean. The plain `cmake -S native/node-core -B build` in §4 is the command an
  ordinary host runs, and it is the one that was exercised.
- **`supervisor/src` on the include path**: `core-lite` includes the supervisor's
  private `json_detail.hpp` — the one strict JSON reader in this repository, and
  the one that reads an untrusted snapshot as input — via a `PRIVATE` include of
  `../supervisor/src`. The structural fix belongs to the supervisor's review
  (publish it as `supervisor/include/supervisor/json_reader.hpp`), not to this
  one. Recorded rather than hidden, because before this branch the documented
  CMake path did not build at all and that is exactly what the missing include
  was hiding.
- **No unit, anywhere**: not installed, not enabled, not started, no linger.
- **Dell `fjd`**: this account has no permission there. The profile is a
  **declaration awaiting `fjd`'s own authorisation**.
- **PSI on Windows**: impossible; reported `UNAVAILABLE` / `UNSUPPORTED_PLATFORM`,
  never zero — and deliberately a different reason word from the `NOT_IMPLEMENTED`
  that a Linux host reports for the same family today (`contract/core-lite-status.v1.md` §3).
- **Cross-UID reads**: only the owning UID's Node adapter may return a
  de-identified summary. Tokyo does **not** read Dell's private UNIX socket.
- **Fleet integration**: **out of scope for this branch**. `fleet_mcp/fleet.py`
  and the plugin installer are deliberately untouched so this does not collide
  with the other agents' runtime work; the optional Core-health summary in
  `fleet_status` / `fleet_list_devices` is the integration point, and it must
  carry its source and freshness — a later, separately-reviewed change.

## 7. Windows — a DIFFERENT adapter, not a port

`supervisor/src/systemd_user.cpp` is **Linux only**. There is no Windows
equivalent in this tree, and nothing here may be reported as «installed on
Windows». See `windows/README.md` for the contract a Windows adapter must meet
and what is UNKNOWN about it.

## 8. The status contract, and the build topology that was broken

`contract/core-lite-status.v1.md` is the cross-platform read-only status every
Core-Lite reports — one document Tokyo, FJ Windows and Dell can all be described
by. `src/status.cpp` builds it, `src/status_main.cpp` is the `core-lite-status`
front door, and `src/collector_readings.cpp` is the Linux adapter that answers it
from the collector's own snapshot.

`CMakeLists.txt` in this directory **used to be a standalone project**: it called
`project()`, linked `nodecore_supervisor` and could not configure on its own,
while nothing reached it from the root either — so the documented build command
in §4 was one nobody could have run. It is now a subdirectory: the root
`native/node-core/CMakeLists.txt` adds `supervisor` and then `core-lite`, in that
order, because the dependency is real and order is part of the fix.

**Core-Lite does not link `nodecore_supervisor` twice, and does not re-implement
`valid_unit_id`.** One function decides what a unit id is, one reader decides
what JSON is, and both are the supervisor's.

## 9. The trust boundary: what a caller and an input may NOT do

A status document is read by people who are not on this host. Two P0 findings on
this branch were both about the same mistake in different clothes — letting
something that is not evidence produce a claim.

**A caller cannot name the device.** `core-lite-status` has **no `--device-ref`**
(and no `--observed-at`). It had one; a caller who typed the profile's own node
string got `device_identity MATCHED` and `live: true`, having supplied both sides
of the comparison. The device reference now comes only from this host's
declaration file, read out of the home directory **the user database records**
(`getpwuid_r`'s `pw_dir`, never `$HOME`), with every path component `lstat`ed:
no symlink at any level, each component owned by this uid, none writable by group
or other, the file a regular file of bounded size. An identity that was never
established has no home this program may read.

**And a local declaration is still not an attestation.** It is stamped
`LOCAL_DECLARATION` — this account's claim about itself — and `live` requires
`basis: ATTESTED`, which nothing in V1 can produce. So **`live` is false for
every document this program emits today**, and the document says why in
`device_identity.basis` rather than leaving a reader to guess. A Fleet consumer
that needs to know which device this really is must get that through its own
authenticated route. `principal_identity` is different and IS `ATTESTED`: this
process read its own account straight from the OS.

**An input cannot smuggle a key.** The Linux adapter no longer forwards the
collector's blocks. It PROJECTS them — names every field it emits, and can only
emit what it names:

| observation | projected | never forwarded |
|---|---|---|
| `cpu` | `used_percent` (0..100) | `counters`, `busy_jiffies`, `total_jiffies`, `sample_seconds`, `state` |
| `memory` | `total_bytes`, `available_bytes`, `free_bytes`, `used_percent` (0..100) | `scope`, `source` |
| `storage.readonly` | per role: `role`, `total_bytes`, `available_bytes`, `used_bytes`; and per role: `read_bytes`, `write_bytes` | `path`, `mount_point`, `source`, `fstype`, `inodes`, the device name (`vda2`) |

A filter has to recognise what to remove and is wrong the first time something
new appears; a projection can only emit what it was written to emit. The role
name — the one free-text field — is allowed through only when it matches
`[A-Za-z0-9_-]{1,32}`. A block that is PRESENT but incomplete, out of range
(`used_percent: 340`), or carrying a byte count written as a real is
`UNAVAILABLE` / `NO_SOURCE`: **no partial value is ever published**, because
three of four memory numbers is a value a reader will do arithmetic on and get
wrong. `Reading::value` is a `Json` the adapter builds key by key, not a text
fragment re-parsed before emission — the round trip that let a forged `token`
travel from a document into a status is gone from the type, not filtered out.

**Freshness is not asserted.** `observed_at` is the SOURCE snapshot's own sample
time, copied; it is never this process's clock and never a caller's string. The
document carries `"freshness": "NOT_ASSERTED"`, because deciding a reading is
stale needs a clock and a policy that belong to a trusted Node or Fleet reader,
not to the document's opinion of itself.

The forgery tests are the ones that hold this: `tests/test_collector_readings.cpp`
poisons the real fixture with `token`, `argv` and a `config_path`, runs the whole
chain, and asserts none of them reach the document — and separately asserts the
collector's OWN `path` / `mount_point` fields do not either.
