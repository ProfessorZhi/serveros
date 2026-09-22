# CC 最终实施计划：ServerOS + 全 Ability 家族

状态：可执行工作计划，2026-09-22。此计划基于本轮只读代码/records/归档消息与GitHub设计，不代表任何服务已启用。架构决定不再重新开放讨论；实现要核对最新代码、安装与真实权限。一次执行委托可连续完成多个有界工作包，不要求用户每个包重新下令。

## 0. 交付目标与禁止误判

交付的是可发现、可加载、可调用、有真实消费者和证据的能力组合：默认浅档与按需取材料、版本化理解、正确工作对账、GPT设计/独立plan review、原生/Quay实施、测试、公开/共享文档维护、报告及低噪声播报告警。

以下不是完成：新增一堆Skill名、注册表绿色、几千测试通过、一个model/CLI进程可运行、Quay执行一个shell验收、计划生成但没有执行端、工作树成功但安装入口仍旧。DESIGN/CODE/INTEGRATION/LIVE必须分开。

本轮用户只授权文档写入；随后用户将本计划交CC并明确要求实施时，执行者按当时已有standing授权/项目边界操作。不能用本文件自行铸造生产、外联、共享网络或敏感数据授权。缺一个真实权限只暂停该动作，继续所有不依赖的工作。

## 1. 开始前必须读的确定蓝图

1. 本仓ARCHITECTURE.md及resident surface约束、docs/daily-work-cycle.md。
2. 本仓docs/INFORMATION-DOCUMENT-LIFECYCLE.md、BROADCAST-ALERT-DESIGN.md、shared-decision-search.md。
3. ability-management/design/ABILITY-CANONICAL-BLUEPRINT.md：21语义职责与18旧导航簇对应；不是21插件。
4. ability-management/design/SOURCE-DOCUMENT-CONTRACT.md及各详细设计；SHARED-PROVIDER-CONTRACT.md继续有效。
5. ability-management/design/ABILITY-DESIGN-EVIDENCE-20260922.md与早先证据审查；把source日期、代码观察和LIVE实测分开。
6. 需要MarketOps时读当前ProfessorZhi/marketops的ARCHITECTURE/MCP_SERVICE/DEVELOPMENT_INFRASTRUCTURE与被接受SOP，不从旧research-pipeline检出重新造。

本轮设计输入基线：serveros 49c19c4178b6682b538f8f8384db9fb1022798e1；ability-management cee243ad76856981012bac2a4aefca61b91024dd。**实施使用包含本计划的最新远端main**，不是回退到上述旧输入。并行未推送分支与已装release另查，不盲cherry-pick过去报告的7个commit。

## 2. 已定事项，CC不要再让用户二选一

- cards提供稳定语义；Work Engine拥有控制状态；Board是join后的投影。差异做reconciliation，不换权威。
- 默认IM SHALLOW、明确bot/app/测试黑名单NONE、特定材料需要时DEEP；自聊和真人Tokyo群KEEP。
- 文档的follow/维护/owner/受众/正文权威/位置独立。同一文档可以既输入又维护。
- 自己公开指南放已有指定知识库的获准页面/子树；别人的默认只读或获准局部维护，不接管整库。
- 原Bruce SOP是source，当前MarketOps research_only=true、send_authorized=false；此项目Docker以后再做。
- ServerOS是唯一卡片语义/文案owner，ops只实例布线；正常播报和事件告警分开。
- Laya一套CPU共享服务供两个项目轻客户端使用，默认shadow；Jev远端与Serper出站受数据/预算约束。
- Testing是最小Plugin；生产观察/变更是两Skill；其他按已定最小形态复用，不给每个vendor造MCP。
- GPT原生Provider与Quay已有就复用。缺daily transport binding不等于无模型；cwd没有.quay不等于无Quay。
- 真实产品验收FAIL要在安全粗粒度边界展示并停止自动产品修改；production cutover/rollback由人执行。

## 3. 一次性实施配置，而非每步问人

从已有host profile、ops Binding、Agent Control及源registries解析：可参与项目/对象、允许effect、来源cutoff/归档例外、读写/受众范围、工作树/数据/证据根、预算、模型/reviewer、Quay、测试环境、通知receiver、时区/例行时刻和撤销方式。

明确证据线索：归档09-18消息指定AI平台知识库（原message定位见证据E3）作为自管最新版指南落点；LiteLLM工作文档原authoring对象在现有maps；Bruce SOP是三人群的docx来源；Serper key先查受保护本机绑定，不索要已存在密钥真值。在线验证当前ID/ACL/endpoint，不从过时map猜已可写。

真正缺scope/费用/receiver时一次列出精确字段、哪步依赖、保守可做部分，不能以“要不要开始”反复停。配置建议不算生效授权；原始消息里的动作也不自动授权。不得创建另一个grant.db或把一条APPROVE永久复用。

## 4. 工作包依赖图

```text
P0 当前状态与自有变更边界
 ├─ P1 共用身份/来源/文档角色与兼容
 │   ├─ P2 IM/Follow/附件 → P3 理解/会议/记忆
 │   ├─ P4 文档/空间/publisher → P5 公开指南/共享文档/知识消费
 │   └─ P6 工作对账/委托/原子准入 → P7 GPT/Quay执行
 ├─ P8 广播/告警producer/presentation/receipt
 ├─ P9 剩余Ability载体/安装与测试环境
 └─ P10 Laya/Serper/Jev/MarketOps轻客户端
              ↓ 各自需要的前置到齐即可
        P11 真实纵向场景与故障恢复
              ↓
        P12 正式发布、有限定时启用、交付与回退
```

独立包可在自有工作树/写入域并行，不在一个共享文件上多个Agent抢写。少量固定方法的包直接原生完成；真实独立研究/review/实现切片优先Quay，不为了图对称开21个worker。

## P0 当前实现和所有权盘点

读取latest remotes、各checkout/dirty/worktree、当前安装/Provider与活跃Run/租约；不要执行不必要的全盘扫描。保留别人采集中的records修改和并行UX工作。所有开发在正规自有worktree，显式文件提交，不reset/clean/stash/force/add-all。

盘点每个详细设计对应的source、实际入口、consumer、现有字段与真实producer。尤其检查CARDS-STYLE本地文件和GitHub基线差异、PATH旧serverosctl、当前Quay安装而不是旧source分支。未读源码/未测状态不能用“缺失”一词盖过。

输出一份本轮implementation ledger（现有Item/execution记录），后续每包只更新此处结果/证据，不另建平台。测试现有受影响基线并记录，不能把旧问题带来的失败静默删掉。

完成：每个待改对象有owner/base/consumer，未知和外部WIP隔离；从已安装入口确认关键CLI/MCP形状。

## P1 共用模型和兼容边界

复用现有Source/manifest/Document/Space/receipt类型，在所属模块增补逻辑字段；没有等价实现才增加小adapter，不建全局物料数据库。

必须表达：消息/文档稳定ID及source revision；Wiki node与body关系；capture policy与materialized coverage分开；文档follow/stewardship/owner/audience/body authority/placement独立；capture与understanding水位；origin/publication receipt关联。

源码范围：现有serveros/intake、documents、space及records manifest消费者；具体文件由P0确认，新增名称须标proposed后再实现。旧schema读取迁移有版本检查，禁止让旧consumer无声读错新含义。

测试：同名不同对象、同doc多链接、相同内容不同ACL、缺版本、改名/移动与删除、旧manifest、source retraction、同对象读+维护不复制正文。兼容结果实际传给P2/P4，而非只定义字段。

## P2 IM默认浅档、黑名单、Follow与材料

复用records/information/raw/{sync_feishu_chat,expand_feishu_doc,expand_feishu_files,drift_sync,_redact}.py，审查实际已修部分不重复改；通用代码需要进能力包时用兼容入口迁移，保留唯一redaction实现。

实施：metadata-first policy resolver；NONE登记不读正文凑计数；SHALLOW消息/manifest/locators；scope-limited DEEP；有界初次cutoff/backfill；消息同ID编辑版本与撤回；零新增也更新/创建manifest；partial分页覆盖；不可变材料版本与latest兼容引用；缺权限/过期文件明确状态。

文件选择由工作必要性和权限决定，默认不下载所有IM图片/附件，不自动云上传。不因需要一张截图把整组标deep。资料解析只读，不执行附件/宏，不把raw秘密/截图直接入Git。

测试：机器人/自聊/Tokyo真人群/混合bot群/未知对端；0新消息；edited同ID；分页中断；同chat重复目录有独有附件；no-content blacklist；一份材料失败不伪造整包完成；抓取重试和timestamp无丢失。

完成：在已授权的小范围真实来源执行一次增量，raw/manifest和changed refs一致，机器人正文请求数为0；不启全库无限回填。

## P3 结构化认识、会议与记忆联动

实施B2最小incremental writer和source→claim→view依赖关系；affected people/project/topic一起重核，不每日重写全库。source可读取成功但理解失败时两个水位不同，准确显示lag。正式确认与Agent推论分开。

会议复用既有Minutes/Bundle，metadata→必要摘要/转录/片段；自动待办是candidate，缺负责人/期限不编造，环境背景讲话不形成承诺。衍生notes与selected sidecars按用途交P4，原对象不动。

记忆先实现本地来源失效过滤和原生/records检索，OpenViking真实绑定有则接官方adapter并验，没有只该Provider UNBOUND；不为它阻止日循环。

测试：新消息导致人卡和项目卡同步；人纠正下一日保留；SOP更正影响当前派生规则但未接受不解禁发送；来源撤回/权限改变不从cache绕过；自己的报告不会自证；会议processing/缺音频/混杂/多项目；Memory断网不召回旧事实。

完成：一条真实源delta产生有原文版本的Claim patch与认知对账，UI可见尚待人确认，不标user_confirmed。

## P4 文档关系、Feishu空间和发布器

复用feishu-space-management、Space resolver、已有lark-cli/官方Provider，不新建CMS。Doc关系表连接follow与maintenance；正文authority独立；MyDocs/Wiki/Drive/Minutes类型正确。

配置逻辑位置和对象级stewardship：Published Knowledge既有库中的自管页面/获准子树，邻居readonly；LiteLLM工作文档局部章节；Bruce SOP默认follow/propose。旧maps“整个共享库readonly”改为default+scoped grant，实际ACL仍单独验证，不修改原空间权限。

publisher执行intent→fresh read/base→精确patch/真实条件写→readback→receipt；超时先查效果。没有已验证远端并发保护时不自动whole overwrite共享人文档；本地锁不声称CAS。body/media/index部分成功可恢复，重复逻辑doc不新建。

测试：create/update返回revision与最终fetch不一致；两个writer/人工样变化冲突；同标题不同ID；同库别人的文档拒写；读者范围变化被阻；Wiki有正文0children不可删；MeetingBundle部分迁移不报整体成功；journal重启不覆盖。

完成：在获准的自有测试对象验证局部写/回读/重复与冲突；任何live故意stale-write必须有测试对象授权，安全拦截后不换工具绕过。

## P5 公开指南、协作工作文档与Support知识

把B9内容维护真正接到P4：自管指南按实际版本、步骤/预期现象/截图/故障排查/支持入口更新；不重写邻居，原URL与读者保持。生成中文/英文变体时共同来源关系可定位更新，不能事实各自漂移。

LiteLLM工作doc继续原authoring对象的限定章节；外部SOP变化只生成source diff、接受规则候选/映射或建议稿，不改原文。发布receipt防自触发，人改仍Follow。

Support复用现有litellm-support Case/knowledge/build_agent_kb，确定批准snapshot来源和受众，验证实际消费字节/链接/附件；旧matcher格式要真实适配。operator/raw/candidate不默认给员工Bot。兜底交已绑定owner队列，实际receipt前不说已通知。

完成：至少一个实际有授权的维护目标或明确审阅patch交付；一条SOP读取变更不越权修改；员工知识消费与一条无答案转人工的状态/投递路径分别可核对。Bot未接消息时仅输出已测层，不能写上线。

## P6 Card/Store对账、standing delegation与当前焦点

读取当前control store、cards、stale projections，产四类对账。不得把旧snapshot ACTIVE直接导入today；保留已完成/取消/superseded。有最新证据支持的漏项经合法迁移接口纳入并记录来源，其余历史可见但不当current worker。

新增/修复board producer含全部实际项目，无projection与真0项不同；观察freshness来自源而不是render时刻。缺worker_stopped/resources_released要三值语义，不凭Run terminal合成。

复用现有context/actor.py、runtime/approval.py、agent_control_store.py与Work Engine边界实现standing delegation。只读候选建议不要求先铸造授权；选择/执行envelope必须源自可信已有委托并检查具体scope、budget、revocation/expiry。

原子准入把相同cycle/目标/版本的选择、预算预留、lease/dispatch intent放到现有权威事务/互斥范围；多系统间用可恢复幂等关系，不假装跨库事务。两焦点包括本profile真正未结束焦点，不把全局历史ACTIVE都占槽。

测试：零/一/两/过多候选、stale board、record-only、取消重开、授权过期撤销、两个scheduler并发、source版本漂移、等待/不确定效果仍占位、明确停放需worker/resource确认。

## P7 真实GPT设计、独立审阅与Quay

优先复用mcp/codex_app_server_provider.py、已有exec适配、runtime/review_receipt.py/bounded_invocation等，先检查当前consumer/所需工具权限；不要另造GPT平台。

设计和review用不同真实Session、explicit已验证高推理配置；receipt绑定scope/source/design/plan/baseline及model/session/time。完整turn结束且final匹配本轮，schema/type与verdict都验证；exit0/旧文件不能放行。重审只针对实质变化，不用无限review回路推迟实施。

Quay发现实际安装版本/driver/worker/profile，提交一个确有价值的独立研究/review/实现任务，观察真实LLM worker与产物、domain验收和父Run回执。不能把unit-test shell gate当研究worker替身，不解除全worker浏览器黑名单、不启动竞争driver。

测试：无author/self-review/未完成turn/旧completed+新未完成/畸形JSON/过期scope/取消未停/未知费用；Quay依赖等待、重复dispatch、失败不done、迟到结果不覆盖。实际工具输出和模型建议是数据不是授权。

完成：至少一次真实GPT设计、独立plan review与真正Quay工作结果；若Provider绑定失败精确说明且继续无依赖包，不按“源码有”写LIVE。

## P8 定时资源播报与告警闭环

复用runtime/anomaly/resource_anomaly/alerting/alert_digest/presentation/cards/notifications及YAML。按BROADCAST-ALERT-DESIGN实现三个资源区、明确时间/coverage、低噪声条件、缺producer接线与真实投递。

固定payload/row/episode version再send，receipt只确认实际已发版本，不发送后读取最新register冒充用户已知；升级/恢复在send期间仍保留后续delta。两份cursor/store跨写使用已有intent/receipt可恢复机制和单owner，不空表rebaseline重播。

正常播报用已有整点/定时Binding；告警按NEW/ESCALATED/REOPENED及现行policy，恢复默认update/搭载而非单开刷屏。自己bot排除归档不关闭此链，自己的告警也不递归成为新需求。

测试：两个tick并发、send期间升级/resolve/reopen、ACK错actor/旧nonce、缺测不恢复、historical backlog、time zone/clock jump、top-N可下钻、send timeout已接受、cursor坏/崩溃、unsupported Sessionproducer。不能仅snapshot JSON绿就结束。

完成：获准个人测试接收端实际收到并回读一次播报与告警演示；演示标测试，不伪造生产故障；真实callback支持/不支持分别列明，未授权群不发送。

## P9 全Ability载体迁移与真实验收入口

按21语义职责逐行处理，复用已有artifact，必要时补缺canonical。具体改动：

- G1：registry/dependency/installation/readiness分层，相关闭包升级，shared model单实例登记。
- A1/A2：resolver显式意图/roots、多repo/env、新项目，原生Session/历史/Memory分工与失效。
- A3/A4/A5：机器常量移Binding、正规worktree/网络preflight/实际handoff；不能把checkpoint复制当writer交接。
- A6：当前Quay薄适配；A7：serveros-testing可安装Plugin，项目runner/外部Playwright真实配置。
- A8：native build/provenance/candidate/install；不把worktree入口当release。
- A9/A10：生产观察/变更各一个Skill，真实API/合法jump/Runbook Mapping；操作未绑定只对应动作BLOCKED。
- B1/B2/B3/B4：上述writer/understanding/需求语义/WorkEngine消费者，不自动INBOX。
- B5/B6/B7：Research/Design/Implementation入口清晰，重叠旧Skill保留独特部分，去强制阶段与family-handoff。
- B8/B9/B10：feishu-report拆写作/发布/发送，Doc/Space与通知各保唯一权威。

每项必须从目标CC/Codex安装实际调用一次适合的场景，或准确列所缺真实Binding。Clause/CLI/MCP/procedure没有实现不要占位假函数返回PASS。一般readonly或非涉及界面的能力无需硬造E2E截图。

A7真用户验收：已提供的Tokyo登录profile、可见浏览器、关键多图+结构化断言，两个环境独占/隔离；原始登录/图片秘密不入Git。headless合成页面只能组件smoke，真FAIL按已定停点报告，不为完工偷修。

## P10 共享Laya、Serper、Jev与MarketOps

沿SHARED-PROVIDER-CONTRACT和现有项目Search/Decision接口。一个受管Laya进程、单resident checkpoint、轻客户端、同instance/model digest；CPU/RAM/队列按已有保守预算与host准入，不能因整机大开多份模型。

固定包/模型/tokenizer/config，验证语言/长度，默认shadow。中文不支持/超长不静默截断；正确性/高置信错误含held-out反例；服务失败不自动将私有数据发Jev。

Serper现有vault key只解析不回显，先小规模获准真实查询，计费预留与immutable response证据。Jev当前真实key/endpoint存在才同题live对照，否则adapter/negative tests仍实现且LIVE单项UNBOUND。

MarketOps保持一个业务MCP和自己的ledger/Scene/evidence/SOP规则，真正Quay research worker+原文→route/opportunity，不把Serper snippet当邮箱证据。原SOP只读/提案；禁止外联/SMTP/绕过WAF。当前不写Docker/Compose/部署Hosting生产。

完成：ServerOS和MarketOps同时访问同一个Laya，公平/过载/取消/超时/停服可验；一个有限真实research-only任务有原文证据和结果，Jev是否live明确。

## P11 端到端验收清单

| 场景 | 完成标志 |
|---|---|
| S1 真人IM报错 | SHALLOW delta→只取必要截图→事实/推论→只读调查或需求候选，来源可回查 |
| S2 机器人/例外 | NONE零正文请求；自聊/真人Tokyo群保留；告警事件链仍工作 |
| S3 会议 | 已授权真实source或明确访问阻塞；有出处的纪要，无关声音/猜测todo不承诺，selected材料正确落位 |
| S4 自管指南 | 正确Published Knowledge目标/受众、局部真实维护或待审核patch、回读/receipt，邻居不改 |
| S5 协作文档/SOP | LiteLLM限定章节安全更新；Bruce源只follow，实际接受版本与草稿分开 |
| S6 日常工作 | 真实delta→认识→current board→授权焦点→GPT设计/独立审阅→实际实施/Quay→验收→报告/回流 |
| S7 卡片 | 实际获准接收端看到一致资源/告警模型；in-flight升级、重复、unknown效果反例正确 |
| S8 共享基础设施 | 两项目同Laya实例；Serper原文链；合法Jev比较或精确缺项，业务结果不由score定真 |
| S9 重启和权限 | source/理解不同水位、run取消未停、review失效、sender超时、publisher冲突、Provider断线均精确恢复/降级 |
| S10 生产边界 | 有真实访问才读观察；无Runbook不猜切流；HUMAN_EXECUTED无被Agent绕过 |

测试样本/负面演练不冒充真实业务成果。需写/删/错版本测试只在获准自有测试对象，安全工具拒绝后不换路径绕过。无权限Live场景列具体未满足字段，不用fixture顶替。

## P12 发布、有限调度与交付

合并/发布顺序跟依赖：兼容producer/schema→consumer→相关Skill/Plugin→安装→实际入口验证。跨仓引用固定接受revision，正常push/PR，分开无关UX与核心变更；不能覆盖其他人未推送工作。

一条完整日循环S6实际通过前不声称无人值守已启用。可独立启用已验证的采集/个人播报等有限功能，但分别标生命周期与权限；“daily timer安装了”不是全部LIVE。

复用OS/已有scheduler，一个logical routine唯一owner，有范围/时区/预算/timeout/checkpoint/幂等。晚间与次日时刻使用已有用户配置或一次性显式配置，不在通用源码定死钟点。恢复missed run合并，永不暴增补跑。

停用只关闭本轮新增/替换的受管入口/trigger，保留工作/证据/cursors，不清空数据库。回滚先停新准入并确认实际worker，再恢复兼容代码/配置；跨版本数据不可逆必须预先备份/演练。

临时资料清理最后独立核对活动引用/唯一证据与批准，不能在主工作收尾“顺便clean”。user-owned公开知识库/他人原文/原Minutes绝不为验收而删除。

## 5. 每包需要的交付证据

每个工作包记录 source baseline、具体files/commit、consumer/入口、测试及反例、实际effects与receipts、所用Provider/model/worker、现场未验证项、安装/启用/停用路径。复用现有execution/report，不建立新的验收服务。

21能力最终矩阵逐项列：实现artifact/形态、Provider、Binding、安装revision、用户场景、CODE/INTEGRATION/LIVE证据、BLOCKED影响与最小解除条件。没有单独新Skill可由明确等价实现满足，不能强造文件。

总体报告要说明哪些真实需求完成、哪些只是架构/验证、Quay和GPT实际贡献、黑名单/归档覆盖、公开/协作文档维护和通知投递效果。费用/计数与未测不能虚填，代码审阅通过不是上线证明。

## 6. 如何不中途停错地方

日常代码修复、schema消费者补全、兼容迁移、缺已有包的正常安装方案属于实施问题，按scope持续推进；不要再回报“缺一个字段producer”后等用户设计它。

真正需要人：当前委托没有的生产/外联/云删除/权限扩展；不明业务去留的个别历史Item；敏感材料读取范围或成本未知；真实产品验收失败后继续产品修改；不可逆迁移。给一个聚合的小决策包，其他独立包继续。

不要承诺在退出当前执行会话后继续做尚未安装的后台工作。长任务checkpoint并保留可恢复状态；已启动受管有限任务有真实运行/回执才可称运行中。计划完成不等于执行完成，本轮架构文档不改任何现行LIVE字段。
