# ServerOS 协作体系合同：能力局部结果怎样成为用户交付

状态：2026-09-23 需求反查后的设计修订，非部署声明。本文件细化 ARCHITECTURE.md §1/3/4/8/10/11/27 的协作行为；不改变原生Agent主脑、唯一serverosd、Work Engine状态权威和生产人执行边界。出处见 [反查记录](WORK-SCENARIO-REVIEW-20260923.md)，各职责的具体接力见 ability-management/design/ABILITY-SCENARIO-CONTRACTS.md。

## 1. 根问题不是缺一套流程图

一个Skill有说明、每个工具能运行、每张表字段有生产者，仍可能没有任何人负责让整个需求满足。原生Agent结束了一轮、生成了报告，或者把缺consumer记成BLOCKED，不会自动完成用户委托。

协作必须闭合六件事：用户要什么结果；谁持有继续推进的责任；子工作需要什么准确材料/授权；谁接收它；依据什么接受；失败/离开/来源改变时谁恢复。这些是现有对象的关系与检查，不建设新的workflow engine、消息总线或“总控Agent平台”。

## 2. 角色和权力分开

| 角色/对象 | 负责什么 | 不负责什么 |
|---|---|---|
| 人类委托者 | 接受业务目标、给有界委托、决定真正争议和人执行效果 | 为每个普通实现选择签字、手工同步所有store |
| Parent/Coordinator 原生Session | 当前目标的分解、进展、交接、结论与继续责任 | 成为永不结束的进程、另存一套Item状态、伪造授权 |
| GPT设计/独立review Session | 研究、方案、找反例、审具体计划 | 凭APPROVE执行或扩大scope，代替实测 |
| CC/Codex/Quay执行者 | 在指定输入/作用域/预算内产生结果与效果证据 | 自己判父目标已完成、递归扩大授权/配额 |
| Testing/领域验收者 | 检查指定目标在指定边界的后置条件 | 修改AC使结果通过、把进程退出当业务成功 |
| Work Engine/Agent Control | 各自持久控制关系、当前授权、原子准入/租约 | 语义思考、从缺失字段猜事实 |
| records/正文权威 | 目标、来源、理解、设计和人读结果 | 活跃进程/授权/实时部署状态 |
| serverosd/OS/Provider | 实际运行观测、机械准入与受管生命周期 | 理解所有飞书消息、替代主Agent计划 |

Parent是当前工作owner而非新软件组件。它可以结束会话，但结束前必须把待续责任连到有真实接收者的checkpoint/既有调度；未绑定自动恢复时只说“已保存，待继续”，不能承诺后台自会接管。

## 3. 以已接受的交付内容判断结束

在已有Item/brief/plan中说明本目标需要的结果，而不是新增一个独立业务状态库。按工作选择：解释/RCA、验证已有能力、代码、安装、真实环境验收、指南更新、报告发布、消息投递中的必要集合；没有需要的维度写明不适用。

例如：LIT-0101已有能力验证成功可以无新代码，但不可推导生产已具备；维护指南需要正确原对象回读；要求发送的日报不仅需要draft；共享Laya集成需要真实consumer，不仅service health。

判断目标完成要同时满足所有必需结果及其接受证据。可选工作未完成不挡核心Outcome；必需工作只有候选/提案/未绑定则不能被“过程执行成功”掩盖。不得事后把没做出的必需交付改成可选让台账全绿。

## 4. 状态不是又一套21个状态机

各Ability设计中的AVAILABLE、AWAITING、PUBLISHED、RUNNING等词是不同对象的用户解释维度，不要求新增一套统一enum或所有能力都按表顺序走。至少区分：能力实现支持、当前readiness、授权、子执行、业务Outcome、观测覆盖、发布/投递。

一个报告可以“内容已验、文档已发布、消息等待静默结束”；不能压成单个DONE或FAIL。Card不拥有grant/worker状态；schema可解析不证明语义正确；message_id不证明用户读过。

## 5. 交接对象：最小材料、明确接收者

在已有Run/Task/文档/效果回执中保留以下关系；同步小函数调用返回值即可，不要求每跳创建文件。

| 关系 | 最小内容/用途 |
|---|---|
| belongs_to | 用户目标、parent尝试/当前owner，知道谁应继续 |
| asked_for | 子目标、验收谓词、必需/可选范围和不做什么 |
| inputs | 准确source版本、accepted plan、代码/制品、环境/观察点、必要证据locator |
| authority | 真实grant/scope/policy引用；模型只读，不能自填作为权威 |
| resources | 独占写入域/profile、child预算与provider配置，不带秘密真值 |
| produces | 产物身份/版本、观测/效果receipt、未完成范围/疑问 |
| accepted_by | 接收者及其验证结果，所接受的确切输入输出版本 |
| continuation | 下一步owner、唤醒条件/到期策略、checkpoint和实际scheduler引用（若有） |

发送资料只是offered；接收者确认版本、权限、能力和验收边界后才accepted。accepted表示接了任务，不表示结果已验。producer送达文件后不能自动推进父阶段；parent根据真实结果接受/要求具体修订/登记准确缺口。

同一个scope中只有一个父继续owner；handoff后旧owner不能再发新写入。接收方不存在或拒绝时父方保留责任并选择其他合法路径，不把状态散落给无人消费的文件。

## 6. 输入与理解：两条水位，不是全库等待

B1先把所取范围的合规raw/event与manifest安全提交，再推进该source capture watermark。B2读取固定版本材料，形成有出处的语义patch，检查后提交Claim/相关视图，再推进understanding watermark。理解失败不会倒退已持久raw或强迫重新获取全部历史。

source批次可以分割为已处理区间和精确gap，不以一个最大offset掩盖中间丢页。编辑/撤回有revision或有界重访；源API无法保证的历史改动明确未覆盖。

B2的机器事实/有依据推论可在已有委托内成为agent_reconciled；重大目标、责任、期限、对外承诺/权限变化才需user_confirmed。完整span被人确认读过，不等于其中每句话已经业务接受，更不等于grant。

source更新令其依赖Claim/review进入待重核；在无法细分影响前保守阻止相关效果，而非停止无关项目。日循环使用冻结的本轮cutoff；新到数据进入下一批或确切当前受影响目标，不每来一条消息就推翻全部当日计划。

## 7. 工作准入：组装权威，不复制动态事实

B4从当前Work Engine取Item、focus、依赖和lease；从Card/正式record取目标/AC；从Agent Control取有效scope/budget/revocation；从执行器取实际worker/effect；从Registry/Binding取能力和环境。组装后在真正的准入边界再次校验，不持久化另一份可独立修改的grant/worker副本。

从未启动的新候选：检查没有冲突lease/dispatch intent即可，不要求虚构worker_stopped=true。已有取消/未知效果必须查询对应真实attempt再释放。每个profile最多两项root焦点，child research/test可有自己的子预算，不偷增root名额。

排序有依据但不强制先有P0字段；合法委托可授权Coordinator依据业务价值、已知deadline、解除依赖、证据/环境就绪度做选择，保留理由与备选。不使用Laya分数决定权限。无工作就诚实不选，不为夜间产物凑两项。

紧急事故按既有incident授权处理，不从研究例行委托推导生产变更。若需调整焦点，先明确停放/停止旧执行与资源，不能靠越过2-slot或假完成抢占。

## 8. 授权、预算与可执行的自主边界

授权按实际effect而不是词“网络/外联/准备”判断：本地开发写入、管理的开发服务安装、公开检索/模型API计费、私人资料外发、客户发送、生产变更、ACL/云删除分别核对。Jev inference不是客户邮件，但依然需要数据与费用许可；shared gateway/LiteLLM生产人执行边界不变。

总预算owner在现有父控制对象预留，再分给child/provider尝试；子任务、重试、浏览器subrequest和shadow评估消耗总预算，不能每个worker重新拿满额。共享上游key的账号级限额若没有统一原子owner只能声明本任务预算，不夸称控制其他应用消费。

明文配置中的allow和Scope描述不是可信授权；实例缺授权就给精确一次性配置请求，不把本设计写成永久许可。已获准的常规调查/编码/测试设置/兼容迁移自主，缺consumer/语法错误/正常跨仓合并不是要用户重新设计。

## 9. 长工作、阻塞与继续责任

等待必须有依赖对象/当前owner、等待什么证据、检查或事件条件、到期处理和能继续的其他部分。WAITING_ON_ACTIVE_WIP要指向具体commit/interface/作者交接；不能把“有人在开发”设成整个repo永久禁区。

报告“仍运行中”需要真实worker/Task状态；重写ledger不是业务进展。每次续跑从checkpoint、真实lease、上次效果查起，避免auto-compact后从旧Prompt盲重放。

时间窗到达只是可尝试，不等于有scheduler已登记。静默窗口中可以完成draft、publisher准备和待投递intent；实际投递必须由有receiver/授权/去重的既有sender消费，不能口头许诺稍后发。

## 10. 失败分型与安全停止

至少区分：前置缺失、采集/环境失败、执行器失败、产品AC失败、研究未复现/无结论、效果未知、可选对照失败。类型要证据支持，不能为了继续给产品bug改名。

研究/复现目标的预期反例失败可能是任务成功：已稳定复现缺陷且证据足够。验证“是否已修好”若未复现，只能说该路径没复现，不能全局“已修复”。真实用户验收中的非预期FAIL按当前规则收证据并暂停该产品修改，其他独立工作继续；没有新修复授权不能借review/诊断命名继续改产品。

取消请求、进程退出、外部效果完成、资源释放各自有事实。未知效果先核对对应provider/target；取消child不撤销已发生的云写入/费用。补偿也需合法权限，不自动把失败撤回解释成允许删外部对象。

## 11. 审阅与验证不靠自我证明

不同GPT Session是隔离条件，不是统计独立或正确性的证明。review必须给可定位的finding、原始输入与限制；主执行者核对事实，不能仅因评审模型说错就改正确代码。缺少反例不以“更谨慎”无限循环加范围。

同输入/同finding未新增证据，不重复模型调用刷APPROVE；有界修订次数和预算由实例策略给定。研究、实施计划和代码验收不同，不每次微小文本变更都重做全部。

版本变化按依赖映射失效：改相关源码→相关测试；改source规则→依赖计划；改receiver/ACL→发布/投递重检。无法可靠定位影响时在该作用域保守重验，不全系统无限重跑。跨consumer验证必须使用各自真实序列化/API，不让同一个fixture工厂同时定义正确输入和正确答案。

## 12. 文档、知识与通知是三个可恢复效果

B8提供已核内容；B9确认原对象/受众/章节并提交；B10实际投递。publisher失败不要求重写正文，sender静默不阻止知识更新；但用户要求“发给某人”时sender receipt未完成则不能关闭该交付。

Follow同时可维护同一doc；publish自身revision去重，不吞后续人工修改。源SOP更新先生成规则diff，不能自动改原文或业务授权。原始附件默认locator，必要材料才按用途物化/落位；禁止为完整交接全量复制raw/secret给child。

指南与员工snapshot之间存在消费关系：新指南改了不自动证明Bot用的是新版；有接受版本和实际消费者测试后才宣布知识更新生效。无人/机器人自动答复不是人已解决Case的证明。

## 13. 可观测性围绕有意义进展

三层分别呈现：Core/Provider健康；工作推进/等待；领域结果/质量。serverosd心跳通过不能证明认识更新，Quay worker活着不能证明研究有产出，Laya ready不能证明答案可靠。

每条跨能力关系能定位最近有效产物、accepted输入、待接收consumer和需要的下一动作；已运行但长期没有有效进展才按具体预算/预期提警。没有工作、合法on-demand停止、历史文档到期复核不是一类事故。

复用现有日志/receipt/Board做追踪；OpenTelemetry等仅在项目已有合适体系或确有需要时采用，不建设新遥测平台。纯卡片模型输出症状/影响/证据，只有因果证据才能合并根故障。Core死时采用既有独立健康/发送设计。

## 14. 实施与验收

现有落点：intake/understanding_delta和understanding_patch是差量/提案基础；context/delegation与Agent Control是授权基础；intake/focus_source是board适配而非完整准入；Codex/Quay与原生runner是真执行端；publisher/sender各有receipt。缺失的adapter/writer只在相应owner补，不让Skill自己接管store。

各边最少验证：真实输入能进入、正确结果被下一consumer接受、输入变化/权限撤销拒绝旧交付、中断恢复不重复效果、一个业务失败不使无关工作永久等待。具体旅程见[SCENARIO-ACCEPTANCE](SCENARIO-ACCEPTANCE.md)，现有P0–P12仍是唯一实施计划，细化接口见[COLLABORATION-IMPLEMENTATION](COLLABORATION-IMPLEMENTATION.md)。

本轮只修改设计，不创建Run/授权、执行任务或改任何runtime enum。普通Session只加载当前所需能力和这些关键边界，不把全部文档变成新的巨大bootstrap。

## 15. Completion、接续与最小持久化

Completion Boundary写在既有Requirement/brief的required结果中，不另建完成条件服务。Parent以当前接受版本和适用证据检查剩余义务，不按Activity计数/Run exit/child DONE/PR状态关闭。完成深度和严谨度独立；部分结果不能抵消缺失必需结果，历史完成事实也不因后来运维故障被改写。

持续责任绑定逻辑Parent，不依赖同一transcript永生。checkpoint与真实继续receiver/trigger配对才构成自动接续；timer、消息送达和文件存在分别不够。短同步调用用返回值即可；长执行、显著成本、独立worker或外部效果才要求对应durable关系。Operation Attempt概念不意味着每个tool call新增表/Run。

实际effects结合principal/target/action/data/受众/预算/可逆性判断，不能用单一E0–E4或“本地/远程”自动决定权限。可信控制、Provider和实际OS边界各自承担已证明的强制范围；Skill只指导，B10只呈现决定。未调解的effect unknown只围住受影响scope，不锁死所有工作。

当前Focus完成可归零，不自动补位。Waiting仍有明确继续责任；park/取消/替代须协调实际writer、effect、资源和可恢复状态。不是所有后台routine都需要Work Item；长期采集/监控属于standing操作，具体建设/修复才有可完成的工作目标。
