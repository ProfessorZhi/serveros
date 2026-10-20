# QA-TRIAGE-2026-10-20-w — Fleet Next unit-name P0 / boundary review

**Status: `LOCAL` + `HOSTED_CI` PASS for #330; `REAL_NEXT_CANARY` /
`PRODUCTION` NOT_RUN. No V1 signature; RC-v3 is not covered.**

Lane: `Q1-independent-qa`, campaign #244, parent #250, QA register #256.
Independent Session; no product source edited. Additive QA files only under
`integrations/fleet-v1-qa/**`; probes/logs under
`artifacts/skills/automated-testing/q1-security-qa-20261020/`.

Snapshot: **2026-10-20T08:2xZ (UTC)**.

---

## 1. Pinned inputs

| Item | Exact ref |
|---|---|
| F1 Draft PR #330 | `bf1b514f9bcb1386f14f3a866dfda8162ee5cfc4` |
| Base RC-v2 | `ec43f714c855e07055f32707d4afaebcc5313580` |
| Hosted run | `39389009969` (head `bf1b514`, conclusion `success`) |
| Prior QA | #331 `dcbdaa4` |
| RC-v3 | **not pushed** — PENDING |

The hosted run's `headSha` is exactly `bf1b514f…`; both jobs
(`offline-unit-contracts`, `runner-smoke`) are `success`. Its steps are the
committed offline contract steps (checkout exact PR contents, syntax check,
unittest discover for the four named suites, artifact upload). No production
systemd/OAuth/device action.

---

## 2. Root cause and exact RED → GREEN

The bug: `next_install.TEMPLATE`, `_HERE / "<template>"`,
`next_dual_stack_preflight.NEXT_UNIT`, the proposal example, the unit
`SyslogIdentifier`, and the workflow path filter all named
`serveros-fleet-example-mcp.service`, which the tree does not contain. The
committed Next template is `serveros-fleet-next-mcp.service`.

Reproduced at the base `ec43f71` with the exact Actions command:

```text
python3 -m unittest discover -s tests -p test_fleet_next_install.py
Ran 99 tests
FAILED (failures=18, errors=35)
KeyError: 'effect_state'          # plan() returned BLOCKED: template missing
```

At #330 `bf1b514` the same command is **100 OK**. The preflight suite is **28
OK**; `test_fleet_runtime_create_policy` **26 OK**;
`test_fleet_create_session_runtime` **13 OK**. These are the exact four
commands the hosted workflow runs; I ran them locally under the 1G/no-swap/25%
guard (no network beyond the venv's already-installed PyYAML).

Additional offline suites at #330: canary start **70 OK**, refresh-stage **29
OK**, stage-unit-receipt **1 OK**, tailscale gate **58 OK**.

---

## 3. Authority boundary — no silent change

The #330 diff is a **pure rename** (unit/template/proposal/docs/tests/workflow
path filter). It does not widen a check; one test is strengthened (it now also
asserts the V1 `SyslogIdentifier` is absent from the Next template).

Independent probe `qa_probe_r6_fleet_next_gate.py` — **32/32**:

- **Names**: `NEXT_UNIT != V1_UNIT`; both templates exist; the Next
  `SyslogIdentifier` is `serveros-fleet-next-mcp`, V1's is
  `serveros-fleet-mcp`; the two journals are distinct.
- **Next template**: keeps `MemoryMax=1536M`, `MemorySwapMax=0`,
  `CPUQuota=40%`, `TasksMax=96`, `NoNewPrivileges=yes`,
  `@FLEET_MCP_RUNTIME@`, `@FLEET_MCP_CONFIG@`; the V1 template does not carry
  the Next canary limits.
- **Preflight negatives** (independent proposal):
  - a valid proposal is `PRECHECK_READY_FOR_HUMAN_CANARY` with no
    writes/executes and `v1_changed=false`;
  - `next.service_unit = V1_UNIT` → `must use dedicated Next unit`;
  - nested/overlapping `runtime_root` → `overlaps V1`;
  - same listening port → `conflicts with V1 listening port`;
  - same public `/mcp` endpoint → `same published endpoint as V1`;
  - missing `next_owner_binding_reviewed` → BLOCKED in both normal and canary
    modes (owner-ack is not bypassed);
  - canary mode excusing only `plugin_id` and
    `next_oauth_client_authorized` → `CANARY_START_READY`; normal install still
    refuses those unassigned facts.
- **Installer**: `canary-start --no-start` is BLOCKED with no write/execute;
  `stage` is `NEXT_ONLY_STAGE_NO_START` with empty writes/executes; the
  installer source starts/stops only `UNIT` (Next), never `V1_UNIT`;
  `--execute` requires `--owner-approved`.
- **Workflow**: the path filter names the committed
  `serveros-fleet-next-mcp.service`; `permissions: contents: read`; the job runs
  offline `unittest discover`; no `systemctl`, no `deploy/install.py`, no
  service install/enable.

The canary path in `next_install.py` is one `systemctl start UNIT` (Next); it
never `enable`s, never publishes, never touches V1 except to read health and
`MainPID`; failure rollback is a Next-only `stop UNIT`. `install --no-start`
publishes without starting and requires the Next unit to be inactive and
disabled; `install` uses `enable --now` on the Next unit.

The canary fixtures/tests use a fake systemctl and temp directories; the hosted
run is the only external execution, and it is offline contract tests. **No real
systemctl, install, OAuth, token, host or production data was used by this QA.**

---

## 4. Levels

| Scope | Result | Level |
|---|---|---|
| #330 exact SHA hosted workflow | `success` (both jobs) | **HOSTED_CI** |
| Four exact Actions commands locally | 28 + 100 + 26 + 13 OK | **LOCAL** |
| Additional offline suites | 70 + 29 + 1 + 58 OK | **LOCAL** |
| Independent boundary probe | 32/32 | **LOCAL** |
| Base `ec43f71` reproduction | 99 tests, 18F + 35E | **LOCAL** |
| REAL_NEXT_CANARY (actual start on a device) | **NOT_RUN** | — |
| PRODUCTION / real OAuth / real systemd | **NOT_RUN** | — |
| RC-v3 | **PENDING**; #330 tests do not cover it or all T1–T12 | — |

---

## 5. Findings

- **No blocking finding in #330.** The P0 is the committed-versus-referenced
  unit name; the fix uses the one committed name and does not relax the
  isolation checks.
- **Scope note**: the hosted workflow's only external step is
  `pip install PyYAML==6.0.2`; I did not run it (the local venv already has
  PyYAML). The hosted run is read-only GitHub evidence.
- **Not a V1 signature**: #330 is a F1 naming fix. It does not cover RC-v3,
  the full T1–T12 matrix, the S1/S2/native slices, or REAL_DEVICE/CLIENT_E2E.
- **No production claim**: REAL_NEXT_CANARY and PRODUCTION remain NOT_RUN;
  no owner OAuth/token or real host was touched.
- **RC-v3 remains PENDING** (I1). A future RC-v3 must include #330 and rerun
  the full independent matrix; this receipt does not sign it.

---

## 6. Receipts

| Run | Result | Log |
|---|---|---|
| Hosted run 39389009969 | success at `bf1b514`; 2 jobs | GitHub run |
| Base `test_fleet_next_install` | 99 tests, 18F + 35E | `r6-rcv2-next-install-red.log` |
| #330 preflight | 28 OK | `r6-330-test_fleet_next_dual_stack_preflight.log` |
| #330 install | 100 OK | `r6-330-test_fleet_next_install.log` |
| #330 runtime create policy | 26 OK | `r6-330-test_fleet_runtime_create_policy.log` |
| #330 create session runtime | 13 OK | `r6-330-test_fleet_create_session_runtime.log` |
| #330 canary start | 70 OK | `r6-330-test_fleet_next_canary_start.log` |
| #330 refresh stage | 29 OK | `r6-330-test_fleet_v1_next_refresh_stage.log` |
| #330 stage unit receipt | 1 OK | `r6-330-test_fleet_v1_next_stage_unit_receipt.log` |
| #330 tailscale gate | 58 OK | `r6-330-test_fleet_v1_next_tailscale_gate.log` |
| Independent boundary probe | 32/32 | `probe-r6-fleet-next-gate.log` |

All local runs used explicit files/commands, `timeout`, and the `systemd-run`
1G/no-swap/25% CPU/nice-10 guard. No real systemctl/install/OAuth/network
operation, no production data.

---

## 7. Handback

1. **#330**: LOCAL + HOSTED_CI PASS at the exact SHA; no authority-boundary
   relaxation found. Keep it a Draft; it is not a V1 signature.
2. **#256/#244**: record the P0 naming fix and the boundary probe; REAL
   canary/production/RC-v3 remain NOT_RUN/PENDING.
3. **I1**: RC-v3 must include #330 and rerun the full matrix; no QA_SIGNED
   before that.

## 8. What this does not claim

- No REAL_NEXT_CANARY, no production, no real OAuth/token/host/systemd.
- No full RC-v3 or T1–T12 acceptance.
- No V1 release sign-off.
