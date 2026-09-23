# Ability Operating Model · 定时节点、职责Owner与serverosd边界

状态：2026-09-23 产品架构补充。本文把 ServerOS 的稳定 Ability 职责、Daily Cycle 的定时节点、Session/Workspace 等横向能力和唯一 Core 的边界放在一张图里。21个顶层语义职责不随 Skill / Plugin / MCP 重构改变；完整多层产品树见 ability-management [PRODUCT-ABILITY-TREE](https://github.com/ProfessorZhi/ability-management/blob/main/design/PRODUCT-ABILITY-TREE.md)。

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

同理，“选第二天工作并准备环境”是：

```text
Next-day Focus Planning
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
| Incremental Intake | 每30–60分钟 | A4触发 + B1 | 新消息/编辑/材料locator增量安全落地，不每次跑LLM |
| Evening Cutoff | 20:00 local | A4 | 固定本轮cutoff/cycle_ref，不代表采集已完成 |
| Archive Catch-up | cutoff后 | B1 | SHALLOW/DEEP/FOLLOW/REFERENCE追到cutoff；partial精确可见 |
| Structured Understanding | B1 receipt后 | B2 + A2 | 新事实/冲突/撤回更新；旧Claim即时失效 |
| Daily Reconciliation | B2 receipt后 | B10 | 只向人展示真正重要变化和待确认业务语义 |
| Requirement/Board Reconcile | 人回复或策略允许继续后 | B3 + B4 | 去重需求、现有工作/候选/等待状态对齐 |
| Next-day Focus | 建议约21:00或reconcile后 | B4 | 0–2个root focus，有来源理由，不凑数 |
| Session Prep | Focus成立后 | A1 | resume/create Parent Session、checkpoint与当前目标 |
| Workspace Prep | Focus成立后 | A3 | repo/base/worktree/dirty-owner/写入范围安全就绪 |
| Runtime Prep | Session/Workspace后 | A4 | 环境、资源、必要service、Binding ready |
| Night Research/Execution | standing delegation允许时 | B5/B6/B7/A6/A7 | 安全研究/设计/review/实现/测试，可部分完成 |
| Morning Resume | 建议08:30 local | A1+A3+A4+B4 | 检查source/base/runtime漂移，续昨天focus |
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

## 5. Focus之后Session与Workspace必须显式出现

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

A1回答“谁在做什么/如何继续”，A3回答“在哪里安全写”，B4回答“是否被准入”，Agent Control回答“允许做什么”，A4回答“环境能不能做”。

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
| 看板/明日0–2 Focus | B4 | serverosd不选业务优先级 |
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
