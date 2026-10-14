# V13 时段卡生命周期（issue #170 · B 线增量）

本文件记录这一支分支（`feat/v13-period-card-20261014`，基线 PR #181 的
`feat/fleet-v13-uid-directlark-20261014@4535dca`）做了什么、依据什么、以及
**明确没有**做什么。它是开发记录，不是发布说明：本分支不部署、不打开定时器、
不发真实飞书消息、不改应用1的生产播报。

## 0. 与既有 §10 / §11 的关系（这一节的裁决来自 2026-10-14）

旧权威 `docs/MONITORING-SCOPE-RECOVERY-CARDS.md` §10 写：

> 常规资源/状态播报只在 WORKDAY 的 08:00–20:00（含两端）更新……20:00 之后、
> 08:00 之前、NON_WORKING_DAY 都不做常规 Broadcast。

本分支持有的裁决是：**§10 约束的是「常规主动播报」这一动作**，它
**不约束**资源采样，也**不约束**对一张已经存在的卡做**静默原位 PATCH**。
于是：

- 监控面**全天候**保留完整 V13 LIVE 监控卡（`card_day` 的 08:00–20:00 整点
  播报规则**不变**，它仍然管常规播报那一条路径）；
- 一天里变化的是**承载它的那条消息**，不是监控本身；
- 这一裁定**没有改动** §10/§11 正文——本分支只新增了一份并行的时段卡文档，
  权威文档的改写是另一个决策，本分支不代为做出。

## 1. 时段模型：是声明，不是推导

`runtime/card_period.py` 的时段表是**声明**（沿用
`runtime/recipe-contract.yaml` §7 对 recipe 的 period 的规则：
«a loop that picks its own window writes a record about a period nobody asked
for»）。默认表：

| 日种 | 时段 | 标签 | 起–止（本地时区） |
|---|---|---|---|
| WORKDAY | morning | 上午 | 09:00–12:00 |
| WORKDAY | afternoon | 下午 | 12:00–18:00 |
| WORKDAY | night | 夜间 | 18:00–**次日** 09:00 |
| NON_WORKING_DAY（节假日） | all_day | 全天 | 00:00–24:00 |

- **日种来自 `business_calendar.day`**，本模块不自行推导日历。`kind` 为
  `UNKNOWN`（未导入/未覆盖该年）时**不给时段、不建卡**，与
  `routine_window` 的 `calendar=UNKNOWN` 默认一致——「实际假日日历不可猜」。
- **跨夜段属于它开启的那一天**。02:00 落到的是**前一天**的 `#night`，
  `period_ref` 因而写成 `CN-Test/2026-10-14#night` 而不是 15 日——这是「每个
  午夜不新开一张卡」的全部依据（`_contains(..., tail=)`）。
- **时段表可整体替换**：`{"tick_seconds": int, "workday": [...],
  "non_working_day": [...]}`，每张列表**整表替换**而非逐条合并；`tick_seconds`
  默认 600（10 分钟）。
- 表错即拒：`tick_seconds ≤ 0`、缺 `name`、`start/end` 不是 `"HH:MM"`，都
  `raise CardPeriodError`，不静默兜底。

## 2. 生命周期：一时段一条消息

`decide()` 返回一个 **steps 列表**——因为「换班」是**两个**操作，把它当成一个是
卡会增殖的地方：

1. **CREATE** 时段首拍：新建一条消息。`message_verdict` 答 `NEW_MESSAGE`，且
   模块**明确不声称它是静默的**——裁决原文「新时段卡初建可能有一次可见消息，
   不可声称静默创建」。
2. **PATCH** 时段内后续每一拍：**静默** patch 同一 `message_id`。patch 不触发
   新通知（这是 `monitoring_policy` 立身的那条实测事实），所以「夜间不频繁推送」
   不是特例，它就是 patch 本身。
3. **FINAL** 时段结束：对这**同一条消息**做最后一次 patch，写入归档
   （见 §4），随后**冻结**。边界拍会一次给出 `[FINAL(旧), CREATE(新)]` 两步，
   顺序固定——归档必须在旧时段的行还是旧的时被写。
4. **收班（`closing=`）**：调用方也可显式结束一个时段，而不等下一时段的首拍来
   探测边界。

**幂等 / 防重复消息**：

- 去重键是 `period_ref`（`<day_ref>#<name>`）。重放同一拍只会 **PATCH 同一条**
  消息，绝不新发；`apply` 对同一 `period_ref` 的第二次 CREATE 直接拒绝。
- **方向被检查而非假定**：一个落在**不同**时段的即时拍，只有在它**晚于**当前
  打开时段的开始时刻时才算边界（`_is_later`）。更早的一拍 = 时钟回拨或重放，
  答 `OUT_OF_ORDER` 并拒绝动作，避免「把时钟倒退读成换班」。
- 已冻结的时段再被同一 `period_ref` 拍到，答 `ALREADY_FROZEN`，不动卡。
- 传输侧重试走 `message_transport.LarkMessageSender.reconcile`（幂等键去重），
  本模块不实现第二套重试。
- 不可读的 store **拒绝而非重建**：重建会让同一时段拿到第二条 NEW 消息。

## 3. 复用了什么（不新写第二套）

| 需要 | 复用于 |
|---|---|
| 版本、回执、14 天可改窗口、`freeze_card`、`binding_verdict` | `runtime/card_delivery.py`（**新增一个 `period` kind**，仍是同一份账本、同一个 DB） |
| 原位 PATCH（`im messages patch`）、reconcile | `runtime/message_transport.py` |
| compact row 词表、`timeline`、`ROW_SECTIONS` | `runtime/card_day.py` |
| V13 三设备卡（含折叠面板、per-UID 账户块、chart 门） | `runtime/fleet_monitor_card.py`（FINAL 卡 = 它 + 上方一段归档） |
| 日身份与权威 | `runtime/business_calendar.py` |

`card_delivery.KINDS` 由五词扩为六词（`+period`）；扩词的依据写在
`card_delivery.py` 的注释里：两词共用一个名字会让后来的 patch 打错消息，
而**当天卡**与**班次卡**是同一台机器的两条不同消息。

## 4. FINAL 归档：四块

`card_period.archive()` 产出、`render_archive()` 渲染：

1. **总览概括** — 设备数、需关注项数、状态变化处数、未观测段数；
2. **完整时段趋势一览** — 本时段全部 row 经 `card_day.timeline` 折叠成区间
   （走稳/变化/未观测各自成条，不把「没人看过」压成「无变化」）；
3. **三设备摘要** — 逐设备的可达性与关键读数（来自 fleet 模型）；
4. **交接：事故 / 自愈 / 未解决** — 由调用方传入的时段事件账本渲染；本模块
   不读事件账本。

渲染**不复制 V13 布局**：FINAL 卡是 `fleet_monitor_card.render_cardkit` 的输出
被抬起，归档写在它上方。同一三设备的第二个渲染器会是与它分歧的第二个布局权威。

## 5. 明确没做 / 不声称

- **不部署、不启用定时器、不发真实飞书、不读生产 token**；不动 `ops/`、
  不动应用1的生产播报。
- **不声称图表已投产**：`render_archive(charts_verified=True)` 直接拒绝——
  VChart 元素与 390px 窄屏 `column_set` 都**未在真机验收**，默认卡是文本卡。
- **不声称初始创建是静默的**：见 §2.1。
- **不声称 §10 已被改写**：见 §0。
- 未动 PR #160 / #161 / #172 / #180 各自的工作树；`card_day` 的既有行为未改。

## 6. 证据

- `tests/test_card_period.py`（新，21 用例）：时段切分（含跨夜回指前一天、
  节假日全天、UNKNOWN 不给段）、10 分钟 tick 序号、首拍 CREATE / 后续 PATCH
  同一消息、边界两步 `[FINAL, CREATE]` 与冻结、收班后 `ALREADY_FROZEN`、
  重放不新发、陈旧拍 `OUT_OF_ORDER`、store 不可读即拒、announce 的
  BOUND/UNCHANGED/REBOUND、row 闭集拒绝机器细节、归档四块、`update_multi`
  契约、无 chart 元素、表可替换与表错即拒。
- `tests/test_card_delivery.py`（改）：`KINDS` 断言补 `period`。
- 相邻套件回归：`test_card_delivery` / `test_card_day` / `test_business_calendar`
  / `test_message_transport` / `test_fleet_monitor_card` / `test_fleet_monitor_uid`
  / `test_card_period` 合计 **372 passed**（`ulimit -v 8GiB`、`timeout`）。
- 运行方式见 `memory/serveros-pytest-invocation-rule`：显式 `tests/test_*.py`
  + `timeout` + `ulimit`，不裸跑。

## 7. 未验证项（明确 UNKNOWN）

- **无真实飞书收发**：全部 fake / 确定性时钟 / `model_from_fixture()`；没有
  真机上的 CREATE/PATCH/FINAL 往返。
- **无真机渲染**：CardKit 2.0 的 `column_set` 与 VChart 未在手机窄屏验收。
- **未接生产定时器**：本模块还没有被任何 `ops/` timer 调用；把它接进
  `serverosctl` 与生产 timer 是**另一个**决策，需要先过 §CANARY 门禁（见
  `docs/NATIVE-CORE-V13-SEAM-AND-CANARY-GATE.md`）。
- **未接 C++ Core**：时段卡的采样目前来自 Python 只读 adapter；C++ 采集器的
  `native-node-measurement.v1` 接缝见同文档 §2。
