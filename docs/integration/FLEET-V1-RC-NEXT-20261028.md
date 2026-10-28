# FLEET V1 RC-NEXT（2026-10-28）— exact-SHA 集成树与两处最小 merge gate

主控：[#425](https://github.com/ProfessorZhi/serveros/issues/425) · 四线总收口：[#434](https://github.com/ProfessorZhi/serveros/issues/434)
上一轮 RC：[#449](https://github.com/ProfessorZhi/serveros/pull/449) @ `4db2e711`（其记录见 `docs/integration/FLEET-V1-RC-20261028.md`）

本文只记录**本机实测**过的东西。hosted CI 的数字不写在这里（新增测试文件会重排
`scripts/hermetic_tests.py` 的 4 个分片，跨树的分片读数不可比，见 §5.3）；CI 读数在
本 PR 的 check 与 #425/#434 的回执评论里。

## 0. 身份与边界

- 我是 **#414 / #422 的作者**，**不是** #413 / #433 / #436 / #423 / #459 / #410 / #407 / #457 的作者。
- 本轮**只新增**三处属于本车道的文件：
  - `integrations/fleet-v1-rc-next/**`
  - `docs/integration/FLEET-V1-RC-NEXT-20261028.md`（本文）
  - `tests/test_fleet_v1_rc_next_packaging.py`、`tests/test_fleet_v1_rc_next_notifier_seam.py`
- **没有**改任何原作者的源码，**没有**写 #449 的分支，**没有**动
  `tests/hermetic-known-failures.txt`（不扩假基线），**没有**合 main、改运行服务、
  触碰密钥/OAuth/飞书，也没有碰其他在跑的 Agent 的 worktree。

## 1. 25 个 PR 的 live SHA（复核于 2026-10-28，`gh pr view` 逐条）

| PR | live head | 状态 | base 分支 | head 分支 |
|---|---|---|---|---|
| #407 | `9538238d6a` | OPEN/Draft | `main` | `codex/fleet-v1-delivery-20261027` |
| #411 | `7c5b9b2ea9` | OPEN/Draft | `main` | `claude/cloudcli-compat-windows-20261027` |
| #412 | `696e823fa8` | OPEN/Draft | `main` | `feat/l1-serverosd-native-runtime-20261027` |
| #413 | `3e9428ab5f` | OPEN/Draft | `main` | `feat/l3-device-ability-build-decoupling-20261027` |
| #419 | `b798e32bec` | OPEN/Draft | #412 | `feat/l1-native-runtime-release-20261027` |
| #421 | `95375da719` | OPEN/Draft | #413 | `feat/l3-runtime-truth-face-20261027` |
| #423 | `d9b2948a62` | OPEN/Draft | `main` | `feat/fleet-v1-milestone-notify-20261027` |
| #426 | `6781b3aa23` | OPEN/Draft | #423 | `feat/fleet-v1-milestone-sources-20261027` |
| #428 | `dde957775c` | OPEN/Draft | #423 | `feat/l3-milestone-observer-20261027` |
| #431 | `517ef91507` | OPEN/Draft | `main` | `feat/fleet-v1-l2-release-separation-20261028` |
| #433 | `0ef980fbe1` | OPEN/Draft | #419 | `feat/l1-l2-release-kinds-20261028` |
| #435 | `673c766d04` | OPEN/Draft | `feat/l1-d89-native-takeover-20261028` | `feat/l1-d89-lease-writer-20261028` |
| #436 | `85493198df` | OPEN/Draft | #433 | `feat/l1-d89-closure-20261028` |
| #437 | `2d6eef1f59` | OPEN/Draft | #421 | `feat/l3-device-ability-local-20261028` |
| #440 | `f7dbf385f3` | OPEN/Draft | `integrate/fleet-v1-rc-20261027` | `fix/l3-observer-transport-key-20261028` |
| #446 | `9b1b1ad1b6` | OPEN/Draft | #440 | `feat/fleet-v1-milestone-runner-20261028` |
| #447 | `6e31ccfe02` | OPEN/Draft | `test/core-dir-granularity-20261028` | `feat/fleet-hub-top-level-stage2-20261028` |
| #448 | `3195abe981` | OPEN/Draft | #436 | `feat/l1-l2-posix-paths-20261028` |
| #450 | `be61f8a2a5` | OPEN/Draft | #413 | `feat/d2-stage2-build-fleet-hub-combined-20261028` |
| #451 | `f1e7dff929` | OPEN/Draft | #447 | `docs/l2-register-fleet-hub-stage2-20261028` |
| #453 | `08365429fd` | OPEN/Draft | `feat/l4-commander-skills-v1-20261027` | `feat/fleet-l4-quality-20261028` |
| #456 | `76d63766d6` | OPEN/Draft | #453 | `feat/fleet-chatgpt-operator-20261028` |
| #457 | `b6169f19a5` | OPEN/Draft | `feat/l1-d89-lease-writer-20261028` | `feat/l1-monitor-only-20261028` |
| #458 | `36d0c1d23f` | OPEN/Draft | `main` | `feat/l2-cloudcli-control-matrix-20261028` |
| #459 | `a913fddc3b` | OPEN/Draft | #449 的分支 | `feat/fleet-v1-batch-observation-20261028` |

**本轮复核抓到的两处漂移**：#456 `17ee719ce7 → 76d63766d6`、#457 `e8515ccb4d →
b6169f19a5`。第一次合成用的是旧 SHA，已整棵重建（旧树 `a809cdcb` 作废，不作证据）。

补充：**#416**（`dc73c60095`）的 base 就是 **#407 的分支**，所以 §7 里「#407 按 #449 §9
继续排除」这条依赖会直接把 #416 一起挡在门外 —— 它不是待验收的独立件。

## 2. 与 #449 的包含关系（`git merge-base --is-ancestor`）

- **#449 已包含**（14）：#411 #412 #413 #419 #421 #423 #426 #428 #433 #435 #436 #437 #440 #446
- **#449 未包含**（11）：#407 #431 #447 #448 #450 #451 #453 #456 #457 #458 #459

即 #449 相对今天已落后 11 个 PR，其中包含整条 **#450（L3 builder 的 `fleet_hub` 打包）**
与 **#459（Batch 关联）** 新血，以及刷新过的 #431（L2 release separation）。

## 3. 合成配方（可复现，零冲突）

起点 = #449 的 head `4db2e711aa24e75f1eeed998e33c87a776ab8765`。按下面顺序逐个
`merge-tree --write-tree` + `commit-tree`（对象库操作，零 checkout、零分支改写）：

```
431 517ef91  -> 69b9780c789222312c3f44304e1b5cba57ae1eeb
447 6e31ccf  -> ae42ce2d411e7efdb0df9a5163060a8df5be93f3
451 f1e7dff  -> 84d8fd9f44b9085ceeaca90e302c8f5c67adea63
448 3195abe  -> 3ad8eed893705992620af719b95a5495b1e1f67a
450 be61f8a  -> 9fb7897cfb1d2a7312861ae9ebd15add18d21631
453 0836542  -> 2d9bde275c69f2a50b4d90523ab9d6c9d0831bc5
456 76d6376  -> 36c41df2a18b436b1bd022d90128da1f09c77a4b
457 b6169f1  -> 425fdf485f1802a82fb2404dea83df46642da55e
458 36d0c1d  -> 7c008ab22646c8ea2519ff5cb91e2026aba0637d
459 a913fdd  -> 5113cdc6498c0ed1a497b72cbbc813005c9e4117   ← RC-NEXT tree
```

**10 次合并，0 冲突。** 顺序不是随意的：

- `447 → 451`：#451 是 #447 的 stacked 补丁（`fleet_hub:` 的 layers 登记，我的上一轮产出）。
- 其余按「作者的 base 已在树里」排列；#459 的 base 就是 #449 的分支，必须最后。
- 这是**对象库里的合成树**，不是任何人的分支。要复现只需上表的 10 个 SHA 与顺序。

## 4. 分层证据矩阵

| 层 | 本轮覆盖的内容 | 载体 |
|---|---|---|
| **CODE** | 合成树 `5113cdc6` 本身；`scripts/build.py#verify_artifact_kinds` 与 `release.py#ARTIFACT_KINDS/REQUIRED_MEMBERS` 的真实行为；#423 的 `MilestoneNotifier` 公开面；#459 的调用点 | §3 + §6 |
| **UNIT** | 下表的本机 pytest 读数（单核、单进程、`-p no:cacheprovider`） | §5 |
| **HOSTED_CI** | 仅本 PR 的 check。**不当证据使用**：新增测试文件会重排分片（§5.3），且本仓 CI 的历史基线噪声远大于本轮改动面 | 不写进本文 |
| **REAL_DEVICE** | **NOT_RUN**（未登录任何设备，未安装、未起服务） | — |
| **CLIENT_E2E** | **NOT_RUN**（无 Claude Code / ChatGPT 客户端会话，无飞书投递） | — |
| **PRODUCTION** | **NOT_RUN**（未触碰东京/戴尔任何运行中的服务、网关、隧道） | — |

## 5. inherited baseline vs cross-PR new failures

### 5.1 判定方法

基线树 = **#449 原样**（`4db2e711`），新树 = 上面的合成树 `5113cdc6`。同一台机器、
同一个 Python、同一组文件，先跑基线取**失败 ID 集合**，再跑新树取集合并 `diff`。
不比计数——比**测试 ID 是否完全相同**。

### 5.2 读数

| 测试文件 | #449 基线 | RC-NEXT `5113cdc6` | 失败 ID 集合 |
|---|---|---|---|
| `tests/test_build.py` | 35 failed / 9 passed | 35 failed / 9 passed | **完全相同（35/35）** |
| `tests/test_build_decoupling.py` | 3 failed / 5 passed / 11 errors | 3 failed / 5 passed / 11 errors | **完全相同（14/14）** |
| `tests/test_fleet_v1_device_ability_local.py` | 49 errors | 49 errors | **完全相同（49/49）** |
| `tests/test_runtime_truth.py` | 收集期 `import fcntl` 失败 | 同 | **NOT_RUN（Windows）** |

⇒ **cross-PR 新增失败 = 0**。98 条红全部在两个分支上以同一 ID 出现，属
**继承/环境**，不归这 11 个 PR 中的任何一个。

同一批里**由绿变红**的只有我自己写的 gate（§6.1），那是**故意的**，且它不是回归：
基线树上这条规则根本不存在（`#449` 里没有这份测试文件）。

### 5.3 绿的那半（新树实测）

| 文件 | 读数 | 归属 |
|---|---|---|
| `tests/test_fleet_v1_milestone_batch.py` | **27 passed** | #459 |
| `tests/test_fleet_l4_quality.py` | **12 passed / 47 subtests** | #453 |
| `tests/test_fleet_hub_boundary.py` | **18 passed**（基线 26 passed 含 `test_repository_layers`） | #447 |
| `tests/test_repository_layers.py` | **10 passed / 37 subtests** | #414/#412 |
| `tests/test_repository_layers_fleet_hub.py` | **10 passed / 6 subtests** | 我（上一轮 #451） |
| `tests/test_build_fleet_hub_packaging.py` | 5 passed / **11 errors** | #450 —— 11 个 error 全部是 §8 的 Windows 路径拼写，不是它的逻辑 |
| `tests/test_build_decoupling.py` 的 `AForgedRecordCannotCreateAFace` | 2 errors | 同上 |

`test_native_monitor_only.py`（#457 的新 head）：**2 failed / 5 passed / 5 skipped**，
两条红都是 `native\node-core\...`（反斜杠）对比 `native/node-core/...`，见 §8。

`test_l2_cloudcli_control_matrix.py`（#458）：**1 failed / 21 passed**。那一条
`test_the_fixture_is_derived_from_captures_and_not_from_the_tree` 的差异只有两个
sha256，而**同样的文件从 blob 读出来的 sha256 与提交的 fixture 完全一致**：

```
tests/data/capture-cloudcli-1.41.0-tools-list-2026-10-27.json
  worktree sha256 = 0a8c0ba7541cef3e…   expected = 24e0bef7ce297e0c…
  blob     sha256 = 24e0bef7ce297e0c…   ← 与 expected 一致
tests/data/capture-cloudcli-1.41.0-v1-roundtrips-2026-10-27.json
  worktree sha256 = 03a557d9d4e00c83…   expected = bdcf042471e6548d…
  blob     sha256 = bdcf042471e6548d…   ← 与 expected 一致
```

⇒ 这是本机 `core.autocrlf=true` 把工作树写成 CRLF 造成的，**不是 #458 的缺陷**。
该测试按设计在缺 capture 时 skip，在 capture 存在时才比对；在 LF 工作树上应为绿。

## 6. 两处最小 merge gate（本车道新增，各一个文件）

### 6.1 `tests/test_fleet_v1_rc_next_packaging.py` — L3 builder / 打包声明

**今天的读数：9 passed / 1 failed**，唯一红是
`TheDeclaredKindsAreTheShippedBytes::test_the_composition_declares_every_kind_the_payload_carries`。

它不复制 `verify_artifact_kinds` 的规则，而是**调用它**；只在失败信息里用一份镜像枚举
把「所有被 payload 支撑却没有声明的 kind」一次列全（build 的循环在第一个就抛）：

```
does not declare `l2-node` yet the payload carries
  ['fleet_mcp/__init__.py', 'fleet_mcp/node/__init__.py', 'fleet_mcp/node/connector.py']
undeclared kinds the payload backs:
  {'l2-node': [...上述 3 个...],
   'l2-hub':  ['fleet_mcp/__init__.py', 'fleet_mcp/server.py']}
```

**对 `#449` §4 记录的第一处更正**：那里只写了 `l2-node`。真实是**两个 kind**——
`fleet_mcp/` 同时装了 `server.py`，所以 `l2-hub` 也漏了。第一个拒绝把第二个藏住了。
「declare the kinds — two lines」因此是**三行**（`l2-node` + `l2-hub`，再加既有的两项），
而不是两行。

两个出口都不归我：Exit A 是 `plugin/bundle.yaml#artifacts`（组装方），Exit B 是让
node/plugin 的 payload 不再装 `fleet_mcp/`（打包方）。gate 只要求**声明与字节一致**，
不替 owner 选。

**翻转已证明**（同一份测试，只把 bundle 复制一份并补上 `l2-node`/`l2-hub`）：
`9 passed, 1 skipped`（`..._is_enough` 因为已经没有 leak 而 skip）。

同一个文件里另有 4 条绿的状态钉住 #450 的修复：`CORE_MODULES` ↔ `bundle.yaml` 双向一致、
一模块一路径、`fleet_hub` 在两个列表里同路径且被 payload 真的带上、以及
「打包列表不是层权威」（`tests` 在 `layers.yaml` 里但不在 `CORE_MODULES`，
`fleet_hub` 两边都有 ⇒ 两个词表既不相等也不互相包含）。

### 6.2 `tests/test_fleet_v1_rc_next_notifier_seam.py` — #423 的 sender seam

**今天的读数：7 passed / 1 xfailed。**

- 契约三处一致（#459 的调用点 AST、#459 README 里的补丁签名、以及落地后的真类）：
  全部 keyword-only，五个名字 `delivery_key, milestone_key, outcome, text, why`。
- **merge gate**（`expectedFailure`）：`MilestoneNotifier.send_text` 必须**定义在类体里**
  （`__dict__`），不是子类或实例挂上去的。#459 自己的 `SeamedNotifier` 是测试里的子类，
  它证明的是「契约可写」，**不是** owner 已经写了。
- 拒绝在今天仍是诚实的：把**真 notifier** 交给 `BatchAggregator` 得到
  `NOTIFIER_SEAM_MISSING`，不是偷偷去调 `_send`。
- AST 扫描 `batch.py`：**没有任何 `_send` 属性访问**（`_send` 只出现在两处 docstring 里），
  这条挡住「为了让 gate 变绿而写第二条出口路径」。
- 正向控制 `test_the_public_surface_today_has_no_text_seam` 钉住今天的公开面
  `['observe','observe_payload','reconcile']`，保证 xfail 是因为**这个**原因。

**翻转已证明**（用一个 pytest 插件在 collection 之后给类挂上 `send_text`）：
gate 变成 **failed（unexpected success）** —— 正是「seam 落地了，请删掉装饰器」的信号；
正向控制同步红并点名多了 `send_text`。

## 7. BLOCKED / DEPENDENCY（不做空等）

| # | 项 | 状态 | 谁欠 |
|---|---|---|---|
| B1 | `plugin/bundle.yaml` 声明 `[node-runtime, plugin]`，payload 却带 `fleet_mcp/{node/*,server.py}` | **BLOCKED** —— `build.py#verify_artifact_kinds` 拒绝，两个 kind（`l2-node`, `l2-hub`） | 组装方（Exit A）或打包方（Exit B） |
| B2 | #423 没有公开 `send_text` | **BLOCKED** —— #459 的 `BatchAggregator` 构造即拒 `NOTIFIER_SEAM_MISSING`；真机 Batch 摘要不可用 | #423 |
| B3 | #407 按 #449 §9 继续排除（V2 manager wake） | **DEPENDENCY** | #407 |
| B4 | **#416** 的 base 就是 #407 的分支 | **BLOCKED on B3** —— 不在本轮可验收集合内 | #416 |
| B5 | #448（L2 POSIX paths）合入后仍未被 §4/§8 的 Windows 路径问题覆盖 | 观察项：Windows 上 build 仍不可跑 | 记录在案 |

B1 与 B2 **都不是「再等一等」**：两个 gate 都已经在树上，条件一满足会自己变绿（B1）
或变成 unexpected-success（B2）。

## 8. Windows 上 NOT_RUN 的清单（原因都已定位，不是「跑不动」）

| 现象 | 根因 | 归谁 |
|---|---|---|
| `tests/test_runtime_truth.py` 收集期失败 | `runtime/daemon.py:53 import fcntl` | 平台 |
| `tests/test_build.py` 35 红 / `device_ability_local` 49 红 / `test_build_fleet_hub_packaging` 11 红 | `scripts/build.py` 用 `str(rel)` 生成 members ⇒ Windows 下是 `bin\serverosd`，而 `release.py#REQUIRED_MEMBERS` 是 `bin/serverosd`，`verify_artifact_kinds` 拒绝**它自己刚写下**的 payload | CODE（可移植性）,非本轮任何 PR 引入（基线同 ID） |
| `test_native_monitor_only` 2 红 | `str(path.relative_to(ROOT))` 与 POSIX 字面量比 | #457，测试侧，Linux 上应为绿 |
| `test_l2_cloudcli_control_matrix` 1 红 | `core.autocrlf=true` 令工作树 CRLF，而 fixture 记的是 blob 的 sha256 | 本机环境 |
| `test_build_decoupling` 1 条 `os.geteuid` | POSIX-only API | 平台 |
| systemd / symlink / 真实服务 / C++ 编译 / ctest | 本机不具备 | 平台 |

这些**没有**被记进 `tests/hermetic-known-failures.txt`，也没有被任何 skip 掩盖：
它们就是上面表格里的原始读数。

## 9. 没做的事

未合 main；未改 #449 的分支；未改任何原作者的生产源码（含 `plugin/bundle.yaml`、
`scripts/build.py`、`milestone_notify.py`、`batch.py`）；未扩 fake known-failure 基线；
未装任何东西、未起停服务、未碰密钥/OAuth/飞书；未清理任何人的 worktree；未强推。
