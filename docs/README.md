# ServerOS 当前文档入口

2026-09-23整合。产品总边界仍为[ARCHITECTURE.md](../ARCHITECTURE.md)：原生Agent主脑、薄Plugin、唯一Core、Work Engine/Agent Control各自权威。本轮补外部参考、重点深归档与Git/飞书方向，并把最新并行实现纳入**一个**实施计划。

## 先看三份

1. [CC-EXECUTION-PLAN](CC-EXECUTION-PLAN.md)：唯一P0–P12，当前源码/历史回执分开。提交前已读新增semantic writer、水位、Nature资料与发布排除；不重造已实现机制。
2. [INFORMATION-DOCUMENT-LIFECYCLE](INFORMATION-DOCUMENT-LIFECYCLE.md)：Source/材料/Reference/理解/工作/交付的统一对象与权威图。
3. [Ability Blueprint](https://github.com/ProfessorZhi/ability-management/blob/main/design/ABILITY-CANONICAL-BLUEPRINT.md)与[21项实现矩阵](https://github.com/ProfessorZhi/ability-management/blob/main/design/ABILITY-REALIZATION-PLAN.md)：最小形态、真实消费者和退出门，不是21行LIVE勾。

仅按当前任务加载下面相应合同，不每次读全库。

## 信息、参考和知识

| 文档 | 范围 |
|---|---|
| [外部参考资料库](EXTERNAL-REFERENCE-LIBRARY.md) | 原网页/论文/他人文档→个人Reference、可选本地保存、Follow、人批注、采用；REF01–REF12 |
| [重点深归档](DEEP-ARCHIVE-POLICY.md) | confirmed source、历史范围、转发包内部有界遍历、多材料与discovery分母 |
| [Source/Document合同](https://github.com/ProfessorZhi/ability-management/blob/main/design/SOURCE-DOCUMENT-CONTRACT.md) | desired/observed、身份、版本、权限、正文authority、单向投影 |
| [B1输入](https://github.com/ProfessorZhi/ability-management/blob/main/design/INFORMATION-INTAKE-ABILITY.md) | native/现有采集、Web质量、限定doc/Wiki与部分范围 |
| [B2理解](https://github.com/ProfessorZhi/ability-management/blob/main/design/STRUCTURED-UNDERSTANDING-ABILITY.md) | 原生reading、现有writer、真实委托与接受、view/记忆传播 |
| [B9维护](https://github.com/ProfessorZhi/ability-management/blob/main/design/DOCUMENT-STEWARDSHIP-ABILITY.md) | Reference/自管指南/共同章节/SOP，正文与批注分责 |
| [飞书空间](https://github.com/ProfessorZhi/ability-management/blob/main/design/FEISHU-SPACE-MANAGEMENT-ABILITY.md) | 复用MyDocs/独立个人笔记/Published Knowledge/Drive/Minutes，只增必要参考节点 |

## 协作与运行

| 文档 | 范围 |
|---|---|
| [Collaboration](COLLABORATION-CONTRACT.md) | Parent继续责任、receiver接受、授权/预算/恢复 |
| [Daily Work Cycle](daily-work-cycle.md) | 两水位、最多两root焦点、GPT/Quay与必需交付 |
| [Scenario Acceptance](SCENARIO-ACCEPTANCE.md) | J01–J10；本轮REF/DEEP追加到对应用户场景 |
| [协作实现索引](COLLABORATION-IMPLEMENTATION.md) | 合同到主计划映射，明确新writer/水位已存在而业务消费还须验证 |
| [21项场景](https://github.com/ProfessorZhi/ability-management/blob/main/design/ABILITY-SCENARIO-CONTRACTS.md) | 专属工作/交接；旧源码观察要按当前revision复核 |
| [产品体验](https://github.com/ProfessorZhi/ability-management/blob/main/design/PRODUCT-EXPERIENCE-CONTRACT.md) | 首层结果、空/未知/partial/等待与真实验收 |

## 监控与共享Provider

[Node Observability](NODE-OBSERVABILITY.md)定义CPU/RAM/盘/必要服务、Tailscale/隧道与Core独立健康；[卡片产品](BROADCAST-ALERT-DESIGN.md)、[表达规范](../CARDS-STYLE.md)、[Monitoring Acceptance](MONITORING-ACCEPTANCE.md)给可读默认面、冻结receipt、原生360/390px与隔离故障测试。

[Shared decision/search](shared-decision-search.md)及[Provider合同](https://github.com/ProfessorZhi/ability-management/blob/main/design/SHARED-PROVIDER-CONTRACT.md)定义单Laya及Jev/Serper边界。服务部署、两个真实consumer、答案质量不是同一证明。当前MarketOps保留业务MCP/独立ledger，Docker和客户外联不因本轮集成启动。

## 原技术合同与历史

[Context](context-capsule.md)、[Discovery](capability-discovery.md)、[Parity](claude-codex-parity.md)、[Safety](safety-and-concurrency.md)、[Ability Fabric](ability-fabric.md)、[Agent Runtime](agent-runtime-model.md)、[Memory](memory-fabric.md)、[Human Control](human-control.md)、[Runtime Resources](runtime-resources.md)、[Fleet](fleet-controller-adapter.md)、[Space Resolver](space-resolver.md)、[Repository Roles](repository-roles.md)保持各自责任。

[此前反查](WORK-SCENARIO-REVIEW-20260923.md)、[旧实测](integration-readiness-20260922.md)、[历史收敛](architecture/CONSOLIDATION-REVIEW-20260921.md)和ledger保留日期/范围，不倒改成新实现的证明。新版源码推翻旧缺失判断时以主计划当前基线与实际测试为准，不把历史风险无限当待办。

本次只改设计/计划/records说明，基于最新main保留并行代码/数据/测试。无payload、runtime、manifest、source文件、凭据、飞书对象、部署和授权的本轮修改；后续实现必须把新规则落实到真正入口和消费者，不靠文档改LIVE。
