# ServerOS 文档地图

这里按**你要做什么**组织，不按写作时间。每篇文档回答一个问题；读之前先看文件头部的状态与日期。

## 状态约定

| 标记 | 含义 |
|---|---|
| **设计** | 说明应该怎样，不代表已经实现 |
| **实现** | 代码存在并有测试，不代表已安装 |
| **已安装** | 目标设备上真的在运行 |
| **已验收** | 在真实设备与真实客户端上通过验收场景 |

带日期的文档记录的是**当天**的测量与判断。数字（工具数、revision、通过项）不回改成今天的值；引用前先看日期。

## 第一次来

1. [根 README](../README.md)：产品是什么、现在能做什么、5 分钟上手。
2. [架构导读](../ARCHITECTURE.zh.md)：核心模型与阅读路径（中文）。
3. [ARCHITECTURE.md](../ARCHITECTURE.md)：唯一规范性产品架构（英文）。

## 运行与部署

| 文档 | 回答什么问题 | 状态 / 日期 |
|---|---|---|
| [Fleet V1 拓扑与运行](architecture/FLEET-V1-TOPOLOGY-OPERATIONS.md) | 每台设备上 Core、Runtime、Ability 的边界；Fleet 如何连接 | 设计 · 2026-10-17 |
| [Fleet V1 总跟踪](plans/fleet-v1-four-pillar-tracker.md) | V1 做完长什么样：三设备 × 四部分 + 一次真实自迭代的验收矩阵、推进顺序、owner 动作、PR 处理建议 | 草稿 · 2026-10-29 |
| [Fleet V1 差距清单与工作包](plans/fleet-v1-gap-and-work-packages.md) | V1 还差什么；按泳道拆成可交给本地 Codex / Claude Code 的工作包（F 功能、D 解耦与目录、Q 质量与运维），每个带范围、验收与依赖 | 计划 · 2026-10-26 |
| [Fleet 需求与 V1/V2 架构](architecture/FLEET-V1-V2-ARCHITECTURE.md) | 舵手模型、需求与稳定性要求、V1 技术设计（事件收件箱、通知、抢占）、V1 必须预留的 13 个接口、V2 层层管理与 OpenAgents | 设计 · 2026-10-26 |
| [Fleet 四层与仓库目录](architecture/FLEET-FOUR-LAYERS.md) | Fleet 的四层（底座、接入与枢纽、设备能力、指挥侧）各管什么、在哪个目录、怎么依赖；目录登记表 [layers.yaml](architecture/layers.yaml) | 设计 · 2026-10-26 |
| [Fleet Next 验收手册](fleet-next-acceptance-runbook.md) | 人工验收怎么做；离线测试覆盖不到什么 | 运行手册 |
| [Next ingress runbook](fleet-v1-next-ingress-runbook.md) | 独立 Tailscale 节点从零到可用的完整步骤 | 运行手册 |
| [Owner/runtime 验收 2026-10-22](fleet-v1-owner-runtime-acceptance-20261022.md) | 候选版本的 34 个工具、授权与恢复行为 | 候选证据 · 2026-10-22 |
| [Dell 就绪](fleet-v1-dell-readiness.md) | Dell 多账号的只读就绪矩阵 | 只读测量 · 2026-10-15 |
| [Dell cutover](fleet-v1-dell-fjd-cutover-plan.md) | Dell 账号切换的前置条件 | 计划 |
| [设备就绪缺口](fleet-next-device-readiness-gaps.md) | 各设备还缺什么 | 缺口清单 · 2026-10-13 |
| [受约束测试运行器](operations/constrained-test-runner.md) | 共享主机上如何跑测试而不越界 | 运行约定 |
| [密钥泄露与轮换](operations/152-secret-disclosure-and-rotation.md) | 凭据泄露后的处置 | 运行手册 |

## 开发与契约

| 文档 | 回答什么问题 | 状态 / 日期 |
|---|---|---|
| [Fleet MCP 权限矩阵](../integrations/fleet-v1-mcp-conformance/AUTH-MATRIX.md) | 每个工具需要哪个 scope、过哪道闸门 | 契约 |
| [能力投影](../integrations/fleet-v1-mcp-conformance/CAPABILITY-PROJECTION.md) | `handlerBound` 与三态语义 | 契约 |
| [协作合同](COLLABORATION-CONTRACT.md) | Parent 继续责任、接收与恢复 | 设计 |
| [安全与并发](safety-and-concurrency.md) | 共享主机上的并发与副作用边界 | 设计 |
| [仓库职责](repository-roles.md) | 各仓库拥有什么、不拥有什么 | 设计 |
| [目录收敛](architecture/DIRECTORY-CONVERGENCE.md) | 目录与产物的放置规则 | 设计 |

## 产品与设计

| 文档 | 回答什么问题 | 状态 / 日期 |
|---|---|---|
| [原生 Agent 工作环境](architecture/NATIVE-WORK-ENVIRONMENT.md) | 目标产品形态与一天的运转 | 设计 · 2026-10-07 |
| [记忆系统](memory-fabric.md) | 三层记忆、权威归属与召回 | 设计 · 2026-09-24 |
| [每日工作循环](daily-work-cycle.md) | 采集、对账、Focus 与交付 | 设计 |
| [信息与文档生命周期](INFORMATION-DOCUMENT-LIFECYCLE.md) | Source、理解、工作、交付的统一对象 | 设计 |
| [监控产品架构](MONITORING-PRODUCT-ARCHITECTURE.md) | 状态卡、告警与 Incident 的产品模型 | 设计 · 2026-09-24 |
| [Ability 运行模型](ABILITY-OPERATING-MODEL.md) | 定时节点、职责 Owner 与 `serverosd` 边界 | 设计 · 2026-09-24 |
| [外部参考资料库](EXTERNAL-REFERENCE-LIBRARY.md) | Reference、Follow 与采用 | 设计 |
| [重点深归档](DEEP-ARCHIVE-POLICY.md) | 深归档范围与 discovery 分母 | 设计 |
| [Agent 身份、Session 与委派](architecture/AGENT-IDENTITY-SESSION-DELEGATION.md) | V2 的角色与责任拆分 | **V2 设计，未构建** |
| [Fleet V2 自治组织](architecture/FLEET-V2-AUTONOMOUS-ORGANIZATION.md) | V2 的分层自治形态 | **V2 设计，未构建** |

能力（Ability）的权威定义在 [ability-management](https://github.com/ProfessorZhi/ability-management)：[能力蓝图](https://github.com/ProfessorZhi/ability-management/blob/main/design/ABILITY-CANONICAL-BLUEPRINT.md)、[实现矩阵](https://github.com/ProfessorZhi/ability-management/blob/main/design/ABILITY-REALIZATION-PLAN.md)。

## 历史与证据

这些目录保留**当时的**判断、计划与证据，不是当前状态。引用前先看文件日期和 Status 行。

- [plans/](plans)：实施计划，含 Fleet V1 campaign（[plan-02](plans/plan-02-complete-fleet-v1-delivery-campaign.md)）和 P0–P12（[CC-EXECUTION-PLAN](CC-EXECUTION-PLAN.md)）。
- [proposals/](proposals)：成对的设计提案。
- [reviews/](reviews)：独立评审记录。
- 带日期的验收/评审：`integrations/fleet-v1-release/QA-MATRIX.md`、`integrations/fleet-v1-release/RC-*-REVIEW-2026-10-20.md`、`integrations/fleet-v1-qa/QA-TRIAGE-2026-10-17.md`。

历史文档里的工具数（33/34）、revision（`fleet-mcp-3/4/5`）与「未部署」判断都是当天的记录。当前工具面以 `fleet_mcp/version.py` 为准。
