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

## 7. 未验证 / BLOCKED（不得声称已上线）

- **CLI/daemon/真实 timer 未接**：没有 `serverosctl surface …` 命令面，没有 30 分钟
  service，没有 alert detector 进程；`ShadowRunner` 只被测试与 `--preview` 调用。
- **真实 sender 未接**：真实 `message_transport` 适配器需**单独授权**；本轮所有回执
  都是 fake。
- **真实飞书 / readback 未做**；`+messages-mget` 校验留待接线轮。
- **生产旧链路未变**：`tokyo-status-card.timer`（hourly + notice）与独立 alert 路径
  仍在跑；`monitoring-policy.yaml` 未改。
- **OpenAgents / Fleet 插件 / Windows / Dell 部署**不在本轮。
