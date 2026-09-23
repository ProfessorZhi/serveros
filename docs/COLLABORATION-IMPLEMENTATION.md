# 协作收口实施细化：沿现有 P0–P12，不新增规划轮

2026-09-23，配套COLLABORATION-CONTRACT与SCENARIO-ACCEPTANCE。主计划仍为CC-EXECUTION-PLAN.md；这里定位本轮R1–R10反查后的源文件/consumer缺口和完成顺序。本文件是执行约束，不自动授予运行/数据/费用权限。

## 1. 工作顺序和保护范围

先获取当前合法基线/实际安装与并行owner。依据当时事实，不复用旧台账中“已无权/无凭据/某worker结束”的判断。保护用户并行MarketOps及LiteLLM验证/调查；本次开发只改相应自有module/分支，冲突部分有明确interface交接。

建议五条实现线可并行，但在共同schema/owner上一个集成者：

1. B1/B2/A2：源增量→语义patch→正式理解→失效与视图。
2. B4/B6/A6/B7：多权威准入→GPT/Quay真实工作→父目标验收。
3. G1/A3/A4/A5/A7/A8：真实载体/环境/安装/跨端与可恢复资源。
4. B8/B9/B10：文档角色→局部publisher→报告/通知/知识消费。
5. A4/B10与共享Provider：监控产品路径、单Laya两consumer、Serper/Jev准确调用边界。

不是“21个Agent同时写”。同一scope内选择最少worker与小diff；不能为了保持Quay忙碌拆出无价值任务。

## 2. P1/P2/P3：先修两水位，再完成语义writer

真实基础：records已有sync/expand/drift采集；serveros/intake/understanding_delta.py与understanding_patch.py。先验新设计是否已被其他分支实现，再复用，不从旧main重做。

understanding_patch现仅提重读范围，不读正文：保留其明确能力，不把它改名就称Claim writer。增加真实语义消费步骤，由已授权原生Agent读取固定source内容，产生限定patch；由可信写入适配验证source/理解baseline、scope/来源和人确认语义后提交。

不能要求每条机器事实都人工签字；也不能让模型自己标user_confirmed。agent_reconciled与真实人接受分开，未确认业务承诺保持候选。原range决定匹配需包含内容身份/版本，给same-range changed-content和错误actor来源反例。

测试先复现：raw提交后语义失败只增加lag；再跑不同source revision、edited同ID、partial页、source撤回与Memory断开，确保原源未被写者篡改。写成功后相关view/consumer接受可核，不能只返回一个JSON文件。

## 3. P6：准入context的真实producer/consumer

真实基础：intake/focus_source.py、workchain/board接口、context/delegation.py、runtime/agent_control_store.py、Work Engine Run/lease。NOT_PRODUCED列表是定位差距，不是最终交付。

将grant/scope归Agent Control、worker/effect/resource归真实attempt/lease、依赖/focus归Work Engine、目标/AC归records；适配join当前版本并在admission重新验证，不强迫Card携带所有字段。

对从未执行候选，证明无冲突reservation；对旧attempt先确认actual stopped/fenced和未知效果。投影freshness按相关输入，不用某无关历史文件最老时间阻塞全部目标；相关性无法确认则保守该scope。

验收J06：两个同trigger竞争只有一个dispatch、撤销/过期拒绝、卡片存在无授权不能执行、优先级缺时按已授权排序而不伪造P0、park后资源确实释放。用户说“最多两项”不允许child算第三root或每周期新造两项。

## 4. P7/P9：让交接被实际执行者接收

真实基础：既有CodexAppServerProvider/exec、review_receipt、bounded_invocation、Quay当前原生CLI/driver。不要仅写adapter调用格式或检查installed后结束。

Parent交固定input/AC/allowed effects给真实执行者，接收确认scope后启动；结果绑定source版本和输出receipt，由实际consumer验收。设计/独立review必须真实不同Session，空final、过期review、作者身份缺失不能放行。LLM finding需要事实校验，不照单重写正确代码。

continuation存在于既有run/checkpoint控制对象，明确parent/receiver/trigger/last effect。没有被运行时读取的字段不得用来证明“会自动继续”。parent压缩或重启时查询已派Task，不重复create。

Quay验收必须包含至少一个真实研究/review/实现worker，shell gate只算机制；一任务需要浏览器且worker禁用时调用独立Testing Provider而非解除所有黑名单。向子任务分配预算，不能各自拿父总额度。

## 5. P4/P5/P8：发布、通知和知识接收者

采用既有Document/Space/source合同。准入payload含原对象、accepted内容/来源、章节scope、expected revision/hash和receiver；API缺可靠CAS时受管局部方案或提案，不假称本地锁防住人改。

正文/媒体/索引/通知分段receipt，错误恢复只补未完成效果。当前self-generated revision通过origin去重，新人工修改保留；SOP新版本需要业务接受，不从可读/可编辑推导维护/发送权。

实际guide知识生效需snapshot消费者/权限测试，不以导出器exit0结束。Support人工兜底缺receiver仍能完成只读SOP/协作文档验证，两个场景不可混；发送静默不等于其他工作必须停止。

监控专项执行MONITORING-ACCEPTANCE：producer真实指标→明确summary→native布局→冻结投递→Core独立失效路径，不从status rendered推导全系统健康。

## 6. P10：共用Laya不是可 import 两次

当前MarketOps/adapters/laya.py明确进程内english Router；按已确认开发拓扑添加/选择真实shared-client adapter，复用ability-management/providers/laya客户端协议。旧in-process可以保留为显式实验模式，不能默认为共享。

ServerOS必须有至少一个真实非权限关键业务consumer；不得只新建acceptance-a/b证明“两消费者”。实际instance/model digest、单进程、命名空间、队列公平、超时释放与private-data不自动Jev fallback均验。

新adapter配置和service deployment独立，避免共享service跟随MarketOps活动venv升级。语言/最大token/答案schema/校准在consumer的DecisionSpec版本中清晰匹配。Jev、Serper实际调用需要相应费用/出站数据许可，客户外联继续禁止。

本阶段不写MarketOps Docker/Compose，不改其原SOP/发送系统。缺Jev授权可以交明确UNBOUND但不能把其full-MVP要求从验收删除。

## 7. P0/P9/P12：资料层级与部署一致性

源码、构建artifact、安装record、fresh Session loaded和daemon实际running分别验。canonical Skill payload若仍与新设计矛盾，必须在相应repo迁移并用普通安装入口验证，不能只补design。

源/目标跨仓有依赖就固定并兼容迁移；先保护原有refs/dirty/活动consumer，正常集成，再安装候选，再安全切换。目录整理先验证真实内容/权限/引用，不为清单数字一致而删正确例外。

原stale台账/CLOSURE只在有真实consumer时修该consumer/刷新owner；无人维护文件不能作为卡面当前结论。old local main只是落后时正常事实标注，不能误判分叉触发破坏操作。

## 8. 恢复与最终退出

每条必需交付有通过/未通过/未运行及证据；受阻项有精确owner、所缺条件和可继续范围。普通代码/schema/consumer问题属于实施，不重新求用户设计；真实生产/外发/权限/费用/不可逆迁移按已有policy。

日循环只有资料render仍不能enable后称全链。先用J06做一个低风险真实目标并验证中断/重复；监控/输入等独立功能可单独激活并明确范围。可选实验或当前只读权限缺口不致使全部工作冻结，但必需功能未实现不能放到“不是没做完”类别。

最终报告按用户任务和必需结果，而非21项全绿矩阵；可引用真实Run/Task/provenance，不创建新的完成度数据库。论文只指导反例和review方式，不是产品可靠性的证明。
