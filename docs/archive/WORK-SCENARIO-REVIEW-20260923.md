# 从实际工作反查 Ability 与协作设计

2026-09-23，只读需求/源码审查 + GitHub文档修订。没有现场测试、模型计费调用、云文档操作、服务/目录写入。本文是设计依据与已发现差距，不是运行状态盘点。

## 1. 本轮实际读取的材料

| Ref | 来源 | 能证明什么/不能证明什么 |
|---|---|---|
| R1 | [LIT-0101 brief](https://github.com/ProfessorZhi/hwz-records/blob/2e6120079b0bacefeebae644b1b1df68a36dfe57/projects/litellm/items/LIT-0101/brief.md) | 明确既有能力验证/no-code交付，团队基线与生产、UI静态与浏览器验证分开；不证明今日生产现状 |
| R2 | [LIT-0104 brief](https://github.com/ProfessorZhi/hwz-records/blob/2e6120079b0bacefeebae644b1b1df68a36dfe57/projects/litellm/items/LIT-0104/brief.md) | 真实诉求为缓存计费/额度因果链，非简单提高命中率；key/请求/窗口、失败与重试不应混淆 |
| R3 | [归档会话](https://github.com/ProfessorZhi/hwz-records/blob/2e6120079b0bacefeebae644b1b1df68a36dfe57/information/raw/feishu/chats/people/zhangzhengji/oc_ddc206bb6093ec959b51752cc7a59533/messages.jsonl)，读取文件行710–723、message_position709–722 | 09-18用户新版指南/截图、旧版隐藏、指定知识库、面向海外读者配置；不证明当前ACL或链接可达性 |
| R4 | [Bruce SOP](https://github.com/ProfessorZhi/marketops/blob/d4f5ddf20e63a5a91832e695fdf40836a5c08e82/sources/bruce-vince-zhengji/sop/uk-natural-grass-paintmaster-sop-v1.md) | responsibility-based Account、literal email、A/B/C/D/X、研究不等于发送；SOP原暂停不由发布自动解除 |
| R5 | [MarketOps架构](https://github.com/ProfessorZhi/marketops/blob/d4f5ddf20e63a5a91832e695fdf40836a5c08e82/docs/ARCHITECTURE.md)及[当前Laya adapter](https://github.com/ProfessorZhi/marketops/blob/d4f5ddf20e63a5a91832e695fdf40836a5c08e82/marketops/adapters/laya.py) | 架构已要求单host共享服务，但已读adapter仍显式in-process english Router；不证明部署选择一定使用该adapter或服务当前停机 |
| R6 | [understanding_patch.py](https://github.com/ProfessorZhi/serveros/blob/5c0b9fc38a4f81401638dc1db051730f4e91e327/intake/understanding_patch.py) | 只产重读提案、没有正文理解；item_key未包含content hash；静态风险不称已复现 |
| R7 | [focus_source.py](https://github.com/ProfessorZhi/serveros/blob/5c0b9fc38a4f81401638dc1db051730f4e91e327/intake/focus_source.py) | 对缺grant/scope/worker/resource字段的解释仍指向Card；多权威事实尚需真正join |
| R8 | [daily-work-cycle.md输入基线](https://github.com/ProfessorZhi/serveros/blob/5c0b9fc38a4f81401638dc1db051730f4e91e327/docs/daily-work-cycle.md) | §4仍写理解后推进采集游标，与后续两水位合同冲突；本次直接修该源文件 |
| R9 | [先前完整度表](https://github.com/ProfessorZhi/ability-management/blob/9bfb61e84fa72766328d5b88fd239a836fb77484/design/ABILITY-PRODUCT-COMPLETENESS-20260923.md) | U1–U10已覆盖、逐Ability产品文字已补；不能据此证明跨能力可接收性或总体设计无缺陷 |
| R10 | 本对话用户当前请求/上一轮截图与并行工作说明 | 监控须默认可读；ServerOS设计不接管MarketOps并行开发及LiteLLM toolcall验证/reasoning专用容器。此次没检查这些运行对象，不推断仍活跃或已结束 |

只读GitHub的hwz-records搜索未命中reasoning不等于没有此工作；本轮改用已知records目录与原文，并将用户说明作为需求约束，不猜新增Item ID/当前bug已修。

## 2. 需求反查出的设计修正

| Finding | 原设计/实现风险 | 本轮选择 |
|---|---|---|
| F01 | 每项局部结果有了，但下一步没有真实接收/继续owner | 最小交接合同、接收验证与parent-owned continuation，沿现有Task/Run保存 |
| F02 | 原日循环把capture和understanding水位耦合 | raw/manifest提交后capture前进，Claim提交后understanding前进；gap与重算可恢复 |
| F03 | 安全被误解成所有事实更新均要人确认 | 分离agent_reconciled和user_confirmed，审查作用域和来源版本，不让人变自动化瓶颈 |
| F04 | range相同的文档变更可能复用旧决定 | 决定与patch绑定source digest、理解baseline和具体接受内容；加same-range changed-content反例 |
| F05 | 缺执行/授权事实被处理为Card字段缺失 | 正式从Agent Control/Work Engine/executor join；不建立Card动态权威 |
| F06 | UX状态表可能被照抄成21套强制状态机 | 状态是正交投影维度，源码enum变化另有owner/兼容，不增加业务pipeline |
| F07 | “测到了bug”与“产品验收失败”混为一谈 | 预先声明验证目的/假设；复现成功可完成诊断，证明修复需真实路径对照，非预期FAIL停产品自动修改 |
| F08 | 文档/模型/Task成功被升格为父目标完成 | 已接受的必需交付集合分别有证据，consumer未接就是未完成相应义务 |
| F09 | 共享模型服务健康验证被当双业务集成 | 真实ServerOS和MarketOps入口读取同instance/model，保留独立业务ledger，质/量/费用分开 |
| F10 | 只要别人WIP就无限等待整个工作 | 文件/接口级交接，明确接收者与解除条件，无冲突工作继续 |
| F11 | 公开知识、协作文档、报告、通知回流各自成功但总结果漂移 | freeze输入/正文版本、对象级grant、publish/send分责、原origin去重和人改重新理解 |
| F12 | 总测试数/全绿字段替代真实验收 | 用工作旅程和交接反例；先声明oracle，再测试，不补更多名称计数 |
| F13 | 重试/子任务/比较实验各自预算、parent可能失控 | 总预算由已存在owner分配，子尝试包括恢复成本，未知计费先记尝试再核对 |
| F14 | 静默窗口/无独立观察被解释为全部工作已收口 | 待投递必须有真实consumer；本机Core健康与整机故障域分清 |

这是针对上述证据的设计审查结论，不是已经验证了所有故障，也不是开启新一轮通用框架开发。

## 3. SciSpace与原始研究的使用

本轮用SciSpace检索协作失败与代码验证研究，随后回到原始论文摘要核对。没有把内部聊天/日志发给学术服务；没有复现论文实验。

- [Why Do Multi-Agent LLM Systems Fail?，arXiv:2503.13657v3](https://arxiv.org/abs/2503.13657v3)：采用设计、协作失配、验证三类失败作为边界反例框架，不把任务集上的比例当本系统故障率。
- [Uncovering Systematic Failures of LLMs in Verifying Code Against Natural Language Specifications，arXiv:2508.12358v1](https://arxiv.org/abs/2508.12358v1)：研究提示评审可能误判正确代码；据此要求finding可定位、独立实测/对照和有界复审，不自动接受任一模型纠错。
- [AgentCompass，arXiv:2509.14647v1](https://arxiv.org/abs/2509.14647v1)：借鉴对实际执行轨迹定位和归类失败的思路，不引入其平台/双Memory实现，不承诺迁移性能收益。

本系统的修法由R1–R10实际工作约束决定。论文只帮助检查盲点，不能替当前Runbook、ACL、安装版本或用户授权。

## 4. 完整度结论与适用范围

此前“21/21设计完整”只能解释为主题栏目覆盖，不是端到端充分性证明。本轮新增所有21职责的任务边界/交接/恢复案例，修订共同协作与日循环合同，并给出可执行反例矩阵。

还必须在实施时验证：实际序列化接口、可信identity注入、当前云ACL/条件写、真实模型/执行端、正确浏览器路径、生产Runbook、并行writer与预算机制。未知现场参数不靠架构补假值；有明确需求的缺consumer不再降格为“不需要做”。

设计验证到这里采用SCENARIO-ACCEPTANCE规定的oracle和任务范围；以后实测发现矛盾修相应owner文档/代码，不无限向每个Skill追加同样的通用段落。
