# Fleet Next · Agent Monitor 跨 PR 独立验收（2026-10-28）

**结论：CODE 层部分通过；跨模块链路存在 5 个 `BLOCKED_INTERFACE` / `BLOCKED_DEPENDENCY`，不能签 V1，也不能把 24 个单 PR 单测 PASS 当成全链路 PASS。**

本验收由非 #493/#488/#490/#423 作者执行。Legacy Fleet 0.1.0 仍是 ChatGPT 现行控制面；本文件没有启动、停止、安装或修改任何 Fleet 进程、timer、systemd unit、端口、插件、OAuth 或凭据，也没有真实 Feishu/IM 发送或付费模型调用。

## 1. 输入与 HEAD 核对

| PR | base | head | 状态 |
|---|---|---|---|
| #493 Agent Monitor 主体 | `main` | `4f5320de7e187daadd79adc92e7487facd7b1820` | Draft, OPEN |
| #488 activation × #481 Batch CLI | `integ/fleet-v1-batch-fixture-20261028` | `009662f36a2a5f8e11c51ae517834379a91a7770` | Draft, OPEN |
| #490 Legacy read-only source adapter | `main` | `06a8be67422f1320e7bdfee38410f8b24d27b501` | Draft, OPEN |
| #423 MilestoneNotifier / 单一 ledger | `main` | `d9b2948a62609e16007f2fe0ae288f9652d64cd1` | Draft, OPEN |

四个 head 与本轮派发给出的 SHA 逐一相等。`#424/#425/#478/#434` 是 Issue，不是 PR。

- 执行前已取 fresh ServerOS/worktree/process 快照；没有其它 Agent 正在写本 QA 的三个新路径。
- 独占 worktree：`worktrees/serveros/SRV-0011-fleet-monitor-crosspr-qa-20261028`。
- 新文件仅在本文件、`tests/test_fleet_next_monitor_crosspr_qa.py`、`integrations/fleet-next-monitor-crosspr-qa/**`。
- 没有编辑 #493/#488/#490/#423 的生产或测试源码。

## 2. 组合方式与真实进程

`integrations/fleet-next-monitor-crosspr-qa/compose.py` 从 git 逐 SHA 取出四棵完整树，放入同一进程的 `sys.path`：

```
#423 → #490 → #493 → #488（+ #481 Batch 目录）
```

测试断言每个被导入模块的 `__file__` 位于其 pinned checkout 内。CI 的 shallow checkout 不含这四个 commit 时，`compose.py` 会 `git fetch refs/pull/<n>/head` 到临时 bare repo，并断言取回的 commit **等于** pinned SHA；PR 漂移是命名失败，不会静默换成新树。

真实进程：

- 3 个独立 `python3` 启动 #488 activation wrapper，`--monitor-entry fleet_next_agent_monitor.runtime:tick`，各自持有 flock、各自写 state，跑完退出；
- 独立的 `python -m fleet_next_agent_monitor` 进程；
- 独立的 #481 Batch CLI 进程（#488 树内 `cli.py`）；
- #423 的 provider 是文件 journal fake（`O_APPEND` + `fsync`），跨进程可读；#490 用 `FixtureLegacyClient`；没有 stub 替代 Monitor/activation/Batch CLI/notifier。

### 命令与结果

```bash
# 失败前基线（pristine main，monitor 包不存在）
FLEET_QA_BASELINE_ROOT=/data/home/vince/workspace/src/serveros \
  python3 -m pytest -q -p no:cacheprovider tests/test_fleet_next_monitor_crosspr_qa.py
# exit 1；30 errors；首条：BASELINE_RED: integrations/fleet-next-agent-monitor is absent

# 真实组合
python3 -m pytest -q -p no:cacheprovider tests/test_fleet_next_monitor_crosspr_qa.py
# 25 passed, 5 xfailed, 8 subtests passed；exit 0

# 与 CI 相同的入口
python3 scripts/hermetic_tests.py tests/test_fleet_next_monitor_crosspr_qa.py -q
# 25 passed, 5 xfailed, 8 subtests passed；exit 0
```

5 个 `xfailed` 是 `unittest.expectedFailure`，不是 pass：它们逐条陈述“接口应当怎样”。若 Owner 修好，它们会变成 unexpected success 并使套件变红，提醒删掉豁免。对应的“当前缺陷”由同文件的 characterization 测试钉住（pass 只表示缺陷可复现，不表示接受）。

### 单 PR 测试复核（全部真实 SHA）

| PR | 命令 | 结果 |
|---|---|---|
| #493 | `pytest tests/test_fleet_next_agent_monitor_core.py` | 24 passed |
| #490 | `pytest tests/test_fleet_next_monitor_source_adapter.py` | 35 passed + 11 subtests |
| #423 | `pytest tests/test_fleet_v1_milestone_notify.py` | 39 passed |
| #488 activation | `pytest tests/test_fleet_next_agent_monitor_activation*.py` | 50 passed + 18 subtests |
| #488/#481 Batch CLI | `pytest tests/test_fleet_v1_milestone_batch_cli.py` | 24 passed |

这五组全绿，与本文件的 5 个阻断同时成立：单 PR 单测没有覆盖跨 PR 的收据、签名和 supervisor 返回值。

## 3. 验收矩阵

| 链路 | 证据 | 判定 |
|---|---|---|
| #488 → #493 零参数 `tick` | `resolve_entry("fleet_next_agent_monitor.runtime:tick")` 返回真实 `tick`；3 进程各 1 tick | **PASS** |
| #493 → #423 cloudcli run | verified run 跨 3 进程只 1 次 provider 调用、1 条 `DELIVERED` ledger | **PASS** |
| #423 ledger 二次去重 | 删除 monitor state 后再跑，ledger 返回 `DUPLICATE`，provider journal 仍 1 行 | **PASS** |
| Batch 与 Monitor 单一 ledger | Batch CLI 先送 member+summary（2 行），monitor 读到 `DUPLICATE`，无第 3 次发送 | **PASS**（两棵 #423 代码副本共享 schema） |
| #490 → #493 observation | 直接替换：`TypeError: unexpected keyword argument 'expected_boot_id'`；经桥接后 transcript fixture 全链路投递 | **BLOCKED_INTERFACE**（桥接才可用） |
| #493 → #423 CI | monitor 判 `TERMINAL_VERIFIED`，notifier 抛 `device_ref is required`，`DELIVERY_UNKNOWN` | **BLOCKED_INTERFACE** |
| #493 交付未确认后的下一次 tick | tick1 `UNCONFIRMED` exit 4；tick2 `SETTLED` exit 0，ledger 仍 `DELIVERY_UNKNOWN` | **FAIL（假成功）** |
| #488 Batch → #423 pinned head | `BatchAggregator` 拒绝 `NOTIFIER_SEAM_MISSING`：#423 head 无 `send_text` | **BLOCKED_DEPENDENCY** |
| #488 activation 报告 monitor vitals | tick `BLOCKED_INPUT` exit 3 → activation `BOUNDED_STOP` exit 0；`last_tick=0.0`、`stale=false` | **FAIL（假成功）** |
| 失联/过期/离线/CI Draft/错 SHA/未验证/拒绝授权 | 7 类均 UNKNOWN/OFFLINE/DENIED，dispatch=0，journal 空 | **PASS（无假成功）** |
| UNKNOWN 不升级 | 连续 3 进程无 native output 均 UNKNOWN、无投递；第 4 次才有输出→VERIFIED | **PASS** |
| runId/deliveryId/bootId/SHA 不串 | 2 个 run 的 journal/key/fingerprint/payload 各自携带自己的标识 | **PASS（见 §4.7 的 boot 字段局限）** |
| heartbeat/backlog/stale（core） | 旧 `last_ok` + 无可投递 ledger → `STALE_MONITOR`/`BACKLOG`/`BLOCKED_LEDGER`/`CONSECUTIVE_FAILURES` | **PASS（仅 core tick 文档）** |
| heartbeat/stale（activation CLI） | `main()` 不传 `cursor_age_seconds`，`last_tick` 恒 0.0 | **FAIL（未接线）** |
| #485 `BLOCKED_UPSTREAM_SOURCE` | 包已存在于 `4f5320d` | 源层解除；REAL_DEVICE 仍 NOT_RUN |
| #491 `BLOCKED_RUNTIME_ENTRY` | 零参数 tick 可解析并运行 | 入口层解除；新增上述 supervisor/ledger 阻断 |

## 4. 阻断与精确补丁要求

### 4.1 `BLOCKED_INTERFACE` · #493：未确认交付在下一次 tick 变成 `SETTLED`

- `core.py:197-206` 在调用 notifier **之前**写 cursor；异常/`DELIVERY_UNKNOWN` 只改局部变量。
- `core.py:223-225` 下一次 tick 只要 cursor 命中就把 `receipt` 写成 `DUPLICATE`，没有向 #423 ledger 复核。
- `core.py:253-263` 于是 `unknown_receipts=0, backlog=0` → `SETTLED, exit 0`。

实测（provider `delivered:false`）：

```
tick1 UNCONFIRMED exit=4 dispatched=1 delivered=0 unknown=1  receipt=DELIVERY_UNKNOWN
tick2 SETTLED     exit=0 dispatched=0 delivered=0 duplicate=1 receipt=DUPLICATE
ledger: state=DELIVERY_UNKNOWN, remote_id=NULL
provider calls: 1（从未确认）
```

补丁要求（交回 #493 Owner）：

1. cursor 必须区分“已交付”和“已尝试但收据未知”；只有 #423 明确返回 `DELIVERED`/`DUPLICATE` 才能进入 `SETTLED`。
2. `already` 分支应调用 #423 的 `reconcile(delivery_key)` 或读取 ledger 行；`DELIVERY_UNKNOWN` 必须保持 `UNCONFIRMED`，并允许下一 tick 重新核对（不是重新发送未经 ledger 允许的消息）。
3. 新增回归：#423 provider unknown → 连续两次 tick 都 `UNCONFIRMED`。

### 4.2 `BLOCKED_INTERFACE` · #493 → #423：CI payload 的 `device_ref` 为空

- #493 `notifier.py:177-193` 的 `github_payload` 固定 `"device_ref": ""`。
- #423 `milestone_notify.py:219-225` 的 `_device` 要求 `device:<id>`，空串直接 `MilestoneRefused`。
- 实测：CI 绿灯在 monitor 内是 `TERMINAL_VERIFIED`，但 `receipt=DELIVERY_UNKNOWN`，`receipt_why="MilestoneRefused: device_ref is required"`，journal 0 行、ledger 0 行；随后触发 §4.1 的假 `SETTLED`。

补丁要求：CI watch 要么携带一个显式的 typed device ref（例如 watch 增加 `device_ref`），要么 #423 为 CI 增加一个明确的无设备 subject（如 `device:ci` 或 typed `ci:<project>`），二者必须在契约里写清楚；不能靠空串。

### 4.3 `BLOCKED_DEPENDENCY` · #488/#481 → #423 pinned head：缺少 `send_text`

- #481 `batch.py:665-682` 的 `BatchAggregator.__init__` 要求 notifier 有公开 `send_text`，否则 `NOTIFIER_SEAM_MISSING`。
- #423 head `d9b2948` **没有** `send_text`（`MilestoneNotifier` 只有 `observe/observe_payload/reconcile`）。
- #488 树内 `integrations/fleet-v1-milestone-notify/milestone_notify.py` 比 #423 head 多 72 行（`4fc9fcb feat(notify): a public send seam`），正是 #488 依赖的版本。

实测：用 #423 head 构造 `BatchAggregator(filed.batch, notifier=notifier)` → `BatchRefused: NOTIFIER_SEAM_MISSING`。

补丁要求：在 #423 合入/重放 `4fc9fcb` 的 `send_text`，或让 #488 rebase 到包含该提交的 #423；在两份 `milestone_notify.py` 收敛前，不能声称 #488 与 #423 head 可组合。

### 4.4 `BLOCKED_INTERFACE` · #490 → #493：观察协议签名不一致

- #493 `sources.py:494-504` 调用 `observe_run(..., project=, expected_boot_id=, expected_source_sha=)`。
- #490 `adapter.py:272-275` 只接受 `project=`、`source_sha=`，没有 `expected_boot_id`。
- 直接替换实测：`TypeError: LegacyReadOnlySourceAdapter.observe_run() got an unexpected keyword argument 'expected_boot_id'`。
- 另外 #490 在授权失败时 **raise** `SourceRefused("DENIED")`（`adapter.py:156-171, 276`），#493 的 core 只把 `MonitorRefused` 当 watch 级拒绝；`SourceRefused` 会落进 `except Exception` → `UNKNOWN/SOURCE_SourceRefused` + hard failure，而不是 `DENIED`。
- 读操作返回 `Observation` dataclass，需 `.as_dict()` 才是 #493 读的 mapping。

本 QA 在 `source_bridge.py` 里用最小桥接（重命名 `expected_source_sha→source_sha`、丢弃 `expected_boot_id`、调用 `as_dict()`）证明 observation 可以被真实 core 消费；桥接不是修复，且 **丢弃 watch 级 boot 期望** 本身是缺口。

补丁要求：统一 kwargs（`source_sha` vs `expected_source_sha`）；#490 增加 `expected_boot_id`，或明确把 boot 绑定定义在 client 层并让 #493 不再传 watch 级期望；授权拒绝返回 `DENIED` Observation 而不是异常；让 README 的“可不变替换”与代码一致。

### 4.5 `BLOCKED_INTERFACE` · #490 与 #493 的契约漂移

| 输入 | #490 | #493 |
|---|---|---|
| CloudCLI 完成且有 `lastAssistantMessage` | `UNKNOWN / NO_NATIVE_ACTIVITY`（只认 `transcript`/`messages`，`adapter.py:374-393`） | `TERMINAL_VERIFIED`（`sources.py:385-412`） |
| CI 在别的 SHA 上绿灯 | `UNKNOWN / IDENTITY_MISMATCH`（`adapter.py:323-326`） | `UNKNOWN / SHA_MISMATCH`（`sources.py:458-465`） |

#493 `contract.py` 与 #490 README 都声称状态/原因逐字节一致；上表两项不一致。补丁要求：选定一个 native-evidence 字段（CloudCLI 的 `lastAssistantMessage` 或统一 transcript）和一个 wrong-SHA reason，两边同时改并加交叉测试。

### 4.6 `BLOCKED_INTERFACE` · #488 activation 把 monitor 的拒绝报告成成功

- `activation.py:234-237` 只从 tick 读 `backlog`/`unknown_receipts`，丢弃 `state`/`exit`/`why`/`health`。
- `activation.py:258-259` 无条件返回 `BOUNDED_STOP, exit 0`。
- `activation.py:310-312` 的 `main()` 不传 `cursor_age_seconds`；`run_bounded` 的 `clock` 默认 `lambda: 0.0`，所以 `last_tick` 恒 0.0、`stale` 恒 false。

实测：无 `FLEET_NEXT_AGENT_MONITOR_CONFIG/STATE` 时，`tick()` 返回 `BLOCKED_INPUT, exit 3`；activation 仍输出：

```json
{"state":"BOUNDED_STOP","exit":0,"ticks":1,
 "health":{"last_tick":0.0,"stale":false,"backlog":0,"unknown_receipts":0},
 "warnings":[]}
```

一个 systemd unit 会看到成功，而 monitor 一次也没运行。

补丁要求：把 tick 的 `state/exit/why/health/warnings` 映射进 activation 结果；`BLOCKED_INPUT`/`UNCONFIRMED` 不得映射为 exit 0；`main()` 必须把真实 cursor age / last_tick 传入，或直接透传 tick 的 `health`。新增回归：blocked tick → activation 非零退出且 `why` 可见。

### 4.7 其它 CODE 观察（不阻塞主链路，但应记录）

- **watch 级 boot 期望不进 durable fingerprint**：`sources.py:230-241` 的 identity `bootId` 取 client 的 `boot_id`，而 `sources.py:353-355` 的 boot 比较用 watch 的 `expected_boot_id`。两个只有 expected boot 不同的 watch 在 cursor fingerprint 里 boot 字段相同；watch key 仍能区分。payload 侧的 boot 是分开的（实测 `run.bootId` 分别为 `boot-qa-1`/`boot-qa-2`）。
- **`watch_key` 用 `|` 拼接不转义**：`contract.py:166-175`。`session_ref` 合法地含 `|`；若 `device_ref` 也含 `|`，两个不同 `(device, session)` 组合可能拼出同一个 key。当前 grant/transport 未把 `|` 从 device ref 排除。建议转义或长度前缀。
- **`backlog` 只数非终态**：一个 `TERMINAL_VERIFIED` 但交付 `DELIVERY_UNKNOWN` 的 watch 不计入 backlog，容易被 §4.1 的 `SETTLED` 掩盖。

## 5. 历史独立 QA #485 / #491

- **#485 `BLOCKED_UPSTREAM_SOURCE`**：`integrations/fleet-next-agent-monitor/` 已在 `4f5320d` 存在，源层阻断解除。但不等于 REAL_DEVICE/PRODUCTION 通过；其未运行的 `last_tick/backlog/stale` 与本文件 §4.6 的 supervisor 断线相互印证。
- **#491 `BLOCKED_RUNTIME_ENTRY`**：零参数 `fleet_next_agent_monitor.runtime:tick` 已可解析并在 3 个真实进程中运行，入口层阻断解除。但 #491 未覆盖的“未确认收据 / 跨模块假成功”正是 §4.1、§4.2、§4.6 命中的部分。
- 两项历史 BLOCKED 不得被“24 PASS”刷绿：本文件保留其历史结论，只追加“源/入口已解除、链路仍有新阻断”的分层证据。

## 6. 分层

| 层 | 状态 |
|---|---|
| CODE | **部分通过**；5 个阻断，见 §4 |
| UNIT | 25 passed + 5 xfailed（跨 PR）；单 PR 24/35/39/50/24 各自通过 |
| CI | 见 PR checks；不把“某些 shard 绿”当成全链路 |
| REAL_DEVICE | **NOT_RUN** |
| PRODUCTION | **NOT_RUN**；Legacy Fleet 0.1.0 未被触碰 |
| Timer / systemd | **NOT CLAIMED**；未安装、未 enable、未 systemctl |

## 7. 未做与边界

- 没有 main merge、force push、`git add .`、reset/clean/stash 其它 Agent 的 WIP。
- 没有真实 Feishu/IM 发送；provider 是文件 journal fake。
- 没有付费模型调用；summarizer 未启用。
- 没有旧 Fleet/plugin/端口/密钥/OAuth 改动。
- 没有安装/启用 timer 或 systemd unit。
- 没有停止其它 Agent。
- 没有把 #490/#488 的源码复制进本 PR 的生产路径；组合由 `compose.py` 按 SHA 取树，桥接仅存在于 QA 目录并明确标注。
