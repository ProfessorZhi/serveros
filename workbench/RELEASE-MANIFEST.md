<!-- ⟦snapshot⟧ rendered 2026-09-27T06:51:41+00:00 by `runtime/workbench.py` from ~/workspace/worktrees/serveros/serveros-completion-20260925 · inputs: projects=serveros,litellm,multi-user-claw; project=litellm; item=LIT-0101; quay-run=~/artifacts/work-items/serveros/SRV-0011/RUN-0001/quay-run; quay-bin=~/workspace/worktrees/quay/foundation-skeleton/plugin/vendor/quay/dist/quay.js; ability-repo=~/workspace/src/ability-management; reports-dir=~/artifacts/work-items/serveros/SRV-0011/RUN-0001; release-root=~/runtime-assets/hwz-server-os/serveros-linux-work -->

# RELEASE-MANIFEST

> **主张** — 本页内容由命令的判定产生，重跑应当复现。pin 与 revision 是定值；promote 之后这一页应当变红，直到重新渲染。`serverosctl workbench check` 会整页比对。

<!-- ⟦answers⟧
(none) 本页不读任何命令
-->

产物摘要与代码/能力/**策略**/**adapter** revision 固定在这里，并且**每一条 pin 都被验证过可达**。

跨 repo 不是原子事务：这个文件不声称它们是。它声称的是，每一半单独可达，并且没有任何一半是在未验证的情况下被写进 active pointer 的。

### pin 与可达性（两列，两个问题，互不冒充）

| 名字 | repo | revision | 本地对象 | revision_remote_reachable | designated_release_ref |
|---|---|---|---|---|---|
| serveros | ~/workspace/worktrees/serveros/serveros-completion-20260925 | `60da36f3` | 存在 | 可达 | `serveros/system-v1-20260920` **未发布** |
| ability-management | ~/workspace/worktrees/ability-management/SRV-0011 | `fbd0b1a7` | 存在 | 可达 | `serveros/system-v1-20260920` **未发布** |
| work-engine | ~/workspace/worktrees/work-engine/SRV-0011 | `65e9b56c` | 存在 | 可达 | `serveros/system-v1-20260920` **未发布** |
| hwz-ops | ~/workspace/worktrees/hwz-ops/SRV-0011 | `790bca27` | 存在 | 可达 | `serveros/system-v1-20260920` **未发布** |
| Claude/Codex adapter | ~/workspace/worktrees/serveros/serveros-completion-20260925 | `60da36f3` | 存在 | 可达 | `serveros/system-v1-20260920` **未发布** |
| quay（工作包 E 固定的版本） | ~/workspace/worktrees/quay/foundation-skeleton | `ddc4fb40f` | 存在 | 可达 | —（本节点持有，不声称发布） |
| quay（本轮 Goal 闭环用的版本） | ~/workspace/worktrees/quay/foundation-skeleton | `8bb184ef3` | 存在 | 可达 | —（本节点持有，不声称发布） |
| design package（本轮固定版本） | ~/workspace/worktrees/serveros/serveros-completion-20260925 | `c01d2a0d` | 存在 | **不可达** —— 远端答了，本地持有的 6 个 ref 没有一个包含它；另 7 个 ref 的对象本地没有，这一次没判 | `docs/final-architecture-20260920` **未发布** |
| design package（本轮从它继续） | ~/workspace/worktrees/serveros/serveros-completion-20260925 | `e1f7de4d5` | 存在 | **不可达** —— 远端答了，本地持有的 6 个 ref 没有一个包含它；另 7 个 ref 的对象本地没有，这一次没判 | `docs/foundation-skeleton-20260920` **未发布** |

**左边一列问「这个 revision 从远端取不取得到」** —— 注意它问的是revision，不是分支名。它把远端 advertise 的**全部 ref** 取回来（`git ls-remote --heads --tags`），先用 `git cat-file --batch-check` 留下本地**真的持有**的那些，再用一次 `git rev-list <pin> --not <所有能判定的 ref>` 定案：空输出就是可达。**它不读任何手写字段**，也不打印 ref 名 —— 这一页是整页比对的，而「包含某个 revision 的 ref 集合」会随别人建分支而增长，ref 名写进格子里会让这一页因为一件与 release 无关的事变红。ref 的归属是右边那一列的事。

这一列**刻意不用 `--ignore-missing`**，理由是本机实测的：对一个克隆里没有的对象，`git rev-list --ignore-missing <sha>` **退出码 0 且输出为空**（那个 flag 的意思是「打不开的跳过」），而空输出正是本列读作可达的东西。那个 flag 看起来像宽容，实际是一处假绿：一个**哪里都不存在**的 revision 会被印成同一行里「本地对象=不存在」旁边写着「可达」——这不是舍入误差，是一行内部自相矛盾。去掉它，同一条命令对同一个输入退出 128，拒绝作答才是这里诚实的答案；而 `--not` 名单里的 ref 已经预先筛成本地持有的对象，那个 flag 在这条路径上本来也没买到任何东西。

**右边一列问「这个 release 声明自己在哪个 ref 发布，那个 ref 含不含它」** —— 它是关于**声明**的一句话，不是关于 release 真假的一句话。没声明发布点的行写 `未指定` 而不是画一个 `—`：**未声明是一个结论**，而 `—` 是「没有结论」被渲染成「没有发现」的样子。

把这两问压进同一格，两个方向会同时出错，而本轮两个都发生了：一个真的发布在别的 ref 上的 revision 会被读成**悬空**（四仓里有三仓的 pin 一直在各自默认分支上，列却写 `远端不存在`，因为那个 pin 点名的分支那些仓从来没建过）；而**事后补一个同名分支**就能让它变绿、release 本身什么都没变 —— 本轮正是这么做的，`serveros/system-v1-20260920` 在四个远端被创建，其中三个的 tip 本来就是那个远端的 HEAD，于是页面读成「五条 pin 全部可达」，那是一句关于**名字**的话被当成了一句关于**发行**的话。

判读：`不可达` / `未判定` / `远端未答` 是这一页的 FAIL —— 最后一个尤其不要读成前两个，**远端没答和远端答了「没有」是两条不同的发现**。`未指定` 是缺口，不是 FAIL。`不含 pin` 也不是 FAIL：它是「可达，但发布点在别处」，而这件事只有在两列分开之后才首次说得出来。

### revision 与 hash（每一类都要有）

| 名字 | 类别 | revision | 树 hash |
|---|---|---|---|
| serveros | code | 60da36f3 | 0dbfe56865b4 |
| ability-management | ability | fbd0b1a7 | 3520f8568ab5 |
| work-engine | code | 65e9b56c | a0b20be6e7e7 |
| hwz-ops | ops | 790bca27 | 668d1e5d27c9 |
| Claude/Codex adapter | adapter | 60da36f3 | 0dbfe56865b4 |
| quay（工作包 E 固定的版本） | toolchain | ddc4fb40f | 8b55340bb5b4 |
| quay（本轮 Goal 闭环用的版本） | toolchain | 8bb184ef3 | 2738b6cb4fbf |
| design package（本轮固定版本） | design | c01d2a0d | 03729a55fed8 |
| design package（本轮从它继续） | design | e1f7de4d5 | 4d732cac8476 |

**hash 是 git 自己对那棵树的散列**（`git rev-parse <rev>^{tree}`），不是这里另算的第二个数。两个人为同一次 revision 各算一个摘要，迟早会有一个不再匹配，而那时没有任何东西会报错 —— 所以这里印的是 git 的。

树 hash 覆盖的是**树**：它证明两个 revision 的内容相同或不同，它不覆盖 packfile、远端配置或工作树里未提交的东西。一行写着 hash 的清单很容易被读成「整台机器都被验证过了」，所以这一行说的正是它不覆盖什么。

**类别这一列是这一节存在的理由。** 一份只固定了 code 的清单，和一份固定了全部四类的清单，在没有这一列的时候长得一模一样：读者只能看见表里有什么，看不见表里**该有什么**。策略与 adapter 不是代码的附属物 —— 它们各自有自己的 revision，也各自能单独地过时。

### schema 版本

| 文件 | schema |
|---|---|
| actuator-contract.yaml | serveros/actuator.v1 |
| agent-session-contract.yaml | serveros/agent-session.v1 |
| approval-contract.yaml | serveros/approval.v1 |
| attribution-contract.yaml | serveros/attribution.v1 |
| card-definitions.yaml | serveros/card-definitions.v1 |
| card-scenarios.yaml | serveros/card-scenarios.v1 |
| environment-contract.yaml | serveros/environment.v1 |
| managed-mutation-contract.yaml | serveros/managed-mutation.v1 |
| managed-runtime-contract.yaml | serveros/managed-runtime.v1 |
| network-contract.yaml | serveros/network.v1 |
| notification-contract.yaml | serveros/notification.v1 |
| ownership-contract.yaml | serveros.ownership.v1 |
| periodic-report-contract.yaml | serveros/periodic-report-contract.v1 |
| plugin/bundle.yaml | **不在这个产物里** —— `plugin/` 是与 runtime 分开的另一次构建，不随本产物发行 |
| plugin/profiles/linux-work.yaml | **不在这个产物里** —— `plugin/` 是与 runtime 分开的另一次构建，不随本产物发行 |
| recipe-contract.yaml | serveros/recipe-contract.v1 |
| report-publication-contract.yaml | serveros/report-publication-contract.v1 |
| resource-governance-contract.yaml | serveros/resource-governance-contract.v1 |

从 shipped tree 里**读出来**的，不是在这里重复一遍的：每个 `runtime/*.yaml` 抬头声明的 `schema:`，加上 plugin composition 声明的 bundle schema。一份手抄的 schema 清单会在有人改 schema 的那一天开始说旧话，而那一天不会有任何东西变红。

### 隔离安装（本轮的 promote 目标）

runtime root：`~/runtime-assets/hwz-server-os/serveros-linux-work`

```
current       serveros-reading-honesty-20260927
previous      serveros-memory-provider-20260927
```

这两行就是 AC-003 断言的那一条：`previous` 不是一句承诺，而是一个**真的还能启动**的 revision。本页读的是这个隔离 root **自己的结构**，没有去调构建工作树里的 `release.py` —— 那个脚本不进产物，shipped renderer 依赖它，会让这一页在源码目录里好看、在任何已安装节点上抛错。

**这不是线上 runtime root。** 这一轮的 promote 落在运行产物目录下的隔离 root，线上指针没有动过。线上切换是 [DEPLOYMENT.md](DEPLOYMENT.md) 的 P1。

### 恢复信息

回滚**恢复的是 revision 指针，不是状态**。这两件事必须分开写下来，因为一句「可以回滚」在读者那里同时意味着两件事，而其中只有一件是真的。

```
回滚恢复：
  <runtime root>/current  →  <runtime root>/previous
  以及随它一起被引用的那棵 shipped tree

回滚不恢复、也不该恢复：
  state root       —— 授权、工作记录、receipt、观察结果都在这里
  worktree / 制品   —— 回滚不动任何人的工作树
  Quay 的工作区     —— 闸门账本属于那一轮，不属于这次发行
```

**状态不在 revision 里，这正是回滚能救回工作的前提。** 如果授权与 receipt 是随 revision 一起发的，那么一次回滚就会把它们一起倒回去 —— 于是「回滚到上一个可用版本」会顺手丢掉期间产生的批准，而这件事不会表现为一次失败，只会表现为批准变少了。`state` 目录落在 runtime root 之外，由 `SERVEROS_STATE_DIR` 指向，回滚不碰它。

所以恢复的完整性检查不是「指针动没动」，而是回滚之后去 state root 里读一次授权、读一次 receipt：**它们还在，才叫恢复了。**
