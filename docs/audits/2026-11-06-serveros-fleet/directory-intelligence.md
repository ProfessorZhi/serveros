# Directory Intelligence：三设备目录与仓库治理审计

采集窗口：2026-11-06 UTC。公开版路径已脱敏为 Path Role 或 HOME 相对名称。所有目录数量、mtime、状态都是快照。

## 1. 设计结论

当前主要问题不是“目录太多”本身，而是缺少目录身份、Path Role 绑定、Git checkout 类型、owner principal 和生命周期证据。

Fleet 必须区分：

- Canonical source checkout
- Git worktree
- Independent clone
- Runtime installation
- Toolchain
- State
- Cache
- Artifact / evidence
- Scratch / temporary
- Archive / backup
- Knowledge / data

同一个 Git remote 的多个目录不必然是重复 clone；Worktree 共享 common git dir，应作为同一 repo 的不同 checkout 身份管理。

## 2. Tokyo Linux

### 2.1 Path Role 绑定

Fleet Profile 定义 12 个语义 Path Role；现场 host-profile 声明约 21 个物理 roots。两者没有完全投影。

| Path Role | 声明/现场情况 | 风险 |
|---|---|---|
| data_root / home | 指向同一 principal data root | 低；语义重复 |
| workspace.src | 绑定到 workspace source root | 与另一个未登记 src root 并存 |
| workspace.worktrees | Worktree root 存在 | 混入非 Worktree 数据目录 |
| artifacts | 已绑定且存在 | 低 |
| state | 已绑定且存在 | 低 |
| toolchains | 已绑定且存在 | 低 |
| runtime-assets | 已绑定且存在 | 低 |
| downloads | 已绑定且存在 | 低 |
| knowledge.corporate | BOUND，但目标目录不存在 | 高：STALE_BINDING |
| software.gui | Profile 为 NOT_APPLICABLE，但 host-profile 有 software root | 中：语义冲突 |
| scratch | 声明一个 scratch root；workspace 下另有第二个 scratch | 中：双重写入位置 |

host-profile 中额外存在 logs、envs、software、cache、tmp、build scratch、bin、node 等 roots；role consumer 无法仅通过 12 项词表完整看到它们。

### 2.2 Checkout / Worktree 清点

| 指标 | 观测 |
|---|---:|
| Git repo entries | 147 |
| Worktrees | 128 |
| Standalone checkouts | 17 |
| Dirty repositories | 29 |
| 未提交文件总量 | 2,467 |
| ServerOS checkouts | 46 |
| Ability Management checkouts | 15 |
| MarketOps Session checkouts | 19 |
| MarketOps checkouts | 18 |
| LiteLLM Bridge checkouts | 17 |
| HWZ Records checkouts | 12 |

重要区分：
- 128 个 Worktree 不是 128 个独立 clone。
- 同一 ServerOS repo 的多份 checkout 跨越多个目录根。
- 4 个 ServerOS checkout 存在 dirty 状态；不能列为自动清理候选。
- 两份 Ability Management checkout 的 branch/HEAD 不同；其中一份 readback profile 所在 checkout 不是 main。
- 同名 ServerOS checkout 有不同 HEAD；本次审计曾使用设备本地旧 SHA，而 GitHub main 基线不同，说明 canonical checkout 不明确。

### 2.3 其他目录异常

- workspace 下存在未投影目录、空 backup 目录和非 Git 数据目录。
- Worktrees root 下存在空目录和被误放入的 records 数据。
- workspace/scratch 与独立 scratch role 并存。
- 目录体积统计仅完成部分根：source root 约 109 MiB、state 约 77 MiB、cache 约 647 MiB；其余多项因负载超时标记 UNKNOWN/SKIPPED。
- 目录扫描期间 Load Average 从约 121 升至约 245；禁止在类似负载下重复全量 git status / du。

## 3. Dell A6000 Linux

### 3.1 Path Role principal 错配

Fleet Profile 的 home / source / worktrees / toolchains 绑定指向另一个 OS principal 的 HOME。该目录确实存在，但其 owner 与当前执行 principal 不同；部分声明子路径不存在。

当前执行 principal 的真实路径则包含 projects、worktrees、artifacts、state、runtime-assets、downloads 等目录。

这是 P0 级治理问题：设备级 Registry 不能把某个用户的 HOME 当作所有 Runtime 的通用 HOME。

### 3.2 活跃但未声明的目录

| 语义角色 | 实际观察 | 当前 Profile |
|---|---|---|
| projects / source | 存在，含多个 repo | 声明路径错配 |
| worktrees | 存在，约 37 entries | 声明路径错配 |
| artifacts | 存在，约 28 directories | UNBOUND |
| state | 两套状态根在使用 | UNBOUND |
| runtime-assets | serverosd runtime 从此处启动 | UNBOUND |
| downloads | 存在 | UNBOUND |
| workspaces / scratch | 存在但为空 | 未完整建模 |

### 3.3 两个 State Root

现场有两套用途不同的 state：

1. serverosd runtime registry/state
2. Core-Lite / device-kit / device-version publishing state

不应把它们合并成一个模糊的 state 目录。建议使用语义角色，例如 state.serverosd 与 state.core_lite，并分别记录 writer、consumer、freshness 与 retention。

另外，serverosd unit 声明的 node configuration 文档路径在现场为空；daemon 仍在运行。实际配置来源需要进一步确认，不能推断其运行默认值。

### 3.4 Repo / Worktree 统计

| 指标 | 观测 |
|---|---:|
| Git repo entries | 61 |
| ServerOS remote entries | 52 |
| Primary clone registered worktrees | 53 |
| Registered worktrees 超过 7 天未活动 | 51 / 53 |
| Orphan ServerOS clone | 1 |
| Symlink aliases（限定深度） | 0 |

正确解释：
- 52 个 ServerOS remote entries 主要是注册 Worktree，不应称为 52 个独立克隆。
- Primary clone 候选拥有 53 个 registered worktrees。
- 另有一个未注册的 dirty clone，不能自动清理。
- 51 个 stale Worktree 是清理候选，不是删除授权。
- Ability Management 有两个主要 checkout，HEAD/branch 不同，且本机 checkout 均未与当前上游版本对齐。
- 一个目录名与其 Git remote repo identity 不一致。

### 3.5 Principal 与 Runtime 可见性

- 当前 Fleet Runtime 绑定到一个明确的 Linux principal。
- 设备是多用户主机；其他 principal 的 HOME 未进入。
- Profile 指向其他 principal 的 HOME 是错误/歧义，不代表可以跨用户读取。
- 真实 HOME 的 Skills 与 CloudCLI sandbox HOME 的 Skills 不同；能力必须按 principal + runtime + filesystem namespace 评估。

## 4. FJPCOS Windows

### 4.1 Path Role 与路径权威冲突

Windows 现场至少有两份路径权威：
- Fleet Profile
- 本机 paths.yaml / node profile

两者对 knowledge path 的声明不同，而且两处目标均不存在。

| Role | 现场情况 | 风险 |
|---|---|---|
| home | 当前 principal HOME 存在 | 应按 principal 绑定 |
| workspace.src | projects root 存在 | 可作为 source root 候选 |
| workspace.worktrees | 有独立 worktrees root 与多个 Worktree | 未绑定 |
| artifacts | artifacts root 存在 | 有目录数据 |
| state | ServerOS local state 存在 | Fleet Role 未绑定 |
| runtime-assets | ServerOS runtime assets 存在 | Fleet Role 未绑定 |
| toolchains | 开发工具链 root 存在 | C/D volume 分散 |
| downloads | paths.yaml 使用 intake.raw 命名 | 词表不一致 |
| software.gui | GUI software root 存在 | 应明确 role |
| knowledge.corporate | 两份配置目标均不存在 | P0：BROKEN |
| data_root | 未形成单一绑定 | 高 |
| scratch / tmp | 多个临时根并存 | 中 |

另外：
- 一个声明的 volume 在采集时不存在，标记 STALE。
- work.active、session.archive 等本机角色未进入统一 Path Role 词表。
- CloudCLI 自身 state 与 ServerOS state 需要分别归类。
- Core-Lite state 已存在，但 live=false。

### 4.2 Repo / Worktree

- ServerOS 有 54 个 registered Worktree。
- Ability Management 有 5 个 Worktree。
- 主 projects root 下存在多个 ServerOS checkout、独立备份和工作树。
- 部分 Worktree 位于独立 worktrees / scratch roots。
- 一个 backup 目录不是 Git repo。
- 临时目录下约 172 个子目录，建议建立保留策略，但不得自动删除。

## 5. 统一 Directory Intelligence 设计

### 5.1 稳定 Path Role 词表

建议语义词表至少包括：

- home
- workspace.src
- workspace.projects
- workspace.worktrees
- artifacts
- state.serverosd
- state.core_lite
- state.cloudcli
- toolchains
- runtime-assets
- downloads
- software.gui
- knowledge.corporate
- logs
- cache
- scratch
- tmp
- session.archive

词表是语义层；每台设备、每个 OS principal 的物理路径是独立 binding。不要把路径写死在 device-level role 中。

### 5.2 Path Binding Schema

每条 binding 至少需要：

- device_ref
- principal_ref
- path_role
- path
- realpath
- filesystem_ref
- owner_ref
- state：BOUND / UNBOUND / NOT_APPLICABLE / STALE_BINDING / CONFLICT
- provenance
- observed_at
- expires_at / freshness_policy
- evidence_ref

校验规则：
- BOUND 路径必须存在。
- BOUND 路径 owner 必须与 principal binding 匹配，除非显式授权共享。
- 同一 principal + role 默认只有一个 canonical binding；多路径需显式标记 primary / secondary / staging。
- 实际在用但未声明的目录应标记 UNDECLARED_IN_USE，不能静默忽略。
- 未知不能自动降级成不存在。

### 5.3 Git Checkout Identity

每个 checkout 应记录：

- normalized remote identity（不含 credentials/query）
- checkout path binding
- clone / linked worktree
- common git dir / parent repo identity
- branch / HEAD
- dirty state 与 dirty count
- last commit time
- last observed filesystem activity
- owner principal
- registered / orphan / detached / stale status

**Stale 与 dirty 必须分开。** Dirty checkout 绝不能自动成为清理候选。Registered Worktree 不能按目录数误判为独立 clone。

### 5.4 Lifecycle / Cleanup

建议状态机：

DISCOVERED → CLASSIFIED → CANDIDATE → OWNER_REVIEW → APPROVED_ARCHIVE → RETENTION → DELETE_APPROVED

- 扫描器只提出候选。
- Owner 确认前不得移动或归档。
- 归档与物理删除是两个不同授权。
- 自动清理只允许在明确策略、保留期、无 dirty / no active run 证据下提出建议，不能直接执行。
