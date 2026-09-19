<!-- ⟦snapshot⟧ rendered 2026-09-19T19:12:33+00:00 by `runtime/workbench.py` from ~/workspace/worktrees/serveros/foundation-skeleton -->

# DEPLOYMENT

> **主张** — 本页内容由命令的判定产生，重跑应当复现。作者输入，没执行过就不能变。`serverosctl workbench check` 会整页比对。

<!-- ⟦answers⟧
(none) 本页不读任何命令
-->

§8 的最小变更清单。**本页没有任何一条已执行**，措辞上也不允许把待部署写成已上线。

### P1 · 把 serverosd 指到新 revision（promote + restart）

**影响**：线上 `current` 指针移动，serverosd 重启；节点观察中断一个 tick 级别的时间

**执行前**：`scripts/release.py status --runtime-root ~/runtime-assets/hwz-server-os` 记下 current 与 tree_hash；`serverosctl status --json` 记下 pid 与 ticks

**执行后怎么知道成了**：`release.py status` 显示新 revision 且 agreement AGREE；`serverosctl status` 的 ticks 继续增长、tick_failure_count 未上升

**回退**：`scripts/release.py rollback --runtime-root ~/runtime-assets/hwz-server-os`（previous 指针已在 promote 时写下），再 restart

### P2 · 覆盖现用的 CC 插件 / Hook

**影响**：当前会话与后续会话加载的 skill 集合变化；正在跑的其他会话可能读不到原有 skill

**执行前**：记录当前插件目录的 revision 与 tree_hash；记录 `.claude/settings.json` 的 hook 段

**执行后怎么知道成了**：新会话里 skill 列表包含本轮新增项；旧 skill 的入口仍可解析

**回退**：把插件目录指回记录下的 revision；hook 段按记录恢复

### P3 · 切换生产卡片 timer（tokyo-status-card / tokyo-alert-digest）

**影响**：真实投递到飞书；收件人看到的卡片内容与频率变化

**执行前**：记录两个 timer 的 enabled/active 状态与上一次投递时间

**执行后怎么知道成了**：卡片出现在目标会话，且内容与本仓 `workbench/STATUS-PREVIEW.md` 同源可比

**回退**：`systemctl --user disable --now` 回原 timer，并恢复原 unit 文件

### P4 · 启用每日定时任务（日报/看板生成）

**影响**：每天固定时刻产生一次写入；本轮只交付默认 disabled 的模板

**执行前**：确认模板 unit 的 ExecStart 指向隔离安装而不是源码树

**执行后怎么知道成了**：首次 fire 后检查产物落点与 exit code，而不是只看 timer 是 active

**回退**：`systemctl --user disable` 并删除已生成的 unit

### P5 · 真实飞书投递与客户端回读

**影响**：对外发送。内容一旦发出即可能被缓存或索引，删除不等于撤回

**执行前**：用 `workbench/*-PREVIEW.md` 的离线渲染逐字确认要发的内容

**执行后怎么知道成了**：在飞书客户端回读，确认与预览逐字一致；不一致则按 P3 回退

**回退**：无法撤回已投递内容；回退的是后续投递（停 timer）

### P6 · `records` 发布位置写入工作台

**影响**：向另一个 actor 正在写的工作树写入；测量时有 36 条未提交条目

**执行前**：重新取一次 records 的 git 状态快照（不得复用旧快照）

**执行后怎么知道成了**：只以显式路径提交，`git status` 里其他人的条目数量不变

**回退**：`git revert` 该条提交

### 这一轮做了什么、没做什么

- **做了**：在隔离 runtime root 上跑通了 promote / status / rollback（见 [RELEASE-MANIFEST.md](RELEASE-MANIFEST.md)），入口从 `/tmp` 走通。
- **没做**：线上 promote/restart、插件/Hook 覆盖、timer 切换、真实发卡、启用定时任务、云端写、网络/隧道/容器变更、秘密变更。
- 这条界线由 §8 划定，不由本页自行放宽。
