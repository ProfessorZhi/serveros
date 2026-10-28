# FLEET V1 RC-NEXT（2026-10-28，第二轮）— exact-SHA 集成树、跨 PR 新失败分离、两处 merge gate

> 第一轮的树（`5113cdc6`，基于 #449 `4db2e711` + 10 个 PR）已被本轮的 **`5a97605e`
> （commit `c2e885e5`，+13 个 PR 头）作废**，因为 #462 之后 L3 builder 的规则变了，
> 而旧树上两个 gate 的前提也不再成立。本文只记录本轮的数字；第一轮的读数只在
> §5.4 保留为「上一轮 CI 层」的历史对照。

## 0. 身份与边界

- **车道**：`integrations/fleet-v1-rc-next/**`（新路径）、
  `docs/integration/FLEET-V1-RC-NEXT-*.md`（新名）、`tests/test_fleet_v1_rc_next*`（新文件）。
- 我改的**只有**这四项。**未改**任何原作者的生产源码：`scripts/build.py`（#413/#462）、
  `plugin/bundle.yaml`（未动，一个字节也没有）、`scripts/release.py`（#431/#433/#448）、
  `milestone_notify.py`（#423/#460）、`batch.py`（#459/#464）、`layers.yaml`（#414）。
- 未合 main；未改任何在跑的服务/网关/隧道；未碰密钥、OAuth、飞书；未清理他人 worktree；
  **无 force-push**（本车道只有追加 commit）。
- 没有对 FJ Codex 的旧 Run 做盲重派。本轮是在我自己的、RESIDENT_HOST idle 的会话里重新取证。

## 1. 输入（`gh pr view` 逐条复核于 2026-10-28）

起点 #449 `4db2e711`（`integrate/fleet-v1-rc-20261028`）不变。

| # | head | 角色 | 状态 |
|---|---|---|---|
| 431 | `517ef91507` | L2 node release 构建/校验 | 本轮**并入** |
| 447 | `6e31ccfe02` | `runtime/fleet_hub` → 顶层 `fleet_hub` | 并入 |
| 451 | `f1e7dff929` | `layers.yaml` 登记 `fleet_hub: L2`（我的上一轮） | 并入 |
| 448 | `3195abe981` | POSIX member paths on every host | 并入 |
| 450 | `be61f8a2a5` | build 打包 `fleet_hub` | 并入 |
| 453 | `08365429fd` | L4 operator skill 收紧 | 并入 |
| 456 | `76d63766d6` | ChatGPT 批量指挥手册 | 并入 |
| 457 | `b6169f19a5` | monitor-only profile | 并入 |
| 458 | `36d0c1d23f` | L2 capability matrix | 并入 |
| 459 | `a913fddc3b` | Batch 关联契约 | 并入 |
| **462** | **`a8ec6c3094`** | **L3 builder 只决定两个 face** | 并入（关闭 B1） |
| **460** | **`4fc9fcbc67`** | **`MilestoneNotifier.send_text` 真身** | 并入（关闭 B2） |
| **464** | **`d72befe771`** | **Batch 持久登记** | 并入 |
| 461 | `b042f515b4` | node payload 脱离 hub（26 模块） | **排除**，见 §2/§7 |
| 465 | `1a162c44b6` | receipt/error truth | **排除**，见 §2/§7 |
| 407 | `9538238d6a` | V2 delivery（85 文件 Draft） | 按 #449 §9 继续排除 |

`#449` 自身包含的 14 个（`git merge-base --is-ancestor` 实测）：407 411 412 413 419 421
423 426 428 433 435 436 437 440 446。

## 2. 包含关系与「V1-only 子集 vs #407 依赖」

本轮 15 个抓取头里，**13 个是合成树的祖先**，**2 个不是**：`pr/461`、`pr/465`。
原因实测：两者的 base 都是 **#407 的分支**，而 #407 是 85 文件的 Draft，按 #449 §9 不进 RC。

量化（`git diff --stat`）：

```
#407 相对共同祖先          85 files, 7553 insertions(+), 548 deletions(-)
#461 自身（对 #407）        4 files,  608 insertions(+),  52 deletions(-)
#465 自身（对 #407）        4 files,  593 insertions(+),  18 deletions(-)
```

⇒ 想要 #461/#465 的 4+4 文件，必须先把 #407 的 85 文件带进 RC。它们的**V1-only 子集**
就是上表第二、三行；本轮**没有**把 #407 拉进来（见 §7 B3/B4），也没有假装它们是独立可取的。
合成树相对 #449 的总量：**77 files, 12229 insertions(+), 362 deletions(-)**。

## 3. 合成配方（可复现，13 次合并，零冲突）

`git merge-tree --write-tree <cur> <pr>` → tree；`git commit-tree <tree> -p <cur> -p <pr>`。
不用 worktree、不碰任何分支指针。

```
4db2e711 (起点 #449)
pr/459 -> 6ce6112308e3  tree 609a8156
pr/462 -> 6b4c04ef3406  tree 5a8a74af
pr/464 -> 8a1a32bc34cd  tree 3c1b2faa
pr/460 -> 3263f2ca0d80  tree d04794f6
pr/431 -> dd643ad432d5  tree a718061d
pr/447 -> 5a7ee2ebf45c  tree df5778fb
pr/451 -> 92442cc52b6a  tree 867a19a3
pr/448 -> 7333d050ead5  tree 9911eb3d
pr/450 -> ac90bbdb2ef3  tree 4c0f4b89
pr/453 -> 0b2ed412be12  tree d7f51a80
pr/456 -> 10074cc0bb4c  tree 1db9e70f
pr/457 -> 9d2fd57af1d6  tree 9af701d3
pr/458 -> c2e885e54ba8  tree 5a97605e   ← FINAL
```

顺序的**因果**部分（不是审美）：`459` 必须在 `462`/`464` 之前（后两者的 base 就是 #459 的
分支）；`447` 在 `451` 之前（#451 是 #447 的 stacked patch）；`453` 在 `456` 之前。
其余按作者自己的 base 排列。**13 次合并全部零冲突**，没有任何一处需要手工解。

## 4. 分层证据矩阵

| 层 | 本轮覆盖的内容 | 载体 |
|---|---|---|
| **CODE** | 合成树 `5a97605e`；`build.py#verify_artifact_kinds` / `build_face_kinds`（#462 后的真实行为）；`release.py` 的 L2 校验路径；`MilestoneNotifier.send_text` 真身；`BatchAggregator` 构造闸门 | §3 + §6 |
| **UNIT** | 本机 pytest（单进程、单核、`-p no:cacheprovider`），只跑入口/打包/Batch/Skill 相关 | §5 |
| **HOSTED_CI** | 本轮树的 run **尚未产生**（分支见 §10）。§5.4 的 183/197 是**上一轮**的读数，只作方法示范，不作本轮结论 | §5.4 |
| **REAL_DEVICE** | **NOT_RUN** —— 未登录任何设备，未安装，未起服务 | — |
| **CLIENT_E2E** | **NOT_RUN** —— 无 Claude Code / ChatGPT 客户端会话，无飞书投递 | — |
| **PRODUCTION** | **NOT_RUN** —— 未触碰东京/戴尔任何运行中的服务、网关、隧道 | — |

## 5. inherited baseline vs cross-PR new failures（本机）

### 5.1 方法

基线树 = **#449 原样** `4db2e711`；新树 = `c2e885e5`。同一台机器、同一 Python、同一命令。
**只比测试 ID 集合，不比计数**；且 **ID 要剥掉 `FAILED`/`ERROR` 前缀再比** ——
否则同一个测试在基线是 `ERROR`（fixture 构造失败）、在新树是 `FAILED`（真跑起来），
会被误判成「两侧各自独有」。这一条本轮先踩到，纠正后才得出下表。

比较范围只取两树**共有**的 4 个文件（另 4 个文件在 #449 上不存在，见 §5.3）：

`tests/test_build.py`、`tests/test_build_decoupling.py`、
`tests/test_fleet_v1_device_ability_local.py`、`tests/test_release_l2_layers.py`

### 5.2 读数

| | 基线 #449 | 新树 `c2e885e5` |
|---|---|---|
| distinct 红 ID | **111** | **43** |
| pytest 计数（含 subTest） | 51 failed / 60 errors / 49 passed | 54 failed / 200 passed / 5 skipped |

状态迁移（精确到 ID）：

| 迁移 | 条数 | 说明 |
|---|---|---|
| 基线红 → 新树**绿** | **74**（39 ERROR + 35 FAILED） | #462 让构建不再拒绝自己写下的 payload ⇒ fixture 不再在 `setUpClass` 里炸；#448 的 POSIX member 路径让 Windows 上也能命中 |
| 两侧都红 | 37（21 ERROR→FAILED + 16 FAILED→FAILED） | 状态从「跑不起来」变成「跑起来但断言不符」，见下 |
| 基线绿 → 新树**红** | **6** | 全部落在 #448 新增的两个测试类，根因已双向证明，见 §5.3 |

⇒ **跨 PR 引入的真实新失败 = 6，且 6 条同一个根因：测试 fixture 自己的换行处理**，
不是产品缺陷，也不是任何一个 PR 的源码回归。

### 5.3 那 6 条新红的根因（已证明，不是推测）

`tests/test_release_l2_layers.py` 里 `WindowsSeparatorsTest` / `SchemaDriftSince2469ec5Test`
两个类由 **#448** 引入（blob 归属实测：只有 #448 的分支含这两个类）。它们的 fixture：

```python
path.write_text(text, encoding="utf-8")                       # 落盘：Windows 把它翻成 CRLF
member_sha256 = hashlib.sha256(payload[name].encode())        # 摘要：按 LF 文本算
```

双向探针（同一段文本，只改落盘方式）：

```
manifest 期望 (LF str)  08ae9145440a0014…
write_text  实际字节    7c6b818775dfddab…   MISMATCH
write_bytes 实际字节    08ae9145440a0014…   MATCH
```

⇒ 在 Windows 上**每一个**成员都「does not match the manifest entry」，于是「一个正确的
payload 应该校验通过」这条自然红。**Linux CI 上不会出现**（text 模式不翻译换行），
所以这是一个只在 Windows 上可见的真实可移植性缺陷，值得报给 #448 的作者。

同文件另有 1 条非此因：`L2VerificationTest::test_a_symlinked_member_is_refused` ⇒
`OSError: [WinError 1314] 客户端没有所需的特权` —— 建符号链接需要特权，**NOT_RUN**。

### 5.4 上一轮 CI 层的读数（方法保留，**不是本轮结论**）

判法与上面相同，只是数据来自 `== baseline gate ==` 之后的 NEW FAILURES ID 集合
（四片分片成员随文件集重排，跨片比计数必错）：

| 树 | NEW FAILURES | 文件 |
|---|---|---|
| #449 `4db2e711` | 183 | 7 |
| 上一轮合成树 `5113cdc6` | 197 | 8 |

`only-in-#449 = 0`；`only-in-上一轮 = 14` = 11 × #450 新文件撞同一个阻塞 + 1 同族打包布局
id + 1 我自己的 gate（故意红）+ 1 `test_fleet_mcp_human_auth` 的无归属 id
（`git diff 4db2e711 5113cdc6 -- fleet_mcp/` 为空、相关 blob 完全相同 ⇒ 分片内顺序/并发嫌疑）。

本轮新树的 CI 需要新的 run 才能读；在拿到之前**不把上一轮的数字算到本轮头上**。

### 5.5 绿的那半（新树实测）

| 文件 | 读数 | 归属 |
|---|---|---|
| `tests/test_fleet_v1_rc_next_packaging.py` | **11 passed** | 我（§6.1） |
| `tests/test_fleet_v1_rc_next_notifier_seam.py` | **11 passed** | 我（§6.2） |
| `tests/test_build_fleet_hub_packaging.py` | **16 passed**（基线 #449 上该文件不存在；上一轮树上是 11 errors） | #450 + **#448 修好了 Windows 路径拼写** |
| `tests/test_fleet_v1_milestone_batch.py` | 26 passed / **1 failed** | #459 + #464，那 1 条是**过时的桥测试**，见 §7 B6 |
| `tests/test_native_monitor_only.py` | 2 failed（`native\node-core\…` 反斜杠 vs POSIX） | #457 测试侧，Linux 应为绿（§8） |
| `tests/test_l2_cloudcli_control_matrix.py` | 1 failed（`core.autocrlf=true` 令工作树 CRLF，fixture 记的是 blob sha256；**blob 的 sha256 与 fixture 一致**） | 本机环境（§8） |

## 6. 两处 merge gate（本车道新增，各一个文件）— 本轮**全绿**

### 6.1 `tests/test_fleet_v1_rc_next_packaging.py` — L3 builder / 打包声明

**读数：11 passed。** 第一轮这里是「9 passed / 1 failed」，红的原因是
`verify_artifact_kinds` 既要求声明 `l2-node` 又要求声明 `l2-hub`（它在第一个就抛，
第二个被藏住）。**#462 从 builder 侧关闭了它**，而且不是靠改 `plugin/bundle.yaml`、
也不是靠少收字节。新规则（读源码确认）：

1. 不在 `ARTIFACT_KINDS` 里的 kind ⇒ 拒绝；
2. **不是本 builder 产出的 kind（`l2-node`/`l2-hub`/`native-runtime`）⇒ 按名拒绝**，
   因为那个校验器属于组装该 payload 的工具（#431/#433）；
3. 「声明了却不在 payload 里」⇒ 拒绝；
4. 「payload 里有却没声明」**只对 `build_face_kinds()` 的两个 face 生效**
   （`node-runtime`、`plugin`）。

gate 因此改成断言**结果**，并且保留咬合力：

- `test_the_composition_verifies_and_returns_its_own_evidence`：真 composition 干净通过，
  证据键恰等于声明的 kind；
- `test_a_plugin_payload_that_carries_the_daemon_is_still_refused` /
  `..._node_payload_that_carries_the_plugin_...`：**正向控制** —— 规则被「收窄」和
  被「关掉」在通过率上无法区分，所以这两条必须自己会红；
- `test_the_build_refuses_to_claim_an_l2_identity`：`l2-node`/`l2-hub` 必须按名被拒
  （「not a kind this build produces」）。这条若不再抛，说明 builder 抢了 L2 发布工具的裁定权；
- `test_the_two_faces_are_the_builders_whole_claim`：钉住 `build_face_kinds()` 恰为两个 face，
  收窄的边界被人悄悄放宽时这里先红。

**翻转已双向证明**（探针把 `build_face_kinds` 恢复成旧的五 kind 语义）：
**3 failed**，红的正是「refuses to claim an L2 identity」「composition verifies」
「two faces are the whole claim」这三条，且失败信息就是「builder 的声明被放宽了」。

**残留风险（写在文件里，不掩盖）**：新规则下，合体 artifact 的 manifest 不再说明它内嵌了
哪一半 L2；`fleet_mcp/node/*`、`fleet_mcp/server.py` 作为 Core 模块随身携带，没有任何东西
声称它们是不是 standalone 的 `l2-node`/`l2-hub`。那个裁定要靠 #431/#433 的 standalone
校验器在**本车道从不组装**的 payload 上做 —— 在本车道是 **NOT_RUN**。#461 才是真正把
两半拆开的工作（且被 #407 85 文件挡住，§7）。

### 6.2 `tests/test_fleet_v1_rc_next_notifier_seam.py` — #423/#460 的 sender seam

**读数：11 passed。** 第一轮这里是 `7 passed / 1 xfailed`，并且我明确写过：
seam 落地时那条 `expectedFailure` 会变成 **unexpected success**，让运行报红、逼下一个
读者删装饰器。**#460 落了 `send_text`，它果然那样红了；装饰器已经删掉**，现在类名叫
`TheSeamIsOnItsOwnersClass`，断言：

- `send_text` 在 `MilestoneNotifier.__dict__` 里（类体定义，不是子类/实例挂的），
  keyword-only 且恰是五个名字 `delivery_key, milestone_key, outcome, text, why`；
- 公开面 = `['observe','observe_payload','reconcile','send_text']`；
- 三个地方写的契约一致：#459 的调用点 AST、#459 README 的补丁、#460 的真类；
- **真 notifier 能被 `BatchAggregator` 构造接受**（跨 PR：#459 的批 + #460 的 seam +
  #464 的 `registration.py` 在同一棵树里，不是对某个 sealed stub 说话）；
- `batch.py` 里**没有任何 `_send` 属性访问**（AST 扫描）：挡住「为了让 gate 变绿而写
  第二条出口路径」。

**翻转已双向证明**（探针在 collection 后把 `send_text` 从类上摘掉）：**3 failed**，
其中 `test_the_real_notifier_is_accepted` 红在 **`BatchRefused: NOTIFIER_SEAM_MISSING`**
—— 即 #423 自己的拒绝代码，不是我的断言自说自话。

## 7. BLOCKED / DEPENDENCY（不做空等）

| # | 项 | 状态 |
|---|---|---|
| B1 | `verify_artifact_kinds` 的 kind/face 冲突 | **已关闭（#462 `a8ec6c30`，builder 侧）**。本机实测：composition 通过、L2 kind 按名被拒 |
| B2 | #423 没有公开 `send_text` | **已关闭（#460 `4fc9fcbc` + #464 `d72befe7`）**。本机实测：真 notifier 被接受 |
| B3 | #407 按 #449 §9 继续排除（V2 manager wake） | **DEPENDENCY**，未变 |
| B4 | #461 / #465 的 base 就是 #407 的分支 | **BLOCKED on B3**：V1-only 子集各 4 文件，但要连带 85 文件（§2） |
| B5 | #448 合入后 Windows 路径问题 | **部分解决**：`build.py#str(rel)` 的反斜杠问题已被 #448 修掉——`test_build_fleet_hub_packaging` 由 11 errors 变 **16 passed** |
| **B6** | **#459 的 `TheBoundsAreReal::test_the_aggregator_refuses_a_notifier_without_the_seam` 已过时** | **跨 PR 新发现**：它拿**真** `MilestoneNotifier` 期望 `BatchRefused`；#460 给了真身 `send_text`，于是「不抛」——这条在任何单 PR 上都看不出来，只在 #459+#460 同树时才红。改法是换成无 seam 的 stub（我的 gate 用 `object()` 已经这么做） |
| **B7** | **#448 新增的两个 Windows 测试类在 Windows 上必红** | **跨 PR 新发现**：fixture 用 `write_text` 落盘（CRLF）却按 LF 文本算摘要，§5.3 已双向证明。Linux CI 看不见 |

B1/B2 的关闭是**实测**（`22 passed` + 两个翻转探针），不是从别人的 CI 结果继承来的
—— #462 的 5/5 是它自己那棵树上的读数，我重新合成、重新跑过。

## 8. Windows 上的 NOT_RUN / 已修复（原因都已定位）

| 现象 | 根因 | 归谁 |
|---|---|---|
| ~~`test_build*.py` 的 71 条红 / `test_build_fleet_hub_packaging` 11 errors~~ | `scripts/build.py` 用 `str(rel)` 生成 members（`bin\serverosd` vs `bin/serverosd`） | **已由 #448 修复**，本轮实测变绿 |
| `test_release_l2_layers` 16 条红 | 测试 fixture `write_text` 的 CRLF 翻译（§5.3） | #448，测试侧，Windows-only |
| `test_release_l2_layers::test_a_symlinked_member_is_refused` | `WinError 1314`（建符号链接需特权） | 平台，**NOT_RUN** |
| `test_native_monitor_only` 2 红 | `str(path.relative_to(ROOT))` 与 POSIX 字面量比 | #457，测试侧，Linux 应为绿 |
| `test_l2_cloudcli_control_matrix` 1 红 | `core.autocrlf=true` ⇒ 工作树 CRLF，fixture 记的是 blob sha256（**blob 的 sha256 与 fixture 完全一致**） | 本机环境 |
| `tests/test_runtime_truth.py` | 收集期 `import fcntl` | 平台，**NOT_RUN** |
| systemd / 真实服务 / C++ 编译 / ctest / 真机安装 | 本机不具备 | 平台，**NOT_RUN** |

这些**没有**被记进 `tests/hermetic-known-failures.txt`，也没有被任何 skip 掩盖：
它们就是上面表格里的原始读数。**基线文件本轮一行未加。**

## 9. 没做的事

未合 main；未改 #449 旧分支；未改任何原作者的生产源码（`plugin/bundle.yaml` 一个字节没动）；
未扩 fake known-failure 基线；未装任何东西、未起停服务、未碰密钥/OAuth/飞书；
未清理任何人的 worktree；未强推；未把 #407 的 85 文件拉进 RC；
未在别人的 PR 上改测试（B6/B7 只记录并给出改法，不动手）。

## 10. 分支

本轮的四个自有文件（两份 gate + 本文 + `integrations/fleet-v1-rc-next/README.md`）追加在
合成树 `c2e885e5` 之上，作为一个**新的** Draft RC 分支推送（旧分支 `6c91073..a49783e`
与旧 PR 保持原样、只留一条指向后继的说明）。原因：#425 明确「#463 仍是 3/7 且必须重建」，
而重建的正确形状是把**新树**当底座，不是把旧 PR 的树强推成新树。
