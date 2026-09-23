# Daily Work Cycle · 有真实接收者的每日工作

状态：2026-09-23 协作修订，产品规范仍为ARCHITECTURE.md。此Profile组合现有能力，不新增workflowd、第二任务数据库或固定业务阶段机。通用交接/预算/恢复依 [COLLABORATION-CONTRACT](COLLABORATION-CONTRACT.md)，每职责详见ability-management/design/ABILITY-SCENARIO-CONTRACTS.md。

## 1. 已接受的目标

每天增量接收真实工作信息、修正结构化认识并对账；晚间形成最多两个合格工作焦点，GPT深度参与研究设计、独立Session审实施计划；原生CC/Codex/Quay实际推进；用真实验收、报告、获准维护与通知交付。正常授权范围内少找人，重大目标/权限/生产效果与真实非预期产品失败才升级。

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

对本profile已经选中的root先继续/结束/合法停放，再填空位。运行/委派/等待/取消未确认/unknown effect占位；历史record-only不自动占位。明确停放需checkpoint与旧writer/资源确认，不新建Item enum。

准入从Work Engine、Agent Control、实际executor、Registry/Binding和接受需求组装；grant/scope/worker事实不塞Card当独立权威。新候选从没开始过时检查无冲突lease/intent，不要求虚构停止回执。

选择按已有业务价值/优先级、deadline、依赖、可验收和环境就绪，有来源解释；没有priority字段不默认P0，也不永久拒绝所有候选。若授权允许Coordinator排序，记录其建议与接受范围。实际effects前再次原子核scope/revocation/基线/资源/预算。

## 6. GPT设计、独立审阅和研究往返

采用已有CodexAppServerProvider或已验证exec适配；明确模型/推理配置、允许工具、预算/deadline。设计与plan review不同真实Session，提供问题、原始依据、候选方案/反例与具体当前代码，不只给作者摘要。

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

完整LIVE前至少跑通SCENARIO-ACCEPTANCE的J06：真实源变化→理解提交→current board与有效委托→设计/独立review→实际执行→验收→必需交付/回流，并完成duplicate、source漂移、权限撤销、cancel未知和partial发布恢复反例。其他独立功能可分别启用，但不能汇总成全链已完成。

## 11. 历史实测和当前缺口的位置

2026-09-22原生GPT、Quay gate、合成browser、Windows checkpoint等历史实测在integration-readiness-20260922.md；它们不是当前LIVE/业务完整证明。此次源码反查与风险见WORK-SCENARIO-REVIEW-20260923.md。

执行仍用CC-EXECUTION-PLAN.md的P0–P12；COLLABORATION-IMPLEMENTATION只给本轮接口/失败定位和依赖细化，不另开一套完成度表或工作平台。


## 12. 显式定时节点与Ability owner

Daily Cycle不是单一Ability，也不是serverosd内部循环。时间只唤醒有限routine，前置receipt/state决定能否继续。推荐当前个人实例候选：

| 节点 | 推荐触发 | owner |
|---|---|---|
| 日间轻量增量 | 每30–60分钟 | A4 scheduler → B1 |
| Evening cutoff | 20:00 local候选 | A4 |
| Archive catch-up | cutoff后 | B1 |
| Structured Understanding | B1 receipt后 | B2 + A2 |
| Daily Reconciliation | B2 receipt后 | B10 |
| Requirement/Board reconcile | 人回复或standing policy允许 | B3 + B4 |
| Next-day Focus <=2 | reconcile后/约21:00候选 | B4 |
| Session Prep | Focus成立后 | A1 |
| Workspace Prep | Focus成立后 | A3 |
| Runtime Prep | Session/Workspace后 | A4 |
| Morning Resume | 08:30 local候选 | A1 + A3 + A4 + B4 |
| Session/Workspace maintenance | 低频夜间 | A1.3 / A3 |

具体时间/时区/预算是instance policy；用户只说“8点”时要结合“归档今天/选明天”语境解析早晚，必要时只问这一处配置。

A1 Session Prep必须显式包含：Context Resolver、agent-session-management实时控制、session-manager历史archive/restore。A3 Workspace Prep必须显式确认repo/base/worktree/dirty owner/remote freshness/write scope。完整职责见 [Ability Operating Model](ABILITY-OPERATING-MODEL.md)。

## 13. serverosd与scheduler的边界

`serverosd`提供长期Node Core事实/控制面：runtime registry、IPC、Agent Control持久机械状态、lease/effect协调、低成本readiness、有限reconcile和自身健康。它不主动抓飞书、做B2语义推理、选业务Focus、管理Session archive、创建worktree或运行长驻workflow。

OS/授权scheduler负责wake finite routine；routine调用对应Ability实现，完成/partial/wait后写receipt并退出。一个logical routine只有一个trigger owner，错过周期按cycle_ref幂等恢复，不重复开批。

## 14. Ability自迭代进入治理而非业务流水线

G1.5 Ability Evaluation & Evolution消费Journey/A7、用户纠正、运行receipt和MetaCC session evidence，比较“Ability理想职责”与“真实完成效果”。只有具体gap才产生候选优化，并在隔离eval/held-out/fresh consumer后走G1正常升级。它不每天盲改Skill，也不自改权限/安全/业务职责。
