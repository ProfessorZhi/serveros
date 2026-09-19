# CC 驱动 Quay：需求 → Goal/AC → Task → 一轮执行 → 汇报

这是工作能力家族中的有界委派协议。主入口建议为 `cc-quay-delegation`，可在同一能力下分 assess/prepare/drive/verify/report 操作；不要一开始就造五个独立 Skill 或第二个 Quay。

## 1. 本次源码确认的概念

本次读取 Quay `develop-archived-20260816@ddc4fb40f616148e3b278cbc062e6599285cd549`。该 repo 另有 develop 和 dist-plugin 分支；这里的源码结论不是东京已安装版本的证明。[S7]

`packages/quay/src/goal-store.ts` 与对应 SPEC 明确：

- Goal store 是独立对象类型，不是 Task/ADR/Method Document。
- `PHASE-NNN` 表示一个阶段目标；`AC-NNN` 表示关联该 PHASE 的验收判据。
- Goal 的状态为 active/achieved/superseded/retired。AC 有 criterion、expect、origin 和 evidence；PHASE 本身没有 criterion，其达成由关联 AC 推导，空 AC 集不算达成。
- 同一 Goal store 不应有两个 active PHASE；切换必须明确旧阶段处置。不能为每日试用擅自替换另一个会话的 active PHASE。
- Task 有 primitive/compound、父子、Proposal/Plan/AC/DoD；Task 正文中的 AC 与 Goal store 的 AC 不是同一对象。[S7a][S7b]

`author` 以独立交付物判断拆分；`loop-driver` 包含选择/隔离/构建/门禁/审查/记录/land，不是 test-only 命令。`task-operator` 明文禁止自主启动 worker、执行和完成任务，不能拿它冒充驱动器。[S7c–S7e]

## 2. 正确映射

```text
ServerOS 工作 Item（业务目标，常跨多天）
  └── Activity（本次实现、测试、调研等）
      └── Run + 委派合同（本次授权边界）
          ├── Quay PHASE（本次适合它的阶段目标）
          ├── Quay AC（结果如何证明，含 origin）
          └── Quay Task tree（做什么才能达到 AC）
                 ↓
          实际执行/门禁/独立验证
                 ↓
          结果、未通过项、对父工作贡献、日报段
```

不是把每个业务 Item 自动复制成一个 Quay epic；也不是为了每天跑一次而每天强制建一个 PHASE。已有获批且范围相同的 PHASE 可以继续，只分配新的执行记录。新范围需要独立或获批切换的 store；任何 ID 必须按当前合法接口和现存命名空间分配，不从聊天估计下一个数。

映射关系保存在 ServerOS 的委派合同与 Quay 支持的引用字段/正文链接，不自行改 Quay schema 加假的 parent_item 字段。每个 task 说明覆盖哪些 Goal AC；覆盖表是生成视图，不再复制一份状态数据库。

## 3. 先确认实际执行面

CC 只读识别当前 plugin/artifact/version、Provider manifest、命令 help、Goal store/Task store、author/execute/目标 gate 支持及现有活跃工作。入口映射落到一份小的 capability report：Goal 读写/评估、Task 读写、指定任务执行、结果读取，各自的支持和权限。

不同版本的工具名称以实际暴露为准；本设计不发明 `quay goal run` 或新的 MCP 名称。不支持 Goal 时可输出待提交规范草案和明确缺口，但不得说已用原生 Goal 跑通；更不能把 PHASE/AC 偷塞进 Task 来冒充同等语义。

Quay CLI init 与 CC 的 /quay:init 不是一回事，后者可能安装循环、agents 和 gates。不要在业务 canonical 或他人 dirty 树执行全量初始化。首次样例在本 Item 独占的 Quay workspace 中，使用明确允许的安装/已有发行物；初始化将改 CC 全局配置或启动服务时先检查副作用，未获批不做。

## 4. 拆分算法

1. 读取已选择的 Item brief/design；不让 Quay 代选今日业务重点。
2. 判断可委派性：输入确定、边界小、可验收、权限够、资源/费用可控。
3. 选一个有价值结果作为本次 PHASE 目标，关联来源与用户决定，不把原始聊天直接当可执行指令。
4. 写 Goal AC：criterion 是真实可执行判据；expect 可解析；origin 是真实需求/证据。检查 criterion 自身的写入/网络副作用，禁止不可信原文变 shell 命令。
5. 由结果反推 Task。只有多个独立交付物才拆 compound，步骤留 Plan。Task 的 AC/DoD 分别说明局部验收和真正交付。
6. 创建、回读、校验 Goal 与 Task，检查无越界引用、不碰已有 active PHASE；不直接写底层 store 绕过合法接口。
7. 在明确任务 ID/独占任务域内驱动一次，禁止从全项目 ready pool 或 Quay 自己的 dogfooding backlog 自动选活。
8. 核验真正执行数量、输出、目标版本、实际 diff、gate 语义、Goal AC 读数；结果归还父 Activity。process exit=0、任务创建、日志中说“done”都不足以验收。

## 5. 三种范围策略

| 场景 | 可授权切片 | 禁止 |
|---|---|---|
| 自有新项目 | 已批准模块开发、检索 prototype、引用回链、离线评测 | 自行确定业务需求、未获准销售/客户资料、生产发布 |
| LiteLLM 等旧项目 | 固定版本测试、复现、合同验证、脱敏样本对照 | 修改产品代码、全量 loop 接管、合并/部署、改共享网关 |
| 沟通/开放调研 | 可核材料检查、已定义实验，或不委派 | 冒充沟通完成、对外承诺、用一次工具调用代替研究结论 |

test-only/scoped-build 是本适配层策略，不是假定存在同名 Quay flags。旧项目可用独立 test harness，输入版本只读，输出写隔离目录。worktree 不是安全沙箱；Quay 默认 land 只能在明确批准的 harness/task store 发生，绝不能外溢到目标产品。

## 6. 最小委派合同

字段建议作为新接口草案，实施时复用已有扩展槽，不强改闭集：

```yaml
schema: serveros/quay-delegation.v1
parent: {item_ref: REQUIRED, activity_ref: REQUIRED, run_ref: REQUIRED}
objective: REQUIRED
input_refs: []                 # 内容、revision、相关决定
scope:
  mode: test-only              # 本适配层策略标签
  readable_refs: []
  writable_refs: []
  forbidden_actions: []
authorization_ref: REQUIRED
budget: {wall_seconds: REQUIRED, retries: REQUIRED, cost_limit: REQUIRED}
quay:
  artifact_revision: REQUIRED
  provider_ref: REQUIRED
  workspace_ref: REQUIRED
  phase_ref: null
  criterion_refs: []
  task_refs: []
acceptance_refs: []
output_ref: REQUIRED
```

未填 REQUIRED 的合同不能执行。预算来自本轮约定/现有策略，不能把示例数值自动当授权。scope 包含网络/秘密/全局配置边界；允许目录不等于授予命令无限权限。

## 7. 本轮要跑的真实样例

优先用本次 ServerOS 骨架自己的实际交付：固定候选提交，委派 Quay 检查三板块状态模型、UNKNOWN/STALE、恢复关联和看板完成项呈现，并生成具体问题清单。这样结果用于当前工作，不虚构业务需求，不触碰 LiteLLM。

在能合法使用原生 Goal/Task 和有界入口时执行一轮；存在安装/权限/不兼容阻塞则交付可回放合同与准确阻塞，不由 CC 在 Quay 外做完再宣称 Quay 成功。synthetic fixtures 可验证本系统 renderer，但必须标 SAMPLE；真正发生的 Quay 调用、任务和输出才是其试用证据。

## 8. 汇报与完成语义

分别记录：驱动执行成功否、被测目标通过否、Quay AC 是否达成、父 Item 是否满足验收、PR 是否合并。目标是“可靠复现 bug”时，发现 bug 可以满足该目标；目标是“目标系统全部通过”时，不能把测试执行结束写成 achieved。

日报 Quay 段包含：父工作、PHASE/AC/Task refs、真实版本/运行、输入、执行内容、结果、验证证据、对主工作价值、试用问题。没合适任务可不跑；不把 build task cards 或 fixtures-only 冒充实际 Quay 运行。后续改目标须版本化，不能改验收标准掩盖失败。
