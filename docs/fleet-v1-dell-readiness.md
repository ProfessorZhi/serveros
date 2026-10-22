# Fleet V1 · Lane D — Dell A6000 多账号就绪度

> **这篇回答什么：** Dell 上两个 Linux 账号（`vince` uid 1002、`fjd` uid 1000）在 Fleet V1 里各自就绪到什么程度：哪些测过、哪些是 `UNKNOWN`、哪些被阻塞。
>
> **状态：** 只读测量记录，2026-10-15。分支 `feat/fleet-v1-dell-runtime-e2e-20261015`，源自 #198 `8186028`；作者是 Dell 上受管的 CC session（OS 账号 `vince`，uid 1002）。数字与判断都是当天值。
>
> **测量边界：** 对设备只读，测试离线。没有写 binding、没有启动服务、没有开隧道、没有读另一个账号的 home 或凭据、没有占用 GPU。每条结论要么是带命令的测量，要么标 `UNKNOWN`。
>
> **一句话结论：** Dell 的 `vince` runtime 实测 PASS；`fjd` 未被观测到，也未接入 Fleet；两账号路由只在离线用 fake transport 验证过（66 tests），真机未跑。不要把本文件读成「Dell 两账号已就绪」。

---

## 1. Scope

The device carries two Linux accounts. V1 names four initial targets; on this
host two of them are `vince` and `fjd`. This lane registers what is **actually
true** about that pair today, and refuses to register what it cannot measure.

---

## 2. Registered matrix

`—` means not applicable. `UNKNOWN` means this host has no legitimate way to
find out; it is a statement about evidence, not a placeholder for "probably".

| Device | OS uid | Account | Runtime tag | Family | Credential owner | Project | create | send | control | Model readback | permissionMode readback |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `device:llm-lab-dell` | 1002 | `vince` | `claude` | claude | `vince` (anchor present, **not read**) | `dell-lab` | owner-approved | owner-approved | owner-approved | **UNKNOWN** | **UNKNOWN** |
| `device:llm-lab-dell` | 1000 | `fjd` | `claude-fjd` | claude | **UNKNOWN — `fjd`'s own** | — | **not granted** | **not granted** | **not granted** | **UNKNOWN** | **UNKNOWN** |
| `device:llm-lab-dell` | 1004 | `kaidong` | — | — | — | — | — | — | — | — | — |
| upstream production host | — | — | — | — | — | — | — | — | — | — | — |

Two rows explicitly **not** registered:

* **`kaidong` (uid 1004)** runs agent sessions on this host and is **outside this
  lane's authority**. It is listed only so the omission is visible. No fact
  about it is read or claimed.
* **The upstream FJ production host's `admin` route label** is a *routing* label
  on a *different machine*. It is not read by this surface, it is not mapped to
  any row here, and **`admin` does not mean `root`**. The two accounts on this
  device are ordinary unprivileged accounts.

### Why the model and permission columns are UNKNOWN

A real readback requires the runtime's own credential to call its `tools/list`.
This lane does not read a credential value — not `fjd`'s (never) and not
`vince`'s either (deliberately: a value that enters a transcript is a value that
has been handled by a process that did not need it). So both columns are
`UNKNOWN` **by policy**, not by failure. The capability surface has exactly one
honest spelling for that and it uses it (`state: "UNKNOWN"`).

---

## 3. (a) Live read-only availability — measured, not asserted

Measured 2026-10-15 07:33Z. Read-only; no service touched.

| What | Command | Result |
|---|---|---|
| Host | `hostname`, `uname -r` | `FJ-XX-SER-0000`, `7.0.0-38-generic`, Ubuntu 26.04 LTS |
| Runtime alive | `curl -s http://127.0.0.1:3001/health` | `{"status":"ok","installMode":"npm","version":"1.39.0"}` |
| Provider version on disk | `cloudcli/package.json` | `@yalehwang/cloudcli` **1.39.0** |
| Connector alive | `curl -o /dev/null -w '%{http_code}' http://127.0.0.1:3402/v1/health` | **401** |
| Connector enforces its protocol | same, body | `{"code":"PROTOCOL_MISMATCH","detail":"expected serveros/fleet-node/v1"}` |
| Connector route count | `curl http://127.0.0.1:3402/v1/capabilities` | `UNKNOWN_ROUTE`, names **exactly 9** served routes |
| Socket owners | `/proc/net/tcp{,6}` uid column | `3001`→1002, `3402`→1002, `26600`→1002 |
| Recorded capability capture | `tests/data/capture-cloudcli-1.39.0-tools-list-2026-10-14.json` | 22 mirrored verbs, verbatim fields |

The 401 is the strongest single piece here: the connector answers an
unauthenticated request with a *typed protocol refusal*, which is evidence it is
up, enforcing, and has not fallen open. A `200` on `/v1/health` would have been
weaker evidence than this.

**What this does not prove:** that any session can be listed, created or driven.
A live listener is not a working verb. The verbs remain `UNKNOWN` until an
authorized call reads them.

### Resource gate

`uptime` at measure time: load average **0.33**. Two GPU compute processes were
resident (~39 GiB and ~6 GiB) belonging to the protected Strata work. This lane
therefore did **static, offline development only — zero GPU, zero new agent
processes**, and did not probe the producing service.

---

## 4. (b) `fjd` — precise blockers

`fjd` is **not connected**, and the blocker is authorization, not code.

| # | Blocker | Who clears it | Evidence |
|---|---|---|---|
| B1 | No CloudCLI runtime under uid 1000 | `fjd`, the account's owner | full `ps` for uid 1000: desktop session, `ssh-agent`, `sshd-session`, and `python→strata` on `8080` — **no `claude`, `node`, or connector process** |
| B2 | No node binding for `claude-fjd` | `fjd` + the operator | no config on this host mentions `fjd` |
| B3 | Device credential for `fjd` **not readable from here, and must not be** | `fjd`, the account's owner | `/home/operator` is `0750`; `vince` cannot even `stat` it |
| B4 | **Owner authorization for the real matrix rows** | `fjd`, the account's owner | this is the blocking item: everything above is *unable*, this one is *forbidden* |
| B5 | Fleet registration is master-side | Tokyo master operator | `device_ref` is authored in the master's registry and never derived; a local binding cannot create it |
| B6 | Node secret per runtime | device + host | `ISOLATION_CONTRACT` lists `node_secret` as the one fact with a `host` owner, and it is the one this host has **not** arranged |

`fjd`'s `Linger=no`. Enabling it is `fjd`'s own single privileged action; no
agent performs it.

**Not a blocker, but worth stating:** `fjd` currently holds a live SSH session on
this host. That is evidence of *login*, and of nothing else. It does not mean a
runtime exists, and it must not be reported as connectivity to the fleet.

---

## 5. (c) Offline verification of the two-account routing

Three files, 66 tests, all passing offline against fakes. No device, socket,
credential or second account is involved.

| File | Tests | What it pins |
|---|---|---|
| `tests/test_fleet_v1_dell_runtime_matrix.py` | 23 | the tag rule, the four isolation facts and their owners, credential-owner `UNKNOWN`, family ≠ runtime tag, **an account name is not a privilege claim**, per-runtime grants |
| `tests/test_fleet_v1_dell_create_route_isolation.py` | 28 | **create / continue / reply never cross routes**; same native id under two accounts; the model and permission-mode ceilings refuse without substitution; an unbound runtime is never served by the reachable account |
| `tests/test_fleet_v1_dell_instance_identity_and_smoke.py` | 15 | instance identity must not enter the ref; the ref authority refuses a 4-part string; the V1 smoke floor and its authorization guard |

Run them:

```bash
python3 -I tests/test_fleet_v1_dell_runtime_matrix.py
python3 -I tests/test_fleet_v1_dell_create_route_isolation.py
python3 -I tests/test_fleet_v1_dell_instance_identity_and_smoke.py
```

Measured: `Ran 23 … OK`, `Ran 28 … OK`, `Ran 15 … OK`.

Why these do not duplicate `tests/test_fleet_multi_runtime_routing.py` (55 tests,
still green): that file pins that a **read** follows the reference and that a
**listing** walks `device × runtime`. This lane adds the three **write** verbs —
the ones that can act on the wrong account — plus the model ceiling, the
credential-owner `UNKNOWN`, and the instance-identity boundary.

### Finding for the Runtime Lane (not fixed here)

**`send_to_session` folds "unbound runtime" into "unauthorized".**

`_may_send` is called before `_require_provider`, and it reads
`binding is None or not binding.allow_send` as a single condition
(`fleet.py:314-323`). A session ref naming a runtime this host holds **no
binding for** is therefore answered `APPROVAL_REQUIRED` — "not authorized for
control" — when the true statement is "there is no route to that runtime".
`create_session` answers the identical situation with `UNKNOWN_DEVICE`, because
it calls `_require_provider` first.

Nothing is mis-delivered: both refuse, and no provider is reached. The cost is
which file an operator is sent to edit — the policy for an authorization
question, the binding for a routing one. On a device whose second account is
simply not running yet, that is the common case.

Pinned by `TheSendRefusalFoldsUnboundIntoUnauthorizedTest`, which asserts
**today's** behaviour so a fix cannot land silently; that test is expected to be
updated when the code changes, and the update is the evidence the fix landed.

---

## 6. (d) Binding and SessionRef adaptation — advice

### The gap

A binding is keyed by `(device_ref, runtime)`. That names an **account**, which
is durable, and says nothing about the **instance** behind it. `ProviderBinding`
has no instance field (`instance_id`, `generation`, `boot_id` — none exist).
Two successive instances of `fjd`'s runtime are therefore indistinguishable to
the fleet, as `TheBindingNamesAnAccountAndNotAnInstanceTest` measures directly.

What that costs, concretely: a restarted or re-provisioned runtime keeps every
binding key, so the fleet cannot tell "the same runtime came back" from "a
different runtime took its place", and a stale route reads as a live one.

### The advice

1. **Add a persisted instance identity to the binding**, alongside `runtime` —
   opaque, stable across restarts, changing only when the runtime genuinely
   becomes a different runtime (re-provision, credential rotation, state-root
   replacement).
2. **Keep it out of the session ref.** `SessionKey.parse` refuses a 4-part
   string with `BAD_SESSION_REF`, and that refusal is the invariant: if the
   instance id reached the ref, every existing
   `device:llm-lab-dell|claude|<uuid>` would be re-addressed at the moment of
   upgrade, and there would be a window in which two readings of one address
   disagree — which is exactly the window a message is delivered in the wrong
   place. The ref must stay `device|provider|native`.
3. **Do not smuggle it into the provider tag.** The grammar
   `^[a-z][a-z0-9._-]{0,31}$` would accept it. `runtime_identity`'s rule is that
   the tag is a function of the *account* alone, which is what makes the address
   stable across a restart; an instance-bearing tag breaks that and is the
   failure mode the module's own comment warns about.
4. **Carry it where the binding is described**, so `fleet_status` /
   `fleet_list_devices` can report which instance a route currently reaches, and
   a mismatch is visible rather than inferred.
5. **Do not mint a second `device:` ref.** One machine is one authenticated
   device. The account rides in the tag; the instance rides beside the binding.
   Two accounts must never become two devices.

Items 2 and 3 are pinned by `TheSessionRefMustNotChangeTest`. Items 1, 4 and 5
are the Runtime Lane's to implement; this lane has not edited
`fleet_mcp/runtime_identity.py` or `fleet_mcp/config.py`.

---

## 7. (e) The minimal V1 smoke, and its guard

Declared in `TheV1SmokeFloorTest` as data with a guard, not as a script — a
script can be run by whoever holds the checkout; a guarded plan has to be handed
a decision, and the decision is checkable afterwards.

**Runnable without owner authorization (read-only):**

1. `fleet_status` — which device this host believes it is
2. `fleet_list_devices` — the device answers on its own binding
3. `fleet_cloudcli_native_capability` — what *this* runtime enumerates, or `UNKNOWN`
4. `fleet_list_sessions` — the device's own runtime lists; the second account is
   reported as a **gap**, not as empty
5. `fleet_get_session` — the same native id under two accounts stays two refs

**Requires owner authorization — not operated by this lane:**

6. `fleet_create_session` — mints a session in an account, spends a credential, starts work
7. `fleet_send_to_session` (continue / reply) — starts a run
8. `fleet_interrupt_session` — ends work in progress

V1 explicitly does **not** include OpenAgents / model-tier formation, long-lived
Ability autonomy, or advanced resource telemetry. Only the four managed initial
instances and dynamic managed-account capability are in scope.

---

## 8. Honest status

| Item | Status |
|---|---|
| Dell `vince` runtime live and enforcing | **PASS** (measured) |
| Dell model / permissionMode readback | **UNKNOWN** (deliberate: no credential read) |
| `fjd` runtime exists | **NOT_OBSERVED** |
| `fjd` owner authorization | **BLOCKED** — B4 |
| Fleet registration of a second Dell account | **BLOCKED** — master-side, B5 |
| Two-account routing correctness (offline) | **PASS** (66 tests, fake transport) |
| Real two-account routing on the device | **NOT RUN** — requires B4 and B5 |
| Per-runtime node secret arranged | **NOT DONE** — this host's obligation, B6 |

**`fjd` is not connected to the fleet.** No local test, no live listener and no
SSH session is evidence of registration, and nothing in this lane should be read
as such.
