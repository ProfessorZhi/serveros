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
| CPU, memory, swap | the EXISTING `nodecore` collector (`native/node-core/src/linux_readers.cpp`) — **no second monitoring library** |
| PSI (`psi.cpu` / `psi.memory` / `psi.io`) | declared in the profile; **UNAVAILABLE where `/proc/pressure` does not exist**, never zero |
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
| **Source** | **REAL** | `src/profile.cpp` + `include/corelite/profile.hpp` compiled by `g++ 13.3 -std=c++20 -Wall -Wextra -Wpedantic`, **zero warnings** |
| **Test** | **REAL, EXECUTED** | `tests/test_profile.cpp` → **46 checks, 0 failures**, including both SHIPPED profiles parsing (`vince` 8 observations/1 service, `fjd` 8/0). Run under `systemd-run --scope -p MemoryMax=1G -p MemorySwapMax=0 -p CPUQuota=25%` |
| **RealUserStaged** | **NOT DONE — BLOCKED** | no unit installed, none enabled, no linger. The Tokyo account would need the owner to install it; **Dell's `fjd` is not this account and only `fjd` may install there** |
| **Running** | **NOT DONE — BLOCKED** | nothing has ever executed on a real host. No `systemd --user` unit for Core-Lite exists yet |

**A green Test layer is not a Running layer.** The tests exercise a *reader*;
they say nothing about a deployment, and nothing here may be reported as
«running on Tokyo» or «running on Dell».

## 4. Install and rollback (IF and WHEN an owner authorises it)

**Nothing in this repository installs, enables, starts or lingers anything.**
The steps below are what an owner would run on their OWN account.

```bash
# 0) build (needs cmake; NOT present on the Tokyo host today — see §6)
cmake -S native/node-core/core-lite -B build/core-lite && cmake --build build/core-lite
# 1) install to a stable path, NOT a worktree
install -D -m 0755 build/core-lite/core-lite-profile-check \
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

- **No compile via CMake on this host**: `cmake` is not installed. The tests
  above were built with a direct `g++` invocation over the same sources, which
  is why the Test layer is real — but `core-lite/CMakeLists.txt` has never been
  configured here.
- **No unit, anywhere**: not installed, not enabled, not started, no linger.
- **Dell `fjd`**: this account has no permission there. The profile is a
  **declaration awaiting `fjd`'s own authorisation**.
- **PSI on Windows**: impossible; reported UNAVAILABLE at runtime, never zero.
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
