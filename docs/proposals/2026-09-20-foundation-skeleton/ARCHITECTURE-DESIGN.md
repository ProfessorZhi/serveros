# 架构实施设计：环境底座与工作能力骨架

状态：本轮实施提案。唯一 canonical 架构为根 ARCHITECTURE.md；下述两大家族是导航/组合视图，不是替代现有四平面的第五套架构。引用见 SOURCES.md。

## 1. 仓库职责与交付位置

| 组件 | 本轮负责 | 禁止变成 |
|---|---|---|
| serveros | bootstrap、已有本地运行核/解析器的必要增量、plugin/bundle 与 profiles、发行构建、产品设计与验收 | 另一份 reusable Skill 源码仓或新的业务数据库 |
| ability-management | reusable Skill、模板与确定性辅助代码；能力身份、分类、Revision、Compatibility、Installation | 第二份 ServerOS composition、常驻 abilityd |
| work-engine | 复用 Item/Activity/Run/状态/投影/发布机制，只有被样例链证明必要才小改 | Quay 原生任务库、另一个 Git 或飞书 |
| hwz-ops | 东京实例 Profile、关注范围、端点引用、收件人/时间/预算策略、地图与部署配置 | 手写复制所有 Skill 的执行逻辑 |
| records | 人类工作页、获准发布的工作台与日报、决策记录 | 原始秘密库、全部企业资料镜像 |
| Quay | 本次被委派的 Goal/AC、Task、执行与门禁事实 | 个人跨项目工作主线或 ServerOS supervisor |

Reusable Skill 使用 ability-management 现有平铺目录与 payload 结构；脚本属于哪个能力就跟着该能力。serveros 的 deterministic builder 从明确版本取它们，不复制一份可独立编辑的源码。[S1][S3][S4]

## 2. Core 保持小

沿用闭合职责：Inventory、Managed Runtime Registry、Probe、Attribution/Graph、Anomaly/Hygiene、Policy/Admission、Reconciler、Observation/Provenance、Local IPC。

禁止新增业务常驻内核进程。Feishu/GitHub/Quay/LLM 调用、复杂报告、完整内容摄入放到按需 Ability/Provider 工作进程。底层不能因外部飞书失败而停止本地观测。即使整个产品用于工作自动化，工作 recipe 仍可选择、跳过和迭代。[S1]

## 3. 两大家族与最小能力契约

以下名称用于职责定位。已有身份优先；不是要求新建同名仓库或为每个指标创建 Skill。实现者先生成现有→复用/增量/新增对照表。

| 家族/簇 | 复用或拟新增能力 | 最小输入 → 输出 |
|---|---|---|
| A 运行与资源 | runtime-management / resource-observation | 节点与范围 → 当前健康/资源/归属/证据 |
| A 能力治理 | ability-management | 需求或发现候选 → 可用入口/拒绝理由/登记计划 |
| A 工作空间 | workspace-management | 放置/容量需求 → 根解析/地图/提案 |
| A 会话 | session-manager 与 agent-session-management 各守已有边界 | 历史/实时会话请求 → 可恢复资料或受支持操作 |
| A 工具与测试环境 | test-management；Provider bindings | 固定版本/测试需求 → 环境定义/执行证据 |
| A 人机控制与可观测性 | human-control；状态/告警呈现与投递能力 | 当前快照/事件 → 三板块状态卡或人话告警 |
| B 输入与归档 | information-management；content-archiving 职责 | 获批来源及深度 → 增量快照/覆盖缺口 |
| B 结构化认识 | information-management | 可追溯来源 → 事实/推断/冲突/决定/候选 |
| B 看板与工作 | work-management | 候选、现有 Item、用户选择 → 个人/项目视图与单项 brief |
| B 设计与执行 | 复用调研/设计/开发/测试技能 | 获批 brief → design/plan → 证据与交付 |
| B Quay 委派 | cc-quay-delegation（拟新增一个主 Skill） | 工作切片 → Goal/AC/Task → 有界运行 → 结果/日报段 |
| B 文档与空间 | feishu-space-management / document-stewardship | 云对象元数据/维护责任 → 三空间地图/更新或归档提案 |
| B 汇报与交接 | feishu-report；GitHub handoff 职责 | 已验证工作事实 → 日报/决策包/发布快照 |

每项契约至少有：用途、触发条件、输入/输出、权威位置、支持操作、所需权限/依赖、拒绝条件、预算、来源/版本、验证方式。没有实现的部分是具名缺口，不能用 SKILL.md 存在推成 AVAILABLE。

两大家族投影从既有 manifest/taxonomy/pack 数据生成。物理目录平铺；树/DAG 可重排；不要再建一份要人工保持同步的 catalogue。骨架交付能力地图和机器可读对照结果即可，不新造图数据库。

## 4. 动态环境与登记闭环

V1 复用注册表和已有关系/状态库，先接三类输入：节点基线声明、已批准注册、带明确 parent 的 Session/Run 使用需求。日后来源增多不改身份模型。

```text
批准声明 + 有效工作/会话需求
             ↓
       有效期望资源集合
             ↕
     实际观测 + 归属证据
             ↓
  健康/缺口 → 准入 → 有限恢复或人工处理
```

最小对象：resource_ref、device/owner、provider/binding、lifecycle、required_by、desired、probes/observed、recovery_policy、protected_state_refs。

- 原始新进程只先发现；CC 启动新 MCP 时若有可信 launch record，可生成候选。不要仅凭进程名或开放端口认定 MCP、工具集或用途。
- 问用户：仅本次、项目登记、节点持久登记、外部依赖、忽略。已授权的相同候选去重，不每次启动重复问。
- 登记不自动授予 auto-restart。会话内 stdio MCP 由所属客户端启动/结束；不能把它当共享 HTTP 服务。
- RDC 是 Provider，ChatGPT/CC/其他 Agent 是消费者。复用现有 binding/usage 关系表达使用者；需要新 Attachment 字段时做最小增量，不建立平行身份库。多消费者支持与各自授权必须实测，不共享账号秘密冒充兼容。
- 一个节点看不到 Windows 隧道的本地进程，不可冒认自己能重启它。跨设备执行要走被授权的对应设备执行器。
- 来源失联保留最后已批准的期望并标记新鲜度，不将资源全部退休。节点冻结/人工停止要成为明确 desired 状态，不能被恢复器推翻。

## 5. 资源观测接口与当前 Slice 的协作

用户/整机资源、runtime 状态必须复用 ServerOS 当前事实入口。新 CLI/MCP/卡片不得另写 ps/df/docker 采集器。已有 resource Slice 正在被其他会话修改时，只按其提交/接口整合；不抢改 dirty 树。

本轮若负责该增量，应满足：

- 统一观测元数据，freshness 读取时计算；授权有效期不纳入观测 freshness。
- Profile 声明 role/path，内核验证挂载/设备/fstype/容量，缺失与不具权限分开。
- 一个物理文件系统只测一次，多 role 做关联；/data 不被 Docker/state/artifacts 投影遮没。
- user CPU 用实际 UID 跨 tick 差分；RSS 聚合标估算；首样本未知；使用 monotonic 时钟计间隔。
- quota 读实际 user/project 类型和 enforcement 状态，分别报告 used、soft、hard、grace 与两种百分比；旧 used_percent 语义保持。soft 不是立即硬上限，0/unlimited 不除零。
- shm 是 tmpfs/shared-memory，不当数据盘；内存与 tmpfs 不重复相加为整机占用。
- CPU/load/memory/磁盘 IO 或网络吞吐：已有可用就显示，缺的具名未知，不为了铺满表格先造完整时序平台。
- 采样采用有预算的类别调度，复用当前机制；fast/normal/slow 可配置，参考 5/30/300 秒，不阻塞 tick 等一秒，不同步 RPC 自己。外部 node.status 验 IPC，内部 heartbeat 不能证明自身未来可响应。

共享网关与个人 container0 分开登记/展示。owner 账户、业务 maintainer、允许动作是不同字段；用户负责共享服务不等于本轮获准重启它。真实模型服务成功来自合规遥测或获批测试，health 200 不等于所有模型健康。

## 6. 工作链：先看板，再展开需求文档

```text
授权信息/归档 → 结构化认识 → 跨项目需求看板/候选
  → 用户选 1–2 个重点 → 单项需求文档（brief）
  → design + plan → 实施/调研/沟通/测试 → 报告/反馈
```

候选入看板只需要稳定候选身份、来源、摘要、价值、优先级、未知项；不要求先给每个候选写长需求文档。选择后复用 work-engine 正式分配 Item，再展开 brief。已有 Item 直接引用，不重复建号。

Recipe 是默认路径，不是线性强制状态机：沟通可没有 code/design/test；测试发现证据不足可回到设计；取消、替代、搁置、交付未合并按既有状态/outcome 记录，不发明平行闭集。[S5]

个人看板保留团队来源/优先级与个人优先级两个字段。布局：今日重点 → P0 → P1 → 待排期 → 等待/待决定 → 已交付待验收/合并 → 取消/替代 → 已完成（标题划线，置底）。只满足 Item 约定验收才进成功完成。Quay Task 不重复计为第二个业务成果。

## 7. 飞书来源、云空间与文档维护

关注源清单：获批 IM 范围、知识库/子树、散落 Wiki/文档、共享文件、会议/妙记、相关 GitHub 对象。逐项有身份、关注理由、刷新策略、归档深度、凭据引用和可发布级别。

不归档/浅归档/深归档是用户策略，不能冒充现有 closed enum。浅档保存获批消息与图片及链接关系；深档按范围展开附件与链接，逐子对象检查权限、深度/体积预算和去重。失败不是删除；分页不完整不报 100%。

来源树保留云端真实位置；工作树把相同对象关联项目/Item，不重复复制正文。知识库、云盘、我的文档库是不同 surface；骨架输出三张地图及范围/覆盖缺口。发现文档与拥有移动/编辑权不同。会议 bundle 关联会议、妙记、转写、获准音视频、摘要、决定和行动候选。

本轮先做显式输入适配、增量接口、样例与覆盖报告，不默认全量抓企业资料、不自动整理别人的云目录。当前 information-management 的手动刷新、board local-only 等合同若阻挡新功能，要明确版本化扩展“批准源的定时更新/审核后发布快照”，不静默改解释。[S6]

## 8. 目录与持久交接

沿用现有 role resolver，不把本提案当搬目录命令：

```text
workspace/src/<repo>                         canonical 代码
workspace/worktrees/<repo>/<ITEM>            隔离开发（按需）
workspace/test-envs/<repo>/<ITEM>             测试环境定义（按需）
workspace/ops                                实例策略/来源范围/地图
workspace/knowledge                          稳定可复用知识
records/projects/<project>/items/<ITEM>      card/brief/design/report 索引
artifacts/work-items/<project>/<ITEM>/<RUN>  原始执行证据与交付物
state/serveros                              状态/租约/游标
```

原始内容按现有信息能力的授权落点保存；给人看的 report 索引引用原件，不强迫同一 bytes 复制多份。Quay workspace/任务库属于自己的委派运行范围，父 Item 只存引用，不镜像一份 Quay state。

可发布工作台包含 START-HERE、BOARD、TODAY、UNDERSTANDING-DELTA、DECISIONS、REPORTS、QUAY-RESULTS、COVERAGE 与 RELEASE-MANIFEST；在获准 records 发布位置新增，不把本地数据库上传。

CC 整理 Decision Packet → 白名单 GitHub commit → ChatGPT 与用户审阅 → 决策记录绑定输入 revision → CC 在批准范围执行。GitHub push 不等于 ChatGPT 已读取，也不自动唤醒该对话。

## 9. 定时与用户控制

骨架提供可手动运行的 recipe 和默认 disabled 的定时模板；不在 daemon 调 LLM。实例策略明确 timezone、来源、收件人、窗口、预算和 last-success/cursor。单次锁、幂等、有限重试、可恢复检查点；局部来源失败报告未知而不清空看板。

日循环：更新来源/认识 → 推荐重点 → 用户选择/设计审批 → 获批执行与 Quay → 验证 → 日报/发布。已有批准队列可继续，不自行承诺所有新需求。新权限、共享生产变更、网络、预算超限或推翻目标时才提问。

## 10. 骨架边界

本轮至少一个正常样例走完整条链，并包含取消/未合并/失败/权限不足的反例。深度内容管理、全量云重排、每个业务环节的专家级方法、跨设备自动修复、无限 Quay loop、购买加速/改网络都留后续。以 IMPLEMENTATION-PLAN.md 的固定验收结束，不因发现旁支持续扩大本轮。
