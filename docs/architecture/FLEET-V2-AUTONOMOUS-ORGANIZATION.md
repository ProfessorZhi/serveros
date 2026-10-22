# ServerOS Fleet V2：分层自治 Agent 组织与 Owner 全权工作环境

> **状态：PRODUCT TARGET / ARCHITECTURE PROPOSAL，2026-10-22；尚未实现、签收或启用。**
>
> 本文记录 Owner 在 2026-10-22 讨论中明确提出的新产品需求：消除「GPT 每轮替 Agent 搬运消息」的协调瓶颈；采用可配置的总管—分管—专家组织；Agent 设定独立于原生 Session；原生接入 Claude Code、Codex、DeepSeek Harness (DSH)，按需评价 OpenAgents；对 Owner 自有、纳管的资源提供 **Owner Full-Control 持续自治**，不按每个项目、Session 或工具调用反复申请；支持设备维修专家集群与 ServerOS 自迭代。
>
> **强制区别：目标需求 ≠ 当前运行授权。** 本文及本 PR 只修改设计资料；不会发放 OS/root 权限、迁移数据、改变现有 Grant、部署或让当前 Fleet 获得更大操作权。当前 V1 真实权限、审批与保护门槛继续生效。未来启用全权模式必须由真实 Owner 通过可信入口对实际受管主体和资源完成独立配置、执行侧验证和可撤销绑定；角色文字、@ 消息或 Agent 自述不能授予权限。
>
> **规范关系：** 根 [ARCHITECTURE.md](../../ARCHITECTURE.md) 仍为产品结构总入口；[Native Agent Work Environment](NATIVE-WORK-ENVIRONMENT.md) 保持基本产品哲学；[Agent Identity / Session / Delegation](AGENT-IDENTITY-SESSION-DELEGATION.md) 管理身份、不可变 Session 绑定和责任视图；本文补足 **Fleet V2 产品组织、Full-Control 目标与跨设备自治流程**。现有 [V1 release gate #237](https://github.com/ProfessorZhi/serveros/issues/237) 和 [campaign #244](https://github.com/ProfessorZhi/serveros/issues/244) 未被本目标覆盖、绕过或视为通过。离线原型 [Draft PR #389](https://github.com/ProfessorZhi/serveros/pull/389) 是独立探索，不能当作这个组织形态的现场验证。

## 1. 目标：将 GPT 从「逐条指挥官」变成「人机总管」

### 1.1 当前根因

现有通过 ChatGPT→Fleet MCP→数个 Native Session 的控制链，往往要求 GPT 反复查询 Agent、读日志、搬运指令、决定下一轮；用户通过「继续」驱动下一阶段。对长任务来说，这既是 token/人工注意力瓶颈，也会放大 ChatGPT 对话、单个总管 Session 或 Fleet ingress 失联的影响。

V2 的成功定义不是「能一次创建更多 Session」，而是 **一个已接受的目标能在授权范围内持续产生真实交付，不需要用户与 GPT 保持常在线，也不需要每一层 Agent 不断向总管索要下一条指令**。

### 1.2 对用户的承诺（目标，不是现状）

- **单一主交互对象：** 用户主要与「总管」交流，提出目标、排序、取消、替换负责人、调整组织设定或查看结果；普通执行噪声由下层处理。
- **多入口同一份工作：** ChatGPT、Codex、未来 Claude Chat / CLI 可以共同作为总管的客户端，但共用 ServerOS 已接受的 Work/权限/证据；不会形成多套相互矛盾的总管数据库。
- **任务不中断于聊天结束：** 关闭 ChatGPT 不取消已经接受的 Work；Agent 的后续进度由设备/调度事件驱动；重开客户端只需读取准确状态。
- **授权内自治：** 新建项目、分配职责、创建 Session、执行命令、委派子任务、调试/测试/集成、自行修复与升级都不要求逐步骤找用户确认。
- **例外上报而非状态洪水：** 总管仅接收重要冲突、不可恢复故障、目标变化、需要 Owner 决策的范围外效应、阶段验收与阶段总结。
- **可观察、可接管、可撤回：** 人能够知道工作和 Agent 在做什么；及时打断、变更目标、冻结新委派、撤回某个授权或选择回滚版本。

### 1.3 非目标

不建立一个必须一直推理的「超级 GPT Manager」；不把 Node Core 改成 LLM 调度器；不要求每个小任务启动一个多级团队；不把 Agent Persona、OpenAgents Workspace 或 GitHub Issue 升格成第二个 Work/Grant 权威；不无限创建进程消耗宿主资源。

## 2. 组织形态：职责树 + 平级协作网 + 设备资源网

三种关系独立建模：

1. **责任树（Who owns delivery）：** Human Owner → Portfolio Steward / 总管 → Project Parent / 分管 → Specialist Worker。回答「谁负责目标、阶段接收和最终结论」。
2. **协作网（Who talks to whom）：** consult、@mention、delegate、review、escalate、broadcast scoped status；允许平级和跨项目协商。回答「信息如何到达专家」，不证明授权。
3. **执行资源网（Where/how work runs）：** project/role requirements → effective Ability → device / OS principal / credential / provider / model / workspace / Session → Run。回答「实际在哪运行」。

**不要将此三图合为固定树。** 一位 Project Parent 可以在东京规划，临时把测试分配给 Dell；设备管家属于横向服务职能；Reviewer 可独立于 Author 的直接上下级结构。多级委派按实际复杂度按需展开，不强制固定深度。

### 2.1 推荐职责模板

| 角色模板 | 决策与职责 | 不应该出现的行为 |
| --- | --- | --- |
| Human Owner | 长期目标、重要取舍、Owner 模式的实际配置与撤回 | 日常逐个授权 Session |
| Portfolio Steward / 总管 | 多项目优先级、跨项目预算、冲突与接收、面向人汇报 | 转发所有 Agent 日志、替项目处理每个 bug |
| Project Parent / 分管 | 项目目标分解、里程碑、工作树与角色分配、交付集成及项目质量 | 自行把子任务结果写成 Owner 已批准的业务决定 |
| Device Steward / 设备管家 | 设备健康、OS/Runtime/资源观测、容量、运行环境、维护协调 | 接管项目目标、读取无关私有材料、从诊断角色获得隐含 OS 特权 |
| Implementation Specialist | 有边界的实现、测试与原始产物 | 以「已经完成」替代独立验收 |
| Reviewer / QA | 独立代码、安全、测试和结果证据复核 | 与作者共用不可信上下文且自我签收 |
| Platform Maintainer | ServerOS、Fleet 插件、Skills、Provider Adapter、自更新流程 | 修改验收条件以使自己通过，更新时摧毁回退入口 |
| Domain Expert | Linux、Windows、网络、GPU、数据库、权限诊断等 | 角色名即 root 权限 |

上述为 **Role Profile / 职责配置**，不是八类永久后台 LLM 进程；同一 Role 可在不同 Work/Session/设备重复使用，甚至绑定不同 Runtime。

### 2.2 管理关系是可配置的

Owner 可用自然语言要求：更换总管的执行模型、增加分管、规定 DSH 优先处理实现、Codex 优先做架构、Claude Code 做独立评审、调整设备修复团队的职责、重排优先级。ServerOS 将其转为**版本化的项目级角色与路由配置**，标记生效版本和影响的工作范围。

已在执行的 Work/Session 不得因某条随口新设定自动失去上下文：变更必须选择保持既有配置直到 checkpoint、通过明确 successor/epoch 接任、或停止；不能把旧 Session 隐式重绑定到新 Work。角色描述变更不等于 grant/credential 变更。

### 2.3 总管本身可接续

「总管」是一个稳定的人机产品入口和可交接职责，不是某个 ChatGPT 对话 ID、单一 Codex Session、OpenAgents 用户名或东京进程。总管持有的是经过 Work 权威组合得出的 portfolio 视图和决策记录；推理 Session 可替换、按需唤起。多个客户端同时连接必须使用当前 Work revision/owner epoch 协调写入，避免同时生成冲突任务。

## 3. 分层组件与权威边界

    Human Owner / ChatGPT / Codex / Claude Chat
                     |
               Fleet Client / thin Skills
                     |
                 Fleet MCP ingress
                     |
         ServerOS Work + Agent Control authority
        (Item / Run / Owner / Grant / Lease / Effects)
                     |
          Responsibility + dispatch projections
            /                |                \
       Project Parent    Device Steward     Platform Maintainer
             |               |                 |
        bounded Work      diagnosis          update candidate
             \               |                 /
           Fleet Node / Effective Ability / Runtime selectors
                Claude Code | Codex | DSH
                  provider-native Sessions
                         |
                  verified effects + receipts
                         |
            independent acceptance / reports

**已有权威继续唯一：**

| 信息 | 真正 owner | V2 添加的仅是 |
| --- | --- | --- |
| Work/Item/Run、Parent/child、完成定义、验收 | ServerOS 当前 Work Engine / Agent Control | 投影、被动汇总和任务路由 |
| Owner/grant、revocation、epoch、lease、可写 effect | 当前可信发行者和 Writer 边界 | 严格读取当前事实、验证效应 |
| Node 资源、服务、OS principal、真实运行状态 | 节点 Core / OS service manager / 实际 Provider | 新增观测消费者与派遣候选 |
| 能力、Skill provenance、有效 Provider | ability-management + Device EffectiveAbility | 角色需求与能力的匹配 |
| Provider-native Session 和进程/运行 | Claude Code、Codex、DSH / CloudCLI Provider | 生命周期标准化，不造伪事实 |
| 第三方协作消息 | 可选 OpenAgents / native messaging | 仅传递消息、映射现有 Work 关联 |

**计算式责任视图：** Current Work + Role Profile revision + current owner/epoch + grant + exclusive lease + native Session immutable binding + provider health → 当前负责人/下一动作/证据可信度。视图是派生值，不另建一份可编辑的 Assignment 权限库。任何缺少实测状态都标为 UNKNOWN。

Node C++ Core 保持确定性观测、自检、受控恢复和局部资源管理；LLM 规划与团队协调不进入 Core。CloudCLI 是可选的人机界面与 Provider 桥接，不应成为 Fleet 使用 DSH 的必经协议跳板。

## 4. Owner Full-Control：授权一次日常自治，而非每次 Session 请示

### 4.1 产品决策

**Owner 期望 V2 默认具备可选的 OWNER_FULL_CONTROL 运行形态。** 它应覆盖已明确登记为 Owner 自有/有权管理的设备、账户、服务和工作域。Owner 通过可信设置入口激活覆盖范围后，总管/分管/执行者在真实设备许可能力内按职责自动运行，正常情况下不逐个申请 Session、目录、项目、工具调用或相同范围的重复授权。

这是一种**权利可调用的自治操作模式**，不是文字上的「超级管理员角色」，也不是要求不同 OS 用户的权限合并成 root。未经纳管的设备、第三方账号、公司资源、其他人的工作和未拥有的 Provider 凭据，不能靠 Full-Control 标签变成 Owner 的资产。

### 4.2 期望能力矩阵

| 动作 | V2 Full-Control 目标 | 执行侧必要事实 |
| --- | --- | --- |
| 创建项目、目录、worktree、内部仓库、文档 | 自动，无需逐次提问 | 目标命名空间/实际仓库写能力与约束 |
| 新建、恢复、分配、终止受管 Session | 自动，无需列举允许的 Session ID | 设备/OS principal/Provider 支持、容量、现有 WIP |
| 管理 Role、团队、路由、预算、模型候选 | 自动版本化配置 | 当前 Scope / policy 允许且可审计 |
| 开发、测试、安装依赖、提交和集成 | 自动执行、真实测试 | 当前 Workspace/项目凭证、互斥写作用域 |
| 设备排障、服务管理、软件升级、配置修复 | 在已激活的受管管理域内自动处理 | 节点身份、有效 OS 许可、对象属主、效果验证与恢复策略 |
| Fleet 插件/Runtime/ServerOS 自更新 | 允许形成候选、验证和在授权发布域内递进更新 | 受保护部署效果通路、回退、独立验收 |
| 生产环境、权限/密钥、删除或不可逆资源改变 | 产品目标可由 Owner **明确配置的长期高权限策略**决定是否覆盖，而非每次临时询问 | 现实身份、权利来源、受保护效果类别和不可逆风险门槛；未配置则必须升级给 Owner |

**当前运行政策不随本文变更。** V1 已有生产变更、删除、权限变化等单独确认要求继续有效；V2 若要引入覆盖它们的长期 Owner mandate，必须设计独立的可信启用/撤回仪式、受保护对象清单、真实效果执行路径和现场验收，而不能把此次对架构的文字认可解释为现役权限已扩大。

### 4.3 不消失的机制（不是人工审批循环）

- **真实身份:** caller/device/OS principal/credential instance 逐维认证，不能借用无授权账户，也不能继承另一 UID 的密钥。
- **Grant 与执行时校验:** current grant/owner epoch/lease 对 mutating admission 与最终效果提交重新验证；文字 instruction 与 @mention 永远不构成 grant。
- **避免重复和写冲突:** 一个 Work/write scope/epoch 一个当前 Writer；重放只读取原 attempt，效果不明时 EFFECT_UNKNOWN 而不是盲重试。
- **变更前后的证据:** 对设备或部署等高影响操作记录目标、预期变更、最小回滚与变更后 readiness；不需要 Owner 人工审查每条正常日志。
- **撤回与停止:** Owner 的停止、范围回收、紧急冻结优先于所有长期 mandate；不能要求先等某个 Agent 同意。
- **资源真实上限:** 有权任意按需创建 Session ≠ 无穷并发；容量不足自动排队或选择合适设备，不能抢占未知正在运行的任务。
- **信息隔离:** 平级 Agent 协作不能传播所有项目原始 transcript、Owner 私人凭据；源自网页/代码的低信任文本不能授权新增工具。

### 4.4 Full-Control 目标中的清晰状态

应显式区分 OWNER_MANDATE_NOT_CONFIGURED、OWNER_MANDATE_ACTIVE、EFFECT_IN_SCOPE、EFFECT_OUT_OF_SCOPE、UNDERLYING_OS_DENIED、WRITER_FENCE_MISSING、EFFECT_UNKNOWN。不能把「有意愿放开」显示成「拥有真实 OS 权限」，也不能用一个统一 POLICY_DENIED 遮蔽修复路径。

Full-Control 不是一个可由 Agent 自行切换的总开关。新增设备、身份、账号、数据出口或原先未覆盖的高风险对象属于**策略范围扩大**，不应由当前执行者自行决定。

## 5. Role 与 Session 的正式拆分

| 实体 | 应长期存在什么 | 不与什么同一 |
| --- | --- | --- |
| Role Profile | 版本化职责、能力要求、协作风格、偏好 Runtime/模型、检查点方式 | 不等于 credential/grant 或永不退出的 LLM |
| Work Responsibility | Work/Role/Owner/epoch/写入边界的当前关系 | 不复制 Work Engine 状态 |
| Provider Runtime | Native CC、Codex、DSH 的实际执行器、配置、凭据拥有者 | 不等于设备或模型名称 |
| Native Session | Provider 自己的状态、消息历史与工作上下文，标识一经创建固定 | 不等于 Work 或任意可清空的线程 |
| Turn / Run / Attempt | 一次有界调用、费用与副作用关联 | 成功回复不等于 Parent ACCEPTED |
| Device / Workspace | 可承载某一会话的资源及隔离现场 | 同主机不同 UID 不等于同一个授权主体 |

**Session 分配规则：** 能证明仍属于同一 Work、同一 Owner epoch、上下文来源可信且 Provider 明确可恢复时，优先复用；否则分配受隔离的新 Session。旧 Session 可以保留只读历史，但旧 epoch 写入必须被 fencing 拒绝。不能将 Session 回收当作共享历史池，把一个客户/项目的隐藏内容交给新项目。

**创建自由度：** Project Parent 可以在受管容量内任意创建该工作需要的 Session；Role Profile 按需实例化多个执行者；不再将一组预制 Session ID 作为授权白名单。每次分配都产生可追溯 Session→Work/attempt 绑定，后继 Session 另有 ID，不能悄悄重定向旧绑定。

## 6. 运行时与协议：CC、Codex、DSH、CloudCLI、OpenAgents

### 6.1 原生 Runtime Adapter

ServerOS 对每个受管 Native Provider 实现同一 **能力语义** 而不是假设三套 CLI 完全一致：discover / health / create / resume-if-native / send-or-prompt / read / cancel / stop / receipt / actual model & permission modes。每个操作返回 SUPPORTED / UNSUPPORTED / NOT_READY / AUTH_DENIED / EFFECT_UNKNOWN 等清楚结果。适配器声明原生 session/child 支持级别，绝不把重新起一个新 Session 伪称 resume。

- **Claude Code:** 保留原生 Session、tools、subagents；实际嵌套层次和消息协议以现场版本能力为准。
- **Codex:** 保留原生 Session、subagent 与对 Skills/MCP 的支持；可当 Parent 或独立 Reviewer，不强制只有 Codex 才能管理。
- **DeepSeek Harness:** 优先研究官方 SDK stdio JSON-RPC 或 ACP Session 接口，以 native Session、事件和恢复为一等对象；对 headless、SDK、ACP 的差异进行分别验收。
- **CloudCLI:** 已有 CC/Codex Provider 和 UI 只作为可选展示/接入层；不要要求 DSH 必须「先接 CloudCLI 再转 MCP」才能被 Fleet 操作。若需要 CloudCLI 可视化 DSH，再开发薄 CloudCLI DSH Provider，复用原生 DSH Adapter 语义。

### 6.2 委派分两级

- **Native subagent:** Runtime 内部短期方法性拆分（探索、并行读、测试）。上层通常只需收集一个 bounded native Run 的结果与费用。
- **Fleet child Work:** 跨 Runtime/设备/团队，要求持久责任、独立 Workspace、可恢复 Session、验证/撤销/效果记录。必须使用现有 Work 的 parent/child、租约和 attempt 语义。

可让 Codex 管理 DSH Worker、DSH 管理同域已授权子任务、Claude Code 做独立审查；不存在「模型品牌决定阶级」的规则。动态嵌套受预算、并发与递归深度约束，不能无限扇出。

### 6.3 OpenAgents 是可替换的协作层

候选为 [openagents-org/openagents-sdk](https://github.com/openagents-org/openagents-sdk)，可评价其 Agent 网络、消息、协作频道、@mention、task delegation、MCP 传输、Launcher DSH preview adapter。它有协作机制，但**不能直接当作可信分布式权限/工作事务/唯一 Writer**。任何 SDK identity、workspace message、task id 都必须重新映射到 ServerOS 已认证的 Work/Device/Principal/Current Grant 与权威 epoch。

优先验证 native 协作是否足以完成目标；只有在 OpenAgents 相较现有 native 委派带来客观的稳定性、延迟、人工开销或跨 Agent 交互收益，才采用其具体模块。禁用任意默认托管 Workspace 外传机密；评估先从自管 loopback/临时网络开始，外部公开连通和数据同步独立治理。

## 7. 通信语义：@ 是交互，不是权利

| 消息语义 | 允许发生的事 | 必须区分 |
| --- | --- | --- |
| consult / @expert | 平级询问、读取授权范围内必要上下文 | 不产生执行许可 |
| delegate / accept / decline | 交付有界 child Work 与接收责任 | ACCEPT_TASK 不等于执行完成 |
| progress / result | 返回最小进度摘要、artifact/receipt locator | RESULT_READY 不等于 ACCEPTED |
| peer-request | 项目分管向设备管家申请能力/容量 | 资源可用性 ≠ 业务授权 |
| escalate | 任务或管理范围外的问题升级至上级/Owner | UNKNOWN 不自动当作拒绝或通过 |
| cancel / supersede | 冻结新工作，结算/对账既有 effects，再推进 successor | 取消消息 ACK ≠ 已停机、撤回旧授权 |
| notify | 发送重要成果或告警，避免冗长转发 | “消息已发送”≠“用户已看到” |

所有会改变工作状态的跨进程消息携带 source message id、Work/Parent、input revision、attempt/epoch、受理者、Effect 类别、budget/ref、deadline 和 evidence locator；真正签发和校验在现有 Work/Grant 系统中完成。通信可用性中断时不造「已完成」状态。

## 8. 四个典型自治场景

### 8.1 用户说：「新建一个项目，把原型做出来」

总管识别目标和 Owner 在受管工作根、仓库、Runtime 的持续授权；Project Parent 自动创建工作对象、项目文件与 repo/worktree，分配 DSH/CC/Codex（按照真实能力和成本），分别执行编码、测试和独立 review。普通失败由 Parent 与 Worker 自行修复，最终以原始文件、测试/CI/设备证据给总管阶段结论。你不需要每个 Session 再下指令，也不因为浏览器对话关闭而停止。

### 8.2 「ServerOS 某功能继续开发」

项目分管读取上一次 Work、真实当前 writer/epoch、代码与验证记录，不从聊天记忆猜测哪个 PR/Session 正在写；优先接续相同 Work 的合法 Session 或创建新 Session。不同 Worktree 分区让多个 Worker 并行；当发现同一文件或资源冲突时自动串行化。阶段验收失败则在已有目标下继续修复，不靠 GPT 每轮重新转发。

### 8.3 设备维修 Agent 集群：服务故障与权限打不开

Node Core 先按真实已配置策略进行确定性的有限恢复。未恢复则生成设备故障 Work；Device Steward 分发给 Linux/Windows/网络/Runtime/权限专家，观察故障现场与所属主体。遇到 Permission denied，先区别错误路径、错误 UID、失效凭据、拒绝本就合理、已准许配置损坏；根据真实资源拥有关系决定配置修复或升级 Owner。**不把一切权限拒绝都当成 chmod/sudo 的理由。** 在 Full-Control 已覆盖的设备管理域中，可自动执行需要的有界配置修复、重启/升级，并经下一次健康观测确认 RECOVERED；超出现实授权或对其他 principal 的保护边界则拒绝冒充已恢复。

设备掉线不能由同设备上的 LLM 自己修复所有情况：需要受控本地救援、其他受管节点的只读观察、不可达如实上报及预置回滚方式。修复团队是按事件弹性组建的角色组合，不是每台设备永久常驻十个模型进程。

### 8.4 ServerOS 自迭代：插件或核心适配器升级

系统发现重复失败/工具版本差异 → Platform Maintainer 提出更新目标、选定确切源码/消费端/发布版本 → Developer 在独立分支实现 → Reviewer/QA 对原始 diff、负例、故障回退审查 → staging/canary → 目标消费端 readback → 根据已授权发布策略推广或回退。结果区分 source committed、package built、device installed、client loaded、real E2E passed。系统不能通过更新自己来替换独立验收标准、扩大权限、关闭 Owner 的紧急停止能力或丢弃稳定回滚路径。

## 9. 自迭代与恢复：不能依赖唯一一位思考中的 Agent

### 9.1 持久事件与接任

运行进展由已存在的 Work/Agent Control/Provider 原生状态承载，不由 ChatGPT 私有会话承载。采用「事件触发 + 有界兜底 reconciliation」：Child RESULT_READY、健康告警、授权变化、资源空闲、Owner 新指令等触发需要的管理轮次；调度与重试主要由确定性程序处理。一个分管的推理结束后不意味着团队永久停摆。

### 9.2 故障与分区

总管 Session 消失时，已授权、在预算内的下层 Work 可以继续。Node/Provider 失联时，不从断线推断任务失败，也不凭旧 Grant 创建新权利。远程调用请求状态不明时持久标注 EFFECT_UNKNOWN 并向原 Provider / effect owner 对账，绝不盲重发有副作用操作。东京 ingress 故障不能暗示 Dell 已停工；Dell 也不能在断网时伪造东京新的 Owner mandate。

### 9.3 Supervisor 不是额外常驻大脑

适合由 OS/Core/Task scheduler 处理的心跳、资源闸门、更新版本检测、Session liveness、效果对账，使用确定性机制；只有复杂诊断、架构判断、无法分类冲突等才调用 LLM。避免一个 Planner 高吞吐持续读取所有子任务 transcript 变成新的费用和延迟瓶颈。

## 10. ChatGPT、Codex、Claude Code 的安装与交互

**原则：一个 ServerOS Fleet 产品，一个能力/Work/权限权威，多种客户端适配与发行材料。** ChatGPT、Codex、Claude Code 实际插件/Skills/MCP 格式或授权方式可能不同，按每个客户端实际机制构建 thin adapter，而不是两套核心后端。共享 Operator Skills 的语义来源、公开的协议/Schema 与升级版本；只在客户端层定制安装、认证、UI 和进程接续方式。

ChatGPT 默认承担人机战略对话；Codex 可以是另一位总管入口、Project Parent、Reviewer 或 Worker；Claude Code 同样不被限制只能是底层；DSH 既可以作为高性价比 Worker，也可以在明确能力与授权下承担分管。模型/产品类别**不决定永久组织层级**。

跨客户端共享 accepted Work、Role Profile revision、当前责任、状态和证据，但不假设两个客户端共享原生聊天 Session、OAuth principal 或环境密钥。一次实际插件更新需分别区分 repo source、打包版本、安装版本、客户端实际加载版本、已验证调用工具列表与结果。任何特定客户端若无法更新，报告 PARTIAL 而不是全集群已更新。

## 11. 最小数据与接口形状（提案，不是生产 Schema）

| 记录或视图 | 关键要素 | 权威与约束 |
| --- | --- | --- |
| RoleProfile | role_ref, project_scope, revision, responsibilities, required_abilities, preferred_runtime, reporting_policy | 版本化配置，可改但不含副本 grant |
| ResponsibilityView | work_ref, current_parent, role_ref, owner_epoch, current_grant_ref, lease_ref, session_binding_ref, freshness | 组合视图，只读，不新建 mutable Assignment 权威 |
| DispatchAttempt | work_ref, child_ref, input_revision, attempt_id, idempotency_key, device/principal/runtime, effects, budget_ref | 复用 Work/Agent Control durable attempt owner |
| NativeSessionBinding | provider, native_session_id, device/principal, bound_work_ref, binding_epoch, provenance | 建立后不可重定向，续接用 successor |
| EffectReceipt | intent, performer, source revision, actual effect, evidence locator, commit check, uncertainty | 唯一 trusted Writer/effect owner 对真实写动作作判定 |
| Incident | device, service, observation, owner, severity, repair policy, evidence, verified readiness | 复用现有监控/Incident 记录 |
| ReleaseCandidate | artifacts, source SHA, build/test/prod evidence, installed/readback revisions, rollback | 更新系统不得冒充 verifier |

这些字段是设计草案，不代表已经注册工具或可以直接调用。新接口原则上以现有 Fleet/Agent Control Provider 适配，只有存在具体消费者与用例时才增加最小字段；不为新图表复制第二套 state store。

## 12. 用户操作界面：只展示决策重要的事实

总管的组合视图回答：有哪些活跃目标、谁是当前分管、进度与阻塞、现在有没有真正的有效 Writer、下一件具体动作、设备负载、费用与风险、待你决策的事项。项目视图下钻 Work→子任务→当前 Role/Agent/Session→证据；设备视图回答系统服务、Runtime、Session 占用、可恢复性与维护动作。独立列出三种事实：**模型声称、工具执行回执、实际验收/健康结果**。

理想的人机命令示例：
- 「ServerOS 这周优先，其他项目只完成手头安全工作。」
- 「DSH 负责实现，Codex 管架构，Claude Code 独立审核；以后按此团队配置。」
- 「新建一个试验项目，使用现有受管工作区，不用再逐个询问我能不能开 Session。」
- 「设备权限打不开交给运维分管，查明主体与原因；在当前 Owner 管理域内能修就修。」
- 「平台维护分管升级 Fleet 插件，必须从真实消费端读回版本，不允许自行改变授权配置。」
- 「停止该项目的新委派，展示无法立即撤销的在途效果。」

## 13. 上线与试验的分阶段边界

产品目标清晰并不等于当前原型已经安全。先按 [Fleet V2 delivery/acceptance plan](../plans/fleet-v2-delivery-and-acceptance.md) 落地：
1. 固化用户交互、组织/Role/Full-Control 产品契约与离线负控；不触碰真实权限。
2. 做真实 Work/Ability/Runtime 的只读 Join 与可重复的管理决策；没有可信事实不派发。
3. 完成 V1 #237 所需的真实 Writer fencing、effect idempotency 与 OS Writer 边界，才能给跨设备副作用可信写路径。
4. 在独立环境下验证 CC/Codex/DSH Session 生命周期与 role-independent placement；Full-Control 实际接线单独授权、独立验收。
5. 做一条自动「新建项目→团队交付」路径、一条受管设备维修路径、一条自更新 canary→rollback；都必须含负例。
6. 只在 native 基线已测且有量化收益时引入 OpenAgents 消息/委派模块。
7. 验证 ChatGPT/Codex 多端同源、取消/变更优先级、总管更换、服务失联恢复。

本文既不授权把现有 V1 生产权限放大，也不改变 [#244](https://github.com/ProfessorZhi/serveros/issues/244) 当前 release blockers 的证据状态。V2 的可用状态必须按 SOURCE / TEST / STAGED / REAL_DEVICE / CLIENT_E2E / PRODUCTION 分别报告。

## 14. 关键验收指标（衡量是否真的减轻总管瓶颈）

- 已接受 Work 从开始到有据可验的 ACCEPTED 所需 **用户「继续」次数**；目标是普通任务可以为 0。
- 每个完成的 Work 需要的 **总管模型介入轮次**、人工打断次数、完整 transcript 跨层复制量；均应明显小于基线。
- 同等工作完成率、独立质量审核通过率、修复后恢复率；不能为了减少提问牺牲正确性。
- 每项工作的总费用、真正排队耗时、设备峰值并发/资源冲突，避免无限子 Agent 扇出。
- 总管会话重启、父级故障、Provider 不可达、东京↔Dell 分区之后的接续成功率与 EFFECT_UNKNOWN 透明度。
- Owner Full-Control 下的新项目/Session/维修动作，正常不发生逐次 Owner 审批；越界扩权和失效凭据必须阻止且理由可验证。
- 自更新后插件/服务/客户端 readback 的版本一致性、rollback 成功率；不可把 PR merged 统计为 deploy success。

## 15. 既有链接、冲突管理与开放决策

- [V1 safety gate #237](https://github.com/ProfessorZhi/serveros/issues/237) 是当前真实写入和恢复的安全前置，不因 V2 文档自动解除。
- [Fleet Complete V1 #244](https://github.com/ProfessorZhi/serveros/issues/244) 的 CC/Codex 真实设备 E2E 仍属于 V1；**V2 增加的是自治组织与 DSH/动态多 Agent 协作**。
- [Agent Session/Delegation 设计](AGENT-IDENTITY-SESSION-DELEGATION.md) 是 Role/Session/epoch/绑定细节规范，本文件不重定义已存在的 V1 schema。
- [V2 R0 PR #389](https://github.com/ProfessorZhi/serveros/pull/389) 只有纯离线路由预览、不能视为正式 runtime scheduler。
- [OpenAgents #187](https://github.com/ProfessorZhi/serveros/issues/187)、[Fleet Plugin #186](https://github.com/ProfessorZhi/serveros/issues/186) 是相关候选/跟踪，不代表 SDK 已经接入。
- [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness)、[OpenAgents Network SDK](https://github.com/openagents-org/openagents-sdk)、[Yale Quay](https://github.com/yaleh/quay) 为第三方实现与参考；采用前固定版本、检视真实接口，禁止将宣传文字当成通过验收。

**待独立设计裁决：** Full-Control 首次启用时如何对已纳管的高影响生产资产形成可撤销长期 mandate（含哪些动作、自动回退与不可逆效果处理）；跨设备可信 Writer/IPC 对 OS 身份的实际实现；主总管故障恢复和多客户端冲突策略；自更新所需的独立验收主体及应急回退链。这些是**实现与安全契约待定**，不是对已确定 Owner 自治产品目标的否定。

**一句话：V2 是「统一的人机总管 + 可配置的多级责任组织 + 平级协作网 + 跨设备 Native Runtime Fabric + Owner 全权自治 + 可验收自维护」，而不是扩大的远程终端列表。**
