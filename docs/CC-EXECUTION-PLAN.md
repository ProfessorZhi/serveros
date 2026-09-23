# CC 实施主计划：从当前成果完成 ServerOS 与全部 Ability

2026-09-23整合版。仍为**一个 P0–P12 主计划**，专项文档提供接口与验收，不另起V版roadmap。纳入重点联系人深归档、外部参考、限定Follow、Git/飞书方向、21能力真实消费者、完整工作闭环与监控。本次只修改GitHub文档，没有运行测试、部署、实际抓取或云写入。

## 0. 完成目标与工作方式

后续用户将本计划交CC并要求实施时，在当时有效的项目/资料/费用/效果授权下连续推进。已定产品选择不再反复询问，普通代码、consumer、兼容迁移与跨自有仓库集成由执行者完成；真正新权限、业务歧义、不可逆操作与既定真实产品FAIL停点单列。

交付不是21个目录、一次注册FRESH或若干千测试通过，而是：

1. 获准信息按政策捕获，转发材料覆盖真实，语义变化进入正式认识并能被下一消费者使用。
2. 外部文章能进入正确个人飞书参考区；原文/摘要/批注/认识/规范分责，必要本地保存与Git范围明确。
3. 当前工作从可信来源/委托/焦点到GPT设计和独立审阅、实际执行、验收及要求的文档/通知闭合。
4. 正常安装入口、资源/关键服务/Core自身监控、共享Laya业务消费与失败恢复有证据。

一次委托可包含多个小commit、正规worktree和独立worker，不要求一个大补丁、21个Agent或全部能力每次都加载。Parent负责原目标的必需结果；缺真实consumer是未完成义务，不是“不需要做”。

## 1. 提交前复核的新起点：不要重做刚完成的代码

本轮初读基线为serveros@0f99d45、records@fd526f5；设计期间远端前进，本计划已读取并采用**serveros@7b9d4172a06c7410f3a6d95473df175f71f7ad63、records@81673ebb91daee9e6a900ead38cbf6c804b723fa**。ability-management输入为5dbf5492ccd37715d6fc1509f9edc3fe20806179，MarketOps读取0b30a541ee68621ef65841596c50b07592b962df。实施使用包含本计划的最新main和真实安装，不回退这些观察基线。

| 已读对象 | 现在可确定的代码/记录事实 | 下一步，不等于新LIVE证明 |
|---|---|---|
| understanding_patch.py | 已有card_digest/content_identity，旧source/理解基线决定失效 | 保留回归，不再称缺hash/重造保护 |
| 新understanding_semantic.py | 已有reading校验、按委托提交部分Claim、独立候选、fence内写入和幂等receipt；reading来自原生Agent，本模块不解释正文 | 复用该writer，核可信委托/身份接入、原生reading producer、多view/失效与真实J03/J06，不另写同功能模块 |
| 新source_waterline.py | 已有分source的区间/gap/tail/beyond-source计算；文档按digest而非伪造位置 | 核实际capture/语义消费者；材料/发现覆盖不能由引用跨度自证 |
| 新capture coverage测试 | 已改为按manifest/验证时间区分arrival/change/gone，不冻结旧数据总数 | 保留并按变化面验证，不以今天数量变化逼代码改假值 |
| focus_source.py | 多项grant/worker/resource仍未在该board适配产出 | 从既有控制/执行权威join，不把动态权威塞Card |
| B1 canonical payload | 仍有旧机器路径、full-recompute/manual-only/local-only指令 | 迁原payload、validator和实际loader，design更新不会自动生效 |
| records新增内容 | Nature有新增消息、2个docx/1图和SOP快照；新增Bruce/Nature/项目认识记录 | 保留全部新数据，复核覆盖与出处；不重复“Nature还只有38条/无文件”的旧判断 |
| publish-excluded.json及test_publish_plane | 新增发布排除登记及manifest联动；凭据类字节保留在受控归档、Git只安全记录 | 与按大小的heavy-media政策分开，保留当前reason-code/结构身份口径，不合并或重建 |
| 最新records README | 报告历史远端已有含凭据图片，并明确排除当前HEAD不能撤销历史传播 | 作为优先安全待核项通知owner；不读取/重抄值、不声称仍有效。按实际权威轮换/处置，禁未授权生产变更或历史force重写 |
| MarketOps laya.py | 已读adapter仍in-process english模式 | shared服务存在不等于该consumer已迁，接实际shared client |
| 旧执行ledger | 记录了Plugin/Testing/release/Serper/Laya等成果，也有日循环及若干场景未通 | 作为现有资产去核验，不重新部署第二套，不将旧运行统计当现在实测 |

以上仅源码/记录只读审查，未重跑新测试，也未读取主机进程或当前云ACL。没有模型client在writer模块里是正确分工，不应逼纯校验器自己做推理；应让原生Agent实际生产reading并由可信控制边界提交。

## 2. 开始只需一次范围配置

读ARCHITECTURE边界、最新Blueprint、本计划；每个切片再读相应focused design与场景，不每次全库注入。共同资料合同为INFORMATION-DOCUMENT-LIFECYCLE、DEEP-ARCHIVE-POLICY、EXTERNAL-REFERENCE-LIBRARY；协作为COLLABORATION-CONTRACT；监控为NODE-OBSERVABILITY/MONITORING-ACCEPTANCE。

从现有可信委托、host profile、source/ops Binding解析：允许项目、原文/回填范围、数据级别、可写树/文档章节、开发安装、Git目标、费用/外发预算、receiver、时区/周期、资源与撤销。缺少真正改变风险的字段一次聚合，其他工作继续。

本文件不自签授权。当前LiteLLM生产/共享网关保护边界、人执行切流/回滚不变；客户联系、私人资料传播、模型API计费、云删除/ACL扩大、不可逆迁移分别核权。“禁止客户邮件”不等于所有已批准模型API都禁；可读也不等于可外发/发布。

## 3. 并行保护与记录

只读识别MarketOps、LiteLLM toolcall/排版验证、reasoning专用容器、SRV-0011/SRV-0005等真实owner/状态，不能从旧报告猜已结束。相同文件/资源冲突只暂停对应片段，约定作者/接口/commit/验收交接；不同工作树无冲突部分继续。

不reset/clean/stash他人WIP，不force-push/批量git add，不借别人的容器/profile做故障演练。ops检出同时是部署目录时，更新文件也是运行变化，不为测试直接pull到生产路径。源数据/排除表/测试未经本任务授权不顺手改。

使用既有execution记录/checkpoint，记录最新接受输入、实际结果、下游receiver和未完条件；不再建第二完成度数据库。时间/预算或真实安全停点到达时给可恢复checkpoint，不承诺不存在scheduler的后台继续。

## 4. 依赖顺序

```text
P0 当前成果/owner/权限
 → P1 共同身份/角色/版本/旧入口适配
    ├→ P2 归档/Forward/Follow/Web → P3 实际reading/writer/理解
    ├→ P4 Doc/Space/publisher → P5 Reference/指南/知识消费
    └→ P6 当前工作准入 → P7 GPT/Quay/继续责任
P8监控、P9载体/环境可在无冲突域并行
P10共享Provider真实业务消费，不阻断不依赖它的步骤
 → P11四条真实用户链与失败恢复 → P12安装/激活/目录收尾
```

先跑小规模“真实资料→认识”和“网页→个人Reference”暴露consumer问题，不先做全catalog绿化。已有semantic/waterline和Nature资料直接进入复用/验证阶段，不从零重写。

## P0 当前实现与最小实施清单

核远端/source、相关工作树dirty、实际安装/服务receipt、活跃writer。针对每个目标记录当前source/正常入口/receiver/已测范围/具体缺口。新代码存在不自动算运行过，旧缺口已修就从待实现转为回归。

敏感资料采用最新publish-excluded登记，保留排除与heavy-media两种原因；新增传播前检查受众。历史凭据暴露的有效性/轮换只有owner能确认，准备最小处置包，不能在文档里复制真值或未经授权重写历史。

**退出门**：下一切片有明确owner/base/consumer及安全范围。不要求全机每个UNKNOWN都裁定才能开始。

## P1 共同数据与source声明

复用Source/Document/manifest与实际resolver。desired关注范围/角色拟归information/sources，observed仍在manifest；DRIFT_WATCHLIST和not-archived用adapter迁移，不两个可编辑表同时决定同字段，不创建额外source数据库。

材料N:M关联message/forward part/source revision；网页有安全input/final/canonical关系、原始/提取hash及parser版本；Reference是自己的doc，原文/批注分责。每个新字段有实际producer/consumer和兼容测试。

**退出门**：旧chat/doc/Web输入可解析给实际consumer，多材料不覆盖，同名/跨ACL不误合；desired变化不改observed历史。publish-excluded是发布安全登记，不是源抓取黑名单或体积清单。

## P2 重点深归档、限定Follow与Web获取

复用现有sync/expand/drift/_redact，不复制规则。普通SHALLOW、bot NONE不抓正文、已接受priority/项目群DEEP_REQUIRED。历史回填有范围和预算，新消息先保存，旧pending/index对账以实际材料与source版本为准。

转发包内可见子消息/子转发有界遍历和去环；外部网页出链默认第一层，不扫描原群。unsupported/token缺失/限额都是discovery gap；0材料+未解析不能100%。现存文件先hash/关联验证，只补缺失，不重复回填Nature新到数据或为了旧“complete样板”忽略缺口。

Web优先原生HTTP/官方接口/已有parser，核原页身份和抽取质量；登录壳/JS空页/403/截断不算全文。只收藏链接不自动下载全部媒体或Follow；完整保存按实际许可/敏感性，并区分归档与Git发布。

**退出门**：授权小范围新消息/forward真实落地、NONE零正文、分页/编辑/多附件/未知格式可恢复；一个公开网页真实解析、source版本可回查。当前排除图片的私有归档完整不因不在Git而判丢失。

## P3 用已落地semantic writer接通原生理解

读取understanding_delta/proposal、水位和新understanding_semantic的真实CLI/schema，复用其fenced patch、digest、候选和幂等机制。原生Agent读取固定版本内容并生成合法reading，可信adapter从真实Agent Control解析委托/actor再提交；不让模型自填grant/subject/major-change声明成为唯一安全判据。

观察/有依据推论可在当前委托内agent_reconciled；重大承诺/目标/负责人/期限/权限需真实接受。现有writer能拒user_confirmed不等于来源支持已验证，引用范围合法也不自动等于断言属实。原生reading、来源支持与调用者信任一起验。

复用source_waterline的区间/gap/tail和文档digest，实际材料/发现/语义覆盖仍独立；卡片引用跨度不是每份附件已读证明。自然资料、新Reference、会议纪要中的候选/噪声和多view更新分别处理；Memory未绑走records/native，不重建平台。

**退出门**：真实source→原生reading→已有writer commit→相关people/project/topic→下游接收；当前Nature/Bruce新认识能追源而非重新编一份。重放、源/卡变化、越引用范围、错误委托、source撤回、view写一半、材料迟到/Memory故障均有证据。源码测试数不是J03已跑。

## P4 文档对象、位置和发布方向

复用真实Space/publisher。保留MyDocs工作/日报等既有结构、独立个人笔记与Published Knowledge；仅复用/创建必要本人“参考资料”节点，实际ID/ACL/对象类型核对，不建同名替代库。

外部Source→获准snapshot；飞书原生Reference/指南→只读本地；Git规范→云视图；用户改投影→回源提案。没有mtime通用双向同步。自动摘要/来源区和个人批注区有可核身份，不能模板更新覆盖人改。

Publisher按logical identity幂等：intent→fresh baseline→真实条件写/三方patch→readback→receipt。Provider未证明CAS时保守共享冲突范围；本地锁不保护远端人写。body/media/index/send部分效果分开，未知先查不盲建。

**退出门**：获准测试对象create/update/重复、人改冲突、媒体partial与未知结果恢复；原URL/受众不扩大；只读邻居未改；0 children正文不删；备份不变第二权威。

## P5 外部Reference、指南与知识消费

执行EXTERNAL-REFERENCE-LIBRARY的REF01–REF12。用户收藏默认原链接+原创摘要/用途/批注，未读全文不编要点；完整副本需许可与材料coverage；未要求持续关注不设timer。可用已核官方文章作公开Source，实际执行再取当前版本，不在设计仓转载全文。

源变更先标摘要待核，个人批注保持；采用建议经B2/B5/B6与相应授权再改规范，网页命令不执行。限定Wiki子树/new-child策略不能扩整库；原SOP只读/提案，shared工作doc按授权章节维护。

至少一份真实自管指南/共同章节由版本/截图证据支持局部更新；员工snapshot与实际Bot消费分开验证。Support缺人工receiver不阻止Reference/SOP工作；只在真实receipt后说转交/通知成功。

**退出门**：原网页→正确个人Reference→source/本地安全记录→检索与版本关系；重复无双份、人批注不丢，文档变化/撤权/回源提案可恢复；任何未授权全文或更广传播准确部分交付，不假已同步。

## P6 当前工作与多权威准入

Work Engine取生命周期/依赖/focus/lease，Card取目标/AC，Agent Control取grant/scope/revoke/budget，executor取实际worker/effect，Binding给readiness。修focus_source接收这些事实，而不是向Card塞动态字段。

旧projection/store/records对账保取消/替代；缺某项目视图不是0项。current范围freshness与render时间分开。new candidate核无冲突intent/lease，不要求假stop；已有cancel/unknown必须核实际attempt。

最多两个root焦点、已运行/等待继续占位、child不变第三root；合法委托允许排序时给有依据建议，不默认P0也不永远因无priority字段阻断。

**退出门**：真低风险目标得到一次合法准入/dispatch；重复竞争、撤销、过期review/source/plan、旧Run结束但进程活、park/resume/partial effects正确。没有grant可给建议，不能执行。

## P7 GPT、Quay与实际继续责任

复用CodexAppServerProvider/exec、review_receipt、bounded_invocation及当前Quay。真实设计/独立review Session、输入和model/effort配置、turn完成与final归属明确；APPROVE/exit0不发授权，review finding须核证据，预算有界不无限审。

Parent在既有Run/Task/checkpoint保存asked-for/inputs/authority/resources/receiver/accepted结果/continuation。child创建≠执行≠验收≠父目标结束；退出前有真实接续consumer才说会后台继续。

Quay至少执行一项有价值研究/review/实现，实际worker产物回父；shell gate不是研究。浏览器由明确允许的Testing Provider，不解除全worker黑名单。父预算覆盖重试/子调用/模型对照，未知费用记录实际尝试。

**退出门**：从接受计划到真实executor、产物、独立验证与Parent接收；parent重启查原Task而非重复创建；cancel未停不释放，旧epoch晚到拒覆盖；一项等待他人WIP不停止无关工作。

## P8 可读资源卡与Core独立健康

执行MONITORING-ACCEPTANCE M1–M6/V01–V18。资源摘要显式字段而非lines[0]；本人CPU/RAM/盘、整机余量、网关功能、必要Tailscale/隧道/Core默认可见，declared endpoint不冒充probed，unknown不0/green。

OS/有限独立check核serverosd进程、IPC、cycle、持久化；Core坏时合法独立outbox仍能留/发该告警，恢复不双写/双发。原生Feishu 360/390px默认折叠和实际payload回读；HTML仅预览。无离机observer就不承诺整机断网必达。

合并现有卡片owner成果，不抢dirty；旧CLOSURE仅历史，不指作当前完整状态。隔离实例故障测试，不断真实网络、不借并行容器。

**退出门**：实际获准测试接收端可读、冻结receipt、升级/恢复/timeout/quiet/重复正确，Core独立失效路径有证据。收到一张卡不等于系统全部LIVE。

## P9 全21能力的真实载体与入口

按ability-management/design/ABILITY-REALIZATION-PLAN逐项核：canonical artifact/source、shape、实际Provider/consumer、Binding、安装和用户场景。已有工具足够不新建Skill；设计和旧payload冲突要迁具体source及validator/loader，而非仅写说明。

G1/A1/A2做相关依赖与薄上下文/更正；A3/A4/A5保护worktree/网络/profile/跨端lease；A6/A7/A8复用真实Quay、Testing Plugin和native发布；A9/A10保持两个Skill与真实API/jump/Runbook，人执行边界不变；B1–B10接输入/认识/需求/工作/研究/设计/实施/写作/正文/投递。

**退出门**：每职责从目标普通runtime可发现/调用并有真实receiver。缺现场身份、生产许可等精确标该层，不将只会安全拒绝当正向完成。真实headed登录E2E按AC，mock/synthetic不替原路径。

## P10 已有共享Laya与两个真实业务消费者

先复核已部署实例/权重/依赖/资源/合法key注入，不创建第二份服务。MarketOps当前in-process适配保留为显式实验时可以，默认共享开发模式须改走现有shared客户端；ServerOS接一个真实非安全关键用途，不以acceptance-a/b替业务consumer。

两端同instance/model、独立namespace/evidence/budget、公平有界队列；服务工具链独立于并行MarketOps mutable venv。Laya仅shadow/advisory，ready不证答案质；超长/错语言/高置信错答不自动事实化或权限放行。

Serper复用真实SearchProvider及不可变响应，原文才支持邮箱归属；Jev调用许可与客户邮件分开，credential未注入不说无key。无data/费用许可只该LIVE实验受阻，代码/反例仍做且不删除full-MVP必要对照。

**退出门**：两个实际入口、单模型、公平/取消/超时/停服不自动私有外发；一条有限research-only有原文和领域结果。MarketOps Docker/Compose/Hosting和客户发送延期，不借共享集成扩范围。

## P11 四条真实链和有意义的故障验证

复用SCENARIO-ACCEPTANCE J01–J10、监控V系列、本轮REF01–REF12与DEEP §10。变化面相同且有效的旧结果可复用；完整套件在适当集成/发布边界运行，不每个小改重复全世界。

必须正向证明：①真实消息/转发材料→正确coverage→原生reading/semantic commit→下游；②网页→本人Reference→获准本地/安全Git关系→人改/源更新；③J06当前工作/可信委托→GPT设计review→实际worker→AC→要求的报告/维护/通知→回流；④普通安装、两业务shared Laya、资源卡/Core隔离故障。

事前定oracle与scope：缺陷复现成功可完成诊断，NOT_REPRODUCED不是全局修复；预期安全拒绝是测试成功；真正非预期产品AC FAIL按既定规则收证据并停该产品自动修改，不删断言/降真实度。其他独立工作继续。

**退出门**：accepted必需交付都由实际consumer接受，未跑/受阻准确。生产操作未授权不执行；A9/A10可实现/只读部分与现场缺口分别记录，不把假生产fixture标LIVE。

## P12 发布、有限启用与目录收口

producer/schema兼容→consumer→Skill/Plugin→pin/派生view→正常安装→安全激活→running回读。各repo非force集成，保护远端并行成果；当前symlink更新与进程reload分别观测。

目录stage/copy→内容/权限/引用校验→真实consumer切换→fresh验证→允许时退休旧路径。unknown/保留备份可成为正常结果，不能为了MISPLACED=0或零staging删唯一数据。保全archive tags/未推送refs、活动profile/容器及排除归档字节。

飞书只做授权范围必要分类/索引修复、原对象回读，不全库重排或删他人源。publish-excluded与heavy-media各保原owner，缺字节要先查合法外置记录而非判丢失。

完整daily timer在J06真通过及可信trigger/互斥/预算/撤销/恢复配置存在后才启；若仅workbench render，只称资料刷新。独立归档/播报可各自门通过后单独启用，但不汇总成全链已完成。

停用先停止新准入、核真实worker，再关本轮受管trigger/恢复旧安装；数据/证据不清空。不可逆数据变化须预先备份/演练与明确权限。

**最终报告**：source/build/install/running版本；21项入口/receiver与未覆盖；四条真链；归档/discovery/材料/理解和Reference各自范围；磁盘/飞书实际回读、保留例外；安全事件待owner动作；明确停用/回滚。不要再以“只有最后消息没发”掩盖未完成的业务consumer。

## 5. 如何不再中途停错地方

本计划已决定Source/Reference角色、深归档、单主同步、Testing Plugin、生产两Skill、共享Laya及Parent继续。缺函数/producer/consumer、普通跨自有仓合并、数据适配和安全可逆迁移都属于实施。真实费用/数据/生产/ACL/不可逆/业务冲突或约定产品FAIL才找人，给一次聚合的小决策包。

已写下当前约束不等于可信runtime已执行；必须让现有控制面消费。长任务预算用尽给准确checkpoint；未经scheduler真实登记不许口头后台承诺。对已进main的新成果先复用和回归，不反复依据旧报告重做；新证据推翻设计就在所属合同/实现修正，不无限新增同义文档。
