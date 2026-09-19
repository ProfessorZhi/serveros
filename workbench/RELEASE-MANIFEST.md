<!-- ⟦snapshot⟧ rendered 2026-09-19T19:12:33+00:00 by `runtime/workbench.py` from ~/workspace/worktrees/serveros/foundation-skeleton -->

# RELEASE-MANIFEST

> **主张** — 本页内容由命令的判定产生，重跑应当复现。pin 与 revision 是定值；promote 之后这一页应当变红，直到重新渲染。`serverosctl workbench check` 会整页比对。

<!-- ⟦answers⟧
(none) 本页不读任何命令
-->

产物摘要与代码/能力/fixture revision 固定在这里，并且**每一条 pin 都被验证过可达**。

跨 repo 不是原子事务：这个文件不声称它们是。它声称的是，每一半单独可达，并且没有任何一半是在未验证的情况下被写进 active pointer 的。

### pin 与可达性

| 名字 | repo | revision | 可达性 | 证据 |
|---|---|---|---|---|
| serveros | ~/workspace/worktrees/serveros/foundation-skeleton | `01f5480` | 存在 | `serveros/foundation-skeleton` **远端不存在** |
| ability-management | ~/workspace/worktrees/ability-management/foundation-skeleton | `fbd0b1a` | 存在 | `serveros/foundation-skeleton` **远端不存在** |
| quay（工作包 E 固定的版本） | ~/workspace/worktrees/quay/foundation-skeleton | `ddc4fb40f` | 存在 | — |
| quay（本轮 Goal 闭环用的版本） | ~/workspace/worktrees/quay/foundation-skeleton-develop | `8bb184ef3` | 存在 | — |
| design package | ~/workspace/worktrees/serveros/foundation-skeleton | `e1f7de4d5` | 存在 | `docs/foundation-skeleton-20260920` = `e1f7de4d53e9`（= pin） |

可达性是**跑出来**的：对每个 pin 执行一次 `git cat-file -e <sha>^{commit}`，对远端分支再执行一次 `git ls-remote --heads`。`unreachable` 不是警告，是这一页的 FAIL。

### 隔离安装（本轮的 promote 目标）

runtime root：`~/artifacts/skills/serveros/RUN-20260920-foundation-skeleton-f/release/hwz-server-os/serveros-linux-work`

```
current       srv-01f5480-abil-fbd0b1a-wb3
previous      srv-01f5480-abil-fbd0b1a-wb2
```

这两行就是 AC-003 断言的那一条：`previous` 不是一句承诺，而是一个**真的还能启动**的 revision。本页读的是这个隔离 root **自己的结构**，没有去调构建工作树里的 `release.py` —— 那个脚本不进产物，shipped renderer 依赖它，会让这一页在源码目录里好看、在任何已安装节点上抛错。

**这不是线上 runtime root。** 这一轮的 promote 落在运行产物目录下的隔离 root，线上指针没有动过。线上切换是 [DEPLOYMENT.md](DEPLOYMENT.md) 的 P1。
