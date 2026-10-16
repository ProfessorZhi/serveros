# ServerOS Fleet V1 / V2：统一 MCP、逻辑 Agent、Session 资源与分层协作

**状态：架构决策 / DESIGN，2026-10-16。本文不是 CODE、CLIENT INSTALLED 或 LIVE 验收。**

本文件记录当前用户确认的 Fleet 产品边界，以及 V2 的身份和委派模型。它是 [ARCHITECTURE.md](../../ARCHITECTURE.md) 的 **Fleet 专题补充**，不得反向替换下列既有真值：Agent Session 控制合同、Work Engine、Ability Management 注册表、Node Core/OS 管理器或真正的 Provider 运行事实。当前工作方式的总体规范仍见 [Native Agent Work Environment](NATIVE-WORK-ENVIRONMENT.md)。其名称里的 V2 是该文档的修订称谓，**不是本文件的 Fleet 产品 V2**。

## 1. 目的与冻结决策

ServerOS 是增强每台设备上原生 Agent 的本地运行底座：提供可信节点事实、资源准入、已授权服务的本地自启/有界自愈、工作上下文与 Ability 解析；**不把 LLM、业务规划和通用调度塞进 C++ Core**。

跨设备由 Yale CloudCLI / Provider Adapter 与 Fleet Connector 连接；东京的 **Fleet MCP** 对外暴露统一、可授权的工具表面。ChatGPT Chat、Claude Chat 和东京本地 Claude Code 可以通过各自的兼容连接器使用同一服务。Plugin 由客户端所需的 MCP 连接配置和少量 Operator Skills 组成；Skills 教指挥 Agent 怎样发现设备/Runtime/Ability、申请许可、委托、追踪及验收，**不是复制各设备的 Ability 实现或凭据**。

**一个远程 MCP 入口不等于一个万能本机账号。** 设备、OS 用户、Provider、Runtime、项目、动作和授权各有单独边界。33 个声明工具不等于 33 项权限，六个 Scope 不自动批准设备写入或本机管理操作。Connector/Node 的最低许可及 Work/OS 的实际准入仍有效。

### V1 与 V2

| 维度 | Fleet V1 必须完成 | Fleet V2 计划，尚非现有能力 |
| --- | --- | --- |
| Agent Runtime | Claude Code / 已实测 CloudCLI Runtime 的完整受授权生命周期；不移除现有 Codex 契约和历史代码 | **正式纳入 Codex**：真实安装、可用性、模型、Session/Tool 适配及权限 E2E |
| 节点底座 | 每个受管用户的 Native C++ Core 身份/资源、服务状态、开机自启、批准服务的有界自愈；Windows 适配按本机真实 API 验收 | 扩展分布式资源治理、弹性容量与复杂协同 |
| Fleet | 注册新设备/OS 用户/Runtime、发现、明确身份与授权、会话控制、断线与证据；保持一个稳定 MCP 产品接口 | Agent 身份、团队/角色/Assignment、代理间通信及分层委派 |
| Abilities | G1 唯一 Registry、每设备真实 Provider/Installation/Readiness、按任务选择与复用 | 动态角色级 Ability 组合与更丰富协作策略 |
| 工作 | 东京 Work/Feishu → 需求/接受 → 开发/测试 → 收据/报告的真实闭环 | 多项目分层 Parent 和专业 Logical Agent 自主协作 |
| 人类入口 | 东京 CC 先加载 MCP+Skills 并做真实验收，随后才安装 ChatGPT/Claude Chat 所需适配 | 同源客户端接口之上的多 Agent 组织与协商 |
| OpenAgents | **不安装、不开启、不作为 V1 验收阻塞项** | 以 openagents-org/openagents-sdk 为候选的**可替换消息/委派 Provider**，先隔离 PoC |

“V1 不强制 Codex”仅修订本轮发布分母，不声称历史 Codex 安装或接口消失，也不代表 Codex 今日真的可用。所有设备/模型可用性必须通过在线 capability/版本/运行态证据判断。V1 的服务自愈 **属于 V1**，不因生产启用仍须批准而移到 V2。

## 2. 不可混淆的四层与两类 Plugin

~~~text
用户 / ChatGPT Chat / Claude Chat / 东京本地 Claude Code
           │ MCP Client + 薄 Operator Skills（客户端适配各自独立）
           ▼
Tokyo Fleet MCP Gateway（唯一外部控制入口 / grants / 发现 / 路由）
           │
           ├── Tokyo：Work Node（Feishu、Work Engine、主开发、报告）
           ├── Dell A6000 / fjd：Compute Node（有界 GPU、测试、转换、获准日志观察）
           └── Windows：Edge Node（本机专用 API/GUI/隧道/轻量协调）
                   每台节点：Node Core + Connector + 已安装的 Agent Runtime + Abilities
~~~

- **Node Core**：局部资源/服务/启动/故障事实，及 owner-approved 的有界恢复；本地离线仍可治理自身确切受管服务。
- **Fleet MCP**：安全跨边界路由，知道设备、会话和授权；不拥有第二份 Work/Ability 注册表，也不把远端自述当权限。
- **CloudCLI / Native Provider**：把 Agent 原生能力映射成可探测操作，区分 advertised capability、真实 readiness、当前授权和实际 response。
- **Operator Skills**：指挥 Agent 的使用说明；Local Ability 和 Provider 本体仍由设备上相应的 owner 管理。
- **Work Engine/Agent Control**：工作目标、状态、接受、租约、epoch、决策及收据权威；不随聊天 UI 或执行节点迁移而复制。

ChatGPT 发行 ZIP、Claude Code 开发插件，以及 Claude Chat 对远程 MCP 的连接形式可以不同，但都必须来自同一能力/工具契约和 Skills 单一来源，不创建两套业务逻辑。每个客户端的 OAuth 授权要独立验收；ChatGPT/Claude Chat 登录与品牌包缺失不能伪装成中央 Server LIVE 失败。

### 设备分工与数据边界

东京是共享主工作节点：公司飞书信息流、结构化理解、Work/设计、主要代码、版本、长期证据索引与日报。Dell A6000 是受 fjd 账号管理的副开发和尽量干净的计算执行节点；承接普通项目、测试、MarkItDown、GPU 及**经授权且具备内网访问**的生产只读诊断。临时 checkout 可回收，持久模型权重/受管 Core 数据不可当普通缓存清理。Windows 尽量只保留必须 Windows 的 API、GUI、企业客户端、特殊本地隧道及不能迁出的敏感数据；资源余量不是转移敏感数据的许可。

生产原始日志尽可能在数据源侧限窗、限字段、脱敏；不因 A6000 能连内网便复制全部秘密。东京/共享机不自动接收个人高度敏感素材。Windows 登录东京共享 Ubuntu 用户或管理共享网关的控制动作，也可由本机轻量入口发起，在受权东京服务端实际执行。Windows 关闭不能取消东京 Work/Dell Run。

设备新增遵循 device identity → owner/OS user/Runtime Binding → 可信 Node/transport → Capability/Readiness → 逐 verb grant → 真实 E2E 的程序，不硬编码设备数量。个人 Dell 没有有效 Provider 时保持 NOT_CONNECTED，不能借公司 Windows 结果冒充。

## 3. 四种身份必须分开

| 对象 | 权威与典型寿命 | 不是什么 |
| --- | --- | --- |
| **Work Item** | Work Engine/records：长生命周期目标、AC、Owner、Outcome | 不是 PR、设备、Session |
| **Agent Definition / Role** | Ability/角色元数据：职责、能力要求、行为和版本；跨设备 | 不是人类权限、进程、具体 Session |
| **Logical Agent（V2 新上层身份）** | 稳定 agent identity，负责同类工作，可跨 Runtime/Session 延续 | 不是现有 provider-native agent_ref 的简单改名 |
| **Assignment / Activation（V2）** | 对一个 Work/切片的具名临时任命，包含授权、epoch、预算、接收者 | 不是授权转授，不是无限自主任期 |
| **Provider Agent / Runtime** | 特定设备、OS user、Provider、账号/版本的真实安装与状态 | 不是 Logical Agent 的全球身份 |
| **Session** | Provider 管理的持续对话/模型上下文/工具状态/原生 locator，属于具体 Runtime | 不是 Work，不等于 PID，不等于 Workspace |
| **Turn / Run** | 一次请求的执行及可核实结果/副作用 | 请求被接受不等于 Run 完成 |
| **Workspace / Worktree** | A3 文件与 Git 写入资源，独立 lease | 不是 Session 的身份和唯一状态库 |

用户关于 Session 是“解码工作区域”的直觉有合理部分：它主要提供上下文与原生执行环境。但它不只是 token window；不同 Provider 可能保存对话、工具状态和可恢复 locator。Session 并不天然持有源代码目录；Workspace 是另一种资源。跨机器“同一工作接续”不自动等于“原生 Session 被无损迁移”。

**核心决策：Logical Agent 身份不绑定 Session；一次 Activation 必须明确绑定正在使用的 Session/Runtime、Work、writer lease 和 epoch。** 一个 Agent 可依次使用多个 Session，也可为独立任务创建多个 Assignment；同一 Session 同时只有一个被授权的 mutating controller，同一文件写入域遵守单写者。Role 名称不产生权限。

当前 [runtime/agent-session-contract.yaml](../../runtime/agent-session-contract.yaml) 的 agent_ref/session_ref 与设备和 Provider 相关，且 execution Session 的 Work Item 绑定在创建时不可变。V2 应**新增版本化 Logical Agent/Assignment 映射**，保留已有地址与不可变约束：不把现存 session_ref 重解释为跨设备 Agent ID，不在执行中重指向另一个 Item。

### V2 Assignment 提案（非运行 API）

~~~yaml
schema: serveros/logical-agent-assignment.proposal.v0
assignment_ref: "<stable-assignment-id>"
logical_agent_ref: "<stable-agent-id>"
role_ref: "<versioned-role>"
work_ref: "<existing-canonical-work-ref>"
parent_assignment_ref: null
epoch: 1
input_revision: "<immutable-source-or-work-revision>"
target:
  device_ref: "<enrolled-device>"
  os_user_ref: "<authorized-user-binding>"
  runtime_ref: "<observed-provider/runtime>"
  session_ref: "<resolved-native-session-or-null-before-binding>"
  workspace_ref: "<owned-worktree-or-null>"
grants_ref: "<existing-admitted-effect-scope>"
budget_ref: "<existing-bounded-resource-budget>"
acceptance_ref: "<work-acceptance-contract>"
checkpoint_ref: null
~~~

各字段只是待定义的适配契约，**不得直接写成新 SQLite 数据库/真实 MCP 工具或已注册 ID**。优先扩展现有 Agent Control / Work Engine 的少量引用与事件。Permission 由 owner、Work scope、设备/Runtime、本机 Provider 的交集决定，不能从父 Agent 或 Role 名称无条件继承。

## 4. Session 作为资源：租借、迁移、续接和释放

1. Resolve：从 Work/Role 所需 Ability、数据边界、已有 Session、设备/Provider capability 与新鲜资源指标中选择有许可的目标；UNKNOWN 不伪装 READY。
2. Admit：检查 input revision、模型/账号、设备/OS 用户、scope/verb、预算、Workspace 独占写入域和 active leases；不要自行提升 permissionMode。
3. Bind：为 Assignment 记录 session_ref、activation epoch、writer owner 和 checkpoint；单个 Provider Session 可以复用，但不能隐式重指向别的 Item。
4. Execute：一个 Session 可接受多个顺序 Turn；request accepted、turn settled、result validated、Work accepted 分别记 receipt；断线不能通过新建 Session 盲目重派。
5. Handoff：先保存持久 checkpoint 与原始可信 result locator；目标接受、校验 Scope/版本/Workspace 后由 fencing 使旧 epoch 失去写权，新 Session 作为**后继执行**继续 Work，而非谎称原生 resume。
6. Park/Release：先确认无在途 effect、Run/lease 状态、dirty worktree、归档/恢复支持，再关闭具体 Run/释放资源。interrupt turn 不等于关闭 Session 或进程，不能按历史会话数量推断内存。
7. Recover：控制面重启后由持久 Work/receipts 去重、确认未知 effect、恢复正确 Assignment；不读取旧未验证对话充当授权。

## 5. 分层协调，不让一个 Chat LLM 成为全局瓶颈

**建议的默认拓扑是浅层分工：Human/Chat Client → Tokyo Work/Project Parent → 独立专业 Assignment → Native Session/Provider。**

东京持久的 Work/权限权威并不要求一只东京 LLM 对每条子任务消息做 reasoning 和全文转发。项目 Parent 可以在既有授权范围下负责拆分、跟踪和验收，工作 Agent 之间交换**必要的结构化消息和证据**。简单工作直达单 Agent；多 Agent 不是日常强制阶段。

层级是责任和协作关系，不是永远固定的人事等级；Parent/worker/reviewer 属于当前 Assignment，不因为上下级名称获得更大权限。同一 Work 的最终 Parent 责任仍唯一；一个项目可有多个独立 Work Parent，不由一个 Session 累积所有上下文。

**消息至少有三类**：
- CONSULT：询问/协作，不产生执行授权。
- DELEGATE / ACCEPT：任务切片的幂等提议与目标真实接受；接单≠执行≠完成。
- RESULT / EVIDENCE：产物、版本、测试/失败与收据；由 Parent 决定采用，不允许 worker 自证 ACCEPTED。

消息提议的最小信封：message_id、correlation_id、work_ref、source/target logical-agent-or-assignment refs、attempt、epoch、input_revision、kind、sent_at/deadline、payload locator/digest、授权/预算引用、reply_to。权限检查按双方身份及目标数据/Work Scope 执行，跨租户或跨账号没有默认互信。不传原始秘密、完整 transcript、万能 Token 或不受限命令。

通信可以通过现有有界 Provider/队列收据机制异步发生；上级 LLM 不必逐字转发，**但每个执行结果须回 Work 权威**。实现需要幂等 inbox/outbox、ack/timeout、乱序/重复、撤销/晚到结果/epoch fencing 和 effect-unknown 核查。不能因为 sender 返回 200 就报告 receiver 完成任务；没有可靠接收者则状态是 WAITING/UNKNOWN 而非“后台继续”。

中央 Fleet Gateway 仍是控制入口和潜在可用性单点；减少中央 LLM 上下文瓶颈**不等于**控制面已高可用。离线节点应保持有限、本地已授权工作与 Core 自救；Work store/消息证据要有可恢复的持久化与备份。

## 6. OpenAgents 的准确定位

V2 候选使用 [openagents-org/openagents-sdk](https://github.com/openagents-org/openagents-sdk) 的 Agent Network / task_delegation / messaging 机制，而非把其他同名框架误当作一个产品。它在 ServerOS 中是**可替换的 A6 委派/通信 Provider**：

- 不取代 Work Engine 的 Item/Activity/Run、Agent Control 的 grants/epoch、G1 Ability Registry、Node Core、Fleet Device/Session 真实状态；
- 不必部署新的全局 Work registry、云端第二后台或一个常驻 LLM 总调度器；
- 先 pinned 版本、独立测试网络、fake transport、duplicate/replay/unknown effect/独立 reviewer 负例；确认 SDK 对 Claude、Codex、DeepSeek 的真实 adapter/harness 后，才进入受限设备 PoC；
- Hosted Workspace/外部中继不是默认允许的数据流。公司仓库、飞书材料、会话和 token 不能因为 SDK 能联网就上传；
- 与原生单 Agent/已有 Quay 比较成功率、验收质量、时延、Token/计算和人工干预。无可核实净收益时维持较简单路径。

现有 [PR #188](https://github.com/ProfessorZhi/serveros/pull/188) 的 federation-v0 与薄 Skills 是**历史候选/未部署设计**；[Issue #187](https://github.com/ProfessorZhi/serveros/issues/187) 仍为 deferred。本文冻结当前架构共识，不代表已安装 OpenAgents，也不将 #188 的 WIP Skills 顺带合入 V1。

## 7. Work/Ability/数据目录的唯一权威

| 问题 | 权威来源 | Fleet / Agent 只保存 |
| --- | --- | --- |
| 能力身份、来源、版本、安装 | [ability-management](https://github.com/ProfessorZhi/ability-management) 的 abilities-manifest.yaml、G1 | 能力引用、设备可用性与用时版本 |
| Work/Item/Activity/Run/Outcome | [work-engine](https://github.com/ProfessorZhi/work-engine) + records/项目 Current View | work_ref、授权、checkpoint/result locator |
| Feishu Source/Claim/Understanding | B1/B2 的原始 source revision 与受管理 records | 稳定 locator、digest、覆盖/可见范围 |
| 会话/运行态/批准 | 原生 Provider + ServerOS Agent Control / Node Core | 当前路由、已证实的状态、receipt |
| Git 代码和发布版本 | 各业务仓 Git / GitHub | commit/release locator、验收证据 |
| 设备/网络/自愈事实 | 各设备 OS/Core/可信 Connector | 带时间和归属的只读观测 |
| 文档与日报 | B8/B9/B10 publication/outbox/Feishu 的真实回读 | delivery locator/revision/receipt |

目录沿用 [DIRECTORY-CONVERGENCE](DIRECTORY-CONVERGENCE.md)：Tokyo 的 src、worktrees、test-envs、ops、records、knowledge 分区；受管 data-root/artifacts/work-items 与 state/serveros 各有唯一责任。Dell 主要使用按 Work/Run 的临时执行区，不复制东京的 Work authority；Windows 只留本机必须运行或不能外移的内容。移动或清理前必须核实活动 Session/worker、脏 worktree、证据/引用和 owner，先改生产者与消费者，再考虑删除旧文件。

**飞书到日报的设计流**：B1 增量接入与 source revision → B2 可更正的认识 → B3 候选/获授权需求 → B4 Work/Focus → A1/A3/A4/G1 选 Session、环境和 Ability → B5/B6/B7/A6/A7 执行与验收 → B8/B9/B10 从真实 Work/evidence 发布报告并读取送达回执 → 人类反馈回流。小任务按需跳过无关阶段；定时器负责唤醒，不凭定时到达制造新工作或假“已完成”。

## 8. 验收与发布门

### Fleet V1 当前正确完成定义

- 三设备所需能力按身份列出：Core 可信且新鲜的资源/服务观测、已核实的 Node/Runtime 路由；不可达必须明确 UNKNOWN/NOT_CONNECTED。
- 有 owner/服务名单、单写者、预算、backoff、Agent-busy 保护的本机自启/有界自愈；toy PASS 不冒充真实 CloudCLI/Tailscale 生产恢复。
- Claude Code 实现真实受授权 Session 发现/创建/发送/状态/中断/继续的支持矩阵，默认模型与 permissionMode 区分 requested、resolved、effective；未读回不假定。
- 在东京本地 Claude Code 上先验证同源 MCP + Operator Skills 的 **实际**加载、authenticated tools/list、受限跨设备真实调用、Capability/权限负测和证据回传；随后分别验证 ChatGPT/Claude Chat 适配及真实 OAuth。当前只完成部分 CODE/LOCAL STAGED，不声称 LOCAL_CC_E2E_PASS。
- 所需 Feishu→Work→实际 Agent→Test/Outcome→日报/报告→实际发送回执具有至少一条不靠手动“再说继续”的真实 J06/Journey 证据。
- 扩展设备注册与断线/失效/旧 Runtime 的拒绝、发布 revision 和回滚验收。

### Fleet V2 前置技术门与试验

- Logical Agent/Assignment/Session 的版本化身份关系：跨 Session/跨设备接续，旧 epoch 不可写，已有 session_ref/Work Item 约束不破坏。
- 多 Parent 并行且消息点对点或经 broker 传递，中央 Chat LLM 不介入每一跳；重复投递、结果晚到、Agent 失联、控制面重启可恢复。
- OpenAgents 作为可替换 Provider 的隔离合同测试，随后才可见证真实 Codex、Claude/DeepSeek 组合；真实 provider readiness/agent runtime/模型和授权分别查证。
- 对比单 Agent 基线的交付质量、吞吐、沟通开销、成本和人工介入。没有正向证据时不提升自治层数。

## 9. 相关现有契约（不复制、不重造）

- [总架构](../../ARCHITECTURE.md) §6 Session Fabric、§16 Ability Fabric、§18 Connectivity、§21 Node Core。
- [Agent Runtime Model](../agent-runtime-model.md) 与 [Agent Session 合同](../../runtime/agent-session-contract.yaml)。
- [Ability Operating Model](../ABILITY-OPERATING-MODEL.md)（21 项语义职责、Daily Cycle、A1/Work 边界）。
- [目录收敛](DIRECTORY-CONVERGENCE.md) 与 [Native 工作方式](NATIVE-WORK-ENVIRONMENT.md)。
- [Work Engine 配套合同](https://github.com/ProfessorZhi/work-engine/blob/main/docs/serveros-system-v1-work-contract.md)。
- [V2 候选 PR #188](https://github.com/ProfessorZhi/serveros/pull/188)、[OpenAgents Issue #187](https://github.com/ProfessorZhi/serveros/issues/187)、[Ability Journey Issue #190](https://github.com/ProfessorZhi/serveros/issues/190)。
