# V13 monitor · schema / gap matrix

**审计对象**：`feat/v13-wave2-monitoring-audit-20261016` head `c345f6b`
（PR #239；stacked on #233 `f76bc09`；再往下是 #196 的双卡投递控制器）。
**审计方式**：源码逐项核对 + 合成文档只读探针 + 独立负测。
**没有**真实 Core 路由、真实 Feishu 发送、生产 state 读取、timer 或服务变更。

> 本文件不是 LIVE REPORT。表中的 `CONFIRMED_BY_PROBE` 只表示“在本机用合成
> 文档复现了该行为”，不表示生产链路已经被验证。

---

## 1. 文档与所有权总表

| 文档 | schema | 本分支的生产者 | 写入者 / 路径 | 消费者 | 状态 |
|---|---|---|---|---|---|
| Core readback | `serveros/fleet-core-lite-readback.v1` | **不在本分支**（`fleet_mcp.core_lite_source` 属 F1/#243，未合入） | 无（只读输入） | `fleet_core_bridge.device_from_core_readback` | 生产端不可核验 |
| 设备条目 | `serveros/fleet-monitor-device.v1` | `fleet_core_bridge` | 内存对象，不落盘 | `fleet_monitor_source.fleet_model` → V13 renderer | 已验证 seam |
| 双卡状态 | `serveros/card-surface.v1` | `card_surface.SurfaceStore` | `<state>/../cards/surfaces.json`（`card_surface.py:438-439`） | `ShadowRunner` / `surface_cli` | 单一写入者（真实模式另有 `SurfaceLock`） |
| 样本账本 | `serveros/snapshot-journal.v1` | `surface_cli._record_sample` | `<state>/../cards/samples.json`（`snapshot_journal.py:48-50`） | `surface_cli._report_history`、`tests/test_v13_integration.py` | **唯一活跃样本账本** |
| 趋势账本（旧） | `serveros/fleet-trend.v1` | **无生产 writer** | `<state>/fleet-trend.json`（`fleet_monitor_source.py:958-966`） | 无生产 consumer | **SUPERSEDED**，见 V13-G4 |
| 投递回执 | `card_delivery.CardLedger` | 既有投递路径 | 既有 ledger | `card_surface` 复用其 `binding_verdict` | 未改动、未替换 |

结论先行：**活跃样本写入者只有一个**（`snapshot_journal`）。第二套
`TrendArchive` 仍在树里，但截至本基线没有生产 writer / consumer；唯一的潜在
第二写入路径是 `fleet_core_bridge.peer_model_source(archive=...)`，默认
`archive=None`。因此“是否重复 owner”的答案是：

> **当前没有活跃的双写；存在一个被标记为 SUPERSEDED 的第二实现和一个可选接线口。**
> 在完成 V13-G5 的轴映射裁决之前，不得把 `archive=` 接向 `snapshot_journal`。

---

## 2. Core readback 的 TTL / freshness / UNKNOWN 契约

证据：`runtime/fleet_core_bridge.py:167-230`（判决）、`:237-405`（读数）、
`:424-464`（设备条目）。

### 2.1 字段检查

| 字段 | 要求 | 违反时 |
|---|---|---|
| `schema` | 精确等于 `serveros/fleet-core-lite-readback.v1` | `CoreRefused`（调用方 bug，不归属本设备） |
| `device_ref` | 精确等于目标设备 | `CoreRefused` |
| `route.state` | `VERIFIED`（闭集另含 `NOT_CONFIGURED` / `SOURCE_FAILED` / `UNAUTHENTICATED` / `DEVICE_MISMATCH` / `NO_DOCUMENT`） | `UNKNOWN` + route 自带的 reason |
| `live.accepted` | `is True` | `UNKNOWN` + `live.why` |
| `principal.state` | 不得为 `"UNKNOWN"` | `UNKNOWN`（“没有确立属于哪个账户”） |
| `source.received_at` | 带时区、可解析的 ISO 时间 | `UNKNOWN`（无法判定新鲜度） |
| 未来时间 | `received_at <= now` | `UNKNOWN`（拒绝把未发生的时钟当证据） |
| `freshness.state` | `FRESH` 且 `age <= stale_after_s` | 见 2.2 |
| `observations` | 对象 | 单条 `主机读数` UNKNOWN + 原因 |
| 单个 observation | `state == PRESENT` 且 `value` 是对象 | 该读数 UNKNOWN + 原因，**不是 0** |
| observation 键 | 闭集 `cpu/memory/swap/psi.*/storage.readonly/services/gpu.readonly` | 闭集外的字段不读取、不渲染 |

`stale_after_s` 默认 **1800 s**（30 分钟），与 LIVE 的 `:00/:30`
（`card_surface.SLOT_SECONDS = 1800`）一致；采样节拍可以更密，TTL 是**读数**
的有效期，不是发卡节拍。

### 2.2 TTL / freshness 判决表

| route | live | principal | 时间 | freshness | age vs 1800s | 结果 |
|---|---|---|---|---|---|---|
| 非 VERIFIED | 任意 | 任意 | 任意 | 任意 | 任意 | UNKNOWN |
| VERIFIED | 非 True | 任意 | 任意 | 任意 | 任意 | UNKNOWN |
| VERIFIED | True | `state=="UNKNOWN"` | 任意 | 任意 | 任意 | UNKNOWN |
| VERIFIED | True | **缺失 / 空对象** | 任意 | 任意 | 任意 | **UNKNOWN（当前实现给 KNOWN → V13-G1）** |
| VERIFIED | True | 已确立 | 不可解析 / 未来 | 任意 | 任意 | UNKNOWN |
| VERIFIED | True | 已确立 | 可解析、非未来 | `FRESH` | `<= 1800` | **KNOWN** |
| VERIFIED | True | 已确立 | 可解析、非未来 | `FRESH` | `> 1800` | STALE |
| VERIFIED | True | 已确立 | 可解析、非未来 | 非 `FRESH` | `> 1800` | STALE |
| VERIFIED | True | 已确立 | 可解析、非未来 | 非 `FRESH` | `<= 1800` | UNKNOWN（文案见 V13-G7） |
| VERIFIED | True | 已确立 | 非对象 `source`/`freshness` | — | — | **UNKNOWN（当前抛 AttributeError → V13-G2）** |

### 2.3 UNKNOWN 不可变成 0

- `unobserved()` 的 `value` 永远是 `None`，且必须带 `reason`
  （`fleet_monitor_card.py:158-169`）。
- `core_readings()` 在 `state != KNOWN` 时，所有读数都走
  `unobserved()` / `stale()`（`fleet_core_bridge.py:250-260`）。
- STALE **保留旧值**，但状态词是 STALE 并带原因；这是有意的，见
  `fleet_monitor_card.stale()` 的 docstring。旧值不会被读成“现在”。
- 磁盘 `total_bytes == 0` 或缺失时给 `—`，不是 0%；`_pct()` 返回 `None`
  （`fleet_core_bridge.py:142-146`）。

### 2.4 服务 / GPU 语义

- 服务状态词来自 producer；severity 映射是闭集
  （`ACTIVE/RUNNING/READY→OK`、`ACTIVATING/.../INACTIVE→ATTENTION`、
  `FAILED/DEAD→CRITICAL`，未知词→ATTENTION）。
- **GPU 利用率不参与 health/severity**：它是一条 `kind="gpu"` 的读数，
  severity 为 OK；低利用率不等于服务健康，高利用率也不等于故障
  （`fleet_core_bridge.py:379-404`）。GPU 采样时刻单独成行。

---

## 3. 报告幂等与收据门

证据：`runtime/card_surface.py` 与 `runtime/card_surface_runner.py`。

### 3.1 文档与游标

`serveros/card-surface.v1` 一个文档承载两个 surface：

```text
{ schema, live: {...} | null, reports: [...], retired: [...] }
```

- `live.last_delivered_regular_slot`：心跳游标，只在**送达的回执**上推进。
- `live.consumed_events`：事件 key（`episode/event_id@version`）游标，独立于
  心跳；`CONSUMED_CAP=200`。
- `reports[].last_patch_at`：报告 patch 的确认游标；`apply_report(PATCH)` 只写
  `pending_patch`，`ack_report_patch` 才推进。
- `reports[].phase`：`LIVE / FINAL_PENDING / FROZEN / CREATE_PENDING /
  NEEDS_RECONCILE`；`FINAL_PENDING` 只由 `confirm_report` 变 `FROZEN`。
- `IDEMPOTENCY_WINDOW_SECONDS = 3600`：CREATE 意图超出窗口后**不再重发**，
  进入 `NEEDS_RECONCILE`，等真实 readback 或人工裁决。

### 3.2 收据门矩阵

| surface | 动作 | 必须的回执 | 违反时 | 游标效果 |
|---|---|---|---|---|
| LIVE | CREATE / SUCCESSOR | `DELIVERED` + `remote_id` | 拒绝，保留 pending | 绑定 message |
| LIVE | PATCH | `DELIVERED` + 指向本条消息的 id | 拒绝，保留 pending | 心跳 slot + 事件 keys |
| LIVE | PATCH | **无 `remote_id`** | **当前接受（V13-G3）** | **会推进** |
| REPORT | CREATE | `DELIVERED` + `provider.remote_id` | 保留 `CREATE_PENDING` | 绑定报告消息 |
| REPORT | PATCH ack | `DELIVERED` + 与绑定 id 相同 | 拒绝，保留 `pending_patch` | `last_patch_at` / `archive` |
| REPORT | FINAL | `DELIVERED` + `version == prepared_version` + 与绑定 id 相同 | 拒绝，留在 `FINAL_PENDING` | `FROZEN` |

**LIVE 与 REPORT 的独立性**：`decide_live` 只读 `live` 键，`decide_report` 只读
`reports` 键；`apply_report` / `ack_report_patch` / `confirm_report` 不读写
`live`。测试 `report_operations_do_not_touch_live` 直接断言这一点。

### 3.3 当前没有做硬门的两处

- **evidence 词**：`EVIDENCE_REMOTE_VERIFIED` 的注释说它是“真实发送唯一可提交
  的证据”，但 `confirm_live` / `confirm_report` 不检查 `evidence`；合成
  `DELIVERED` 也能推进/冻结。当前真实发送只有 canary 的
  `ReadbackVerifiedSender`，dry-run 用临时 sandbox store，因此是**潜在**问题
  （V13-G8）。
- **payload digest**：`stage_report` 在“同 period + 同 key”时直接返回
  `idempotent: True`，不比较 `payload_digest`（V13-G6）。

---

## 4. 缺口矩阵

严重度：P0 错误数据/重复卡/重复 owner；P1 身份、新鲜度、收据的 fail-open；
P2 潜在重复账本或幂等 payload；P3 文案/防御性健壮性。

| ID | 严重度 | 面 | 事实与证据 | 当前行为（已复现） | 应有行为 | 状态 |
|---|---|---|---|---|---|---|
| **V13-G1** | **P1** | Core 归属 | `core_verdict` 只拒 `principal.state == "UNKNOWN"`；`summary.get("principal") or {}` 把缺失/空对象变成 `{}`（`fleet_core_bridge.py:202-207`） | 删除 `principal` → `KNOWN`；`principal=[]` → `KNOWN` | 缺失、空、非对象都必须是 `UNKNOWN`；身份未确立不是身份已确立 | CONFIRMED_BY_PROBE |
| **V13-G2** | **P1** | fail-closed | `source` / `freshness` 直接 `.get`，没有非对象守卫（`:209-229`） | `source="x"`、`source=["x"]`、`freshness="FRESH"` → `AttributeError` | 非对象 → `UNKNOWN` + 原因；一个畸形文档不能让整个 tick 崩掉 | CONFIRMED_BY_PROBE |
| **V13-G3** | **P1** | LIVE 收据身份 | `confirm_live` 的 PATCH 分支用 `receipt_remote_id(receipt) or target`（`:703-709`）；`confirm_report` 明确拒绝无 id 回执（`:920-927`） | `{"state":"DELIVERED"}` 无 `remote_id` → 确认成功，心跳 slot 推进 | 与 REPORT 同则：没有消息身份的回执不是关于这条消息的证据；拒绝并保留 pending | CONFIRMED_BY_PROBE |
| **V13-G4** | **P2** | 样本账本 owner | 两个整文档 JSON writer：`snapshot_journal`（`serveros/snapshot-journal.v1`，`:32-50`）与 `TrendArchive`（`serveros/fleet-trend.v1`，`fleet_monitor_source.py:798-966`） | 生产 writer 只有 `surface_cli` → Journal；`TrendArchive` 0 生产 writer / 0 生产 consumer；`peer_model_source(archive=)` 默认 `None` | 唯一账本 = `snapshot_journal`；`TrendArchive` 删除或只读兼容；不得接新 caller、不得与 journal 同跑 | CONFIRMED_BY_SOURCE + STATIC_TEST |
| **V13-G5** | **P1（迁移）** | Core → journal 轴 | journal `AXES=(cpu_load1, mem_percent, swap_percent, disk_percent)`，按中文 label 子串匹配（`snapshot_journal.py:42-45,80-91`）；Core bridge 的 label 是 `CPU`(%) / `内存` / `Swap 存量` / `磁盘 <role>`（`fleet_core_bridge.py:270-362`） | `sample_from_readings(Core readings)` → `cpu_load1=None`、`swap_percent=None`；内存/磁盘有值 | 迁移前显式定义轴映射。**CPU% ≠ load1**；无法映射的轴不得静默变成 `None` 后仍计 coverage | CONFIRMED_BY_PROBE |
| **V13-G6** | **P2** | 报告幂等 payload | `stage_report` 同 `period_ref` + 同 key 直接 `idempotent: True`，从不比较 `payload_digest`（`card_surface.py:755-764`） | key `rk` digest A，再次 digest B → `idempotent: True`，保留 digest A | 同 key 不同 payload 必须拒绝，或显式记为 superseded / needs-reconcile；不能当重放 | CONFIRMED_BY_PROBE |
| **V13-G7** | **P3** | 新鲜度文案 | UNKNOWN 分支无条件写“超过上限 N 分钟”（`:227-229`） | 非 `FRESH` 但 `age=0` → `UNKNOWN` + “超过上限 30 分钟” | 未超上限时不得声称超了；文案按实际分支生成 | CONFIRMED_BY_PROBE |
| **V13-G8** | **P3** | evidence 硬门 | `EVIDENCE_REMOTE_VERIFIED` 注释称“真实发送唯一可提交证据”（`:119-121`），但两个 `confirm_*` 都不检查 `evidence`；未标注的回执按 sender 类型归为 synthetic（`:192-206`） | 合成 `DELIVERED` 可推进/冻结（测试与 dry-run 均如此） | 若出现非 mock 的生产 runner，提交处必须有 `remote_verified` 硬门；当前仅 canary 真发且用 `ReadbackVerifiedSender`，dry-run 用临时 store | SOURCE_ONLY |
| **V13-G9** | **P3** | 报告行装载校验 | `SurfaceStore._load` 对 live 的 `CREATE_PENDING` 校验 key/recipient（`:503-527`），对 `reports` 只校验 `period_ref`/`phase`（`:528-535`） | 一个缺 key 但带可解析 `pending.at` 的 report 行 → `decide_report` 返回 resume CREATE + `idempotency_key: None`，runner 用确定性 fallback key（`card_surface_runner.py:516`） | 与 live 同等校验；缺 durable key 即拒绝装载或 `NEEDS_RECONCILE`，不猜 key | SOURCE_ONLY |

### 4.1 未列为缺口的项

- **LIVE 半小时心跳 vs 告警事件两触发、各自游标**：成立；`decide_live` 只读
  `live`，心跳与事件在同一个 patch 中 coalesce，收据里同时带 slot 与 event keys。
- **报告归档不冻结 LIVE**：成立；所有 report 方法都不触碰 `live`。
- **声明设备无采集时保留 UNKNOWN 行**：`fleet_model` 对 inventory 的每个设备
  都保留行；`observed_ever=False` 渲染为“尚未接入”而非删除（`fleet_monitor_source.py:1034+`）。
- **GPU 利用率 ≠ 模型健康**：成立；GPU 读数 severity 恒为 OK。
- **`exitcode 0 ≠ RECOVERED`**：`fleet_recovery_events` 把 executed 一律给
  `HANDLING`；`RECOVERED` 只来自 readiness 迁移。
- **读不出/损坏的 surfaces store 拒绝启动**：成立；`SurfaceStore._load` 抛
  `CardSurfaceError`，不会用空文档制造第二张 LIVE。

---

## 5. 合成负测

文件：`tests/check_monitor_contract.py`。全部为内存/临时目录操作：

- 不打开 socket、不跑 `lark-cli`、不读生产 state、不执行 `card_source.gather()`；
- `SurfaceStore` 只写 `tempfile.TemporaryDirectory()`；
- 每个 `expectedFailure` 都带 `V13-G*` 标识和“修复后改成真断言”的说明。

覆盖：

1. Core 读数：非 VERIFIED 路由、live 未接受、principal UNKNOWN、未来时间、
   超过 TTL、错误设备、错误 schema、缺 observations、PRESENT 无对象、
   服务 FAILED、GPU 利用率不参与 health。
2. 报告收据：CREATE 无 message id 拒绝、FAILED patch 不推进游标、REPORT
   FINAL 要求 version + 同一 message、REPORT 操作不触碰 LIVE、
   CREATE 意图窗口内 resume / 窗口外 NEEDS_RECONCILE、stage 先于 send。
3. 账本：两个 schema/path 不同、`peer_model_source` 默认不写第二账本、
   `TrendArchive` 带 SUPERSEDED 标记。
4. 已确认缺口：G1/G2/G3/G5/G6/G7 各一条 expectedFailure。

---

## 6. 验证边界（不得越过的声明）

| 声明 | 状态 |
|---|---|
| 真实 Core 路由 | **未验证**；producer `fleet_mcp.core_lite_source` 不在本分支 |
| 真实 Feishu 发送 / PATCH / readback | **未验证**；无凭据、无 sender、无 timer |
| 生产 `tokyo-status-card.timer` / `tokyo-alert-digest.timer` | **未触碰**；未读取、未重启 |
| `cards/samples.json` 的生产内容 | **未读取**；测试只用临时目录 |
| V1 验收 / LIVE REPORT | **不成立**；本交付是审计与合成负测 |
| 主套件回归 | **未跑**（资源门禁见第 7 节）；只运行本目录的短测试 |

---

## 7. 本轮资源门禁与测试收据

审计时（2026-10-20，东京共享机）实测：

| 量 | 读数 | 判读 |
|---|---|---|
| load(1/5/15) | 25.73 / 26.22 / 24.00 | 高，存在其他 Agent |
| Mem available | 156 GiB / 246 GiB | 尚可 |
| Swap used | 31 / 31 GiB（100%） | **红灯**；与 #167 同源 |
| PSI io full avg10 / avg60 / avg300 | 0.36% / 0.82% / 2.00% | 低于 #239 红灯时的 9.09%，但 swap 仍满 |
| PSI cpu some avg10 | 0.05% | 低 |

按“swapout/IO”门禁，**重套件仍判红**。因此本轮：

- 不跑仓库全量 pytest、不跑相邻面大套件、不跑 C++ 编译、不做多 Session 压测；
- 只运行 `integrations/fleet-v13-monitor/tests/check_monitor_contract.py`
  （纯内存、无网络、秒级），`nice -n 10` + `timeout`；
- 另跑 `tests/test_collection_scope.py`（4 例，0.11 s），证明本目录的
  `check_*.py` 命名没有破坏仓库的 collection-scope 守卫。

实测收据（`/data/home/vince/toolchains/serveros-test/bin/python`，Python 3.12.3，
pytest 9.1.1）：

```text
$ python -m pytest integrations/fleet-v13-monitor/tests/check_monitor_contract.py -q
21 passed, 7 xfailed, 4 subtests passed in 0.16s   (exit 0)

$ python -m pytest tests/test_collection_scope.py -q
4 passed in 0.11s                                   (exit 0)
```

`7 xfailed` 对应第 4 节的 V13-G1/G2/G3/G5/G6/G7/G9。**它们不是修复**；
其中任何一条变成 `xpassed` 时才说明缺口被改动，届时应改成真断言。
