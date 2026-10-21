# R1 #248 / S2 #245 · client turn id 与 provider readback（2026-10-20）

> **证据级别**：`SCHEMA_ONLY` + `SYNTHETIC_FIXTURE`。读取了本机
> `codex-cli 0.160.0` 的 stable v2 app-server schema；**没有**真实
> create/send/model call；**没有**读取 token、transcript 或环境值；
> **没有**触碰现存 production provider。schema 生成使用空的临时
> `CODEX_HOME`，只写临时目录，事后删除。

## 1. 结论

**存在唯一可信的协议级路径，但只在 Codex app-server 的
“send 到已知 thread”这一格。**

| 环节 | 字段 | 来源 |
| --- | --- | --- |
| 发送前 client id | `TurnStartParams.clientUserMessageId: string\|null` | `stable/v2/TurnStartParams.json` |
| provider 回读 | `ThreadItem.userMessage.clientId: string\|null` | `stable/v2/ThreadReadResponse.json`、`stable/v2/ThreadTurnsListResponse.json` |
| 推荐读法 | `thread/turns/list` + `itemsView: "full"`，按 `threadId` 分页 | `stable/v2/ThreadTurnsListParams.json` |
| 兼容读法 | `thread/read` + `includeTurns: true`（schema 标注 paginated threads 已弃用 full-history hydration） | `stable/v2/ThreadReadParams.json` |
| provider-native turn id | 匹配项所在 `Turn.id`（UUIDv7） | `stable/v2/ThreadReadResponse.json#Turn` |

读法不是按 client id 过滤：`ThreadTurnsListParams` 与
`ThreadItemsListParams` 都**没有** clientId 过滤参数。调用者必须对已知
thread 分页，取 `itemsView: "full"`，扫描 `userMessage.clientId`，并在匹配项
所在 turn 上取 `Turn.id`。这是一个 provider-owned readback，不是 ServerOS
自己造的账本。

**但生产结论仍是 `BLOCKED_PROVIDER_SOURCE`。** stable schema 允许这两个字段，
却没有声明 provider 会把 `clientUserMessageId` 复制进 `clientId`；本轮没有
真实 send，因此不能把“schema 允许”写成“live 已证明”。在 owner 批准的一次
bounded echo test 证明映射之前，S2 的 production source 必须保持
`BLOCKED_PROVIDER_SOURCE`；本目录的 `SyntheticCodexReadbackSource` 只是
`SCHEMA_ONLY` 的合成源。

## 2. 这个回读能证明什么、不能证明什么

| 情形 | 结果 | provenance | 说明 |
| --- | --- | --- | --- |
| clientId 精确匹配 + intent digest 相同 | **APPLIED** | `FACT`，`source=codex:thread/turns/list` | 带 provider turn id / item id |
| clientId 匹配 + intent digest 不同 | **UNKNOWN**（`CONFLICT`） | `UNKNOWN` | 不是 APPLIED；不挑一个结果 |
| 同一 clientId 出现多次 | **UNKNOWN**（`CONFLICT`） | `UNKNOWN` | 不做任意选择 |
| 无匹配（扫描完整） | **UNKNOWN**（`LOOKUP_MISS`） | `UNKNOWN` | **绝不是 NOT_APPLIED** |
| 无匹配（分页未完成） | **UNKNOWN** | `UNKNOWN` | `scan_complete=false` |
| `itemsView` 为 summary/notLoaded | **UNKNOWN** | `UNKNOWN` | 无 clientId / content |
| 匹配项无文本 content | **UNKNOWN** | `UNKNOWN` | intent 无法核验 |
| thread 不存在 / thread id 不一致 / 响应畸形 | **UNKNOWN** | `UNKNOWN` | 不把错误当 absence |
| Codex **create**（thread id 在丢失响应里） | **RESPONSE_ID_ONLY** | — | 不能凭 client id 找到未知 thread |
| BotMux / human-control | **RESPONSE_ID_ONLY** | — | 只有 `triggerId` 可读 |
| CloudCLI MCP | **NO_SOURCE** | — | `session_send` 无 client id；`run_get` 要 `runId` |

**硬约束**：

- `negative_proof` 恒为 `false`；`exactly_once` 恒为 `false`；
  `retry_allowed` 恒为 `false`。
- 回读只能证明 **APPLIED**；不能证明 **NOT_APPLIED**，因此不能释放 S2 的
  retry 门。一个 miss 永远保持 `EFFECT_UNKNOWN`。
- 本模块不提供 send，不连接 provider，不读取 transcript；readback 由调用者
  作为数据注入。

## 3. Schema 证据（本机 codex-cli 0.160.0）

生成命令：`CODEX_HOME=<empty temp> codex app-server generate-json-schema --out <temp>`。
下表是每个文件与其 sha256；这些 hash 也记录在
`fixtures/client-turn-id-2026-10-20.json#schema_evidence`。

| 文件 | sha256 | 关键字段 |
| --- | --- | --- |
| `stable/v2/TurnStartParams.json` | `2dfcf68705896fadc344ccfeb2e9fe5a6bcbbb8b9a90cf449ce232b636daf05a` | `clientUserMessageId:string\|null` |
| `stable/v2/ThreadReadParams.json` | `dfe040c6ac71d30795b8be3f3ff232e66f362a37f883b491e5d1ea367f470db4` | `includeTurns:boolean`；描述建议 paginated threads 用 `thread/turns/list` + `thread/items/list` |
| `stable/v2/ThreadReadResponse.json` | `3a0aaf9cff80796d07130b85723ca825f9203796d15c878f452083cf9f9a6f0a` | `ThreadItem.userMessage.clientId`；`Turn.id`；`Turn.items:ThreadItem[]` |
| `stable/v2/ThreadTurnsListParams.json` | `2a8b93d7d4437cc25e16a1dde16d8db3c338d39e16c5eece2a56e943a21577d7` | `threadId`；`itemsView:notLoaded\|summary\|full`；cursor；无 clientId 过滤 |
| `stable/v2/ThreadItemsListParams.json` | `d5cf9784413f1d1abba756e04e48b273a9d12ab2c5675442e91b6a4ee55e19f0` | `threadId`；可选 `turnId`；无 clientId 过滤 |
| `stable/v2/ThreadTurnsListResponse.json` | `8db83d38553204a223cf349680c59b0cc674ba95a4637e1995bfeabd73c80219` | `ThreadItem.userMessage.clientId` |

补充：`Turn.items` 的 schema 描述是“Thread items currently included in this
turn payload”；`Thread.turns` 只在 `thread/resume`、`thread/fork` 和
`thread/read`（`includeTurns: true`）时填充。`itemsView` 默认是 `summary`，
所以回读必须显式请求 `full`。

## 4. 能力矩阵

| adapter | verb | 前置 | capability | 读法 | 正向 | 负向 | live proof | production |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Codex app-server | send | thread id 已知 | **CLIENT_TURN_ID** | `thread/turns/list` + `itemsView=full` | APPLIED（clientId + intent digest） | UNKNOWN；无 NOT_APPLIED | **NOT_RUN** | `BLOCKED_PROVIDER_SOURCE` |
| Codex app-server | create | thread id 在丢失响应 | RESPONSE_ID_ONLY | 无 | — | — | NOT_RUN | `BLOCKED_PROVIDER_SOURCE` |
| BotMux | send | triggerId 在丢失响应 | RESPONSE_ID_ONLY | `/trigger-result?triggerId=...` | — | — | NOT_RUN | `BLOCKED_PROVIDER_SOURCE` |
| CloudCLI MCP | send | 无 client id 参数 | NO_SOURCE | `run_get` 要 `runId` | — | — | NOT_RUN | `BLOCKED_PROVIDER_SOURCE` |
| human-control | send | triggerId 在丢失响应 | RESPONSE_ID_ONLY | BotMux `/api/trigger` | — | — | NOT_RUN | `BLOCKED_PROVIDER_SOURCE` |

## 5. 负测矩阵

`tests/test_fleet_v1_runtime_client_turn_id.py` 共 30 个用例，包含：

| case | 期望 |
| --- | --- |
| `applied-exact-match` | APPLIED，FACT，provider turn/item id |
| `applied-exact-match-thread-read` | APPLIED，`source=codex:thread/read` |
| `forged-client-id` | UNKNOWN `CLIENT_ID_NOT_FOUND`，`lookup_miss=true` |
| `unknown-restart-empty-history` | UNKNOWN `NO_TURNS`；restart 不变成 NOT_APPLIED |
| `intent-mismatch` | UNKNOWN `CLIENT_ID_MATCH_INTENT_MISMATCH`，`conflict=true` |
| `duplicate-client-id` | UNKNOWN `CLIENT_ID_DUPLICATE`，不任意选 |
| `summary-only-items` | UNKNOWN `THREAD_ITEMS_UNAVAILABLE` |
| `thread-not-found` | UNKNOWN `THREAD_NOT_FOUND` |
| `thread-mismatch` | UNKNOWN `THREAD_MISMATCH` |
| `malformed-pages` / `malformed-turn-items` | UNKNOWN `SOURCE_MALFORMED` |
| `incomplete-pagination-miss` | UNKNOWN，`scan_complete=false` |
| `non-text-content` | UNKNOWN `INTENT_UNVERIFIABLE` |
| `null-client-id` | UNKNOWN `CLIENT_ID_NOT_FOUND` |
| `missing-provider-id` | UNKNOWN `SOURCE_MALFORMED` |
| `secret-shaped-client-id` | UNKNOWN `INVALID_CLIENT_TURN_ID`，值不回显 |
| 7 个 capability case | Codex send/known=CLIENT_TURN_ID；其余 RESPONSE_ID_ONLY / NO_SOURCE |

每个 case 还断言 `negative_proof=false`、`exactly_once=false`、
`retry_allowed=false`。

## 6. 需要 S2/R1 owner 接线（不在本 PR）

1. **R1 `mcp/codex_app_server_provider.py`（未来写入，不在此次范围）**：
   增加一个只读函数，对已知 thread 分页 `thread/turns/list`
   （`itemsView: "full"`），匹配 `userMessage.clientId`，比较预发送的
   intent digest；返回 `APPLIED`/`UNKNOWN`，带 `source` 与 provider ids。
   **不得返回 NOT_APPLIED**，不得声称 exactly-once。
2. **S2 observation source**：只有 `codex-app-server` + `send` + thread id
   已知时声明 `CLIENT_TURN_ID`；create、BotMux、human-control 声明
   `RESPONSE_ID_ONLY`；CloudCLI 声明 `NO_SOURCE`。
3. **S2 production**：在 owner 批准的一次 bounded echo test 证明
   `clientUserMessageId → clientId` 之前，保持
   `BLOCKED_PROVIDER_SOURCE`。`SyntheticCodexReadbackSource` 只是
   `SCHEMA_ONLY` test source，不能注册为 production source。
4. **Owner approval（一次即可）**：在隔离 thread 上发送固定文本，带已知
   `clientUserMessageId`；用 `thread/turns/list` 回读并确认 `clientId` 等于
   该值；随后停止。没有这笔批准，live echo 仍 `NOT_RUN`。

## 7. 明确未声称

- 没有真实 send / model call / 付费消息。
- 没有读取 token、transcript、环境或凭据值。
- 没有触碰现存 production provider；schema 生成使用空临时 `CODEX_HOME`。
- 没有把 schema 允许写成 live 已证明。
- 没有 NOT_APPLIED、没有 retry 放行、没有 exactly-once。
- 没有修改 `mcp/`、`runtime/`、S2 代码或其他 lane。

## 8. 验证

```sh
cd /data/home/vince/workspace/worktrees/serveros/r1-client-turn-id-20261020

PYTHONDONTWRITEBYTECODE=1 nice -n 10 \
  flock -n /tmp/serveros-fleet-v1-runtime.lock \
  systemd-run --user --scope -q \
    -p MemoryMax=1G -p MemorySwapMax=0 -p CPUQuota=25% -- \
  timeout 120 /data/home/vince/toolchains/serveros-test/bin/pytest \
    tests/test_fleet_v1_runtime_client_turn_id.py -q -p no:cacheprovider
```

实测：`30 passed in 0.50s`，exit 0。与 R1 既有两套测试合跑：
`151 passed in 1.60s`，exit 0。

## 9. Pinned refs

- authoritative `ProfessorZhi/serveros` main `771c81c`
- S2 PR #299 head `4452ddfa3cda3404cc4038acf8086772d868c578`
- R1 PR #308 head `3e30b74b28843b0f466fda5c82b8b601fe230772`
- `codex-cli 0.160.0` stable v2 schema hashes 见 §3
- R1 observed read-only snapshot `observed/tokyo-2026-10-20.json`
