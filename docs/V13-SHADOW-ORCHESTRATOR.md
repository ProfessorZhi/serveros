# V13 Shadow Orchestrator —— 策略 → 原生渲染 → 注入式传输的最小集成层

本文件是**无副作用端到端适配层**的开发记录。**本分支不部署、不接 timer、不发飞书。**

## 0. 一句话

`runtime/card_surface.py`（策略）此前只有纯函数 + `SurfaceStore`，没有把决策变成
卡、也没有把它交出去。`runtime/card_surface_runner.py` 补上这一层：**决策 → 用
`card_period.render_period_card` 渲染已验收的原生 CardKit 2.0 卡 → 交给注入的
sender → 只有 DELIVERED 回执才提交游标**。

## 1. 模拟是什么，真实是什么

| | fake（本轮全部测试） | real |
|---|---|---|
| sender | `RecordingSender`：记录每次调用、**模拟服务的幂等窗口**（同一 key 返回同一 message id）、可配置失败 | **本模块没有**。真实 `message_transport` 适配器是**另行授权**的集成 |
| model | 内存构造 | `local_model_source` 走 `card_source.gather/snapshot` + `fleet_monitor_source.fleet_model` —— **只读**、只读本机、对端设备一律 UNKNOWN |
| instant | 字符串常量 | 由调用方传入 |
| 渲染 | — | **真实** `card_period.render_period_card`（已验收原生 2×2 KPI / div.fields / 3 折叠） |
| 策略 | — | **真实** `card_surface` 的 slot/事件身份/ACK 门禁 |

## 2. 注入点

```
ShadowRunner(store, sender, model_source, period_source, recipient, test_mark)
  tick(at, events, closing, rows)   → 一轮：LIVE + REPORT，产出**脱敏**轨迹
  preview(at, events, closing)      → 只决策+渲染，**不写 store、不调 transport**
```

- `recipient` 在构造时按前缀校验（`user:` / `chat:`）；**裸 id fail-closed**。
- `--preview` 用**内存 store** + `PreviewSender`（调用即抛）：预览既不落盘也不外发。
- **模块内不存在任何调用真实 CLI 的分支**（测试断言源码不含 `LarkMessageSender` /
  `lark-cli` / `subprocess`）。

## 3. 事件适配（read-only，typed）

`alert_digest` 的行 → surface 事件：

| digest kind | surface kind |
|---|---|
| NEW / ESCALATED / RECOVERED | 同名 |
| REOPENED | ESCALATED（复发对读者是升级） |
| UPDATED | HANDLING（episode 内的变化） |
| 其他 | **UNVERIFIED（丢弃）** |

- `episode = alert_digest.episode_of(row)`；`version = sha256(row_version(row))[:16]`
  —— 同一行**重复轮询 → 同一 version（去重）**；severity/state 变化 → **新 version
  （触发 PATCH）**。
- 无 `key`/`alert_ref` 的行 **fail-closed 为 UNVERIFIED**，不触发。

## 4. 一轮做了什么（`tick`）

1. **LIVE**：`decide_live`（slot 心跳 / 事件 / 轮换）→ 渲染 → `stage_live`（只写意图）
   → `sender.send_card`（CREATE/SUCCESSOR）或 `sender.update`（PATCH）→
   **DELIVERED 才 `confirm_live`**；否则保留 pending（下一拍重试**同一条**）。
2. **REPORT**：`decide_report`（含**跨期封存**）→ 渲染归档卡 → CREATE/PATCH 直发、
   FINAL 先 `apply_report(prepared_version=…)` 再 PATCH → `confirm_report` 需
   **DELIVERED + version + remote_id 三重匹配**。

**幂等键跨重试稳定**：CREATE `card-surface-live-create-<rotations>`、
PATCH `card-surface-live-slot-<slot>`、报告 `card-surface-report-<period_ref>`。
失败重试因此**不会变成第二张卡**。

## 5. 本轮发现并修复的两个真缺陷

1. **回执 `remote_id` 读错位置**：`message_transport` 把 id 放在 `provider.remote_id`
   下，而 `card_surface.confirm_live/confirm_report` 只读顶层 —— 一次成功的 CREATE
   会被判成「回执没有指名消息」。新增 `receipt_remote_id()` 同时读两处。
2. **预览把日历读数当声明**：`read_declaration()` 返回的是**读数**（含 `declaration`），
   `period_at` 要的是里面的**声明**；直传会让每天都变 UNKNOWN，静默丢掉时段身份。

## 5.1 崩溃边界：CREATE 是可恢复状态，不是「没有绑定」（2026-10-14 P0 审查）

- **`CREATE_PENDING` 是持久状态**：`stage_live(CREATE)` 写入的是带
  `idempotency_key/recipient/payload_digest/slot/at` 的**意图**，没有 `remote_id`。
  `SurfaceStore._load()` 现在**接受**它（并要求这些字段），而不是拒绝加载；把没有 id
  的 live 读成「无绑定」正是第二轮卡的来源。
- **重启复用同一 key**：`decide_live` 对 `CREATE_PENDING` 返回
  `{action: CREATE, resume: true, idempotency_key: <原 key>}`；超过
  `IDEMPOTENCY_WINDOW_SECONDS=3600`（与 `message_transport` 一致）或时间读不出来 →
  **`NEEDS_RECONCILE`，不发**，并把该相位落盘。**不允许靠清空 store 解决。**
- **未解决的 pending 不被覆盖**：`stage_live` 在已有**不同 key** 的未解决意图时
  **拒绝**；同 key 视为同一次重试。
- **REPORT CREATE 先落 intent 再发送**：`stage_report`（无 id、有 key/digest）→ 发送 →
  `bind_report`（拿到消息 id 才 BOUND）。进程在「已受理、未记 id」之间死掉，重启后按
  **同一 intent** 恢复；超窗则 `NEEDS_RECONCILE`，不补发。
- **REPORT PATCH 不再提前记成功**：`apply_report(PATCH)` 只写 `pending_patch`
  （观察），确认游标 `last_patch_at/archive` **仅在 `ack_report_patch` 收到
  指向该 message 的 DELIVERED 回执时**前进。
- **冻结三重硬门禁**：`confirm_report` 要求**非空** `prepared_version`、回执 `version`
  与之**相等**、且回执 `remote_id`（顶层或 `provider`）**存在并等于**该报告绑定的
  message。**没有身份的回执不能冻结任何报告**，也不再回退到绑定 id。
- **证据来源被标注**：每次提交记录 `evidence: synthetic | remote`。**本模块的 fake
  `DELIVERED` 永远标 `synthetic`**——模拟的「成功」不是飞书受理的证据。

## 5.2 单写者：本进程内成立，跨进程未验证

`ShadowRunner.tick()` 有一个**本进程**的重入守卫：一次 tick 进行中再次调用会抛
`RunnerError`。这是为了不让一个 half-staged 的意图被另一个调用覆盖。

**这不是跨进程互斥。**`_write()` 是 atomic replace，**不是 compare-and-swap**；一个
30 分钟 timer 与一个即时告警**仍然可以交错**并各自读到旧状态。真正串行化属于「拥有
这份工作」的那一层（Work owner 或单进程队列），**本轮未实现、未验证**。

## 6. 真实只读轨迹（`--preview`，本机采样）

```
preview @ 2026-10-14T17:30:00+08:00
  LIVE  : CREATE · ['HEARTBEAT']
  REPORT: CREATE · CN-Mainland/2026-10-14#afternoon
  live   components: [column_set, column_set, div, markdown,
                      collapsible_panel ×3, markdown]  kpi_rows=[2,2] chart=False table=False
  report components: 同上 + 归档 column_set          kpi_rows=[2,2,1] chart=False
  store_touched: False   transport_calls: 0
```

- **真实**：东京本机只读采样（`card_source.gather`）、真机日历声明
  （`~/.serveros/business-calendar.yaml`，`CN-Mainland`，覆盖 2026）、原生渲染器。
- **未外发**：0 次 transport 调用；`store_touched: False`。
- **未验证**：Windows / Dell 一律 UNKNOWN（无采集，不造数）；趋势只有 1 拍 →
  REPORT 标 UNKNOWN 趋势；**chart gate 仍 FALSE**。

## 6.1 可见内容指纹（2026-10-15 最后一项 P0）

`live_sender.card_fingerprint()` 此前只比**结构**（schema / 标题 / tag 序 / 列数），
所以「内存 32%」与远端仍是「内存 29%」的旧卡**比较相等**，会把陈旧卡报成已核实、
推进 LIVE 游标、冻结错误的 REPORT。

现在它折叠**读者真正看得到的文字**，按文档顺序：`markdown.content`、
`div.fields[].text.content`、`plain_text.content`（标题/副标题/面板标题/标签）、
tag 角色、以及有语义的 `color` / `background_style` / `template`。**按名字排除**
被服务端归一化/丢弃的字段（`icon`、整个 `config` 含 `update_multi`、`element_id`、
`margin`/`padding`/`width` 等布局记账）。

`readback()` 另外核对 API 回应的 `message_id` 与请求一致（回应提供时）。

## 6.2 报告历史：**部分接线**（诚实状态）

| 环节 | 状态 |
|---|---|
| 只读采样 → typed Journal（slot 去重、有界、0700/0600）| **已接线**（canary 持锁期间 append）|
| Journal `summary(start,end)` → **CORE REPORT 卡正文**的「时段采样」块 | **已接线**：覆盖率/均值/峰值/峰值时刻；**<2 点写「趋势不足」，不出曲线** |
| 生产 alert detector → `--events` | **未接通**：`--events` 目前只接受 CLI 人工传入的 JSON，**没有自动读取生产事件源** |
| VChart 曲线 | **未开**（`charts_verified=False`）|

**因此不得声称「告警即时更新已上线」或「时段均峰已出现在卡上并已在生产运行」** ——
前者的事件源未接线，后者的展示**代码已接但从未在真实卡上跑过**。

## 7. 未验证 / BLOCKED（不得声称已上线）

- **CLI/daemon/真实 timer 未接**：没有 `serverosctl surface …` 命令面，没有 30 分钟
  service，没有 alert detector 进程；`ShadowRunner` 只被测试与 `--preview` 调用。
- **真实 sender 未接**：真实 `message_transport` 适配器需**单独授权**；本轮所有回执
  都是 fake。
- **真实飞书 / readback 未做**；`+messages-mget` 校验留待接线轮。
- **生产旧链路未变**：`tokyo-status-card.timer`（hourly + notice）与独立 alert 路径
  仍在跑；`monitoring-policy.yaml` 未改。
- **OpenAgents / Fleet 插件 / Windows / Dell 部署**不在本轮。
