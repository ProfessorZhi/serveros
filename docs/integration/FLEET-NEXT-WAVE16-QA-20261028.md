# Fleet Next · Wave 16 独立跨 PR 安全回归 QA（2026-10-28）

**结论：三个 P0 修复在真实组合里大部分转绿；仍发现 2 个新的 P0/接口缺口，不能签 V1。** 组合为 **#494 RC + #497 + #498 + #499** 的真实 Git merge。RED baseline 用 #494 原树复现四个旧缺陷；当前组合为 **31 passed + 3 xfailed**（QA 套件），另有 **3 个组合新增的作者测试 failure**（#498 新测试文件），均单独列出，不混入 QA 的 pass。

Legacy Fleet 0.1.0 未改动；没有 main merge、force push、生产部署、Timer/systemd、Creator、OAuth/密钥、真实 Feishu/IM、付费模型或设备操作。

## 1. 精确输入与组合

| PR | 当前公开 head | 备注 |
|---|---|---|
| #494 RC | `00503f91b019ce342af52f38b19bb3aa21dfff55` | base `feat/fleet-next-rc-wave13-20261028` |
| #497 monitor receipt / CI subject | `3db8ebf107f83981965cc888269b3b90d89ecb72` | base #493 branch |
| #498 activation verdict | `0096ba695f6c7bcd35dbb5a82957a1f5631d9058` | base #488 branch |
| #499 adapter compat | `02027c53f59100fbf43e0ac1f6d84f58561ee91c` | base `main` |
| #495 QA（本分支前身） | `f600fe6622b1e6942de79c90dca66abfa5f2b7ff` | Wave15 |
| #496 prereq QA | `a34b811f11fe9b4a2db535a933309b47813c5811` | 证明 #494 带 `send_text` |

独立 worktree：`worktrees/serveros/SRV-0011-fleet-wave16-crosspr-qa-20261028`。三个 fix 与 #494 的 `git merge-tree` 均无冲突；真实 merge 提交：

```
e44abae  merge #497
4fe5204  merge #498
b9aea42  merge #499
```

作者源码/测试未编辑。QA 只新增本文件、`tests/test_fleet_next_wave16_crosspr_qa.py`、`integrations/fleet-next-wave16-crosspr-qa/**`。

### 1.1 不重复 `NOTIFIER_SEAM_MISSING`

#494 的 notifier 是 #460 `4fc9fcb`（blob `7df27cb`），已含 `send_text`。组合测试直接断言该文件与 `BatchAggregator` 构造成功；Wave15 的旧 #423 `d9b2948` 双重 pin 不再出现在本组合。**没有**再报 `NOTIFIER_SEAM_MISSING`。

### 1.2 作者 blob 校验

`compose.FIX_BLOBS` 固定了 #497/#498/#499 关键文件的 git blob id；`setUpModule` 逐字节校验，组合树必须是作者 head 的代码，而不是同名函数的重写：

| PR | 文件 | blob |
|---|---|---|
| #497 | `fleet_next_agent_monitor/core.py` | `f95e9a8e…` |
| #497 | `notifier.py` | `b29122e3…` |
| #497 | `state.py` | `8ff084be…` |
| #498 | `activation.py` | `200452f1…` |
| #499 | `adapter.py` | `28ad9baa…` |
| #499 | `compat.py` | `f14272e7…` |

## 2. RED baseline（#494 原树）

`baseline_probe.py` 用 #494 的旧 core/activation/adapter 复现四个旧缺陷，exit 0 表示四个都复现：

```json
{
  "unknown_delivery": {"tick1_state": "UNCONFIRMED", "tick1_exit": 4,
                       "tick2_state": "SETTLED", "tick2_exit": 0,
                       "tick2_receipt": "DUPLICATE"},
  "activation_blocked": {"state": "BOUNDED_STOP", "exit": 0,
                         "health": {"stale": false, "cursor_age": 0.0, "last_tick": 0.0}},
  "adapter_direct": "TypeError: LegacyReadOnlySourceAdapter.observe_run() got an unexpected keyword argument 'expected_boot_id'",
  "ci_payload": {"state": "UNCONFIRMED", "delivered": 0,
                 "receipt": "DELIVERY_UNKNOWN",
                 "why": "MilestoneRefused: device_ref is required"}
}
```

完整套件对 #494 的 baseline：`34 errors, exit 1`，首条 `BASELINE_RED: the Wave 16 fix blobs are absent or different`。这是 before 图，不是产品 pass。

## 3. 当前组合结果

```bash
python3 -m pytest -q -p no:cacheprovider tests/test_fleet_next_wave16_crosspr_qa.py
# 31 passed, 3 xfailed, 16 subtests passed — exit 0

python3 scripts/hermetic_tests.py tests/test_fleet_next_wave16_crosspr_qa.py -q
# 31 passed, 3 xfailed, 16 subtests passed — exit 0
```

3 个 xfailed 都是**新发现**，不是把 Wave15 的旧缺陷改小：

| xfail | 缺口 | owner |
|---|---|---|
| `test_a_ci_watch_must_not_deliver_under_an_ungranted_device` | CI watch 声明 typed `device_ref` 时未与 grant 的设备集合校验 | #497 |
| `test_an_unobserved_watch_must_not_exit_zero_at_the_supervisor` | expired/unknown run 的 tick 是 `TICKED` exit 0，supervisor 仍看到 0 | #493 / #498 |
| `test_a_delivery_id_only_watch_must_be_deliverable` | 只给 `delivery_id` 的合法 watch 无法形成 #423 payload | #497 / #493 |

Wave15 的 5 个旧 xfail 在组合里已成为**普通通过测试**（未 skip、未降级）：

| Wave15 xfail | Wave16 对应测试 | 结果 |
|---|---|---|
| unconfirmed 不得 SETTLED | `test_unknown_delivery_stays_unconfirmed_then_reconciles_once` | PASS |
| verified CI 必须到达 notifier | `test_a_granted_project_is_delivered_as_device_ci_project` | PASS |
| activation 不得把 blocked 当成功 | `test_a_blocked_tick_is_not_reported_as_bounded_stop` | PASS |
| #490 直接替换可用 | `test_the_direct_adapter_is_callable_by_observe_watch` | PASS |
| #490 denial 返回 DENIED | `test_denial_native_raises_and_compat_returns_a_denied_observation` | PASS |

## 4. P0 验收矩阵

| 要求 | 证据 | 判定 |
|---|---|---|
| #497 `DELIVERY_UNKNOWN` 绝不假 SETTLED | 2 个真实 activation 进程均 `UNCONFIRMED` exit 4；ledger `DELIVERY_UNKNOWN`；state `UNCONFIRMED` | **PASS** |
| 真实 #423 ledger 确认后才 green | 第 3 个进程 `reconcile` 同 key → `DELIVERED`，tick `SETTLED`；ledger 变 `DELIVERED` | **PASS** |
| 重复重启不二次通知 | 4 次运行 journal 的 distinct `idempotency_key` 只有 1 个（`ms1:` + 46 hex）；删除 state 后再跑仍 1 个 | **PASS** |
| receipt 身份不串 | 错 `delivery_key` → `IDENTITY_MISMATCH`、`UNCONFIRMED`；无 key 的 DELIVERED → `DELIVERY_UNKNOWN`、`UNCONFIRMED` | **PASS** |
| CI `device:ci:<project>` 授权/类型 | 授权 project → `DELIVERED`，文本/key 都是 `device:ci:serveros`；未授权 project → `DENIED`、0 reads、0 send；untyped declared device → `BLOCKED_CI_SUBJECT`、0 send | **PASS** |
| CI 不伪造授权提升 | watch 声明 `device:victim`、grant 仅 `device:tokyo-qa-1`、project 已授权 → 实际以 `device:victim` `DELIVERED` | **FAIL（新 P0）** |
| #498 blocked/UNCONFIRMED 非 0 | 无 config → `BLOCKED_INPUT` exit 3；provider unknown → `UNCONFIRMED` exit 4；`TICK_FAILED` exit 2 | **PASS** |
| #498 未知/失败/过期入口词非 0 | 注入 `UNKNOWN`/`FAILED`/`EXPIRED` → `UNRECOGNISED`，exit 非 0 | **PASS** |
| #498 stale/age 不默认 healthy | 无测量 → `stale=null`, `cursor_age=null`；wrapper 测量 7200 + 静默 success → `stale=true`, exit 4；真实 tick 自报 `stale:false` 时 producer 优先（见 §5.2） | **PASS（含 precedence 记录）** |
| expired/unknown 到达 supervisor 非 0 | 真实 expired/unknown watch → tick `TICKED` exit 0，activation `TICKED` exit 0、`backlog=1` | **FAIL（新 P0/接口）** |
| #499 签名/类型/denial/boot/SHA/lastMessage | 直接 `observe_watch` 可用；compat 返回 mapping；denial 为 `DENIED` 0 reads；boot mismatch `BOOT_MISMATCH`；native `IDENTITY_MISMATCH` vs compat `SHA_MISMATCH`；`lastAssistantMessage` 可 VERIFIED，无输出 UNKNOWN | **PASS** |
| 错设备/另一 boot/错 SHA/Draft/Running 负控 | 全部 UNKNOWN/OFFLINE/RUNNING，dispatch 0，journal/ledger 空 | **PASS** |
| 重复摘要/单一 ledger | Batch CLI member+summary 2 行；monitor 读 `DUPLICATE`，无第 3 次发送 | **PASS** |
| 只给 deliveryId 的 watch | 合法 config，但 #423 报 `run.runId is required`；`UNCONFIRMED` exit 4、0 send | **FAIL（P1 接口）** |

### 4.1 新 P0 · #497 CI typed device 未与 grant 校验

- `notifier.py:181-188`：CI watch 声明了 typed `device_ref` 就直接 `return declared`，没有查 `grant.devices`。
- `sources.py:416-424`：`observe_ci` 只授权 `project`，不授权 watch 上的 `device_ref`。
- 实测：`grant.devices=["device:tokyo-qa-1"]`、watch `device_ref="device:victim"`、project 已授权 → tick `SETTLED`、`delivered=1`，journal 文本和 ledger key 都是 `device:victim`。

补丁要求（#497）：CI 的 declared `device_ref` 必须与 `grant.devices` 校验，或彻底不允许 CI watch 覆盖 subject（只用 `device:ci:<authorized project>`）。回归：未授权 device + 已授权 project 必须 `BLOCKED_CI_SUBJECT`，0 send。

### 4.2 新 P0 · expired/unknown 到 supervisor 仍是 exit 0

- `core.py:321-323`：`backlog>0` 的 tick 是 `STATE_TICKED, EXIT_SETTLED`。
- `activation.py:98`：`TICKED: EXIT_OK`。
- 实测：`EXPIRED` 或 completed-without-native-output 的 watch → tick `TICKED` exit 0，activation `TICKED` exit 0、`completed=false`、`backlog=1`、warning `UNKNOWN_OBSERVATION/EXPIRED`。

补丁要求（#493 与 #498 协调）：要么 #493 对 backlog/UNKNOWN/EXPIRED 返回非 0（UNCONFIRMED），要么 #498 在 `backlog>0` 或 `UNKNOWN_OBSERVATION` 时把 `TICKED` 降为 `UNCONFIRMED`。回归：expired/unknown 经真实 tick 到 activation 必须非 0。

### 4.3 P1 · 只给 deliveryId 的 watch 无法投递

- `contract.py:155` 明确允许 run watch 只有 `delivery_id`；`notifier.py:247` 只取 `identity.runId` 或 `watch.run_id`，此时为 `None`；#423 报 `run.runId is required`。
- 实测：`TERMINAL_VERIFIED` 但 `UNCONFIRMED` exit 4，journal/ledger 空。
- 补丁要求（#497 或 #493）：用 observation native `runId`/`deliveryId` 形成 key，或在 config 阶段拒绝只有 deliveryId 的 watch，不能接受后永远不投递。

### 4.4 #498 的 `--cursor-age-seconds` precedence（记录，不判缺陷）

真实 tick 会自报 `stale:false`；#498 让 producer 的 boolean 优先于 CLI 传入的 7200。因此“operator 测量 7200 + 真实成功 tick”仍 `stale=false`。这不是默认 healthy（producer 有测量），但与 CLI 帮助的“operator 测量”措辞有张力。建议 #498 Owner 明确：producer 自报时以 producer 为准并在帮助里写明；或在两者冲突时取更坏值。QA 的 stale 负控使用静默 success row 验证 wrapper 自身测量路径。

## 5. 组合新增 failure（作者测试，非 QA 套件）

在组合树跑 #497/#498/#499 的目标套件：

```
193 passed, 3 failed, 49 subtests passed
```

3 个 failure 全在 **#498 新增的** `tests/test_fleet_next_agent_monitor_activation_runtime_tick.py`：

| 行 | 失败 | 原因 |
|---|---|---|
| 170 | `test_three_processes_report_the_blocked_tick_and_none_of_them_flattens_it` | 三次运行比较整个 document，包含 wall-clock `health.last_tick`，进程间必然不同 |
| 217 | `test_a_configured_tick_carries_its_vitals_through_the_wrapper` | 断言 `health.reported["backlog"]`，但真实 #493 tick 的 `health` 没有 `backlog`（`backlog` 在 tick 顶层），KeyError |
| 231 | `test_a_second_process_does_not_inherit_the_first_ones_verdict` | `ticks_report` 包含 `health.cursor_age`/`last_tick` 浮点，两个进程必然不同 |

这些测试在 **#498 单独树上 skip**（monitor 包不存在），所以 #498 的 hosted CI 不会看到；进入真实组合后才执行。它们是**测试缺陷**，不是产品缺陷，但意味着“#498 作者测试全绿”不能成立。Patch owner：#498。不要在本 QA 中修改它们。

## 6. 各输入 PR 的 hosted CI（继承失败）

| PR | run | 结果 | 主要失败 |
|---|---|---|---|
| #494 RC | 40669351645 | **4/5** | shard4 `25 failing, 2 new, 1 flaky`；2 new 是 #490 request naming（#496 记录） |
| #497 | 40677827346 | **4/5** | shard4 `13 failing, 1 new`；new = `test_direct_lark_notify.py::CardCreateThenPatchTest::test_create_returns_an_id_and_patch_names_it`（`'FAILED' != 'DELIVERED'`），与 #495 的既有 Lark 失败同族 |
| #498 | 40680961489 | **0/5** | 4 个 shard 全红；主错误 `plugin/bundle.yaml: does not declare l2-node yet the payload carries fleet_mcp/...`；new failures 14/49/62/58（按 shard），是 #488 base 的插件包继承问题，不是 activation diff |
| #499 | 40679387678 | **4/5** | shard4 `13 failing, 1 new`，同 #497 的 Lark 失败 |

#498 的 0/5 不能因为“作者说 activation 修好了”而忽略；它的分支基线本身未全绿。组合 QA 不把 hosted 4/5 当全链路通过。

## 7. QA PR 的 CI

见本 PR checks；QA 套件所在 shard 与作者测试失败所在 shard 分开报告。若 hosted CI 因 #498 的 3 个组合新增 failure 或既有 Lark 失败而红，本文件保留红色，不把 QA 的 31 pass 说成全仓通过。

## 8. 分层与边界

| 层 | 状态 |
|---|---|
| CODE | 三个 P0 大部分转绿；2 个新 P0/接口缺口 + 1 个 P1 |
| UNIT（QA） | 31 passed + 3 xfailed；RED baseline 34 errors / probe 四项复现 |
| UNIT（authors, composite） | 193 passed + 3 failed（#498 新测试文件） |
| CI | 见 §6/§7；输入 PR 未全绿，组合有新增 failure |
| REAL_DEVICE | **NOT_RUN** |
| PRODUCTION | **NOT_RUN**；Legacy 0.1.0 未触碰 |
| Timer / systemd | **NOT CLAIMED**；未安装、未 enable、未 systemctl |

边界：无 main merge、force push、生产部署、真实 Feishu/IM、付费模型、Creator、OAuth/密钥、设备操作；未编辑 #494/#497/#498/#499 源码或测试；只读作者 worktree。
