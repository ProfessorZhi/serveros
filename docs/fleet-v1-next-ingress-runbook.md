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
  route. That sentence is enforced in code, not only written here.

---

## 1. The stage ladder

Monotone and fail-closed. A stage is reached only when every stage below it has
its own evidence, and the gate reports `BLOCKED` rather than letting a lower
stage claim a higher one.

| # | Stage | Requires |
|---|---|---|
| 0 | `SOURCE` | the reviewed release is present |
| 1 | `STAGED_NO_START` | Next bytes staged, **no** service started |
| 2 | `NEW_NODE_REGISTERED` | **`owner_login_attested`** + `node_registered` |
| 3 | `HTTPS_LIVE` | Funnel on the new node, aimed at **this** ingress's loopback port |
| 4 | `NEXT_MCP_LIVE` | Next user unit active, cgroup caps confirmed |
| 5 | `FEISHU_AUTHENTICATED` | one real Feishu login against the new callback |
| 6 | `CHATGPT_INSTALLED` | the real plugin, its own PluginID, one tool discovery |

`owner_login_attested` is a **prerequisite of registration**, not a parallel
flag: a node that was not interactively logged in by the owner was registered by
something else. The gate refuses `node_registered` without it.

---

## 2. Repository-side checks (offline, no device, no socket)

```bash
# the isolation gate for the NEW node: old node, old state, old port, old unit,
# issuer/discovery/callback consistency, credential-shaped fields, the ladder
python3 -I fleet_mcp/deploy/next_tailscale_gate.py \
        --plan fleet_mcp/deploy/next_tailscale_plan.example.json

# the owner sheet for the next stage (still writes nothing, executes nothing)
python3 -I fleet_mcp/deploy/next_tailscale_gate.py \
        --plan <reviewed-plan.json> --manual-run --owner-approved

# the #198 dual-stack gate, unchanged, still the first thing to pass
python3 -I fleet_mcp/deploy/next_dual_stack_preflight.py --proposal <dual-stack.json>

# tests for this lane
python3 -I tests/test_fleet_v1_next_tailscale_gate.py     # 40 offline cases
```

All four are dry-run. `decision: "PLAN_READY"` means **the plan is isolated**,
not that anything was deployed.

---

## 3. Host-side sequence (Tokyo owner, under the resource gate)

Each step states what it proves and what must be read back. Nothing here is
authorized by this document.

### Step 1 → `STAGED_NO_START`

```bash
python3 -I fleet_mcp/deploy/next_install.py stage \
        --proposal <dual-stack.json> --no-start      # add --execute --owner-approved to act
```
Read back: the Next revision root holds the bytes and **no Next unit is running**.

### Step 2 → `NEW_NODE_REGISTERED` ⚠️ **owner browser required**

Confirm the candidate loopback port is free **on the Tokyo host** — `3411` is a
candidate from the plan, not a reservation:

```bash
ss -tlnH | grep -E ':(3411|3401|3402)$'      # expect no Next entry; V1 untouched
```

Then start the **new node's own** daemon with its own state dir and socket, and
complete the login **in the owner's own browser, in the owner's own session**:

* **Owner web login is required here, and only here.** If the tailnet requires
  MFA, that happens in the same browser interaction.
* **Never an auth key.** No `--authkey`, no `TS_AUTHKEY`, no pre-authorised key
  in a file, environment or CI variable. `next_tailscale_gate.py` has no field
  for one and refuses the field name if it appears — the registration must be the
  owner's own browser action, so that "who registered this node" is answerable.

Read back: the new node appears as **its own machine** in the tailnet, and the
existing node's state, socket and service are **unchanged**.

### Step 3 → `HTTPS_LIVE`

Enable Funnel **on the new node only**, aimed at `127.0.0.1:<next port>`. Read
back over the new `*.ts.net` origin:

```bash
curl -sS https://<next-host>.ts.net/health
curl -sS https://<next-host>.ts.net/.well-known/oauth-protected-resource
curl -sS https://<next-host>.ts.net/.well-known/oauth-authorization-server
```

All four paths — `/health`, the two discovery documents, `/mcp` — must answer on
**one** origin, which is `https://<next.tailnet_host>`. The Feishu callback
`/oauth/feishu/callback` is registered in a console; the gate pins it exactly
because a console URL is not something an operator can silently fix.

### Step 4 → `NEXT_MCP_LIVE`

Start the Next user unit, then **measure** that the caps took effect — a template
cannot prove isolation:

```bash
systemctl --user show serveros-fleet-next-mcp.service \
  -p MemoryMax -p MemorySwapMax -p CPUQuota -p TasksMax
```
Expect `MemoryMax=1536M`, `MemorySwapMax=0`, `CPUQuota=40%`, `TasksMax=96`, and
**exactly one** Next instance. Re-check V1 `/health` in the same window.

### Steps 5–6 → `FEISHU_AUTHENTICATED`, `CHATGPT_INSTALLED`

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
| Never claim a stage without its evidence | gate: monotone ladder, `BLOCKED` on a gap |
| Never invent a second installer or budget | test: the gate source contains no `MemoryMax=`/`CPUQuota=`/`TasksMax=`/`memory_max_mib` |
| Never run a privileged or system-wide step from the sheet | test: no sheet step contains `sudo`, `systemctl restart`, `/etc/`, `authkey`, `tskey` |

This branch does **not** change `sshd`, `sudoers`, PAM, the tailnet, the Feishu
console, any running service or any device identity, and does not open a public
port.

---

## 5. Mock vs real — read this before quoting a result

| Claim | Status |
|---|---|
| The isolation rules and the ladder behave as specified | **REAL** — 40 offline cases, pure functions |
| The #198 dual-stack gate still passes on a reviewed plan | **REAL** (offline) |
| The gate refuses credential-shaped fields without echoing them | **REAL** (offline) |
| Candidate port 3411 is free | **UNVERIFIED** — must be measured on the Tokyo host |
| A Next Tailscale node exists | **NOT DONE** |
| Funnel / HTTPS origin live | **NOT DONE** |
| Next MCP reachable to ChatGPT | **NOT DONE** |
| Feishu login completed | **NOT DONE** |
| Any stage above `SOURCE` reached on a real host | **NOT DONE** |

**Nothing in this branch has been deployed.** Offline tests over pure functions
prove the *rules*; they are not evidence that a host, a node, an origin or a
plugin exists.

---

## 6. V2 boundary

OpenAgents, model-tier formation, long-lived Ability autonomy and advanced
resource governance are **out of scope for V1** and are not designed here.
