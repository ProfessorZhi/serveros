# V13 两张卡：LIVE 与 CORE REPORT 的接缝与迁移

本文件是 2026-10-14 产品裁决（Issue #170 / PR #193 comment）的**实现接缝与迁移
设计**。它是开发记录，不是发布说明：**本分支不部署、不改生产 timer、不发飞书。**

## 0. 裁决摘要（一句话）

**只有两种卡片类型**：① 一张长期存在的 **LIVE「实时监控 + 告警」卡**（同一
`message_id`，30 分钟心跳 PATCH，告警事件即时 PATCH）；② **独立的
「ServerOS Core REPORT」卡**（按时段生成，与 LIVE **绝不共用 `message_id`**）。
**ALERT 是 LIVE 卡里的一个区域，不是第三张卡。**

## 1. 现有实现盘点（谁拥有什么，不重复造）

| 关注点 | 唯一 owner | 事实 |
|---|---|---|
| 投递台账 / 版本 / 回执 | `runtime/card_delivery.py` | kinds = `status / alert / recovery / notice / fault / period`；`KEEP=20`（按 kind） |
| **14 天可改窗口** | `runtime/message_transport.py::CARD_UPDATE_WINDOW_DAYS = 14` | `card_delivery.update_window_days()` 读它；`binding_verdict()` 判「这条还能不能改」 |
| 传输（send / patch / reconcile） | `message_transport.LarkMessageSender` | `send_card()` / `update()` / `reconcile()`；幂等键窗口 3600s；内容 ≤30KB |
| 一天一卡的**生产**路径 | `runtime/card_day.py::DayStore` + `monitoring-policy.yaml` | `cadence: hourly`、`persistent_card: one-per-workday`、`on_patch: lightweight-notice`。**这是现网正在跑的旧卡，本分支不动它。** |
| 时段身份 | `runtime/business_calendar.py` + `card_period.period_at` | 工作日 早/午/夜；节假日全天；`calendar=UNKNOWN` 不猜 |
| 原生 CardKit 渲染 | `runtime/card_period.py::render_period_card` | 已验收的 2×2 KPI / div.fields / 3 折叠 / 类型化 KPI |

**结论**：新增的 `runtime/card_surface.py` **只做投递策略**，复用上表每一行；它
**不新建 transport、不新建 ledger、不替换生产 hourly 规则**。

## 2. 两张卡各自的接缝

### 2.1 LIVE（唯一一条 message）

```
surface.live_decision(now, binding, events, live_state, slot_seconds=1800)
  → {action: CREATE|PATCH|SUCCESSOR|SKIP, reasons:[EVENT|HEARTBEAT|ROTATION],
     events:[event key…], unverified_events:n, slot, coalesced}
```

- **心跳是 SLOT，不是「距上次 PATCH 多久」**：`regular_slot_id(at) = utc_epoch // 1800`。
  「是否到点」= 当前 slot > `last_delivered_regular_slot`。这样 **17:29 的告警
  不会抹掉 17:30 的正规心跳**（旧实现用 `now - last_patch_at >= 1800` 就会被抹掉）。
  本地 `:00/:30` 是渲染层的事；`+08:00` 这类 30 分钟整数倍偏移与本地半点对齐。
- **事件即时**：身份 = **`episode|event_id @ version`**（`event_key`）。同一 episode
  重放**不算新事件**；**同 kind 不同 episode 算两个事件**；**无身份的事件记 UNVERIFIED
  且不触发**（不制造刷屏）。命中即 PATCH，不等 slot。
- **合并一次写**：心跳与事件同一 slot → **一个 PATCH**，`reasons` 两者都给，
  `coalesced=True`。
- **游标只在 DELIVERED 回执后前进**：`stage_live()` 只记**意图**（slot / 事件键 /
  目标 message），`confirm_live(receipt)` 才推进；`FAILED/UNSUPPORTED/UNKNOWN` 或
  **回执指向别的消息**一律拒绝，意图留在 pending → 下一拍**重试同一条 message**。
- **可改性不由本模块判**：`card_delivery.binding_verdict` 唯一裁决；`IDENTITY_UNDATED`
  → 不 PATCH，`IDENTITY_EXPIRED` → **SUCCESSOR**（按台账有据轮换，旧 id 记进
  `previous_remote_id`，**游标随之继承**），`IDENTITY_MISSING` → CREATE。
  **没有「到期就 FINAL/冻结」这一步**——LIVE 不因报告周期结束而归档。

### 2.2 REPORT（每时段一条独立 message）

```
surface.report_decision(period, reports, closing)
  → {action: CREATE|PATCH|FINAL|SKIP, period_ref}
```

- 一个 `period_ref` 一条报告消息；**与 LIVE 的 binding 是两个字段**，代码里没有任何
  从 `apply_report` 到 `live` 的路径。
- **封存顺序**：① 先处理 `FINAL_PENDING`（重试同一条）；② 再封存**任何**`period_ref`
  已不等于当前时段、仍处 LIVE 相位的报告——**包括当前根本没有时段身份时**
  （`in_period: false`，如跨夜/换班瞬间），否则跨班报告会被搁死；③ 才是当前时段的
  CREATE/PATCH。
- 相位复用 `card_period` 的三词：`LIVE → FINAL_PENDING → FROZEN`；**`FINAL` 只
  PREPARE**，冻结只由 `confirm_report(receipt)` 提交，且**三重门禁**：
  `state == DELIVERED` **且** 回执 `version` 等于该报告准备好的 `prepared_version`
  **且** 回执 `remote_id` 等于该报告绑定的 message。**别的卡（含 LIVE）的 DELIVERED
  回执不能冻结这份报告。**
- 失败/超时/未知 → 留在 `FINAL_PENDING`，**重试同一条 message**；**只阻塞这份报告**，
  LIVE 照常心跳。
- **热存储有界**：`REPORT_HOT_CAP=64`；超出的**已冻结**报告移入紧凑 `retired` 索引
  （`period_ref/remote_id/frozen_at`），**不删除历史**；**未冻结/待确认的报告永不淘汰**。

## 3. 迁移（从 #193 的「一时段一卡」到「一条 LIVE + 每时段 REPORT」）

| #193 现状 | 本分支 |
|---|---|
| 每时段 `CREATE` 一张卡 | LIVE **只在无 binding 时 CREATE 一次**；时段变化**不再产生新消息** |
| 时段结束 `FINAL` + `FROZEN`（同一消息冻结） | **LIVE 永不 FINAL**；`FINAL/FROZEN` 只属于 REPORT |
| 换班 = 旧卡冻结 + 新卡 CREATE | 换班 = **REPORT 的一条新消息**；LIVE 不变 |
| `card_period.PeriodStore.advance` 一个状态机 | `SurfaceStore`：`live` + `reports` 两个独立入口 |

**保留不动**：`card_delivery` 的 ACK/readback/fail-closed 纪律、`message_transport`
的 14 天与幂等、`card_period` 的原生渲染器与其 `FINAL_PENDING` 语义（REPORT 复用同一
套词与门禁）、`card_day` + `monitoring-policy.yaml` 的**现网旧卡路径**（旧 incident
card 的生产路径**不删除**）。

## 4. 兼容与上线边界

- **现网未变**：`tokyo-status-card.timer`（hourly + notice）与独立 alert 路径**仍在跑**；
  `monitoring-policy.yaml` 的 `hourly` / `one-per-workday` **本分支未改**。新策略只能
  在**单独批准**后替代它——改产品策略需要显式版本升级或新独立 policy。
- **本轮未授权**：真实部署、启停 timer、创建新飞书卡、改持久化数据库、C++ 自愈、
  账号/应用权限。**不发任何飞书消息。**
- **通知与静默**：飞书 PATCH 通常**静默**。「异常写进 LIVE 卡」不保证手机收到提醒。
  若严重告警需要主动提醒，只能另评审一条**短纯文字通知（非卡片）+ 消息跳转**，
  需清晰的通知策略/去重/明确同意——**本轮不自动打开，也不把它算作第三张卡。**

## 5. 未验证 / GAP

- **真机未验**：本分支产物是**纯函数 + 本地 store**，未接 `serverosctl`、未接任何
  timer、未发飞书；真机观感仍以先前 2×2 KPI 原生卡为样式锚点，无新 UI 变更。
- **chart 仍关**：需要真实带时间戳的连续采样（≥2 拍）+ 移动端 VChart 验收；当前
  `card_period` 的 row 只存状态词，**没有数值时序**。
- **接缝未接线（BLOCKED，需 follow-up）**：`SurfaceStore` 尚未被任何 CLI/daemon
  调用。接线需要：① 一个 `serverosctl surface …` 命令面；② 一个 30 分钟 timer 与
  事件源（alert detector）的**合并调用点**；③ 采样侧提供**带时间戳**的数值序列；
  ④ 生产切换与 canary 门禁——**均不在本轮授权**。
- **事件源格式未定**：`event_key` 假定事件携带 `episode|event_id` 与 `version`；
  真实 alert detector 的字段名需在接线时对齐（本模块对**无身份**的事件已 fail-closed）。
- **真实 ACK/readback 未做**：门禁用 **mock receipt** 验证（DELIVERED/FAILED/UNKNOWN/
  版本不符/别的卡），真实飞书回执与 `+messages-mget` readback 留待接线轮。
