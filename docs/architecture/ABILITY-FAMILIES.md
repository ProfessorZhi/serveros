# Ability 家族与职责闭合

> 从 [总架构](../../ARCHITECTURE.md) 委托而来的能力分工规范；不是另一份实际安装 manifest。

## 1. 类型规则

Ability/Skill 是可复用工作方法；operation 是其内部动作；recipe 是组合能力的常见路径；policy 是所有相关动作都必须遵守的边界；Provider 是具体实现。只有具有独立目的、输入/输出、失败语义且可复用的方法才值得成为独立 Skill。

以下是职责合同，不声称同名 payload 已存在。已有 stable ID 优先保留；显示名可调整，旧名称保留来源/alias。不根据本表手工复制生成目录或伪造 registry revision。每个新增节点必须有真实 consumer、执行协议/机制、正常与拒绝案例。

## 2. 两大导航树

```text
ServerOS Abilities
├── A 工作基础设施
│   ├── 能力治理：ability-management
│   ├── 环境与运行：runtime-management
│   ├── 工作空间：workspace-management
│   ├── 活会话：agent-session-management
│   ├── 会话历史：session-manager
│   ├── 工作记忆/交接：shared-memory
│   ├── 测试环境：test-management
│   ├── 文档生命周期：document-stewardship
│   ├── 构建与发行：build-release（有独立机制时登记）
│   ├── 云空间治理：feishu-space-management
│   ├── 人类控制：human-control
│   ├── 多设备：fleet-coordination
│   └── 工程/安全规则：policy + shared procedure，非一堆微型 Skills
└── B 信息到交付
    ├── 采集与认识：information-management
    ├── 候选/工作：work-management
    ├── 需求/产品：product-requirements-and-design
    ├── 调研：research-and-analysis（有独立协议时登记）
    ├── 架构/计划：technical-architecture-and-planning
    ├── 开发/实施：implementation-delivery
    ├── 验收/反馈：quality-feedback-and-iteration
    ├── 有界委派：cc-quay-delegation
    └── 汇报/发布：feishu-report（保留身份，方法与 Provider 分离）
```

技术支持、部署运维、文档修订、测试方案、沟通对接都由这些能力组合处理，不每种工单创建一个新工作系统。现有 `software-journey-orchestrator` 可提供推荐 recipe，但不存另一份 Item 状态；`inventory-publishing` 提供地图投影操作，不成为另一采集权威。其他已有 specialist（frontend/backend/feature 等）仍可按需发现，不强迫删除或整体改名。

## 3. Family A 合同

| 能力 | 输入 -> 操作 -> 产物 | 边界/关键验收 |
|---|---|---|
| ability-management | registry + scoped observation -> trace/identify/revision/compatibility/search/build/install/reconcile -> 可追溯能力与实际装机记录 | `(type,id)` 不按名字猜；生成包与 canonical 一致；验证前不写 verified/installation；composition 的产品选择仍归 serveros |
| runtime-management | desired + observed + policy -> inspect/probe/ensure/release/recover -> EffectiveEnvironment/运行结果 | 不重复 ps/df 真值生产；unknown owner/protected 不自动动；MCP/browser/container/tunnel/gateway 都走 Provider，不写固定进程名单 |
| workspace-management | root roles + repo identity + current ownership -> locate/prepare/check/propose/apply/verify -> execution context、地图、有限变更 | linked worktree 与 standalone 区分；并行业务 DEFER；不用全局 Git clean 作为前置；不安装 Skills |
| agent-session-management | actor + native session + Item -> list/inspect/create/resume/send/result/interrupt/stop/handoff | operation 单独 support；interrupt 不代替 stop；request accepted/turn done/session state 不混；单 writer |
| session-manager | native transcript inventory -> search/inspect/archive/restore/export | 保留 Claude 专属协议；Codex 未实测不假称兼容；运行中会话不归档；不上传 transcript |
| shared-memory | Item facts/decision/checkpoint -> scoped recall/checkpoint/handoff -> 小 context capsule | durable 工作记录为权威；索引可重建；权限过滤先于召回；不把习惯自动发布成 Skill |
| test-management | fixed revision + test definition + budget -> acquire/smoke/run/release -> 隔离环境、Run evidence | test-envs 只存 HOW TO BUILD；端口/network/volume 唯一；复用前小 smoke，不每次 rebuild；不决定业务 acceptance |
| document-stewardship | document ref + authoring surface + revision + purpose -> inspect/revise/verify-links/publish/supersede/retention-plan | 不按标题猜 identity；不覆盖人工并发编辑；只管生命周期，内容认识归 information，云端位置归 feishu-space |
| build-release | pinned source/deps/platform + registry target -> build/inspect/smoke/publish/promote/rollback -> digest、manifest、receipt | 构建 != 发布 != 部署；immutable digest；秘密不烘焙镜像；无需每次完整重建；共享 registry/tag 改写需授权 |
| feishu-space-management | scope-bound Wiki/Drive/MyDocs/Minutes metadata -> inventory/resolve-placement/propose/apply/verify | 真实 API 能力决定 hierarchy；不复制全文；无关内容剔除不同于删除；按 MeetingBundle 引用安全治理 |
| human-control | 已认证 actor + request/target/revision -> interpret-intent/admit/dispatch/receipt | card delivery != consent；approval 绑定精确请求且过期/防重放；能力缺失不绘假按钮 |
| fleet-coordination | authored device refs + profiles + authorized reachability -> resolve/hand-off | 单节点合法；断线不是 CLOSED；不读另一 Provider 私有库绕过 API |

### 工程规则不是独立业务能力

small-change-surface 是**通用变更纪律**：解决完整问题所需的最小语义闭合；禁止顺手重构/搬目录/扩大 repo。它不是“最多三个文件”，也不应每调用一个 Skill 就再跑一套 gate。

Tokyo 规则是实例 policy，安全边界由准入与 OS/native permissions 执行；Skill 解释规则与引用，不复制一遍本机路径/用户名/阈值。跨运行时适配是发行逻辑，不复制业务代码。

研究检索渠道、LLM gateway、credential、MCP、浏览器、shell、容器、网络都是 Providers/基础设施接口；不是每个供应商一个 canonical 方法 Skill。

## 4. Family B 合同

| 能力 | 输入 -> 产物 | 不承担/停止条件 |
|---|---|---|
| information-management | 授权 source+cursor -> 最小 raw/snapshot -> relevance-filtered claims/understanding delta | capture 与 interpretation 是两个 operation；不创建 Item，不让无关录音污染认识；来源失联不撤回全部知识 |
| work-management | 来源/候选/人类选择 -> Candidate、Item、Today、Activity/Run、delivery closure | 生命周期复用 work-engine；Board 是投影；所有工作 kind 共用一套身份；取消/失败不写成成功 |
| product-requirements-and-design | 目标+stakeholders+constraints -> brief/acceptance/范围/未知 | 用户与 ChatGPT 深度参与；无目标先讨论，不能把自动推断需求当获准开发 |
| research-and-analysis | 问题+scope+budget -> primary sources/比较/结论/不确定性 | 不为普通查文档启动多 Agent；文献发现与原文核验分开；不用引用数量代替决策质量 |
| technical-architecture-and-planning | 已确认 brief +研究 -> 设计/替代方案/接口/风险/按需计划 | 重大选型必须形成 Decision；小改可用简短设计说明，不强制 ADR/完整 DAG |
| implementation-delivery | approved design/envelope +context -> 代码/文档/部署操作/支持答复等 deliverable | 在自己的 scope 自主推进；新目标/破坏/共享线上/预算越界升级；不自己改验收标准使结果通过 |
| quality-feedback-and-iteration | acceptance + revision + evidence -> 测试方案/定向验证/verdict/feedback | 设计测试与验收方法；test-management 提供环境；报告未测范围；不把 exit 0 当全部验收通过 |
| cc-quay-delegation | 父 Item 的有界切片 -> Quay 原生 Goal/PHASE/AC/Task -> 真实执行/回读/父工作引用 | 先查真实 CLI/API，不能编造 Quay 字段；不重复执行同一任务；Task done != Item done |
| feishu-report | durable work delta+decisions+evidence -> 日报/周报/决策包/发布 receipt | 方法可复用，飞书是 publication provider；原始摘要不等于事实；不编造进展百分比/节省时间/Quay 使用 |

## 5. 一种工作模型覆盖典型杂事

| 用户要做的事 | 默认 recipe | 最小有意义产物 |
|---|---|---|
| 小文档更正 | workspace/context -> document-stewardship -> implementation -> 定向检查 | 修订 diff + canonical link，无强制长设计 |
| 研究技术路线 | research -> requirements/design -> Decision | 有来源的比较、选择理由、边界 |
| 测试方案/复现 | quality -> test-management -> execute/Quay -> report | 场景/固定 revision/失败与成功证据 |
| 镜像/部署 | implementation -> build-release -> runtime -> quality | digest、部署目标、健康与 rollback 证据 |
| 支持咨询 | information -> KB lookup -> issue understanding -> repro/diagnosis -> response -> KB proposal | 案例答复、证据、知识改进；简单答复可不建 Item |
| 长期支持问题 | Candidate -> selected Item(kind=support) -> 同一 recipe | 与开发 Item 相同的跟踪/交付机制 |
| 会议/沟通 | scoped capture -> relevance -> understanding -> actions/candidates | 只保留相关段落；旁人闲聊不得生成工作 |
| 运维异常 | daemon fact -> alert -> approved investigation/recovery -> verify | 可理解事件与已执行动作 receipt |

### 两个并行业务项目

MarketOps 和 Support Bot 是消费者，不是本轮可随便编辑的目标。Support Bot code=`ProfessorZhi/litellm-support-bot`；knowledge=`ProfessorZhi/troublebuddy-kb-bridge`；`litellm-support` 只是既有支持资料源。实现验收可使用授权只读样本或 ServerOS 自有案例；不得往业务仓制造 demo 提交。

## 6. 每项能力的完成定义

每项至少具备短 description、真实入口/操作协议、明确 input/output、scope/拒绝条件、依赖/Provider、失败与部分结果语义、一个代表性真实执行、一个边界案例、来源 revision。纯方法 Skill 可由执行 Agent 按协议产出文档，不强迫制造无意义 Python wrapper。

每个 Skills/MCP/plugin/hooks/subagent/instructions surface 都要检查用途与加载时机；不强制六种 artifact 全部生成。canonical payload 修改后通过现有 control-plane 更新 fingerprint/revision，按实际测试扩 compatibility，按实际观察记录 installation。
