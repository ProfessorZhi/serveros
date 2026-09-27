# CC 实施主计划：目标架构 → 真实消费者 → 安装 → 目录收口

2026-09-25，依据Tokyo只读现场复核修订。仍使用唯一P0–P12计划；本页替换旧实施建议，不另建路线图或任务数据库。**本次交付是计划，未实施、未安装、未启用定时器。**

## 0. 执行入口与完成定义

后续用户明确要求实施时，CC先读本页和[短交接提示](CC-IMPLEMENTATION-HANDOFF.md)，按需读[现场基线](IMPLEMENTATION-BASELINE-20260925.md)及相关架构章节。不要把整棵Ability Tree、所有Skill或全部设计预先装入上下文。

目标不是重写ServerOS，而是让现有实现满足已对齐产品：Native Claude/Codex主脑；ServerOS Plugin提供能力入口；两个MCP适配器连接真实Core/Provider；`serverosd`保持唯一常驻Node Core；OS/既有执行器唤醒有限工作；G1、A1–A10、B1–B10按任务组合。

“完成”必须同时交付：已接受范围的源码及兼容迁移；正常构建与版本锁定；Tokyo Node Runtime及Claude/Codex正常入口安装；真实信息、工作、知识、能力、运行五条循环的适用场景；获准定时工作真正接续；目录消费者切换与保留例外；可执行回退。仅代码、测试数、PR合并或`current`链接变更都不能封板。

所有阶段保留CODE / CANDIDATE / INSTALLED / RUNNING / SCENARIO_VERIFIED事实。必需项被权限、认证或资源阻断时明确PARTIAL/BLOCKED及解除条件，继续其他独立工作，不删验收项后称全完成。

## 1. 先固定正确基线，不能覆盖现用修复

| 来源 | 本轮观察版本 | 实施要求 |
|---|---|---|
| ServerOS远端main | `c5d118cc44629e7cb264b73e3026d8ac3abbf7dc` | 实施时重新fetch；本机canonical main不是最新代码 |
| ServerOS架构PR #6 | `13f2819af52e7e1df8bcb81c0b28eee2814e3b2b` | 本计划继承此架构；PR未合并前不能称main含目标设计 |
| Ability架构PR #4 | `5c1963e401fff7766327938dad97549633ec9673` | 接受/合并此配套设计后再锁实际artifact revision |
| 已安装Linux Work | `alert-card-scope-20260924@8ce6fe7`，`3.1.0+srv0003.20260924c` | 比main多`dbc9144`、`8ce6fe7`两笔告警修复，必须保留其行为 |
| records | `7368e298fe8c07f9289aeadc7fb4016f0fcd2995` | 已有source声明/采集工具，复用；不重抓全部历史 |
| ops本地 / 远端 | `a8272d4` / `1993042` | 本地正在执行定时脚本；远端改了maps和gateway Binding，禁止直接pull活动checkout |
| work-engine本地 / 远端 | `f6be338` / `a0f370f` | 本次差异仅README，但实施仍锁实际源/安装/数据schema |

先接受两份架构PR和本计划，再把现用告警修复非force集成到实施基线；已被后续main包含的提交不重复cherry-pick。PR号/分支不是永久版本，最终记录完整SHA与依赖关系。实施从最新已接受基线新建自己的worktree；不在canonical或现有SRV/LiteLLM/MarketOps工作树施工。

本机结构以实测host profile为准：`DATA_ROOT=/data/home/vince`，`WORKSPACE_ROOT=/data/home/vince/workspace`；产物、缓存、venv、模型、临时文件留`/data`，不能套用北京路径。详细现场证据和观察限制见基线文档。

## 2. 实施责任、权限与顺序

沿现有B3/B4创建或解析一个被接受的ServerOS交付Requirement及唯一Work Item；阶段是它的计划，不是13个新需求。独立worker/长执行/效果恢复使用有意义Run；普通同步编辑用工作项、Git revision和必要证据即可。预算/Session结束保存待完成项、实际child/effect、准确基线与下一动作。

当前请求只授权写计划。后续“按计划实施并安装”可承接明确的自有源码和开发安装范围，但不自动包含生产切流、共享网关重启、客户联系、新数据外发、删除历史目录或全库改ACL。从现有可信授权解析所需项目、写域、测试对象/受众、预算、窗口；缺失项一次聚合，未受影响工作先做。

已安装CLAUDE/AGENTS仍有旧强流程。先区分业务提示、真实权限与实例事实；迁移经接受的canonical指引再渲染安装，不能让新提示词覆盖更高层实际保护。真实产品验收unexpected FAIL仍留证并停该产品自动修改；实施阶段局部编译/单测/adapter错误在既有scope内修复，不每步追问。

保护`container0-litellm`、`container0-postgres`、`browser-stack`、LiteLLM reasoning预留环境、Support Bot、MarketOps、所有他人WIP、数据库/卷、current/previous与唯一证据。SRV-0011的三个测试daemon是已知历史路径，**不是按PID可杀的垃圾**；重新核owner/lease/socket/consumer/effect后才能单独处置。只读观测不意味着资源清理已获准。

```text
P0 现场/容量/基线/权限
 ├─ P9先修全局入口与装载阻塞（不先全面改Skill）
 ├─ P1 source/identity → P2增量采集 → P3真实理解
 │                                  └→ P4正文/资产 → P5长期知识维护
 ├─ P6工作准入 → P7真实Parent/child/恢复
 ├─ P8监控+现有ops发送端（保留已装告警修复）
 └─ P10共享Provider/Memory
       → P9候选构建与终端隔离验收
       → P11真实用户路径
       → P12获准安装/切换/定时启用/目录收口/回退演练
```

P0–P12是本次实施依赖，不是产品对每个日常任务施加的流水线。安装前可在隔离候选运行P11；P12后必须再跑已安装入口的关键回归，不能等安装后第一次发现缺模块。

## P0 现场复核、写入预算和隔离

本轮`/`使用99%、约500MB可用，`/data`约3.6TB可用，vince XFS已用82.9G/硬限500G。实施前重测space/inode/quota，按候选构建、依赖、备份与验证的峰值预算判断是否安全。默认不在`/tmp`、系统盘cache或HOME错误分支做大写入；系统盘问题先归因、给owner处置建议，不擅自删系统日志/Docker/他人目录。容量不足只挡对应大写入/激活，允许安全只读与小型`/data`源码工作。

冻结真实host roots、Git分支/dirty/未推送refs、unit/CLI/Plugin/MCP/入口指令、触发器、受管数据库/游标/注册表的基线。只收必要非秘密字段；配置备份放受控`/data`目录，不入Git。活跃SQLite用一致性backup，不裸拷贝主文件忽略WAL。**退出**：下一个切片的代码基线、writer、目录、预算和回退点可明确，不要求全机所有UNKNOWN先变绿。

## P1 身份、source与版本适配

**复用**：`intake/source_policy.py`、`material_index.py`、`source_waterline.py`、`documents/document_governance.py`，records的`information/sources`、raw manifest及`_redact.py`。source声明与观测不同权威；不再建source/Claim数据库。

**改动**：让IM编辑/转发包多材料、Doc/Wiki精确revision、Meeting各成员、Web snapshot都保留稳定身份、允许范围、实际coverage及缺口。material可N:M关联；来源更新、parser变更、ACL变化分别处理。保留`publish-excluded`与大媒体外置的独立判据；不得把private Git视为任意数据可上传。

**验收**：`test_source_policy.py`、`test_source_waterline.py`及真实records小样；旧manifest兼容，多附件不覆盖，同名/跨ACL不误合，缺失不是零。新字段必须被实际reader/publisher消费；迁移先dry-run比较，再对获准数据局部执行，禁止全量重写历史。

## P2 IM / Follow / Web / 会议的有限采集

**复用**：records `information/raw/{sync_feishu_chat,expand_feishu_doc,expand_feishu_files,drift_sync}.py`和现有redaction；ServerOS `capture_coverage.py`、`watch_coverage.py`、`material_index.py`；`space/`会议解析与既有Provider。

**改动**：provider事件可用就用，否则有限poll；NONE零正文、普通SHALLOW、已接受重点范围DEEP_REQUIRED。重跑只补未完成页/材料；网页登录壳/截断不算全文。Follow限定对象/子树，不因一次读或收藏自动扩大订阅。

会议结束→已知Bundle→转录PROCESSING/附件pending→有界复查→材料到达→只触发受影响reading。已有合法transcript可先读，不等MP3或全套成员。原生Minutes保持原对象；Drive只放获准文件/导出及关系。support远控背景音不生业务Claim。

**验收/交付**：真实小范围IM编辑/转发附件、一个Follow revision、一个会议晚到材料，raw→receiver闭合；无变化不调用LLM/新发通知。`test_capture_coverage.py`、`test_meeting_bundle.py`、`test_meeting_relevance.py`及records既有测试按存在的版本选用。只取得source不算理解或云镜像完成。

## P3 原生reading接现有semantic writer

**复用**：`understanding_brief.py`、`understanding_semantic.py`、`understanding_patch.py`、`claim_recall.py`和分源水位。writer不调用模型是正确分工，不补第二writer/understandingd。

**改动**：固定source版本→原生Agent实际阅读→可信adapter注入actor/委托→原writer校验/提交→四类Current View→A2资格失效。事实/有支持推论可在委托内自动reconcile；目标/负责人/期限/承诺/权限变化仍交真实接受。不能让reading中的`grant`或模型自报major-change成为权限事实。

修`runtime/recipes.py:perception`的旧耦合：单个source未答不能阻断所有成功源；先持久化各源capture，再分别推进understanding；不能把“source变了”直接解释为“新Item候选”。保留原有限routine记录/锁/预算，补consumer而非新工作流框架。

**验收**：真实IM/Doc/会议来源各有一个reading及可核支持；Claim成功/View失败只补View；retraction立即影响当前recall，即使OpenViking掉线。执行`test_understanding_brief.py`、`test_understanding_semantic.py`、`test_claim_recall.py`、`test_source_waterline.py`及J03；引用范围合法不替代语义正确性。

## P4 飞书正文 / Drive / Records三端交付

**复用**：`documents/document_governance.py`、`intake/external_reference.py`、现有B9 `document-stewardship` / `feishu-space-management`脚本、lark-cli/原生API。文档identity resolver存在不等于远端publisher已闭环；逐个真实入口核验后补最薄adapter。

**改动**：FOLLOW_MIRROR→既有外部文档；EXTERNAL_MAINTAINED→原共享对象获准章节；OWNED→MyDocs/个人笔记/公开文档。沿既有manifest表达body_authority/sync_mode/write_policy/Style/material关系；local checkout与GitHub是一份Records权威的物化，不是三主同步。

先查logical ID与上次receipt，读取fresh base/current，再做可保护人改的scoped patch和readback。unknown block/富表/图片不得经Markdown往返丢掉；没有可靠条件写时不得以本地锁自称全局安全。正文、媒体、索引、Git、送达分别记录；timeout先查同一effect，不能盲重建。

**验收**：J04/REF场景中至少一个真实OWNED和一个获准mirror/共享章节；重复不双份，人改保留，权限不扩大，body成功/media失败只补缺边。大音视频留Drive/受控archive，Git只留获准文本和metadata；不批量移动历史云对象。

## P5 长期知识维护、报告与真实接收者

**复用**：B8 `feishu-report`的可用行文/发布组件、`runtime/report_publication.py`、`daily_report.py`与现有B9/Support knowledge consumer。先核Support双仓真实readme与owner，不顺便接管其产品路线。

**改动**：Follow→局部理解→standing stewardship复核允许区块→patch/readback；外部建议不能自动变内部政策。日报/周报从当前accepted Outcome/evidence聚合，同周期/用途/受众只有一个logical report；不重跑E2E取图，不把子Task数当产出，不因日报cutoff停止夜间Focus。

发布→真实sender/知识consumer接收；有outbox但没有消费者只称待续。源撤权先阻断当前传播，历史合法保留另按retention；own publish receipt只抑制同revision自循环，不能吞后续人改。

**验收**：一个收藏/Follow增量、一个指南scoped维护、一个周期报告完成适用的compose/publish/send；J04/J10/REF01–REF12。没要求通知不强发；要求“发给我”只有draft不能关闭；Bot导出包存在不等于Bot已切版本。

## P6 Requirement / Work Item / Focus的真实join

**复用**：`work-engine`已有Item/Run/store，`intake/workchain.py`、`focus_source.py`、`daily_cycle.py`、`runtime/agent_control_store.py`和`context/delegation.py`。Card给接受目标/AC，Work Engine给工作控制，Agent Control给可信grant，executor/lease给实际占用；不把这些动态权威塞Card独立维护。

**改动**：正式Requirement幂等对应一个canonical Work Item；草稿不建项，新证据不改目标。完成边界/接受版本与真实evidence联结。把`focus_source.NOT_PRODUCED`逐项变成从正确owner取得或精确UNKNOWN，不硬编码“无worker/资源已释放/默认有grant”。

拆开“推荐候选”与“已接受Focus接续”；`daily_cycle.select_focus`当前会按空槽选候选，不能直接当自动接续器。0是合法当前数量；DELIVERED/待验收是否释放责任取决于required结果和实际effects，不单凭旧enum。无priority不默认为P0，也不阻断人已选且合法的任务。

**验收**：无新选择时完成后Focus=0；两次触发只一次准入；更正/撤销在effects前生效；旧Run终态但worker仍写不得释放；park/cancel核实际资源与checkpoint。`test_focus_source.py`、`test_daily_cycle.py`、`test_admission.py`及work-engine对应CLI/store测试；首次真实grant走可信用户控制，不能让fixture值进入live。

## P7 原生Parent、委派与真正自动接续

**复用**：`runtime/agent_sessions.py`、`bounded_invocation.py`、`review_receipt.py`、`recipes.py`，`fleet/agent_control_router.py`及已验证的Codex/Quay Provider。把现有逻辑接通，不重写Quay调度、不建立第二Session状态库。

**先修实际死锁**：`recipes.work`当前在派发之前用worktree历史差异判断进展，NONE即NO_PROGRESS；新任务尚未执行、本来没有历史产出，因此可能永远无法开始。准入检查应回答能否开始，进展观察应在真实启动后的有界窗口回答是否卡住；no-code任务也不强制需要Git差异/代码工作树。

Focus接受→确保唯一logical Parent→完成当前授权步骤→等待child/依赖则写checkpoint并退出→实际事件/有界poll唤醒后继Parent→查原attempt并接收一次。输入冻结accepted revision、效果/预算、确切工作树、receiver；Parent变化不能复制Task或继承旧授权文本。需要跨设备时先做有界子问题，私网/身份/结果回传分别验证。

简单任务原生直达；复杂度/风险值得时才用GPT设计、独立review或Quay。评审要原始证据与准确输入，不能以APPROVE铸造grant。Quay本次至少用一个真实有价值切片验证集成，不把这一验收要求变成未来每日调用配额。

**验收**：`test_agent_mcp.py`、`test_codex_app_server_provider.py`、`test_recipes.py`及J06/J07：新任务零历史进展也能启动；UI断线不取消worker；Parent替换后原child自动接回；重复触发不双派；一项等待不冻其他合法scope；unknown effect先查原状态。`scripts/live_closure.py --yes`会真实驱动Agent，必须分配本任务自己的Agent/预算，禁止借现存会话默认执行。

## P8 监控真相、每日一张卡与事故接续

**先保现用修复**：集成`dbc9144`/`8ce6fe7`或已包含它们的后继提交，保`test_alert_digest.py`、`test_attention.py`回归。当前ops sender已服从`decision.send`；不得重复把“没有任何空摘要抑制”当未实现事实。

**改动位置**：`runtime/{alert_digest,card_delivery,card_source,cards,presentation,alerting}.py`及对应合同；ops `scripts/tokyo-monitor/{monitor_card,alert_digest_card}.py`与实例Binding/units。旧status采用小时key、send、新卡及09–22静默规则，不等于目标08–20工作日一张卡。复用真实传输，按当日稳定identity创建一次并patch；前序compact历史下沉。同一Incident跨天保episode/message关系，不按每5分钟或每小时新发。

统一官方CN工作日/调休日历的来源、年份与更新方式；缺当前日历时明确配置缺口，不伪造Mon–Fri为官方日历。状态健康由观测/覆盖/影响决定；无人动作不假绿，诊断数量不假黄；unknown/恢复/本周期变化与历史累计分开。恢复只能沿真实注册授权执行；不得因计划存在就把`reconcile_enabled`切为true。

**验收**：M1–M6、V01–V44中适用反例，特别daily message ID复用、off-window无routine新卡、P1独立、空Incident不发、sender timeout读回、Core失明仍有独立观察。只在获准测试接收端发消息，合成UI不是原生客户端验收。既有timer只在受控切换时替换其消费者，不能旁边再起第二套通知任务。

## P9 载体、入口指令与候选包

**早期切片**：先迁真正卡住普通任务的源指引：机器policy模板/渲染、work-management、information-management、dev-doctor YAML、vision、session-manager、report、MUC凭据输出示例。保非干扰/秘密/生产保护，清掉“每producer必有Run”“每云写必全量proposal”“Meeting成员必须齐全”“Codex未安装”的失效指导。

**实现规则**：G1.4按需authoring；原生够用不新造Skill。description精准，root按当前模式展开，不无条件读所有reference或强制全套research/review/test。metadata长度仅信号；安全边界实际由谁消费要验证。旧正确规则不能仅为lint/token数删除。全部21职责对照[现场职责映射](IMPLEMENTATION-BASELINE-20260925.md#5-21项责任的实施落点)，共用MCP/CLI/Provider，不要求物理载体对称。

**构建/安装接缝**：`plugin/bundle.yaml`、`plugin/profiles/linux-work.yaml`、`scripts/build.py`与pack/manifest声明共同锁完整Ability SHA；更新真正改变的cache identity，不复用旧版本字符串承载新字节。两个ServerOS MCP已存在，node面转发daemon事实，agent面调用真实控制/Provider；普通原生工具/外部读取不必绕daemon，不把所有Ability变成Core业务代码。

当前Node Runtime已装并运行，但检查到的Claude用户安装/启用列表只见ServerOS Testing，Codex配置没有两个ServerOS MCP；marketplace路径存在和历史skill usage不证明fresh加载。分别建立当前版本支持的Claude plugin、Codex plugin或MCP入口；先在隔离profile测试，再获准写正式配置，保留用户其他插件/权限/模型设置。共享部署字节不等于每个会话应加载所有Skill/schema。

**验收**：`test_build.py`、node/agent MCP测试、真实fresh Claude和Codex分别证明bootstrap可发现、两个MCP能连正确daemon、选中能力可读/可执行、可继续查未列入shortlist的能力、受保护效果拒绝。记录source pin、artifact tree hash、cache、enabled、actual runtime和场景结果，不用一行“已安装”替代。现有原生hook确切支持才采用，不臆造热卸载或协议参数。

## P10 共享Provider与跨会话Memory

**Laya复用优先**：现有用户级`laya-shared.service`实际在线，但执行依赖`toolchains/marketops-mvp`及canonical Ability源码cwd。先核实际消费者、版本与兼容，在授权窗口切到独立稳定Provider环境/包；不重下第二份模型、不并发起第二实例、不因调整ServerOS切坏MarketOps。已有remote新提交先检查，不以旧审计假设重做adapter。

**OpenViking**：目标共享Provider尚无本轮已验证部署。先核官方当前服务/认证/native CC与Codex集成版本，冻结license/revision/model成本与capture数据范围。将`memory/provider-contract.yaml`旧“不得有新服务/完全可由Layer B重建/没有全局Provider”假设和真实adapter/测试一起迁移；保住当前业务权威、daemon无Memory启动/tick依赖。

> **已完成，2026-09-27。** 这段的每一项都落地了，逐条对账：部署了（OpenViking 0.4.21，独立 `openviking.service`，`127.0.0.1:1933`，本地 embedder，无远端调用）；认证与成本冻结了（
> `auth_mode: api_key` 显式写死，凭据只在 vault，`cors_origins: []`）；license/revision 记在 R11；capture 数据范围**故意未开**——未配 VLM，所以 provider 可检索不自动采集。
> 合同已迁到 v2：那三条假设换成 12 条采用条件，「保住」的两条按原样留在 v1 的位置上（daemon 独立＝条件 1、2；业务权威＝条件 3、4）。
> adapter 与测试同改，`test_memory_provider.py` 的逐字断言把转写钉在合同上（一条条件的文本被改就会被咬住）。
> **一处没有做的**：没有 consumer。所以「current records 覆盖 retrieved memory」现在是空真而不是被执行的规则，这条在 consumer 落地时必须重验。

OpenViking单独私网服务与持久数据/一致性备份，设备及项目ACL隔离；Git只放获准记录/语义与安全metadata，不放索引/数据库/凭据。优先上游SDK/HTTP/MCP/hooks组合；若版本不支持精确capture过滤或关闭Skill catalog，则禁用该自动注入/捕获路径，采用有界显式输入，不能为“原生集成”默认上传全量聊天。一个用户namespace不等于跨项目任意可读。

**验收**：一个真实ServerOS用途及允许协调的第二实际consumer访问同一Laya实例；OpenViking用获准非敏感小样在两次fresh native Session capture→recall，后继会话能找回经验但以当前Claim纠正为准；无权项目不返回，撤销立即挡当前读取；停Provider不阻断records/checkpoint，备份恢复有证据。跨Windows验证需对应私网/身份授权，缺少时标该层未验，不能伪造跨设备完成。

Serper/Jev/MetaCC复用实际Provider，区分历史搜索、长期经验和当前事实。可选Provider缺失只降级无关任务；但被本需求明确纳入的Memory/真实consumer集成仍是未完义务，不因可降级就删掉完成条件。不要把本次ServerOS实施扩大成MarketOps完整MVP或Support Bot重建。

## P11 验收的是用户结果，不是文件数

沿用[Scenario Acceptance](SCENARIO-ACCEPTANCE.md)、[Monitoring Acceptance](MONITORING-ACCEPTANCE.md)、REF场景及G1 authoring规则。小改只测相关范围；候选集成和安装边界再跑组合验证。重大control/effect/数据迁移安排独立review，输入含实际代码/证据而非仅作者摘要；review通过不是运行权限。

| 验收链 | 正向必须发生 | 恢复/反例必须发生 |
|---|---|---|
| 信息循环 | 实际获准IM/Doc/会议增量→原生reading→原writer→View/recall | 部分材料晚到、撤回、某源失败，其他源不丢；无变化不叫LLM |
| 工作循环 | 接受目标→唯一Work Item/Focus→Parent真实推进→required结果 | 零历史产出能开始；child回收/Parent替换不双派；完成后0不补位 |
| 知识循环 | Follow/OWNED/共享章节按正确关系维护→readback→合规Git | 人改、unknown block、partial媒体/Git/send只补缺边，无第二正文 |
| 能力循环 | 已有工具复用或必要候选→包→fresh CC/Codex真实选择/调用 | 无关Skill不注入，两个MCP正确连接，权限/来源/pin不漂移 |
| 运行循环 | 真资源/覆盖/影响→工作日daily卡及独立Incident | 无空新事故、不假绿/假黄；独立Core失明观察，sender失败不改健康 |
| 跨会话Memory | 同一私有Provider里合法经验可跨Session召回 | 旧决定被纠正、项目无权、Provider停服/恢复，不从Git重建活跃grant |

真实场景预先固定目标、scope、版本、oracle、受众和effects；合成故障只在隔离测试资源。正常路径必须少一次人工搬资料/说“继续”，不能只测试“会安全拒绝”。记录每个真实人工停点的原因，不引入新的计分/审批平台。所需认证/效果未授权记BLOCKED，未跑记NOT_RUN，不以fixture标LIVE。

## P12 安装、有限启用与目录收口

### 12.1 候选准备：绝不覆盖live current

源变更经review正常合并，记录ServerOS/Ability/Work Engine/ops/records各自适用SHA；新代码必须保现用`dbc9144`/`8ce6fe7`修复。候选构建使用自己的artifact目录与完整Ability SHA，验证两次相同输入产生相同成员/内容；所有加载依赖包括intake/documents/fleet均应在包内。模型数据、临时文件和用户状态不进不可变artifact。

构建/校验示例仅说明本轮已读取的CLI形状；执行前重核最新`--help`，变量由本任务真实记录赋值，未设置就停止，不猜实例路径：

```bash
# 只写本任务候选；$CANDIDATE、$MARKETPLACE必须在自己的artifact root内
python3 scripts/build.py --write --bundle plugin/profiles/linux-work.yaml \
  --ability-repo "$ABILITY_REPO" --ability-rev "$ABILITY_SHA" \
  --output "$CANDIDATE" --marketplace-path "$MARKETPLACE"
python3 scripts/build.py --check --bundle plugin/profiles/linux-work.yaml \
  --ability-repo "$ABILITY_REPO" --ability-rev "$ABILITY_SHA" \
  --output "$CANDIDATE" --marketplace-path "$MARKETPLACE"
# 指向隔离状态，验证候选而不stage/swap；Plugin kind也单独验证
python3 scripts/release.py promote --dry-run --artifact "$CANDIDATE" \
  --revision "$RELEASE_ID" --runtime-root "$CANDIDATE_DIST" \
  --composition serveros-linux-work --kind node-runtime \
  --state-dir "$TEST_STATE" --unit-dir "$TEST_UNITS" --bin-dir "$TEST_BIN"
```

`--no-reload`**不是dry-run**：正常promote仍写revision、切链接、写unit/CLI。`promote`的daemon-reload也**不是restart**，不代表现存进程加载新版。`daily-task`当前只装workbench render；不能称它是完整日循环安装器。`alert-worker`默认可能启用timer，不能盲跑造成第二sender。

### 12.2 获准切换：Node、Claude、Codex分别核验

激活前再读容量/owner/进行中attempt/授权；记录旧current/previous、unit/drop-in、CLI链接、Plugin版本/cache/enable、scheduler状态、受管store schema及一致性backup。只暂停本次影响的写者/发送器，保留独立Core健康可见性；其他项目和共享网关不跟随停机。

沿release机制stage→verify→切`serveros-linux-work/current`→渲染准确state base→受控daemon-reload及**明确获准的目标user unit restart**。核新PID/启动时间、实际文件/版本、socket、持续tick和调用结果；只有daemon响应不能证明全部业务完成。用户bus需正确`XDG_RUNTIME_DIR`/`DBUS_SESSION_BUS_ADDRESS`，不能把远程shell缺bus误判daemon宕机。

Claude安装对应新composition并核cache与enabled记录；Codex按其实际版本支持的plugin/MCP方式登记，分别fresh Session读取。不能只修改marketplace目录或复制Skills就宣称安装完成；不能用一次性临时覆盖config的成功替代日常入口。测试ServerOS Node MCP读实际daemon、Agent MCP遵守授权、必要Skill按需发现、历史用户插件/模型配置仍有效。

A2/Laya等外部Provider独立部署/切换；其健康/认证/质量和数据迁移不挂在serverosd启动路径。相同候选跨Node/Plugin部分成功要明确版本组合；发现接口不兼容就回退受影响consumer，不让Node新、Plugin旧的混合状态长期隐身。

### 12.3 真正启用日常自动化

先登记真实source/receiver、预算、时区/业务日历、独占trigger owner、幂等key、checkpoint和暂停/撤销方式；在受控小样跑通对应Journey后才启用该有限routine。正常启动要能在没有用户第二条“继续”消息时接下一步。没有回调支持就用有限poll，不自造常驻workflowd。

逐项启用：B1增量/Follow/会议pending复查；B2 reading→writer；晚间catch-up与20:00对齐提醒；只接续已接受Focus的Parent；需要时的B9维护/B8报告；工作日daily卡和独立incident。各自无变化可安静退出；一项失败只保该范围lag，不重抓已完成数据，不重复Task/消息。

现有`tokyo-status-card.timer`/`tokyo-alert-digest.timer`必须与新消费者协调切换：保持同一责任owner，替换原sender行为而非同时启用两套。完整日循环不能由一次render或timer active证明；至少验证实际一次触发、接收、效果回读、重放不重复以及新Session接续。计划中的时间不自动取得云写/通知权限。

### 12.4 目录收口按consumer证明，不按名字清空

| 对象 | 收口方式 | 保留/停止条件 |
|---|---|---|
| canonical源码及并行worktree | 正规Git集成，新工作只在自有tree；提交/PR关系可追 | dirty/未推送ref/活跃owner不动；不得reset/clean/stash或force |
| LinuxWork revisions/current/previous | 不可变候选、准确链接与回退版本，校验fresh运行consumer | 保current、previous、在途安装与实际回退依赖 |
| 旧`runtime-assets/hwz-server-os/current` | 先查Plugin/CLI/MCP/marketplace/unit全部引用再提迁移 | 它当前是实目录，不可凭“旧core”名称删除 |
| 三个SRV-0011测试daemon与产物 | 核业务owner/lease/socket/打开文件/实际影响，明确停止方案 | 历史PID/Run终态不授权kill；unknown effect/唯一证据先保留 |
| ops脚本与Laya执行环境 | 从独立worktree准备稳定发布，再切真实unit/sender consumer | 活动ops checkout不能直接pull；共享Laya不能跟随清理失效 |
| 原生Skills/Plugin cache/旧备份 | 比对canonical revision、安装清单、实际加载与回退需求 | 不批量删除`.claude/skills`/`.codex/skills`历史备份，不手改派生物反写源 |
| records、state、DB、资产 | 保权威原位，备份/迁移验证，修resolver与locator而非重造同义树 | serveros.db/WAL、游标、回执、原始材料不得因“收敛目录”清空或入Git |
| 飞书历史对象 | 新对象自动正确落位，已有对象按接受范围局部迁移/readback | 不批量重排空间、删原Minutes或复制共享正文；需独立权限 |

每个候选目录执行：相关inventory→明确意图/权限→stage/copy→内容/权限校验→切全部实际consumer→fresh验证→满足retention后才退休旧路径。可证明的正常保留/DEFER是合法结果；缺producer/consumer却改名“保留例外”不能掩盖未完成。

### 12.5 回退必须覆盖代码、配置、运行与语义

先停止本次新增派发/发送，查在途worker与unknown effects；不重放外部请求。回退到记录的兼容release，恢复对应unit/CLI/Plugin配置并重启获准目标，重新查daemon和fresh终端；恢复旧timer前确认新owner已停。`release.py rollback`的支持范围按当前源码验证，不把它当所有数据/Provider都自动可逆。

数据库/文档schema和已发生云效果单独处理：代码回退不能直接用旧state快照覆盖新grant撤销、已发送消息、游标与人改。预先写兼容/迁移反向路径，不可安全降级则停止相关写入并交具体恢复决定；保留当前日志、原始source和已接受结果。OpenViking/Laya用各自版本及持久化恢复，不复制索引进Git。

## 3. CC逐片执行与最终交付

每个切片按“最新accepted baseline→复用/最小改动→相关验证→真实接收→commit/checkpoint”推进。阶段是计划而非强制业务状态；同文件/环境冲突只隔离该片。不需要每片重新全量研究、独立大review或跑全库测试。

一次性交付给用户：

- 各仓最终commit、包的ServerOS/Ability pins及tree hash，当前/previous、实际Node PID/版本、Claude/Codex装载证明；
- 21职责各自真实入口/receiver/有效证据/仍待身份或权限的层，按五条循环汇总，不以目录计完成度；
- 实际启用的source、routine、触发owner、日历/窗口、最后有效结果和安全暂停方法；
- 云文档/报告/通知真实readback，Memory/Laya实际consumer及失败降级，不把源码样例当LIVE；
- 目录迁移前后locator、实际引用切换、删除权限与保留理由，其他并行工作未被改动；
- 回退验证及剩余必需项。仍有required项未满足则报告PARTIAL，不用“只差部署/只差consumer”当完成。

**启动委托不是本页自动执行**：本轮仅在GitHub交付计划。实施、安装、迁移和启用由后续明确委托触发；无认证/预算/权限的效果不得从本页推导授权。整个计划未改变生产人执行、共享WIP和秘密边界。

## 4. 2026-09-27 实测复核：§1 各对象的现况

§1 那张表是 **2026-09-25 计划时**的读数，本节的对应行是 **2026-09-27 14:13（北京时间）** 的实测。
两者不一致时以本节为准；**§1 的历史行不回改**，因为「当时读到什么」本身是这份计划的证据之一。

| 对象 | §1 计划时 | 2026-09-27 实测 |
|---|---|---|
| serveros 远端 main | `c5d118c`（本机 canonical 落后） | `b489e19`；本机 canonical `main` **等于** `origin/main`，clean，0/0 |
| ServerOS 架构 PR #6 | `13f2819` | 未动，仍是 `refs/heads/docs/product-convergence-20260924` 的 tip |
| 实施计划分支 | —— | `refs/heads/docs/implementation-plan-20260925` = `9560e9d`（2026-09-25 16:55） |
| ability-management | canonical `8068d0b` / main `d057b6a` | canonical `main` = `368c1be` **等于** `origin/main`，clean，0/0 |
| Ability 架构 PR #4 | `5c1963e` | 未动，仍是 `refs/heads/docs/product-convergence-20260924` 的 tip |
| 已安装 Linux Work | `alert-card-scope-20260924@8ce6fe7`，`3.1.0+srv0003.20260924c` | `serveros-memory-provider-20260927`，`3.1.0+srv0004.20260927b`；`previous` = `serveros-j09-identity-20260927@b8f7d21` |
| records | `7368e298` | `50a50ea` **等于** `origin/main`（本地 `main` 未配 upstream，两侧内容相同，0/0） |
| ops | 本地 `a8272d4` / 远端 `1993042` | 本地 **等于** `origin/main` = `18553ed`，clean |
| work-engine | 本地 `f6be338` / 远端 `a0f370f` | `a0f370f` **等于** `origin/main`，clean，0/0 |
| meta-cc | 未列入本表 | `a8c57f5` **等于** `origin/main`，且等于已装插件记的 `gitCommitSha` |

**读法**：计划时「本机 canonical 落后于远端、不能作为默认包 pin」这一条已经不再成立——六个仓的
canonical 现在全部 clean 且等于各自的远程 main，本机默认入口也已从 core 迁到
`serveros-linux-work`，已装载荷是 `serveros-memory-provider-20260927`。

**上面表格里「未动，仍是 … 的 tip」是对的，但 §1 对它们的定性已经过期**：两个架构对象的提交
逐字未变，然而 `merge-base --is-ancestor 13f2819 main` 与 `merge-base --is-ancestor 5c1963e main`
（各自仓）**都退出 0** —— 它们已经是各自 main 的祖先，`git cherry main` 一条 `+` 都没有。
所以 §1 表格里「PR 未合并前不能称 main 含目标设计」这句前提**已经不再成立**：main 现在就含
那份设计。这不改 §1 的历史行，但它改**引用方式**：

```text
判一个提交在不在 main 里 —— 用 merge-base --is-ancestor / git cherry 的 + 列
不要用 —— 分支名、PR 号、分支是否还在
```

serveros 远端 6 条 ref（`origin/HEAD` 之外）**全部**是 main 的祖先，因此「远端还有 6 条分支」
不等于「还有 6 份未合并的工作」。同一判据也已用在 ability-management 侧（`5c1963e` 同样在
main 里）。

**§6 跨仓一致性（本次实测的结论，不另开章节）**：

- **serveros ↔ quay**：本仓**没有**硬编码 quay 可执行路径。`runtime/workbench.py` 的
  `doc_quay()` 刻意不猜——它要 `--quay-bin` 或 `SERVEROS_QUAY_BIN`，缺失时印出
  「这一页问的是 Provider 的 store，而 Provider 的构建产物在哪是这一台机器的事」并继续渲染
  账本与日志两节。`src/quay` 是**三个用户共用的活动检出**（yale/zhengji/tom 都有进程在跑），
  按政策 DEFER，不动。
- **serveros ↔ marketops**：本仓**没有任何** `marketops` 引用，因此没有可修的集成点。
  MarketOps 侧在本机的实际形态是 Laya：一个用户级服务，`WorkingDirectory` 就是 canonical
  `src/ability-management`，即它跑的是 canonical 工作树的 `providers/laya`。该目录在
  `819651d`（laya 分支 tip）与 `368c1be`（canonical main）之间**零改动**，而 `819651d` 是
  `368c1be` 的祖先 —— 这条分支不是分叉，是已合入的旧落点，没有漂移。
- **serveros ↔ meta-cc**：真正需要修的一处是**能力树里 meta-cc 的归类**。它此前同时出现在
  「已归类」与「未归类」两个答案里（详见 `runtime/ability_projection.py` 的 `domain_tree()`
  第 4 条与本轮 R9 的续记），原因是目录把同一个能力写成两条记录（`mcps/meta-cc` 与
  `plugins/meta-cc`，同一 `Canonical ID`、同一 `Family`，只有 `Type` 不同），而推导按**记录**
  读。已修并在测试里钉住。meta-cc 自身只负责 session history / intelligence，其自述元数据
  就是这么写的，与 §8 的确认一致。
- **Laya / MetaCC 资源实测（§8）**：`laya-shared.service` cgroup 用量 **2.0 GiB / max 3.0 GiB，
  peak 已到 3.0 GiB**，swap 499 MiB（peak 1.1 GiB），`NRestarts=3` —— peak 顶到上限是**当前**
  的漂移，不是过去某次验收的结论，已记入 §8 待办所需的现状。`meta-cc-mcp`（vince）RSS 约
  **34 MiB**，不是常驻服务、无 cgroup 上限；它与已装 cache 的同一个二进制**逐字节相同**。
