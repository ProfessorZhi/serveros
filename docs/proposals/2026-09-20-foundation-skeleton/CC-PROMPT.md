# 给东京 Claude Code：一次搭好 ServerOS 骨架

你是本轮实施协调者。目标不是再研究一轮，也不是立刻做完完整自动秘书系统；目标是把现有 ServerOS/ability-management/work-engine 组合成可构建、可验证、可展示、后续能逐环节打磨的骨架。

## 阅读入口与决策

先读同目录 README.md、PHILOSOPHY.md、ARCHITECTURE-DESIGN.md、IMPLEMENTATION-PLAN.md，以及仓库根 ARCHITECTURE.md。卡片负责人只补 OBSERVABILITY-CARDS.md；Quay负责人只补 QUAY-DELEGATION.md。SOURCES.md 固定研究基线；实现前检查当前源码/安装版本，不能把历史说明当现场事实。

这些产品决策已确定，无需再逐节请求批准：

- 唯一常驻核心 serverosd，Core-2 是 serveros 与 ability-management，四平面不变。
- 环境是动态的；角色/版本/归属/生命周期/期望/观测/准入稳定，具体进程/端口/Provider 不写死。
- 两大家族：环境与能力基础设施；信息到交付工作能力。先复用稳定 Ability ID，不按导航树搬目录。
- 默认上下文薄，按需加载 Skill 与证据。不要把本设计包复制进 CLAUDE.md，不重造 CC ToolSearch，不默认启动所有 MCP。
- 工作顺序：授权归档 → 结构化认识 → 跨项目看板/候选 → 用户选择 → 单项需求文档 → 设计方案 → 实施/测试 → 报告/反馈。沟通等工作按需跳过代码步骤。
- 个人看板沿用工作文档形式：P0/P1、今日1–2项、等待/待合并、取消/替代；成功完成标题划线并置底。
- Quay能力家族的中心是 CC 能把获批需求切成 Quay 的 PHASE/AC/Task，真正驱动一轮、验证结果并用于汇报，而不只是建任务卡。

## 本轮直接实施的工作包

A. ability-management：盘点并复用 runtime/resource、ability、workspace/session/test、information/work、Feishu space/document/report 等能力；做两大家族生成视图、必要契约和 cc-quay-delegation 主 Skill。更新真实 Revision/指纹/compatibility/packs；未验证不要登记 installed/AVAILABLE。

B. serveros：保持核心边界与 canonical composition。复用或整合当前资源观测 Slice 的提交/接口，提供可靠 snapshot 与注册候选接口；未知不误报死亡，注册不自动授权恢复。别抢另一个会话的 resource Slice dirty 文件。

C. 卡片：交付固定三板块状态卡（我的资源、整机资源、共享网关），顶部简短环境健康；独立的人话告警与恢复卡。完成确定性 semantic model、同源 JSON/可视预览和六类场景。共享网关和个人 container0 分开；UNKNOWN/STALE/无样本不绿；不改生产告警游标。

D. 工作链：提供关注清单、飞书三空间与磁盘目录覆盖样例、认识变化、看板、选中后 brief/design/plan、执行/测试报告与日报。至少两个项目样例和取消/待合并反例。数据经稳定引用串起来，不靠人工复制粘贴。

E. Quay：先读实际安装版本和 Goal/Task/执行接口，再在本 Item 独占 workspace 驱动一轮与当前骨架相关的验证。不要切换他人 active PHASE、不要从全 backlog 自动选活。旧 LiteLLM 只可测试，不允许 product修改/合并/部署。若真实入口/权限缺失，保留可执行适配代码与规范包，具名报告 live 阻塞，继续完成其他包，不伪造 Quay成功。

F. 发布：交付一个 START-HERE 产物首页，列出能力地图、卡片预览、空间/来源图、认识/看板、brief/design、Quay结果、日报与ChatGPT决策包。源码和经过审查的产物摘要推对应获准 GitHub分支；提交后回读。清楚标注代码、离线、安装和线上状态。

## 权威与并发

serveros 管 architecture/bootstrap/composition/runtime；reusable Skill 源码属于 ability-management；work-engine 管 Item/Activity/Run；hwz-ops 管实例策略；records 管人类记录；Quay 管自己的 Goal/Task；卡片和看板不是第二数据库。

按既有 Work Engine 合法分配本轮 Item/Run，不猜编号。每 repo 从当前合适基线建自己 clean worktree，分工明确 paths/owner，串行整合。不得 stash/reset/clean/checkout覆盖他人WIP、force-push、git add .、碰并行会话未提交文件。

对账只限相关目录和接口；不要回北京、不要重新迁移、不要全盘诊断。只读 Python 禁止生成字节码，同时注意导入/工具本身仍可能写状态；不要为清缓存删除共享目录。

## 执行方式

普通实现决策你自行完成，不停在“等你逐节确认”。必要时少量并行分工，避免每个节点再次派生一套总协调器。优先薄适配，不造新 daemon、新调度平台、新状态库或新时序数据库。

骨架必须有一个能回放的端到端样例，有输入/输出/拒绝条件/测试，不交一堆空Skill。模板中的接口名与 enum 若和仓内不同，按实际合同映射；新接口版本化，不改断言制造PASS。

测试先定向、再按既有发行要求一次必要集成验证；不做业务全量E2E、生产压测、付费模型健康探针或破坏性故障演示。严格检查实际执行计数与错误传播，退出0不自动等于通过。

## 线上边界

开发、隔离安装和预览直接完成。不要自启动新网络/隧道、改防火墙/路由/Docker daemon、重启共享网关/RDC、修改他人服务或轮换秘密。

当前运行的ServerOS、插件、Hook、卡片timer与真实飞书投递需切换时，在代码/预览验证后集中提交一次最小变更清单和回退方案。未获批就保持staged，不把“待上线”伪装成失败或已上线。定时任务本轮生成可手动验证且默认disabled的模板，不能擅自承诺每日外发。

## 最终只给一份交付

1. GitHub入口、分支/提交/PR、各repo revision与artifact pin。
2. 本轮各工作包：实现了什么、如何运行、已验到哪一层。
3. 一页产物导航，用户可逐项看目录、云空间、认识、看板、需求/设计、卡片、Quay和日报。
4. 未实现/未授权/不兼容事项逐条写清，不以目录存在冒充可用。
5. 一个集中部署批准请求（确有需要时），不逐文件询问。

完成本轮固定骨架后停止。后续由用户按产物修改偏好，再逐步打磨归档、认识、看板、设计和报告。不要因为发现新技术债继续扩大这轮。
