# Fleet V1 里程碑投递合同

> **这篇回答什么：** Fleet V1 里「一件工作到了一个值得 owner 知道的里程碑」这件事，由谁观测、谁判定、谁投递、投给谁、失败时降级成什么，以及每一条事实的唯一来源。它是 [Fleet 四层](FLEET-FOUR-LAYERS.md) 与 [Fleet V1/V2 架构](FLEET-V1-V2-ARCHITECTURE.md) 之上的一层**交付保证**，不是第五个子系统。
>
> **状态：`SPEC_PROPOSED`（合同本身）；代码存在但只是 Draft，未合并、未部署；设备 `NOT_INSTALLED`**（2026-10-27 修订）。本合同描述的适配器、真源注入与观察器**在 `main` 上都不存在**，只作为 Draft PR 存在（#423 / #426 / #428，见 §15）；三台设备的实际状态见 §13，**没有任何设备被更新，本文不宣称任何设备已更新**。
>
> **修订说明（2026-10-27，回应 [#422 审查](https://github.com/ProfessorZhi/serveros/pull/422#issuecomment-6440359323)）**：§5 原先的错误是把「派发/投递生命周期位置」写进了 `milestone_kind`。现拆成三根正交轴（`milestone_kind` / `delivery_state` / `outcome`），词表**与 #423 `86e49992` 的真实常量逐字对齐**；§15 也从「尚无代码」改为按 Draft 实况记录。
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
| 三个词表的**语义与互斥关系**（§5） | 词表在代码里的**常量名与取值实况**（#423 `milestone_notify.py`） |
| 目录归属与本合同的后续变更规则 | 层登记表本身（[layers.yaml](layers.yaml)，由 #414 拥有） |

**冲突时的裁决顺序：** 事件词汇与句子归 `runtime/notification-contract.yaml`；层与目录归属归 `layers.yaml`；V1/V2 需求边界归 `FLEET-V1-V2-ARCHITECTURE.md`。本文**只解释如何把它们组合成一条投递保证，不重新定义其中任何一个**。本文若与它们冲突，是本文错。

**但词表有一处例外必须写清：** §5 的三根轴里，`milestone_kind` 与 `outcome`、`delivery_state` 的**具体取值**已由 #423 的实现固定。本文不能凭「更合理」去改它们——本文只能**描述**它们，并在两者不一致时按 §5.6 登记差异、按 §16 走变更。本文与 #423 常量冲突时，**以合并进 `main` 的那一份为准**；在那之前，以 #423 当前 head 为准并标 `PENDING_RECONCILE`。

**本文不新造一套「红绿灯授权」**。现有通知契约已经有两个正交轴（事件严重度 `INFO/ATTENTION/ACTION`，以及告警自身 `low/medium/high` → 人读到的词），本合同 §6 只在它们之上规定「什么算里程碑、什么状态下允许打扰人」，不引入第二套颜色—权限映射。

---

## 1. 只用两个状态词

本合同对「这件事做到哪一步」只允许这两个词，别的一律视为未声明：

| 词 | 含义 | 能推出什么 | 推不出什么 |
|---|---|---|---|
| `SPEC_PROPOSED` | 只有文档/设计，源码未合并到 `main` | 有人写下了规则 | 任何一行为已经存在 |
| `NOT_INSTALLED` | 源码可能有（例如只在 Draft PR 上），设备上**没有**该产物 | 该设备没有它（或者根本不知道有没有） | 「装了但没过」或「过了但没装」；也**不能**推成「装的是旧版」 |

**验收状态**（产物的，不是文档的）单独用这五个词，按顺序不可跳级、不可互相替代：

`CODE` → `HOSTED_CI` → `REAL_DEVICE` → `CLIENT_E2E` → `PRODUCTION`

一个模块有单元测试 = `CODE`；hosted CI 绿 = `HOSTED_CI`；真机上跑过 = `REAL_DEVICE`；真的在飞书客户端上收到 = `CLIENT_E2E`；owner 在生产里依赖它 = `PRODUCTION`。**本合同自身处在 `SPEC_PROPOSED`**（它是一份合同，永远不「安装」）。

**Draft PR 上的代码怎么记（本文统一写法）**：`SPEC_PROPOSED` + 验收状态 `CODE`（该 PR 自己的 hosted CI 绿时再加 `HOSTED_CI`），并显式追加 **`未合并 / 未部署`**。这不是第三种状态词，是同一套词的正确组合：`SPEC_PROPOSED` 说的是「`main` 上没有它」，`CODE` 说的是「Draft 里有单元测试」，`NOT_INSTALLED` 说的是「设备上没有」。

因此本轮的真实组合是：

| 对象 | 状态 | 证据 |
|---|---|---|
| 本合同（本文） | `SPEC_PROPOSED` | Draft PR #422 |
| 投递适配器 / 真源 / 观察器 | `SPEC_PROPOSED` + `CODE`，未合并、未部署 | Draft #423 `86e49992` · #426 `6781b3aa` · #428 `b4a62092` |
| 三台设备上的产物 | `NOT_INSTALLED` + 未采集 | §13.3（无任何设备被更新） |

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
| **里程碑判定（三根轴的语义）** | **本合同 §5** | 观察器、Agent、卡片状态文本 |
| **三个词表的取值实况** | #423 `integrations/fleet-v1-milestone-notify/milestone_notify.py` 的模块常量（Draft，未合并） | 本合同擅自「优化」词表；投递后的卡片文案 |
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

先给总图，**每条箭头单独一行**，避免把「读」和「被读」画成双向：

```text
L4  ──MCP──────────────────▶  L2-hub
L2-hub ──节点协议──────────▶  L2-node
L2-node ──────────────────▶  CloudCLI（原生 Run 事实）
L4、L2  ──身份/授权查询───▶  L0
L1  ──bootId / 本机事实───▶  L0
```

本文只**新增**下面三条方向，它们都是上图的细化，**没有任何一条引入新的依赖方向**：

```text
L2 观察者 ──候选事实──▶ product 投递者 ──▶ 东京唯一 Feishu 出口
（只产事实，不渲染）      （只渲染与投递，不产事实）
product ──事件──▶ L3 摘要 ──可选──▶ product（缺席则走确定性模板）
```

也就是：`product` 依赖 `L3` 的是**可选**的被调用方；`L3` 反过来不依赖 `product` 的事实产生路径。

**注意图中没有 `L1 → L3` 这条箭头。** L1 只产本机事实（并与 L0 对账），**不直接激活 L3**；L3 由 product 侧的事件激活。这与 §3.1 说 L3「被事件激活、**不观测**」是同一件事——上一版把它画成 `L1 ──观测──▶ L3`，与 §3.1 矛盾，已删。

**唯一被禁止的方向**（负向对照，写下来是为了能被核查）：

```text
L1 ──不依赖──▶ L2 / L3 / product / 东京出口     （本机自愈必须断网可用，不等 Agent）
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
② 派发回执（`delivery_state = DISPATCH_ACK`）← 只证明「排队了」。
      │  这是**派发生命周期**的位置，不是里程碑：指挥端到这里就可以离开。
      ▼
③ L2 观察者（有界、无 LLM、可从中断处恢复）
      │  原生 run_get / 会话事件；GitHub Actions 运行记录或有界轮询
      ▼
④ Run/CI 事实（`milestone_kind = RUN_COMPLETED` / `CI_PASSED`）
      │  有可信原生证据才算；否则 `outcome = UNKNOWN`
      ▼
⑤ 里程碑判定（本合同 §5）→ 大多数变化停在这里：卡片静默更新
      │  只有「重要的一次」或「必须有人动手」才往下去
      ▼
⑥ L3 caretaker Agent 被事件激活：写摘要（可选）
      │  模型不可用 → 确定性模板，直接跳到 ⑦
      ▼
⑦ product 投递者：封 delivery_key，把账本置为 IN_FLIGHT
      │
      ▼
⑧ 东京唯一出口 → Feishu App 1 / lark-cli → owner 会话
      │
      ▼
⑨ 记 provider_receipt 与 `delivery_state`（`DELIVERY_UNKNOWN` ≠ `DELIVERED`）
```

四件事写死在这条链上：

- **② 和 ④ 之间可能隔几个小时。** 这正是这条能力存在的理由；任何让 ② 等到 ④ 的实现都违反本合同。**② 与 ④ 在词表里不属于同一根轴**（§5）：② 是 `delivery_state` 上的派发位置，④ 是 `milestone_kind` 上的业务节点，`DISPATCH_ACK` 永远不能当里程碑记账。
- **⑤ 是漏斗最窄的一处。** 绝大多数 Run 的结束不是里程碑（`ORDINARY_GREEN` → 只改卡片，`SILENT` → 不入账），只有 `VERIFIED_GREEN` 或红/灰才往下去。
- **⑥ 可以缺席。** 缺席不是降级到「不发」，而是降级到「用模板发」。
- **⑨ 的 `DELIVERY_UNKNOWN` 不能被写成 `DELIVERED`**（§9）。发送调用返回成功，只说明我们发出去了，不说明有人收到；`DELIVERED` 只代表 provider 回执点了名，**不代表 `OWNER_READ`**。

---

## 5. 三个封闭词表：业务节点 / 投递生命周期 / 颜色判定

一条里程碑事实带上三根**正交**的轴。**任一词不能跨轴借用**——这是本合同上一版最严重的错误（把 `DISPATCH_ACK`、`FEISHU_DELIVERED` 写进 `milestone_kind`），也是本节重写的全部理由。

| 轴 | 回答的问题 | 取值来源 |
|---|---|---|
| `milestone_kind` | 这次观测**针对哪个业务节点** | #423 模块常量 + 规范化器（`milestone_notify.py:77-81`、`550-582`） |
| `delivery_state` | 这次**投递尝试**走到哪一步 | #423 账本状态常量（`milestone_notify.py:86-92`） |
| `outcome` | 这条事实该算**什么颜色** | #423 判定器（`milestone_notify.py:70-75`） |

[#418](https://github.com/ProfessorZhi/serveros/issues/418) 要求「distinguish `DISPATCH_ACK`, `RUN_ENDED`, `CI_PASSED`, `QA_ACCEPTED`, `FEISHU_DELIVERED`（不是 `OWNER_READ`）」——**这五个词并不都在同一根轴上**。正确的落地方式是 §5.4 的映射表，而不是把它们塞进同一个列表。

### 5.1 `milestone_kind`：可证实的业务节点（唯一能被称为「里程碑」的轴）

封闭词表。**它命名的是「这次观测针对哪个业务节点」，不命名「这个节点重要不重要」**——重不重要由 `outcome`（§5.3）决定。同一 `milestone_kind` 可以带绿、黄、红、灰四种 outcome。

**成功向**（模块常量，`milestone_notify.py:77-81`），取值由**源**决定，调用方不能自由填：

| `milestone_kind` | 由哪个源给出 | `key` 的形状 | 语义 |
|---|---|---|---|
| `RUN_COMPLETED` | `cloudcli`（固定，不读负载） | `run_id` | 一轮 Run 到了**原生终态** |
| `CI_PASSED` | `github_actions`（固定，不读负载） | `<workflow name>:<run_id>` | 一个 workflow run 完成了；SHA 只进 `refs` |
| `PHASE_ACCEPTED` | `phase`（负载 `milestone_kind` 三选一） | 负载的 `key`（如 `v1-wave2`） | 一个**阶段**被独立签收 |
| `PR_ACCEPTED` | `phase`（同上） | 同上 | 一个**已签收的 PR**（§5.5） |
| `TASK_COMPLETED` | `phase`（同上） | 同上 | 一个**任务**被签收 |

`_PHASE_KINDS = frozenset({PHASE_ACCEPTED, PR_ACCEPTED, TASK_COMPLETED})`（`milestone_notify.py:108`）——负载给别的值直接 `MilestoneRefused`（`:386`）。

**失败 / 未知向**（字面量，`milestone_notify.py:550-582`，由判定器按源派生）：

| 源 | 黄 | 红 | 灰 |
|---|---|---|---|
| `cloudcli` | — | `RUN_FAILED` · `RUN_CANCELLED` | `RUN_OUTCOME_UNKNOWN` |
| `github_actions` | `CI_NOT_PASSED` | `CI_FAILED` · `CI_ACTION_REQUIRED` | `CI_OUTCOME_UNKNOWN` |
| `phase` | `PHASE_AWAITING_ACCEPTANCE` | `PHASE_REJECTED` | `PHASE_UNVERIFIED` |

**明确不在本轴上的词：**

| 词 | 实际属于 | 为什么不能当 `milestone_kind` |
|---|---|---|
| `DISPATCH_ACK` | `delivery_state`（派发阶段，早于账本） | 它只证明**排队**，不证明任何业务节点成立 |
| `FEISHU_DELIVERED` | `delivery_state == DELIVERED` | 它是**我们自己的投递结果**，不是被观测对象的事实 |
| `OWNER_READ` | 任何轴都不许有 | 出站通道拿不到已读回执，写进去就是宣称观测不到的事实 |
| `RUN_ENDED` | 本轴上叫 `RUN_COMPLETED`（失败则 `RUN_FAILED` / `RUN_CANCELLED`） | 口头词，代码里没有这个常量 |
| `QA_ACCEPTED` | 本轴上细分为 `PHASE_ACCEPTED` / `PR_ACCEPTED` / `TASK_COMPLETED` | 口语词；代码按签收粒度分成三个 |
| `ARTIFACT_PRODUCED` | **不属于任何轴**（§5.5） | 产出物不是业务节点，登记它等于给「打开一个 PR」发里程碑 |

### 5.2 `delivery_state`：派发与投递生命周期

封闭词表。只有 `DELIVERED` 代表「provider 点了名」。

| 状态 | 谁写 | 能推出 | 推不出 |
|---|---|---|---|
| `DISPATCH_ACK` | L4 / L2 派发路径（**先于账本，尚未在 #423 里**） | 已入队 | 开始执行；更推不出任何业务节点成立 |
| `IN_FLIGHT` | 投递者（认领后、发送前） | 键已封、可能已发出 | 是否送达 |
| `DELIVERED` | 账本（provider 返回了消息标识） | 通道点名了一条消息 | **人读过**（≠ `OWNER_READ`） |
| `DELIVERY_UNKNOWN` | 账本（发送异常 / 无回执） | 我们不知道 | 失败（也推不出成功） |
| `CARD_ONLY` | 账本（`deliver=False` 且非 `SILENT`） | 只改了卡片/摘要 | 有人被告知 |
| `NO_EVENT` | 账本（`SILENT`） | 没有任何值得记的变化 | 降级、故障或已被处理 |
| `DUPLICATE` | 账本（同键同 outcome 已投递过） | 本次没有重发 | 上一次真被人看到 |
| `UNKNOWN_REPLAY` | 账本（上一次状态未知，本次是重放） | 我们**没有盲发** | 已送达 |

`DISPATCH_ACK` 是 owner 口头词里**唯一没有账本行**的那个：它在 L2 派发回执里，与 §4 的 ② 对应。

### 5.3 `outcome`：颜色判定（六值）

| `outcome` | 打扰 | severity | 账本落点 | 含义 |
|---|---|---|---|---|
| `VERIFIED_GREEN` | **是，一次** | `INFO` | `IN_FLIGHT` → `DELIVERED` / `DELIVERY_UNKNOWN` | 一个**重要**的业务节点被证实达标 |
| `ORDINARY_GREEN` | 否 | `INFO` | `CARD_ONLY` | 成功，但**不是**里程碑（日常 tick、非闸门 CI） |
| `YELLOW` | 否 | `ATTENTION` | `CARD_ONLY` | 可恢复、待观察 |
| `RED` | **是，立即** | `ACTION`（需人动手）/ `ATTENTION` | `IN_FLIGHT` → `DELIVERED` / `DELIVERY_UNKNOWN` | 必须有人决定 |
| `UNKNOWN` | **是，立即** | `ACTION` | `IN_FLIGHT` → `DELIVERED` / `DELIVERY_UNKNOWN` | 证据不足，**绝不能当绿** |
| `SILENT` | 否 | 无 | `NO_EVENT` | 还在跑 / 无变化 |

`ORDINARY_GREEN` 与 `SILENT` 必须与 `VERIFIED_GREEN` 分开（设计理由见 `milestone_notify.py:66-69`）：**一次成功的日常 tick 不是里程碑；一个仍在跑的源还没有回答。** 把两者混进「绿」，会让「重要绿」贬值到无人再看。

（顺带记一笔：`:67` 的注释写「The four answers a milestone can have」，而该处实际有**六个**取值。以代码为准，本合同按六值描述。）

### 5.4 owner 口头词 → 轴 的映射（替换 #418 那句并列列表）

| 口头词 | 实际发生的事 | 落在哪根轴 | #423 里的对应 |
|---|---|---|---|
| `DISPATCH_ACK` | 派发已入队 | `delivery_state`（派发阶段，先于账本） | 不适用（代码从 Run 事实开始观测） |
| `RUN_ENDED` | 一轮 Run 到了终态 | `milestone_kind` | `RUN_COMPLETED` / `RUN_FAILED` / `RUN_CANCELLED` |
| `CI_PASSED` | 精确 SHA 上的 workflow run 完成 | `milestone_kind` | `CI_PASSED`；未通过侧见 §5.1 第二张表 |
| `QA_ACCEPTED` | 阶段 / PR / 任务被**独立签收** | `milestone_kind` | `PHASE_ACCEPTED` / `PR_ACCEPTED` / `TASK_COMPLETED` |
| `FEISHU_DELIVERED` | provider 给了回执 | `delivery_state` | `DELIVERED` |
| `OWNER_READ` | — | **不存在** | 代码里也没有；观测不到就不许记账 |

### 5.5 已签收的 PR ≠ 草稿 PR ≠ CI 绿

这三件事最容易被合并成一句「都提交了 = 绿」。本合同按 #423 的 phase 规范化（`normalize_phase_milestone`，`milestone_notify.py:380-400`）划死：

| 状态 | 判定 | outcome | 是否打扰 |
|---|---|---|---|
| 打开 / 更新一个 PR（**含 Draft**） | `ARTIFACT_PRODUCED`——**不是任何 `milestone_kind`** | `SILENT` | 否 |
| CI 在精确 SHA 上全绿，但没人签收 | `milestone_kind = CI_PASSED` | 负载 `milestone=true` → `VERIFIED_GREEN`；否则 `ORDINARY_GREEN`（`milestone_notify.py:649-653`） | 仅 `VERIFIED_GREEN` |
| PR 被**独立于实现者**签收，且带证据 | `milestone_kind = PR_ACCEPTED` | `VERIFIED_GREEN`（phase 分支不设 `milestone` 门控，`:655-662`） | **是，一次** |

`PR_ACCEPTED` / `PHASE_ACCEPTED` / `TASK_COMPLETED` 的前置，**逐条标注代码是否真的强制**（这是本节最容易被过度宣称的地方）：

| 前置 | 代码强制？ | 位置 / 说明 |
|---|---|---|
| `accepted == true` | ✅ 是 | 否则黄 `PHASE_AWAITING_ACCEPTANCE`（`:656-659`） |
| `accepted_by` 非空 | ✅ 是 | `accepted` 为真时必填（`:393-394`） |
| `evidence` 至少一条 ref | ✅ 是 | `:395` 直接 `MilestoneRefused` |
| `source_trusted == true` | ✅ 是 | 否则灰 `PHASE_UNVERIFIED`（`:660-661`） |
| `accepted_by` **不是实现者本人** | ❌ **否** | 代码只知道「谁签的」，不知道「谁是实现者」——见 §5.6 第 6 条 |
| `gates_required ⊆ gates_passed` | ❌ **否** | phase 分支不查 gates（cloudcli / github 分支才查，`:624-625`、`:647-648`）——见 §5.6 第 6 条 |

**草稿 PR 为什么不够**：Draft 的含义是**作者自己还没请求验收**，所以它既没有独立签收人，也不满足「有证据的签收」。合同在此明确：**Draft PR 永远不能当成 `PR_ACCEPTED`**。

**但要说清举证现状**：phase 负载里**没有 `draft` 字段**，代码也无法分辨一个 PR 是不是 Draft。这条约束目前**只由本节与评审守**，没有测试守住——已登记在 §5.6 第 4 条。

### 5.6 与 #423 现状的差异登记（`PENDING_RECONCILE`）

本合同上一版凭「更合理」写了六处与真实代码不一致的地方。本轮一律**采纳代码**（因为代码已被独立审查、且 #426/#428 叠在它上面），差异在此留痕：

| # | 上一版合同写法 | #423 `86e49992` 的实况 | 本轮处置 |
|---|---|---|---|
| 1 | `milestone_kind` 含 `DISPATCH_ACK` / `FEISHU_DELIVERED` / `RUN_ENDED` / `QA_ACCEPTED` | 五常量 + 十字面量，见 §5.1 | 改成三轴 + §5.4 映射表；旧词不再出现在本轴 |
| 2 | `delivery_key` 含 `source_sha` | `milestone:{source}:{device_ref}:{milestone_kind}:{key}:{outcome}`（`:695-705`），**SHA 只进 `refs`** | §9.2 已改为真实公式；推论：新一次 CI 由新 `run_id` 表达，不由 SHA 表达 |
| 3 | 灰（`UNKNOWN`）「起初否，按年龄升级」 | `UNKNOWN → deliver=True, severity=ACTION`（立即，`:576-588`） | §5.3 / §6 改为「立即 ACTION」；「按年龄升级」只保留给**告警面**（`runtime/alerting.py`），不适用于里程碑投递 |
| 4 | 未规定 | phase 负载无 `draft`，代码分辨不出草稿 PR | §5.5 写出约束并标缺口；待 #423 作者确认（#417 同步） |
| 5 | 未规定 | 失败/未知 kind 是**字面量**，不是导出常量（`:550-582`） | 记为治理缺口，见 §16 第 6 条 |
| 6 | 签收必须「独立于实现者」且「验收条目逐条有证据」 | 代码只强制 `accepted` / `accepted_by` 非空 / `evidence` 非空 / `source_trusted`；**不校验独立性，phase 分支也不查 `gates_required ⊆ gates_passed`** | 合同**保留**这条要求（它是 `PR_ACCEPTED` 的语义），但 §5.5 已逐条标注「代码未强制」；补测试是 #423 的后续项，不在本轮文档 PR 内 |

**这三根轴的改动必须同批**：改本文件 + 改 #423 常量 + 改契约测试，缺一不许合。**当前没有「文档词表 == 代码常量」的一致性测试**（#420 只检查文件存在与形状，不解析词表），所以第 1–6 条在本轮靠人工对照留痕。

三条判定例子（保留，词已按 §5.4 校正）：

| 现象 | 正确判定 | 常见错误 |
|---|---|---|
| 四个 Draft PR 被打开 | 不属于任何轴（`ARTIFACT_PRODUCED`，`outcome = SILENT`） | 「四部分都提交了 = V1 绿」 |
| GitHub CI 矩阵全绿 | `milestone_kind = CI_PASSED`，`outcome` 视 `milestone` 门控 | 「CI 绿 = 可以发布」 |
| `run_get` 显示 completed 但没有可信原生结果 | `milestone_kind = RUN_OUTCOME_UNKNOWN`，`outcome = UNKNOWN` | 「completed = 成功」 |

**`UNKNOWN` 永不升级为绿。** 它有自己的颜色与提醒等级（§5.3），不按「看起来还行」升级。

---

## 6. 红 / 黄 / 灰 / 绿 的处置

分色只管**打扰谁、何时、几 次**；不管「这件事多严重」（那是 `low/medium/high` 的词）。两轴不要合并成一个数字。

| 色（= `outcome`） | 判据 | 打扰人？ | 出口 | 关闭条件 |
|---|---|---|---|---|
| **重要绿** `VERIFIED_GREEN` | §5 的前置全满足，**且**该批是里程碑（CI/Run 需负载 `milestone=true`；签收类无条件算） | **是，恰好一次** | 飞书卡片 | 收到回执后关闭；重复观察不再发 |
| **普通绿** `ORDINARY_GREEN` | 前置全满足，但不是里程碑（日常 tick、非闸门批次） | 否 | 只改卡片（账本 `CARD_ONLY`） | 下一次卡片更新自然覆盖 |
| **黄** `YELLOW` | 可恢复、待观察、未签收（`PHASE_AWAITING_ACCEPTANCE`）、CI 没产出通过（`neutral/skipped/stale`） | **否** | 只进卡片与周期摘要（账本 `CARD_ONLY`） | 下一次摘要自然覆盖 |
| **红** `RED` | 需要 owner 动作才能继续（`CI_ACTION_REQUIRED`、失败、不可恢复故障） | **是，立即** | 飞书卡片，带动作与影响面 | 可靠解除**或** owner 明确确认；不由超时自动关闭 |
| **灰** `UNKNOWN` | 证据不足：Run 终态无原生输出/`bootId` 不符/必需 gate 未举证、CI 结论不可读、源不可信 | **是，立即**（`severity=ACTION`） | 飞书卡片，明确写「不许当成功」 | 补到证据为止；**永不当绿** |
| **静默** `SILENT` | 还在跑（未到终态）、源不可信、无变化 | 否 | 不记账（`NO_EVENT`） | — |

**灰为什么是立即而不是「先观察」**：这是 #423 的现行判定（`_unknown` → `deliver=True, severity=ACTION`），比本合同上一版的「起初否、按年龄升级」更严。本合同采纳更严的一侧——灰的含义是**我们不知道**，而「不知道自己不知道」的时间越长，越容易被人当成绿。**「按年龄升级」仍然有效，但它只属于告警面**（`runtime/alerting.py` 的 `OPEN/ACKNOWLEDGED/SNOOZED/RESOLVED`），不属于里程碑投递。

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
| `milestone_kind` | 事实 | 判定层；取值由**源**决定或由 phase 负载三选一（§5.1） | 自由文本；`DISPATCH_ACK` / `FEISHU_DELIVERED` / `OWNER_READ`（§5.4） |
| `outcome` | 判定 | 判定层（§5.3 六值） | 由投递结果回填（`DELIVERED` 是 `delivery_state`，不是 `outcome`） |
| `observed_at` | 事实 | 观察者 | 人读时间 |
| `source_sha` | 事实 | L2 / GitHub | 「当前 main」这类浮动引用 |
| `evidence_ref` | 事实 | 观察者 | 原始提示词、token、transcript |
| `decision` | 判定 | 判定层 | 由投递方回填 |
| `delivery_key` | 判定 | 投递者（稳定幂等键） | 时间戳、pid、重试次数 |
| `provider_receipt` | 判定 | 出站通道 | 猜测值；拿不到就是 `UNKNOWN` |
| `delivery_state` | 判定 | 投递者 | 由「发送没报错」推断的 `DELIVERED` |

**三个封闭词表，各自不许越界**（取值见 §5）：

```text
milestone_kind : RUN_COMPLETED · CI_PASSED · PHASE_ACCEPTED · PR_ACCEPTED · TASK_COMPLETED
               + RUN_FAILED · RUN_CANCELLED · CI_FAILED · CI_ACTION_REQUIRED · CI_NOT_PASSED
               + PHASE_REJECTED · PHASE_AWAITING_ACCEPTANCE
               + RUN_OUTCOME_UNKNOWN · CI_OUTCOME_UNKNOWN · PHASE_UNVERIFIED
delivery_state : IN_FLIGHT · DELIVERED · DELIVERY_UNKNOWN · CARD_ONLY · NO_EVENT
               + DUPLICATE · UNKNOWN_REPLAY
               + DISPATCH_ACK（派发期；本波尚无账本行，见 §5.2）
outcome        : VERIFIED_GREEN · ORDINARY_GREEN · YELLOW · RED · UNKNOWN · SILENT
```

**`OWNER_READ` 不在这三个词表里，也不许出现。** 出站通道拿不到已读回执；把它写进去等于宣称一个我们观测不到的事实。`FEISHU_DELIVERED` 只是 `delivery_state == DELIVERED` 的人读说法——**provider 点了名，不等于有人读过**。

一次 V1 阶段验收的载荷（示例，非真实数据；字段名与 #423 的 `phase` 信封一致）：

```json
{
  "work_ref": "serveros:v1-four-lanes",
  "source_ref": "ProfessorZhi/serveros@<sha>",
  "session_ref": "<session>",
  "runId": "<run>",
  "deliveryId": "<dispatch>",
  "bootId": "<boot>",
  "milestone_kind": "PHASE_ACCEPTED",
  "key": "v1-wave2",
  "device_ref": "device:<device>",
  "observed_at": "2026-10-27T00:00:00Z",
  "source_sha": "<sha>",
  "evidence_ref": ["pr:#413@<sha>", "pr:#410@<sha>", "ci:<run-id>"],
  "accepted": true,
  "accepted_by": "<独立于实现者的签收人>",
  "source_trusted": true,
  "outcome": "VERIFIED_GREEN",
  "decision": "deliver_once",
  "delivery_key": "milestone:phase:<device_ref>:PHASE_ACCEPTED:v1-wave2:VERIFIED_GREEN",
  "provider_receipt": null,
  "delivery_state": "IN_FLIGHT"
}
```

`provider_receipt: null` 与 `delivery_state: "IN_FLIGHT"` 一起出现，是在陈述**我们只知道已经封了键、还没拿到回执**。这就是诚实的写法：拿不到回执时它只能是 `DELIVERY_UNKNOWN`，永远不能是 `DELIVERED`。

---

## 8. 异步 ACK 与可靠验收的差别

| 位置 | 在哪根轴 | 证据 | 能推出 | 推不出 |
|---|---|---|---|---|
| `DISPATCHED` | 派发（L4 侧，无账本行） | L4 调用了派发工具 | 我们发出了请求 | 节点收到 |
| `DISPATCH_ACK` | `delivery_state`（L2 派发回执） | `session_ref` + `run_id`/`queued_message_uuid` + `accepted` | 已入队 | 开始执行；**任何业务节点成立** |
| `SILENT`（`RUN_NOT_TERMINAL`） | `outcome` + `delivery_state=NO_EVENT` | 原生会话/`run_get` 显示还在跑 | 在做 | 会做完 |
| `RUN_COMPLETED` + `VERIFIED_GREEN` | `milestone_kind` + `outcome` | 原生终态 + 可信输出 + `bootId` 相符 + gate 齐 | 这一轮结束且达标 | 发布验收 |
| `RUN_OUTCOME_UNKNOWN` + `UNKNOWN` | `milestone_kind` + `outcome` | 终态缺失 / 原生输出不可信 / boot 不符 / gate 缺证 | 我们不知道 | 失败（也推不出成功） |
| `CI_PASSED` + `VERIFIED_GREEN`/`ORDINARY_GREEN` | `milestone_kind` + `outcome` | 精确 `head_sha` 上该 workflow run 的 conclusion 为 success | 那批检查过了 | 发布验收、有人签收 |
| `PR_ACCEPTED` / `PHASE_ACCEPTED` / `TASK_COMPLETED` | `milestone_kind` | `accepted` + `accepted_by` + 至少一条 evidence + trusted（§5.5） | 这一阶段被签收 | 已投产；**也不能**证明签收人独立于实现者（§5.6 第 6 条） |
| `DELIVERED` | `delivery_state` | provider 返回了消息标识 | 我们确切交给了通道并被点名 | **owner 读过**（≠ `OWNER_READ`） |
| `DELIVERY_UNKNOWN` | `delivery_state` | 发送异常或回执缺失 | 我们不知道送没送到 | 失败（也推不出成功） |

这张表的核心一句：**`DISPATCH_ACK` 是这条链上最容易被当成「完成」的位置，而它只证明排队**——它在 `delivery_state` 轴上，**不在 `milestone_kind` 轴上**，所以它连「一个业务节点成立了」都证明不了。「消息送达」与「owner 知道」之间还有一道我们观测不到的墙，合同在这里止步。

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

**稳定**是唯一要求：同一件里程碑，在任何时间、任何进程、任何重试里都必须得到同一个键。**本条采用 #423 的真实公式**（`milestone_notify.py:695-705`），不再用本文上一版自己拟的形状：

```text
delivery_key = "milestone:" + source + ":" + device_ref + ":" + milestone_kind
                            + ":" + key + ":" + outcome
```

其中 `source ∈ {cloudcli, github_actions, phase}`，`key` 对 Run 是 `run_id`、对 CI 是 `<workflow name>:<run_id>`、对签收是负载的 `key`。

**不许**包含：时间戳、pid、重试次数、`deliveryId`（每次尝试不同）、随机会话 ID。这与现有告警身份规则同一个道理——身份由 `code + device_ref + subject_ref` 组成（定义在 `runtime/notification-contract.yaml:292`，实现在 `runtime/alerting.py:170` 的 `identity()`），且 `identity_is_not: [pid, container_name, timestamp]`。

**SHA 为什么不在键里**（这是本轮修正的第二处）：CI 的 `head_sha` 只进 `refs`，不参与 `key`。这不削弱幂等——**新一次 CI 是新的 `run_id`，因此天然是新键**；而同一次 CI 重放仍是同一个 `run_id`，因此收得住。把 SHA 塞进键会让「同一次 run 换了 head SHA」变成两次投递，那是重复而不是更严。

### 9.3 三个崩溃窗口

| 窗口 | 现象 | 正确行为 |
|---|---|---|
| 事件已写、投递未发生 | 重启后事件游标未推进 | 补投；若里程碑已过期（§11），记为 `SUPERSEDED` 而不发（该词目前只是本合同概念，#423 账本里没有；落地要么落 `NO_EVENT`，要么按 §16 新增状态） |
| 已发送、回执未落盘 | `provider_receipt` 缺失 | 标 `DELIVERY_UNKNOWN`，**按 key 先查重再决定是否重发**；不可盲发（#423 对应状态 `UNKNOWN_REPLAY`：证明我们**没有**盲发） |
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
| 飞书不可达 | `delivery_state = DELIVERY_UNKNOWN`，有限重试，卡片保留 | 把重试成功写成 `DELIVERED` |
| GitHub 不可达 | `CI_PASSED` 保持未知 | 用「本地跑过了」代替 CI 结论 |
| 全部不可用 | 什么都没有被声称；`UNKNOWN` 保持 `UNKNOWN` | 静默地把灰变绿 |

**补投的过期规则：** 恢复后补投时，只补仍然成立的里程碑。一个已经过期的「绿」不再发——发一条三天前的「全绿了」既不真也不有用，记 `SUPERSEDED` 即可。红色事件相反：它**一直有效到被解除或被 owner 确认**，所以补投必须发。

---

## 12. 目录归属

| 路径 | 层 / 归属 | 现状（`main` 上 / Draft 里） | 说明 |
|---|---|---|---|
| `integrations/fleet-v1-milestone-notify/` | `integrations: shared` | `main` 上**不存在**；Draft **#423 `86e49992`** 有实现 | 顶层 `integrations/` 已在 `layers.yaml` 登记为 `shared`，**新增其下的子目录不需要改 `layers.yaml`** |
| `integrations/fleet-v1-milestone-sources/` | `integrations: shared` | `main` 上**不存在**；Draft **#426 `6781b3aa`** 有适配器 | 同上；base 是 #423 的分支（stacked） |
| `integrations/fleet-v1-milestone-observer/` | `integrations: shared` | `main` 上**不存在**；Draft **#428 `b4a62092`** 有观察器 tick | 同上；base 是 #423 的分支（stacked） |
| `tests/test_fleet_v1_milestone_notify.py` · `..._sources.py` · `..._observer.py` | `tests: shared` | 各自随对应 Draft | 契约测试与实现同 PR |
| 任何新顶层目录 | — | 不新增 | 薄适配器不值得一个顶层目录；三个 Draft 都只在 `integrations/` 下加子目录 |

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
| **#423**（投递器 + 判定器，`86e49992`） | **本合同 §5 的三根轴以它的常量为准**；它是 Draft、未合并、未部署。本合同不改它的源码，只描述它。**双向引用缺口**：审查实测 #423 的 README 与代码对本合同 **0 引用**；补齐由 [#417 词表同步](https://github.com/ProfessorZhi/serveros/issues/417#issuecomment-6449735069) 推动，不在本 PR 内改它的文件 |
| **#426**（真源适配器，`6781b3aa`） | 提供 CloudCLI / GitHub Actions 两个只读适配器；**凭据/传输由外部注入**，因此「真实 `SourceRead` 注入」仍未接线（§15） |
| **#428**（观察器 tick，`b4a62092`） | `run_periodic(interval_seconds, sleep, max_ticks)` 只是**有界入口**，调用方拥有循环；**常驻调度、重启策略不在 #428 里**，也不在 RC #427 里 |
| #407 / #416（L2 交付链） | 不并入本链条的实现；`fleet_mcp/` 的 `events.py` / `run_watcher.py` 在 `main` 上**不存在**，本合同只按「候选」引用 |
| #412 / #419（L1 serverosd、native-runtime 发布物） | 本文件只引用其**发布物 kind 名**作为溯源字段（§13.1、§13.4），不引用其代码 |
| #413（构建拆分）、#410（L4 Skills）、#415（Lark 测试）、#420（存在性闸门） | 零重叠；#420 只检查文件与形状，**不校验 §5 的词表**（§5.6） |
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

| 设备 | 里程碑适配器（安装） | 本合同的观测 |
|---|---|---|
| 东京 Linux | `NOT_INSTALLED` | 未采集（`NOT_COLLECTED`） |
| Dell Linux | `NOT_INSTALLED` | 未采集（`NOT_COLLECTED`） |
| FJPCOS Windows | `NOT_INSTALLED` | 未采集（`NOT_COLLECTED`） |

**这里的 `NOT_INSTALLED` 是「根本没有」，不是「装了旧版」。** #423 / #426 / #428 只存在于 GitHub 的 PR 分支，**没有任何设备 clone 了它们**（本波未向任何设备分发）。同理，Draft 有单元测试也不等于 `REAL_DEVICE`。

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

## 15. 工程 Gate 实况（本轮修订：不再说「尚无代码」）

上一版这张表写「尚无代码 / `NOT_RUN`」，与本轮已经存在的三个 Draft 冲突。改为按**逐个产物**记，并把「有代码」「有 CI」「能真跑」拆开。

### 15.1 三个 Draft 现在到底有什么

| PR | exact SHA | 路径 | 单元测试 | 状态 |
|---|---|---|---|---|
| #423 投递器 + 判定器 | `86e49992` | `integrations/fleet-v1-milestone-notify/` | **31 个用例 / ~145 条断言** | **Draft CODE+UNIT，未合并 / 未部署** |
| #426 真源适配器 | `6781b3aa` | `integrations/fleet-v1-milestone-sources/` | **30 / ~70** | **Draft CODE+UNIT，未合并 / 未部署** |
| #428 观察器 tick | `b4a62092` | `integrations/fleet-v1-milestone-observer/` | **31 / ~127** | **Draft CODE+UNIT，未合并 / 未部署** |

三者都**只在 GitHub 分支上**：`main` 上没有这些目录（§12），三台设备上也没有（§13.3）。Draft 有测试 ⇒ 最多拿到 `CODE`，**拿不到 `REAL_DEVICE`**。

### 15.2 逐 Gate

| Gate | 对象 | 状态 | 证据 / 缺什么 |
|---|---|---|---|
| 本合同 | #422 | `SPEC_PROPOSED` | 待独立评审；本 PR 不自签、Draft |
| 单元测试（mock CloudCLI + mock GitHub，无真飞书） | #423 / #426 / #428 | **`CODE`** | 见 15.1；Mock 不真发（`FakeLark`，账本路径由参数注入） |
| Hosted CI 收集到这些测试 | RC **#427 `a55f09e9`** | **`HOSTED_CI`（#423、#426）**；#428 **不在该 RC** | RC 的 7 个检查 **7/7 pass**，其中 `Suite (shard 4/4)` pass；RC 合并清单里没有 #428，所以 **#428 的 hosted CI 尚无干净证据** |
| 各 Draft 自己的 CI | #422 / #423 / #426 / #428 | `shard 4/4` 红 | **归因继承**：`test_direct_lark_notify::CardCreateThenPatchTest::test_create_returns_an_id_and_patch_names_it`（2026-10-13 墙钟窗口过期），修复在 #415，尚未合并；shard 1–3 在我观察到的那次 run 里 pass（#423 目前在整套重跑，未再核对）。**不写 known-failures，不重跑求绿** |
| 真实 `SourceRead` 注入（生产接线） | #426 / #428 | **`NOT_RUN`** | #423 模块 docstring 自己写着「THE ONE MISSING PRODUCTION SEAM」：`SourceRead` 只有 `FixtureSource`；#426 的适配器**凭据与传输由外部注入**，没有人调用它们 |
| 独立定时 observer（有界、可恢复、无 LLM） | #428 | **`NOT_RUN`** | **有界 tick 有了**（`run_periodic(interval_seconds, sleep, max_ticks)`，Draft），但**常驻进程不存在**：注释明写「The caller owns the loop… Starting it, unit rendering and restart policy are a separate release」，`main` 上没有该进程，也没有 unit/重启策略 |
| 单次真实飞书送达 + 回执回读（`CLIENT_E2E`） | 全部 | `NOT_RUN` | **未授权**；本波只用 mock |
| 三设备只读清单 | 全部 | **`NOT_COLLECTED`** | P 线回填（§13.3） |
| 真机 E2E（`REAL_DEVICE`） | 全部 | `NOT_RUN` | 未授权；且没有产物可装 |
| 发布物溯源（§13.4 六项） | 全部 | `NOT_RUN` | 依赖 L1/L2 发布路径 |
| **文档词表 == 代码常量** 的一致性测试 | 本合同 × #423 | **不存在** | §5.6 的 6 条差异目前**全靠人工对照**；#420 只检查文件存在与形状，不解析词表 |
| 目录守卫 | 本 PR | 通过（现有） | 三个 Draft 都只在 `integrations/` 下加子目录，不新增顶层目录 |
| 基线闸门 | 全仓 | 严格（现有） | 不得为了变绿往 `tests/hermetic-known-failures.txt` 添条目 |

**一句话总结本轮状态：`SPEC_PROPOSED`（合同）+ 三个 `CODE` 的 Draft + #423/#426 的 `HOSTED_CI`（经 RC，#428 不在其中）+ 真实注入/常驻调度/Feishu E2E 全部待验收 + 三设备 `NOT_INSTALLED`。**

---

## 16. 怎么改这份合同

1. **改三根轴里的任何取值**（新增 / 改名 / 删除 `milestone_kind`、`delivery_state`、`outcome`）= **同一个 PR 内**同时改：本文件 §5 + #423 的模块常量 + 契约测试。缺一不许合。只改代码 = 代码单方面重定义里程碑；只改文档 = 本文宣称一个不存在的事实（这正是上一版的 P0）。
2. **新增映射只能加在 §5.4**，且每一行必须能指到代码里的常量或规范化分支。指不到的映射只能进 §5.6 并标 `PENDING_RECONCILE`——**不许**为了「更完整」把口头词升格成词表取值。
3. **加一条依赖方向** = 先在 §3.3 写清楚，再走 §12.2 的交接把它变成 `layers.yaml` 的 `rules`（不在本 PR 里双写）。
4. **改变「谁拥有某条事实」** = 必须同时改动 §2 表与至少一个现有权威文档的对应小节；不允许只在实现里换来源。
5. **词表只收缩、不扩张**：`OWNER_READ`、把 `FEISHU_DELIVERED` 当 `milestone_kind`、把 `DISPATCH_ACK` 当 `milestone_kind` 这三类做法不得因「看起来方便」被加回来（§5.1、§5.4）。任何新增取值必须带代码与测试同批。
6. **已知的守卫缺口**：`milestone_kind` 的失败 / 未知取值目前是**字面量**（`milestone_notify.py:550-582`），不是导出常量；本合同与 #423 之间也还没有「文档词表 == 代码常量」的测试。下一个碰这段的人应该把字面量提升为常量，并加一条断言「常量集合 == §5.1 的表」。在此之前，§5.6 的差异登记是唯一留痕。
7. 本文与 `runtime/notification-contract.yaml` 冲突时，**事件词汇以 YAML 为准**，本文改。
8. 本文与 #423 常量冲突时：已合并进 `main` 的为准；未合并时以 #423 当时 head 为准并标 `PENDING_RECONCILE`，**不得为了让文档自洽去单方面改代码**（那正是 P0-1 的成因）。
