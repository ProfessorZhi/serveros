# Fleet V1 · 常驻监控与东京中枢设计

> **状态：** 设计草案，2026-10-30。owner 确认第 6 节的决定后才实施。挂在 #531 下。
> **这篇回答什么：** ① 各设备上的 agent 任务谁来盯、怎么做到只播报一次；② 项目理解和需求看板放在哪、GPT 怎么看；③ 设备角色和默认模型路由（owner 2026-10-30 的口径）。
> **不回答什么：** 已有组件的实现细节，见各组件 README。

---

## 0. 结论

1. **常驻监控 = 一个确定性的常驻进程 + 一个按事件唤醒的 LLM 总管。** 常驻进程放在 Tokyo，不带 LLM：发现各设备的 run、判定状态、写事件、发播报。LLM 总管是 Tokyo 上一个 Claude Code 会话，只在需要判断时被唤醒。LLM 不做常驻轮询。
2. **项目理解、需求、看板都存在 Tokyo 本机，只此一处。** 其他设备的工作空间只放本机的 worktree 和产物，不保存需求的副本。
3. **GPT 通过 Fleet Next 入口上新增的只读工具查看看板和项目理解。** 写入只开放"提交需求候选"；接受与否仍由 owner 决定。
4. **所有设备的 CloudCLI Claude Code 默认都走 DeepSeek，路由是东京网关0，不是 FJ 生产服网关 admin。**

---

## 1. 设备角色

| 设备 | 账号 / runtime | 角色 |
|---|---|---|
| Tokyo `device:tokyo-shared-dev-01` | `vince` / `claude` | **中枢**：Fleet Next 入口、事件收件箱、常驻监控、项目理解与需求看板、LLM 总管会话 |
| Dell A6000 `device:llm-lab-dell` | **`fjd`（我们是这台机器的管理员）** / `claude-fjd` | **计算节点 + 个人项目**：GPU 计算任务和个人项目都在这里。`vince` runtime 只保留做无损过渡，不再派发新工作 |
| FJPCOS `device:work-fj` | 授权用户 / `claude` | Windows 工作站 |

Dell 上的约束不变：不得抢占他人的 GPU 任务，不得重启 Strata，不得占用 GPU 显存来做监控。core-lite 在 Dell 上只**读**显存摘要。

---

## 2. 默认模型路由（替代 `fleet-next-runtime-model-defaults.md` 原来的首选）

| 优先级 | 路由 | 模型 ID（以设备回读为准） |
|---|---|---|
| **默认** | **东京网关0** | `v4.1flash-anthropic` |
| 备选 | FJ 生产服网关（vince） | `deepseek-v4-flash-anthropic` |
| 不再作为默认 | FJ 生产服网关（admin） | `deepseek-v4-flash-vision-anthropic` |

- 适用范围：**所有设备**上的 CloudCLI Claude Code runtime（`cloudcli-deepseek` 类），包括 Tokyo 的 LLM 总管会话。
- 例外照旧：官方订阅的 Claude Code 账号和 MarketOps 账号各用自己的凭据，不继承 DeepSeek（待 owner 确认，见第 6 节）。
- 模型 ID 是代码里登记的候选值，**还没在任何设备上验证过**。真机上以 create 之后读回的 `effective_model` 为准。如果读回的 ID 不一样，就改代码里的常量，不要绕过门禁。
- 代码改动：`fleet_mcp/release_evidence.py` 的 `DEFAULT_DEEPSEEK` 改为网关0 的模型，发布门禁随之要求 DeepSeek runtime 读回这个模型。

---

## 3. 常驻监控

### 3.1 要做到什么

- **自动发现**：每台设备、每个 runtime 上新开始的 run，不管是不是经 Fleet 派发的，都能被看见。
- **只播报一次**：完成、失败、卡住（长时间没有进展）、等待审批，各发一次。重启后不重发。
- **不误报完成**：只有读到 agent 的真实输出，才算完成。`UNKNOWN` 不报成功。
- **资源异常也播报**：core-lite 读到红色（内存或磁盘压力、服务挂掉、GPU 显存占满）时立即发。
- **GPT 能翻看**：所有事件都能按时间顺序查询。

### 3.2 已有组件，哪些直接复用

| 组件 | 位置 | 现状 | 在新设计里的角色 |
|---|---|---|---|
| core-lite（C++） | `native/node-core/core-lite/` | 有代码有测试，未安装 | **设备层**：每台设备周期性采集资源 |
| supervisor（C++） | `native/node-core/supervisor/` | 默认只观察 | 设备层：服务健康；开启 acting 由 owner 决定 |
| Agent Monitor 本体 | `integrations/fleet-next-agent-monitor/` | oneshot，只盯手工列出的 `watches` | **复用判定逻辑**（`MonitorCore`、证据标准、去重、通知器接口），外面再包一层 |
| 事件收件箱 | 架构文档 §5.1 | 只有设计，**未实现** | 按原设计实现，作为唯一的事件存储 |
| 飞书投递 | `runtime/notifications.py`、`runtime/direct_lark_notify.py`、#460 | 有，#460 未合 | 播报出口 |
| 总管唤醒 | `runtime/wake.py`，架构文档 §5.4 | 有设计和部分代码 | decide 级事件唤醒 LLM 总管 |

### 3.3 架构

```text
每台设备                              Tokyo（中枢）
──────────                            ─────────────────────────────────────────
core-lite (C++, 周期采集) ──┐
CloudCLI sessions / runs ───┼──经 Fleet provider──▶ serveros-fleet-watch（常驻，无 LLM）
                            │                          1 发现：枚举各 runtime 的活跃 run
                            │                          2 观察：MonitorCore 判定 + 退避轮询
                            │                          3 记录：写入 events 表（去重）
                            │                          4 资源：读 core_lite，过阈值写 node.* 事件
                            │                               │
                            │                    events（Tokyo state/ 下 SQLite，单写者）
                            │                     │                 │                 │
                            │              飞书投递者          fleet_inbox          总管唤醒
                            │             （按级别合并）     （GPT 只读查看）   （decide 级事件 →
                            │                                                 Tokyo LLM 总管会话）
```

**常驻进程 `serveros-fleet-watch`**（Tokyo，systemd user service，`Restart=on-failure`，带 `MemoryMax` 和 `TasksMax`）每一轮做四件事：

1. **发现**：对每个已绑定、可读的 runtime 调 `sessions_list`，只取活跃会话。对活跃会话取最新的 run。新出现的 run 加入观察集，并记录发现时间。这一步只用 `fleet:read`。
2. **观察**：观察集里的每个 run 用 `MonitorCore` 的判定逻辑分类，得到 `TERMINAL_VERIFIED`、`FAILED`、`RUNNING` 或 `UNKNOWN`。轮询间隔按 10s → 30s → 2min → 10min 退避，最长观察 24 小时；超时记 `run.unknown`。运行超过阈值且没有新输出的，记 `run.stalled`。有待审批项的（`approvals_list` 非空），记 `decision.required`。
3. **记录**：写入 events 表。`event_id = hash(kind, run 引用, 终态)`，同一个事实重复观察也只写一次。
4. **资源**：读每台设备的 `core_lite` 读数，过阈值写 `node.pressure`、`node.service_down`、`node.unreachable`。

**为什么常驻进程里不放 LLM**：成本低；判定可以重放、可以审计；也不会出现"LLM 觉得任务做完了"这种误报。需要判断的场景（失败后怎么办、待审批要不要批）才唤醒总管会话。

### 3.4 播报规则（沿用架构文档 §5.3）

| 事件 | 级别 | 播报 |
|---|---|---|
| `run.completed`（有真实输出） | notify | 同一项目 5 分钟内的完成合并成一条 |
| `run.failed`、`run.stalled` | notify | 立即发 |
| `decision.required` | decide | 立即发，并唤醒总管 |
| `run.unknown` | decide | 立即发，写明"无法确认"，**不说成功** |
| `node.pressure` / `node.service_down` / `node.unreachable` | notify | 立即发；同一状态持续时不重复发 |

### 3.5 重启和单写者

- 单写者：用 flock 保证同一时刻只有一个 `serveros-fleet-watch`。
- 重启恢复：观察集可以从 events 表（未到终态的 run）和下一轮"发现"重建，不依赖内存状态。
- 自身健康：每轮写心跳。core-lite 在 Tokyo 上盯这个服务；监控自己挂了，也会作为 `node.service_down` 播出去。

### 3.6 新增的接口

- `fleet_inbox`：只读工具，需要 `fleet:read`，按游标分页读事件。工具数会从 44 变成 45，需要升级接口版本（`fleet-mcp-8`）并更新指挥侧 Skill。

### 3.7 实施拆分

| 步 | 内容 | 验收 |
|---|---|---|
| M1 | events 表 + `fleet_inbox`（按架构文档 §5.1 实现） | 离线：`seq` 单调、`event_id` 去重、游标只前进；重启后重建 |
| M2 | `serveros-fleet-watch`：发现 + 观察（复用 `MonitorCore`） | 用假 provider：新 run 被发现；完成只记一次；`UNKNOWN` 不当成功 |
| M3 | 飞书投递者 + 合并规则（依赖 #460） | 用 journal 替身：每个事件只发一条；合并窗口生效 |
| M4 | 资源事件（读 `core_lite`） | 阈值越过才发，状态持续时不重复 |
| M5 | Tokyo 真机：装服务，盯 Tokyo 本机 runtime | 一个真实 run 完成 → 飞书收到一条（需 owner 授权真实发送） |
| M6 | 扩到 Dell fjd、Windows | 同上 |

---

## 4. 东京中枢：项目理解与需求看板

### 4.1 存在哪里：Tokyo 本机，只此一处

依据架构文档 §24："SQLite 留在拥有它的服务本地；其他设备通过认证接口访问，不跨网络写共享。"以及工作合同："不得用另一个 JSON/YAML backlog 偷建第二个状态数据库。"

| 内容 | 存储位置（Tokyo） | 权威性 |
|---|---|---|
| 项目理解 | `WORKSPACE_ROOT/records/projects/<project>/`：已确认的理解、决定、待解决问题，用私有 git 备份 | **真值** |
| 需求 | Work Engine：`Candidate`（候选，有来源和依据）→ owner 接受后变为 `Item`，存在 `state/serveros.db` | **真值** |
| 看板 | `workengine/board.py` 生成的投影 | 随时可重新生成，**不是真值** |
| 各设备工作空间 | 只放本机的 worktree、测试环境、产物 | 不保存需求副本 |

为什么不放在各设备上：同一个需求如果存在两份，迟早会改出两个版本。设备上的 agent 需要需求时，通过 Fleet 去读 Tokyo 的那一份。

### 4.2 GPT 怎么看

GPT 只能连 Fleet Next 入口，所以在入口上加只读工具。数据由 Tokyo 本机的 Work Engine 提供：

| 工具 | 作用 | 权限 |
|---|---|---|
| `fleet_work_board` | 按项目看板：候选、进行中、待验收、已完成，每条附证据位置 | `fleet:read` |
| `fleet_project_understanding` | 某个项目的当前理解：目标、关键决定、待解决问题，以及更新时间 | `fleet:read` |
| `fleet_work_candidate_add` | 提交一条需求候选（必须写来源和理由） | `fleet:send` |

接受候选、改优先级、关闭 Item 仍由 owner 做，V1 不开放给 GPT。

另一个做法是不加工具，让 GPT 给 Tokyo 总管会话发消息，请它去读看板。这样慢，回来的是非结构化文本，所以不推荐。

### 4.3 汇总流程

Tokyo 总管会话按 routine 运行（不是常驻 LLM）：

```text
各项目的增量（Git 提交 / PR / issue / 飞书文档 / 会议纪要）
   → 读取并抽取 Claim（带来源）
   → 项目理解的增量（写入 records，需要 owner 确认的标出来）
   → 新的需求候选（Candidate）
   → 每日对账文档：owner 接受、修改或推迟
```

这条链路在 ability-management 的 Understanding / Reconciliation 设计里已有定义，这里只是指定 Tokyo 为执行地点。

### 4.4 试点

先用 serveros 项目本身，再加一到两个 owner 指定的项目。验收标准：GPT 通过 `fleet_work_board` 读到的内容，和 Tokyo 本机 `python -m workengine` 读到的一致。

---

## 5. 和 #531 的关系

- 常驻监控覆盖 #531 中第 ③ 部分的"播报"一格，取代原来"Agent Monitor oneshot + 手工 watches"的做法。
- 东京中枢是第 ② 部分的延伸。它不在原来的 V1 验收矩阵里，需要 owner 决定是否纳入 V1（第 6 节）。
- 默认路由变更影响第 ② 部分 create 后的读回标准（`effective_model`）。

## 6. 需要 owner 确认

1. 常驻进程不带 LLM，LLM 总管按事件唤醒：同意吗？
2. 项目理解和需求只存 Tokyo 一处，GPT 通过新增只读工具查看：同意吗？
3. 东京中枢（项目理解 + 需求看板）纳入 V1，还是放 V1.1？
4. 默认路由东京网关0：两个例外账号（官方订阅、MarketOps）是否仍然保留？
5. 试点用哪几个项目？
