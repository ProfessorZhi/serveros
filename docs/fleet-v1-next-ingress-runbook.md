# Fleet V1 · Next ingress runbook — independent Tailscale node

Lane E · branch `feat/fleet-v1-independent-ingress-20261015` (from #198 `8186028`)
Scope: **repository source, tests and this sheet.** Tokyo host operations are
performed by the Tokyo owner under the resource gate, not by this branch.

---

## 0. What this runbook is not

* **Not an installer.** It reuses #198's `next_install.py` and
  `next_dual_stack_preflight.py` and the existing Next unit file. There is no
  second installer here.
* **Not an execution.** `next_tailscale_gate.py` is pure: `writes: []`,
  `executes: []`, always. `--manual-run` prints a sheet and stops.
* **Not proof of ChatGPT usability.** One open TCP port is not a usable MCP
  route, and a self-reported flag is not public liveness. Both sentences are
  enforced in code, not only written here.

---

## 1. The stage ladder

Monotone and fail-closed. A stage is reached only when every stage below it has
its own evidence.

The rungs are ordered so that **no step asks for something the stage cannot
produce**. In particular the route and certificate rungs run against a backend
that is deliberately not started, and they never ask for an HTTP status — the
unit only starts one rung later. Asking for `/health` 200 at
`NEXT_ROUTE_PROVISIONED` would forbid its own next stage.

| # | Stage | Requires | Backend running? | A-lane phase |
|---|---|---|---|---|
| 0 | `SOURCE` | the reviewed release is present | no | — |
| 1 | `STAGED_NO_START` | Next bytes staged, **no** service started | no | S1/S2 |
| 2 | `NEW_NODE_REGISTERED` | `owner_login_attested` **and** `node_registered` | no | S2 |
| 3 | `NEXT_ROUTE_PROVISIONED` | Funnel on the new node → **this** ingress's loopback port; route published and read back from the tailnet side | **no — must not be started for this** | **S3a** |
| 4 | `TLS_CERT_READY` | the issuer host's TLS handshake and served chain | **no** | **S3a** |
| 5 | `NEXT_UNIT_LIVE` | the Next user unit is up; cgroup caps read back; exactly one instance | yes | **S3b** |
| 6 | `PUBLIC_MCP_VERIFIED` | a **recorded readback** of the public origin: `/health` + both discovery documents at 200, the `/mcp` tool face, and the issuer/audience it reported | yes | **S4** |
| 7 | `FEISHU_AUTHENTICATED` | one real Feishu login against the new callback | yes | S5 |
| 8 | `CHATGPT_INSTALLED` | the real plugin, its own PluginID, one tool discovery | yes | S6 |

Two things this ordering fixes:

* **No circular block.** Rungs 3–4 are completable with the backend down, and
  the sheet says so. At rung 3 a connection refused or a 502 *behind a working
  route* is the correct observation, not a failure; at rung 4 a successful TLS
  handshake with no HTTP response is the expected result.
* **No self-reported liveness.** `PUBLIC_MCP_VERIFIED` is **derived** from
  `public_readback`. There is no `public_verified` flag in the plan schema — a
  plan that sets one is refused for an unsupported field, so the stage cannot be
  claimed by assertion.

`owner_login_attested` is a prerequisite of registration, not a parallel flag: a
node that was not interactively logged in by the owner was registered by
something else.

---

## 2. Repository-side checks (offline, no device, no socket)

```bash
# the isolation gate for the NEW node: old node, old state, old port, old unit,
# issuer/discovery/callback consistency, credential-shaped fields, the ladder
python3 -I fleet_mcp/deploy/next_tailscale_gate.py \
        --plan fleet_mcp/deploy/next_tailscale_plan.example.json

# the owner sheet for the NEXT stage (still writes nothing, executes nothing)
python3 -I fleet_mcp/deploy/next_tailscale_gate.py \
        --plan <reviewed-plan.json> --manual-run --owner-approved

# the #198 dual-stack gate, unchanged, still the first thing to pass
python3 -I fleet_mcp/deploy/next_dual_stack_preflight.py --proposal <dual-stack.json>

# tests for this lane
python3 -I tests/test_fleet_v1_next_tailscale_gate.py     # 58 offline cases
```

All four are dry-run. `PLAN_READY` means **the plan is isolated** and names the
stage it honestly stands at — never that anything was deployed.

`run_sheet` prints the sheet for the stage **after** the one the plan stands at.

---

## 3. Host-side sequence (Tokyo owner, under the resource gate)

Each step states what it proves and what must be read back. Nothing here is
authorized by this document.

### Rungs 0–1 → `STAGED_NO_START`

At this point the **new node is not registered**, so its real public FQDN
does not exist yet. The reviewed dual-stack proposal must explicitly set
`next.public_mcp_url: null` and may set `next.plugin_id: null` for the
**non-starting `stage` command only**. `null` means *unassigned*, not a
fictional or healthy endpoint; omitting the URL field, supplying a malformed
URL, or reusing the V1 endpoint is still refused. `install` still requires
a distinct HTTPS `/mcp` URL and a real registered Plugin ID. The stage gate
also retains its V1 identity, owner binding review, rollback readiness, path,
port, resource budget and Next-only unit checks. No consent is inferred from
the proposal or this document.
```bash
python3 -I fleet_mcp/deploy/next_install.py stage \
        --proposal <dual-stack.json> --no-start      # add --execute --owner-approved to act
```
Read back: the Next revision root holds the bytes and **no Next unit is running**.

### Rung 2 → `NEW_NODE_REGISTERED` ⚠️ **owner browser required**

Confirm the candidate loopback port is free **on the Tokyo host**. The owner's
read-only check reported `3411` free, `user-systemd` available and memory PSI
`full=0`; re-measure at execution time, because a free port is a point-in-time
fact and not a reservation:

```bash
ss -tlnH | grep -E ':(3411|3401|3402)$'      # expect no Next entry; V1 untouched
```

Then start the **new node's own** daemon with its own state dir and socket, and
complete the login **in the owner's own browser, in the owner's own session**:

* **Owner web login is required here, and only here.** If the tailnet requires
  MFA, that happens in the same browser interaction.
* **Never an auth key.** No `--authkey`, no `TS_AUTHKEY`, no pre-authorised key
  in a file, environment or CI variable. The gate has no field for one and
  refuses the field *name* if it appears.

Read back: the new node appears as **its own machine**, and the existing node's
state, socket and service are **unchanged**.

### Rung 3 → `NEXT_ROUTE_PROVISIONED` — **backend stays down**

Enable Funnel **on the new node only**, aimed at `127.0.0.1:<next port>`, then
read the route back from the tailnet side: which node serves it, and which
loopback port it targets.

**Do not start the unit to satisfy this step.** No HTTP status is expected,
asked for or recorded here. A connection refused or a 502 behind a working route
is the correct observation at this rung.

### Rung 4 → `TLS_CERT_READY` — still no backend

Read the certificate back as a **TLS fact**: the handshake and the served chain
for `<next.tailnet_host>`.

```bash
openssl s_client -connect <next-host>.ts.net:443 -servername <next-host>.ts.net </dev/null
```
A successful handshake with no HTTP response is the expected result. **No 200 may
be claimed here.**

### Rung 5 → `NEXT_UNIT_LIVE` — the backend starts now

Start the Next user unit, then **measure** that the caps took effect — a template
cannot prove isolation:

```bash
systemctl --user show serveros-fleet-next-mcp.service \
  -p MemoryMax -p MemorySwapMax -p CPUQuota -p TasksMax
```
Expect `MemoryMax=1536M`, `MemorySwapMax=0`, `CPUQuota=40%`, `TasksMax=96`, and
**exactly one** Next instance. Re-check V1 `/health` in the same window.

### Rung 6 → `PUBLIC_MCP_VERIFIED` — now fetch the public origin

```bash
curl -sS -o /dev/null -w '%{http_code}\n' https://<next-host>.ts.net/health
curl -sS -o /dev/null -w '%{http_code}\n' https://<next-host>.ts.net/.well-known/oauth-protected-resource
curl -sS -o /dev/null -w '%{http_code}\n' https://<next-host>.ts.net/.well-known/oauth-authorization-server
# and the tool face, over the same origin
```

Record what came back — the stage is reached from the recording, not from a flag:

```json
"public_readback": {
  "health_status": 200,
  "oauth_protected_resource_status": 200,
  "oauth_authorization_server_status": 200,
  "mcp_tools_seen": 33,
  "issuer": "https://<next-host>.ts.net",
  "audience": "https://<next-host>.ts.net/mcp"
}
```

A non-200, an empty tool face, a missing audience, or an issuer that differs
from `next.issuer` all keep the ladder where it is.

### Rungs 7–8 → `FEISHU_AUTHENTICATED`, `CHATGPT_INSTALLED`

One real Feishu login; then one plugin install under its **own PluginID**, and
one tool discovery over the new HTTPS origin. Only here may "ChatGPT can use it"
be claimed.

### Rollback (Next-only, always available)

`next_install.py rollback` addresses the Next unit only and never restarts V1.
`SyslogIdentifier=serveros-fleet-next-mcp` keeps the two journals separable.

---

## 4. Prohibitions, and where they are enforced

| Prohibition | Enforced by |
|---|---|
| Never modify or reuse the old node's Funnel / state / socket | gate: name, state-dir, socket, unit, default-socket, published-port, and cross-field path-overlap checks |
| Never accept, store, copy or log an auth key or old V1 token | gate: field-NAME audit over the whole plan, refusing **without echoing the value** |
| Never publish V1's port | gate: `funnel_target_port` must equal this ingress's own loopback port |
| Never bind anything but loopback | gate: `local_listen.host in {127.0.0.1, ::1}` |
| Never demand an HTTP status from a backend that is not running | gate: the two route rungs complete with `backend_running: false`, and their sheets contain no `/health` and no status demand |
| Never treat a self-reported flag as public liveness | gate: `public_verified` is not a plan field; the stage derives from `public_readback` |
| Never claim a stage without its evidence | gate: monotone ladder, held below the public rung by any gap |
| Never invent a second installer or budget | test: the gate source contains no `MemoryMax=`/`CPUQuota=`/`TasksMax=`/`memory_max_mib` |
| Never run a privileged or system-wide step from the sheet | test: no sheet step contains `sudo`, `systemctl restart`, `/etc/`, `authkey`, `tskey` |

This branch does **not** change `sshd`, `sudoers`, PAM, the tailnet, the Feishu
console, any running service or any device identity, and does not open a public
port.

---

## 5. Mock vs real — read this before quoting a result

| Claim | Status |
|---|---|
| The isolation rules and the ladder behave as specified | **REAL** — 58 offline cases, pure functions |
| The route/cert rungs complete without a running backend, and demand no status | **REAL** (offline) |
| A self-reported `public_verified` flag is refused; evidence is required | **REAL** (offline) |
| The gate refuses credential-shaped fields without echoing them | **REAL** (offline) |
| `--execute` publishes exactly one Next-only receipt, and reports `UNKNOWN` rather than success without one | **REAL** (offline; 38 cases) |
| The #198 dual-stack gate and the Next installer suite pass | **REAL** (offline; both were repaired in this branch, see §6) |
| Port **3411 free on the Tokyo host**; user-systemd available; memory PSI `full=0` | **REPORTED by the Tokyo owner's read-only check** — not measured by this branch, and a free port is not a service |
| Official userspace Tailscale **1.104.1** binary staged on the Tokyo host | **STAGED ONLY** — no node registered, no Funnel enabled |
| A Next Tailscale node exists | **NOT DONE** |
| Funnel / TLS / HTTPS origin live | **NOT DONE** |
| Next MCP reachable to ChatGPT | **NOT DONE** |
| Feishu login completed | **NOT DONE** |
| Any stage above `SOURCE` reached on a real host | **NOT DONE** |

The staged binary and the free port are real milestones and they are **still
below rung 2**. A staged artifact is not a registered node; a free port is not
a listener; `SOURCE` remains the highest stage reached anywhere.

**Nothing in this branch has been deployed.** Offline tests over pure functions
prove the *rules*; they are not evidence that a host, a node, an origin or a
plugin exists.

---

## 6. Repaired in this branch

### Two failures that existed at `8186028`

Each fixed on the side that preserves the other's intent:

* `next_dual_stack_preflight.py` — the dry-run note read `"never an
  authorization or a launch."` while `test_fleet_next_dual_stack_preflight.py`
  asserts the phrase `"not an authorization"`, which is also this repository's
  own idiom elsewhere. The **note wording** was brought in line; no behaviour
  changed.
* `test_fleet_next_install.py::cli()` — the helper unconditionally
  `json.loads`-ed stdout, but a successful `--execute` printed nothing. That
  first repair tolerated the empty stream, which was **wrong** and is
  superseded below: `install.py::install()` does print a receipt, so an empty
  stream is a symptom, not a valid result.

Both reproduced identically on a worktree containing none of this branch's
files, which is how they were confirmed pre-existing rather than introduced
here.

### The `--execute` receipt contract (supersedes the empty-stdout tolerance)

`install.py::install()` prints exactly one JSON object on each success path
(`--no-start`, and a full start), and `rollback()` prints one too. The wrapper
now **captures** that stdout, requires exactly one well-formed document, and
republishes a single Next-only receipt:

```json
{"decision": "EXECUTED", "unit": "serveros-fleet-next-mcp.service",
 "started": false, "health": null, "unit_path": "...",
 "effect_state": "NEXT_ONLY_ACTION_ATTEMPTED", "v1_action": false,
 "writes": ["..."], "executes": ["..."], "receipt": { ...the installer's own... }}
```

A command that returned 0 without leaving one usable receipt reports
`decision: "UNKNOWN"` and a nonzero exit — never success:

| Output | Result |
|---|---|
| one well-formed document | `EXECUTED`, with the installer's own `started`/`health`/`unit_path` |
| nothing | `UNKNOWN` — "the installer printed no receipt" |
| not valid JSON | `UNKNOWN` |
| two documents | `UNKNOWN` — one receipt is required, and picking one of two would be a guess |
| `SystemExit` after printing | refusal that **quotes the installer's own** `started`/`health` |

`writes` is never `[]` on an attempted effect.

### Same-revision restart is no longer reported as "untouched"

`_restore_failed_install` used to decide "nothing happened" from `current`
symlink equality. An install over an already-live unit takes the **restart**
path, and a restart of the *same* revision leaves `current` byte-identical
while replacing the process serving it — so a restart that never came back
healthy was reported as `existing Next untouched`, leaving an unhealthy Next
running under a clean-sounding line.

The wrapper now passes an explicit `effect_attempted` flag, because only the
caller knows whether an effect was attempted:

| Situation | Behaviour |
|---|---|
| failure **before** any effect (validation, path isolation, pre-launch health) | `existing Next untouched`; a healthy Next is **not** stopped |
| effect attempted, pointers moved | Next-only `stop` + pointer restore |
| effect attempted, same revision (no pointer to restore) | Next-only `stop`, and the receipt says the **Next service state is UNKNOWN until read back** |

V1 is never addressed in any branch, and the flag is the only thing
distinguishing the outcomes — the filesystem looks identical in both.

---

## 7. V2 boundary

OpenAgents, model-tier formation, long-lived Ability autonomy and advanced
resource governance are **out of scope for V1** and are not designed here.
