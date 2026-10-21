# Ephemeral V1 acceptance smoke — 2026-10-18

**Scope: `REAL_TEMP_PROCESS` only. Nonproduction, ephemeral, no service, no
production DB, no provider send.** This is an I1 receipt, not a QA signature and
not a release.

Authorization: explicit user approval in the current chat for an isolated
Tokyo/vince temporary `serverosd + Work Engine` acceptance test. Scheduled
automation remains OFF. Everything ran as the existing `vince` user; no uid or
permission change.

## Pinned source

| Item | SHA |
|---|---|
| serveros combined nonprod #276 (source used) | `91006975e52765bb980e32929f7f54d813f6c0f9` |
| serveros grant/peer authority #275 | `92d7a92c971a682078e9d125e3ee7ee80ea2bfb6` |
| serveros strict V1 #277 | `7b84ae191b67b74f398dee2652339d4b9a218d84` |
| Work Engine #15 (finalize entry) | `4bd9e8a1c6a6a1b24c1717ec65862c8900d5f7be` |
| Work Engine #16 (read-only context only) | `8b32aabdfc4e25f99b271e2667bb2e4ae0228b2e` |
| independent QA #256 | `65f80d93c1c596637a40b67112b4d94503577510` (`QA_PARTIAL`) |

Work Engine was used from a **read-only `git archive`** of `#15`. #16 was not
called, validated, patched or reproduced; the previously `BLOCKED_ACTION`
compatibility bridge was not retried or rerouted.

## Isolation

- `mktemp -d /tmp/i1-v1-accept.XXXXXX`, mode `0700`, removed in a `trap cleanup`.
- Empty registry (`resources: []`), explicit `--state-dir`, explicit unique
  `--socket`; **no `--enable-reconcile`**, no default socket, no release pointer.
- Run under `systemd-run --user --scope` with `MemoryMax=1G`,
  `MemorySwapMax=0`, `CPUQuota=25%`, `TasksMax=64`, `timeout 600`, plus a
  non-blocking `flock`.
- Only the recorded daemon PID and the scope job PID were terminated; nothing
  shared was signalled.

## Results

| Gate | Result | Evidence |
|---|---|---|
| serverosd start on temp state/socket | `REAL_TEMP_PROCESS_PASS` | socket created mode `600`; `serverosd: listening on <temp>/sock` |
| `agent.lease.authority.verify` with no `context_ref` | `REAL_TEMP_PROCESS_REFUSAL` | `verified=false`, `UNTRUSTED_ACTOR` |
| … with nonexistent `context_ref` | `REAL_TEMP_PROCESS_REFUSAL` | `verified=false`, `UNTRUSTED_ACTOR` |
| … with grant/`lease.acquire`/item/project/epoch but no trusted context | `REAL_TEMP_PROCESS_REFUSAL` | `verified=false`, `UNTRUSTED_ACTOR` |
| … with `work.commit` action, wrong project, epoch 999 | `REAL_TEMP_PROCESS_REFUSAL` | `verified=false`, `UNTRUSTED_ACTOR` |
| `agent.grant.issue` with unsigned evidence | `REAL_TEMP_PROCESS_REFUSAL` | `StoreError: DELEGATION_EVIDENCE_INVALID: signed provider evidence required` |
| No grant silently minted | `REAL_TEMP_PROCESS_PASS` | `agent-control/store.json` absent; re-verify still `UNTRUSTED_ACTOR` |
| Positive `create → lease → send` | **`BLOCKED_TRUST_SOURCE`** | no legitimate source-authorized temp signed context/grant issuance path, and no production trusted parent |
| WE `run finalize --t10-required` without a trusted parent | `REAL_TEMP_PROCESS_REFUSAL` | exit **2**, `BLOCKED_TRUST_SOURCE`; Run stayed `FINALIZING`; evidence 0 |
| WE legacy finalize on the same Run | `REAL_TEMP_PROCESS_PASS` (not V1 accepted) | exit 0, `authorization="UNSIGNED"`, `t10_acceptable=false`, Run `SUCCEEDED` |
| Cross-repo lease-acquire + T10 finalize positive | **`BLOCKED_TRUST_SOURCE`** | no OS-attested parent; no production finalization caller |
| Provider / Agent send | `NOT_RUN` | explicitly out of scope |
| Dell/Windows native, legitimate client OAuth E2E | `NOT_RUN` | out of scope |

Kernel peer provenance: the daemon takes the peer from `SO_PEERCRED` in its own
accept path (`_CALLER_PEER` set per call); no `handle(peer=fake)` seam was used
in the primary test. No positive case reached a peer-bound grant, so **no
observed peer-binding proof is claimed** — the provenance is `SOURCE` for the
code path and `NOT_RUN` for a successful peer-bound verify.

## Exact commands (sanitized)

```sh
# serverosd (temp registry/state/socket, observe-only)
python3 <src>/bin/serverosd --registry <tmp>/registry.yaml \
  --state-dir <tmp>/state --socket <tmp>/sock --interval 3600
# one JSON line per request over the temp AF_UNIX socket
{"method":"agent.lease.authority.verify","params":{...}}
{"method":"agent.grant.issue","params":{"evidence":{...}}}

# Work Engine #15 from a read-only archive
python3 -m workengine item create --project SMOKE --prefix SRV --title "i1 smoke" --kind test
python3 -m workengine activity add SRV-0001 ACT-1 --kind execute
python3 -m workengine run begin SRV-0001 ACT-1 --worker smoke --idempotency-key k1
python3 -m workengine run advance  SRV-0001 RUN-0001 --to PREPARING
python3 -m workengine run advance  SRV-0001 RUN-0001 --to RUNNING
python3 -m workengine run advance  SRV-0001 RUN-0001 --to FINALIZING
python3 -m workengine run finalize SRV-0001 RUN-0001 --verdict PASS --exit-code 0 \
  --command true --revision smoke-rev --t10-required     # exit 2 BLOCKED_TRUST_SOURCE
python3 -m workengine run finalize SRV-0001 RUN-0001 --verdict PASS --exit-code 0 \
  --command true --revision smoke-rev                     # exit 0, UNSIGNED, t10_acceptable=false
```

## Cleanup

- Daemon PID dead after `SIGTERM`; the temp AF_UNIX socket file was **not**
  unlinked by the daemon on `SIGTERM` (observed), and the `0700` temp dir
  removal removed it.
- `temp_removed=YES`; no shared process, service, socket or path was touched.

## What this does not establish

Not `V1_CONNECTED`. Not a release. Not a QA pass. There is no production
`acquire_lease`, no production T10 finalization caller, no OS-attested
`LeaseAuthority` parent, and no provider/device/client evidence. G1 #255 was not
touched and its earlier `BLOCKED_ACTION` was not retried.
