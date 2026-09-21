# Daily Work Cycle · 每日低干预工作闭环

Status: product profile and implementation contract, 2026-09-22. 产品架构仍以 ARCHITECTURE.md 为准。不是新的 workflowd、任务数据库，也不把该顺序强加给所有临时工作。

## 1. 用户目标与本轮取舍

每天自动更新结构化认识与工作对账；晚间从现有需求看板选择两项值得推进的目标，GPT 深度参与调研设计、独立审阅实施计划，次日由原生 Agent / Quay 执行、验收与汇报。正常情况不等人逐步点确认；权限、重大业务取舍、生产变更和实质失败才升级。

“两项”是本 profile 的并行焦点上限与期望，不是每天必须新造两项：未完成/委派/等待/取消未确认/效果未知的已有焦点仍占位。不足两项符合准入时少做，并解释缺口。已结束或被取消的旧需求不自动复活。

“每天 Quay 在跑”意味着优先给 Quay 有价值、可验证的切片，不是保持进程忙碌或造任务凑日报。Quay 的任务数、进程数与父需求完成情况分开。

## 2. 每日动作与责任

| 时机 | 动作 | 所有者与输出 |
|---|---|---|
| 当日工作后 / 晚间 | 获取已登记来源增量 | Information Intake；source revision、分页覆盖、游标和必要原文 locator |
| 同轮 | 更新认识并对账 | 理解事实/推论/冲突、需求处置、Git/PR/Run/实际环境，不复制全量数据库 |
| 同轮 | 选取最多两项次日焦点 | 原生 Coordinator 根据已授权排序策略提案；Work Engine 原子准入、记录选择来源 |
| 晚间 | GPT 参与研究与设计 | 原生 GPT reasoning Provider；独立 source/design 版本与证据 |
| 设计足够明确后 | 形成实施计划并由独立 GPT Session 审阅 | 审阅绑定 scope、source、design、plan、代码 baseline；拒绝/未完成不能放行 |
| 次日 / 条件满足时 | 原生 Agent 或 Quay 执行有界工作 | 已存在执行器和 Work Engine Run/lease；不把一个状态字当执行许可 |
| 有意义的验收边界 | 测试并给出证据 | Testing；需求 E2E / 范围 E2E、后端断言、多张必要截图 |
| 结束或受阻 | 报告、发布及反馈回流 | Reporting / Document / Human Control 各自回执；结果回到看板与认识 |

晚间/次日是用户产品节奏，不是不可变时钟。具体时间、时区 Asia/Singapore、资源和费用预算是实例配置；代码不写死业务时刻。本轮没有启用全业务每日执行定时器。

研究/实验可往返，研究型或 no-code 目标不强制写代码；代码计划改变必须重新审阅受影响部分。分阶段回执是证据与准入条件，不增加另一套业务 Item 状态机。

## 3. 一次配置，日常少确认

授权分两层：

1. 站立委托规定可参与项目、来源、可自动选择的候选范围、允许读写/网络/发布目标、风险上限、运行预算、有效期和撤销途径。
2. 每日选择通过真实控制面将它落实成具体 Item/动作/scope digest 的有界授权，并占用现有工作租约；模型不能自己填写一个 allow 字段就完成授权。

用户本轮表达的是自动选择/研究/审阅/实施的产品方向，不隐式授权未知生产操作、管理员知识外发、所有共享仓库 push 或无限模型费用。已确认的项目边界继续有效。首次配置后正常日子不再反复问“要不要研究、要不要写计划”。

Coordinator 可自动记录可核对事实与已授权范围内的判断。`agent_reconciled` 不等于 `user_confirmed`；用户未到场时对账机会照常产生，重大争议保留，其他已授权工作继续。不能用每日人类签到作为全部工作的启动条件。

## 4. 认识不是每日重写一遍长摘要

按来源 stable ID / revision 处理增量、更正、撤回和失效；提交理解变更后才推进相应采集游标。只更新被新证据影响的 people/project/topic/responsibility/decision 记录。

每条重要结论能回到来源、时间、适用版本与证据强度；missing 与 zero、推断与观察、团队基线与生产事实保持分开。冲突不会靠最后写入者获胜，也不为自动化而吞掉原失败。

每次执行前使用同一已核对的 knowledge/source digest 加该任务最小 Context Capsule。当前上下文与实际可写 checkout 对齐；新的关键来源变化撤销受影响计划的准入，不撤销全世界所有计划。

原生 Memory、records/checkpoint 与 MetaCC 可先满足连续性；OpenViking 是可选检索 Provider，不是每天工作的启动依赖。失效来源在调用方 recall/filter 层即排除，不能等远端索引恢复才阻止旧结论召回。

## 5. 自动选两项，不把规划变成无限积压

先处理本 profile 已选焦点的继续/结束/明确停放，再填空位。等待、delegated、cancelling、效果未知不能被当成“已经空闲”。明确停放是焦点选择的 disposition，不凭空增加 Work Engine 的 Item enum；必须已确认执行者停止、资源释放并留下恢复点。

按用户/授权来源既有优先级、真实依赖、可验收范围与环境准备度排序。重要业务优先级不存在时先说明，不用模型热度猜 P0。全局 Work Engine 仍检查与其他工作流的冲突，不接管别人的 Quay 队列。

选择是可撤销的计划，不是两项已经开始执行；执行仍要在权威准入锁内检查最新授权、撤销、源/计划基线、环境和资源。过期看板不能产生新的可信选择。

## 6. GPT 高强度参与的两条真实路径

### 原生自动路径

使用已授权的 Codex 非交互接口作为 GPT reasoning Provider。为设计与计划审阅明确指定已验证可用模型与 high/xhigh 推理配置，不继承当前用户可能为低延迟编码设置的 low 配置。

设计轮与独立计划审阅使用不同 Session，提供问题、来源、候选方案与验收，后者检查遗漏、因果证据、迁移、权限、资源、测试和恢复。独立 Session 是隔离条件，不自动证明审阅质量，关键结果仍需证据核查。

可信调用适配器记录：真实进程状态、完整 completion 事件、最终结构化正文、requested model/effort、Session、输入 digest、来源/计划 baseline、时间与未决意见。模型返回的“APPROVE”本身不具备身份、授权或签名效力。

本轮实测出现过 exit 0 但没有 turn.completed/最终正文，所以必须判为 INCOMPLETE。原生 CLI 成功、模型审阅通过和工作执行准入是三个不同判断。代码与测试已加入该反例。

### 人类参与 ChatGPT 路径

你在 ChatGPT 对话中的深度讨论仍可通过 GitHub work packet 回写。这是可选的更强参与路径，不冒充已自动控制网页/桌面 ChatGPT。原生自动路径不依赖你每晚把一份设计粘贴回来。

## 7. 设计审阅与实施准入

审阅接受的对象是一个固定组合：Item、scope、source、design、plan、代码/config baseline 与所需执行 Binding。审阅结束到实际实施前任何重要部分变化，重新审受影响版本。

CHANGES_REQUIRED 可以在既有授权/预算内回到设计修订再审；保留原意见和修改依据。模型/认证/输出不完整、费用或次数预算耗尽时停止该分支。不得降成未经批准的低推理模型，也不得将空答复当默认批准。

原生执行在 Work Engine Run/lease 与真实 OS/Provider 权限边界下进行。新 pure policy helper 的 ELIGIBLE_FOR_ADMISSION 不等于执行权限；可信授权/receipt 由调用者注入，不能接受网页或模型自行声明的 allow。

## 8. Quay 与测试的实际接法

复用当前安装的 Quay 插件 CLI/原生 Task/acceptance/gate-events。源码目录、插件版本、CLI 版本、当前运行者分别记录。对一个有界任务，创建任务、配置真实验收命令、执行 gate/complete、回读证据，不能只创建 Task 然后报“Quay 已参与”。

当前默认 Quay worker 屏蔽 Playwright MCP，保留这一隔离，不偷偷开放全部 worker。非浏览器实现/验证可交 Quay；浏览器测试由具备授权环境的独立 Tokyo Testing Provider 执行，Quay/父目标引用其结果。需要独立 browser worker 时专门验证身份、profile、权限、并发与清理。

本轮已证明 Quay 能调用原生验收命令运行单元测试，也能调用独立真实浏览器 context 的验证任务并保留截图。该 smoke 不证明用户登录态/可见窗口 E2E；不能将它替代已约定的真实用户验收。

测试失败在已选的安全粗粒度边界保存证据并呈现，停止自动产品修改；不能为了低人工介入而隐藏失败、改 acceptance 或无限重试。设计审阅修订和产品测试失败的停点不同。

## 9. 六处接缝的默认处理

| 接缝 | 采用选择 | 不能冒充的完成 |
|---|---|---|
| Quay / 浏览器 | 现有插件 + 明确任务 + 独立 Testing Provider | MCP 进程存在不等于真实任务；synthetic browser smoke 不等于带用户登录态 E2E |
| Windows handoff | 已授权远端 Provider + checkpoint/hash + 目标根解析 + 真实写入权交接 | 传文件或远程命令通过不等于已移交业务租约；SSH 不可用不绕企业控制 |
| OpenViking | 采用官方独立 SDK/HTTP 服务，配置 endpoint 与限定身份；当前先用 records/checkpoint | fixture/SDK 可导入不等于真实检索服务在线 |
| 云文档并发 | 写前 fetch 当前 revision/内容；精确局部写、回读；共享正文需已验证条件写 | 存在 revision 参数不等于 CAS；无保护时不能自动覆盖他人正文 |
| 生产切流 | 真实 Runbook/traffic authority 与目标 Binding，人工执行 | 通用蓝绿设计不等于已知道当前线上 nginx/脚本/命令 |
| Support Bot | support 受审知识 → 员工快照 → bot 只读消费；无解交 owner 队列 | 生成员工包不等于 Bot 已接消息；未收到投递回执不能说已转人工 |

六者不是所有工作共同的启用门：无 Windows/生产依赖的 Tokyo 工作继续；OpenViking 不可用仍可对账；共享文档条件写未验证时生成可审核版本/新报告，不自动覆盖旧正文。缺口必须明确影响哪些具体动作，不能以一个总 READY 掩盖差异。

## 10. Support Bot 的知识入口与兜底选择

知识维护归 litellm-support，而不是 Bot 代码仓或 ServerOS。消费的具体快照必须带 source revision、entry hashes、audience 与经过实质审核的适用状态。现有导出器可复用，但标签 verified 与正文警告冲突的条目要先核对；不得仅凭目录白名单/字符扫描宣称完全安全。

Bot 默认只消费员工可用的快照，不挂 raw、operator、candidate 或来源全文作为可穿透链接。ServerOS 旧 support matcher 与该快照格式不是同一接口，需真实消费测试，不能换路径就宣称适配完成。

兜底默认交绑定的个人工作 owner，而非擅自指定同事或 SLA：无匹配、证据冲突、权限不够、用户要求转人工或预算耗尽时形成小决策包（原消息引用、已查事实、尝试、疑问、建议）。先入已有工作/通知队列并得到回执，再按许可给 owner 提醒；外发收件人/机器人未绑定时显示待投递，不能告诉求助者“已通知”。

回包后按原 case/thread 回写事实与结果，同一问题幂等归并；Bot 的答复不能自动变成 verified 知识。服务接入、实际接收/回调和 owner 收件仍需端到端验证。

## 11. 可靠性与通知

只保留一个该 profile 的触发所有者，沿用 OS 定时器或已授权 Scheduler 启动有限 worker。日循环不在 serverosd tick 里调用模型，不建立第二套 Quay/work 状态库。

同一轮/阶段动作的 idempotency key 绑定输入版本；STARTED 后崩溃/超时先查实际效果，不能盲目再发。共享动作回执写入既有 namespace；本轮 bounded invocation 只提供进程证据，不证明业务结果，也不证明所有子进程已经消失。

日志大小/等待时间有明确边界，不对整个 native 进程施加可能破坏其缓存写入的全局文件大小限制。真实 worker 的进程树/cgroup、安全沙箱和累计预算由受管执行端承担；helper 不夸称具备这些能力。

通知默认一份日对账/成果摘要，加实质异常；一项等待不阻塞其他已授权焦点。报告列选了什么、做了什么、哪些证据支持、Quay 的真实贡献、未完成/未投递/待你决定什么，不用工具调用数充当进展。

## 12. 当前实现与启用条件

已有本轮代码：intake/daily_cycle.py（选择/当前授权与审阅绑定/来源失效），runtime/review_receipt.py（完整原生审阅证据），runtime/bounded_invocation.py（有限调用与幂等证据）。这些都是现有系统的库函数，不是已接完的无人值守总执行器。

在任何“每日自动实现已启用”声明前，必须验证完整一路：真实增量 → 认识提交/游标 → 真实 board/current grants → 原子选择/资源准入 → 设计/计划审阅 → 自有工作树实施 → 真实验收 → 对账和发布回执。失效/重启/双触发与取消反例也要覆盖。

本轮运行范围与未覆盖项见 [现场接缝验证](integration-readiness-20260922.md)。该报告不替代未来某次 Run 的准入。不得仅安装本 profile 或测试 helper 就声称无人值守业务闭环已经运行。
