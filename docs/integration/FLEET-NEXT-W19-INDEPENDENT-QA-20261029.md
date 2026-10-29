# Fleet Next · W19 主干合并独立 QA（2026-10-29）

**结论：`main` `3bc7adf` 的已合并内容是 SIGNED_PASS；W19-B 的 Fleet Next 主线 landing 为 `BLOCKED_OWNER / NO_LANDING_PR`。#507 的 5/5 是旧集成树，且相对 main 有 271 文件/67,976 行、68 个 forbidden path，不能无条件签主干。** 本轮建立了一个窄化、可复跑的候选 gate（47 文件，只含 Fleet Next monitor/activation/adapter/notify/batch/observer/runner/sources + 1 个 runtime-tick 测试），**50 passed / 0 xfail / 0 skip / 33 subtests**；它是 landing 的验收条件，不是 landing 本身的签署。

Legacy Fleet 0.1.0 未改动；没有 main merge、force push、真实安装/IM/timer、凭据/OAuth、付费模型或设备操作。REAL_DEVICE / PRODUCTION 均 **NOT_RUN**。

## 1. 当前主干与 PR 坐标

| 对象 | 精确 SHA | 状态 |
|---|---|---|
| `main` | `3bc7adfe40274741cc528c7dd96167d2b67b00ec` | 最新；push CI run `40803065087` **5/5 success** |
| #415 merge | `93d9a1d27d99fd1fac4531b6c8bc458feaf2fbc8` | 已合并，5/5 |
| #483 merge | `3bc7adfe…` | 已合并，5/5；四个纯新增文件 |
| #507 RC | `05da04f8c78f302b029c6304ed96f4e8ded5fc9b` | Draft；旧集成树，**不签主干** |
| #508 QA | `fb8c435637e486fc0ecf78a2ea83b9b323c936bb` | Draft；218 commits/279 files，**不整棵合并** |
| #514 release gate | `30c4581ac8989b26cb5a065b49bc5a868e48d57d` | **BLOCKED**（#506/#509 两项） |
| #515 | Issue | 尚无 public landing PR |

`#515` 是 Issue，不是 PR。本轮没有虚构 landing PR；`landing_check.py` 在无 PR 时返回 `BLOCKED_OWNER / NO_LANDING_PR`。

## 2. #507 / #508 与 main 的差分

| 对象 | 相对 main 的 delta | forbidden path |
|---|---|---|
| #507 全树 | **271 files / 67,976 insertions** | **68**：`native/` 25、`runtime/` 13、`plugins/` 10、`serverosd/` 8、`scripts/` 5、`fleet/` 3、`.github/` 1、`.claude-plugin/` 1、`plugin/` 1、`fleet_hub/` 1、`ARCHITECTURE.md` 1 |
| #508 QA 全树 | 218 commits / 279 files | 不能作为 QA 合并；只抽取 QA 自有测试 |
| W19 窄候选 | **47 files**：46 integration + 1 runtime-tick test | **0** |

#507 的 5/5 是旧集成树的 CI；它包含 runtime/plugin/serverosd/native/plugins/fleet/scripts 等生产/Legacy 路径，不能用 5/5 替代新 main 合成树 CI。

## 3. W19 窄候选（可复用 fixture）

`compose.build_candidate()` 从 exact commit 构造，不 merge 任何分支：

```
main 3bc7adf
  + #507 05da04f 的以下目录（逐字节）：
    integrations/fleet-next-agent-monitor
    integrations/fleet-next-agent-monitor-activation
    integrations/fleet-next-monitor-source-adapter
    integrations/fleet-v1-milestone-notify
    integrations/fleet-v1-milestone-batch
    integrations/fleet-v1-milestone-observer
    integrations/fleet-v1-milestone-runner
    integrations/fleet-v1-milestone-sources
  + tests/test_fleet_next_agent_monitor_activation_runtime_tick.py
```

不复制任何 `runtime/`、`plugin/`、`serverosd/`、`native/`、`plugins/`、`fleet/`、`scripts/`、`.github/`、`fleet_mcp/` 或其它生产路径。候选文件逐一与 #507 的 blob 比较；任何 allowlist 之外的路径是 **veto**，不是 warning。

main 的 `runtime/notifications.py`、`runtime/message_transport.py`、`fleet/transport.py` 与 #507 的 blob **完全相同**，因此候选无需触碰这些生产文件。

## 4. Gate 结果

```bash
python3 -m pytest -q tests/test_fleet_next_w19_mainline_qa.py
# 50 passed, 33 subtests passed — exit 0, 0 xfail, 0 skip
```

- 43 个 Wave18 独立 QA 测试原样保留强度（不是 skip/xfail）；
- 7 个 W19 mainline 测试：候选 blob 精确性、allowlist、#507 全树 veto、生产/Legacy byte-identity、main 无 monitor 的 RED baseline、#483 survival、landing verdict；
- #503 的 3 个 runtime-tick 测试 **连续两次 3 passed**，无 skip；
- 两个 mutation control：删 `sources.observe_ci` 的 device grant 检查 → `device:victim` 重新 DELIVERED；把 `STATUS_UNKNOWN` 加回 `LIVE_STATUSES` → expired 重新 TICKED exit 0；
- main 的 #483 survival：`70 passed`；
- #500 RED baseline：四项旧行为复现（CI victim、expired TICKED exit0、delivery-id-only 失败）。

### 4.1 硬验收矩阵（候选）

| 要求 | 结果 |
|---|---|
| CI project + device 双重 grant | PASS：未授权 device + 已授权 project → DENIED，0 reads/0 sends，`BLOCKED_CI_SUBJECT` exit3；未授权 project + 已授权 device → DENIED 0 reads；两者都授权 → 按 declared device 投递；只给 project → `device:ci:<project>` |
| UNKNOWN/EXPIRED/NEVER_ISSUED 非 0 | PASS：`UNCONFIRMED` exit4、`UNOBSERVED`；RUNNING 仍 `TICKED` exit0，不误当 FAILED |
| 只有 native activity / bound artifact 才 VERIFIED | PASS：`lastAssistantMessage`、绑定 expected SHA 的 verified artifact → VERIFIED；无输出/unverified/错 SHA/note veto → UNKNOWN 0 send |
| delivery-id-only 安全对齐或具名拒绝 | PASS：native `runId` → DELIVERED；无 runId → `BLOCKED_RUN_IDENTITY` exit3、0 send |
| DELIVERY_UNKNOWN 两次不 SETTLED | PASS；随后同 key 真实 DELIVERED → SETTLED；错 key/无 key 不 settle；重启 distinct key=1 |
| runtime_tick 3 真跑 | PASS：两次 `3 passed`，0 skip/xfail |
| shadow create/send 0 | PASS：`WriteTrap` reads=1 writes=0，同 tick delivered=1 |
| identity 不串 | PASS：boot/instance/device/run/delivery/sourceSHA 各带自己的值；instance mismatch → UNKNOWN 0 send；两 CI SHA 不互含 |
| ≥3 进程 + file-backed fake | PASS：2 activation + 1 monitor，共享文件 journal/ledger |
| 插件/backend/port/state/lock 路径碰撞 | **VETO**：候选 0 forbidden；#507 全树 68 forbidden（含 runtime/plugin/serverosd/native/plugins/fleet/scripts） |
| #483 fake survival 正反控 | PASS：main 上 `70 passed`（同一文件含 collision/refusal 负控与 survival 正控） |
| Legacy 既有契约 / main 原始 tree | PASS：候选不触 `fleet_mcp`/`runtime`/`plugin`/`serverosd`/`fleet`/`native`/`plugins`/`scripts`，与 main 字节一致；`fleet_mcp` tree hash 不变 |

## 5. 逐 SHA 裁决（#515）

| PR / 对象 | exact SHA | 裁决 | 依据 |
|---|---|---|---|
| #415 | head `dd5919a…` / merge `93d9a1d…` | **SIGNED_PASS**（已在 main） | main push CI 5/5；只改 Lark 测试窗口 |
| #483 | head `644ee4a…` / merge `3bc7adf…` | **SIGNED_PASS**（已在 main） | 4 个纯新增文件；70 survival tests；main CI 5/5 |
| #507 | `05da04f…` | **BLOCKED_OWNER**（不得整体 landing） | 271 files / 67,976 insertions；68 forbidden path；旧树 5/5 不可转移；需按模块窄 PR |
| #507 窄候选 | 基于 main + 47-file overlay（非 PR） | **CANDIDATE_FIXTURE_PASS** | 50 passed / 0 xfail / 0 skip；mutation control 2/2；见 §4 |
| #508 | `fb8c435…` | **BLOCKED_OWNER**（不得整棵合并） | 218 commits / 279 files；只抽取 QA 自有测试；P1 transcript 仍开放 |
| #514 | `30c4581…` | **BLOCKED** | release gate 两项：`BLOCKED_PARTIAL_UPGRADE_TRAPS_ROOT:#506`、`BLOCKED_ARTIFACT_NOT_REPRODUCIBLE:#509`；源码测试不能关闭 |
| W19-B landing | 无公开 PR | **BLOCKED_OWNER / NO_LANDING_PR** | 无 exact SHA 可签；需先有 main-based landing PR，再按 §3/§4 gate 重跑 |
| #515 | Issue | 主表；本轮只追加本报告 | — |

**只有 `SIGNED_PASS` 才允许 Integrator 执行用户本轮授权的 main merge。** 当前没有可签的 landing PR；#507/#508 不构成 SIGNED_PASS。

## 6. #514 两个 BLOCKED 与发布边界

- `BLOCKED_PARTIAL_UPGRADE_TRAPS_ROOT` → #506：中途 upgrade 留下 receipt 不拥有的孤儿文件，revoke 拒绝 `BLOCKED_FOREIGN_PRESENT`。
- `BLOCKED_ARTIFACT_NOT_REPRODUCIBLE` → #509：在 `ff514f4b` 重建只复现 1/5 digest。
- 这两项是 **发布门禁**，不是源码测试能绕过的；本 QA 的 50 pass 不改变 `RELEASE` 状态。
- 本轮只讨论 main **source landing**；REAL_DEVICE/PRODUCTION **NOT_RUN**，不安装、不启动 timer/服务、不真实 IM。

## 7. Hosted CI

- `main` push run `40803065087`：**5/5 success**（head `3bc7adf`）。
- 本轮 W19 QA PR 的 CI 见 PR checks；QA 套件与 main 基线分开报告。若 CI 出现 main baseline 之外的 failure，本报告保留红色，不把 50 pass 说成全仓通过。
- 已知 baseline：main 自身 5/5；#507 旧树 5/5 不作为 mainline 证据；#503 自身 0/5（l2-node 继承）、#504 shard4 既有 Lark、#508 前序 7/8，均为历史，不继承为 PASS。

## 8. 边界

- 未编辑 Integrator / Producer / RC owner 源码或 worktree；未修改 Legacy Fleet 0.1.0、`fleet_mcp/**`、旧 runtime/plugin/port/URL/OAuth/DB/state/lock/service。
- 未 main merge、force push、安装服务、启动 timer、真实 Feishu/IM、使用凭据/OAuth/付费模型。
- 新增文件仅在 `integrations/fleet-next-w19-mainline-qa/**`、`tests/test_fleet_next_w19_mainline_qa.py`、本文件；#508 的 43 tests 仅以本 QA 的自有副本运行，未改 #508 文件。
- 可复用 fixtures：`compose.build_candidate()`、`case_probe.py`、`baseline_probe.py`、`mutation_probe.py`、`landing_check.py`。有公开 landing PR 后，重跑 `landing_check.py`（`FLEET_QA_LANDING_PR`/`FLEET_QA_EXPECTED_SHA`）与同一 gate 即可给出该 exact SHA 的最终裁决。
