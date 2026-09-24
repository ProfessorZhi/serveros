# Understanding, Memory & Daily Reconciliation · ServerOS Integration

状态：2026-09-24 系统组合合同。详细语义由 ability-management [UNDERSTANDING-MEMORY-RECONCILIATION-ARCHITECTURE](https://github.com/ProfessorZhi/ability-management/blob/main/design/UNDERSTANDING-MEMORY-RECONCILIATION-ARCHITECTURE.md) 拥有。本文只定义 ServerOS 如何组合 B1/B2/A2/B10/B3/B4/B9。**DESIGN != LIVE**：不改现有Claims、records、人卡、Memory provider或timer。

## 1. 系统闭环

~~~text
B1 fixed source / revision / material
        ↓
B2 Source Reading Packet
        ↓
Native Agent reading
        ↓
Atomic Claims
        ↓
Current Views
people / projects / topics / responsibilities
        ↓
A2 current-first retrieval
        ↓
B10 Daily Reconciliation
        ↓
human accept / correct / defer
        ↓
B3 / B4 / B9 consume eligible semantic changes
~~~

没有新的 Understanding daemon、Memory daemon 或 Reconciliation daemon。OS/authorized scheduler 只唤醒有限routine。

## 2. Records 仍是当前语义层

`hwz-records/information/understanding` 继续保留四类 View：

~~~text
people/
projects/
topics/
responsibilities/
~~~

这些是 current human-readable views。完整历史属于 raw/source snapshot、Claim关系和receipts，而不是无限追加到 View 正文。

Claim有一个OWNER，可以被多个VIEW引用；View不是第二份事实。

## 3. 多源阅读

当前 IM span 的 fixed-version brief 是已存在实现。目标是让同一个阅读合同支持：

- IM message span；
- Feishu Doc/Wiki exact revision/body blocks；
- Meeting/Minutes/Miaoji selected segments with attribution；
- Web snapshot / Git/Paper captured revision。

扩的是reader/material resolver，不重造semantic writer。

## 4. 每日对账

Daily Reconciliation 的正常首层：

~~~text
自动更新
需要你决定
冲突
理解缺口
~~~

不是：

~~~text
抓到多少消息
处理了多少token
今天所有Claim列表
~~~

重大业务语义——goal、responsible、deadline、external commitment、permission——不能由模型自批。普通事实/有支持推论在standing delegation内可以agent_reconciled。

## 5. MyDocs / 每日对齐

`My Document Library/每日对齐` 是人类投影：

- 重要语义变化摘要；
- 自动更新摘要；
- 真正人的确认/纠正/暂缓；
- 尚未解决冲突；
- capture / understanding lag。

自由正文编辑不是 authorization。人的决定必须通过可归因的B10 acceptance path回到B2。

日报/周报仍属B8，不能用日报替代每日对账。

## 6. Source invalidation 与 Memory

Source edit/retract/permission change 可以立即使旧Claim不再eligible for current recall；这不等于自动宣布旧业务决定被撤销。

A2查询顺序：

~~~text
current Claims / Views
→ current source
→ applicable historical records
→ MetaCC / native Session history
→ optional OpenViking / vector retrieval
~~~

Provider score不是truth。B2更正后local eligibility立即阻止旧Claim，远端provider异步reindex；provider不可用不能复活旧结论。

## 7. 当前实现状态

截至本合同写入前的真实源码观察：

- `understanding_semantic.py` 已有Atomic Claim kinds、major-change边界、OWNER/VIEW、receipt/retraction；
- `claim_recall.py` 已有local recall eligibility；
- `understanding_brief.py` 已能固定IM span，但对document body明确拒绝；
- 人确认已有机械 acceptance 入口；
- 当前主要缺口是 Doc/Wiki/Meeting/Web reader、多源真实集成、Daily Reconciliation product surface、MyDocs每日对齐真实decision path，以及Memory invalidation consumer验收。

这些是 CODE/INTEGRATION gaps，不因本文写入变 LIVE。

## 8. 迁移

现有Bruce/Nature等长篇人卡不批量重写。以后source触碰时逐步：

~~~text
old prose
→ identify still-current meaning
→ commit/update atomic Claims
→ shrink Current View
→ preserve history in raw/receipts
~~~

避免一次大模型重写导致业务认识丢失或漂移。

## 9. 验收

- IM、Doc/Wiki、Meeting、Web各一个真实source走到同一writer；
- 远程控制背景人声不形成Claim；
- 普通事实自动更新，重大变化等人决定；
- source撤回后旧Claim立即不能current recall；
- Provider断开也不会复活stale Claim；
- Daily Reconciliation不展示消息流水；
- 人确认“只是某人的观点”不会改项目正式优先级；
- Current View变短后，历史证据仍可追；
- B3/B4/B9只消费eligible/accepted semantic changes。

## 2026-09-24 差量消费与不强制对话

Reader扩IM、Doc/Wiki、Meeting、Web的固定版本包，继续复用现有semantic writer/receipt/claim recall；不因为多来源新建writer或understandingd。机器出处合法只证明引用位置正确，语义是否支持断言还需实际阅读和反例验收。

Current View只保people/projects/topics/responsibilities；长期历史留source/receipt。普通事实和支持推论按委托agent_reconciled；业务决定通过真实接受。每日首层为自动更新/需要决定/冲突/理解缺口，实际没变化可空，用户不必每天完成三步wizard。

Claim已提交而View、Memory或Board未更新时分别补对应consumer；当前Recall资格先本地纠正，不等远端provider恢复。任何新材料不自动成为Requirement，任何自己写的日报不变成独立支持证据。已授权Focus可继续，未回复不被当作接受新承诺。
