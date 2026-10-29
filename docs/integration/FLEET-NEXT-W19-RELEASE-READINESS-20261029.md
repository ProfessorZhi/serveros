# Fleet Next W19 — 主干/构件/回滚 独立验收与分支清理结论（2026-10-29）

发布门禁 Issue：**#515**。本文件是本车道（#514 独立发布门禁 Owner）对 W19 的
唯一结论记录。所有数字都可以用 `integrations/fleet-next-w19-release-acceptance/`
下的三个工具在相同 commit 上重算，证据文件是
`integrations/fleet-next-w19-release-acceptance/evidence/w19-acceptance-20261029.json`。

被测主干：`main @ 99c06fab`（`Merge pull request #520`）。

---

## 1. 结论

| 裁决 | 值 | 含义 |
| --- | --- | --- |
| `LANDING_VERDICT` | **SIGNED_PASS（READY）** | #519 可以合入 `main` |
| `RELEASE_VERDICT` | **BLOCKED** | 不能称 V1 / RELEASED |

两条裁决分开，是因为它们本就不同：#515 自己写着「开发合并 ≠ 正式
RELEASED」，而今天的六个门禁层里 REAL_DEVICE 是 `NOT_RUN`（0/3 台设备，本任务
禁止实机），另有三条 release 级 blocker。

**建议合入的是 #519，不是任一 Draft。** #519 相对 main 是纯新增 20 个文件、
0 个既有文件被改动、0 冲突（`git merge-tree --write-tree main p519` rc=0），
exact-head hosted CI 四片全绿且 `0 new`。合入后关闭 #516（其 head 是 #519 head
的祖先，13 个文件全部被 #519 包含）。

---

## 2. 六层门禁矩阵

每一层只问一件事：**这次 landing 自己的增量有没有把这一层弄坏**。一整仓是否
可发布是另一个问题，走 `scope=RELEASE` 的 finding，不混进这一层，否则两条裁决
就是同一个数字的两种说法。

| 层 | 裁决 | 证据 |
| --- | --- | --- |
| CODE | READY | #519 增量不修改任何既有源文件（`differing=[]`，`touched=[]`）；与当前 main 合并 rc=0、0 冲突 |
| UNIT | READY | #519 exact head `cd8b875b`：`Suite (shard 1..4/4)` 全 SUCCESS，四片均 `0 new` |
| CI | READY | 同一 head 的 hosted run 全绿；`mergeStateStatus=CLEAN` |
| INDEPENDENT_QA | READY | 本车道四条主动复验全部执行并记录在第 3 节（含负控） |
| ARTIFACT | READY | #519 增量不触及任何被构件 pin 覆盖的路径（wave18-artifact-builder / evidence 目录均不在增量内） |
| REAL_DEVICE | **NOT_RUN** | 0/3 台设备；本任务禁止实机门禁，因此记 NOT_RUN 而不是绿 |

其余 11 个 PR 的 CI 读数（同一时刻抓取）：

| PR | head | 结论 | 备注 |
| --- | --- | --- | --- |
| #516 | `15d82ab3` | 4/4 SUCCESS | 被 #519 包含 |
| #517 | `823a3819` | 4/4 SUCCESS | 纯新增 9 个文件 |
| #519 | `cd8b875b` | 4/4 SUCCESS, CLEAN | 建议合入 |
| #506 | `30d7c4d3` | shard 4/4 FAILURE `13 failing, 1 new` | 见第 4 节 |
| #512 | `d3c7c391` | shard 4/4 FAILURE | 同上 |
| #513 | `6602961d` | shard 4/4 FAILURE `17 failing, 1 new` | 同上 |
| #514 | `30c4581a` | shard 4/4 FAILURE `13 failing, 1 new` | 同上（本车道上一个 PR） |
| #518 | `d7045619` | shard 4/4 FAILURE `13 failing, 1 new` | 同上 |
| #508 | `fb8c4356` | shard 2/4 FAILURE `12 failing, 2 new` | 真·自身增量：`test_fleet_next_rc_wave16_ledger` |
| #507 | `05da04f8` | 4/4 SUCCESS | 但改写了 main 已有的 74 个文件 |
| #483 | `644ee4a8` | 已 MERGED（旧 run 4/4 FAILURE） | 结论为 RELEASE_NOT_RUN |

---

## 3. 四条主动复验（不看别人说"修好了"，只看自己跑出来的数）

### 3.1 `#509` 的五个构件摘要能否重建 —— 能，在敌意宿主上也能

在 `core.autocrlf=true` 的环境里、于 #518 的 head `d7045619` 上跑 builder，五个
摘要全部逐字节重建、退出码 0：

```
ace00295…authority  4ca314f5…hub  82983d7f…composition  381dbd8e…node  333caea1…runtime-base
```

**负控（证明这个量具会翻）**：用 #509 旧的期望值去校验同一棵树，退出码 3、
且只有一条不匹配 —— `composition` 期望 `2a0933b9…`。原因是 #518 给
`ARTIFACT.json` 加了 `install` 块（`independently_installable: false` 及理由），
这是「可复现性」PR 里一次有据可查的内容变更。三个负控各自 `boot=False`。

结论：#509 的 blocker 被 #518 修复，且修复是可验证的；#509 本身被 #518 按祖先
关系取代（`successor=518`）。

### 3.2 `#506` 的「升级中途被中断后能否 revoke、有没有孤儿」—— 在 #513 的实现上不复现

18 个命名的步骤、**每一步一个独立进程**、世界目录在 tempfile 下，18/18 `rc=0`：

| 场景 | 实测 |
| --- | --- |
| `fail_at="after-files"` | `BLOCKED_ABORTED`，root 与 v1 **逐字节相同**（含 receipt 与 marker），无 staging、无 in-root lock、无 backup 残留；随后 revoke `OK`，恰好删 2 个文件，`leftovers=[]` |
| `crash_at="after-files"` | `SimulatedCrash` 后进程结束；revoke 拒绝 `BLOCKED_CONCURRENT_WRITER`；`recover()` 从 `.sidecar-backup/0.1.0+wave18` 恢复两个文件、丢弃 staging、删除锁 → 与 v1 逐字节相同；revoke 再次 `OK`、零残留 |
| `crash_at="after-marker"` | 同上，且 `.sidecar-owner.json` 被逐字节重建（`next_backend_id` 没丢） |
| 外来文件 | `BLOCKED_FOREIGN_PRESENT`，在任何写入之前；revoke 拒绝且一个都不删；清掉外来文件后 revoke `OK` |
| 陈旧锁（死 pid） | `BLOCKED_STALE_WRITER`；只有 `recover()` 能清锁，之后 revoke `OK` |
| 活锁（活 pid） | install / revoke / recover 三者全 `BLOCKED_CONCURRENT_WRITER`，root 分毫未动 |
| **负控** | 一次**成功**的升级会改变 5 个指纹条目；`rollback_to_previous()` 把树还原到与 v1 逐字节相同（marker 也算），revoke 不留东西 |

负控的意义：如果连「成功升级」都不能让指纹变化，那么上面所有「逐字节相同」
都等于什么都没证明。

### 3.3 `#508` 的 P1（transcript 两条路径不一致）—— 在 main 上仍然存在

同一份完成态 payload，分别用两个读取器自己的代码去读：

| 读取 | 结果 |
| --- | --- |
| adapter `_has_native_evidence`（`adapter.py:425`） | `True` → `TERMINAL_VERIFIED` |
| monitor `_has_native_activity`（`sources.py:399`） | `False` |
| monitor 的离线 fixture（`offline.py` `RUN_KEYS`） | `ValueError: unknown fixture key(s): ['transcript']` |
| **两个正控**（`lastAssistantMessage` 这种两边都认的写法） | 两边都 `True` |

在 PR 的两个 head 上、以及在 `main @ 99c06fab` 上，结论一致
（`two_paths_disagree=True, controls_agree=True`）。正控是关键：它证明这两个
读取器不是"一个恒真一个恒假"。

更值得记的是：#519 自己新增的
`tests/test_fleet_next_monitor_source_adapter.py::...::test_completed_with_a_transcript_is_terminal_verified`
把 adapter 的这半边**当成正确行为断言下来**，而 monitor 那半边永远看不到
transcript。两套测试从不交汇，所以全绿与这条分歧可以同时成立。

### 3.4 `#483` 的 legacy fake 在新组合前后是否漂移 —— 不漂移

`integrations/fleet-next-legacy-safety/legacy_safety.py` 的 blob 在 main 与 #519
head 上是同一个 `5204be95…`；两边各跑 70 条 legacy survival 测试全过；junit XML
逐节点比对 → **70 个 node id 完全相同、0 条结果差异**。

---

## 4. 旧分支「1 new」的真因（实测，非推断）

结论先说：**不是这些分支的内容坏了**。它们的 CI 跑的是
`refs/pull/<N>/merge`，而那些 merge ref 的**第一父提交停在一个旧 base 上**。

| ref | merge ref 第一父 | 该文件里修复的行数 |
| --- | --- | --- |
| `refs/pull/506/merge` | `e69fc033` | 1 |
| `refs/pull/512/merge` | `e69fc033` | 1 |
| `refs/pull/514/merge` | `e69fc033` | 1 |
| `refs/pull/518/merge` | `3520d0c8` | 1 |
| `refs/pull/513/merge` | `30d7c4d3` | 1 |
| `refs/pull/519/merge` | `15d82ab3` | 3 |
| `main @ 99c06fab` | — | 3 |

修复是 `dd5919a9`（2026-10-27 落地，早于上述所有 run）。这些 merge ref 是在
base 还旧的时候建的，之后没有重建，所以它们跑的那棵树里
`tests/test_direct_lark_notify.py` 仍是修复前的版本 —— 这个文件在
`differing` 里出现，但**这些分支谁都没改过它**（`touched=[]`）。

**证明这不是"分支的问题"**：把 #513、#514、#518 与**当前 main** 做
`git merge-tree --write-tree`，三者都 rc=0、0 冲突，且合并结果里那个文件是
修复后的版本（3 行）。也就是说，重定向到 main 之后这条红不会存在。

**#507 / #508 要分开看**：#507 自己改写了 main 已有的 74 个文件
（`touched=74`），#508 同样 74 个，且 #508 的 2 条 new failure 是
`test_fleet_next_rc_wave16_ledger` —— 那是它自己的增量造成的，不是 merge ref
的陈旧。这两条判 `BLOCKED_OWNER` 是名副其实的。

同时必须诚实记一笔：**W18 那次 `BLOCKED_CI_NEW_FAILURE` 的成因主要是这个**，
而不是当时那棵树本身。W18 的结论在"不该放行"上没错，但理由要改写成这里写的
这一条。

---

## 5. PR 退场表（逐条由 `w19_measure.py` 量出）

列含义：`absent` = 只在 PR 里有、main 里没有的文件数；`differing` = 两边都有
但字节不同的文件数；`touched` = PR **自己动过**（相对它的 merge-base）且 main
也有的文件数。

| PR | 分类 | absent | differing | touched | successor |
| --- | --- | --- | --- | --- | --- |
| #483 | RELEASE_NOT_RUN | 0 | 1 | 0 | main |
| #506 | SUPERSEDED_ANCESTOR | 5 | 1 | 0 | **#513** |
| #507 | BLOCKED_OWNER | 149 | 74 | 74 | main |
| #508 | BLOCKED_OWNER | 153 | 74 | 74 | main |
| #509 | SUPERSEDED_ANCESTOR | 25 | 1 | 0 | **#518** |
| #512 | OWN_DELTA_PENDING | 5 | 1 | 0 | main |
| #513 | OWN_DELTA_PENDING | 10 | 1 | 0 | main |
| #514 | OWN_DELTA_PENDING | 9 | 1 | 0 | main |
| #516 | SUPERSEDED_ANCESTOR | 13 | 0 | 0 | **#519** |
| #517 | OWN_DELTA_PENDING | 9 | 0 | 0 | main |
| #518 | OWN_DELTA_PENDING | 32 | 1 | 0 | main |
| #519 | OWN_DELTA_PENDING | 20 | 0 | 0 | main |

计数：3 SUPERSEDED_ANCESTOR / 2 BLOCKED_OWNER / 6 OWN_DELTA_PENDING /
1 RELEASE_NOT_RUN。

**可以建议关闭的（有祖先证据，关掉不丢任何 commit）：**

* #506 → 由 #513 承载（`30d7c4d3` 是 `6602961d` 的祖先；#506 的 5 个文件全部
  被 #513 的 10 个文件包含，且同 blob）。关闭时在该 PR 评论里写 successor
  `#513 / 6602961d`。
* #509 → 由 #518 承载（`3520d0c8` 是 `d7045619` 的祖先，且 #518 的 merge ref
  第一父就是 `3520d0c8`）。评论写 successor `#518 / d7045619`。
* #516 → 由 #519 承载（`15d82ab3` 是 `cd8b875b` 的祖先，13 个文件全被包含）。
  评论写 successor `#519 / cd8b875b`。

**不能关、也不能合（要先归还 owner 的）：** #507、#508 —— 它们改写了 main
已有的 74 个文件，属主不是本车道，合并会把别人的文件退回旧版本。

**其余待落地的是各自 owner 的事**：#512、#513、#517、#518、#519 各自只新增
自己声明的文件，重定向到 main 后逐条评估即可。本车道只对 **#519** 签
`SIGNED_PASS`。

---

## 6. 缺陷清单（点名 owner 与路径，不代改）

| # | 级别 | scope | owner | 路径 | 事实 |
| --- | --- | --- | --- | --- | --- |
| 1 | BLOCKER | RELEASE | fleet-next-agent-monitor + monitor-source-adapter | `fleet_next_agent_monitor/sources.py:399` vs `fleet_next_monitor_source_adapter/adapter.py:425` | 同一份完成态 payload，adapter 认 transcript、monitor 不认；两边各自有测试断言自己那半边，从未交汇（第 3.3 节） |
| 2 | BLOCKER | RELEASE | 构件 owner（#518 车道） | `integrations/fleet-next-wave18-artifact-builder/wave18_builder.py` | 五个摘要只在 #518 的 head 上可复现，该 head 不在 main 里；release 不能引用一个不存在的树上的可复现性 |
| 3 | BLOCKER | RELEASE | release owner | `0 of 3 devices` | 没有任何设备跑过这个构建；本任务禁止实机门禁，故记 NOT_RUN |
| 4 | NOTE | RELEASE | mainline owner | `integrations/fleet-next-agent-monitor/`（有源无测） | main 带着 monitor/adapter 的源文件却没有 #519 新增的那些测试，这正是缺陷 1 在 main 上没被发现的原因。落地 #519 是在补这个洞，不是在扩大它 |
| 5 | NOTE | RELEASE | fleet-next-agent-monitor | `fleet_next_agent_monitor/state.py:34` | 模块级 `import fcntl` 使该包在 Windows 上不可导入，52 个 Windows 失败里 45 个由此而来。产品跑在 Linux，故记为可移植性备注 |

---

## 7. 什么情况下 RELEASE_VERDICT 会变成 PASS

三条同时满足，缺一不可：

1. 缺陷 1 被一个有名字的 owner 修掉，并且**有一条跨路径的测试**同时约束两个读取器
   （只各自修各自的半边不算 —— 那正是今天的状态）。
2. #518 落进 main，且有人**在 main 上**重跑一次五个摘要的重建并附输出。
3. REAL_DEVICE 层由 release owner 在单独授权的任务里跑到 3/3，并把每台设备的
   读数贴回 #515。

在那之前，任何"V1 已上线"的说法都不成立。

---

## 8. 本车道没有做的事

没有合并 main、没有关闭任何 PR、没有删除任何分支、没有安装新版、没有切换旧版、
没有调用 Plugin Creator、没有动 service/systemd、没有碰 OAuth/密钥/Tailscale、
没有发真实 Feishu/IM、没有花任何付费模型的钱、没有跑任何实机或生产门禁。
本文件与 `integrations/fleet-next-w19-release-acceptance/**`、本测试文件是本车道
唯一写入的字节。
