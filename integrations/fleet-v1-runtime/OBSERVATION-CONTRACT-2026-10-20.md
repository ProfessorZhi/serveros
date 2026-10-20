# R1 #248 / S2 #245 · minimal production observation interface

> **Base**：R1 #361 exact `6a27204300753f129719103d0aee811ac675622a`。
> **Earlier pins**：#356 `b4020b7`；QA2 #360 `2888d8b`；S1 #363 `8840f41`；
> S2 Core boundary #328 `cf32af8`；authority main `771c81c`。
> **Evidence**：`CODE` / `LOCAL` / `SYNTHETIC_FIXTURE`。没有真实 Provider
> send、model call、token/transcript、OAuth、serverosd/Fleet/Connector、部署或
> S1/I1/RC-v8/Q1/X2 文件变更。

## 1. 结论

**接口可落地；生产仍 `BLOCKED_PROVIDER_SOURCE`。** 本轮给出三个签名对象的
最小 production contract、威胁模型、离线 conformance validator 与 41 项测试，
但**不实现生产正向状态**：

- 生产字段恒为拒绝：`production_decision=BLOCKED_PROVIDER_SOURCE`、
  `production_eligible=false`、`send_allowed=false`、`retry_allowed=false`、
  `resend_allowed=false`、`settle_allowed=false`。
- `synthetic_decision` 只表示本地 contract 是否满足；`synthetic_settle_allowed`
  恒为 `false`。
- 五个外部依赖未满足：`BLOCKED_OWNER_SIGNED_SOURCE`、
  `BLOCKED_PROVIDER_ECHO`、`BLOCKED_S2_OBSERVATION_FIELD`、
  `BLOCKED_COMMIT_SEAM`、`BLOCKED_REPLAY_STORE`。

## 2. 最小接口（三个签名对象）

### 2.1 Core source binding

由 **Core** 用 Ed25519 签名，声明“这个 source 只能读什么、属于谁”：

```text
schema=serveros/core-observation-source-binding.v1
source_id, provenance=core:agent-control,
adapter, verb, thread_known, read_path, capability,
device_ref, project_ref, owner_ref, audience,
key_id, issued_at, expires_at, nonce, signature
```

约束：

- `provenance` 只从这个**已签名**对象读取；observation/request 中同名字段一律
  拒绝为 `REFUSED_SELF_DECLARED_PROVENANCE`。
- `issued_at <= now <= expires_at`。
- `(adapter, verb, thread_known)` 必须与 `capability` 一致：
  - `codex-app-server/send/true -> CLIENT_TURN_ID`
  - `codex-app-server/send/false -> RESPONSE_ID_ONLY`
  - `codex-app-server/create -> RESPONSE_ID_ONLY`
  - `codex-cli -> NO_SOURCE`；`claude-code-cli -> CLI_ONLY_NOT_WIRED`
  - BotMux/human-control `RESPONSE_ID_ONLY`；CloudCLI `NO_SOURCE`
- `read_path` 只能是 `codex:thread/turns/list` 或 `codex:thread/read`。

### 2.2 Owner-signed observation

由 **owner** 用 Ed25519 签名，声明一次具体观察：

```text
schema=serveros/owner-signed-observation.v1
observation_id, client_turn_id, thread_id, thread_known,
source_binding_id, owner_ref, device_ref, project_ref,
intent_digest, provider_locator, parent_ref, state,
source, observed_at, writer_owner/writer_epoch/writer_token,
audience, key_id, issued_at, nonce, signature
```

约束：

- `observation_id`、`client_turn_id`、`thread_id` 非空；`thread_known=true`。
- `owner_ref` = attempt principal = trust owner；`audience` = trust audience；
  `source_binding_id` 必须指向已验签的 source binding。
- `intent_digest` 或 `provider_locator` 至少一个与 attempt 一致；两者都存在时
  不一致即拒绝。
- `writer_owner/epoch/token` 必须在签名内，并等于 attempt 的 current writer；
  attempt 自身 writer 与 current writer 也必须一致。
- `observed_at` 必须是 zoned ISO-8601；不得晚于 `now`；`now - observed_at <=
  max_age`（1..3600s）；`observed_at <= issued_at <= now`。
- `state` 只接受 `APPLIED`；`NOT_APPLIED` 对这条 positive-only readback 不可用；
  `UNKNOWN` 不 settle。
- `observation_id` 必须非空且不在 applied set；空 id 不是“跳过 replay”，而是
  `REFUSED_MISSING_OBSERVATION_ID`。

### 2.3 Provider client-id echo

由 **Core 持有的 provider 客户端**从 provider read path 取得：

```text
schema=serveros/provider-client-id-echo.v1
capability=CLIENT_TURN_ID, adapter, verb, thread_known,
read_path, client_turn_id, echoed_value,
matched_on=userMessage.clientId,
observed_at, live, synthetic, source
```

- production 要求 `live=true`、`synthetic=false`、
  `echoed_value == client_turn_id`。
- `synthetic=true` 只能满足本地 conformance，不能解除
  `BLOCKED_PROVIDER_ECHO`。
- echo 必须来自 Core 持有的 provider 客户端；caller 自报的 echo 不是证据。

### 2.4 签名与信任根

- 算法：Ed25519；canonical bytes 与 `context/actor.py#signed_bytes` 一致
  （`sort_keys`、UTF-8、紧凑分隔符、`allow_nan=false`、排除 `signature`）。
- 信任根：Core/owner 持有的 public-key binding；observation 不能自带公钥，
  caller 不能选择信任根。
- key authority：source binding 必须 `core`；observation 必须 `owner`；
  execution key 永远不是 owner authority。
- 测试 venv 无 `cryptography` 时，`Ed25519Verifier` 抛
  `VerifierUnavailable`（fail closed）；测试使用显式
  `SyntheticVerifier(authority=..., production=False)`，不能通过生产检查。

## 3. 威胁模型

| ID | 威胁 | 控制 | 残余 |
| --- | --- | --- | --- |
| T1 | caller 在 observation 中自报 `provenance/core` | observation schema 封闭；provenance 只来自已签名 source binding | 无（本地 validator 拒绝） |
| T2 | synthetic HMAC 被当成生产签名 | verifier 声明 `authority`/`production`；生产字段恒拒绝；`synthetic_settle_allowed=false` | 无；测试 key 不是 authority |
| T3 | 伪造 source binding | Core Ed25519 签名 + 受信 `key_id` | 需要真实 Core key/trust binding（外部） |
| T4 | 伪造 observation | owner Ed25519 签名 + 受信 `key_id` | 需要真实 owner key/trust binding（外部） |
| T5 | execution key 冒充 owner | `authority=="owner"`；execution key 永不 owner | 需要 Core 的 key 分类（外部） |
| T6 | capability inflation（CLI / unknown thread 声称 `CLIENT_TURN_ID`） | `(adapter, verb, thread_known)` 交叉检查 | 无（本地 validator 拒绝） |
| T7 | cross-owner / cross-device / cross-project | source 与 observation 的 scope/audience 检查 | 无 |
| T8 | 过期或尚未生效的 source binding | `issued_at <= now <= expires_at` | 无 |
| T9 | observation id replay | 非空 id + applied set；空 id 直接拒绝 | 需要持久 replay store（外部） |
| T10 | writer epoch 7→8 旧 observation 复用 | writer fence 在签名内；attempt writer vs current writer | 无（本地 validator 拒绝） |
| T11 | stale / future / invalid observed_at | parse + trusted `now` + max age + order | 无 |
| T12 | 缺 client id / 未知 thread | 必填且 `thread_known=true` | 无 |
| T13 | changed payload / forged locator | digest 与 locator 双匹配 | 无 |
| T14 | 发明 `NOT_APPLIED` | positive-only readback 拒绝；只接受 APPLIED | 无 |
| T15 | verify→commit TOCTOU | `BLOCKED_COMMIT_SEAM`；纯 validator 不提供原子性 | 需要 S1 atomic commit seam（外部） |
| T16 | 签名 malleability / key substitution | Ed25519 + canonical bytes + strict base64 + trusted key id | key 轮换/撤销需要 Core trust binding（外部） |
| T17 | caller 伪造 provider echo | echo 必须来自 Core 持有的 provider 客户端；production `live=true` | 纯 validator 不能证明 echo 的来源，需 live proof（外部） |
| T18 | 重放整组 source+observation | observation id replay + source binding window | 需要持久 replay store（外部） |

## 4. 外部依赖与可验收标准

| ID | Owner | 可验收标准 |
| --- | --- | --- |
| `BLOCKED_OWNER_SIGNED_SOURCE` | Core/owner trust domain | 真实 owner Ed25519 key + Core-held trust binding 签 source binding 与 observation；execution key 不能满足；test HMAC 不接受 |
| `BLOCKED_PROVIDER_ECHO` | Provider adapter / Core | 一次 owner 批准的 bounded Codex send，带已知 `clientUserMessageId`；`thread/turns/list` 回读 `userMessage.clientId`；`live=true`、`synthetic=false` |
| `BLOCKED_S2_OBSERVATION_FIELD` | S2 kernel/store/daemon | S2 observation envelope 接受签名字段，并拒绝未签名/未绑定 observation；本接口在 owner 落地前不 wiring |
| `BLOCKED_COMMIT_SEAM` | S1 authority lane | 原子 verify→commit seam，或 `verified:false` + `BLOCKED_OWNER_COMMIT_SEAM`，并有独立 revoke-before-acquire RED→GREEN |
| `BLOCKED_REPLAY_STORE` | S2 store | 持久 observation-id replay set（建议同时 keyed by observation id + writer epoch）；缺失时保持 `EFFECT_UNKNOWN` |

在这五项全部满足之前，任何“production positive”都是不可信的；本轮不实现。
`EFFECT_UNKNOWN` 不重复发送：所有结果的 `resend_allowed=false`、
`send_allowed=false`、`retry_allowed=false`、`settle_allowed=false`。

## 5. RED / GREEN

RED（module 尚未存在）：

```text
ERROR collecting tests/test_fleet_v1_runtime_observation_contract.py
FileNotFoundError: .../integrations/fleet-v1-runtime/observation_contract.py
2 errors in 0.39s; rc=2
```

GREEN：

```text
41 passed in 0.58s; exit 0
```

覆盖：contract satisfied synthetic、live echo alone still blocked、自报
provenance、伪造 source/observation 签名、execution key、scope/audience、
expired source binding、CLI/unknown-thread capability inflation、missing
observation/client id、unknown thread、writer epoch、invalid/future/stale
observed_at、replay、NOT_APPLIED、changed payload、forged locator、missing
parent、cross-project、non-EFFECT_UNKNOWN、缺 source/observation 签名、YAML
contract 与 module 字段/blockers 一致、purity。

级别：**CODE / LOCAL / SYNTHETIC_FIXTURE**。`REAL_PROVIDER`、
`REAL_DEVICE`、`CLIENT_E2E` = **NOT_RUN**；不声称
`REAL_PROVIDER`、`QA_SIGNED` 或 `V1_RELEASE`。

## 6. 明确未声称

- 没有真实 Provider send、model call、token/transcript、OAuth、CLI session
  payload 或付费消息。
- 没有修改 S1、S2、I1/RC-v8、Q1、X2、canonical `src/serveros` 或在用
  `fleet/registry.yaml`、serverosd/Fleet/Connector、生产 OS policy/grant/凭据。
- 没有实现 production-positive；没有 send/retry/resend/settle、NOT_APPLIED
  或 exactly-once。
- 没有把 test HMAC 提升为 owner authority；没有把 schema-level echo 当作 live
  proof。

## 7. Pinned refs

- R1 #361 `6a27204300753f129719103d0aee811ac675622a`（base）
- R1 #356 `b4020b73e6b7ec2b1f6301471a9872f8864c1643`
- QA2 #360 `2888d8bda6828bd2c87c68a770440933fdb4ae23`
- S1 #363 `8840f416169b22122450fb61a85f2b88c73715a0`
- S2 #328 `cf32af82c03eb52a4f34e2907e670f23b04a9b57`
- authority main `771c81c`
