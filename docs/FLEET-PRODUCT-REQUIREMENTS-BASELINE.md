# Fleet 产品需求基线与当前缺口

> 状态：产品需求基线草案，待产品 Owner 审阅。  
> 基线日期：2026-11-13。  
> 适用范围：ServerOS Fleet、ChatGPT Fleet 插件、Tokyo Hub、三设备 Agent Runtime、Ability Management 与 Feishu 播报。  
> 本文记录产品目标、用户痛点、需求边界、当前仓库证据和验收条件。**设计存在、源码存在、已安装、运行有效、真实端到端验收是不同状态，不得互相替代。**

## 0. 执行摘要

Fleet 当前的主要问题已经不再是“缺少多少个 MCP 工具”，而是系统没有稳定地把用户意图转化为低延迟、低干扰、正确路由、可验证的 Agent 执行。

本轮明确七个核心问题：

1. **插件性能**：Fleet 插件带来的启动、工具发现、上下文和浏览器开销不透明，用户感受到性能明显变差。
2. **指挥延迟**：任务经过指挥层后等待时间过长，路由、状态查询、能力解析和重复往返可能超过任务本身的价值。
3. **执行绕路**：简单任务没有直接交给已有 Agent，而是经过过多中间步骤；Fleet 应当加速原生 Agent，而不是成为必经审批流水线。
4. **Feishu 播报体验**：文案不自然、信息层级差、卡片视觉质量不佳，必须按真实飞书客户端重新设计和验收。
5. **资源理解与告警**：系统虽然已有采集和阈值，但没有形成三台设备各自的资源能力模型、工作负载归属和“什么情况值得通知人”的完整规则。
6. **Ability 家族和版本**：21 个语义职责、Skill、Plugin、MCP、Provider、设备 Profile、安装和 Runtime Readback 之间存在不一致，尤其生产观察和 Workspace。
7. **自动发现与 Workspace 治理**：Agent 应能按任务自动发现合适的 Skill；Workspace Management 必须真正参与目录落位、盘点和防漂移，不能只存在于能力目录中。

本文件是产品级需求入口，不替代现有的监控、Ability、Session、Memory、Workspace、密钥和 Work Engine 细分合同，也不创建第二份运行状态数据库。

## 1. 产品定位与不可破坏的架构约束

### 1.1 产品定位

目标产品形态：

- GPT 是一个逻辑上的指挥官，负责理解目标、选择执行路径、必要时协调设备并验收结果。
- Claude Code、Codex 等原生 Agent 是实际推理和执行主体。
- ServerOS 是 Agent 外层运行与治理底座，提供 Environment、Context、Capabilities、资源事实、跨设备路由、审计和受控接续。
- Fleet 是可选的快速路由、跨设备协调、状态观察和管理入口，不是每个任务都必须经过的工作流引擎。
- Ability Management 是可复用能力的唯一权威供应链。
- Feishu 是人类监督、通知、状态阅读和必要决策入口。

**最高优先级体验原则：启用 Fleet 后，同一任务不应比直接向已有 Agent 下令更慢、更绕、更难理解。**

### 1.2 架构不变式

1. 一个逻辑指挥官；设备 Agent 自主执行，不把 GPT 变成每一步都要等待的中间代理。
2. 复用 CloudCLI / 原生 Runtime 已有的 Session 生命周期接口；不新建第二套 Agent Loop、Session Runtime、Run Engine 或业务调度器。
3. Work Engine 仍是正式 Work、Grant、Checkpoint、Lease、Effect 和 Receipt 的权威。
4. **serverosd** 仍是唯一常驻 Node Core；不为每项治理能力增加新的常驻 daemon。
5. **ability-management** 是 Ability canonical registry、revision、compatibility、distribution 的唯一权威。
6. Capability / Ability / Skill / Plugin / MCP / Provider / Runtime Binding 是不同对象，不得因为名称相似就合并身份。
7. Discovery、Ranking、Installation、Enablement、Attachment、Authorization 分开判断。
8. Ability 有效性必须按 device、OS principal、runtime、target、revision、health 和 grant 解析；不存在全 Fleet 通用的 AVAILABLE。
9. 原生 Agent 已经能完成的工作可以直接完成；Fleet 推荐不得隐藏原生可用能力，也不得强迫所有操作经过 Fleet。
10. 资源、Session、生产、密钥等事实必须来自对应权威；Agent 的临时命令输出不能成为第二份系统真相。
11. UNKNOWN 不等于 NO、UNAVAILABLE、UNSUPPORTED、0 或健康。
12. 告警不等于授权；资源告警不得自动 kill、stop、delete、restart 或清理未知归属资源。
13. 秘密材料不得进入 Prompt、Ability Graph、日志、普通报告或 Feishu 卡片。
14. Progressive Disclosure 优先：能力目录可以完整，但启动上下文必须小，按任务展开。

## 2. 三设备职责模型

三台设备共享 Ability Registry，但不要求安装完全相同的能力。设备角色决定默认能力组合；最终可执行性必须由实时设备证据决定。

| 设备 | 产品角色 | 主要工作负载 | 资源和治理重点 |
|---|---|---|---|
| Tokyo development-primary | 主开发节点、Fleet Hub、项目和工作流入口 | 需求、研究、设计、代码、测试、GitHub、Feishu、生产只读观察 | 多用户共享主机、个人 quota、整机 CPU/RAM/PSI、系统盘与数据盘、Docker、常驻服务 |
| Dell-LLM-Lab / A6000 | GPU 计算和模型工作节点 | GPU/模型计算、构建、评估、测试、生产观察及获准的变更准备 | GPU 显存与计算利用率、CPU/RAM、任务归属、磁盘、模型服务和共享网关 |
| FJPCOS / Windows corporate workstation | Windows 原生客户端与企业环境节点 | Windows GUI、浏览器、SCM、原生客户端、Windows 服务、隧道和企业环境验证 | Windows CPU/RAM/磁盘、实际 GUI/SCM/网络服务、Tailscale/SSH Tunnel/Selkies/RDC；Linux PSI 等指标应为不适用或未知，不能填 0 |

### 2.1 当前已知的设备事实和缺口

- Tokyo 的 profile 已记录 Linux shared multi-user 形态：系统盘约 50 GB，数据盘 /data 约 4 TB XFS，启用 user/project quota；多个用户共享同一数据盘。不能把整机容量、个人 quota 和个人工作负载混为一谈。
- Tokyo 的 workspace.src、workspace.worktrees 等角色已有设备侧绑定，但“路径已绑定”不等于目录已收敛、资源有明确 owner 或可以清理。
- A6000 当前运行主体已确定为 fjd，不能沿用旧的 vince 路径。当前 Fleet profile 将 data root、workspace source、worktrees、artifacts、state、toolchains、runtime-assets 等多个路径角色标为 UNBOUND；profile 说明曾观察到约 37 个 worktree，但尚未登记规范路径。此项是 Workspace 与资源治理的高优先级缺口。
- Windows 必须按其自己的 host profile、Runtime 和设备能力读取；不得将 Linux 设备的路径、PSI 或服务能力复制过来。
- 上述 profile 是设备职责与已记录事实，不等于三设备的所有指标和安装状态都已完成实时验收。

## 3. 核心产品需求

### 3.1 FLEET-PERF：插件性能必须可测、可解释、可控制

**用户问题**

启用 Fleet 插件后，ChatGPT / 浏览器 / Agent 交互明显变慢，但目前不能明确指出是 MCP 初始化、工具定义、Skill 描述、工具发现、网络往返、Hub 查询还是设备响应造成的。

**需求**

- 建立未安装 Fleet、仅基础 Fleet、完整 Fleet 三种可重复对照基线。
- 分别测量冷启动和热启动，不以单次体感代替性能证据。
- 统计 MCP initialize、tools/list、工具 Schema 总字节数、工具数量、Skill metadata token 预算、首个工具调用、常用工具调用、Session send 到 Agent 开始执行的延迟。
- 测量插件启用前后的 ChatGPT 页面响应、浏览器 CPU/RAM、网络请求量和后台活动；区分客户端开销与服务端开销。
- 记录每次工具发现和路由实际使用了哪些工具，识别重复暴露、重复查询、无关 Schema 和不必要的全量扫描。
- 工具面按任务和权限渐进披露；不为了“能力丰富”把全部工具和全部 Skill 正文注入每个 Session。
- 保留必要的诊断能力，但常规任务不得触发昂贵的全量诊断。

**验收**

1. 有同一设备、同一浏览器、同一账号和同一任务的对照测试。
2. 报告冷/热启动、p50/p95、Schema/上下文开销和客户端资源变化。
3. 能定位主要开销来自客户端、MCP、Hub、设备传输还是 Agent Runtime。
4. 能按需缩减暴露面，而不破坏原生 Claude/Codex 的能力发现。
5. 性能预算在取得基线后由 Owner 冻结；在没有实测前不编造“已达到某毫秒”的目标。

### 3.2 FLEET-ROUTE：低延迟直达 Agent，复杂任务才协调

**用户问题**

通过指挥层完成简单任务，比直接向目标 Agent 下令更绕、更慢。指挥端可能重复查询设备、Session、能力和状态，增加无价值往返。

**目标路由**

| 路径 | 使用条件 | 行为 |
|---|---|---|
| Native Direct | 已有明确目标 Agent / Session，任务在其当前能力与授权范围内 | 直接发送用户意图，不先执行全量 Fleet 盘点 |
| Fleet Fast Route | 需要确认设备、Runtime、当前 Session 或一个关键 Ability | 只查缺少的事实，完成一次路由后直接发送 |
| Coordinated Route | 多设备并行、跨设备接续、需要依赖协调或汇总 | GPT 规划，Fleet 协调，设备 Agent 自主执行 |
| Human-gated Route | 生产变更、密钥、删除、权限变更、部署等高风险效果 | 先准备证据和执行包，按既定授权边界等待人类决策 |

**需求**

- 对已有目标 Session 优先复用，不为每个任务新建 Session。
- 简单任务不要求先读取完整设备清单、全量 Ability Tree、全部 Session 和所有 Provider 健康。
- 目标设备、Session、项目和 Runtime 已明确时，不重复询问或重新推断。
- 一次路由只获取决策所需的最少信息；已有缓存必须带 revision、时间和 freshness，过期时只刷新相关对象。
- 直接向 Agent 下令应是正常路径，而不是 Fleet 的异常旁路。
- 复杂任务仍可使用 Work Engine、Lease、Checkpoint、Effect Receipt 和跨设备协调，不能为追求速度绕过安全约束。
- 不得让通知系统、Feishu 卡片或 GitHub Issue 变成第二个实时 command bus。

**链路观测**

统一记录：用户/指挥端起始时间、路由决策、设备解析、Ability resolve、Session 选择、dispatch、Agent 开始、Agent 完成、结果读取、批次关闭、最后一次卡片更新、指挥端回复。每个阶段都要能计算等待时间与实际执行时间。

当前主分支已加入从指挥端开始到最后卡片更新的链路时间记录；这属于观测基础，不等于已完成直接路由优化，也不等于已经证明 p95 足够低。

**验收**

- 同一任务分别走 Native Direct 与 Fleet Fast Route，比较总耗时和额外往返。
- 简单单设备任务不得因为 Fleet 自身的查询/编排显著慢于直接发送。
- 多设备任务能够解释每次必要协调的原因。
- 失败或未知结果先核对实际 Session/Run，不盲目重发。
- 不以工具调用成功代替 Agent 已开始执行或任务已完成。

### 3.3 FLEET-FEISHU-UX：重做播报文案、卡片信息层级和视觉

**用户问题**

当前播报的文案和卡片不好看、不好读，技术字段和重复说明影响首屏理解。Hub Watcher README 在 2026-11-10 已记录 Owner 对可读性的负面反馈；现有 Card Spec 和测试不代表真实飞书客户端体验已通过。

**产品表面**

1. Daily Status Card：回答“现在怎么样”。
2. Incident Card：回答“发生了什么、影响谁、需要谁做什么”。
3. Task Completion Card：回答“哪些任务真实完成，结果在哪里”。
4. Daily Report：回答“今天完成了什么、还有什么未解决、哪些问题需要决策”。

这些是人类阅读表面，不是四套独立状态数据库。

**文案要求**

- 第一行先给自然、明确的结论，不以系统名、UID、错误码、计数或技术缩写开头。
- 说明设备/工作对象、实际状态、影响和下一动作。
- 同一状态只表达一次；相同事实不在标题、正文、脚注和详情重复。
- 不将 UNKNOWN 写成健康，不把没有数据写成 0。
- 区分“当前仍异常”“本周期已恢复”“历史累计”和“需要人工决策”。
- 健康数字不滥用颜色；只有需要注意的问题突出显示。
- 技术字段、原始日志、PID、fingerprint、内部 ID 放到详情，不占首屏。
- 文案要像专业运维助理，而不是把程序日志翻译成中文。

**视觉要求**

- 重新设计标题、摘要、分组、数字、状态标签、留白、详情入口、按钮和恢复态。
- 统一资源、Incident、完成和日报的视觉语言。
- 以真实 Feishu 客户端为准，不能仅凭 JSON 合法、CardKit fixture 或 HTML 预览宣称验收。
- 至少验收手机窄屏和桌面端；正常状态首屏无需展开即可读懂关键结论。
- 按钮必须完成真实回调、身份检查和语义操作；按钮可见不代表功能可用。
- 每类卡片都需要正常、异常、未知、恢复、升级、空结果和长文本样本。

**验收**

- Owner 对真实飞书截图确认文案和视觉。
- 真实飞书消息投递与 PATCH 回执可追踪。
- 卡片默认折叠态能读懂结论和影响。
- 点击按钮的真实回调通过端到端验证。
- 同一 Incident 不重复轰炸，同一事实不重复出现。

### 3.4 FLEET-RESOURCE：形成三设备资源理解模型和告警政策

**用户问题**

系统能采集一些 CPU、内存、磁盘、GPU 和服务读数，但对设备实际能力、资源归属、负载影响和告警价值理解不足。现在还没有把“资源状态”“异常 finding”“需要通知人”区分清楚。

#### 3.4.1 每台设备必须有资源画像

每台设备的 Resource Profile 至少包含：

| 维度 | 需要掌握的事实 |
|---|---|
| CPU | 逻辑核数、利用率、持续饱和、runnable/队列、CPU quota、关键进程归属 |
| 内存 | 总量、available、RSS/cgroup、每 principal 用量、OOM 风险、回收与压力 |
| Swap / PSI | Swap 使用量、持续换入换出、memory PSI some/full、采样窗口 |
| 存储 | 每个 mount 的角色、总量、可用量、百分比、inode、quota、IO 错误/只读状态 |
| GPU | 设备型号、显存总量/可用量、计算利用率、进程与 Job 归属、当前任务需求 |
| 网络 | 网卡/链路、延迟、丢包、带宽、Tailscale/隧道/关键 endpoint 的实际健康 |
| 服务 | serverosd/Core、Connector、共享网关、数据库、必要隧道和当前 Work 依赖 |
| 工作负载 | Agent、Session、Run、容器、构建、模型任务与资源消耗之间的可信归属 |
| 观测质量 | source、observed_at、freshness、coverage、permission、UNKNOWN 原因 |

不得仅凭进程名、容器名或端口推断 owner。ServerOS 创建的资源应在创建时登记 identity、owner、lifecycle；外部发现资源只有在有证据时才能归属。

#### 3.4.2 告警必须有分层政策

资源有读数不代表需要发 Incident。至少分为：

| 级别 | 定义 | 通知方式 |
|---|---|---|
| P1 · Interrupting | 关键设备/服务失效、严重容量风险、OOM/持续压力导致工作中断、关键路径不可用 | 立即通知 |
| P2 · Attention | 持续资源压力、容量预警、重要服务 degraded、关键观测失明或即将影响当前工作 | 需要关注，按策略通知 |
| P3 · Review | 单进程偏高、历史遗留、无当前影响的 owner/生命周期问题、可治理但不紧急的 finding | 进入诊断或定期治理，不逐条推送 |

告警必须考虑持续时间、采样窗口、实际影响、当前 Work/standing dependency、owner、恢复滞回、去重和冷却时间。

#### 3.4.3 当前阈值是已有默认值，不是最终三设备政策

当前 **runtime/attribution-contract.yaml** 已有全局默认基线，例如：

- 文件系统 warning 85%、critical 92%；
- inode warning 85%；
- host memory 90%；
- swap 90% 只是 watermark，必须结合 PSI 和 paging activity；
- memory PSI some avg60 1.0%、full avg60 0.5%；
- per-account memory sustained 30%、critical 50%；
- Docker storage 85%。

这些值说明阈值机制已存在，但不能直接当成三台设备的最终报警规则。系统盘 50 GB 与数据盘 4 TB、GPU worker 与多用户共享主机、Windows 与 Linux 的资源语义不同。

**阈值必须绑定设备角色、mount role、principal、资源类型和实际负载。** 百分比和绝对剩余量要结合；Swap 使用率单独不能证明当前内存压力；Load 必须结合 CPU 核数和 runnable；单进程 RSS 高不等于整机异常。

当前 Hub Watcher 文档还指出 GPU 显存展示已有，但没有声明 GPU 告警阈值。GPU 应进一步结合当前任务、显存余量、利用率、任务失败和排队压力制定政策，不能只按显存百分比机械报警。

#### 3.4.4 三设备的专项关注

- Tokyo：区分个人 CPU/RAM、个人 XFS quota、整机 CPU/RAM、系统盘 /、数据盘 /data、Docker 和共享服务；避免把多用户负载归到错误主体。
- A6000：建立 GPU Job、显存、CPU/RAM、磁盘、模型服务与任务的资源归属；先完成 fjd 主体下的真实路径和资源 Binding，再设阈值。
- Windows：以真实 Windows 指标、SCM 服务、GUI/隧道和当前客户端依赖为准；不适用的 Linux 指标标记 NOT_APPLICABLE，不得显示为 0 或健康。

**验收**

- 三台设备各有一份基于本机实测的 Resource Profile。
- 每个关键指标都能说明 source、时间、口径、owner 和 freshness。
- 每种告警规则能用正常、边界、持续异常、恢复、缺失观测样本测试。
- 告警能解释“为什么现在通知人”，并能反查原始读数和关联 Work。
- 资源状态、诊断 finding、Incident 和自动恢复权限严格分离。

### 3.5 FLEET-ABILITY：统一 Ability、Skill、Plugin、MCP 和三设备版本

**用户问题**

Ability 家族、Skill 和设备实际安装存在不一致；部分能力有多个 revision，却没有明确的版本选择和运行时 readback。能力在仓库中存在，不代表 Agent 真能使用。

**唯一映射模型**

每项能力都必须可追溯：

Product Ability / Sub-Ability → Capability Operation → Canonical Artifact → Revision → Package/Plugin Membership → Device Profile → Runtime Materialization → Effective Resolution

每个映射至少记录：

- 稳定 identity、显示名称、历史别名、renamed-from。
- canonical repository/path 和 source revision。
- Skill / Plugin / MCP / Hook / Instruction / Provider 的 typed identity。
- 语义职责和 trigger / not-for。
- 版本、fingerprint、compatibility、依赖 closure。
- Tokyo / A6000 / Windows 各自的 desired profile。
- Claude Code / Codex 各自的安装、加载和实际 Runtime 观察。
- supported、ready、grant、effective 和缺失原因。
- 运行时使用记录、评估结果、升级和回滚来源。

必须区分：

DECLARED → INSTALLED → LOADED → READY → GRANTED → EFFECTIVE

任何缺失轴都保留 UNKNOWN，并说明缺少什么证据。

**当前明确的对齐问题**

1. production-observation 的 canonical Skill 和产品职责已经存在，Tokyo 与 A6000 desired profile 也引用了它；但当前 manifest 对应 revision 的 installations 为空。必须证明实际只读生产 Binding、Provider、设备安装、版本和权限。
2. production-change 与 production-observation 必须保持两个不同的职责和效果边界。production-operations 目前是家族导航，不是可执行 Artifact。
3. Workspace Artifact 的 canonical identity 是 ProfessorZhi/workspace-maintainer，Skill 显示名称是 workspace-management；manifest 中存在多个 revision fingerprint，语义版本和 installation 记录并不完整。必须建立明确 alias、revision pin、兼容性和实际安装关系。
4. Windows Profile 明确注明其 Workspace 专用实现没有从当前配置分发，必须单独验收，不得把 Linux Skill 安装状态推定为 Windows 可用。
5. Plugin bundled、recommended、standalone、reference-only 和 unavailable 必须有准确区别，不能因一个 Plugin 声明推荐某能力就把它当成已经安装。
6. 每台设备、每个 OS principal、每种 Agent Runtime 都要有独立的有效能力结果。

**验收**

- 任意用户需求都能解析到正确的语义 Ability 和实际实现。
- 任意设备都能回答某能力当前是否可执行、由哪个 Provider 提供、使用哪个 revision、缺少哪个条件。
- 版本升级有兼容性检查、差异、回滚和真实 consumer 验收。
- Claude Code 与 Codex 分别进行 fresh-session discovery/readback。
- 不再存在“Skill 在目录中、manifest 认为已装、实际 Runtime 却无法发现”的静默状态。

### 3.6 FLEET-DISCOVERY：Agent 自动发现合适的能力

**目标**

用户只描述任务，不需要先知道 Skill、MCP 或 Plugin 的名字。Agent 应能自主找到适合当前任务、设备和 Runtime 的能力。

**发现流程**

1. 识别用户意图和当前 Work Context。
2. 优先识别原生 Agent 已有能力和当前 Session 已可用工具。
3. 从 Ability Registry 检索少量语义候选。
4. 根据任务、设备、OS、Runtime、版本、Provider、Target 和适用条件过滤。
5. 解析当前 readiness、grant 和有效性。
6. 向 Agent 展示精简的能力说明和选择理由。
7. Agent 选择后才读取 root Skill。
8. 只加载当前 workflow 需要的 references、scripts 和 MCP schemas。
9. 执行后记录实际使用的能力、版本和结果。

**要求**

- 完整 Ability Tree 不进入每个 Session 的启动上下文。
- Agent 不必先加载 ability-management Skill 才能发现其他 Skill，避免 bootstrap 环。
- Native-first；原生工具可直接用，推荐器不得屏蔽其他有效能力。
- 能力发现不代表授权；未授权操作仍被 policy 拒绝。
- 已安装能力可按需加载；新安装、外部代码、Hooks、可执行插件、新凭据和网络边界按准入规则处理。
- 只有当前 Runtime 确实支持动态工具发现/attachment 时才使用；不支持时明确需要新 Session 或安全 CLI，不得伪称 hot reload 成功。

**验收**

- 用真实任务测试自动发现，而不是只测搜索 API。
- 对 workspace 治理、生产只读查询、GPU 测试、Feishu 文档等典型意图，Agent 能找到正确候选。
- 不加载无关 Skill，不重复暴露已有 MCP，不把完整目录注入 Prompt。
- 发现不到时能解释缺失原因，并允许继续查更广的目录或原生工具。

### 3.7 FLEET-WORKSPACE：Workspace Management 必须解决真实目录混乱

**用户问题**

当前设备和项目的目录存在历史遗留、路径不一致、产物错位和 Workspace Ability 分发不完整的问题。仅仅拥有 workspace-management Skill 不等于目录治理已经完成。

**目标目录语义**

稳定的逻辑角色包括：

- workspace/src：canonical source checkout，默认 read-mostly。
- workspace/worktrees：每个 Item 独立的开发 worktree。
- workspace/test-envs：测试环境定义。
- workspace/ops：治理、地图和运维资料。
- workspace/knowledge：可复用知识。
- workspace/records：正式记录。
- artifacts：Item Run 与 Skill Run 证据。
- state：ServerOS machine state。
- toolchains、runtime-assets、caches、envs、downloads、logs、data：按 DATA_ROOT 和机器角色解析。

**自动化要求**

- Agent 在创建 repo、worktree、测试环境、报告、日志和 Run 产物前自动解析语义角色，不让用户反复指定目录。
- 所有持久化路径通过当前设备的 path contract / resolve / admit 得出，不从其他设备复制绝对路径。
- 新产物自动放入正确位置；不确定唯一 parent 时保留安全位置并报告缺口，不发明新顶层目录。
- Workspace Management 自动检查当前任务相关的 HEAD、dirty、owner、lease、文件占用和保护标记。
- 提供全机只读盘点：目录分类、深度、容量、重复语义 root、路径引用、owner、活动状态、历史遗留和治理例外。
- 生成可审阅的 desired layout 与迁移 proposal。
- 大规模移动、重命名、合并、删除和 root-level 重构必须经过 proposal → approval → apply → verify；未知 owner、active Run、dirty worktree、唯一证据和 protected WIP 一律 DEFER。
- 默认 Prevent > Detect > Reconcile；先修正新产物生产者和默认路径，再处理历史目录。
- Map 是可重建 projection，不得成为第二份手工目录权威。

**当前重点**

- Tokyo 的路径角色已有绑定，但目录整体仍需要治理盘点。
- A6000 的 fjd profile 中多个 Workspace 和数据角色仍为 UNBOUND，已有 worktree 的规范路径尚未完成登记。必须先完成本机只读 inventory 和 path binding，再进行整理。
- Windows 使用独立 profile 和路径，不得套用 Linux 目录规则的物理路径。

**验收**

- 新产物不再继续写入错误位置。
- 每个持久化对象有 owner、role、lifecycle 和稳定 locator。
- 目录治理报告能区分已知活动、可再生缓存、历史保留、待迁移、未知 owner。
- 普通任务不会因为全机治理而被阻塞。
- 迁移后实际 consumer 能读回正确版本和内容。
- 未经批准不移动或删除任何受保护数据。

### 3.8 FLEET-GOVERNANCE：其余治理域的统一要求

以下领域继续属于 Fleet / ServerOS 产品范围，不因本轮重点放在性能和播报而被遗漏。

| 治理域 | 必须提供的能力 | 验收重点 |
|---|---|---|
| Feishu Space / Drive / IM | 空间结构、ACL、文档/文件、消息来源、附件、会议材料、归档、Follow 和版本 | 内容源与 Human Control 分离；能回到原始来源；归档范围和权限可解释 |
| GitHub | Repo、Branch、Worktree、Issue、PR、Commit、Release、跨仓依赖和交付证据治理 | GitHub 管代码事实；Work Engine 管正式 Work 状态；不重复造状态库 |
| Session | Session 创建、选择、发送、读取、interrupt、resume、handoff、archive、restore、身份和写入隔离 | 生命周期动词准确；handoff 后旧 writer 被 fence；未知效果先 reconcile |
| Logs / Audit / Trace | 统一关联 ID、来源、时间、设备、principal、Runtime、revision、错误、回执、保留和脱敏 | 能从 Feishu 卡片反查 Run、Session、设备日志和代码版本；默认不复制敏感原文 |
| Memory / Cross-device | Durable ledger、semantic/episodic retrieval、checkpoint、handoff、correction 和失效 | 跨设备能继续工作；记忆是有来源的上下文，不替代 Work、Registry 和 Runtime 真相 |
| Credential / Secrets | Secret reference、设备绑定、权限范围、健康、过期、轮换、撤销和审计 | secret 不进 Prompt/Log/Card；泄露后能轮换并验证所有 consumer |
| Device / Runtime | 三设备 OS principal、CC/Codex、Provider、模型、权限、网络和 Core readback | 不从设备在线推定 Runtime 可用；每个 principal 独立验收 |

## 4. 当前仓库证据与缺口基线

第一轮为 2026-11-12 对主仓和 Ability 仓库的只读核对；2026-11-13 的深入审计补充见第 8–13 节。所有内容描述仓库证据，不代替设备实时验收。

| 领域 | 已有资产 / 证据 | 当前缺口与判断 |
|---|---|---|
| 插件性能 | MCP、Skill progressive disclosure、插件分层和工具测试已存在 | 未找到完整的插件启用前后性能基线、客户端资源对照和 p50/p95 报告；根因未确认 |
| 指挥延迟 | 主分支已有从指挥端开始到设备执行、结果读取和最后卡片更新的链路时间记录 | 尚无 Native Direct 与 Fleet Fast Route 的同任务对照和 p95 目标 |
| 直达 Agent | Capability Discovery 合同已明确 native-first | 仍需证明常见简单任务实际走最短路径，没有重复查询或强制编排 |
| Feishu 播报 | Hub Watcher、资源/Incident/完成卡、日报、Card Spec、投递逻辑和测试存在 | Hub Watcher README 记录 2026-11-10 Owner 对可读性的负面反馈；需要真实飞书客户端重新设计和验收 |
| 资源理解 | Core-Lite、Resource Attribution、Anomaly、P1/P2/P3、阈值和卡片架构已有实现/合同 | 当前全局默认阈值不能替代三设备实例 policy；GPU 告警规则未完整声明；设备资源画像和工作负载归属需重新核验 |
| Ability 家族 | 21 个语义职责、manifest、Profile、Ability Projection、Resolver 和大量 Skill/Plugin/MCP 已存在 | 不能据此声称三设备有效能力完成；manifest revision、实际安装、加载、ready、grant 和 runtime readback 仍需逐设备对齐 |
| Production Observation | canonical Skill 存在；Tokyo 与 A6000 desired profile 引用 | 当前 manifest 对应 revision 的 installation 记录为空；需要验证只读生产 Binding、Provider、principal 和授权 |
| Workspace Management | workspace-management Skill、path contract、admission、fs_hygiene、目录规范与测试存在 | Artifact identity 与显示名称/历史 alias 不一致；多 Revision 缺少完整版本/安装证据；A6000 多个 path role UNBOUND；目录收敛未完成 |
| Agent 自动发现 | Native-first 和 PD0–PD5 合同存在 | 必须按当前 CC/Codex Runtime 分别证明真实 discovery、load、dynamic attachment 或安全 fallback |
| Session / Logs / Memory / Secrets | 对应模块、合同、Issue 和工具已经存在 | 必须以真实效果隔离、端到端回执、跨设备召回和密钥轮换证据完成闭环 |

### 4.1 必须注意的历史证据

- integrations/device-ability-projection/README.md 中的覆盖测量记录来自 2026-10-20 的旧 manifest revision；其中 Windows 无设备快照、Dell A6000 device id unresolved、ready/grant 为 UNKNOWN。它是历史证据，不应冒充当前 11 月 12 日设备状态。需要使用当前 manifest 和当前设备身份重新运行。
- 当前 Ability Manifest 中，production-observation profile 引用与 installation records 不是同一事实。
- Workspace Artifact 的 revision fingerprints 不能被直接理解成多个已发布语义版本；版本字段、兼容性、canonical source 和设备安装必须一起核对。
- 当前资源阈值文件中的默认值不是三台设备最终告警配置；设备 host profile、mount role、principal 和实际负载决定实例化政策。

## 5. 统一状态模型和完成定义

每个产品需求、Ability、设备投影和治理对象必须分开报告以下状态：

| 状态 | 含义 |
|---|---|
| DESIGNED | 产品合同或设计文档存在 |
| IMPLEMENTED | 源码/配置存在且有相应测试 |
| INSTALLED | 目标设备真实安装了指定 revision |
| LOADED | 目标 Runtime 能发现并加载 |
| READY | Provider/依赖/健康状态满足 |
| GRANTED | 当前 principal 对当前 operation 有效授权 |
| EFFECTIVE | 上述条件在当前设备、Runtime、Target 下成立 |
| E2E_VERIFIED | 真实用户路径、真实设备和真实结果已验收 |
| UNKNOWN / BLOCKED | 证据缺失或当前条件不成立，必须说明原因 |

不允许把 DESIGNED 写成 IMPLEMENTED，不允许把 IMPLEMENTED 写成 INSTALLED，不允许把 INSTALLED 写成 EFFECTIVE，也不允许把 fixture PASS 写成 REAL_DEVICE 或 CLIENT_E2E PASS。

每项需求至少维护：需求 ID、Owner、canonical contract、实现仓库/路径、测试、目标设备/Runtime、当前 revision、部署/运行证据、阻塞项、验收结果和下一步。

本文件是产品需求入口；详细执行状态继续由既有 Work Engine / Receipt / GitHub Issue / 设备 readback 承载，不另建第二套工作状态数据库。

## 6. 优先级与实施顺序

### P0：先解决系统自身的低效和能力可信度

1. 插件性能基线：确定真正的客户端、MCP、Schema、Skill、网络和设备开销。
2. 指挥链延迟：建立 Native Direct / Fleet Fast Route / Coordinated Route 的对照测试。
3. 简单任务直达 Agent：复用现有 Session，减少无意义往返。
4. 三设备 Ability 对齐：先核实 canonical revision、desired profile、installation、runtime readback、ready 和 grant。
5. Production Observation：明确只读生产入口、target binding、provider、权限和实际 Skill 的对应关系。
6. Workspace：优先修正 A6000 fjd 路径 Binding，盘点 Tokyo/A6000/Windows 的真实目录状态。
7. Session 写入隔离、密钥轮换和日志脱敏等安全阻塞不得被性能优化绕过。

### P1：提升日常运行体验

1. Feishu 播报文案和卡片视觉重做，完成真实客户端验收。
2. 三设备资源画像、实例化阈值和低噪声告警。
3. Agent 自动 Ability Discovery、版本选择和按需加载。
4. Workspace 新产物自动落位、目录漂移检测和受控迁移。
5. Agent 效率指标：dispatch、queue、execution、completion latency，成功率、重试率、资源成本和工具调用成本。

### P2：补齐完整治理闭环

1. Feishu Space / Drive / IM / Archive。
2. GitHub 多仓库项目治理和交付证据。
3. Session 历史、归档、恢复与跨设备接续。
4. 日志长期保留、检索、关联与审计。
5. 跨设备 Memory 自动 Capture / Recall / Correction。
6. Credential 生命周期、健康、rotation 和 consumer reconciliation。

以上优先级用于安排产品建设，不表示 P2 范围可以不做。

## 7. 本轮需要 Owner 冻结的产品决策

1. **性能预算**：先取得对照基线，再冻结冷/热启动、工具发现、常用调用和浏览器资源的可接受开销。
2. **直达规则**：明确哪些任务默认 Native Direct，哪些需要 Fleet Fast Route，哪些必须 Coordinated / Human-gated。
3. **Feishu 视觉方向**：基于真实卡片截图确认文案语气、信息密度、颜色、层级和手机布局。
4. **资源告警**：按三设备真实资源画像确定 warning/critical、持续窗口、恢复滞回、通知对象和 P1/P2/P3，不直接照搬全局默认阈值。
5. **Ability 版本策略**：确定 canonical revision、兼容范围、设备 pin、升级与回滚规则；旧 alias 保留可追溯，不随意改 identity。
6. **自动发现边界**：默认自动发现和按需加载；新安装、外部执行代码、Hook、凭据、网络和高风险效果遵循准入。
7. **Workspace 迁移边界**：普通新产物自动落位；历史结构性移动、重命名、合并和删除必须单独 proposal 与批准。

## 8. 目录域与权威边界：磁盘、飞书、Ability 源码、Runtime 安装必须分开

“目录”至少是四种不同对象，不得共用一份物理目录树，也不得互相推断：

| 域 | 对象和身份 | 唯一职责 Owner | 主要规则 |
|---|---|---|---|
| D1 · 设备磁盘目录 | Host filesystem、mount、Path Role、Repo、Worktree、Artifact、State | Workspace Management + ServerOS Path Contract | 物理路径按设备、OS principal、volume 和 Path Role 解析；禁止跨设备复制绝对路径 |
| D2 · 飞书云目录 | Tenant、Space、Wiki Node、Drive Folder/File、Minutes、ACL、Owner、Revision | Feishu Space Management | 以 Provider + tenant/security domain + native object ID 为身份；目录名不是身份 |
| D3 · Ability 源码目录 | Canonical Skill / Plugin / MCP / Hook 等 Artifact、Manifest、Revision | ability-management | Canonical Source 是唯一可编辑源；产品树是逻辑投影，物理源码目录保持稳定 |
| D4 · Agent Runtime 安装目录 | Claude/Codex Skills、Plugin Cache、MCP config、Hooks、Instructions、Subagents | Ability Distribution + Runtime Adapter | Runtime 目录是 Installation，不是 Canonical Source；只能单向 materialize 和 readback |

### 8.1 D1 磁盘目录

Workspace 的六个主要逻辑区域继续保持：src、worktrees、test-envs、ops、records、knowledge；其它数据按 Host Profile 和 Path Contract 放入对应位置。

- Tokyo：路径绑定相对完整，但绑定不等于目录已经收敛。
- A6000：当前 linux-gpu-compute profile 中 data root、workspace source、worktrees、artifacts、state、toolchains、runtime-assets 等仍有多个 UNBOUND。已有 worktree 观察记录不能代替规范路径绑定。
- FJPCOS：D:\\projects、D:\\apps、D:\\develop 等路径多数来自 user_reported；workspace.worktrees、state、runtime-assets、home 等仍有未绑定角色，artifacts 路径也需要设备侧核验。

任何新产物必须先调用 Path Resolve / Admit；历史目录治理先只读盘点、生成 proposal，再经 approval → apply → verify。不得把目录数量变少当成成功，也不得自动清理未知 owner、活动 Run 或受保护 WIP。

### 8.2 D2 飞书目录

Feishu 不是本地文件夹的云端副本。现有信息架构已经区分：

- Feishu Wiki / Doc：人类阅读和维护的内容表面。
- Records / Git：可审计、可 diff、可恢复的记录与受控镜像。
- Feishu Drive：大字节资产层，保存 PDF、图片、录音、视频、附件和测试材料；Git 默认只保留必要 metadata、hash 和 locator。

三类文档关系必须保留：

- FOLLOW_MIRROR：外部只读来源，Source 仍是正文权威。
- EXTERNAL_MAINTAINED：别人的位置，只维护获准范围。
- OWNED：我们拥有的正文，按既定 body authority 和 readback 维护。

三端同步不是三主双向 mtime sync。Feishu Space Management 管对象位置、ACL、Map、生命周期和 drift；Document Stewardship 管正文关系、局部 patch、冲突、revision 和 readback；Information Management 管来源理解、claims、provenance 和 redaction；Feishu Report 管正式报告发布；B10 管通知。

现有 Feishu 信息架构的 live 基线记录于 2026-09-24，文档明确标注 DESIGN != LIVE，且当时没有重新验证当前 ACL、所有云空间和三端同步。此前记录的外部文档 Space、Drive 根目录和 Records 布局只能作为历史基线。实施前必须按 stable locator 重新读取当前对象、父子关系、ACL、revision 和实际消费者。

### 8.3 D3 / D4 Ability 目录

Ability Product Tree 是逻辑树；Resolver 使用的是 Ability ↔ Provider ↔ Profile ↔ Binding 的机器图；源码物理目录按稳定 identity 保持扁平，例如 ability-management/skills/<ability-id>/。不要为了“目录看起来整齐”把 Skill 源码物理搬进 A1/A2/A3 子目录。

Runtime 安装目录是由 canonical payload 生成的只读副本。禁止在 Claude/Codex runtime 目录分别手工修复；必须回到 canonical source，生成新 Revision，再分发和 readback。

## 9. 指挥侧与设备侧：采用双层发现、单一执行权

### 9.1 产品分工

| 能力类型 | 指挥侧（GPT / Fleet MCP） | 设备侧（Claude Code / Codex Agent） |
|---|---|---|
| Fleet Control MCP | 主动调用：设备、Session、派发、批次、Trace、Inbox、状态和授权范围内的控制动作 | 不重复代理这些全局控制动作 |
| 本地 Skill | 不逐个加载设备专属 Skill；只识别语义需求和目标设备 | Native-first 自动发现，必要时调用本机 ability.search |
| 本地 MCP / CLI / Script | 不把所有本地工具 Schema 搬到 GPT 工具面 | 在目标 Runtime 按需发现和调用 |
| Plugin | 负责版本、发布、安装策略和目标范围，不是每个任务都调用一次 | 安装后由 Runtime 暴露其中的 Skill/MCP；按 Runtime 支持情况刷新或新建 Session |
| 共享云服务 Provider | 只有当操作属于 Hub/全局表面时才直接调用 | 需要本地身份、数据、网络或工作区时由目标 Agent 调用 |
| Ability Management | 用户要求上架、审核、版本、下架或分发时主动调用 | 任务执行前本机检索、resolve、按需加载；安装仍经过明确写操作 |

**Plugin 是 Packaging，不是 Task-time Tool。Skill 是 Procedure，不是 MCP。MCP 是某个 Runtime 的工具表面。Provider 是实际执行某个 Operation 的实现。Ability 是语义职责。Binding 决定它在哪台设备、哪个 principal、哪个 target 上真正可用。**

同一个云服务可以在 Hub 和设备侧有不同 Adapter，但每个 Operation 必须有明确的 Owner、权限和审计，不允许出现两个工具都声称是同一操作的权威入口。

### 9.2 正常任务路径

1. GPT 先把用户意图映射到语义 Ability / Operation，而不是先挑某个 Skill 名字。
2. 如果当前 Agent 已能完成，直接让当前 Agent 执行，不调用 Fleet 做全量设备盘点。
3. 如果需要另一设备，指挥侧查询最小的全局能力摘要，选择满足 Binding、Runtime、Readiness 和 Grant 的设备。
4. GPT 使用 Fleet MCP 发送有边界的 Task Envelope：目标、上下文 locator、operation、数据范围、预算、验收条件和返回格式。
5. 目标设备 Agent 优先使用原生工具和已安装 Skill；未明确匹配时调用本地 ability.search，查看 metadata，再进行本机 resolution。
6. 目标 Agent 加载所需 Skill、MCP 或脚本并执行，不等待 GPT 为每个子工具逐次下令。
7. Agent 返回 Execution Receipt：device_ref、principal、runtime、ability identity、revision、provider、operation、target binding、授权结果、证据 locator、结果和 freshness。
8. GPT 负责验收结果、更新父 Work 和决定是否需要下一台设备。Work Engine 仍是工作状态权威。

### 9.3 自动发现的边界

- 常驻 Context 只放小型 Resident 核心能力；其它能力保持 Catalog，不把完整目录或所有 MCP Schema 注入每个 Session。
- 已安装的原生 Skill / Tool 由 Claude/Codex 原生发现优先处理。
- 原生发现没有命中、任务跨领域或需要查版本时，再调用本地 ability.search。
- 当前 ability.search 是 substring 检索，只返回 trigger hints，不返回 avoid hints；它不是完整语义任务路由器。
- 当前 ability.resolve 支持 operation reference，例如 ability:<id>#<verb>，但请求中传入的 providers、instances、health、profile、runtime_binding 等属于 caller claims，不是自动采集的设备事实，也不签发授权。
- 当前 ability.inspect 明确把 resolution 标为 not attempted；“在目录中”不等于“本设备可执行”。
- 新 MCP、Hook、可执行 Plugin、凭据或网络权限不能因为被发现就自动安装或授权。动态加载只有在目标 Runtime 已验证支持时才可宣称；否则需要安全 CLI、明确 rollout 或新 Session。

**推荐的平衡：GPT 负责跨设备和全局控制，设备 Agent 负责本地能力选择与执行。GPT 不需要知道每台设备上几十个工具的细节，但必须知道每台设备有哪些语义 Operation 可以被真实调用。**

## 10. Ability 上架、发布、安装、下架和退役

### 10.1 当前已经实现的部分

Ability Management 的 control_plane.py 已有：

- scan / fingerprint / sanitize / reconcile / sync-self
- publish（构建和发布 vendored Plugin 制品）
- install / update / readback / rollout
- fleet-sync（Desired Profile 与 Observed Snapshot 对账）
- catalog list / search / install（按 pin Revision 安装 Skill）

catalog.yaml 已采用 Resident / Catalog 两层设计，并有 first-party 与 approved third-party 门槛。第三方未获 Owner 批准时返回 NEEDS_OWNER_APPROVAL，不应自动安装。

### 10.2 当前不完整的部分

1. catalog_entries 当前只遍历 manifest 的 Skill 类型；catalog install 只安装 Skill，不是 MCP、Plugin、Hook、Subagent 的通用安装器。
2. publish 是 Plugin 制品发布，不等于把某个 Ability 加入全局可发现目录。
3. fleet-sync 对不在 Desired Profile 的 EXTRA 默认只标记，不自动卸载，这是正确的安全默认，但没有配套的显式卸载和退役命令。
4. control_plane.py 当前有 publish/install/update/readback/rollout/fleet-sync/catalog 命令，但 ability-management 的 SKILL.md 单入口命令列表仍是旧集合，没有完整教 Agent 使用这些新命令。
5. 目前缺少一个能同时展示 typed artifact、catalog visibility、device installation、runtime load、readiness、grant 和 retirement 状态的统一生命周期视图。

### 10.3 必须区分的生命周期状态

| 维度 | 状态示例 | 语义 |
|---|---|---|
| Registry | CANDIDATE / ADOPTED / RETIRING / RETIRED | 能力身份和治理生命周期 |
| Catalog | RESIDENT / DISCOVERABLE / HIDDEN / APPROVAL_REQUIRED | Agent 是否可以发现和申请 |
| Release | DRAFT / PUBLISHED / REVOKED | 不可变发布制品及其来源 |
| Distribution | DESIRED / PINNED / DISTRIBUTED | 哪个设备 Profile 期望哪个 Revision |
| Installation | NOT_INSTALLED / INSTALLED / REMOVED | 设备实际安装记录 |
| Runtime | NOT_LOADED / LOADED / READY / STALE | Agent 是否实际发现和加载 |
| Operation | SUPPORTED / UNSUPPORTED / UNKNOWN | Provider 是否声明支持具体 Operation |
| Authorization | ALLOW / DENY / HUMAN_REQUIRED / UNKNOWN | 当前 principal / target / work envelope 是否获准 |

这些状态不得压成一个 AVAILABLE 布尔值。

### 10.4 上架流程

发现来源 → 确认 stable identity / license / provenance → 选择 USE_NATIVE / ADOPT / WRAP / COMPOSE / FORK / BUILD → 创建 typed Ability Card 和 Revision → 安全检查与兼容测试 → 选择 Catalog tier → 发布 immutable Revision → 设备 Profile pin 或按需安装 → Runtime readback → 真实 Operation 验收。

### 10.5 下架和退役流程

标记 DEPRECATED / RETIRING → 禁止新任务选择或新安装 → 查询所有设备、Session、Plugin、MCP 和 active Work 消费者 → 选择替代能力并迁移 → 等待活动消费者结束或安全交接 → 对目标设备执行显式 uninstall → readback 确认移除 → 写入 Installation Receipt → 标记 RETIRED 并保留历史 Revision / alias / 审计记录。

撤销权限、从 Catalog 隐藏、卸载 Runtime、删除 Registry 记录是不同操作。不得因为下架就立即删除历史 Revision，也不得在未知消费者仍运行时强行卸载。

## 11. 三设备协调与跨设备 Ability Resolution

### 11.1 已有内部组件与暴露缺口

仓库内部已经有部分能力：

- fleet/interfaces.py 有 ability_list / ability_status，可读取设备的 observed Ability Snapshot。
- 同一模块的 fleet_capability_find 解析的是 transport capability level（例如通过什么 transport 能到达），不是产品 Ability / Skill 能力。
- ability_list 没有最新 .observed.yaml 时会返回 OBSERVATION_UNAVAILABLE，而不是伪造空列表。
- serveros-node-mcp 暴露本机 ability.list/tree/inspect/search/resolve。
- serveros-agent-mcp 暴露 device、Agent Provider 和 Session Control。
- off-host Fleet MCP 当前定义 26 个工具，主要覆盖设备、Session、派发、批次、状态、Trace、Inbox、Model 和 Monitor；没有把 fleet/interfaces.py 的 semantic ability_list / ability_status 或跨设备 Ability Resolve 暴露给 GPT。
- fleet_device_versions 只提供最近记录的 Ability Family Profile / Revision，不等于每个设备当前有效 Ability 清单。

因此现在不是完全没有能力发现代码，而是内部 Observation、设备本地 Resolver 和指挥侧 MCP 之间没有形成一条可验证的跨设备能力查询链路。

### 11.2 应补充的全局接口

建议在现有 Fleet Control Plane 增加语义级的只读接口，而不是新建第二套 Registry：

- capability.search：根据用户意图和 Operation 找到候选 Ability。
- capability.resolve：按 device + principal + runtime + target + operation 返回每台设备的真实有效状态。
- capability.explain：解释为何可用、为何不可用、缺失哪个 Provider / Binding / Revision / Grant。
- capability.matrix：返回精简的三设备能力摘要，支持 freshness、coverage 和 observation source。

这些接口应复用 ability-management Registry、现有 resolver、fleet/interfaces.py Observation 和现有 Agent Control / transport，不复制一份 Ability 真相。

跨设备查询必须读取目标设备的实际观察或让目标节点自身回答。不能把 Tokyo 的本地能力投影当成 A6000 或 Windows 的事实。

### 11.3 当前三设备 Profile / Runtime 不一致

Ability Manifest 的 Desired Profile 当前包含：

- Tokyo：Claude Code 和 Codex 都声明了 linux-work、research、design、production-observation、testing、session-manager。
- A6000：Claude Code 和 Codex 都声明了 linux-work、production-observation、production-change、testing、session-manager。
- FJPCOS：Claude Code 和 Codex 都声明了 Windows core、work-management、test-management、automated-testing、information-management、session-manager、ability-management。

但 Fleet Device Registry 当前记录：

- Tokyo：只有 Claude Code。
- A6000：只有 Claude Code。
- FJPCOS：Claude Code + Codex。

因此 Tokyo / A6000 Desired Profile 中的 Codex 安装目标与当前设备 Runtime Registry 不一致。必须决定是安装并验证 Codex，还是修正 Desired Profile；不能把“期望”误报成“实际可用”。

此外：

- Ability Manifest 的 Device Profile 目前按角色命名，但 Fleet registry 没有显式的 ability_profile_ref 将 device_ref 绑定到唯一 Desired Ability Profile。
- Ability Manifest 的 authors.devices 仍有 linux-server、dell-work 等历史 ID；Fleet registry 使用 work-fj、tokyo-shared-dev-01、llm-lab-dell。需要显式 alias / migration 对账，不能猜测身份。
- FJPCOS Desired Profile 没有 workspace-maintainer；注释说 Windows 有独立 Workspace specialization，但该实现没有从当前 Profile 分发。必须通过真实 Runtime scan 证明它实际存在。

### 11.4 协调责任

- GPT：高层目标、跨设备分解、目标选择、依赖顺序、最终验收。
- Fleet：设备身份、可达性、路由、Session、dispatch、handoff、状态和 Receipt。
- Work Engine：Item / Activity / Run / Outcome / Lease / Checkpoint 的正式状态和依赖。
- Device Agent：本地 Ability Discovery、Skill/MCP 选择、执行和证据生成。
- Memory Handoff：异步工作交接和跨设备上下文定位，不是第二个 Work Engine。

fleet-coordination Skill 明确不是 Scheduler，不应由它接管步骤排序、重试和长期工作流。单设备内部并行使用原生 Subagent / Quay；跨设备步骤由 GPT 规划，并写入现有 Work Engine / Handoff。

### 11.5 当前可靠性阻塞

- Agent Control Router 的 NodeControlTransport 在当前合同中仍标记 deployed=false；合同明确记录 work-fj Windows 没有可用的 ServerOS Node Runtime / Connector。Fleet MCP 的 CloudCLI Session 路由是另一条路径，不能用它证明 Windows Node MCP / Ability Resolver 已就绪。
- Agent Control 的 durable-effect reconciliation 仍是 partial。失去 Mutating Dispatch 的响应后，若无法从实际 Effect Owner 取得 APPLIED / NOT_APPLIED 观察，结果必须保持 EFFECT_UNKNOWN，不能盲目重发。
- Work Handoff 与 Session / Writer Handoff 不同。前者传任务和 checkpoint locator；后者必须 checkpoint、验证目标、fence 旧 writer、转移 lease，再允许新端写入。

## 12. A9 Production Observation：当前实际缺口

A9 的 Skill 正文已经明确四类入口和只读纪律，但它目前仍是调查流程说明，不是已绑定生产环境的 Provider。

当前 manifest：

- production-observation：1 个 Revision，version=null，compatibility=[]，installations=[]。
- production-change：1 个 Revision，version=null，compatibility=[]，installations=[]。
- Tokyo 和 A6000 Desired Profile 引用了 A9；A6000 还引用 A10。
- Windows Terminal Capability Profile 预期它能承担生产状态 / 日志观察，但 work-fj-windows Desired Profile 未包含 A9。
- 仓库中尚未找到能把 LiteLLM Production target、只读 Provider、设备 Binding、Runtime Compatibility、Readiness 和 Grant 连起来的完整配置与验收证据。

必须补齐：

1. Target Registry：生产环境 stable identity、environment、service、版本和数据范围。
2. Read-only Provider：管理/用量 API、请求查询、日志查询和必要的只读跳板。每个 Provider 声明具体 Operation，不提供任意 Shell。
3. Device Binding：哪个 device / principal / runtime 持有合法网络、credential reference 和 target binding；Secret 只留在目标设备的受控凭据系统。
4. Readiness：实际 endpoint、版本、认证和最小查询探测；不以 SSH 可达等同生产主机可达。
5. Authorization：按 Operation、Target、principal、查询预算和时间范围限制。
6. Evidence Receipt：每个事实记录 source、version、time、query scope、redaction 和 evidence locator。
7. E2E：服务状态、精确用户、Request ID、时间 + 错误、指定 Feishu message 五类调查真实通过。

建议先只实现 A9 只读查询，不把 A10 生产变更混进来。哪个设备拥有实际生产 Binding，就优先选择哪个设备；不要因为 Tokyo 是 development-primary 或 A6000 是 GPU worker 就推断它们能访问生产。

本轮只读检查了 GitHub 仓库，没有连接生产服务器、读取生产 Secret 或执行生产查询。设备侧真实 Binding 需要另行授权检查。

## 13. 2026-11-13 仓库审计发现登记

| ID | 当前发现 | 判断 / 后续动作 |
|---|---|---|
| G01 | Fleet MCP 有 26 个工具，但没有对外的 semantic Ability Matrix / cross-device resolve | 补接口，复用内部 Observation 和 Resolver |
| G02 | Node MCP 的 ability.search 是 substring，缺 avoid_hints；ability.resolve 使用 caller claims | 增加触发/排除词和设备侧可信事实解析 |
| G03 | control_plane.py 有发布、安装、更新、readback、rollout、catalog install，但没有完整 uninstall / unpublish / retire | 建立受控退役与消费方迁移流程 |
| G04 | Ability Management SKILL.md 的命令清单落后于 control_plane.py | 更新 Skill 并加入命令合同测试 |
| G05 | catalog install 当前只处理 Skill 类型 | 建立 typed lifecycle / action registry，不把不同类型安装方式混为一谈 |
| G06 | Ability Device Profiles 未显式绑定 Fleet device_ref | 在现有 Device Registry 建立唯一 Profile Reference |
| G07 | Tokyo / A6000 Desired Profile 声明 Codex，但 Fleet Registry 只记录 Claude | 对齐真实 Runtime，不允许假想安装 |
| G08 | authors.devices 仍保留旧设备 ID，与当前 Fleet device IDs 不一致 | 做 alias / identity migration 和 referential integrity 检查 |
| G09 | workspace-maintainer 有 20 个历史 Revision；当前 Installation 记录仍以旧 linux-server 为主，版本字段大量为空；canonical identity 为 workspace-maintainer，显示名为 workspace-management | 清理版本与 alias 语义，重新扫描三设备 |
| G10 | feishu-space-management 有 3 个 Revision，Tokyo 安装记录包括 Claude 与 Codex，但 compatibility 为空，Tokyo 当前 Fleet Runtime 只记录 Claude | Fresh scan + compatibility self-check |
| G11 | production-observation / production-change 没有 Compatibility 和 Installation 证据 | A9/A10 优先补齐真实 Binding / Provider |
| G12 | Feishu 三端同步架构的最后 live baseline 为 2026-09-24 | 重新盘点 Space、ACL、Drive、Wiki 和 Records consumer |
| G13 | CAPABILITY-FAMILIES 当前视图标注 manifest 65 个 Ability 中 20 个已归类、45 个未归类 | 这是导航映射覆盖缺口，不代表 45 个实现缺失；逐项映射或标记 legacy / provider / specialist |
| G14 | resource-observation 被列为 declared absent，但产品树已把它定义为 A4 ServerOS Core 子能力 | 修正视图，不另造重复 Skill |
| G15 | content-archiving 没有独立 Ability，当前由 information-management 承担 B1 子职责 | 评估是否需要独立 Specialist，不为填空强行造新 Ability |
| G16 | A6000 多个 Workspace Path Role 为 UNBOUND，FJPCOS 多个 Path Role 仍是 user_reported | 先设备只读扫描，再做物理目录治理 |
| G17 | Agent Control 的 Windows NodeControlTransport 未部署，Mutating Effect Reconciliation 仍 partial | 完成 Windows Node 接入和可靠交接闭环；保留未知效果拒绝重试 |

以上是仓库静态审计结果，不代表已经完成三台设备的实时扫描。Manifest 的 Installation、Compatibility 和 Desired Profile 只能按各自语义解读；必须通过设备 snapshot / Runtime readback 更新后，才能宣称 E2E 可用。

## 14. 相关权威文档

本文件不替代以下细分合同；发生冲突时，应先确认对应领域的 canonical authority，而不是在本文件复制第二套规则。

### ServerOS

- [Capability Discovery — Native-first](capability-discovery.md)
- [Ability Fabric](ability-fabric.md)
- [Ability Operating Model](ABILITY-OPERATING-MODEL.md)
- [Monitoring Product Architecture](MONITORING-PRODUCT-ARCHITECTURE.md)
- [Monitoring Acceptance](MONITORING-ACCEPTANCE.md)
- [Runtime Resources](runtime-resources.md)
- [Directory Convergence](architecture/DIRECTORY-CONVERGENCE.md)
- [Memory Fabric](memory-fabric.md)
- [Fleet V1 delivery campaign](plans/plan-02-complete-fleet-v1-delivery-campaign.md)
- [Device Ability Projection](../integrations/device-ability-projection/README.md)
- [Fleet Hub Watcher](../integrations/fleet-hub-watch/README.md)
- [Fleet Hub Card Spec](../integrations/fleet-hub-watch/card-spec.yaml)

### Ability Management

- [Product Ability Tree](https://github.com/ProfessorZhi/ability-management/blob/main/design/PRODUCT-ABILITY-TREE.md)
- [Progressive Disclosure Contract](https://github.com/ProfessorZhi/ability-management/blob/main/design/PROGRESSIVE-DISCLOSURE-CONTRACT.md)
- [Ability Card & Registry Contract](https://github.com/ProfessorZhi/ability-management/blob/main/design/ABILITY-CARD-REGISTRY-CONTRACT.md)
- [Ability Realization Plan](https://github.com/ProfessorZhi/ability-management/blob/main/design/ABILITY-REALIZATION-PLAN.md)
- [Production Observation](https://github.com/ProfessorZhi/ability-management/blob/main/design/PRODUCTION-OBSERVATION-ABILITY.md)
- [Workspace / Git Ability](https://github.com/ProfessorZhi/ability-management/blob/main/design/WORKSPACE-GIT-ABILITY.md)

---

**本文件的完成只表示产品需求基线已写清楚；不表示上述缺口已经修复、部署或验收。**
