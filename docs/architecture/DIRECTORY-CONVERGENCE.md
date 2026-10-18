# 目录收敛实施合同：清楚、浅显、可持续

2026-10-08。本文把 [总架构 §17](NATIVE-WORK-ENVIRONMENT.md#17-本地飞书与-github-的目录合同) 落成 CC 可实施的规则。它定义目标，不授权删除未知或受保护内容，不宣称历史迁移已经执行。机器根值以 host profile/resolver 为准；通用 Skill 不复制 Tokyo 路径。

## 2026-10-17 Fleet 三设备实现落位提醒

当前 [完整 Fleet V1 campaign](../plans/plan-02-complete-fleet-v1-delivery-campaign.md) 将每设备授权 OS principal 的 C++ Native Core、CloudCLI/CC+Codex Runtime、Node Connector/安全凭据/Ability/服务 Desired State 列为本地受管对象；Work Engine、G1 Ability 和 Agent Control 仍只保持各自一份 authority。三机开发源码和测试现场应在可追踪的 Git branch/worktree 和受管 artifacts，而非从东京直接把某个用户的 $HOME、models、凭据或进程状态搬去 Dell/Windows。Windows 127.0.0.1:16095 SSH→Selkies/Claude Web 是有明确 owner 的服务 Binding，不是应搬入 Git 或清理重装的目录。

GitHub Issue #244 及其子任务用作计划和 PR 可见性，不建第二 Work 状态库。真实跨设备安装、生产服务自启、文件/凭据迁移或删除仍须独立人类授权与回滚；Hosted GitHub CI 因额度耗尽可以标记托管运行器豁免，但不能跳过本地验证。

## 1. 必须实现的结果

- **D01 唯一默认入口**：同一种新产物只有一个可解释的默认落点；来源、owner、生命周期和 reader 清楚。
- **D02 先修生产者**：工具、脚本、timer、测试、构建从开始就写对；不靠每日搬运补救。
- **D03 深度可解释**：导航浅，身份层级保留；不叠加等义目录，不扁平化第三方工具内部结构。
- **D04 实际消费闭合**：服务/Agent 读到正确版本，不只检查文件存在或 PR 合并。
- **D05 当前开发连续**：活动 worktree、Session、lease、挂载、受保护 Item、他人 WIP 和唯一证据保留。
- **D06 历史有去向**：可安全切换的有兼容/回退，不能动的标明保留原因与再次检查条件；未知不自动删除。
- **D07 人读入口整洁**：每个主要入口用短说明说明用途、owner、当前入口和相关链接。文档/卡片避免内部代码和长日志占首屏。

D01–D07 同时成立才算相应范围收敛；“目录数变少”不是独立成功判据。

## 2. 根与区域职责

workspace 严格保留 `src/ worktrees/ test-envs/ ops/ records/ knowledge/` 六区及已声明的根说明文件。其它产物去 host profile 声明的工作盘区域。

| 区域 | 新内容的默认职责 | 不允许混入 |
|---|---|---|
| `src/<repo>` | canonical 源码检出与 Git 缓存 | 本次实施 WIP、测试输出、venv |
| `worktrees/<repo>/<work-name>` | 隔离代码/文档实施；仓库自己的构建规则可由该处发起 | 把 runtime 安装绑定到可退役工作树；永久证据唯一放此 |
| `test-envs/<repo>/<profile>` | 可复用环境定义、配置模板、启动/验证入口 | 运行数据库、浏览器 profile、真实 secrets、构建大产物 |
| `ops` | 实例、项目、source/Provider Binding、地图、runbook | 复制 Core 代码或复用 Skill 实现；凭据值 |
| `records` | 来源索引、适合持久记录的快照、认识、正式工作记录、文档源与交付指针 | 活进程状态权威、构建/缓存/大日志 |
| `knowledge` | 有长期复用价值的知识与方法 | 重复的 Current View、Work 状态或整个 raw 库 |
| `artifacts/work-items/<project>/<ITEM>/<RUN>` | Run 输出、测试证据、截图、交付版本、当次临时文件 | 装机的可变 source、业务状态的第二权威 |
| `artifacts/skills/<skill>/<run>` | 无 Item 的有限操作产物 | 为普通读写强行创建 Work Item |
| `state/serveros` | 已有 Work/Agent Control/recipe/outbox/投影等 owner 状态 | 未声明的随意新 namespace、源码和秘密 |
| `runtime-assets` | 固定版本安装产物及合法 current/previous 入口 | 从运行副本反向回写 canonical；不受版本管理的手改补丁 |
| `logs/caches/toolchains/envs/data` 等 profile 区 | 分别承担日志、可重建缓存、工具链、已声明共享环境、受控业务数据 | 将同一个位置同时当唯一证据、缓存和正式资料 |
| native `.claude/.codex` 等 | 原生客户端的安装/会话布局 | 为目录对称迁移活跃缓存或伪造客户端加载状态 |
| secret store | 当前允许的 vault 或实际凭据 owner | 工作树、Git、报告、截图里的明文秘密 |

本机实例树仍以总架构 §17.2 与 profile 为准；已有合法 scratch/software/downloads/tmp 等区域按原用途保留。若 profile、指令和 producer 对某类输出的默认值冲突，先列出具体冲突并统一有效规则；在统一前新工作使用当前明确允许的较窄路径，不能拿设计草案自行越过现有约束。

## 3. 深度预算与命名

以下深度指相对所列语义根的**目录层数，不计文件名**。目标用于我们控制的导航/输出结构；超过目标要有真实身份或工具格式理由，不是自动移动/删除命令。

| 语义根 | 默认形状 | 目标深度 |
|---|---|---|
| `src` | `<repo>/` | 仓库入口 1；仓内源码不按此限制 |
| `worktrees` | `<repo>/<work-name>/` | 实施入口 2；不把 Git branch 的每个 `/` 变成物理层 |
| `test-envs` | `<repo>/<profile>/` | 定义入口 2；需要的内部模块可保留 |
| `records` 项目工作 | `projects/<project>/items/<ITEM>/` | Item 入口 4；brief/card 通常直接放其中 |
| `records` 当前认识 | `information/understanding/<kind>/` | 目录 3，实体通常是一份文档 |
| `records` 新来源入口 | `information/raw/<provider>/<source-key>/` | source 入口建议 4；精确版本快照可再加 `snapshots/<revision>/` |
| `artifacts` Item 产物 | `work-items/<project>/<ITEM>/<RUN>/` | Run 入口 4；其下通常只加 evidence/tmp，确有多个 case 才再分一层 |
| `artifacts` 非 Item 产物 | `skills/<skill>/<run>/` | 操作入口 3；其下规则同上 |
| 人类飞书导航 | 区域 → 项目 → 用途/具体文档 | 从区域入口通常 2–3 次点击；此处是导航步数，不是文件系统层数 |

新 raw 布局是**待实施的有版本布局 profile**，不是要求立即移动现有 `feishu/chats/...`。source-key 由现有 B1 owner 从 provider/tenant/kind/稳定身份分配，不能仅凭名称合并。先让 reader/producer 支持新 profile 和历史 locator，再决定哪些新源采用；历史准确版本与引用不可失效。若现有布局已经必要且消费者稳定，可登记理由保留，不为达到数字重写所有 capture。

命名规则：

- repo/project/工具名沿既有稳定身份；一般人建目录使用短、可读的 kebab-case。
- Item/Run 保留 owner 分配的 ID，不重编号，不在文档预占“下一个编号”。
- 日期用于版本、周期或证据，不给没有日期语义的每层目录都加年月日。
- 一个层级只表达一件事；禁止新增等义的 `final/final2/output/result/output` 链。
- Git branch、文件名和物理目录是不同对象；不靠改目录名改变项目身份。
- 第三方源码、node_modules、venv、浏览器/客户端缓存、不可变 release 和原始 trace 包按工具格式保留，按 scope 排除出导航深度统计。
- 不为空目录写一套 README；repo、项目、运行入口等主要节点需要短入口。长历史台账可以保留，但不继续充当日常默认必读正文。

## 4. 一件工作与一个 Run 的模板

```text
records/projects/<project>/items/<ITEM>/
  card.yaml                 # 身份、来源、AC、预期交付
  brief.md                  # 当前工作说明
  design.md                 # 需要方案记录时
  plan.yaml                 # 需要持久 Activity 计划时
  verify.md                 # 需要长期人读验证说明时
  report.md                 # 需要长期人读交付说明时

artifacts/work-items/<project>/<ITEM>/<RUN>/
  result.json               # 执行结果/receipt 的快照或索引，不是第二 Work 状态
  report.md                 # 如需，基于同一事实源生成
  evidence/                 # 对应测试/效果的日志、截图、trace
  tmp/                      # 当次可再生中间产物
```

这是新产物的默认形状，不要求每个 Run 四项齐全，也不移动已登记的历史证据。正式 deliverable 通过稳定 locator 登记；报告与索引不抄多份机器事实，不把自身 hash 写进自身文件形成循环。

## 5. 发布读面与编辑现场

每个 reader 必须知道自己读的是工作树、固定 Git revision、来源 snapshot 还是运行 owner。返回实际版本与读取面，不能把一棵树的 MISSING 当整个对象不存在。

1. 保护带 WIP 的 authoring checkout，不整体 reset/clean/快进来解决 reader 问题。
2. 优先复用已交付的显式 Git revision 读取。需要文件系统路径的消费者，才使用声明的只读派生快照，保留同一 Records authority。
3. consumer 实际读取内容才算成功；状态行解释清楚只算诊断改善。
4. 源快照按 B1 版本读取，运行 lease/grant/Run 从实时 owner 读取；不把一份 published records 当所有事实的当前库。
5. local WIP、已接受 revision 与 source 版本冲突时明确交 owner 处理，不静默拼出混合版本。

## 6. 飞书与 GitHub 的人读整洁度

飞书沿用 My Document Library、个人笔记、公开文档、外部文档、Drive 五个区域。自己的项目入口可链接需求板、正在做的工作、报告和知识，不必复制每个 Item/Run 为一个 Wiki 节点。

- 需求板：当前 Focus、待对齐、未完成、已完成；源优先级与正式优先级有出处。没有 P 级可显示待排，不阻止已选工作。
- 公开/协作文档：原位维护 canonical，保留既有读者、链接、人工区和风格。
- Follow：Source Card/生成镜像与人的批注分区；上层导航可缩短入口，不擅自搬深层原文。
- 报告：首屏给结果、影响、要不要行动和关键链接。内部 ID、raw JSON、完整日志折叠或链接到证据。
- 资产：Drive/受控资产层保存大字节，records/Git 保留必要 metadata 和定位。
- 390px 预览要求层次、留白、状态语言一致，颜色不作为唯一状态信号；截图验收只覆盖本次人可见变化。

GitHub 按项目收敛为六个仓库：ServerOS 用 `serveros`（含 `workengine/` 与 `research-exchange/` 的模板和工具）和 `ability-management`，外加私有数据仓库 `hwz-records`；MarketOps 用 `marketops` 和 `marketops-session`；LLM Lab 用 `llmlab`。各仓库守自己的 owner 边界；根 README 指向短入口和正式契约；临时输出、运行库、秘密与大资产不进普通 Git。

## 7. CC 必须交付的迁移批次

逐个**实际需要变更的范围**准备以下字段，可复用现有 ops/proposal/Run 载体，不建新数据库：

`对象与 owner / from / to / 为什么要变 / producer / consumers与引用 / 当前活动快照 / 版本与内容检查 / 授权范围 / 切换步骤 / 回退 / 保留与再次检查条件`。

按次序执行：

1. **改默认生产者**：修脚本、配置、测试入口、timer、模板，使新产物正确落位；临时文件显式设置数据盘路径。
2. **准备目标与兼容读取**：在当前允许范围内生成/验证新目标，证明旧版本仍可解析。
3. **切换消费者**：只切本范围内可安全切换的入口，必要时用原子 pointer；不把候选 build 当已加载。
4. **实际读回**：业务命令/服务/Agent 使用新入口；核内容、版本、owner、资源和未中断工作。
5. **处理旧位置**：标记 retained/defer/可退役条件。未获批准的删除保持 proposal-only；不为报零漂移清理未知内容。

读写大于等于 250 MB 时执行既有容量/地图检查。清理、退役、移动、改名前重新取得相关 state、worktrees、containers、open files、active Runs/lease 的新鲜快照，不能沿用报告里的旧快照。已知活动/可恢复等待、LIT-0103/LIT-0104、未知 owner 或唯一证据不动。

普通已授权的代码、配置、无损新物化与已核实声明修正直接推进。需要删除独有内容、处理他人 WIP、扩大 ACL/受众或改变生产执行模式的批次，必须另有具体授权；不能把它们混成对整个工程的“批准继续”。

## 8. 本轮完成判据

- 选定范围的新 producer 落点全部符合 D01/D02；没有继续向已确认错误位置写入的默认行为。
- 深度统计排除有理由的 native/tool/vendor/release 子树；被治理入口符合目标或有具名保留理由。
- Source、Item、Run、文档和版本 identity 在切换前后相同，旧证据可定位，当前 consumer 有实际读回。
- 活动工作未被中断，保留例外可解释、可再次检查，没有靠删除保护对象减少 finding。
- 给人一份短 current map 和 before/after 变更范围；不再把全量 inventory 或历史流水账作为默认入口。

上述完成是本次**目录范围**的完成，不代替 21 项 Ability 的业务验收，也不要求把每个历史目录清空。
