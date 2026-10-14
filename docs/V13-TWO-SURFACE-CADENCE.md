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
surface.live_decision(now, binding, events, last_patch_at, slot_seconds=1800)
  → {action: CREATE|PATCH|SUCCESSOR|SKIP, reasons: [EVENT|HEARTBEAT|ROTATION], coalesced}
```

- **心跳**：`heartbeat_due(now, last_patch_at, 1800)`。采样频率 ≠ 卡片刷新频率：
  采样可以更密（供趋势/告警检测，落在既有受管记录），卡片只按 30 分钟刷一次。
- **事件即时**：`event_kinds(events)` 取 `NEW / ESCALATED / RECOVERED / HANDLING`，
  **去重**、忽略未知词；命中即 PATCH，不等 slot。
- **合并一次写**：心跳与事件同一拍到达 → **一个 PATCH**，`reasons` 同时给出两者，
  `coalesced=True`。
- **可改性不由本模块判**：`card_delivery.binding_verdict` 唯一裁决；`IDENTITY_UNDATED`
  → 不 PATCH（窗口不可判），`IDENTITY_EXPIRED` → **SUCCESSOR**（按台账有据轮换，
  旧 id 记进 `previous_remote_id`），`IDENTITY_MISSING` → CREATE。**没有「到期就
  FINAL/冻结」这一步**——LIVE 不因报告周期结束而归档。

### 2.2 REPORT（每时段一条独立 message）

```
surface.report_decision(period, report, closing)
  → {action: CREATE|PATCH|FINAL|SKIP}
```

- 一个 `period_ref` 一条报告消息；**与 LIVE 的 binding 是两个字段**，代码里没有任何
  从 `apply_report` 到 `live` 的路径。
- 相位复用 `card_period` 的三词：`LIVE → FINAL_PENDING → FROZEN`；**`FINAL` 只
  PREPARE**，冻结只由 `confirm_report(receipt)` 在 `state == DELIVERED` 时提交。
- 失败/超时/未知 → 留在 `FINAL_PENDING`，**重试同一条 message**；**只阻塞这份报告**，
  LIVE 照常心跳。

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
- **接缝未接线**：`SurfaceStore` 尚未被任何 CLI/daemon 调用；把它接进 `serverosctl`
  与生产 timer 是**另一个**决策，且需要先过 canary 门禁。
