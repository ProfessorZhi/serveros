# ServerOS Fleet：需求、V1/V2 架构与技术设计

> **这篇回答什么：** ServerOS Fleet 最终要成为什么（舵手模型）；V1 交付什么、V2 再加什么；V1 的关键技术设计；稳定性要求与验收；为了让 V2 只「加」不「改」，V1 必须预留的 13 个接口。
>
> **状态：** owner 对齐结论，2026-10-26。本文是需求与设计，不是实现或验收记录。第 5 节的事件收件箱是下一个实施项。
>
> **与其他文档的关系：**
> - 代码放在哪一层、层间依赖：[FLEET-FOUR-LAYERS.md](FLEET-FOUR-LAYERS.md)；
> - 三台设备的拓扑与 V1 运行边界：[FLEET-V1-TOPOLOGY-OPERATIONS.md](FLEET-V1-TOPOLOGY-OPERATIONS.md)；
> - V2 组织形态的完整提案：[FLEET-V2-AUTONOMOUS-ORGANIZATION.md](FLEET-V2-AUTONOMOUS-ORGANIZATION.md)；
> - 身份、Session 与委派：[AGENT-IDENTITY-SESSION-DELEGATION.md](AGENT-IDENTITY-SESSION-DELEGATION.md)。
> - V1 还差什么、怎么分工：[fleet-v1-gap-and-work-packages.md](../plans/fleet-v1-gap-and-work-packages.md)。
>
> 本文与它们冲突时，以本文记录的 owner 决定为准，并应回头修正那篇文档。

## 1. 北极星：舵手模型

**owner 是舵手，只掌握若干项目的方向；Agent 负责把事情做完；owner 参与得越来越少。**

### 1.1 权威顺序

多个指挥者意见冲突时，按以下顺序裁决：

1. owner 最新的指令——最高，可以推翻一切；
2. owner 更早的指令——被新指令覆盖的部分自动失效；
3. owner 批准过的计划与布局；
4. 各指挥者（GPT、Claude、Agent 总管）自己的安排；
5. 执行 Agent 的局部决定。

「最新指令优先」是**机制**，不是靠 Agent 自觉：owner 的每条指令记成一条带时间与覆盖关系的方向记录（§5.5）；新指令到达后，与之冲突的在途工作暂停或改道，而不是按旧指令跑完。

owner 从任何入口（ChatGPT、Claude Code、飞书，以后的其他 IM）说的话都是同一个人：所有入口映射到同一个 owner 身份。

### 1.2 owner 参与越来越少，靠什么

| 机制 | 含义 |
|---|---|
| 只上报例外 | 只有方向冲突、超出授权、不可恢复的故障、阶段验收这四类事找 owner |
| 授权逐步放大 | 同类事连续做对，自主等级自动升级为「不用再问」；owner 随时可收回（V2） |
| 参与度可度量 | 每天、每个项目统计 owner 被打扰几次、多少工作在 owner 不在时完成（§7.3） |
| 随时可接管 | 一条指令即可暂停、改道、换负责人、回滚：参与少，不等于控制少 |

### 1.3 Agent 是实体，Session 只是载体（V2 落地，V1 预留）

一个 Agent 由五样东西组成，**都存放在 Session 之外**：

| 组成 | 是什么 | 对应 |
|---|---|---|
| 身份 | 它是谁（如「东京设备管家」「serveros 项目分管」） | principal（§6 接口 1、7） |
| 职责 | 负责哪些项目/领域，向谁汇报，能管谁 | 职责树（V2） |
| 能力组合 | 掌握哪些 skill / ability | ability-management 按需分发（§2 第①部分） |
| 上下文 | 当前目标、进度、已接受的证据、下一步 | continuation 检查点 + memory（接口 12） |
| 权限 | 能做什么、在哪些设备与项目上 | grant：scope × 设备 × 项目，配置化（接口 6） |

Session 是某台设备上一个具体的 CC / Codex 进程，可以挂、可以换。Agent 换一个新 Session，按「身份 + 能力 + 检查点」重新装配后继续，**不读旧聊天记录**。两条纪律：

- 上下文必须是显式检查点，不能是整段对话；
- Session 不跨项目复用。

## 2. 系统组成：四部分

按 owner 的表述顺序（括号内是 [FLEET-FOUR-LAYERS.md](FLEET-FOUR-LAYERS.md) 的层号）：

| # | 部分 | 职责 | 状态 |
|---|---|---|---|
| ① | **能力分发**（L3 + L0） | 给每台设备的 Agent 提供开发所需的 ability / skill / plugin；能力目录只有 ability-management 一份，按需分发；ServerOS 负责组合与观测「这台设备实际装了什么」 | 有；设备侧插件与节点运行时仍是同一份构建，待拆 |
| ② | **serverosd**（L1） | 每台设备一个进程，C++ 为主：资源/服务/网络监控、受控自愈、本地 lease、本机事实 | 两套实现并存（Python + C++），按 FOUR-LAYERS §6.1 收敛 |
| ③ | **CloudCLI 穿透 + MCP 枢纽**（L2） | 每台设备的 connector 接 CloudCLI，把 Codex / Claude Code 的能力与权限原样上传；东京枢纽包成一个 Fleet MCP，连到 GPT / CC 指挥端 | 已有：31 个 CloudCLI 镜像工具 + 13 个自写工具；owner-max（CC bypass / Codex full-access）；缺事件上行 |
| ④ | **指挥侧**（L4） | 指挥端插件与 Operator Skills；派完就走，事件到了再处理 | 插件与 Skill 已有；缺「派完不用等」 |

另有 **L0 权威**：设备身份与拓扑（`fleet/`）、工作状态与 lease（`workengine/`）、能力目录（ability-management）。多点指挥的唯一仲裁点是 Work Engine（§4.4）。

## 3. 需求

### 3.1 功能需求

| ID | 需求 | V1 | V2 |
|---|---|---|---|
| F1 | 能力按需分发到每台设备，并能观测实际安装情况 | ✓ | 能力随 Agent 实体装配 |
| F2 | 每台设备本地监控与受控自愈，不依赖东京在线 | ✓ | — |
| F3 | Codex / Claude Code 的会话能力（建、发、读、停、建/删项目）与权限经 MCP 暴露给指挥端 | ✓ | 更多 runtime（如 DeepSeek Harness） |
| F4 | 指挥端派完任务即返回，不轮询等待 | ✓（事件收件箱） | — |
| F5 | 任务完成、失败、需要决定时通知 owner | ✓ 飞书 | 多 IM（微信、Telegram、邮件…） |
| F6 | 等待中的工作在事件到达后自动继续 | ✓ 东京一个「总管」CC Session 被唤醒 | 层层管理：总管 → 分管 → 专家 |
| F7 | owner 最新指令优先，可抢占在途工作 | ✓ 方向记录 + 暂停/改道 | 自动重排全局计划 |
| F8 | 多个指挥者同时工作不冲突 | ✓ 同一工作项只有一个写入者 | 多个总管、Agent 团队 |
| F9 | Agent 与 Session 脱钩，换 Session 继续 | 预留（接口 11、12） | ✓ |
| F10 | Agent 之间互相沟通、@、委派 | — | ✓ OpenAgents 作为通信层 |
| F11 | 职责与管理权 | — | ✓ 职责树 + 管理关系 |
| F12 | 自主等级随成功率放大 | — | ✓ |

### 3.2 非功能需求（稳定性）

| ID | 需求 | 验收方式 |
|---|---|---|
| S1 | **不丢事件**：已写入收件箱的事件在枢纽重启后仍在，读取方的游标不回退 | 写入后杀进程重启，事件与游标都在 |
| S2 | **不重复执行**：同一意图（幂等 key）只执行一次；结果未知时先对账，不盲目重发 | 已有：持久派发账本 + 重启重放测试 |
| S3 | **一个工作项一个写入者**：任一时刻只有一个持 lease 的写入者；被抢占的旧写入者无法再写 | 已有 S1 安全线；多指挥者并发测试 |
| S4 | **枢纽挂了，设备照常**：东京不可达时各设备的 serverosd 继续监控与自愈 | 断开东京，设备侧自愈仍有收据 |
| S5 | **枢纽可恢复**：枢纽状态全部在持久存储中，重启后恢复到最近状态；一条命令回滚到上一版本 | 重启与回滚演练 |
| S6 | **自愈有界**：预算、退避、熔断、忙碌 Agent 不动、手动停用不动、一个 unit 一个恢复者 | 已有设计；假进程演练后才对真服务开启 |
| S7 | **诚实状态**：UNKNOWN / STALE / NEEDS_LOGIN 与失败分开报告，不报假成功 | 已有约定；事件也遵守 |
| S8 | **测试离开节点也能跑**：整套测试在任何机器上可运行，CI 只在新失败时变红 | 已有：`scripts/hermetic_tests.py` + 基线门禁 |

## 4. V1 架构

### 4.1 总图

```text
owner ──飞书（V1 唯一 IM）◀──────────────── 通知（完成摘要 / 失败 / 需要决定）
  │                                                   ▲
  │ 指令                                               │
  ▼                                                   │
指挥端：ChatGPT ／ Claude Code ／ 东京「总管」CC Session   │
  │ MCP（OAuth，6 scope）           ▲ fleet_inbox（各自游标）
  ▼                                 │
┌──────────────── 东京 Fleet MCP 枢纽 ─────────────────────────────┐
│ 工具面 │ 授权 │ 持久派发账本 │ **事件收件箱** │ Run 观察器 │ 通知路由 │
└───────┬───────────────────────────────────────────▲──────────────┘
        │ 签名节点协议（请求/响应）                     │ GET /v1/runs/{id}
        ▼                                             │
每台设备：Node Connector → CloudCLI → CC / Codex Session
          serverosd（监控、自愈、本机事实）
          设备能力插件（ability-management 分发）
                                  ▲
L0：Work Engine（工作项、lease、epoch：唯一仲裁） · fleet 身份与拓扑 · 能力目录
```

### 4.2 一次派发的完整生命周期（V1）

1. 指挥端调 `fleet_create_session` / `fleet_send_to_session`，带幂等 key 与（可选）派发上下文（接口 2）。
2. 枢纽写持久派发账本，经节点协议送达，拿到 ACK（`session_ref`、`run_id`、`queued_message_uuid`），**立即返回**。指挥端不等待。
3. 枢纽的 Run 观察器把这个 `run_id` 记入待观察集合，按退避轮询节点的 `GET /v1/runs/{id}`，直到终态。
4. 终态（完成 / 失败 / UNKNOWN）写入事件收件箱。
5. 通知路由按事件等级发飞书；若有等待中的检查点，唤醒东京总管 Session 继续。
6. 任何指挥端下次出现时，调一次 `fleet_inbox(since=游标)` 拿到所有新事件。

### 4.3 为什么 ChatGPT 不能被直接通知

ChatGPT 只在 owner 发消息或 ChatGPT 定时任务触发时行动；MCP 的服务端通知在对话之外不会被它接收。所以「完成后接着干」不能依赖 GPT，必须由三个接收方之一承担：

- **owner**：飞书收到摘要，回到任一指挥端说「继续」；
- **东京总管 Session**：被事件唤醒后按依赖关系派下游任务——这是真正的「不用等」；
- **GPT**：下次被唤起时读收件箱（可选：ChatGPT 定时任务每小时读一次）。

### 4.4 多指挥者的仲裁（V1 规则）

- 每个工作项同一时刻只有一个持 lease 的写入者（Work Engine 的 lease + epoch）；其他指挥者只读，或请求交接。
- owner 指令可以抢占任何写入者（§5.5、接口 9）。
- 同一意图重复下发由幂等 key 去重；不同指挥者对同一工作项的冲突写入在 lease 校验处被拒绝，并记一条事件。

## 5. V1 关键技术设计

### 5.1 事件收件箱（下一个实施项）

**存储。** 枢纽本地 SQLite，与持久派发账本放在同一状态目录（由 `runtime/fleet_mcp_owner.py` 注入，与账本同一主人），单写者。

**表结构（草案）**

```text
events(
  seq          INTEGER PRIMARY KEY AUTOINCREMENT,   -- 全局单调序号，即游标
  event_id     TEXT UNIQUE,     -- 稳定 ID：hash(kind, run_id/引用, 终态)；重复观察不重复写入
  occurred_at  TEXT,            -- 设备或观察器给出的时间
  recorded_at  TEXT,            -- 枢纽写入时间
  kind         TEXT,            -- run.completed | run.failed | run.unknown | decision.required
                                -- | directive.issued | preempted | node.unreachable
  level        TEXT,            -- auto | notify | decide
  device_ref, runtime, session_ref, run_id, project,
  dispatched_by TEXT,           -- 派发者 principal（接口 1、2）
  work_ref     TEXT NULL,       -- 所属工作项（接口 2，V1 可空）
  agent_ref    TEXT NULL,       -- V2 用（接口 11）
  summary      TEXT,            -- 有界摘要；不含凭据、原始提示词或私有路径
  evidence     TEXT             -- 证据定位（run 记录、分支、产物路径）
)
cursors(subscriber TEXT PRIMARY KEY, seq INTEGER, updated_at TEXT)
```

**写入来源（V1）**

- Run 观察器：每个已 ACK 且带 `run_id` 的派发进入待观察集合；按 10s → 30s → 2min → 10min 退避轮询，上限 24 小时，超时记 `run.unknown`。枢纽重启后从账本重建待观察集合。
- 派发账本：结果未知（UNKNOWN）的派发记 `run.unknown`，等级 `decide`。
- owner 指令：记 `directive.issued`（§5.5）。

**读取 API：新工具 `fleet_inbox`**

```text
fleet_inbox(since?: seq, kinds?: [...], level?: str, limit?: int ≤ 200, ack?: bool)
  → { events: [...], next_since: seq, more: bool }
```

- 需要 `fleet:read`；只返回调用者有权读的设备与项目的事件。
- `ack: true` 时把调用者自己的游标推进到 `next_since`。游标按 principal 分开：多个指挥者各读各的，互不抢（接口 3）。

**不变量（对应 S1）**

- `seq` 单调；`event_id` 唯一，重复观察只写一次；
- 游标只前进不后退；
- 重启后待观察集合可从账本完整重建。

### 5.2 Run 终态的判定

以节点 `GET /v1/runs/{id}` 的回答为准。CloudCLI 已能识别 `provider-turn-completed`，不再把「没有退出码」误判为失败。`RUN_NOT_FOUND` 不等于失败：先查原生 Session 历史，查不到才记 `run.unknown`。

### 5.3 通知路由（V1：飞书）

- 复用 `runtime/notifications.py`（定义事件含义）+ `runtime/direct_lark_notify.py`（飞书投递），新增一个从收件箱读事件的投递者。它也有自己的游标。
- 等级 `notify`：完成摘要合并发送（同一项目 5 分钟内的完成合成一条）；失败与 `decide` 立即发送。
- 渠道是适配器（接口 13）：V2 加 IM 只需新增一个适配器。

### 5.4 东京总管唤醒

- 「总管」在 V1 是东京一个指定的 Claude Code Session（owner-max，项目为 serveros 或指定项目）。
- 派发时若写了 `parent_ref`（接口 2），对应事件到达后由 `runtime/wake.py` 的接收器唤醒持有该检查点的总管 Session，送入「事件 + 检查点」，由它决定下一步。
- 唤醒前必须重新验证：检查点仍是该工作项的当前持有者、授权仍有效（复用 `continuation_control.py` 的 ALLOW / DENY 判定）。

### 5.5 owner 方向记录与抢占

- owner 指令记为 `directive`：`(directive_id, issued_at, source_channel, scope: project/work_ref, text, supersedes[])`，并写一条 `directive.issued` 事件，所有指挥者都能读到。
- 抢占：新指令覆盖某个工作项时，枢纽对该工作项下的在途 Run 执行 interrupt（已有 `fleet_interrupt_session`）或标记「暂停，待重新规划」，并写 `preempted` 事件。
- V1 的识别规则保守：由指挥端显式调用 `fleet_directive(...)` 记录与抢占，不做自然语言自动判断冲突。

### 5.6 身份：principal 增加 kind

`Principal` 现在只有 `name`、`scopes`、`credential_kind`。V1 增加：

- `kind: owner | client | agent`：owner 的所有入口映射到同一个 owner principal；ChatGPT、各 Claude Code 是 client；V2 的 Agent 是 agent；
- 审计与事件里记录 `dispatched_by = principal.name`。

### 5.7 派发上下文字段

`fleet_create_session` / `fleet_send_to_session` 增加三个可选参数，写入账本与事件：

- `work_ref`：所属工作项；
- `parent_ref`：派发者的检查点或父任务；
- `agent_ref`：V2 的 Agent 实体，V1 可为空。

V1 只记录、不强制。

## 6. V1 必须预留的 13 个接口（V2 只加不改）

| # | 接口 | V1 做到 | V2 用途 |
|---|---|---|---|
| 1 | 每个指挥者独立身份 | principal 区分 owner / client / agent；审计记录派发者 | 角色、管理权、追责 |
| 2 | 派发记录带上下文 | `work_ref`、`parent_ref`、`agent_ref` 可选字段 | 分层委派、团队树、接任 |
| 3 | 持久事件收件箱 + 每订阅者游标 | §5.1 | 多总管、Agent 团队的协作总线 |
| 4 | 工作权威唯一 | 所有写入经 Work Engine lease + epoch | 多点指挥不冲突、负责人交接 |
| 5 | 能力只在 MCP 工具面 | 已做到；Skill 同源 | 任意指挥端、OpenAgents 都能接 |
| 6 | 权限是数据 | 已做到（6 scope、`"*"`、owner-max） | 角色 = 一组预设授权 |
| 7 | owner 是特殊身份 | 所有入口映射到同一个 owner principal | 舵手权威 |
| 8 | 方向记录 | `directive` + `directive.issued` 事件 | 自动重排全局计划 |
| 9 | 可抢占 | 按工作项暂停/中断 | 方向变更即时生效 |
| 10 | 上报分级 | 事件等级 `notify` / `decide`（`auto` 预留） | 自主等级 |
| 11 | Session 绑定 Agent | `agent_ref` 可选 | Agent 履历、换 Session 接续 |
| 12 | 检查点与 Session 分离 | 上下文写 continuation 检查点 | Agent 实体的上下文 |
| 13 | 渠道适配器 | 飞书是第一个实现 | 多 IM |

## 7. V2 架构（方向，不在 V1 实施）

### 7.1 层层管理

```text
owner（舵手）
 └ 总管 Agent（若干项目的整体布局，向 owner 汇报）
    ├ 项目分管 Agent（拆任务、验收、集成）
    │   └ 专家 Agent（写代码、测试、调研…）
    └ 设备管家 Agent（运维、修复）
```

- 指挥者可以是 owner、GPT、Claude，也可以是 Agent；多个总管可同时在线，各读各的收件箱游标。
- 管理关系是配置（职责树），不是代码。

### 7.2 两条总线

| 总线 | 承载 | 实现 |
|---|---|---|
| 交流总线 | Agent 之间的讨论、@、委派请求 | OpenAgents（可替换） |
| 裁决与记账总线 | 授权、lease、工作状态、事件、证据 | Work Engine + 事件收件箱 |

**OpenAgents 里的消息只是交流，不是授权。** 一个 Agent 在频道里 @ 另一个「去改这个」，真正动手仍要通过 Work Engine 拿到 lease 与授权；OpenAgents 身份要映射回 ServerOS principal。原因：其身份校验较弱（`validate_agent` 直接返回 true）；任务存储在内存，重启即丢。

### 7.3 度量（证明 owner 越来越少参与）

- 每天、每个项目的 owner 打扰次数（`decide` 事件数）；
- owner 不在线期间完成的工作占比；
- 从事件到下游派发的平均延迟（「不用等」是否成立）；
- 抢占后在途工作停下的时间。

### 7.4 V2 其余

- 多 IM 渠道适配器；
- 自主等级自动升级；
- Agent 实体的装配器：身份 + 能力 + 检查点 → 新 Session；
- 枢纽多副本（V1 先保证可恢复）。

## 8. 里程碑

| 阶段 | 内容 | 验收 |
|---|---|---|
| V1-a | 事件收件箱 + Run 观察器 + `fleet_inbox`（§5.1–5.2） | S1：重启不丢事件与游标；一次派发后不轮询即可从 `fleet_inbox` 拿到终态 |
| V1-b | 飞书通知路由（§5.3） | 完成、失败、需要决定各收到一次，不重复 |
| V1-c | principal kind + 派发上下文字段（§5.6–5.7） | 事件里能看到派发者与工作项 |
| V1-d | 东京总管唤醒（§5.4） | 父任务派两个子任务后离开；子任务完成时总管被唤醒并派出下游 |
| V1-e | owner 方向记录与抢占（§5.5） | 新指令发出后，冲突的在途 Run 在 1 分钟内停止并有 `preempted` 事件 |
| V1-f | serverosd 收敛为 C++（FOUR-LAYERS §6.1）与设备侧插件拆分构建 | 一个 unit 一个恢复者；设备侧 Skill 改动不重建节点运行时 |
| V2 | §7 | §7.3 的度量持续下降 |

## 9. 待定事项

1. 旧控制面 `mcp/agent_mcp.py`、`fleet/controller.py`、`fleet/agent_control_router.py` 是否按 FOUR-LAYERS §6.2 退役；
2. 实验插件 `serveros-fleet-zip-lab-20261014` 是否删除；
3. 东京「总管」Session 的项目与模型；
4. 是否启用 ChatGPT 定时任务每小时读一次收件箱。
