# ServerOS 当前文档入口

2026-09-24需求/产品/架构收敛。产品总边界仍为[ARCHITECTURE.md](../ARCHITECTURE.md)：原生Agent主脑、薄Plugin、唯一Core、Work Engine/Agent Control各自权威。本轮补外部参考、重点深归档与Git/飞书方向，并把最新并行实现纳入**一个**实施计划。

## 2026-10-17 当前 Fleet V1 唯一实施入口

当前 V1 产品范围以 [根架构](../ARCHITECTURE.md) + [完整三机 Fleet 架构](architecture/FLEET-V1-TOPOLOGY-OPERATIONS.md) 为准，**实施 DAG、Work 归属、设备约束与最终验收**以 [Full Fleet V1 Campaign Plan](plans/plan-02-complete-fleet-v1-delivery-campaign.md) 和 [#244](https://github.com/ProfessorZhi/serveros/issues/244) 为准。相比 09 月和 10 月 14 日的计划，现已纳入各用户 C++ Core/Windows SSH 16095→Selkies/Tailscale/RDC 状态及自愈、三机真实 CC **与 Codex** CloudCLI Runtime、设备 Ability 实际消费和 **本地 CC Fleet Plugin 优先**。ChatGPT 插件 / App2 SSO 为后续独立客户端阶段。

下述 P0–P12 与 10 月初 Native Work 计划继续作为原子子系统背景，**不再是这次完整 Fleet V1 的唯一实施计划**；历史版本日期和当时状态不改写为最新 LIVE。CI 因额度耗尽只能作 HOSTED_CI_QUOTA_WAIVED，必须保留真实本地/三机回执。

## 按当前问题选择入口

1. [CC-EXECUTION-PLAN](CC-EXECUTION-PLAN.md)：唯一P0–P12，2026-09-25现场→目标源码→真实消费者→安装/定时启用→目录收口/回退；配套[只读现场基线](IMPLEMENTATION-BASELINE-20260925.md)和[给服务器CC的短交接](CC-IMPLEMENTATION-HANDOFF.md)。
2. [INFORMATION-DOCUMENT-LIFECYCLE](INFORMATION-DOCUMENT-LIFECYCLE.md)：Source/材料/Reference/理解/工作/交付的统一对象与权威图。
3. [Ability Blueprint](https://github.com/ProfessorZhi/ability-management/blob/main/design/ABILITY-CANONICAL-BLUEPRINT.md)与[21项实现矩阵](https://github.com/ProfessorZhi/ability-management/blob/main/design/ABILITY-REALIZATION-PLAN.md)：最小形态、真实消费者和退出门，不是21行LIVE勾。

仅按当前任务加载下面相应合同，不每次读全库。

## 五条自动循环与共享记忆

根README的五条循环是当前产品入口：信息、工作、能力、知识、运行。它们共享Memory、权限和证据，但不是一个固定DAG。OpenViking的目标形态是一份Tokyo共享长期记忆Provider：Claude Code/Codex优先复用官方Memory Plugin的hooks+MCP；Windows等设备通过受认证私网访问同一服务。GitHub/private records保存当前可审计语义与跨系统互操作，不同步OpenViking内部索引；ChatGPT当前从GitHub/项目资料读取。详见 [Memory Fabric](memory-fabric.md) 与能力仓 [A2 Memory](https://github.com/ProfessorZhi/ability-management/blob/main/design/MEMORY-RETRIEVAL-ABILITY.md)。

OpenViking **已于 2026-09-27 在这台节点上绑定并运行**（独立 unit、`127.0.0.1:1933`、已认证数据面、数据盘持久 workspace、已演练的 backup/restore）；`memory/provider-contract.yaml` 已迁到 v2，旧的「不得有新服务 / 完全可由 Layer B 重建 / 没有全局 Provider」三条假设换成了已接受目标的 12 条采用条件，每条都带它被量到时的证据。**仍未做的两件**：一是**没有 consumer**——没有任何代码把检索结果喂进决策，所以「current records 覆盖 retrieved memory」目前是空真，consumer 落地时必须重验；二是**未配 VLM/capture**，即 provider 可检索但不自动采集，这是有意的（capture 的 scope 策略尚未写定，且会花用户的 Codex 额度）。逐条状态见 [Memory Fabric](memory-fabric.md) 的「What of this exists」一节。

## 信息、参考和知识

| 文档 | 范围 |
|---|---|
| [外部参考资料库](EXTERNAL-REFERENCE-LIBRARY.md) | 原网页/论文/他人文档→个人Reference、可选本地保存、Follow、人批注、采用；REF01–REF12 |
| [重点深归档](DEEP-ARCHIVE-POLICY.md) | confirmed source、历史范围、转发包内部有界遍历、多材料与discovery分母 |
| [Source/Document合同](https://github.com/ProfessorZhi/ability-management/blob/main/design/SOURCE-DOCUMENT-CONTRACT.md) | desired/observed、身份、版本、权限、正文authority、单向投影 |
| [B1输入](https://github.com/ProfessorZhi/ability-management/blob/main/design/INFORMATION-INTAKE-ABILITY.md) | native/现有采集、Web质量、限定doc/Wiki与部分范围 |
| [B2理解](https://github.com/ProfessorZhi/ability-management/blob/main/design/STRUCTURED-UNDERSTANDING-ABILITY.md) | 原生reading、现有writer、真实委托与接受、view/记忆传播 |
| [理解、Memory与每日对账](UNDERSTANDING-MEMORY-RECONCILIATION.md) | Atomic Claim + Current View、多源Reading Packet、current-first Memory、MyDocs每日对齐与人类决策面 |
| [B9维护](https://github.com/ProfessorZhi/ability-management/blob/main/design/DOCUMENT-STEWARDSHIP-ABILITY.md) | Reference/自管指南/共同章节/SOP，正文与批注分责 |
| [飞书三端与最终目录](https://github.com/ProfessorZhi/ability-management/blob/main/design/FEISHU-INFORMATION-SYNC-ARCHITECTURE.md) | 当前live MyDocs/个人笔记/公开文档/外部文档/Drive；三类文档关系、MeetingBundle、Records目录与Feishu-local-GitHub连续性 |
| [飞书空间](https://github.com/ProfessorZhi/ability-management/blob/main/design/FEISHU-SPACE-MANAGEMENT-ABILITY.md) | 位置/对象/ACL；外部文档的历史观察与当前初始化/同步分开，不再默认MyDocs参考资料 |

## 协作与运行

| 文档 | 范围 |
|---|---|
| [Collaboration](COLLABORATION-CONTRACT.md) | Parent继续责任、receiver接受、授权/预算/恢复 |
| [Fleet V1：设备拓扑、MCP/Skills、自愈与交付验收](architecture/FLEET-V1-TOPOLOGY-OPERATIONS.md) | V1：东京 Work、A6000 Compute、Windows Edge、CloudCLI/Fleet MCP、Native Core、有界自愈和 CC-first E2E 门禁 |
| [Agent Identity / Session / Delegation V2](architecture/AGENT-IDENTITY-SESSION-DELEGATION.md) | 复用既有 Work/Grant/Checkpoint/epoch，以项目角色＋派生责任视图解耦 Session；不另建 Assignment DB；V1 写入 fencing/幂等/互斥租约先通过 [#237](https://github.com/ProfessorZhi/serveros/issues/237) |
| [Daily Work Cycle](daily-work-cycle.md) | 两水位、最多两root焦点、GPT/Quay与必需交付 |
| [Ability Operating Model](ABILITY-OPERATING-MODEL.md) | 21稳定职责→子Ability/实现层；定时节点owner、Session/Workspace显式位置、serverosd与scheduler边界、自迭代入口 |
| [Scenario Acceptance](SCENARIO-ACCEPTANCE.md) | J01–J10；本轮REF/DEEP追加到对应用户场景 |
| [协作实现索引](COLLABORATION-IMPLEMENTATION.md) | 合同到主计划映射，明确新writer/水位已存在而业务消费还须验证 |
| [21项场景](https://github.com/ProfessorZhi/ability-management/blob/main/design/ABILITY-SCENARIO-CONTRACTS.md) | 专属工作/交接；旧源码观察要按当前revision复核 |
| [产品体验](https://github.com/ProfessorZhi/ability-management/blob/main/design/PRODUCT-EXPERIENCE-CONTRACT.md) | 首层结果、空/未知/partial/等待与真实验收 |
| [Progressive Disclosure](https://github.com/ProfessorZhi/ability-management/blob/main/design/PROGRESSIVE-DISCLOSURE-CONTRACT.md) | 完整Ability树不整棵入prompt；description短而准、Skill根做router、references/scripts/MCP按需展开 |
| [Ability Card & Registry](https://github.com/ProfessorZhi/ability-management/blob/main/design/ABILITY-CARD-REGISTRY-CONTRACT.md) | Product Ability/Artifact术语、Card、适用范围、来源/license、Content Digest、Author/Device分离、runtime projection |

## 监控与共享Provider

[Monitoring Product Architecture](MONITORING-PRODUCT-ARCHITECTURE.md)定义监控产品总模型；[Monitoring Scope / Recovery / Calendar / Cards](MONITORING-SCOPE-RECOVERY-CARDS.md)固定monitored set、共享机principal边界、分层自愈、中国工作日/调休日历、08:00–20:00播报窗口、latest+折叠历史和独立Incident卡；[Node Observability](NODE-OBSERVABILITY.md)定义采集与Core独立健康；[卡片产品](BROADCAST-ALERT-DESIGN.md)、[表达规范](../CARDS-STYLE.md)、[Monitoring Acceptance](MONITORING-ACCEPTANCE.md)负责具体人面与验收。

[Shared decision/search](shared-decision-search.md)及[Provider合同](https://github.com/ProfessorZhi/ability-management/blob/main/design/SHARED-PROVIDER-CONTRACT.md)定义单Laya及Jev/Serper边界。服务部署、两个真实consumer、答案质量不是同一证明。当前MarketOps保留业务MCP/独立ledger，Docker和客户外联不因本轮集成启动。

## 原技术合同与历史

[Context](context-capsule.md)、[Discovery](capability-discovery.md)、[Parity](claude-codex-parity.md)、[Safety](safety-and-concurrency.md)、[Ability Fabric](ability-fabric.md)、[Agent Runtime](agent-runtime-model.md)、[Memory](memory-fabric.md)、[Human Control](human-control.md)、[Runtime Resources](runtime-resources.md)、[Fleet](fleet-controller-adapter.md)、[Space Resolver](space-resolver.md)、[Repository Roles](repository-roles.md)保持各自责任。

[此前反查](WORK-SCENARIO-REVIEW-20260923.md)、[旧实测](integration-readiness-20260922.md)、[历史收敛](architecture/CONSOLIDATION-REVIEW-20260921.md)和ledger保留日期/范围，不倒改成新实现的证明。新版源码推翻旧缺失判断时以主计划当前基线与实际测试为准，不把历史风险无限当待办。

本次只改设计/计划/索引与结构一致性检查，基于最新main保留并行代码/数据/测试。无payload、runtime、manifest、source文件、凭据、飞书对象、部署和授权的本轮修改；后续实现必须把新规则落实到真正入口和消费者，不靠文档改LIVE。


### Shared-node alert scoping

Monitoring contracts explicitly separate current-principal objects, shared infrastructure and other users' WIP. Other-principal runtimes do not become the current user's cleanup alerts; known-owner lifecycle drift, Agent-session identity and one-card-per-day/episode rules are in [Monitoring Product Architecture](MONITORING-PRODUCT-ARCHITECTURE.md).

## 本轮收敛与未决事项

能力仓[全职责审计](https://github.com/ProfessorZhi/ability-management/blob/main/design/REQUIREMENTS-ARCHITECTURE-AUDIT-20260924.md)给出21职责、36个payload及其他Registry入口的源证据与最小迁移；[Authoring Standard](https://github.com/ProfessorZhi/ability-management/blob/main/design/ABILITY-AUTHORING-STANDARD.md)属于G1.4，不是普通任务必读的元平台。

当前主线是已授权信息自动更新、已选Focus自动接续、按required结果交付、工作日daily状态与真实Incident分开。新表/服务/规则必须有真实consumer，不把每个产品概念都实现为对象。未决产品/权限选择在审计中单列；设计明确、源码存在、安装/运行和业务通过各自有证据。
