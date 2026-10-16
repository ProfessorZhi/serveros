# V13 / monitoring lane · Wave 2 源码审计（红线轮，未跑 pytest）

分支 `feat/v13-wave2-monitoring-audit-20261016`（baseline = PR #233 head `36a206d`）。
**未部署、未接 timer、未发飞书、未登录账号、未监听端口、未启动任何服务。**

## 0. 资源门禁：判为**红**，因此只做源码盘点 + Issue 列明

实测（2026-10-16 22:34，东京共享机）：

| 量 | 读数 | 判读 |
|---|---|---|
| load(1/5/15) | 27.4 / 24.1 / 25.3 | 高（A/B 线在跑） |
| PSI cpu some avg10 / full | 1.10% / 0.00% | 低 |
| PSI memory some avg10 / full avg10 | 0.00% / 0.00% | 低 |
| **PSI io full avg10 / avg60 / avg300** | **0.02% / 6.07% / 9.09%** | 近 5 分钟内约 **9% 时间 IO 完全失速**；当前已回落 |
| Mem available | 154 GB / 246 GB | 尚可 |
| **Swap used** | **29898 MB / 32767 MB（91%）** | **红灯**（#167 事故同源） |
| swapout（vmstat si/so，2 行） | 323/1008 → **0/0** | 当前停止，但曾换出 |
| Dirty / Writeback | 465 MB / 0 B | Writeback 已清 |

结论：**swap 91% 占用 + 近窗口 IO full 失速 9%** 触发用户给出的红灯条件（"swapout/IO"）。
本轮**不执行 pytest**，不做行为改动，只做源码盘点、最小零风险修正与 Issue 列明。
**本 worktree 的共享 build 锁未取用**（红灯不需要）；工作树干净，HEAD 与 #233 一致，
未发现并发写者。

## 1. 审计：V1 清单逐项（源码核对，非测试结论）

| V1 要求 | 结论 | 依据 |
|---|---|---|
| 复用**唯一** CardKit renderer | ✅ | `fleet_monitor_card.render_cardkit` / `card_period` 各一份，本分支未新增 |
| 复用**唯一**投递 ledger | ✅（LIVE/REPORT） | `card_surface.SurfaceStore` 单写者；`card_delivery` 仍是回执权威 |
| LIVE = 监控+告警同卡、同 `message_id` PATCH | ✅ | `decide_live` → `sender.update({message_id: live["remote_id"]})` |
| **告警事件与心跳两触发、各自游标** | ✅ | `decide_live` 只读 `self._data["live"]`；`decide_report` 只读 `self._data["reports"]`——**两个独立顶层键** |
| **报告归档不冻结 LIVE** | ✅ | `_round` 先 LIVE 后 REPORT，且各自只在 `DELIVERED` 时提交；LIVE 的槽游标与事件游标都在 `live` 键里，report 行卡住不影响它 |
| `:00`/`:30` 心跳（1800s） | ✅（本 lane） | `card_surface.SLOT_SECONDS=1800`；PR #233 已把 `fleet_monitor_card`/`fleet_monitor_plan` 的 600 更正为 1800 |
| 收件 device×recipient ACL | ✅ | `fleet_audience.AudienceVerdict`（签名类型）+ `apply_plan` 逐项核对 |
| private Dell 无 Provider → **UNKNOWN 而非抹掉** | ✅ | `EXPECTED_DEVICES` 每条都成行；`observed_ever=False` → 「尚未接入」+ UNCONFIRMED（灰） |
| 其他用户数据不进公司群 | ✅ | `AudiencePolicy` 默认拒绝；corporate 需显式 `approved_corporate`，private 只看自己的 grant |
| owner 缺失 / 未来时间 / stale 拒 | ✅ | `fleet_core_bridge.core_verdict` 与 `fleet_recovery_events.admit_receipt` 各自拒绝 |
| **不把 GPU util 当模型健康** | ✅ | 全 lane 源码中 GPU 利用率**从不**参与 health/severity；服务健康是独立的 `services` 状态词 |
| `exitcode 0 ≠ RECOVERED` | ✅ | `recovery_events` 把 executed 一律给 `HANDLING`；`RECOVERED` 只来自 `recovery_transitions` 的 readiness 迁移 |

## 2. 发现

### F1（真缺陷，本 lane，需最小修正）—— **第二套样本账本**

同一 lane 里有**两个**「只读样本账本」，都是原子整文档 JSON 写入者：

| | schema | 桶 | 消费方 |
|---|---|---|---|
| `runtime/snapshot_journal.py`（#196） | `serveros/snapshot-journal.v1` | 10 分钟 slot，`AXES=(cpu_load1,mem_percent,swap_percent,disk_percent)`，`CAP=576` | `runtime/surface_cli.py`；`tests/test_v13_integration.py` |
| `fleet_monitor_source.TrendArchive`（本人在 #218 引入） | `serveros/fleet-trend.v1` | 三时段 daypart，按 `(device,label)` 序列 | **无**（仅其自身测试与 `peer_model_source(archive=…)` 这个可选接缝） |

`TrendArchive` 是在 `snapshot_journal` 已知于本 lane 之前写的，现在它正是
「绝不重写第二套 ledger」所禁止的重复，且**无人消费**。

**本轮所做的零风险修正**（无行为改动，因此红灯下不可能弄坏任何东西）：
在 `TrendArchive` / `trend_archive_path` 与 `fleet_core_bridge.peer_model_source`
的 `archive=` 参数上写明 **SUPERSEDED → `runtime/snapshot_journal.py`**，并写明
「不要接新调用方、不要让它与 journal 同时跑」。

**延后到绿灯轮的删除/改接**（列在 Issue，不在本轮做）：删 `TrendArchive` 及其
`fleet_monitor_source` 内的测试，把唯一的 Core 样本写入路径改到 `snapshot_journal`，
并让 REPORT 的 coverage/mean/peak 与 LIVE 的趋势读**同一个** store。

### F2（问题，非我单方可定）—— **`snapshot_journal.SLOT_SECONDS = 600` 与 `:00/:30` 裁决**

`card_surface` 自己的注释把两者分得很清：「`SLOT_SECONDS` 是**卡片的刷新**节拍，
**不是采样**节拍」。所以 600 s **采样**、1800 s **发卡**在语义上可以并存。
但用户上一轮的更正针对的是「旧 issue 评论写 600s」，且 `CAP=576` 是按 600 s 选的
（4 天）——若改成 1800 s 则为 12 天。**这是设计取舍，不属我可单方静默修改**，
故列为 Issue 问项，不动代码。

## 3. 本轮差异（可审）

只有两处**文档级**修正（F1 的标记），零行为改动：

- `runtime/fleet_monitor_source.py`：`TrendArchive` 与 `trend_archive_path` 的
  docstring 加 SUPERSEDED 段。
- `runtime/fleet_core_bridge.py`：`peer_model_source` 的 `archive=` 参数 docstring
  加同向警告。

## 4. 测试收据（明确：**本轮未跑**）

用户 gate 规定红灯不执行 pytest，故本轮**没有任何新测试收据**。最近一次**已验证**的
收据来自本分支的父提交 `36a206d`（PR #233）：

- fleet + V13 面 **499 passed**（exit 0）
- loader/契约/相邻面 **469 passed + 550 subtests**（exit 0）

本轮改动仅 docstring，按上述收据推断不可能改变行为；**但这只是推断，不是实测**——
绿灯轮应重跑 `tests/test_fleet_monitor_card.py`、`test_fleet_monitor_plan.py`、
`test_fleet_coverage_audience.py`、`test_fleet_core_bridge.py`、
`test_fleet_recovery_events.py`、`test_card_surface*.py`、`test_v13_integration.py`
作为确认。

## 5. Core route / 生产发卡尚未验收（明确差距）

- **真实 Core 路由未部署**：本机没有受信 Core 源，`admit_core_lite` 默认为
  `NOT_CONFIGURED/UNKNOWN`；受信 Core 摘要→卡这一跳**只有 fixture/构造文档验证过**。
- **生产发卡未验收**：`lark-cli` 未运行、`SERVEROS_ALERT_RECIPIENT` 未设、
  任何 timer/service 未启用、无生产凭据。LIVE/REPORT 全链路只在 mock sender 上跑过。
- **真实采样只到本机那一路**：`card_source.gather`（真读本机）跑过；对端设备是构造文档。
- 个人 Dell 无路由 → UNKNOWN，且**不借用公司 Windows 样本**；私人数据只到 owner
  明确的私人收件人，需 `device×recipient` 授权。

## 6. 边界

未改 `main`、未改他人分支；未触碰 `fleet_mcp/**`（B）、Work/coordination（A）、
`native/**`（C）、`intake/**`（E）、`integrations/**`+`Skills/**`（F）。
未启动任何服务、未干扰任何 Agent、未触及 3013。
