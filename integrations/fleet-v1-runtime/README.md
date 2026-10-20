# R1 #248 · Runtime Session authorization matrix (2026-10-20)

本目录是 Fleet V1 campaign #244 / R1 #248 的 **2026-10-20 授权矩阵与兼容性 fixture**。
它回答一个比“provider 支持哪些 verb”更窄的问题：

> 给定设备、OS principal、family、transport、runtime binding、provider support、
> readiness、grant 与 context，某个 verb 现在到底被谁拒绝？拒绝是 source 的
> `REFUSED`、未建立的 `UNKNOWN`，还是 owner/授权边界的 `BLOCKED`？

三类“不行”必须分开，因为修复人不同：

- `REFUSED` — source 用自己闭集里的 reason 明确拒绝；按 source 的步骤顺序，第一个拒绝获胜。
- `UNKNOWN` — 没有任何一步拒绝，但某个决策所需的事实从未建立（readiness、grant、协议字段）；不是 deny，也不是 allow，且没有 dispatch。
- `BLOCKED` — owner、consent、enrollment 或 adapter 边界在 source 被询问前就停下；这些是 R1 边界码，不是 source reason。

> **证据级别**：观测是 `REAL_READ_ONLY`；source 读是 `SOURCE_READ`；所有
> create/send/resume/interrupt/end 与 model call 都是 `NOT_RUN`。
> `PRECONDITIONS_MET` 不是授权；admission 仍在 Agent Control。

## 与 `fleet-v1-runtime-parity/` 的关系

`../fleet-v1-runtime-parity/` 是 #257 的 **SOURCE_FIXTURE** 能力投影，回答
“pinned source 声称支持什么”。本目录不修改它，也不重复它的 projection。
本目录补的是 **authorization chain**：source 的 runtime → support → readiness
→ admission → thread binding 顺序，以及 2026-10-20 在三设备上实际读到的状态。

## 文件

| 文件 | 作用 |
| --- | --- |
| `contract.yaml` | 2026-10-20 的闭集词表：四个 surface、admission class、source reason、R1 blocked 码、CC-first / Full V1 两档 |
| `authorization.py` | 纯投影：无 IO、无时钟、无 provider import；只做映射、拒绝、UNKNOWN、BLOCKED |
| `fixtures/authorization-2026-10-20.json` | 最小兼容性 fixture：33 个 case，覆盖四个 decision 与全部 12 个 blocked 码 |
| `observed/tokyo-2026-10-20.json` | 2026-10-20 Tokyo 真实只读快照（已脱敏） |
| `OWNER-MATRIX-2026-10-20.md` | 三设备/四面矩阵、CC-first 与 Full V1 Codex 分档、owner 批准清单 |
| `CLIENT-TURN-ID-2026-10-20.md` | S2 #245 交互：client turn id 与 provider readback 的证据矩阵 |
| `client_turn_id.py` | 合成 Codex readback 源；`SCHEMA_ONLY`，无 IO / send |
| `fixtures/client-turn-id-2026-10-20.json` | 16 个 readback case + 7 个 capability case |
| `RECONCILE-SOURCE-2026-10-20.md` | S2 #245 reconcile source 能力矩阵与 FAIL CLOSED 回归 |
| `reconcile_source_gate.py` | 纯 gate；默认不发送/不重试/不自动 settle |
| `fixtures/reconcile-source-2026-10-20.json` | owner-signed observation 负测与 capability matrix |
| `tests/test_fleet_v1_runtime_authorization.py` | 69 个断言/参数化用例，含拒绝路径与纯度守卫 |
| `tests/test_fleet_v1_runtime_client_turn_id.py` | 30 个断言，含 forged id / restart / mismatch |
| `tests/test_fleet_v1_runtime_reconcile_source.py` | 35 个断言，含 forged locator / cross-owner / project / payload / parent |

## 结论摘要（经证据核验）

1. **Tokyo 只声明 `claude-code`。** `fleet/registry.yaml` 的
   `runtime_bindings` / `agents` 都只有 `agent-runtime:claude-code`。
   `codex-cli 0.160.0` 实际安装、`provider:codex-app-server` 实际 RUNNING、
   `codex:thread/list` 实际返回 3 个 thread（model `gpt-6-astra`，provenance
   `FACT`），但这些都不改变 registry 的 runtime gate。
2. **Codex 的 ServerOS 读路径在 runtime 步被拒。**
   `agent.session.inspect(runtime=codex)` 返回
   `AGENT_RUNTIME_UNAVAILABLE`：`device:tokyo-shared-dev-01 does not declare the
   codex runtime; it declares claude-code`。拒绝发生在 provider support 之前，
   因此“provider 支持 inspect”没有机会生效。
3. **Claude Code 的 ServerOS 读路径在 support 步被拒。**
   `agent.session.inspect(runtime=claude-code)` 返回
   `ACTION_UNSUPPORTED`：`session.inspect is not in the provider's
   action_support table`。`BotMuxProvider` 没有 public `actions()`，resolver 的
   `provider_action_support` 读到空表；per-session 的 `action_support` 不被
   resolver 读取。`agent.session.list` 的 per-session 行仍显示 inspect/send/
   result/stop SUPPORTED、resume/interrupt UNSUPPORTED。
4. **两个 provider 的 execution readiness 都是 UNKNOWN。**
   `EXECUTION_UNVERIFIED`（无 readiness endpoint，probed 404）。source 的
   `SessionService._preflight` 只拒绝 KNOWN NEGATIVE；但 resolver 把未建立的
   readiness step 报成整体 `UNKNOWN`，router 不 dispatch `UNKNOWN` target。
   因此即使 runtime/support 通过，mutation 仍不会到达 admission。
5. **ServerOS Agent MCP 没有 `agent.session.interrupt`。**
   `mcp/agent_mcp.py` 明确不暴露它；`agent.session.stop` 是语义上的 end verb。
   Codex provider 的 `turn/interrupt` 只停当前 turn，不等于 end session。
6. **CloudCLI 是另一个 control plane。** 本机 CloudCLI 1.39.0 在
   `127.0.0.1:3011` 运行，工具面包含 `sessions_list`/`session_get`/
   `session_read`/`run_get`/`session_create`/`session_send`/`session_interrupt`/
   `session_close`，**没有 `session_resume`**。它的 bearer token 由 MCP client
   持有，不是 ServerOS 的 `context_ref` 或 grant。`run_get` 还存在字段不匹配：
   schema 声明 `run`，server 要求 `runId`；`session_get` 给出的 active host
   lease run id 被 `run_get` 回答 `This runId was never issued.`。
7. **CC-first 是 PARTIAL，不是可用 lifecycle。** 可读的是
   `agent.provider.list` / `agent.session.list`（direct，不经 resolver）与
   CloudCLI 的 `sessions_list` / `session_get`。ServerOS routed 的 Claude
   read 已在 support 步被拒；mutation 仍 `NOT_RUN`。
8. **Full V1 Codex 是 BLOCKED。** 缺 registry 声明、缺 context_ref/grant、
   readiness UNKNOWN、没有任何真实 create/send/get_run/resume/interrupt。
9. **三设备中只有 Tokyo 可读。** `device:work-fj` 与 `device:personal-dell`
   都是 enrolled 但 `NODE_RUNTIME_UNAVAILABLE`。campaign 的 Dell A6000
   `device:llm-lab-dell` 在 live control plane 是 `UnknownDevice`；它只出现在
   canonical 的 **未提交** `fleet/registry.yaml` 工作树里（且只声明
   `claude-code`），不能当作 live 或 Full V1 Codex 节点。
10. **registry 的 authored 注释已过期。** Tokyo registry 注释写“Codex is not
    installed here and never has been”，但本机 `codex-cli 0.160.0` 与
    `provider:codex-app-server` 均存在；这是“authored registry 不是 measurement”
    的直接例子。

## S2 #245 交互（2026-10-20）

见 [`CLIENT-TURN-ID-2026-10-20.md`](CLIENT-TURN-ID-2026-10-20.md)。

- Codex app-server **send 到已知 thread** 存在 stable v2 schema 级
  `TurnStartParams.clientUserMessageId` 与 `ThreadItem.userMessage.clientId`
  回读；推荐 `thread/turns/list` + `itemsView: "full"`。
- 但该映射**未 live-proven**；production 仍 `BLOCKED_PROVIDER_SOURCE`，直到
  owner 批准一次 bounded echo test。
- 回读是 **positive-only**：匹配 + intent digest 相同 → APPLIED；miss →
  UNKNOWN，绝不 NOT_APPLIED；不释放 retry，不声称 exactly-once。
- BotMux / human-control 是 `RESPONSE_ID_ONLY`；CloudCLI 是 `NO_SOURCE`；
  Codex create 是 `RESPONSE_ID_ONLY`（thread id 在丢失的响应里）。

## S2 #245 reconcile source（2026-10-20）

见 [`RECONCILE-SOURCE-2026-10-20.md`](RECONCILE-SOURCE-2026-10-20.md)。

- Codex app-server **send 到已知 thread** 有 schema 级 pre-send id +
  `userMessage.clientId` 回读；public docs 也描述该回显。
- 但 **owner-signed observation 不存在**：S2 kernel/store/daemon 只有
  principal、writer、intent/locator、`source` provenance，没有签名字段；
  production source 是 `None`。因此 production 仍 `BLOCKED_PROVIDER_SOURCE`。
- 本轮交付 FAIL CLOSED gate 与负测：forged locator、cross-owner、
  cross-project、changed payload、missing parent、cross-device、unsigned/bad
  signature、replay、stale writer、NOT_APPLIED unavailable。
- 默认：**不发送、不重试、不自动 settle**；synthetic HMAC 仅测试用，不能提升为
  owner authority。
- Claude Code CLI 的 `--session-id <uuid>` + `--resume <session-id>` 是真实
  CLI 级 pre-send id，但未接 BotMux provider，标 `CLI_ONLY_NOT_WIRED`。

## 运行

```sh
cd /data/home/vince/workspace/worktrees/serveros/r1-provider-parity-20261020

PYTHONDONTWRITEBYTECODE=1 nice -n 10 \
  flock -n /tmp/serveros-fleet-v1-runtime.lock \
  systemd-run --user --scope -q \
    -p MemoryMax=1G -p MemorySwapMax=0 -p CPUQuota=25% -- \
  timeout 120 /data/home/vince/toolchains/serveros-test/bin/pytest \
    tests/test_fleet_v1_runtime_authorization.py -q -p no:cacheprovider
```

本机实测：`69 passed in 0.69s`，exit 0；与 #257 的
`tests/test_fleet_v1_runtime_parity.py` 合跑 `121 passed in 1.30s`，exit 0。

## 未声称

- 没有真实 create / send / get_run / resume / interrupt / end。
- 没有 model call、credential/token/transcript/environment 读取。
- 没有 grant、registry、service 或 production 变更。
- 没有 Windows 或 Dell 执行；没有把 declared binding 当作 installed 或 runnable。
- 没有把未提交的 `llm-lab-dell` registry row 当作 live。
- 没有把 `PRECONDITIONS_MET` 当作授权；admission 仍在 Agent Control。

## Pinned refs

- authoritative `ProfessorZhi/serveros` main `771c81c`
- workspace canonical head `665067e`
- prior R1 branch head `65ead6d`（基于 `665067e`，不是其 body 写的 `4a3162`）
- Tokyo `device:tokyo-shared-dev-01`；Windows `device:work-fj` / `device:personal-dell`
- Claude Code `2.1.272`；Codex `0.160.0`；CloudCLI `1.39.0`
