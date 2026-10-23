# Daily Work Cycle · 有真实接收者的每日工作

> **这篇回答什么：** ServerOS 的一天怎么运转：采集与理解两条水位、对账后最多两个 Focus、按需展开研究与执行、报告与通知，以及哪些环节由定时节点唤醒、哪些必须由 Work Engine 驱动。
>
> **状态：** 设计 · 2026-09-24。这是产品与自动化的收敛，**不新增** workflowd、第二任务数据库或固定业务阶段机；产品规范仍以 [ARCHITECTURE.md](../ARCHITECTURE.md) 为准。
>
> **三条边界：** ① 一天不等于「必开两个新任务」，也不要求写代码或走完每个阶段；② 定时节点只唤醒有限 routine，不做业务判断；③ 报告写出来不等于这些步骤真的运行过。
>
> **怎么读：** §1–§2 是目标与一轮的输入输出；§3–§5 是授权、采集水位与 Focus；§6–§8 是按需研究、执行与交付；§9–§10 是故障与调度资格；§11–§17 是历史缺口、定时节点、`serverosd` 边界、自迭代与默认自主推进。

**配套：** 通用交接、预算与恢复见 [COLLABORATION-CONTRACT](COLLABORATION-CONTRACT.md)；每项职责的场景合同见 ability-management 的 `ABILITY-SCENARIO-CONTRACTS.md`。

## 1. 已接受的目标

每天增量接收真实工作信息、修正结构化认识并对账；晚间形成0–2个合格工作焦点。简单、低风险任务由原生Claude/Codex直接完成；只有复杂度、风险或Completion Boundary值得时才展开GPT深度研究/设计、独立Session review、Quay并行或真实E2E。需要的验收、文档、报告、发布与通知按目标展开，不组成固定套餐。正常授权范围内少找人，重大目标/权限/生产效果与真实非预期产品失败才升级。

这不是每天必新开两项，也不要求所有任务写代码、使用每种Provider或经历每个阶段。研究、验证已有能力、报告、指导配置可以单独完成。Quay要有可验证的贡献，不为进程忙碌造任务。

## 2. 一轮的输入与结果

一轮读取固定cutoff内的来源变化、当前理解版本、Work Engine真实控制状态、接受目标/AC、已有委托、资源/环境和运行证据。cutoff是本轮一致性边界，不是忽略紧急变化的理由。

本轮结果至少说明：采集/理解哪些范围完成；关键认识变化/冲突；实际选中和继续的焦点；做了哪些有意义工作；验证/发布/投递分别到了哪；哪些待续、谁接收、什么条件唤醒。report写出来不等于这些步骤都运行过。

例行状态、人类日对账与正式日报不同用途，共用事实但不复制正文权威。当前没有任务/没有新信息可合法空结果，不制造日报成果或无意义告警。

## 3. 一次配置与授权

通过已有Agent Control的可信actor/委托记录，明确可读来源/项目/cutoff、可写树/文档章节、允许effect、模型/搜索/Quay费用与资源、review策略、receiver、时区/触发、有效期和撤销。文档本身和模型正文不能签发grant。

模型API、公共搜索、私人材料外发、客户联系、开发服务安装、生产变更、云删除/ACL分别判断。允许本地研究不自动允许Jev外发，也不能用禁止客户邮件推导所有已授权模型API都必须停。当前生产cutover/rollback/高风险配置依旧人执行。

首次真正缺字段就一次列出精确配置和影响；可从现有record/Binding解析的不反复问。用户没出席不把agent_reconciled写成user_confirmed，也不冻结其他已合法授权的工作。

## 4. 采集与理解两条水位

**纠正前版“理解提交后才推进采集游标”的耦合。**

B1对原始事件/manifest安全持久的精确范围推进capture watermark；B2对完成语义patch/相关视图提交的范围推进understanding watermark。模型失败可以留下understanding lag，不重抓已存raw；任何中间gap都不能被最大位置掩盖。

默认SHALLOW、明确bot/app/测试来源NONE、指定必要材料DEEP。NONE不为计数先拉正文；Follow按对象/版本和受控子树；所有private文件/截图均有物化与外发权限边界。

B2读取actual source内容，产生有出处的观察/推论/决定候选和冲突，写入合法authoring对象。现有understanding_delta/understanding_patch的重读提案是前置，不代替语义writer。来源正文变而range没变时旧确认仍失效，patch接受须绑定source digest与当前理解baseline。

只更新受影响people/project/topic/Claim；自己的发布revision通过receipt去重，后续人改不能被吞。撤回/失效先在本地召回过滤生效，远端Memory更新晚到不复活旧结论。

## 5. 对账与最多两项焦点

Card拥有语义，Work Engine拥有状态/Run/lease，Board派生。current store、cards、旧projection按身份/来源对账，历史ACTIVE不当今日worker；取消/被替代不自动重开。没有某项目projection与项目确有0项分开。

对本profile已经选中的root先继续/结束/合法停放；完成后可以为0，不自动从backlog填空位。运行/委派/等待/取消未确认/unknown effect占位；历史record-only不自动占位。明确停放需checkpoint与旧writer/资源确认，不新建Item enum。

准入从Work Engine、Agent Control、实际executor、Registry/Binding和接受需求组装；grant/scope/worker事实不塞Card当独立权威。新候选从没开始过时检查无冲突lease/intent，不要求虚构停止回执。

选择按已有业务价值/优先级、deadline、依赖、可验收和环境就绪，有来源解释；没有priority字段不默认P0，也不永久拒绝所有候选。若授权允许Coordinator排序，记录其建议与接受范围。实际effects前再次原子核scope/revocation/基线/资源/预算。

## 6. 按需研究、设计与独立审阅

这一段只在任务真正需要B5/B6/独立review时展开；普通已知实现、小修、简单文档、已有方案执行不为了Daily Cycle形式再跑GPT设计。需要时采用已有CodexAppServerProvider或已验证exec适配；明确模型/推理配置、允许工具、预算/deadline。设计与plan review若都要求，则使用不同真实Session，提供问题、原始依据、候选方案/反例与具体当前代码，不只给作者摘要。

review receipt绑定Item/scope/source/design/plan/baseline、实际Session、requested model/config和时间；完整turn与有效final对应才算完成。模型APPROVE不授权；field缺失、旧final、同作者、自述adapter_verified不能放行。

CHANGES_REQUIRED在原设计预算内有界修订。相同输入/相同finding且无新证据不重复review刷结论；评审可能错，主侧核对可定位证据，不为了迎合它删正确行为。实质输入变更仅重审受影响组合，不把所有业务变成永远审计划。

人工ChatGPT仍可经GitHub参与深度讨论，但不要求用户每天手动搬材料，也不冒充无人值守操控ChatGPT界面。

## 7. 实际执行、验收与父继续责任

Parent将接受的子任务交真实native/Quay执行端，记身份/输入/allowed effects/资源/预算/验收receiver。接收者确认可执行才accepted，Task创建不等于worker已研究。Quay保持原生driver/队列/工作树生命周期，不重建其调度。

浏览器按当前worker实际工具限制，必要时独立Testing Provider执行；不解除全部worker黑名单。真实登录/headed、多图/断言按AC，mock/synthetic只用于明确的机制验证。

验证目的先声明：复现一个缺陷、确认已有修复、证明新实现不是同一oracle。未复现只限已测路径，产品非预期FAIL按既定粗粒度收证据停自动修；预期拒绝/预期缺陷复现可完成相应诊断任务，但不能报产品已好。

Parent验收结果并决定下一步，不让child自己关闭父Item。parent压缩/退出前有真实checkpoint与接收/唤醒机制才称会自动继续；无scheduler只是待续状态。一个child受阻不取消其他scope正确的工作。

## 8. 文档、报告、通知与反馈

B8组织已验证事实；B9在正确对象/受众/授权章节做局部维护与回读；B10按receiver/静默/幂等发送。三个效果分别记录，失败恢复只补未完成部分。用户要求发出时只有draft不能完工；没有发送目标的研究任务也不强制飞书发送。

自管公开指南保持指定知识库入口；LiteLLM共享doc可Follow+局部patch；Bruce SOP默认只读+规则差异建议。不能保护人改冲突时交候选，不自动whole-body覆盖。原Minutes/文件按用途保留，不在每处复制正文。

静默窗口内完成准备与合法待发intent，actual sender在允许时间消费并回执；没有真实consumer不得声称已排队。知识/工作结果回B2更新，自己报告不反向证明自己的原结论。

## 9. 故障、预算和可选依赖

worker cancel、process exit、business effect、resource released分别有证据；unknown effect先查同attempt，不重放child。父级预算包括子任务/重试/shadow/浏览器subrequest，计费未知先保留尝试再核对。

OpenViking可选、Laya shadow、某远端UI/Windows路径只影响依赖它的步骤；缺生产Runbook只阻断生产变更。Jev未绑定可完成adapter/本地部分，但MarketOps full-MVP要求的实际对照不能被静默删掉。

source取得失败/权限撤销只冻结可能受影响的后续效果，精确scope无法证明时保守暂停该scope；无关项目不被一个旧Board/历史文档全局卡住。基础监控与业务无进展分开：serverosd健康不证明循环做了工作。

## 10. 调度与运行资格

复用OS/已有授权scheduler启动有限worker；一个logical routine唯一trigger owner，checkpoint/intent/receipt进既有control体系。错过周期按语义合并，不能机器恢复后重复开多批。

时区、晚间/次日时刻、预算和告警策略是实例配置。本轮未启用任何timer。现有unit只跑workbench render就只能叫资料刷新，不能写完整日循环LIVE。

完整LIVE前至少跑通SCENARIO-ACCEPTANCE的J06：真实源变化→理解提交→current board与有效委托→真实Parent/执行→required验收→必需交付/回流，并完成duplicate、source漂移、权限撤销、cancel未知和partial发布恢复反例。GPT设计/review、Quay、E2E等可选分支通过各自适用case证明，不强塞进每次J06。其他独立功能可分别启用，但不能汇总成全链已完成。

## 11. 历史实测和当前缺口的位置

2026-09-22原生GPT、Quay gate、合成browser、Windows checkpoint等历史实测在integration-readiness-20260922.md；它们不是当前LIVE/业务完整证明。此次源码反查与风险见WORK-SCENARIO-REVIEW-20260923.md。

执行仍用CC-EXECUTION-PLAN.md的P0–P12；COLLABORATION-IMPLEMENTATION只给本轮接口/失败定位和依赖细化，不另开一套完成度表或工作平台。


## 12. 显式定时节点与Ability owner

Daily Cycle不是单一Ability，也不是serverosd内部循环。时间只唤醒有限routine，前置receipt/state决定能否继续。推荐当前个人实例候选：

| 节点 | 推荐触发 | owner |
|---|---|---|
| 日间轻量增量 | provider event优先；否则实例配置有界poll | A4 scheduler → B1 |
| Evening source cutoff | 19:xx实例时点 | A4 |
| Archive catch-up | cutoff后 | B1 |
| Structured Understanding | B1 receipt后 | B2 + A2 |
| Daily Reconciliation | 20:00提醒；资料不完整则明示，仍可先谈已知项 | B10 |
| Standing daily report（启用时） | 配置的report cutoff后；不阻断夜间Focus | B8 → B9 → B10 |
| Requirement/Board reconcile | 人回复或standing policy允许 | B3 + B4 |
| Active Focus 0–2 | 对齐/接受后，可当晚立即执行；不自动补位 | B4 |
| Session Prep | Focus成立后 | A1 |
| Workspace Prep | Focus成立后 | A3 |
| Runtime Prep | Session/Workspace后 | A4 |
| Optional morning catch-up | 实例需要时核已选Focus；不是下一天才可执行 | A1 + A3 + A4 + B4 |
| Session/Workspace maintenance | 低频夜间 | A1.3 / A3 |

具体时间/时区/预算是instance policy；用户只说“8点”时要结合“归档今天/选明天”语境解析早晚，必要时只问这一处配置。

A1 Session Prep按需要解析Context和真实session控制；只有恢复历史确有必要才调用session-manager的archive/restore，不把三个入口设为每次启动的必经步骤。A3 Workspace Prep必须显式确认repo/base/worktree/dirty owner/remote freshness/write scope。完整职责见 [Ability Operating Model](ABILITY-OPERATING-MODEL.md)。

## 13. serverosd与scheduler的边界

`serverosd`提供长期Node Core事实/控制面：runtime registry、IPC、Agent Control持久机械状态、lease/effect协调、低成本readiness、有限reconcile和自身健康。它不主动抓飞书、做B2语义推理、选业务Focus、管理Session archive、创建worktree或运行长驻workflow。

OS/授权scheduler负责wake finite routine；routine调用对应Ability实现，完成/partial/wait后写receipt并退出。一个logical routine只有一个trigger owner，错过周期按cycle_ref幂等恢复，不重复开批。

## 14. Ability自迭代进入治理而非业务流水线

G1.5 Ability Evaluation & Evolution消费Journey/A7、用户纠正、运行receipt和MetaCC session evidence，比较“Ability理想职责”与“真实完成效果”。只有具体gap才产生候选优化，并在隔离eval/held-out/fresh consumer后走G1正常升级。它不每天盲改Skill，也不自改权限/安全/业务职责。


## 15. Daily Reconciliation 的产品结果

Structured Understanding完成后，B10只把真正需要人的语义变化呈现为：

~~~text
自动更新
需要你决定
冲突
理解缺口
~~~

普通agent_reconciled事实只给摘要，不要求逐条点确认。goal/responsible/deadline/external commitment/permission和关键语义冲突进入decision surface。人的选择通过真实acceptance/correction/defer receipt回B2；没有回复不是默认接受。

`My Document Library/每日对齐`保存该轮的人读projection和真实决定记录，不承担Claim authority，也不替代B8日报。没有重要变化时允许极短或空对账，不为了每天有文档制造语义。

B2 reader应逐步从IM span扩展到Doc/Wiki、Meeting/Miaoji和Web fixed revisions；仍复用单一semantic writer。详见 [UNDERSTANDING-MEMORY-RECONCILIATION](UNDERSTANDING-MEMORY-RECONCILIATION.md)。

## 16. 无人参与、结果到达与错过时点的产品行为

对齐是delta-driven conversation：自动更新、需要决定、冲突、理解缺口按实际存在的内容显示。没有新消息或没有待决定项允许极短/空结果，不制造会议、Requirement或日报成果。20:00是提醒时点，不是用户在其他时刻不能接受需求/选择Focus。

本轮cutoff固定正在解释的材料；之后新来源进入后续批次，重大变动只暂停受影响效果而不推翻全部计划。理解失败不回滚已保存raw；Claim成功而View/Board发布失败只补下游。用户未回复不接受新目标，已有授权Focus仍可推进。

有限接续检查可由真实child结果、合法时间窗口或已登记依赖事件触发：核Work Item当前范围/owner/授权/预算→去重原attempt→唤醒实际Parent→Parent决定下一步。不是timer每响一次就创建一个长期Claude Session。没有已验证的receiver/scheduler只保checkpoint并显示待续。

错过多个周期按来源游标与实际效果有界catch-up；状态播报不追发每个错过整点，关注文档不重建全部镜像，工作不重派已经存在的Task。连续失败显示哪个接缝落后及恢复方式，不用“routine执行成功”冒充全业务完成。

验收：一晚完成后次日Focus为0；无人对齐不冻结原已授权工作；一次child结果只被接受一次；重启后补缺阶段而不重复外部效果；没有真后台执行就不显示运行中。自动补位/自动park、额外成本或新资料外发必须另有接受策略。

## 17. 默认自主推进与打断预算

OpenAI 2026-09-11 的 Astra 指导在本Profile里的落地原则是：description短而可区分、只读任务真正需要的资料、不要把Skill写成固定行程、明确完成边界并允许Agent在安全范围内持续做到完成。它们是G1.4的authoring/eval约束，不是新增runtime阶段。

### 17.1 从请求到完成的默认行为

```text
用户表达目标/动作
→ Native Agent先做可逆/read-only调查
→ 解析当前Requirement/Focus（仅当真的存在或写入需要）
→ 只加载相关Skill/refs/tools
→ 在已接受scope和effects内持续推进
→ 相关局部验证
→ required文档/交付/readback
→ Outcome 或精确 blocker/checkpoint
```

不允许把“我可以做”“我已经列计划”“第一版代码写完”“子Task DONE”“测试命令exit 0”作为默认停点。普通read/grep/计算/临时实验不因producer一词强制创建Item/Run；只有需要长期责任、独立worker、重要成本、代码/环境写入、真实E2E、外部effect或跨Session恢复时才进入相应durable控制。

### 17.2 一个真正的人类停点应满足什么

人工停点应至少命中一种：目标/acceptance有会改变结果的歧义；新增或扩大external/shared/production effect；敏感数据/新受众/费用范围变化；共享WIP/唯一writer冲突；需要用户持有的认证/身份；不可安全自动解决的semantic conflict；当前明确的真实产品验收unexpected FAIL停点。停点只冻结相关scope，其余可独立工作继续。

“Skill要求先确认”“参考文档说按五步做”“某Provider未绑定”“当前没有Run ID”“还没读完整架构”本身都不是停人的理由。

### 17.3 上下文和测试预算

首次选择能力只暴露能区分相邻意图的name/description；选中后只读当前路由。复杂Skill不得因被选中就全读所有reference。项目级AGENTS/CLAUDE只给全局不变量和条件locator，不把架构/数据库/部署/测试手册设成每次编辑前置。

低风险小改运行受影响的最小验证并修本次改动造成的失败；没有风险/变化证据不机械扩大到全库测试或独立review。真实Completion Boundary要求E2E/客户端/安装时再展开A7/A8。测试失败如果只是实施阶段局部问题继续修；独立真实产品验收unexpected FAIL仍遵守当前停止自动产品修改的policy。

### 17.4 连续工作不能依赖用户反复说“继续”

Focus一旦被接受，Parent在真实continuation receiver存在时由child result、依赖满足或允许时间窗口唤醒；恢复前对账baseline、writer、effect和旧attempt。没有receiver时准确显示“checkpoint已保存，待续”，不能伪造后台运行。UI/Session断开不改变Work Item owner；新的native Session从最小checkpoint恢复，不全量重播聊天。

验收一个普通本地Work Item时记录`human_interrupt_count`及每次原因作为体验指标即可，不建立新的控制字段。目标不是绝对0次，而是每次打断都对应真实不可替代的人类决策。
