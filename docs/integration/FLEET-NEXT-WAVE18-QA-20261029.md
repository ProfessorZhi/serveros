# Fleet Next · Wave 18 独立安全/运行闭环 QA（2026-10-29）

**结论：Wave16 的 3 个 xfail 已全部真实转绿，且没有用 skip/xfail 保留；本轮 QA 套件 43 passed / 0 xfail / 22 subtests。硬验收项全部通过；仅剩 1 个 P1 协议缺口（transcript 在 #493 离线客户端不可表示、在 #499 compat 可用），不是 P0。**

组合为 **#500 Wave16 RC + #503 + #504** 的真实 Git merge。RED baseline 用 #500 原树复现四项旧行为；mutation control 删除 #504 的一行修复后能重新触发旧缺陷。Legacy Fleet 0.1.0 未改动；无 main merge、生产部署、Timer/systemd、真实 Feishu/IM、付费模型、Creator、OAuth/密钥或设备操作。

## 1. 精确输入与组合

| PR | head | 角色 |
|---|---|---|
| #500 Wave16 RC | `80220a4ad1b009946f11bda0ce96dd8ff8e05468` | base；已含 #497/#498/#499 |
| #503 runtime-tick ABI | `b7f1f6134a1e3dcee4b916068c693ff059c097bd` | merge `f8f6724` |
| #504 monitor P0/P1 | `6286bfbc885771a45415efcb33405bb21a841fde` | merge `fffafbe` |
| #501 前序 QA | `5ef2f42a5f1a7b24b40cca36252038ac19a289f3` | 3 个 xfail 的来源 |
| #499 adapter compat | `02027c53f59100fbf43e0ac1f6d84f58561ee91c` | 已在 #500 |

独立 worktree：`worktrees/serveros/SRV-0011-fleet-wave18-crosspr-qa-20261029`。`git merge-tree` 预检无冲突；真实 merge 后作者源码/测试未编辑。

### 1.1 作者 blob 校验

`compose.FIX_BLOBS` 逐字节校验：

| PR | 文件 | blob |
|---|---|---|
| #503 | `tests/.../test_fleet_next_agent_monitor_activation_runtime_tick.py` | `a71d4ffc…` |
| #504 | `fleet_next_agent_monitor/contract.py` | `50d582b2…` |
| #504 | `core.py` | `33ce9aa0…` |
| #504 | `notifier.py` | `6a0c16c4…` |
| #504 | `sources.py` | `d8a3a551…` |

#500 的 notifier 仍含 #460 `send_text`；本轮没有重复报 `NOTIFIER_SEAM_MISSING`。

## 2. RED baseline（#500 原树）

`baseline_probe.py` 复现四项旧行为，exit 0 表示全部复现：

| 旧行为 | #500 实测 |
|---|---|
| 未授权 typed CI device + 已授权 project | `SETTLED`，`delivered=1`，journal/ledger 均为 `device:victim` |
| expired / unknown | `TICKED` exit 0，`backlog=1`，supervisor 看到 0 |
| delivery-id-only + native runId | `UNCONFIRMED` exit 4，`MilestoneRefused: run.runId is required` |
| delivery-id-only 无 runId | 同上，且不是具名拒绝 |

完整 baseline 输出见 `baseline_probe.py`；当前 QA 套件中的 `test_baseline_500_reproduces_the_four_old_defects` 断言这四项。

## 3. 当前组合结果

```bash
python3 -m pytest -q -p no:cacheprovider tests/test_fleet_next_wave18_crosspr_qa.py
# 43 passed, 22 subtests passed — exit 0, 0 xfail, 0 skip

python3 scripts/hermetic_tests.py tests/test_fleet_next_wave18_crosspr_qa.py -q
# 43 passed, 22 subtests passed — exit 0
```

作者目标套件在同一组合树：

```
tests/test_fleet_next_agent_monitor_core.py
tests/test_fleet_next_agent_monitor_activation*.py
tests/test_fleet_next_monitor_source_adapter.py
# 211 passed, 53 subtests passed
```

#503 的 3 个 runtime-tick 测试在 QA 套件里被**连续执行两次**，每次 `3 passed`，无 `skipped`：

```
tests/test_fleet_next_agent_monitor_activation_runtime_tick.py ...   [100%]
3 passed
```

### 3.1 Wave16 的 3 个 xfail → 普通 PASS

| Wave16 xfail | Wave18 对应测试 | 结果 |
|---|---|---|
| CI 未授权 device 伪造 subject | `test_an_ungranted_device_with_a_granted_project_is_zero_reads_zero_sends` | **PASS**（DENIED, 0 reads, 0 sends, `BLOCKED_CI_SUBJECT`, exit 3） |
| expired/unknown 到 supervisor exit 0 | `test_expired_unknown_and_never_issued_are_unconfirmed_nonzero` | **PASS**（UNCONFIRMED exit 4；RUNNING 仍是 TICKED exit 0） |
| delivery-id-only 不可投递 | `test_a_delivery_id_only_watch_aligns_with_a_native_run_id` / `..._without_a_run_id_is_named_refused` | **PASS**（native runId → DELIVERED；无 runId → `BLOCKED_RUN_IDENTITY` exit 3、0 send） |

没有把 `expectedFailure` 改成 skip，也没有减少断言；旧断言被替换成同强度、非恒真的普通测试。

### 3.2 Mutation control

`mutation_probe.py` 把 HEAD 树复制到临时目录，只删除一行修复，然后跑真实 monitor：

| mutation | 结果 |
|---|---|
| `ci-device`：删 `sources.observe_ci` 的 `grant.permits(device_ref=)` 检查 | 旧缺陷复现：`device:victim` `DELIVERED`，journal/ledger 都指向 victim |
| `unobserved`：把 `STATUS_UNKNOWN` 加回 `LIVE_STATUSES` | 旧缺陷复现：expired → `TICKED` exit 0 |

两个 mutation 都被 QA 套件的对应断言捕获。

## 4. 硬验收矩阵

| 要求 | 证据 | 判定 |
|---|---|---|
| CI 同时校验 project 与 device 两重 grant | 已授权 project + 未授权 device → DENIED，0 reads，0 sends，`BLOCKED_CI_SUBJECT`；已授权 device + 未授权 project → DENIED，0 reads；两者都授权 → 按 declared device 投递；只给 project → `device:ci:<project>` | **PASS** |
| UNKNOWN / EXPIRED / NEVER_ISSUED 非 0 | 三者均 `UNCONFIRMED` exit 4，`backlog=1`，warning `UNOBSERVED` | **PASS** |
| RUNNING 不被误当 FAILED | tick `TICKED` exit 0，status `RUNNING`，counts `RUNNING`，无 `UNOBSERVED`；activation `TICKED` exit 0 | **PASS** |
| 只有 native assistant activity 或绑定 SHA 的 artifact 才 VERIFIED | `lastAssistantMessage` → VERIFIED；`artifacts[{verified:true,sourceSHA:<expected>}]` → VERIFIED；无输出 / unverified / 错 SHA → UNKNOWN、0 send；note veto → UNKNOWN | **PASS** |
| DELIVERY_UNKNOWN 连续两次不 SETTLED | 2 个真实 activation 进程均 `UNCONFIRMED` exit 4，ledger `DELIVERY_UNKNOWN`，state `UNCONFIRMED` | **PASS** |
| 后续真实 DELIVERED 允许成功 | 第 3 个进程 `reconcile` 同 key → `DELIVERED`，tick `SETTLED` | **PASS** |
| 错 delivery_key 不匹配 | 返回 `IDENTITY_MISMATCH` / `UNCONFIRMED`；无 key 的 DELIVERED → `DELIVERY_UNKNOWN` / `UNCONFIRMED` | **PASS** |
| 重启幂等一次通知 | distinct `idempotency_key` 数 = 1（`ms1:` + 46 hex）；删除 state 后再跑不新增 journal | **PASS** |
| delivery-id-only 安全对齐或具名拒绝 | 有 native `runId` → DELIVERED，payload runId 正确；无 `runId` → `BLOCKED_RUN_IDENTITY` exit 3，0 send，why 明确“不合成 id” | **PASS** |
| 真实 runtime_tick 三测试全部执行且稳定 | 连续两次 `3 passed`，0 skipped，0 xfail | **PASS** |
| shadow create/send = 0 | `WriteTrap` 客户端 `reads=1, writes=0`，同一 tick `delivered=1`；create/send 若被调用会立即 AssertionError | **PASS** |
| bootId/instanceId/deviceRef/runId/deliveryId/sourceSHA 不串 | 两 run 的 journal/ledger/fingerprint 各带自己的 device/run/delivery/boot；instance mismatch → UNKNOWN、0 send；两 CI SHA 的 journal 与 state fingerprint 各带自己的 SHA，不互含 | **PASS** |
| 至少 3 独立 Python 进程 + file-backed fake | 2 个 activation 进程（unknown）+ 1 个 monitor 进程（reconcile deliver），共享文件 journal/ledger | **PASS** |
| Batch + Monitor 单一 ledger | Batch CLI member+summary 2 行；monitor `DUPLICATE`，无第 3 次发送 | **PASS** |

## 5. 仍存的 P1 协议缺口：transcript

- #499 `adapter.py:425` 的 `_has_native_evidence` 接受非空 `transcript`/`messages`。
- #493 `offline.py:27` 的 `RUN_KEYS` 是封闭集合，**不包含 `transcript`**；同一 fact 在 monitor 自己的离线路径上被 `REMOTE_ValueError` 拒绝。
- #493 `sources.py:399` 的 `_has_native_activity` 只认 `lastAssistantMessage`/artifact，不认 transcript。
- 实测：monitor 离线路径 → `UNKNOWN / REMOTE_ValueError`、0 send；同一 fact 经 **#499 compat view** → `TERMINAL_VERIFIED`、`DELIVERED` 一次。

这不是 Wave16 的 3 个 xfail 之一，也不是 P0；它说明 #493 与 #499 对“native activity”的形状仍未完全统一。Patch owner：**#493 与 #499** 选定一个 native-evidence 形状（或让 #493 的 offline client 接受 transcript 并在 classifier 中承认它），并加交叉测试。本 QA 不修改作者文件。

## 6. 输入 PR 的 hosted CI（继承失败）

| PR | run | 结果 | 主要失败 |
|---|---|---|---|
| #500 | 40694269346 | **3/4** | shard3 `17 failing, 3 new`：正是 #503 修的 3 个 runtime-tick 测试 |
| #503 | 40702481188 | **0/5** | 4 个 shard 全红；`plugin/bundle.yaml: does not declare l2-node`，new 62（shard3 示例）；继承自 #498 base。它的 3 个目标测试在真实组合里通过 |
| #504 | 40702652945 | **3/4** | shard4 `13 failing, 1 new` = 既有 `test_direct_lark_notify.py::CardCreateThenPatchTest::test_create_returns_an_id_and_patch_names_it` |
| #501（前序 QA） | 40695554409 | 6/8 jobs | shard1 RC manifest、shard3 #498 runtime tests；本轮 #503/#504 正是修这两处 |

不能据“作者说修好了”签核：本轮用真实 merge + 作者 blob + baseline/mutation 证明；#503 的自身 CI 仍 0/5（继承 l2-node），#504 的 shard4 仍红（既有 Lark）。

## 7. QA PR 的 CI

见本 PR checks；QA 套件所在 shard 单独报告。若 hosted CI 因组合后的 RC manifest（#500 ledger 仍按 80220a4 记录，而本分支加入 #503/#504）或既有 Lark 失败而红，本文件保留红色，不把 QA 的 43 pass 说成全仓通过。

## 8. 分层与边界

| 层 | 状态 |
|---|---|
| CODE | 硬验收项全部通过；1 个 P1 transcript 协议缺口 |
| UNIT（QA） | 43 passed / 0 xfail / 22 subtests；baseline 四项复现；mutation 两模式复现 |
| UNIT（authors, composite） | 211 passed + 53 subtests；runtime-tick 3 passed ×2 |
| CI | 输入 PR 未全绿；见 §6/§7 |
| REAL_DEVICE | **NOT_RUN** |
| PRODUCTION | **NOT_RUN**；Legacy 0.1.0 未触碰 |
| Timer / systemd | **NOT CLAIMED**；未安装、未 enable、未 systemctl |

边界：无 main merge、force push、生产部署、真实 Feishu/IM、付费模型、Creator、OAuth/密钥、设备操作；未编辑 #500/#503/#504/#499/#501 的源码或测试；只读作者 worktree。`#501` 的 3 个过时 xfail 没有被修改（那是另一份 QA 文件），本轮在新路径下用同强度普通断言替代并验证。
