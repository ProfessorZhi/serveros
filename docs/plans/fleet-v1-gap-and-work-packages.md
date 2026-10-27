# Fleet V1：差距清单与工作包

> **这篇回答什么：** 对照 [FLEET-V1-V2-ARCHITECTURE.md](../architecture/FLEET-V1-V2-ARCHITECTURE.md)（需求与设计）和 [FLEET-FOUR-LAYERS.md](../architecture/FLEET-FOUR-LAYERS.md)（分层与目录），V1 现在还差什么；把差距拆成可以交给本地 Codex / Claude Code 独立完成的工作包，每个都写明范围、步骤、验收与依赖。
>
> **状态：** 计划，2026-10-26，基于 main `3f5ec20` 的实测。工作包完成后在本文对应行标注 PR 号；不另建第二份进度表。
>
> **补充（2026-10-27）：** 里程碑播报的语义、字段、红黄灰处置与降级规则已收进一份正式合同 [FLEET-V1-MILESTONE-DELIVERY.md](../architecture/FLEET-V1-MILESTONE-DELIVERY.md)，对应下面的工作包 F7。那份合同是判定的唯一权威，本文只记录它落到哪个包。

## 0. 给执行 Agent 的规则（每个工作包都适用）

1. **一个工作包一个分支、一个 PR**，从最新 main 切出；不跨包修改文件。
2. **先读**：本文对应条目 + 两篇设计文档的相关小节 + `docs/architecture/layers.yaml`。
3. **测试入口固定**：
   `python3 scripts/hermetic_tests.py --baseline tests/hermetic-known-failures.txt -n auto`
   新失败为 0 才能提 PR。修好的基线条目同一个 PR 里删掉。
4. **搬文件的包**：旧路径保留一个转发模块，直到引用它的测试与代码全部改完；同一个 PR 内删掉转发模块更好。改完更新 `layers.yaml`，并把对应的依赖规则加进 `tests/test_repository_layers.py`。
5. **不做**：不碰生产服务、不改真实设备、不改授权配置；涉及真实设备的验收由 owner 或指定会话执行。
6. **不确定就停**：遇到本文没写清楚、需要 owner 决定的事，在 PR 里写明，不自行决定。

## 1. 差距总览

| 领域 | 目标（V1） | 现状（实测 2026-10-26） | 差距 | 工作包 |
|---|---|---|---|---|
| ① 能力分发 | 设备侧插件独立构建、独立发布；每台设备装当前版本 | 设备侧插件与节点运行时是同一份构建（`scripts/build.py` 把同一批字节发布两次） | 拆构建 | D1 |
| | | 东京设备侧插件停在 10-07，cache 有 `serveros-testing`、`marketops-probe` 残留；Windows 没有设备侧插件；Dell 未验证（据 owner 另一会话报告，本文未复核） | 设备侧安装对齐 | Q3 |
| ② serverosd | 一个进程、一个名字、C++ 为主 | Python `runtime/daemon.py`（5445 行）与 C++ `native/node-core`（约 2.9 万行）并存，名字不统一 | 改名、单写者、逐项迁移 | D8、D9 |
| ③ 穿透 + 枢纽 | 能力与权限原样上传；事件上行；枢纽与 connector 分开发布 | 工具面与 owner-max 已有；**没有完成事件**，只能逐个 `fleet_get_run`；枢纽与 connector 同在 `fleet_mcp/` 一起发布 | 事件收件箱；发布物拆分 | F1、D7 |
| ④ 指挥侧 | 派完不用等；通知 owner；总管被唤醒继续 | 插件与 8 个 Skill 已有；无收件箱、无飞书联动、无唤醒 | 通知、唤醒、Skill 更新 | F1、F2、F4、F6 |
| 舵手 | owner 最新指令优先、可抢占；身份区分 owner / client / agent | `Principal` 只有 name / scopes / credential_kind；无方向记录 | 身份与指令 | F3、F5 |
| 解耦：`runtime/` | 只留 L1 | 123 个文件，混着 L1（39 个模块）、L0 代理控制记录（10 个）、L2 枢纽装配（8 个）、product 卡片与报告（26 个）；`cli.py` 15.6k 行横跨各层 | 迁出非 L1 部分；拆 CLI | D2、D3、D4、D10 |
| 解耦：`fleet/` | 只剩身份与拓扑 | 一半身份（L0），一半旧控制面 | 退役旧控制面 | D5、D6 |
| 解耦：`mcp/` | 只有一个控制面 | `mcp/agent_mcp.py` 等旧适配器与 `fleet_mcp/` 并存（owner 已定：退役） | 退役 | D6 |
| 质量 | 基线只剩「必须在节点上跑」的条目 | 基线 56 条，其中约 20 条是 main 上真实的不一致（drift） | 修 drift | Q2 |
| CI | 4 片全绿 | 缺 `SIBLING_REPOS_TOKEN` 时第 1、4 片红 | owner 配 secret | Q1 |

迁移成本实测（`runtime/` 之外引用该组模块的文件数）：L2 枢纽装配 43、product 220、L0 代理控制 218、L1 466（L1 留在原地，不搬）。

## 2. 并行泳道与顺序

```text
泳道 F 功能（枢纽，Python）  F1 ─▶ F2 ─▶ F3 ─▶ F4 ─▶ F5 ─▶ F6
泳道 D 解耦（Python）        D2 ─▶ D3 ─▶ D10 ─▶ D4 ─▶ D6 ─▶ D5
泳道 B 构建与发布            D1 ─▶ D7
泳道 C serverosd（C++）      D8 ─▶ D9
泳道 Q 质量与运维            Q1（owner） · Q2（可随时） · Q3（owner 或指定设备会话）
```

- 同一泳道串行，不同泳道可并行。
- **F7（里程碑播报）跨泳道**：判定与投递适配器在 `integrations/`（`shared`），不占上面任何泳道的文件；只依赖 F1 的事件游标，可与 F2–F6 并行。见 [FLEET-V1-MILESTONE-DELIVERY.md](../architecture/FLEET-V1-MILESTONE-DELIVERY.md)。
- 两组文件冲突要避开：
  - F1 与 D2 都碰 `runtime/fleet_mcp_owner.py`：F1 先合并，D2 再做；
  - D3 与 F2 都碰 `notifications.py`、`direct_lark_notify.py`：F2 先合并，D3 再搬。

## 3. 功能工作包

### F1 事件收件箱 + Run 观察器 + `fleet_inbox`　〔最先做〕

- **设计**：FLEET-V1-V2-ARCHITECTURE §5.1–5.2。
- **范围**：
  - `fleet_mcp/` 新增事件存储模块（SQLite，与派发账本同目录，由装配入口注入路径，**不让 `fleet_mcp/` 加载 `runtime/`**）；
  - Run 观察器（退避轮询节点 `GET /v1/runs/{id}`，重启后从账本重建）；
  - 新工具 `fleet_inbox`；
  - `runtime/fleet_mcp_owner.py` 注入存储路径。
- **步骤**：
  1. 写失败的测试：派发 → 节点 fixture 返回终态 → `fleet_inbox` 能读到；
  2. 实现存储，`seq` 单调、`event_id` 唯一、游标只前进；
  3. 实现观察器，带退避与 24 小时上限（超时记 `run.unknown`）；
  4. 实现工具（`fleet:read`，按调用者权限过滤，`ack` 推进自己的游标）；
  5. 重启测试：写事件 → 重建服务对象 → 事件与游标都在、待观察集合恢复。
- **验收**：
  - 新测试全过；
  - `tests/test_repository_layers.py` 规则仍成立；
  - 工具数变化同步到 `plugins/` 的 Skill 生成（`scripts/fleet_plugin.py build`）。
- **不做**：通知、唤醒（F2、F4）。

### F2 飞书通知路由

- **设计**：§5.3。
- **范围**：一个从收件箱读事件的投递者，带自己的游标；复用 `runtime/notifications.py` + `runtime/direct_lark_notify.py`。
- **验收**：
  - `notify` 级完成事件 5 分钟内按项目合并成一条；
  - `run.failed` 与 `decide` 级立即发送；
  - 同一事件不重复发送；
  - 投递失败可重试且不丢；
  - 用飞书传输 fixture 测试，不发真实消息。
- **依赖**：F1。

### F3 principal kind + 派发上下文字段

- **设计**：§5.6–5.7。
- **范围**：
  - `Principal` 增加 `kind: owner | client | agent`，由配置决定，不由调用者参数决定；
  - `fleet_create_session` / `fleet_send_to_session` 增加可选参数 `work_ref`、`parent_ref`、`agent_ref`，写进账本与事件。
- **验收**：
  - 旧调用不带新参数时行为不变；
  - 事件里有 `dispatched_by` 与上下文；
  - 调用者不能自称 owner。
- **依赖**：F1。

### F4 东京总管唤醒

- **设计**：§5.4。
- **范围**：带 `parent_ref` 的派发，其事件到达后，经 `runtime/wake.py` 的接收器唤醒持有该检查点的 Session；唤醒前用 `continuation_control.py` 重新判定 ALLOW / DENY。
- **验收**：
  - 父任务派两个子任务后离开；
  - 两个子任务都完成时，总管被唤醒一次（不是两次），拿到两条事件；
  - 检查点已不是当前持有者时拒绝唤醒，并记事件。
- **依赖**：F1、F3。

### F5 owner 方向记录与抢占

- **设计**：§5.5。
- **范围**：
  - 新工具 `fleet_directive`（仅 owner kind 可调）：记录指令、写 `directive.issued` 事件；
  - 若带工作项范围，对该工作项的在途 Run 执行 interrupt 或标记暂停，写 `preempted` 事件。
- **验收**：
  - 非 owner 调用被拒；
  - 抢占后在途 Run 1 分钟内停止（fixture 计时）；
  - 被抢占的旧写入者再写被 lease 拒绝。
- **依赖**：F3。

### F6 指挥侧 Skill 更新

- **范围**：`integrations/fleet-v1-first-install/plugin/skills/` 增加「派完就走、用 `fleet_inbox` 收结果、用 `fleet_directive` 改方向」的写法；重新生成 `plugins/`。
- **验收**：Skill 测试与哈希重新固定；`scripts/fleet_plugin.py check` 通过。
- **依赖**：F1、F5。

### F7 里程碑播报（跨层，窄范围）　〔`SPEC_PROPOSED` · 代码见 Draft #423 / #426 / #428（`CODE` + 单元测试，未合并、未部署）〕

- **设计**：[FLEET-V1-MILESTONE-DELIVERY.md](../architecture/FLEET-V1-MILESTONE-DELIVERY.md)（owner 2026-10-27 决定；实现权在 #417，进度在 #409，跨设备/CI 一致性在 #418）。
- **范围**：`integrations/fleet-v1-milestone-notify/`（判定与投递，#423）· `integrations/fleet-v1-milestone-sources/`（CloudCLI / GitHub Actions 只读真源，#426）· `integrations/fleet-v1-milestone-observer/`（有界观察器 tick，#428），各有同名 `tests/`；复用 `runtime/notification-contract.yaml`、`runtime/alerting.py`、`runtime/direct_lark_notify.py`、`runtime/message_transport.py`，东京唯一出口。**不新增顶层目录，不改 `layers.yaml`**。三个 Draft 的单元测试分别为 31 / 30 / 31 个用例；`main` 上仍不存在，三台设备上也没有。
- **仍未成立**：真实 `SourceRead` 注入（生产接线）、常驻调度与重启策略、单次真实 Feishu 送达与回执回读。这四项在合同 §15 里逐条记为 `NOT_RUN`。
- **只做**：重要绿发一次、红立即发且保持开启、黄与普通变化只进卡片、`UNKNOWN` 永不当绿、确定性模板兜底、`delivery_key` 幂等、`provider_receipt`/`delivery_state` 与实际情况一致。
- **不做**：总管唤醒（F4）、抢占（F5）、凭据分发、常驻 LLM 轮询、飞书入站决策。
- **验收**：mock CloudCLI + mock GitHub 下正/负/重复 webhook/重复观察/重启重放/红升级/黄静默/投递 UNKNOWN/恢复全部有测试，且被 CI 真正收集；真实飞书 E2E 单独授权、单独回执。
- **依赖**：F1（观察者与事件游标）。

## 4. 解耦与目录工作包

### D1 设备侧插件与节点运行时拆成两份构建

- **范围**：`plugin/bundle.yaml` 的 artifact kinds、`scripts/build.py`、`scripts/release.py` 的对应路径与测试。
- **目标**：
  - `node-runtime` 只含 L1；
  - 设备侧插件只含清单、Skill 与适配器，不含 `runtime/` 的 Python 代码。
- **验收**：
  - 改一个设备侧 Skill，只有插件产物的哈希变；
  - 两个产物各自通过现有验证器；
  - `tests/test_build.py`、`tests/test_release.py` 全过。
- **风险**：测试里大量写死产物结构，先读 `tests/test_build.py` 再动。

### D2 枢纽装配迁出 `runtime/`

- **范围**：8 个模块（`fleet_mcp_owner`、`fleet_core_bridge`、`fleet_monitor_card`、`fleet_monitor_plan`、`fleet_monitor_source`、`fleet_recovery_events`、`fleet_audience`、`agent_event_bridge`），外部引用 43 处。
- **目标位置**：新顶层目录 `fleet_hub/`（L2 装配：把 `fleet_mcp/` 与 L0 的持久账本组装起来），在 `layers.yaml` 登记。
- **验收**：
  - 引用全部改完，旧路径无转发模块残留；
  - 新增规则「`fleet_mcp/` 不加载 `fleet_hub/`」并通过。
- **依赖**：F1 合并后。

### D3 product 部分迁出 `runtime/`

- **范围**：26 个模块（卡片、日报、告警投递、工作台、通知），外部引用约 220 处；连同相关 yaml 合约与 systemd 单元（`serveros-daily.*`、`serveros-alert-sync.*`、`serveros-evening-alignment.*`）。
- **目标位置**：新顶层目录 `reports/`（product），在 `layers.yaml` 登记。
- **步骤**：按子主题分 3 个 PR——卡片、报告、通知与告警，每个 PR 改完对应引用。
- **验收**：新增规则「`runtime/` 的 L1 模块不加载 `reports/`」并通过；daily / alert 单元测试全过。
- **依赖**：F2 合并后。

### D4 代理控制记录并入 `workengine/`

- **范围**：10 个模块（`agent_control_store`、`coordination`、`continuation`、`continuation_control`、`dispatch_attempts`、`approval`、`owner_policy`、`agent_sessions`、`wake`、`review_receipt`），外部引用约 218 处。
- **目标位置**：`workengine/agent_control/`，与 Work lease 同一个主人。
- **验收**：
  - S1、S2 安全测试（lease、epoch、伪造写入者、重放）全部原样通过；
  - 新增规则「`workengine/` 不加载 `runtime/`」。
- **风险**：这是安全边界代码，只搬不改逻辑；**逻辑改动另开 PR**。
- **依赖**：D2、F4 合并后。

### D5 `fleet/` 只留身份与拓扑

- **范围**：D6 完成后，`fleet/` 中旧控制面文件已删除；更新 `fleet/README.md` 与 `layers.yaml`（取消 `split`）。
- **依赖**：D6。

### D6 旧控制面退役　〔owner 已决定退役，2026-10-26〕

- **范围**：`mcp/agent_mcp.py`、`fleet/controller.py`、`fleet/agent_control_router.py`（及 `fleet/node.py`、`fleet/interfaces.py`、`fleet/remote_desktop_commander.py` 的去留）。
- **步骤**（FOUR-LAYERS §6.2）：
  1. 列出调用入口；
  2. 能由 `fleet_mcp/` 覆盖的改走 `fleet_mcp/`，覆盖不了的降为适配器；
  3. 删除，并加规则「L2 只有一个控制面」。
- **验收**：插件组合（`plugin/bundle.yaml` 声明的 MCP 面）启动测试通过。

### D7 connector 与枢纽分开发布

- **范围**：`fleet_mcp/deploy/` 的打包。connector 产物只含 `fleet_mcp/node/` 及其依赖。
- **验收**：
  - 新增规则「`fleet_mcp/node/` 不导入枢纽模块」（`fleet.py`、`tools.py`、`http_transport.py`、`oauth.py`、`human_auth.py`）并通过；
  - 两个产物各自可安装。
- **依赖**：D1（共用构建约定）。

### D8 serverosd 改名与单写者登记（C++）

- **范围**：
  - `native/node-core` 改名为 `serverosd/`；
  - 二进制、服务名统一为 serverosd；
  - 新增「每个受管 unit 一个恢复者」的登记文件，C++ 与 Python 两侧启动时都读它，拒绝恢复不属于自己的 unit。
- **验收**：
  - CMake 构建与 ctest 全过（Linux 与 Windows 两套 workflow）；
  - 登记文件里同一个 unit 出现两个恢复者时，两侧都拒绝启动恢复。
- **不做**：迁移任何监控逻辑（D9）。

### D9 serverosd 逐项接管（C++）

- **顺序**：观测 → 自愈 → 本地 lease → 本机事实接口。每一项一个 PR。
- **每项验收**：
  - C++ 侧行为测试；
  - Python 侧对应代码改为只读、不动作；
  - 在假进程上演练 probe → restart 后，才在真实设备启用（由 owner 安排）。
- **结束条件**：`runtime/daemon.py` 只剩转发后删除，`bin/serverosd` 指向 C++ 二进制。

### D10 拆分 `runtime/cli.py`

- **范围**：15.6k 行，按子命令拆成多个模块，入口不变（`bin/serverosctl`）。
- **验收**：`serverosctl --help` 输出不变；所有调用 CLI 的测试全过。
- **依赖**：D2、D3（先搬走非 L1 部分，CLI 跟着拆更自然）。

## 5. 质量与运维工作包

### Q1 配置 `SIBLING_REPOS_TOKEN`　〔owner〕

- 仓库 Settings → Secrets and variables → Actions。
- 值是一个 fine-grained token，对 `ability-management` 与 `hwz-ops` 只读内容。

### Q2 修 main 上的真实不一致（缩短基线）

- **范围**：`tests/hermetic-known-failures.txt` 中「drift」与「not yet triaged」两组。已知的几处：
  - 文档引用了不存在的命令 `serverosctl surface`、`serverosctl work grant`；
  - `KNOWN_UNPRODUCED` 里的 `turn.completed`、`approval.required` 已有生产者；
  - 84 处写文件的调用点未在 `mutation_guard` 声明；
  - 原因码 `NOT_CURRENT_WRITER` 不在原因词表里；
  - `continuation_control` 的完成记录集合。
- **规则**：每修一条删一行；**不改测试来迎合代码**，除非测试本身的事实已过时（PR 里写明依据）。

### Q3 设备侧安装对齐　〔owner 或指定设备会话〕

- **东京**：设备侧插件从 10-07 更新到当前；清掉 `serveros-testing`、`marketops-probe` 残留；指挥侧只保留 `serveros-fleet-ingress@serveros`。
- **Windows**：安装设备侧插件（与「本机没有 serverosd」一并在 D9 后处理）。
- **Dell**：核实插件读取失败的原因。
- **验收**：三台设备 `fleet_status` 不报 DRIFT；插件版本与 main 一致。

## 6. V1 完成的定义

全部满足才算 V1 完成：

1. F1–F6 合并：派发后不轮询即可收到结果；飞书收到通知；总管被唤醒继续；owner 指令可抢占。
2. D1–D10 全部合并：
   - `runtime/` 只剩 L1；
   - `layers.yaml` 中没有 `split: true` 的目录（`fleet_mcp/` 例外：它按 hub / node 拆的是发布物，不是目录）；
   - 每次搬迁对应的依赖规则都在测试里。
3. serverosd 是一个名字、一个 C++ 进程；每个 unit 一个恢复者。
4. CI 4 片全绿；基线只剩「必须在节点上跑」的条目。
5. 三台设备的设备侧插件与 main 一致，指挥侧插件只装在指挥端。
