# R1 #248 · 三设备 Runtime Session 授权矩阵（2026-10-20）

> **证据级别**：设备/provider/session 读为 `REAL_READ_ONLY`；source 行为
> `SOURCE_READ`；所有 lifecycle mutation 与 model call 为 `NOT_RUN`。
> 本文只写经工具回读或源码核验的事实。`PRECONDITIONS_MET` 不是授权；
> admission 仍在 Agent Control。
>
> 快照窗口：`2026-10-20T04:55:42Z` – `2026-10-20T05:00:00Z`（UTC）。
> 原始快照：`observed/tokyo-2026-10-20.json`。fixture：
> `fixtures/authorization-2026-10-20.json`。投影：`authorization.py`。

## 1. 结论摘要

1. **Tokyo 的 registry 只声明 `claude-code`。** `runtime_bindings` 与 `agents`
   都只有 `agent-runtime:claude-code`。Codex 的安装、provider RUNNING、3 个
   真实 thread 都不改变这个 runtime gate。
2. **Codex 的 ServerOS 读路径在 runtime 步被拒。**
   `agent.session.inspect(runtime=agent-runtime:codex)` →
   `AGENT_RUNTIME_UNAVAILABLE`，detail 为
   `device:tokyo-shared-dev-01 does not declare the codex runtime; it declares claude-code`。
   该拒绝发生在 provider support 之前。
3. **Claude Code 的 ServerOS 读路径在 support 步被拒。**
   `agent.session.inspect(runtime=agent-runtime:claude-code)` →
   `ACTION_UNSUPPORTED`，detail 为
   `session.inspect is not in the provider's action_support table`。
   `BotMuxProvider` 没有 public `actions()`，resolver 读到空实例表；
   per-session 的 `action_support` 不被 resolver 读取。
4. **两个 provider 的 execution readiness 都是 `UNKNOWN` /
   `EXECUTION_UNVERIFIED`。** source 的 `SessionService._preflight` 只拒绝
   KNOWN NEGATIVE；但 target resolver 把未建立的 readiness step 报成整体
   `UNKNOWN`，router 不 dispatch `UNKNOWN` target。mutation 因此到不了 admission。
5. **ServerOS Agent MCP 故意没有 `agent.session.interrupt`。**
   `mcp/agent_mcp.py` 只暴露 create/send/resume/stop/handoff；
   `agent.session.stop` 是 end verb。Codex 的 `turn/interrupt` 只停当前 turn。
6. **CloudCLI 是独立 control plane。** 本机 CloudCLI 1.39.0 在
   `127.0.0.1:3011` 运行；工具面有 `sessions_list`/`session_get`/
   `session_read`/`run_get`/`session_create`/`session_send`/
   `session_interrupt`/`session_close`，**没有 `session_resume`**。
   它的 bearer token 由 MCP client 持有，不是 ServerOS `context_ref`/grant。
7. **CloudCLI `run_get` 有可复现的协议缺陷。** schema 声明参数 `run`，
   server 要求 `runId`；用 `run` 调用得到 `"runId" is required`；用
   `session_get` 返回的 active host lease run id 调用 `runId` 得到
   `This runId was never issued.`。get_run 因此未端到端成立。
8. **CC-first 是 `PARTIAL`。** 可读的是 direct inventory
   （`agent.provider.list` / `agent.session.list`）与 CloudCLI 的
   `sessions_list` / `session_get`。ServerOS routed Claude read 已在 support
   步被拒；lifecycle 仍 `NOT_RUN`。
9. **Full V1 Codex 是 `BLOCKED`。** 缺 registry 声明、缺 context_ref/grant、
   readiness UNKNOWN、无真实 create/send/get_run/resume/interrupt。
10. **三设备中只有 Tokyo 可读。** Windows 两台 enrolled 但
    `NODE_RUNTIME_UNAVAILABLE`；Dell A6000 的 live ref 是 `UnknownDevice`，
    只在 canonical 的未提交 `fleet/registry.yaml` 工作树中出现。

## 2. 设备矩阵

| 设备 | live enrollment | reachability | node runtime | declared runtimes | live provider inventory | OS principal | 结论 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `device:tokyo-shared-dev-01` | enrolled | `KNOWN_ONLINE`（local-loopback） | `ABSENT`（无 connector；本地 provider 仍可读） | `agent-runtime:claude-code` only | BotMux + Codex app-server | `vince`（进程 owner） | CC-first `PARTIAL` |
| `device:work-fj`（FJPCOS） | enrolled | `UNKNOWN` / `NOT_MEASURED` | `ABSENT` / `NODE_RUNTIME_UNAVAILABLE` | claude-code + codex（仅声明） | `UNKNOWN`（transport 未返回 inventory） | `UNKNOWN` | Windows `BLOCKED` |
| `device:personal-dell`（DellPCOS） | enrolled | `UNKNOWN` / `NOT_MEASURED` | `ABSENT` / `NODE_RUNTIME_UNAVAILABLE` | claude-code + codex（仅声明） | `UNKNOWN` | `UNKNOWN` | Windows `BLOCKED` |
| `device:llm-lab-dell`（Dell A6000） | **NOT_ENROLLED**（live `UnknownDevice`） | `INVALID_CONTEXT` | `UNKNOWN` | 仅在**未提交** registry 工作树：claude-code only | 无 | `UNKNOWN` | Dell `BLOCKED` |
| `device:server-beijing` | enrolled | `UNKNOWN` / `NOT_MEASURED` | `ABSENT` | claude-code + codex | `UNKNOWN` | `UNKNOWN` | 三节点范围外 |

### 2.1 Dell A6000 的 authored-vs-live 分离

canonical `src/serveros` 的**未提交** `fleet/registry.yaml` 工作树新增了：

```yaml
device_ref: device:llm-lab-dell
display_name: Dell-LLM-Lab
platform: linux
os_family: linux-ubuntu-26.04
ownership: company
roles: [gpu-compute-worker, development-worker]
environment_profile: linux-gpu-compute
agents: [claude-code]
runtime_bindings:
  - runtime_ref: agent-runtime:claude-code
    declared_at: 2026-10-12
enrollment: enrolled
```

但 live control plane 对同一 ref 返回
`UnknownDevice: no enrolled device with id 'llm-lab-dell'`。因此：

- 不能把该行当作 live enrollment；
- 不能把该行当作 Full V1 Codex 节点（它只声明 claude-code）；
- 不能 fallback 到 `device:personal-dell` 或任何其他 Windows 设备。

## 3. 四个面的逐 verb 矩阵

### 3.1 ServerOS Agent MCP（admission-controlled）

| verb | 工具 | Tokyo Claude Code (BotMux) | Tokyo Codex app-server | 说明 |
| --- | --- | --- | --- | --- |
| read | `agent.session.inspect` / `agent.session.result` / `agent.session.list` | routed inspect: **REFUSED** `ACTION_UNSUPPORTED`；direct list: **可读** | routed inspect: **REFUSED** `AGENT_RUNTIME_UNAVAILABLE`；direct list: **可读** | BotMux 实例表为空；Codex 未声明 |
| create | `agent.session.create` | **不可达**（support/readiness/grant） | **不可达**（runtime gate） | BotMux create 方法需 dashboard auth，且拒绝任意 workspace |
| send | `agent.session.send` | **不可达** | **不可达** | 同上；未执行 |
| get_run | `agent.session.result` | **不可达** | **不可达** | 无独立 get_run；result 是异步 turn result |
| resume | `agent.session.resume` | **不可达** | **不可达** | BotMux resume 在 Dashboard API 不存在；Codex 在 runtime gate |
| interrupt | **无工具** | **REFUSED** `ACTION_UNSUPPORTED`（tool absent） | 同左 | MCP 故意不暴露；provider 层有 turn/interrupt |
| end | `agent.session.stop` | **不可达**（support unpublished） | **REFUSED** `ACTION_UNSUPPORTED`（provider stop UNSUPPORTED） | BotMux close 是 whole-session，不是 turn interrupt |

### 3.2 CloudCLI MCP（独立 control plane）

| verb | 工具 | 观测 | 是否执行 |
| --- | --- | --- | --- |
| read | `sessions_list` / `session_get` / `session_read` | **KNOWN**：26 projects、146 sessions、self session `provider=claude`、`hostState=busy`、`pid=null` | 是（只读） |
| create | `session_create` | 工具存在；真实 model request | **否** `BLOCKED_OWNER_CONSENT` |
| send | `session_send` | 工具存在 | **否** `BLOCKED_OWNER_CONSENT` |
| get_run | `run_get` | **协议不匹配**：`run` vs `runId`；`This runId was never issued.` | **否** `UNKNOWN` |
| resume | — | **不存在**；`session_start` 是启动 resident host，不是 resume | **否** `ACTION_UNSUPPORTED` |
| interrupt | `session_interrupt` | 工具存在；语义是当前 run | **否** `BLOCKED_OWNER_CONSENT` |
| end | `session_close` | 工具存在 | **否** `BLOCKED_OWNER_CONSENT` |

CloudCLI 的 bearer token 是 MCP client 的环境授权，不是 ServerOS 的
`context_ref`、grant 或 approval。把 CloudCLI 的成功当成 Agent Control 的
admission 会制造第二套授权账本。

### 3.3 Codex app-server（provider-native）

| verb | action_support | 观测 |
| --- | --- | --- |
| read | `SUPPORTED` | `thread/list` 返回 3 个 thread；model `gpt-6-astra` 为 `FACT`（source `codex:thread/list`） |
| create | `SUPPORTED` | 未执行；`thread/start` + first turn |
| send | `SUPPORTED` | 未执行；`turn/start` |
| get_run | `SUPPORTED` | 未执行；result 来自 turn completion / thread/read |
| resume | `SUPPORTED` | 未执行；`thread/resume` |
| interrupt | `SUPPORTED` | 未执行；`turn/interrupt` 只停当前 turn |
| end | `UNSUPPORTED` | app-server 无 session-stop；archive 是 hide，delete 销毁历史 |

### 3.4 BotMux（Claude Code 的 Dashboard adapter）

| verb | per-session action_support | 观测 |
| --- | --- | --- |
| read | `SUPPORTED`（inspect/result） | `agent.session.list` 可读 1 个 IDLE session；model/ permissionMode 均 UNKNOWN |
| create | **instance 表未声明** | 方法存在，但设置 workspace 时 `BotMuxUnsupported`；无 dashboard token 时 `BotMuxCredentialMissing` |
| send | `SUPPORTED` | 未执行；`/api/trigger` 需要 dashboard auth |
| get_run | `SUPPORTED` | 未执行；`/trigger-result` 需要 dashboard auth |
| resume | `UNSUPPORTED` | Dashboard API 无 resume；send 到 dormant session 是 cold-resume |
| interrupt | `UNSUPPORTED` | BotMux close 终止 whole session，不是 turn interrupt |
| end | `SUPPORTED` | 未执行；`/close` |

## 4. 授权链（source 顺序）

| # | step | source | 拒绝条件 | Tokyo 观测 |
| --- | --- | --- | --- | --- |
| 1 | device | `abilities/target.py#_step_device` | 无 enrollment / unreachable / no node runtime | Tokyo 本地通过；Windows/Dell 拒 |
| 2 | runtime declared | `abilities/target.py#_step_agent_runtime` | runtime 不在 `device.agents` | Codex `AGENT_RUNTIME_UNAVAILABLE` |
| 3 | provider instance | `abilities/target.py#_step_provider_instance` | 无 instance | 两 provider 均有 instance |
| 4 | adapter | `abilities/target.py#_step_adapter` | runtime 不能消费 provider kind | 通过 |
| 5 | support | `abilities/target.py#_step_support` + `fleet/agent_control_router.py#provider_action_support` | 实例表 UNSUPPORTED/UNKNOWN/缺失 | BotMux 缺失 → `ACTION_UNSUPPORTED` |
| 6 | readiness | `abilities/target.py#_step_readiness` | KNOWN NEGATIVE → `PROVIDER_UNAVAILABLE`；UNKNOWN → 整体 UNKNOWN | 两 provider `EXECUTION_UNVERIFIED` |
| 7 | admission | `runtime/agent-session-contract.yaml#admission` | read_only ALLOW；delegation 需 context_ref + grant；human_gated 需 actor + approval | mutation 未到达 |
| 8 | thread binding | `runtime/coordination.py` | create 需 role/item；send/resume 需 binding | 未到达 |
| 9 | dispatch | `fleet/agent_control_router.py#route` | status 不是 AVAILABLE/DEGRADED 则不 dispatch | 未到达 |

### 4.1 三类“不行”

| decision | 含义 | 修复者 | 例 |
| --- | --- | --- | --- |
| `REFUSED` | source 用闭集 reason 明确拒绝 | 改请求/provider/registry | `AGENT_RUNTIME_UNAVAILABLE`、`ACTION_UNSUPPORTED` |
| `UNKNOWN` | 没有拒绝，但事实未建立 | 等待/补观测/补 grant | readiness UNKNOWN、grant UNKNOWN、`run_get` 字段不匹配 |
| `BLOCKED` | owner/consent/enrollment/adapter 边界 | 人 | `BLOCKED_OWNER_LOGIN`、`BLOCKED_OWNER_CONSENT`、`BLOCKED_DEVICE_NOT_ENROLLED` |

### 4.2 blocked 码

| code | 触发 | 当前实例 |
| --- | --- | --- |
| `BLOCKED_OWNER_LOGIN` | provider 进程缺 BotMux dashboard credential | agent inventory |
| `BLOCKED_OWNER_CONSENT` | 真实 mutation/model request 未授权 | CloudCLI create/send/interrupt/close；direct provider mutation |
| `BLOCKED_GRANT_UNKNOWN` | 无可读 grant | ServerOS mutation |
| `BLOCKED_RUNTIME_NOT_DECLARED` | registry 未声明 runtime | Tokyo Codex |
| `BLOCKED_PROVIDER_SUPPORT_UNPUBLISHED` | provider 无实例级 action_support | BotMux 所有 routed verb |
| `BLOCKED_PROVIDER_ADAPTER_ABSENT` | 无 provider adapter | Claude Code native |
| `BLOCKED_EXECUTION_UNVERIFIED` | readiness UNKNOWN | 两 provider |
| `BLOCKED_MCP_TOOL_ABSENT` | surface 无该工具 | ServerOS interrupt；CloudCLI resume |
| `BLOCKED_PROTOCOL_FIELD_MISMATCH` | schema 字段与 server 不一致 | CloudCLI `run_get` |
| `BLOCKED_NODE_RUNTIME_UNAVAILABLE` | 远端无 node runtime | work-fj / personal-dell |
| `BLOCKED_DEVICE_NOT_ENROLLED` | live 未知 device ref | llm-lab-dell |
| `BLOCKED_AUTHORED_NOT_LIVE` | 只在未提交 registry 工作树 | llm-lab-dell authored row |

## 5. CC-first 与 Full V1 Codex

### 5.1 CC-first（当前 `PARTIAL`）

可用：

- `agent.provider.list` / `agent.session.list`（direct，不经 resolver）；
- CloudCLI `projects_list` / `sessions_list` / `session_get`；
- Claude Code CLI `2.1.272` 已登录（`loggedIn: true`、`authMethod: oauth_token`、
  `apiProvider: firstParty`），credential owner 是 `vince`。

不可用：

- ServerOS routed Claude read（`agent.session.inspect`）在 support 步被拒；
- 任何 ServerOS mutation；
- CloudCLI mutation（未授权，未执行）。

要把 CC-first 从 `PARTIAL` 提升为可用 lifecycle，至少需要：

1. BotMux 发布 public `actions()`，或 resolver 改为读取 per-session support；
2. 一个已知正向的 execution readiness（或 owner 明确豁免）；
3. 一个可信 `context_ref` 与覆盖 verb 的 grant；
4. 一次真实的 create/send/get_run/end（在隔离项目）。

### 5.2 Full V1 Codex（当前 `BLOCKED`）

前提：

1. Tokyo 的 `runtime_bindings` **和** `agents` 都声明 `agent-runtime:codex`；
2. Codex provider 的实例级 action_support 完整；
3. execution readiness 已知正向，或 owner 明确豁免；
4. 可信 `context_ref` + grant；
5. 真实 create/send/read/get_run/resume/interrupt 一次。

当前缺口：

- registry 只声明 claude-code；
- resolver 在 runtime 步拒绝，早于 provider support；
- readiness `EXECUTION_UNVERIFIED`；
- 无 context_ref/grant；
- 无任何 lifecycle 执行。

Codex 的真实证据只有：CLI `0.160.0`、provider `RUNNING`、3 个 thread 的
model `gpt-6-astra`（`FACT`）。这不是 Full V1 验收。

## 6. 源级修正（对 prior R1 材料）

| # | 事实 | 影响 |
| --- | --- | --- |
| 1 | `fleet_mcp/cloudcli_native.py` 在 `771c81c` 与 `65ead6d` 都不存在 | prior R1 的 CloudCLI 能力表引用了一个不存在的 source；不能据此声明 CloudCLI SUPPORTED |
| 2 | `mcp/codex_app_server_provider.py` 的 `MEASURED_AGAINST` 是 `codex-cli 0.153.4`（2026-09-14） | live handshake 是 `0.160.0`；版本已漂移，协议方法未在本轮重测 |
| 3 | `abilities/target.py#_step_agent_runtime` 读 `device.agents` | `abilities/runtime_binding.py` 说 `runtime_bindings` 是 authoritative、`agents` 是 projection；控制路径读的是 projection |
| 4 | `BotMuxProvider` 只有 `_actions()`，没有 public `actions()` | resolver 的 `provider_action_support` 返回 `{}`；per-session support 被忽略 |
| 5 | `mcp/agent_mcp.py` 没有 `agent.session.interrupt` | provider 层 turn/interrupt 不等于 MCP 工具 |
| 6 | `run_get` schema 参数 `run`，server 要求 `runId` | 工具不能按声明使用；active host lease run id 也不可解析 |
| 7 | Tokyo registry 注释写 codex “not installed here and never has been” | 与 `codex-cli 0.160.0` + `provider:codex-app-server` 矛盾 |
| 8 | 未提交的 `fleet/registry.yaml` 新增 `llm-lab-dell` | live daemon 仍返回 `UnknownDevice`；authored 不等于 live |
| 9 | `SessionService._preflight` 只拒绝 KNOWN NEGATIVE；resolver 把 readiness UNKNOWN 报成整体 UNKNOWN | 两层对同一观测使用不同词；router 因此不 dispatch |

## 7. Owner 批准清单

| ID | owner | 需要的决定/批准 | 不批准的当前结果 |
| --- | --- | --- | --- |
| A1 | owner / registry | 是否在 Tokyo 同时声明 `agent-runtime:codex` 到 `runtime_bindings` 与 `agents` | Codex 永远在 runtime gate 被拒 |
| A2 | owner / provider | 是否让 BotMux dashboard credential 对 provider 进程可用（不暴露给 R1） | agent inventory 永远 `NEEDS_LOGIN` |
| A3 | source owner | 是否给 `BotMuxProvider` 增加 public `actions()`，或让 resolver 读 per-session support | 所有 routed BotMux verb 永远 `ACTION_UNSUPPORTED` |
| A4 | owner / provider | readiness 如何变成已知正向：provider readiness surface，或一次有界 bootstrap turn | mutation 永远不到 admission |
| A5 | owner | 是否提供可信 `context_ref` 与覆盖 verb 的 grant，用于有界真实 lifecycle | 所有 mutation `BLOCKED_GRANT_UNKNOWN` |
| A6 | owner | 是否授权一次 CloudCLI 测试会话：`session_create` + 固定 echo + `session_get` + `session_close` | CloudCLI mutation 永远 `BLOCKED_OWNER_CONSENT` |
| A7 | CloudCLI/bridge owner | 修复 `run_get` 的 `run`/`runId` 字段与 `session_get` run id 解析 | get_run 永远 UNKNOWN |
| A8 | owner / enrollment | 给出 Dell A6000 的 live device ref 或先完成 enrollment | Dell 永远 `BLOCKED_DEVICE_NOT_ENROLLED` |
| A9 | docs owner | 是否修正 Tokyo registry 的过期 codex 注释与 `MEASURED_AGAINST` 版本 | authored 文档继续与测量冲突 |

## 8. 明确未声称

- 没有真实 create / send / get_run / resume / interrupt / end。
- 没有 model call、credential/token/transcript/environment 读取。
- 没有 grant、registry、service 或 production 变更。
- 没有 Windows 或 Dell 执行。
- 没有把 declared binding 当作 installed 或 runnable。
- 没有把未提交的 `llm-lab-dell` registry row 当作 live。
- 没有把 `PRECONDITIONS_MET` 当作授权；admission 仍在 Agent Control。

## 9. 验证

```sh
cd /data/home/vince/workspace/worktrees/serveros/r1-provider-parity-20261020

PYTHONDONTWRITEBYTECODE=1 nice -n 10 \
  flock -n /tmp/serveros-fleet-v1-runtime.lock \
  systemd-run --user --scope -q \
    -p MemoryMax=1G -p MemorySwapMax=0 -p CPUQuota=25% -- \
  timeout 120 /data/home/vince/toolchains/serveros-test/bin/pytest \
    tests/test_fleet_v1_runtime_authorization.py -q -p no:cacheprovider
```

实测：`69 passed in 0.69s`，exit 0。与 #257 的
`tests/test_fleet_v1_runtime_parity.py` 合跑：`121 passed in 1.30s`，exit 0。

## 10. Pinned refs

- authoritative `ProfessorZhi/serveros` main `771c81c`
- workspace canonical head `665067e`
- prior R1 branch head `65ead6d`（merge base `665067e`；其 body 的 `4a3162`
  不是该分支的 base）
- Tokyo `device:tokyo-shared-dev-01`；Windows `device:work-fj` /
  `device:personal-dell`；Dell `device:llm-lab-dell`（live `UnknownDevice`）
- Claude Code `2.1.272`；Codex `0.160.0`；CloudCLI `1.39.0`
