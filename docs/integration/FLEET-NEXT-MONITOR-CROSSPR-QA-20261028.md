# Fleet Next · Agent Monitor 跨 PR 独立验收（Wave 15 续作，2026-10-28）

**结论：基线已切到真实 Wave 14 RC #494。notifier `send_text` 缺口撤销并历史化；其余 5 项真实断点保留（#493 P0、#488 P0、#490 P0），作者新 HEAD 尚未公开，故全部标 `BLOCKED_UPSTREAM`，不用跳过或改 `expectedFailure` 个数冒充修复。**

本文件是 #495 独立 QA 的 Wave 15 续作。Legacy Fleet 0.1.0 仍是 ChatGPT 现行控制面；没有启动、停止、安装或修改任何 Fleet 进程、timer、systemd unit、端口、插件、OAuth、凭据、Feishu/IM 或付费模型调用。

## 1. 输入、纠正与当前 HEAD

### 1.1 #495 v1 的 notifier 误报

#495 v1 把 #423 的原始 head `d9b2948` 当作 notifier 的 pin。该 head 的 blob 是 `72fcec8`，**没有** `send_text`，于是 #481 的 `BatchAggregator` 正确地报了 `NOTIFIER_SEAM_MISSING`。但真实 Wave 14 RC #494 通过 #481 的 merge `ba76a80` 携带的是 #460 的 send seam `4fc9fcb`，blob `7df27cb`，**有** `send_text`。

| 位置 | commit | notifier blob | `send_text` |
|---|---|---|---|
| #423 原始 head（#495 v1 的 pin） | `d9b2948a62609e16007f2fe0ae288f9652d64cd1` | `72fcec8d553f1568a114449d352e1803250d26cc` | 否 |
| #460 send seam | `4fc9fcbc677f278d10caeb34d55184545810a13a` | `7df27cb36b169134ff279e43c0b9c329cf919fef` | 是 |
| Wave 14 RC #494 | `00503f91b019ce342af52f38b19bb3aa21dfff55` | `7df27cb36b169134ff279e43c0b9c329cf919fef` | 是 |

**纠正**：#495 v1 §4.3 的 `NOTIFIER_SEAM_MISSING` 是 **pin artifact**，不是 RC 缺陷。该缺口已撤销；本文件的 RED→GREEN 控制同时保留旧 pin 的 RED 和 RC 的 GREEN，防止再次把旧 pin 当成产品。

#496 的独立 gate 在 `aed93c2f780944e6b773019f19c750d387bfc441` 给出同一结论（当前 head 是 `a34b811f…`，一个更晚的 independence 调整；结论不变）。本 QA 另外直接核对了 #494 的 notifier blob 与 #494 `composition.json` 的 `blob` 字段，二者都是 `7df27cb`。

### 1.2 仍被验收的作者 HEAD

截至本次执行，以下 PR 的公开 head 未变，作者的 P0 修复尚不可读：

| PR | 当前公开 head | 状态 |
|---|---|---|
| #493 Agent Monitor 主体 | `4f5320de7e187daadd79adc92e7487facd7b1820` | 未刷新；P0 修复未公开 |
| #488 activation × #481 Batch CLI | `009662f36a2a5f8e11c51ae517834379a91a7770` | 未刷新；P0 修复未公开 |
| #490 Legacy read-only adapter | `06a8be67422f1320e7bdfee38410f8b24d27b501` | 未刷新；P0 修复未公开 |
| #423 MilestoneNotifier | `d9b2948a62609e16007f2fe0ae288f9652d64cd1` | 仅作历史 pin；RC 实际用 `4fc9fcb` |

因此本文件不把 #493/#488/#490 的断言改成 pass；它们保持 failing/`expectedFailure`，并标 `BLOCKED_UPSTREAM`。作者一旦公开新 HEAD，QA 需要按新 SHA 重跑，而不是复用本文件的结论。

### 1.3 RC 组合与记录

#494 `composition.json` 记录：

- base #492 `a00fabc`，composed source `7ff573d`（merge #483）；
- authors：#493 `4f5320d`（merge `b23cda6`）、#488 `009662f`（merge `efde750`）、#490 `06a8be6`（merge `a5e55b9`）、#483 `644ee4a`（merge `7ff573d`）；
- notifier：`blob=7df27cb`（即 `4fc9fcb`），但 `head` 字段仍写 `d9b2948`。

**#494 记录内部不一致**：`notifier.head` 是旧 head，`notifier.blob` 是新 seam。代码以 blob 为准；这是 RC 记录的文档缺陷，不应反过来让 QA 继续 pin 旧 head。

本 QA 用 blob 逐文件验证：#494 中 #493/#488/#490 的三个关键文件与各自 pin 的 blob **完全相同**；notifier 文件 blob 为 `7df27cb`；历史 pin `d9b2948` 的文件 blob 为 `72fcec8` 且不含 `def send_text`。

## 2. 组合方式与真实进程

`integrations/fleet-next-monitor-crosspr-qa/compose.py` 从 git 取出：

1. **#494 `00503f9`** 作为被测树；
2. **#423 `d9b2948`** 仅作为历史 RED 控制（只读文本，不进 `sys.path`）。

CI 的 shallow checkout 不含这些 commit 时，`compose.py` 会 `git fetch refs/pull/<n>/head` 到临时 bare repo，并断言取回的 commit **等于** pinned SHA。PR 漂移是命名失败，不会静默换成新树。

真实进程：

- 3 个独立 `python3` 启动 #488 activation wrapper，`--monitor-entry fleet_next_agent_monitor.runtime:tick`，各自 flock、各自写 state，跑完退出；
- 独立 `python -m fleet_next_agent_monitor` 进程；
- 独立 #481 Batch CLI 进程（#494 树内 `cli.py`）；
- #423 notifier 是 #494 树内的真实 `MilestoneNotifier`；provider 是文件 journal fake；#490 用 `FixtureLegacyClient`。没有 stub 替代 Monitor/activation/Batch CLI/notifier。

### 2.1 命令与结果（本地）

```bash
# 失败前基线（pristine main，monitor 包不存在）
FLEET_QA_BASELINE_ROOT=/data/home/vince/workspace/src/serveros \
  python3 -m pytest -q -p no:cacheprovider tests/test_fleet_next_monitor_crosspr_qa.py
# exit 1；32 errors；首条：BASELINE_RED: integrations/fleet-next-agent-monitor is absent

# 真实 #494 组合
python3 -m pytest -q -p no:cacheprovider tests/test_fleet_next_monitor_crosspr_qa.py
# 27 passed, 5 xfailed, 8 subtests passed；exit 0

# 与 CI 相同的入口
python3 scripts/hermetic_tests.py tests/test_fleet_next_monitor_crosspr_qa.py -q
# 27 passed, 5 xfailed, 8 subtests passed；exit 0
```

5 个 `xfailed` 仍是 5 个 `expectedFailure`，没有增减：

1. `test_an_unconfirmed_delivery_must_stay_unconfirmed`（#493 P0）
2. `test_a_verified_ci_fact_must_reach_the_one_notifier`（#493 P0）
3. `test_activation_must_not_report_a_blocked_tick_as_success`（#488 P0）
4. `test_490_adapter_should_plug_into_493_unchanged`（#490 P0）
5. `test_490_denial_should_be_a_denied_observation_for_493`（#490 P0）

notifier seam 不是 xfail，也不是 blocker：它是 **RED→GREEN** 的普通测试（旧 pin RED、#494 GREEN）。因此本轮减少的是 1 个**误报**，不是 1 个真实 P0。

### 2.2 #494 自身的目标测试

在同一 #494 树上直接跑作者测试：

| 文件 | 结果 |
|---|---|
| `tests/test_fleet_next_agent_monitor_core.py` | 24 passed |
| `tests/test_fleet_next_agent_monitor_activation.py` | 26 passed |
| `tests/test_fleet_next_agent_monitor_activation_batch.py` | 24 passed + 18 subtests |
| `tests/test_fleet_next_monitor_source_adapter.py` | 35 passed + 11 subtests |
| `tests/test_fleet_next_legacy_survival.py` | 70 passed |

`test_fleet_next_rc_wave14_ledger.py` 需要 git 历史/仓库根，在 `git archive` 的临时树里 52 failed；这不是作者代码结论，故不作为证据，只记录该文件的运行环境限制。hosted #494 CI 见 §6。

## 3. 验收矩阵（Wave 15）

| 链路 | 证据 | 判定 |
|---|---|---|
| 真实 RC 组合 | #494 含 #493/#488/#490 同 blob；notifier blob `7df27cb` | **PASS** |
| notifier send seam | 旧 pin `d9b2948` 无 `send_text` → `NOTIFIER_SEAM_MISSING`；#494 `4fc9fcb` → `BatchAggregator` 构造成功 | **PASS（RED→GREEN；撤销 v1 误报）** |
| #488 → #493 零参数 tick | 3 真实进程，1 次投递 | **PASS** |
| #493 → #423 cloudcli run | 1 provider call、1 条 `DELIVERED` ledger | **PASS** |
| 删除 cursor 后 #423 ledger 去重 | `DUPLICATE`，journal 不变 | **PASS** |
| Batch CLI + Monitor 单一 ledger | 2 行（member+summary），monitor `DUPLICATE`，无第 3 次发送 | **PASS** |
| #493 未确认交付后再 tick | tick1 `UNCONFIRMED` exit 4 → tick2 `SETTLED` exit 0，ledger 仍 `DELIVERY_UNKNOWN` | **FAIL（P0，BLOCKED_UPSTREAM #493）** |
| #493 CI typed identity | monitor `TERMINAL_VERIFIED`；#423 拒 `device_ref:""`；给 `device:ci` 时 #423 可 `DELIVERED`（负控） | **BLOCKED_INTERFACE（P0，BLOCKED_UPSTREAM #493/#423）** |
| #488 activation 回报 | blocked tick exit 3 → `BOUNDED_STOP` exit 0，`last_tick=0.0`；`TICK_FAILED` 负控可非 0 | **FAIL（P0，BLOCKED_UPSTREAM #488）** |
| #490 → #493 observation | 直接替换 `TypeError: expected_boot_id`；桥接 + transcript 全链路 `DELIVERED`（负控） | **BLOCKED_INTERFACE（P0，BLOCKED_UPSTREAM #490/#493）** |
| #490 denied | 直接替换 raise `SourceRefused(DENIED)`；#493 期望 `DENIED` Observation | **BLOCKED_INTERFACE（P0，BLOCKED_UPSTREAM #490）** |
| #490/#493 契约漂移 | native evidence `transcript/messages` vs `lastAssistantMessage`；wrong-SHA `IDENTITY_MISMATCH` vs `SHA_MISMATCH` | **BLOCKED_INTERFACE（P0，BLOCKED_UPSTREAM #490/#493）** |
| 失联/过期/离线/Draft/错 SHA/未验证/拒绝授权 | 均 UNKNOWN/OFFLINE/DENIED，dispatch 0、journal 空 | **PASS（无假成功）** |
| UNKNOWN 不升级 | 3 进程无 native output 均 UNKNOWN；第 4 次有输出才 VERIFIED | **PASS** |
| boot / SHA / runId / deliveryId 不串 | 2 个 run 的 journal/key/fingerprint/payload 各带自己的标识；boot mismatch/错 SHA 均 UNKNOWN | **PASS（watch-level boot 局限见 §4.6）** |
| heartbeat/backlog/stale | core tick 四种 warning 齐全 | **PASS（core）** |
| heartbeat/stale 经 activation CLI | `main()` 不传 cursor age，`last_tick=0.0` | **FAIL（P0，BLOCKED_UPSTREAM #488）** |
| #485 `BLOCKED_UPSTREAM_SOURCE` | 包已存在于 `4f5320d`/RC | 源层解除；REAL_DEVICE 仍 NOT_RUN |
| #491 `BLOCKED_RUNTIME_ENTRY` | 零参数 tick 可解析并运行 | 入口层解除；supervisor/ledger 断点仍存 |

## 4. 仍然真实的断点与补丁要求

### 4.1 `#493 P0` · 未确认交付在下一次 tick 变成 `SETTLED`

- `core.py:197-206` 在 hand-over **之前**写 cursor；异常/`DELIVERY_UNKNOWN` 只改局部变量。
- `core.py:223-225` 下一次 tick 只要 cursor 命中就写 `DUPLICATE`，没有向 #423 ledger 复核。
- `core.py:253-263` 于是 `unknown_receipts=0, backlog=0` → `SETTLED, exit 0`。

实测（#494 树，provider `delivered:false`）：

```
tick1 UNCONFIRMED exit=4 dispatched=1 delivered=0 unknown=1  receipt=DELIVERY_UNKNOWN
tick2 SETTLED     exit=0 dispatched=0 delivered=0 duplicate=1 receipt=DUPLICATE
ledger: state=DELIVERY_UNKNOWN, remote_id=NULL
provider calls: 1（从未确认）
```

**负控**：同一条链在 provider 确认时能 `SETTLED`（`test_a_verified_run_is_delivered_once_through_423`），所以失败不是“永远无法 SETTLED”的空断言。

补丁要求（交回 #493 Owner）：

1. 只有 #423 明确返回 `DELIVERED`/`DUPLICATE` 才允许 `SETTLED`；`DELIVERY_UNKNOWN` 保持 `UNCONFIRMED`。
2. `already` 分支应调用 #423 的 `reconcile(delivery_key)` 或读 ledger 行；不要用 cursor 代替收据。
3. 新增回归：provider unknown → 连续两次 tick 都 `UNCONFIRMED`。

### 4.2 `#493 P0` · CI typed identity

- #493 `notifier.py:177-193` 的 `github_payload` 固定 `"device_ref": ""`。
- #423 `milestone_notify.py:219-225` 的 `_device` 要求 `device:<id>`，空串 `MilestoneRefused`。
- 实测：CI `TERMINAL_VERIFIED` 但 `DELIVERY_UNKNOWN: device_ref is required`，journal/ledger 0 行，随后触发 §4.1 的假 `SETTLED`。

**负控**：同一 #494 notifier 收到 `device_ref: "device:ci"` 的同一 CI payload 时 `DELIVERED`（`test_control_423_delivers_ci_when_given_a_typed_device`）。所以 #423 不是缺陷源；#493 的空串是。

补丁要求：CI watch 携带 typed device ref，或 #423 为 CI 增加显式无设备 subject；二者在契约写清，不能为空串。

### 4.3 `#488 P0` · activation 把 monitor 的拒绝报告成成功

- `activation.py:234-237` 只读 `backlog`/`unknown_receipts`，丢弃 `state/exit/why/health`。
- `activation.py:258-259` 无条件 `BOUNDED_STOP, exit 0`。
- `activation.py:310-312` 的 `main()` 不传 `cursor_age_seconds`；`clock` 默认 `0.0`。

实测：无 config 时 `tick()` 返回 `BLOCKED_INPUT, exit 3`；activation 仍 `BOUNDED_STOP, exit 0`，`last_tick=0.0`、`stale=false`、`warnings=[]`。

**负控**：同 wrapper 在 `--monitor-entry batch_adapter:tick`（不是零参数）时返回 `TICK_FAILED, exit 2`，说明非零退出路径存在；blocked tick 走 0 是该路径的缺陷。

补丁要求：把 tick 的 `state/exit/why/health/warnings` 映射进 activation；`BLOCKED_INPUT`/`UNCONFIRMED` 不得 exit 0；`main()` 传真实 cursor age / last tick。

### 4.4 `#490 P0` · observation 协议签名与拒绝形状

- #493 调用 `observe_run(..., expected_boot_id=, expected_source_sha=)`（`sources.py:494-504`）。
- #490 接受 `source_sha`，无 watch 级 boot（`adapter.py:272-275`）。直接替换：`TypeError: unexpected keyword argument 'expected_boot_id'`。
- #490 授权失败 raise `SourceRefused("DENIED")`（`adapter.py:156-171, 276`）；#493 只把 `MonitorRefused` 当 watch 级拒绝，`SourceRefused` 会落进 generic exception → `UNKNOWN` + hard failure。

**负控**：`source_bridge.py` 最小桥接 + transcript fixture 能把 #490 `Observation.as_dict()` 送进 #493 真实 core 并 `DELIVERED` 一次；boot mismatch 时 0 投递。桥接不是修复，且丢弃 watch 级 boot 期望本身是缺口。

补丁要求：统一 kwargs；#490 增加 `expected_boot_id` 或把 boot 绑定明确放在 client 层；denial 返回 `DENIED` Observation 而非异常。

### 4.5 `#490 P0` · #490/#493 契约漂移

| 输入 | #490 | #493 |
|---|---|---|
| CloudCLI 完成且有 `lastAssistantMessage` | `UNKNOWN / NO_NATIVE_ACTIVITY`（`adapter.py:374-393`） | `TERMINAL_VERIFIED`（`sources.py:385-412`） |
| CI 在别的 SHA 上绿灯 | `UNKNOWN / IDENTITY_MISMATCH`（`adapter.py:323-326`） | `UNKNOWN / SHA_MISMATCH`（`sources.py:458-465`） |

两边 README 都声称状态/原因逐字节一致；上表两项不一致。补丁要求：选定一个 native-evidence 字段和一个 wrong-SHA reason，两边同时改并加交叉测试。

### 4.6 其它 CODE 观察

- **watch 级 boot 期望不进 durable fingerprint**：`sources.py:230-241` 的 identity `bootId` 取 client 的 `boot_id`，而 `sources.py:353-355` 的 boot 比较用 watch 的 `expected_boot_id`。两个只有 expected boot 不同的 watch 在 cursor fingerprint 里 boot 字段相同；watch key 仍能区分。payload 侧 boot 分开（实测 `run.bootId` 分别为 `boot-qa-1`/`boot-qa-2`）。
- **`watch_key` 用 `|` 拼接不转义**：`contract.py:166-175`。`session_ref` 合法含 `|`；若 device ref 也含 `|`，两个不同组合可能拼出同一 key。
- **`backlog` 只数非终态**：`TERMINAL_VERIFIED` 但交付 `DELIVERY_UNKNOWN` 的 watch 不计入 backlog，容易被 §4.1 的 `SETTLED` 掩盖。
- **#494 `composition.json` 的 `notifier.head` 是旧的 `d9b2948`**，而同一对象的 `blob` 是 `7df27cb`（`4fc9fcb`）。代码正确，记录字段过期；建议 RC Owner 修正记录，避免下一个 QA 再 pin 错。

## 5. 历史 QA 与 #495 的更正

- **#495 v1 `NOTIFIER_SEAM_MISSING`**：pin artifact，已撤销。v1 的 #423 `d9b2948` 不是 RC 的 notifier；RC 是 `4fc9fcb`/blob `7df27cb`。
- **#485 `BLOCKED_UPSTREAM_SOURCE`**：源层已解除（包存在于 RC）；REAL_DEVICE/PRODUCTION 仍 NOT_RUN。
- **#491 `BLOCKED_RUNTIME_ENTRY`**：入口层已解除（零参数 tick 可运行）；supervisor/ledger 断点仍存。
- 两项历史 BLOCKED 不得被“27 pass”刷绿：本文件保留其历史结论，只追加分层证据。

## 6. 分层与 CI

| 层 | 状态 |
|---|---|
| CODE | 部分通过；1 个误报撤销，5 个真实断点保留 |
| UNIT | 27 passed + 5 xfailed（跨 PR）；#494 目标套件 24/26/24+18/35+11/70 |
| CI | 本地 hermetic exit 0；hosted #495 见 §6.1；#494 hosted 4/5 |
| REAL_DEVICE | **NOT_RUN** |
| PRODUCTION | **NOT_RUN**；Legacy 0.1.0 未触碰 |
| Timer / systemd | **NOT CLAIMED**；未安装、未 enable、未 systemctl |

### 6.1 Hosted CI

- **#494 Wave 14 RC**：run `40669351645`，**4/5**；shard4 baseline gate `25 failing, 2 new, 1 flaky`，两个 new 是 #490 的 request naming（#496 记录，本 QA 未把它们加入 baseline）。
- **#495 Wave 15 续作**：见本 PR checks。若 shard4 仍因既有 Lark/命名失败而红，本文件不把它算作 QA 通过；QA 文件所在的 shard 与 5 个 xfailed 的读数是分开报告的。

## 7. 边界

- 没有 main merge、force push、`git add .`、reset/clean/stash 其它 Agent 的 WIP。
- 没有编辑 #493/#488/#490/#423 的源码或测试；没有抢作者修复。
- 没有真实 Feishu/IM 发送；provider 是文件 journal fake。
- 没有付费模型调用；summarizer 未启用。
- 没有旧 Fleet/plugin/端口/密钥/OAuth 改动；没有安装/启用 timer 或 systemd unit。
- 没有停止其它 Agent。
- 没有把 #490/#488 的源码复制进生产路径；组合由 `compose.py` 按 SHA 取树，桥接只在 QA 目录并明确标注。
