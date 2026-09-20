<!-- ⟦snapshot⟧ rendered 2026-09-20T11:56:36+00:00 by `runtime/workbench.py` from ~/workspace/worktrees/serveros/SRV-0011 · inputs: projects=serveros,litellm,multi-user-claw; project=litellm; item=LIT-0101; quay-run=~/artifacts/work-items/serveros/SRV-0011/RUN-0001/quay-run; quay-bin=~/workspace/worktrees/quay/foundation-skeleton/plugin/vendor/quay/dist/quay.js; ability-repo=~/workspace/worktrees/ability-management/SRV-0011; reports-dir=~/artifacts/work-items/serveros/SRV-0011/RUN-0001; release-root=~/artifacts/work-items/serveros/SRV-0011/RUN-0001/release/dist/serveros-linux-work -->

# RELEASE-MANIFEST

> **主张** — 本页内容由命令的判定产生，重跑应当复现。pin 与 revision 是定值；promote 之后这一页应当变红，直到重新渲染。`serverosctl workbench check` 会整页比对。

<!-- ⟦answers⟧
(none) 本页不读任何命令
-->

产物摘要与代码/能力/**策略**/**adapter** revision 固定在这里，并且**每一条 pin 都被验证过可达**。

跨 repo 不是原子事务：这个文件不声称它们是。它声称的是，每一半单独可达，并且没有任何一半是在未验证的情况下被写进 active pointer 的。

### pin 与可达性

| 名字 | repo | revision | 可达性 | 证据 |
|---|---|---|---|---|
| serveros | ~/workspace/worktrees/serveros/SRV-0011 | `60da36f3` | 存在 | `serveros/system-v1-20260920` **远端不存在** |
| ability-management | ~/workspace/worktrees/ability-management/SRV-0011 | `fbd0b1a7` | 存在 | `serveros/system-v1-20260920` **远端不存在** |
| work-engine | ~/workspace/worktrees/work-engine/SRV-0011 | `65e9b56c` | 存在 | `serveros/system-v1-20260920` **远端不存在** |
| hwz-ops | ~/workspace/worktrees/hwz-ops/SRV-0011 | `790bca27` | 存在 | `serveros/system-v1-20260920` **远端不存在** |
| Claude/Codex adapter | ~/workspace/worktrees/serveros/SRV-0011 | `60da36f3` | 存在 | `serveros/system-v1-20260920` **远端不存在** |
| quay（工作包 E 固定的版本） | ~/workspace/worktrees/quay/foundation-skeleton | `ddc4fb40f` | 存在 | — |
| quay（本轮 Goal 闭环用的版本） | ~/workspace/worktrees/quay/foundation-skeleton | `8bb184ef3` | 存在 | — |
| design package（本轮固定版本） | ~/workspace/worktrees/serveros/SRV-0011 | `c01d2a0d` | 存在 | `docs/final-architecture-20260920` ⊇ pin |
| design package（本轮从它继续） | ~/workspace/worktrees/serveros/SRV-0011 | `e1f7de4d5` | 存在 | `docs/foundation-skeleton-20260920` ⊇ pin |

可达性是**跑出来**的，而且是「能不能取到」而不是「head 相不相等」：对每个 pin 执行一次 `git cat-file -e <sha>^{commit}`，对远端分支执行 `git ls-remote --heads`，再执行一次 `git merge-base --is-ancestor <pin> <远端 head>`。分支 head 会随每次提交移动，包含关系不会 —— 一个 revision 一旦被推上去的分支包含，就永远被它包含。`不存在`/`不含 pin` 不是警告，是这一页的 FAIL。

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
| managed-mutation-contract.yaml | serveros/managed-mutation.v1 |
| managed-runtime-contract.yaml | serveros/managed-runtime.v1 |
| network-contract.yaml | serveros/network.v1 |
| notification-contract.yaml | serveros/notification.v1 |
| ownership-contract.yaml | serveros.ownership.v1 |
| plugin/bundle.yaml | **不在这个产物里** —— `plugin/` 是与 runtime 分开的另一次构建，不随本产物发行 |
| plugin/profiles/linux-work.yaml | **不在这个产物里** —— `plugin/` 是与 runtime 分开的另一次构建，不随本产物发行 |
| resource-governance-contract.yaml | serveros/resource-governance-contract.v1 |

从 shipped tree 里**读出来**的，不是在这里重复一遍的：每个 `runtime/*.yaml` 抬头声明的 `schema:`，加上 plugin composition 声明的 bundle schema。一份手抄的 schema 清单会在有人改 schema 的那一天开始说旧话，而那一天不会有任何东西变红。

### 隔离安装（本轮的 promote 目标）

runtime root：`~/artifacts/work-items/serveros/SRV-0011/RUN-0001/release/dist/serveros-linux-work`

```
current       srv-01f5480-abil-fbd0b1a
previous      srv-60da36f3-abil-fbd0b1a
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
