# Fleet Next × Native Ability × OpenAgents：联邦多 Agent 协作 v0

状态：**DESIGN / NOT INSTALLED / NOT DEPLOYED**（2026-10-14）。本文件是接口与交付实验设计，不代表第三方网络、Agent 或容器已经启动。

## 目标
用户向 ChatGPT 提出目标（例如“在 Dell 的隔离环境起一个测试容器并跑验证”）；GPT 负责消歧、检查授权和结果回报，**不会硬记**该主机的 Docker/Compose 命令、路径、凭据。ServerOS 依据 canonical Ability/Provider/Device/Runtime/Policy 选择实际执行位置，由具备该环境知识的 Native Agent 执行。长任务由项目 Work Parent 在本地分层委派，允许 Codex 领导、DeepSeek 做有界实现、独立 reviewer 验证，而 ChatGPT 不必逐步转发每条中间消息。

## 既有能力的权威，不可新建第二套
| 问题 | 唯一 owner | 新层只可以做什么 |
|---|---|---|
| 21 项顶层语义 Ability、Skills 来源和 revision | ProfessorZhi/ability-management（G1） | GPT Skill 只保存调用/选择原则；不得复制全量本地技能 |
| 当前目标、Work Item、Parent、Run、完成定义 | ServerOS B3/B4/Agent Control | 记录关联 ID；OpenAgents Task 不可改写权威 |
| 单机有效能力 | ServerOS Ability Fabric + 设备 runtime binding | 按 Ability × Provider × Profile × Binding × Runtime × Health × Policy × TaskContext 求交集 |
| 真实执行、进程、系统资源与服务健康 | Node Core + OS service manager + 本地 Provider（A4） | 网络只接收观测/receipt，不具备绕过 OS 的权力 |
| 设备/会话寻址、owner grants | Fleet（A5） | GPT/Parent 调用授权接口；断线不另找秘密入口 |
| 委派方法、租约、任务返回与独立核验 | A6/Agent Control/Work Parent（A7 验收） | OpenAgents 只是可换的 delegation/messaging Provider |
| 业务与生产高风险授权 | Human Control（B10） | Agent 不给自己批准或扩权 |

**禁止角色上提权**：Codex 是 Parent 不代表它能让 DeepSeek 自动取得生产权限；OpenAgents 的 @mention/Task 不是 ServerOS grant。一个 Work Item 同一 epoch 只有一个有效 writer Parent。其他研究/评审可以只读并行，具体编辑使用独立 worktree/写入租约。

## 目标调用流
1. GPT 接收用户意图，识别本轮是否适合直接回答、GitHub 直接改、还是必须本地 Agent。跨设备执行前先查已授权范围。
2. GPT/Fleet 读设备和 Session 的 *当前* fact，必要时通过 Native Agent 调用 existing Ability Resolver；`UNKNOWN` 与 `UNAVAILABLE` 不可混同。
3. 将目标转成有界 task envelope，携带工作、设备、Runtime、Ability/operation、源版本、可允许副作用、预算、验收条件、原尝试 ID；不得放明文密钥、公司 transcript。
4. ServerOS 按当前权限、provider readiness、write lease / active Agent 检查，指派一个 Parent（本地 Codex/Claude 皆可），而不是让 ChatGPT 持有全部子任务循环。
5. Parent 依据任务可独立验收性/预算分解 worker 或直接完成。可选通过 OpenAgents Network SDK 的 task_delegation mod 派发；先登记 intent、幂等关联，断线后先查询原任务。
6. 实际 Native Agent 读取受管的本地 Skill/Provider（例如容器运行环境）、做 preflight，必须有 owner 授权才发出命令或修改文件；原始环境不复制到云端。
7. Worker 产出 raw diff / tests / receipt；Reviewer 在独立上下文核原始证据。Parent 接收 RESULT_READY 后才评估 ACCEPTED 或 FAILED。
8. GPT 查询 Work 状态和验收记录，向人报告 **已派发 / 执行中 / 结果待验 / 已验收**，而非用 tool delivery 冒充完成。

### 示例：起测试容器（仅语义示意）
- GPT 不构造 `docker run` 命令，也不猜镜像、卷、网络或 root path；指定目标设备、环境和“仅测试环境”的授权边界。
- 目前注册的 Ability ID 必须从 canonical catalogue 查；`container-start` 只是待核对的 operation 候选，不得因为此文出现就算受支持。
- 本地 Native Agent 解析实际 provider/installed skills/Compose project、owner、端口冲突、资源限额及潜在副作用。无可用 provider → 明确返回 UNKNOWN/UNAVAILABLE；生产 deployment 仍由 Human Control 批准。
- 返回运行中的容器 ID、health/readiness、实际测试和版本 evidence；仅生成配置或退出码 0 不等于运行健康。

### 示例：Codex 强模型领导，DeepSeek 执行
- Codex Parent：需求与 AC、方案/任务 DAG、预算、工作树分配、对原始结果复核。
- DeepSeek worker：通过**确认可用且获准的** LiteLLM/OpenAI-compatible Provider 执行一个有界实现切片；没有可用模型/agent harness 时拒绝而不是假装已运行。
- 独立 Codex/Claude review：新的 Session/隔离视角，读取 diff、测试、依赖和安全界线；不让作者自己盖章。
- Parent：组合验证、接收、形成可审 PR 候选；只有人或既有发布策略能授权生产变更。
- 可追加研究/测试 agent，**不是**所有任务都必须组队。小任务直接 GitHub 或单个 Agent 成本更低。

## 提议中的数据合同（非运行接口）
~~~yaml
schema: serveros/agent-delegation-envelope.proposal.v0
work_ref: "work:<canonical>"
parent_ref: "agent-parent:<canonical>"
task_ref: "task:<id>"
attempt: 1
epoch: 1
idempotency_key: "<stable-id>"
assigned_role: implementer  # parent | implementer | reviewer | researcher
assignee_provider: openagents  # or native | quay
source_revision: "<immutable git sha>"
ability_ref: "ability:runtime-management"  # known canonical ability; no invented op
requested_operation: "<resolve from manifest>"
target:
  device_ref: "device:<enrolled-and-authorized>"
  runtime: "<resolved runtime tag>"
  worktree_ref: "<verified isolated locator>"
authorized_effects: ["none"]    # requires an actual grant for writes
budget: {wall_seconds: 600, memory_bytes: null, model_tokens: null}
acceptance:
  kind: "raw-artifact-plus-tests"
  checks: ["source revision matches", "independent evidence"]
evidence_locator: null
~~~

`work_ref`, task refs, operation names and runtime tags are **illustrative** until mapped to current actual schemas; this proposal must not become a second canonical registry or a fake invocation endpoint. The provider returns provider task ID + lifecycle + checksum/provenance; ServerOS stores only a minimal mapping to existing Work/Agent Control, not duplicated task bodies.

## 状态、幂等和故障
- `QUEUED` → `RUNNING` → `RESULT_READY` → `ACCEPTED/FAILED`; `WAITING_DEPENDENCY`、`CANCEL_REQUESTED`、`STOPPED`、`EFFECT_UNKNOWN` 显式表达。
- Provider ACK 只能确认已接单；模型回复 DONE 只能触发 RESULT_READY，不自动变为验收通过。
- 重放同 task+attempt 返回旧关联；输入、baseline 或授权范围变化时必须增 epoch。晚到结果不覆盖新 epoch。
- Agent 掉线先查 last receipt/actual effect，不能自动再派一遍破坏幂等；取消未被进程确认，不能释放 writer lease。
- 主管/子 Agent 的权限是各自身份和 device/runtime/verb grant 的交集，不传递 Owner Token、飞书 cookie、SSH key。
- 上游共享网络内原始业务数据、完整 transcript、机密不得默认写入 OpenAgents hosted Workspace；PoC 优先 self-hosted/loopback，不新增公网暴露端口。
- Native C++ Core 不嵌入模型，不跑 OpenAgents。它只负责资源、健康、服务守护和可验证的状态；Python ServerOS 继续作为生产 writer，迁移需单独 canary。

## OpenAgents 选型
选择 **openagents-org/openagents-sdk**（Agent Network SDK，含 `coordination/task_delegation`）；区别于旧的 `xlang-ai/OpenAgents` Data/Web/Plugins 平台。SDK 可作消息/委派/发现 Provider，Launcher/Workspace 是可选层，不得无审查安装一套新的 Agent 常驻管理器抢走 Core 的进程监管职责。需核实所选固定版本、适配器工具面、MCP/A2A 边界和 DeepSeek agent harness。

参考：
- https://openagents.org/docs/en/getting-started/overview
- https://github.com/openagents-org/openagents-sdk
- https://openagents.org/blog/posts/2025-12-01-task-delegation-mod
- ServerOS `docs/ability-fabric.md`、`docs/ABILITY-OPERATING-MODEL.md`、`docs/architecture/NATIVE-WORK-ENVIRONMENT.md`
- Ability Management `design/DELEGATION-ABILITY.md`、`design/FLEET-HANDOFF-ABILITY.md`

## 最短落地路径与验收
**P0 GitHub-only（本 PR）**：合同与两份 cloud-facing thin Skills。未经跑环境测试，不算插件完成。
**P1 离线 PoC**：SDK 锁定版本、fake task transport、fake provider、任务关联/幂等/失联/拒绝/独立review单测；不得安装到生产。
**P2 设备侧授权 canary**：有明确 owner/设备/资源/端口/数据范围后，隔离测试环境中 Codex Parent → DeepSeek Worker → Review 完整闭环；逐个检查真正运行的 agent、产物和成本。
**P3 Fleet Next 分发**：正式 ZIP 包含薄 Skills、图标、MCP app link；先让 34 tools + 飞书 SSO 和双账户在真实客户端验收，再与 OpenAgents 做联合演练。旧 V1 在验收后退役。

至少比较原生单 Agent 与多 Agent：从提交到接受的耗时、真实通过率、出错率、模型成本、资源消耗、人工中断数、恢复能力。只有有净收益的任务类别才自动采用多 Agent。
