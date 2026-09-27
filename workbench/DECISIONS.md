<!-- ⟦snapshot⟧ rendered 2026-09-27T06:51:41+00:00 by `runtime/workbench.py` from ~/workspace/worktrees/serveros/serveros-completion-20260925 · inputs: projects=serveros,litellm,multi-user-claw; project=litellm; item=LIT-0101; quay-run=~/artifacts/work-items/serveros/SRV-0011/RUN-0001/quay-run; quay-bin=~/workspace/worktrees/quay/foundation-skeleton/plugin/vendor/quay/dist/quay.js; ability-repo=~/workspace/src/ability-management; reports-dir=~/artifacts/work-items/serveros/SRV-0011/RUN-0001; release-root=~/runtime-assets/hwz-server-os/serveros-linux-work -->

# DECISIONS

> **主张** — 本页内容由命令的判定产生，重跑应当复现。作者输入，不随机器变化。`serverosctl workbench check` 会整页比对。

<!-- ⟦answers⟧
(none) 本页不读任何命令
-->

这一轮的取舍。`state` 为 OPEN 的条目会出现在 [REPORTS.md](REPORTS.md) 的 Decision Packet 里 —— 一个还开着的决定，是外面的人唯一能真正帮上忙的东西。

| id | state | 选了 | 没选 | 为什么 |
|---|---|---|---|---|
| D1 | CLOSED | 复用已有的 `work_delivery_slice` 与 work-engine，不新建工作引擎或调度平台 | 为本轮另写一条工作链 | 主链不是从零开始：已经有一条跑通过完整交付路由的 slice。再写一条会产生两个关于「一个 Item 怎么走完」的权威，而这类双权威正是这个仓库一直在删的东西。 |
| D2 | CLOSED | 卡片通用逻辑留在运行时模块，节点/对象/目标由配置给出 | 把 renderer 留在 ops 里，每个节点一份 | 状态/告警/恢复三种卡共用一个确定性模型；按节点复制会让「同一条事实在不同节点上长得不一样」，而手机上的那张卡才是人真正读的那张。 |
| D3 | CLOSED | Work Engine ID 真实冲突时不硬建 Item，改用具名 `foundation-skeleton` 工作区 | 直接创建 SRV Item，接受一次静默重号 | 当前 store 只认识 `tokyo/TKY`，而 records 里还留着历史 `SRV-0001...`。直接创建会静默重新分配同一个 ID，让两个不同对象共用一个名字——这类损坏在后来的每一次引用里都会继续。 |
| D4 | CLOSED | Quay 适配写在 Provider 版本自己的语义上（`GOAL-NNN`/`AC-NNN` + criterion），不在 ServerOS 侧写死 `PHASE-NNN` | 把设计文档里固定的 `PHASE-NNN` 当作全世界的约定 | 被固定的 archived 版本概念里有 goal store 却没有可调用的 goal CLI；当前 develop有。ID schema 是 Provider 版本的事情，ServerOS 需要知道的是「Goal / criteria /Task / execution / result」这五件事的语义。 |
| D5 | CLOSED | 复用 `scripts/release.py` 的 promote/rollback，不手搓 RELEASE.json | 为工作包 F 写一个自己的发布步骤 | 那条路已经有不可变 revisions/、current/previous 指针、渲染后再交换、验证通过才激活。手搓一份会得到一个只有本轮验证过的第二实现。 |
| D6 | CLOSED | 工作台提交到 `serveros` 仓的 `workbench/`，不在 `records` 仓提交 | 把工作台写进 records 的发布面 | `records` 是另一个 actor 的工作树，测量时有 36 条未提交条目。在白名单 commit 的范围内，向一个别人正在写的工作树提交是不可接受的；而工作台是**产物**，把它和产出它的 revision 放在同一个仓里才是可复算的。records 的发布位置在 DEPLOYMENT 里作为待决项列出，不在这里替它决定。 |
| D7 | CLOSED | 负控保留在产物里（Quay 探针任务、加深两级的 workspace 副本） | 清理掉探针，只留干净结果 | 负控是「这条判据真的会红」的唯一证据。删掉它们，剩下的全绿就退回成一句主张。 |
| D8 | CLOSED | 生成器进产物，渲染出的文档不进：`runtime/workbench.py` 随 `runtime/` 一起发行，`workbench/` 留在源码仓作为被审阅的快照 | 把 15 份渲染好的 .md 也作为 composition 成员发行 | 这个判断在本轮改过一次，改的原因是读产物：最初的写法是「先按不进实现，列为待决」，但真正去写 `bundle.yaml` 时看出两者不是同一类东西。生成器是代码，属于发行；文档是**某一个节点某一时刻的读数**——这台机器的 state 根、覆盖分母、pin 所在路径。发行不是节点，把它装进产物等于把一台机器的读数塞进每一份安装。另有两个具体后果：产物目录会被下一次更新整体替换（正是 `bundle.yaml` 给 MCP adapter 设 PYTHONDONTWRITEBYTECODE 的同一个理由），写进去的东西会被它本要服务的那件事销毁；而且产物里的 `runtime/` 是 `release.py` 按 hash 验证过的 staged revision，改写会让 RELEASE.json 描述一棵已经不存在的树。所以 `workbench render` 在安装里拒绝默认输出目录并按名字说明原因，要求显式 `--out`；`check` 在目录不存在时返回 2 说不曾比较过任何东西，而不是报 15 份文档缺失。 |
| D9 | OPEN | （待定）`records/<project>/` 与 `records/projects/<project>/` 两套布局是否合一 | （待定） | 这个分歧在本轮被观察到（同一份东西两种写法都出现过）。合一需要改别的 actor 正在写的工作树，超出本轮白名单范围。 |

*本页内容是作者的输入，不是任何命令的产物。见 `runtime/workbench_sources.py`。*