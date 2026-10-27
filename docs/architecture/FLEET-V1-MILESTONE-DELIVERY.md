# Fleet V1 里程碑投递合同

> **这篇回答什么：** Fleet V1 里「一件工作到了一个值得 owner 知道的里程碑」这件事，由谁观测、谁判定、谁投递、投给谁、失败时降级成什么，以及每一条事实的唯一来源。它是 [Fleet 四层](FLEET-FOUR-LAYERS.md) 与 [Fleet V1/V2 架构](FLEET-V1-V2-ARCHITECTURE.md) 之上的一层**交付保证**，不是第五个子系统。
>
> **状态：`SPEC_PROPOSED` / `NOT_INSTALLED`**（2026-10-27）。这是合同，不是实现记录。本合同描述的适配器、观察器、里程碑判定目前**在 `main` 上都不存在**；三台设备的实际状态见 §13，**没有任何设备被更新，本文不宣称任何设备已更新**。
>
> **上下游：** 源码实现权在 [#417](https://github.com/ProfessorZhi/serveros/issues/417)，四部分交付进度在 [#409](https://github.com/ProfessorZhi/serveros/issues/409)，跨设备/CI/发布一致性闸门在 [#418](https://github.com/ProfessorZhi/serveros/issues/418)。本文只拥有**语义与归属**。

---

## 0. 权威边界

| 本文拥有 | 本文不拥有 |
|---|---|
| 「里程碑」「投递」「已送达」的准确定义与判定前置条件 | 实现代码、PR 拆分、测试写法（#417） |
| 每条事实的唯一来源；什么不能成为第二权威 | CI 编排与收集路径（#418 的 CI 线） |
| 红 / 黄 / 灰 / 绿 的处置与静默规则 | 设备上的安装、重启、升级（#418 的 P 线；且本波未授权） |
| 重启、去重、重放、回执状态机 | 四部分各自的完成度（#409） |
| 目录归属与本合同的后续变更规则 | 层登记表本身（[layers.yaml](layers.yaml)，由 #414 拥有） |

**冲突时的裁决顺序：** 事件词汇与句子归 `runtime/notification-contract.yaml`；层与目录归属归 `layers.yaml`；V1/V2 需求边界归 `FLEET-V1-V2-ARCHITECTURE.md`。本文**只解释如何把它们组合成一条投递保证，不重新定义其中任何一个**。本文若与它们冲突，是本文错。

**本文不新造一套「红绿灯授权」**。现有通知契约已经有两个正交轴（事件严重度 `INFO/ATTENTION/ACTION`，以及告警自身 `low/medium/high` → 人读到的词），本合同 §6 只在它们之上规定「什么算里程碑、什么状态下允许打扰人」，不引入第二套颜色—权限映射。

---

## 1. 只用两个状态词

本合同对「这件事做到哪一步」只允许这两个词，别的一律视为未声明：

| 词 | 含义 | 能推出什么 | 推不出什么 |
|---|---|---|---|
| `SPEC_PROPOSED` | 只有文档/设计，源码未合并到 `main` | 有人写下了规则 | 任何一行为已经存在 |
| `NOT_INSTALLED` | 源码可能有，设备上没有该产物（或未观测） | 该设备跑的是旧的东西 | 「装了但没过」或「过了但没装」 |

**验收状态**（产物的，不是文档的）单独用这五个词，按顺序不可跳级、不可互相替代：

`CODE` → `HOSTED_CI` → `REAL_DEVICE` → `CLIENT_E2E` → `PRODUCTION`

一个模块有单元测试 = `CODE`；hosted CI 绿 = `HOSTED_CI`；真机上跑过 = `REAL_DEVICE`；真的在飞书客户端上收到 = `CLIENT_E2E`；owner 在生产里依赖它 = `PRODUCTION`。**本合同当前处在 `SPEC_PROPOSED`，五个验收状态一个都没拿到。**

---

## 2. 唯一权威：每条事实只有一个来源

| 事实 | 唯一来源 | 明确不允许的第二来源 |
|---|---|---|
| 设备身份、拓扑、owner 关系 | L0 `fleet/`（`registry.yaml`、`profiles.yaml`） | 设备名、主机名、Tailscale 名 |
| Work / lease / 授权 / 抢占 | L0 `workengine/` + Ability 目录 `ability-management` | 任何会话里的「我记得我拿到了」 |
| Run 的原始终态 | L2 节点原生 CloudCLI `GET /v1/runs/{id}` 的**回答本身** | 观察者的推断、Agent 的总结 |
| CI 结果 | GitHub Actions 在**精确 SHA** 上的 conclusion | 「本地跑过了」、`git log` 里有这个提交 |
| 能说什么（句子与词汇） | product `runtime/notification-contract.yaml`（封闭词汇 + 模板，作者是 ServerOS） | 投递方或模型现编的文案 |
| 告警状态与去重身份 | product `runtime/alerting.py`（`code + device_ref + subject_ref`） | 时间戳、pid、容器名 |
| 卡片与出站投递 | product `runtime/direct_lark_notify.py` + `runtime/message_transport.py`（东京唯一授权出口） | 任何设备自带的第二套飞书凭据 |
| **里程碑判定** | **本合同 §5** | 观察器、Agent、卡片状态文本 |
| 解释与摘要 | L3 每设备 caretaker Agent | 它不得成为上面任何一行的来源 |

两条推论，写下来是因为它们最容易被违反：

1. **L3 的摘要不是事实。** caretaker Agent 可以写「四个 lane 都过了」这样的句子；这句话的每个成分都必须能追到 §2 表里的某一行，否则这句话不许发出。
2. **卡片上的颜色不是授权。** 卡片是渲染结果。任何「看到绿卡就继续派活」的自动化都是在用一个渲染产物当权威。

---

## 3. 四层职责与单向依赖

### 3.1 在这条链上每层做什么

| 层 | 在这条链上的职责 | 明确**不**做 |
|---|---|---|
| **L1** `serverosd`（C++，本机常驻） | 确定性本机观测与有界自愈：资源/服务/网络事实，`bootId` 的宿主侧锚点 | 不投飞书、不跑长驻 LLM、不解释里程碑 |
| **L2** 节点 CloudCLI + 东京 Fleet 枢纽 | `runId`/`deliveryId` 的权威、Run 终态、CI 证据装配、稳定回执 | 不判定「这个里程碑重要不重要」、不写人读文案 |
| **L3** 每设备逻辑 caretaker Agent | 被事件激活，**只做**解释与摘要；模型不可用时退化为确定性模板 | 不观测、不发授权、不把假完成说成完成、不编造 CI 结果 |
| **L4** 指挥侧 Skills（CC / Codex / ChatGPT 插件） | 派发后立即返回，不等 | 不轮询、不持有长连接、不记账 |
| **L0** 身份 / Work / lease / Ability | 谁可以做、对哪件工作、还能做多久 | 不投递、不渲染 |
| **product**（`runtime/` 内的通知与卡片） | 事件语义、告警状态、卡片、唯一出站通道 | 不产生 Run/CI 事实 |

### 3.2 方向

```text
L4 ──MCP──▶ L2-hub ──节点协议──▶ L2-node ──▶ CloudCLI
               │                    │
               ▼                    ▼
               L0  ◀──────────────  L1 ──观测──▶ L3
product ──▶ L0（读）               shared ──▶ 任意层（构建/测试/适配器）
```

本文新增的三条方向，都只是上面这张图的具体化，**没有任何一条需要新的依赖方向**：

```text
L2 观察者 ──候选事实──▶ product 投递者 ──▶ 东京唯一 Feishu 出口
（只产事实，不渲染）      （只渲染与投递，不产事实）
L3 摘要 ──▶ 投递者     （可选；缺席时走确定性模板）
L1 ──✗──▶ L2/L3/product/Tokyo   （本机自愈不依赖网络、不依赖 Agent）
```

### 3.3 「签名依赖」的定义

本合同里的**签名**不是密码学签名，指的是：**每一条允许的依赖方向，都在 `layers.yaml` 的 `rules` 里被显式写下来，并由 `tests/test_repository_layers.py` 逐条核对**。没写进 `rules` 的方向靠评审守，不算签名。

当前已签的三条（`layers.yaml`）：枢纽不加载 `runtime/` 或 `mcp/`；`plugins/` 无代码；身份层不加载枢纽。

本链条还需要三条签名，**目前都未登记**（`layers.yaml` 由 #414 拥有，见 §12 的交接）：

| 待签规则 | 为什么 | 状态 |
|---|---|---|
| 投递适配器不导入 L2 枢纽内部模块，候选事实由外部注入 | 避免把枢纽编进 product | `SPEC_PROPOSED` |
| L3 摘要模块不被任何事实产生产路径导入 | 保证 Agent 挂了投递仍成立 | `SPEC_PROPOSED` |
| L1 不导入 L2 / L3 / product | 保证断网单机自愈 | `SPEC_PROPOSED` |

---

## 4. 一次里程碑的一生

```text
① L4 派发（fleet_create_session / fleet_send_to_session / 触发 CI）
      │  写入稳定 work_ref + source_ref，记 dispatch receipt
      ▼
② DISPATCH_ACK  ← 只证明「排队了」。指挥端到这里就可以离开。
      │
      ▼
③ L2 观察者（有界、无 LLM、可从中断处恢复）
      │  原生 run_get / 会话事件；GitHub webhook 或有界轮询
      ▼
④ RUN_ENDED（终态）→ 有可信原生证据才算；否则 OUTCOME_UNKNOWN
      │
      ▼
⑤ 里程碑判定（本合同 §5）→ 大多数变化停在这里：卡片静默更新
      │  只有「重要的一次」或「必须有人动手」才往下去
      ▼
⑥ L3 caretaker Agent 被事件激活：写摘要（可选）
      │  模型不可用 → 确定性模板，直接跳到 ⑦
      ▼
⑦ product 投递者：封 delivery_key，记录交付意图
      │
      ▼
⑧ 东京唯一出口 → Feishu App 1 / lark-cli → owner 会话
      │
      ▼
⑨ 记 provider_receipt 与 delivery_state（UNKNOWN ≠ DELIVERED）
```

四件事写死在这条链上：

- **② 和 ④ 之间可能隔几个小时。** 这正是这条能力存在的理由；任何让 ② 等到 ④ 的实现都违反本合同。
- **⑤ 是漏斗最窄的一处。** 绝大多数 Run 的结束不是里程碑，应当只改卡片。
- **⑥ 可以缺席。** 缺席不是降级到「不发」，而是降级到「用模板发」。
- **⑨ 的 `UNKNOWN` 不能被写成 `DELIVERED`**（§9）。发送调用返回成功，只说明我们发出去了，不说明有人收到。

---

## 5. 什么算「重要绿」

`milestone_kind` 是**封闭词表**（新增需要改本文件，见 §16）：

| `milestone_kind` | 判定前置条件（全部满足才允许） | 默认打扰 |
|---|---|---|
| `DISPATCH_ACK` | 收到 L2 派发回执（`session_ref` + `run_id` 或 `queued_message_uuid`）；`accepted` 为真 | 不打扰（只入账） |
| `RUN_ENDED` | 原生 `run_get` 给出终态**且**有可信结果引用 | 一般不打扰 |
| `CI_PASSED` | 明确 SHA 上该批次**全部**结论为 success；flaky 重试后仍记原结论 | 一次（若该批次是 owner 关心的闸门） |
| `QA_ACCEPTED` | 按事先约定的验收条目逐条有证据，且由独立于实现者的会话给出结论 | **一次** |
| `FEISHU_DELIVERED` | provider 回执可读，且 `delivery_state` 不为 `UNKNOWN` | 不再打扰（它本身就是投递） |

三条从 [#417](https://github.com/ProfessorZhi/serveros/issues/417) 抄下来的判定例子，写在这里是因为它们是最常被弄错的三处：

| 现象 | 正确判定 | 常见错误 |
|---|---|---|
| 四个 Draft PR 被打开 | `ARTIFACT_PRODUCED`（不是 V1 完成） | 「四部分都提交了 = V1 绿」 |
| GitHub CI 矩阵全绿 | `CI_PASSED`（不是发布验收） | 「CI 绿 = 可以发布」 |
| `run_get` 显示 completed 但没有可信原生结果 | `OUTCOME_UNKNOWN` | 「completed = 成功」 |

**`UNKNOWN` 永不升级为绿。** 它有自己的状态，按年龄与影响升级（§6），不按「看起来还行」升级。

---

## 6. 红 / 黄 / 灰 / 绿 的处置

分色只管**打扰谁、何时、几 次**；不管「这件事多严重」（那是 `low/medium/high` 的词）。两轴不要合并成一个数字。

| 色 | 判据 | 打扰人？ | 出口 | 关闭条件 |
|---|---|---|---|---|
| **绿** | §5 中 `CI_PASSED` / `QA_ACCEPTED` 前置条件全满足，且该 `delivery_key` 从未投递成功 | **是，恰好一次** | 飞书卡片（重要里程碑） | 收到回执后关闭；重复观察不再发 |
| **黄** | 可恢复、待观察、或已恢复的告警（`alert.resolved`） | **否** | 只进卡片与周期摘要 | 下一次摘要自然覆盖 |
| **红** | 需要 owner 动作才能继续（授权、决定、不可恢复故障） | **是，立即** | 飞书卡片，带动作与影响面 | 可靠解除**或** owner 明确确认；不由超时自动关闭 |
| **灰（`UNKNOWN`）** | 证据不足：Run 终态未知、CI 结论未知、投递回执未知 | 起初否，**按年龄升级** | 先卡片；超阈值后按红处理 | 补到证据为止；**永不当绿** |
| 静默 | 普通变更、正常推进、重复的同类事件 | 否 | 卡片状态静默更新 | — |

「黄色不发」与「绿色只发一次」是两条不同的规则：前者是**永不主动推**，后者是**推一次之后永不再推同一里程碑**。把它们混成「重要的才发」，会让恢复消息消失，而恢复消息的缺席会被人读成「问题还在」。

---

## 7. 字段

三段身份、三段执行、四个事实、四个判定，**不可互相顶替**：

| 字段 | 属于 | 谁写 | 不得承载 |
|---|---|---|---|
| `work_ref` | 身份 | L0（派发时定，跨 Run 稳定） | 一次执行的时间、随机数 |
| `source_ref` | 身份 | L2（PR / commit / CI run 的定位） | 分支名当 SHA 用 |
| `session_ref` | 身份 | L2（会话） | 把会话当 Agent 实体 |
| `runId` | 执行 | L2 节点 | 派发回执本身 |
| `deliveryId` | 执行 | L2（一次派发/一次投递尝试） | `runId` 的别名 |
| `bootId` | 执行 | L1 宿主 | 会话 ID、进程启动时间 |
| `milestone_kind` | 事实 | 判定层（§5 封闭词表） | 自由文本 |
| `observed_at` | 事实 | 观察者 | 人读时间 |
| `source_sha` | 事实 | L2 / GitHub | 「当前 main」这类浮动引用 |
| `evidence_ref` | 事实 | 观察者 | 原始提示词、token、transcript |
| `decision` | 判定 | 判定层 | 由投递方回填 |
| `delivery_key` | 判定 | 投递者（稳定幂等键） | 时间戳、pid、重试次数 |
| `provider_receipt` | 判定 | 出站通道 | 猜测值；拿不到就是 `UNKNOWN` |
| `delivery_state` | 判定 | 投递者 | 由「发送没报错」推断的 `DELIVERED` |

**状态词表（封闭）：** `DISPATCH_ACK` · `RUN_ENDED` · `CI_PASSED` · `QA_ACCEPTED` · `FEISHU_DELIVERED`

**`OWNER_READ` 不在词表里，也不许出现。** 出站通道拿不到已读回执；把它写进词表等于宣称一个我们观测不到的事实。

一次 V1 阶段验收的载荷（示例，非真实数据）：

```json
{
  "work_ref": "serveros:v1-four-lanes",
  "source_ref": "ProfessorZhi/serveros@<sha>",
  "session_ref": "<session>",
  "runId": "<run>",
  "deliveryId": "<dispatch>",
  "bootId": "<boot>",
  "milestone_kind": "QA_ACCEPTED",
  "observed_at": "2026-10-27T00:00:00Z",
  "source_sha": "<sha>",
  "evidence_ref": ["pr:#413@<sha>", "pr:#410@<sha>", "ci:<run-id>"],
  "decision": "green_once",
  "delivery_key": "QA_ACCEPTED:serveros:v1-four-lanes:<sha>",
  "provider_receipt": "UNKNOWN",
  "delivery_state": "PENDING"
}
```

`provider_receipt: "UNKNOWN"` 与 `delivery_state: "PENDING"` 一起出现，是在陈述**我们只知道已经封了键、还没拿到回执**。这就是诚实的写法。

---

## 8. 异步 ACK 与可靠验收的差别

| 状态 | 证据 | 能推出 | 推不出 |
|---|---|---|---|
| `DISPATCHED` | L4 调用了派发工具 | 我们发出了请求 | 节点收到 |
| `DISPATCH_ACK` | L2 回执（`session_ref`、`run_id`/`queued_message_uuid`、`accepted`） | 已入队 | 开始执行 |
| `RUNNING` | 原生会话/`run_get` 观测 | 在做 | 会做完 |
| `RUN_ENDED` | 原生终态 + 可信结果引用 | 这一轮结束了 | 结果正确、里程碑达成 |
| `OUTCOME_UNKNOWN` | 终态缺失或不可信 | 我们不知道 | 失败（也推不出成功） |
| `CI_PASSED` | 精确 SHA 上该批次结论 | 那批检查过了 | 发布验收 |
| `QA_ACCEPTED` | 预先约定的验收条目逐条有证据 | 这一阶段达成 | 已投产 |
| `FEISHU_DELIVERED` | provider 回执，`delivery_state != UNKNOWN` | 我们确信交给了通道 | **owner 读过** |

这张表的核心一句：**`DISPATCH_ACK` 是这条链上最容易被当成「完成」的状态，而它只证明排队。** 「消息送达」与「owner 知道」之间还有一道我们观测不到的墙，合同在这里止步。

---

## 9. 重启、去重、重放

### 9.1 三类游标，各自恢复

| 游标 | 位置（现有机制） | 恢复后做什么 |
|---|---|---|
| L2 观察者事件游标 | 枢纽事件存储（设计见 V1/V2 §5.1，**`main` 上尚未实现**） | 从 `seq` 继续，不重放已确认的 Run |
| product 投递游标 | `runtime/alert_digest.py::DeliveryCursor`（`delivery.json`） | 从上次投递位置继续 |
| 卡片回执账本 | `cards/delivery.json` | 用来判断「这张卡是否已经建过」 |

三者是兄弟文件而不是同一文件（见 `runtime/alerting.py` 对 `state.json` / `ledger.json` 的拆分理由）：每 tick 重写的文档不要和偶尔重写的文档共用一个文件，否则一次撕裂写会同时毁掉两份。

### 9.2 `delivery_key` 怎么生成

**稳定**是唯一要求：同一件里程碑，在任何时间、任何进程、任何重试里都必须得到同一个键。

```text
delivery_key = milestone_kind + ":" + work_ref + ":" + source_sha + ":" + 目标
```

**不许**包含：时间戳、pid、重试次数、`deliveryId`（每次尝试不同）、随机会话 ID。这与 `runtime/alerting.py` 的告警身份规则同一个道理（`code + device_ref + subject_ref`，`identity_is_not: [pid, container_name, timestamp]`）。

### 9.3 三个崩溃窗口

| 窗口 | 现象 | 正确行为 |
|---|---|---|
| 事件已写、投递未发生 | 重启后事件游标未推进 | 补投；若里程碑已过期（§11），记为 `SUPERSEDED` 而不发 |
| 已发送、回执未落盘 | `provider_receipt` 缺失 | 标 `UNKNOWN`，**按 key 先查重再决定是否重发**；不可盲发 |
| 回执已落盘、实际未送达 | 账本说已发，人没收到 | 只能靠 owner 反馈或后续回读发现；合同不声称能自愈，只要求 `delivery_state` 与实际一致地记录 |

### 9.4 重放的三种来源

重复 webhook、重复的 Run 观察、进程重启。三者都靠 `delivery_key` 唯一性收敛，**不靠在投递前用 LLM 判断「这条是不是新的」**。重发有上限；超过上限转人工卡片，而不是无限重试。

---

## 10. 单 Feishu 出口

1. **一个应用、一台机器、一条通道。** 东京授权的 Feishu App 1 + `lark-cli`，经 `runtime/direct_lark_notify.py` 与 `runtime/message_transport.py` 出站。
2. **节点只发事实候选，不持凭据。** 任何设备不得持有 Feishu 凭据的第二份副本；凭据分发不是本合同的选项，也不在 V1 范围内。
3. **内容边界。** 消息里不许出现：提示词、OAuth/PAT/bearer、会话 transcript、私有路径、原始 provider 错误体。原因词必须来自封闭词表（`notification-contract.yaml` 的 `templates` 只接受 `{subject}`/`{device}`/`{reason}` 这三个受控占位符）。
4. **只出站。** 飞书是通知通道，**不是鉴权通道**。任何「从飞书发来一条消息 = 获得授权」的设计都违反 L0 的权威。
5. **本波不真发。** 探索波只用 mock；真实发送、凭据变更、生产服务重启都需要独立授权。

---

## 11. 安全降级

| 故障 | 降级行为 | 绝不能发生 |
|---|---|---|
| L3 模型不可用 | 用 `notification-contract.yaml` 的模板发确定性句子（句子作者是 ServerOS，不是模型） | 因为「没法总结」而不发红事件 |
| L3 模型返回可疑内容 | 丢弃模型文本，走模板 | 让模型改写事实（把 UNKNOWN 说成完成） |
| 东京枢纽不可达 | 本机 `serverosd` 照常观测与自愈；候选事件留在 L2 账本，恢复后按游标补投 | 本机自愈因为中心掉线而停摆 |
| 飞书不可达 | `delivery_state = UNKNOWN`，有限重试，卡片保留 | 把重试成功写成 `DELIVERED` |
| GitHub 不可达 | `CI_PASSED` 保持未知 | 用「本地跑过了」代替 CI 结论 |
| 全部不可用 | 什么都没有被声称；`UNKNOWN` 保持 `UNKNOWN` | 静默地把灰变绿 |

**补投的过期规则：** 恢复后补投时，只补仍然成立的里程碑。一个已经过期的「绿」不再发——发一条三天前的「全绿了」既不真也不有用，记 `SUPERSEDED` 即可。红色事件相反：它**一直有效到被解除或被 owner 确认**，所以补投必须发。

---

## 12. 目录归属

| 路径 | 层 / 归属 | 现状 | 说明 |
|---|---|---|---|
| `integrations/fleet-v1-milestone-notify/` | `integrations: shared` | **不存在**（`SPEC_PROPOSED`） | 顶层 `integrations/` 已在 `layers.yaml` 登记为 `shared`，**新增其下的子目录不需要改 `layers.yaml`** |
| `tests/test_fleet_v1_milestone_notify.py` | `tests: shared` | 不存在 | 契约测试与实现同 PR |
| 任何新顶层目录 | — | 不新增 | 薄适配器不值得一个顶层目录 |

### 12.1 为什么不需要动 `layers.yaml`

`tests/test_repository_layers.py` 的两条守卫都只看**顶层**目录：每个被跟踪的顶层目录必须登记，且登记的必须存在。`integrations` 已经登记为 `shared`，所以在其下新增 `fleet-v1-milestone-notify/` 不触发任何守卫变化。这与「新功能需要额外归属」是两件事——**本次没有这样的需求**。

### 12.2 交接（不改 `layers.yaml`）

`docs/architecture/layers.yaml` 当前由 **PR #414**（head `88484a70edfa2ed25ce2f2358f5d9fa95091d693`，登记 `serverosd: L1`）拥有，且 #414 的合并次序在 #412 之后。本 PR 是 docs-only，**不改、不覆盖、不与 #414 双写**。

若将来本链条真的需要新登记（例如 §3.3 的三条规则要进 `rules`），交接内容固定为：

```text
接手 PR：<新 PR>
要改的键：layers.yaml → rules[]（新增 3 条依赖规则）
前置：本文件 §3.3 已被评审接受
不得：与 #414 同 PR 修改
```

### 12.3 与在途 PR 的边界

| PR | 边界 |
|---|---|
| #407 / #416（L2 交付链） | 不并入本链条的实现；`fleet_mcp/` 的 `events.py` / `run_watcher.py` 在 `main` 上**不存在**，本合同只按「候选」引用 |
| #412 / #419（L1 serverosd、native-runtime 发布物） | 本文件只引用其**发布物 kind 名**作为溯源字段（§13.1、§13.4），不引用其代码 |
| #413（构建拆分）、#410（L4 Skills）、#415（Lark 测试） | 零重叠 |
| #414（layers.yaml） | 见 §12.2 |

---

## 13. 版本溯源：四种「存在」不是一回事

### 13.1 四个层次

| 概念 | 是什么 | 不能证明什么 |
|---|---|---|
| **GitHub canonical `main`** | 唯一权威源码 | 任何设备装了它 |
| **源码 worktree** | 某人某地的一次 checkout，可能落后、可能脏、可能带未推送提交 | 那就是 main；也不能假定三台机器路径相同 |
| **独立发布物** | 一个 exact SHA 构建出的产物（`node-runtime`、ability plugin、connector、hub、commander plugin、里程碑适配器），各有 `SOURCE_SHA` / kind / version 或 digest | 已安装 |
| **installed digest** | 设备上**实际**在跑的那个产物的 digest | 等于源码最新 |

**一句写死的话：源码 checkout 在当前 `main`，不等于服务已更新。**

### 13.2 每台设备自己报告

设备事实以**该设备自己的只读清单**为准（#418 的 P 线），本合同不代替、不推断、不合并成「大概是这样」。清单必须包含：`device_ref`、OS principal、clone 根、remote、分支、`HEAD`、dirty/untracked、worktree、测试运行器可用性、已安装各组件的 digest 或 `NOT_OBSERVED`、以及该设备**到底有没有**这个组件（而不是只有源码）。

### 13.3 当前状态

| 设备 | 里程碑适配器 | 本合同的观测 |
|---|---|---|
| 东京 Linux | `NOT_INSTALLED` | 未采集（`NOT_COLLECTED`） |
| Dell Linux | `NOT_INSTALLED` | 未采集（`NOT_COLLECTED`） |
| FJPCOS Windows | `NOT_INSTALLED` | 未采集（`NOT_COLLECTED`） |

「未采集」不是「没问题」。这张表在 P 线回填之前，只能用这三个词。

### 13.4 每个产物必须报的六项

`SOURCE_SHA` · kind · version/digest · 兼容的 CloudCLI/provider schema · 安装路径 · 上一个 digest 与回滚方式

外加一个验收状态（§1）：`CODE` / `HOSTED_CI` / `REAL_DEVICE` / `CLIENT_E2E` / `PRODUCTION`。**Agent 会话的 worktree 可以和 main 不同；发布物只能从精确评审过的 SHA 构建。**

---

## 14. V2 兼容性

本合同按 V1/V2 架构预留的接口设计，V2 应当只**加**：

| V2 变化 | 本合同的应对 |
|---|---|
| 多 IM 渠道 | 只新增一个出站适配器；`delivery_state` / `provider_receipt` 语义不变 |
| 多指挥者 | 每个 principal 各有游标（现有 `fleet_inbox` 设计已按 principal 分游标） |
| 层层管理（总管 / 分管 / 专家） | 管理层只是新的订阅者，不成为新的事实来源 |
| 自主等级升级 | 影响「哪些里程碑需要打扰」，不影响判定条件本身 |

**V1 明确不做**（做了就是改变 V2 的边界）：总管自动唤醒、指令抢占、凭据分发、常驻 LLM 轮询、把飞书做成入站决策权威。这些仍是 V2 议题（见 V1/V2 架构 §7）。

---

## 15. 尚未成立的工程 Gate

| Gate | 状态 | 缺什么 |
|---|---|---|
| 本合同 | `SPEC_PROPOSED` | 评审接受 |
| 单元测试（mock CloudCLI + mock GitHub，无真飞书） | `NOT_RUN` | 尚无代码 |
| Hosted CI 收集到该测试 | `NOT_RUN` | 尚无测试文件；收集路径需 CI 线确认 |
| 三设备只读清单 | `NOT_COLLECTED` | P 线回填 |
| 真机 E2E（`REAL_DEVICE`） | `NOT_RUN` | 未授权 |
| 单次真实飞书送达 + 回执回读（`CLIENT_E2E`） | `NOT_RUN` | 未授权 |
| 发布物溯源（§13.4 六项） | `NOT_RUN` | 依赖 L1/L2 发布路径 |
| 目录守卫 | 通过（现有） | 本 PR 不新增顶层目录，无需变化 |
| 基线闸门 | 严格（现有） | 不得为了变绿往 `tests/hermetic-known-failures.txt` 添条目 |

---

## 16. 怎么改这份合同

1. **加一个 `milestone_kind`** 或改判定前置条件 = 改本文件 + 在同一 PR 里更新契约测试。只改代码不改这里，等于让代码单方面重定义里程碑。
2. **加一条依赖方向** = 先在 §3.3 写清楚，再走 §12.2 的交接把它变成 `layers.yaml` 的 `rules`。
3. **改变「谁拥有某条事实」** = 必须同时改动 §2 表与至少一个现有权威文档的对应小节；不允许只在实现里换来源。
4. **状态词只增不减地收缩**：`OWNER_READ` 这类观测不到的词不能因为「看起来方便」被加回来。
5. 本文与 `notification-contract.yaml` 冲突时，事件词汇以 YAML 为准，本文改。
