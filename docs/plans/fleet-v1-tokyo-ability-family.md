# Fleet V1 · Tokyo 能力家族设计（LiteLLM / 日志分析 / MarketOps）

> 状态：设计，2026-10-31，待 owner 确认后进入 ability-management profile 与 rollout。
> 依据：Tokyo 实测（ability-management#39，2026-10-20 / 10-30 扫描）、serveros#531 三设备现场回执、
> 三个项目仓库（litellm-bridge、litellm-log-analysis、marketops / marketops-session）。
> 上游设计：`fleet-v1-device-plugin.md`（插件核心 / 外围 / 记忆）。

## 0. 结论

1. Tokyo 是公司主开发节点，当前负责三个项目：**LiteLLM**、**LiteLLM 日志分析**、**MarketOps**。MUC 不再负责。
2. 设备侧只有一个入口：**serveros 插件**。它常驻少量核心能力，并负责发现其余能力（目录搜索 → 按 pin 安装 → 读回）。
   项目专属的规则不做成常驻能力，写进**项目合同**，进入该项目时才读。
3. **serverosd 与任务监控**是设备侧的另一半：serverosd 提供本机事实（上下文、路径、资源、租约），
   中枢的常驻监控负责任务完成 / 失败 / 卡住的判定和飞书卡片。agent 不承担监控。
4. **调研分两处做**：GPT 侧负责广域联网搜索和把线索带进任务；设备侧负责本地取证（代码、日志、
   仓库历史、容器），也可以联网核对（上游 issue / PR / 发布说明），结论和链接由设备写进调研记录。

## 1. Tokyo 的工作链与能力

```text
来源（飞书消息 / 会议 / 谈话 / 线上报错 / 日志分析结论）
  → 结构化认识（records/projects/<项目>/）
  → 需求看板（候选，owner 接受）
  → 立项（分配编号；复现之前）
  → 需求文档 → 调研 → 设计文档        （按规模分档，见 §3）
  → 实施（worktree）→ 测试（A/B 容器，真实 E2E）
  → PR → 合并 → 验收 → 汇报 / 回流
```

| 环节 | 能力 | Tokyo 现状 | 处理 |
|---|---|---|---|
| 来源收集 | `information-management` + `feishu-space-management`（读） | 已装；B1「来源跟进」无主责 | 由 information-management 明确负责飞书跟进：跟哪些群/文档、频率、去重 |
| 结构化认识 | `information-management` | 已装 | 写到中枢 `records/projects/<项目>/`，GPT 用 `fleet_project_understanding` 读 |
| 看板 | Work Engine + `fleet_work_board` / `fleet_work_candidate_add` | 代码已合并，未部署 | 部署到 Tokyo（M5 同批） |
| 立项 | `work-management` | 已装，正文仍是旧 Item 写法 | 改为「候选 → owner 接受 → 编号」，写流程总规范与三档 |
| 需求文档 | `product-requirements-and-design` | 未装 | 进目录，按需装 |
| 调研 | `research-evidence` | 未装 | **常驻**（三个项目都用）；加「LiteLLM 先查团队仓、再查上游」 |
| 设计文档 | `design-and-contract` | 已装 | 补模板：根因 / 方案 / 验收 / 非目标 / 风险 / 验证计划 |
| 实施 | `implementation-delivery` | 现为 `feature-development`（CC）/ `feature-developer`（Codex）两份 | 统一到 implementation-delivery，旧名作别名 |
| 测试 | `test-management` + `automated-testing` | test-management 未装（实测） | 常驻；加 A/B 容器命名（§4） |
| PR | `delivery-pr-report` | `good-pr` / `codex-pr-body` 两份重复 | 只留 delivery-pr-report 入口，另两个作为它调用的第三方，目录按需 |
| 汇报 | `feishu-report` | 已装 | 保留 |
| 生产只读取证 | `production-observation` | 未装 | 常驻（日志分析与 LiteLLM 都要） |

## 2. Tokyo 现有能力逐项处置（实测清单）

Claude Code 23 个 skill、Codex 19 个 skill，以及插件。按「常驻 / 项目级 / 目录按需 / 移除」。

| 现有 | 处置 | 理由 |
|---|---|---|
| serveros、workspace、work、test、information、session、ability-management | **常驻核心** | 每个任务都用 |
| research-evidence、design-and-contract、production-observation、feishu-report、feishu-space-management、automated-testing | **Tokyo 常驻** | 三个项目的主链都用 |
| `bridge-development` | **项目级（LiteLLM）** | 只在 litellm 工作时有意义；未在登记表里，需先登记 |
| opencli 一族（CC 6 + Codex 6） | **项目级（MarketOps）/ 目录** | 网页读取与浏览器操作，MarketOps 研究可能用；其余项目不常驻。待 owner 确认 MarketOps 是否依赖 |
| MarketOps 4 个插件 | **项目级（MarketOps）** | 只在 MarketOps 工作时装载；是否放在 vince 日常环境由 owner 定（原定例外账号） |
| `fjllm-switch`、`dev-doctor`、`network-environment`、`inventory-publishing`、`hwz-image-build` | **目录按需** | 运维工具，偶尔用 |
| `frontend-design`、`frontend/backend-solution-design`、`grill-me`、`project-semantics-discovery` | **目录按需** | 偶尔用；grill-me 来源不明需批准 |
| `good-pr`、`codex-pr-body` | **目录按需**（由 delivery-pr-report 调用） | 去重 |
| `feature-development` / `feature-developer` | **合并到 implementation-delivery** | 去重 |
| `muc-playwright-e2e` | **移除常驻** | MUC 不再负责 |
| 插件 `quay`、`openviking-memory`、`meta-cc`、`superpowers`、`better-know` | 保留 | 委派、记忆、会话分析、写作方法 |
| `serveros-fleet-ingress`（指挥侧） | 保留，**升级到 12 skill / 48 工具** | 现为旧 8 skill |
| `hwz-server-os-linux-work` | 保留，修安装记录与运行版本不一致 | — |

结果：Claude Code 与 Codex 用同一套（现在两边不一致）；常驻约 13 个，其余按项目或按需。

## 3. 流程分档

| 档 | 何时 | 必走 |
|---|---|---|
| 小 | 小 bug，对外行为不变 | 立项 → 复现（A）→ 修 → B 验证 → PR（PR 里写清来源与根因） |
| 标准 | 有对外行为变化 | + 需求文档、设计文档 |
| 大 | 协议 / 权限 / 路由 / 跨模块 / 付费 / 生产影响 | + 调研、独立评审、真实客户端 E2E、上线与回滚 |

## 4. 三个项目的项目合同（要点）

### 4.1 LiteLLM

- **三层仓库**：上游 `BerriAI/litellm` → 团队主线 `79JaX/litellm-fjd`（真正开发处）→ 镜像 / 发布
  `ProfessorZhi/litellm-bridge`（`main`/`dev` 冻结于 2026-09-04，不在此开发，不从此部署；团队血统
  分支只快进）。
- **调研先查已有修复**：先查团队仓，再查上游 issue / PR / 发布说明 / changelog，与我们用的版本比。
  结论四选一：已修（升级或移植）/ 有 PR 未合（跟踪，必要时先摘）/ 已报未修（自修，考虑回馈上游）/
  无人报（自修，并到上游报 issue）。结论与链接写进调研记录，设计文档引用它。
- **编号**：我们的工作项是 `LIT-XXXX`；上游 CLAUDE.md 里的 `LIT-1234` 是 BerriAI 的 Linear 工单号，
  两者无关，agent 不得混用。
- **容器**：`lit-XXXX-version-a-proxy` / `-version-b-proxy`（新旧代码）、`-config-a/b-proxy`（同代码开关）、
  `lit-XXXX-<变体>-proxy`（附加对照，如 filter-on、keepalive）、`lit-XXXX-repro-mock-proxy`（只定位，不作证据）。
  复现容器就是 A。未立项不起容器。
- **测试**：同一矩阵跑 A/B；核对容器内实际源码 SHA；写明测试层级；付费上游有预算；客户端相关的问题
  在 FJPCOS 用真实客户端验证。

### 4.2 LiteLLM 日志分析（litellm-log-analysis）

- 数据三层：生产 PG `LiteLLM_SpendLogs`（运行事实）/ 测试服 OpenSearch + 对象存储（全量原文）/
  `records/projects/litellm/reports/daily/`（脱敏结论，唯一进 Git 的一层）。
- 硬约束：生产只读；单次拉取 100 GiB 上限；原始 prompt/response 永不进 Git、不进长期记忆；
  百分比由程序算，LLM 只解释样本。
- 产出：日报 + 从中发现的问题作为看板候选（来源 `online-error`），例如 LIT-0225。

### 4.3 MarketOps（marketops / marketops-session）

- marketops：市场研究执行系统（场地与资产母池、责任实体核实、有证据有时效的联系人与公开邮箱、
  台账与交付表）。真实外联默认关闭（`send_authorized=false`），单独授权。
- marketops-session：用真实会话反推人工市场作业流程，判断哪些能交给 MarketOps。
- 研究轮次与系统改进需求分开记：研究轮次不进开发看板，系统改进走开发流程。

## 5. 调研的分工

| 谁 | 做什么 | 产出 |
|---|---|---|
| GPT（指挥侧） | 广域联网搜索：上游 issue、讨论、文档、同类方案；把链接和要点带进派发给设备的任务 | 线索清单（附链接），不下结论 |
| 设备 agent | 本地取证：代码、git 历史、团队仓与上游仓对比、日志、容器复现；也可联网核对上游 issue / PR / 发布说明 | 调研记录（结论、来源、反证、适用范围、缺口），写进证据目录，设计文档引用 |

GPT 的搜索结果是线索，不是证据；结论以设备侧可复核的记录为准。

## 6. serverosd 与任务监控（Tokyo）

| 部分 | 现状（10-30） | 要做 |
|---|---|---|
| serverosd（Python） | user unit 在跑，socket 在，`serverosctl` 可用 | node MCP 改用 `core` 工具集后读回一次 |
| core-lite（C++） | 未装，`fleet_status.core_lite = NOT_CONFIGURED` | owner 批准后 observe-only 启用 |
| 中枢常驻监控（M1–M4） | 已合并，未部署 | M5：部署、读回真实资源与任务数据 |
| 飞书卡片 | journal 模式 | owner 授权真实发送（接收人 owner 本人） |

## 7. 落地顺序

1. owner 确认本设计（§2 处置表、§3 分档、§4 项目合同）。
2. ability-management：Tokyo profile `tokyo-development-primary`；research-evidence / work-management /
   test-management 写入 §3、§4 的规则；去重与别名；先合 #43，再更新 #44。
3. 中枢 records：三个项目的项目合同与当前认识（需要 records 仓库）。
4. 指挥侧 `fleet-work-drive`：分档、需求 / 调研 / 设计三道关、LiteLLM 两层查已有修复、调研分工。
5. Tokyo：升级指挥侧插件；按 profile dry-run → apply → readback；新会话抽查行为；演练一次回滚。

## 8. 交给 GPT 的汇总任务（各设备工作内容）

> 用旧版 Fleet 插件即可。只读，不安装、不改配置。回执写到 ability-management#39，每台设备一条。

对 Tokyo（vince）、Dell A6000（fjd）、FJPCOS 各派给原有空闲会话，请设备 agent 汇总：

1. **最近 30 天在这台设备上做过的工作**：按项目列（LiteLLM / 日志分析 / MarketOps / 其他），每项给出
   仓库、分支或工作项编号、做到哪一步（立项 / 需求 / 调研 / 设计 / 实施 / 测试 / PR / 已合并）。
   来源：会话历史、git 提交、工作项记录、证据目录。只写去敏摘要。
2. **每类工作实际用到了哪些 skill / MCP / 插件**，哪些装了却从没用过。
3. **在哪一步卡住过、缺什么能力**（例如：找不到上游修复、不知道容器怎么命名、汇报格式不对）。
4. **这台设备上正在运行的长期资源**：容器（按名字）、测试环境、租约，标出属于哪个工作项。
5. **个人项目（仅 Dell）**：只写「有 / 无」和占用的 GPU 时间段，不写项目内容。

汇总后，由 Claude 依据回执修订 §2 的处置表。
