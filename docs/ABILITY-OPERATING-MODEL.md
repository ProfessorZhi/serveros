# Ability Operating Model · 定时节点、职责Owner与serverosd边界

状态：2026-09-24 产品行为收敛。本文把 ServerOS 的稳定 Ability 职责、Daily Cycle 的定时节点、Session/Workspace 等横向能力和唯一 Core 的边界放在一张图里。21个顶层语义职责不随 Skill / Plugin / MCP 重构改变；完整多层产品树见 ability-management [PRODUCT-ABILITY-TREE](https://github.com/ProfessorZhi/ability-management/blob/main/design/PRODUCT-ABILITY-TREE.md)。

## 2026-10-17 完整 Fleet V1 · 设备 Ability 与 Core 分层

原有 21 项语义职责与 B1–B10 日常流程不变。增加三机原生运行基础范围：[Fleet 拓扑](architecture/FLEET-V1-TOPOLOGY-OPERATIONS.md) 和 [V1 campaign](plans/plan-02-complete-fleet-v1-delivery-campaign.md)。**每受管设备/OS principal 一个生产写入 Native C++ Core** 是最终本地运行形态；本文件现有 serverosd/Python Core 的文字是当前/历史实现基线，迁移为 C++ 时需 observe-only 影子阶段且不能同一 unit 双重恢复。Device Ability 仍从 [ability-management](https://github.com/ProfessorZhi/ability-management) 的同一份 manifest 派生，不给 Tokyo/Dell/Windows 建三个权威 registry。对 CC+Codex 的真实 Runtime、实际安装的 Skill/MCP/CLI、Device Binding、健康和授权逐设备决议。

用户要求开发阶段**先验收本地 Claude Code Fleet Plugin + 共享 Operator Skills**，再将同源能力打包到 ChatGPT；Work Engine/Agent Control 仍是任务、writer、grant、checkpoint/receipt 权威。定时检查只触发有限 routine，不得变成长驻 LLM/额外 workflow daemon。当前每小时外部进度检查是辅助接续，不能用通知系统替代 Work Engine 的 durable dispatch/effect recovery。CI Hosted runner 因额度耗尽可明确记录 QUOTA_WAIVER，不把未运行的本地/真机验收称作通过。

## 1. 一句话

ServerOS 的日常产品不是“一个daemon替用户工作”，而是：

```text
OS scheduler 在有限时间点唤醒有限routine
        ↓
routine组合稳定的Semantic Abilities
        ↓
Abilities调用当前可用的Skill/Core/Provider
        ↓
结果写回各自authority与receipt
        ↓
下一节点只在前置结果真实成立时继续
```

时间负责**唤醒**，receipt/state负责**依赖**。任何一个Timer都不能用“时间到了”代替“上一步完成”。

## 2. Scheduled routine不是新的Ability

例如“每天晚上归档飞书→结构化认识→和用户对账”不是一个叫 `daily-sync` 的新Ability，而是一条产品 Profile：

```text
Evening Reconciliation
├─ A4  Scheduler/runtime wakeup
├─ B1  Source Intake / archive catch-up
├─ B2  Structured Understanding
├─ A2  Recall invalidation/correction
└─ B10 Human Reconciliation delivery
```

同理，“选择本轮Focus并准备环境（可当晚执行）”是：

```text
Current Focus Selection
├─ B3 Requirement reconcile
├─ B4 Board / Focus / Admission
├─ A1 Session prep
├─ A3 Workspace prep
└─ A4 Runtime readiness
```

Profile/Recipe可以改变时间和组合，但不拥有上述职责的事实。

## 3. 推荐的一天节奏

时间是**实例策略**，不是全球架构常量。对当前个人工作节奏，推荐以下默认候选；实际启用前由instance Binding/standing delegation确认时区、预算和receiver。

| 节点 | 推荐触发 | Ability owner | 用户结果 |
|---|---:|---|---|
| Incremental Intake | provider event优先；否则实例配置的有界poll | A4触发 + B1 | 新消息/编辑/材料locator增量安全落地；无变化不跑LLM |
| Evening Cutoff | 19:xx Asia/Shanghai实例时点 | A4 | 固定本轮cutoff/cycle_ref，不代表采集已完成 |
| Archive Catch-up | cutoff后 | B1 | SHALLOW/DEEP/FOLLOW/REFERENCE追到cutoff；partial精确可见 |
| Structured Understanding | B1 receipt后 | B2 + A2 | 新事实/冲突/撤回更新；旧Claim即时失效 |
| Daily Reconciliation | 20:00提醒；资料不完整明示 | B10 | 差量对话，没有待决定项可短/空，不伪造已读完 |
| Standing Daily Report（启用时） | report cutoff后 | B8 + B9 + B10 | 从accepted Outcome/evidence生成一份稳定日报；不阻断夜间Focus |
| Requirement/Board Reconcile | 人回复或策略允许继续后 | B3 + B4 | 去重需求、现有工作/候选/等待状态对齐 |
| Active Focus | 对齐/接受后 | B4 | 0–2个root focus，可当晚执行，完成后不自动补位 |
| Session Prep | Focus成立后 | A1 | resume/create Parent Session、checkpoint与当前目标 |
| Workspace Prep | Focus成立后 | A3 | repo/base/worktree/dirty-owner/写入范围安全就绪 |
| Runtime Prep | Session/Workspace后 | A4 | 环境、资源、必要service、Binding ready |
| Focus Execution / Continuation | standing delegation允许时 | Parent + 按需B5/B6/B7/A6/A7 | Native直达简单任务；复杂/高风险时才展开研究、review、委派或E2E |
| Optional Morning Catch-up | 实例需要时 | A1+A3+A4+B4 | 核已选Focus与source/base/runtime变化；不是次日才允许开始 |
| Session Maintenance | 低频/夜间 | A1.3 | 只archive明确非活动且有checkpoint的Session |
| Workspace Maintenance | 低频/夜间 | A3 | 只标RETIRE_CANDIDATE或清明确可再生物，不大扫除 |

用户说“每天8点”时，产品应先解析是早8还是晚8及具体时区；如果语境是“归档今天并选第二天”，默认建议晚间20:00，但配置本身归instance policy。

## 4. Daily Reconciliation是什么

它不是日报，不是500条消息审核，也不是B2自己给自己通过。

首层应类似：

```text
今天真正改变认识的事项 4
- Nature补充了X → 项目认识已更新
- Bruce转发Y → 与当前SOP冲突，需确认
- LiteLLM验证Z → 只是已有修复验证，不创建新需求
- 外部文章W → Reference已收藏，未采用为内部规范

自动更新 3
需要你确认 1
当前冲突 1
```

B10负责把B2的结果呈现给人并收回真实决定；B2负责语义事实，不拥有“用户已确认”的UI效果。

## 5. Focus后的继续责任与按需准备

```text
B4 Focus accepted
      │
      ├──────────────┐
      ↓              ↓
A1 Session       A3 Workspace
├─ Context       ├─ repo/root
├─ live Session  ├─ base/remote
├─ Parent        ├─ worktree/branch
├─ checkpoint    ├─ dirty/owner
└─ history       └─ write scope
      └───────┬──────┘
              ↓
        A4 Runtime ready
              ↓
      B5/B6/B7/A6/A7...
```

A1回答“谁在做什么/如何继续”，A3回答“在哪里安全写”，B4回答“是否被准入”，Agent Control回答“允许做什么”，A4回答“环境能不能做”。这些责任必须有着落，不要求所有任务依次调用所有Skill。已有Session/工作树/环境符合当前范围就复用；历史archive/restore只在实际需要时展开。

## 6. serverosd只做Node Core，不做业务总管

唯一resident Core `serverosd`负责**长期存在且必须有单一节点事实/控制面的事情**：

- runtime/resource registry与readiness事实；
- IPC与Node API；
- Agent Control durable store、trusted-context RPC、approval/grant/admission相关机械状态；
- lease/effect/bounded-runtime所需的节点级协调；
- sampling/progress/persistence等自身健康事实；
- current Binding/registry解析所需的低成本node facts；
- 有限、幂等、可恢复的机械reconcile；
- 向外暴露“当前事实/控制状态”，供Abilities消费。

`serverosd`**不负责**：

- 每小时主动抓全部飞书；
- 自己读消息并生成语义认识；
- 调LLM决定Business Requirement；
- 拥有Personal Board或Work Engine状态；
- 选择业务Focus；
- archive Session transcript；
- 创建/删除Git worktree；
- 运行一个长驻workflow engine；
- 把Laya分数当权限/事实；
- 自证自己的release已经VERIFIED。

这些分别属于B1/B2/B3/B4/A1/A3/G1/A7等职责。

## 7. Scheduler负责唤醒，不负责业务判断

使用OS/已授权scheduler，例如systemd user timer。一个logical routine只有一个trigger owner。Timer只做：

```text
wake finite worker
→ worker读取current state / receipt
→ 该做就做
→ 不满足就WAIT/PARTIAL/NOOP
→ 写checkpoint/receipt
→ exit
```

不要新增 `workflowd`、`daily-managerd`、`sessiond`、`ability-evolutiond`。

遗漏周期恢复时按cycle_ref/cutoff幂等合并；不能机器重启后补跑三遍同一晚任务。

## 8. 自迭代如何进入一天，但不抢业务工作

G1.5 Ability Evaluation & Evolution是低频/事件驱动治理子能力：

```text
正常业务运行
  ↓
Journey/A7/人反馈/MetaCC/receipts积累证据
  ↓
G1.5发现“职责 vs 实际效果”gap
  ↓
形成候选改进
  ↓
隔离eval
  ↓
G1正常build/install/fresh consumer
  ↓
promote/rollback
```

它**不需要**每天自动改Skill。每日循环最多收集证据；真正优化只在有重复失败、重要gap、新模型/runtime采用或人工反馈时进入治理队列。

MetaCC适合提供Session错误、retry、tool use、bug-fix、tech-debt、edit-sequence等观测；它不能决定新的Ability职责、扩大权限或自动promote。

## 9. 关键职责表

| 用户事件 | 稳定Owner | 非Owner |
|---|---|---|
| 定时触发一次routine | A4 / OS scheduler | B1/B2不自己做daemon |
| 飞书IM归档 | B1 | serverosd不读语义 |
| 关键联系人深归档/forward材料 | B1 | B9不接管raw |
| 别人Doc/Wiki Follow | B1 | B9只管维护/Reference |
| 结构化认识 | B2 | B1不下业务结论 |
| 旧认识失效 | A2 | remote Memory不覆盖records |
| 每日和用户对账 | B10 | B2不自称user_confirmed |
| 新需求是否成立 | B3 | IM本身不自动建Item |
| 看板/当前0–2 Focus | B4 | serverosd不选业务优先级，不因空槽造工作 |
| Session resume/archive | A1 | B4不管transcript |
| worktree/branch/dirty-owner | A3 | Session不拿cwd当项目 |
| runtime/service/resource | A4 | Workspace不另采ps/df |
| GPT/研究 | B5/B6 | Laya不当事实/权限 |
| Quay并行 | A6 | Task done不关Parent |
| 验收 | A7 | child自报成功不算 |
| build/install/release | A8/G1 | Git merge不等于LIVE |
| own/shared docs | B9 | B8 compose不等于publish |
| 卡片/告警/决策 | B10 | send不等于人同意 |
| Ability自迭代 | G1.5 | MetaCC不自改系统 |

## 10. LIVE门

不能因为Timer存在就称Daily Cycle LIVE。至少J06要证明：

```text
real source delta
→ B1 capture
→ B2 semantic commit
→ A2 current recall
→ B4 board/admission
→ <=2 focus
→ A1/A3/A4 prepare
→ GPT/design/review
→ CC/Quay execution
→ A7 acceptance
→ required B8/B9/B10 delivery
→ feedback
```

并验证duplicate trigger、source drift、grant revoke、cancel/effect unknown、parent restart、partial publication与quiet window。

## 11. 自动化接续不是第二业务调度器

已接受Work Item的子结果到达、已登记依赖满足或合法时间窗口到来，可通过已有有界routine唤醒原逻辑Parent；先核当前scope/owner/预算/原attempt再继续，不每个timer新建长期Session。实际没有receiver或触发机制就只称保存待续。

原生Parent选择方法并验证必需交付；`serverosd`只维护被真正consumer使用的机械事实，不解释所有飞书消息或替用户选新目标。一个resident Node Core不禁止既有Provider、CloudCLI、Tailscale和实际Agent进程存在。

采集/对齐/接续/状态卡各有不同节奏，不用统一“一天一次”的时钟；routine错过时点有界补缺，状态不补发每个漏小时、工作不重复创建收费Task。源/权限/目标变更只围住相关效果，其他已授权工作继续。

## 12. 自动交接与唤醒：消除“人肉连接器”

自动化承诺必须落到一个真实receiver/trigger；不是看到上一步DONE就假设下一步会发生。这里定义产品接缝，不新增workflow engine或事件总线。receiver可以是同一个Parent同步继续、已有finite routine、provider callback/receipt consumer或OS scheduler。

| Producer事实 | 需要时的Receiver | 默认触发 | Receiver不可用时 |
|---|---|---|---|
| B1 capture/source revision receipt | B2 reader/semantic routine | 当前batch/cutoff或已登记重要source事件 | raw保留，understanding lag明确；不重抓source |
| B2 Claim/View commit / correction | A2 eligibility + B3/B4相关projection | commit后本地失效即时；需求/Board按当前对账节奏刷新 | current recall先正确；远端Memory/Board可滞后但不复活旧事实 |
| B3 Formal Requirement accepted/revised | B4 canonical Work Item reconcile | acceptance receipt | Requirement保留，Work Item接缝标未闭合；不让用户手建第二任务 |
| B4 Focus accepted / resumed | A1 logical Parent；按需A3/A4 | focus/continuation intent | 明确待续；不启动重复Parent，不声称后台运行 |
| A6 child result / Quay Task terminal | 当前Work Item Parent | provider result/receipt或有界poll | 结果durable保存为待接收；新Parent可接原结果，不重派 |
| A7 acceptance verdict | Parent / B4 completion evaluation；B8按需消费证据 | verdict receipt | 保留已验证范围，Completion不前移；不丢已成功测试 |
| A8 install/running observation | Parent / G1 runtime projection | install/readback receipt | source/build结果保留，准确停在未安装/未运行 |
| B9 publish/readback receipt | B10 sender（仅当Requirement要求送达） | 受众/窗口/receiver已解析 | 文档保持已发布，delivery单独待续；不重建正文 |
| B9/B8自己产生的新revision | B1/B2 source loop | 自发revision去重后只处理后续人改/外部改 | 不形成自循环理解/报告 |
| A4 health/impact change | B10 Daily Status / Incident | 状态/episode transition | observation仍durable；sender故障不改健康真相 |
| OpenViking/MetaCC indexing result | A2 future retrieval | 异步ack，无需唤醒普通Work Item | records/source路径继续；只有当前required retrieval受影响才提示 |

### 接续去重

每条异步边至少能够回答原producer identity/revision、当前receiver、是否已被接受、是否仍适用于当前Work Item/epoch。timeout/断线先查同一attempt/result，再决定是否补派；不能因为Parent换Session或scheduler重启就复制child、消息、文档或外部effect。

### 不把接缝变成固定流水线

表中某一边只有在当前用户目标需要它时才存在。一个小型代码修复可以在同一Parent内从A3直接B7→局部验证→Outcome，不需要先跑B1/B2/B3；一个会议理解更新可以B1→B2结束，不创建Work Item。自动交接解决的是**已有责任如何继续**，不是规定所有任务必须经过哪些框。

### 实施验收

每个被宣传为“自动”的异步接缝都要跑一次 producer完成后用户不发第二条消息的测试：receiver应自己收到/轮询到结果并继续，或者准确落为待续。如果测试只能靠操作者复制ID、打开另一个Session说“继续”、手工贴child结果，架构覆盖仍是PARTIAL。

## 产品职责树的按需入口

`serverosctl ability tree --view product` 从已物化的 canonical catalogue 显示 G1/A/B 全职责；`--responsibility A7` 只展开该职责。MCP 使用既有 `ability.tree`，参数同名。词表与 manifest 同 revision，刷新用既有 `ability refresh`。此视图表达职责和声明映射，不能宣称安装、授权或旅程完成；`view=domain` 保留安装导航语义。当前各职责的工程与验收边界见 [执行 ledger 当前入口](CC-EXECUTION-LEDGER.md#当前入口整棵产品职责树2026-10-07srv-0011--run-0018)，不要把完整 ledger 放进每次初始上下文。
