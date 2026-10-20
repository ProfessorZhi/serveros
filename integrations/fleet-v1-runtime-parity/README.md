# R1 #248 · Runtime and Session parity (SOURCE_FIXTURE)

本目录是 Fleet V1 campaign #244 / R1 #248 的**源码级、离线**映射与适配 fixture。
它回答一个问题：给定一条 observation，把 device × OS principal ×
credential-instance reference × family × transport × runtime tag × project ×
requested/resolved/effective model + permissionMode × Session lifecycle
投影成一条**可审计、可拒绝、可显示 UNKNOWN** 的记录。

> 证据级别：`SOURCE_FIXTURE`。这里没有真实 Runtime、没有 provider send、没有
> model call、没有 credential/transcript/argv/environment 读取、没有网络或生产变更。
> 它不是的验收证据，也不是 LIVE。

## 交付物

| 文件 | 作用 |
| --- | --- |
| `contract.yaml` | 闭集词表、拒绝码、grant 要求、capability matrix（每格标注 pinned source） |
| `parity.py` | 纯投影：无 IO、无时钟、无 provider import；只做映射、拒绝与 UNKNOWN |
| `observed.py` | observed-vs-declared 纯投影：五个观测状态按 surface 分开，不合并 |
| `observed/tokyo-2026-10-18.json` | 2026-10-18 Tokyo 真实只读快照（已脱敏） |
| `observed/tri-node-2026-10-18.json` | Tokyo / Dell A6000 ref / Windows 三节点真实只读矩阵（已脱敏） |
| `OBSERVED-2026-10-18.md` | Tokyo 真实只读观测报告、矩阵、脱敏与外部批准清单 |
| `TRINODE-2026-10-18.md` | 三节点 capability/entitlement 矩阵与外部批准清单 |
| `fixtures/positive.json` | 4 个正向 profile：Tokyo/vince Claude+Codex、Tokyo/vince Codex app-server、Dell/fjd Claude |
| `fixtures/negative.json` | 14 个负向 profile：principal、installed CLI、grant、stale session、unbound provider、NEEDS_LOGIN、secret-shaped ref、runtime tag/family 轴、project、session ref、adapter absent、readiness absent |
| `tests/test_fleet_v1_runtime_parity.py` | 52 个断言/参数化用例 |

## 两个不变量

1. **`effective` 绝不从 `requested` / `resolved` / default label 推断。**
   只有 observation 明确给出 provider-response 来源字段且 provenance 为 `FACT`
   时才接受；model 与 permissionMode **逐字段独立**判定。否则为 `null` +
   `UNKNOWN`，并记录 `rejected`。这与 F1 的 create receipt 语义一致：
   `effective: null` 表示 UNKNOWN，不表示“用了 resolved 的值”。
2. **session ref 中段是 runtime tag，不是 family，也不是 agent-runtime id。**
   例如 `claude` / `claude-primary` / `claude-fjd-primary` / `codex-primary`；
   `agent-runtime:claude-code` 不是 tag。tag 由 family + OS account + credential
   profile 推导；声明与推导不一致即拒绝。

## Capability matrix（源码实际支持）

| family | transport | read | create | send | get_run | resume | interrupt |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Claude Code (`claude`) | CloudCLI MCP HTTP | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | **UNSUPPORTED** | SUPPORTED |
| Codex (`codex`) | CloudCLI MCP HTTP | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | **UNSUPPORTED** | SUPPORTED |
| Codex (`codex`) | Codex app-server | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED | SUPPORTED |
| Claude Code (`claude`) | Codex app-server | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED |
| Claude Code (`claude`) | claude-code-native | **UNKNOWN** | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN |
| Codex (`codex`) | claude-code-native | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED |

依据（pinned `serveros main 4a3162…`）：

- `fleet_mcp/cloudcli_native.py#NATIVE_TOOLS`：22 个原生 verb 中有
  `sessions_list` / `session_get` / `session_read` / `run_get` /
  `session_create` / `session_send` / `session_interrupt`；**没有
  `session_resume`**。`session_start` 启动 resident host，不是 resume。
- `mcp/codex_app_server_provider.py#actions`：`session.create` / `inspect` /
  `send` / `result` / `resume` / `interrupt` SUPPORTED，`session.stop`
  UNSUPPORTED；resume 是 `thread/resume`，interrupt 是 turn-scoped 的
  `turn/interrupt`。
- pinned source **没有 Claude Code provider adapter**。
  `integrations/fleet-cc-dev-plugin` 是 client plugin，不是 Session provider；
  因此 CC native lifecycle 是 `UNKNOWN` + `PROVIDER_ADAPTER_ABSENT` blocker，
  不是被推断出来的 SUPPORTED。

## 真实只读观测（2026-10-18）

见 [`OBSERVED-2026-10-18.md`](OBSERVED-2026-10-18.md)。一次有界的只读快照，
证据级别 `REAL_READ_ONLY`：

- Tokyo registry 实际只绑定 `agent-runtime:claude-code`。
- Claude Code CLI `2.1.272` 安装、binding `BOUND`，但**没有直接 provider 实例**；
  只有 BotMux dashboard adapter：session metadata `READ_AUTHORIZED`，
  agent inventory `NEEDS_LOGIN`。
- Codex CLI `0.160.0` 安装，`provider:codex-app-server` 在线且 session metadata
  `READ_AUTHORIZED`；2 个既有 session 的 model `gpt-6-astra` 为 `FACT` readback。
  **但 registry 未绑定 codex → new work `REFUSED` / `PROVIDER_UNBOUND`**，无 fallback。
- effective `permissionMode` 两侧均 `UNKNOWN`；没有把 config/sandbox 当作 effective。
- `CONFORMANT` 只是结构性结论；`behavioral.state = NOT_RUN`，`live = false`。

既有 session list 不等于新的 Claude Code / Codex 账号 entitlement。

### 三节点只读矩阵（2026-10-18）

见 [`TRINODE-2026-10-18.md`](TRINODE-2026-10-18.md)：

- Tokyo：`ENROLLED_REACHABLE`；registry 只绑定 claude-code；BotMux session
  metadata `READ_AUTHORIZED`、agent inventory `NEEDS_LOGIN`；Codex provider
  `READ_AUTHORIZED` 但 **UNBOUND**；Codex effective model `gpt-6-astra` 为 FACT，
  effective permissionMode 两侧 `UNKNOWN`。
- Windows `device:work-fj`：`ENROLLED_UNREACHABLE` / `NODE_RUNTIME_UNAVAILABLE`；
  只有 claude-code + codex 声明，无 provider inventory、无 session、无 grant。
- Dell A6000 `device:llm-lab-dell`：**`DEVICE_NOT_ENROLLED`**（UnknownDevice）；
  不 fallback 到任何其他设备；既有 A6000 只读 terminal proof 不是 lifecycle test。
- Fleet MCP `127.0.0.1:3401`（`serveros-fleet-mcp/0.1.0`）无 token
  `initialize` → **401 `invalid_token`**；`create_allow` / `session_create`
  三节点均 `GRANT_UNKNOWN`，未读取 token 或 raw policy。
- Lifecycle verb 全部是 `DECLARED_ONLY` / `UNSUPPORTED` / `UNKNOWN`，
  没有 `OBSERVED`；实际 lifecycle 仍 `BLOCKED_BY_OWNER`。

## 拒绝与 UNKNOWN

- 拒绝码是闭集（`contract.yaml#refusal_codes`）：principal/credential 不匹配、
  family/tag/runtime 轴不一致、provider 未绑定、project 未绑定、CLI 未安装、
  NEEDS_LOGIN、grant 缺失、session stale / freshness unknown / ref 不匹配、
  secret-shaped credential ref。
- `UNKNOWN` 不是拒绝，也不是“通过”：readiness 未观测 → dispatch `UNKNOWN`；
  CloudCLI 设备 tools/list 未读取 → support `UNKNOWN`；CC native → support
  `UNKNOWN` + blocker。
- 没有 fallback：`fallback` 字段恒为 `null`。未绑定 provider 不会改试另一个
  family / account / model。
- `PRECONDITIONS_MET` **不是授权**。输出里 `authorization` 明确写着
  `NOT_DECIDED: admission remains with Agent Control`。

## 负向覆盖（`fixtures/negative.json`）

| case | 期望 |
| --- | --- |
| `principal-mismatch` | `PRINCIPAL_MISMATCH`，全部 REFUSED |
| `missing-installed-cli` | `PROVIDER_NOT_INSTALLED`，全部 REFUSED |
| `missing-grant` | `GRANT_MISSING`，全部 REFUSED |
| `stale-session` | `SESSION_STALE`；会话寻址 verb REFUSED，create 仍可评估 |
| `unbound-provider` | `PROVIDER_UNBOUND`，无 fallback |
| `needs-login` | `NEEDS_LOGIN`，全部 REFUSED |
| `effective-unproven` | effective 保持 `null` + `UNKNOWN`，`rejected=[model, permissionMode]` |
| `secret-shaped-credential-ref` | `CREDENTIAL_REF_INVALID`，secret 不回显 |
| `runtime-tag-axis` / `family-agent-runtime-id` | `RUNTIME_TAG_MISMATCH` / `FAMILY_INVALID` |
| `project-unbound` | `PROJECT_UNBOUND` |
| `session-ref-mismatch` | `SESSION_REF_MISMATCH`；create 仍可评估 |
| `claude-native-adapter-absent` | `PROVIDER_ADAPTER_ABSENT`，support/dispatch 全 UNKNOWN |
| `readiness-absent` | readiness `UNKNOWN`，无拒绝，dispatch UNKNOWN |

## 运行

从本 worktree 根目录，显式测试文件 + 受限执行：

```sh
PYTHONDONTWRITEBYTECODE=1 \
flock -n /tmp/serveros-fleet-v1-runtime-parity.lock \
  systemd-run --user --scope -q \
    -p MemoryMax=1G -p MemorySwapMax=0 -p CPUQuota=25% -- \
  timeout 600 /data/home/vince/toolchains/serveros-test/bin/pytest \
    tests/test_fleet_v1_runtime_parity.py -q -p no:cacheprovider
```

本机实测：RED = `FileNotFoundError: parity.py`，exit 2；GREEN = 52 passed，exit 0。

## 非目标 / 未验证

- 不是第二个 Session owner：没有 create/send/interrupt 实现，不 import
  `fleet_mcp` / `mcp`。
- 不是 authorizer、不是 registry、不是 store。
- **没有真实设备/provider 证据**：Dell/fjd、Windows 未接触；CC native 适配器
  不存在；CloudCLI 设备 tools/list 未读取。真实 Runtime 验收仍是
  `BLOCKED_PROVIDER_SOURCE` / `NOT_RUN`。
- 不包含 S2 durable effects、G1 ability projection、F1 Fleet MCP、P1 plugin 或
  I1 integration 的改动。

## Pinned refs

- `serveros` main `4a3162864911420309665e92ec38fb68e9ef76ec`
- S1 #241 `d0754edfd65a490213a40b3033a70a43b0a4a187`
- F1 #243 `4a5d0a06f2452424972d66399b7c2b60dc8d03a1`
- CC local plugin #230 `a934beece13af429808f04c1bd5b887e9c19ae91`
- S2 #252 `f2b89e1275a0fdc0b460be0f8c1b82369c75686f`
