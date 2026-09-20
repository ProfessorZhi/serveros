# ServerOS — 广义产品架构基线 v1

> Design ID: `serveros-system-v1` · 2026-09-20
>
> 本文件是本分支的唯一总体架构入口。它定义要实现的产品，不宣称这些能力已经安装、验证或上线。当前实现状态只由代码、固定 revision 的验证及现场证据证明。
>
> 本版依据用户授权收敛整个 ServerOS 体系，而非仅 daemon 或插件。它取代此前 proposal 中互相重叠的目标描述；旧研究、旧实现事实、旧失败记录保留在 Git 历史及原文档中，不应被抹掉。

## 1. 产品是什么

**ServerOS = user-space Agent Operating Fabric。**

它让 Agent 在一个真实、动态、多人并行使用的环境里，快速回答：当前是什么工作；已有何种认识；需要哪些能力；它们通过什么执行；此刻能否执行、是否获准；结果应落在哪里；下一步可否自动推进。

所有有目标的工作都适用：文档维护、技术支持、调研、需求讨论、方案/测试方案、开发、构建、部署、运维、沟通与汇报。不以“写代码”作为进入工作系统的条件，也不为每件杂事单独造引擎。

**产品完整性**意味着每个本版要求都有真实入口、执行路径、失败语义、可审阅产物及相应验证；不意味着安装所有外部工具、拥有所有企业资料、自动控制全部设备。

## 2. 最终产品形式

| 产品面 | 职责 | 不能承担的职责 |
|---|---|---|
| ServerOS Core | `serverosd`、统一 `serverosctl`、本地事实/准入/有限维护 | LLM planner、企业内容处理、Web UI |
| ServerOS Agent Kit | Claude Code 插件、Codex 插件/适配器；薄 bootstrap、生命周期接口、按需能力入口 | 第二套业务状态、全量 Skills/MCP 注入 |
| Ability Fabric | 身份/版本/兼容/安装、能力搜索、操作解析与分发 | 项目状态机、设备事实的替代品 |
| Work & Knowledge Fabric | 信息认识、候选/Item、执行/交付/报告的协作层 | 内核调度器、另起通用工作流平台 |
| Human Control | 飞书播报/告警/轻量控制、审批与决策 | 因“消息发到了”而授予权限 |
| Optional Console | CloudCLI 类 Web/mobile 控制台、CLI/TUI、原生终端入口 | 不可替换的 ServerOS 主体 |

Agent 默认安装**一个 ServerOS 入口插件**，不是每个 Ability 一个插件。各 runtime 使用其真实支持的 manifest、Skills、MCP、hooks 机制；不假设两个 runtime 文件格式或热加载语义相同。

CloudCLI 不存在或离线时，CLI、Agent 和定时工作仍可运行。飞书离线不终止已获准的本地工作。tmux 是终端承载，不能成为 Item 或 Agent Session 主键。

## 3. 设计哲学

1. **严格事实，快速默认路径**：不知道就具名返回；不因不知道一项而全盘扫描。
2. **一个常驻核心，多个按需能力**：唯一 resident core 是 `serverosd`；外部 Provider、Web 服务、系统调度器可有进程，但不是新增语义 daemon。
3. **安装丰富，活跃上下文少**：目录可大，当前上下文只拿与当前动作有关的少量内容。
4. **职责单一而不是进程繁殖**：resolver/schema/operation 不自动对应新服务、新 repo、新 Skill。
5. **声明、事实、授权不同**：Profile 是 should-be，内核/Provider 是 observed；两者都不是授权。
6. **一类事实一个 authority**：允许不同领域各有权威；禁止把所有真相集中复制成第二数据库。
7. **操作级可用性**：`support != readiness != authorization`，必须对实际 operation 判定。
8. **人参与有价值的判断**：目标、需求、技术选型、架构取舍集中讨论；批准的执行边界内不逐命令请示。
9. **验证按风险和影响面**：最少充分验证；不是跳过关键验收，也不是每步全套测试。
10. **并行工作优先于目录整洁**：ACTIVE/未知归属/租用中的工作不得为了变绿而清理。
11. **先候选看板，后需求展开**：一条消息不等于一个 Item，一个 Item 不等于五篇文档。
12. **授权范围内自动推进**：无输入变化不重复重算；无进展不无限循环；无权限不换身份绕过。
13. **隐私最小化**：能用元数据/引用就不复制正文，能不用录音就不抓音频；无关内容不进入记忆。
14. **表面可替换，语义不分叉**：飞书、Web、CLI、Agent 使用同样的对象与准入，不能四套逻辑。
15. **范围闭合而非虚假全绿**：本版全部要求须落地；外部权限阻塞如实列出，不能把 fixture 当 live。

## 4. 四个问题平面、两大能力家族

保持四个问题平面：Node Runtime、Ability、Memory-Context、Coordination。Work Fabric 是协作与工作状态的产品组合，**不是第五个常驻核心**。

两大 Ability 家族是面向使用者的导航：

- **A 工作基础设施**：环境、能力、空间、会话、记忆、测试环境、文档生命周期、构建发布、人类控制与合规。
- **B 信息到交付**：输入/认识、候选/工作、需求、研究/设计、实施、质量、支持/沟通、委派与汇报。

家族分类与磁盘目录、repo、进程、工作状态均正交。物理 `skills/<stable-id>/payload/` 继续扁平；跨家族复用引用同一能力，不复制。

详见 [能力家族与边界](docs/architecture/ABILITY-FAMILIES.md)。

## 5. serverosd 基本核：封闭职责

```text
serverosd
├── Host Inventory（设备身份引用、用户/整机/文件系统事实）
├── Managed Runtime Registry（owner、desired、lifecycle、依赖、保护）
├── Probe Engine（有预算的本地检查与 Provider 观测接收）
├── Runtime Attribution / Graph（进程、容器、环境引用）
├── Anomaly / Hygiene（事实、稳定 alert identity、状态与待投递 intent）
├── Policy / Admission（带 actor/scope 的操作准入）
├── Reconciler（仅明确可管理对象、有界重试）
├── Observation / Provenance（时间、来源、覆盖、新鲜度）
└── Local IPC（查询、操作请求、受限事件读取）
```

### IN

主机和用户资源；真实挂载/配额/inode/shm；可观察进程、容器与受管 runtime；liveness/transport/functional/readiness 四轴；设备级 desired runtime；Attachment 引用/lease 的运行语义；局部异常与事件；权限、预算、保护状态；有限自愈；自健康。

记录内核 UID 所有权与业务 Item/Session ownership 的区别。进程名称、cwd、tmux pane、容器名称可以是线索，但不能凭名称授予业务 ownership 或回收权。

本地事实采集保持低成本、有采样窗口和覆盖分母。CPU utilization 不用 load 代替；RSS 明示估算；磁盘与配额分开；同挂载多 role 不重复累计。失败/无样本不是零或绿色。

### OUT

LLM 推理/规划、源内容抓取、飞书/GitHub SDK、报告正文生成、完整 Skill 目录、向量索引、Work Item 状态机、Quay 调度算法、UI、任意远程 shell、全机进程托管、Docker 编排平台、秘密数据库、通用 scheduler。

外部业务与网络 Provider 在 daemon 闭包外运行，通过定义明确的观测与操作边界连接。不能因为新增飞书/浏览器就把其 SDK import 进常驻核心。

### 进程与存储约束

systemd 用户 timer（或目标 OS 原生 scheduler）调用 oneshot recipe/worker。daemon 不运行模型。已有 store 能表达的状态复用已有 store；默认不引入 Kafka、Redis、工作流集群或图数据库。

运行事实缓存可以重建；**授权记录、工作状态、人工决策、消费游标、投递 receipt、引用所有权不都是可丢缓存**，必须按域持久化和备份。`state/` 不能一律清空。

常驻核心唯一不表示必须拒绝长期运行的 Provider；Provider 的生命周期和权限须单独声明。

## 6. Authority 和仓库职责

| 领域 | 唯一写权威/所有者 | 派生面 |
|---|---|---|
| 总体架构、bootstrap、composition、通用语义适配 | `ProfessorZhi/serveros` | 插件发行包、CLI/MCP/UI adapter |
| Ability registry/revision/compatibility/installation、可复用 payload | `ProfessorZhi/ability-management` | 索引、能力地图、安装投影 |
| Item/Activity/Run 生命周期、工作租约、工作事件 | `ProfessorZhi/work-engine` | Board、Today、日报视图 |
| 本机政策、roots、保护对象、来源/接收人绑定 | `ProfessorZhi/hwz-ops` 的实例策略及受保护 local-only 配置 | 环境/治理地图 |
| 本机当前资源事实 | `serverosd` 的观察 | 状态卡、readiness |
| 原生 Agent session/transcript | 对应 Agent Provider | ServerOS 规范化会话视图 |
| 文档内容 | 每份文档声明的唯一 `authoring_surface` | 其他面上的预览/发布副本 |
| 原始云内容 | 原 Provider；授权快照有独立 revision | 结构化认识、相关索引 |
| 决策/交付证据 | records/证据库的固定版本记录 | 检索索引、报告 |

Core-2 仍指 `serveros + ability-management` 的产品与能力治理关系，不意味着两个 daemon。`workspace-management` 是 specialist。三个或更多仓库协作不创建第二 Item 状态库。

## 7. 对象模型

**Ability 侧**：Ability/Operation、Revision、Provider、Binding、Attachment。Profile/Adapter 是配置或实现描述，不再各造一套生命周期引擎。

外部产品语言的 Ability 是 WHAT；现有 registry 的 artifact identity 仍保留 `(type, canonical identity)`。Skill/MCP/plugin 同源不能无证据合并；语义能力经显式 artifact/provider 关系解析，名字或路径不作为同一身份的证明。

```text
Ability#Operation -> Provider -> Binding(device, instance)
                                  ^
                    Attachment(Agent/Session/Item, scope, lease)
```

对 operation 分别输出：support、readiness、authorization、reason、observed_at/source。未知、明确不支持、当前不可用、拒绝、未配置、无数据和不适用不可混淆。具体 wire enum 保留版本兼容，升级须明确映射；禁止靠换术语掩盖没有实现。

**Work 侧**：Source/有版本的 Snapshot、来源可追溯的 Claim、Candidate、Item、Activity、Run、Deliverable。Understanding 是可修正的 Claim 集合；Board 是投影；Decision/Checkpoint/Report 是带类型的工作记录或文档，不为每个名词建立服务。Execution Envelope 是 Item/Activity 的授权范围记录，不是新引擎。

## 8. 发现、上下文与权限

默认路径：thin bootstrap -> 当前 context -> 小集合 search -> inspect/resolve -> 按需加载 -> 执行。已知能力可以直达；不强迫每步额外 API 往返。

小型本地 lexical/tag 索引优先；需要时才引入 embedding。索引是缓存，保留 source revision。Skill 正文、MCP schema、Provider 启动、全文证据分别按需。并非所有 Agent 都支持相同的动态 attach；采用真实 runtime 能力，无法热加载时具名提示或复用受控工具边界，不能假装成功。

读取同样受 scope/ACL/脱敏限制；“只读”不等于任何资料都可读。重权限或有费用的 probe 也需预算/授权。共享终端/CloudCLI 文件写入若绕过 ServerOS，不能宣称已受 ServerOS 完全准入；需 native permissions、sandbox/OS 隔离或明确 external/unmanaged 标记。

详见 [Agent 与人类使用面](docs/architecture/AGENT-EXPERIENCE.md)。

## 9. 动态环境与自动推进

Desired = 已批准的 Session/Item/Project/Device 需求引用；Observed = 当前观察；Effective = desired、observed、兼容与政策共同解析的结果，不是把列表拼在一起。

新发现 MCP/runtime 仅为 candidate。Session/Item/Project/Device-persistent/External 的范围由明确选择或预授权 recipe 决定。最后一个消费者退出也只有在 owner、lease、grace 和 retain policy 均允许时才停资源；观察失联不等于引用归零。持久资源不能仅归短命 session 所有。

自动工作是三种外部有限 recipe：输入与认识更新、获准工作推进、日报/周报生成。优先增量/事件，定时补扫；无变化跳过；有预算/单次锁/幂等/检查点。Human 与 ChatGPT 参与优先级、需求、技术选型与设计；Claude/Codex 执行；Quay 承接有界子任务，Task done 不关闭父 Item。

详见 [工作与信息链](docs/architecture/INFORMATION-WORK.md)。

## 10. 空间与内容治理

目录按用途和生命周期管理，不要求所有目录或仓库 clean 才能开发。保留 Tokyo 的长期 roles；新增本轮目录只登记真实 owner/purpose/lifecycle，不迁移并行业务工作面。

飞书“云文档”是产品总称；用户需要的云文档、云盘、我的文档三个入口均纳入 inventory，但不假造三个互不重叠数据库。Wiki 节点树、Drive 文件夹、My Docs 页面树、Minutes 原生对象按 Provider 事实建模，同一云对象按 ID 去重。

会议录音、转写、自动摘要、人工摘要和引用是一个逻辑 MeetingBundle。无关环境录音不得自动进入认识/候选/日报。闭麦只是线索，不等于没有支持工作；Marek 开麦例外须精确绑定。过滤内容与删除原件是两条独立决策。

详见 [磁盘、飞书与内容保留](docs/architecture/SPACE-AND-RETENTION.md)。

## 11. 产品输出

Status：现在怎么样，固定“我的资源 / 整机资源 / 共享 AI 网关”，顶部给当前工作环境 readiness。

Alert：发生什么、对象、影响、确定程度、建议；无关总账不占第一屏。Recovery：只针对该接收面之前成功通知的问题，有肯定恢复证据才可说恢复。Work/Decision：今天重点、阻塞、需要选择。Report：真实进展差量、交付/证据、决策、阻塞、Quay 贡献；没有进展要如实说，不编造完成百分比。

一种语义 presentation model，多种 renderer/transport。原始告警 code、baseline、generation 和全历史计数放诊断详情。UI 不可显示没有后端的假按钮。

## 12. 本版范围与完成标准

本版覆盖两大家族中全部必需职责及其真实调用链，不以“新增 Skill 文件”“map 中 PRESENT”“CLI exit 0”代替可用。

目标设备为 Tokyo；Claude/Codex 的支持与验证分别记录；其他设备给合法 profile/拒绝语义，不谎称部署。CloudCLI 是可选消费面，相关 adapter/隔离说明须完成，不要求开发第二 IDE。

遵循 [完整验收矩阵](docs/architecture/ACCEPTANCE.md)。必须逐行报告 CODE、INTEGRATION、LIVE，真实外部阻塞不偷改为 PASS；也不能因一项阻塞放弃其余能力。

[CC 执行任务书](docs/execution/CC-PROMPT.md)规定实施授权、验证预算、并行保护和交付方式，不另定义一套架构。

## 13. 仍然禁止

新增语义 daemon、全量启动上下文、每能力一个插件、随意任意 shell 控制面、全局 workspace-clean 前置、自动 destructive cleanup、为填图造 fake abilities、并行项目重构、无证据的可用性/完成度、无限验证/重试、把原始录音当用户指令、向非授权渠道发布私有资料。

## 14. 文档地位

本版明确修正旧措辞中“地图全是生成物”“state 全可重建”“只读不需要权限”“所有 Provider 都可由统一热加载控制”“UI 只是无副作用显示器”等过宽简化。L1/L2 语义地图可人工维护；L3 是 observed/projection；各域保留自身 authority。

历史入口 `docs/fabric-v1-roadmap.md` 与旧 proposal 用于理解既有实现/失败，不扩大也不缩小本版验收范围。规范冲突以本文件及其专题章节为目标基线，实际代码/现场状态照实记录；禁止通过修改证据来迎合设计。
