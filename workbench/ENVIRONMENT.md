<!-- ⟦snapshot⟧ rendered 2026-09-19T19:15:42+00:00 by `runtime/workbench.py` from ~/workspace/worktrees/serveros/foundation-skeleton -->

# ENVIRONMENT

> **读数** — 本页内容是这台机器在一个时刻的测量值，重跑会得到**新的**读数，不应当相等。load、内存、进程数取自本机当前快照。`serverosctl workbench check` 因此只核对这些命令今天仍然回答、页面结构没有塌陷，不比对任何数字。

<!-- ⟦answers⟧
serverosctl ability tree --json	0
serverosctl taxonomy tree --json	0
serverosctl device show --json	0
-->

### 能力树（本节点的投影）

来源 `~/state/serveros/abilities/effective.json` @ `fa784d228eb54c10180e928e6a04f602140ab307`，权威是 `ability-management`，基数 `domain.primary`。
能力 30 个，已归类 19 个；这个读数的年龄 209295 秒。

**这份投影可能过期。** 它不自己去读权威；`serverosctl ability refresh` 才会。

| node | abilities |
|---|---|
| serveros | — |
| serveros.agent | — |
| serveros.agent.embodiment | browser-use、opencli、playwright-mcp |
| serveros.core | ability-management |
| serveros.corporate | fjllm-switch |
| serveros.corporate.feishu | feishu-report |
| serveros.engineering | archguard、backend-solution-design、codex-pr-body、feature-developer、frontend-design、frontend-solution-design、good-pr、project-semantics-discovery |
| serveros.memory | meta-cc、session-manager |
| serveros.operations | — |
| serveros.operations.test | muc-playwright-e2e |
| serveros.operations.workspace | workspace-management |
| serveros.runtime | dev-doctor |

**没有归类的能力（11 个）**：archguard、better-know、browser-use、claude-plugins-official、codex-builtin、hwz-server-os、instructions、meta-cc、opencli、playwright-mcp、superpowers@cc-server-local。

这一行是刻意留着的：一个「已归类 19/30」的读数如果不同时说出剩下 11 个是谁，读起来就像那 11 个不存在。

### 声明的导航 DAG

基数 `declared-taxonomy`；家族 10 个，容器 1 个，catalogue 55 项（READ）。

这棵树由**分类元数据**派生，不由文件位置派生 —— 所以它可以被重新组织而不需要移动任何文件。

| family |
|---|
| serveros.agent |
| serveros.creative |
| serveros.human |
| serveros.information |
| serveros.memory |
| serveros.network |
| serveros.runtime |
| serveros.safety |
| serveros.security |
| serveros.work |

### 两大家族视图

渲染自 `~/workspace/worktrees/ability-management/foundation-skeleton` 的 `scripts/render_capability_families.py`。该渲染器有 `--check`，会因漂移而失败。

```
<!-- GENERATED FILE - DO NOT EDIT BY HAND -->

# CAPABILITY-FAMILIES.md - two-family work navigation view (v1)

> **GENERATED / NON-AUTHORITATIVE / REPRODUCIBLE.**
> A *view* of Ability metadata plus a declared family membership. Editing this
> file moves nothing; the next render overwrites it and `--check` reports drift.
> Source of truth: `abilities-manifest.yaml` (identity, path, revision, packs) +
> the membership table in `scripts/render_capability_families.py`.
> Regenerate: `python3 scripts/render_capability_families.py --write` - Verify: `python3 scripts/render_capability_families.py --check`
> Companion (machine-readable): `capability-families.json` - Metadata digest: `691f4bc13ae80c5c7ee9a5e48286c94afe8023fa8ff56d6165078a2db9514715`

Three things this view is not:

- **Not `ABILITY-MAP.md`.** That file is the taxonomy node view keyed on
  `domain.primary`. This one is a work-navigation view keyed on a declared
  membership. They are separate on purpose (§21: the two vocabularies must not
  overlap), and neither replaces the other.
- **Not a second catalogue.** Identity, Revision, Compatibility and Installation
  live in the manifest and are only *read* here. The single thing declared in
  this view is which cluster an ability is navigated under.
- **Not a plan.** A cluster with no member is a named gap, not a work item.

## 环境与能力基础设施 · Environment and capability infrastructure

> 这台机器、它上面注册的能力、工作空间与会话，现在是什么状态？

### 运行与资源  ·  1 项声明缺失

用户/整机资源、runtime 状态必须复用 ServerOS 当前事实入口；新 CLI/MCP/卡片不得另写 ps/df/docker 采集器。

| Ability | Identity | Status | domain.primary |
| --- | --- | --- | --- |
| `runtime-management` | `ProfessorZhi/runtime-management` | `PRESENT` | `serveros.operations.runtime` |
| `inventory-publishing` | `ProfessorZhi/inventory-publishing` | `PRESENT` | `serveros.operations.inventory-publishing` |
| `resource-observation` | — | **MISSING** | — |

### 能力治理

能力身份、Revision、Compatibility、Installation 的唯一管理协议。

| Ability | Identity | Status | domain.primary |
| --- | --- | --- | --- |
| `ability-management` | `ProfessorZhi/ability-management` | `PRESENT` | `serveros.core` |

### 工作空间

放置与容量需求 → 根解析/地图/提案。

| Ability | Identity | Status | domain.primary |
| --- | --- | --- | --- |
| `workspace-management` | `ProfessorZhi/workspace-maintainer` | `PRESENT` | `serveros.operations.workspace` |

### 会话

两个能力各守既有边界，不合并为一个会话库。

| Ability | Identity | Status | domain.primary |
| --- | --- | --- | --- |
| `session-manager` | `ProfessorZhi/session-manager` | `PRESENT` | `serveros.memory` |
| `agent-session-management` | `ProfessorZhi/agent-session-management` | `PRESENT` | `serveros.operations.runtime` |

### 工具与测试环境

固定版本/测试需求 → 环境定义/执行证据。

| Ability | Identity | Status | domain.primary |
| --- | --- | --- | --- |
| `test-management` | `ProfessorZhi/test-management` | `PRESENT` | `serveros.operations.test` |

### 人机控制与可观测性

当前快照/事件 → 三板块状态卡或人话告警。确定性呈现模型属于能力层通用代码，ops 只配节点/对象/目标。

| Ability | Identity | Status | domain.primary |
| --- | --- | --- | --- |
| `human-control` | `ProfessorZhi/human-control` | `PRESENT` | `serveros.operations.human-control` |


## 信息到交付工作能力 · Information-to-delivery work capability

> 从获批输入到可验收交付，这条链的每一环现在走到哪？

### 输入与归档

获批来源及深度 → 增量快照/覆盖缺口。

| Ability | Identity | Status | domain.primary |
| --- | --- | --- | --- |
| — | — | **无专职能力** | — |

复用（非本簇成员）：`information-management`

### 结构化认识

可追溯来源 → 事实/推断/冲突/决定/候选。本轮没有专职能力，由 information-management 兼任——这是缺口，不是设计。

| Ability | Identity | Status | domain.primary |
| --- | --- | --- | --- |
| — | — | **无专职能力** | — |

复用（非本簇成员）：`information-management`

### 看板与工作

候选、现有 Item、用户选择 → 个人/项目视图与单项 brief。候选入看板只需稳定身份/来源/摘要/价值/优先级/未知项，不要求先写长需求文档。

| Ability | Identity | Status | domain.primary |
| --- | --- | --- | --- |
| `work-management` | `ProfessorZhi/work-management` | `PRESENT` | `serveros.operations.work` |

### 设计与执行

获批 brief → design/plan → 证据与交付。Recipe 是默认路径，不是线性强制状态机。

| Ability | Identity | Status | domain.primary |
| --- | --- | --- | --- |
| `technical-architecture-and-planning` | `ProfessorZhi/technical-architecture-and-planning` | `PRESENT` | `serveros.engineering` |
| `product-requirements-and-design` | `ProfessorZhi/product-requirements-and-design` | `PRESENT` | `serveros.engineering` |
| `design-and-contract` | `ProfessorZhi/design-and-contract` | `PRESENT` | `serveros.engineering` |
| `implementation-delivery` | `ProfessorZhi/implementation-delivery` | `PRESENT` | `serveros.engineering` |
| `feature-development` | `ProfessorZhi/feature-development` | `PRESENT` | `serveros.engineering` |
| `quality-feedback-and-iteration` | `ProfessorZhi/quality-feedback-and-iteration` | `PRESENT` | `serveros.engineering` |
| `software-journey-orchestrator` | `ProfessorZhi/software-journey-orchestrator` | `PRESENT` | `serveros.engineering` |

复用（非本簇成员）：`test-management`

### Quay 委派

工作切片 → Goal/AC/Task → 有界运行 → 结果/日报段。

| Ability | Identity | Status | domain.primary |
| --- | --- | --- | --- |
| `cc-quay-delegation` | `ProfessorZhi/cc-quay-delegation` | `PRESENT` | `serveros.operations.work` |

### 文档与空间

云对象元数据/维护责任 → 三空间地图/更新或归档提案。

| Ability | Identity | Status | domain.primary |
| --- | --- | --- | --- |
| `feishu-space-management` | `ProfessorZhi/feishu-space-management` | `PRESENT` | `serveros.corporate.feishu` |

### 汇报与交接

已验证工作事实 → 日报/决策包/发布快照。

| Ability | Identity | Status | domain.primary |
| --- | --- | --- | --- |
| `feishu-report` | `ProfessorZhi/feishu-report` | `PRESENT` | `serveros.corporate.feishu` |


## 声明缺失（DECLARED_ABSENT）

设计点名而本仓库没有的能力。它们在这里被具名，不是被补齐——§26 禁止为了填一个空位而造一个 Ability。要么建能力，要么改设计叫法。

| 设计中的名字 | 现状 |
| --- | --- |
| `content-archiving` | 设计把内容归档列为 B 族的输入与归档职责；本仓库无此 Ability，本轮由 information-management 兼任。 |
| `document-stewardship` | 设计把文档维护列为 B 族的文档与空间职责；render_ability_map.py 已把它列为 baseline slot 并渲染 MISSING，本视图沿用同一结论，不另立一份判断。 |
| `resource-observation` | 设计把资源观测列为 A 族的运行与资源成员；实现上它是 serveros runtime 的代码（§40 resource observation），不是独立 Ability。要么建 Ability，要么在设计中改称它。 |

## 未被本视图归类（覆盖分母）

manifest 共 51 个 name，本视图归类 19 个，未归类 32 个。名单在下面——一个只列出自己认识的部分的导航图，读起来像覆盖完整，实际不是。

| Ability |
| --- |
| `agent-plugins` |
| `archguard` |
| `backend-solution-design` |
| `better-know` |
| `browser-use` |
| `claude-plugins-official` |
| `codex-builtin` |
| `codex-curated-remote` |
| `codex-node-repl` |
| `codex-pr-body` |
| `codex-primary-runtime` |
| `computer-use` |
| `dev-doctor` |
| `feature-developer` |
| `fjllm-switch` |
| `fleet-coordination` |
| `frontend-design` |
| `frontend-solution-design` |
| `good-pr` |
| `hooks` |
| `hwz-server-os` |
| `information-management` |
| `instructions` |
| `meta-cc` |
| `muc-playwright-e2e` |
| `opencli` |
| `playwright-mcp` |
| `project-semantics-discovery` |
| `serveros` |
| `shared-memory` |
| `superpowers@cc-server-local` |
| `vision` |

## Pack 成员关系与 pin（source/pack/pin 可复核）

Pack 是名字的集合，没有自己的 payload、path 或 version。`authority.revision` 是这组成员 ID 被**校验时**的 catalogue revision，不是「当前 main」；它可以是一个祖先，但那时 `abilities-manifest.yaml` 必须在两个 revision 上逐字节相同，否则构建报 PIN_MISMATCH 并拒绝。

| Pack | authority.revision | 成员 |
| --- | --- | --- |
| `pack:agent-orchestration` | `28f2cc76bb8c97c3714a3228482ed10f66a77e5c` | `agent-session-management` (required)<br>`fleet-coordination` (required)<br>`human-control` (required)<br>`runtime-management` (required)<br>`shared-memory` (required)<br>`work-management` (required) |
| `pack:autonomous-quality-loop` | `28f2cc76bb8c97c3714a3228482ed10f66a77e5c` | `implementation-delivery` (required)<br>`quality-feedback-and-iteration` (required)<br>`runtime-management` (required)<br>`test-management` (required)<br>`work-management` (required) |
| `pack:cross-device-collaboration` | `28f2cc76bb8c97c3714a3228482ed10f66a77e5c` | `fleet-coordination` (required)<br>`runtime-management` (required)<br>`shared-memory` (required)<br>`work-management` (required) |
| `pack:litellm-development` | `28f2cc76bb8c97c3714a3228482ed10f66a77e5c` | `backend-solution-design` (required)<br>`implementation-delivery` (required)<br>`quality-feedback-and-iteration` (optional)<br>`technical-architecture-and-planning` (required)<br>`test-management` (required) |
| `pack:quality-delivery` | `28f2cc76bb8c97c3714a3228482ed10f66a77e5c` | `feishu-report` (optional)<br>`quality-feedback-and-iteration` (required)<br>`test-management` (required) |

### Pack 点到而 manifest 里没有的名字

_无：每个 pack 成员都在 manifest 里。_
```

### 这台设备

{
  "device_ref": "device:tokyo-shared-dev-01",
  "host": {
    "cpu": {
      "cores_per_socket": 64,
      "logical_cpus": 128,
      "model": "AMD EPYC 9K65 192-Core Processor",
      "siblings_per_socket": 128,
      "threads_per_core": 2
    },
    "gpu": {
      "compute_capable": false,
      "display_controllers": [
        "Cirrus Logic GD 5446"
      ],
      "reason": "no compute driver reachable (nvidia-smi did not run). This host may still have a virtual display adapter, which is not a GPU in the resource sense.",
      "state": "UNAVAILABLE"
    },
    "load": {
      "load1": 2.39,
      "load15": 3.13,
      "load5": 2.94,
      "runnable": "4/7732"
    },
    "memory": {
      "available_bytes": 225648123904,
      "cached_bytes": 163056173056,
      "free_bytes": 51851628544,
      "swap": {
        "free_bytes": 17169920000,
        "total_bytes": 17179865088,
        "used_bytes": 9945088,
        "used_percent": 0.1
      },
      "total_bytes": 265182715904,
      "used_bytes": 39534592000
    },
    "uptime_seconds": 389108.37
  },
  "managed_resources": {
    "automatable": 0,
    "by_kind": {
      "container": 3,
      "systemd-user-service": 1
    },
    "count": 4,
    "protected": 4
  },
  "registry_path": "~/state/serveros/runtime/registry/resources.yaml",
  "role": "fleet-controller-primary (reference node)"
}
