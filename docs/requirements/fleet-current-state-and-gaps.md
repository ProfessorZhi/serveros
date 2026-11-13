# ServerOS Fleet 当前现状、缺口与目标工作体系

- **状态：** 需求收集与静态现状基线；待 Owner 确认，不是实现计划或生产验收报告
- **日期：** 2026-11-13
- **代码快照：** main 02bf540ed3f5dfe3b28685a76985eae7f4a71a52
- **范围：** ServerOS 仓库目录、产品/控制架构、信息流、日常工作流、飞书播报、可观测性、性能与已知可靠性问题
- **配套需求：** [Fleet 效率、性能与执行可靠性需求](fleet-efficiency-performance.md)

## 0. 阅读规则：事实、设计、用户反馈和运行状态必须分开

本文件用于建立可持续更新的当前工作结构化认识。以下标签含义固定：

| 标签 | 含义 |
|---|---|
| SOURCE_CONFIRMED | 当前 GitHub main 中能直接读到代码、配置、测试或契约 |
| MERGED_SOURCE_FIX | 对应修复 PR 已合并到 main；只证明源码修复进入仓库 |
| DESIGN_ONLY | 有架构/设计/验收文档，不代表功能已运行 |
| HISTORICAL_EVIDENCE | 某个日期的历史现场证据，不可自动当作今天的状态 |
| OWNER_REPORTED | Owner 在当前对话反馈的问题，尚未通过目标设备或真实客户端独立核验 |
| RUNTIME_NOT_VERIFIED | 没有本次目标设备、服务、Feishu 客户端或真实回执证据 |
| OPEN_BLOCKER | GitHub 当前仍有开放 Issue 记录的阻塞或风险 |

**本次核验边界：**

- 读取 GitHub main 的文件树、关键文档、源码、Issue 和已合并 PR。
- main 快照包含 1,917 个 Git tree entries；下文目录数量是该快照中各顶层目录下的 tracked file 数量，不是代码行数或覆盖率。
- 没有登录 Tokyo、Dell、Windows 或其他设备；没有查询真实服务状态、计划任务、数据库、Feishu 消息或客户端截图。
- 没有运行测试、构建、部署或修改任何设备。
- 因此，仓库里有代码不等于已安装；PR 已合并不等于设备已更新；服务进程存在不等于功能健康；本地 journal 有卡片不等于飞书已送达。

## 1. 当前总体判断

ServerOS 已经不是空架构：仓库中有 Work Engine、Fleet MCP、Native Core、Intake、Memory、Feishu Hub Watch、日报/报告、测试和设备分发等大量实现。但目前最大的风险不是“完全没有模块”，而是**模块数量已经很多，端到端事实、运行状态、责任边界和真实验收证据没有始终汇聚成一张可信的当前状态图**。

当前最值得关注的结构性缺口：

1. **目录与职责仍有混层。** runtime/ 下同时存在节点守护、代理控制记录、Fleet Hub 装配、卡片/告警/报告/通知等职责；现有目录收敛计划提出迁移，但 reports/ 目标目录尚不存在。
2. **控制面存在 Legacy 与 Next 双轨。** fleet/、mcp/ 与 fleet_mcp/ 同时存在。Owner 已明确要求 Legacy Fleet 保留、Fleet Next 并行演进；较早的目录架构文档仍写着退役 Legacy，形成文档冲突。
3. **理想工作流有设计和部分代码，但完整闭环缺少统一验收。** 飞书/文档摄入、结构化认识、需求接受、Work Engine、研究设计、执行测试、报告发布、Memory 更新分别有模块；本次没有证据证明这些阶段已稳定串成一条持续运行的链。
4. **飞书播报有过真实断链，也有多轮修复。** 历史上曾出现 watcher 正常运行、120 张卡片已生成、但 0 张发出；源码修复已合并。Owner 当前仍反馈播报不工作、卡片难看。当前真实投递状态必须重新核验，不能用历史修复推断现网正常。
5. **可观测性从“没有 trace”进步到“部分任务链可追踪”，但仍不是完整工作系统可观测性。** 已有任务 trace 和链路时间，但没有一个可验证的统一视图覆盖从原始信息进入到最终交付和 Memory 更新的全部阶段。
6. **性能还没有成为可量化的全局预算体系。** 有性能优化方向和局部 timeout，但尚无统一的延迟、吞吐、资源、空闲开销和性能回归基线。
7. **Work/Agent 控制仍有开放的 P0 安全与一致性问题。** Issue #237 记录了 Session fencing、持久化幂等和独占 Work lease 等阻塞项，不能仅凭代码存在或单测通过视为已关闭。
8. **Memory Provider 已有部署记录，但业务消费者接入并不完整。** 现有 Memory 设计明确 OpenViking 是派生、非权威层；截至文档记录，自动 capture 未配置，且没有 consumer 读取 Provider。
9. **项目状态文档本身有时间漂移。** 多份核心文档标注为 9 月或 10 月的设计/历史测量；不能直接把它们当作 11 月 13 日的设备实时状态。

## 2. 仓库当前目录地图

以下按当前 main 的真实目录组织，而不是理想架构。目录职责是对现有文件的归纳；有混层的地方明确标出。

| 顶层目录 | 文件数 | 当前职责 / 主要内容 | 当前结构性关注点 |
|---|---:|---|---|
| abilities/ | 15 | Ability、Provider、Target、Pack 契约和解析/绑定逻辑 | 可复用能力目录的权威仍在 ability-management 外部仓库；本仓库主要负责运行时投影和绑定 |
| bin/ | 4 | serverosd、serverosctl、Fleet MCP、Work MCP 等入口 | 入口实际指向哪个安装版本，需设备 readback |
| context/ | 8 | Actor、信任绑定、委派、上下文披露和解析 | 需要与 Work Engine 的 owner/epoch/lease 权威保持一致 |
| deploy/ | 5 | V13 LIVE heartbeat、period report 等 systemd unit/timer 模板 | 模板存在不代表已安装、启用或成功执行 |
| distribution/ | 105 | 构建、设备 Kit、Windows、Claude/Codex、MCP、Key Center 和安装分发 | 设备产物、源码 revision、实际安装 revision 必须分开记录 |
| documents/ | 2 | 文档治理与词汇模型 | 需要与 Feishu Docs、Drive 和 records 的正文权威对齐 |
| fleet/ | 25 | 设备 Registry、Profiles、Transport、旧 Controller / Agent Control Router | Legacy 控制面仍保留；不可按旧文档直接退役 |
| fleet_mcp/ | 78 | Fleet 新入口、授权、身份、Session/Run 路由、Node Protocol、Inbox、Trace、Workboard | 与 Legacy Fleet 的能力和运行边界需要持续清晰化 |
| intake/ | 69 | Source Policy、Capture/Reading Waterline、Understanding、Daily Cycle、Focus、Global Board、文档发布 | 模块存在；真实来源覆盖、语义 writer 消费链和每日运行回执仍需核验 |
| integration/ | 1 | 组件登记入口 components.yaml | 应成为组件归属/依赖盘点的基础之一，不宜再造平行登记表 |
| integrations/ | 285 | Hub Watch、Agent Monitor、Milestone、Network Health、Fleet Next、Release/QA 等集成包 | 多代组件并存，需要版本、Owner、消费者、部署状态和替代关系索引 |
| mcp/ | 10 | 较早的 Agent MCP、Human Control、BotMux、Codex App Server 等适配器 | 与 fleet_mcp/ 并存是现状；Legacy 与 Next 的职责不可混淆 |
| memory/ | 15 | Memory Record、Scope、Projection、Handoff、OpenViking Provider/Backup | Provider 已有实现和部署记录，但自动 capture / consumer 接入仍有缺口 |
| native/node-core/ | 180 | C++ Node Core / Core-Lite、Windows/Linux 原生适配、观察和恢复逻辑 | 代码与测试不代表三台设备都已安装并通过真实验收 |
| plugin/ | 3 | 设备侧 Plugin Bundle 与 Profile | 应与节点 Runtime 独立构建、独立发布 |
| plugins/ | 21 | 指挥侧 serveros-fleet-ingress 插件及其资源 | 生成产物与源插件目录需保持一致 |
| relations/ | 3 | Project/Relation schema 与解析 | 项目关系应成为跨文档、需求和 Work 的可追踪关联 |
| releases/ | 4 | ServerOS release manifests | release manifest 需要与设备安装回执对账 |
| research-exchange/ | 11 | Research Packet / Result 合同、模板和工具 | 有研究交换基础；需要与正式 Requirement、Design、Work Item 形成稳定引用 |
| runtime/ | 123 | Python daemon、health、inventory、resource、alert、card、Feishu transport、daily report、report delivery、workbench、continuation、Fleet 装配等 | 当前最明显的混合职责目录；D2/D3/D4 收敛计划尚未完成 |
| scripts/ | 10 | Build、Release、Hermetic Tests、Verify、Runtime Truth 等脚本 | 需要让每个脚本输出可机器读取的版本和验收证据 |
| skills/ | 10 | ServerOS Skill、References 和脚本 | 是 Agent 使用说明，不应成为第二套权威架构或状态库 |
| space/ | 8 | Meeting Bundle、会议策略、会议相关性和解析 | 应与飞书 Minutes/Drive 来源关联和权限范围一致 |
| workbench/ | 15 | TODAY、BOARD、STATUS、COVERAGE、REPORTS、DECISIONS 等人类阅读视图 | 是投影/工作台，不应成为 Work Engine 的第二个可写权威 |
| workengine/ | 23 | Item、Activity、Run、Board、Store、State Machine、Lease/Authority 等正式工作控制 | 应是正式 Work 生命周期权威；Issue #237 仍记录关键一致性阻塞 |
| tests/ | 417 | 单元、契约、集成、平台和验收测试 | 文件数量不是覆盖率；本次未执行测试，也未读取当前 CI 全量结果 |
| docs/ | 126 | 架构、设计、运行手册、计划、Proposal、Review 和历史证据 | 文档日期与状态跨度大，需要显式区分当前、历史、设计和验收 |
| .github/ | 6 | GitHub Actions 与 Hosted CI 脚本 | Hosted CI 结果必须绑定 exact SHA；不能替代设备验收 |

### 2.1 当前缺失或需要特别说明的目录

- **reports/**：当前 main 没有这个顶层目录。现有卡片、日报、告警、通知和报告模块仍大量放在 runtime/。目录收敛计划 D3 已明确把 26 个 product 模块、约 220 处外部引用和相关 timer/unit 迁到 reports/，但当前树仍未体现该迁移完成。
- **fleet_hub/**：目录收敛计划 D2 曾提出从 runtime/ 迁出 Hub 装配模块到 fleet_hub/；当前 main 仍可见 runtime/fleet_mcp_owner.py 等装配代码，尚不能认定迁移完成。
- **workengine/agent_control/**：D4 曾提出把代理控制记录并入 Work Engine 权威边界；当前 workengine/ 下仍未见该目标子目录，runtime/ 中仍有 agent_control_store、coordination、continuation 等模块。
- **ops/** 或独立运行证据仓：本仓库有监控架构、运行脚本和 Workbench 视图，但当前没有一份由实时设备回执持续刷新的完整 Fleet 运行状态快照。不要把静态文档当作实时运行数据库。

### 2.2 目录混层和多代实现

- runtime/ 当前同时承载 L1 节点守护、L0 代理控制状态、L2 Fleet Hub 装配和 product 报告/通知。现有 D2/D3/D4 计划已经识别这一问题，不能通过继续增加 runtime/ 文件来回避。
- fleet/ + mcp/ + fleet_mcp/ 是多代控制面并存。Owner 在 2026-10-28 的 Issue #478 明确 Legacy Fleet 保留、Fleet Next 双轨并行；这不是简单的“旧目录未清理”。
- integrations/ 下同时存在 fleet-hub-watch、fleet-next-agent-monitor、fleet-next-agent-monitor-activation、fleet-next-monitor-source-adapter、fleet-v1-milestone-* 等组件。它们有不同阶段和职责，不应粗暴合并，但需要明确每个组件是当前权威、兼容适配、实验、候选还是历史。
- CARDS-STYLE.md、runtime/cards.py、integrations/fleet-hub-watch 的 CardKit renderer/spec、V13 监控卡和其他通知卡属于不同消费面。需要统一视觉规则和语义来源，不意味着所有卡都必须强行使用同一个渲染器。

## 3. 目标工作体系：一条可追踪、可恢复的端到端工作流

ServerOS 应将信息、理解、需求、工作、研究设计、执行验证和交付反馈连成一条有证据的链路。它不是强制每条消息都走完所有阶段；简单信息可以归档，简单任务可以跳过正式研究设计，但正式 Work Item 必须有目标、Owner、授权范围和验收条件。

~~~text
外部信息与运行事实
  Feishu IM / Docs / Wiki / Minutes / Drive
  GitHub Repo / Issue / PR / CI
  Web / Papers / Research
  Agent Session / Run / Device / Service / Resource
              |
              v
1. Source Intake
  来源登记、授权范围、身份、时间、原始事件、附件/材料、快照、
  Capture Watermark、Material Coverage、读取状态
              |
              v
2. Structured Understanding
  Observation -> Claim -> Conflict -> Decision Candidate
  Project / Person / System / Dependency / Current State
              |
              +------> Memory Provider（派生、可召回、非权威）
              |
              v
3. Requirement Intake
  Candidate -> 去重/补充证据/澄清 -> Owner 接受或拒绝
  -> 正式 Requirement / Work Engine Item
              |
              v
4. Daily Alignment & Focus
  来源增量、当前理解、已接受需求、活动 Work、依赖和资源对账
  -> 今日 Focus / Board Projection / 需要 Owner 决策的事项
              |
              v
5. Research & Design（按需）
  调研问题 -> Evidence -> Alternatives -> Design / ADR
  -> Acceptance Criteria / Risk / Dependency / Rollback
              |
              v
6. Execution
  Work Engine Item -> Activity -> Run
  Agent / Session / Worktree / Environment / Ability / Credential Reference
  -> Bounded execution / Lease / Checkpoint / Progress evidence
              |
              v
7. Verification & Acceptance
  Unit / Contract / Hosted CI / Device / E2E / Client / Production
  -> exact SHA + environment + test result + artifact + reviewer receipt
              |
              v
8. Delivery & Publication
  Git Commit / PR / Artifact / Feishu Card or Message / Report / Document
  -> actual provider receipt / message_id / document revision / delivery state
              |
              v
9. Reconciliation & Memory Update
  更新项目当前认识、Requirement 状态、Work 状态、报告索引和派生记忆
  -> 记录未完成边、未知效果和下一轮唤醒条件
~~~

### 3.1 每一阶段必须留下什么

| 阶段 | 最小持久对象 / 证据 | 不能混淆的状态 |
|---|---|---|
| Source Intake | source_ref、范围/授权、snapshot/material ref、observed_at、coverage/watermark | 消息已捕获 ≠ 附件已读取 ≠ 语义已理解 |
| Understanding | observation/claim/conflict/decision、source revision、confidence/provenance | 模型推论 ≠ 已接受事实 |
| Requirement | requirement_ref、来源、Owner decision、priority、acceptance criteria | 候选需求 ≠ 已接受需求 |
| Daily Focus | cutoff、source delta、当前 Work/依赖、Focus receipt | 生成日报 ≠ Daily Cycle 真正运行 |
| Research/Design | evidence refs、方案、反例、风险、决策、AC | 调研完成 ≠ 方案批准 |
| Execution | Item/Activity/Run、Agent/Session、worktree、lease、attempt ID | Dispatch ACK ≠ Agent 开始执行 ≠ 任务完成 |
| Verification | exact SHA、测试环境、命令、结果、CI/设备/E2E receipt | Hosted CI PASS ≠ 真机安装通过 |
| Delivery | artifact/document version、recipient、provider receipt、message_id | 发送尝试 ≠ DELIVERED ≠ Owner 已读 |
| Reconciliation | updated understanding revision、Work state、Memory provenance、unresolved edges | Memory 命中 ≠ 当前事实权威 |

### 3.2 权威边界

- GitHub 源码、Commit、PR、CI：工程源码与版本权威。
- Work Engine：正式 Item、Activity、Run、状态转换、授权和执行回执的工作生命周期权威。
- ability-management：可复用 Ability / Skill / Provider 的能力供应链权威。
- Source / Records / Claims / Project Understanding：当前事实和有来源的结构化认识权威。
- Memory Provider / OpenViking：派生、非权威的长期召回层；不能覆盖当前 Source、Claim、Requirement 或 Work 状态。
- Feishu Board / Workbench / Cards：人类阅读和操作投影；不应再生成第二套独立状态。
- Device Runtime / serverosd：设备观察和受控本地运行权威；不替代 Work Engine，不自行决定业务 Focus。
- Feishu Delivery Receipt：证明指定内容向指定目标投递的回执；不能证明用户已经阅读。

## 4. 当前各阶段的实现和缺口

| 阶段 | 当前仓库证据 | 当前缺口 / 风险 | 当前判断 |
|---|---|---|---|
| Source Intake | intake/source_policy.py、source_reading.py、source_waterline.py、capture_coverage.py、material_index.py 等 | Feishu IM、Docs/Wiki、Minutes、Drive、Web 的真实覆盖范围、水位、附件读取成功率和权限变化没有在本次静态审计中得到当前运行回执 | SOURCE_CONFIRMED；RUNTIME_NOT_VERIFIED |
| Understanding | intake/understanding_delta.py、understanding_patch.py、understanding_semantic.py、understanding_brief.py | INFORMATION-DOCUMENT-LIFECYCLE.md 记录：content_identity/card_digest 已进源码，但语义 writer 的正向消费者仍需核验；focus_source 仍列有 NOT_PRODUCED 项 | 部分实现；端到端消费者待核 |
| Memory | memory/record.py、scope.py、projection.py、handoff.py、openviking/ | memory-fabric.md 的 2026-09-27 状态表记录 OpenViking 已部署、认证访问和备份已建立；同一文档也明确“没有 consumer 读取 Provider”，auto-recall/capture 未配置 | Provider 有部署记录；业务协同不完整 |
| Requirement Intake | workengine/request.py、intake/global_board.py、focus_source.py、board-contract.yaml | 本次未读取当前 Work Engine 数据库、Board 实际状态或 Feishu 看板映射；候选、正式接受和看板投影需要有 fresh receipt | SOURCE_CONFIRMED；RUNTIME_NOT_VERIFIED |
| Daily Focus | intake/daily_cycle.py、daily_reading.py、runtime/serveros-daily.*、evening-alignment.* | 有每日工作循环设计和定时单元；本次没有真实最近一次成功运行、cutoff、水位、Focus 和交付回执 | 设计与实现并存；实时执行待核 |
| Research & Design | research-exchange/、docs/proposals/、docs/plans/、docs/reviews/ | 有研究包、Proposal 和 Review 结构，但没有统一证据证明正式 Requirement 到 Research Packet、Design、AC、Implementation 的关联全部完整 | 部分实现 |
| Execution | workengine/、fleet_mcp/、fleet/、mcp/、integrations/ | Issue #237 仍开放并记录 Session fencing、持久化幂等、独占 Work lease 等 P0 风险；Legacy/Next 需要保持双轨边界 | OPEN_BLOCKER |
| Verification | tests/、.github/workflows/、scripts/hermetic_tests.py、Fleet QA packages | 417 个测试文件不等于 417 项验收通过；本次没有运行测试，也没有收集当前 main exact-SHA CI 结果和三设备证据 | SOURCE_CONFIRMED；本轮 NOT_RUN |
| Reports & Publication | runtime/daily_report.py、report_delivery.py、report_publication.py、workbench/REPORTS.md | 生成、发布和投递分散在 runtime/；reports/ 尚不存在；没有本次实际日报生成、发布、Feishu receipt 的端到端证明 | 部分实现；交付闭环待核 |
| Feishu Broadcast | integrations/fleet-hub-watch/、runtime/message_transport.py、direct_lark_notify.py、cards.py | 历史静默发送缺陷已有源码修复；Owner 当前仍反馈播报故障和 UI 不满意；目标设备现网状态未核 | MERGED_SOURCE_FIX + OWNER_REPORTED + RUNTIME_NOT_VERIFIED |
| Observability | runtime/health.py、fleet_mcp/trace.py、fleet-hub-watch heartbeat、NODE-OBSERVABILITY.md | 已有任务级 trace 和部分自检，但缺少全工作流、来源覆盖、设备、Agent、资源、Memory、报告和 Feishu 的统一可追踪视图 | 部分实现；覆盖不足 |
| Performance | bounded_executor、bounded_invocation、部分 Provider timeout、Native C++ | 没有统一 P95/P99、吞吐、资源、空闲开销和性能回归基线；部分外部调用仍无 timeout | 需求已提出；基线不足 |

## 5. 飞书播报：历史故障、源码修复与当前体验缺口

### 5.1 已有能力与历史故障

当前源码中存在：

- integrations/fleet-hub-watch：Tokyo Hub Watch，负责发现/观察 Run、资源卡、日报/完成卡、告警和 Feishu 投递。
- runtime/message_transport.py：已认证 lark-cli 的消息发送适配。
- runtime/direct_lark_notify.py：OUTBOUND Feishu 通知适配；模块文档明确它只解决出站，不代表 Feishu 入站身份/审批已完成。
- runtime/cards.py：ServerOS 卡片模型的 Feishu JSON 与 HTML preview 渲染。
- integrations/fleet-hub-watch/fleet_hub_watch/feishu.py：目标解析、lark-cli 自检和卡片按钮事件消费。
- integrations/fleet-hub-watch/fleet_hub_watch/notify.py：发送/PATCH、卡片生命周期和投递账本。

**历史真实故障：Issue #560（2026-11-06）**

当时 Tokyo 的 serveros-fleet-watch 已运行约 8 小时，inbox 有 232 个事件，notify 数据库有 120 张已生成卡片，但真实飞书投递为 0。原因是服务使用了 journal 发送方式：卡片写入本地 notify.journal.jsonl，没有发到 Feishu。

后续源码修复：

- PR #562：取消 --notify 的静默默认值；未指定目的地时拒绝启动，不再默默 journal。
- PR #576：设备 Kit 默认选择 lark:owner，并在无法解析接收者时明确报错。
- PR #577 / #578：增加目的地解析、lark-cli 身份自检和相关健康状态修复。

这些 PR 已合并到 main，说明历史静默发送缺陷有源码修复。**这不证明当前 Tokyo 安装的 unit 已更新，也不证明当前 Feishu 投递正常。**

### 5.2 当前 Owner 反馈

<OWNER_REPORTED> 2026-11-13：

- 飞书播报目前有挂掉/不工作的情况。
- 当前播报卡片视觉效果仍然太丑、不够清楚。
- 整体可观测性不足，无法快速判断究竟是采集、判定、生成、发送、更新还是回执阶段出了问题。

本次没有设备或真实 Feishu 客户端访问，因此不推断当前故障原因，也不把 #560 的历史原因套用到今天的问题。

### 5.3 卡片 UX 已多轮修改，但仍未形成稳定验收

相关已合并改进：

- PR #566：CardKit header tag、聊天列表摘要和紧凑告警卡。
- PR #589：统一 Hub 发送资源卡、按设备生成丰富资源卡。
- PR #591：Feishu 可问询状态/任务/资源等卡片，增加任务 Trace。
- PR #597：自然语言资源查询和每台设备完整资源卡。
- PR #600：恢复资源历史曲线。
- PR #604：针对 Owner 反馈修复日报层级、重复问题、曲线标签和资源卡内容。
- PR #605：统一 Feishu 卡片视觉风格。
- PR #608：增加模型辅助的模糊意图识别和人话回答；需要配置 intent command，默认关闭。
- PR #621：修复网络卡表格截断、错误丢包图和不准确网络建议。
- PR #625：补充从指挥端处理到任务、设备结果、批次和卡片更新的链路时间。

这些提交说明卡片体验已经多次迭代，但**多轮修复不等于最终 UX 已验收**。Owner 当前反馈应作为新的验收输入，不能因为历史 PR 已合并就标记“卡片美观问题已解决”。

### 5.4 飞书播报目标体验

所有主要卡片（资源、状态、告警、任务完成、日报、网络、投递故障）需要统一遵守：

1. **第一屏 5–10 秒能看懂。** 当前总体状态、发生什么、影响什么、建议下一步。
2. **视觉层级一致。** 标题、状态 Tag、关键指标、设备/任务分组、建议、时间和技术详情有统一规则。
3. **信息密度合理。** 不把 UUID、内部错误码、原始账本计数、长 JSON 和大量免责声明放首屏。
4. **状态语义准确。** UNKNOWN/STALE 不伪装成正常；采集成功不等于业务健康；发送尝试不等于 DELIVERED。
5. **异常突出、正常收敛。** 不把一条普通注意事项染红整张状态卡；不刷屏；同一状态卡和 Incident 应按稳定 ID 原位更新。
6. **多端可读。** 桌面端、390px 手机、360px 窄屏、长名称、大数值、多异常、折叠状态都要验收。
7. **原生 Feishu 是最终验收对象。** CARDS-STYLE.md R11 已明确：HTML preview 不能替代 Feishu native render。必须保留授权测试对话中的真实截图和回读证据。
8. **风格一致但不强制同一个模板。** 不同卡片可有不同信息结构，但颜色、字号、间距、Tag、时间、技术详情层级必须来自同一套设计规则。
9. **投递状态可见。** 每张卡显示内部 trace 可查的 send/PATCH 尝试、receipt、remote message_id、UNKNOWN、失败和下一步检查位置。
10. **一处失败不能伪装整条链成功。** 生成成功、写入 journal、发送 API ACK、取得 message_id、原卡 PATCH 成功和用户阅读是不同状态。

### 5.5 Feishu 仍需核验的运行边界

- 当前 serveros-fleet-watch unit 的真实 ExecStart、配置目的地和安装 revision。
- Tokyo lark-cli 的 bot/user 身份、自检时间和授权目标。
- 最近一次成功 send、PATCH、message_id 和失败回执。
- notify backlog、UNKNOWN receipt、journal 新增量和队列最老年龄。
- 日报是否实际生成、发布、投递，是否与资源播报重复或互相覆盖。
- Feishu 模糊意图识别是否配置 intent command；没有配置时应明确降级，不假装可理解自然语言。
- 卡片按钮依赖 Feishu 长连接 callback 配置；listener 在运行不等于 callback 真正收到事件。
- OUTBOUND 发送与 INBOUND 用户身份/审批是两项独立能力。不能以 lark-cli 能发送消息证明回调身份验证、权限判定和审批链也已完成。

## 6. 可观测性：已有局部能力，缺少全局工作系统视图

### 6.1 当前已有基础

- runtime/health.py：有多轴 Health 模型，避免只根据 PID 存在或 TCP 可连就宣称健康。
- integrations/fleet-hub-watch：有 watcher heartbeat、设备状态、事件/卡片处理。
- Feishu 自检：lark-cli auth status 被读入 Hub heartbeat，定期检查 bot/user 身份。
- PR #591：增加 Tokyo Hub task trace，记录 dispatch、inbox、monitor、notifier 等环节，并保留 14 天。
- fleet_mcp/trace.py：可按 Run、Batch、Session 或时间范围读取任务链路，指出停在哪一阶段以及下一步检查方向。
- PR #625：为链路增加阶段时间和等待时间，包括指挥侧处理、派发、进行中卡片、设备执行、结果读取、批次关闭、卡片最后更新和指挥侧回复。
- docs/NODE-OBSERVABILITY.md：定义 CPU/RAM/Swap/PSI、Disk、GPU、服务、网络、Agent/Session、监控自身健康等观察面。

### 6.2 当前可观测性缺口

1. **Trace 不完整。** 现有 task trace 主要从 Fleet 已接收到的调用开始；无法证明它覆盖原始 Feishu IM/Docs 输入、Understanding、Requirement 接受、Research/Design、Work Engine、QA、报告发布和 Memory 更新的全部链路。
2. **源消息起点缺失。** PR #625 说明 ChatGPT 指挥端没有把 Owner 原始输入时间交给 Fleet；当前以连续 Fleet 调用的最早时间作为近似起点。需要明确标记这是 proxy，而不是原始输入时间。
3. **没有统一的系统总览。** 需要在一个可读入口看到设备、Agent/Session/Run、Provider、服务、资源、来源覆盖、Daily Cycle、Work Engine、Memory、报告和 Feishu 的状态与更新时间。
4. **缺少数据覆盖水位视图。** 需要区分 IM capture、Docs/Drive material discovery、附件读取、语义理解、Claim/Understanding 提交和 View/Board 发布的水位。
5. **缺少链路级失败定位。** 不能只看到“日报没来”或“飞书没播报”；应能快速定位 source、scheduler、producer、renderer、transport、receipt 或 consumer 的具体阶段。
6. **监控自身需要被监控。** watcher tick、last_success、cursor_age、backlog、队列最老年龄、trace 写入失败、provider timeout、Feishu auth、发送失败和日报发布失败必须有自检。
7. **设备覆盖没有当前证据。** Issue #531 的最近公开验收矩阵仍列 Tokyo、Dell A6000、FJPCOS Windows 的 Ability、原子能力、监控/播报多项 NOT_RUN。该矩阵日期为 2026-10-29，属于待更新的历史跟踪状态；没有新的设备回执前不能宣称三设备已验收。
8. **缺少统一时间线。** 每个阶段要同时显示 observed_at、started_at、finished_at、age、duration、deadline、source revision 和 receipt；不能用 report 生成时间覆盖事实采集时间。
9. **缺少可观测性 SLO。** 还没有全局定义数据新鲜度、探针成功率、任务可追踪率、告警投递成功率、Trace 完整率和自监控发现延迟的目标值。
10. **状态权威分散。** Workbench Markdown、Feishu 卡、Hub Trace、Work Engine 和各个本地 Journal 的职责必须清楚；任何一个视图都不能悄悄成为第二套可写状态。

### 6.3 目标可观测性入口

目标是一个统一的 Fleet/Work System Overview，至少包含：

| 区域 | 必须回答的问题 |
|---|---|
| Devices | 哪些设备可达、运行哪个版本、最后一次可信采样是什么时候 |
| Agents & Sessions | 哪些 Agent/Session 正在工作、属于哪个 Work/Run、是否有真实进展 |
| Work Engine | 当前 Item、Activity、Run、Owner、Lease、Deadline、Blocked/Unknown |
| Sources | 飞书/文档/GitHub 等来源是否在采集、覆盖范围、watermark 和失败原因 |
| Understanding & Memory | 哪些 Claim/Conflict/Decision 更新了；Memory Provider 是否可用、是否有消费者 |
| Daily Cycle | 最近一次运行、输入 cutoff、完成水位、Focus、未完成边 |
| Research & Design | 当前证据、设计版本、决策和 AC 是否已接受 |
| Tests & Acceptance | exact SHA、CI、设备测试、E2E、Review 和未通过项 |
| Reports & Delivery | 报告版本、发布状态、收件人、Feishu send/PATCH receipt |
| Performance | 延迟、吞吐、CPU、内存、磁盘/网络、进程数、队列和性能趋势 |
| Self-health | 监控器是否真的在推进、是否有过期/停滞/持久化失败 |

## 7. 性能与效率：从局部 timeout 走向全局工程预算

详细非功能要求见 [fleet-efficiency-performance.md](fleet-efficiency-performance.md)。本节记录当前代码审计中直接发现的效率/可靠性风险。

### 7.1 Windows Secrets Sync：源码中存在无界子进程调用

当前 main 的静态证据：

- distribution/keycenter/windows/register-sync-task.ps1：默认每 5 分钟触发；MultipleInstances=IgnoreNew；ExecutionTimeLimit=3 分钟。
- distribution/keycenter/windows/run-hidden-wait.vbs：使用 shell.Run(command, 0, True) 等待并传播退出码；当前脚本没有显式 Job Object / process-tree ownership / timeout cancellation 逻辑。
- distribution/keycenter/windows/sync-secrets.ps1：同步调用 Python CLI，记录结果日志；没有自己的完整进程树 deadline 管理。
- distribution/keycenter/serveros_secret.py::_run：使用 subprocess.run(..., capture_output=True)，没有显式 timeout 参数。
- serveros_secret.py 的 git pull、decrypt、git read 和 restart 等操作通过该 runner 执行。

Owner 在本轮提供的现场排查链：

~~~text
Task Scheduler
  -> wscript.exe run-hidden-wait.vbs
  -> powershell.exe sync-secrets.ps1
  -> python.exe serveros_secret.py sync
  -> git.exe pull --ff-only
  -> ssh.exe vince git-upload-pack
~~~

Owner 报告 SSH 子进程曾卡住；计划任务执行期限到达后出现父进程/后代进程生命周期脱节，并观察到多套残留执行链。该现场根因解释尚未在本次重新登录目标 Windows 设备复核，但仓库代码确实存在缺少端到端 timeout 和进程树回收机制的缺口。

**需求：** 必须把该任务纳入统一进程生命周期治理：有界 Git/SSH 调用、端到端 deadline、进程树取消与回收、单实例、真实终态、可恢复重试、资源泄漏回归测试。不得只调整计划任务执行时长而不解决子进程归属。

### 7.2 全局性能基线缺失

当前尚未建立统一、可持续比较的性能基线：

- Fleet MCP 请求延迟与吞吐；
- 任务排队、派发、启动、执行、结果读取和交付总耗时；
- Node Core 采样周期、采样开销和空闲 CPU；
- Agent Monitor 轮询成本、事件积压和恢复耗时；
- Feishu 发送/PATCH 延迟、失败率、限流与重试；
- Daily Cycle/Source Intake 的吞吐、覆盖滞后和处理成本；
- Memory Recall 的延迟、命中率和上下文注入开销；
- Windows/Linux 不同设备的常驻内存、峰值内存、进程数、句柄数和网络流量。

每个关键组件应有基线、负载条件、P50/P95/P99、资源成本、故障注入和回归阈值。没有测量数据时，不编造性能预算。

### 7.3 Rust 与现有 C++ / Python 的定位

Owner 已明确效率和性能是几乎最重要的工程环节，并希望大量适合的模块考虑 Rust。

需求记录：

- 高频、常驻、并发密集、低延迟、资源敏感路径必须主动评估 Rust。
- 现有 C++ Native Core 不应因语言偏好被无理由重写。
- Python 可继续承担低频编排、集成和脚本，但不应默认用于高频性能关键路径。
- 先 profiling 和基准，再选择 Rust/C++/Python；不做无证据的全仓重写。
- 优化目标包含响应速度、吞吐、资源效率、稳定性、部署成本和迭代效率。

## 8. Work Engine、Fleet 双轨和架构一致性风险

### 8.1 Issue #237：仍开放的 P0 工作控制阻塞

Issue #237 仍为 OPEN，记录了以下待关闭问题：

- Session handoff 后旧 Session 是否仍能写入同一 Work/write scope，需要 owner/epoch/fencing 在真实 mutation admission 和 side-effect boundary 生效。
- 网络超时或进程重启后的重复 Send/Mutation 需要持久化 attempt identity 和 effect reconciliation；无法确认时必须是 EFFECT_UNKNOWN，不能盲目重试。
- Work Engine 的独占 Work lease 与 runtime resource occupancy lease 必须区分；旧 token/过期 lease 不能继续授权写入。
- 跨设备真实请求、断线恢复和结果回执需要 exact device/runtime/grant provenance。

这些是开放 Issue 中的实现/验收要求，不在本文件中声称已确认当前部署存在安全事故；但在关闭前应作为高优先级架构风险跟踪。

### 8.2 Legacy Fleet 与 Fleet Next 的文档冲突

- docs/architecture/FLEET-FOUR-LAYERS.md（2026-10-26）仍写有旧控制面退役的决定。
- Owner 后续在 Issue #478（2026-10-28）明确决定 Legacy Fleet 继续服务当前 ChatGPT 控制，Fleet Next 独立发布、配置、状态和验收，双轨并行；未经另行批准不能切换插件、端口、OAuth、DB、state、lock 或生产路由。

**要求：** 以 #478 的后续 Owner 决定为准，更新过时架构/目录计划的表述。Legacy 保留不是待清理缺陷；Next 不能以源码合并或测试通过代替实际安装和切换验收。

### 8.3 V1 三设备验收状态仍需更新

Issue #531 是当前公开的 Fleet V1 总跟踪入口，范围为 Tokyo、Dell A6000、FJPCOS Windows 的四部分（Ability、原子控制能力、serverosd 监控/播报、指挥侧 Skill）和一次真实自迭代。该 Issue 的公开验收矩阵仍列多项 NOT_RUN，尤其三台设备的监控与播报没有真实回执。

该矩阵的最后明确日期为 2026-10-29；本次没有新的设备状态采集。因此准确表述是：**最后公开跟踪矩阵未证明通过，当前设备状态待新回执刷新**，而不是直接断言今天三台设备全部离线或全部未安装。

## 9. 文档与状态治理缺口

当前需要治理的不是“再多写几篇文档”，而是让文档有唯一职责和可信更新时间：

1. docs/architecture/FLEET-FOUR-LAYERS.md 的 Legacy 退役表述需要按 #478 更新。
2. docs/plans/fleet-v1-gap-and-work-packages.md 是 2026-10-26 的计划；其中 D2/D3/D4 目录迁移目标仍需对照当前 tree 更新状态。
3. docs/INFORMATION-DOCUMENT-LIFECYCLE.md 是 2026-09-23/24 的设计；其 B2 semantic writer consumer 和 focus_source NOT_PRODUCED 记录需要刷新。
4. docs/daily-work-cycle.md 是 2026-09-24 的设计；必须附最近一次真实 Daily Cycle receipt 才能说明当前运行。
5. docs/NODE-OBSERVABILITY.md 明确是产品/架构修订，不是运行实现；需要当前设备采样、source freshness 和 self-health 证据。
6. docs/MONITORING-PRODUCT-ARCHITECTURE.md 和 docs/MONITORING-ACCEPTANCE.md 明确区分 DESIGN != LIVE；后续状态更新要保持这一原则。
7. docs/memory-fabric.md 有 OpenViking 2026-09-27 部署记录，但同时写明无 consumer、未配置自动 capture；需要补当前实际 provider 和 consumer 回执。
8. 文档、PR、Issue、Work Engine Item、Feishu Board 不能各自维护一套相互矛盾的状态。应让正式 Work 状态只有 Work Engine 一份，其他系统发布其视图。

## 10. 建议优先级：从当前缺口形成正式需求

下表是根据本次静态审计和 Owner 当前反馈整理的**候选优先级**，不是已接受的 Work Engine 排期。

| 优先级 | 领域 | 当前问题 | 需求结果 / 验收方向 |
|---|---|---|---|
| P0 | Feishu Delivery | Owner 报告当前播报故障；历史曾发生 journal 静默不发送 | 读取当前 Hub/Feishu 状态；一条真实 Run/状态变化端到端发出并拿到 receipt；失败能定位到具体阶段 |
| P0 | Windows Sync Lifecycle | 任务有 3 分钟期限、5 分钟触发；Python runner 无显式 timeout；无进程树回收合同 | 故障注入下无孤儿进程、无并发堆积、真实终态和可恢复同步 |
| P0 | Work/Agent Safety | Issue #237 的 fencing、持久化幂等、独占 lease 仍开放 | 对应并发、重启、handoff、unknown effect 场景有 exact evidence 并关闭阻塞 |
| P0 | Fleet Live Observability | 没有本次最新的三设备、Hub、Agent Monitor、Feishu 运行快照 | 统一只读基线，明确设备版本、最后成功采样、数据新鲜度、Monitor 自身健康和送达回执 |
| P1 | Feishu UX | 多轮样式修复后 Owner 仍不满意 | 统一设计规则；状态/资源/告警/日报/网络/任务卡 native desktop/mobile 截图验收 |
| P1 | End-to-end Trace | 现有 trace 主要覆盖 Fleet 任务链，不覆盖完整信息到 Memory 闭环 | 为 Source、Understanding、Requirement、Item、Activity、Run、Test、Artifact、Report、Feishu、Memory 建立可关联 Trace |
| P1 | Source Coverage | IM/Docs/Wiki/Minutes/Drive/Web 的实际覆盖与理解水位不透明 | Capture/Material/Reading/Understanding/Claim/View 水位独立展示，缺口可定位 |
| P1 | Memory Integration | OpenViking 有部署记录，但文档称无 consumer、auto-capture 未配置 | 明确哪些消费者读取何种 scope，召回结果如何引用权威 Source/Claim，失败如何降级 |
| P1 | Directory Convergence | runtime 混合四种职责，reports/ 尚不存在 | 按 D2/D3/D4 的现有计划小步迁移，不另建平行系统 |
| P1 | Architecture Consistency | Legacy/Next 双轨决策与旧文档冲突 | 更新目录、架构和发布文档，保留 Legacy 并明确 Next 独立边界 |
| P1 | Daily Cycle & Board | 设计和模块存在，但真实每日运行/Board readback 不透明 | 最近一次 cutoff、source delta、Focus、未完成边、owner decision 和 delivery receipt 可读 |
| P2 | Performance Baseline | 缺少全局 SLO 和性能回归 | 建立基准，逐项评估 Rust/C++/Python，按测量结果优化 |
| P2 | Component Inventory | integrations/ 多代组件并存，当前替代/Owner/安装状态不够直观 | 复用 integration/components.yaml、layers.yaml 和现有文档建立组件责任/状态索引 |

## 11. 必须形成的统一状态视图

后续不应让 Owner 在 20 个目录、几十个 Issue 和多张飞书卡之间拼接当前状态。建议形成一份可自动更新的 System Map / Current State View，至少有：

- Component ID、名称、Owner、职责、权威来源、代码目录、消费者和依赖；
- 状态：DESIGN_ONLY / SOURCE_CONFIRMED / TESTED / PACKAGED / INSTALLED / RUNNING / LIVE_VERIFIED / RETIRED；
- exact source SHA、artifact digest、设备、OS principal、安装路径和当前运行 revision；
- 最近一次成功执行、最近一次失败、last_seen、observed_at、TTL 和 evidence ref；
- 当前 Bug / Open Issue / Blocker、影响范围、临时绕行和恢复入口；
- 性能指标和资源预算；
- Feishu 目的地、卡片版本、最近 send/PATCH receipt、失败/UNKNOWN；
- 数据来源覆盖、水位、权限范围和缺失项；
- 对应正式 Work Engine Item、Activity、Run、PR 和验收证据。

状态视图必须从现有权威读取并标注新鲜度，不得成为第二个可写 Work Engine、第二套 Memory、第二个消息账本或第二个组件注册表。

## 12. 仍需 Owner 确认的关键问题

1. **飞书产品面：** 是否继续将资源状态、可行动告警、任务完成、日报和网络信息作为不同卡型，但统一设计系统？还是进一步收敛产品面？
2. **广播故障范围：** 当前“飞书播报挂了”具体是资源卡、任务完成、日报、告警、网络卡，还是全部出站消息？本次没有现场证据，不擅自指定根因。
3. **UX 验收设备：** 桌面端与手机端分别以哪些真实客户端、窗口宽度和接收目标作为最终验收？
4. **可观测性主入口：** 是否以 Workbench 为主要人类入口，Fleet Trace 为按任务钻取工具，Feishu 为通知与轻量查询面？
5. **Memory 接入：** 哪些 Agent/ChatGPT/Work 消费者应读取 OpenViking，允许哪些 scope，哪些内容只能来自 hwz-records / GitHub 的权威正文？
6. **三设备 V1：** Issue #531 中旧的 NOT_RUN 矩阵是否已有新的设备回执，需要用新证据刷新，而不是沿用旧日期？
7. **目录迁移：** 是否继续按 D2/D3/D4 逐步迁移 runtime，而不是一次性大重构？
8. **性能目标：** 需要支持的节点规模、每节点并发 Agent/Session、关键路径 P95/P99、资源预算和 Rust 默认评估范围。
9. **工作系统验收：** Daily Cycle、Feishu Intake、Understanding、Memory、Board、Report 的哪些端到端场景必须列为第一批 P0/P1？

## 13. 本文件的完成标准

- 当前仓库的主要顶层目录与职责已按 main 文件树记录；
- 代码存在、设计存在、已合并、已安装、运行中和真实验收被严格区分；
- 理想信息流与工作流可从来源一直追踪到交付和 Memory 更新；
- 飞书历史故障、后续源码修复和 Owner 当前反馈分别记录；
- 可观测性现状与完整工作系统可观测性目标明确区分；
- 性能问题与 Windows Secrets Sync 代码缺口有具体证据；
- Work Engine P0 风险、Fleet Legacy/Next 决策和文档冲突被记录；
- 所有未核验的设备、服务、Feishu 客户端和运行状态明确标记 RUNTIME_NOT_VERIFIED；
- 后续实施应拆成正式 Work Engine Items，不以本文件替代需求接受、设计、实现、测试或验收。
