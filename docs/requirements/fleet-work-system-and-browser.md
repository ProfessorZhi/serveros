# ServerOS Fleet：统一工作系统与 Browser Runtime 需求补充

> **状态：需求提案，待 Owner 评审；不是实现、安装或验收声明。**
> **基线日期：2026-11-13。** 本文根据 `serveros`、`ability-management`、`hwz-records` 的当前可读文档与代码整理；运行状态只按带日期的观测描述。
> **目的：** 把分散在 Feishu、GitHub、项目文档、设备、Agent Session 和 Memory 中的工作信息，连接成可追溯的工作闭环；同时把 Chrome/Browser Automation 纳入 Fleet 的设备资源、能力分发、权限与运行观测体系。

本文是现有架构的需求补充，不创建第二套产品架构。规范性架构仍以 `ARCHITECTURE.md` 为准；21 项 Ability 的职责词表仍以 `ability-management/product-tree.yaml` 为准；工作状态仍以 Work Engine 为准；当前结构化认识与项目记录仍以 `hwz-records` 为准。

## 0. 结论摘要

**ServerOS 已经考虑了这些需求中的相当一部分，但完成度不均衡。** 目前不是从零开始：Fleet V1/V2 已定义 Owner、Agent、Session、能力分发、设备、授权、Work Item、事件和跨设备委派；信息生命周期、每日工作循环、Memory Fabric、21 项 Ability、资源治理也已有设计或局部实现。

但需要区分四种状态：

- **已定义：** 架构、契约或需求文档明确描述。
- **有实现：** 仓库中存在代码或测试。
- **已部署：** 目标设备实际安装并运行。
- **已验收：** 真实 Agent、真实设备、真实来源和真实工作旅程通过验证。

文档存在不等于已部署；工具可调用不等于真实工作闭环已打通。

本轮最重要的判断：

1. **工作信息流和工作生命周期已有较完整的设计基础。** 缺口主要在实时来源摄入、结构化认识写入、Work Engine 与 records 的映射、每日 Focus 的真实数据对账，以及报告投递回执的端到端验收。
2. **Browser 作为资源已被考虑，但 Browser Runtime 还不是 Fleet 的一等受管对象。** 已有共享 Chromium、Playwright Runtime、持久 Profile 独占租约、storage state 按 Run 隔离等资源设计；MUC E2E 也已有 Browser Broker。缺少通用的跨设备 Browser Session 生命周期、Agent 归属、浏览器专项监控和统一凭据授权。
3. **Browser Automation 应成为 Ability 体系的重要横向能力，但不应擅自新增第 22 个顶层 Ability。** 它主要由 G1、A1、A4、A5、A7、B1、B5、B7、B9 等既有职责组合实现。
4. **Credential Center 应提供凭据引用与短时授权，不应把密码、Cookie 或 Token 交给 Agent。** 当前仓库已有密钥形状扫描、Transport 字段禁入和 Fleet 自身登录认证，但没有发现通用网站凭据 Broker 契约。
5. `professorzhi.fun` 个人网站矩阵应作为独立 Website Registry / Website Project 管理。Fleet 提供浏览器执行、监控、授权和测试能力，不拥有网站内容、DNS、部署架构或业务数据。

## 1. 现有设计与实现基线

### 1.1 已有的工作系统基础

| 领域 | 已有依据 | 判断 |
|---|---|---|
| Fleet 产品目标与 V1/V2 | `docs/architecture/FLEET-V1-V2-ARCHITECTURE.md` | 已定义 Owner 舵手模型、Agent/Session 分离、能力分发、设备、委派、事件、抢占与权限边界 |
| 四层架构 | `docs/architecture/FLEET-FOUR-LAYERS.md` | 已定义底层权威、Node Core、接入/枢纽、设备能力与指挥侧边界 |
| 21 项 Ability | `docs/architecture/ABILITY-REQUIREMENTS.md`、`ability-management/product-tree.yaml` | 已定义能力职责；包含资源管理、信息摄入、需求、Work、研究、设计、实现、测试、报告和人类控制 |
| 信息生命周期 | `docs/INFORMATION-DOCUMENT-LIFECYCLE.md` | 已定义 Source、Snapshot、Understanding、Work、Artifact、Publication 等对象关系 |
| 每日工作循环 | `docs/daily-work-cycle.md`、`docs/ABILITY-OPERATING-MODEL.md` | 已定义采集、理解、每日对账、需求对齐、Focus、执行、验收、交付和反馈的循环 |
| Memory | `docs/memory-fabric.md` | 已定义记忆层次、权威归属、召回和纠正；OpenViking 是 Provider 选择，不代表已接通 |
| 需求看板 | `intake/board-contract.yaml`、`intake/workchain-contract.yaml` | 已定义 Candidate、Item、Board、Today Focus 与文档链；Work Engine 是 Item 状态唯一权威 |
| 报告 | `runtime/daily_report.py`、`runtime/report_delivery.py` | 已有报告生成和投递回执边界；生成成功不等于投递成功 |

这些文档已经覆盖目标工作流的主要概念，不应再建立第二套 Work Engine、第二份 Priority、第二个 Board 或另一套 Memory 数据库。

### 1.2 已有的 Browser / GUI 基础

| 领域 | 已有依据 | 已覆盖内容 | 尚未覆盖 |
|---|---|---|---|
| Browser 资源分类 | `hwz-records/ops/workspace/serveros/resources.yaml` | Chromium 与 Playwright Runtime 为 shared；persistent profile 为 exclusive；storage state 为 clone-per-run | 通用 Fleet Browser Session 管理与跨设备归属 |
| 动态资源租约 | `runtime/environment-contract.yaml`、`runtime/environment.py`、`runtime/lease_store.py` | 引用生命周期、共享策略、独占策略、过期引用、释放裁决和持久占用记录 | Browser 专项生命周期、自动发现与监控消费者 |
| Fleet 资源读取 | `fleet/mcp-contract.yaml` 的 `environment.leases` | Agent 可读取当前资源占用，避免资源冲突 | 尚无完整 Browser create/attach/inspect/close 工具合同 |
| Playwright E2E | `ability-management/skills/muc-playwright-e2e/SKILL.md` | 有原子操作、Flow、Evidence、Run、Resume 和安全策略 | 当前是 MUC 业务专用 Skill，不是 Fleet 通用 Browser Runtime |
| Browser Broker | `ability-management/skills/muc-playwright-e2e/legacy/scripts/browser_broker.py` | 能发现 Native Playwright、Playwright MCP、CDP 已有 Session、Persistent Chrome Profile 等候选 | 需要提炼成通用 Provider 选择与会话接缝，不能直接复制旧实现 |
| Playwright MCP | `ability-management/mcps/playwright.md`、`mcps/playwright-mcp/ABILITY.md` | Claude/Codex 可用的浏览器操作 Provider；可截图、快照、点击、填写和观察页面 | 设备级版本/就绪/权限/租约与 Agent 任务归属的统一投影 |
| Browser-use | `ability-management/mcps/browser-use.md` | 已有候选 MCP Provider | 与其他 Provider 的统一策略、隔离和验收 |
| OpenCLI | `ability-management/skills/opencli/ABILITY.md`、`plugins/opencli/ABILITY.md` | 已登记能力身份与来源 | 当前 Canonical Revision 没有安装记录；不能宣称设备已可用 |
| Computer Use | `ability-management/mcps/computer-use/ABILITY.md`、`mcps/computeruse.md` | 已有 GUI 控制 Provider 记录 | 与 Browser Session 的权限分离和 Fleet 统一观测 |
| GUI 权限 | `fleet/transport-contract.yaml` | Transport Capability Level 已包含 `gui-control` | Browser DOM 控制与整台桌面 GUI 控制的细分授权和审计 |

**重要：** 资源租约已经考虑 Browser，但它解决的是资源占用和释放，不等于浏览器自动化产品已经完成。MUC Browser Broker 是可复用的实现经验，不等于 Fleet 已有通用 Browser Broker。

### 1.3 凭据与安全基线

当前已有：

- `fleet/transport-contract.yaml` 禁止 Transport Binding 携带 Token、Password、Secret、Cookie、Authorization 等敏感字段。
- `runtime/credential_shapes.py` 提供凭据形状扫描，返回规则名或字段路径，不返回秘密值。
- `fleet_mcp/human_auth.py` 管理 Fleet 自身的用户名、密码哈希和 TOTP 登录证明。
- `runtime/approval-contract.yaml` 将人工批准绑定到具体 Actor、Context、Action、Target、Scope、参数指纹和有效期。
- MUC E2E 要求在日志和 Evidence 中掩码 Password、Token、Cookie，并禁止将 Profile 或凭据提交到 Public。

这些是安全基础，但 **Fleet 自身登录认证不是网站 Credential Center**；密钥扫描也不是凭据托管和授权服务。本文只定义 Fleet 对 Credential Center 的消费合同，不假设当前已经存在某个通用 Vault。

### 1.4 当前需要核实的运行缺口

以下是 2026-11-13 本轮只读检查所得的观测，不应被误读为永久事实：

- Hub 的项目看板读取对 `hwz-records` 返回 `NOT_CONFIGURED`；若干项目 Board 返回空投影。它不能证明 Work Engine 中没有 Item，只能说明当前 Hub 与 records 的映射未建立或未读到。
- `hwz-records` 的 Feishu Chat 快照最后同步时间分布在 2026-09-14 至 2026-10-06；历史分页完整不等于 2026-11-13 的增量采集仍正常。
- Memory Fabric 有契约和 Provider 设计，但 OpenViking 的实际部署、连接、召回和纠正验收仍需单独证明。
- Tokyo Monitor 本轮返回 Watcher `RUNNING`，但健康检查 `TransportFault`；这属于当前运行故障线索，不是 Browser 功能已部署的证据。

因此，实施前应先读取真实 Work Engine、records root、来源游标、Memory Provider 和设备 Binding，不得通过创建空白数据或复制历史状态来制造绿色结果。

## 2. 产品目标与边界

### 2.1 目标

建立一个由 ServerOS 提供控制与执行基础设施、由 Agent 负责理解和工作、由 Owner 保留最终方向与敏感授权的统一工作系统：

1. 把 Feishu IM、Feishu Docs/Wiki/Minutes、GitHub、网页、论文、文件和项目产物作为可声明、可授权、可追溯的信息来源。
2. 将原始信息转化为有来源、有时间、有置信度、有冲突关系的结构化认识。
3. 将有价值的目标转化为唯一的正式 Requirement / Work Item，而不是让每条消息都变成任务。
4. 通过 Board 和 Daily Focus 管理当前工作；通过 Research、Design、Implementation、Test、Acceptance 和 Report 形成交付闭环。
5. 让 Memory 能召回当前有效认识和历史证据，并在更正、撤回或来源失效时同步失效。
6. 让 Browser 成为可被 Agent 申请、由 Fleet 解析、按设备执行、受租约和凭据策略约束、可观测和可审计的执行资源。
7. 支持 Claude Code、Codex、GPT 和未来 Agent 使用各自的 Browser Provider，而不要求它们共用同一个浏览器进程或 Profile。

### 2.2 非目标

- 不创建第二套 Work Engine、Board、Focus、Priority、Requirement Store 或 Memory Store。
- 不让 `serverosd` 负责 LLM 规划、阅读所有 IM、判断业务需求或替 Owner 选择优先级。
- 不把 Browser Automation 变成一个新的常驻 Workflow Engine，也不新增一个独立 Browser Daemon 作为前提。
- 不让 Ability 声明、Skill、MCP 或网页内容自行授予权限。
- 不把 Cookie、Storage State、密码或 Token 复制进 Agent Prompt、GitHub、普通 Memory、飞书报告或 Fleet Journal。
- 不把 Computer Use 等同于 Browser Automation；浏览器能力不得隐式获得整台桌面的控制权。
- 不由 ServerOS 承担 `professorzhi.fun` 网站内容、DNS、域名续费、业务数据库或部署拓扑的所有权。

## 3. 统一信息流与工作生命周期

```text
Feishu IM / Docs / Wiki / Minutes / GitHub / Web / Files / Research
                         │
                         ▼
              Source Registry & Policy
       身份、权限、范围、采集深度、版本、游标
                         │
                         ▼
                Information Intake
       增量捕获、Snapshot、Manifest、Coverage
                         │
                         ▼
             Structured Understanding
      Claim / Fact / Inference / Decision / Conflict
                         │
                         ▼
             Requirement Candidate
       去重、归属、价值、未知项、来源引用
                         │
                         ▼
              Work Engine / Board
       唯一 Item 状态、Activity、Run、Lease
                         │
                         ▼
                 Daily Focus
       Owner 对今日重点的选择与范围确认
                         │
                         ▼
             Research & Solution Design
       Evidence / Alternatives / Design / AC
                         │
                         ▼
                    Execution
      Agent / Session / Worktree / Ability / Browser
                         │
                         ▼
              Test & Acceptance
       TestRun / Evidence / Review / Verdict
                         │
                         ▼
             Deliverable & Report
       Artifact / PR / Report / Publication Receipt
                         │
                         ▼
                  Memory Update
       经验、有效认识、历史证据、失效关系
                         └──────────→ 下一轮 Understanding
```

### 3.1 来源采集

- 每个来源必须有稳定 `source_ref`、来源类型、Owner、权限范围、允许读取的内容范围、采集深度和当前 Revision。
- Feishu IM 按 P2P / Group、Chat ID、成员/业务范围、Follow Policy 管理；Feishu Docs/Wiki/Minutes 按文档身份、版本、空间权限和引用关系管理。
- GitHub 的 Repository、Issue、PR、Commit、Workflow 和 Review 作为可引用的工程事实；代码和版本仍以 GitHub 为权威，不把 GitHub 复制成另一套工程数据库。
- Web/论文/外部页面区分 Reference 与 Follow。收藏一次不等于持续监控，Follow 必须明确范围、周期、预算和退出条件。
- Snapshot 必须记录读取时间、源 Revision、实际读取范围、失败项和未读取范围。`NOT_READ`、`NO_CHANGE`、`SOURCE_UNAVAILABLE` 不得折叠成同一个空结果。
- 增量采集失败不得推进成功水位；单一来源故障不得阻断其他独立来源。

### 3.2 结构化认识与 Memory

- Understanding 记录原子 Claim、事实/推论/提案/决定、来源引用、有效时间、适用范围、冲突、撤回和更正。
- 项目、人员、主题、职责和跨项目关系应在 `hwz-records` 形成当前可读视图；ServerOS 提供摄入、验证、检索和失效机制。
- Memory 负责跨 Session 召回与经验复用，不取代 records 的正式语义权威，也不取代 Work Engine 的状态权威。
- 新消息不能自动升级为已确认事实；重要目标、责任、业务规则和权限变化必须经过正确的 Owner 或已授权决策流程。
- Source 被撤回、Revision 改变或 Claim 被纠正时，旧结论必须从当前召回中失效，但历史 Evidence 仍可追溯。
- OpenViking、MetaCC 或其他 Provider 只能作为可替换 Provider；Provider 断线时必须明确降级，不得伪造已召回或已写入。

### 3.3 需求、看板与每日工作

- Candidate 是尚未接受的需求候选；Requirement 表达长期目标、范围、非目标、交付边界和验收条件；Work Engine Item 是唯一正式工作责任。
- 已有 Item 的后续消息应更新来源引用或 Requirement Revision，不得因重复摄入而重复创建 Item。
- Board 是 Work Engine 状态的派生投影，不可独立编辑状态。飞书看板如作为入口，只是投影或交互界面，不成为第二权威。
- Today Focus 是 Owner/授权角色作出的选择，不是由 Board 或 Agent 自动推断出来的优先级。当前设计的 0–2 个 root Focus 约束继续有效，除非 Owner 另行决定。
- 未声明 Priority 必须如实显示未分级，不得根据 Item 状态、消息紧急词或类型猜测 P0–P4。
- Daily Reconciliation 应总结真正改变认识的事项、重复需求、冲突、待决问题、当前 Focus、阻塞和来源缺口；不得把未读取的信息描述为已读完。

### 3.4 调研、设计、实现、测试与报告

- Research 围绕真实问题收集官方资料、代码、生产只读事实、实验、反证和适用范围；研究结果应能被 Parent / Work Item 采用。
- Design 明确方案、替代方案、模块边界、兼容性、风险、迁移策略、停止条件和 Acceptance Criteria。简单任务允许轻量设计，不强制每项工作写完整设计文档。
- Execution 绑定 Work Item、Activity、Run、Agent、Session、Device、Workspace、Ability 和授权；Agent Session 不是工作状态权威。
- Test 与 Acceptance 分开：测试产生证据，正确的验收 Owner 判断是否满足 AC。Run 退出码、PR 创建或 Agent 自报完成都不等于验收通过。
- Report 从已接受的结果和 Evidence 生成。报告生成、发布、投递、对方收到和 Owner 确认是不同状态，必须分别留痕。
- 交付结果、失败原因、有效方案、未解决问题和用户反馈回到 Understanding / Memory，成为下一轮输入。

## 4. Browser Runtime：把 Chrome 纳入 Fleet

### 4.1 产品定位

Browser Runtime 是 Fleet 管理的一类设备执行资源和 Session 能力。它不是一个新的 Agent，也不是所有浏览器自动化工具的集合。

Fleet 负责：

- 设备上的浏览器能力发现、Runtime/Provider Binding、Readiness 和资源归属。
- Browser Session 的创建、连接、检查、续期、释放和失联处理。
- Browser/Profile/Context 的租约、并发隔离和资源预算。
- CPU、内存、进程、崩溃、连接和功能探针等运行观测。
- Agent、Work Item、Run、Device、Browser Session 和 Credential Lease 的关联。
- 授权、策略、审批、审计和结果回执。

`ability-management` 负责：

- Playwright、Playwright MCP、Browser-use、OpenCLI、Computer Use、Chrome Extension 等 Provider / Skill / MCP 的身份、来源、版本、兼容性、分发和升级。
- Provider 如何被 Claude Code、Codex 或其他 Agent 调用，以及它的具体操作方法。

Fleet 不应复制这些 Ability 的实现正文，也不应让 Browser Provider 自己成为授权权威。

### 4.2 Browser 资源与会话模型

至少区分以下对象，避免把 Chrome PID、Profile、Context 和 Page 当成同一件事：

| 对象 | 含义 | 默认共享策略 |
|---|---|---|
| Browser Runtime | Chrome/Chromium 可执行文件、版本、安装来源和启动能力 | shared |
| Playwright Runtime | Node/Python Playwright 运行环境及 Browser Driver | shared |
| Browser Process Group | 一次启动的浏览器主进程与子进程集合 | 按 Session/Run 归属 |
| Browser Profile | 持久 User Data Directory，包含登录态和本地状态 | persistent profile 默认 exclusive |
| Browser Context | 一组隔离的 Cookie、Storage、Permission 和 Network Context | 默认每个 Agent Run 独立 |
| Storage State | 可导入的登录态快照，属于敏感材料 | 仅按明确策略 clone-per-run |
| Page / Tab | Context 中的页面和标签 | 随 Context 生命周期 |
| Browser Endpoint | CDP / WebSocket / Provider 连接入口 | 本地或受控传输；按 Session 授权 |

每个逻辑 Browser Session 应能关联：

- `browser_session_ref`、`device_ref`、`agent_ref`、`session_ref`。
- `work_ref`、`activity_ref`、`run_ref`（任务场景适用时）。
- `provider_ref`、`browser_runtime_ref`、`profile_ref`、`context_ref`。
- Resource Lease、Credential Lease、创建时间、过期时间、状态和 Evidence Locator。

上述是需求字段，不要求一次性把所有字段加入某一个数据库。必须先确定各字段的权威 Owner 和实际消费者，避免第二套 Session DB。

### 4.3 Agent 自有浏览器与共享登录态

默认策略：

1. 每个 Agent Run 使用独立 Browser Context；需要隔离时使用独立 Browser Process / Profile。
2. Browser Binary、Playwright Runtime、下载缓存可以共享，不因此共享 Cookie 或登录态。
3. Persistent Profile 默认独占租约。同一 Profile 不允许多个 Agent 同时作为写入者；需要跨 Agent 协作时通过明确的 Broker 接口，而不是让多个 Agent 直接抢同一目录。
4. Storage State 是凭据材料。只有 Credential Policy 明确允许时才可加密保存并 clone-per-run；不得进入 Git、普通日志、Memory 或普通 Work Artifact。
5. 用户已经登录的 Chrome 只能通过明确授权的 Extension / CDP Attach 流程接入。不得默认扫描、读取或接管用户日常 Chrome Profile。
6. Agent 自己启动的隔离浏览器与用户桌面上的 Chrome 必须区分标识、权限和资源归属。
7. Profile Lease 过期、持有者失联或浏览器崩溃时，状态应为 EXPIRED / UNKNOWN / NEEDS_RECONCILIATION；不得直接把 Profile 当空闲，也不得自动删除 Profile。

现有 `resources.yaml` 已有 shared / exclusive / clone-per-run 分类；后续实现应复用 `runtime/environment.py` 与 `runtime/lease_store.py`，不得重写第二套 Browser Lease 系统。

### 4.4 Browser Provider 选择

Provider 是可组合的执行后端，不是互相覆盖的产品：

| 后端 | 适用场景 | 约束 |
|---|---|---|
| 官方 API / 网站 API | 网站有稳定、获准的 API，操作语义明确 | 优先使用；必须遵守 API 权限和限流 |
| OpenCLI | 已有 CLI 化的网站操作适配器，或能复用受控登录态的命令接口 | 通过 Ability Registry 管理；未安装/未验证时不能宣称可用 |
| Playwright Library / CLI | 确定性 DOM 操作、批量表单、自动化测试、截图和网络观察 | 适合可复现任务；依赖隔离的 Context 和 Evidence |
| Playwright MCP | Agent 需要交互式探索页面、访问性快照、截图、点击和填写 | MCP 是工具接口，不是凭据或权限权威 |
| Browser-use | 自然语言驱动的网页探索与多步交互 | 必须经过同一 Browser Session、Credential 和审计边界 |
| Chrome CDP / Extension | 连接明确授权的真实 Chrome 或复用用户登录态 | 连接本身是高权限；Endpoint 不得裸露或写入普通日志 |
| Computer Use / RDC | DOM/浏览器接口无法完成的系统对话框、桌面应用或 GUI-only 操作 | 独立 `gui-control` 授权；不得因 Browser 授权自动获得桌面控制 |

推荐选择顺序是按任务与风险选择最薄、最稳定的 Provider，而不是硬编码一个工具：API/CLI 优先于浏览器；确定性 Playwright 优先于视觉点击；MCP/Browser-use 用于需要 Agent 交互判断的场景；CDP/Extension 用于明确授权的现有会话；Computer Use 作为 GUI 专项能力。

Provider 不可用时应返回精确的 `UNAVAILABLE` / `NOT_INSTALLED` / `NOT_READY` / `NOT_AUTHORIZED`，不得静默切换到另一台设备、另一个 Profile 或更高权限的 Provider。

### 4.5 浏览器运行监控

Browser Runtime 应进入现有 A4 Runtime & Resource Management 与 Node Observability，不另建监控系统。

至少观测：

- Browser/Driver 版本、安装位置角色、启动参数的安全摘要、所属 Device 和 User Scope。
- 主进程及子进程树、PID、进程数、运行时长、退出码、崩溃和重启次数。
- CPU、RSS/Private Memory、Memory Pressure、Swap、文件句柄和 Profile 磁盘占用。
- Browser Session / Context / Page 数量，所属 Agent / Work Item / Run，当前 Lease 和 Lease 到期时间。
- CDP / WebSocket Endpoint 可达性、协议握手、页面目标枚举和必要的功能探针。
- 浏览器是否启动、是否可连接、页面是否可操作、认证是否有效必须分开判断。
- 采样时间、数据来源、观测范围、缺失字段和 UNKNOWN 原因。

监控默认只采集运行元数据，不采集页面正文、密码、Cookie、表单值或完整浏览历史。URL 只在任务明确授权时记录，并优先记录 Origin / Domain 而非完整 Query。

高 CPU / 高内存 / 长时间运行只能形成 Observation / Alert，不自动获得 kill、restart、profile cleanup 或关闭页面的权限。任何恢复动作必须经过现有 ownership、protection、lease、budget 和 approval 机制。

### 4.6 Credential Center 协同

Fleet 不存放网站密码，不把 Cookie 当普通 Browser State。它只消费 Credential Center 提供的凭据引用和授权结果。

建议的最小接缝：

1. Agent 为一个明确的 Work Item / Run 请求某个 `credential_ref`，同时声明目标 Origin、动作类别、设备、Browser Session 和所需有效期。
2. Credential Center 校验主体、账号、目标网站、允许动作、设备范围、有效期、MFA 状态和撤销状态。
3. Broker 返回短时 `credential_lease_ref` 或通过受控本地注入机制建立 Browser Session；原始 Secret 不进入 Agent Prompt、MCP 参数回显或 Fleet Journal。
4. Browser Runtime 将 Credential Lease 绑定到 Browser Context / Profile Lease；不能把一个站点的登录态无边界地用于其他 Origin。
5. 任务完成、Session 退出、Lease 到期或 Owner 撤销时，Broker 使授权失效；持久 Profile 的保留/退出仍按资源释放策略裁决。
6. 审计记录只保存 credential_ref、lease_ref、主体、Origin、动作、批准来源、时间、结果和 Evidence Locator，不保存 Secret、Cookie 或 Storage State 内容。

必须支持：Origin Allowlist、按网站/账号/设备/Agent/Work Item 授权、短时授权、撤销、MFA/人工接管、审计、失败关闭和凭据轮换。

敏感外部动作默认需要人工确认或预先明确的委托策略，包括付款、发布、删除、提交不可逆表单、修改账号安全设置、权限授予和批量发送。验证码、MFA 和反机器人挑战不得绕过；需要人工时应暂停并保留可恢复 Checkpoint。

若组织已有 Credential Center / Secret Provider，应复用其接口；若尚无通用服务，本提案只要求定义 Provider Contract，不在 ServerOS 内再造一个 Vault。

### 4.7 网站目标注册与 professorzhi.fun

建议建立独立 Website Registry，记录网站身份，而不是让 Agent 从聊天记录中猜网站：

| 字段 | 用途 |
|---|---|
| `website_ref` | 稳定网站身份 |
| canonical URL / allowed origins | 目标站点和导航边界 |
| GitHub repository / deployment target | 代码与部署权威引用 |
| environment | production / staging / local 等环境声明 |
| owner / project_ref | 业务负责人和所属项目 |
| credential_ref | 只引用凭据，不保存凭据值 |
| allowed_actions | read / inspect / fill / submit / publish / admin 等操作范围 |
| data_sensitivity / approval_policy | 敏感数据和审批要求 |
| health / last_verified | 网站状态与最近验证证据 |

`professorzhi.fun` 可在独立 Website Project 中登记门户和子站，例如 `www`、`blog`、`notes`、`lab`、`tools`、`apps`、`status`。这些是候选信息架构，不代表当前已经部署或确定使用这些子域名。

Website Registry 应由个人网站项目或 `hwz-records` 中的项目记录持有；ServerOS Fleet 只消费 `website_ref`，解析目标 Origin、Device/Provider、Credential Policy 和允许动作。DNS、TLS、Reverse Proxy、网站内容、数据库、发布流程和域名运维由网站项目负责。

## 5. 功能需求与验收

下表中的优先顺序是建议的建设顺序，不是 Work Engine 的 P0–P4 Priority，也不替 Owner 创建正式 Item。

### 5.1 工作系统需求

| ID | 需求 | 验收条件 | 当前判断 |
|---|---|---|---|
| WS-01 | Source Registry 与增量摄入 | 至少一条 Feishu IM、一条 Feishu Doc/Wiki、一条 GitHub 来源能从声明、授权、Snapshot、Revision 到 Coverage 回执；失败不推进成功水位 | 设计较完整；实时链路需验收 |
| WS-02 | Structured Understanding | 一次真实来源变化可形成带 Source Ref 的 Claim / Conflict / Decision；更正后旧 Claim 不再作为当前事实召回 | 设计存在；实时 Writer 与端到端接收需核验 |
| WS-03 | Requirement 去重与接受 | 同一来源重放不重复建 Item；Candidate 经接受后只映射一个 canonical Work Item | 契约存在；records/Hub 映射待修复 |
| WS-04 | Board 与 Daily Focus | Board 从 Work Engine 再生；Today Focus 是明确决策；状态、优先级未知时如实呈现 | 契约存在；当前 Hub root 配置与真实数据需对账 |
| WS-05 | Research / Design | 正式 Item 可引用 Evidence、Alternatives、Design 和 AC；简单任务可跳过完整研究阶段 | 已定义；需以真实 Item 验证 |
| WS-06 | Agent Execution | Activity / Run 关联 Agent、Session、Device、Workspace、Ability、Work Item 和 Grant；异步结果能被 Parent 接收 | 多数控制面已设计/实现；真实跨设备链路需逐项验收 |
| WS-07 | Test / Acceptance | 测试证据与验收 Verdict 分离；未运行、未知和失败不折叠；Acceptance 由正确 Owner 完成 | 已定义；需覆盖完整用户旅程 |
| WS-08 | Report / Publication Receipt | 报告能追溯 Item、Run、Evidence、Revision、Recipient；生成成功不冒充投递成功 | 已有生成/回执模块；实际 Transport Consumer 需验收 |
| WS-09 | Memory Reconciliation | 结果和更正能更新当前认识；Provider 失联明确降级；历史 Evidence 保留 | 设计存在；OpenViking 等 Provider 需实测 |

### 5.2 Browser / Credential 需求

| ID | 需求 | 验收条件 | 当前判断 |
|---|---|---|---|
| BR-01 | Browser Capability Discovery | 每台设备分别报告 Browser Binary、Playwright Runtime、Provider 的 Supported / Installed / Ready / Authorized / Consumed / Journey-Verified | 通用 Ability 投影存在；Browser 专项合同缺失 |
| BR-02 | Browser Session Lifecycle | 可创建、Attach、Inspect、Renew、Close；每个 Session 有明确 Device/Agent/Work/Run/Provider 归属和 Receipt | MUC 有局部 Broker；Fleet 通用生命周期缺失 |
| BR-03 | Profile / Context Isolation | 两个 Agent 并行运行时 Cookie/Storage 隔离；Persistent Profile 独占；Storage State 按授权 Clone-per-Run | 资源分类已定义；通用执行侧尚需集成 |
| BR-04 | Browser Resource Monitoring | 能将 Chrome 主/子进程、CPU/RAM、Page 数、CDP 健康、崩溃和 Profile 磁盘占用归属到 Browser Session / Agent / Run | 通用资源监控存在；Browser 专项观测缺失 |
| BR-05 | Provider Orchestration | Claude/Codex 等 Agent 能按策略使用 Playwright、MCP、Browser-use、OpenCLI、CDP 或 Computer Use；无静默跨权限降级 | Ability 目录有候选；Fleet 统一编排缺失 |
| BR-06 | Credential Broker | Browser Session 只获得短时 Credential Lease；按 Origin、账号、设备、Agent、Work Item 和动作授权；支持撤销和审计 | 没有发现通用网站 Credential Broker 合同 |
| BR-07 | GUI Control Boundary | Browser DOM 权限与整台桌面 `gui-control` 权限分离；Computer Use 需要独立授权 | 有 GUI Capability Level；细分策略待定义 |
| BR-08 | Browser Audit / Evidence | 能记录操作意图、目标 Origin、主体、Provider、授权、关键结果和 Evidence Locator；不记录秘密或无关页面正文 | MUC E2E 有 Evidence 模式；通用 Fleet 记录合同缺失 |
| BR-09 | Website Registry Integration | 可通过 `website_ref` 解析允许 Origin、项目、环境、仓库、凭据引用和动作策略 | 未发现 Fleet 通用 Website Registry 接口 |

## 6. 安全、隐私与稳定性要求

### 6.1 凭据与浏览器隔离

- Secret、Cookie、Authorization Header、Storage State、Local Storage 和完整 Profile 都视为凭据或敏感状态。
- 凭据值不得出现在命令行、Agent Prompt、MCP 回显、环境诊断输出、日志、Git、PR、Memory 或报告中。
- Browser Endpoint / CDP Port 本身可授予页面控制能力，应限制为本机或受控传输，并按 Browser Session 做授权。
- Profile 目录必须有明确 Owner、权限、保留策略和保护级别；共享机器必须避免不同 OS 用户互相读取。
- 用户日常 Chrome 与 Agent 隔离 Profile 不得混用；显式 Attach 后仍须保留会话归属和撤销能力。

### 6.2 网页内容是不可信输入

- 网页文字、DOM、下载文件和嵌入提示都视为不可信内容，不能改变 Agent 的 System/Developer 指令、Grant、Work Scope 或审批规则。
- 网页中的“请粘贴密钥”“忽略之前指令”等内容不能构成授权。
- Origin Allowlist 和可执行动作由 ServerOS Policy / Credential Broker 决定，不由页面内容决定。
- 上传文件、下载文件、剪贴板和本地文件选择器需要明确的文件系统 Scope；浏览器访问权限不等于任意本地文件权限。

### 6.3 运行稳定性

- Browser Process、Browser Session、Context、Profile、Provider 和 Agent Session 的健康状态分别观测。
- 健康至少区分 Liveness、Transport、Functional、Readiness；进程存在不等于页面可操作。
- 资源租约必须有明确过期、续期、撤销和失联处理；UNKNOWN 不能解释为 FREE。
- 同一 Profile 同一时刻只有一个写入者；同一 Browser Session 的并发操作必须串行化或显式拒绝。
- Provider Timeout 不等于 Browser 崩溃；CDP 可达不等于页面功能正常；页面登录成功不等于凭据永久有效。
- 高 CPU/RAM 只产生 Observation / Alert，不自动杀进程；资源回收必须遵守 Ownership / Protection / Lease / Approval。
- 浏览器运行监控不得把 Agent Session CPU 与 Browser Process CPU 重复计入同一资源预算。

## 7. 仓库职责与实现归属

| 仓库/系统 | 负责 | 不负责 |
|---|---|---|
| `serveros` | Fleet 控制面、Device/Runtime/Browser Session Binding、资源租约接缝、运行观测、权限/审批、审计和结果回执 | 浏览器自动化脚本全集、网站业务内容、第二套凭据库 |
| `ability-management` | Playwright / OpenCLI / Browser-use / Computer Use 等 Ability 身份、来源、版本、分发、Skill/MCP 和 Provider 文档 | Fleet 设备真实状态、Work Item 状态和授权 |
| `hwz-records` | 当前结构化认识、Source 声明、项目关系、需求、决策、Website Registry 的业务记录、设计和报告 | 运行时 Browser Session、实时进程状态和 Work Engine Item 状态 |
| `hwz-test-envs` | 可重复、隔离的 Browser/E2E 测试环境生命周期 | 生产用户 Profile 和生产凭据 |
| `fleet-journal` | 脱敏后的任务元数据、Session Ref、时间线和执行回执 | 原始 Session Transcript、Cookie、Token、页面正文 |
| Credential Center / Secret Provider | 网站账号、Secret、Credential Lease、撤销和轮换 | Work Engine 工作状态和 Browser 进程管理 |
| `professorzhi.fun` 网站项目 | 网站矩阵、DNS、TLS、反向代理、应用、内容、部署和网站业务授权策略 | Fleet 的跨设备 Agent 调度与 Browser Runtime 生命周期 |

### 7.1 Ability 映射

Browser Automation 是横向执行能力，不新建顶层 Ability。建议映射：

- G1：Provider 身份、来源、版本、兼容性、分发和安装。
- A1：Browser Session 与 Agent Session 的连续性和交接。
- A4：Browser Process、Profile、Context、资源预算、健康和 Lease。
- A5：选择设备、传输、远端 Browser Runtime Binding。
- A7：Browser E2E、视觉验证、页面证据和验收。
- B1：浏览器作为授权网页来源的采集入口。
- B5：网页调研与证据采集。
- B7：通过网页完成被授权的实施或运维动作。
- B9：维护网站文档、知识页和已授权的外部文档。
- B10：敏感提交、登录挑战、审批和人工接管。

产品树仍保持 21 项闭集。若未来确实需要新增产品职责，应修改 `ability-management/product-tree.yaml` 并走独立 Review；不能仅因出现 Browser Runtime 就绕过产品词表治理。

## 8. 建议建设顺序

以下是需求拆解建议，不是已接受的 Work Engine Priority：

### Stage 0 — 先修复工作系统的真实映射

- 核对 `hwz-records` 项目索引、ServerOS 项目入口、Source 声明、项目理解和 Item Cards。
- 配置并验证 Hub 的 records root；只读对账 Work Engine 实际 Item、Activity、Run 和 Board 投影。
- 对账 Feishu IM / Docs / Wiki 的声明、最新增量游标和 Coverage；区分历史归档与实时增量。
- 验证 Understanding Writer、Memory Provider、Daily Focus、Report Transport 的真实消费者。
- 形成一份可读的 Work System Map，放在 `hwz-records`，ServerOS 只保留机制和合同。

### Stage 1 — Browser Runtime 最小可用闭环

- 复用 SYS-02 Resource Lease，不新建租约系统。
- 在一台隔离测试设备上发现 Chromium/Playwright Runtime，形成 Installed/Ready/Authorized 观测。
- 以每 Run 独立 Browser Context 执行一个可重复的测试页面流程。
- Browser Session 绑定 Agent、Work Item、Run、Device 和 Provider。
- 形成 CPU/RAM、进程树、Context 数量、崩溃和 CDP 健康的只读监控。
- 先不接生产用户 Profile，不自动提交敏感外部表单。

### Stage 2 — Provider 统一与 Credential Broker

- 把 MUC Browser Broker 的 Provider Discovery/Selection 经验提炼为通用 Ability Provider Contract。
- 接入 Playwright Library/CLI、Playwright MCP、Browser-use；OpenCLI 仅在目标设备真实安装并验证后启用。
- 定义 Credential Center Provider Contract、Credential Lease、Origin Allowlist、MFA/人工接管和撤销。
- 增加 persistent profile exclusive lease 与受控 CDP/Extension attach。
- 完成两个 Agent 并发隔离、同一 Profile 冲突拒绝、凭据撤销和会话过期验收。

### Stage 3 — Computer Use 与网站矩阵

- 把 Computer Use/RDC 纳入独立 GUI Control Provider，不混入 Browser DOM Grant。
- 定义 Website Registry 和 `website_ref` 解析接口。
- 在 `professorzhi.fun` 独立项目中登记站点、仓库、环境、域名、Credential Ref 和动作策略。
- 选择一个低风险网站完成浏览、登录、填表草稿、人工确认和结果回执的完整旅程。
- 之后再扩展到发布、管理后台和多站点批量操作。

## 9. 端到端验收旅程

| Journey | 过程 | 通过条件 |
|---|---|---|
| J1 信息到工作 | Feishu/GitHub 新变化 → Snapshot → Understanding → Candidate → 接受为 Item → Board/Focus | Source Revision 可追溯；重放不重复建 Item；未知覆盖不报成功 |
| J2 工作到报告 | Item → Research/Design/AC → Agent Run → Test Evidence → Acceptance → Report → Publication Receipt → Memory | 每个阶段有正确 Owner；测试与验收分离；投递回执真实；结论可召回 |
| J3 Agent 独立浏览器 | Agent A/B 在同一设备并行启动 Browser Session，分别访问测试站点 | Context/Profile 隔离；CPU/RAM 归属正确；无 Cookie 互串；Run 结束只释放自己的资源 |
| J4 Persistent Profile 独占 | Agent A 持有登录 Profile Lease，Agent B 请求同一 Profile | B 被明确拒绝或排队；A 结束后经裁决释放；Profile 不因最后一个引用退出而被误删 |
| J5 凭据撤销 | Agent 获得短时 Credential Lease，Owner 撤销后再次访问 | 新操作被拒；Secret 不出现在日志；历史审计不泄漏 Cookie/Token |
| J6 用户 Chrome Attach | Owner 明确授权 Extension/CDP 接入一个已登录 Chrome | 只暴露批准的 Browser Session；可撤销；不扫描或接管其他 Profile |
| J7 GUI 后备 | Playwright 无法操作系统文件选择器或原生窗口，申请 Computer Use | 需要独立 gui-control Grant；Browser Grant 不足以执行桌面操作 |
| J8 网站矩阵 | Agent 通过 `website_ref` 打开一个 `professorzhi.fun` 子站并执行获准动作 | Origin、环境、仓库、Credential Ref 和动作范围均来自 Registry；敏感提交有确认与 Receipt |

真实旅程必须在目标设备、真实 Agent Runtime、真实 Browser Provider 和真实权限下验收。离线单元测试只证明契约，不证明设备已安装或 Browser 已可用。

## 10. 需要 Owner 后续确认的决策

1. **Persistent Profile 默认归属：** 个人用户、设备账户还是特定 Agent？本文建议 Profile 归属明确的用户/设备身份，Agent 通过短时 Lease 使用，不让 Agent 永久拥有个人登录态。
2. **Credential Center 的权威系统：** 需要确认组织已有的 Secret/Vault 服务及其 API；本提案不预设产品或仓库。
3. **Browser 首批目标设备：** 建议先选隔离测试设备完成 Stage 1，再扩展 Tokyo、A6000 和 Windows GUI 节点；不得默认在共享生产工作区启动新的 Browser Profile。
4. **用户 Chrome 接入方式：** 建议先支持 Agent 自有隔离 Browser，再通过 Extension/CDP 做明确授权的用户会话接入。
5. **敏感网站动作策略：** 发布、付款、删除、批量发送、权限管理等动作需要逐类定义是否始终人工确认，还是允许带预算和有效期的委托。
6. **个人网站矩阵：** `professorzhi.fun` 的子域名、托管平台、DNS、认证和部署结构由独立 Website Project 决定，本文不提前替 Owner 做技术选型。

## 11. 相关权威文档

- [Fleet V1/V2 需求与架构](../architecture/FLEET-V1-V2-ARCHITECTURE.md)
- [Fleet 四层架构](../architecture/FLEET-FOUR-LAYERS.md)
- [21 项 Ability 需求合同](../architecture/ABILITY-REQUIREMENTS.md)
- [信息与文档生命周期](../INFORMATION-DOCUMENT-LIFECYCLE.md)
- [每日工作循环](../daily-work-cycle.md)
- [Ability 运行模型](../ABILITY-OPERATING-MODEL.md)
- [Memory Fabric](../memory-fabric.md)
- [监控产品架构](../MONITORING-PRODUCT-ARCHITECTURE.md)
- [资源环境契约](../../runtime/environment-contract.yaml)
- [资源租约实现](../../runtime/lease_store.py)
- [Fleet MCP 契约](../../fleet/mcp-contract.yaml)
- [Transport 与 GUI 权限契约](../../fleet/transport-contract.yaml)
- [Ability 产品树](https://github.com/ProfessorZhi/ability-management/blob/main/product-tree.yaml)
- [Playwright MCP Ability](https://github.com/ProfessorZhi/ability-management/blob/main/mcps/playwright-mcp/ABILITY.md)
- [MUC Playwright E2E Skill](https://github.com/ProfessorZhi/ability-management/blob/main/skills/muc-playwright-e2e/SKILL.md)
- [Browser Resource Policy](https://github.com/ProfessorZhi/hwz-records/blob/main/ops/workspace/serveros/resources.yaml)

---

**本提案的完成标准：** Owner 评审并确认需求边界；随后将接受的条目拆成 Work Engine Item / Activity，按阶段实施。本文本身不创建授权、不改变设备配置、不声明 Browser Runtime 已部署，也不替代真实验收。
