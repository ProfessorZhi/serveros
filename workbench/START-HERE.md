<!-- ⟦snapshot⟧ rendered 2026-09-20T11:56:36+00:00 by `runtime/workbench.py` from ~/workspace/worktrees/serveros/SRV-0011 · inputs: projects=serveros,litellm,multi-user-claw; project=litellm; item=LIT-0101; quay-run=~/artifacts/work-items/serveros/SRV-0011/RUN-0001/quay-run; quay-bin=~/workspace/worktrees/quay/foundation-skeleton/plugin/vendor/quay/dist/quay.js; ability-repo=~/workspace/worktrees/ability-management/SRV-0011; reports-dir=~/artifacts/work-items/serveros/SRV-0011/RUN-0001; release-root=~/artifacts/work-items/serveros/SRV-0011/RUN-0001/release/dist/serveros-linux-work -->

# START-HERE

> **主张** — 本页内容由命令的判定产生，重跑应当复现。状态矩阵是 23 条命令的判定，不是读数。渲染时会重跑它们并比对同一份判定块。`serverosctl workbench check` 会整页比对。

<!-- ⟦answers⟧
serverosctl status --json	0
serverosctl device show --json	0
serverosctl resource summary --json	0
serverosctl process top --json	0
serverosctl container attribution --json	0
serverosctl anomaly list --json	0
serverosctl fs hygiene --json	0
serverosctl tunnel list --json	0
serverosctl ability list --json	0
serverosctl ability tree --json	0
serverosctl taxonomy tree --json	0
serverosctl ability resolve --request abilities/requests/resolve-example.json --json	2
serverosctl ability resolve --request abilities/requests/resolve-example.json --catalogue abilities/requests/catalogue-example.txt --json	2
serverosctl registration discover --json	0
serverosctl registration resolve --json	0
serverosctl card status --json	0
serverosctl card alert --json	0
serverosctl card recovery --json	0
serverosctl alert list --json	0
serverosctl requirement board --json	0
serverosctl work board --json	0
serverosctl work coverage --project serveros --json	0
serverosctl work skeleton brief --json	0
serverosctl workspace overview --json	0
-->

<!-- ⟦verdicts⟧
runtime=OK
device=OK
resource=OK
process=OK
container=OK
anomaly=OK
fs=OK
tunnel=OK
ability=OK
ability-tree=OK
taxonomy=OK
ability-resolve=OK
ability-resolve-catalogue=OK
registration-discover=OK
registration-resolve=OK
card-status=OK
card-alert=OK
card-recovery=OK
alert-list=OK
requirement-board=OK
work-board=OK
work-coverage=OK
work-skeleton=OK
workspace=OK
-->
这是本轮骨架的检查入口。它不介绍架构，只回答一个问题：**哪些东西现在可以从这台机器的入口真正走到，哪些只是存在。**

每一节都是跑一条命令得到的。命令和退出码一并印出，所以任何一行都可以自己复算；不一致的行渲染成 **FAILING** 而不是被删掉。

### 怎么读

1. 先看下面的状态矩阵 —— 它是这一轮「已实现」这个词的实际含义。
2. 再看 [DEPLOYMENT.md](DEPLOYMENT.md) —— §8 的最小变更清单。**没有任何一条已执行。**
3. 然后按需展开其余文档。每份文档顶部都写了它是哪条命令渲染的。

### 入口清单

| 文档 | 它是什么 | 渲染自 |
|---|---|---|
| [START-HERE](START-HERE.md) | 这一页。状态矩阵与四栏判定 | `本页` |
| [ENVIRONMENT](ENVIRONMENT.md) | 环境/能力地图 | `serverosctl ability tree, taxonomy tree` |
| [STATUS-PREVIEW](STATUS-PREVIEW.md) | 状态卡预览 | `serverosctl card status` |
| [ALERT-PREVIEW](ALERT-PREVIEW.md) | 告警卡预览 | `serverosctl card alert` |
| [RECOVERY-PREVIEW](RECOVERY-PREVIEW.md) | 恢复卡预览 | `serverosctl card recovery` |
| [COVERAGE](COVERAGE.md) | 磁盘与三个飞书面覆盖页 | `serverosctl work coverage` |
| [UNDERSTANDING-DELTA](UNDERSTANDING-DELTA.md) | 关注源与认识变化 | `serverosctl work skeleton understanding` |
| [BOARD](BOARD.md) | 跨项目看板 | `serverosctl requirement board, work board` |
| [TODAY](TODAY.md) | 今日重点：一次选择，不是一次派生 | `serverosctl work board` |
| [ITEM-CHAIN](ITEM-CHAIN.md) | 单项 brief/design/plan | `serverosctl work item, work skeleton` |
| [DECISIONS](DECISIONS.md) | 本轮的取舍 | `作者输入 runtime/workbench_sources.py` |
| [REPORTS](REPORTS.md) | 日报与 Decision Packet | `运行产物目录里的日报文件` |
| [QUAY-RESULTS](QUAY-RESULTS.md) | Quay Goal/Task 对应与执行结果 | `quay goal list, task list, GateEvent 账本` |
| [RELEASE-MANIFEST](RELEASE-MANIFEST.md) | pin、摘要与可达性 | `git cat-file, git ls-remote, 隔离 root 自身的符号链接结构` |
| [DEPLOYMENT](DEPLOYMENT.md) | 待部署清单（未执行） | `作者输入 runtime/workbench_sources.py` |

### 状态矩阵（24 条 OK，0 条 FAILING）

| id | 主张 | 命令 | 实测 |  |
|---|---|---|---|---|
| runtime | serverosd 在跑，并且报告它最后一次看的时间 | `serverosctl status --json` | exit 0 | OK |
| device | 这台设备的 CPU/内存/load/GPU/磁盘事实 | `serverosctl device show --json` | exit 0 | OK |
| resource | 按 role 分的资源压力（根盘 vs 数据盘） | `serverosctl resource summary --json` | exit 0 | OK |
| process | 进程按 group/session/container/role 归属 | `serverosctl process top --json` | exit 0 | OK |
| container | 容器归属，证据留在结论旁边 | `serverosctl container attribution --json` | exit 0 | OK |
| anomaly | 异常码 + 未评估项（未评估不是阴性） | `serverosctl anomaly list --json` | exit 0 | OK |
| fs | 文件系统根分类（只报告，不移动） | `serverosctl fs hygiene --json` | exit 0 | OK |
| tunnel | 隧道与结构性盲区（hidepid=invisible） | `serverosctl tunnel list --json` | exit 0 | OK |
| ability | 本节点的能力投影，以及它有多新鲜 | `serverosctl ability list --json` | exit 0 | OK |
| ability-tree | 从分类元数据派生的能力树（不看文件位置） | `serverosctl ability tree --json` | exit 0 | OK |
| taxonomy | 声明的导航 DAG，投影成树 | `serverosctl taxonomy tree --json` | exit 0 | OK |
| ability-resolve | 八输入一个未给：UNKNOWN，且把八个输入全部具名列出 | `serverosctl ability resolve --request abilities/requests/resolve-example.json --json` | exit 2 | OK |
| ability-resolve-catalogue | 给了 catalogue 之后：它读到了它，然后只指出剩下没给的 | `serverosctl ability resolve --request abilities/requests/resolve-example.json --catalogue abilities/requests/catalogue-example.txt --json` | exit 2 | OK |
| registration-discover | 观察到却没有注册的东西，具名列出 | `serverosctl registration discover --json` | exit 0 | OK |
| registration-resolve | 每条注册对照实测：OBSERVED/ABSENT/STALE/UNREPORTED | `serverosctl registration resolve --json` | exit 0 | OK |
| card-status | 状态卡：一个模型，Feishu JSON，一个预览 | `serverosctl card status --json` | exit 0 | OK |
| card-alert | 告警卡：最多三条事件，各带未容纳计数 | `serverosctl card alert --json` | exit 0 | OK |
| card-recovery | 恢复卡：只讲被通知过、且已恢复的 | `serverosctl card recovery --json` | exit 0 | OK |
| alert-list | 告警账本：持久化的是同一个问题，不是每分钟一条 | `serverosctl alert list --json` | exit 0 | OK |
| requirement-board | 跨项目需求看板 | `serverosctl requirement board --json` | exit 0 | OK |
| work-board | 个人看板：§6 的分节与顺序 | `serverosctl work board --json` | exit 0 | OK |
| work-coverage | 覆盖页：磁盘、三个飞书面、关注源，各带分母 | `serverosctl work coverage --project serveros --json` | exit 0 | OK |
| work-skeleton | 文档骨架：每一节为什么在那里 | `serverosctl work skeleton brief --json` | exit 0 | OK |
| workspace | 放置与治理：一个东西属于哪里 | `serverosctl workspace overview --json` | exit 0 | OK |

### 已实现 / 样例 / live 未验证 / 待部署

**已实现**：上面 exit 0 的行。它们跑在这台机器上，读的是这台机器的真相。

**样例**：由固定输入产生的产物 —— 见下方清单。样例通过说明离线路径通过，**不说明任何连接可用**。

**live 未验证**：代码在，但从未在真实链路上走过。这一栏是这一轮最该被盯着看的地方。

**待部署**：需要单独批准的动作，见 [DEPLOYMENT.md](DEPLOYMENT.md)。

### 样例

- `~/artifacts/skills/serveros/RUN-20260920-foundation-skeleton-f/release/hwz-server-os/serveros-linux-work/current`（存在）— 隔离安装：一个 runtime root 下不可变的 revision 加 current/previous 指针。由 `scripts/release.py promote` 落的，不是手工拷的；入口从 `/tmp` 走通
- `~/artifacts/skills/serveros/RUN-20260920-foundation-skeleton-f/install/build.log`（存在）— 构建日志：5 个 pack 的 pin 状态与产物落点。`scripts/build.py --write` 的原始输出
- `~/artifacts/skills/serveros/RUN-20260920-foundation-skeleton-f/install/check.log`（存在）— 确定性构建检查：同一输入两次构建逐字节一致。`scripts/build.py --check`，两次 tree_hash 相同
- `~/artifacts/skills/serveros/RUN-20260920-foundation-skeleton-f/quay/ws`（存在）— Quay Goal/AC/Task 记录与 GateEvent 账本（本轮真实执行）。`quay goal write` / `quay goal gate` / `quay task` 在隔离 workspace 里跑出来的
- `~/artifacts/skills/serveros/RUN-20260920-foundation-skeleton-f/quay/probe-deeper/a/b/ws2`（存在）— 负控：同一份 config 放深两级的 workspace。复现 provider `path` 的深度依赖（`spawn node ENOENT`）
- `~/artifacts/skills/serveros/RUN-20260920-foundation-skeleton-e/negative`（存在）— 工作包 E 的三个负控：陈旧 revision / 子任务缺席 / complete 事件被删。每个负控单独 exit 1 并给出具名原因；未做手脚的副本 exit 0
- `~/workspace/worktrees/serveros/SRV-0011/runtime/card-scenarios.yaml`（存在）— 六类卡片场景（正常/部分未知/单异常/观测失败/混合/仅恢复）。测试与预览读同一份输入，所以审阅的卡片就是测试跑过的卡片
- `~/workspace/worktrees/serveros/SRV-0011/runtime/workbench_sources.py`（存在）— 工作台里不是由命令产生的**那一半**：状态矩阵的主张、九条取舍、待部署清单、pin。这一页与它背后的文档由 `serverosctl workbench render` 生成；作者输入在这里，所以「这条主张是谁写的」有答案，而不是藏在生成器里。**本文件集自身不作为样例列出**，因为 `render` 第一次写它时它还不存在，把自己作为存在性样例会让首次 `render` 之后的 `check` 必红一次

### live 未验证

- **真实飞书投递** — `card` 渲染 payload 但不持有 transport；发送需要一个收件人，而收件人由人指定。本轮投递次数为 0。
- **飞书客户端回读** — 没有发送就没有回读。布局验证目前只到「同一份 payload 的离线渲染」。
- **CC 插件 / Hook 在真实会话中加载** — 覆盖现用插件是 §8 的 P2，需要单独批准；本轮没有动过任何会话实际加载的东西。
- **serverosd 指向新 revision（线上 promote/restart）** — 本轮只在隔离 runtime root 上跑通了 promote/rollback。线上指针未动，见 P1。
- **三个飞书空间的 live 抓取** — 覆盖页读的是离线契约与本地测量；没有任何适配器声称支持 live 抓取。

### 未完成

- **认识变化（delta）的产生者** — 认识层有卡片与 Sources 溯源，但没有东西把「今天知道了什么」读成一次变化。`UNDERSTANDING-DELTA.md` 因此是 PARTIAL，并且写明缺的是哪一个输入。
- **serveros 项目的关注源清单** — `work coverage` 报 `sources_state = UNPRODUCED`：读取器在，但 `records/projects/serveros/context/sources.yaml` 没有被写出来。
- **每日定时任务的已启用实例** — 只交付默认 disabled 的模板，并且本轮不启用它 —— 启用是 §8 的 P4。
- **board 的优先级（P0/P1）** — `requirement board` 报 `priority.state = UNPRODUCED`：这套 fabric 里没有任何 priority/severity 字段，看板的优先级分节因此全是空的。这是一个缺失的**产生者**，不是一个读错的值。
- **`today.yaml` 的一次真实选择** — 四个 ACTIVE 的 Item 在飞，而今日重点没有被记下来过。看板列出所有在做的事，与「今天做这两件」是两回事。

### 待部署

| id | 动作 | 回退 |
|---|---|---|
| P1 | 把 serverosd 指到新 revision（promote + restart） | `scripts/release.py rollback --runtime-root ~/runtime-assets/hwz-server-os`（previous 指针已在 promote 时写下），再 restart |
| P2 | 覆盖现用的 CC 插件 / Hook | 把插件目录指回记录下的 revision；hook 段按记录恢复 |
| P3 | 切换生产卡片 timer（tokyo-status-card / tokyo-alert-digest） | `systemctl --user disable --now` 回原 timer，并恢复原 unit 文件 |
| P4 | 启用每日定时任务（日报/看板生成） | `systemctl --user disable` 并删除已生成的 unit |
| P5 | 真实飞书投递与客户端回读 | 无法撤回已投递内容；回退的是后续投递（停 timer） |
| P6 | `records` 发布位置写入工作台 | `git revert` 该条提交 |
