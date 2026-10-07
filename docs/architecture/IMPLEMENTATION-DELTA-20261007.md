# 从现有实现到目标架构：差距与改动

基线读取时间：2026-10-07，北京时间。serveros 远端 main `2b95201`；运行产物 `serveros-capacity-20261006n` 来源 `72cc6f2`。二者不是同一 revision。此表是实施起点，执行前只重核本批依赖的事实。

目标规范：[Native Work Environment](NATIVE-WORK-ENVIRONMENT.md)。已有 SYS-01–SYS-24 与专项验收不因本表消失；新增体验验收补充它们，不能降低安全或证据标准。

本轮实施更新：E1/P0 的私有仓库、模板、packet/result schema 和标准库离线检查器已通过 `serveros-research-exchange` PR1/PR2 交付；CC 实施后经 Parent 补正并通过 23 项定向检查。当前会话的 GitHub app 读取已实测。研究模式的实际读取/研究/写回及后续 Parent 业务接受仍未验收，E2–E8 继续按计划推进。不要重做 P0。

## 1. 已有能力必须复用

| 已有组件/入口 | 当前可确认状态 | 本次处理 |
|---|---|---|
| `intake/daily_reading.py`、`understanding_brief.py`、`understanding_semantic.py` | 真实增量阅读、分批缓存、旧卡上下文、追加接受 receipt 已有 | 补真实决定入口、低风险认识自动接受政策和后续 B3 消费；不重写 reader/writer |
| `intake/alignment_delivery.py`、`runtime/report_delivery.py` | 简报 compose + 真实投递 + 幂等已有；不拥有语义确认或需求接受 | 接 C1/C2/C3/C4 的版本化决定，复用既有 outbox/receipt |
| Work Engine `item_candidates`、`items_index`、Run/board | SRV-0011 实际执行/PR/报告已登记；候选表当前为空 | 增加同一来源到需求草稿/接受/唯一 Item 的实际交接验证，不为填表制造目标 |
| `runtime/agent_control_store.py`、`agent_sessions.py`、`fleet/agent_control_router.py` | 有 Session/绑定/准入 owner，thread role 当前 coordinator/execution | 扩展有版本的角色接任与资源观测；不新建 Session 数据库 |
| `runtime/continuation*.py`、`mcp/continuation_mcp.py`、`runtime/bounded_executor.py` | 受限 adapter、epoch、grant、工程隔离执行和 checkpoint 机制已有 | 接真实长期授权签发/消费及正式 Work 的恢复；不复活已撤销测试 grant |
| `context/actor.py`、BotMux delegation 候选 | 签名与 lifecycle 代码已有；本机 trust/signer binding 未在位，BotMux PR1 仍为 draft | 处理真实隔离与入站批准，明确剩余 OS/凭据前提 |
| `ability-management` 的 product tree / Thin Link；node MCP ability search | 搜索可用；grill-me B6 身份已挂接，来源仍 unknown | 补任务相关的来源追溯、版本刷新与结果接收；不把所有 catalogue 都装进会话 |
| `runtime/recipes.py`、OS timers | 有有限执行与预算；实际来源 binding 当前一个 IM | 保持本机范围，不扩大 Follow；实例时点和预算留 ops |
| `runtime/inventory.py` / 监控投递 | 读数、owner、卡片与事件已有大量实现 | Session/worker 归因并入既有资源模型；业务推进与监控事故分开 |
| `hwz-ops` testing contract、test-envs | LiteLLM 环境/能力合同已在位 | 复用实际 capability 验收；把 test venv 缺声明修到 producer，不常态手装包 |

## 2. 需要的工程增量

| ID | 改动与 owner | 现有落点 | 关键验收 |
|---|---|---|---|
| E1 | GPT 研究交换：输入/结果版本、检查与回传 | 新 `serveros-research-exchange`；B5/B8/G1 提供薄 metadata | 指定输入 commit 可读，结果可核对写回，Parent 接受；无私密原文/伪批准 |
| E2 | 审核面与决定回写 | `intake/alignment_delivery.py`、semantic writer、`mcp/human_control.py`、Work Engine candidate API | 一次真实决定只更新被选 scope；过期版本拒绝；普通事实不逐条审批 |
| E2-LIT | 工作文档/聊天状态对账与自有看板样式 | 既有 SourceReadingPacket、semantic writer、B3/B4、B9 style profile | 已完成费用异常不重复成新需求；仍开放的 WorkBuddy 400 问题不误关闭；保留删除线/章节语义与解决范围 |
| E3 | 三项目组合与交付方案形态 | ops `projects.yaml`、Work owner、`intake/global_board.py`、`workchain.py` | 全局 0–2 Focus；IMPLEMENT/VERIFY/RESEARCH/REPORT 均可完成，不强迫 PR |
| E4 | Session 资源与角色接任 | `runtime/agent-session-contract.yaml`、Agent Control、`context/resolver.py`、continuation adapter | 新/已有 Session 接任对齐/协调/Parent；旧 epoch 不能写，新角色不扩大权限 |
| E5 | 调研、生产只读与复现连接 | B5/A9/A5/A7 realization 与项目 contract；`litellm-log-analysis` source adapter | 有界生产事实进入合法脱敏复现与研究；需人项只阻塞本段 |
| E6 | 可信 standing delegation 与安全续跑 | `context/actor.py`、Agent Control、BotMux 候选、受限工程环境 | 一次有效批准跨执行保留，到期/撤销/预算/epoch 正确阻止，外部 effect 不重放 |
| E7 | AC 到交付、报告和知识更新 | TestRun/Work Engine、`runtime/daily_report.py`、`report_publication.py`、B9 | 三种业务交付均有相应结果与 Outcome；报告复用证据，不重跑测试取素材 |
| E8 | 真实旅程与效率对照 | 已有 scenario acceptance、有限 Run 与实际客户端 | 一条真实需求从 Source 至交付，含一次 Session 接任和必要的第三方能力；正常路径无额外人工点火 |

E1 的 schema/checker/模板是本次新增契约；E2–E8 的新字段/动作必须显式迁移和版本化。以上符号是实施任务编号，不是新 CLI、不建强制状态机，也不成为每个业务任务必须经历的步骤。

## 3. 现状中不能当成“只等人”的事

- records 的 IM revision 整合与切换 producer pin 是正常工程工作；保留原工作树，在新 worktree 整合即可。
- 测试环境依赖缺 requirements/lock 是可修的 producer 问题。
- 已授权的普通修复、分支合并、事实性地图订正不重复申请许可。
- 目录里的 158 条历史材料不必先移动，才能接通业务主链；按 owner/consumer 保留或分批处理。
- 没有业务候选、没有 live continuation 可以是正常空状态；它们不等于一次真实正向旅程已验收。
- 私有研究仓库建立与模板提交，不等于 ChatGPT 研究会话已连上，更不等于有 GitHub 写回能力。

## 4. 三项目约束与兼容迁移

`litellm` 和 `marketops` 已有 ops registry。`litellm-log-analysis/README.md` 已声明 `litellm-observability` 项目名，ops 尚未作为独立项目注册；沿用该身份，先查 Work owner prefix 冲突再分配，不重编号已有 LIT Item。

MarketOps 当前研究用途与外联禁用边界不变。LiteLLM 测试环境的分布式/管理员语义与 10 GiB lease 上限不变。日志分析仍用现有 LogSource、确定性聚合、代表性样本和报告生成；原始日志留已有受控数据层。

Session 当前 thread binding 不可变：新增 activation 必须保留旧绑定和 receipt，以后继 revision/epoch 切换；与旧客户端不兼容时明确返回 unsupported 并创建干净 Session，不就地偷偷换 Item。

## 5. 修改顺序与并行边界

先 E1 的无副作用工具和 E2/E3 的最小 join；E4 接任和 E6 信任链可独立实施，再接 E5/E7/E8。每批只重核涉及组件、补必要测试和实际消费者；不整仓重做或每次跑全套。

发布时分别列 main、candidate、installed、loaded 版本；常驻消费者若未安全重载，明确留在待切换项。Node Core 与活跃 Native Session/业务容器的生命周期不能混为一谈。

## 6. 交付清单

- 当前目标架构、研究协议、CC 可执行计划和版本化接口增量。
- 私有研究交换 repo 的模板、结构校验和真实接入状态。
- 三项目最小实例绑定、条件审核面及 Session role 接任。
- 生产观察/复现与受控研究的可追溯交接。
- 一条真实业务闭环、一条适用的第三方消费链和代表性效率证据。
- 精确剩余阻塞与恢复位置；不得把无效审批、等待目录整理或反复全套测试当终点。
