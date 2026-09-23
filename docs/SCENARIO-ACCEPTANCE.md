# 协作场景验收：从真实目标反推证据

2026-09-23设计，不是已经执行的测试报告。P11原十类场景仍保留；此矩阵增加验证目的、跨能力接力、完成判据与恢复，使“每个工具成功”不能替代“用户目标完成”。现有测试runner/Run evidence即可，不需要新验收平台。

## 1. 验收基本方法

每个case先冻结：原目标/accepted AC、实际scope/source/代码/环境、oracle、必要与可选交付、允许effects和预算。再记录各边produced→received→accepted→effect/receipt，而不是测试后按输出倒推PASS。

系统组件测、真实业务测、效果授权是三回事。合成样本可测试拒绝、计数、丢消息、并发；只在获准测试对象执行。真实生产/用户环境的未授权部分保持BLOCKED，不能用fixture写成实测成功。

独立测试输入从真实producer或其固化样本取得，consumer输出用外部原事实校验；若两边使用同一工厂/同一错误parser，绿色不构成跨接口证据。人读验证保留默认折叠和真实客户端，JSON/HTML可解析只是前置。

## 2. 真实工作旅程

### J01 已有能力验证与toolcall复现范围（P11 S6的简化交付型）

依据R1与用户并行验证诉求：先从原目标确定“验证已有行为”，不会先开发。A1/B3确认客户端/route/版本与旧证据，A3/A4只准备获准独立环境，A7核真实路径，B8交有范围报告，B4更新Outcome。

成功：验证满足所需AC即使0代码也可结束；未测生产/UI明确排除。负例：mock能复现但原路由没复现，不推导全局修复；旧版本PASS不证明当前客户端；别的worker正验证同范围时复用/交接而非重复启动。

恢复：测试中断保留已发生请求和范围，继续需核current baseline及未知效果，不能重发计费请求凑完整。此case不授权触碰用户正在运行的两条LiteLLM任务。

### J02 费用/用户报错到RCA（S1/S10）

依据R2：B1取得准确消息/必要截图，A9按user/key/request/窗口合法查询，B5固定各域证据/独立重算，B3明确诊断或修复新scope，B8/B10给支持解释。

成功：能区别raw usage、normalized、价格配置、spend与budget，反证和缺口清楚；不要求为了RCA全改生产。负例：缺cached字段不等于0，shared key混流量不能归人；query无匹配但阳性对照也失败不得NEGATIVE_RESULT。

恢复：某域不足仍产限定结论，不能重读更广private原文；需要改配置交A10/人，而不是只读Skill顺手执行。

### J03 信息增量、会议与材料（S1/S2/S3）

B1 metadata-first确定NONE/SHALLOW/selected DEEP，raw/manifest先提交；B2实际语义读取/patch更新关联views；A2失效旧记忆；B9只对需要的材料正确落位。

成功：一条真实获准信息改变相应认识，或有证据判本轮不需改；无新信息不造工作。会议负责人/期限不明确保持candidate。附件只取需要的，其余明确未读。

负例：bot无消息计数依然零正文请求；真人群有bot不丢；编辑同ID/撤回/中途分页/过期附件范围真实。恢复：capture已提交而语义阶段崩溃，重启仅续理解；source正文变而range不变旧确认拒绝；自发日报不产生自循环。

### J04 自管指南与协作文档（S4/S5）

R3限定原知识库/指南关系，R4是外部原SOP。B9 resolve owner/maintainer/audience/原ID，B5/A7给适用版本事实，Space/publisher实施获准局部patch，B1/B2 Follow回流。

成功：正确原URL/读者/段落/截图与独立readback一致；自己的指南与邻居readonly分开；共同文档没有接管作者；SOP变更只是差异候选，未接受不改变执行policy。

负例：身份有edit不等于move/share；0children但有正文不删除；create回版本不是最终正文版本；本地锁挡不住人编辑。恢复：正文已写、媒体失败只补媒体；send timeout先查；自己的revision已去重而后续人工修改仍进入B2。

### J05 MarketOps研究与共享Laya（S8）

真实业务MCP提交有限research-only任务，Quay真实worker用Serper取候选、合法获取原网页/记录块，Laya/Jev按允许配置输出shadow建议，领域服务验Account/route/opportunity；另一个ServerOS实际业务入口访问相同shared Laya。

成功：两个实际consumer观察到同instance/model digest且只一模型进程；Account与asset不混、literal邮箱归属和职能有证据；报告区分合法partial research与full-MVP要求。

负例：in-process english adapter不能被算共享客户端；model高置信不是邮箱事实；无邮箱不强造D；服务ready不代表答案质量；无Jev合法调用不能静默删除必需对照。恢复：共享服务过载/timeout不新起模型或自动外发私有数据，已验证领域结果不因optional shadow失败丢掉。

### J06 完整日循环（S6）

选择低风险真实目标，事先明确必需交付。完整链：登记源变化→语义认识receipt→当前board/委托→最多两root焦点→GPT研究设计与独立review→真实executor→AC验证→要求的报告/维护/投递→B2/B4回流。

成功：每次交接有真实receiver和接受证据，Parent在最后必要结果满足后结束；不用人每步搬文件。可以0代码/少于2目标，但不能只render START-HERE或写proposal。

负例：record-only不当ACTIVE；grant不从Card/模型制造；从未启动候选不要求假stop证明；空APPROVE/旧final/撤销grant拒用；child done不关闭父；无优先级不自动P0。

恢复：duplicate scheduler只一次准入/dispatch；Parent新Session读取实际child状态而非重开；worker结束但外部effect unknown不能释放并重复；一任务待决定另一独立授权任务继续；quiet期有真实outbox与sender才算排队。

### J07 跨端验证/交接（S9的设备路径）

A1准备scope/checkpoint，A4/A5核目标身份/工具/网络，A3/B4明确写入域/epoch。默认验证可限定delegate，不必迁移整个工作。

成功：远端结果可被父端合法读取并对应相同AC/版本；真正writer handoff则旧端停止/fence、新端持权、首次写各有证据。

负例：文件hash相同但target tool缺不能报业务就绪；手机remote view不产生第二worker。恢复：新端未ready旧owner仍有效；转移中断不双写；真实等待只有该分支，其他scope继续。

### J08 运行状态卡与Core失效（S7/S9）

执行MONITORING-ACCEPTANCE的M1–M6/V01–V18，不重复另建卡片设计。当前必要资源/服务给默认可见摘要，独立观察Core而非让它自证。

成功：native Feishu不展开可判断CPU/RAM/容量和关键服务；raw字段/型号不替结果；Core死/IPC卡/采样停滞能通过合法独立outbox留证/投递，整体失联覆盖边界准确。

负例：relay/手机睡眠/on-demand停机不自动报警；“读到了failed”不能green；declared endpoint不当observed；旧CLOSURE文件不做当前状态入口。恢复：send期间升级/resolve/reopen旧ACK不吞新episode，quiet与empty queue不判sender坏。

### J09 安装与目录/证据迁移（S9）

G1/A3/A8固定canonical/pin/artifact与真实consumer，计划非破坏性切换并保留当前/previous；A4核running版本，B10给准确部分状态。

成功：正常新Session与运行进程各自使用正确版本，旧路径引用归零或有明确合法保留；Git refs/tag/历史唯一证据不丢。不是所有目录最终必须只剩一个root。

负例：working tree通过不等于安装；current symlink更新不等于进程reload；staging名字/clean Git/0children不证明安全删除。恢复：已复制未切consumer可退回；跨库/远端多对象迁移没有假ACID；身份/资源冲突不抢写其他开发。

### J10 报告、静默与Support人工兜底（S5/S7）

B8生成准确范围内容，B9做必要发布，B10按receiver/窗口发送；Support采用批准snapshot，问题不能解时交绑定owner。

成功：所要求收件端真实receipt与准确文档版本关联；内容层、publication、delivery各自能回答；没要求发通知的任务不强制发一张卡。

负例：导出知识包≠Bot消费新版本；未投递不说已转人工；API查询许可不解禁客户发送；日报不把验收成功改写成生产上线。

恢复：静默期间可生成待发intent，醒来重查receiver/内容版本/授权；回执丢失但远端已接受先查，不重复新建消息/报告；无人receiver不冒充待办已被接受。

## 3. 完成度从证据计算，不看目录数量

记录每个case的实际source/intent、环境/版本、有效授权、必要交付、断言、观察/效果与receiver receipt、未覆盖和对应Run/Task。至少包含正向、关键负例、一次重放/中断恢复；不用为每个case发新业务Item或新建证据数据库。

主题栏目覆盖可说明design有定义；实际场景PASS证明该范围集成；安装/生产验证才证明目标LIVE。未运行写NOT_RUN，不支持写UNSUPPORTED需有接口依据，UNBOUND不是不存在。被发现的设计/实现差距保留到相应目标的接收验收通过，不能从最终报告消失。

## 4. 输出给用户

首层按用户目标报告：已经解决/验证什么、在哪个版本和范围、未满足的必需项、具体下一owner/条件。总测试数/代码行/模型调用次数仅为附带工程信息。

本轮只形成设计案例与oracle，没有重跑这些场景，也没有权限替用户注入真实生产故障或重复并行工作。
