# CARDS-STYLE · ServerOS 的可读产品表面

2026-09-23 根据用户实际飞书截图修订。R1–R9编号保留方便旧引用迁移，但本版纠正“首行可辨就算摘要”“所有卡统一免责声明”“不许出现任何因果词就等于准确”等过窄判据。现有代码/测试不会因文档更新自动符合；实现按docs/MONITORING-ACCEPTANCE.md逐项迁移。

本文件只定义表达与层级。阈值/注意力/状态语义仍由ServerOS拥有的contract/model产生，renderer不重新判断，ops sender不另造中文事实。

## 默认语气

专业、简洁、可操作，不是原始台账，也不是解释自己如何工作的聊天。正常卡让人看到资源和服务；告警让人知道症状、影响、何时开始与下一步。未知如实，但不把免责声明占满页面。

## 规则

**R1 · 先说当前结果/症状，不以对象存在充数。**

“内存6.8GiB”“数据盘剩余32GiB”“Tailscale入口未连通”有判断价值；“账户uid1002”“CPU型号…”只标识对象。后者放详情，不再作为唯一可见摘要。

**R2 · 不重复标题、不让卡片自述占首屏。**

状态卡不出现“只读告警”；alert如确需说明动作状态只写一处“未自动操作”。删除“三项均已读到”与重复“观测三项已读到”，保留具体缺口而不是两次描述renderer。声明与个人container0分开只需在对象名称/详情能正确消歧，不每次三处解释。

**R3 · 名称服务于人，精确身份保留在详情。**

人面优先“监控核心(serverosd)”“Tailscale”“CloudCLI内网入口”“MarketOps研究隧道”；同名按项目/用途消歧。PID/UID/code/token/原始ref不是标题。名称不能替代鉴权、归属或精确target。

**R4 · 数值有单位、窗口和分母。**

本人CPU用核数与真实配额/整机口径，内存用GiB，磁盘同时给比例与余量。失败数/请求数同窗口，不以成功日志计算全量错误率；未知不0。finding计“项”、subject计“个”，但不把时间/请求/文件都机械改同一量词。

**R5 · 建议指向下一件可做的事，不等于执行许可。**

“检查Tailscale登录状态与入口映射”优于“查看登记表”。需要有风险动作时呈现可审核指引/人执行边界，不能因文案提及重启就执行，也不因一律禁操作词而使建议永远含糊。只读卡不提供暗含mutation的按钮。

**R6 · 不能发明原因，但允许有证据的解释。**

进程failed不能写成网络故障；不同现象同时出现不自动连因果。已有诊断/依赖证据时可以写“已确认的原因/相关依赖”，附evidence_ref；待核对解释明确标假设。词表禁“因为/导致”不能替代证据审查，也不能阻止正确因果解释。

**R7 · “没查”与“查过未发现影响”不同。**

默认缺影响证据可简写“影响待确认”或具体“尚未验证远端访问”。只有相应检查支持才写“暂未发现明确影响”。“本地任务仍在运行”也需要独立证据，不能从监控器自身出错推断业务一切正常或全部失败。

**R8 · 当前重要信息默认可见，历史账本只在详情。**

正常资源卡不出现数百项历史积压/恢复清单。告警有超过cap的当前问题时可一行“另有N项当前问题”并下钻；不能用历史数量代替紧急程度。无需要通知的新变化不额外发空告警。主动诊断视图可显示完整审计计数，和自动推送分开。

**R9 · 告警对象/症状/影响/建议有明确层级，不强制五行模板。**

例如“监控核心采样停更3分钟”作标题，正文交代OS进程仍在、哪些状态不能确认、建议检查何处。没有额外信息的重复状态/首次观测行可合并；关键证据和首次/最近观察在详情完整保留。

**R10 · 状态卡不再用lines[0]决定摘要。**

模型显式提供summary_metrics、service summary、当前issues和details或等价结构；renderer不能把身份第一行当资源结论。三个资源区CPU/RAM/存储与关键服务在默认折叠态可判断；正常明细折叠，异常自动上提。

**R11 · 可见性必须在真实客户端验证。**

390px目标、360px回归、大屏、长名称、空值/大值/多异常、折叠初态均测；HTML预览不是Feishu native渲染证明。不能靠缩字号、空格对齐、HTML专属CSS或删重要信息让卡面“更短”。图标/色彩伴文字，不只有红绿。

**R12 · 链接与时间不得制造错觉。**

127.0.0.1不是手机服务入口；declared/probed/reachable_from分开。主时间显示观测窗口/年龄，render或send时间不能刷新旧事实；时钟异常不改成“刚刚”。失效历史CLOSURE不作为当前完整状态入口。

**R13 · 样例与真实结果严格区分。**

合成样例标题和正文保留“示例/非当前采集”，截图裁剪也不冒充现场。截图去敏感信息；部署改变用实际revision/consumer验收，不以文档写成新样式证明已上线。

## 验收不再只检查漂亮的字符串

需要四层：具有真实口径的source fixture；producer→model字段与证据；默认折叠的JSON/HTML可读性；授权测试接收端的native截图/回读。同一输入版本贯穿，任何一层失败不能由另一层PASS顶替。

旧test中要求所有卡出现同一个FOOTNOTE_READ_ONLY、首行恰为UID/型号、总禁因果词、历史计数必露出的规则若存在，必须据本次用户反馈明确迁移并保留安全反例，不为让旧测试绿继续保留错误体验。

详细页面与语义：[BROADCAST-ALERT-DESIGN](docs/BROADCAST-ALERT-DESIGN.md)。运行/自身监测：[NODE-OBSERVABILITY](docs/NODE-OBSERVABILITY.md)。具体迁移/验收：[MONITORING-ACCEPTANCE](docs/MONITORING-ACCEPTANCE.md)。本轮只有设计修订，没有执行截图/服务故障测试或修改运行卡片。


**R14 · Current Card 与 Incident Card 都是稳定对象，不靠刷屏表达时间。**

当前状态用一张rolling interactive card原位更新；可按日轮换，但每小时采样不生成24条聊天历史。一个incident episode对应一张card，持续时间/证据/恢复通过patch更新。只有NEW、ESCALATED、REOPENED或实质影响变化才重新打断用户。完整历史属于records/diagnostic，不属于聊天消息数量。

**R15 · 资源首屏固定回答“我 / 设备 / 基础设施 / 当前问题”。**

本人CPU/RAM/quota、整机CPU/RAM、系统盘、数据盘与少量关键基础设施默认直接可见。UID、型号、loopback、PID、container list、probe URL和采集口径进入详情。采集成功不能改写成环境健康。


**R16 · Session 名称必须来自正确身份层。**

loginctl/cgroup 的 `session-15413.scope` 只能称OS登录会话/用户会话；tmux session只能称tmux；BotMux session只能称BotMux；只有A1 AgentSessionProvider证明的provider/native identity才能显示“Claude会话”“Codex会话”。禁止从ref prefix或进程名猜Agent类型。

**R17 · Daily Status 一天一张；Incident 一个episode一张。**

状态采样更新同一张当日卡，不以聊天条数保存小时历史。Incident跨小时/跨天patch同一episode card；平台patch窗口到期才创建successor。页面默认不显示seen count、internal code、raw refs和历史积压。


**R18 · 共享机个人卡不得把别人WIP变成我的动作。**

面向某 principal 的卡片只把本人 owned 对象写成可处理对象。其它用户对象只有形成 device/shared-service 影响时才以聚合事实出现；默认不点名 PID/Session/Work Item，更不写“请确认是否清理”。

**R19 · Subject 粒度必须对应 action boundary。**

login session、Agent Session、Work Item、Run、runtime instance 不能混用。若真正异常是某个已结束 Run 遗留的测试 daemon，标题写该 Run/runtime；不能把父 login scope 整体命名为“Claude 会话遗留”。

**R20 · 卡片生命周期固定。**

Daily Status 每个本地日一张并持续 patch；Incident 每个 episode 一张并持续 patch。采样次数、seen count、小时历史不作为新消息理由。
