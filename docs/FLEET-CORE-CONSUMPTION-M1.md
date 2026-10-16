# V13 真实监控消费 · M1 —— 受信 Core 摘要接进既有卡与投递

本文件记录这一支（`feat/fleet-v1-core-consumption-m1-20261016`，baseline =
PR #196 head `402c470`，并 cherry-pick 了 PR #218 的 `bbfd436`/`5c9b696`）。
**只做开发**：不部署、不接 timer、不发飞书、不登录任何账号、不监听端口。

## 0. 本 run 闭合的那一段链

缺口是**「受信 Core 摘要 → 既有 viewmodel」这一跳不存在**：`card_surface_runner.
local_model_source` 能带对端设备，但只能带成 UNKNOWN 行，没有任何东西把对端的
Core 读数变成读数。现在：

```
fleet_mcp.core_lite_source.admit_core_lite   (T1 的路由/归属判决，不改)
        │  admitted readback summary
        ▼
runtime/fleet_core_bridge.device_from_core_readback   ← 本 run 新增
        │  serveros/fleet-monitor-device.v1（既有 seam 校验）
        ▼
runtime/fleet_monitor_source.fleet_model(entries=…)   ← 复用，未新增第二个 builder
        ▼
runtime/fleet_monitor_card.render_cardkit            (既有 V13 renderer)
        ▼
runtime/card_surface_runner.ShadowRunner.tick         (既有投递控制器，LIVE + REPORT)
```

**没有新的 renderer、没有新的 ledger、没有第二个状态权威。** 新增的两个模块都只做
「把别人的受信文档翻译成既有形状」，各自 200 行上下。

## 1. 用户的直接定案：两种卡

- **LIVE**：监控 + 告警**同一张卡、长期同一个 `message_id`**，靠 PATCH 更新。
- **CORE REPORT**：独立的归档卡，按时段封存。

`:00`/`:30` 心跳与「告警事件」两个触发**各自游标**；**报告归档不冻结 LIVE**。这一条
由 `runtime/card_surface.SLOT_SECONDS = 1800` 与 `regular_slot_id` 实现（PR #196），
本轮**只更正旧周期**：

| 位置 | 旧 | 新 |
|---|---|---|
| `fleet_monitor_card.DEFAULT_REFRESH_S` | 600（10 分钟） | **1800**（`:00`/`:30`） |
| `fleet_monitor_plan.NORMAL_REFRESH_S` | 600 | **1800** |

两个模块与 `card_surface` 从此只有一个周期数：卡片页脚说「每 30 分钟原位更新」，
调度器就按 1800 s 走，读者看到的两句话不会互相打架。旧 issue 评论里的
「600 s / 时段冻结 LIVE」与用户直接决定冲突，**以本更正为准**，源码注释里点名了
被取代的值而不是悄悄改掉。

## 2. Core 消费桥（`runtime/fleet_core_bridge.py`）

输入是 `admit_core_lite` 产出的 **readback summary**（`serveros/fleet-core-lite-readback.v1`）。
输出是**一个 device entry**，逐条只按真实 producer schema 映射：

| observation | 读数 | 口径 |
|---|---|---|
| `cpu.used_percent` | CPU % | 整机 |
| `memory` | 内存 %（+可用/共 GiB） | 由 bytes 换算 |
| `swap` | **Swap 存量**（已用/共）与 **Swap 换页流量**（页/秒）**两条** | 存量与流量分开，不合并 |
| `psi.cpu/memory/io` | PSI（some/full） | 压力失速 |
| `storage.readonly.filesystems[].roles` | 磁盘 <role> %（f_bavail 口径） | |
| `storage.readonly.disk_io[].roles` | 磁盘吞吐（读/写，本机设备，非公网） | |
| `services.services[]` | 服务 <id>：状态词 + 严重度 | 状态词是 producer 的，本桥只决定多响 |
| `gpu.readonly.gpus[]` | GPU <label> 利用率 / VRAM / 温度（**卡级计数**） | 低利用率**不等于**服务挂了 |

**fail-closed 与不可变 UNKNOWN**：`UNAVAILABLE`、缺字段、不可解析的数一律渲染
`未读到/无法确认` + 原因，**绝不 0**；`state != FRESH` → STALE；`live.accepted != true`
→ UNKNOWN；`principal.state == UNKNOWN`（**owner 未知**）→ 只给词不给数；`received_at`
在未来 → 拒绝；`device_ref` 不符 → **拒绝**（`CoreRefused`），因为把别的设备的读数
filed 到这台名下，正是路由检查存在的理由。

**隐私**：只读计数器与状态词；不读、不复制、不渲染任何账户的 uid/argv/env/cwd/token，
且本桥只看一个**闭集**里的字段，闭集之外的成员被忽略而不是携带。

`peer_model_source(core_readbacks, …)` 是注入 `ShadowRunner` 的 `model_source`：
本地一台仍走 `card_source.gather/snapshot`，对端走 Core 摘要；**没有摘要的设备由
inventory 保留为 UNKNOWN 行**（覆盖不因没有 provider 而丢）。给了
`fleet_monitor_source.TrendArchive` 时，Core 样本**落进同一份共享样本账本**，
卡与小时报告读的是同一个 store。

## 3. Native 恢复回执 → 类型化事件（`runtime/fleet_recovery_events.py`）

消费 `serveros/native-core-receipt.v1`（B 的 `supervisor/receipt.cpp` 写的形状）。
**该回执不携带 budget/backoff/attempt 计数**（那些在 recovery 自己的持久状态里），
所以本模块把它们报成 `UNKNOWN`，除非 producer 在可选的 `recovery` 块里给出——**不发明**。

事件形状与 `card_surface_runner` 的既有四字段（`kind`/`episode`/`version`/
`source_row_key`）**完全一致**，所以 Core 事件与告警事件从同一扇门进 LIVE：

- **一次执行**（`executed[]`）→ `HANDLING`，版本含 `code`/`exit_code`/`ok`：
  `ok=true, exit_code=0` **是「systemd 说好」，不是「服务好了」**——
  **绝不 RECOVERED**。
- **一次拒绝**（`plan.refusals[]`，闭集 code）→ `NO_LEASE`/`AGENT_BUSY`（正确地
  在等）是 `HANDLING`；`NOT_INSTALLED`/`MASKED`/`LINK_DOWN`/`INSTALL_STATE_UNKNOWN`/
  `AGENT_STATE_UNKNOWN` 是 `ESCALATED`（机器上的发现）。
- **RECOVERED 只有一个来源**：`recovery_transitions(previous, current)` 的
  **readiness 迁移**（NOT_READY→READY）。首见不算恢复（没有人看到那次跨越）。

拒绝：错 schema / `node` 不是这台设备 / `principal` 为空（**未知 owner 不是 owner**）
或非期望 owner / 时间不可解析 / 时间在**未来** / 超过新鲜度上限。

## 4. 测试

- `tests/test_fleet_core_bridge.py`（**27 例**）：verdict（未认证/未配置/设备不符/
  live 未接受/owner 未知/未来时间/过期/非 FRESH）、读数字段
  （CPU/内存/Swap 存量+流量/PSI/磁盘/服务/GPU 卡级）、**UNAVAILABLE 绝不 0**、
  stale 保留值但行说已过期、无 argv/env/uid 泄漏、卡面渲染、`peer_model_source`
  （有摘要→读数、无摘要→仍在卡上为 UNKNOWN、错设备不 filed 到这台、
  样本进共享账本）。
- `tests/test_fleet_recovery_events.py`（**25 例**）：exit 0 = HANDLING 不是 RECOVERED、
  RECOVERED 只来自 readiness 迁移、code 闭集、budget UNKNOWN 除非 producer 给出、
  重复轮询同版本 / 结果变则新版本、错 node/未知 owner/未来/过期/schema 拒、
  事件形状与既有 seam 一致。
- 相邻面（本机有界：`MemoryMax=1G / SwapMax=0 / CPUQuota=25% / TasksMax=64` + timeout）：
  fleet + V13 面 **499 passed**；loader/契约/相邻面 **469 passed + 550 subtests**，
  含 `tests/test_loader_identity.py`（新模块的加载形状合规）。

## 5. 真实 vs fixture（按用户要求分别标注）

- **真实只读**：`peer_model_source` 对**本机**仍走 `card_source.gather/snapshot`
  （真读本机）；测试里那一次真读确实发生了。
- **fixture**：对端（A6000 / 个人 Dell）的 Core 摘要在测试里是**构造的 readback**，
  不是真机取回——本机没有受信的 Core 路由（这正是 `admit_core_lite` 默认 UNKNOWN 的
  状态）。恢复回执同理，是构造文档。
- **未验收**：真实 Feishu sender（`lark-cli` 未运行）、任何生产 timer/service/凭据、
  真实 Core 路由、ChatGPT 侧插件安装。个人 Dell **无路由 → UNKNOWN**，且**不借用
  公司 Windows 的样本**；私人数据只到 owner 明确的私人收件人，且 audience 必须
  `device×recipient` 授权（`fleet_audience` 的签名判决，mock 许可不能用于真 sender）。

## 6. 明确没做

不部署、不接 timer、不发飞书、不登录账号、不监听端口；不改 `fleet_mcp/**`（T1）、
`native/**`（B）、`integrations/**`、`Skills/**`（P）；不合并。
