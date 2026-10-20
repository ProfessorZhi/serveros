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
3. **FINAL = PREPARE（准备），不是冻结。** 时段结束（边界拍或收班）时，本模块
   把这**同一条消息**的最终归档准备好、算出将要发出的版本，把时段置为
   **`FINAL_PENDING`**，**然后等**。边界拍**只给一步 `[FINAL]`**——继任时段要等
   归档被远端确认之后才开（见 §2.1）。
4. **收班（`closing=`）**：调用方也可显式结束一个时段，而不等下一时段的首拍来
   探测边界。

### 2.1 回执门禁：先确认，再冻结，再换班（2026-10-14 review）

`schema` 里的 `open.phase` 三态，**这是本模块最要紧的字段**：

| phase | 含义 | `frozen` |
|---|---|---|
| `LIVE` | 时段进行中，拍子静默 patch | `None` |
| `FINAL_PENDING` | 归档**已准备并已尝试发送**，但**没有**任何经核验的回执 | `None` |
| `FROZEN` | 已记录一条**DELIVERED** 的 `card_delivery` 回执（版本匹配） | 已设 |

- **`apply(FINAL)` 不写 `frozen`。** 只有 `PeriodStore.confirm(receipt=…)` 会在
  回执 `state == DELIVERED` **且版本与准备好的版本一致**时提交冻结。FAILED /
  UNSUPPORTED / UNKNOWN / 版本不符 / 无 message id，一律 `raise`，时段留在
  `FINAL_PENDING`。
- **`FINAL_PENDING` 独占每一拍**：同一时段的后续拍、以及**下一时段的首拍**，都只
  **重试同一条 `message_id`**（`reason=FINAL_UNCONFIRMED`），绝不新发、绝不开新
  时段。`apply(CREATE)` 在写层再挡一次：前一时段非 `FROZEN` 即拒绝。
- **重启对账**：`PeriodStore.reconcile_pending(ledger=…)` 拿**既有**
  `card_delivery` 台账（`kind="period"`，按版本匹配）判断归档是否落地 —— 意图在
  `open.pending`，回执在 `card_delivery`，**不建第二套 delivery 账本**。
- `deploy_readiness()` 恒返回 `{"ready": False}`：见 §7。

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
- **`FINAL_PENDING` 且无准备版本的 store 在加载时即拒**：没有版本，重启后无法向
  台账对账，唯一出路就是第二条消息。

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

## 4.1 UI 修订（2026-10-14，按用户真机截图反馈）

用户看到首版灰度归档卡的截图后评价「有点丑」，并逐条指出问题。改动**全部收敛在
`runtime/card_period.py` 的展示层**（新增 `_reach_*` / `_kpi_lines` / `_swap_warning`
/ `_alert_lines` / `_gap_lines` / `_fold_*` / `_coverage_line` / `_service_line`
与统一入口 `render_period_card`），**未改 #181 的 `fleet_monitor_card.py`**：

| 截图问题 | 根因 | 现状 |
|---|---|---|
| 标题整片红 + 写开发身份 `【…TEST/GRAY】…gray/2026-10-14#demo 已冻结` | header 继承舰队卡模板；且 `_attention` 把「未获取采样」的设备判为 **CRITICAL** | 标题 `ServerOS · 设备运行概览`；`[灰度测试]`/时段身份/`时段总结` 降入**小号副标题**；配色**中性蓝**，**只有 `_real_critical`（真实已读的 CRITICAL 读数或服务）才红** |
| `三设备摘要` 直接渲染 Python dict：`{'state':'KNOWN','reason':None,...}` | 展示层把 `reachability` **整个映射**交给 formatter | 新增**类型化** `_reach_state` / `_reach_note`（三词：有新鲜读数 / 可达性过期 / 未获取采样·状态未知）；`_device_summary` 只返回 `reach` + `reach_word` **字符串**；**映射无任何路径进入正文** |
| 正文乱序、首屏是趋势再是长设备列表、重复 | 归档块拼在舰队卡**上方**再叠整卡 | 单一 `render_period_card` 重建首屏：①覆盖 ②KPI ③需要关注+数据缺口 ④三设备短摘要+折叠（保留完整 V13 内容）⑤归档+交接 |
| Windows/Dell 被称「失联」 | `_attention`/`_device_title` 用「失联」 | 一律 **`未获取采样 · 状态未知`**（连接器可达 ≠ 资源监测已接入） |
| `服务共0` 可能其实是未采集 | 摘要直接印计数 | `total<=0` → **`服务：未采集`**，不印 `共 0` |
| 1 拍却写「完整时段趋势一览」/「0 状态变化=无故障」 | 固定文案 | ticks<2 → **`时段趋势（仅 N 次采样，不足以评价趋势）`**；无事件写 **`本时段未记录事故`**（不写「无事故」） |
| 时间显示长 ISO | header 直接印 ISO | `_local_hhmm` → 本地 **`17:xx`** |
| swap 逼近阈值无提示 | 无 | `_swap_warning` 给一条紧凑线（≥89%，不染红） |

**灰度卡与正式产品 UI 的差距（明确）**：本轮卡是**灰度测试卡**，副标题显式带
`[灰度测试]`、页脚写「灰度测试，非正式播报」；正式产品的标题/页脚措辞、是否保留该
标记、以及**真机窄屏（390px）观感**均**未经验收**——`charts_verified=False` 保持关闭，
`column_set` rich 模式未使用，卡片只用 `markdown`/`hr`/`collapsible_panel`。
真机效果须由用户再截图确认。

## 4.2 原生 CardKit 2.0 仪表盘（2026-10-14，第二轮 UI 反馈）

用户对 17:55 的截图仍不满意，要求「用飞书卡片自己的构造」做。已按**本地 lark-cli
实际内置契约**（`lark-cli skills read lark-im/references/card/…`，v1.0.96）把整卡从
「大段 markdown」改为**原生组件**：

| 位置 | 原生组件 | 依据 |
|---|---|---|
| Header | `title` / `subtitle` / `template`(blue) / `icon`(standard_icon `setting_outlined`) / `text_tag_list`(`1/3 已采集`·blue、`2 待接采集`·neutral) | `components/header.md` |
| 首屏 KPI | **`column_set`(flex_mode:none) ×2**，各 **2 个 `column`**，`background_style` grey-50 / yellow-50（swap≥89%），`padding:12px`，`vertical_spacing:2px`；大数字 `##` + 灰色 `notation` 标签 | `components/column_set.md`、style guide §3 |
| 覆盖状态 | 原生 **`div.fields`**（`is_short` label/value 对） | `components/div.md`、style guide §7 |
| 设备正文 | 3 个原生 **`collapsible_panel`**（东京内嵌 2 列一组读数 `column_set` + 服务行；Windows/Dell 仅缺口） | `components/collapsible_panel.md` |
| FINAL 小结 | 轻背景（grey-50）单位 `column_set`，位于首屏与折叠之间 | style guide §5 |
| 页脚 | `markdown` notation | — |

**明确拒绝的字段/组件**（不照抄 style guide 的错误示例）：

- **`column.corner_radius`** —— `components/column_set.md` 的 `column` 字段表**没有**它，
  且它会触发 Card 2.0 `200621 unknown property`（larksuite/cli issue #2371，其
  style-guide 示例恰好带了这个错字段）。本卡 `column` 只用已证实字段
  `background_style` / `padding` / `vertical_spacing` / `width` / `weight`。
- `interactive_container` —— 声明后 `behaviors` 必填，本卡无交互，**不做装饰块**。
- `chart` / `table` —— 未真机验证，**gated off**；1 次采样不画曲线。
- `stretch` —— 慎用；KPI 用 `none` + `weighted`（手机按比例压缩、不截值）。

**本卡实测结构**（真实东京数据，48/200 元素、3 个唯一 element_id、6780 字节）：
`column_set×2(KPI) → markdown(warning) → div(fields) → markdown(gap) → collapsible_panel×3 → markdown(footer)`。

**本地静态有效 ≠ 真机可显示**：`_validate_native` 只校验 schema/tag 词表/element_id/
200 元素/30KB；**客户端是否真渲染 `column_set` 背景、`div.fields`、`text_tag_list`
须由用户桌面/手机截图确认**。

## 4.3 下一代原生模块：设计决策（2026-10-14，桌面截图认可后）

用户已认可 18:32 的原生 2×2 KPI 分栏（「这样漂亮」）。本轮**保持该基线**，只做 P0
精修与组件化收敛。以下是**可验收的组件使用合同**，供下一轮灰度前确认：

| 组件 | 允许的条件 | 不允许 |
|---|---|---|
| `column_set` + `column` | KPI 分栏（`flex_mode:none` + `weighted`，浅底色 `grey-50`/`yellow-50`） | 固定 px 宽、`stretch` 拉伸、`column.corner_radius`（非法，见 §4.2）|
| `div.fields` | label/value 对（设备覆盖） | 用 `column_set` 模拟字段对 |
| `collapsible_panel` | 设备详情的收纳（折叠内可放 `column_set`） | 折叠里放 `table`；嵌套 > 5 层 |
| **`chart`（VChart）** | **仅当有真实带时间戳的时序样本（≥2 拍，含来源/缺口量）**；`chart_spec` 为纯 JSON；移动端**需真机验收后**才开门 | 单拍画曲线；用 `rainbow` 配色；把缺口补成 0 或复制末次值 |
| **`table`** | 只放 **Card2.0 body 根**（不可嵌折叠） | 用于 302px 主卡；宽表硬塞 |
| **`input` / `button`** | **仅当存在真实授权的 callback**（有可核的交互语义） | 无回调的装饰按钮；未授权的控制面 |
| **`interactive_container`** | 必须带**合法 `behaviors`** 且确有交互 | 纯装饰块 |
| 彩色 header / 关键警告 | 语义一致：红**只**代表**真实确认的 CRITICAL**（`_real_critical`）；`yellow/orange` 代表接近阈值的提示 | 用 UNKNOWN/未采集触发红色；用彩色表达装饰 |

**语义边界（不改）**：UNKNOWN（未获取采样）**不等于离线/失联**；「服务未采集」
**不等于 0 个服务**；缺失读数**不补 0、不复制末次值**。

**本轮保持不变的基线**：2×2 浅色原生 KPI 分栏、header（icon + title/subtitle +
`text_tag_list`）、`div.fields` 覆盖、3 个原生 `collapsible_panel`、零
`corner_radius` / `chart` / `table` / `interactive_container`。

**KPI 现在是类型化的**：caption 由 `_KPI_AXES` 的类型表决定，不抄读数 label；
`CPU逻辑核`（计数）与 `CPU 使用率`（百分比）按值的单位区分，读数与轴单位**矛盾则
该格从网格丢弃**（原始读数仍留在折叠中）——「CPU 128」被误读成利用率的缺陷由此根治。

**下一轮图表的真实采样缺口**：目前每个时段只有**单拍**样本，且 `card_period` 的
row 只保留 `verdict/observation/sections/gaps` 的**状态词**，**不含带时间戳的
CPU/RAM/GPU/Swap/网络数值序列**。要打开 `chart` 必须先有：(a) 采集侧提供带
`observed_at` 的数值时序；(b) 每时段 ≥2 拍；(c) 真机验收移动端 VChart。

## 5. 明确没做 / 不声称

- **不部署、不启用定时器、不发真实飞书、不读生产 token**；不动 `ops/`、
  不动应用1的生产播报。
- **不声称图表已投产**：`render_archive(charts_verified=True)` 直接拒绝——
  VChart 元素与 390px 窄屏 `column_set` 都**未在真机验收**，默认卡是文本卡。
- **不声称初始创建是静默的**：见 §2.1。
- **不声称可上线**：`card_period.deploy_readiness()` 恒返回 `{"ready": False}`
  并列出未验证项——见 §7。`PeriodStore` 是**计划器 + 本地状态机**，它**不**
  单独证明「时段已在远端归档」。
- **不声称 §10 已被改写**：见 §0。
- 未动 PR #160 / #161 / #172 / #180 各自的工作树；`card_day` 的既有行为未改；
  **不创建平行消息账本**（`period` kind 落在既有 `card_delivery` 台账）。

## 6. 证据

- `tests/test_card_period.py`（新，31 用例 + 6 subtests）：
  - 时段切分（跨夜回指前一天、节假日全天、UNKNOWN 不给段）、10 分钟 tick 序号；
  - 首拍 CREATE / 后续 PATCH 同一消息、重放不新发、陈旧拍 `OUT_OF_ORDER`；
  - **回执门禁（`TheRemoteAckGate`）**：FAILED / UNSUPPORTED / UNKNOWN 回执
    绝不冻结；版本不符 / 未准备先确认均拒；重试**复用同一 `message_id`**；
    重启 `reconcile_pending` 对**既有 `card_delivery` 台账**判 RETRY→CONFIRMED
    （并断言该台账只有 2 行，本模块没写第二条账）；写层拒绝在 `FINAL_PENDING`
    之上开继任；更晚的下一时段拍仍只重试（换班顺序）；`FINAL_PENDING` 且无准备
    版本的 store 加载即拒；`deploy_readiness()` 非 ready；
  - 归档四块、`update_multi` 契约、**无 chart 元素**、表可替换与表错即拒。
- `tests/test_card_delivery.py`（改）：`KINDS` 断言补 `period`。
- 相邻套件回归：`test_card_period` / `test_card_delivery` / `test_card_day` /
  `test_business_calendar` / `test_message_transport` / `test_fleet_monitor_card`
  / `test_fleet_monitor_uid` / `test_architecture_check` /
  `test_declared_implementations` = **432 passed**（内核限额：
  `systemd-run --user --scope -p MemoryMax=8G -p MemorySwapMax=0 -p CPUQuota=200%`）。
- 运行方式见 `memory/serveros-pytest-invocation-rule`：显式 `tests/test_*.py`
  + `timeout` + `ulimit`/cgroup，不裸跑。

## 7. 未验证项（**精确范围**：remote proof 仍 UNKNOWN）

**这一节是 review 明确要求的「不能宣称可上线」的准确边界。**

- **远端 ACK 未经真机验证**（**唯一**的冻结依据是一件未经往返核验的回执）：本模块
  只接受 `state == DELIVERED` 的回执来提交冻结，而该回执**只**来自
  `message_transport` 对 `lark-cli im messages patch` 自述的转述，
  **没有任何独立的 readback** 去核对它。因此：`confirm()` 的**门禁逻辑**是
  经过测试的（失败/超时/版本不符都不冻结），但「这件回执是否真的等于飞书接受了
  这一版」**仍是 UNKNOWN**。这正是 `deploy_readiness()` 返回 `ready: False` 的
  原因，也是本模块**不被登记为可上线**的原因。
- **无真实飞书收发**：全部 fake / 确定性时钟 / `model_from_fixture()`；没有真机上的
  CREATE / PATCH / FINAL 往返，也没有真实的 429 / 超时 / 断网重试。
- **重启对账只用离线台账**：`reconcile_pending` 的测试喂的是**构造出的**
  `card_delivery` 台账，不是一次真实崩溃后由真 daemon 写下的台账。
- **无真机渲染**：CardKit 2.0 的 `column_set` 与 VChart 未在手机窄屏验收。
- **未接生产定时器 / 未接 C++ Core**：把本模块接进 `serverosctl` 与生产 timer、
  或接上 C++ 采集器的 `native-node-measurement.v1` 接缝（见
  `docs/NATIVE-CORE-V13-SEAM-AND-CANARY-GATE.md`），是**另一个**决策，且必须先过
  该文档的 canary 门禁。
