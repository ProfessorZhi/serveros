# Fleet Next · W19 独立 Mainline QA（2026-10-29，W19E 更新）

**当前结论：本 QA gate 现在直接跑在 main `44f039d70dc4f593ea9db73aae873be3455fc48c` 上；本地 50 passed / 0 xfail / 0 skip / 34 subtests。P1 transcript 仍是 `BLOCKED_SOURCE`（#522 未进 main），本 gate 只证明 main 当前行为，不签 release。**

本文件是自有 QA 路径；没有修改产品源码、Legacy、生产、密钥或被人的 worktree。

## 1. 时点日志（不可误读）

这些是**历史时点**，不是当前状态：

| 时点 | main / 事件 | 当时结论 |
|---|---|---|
| W19 初始 | main `3bc7adf`；#520/#519 尚未合；#517 旧 head `823a381` | 旧 #517 报告写 `main@3bc7adf / NO_LANDING_PR`；FJ 在 #515 comment `6489682963` 对旧 head 标 `SAFE_MERGE_EXACT_HEAD`（9 个纯新增 QA 文件、旧 5/5、merge-tree 0 冲突） |
| #520 合入 | merge `99c06fab07bd301e7df1ff7fa253f670b4d51d4d` | monitor source 47 files 落 main |
| #519 合入 | merge `b27c93e346d5503a75f95ec068c386b416841a62` | tests/docs/contract 20 additions 落 main |
| #521 合入 | merge `44f039d70dc4f593ea9db73aae873be3455fc48c` | W19 DAG/provenance ledger 落 main；W19D signed `W19D_SIGNED_PASS_EXACT_HEAD #521 683ef961…`（#515 comment `6489262104`） |
| 当前 | **main `44f039d`，push CI `40827808966` 5/5** | 本 QA 的当前基线 |

旧 `main@3bc7adf / NO_LANDING_PR` 与“47-file candidate fixture”描述**已过期**；保留在此仅作历史审计记录，不再作为当前 gate 的输入。

## 2. 当前 gate

`compose.build_candidate()` 不再 overlay #507：

```
candidate == current main 44f039d
#507 05da04f still fetched for archive/veto comparison only
```

若 main 缺少 allowlisted monitor 目录或 runtime-tick 测试，gate 抛 `BLOCKED_SOURCE`，不制造 overlay 绿树。

命令：

```bash
python3 -m pytest -q tests/test_fleet_next_w19_mainline_qa.py
# 50 passed, 34 subtests passed — exit 0, 0 xfail, 0 skip
```

保留的 #508 43 个独立断言与三进程负控全部在 main 上真实运行；没有 skip/xfail/文件存在性假绿。

## 3. 当前验收

- 43 个 #508 QA 断言 + 7 个 W19 mainline/ledger/负控测试；
- #503 runtime-tick 3 tests 连续两次 `3 passed`，无 skip；
- mutation control 2/2：删 device grant 检查 → victim 重新 DELIVERED；`STATUS_UNKNOWN` 回到 `LIVE_STATUSES` → expired 重新 TICKED exit 0；
- #483 main survival：70 passed；
- #500 RED baseline：四项旧行为仍可复现；
- CI project + device 双重 grant、UNKNOWN/EXPIRED/NEVER_ISSUED 非 0、RUNNING 不误当 FAILED、DELIVERY_UNKNOWN 不 SETTLED、delivery-id-only native/具名拒绝、identity 不串、shadow create/send 0 均在 main 上通过。

## 4. P1 transcript：`BLOCKED_SOURCE`，不是 release PASS

main 当前（`44f039d`）没有 #522 的 core+adapter transcript 统一修复：

- #493 monitor 的离线 `RUN_KEYS` 不接受 `transcript`，同一 fact 在 monitor 路径是 `UNKNOWN / REMOTE_ValueError`、0 send；
- #499 compat view 接受 transcript 并可 DELIVERED。

本 gate 的测试**明确断言这个不一致**，不把 transcript 默认为 VERIFIED/DELIVERED，也不把它藏进 xfail。只要 #522 未进 main，release 结论为 **`BLOCKED_SOURCE`**；W19E 不签发布。

## 5. #521 与 #517 的签署关系

- #521 的 `W19D_SIGNED_PASS_EXACT_HEAD` 由独立 W19D reviewer 在 #515 comment `6489262104` 给出；本 QA 不重复签署。
- **#517 不能自签。** 本文件作者是 #517 QA Owner；新 head 仍需独立后续 signer（FJ/W19E reviewer 或 Captain 指定的独立 reviewer）执行 exact-head 审核并签 `SAFE_MERGE_EXACT_HEAD`。
- Hosted CI 以本 PR 新 head 的 main-based 5/5 为准；旧 head `823a381` 的 5/5 不可替代。

## 6. 边界

- 只改 `integrations/fleet-next-w19-mainline-qa/**`、`tests/test_fleet_next_w19_mainline_qa.py`、本文件；PR diff 仅 9 个 **新增** QA 文件。
- 未修改产品源码（Dell/FJ 作者）、Legacy Fleet 0.1.0、生产、密钥、branch 删除或其它 worktree。
- 未 merge/close/mark ready；无真实设备/IM/timer/模型/GPT scheduled task。
- REAL_DEVICE **0/3**，release **BLOCKED**；main source landing 不等于 release。
