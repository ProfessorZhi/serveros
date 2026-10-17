# Fleet V1 N3 · network/service health and bounded recovery planning

> **Status: SOURCE_ONLY / OFFLINE.** This package is a pure model plus synthetic
> fixtures. It performs no network probe, no process inspection, no service
> start/stop/restart, no Tailscale reset, no SSH-tunnel change, no RDC action,
> no provider login and no remote recovery. It does not claim LIVE, REAL_TOY,
> REAL_WINDOWS or PRODUCTION evidence.

- Campaign: [Issue #244](https://github.com/ProfessorZhi/serveros/issues/244)
- Lane: [Issue #247](https://github.com/ProfessorZhi/serveros/issues/247) (N3)
- Base: `4a3162864911420309665e92ec38fb68e9ef76ec` (`origin/main`)
- Branch: `feat/fleet-v1-network-health-20261018`
- Inputs read (never edited): `runtime/health.py`, `runtime/probes.py`,
  `native/node-core/**` from N1 Draft PR #242 `bec4835c`, and the Fleet
  Connector contracts (`fleet/transport-contract.yaml`,
  `fleet/device-contract.yaml`, `fleet/node-interface-contract.yaml`,
  `fleet/remote_desktop_commander.py`).
- Owned paths: `integrations/fleet-v1-network-health/**` and
  `tests/test_fleet_v1_network_health.py` only.

## Why this exists

A running process, an open listener, an ESTABLISHED socket, a successful
`GET /health`, and a service that can actually serve an authenticated request
are different facts. The 2026-09-12 endpoint incident in `runtime/health.py`
held a transport connection for the whole outage while sitting in a device
authorization loop; the process was `active` and the service served nothing.
N3 is the network/access version of the same failure: `127.0.0.1:16095` may
accept a connection while the SSH forward is down, the remote app is
unauthenticated, or the answer is coming from another device.

So the model keeps five axes separate:

| Axis | The question it answers | What it is not |
| --- | --- | --- |
| `liveness` | Does the OS manager say the process/unit/task is running? | Service health |
| `listener` | Does the declared local listener accept a connection? | A remote application |
| `transport` | Does the declared transport carry a real request/response? | Identity or authorization |
| `identity` | Are the device, OS principal, tunnel and remote owner the declared ones? | Application readiness |
| `functional` | Does the application answer an **authenticated** readiness call? | A TCP connect |

`HEALTHY` requires every axis the profile declares, including `functional`. A
profile that only measures local layers reports `LOCAL_ONLY` and never
`HEALTHY`. An uncovered combination raises `HealthUndetermined`; there is no
fallback word.

## Service dependency profiles (separately owned)

| Profile / surface | Actual owner | Facts that may be measured | Evidence that is **not** accepted | Recovery authority |
| --- | --- | --- | --- | --- |
| Per-node **Tailscale** | The node's authorized OS principal | Local OS-manager liveness, tailnet transport, provider-reported self identity, tailnet functional readiness | A process name/PID, a hostname or tailnet name as ServerOS identity, another node's healthy tailnet as proof for this one | Local device owner only; local service restart under a declared grant; never `tailscale reset`, never another node's state |
| **CloudCLI / Fleet Node Connector** | The local OS principal | Unit liveness, declared local listener, connector transport, provider identity, authenticated provider readiness | A `GET /health` alone, tool discovery as entitlement, a connector on another device | Local unit restart under grant; link-dependent; `NEEDS_LOGIN` is human |
| **Linux SSH tunnel** | Local OS principal for the local tunnel; remote application owner separately | Local listener, SSH forward transport, SSH identity, remote application functional readiness | "Remote app is healthy" as proof the local tunnel is up; raw process arguments; a guessed tunnel id | Local tunnel re-establish/restart only; never restart the remote app; per-service `offline_recoverable` for a crashed local tunnel |
| **Windows `127.0.0.1:16095` → Selkies / Claude Web** | Windows OS principal for the local listener/forward; remote Selkies owner for the application | Task/SCM liveness, loopback listener, SSH-forward transport, local Windows principal identity; authenticated application readiness only if it is actually measured **and** the remote owner identity is declared | The port number as a service name; a URL query; a speculated SSH endpoint or SCM registration; loopback reachability as application health | Local owned SSH forward only; remote Selkies is fixed at its actual remote owner; verdict is `LOCAL_ONLY` until authenticated app health is measured |
| **Selkies stream (local / remote)** | The stream's host owner (`local` or `remote` scope) | Listener, stream transport, identity, authenticated stream readiness | A listener connect as "the interactive stream works"; a local success as proof for the remote half | Local scope: local owner under grant. Remote scope: remote owner, with an **external** grant; this node may only return a request plan |
| **Remote Desktop Control (RDC)** | The target machine owner | Local adapter/service liveness, transport, identity, authenticated session readiness | Tool presence as desktop-control authority; "can connect" as "may operate" | **Human only.** No automated action, even with every grant |

The Windows profile hard-codes only the user-stated endpoint
`127.0.0.1:16095`. It does not declare a service name, a tunnel id, a remote
device, a remote address, or a PID. Those remain `None`/`UNKNOWN` until an
owner or provider actually declares them.

## Fail-closed verdicts

| Verdict | Meaning | Recovery disposition |
| --- | --- | --- |
| `UNKNOWN` | A required axis has no authoritative reading | `REFUSED / UNREAD_OBSERVATION` |
| `STALE` | A reading is older than its TTL, or has a future timestamp | `REFUSED / UNREAD_OBSERVATION` |
| `AMBIGUOUS` | Two authoritative readings of one axis disagree | `REFUSED / UNREAD_OBSERVATION` |
| `NEEDS_LOGIN` | The service itself says a person must renew authorization | `NEEDS_HUMAN / REQUEST_LOGIN`; never a restart loop |
| `MANUAL_STOPPED` | A person stopped it | `REFUSED / MANUAL_STOPPED`; never auto-start |
| `WRONG_OS_USER` | Requester or observation principal does not match the owner | `REFUSED / WRONG_OS_USER` |
| `WRONG_DEVICE` | Requester device does not match the owner | `REFUSED / WRONG_DEVICE` |
| `FOREIGN_DEVICE_SERVICE` | A reading belongs to another device, or names a different remote owner | `REFUSED / FOREIGN_DEVICE_SERVICE` |
| `MISSING_GRANT` | The declared read or recovery grant is absent | `REFUSED / MISSING_GRANT` |
| `IDENTITY_UNSPECIFIED` | A required identity field was never declared | `REFUSED / IDENTITY_UNSPECIFIED`; no guessing |
| `REMOTE_OK_LOCAL_DOWN` | The remote application answered, but the local listener/transport did not | Local-only plan; the remote owner is **not** the repair authority |
| `REMOTE_APP_UNHEALTHY_UNOWNED` | The local layers are up and an authenticated app reading is `FAIL`, but no remote owner is declared | `NEEDS_HUMAN`; never restart the local half as a substitute |
| `LOCAL_ONLY` | Local layers are OK; authenticated application health is not claimed | `NO_ACTION`; never rendered as `HEALTHY` |
| `UNHEALTHY` | A required axis answered `FAIL` | Plan or refusal under the gates below |

A journal line is corroborating evidence only: a non-`UNKNOWN` state carried by
`journal-diagnostic` is rejected at construction. A PID, service name, hostname
or tailnet name never participates in identity matching.

## Recovery is a plan, never an effect

`plan_recovery()` returns symbolic actions (`RESTART_LOCAL_SERVICE`,
`REESTABLISH_LOCAL_TUNNEL`, `REQUEST_LOGIN`,
`REQUEST_REMOTE_OWNER_RECOVERY`, `REQUEST_EXTERNAL_ACTION`) and a decision.
Every plan has:

- `executable: False`
- `execution_denied_reason: "SOURCE_ONLY_MILESTONE"`
- no argv, command, environment, credential, token or login material

Gate order (the first matching refusal is the one a reader is told to fix):

1. manual stop / wrong device/principal / foreign service / missing identity or grant;
2. `NEEDS_LOGIN` → human; `HUMAN_ONLY` → human;
3. `HEALTHY` / `LOCAL_ONLY` → no action;
4. unread/stale/ambiguous observation → refusal;
5. `NO_LEASE` → `MASKED` → `INSTALL_STATE_UNKNOWN` → `NOT_INSTALLED`;
6. link gate: `LINK_STATE_UNKNOWN` / `LINK_DOWN`; a per-service
   `offline_recoverable: true` may exempt a **local** tunnel action only;
7. `AGENT_STATE_UNKNOWN` / `AGENT_BUSY`;
8. owner/device/grant gates; remote/external actions require an explicit
   external grant;
9. retry/backoff budget: `max_attempts`, exponential delay capped at
   `max_delay_s`, `RETRY_BUDGET_EXHAUSTED` when spent.

Action selection is **axis-aware**. A `functional` failure with a declared
remote owner becomes `REQUEST_REMOTE_OWNER_RECOVERY` under an external grant,
even for a profile whose local tunnel is owned by this device. The module does
not turn "remote app failed" into a local tunnel restart, and it does not turn
"local tunnel down, remote app answered" into a remote action.

`offline_recoverable` defaults **false**. `link_dependent` defaults **true**.
`lease_available`, `install_state`, `link_state` and `agent_state` default to
their refusing values. Nothing in the module reads the host to fill them in.

## Fixtures and the Windows mock

`fixtures/*.json` are synthetic, deterministic (`NOW = 2026-10-18T00:00:00Z`)
and `fixture_only: true`. They cover Tailscale, CloudCLI/Fleet Node, Linux SSH
tunnel, Windows 16095/Selkies, local and remote Selkies streams, and RDC.

The Windows case is a **mock host** (`mock-windows-task-scheduler`,
`mock-windows-loopback`, etc.). It reports local liveness/listener/transport/
identity and deliberately leaves `functional` `UNKNOWN`. Its verdict is
`LOCAL_ONLY`, not `HEALTHY`; no service name, tunnel id or remote address is
invented.

## Blocked future approvals

| Gate | Status | What would be required |
| --- | --- | --- |
| `REAL_WINDOWS` | **BLOCKED** | A short, separately authorized native Windows window to read the actual SCM/Task Scheduler, loopback listener, owned SSH forward and (where access is authorized) the Selkies application. Not authorized by this milestone. |
| `REAL_TOY` | **BLOCKED** | An approved isolated toy service/tunnel on Tokyo with owner consent, a bounded recovery run and post-action functional reprobe. Toy PASS would prove the mechanism, not production permission. |
| `LIVE_NETWORK` | **BLOCKED** | Any live Tailscale/SSH/Selkies/RDC probe or remote host contact. This milestone performs none. |
| `PRODUCTION_RECOVERY` | **BLOCKED** | Any real restart, tunnel change, `tailscale reset`, provider account login or RDC action. It requires per-unit owner approval, a durable writer lease, an explicit grant and a rollback plan. |

No credential, token, cookie, private key, raw process argument, environment
dump or URL query is accepted by the observation boundary; the fixture/test
path rejects those keys and secret-shaped strings.

## Verification

One bounded offline file only:

```bash
cd <worktree>
flock -n /tmp/fleet-v1-network-health-20261018.lock \
  systemd-run --user --scope -q \
    -p MemoryMax=1073741824 -p MemorySwapMax=0 -p CPUQuota=25% \
    -- timeout 600 /data/home/vince/toolchains/serveros-test/bin/pytest \
       tests/test_fleet_v1_network_health.py -q -p no:cacheprovider
```

Claimed evidence for this milestone: **CODE + MOCK/LOCAL synthetic only**.
`REAL_WINDOWS`, `REAL_TOY`, `LIVE_NETWORK` and `PRODUCTION_RECOVERY` are
explicitly **BLOCKED**, not implied by the synthetic pass.
