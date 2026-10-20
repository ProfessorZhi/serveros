# R1 #248 / S2 #245 · reconcile source capability（2026-10-20）

> **证据级别**：`CODE` + `LOCAL` + `SYNTHETIC_FIXTURE`。
> 没有真实 send / model call / 付费消息；没有读取 token、transcript 或环境值；
> 没有触碰 production Provider、serverosd、FleetNext/Connector、I1 RC-v7 或任何
> 设备服务/凭据/OAuth。Provider CLI 只读取了 `--help` 与本地 schema。

## 1. 结论

**当前 Runtime 没有同时满足“provider 明确支持发送前 client id + 可重新查询”与
“owner-signed observation”两个条件，因此 production 仍是
`BLOCKED_PROVIDER_SOURCE`。**

| 条件 | 状态 | 证据 |
| --- | --- | --- |
| provider 明确支持发送前 client id | **Codex app-server 满足（schema 级）** | stable v2 `TurnStartParams.clientUserMessageId` |
| 可重新查询 | **Codex app-server 满足（分页扫描）** | `ThreadItem.userMessage.clientId`；`thread/turns/list` + `itemsView=full`；`ThreadTurnsListParams` 无 clientId 过滤 |
| public docs 说明回显 | **有第三方客户端文档** | `clientUserMessageId` described as echoed back as the userMessage item's `clientId`；不是 OpenAI 单一 canonical spec |
| owner-signed observation | **不存在** | S2 kernel/store/daemon 只有 principal、writer、intent/locator、`source` provenance；没有签名字段 |
| production observation source | **`None`** | `dispatch_observation_source=None` → `BLOCKED_PROVIDER_SOURCE` |

所以本轮**不做** production readbackAdapter；交付的是严谨的 capability matrix +
FAIL CLOSED 回归小片。合成 HMAC 只用于证明负测路径，不是 owner authority。

## 2. 本机只读 Provider CLI 能力（未读 token，未登录）

| CLI | 只读命令 | 看到的 pre-send id | 看到的 re-query | 结论 |
| --- | --- | --- | --- | --- |
| Codex CLI `0.160.0` | `codex --help`、`codex exec --help`、`codex resume --help`、`codex agents --help`、`codex queue --help`、`codex app-server generate-json-schema` | CLI 无 `clientUserMessageId` 参数；`--json` 是事件输出 | `resume <session-id>` / `exec resume <session-id>` / `queue --thread` 都按 provider session id | `NO_SOURCE`（CLI 层） |
| Codex app-server | 同上 schema | `TurnStartParams.clientUserMessageId` | `thread/turns/list` + `itemsView=full`，扫描 `userMessage.clientId` | `CLIENT_TURN_ID`（schema 级） |
| Claude Code CLI `2.1.272` | `claude --help` | **`--session-id <uuid>`** | **`--resume <session-id>`** | `CLI_ONLY_NOT_WIRED` |
| BotMux `3.31.0` | `botmux --help`、`list`、`resume <id>` | `turnIdempotencyKey`（provider body） | `resume <id>` / Dashboard `/trigger-result?triggerId=` 都按 provider id | `RESPONSE_ID_ONLY` |
| CloudCLI `1.39.0` | `cloudcli --help`、`status`；MCP `session_send`/`run_get` schema | `session_send` 无 client id 参数 | `run_get` 需要 `runId` | `NO_SOURCE` |

关键区分：

- **Claude Code CLI 的 `--session-id <uuid>` 是真正的发送前 client id，且
  `--resume <session-id>` 可重新查询**；但它是 **CLI 层**能力，不是 BotMux
  provider 的路径。BotMux 不把该 id 传给 Claude Code，也不在 Dashboard 中回读它。
  因此 R1 的 provider 结论仍是 `RESPONSE_ID_ONLY`，标 `CLI_ONLY_NOT_WIRED`，
  不能把 CLI 能力写成 provider 能力。
- **Codex app-server 的 `clientUserMessageId` 是 schema 级证据**；`thread/turns/list`
  在部分版本被标为 experimental，且 `ThreadTurnsListParams` 没有 clientId 过滤，
  必须对已知 thread 分页扫描。它不是“按 client id 直接查询”的 API。

## 3. Public docs 证据

- 第三方 Codex 客户端文档把 `clientUserMessageId` 描述为 “echoed back as the
  userMessage item's `clientId`”，与 stable v2 schema 的两个字段一致。
  - `https://www.npmjs.com/package/@happycatlabs/codex-client`
  - `https://github.com/yan5xu/codexloom/blob/main/docs/codex-app-server-protocol.md`
  - `https://mintlify.wiki/openai/codex/api/turns`
- 同一批文档也说明 `thread/turns/list` 在部分版本是 experimental，method/shape
  随 codex-cli 版本变化；本机 `0.160.0` 的 stable v2 schema 含该参数/响应，
  但这不是跨版本保证。
- 这些是**第三方/协议观察**，不是 owner-signed observation，也不能替代真实
  echo test。

## 4. 为什么 owner-signed observation 不存在

S2 现有契约（#299 / #312 / #328）：

- `runtime/dispatch_attempts.py#reconcile_observation` 检查：
  - phase 必须是 `EFFECT_UNKNOWN`；
  - reconciler principal 必须等于记录 principal；
  - Work scope 需要 current writer（owner/epoch/token）；
  - observation 必须通过 `intent_digest` 或 `provider_locator` 绑定到本次 intent；
  - observation 必须带 `source`；
  - `state` 只能是 APPLIED / NOT_APPLIED / UNKNOWN。
- `runtime/agent_control_store.py#reconcile_dispatch_attempt` 在同一个 mutation
  lock 内委托 kernel，并记录 `reconciled_by` principal。
- `runtime/daemon.py#_r_agent_dispatch_reconcile` 禁止请求携带 `observation`、
  `source`、`capability`、`principal`、`writer`、`verifier` 等字段；source 与
  verifier 是 daemon-held 对象。
- 以上是 **principal + writer + provenance + intent binding**，**不是签名**。
  没有 `signature` / `signer` / `key_id` 字段，也没有 production owner key。

因此 `SYNTHETIC_FIXTURE` 的 HMAC 只能证明“如果有一个 owner-signed envelope，
gate 会怎么检查”，不能把测试用户提升为真实签名权威。

## 5. Capability matrix

| adapter | verb | pre-send id | re-query | provider explicit | owner-signed observation | capability | production |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Codex app-server | send，thread 已知 | `clientUserMessageId` | `thread/turns/list` + `itemsView=full` → `userMessage.clientId` | 是（stable schema） | **ABSENT** | `CLIENT_TURN_ID` | `BLOCKED_PROVIDER_SOURCE` |
| Codex app-server | create | `clientUserMessageId` | 无已知 thread | 是 | ABSENT | `RESPONSE_ID_ONLY` | `BLOCKED_PROVIDER_SOURCE` |
| Codex CLI | any | 无 | `resume`/`queue` 按 session id | 否 | ABSENT | `NO_SOURCE` | `BLOCKED_PROVIDER_SOURCE` |
| Claude Code CLI | any | `--session-id <uuid>` | `--resume <session-id>` | 是（CLI） | ABSENT | `CLI_ONLY_NOT_WIRED` | `BLOCKED_PROVIDER_SOURCE` |
| BotMux | send | `turnIdempotencyKey` | `/trigger-result?triggerId=...` | 否 | ABSENT | `RESPONSE_ID_ONLY` | `BLOCKED_PROVIDER_SOURCE` |
| CloudCLI MCP | send | 无 | `run_get` 要 `runId` | 否 | ABSENT | `NO_SOURCE` | `BLOCKED_PROVIDER_SOURCE` |
| human-control | send | `idempotencyKey` | `/api/trigger` → `triggerId` | 否 | ABSENT | `RESPONSE_ID_ONLY` | `BLOCKED_PROVIDER_SOURCE` |

## 6. FAIL CLOSED 回归小片

`reconcile_source_gate.py` 是纯 gate，不发送、不重试、不自动 settle。生产默认：

```
uncertainty → send_allowed=false, retry_allowed=false, settle_allowed=false,
              production_eligible=false, production_decision=BLOCKED_PROVIDER_SOURCE
```

| case | decision | s2_verdict |
| --- | --- | --- |
| `valid-synthetic-signed` | `SYNTHETIC_ACCEPTED_APPLIED` | `SETTLE_APPLIED`（**仅 synthetic**） |
| `production-mode-always-blocked` | `BLOCKED_OWNER_SIGNATURE` | `None` |
| `no-source` / `response-id-only` / `unknown-capability` | `BLOCKED_PROVIDER_SOURCE` | `None` |
| `unsigned-observation` / `bad-signature` | `BLOCKED_OWNER_SIGNATURE` | `None` |
| `phase-settled` | `REFUSED_NOT_UNKNOWN` | `REFUSE_NOT_UNKNOWN` |
| `cross-owner` | `REFUSED_PRINCIPAL` | `REFUSE_PRINCIPAL` |
| `cross-device` | `REFUSED_CROSS_DEVICE` | `REFUSE_UNKNOWN` |
| `cross-project` | `REFUSED_CROSS_PROJECT` | `REFUSE_UNKNOWN` |
| `missing-parent` | `REFUSED_MISSING_PARENT` | `REFUSE_UNKNOWN` |
| `forged-locator` | `REFUSED_LOCATOR_MISMATCH` | `REFUSE_UNKNOWN` |
| `changed-payload` | `REFUSED_INTENT_MISMATCH` | `REFUSE_UNKNOWN` |
| `stale-writer` | `REFUSED_STALE_WRITER` | `REFUSE_STALE_WRITER` |
| `replayed-observation` | `REFUSED_REPLAY` | `REFUSE_NOT_UNKNOWN` |
| `not-applied-is-not-available-from-this-readback` | `REFUSED_NOT_APPLIED_UNAVAILABLE` | `REFUSE_UNKNOWN` |
| `observation-unknown` | `UNKNOWN` | `REFUSE_UNKNOWN` |

硬规则：

- 所有 case 都断言 `send_allowed=false`、`retry_allowed=false`、
  `production_eligible=false`、`production_decision=BLOCKED_PROVIDER_SOURCE`。
- 只有 `SYNTHETIC_ACCEPTED_APPLIED` 的 `settle_allowed=true`；即便如此，
  `production_eligible=false`，并且 `signature_authority=SYNTHETIC_TEST_ONLY`。
- 本 readback **不能产生 NOT_APPLIED**；`REFUSED_NOT_APPLIED_UNAVAILABLE`
  保持 retry 门关闭。
- gate 比 S2 kernel 更严格：当 digest 与 locator 都存在但 digest 不一致时，
  gate 拒绝，即使 locator 相同；kernel 的 either/or 规则适用于只知道其中一个
  的 observer，而 Codex readback 两个都有。

## 7. 提升为 production 的必要条件（未满足）

1. **Live echo proof**：一次 owner 批准的 bounded send，带已知
   `clientUserMessageId`；用 `thread/turns/list` + `itemsView=full` 回读，
   确认 `userMessage.clientId` 等于该值。没有这笔批准，live proof 仍 `NOT_RUN`。
2. **Owner-signed observation contract**：在 S2 契约中增加签名字段（至少覆盖
   owner、device、project、intent digest、provider locator、parent ref、state、
   source、observed_at），由 daemon 持有 production key，并由 daemon 验证；
   不能让 caller 提供 signature/observation。
3. **R1 `mcp/` read adapter**：分页读取 + clientId/intent 匹配；只返回
   APPLIED/UNKNOWN，不返回 NOT_APPLIED。该改动不在本 PR，也不在 I1/Q1/X2 路径。
4. 在 1–3 之前，`dispatch_observation_source=None`、`BLOCKED_PROVIDER_SOURCE`、
   `durable_effects=partial` 保持不变；不安装自动 reconnect sweep。

## 8. 明确未声称

- 没有真实 send / model call / 付费消息。
- 没有读取 token、transcript、环境或凭据值；没有登录或借用户 credential。
- 没有触碰 production Provider、serverosd、FleetNext/Connector、设备服务或 I1
  RC-v7 分支。
- 没有把 synthetic HMAC 当作 owner authority，没有把第三方文档当作 live proof。
- 没有 NOT_APPLIED、没有 retry 放行、没有 exactly-once、没有自动 settle。
- 没有修改 `runtime/`、`workengine/`、`fleet_mcp/`、`mcp/` 或其他 lane 的文件。

## 9. 验证与级别

```sh
cd /data/home/vince/workspace/worktrees/serveros/r1-reconcile-source-20261020

PYTHONDONTWRITEBYTECODE=1 nice -n 10 \
  flock -n /tmp/serveros-fleet-v1-runtime.lock \
  systemd-run --user --scope -q \
    -p MemoryMax=1G -p MemorySwapMax=0 -p CPUQuota=25% -- \
  timeout 120 /data/home/vince/toolchains/serveros-test/bin/pytest \
    tests/test_fleet_v1_runtime_reconcile_source.py -q -p no:cacheprovider
```

实测：`35 passed in 0.63s`，exit 0。与 R1 三套测试合跑：
`186 passed in 1.70s`，exit 0。

| Level | State |
| --- | --- |
| `CODE` | this PR |
| `LOCAL` | hermetic synthetic tests |
| `REAL_TEMP` | `NOT_RUN` |
| `REAL_DEVICE` | `NOT_RUN` |
| `CLIENT_E2E` | `NOT_RUN` |

## 10. Pinned refs

- authoritative `ProfessorZhi/serveros` main `771c81c`
- S2 #299 `4452ddfa3cda3404cc4038acf8086772d868c578`
- S2 #312 `2a3e1352b442b0a307deba50e293a10c8ef34d61`
- S2 Core boundary #328 `cf32af82c03eb52a4f34e2907e670f23b04a9b57`
- R1 #308 `3e30b74b28843b0f466fda5c82b8b601fe230772`
- R1 #311 `34b215c355d93efe9e7b06fcc09f9a803c061f00`
- `codex-cli 0.160.0`; schema hashes are in
  `fixtures/client-turn-id-2026-10-20.json#schema_evidence`
- local read-only CLI help: `codex --help`, `codex exec --help`,
  `codex resume --help`, `codex agents --help`, `codex queue --help`,
  `claude --help`, `botmux --help`, `cloudcli --help`
