# Fleet V1：设备运行底座、统一 MCP、Operator Skills 与工作交付

> **这篇回答什么：** 一个由多台设备组成的 Fleet，每台机器上分别跑什么、边界在哪、跨设备工作靠什么约束；以及 V1 验收到哪里结束、V2 从哪里开始。
>
> **状态：** 设计基线 2026-10-16，2026-10-17 修订完整 V1 范围。这是**范围与拓扑决策，不是已部署验收记录**；线上功能、授权与 Runtime 能力以各自现时回读为准。
>
> **怎么读：** §1–§3 是产品定义与三类设备的分工；§4 是客户端插件与设备能力的区别；§5 是一条工作如何跨设备流动、文件落在哪；§6 是 V1 验收门与 V2 分界；§7 是依赖的既有规范。

**当前实施范围：** [Complete Fleet V1 Delivery Campaign](../plans/plan-02-complete-fleet-v1-delivery-campaign.md) · [Issue #244](https://github.com/ProfessorZhi/serveros/issues/244)。三设备 Native Core + 网络/服务自愈 + CC/Codex/CloudCLI + Device Ability + Fleet 工具面 + 本地 CC Plugin/Skills 构成 V1 的功能范围。未安装或未授权的能力必须明确列为 BLOCKED，不能因为设计存在就当作已接通。开发优先 Tokyo/Dell，Windows 只做最终必需的原生验收；ChatGPT App2 与插件注册不在当前关键路径上。

**V2 专题：** Agent 身份、Session 资源化、Assignment、分层委派与消息通信只在 [Agent Identity, Session Resources and Multi-Agent Delegation](AGENT-IDENTITY-SESSION-DELEGATION.md) 中定义。本文只定义 Fleet V1 的执行拓扑与验收边界，不再建第二份 V2 身份规范。

## 1. 产品定义：各设备 ServerOS 加速原生 Agent，Fleet 连接它们

这一节定义 Fleet 与 ServerOS 的关系：每台设备自己加速原生 Agent，Fleet 只负责连接，而不是让东京的模型代替所有机器推理。

ServerOS 在每个受管设备/OS 用户内增强 Claude Code 等原生 Agent 的工作条件：清晰的资源与健康事实、正确 Workspace/Context/Ability、Runtime 准入、可信授权、工作证据，以及对指定服务经批准的安全自启/自愈。它**不重写模型解码器、不在 C++ Core 中运行 LLM 业务决策、不接管 Claude Code 的原生工具**。

Yale CloudCLI（@yalehwang/cloudcli）是目前可用的原生 Agent Session/工具接入途径之一；Node Connector/Provider 按实际版本与能力把设备上的 Runtime 暴露给东京 Fleet。**Fleet MCP 是一个对外控制接口**，不是单个超级操作系统账号：可发现注册设备、当前 Runtime、Session、Capability 和状态，只允许经设备、OS user、Runtime、project、verb、principal、grant 多层交集批准的操作。

~~~text
   Human / ChatGPT Chat / Claude Chat / Tokyo Claude Code
           |  客户端独立 MCP 连接、认证和薄 Operator Skills
           v
   Tokyo · Fleet Next MCP（发现、会话寻址、策略、结果路由）
           |
           +---- Tokyo  / vince   · Work Node
           +---- Dell   / fjd     · Compute Node
           +---- Windows          · Edge/Client Node
                (可按受权注册第 4、5、N 台设备)
           |
           各受管实例：OS service manager + Native Core
                     + Fleet Node / CloudCLI/Provider
                     + Agent Runtime + Device Abilities
~~~

这不是“东京一个模型代替三台机器推理”。东京保存工作权威、协调与交付；实际代码、推理、测试、图形/网络操作由合适的设备和原生 Agent 执行。**一个统一 MCP 入口不能消除控制面可用性风险**：节点在东京不可达时只能在已有授权与资源预算内维持局部安全行为，不能偷偷扩权或发明新的主工作状态。

## 2. 每个设备 ServerOS Core / Node / Runtime / Ability 的边界

这一节列出每台设备上各组件的边界，以及「进程存在、能接通、功能可用、已验收」这四种不同的事实。

| 组件 | 负责的真实问题 | 明确不拥有 |
| --- | --- | --- |
| **Native C++ Core（A4）** | OS/用户身份、CPU/RAM/磁盘/资源压力/GPU（有合法权限时）、服务与隧道健康、新鲜度、本机受控自启和有限恢复收据 | Work/需求/日报状态，LLM 任务分解，跨账号读数 |
| **OS Manager** | 进程和服务的启动、停止、守护及受信状态（Linux systemd、Windows SCM/Task Scheduler 等） | 任意 Agent 对服务的临时授权 |
| **Fleet Node / Connector** | 受管身份、签名和固定路由、状态/操作的安全数据边界，按 owner 配置取得受信源 | 任意 caller 提供的文件路径、shell 命令、跨用户 Token |
| **CloudCLI / Native Provider** | 真实 Session/Turn 能力、Provider 的可用性与结果；每账号/Runtime 独立配置 | 业务 Work 最终验收和设备全部权限 |
| **Device Ability / G1** | 实际 Skill/Plugin/MCP/CLI/Provider 实现与来源版本，按需发现、安装及调用 | 一份按设备复制的第二注册表 |
| **Fleet MCP** | 对外工具、Session/Runtime/设备路由，认证、Grant 和拒绝，返回有归属的 evidence | 设备本机进程主管、统一机器超级用户 |
| **Work Engine/Parent** | 目标、Item/Activity/Run、批准范围、Checkpoint、接受与交付 | 原生 Provider Session 生命周期 |

Native Core 的“进程存在”、Provider“能接通”、业务服务“功能可用”和 Work“已经验收”是**四种不同事实**，不能互相代替。UNKNOWN/NOT_CONNECTED/NOT_INSTALLED、STALE、NEEDS_LOGIN 和 FAILED 要保留不同含义。

### V1 自启与有界自愈

V1 **必须**拥有对已明确批准的本用户服务的本机受限恢复能力（例如 CloudCLI、Fleet Connector/必要 Tunnel、受管 Tailscale 或特殊访问隧道），不是推迟到 V2。具体生产单元是否允许自动行动，须按 owner、unit 身份、allowed verbs 和变更授权单独启用。

执行前要求：受信的安装与期望状态、明确 unit allowlist、足够新鲜的故障证据、真实 busy-Agent 状态、单写者锁与持久恢复预算/退避/熔断。人工 disabled/masked、账户 NeedsLogin、ACL/DNS 故障、未知源与跨用户对象均不能盲目恢复。**当 Tailscale 等网络依赖本身崩溃时，需要本机离线 OS/Core 有界恢复通路**，不可要求已经断线的远端 LLM 通过同一隧道重新救它。

服务管理器返回 active 只是 liveness；CloudCLI/MCP、模型或隧道仍须按功能协议重新探测。故障演练先使用隔离 toy service；toy PASS 不等于获准重启真正的 Fleet、CloudCLI 或 Tailscale。


### 完整 Fleet V1：每设备 / 每授权 OS principal 的 ServerOS 实例

每台物理机可以有多个受管 OS principal，每个受管 principal 至多有一个生产写入 Core 实例；同一 OS principal 下多个 Claude Code/Codex Session 共享本地资源观测而不共享私密会话历史。Core 必须依赖 OS 服务管理器启动/监督，本机状态健康、受信读回、Credential Owner 和实际工作授权独立计算；Linux 是 per-user systemd / owner policy，Windows 使用可信 SCM/TaskScheduler adapter。迁移期间不能由 C++ Core 和现存 Python serverosd 同时恢复同一 unit。

各节点的最小共同运行层包括：进程和服务生命周期、CPU/RAM/Swap/PSI（支持时）、磁盘/IO、GPU（仅已授权设备）、隧道/网络可用性、服务级 readiness、恢复/拒绝 receipt、Node Connector、CloudCLI、可证实的 Claude Code 与 Codex Runtime/Session、有效 Ability 安装与执行证据。任何无法受信测量的项目保持 UNKNOWN/UNSUPPORTED。

| 节点与实际主责 | V1 节点能力重点 | 不混淆的责任 |
| --- | --- | --- |
| Tokyo / vince | Work & Fleet 控制入口、GitHub 集成、Feishu 信息/报告、Linux Core/CC+Codex/CloudCLI、能力注册与发布 | 保护共享机器其他 OS 用户的活跃 Agent/服务，不将同主机存在的账号视为有授权 |
| Dell A6000 / fjd | Linux Core、受控资源/GPU 遥测、模型/测试/批处理 Device Ability、CC+Codex/CloudCLI 与隔离编译测试 | 未获准不得触动 Strata、GPU 生产任务或借用别人的账号/令牌 |
| FJPCOS Windows | Windows Core/SCM、Task Scheduler、SSH 转发、Selkies/Claude Web 客户端、Tailscale、Remote Desktop Control、CC+Codex/CloudCLI 与 Windows 专项 Ability | 开发源代码/Mock 优先在 Tokyo/Dell；Windows 只做确认必要的短时原生测试 |

### 每台设备的服务/网络健康与自愈矩阵

| 受管目标 | 需要测量的事实 | 允许的恢复边界 |
| --- | --- | --- |
| CloudCLI / CC / Codex / Node Connector | OS principal + service/transport 健康 + Provider Run + 实际账号/模型/PermissionMode | 本机 owner 已授予的精确 unit，重启后重新进行应用协议层 readback；不可自动重新登录 |
| Tailscale / Fleet 隧道 | 本机 service、链路/路由、鉴权、新鲜度以及与关键远端探测分开 | 网络断开时仅本地 Core 可用现存 standing grant 修复已登记的本地连接服务，不能远端自授权 |
| SSH 隧道 / Selkies | Windows 声明的浏览器入口 http://127.0.0.1:16095/ → 本地 listener → 受管 SSH forward → 上游 Selkies/Claude Web → 实际交互流 | 16095 是用户给的待验证本地端口，不是已证实的服务名/上游路径；Windows 只处理自己的隧道，远端服务由其实际 host Core 维护 |
| Remote Desktop Control | 本地 Adapter / transport 状态、可用权限、客户端功能 | 与 Fleet 原生 Session 管理分离，拒绝“可连接＝可操作”，绝不凭工具存在赋予桌面控制 |
| Core 自己 / 监控管道 | OS manager、版本、采样时间、新鲜度/缺口、恢复 budget/circuit breaker | OS manager 管 Core 本体；Unit ACTIVE 与服务 READY/用户最终可用不是一个状态 |

恢复策略须具有 owner、exact service ID、desired-state、start-on-boot、probe、allowed verbs、TTL、频次/预算、maintenance/manual disable、agent busy 与恢复后验证；无授权保持 observe-only，不尝试重启业务 Agent/模型服务。

## 3. 三类设备分工，新增设备无需重做 Plugin

这一节说明三类设备各自做什么，以及新增一台设备要走哪些步骤，而不是复制插件。

| 节点 | 主要职责 | 约束 |
| --- | --- | --- |
| **Tokyo（共享 Work Node）** | 飞书信息增量、结构化认识、Work/版本/项目主工作区、主要开发与集成、Fleet Gateway、日报归档和汇报 | 保护共享机其他 OS user/Agent，不默认接收最敏感个人资料或任意生产原始日志 |
| **Dell A6000（Compute Node，fjd）** | GPU、模型评估、通用开发任务的副执行、MarkItDown/批量文档、受限回归、按自身权限的生产日志观察 | 按 Work/Run 使用隔离临时 checkout；不复制长期 Work 权威；模型/服务受资源预算保护 |
| **Windows（Edge Node）** | 本机 GUI/Windows API/企业专有工具、必要客户端测试、特殊 SSH/noVNC/Tailscale/RDC 访问和轻量协调 | 普通开发不因设备在线就派过来；高敏本地材料不自动移出 |

A6000 位于可用内网不等于获得生产观察权限。**生产日志 Ability 的源代码、契约和路由在 G1/东京管理，获授权的 Dell Ability/生产端执行有界只读查询**；优先源端过滤、脱敏并把结构化结论与证据 locator 交回东京。需要 Windows 特定凭据/GUI/本机环境时仍留 Windows，不因为“减负”突破数据许可。

Windows→东京 Claude Web/noVNC 特殊隧道和 Remote Desktop Control 是 Edge 的访问能力；服务本体及 Work 应在适当受管节点，即使 Windows 关机，东京的 Work 和 Dell 的计算任务也不应被取消。RDC 只作图形操作/故障兜底，不能代替正常 Fleet/CloudCLI 的原生 Session 控制。

新增设备流程：注册 stable device_ref → owner/OS user/Runtime Profile/credential Binding → 可信 transport → Ability/Capability/Readiness → 精确 verb grant → 真正的 Session/Core/E2E 读回。不能靠已存在的其它设备成功结果来证明新增节点“健康”。

## 4. Operator Plugin 与设备 Ability 的关系

这一节区分客户端插件里的 Operator Skills 与设备上的 Ability，避免把它们混成同一张能力表。

面向 ChatGPT/Claude Chat/本地 Claude Code 的 **Fleet Plugin 只做薄客户端适配**。其概念组合是：

1. 一个可认证的远程 Fleet MCP 入口和当前真实工具表面；
2. 少量共享来源的 Operator Skills：查询设备与 Session、核对 Runtime/模型/项目/授权、优先复用可安全接续的 Session、精确派发、轮询真实 Run、读取证据、必要时上报阻断；
3. 客户端特有的 manifest、MCP/OAuth 配置、资源/说明；各客户端的打包格式可不同，能力合同不能分叉。

**Operator Skills 不等于 Device Abilities**，不能把全部机器内 Skill/CLI 的正文复制到 ChatGPT 里。当前能力权威是 [ability-management](https://github.com/ProfessorZhi/ability-management) 的 manifest；同一 Ability 的来源/Revision 只注册一次，设备 Installation/Ready/Supported 则逐个观测。一个 Agent 是否能调用，先核当前 Ability × Provider × Device Binding × Runtime × Target × Health × Authorization。

工具面里的条目数不等于“可做的写操作数”；六个 Scope 只是可能的权限类，与发给此 token 的 Scope、device allow flags、本地 owner floor、Work/OS 协议分别核验。权限、默认模型和 permissionMode 必须区分 requested / resolved / effective；不能因为 CLI 参数包含“default”或某配置文件缺失就声称实际模型是既定 DeepSeek。

**V1 当前的第一个正式客户端验收者是东京本地 Claude Code Fleet Plugin**（一个经真实认证的 MCP Server + 一套共享 Operator Skills 源），并需要至少验证实际工具发现、负面授权、受管 Session 生命周期、本机 Core/网络健康、Device Ability 调用和原始回执。未来 ChatGPT/Claude Chat 的格式/OAuth/图标/飞书 App2 属后续部署工作；不能因为 CC 本地通过就声称 ChatGPT 已安装。CC Plugin 是 Fleet 使用指南而不是 Device Ability 的复制件。

Operator Skills 的初始五个核心入口分别是 device-and-session-discovery、reuse-idle-session、create-with-grants、evidence-review 和 safety-boundaries；按需以 references 扩充运行时/模型核验、Tailscale/SSH/Selkies/RDC 故障分析、恢复审批、Ability route 和版本回滚，不复制新 registry。

## 5. 主要业务流程在东京自动接续，而不是固定步骤仪式

这一节给出一条工作跨设备流动的路径，并规定文件落在哪里、谁是权威。

~~~text
飞书与批准的其他来源
  → B1 Source ID / revision / cursor / intake receipt
  → B2 可更正的 Claim / Structured Understanding
  → B3 候选 Requirement / 重要决定经 Human 接受
  → B4 Work Item / 已获批准的 Focus
  → A1 Session + A3 Workspace + A4 Core/Runtime + G1 Ability
  → B5/B6/B7 + A6/A7/A8 的按需执行、独立证据/验收
  → B8/B9/B10 日报/文档/真实消息投递回执
  → 人类反馈经来源及修订版本回流
~~~

Work Item 不等于 PR；Provider 的 Send ACK 不等于 Run 完成；worker 自称 DONE 不等于 Parent 验收；写出报告文档不等于飞书收件人收到。每个跨边界交接要有稳定 source/work/run/attempt/ref、version、对应 owner、接受/失败/等待状态和 receipt。用户未授权新目标不能把“我有一个想法”直接提升为 ACTIVE Work。

此业务路径不是所有问题的强制 DAG：小任务可以直接实现和局部验证，没有实质新资料时不制造每日需求。OS Timer 负责唤醒可终止 routine，现有 serveros-daily.service 目前的源码职责只是 Workbench render，**不能据此声称完整的每日飞书→Agent→日报流程已 LIVE**。异步交接需要真实 receiver：若还需人在另一个 CC Session 输入“继续”，则不能称自动。

### 文件落点与权威

沿用 [目录收敛合同](DIRECTORY-CONVERGENCE.md)，不创造 Fleet 专属第二棵 Work 树：
- workspace/src（canonical 源码）、worktrees（隔离实施）、test-envs（定义）、ops（部署/Binding/map）、records（raw/understanding/project Item）、knowledge（可复用知识）；
- 受管 artifacts/work-items/<project>/<ITEM>/<RUN>（证据和临时执行产物）、state/serveros（原有机械控制态）、独立受保护 credential store；
- 项目真实 Item/Activity/Run 仍由 [work-engine](https://github.com/ProfessorZhi/work-engine) 及 records 的约定权威持有，东京不和 Dell/Windows 复制第二当前状态库。

Dell 任务临时目录可以按 owner/Work/Run 做已结束工件 dry-run 清理；活动 Session、未提交 worktree、唯一证据、凭据、模型权重和其它 OS user 文件不得自动删除。目录的“整齐”靠从生产者第一笔写入就遵守放置规则，而不是事后全盘 mv。

## 6. Fleet V1 验收与后续 V2 分界

这一节列出 V1 的验收门，并说明 V2 新增什么、哪些不回填为 V1。

**V1 验收门：**
- 经授权设备/OS user 上 Native Core 的实际采集、身份/新鲜度、服务与资源状态、受管重启后自启、必要服务的有界恢复/拒绝及复测；生产动作仍需要各 unit 单独批准。
- **CC-first 里程碑**：Claude Code + CloudCLI/Fleet 的实际多 Runtime 寻址、发现/创建/发送/读取/中断/继续所支持的生命周期，以及项目/有效模型/permissionMode/账号/授权负测；**完整 V1 验收还必须包含真实 Codex 与 CloudCLI/Node/Fleet 的三机相应 Runtime/Session 支持（真实支持/授权的 profile），不得用 registry 声明或 Windows mock 充当验收。**
- **首先**在东京本地 Claude Code 真正加载同源 Fleet MCP+Skills，完成 authenticated initialize/tools/list、真实设备只读/受权隔离 Session 操作、Core/网络健康和 Device Ability 访问、负例与对应 Run 证据；待 Fleet V1 主体完成后，再分别开展 ChatGPT 和 Claude Chat 的客户端注册/同意验收。
- 至少一条真实飞书来源改变 → 有权限的认识/需求/Work → native 执行/测试/Outcome → 报告/发送/回读的闭环，不依赖手工逐层粘贴 Agent 结果。
- 新设备和断线、旧 Runtime、未登录账号、资源紧张等场景精确拒绝；生产发布与回滚取得独立验收。

**V2 新增但不回填为 V1：** 稳定跨设备 Logical Agent/Role Principal 身份、动态 Session 资源池、复杂 Assignment/Activation 派生视图；专业 Agent 间的受治理通信、浅层 Parent 分工，以及 [openagents-org/openagents-sdk](https://github.com/openagents-org/openagents-sdk) 作为可替换 A6 委派 Provider 的独立实验。V2 的详细合同只见 [Agent Identity / Session / Delegation](AGENT-IDENTITY-SESSION-DELEGATION.md)；不在此复制新的 Agent schema、业务数据库或第二个调度器。

截至本文决策日，候选源码、PR 与合成/toy 验收**不等于三设备生产部署或本地 CC authenticated E2E 已通过**。[Fleet 完整 V1 Campaign #244](https://github.com/ProfessorZhi/serveros/issues/244)、[完整 V1 实施计划](../plans/plan-02-complete-fleet-v1-delivery-campaign.md) 和 [早期计划 Issue #189](https://github.com/ProfessorZhi/serveros/issues/189)、[Ability Journey #190](https://github.com/ProfessorZhi/serveros/issues/190) 和各自真实回执是实施范围与证据入口。

## 7. 直接依赖的既有规范

这一节指向本文依赖的规范；具体语义以那些文档为准。

- [总架构](../../ARCHITECTURE.md)、[Ability Operating Model](../ABILITY-OPERATING-MODEL.md)（21 项职责）、[Native 工作环境](NATIVE-WORK-ENVIRONMENT.md)。
- [Agent Session 合同](../../runtime/agent-session-contract.yaml)、[Agent Runtime Model](../agent-runtime-model.md)。
- [Ability 设计树](https://github.com/ProfessorZhi/ability-management/blob/main/PRODUCT-ABILITY-TREE.md)、[Work Engine 合同](https://github.com/ProfessorZhi/work-engine/blob/main/docs/serveros-system-v1-work-contract.md)。
- [V2 Agent 身份与分层协作](AGENT-IDENTITY-SESSION-DELEGATION.md) 是 V2 语义唯一专题权威，不要另建同义设计。
