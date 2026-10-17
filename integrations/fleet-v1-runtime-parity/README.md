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
| `fixtures/positive.json` | 4 个正向 profile：Tokyo/vince Claude+Codex、Tokyo/vince Codex app-server、Dell/fjd Claude |
| `fixtures/negative.json` | 14 个负向 profile：principal、installed CLI、grant、stale session、unbound provider、NEEDS_LOGIN、secret-shaped ref、runtime tag/family 轴、project、session ref、adapter absent、readiness absent |
| `tests/test_fleet_v1_runtime_parity.py` | 34 个断言/参数化用例 |

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

本机实测：RED = `FileNotFoundError: parity.py`，exit 2；GREEN = 34 passed，exit 0。

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
