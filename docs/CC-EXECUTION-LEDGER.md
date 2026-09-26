# CC execution ledger

P0 的产物，与 `CC-EXECUTION-PLAN.md` 同目录。计划要求「输出一份本轮
implementation ledger（现有 Item/execution 记录），后续每包只更新此处
结果/证据，不另建平台」——所以这份文件是那张表本身，不是又一份设计。

## 0. 这份文件是什么

**是**：每个待改对象的 owner / base / consumer，21 个语义职责的四层状态
（DESIGN / CODE / INTEGRATION / LIVE），以及所有**已经存在**的红。

**不是**：不是第二份总架构。任何与设计冲突的地方，设计赢，这份文件改成
描述实现；反过来不成立。`design/ABILITY-CANONICAL-BLUEPRINT.md` 是 21 的
权威，`abilities-manifest.yaml` 是注册表的权威，这份文件只记录**它们落到
盘上没有**。

## 1. 方法与边界

计划 P0 有一句必须照做的话：「未读源码/未测状态不能用『缺失』一词盖过」。
所以本文用三个不同的词：

| 词 | 意思 |
| --- | --- |
| `absent` | 读过盘，确认真没有。同义词是「不存在」，不是「没找到」。 |
| `not read` | 没读。**不是**缺失的证据，只是本轮没看。 |
| `UNBOUND` | 有该职责的设计，没有任何 provider 绑定。缺失的可选 provider 一律记这个，记 `READY` 是错的。 |

四层状态的定义见 §4。「代码在盘上」永远不算 LIVE。

## 2. 本轮 live 状态（2026-09-22 实测，promote 后重测）

```text
checkout    /data/home/vince/workspace/src/serveros                     d0a94d6 [main]
worktree    /data/home/vince/workspace/worktrees/serveros/fabric-integration-20260922
                                                                       40ffaa5 [serveros/fabric-integration-20260922]
canonical   /data/home/vince/workspace/worktrees/ability-management/fabric-integration-20260922
                                                                       074a518 [integration/fabric-20260922]
packs pin   authority.revision = e7d20f3b50e1a86c9998b5dff3f519fa52b4aa1d
```

两条集成分支各自相对自己的 `origin/main` 的关系，2026-09-22 订正后重测：

| 仓 | 分支 | `origin/main` | 相对它 | 远端 | 本工作树 = 远端 |
| --- | --- | --- | --- | --- | --- |
| serveros | `serveros/fabric-integration-20260922` | `4d090d6` | `0 36` | 同名分支已 non-force 推 | 是（`40ffaa5` 两边逐字相同） |
| ability-management | `integration/fabric-20260922` | `7ea44af` | `0 10` | 同名分支已推 | 那是 canonical 检出，不是本工作树 |

上一版这一格写的是 worktree `fb97f77`、并说「本轮三个 commit 落在它上面
（`aef122d`、`dc23707`、`2f77b02`、`fb97f77`）」——**同一个句子里数了三个、列了四个**，
而且那两个数今天都不是现状（本分支已有 36 个提交，其中 10 个是集成分支带过来的、
26 个是本轮落的）。这一格是「重测」标题下唯一一处没有跟着重测的地方，所以现在补上；
改的是这一格，不是那些读数。

**安装面与发布面原本不是同一棵树**，现在**发布面已经收敛，安装面还差一次 update**：

| 面 | 指向 | 版本 | 时间 |
| --- | --- | --- | --- |
| 已安装 plugin（工作台） | `~/.claude/plugins/cache/hwz-server-os/hwz-server-os/3.1.0-srv0003.20260915a` | `3.1.0+srv0003.20260915a` | installedAt 2026-09-17 |
| CC marketplace `hwz-server-os` 的 source | `runtime-assets/hwz-server-os/current`（**真实目录**，不指向 `revisions/`） | `3.1.0+srv0003.20260915a`，3 skills | lastUpdated 2026-09-16 |
| release 管理的 composition | `runtime-assets/hwz-server-os/serveros-linux-work/current` → `revisions/cards-and-release-20260922@2f77b02` | `3.1.0+srv0003.20260922a`，5 skills | promote 于 2026-09-22 14:55 |
| 它的 `previous` | `revisions/noc-copy-20260922@c233946` | `3.1.0+srv0003.20260915b` | 回滚是一条命令 |
| Codex 描述符 | `runtime-assets/hwz-server-os/.agents/plugins/marketplace.json` → `./serveros-linux-work/current` | — | 已由构建产出 |

三件事必须分开读，因为它们过去被读成了一件：

1. **release 面已经对齐。** `serveros-linux-work/current` 现在指 `@2f77b02` 那次
   promote，`RELEASE.json` 记 `kind: node-runtime`、`kind_basis: chosen`、
   `tree_hash 5e8694ce…`、`previous noc-copy-20260922@c233946`。`previous` 在，
   回滚有对象。
2. **Codex 描述符的那个缺陷已修。** 它曾经写 `./.staging-c233946`——一个可被清理
   的目录名。修法是构建期判据（`build.py::_is_transient`），不是改那一个字符串：
   任何以 `.` 开头的路径分量都算「没人承诺保留」，于是描述符改写
   `./<composition>/current`。见 §5。
3. **CC 那侧的安装面还没动，而且不该由本轮动。** CC 的 marketplace
   `hwz-server-os` 指的是**遗留目录** `hwz-server-os/current`（不是 symlink 进
   `revisions/`），它的 `.claude-plugin/marketplace.json` 声明的 plugin 名是
   `hwz-server-os`；而新 release 里 `.claude-plugin/plugin.json` 声明的名字是
   `hwz-server-os-linux-work`。**把 marketplace 指过去等于给已装 plugin 改名**——
   那是一次用户可见的身份变更，不在本轮授权里。所以：报告，不改。已装那一份仍然
   是 `srv0003.20260915a`（3 skills），差一次 `claude plugin update`。

已跑的服务：

```text
serverosd.service           active running   MainPID 2615936，起于 2026-09-22 11:00:26
tokyo-status-card.timer     active waiting   每小时 :00（北京整点）
tokyo-alert-digest.timer    active waiting   每 5 分钟
```

**daemon 没有被重启**：`current` 已经是新 revision，但 PID 2615936 仍在执行 promote
之前那一版。重启它会打断这台共享机上别人正在跑的东西，所以留作
`HUMAN_EXECUTED`，等一个安全窗口。`~/.local/bin/serverosctl` 这个 shim 已经指到
`revisions/cards-and-release-20260922@2f77b02/bin/serverosctl`（shim 是路径跳转，
不随进程走），所以**新开进程拿到的是新代码，长驻进程还是旧的**——这个区别正是
「代码在盘上不等于 LIVE」在同一个节点上的实例。

## 3. 21 个语义职责

21 = G1 + A1–A10 + B1–B10，见 `design/ABILITY-CANONICAL-BLUEPRINT.md` 第 56 行
起的表。**21 是语义职责数量，不是 21 个 Skill，也不是 21 个插件**——多对一
的映射是刻意的，见 §3.2。

source revision 一列给的是**指纹成立的那次 revision**，也就是 packs 的
`authority.revision` `ce6e103`：在那个 revision 上 `abilities-manifest.yaml`
与当前 `c993c82` 逐字节相同（`packs` 的祖先规则就要求这一条），所以这一刻
的指纹在两者上都成立。指到 `c993c82` 也不假，但 `ce6e103` 是更强的说法。

### 3.1 表

| # | 职责 | semantic owner | canonical artifact | shape | source rev | consumer | Binding | installed rev | CODE | INTEGRATION | LIVE | blocked |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| G1 | 能力治理 | ability-management | `skills/ability-management` | Skill | f5e563b | registry/build/install 工具 | 原生 | `3.1.0+srv0003.20260915a` | DONE | DONE | ENABLED | — |
| A1 | 接续 | agent-session-management | `skills/agent-session-management`、`skills/session-manager` | Skill ×2 | 早于本轮 | `serveros-agent` MCP | 原生 | srv0003.20260915a | DONE | DONE | ENABLED | — |
| A2 | 记忆检索 | shared-memory | `skills/shared-memory` | Skill | 早于本轮 | memory 原生/records | **UNBOUND**（OpenViking 未绑） | srv0003.20260915a | DONE | DONE | PARTIAL | OpenViking provider 无处可绑 |
| A3 | Workspace/Git | workspace-maintainer | `skills/workspace-maintainer`（运行时名仍 `workspace-management`） | Skill | 早于本轮 | 原生 git/gh | 原生 | srv0003.20260915a | DONE | DONE | ENABLED | — |
| A4 | 运行环境/Fleet | runtime-management、test-management | `skills/runtime-management`、`skills/test-management` | Skill ×2 | 早于本轮 | `serverosd`、`serveros-node` MCP | 原生 | srv0003.20260915a | DONE | DONE | ENABLED | — |
| A5 | Fleet 交接 | fleet-coordination | `skills/fleet-coordination` | Skill | 早于本轮 | 已授权 transport | **UNBOUND** | srv0003.20260915a | DONE | DONE | PARTIAL | 单设备路径，跨设备动词 absent |
| A6 | 委派 | cc-quay-delegation | `skills/cc-quay-delegation` | Skill | 早于本轮 | 原生 Quay 0.11.0 | 原生（quay@quay 已装） | srv0003.20260915a | DONE | DONE | ENABLED | — |
| A7 | 自动化测试 | automated-testing | `skills/automated-testing` + `plugin/profiles/testing.yaml` | Skill + Plugin composition `serveros-testing` | ce6e103（Skill）/ f5e563b（profile） | 目标 CC 以正规 install 加载 | 原生 | `1.0.0+srvtest0001.20260927a` | DONE | DONE | **ENABLED** | 见 §3.3、§9.50 |
| A8 | 构建发布 | build-release | `skills/build-release` | Skill | **ce6e103** | 项目 builder/CI | 原生 | 随 `srv0003.20260922a` | DONE | DONE | ENABLED | 已加进 `pack:litellm-development`（§7 第 5 条） |
| A9 | 生产观察 | production-observation | `skills/production-observation` | Skill | **ce6e103** | 既有 API/CLI/跳板 | **UNBOUND** | 无 | DONE | NOT_MEASURED | NOT_ENABLED | 未装进任何 composition |
| A10 | 生产变更 | production-change | `skills/production-change` | Skill | **ce6e103** | 调用 A9 + 真实 Runbook | **UNBOUND** | 无 | DONE | NOT_MEASURED | NOT_ENABLED | 同 A8；且 READY≠切流授权 |
| B1 | 来源采集 | information-management | `skills/information-management` | Skill | 早于本轮 | `records/information/raw/*` | 原生 | srv0003.20260915a | DONE | DONE | ENABLED | — |
| B2 | 结构化认识 | information-management（共用 payload） | 无独立 artifact（读者在 `intake/understanding_delta.py`，写者在 `intake/understanding_patch.py`） | — | 早于本轮 | affected people/project/topic | 原生 | 未 promote（本轮新增） | DONE | DONE | NOT_ENABLED | 设计就写「可先共用 payload」。读的一侧本轮补上（§9.7），**写的一侧本轮补上**（§9.9）：`serverosctl work understanding-patch`。CODE 从 PARTIAL 转 DONE 的条件是两侧都在，且**两者互不代表**——写者只消费读者的区间，确认仍是人工输入。LIVE 仍是 NOT_ENABLED：没有消费者读这份 patch |
| B3 | 需求 | product-requirements-and-design | `skills/product-requirements-and-design` | Skill | 早于本轮 | — | 原生 | srv0003.20260915a | DONE | DONE | ENABLED | — |
| B4 | 工作管理 | work-management | `skills/work-management` | Skill | 早于本轮 | Work Engine MCP/CLI | 原生 | srv0003.20260915a | DONE | DONE | ENABLED | — |
| B5 | 调研 | research-evidence | `skills/research-evidence` | Skill | **ce6e103** | Serper / 既有专业知识 | **UNBOUND** | 无 | DONE | NOT_MEASURED | NOT_ENABLED | 未装进任何 composition |
| B6 | 方案 | design-and-contract + 专业 Skill | `skills/design-and-contract`、`VenusFennn/Backend-Solution-Design`、`VenusFennn/Frontend-Solution-Design` | Skill ×3 | 早于本轮 | — | 原生 | srv0003.20260915a | DONE | DONE | ENABLED | 旧入口分步迁移未做 |
| B7 | 实现 | implementation-delivery | `skills/implementation-delivery` | Skill | 早于本轮 | — | 原生 | srv0003.20260915a | DONE | DONE | ENABLED | 重叠旧入口未迁移 |
| B8 | 报告发布 | feishu-report | `skills/feishu-report` + `runtime/report-publication-contract.yaml` | Skill + 契约 | 早于本轮 | `serverosctl report`、那四个移除的接手方 | 原生 | srv0003.20260915a | DONE | PARTIAL | PARTIAL | 见 §3.5：**没有独立 artifact 是设计选定的落法**。契约与检查器已落地（31 测试），四处移除仍 `PENDING_CONSUMER`，被 `srv-0005-e2e-evidence` 的活动工作树挡着 |
| B9 | 文档维护 | document-stewardship | `skills/document-stewardship` | Skill | **ce6e103** | feishu-space-management + publisher | 原生（仅正文侧） | 无 | DONE | NOT_MEASURED | NOT_ENABLED | 位置/ACL 侧留在 B-空间 |
| B10 | 人机控制 | human-control | `skills/human-control` | Skill | 早于本轮 | `serverosctl card`/presentation | 原生 | srv0003.20260915a | DONE | DONE | ENABLED | 告警卡面已在生产链上；status 卡面仍未接，见 §3.6 |

图例：`DONE` 已落地并测过；`PARTIAL` 只落了一部分；`NOT_MEASURED` 没测，**不是**失败也不是通过；
`NOT_ENABLED` 已明确没启用；`ENABLED` 在生产/日常链路上真在跑。

### 3.2 为什么 21 不等于 21 个 artifact

- G1 与 A4 各是两个 artifact 担一个职责（`ability-management` + 治理工具；`runtime-management` + `test-management`）。
- B6 是三个 artifact 担一个职责，其中两个是专业 Skill（前后端），设计明说要保留。
- A1 是一个职责两个 artifact：`agent-session-management` 管实时控制，`session-manager` 管历史归档。
- B1 与 B2 是**一个 artifact 担两个职责**：B2 没有独立 payload，设计原话是「可先共用 payload」。
- A7 是唯一一个职责落成 **Plugin composition**（不是因为职责需要插件，而是因为它要装到一个**别的** CC 上——见设计 §10「部署时必须证明 Plugin 从目标 CC 实际加载」）。

### 3.3 A7 已经装好（这一节从「blocker」改写为「怎么装的」）

先前记录的理由是「`release.py` 没有承载非 node composition 的动词」。**这条是错的**，
已被两件事同时推翻：

- `runtime-assets/hwz-server-os/serveros-linux-work/` 是完整的 release 布局，而
  `serveros-linux-work` **本身就是** `plugin/profiles/linux-work.yaml` 那个 composition。
  所以 `release.py promote` 完全能承载 composition，它天天在这么做。
- 真正的问题比那条窄：`stage()` 里有一条**硬要求 `bin/serverosd`** 的判据。那不是
  「composition 不能 release」，而是**一个 node runtime 的 verifier 在替所有 release
  类型做决定**。它现在按 `ARTIFACT.json` 的 `kinds` 分派：`node-runtime` 走原来的清单，
  `plugin` 走 plugin verifier（`plugin/dist` 在、plugin manifest 在、声明的 name/version/
  cache 身份一致、skills 真的materialize了、MCP/hook/agent 声明与 composition 逐条相符、
  没有未声明文件、加载 smoke）。**没有第二个 `testing-release.py`。**

于是 A7 走的就是那条共用的生命周期：build → 不可变 candidate → verify →
`revisions/<revision>` → `current`/`previous` → RELEASE receipt → rollback。

实测（`kind: plugin`，`kind_basis: chosen`）：

```text
build      3.1.0+srvtest0001.20260922b   （cachebuster 从 a 移到 b）
release    revisions/fabric-integration-20260922@2f77b02
current    runtime-assets/hwz-server-os/serveros-testing/current -> 上面那一版
validate   claude plugin validate                      → 通过
marketplace claude plugin marketplace add .../serveros-testing/current
install    claude plugin install serveros-testing@serveros-testing
details    serveros-testing  Skills (2)  automated-testing, test-management  ~218 tok
```

**fresh session 验证**：另起两个 headless 会话，一个列出 `serveros-testing:automated-testing`，
第三个真的调用了 `serveros-testing:test-management` 并返回了它的真实内容。全程
**没有用 `--plugin-dir`**（那只是集成证据，不是安装证据），也**没有手改
`installed_plugins.json`**。已装路径
`~/.claude/plugins/cache/serveros-testing/serveros-testing/1.0.0-srvtest0001.20260922b`，
`installedAt 2026-09-22T06:53:45Z`。

> 上面这一段记的是**那一次**安装，它对那一刻成立。已装路径此后随 cachebuster 移到了
> `1.0.0-srvtest0001.20260927a`（`lastUpdated 2026-09-26T16:33:29Z`），
> 因为 `22b` 那份载荷把 `skills/test-management/SKILL.md` 里的测试环境根写成了一台机器的
> 字面量。载体级的实测见 §9.50；`22b` 的缓存仍在盘上，规则对它的 `skills/` 量到 **1** 条，
对 `27a` 量到 **0** 条。

§7 里那两条旧决策（「要不要给 `serveros-testing` 注册第二条 plugin 记录」「要不要在这台
共享机上装它」）因此都已关闭：注册表里它是**独立的第二条 plugin 记录**（§3.7），安装走的
是正规 plugin 安装路径。

### 3.5 B8 为什么没有独立 artifact（这不是缺口）

设计 §8 写得很清楚：「feishu-report 保留正式中文/用途差异和既有 report-consumer 映射…
**只有实际需要才独立新 Skill 身份**。」所以「没有 `reporting` 这个 Skill」是设计选定的落法，
不是没实现。把它记成 absent 会把一个决定读成一个遗漏。

真正没做的是同句里那四处**移除**，实测四处都还在：

| 设计说要移除 | 现在还在哪 | 本该归谁 |
| --- | --- | --- |
| 复制的测试执行 | `payload/SKILL.md:186-191` 教自己跑 `pytest --junitxml=` 取 e2e 证据；`:202-207` `upload_images()` | A7 / `test-management` |
| space 规则 | `payload/SKILL.md:252` `lark-cli wiki +node-list --space-id … --parent-node-token` | B9 的 `feishu-space-management` |
| 默认 whole overwrite | `payload/SKILL.md:58,181,206` 一律 `docs +update --command overwrite` | B9 的 `document-stewardship`（绑定 base revision 的局部 patch） |
| 强制 Milestone | `payload/SKILL.md:10,13,37,41,44` 每个可说明阶段必须新增不可变 Milestone Report | 设计说这不再是强制 |

设计同时定了顺序：「**先保留兼容入口再迁 consumer**」——移除不能先于 consumer 迁移做。
这一条决定了 B8 是一个按顺序走的包，不是一次顺手改文件。

**这一轮做完的是「可被复核」那一半**，因为「保留入口」和「迁移完成」在交付里读起来是
一样的：

| 物件 | 是什么 |
| --- | --- |
| `runtime/report-publication-contract.yaml` | 说出还路由到谁、还欠哪四件事、欠给谁、被谁挡住 |
| `runtime/report_publication.py` | 只做一件事：**拒绝**一份说漏了其中任一条的契约 |
| `tests/test_report_publication.py` | 31 条，绝大多数是「这几种写法必须被拒」 |

几处刻意的判断，都不是格式偏好：

- **`owners: []` 与缺字段是两个答案。** `compulsory-milestone` 是**取消**这条要求，
  不是把它挪给别人，所以它的接手方是空的——但空名单必须带 `why`，否则它和「忘了填」
  长得完全一样。
- **`state` 只有 `PENDING_CONSUMER` 一个值。** 已迁完的职责应当从 `delegations` 里
  消失，而不是换个词留在原地，所以 `MIGRATED` 不是一个可写状态。
- **证据带 `at_revision`。** 行号只在指定 revision 时才有意义——本仓已经因为读工作树
  行号得到过「引错了」的假结论一次。
- **`facade.route` 与两处本地权威逐字比对，而两处说的不是同一句话**：
  `serveros-contract.json` 给路由字符串，`context/resolver.py` 给的是「哪些 kind 是发布
  物」的 capability 名单（没有 `report.publish` 这个键）。第一次运行时这条检查就拒绝了
  我自己的契约——我把后者也写成了 `route-string`，于是它读到 `None`。按各自的形状声明
  才是诚实的写法，而拒绝本身是这条检查在工作的证据。

唯一被挡住的 consumer 是 `srv-0005-e2e-evidence`，处置写作
**`MIGRATION_WAITING_ON_ACTIVE_CONSUMER`**：它的活动工作树
（`worktrees/ability-management/SRV-0005`，branch `serveros/srv-0005-e2e-evidence@22cd2a2`）
正在改这份契约要动的那三个路径，且尚未提交。契约里记下了那三个路径与五个待落接口，
等它合并后按表逐条落。

### 3.6 B10 的 LIVE 到哪一步

先纠一条曾被读反的：**告警摘要卡的卡面已经是 ServerOS 出的**。
`ops/scripts/tokyo-monitor/alert_digest_card.py:107 serveros_card()` 调
`serverosctl card alert --json` 并把返回的 `body` 当卡面用（注释写着「2026-09-22 起，
人类语义只有一个 authority」）；只有在取不到时才回落旧渲染器，且回落会往 stderr
写一行。实测 `serverosctl card alert --json` exit 0，给出的是真实的飞书
`body.elements`，点名一条活着的 finding（`RUNTIME_OUTLIVED_OWNER`，Claude 会话 15413）。
所以 `card` 这个面**在生产链上**，B10 的 LIVE 成立。

链条本身是活的，读出来的形状是：

```text
anomaly(detectors, runtime/anomaly.py) 
  -> alert producer (runtime/alerting.py:461 evaluate(), 身份 = code+device+subject)
     唯一生产调用者 = daemon tick (runtime/daemon.py:569)
  -> card model (runtime/cards.py:156 alert_card(); catalogue runtime/card-definitions.yaml)
     -> serverosctl card {status,alert,recovery}  (cli.py:6721/6732/6736, 恰好三个)
  -> ops alert_digest_card.py 取面 -> lark-cli 发
```

登记表现状（写这一节时的读数）：`serverosctl alert list --json` exit 0，**4117 条**
（795 OPEN / 3322 RESOLVED），其中 701 条带 `last_notified_at`。

缺口在三处，都实测过。**这三条是写下时的读数，不是现状**——第 1、2 条后来已修，
判词在 §9.2；每条下面带一条 `现行`，因为这一节与 §9.2 曾经给过相反的判词，
而同一份台账里两笔账正是它自己反复警告的东西：

1. **没有冻结 payload / payload hash。** 设计 `BROADCAST-ALERT-DESIGN.md:80` 要求
   `frozen presentation payload + payload hash + intended rows/versions`；
   `grep -rn 'payload_hash' runtime/*.py` → 0 命中。这是读过的 absent，不是没找到。

   **现行（2026-09-22 复测）：已修。** `runtime/alert_digest.py:693` 起 `sha256(blob)`
   → `payload_hash`，`version = f"{generation}@{payload_hash[:12]}"`，冻结块带自己的
   schema `serveros/alert-digest-frozen.v1`；`runtime/card_delivery.py:134` 是同一形状。
   上面那句「0 命中」属于**当时那个 revision**，留在这里是因为它是 §9.2 那次修复的
   对照，不是因为今天还成立。
2. **`alert_digest.record()` 盖的是活登记表，不是发出去的那一版。**
   `alert_digest.py:730 _shown_pairs()` 把冻结文档压成 `(key, kind)`，写回时
   `:790` 取 `alerts.get(key)` 的**当前**值，`:811` 和 `:822` 于是把注册表的现值
   记成「这一版就是这么播的」。compose 与 record 之间若发生 severity 升级，
   记录会把它写成以新 severity 播过。（相邻的 `alert is None` 分支 `:792-800`
   反而正确写了 `None`，说明这个坑是知道的。）

   **现行：已修。** `record()` 现在收 `frozen`（`runtime/alert_digest.py:933-935`），
   severity / state / **episode** 都取自冻结块，`_frozen_rows()`（`:890`）只认那一版的
   行，`_refuse_a_different_document()`（`:907`）在冻结块与同来的那批行**互相矛盾时
   拒绝**而不是就地调停。上面引的 `:730` / `:790` / `:811` / `:822` 是**修复前**的行号：
   今天 `_shown_pairs()` 在 `:870`，而 `:730` 是 `deltas(store, cursor, at=at, …)`
   那一行。行号只在指定 revision 时才有意义——这一节自己就是又一处例子。
3. **ServerOS 原生投递那一跳从没在这台机上跑过。** `~/.config/systemd/user/` 里有
   `serveros-alert-sync.service.d/10-botmux-token.conf`，但**没有**
   `serveros-alert-sync.service`、也没有 `.timer`；`is-enabled` → `not-found`，
   `journalctl -u` → 无条目。所以那 701 条 `last_notified_at` 来自
   `alert_digest.record()`（`alert_digest.py:821`），**不是**来自
   `alerting.record_delivery()`（`alerting.py:947`）。unit 模板在树里
   （`runtime/serveros-alert-sync.service`/`.timer`），安装动词是
   `scripts/release.py:528 cmd_alert_worker`，它在没有 `--conversation` 时拒绝。

   **现行（2026-09-22 复测）：仍然成立，而且这一条是本机今天唯一还开着的那条。**
   `~/.config/systemd/user/serveros-alert-sync.service.d/10-botmux-token.conf` 在
   （881 B，9 月 17 日），而 `serveros-alert-sync.service` 与 `.timer` 都不在——
   **两个** `is-enabled` 都答 `not-found`。登记表也在长：**4364 条**告警、其中
   **740 条**带 `last_notified_at`（写下这一节时是 4117 / 701），而这 740 条仍然来自
   `alert_digest.record()`（今日 `alert_digest.py:1119`），**不是**来自
   `alerting.record_delivery()`（`alerting.py:947`，本机从没跑过）。安装动词今天的
   行号是 `scripts/release.py:885 cmd_alert_worker`；上面写的 `:528` 是旧行号。

   **§9.24（2026-09-25）在这一条上又加了两笔，一笔向下、一笔向上。**
   **向下**：`mcp/human_control.py` 的能力表里 `human.notification.transport` 一直写着
   `SUPPORTED`，理由是「POST /api/trigger carries a ServerOS sentence into the
   conversation」；实测不成立 —— 不写 `target.chatId`/`sessionId`/`rootMessageId` 三者，
   服务造的是一个**合成会话**，文本变成**模型的任务**，那一轮的头部自己写着「不显示在任何
   聊天里」并禁止发布到飞书，所以**它到不了任何人**。已降为 `UNKNOWN`，理由写在降级那一行
   本身。**向上**：一条真正有地址的发送路径现在有了（`runtime/message_transport.py`，
   走 `lark-cli im +messages-send`），并且一份真实日报经它送到了真实收件人（§9.24.3）。
   **但这一跳仍然不是本条说的那一跳**：`serveros-alert-sync` 的 unit/timer 依旧不存在，
   `cmd_alert_worker` 依旧需要一个获准的 `--conversation` —— 前者是报告投递，后者是告警
   投递，两条腿分开记，别把一条的成功读成另一条的。

第 3 条同时解释了 §8 里那个「孤立的 drop-in」：它不是残渣，是**一个设计好的跳没装**。

还有一处**已测但没接线**：`monitor_card.py:994-1009`（每小时那张主卡）只调
`resource summary` / `resource containers` / `runtime list` / `runtime status`，
从不调 `serverosctl card status`。这两处 ops 侧的行号是**别仓的**，在 ops 仓
`9d7a2ff` 上逐条复核过（`alert_digest_card.py:107` 的 `def serveros_card()`、
主卡那四条命令），所以它们成立的前提是**那个 revision**——ops 仓自己的活动工作树
正在改 `monitor_card.py`（见下），行号会漂。所以 `presentation.status_model()` 与
`cards.status_card()` 有实现、有测试、**没接**。主卡也不写 receipt
（`grep 'record|digest-record|receipt' monitor_card.py` → 0 命中）。

**ServerOS 这一侧已经是齐的**，缺的只在 ops 那一跳：链条是
collectors → 结构化事实模型 → `status_model`/`status_card` → 冻结的 presentation payload
→ delivery intent → transport → receipt，而 CLI 上对应的三个动词都在
（`cmd_card_status` `cli.py:4269`、`cmd_card_record_delivery:4201`、`cmd_card_deliveries:4242`）。
实测 `serverosctl card status --with-frozen --json` 在**新进程**里 exit 0，给出冻结块
`{"version": "status@9820fa98d597", "payload_hash": "9820fa98d597…", …}`，2967 字节。

顺带一提：同一条命令在 promote 之前给的是 `status@cfc8bc7165ab`、3185 字节。**版本号是内容
的函数，不是时钟的函数**——shim 指到新 revision 之后卡面内容确实变了，于是版本也变了。
这正是 §3/§4 要的语义；如果它两次一样，那份冻结就没有冻住任何东西。

**这一跳本轮做不了，不是因为难，是因为有两个活动工作树压着同一个文件**：

```text
worktrees/hwz-ops/SRV-0011        M scripts/tokyo-monitor/monitor_card.py     （未提交）
                                  ?? gateway-binding.yaml / gateway_binding.py / test_gateway_binding.py
worktrees/hwz-ops/alert-card-v2   ?? ALERT-CARD-V2.md / alert_card_v2.py / alert_semantics.py
                                     alert_subjects.py / health_face.py / validate_card_v2.py / fixtures/
```

`monitor_card.py` 正被别人改，而 `alert-card-v2` 是一整条独立的飞书 UX 工作流。去改同一
个文件等于覆盖另一个 actor 的 WIP——所以这一条记成**等待**，不记成没做：状态广播
（定时状态摘要）与告警摘要（NEW/ESCALATED/REOPENED/RECOVERED）是两种节奏，共享事实与
呈现规则但**不共享发送触发语义**，而两种节奏的卡面现在都已经由 ServerOS 出（告警那份
已经在链上，见上）。

### 3.7 G1 的注册表：一个 artifact 一条 plugin 记录，成员按 composition 精确取

先前 `manifest_members()` 会把**全部** plugin 记录的成员并起来，于是「composition 声明
了什么」与「注册表里这条 plugin 有哪些成员」是两件事，读的人无法从后者检查前者。现在
是一对一的：

```text
plugin: hwz-server-os              provides  3  serveros, ability-management, runtime-management
plugin: hwz-server-os-linux-work   provides 10  ability-management, runtime-management, test-management,
                                                workspace-maintainer, feishu-space-management,
                                                information-management, work-management, feishu-report,
                                                inventory-publishing, document-stewardship
plugin: serveros-testing           provides  2  automated-testing, test-management
```

`serveros-testing` 因此是一条**独立的 registry 记录**，不是从别的记录里并出来的。检查的
路径也随之变成一条链：**composition → 声明的 plugin 身份 → 那条 plugin 记录 → 该
artifact 的精确成员集合**。旧 consumer 的兼容迁移保留着。

### 3.8 五个 pack 与它们的成员（§7 的落点，实测）

| pack | 成员数 | 这一轮动了什么 |
| --- | --- | --- |
| `pack:quality-delivery` | 4 | `automated-testing` 加为 `required` |
| `pack:autonomous-quality-loop` | 6 | 同上 |
| `pack:litellm-development` | 7 | 同上，且 `build-release` 加为 `required` |
| `pack:cross-device-collaboration` | 4 | — |
| `pack:agent-orchestration` | 6 | — |

`test-management` 与 `automated-testing` 在三个包里都是 `required`，这个分工是有意的：
前者是**环境**（一个可复现的测试环境是什么、复用前怎么 smoke），后者是**判断**（场景
选择、真实/合成证据、verdict、真 FAIL 后的停止规则）。设计 §10 把环境定义**移出**测试
而不是移进它，所以一个节点可以定义环境而从不跑需求 E2E——两者都在的话，测试插件才有
边界可测。

**A9/A10/B5 不进任何 pack**，这是决定不是遗漏：`production-observation`、
`production-change`、`research-evidence` 三条都**没有**为了「每个职责都有包」而造一个空
composition。没有 pack 意味着没有产品承诺面在替它们说话，而造一个空包会把「还没接线」
读成「已经承诺」。

packs 的 `authority.revision` 现在是
`e7d20f3b50e1a86c9998b5dff3f519fa52b4aa1d`（五包同一值），它是读到的 revision 的
**祖先**，且 `abilities-manifest.yaml` 在两者上逐字节相同——正是 pack pin 的祖先规则
要求的两条。

### 3.9 日循环的渲染腿：装了、手跑过一次、**没有上弦**

`runtime/serveros-daily.service` / `.timer` 这对模板一直在树里，而
`~/.config/systemd/user/` 里**从来没有它们**——所以「日循环」此前是一个有实现没安装的
routine。这一轮把它装上了，并且**按设计的极性保持未启用**：

```text
scripts/release.py daily-task --runtime-root <dist> --composition serveros-linux-work \
                              --out /data/home/vince/artifacts/serveros/workbench
  installed      ~/.config/systemd/user/serveros-daily.service
  installed      ~/.config/systemd/user/serveros-daily.timer
  revision       cards-and-release-20260922@2f77b02
  destination    /data/home/vince/artifacts/serveros/workbench
  installed and NOT enabled
```

`--out` 是必填且没有缺省值，这条不是形式：缺省会落在 revision 自己的 `workbench/`，
而那是下一次更新会整份替换的目录，渲染器从安装态往那里写是**按名字被拒**的。
目的地先用 `path_admit` 过了一遍（`ADMITTED_NON_CANONICAL`，属 artifacts 根）。

然后**手工跑了一次**，这正是模板设计的验证路径（不需要 timer）：

```text
systemctl --user start serveros-daily.service
  Result=success   ExecMainStatus=0   消耗 2min 6s CPU
  wrote 15 份文档到 /data/home/vince/artifacts/serveros/workbench
```

输出是一份真实的 START-HERE 集合：`START-HERE.md` 的头部记着渲染者
（`runtime/workbench.py`，来自新 revision）、三组输入、以及**23 条命令逐条的退出码**，
其中 `ability resolve` 两条是 2——那不是失败，是「没给 ability catalogue，所以答案是
UNKNOWN，且 `inputs_missing` 逐条点名缺哪八个输入」。**把「问不出来」记成 2 而不是记成
0**，正是这条链要的诚实性。

未启用的那一行留给要人做的那一步：

```text
systemctl --user enable --now serveros-daily.timer
```

**为什么不由本轮上弦**：单元自己的头部写着「enabling is the act §8 puts on the approved
list」。装上（可手工验证、不自动运行）与上弦（每天 08:17 自动产出）是两个动作，这一轮
只做前者。

## 4. 四层状态的定义

| 层 | 判据 | 不算这一层的 |
| --- | --- | --- |
| DESIGN | 设计文档里写清楚，且已冻结 | 只在自己脑子里 |
| CODE | 盘上有 artifact，且**测试读过它** | 文件存在但没有任何测试/消费者碰过它 |
| INTEGRATION | 有个真实 consumer 真去调它，且那一次调用被测到 | 单测里 mock 出来的 consumer |
| LIVE | 在生产或日常链路上真在跑 | 代码在盘上 |

「代码在盘上写 LIVE」是本轮明确禁止的错误。所以 A8/A9/A10/B5/B9 五条 CODE=DONE、
INTEGRATION=NOT_MEASURED、LIVE=NOT_ENABLED——它们**没有**被说成已完成，只是从
DESIGN-only 变成了 CODE。`NOT_MEASURED` 与 `FAILED` 是两件事，不能合并。

## 5. 已存在的红（记录，不删除）

计划 P0：「测试现有受影响基线并记录，不能把旧问题带来的失败静默删掉。」

### 5.0 本轮实测基线（含一条被修正的过度声明）

**先纠一条本轮自己写错过的**：早先记成「`4207 passed, 0 failed`」的那次绿灯，实际命令里带着
`SERVEROS_COMPOSITION_EVALUATOR=<…>/fabric-v1/skills/ability-management/payload/composition.py`。
也就是说那 8 条 composition/registry 配对测试**是在把规则钉到含 pairing 的那个 revision 上
之后**才绿的。不带这个变量跑，同样这棵树会红 8 条。一条省略了环境变量的绿灯，读起来和
一条无条件的绿灯一模一样——所以这一格现在把变量写出来。

**再纠一次，同一类的第二次。** 本文件先前把 `1c36789` 记成
「4260 passed, 0 failed」。本轮再跑同一棵树，红 **1** 条：

```text
tests/test_declared_commands.py::DeclaredCommandsExist::test_every_declared_command_is_real_or_a_recorded_gap
`serverosctl` `cleanup` — cited in docs/CC-EXECUTION-LEDGER.md (after `|`)
```

（这一行**不是** pytest 的逐字输出：原文里那两个记号之间是一个空格。改记法的原因见下面的
「第三次」，不是排版偏好。）

`git show HEAD:docs/CC-EXECUTION-LEDGER.md | grep -n "relevance|cleanup"` → `667`，也就是说
**这条红在 `1c36789` 那棵树里就在**。所以那份「4260 passed」不是在它被记到的 revision 上
测出来的——套件跑完之后、提交之前，台账又被改过一次。这和上一段那条「省略了环境变量的
绿灯」是同一族：**一条不覆盖它所标注的那棵树的绿灯，读起来和无条件的绿灯一模一样。**
差别只在于上次漏的是环境变量，这次漏的是一次后置编辑。

修的是文档不是闸门：`[--kind relevance|cleanup]` → `[--kind relevance / cleanup]`。
闸门「`serverosctl` 后带 `|` 的分段当成另一条命令」是有意的
（`context/disclosure-contract.yaml`、`context/disclosure.py` 靠它工作），而这个 fence 让它
读到了一条没人写的命令。顺带记下这条闸门的一个已知弱点：同一族里
`'record|digest-record|receipt'`（§3，`monitor_card.py` 那条 grep）也是从 fence 里读进来的，
它**通过只因为 `digest-record` 恰好是个真动词**——一条靠巧合变绿的行，和一条红一样值得记。

**第三次，同一个字面量，另一条路径。** 上面那条改完后再跑全量，同一条测试**还是红**，
这次报的是不带 `(after `|`)` 后缀的同一句话——它不再是 fence 里 `|` 分段的误读，
而是**本节上面那个代码块本身**：`_REF` 在 `.md` 上只要 `serverosctl` 后面跟一个词就记一条，
不问它在不在反引号里、在不在 fence 里、是不是正被引用。于是「把修复过程写进台账」这件事
自己又触发了一次同一条闸门——只有把引用写成两个记号才不再触发，这就是上面那行注解的来由。

这一族到此的准确形状是：**闸门能看见字面量，看不见语境**。两个面都记在这里——
`'record|digest-record|receipt'` 之所以绿，只因为 `digest-record` 恰是个真动词（巧合）；
这条之所以红，只因为这句话在**谈论**一条命令而不是在**指示**人去跑它（引用）。
两个方向都不是判据在起作用。不改闸门，理由和上面那条一样：它是这类缺陷唯一的自动发现机制，
为引用开豁免会让真声明也走进豁免，而豁免名单一旦为散文增长，真的条目就不再被读。
**留给下一个读到这里的人的判据**：本节若再出同类红，先问这行是在指示读者去跑，还是在
谈论一条命令；只有前者该改文档。

| 套件 | 结果 | 命令 |
| --- | --- | --- |
| serveros（接线终态，带 evaluator 覆盖，含 `information captured`） | **1 failed**, 4298 passed, 23 skipped, 3714 subtests passed（468.52s） | `SERVEROS_COMPOSITION_EVALUATOR=<worktree>/…/composition.py SERVEROS_ABILITY_REF=serveros/fabric-integration-20260922 pytest tests/ -q -p no:cacheprovider` |
| serveros（同上 + 本节这次改台账） | 未整跑；**这一格只覆盖上一条**，见下方说明 | 只跑 `tests/test_declared_commands.py` → 12 passed |
| serveros（`1c36789`，上一份记成「0 failed」的那棵） | **1 failed**, 4298 passed, 23 skipped, 3714 subtests passed（469.78s） | 同上。修文档后同树变 12 passed（`tests/test_declared_commands.py`） |
| serveros（终态，带 evaluator 覆盖） | **4239 passed, 23 skipped, 3704 subtests passed, 0 failed**（480.07s，`fb97f77`） | 同上 |
| serveros（同树不带覆盖） | **8 failed, 4231 passed, 23 skipped, 3704 subtests passed**（467.10s） | 同上去掉那两个变量 |
| serveros（本轮末，不带覆盖，含 §9.10 的 `information watched`） | **8 failed, 4480 passed, 20 skipped, 3717 subtests passed**（492.73s） | `pytest tests/ -q -p no:cacheprovider`。**8 条全部是 `tests/test_architecture_check.py` 的配对断言**（本节 §5.0 那条已定案的跨仓红），无新增失败；pass 数从 4231 涨到 4480 是本节 §9.6/§9.7/§9.9/§9.10 四轮新增的测试 |
| serveros（§9.11 standing delegation 落盘**之前**，第一次量） | **9 failed, 4520 passed, 20 skipped, 3718 subtests passed**（490.34s） | 同上。**8 条跨仓红 + 1 条新红**：`tests/test_verification_tool.py::TheStaticGraphIsALowerBound::test_the_static_input_set_is_the_test_file_alone_for_half_the_suite`——新测试文件进套件把分母从 111 推到 112，分子 55 没动。**这一格留在这里，因为它是这一轮唯一的真红**，处置见 §9.12 |
| serveros（§9.11 + §9.12 修正后**终态**） | **8 failed, 4521 passed, 20 skipped, 3718 subtests passed**（485.44s） | `pytest tests/ -q -p no:cacheprovider`。**回到既有的 8 条跨仓红**，无新增失败；pass 从 4480 涨到 4521 是本轮新增的 40 条 `tests/test_delegation.py` + `test_verification_tool.py` 里新增的一条判据 |
| serveros（P5 尾款 + P6 切片 A 的代码，台账那条引用**还没改**时） | **1 failed**, 5607 passed, 20 skipped, 3928 subtests passed（818.82s） | `pytest tests/ -q`。**那 1 条就是上面那一族的第四次**：本节 §9.24.5 写「那个扫描器读得太字面」时，把两个假命令原样引了出来 |
| serveros（同上，把该处引用改成**谈论**的写法之后） | **5632 passed, 20 skipped, 3928 subtests passed, 0 failed**（802.68s） | 同上。**这两行是同一次改动的前后两棵树**，中间只动了那一处引用 |
| serveros（P6 切片 B：完成边界 + 渲染器 + 三个套件的新测试） | **5654 passed, 20 skipped, 3934 subtests passed, 0 failed**（649.58s） | `pytest tests/ -q`。相对上一行的 **+22 passed / +6 subtests** 就是这一片新增的测试；**0 failed** 是这一片唯一一处旧行为被有意改掉（无 `completion` 键的行不再被当成完成）之后的结果 |
| serveros（`dc23707`，带覆盖） | 4207 passed, 23 skipped, 3703 subtests passed, 0 failed（459.73s） | 同上 |
| serveros（起点） | 3 failed, 4171 passed | — |
| ability-management | **105 passed, 2 skipped**（`074a518`）；三个 renderer `--check` 干净 | 加 `SERVEROS_BUNDLE=<serveros worktree>/plugin` |
| serveros（P6 切片 C：`worker_stopped` 接上它的 receipt 生产者，§9.27） | **1 failed**, 5669 passed, 20 skipped, 3936 subtests passed（660.19s） | `pytest tests/ -q`。相对上一行的 **+15 passed**（5654 → 5669）与 **+2 subtests**（3934 → 3936）就是这一片新增的测试。**那 1 条不是这一片引入的**，身份与判据见下一段 |
| serveros（P6 切片 D：`resources_released` 接上它的 lease 生产者 + 一对字段必须先被读全，§9.28） | **5683 passed, 20 skipped, 3945 subtests passed, 0 failed**（646.90s） | `pytest tests/ -q`。算术是闭合的：上一格 5669 ＋ 这一片新增的 **13 条**（`test_focus_source.py` 的 `ResourceOccupancyIsReadFromTheLeaseStoreThatOwnsIt` 10 条 ＋ `test_daily_cycle.py` 3 条）＝ 5682，**再加回上一格那条红的 1 条** ＝ 5683；subtests 3936 ＋ **9** ＝ 3945。上一格那 1 条**这一次没有出现**——同一个文件单跑 `27 passed`、那一条参数化单跑 `4 passed`（8.10s）。**这不表示这一片修好了它**：这一片的六文件与它无涉，只表示那条红是**间歇的**，与 §9.27.6 记的探针读法一致（`container.attribution` 是四条参数化里唯一会 shell out 到 Docker 的那条）。所以它从「见过一次」变成「见过一次、缺席一次」，**不结案** |
| serveros（P7 切片 A：准入与观察拆成两个问题、四个记录目录陷阱、§9.29） | **5697 passed, 20 skipped, 3947 subtests passed, 0 failed** | `pytest tests/ -q`。算术闭合：上一格 5683 ＋ 本片新增的 **14 条**（`TheAttemptWatchTest` 12 条 ＋ `TheRecipeCommandLineTest` 2 条）＝ 5697；subtests 3945 ＋ **2** ＝ 3947。**这一格必须先记一次红**：修掉本片自己引入的 monkeypatch 泄漏之前的那一跑是 **17 failed, 5680 passed, 3947 subtests passed（812.08s）**，17 条全落在五个与本片无涉的文件里，根因是本片新测试的 `addCleanup` 把**打过补丁的** `paths_mod.Paths` 又装了回去，而那是全树共享的模块对象；因为「单文件绿、15 条变异全杀、全量却红」是这一片最有价值的那条教训，它留在这里不删。**本格不记墙钟**：这一行的文字在跑之前就已落盘，所以那一跑读到的正是提交的那份字节——这比一个秒数强，所以拿它换掉了秒数。 |
| serveros（P7 切片 B：一个缺席的两种拼法，§9.30） | **5700 passed, 20 skipped, 3947 subtests passed, 0 failed**（636.60s） | `pytest tests/ -q`。算术闭合：上一格 5697 ＋ 本片新增的 **3 条**（`ReportTest` 三条）＝ 5700；subtests 3947 ＋ **0** ＝ 3947（本片三条都没有 subtests）。这一跑没有被改动的文件在中途换过字节：起跑前记下 `runtime/recipes.py` 的 sha256 `d0c40f45a1db42b1…`，跑完核对一致，那一跑读的就是提交的这一份。 |
| serveros（P7 切片 C：`DISPATCHED` 的生产者 / `invoke_once` 的第一个生产调用者，§9.31） | **5709 passed, 20 skipped, 3947 subtests passed, 0 failed**（604.13s） | `pytest tests/ -q -p no:cacheprovider`。算术闭合：上一格 5700 ＋ 本片新增的 **9 条**（`TestTheDispatchVerb` 九条）＝ 5709；subtests 3947 ＋ **0** ＝ 3947（本片九条都没有 subtests）。这一跑读到的就是提交的那份字节：起跑前记下 `runtime/cli.py` 的 sha256 `135c11a00e159a40…`，跑完与提交前复核一致（本片另外两处改动是 `runtime/actuator-contract.yaml` 与 `tests/test_admission.py`，都不在跑动中被改）。**本片有一个必须先记的前置**：先跑的是 `tests/test_admission.py` 单文件（`115 passed in 18.77s`）作为变异检查的基线，然后 8 条变异全部被杀、每条都由它该杀的那一条测试杀死（§9.31.6），最后才是这一跑全量 |
| serveros（P7 切片 D：回执锁改成「拒绝」而不是「等」，J07 判据 5，§9.32） | **5714 passed, 20 skipped, 3947 subtests passed, 0 failed**（647.81s） | `pytest tests/ -q -p no:cacheprovider`。算术闭合：上一格 5709 ＋ 本片新增的 **5 条**（`tests/test_bounded_invocation_paths.py` 四条 ＋ `tests/test_admission.py` 一条）＝ 5714；subtests 3947 ＋ **0** ＝ 3947（本片五条都没有 subtests）。这一跑读到的就是提交的那份字节：跑前记下 **7 个**被测文件的 sha256（`runtime/agent_control_store.py` `b54b22c4470006e0…`、`runtime/bounded_invocation.py` `c324f0dbd3c6dfce…`、`runtime/cli.py` `4ae3dc99c4a5bacae…`、`runtime/recipes.py`、`runtime/actuator-contract.yaml` 与两个测试文件），跑完 `sha256sum -c` 全部 `OK`。**本片同样有一个必须先记的前置**：先跑两个文件的合并基线（`131 passed`）作变异检查的底，然后 6 条变异 **5 杀 1 存**（§9.32.5，存活的那条 M5 与其理由记在那里），最后才是这一跑全量。本片改了错误/异常语义（新增 `LockUnavailable`），**安装产物未变**——三处改动全在 `runtime/`、`tests/` 与一份契约的散文里，所以没有 build→promote→restart |
| serveros（P7 切片 E：退休后复用的键不再把上一轮的回执递给下一轮，§9.33） | **5718 passed, 20 skipped, 3947 subtests passed, 0 failed**（658.60s） | `pytest tests/ -q -p no:cacheprovider`。算术闭合：上一格 5714 ＋ 本片新增的 **4 条**（`TestTheDispatchVerb` 四条）＝ 5718；subtests 3947 ＋ **0** ＝ 3947（本片四条都没有 subtests）。这一跑读到的就是提交的那份字节：跑前记下 `runtime/cli.py` `5da0ffbeefadf3a3…` 与 `tests/test_admission.py` `d374fde739b10327…`，跑完两个 sha256 逐字一致；跑动中只写过 `docs/CC-EXECUTION-LEDGER.md`，而全树**没有测试读那份文件**。**本片有一个必须先记的前置**：先跑 `tests/test_admission.py` 单文件（`120 passed in 26.10s`）作变异检查的底，4 条变异**全部被杀**（§9.33.6），其中 E4 是**补了一条构造边界的测试之后**才被杀的（§9.33.5）。**本片改了拒绝语义（多出两种拒绝语的来源），安装产物未变**——三处改动全在 `runtime/`、`tests/` 与台账里，所以没有 build→promote→restart。另记一条保真度缩水：这一跑里 `tests/test_daemon_equivalence.py` 有一处断言因宿主在比较过程中变动而**未按精确相等判**（测试自己的警告如此写），环境造的、非本片，记在这里而不是省略 |
| serveros（P7 切片 F：等待中的 Parent 留下一份可续的 checkpoint，「没有 receiver」是一句关于 fabric 的真话，§9.34） | **5782 passed, 20 skipped, 3948 subtests passed, 0 failed**（669.25s），第二遍同样三项（640.97s） | `pytest tests/ -q -p no:cacheprovider`。算术**两项都闭合**：上一格 5718 ＋ 本片新增的 **64 条**（`tests/test_continuation.py`，全部新增文件）＝ 5782；收集数 5802 ＝ 5782 ＋ 20 skipped；subtests 3947 ＋ **1** ＝ 3948，而那 **1** 是本片新增的**一个被产出的模块**被 `test_every_shipped_module_imports_from_the_install` 按产物里的 `.py` 逐个计数数到的（§9.34.8 有实测：那个循环从 111 变 112，产物里确实有 `runtime/continuation.py`）。**跑了两遍**，因为 subtest 那一格要先判它是复现还是浮动；两次 passed/skipped/subtests 三项逐字相同。这一跑读到的就是提交的那份字节：跑前记下 `runtime/continuation.py` `85079def1361b150…`、`runtime/agent_control_store.py` `71cf23e8a775c302…`、`runtime/cli.py` `3dc1df2134c1359b…`、`tests/test_continuation.py` `71088c504be23477…`，跑完 `sha256sum -c` 四项全 `OK`；跑动中只写过 `docs/CC-EXECUTION-LEDGER.md`，而全树**没有测试读那份文件**。**本片有一个必须先记的前置**：先跑 `tests/test_continuation.py` 单文件（`64 passed in 4.91s`）作变异检查的底，16 条变异**全部被杀**、无存活（§9.34.7），另有两条**等价变异**如实列出未计入。**本片改了 store 的写入语义（多出同事务取代与三处新拒绝），而且安装产物内容变了**——`plugin/bundle.yaml` 按**目录**声明 `runtime: path: runtime`，本片在那个目录下新增了一个模块。后果写清楚：`work checkpoint` 与 `work continuations` 在**装好的节点上要等下一次 build→promote→restart 才可用**，那一次是 P12 的；本片没有动 `plugin/`、`mcp/` 或任何 composition，所以本片自己不触发它。这一行的「产物未变」最初写错了，落笔后自查改成现在这句（§9.34.8） |

**切片 C 那一格里的那 1 条红：`container.attribution` 的 daemon/CLI 计数比对。**
逐字是：

```text
FAILED tests/test_daemon_equivalence.py::test_the_daemon_and_the_cli_count_the_same_things[container.attribution-cli_args2-keys2]
E       KeyError: 'result'
tests/test_daemon_equivalence.py:301: KeyError
```

单跑该条：`1 passed in 1.72s`。这一条的形态与「计数不一致」完全不同，值得把它拆开写下来，
因为它第一次暴露了那个测试里的一个缺口。

**它读到的不是计数，是一个错误信封。** `_ipc(method)["result"]`（`tests/test_daemon_equivalence.py:301`）
在 daemon 回 `{"ok": True, "method", "result"}` 时取到结果，而在 `runtime/daemon.py:1113` 的
`except Exception as exc` 分支下，回的是 `_error(method, type(exc).__name__, str(exc))`
——即 `{"ok": False, "method", "error": {"code", "message"}}`，**里面没有 `result` 键**。
本机 live 实测同一个 daemon（`path.resolve` 一个不存在的 ref）：

```text
error envelope keys: ['error', 'method', 'ok']
ok: False | method: path.resolve
error.code: PathRefused
has 'result'? -> False
indexing ['result'] raises: KeyError('result')   <- 与那条红同形
```

**为什么只能是四条参数化里的这一条。** 四条里只有 `container.attribution` 会 shell out 到 Docker
（`view.container_attribution` → `containers.inspect_all` → `probes.LiveHost.docker_ps` →
`probes.LiveHost._run`）。`_run` 在非零退出、`OSError`、或 24 秒超时三种情况下抛
`ProbeUnavailable`；`inspect_all` 把它包成 `DockerUnavailable`（`runtime/containers.py:1559-1560`），
而这个类**不是** `RegistryError` 的子类，所以 `handle` 会把 `"DockerUnavailable"` 原样当作 code 报回。
隔离复现（把 PATH 指向一个 `exit 1` 的假 `docker`，不动真 daemon、不动台上任何容器）：

```text
probes raises:      ProbeUnavailable | docker ps -a exited 1: Cannot connect to the Docker daemon …
inspect_all raises: DockerUnavailable | docker ps -a exited 1: Cannot connect to the Docker daemon …
code the daemon would report: DockerUnavailable
```

同一链路里另一条能抛的是 `attribution_index` 的 `TypeError`（传错了 `Registry` 与 index），
但 daemon 传的是自己的 `self.registry`（`runtime/daemon.py:2679`），正常路径实测 0 次抛出；
live 再测一次 `container.attribution` → `ok: True`、`containers counted: 17`、`registry_present: True`。
所以**能产生那条红的只有 docker 那一处**。

**这一片与它无关，是核过的，不是推的。** `git status --porcelain` 里这一片的六个文件是
`docs/CC-EXECUTION-LEDGER.md`、`intake/daily_cycle.py`、`intake/focus_source.py`、`runtime/cli.py`、
`tests/test_daily_cycle.py`、`tests/test_focus_source.py`（`grep -E "daemon|containers|probes|view|attribution"` → 无命中）。
`runtime/daemon.py`、`runtime/containers.py`、`runtime/probes.py`、`runtime/view.py`、`runtime/attribution.py`
**一个都没动**。

**没能取到的那一件，如实写在这里：** 那个 `error.code` 的**具体值**没有被任何东西记录下来——
daemon 不写 handler 的异常日志（`journalctl --user -u serverosd.service` 在套件窗口 22:21–22:34 内
只有 13 条 `tick NNN: 4 observed, 0 drift, 0 decisions`），`docker` 的系统日志本用户读不到
（「No entries」+ 权限提示），而那个测试本身在 `except` 之外把信封丢掉了。所以
「那一次到底是什么让 docker 失败」**本机没有证据**：可能是非零退出（Docker 在一个 11 分钟、
同机还有别的 agent 的套件里瞬时拒绝），本机现在 `docker ps -a` 实测 0.095s，24 秒超时不像。

**顺手记下那个缺口（不改断言、不改测试）：** `_sample_the_pair` 里的三次 `_ipc(...)` 调用
在 `for attempt in range(1, ATTEMPTS + 1)` 循环体内**但不被 try 包住**，所以它只在
「`_count_agreement` 判出计数不一致」时重试；daemon 回错误信封时异常直接穿出循环，
一次都不重试。这个形状是对的（一个答不上来的 daemon 不该被重试成一个读数），
但**它今天读起来和「计数不一致」一样红**——两者都只是 `KeyError: 'result'`。
要不要区分是一处独立的改动，不在这一片里；记下来是因为下一个人会先看到那条红、
再看到「单跑就绿」，然后花掉我花掉的这段时间。

**这一次（切片 D 的全量）它没有出现，而这不结案。** 见 §5.0 表格切片 D 那一格：全场
`0 failed`，同一文件单跑 `27 passed`、那一条参数化单跑 `4 passed`。切片 D 的六个文件与
`tests/test_daemon_equivalence.py` 无涉，所以这一次缺席只能读成**间歇性**的一次消失，
与本节上面「可能是 Docker 在套件里瞬时拒绝」的读法一致，而不是「被修好了」。这一条的
状态因此是：**见过一次、缺席一次、原因仍无本机证据**。

**这一格的可归属性：现场记一次，事后又量了一次。** `intake/focus_source.py` 的 mtime 是
`22:25:02`，落在套件窗口（`22:21:59` 起，`660.19s`）**之内**——套件跑到约四分之一时，我改了
这个文件里的一行 `#:` 注释。当时的证明是把当前文件与「把那条注释改回原文」的副本做 `ast.dump`
比对，相等。事后不必靠那句当时的断言，因为同一件事现在能**再量一次**：

```text
AST(now) == AST(now without # lines): True
```

即这棵树的 AST 与注释字节无关，而那次改动就是一条这样的注释；再把受影响的两个文件在
**最终字节上**重跑：`117 passed, 17 subtests passed in 4.28s`。所以这一格的读数归属这棵树，
差别只在注释字节——这不是「只改了一个文件所以不用重跑」那种推理，那种推理正是本节上面
拆过三次的那句谎。

**接线终态那一格为什么不是整跑的。** 全量是在「接线完成、台账还没记这次修改」那棵树上跑的，
红 1 条，就是本节那条 `cleanup`。之后只动了一个文件：`docs/CC-EXECUTION-LEDGER.md`。
「只动了一个文件所以不用重跑」这句话，正是本节上下两段在拆的那句谎——所以不靠断言，靠读出来的
读者集合：`grep -rln "CC-EXECUTION-LEDGER" tests/ runtime/ skills/ docs/` 只回它自己；
再查有没有别的测试扫 `docs/*.md`，只有 `tests/test_memory_ledger.py` 读 `docs/memory-fabric.md`
一个具名文件。**即：`docs/CC-EXECUTION-LEDGER.md` 的自动读者只有 `tests/test_declared_commands.py`
一个**（它经 `docs/*.md` glob 读到），而那个文件在终态上 12 passed。这就是那一格的全部依据，
不是「应该没影响」。

4239 − 4231 = 8，**正好是那 8 条配对测试**——两个数之间的差就是那一族红的全部，没有第二处。

4298 − 4239 = **59**，这个数我一开始按「新增文件的行数」估成 21 + 38，**估错了**。实测是
逐文件 collected 数在两棵树上的差（`--collect-only` 按文件计数后 diff，两份计数留在
`artifacts/skills/serveros/RUN-20260922-raw-coverage/collect-{fb97f77,HEAD}.tsv`）：

| | `fb97f77` | 终态 | 差 |
| --- | --- | --- | --- |
| 全量 collected | 4262 | 4322 | **60** |
| passed | 4239 | 4298 | **59** |
| 逐文件差 | — | 只多两条：`test_capture_coverage.py` 39、`test_space_cli.py` 21 | **60**，无第三处移动 |

60 与 59 差的 1 就是全量那次红的那条：它是**既有**用例（`test_declared_commands.py`），
不是新文件带来的。subtests 3704 → 3714 = **10**（space 9 + capture 1）。加一个动词族加一个
读取器，没有让别的行移动。**这一格的价值不在 60，在于「无第三处」是 diff 出来的**：
拿「新增两个文件各有多少条」相加，是在两条不相干的数之间做减法，形状和真判据一样。

serveros 那套的代价要写下来：单次 **7 分 47 秒**，且主机 load 一度到 53 时被拖到
近乎两倍。所以「跑一套」在这台共享机上不是随手动作，改一个字面量就该只想跑受影响的那两个文件。

起点那 3 failed 是本轮改掉的第一件事（§3/§4 的 send-time race 与 freeze），不是外部红。

那 8 条红的身份要说清楚，因为它们的形状**看似**是本轮代码的问题：全部在
`tests/test_architecture_check.py`，且都是「本应报出了 drift，却一条都没报」。
实测三条判据定案：

1. 单独跑这个文件也红 8 条 → 不是全量跑的顺序污染。
2. `git diff dc23707 fb97f77` 对这个文件与 `skills/serveros/scripts/architecture_check.py`
   **无改动** → 不是本轮改坏的。
3. 把 `SERVEROS_COMPOSITION_EVALUATOR` 指向 `worktrees/ability-management/…` 的
   `composition.py` → **24 passed** → 是这个输入决定的。

原因是 fixture 的搜索顺序（它自己的 docstring 就写着）：**canonical checkout 优先**，
于是它读 `src/ability-management/skills/ability-management/payload/composition.py`——
那一份是 9-18 的，**不含 §8 的配对规则**（`grep -c pairing` → 0，worktree 那份 → 1）。
规则读不到，检查就报 `COMPOSITION_PAIRING_UNAVAILABLE`，而「报出了不可用」在断言里
与「没报出 drift」长得一样。

| 红 | 位置 | 性质 | 处置 |
| --- | --- | --- | --- |
| **8 条 pairing 断言** | serveros `tests/test_architecture_check.py` | **跨仓库、同一族的反向**：它读 `src/ability-management` 的 `composition.py`（9-18），那份不含 §8 的配对规则；带 `SERVEROS_COMPOSITION_EVALUATOR` 指向 worktree 那份就 24 passed | **未修，已定案**。解除条件是让 `src/ability-management` 前进到含 `e7d20f3`（配对规则进 evaluator 的那次）的 revision——**不是**改测试，也**不是**把 fixture 的搜索顺序倒过来（那会让 canonical checkout 永远不再被读）。它是 §5.0 里那条跨仓库红的同一族 |
| `test_real_registry_matches_real_bundle` | ability-management `tests/test_materialization_contract.py` | **跨仓库**：它读 `src/serveros`，而 `plugin/profiles/testing.yaml` 只在 worktree 里，于是注册表「多」了 `automated-testing` | 已确诊：`SERVEROS_BUNDLE=<worktree>/plugin` 下全套通过。修法是让 `src/serveros` 前进到含该 profile 的 revision，**不是**改测试 |
| `test_the_committed_view_is_up_to_date` | ability-management `tests/test_capability_families.py` | repin 的派生后果：视图里嵌 `authority_revision` | 已修，并入 repin commit `c993c82`（只动 pin 不动 pin 的渲染，那一格 bisect 出来就是红的） |
| 9 个 profile 构建失败 | serveros `tests/test_build.py`、`tests/test_plugin_composition.py` | **本轮自己引入又修掉的**：`ABILITY_SKILLS` 是 fixture 里手维护的「注册表替身」，它含全部 `available` 名字却独缺 `document-stewardship` | 已修，commit `6986b6f`。它当时**正确地**发火了——fixture 一直默默模拟旧事实而从不声明，所以 profile 一改就炸在这里而不是炸在别处 |
| 同一个名字当了两种样例 | 同上 | `document-stewardship` 同时是「catalogue 有的」断言对象与「catalogue 没有的」样例；前者一改，后者就假 | 已修：后者改用哨兵 `no-such-ability`（`test_plugin_composition.py` 里早就用的同一个哨兵） |
| **构建产出会写一个可被清理的目录名** | `scripts/build.py::codex_marketplace()` | 实测：`runtime-assets/hwz-server-os/.agents/plugins/marketplace.json` 写的是 `./.staging-c233946`，而 `current` 正是从那个目录构建出来的。当时没有东西坏（没有读者读那个文件），但**一个指着一次性目录的描述符离「什么都没有」只差一次清理**，而清理正是 §5 的 staging 规则要求操作者考虑的事 | 已修，commit `2f77b02`：判据落在**构建期**（任何以 `.` 开头的路径分量都算「没人承诺保留」），于是描述符改写 `./<composition>/current`。修一个字符串治不了第二次 |

第二条值得单独记一笔：它是**同一个族的第四次**——源动了，派生物没跟着动。前三次是
manifest 行、packs 闭包、cards。凡是「X 的渲染嵌了 X 的值」，都必须和 X 同一次改。

### 5.0.1 那 8 条红的解除条件收窄到一次 fast-forward，而它不在本轮授权内（2026-09-22 量）

台账上面那句「让 `src/ability-management` 前进到含 `e7d20f3` 的 revision」是对的，但当时
**没有量到它到底要求哪一步**。这一节把两个看起来显然的修法排除掉，剩下的那一个是一条
外部权限项。

**它不是 fixture 独有的一条红，产品检查本身也是红的。** `architecture_check.py:1479` 的
`--ability-ref` 默认是 `main`（`SERVEROS_ABILITY_REF` 未设时），而 `:750` 的
`observe_object(ability_repo, args.ability_ref, …)` 读的是**这个 ref 的 git object**。
所以生产的 `architecture_check` 读的正是 ability-management 的 `main`，今天会报
`COMPOSITION_PAIRING_UNAVAILABLE`（HIGH）。fixture 的「canonical checkout 优先」不是它
自己的怪癖，是在复现产品的读法——**两者要一起绿，只有一条路**：

| 看起来能修的 | 实测 | 结论 |
| --- | --- | --- |
| 把 `src/ability-management` 快进到 `origin/main` | `origin/main` = `7ea44af`；`git -C src/ability-management branch -r --contains e7d20f3` → **只有** `origin/serveros/fabric-v1-20260922` | **不够**。`e7d20f3` 根本不在 `origin/main` 上，快进 33 个提交也读不到配对规则 |
| 在 canonical 检出里切到带规则的分支 | 工作树会带上规则（fixture 变绿），但 `--ability-ref=main` 读的是 **ref**，不是工作树 | **更坏**：只有量具绿、产品照红。这正是本仓记过五次的那一族 |
| 把 `074a518` 落到 ability-management `main` | `git merge-base --is-ancestor origin/main HEAD` → **YES**，`0 10` | **唯一的路**，且是 fast-forward：无 merge commit、不改写任何历史 |

那 10 个提交的内容也审过了，因为 §12 点名不许夹带：`git diff --name-only origin/main..HEAD`
的 35 个路径**全部**落在 ability-management 自己的 `skills/`、`plugins/`、`abilities-manifest.yaml`、
`ability-packs.yaml`、capability-families 派生视图与 `tests/` 里——没有 Feishu UX、没有
MarketOps、没有两处 LiteLLM、没有 `records/` 脏资产。它正是内存里那句
「其余剩的都是有独有内容的 blocker」里的一条。

**而这一步本轮不做**：§1 明写「不覆盖 main」，`ops/CLAUDE.md` 是「未明确授权时只检查、
不移动/删除/修改/commit/push」，所以对**另一个仓的 main** 做远端写是一条外部权限项，
不是本轮的授权动作。它的形态很轻（一条 fast-forward，`git push origin 074a518:main`），
但轻不是自己批的理由。§16 的最终报告把它列进「孤立的外部权限操作」那一栏。

## 6. 外部 WIP 隔离

以下对象**本轮不碰**，理由不是它们不重要，是它们在别人的活动引用里：

- MarketOps 并行开发中的 checkout、dirty 文件、活跃 worktree。
- `lit-0106-*` 容器：`RESERVED_BY_OTHER_WORK`。禁止 stop/restart/remove/recreate/reconfigure/改端口/
  当 ServerOS 或 Laya 或 Testing 的测试环境复用。只读必要 metadata 以避免冲突。
- LiteLLM need A（toolcall/typography）：据信已修，**只验证不重实现**，不重跑同一个产品验收。
- 别的 agent 正在跑的 pytest（本轮观测到 zhengji 的 litellm 进程）——不动。
- `records/` 的未提交资产（`projects/tokyo/`、`SRV-0008/`、`environment/`、`migrations/`）：
  只存在于磁盘，禁止 clean/reset/checkout。
- `~/.codex/`、`skills/*/SKILL.md-windows`：Windows onboard，不动。

## 7. 上一版列在这里的九条问题——都已关闭

这一节原本是「待决决策包」。九条不是九个需要人重新决定的产品问题，所以它们全部按已定的
设计实施了。逐条记下落点，因为「问了但没答」和「决定过并落地」在交付里长得一样：

| 旧的问法 | 落点 | 证据 |
| --- | --- | --- |
| 1. `noc-copy-20260922@c233946` 要不要落到 `main` | 按设计**整合而不是重写**：从最新 `origin/main` 起集成分支，reconcile 那个分支的提交 | `aef122d`、`dc23707`、`2f77b02`（`runtime/cards.py`、`presentation.py`、`cli.py`、`card-definitions.yaml`、`card-scenarios.yaml`、`notification-contract.yaml`）；`CARDS-STYLE.md` 随之落地 |
| 2. 东京卡要不要搬到 `serverosctl card` | 是。`serverosctl card status` 是**唯一**人读的主资源卡面 | §3.6：`monitor_card.py` 仍在自渲染，属**待迁**（见下） |
| 3. `.staging-*` 怎么处置 | 先**只读分类**，不删 | §7.1 |
| 4. `automated-testing` 属于哪个 pack | 三个包都加，且都是 `required` | §3.8；commit `0980a57` |
| 5. A8/A9/A10/B5 要不要有 pack | 只给 A8 `build-release` 加进 `pack:litellm-development`；A9/A10/B5 **保持 standalone** | §3.8；同 commit |
| 6. 要不要给 `serveros-testing` 注册第二条 plugin 记录 | 是，一条**独立**记录 | §3.7；commit `e7d20f3` |
| 7. 要不要在这台机上装它 | 是，走正规 plugin 安装路径并做 fresh-session 验证 | §3.3 |
| 8. 要不要教 `release.py` 非 node composition 的形状 | 要，按**显式 artifact kind** 分派，不按名字特判 | §3.3；`tests/test_release.py::ArtifactKinds`、`StagingIsNotADeletionOpportunity` |
| 9. 要不要重建并 promote `serveros-linux-work` | 已在授权内执行，**且目标版本是推出来的不是写死的** | §2、§7.2 |

### 7.1 `.staging-*` 的三个目录：分类结果与处置

`release.py` 的规则是**基于身份的拒绝**，不是无条件的 `rmtree`：staging 不存在就建；已存在
且 release 身份相同、明确陈旧或有主，才允许安全清理或按协议续做；**身份/意图未知就拒绝**。
这条规则已在代码里并被测到（`StagingIsNotADeletionOpportunity`）。

三个目录的只读分类：

| 目录 | 有 `ARTIFACT.json` | 被谁引用 | 处置 |
| --- | --- | --- | --- |
| `.staging-c233946` | 无 | **Codex 描述符曾指着它**（`./.staging-c233946`） | **留在原地**。它的名字被一个描述符用过，而那份引用已由 §5 的构建期修法改成 `./serveros-linux-work/current`；即便如此也不删——它的来源是一个已 promote 的 revision，删它换不来什么，误删却要重新构建 |
| `.staging-a4758a0` | 无 | 无 | **留在原地**，记 `STAGING_OWNER_UNKNOWN`：对应已 promote 的 `main@a4758a0`，但**owner 与意图无法从盘上证明** |
| `.staging-978a0f9` | 无 | 无，且无对应 revision | **留在原地**，同上；它是三者中最接近残渣的一个，但「接近」不是判据 |

结论写成一句：**三个都不删，理由是任何一个都存在不确定性；不确定就不动，继续做别的事。**
这是 §5 的规则要求的结果，不是拖延。

### 7.2 只剩要人做的五件（其余都已做完）

| # | 动作 | 为什么不是本轮能做的 |
| --- | --- | --- |
| 1 | **重启 `serverosd.service`**（PID 2615936，起于 11:00:26） | `current` 已是新 revision，shim 也已指向它，所以**新开的进程拿到的是新代码**；但长驻进程仍在跑 promote 之前那一版。重启会打断这台共享机上别人正在跑的东西。等一个安全窗口，`HUMAN_EXECUTED` |
| 2 | **把 CC marketplace `hwz-server-os` 指到新 release，并 `claude plugin update` 工作台** | 遗留目录 `hwz-server-os/current` 声明的 plugin 名是 `hwz-server-os`（3 skills），新 release 声明的名字是 `hwz-server-os-linux-work`（5 skills）。指过去等于**给已装 plugin 改名**——用户可见的身份变更，不是基础设施细节。已装那一份仍是 `3.1.0+srv0003.20260915a` |
| 3 | **给日渲染上弦**：`systemctl --user enable --now serveros-daily.timer` | 单元已装、已手工跑通一次（§3.9），但没有启用。单元头部写明「enabling is the act §8 puts on the approved list」——自动产出与手工验证是两个动作 |
| 4 | **装原生告警投递那一跳**：`scripts/release.py alert-worker --conversation <id>` | 它与它的 timer **从没装过**（§3.6 第 3 条），所以 701 条 `last_notified_at` 来自 `alert_digest.record()` 而不是原生投递。这个动词在没有 `--conversation` 时拒绝——而「往哪个会话发」是一次外联决定 |
| 5 | **给漂移采集器上弦**：在 `~/.claude/settings.json` 的 `SessionStart` 下注册 `records/information/raw/drift_sync.py`（§9.20） | 两件都超出本轮：改的是用户的 Agent 配置（§42），而且**接上以后每次会话开始都会经 lark-cli 去飞书取一次**——那是一次持续的外联，不是一次配置编辑。这个缺口现在**有读数、有名字**：`serverosctl device agents` 报 `NOT WIRED`，`serverosctl information watched` 报 `OVERDUE 6 天`（`2026-09-16` → 截止 `2026-09-22`），契约把它记成 `UNWIRED_ON_THIS_HOST` / `NEEDS_HUMAN`，改动连同回滚写在 `runtime/workbench_sources.py` `DEPLOYMENT` P2 |

五条都是**外部/共享面动作**，不是内部架构选择——所以它们出现在这里，而不是被包装成
「请用户决定」。其余七条已按设计落地。

## 8. 卫生发现（只给位置，不给值）

| 位置 | 现象 | 为什么值得看 |
| --- | --- | --- |
| `~/.config/systemd/user/serveros-alert-sync.service.d/10-botmux-token.conf` | drop-in 存在，但它属于的 `serveros-alert-sync.service` 与 `.timer` **都不存在** | 不是残渣：**一个设计好的投递跳从没装过**，见 §3.6 第 3 条。装它是 `release.py cmd_alert_worker` 的活，且需要 `--conversation` |
| `hwz-monitor.service` | dead | 死服务 |
| `~/.claude/settings.json` | `ANTHROPIC_AUTH_TOKEN` 内联，且 `ANTHROPIC_BASE_URL=http://127.0.0.1:26500`，`permissions.defaultMode: bypassPermissions` | 明文凭据落在 HOME 下，而策略说凭据只住 vault |
| `sanitize.default_files()` | fail-open | 脱敏失败时放行，方向反了 |
| `runtime-assets/hwz-server-os/.candidate-074a51863d92cfbb89608a28f0779668aa448408-lw`（5.5 MB）与 `runtime-assets/hwz-server-os/serveros-testing/.candidate-074a51863d92cfbb89608a28f0779668aa448408`（32 KB） | 两个 `ARTIFACT.json` 齐全的构建输出目录（`3.1.0+srv0003.20260922a` / `1.0.0+srvtest0001.20260922b`），**以 ability 仓的 revision 命名**，顶层那个还带一个手写的 `-lw` 后缀 | **不是另一个实现，是本轮自己的构建输出**：`build.py` 的 `--output` 是必填且无缺省，所以这两个名字是**调用者起的**，仓库里没有任何代码产出 `.candidate-`（`release.py` 用的是 `.staging-<revision>`，`build.py` grep `candidate` 零命中）。`stage()` 从它们 `copytree` 进 `revisions/` 之后**没有任何东西再消费它们**。记在这里是因为：名字里的 `074a5186…` 是 ability 仓的 revision，`-lw` 是人加的消歧后缀——**名字不承载身份，`ARTIFACT.json` 才承载**，这与内存里那条「产物名只有后半截是真的」同族。**不删**：清理在本项目是 proposal-only，且它们是本轮两次已 promote 的构建的输入侧存证 |

第 4 条只报行为，不报调用点——本轮没读它的调用链。

## 9. 待改对象的 owner / base / consumer（P1–P7，实测）

计划 P0 要的第三样东西：「盘点每个详细设计对应的 source、实际入口、consumer、现有字段与
真实 producer」。下面每条都是读过盘之后写的；grep 没命中的写 `not found`，没读的写 `not read`。

四处**在 fabric 树里没有消费者**的孤岛，是本轮最实的接线缺口（第三处见 §9.7，第四处见 §9.10）：

| 对象 | 在哪 | 谁调它 | 判定 |
| --- | --- | --- | --- |
| ~~records 的 66 份 `information/raw/**/manifest.json`~~ | `/data/home/vince/workspace/records/information/raw/…` | **已接线**：`serverosctl information captured`（`intake/capture_coverage.py`）。此前 `grep -rn "pagination_complete\|msg_count" --include=*.py` 在 fabric 树里零命中 | **本轮已修**，见 §9.6。设计要求「capture policy 与 materialized coverage 分开」，此前 coverage 这一半没有被消费 |
| ~~`space/resolver.py`（402 行，含词汇表）~~ | serveros worktree `space/resolver.py` | **已接线**：`serverosctl space resolve`（`runtime/cli.py`，`_load_module_from(REPO_ROOT / "space" / "resolver.py", …)`） | **本轮已修**，见 §9.5。此前只能 `python3 space/resolver.py --request …` 单跑——模块**在 artifact 里**而没有任何东西加载它，于是装在节点上的动词不是缺失，是**叫不出名字**。它的 docstring 自己声明「不认识 lark-cli 且永远不许认识」，所以缺的从来是**接线**不是实现 |
| ~~`records/information/understanding/**/*.md` 的出处~~ | `/data/home/vince/workspace/records/information/understanding/…`（14 份卡） | **已接线**：`serverosctl work understanding`（`intake/understanding_delta.py`）。此前 `grep -rn "message_position" --include=*.py` 在 fabric 树里零命中，`workchain.py` 只数写了 `## Sources` 标题的卡 | **本轮已修**，见 §9.7。这一层的 `README.md` 要求卡可追溯，而没有东西核对过任何一条引用 |
| ~~`records/information/raw/DRIFT_WATCHLIST.txt` + `.drift-sync-state.json`~~ | `/data/home/vince/workspace/records/information/raw/` | **已接线**：`serverosctl information watched`（`intake/watch_coverage.py`）。此前只有采集器自己读（`drift_sync.py:71`），`grep -rn "DRIFT_WATCHLIST\|drift-sync-state"` 在 fabric 树里零命中 | **本轮已修**，见 §9.10。缺的是 **watchlist 与 manifest 的 join**：一份采集都没有、有两份、或者 manifest 解析不了，三种在 manifest 遍历里全都不可见——**因为缺的正是 manifest 本身** |

| 包 | 对象 | source | 入口 | consumer | 判定 |
| --- | --- | --- | --- | --- | --- |
| P1 | 文档身份与权威链 | `documents/document_governance.py:79`（`serveros/document-identity.v1` 等四个 schema），`document-vocabulary.json` | `serverosctl document resolve --request` (`runtime/cli.py:7260`) | — | working，纯内核无 I/O |
| P1 | Context Capsule | `context/resolver.py:37` | `serverosctl context startup` (`cli.py:1736`) | — | working |
| P1 | Publication receipt | `runtime/daily_report.py:77` `RECEIPT_SCHEMA`，`receipt():924`，`publish():1016` | `serverosctl report daily` (`cli.py:225`) | — | working，**树里唯一实现**。**两处行号本轮核定更正**：上一版写 `publish():993` 与 `cli.py:7057`，实测分别是 `:1016` 与 `:225`（`runtime/cli.py:7057` 现在落在别的代码里）。行号只在指定 revision 时才有意义，这两处是拿当前工作树量的 |
| P1 | `Source` 类型 | — | — | — | **not found**：没有该类型。最近的字段是 `document_governance.py:252` 的 `source_ref`、`memory/record.py:83` 的 `source`（REQUIRED）、Item 卡的 `source_refs` |
| P1 | 编写的 follow 源清单 | `intake/workchain.py:723` `load_sources()` 读 `<records_root>/<project>/context/sources.yaml` | `serverosctl work coverage --project` (`cli.py:5592`) | — | 有读者，**但盘上没有 `sources.yaml`**（`find` 空） |
| P2 | 五个采集器 | `/data/home/vince/workspace/records/information/raw/{sync_feishu_chat,expand_feishu_doc,expand_feishu_files,drift_sync,_redact}.py` | 各自 `main()`；`drift_sync.py:2` 自称 SessionStart hook driver | lark-cli（`sync_feishu_chat.py:131`、`expand_feishu_doc.py:42`） | working；`information/raw/` 在 records 仓里是 clean |
| P2 | 唯一脱敏实现 | `records/information/raw/_redact.py`（327 行，`a1c1a4d00fd798eb…`） | 库：`redact()`、`persist_secrets()` | `sync_feishu_chat.py:29`、`expand_feishu_doc.py:32`、`redact_backfill.py:54` | **盘上现在是 3 份，3 个不同哈希**（2026-09-22 重量，上一版记的是 5 份 / 2 份逐字节相同——那时还在的 `reconcile-20260922` 与 `clean-line-20260920` 两个工作树已退役）。另两份是**别的 workstream 工作树里的陈旧快照，不是竞争实现**：`worktrees/records/SRV-0003/…`（75 行，2026-09-16）、`worktrees/records/MKT-0001/…`（90 行，2026-09-20），两者都早于 `ec500a4`（12 条脱敏规则落进 records，含**口令**四种形态）。**风险是真的**：那两个工作树里的采集脚本 import 的是本地那份，跑起来用的是没有口令规则的版本。**不动它们**——不属于本轮授权范围，且它们是别人工作树里的在制品 |
| P2 | 黑名单 | `records/information/raw/DRIFT_WATCHLIST.txt`（5 行，1 条 `wiki-litellm-work-doc`） | **两侧都有读者了**：采集器侧 `drift_sync.py:71` `load_watchlist()`（`DRIFT_WATCHLIST` 可覆写，支持行内 `#` 注释；**2026-09-25 改**：缺失时**不再**回退内置默认 `["wiki-litellm-work-doc"]`，改为返回空表并明说「未配置」——内置默认会让「没有人配过 watchlist」与「配过、恰好就是那一条」在读数上无法区分，而这一格的上游本来就是一个**零命中**的缺口；空表 + 显式说明才是可辨的）+ `locate()` 按 `link_id` 反查；ServerOS 侧**本轮补上** `serverosctl information watched`（`intake/watch_coverage.py`，§9.10） | `drift_sync.py`（自称 SessionStart hook driver） | **上一版这一格写的「not read」是错的**（采集器侧读得好好的），2026-09-22 改。**那一件真的缺现在有面了**：注册 `drift_sync` 为 SessionStart hook 的 `settings*.json` 在本机 **not found**——「装好了、手动能跑、没有上弦」，与 §3.9 的日报渲染腿同一形状——而**这个事实本轮有了自己的读者**：契约加 `capabilities.session_drivers`，`serverosctl device agents` 逐 driver 报 `NOT WIRED` 并把它作为**已记录的缺口**（`UNWIRED_ON_THIS_HOST` / `NEEDS_HUMAN`）打印出来，见 §9.20。**注册本身仍是 HUMAN_EXECUTED**（改它要动用户的 Agent 配置），仍然只记不代做。上一版说「ServerOS 这一侧没有读者」时**把 `last_verified_at` 也连写了进去，那一项是错的**（`intake/capture_coverage.py:436` 一直在读，§9.6 就是为它写的）；真正零命中只有 `DRIFT_WATCHLIST` 与 `drift-sync-state` 两个串，缺的是 **watchlist 与 manifest 的 join**——本轮已补，见 §9.10 |
| P2 | 修订流（源编辑/撤回的只增事实） | `records/information/raw/sync_feishu_chat.py` `_events()`（两遍：**判据 → 独立确认读 → 落**），`messages.revisions.jsonl` 与 `messages.jsonl` **分文件**，前者只 append、永不重写后者 | `sync_feishu_chat.py` 主流程（每次运行都跑，不是单独命令） | manifest `sync.{revision_count,retraction_count,restoration_count,revisions_compared,unstable_read_count}`；`_note_of()` 把同一组数写进采集说明 | **本轮闭环**。判据 = 「脱敏后正文与存档不同」**且**由**另一个读面**复读确认（`im +messages-mget`，≤50 个 id/次，只读有争议的那些）。两个读面在 30 条稳定条上 **30/30** 逐字节一致、在 20 条不稳定条上 **20/20** 不一致 —— 这才叫独立证据。`unstable_read_count` 数的是**这次看到多少条正文不可复现**而不是**这次新记了几行**：按后者报的话，一个已经记过的会话会输出 `0`，而 0 在缺分母时读作「没有这种东西」。**幂等（同一轮验收里发现）**：存档 append-only 且**已捕获的行永不被改写**，所以「源现在是 X、存档是 Y」这个差**下一轮还在** —— 按差本身记，行数就成了**运行次数**的函数（这一会话一轮 60 条编辑 + 20 条不可复现 = 每天 80 行同一批事实，读者会把行数读成「源改过多少次」）。去重按**这条修订断言的是什么**分：`edited` 断言「源读作 X、存档是 Y」，两个摘要在身份键里（换一处差异 = 另一条事实）；`retracted`/`restored` 断言状态翻转、`unstable-read` 断言**这条正文不可复现**这个**性质** —— 它们的摘要每轮都可能不同（对不可复现条那是必然），摘要在键里就等于每轮换一个键、每轮记一条。两种错法各有一条测试看着：键太粗会吃掉真的后续改动，键太细会让不可复现条每轮重记。**还有一层**：两次读恰好一致**不等于**这条消息可复现 —— 实测两轮之间同一个 id 既有 `unstable-read`→`edited`，也有 `edited`→另一个正文的 `edited`。这种「碰巧一致」的编辑对那一轮是真的，所以照记，但行上带 `prior_unstable: true`，读者不会把一次碰巧当成一条确定的编辑。10 处变异全部致死（含「从不确认」「读不到就静默降级」「确认读整个候选集」「prior 读成空集」「键太粗/太细」「该标的不标/不该标的乱标」）。**修好之后的实测追加率**（同一会话、同一份存档、连续三轮）：80 行 → 追加 4 行 → 追加 3 行；累计 87 行落在 **82 个** message_id 上，其中 **78 个恒为一行**，多行的只有 **4 个**，全部是边际 id（两次读有时一致、有时不一致）：3 行是 `unstable-read`→`edited`（带 `prior_unstable: true`），1 行是 `edited,edited,unstable-read`。**它不是零, 而这是对的**：一个边际 id 每轮读到的新变体本身就是一条新观测，记下来并标出来比压掉它诚实 —— 要防的是「同一批事实每天再来一遍」（那个数是 80/轮），不是这个 3 |
| P2 | 正文不可复现（读噪声） | 不是本仓的代码，是**源的实测性质**：同一个 `message_id` 连续两读返回不同正文 | — | — | **本轮实测**（nanjing-office 群，4989 条）：两次独立的全会话实测分别得到 **20 条**（0.40%）与 **22 条** —— **数本身不是常数**，因为被数的那个性质就是不可复现的；所以这个数要读成「每次运行看到的量级」，不是「有几种」。两次读之间 `updated`/`update_time`/`deleted`/`create_time`/`sender`/`mentions`/`message_position` **逐字节相同**，差异**全部**落在 latin/digit（首字母大写的 1–8 字母英文词，偶尔带尾数字），**中文一个字节不动**；无 URL、无 `<redacted-*>` 占位、无 `<at …>`，19 条抽查全为 `msg_type: text`。归属探针（抽样首个不稳定条）：差异词 4 个，与 mention 名、reaction 类型**零命中** —— 它落在正文本身，不来自任何别的字段（探针在该条之后因 `reactions` 元素是字符串而非 dict 而崩，故只到 1/22 条，已按实际样本量如实记录）。**它推翻了一条判据**：旧代码把「正文与存档不同」直接判成编辑，于是把读噪声写成只增的「源改过」事实 —— 真实那次运行 83 条事件里 77 条走这条分支，**而这条分支当时一条测试都没有**。处置：新增 `unstable-read` kind（`because=two-reads-disagree`），与「编辑」并列而互不包含 —— 一条 `unstable-read` **不断言源改过任何东西**，且**不落正文**（落哪一版都会被读成「这就是那条消息」），改落两个 sha256。确认读失败一律**抛**，不降级：降级会把「读不到」读成「不稳定」，把一次事故造出一条关于源的事实 |
| P2 | 采集的每轮浪费 | `sync_feishu_chat.py` 未传 `--no-reactions` | lark-cli 默认给**每条**消息补一次 reactions 请求 | — | **本轮已闭合**（records `56783f1`）：存档 schema `to_record` 只有 10 个键、**从不存 reactions**，这一会话 4989 条 = 每轮 4989 次请求换一个当场丢掉的字段。加 `--no-reactions`（每页都带，desc 交叉探测同路径）。**实测验证而非推断**：一次真实请求 `ok: true`，取回字段恰好是 `to_record` 用的那 10 个、`reactions` 零命中 —— 关掉的确是没人读的字段，存档内容不变，故不需重采 |
| P2-D | 网页采集的生产者 | `serveros/web-capture.v1`（声明层 `records/information/sources/web/<slug>.yaml`，观测层 `records/information/raw/web/<provider>/<slug>/manifest.json`） | **没有生产者**。两侧都有**读者**：serveros 的 `tests/test_capture_coverage.py`、`test_external_reference.py`、`test_material_index.py`、`test_source_policy.py` 四个测试文件读这个 schema；盘上那**一份**实例（openai-developers 那篇）不是本仓任何一个脚本写出来的 —— 在 records 与 serveros 两仓 `command grep -rln 'web-capture'` 的结果里，**写方零命中**，只有读方与那一份 manifest 本身 | — | **缺口形状与 §3.9/§9.20 同类：装好了、有读者、没有生产者。**判据不是「有没有那份 manifest」，而是「能不能再产出第二份」—— 现在不能：换一篇就得手搓。**本轮只登记不动手**，两个理由：(1) 计划里 P2-D 的动作就是报告；(2) 用户长期约束里有「不要复制整篇官方文章」，一个采集器必须围绕 `permitted_scope` 设计（存什么字节、放哪个面），这不是顺手能做完的活，草率落地会把一条约束做成代码里的既成事实 |

`records/information/understanding/{people,projects,topics}/*.md` | **两侧都已接线**：读 `serverosctl work understanding`（`intake/understanding_delta.py`）、写 `serverosctl work understanding-patch`（`intake/understanding_patch.py`） | — | **两半都补上了**：读的一侧见 §9.7（14 份卡逐份对账到源，`BEHIND 3 · UNMEASURABLE 7 · UNTRACEABLE 4`）；写的一侧见 §9.9（15 条提议全 `AWAITING`，`--write`+`--verify` ok）。设计要的「最小 incremental writer」不再缺口。**仍未 LIVE**：没有任何消费者读这份 patch |
| P3 | Minutes / MeetingBundle | 无 Python 类。`space/meeting-policy.schema.yaml:30` 声明结构；内核 `space/meeting_relevance.py:69`；**生产者 `space/meeting_bundle.py`（§9.15）** + 契约 `space/meeting-bundle-contract.yaml` | ~~`python3 space/meeting_relevance.py --request`（**未接 serverosctl**）~~ **已接线**：`serverosctl space meeting`（`--request` 与 `--sidecars` 两条入口） | — | 内核 working，**接线已补**（§9.5）；**生产者也补上了**（§9.15）：会议自己声明的 sidecar 现在能组装成内核收得下的 bundle，`COMPLETE` 才把问题递给内核，`INCOMPLETE`/`MIXED_SCOPE` 只给读数不给问题。**仍未 LIVE**：本机没有任何真实会议 manifest 可测，四个探针声明是合成的（`RUN-20260922-raw-coverage/meeting-bundle/*.json`） |
| P3 | 记忆账本/投影/scope | `memory/{record,projection,scope,handoff}.py` | `serverosctl memory {audit,project,status,subject}` (`cli.py:6479`) | — | working；`docs/memory-fabric.md` 逐条点名这些模块 |
| P12 | **活着的**技能面落后于 canonical | canonical：本仓 `skills/serveros/SKILL.md`（157 行，`21281cfc3c0b`）。装到机上的：插件缓存 `~/.claude/plugins/cache/hwz-server-os-linux-work/hwz-server-os-linux-work/3.1.0-srv0003.20260923d/skills/serveros/SKILL.md`（157 行，`fbfbefbc5870`）；另有 `runtime-assets/hwz-server-os/current/skills/serveros/SKILL.md` 同样落后 | — | — | **P12 待做**（不属 HUMAN_EXECUTED：装 vince 用户级 runtime/插件在本轮授权内）。**实测**：两份**逐字节只差第 143 行**，而差的那一行是**北京搬迁留下的错话** —— 活的那份还在说「`/` 是系统盘，`/opt/applications` 是数据盘」，而本机 `ls -ld /opt/applications` → **No such file or directory**；canonical 已改成与节点无关的「数据盘（机器策略里的 `DATA_ROOT`）」。同一处陈旧还落在 `skills/serveros/scripts/architecture_check.py`：两份只差一段**注释折行**，canonical 那份带着 `# node-path-ok: 考古记录` 标记而装的那份没有。**判据不是「两份文件不一样」，而是「Agent 现在照着读的那句话是不是真的」—— 它现在不是。**处置走 build→promote→restart（不手改派生安装：手改会在下次 promote 时被盖掉并再次漂移）。**2026-09-27 更新（§9.50）**：那条处置**已被执行，而且第一次是量出来的**。两个 composition 的 cachebuster 各自移到 `27a`（`50a86fa` / `512f886`），promote 与 install 都真的落了盘（`installed_plugins.json`：`…linux-work` `3.1.0+srv0003.20260927a` `lastUpdated 2026-09-26T16:11:47Z`；`serveros-testing` `1.0.0+srvtest0001.20260927a` `16:33:29Z`）。把本仓这条规则指向**已装载荷**的 `skills/`（不是指向仓库）：`27a` 两个各 **0** 条，被取代的 `23d` **13** 条、`22b` **1** 条，而两版 `skills/` 文件清单逐条相同（26 文件 / 5 条 Skill），所以这是同一套文件上的比较。**第一个落点关闭。** 第二个落点 `runtime-assets/hwz-server-os/current`（`3.1.0+srv0003.20260923c`）**仍逐字节只差第 143 行**，但它**没有消费者**：`enabledPlugins` 与 `extraKnownMarketplaces` 里都没有 `hwz-server-os`，四棵配置面 `command grep` 这棵树 **0 命中**，`ps` 无一条引用它，`skillUsage["hwz-server-os:serveros"]` 最后读于 **2026-09-20 01:30**。「那句错话躺在一个载体里」与「有 Agent 正在被这句话教」是两句不同的话：**前者成立，后者不成立**。这一格的处置因此从「待做」变成**一条 proposal**——重建这个基座 composition，或退役这个被取代的载体；理由与读数见 §9.50.4 |
| P4 | 文档关系边 | `relations/project.py:61`（`serveros/relations.v1`），`relations/schema.yaml:29` | `python3 project.py --records ROOT --write relations/current.jsonl` | — | **partial**（比上一版进了一步）：8 个谓词里 3 个有生产者（`belongs_to`、`derived_from`、`attached_to`），其余 5 个 `declared_only`。本机实测 239 行 / 19 条报出，见 §9.8；另 5 个谓词逐条查过，**本机没有载体**，不是差功夫 |
| P4 | Feishu 空间适配 / 共享 publisher | `skills/feishu-space-management/`：**只有 SKILL.md 文本，本仓没有代码**。`skills/document-stewardship/`：本仓同样只有文本，但**外仓有代码**——`~/.claude/skills/document-stewardship/scripts/maintain.py`，且 `plugin/profiles/linux-work.yaml:119-123` 声明它 `available` | — | — | partial。**上一版这一格把两者合成了一句「只有 SKILL.md 文本，没有代码」，那是错的**：`document-stewardship` 在外仓有实现，且 ability-management 注册表（HEAD `8d98aff`）**不持有**它——与那 8 条跨仓红同族。**§9.23 再纠一次**：那个实现里**没有 publisher 循环**。文件只有 `refresh` 与 `audit` 两个动词，它自己的 docstring 写着「**它从不写文档正文，也从不写 profile**」。原来那句「取 revision N → 定位 → 校验 → 候选 patch → …」描述的是**契约要求**，被读成了**已有实现**——恰好是这一格本来要防的那种误读。真正的 publisher 是本轮新写的 `intake/document_publish.py` + `runtime/document_transport.py` |
| P5 | 员工 KB 生产者 | `src/litellm-support/scripts/build_agent_kb.py`，输出 `dist/employee-kb/`（`dist/` 被 gitignore） | `python3 scripts/build_agent_kb.py` | — | working，但**在 ServerOS 之外** |
| P5 | KB 消费 | `intake/support.py:187,432,534,697,855,977`（另 `read_entries` 的第三个字段 `unrecognised`，见 §9.14），契约 `intake/support-contract.yaml`；**本轮新增 `intake/support_handoff.py` + `intake/support-handoff-contract.yaml`（见 §9.19）** | `serverosctl support {entries,match,answer,check,handoff} --kb-root <path>`（support 组：`cli.py:8719` @ `4fa1748`，`:8784` @ 本工作树；旧记账写 `cli.py:7206`，实测那里是 taxonomy 转发，已纠正） | — | working，但 `--kb-root` 必填且**没有任何东西把它接到 litellm-support 那棵树**。**§9.14 修掉了一个把「没读到」印成「干净」的洞**：契约写就的那棵树读数不变（`21 entries` / `21 index rows` / exit 0），员工树现在 `entries`/`check` 都是 exit 2 并点名读过哪些文件、哪个 pattern 没匹配上。**§9.19 补上无答案转人工的状态/投递路径**：`support.answer` 那个此前无消费者的改进候选现在有消费者，两个锚未绑定时输出 `PENDING_DELIVERY`、`notified` 只在 `DELIVERED` 为真——但**两个锚在本机仍然 UNBOUND**，所以生产读数是待投递 |
| P5 | 公开指南生产者 | — | — | — | **not found as code** |
| P6 | 线程准入 / 控制存储 | `runtime/coordination.py:89,116,228`，`runtime/agent_control_store.py:86,97,103,149` | `serverosctl coordination {resolve,bind,close,list}` (`cli.py:6632`) | — | working |
| P6 | provider 侧准入 | `mcp/agent_session_service.py:58,115,159,251` | `mcp/bin/serveros-agent-mcp` | — | working（support→readiness→admission 顺序在 `:229`） |
| P6 | 租约 / 所有权 / 需求板 | `runtime/lease_store.py:61`，`runtime/ownership_gate.py:209`，`intake/global_board.py:84` | `serverosctl {lease,ownership,requirement}` | — | working |
| P6 | focus 授权 | `intake/focus_source.py:56,71,83,225`；`context/delegation.py`（本轮新增，纯模块） | `serverosctl work focus`（不再接受 `--grants`） | `intake/daily_cycle.select_focus` ← `context/delegation.envelopes` | **本轮已修，见 §9.11**。上一版这一格写的「`--grants` 未绑定」**是错的**：那个 flag 一直绑着，绑在一个能用 JSON 文件凭空铸造授权的洞上（`intake/daily_cycle.py:51` 自己写着「Validate scope/time/budget, not the authenticity of the supplied grant」）。**洞已闭合**：flag 被删除，信封只能从存储里已记录的授权推导 |
| P6 | 委托/授权存储 | `runtime/agent_control_store.py` 的 `grants` 集合（第五个，本轮新增 `put_grant`/`get_grant`/`grants`/`revoke_grant`） | `serverosctl work grants`（本轮新增） | `context/delegation.envelopes`、`runtime/cli.py#cmd_work_focus` | **本轮已修，见 §9.11**。上一版「not found：只找到规则类（`runtime/agent_sessions.py:82`、`fleet/agent_control_router.py:244`）」**在那一轮是对的**——规则一直在，缺的是**存储**，而规则不能代替存储 |
| P6 | 原子准入（选择+预算+租约+派发一个事务） | `runtime/agent_control_store.py` 的第六个集合 `admissions`（`admit`/`get_admission`/`admissions`/`confirm_admission`/`retire_admission`）；策略那半是纯函数 `intake/daily_cycle.admission_plan`。另两条关系仍读对方自己的 store：`lease_store.py:172`、`bounded_invocation.py:88` | `serverosctl work {admit,admissions,confirm,retire}`（本轮新增，`wk_admit` **故意没有 `--grant`**） | `intake/daily_cycle.admission_plan` ← `select_focus` 的 decision 与板上的 `grant_ref` | **本轮已修，见 §9.13**。四样在**同一次 `_mutation()`** 里写入，共用那个 store 已有的 sidecar 锁；租约与派发**不在**该事务里，各自按相**读回真实 store**并只记录对方回答了什么——计划那后半句要的「可恢复幂等关系，不假装跨库事务」就是这个形状。上一版「仍是 partial」在当时是对的。**同一节记下明确没做的**：没有预算池（`exhausted` 在本仓没有写入者）、没有清扫器、`work admit` 没有调用者（§3.9 的腿没上弦） |
| P7 | 已安装 Quay | `~/.claude/plugins/cache/quay/quay/0.11.0/`，`bin/quay --version` → `0.11.0-dev` | MCP：`…/0.11.0/.mcp.json` → `vendor/quay/dist/quay.js mcp` | — | working（`0.4.0` 没有 `goal`，`0.11.0` 又有了——本机至少三代并存） |
| P7 | codex provider | `mcp/codex_app_server_provider.py:88,89,95,564,1181` | `--live` 才启动，默认只读 | `codex-cli 0.153.4`（`~/.local/bin/codex`，与 `MEASURED_AGAINST` 一致） | working |

### 9.1 两个必须记下的现场事实（已收敛，保留原始观测）

**（一）当初活的节点跑的不是这个 worktree。** 实测 md5 对比：

```text
DIFF  runtime/cards.py              DIFF  runtime/card-definitions.yaml
DIFF  runtime/presentation.py       DIFF  runtime/card-scenarios.yaml
DIFF  runtime/cli.py                DIFF  runtime/notification-contract.yaml
SAME  runtime/coordination.py       SAME  documents/document_governance.py
```

当时安装面是 `noc-copy-20260922@c233946`（分支 `serveros/noc-copy-20260922`）。
**`runtime/cli.py` 在被别人改**——所以本轮任何 `cli.py` 改动都要**先整合再改**，不复用
那棵分叉的树。

**（二）`CARDS-STYLE.md` 当时只存在于 noc-copy。** 9310 字节，mtime 2026-09-22 11:02，
和那次 promote 同一刻；被 `presentation.py` / `cards.py` / `card-definitions.yaml` 引用。

（一）与（二）合起来给出一条硬结论：**P8（广播/告警 producer/presentation/receipt）
的实现已经在未合并的 noc-copy 分支上做完了。** 所以那一条卡住的不是版本新鲜度，是整条
P8——不整合那个分支，P8 在 main 上无从做起，重做一遍就是第二来源。

**现在的状态**：那条结论已被执行。集成分支从最新 `origin/main` 起，
reconcile 了 noc-copy 的六个 runtime 文件与 `CARDS-STYLE.md`，`aef122d`/`dc23707`/`2f77b02`
落在 `fb97f77` 上，并已 promote 成 `serveros-linux-work/current`（§2）。
所以 §9.1 记的是**当时的观测**，不是现在的状态；它值得留着，因为「安装面与工作树分叉」
这件事在同一台机器上会再次发生。

### 9.2 P8 的四条断点（前两条已修，后两条见 §3.6）

| 设计要点 | 当时的实测 | 现在的判定 |
| --- | --- | --- |
| frozen payload + payload hash | `grep -rn 'payload_hash' runtime/*.py` → 0 | **已修**：`alert_digest.py:693` 起 `sha256(blob)` → `payload_hash`，`version = f"{generation}@{payload_hash[:12]}"`，冻结块 schema `serveros/alert-digest-frozen.v1` |
| 播报记录绑定发出那一版 | `alert_digest.py:790,811,822` 取活登记表现值，于是 compose 与 record 之间若发生升级，记录会把它写成「以新 severity 播过」 | **已修**：`record()` 收 `frozen`，severity/state/**episode 都取自冻结块**（`:960`）；`_refuse_a_different_document()` 在冻结块与它一起来的那批行**互相矛盾时拒绝**而不是就地调停（`:907`）；`_frozen_rows()` 只认那一版的行。旧投递的 ACK 不会覆盖新 episode/severity/state，B 留在 pending delta |
| ServerOS 原生投递 | unit/timer 都不存在，从没跑过；701 条 `last_notified_at` 来自 `alert_digest.record()`，**不是** `alerting.record_delivery()` | **仍未安装**。`cmd_alert_worker` 在没有 `--conversation` 时拒绝。这是要人做的一件（§7.2 之外，因为它需要一个获准的接收端） |
| 六个通知生产者 | `runtime/notification-contract.yaml` `producers:` `:130` 起：`alert.opened`/`alert.resolved` → IMPLEMENTED；`session.created`/`session.waiting_input`/`turn.completed`/`approval.required`/`handoff.completed`/`run.terminated` → 各自带 `because`/`disposition` 的 DECLARED_ABSENT | 声明诚实；该文件 `:127-128` 自己记着「这六条曾零调用者而声明对声明的闸照样过」 |

反例是跟着修复一起来的，不是事后补的：发信时升级、发信时 resolve、发信时 reopen、
超时但 provider 已接受、重复重试、cursor 崩溃——六条都在 `tests/` 里各自有名字。

`alerting.py` / `alert_digest.py` / `anomaly.py` / `bin/serverosctl` 在 worktree 与
安装面**逐字节相同**；不同的那 6 个文件见 §9.1。所以 P8 的断点不是版本差，
是真的没做。

### 9.3 P9 的安装覆盖

62 个 ability（skill 41 / plugin 13 / mcp 6 / instruction 1 / hook 1）。
按「composition 成员 + pack 成员」两种口径：

| 口径 | 数 |
| --- | --- |
| 有 composition 或 pack 成员身份 | **20 / 62** |
| 无任何成员身份 | **42 / 62**（其中 plugin 型 13 个**全部**无身份；`hwz-server-os` 是 composition 本身，属类别假象不是缺口） |
| 最新 revision 带 installation 行 | **25 / 62** |
| 任一 revision 带 installation 行 | **35 / 62** |
| 任何 revision 都没有 installation 行 | **27 / 62** |

最后一行的读法要小心：`session-manager` 最新版 `4.0.0` 没有 installation 行，早先的
有 —— 所以诚实的说法是「最新 revision 不带 installation 行」，**不是**「没装」。

`test-envs/` 下只有 `litellm-bridge` 和 `muc-bridge`：**本机不存在
ability-management 或 serveros 的测试环境**。A7 若要 LIVE 需要先有这个，现在没有。

### 9.4 P10：代码在 MarketOps，不在 ServerOS

| 对象 | 在哪 | 实测 |
| --- | --- | --- |
| Laya adapter | `worktrees/marketops/mvp/marketops/adapters/laya.py`，`LayaDecisionProvider:128` | **partial**：~~盘上**没有权重**（`find *laya*` 无模型目录）~~ → **更正 2026-09-23**：权重在盘上，`~/caches/models/laya-multilingual/052592a15d198d9ad47da779604259b10b47b7aa/`（21 文件 / 647 MB，revision 与 `laya-shared-binding.yaml` 的 pin 一致），且已作为受管服务 `laya-shared.service` 运行。`LAYA_CHECKPOINT_DIR` 仍未设，所以**是这条适配器没接线**，不是权重不存在 |
| Jev adapter | `…/adapters/jev.py`，形状镜像 `laya.py` | ~~**UNBOUND**：无 key~~ → **更正 2026-09-23**：`~/.secrets/vault/typesafe.env` 存在（0600，489 B，`TYPESAFE_API_KEY` 108 字符、非空，值从未输出）。它**未注入进程环境**且 `MARKETOPS_DECISION_PROVIDER` 未设，所以阻塞在**接线与出站授权**，不在凭据缺失 |
| Serper adapter | `src/marketops/marketops/search/serper.py`，`SerperSearchProvider:24` | working（adapter+port+tests）；凭据在 `~/.secrets/vault/serper.env`（0600，152 B）。~~**没有跑过真实查询**，所以 LIVE 不可声称~~ → **更正 2026-09-23**：已跑过一次真实付费查询（走 `SerperSearchProvider.from_config(RuntimeConfig.from_env())` → `search(SearchRequest(...))`，HTTP 成功、1 条、2.31s），判 **LIVE**；额度余量/并发/长期可用性仍 NOT_MEASURED。见 `docs/shared-decision-search.md` §2 |
| MarketOps 业务 MCP | `…/interfaces/mcp/__main__.py` + `adapters/mcp_server/*` | **没在任何地方注册**——唯一相邻的 `.mcp.json`（`worktrees/quay/marketops-goalwrite/.mcp.json`）是空表 |
| ServerOS 侧的 decision/search 消费者 | — | **read-and-absent**：`grep -rl 'DecisionProvider\|SearchProvider' runtime/ mcp/ abilities/` → 0 文件 |

**`providers/` 目录不存在——四种独立查法确认，不是没找到。** 全机只有一个 provider
记录：`provider:openviking`，在 `memory/provider-contract.yaml:83`，属记忆域，
且被复制进全部 10 个 serveros worktree/revision（所以它在每个 worktree 里都"存在"，
这本身不是 10 个 provider）。设计只在
`design/SHARED-PROVIDER-CONTRACT.md:7` 提过一次 `providers/laya/`，措辞是「或真实已
存在的等价位置」。ServerOS 实际有的是契约与解析器：
`abilities/provider-contract.yaml`（`objects.Provider`、`provider_kinds`、
`materialization.forbidden` 禁绝对路径/`$HOME`/env 名/凭据）、`abilities/resolver.py:636`、
`abilities/provider_instance.py:297` —— 最后一个的 docstring 自己记着 `instance:`
记录「自契约写下就声明了，**而没有任何 writer**」。

### 9.5 `space/` 的两个内核已接线（本轮唯一一处从「孤岛」变「可达」）

§9 表里那条孤岛不再成立。两个内核此前**在 artifact 里**而没有任何东西加载它们——
`grep -rn 'space\.resolver\|from space\|import space' runtime/ mcp/ abilities/` 零命中，
`runtime/cli.py` 里没有 space 动词。所以装到节点上的不是「动词缺失」，是**动词叫不出
名字**：唯一跑法是 `python3 space/resolver.py --request …` 从一个源码检出。

修法是一个新动词族，不是新实现：

```text
serverosctl space resolve --request <json> [--vocabulary <yaml>]
serverosctl space meeting --request <json> [--kind relevance / cleanup] [--vocabulary <yaml>]
```

四条判据写在代码里，而不是写在文档里：

1. **退出码是内核的**。两个内核的 docstring 都写着「判定 0，请求malformed/读不到 2」，
   CLI 不重排：`PROPOSAL_REQUIRED`（没有规则覆盖这一对、或策略拒绝选一个 canon、或模板
   缺上下文）、`REFUSAL`（清理内核拒绝出计划）、`REVIEW`、`UNKNOWN` **都是判定**，都退
   0。一个在「最诚实的答案」上退非零的动词，会教它的调用者把诚实读成失败。
2. **`--json` 就是内核的记录**，不是 CLI 形状的摘要。测试里逐字段比对了
   `resolve()` / `classify()` / `plan_cleanup()` 的返回值。
3. **`MOVE` 渲染出 `approval`**。词汇表自己把 `approval.cross_surface` 定成
   `proposal-only`，理由是飞书上「加进 wiki」没有自动撤销。一条不带 approval 的 MOVE
   读起来就是一条指令。
4. **没有 `--as-of`**。会议内核按**显式** `as_of` 判过期、判不了就 fail closed，一个
   CLI flag 就是 shell 时钟进来的地方。这条以「`--help` 里没有这个词」断言，并以
   「把 `as_of` 拿掉后 `REBUILDABLE_ARTIFACT_CLEANUP` 仍 REFUSAL」作为它保护的性质。

`REJECTED` 是这轮的第二个判据：两个内核都把 `REJECTED` 写进 `DECISIONS`，而
`resolve()` / `classify()` / `plan_cleanup()` **没有任何一条路径返回它**——只有那两个模块
自己的 `main()` 的 except 分支合成它。所以 CLI 不调 `main()`（那条 `return 2 if
record["decision"] == "REJECTED"` 因此是死代码），而是自己印拒绝理由并退 2。这正是
`cmd_document_resolve` 的 docstring 拒绝发明的那个东西：一个观测不到被发出的状态比没有
状态更糟，因为它读起来像一个检查。

**证明跑在 artifact 上，不在检出上。** 这才是这条缺口的意义，所以证据是这一串：

```text
scripts/build.py --write --bundle plugin/bundle.yaml \
  --ability-repo <src/ability-management> \
  --ability-rev 074a51863d92cfbb89608a28f0779668aa448408 \
  --output <scratch>/artifact
<scratch>/artifact/bin/serverosctl space resolve --request move.json
  decision    MOVE -> wiki
  placement   wiki/report  (project=litellm owner=actor:human_0001)
  approval    proposal-only  ·  this is a proposal and not a permission: nothing here writes
  exit=0
```

`tests/test_space_cli.py`（21 tests / 9 subtests）把它钉住，并且**做过变异测试**：
把 `PROPOSAL_REQUIRED` 改成退 2、把 proposal 警告去掉、把 `cleanup: null` 印成
「nothing to clean up」、把 locator 行的解释去掉——四次各自被对应的那一条抓住，
每次之后按 sha256 逐字节还原（`02e95518…`）。

`tests/test_core_ability_boundary.py` 的 `KNOWN_CLI_CROSSINGS` 加了
`space/resolver.py` 与 `space/meeting_relevance.py` 两条，各带 `because`。这不是把闸门
放宽：`space` 本来就在 `FABRIC_DIRS` 里，所以这两条是新跨界，必须逐条论证。论证是
§24 的那一句——daemon 不可能回答它，因为问题关于**某一个调用者的请求文档**，而 daemon
没有请求；两个内核都不开 socket、不改任何东西，所以这条跨界不带
`mcp/human_control.py` 那条的风险。

**顺带纠一条我自己差点写错的。** 第一次构建时我把 `--ability-rev` 填成了
`e7d20f3b…`（那是 §3.8 记的 **pin**），于是 `build.py` 报 `PIN_MISMATCH` 并拒绝。
我一度据此判定 §3.8 记错了。实测把结论推翻了：

```text
ability-packs.yaml 里五包的 authority.revision = e7d20f3b50e1a86c9998b5dff3f519fa52b4aa1d
能构建的 revision 是 074a51863d92cfbb89608a28f0779668aa448408
  ← "packs: repin all five to the revision that split the plugin records"
e7d20f3b..074a518 只有这一个提交
abilities-manifest.yaml 在两者上同 blob 4afa3a22…
```

所以 §3.8 是**对的**：pin 是读到的 revision 的祖先，且 manifest 在两者上逐字节相同。
错的是我拿 pin 当读的 revision 用。记这一笔，是因为「一次看似证据充分的自我纠错」和
「一次真的纠错」在台账里长得一样，而这次是前者——差一点就把它写成后者。

### 9.6 采集覆盖已接线：第二处孤岛关闭

`space/` 是「模块在 artifact 里、没有东西加载它」。这一处是同一族的另一半：**采集器写了
记录，ServerOS 侧没有读者**。`records/information/raw/` 下 66 份 `manifest.json`，
`grep -rn "pagination_complete\|msg_count" --include=*.py` 在 fabric 树里零命中。

`intake/workchain.py` 确实报飞书面——但它报的是**编写的** `sources.yaml`，那是**被声明**
的一半，而盘上没有 `sources.yaml`（`sources_state = UNPRODUCED`）。声明的那一半有读者，
落下来的那一半没有。P1 的原话是「capture policy 与 materialized coverage 分开」，
所以补的是**分开的两半**，不是给现有 status 加字段。

**`intake/capture_coverage.py`（新，约 480 行）+ `serverosctl information captured`。**

四件事它在代码里拒绝做，四条各有一条测试：

1. **不合并成一个 verdict。** 一份 chat manifest 可以分页完整、同时有三张附件被声明为
   pending。对象级的一个数会是在回答没人问过的问题。分量是 `messages` / `attachments` /
   `links` / `body` / `media`，各自带**据以判定的 facts**；`summarize` 也**按分量计**。
   `"verdict" not in record` 是被断言的一条。
2. **缺字段不是值。** `pagination_complete` 在这台机器上有**三态**：45 份 `true`、
   0 份 `false`、**2 份没记**。第三态被并进任一个都会把「没人记录」读成「记录为否」或
   「记录为是」。`sync.mode` 在 47 份里缺 37 份——`undeclared`，带理由，**不补默认值**。
3. **一份坏的 manifest 不得缩小分母。** 读不了的文件是一条 `UNREADABLE` 记录而不是跳过：
   返回 65 个而盘上有 66，会**恰好少报它没看的那一份**。
4. **不读时钟。** `last_verified_at` 照着采集器写的印。它有多旧是 staleness 问题、有自己
   的阈值，在这里回答等于让这个动词成为它并不掌握的那个事实的权威。

还修了一处自己写出来的错：`read_root` 一开始 `except Refused` 把什么都吞成
`UNREADABLE`。那样一来**本模块的缺陷会被印成「这份文件读不了」**——正是分量存在的理由是
要防的错位，只是高了一层。现在拆成 `Malformed`（关于文件）与 `Refused`（关于读者），
`read_root` 只吞前者，`test_a_reader_defect_is_not_reported_as_an_unreadable_manifest`
钉住这条。

**实测事实（都是真的，且都经独立复算）**：66 份 = 47 chat + 19 document；role 47 chat +
19 link；`messages` 45 COMPLETE + 2 UNDECIDABLE；`body` 16 COMPLETE + **3 INCOMPLETE**
（那 3 份 `content_sha256` 只有 12 位十六进制——它**命名**一个正文，不让任何人复核一个）；
`media` 14 COMPLETE + 5 UNDECIDABLE；`复核` 61 RECORDED + **5 NOT_RECORDED**（那 5 份只有
采集者对自己工作的自述）。`需要看的` 10 个分量、7 个对象。

**artifact 级证明，不是源码检出**：`scripts/build.py --write --bundle plugin/bundle.yaml
--ability-repo <src/ability-management> --ability-rev 074a51863d92cfbb89608a28f0779668aa448408
--marketplace-path <scratch>/dist/.agents/plugins/marketplace.json --output <scratch>/dist/artifact`
（227 members，`intake/capture_coverage.py` 在内），然后
`<artifact>/bin/serverosctl information captured` → 上表 66 份的完整渲染、exit 0；
指向一个 scratch records 树也照读（证明它读的是 `--records-root` 而不是检出）；
`--records-root records`（相对）→ exit 1 并说明「站在哪个目录里读的覆盖数没有别人能复现」。

**变异测试**：九条承重判据各自切断一次，九次全被对应的测试抓住
（bool 当整数 / 可读的 msg_count 是可陈述的前提 / 截断的 digest / 无生产者的键 /
读者缺陷≠文件坏 / role 来自路径 / shape 来自键 / 散文标注为散文 / 缺根≠空根），
每次之后按 sha256 逐字节还原（`bc0f329f21c60fb4…`），`__pycache__` 每次都清。

**门是自动的，不是我再记一次。** `intake/` 早在 `plugin/bundle.yaml` 里声明过，所以
新模块随 artifact 走；而 `tests/loader_graph.py` 的 `production_closure()` 从 `cli.py` 的
加载闭包**推出** `intake/capture_coverage.py`（87 条里的一条），
`test_build.py::test_the_artifact_answers_the_production_loader_closure` 由此自动覆盖它——
这一族「被 shipped surface 加载、却没有被任何东西声明」的缺陷不再需要有人记得。

### 9.7 认识层已对账：第三处孤岛关闭（读的一侧）

§9.6 那处是「采集器写了记录，ServerOS 侧没有读者」。这一处是同一族的第三例，而且更靠里：
**卡写了出处，没有任何东西去核对那个出处还在不在。**

`records/information/understanding/` 下 14 份卡（实点：`people/` 6、`topics/` 5、
`projects/` 3；两份 `README.md` 不算——`responsibilities/` 里只有一份 README，所以那个 scope
**一张卡都没有**），层自己的 `README.md` 把「可追溯到 raw 源」写成硬要求。而这一层在被读之前
是两半都不成立：`intake/workchain.py` 数的是**写了 `## Sources` 标题的卡数**——那是「把规则
写下来的文档数」，不是可追溯性；`grep -rn "message_position" --include=*.py` 在 fabric 树里
**零命中**。也就是说盘上 14 份卡、每份都带着位置或摘要，没有一行代码读过那些位置。

**`intake/understanding_delta.py`（新，约 870 行）+ `serverosctl work understanding`。**

它读每一份卡自己的引用，把**卡的引用到达处**与**源现在到达处**比。六件事各自有一条测试类，
因为它们各自都能安静地答错：

1. **两个水位不是一个。** 卡的一侧是位置、源的一侧是位置，**只有两边都是位置时才形成
   `lag`**。`sync.msg_count` 是计数：拿它去减一个引用位置，看起来对，但这里没有可减的东西。
2. **没有到达处的引用不是 current。** 14 份卡里有 7 份整份引用文件（没有 `message_position`），
   没有可比的量——`UNMEASURABLE`，不是那次没人做过的比较。
3. **已读区间内的改动。** 一条卡早就读过的位置上的消息之后被改了，`lag` 是 0，而卡是陈的。
   这是 `STALE_IN_PLACE`，与 `BEHIND` 分开；优先级 `BEHIND` > `STALE_IN_PLACE` > `CURRENT`
   是写出来的，不是涌现的。
4. **它不发出任何 claim。** 它不打开消息正文，所以输出里的字段叫 `review_range`（要重读的
   区间），没有 `claim` 这个键。这条判据查的是**报告的键**而不是报告的文本：子串版会命中
   `UNTRACEABLE` 理由里那句「the card claims traceability」——与 §5.0 同一个族，
   **闸门能看见字面量，看不见语境**。
5. **`*` 不跨 `/`。** `people/*/messages.jsonl` 是「每个人的会话」，把它展开成每个人下面每一份
   链接文档，会加进卡没有引用过的行。
6. **读者不持时钟、不写入。** 这条用 `ast` 查，不用 grep——模块自己的文档字符串里就写着
   「there is no `import time`」，grep 版会败在承诺这条性质的那句话上。

**实测事实（14 份卡，全部经独立复算）**：`BEHIND` 3 · `UNMEASURABLE` 7 · `UNTRACEABLE` 4。
其中三件是这一层第一次被指出来的：

- `projects/litellm.md` 引用的一份权威工作文档，卡上写的是 `sha b9fe2535`，而 manifest 记的是
  `content_sha256 = f4927de5…`（把盘上那份 13390 字节的 `content.md` 单独哈希过），
  `prev_content_sha256 = 672cd067…`，**两个都不是**卡上那个前缀。
- `people/zhangzhengji.md` 落后 644 个位置，且**已读区间内还有 2 条被改过**——只看 `lag`
  会漏掉后半句。
- `topics/` 5 份里有 4 份（`hosting-platform`、`litellm-log-analysis`、`litellm-operations`、
  `methodology-log-analysis`）的 `Sources` 区域**一个 raw 路径都没引用**：它们声称可追溯，
  而没有任何可核对的东西；第五份 `workbuddy-deepseek-billing` 引了 3 个源且都落后。

**我写出来的三个错，都是「自信地答错」而不是崩溃**：

- **坐标系统串了。** `capture_root()` 已经含 `feishu/chats`，而索引的键从
  `information/raw` 起算，于是每个路径都 404，**14 份卡被安静地全部判成 `UNMEASURABLE`**。
  修前摘要读作 `UNMEASURABLE 9`，修后才是 `BEHIND 3 · UNMEASURABLE 7 · UNTRACEABLE 4`。
  一处 docstring 把这条写在函数上：一条 join 到更深的根上，「读起来像『这个源没有消息文件』，
  而不是像一个 bug」。
- **没有配上的引用会抛 `KeyError`。** `expand` 对非 glob 直接返回拼出来的键、不查索引，于是
  一条指向本机没采过的源的引用在调用处崩掉——既是错的答案也是错的形状：它本该是一条
  `UNRESOLVED` **测量**，而写那条测量的分支早就在那里。
- **`partial` 只数了一种没核对的引用。** 卡的一条引用指向本机没有的源、另一条指向当前源且
  两者位置一致，会报 `CURRENT` 且 `partial: false`——把「根本没读到」的那条洗成「已核对」。
  这是同一个洗白换了一扇更窄的门，是**写测试的时候发现的**，改在模块里：`unresolved` 单列
  一个计数，`partial` 两种都算，`why` 两种都点名。

**变异测试**：五条承重判据各切断一次（`BEHIND` 永不触发 / `partial` 忽略 `unresolved` /
位置不全也报 `DECIDABLE` / glob 跨 `/` / 未配上的路径返回拼出来的键），五次都被对应的测试抓住。
**第四次第一次没抓住，因为变异根本没应用**——我猜的锚点文本在文件里不存在，脚本断言失败而
pytest 那行照印「52 passed」。一个没应用的变异是一次**假绿**，与 §5.0 同族；按真锚点重跑，
抓住了。每次之后按 sha256 逐字节还原（`c62f3df260f93eb2…`），`__pycache__` 每次都清。

**接线是自动被门覆盖的，不是我再记一次。** 这个动词经 `_load_module_from` 从 `cli.py` 加载，
`tests/loader_graph.py::production_closure()` 因此**推出** `intake/understanding_delta.py`，
`test_build.py::test_the_artifact_answers_the_production_loader_closure` 自动覆盖它。

**没做完的是写入侧。** P3 设计里的「最小 incremental writer」仍然 `not read`：这个模块读、对账、
提议区间，**不写一个字**（`ast` 那条判据是它的证据）。所以 B2 的 CODE 仍是 `PARTIAL` ——
读的一侧 DONE，写的一侧 not read，两者都不许互相代表。

**这一节之后补上的两处，与本节同源**，记在这里以免被读成新事件：

- 读的一侧少了一个区间。一份**整份换版**的文档（卡上引的摘要与 manifest 记的都不符）原先
  只贡献 `STALE_IN_PLACE` 这个状态、**一个区间都不提**——一张被判定为陈的卡，没有可读的东西。
  现在补第三类区间 `WHOLE_DOCUMENT`：文档没有位置，所以区间带的是**做出这次比较的那两个摘要**，
  而不是编一个位置出来。`test_a_replaced_document_proposes_a_range_with_no_positions` 钉住它。
  实测渲染：`→ litellm.md  文档已换版  摘要 b9fe2535 → f4927de5ab12…`。
- **一条判据被自己的假绿救回。** 那次改动里我把一行 `for row in stale` 留在了第三条
  推导式末尾，于是它变成 `for row in digest_differs for row in stale`——一个笛卡尔积，
  `digest_differs` 为空时产出**零行**。而 `test_being_behind_beats_being_stale_in_place`
  **在这个坏状态下仍然是绿的**，因为这个 bug 只把同一个表达式里的摘要那一支清零。
  是新加的那条测试发现的。记下来是因为形状与 §9.7 的 M4 一样：绿不是因为对。

### 9.9 认识层的写入侧：一个只会提议、结构上无法自我确认的 patch

§9.7 关掉的是读的一侧。这一节是写的一侧：**`intake/understanding_patch.py` +
`serverosctl work understanding-patch`**。它把读者的区间变成人能据以行动的那份东西，而边界
就是整个文件的意义：

> **patch 是提议。它不是确认，这个模块也没有任何一条路能自己产生一个确认。**

三条规则是**结构性**的，不是推荐性的：

1. **确认是人工输入。** 确认状态从一份**只读**的 decisions 文件里读，这个模块没有它的写口
   （`ast` 那条判据逐函数扫写入调用，把写点集合钉成恰好 `{"write"}`）。没有 decisions 文件时
   每一项都是 `AWAITING`。判据两半：一半查「没有 decisions 时全是 AWAITING」，一半查
   「模块里不存在第二个写口」。
2. **确认只覆盖它当时被做出的那个区间。** 项的身份是
   `(卡片, 采集对象, 区间种类, from, to)`。源再动一次就产生一个**不同的**区间，旧决定不会
   跟着卡片走：项回到 `AWAITING`，旧决定进 `stale_decisions` 并写明「它不再是关于现在所提议
   的那件事的决定」。**报出来而不是丢掉**——一个安静地停止生效的确认，比一个从没做过的更糟。
3. **每一项都带着它据以计算的版本对。** 卡读到哪、源现在到哪：会话是位置，文档是两个摘要。
   没有这一对的提议，是读者无法核对的提议，而「这张卡落后 644 位」正是那种会被相信、
   不会被核对的句子。

设计文档管这个叫 **Claim patch**，这里实现成**带版本锚的 items**，并且**刻意不叫 `claim`**：
claim 读起来是对内容的断言，而这个模块——和它所组合的读者一样——**不打开任何内容**。
新写下的判据 `test_nothing_anywhere_in_the_patch_is_called_a_claim` 递归扫键，
`claim`/`user_confirmed`/`asserted` 一个都不许出现（`confirmation` 这个**状态**和
`confirmed` 这个**计数**不在禁止之列，理由写在测试里：前者是提议的状态，后者是人数出来的决定，
都不声称这个模块懂了什么）。**说新内容是什么意思的是语义层**（P7），它继承同一条规则：
它提议，人确认。

**实测（本机 records）**：14 份卡 → **15 条提议，全部 `AWAITING`**；
`BEHIND 3 · UNMEASURABLE 7 · UNTRACEABLE 4`；没有提议的 11 份卡**逐类计数并写明原因**
（`no_item`）——列出三条提议的 patch 不能被读成「这一层只有三张卡」。
`--write` 后 `--verify` → `ok`；重跑写 → 逐字节相同
（`92cad4b0c368553a6bcc5c6b67221fa53a9af5709638c4bd7be890c6211fba4a`）。

**它不写记录、不改卡片。** 唯一写的是调用者点名的那一份 patch，绝对路径否则拒（与
`global_board.py#render` 同一条约束），临时文件 + `os.replace` 原子落盘。两个写点登记进
`runtime/actuator-contract.yaml`（`os.unlink` 清理那一节 + `os.replace` 提交那一节），
`mutation_guard` 从 `FAILED` 回到 `CHECKED`。模块**没有 `main`**——`intake/` 下经 CLI 加载的
模块不留第二个入口，`test_capture_coverage.py` 里那条规则的原话是「no `main()` for a verb to
reach for instead」。

**变异测试：五条承重判据各切断一次，都被对应测试抓住，五次之后文件逐字节还原**
（`3a2e5ec4c6188bf6ed73e41d9cd7da19d3f9542d2bdddfae276dbe94bf21a4fa`）：

| # | 变异 | 抓住它的测试 |
| --- | --- | --- |
| M1 | 项默认 `CONFIRMED` | 8 条（含 live 那条） |
| M2 | 身份丢掉采集对象 | `test_the_identity_is_...`、`..._in_another_capture_is_not_it` |
| M3 | 失效决定被丢掉而不是报出 | 4 条（含 `..._becomes_current_supersedes_...`） |
| M4 | 无出处的键照样发出 | 两条 producer 判据 |
| M5 | 绝对路径闸门消失 | 两条相对路径判据 |

**M2 第一次是假绿，改的是测试不是实现。** 那条测试原先只断言「决定指向另一份采集时项仍是
`AWAITING`」。身份丢掉 manifest 之后，那份决定的键与**任何东西**都对不上，项当然还是
`AWAITING`，测试照绿——它分不清「正确地拒绝了」和「根本没能对上」。现在它断言**一对**：
两条只差采集对象的决定，一条被采用、一条被拒。M2 会让「被采用」那一半转红。
与 §9.7 的 M4 同一个形状，记下来。

**M5 在工作树里留下了一个文件，而那正是测试自己的问题。** M5 拆掉的是绝对路径闸门，而抓住
它的两条测试**都从仓库根跑**——于是「按吩咐去写」的变异体**把 `patch.json` 写进了检出**。
提交前 `git status` 发现的（`?? patch.json`，16:43，一条项，卡片 `people/x.md`：本轮夹具），
已删。两条测试改成在自己拥有的目录里跑（直接调用那条用 `monkeypatch.chdir(tmp_path)`，
CLI 那条给 `cli()` 加 `cwd=`），**坏状态下也只写进 pytest 会删掉的目录**。
一条「一旦失效就往检出里拉屎」的测试，恰在它测的性质坏掉时留下证据——下一个读者会看到
一个文件而找不到解释它的那次运行。改后重跑：同样五个变异、同样五处被抓、逐字节还原，
`git status` 无新增。

**一条实测下界被这一轮自己的改动踩过去了，改的是数字不是性质。**
`tests/test_verification_tool.py` 那条量的是「静态输入集只有测试文件自己」的测试文件数，
断言 `> len(suite) // 2`，并在失败信息里写明「52 of 96 …… 这条断言需要**重新测量**而不是放宽」。
新加的写入侧测试文件用的是本树 55 个盲文件同款的 `_load(name, relative)` 习惯，
于是 54/109 → 55/110，**恰好等于一半**，严格大于不再成立。处置：把断言改成下界
（`>=`）+ 把实测数字写进失败信息（55 of 110，2026-09-22），`scripts/verify.py` 里那两处
同样的陈旧数字（「52 of the 96」「91 of the 96」）一并重量为 55 of 110 / 85 of 110。
**没有动那条性质本身**（扫描器看不见 `_load` 这个习惯），也**没有**为了保住一个不等号，
把这个文件改成全树唯一一个不用本树习惯的测试文件。

**接线同上，是自动被门覆盖的**：经 `_load_module_from` 加载，`loader_graph` 推出它，
`test_build.py` 的产物闭包判据自动覆盖（`tests/test_build.py` 43 passed）。

### 9.8 关系边：第三个谓词有了真生产者，另五个各写出「为什么没有」

P4 的上一版判定是「8 个谓词里只有 2 个有生产者，其余 6 个 `declared_only`」。那 6 个里
**只有一个在本机有真载体**：`attached_to`。其余五个不是「还差功夫」，是**盘上没有任何东西
声明过那条边**，逐条查过：

| 谓词 | 本机有没有载体 | 结论 |
| --- | --- | --- |
| `attached_to` | **有**：15 份链接采集的 `media`（配 `content.md`）、2 份会话采集的 `files.by_message`（配 `messages.jsonl`） | 本轮补上生产者，见下 |
| `supports` | 无。证据对象没有「我支持什么」这个字段 | 仍是 declared_only |
| `discussed_in` | 无。meetings 没有生产者（§9 的 P3 行） | 仍是 declared_only |
| `published_as` / `verified_by` | 无。各需要一个盘上没有的登记册 | 仍是 declared_only |
| `canonical_for` | 无。要「哪一份是权威」的声明——**这恰是可重建索引最不该自己断言的东西** | 仍是 declared_only |

`relations/project.py:289` `project_attachments()`，`_CAPTURE_MANIFEST_GLOB`（`:255`）；
`PRODUCED`（`:80`）加上 `attached_to`，`relations/schema.yaml:29` 跟着把它从
`declared_only` 移到 `implemented` —— 这两处的相等由 `test_the_documented_split_matches_the_code`
断言，所以不是两处各写一遍。

**两种采集形态没有合并，因为差别是真的**：文档采集的 media 属于那份文档，会话采集的附件
属于**带它来的那条消息**。都写成 `X attached_to <这片采集>` 会把「读者问的是哪条边」这件事
抹掉，而且抹掉之后更难看出来了——一个谓词、两种 `to`。

**实测（本机 records，239 行 / 19 条报出）**：

- 链接形态：`records://…/links/<slug>/media/<name>` **attached_to**
  `records://…/links/<slug>/content.md`。
- 会话形态：`records://…/files/<name>` **attached_to** `records://…/messages.jsonl#<om_id>`。
- 每行的 `derived_from` 是那份 `manifest.json`——关系写在哪，出处就是哪。

19 条**没有被写成行**、也没有被丢掉，而是从 `skipped` 报出来，因为 `attached_to` 正是
「听起来总是对」的那类断言（「这张图属于这份文档」通常确实成立）：

- **声明了但不在本机**（5 条）：`docx-1-fjbigmodel-guide` 的 `.pdf`、
  `wiki-workbuddy-config` 的 `.dmg`/`.exe`、`codex-config-ccswitch` 的 `.msi`/`.dmg`。
  **逐个 stat 过，也按前缀找过别的扩展名**——确不在本机。规律是**安装包/大二进制**被声明而
  没有落地，图片都落地了。这正是「声明 vs 落地」的分裂，采集覆盖（§9.6）问的是另一半。
- **落地了但没有消息认领**（10 条）：`zhangzhengji` 的 7 条（1 个 `.mp4` + 6 张图）、
  `zhou-kit` 的 3 条。备份回的附件与 `by_message` 的差额，与上一轮手数的一致。
- **只有 key、没有路径也没有消息**（3 条）：`bruce-xu` 的 `image_keys`——三张图都在盘上，
  但没有任何记录说它们是谁带的。
- **一句散文**（1 条）：`zip_expanded_into` 的值是
  `"reference/litellm-methodology-v8 (git repo: litellm-log-analysis)"`——**路径与句子在同一个
  字段里**，而那个目录不存在。原样报出，不解析；`files.pending`（`"3 张图片未下载"`）**读都不读**，
  它是散文，残缺采集是 `capture_coverage` 的问题。

**「一条缺失不许被两条路各数一次」有专门判据**：`by_message` 认领过的名字进了 `attributed`，
即使文件不在盘上也算认领过——否则一份丢失的附件会被报成「没有消息带它」，一个缺失变成两个。
`test_a_file_lost_after_attribution_is_reported_once` 钉住它。

**越界即拒**。manifest 是本进程**没有写过**的文件，所以 `path`/`media` 名一律按 `_inside()`
限制在原采集目录内：绝对路径、`\\`、`../../` 逃逸都返回 `None` 并报出，不写行。判据直接
打在 helper 上（`test_the_helper_refuses_every_shape_it_cannot_confine`），不靠某个上层测试
顺带覆盖。

**测试从 28 条涨到 50 条**（`tests/test_relations.py`，50 passed）。新增 22 条里，
一条是**这一轮唯一改动的旧断言**：`test_a_relation_with_no_producer_is_absent_not_invented`
原先断言 `PRODUCED == 观测到的谓词集合`——那是「卡片夹具恰好能产出 PRODUCED 全集」的巧合，
加了第二个读者就不成立了，改成 `观测 ⊆ PRODUCED < PREDICATES`。

**另一个改动是把 `RealRecordsTest` 从「静默跳过」变成真的在跑。** 它的
`RECORDS_CANDIDATES` 里只有北京路径，所以在本机一直是 skip —— 一个从没跑过的「实测」测试。
加上 `/data/home/vince/workspace/records`（第 3 个候选，北京两条仍在前面）之后，4 条实测全跑：
239 行、两种形态都在、`skipped` 非空。同时那条断言本身也修了：`skipped == []` 混了两种发现——
卡片的引用类型不出来是**记录的缺陷**（必须为零），附件的另一头没被记录是**采集的测量**
（本来就不为零）。现在按条目前缀（`projects/` vs `information/raw/`）分开，并在测试里写明
为什么分开。

**没有动 CLI，也没有新开动词。** `relations/project.py` 仍是脚本，用法就是 P4 行里写的那条；
`runtime/actuator-contract.yaml` 那两条登记的是 `write()` 的 `os.unlink` / `os.replace`，
本轮没有新增任何写入点，所以契约不改。

### 9.10 follow 清单有了面：第四处孤岛关闭，且它揭穿了两个「两笔账」的陷阱

**缺口是上一版自己在 §9 的 P2 表里写下的。** 本轮改那一行时，我在格子里写「watchlist、
`.drift-sync-state.json` 与各 link manifest 的 `last_verified_at` 在 fabric 树里零命中」——
提交前重量，**那句话有三处错**，三处都是「把一个还不存在的东西说成不存在」的反面：
听起来更安全的那个方向，但一样是假的。改法与重量如下。

**(a) `last_verified_at` 有读者，而且还不少。** `intake/capture_coverage.py:436` 逐 manifest 读它，
报 `RECORDED`/`NOT_RECORDED` + 时间；§9.6 那一轮就是为它写的。零命中的只有
`DRIFT_WATCHLIST` 与 `drift-sync-state` 两个串。**缺的不是时间戳的读者，是 watchlist 与 manifest
的 join**——一个文档可以一份采集都没有、有两份、或者 manifest 根本解析不了，而这三种在
manifest 的遍历里**全都不可见，因为缺的正是 manifest 本身**。

**(b) `drift_sync.py:68` 是 `needs_fetch`，`load_watchlist` 在 `:71`。** 行号只在指定 revision 时
才有意义，所以两个数字都得按当前工作树重量，改的是 `:71`。

**(c) 那两份脱敏副本是「陈旧快照」而不是「竞争实现」，而且风险要写出来。** 盘上现在
**3 份、3 个不同哈希**（上一版记的 5 份里，`reconcile-20260922` 与 `clean-line-20260920`
两个工作树已退役）：canonical 327 行 `a1c1a4d00fd798eb`（含 `ec500a4` 的 12 条规则，**含口令**）、
`worktrees/records/SRV-0003/…` 75 行（2026-09-16）、`worktrees/records/MKT-0001/…` 90 行（2026-09-20）。
两个工作树里的采集脚本 import 的是**本地那份**，跑起来用的是没有口令规则的版本。
**不动它们**——不属于本轮授权范围，且是别人工作树里的在制品。这一条从「记着」升级成
「有风险且有位置」。

**新增 `intake/watch_coverage.py` + `serverosctl information watched`。** 读
`DRIFT_WATCHLIST.txt`（`DRIFT_WATCHLIST` 环境变量可覆写，与采集器同一条契约），按
`drift_sync.locate()` **自己的规则**（`<chats>/*/*/*/links/<link_id>/manifest.json`，
按父目录名匹配）反查，逐条给状态：`RESOLVED` / `NO_CAPTURE` / `AMBIGUOUS` / `UNREADABLE`。

**这个面存在的理由，是 `AMBIGUOUS` 那一类。** `locate()` 零命中返回 None，**两命中同样返回
None**，调用方打日志然后 `continue`——于是「一个 link_id 有两份采集」和「一条都没采到」
在采集器里是同一个副作用：**盯着的东西静默地死了**。读者在这里把它拆开，并且**拒绝**从
两个命中里挑一个——挑一个就把「这个 watch 死了」这份发现藏起来，而那正是这个模块存在的理由。

**三处「看起来是同一个数、其实是两笔账」的拒绝，每一处都在 live 树上取到了证据：**

- **去抖不是复核。** `.drift-sync-state.json` 是采集器的 **commit 去抖**：`drift_sync.py:246`
  写的是「无论 commit 是否成功都重置计时」。live 树上两个值**同时存在且不同**——
  state `2026-09-15T00:05:53`，manifest `last_verified_at 2026-09-16`。这是两笔账，
  相减就会**凭空造出一个矛盾**。报告里两者分别命名、各自成块，**永不互推**。
- **缺失的 watchlist 不是别人写好的清单。** `load_watchlist()` 在文件缺失时回退到内置的
  `["wiki-litellm-work-doc"]`。原样打印等于在「清单没了」的那一刻说「跟了一篇」——
  所以缺文件报 `COLLECTOR_FALLBACK`，把那份内置默认**指明是采集器的默认**。
- **读不到的行不能丢。** 采集器跳过空行与 `#` 注释、**其余照收**；一行不可能是 link_id 的
  东西它也会拿去查。读者把它计数并列出：丢掉会让 follow 清单**读起来比实际短**。

**逾期要人给阈值。** `--before YYYY-MM-DD` 是作者的输入，不给就报「未问」。
这个 verb **不读时钟**，且这一条有 AST 判据（不许 `datetime`/`time`/`calendar` 导入、
不许 `now`/`today`/`utcnow` 调用），因为时钟读取只有一行、而且**加进去看起来像功能**。
比较按 **ISO 前缀的那一天**（`2026-09-16T15:45:58` 与 `2026-09-16` 在「哪一天复核的」
这个问题上是同一天）。

**live 层第一次跑出来的东西。** 一条 watch（`wiki-litellm-work-doc`，`AUTHORED`），`RESOLVED`，
复核 `RECORDED 2026-09-16`，**漂移 `DRIFTED`**（`content_sha256 f4927de5…` ≠
`prev_content_sha256 672cd067…`），去抖 `RECORDED 2026-09-15T00:05:53`。那个 `f4927de5…`
正是 §9.7 里 `litellm.md` 那张卡引用的摘要——**这份被盯着反复更新的文档确实换过版，
而在此之前没有任何面把「盯着的东西已经变了」说出来**。

**五处变异…六处，每处都被抓到，文件逐字节还原**
（`sha256 6c95c4c20f6466166873967b0e8b05cd2a8f56593878131c3f10c9d6493fe93a`）：
M1 冲突解成命中、M2 回退标成 `AUTHORED`、M3 逾期判反、M4 读不到的行丢掉、M5
解析不了的 manifest 当 `RESOLVED`、M6 去抖表整个不读。脚本在
`artifacts/skills/serveros/RUN-20260922-raw-coverage/watch-coverage/run.sh`，
每条**写前**断言锚点存在且唯一、**写后**清 `__pycache__`、还原后校验 sha256。

**`no_unsourced` 当场抓到了我自己。** 第一次跑 live：`the row for 'wiki-litellm-work-doc'
carries keys this module has no producer for: ['why']`——`why` 忘了进 `WATCH_PRODUCED` 表。
这就是那个闸门该有的样子：**加一个字段却被生产者表忘掉，是拒绝，不是多出一列**。

**接线同上，是自动被门覆盖的**：经 `_load_module_from` 加载，`loader_graph` 推出它，
`test_build.py` 的产物闭包判据自动覆盖。这一轮**没有任何写入点**（读者只读），
所以 `runtime/actuator-contract.yaml` 不改，`mutation_guard.py` `findings: []`。

### 9.11 standing delegation：一个能凭空铸造授权的洞被结构性关掉

**先分清一件事：这一轮不是 §9.5–§9.10 那种「孤岛接线」。** 那五处是**已有代码没有消费者**，
缺的是名字；这一处是**消费者一直在，而它被喂的东西可以凭空捏造**——`serverosctl work focus`
从未缺过入口，缺的是那个入口背后的东西有出处。所以它不并进 §9 开头那张孤岛表。

**这一轮修的不是缺口，是一个洞，而且上一版台账里那句描述缺口的字是错的。**
P6 那一格原文写着「文件自己写着『本机没有 grant store』，`--grants` 未绑定」。
两半都不对：

- **`--grants` 一直绑着。** `serverosctl work focus --grants <file>` 收一个 JSON 对象，
  交给 `intake/daily_cycle.select_focus`。而那个模块的 `grant_reason()` 在
  `intake/daily_cycle.py:51` 自己写着：**「Validate scope/time/budget, not the
  authenticity of the supplied grant.」** 计划 P6 的原话是「选择/执行 envelope 必须
  源自可信已有委托」——一个文件参数**按定义**做不到这件事。所以这一格的正确读法是
  「绑在一个洞上」，不是「没绑定」。
- **「本机没有 grant store」当时是真的**，写那句话的 `intake/focus_source.py:91`
  逐字写着「there is no grant store under `state/serveros/`」。那句话在写下时是事实，
  它也正是**每一个候选都被判 `MISSING_OR_REVOKED_GRANT`** 的原因。

**闭合方式是结构性的，不是把校验加严。** `--grants` 被**删除**；信封改由
`context/delegation.py` 从 `runtime/agent_control_store.py` 已记录的授权推导。
于是「授权必须来自可信已有委托」不再是一条被期待的纪律，而是**没有别的入口**：
CLI 上不再存在任何一条能把调用者手里的 JSON 变成信封的路径。

**承重的那半句在 `context/delegation.py` 的 docstring 第一段，也是唯一一条别处不查的判据：**

```text
a grant is a scope on a trusted context, and it authorizes a cycle only
while that context still IS one — whatever window the grant carries.
```

后半句是全部要点。授权自己带的 `expires_at`（测试里写的是一个月后）**不能**越过其委托
上下文的 FRESH 窗口（`context/actor-contract.yaml`：`window_seconds: 900`、
`stale_after_seconds: 300`）。判定直接调 `context/actor.py#authorizes`，
**理由复制、从不转述**——这个判断只有一个模块做，第二种措辞就是第二个意见。

**五个状态，四个是拒绝，分开写是因为它们是关于不同东西的不同事实：**

| 状态 | 事实 | 下一个动作 |
| --- | --- | --- |
| `AUTHORIZED` | 根在，且现在授权 | 进信封 |
| `DELEGATION_UNRESOLVED` | 存储里**没有** `delegated_by` 这个上下文——窗口关了被 `sweep` 掉了，或者记录被人手改过 | 重新上报上下文，**不是**重新授权 |
| `DELEGATION_DOES_NOT_AUTHORIZE` | 根在且拒绝（STALE/EXPIRED/未 VERIFIED/非 FACT） | 刷新上下文 |
| `GRANT_MALFORMED` | 记录无法判断：没写 `delegated_by`，或它自己两个 ref 名字打架 | 修记录 |
| `GRANT_NOT_RECORDED` | **关于板，不关于存储**：某行点了一个 ref，存储从没听说过它 | 板的生产者缺一个卡字段 |

塌成一个「没有授权」是 §5.0 那条失败族：问「这一轮为什么什么都没带」的人，会在
「没人委托过」「授权被撤了」「证据过期了」三种答案上收到同一句话——而这三个的下一步
是**三个不同的动作**。`GRANT_NOT_RECORDED` 单独存在也是同一理由：
「板点不出一个授权」和「存储里没有授权」是两个缺口、两个生产者，不许印成一句。

**写入侧的三条拒绝，每条都写明为什么不放在读的一侧。** `put_grant`：

- **没有 `grant_ref` 拒绝**——没有名字的授权无法被撤销。
- **没有 `delegated_by` 拒绝**——没有根的授权就是没根的授权。
- **根的检查在 `_mutation()` 锁内、对盘上的文档做**——不在实例载入的那份副本上做。
  对着副本查会让「这笔授权落不落盘」取决于**谁先写**（M8 就是这个变异，被抓到）。
- **拒绝只针对根缺失**（时钟修不好的那种），**不针对陈旧**。一条派生自已变陈旧上下文
  的授权会**被存下来并报成未授权**——「存储的记录不会变得无效，它只会变旧，而拥有时钟的是读者」。
  durability ≠ authority，这两件事在这一层被分开。

**写入是一次性的、幂等的、撤销不可逆的。** 同一个 `grant_ref` 的**相同**重报返回已记录
的那份（不是错误）；**不同**则拒绝，且没有替换接口。撤销幂等，但**保留第一次的时间**——
第二次撤销不许把「权力何时结束」挪到「某人最后一次提到它」的时刻（M11）。

**这里当场抓到过一个真缺陷。** 第一次冒烟：**相等的重报被拒**，因为 `staged` 里带着
`revoked: False`，而 `theirs` 排除了 `REVOCATION_FIELDS`，两边永远不可能相等。修法是
让 `staged` 不含撤销字段、存储时再构造 `recorded = {**staged, "revoked": False}`——
**比较的两侧都不带撤销字段**，因为撤销是存储写的，不是上报者写的。

**撤销与窗口不归这个模块判。** 信封 = 推导；撤销与窗口 = `intake/daily_cycle.grant_reason`。
所以一条**已撤销的授权在信封里**，而它被拒绝的理由来自另一个模块——这条划分有专门的
判据固定住（`test_a_revoked_grant_is_in_the_envelope_and_grant_reason_excludes_it`，
外加一条正向对照 `test_the_same_row_is_selected_when_the_delegation_is_live`）。

**没有编造耦合。** 授权**不**要求 `approval_ref`：`runtime/agent-session-contract.yaml`
里 `human_gated` 只有 `[session.interrupt, session.stop, session.handoff]`，要求它会让
授权**无法铸造**，机制就靠构造永远为空。

**live 层第一次跑出来的东西，而且它诚实地什么也没有。**
存储路径 `/data/home/vince/state/serveros/runtime/agent-control/store.json`，
`serverosctl work grants` → **记录 0 条，授权中 0 条，上下文 0 个**；
`serverosctl work focus` → **17 行，0 条委派，0 条命名**，板是 `STALE_BOARD`
（投影来自 2026-09-10，是**先前就有的**现场条件，不是本轮造成的）。
两个缺口分开印：空存储是「没人做出过委托」，没有 `grant_ref` 的行是「没人构建过那个卡字段」。
**`intake/focus_source.py` 的三处缺席格按本轮事实重写**——
`NOT_PRODUCED` 的 `scope_digest` 与 `grant_ref` 两格、`NOT_PRODUCED_ELSEWHERE` 的
`grants` 一格。三格都写着「本机没有 grant store」，**那句话现在不成立了**：
存储落了地（`put_grant` 收 `scope_digests`），所以缺的生产者被改写成**卡片字段**。

**十二处变异，每处都被抓到，两个文件逐字节还原。**
`context/delegation.py 1091aae6…`、`runtime/agent_control_store.py 13b0bf31…`；
脚本与 `RESULTS.md` 在 `artifacts/skills/serveros/RUN-20260922-raw-coverage/delegation/`。
捕获数 M1 5 · M2 3 · M3 1 · M4 3 · M5 1 · M6 2 · M7 2 · M8 1 · M9 3 · M10 2 · M11 1 · M12 1。
**五处只被一条测试抓到（M3/M5/M8/M11/M12），这是刻意的形状不是薄弱点**：每一处都是
打断某一句特定句子的唯一方式，而那条以该句命名的测试是唯一站得能观察到它的位置——
逐条读过日志确认过，没有靠推断。

**这一轮把 `grep -Fc` 从变异脚本里换掉了。** 十二个锚点里有**两个跨行**，而 `grep -Fc`
把模式里的换行读成**两个模式**、数的是**匹配的行数**。这次它错在安全的一侧，但工具是错的：
改成 Python 里 `text.count(old) == 1` 精确子串计数，写前断言（缺或不唯一即 exit 91）。

**`mutation_guard.py` `CHECKED`，`findings: []`，declared 37 / undeclared 0 / stale 0。**
`runtime/actuator-contract.yaml` **不需要改**，而这是预期的结果而不是运气：
`put_grant`/`revoke_grant` 落盘只经过 `_flush` → `_atomic_write`，
本轮**没有新增任何** `os.unlink`/`os.replace` 位置。真要是有一条写路径直接摸了
`os.replace`，这里会现出一条声明差——这个检查存在的理由就是这个。

**顺带修正一处同族措辞**（上面那三格）：缺的生产者现在被指名成**卡片字段**，
不再是「存储不存在」。把「生产者还没建」说成「东西不存在」，和本台账 §9.10 里被抓到的
那三处是同一个方向上的假话——只是这次是「把还不存在的东西说成不存在」的反面用在了
已经不存在的前提上。

### 9.12 一条「惩罚改进」的地板，和一处 `plan` 自己已经撤回的数字

**§9.11 的测试文件把 §5.0 那条基线从 8 红推到 9 红，而这一条不是跨仓库红。**
新文件 `tests/test_delegation.py` 进套件后，
`tests/test_verification_tool.py::TheStaticGraphIsALowerBound::test_the_static_input_set_is_the_test_file_alone_for_half_the_suite`
失败：`only 55 of 112 test files have no statically visible inputs`。

**先把机制查清，再决定改哪边。** `tests/loader_graph.py` 的 `module_refs()` 认两种边：
`LOADER_NAMES` 调用里的**字符串字面量**参数，以及**任意** `/` 拼接的字面量链
（`REPO_ROOT / "runtime" / "cli.py"`，只要拼出来的文件存在）。新测试文件把四个路径写成
模块级常量（`CLI`、`MODULE`、`STORE_MODULE`、`DAILY_CYCLE`），第二种形式于是全都命中——
`runtime/cli.py` 的闭包**就是**大半棵树。**这个文件不是盲的，它是可见的。**

**所以 55 没动，112 动了。** 断言的形状是 `len(blind) >= len(suite) // 2`：**每一个
扫描器能看见的测试文件都只给分母 +1、给分子 +0**。也就是说「加一个可见的测试文件」
——`plan` 变**好**的那个方向——会把这条判据往失败上推。它这次就是被这么推过去的。
它的 docstring 自己写着「The property is a FLOOR to trip, not a ratio to defend」，
而一个比值恰恰不是地板。

**改法：地板改成绝对数 `BLIND_FLOOR = 55`，判据从比值改成计数。**
转换五个盲文件为可见文件 → 50，失败；加五十个可见文件 → 仍然 55，通过。
**它现在只在图退化时绊倒，而那正是它在这里的唯一理由。**前两次「落地板」是同族的
另一种情形（B2 writer 的测试文件**以盲的身份**加入），本轮的第三次是**可见**的——
两次都不是图退化，所以两次都不该能绊倒一条地板。

**重测数字（本工作树，2026-09-22）：套件 112，其中 87 个定义 `_load` helper，55 个盲。**
三处写着旧数字的地方一起改：`scripts/verify.py` 的模块 docstring（`85 of the 110` →
`87 of the 112`、`55 of the 110` → `55 of the 112`）、`static_inputs()` 的 docstring
（`55 of the 110` → `55 of the 112`）。

**顺带抓到一处「自己已经撤回的数字还在往外印」。** `plan` 不带记录时的 `limits` 文本
逐字写着「defined in **91 of the 96** test files … for **52** of them」——而这**正是**
`scripts/verify.py` 自己的模块 docstring 已经标注成 `were` 的那对旧数字
（现行的是 85 of 110，见上一段）。**操作员读到的是一对被撤回的测量**。两处都改成
重测后的 87 of 112 / 55，判据同步钉住。这与 §9.10 是同一族：**印出来的数字必须重测**。

**这一处不是本轮新增的缺陷**，是上一轮重测时改了 docstring 而漏了输出串。
记在这里是因为**它是被这一轮的失败顺带翻出来的**，而不是因为本轮造成的。

**新的地板自己也过了一次判据，因为「对着常量做单边断言」正是会长成同义反复的形状。**
`floor-is-live.sh`（同目录，L1/L2 两次，写前 Python 精确锚点计数、写后清 `__pycache__`、
还原按 sha256 校验）：
`BLIND_FLOOR = 56` → **失败**（`55 not greater than or equal to 56`，说明地板是活的）；
`BLIND_FLOOR = 0` → **通过**（说明一条零地板会放过一切，也就是「不是判据」从里面看长什么样）。
两次还原逐字节相同（`1b2fbbc4556ab1d6e9bcf169dbc68d5451a475188629f46e04f3cc61a6ab1ec6`）。

**终态重量：8 failed, 4521 passed, 20 skipped, 3718 subtests passed（485.44s），
8 条全部是已定案的跨仓红，无新增。**

### 9.13 原子准入：一个事务，两条可恢复幂等关系，和一处不可达的句子

**这一格上一版逐字写着「partial，本轮之后仍是 partial」，理由是「选择、预算、租约、
派发今天仍是四次分别落盘，**没有**共同提交点」。那句话在写下时是对的，§9.11 也确实
没有动它——§9.11 补的是**授权的出处**，不是提交点。本轮补的就是提交点。**

计划 P6 那两句话是两件事，实现也拆成两件事：

```text
原子准入把相同 cycle/目标/版本的选择、预算预留、lease/dispatch intent 放到
现有权威事务/互斥范围；多系统间用可恢复幂等关系，不假装跨库事务。
```

**前半句是一个事务，落在 `runtime/agent_control_store.AgentControlStore#admit`。**
选择、预算预留、lease intent、dispatch intent 四样在**同一次 `_mutation()` 里**写入，
共用那个 store 已有的 sidecar 锁。没有新的锁，没有新的 store——「现有权威事务/互斥范围」
是字面执行的：`admit` 走 `_flush` → `_atomic_write`，和第五个集合 `grants` 用的是同一条路。
策略那半是纯函数 `intake/daily_cycle.admission_plan`，无 IO，返回记录或
`{"refused": <词>, "because": <一句话>}`；**拒绝是返回而不是抛出**，因为调用方本来就该
预期有拒绝——本机大多数日子没有任何 grant——而那是答案不是故障。这与
`work focus` 的 `BLOCKED STALE_BOARD` 是同一种形状，CLI 上也同样是 exit 0。

**后半句是两条可恢复幂等关系，不是分布式事务，而且这一点是被结构保证的。**
`runtime/lease_store` 和 receipts 目录都不在 `admit` 的事务里。两相各自**读回真实的那个
store**：`LEASE_HELD` 读 `lease_store` 的引用，`DISPATCHED` 读 `bounded_invocation`
写下的 receipt 文件。admission 只记录**对方回答了什么**，从不假装自己知道。所以
`admit` 之后崩溃会留下一条 `INTENT_RECORDED` 的 admission 并占着 action key——
**这是设计，不是缺口**，`work admissions` 列得出来，`work retire` 放得掉。一个按定时器
释放 key 的清扫器**正是**这份设计拒绝的那个跨库事务。

**「Idempotent on the INTENT, not on the cycle」是 `admit` docstring 里的承重句，
也是 `_intent_digest` 不含 `admitted_at` 的理由。** 同一 intent 重放 → 返回原记录；
不同 intent 撞同一个 cycle → 拒绝。**两个并发调度器因此安全：输的那个被告知它输了，
而不是两个都跑。** M1（把时刻塞进 digest）被两条测试当场抓住，M2（action key 不再独占）
和 M3（退休后仍算持有）各被一条抓住，见下。

**本轮最值得记的是一条不可达的死代码，而它是被新写的测试暴露的。**
`runtime/cli.py#_lease_answer` 原来写的是 `reference.get("retired_at")` 分支，消息在描述
「引用被释放后确认」这个状态。**那个状态到达不了**：`runtime/lease_store.release` 先设
`retired_at`，**再把所有已退休的行从 `references` 里删掉**才落盘——所以从这个列表里读回来
的引用**永远不带 `retired_at`**。一句关于不可能状态的说明，读起来和一句关于真实状态的
说明一模一样。改法是去 `history` 里找 `act == "RELEASE"` 且 scope/holder 相符的条目，
把「从未声明」和「声明后交还」分成**两句不同的话**（一个是找错了持有人，另一个是这个
cycle 在认领一份本节点已经记账为释放的租约）。测试随之改成**断言句子**，并另加一个
「另一个持有人声明过」的反例，确保两句话没有被合成一句。

**同一族的三处小修，都是「顺序即消息的顺序」：**
`cmd_work_confirm` 原来先查 admission 再查 phase，无记录时给出的是错误的那句话——检查
顺序被倒过来，并就地注释了理由。`--phase` **故意不设 `choices=`**：argparse 会在到达我的
句子之前就拒掉 `INTENT_RECORDED`，而 argparse 的消息不解释原因。
`test_work_admit_takes_no_grant` 用正则取选项串而不是子串匹配，**因为我自己的 `--cycle`
帮助文本里就写着字面量 `--grant`**（「There is deliberately no --grant」）——一条被自己的
说明文字搞红的测试。

**`work admit` 故意没有 `--grant`。** 这是 §9.11 那个洞在 CLI 面上的锁：授权不能从一个
文件参数里铸造，`admit` 只从**已经记录的** decision 和 envelope 里读。`_dispatch_answer`
也故意没有默认的 receipts 根，并在 docstring 里写明边界：「this verb records what the
receipts directory ANSWERS, and a caller who fabricates that file is fabricating evidence
in the place evidence lives」。

**活节点实测：拒绝不写盘。** `work focus` 仍是 17 行 / 0 delegations / 0 named /
`BLOCKED STALE_BOARD`；`work admit` 因此 `refused NOTHING_SELECTED`，exit 0，而活 control
store 的 sha256 **前后逐字节一致**（`dcce252f…45a13`），mtime 未变。

**变异证据：三个文件、十七个变异体、17/17 被拒。**
`artifacts/skills/serveros/RUN-20260922-raw-coverage/admission/`（`run.sh`、`mutation-run.log`、
`RESULTS.md`、十七个 `mutant-M*.py` 与三个 `pristine` 快照）。攻击的是信任边界不是算术：
M2/M3 是并发主张，M7/M8/M15/M16/M17 是「读一个自己不拥有的 store」的五个位置，
M5/M6 是「已记录」可能悄悄变成「又记录一次」的两个位置，M9/M12 是「有界」可能变成
「无界」的两个位置，M13 是本机今天真的会撞上的那一个。三条纪律照旧：**写前**在 Python 里
断言锚点存在且唯一（`grep -Fc` 会把模式里的换行当两个模式、数的是匹配行数）、
每次写入与还原清 `__pycache__`、还原按 ARTIFACTS_ROOT 下的快照逐字节 sha256 校验
（不是 `git checkout --`——这棵树带着本轮未提交的改动——也不是 /tmp）。

**这一轮里，锚点检查抓到的是我自己，两次。** M12 的锚点**出现两次**，因为
`grant_reason` 与 `admission_plan` 逐字持有同一个比较（`if type(budget.get(key)) is not int
or budget[key] <= 0:`）——这是有意的，第二处是同一个拒绝高一层，但它让单行锚点歧义，
exit 91。M17 的锚点**出现零次**：我写了 19 个空格的缩进，文件里是 18 个。**一个基于
grep 的 harness 会报「0 处匹配，继续」，变异体从未写入而日志说它写了**——于是 M17 会
显示为「没抓住」，而我会得出「released/holder 的区分没被测到」这个错误结论。
两次都记在 `run.sh` 的注释里，`mutation-run-pass2.log` 是第二遍（M12 已修、M17 还坏着），
第一遍的日志被第二遍覆盖了，唯一独有的内容就是 M12 那次拒绝。

**同族第三次：量具自己把「没有」印成了「干净」。** `final-measure.sh` 第一次跑时，
邻域那一步点名 `tests/test_work_focus.py`，**本仓没有这个文件**；pytest 给一个不存在的
路径是 exit 4 且 **stdout 一个字都不打**，于是 `tail -3` 把它渲染成 `no tests ran in 0.00s`。
改正后重跑：**444 passed, 19 subtests passed**，脚本现在先断言每个路径存在。三次同形：
**一个字面量能看见、语境看不见的量具**，和 §5.0 那一族是同一条教训。

**`runtime/actuator-contract.yaml` 本轮不改，这是判据不是省事。** 契约按文件+符号声明每一个
`os.unlink`/`os.replace` 位置；本轮没有新增位置——`admit` 经由 `_flush` 走已有的
`_atomic_write`，新 CLI 动词是读者，`_dispatch_answer` 读 receipt 文件不写，
`_lease_answer` 经 `lease_store.open_store` 读（它的写位置在加 `release` 那一轮已声明）。
所以 guard 仍是 `CHECKED` / `findings: []` / declared **37** / undeclared 0 / stale 0，
**declared 与上一轮同数**——这一点正是判据：新增一个已声明位置会把这个数字挪走。

**明确没做的事，写在这里是因为这一格变绿不能把它盖住：**

- **没有预算池。** grant 的 budget 三项是**一次派发**的界，reservation 就按那样记录。
  一个 cycle 跑四次不会递减任何东西。`grant_reason` 读 `budget["exhausted"]`、
  `admission_plan` 拷那三个界，但**本仓没有任何写入者写 `exhausted`**——所以
  `BUDGET_UNAVAILABLE` 只对「有人录进那条字段的 grant」可达。消耗记账没实现，也没声称实现。
- **没有清扫器。** `admit` 与 `confirm` 之间崩溃会留下一条占着 action key 的
  `INTENT_RECORDED`，恢复是人工的（`work admissions` 列，`work retire` 放）。
- **`work admit` 没有任何调用者。** 日循环的渲染腿装好了、手跑过一次、**没有上弦**（§3.9），
  所以本机这四个动词可达而无人到达。

**终态重量：8 failed, 4605 passed, 20 skipped, 3718 subtests passed（502.45s）。
8 条全部是 `tests/test_architecture_check.py` 里那 8 条已定案的跨仓红，无新增。**
与 §9.12 的 `8 failed, 4521 passed` 相比是 **+84 passed、+0 failed**，而 84 正好是本轮新增
测试文件的大小——**这一轮加了测试，别的一个数都没动。**

### 9.14 第三种读取形状：文件读到了，一条条目也没认出来

**这一节不是从计划来的，是从一次与本台账绿色格子相矛盾的实测来的。** 把
`serverosctl support` 指向本机**第二个知识库**——`src/litellm-support/dist/employee-kb`，
25 文件、两级目录、2026-09-20 构建——三个动词逐条读作：

```text
$ serverosctl support entries --kb-root …/dist/employee-kb      → exit 0，列出零条目
$ serverosctl support check   --kb-root …/dist/employee-kb
  check 0 entries against 0 index rows, as of None
    no findings                                                  → exit 0
$ serverosctl support match   --kb-root …/dist/employee-kb --status-code 402 \
                              --message "budget exceeded"
  outcome: NO_ENTRY_MATCHES                                      → exit 2
```

**每个动词什么都没读到，并且报告成功。** 第三条最坏：它把锅算在问题上。`answer`
更进一步，对一个**它一条条目都没读过**的知识库提出了改进候选——一条可能被人归档的
候选条目。

**根因两句话。** `read_entries` 只在 **glob 一个文件都没匹配到**时才设
`unreadable`——所以 `unreadable` 是**关于 glob 的事实，不是关于读取的事实**；员工树
的 `*/*.md` 匹配到了一堆。然后 `check()` 的守卫要求
`reading["unreadable"] and index["unreadable"]`——**两侧都**不可读——于是一侧「可读但为
空」、另一侧缺失时守卫不触发，循环比较了两个空集合，`findings: []` 被渲染成 `CHECKED`。

**修法是第三个字段，不是把那个词放宽。** 两个事实分开报，一起消费：

```python
  * `unreadable` — glob 下面什么都没有。关于 GLOB 的事实，而 glob 可能瞄错了根。
  * `unrecognised` — 文件读了，没有一条产出条目。关于读取本身的事实。
```

读者需要区分「你指错了地方」和「我读了你的文件，这份契约描述不了它们」；守卫只需要知道
两者都不是一次读取。合成一个字段会丢掉前一种区分，而只读其中一个**就是**被修的那个缺陷。
`match`/`check`/`next_id`/`listing` 的守卫现在读 `unreadable or unrecognised`，**CLI 也
读两个**，`render` 说出是哪一种。

**为什么这一条自行实施而不上报。** 模块自己的 docstring 已经裁定过：「an unreadable
knowledge base produces NO findings — it produces `KB_UNREADABLE`, because
`findings: []` is what "checked and clean" and "never checked" both render as.」
并且已有两条测试为到达同一处的另外两个根断言了同一意图
（`test_an_unreadable_knowledge_base_reports_a_reading_and_not_clean`、
`test_a_root_with_no_entry_files_is_unreadable_not_empty`）。这是**同一个已被陈述过的
意图的第三例未覆盖情形**，所以是缺陷，不是产品语义变更。

**哪一个 KB 格式权威、契约要不要为员工树那种 YAML front-matter + 英文 `##` 标题再声明
一个 `heading_pattern`，这一条没做，也不自行决定。** 契约的注释记下实测并停在那里；工具
现在打印的那句话同时点名两种读法，并说明这个选择不归它。**上报，不落代码。**

**变异证据 10/10 被拒，零存活**（`support-kb/run.sh`、`mutation-run.log`、
`mutant-M*.log`；逐条见该目录 `RESULTS.md`）。三件事值得留在这里：

- **M1/M2 与 M9/M10 是同一个锚点向两个方向变异。** 守卫是一条边界，只从一侧测过的边界
  没有被测：M2 与 M10 正是分别能通过 M1、M9 那条测试的变异体。
- **M8 是本轮新代码被自己的测试抓住的**——那句话把正则按 `!r` 打印，每个反斜杠都被加倍，
  断言拿契约里的值一比就拒了它。
- **第一遍在 M2 处 abort，并把 M1 留在了 `support.py` 里。** 同一个锚点的两个变异体不可能
  都找到它：M1 改写的正是 M2 要写的那段文字，于是 M2 报 `0 occurrence(s)`——**而基于
  `grep` 的量具会记一句「继续」，把 M2 报成「没被抓住」**，那是对 M2 那条测试的错误结论。
  `mutate()` 现在先 `restore()`，每个变异体都从实测 revision 出发；陈旧文件在 `diff` 确认
  只剩 M1 那一行之后从 `support.pristine.py` 还原，第二遍才是整轮干净的一遍。

**`runtime/actuator-contract.yaml` 本轮不改。** 本轮没有新增 `os.unlink`/`os.replace`
位置：`read_entries` 只读，新字段是字典里的一个字符串，CLI 那处是一个分支。guard 仍是
declared **37** / undeclared 0 / stale 0 / tests_breach 0，227 文件——**和上两轮同数**，
而「新增一个已声明位置会挪走这个数字」正是这个数字算证据的原因。

**明确没做的事：** 「哪种 KB 格式权威」没有决定（见上）；`support-contract.yaml` 没有
声明第二种格式；本机的 `serverosctl` 是指向已安装 release revision 的符号链接，所以
**这个修复在已安装路径上还没生效**——它随 §14 的晋升一起到达，而晋升是推迟的。

**终态重量：8 failed, 4618 passed, 20 skipped, 3722 subtests passed（497.20s）。
8 条全是 `tests/test_architecture_check.py` 那 8 条已定案的跨仓红，无新增。**
与 §9.13 的 `8 failed, 4605 passed, 3718 subtests` 相比是 **+13 passed、+4 subtests、
+0 failed**，而 13 正好是本轮新增的测试数（`test_support.py` 9 条、
`test_serverosctl.py` 4 条）。活节点两个知识库的对照读数：员工树 `entries`/`check`
都是 **exit 2 并点名八个文件与没匹配上的 pattern**；契约写就的那棵树
**`21 entries` / `21 index rows` / exit 0，一个数字都没动。**

### 9.15 MeetingBundle 的生产者：内核可达而无人到达

**这一节补的是 §9.5 留下的那半句。** §9.5 把 `space/meeting_relevance.py` 接进了
`serverosctl space meeting`，那之后内核可达了——但只对**已经知道怎么写 bundle 的人**
可达：`--request` 要求有人手写 `bundle` 对象。仓库里**零个 bundle 生产者**，所以 P3 的
完成句「会议复用既有 Minutes/Bundle」在物理上没有路径。这和 §9.13 之前的 `work admit`
是同一个形状。

新增两个文件：`space/meeting-bundle-contract.yaml`（哪个集合是一个 unit、每个 sidecar
是什么、读数的封闭集）与 `space/meeting_bundle.py`（生产者），外加 `runtime/cli.py` 的
第二条入口 `--sidecars`。

**四项设计判据，逐条都有测试钉住：**

1. **四个成员是一个 unit，不是一张清单。** 机器策略的原话（`ability-management/
   instructions/machine-policy-invariant.md:32`，2026-09-22 实测）是「require minute,
   audio, transcript, and summary **as one unit**」，所以读数只有 `COMPLETE` 与
   `INCOMPLETE` 两种，**没有完成度百分比**——数字会招来「90% 也行」，而要求没有这个意思。
   契约把这句话连同出处引进来，不复制。
2. **五种 presence 状态是五个不同的事实。** `PROCESSING` 是等下去的理由，`ABSENT` 不是，
   `FAILED` 是别再等的理由，`UNREADABLE` 可能说明材料存在只是读不到。折成
   `missing` 会让「等转录」与「这场会议没有转录」读成同一句话，而计划自己的测试清单
   （缺音频 / processing）点名要这个区分。
3. **两个轴由内核声明，生产者只读不写。** `ownership`/`hold` 属于
   `meeting-vocabulary.json` 的 `dimensions`；契约的 `axes` 块只声明**读哪两个维度**，
   值在组装时从 vocabulary 取。声明里没说的那一个：**读数写 `UNDECLARED`，bundle 里
   直接省略该键**——内核自己对「没人说」的词是 `UNKNOWN`，由它从 `None` 补，生产者
   没有第二个词。
4. **`COMPLETE` 以外的读数不向内核提任何问题**（`bundle: None`）。递给内核一个缺音频的
   bundle 会产出一条关于不存在材料的 decision record，而 decision record 是别人拿去做
   事的凭据。

**这一轮找到的缺陷是自己跑出来的，不是看出来的。** 端到端第一次跑通时，四成员齐全、
`state: COMPLETE`，然后

```text
serverosctl: unknown ownership: 'UNDECLARED'; declared values are
             ['OTHER_OWNER', 'OWNED', 'SHARED', 'UNKNOWN']
exit 2
```

根因是 `assemble()` 里两行 `declaration.get(name) or "UNDECLARED"`——**生产者自己造了一个
词，塞进一个内核按封闭集校验的字段**。这与契约自己在 `member_kind` 注释里警告的形状
逐字相同：「this producer would then build bundles the kernel rejects for a reason it
could have caught」。修法不是把 `UNDECLARED` 加进 vocabulary，而是让省略成为答案。
`AxesTest` 把这条钉成测试，`TheBundleReachesTheKernelTest` 是它对应的集成判据：**别
的测试全绿而 bundle 内核不收，正是这一轮发生的事。**

**其余结构性判据：** `MIXED_SCOPE` 优先于 `INCOMPLETE`（两场会议不是一场缺件会议的
bundle，哪一场被意指不是读者的选择）；成员之间的 `meeting_ref` 不一致即拒；通配符规则
`_stable_id` 直接读 `kernel.WILDCARDS` 而不是抄一份（源码断言 `assertNotIn('WILDCARDS = ('）;
`bundle_ref` 从 `scope.meeting_ref` 派生，没有就拒——生成的 id 每次跑都变，内核的记录
两次读数就没法比。

**新出现的 CLI 交叉点被架构守卫拦下，这是它该做的。** `space/meeting_bundle.py`
进了 `KNOWN_CLI_CROSSINGS`（`test_core_ability_boundary.py`），因为 CLI 多了一条通往
fabric 的路就必须说清为什么在 CLI 而不是 daemon：daemon 没有 declaration，而
`--observed-at` 是必填参数，就是为了任何进程的时钟都不能代答。

**没做的事（明确）：** 本机**没有任何真实会议 manifest**，四个探针声明
（`complete` / `processing` / `mixed-scope` / `declared-axes`）全部是合成的；真实会议
材料的接入点、以及它从哪来，这一轮没有决定也没有接。`serverosctl` 仍是指向已安装
release 的符号链接，所以 `--sidecars` 在已安装路径上还不可用（`serverosctl space` 报
`invalid choice: 'space'`，实测），它随 §14 的晋升一起到达。

**变异证据：10 个变异体，10 个被拒，零存活。** `RUN-20260922-raw-coverage/meeting-bundle/`
（`run.sh`、`mutation-run.log`、`mutant-M*.log`）。两个文件的起止哈希逐字节相同
（`95ff536b…` / `304ac98a…`）。两点值得留：

- **量具自己抓住了作者（第五次）。** `verdict()` 第一版只数 `^FAILED`，而 M4 是被一个
  **subtest** 抓住的，于是记成「refused by 0 test(s)」——非零退出配零计数，正是
  RESULTS.md 里记的那一族。改成数 `SUBFAILED` 并把 pytest 的汇总行原样打出来。
- **M5 第一次真的存活了，而它暴露的是测试的洞，不是代码的洞。** 原来的
  `test_mixed_scope_is_read_before_completeness` 用四个成员全 PRESENT，两个分支交换
  顺序都会得到 `MIXED_SCOPE`，所以换序的变异体活得下去。补了
  `test_two_meetings_outranks_a_missing_member`（两场会议 **且** 缺 minute）之后 M5 被拒。
  同时把原测试里那句「a producer that checked completeness first would call this
  COMPLETE」的**错误论断改掉了**——换序的变异体测出来它不会。

**终态重量：8 failed, 4665 passed, 20 skipped, 3767 subtests passed（500.25s）。
8 条全部是 `tests/test_architecture_check.py` 里那 8 条已定案的跨仓红，无新增**
（单独跑该文件确认是同样 8 条）。与 §9.14 的 `8 failed, 4618 passed, 20 skipped,
3722 subtests` 相比是 **+47 passed、+45 subtests、+0 failed**，而 47 正好是
`tests/test_meeting_bundle.py` 的大小。`mutation_guard.py`：`CHECKED`、`findings: []`、
**declared 37 / undeclared 0 / stale 0 / tests_breach 0**，扫 229 文件（§9.14 是 227，
多出的两个正是本轮新增的文件，而 declared 一个没动——本轮没加任何 `os.unlink`/
`os.replace` 位点，**没动就是证据**）。

### 9.16 ability 侧终态：绿，但「绿」这个词在这里有两次不同的意思（2026-09-22 量）

§14 的链条里有一格是「ability 测试绿色」。它此前**没被量过**，而台账 §5.0 的表格里
还留着一条关于它的红（`test_materialization_contract.py::test_real_registry_matches_real_bundle`），
所以这一节先纠一条记录，再报两个数。

**那条红已经不存在了，不是因为它被修好，是因为那个测试名不存在了。**
`tests/test_materialization_contract.py` 本轮被改过，那个函数名已经不在文件里；
`pytest tests/test_materialization_contract.py::test_real_registry_matches_real_bundle` 报
`not found: … (no match in any of [<Module test_materialization_contract.py>])`。
按名字引用一条判据的台账会这样过期——**名字在动的时候，行号与函数名都只是指针**，
这一点本仓先前已记过一次（「行号引用只在指定 revision 时才有意义」），这里是同一族的第二次。

**它现在也从另一条路自己绿了**：该文件解析 serveros bundle 的顺序是
`SERVEROS_BUNDLE` → … → `worktrees/<repo>/*/plugin`（`test_materialization_contract.py:391-415`），
所以它读得到 §9.15 那棵工作树的 `plugin/profiles/testing.yaml`。
注意这与 serveros 那边 fixture 的「canonical 优先」是**相反**的顺序，而两边都写下了理由：
serveros 问的是「规则在哪」，ability 问的是「这个组合长什么样」。

| 跑法 | 结果 |
| --- | --- |
| `pytest tests/`（工作树，无环境变量） | **105 passed, 2 skipped** |
| 同上，`SERVEROS_BUNDLE=<serveros 工作树>/plugin` | **107 passed, 3 subtests passed**（0 skipped） |

那 2 条是 `tests/test_registry_records.py:345,351`，跳过理由是它自己印出来的：
`SERVEROS_BUNDLE 未设置: 本条需要 composition canonical, 跳过而不是静默通过`。
**这是本仓要的那种跳过**——它明说自己没跑，所以「跳过」不会被读成「通过」；
补上环境变量后它确实跑，并且确实过。

顺带量到、留给 §14 与 §16 的两件事：`src/serveros` 的 `plugin/profiles/` 里**只有
`linux-work.yaml`**（没有 `testing.yaml`），且相对自己的 `origin/main` 是 **2 ahead /
40 behind**（`HEAD=d0a94d6`，`origin/main=4d090d6`）。

那 2 个本地提交（`d0a94d6`、`e925fbf`，2026-09-21，都是 `space:` 开头的）**只在
`refs/heads/main` 上**。这一句是 fetch 之后才成立的：`git branch -r --contains` 只认得
本地的远端跟踪引用，所以先 `fetch origin --prune` 再读——刷新后两条仍是 `[]`，
`git for-each-ref --contains d0a94d6` 也只列出本地的 `refs/heads/main`，
且都不在已推送的 serveros 工作树 HEAD 里。**这是本机唯一的、未被任何远端持有的用户提交**，
别人对该检出做一次快进就会丢；本轮不碰它，但它属于 §16 要单列的那类外部权限项。

### 9.17 一句散文许下了一个不存在的闸门，而它许诺的方向正是操作者会信的方向

`plugin/profiles/linux-work.yaml` 的 `recommended:` 上方原写着：这份清单「deliberately
the registry, minus what this composition ships」，并且「Leaving a registry ability
unnamed here … 是被 A1 抓成 HIGH finding 的，所以新注册一个 ability 会逼出一个决定
（ship / recommend / 修 registry），而不是悄悄把缺口撑大」。

**两句话都是假的，而第二句假在要紧的那个方向**：它告诉操作者「注册就够了」。

**量。** 注册表在 `074a518` 上是 64 条，其中 41 个是 `type: skill` 的名字、15 条是
`type: plugin` 记录；每条 `provides[].id` 都能解析成一条 ability 条目（0 unresolved，
这正是检查器自己那条 `PROVIDES_UNRESOLVED` 为 0 的读数）。

**A1 实际比的是什么。** `judge_records()` 把每条 composition 配到**它自己那个 artifact
的记录**上，再 `judge([comp], registry_members=record["members"])`——所以被比的集合是
**那一条记录自己的 `provides` 成员**。在工作树上实测：

| | 数 |
| --- | --- |
| 被某条 plugin 记录的 `provides` 触达的名字 | 12 |
| 被某条 composition 声明的名字 | 12 |
| **差**（会变成 `COMPOSITION_MEMBER_UNREACHABLE` 的） | **没有** |
| 注册表里 `type: skill`、却没有任何 composition 声明的名字 | **29** |

12 与 12 相等，正是那次运行一条 drift 都不报的原因——**这个模型复现出了检查器自己的
输出**，所以那个 29 才可信。29 个里就有本轮那三个：`production-observation`、
`production-change`、`research-evidence`。

**闸门是真的，只是主语收窄了。** 什么都没被删：一条 plugin 记录 `provides` 了、而没有任何
composition ship 或 recommend 的 ability，仍然报 `COMPOSITION_MEMBER_UNREACHABLE`（HIGH），
且 fixture 就钉着它——`test_a_provided_ability_that_no_composition_ships_is_high_drift`
往**记录**的成员表里加 `ghost-ability`，不是往注册表这个目录里加。
所以那条散文把主语说成了「注册表」，实际是「某条 plugin 记录的 `provides`」；
一条没有记录认领的 `skill` 条目**根本不进入任何比较**。

**处置：改散文，不改代码。** 照那句话去实现会与 §28 的 containment 规则相抵触
（`test_a_recommended_ability_the_registry_provides_is_not_drift` 钉着它，且注册表答的是
「这个节点能解析什么」、composition 答的是「这个 artifact 携带什么」两个不同的问题），
并且会对一个行为正确的产品报出 29 条 HIGH。文件现在写它真正把守的东西，并写明那 29 与那 12。

**同一小时内两个量具读错了东西**，两次都是靠与检查器自己的输出对照才抓到的，都没进文件：
先是把 composition 的键猜成 `abilities`/`carries`（真名是 `skills`/`recommended`），
报出 53；后是把 `provides[].id` 的最后一段当成员名，报出 `workspace-maintainer` 是个
「无 composition 可达的成员」——那是检查器的一个假洞。它不是：
`ProfessorZhi/workspace-maintainer` 是名字为 `workspace-management` 那条 ability 的 **id**，
而 `linux-work.yaml` 声明的正是那个名字。**`provides` 装身份，composition 装名字**；
检查器是对的，解析器是错的。

**顺带闭合的两件：** ① 这处只改注释，故 cachebuster 按 `scripts/build.py:789-790`
的规则（「byte-identical rebuild 保持不动；只有当新的生成产物必须让已装 plugin 缓存失效时
才 bump 一次」）不动——而且是量过才不动：`tests/test_build.py` + `tests/test_plugin_composition.py`
+ `tests/test_architecture_check.py` 合跑是 **8 failed, 74 passed, 2 subtests**，
8 条仍是那 8 条跨仓红，无新增，说明没有东西对该文件的字节做哈希。② §5.0.1 那条外部权限项
的严重性被这一节独立确认：**产品检查本身也在读 `main`**，不只是 fixture 在红。

### 9.18 架构检查的判词从来没被记录过，而它是 `NONCONFORMANT`——根因是同一个产品里有两套 root 解析

§14 的链条里有「架构/突变守卫绿色」这一格。**台账此前从未记过架构检查的判词**，这一节补上，并且写下它的根因——它不在 serveros 的代码里，也不在 ops 的实例里，而在两者之间。

**判词，连同它的确切调用方式。** 这一节的每个数字都绑定在下面这一行上；换一个调用方式
判词就变，所以调用方式本身是结果的一部分：

```
$ python skills/serveros/scripts/architecture_check.py --json \
    --serveros-repo . --canonical-ref HEAD \
    --ability-repo ../ability-management/fabric-integration-20260922 --ability-ref HEAD
architecture_status: NONCONFORMANT        runtime_status: RELEASE_INCOMPLETE
summary: {"findings": 7, "blocking_high_open": 1,
          "by_status": {"LEGACY_COMPAT": 1, "OBSERVED": 5, "OPEN": 1},
          "by_class": {"authority": 1, "map-projection": 3, "registry-scope": 1, "topology": 2}}
  HIGH   OPEN          ROOT_TOKEN_UNRESOLVED
  LOW    OBSERVED      BOOTSTRAP_ABILITY_REGISTERED
  MEDIUM OBSERVED      MAP_VIEW_UNBOUND  ×3
  MEDIUM OBSERVED      TRANSITIONAL_GENERATED_SURFACE
  MEDIUM LEGACY_COMPAT UNREGISTERED_DATA_ROOT
```

`blocking_high_open: 1` 就是 `ROOT_TOKEN_UNRESOLVED` 那一条，`observed` 的五个 token 是
`[KNOWLEDGE_ROOT, OPS_ROOT, RECORDS_ROOT, SRC_ROOT, TEST_ENVS_ROOT]`，`roots_source` 写的是
`ops/workspace/environment.yaml`。**这不是本轮代码的问题**，也不是我调用方式的问题——
`ROOT_SOURCES["ops_repo"]` 的默认值就是 `<workspace_root>/ops`，我用的是产品自己的默认。

**现行（2026-09-22 复测，§9.21）：**「不是本轮代码的问题」这半句**仍然成立**，但同一句后面
隐含的那个结论——「所以这就是世界的状态，可以接受」——**不成立**。那五个 token 的真因是
`ops/workspace/environment.yaml` **解析不过去**（`ScannerError`，line 70 column 11，一个以
反引号开头的 plain scalar），而它同时是 `schema: hwz/environment.v2`、**本来就没有 `roots` 键**。
检查器此前把「读不了这份文档」压成 `{}`，于是**一个语法错**看起来像**五个没人绑的 token**。
§9.21 让这个读数分开报：现在多出一条 `ROOTS_SOURCE_UNREAD`（HIGH/OPEN，带类型/行/列/解析器原话），
`ROOT_TOKEN_UNRESOLVED` 仍在并带上 `source_read`。**上面那个 invocation 逐字复测过**：
把本单元改的那个文件换回 HEAD 版，判词是 `NONCONFORMANT` / **7 条 / blocking 1**，与本节记的**一字不差**；
换回本单元版是 `NONCONFORMANT` / **8 条 / blocking 2**——多的那一条就是 `ROOTS_SOURCE_UNREAD`，
所以「后果」与「原因」现在并列在 blocking 名单上，读的人不必再从五个 token 去猜。
其余四种调用方式的对照在 §9.18.1 的 `现行`。
**这一行留下的教训**：一句「这不是本轮代码的问题」是对的，而它离「所以不用管」只有一步——
这一步正是本单元要走的那一步。

### 9.18.1 判词随调用方式变，而三种变化里两种是量具的错，不是产品的错

这一节是上一节的方法论，单独写出来是因为**它差点让我把两条不存在的 HIGH 记成真实缺陷**。
检查器把**代码从工作树扫、把契约从 ref 的 git object 读**（`:617-618` 读
`runtime/actuator-contract.yaml`，`:639-640` 用 `root=serveros_repo` 扫盘）。两者一致时没问题；
ref 的树与工作树的内容不一致时，左右两边说的不是同一件事，于是按构造产生两条 HIGH：

| 调用 | 判词 | 多出来的是什么 |
| --- | --- | --- |
| 纯默认（零参数） | `INCONCLUSIVE`，20 条，`blocking_high_open: 18` | 见下方 9.18.2 |
| `--serveros-repo <canonical 检出>` | `INCONCLUSIVE`，8 条，blocking 4 | — |
| `--serveros-repo .`（`--canonical-ref` 留默认 `main`） | `NONCONFORMANT`，10 条，blocking 3 | **`UNDECLARED_ACTUATOR` ×7 + `STALE_ACTUATOR`** |
| **`--serveros-repo . --canonical-ref HEAD`** | `NONCONFORMANT`，**7 条**，**blocking 1** | 无 |

**现行（2026-09-22 复测，§9.21）：**四行的判词现在**都是 `INCONCLUSIVE`**，条数分别是
21 / 9 / 11 / 9，blocking 19 / 5 / 6 / 5。**§9.21 只贡献了 +1 条、+1 blocking**——
本单元把 HEAD 版换回来逐模式量过，这一步是可归因的；上面那张表与今天之间的其余差额
是那之后别的改动留下的，不记在本单元账上。第三行那个 `UNDECLARED_ACTUATOR ×7 + STALE_ACTUATOR`
的成因（local `main` 落后 HEAD）**没有变**，只是它现在与另外几条并存，所以判词从
`NONCONFORMANT` 变成 `INCONCLUSIVE`：这个量具在 ref ≠ 工作树时报假 HIGH 这件事，本节末尾
点出而未做的那半，仍然未做。

第三行的两条是**量具的产物**：工作树的本地 `main`（`d0a94d6`）比 HEAD（`2ee6b79`）
**落后 71 个提交**，所以契约读的是旧版——`git show main:runtime/actuator-contract.yaml:710`
正是 `symbols: [verify_revision]`，而工作树里 `release.py` 的那个函数已经叫
`verify_node_runtime`（工作树的契约 `:735` 也这么写）。于是：

- `UNDECLARED_ACTUATOR` 的 7 个调用点里 6 个在 `intake/understanding_patch.py` 与
  `runtime/bounded_invocation.py`——**这两个文件在 local `main` 上整个不存在**，是新代码对旧契约；
- `STALE_ACTUATOR` 是旧契约对上新代码，`expected: [scripts/release.py, verify_revision, …]`。

**两条都不是真的**，工作树干净（`git status --porcelain` 空）、ref 改用 HEAD 后两条同时消失。
留在这里的价值是：**这个量具会在 ref ≠ 工作树内容时报出「本轮改坏了安全声明」的假 HIGH**，
而它报的正是会挡住 §14 晋升的那一格。要不要让检查器在 ref 的树与工作树不同时拒绝
（而不是照报），是一个我没做的改动——写在这里，不假装它已经解决。

### 9.18.2 零参数调用量的是一个不存在的目录，而它没有拒绝

同一个「量具读错东西」家族，但这一条的后果更宽：**产品默认值本身在这台机器上是坏的。**
`default_root()` 对 `SERVEROS_REPO` / `SERVEROS_ABILITY_REPO` 的处理与对 host profile 的
键**完全一样**——把变量值当作「装着检出的那一层」，再拼上后缀：

```
serveros_repo    -> /data/home/vince/workspace/src/serveros/serveros            NOT THERE
ability_repo     -> /data/home/vince/workspace/src/ability-management/ability-management  NOT THERE
ops_repo         -> /data/home/vince/workspace/ops                              exists
```

因为本会话环境里这两个变量已被设成**检出本身**（`SERVEROS_REPO=/data/home/vince/workspace/src/serveros`，
`SERVEROS_ABILITY_REPO=…/src/ability-management`），而不是 `…/workspace/src`。
host profile 是对的（`workspace_src = /data/home/vince/workspace/src`）——错的只有变量这一路。

**这不是孤例，是产品内部对同一个变量名有两种读法：**

| 读法 | 谁 |
| --- | --- |
| 是**检出本身** | `scripts/build.py:1037`、`runtime/cli.py:8911`（「default: SERVEROS_ABILITY_REPO, else the sibling checkout」）、`abilities/taxonomy.py:1095` |
| 是**要再拼一层的那层** | `skills/serveros/scripts/architecture_check.py:1427`、`scripts/runtime_truth.py:1588` |

按票数与环境实际值，前一种是对的，后两处是错的。而后两处**恰好是量具**。

后果：零参数运行的判词是 `INCONCLUSIVE`、20 条 finding、`blocking_high_open: 18`——
其中 5 条 `DECLARATION_UNREADABLE`、3 条 `MAP_VIEW_SOURCE_UNAVAILABLE`、4 条 `OWNERSHIP_DRIFT`
都是**关于一个不存在的目录**的。检查器自己在 `:1487-1495` 那段拒绝语里写的就是这件事：

> a wrong root produces findings about a directory nobody asked about — which reads
> exactly like a finding about this one.

它对**五个 root 都缺失**的情况返回 2 并拒绝，但对「路径拼错了、目录不存在」不拒绝——
而是照扫、照报，报出 18 条 blocking。**同一个产品里同一类错误，一种被拒绝、一种被当成结论。**

这两处（9.18.1 的 ref/工作树不一致、9.18.2 的两个变量读法）与本轮的 root 解析是**同一类**
的：一份规则写了两遍或两个地方，迟早被写成两个样子。它们都不在 §15 的升级清单里——
9.18.2 是机械的（把 `architecture_check.py:1427` 与 `runtime_truth.py:1588` 改成与多数一致，
或在拼之前先判目录是否存在并拒绝）；9.18.1 需要先决定「ref 与工作树不一致时该拒绝还是该照报」，
而**产品里已经有一个先例**：它拒绝缺失的 root 而不是猜。**本轮不改这两处**，原因是它们改的是
量具本身，而 §14 要求「架构守卫绿色」——在晋升链上同时改代码和改量具，就无法把「绿」归因给
任何一边。列入 §16。

**根因：`root_tokens()` 读的是东京已经退役的那份文档。** 契约的
`roots_source.locator` 是 `ops/workspace/environment.yaml`、`key` 是 `roots`。实测：

| 事实 | 量 |
| --- | --- |
| 活的 `ops/workspace/environment.yaml`（`main`，8643 B）里 `roots` 这个词 | **一次都不出现**；顶层键是 `schema: hwz/environment.v2` |
| 它能不能被 YAML 解析 | **不能**：`ScannerError … line 70, column 11`（`note:` 的值以反引号开头） |
| 带 `roots:` 的那一份在哪 | 只在 `ops/archive/beijing-20260918/workspace/environment.yaml:7` |
| 东京 v2 把绑定交给了谁 | 文件自己写着 `binding_authority: serveros:fleet/profiles.yaml#linux-shared-multiuser` |

而产品里**已经有一个能把路径解析对的解析器**：`runtime/paths.py`（Profile 绑定），实测
`python -m runtime.cli path contract` 在这台机器上零环境变量给出 **12 个根，全在
`/data/home/vince` 下**。`architecture_check.py` 里 `root_tokens()` 对
`PERSISTENT_ROOT` / `profiles.yaml` / `paths.py` **零引用**——它自己又算了一遍。

**但这里不是两个词表，是三个，而且它们互不同名。** 这一点决定了下一节那半件事的份量，
所以先量清楚：

| 词表 | 它给出什么名字 | 举例 |
| --- | --- | --- |
| topology contract 的机器实例键 | 9 个大写 token | `SRC_ROOT`、`OPS_ROOT`、`KNOWLEDGE_ROOT`、`DATA_ROOT`、`WORKSPACE_ROOT` |
| Profile（`fleet/profiles.yaml`）的路径键 | 12 个点分小写角色 | `workspace.src`、`knowledge.corporate`、`runtime-assets`、`state` |
| v1.1 路径契约（`runtime/paths.py` 的输出） | 12 个**又一组**短名 | `workspace`、`data`、`state`、`artifacts`、`toolchains`、`runtime.serveros-distribution` |

三组名字里，`workspace`/`data` 与 `WORKSPACE_ROOT`/`DATA_ROOT` 是**疑似**同一件事（同样的路径、
不同的主张），而 `SRC_ROOT`、`OPS_ROOT`、`KNOWLEDGE_ROOT`、`RECORDS_ROOT`、`TEST_ENVS_ROOT`
这五个**在第三组里根本没有对应项**——v1.1 契约的 12 个角色里没有任何一个叫 src / ops /
knowledge / records / test-envs。所以「让 `root_tokens()` 读 Profile」不是把五行对上，
而是**让三个各自有道理的文档就「一个根有哪些名字」达成一次一致**。这不是架构选择，
也不是「请人拍板」——它是三个现存词表之间一次没人做过的对齐。

**这正是检查器自己的文件在第 736-744 行禁止的形状**（那里说的是 composition 规则：
「一条规则被写了两遍，就迟早会被写成两个样子」）。同一份文件里的 root 解析没有遵守它。

**那 5 个 token 的真实身份，逐条量过了，不是同一件事：**

| token | 本机 Profile（`linux-shared-multiuser.paths`） | 磁盘 |
| --- | --- | --- |
| `SRC_ROOT` | **绑了**，叫 `workspace.src` → `/data/home/vince/workspace/src` | 存在 |
| `KNOWLEDGE_ROOT` | **路径绑了**，叫 `knowledge.corporate` → `/data/home/vince/workspace/knowledge` | 存在 |
| `OPS_ROOT` | **没有这个条目** | `workspace/ops` 存在 |
| `RECORDS_ROOT` | **没有这个条目** | `workspace/records` 存在 |
| `TEST_ENVS_ROOT` | **没有这个条目** | `workspace/test-envs` 存在 |

也就是说检查器报了 5 条，其中 **2 条是本机其实绑了、只是它读错了文档**，3 条是本机
Profile 真的没绑（而目录和机器政策都写着它们在）。

**所以修法有两半，而两半的性质不同，不能混着做：**

1. **事实那一半（serveros 仓内，可做）**：`linux-shared-multiuser.paths` 按既有命名约定
   （`workspace.src` / `workspace.worktrees`）补上 `workspace.ops`、`workspace.records`、
   `workspace.test-envs`。provenance 与其余十二条相同（`server_local_agent`），事实为真。
2. **词汇表那一半（需要作者，本轮不做）**：`root_tokens()` 要能读 Profile，就得有一份
   「契约 token → Profile 路径键」的映射：`SRC_ROOT→workspace.src`、
   `OPS_ROOT→workspace.ops`、`RECORDS_ROOT→workspace.records`、
   `TEST_ENVS_ROOT→workspace.test-envs`，这四条**看起来**机械；**`KNOWLEDGE_ROOT→knowledge.corporate`
   是语义判断**（Profile 那个名字说的是「corporate」，契约的 token 说的是根目录，
   同一个路径，但两个词不是同一个主张）。而上面那张三方词表说明**「看起来机械」正是陷阱**：
   上半张表里那三条 `workspace.*` 现在**只存在于 serveros 的 Profile 里**，第三组名字里没有对应项，
   所以给它们取名本身就是这次对齐的一部分，不是抄写。把三个词表对上是一次**著作行为**，
   而按约定它只能写在 `roots_source` 里（那里现在只有 `owner/locator/key/required_keys/derivations`）。
   **猜一个映射正是这个检查器拒绝做的事**（「reported as unresolved, never guessed」），
   所以本轮不替它猜。

**因此：只做了一半就不做另一半，等于宣布一个没人读的修复——本轮两半都没落**，
它作为 §16 那条外部权限项的一部分列出，并且是本轮**唯一一处「由现有事实决定不了」的
具体业务语义**（不是架构选择，也不是「请人拍板」：映射该由谁署名的答案在契约自己那句
`note` 里——`owner: hwz-ops` 的实例文档已经退役，而接手的 Profile 属于 serveros，
所以这个映射的归属本身要重新署一次名）。

### 9.19 P5：一条打印后就被丢弃的候选，和一个「不能告诉求助者已通知」的读数

**这一节补的是 P5。** 判据是 `docs/CC-EXECUTION-PLAN.md:128`——「员工知识消费与一条
**无答案转人工的状态/投递路径**分别**可核对**」；规则原句在 `docs/daily-work-cycle.md:118`
（已逐字核过）：「先入已有工作/通知队列并得到回执，再按许可给 owner 提醒；外发收件人/
机器人未绑定时显示待投递，**不能告诉求助者“已通知”**」。

**先记下缺口的形状。** P5 之前，每一味配料都在，唯独**没有那次 join**：

| 配料 | 状态（P5 之前实测） |
| --- | --- |
| `intake/support.py` 的 `NO_ENTRY_MATCHES` 改进候选 | 从写下那天起**没有任何消费者**；唯一读者是它自己的 `render()`（`support.py:1351`），**打印后丢弃** |
| `runtime/notifications.py` 的投递能力三值 | 除 `alerting.py#notify_for` 外**无调用者** |
| `memory/handoff.py` | 全仓唯一持久 inbox，但那是 §59 的**设备**交接（`device_ref`/`OPEN`/`ANSWERED`），**不是给人的队列** |
| `PENDING_DELIVERY` | 全仓零命中；`待投递` 只出现在文档里 |
| `escalate`/`handoff`/`transfer`/`inbox`/`queue` 动词 | 一个都没有 |

**本轮落了什么，四个文件（另有一处装运闸门，见下）：**

| 文件 | 是什么 |
| --- | --- |
| `intake/support-handoff-contract.yaml` | 五个触发、五个部分、两个锚、四种投递词、`exit_codes`；并写明**两个锚是本机今天没有的东西** |
| `intake/support_handoff.py` | 那次 join：读 `support.answer` 的读数 → 组装小决策包 → 记录两个锚各自回答了什么 |
| `tests/test_support_handoff.py` | 35 条 + 27 subtests（`35 passed, 27 subtests passed`） |
| `runtime/cli.py` | `serverosctl support handoff`（`cli.py:8838`，本工作树上） |
| `tests/test_build.py` | 同一处 `test_intake_ships_inside_the_artifact` 里按名字补上新模块与新契约的装运断言（见下文「顺手补的一处装运闸门」） |

**判词，连同它的确切调用方式**（照 §9.18 立下的规矩：换一个调用方式判词就变）。三条命令
逐字在 `support-handoff/cli-readings.log`：

```text
$ serverosctl support handoff --kb-root <KB> --status-code 599 --message '…user=ou_…'
outcome     NOT_NOTIFIED
queue UNBOUND   owner UNBOUND   delivery PENDING_DELIVERY   notified False
question    status=599 message='UpstreamWidgetGasketFailure user=<已脱敏>'      exit 2

$ … --status-code 402 --message 'SomethingElseEntirely' \
    --queue-receipt 'work-queue:ITEM-4242' --owner 'person:oncall' \
    --delivery-state DELIVERED --delivery-at '2026-09-22T20:10:00+09:00'
outcome     NOTIFIED      offered PLAT-006 under PLAT (03_平台代理层/)  NOT assigned
queue ACCEPTED as work-queue:ITEM-4242   owner BOUND   delivery DELIVERED       exit 0

$ … --delivery-state DELIVERED --delivery-at T          # no queue supplied
serverosctl: a delivery was reported for a packet the queue did not accept …
                                                                               exit 1
```

**第一条命令里的 `<已脱敏>` 就是这次 join 的证据，也是这个模块最可能出的那个错。**
support 的读数**把问题带了两次**：`reading["question"]["message"]` 是原文，
`reading["improvement"]["question_shape"]["message"]` 是脱敏后的。包是一份**可能被人归档**
的记录，选错那个字段是**一个词的距离**，另一侧是活标识符。所以 fixture 里放真标识符，
**两半都断言**（`test_the_packet_reads_the_redacted_field_and_not_the_raw_one`）：
既断言原文里**确实有**那个标识符（否则下面两句在同义反复），也断言包里**没有**。

**第二条命令里 `PLAT-006` 是那个候选第一次被消费，而不是被打印。** 同一行的 `NOT assigned`
是 §J 的规矩（「分配后不再变更」是**人**做的承诺），它在这个模块里是一个**字段**
（`recommendation.assigned = False`）而不是注释里的善意。

**四条不能声称的，每条都有对应的结构或测试：**

1. **本机今天发不出去，而这不是本模块的缺陷。** 两个锚都 UNBOUND：`serveros-work-mcp`
   在本机任何 `.mcp.json` / `settings*.json` / 插件清单里都注册不到，`workengine` 不在 PATH 上，
   `fleet/mcp-contract.yaml:362-366` 自己写着实现**不在这个仓**。所以**生产的诚实读数是
   `PENDING_DELIVERY` / exit 2**——§10 要的「显示待投递」就是这个读数，不是 READY。
2. **五个触发只有一个有生产者。** 只有 `NO_MATCH` 能从读数推出；证据冲突、权限不够、
   用户要求转人工、预算耗尽这四个**要调用方自己命名**。契约把这件事写在 `values` 的明面上，
   而不是等人来发现——并且**没有**把 `AMBIGUOUS_MATCH` 冒充成「证据冲突」：前者是两条**条目**
   都匹配（关于知识库的事实），后者是两个**事实**互相矛盾，不是一件事。
3. **`--queue-receipt` 不是队列。** CLI 没有队列客户端，它只能让**已经拿到回执的操作者**
   把引用交出来，并把这件事记进 `routing.queue.provenance`——原文「asserted on the command
   line by the caller; this tool has no queue client and called no queue」。一条命令行上来的
   引用**不能冒充**一次真的调用。
4. **`notifications.deliver()` 没有被使用，11 个事件名的闭集没有被扩。** 那 11 个名字里
   **没有**「向人升级」这个词（`handoff.completed` 是 §59 的**设备**交接，借用它等于让一份
   文档有两个意思），而扩别人的闭集是本模块不该做的事。所以投递是**被报告进来的**，
   本模块只记录、不发送。

**数字与证据。** 证据目录 `artifacts/skills/serveros/RUN-20260922-raw-coverage/support-handoff/`
（README 逐条记了每个文件属于哪个 revision 与产生它的确切命令）：

| 读数 | 数字 |
| --- | --- |
| 全套（本 revision，`pytest tests/ -q -rf -rs`） | `8 failed, 4700 passed, 20 skipped, 3795 subtests passed in 518.37s` |
| 那 8 条是哪 8 条（`pytest tests/test_architecture_check.py -q -rf`） | 全在 `test_architecture_check.py`，`8 failed, 16 passed`，名字逐条在 `arch-check-eight-reds.log`；与 §5.0.1 / §11.1 那组**跨仓红**同名同源 |
| 本模块（单独） | `35 passed, 27 subtests passed` |
| `test_build.py` + 本模块 | `78 passed, 29 subtests passed` |
| 突变守卫（`mutation_guard.py --root . --json`） | `status CHECKED`、`findings 0`、`undeclared 0 / stale 0 / tests_breach 0`、扫 231 个文件、`counts.declared 37 / unresolved 0` |

**这一格里的 `declared 25` 曾是错的，2026-09-22 订正为 37。** 订正方式是把 `5880bef`
（写下这一格的那次提交）`git archive` 到一处临时目录、在**那个 revision** 上重跑守卫：
`counts.declared 37 / undeclared 0 / 231 个文件`——与今天在本工作树量到的 37 一致（今天多一个
文件，正是 §9.21 新增的 `tests/test_root_resolution.py`，231 → 232）。同一份文件里 §9.13
记的也是 37，所以三处里两处对、这一处错。查过 37 里没有任何子集等于 25（按「站点是否全在
产品侧」分组：37 / 37 / 0），所以 25 不是某个更窄口径的读数，是读错了。留在这里是因为它
和这份台账反复记的那件事同形：**同一个数被写下两次，就会有一个是错的。**

**与基线的差额必须逐项说清，不然「绿」是个没人能核的词。** 基线是同一棵树上
HEAD `4fa1748`、**尚无这五个文件**时的全套读数 `8 failed, 4665 passed, 20 skipped,
3767 subtests passed`。差额：`passed` **+35**——正好是新增的 35 条；`skipped` 不变；
`subtests` **+28**，其中 27 条是本模块的。**剩下那 1 条没有归因**：我按「按目录发现模块
的 subTest 循环」去找过（`intake/` 下没有任何测试用 `glob`/`iterdir`/`rglob` 枚举），
没有找到判据，所以**不写成已知**。

**中途有一轮读数与基线不同，记在这里而不是抹掉。** 第一次全套给出 `21 skipped`（比基线
多 1），我按 §9.18 的规矩换 `-rs` 重跑取证：20 条 skip 的判据**全部是环境**（`no live
registry on this host`、`the ability-management checkout is not beside this repo`、
`no ledger at …/records/projects/serveros/memory`、`the governance document is not on this
machine` 等），没有一条与本改动有关。所以那次多出来的 1 条是**环境抖动**，可复现的读数是
20——这一点必须写下来，因为「多一条 skip」和「少一条测试」在计数上长得一样。

**顺手补的一处装运闸门（真缺口，不是顺手改字）。** `tests/test_build.py#test_intake_ships_inside_the_artifact`
本来只断言 `intake/support.py` 与 `intake/support-contract.yaml` 进了制品。本模块和
`support.py` 一样**在身旁读两个 `.yaml`**（脱敏规则读 support 契约、触发器与两个锚读自己
那份），而构建器只排除字节码——所以「模块进了、契约没进」这个失败模式对新契约**同样成立、
且同样无声**：它读起来会像「兜底从不触发」，而不是「兜底没带契约」。已在同一个测试里按名字
补上两条断言（`intake/support_handoff.py`、`intake/support-handoff-contract.yaml`）。

**一个只有真跑过才知道的量具事实。** `pytest -q -rf -rs` 只列出了 SKIPPED、**一条 FAILED
名都没列**：两个 `-r` 是**覆盖**而不是合并，最终生效的是最后一个。这个仓已经记过同一家族
的错三次（§5.0：子串搜索会命中「承诺该性质的那句话」）；这次是它的镜像——**用两个 `-r`
取证，等于把失败的名字丢掉，而计数还是对的**。所以本节的 8 条名字取自单独跑该文件的那一轮。

**没有改变的那条缺口（不得与上面混为一谈）。** `docs/daily-work-cycle.md:116` 与
`docs/integration-readiness-20260922.md:105` 说的 Bot 侧三件事——**Bot 消息入口、旧 matcher
到新知识格式的消费适配、owner 投递/回调闭环**——**一条都没有现场通过**，本轮**没有**碰它们，
也**没有**把「知识包已生成」写成「Bot 已上线」。这一节落的只是那条**状态/投递路径本身**
可核对：两条回执按 §10 的顺序分开、`notified` 只在 `DELIVERED` 上为真、锚未绑定时输出
`待投递`。它的上游（员工快照的真实消费）仍然是 `NOT_MEASURED`。

**顺手改掉的一条旧定位符。** §9 的 P5 行原本把消费动词定位在 `cli.py:7206`。实测：
那一行在 `4fa1748` 上是 `cmd_abilities_taxonomy` 的转发代码；**support 组在 `4fa1748` 上
是 `cli.py:8719`**，本工作树上是 `:8784`。旧定位符差了约 500 行，已改并标上 revision——
这正是「行号引用只在指定 revision 时才有意义」那条的又一例。

### 9.20 「装好了、手跑得动、没有上弦」第一次有了自己的面

**这一节起于两处读数对不上，而不是起于一个设计。**

```text
$ bin/serverosctl information watched --before 2026-09-22
  逾期           OVERDUE 1   —— 对 2026-09-22 而言
      复核 RECORDED 2026-09-16  ·  漂移 DRIFTED  ·  去抖 RECORDED 2026-09-15T00:05:53
      逾期 OVERDUE  —— last re-check 2026-09-16, cutoff 2026-09-22
```

§9.10 结尾把这件事写成一句 **「现在只剩一件真的缺：注册 `drift_sync` 为 SessionStart hook 的
`settings*.json` 在本机 not found」**——而那一句**当时没有读者**。两处读数分开看各自都成立：
覆盖面说「盯着的这篇六天没复核」，采集器自己的 docstring 说「我是 SessionStart hook 驱动」，
而 `grep -rln drift_sync /data/home/vince/.claude/` 的命中**全在会话记录、粘贴缓存和文件历史里**，
**没有一条在 settings 文件里**。合起来才是那份发现：**复核逾期不是源头安静，是没有人跑采集器。**
这两件事要求的动作相反——重跑采集，或者去把 hook 写上。

**为什么不是第三个 `*_surfaces` 条目。** `runtime_parity.py` 的
`OBSERVABLE_SURFACES = ("instruction_surfaces", "mcp_surfaces")` 问的是「这个文件在不在」，
而 driver 的问题**恰好不在那一层**：`/data/home/vince/.claude/settings.json` **在**（2745 B，
`SessionStart` 下注册着 `botmux session-ready`）。把 hook 塞进那两项的形状里，探针会照抄
`path.exists()` 得到 **`present: true`**，报告会继续说这个节点是接好的——
**一个存在的面被读成一个在工作的面**，正是这套东西一路在抓的那个形状往下一层。
所以问题改成 per-driver：**这个 driver 在这台机器上这个 runtime 的 session-start 面上注册了吗。**
它还**不对称**：一份 per-runtime map 得给没有这个 driver 的 runtime 也写一条，而那条唯一的诚实值
是个占位符——与其造占位符，不如让这张表是 driver 的清单。

**只声明实测到的那一个文件。** 本机**没有** `~/.claude/settings.local.json`、**没有**工程级
`.claude/settings.json`，插件自带的 hook manifest 也不存在——所以三个都不声明。这不是谨慎，
是这个文件已经因为「照着惯例写」错过一次：`instruction_surfaces` 四条里错两条，
`runtime_parity.py` 第一次跑就抓到了（`runtime_features` 上面那段注释就是那次的自述）。
一个 driver 的 `program` 走 `path_role: workspace` + `records/information/raw/drift_sync.py`，
因为 `path_roles` 里没有 `records` 这个角色（§9.18.2 量过）。

**落点：** 契约 `abilities/agent-runtime-contract.yaml#capabilities.session_drivers`（一条：
`drift_sync` / `claude-code` / `SessionStart` / `marker: drift_sync.py`）；
读者 `abilities/runtime_parity.py` 新增 `declared_drivers()`、`observed_driver_rows()`、
`check_drivers()`，`report()` 收第四个入参，`main()` 加 `--observed-drivers`；
观测者 `runtime/cli.py` 新增 `_observe_drivers()` 与 `_commands_under()`；测试
`tests/test_runtime_parity.py` 新增 `DriverTests`（11 条）、`DeclaredGapTests` 与 `CliTests` 各一条（23 → 36）。

**三态，加两个各自独立的 finding。** `REGISTERED` / `NOT_REGISTERED` / `UNREADABLE`——
第三个**不是**第二个的软版本：文件在而读不了，是这个节点**答不上来**，把它报成「未注册」
是**对一份没人读得动的文件凭空断言**。`DRIVER_SURFACE_UNREADABLE` 因此走 UNKNOWN；
`DRIVER_PROGRAM_ABSENT` 是独立一条，并带 `registered` 字段，因为
**「注册指着一个不在的脚本」和「脚本没人注册」是相反的两个失败**，
读的人不该靠解析句子去分。

**这个面不打印 hook 命令。** 命令被读、被拿去匹配 `marker`，但**一个字都不出现在报告里**——
一行 hook 命令是可以放 token 的地方，把它打印出来等于把泄漏做成一个 verb。
行上只有 driver 名、event、状态、surface 路径、**注册条数**和原因。
`_commands_under()` 也是**通用遍历**而不是写死 `[].hooks[].command`：那层嵌套是 Claude Code
自己的形状，写死的那天它一变，这个检查会对**每一条** driver 报假警——而它存在的全部理由是如实报。

**live 读数（命令逐字在
`artifacts/skills/serveros/RUN-20260922-driver-surface/cli-readings.log`，含 exit code。
这里**不引提交号**：那份日志连同本节一起进同一个提交，所以引用它等于引用自己，
而引用提交号还会随 rebase 失效——读数该引的是**产出它的文件**，不是承载它的 revision）：**

```text
  driver drift_sync   claude-code  SessionStart NOT WIRED  /data/home/vince/.claude/settings.json
           1 command(s) are registered under SessionStart and none names drift_sync.py
verdict     CONFORMANT
  known     DRIVER_NOT_REGISTERED: … [UNWIRED_ON_THIS_HOST, NEEDS_HUMAN — 改它要动用户的
            Agent 配置，§42 把这件事放在本轮授权之外。改动连同回滚已写在
            `runtime/workbench_sources.py` `DEPLOYMENT` P2]
```

那条 `registrations: 1` 就是 `botmux session-ready`——**这台机器上注册在 `SessionStart` 上的唯一一条东西**。

**三条机制是被测试抓出来的，不是被想出来的。**

- **`acknowledges`：一条缺口不能只按主语承认。** surface 缺口不必写它承认哪个 finding，
  因为**一个缺掉的 surface 只产出一个 code**，点出 surface 就点出了 finding。driver 会**两种失败**，
  一条只写 driver 名的记录会**把另一种也承认掉**——记录成了「没人记过的缺陷」的消音器。
  所以 driver 缺口必须带 `acknowledges: [DRIVER_NOT_REGISTERED]`，不带就什么都不承认。
- **stale 的主语必须是量过的东西。** `unobserved_declared_gaps` 原来的判据是「没被承认过」，
  于是**一次什么都没读的运行会把缺口报成「已修或记录错了」**。这一条**在本轮之前就活着**：
  `report(contract, observed={"codex": {}})` 把 AGENTS.md 缺口列进了 stale，
  而那一次**一个面都没打开**。现在 stale 只对**主语真被量过**的缺口成立
  （`_subject_was_observed()`），并补了两条判据：没量过 → 不是 stale；量过且已注册 → 是 stale。
  测试里那个 `{"path": "/x"}` 占位 fixture 因此**成了输入的一部分**——占位路径量不出主语，
  已改成契约真实声明的尾巴，这条也在测试的 docstring 里写明了。
- **「量过」和「量出东西」不是一回事——这条是我自己的判据先犯的。** 变异测试里第七条
  （`_commands_under()` 一条命令都收不到）**第一次跑是漏网的**：命令的记录变成
  `registrations: 0` / `NOT_REGISTERED`，而那条端到端判据的独立重算拿 marker 在该 event 子树里
  **做子串搜索**，也得 `False`——**两边一致地错**，于是绿。根因是 `drift_sync` 在这台机器上
  **本来就没注册**，所以「读了面、没有一条命令命中」和「一条命令都没读到」产出的**是同一个状态**；
  这条判据分辨不出**被测出来的缺席**和**空测量**，而这恰恰是整个记录赖以存在的那个区分。
  那条判据的 docstring 自己写着它存在的理由正是「不能用被测代码去检验被测代码」，
  而它在 `registrations` 这一维上犯了同一个毛病。
  修法是**把计数也钉住**：测试自己遍历该 event 子树数出 `command` 字符串的条数，
  断言它等于 `row["registrations"]`——这样**收集这一半和匹配那一半各自被独立钉住**，坏哪一半都红。
  改完重跑，**七条全部被抓，还原后两个文件 sha256 逐字节相符**：

  ```text
  caught  M1 未注册的 driver 不再报 finding  (3 failing)
  caught  M2 缺口按 driver 名承认一切（acknowledges 失效）  (2 failing)
  caught  M3 stale 的主语恒为「量过」  (1 failing)
  caught  M4 读不了的 surface 报成未注册  (1 failing)
  caught  M5 DRIVER_PROGRAM_ABSENT 永不触发  (2 failing)
  caught  M6 marker 匹配失效：注册条数当成了命中  (1 failing)
  caught  M7 命令收集器找不到任何命令  (1 failing)
  ALL SEVEN CAUGHT, tree byte-identical
  ```

  这一条值得单独写出来，因为它是一个**关于「补判据」的判据**：整套东西的论点是
  「`present: true` 不等于面在干活」，而这一处是**同一个论点在测试自己身上的实例**——
  一条绿了的判据不等于判据活着，**除非有人试着让它红**。

**这台机器对自己报出的东西。** live 记录里 `stale gap codex workspace AGENTS.md` 仍然出现：
契约把 codex 的 `workspace/AGENTS.md` 记成 `ABSENT_ON_THIS_HOST`，而**东京这台机器上它在**
（`/data/home/vince/workspace/AGENTS.md`，本次 `device agents` 逐条实测 `present`）。
也就是说那条记录**在这台机器上是错的**，而机制自己把它报了出来——这正是这个字段该有的样子。
**本轮不改契约那一行**：它的 `because` 说的是 `/home/hwz/.codex/`，那是另一台机器的读数，
按「行号引用只在指定 revision 时才有意义」同一条纪律，把北京那台的观测改成东京的观测
是另一种错。记在这里，留给人。

**接线同上，是自动被门覆盖的**：经 `_load_module_from` 加载，`loader_graph` 推出它，
`test_build.py` 的产物闭包判据自动覆盖——`abilities/runtime_parity.py` 早就在闭包里，
本轮没有新增文件。这一轮**没有写入点**（观测者只读），所以
`runtime/actuator-contract.yaml` 不改。

### 9.21 五个未解析的 root token，真因是一处 YAML 语法

**这一节起于 §11.3 第 3 条的账对不上。** 那条写的是：检查器报 5 个 token 未解析，其中
2 个（`SRC_ROOT`、`KNOWLEDGE_ROOT`）是**本机 Profile 其实绑了、检查器读错了文档**，
3 个（`OPS_ROOT`、`RECORDS_ROOT`、`TEST_ENVS_ROOT`）是**本机 Profile 真没绑**。去量了活体读数，
两半都不成立：

```text
unresolved : [KNOWLEDGE_ROOT, OPS_ROOT, RECORDS_ROOT, SRC_ROOT, TEST_ENVS_ROOT]
declared   : []
roots_source: ops/workspace/environment.yaml
```

`declared` 是**空的**——不是"绑了三个少了两个"，是检查器从那份文档里**一个 root 都没读到**。
所以「哪几个绑了」这个问题本身问错了对象。

**四种不同的事实，此前是同一个 `{}`。** `_ops_roots` 的返回把下面四件事压成了一个值，
而调用方只看得到最后那个数字：

| 事实 | 改前的样子 | 改后 |
| --- | --- | --- |
| locator 够不到 | `{}` | `found: false` / `roots_key: "unreadable"` |
| 读到了但解析不了 | `{}` | `roots_key: "unparsed"` + `parse_error`（类型/行/列/解析器原话） |
| 读到了、没有 `roots` 键 | `{}` | `roots_key: "absent"` + `schema` |
| 有 `roots`、但不是映射 | `{}` | `roots_key: "not-a-mapping"`，`keys: 0` |

**真因是一处标点。** 本机那份 machine instance 是 `schema: hwz/environment.v2`，
角色根已改为「由 profile 绑定」（`binding_authority: serveros:fleet/profiles.yaml#linux-shared-multiuser`），
所以它**本来就没有 `roots` 键**——这是合法的形状变化，不是谁忘了写。而它**连解析都过不去**：

```text
ScannerError  line 70, column 11
found character '`' that cannot start any token
```

YAML 的 plain scalar 不能以反引号开头（它是保留指示符），而那份文档里有一行
`note: \`.build-*\` / \`.prev-*\` 是发布流程的隐藏前缀桶…`。于是链条是：
**文档语法错 → `_load` 把异常吞成 `None` → `{}` → 五个 token 未解析 → 一条 HIGH 指名五个 token**。
读的人会去绑五个 token，而该做的是改一行文档或修 locator。

**`_load` 吞掉原因是这条链的铰链，所以它也得改。** `_load` 对「空文本 / YAML 语法错 / 不是预期的文档」
一律答 `None`——只要解析值的调用方继续用它就好，但**在报告一份文档的调用方不能用它**：
「它没解析成功」不可行动。新增 `_load_detailed()` 返回 `(doc, reason)`，reason 里只放解析器
**自己的 `problem` 与位置**，绝不放 `str(error)`——那串东西是消息**加一段代码框**，而代码框就是文档正文。
位置是 1-based，因为它是要拿去在编辑器里打开的。这一条与「不打印 hook 命令」是同一条纪律：
**报告不是转载文档的地方。**

**判定抽成了纯函数。** `roots_source_finding(root_map)` 现在单独存在，因为「读数变成哪条 finding」
正是要被钉住的那一步。分轴是**字节有没有被读到**，不是消息多坏：读到了却用不了的文档
（语法错、形状不对）是**有位置的可修缺陷**，locator 一解析出来它就已经 OPEN；够不到的文档
是对本节点可达性的陈述，记成 OBSERVED/MEDIUM——和「登记表不存在」同一个待遇。
反过来会让一条关于可达性的注记占住 blocking 名单，而把可修的缺陷放进记录。

**这条路径此前一条测试都没有**，所以合并才没被发现。新增 `tests/test_root_resolution.py`
（10 条，从 0 条起）：五态各一条、`present` 不产 finding 一条、OPEN/OBSERVED 那对轴一条、
后果带因一条、以及一条「不许把文档正文抄进报告」。

**增量是准的，而且是逐模式量的。** 把 HEAD 版换回来跑一次再换回本单元版，四种调用方式
**都是 +1 条 finding、+1 条 blocking**：

| 调用 | 改动前 | 改动后 |
| --- | --- | --- |
| 纯默认（零参数） | `INCONCLUSIVE`，20 条，blocking 18 | 21 条，blocking 19 |
| `--serveros-repo <canonical 检出>` | `INCONCLUSIVE`，8 条，blocking 4 | 9 条，blocking 5 |
| `--serveros-repo .` | `INCONCLUSIVE`，10 条，blocking 5 | 11 条，blocking 6 |
| `--serveros-repo . --canonical-ref HEAD` | `INCONCLUSIVE`，8 条，blocking 4 | 9 条，blocking 5 |
| **§9.18 那条 invocation**（含 `--ability-repo …/fabric-integration-20260922 --ability-ref HEAD`） | `NONCONFORMANT`，**7 条**，blocking **1** | `NONCONFORMANT`，**8 条**，blocking **2** |

所以 §9.18.1 那张表里 `--serveros-repo .` 的 `blocking 3` 与今天的 5 之间那 2 条**不是本单元加的**，
是那之后别的改动加的；本单元的份额一行一个，没有别的。顺带一提：
`--serveros-repo <canonical 检出>` 与 `--serveros-repo . --canonical-ref HEAD` 现在**读数完全相同**
（9 / 5），这正是「工作树在 HEAD 上与 canonical 检出一致」的样子。

**同一片区域里另外两条 HIGH 我查过了，是诚实的。** `DECLARATION_UNREADABLE` 的观测逐字是
`"object not found"`，`COMPOSITION_EVALUATOR_ABSENT` 点名
`skills/ability-management/payload/composition.py` 并原样印出它实际解析到的那个重复了一段路径的
仓库（`/data/home/vince/workspace/src/ability-management/ability-management`，即 §9.18.2 那条）。
两条都说了自己找不到什么。**所以 laundering 在这片区域只有本单元这一处。**

**第六条变异体漏网了，和 §9.20 的 M7 是同一个形状。** 变异是
`"problem": problem or "…"` → `"problem": str(error)`（把文档正文抄进报告）。它**第一次是绿的**，
原因是我那条判据种了一个哨兵字符串、然后断言它不在报告里——而 **PyYAML 会把代码框按固定宽度截断**，
哨兵在 `str(error)` 里被截掉了（`note: \`SENTINEL_VALUE_THAT_MUST_NOT_AP\ ...`），
于是判据靠「截断窗口比哨兵短」而绿，不是靠代码拒绝抄正文。**种哨兵本身就是错的方法**：
它绿不绿取决于 PyYAML 的窗口宽度，而不取决于被测代码。改成对解析器**逐字段相等**：

```python
with pytest.raises(yaml.YAMLError) as raised:
    yaml.safe_load(body)
assert source["parse_error"]["problem"] == raised.value.problem
assert source["parse_error"]["line"] == raised.value.problem_mark.line + 1
```

以及键集合恰好是那四个（多一个字段就红）。改完重跑：**六条全被抓，还原后 sha256 逐字节相符**。

```text
caught  M1 源读数不再产出任何 finding  (2 failing)
caught  M2 读到的文档也报成「够不到」：OPEN/HIGH 退化成 OBSERVED/MEDIUM  (1 failing)
caught  M3 解析失败与「没有这个键」重新合并成同一种读数  (1 failing)
caught  M4 解析失败不再给出位置  (2 failing)
caught  M5 把解析器的 str() 当原因：文档正文被抄进报告  (1 failing)
caught  M6 非映射的 roots 也报成键数  (1 failing)
ALL SIX CAUGHT, tree byte-identical
```

**量这件事的过程中我自己丢过一次文件。** 为拿「改动前」的读数，我用了
`git checkout-index -f -- <file>` —— 而那个文件在本轮**还没暂存**，索引里是 HEAD 版，
于是它把整个改动**静默抹掉**。这和 §9.20 记的 `git checkout --` 是同一个陷阱的第二副面孔
（一个是按 ref 还原，一个是按索引还原，看起来都像「还原」）。文件从变异测试的备份副本取了回来
（`mutations/architecture_check.py.orig`，sha 相符）。**两次都不是靠运气**：这个单元从写下第一行起
就有备份副本，正是因为那一课已经上过。

**本轮不修那份文档。** `ops/workspace/environment.yaml` 在**另一个仓库**，ops 的策略是默认只读；
改它是一行引号的事，但那是别人的文件、别人的提交。所以它进 §11 的孤立清单，连同精确位置与修法。
`DECLARATION_UNREADABLE` 那条路径的 `_load` 也是同一个吞异常的写法，但它的观测已经是诚实的，
**不在本单元里顺手改**——一次只动一处，绿才能归因。

### 9.22 P3：原生 reading 接上现有 semantic writer —— 三处旧耦合、一个真缺陷、一条只有套件覆盖的验收项

**这一包的判词必须分两半写**，因为它的验收条款有四条，而**真材料只量到了三条**。
把四条合起来说「P3 通过」正是这份台账从第一节起就在防的读法。

计划原话（`docs/CC-EXECUTION-PLAN.md:80`）：

> 复用 `understanding_brief.py`、`understanding_semantic.py`、`understanding_patch.py`、
> `claim_recall.py` 和分源水位；writer 不调用模型是正确分工，不补第二 writer/understandingd。
> ……修 `runtime/recipes.py:perception` 的旧耦合：单个 source 未答不能阻断所有成功源；
> 先持久化各源 capture，再分别推进 understanding；不能把「source 变了」直接解释为「新 Item 候选」。
>
> **验收**：真实 IM/Doc/会议来源各有一个 reading 及可核支持；Claim 成功/View 失败只补 View；
> retraction 立即影响当前 recall，即使 OpenViking 掉线。执行 `test_understanding_brief.py`、
> `test_understanding_semantic.py`、`test_claim_recall.py`、`test_source_waterline.py` 及 J03；
> 引用范围合法不替代语义正确性。

#### 9.22.1 改的是三处耦合，不是三处写法

`perception` 此前把「一个源没答」当成了整个 pass 的判决。三处各自独立：

| 耦合 | 改前 | 改后 |
| --- | --- | --- |
| 单个源未答阻断所有成功源 | 只要有一个源没答，整轮不推进 | 新增 `observe` 步：先记下**哪些源答了、哪些没答**（`captured` / `unanswered`），成功源照常推进 |
| 一个源都没读到 = 一次失败的推进 | 与「部分源没答」同一个分支 | `if not observed` 单独判 `SOURCE_UNAVAILABLE`——**这是唯一一种整轮不可答的情形** |
| 「源变了」= 「有新 Item 候选」 | `refresh` 的 ask 把 revision 问成候选 | ask 改成「说这个 delta 对本 scope 的认识意味着什么」并明写 `Do not propose Items` |

**第二处还有一个不显眼的分支**：源**都没动、但有人没答**，此前被读成「安静周期」。
它不是安静周期——沉默的源可能正是有内容的那个。现在这一情形也判 `SOURCE_UNAVAILABLE`。

**cursor 逐源推进，而不是整批覆盖。** 全答的那次直接换成观测到的集合；有人没答的那次
`{**cursor, **observed}`——**没答的源保留上一次的水位**，否则下次重启会把它从「没读过」
悄悄变成「读过了」。这一条正是计划里「先持久化各源 capture，再分别推进 understanding」
在**现有记录形状内**的落法：一个显式的 per-source `observe` 步，逐源 cursor，
**没有**新增第二条持久化水位线，也**没有**再放松 `last_success`。

#### 9.22.2 两个在改的过程中露出来的缺口

**(a) `observe` 是契约里声明了、代码里从没发出过的一步。** `recipe-contract.yaml` 的
perception 段里一直写着它，而 `ctx.step` 里一次都没有。契约与实现不一致，而**不一致的方向
是契约更诚实**——它描述的正是这一包要做的事。所以这一处不是「补一个步骤」，是**让代码追上契约**。

**(b) `last_success` 的平局没有判据。** 排序只看 `finished_at`，而夹具的时钟是固定的，
于是所有候选的 `finished_at` 相同，谁被选中取决于目录遍历顺序——**上一轮全量套件里已经红了
一条，报出来的却是「顺序不定」**。加文件名为第二键，平局才有确定的答案。

#### 9.22.3 真材料上的验收：四条里量到三条

验收材料在 `$ARTIFACTS_ROOT/tmp-p3/acceptance/`（`records/` 是理解层与所引原始来源的副本，
36 M）。两次 reading 都针对**真的**飞书来源区间，写进的是**真的**收据。

| 验收项 | 判词 | 证据 |
| --- | --- | --- |
| 真实 IM 来源有一个 reading 及可核支持 | **真材料上做到** | reading A 落在真实区间 1418–1422，在委托内以 `agent_reconciled` 提交 2 条 `OBSERVED_FACT`；其中一条同时以**引用线**写进 `projects/litellm.md`；卡 prose 在 fence 外**逐字节**未变（`head.rstrip("\n") == live.rstrip("\n")` → True，追加字节恰为 `'\n\n'`）；recall 两条均 `CURRENT` |
| retraction 立即影响当前 recall | **真材料上做到** | reading B 落在真实区间 1423–1491，提交 1 条新主张并携带一条指向 `wb-1420-astra-torch-only` 的 RETRACTION，依据是真实消息 1476；recall **当场**印出不可召回，理由逐字是「remote 可以掉线、被撤回的事实仍不得回来」。复跑合计 `CURRENT 2, RETRACTED 1` |
| 目标/负责人/期限/承诺/权限变化仍交真实接受 | **真材料上做到两次** | 一条 `ACCEPTED_DECISION` 停在 awaiting（「要做什么不是机器可以接受的」）；一条**碰了 deadline** 的观察（真实消息 1452）停在 awaiting（「这条主张改变期限，无论事实多确定，那是人的决定」） |
| **Claim 成功 / View 失败只补 View** | **只有套件覆盖，真材料没量到** | 这一轮每张 view 卡都在，**没有产生 PARTIAL**。该性质由 `tests/test_understanding_semantic.py` 里为「守着它的卡写成功、写进 view 失败，必须可读为 PARTIAL、可恢复、且绝不是所有消费者已对齐」而写的那组覆盖 |

第四行是本节的要点：**它在盘上、被测过、但这一轮的真材料没有把它逼出来**。
所以 P3 的判词是「三条在真材料上成立，一条由套件担保」，不是「P3 全绿」。

计划点名的四个测试文件与 J03：

```text
tests/test_understanding_brief.py tests/test_understanding_semantic.py
tests/test_claim_recall.py tests/test_source_waterline.py
→ 333 passed in 14.25s
```

J03（`docs/SCENARIO-ACCEPTANCE.md` 的信息增量场景）里**属于本包**的几条，逐条对上：
「一条真实获准信息改变相应认识」→ 上表第一行；「无新信息不造工作」→ `delta.does` 的
`NO_CHANGE` 分支；「source 正文变而 range 不变旧确认拒绝」→
`test_a_reading_whose_range_is_no_longer_proposed_is_refused_by_name`；
「capture 已提交而语义阶段崩溃，重启仅续理解」→ 9.22.1 的逐源 cursor 与四条新测试；
「会议负责人/期限不明确保持 candidate」→ 上表第二行的 deadline 那条。
**J03 里不属于本包的（附件只取需要的、bot 消息计数、分页与过期附件）不在这里声称。**

#### 9.22.4 验收跑出来一个真缺陷：`claim_recall` 的角色是从「这一次写入」读的

**这是本轮唯一一处产品缺陷，而且是验收跑出来的，不是读代码读出来的。**

`build_index` 为每张卡记「它现在载着哪些 id」时，用的角色是**本次写入的角色**：

```python
if role == _semantic.OWNER:
    row["owners"][owner] = True      # 本次是 owner 写，则它载着的每个 id 都记成持有
else:
    row["referenced_by"][owner] = True
```

而模块自己写着 `written.claims` 是**写完之后卡载着的**东西。于是：一张 view 卡先引用别人的主张，
**后来**为自己的主张写一次 OWNER——两次都是 writer 有权做的写入——那条引用线就被记成所有权，
`len(owners) > 1`，判 `CONFLICT`（「一个 id 在两个卡里是两个主张」），**这条主张从此不可召回**。
换句话说：**对任何 view 卡的正常第二次写入都会触发它。**

修法：角色从**卡自己的那一行**读（`→` 开头的是引用，其余是持有），且**只在本次写入没有放下
这个 id 时**才问；卡不在、或没有 claims 区，就退回本次写入的角色——那里记录是仅剩的口径。
两个新助手 `_role_from_card` / `_roles_in_card` 加在 `build_index` 之前，逐卡至多读一次，
缓存挂在 `card_roles` 上。

**为什么读卡的正文是对的、而不是在两种同样好的东西之间挑一个**：写入记下的角色与它留下的
那一行，在 writer 能产出的**每一个**状态里都一致，所以两者只可能因**手改卡**而分歧；而那时
正文是读卡的人看到的东西，一个悄悄相信收据的索引，会把一条主张报成「被一张自己那行写着引用的
卡所持有」。

**验证**：73 条聚焦测试；四条变异**全部致命**（M1 退回「本次角色管所有载着的 id」4 条红、
M2 角色不读正文恒 OWNER 32 条红、M3 去掉兜底 2 条红、M4 反转箭头判据 11 条红），
**存活 0 条、无效 0 条、还原按 sha256 一致**；真材料根复跑读数回到 `CURRENT 2, RETRACTED 1`。

**测试改了构造，没删断言。** 冲突态的构造从「收据撒谎」搬到「卡被手改」
（`test_two_cards_each_holding_a_line_for_one_id_is_a_conflict`），并新增两条钉住新规则：
一条说**收据声称的持有若卡上不显示就不被相信**，一条说**引用线在没有收据解释时不被读成所有权**。
判词一个没动。

#### 9.22.5 活体读数：三个面，读法与改前一致

```text
work waterline       sources  UNREADABLE 1 · UNRESOLVED 1 · LABELS_NOT_CONTIGUOUS 3 · BEHIND 44 · NOT_POSITIONAL 20
work understanding   card     14 份 · BEHIND 3 · UNMEASURABLE 7 · UNTRACEABLE 4
work claim-recall    索引     收据 0 张，主张 0 条，涉及卡片 0 张
```

**「收据 0 张」的正确读法上一轮我读错过一次，这里更正并记下来。** 我当时的结论是
「没有卡能接受一条已提交的主张」，因为活体卡读出来全是 `claims NO_FENCE`。**错了**：
`_replace_fence` 对**没有 fence 的卡是追加**一条 fence（`text + separator + FENCE_BEGIN + …`），
所以 `NO_FENCE` 不是拒绝的理由。全节点零收据**真实的意思是**：writer 从没在真材料上跑过。
这一条把「零」从「这一层收不了」改读成「这一层还没被用过」——**而分母就是 0 张收据本身**。

#### 9.22.6 没有做的，与为什么

- **没有第二 writer、没有 understandingd、没有让 writer 调模型。** 计划明写这是分工不是缺陷。
- **没有在 recipe 记录里加第二条持久化水位线。** 见 9.22.1 末段：per-source `observe` 已落在
  现有记录形状内，再加一条水位线会让「哪个水位是权威」变成一个新的、没人能回答的问题。
- **P2 的 revision 事实不在活体 records 树里。** `capture_coverage._revisions_component` 要的
  manifest 字段（`sync.revisions_checked_at` / `sync.revisions_compared`）只存在于 P2 分支的
  工作树，活体 `workspace/records` 的任何 manifest 里都没有。所以 waterline 那一行
  `revisions UNDECIDABLE` **不是缺陷**，是「这份读数要的事实还没送到」。把它送上去是**对外动作**，
  与先前缓下的 `dbc9144` / `8ce6fe7` 同类，进 §11。

### 9.23 P4：两半从来没有见过面 —— 契约读得出来，不等于发布闭得了环

计划 P4 那一格的原话是「**文档 identity resolver 存在不等于远端 publisher 已闭环**」。本轮
把这句话跑成了一次真实写入，结果那两半各自成立，合起来不成立。

#### 9.23.1 先量：契约本身的两处缺陷，和一处被掩盖的 `KeyError`

在被修之前，真 registry + 真 profile 上的读数是：

- **P4 registry 里只有一行**（`litellm-work-doc`），而它此前被当成「文档身份解析器已存在」的
  证据。一行 registry 不是一套契约。
- **profile 与 registry 的关系只有半边可判。** registry 写 `relation: OWNED`，profile 写
  `standing: CANONICAL`、`sync_mode: NONE`——**三者互不校验**，`read_delivery` 之前根本不存在，
  三个词放在一个文件里被当成了「已经表达过」。
- **一处被掩盖的 `KeyError`**：`derive_sections` 按 `section["key"]` 建索引，而当时那份 profile 的
  某一节没写 `key`。这个错**在真实路径上抛的是 `KeyError`**，而当时的调用点外面套着一个宽
  `except`，于是它变成一句「读不出契约」。**能被读到的是「读不出」，读不出的是「为什么」**。
  本轮把它变成按名报错（`sections[2] 没有 key`），并在测试里钉住 `KeyError` 这件事本身。
- **`relations[3].resolution` 是自相矛盾的**：同一份 state 里 `resolution: RESOLVED`，而
  `relations[3]` 的 `resolution` 是另一回事。两个字段同名不同义，读起来像同一个判断。
  本轮不动那份 state 的形状（它是能力的产物），只在读取侧把**它说的是什么**逐项印出来。

#### 9.23.2 修：契约读取与写入分成两个模块，I/O 全部注入

- `intake/managed_documents.py`（§9.22 之后的第二个 P4 提交）：把 registry + profile + state 读成
  **契约**，规则是「缺失即 unknown，不得默认」——缺 `status` 不得读成 managed、`relation` 与
  `standing` 不一致要**按名拒绝**、闭词表之外的 surface 不得默认。7 条变异全部被测试杀死
  （锚点各命中一次，还原按 sha256 核对）。
- `intake/document_publish.py`：写入的纪律。五个后果 `body/media/index/git/delivery` 各记一笔回执，
  `ALREADY_EFFECTED` / `NOT_APPLICABLE` / `NOT_ATTEMPTED` / `UNKNOWN_OUTCOME` 互不合并。
- `runtime/document_transport.py`：**唯一知道 `lark-cli` 的文件**。`space/resolver.py` 的头里已经写着
  这条道理（内核学会一个租户的云路径之后就不再是内核），所以纪律留在 `intake/`，适配器单独一层。

#### 9.23.3 三条「空过」——本轮最值钱的发现

**这三条的共同形状是：一个检查通过了，而它其实没有执行。** 台账里 0/0 那条纪律说的是分母；
这一组说的是**分子也可以骗人**。

1. **能力的三条保护里有两条拿空原文问就空过。** `guard_protected` 的两条是**与原文比计数**的
   （丢掉几个 `完成` 删除线、抹掉几个 `不确定`），`original_text=""` 时两条都真。所以
   **计划阶段的闸门通过不等于写入会被允许**。修法不是改能力，是把闸门**再问一次**：读到真文档
   之后、拿真那一节的文本问。计划报告里现在印着这句（`PLAN_GATE_NOTE`），不再让
   「计划通过」被读成「写入会被允许」。
2. **没观测到的受众与空受众相等。** `audience_fingerprint(None)` 一度返回 `"UNOBSERVED"`，于是
   前后两次读**都比得出相等**——「受众没变」成了「没看也相等」。改成返回 `None`，并在报告里
   明说「受众未观测，本次没有核对受众有没有变」。**没做的检查必须印成一个没做的检查。**
3. **patch 把自己的锚点替换掉了。** 第一次真写的范围含节标题，而新内容里没有标题，于是那次
   写入**吃掉了自己的锚点**，要到下一轮才表现为「找不到标题」。修法是 `find_section` 只返回
   **正文范围**并另带回 `heading_id`，transport 据此在 `block_replace` 与 `block_insert_after`
   之间选。**一次写入吃掉了它下一次要找的东西**，这一类只有真的写一次才会露出来。

#### 9.23.4 真实写入：一个 OWNED 章节 + 一个获准镜像章节

两个对象都是 ServerOS 自己建的 MyDocs 文档（只有我能看，做完可删）：

```text
源   docx/UgzGd0i5BoJZ4WxIdZCc17Obnlh   OWNED · standing CANONICAL · sync NONE
镜像 docx/GQ17dQsN9oca3sxHtsJcANVwnIf   FOLLOW_MIRROR · standing DERIVATIVE
                                        copied_from 指上面那篇 · sync SOURCE_REVISION
```

跑出来的（逐条对应计划里那句验收）：

| 计划里的判据 | 真实动作 | 读数 |
| --- | --- | --- |
| 至少一个真实 OWNED | base=6 两段正文 | body **OK**（revision 7） |
| body 成功 / media 失败 | 同一轮带一个不存在的媒体文件 | body OK，media **FAILED** |
| **只补缺边** | base=9，同一段正文，媒体文件补上 | body **ALREADY_EFFECTED**（不重写），media **OK**，exit 0 |
| 至少一个获准 mirror | 不说自己在写镜像 | 没有开始，`mirror_ack` **点名 copied_from**，exit 2 |
| 同上 | 说明后写 | body **OK**（revision 4） |
| 重复不双份 | 镜像重跑 | body **ALREADY_EFFECTED**，revision **没有动**，exit 0 |
| 人改保留 | 人改 zone 里手工加的一句，跨六次写入 | 原样在（块 id 未变） |
| 权限不扩大 | 写入前后 `member-list` | 逐项相同（1 人 / full_access） |
| —— | 请求里写 `audience` | 没有开始，按名拒绝，exit 2 |
| —— | base=7 而文档在 11 | body **REFUSED**「base moved」，**没有调 writer** |

**媒体只搬了 69 字节的 1×1 PNG**，没有批量移动任何历史云对象，没有动大音视频；大件仍留在
原处。**"Git 只留获准文本和 metadata" 这一半没有真实对象可验**——本对象的 `index`/`git`/
`delivery` 三条边这一轮都是 `NOT_APPLICABLE`（本轮没有供应 writer），所以那是**三个未跑的分量**，
不是三个通过。

#### 9.23.5 这一轮又改了两个真缺陷，都是真实写入逼出来的

- **`intent_key` 把 `base_revision` 折了进去**，而这个键自己的 docstring 写着「内容寻址、不是
  时间寻址」。真机上量到的后果：同一段正文、中间别人的编辑抬了 base，**重跑又写了一遍**——内容
  一字不差，却凭空多一个修订、块 id 全换、锚在这些块上的东西全断。改成内容寻址之后，
  「没事可做」也**优先于**「base 动了」：base 陈旧只对**写**是问题，而没有写。
- **读回比对把段落之间的断行抹掉了。** 第二次真写：内容两段，读回说「和写的不一样」，
  **而文档一字不差**。于是模块把一篇正确的文档报成错误的。修法与它暴露的测试缺陷一起改：
  假文档此前把所有段落压进一个块，**所以这个缺陷在假文档上永远测不出来**；现在它按真服务的
  存法一段一块。

#### 9.23.6 没有做的，与为什么

- **真实写入只在 ServerOS 自己的验收对象上做过。** 没有一个真实的 Published Knowledge /
  J04 / REF 对象被写过。计划里的判据允许这样读（「至少一个真实 OWNED 和一个获准 mirror」），
  但账面上必须写清：**这两个 OWNED 与 mirror 都是 ServerOS 自己的**，`Codex 配置教程` 那一对
  真实的镜像**一个字都没动**——它是否属于 ServerOS 没有证据，写它就是一次对共享面的写。
- **`index` / `git` / `delivery` 三条边这一轮是 `NOT_APPLICABLE`。** 不是通过。
- **常设维护契约放在哪，没有决定。** 见 §11.6。
- **`~/.claude/skills/document-stewardship/{scripts,references}` 不在任何仓库里**，而 canonical
  只有 `payload/SKILL.md`，注册表那张卡还写着 `Installations: (none)`。也就是说：**跑起来的那份
  实现没有 canonical 出处**。这是 §9.22.6 那类跨仓缺口的一个新实例，本轮只记。
- **`lark-cli` 的本地路径白名单**（工作目录 / `/tmp` / `~/files`）与本机策略（临时文件放
  ARTIFACTS_ROOT、不建 `~/files`）**互相矛盾**：媒体那条边默认永远发不出去。修法是调用时把
  工作目录切到文件自己所在目录——不是放宽白名单，而是让「发送者能指到的文件」与「CLI 允许读的
  目录」重合。

### 9.24 P5：一条到不了任何人的通道，和一条到得了的

计划里 P5 的验收是四件：一个收藏/Follow 增量、一个指南 scoped 维护、一个周期报告完成适用
的 compose/publish/send、以及 J04/J10/REF01–REF12。这一轮把「send」跑到了真的收信人手里 ——
而在跑之前先量了一遍发送通道，量出来的东西比实现本身值钱。

#### 9.24.1 先量：`human.notification.transport` 写着 SUPPORTED，而它到不了任何人

`mcp/human_control.py` 的能力表里这一条一直写着 `SUPPORTED`，理由是一句读起来很自然的话：
「POST /api/trigger carries a ServerOS sentence into the conversation」。实测不是这样：

```text
BotMux /api/trigger   没有 conversationId 这个字段。
路由靠 target.chatId / target.sessionId / target.rootMessageId 三者。
三个都不写 —— 服务得到的是一个【合成会话】（http_async_…），
文本变成【模型的任务】，答案从 HTTP 返回；那一轮的头部自己写着
「不显示在任何聊天里」，并且禁止发布到飞书。
```

所以它到不了任何人，而这张卡一直说它行。这不是措辞问题，是有后果的：同一形状的两条
`ORPHAN_PROCESS_TREE` 告警在这条路上报过 `last_delivery: DELIVERED` /
`last_notified_at: 2026-09-20T11:28:51Z`，三份已部署的插件副本也没有一处按 `chatId` 路由。
台账里那一栏记的是「送达」，而那个动词在这条路上没有对应的现实。**「记成 DELIVERED」与
「真的有人收到」是两个事实，只有当第二个有证据时第一个才算数。**

处置：能力降为 `UNKNOWN`，把实测到的原因写在降级的那一行**本身**（不是写在别处），
`docs/human-control.md` 同步，`tests/test_actor_context.py` 的断言改成 `UNKNOWN`。
这是这张表第一次**向下**修正 —— 它此前每一次纠正都是向上或平移，而向下才是这张表真正
需要能做的事。

#### 9.24.2 能到人的那条路：一个适配器，不是第二个实现

`runtime/message_transport.py`（新）是唯一知道 `lark-cli im +messages-send` 的文件；
`runtime/document_transport.py` 是唯一知道 `lark-cli` 的文件。两句不冲突，因为前者**委派**
给后者。几处承重，每一处都是实测定的：

| 判断 | 为什么不是另一种写法 |
| --- | --- |
| `run()` 委给 `document_transport.run`，不复制封套规则 | §9.22.4 的教训：**在两个冗余位置实现的规则无法被单点变异杀死**。封套规则写两处，删掉一处测试照样绿 |
| 成功判据是 `ok == true`，不是 `code == 0` | 实测：成功那一次 `code` 不是 0。按 `code` 判会把成功读成失败 |
| 收件人必须带前缀（`user:` / `chat:`） | `--user-id` 与 `--chat-id` 互斥；不带前缀就得猜一个，而猜就是替调用方决定收件人 |
| 幂等键取 `str(intent)[:50]` | CLI 的键上限是 50 字符，超了不发 |
| 发送前重算 sha256，不符则 `FAILED` 且**不调 CLI** | 「发出去的必须是它说要发的那份」只有在真去比一遍时才是真的 |

最后一条要单说：`--idempotency-key` 的去重窗口是 **1 小时**且是**静默**的 —— 窗口内再发一条
不报错，只是不出现。所以「没看到消息」有两种原因，这个模块**不把它们合并**：
窗口内 → `ALREADY_DELIVERED`（同一次调用）；窗口外、或时间戳不可定位 → `None`（答不出来，
不猜）。`report_delivery._reconcile` 因此收到的是**整个前回执**（`intent`/`report`/`audience`/
`previous`），而不只是那个 intent 字符串。

#### 9.24.3 报告投递跑到了环

一份真实日报（3,916 字节，`sha256 2ba14a1d…`，周期 2026-09-25 00:00 → 20:39 +08:00）
从 compose 到 send：

| 动作 | 读数 |
| --- | --- |
| `report request` | outbox 记一条，`intent 73c11a28…`，`state REQUESTED`，`requested_by vince` |
| `report deliver` | `DELIVERED`，**exit 0**；回执绑定 report 的 sha256、`provider.id lark-cli`、`transport im +messages-send`、远端 id（`om_…`）、`attempts 1` |
| 再 deliver 一次 | `ALREADY_DELIVERED`，**没有第二条消息** |
| 没请求就 deliver | 拒，exit 2，outbox 里 pending 仍是 1，**没有写回执文件** |
| `report withdraw` 后再 deliver | `WITHDRAWN` → `NOTHING_REQUESTED`，exit 2 |

还有一个读数教训值得记：第一次把退出码读成了 0，因为命令串在管道里，`echo $?` 取到的是
`tail` 的状态而不是被测命令的。**管道会把退出码换掉**，而这条链上「拒绝」与「成功」正好
是 2 和 0 —— 读错一次就会把一次拒绝记成一次成功。

#### 9.24.4 收藏/Follow 增量：REF01 的真云做掉了

这一半不是新抓一篇，是把既有那一篇**做完整**：

- **复核原文**（新增生产者 `information/raw/web/verify_web.py`，见 §9.24.5）：重新取回、
  身份四项与前次一致，`page_kind` 四类壳 20 个标记逐条查过、可读正文零命中。
- **补齐一条从来没人问过的问题**：§4 四问里的 `truncated_body`，于是
  `serverosctl information reference` 的读数由 `UNDECIDABLE` 变成 `CAPTURED`。
  **这是补做检查，不是放宽判据** —— 另外三问的判据一个字没动。
- **把 REF01 的真云做掉**：参考笔记（自写摘要/用途/批注，不是全文转载）发布进既有
  「外部文档」Source Library 空间并回读，逐项都在（原文入口、作者、保存理由、三条要点
  各带出处、适用范围、关联、批注、最近检查、折叠层机器细节）；受众未变（空间 1 人，
  新文档成员与既有页面逐项同形）。`effects.reference_published` 由 `NOT_DONE` 变 `DONE`，
  并明写「上一轮那笔授权没到位，本轮到位了，**不是把 NOT_DONE 改写成 DONE**」。

**Follow 这一半不建，两个理由**：§3 表里 Follow 是「用户要求才启用」的那一栏，用户没有
要求过；而且关注清单的解析器按飞书 `link_id` 定位（`watch_coverage.captures_for` 只看
`links/<link_id>/`），一条网页链接放进去只会解析成 `NO_CAPTURE`，也就是一个**永远不会触发
的关注**。这两条写进了 `reference-note.json` 的 `detail.sync_receipt.not_done_here`。

#### 9.24.5 两条只有实测才会露出来的发现，和整库套件抓出的两个真缺陷

**发现一：字节差不是内容差。** 复核那次字节 **+2,276**，而**文章正文一字未动** —— 量出来的
差异落在站点左侧的文档导航（「Images and video」改成「Images」，并多了一个 Realtime 条目）。
把 sha256 的变化读成「原文变了」在这里是错的，所以 `verify_web.py` 只报字节差，把内容差
留给读者，并把量出来的首个分歧窗口记进 `_recheck.measured_delta`。按词逐位比：
可读正文 17,290 → 17,336 字，**17,290 个是同一段文字**。

**发现二：整页子串匹配会把一篇读得完整的文章判成撞上登录壳。** 这一页 `login` 出现两次，
两次都在 `href="https://platform.openai.com/login"` 这个**导航链接的属性值**里，可读正文里
0 次。所以壳标记按**可读正文**判；只出现在标记里的命中记在
`markers_outside_readable_text` —— 它是事实，只是不是那个问题的答案。

**整库套件抓出的两个真缺陷，两个都属于这次改动：**

1. `runtime/report_delivery.py` 里一句散文被判成子命令：程序名（`serverosctl`）后面紧跟的
   是 "is the entry point"，于是被读成 `is the`。判定它的 `tests/test_declared_commands.py`
   从每个产品 `.py` 里读「程序名 + 两个小写词」的字面形状，再去问 parser 有没有这个动词，
   并**不看那句话是不是在讲别的**。**那句话重写，扫描器不动** —— 它是为「注释里点名一个
   没人建的动词」写的，而它读得那么字面正是它的工作。
2. **一条生产出来却没有读者的观测，和没有生产是一样的。** 复核生产者写完只落了 `_recheck`
   与 `fetch`，没写 `intake/capture_coverage.py` 一直在读的 `last_verified_at`，于是那份
   读数把「**树**动了」读成「**读者**动了」。字段名不是新造的：`links/` 下的 manifest 一直
   带着它，采集器与读者早就对它有共识 —— 所以修法是把复核接进一个**已经有消费者的键**，
   而不是发明一个没人看的新键。同时 `verify_web.py` **不删自己没产出的东西**：上一次复核
   时人写进去的量（两次字节各自的可读正文差）原样留着。

因此 `tests/data/capture-coverage-live-2026-09-25.json` 被**第二次**重取，`retaken` 从一块
变成一个**列表**，两次都留着，因为两次的原因正好相反：第一次是**读者**动了（chat capture
新增 `revisions` 组件），第二次是**树**动了（那篇网页被复核）。写回前量过：68 个对象里
1 个变、0 个消失、0 个新增，其余**逐字节相同**，而变的那一个恰好只差采集器写的那四个字段；
`coverage.verified` 由 `RECORDED 62 / NOT_RECORDED 6` 变成 `63 / 5`。重取也把这次暴露的
**本文件局限**写进了重取记录：验证日期按**天**比（`_verified_on` 取 `[:10]`），所以落在捕获
同一天的复核无法被表成「之后」，只能重取；而 manifest 现在带着复核时间，**明天之后的复核
就不必再重取**。测试侧同时补了一条：一个带着 `last_verified_at` 的 `web_capture` 必须读成
`RECORDED` —— 这条键从此有测试钉着。

**上面第 1 条随后在本节自己身上又发生了一次，而这正是它值得留下的原因。** 本节在写「那个
扫描器读得太字面」时，把两个假命令**原样引在反引号里**，于是修完 `.py` 之后整库套件**第二
次**判红 —— 同一把尺子、同一处红，只是这次造句的是**记录这条规则的文档**。按 §5.0 给的
判据，这一行在**谈论**一条命令，不是在指示人去跑它，所以该改的是文档。修法不是把它加进
`KNOWN_ABSENT`：那张表断言的是一件事实（「这里有一个真缺口」），而这里没有缺口，只有一个
读者被误导的可能，所以那会是**一次假的登记**。修法是让本节不再把程序名与假参数相邻写出。
这条规则是冷的：`SCANNED` 里含 `docs/*.md`，**记录缺陷的文字与缺陷本身受同一把尺子量**。

连同 §5.0 记的三次（`relevance|cleanup` 的 fence 误读、同一句话的非 fence 版本、以及
`'record|digest-record|receipt'` 那条**靠巧合变绿**的分段），这一族到今天是**第四次**，
四次里三次是「文档在谈论命令」，一次是「闸门碰巧没红」。**判据仍然只有一条，而且它不在
闸门里，在读的人手里**：这行是在指示读者去跑，还是在谈论一条命令。

#### 9.24.6 没有做的，与为什么

- **周期报告只到「手工串起来」这一步。** compose→publish→send 三个动词都真的跑到了环，
  但把它们串起来的是**命令行**，不是定时器：`serveros-daily.timer` 在本机**仍是 disabled**
  （§3.9）。所以这一环的 INTEGRATION 有了，**LIVE 没有** —— 与 B2 同一种形状。
- **指南 scoped 维护（第二个验收项）没有做。** 真实写入仍然只发生在 ServerOS 自己的验收
  对象上（§9.23.6 的那两条不变）。
- **`EXTERNAL-REFERENCE-LIBRARY.md:117` 要求三种 hash 分开记**（真实原始字节 hash、
  抽取文本 hash、用于去模板噪声的摘要 hash），本轮只落了字节 hash 与可读正文字数：
  **抽取文本的 hash 没有**。字节留着，所以它可以重算，但今天没有那份 hash 在 manifest 里。
- **`外部文档` 那篇 docx 的索引页今天列不出任何东西**：它的 `<sub-page-list>` 指向一个
  **已被删除**的节点，而在其下建子节点被拒 131006（同一 space 的根节点可见列表只有未改动
  的默认模版「首页」）。这不是本轮的活，但它决定了「外部参考笔记的长期落位」这个问题该
  怎么问 —— 连同上面这几点，交人要定的部分收进 §11。
- **`lark-cli` 的本地路径白名单**（工作目录 / `/tmp` / `~/files`）与本机策略（临时文件放
  ARTIFACTS_ROOT、不建 `~/files`）仍然互相矛盾；本轮用 `--content -` 从 stdin 绕过去了，
  **白名单本身没动**。

### 9.25 P6 切片 A：一个动词回答了两个人没说过的话

计划第 4 条要修的是「拆开推荐候选与已接受 Focus 接续」，第 7 条后半要修的是「无 priority
不阻断人已选且合法的任务」。这两条在本轮被定位成**同一个缺陷的两面**，缺陷就在
`intake/daily_cycle.py` 的 `select_focus` 里。

#### 9.25.1 那个缺陷：一个动词同时回答了两个问题

`select_focus` 干的是「按空槽选候选」：板子上有 5 个在进行、容量是 2，它就挑 2 个填进去。
于是它同时回答了两个**主语不同**的问题 ——

- 「政策推荐什么？」（主语是调度器）
- 「**人已经接受、正在接续的是什么？**」（主语是人）

第二个问题的答案本来可以让 `focus = 0`，但在「按空槽选候选」的实现里**表达不出来**：
一个周期永远不会少于 `limit` 个目标。所以「人被选定的活做完了、今天没有再选」这件事在
这个动词上**没有形状** —— 它不是被算错，是**无从被说**。0 在这里是合法数量，而代码结构上
不允许出现 0。

拆法：`continue_focus(rows, grants, accepted, …)` 是**接续器**，只走 `accepted["items"]`
里的行，**不问优先级**（一句一段地写在 docstring 里：推荐需要排序，被接受的目标不需要竞争
槽位 —— 用「缺 priority」去挡住人已经选定的任务，正是这一条要修的东西）；
`select_focus` 降为**推荐器**，返回里多一个 `recommendation_only: True`，并显式排除已被
接受的行（同一个 Item 不该出现在两个答案里）。两者共用**一块板、一个信封、一个瞬时**：
`runtime/cli.py` 只组合一次板子，把 `board["today"]` 交给两边。

**唯一被 `work admit` 采用的那个答案是接续器**（`cmd_work_admit` 拿 `continuation` 当
`decision`，`proposal` 丢掉）。这一句是这次拆分里最承重的一处 —— 推荐器输出得再漂亮，
它也**不产生任何准入**。CLI 上因此印成两个明确标注的区块：「本次周期：人已选定、可以继续的」
与「建议（不是周期，不产生任何准入）」。

§9.22.4 的教训（「在两个冗余位置实现的规则无法被单点变异杀死」）在这里被直接用上：
「这一行是否仍被持有 / 是否已结束 / 是否授权」现在**只有 `classify()` 一份**，
`select_focus` 与 `continue_focus` 都从它取判断，并且有测试**钉住两者对同一行的分类相同**。
`_order()` 把「没有优先级的行不能被排序」从一句守卫变成**构造上不可能**（候选里出现没有
确定优先级的行就 `ValueError`），因为一个「如果没有优先级就跳过」的守卫正是它被绕过的路径。

#### 9.25.2 一个伪造的默认值：`next_action`

原实现里有一句 `row.get("next_action", "research")`。它做的事是：拿一个**没有人命名过的
动作**去问授权，于是「这张卡的字段没有生产者」这个真实缺口，被一句关于 grant 的**听起来很
合理的话**盖住了。现在没有 `next_action` 就是 `NEXT_ACTION_NOT_ESTABLISHED`（精确 UNKNOWN），
而且**先于 grant 被问**（顺序写在 `classify` 里，就是验收 D 要的那个顺序）。

本机实测：样本树上第一次真的出现 `SAMP-0006 READY NEXT_ACTION_NOT_ESTABLISHED`。
**同时要记下一件不方便的事实**：本机的活板子三个 projection 都停在 2026-09-10，所以
`work focus` 在本机**永远**先判 `STALE_BOARD`，走不到这个分支 —— 这条修正在本机的真实测量上
**不可见**。写在这里，是为了不让后来的人把「本机没看出差别」读成「本来就没差别」。

#### 9.25.3 缺席不是空：`accepted_items` 的三种「没有」

`today.yaml` 读不出来有三种原因，接续器**不把它们合并**：文件不在这个 projection 上 →
`NOT_ON_THIS_PROJECTION`；文件在但解析不了 → `SELECTION_UNREADABLE`（BLOCKED，不是空选择）；
文件在、解析出来了、items 为空 → `SELECTION_IS_EMPTY`（一个**选择**，只是选的是零个）。
「没有人选过」与「有人选了、选的是零」在输出里是两句话。这条与 §9.24 的 0/0 同源：
**未完成项的分子也可能说谎。**

#### 9.25.4 实测读数

命令行上验收 A（同一块样本板、同一次运行、只有一个今天的重点）：

```text
— 本次周期：人已选定、可以继续的 —
accepted    PARTIAL — 本周期读的是 records/projects/<p>/board/today.yaml
focus       PLANNED
objectives  1
  SAMP-0002  ACTIVE      无优先级  STATE_HOLDS_SLOT  示例：进行中，design 有、plan 缺

— 建议（不是周期，不产生任何准入） —
proposal    OVER_CAPACITY
in flight   5 — 已在进行、各占一个槽
refused     6
  SAMP-0006  READY       NEXT_ACTION_NOT_ESTABLISHED
```

把 `today.yaml` 改写成选中一个 **CLOSED** 的 Item，同一份代码给 `objectives 0` +
`TERMINAL_STATE`，而建议块**照旧**报 `in flight 5`。这就是验收 A 的形状：**Focus 是 0，而
「这个板子上有 5 个在进行」仍然是真话**。

本机的活读数（不是样本）：`accepted UNPRODUCED`（`records/projects/*/board/today.yaml`
一个都不存在 —— 这台机器上从没有人写过今日重点）、`focus BLOCKED (STALE_BOARD)`。
这两条都是关于这个节点的**真话**，不是缺陷。

#### 9.25.5 这一片没有做的事

- **完成边界仍然是散文。** `intake/workchain-contract.yaml` 的 `completion.requires`
  （"outcome is set, and report.md is present"）**到今天为止没有任何代码读它**。把它变成机械
  可读的字段、让 DELIVERED 的被选目标**只在必需结果有证据时**才释放槽位，是下一片。
- **`focus_source.NOT_PRODUCED` 只解决了一项**（`next_action`）。`worker_stopped` /
  `resources_released` 仍然没有真实生产者，`checkpoint` 仍然只作为散文出现在
  `runtime/recipe-contract.yaml` 里。**（2026-09-25 更新：`worker_stopped` 已由 §9.27 接上
  生产者——invocation receipt；`resources_released` 与 `checkpoint` 仍无，见 §9.27.7）**
- **正式 Requirement 到 canonical Work Item 的幂等对应**完全没做（ABSENT）。
- **首次真实 grant 走可信用户控制**仍然 PARTIAL：`put_grant` 只有测试调用它，没有 CLI 动词、
  没有 daemon 方法、没有 MCP 工具。
- `work focus` 的 JSON 顶层键由 `decision` 变成 `continuation` + `proposal`。查过：
  `docs/`、`skills/`、`mcp/`、`fleet/`、`plugin/` 下**没有代码消费者**读它，只有测试读，
  所以这次改键没有把任何人打断。

### 9.26 P6 切片 B：完成边界从散文变成机械可读的判据

计划第 4 条的两句在这里落地：「完成边界/接受版本与真实 evidence 联结」（改动）与
「DELIVERED/待验收是否释放责任取决于 required 结果和实际 effects，不单凭旧 enum」（验收）。
入口是 §9.25.5 的第一条 —— `intake/workchain-contract.yaml` 的 `completion.requires`
（"outcome is set, and report.md is present"）那句话，**在那之前没有任何代码读它**。

#### 9.26.1 那句话为什么不能只当一句话

`completion.requires` 读起来是一句完整的要求，但它有一个散文无法避免的性质：**它可以被绕过而
不报错**。`DELIVERED` 这个词本身就长得像「做完了」，于是任何一处只读状态名、不读那句话的代码
都能得出「已完成」。`current.json` 里的 `"outcome": null` 与一份存在的 `report/` 可以同时成立，
而没有任何东西会因为它们同时成立而转红。

这一片的做法是：把那句话**换成代码能对错的两张表**（`requires_fields: [outcome]`、
`requires_documents: [report]`），散文留在原处**只给人读**，并加一条测试断言两张表与那句话点名
的是同一批东西。§9.22.4 的教训（同一规则在两处实现，单点变异杀不死）在这里**反用**：
两份拼写只有在「它们分叉时会转红」的前提下才是可辩护的，否则第二份就是会漂移的副本。

这个块也从 `personal_board` 下**移到顶层**，与 `item_documents` 并列：Item 欠什么是 **Item
chain 的属性**，personal board 只是它的一个读者 —— `compose_item` 会为 personal board 从不显示
的 Item 读这个块。兄弟契约 `intake/board-contract.yaml` 的 `completion:` 本来就在顶层。

#### 9.26.2 四个答案，不是一个

`workchain.completion_of(row, rules)` 返回四值，其中**第一个与第四个分开**是它存在的多半理由：

| 判定 | 意思 |
| --- | --- |
| `NOT_ASSESSED` | 契约**不在这个状态下测完成**，所以无可欠。**不是**一次通过的评估 |
| `COMPLETE` | 状态声称完成，契约点名的一切都在 |
| `DISCHARGED` | 状态不声称完成，但契约点名的一样不缺 —— 欠的是**人的验收**，不是活 |
| `OPEN` | 契约要求的东西不在，缺口被逐个点名 |

把 `NOT_ASSESSED` 混进 `COMPLETE`，是这一类的标准死法：一个**从未被问过的问题**会以「没查出
问题」的样子通过审查。同一类错误本轮已经记过三次（§9.24 的 `UNPRODUCED` vs `EMPTY`、
`NOT_ON_THIS_PROJECTION`、`NOT_ASSESSED`），所以这次把它写进返回值的**第一个字段**，而不是
注释里。

缺口也**不合并成一句**：`missing_fields`（字段没有值）、`missing_documents`（文档不在）、
`unreported_documents`（**链上根本没提这份文档** —— 既不说在有，也不说没有）是三种不同处境，
第三种最容易被读成第二种。`_set()` 单独判「这个字段算不算有值」：`None`、`""`、空集合都是
**穿着值的衣服的缺席**，用 `is not None` 去查会把 `outcome: ""` 读成「outcome 已经记了」。

#### 9.26.3 最承重的一处判断：义务**不进** `disposition`

`classify()` 是「这一行是否仍被持有 / 是否已结束 / 是否授权」的**唯一**一份判断（§9.25.1）。
新读数 `completion` 放哪里，这一片遇到两条路，只有一条是对的：

- 把「终态但欠证据」读成 `HELD`。**错的**：`select_focus` 会把这样的行算进 `carryover`，
  于是一条已经做完的活会被重新算成在飞 —— 正是 §9.11 修掉的 `OVER_CAPACITY` 假象会原样长回来。
- 让 `classify` **只报告** `completion`（终态行上的第三个键），推荐器照旧 `excluded
  TERMINAL_STATE`，**接续器**才据此继续。**对的**：一个读数、两个调用者、两个答案 —— 与
  `priority` **同形**。

这条非对称被一个测试钉住：同一行 `T-1`，推荐器报 `excluded TERMINAL_STATE`、`carryover == []`，
而接续器 `selected == ["T-1"]`。

接续器的三个新理由各自对应一种处境：`AWAITING_ACCEPTANCE_EVIDENCE:<缺口>`（DELIVERED）、
`CLOSED_UNEVIDENCED:<缺口>`（CLOSED）、`COMPLETION_NOT_ASSESSED`（行上**没有**判定 —— 未被
问过的问题不是有利的答案，所以目标继续，同时进 `unevaluated`）。

#### 9.26.4 这一片抓到的东西：我自己读错过的那一条

这一片最值得记的不是代码，是一件**被打脸的过程**。

动手之前，我用 `work item SAMP-0003` 与 `SAMP-0004` 在样本树上取了读数，看到五个文档全是
`PRESENT`，于是记下结论：「样本树上两条终态 Item **都不欠证据**」。**这个结论是错的。**
`sample-alpha/current.json` 里 SAMP-0003 的行是：

```json
"id": "SAMP-0003", "state": "DELIVERED", "outcome": null,
```

它有一份 `report/`，但没有 `outcome` —— 契约在 `DELIVERED` 点名的两样里，缺的正好是**文档清单
不会显示的那一样**。把完成边界算出来之后，同一条命令、同一棵树，立刻说：

```text
完成边界   未结清 —— in DELIVERED the contract requires ['outcome', 'report'], and ['outcome'] is not there
           ↳ 缺字段   outcome
```

也就是说：**读文档清单得出的「全部 PRESENT」与读契约得出的「欠 outcome」可以同时为真**，而前者
看起来像一个完整的答案。这条因此不再需要「本机没有实例、只能在单元层覆盖」的注解 —— 样本树里
**本来就有**一个天然的实例，是用读者面读的时候没看见它。

#### 9.26.5 顺手修的一个错标：`unevaluated` 的表头

§9.25 加进来的 `unevaluated` 列表里原本只有一族字段（`worker_stopped` /
`resources_released`），渲染器因此写死了一句「没有生产者说明 worker 是否仍持有」。这一片往里
加了第二族（`completion`），那句话就会把「契约没在这个状态测完成」印成「没有生产者说明 worker
是否仍持有」—— **一个关于 worker 的句子，说的却不是 worker**。

修法是让表头从条目**实际点名的字段**推出来，并把条目的 `because` 印在下面。记在这里，是因为
凡是「写死的解释句 + 后来变多的族」这个形状，都会以同样的方式错下去。

#### 9.26.6 实测读数

同一块样本板、同一次运行，把 `today.yaml` 改成选中 SAMP-0003：

```text
— 本次周期：人已选定、可以继续的 —
accepted    PARTIAL — 本周期读的是 `records/projects/<p>/board/today.yaml`
focus       PLANNED
objectives  1
  SAMP-0003  DELIVERED   无优先级  AWAITING_ACCEPTANCE_EVIDENCE:outcome  示例：已交付但 PR 未合并
```

对照 §9.25.4 里选中 **SAMP-0004**（CLOSED、outcome 有、report 在）时的 `objectives 0` +
`TERMINAL_STATE`：验收里「无新选择时完成后 Focus=0」与「DELIVERED 是否释放责任取决于 required
结果」是**同一段代码的两个分支**，而它们在同一个 fixture 上各自有一个真实实例。

样本树的十一条里，**只有两条落在被检测的状态上**，其余九条是 `NOT_ASSESSED`：

```text
SAMP-0001   INBOX      未评估    ·  SAMP-0002  ACTIVE      未评估
SAMP-0003   DELIVERED  未结清（缺字段 outcome）
SAMP-0004   CLOSED     已完成
SAMP-0005   CANCELLED  未评估    ·  SAMP-0006  READY       未评估
SAMP-0007   ACTIVE     未评估    ·  SAMP-0101…0103         未评估
```

这九条不是「通过了」，是**没被测**，而输出把它们印成两句话。新增的读者面是 `work item <ID>`：
判定印在文档清单**下面**（在证据之后，因为它是从证据得出的结论），
`--json` 走的是同一个 `compose_item` 返回，所以两个面不会分叉。

#### 9.26.7 这一片没有做的事

- **`work admit` 还没有消费 `completion`。** 接续器用它决定「还占不占槽」，但准入记录本身
  （`runtime/agent_control_store.py` 的 admission）里没有这一项：一条在飞的目标被交付但缺证据，
  准入侧不知道。
- **`assessed_at` 是这一片引入的**，现在等于 `[DELIVERED, CLOSED]`。这意味着 `CANCELLED` /
  `WAITING` / `NEEDS_HUMAN` / `DELEGATED` / `INBOX` / `AVAILABLE` / `READY` / `ACTIVE` 都**不被
  测**。对取消来说这是对的（取消不是完成），但如果以后要问「取消的活有没有留下它该留的东西」，
  这个状态今天是空的。
- **`worker_stopped` / `resources_released` 仍然没有生产者**（与 §9.25.5 同）。§9.26.5 只是让
  第二族出现时不像第一族，没有给第一族补上生产者。**（2026-09-25 更新：前半句已被 §9.27 推翻
  ——`worker_stopped` 现在有生产者，是本机第一条真正接上的 liveness 腿；`resources_released`
  仍然没有，且 §9.27.3 说明了为什么这两个字段必须分开读）**
- **`not_completion: [DELIVERED, CANCELLED]` 不被任何代码读**。它留在契约里是因为它对人有信息
  （说清哪些状态不是「达成验收」），而契约里写明了这一点，免得下游把它当成一条会被执行的规则。
- **正式 Requirement 到 canonical Work Item 的幂等对应仍然 ABSENT**；首次真实 grant 走可信用户
  控制仍然 PARTIAL。两条都还在 P6 的剩余部分里。

### 9.27 P6 切片 C：`worker_stopped` 有了生产者，acceptance D 从死代码变成活规则

计划第 4 条（P6）原文里点名的两条，这一片对上的是「改动」的第二句与「验收」的第二句：

> **改动**：把 `focus_source.NOT_PRODUCED` 逐项变成从正确 owner 取得或精确 UNKNOWN，不硬编码「无
> worker/资源已释放/默认有 grant」。
> **验收**：… **旧 Run 终态但 worker 仍写不得释放** …

#### 9.27.1 为什么不是「再加一个动词去写 work-engine」

原打算在 serveros 侧加 `requirement adopt`（正式 Requirement 幂等地对应一个 canonical Work Item）。
调研否掉了它，理由在两个**本仓自己的声明式文件**里：

- `intake/global_board.py` 模块 docstring 原文：*"Nothing here reads the work-engine store. The store
  is the authority and it is on the other side of a process boundary this repository deliberately does
  not cross — `fleet/mcp-contract.yaml` owns that edge."*
- `intake/board-contract.yaml`：`work-item.read_through: serveros-work-mcp`。

serveros **不拥有**那条边，跨过去写 store 会违反本仓写下的规则。于是这一片改成：`NOT_PRODUCED`
里唯一**有正确 owner** 的那一项（`worker_stopped`）接上它的 owner，其余各项的文案收紧成「无人生产」
并各自说明理由 —— 这正是计划那句「从正确 owner 取得**或**精确 UNKNOWN」的后半截。

Work-engine 那一半（`create_candidate`：一条需求一个候选；冲突拒绝并点名**实际命中的那一半**键）
已在该仓提交 `cbe757a`，含 4 条测试与 2 个被杀的变异（`IS ?`、去掉 dedupe 那一半）。跨了仓边界，
所以台账只登记它的存在与这条边界理由。

#### 9.27.2 acceptance D：一条早就写好、却不可达的规则

`intake/daily_cycle.classify` 里这条分支在本变更之前就在，注释也是原文：

```python
if held:
    # BEFORE the state test, and that order is acceptance D: an Item whose
    # Run reached a terminal state while its worker is still writing has
    # released nothing.
```

它在本机**不可能为真**：`held` 来自 `liveness(row)`，而 `liveness` 只读行上真实存在的字段，17 行
board 一个都不带。也就是说这条注释描述的是**一段没有任何输入能触发的顺序**——验收项写在注释里、
写在文档里，而在唯一能跑的投影上恒假。这一片让它可达：`compose` 现在把 receipt 的答案写进行，
「终态行 + `worker_stopped: False`」第一次真的走到 HELD，并且有一条从**真 receipt** 到
`continue_focus` 的端到端测试（同一块 board、同一终态、同一 `DISCHARGED` 边界，只换 receipt）。

#### 9.27.3 receipt 上那个字段为什么不能直接读

`runtime/bounded_invocation.invoke_once` 在 spawn **之前**就把记录写成 `worker_stopped: False`，
只有 `START_FAILED` 且 `proc is None` 一条路径会把它置 `True`。所以：

- `False` 在其余所有路径上是**初始化值**，不是裁决；
- 读初始化值为裁决 = 把「这次调用不能宣称 worker 停了」读成「worker 停了」；
- `descendants_verified` 在整个 fabric 里**没有写入者**（实测：全仓唯一出现处就是那条初始记录），
  所以 `EXITED` 的诚实答案是**未决**——字段留空 + `RECEIPT:EXITED:DESCENDANTS_UNVERIFIED`，
  既不是 `False`（那会把每次跑完的派工永久留在飞，本机已经为此付过一次 OVER_CAPACITY 假警报），
  也不是 `True`。

三值读法（`True` 放行 / `False` 占槽未放 / `None` 此处未决 + 原因），表按 writer 自己的 status 逐条
转写；同一 Item 被多条 admission 命名时 **`False` 胜过 `None` 胜过 `True`**，因为问题是「还占不占」，
一次派工的 `True` 对另一次派工启动的进程什么也没说。字段名与原因随行输出：`work focus` 多一段
`liveness`，`work admit` 多一行，两者取同一个 `--receipts`、没有默认值。

#### 9.27.4 我自己先写错的一版：把初始值当第二个判据

第一版 `_receipt_liveness` 在查表**之前**先看 `direct_process_exited is False`。这个字段同样是初始
值，于是 `STARTED` / `START_FAILED` / `REFUSED` 三种状态被同一个理由吞掉，其中 `REFUSED`（这次
调用**什么都没启动**）会被答成「已启动、未记录结束」→ 永久占槽。真 receipt 实测：

```text
status REFUSED / reason OUTPUT_ALREADY_EXISTS / worker_stopped False / direct_process_exited False
```

修法：删掉那个第二判据，让 status 成为唯一读法（同一事实只在一处读），并补一条 REFUSED 的实测
测试。形状与 §9.22.4 相同：同一规则两份冗余判据，杀掉一份另一份还活着；不同点是这次两份都在
**新写的代码**里，是变异测试逼出来的。

#### 9.27.5 本机实测（2026-09-25 22:22 +08:00）

| 读数 | 值 |
| --- | --- |
| `work focus`（不带 `--receipts`） | `liveness RECEIPTS_ROOT_NOT_NAMED`；`receipts_root: null`；17 行里带 `worker_stopped` 的 **0** 行 |
| `work focus --receipts <空目录>` | `liveness NO_ADMISSION_NAMES_AN_ITEM`；`items: {}`；原因原文「This is the node's normal state and not a fault: nothing has been admitted, so nothing has a worker」 |
| `focus.not_produced` 的键 | `blocked_by, focus_disposition, grant_ref, next_action, parking_acknowledged, resources_released, scope_digest` —— **`worker_stopped` 已不在其中** |
| control store | `admissions 0 / grants 0`（与 §2 一致） |
| 本机 receipts 目录 | **不存在**。~~`invoke_once` 在本机没有任何调用者~~（**这句话现在不成立了**：§9.31 给了它第一个生产调用者 `serverosctl work dispatch`。目录仍然不存在，但原因换了——那个动词没有上弦，而且 live 的板是 `STALE_BOARD`、`grants 0`，所以还没有 admission 能被派工。同一格里的数字与其余四行照旧），所以哪怕有了 admission 也没有 receipt 可读 |

两条 live 答案都是**精确 UNKNOWN**，不是每行一个 `False`。也正因如此 `--receipts` **没有默认值**：
默认值会把「谁派的活」变成猜，而从一个没人跑过的目录里读出的 receipt 是对**另一次派工**的回答。

#### 9.27.6 变异：5 个全杀，文件按 digest 校验复原

| 变异 | 结果 |
| --- | --- |
| `EXITED` 答「held」而不是未决 | 杀（1 红） |
| `REFUSED` 答「released」 | 杀（1 红） |
| 多条 admission 时 released 胜过 held | 杀（7 红） |
| `compose` 不问答案都写字段 | 杀（3 红） |
| 状态判断挪到 held 判断之前（删掉验收 D） | 杀（3 红） |

复原不用任何 git 动词：脚本持有原文，写回后比对 `sha256`。这条纪律是当场学来的 —— 本轮早些时候
我用 `git checkout -- <file>`「还原」一次变异，**丢掉了整个未提交的重构**（该类动词从引用取值，
看起来像还原、实际是丢弃，退出码 0），只能凭上下文重写。

#### 9.27.7 这一片没有做的事

- **`resources_released` 仍然完全没有生产者**——不是 board、不是 store、不是 receipt。它在本机是
  「`worker_stopped` 的那对里的另一半」，文案已改成**与它分开**，并写明它**不是** `not worker_stopped`
  （一次派工可以停了自己的进程、同时握着租约）。**（2026-09-25 更新：这一条整体被 §9.28 推翻。
  owner 是租约 namespace，联结点是契约自己的 `work-item` scope；而且"与它分开"这句话当时是对的，
  错的是我把"分开"写成了"lease store 对是否交还没说任何话"——`release()` ＋ `release_verdict` ＋
  `references` 恰恰就是那份记录。错句形态保留在 §9.28.1。）**
- **`focus_disposition` / `parking_acknowledged` / `scope_digest` / `grant_ref` / `next_action` /
  `blocked_by`** 仍是卡字段无人建。
- **首次真实 grant 走可信用户控制仍然 PARTIAL**：`put_grant` 只被测试调用，没有 CLI 动词、没有
  daemon 方法、没有 MCP 工具。
- **`work admit` 的准入记录里不存 liveness**，只在输出里加了一行；而那一行**今天不可达**，两个
  各自独立的原因都已在别处记过：**(a)** 没有一行 board 携带 `grant_ref`（`NOT_PRODUCED` 里那项
  「卡字段无人建」，§2 的 live 读数也是「named 0 row(s)」）；**(b)** 没有任何 CLI 动词能记录一条
  delegation（`put_grant` 只被测试调用，§9.25 记的 PARTIAL）。所以 `work admit` 的成功块在本机
  走不到，准入必然停在 `NOTHING_SELECTED` / `MISSING_OR_REVOKED_GRANT` 的拒绝上——实测：隔离
  `DATA_ROOT` 里跑 `work admit --cycle acc-d-slice-c-1 --receipts <dir>`，输出
  `refused NOTHING_SELECTED`，拒绝分支在任何写入之前返回，隔离世界里**没有**生成 store（已核）。
  这一行留着而不删的理由：它是**报告**（人看到的准入是在哪一次 liveness 读之下做出的），不是
  守卫、也不是第二份规则；但要说明白——它在盘上，**今天打不出来**。要让它第一次打印，需要上面
  两条缺口先闭合，而「首次真实 grant 走可信用户控制」正是计划点名不许用 fixture 绕过的那条。
- **work-engine 的 `external_item_mappings` 那条 `INSERT OR IGNORE` 不可达**（实测：能让映射与候
  选冲突的状态在 `create_candidate` 就被拒绝，到不了那一步）。按「不为不可达状态加守卫」**只记录
  不加代码**。
- **receipt 是本片唯一的 live 缺口**：链在测试里以真 writer 自己的字节证明，但本机没有 receipt 可
  读，因为还没有任何东西通过 `invoke_once` 派活。第一个 live receipt 会由 P7/P8/P12 的运行时循环
  产生——在那之前，这一项的 live 状态是 PARTIAL，理由是精确的。
  **→ 2026-09-26 订正**：产生 receipt 的那个动词已经存在（§9.31 的 `serverosctl work dispatch`），
  但**它没有上弦、而且 live 上还不具备被调用的条件**（板 `STALE_BOARD`、`grants 0`，所以没有
  admission 可派）。所以这一项的 live 状态**仍然是 PARTIAL**，缺的不再是「没有生产者」，
  而是「生产者没有调度器，且它需要的准入在 live 上还造不出来」。第一个 live receipt 仍然没出现。

### 9.28 P6 切片 D：`resources_released` 接上它的 owner，以及「一对字段必须先被读全」

#### 9.28.1 owner 与联结点

`resources_released` 的真 owner 是 **`state/serveros/resource-leases/`**：`fleet/state-root-contract.yaml`
把它声明为 `class: authority`、`holds: current resource occupancy`、`producer: runtime/`、
`required: false`、`absent_means: NO_LEASES_HELD`。生产者是 `runtime/lease_store.py`，
读取走 `runtime/view.environment_effective`（`work focus` 里经 `view.environment_effective` 读，
而不是自己开 store —— 否则「这个 Item 还持有什么」会有第二份推导，和 `lease environment` 那份
在有人改其一之前都一致，而漂移的那份没人读）。

**联结点是契约自己的 `work-item` scope**，不是名字匹配：`runtime/environment-contract.yaml#scopes`
对 `work-item` 的说明是「一个 Work Item 持有它；Item 关闭时回收」，所以一个落在该 scope 下的
holder **就是**一个 Item。而且 `lease_store.declare` 把 scope 当资源的属性（顶层 `scope` 与任何被
声明引用的 `scope` 不等即 `Rejected`），所以「按 scope 找资源」是 store 自己的设计而不是本模块的猜。

**订正一条我自己写错的句子**（§9.27.7 原文，形状留着好认）：它说租约 store「says nothing about
whether it was given back」。这是错的，错得挺具体：`release()` 就是「交还」这个动作本身，
`release_verdict` 是它的裁决，`references[]` 是交还之后留下的东西。我上一轮没读这个 store 就
替它说了话，和这个仓库里反复记的那类病同形——**替一个没读过的 producer 断言它没生产**。

#### 9.28.2 三值，以及为什么 EXPIRED 不是「已释放」

`intake/focus_source.resources_released_by_item` 只读 owner 已经产出的两个字符串：引用的 `scope`
与 `state`（LIVE / EXPIRED / RETIRED，由 `runtime/environment.reference_state` 推出）。取值：

| 引用的 `state` | 该 Item 的 `resources_released` | 理由 |
| --- | --- | --- |
| `LIVE` | `False` | 还在被持有 |
| `EXPIRED` | `False` | `environment._reference_row` 自己说它「no longer backed by anything」，且**保留可见**。崩溃的持有者不是交还了东西的持有者；答 `True` 等于把崩溃读成归还 |
| 其它 / 缺席 | `None`（`REFERENCE_STATE_NOT_KNOWN_HERE:<state>`） | 这个名字本模块不知道，就不认 |
| namespace 读不了 | 字段**不上行**（`LEASES_UNREADABLE`） | 空列表本身就是 `NO_LEASES_HELD` 这个断言，两者不能互串 |

`RETIRED` 不会出现在这里：离开 `references` 的引用只活在 `history` 里，所以存储行永远不会携带它。

#### 9.28.3 两个词，而不是一个

namespace 里**什么都没有**（`rows` 为空）与 namespace 里**有别人的东西、只是没有本 Item 的
`work-item` 引用**，是两个不同的发现，所以是两个词：`LEASES_NAMESPACE_EMPTY` 与
`NO_REFERENCE_NAMED_BY_THIS_ITEM`。**`NO_LEASES_HELD` 这个词不在这里再拼一次**：它属于
`view.environment_leases` 的封套，而本函数拿到的是派生文档。上一版我写的是「用契约自己的词」，
但 `view.environment_effective` 的输出**根本不带 `absent_means`**（那是 `view.environment_leases`
的字段，而且是 `view.py` 里的字面量、没人从契约读）——照写就是一条读不到的作者权威。现在两个词
由本模块自己拥有，并注明与 `view.environment_leases` 的对应关系。

#### 9.28.4 本机 live 读数

`state/serveros/resource-leases/` 在本机**不存在**（目录没建），契约 `required: false`、
`absent_means: NO_LEASES_HELD`。`work focus` 的实测：

```
liveness    RECEIPTS_ROOT_NOT_NAMED
leases      PRODUCED
  LIT-0101   released         no resource  LEASES_NAMESPACE_EMPTY
  …（17 行同样）
  SRV-0005   released         no resource  LEASES_NAMESPACE_EMPTY
```

**这是对空 store 的正确回答，不是「live 已验证为非空」。** 一旦 namespace 里出现一条 `work-item`
租约，同一个字段会翻成 `False` 并让该行 `WORKER_OR_RESERVATION_HELD:resources_released`——
那条路径由真 store 的真字节在测试里证明（`tests/test_focus_source.py` 的
`ResourceOccupancyIsReadFromTheLeaseStoreThatOwnsIt`，10 条：LIVE / 别的 scope 的持有者 /
`ttl_s=-60` 的 EXPIRED / shared 与 exclusive 两种释放后形态 / 空 namespace / 别人的记录 /
UNREADABLE / 不给 environment / 非本 owner 的文档 / 终态行＋活租约）。本文件里没有一处
`json.dump` 造租约：问的是**唯一那个生产者**记了什么、**唯一那份推导**说了什么。

`work focus --json` 另两项实测（同一次运行）：`focus.leases.status` = `PRODUCED`、`items` 17 个；
`focus.not_produced` 的键集**恰好是六个卡字段**（`blocked_by`、`focus_disposition`、`grant_ref`、
`next_action`、`parking_acknowledged`、`scope_digest`），`resources_released` **已不在其中**——
这是「字段离开 `NOT_PRODUCED`」这句话在 live 输出上的复核，不是靠读代码得出的。同一行的
`resources_released: True` ＋ `worker_stopped` **不在行上**，就是 §9.28.5 那个缺陷在真实数据上的
样本（也是修好之后的第一份样本）。`continuation.unevaluated` 为空**不能**读成「liveness 已判定」：
板是 `STALE_BOARD`，两个函数都在 `blocked(...)` 早返回，`unevaluated` 恒空——这两件事在这一行
输出里长得一样，是它必须被写明的原因。

**严格化在真实路径上的代价是零，这一点是核过的。** `runtime/cli.py` 的 `_focus_decision` 同时读
两个源（`--receipts` 命名的那份 receipt ＋ 无 `--state-dir` 的租约 namespace），所以 CLI 走到的那
些行要么两个字段都在、要么两个都不在；「只读到一个」只出现在只给一个源的组装里（测试，以及
把 `work focus` 的 `--receipts` 省略掉的 CLI 行——那时 worker 那一半本来就没被看，答 `None` 正是
正确的话）。

#### 9.28.5 这一片自己引入、又自己修掉的一个真缺陷（重要的不是修法，是它怎么露出来的）

上表那四行 live 输出跑出来之后才发现：`liveness` 当时的规则是「只要有字段被读到、且它们全是
`True`，就答 `False`（这个 Item 名下什么都没有）」。而本机每行只带 `resources_released: True`
（receipt root 无人命名），于是**17 行全部读成「已释放」**——对 worker 那一半的断言，建立在一个
没人看过的源上。后果不是措辞问题：

- **acceptance D 禁止的那个释放，只要不写 `--receipts` 就能达成**（终态 Run、worker 仍写 → 被当成
  已释放）。这正是计划里点名不许发生的方向。
- `focus_disposition: PARKED` 的行会因为缺字段而被当作已释放并从 park 里放出来。
- `unevaluated` 归零：原本「这一行是在较弱证据上被继续的」这句话，被换成了一句更强的、不成立的话。

修法是把一个字段说的话与一对字段说的话分开：**一个字段答非 `True` 就足够 `True`（负向不需要
法定人数）；`False` 只在两个字段都被读到且都答 `True` 时才给；其余答 `None`。**`classify` 新增
`liveness_fields_read` / `liveness_fields_missing`，两处 `unevaluated` 条目改成报**缺的那些字段**
（旧文案对着一行已经读到 `resources_released` 的行说它「两个字段都没有」，是同一类假话）。
CLI 的标题从「这些字段没有生产者说明」改成「这些行在这些字段上没有答案」——两个字段现在都有
生产者了，而「有没有生产者」与「这一行带没带」是两件事；末尾那句「同样字段」也改成按尾部条目的
真实字段集打印（那句话可能是假的）。

这是 §9.27 那个缺陷的**镜像**：那次是「缺席被读成 held」，这次是「缺席被读成 released」，
两次都是把「没人说」当成了一份陈述。前者在本机被量到过（17 行全 carryover、OVER_CAPACITY 误报），
后者是被这次 live 跑出来的。**教训精确到可复用：一个字段有了生产者，不等于那一对可以只读一半。**

#### 9.28.6 变异：8 条判据各自承重

改一处判据 → 跑 `tests/test_focus_source.py tests/test_daily_cycle.py` → 每条都必须死：

| 变异 | 结果 |
| --- | --- |
| M1 `EXPIRED` 也答「已释放」 | 杀（2 红） |
| M2 联结点改成名字匹配（不看 `work-item` scope） | 杀（1 红） |
| M3 空 namespace 不再算答案（改答未决） | 杀（1 红） |
| M4 读不了的 namespace 当成「什么都没持有」 | 杀（1 红） |
| M5 `compose` 不再输出 `leases` 块 | 杀（9 红） |
| M6 一个字段答 `True` 就算已释放（**旧的宽松读法**） | 杀（6 红） |
| M7 缺的字段一律报成整对（不看读到没读到） | 杀（1 红） |
| M8 `held` 判断挪到「全都读到且都答 `True`」之后 | 杀（8 红） |

M6 杀 6 条、M8 杀 8 条，就是「§9.28.5 那条严格化是承重的」这句话的尸体。复原不用任何 git 动词：
脚本持有原文，写回后比对 `sha256`，每轮清 `__pycache__`（同尺寸同秒的 pyc 会骗过校验）。

#### 9.28.7 这一片没有做的事

- **`worker_stopped` 的 live 状态仍是 PARTIAL**，理由与切片 C 相同且精确：本机不存在任何 receipts
  目录，因为还没有任何东西通过 `invoke_once` 派活。第一个 live receipt 会由 P7/P8/P12 的运行时循环
  产生。**→ 见 §9.27.7 同一条上的 2026-09-26 订正**：派工动词已经存在但没上弦，而它需要的准入
  在 live 上还造不出来，所以这一项**照旧 PARTIAL**。
- **CLI 里那个 `unevaluated` 渲染块在本机不可达**：板是 `STALE_BOARD`，`select_focus` 与
  `continue_focus` 都在 `blocked(...)` 早返回，`unevaluated` 恒为空。所以那两句话（改前的与改后的）
  在本机**都从未被打印过**，而且这个块没有测试覆盖——它要等板的生产者存在才会第一次输出。如实记下，
  不因为它不可达就把改动省掉：它一旦可达，就是一句会被读的话。
- `focus_disposition` / `parking_acknowledged` / `scope_digest` / `grant_ref` / `next_action` /
  `blocked_by` 仍是卡字段无人建（与 §9.27.7 同）；「首次真实 grant 走可信用户控制」仍 PARTIAL。

### 9.29 P7 切片 A：一个闸门问的两个问题被拆开，以及一次 live 跑出来的第二个死锁

入口是计划 §P7 那一行（`docs/CC-EXECUTION-PLAN.md:124`）与基线第 74 行：

> 「**先修实际死锁**：`recipes.work`当前在派发之前用worktree历史差异判断进展，NONE即
> NO_PROGRESS；新任务尚未执行、本来没有历史产出，因此可能永远无法开始。准入检查应回答能否
> 开始，进展观察应在真实启动后的有界窗口回答是否卡住；no-code任务也不强制需要Git差异/代码
> 工作树。」

#### 9.29.1 那个死锁精确到什么程度：它是**一个**闸门在同一个位置回答了两个问题

改前的 `work` 在派发之前只有一道 progress 判断，而它同时被当成准入用。两个问题不同：

| 问题 | 该由谁答 | 改前由谁答 |
| --- | --- | --- |
| **这一次能不能开始？** | 人已经授权过的选中项（`today.yaml` + `classify`） | 本周期 worktree 差量：新任务本来没有产出 → `NONE` → `NO_PROGRESS` |
| **上一次是不是卡住了？** | 那一次尝试**结束之后**的窗口里，有没有源答过 | 同一个判断，窗口也取自 period |

两个方向都错：新任务永远不能开始（它的「产出」按构造就不在这个窗口里），而一个真的有产出的
尝试，只要它自己的产出落在同一个 period 窗口内，就会被算成「有进展」——**一次尝试自己的产出
是「它没有卡住」的反面证据**。所以修法不是把闸门挪走，而是把两个问题分给两个位置：

- **准入**只读授权选中（`ready`），不需要任何源，因此第一次尝试直接进派发；
- **watch**只在上一次尝试**结束之后**问「有没有动过」，窗口是**两个读到的时刻**
  （`earlier.finished_at` 与本次运行的钟），不是 period。

`runtime/recipe-contract.yaml` 的 `NO_PROGRESS.means` 同步改成「观察的是这一次 scope 的更早
那次尝试」，并写明**没有更早尝试的 scope 永远不会在这里被停**——「还没开始」不是「没有进展」。
`work.steps` 的 `progress` 改名 `watch`，`work.stops` 补上 `IN_FLIGHT_OR_ABANDONED`
（它一直是引擎会产出的词，改前只是没写进这一行的闭集里）。

#### 9.29.2 「更早的那次尝试」从哪来：不加新存储，但四个坑都在同一个目录里

主体是 `_earlier_attempt(ctx)`：读**这个 scope 自己的记录目录**，按「时刻、文件名」排序取最新
的一条。不新增任何存储，因为记录目录本来就是「这个 key 有没有跑过」的唯一权威
（`fleet/state-root-contract.yaml` 把「路径即 key」写成了设计而不是巧合）。四个坑：

| 坑 | 为什么这是个坑 | 处置 |
| --- | --- | --- |
| **本次运行自己的记录** | 引擎在调 loop **之前**就写下 CLAIM（这正是崩溃可见的原因），所以 loop 一开始跑，目录里最新的一条就是它自己：读回来会把**每一次第一次尝试**读成「刚刚结束、没有产出的一次更早尝试」，也就是要修的那个死锁换成反面的面具 | 按文件名排除自己那一条（在同一个目录里比，与两个根怎么写无关） |
| **拒绝副本的时刻** | `--retake` 的提示语指向「重跑那个 period」，而**重跑遭遇 `_refuse` 时会把被拒记录的时间戳抄进副本**（`state`/`key`/`claimed_at`/`finished_at` 都抄）。若只按文件名排，一个更晚 period 的拒绝副本会压过真正更晚结束的那次尝试 | 排序键是 `(finished_at or claimed_at, 文件名)`：时刻在前，因为被拒副本携带的是**很久以前**的 claim 时刻；文件名只在同秒时做 tie-break（与 `last_success` 同规则） |
| **放不进时间顺序的记录** | 一条能 parse、但没有可读 `finished_at`/`claimed_at` 的记录**没有位置**。按字符串排的话 `"None"` 排在 ISO 时刻**之后**，于是它会赢下这一次扫描——那是在替记录发明它读不到的那个事实 | 单独收进 `unread_records` 并**报出来**（不静默跳过：静默跳过会让这次运行拿一条更老的尝试当最新的那次来判断） |
| **一个源没答不能拖垮答了的源** | `perception` 立的规矩在记录库这一侧同样成立 | 可读、可定位的记录照常参与；读不了的只被**点名**，不阻断 watch |

#### 9.29.3 三个值、一个没有 worktree 的块，和一个「能 parse 但不是对象」的挡板

- **`progress_verdict` 的三值原样使用**：`NONE` 只在「至少有一个源答了、且两条源都是零」时给出；
  「没有任何源答上来」是 `UNKNOWN` → `SOURCE_UNAVAILABLE`。这条线在日报里已经画过一次，这里读
  它的裁定而不是自己再画一条。
- **`_no_worktree_block()`** 是 `measure_worktree` 那个块的**缺口形态**（`answered: False`），
  不是「检出来是空的」。**这正是 §9.28.5 的镜像**：那次是「缺席被读成 released」，这次是「没有
  这个源」不能读成「这个源答了零」——否则一个 no-code 的 scope 会被判成「没有动过」。
- **`read_record` 的非对象挡板**：一个能 parse 成 list 的 JSON 以前会从 reader 里抛出来，于是
  「有人留了一个这个产品读不了的文件」被读成「这个产品坏了」。现在它与 parse 不出来走同一句话。
- **`_watch_window` 的两条读不到的边**（M14/M15 各钉一条，都是单测级、都真的可达）：一条能被
  `claimed_at` 定位、但**没有可读 `finished_at`** 的记录，答的是「观察不了，缺的是这个字段」
  而不是 `None` 往下抛——「结束时刻没人写得出来」不等于「没有动过」；而起点晚于本次运行的钟
  （两个时刻互相矛盾）是**拒绝**而不是夹紧，夹紧会挑一个印出来当作测量结果。

`work` 里那份 `--worktree` 缺席的块与 `--run-dir` 缺席的块**同时在工作**，所以 no-code 的 scope
在**有 Quay 账本**时真的会被观察到并派发（`test_a_no_code_scope_is_watched_through_the_source_it
_does_have`）；两个源都没有时它只能准入一次，那个缺口被**命名**为 `SOURCE_UNAVAILABLE` 而不是
掩饰成 `NO_PROGRESS`（下一片用 work-engine 自己的 attempt 记录当主源，那才是它的第一类公民形态）。

#### 9.29.4 变异：15 条判据，15 条都死

改一处判据 → 跑 `tests/test_recipes.py` → 每条都必须死。脚本
`/data/home/vince/artifacts/tmp-p7/mutate_slice_a.py`；复原不用任何 git 动词（脚本自持原件、
写回后比 `sha256`），每轮清 `__pycache__`（同尺寸同秒的 pyc 会让「这条判据活着」成为缓存造出来
的假结论）。

| 变异 | 结果 |
| --- | --- |
| M1 把自己这一次的 CLAIMED 记录也算进「更早的尝试」 | 杀（12 红） |
| M2 只按文件名排序，不看时刻 | 杀（1 红：`…retake_that_the_refusal_points_at_unsticks_the_scope`） |
| M3 删掉「上一次只 CLAIM 没有结果」这一支 | 杀（2 红） |
| M4 观察窗口的起点取自 period | 杀（5 红） |
| M5 观察窗口的终点取自 period 的 end | 杀（4 红） |
| M6 没有 `--worktree` 的 scope 报成「读到了，是空的」 | 杀（1 红） |
| M7 恢复「没有 worktree 就不准开始」这条旧闸门 | 杀（1 红，见下） |
| M8 删掉 `read_record` 的非对象挡板 | 杀（1 红） |
| M9 把「放不进时间顺序」的记录直接拿去排序 | 杀（1 红） |
| M10 一份都读不出来的 scope 当成第一次尝试放行 | 杀（2 红） |
| M11 把 `--records-root` 的原始参数直接交给 Engine（**修之前那一行**） | 杀（1 红） |
| M12 删掉「root 必须是绝对路径」这条拒绝 | 杀（2 红） |
| M13 拒绝时先报派生出来的 `--records-root`，而不是调用者真正打的那个 flag | 杀（1 红） |
| M14 起点读不出来也照样给一个窗口 | 杀（1 红） |
| M15 两个时刻互相矛盾时夹紧（clamp）而不是拒绝 | 杀（1 红） |

**表里的红数取自跑在终态字节上的那一轮**（`15/15`，baseline `76 passed, 6 subtests passed`）。
其中 M1 / M4 / M5 / M12 四条的红数比这轮早些时候记录的多（12/5/4/2，而不是 11/4/3/1）：
这不是判据变强了，是**套件在这几轮之间长大了**，同一条变异被后来新增的测试多杀掉几条——
把红数当成「这条变异有多坏」是读错了它，它只是「这一轮有几条测试因它而红」。

**M7 第一次是活的**（那一轮脚本里是 12 条，它是唯一一条没被杀掉的），而它正好指出了测试里一个真空洞：改前
「没有 worktree 就不准开始」与「没有源答得上来」**产出同一个词**（`SOURCE_UNAVAILABLE`）、同一
个退出码、同一个空的 advance 列表——两次运行**只有靠「读到了什么」才能分辨**。补的那条测试
（`…is_watched_through_the_source_it_does_have`：Quay 账本里有窗口内的一条闸门记录）才让两条路分开。
**教训是「同一个词可以盖住两个事实」，而不是「测试数不够」。**

#### 9.29.5 live 读数：同一台机器上跑出来的第二个死锁（不是这一片引入的）

`recipe run` 在这个节点上**一次都没跑过**（下面这次之前 `state/serveros/recipes/` 不存在；这次
把它建了起来，里面只有这两条读数）。先在活节点上跑一次默认形态（真 records 根、`--budget 0`）：

```text
recipe      work
key         work|project:serveros|20260925T0900+0800-20260925T1000+0800
state       STOPPED  previous NEVER_RUN
stopped     FAULT — TypeError: unsupported operand type(s) for /: 'NoneType' and 'str'
model       0 call(s) of a ceiling of 0 from --budget
```

原因不在 loop 里：`cmd_recipe_run` 把 `--records-root` 的**原始参数**直接交给 Engine，而它省略时
是 `None`；`work` 的第一件事是 `today_path(records_root, project)`，于是 `None / "projects"`。
**这个文件里其余每一个动词都从声明根取默认值，只有这一个没有**——而省略 `--records-root` 正是
定时器会发出的形态。修在 CLI（默认取 `Paths().root("workspace")/records`、非绝对路径拒绝、拒绝时
报**调用者真正打的那个 flag**），并且在 Engine 存在**之前**判，所以一个可笑的参数不会先占下 key。
修后同一棵树、同一个 period：

```text
state       STOPPED  previous STOPPED
stopped     QUEUE_UNPRODUCED — no selection was recorded at /data/home/vince/workspace/records/projects/serveros/board/today.yaml
model       0 call(s) of a ceiling of 0 from --budget
step        queue: the authored selection is UNPRODUCED
```

**这就是本机今天的真话**：loop 走到了它的第一步、读的是一个**没人写过**的今日选中项，并且
`QUEUE_UNPRODUCED` 与 `NO_PROGRESS` 是两个词。**watch 在本机还观察不到**，因为四个项目
（`litellm` / `multi-user-claw` / `serveros` / `tokyo`）**没有一个**有
`board/today.yaml`——写今日重点是**人的动作**（或 P8 例程的动作），不是这次改动能替它产生的。
这与 §9.25.4 记的 `accepted UNPRODUCED` 是同一个缺口，现在它在 recipe 这一侧也被真的走了一遍。

另外记两件事，**都不是这一片修的**：

- **一次 STOPPED 记录上的重跑会把上一次的 `stopped_because` 覆盖掉**（记录说的是这个 key 现在
  怎么样，不是它历来怎么样；上一次的痕迹只剩 `previous_state`）。上面那对前后读数因此只活在
  terminal 里，盘上留下的是后一次——这不是缺陷，但读记录的人要知道。
- **`FAULT` 的退出码与 `cmd_recipe_run` 自己的 docstring 不一致**：docstring 说「1 a fault」，
  而 `exit_code_for` 只按契约的 `normal` 分档，`FAULT` 是 `normal: false` → 实际退出 2。上面那
  次 FAULT 实测就是 `exit=2`。改它会动一条公开的退出码面（读它的是调度器），归到 P8/P9 接线时
  一起定，**不在一片里顺手改**。

#### 9.29.6 我自己的测试把整库弄红了一条线（这一片自己引入、自己修掉）

新测试类要给 `Paths().root("workspace")` 一个替身，而它的 `addCleanup` 写成了
`setattr(self.cli.paths_mod, "Paths", self.cli.paths_mod.Paths)`——**在打补丁之后取的值，
也就是那个替身自己**。`paths_mod` 是这棵树里每个模块都从它那里取 `Paths` 的**同一个模块对象**，
于是替身活过了这个测试，覆盖了后面每一个用到根契约的文件。全量第一次跑：

```text
17 failed, 5680 passed, 20 skipped, 1 warning, 3947 subtests passed in 812.08s
```

17 条分布在五个文件里（`test_runtime.py` 4、`test_serverosctl.py` 3、`test_state_root.py` 8、
`test_view.py` 1、`test_workchain.py` 1），报的全是同一句话：

```text
AssertionError: `recipe run` asked the root contract for 'home'; the declared root this path may take is `workspace`
```

**`tests/test_recipes.py` 单独跑从头到尾都是绿的**，变异 15 条也全是它自己一个进程里跑的——
所以这一条只有整库能看见。修法是把原件在打补丁**之前**取下来（`original = ...Paths` 再
`addCleanup(setattr, ..., original)`）。修后那五个文件 + 本文件同跑 **455 passed, 12 subtests
passed**（12.71s），全量见 §5 那一格。

教训不是「cleanup 要写对」，而是两条可复用的：**打补丁的模块对象如果是全树共用的那一个，
泄漏的范围是整棵树而不只是这个文件**；以及**单文件绿 + 变异全杀，仍然可能是一次整库红**——
这正是每一次收口都要跑一遍全量的理由（本轮的算术也可以自证：5683 ＋ 本片 14 条 ＝ 5697 总数，
17 条失败 + 5680 通过正好是 5697，**没有一条是产品逻辑被改坏**）。

#### 9.29.7 这一片没有做的事

- **~~`report` 面同一个病的兄弟还在~~ → 已由 §9.30 结掉**：`recipe run --recipe report` 在
  `ctx.worktree is None` 时仍然直接 `SOURCE_UNAVAILABLE`（`runtime/recipes.py` 的 report 循环）。
  它比 `work` 轻——一份日报确实可以没有代码差量——但「没有 worktree」与「worktree 读不了」在
  那条路上仍然是一个词。（原文保留，处置见 §9.30。）
- **no-code 且两个源都没有的 scope 只能准入一次**（见 §9.29.3）。下一片把它换成 work-engine
  自己的 attempt 记录当主源。
- **`work focus` 的板仍是 `STALE_BOARD`**，`select_focus` / `continue_focus` 都在早返回，
  §9.28.7 记的那两个渲染块在本机仍然不可达。
- **准入/观察都还没有上弦**：没有任何 timer 跑 `recipe run`（§12.5 的 Daily Cycle 同样没上弦）。
- **「从声明根取 records 根」这段解析在 `runtime/cli.py` 里已经被抄了第五遍**（§9.29.5 的修法
  与四个兄弟面逐字同形）。这一片照抄而不是抽公共函数，因为抽它会碰到四个与 P7 无关的命令面；
  **该抽的时候是接线那一片**，那时才会有第六、第七个调用者来证明它值得抽。

### 9.30 P7 切片 B：一个缺席的两种拼法，和一个被让出去但没被退休的词

#### 9.30.1 那个死锁精确到什么程度：同一个缺席，两种拼法，两个答案

`report` 循环里那一行是：

```python
    if ctx.worktree is None:
        ctx.unbound("SOURCE_UNAVAILABLE",
                    "no worktree was given, so the period's work delta cannot "
                    "be read. An absent path is a GAP in the document, and this "
                    "loop does not write a GAP where it could have measured")
```

它旁边就有一个**同一个缺席的另一半**，而且它的答案相反：同一份 `ReportTest` 里紧挨着的一条
测试（`test_a_worktree_that_is_not_a_checkout_is_a_gap_not_zero_commits`）钉的是「给了
`--worktree`、那个路径不是检出」——那一条**照样把日报写出来**，缺口照样印在纸上。两条并排读：

| 调用方打的字 | 引擎读到 | 这一次的结果 |
| --- | --- | --- |
| 不给 `--worktree` | `None` | `STOPPED / SOURCE_UNAVAILABLE`，**一个字都不写** |
| 给一个不存在的路径 | 一个字符串 | `DONE`，日报写下缺口，exit 0 |

**同一次缺席，只因为拼法不同就有两个答案。** 而两者里没有一个能分辨它面对的是哪一种处境：
一个 no-code 的 scope **没有检出可以给**（计划自己的话：「no-code任务也不强制需要Git差异/
代码工作树」），一个忘了打的调用方**是在要求这份循环把洞发布出去**。这里没有第三种输入可以
用来区分它们，所以按「缺 worktree 就停」写的后果是确定的：**no-code 的 scope 永远发不出一份
日报**——不是「这一次不行」，是这个 scope 在整条生命周期里一份也发不出来。

那一行自己的注解与它旁边那条测试的 docstring 直接矛盾：注解写「this loop does not write a GAP
where it could have measured」，而那条测试的 docstring 写「This is the one place the loop does
NOT stop on a source that did not answer, and §7's own normal stop for this loop is why:
本期生成完」。两句都是这个循环该守的规矩，冲突的是**缺席**该按哪一句算。

#### 9.30.2 改法：不是把闸门挪走，是让两个拼法回到同一个读数

`report` 的 `measure` 步骤改用 `work` 那个循环已经在用的缺口块：

```python
    work_delta = (_no_worktree_block() if ctx.worktree is None else
                  daily.measure_worktree(Path(ctx.worktree), window, fallbacks))
```

`_no_worktree_block()` **一个字都没改**（除了 docstring 里补上「两个循环都读它」与那句指向
`report` 的 `measure` 步骤的注解）。这一片刻意不新造第二个形状：两个循环要是各自有一份「没有
检出」的形态，那就是这一次要修的那个病换了个地方住。

**这个取舍是明写在纸上的，不是顺手让掉的**：停在原地时，一个忘了打 `--worktree` 的定时器会
在日志里被喊一嗓子；改成缺口之后，它拿到的是一份**带一个被点名的洞**的日报。让这个取舍站得
住的不是「反正日报更重要」，是两条读数都在纸面上、且两条都指向同一个缺席：

* 运行记录里 `measure` 步骤的 `worktree` 字段是 `None`（**不是字符串 `"None"`**——改前那一行
  是 `str(ctx.worktree)`，缺省时会印出一个叫 `None` 的路径；见 9.30.4 的 N3）；
* 文档的「提交」一节印的是 `**没有答案。** 这一次没有给 --worktree，…`。

而这份文档**绝对不能**做的那件事，被 N2 那条变异当场演出来了（见 9.30.4）。

#### 9.30.3 让出去的只是「停」，不是那个词

`SOURCE_UNAVAILABLE` 仍然在 `report.stops` 里，而**一条声明只有在还有东西能产出它的时候才是
真话**。这一片删掉的是这个循环里产出它的**一条**路径，不是这条词。剩下的那条路径是引擎级的：
键自己的记录读不出来时，`Engine.run` 照样用这个词停下来——**给不给 `--worktree` 都一样**。
所以新加的第三条测试（`test_the_word_this_loop_gave_up_is_still_reachable`）钉的不是这一次的
修法，而是**这一次没有顺手拿走的东西**：没有它就没有任何东西能分辨「这个词还有人产」与
「这个词已经没人产、只是还写在契约里」。

#### 9.30.4 变异：4 条判据，4 条都死

脚本 `/data/home/vince/artifacts/tmp-p7/mutate_slice_b.py`；复原不用任何 git 动词（脚本自持
原件、按 sha256 比），每轮清 `__pycache__`。

| # | 把哪条判据弄坏 | 结果 |
| --- | --- | --- |
| N1 | 把「没有给 `--worktree`」退回成一次 `SOURCE_UNAVAILABLE` 停机（改前那一行） | KILLED（`test_a_scope_with_no_worktree_at_all_still_gets_its_report`） |
| N2 | 把缺口块报成 `answered: True`（「源答了、是空的」） | KILLED（3 条：`TheAttemptWatchTest::test_a_no_code_scope_with_no_readable_source_says_so` ＋ `ReportTest` 两条） |
| N3 | 缺省时印 `str(ctx.worktree)`（改前的形状，一个叫 `None` 的路径） | KILLED（`test_a_scope_with_no_worktree_at_all_still_gets_its_report`） |
| N4 | 条件表达式的另一臂：两个拼法都不再报出「到底给了什么」 | KILLED（`test_the_two_spellings_of_no_checkout_reach_one_reading`） |

**N2 值得单独读一遍**，因为它把这一片要防的那个错原样演出来了。把缺口块改成
`answered: True` 之后，两条 report 测试红在同一句话上：

```text
AssertionError: '缺口' not found in
'工作树答了：窗口内一条记录都没有。这不是「没查」——是查了，结果是零。'
```

**「没有这个源」被渲染成了一句安静的周期。** 这就是 `progress_verdict` 那三个值存在的全部
理由，也是这条判据不能只靠「测试数够不够」来保证的例子。

#### 9.30.5 live 读数：同一棵树、同一个动词，改前改后各跑一次

在这台机器上真的跑 `serverosctl recipe run`，两次都**不给 `--worktree`**，只换 period
（键是 `recipe+scope+period`，不换就只会读到单飞闸门）：

```text
改前（把那一行放回去）：
state       STOPPED  previous NEVER_RUN
stopped     SOURCE_UNAVAILABLE — no worktree was given, so the period's work
            delta cannot be read. …
step        window: …      step        receipts: …
            （两步，到此为止；`--out-dir` 下**没有**任何文件）

改后（提交的这一份字节）：
state       DONE  previous NEVER_RUN
stopped     COMPLETED — the finite step list ran to its end
step        window · receipts · measure: the verdict for this window is UNKNOWN
step        compose: wrote …/daily-report.md and …/publication-receipt.json
```

写出来的那份文档里，两句话是它自己要说的：

```text
**本周期进展：UNKNOWN。** 没有任何一个源答上来：工作树与 Quay 都返回了缺口。
**这不是「没有进展」。** 没答和答了零是两件事，…
**没有答案。** 这一次没有给 --worktree，所以这个 scope 的工作差量没有 git 这一个源。
这不是「检出来是空的」——是没有检出可读。…
```

两次跑记录进的是**真 state 根**（`state/serveros/recipes/report/project-serveros/`），
`--out-dir` 在 `ARTIFACTS_ROOT` 下。改前那一跑留下的记录不删：它是「同一个动词上一次是这样
答的」的凭据。

#### 9.30.6 这一片没有做的事

- **契约里那段散文没有判官。** `runtime/recipe-contract.yaml#recipes[report].steps[measure]`
  这次被改写了（写清「一个没答的源让那一节说自己没有答案，不让文档停下」），`runtime/cli.py`
  的 `--worktree` 帮助也改了（P7 计划里那句 no-code 不强制要工作树）。**两者都是散文，没有一条
  测试读它们的措辞**（契约里被测试读的是**闭集**：`stops` 的词表、`model_steps` 的 `needs_model`）。
  这是这一片最弱的一处，写在明处：改一个没人读的句子，绿不绿都不说明它对。
- **`report` 这一面仍然没有回执生产者**：本机没有 timer 打这个动词（§12.5 的 Daily Cycle
  同样没上弦），所以 9.30.5 那两次是手工打的。它证明的是**这个动词现在会答**，不是
  「日报每天都在发」。
- **no-code 且两个源都没有的 scope 只能准入一次**（§9.29.3／§9.29.7 第二条）**仍然没修**。
  这一片动的是 `report` 的缺席，不是 `work` 的观察窗口；那条要等 work-engine 自己的 attempt
  记录当主源，是另一片。
- **`work focus` 的板仍是 `STALE_BOARD`**（§9.28.7／§9.29.7 第三条），四条里没动。

### 9.31 P7 切片 C：第三个相位有了生产者，`invoke_once` 有了第一个生产调用者

#### 9.31.1 那个缺陷的形状：一个相位有读者、没有生产者

`runtime/agent_control_store.py` 的三个相位是

```python
ADMISSION_PHASES = ("INTENT_RECORDED", "LEASE_HELD", "DISPATCHED")
```

前两条各有自己的生产者，而且都已经在生产链上（§9.27 的 `worker_stopped`、§9.28 的
`resources_released`）。第三条**一个生产者都没有**：

- 读者是有的，而且不止一个：`intake/focus_source.py` 为每一条准入去读
  `<receipts>/<action_key>.json`；`serverosctl work confirm --phase DISPATCHED` 把那个文件说的
  话记下来。**读的那个文件，全机没有任何一条生产路径会写。**
- `runtime/bounded_invocation.invoke_once` —— 那个文件唯一的写者 —— **一个生产调用者都没有**。
  这件事 `runtime/actuator-contract.yaml` 不是漏了，是**明写在纸上**的（两条 `invoke_once`
  条目的 `why` 里各有一句「No production caller exists on this node today — measured」）。
- `work admit` 自己的收尾句把缺口点了名：「the lease is declared and the dispatch made by
  whatever performs them」。**这句话当时指向一个不存在的东西**。

后果是确定的，而且不是「不方便」：`DISPATCHED` 这个相位唯一的到达方式是**有人伪造一个
receipt 文件**——也就是这个相位存在的全部理由（「有人真的跑过一个命令，回执在此」）恰恰是
生产路径上唯一做不到的那件事。一条相位只有一个读者、没有生产者，和一条相位写了没人读，
是两个方向同一种病。

#### 9.31.2 为什么是一个动词，不是两个

「跑命令」与「记下跑了」分开写会更好复用，这一片**没有**那么做，理由写在
`cmd_work_dispatch` 的 docstring 里：`invoke_once` 在 spawn **之前**就落 receipt，而且
同 key 重入是**重放**不是重跑，所以「跑完到记下之间崩了」是可恢复的；而一个**能跑命令却不能
记录**的调用者会留下一个在途 worker、而准入上没有任何东西说这件事——**那正是这一条链存在的
理由所要防的半成品**。所以 `work dispatch` 一次做完两件：先跑（写好 receipt），再从**那个
文件**读回答案，用同一个读者、同一份文件，做与 `work confirm --phase DISPATCHED` 完全相同
的一次记录。

#### 9.31.3 五道闸门都在 spawn 之前，顺序由「事后检查要付什么」决定

docstring 里把五条按**事后才发现要付的代价**排了序，这是这一片唯一一处「顺序本身是判据」的
地方：

| # | 闸门 | 事后才发现的话，代价是什么 |
| --- | --- | --- |
| 1 | 已退休的准入不许推进 | store 也会拒（`was retired at …`），但它的拒绝会落在**命令已经跑完之后** |
| 2 | 相位已是 `DISPATCHED` 就是重放 | 不是拒绝，是**什么都不跑**（见 9.31.5） |
| 3 | `INTENT_RECORDED` 不能跳到 `DISPATCHED` | 这一条差别最刺眼：命令会带着效果跑完，相位随后被拒——**工作做了、账上没有** |
| 4 | **读租约 store，不读相位** | `agent_control_store` 自己的注解说「a phase is not a permission」，「the reader that wants to act on that reads the lease store itself」；这个动词**就是**那个读者。相位确认之后租约被释放，这里才抓得住 |
| 5 | 命令必须是一个非空的 argv，且在 `--` 之后 | 见 9.31.4 |

第 4 条不是形式主义：`LEASE_HELD` 只记「租约 store 当时那样答了一次」，它**不授权**。一个只信
相位的动词会在租约已经被别人拿走之后照样跑命令——那就是把一条登记当成了许可证。

#### 9.31.4 上限是准入的，不是这条命令行的；`--` 是必需的

`wall_seconds` 与 `max_output_bytes` 从**这个 cycle 被准入时的 reservation** 里取，命令上
没有对应的旗标（有一条测试直接钉住这件事：`--cwd`、`--receipts` 在 `--help` 里，`--timeout`
与 `--max-output-bytes` 不在）。理由是一个跑得比被授权更久、写得比被授权更多的派工，就是
**授权从没覆盖过的效果**，而一个能抬高它们的旗标就是这个洞有了名字。

`--` 也是必需的，而且理由是量出来的不是想出来的：argparse 把没认出来的东西按输入顺序原样退回
（`parse_known_args` 的 leftovers），所以在没有分隔符时 `work dispatch cyc-1 --cwdx /w` 会以
「命令 `--cwdx /w`」的形状到达这里、**然后被执行**。要求分隔符，就是让一个打错的旗标仍然是
一个错误、而不是一条命令。`nargs=argparse.REMAINDER` 是本机实测过不行的（bpo-17050），所以
这一片的机制是 `set_defaults(accepts_unknown_argv=True)` ＋ `main()` 里把 leftovers 交给
`args.trailing_argv`（这个名字从 `checker_argv` 改过来，因为它现在有两个用途：`architecture`
把尾巴转给检查器，`work dispatch` 取 `--` 之后的命令）。

#### 9.31.5 相位记录的是**那个文件**，不是那次调用——而这一点行为上分辨不出来

这是这一片唯一一处「判据必须靠结构钉住」的地方，理由值得单独写：`invoke_once` **重放时返回的
就是文件自己的内容**，所以「印出调用的返回值」与「读出那个文件」在行为上**同形**——一个把
`invoke_once` 的返回值拿去记录的动词，在任何一次可观察的运行里都会印出一样的东西。所以那条
测试钉的不是措辞，是**只有 `_dispatch_answer` 会加的两个键**：

```python
recorded = AgentControlStore(store_path(env)).get_admission("cyc-1")["confirmations"]["DISPATCHED"]
assert recorded["store"] == str(receipts)
assert recorded["receipt"] == str(receipts / "act-1.json")
```

配套的形状改动在 `_dispatch_answer` 的返回值上：`evidence` 仍是**三个字段**（加 store/receipt
四个），因为它是**重放稳定性**的载体——同相位同证据的重报必须回 `replayed: True` 而不是冲突；
而整条记录从新加的 `receipt` 键回来，给**真正做了这一步的那一个调用者**（就是 `work dispatch`
自己）用。两个答案回答同一个问题，而**不一致的那一对正是没人会去看的那一对**——这就是为什么
证据取的是文件那一侧。

#### 9.31.6 变异：8 条判据，8 条都死

脚本 `/data/home/vince/artifacts/tmp-p7/mutate_dispatch.py`；复原不用任何 git 动词（脚本自持
原件、按 sha256 比）。基线 `115 passed in 18.77s`（`tests/test_admission.py` 单文件）。每一轮
清 `__pycache__`，而且**必须清**：被测的 `runtime/cli.py` 是被 `bin/serverosctl` 在**子进程**
里加载的，父进程的 `PYTHONDONTWRITEBYTECODE=1` 只阻止写、不阻止读，同尺寸同秒的旧 `.pyc` 会让
「这条判据活着」变成缓存造出来的假结论。

| # | 把哪条判据弄坏 | 结果 |
| --- | --- | --- |
| M1 | 相位改成记录 `invoke_once` 的**返回值**而不是 receipts 文件 | KILLED（`test_the_phase_records_the_receipt_the_command_wrote`） |
| M2 | 删掉「还没到 `LEASE_HELD` 就不许派工」 | KILLED（`test_a_dispatch_before_the_lease_phase_runs_nothing`） |
| M3 | 删掉「已退休的准入不许派工」 | KILLED（`test_a_retired_admission_is_not_dispatched`） |
| M4 | 不再读租约 store（只信相位） | KILLED（`test_a_lease_released_since_the_phase_still_stops_the_dispatch`） |
| M5 | 不要求 `--` 分隔符 | KILLED（`test_the_command_must_come_after_the_separator`） |
| M6 | 删掉 `phase == "DISPATCHED"` 的重放分支 | KILLED（`test_a_second_trigger_is_not_a_second_dispatch`） |
| M7 | wall 上限改成命令行常量而不是 `reservation["wall_seconds"]` | KILLED（`test_the_bounds_are_the_reservations_and_not_the_command_line`） |
| M8 | 无条件打印 `process_exit_code` | KILLED（`test_a_command_that_cannot_start_is_not_given_an_exit_code`） |

**8/8 全部由它该杀的那一条测试杀死**，没有一条是靠别的测试顺带变红的。M3 与 M6 值得各看一眼，
因为它们的红看起来像「同一条判据在别处也被拦住了」：

- **M3** 删掉退休闸门后，store 自己的 `was retired at …` 照样让动词非零退出，所以光看
  「退出码非零」这一半是分辨不出来的；红的是**另外两半**——`"Nothing was run"` 不在 stderr 里
  （走的是「命令已经跑过」那条分支），以及 `receipts` 目录**存在了**。也就是说这条判据真正钉的是
  **「命令有没有跑」**，而不是「有没有报错」。
- **M6** 删掉重放分支后，第二次触发不是重放而是**被当成错相位拒掉**：退出码非零，但
  `test_a_second_trigger_is_not_a_second_dispatch` 要的是 `returncode == 0` 且那条
  `touch marker` 的命令**一次都没被跑过**。所以「重复触发不双派」在这里的完整含义是
  **既不能跑第二遍、也不能报错**——报错说明这个动词不知道自己已经派过了。

#### 9.31.7 live 读数：一个隔离 DATA_ROOT 下的一次真派工

`ARTIFACTS_ROOT/tmp-p7/dispatch-smoke`，`DATA_ROOT` 指向那里，全程不碰这台机器的真 state 根。
三次调用，逐字读数：

```text
第一次（还没有 LEASE_HELD）：
  退出码 1，stderr 里是「… is at INTENT_RECORDED and a dispatch is the phase after
  LEASE_HELD. … Nothing was run」，`receipts/` 目录**没有被创建**。
第二次（`lease declare browser:chromium --scope session --holder cyc-1` ＋
        `work confirm cyc-1 --phase LEASE_HELD` 之后）：
  退出码 0，`phase       DISPATCHED`，`status      EXITED`，
  receipts 目录里落了 act-1.json / .stdout / .stderr / .stdin / .lock。
第三次（同一条准入再派一次，命令换成 `touch marker`）：
  退出码 0，印的是「nothing was run」，marker 文件**不存在**。
```

`act-1.json` 里那个写者自己决定的字段，逐字（这一格只抄它的读数，不解释）：

```text
action_key            'act-1'
status                'EXITED'
effect                'UNVERIFIED'
process_exit_code     0
worker_stopped        False
executed_now          True
direct_process_exited True
descendants_verified  False
read_only_requested   False
input_digest          '1748b46a4da9ac7e2f7376adca394e20bb5ae031cc7c0308309b1649083c64fc'
started_at            '2026-09-25T18:53:04.086485+00:00'
finished_at           '2026-09-25T18:53:04.089299+00:00'
```

`act-1.stdout` = `hello-from-worker`，`act-1.stderr` = `warn-here`。准入那一侧，
`state/serveros/runtime/agent-control/store.json` 里 `cyc-1` 现在是：

```text
phase         'DISPATCHED'
confirmations.DISPATCHED = {action_key: 'act-1', status: 'EXITED',
                            input_digest: '1748b46a…',
                            store:   '…/dispatch-smoke/receipts',
                            receipt: '…/dispatch-smoke/receipts/act-1.json'}
```

`store` 与 `receipt` 这两个键就是 9.31.5 说的那一对：它们在 `act-1.json` 里**不存在**，
是这个动词自己加上去的，所以它们同时证明了「记录取自哪个文件」这件事。

#### 9.31.8 这一片没有做的事

- **没有上弦，而且没有任何调度器。** 本机没有 timer 打 `work dispatch`（§12.5 的 Daily Cycle
  同样没上弦），所以 9.31.7 那三次是手工打的。这一片证明的是**这个动词现在会答**，不是
  「派工会自动发生」。
- **可达性只有 CLI 一条路，这是量过的**：`work dispatch` 不在 daemon 里、不在 MCP 面里、
  不在观察路径上、不在任何 Run 里，`mcp/node_mcp.py` 不通用地包 CLI 动词。这段话写进了
  `runtime/actuator-contract.yaml`（把那两条条目里「本机没有生产调用者」的散文换成了
  「这个调用者能走多远」）。
- **契约里那段散文没有判官——但这一片和前一片不同。** 有判官的是**调用点本身**：
  `tests/test_mutation_guard.py::TheLiveTreeIsCovered` 断言每一个产品调用点都被声明、且没有
  声明是过期的，**这一片正是被它逼着去改那两条 `why` 的**（原来那两句「本机没有生产调用者」
  在这一片之后就成了假话）。没有判官的是**描述这个调用者能走多远的那几句**。改一个没人读的
  句子，绿不绿都不说明它对——这一处仍然写在明处。
- **`max_attempts` 在 reservation 上没有消费者**（新发现，见 §8 那一族）。`invoke_once` 是
  **一个 action key 一条 receipt**，所以 `max_attempts: 2` 里超出 1 的那一份额度**结构上花不
  出去**；重试是一个**新 cycle、新 key**，而重用已退休的 key 会把旧 receipt 交给新 cycle。
  这不是这一片引入的，是这一片让它第一次可以被读到。
- **J06/J07 六条验收里，这一片让三条从散文变成可达且可测**：「重复触发不双派」（相位检查 ＋
  `invoke_once` 重放）、「unknown effect 先查原状态」（spawn 前落 `STARTED`，重放**从不重
  spawn**）、「UI 断线不取消 worker」（`start_new_session=True`；派工的进程被杀，receipt 停在
  `STARTED`，而 `RECEIPT_LIVENESS` 把那读成「槽位还占着」）。**另外三条仍然只是散文**：
  Parent 替换后原 child 自动接回、一项等待不冻其他合法 scope、`parent_session` /
  `handoff.completed` 没有生产者。
- **live 的 `work admit` 仍然做不成**：板是 `STALE_BOARD`（§9.28.7／§9.29.7 第三条），
  `work grants` 报 0 recorded / 0 authorizing，所以真机上 `work admit` 会被
  `MISSING_OR_REVOKED_GRANT` 拒掉。**第一条真授权是人的动作**（§11 那一类），不能用一条
  fixture 值绕过去——9.31.7 那次用的是隔离根里自己造的 `grant-1`，这正是它必须待在隔离根里的
  原因。

### 9.32 P7 切片 D：回执锁不再让一项等、把另一项冻住（J07 判据 5）

#### 9.32.1 那个缺陷的形状：一把持「整条命令的命」的锁，被用阻塞的方式取了

`runtime/bounded_invocation.invoke_once` 在拿到「这个 action key 的回执」和写它之间必须是一
步，所以它取锁——而那一把锁是从 `Popen` **之前**一直持到整个进程组被 reap **之后**，也就是
持着「这条命令的整条命」。取法一直是 `flock(LOCK_EX)`，阻塞的。

它在 §9.31 之前不是生产可达的：`invoke_once` 当时一个生产调用者都没有。`work dispatch` 落地
之后它是了，于是 J07 验收判据的第 5 条——**「一项等待不冻其他合法 scope」**——变成一条可判、
可触发、而且判据已经写在纸上的缺陷：同一个 action key 上的第二个调用者会**等**第一个命令跑完，
上限就是这条准入 reservation 里的 `wall_seconds`（本机 fixture 与生产同形，900 秒），然后拿到
的答案是回执自己的 `input_digest` 当场就能给的那一个。

本片之前查过全机：**这条是唯一一条「一项在等、把另一项卡在锁上」的路径**。`recipes._write_record`
与 store 自己的 `_mutation` 也取锁，但它们的临界区是写一个小 JSON。

#### 9.32.2 为什么是「拒绝」而不是「等」或「放行」

- **不能放行。** 一个 key 一份回执是这个模块的全部意义所在：放行就等于两条 receipt、两次执行，
  正是 `admit` 的 docstring 里点名的那种「the receipt would say `replayed: True` about work it
  never did」。
- **不能等。** 等的收益是零：锁一旦到手，回执的 digest 判据给出的答案与「当场被拒」是同一个，
  因为争用的那一方**不可能**用同样的输入拿到一个不同的答案；而成本是另一个 scope 的整个 wall
  budget。收益为零、成本是他人的时间，这不是「保守」。
- **第三个选项**：当场拒绝，带上键名、说明为什么不是队列、给出可执行的动作（等它跑完再重试——
  同输入重放回执，不重跑）。而且拒绝发生在 `Popen` **之前**，所以 `work dispatch` 那句
  「nothing was started」仍然是事实而不是希望。
- 反过来说为什么另外两处**保持阻塞**：那两处的临界区是一个小文件的读改写，等它是有界的；
  只有持「整条命令」的那一把不能等。所以 store 里是两个函数而不是一个开关。

#### 9.32.3 两处异常的区分是承重的，不是风格

- `LockUnavailable` **故意不是** `StoreError`。前者是「此刻有人在里面」，重试是对的；后者是
  「你请求本身有问题」，原样重试是错的。合成一个，就会给一半的抛出者配上一句错的重试指令。
- errno 判据：只有 `EAGAIN`／`EACCES` 被说成「有人正拿着」；其它 `OSError` **原样抛出**。
  否则一把**打不开**的锁会被报成「别人正拿着」，操作者会一直重试一件不会发生的事。

#### 9.32.4 测试为什么是这几条，以及为什么里面没有 sleep

- **门闩文件**：被测的命令门在「某个标记文件存在」上，而 `STARTED` 写在锁**内**、`Popen`
  **前**，所以那个文件一出现就证明锁已被持有。断言用的是 `holder.is_alive()` 这个**事实**，
  不是一个时间阈值；释放只发生在测试自己的 `finally` 里。
- **「另一个 key 不被冻住」不是装饰**：没有它，「把锁取在整个 receipts 目录上」这种更坏的实现
  与修好的实现一样绿。它钉的是「锁的身份是 key」。
- **「拒绝要求的重试确实重放」**：计数器自证（同输入重放、不重跑）。这句拒绝里写给操作者的
  指令必须是真的，否则它比等待更坏。
- **产品面那条**：两个 scope 共用一个 key。这个形状只能靠**退休后的复用**造出来——`admit`
  明确拒绝两条**开着的**准入共用一个 key——而这正是人会产生它的方式：关掉一个 cycle，用同一个
  好读的键开下一个。断言是：第二个**当场**被拒、消息里点出是哪把锁（`act-1.lock`），而第一个
  仍在跑。

#### 9.32.5 变异表：6 条，5 杀 1 存——存的那条留着，且没有为它造假测试

| M | 弄坏什么 | 结果 | 抓到它的 |
| --- | --- | --- | --- |
| M1 | 把阻塞锁原样放回来（就是这条缺陷本身） | **KILLED** | 持有者仍在跑时的拒绝、重试重放、跨 scope 三条 |
| M2 | 去掉 `LOCK_NB`（函数还在，但它会等） | **KILLED** | 同上三条 |
| M3 | 丢掉 `LockUnavailable` → `ValueError` 的翻译 | **KILLED** | 同上三条 |
| M4 | 锁按**目录**取而不是按 key 取 | **KILLED** | 「另一个 key 不被冻住」、「打不开的锁」、拒绝那条 |
| M5 | 去掉 errno 判据（什么 `OSError` 都说成争用） | **SURVIVED** | —— |
| M6 | 拒绝消息里不再带出锁的路径 | **KILLED** | 同上三条 |

M5 的读法与处置：`flock` 本身产生非争用 errno 的唯一现实途径是把**内核锁表耗尽**，测试安排不
出来；所以那一行是**防御性的、未被钉住**，如实记在这里，而不是省略、也不是为它加一条读起来像
判据的假测试。

**顺带订正一条本片自己写错的**：M5 的第一版测试 docstring 说「把锁路径做成目录，`flock` 拿不
上」——实测是 `os.open` 先以 `EISDIR` 失败，`flock` 根本没被调到，所以那条测试钉的是「打不开
的锁不被说成争用」，不是「flock 的非争用错误」。docstring 已按实测改写，M5 相应记为存活。

#### 9.32.6 本次读数

- 变异轮：基线 `131 passed`（`tests/test_bounded_invocation_paths.py` + `tests/test_admission.py`
  两个文件）。M1/M2 那两轮各耗时约 105 秒而不是 26 秒——原因就是被测的东西：测试要等到 holder
  的 wall（20 秒）到期，才轮到「第二次调用有没有被拒」这个断言。**那 80 秒的差额本身就是证据。**
- 全量：**5714 passed, 20 skipped, 3947 subtests passed, 0 failed（647.81s）**。
  算术闭合：上一格 5709 ＋ 本片新增 **5 条**（`test_bounded_invocation_paths.py` 四条 +
  `test_admission.py` 一条）＝ 5714；subtests 3947 ＋ **0** ＝ 3947。
- 这一跑读到的就是提交的那份字节：跑前记下 7 个被测文件的 sha256，跑完 `sha256sum -c` 全部 `OK`
  （其中 `runtime/cli.py` 此前有一次编辑是**文本替换**而不是整文件重写，所以记的是内容而不是
  「文件没动过」）。

#### 9.32.7 它没做什么，以及一条邻接的新发现（未处置，列为切片 E）

- 仍然没有 scheduler；`max_attempts` 仍然没有消费者（§9.31.8 第三条照旧）。
- **邻接发现（本片量到，未修）**：`retire` 释放一个 action key 之后，下一个 cycle 复用同一个
  key，而命令输入**相同**时，`invoke_once` 会**重放**上一轮的 receipt，`work dispatch` 于是把
  复用者的相位记成 `DISPATCHED`——**这条准入的派工证据来自另一条 cycle 跑的命令**。store 在
  `admit` 的 docstring 里把这条结果明说成缺陷（「the receipt would say `replayed: True` about
  work it never did」），但那一条判据只覆盖**开着的**准入，退休把这条路重新打开。判据本身很干
  净、可判：**回执的 `started_at` 早于这条准入的 `admitted_at`，它就不可能是这条准入的派工**
  （同一条 cycle 的重试不会被它误伤，因为重试必然晚于自己的准入）。列为下一片，不在本片内改。
- 共享网关那一侧不属本仓，本片一概未动。

### 9.33 P7 切片 E：一条退休后复用的键，把上一轮的回执递给了下一轮

#### 9.33.1 那个缺陷的形状：唯一性只在「开着的」准入之间，而回执是一键一份

三件事各自都对，合起来是错的：

- `agent_control_store.admit` 的唯一性判据是
  `if other.get("action_key") == action_key and not other.get("retired_at")` —— 只挡**开着的**
  第二条准入；
- `work retire` 的报错文本自己写着「released, so another admission may hold it now」—— 复用
  是设计，不是漏；
- `runtime/bounded_invocation.invoke_once` **一个 action key 一份回执**，而且输入 digest 相同时
  **重放**而不重跑。

于是：cycle-1 用 `act-1` 派了一条命令、拿到回执；cycle-1 被退休、键被释放；cycle-2 用**同一个**
`act-1` 派**同样的**命令 —— `invoke_once` 把 cycle-1 那份回执原样交出来。而 `runtime/cli.py` 里
有两个生产者会读那一个文件当作「本 cycle 的派工证据」：`work dispatch` 与
`work confirm --phase DISPATCHED`。两者都会把 cycle-2 的相位记成 `DISPATCHED`，**证据来自另一条
cycle 跑过的、cycle-2 一次都没跑过的命令**。

store 在 `admit` 的 docstring 里已经把这条结果明说成缺陷 ——「the receipt would say
`replayed: True` about work it never did」—— 但那一条判据只覆盖**开着的**准入。**退休是重新打开
这条路的那一步**，所以判据放在 `admit` 里对它无效。

#### 9.33.2 判据是一条比较，以及为什么它误伤不了必须继续工作的情形

判据：**回执的 `started_at` 早于这条准入的 `admitted_at`，它就不可能是这条准入的派工。**

它误伤不了同一条 cycle 的重试，理由不是「大概不会」，是构造上的：重试必然发生在这条 cycle 被准入
**之后**，所以 `started_at >= admitted_at`，比较不成立。比较抓到的恰好是「这份文件写在这条 cycle
存在之前」。

两句前提值得写下来，因为它们决定这个判据能问多细：

- 两个时刻**都是本树写的**：`invoke_once` 写 `started_at`，`work admit` 写 `admitted_at`，都写
  带 offset 的 UTC。所以一个 naive 的时刻被**读作 UTC** 而不是被拒 —— 把格式差异升级成关于一条
  cycle 历史的主张是过度；而一个**根本不是时刻**的值答 `None`，因为调用者问的是一个比较，
  「没法比」不是它的答案。
- `confirm_admission` **不校验** `at` 是否落在 grant 窗口内（原样存 `confirmed_at`），所以
  「一条 `--at T1` 的确认落在真实时钟的窗口里」是合法的。这不影响本判据：`admitted_at` 由
  `work admit` 写、永远是 `now`，所以一条回执只可能来自**更早的** cycle。而「`--at` 不校验
  窗口」本身是另一条待办，不在本片。

#### 9.33.3 为什么既在 spawn 前问、又在唯一那个读者里问

`_dispatch_answer` 是 `runtime/cli.py` 里**唯一**读回执的地方。这是性质而不是巧合：两个生产者都
从它读，所以把问题问在「文件被打开的那个地方」一次覆盖两条路 —— 将来第三个读回执的人也不会绕过
它。

但只问在那里不够。`work confirm --phase DISPATCHED` **不 spawn**，所以拿掉 spawn 前那一问，
`work dispatch` 拒绝语里的「Nothing was run」就从事实变成希望 —— `invoke_once` 本来也不会起进程
（digest 相同时它重放），但「什么都没起」是关于一次**没有发生的 spawn** 的句子，而一个能说更强
那句话的动词欠读者那一句。所以 spawn 前另有一问；两处问的是**同一个函数**，所以不会漂移。

#### 9.33.4 两种「让开」是有区别的

- **键不同**：`_receipt_predates_the_admission` 直接让开（返回 `None`），因为 store 的
  `dispatch_evidence_conflict` 已经用一句更准的话拒了这一对 ——「evidence about a different
  dispatch」。先问这个，会把一句准话换成一句泛话。
- **日期读不出**（字段不在、空串、不是时刻）：也让开，落到读者本来就有的那两道判据（回执自己的
  `action_key`、store 的证据比较）。它不是拒绝，因为这里没有可做的比较；而 `invoke_once` 总会写
  `started_at`，所以这个情形需要一份**本树没写过**的文件，而那已经在回执读者自己的契约之外。

第一版的设计不是这样：当时打算「没有日期就拒」。是**已有的那条测试**把它改掉的 ——
`test_a_refused_dispatch_is_a_sentence_and_not_a_traceback` 特意种了一份键正确、**没有
`started_at`** 的回执，并期待 `invoke_once` 那句「same action key with different input」。一条新
判据去抢那句更准的话，是把别人的答案换掉。

#### 9.33.5 测试为什么是这四条，以及那条 fencepost 是怎么被补出来的

- **假时钟会让判据看起来成立而其实没被考到。** 本文件既有的 fixture 起于 `T0 = 2026-01-01`，
  而 `work admit` 写的永远是真实时钟。所以新增的 helper 走**真实时钟**：`admitted_at` 取
  `datetime.now(timezone.utc)`，grant 窗口取 `now-1h … now+1d`。用 `T0` 会造出一条「回执晚于
  准入」的循环，测试绿，而判据一次都没被走到。
- **两个生产者必须各有一条。** 只修一处的话：网页那条测试绿、`work confirm` 那条红。两条都在。
- **反例不是装饰。** 第三条测试是**正例**：一条 cycle 读**自己的**回执，断言的是那句
  `phase DISPATCHED — confirmed already; nothing was run`，也就是没有被新判据拦下。没有它，
  「干脆全都拒」这种更坏的实现一样绿。
- **fencepost 是补出来的，不是编出来的。** 第一轮变异里 E4（把边界 `>=` 改成 `>`，也就是「只有
  两个时刻完全相等时才不拒」）**存活** —— 没有任何测试能造出两个**恰好相等**的时刻。处置不是把
  它记成「未被钉住」，而是先确认这个 fencepost **是构造得出来的**：种一份 `started_at`
  **逐字等于**该准入 `admitted_at` 的回执（digest 用 `"0"*64`），断言回来的拒绝是
  「same action key with different input」而**不是**「predates the cycle」。这条只能靠**种文件**
  造出来 —— 真的跑一次派工永远得不出两个相等的时刻。补上之后 E4 被杀。
- **没有 sleep。** 四条全靠**种**进去的时刻，不靠时间流逝；判据本身是两个字符串的比较，没有可等
  的东西。

#### 9.33.6 变异表：4 条，4 杀

| M | 弄坏什么 | 结果 | 抓到它的 |
| --- | --- | --- | --- |
| E1 | 回执读者里那条判据拿掉（`why = None`） | **KILLED** | `test_the_confirm_verb_refuses_it_too` |
| E2 | `work dispatch` 的 spawn 前预检拿掉 | **KILLED** | `test_a_reused_key_is_refused_a_receipt_written_before_this_cycle` |
| E3 | 比较方向反过来（`started <= admitted`） | **KILLED** | **9 条**（`test_confirm_dispatch_records_a_receipt_invoke_once_wrote`、`test_the_phase_records_the_receipt_the_command_wrote`、`test_a_second_trigger_is_not_a_second_dispatch` …） |
| E4 | 边界从 `>=` 改成 `>` | **KILLED** | `test_a_receipt_from_the_very_instant_of_admission_is_not_older` |

E3 杀掉 9 条本身就值得记：它说明「方向反过来」不是一条微妙的变异，而是会把一套已经在用的行为整片
翻掉 —— 这正是「这条判据是承重的、不是装饰」该有的读数。E4 是唯一一条需要为它补测试的（见
§9.33.5），补的方式是**构造**那个边界而不是放宽断言。

#### 9.33.7 本次读数

- 变异轮基线：`tests/test_admission.py` 单文件 **120 passed in 26.10s**（本片之前是 116，本片新增
  4 条）。E1/E2/E4 三轮各约 26.5 秒，E3 那轮 23.75 秒 —— 四轮**都没有 sleep**，所以没有一轮因为
  「等超时」而变长。这与 §9.32 的 M1/M2 恰成对照：那两轮各约 105 秒，而**那 80 秒的差额本身是
  证据**（测试要等 holder 的 wall 到期）。本片没有可等的东西，所以四轮齐平 —— 齐平也是一条读数。
- 全量：**5718 passed, 20 skipped, 3947 subtests passed, 0 failed（658.60s）**。算术闭合：上一格
  5714 ＋ 本片新增 **4 条**（`TestTheDispatchVerb` 四条）＝ 5718；subtests 3947 ＋ **0** ＝ 3947
  （本片四条都没有 subtests）。
- 这一跑读到的就是提交的那份字节：跑前记下 `runtime/cli.py`
  `5da0ffbeefadf3a3…` 与 `tests/test_admission.py` `d374fde739b10327…`，跑完两个 sha256 逐字一致。
  跑动中只写过 `docs/CC-EXECUTION-LEDGER.md` 一次，而全树**没有测试读那份文件**（两次命中都只在
  docstring 里），所以它不影响这一跑读到的字节。
- 一条如实记录的**保真度缩水**：这一跑里 `tests/test_daemon_equivalence.py` 有一处断言**没有按精确
  相等判**。测试自己的警告写着宿主在比较过程中动过（daemon 读到 154 组、随后 153 组），于是它改判
  153 是否落在 153..154 的括号内，并明说「一个恰好偏差这个量的盲目 daemon 会通过这一跑」。这是
  **环境造的，不是本片造的**，但它确实是这一跑里唯一一条被放宽的断言，所以记在这里而不是省略。

#### 9.33.8 它没做什么，以及一条邻接发现为什么不并进本片

- 仍然没有 scheduler，`max_attempts` 仍然没有消费者（§9.31.8 第三条照旧）。
- **邻接发现（本片量到，未修；这一条原先在这里被写为「列为切片 G」，而那个字母被 §9.35 先落了 ——
  那条发现**仍然未做**，只是不再占着这个字母。同一族的改法在 §9.34.1 有过一次，那里是把本片
  自己改掉的那个编号就地改正）**：`intake/focus_source.worker_stopped_by_item` 读的
  是**同一个** `<receipts>/<action_key>.json`，而且它**在生产的路上** ——
  `runtime/cli.py` 的 `focus` 那条路把 `admissions` 与 `receipts_root` 交给
  `focus_source.compose`。它已经有「键相符」那道判据（`RECEIPT_NAMES_ANOTHER_ACTION_KEY`，注释里
  点名 `runtime/agent_control_store.dispatch_evidence_conflict` 是同一判据的 owner —— 而
  `intake/` 下**没有任何一个模块** import `runtime.*`，所以那是照抄），**但没有时刻比较**。
- **为什么不在本片照抄一遍**：因为在那一处，判据问的**不是同一个问题**。`work dispatch` 问的是
  「这份回执是不是**这条 cycle 的派工证据**」；`worker_stopped_by_item` 问的是「这个 Item 上
  **还有没有一个进程被占着**」。一份早于准入的回执**仍然可能**是某个真在跑的进程的唯一记录 ——
  一个键一份回执，没有第二份文件。
- 触发条件比它看上去窄，这一点本片量清楚了：`retire_admission` **保留**记录（「Retiring is not
  un-admitting」），所以同一个键上会有两条准入同时指着它 —— 退休的那条与活着的那条。它们读的是
  同一份回执：退休那条的读**仍然是对的**（回执就是它起的那个进程），活着那条的读才是错的。Item
  级聚合是「held 胜过 undecidable 胜过 released」，所以只有当复用同一个键的新准入**指向另一个
  Item** 时，那个 Item 的答案才会错；指向同一个 Item 时答案仍然是对的，因为那个进程确实是它的
  worker。
- 三种候选各有代价，所以这是个**先定问题、再定判据**的决定，不是一次照抄：`False`（现状）＝拿
  另一条 cycle 的进程说这个 Item 被占，正是要修的那件事；`None` ＝「这条准入没有被证明起过
  进程」，方向安全（不释放），但它会让 `daily_cycle.classify` 里 `if held:` 那一支不再命中，于是
  一条 `AVAILABLE` 的行会从 `HELD` 变成可被选中 —— 也就是 OVER_CAPACITY 那一侧。**先决定「这一
  处问的是哪一个问题」，再决定用哪条判据。**
- 共享网关那一侧不属本仓，本片一概未动。

### 9.34 P7 切片 F：等待中的 Parent 留下一份可续的 checkpoint，以及「没有 receiver」是一句关于 fabric 的真话

#### 9.34.1 这一片接的是哪一环：产品点名的两半，先接能如实报告的那一半

切片 E 的 §9.33.8 把 `intake/focus_source.worker_stopped_by_item` 的邻接发现列为「下一片」，
**这个编号在落笔前就改掉了**：本片接的是延续链自己那一环，那一条改列为切片 G（§9.33.8 已就地改
字）。理由是后台 agent 对 P7 延续链的映射给出的主断裂有**两处**，而它们的先后顺序由产品自己定：

- **checkpoint 那一半不存在。** 一个必须等待的 Parent 没有任何地方可以留下「我走到哪了」；
  `docs/context-capsule.md` 把这个对象的名字、字段和性质都写全了（「已接受的 Requirement 修订 /
  所需剩余结果 / 已接受的证据定位器 / 实际的子尝试和未解决的影响 / 写入范围 / 下一个安全动作」，
  并且「**不是转录摘要，也不是新数据库**」），而它没有生产者、没有消费者。
- **唤醒那一半完全不存在。** 没有任何地方有 `wake`/`poll`/`next_parent`，`runtime/daemon.py` 的
  `tick()` 既不读准入也不读 checkpoint，没有 scheduler。

两半的顺序不是偏好。`docs/daily-work-cycle.md` §17.4 要求的是**没有 receiver 时准确显示
「checkpoint已保存，待续」，不能伪造后台运行** —— 这句要求的前半段是 checkpoint 存在，后半段
是那半没有的东西被**如实报告**。先把唤醒做出来，就永远量不到「没有 receiver 时」这个状态；先把
checkpoint 做出来，那半缺失就变成一句可读的输出。所以本片的结果正是：**一个能续的 checkpoint，
加上一句关于本机没有东西会唤醒它的真话。**

#### 9.34.2 记录是一个封闭字段集，和一个只有两种的等待

`runtime/continuation.py` 新建，`CHECKPOINT_FIELDS` 就是 §9.34.1 引的那六个字段加五个（Item、
epoch、owner、下一个安全动作、等待），**闭集**，`build_checkpoint` 返回的恰好是它加上自己派生的
两个（`checkpoint_ref`、`written_at`）。唯一的自由文本是 `next_safe_action`，界 `MAX_NEXT_ACTION = 400`。
这不是洁癖：**一个调用者能加字段的记录，就是一个调用者会把转录放进去的记录**，而产品对这个对象的
措辞恰好是「不是转录摘要」。同一份理由下 `WAIT_KINDS = ("child", "window")` 也是闭的，而且闭的
方式是把「为什么少了一种」写进 docstring：**对另一个 Work Item 的依赖是真实的等待方式，本机没有
任何存储能说出一个 Item 依赖哪些 Item**，加一个没人能核实的 kind 只会让列表更长、记录更弱。

两种 kind 各自对着一样能核实的东西：

- `child` 对着 **admissions**（`waiting_on.refs` 里每个 ref 都必须是这个 store 持有的 `cycle_ref`）。
  等一个 fabric 看不见的孩子，就是 `docs/SCENARIO-ACCEPTANCE.md` 点名的「要求用户复制结果」的开头。
- `window` 对着**写入时的时钟**（`until` 必须是晚于 `written_at` 的 instant）。已经关上的窗口不是
  等待，把它存下来会让记录读成「现在就该续」。

顺带量到一条与 retire 有关的事实并写成了测试：**`retire_admission` 保留记录**，所以一条已退休的
admission 仍然为它记录的那个孩子作答。这不是宽容，是必须——退役不是取消，一个 Parent 在自己等的
孩子刚跑完的那一刻读到「没有这个孩子」才是错的。

#### 9.34.3 为什么 key 里只有 Item 和 epoch，没有 owner

`checkpoint_key(item, epoch) = f"{item}#{int(epoch)}"`。**owner 是字段，不是 key 的一部分。** 一个
continuation 在其中唯一的东西是「这个 Item 在它自己的第几个 epoch 上」；把 owner 放进 key，两个
Parent 争同一个 scope 就变成两条**悄悄共存**的记录，而不是一条可读的冲突。

#### 9.34.4 一个 scope 一个 owner：为什么是「同事务取代」而不是「拒绝并要求先取代」

这一条是**本片自己的冒烟测出来的**，不是设计时想到的。第一版只写了「低于已记录最高 epoch 的写法
被拒」，冒烟第 6 步立刻显示：写入 epoch 2 之后 `live_checkpoints()` 同时返回 `SRV-0001#1` 与
`SRV-0001#2` —— 一个 Item 两个 live owner，而 `docs/COLLABORATION-CONTRACT.md` 写的是
「同一个scope中只有一个父继续owner」。

修法有两种，选了后者：

- 拒绝，并要求调用者先 `supersede_checkpoint`。**不选**：两次写入之间有一段时间是两个 owner，
  而这段窗口正好是崩溃会落进去的地方。
- **较高 epoch 的写入在同一事务里取代较低的 live 记录。** 交接写在**旧记录**上
  （`superseded_by`/`superseded_at`），所以被换掉的 Parent 保住自己的状态、只失去对它的主张；而
  「一个 scope 一个 owner」从一句散文变成一条 store 规则。

两条拒绝与它并存，各自对应一句产品原话：

- 同一个 (Item, epoch) 的第二条 live 记录被拒，报错点名**谁**持有、**什么时候**写的
  —— 「同一个scope中只有一个父继续owner」。
- 早 epoch 不得写进晚 epoch 的当前状态（`other_epoch > epoch` 时拒），
  **这就是「旧结果晚到覆盖新epoch」写成 store 规则而不是写成散文**。而且它只在 ref 是新的时才
  可能触发：`build_checkpoint` 派生 key，所以同 Item 同 epoch 的正常撞车走的是上一条拒绝。

#### 9.34.5 第二个缺陷也是本片自己的：store 写上去的字段让「已接受写入的重试」变成「你的记录不在这」

这是本片改的第二处，而且它的形状值得单独写：**它是修第一处时引进来的。**

重放判据是「把调用者给的记录与已存的比一比，去掉几个字段」，那几个字段原本只有 `written_at`。但
取代是 store 事后写上去的，于是被取代过的记录比它的作者写的**多两个字段** —— 一次超时后重试的
写入（`work checkpoint` 的那条路本来就会重试）会拿到「你的记录不是存的那份」。

**一个 replay 绝不能对调用者说的，就是「你自己那份被接受的写入不在这」。** 修法是把
`CHECKPOINT_STORE_FIELDS = ("written_at", "superseded_by", "superseded_at")` 显式列成一个常量，
在 §9.34.2 与 §9.34.4 之间成为第三个「store 自己加的东西」的清单。变异表里的 F8 就是把它改回
`("written_at",)`，被 `test_a_retry_of_an_accepted_write_survives_the_scope_changing_hands`
杀掉 —— 那条测试因此是这一片最该留着的一条。

顺带定下**重放的判据与两条拒绝的先后**：重放**先问**。一次被接受的写入的重试是重试，不管这个
scope 的历史后来做了什么；放在后面问，一次已经落地的写入会被答成「更晚的 epoch 持有这个 Item」，
也就是**调用者被告知自己那份被接受的记录不在**。这一条也写进了注释，因为它不写在代码上就看不出来。

#### 9.34.6 为什么 receiver 是参数而不是字段

`continuation_state(record, *, receivers_seen=None, now=None)`。**receiver 列表是参数，永远不是
`record` 的字段。** 这是这个函数全部的安全性质：**一份能自己说出「谁会唤醒我」的记录，就是一份能
声称后台在跑的记录** —— 而后者写起来不花任何代价，读的人也无从分辨。所以没有任何一个 checkpoint
字段说谁会唤醒它；答案来自 fabric，`receivers()` 是这个 release 里唯一知道的地方，而
`RECEIVERS = ()` 是**按构造为空**、不是遗漏：它是唤醒那一半将来要填的注册表，在填上之前空元组
就是本机的量测。

三个答案，第三个不是失败：`SUPERSEDED`（**先于** receiver 问题回答，因为「谁在持有」先于「什么
会唤醒它」，而被取代记录的第二个答案说的是一个已经不是 owner 的 Parent）、
`AWAITING_CONTINUATION`（`says` 里是产品那句话本身）、`RECEIVER_INSTALLED`。每个答案都带 `says`，
包括那两句不是产品原话的 —— 一个要自己组织句子的读者会组织出一句不一样的，而第一句不一样的，
就是那句声称后台在跑的。

变异表里的 F7 把默认读注册表改成写死空列表，被
`test_a_filled_registry_changes_the_answer_without_touching_the_record` 杀掉；那条测试
**把 `RECEIVERS` patch 成非空**，所以今天的空元组是被量出来的，不是被写死的。

#### 9.34.7 变异表：16 条，16 杀

底是 `tests/test_continuation.py` 单文件（`64 passed in 4.91s`），只跑这一个文件 —— 这是更严的
量法：别的文件里也有判据可能顺手抓到这些变异，只跑本切片自己的测试才回答「本切片的判据钉住了
没有」。每个锚点都在其文件里**恰好出现一次**，第一轮 F15 的锚点出现 3 次（`if args.item:` 在
`runtime/cli.py` 里不唯一），当场记为 ANCHOR-MISS 并改锚重跑，不把一次锚错读成「这条判据活着」。

| 编号 | 文件 | 变异 | 杀它的测试 |
| --- | --- | --- | --- |
| F1 | `continuation.py` | `waiting_on.kind` 的闭集不再检查 | `test_a_kind_nothing_can_check_is_refused_by_name` |
| F2 | `continuation.py` | child 不再对着 admissions 核实 | `test_a_child_this_store_does_not_hold_is_refused` ＋ CLI 那条 |
| F3 | `continuation.py` | 窗口是否已过去不再检查 | `test_an_instant_in_the_past_is_refused` |
| F4 | `continuation.py` | 界从 `>` 改成 `>=`（400 被拒） | `test_a_long_next_action_is_refused_at_the_bound_and_not_before` |
| F5 | `continuation.py` | `True` 重新算作 epoch 1 | `test_a_bool_is_not_an_epoch_even_though_python_says_it_is_an_int` |
| F6 | `continuation.py` | `SUPERSEDED` 排到 receiver 问题之后 | `test_a_taken_over_record_says_so_before_it_answers_about_waking` |
| F7 | `continuation.py` | 默认不读注册表（空元组写死） | `test_a_filled_registry_changes_the_answer_without_touching_the_record` |
| F8 | `agent_control_store.py` | 重放比较把取代字段算进去 | `test_a_retry_of_an_accepted_write_survives_the_scope_changing_hands` |
| F9 | `agent_control_store.py` | 高 epoch 不再同事务取代低 epoch | `test_a_higher_epoch_takes_the_scope_over_in_the_same_transaction` 等 6 条 |
| F10 | `agent_control_store.py` | 被取代的 scope 退回「已有一个 live checkpoint」 | `test_a_changed_write_into_a_taken_over_scope_is_refused` |
| F11 | `agent_control_store.py` | 早 epoch 不得写进晚 epoch 的规则拿掉 | `test_a_new_record_below_the_live_epoch_is_refused` |
| F12 | `agent_control_store.py` | `supersede_checkpoint` 不再幂等 | `test_it_is_idempotent_on_the_first_instant` |
| F13 | `agent_control_store.py` | 取代不必点名后继者 | `test_it_names_a_successor_or_it_is_refused` |
| F14 | `runtime/cli.py` | 两种等待同给不再被拒 | `test_both_ways_of_waiting_at_once_is_refused` |
| F15 | `runtime/cli.py` | `--item` 不再过滤 | `test_the_item_filter_narrows_the_reading` |
| F16 | `runtime/cli.py` | live 计数不再排除被取代的记录 | `test_the_reader_shows_a_taken_over_record_and_its_successor` |

**两条等价变异如实列出，未计入 16 条**，因为跑了也不改变行为：`cmd_work_continuations` 里
`installed = continuation.receivers()` 与把 `receivers_seen=installed` 换成 `[]` —— **本机
`receivers()` 今天就是 `()`**，两种写法在今天的取值下是同一个问题。（它们不是「判据没被钉住」，
是这个变异体本身不改变行为；把它们写成 SURVIVED 会把一个恒等变换记成一条漏掉的判据。）

#### 9.34.8 本次读数

- `pytest tests/ -q -p no:cacheprovider` → **`5782 passed, 20 skipped, 3948 subtests passed, 0 failed`（669.25s）**。
- **跑了两遍**，因为 subtest 那一格需要一个复现判据：第二遍
  `5782 passed, 20 skipped, 3948 subtests passed`（640.97s）。两次的 passed/skipped/subtests 三项
  逐字相同，所以下面那条 +1 是**确定性的**（本片新增的模块被产物计数），不是宿主造成的浮动。
- 测试数算术**精确闭合**：上一格 5718 ＋ 本片新增的 **64 条**（`tests/test_continuation.py`，全部
  新增文件）＝ 5782；收集数 5802 ＝ 5782 passed ＋ 20 skipped。本片那 64 条**没有 subtests**，
  这一点是单跑量到的（`64 passed in 5.02s`，无 subtests 行），不是推的。
- **subtests 的 +1（3947 → 3948）也是本片造的，而且已经指名到底。** 这一条查得比它值得的久，
  过程记下来因为它同时是本片最重要的一条更正：本片自己的 64 条贡献 **0** 个 subtest（单跑已证），
  所以先按「宿主在两次跑之间变了」假设去找。找到的是
  `tests/test_artifact_install_smoke.py::test_every_shipped_module_imports_from_the_install`：
  它**按装好的产物里每一个 `.py` 开一个子测试**。而本片在 `runtime/` 下**新建了一个模块**，
  `plugin/bundle.yaml` 又按**目录**声明这个 code dir（`runtime: path: runtime`）—— 所以产物里多了
  一个文件，那个循环从 111 个子测试变成 112 个。直接建出产物问它，实测：`code_dirs()` 是
  `['abilities','bin','context','documents','fleet','intake','mcp','memory','relations','runtime','space']`，
  那个循环会开 **112** 个子测试，且**产物里确实有 `runtime/continuation.py`**。**算术闭合：
  5718 ＋ 64 ＝ 5782；3947 ＋ 1 ＝ 3948，那 1 就是本片新增的这一个被产出的模块。**
  顺带记一条关于台账自身的量测：**这个 pytest（9.1.1，未装 `pytest-subtests`）的报告对象上拿不到
  逐条测试的 subtest 计数**，所以「subtest 增量归因」在这种情形下只能靠把产物建出来问，不能靠读数。
- 这一跑读到的就是提交的那份字节：跑前记下四个文件的 sha256 —— `runtime/continuation.py`
  `85079def1361b150…`、`runtime/agent_control_store.py` `71cf23e8a775c302…`、`runtime/cli.py`
  `3dc1df2134c1359b…`、`tests/test_continuation.py` `71088c504be23477…` —— 跑完 `sha256sum -c`
  四项全 `OK`。跑动中只写过 `docs/CC-EXECUTION-LEDGER.md`（§9.33.8 的切片编号那一个字），而全树
  **没有测试读那份文件**（§9.33.7 已核过，本片复核未变）。
- **本片有一个必须先记的前置**：先跑 `tests/test_continuation.py` 单文件作变异检查的底
  （`64 passed in 4.91s`），16 条变异**全部被杀**（§9.34.7），然后才是这一跑全量。
- **本片改了 store 的写入语义（多出取代与三处新拒绝），而且安装产物内容变了 —— 这一句是本片
  落笔后自查改掉的，原来写的是「未变」，是错的。** `plugin/bundle.yaml` 按**目录**声明
  `runtime: path: runtime`，而本片在那个目录下**新增了一个模块**，所以打包出来的产物多了一个文件
  （上面那条 +1 就是同一个事实的另一个读数）。后果要说清楚：`serverosctl work checkpoint` 与
  `work continuations` 在**装好的节点上要等下一次 build→promote→restart 才可用** ——
  `runtime/cli.py` 的 `_continuation_module()` 按 `REPO_ROOT` 下的路径加载那个模块，装好的节点上
  `REPO_ROOT` 是产物，模块不在就加载不了。**那一次 build→promote→restart 是 P12 的**，不是本片的；
  本片没有动 `plugin/`、`mcp/` 或任何 composition，所以本片自己不触发它。

#### 9.34.9 它没做什么

- **仍然没有 scheduler**，`max_attempts` 仍然没有消费者（§9.31.8 第三条照旧）。本片造的是被唤醒
  的那一半所依赖的**记录**，不是唤醒本身。
- **交接那一半刻意未修，需要自己的切片。** `fleet/agent_control_router.handoff` 的 `checkpoint`
  参数被读、被校验非空、被记为一个步骤**名字**，然后**被丢弃**；`context_handed_off` 在
  `handoff.ok` 上**无条件**追加；`resolve_target`/`target_available`/`target_ready` 三个步骤名
  共用同一条未分化的判据。本片量清了它为什么不能顺手修：`checkpoint` 无法被投递给 `resume`
  （`params: []`），`session.create` 的 `instruction` 是唯一的载体，而合约明说 provider-native
  状态不会被移动 —— 所以那三个步骤名的含义**确实未定**，三种解读都符合合约原话。**先定「这个
  checkpoint 指的是哪一个对象」，再动它。** 本片的 checkpoint 与它不是同一个对象。
- `continuation_state` 没有 `now` 的调用者：参数在签名里（`window` 型等待的到期本可以用它判），
  但**没有任何生产方按到期唤醒**，所以本片没有拿它去判「到期了」，那会是第二个声称后台在跑的
  地方。这个参数留在签名里是因为它是那个判据将要读的东西。

### 9.35 P7 切片 G：`agent.list` 从「被声明的名字」变成「被服务的动词」

#### 9.35.1 这一片接的是哪一环：合约声明了它、一处 docstring 指向它、而没有工具服务它

`runtime/agent-session-contract.yaml` 的 `support.actions` 声明十一个 provider 动词，`admission`
把它们分进 `read_only` / `delegation` / `human_gated` 三组，而 §11 那一侧另有一道门断言这三组
**成划分**。这三件事都成立，且它们**都没有说过任何一个面是否服务这些动词**。其中两个 ——
`agent.list` 与 `agent.inspect` —— 被**没有任何工具服务**：

```text
合约：support.actions 十一个动词里，read_only 组列着 agent.list / agent.inspect
      （本片的门实测：11 个动词，只有这两个的名字本身已经带 `agent.` 前缀）
docstring：mcp/agent_session_service.py:142 的 _readiness_summary 对调用者说
      「the full record already travels on `agent.list` under `providers[]`」
      —— 它指名的那个地方在 HEAD 里不存在
实现：SessionService.agent_list 存在，但除它自己的测试之外没有调用者
```

**并且这一处引用必须写准，因为它比「两处」更锋利**：改动前 `git show HEAD:mcp/agent_mcp.py`
里 `agent.list` **一次都没有出现过**。而定义「哪些名字被服务」的正是这个文件里的 `TOOLS`，所以
那句 docstring 指名的是一条**当时不存在的路** —— 它同时是**唯一**一条说得出那条路该长什么样的
记录（合约只说动词存在，不说它由谁服务）。

**这一条是本节初稿自己写错、复核后改正的**：初稿记成「两处 docstring」并点了
`agent_mcp.agent_provider_list` 的名字，那个数字与那个位置都**没有实测**。复核后的全仓读数
（HEAD，按文件）：

```text
1  docs/ability-fabric.md          1  mcp/agent_session_service.py     ← 就是那一处
1  mcp/codex_app_server_provider.py  2  runtime/agent-session-contract.yaml
3  scripts/live_closure.py          2  tests/test_live_closure.py
0  mcp/agent_mcp.py                 ← 服务面的工具表，一次都没提过
```

把一份引用写多与写少一样贵：多写的那一处会把下一个人的核查引到一个**根本没说过这句话**的地方。

**为什么每一道已有的门都没发现它**：每一道门都在遍历一个**那两个名字不在其上**的清单 ——
服务面那道门遍历 `TOOLS`（它们不在），合约那道门遍历合约自己的分组（它们在里面，但那道门只
问「有没有两张表重合」，不问「有没有人服务」）。这是台账反复记的那一族缺陷的又一例，而这一例
的形式最干净：**两个清单各自都合格，缺的是它们之间的那一条**。

#### 9.35.2 它落在 L2，不是 L1

`declared_agents` 在 L1，来自 registry，而 L1「不联系任何东西」（`fleet/view-contract.yaml` 的
原话）。被**测量**到的那一半要问每一个 provider，所以它落在 L2：`fleet/view-contract.yaml` 的
L2 新增 `agents`，`fleet/view.py` 的 `row()` 在 `active_sessions` 之后加
`row["agents"] = self._agent_summary(ref)`。

**为什么不原地把 `declared_agents` 升级成测量值**：registry 说「这个设备**被授权为** Claude 服务」，
provider 说「这个设备**有** Claude」。两个不同的主张，两种不同的修法（一个去改登记，一个去看
provider 的传输），而把它们合成一个字段就等于**让一个字段同时为两件事作证**。合约里逐字写了
这一句，本片的 `agents` 字段说明也逐字重复了它。

#### 9.35.3 `_agent_summary` 为什么不并进 `_session_summary`

两者形状相同（一个 dict 带 `state`）、失败模式相同（来源不答 → UNKNOWN）、来源相邻
（`list_agents` 与 `list_sessions` 是同一个 provider 的两个方法）。合并的代价是**一个 callable
回答两个问题**，于是「provider 说不出它能跑什么」会悄悄变成「provider 什么都答不上」，然后一个
**有它自己理由**的 UNKNOWN 会被报在那个 session 来源的理由下。docstring 里这一句是这一片的
设计判词：它们是两个来源，所以是两个参数、两个 callable。

#### 9.35.4 单一设备规则是照抄的，不是重新推导的

`_sessions_for` 已经为这条规则付过一次账（它的 docstring 写着「`sessions: []` 会读成『那个设备
没有活动会话』—— 一句关于一台没人问过的机器的断言」）。`_agents_for` **直接复制它**，不重新推导：
只回答本进程持有 provider 的那一个设备；别的设备是 UNKNOWN，且 `detail` 同时点名**被问的设备**与
**本进程持有 provider 的设备**。两种声称是同一个谎的粗细两版：粗的是把本节点的答案戴上别人的名字，
细的是一句 `agents: []`。

#### 9.35.5 三条未知与两条拒绝，各说各的字

| 情形 | 答案 | 为什么不是更省字的那一个 |
| --- | --- | --- |
| provider 答不了 | `provider_errors[]` 里一条 + `complete: false` | 空清单会读成「设备没有 agent」；`complete` 若无失败可丢就成了装饰 |
| 问的是别的设备 | UNKNOWN，且**没有 `agents` 键** | 空数组是同一个谎的细版；`detail` 指出该找谁 |
| `agent.inspect` 的清单读不到 | 原样透传 UNKNOWN + `agent_id` | `NOT_FOUND` 是一句**关于舰队**的断言，而这里没人建立过一句 |
| `agent.inspect` 零匹配 | `NOT_FOUND` + `agents_listed` + `inventory_complete` | 拼错与没登记是两种修法，清单是唯一能把它们分开的东西 |
| `agent.inspect` 一对多 | `AMBIGUOUS` + 两个 `agent_ref` | 挑第一个就是**这个面替调用者决定了它指的是哪一个**；`agent_ref` 是无歧义的拼法 |

#### 9.35.6 整条记录原样透传，不按方法已知的键重建

`agent_list` 返回的是 `{"device_ref": ..., **inventory}`。这一条是被**自己吃到的一次缺陷**写进
注释的：早期草稿手写 `state` / `reason` 两个键，于是把 UNKNOWN 记录里那段「问了哪个设备、本进程
为哪个设备持有 provider」的 `detail` 丢了 —— **拒绝里那一半告诉人怎么修的东西**没了。同族缺陷
在本台账里已经出现过（§9.34.5 的「store 自己加的东西」），这里是它的一个正向版本：一个**减法**
造出来的同形缺陷。

#### 9.35.7 变异表：12 条，12 杀；其中一条是**先存活、再被钉死的**

| # | 变异 | 死因 |
| --- | --- | --- |
| G1 | 动词不再去取视图的 agent 来源（改读 `provider_readiness`） | 1 failed |
| G2 | 清单按方法已知的键重建（丢 `detail`） | 1 failed |
| G3 | 单一设备规则从 **agent** 来源里去掉（只去掉这一个，`_sessions_for` 那个留着） | 2 failed |
| G4 | 部分舰队报成整个舰队（`complete` 硬编 `True`） | 1 failed |
| G5 | **`build()` 不再给视图接 agent 来源**（删掉 `agents=`） | 1 failed ← 这就是那条先存活的 |
| G6 | 一对多时取第一个 | 1 failed |
| G7 | 零匹配时不说列了什么 | 1 failed |
| G8 | 读不到的清单变成一次 miss | 1 failed |
| G9 | 行不再携带被测量的清单 | 1 failed |
| G10 | `_agent_summary` 真的折进 `_session_summary`（直接转调） | 1 failed |
| G11 | 视图不再在 L2 声明 `agents` | 1 failed |
| G12 | 服务面不再声明那两个动词 | 1 failed |

**G5 的经过值得单独记，因为它是这一片唯一一条真正有信息量的读数。** 第一轮电池里它**存活了**：
把 `agents=` 从 `agent_mcp.build()` 里删掉之后，`TheAgentInventoryAndTheSourceItReads` 整类
**全绿**。原因不是判据弱，是**架错了对象**：那一类里每一个测试都用 `tests/test_agent_mcp.py`
**自己的** `build()` 搭图，而被测的那一个是 `mcp/agent_mcp.py` 的 `build()` —— 两个 builder，
一个被测。这正是本台账反复记的「两份拷贝」形状，也是 §9.35.1 那条缺陷的**同族变体**：清单给
自己发合格证，换成 builder 给自己发合格证。

补的那条门（`test_the_deployment_actually_wires_an_agent_source`）刻意跑**部署的** builder：
`agent_mcp.build(daemon=FakeDaemon())`，无网络。它可被证伪的原因是两条路的答案**不同字**：
接上了 → codex provider 从本机 app-server 答、botmux 因为没有 dashboard token 落进
`provider_errors`，答案是 `KNOWN` 且 `complete: False`；没接上 → UNKNOWN 加一个理由。补完之后
G5 被杀。

#### 9.35.8 本次读数

- 本片两个测试文件的定局读数：`tests/test_agent_mcp.py` + `tests/test_view.py`
  → **136 passed, 163 subtests passed in 15.75s**。
- 变异电池基线（七文件：`test_agent_mcp` / `test_view` / `test_node_mcp` /
  `test_fleet_transport` / `test_core_ability_boundary` / `test_human_console` / `test_global_board`）
  → **288 passed, 179 subtests passed**；十二轮里每一轮都是 `exit=1` 且**单点失败**
  （除 G3 那轮是 2 failed，因为它同时打在两个来源共有的那条规则上）。
- 全量第一遍：**5793 passed, 20 skipped, 3988 subtests passed, 0 failed（622.77s）**。
  算术闭合：上一格 5782 ＋ 本片 **11 条**（`TestTheSurfaceIsTheDeclaredOne` 两条 ＋
  `TheAgentInventoryAndTheSourceItReads` 九条）＝ 5793；subtests 3948 ＋ **40** ＝ 3988。
- **关于这一跑必须声明的一件事：它的归属不干净。** 它在跑动过程中被切片 H 的源码编辑穿过了
  （`mcp/human_control.py`、`mcp/node_mcp.py` 在本跑进行到 97% 前后被改），而本仓的测试有一批是
  在**测试执行时**用 `importlib` 从源文件装载被测模块的 —— 也就是那些用例读到的是 H 的字节。
  所以这一跑是「跑完时那棵树的绿」，**不是 G 的干净读数**。切片 H 落账时的那两遍全量会把这件事
  一起交代（**本行不点名一个当时还不存在的节号** —— 这正是本节 9.35.1 刚记的那条毛病），而这一跑
  如实记在这里而不是省略。

#### 9.35.9 它没做什么

- **没碰 `agent.session.*` 的任何行为**。十一个动词里只有此前没人服务的那两个被接上；其余九个
  一个字节未动。
- **没有把 `declared_agents` 升级成测量值**（理由见 §9.35.2）。
- **没有让 `agent.inspect` 从别处取源**：它由 `agent_list` 组成，理由与 `agent_provider_inspect`
  由 `agent_provider_list` 组成相同 —— 一个从自己的源读的 inspect 能报出一个列表里没有的 agent，
  于是同一个问题有两个答案。
- **`worker_stopped_by_item` 那条邻接发现仍然未做**（§9.33.8 那一格），本片只是把它的编号占位
  改正了。
- 没有新增任何动词、没有扩大服务面的宽度：`agent.list` / `agent.inspect` 是被**声明过**的两个，
  这一片做的是让声明是真的，不是加新的。

### 9.36 P7 切片 H：§5 唯一可信 context 门第一次有了生产者

#### 9.36.1 这一片接的是哪一环：一道门从来没被敲过

切片 G 走的是「供应商声明了一个没人服务的动词」。这一片走的是它的**最贵版本**：一个动词
被服务了、被文档写了、被测试测了，而**没有任何生产者**。

`runtime/daemon.py#_r_agent_context_attach` 是 §5 唯一的铸 context 通道（它自己的 docstring
第一行就这么写：「Mint a trusted context from a provider report. §5's only doorway.」）。在
本片之前，从 HEAD 上量的调用者清单是：

```text
git grep -n "agent\.context\.attach" HEAD -- | cat
  runtime/daemon.py          7 处：全是它的实现、派发表、注释
  tests/test_actor_context.py  6 处
  tests/test_approval.py       1 处
  ← 生产代码里 0 个调用者
git grep -n "provider_pid" HEAD -- | cat
  runtime/daemon.py:2174      1 处：它是**读者**
  tests/…                     5 处：全是手写的夹具
  ← 生产代码里 0 个**生产者**
```

所以「变更型 Agent Session 动作从来没有从活跃调用者通过准入」**不是推测，是可达性事实**：
`fleet/agent_control_router.route` 对每一个 mutating 动作要求可解析的 `context_ref`，而能铸出
一个的东西只被测试敲过。整条控制面是**只能由测试抵达**的 —— 测试自己手搭 report、自己传 peer
字典，于是那道「报告必须由出示它的那个进程观察」的门**从来没有对过一个真进程**。

#### 9.36.2 为什么 `provider_pid` 属于**动作**而不属于报告

`context/actor.py` 的 `PROVIDER_REPORT_FIELDS` 九项里**刻意没有** `provider_pid`，而
`digest_of` 只哈希 provider 自己的字段 —— 于是同一个事件重放不会铸出第二个 context。
`tests/test_actor_context.py:150` 那条测试把理由写成了断言：

```python
second = actor.build({**VERIFIED_REPORT, "provider_pid": 9999}, now="…:45+00:00")
assert first["context_ref"] == second["context_ref"], (
    "provider_pid is deliberately outside the digest: it binds the report "
    "to a process, and a restart must not make the same event a new context")
```

**这一条是整片设计的枢纽**：它意味着 `provider_pid` 是 `attach` 这个**动作**的属性，不是报告的
字段。所以生产者不能去「填一个字段」，它只能**出示自己是谁** —— 而 `runtime/daemon.py:2174` 那
一侧拿 `_peer_credentials(connection)`（`SO_PEERCRED`，**内核**填的）去比。给一个别的 pid 不是
绕过，是撞上那里按名字点出两个 pid 的那条拒绝。

#### 9.36.3 `attach_context` 的四条不可选

| 不做什么 | 为什么 |
| --- | --- |
| 不收 `report` 参数 | 能交证据的调用者就是 §5 明令禁止的伪造身份模型；这道门可信，正因为它只放行跑过探针的那个进程 |
| pid 不由调用者给 | 探针 `botmux actor current --json` 是**本进程的子进程**，所以「谁观察的」与「谁出示的」按构造是同一个进程；`pid` 参数只为让测试**从这条路上**触发错误观察者拒绝，而不是绕过去 |
| 连 conversation 都不由调用者选 | `identify` 会**优先**采信传入的 `conversation_ref` —— 对一个问「我已有的那个会话」的调用者是对的，在这里是错的：记录里那一栏说的是**这次身份是在哪个 turn 里被观察到的**，能写它的调用者就能操纵审计轨迹。所以铸造时不传，探针自己的 `chatId`/`messageId` 落进记录；探针没点名 turn，报告就缺一个必填字段，`build` 拒掉 —— **没有 context，而不是一个有看起来合理的位置的 context** |
| 未 VERIFIED 的 provider 什么都不产生 | 拒绝掉的 context 不是空 context：`actor_ref` 是 `None`，一个 ref 都不铸，因为 context 永远是**关于某个人**的 |

**第三条是这一片被自己改掉的一个伪造面。** 初版签名有 `conversation_ref` / `message_ref` 两个
参数，写完之后才发现 `identify` 的优先序会把它们当成权威 —— 于是补了一条测试
（`test_a_probe_that_names_no_turn_mints_nothing`）把「探针没报 turn 就没有 context」这个
**后果**钉住，而不是只在 docstring 里写一句。

#### 9.36.4 三种失败各去一个地方，和本机的诚实活体结局

四种结局分开报，因为每一种把读者送去不同地方：

| 结局 | 含义 |
| --- | --- |
| `attached` ＋ `context_ref` | 控制面会用这个 ref 解出 actor |
| `BLOCKED` / `UNSUPPORTED` | 没有识别出人，没有东西可铸；`detail` 是 provider 自己的句子 |
| `REFUSED` | serverosd **答了**，答的是不。`detail` 指出是哪种机制 |
| `UNREACHABLE` | serverosd 根本没答 |

**本机的活体结局是 `BLOCKED`，而且它是正确答案。** 从不是 BotMux turn 的进程里跑探针，
`botmux actor current --json` 返回 `{"status":"blocked","error":"current_actor_unverified"}`
（退出码 0），于是 `attach_context` 返回 `attached: false`、`state: BLOCKED` —— **不是**伪造
一个 context。这与本模块 docstring 里那句「它不声称知道的东西，它就不声称」是同一句。

#### 9.36.5 `DaemonRefused`：一次拒绝曾被报成「控制面不可用」

`mcp/node_mcp.py#DaemonClient.call` 在收到 `{"ok": false}` 时抛的是
`DaemonUnavailable` —— 也就是说，**在控制面按设计正常工作的那一刻，调用方被告知它不可用**。
伪造的 payload、由别的进程观察的报告、这个 actor 不许做的动作，全都从同一个类型出口出去。

新增 `DaemonRefused(DaemonUnavailable)`：**子类**，所以只想要「没有答案」的调用者照旧全捕获，
一处都不用改；想要分开读「答案是不是」的可以分开读。两种情形的修法不同 ——「serverosd 没在听」
是去修一个故障，「serverosd 拒绝了这个调用」是去读一个决定。

**这条不是设计出来的，是踩出来的。** 本片初版 `attach_context` 把 `DaemonClient.call` 的返回
当成信封读（`answer.get("ok")`），而它的契约是：**拒绝时抛异常、成功时返回已拆封的 `result`**
（不是 `{ok, result}`）。症状是三个测试失败在 `state: REFUSED, reason: null, detail: null`；
排查是把原始返回单独打印出来（一个带 `context_ref`/`actor_ref`/`observed_by_pid`/`freshness`
的字典，**没有 `ok` 键**）。这也是「拒绝要能被读」这件事必须在类型上分开的直接原因：混在一起
时，一个读错层级的调用者给出的症状与「控制面挂了」一模一样。

#### 9.36.6 消费者：`scripts/live_closure.py` 曾经错在哪六层

这个脚本是 H 的消费者，也是「§5 终于有了真实调用者」这句话的证据。它此前**从来没有跑通过**，
而它的测试全绿，因为它错在七处，其中七处是**它的桩与它互相印证**。逐层：

1. **裸名**：它发 `session.create`，而合约与服务面说的是 `agent.session.create`。
2. **`agent.list` 不带 `device`**，而那是它的必填键。
3. **信封键错**：它发 `{"ref": ref}`，而 action 的字段叫 `session_ref`。
4. **缺必填**：`context_ref`、`session_role`、`runtime` 都没有；`session_role` 在整条栈里
   **没有默认值**（它是「这段会话是为了什么」的陈述，默认值会把猜测记成事实），本片按
   `coordinator` 报 —— 跨 Item，因此不带 `bound_item_ref`。
5. **把 `provider` 当 `create` 的参数**：它不是参数（provider 从解析出的目标来），而
   `agent_mcp._payload` 只挑已知键，所以这个字段是**被静默忽略**的。
6. **读错了响应层级**：它读 `created["session_ref"]`，而 `AgentControlResponse.show()` 把
   provider 的答案放在 `result` 里，所以要读 `created["result"]["session_ref"]`。
7. 而以上六处能被**同时**写错而测试全绿，是因为 `tests/test_live_closure.py` 的 `FakeClient`
   实现了**同一批裸名、同一种信封、同一处层级**。两个桩互相印证，谁也证明不了图。

本片把脚本改成说真实的图（真实动词名、真实信封、`device`/`runtime`/`context_ref`/
`session_role`、`created["result"]["session_ref"]`），并让它**自己先铸一个可信 context**
（`attach_context`），铸不出来就带 `state`/`detail` 退出并说明 BLOCKED 是正确答案。

#### 9.36.7 那次「桩遮住了接线」现在有一条常驻的门

新增的 `EveryCallTheHarnessMakesIsOneTheSurfaceDeclares` **驱动而非阅读**：把脚本真实发出的
每一个 `(name, arguments)` 拿去与 `mcp/agent_mcp.py` 自己的工具表对账 ——
名字在 `tool_names()` 里、每个键在该工具 `inputSchema.properties` 里、每个 required 键齐全，
并且每一个 mutating 调用都真的出示了 `context_ref`（这一条单独断言，因为
「schema 要求」与「实际发出」是两件事，一次编辑就能把它们分开）。

三个变异，逐个被杀：

| # | 变异 | 死因 |
| --- | --- | --- |
| M1 | create 用裸名 `session.create` | 11 failed（名字门 ＋ 其余） |
| M2 | create 不带 `context_ref` | 2 failed（schema 门 ＋ context 门） |
| M3 | create 带上 `provider` 参数（旧脚本的习惯） | 1 failed（`properties` 里没有它） |

同时改掉的两条旧断言：`test_the_followup_is_sent_to_the_session_the_create_returned` 从
`sent[0]["ref"]` 改成 `sent[0]["session_ref"]`；`FakeClient` 回答的是真实的四个动词名与
`{"ok":…,"result":…}` 形状。

#### 9.36.8 本次读数

- 本片直接触及的五个测试文件：`test_actor_context` ＋ `test_node_mcp` ＋ `test_agent_mcp` ＋
  `test_agent_sessions` ＋ `test_live_closure` → **208 passed, 163 subtests passed in 14.63s**。
- `tests/test_live_closure.py` 单文件：**20 passed in 0.11s**（改前 14 条）。
- 三条**反向**读数，都是实测出来的，而不是「应该成立」：
  **(a)** 每一轮变异前 `rm -rf __pycache__`（理由见内存里那条「同尺寸同秒的『还原』骗得过 pyc
  校验」——不这么做，「这条判据活着」会是缓存造出来的假结论）；
  **(b)** 每一轮之后**逐字节**比对备份（H 这一片用 `cp` 备份 ＋ `diff -q` 复核，
  收尾时输出 `restored-identical`；G 那一片的电池里是 `assert sha(path) != pre` 与跑完
  `assert sha(path) == pre` 两条断言）；
  **(c)** 全程**不用任何 git 还原类动词** —— `checkout --` / `restore` 从引用取值，看起来像
  还原、实际是丢弃。H 这一片的五次变异里每一次都复核了恢复；G 那一片的十二轮另加断言。
- **活体读数：没有。** 这个脚本会在 bot 聊天里发消息，而且现在还会先铸一个 context ——
  按 §21 那属于「生产/共享关键效应」，**需要用户批准，本片没有擅自跑**。它的活体结局由
  §9.36.4 的探针读数代表：在非 turn 进程里它会在铸 context 这一步停下并说 BLOCKED，那正是
  设计要的行为，而不是一次失败。

#### 9.36.9 它没做什么

- **没有造第二个 context 生产者。** `agent.context.attach` 仍然只有一个入口，本片做的是让
  那一个入口**第一次有真实调用者**，不是放宽它。
- **没有唤醒/接收方那一半。** `runtime/continuation.RECEIVERS` 的 `tick()` 消费方仍然不存在；
  在那之前 `continuation_state` 说 `RECEIVER_INSTALLED` 就是产品明令禁止的「伪造后台运行」。
- **没有修交接的 `checkpoint` 缺口**（§9.34.9 第二条照旧）。
- **没有跑一次真实 `live_closure.py`**（见 §9.36.8 最后一条）。
- 没有把 `--attach` 变成默认：它是一个动词，退出码报的是**有没有铸出 context**（`0` / `1`），
  与这个 CLI 其它模式（都是问题，因此都退 `0`）刻意不同。

---

## §9.37 切片 I：等待被求值（`runtime/continuation.wait_state` ＋ `work continuations`）

本片的编号是 §9.34.9 第二条与 §9.36.8 都指过的那一环：P7 延续链里「子级结果到达 → 继续」
的前半截。切片 F（§9.34）造了 checkpoint，切片 H（§9.36）造了它的准入缺口，而 checkpoint
里那个 `waiting_on` 字段从切片 F 起就是**被写、被校验、被打印、从来没有被求值**的。

#### 9.37.1 那个洞是量出来的，不是推出来的

`command grep -rn "waiting_on"` 在 `runtime/continuation.py` 之外只命中 `runtime/cli.py` 的
**写入**与**打印**两处（`--waiting-on-child` / `--waiting-on-window` 的解析，和两段把它渲染成
文字的代码）。没有任何代码决定「等的东西到了没有」。所以：

- 一份 checkpoint **写下的那一刻**与**子级完成一周之后**读起来完全一样；
- `continuation_state` 只有 `AWAITING_CONTINUATION` 一种真实结局 —— 那不是懒惰，是**没有第二
  个问题可问**；
- `docs/daily-work-cycle.md:158` 列的那条链（核 scope/owner/预算 → 去重原 attempt → 唤醒
  Parent → Parent 决定下一步）第一格就断着：「真实 child 结果或合法时间窗口到了吗」没人答。

#### 9.37.2 落在 L2 的问题问在 L2 的权威上

`wait_state` 只问两个权威，两个都不是它自己造出来的：

- **`window`** —— 时钟。`until` 与 `now` 用 `_instant` 读，**与 `build_checkpoint` 接受它们时
  同一个量具**。边界是 `>=`：窗口在 `until` 那一刻就已经结束，这正是 `build_checkpoint` 对
  **新**记录写 `until <= now` 就拒的那条比较。
- **`child`** —— 准入集合，也就是 `build_checkpoint` 已经拿它校验过 child 等待的那个集合。
  **`retired_at` 是唯一判据**，理由是它是这个 store 里唯一一个**只由 `retire_admission` 写**的
  字段。`phase` 刻意不算：`DISPATCHED` 只表示 `runtime/bounded_invocation.invoke_once` 写了
  回执（子级**被调用过**），而回执本身可以写着 `EXITED`（子级跑完并返回了）——那仍然不是
  「这一轮的结果被记下来了」。把 phase 当结果，就是在一个子级还在跑（或没人决定它的退出了
  算什么意思）的时候恢复 Parent。

#### 9.37.3 三个答案而不是布尔，中间那个是要点

`WAIT_ANSWERS = ("WAIT_OVER", "WAIT_OPEN", "WAIT_UNANSWERABLE")`。一个**读不出**的子级不能被
逼着回答「还没」或「已经」：并进「还没」会把 Parent 永久停在某个 store 故障后面；并进「已经」
会在没有任何证据的情况下恢复它 —— **后一种才是这套词汇存在的目的：让它无法被表达**。

`admissions=None`（没人把 store 带来）与 `admissions={}`（store 里没有这个子级）**分开报**，
因为修法不同：一个是去取 store，一个是去找那个子级。两者都是 `WAIT_UNANSWERABLE`，
`detail` 说清是哪一个。

**部分完成永不读成完成。** 一份 checkpoint 可以点多个子级，等待在**最后一个**有结局时结束；
`settled` / `outstanding` / `unheld` 三个清单把「哪些是哪些」写在答案里，不让人从布尔去推。

#### 9.37.4 求值不是唤醒 —— 本片最想被记住的一条

`wait_state` 是这套东西里**最容易被接进唤醒半边**的东西：子级完成了，那总该有东西去继续
Parent 吧。那正是 §17.4 点名的诱惑。本片没有接，理由是这一条**不是风格而是事实**：
`runtime/daemon.py` 的 tick 既不读准入也不读 checkpoint，`RECEIVERS` 仍是空元组 ——
本 release 没有任何东西会自己跑。所以：

- `continuation_state` 的答案**一个字没变**，包括产品原句 `checkpoint 已保存，待续`；
- `RECEIVERS` 仍然为空、`receivers()` 仍然返回它；
- `wait_state` 的 `says` 里明写「a new Parent can resume this Item」，`detail` 里明写
  「it says the wait is over, not that anything has resumed the Parent」；
- `tests/test_continuation.py::TestAnEvaluatorIsNotAReceiver` 钉住这个分离 —— 它是将来有人
  手滑把求值器接进唤醒半边时**第一个会红的**测试；
- 变异 I10 就是那次手滑（让 `cmd_work_continuations` 在 `WAIT_OVER` 时把
  `receivers_seen` 填成非空），**1 failed 被杀**。

这条与切片 H 的 `provider_pid` 是同一种纪律：**一个字段能回答的问题，与它能被拿去回答的
问题，不是同一件事**。

#### 9.37.5 求值器的消费者：两个动词，不是一个

按 §5 第 6 条「代码有了但没接 consumer 不算落地」，求值器带着它的读数一起落地：

- `work continuations`（读半边）：新增 `waits`（按 ref 的答案映射）、`read_at`，摘要行
  `waits N over, M still open, K unreadable`，`resumable` 行**逐个点名**（一个能行动的答案不该
  只给计数），每条记录的行头从 `{ref}  {state}` 变成 `{ref}  {state}  wait {WAIT_STATE}`，
  `says` 两问各印一句。
- `work checkpoint`（写半边）：写完**当场**报一次 wait。理由：一个 Parent 在**子级结果已经
  到达之后**才 checkpoint，等于什么都没等，而**写这条记录的人**是最后一个无需被问就会看到
  这一点的读者。F 的测试 `test_a_retired_admission_still_answers_for_the_child_it_records`
  已经确认「已退役的准入仍然可以为它的子级作证」，所以这条路径是真的可达，不是假想。
- 时钟用 `--now`、复用 `work grants` 的 `_reading_clock`。**没有引入第二个读时钟**：同一个
  「这次读数发生在哪一刻」有两个拼写就会有第二套拒法（拒绝无时区的、拒绝读不出的），而
  那份会漂移。`work checkpoint --at` 不动 —— 那是**写**时钟，由 `build_checkpoint` 校验。

#### 9.37.6 本片改了什么

| 文件 | 改动 |
| --- | --- |
| `runtime/continuation.py` | 模块 docstring 增「AND AN EVALUATOR IS NOT A RECEIVER」一段；新增 `WAIT_ANSWERS`（含「three and not a boolean」的理由）；新增 `wait_state(record, *, admissions=None, now=None)` |
| `runtime/cli.py` | `cmd_work_continuations` 读 admissions、求值、印摘要与逐条 `wait`、JSON 加 `waits`/`read_at`、`--now`；`cmd_work_checkpoint` 写完报一次 `wait`；argparse 加 `--now` |
| `tests/test_continuation.py` | docstring 从「Four properties」改成「Five properties」并把求值列为第 5 条；`DAY_AFTER` 常量（附「`LATER` 不是它」的注释）；新增 §4 两个类共 20 条；CLI 段新增 4 条 |

#### 9.37.7 变异表：11 条，11 杀

| # | 变异 | 死因 |
| --- | --- | --- |
| I1 | 把 `phase` 也当成有结局（`retired_at or phase in (LEASE_HELD, DISPATCHED)`） | 1 failed（`being_invoked_is_not_having_finished`） |
| I2 | 读不出的子级报成「还在等」 | 4 failed |
| I3 | 读不出的子级报成「已结束」 | 4 failed |
| I4 | 没带 store 折叠成空 store | 1 failed |
| I5 | 窗口在 `until` 那一刻仍然算开着（`>` 代 `>=`） | 2 failed（边界那一条） |
| I6 | 缺读时钟时回落到墙上时钟 | 1 failed |
| I7 | 求值器不认识的 kind 当成 child 处理 | 1 failed |
| I8 | 没有 `waiting_on` 的记录读成「开着」 | 1 failed |
| I9 | 写动词不再报它刚记下的 wait | 1 failed |
| I10 | 求值器开始填 `receivers_seen`（§9.37.4 说的那次手滑） | 1 failed |
| I11 | 读半边不再点名哪个 checkpoint 可续 | 1 failed |

三条纪律与 G/H 两片相同：每轮前 `rm -rf __pycache__`（`tests/test_continuation.py` 用
`importlib` 从显式路径装载 `runtime/continuation.py`，**吃 pyc 缓存**）；每轮后逐字节 `diff -q`
复核（收尾输出 `restored-identical`）；全程不用任何 git 还原类动词。

#### 9.37.8 本次读数

- `tests/test_continuation.py`：**88 passed in 8.06s**（`git diff` 数出新增 24 条、删除 0 条，
  所以改前是 64 条）。
- 本片相关六文件（`test_continuation` ＋ `test_serverosctl` ＋ `test_declared_commands` ＋
  `test_declared_implementations` ＋ `test_core_ability_boundary` ＋ `test_admission`）：
  **333 passed in 27.30s**。
- 渲染读数（`ARTIFACTS_ROOT/tmp-p7/wait-read` 上的一次真实跑动，不是断言）：同一份记录，
  子级未退役时读作 `waits 0 over, 1 still open, 0 unreadable` ＋ `WAIT_OPEN`；
  `work retire cyc-1` 之后**不改记录**再读，读作 `waits 1 over, 0 still open, 0 unreadable`
  ＋ `resumable SRV-0001#1` ＋ 行头 `SRV-0001#1  AWAITING_CONTINUATION  wait WAIT_OVER`。
  **两问并列**：子级结束了，而没有东西会唤醒写下它的那个 Parent。
- **一次被作废的全量跑动，如实记下。** 本片开头在 `b7327c7` 上起了两遍全量（任务
  `bae9zizag`），跑动中本片的源码编辑穿过了它（第一遍尚未结束时 `runtime/cli.py` 已在改），
  所以那次读数**归属不干净**，已用 `TaskStop` 停掉而不是留下来当交付读数 —— 这与 §9.35.8
  把「跑动中被编辑穿过」的全量判为不干净是同一条规矩。**在切片 I 提交后于冻结树上重跑**
  的读数，记在 §9.37.10。

#### 9.37.9 它没做什么

- **没有唤醒/接收方那一半。** `RECEIVERS` 仍然是空元组，`runtime/daemon.py` 的 tick 仍然不读
  准入也不读 checkpoint。`WAIT_OVER` 不是「正在自动继续」，它只回答「现在能不能继续」，而
  **继续一直是新 Parent 做的事**（`docs/daily-work-cycle.md:158` 那条链的「唤醒实际 Parent」
  一格照旧缺着，而且它缺的**不是求值器**）。
- **没有 `--at` 那一半的 `waiting_on` 依赖事件。** `WAIT_KINDS` 仍然只有 `child` 与 `window`；
  模块 docstring 里「第三个 kind 刻意缺席」那段照旧，本片没有加 kind。
- **没有去重原 attempt。** `docs/daily-work-cycle.md:158` 那条链的「去重原 attempt」是
  `child_attempts` 的消费者，仍然没有 —— 本片只造了第一格。
- **没有修交接的 `checkpoint` 缺口**（§9.34.9 第二条照旧：被丢弃的参数、无条件追加的
  `context_handed_off`、四个步骤名对一个判据）。
- **没有跑一次真实 `live_closure.py`**（§9.36.8 最后一条照旧）。

#### 9.37.10 冻结树上的全量读数

`b6a07cef52f304a49098529e2026e707a689a88d`，跑前 `git status --short` 0 行，
2026-09-26T05:28:19+08:00 起跑，连跑两遍：

| 遍 | 读数 | 用时 |
|---|---|---|
| 1 | 5831 passed, 20 skipped, 3988 subtests passed | 622.31 s (0:10:22) |
| 2 | 5831 passed, 20 skipped, 3988 subtests passed | 613.11 s (0:10:13) |

跑后 `git status --short` 仍 0 行、`git rev-parse HEAD` 未变。两遍逐字相同，所以它不是
一次侥幸。跑动期间工作树**没有被编辑穿过** —— §9.35.8 与 §9.37.8 记下的两次作废读数
都是这个原因（起跑后又在改源码）。本片把这条纪律用成了前置条件：源码编辑全部在起跑
之前完成并提交，然后才在冻结的提交上跑。

## §9.38 切片 J：§9 的交接程序，五处被声明的东西没有消费者

切片 F 造了 `fleet/agent_control_router.handoff`（§9 的唯一实现），并在 §9.34.9 里
**刻意没有顺手修它**，理由是三种解读都符合合约原话：「先定『这个 checkpoint 指的是
哪一个对象』，再动它」。本片定的就是这个 —— 然后沿着这个答案把五处逐条钉死。

### 9.38.1 那个 checkpoint 指的是哪一个对象

**是延续存储里的记录 ref，不是状态本身、也不是一个开关。**
`runtime/continuation.py#checkpoint_key(item, epoch)` 返回
`f"{work_item}#{int(epoch)}"`（例如 `SRV-0001#1`），`build_checkpoint` 把它写在
`checkpoint_ref` 字段里，`serverosctl work checkpoint` 打印的正是它 —— 而 `mcp/agent_mcp.py`
给这个参数声明的类型本来就是 `_STR`。产品 ref 与工具 schema 一直是同一个东西，
中间那层是唯一读错它的一层。

所以第一步的判据从「有个真值」变成「有一个非空字符串」：`HandoffOutcome` 新增声明字段
`checkpoint: str | None`，在**每一个** return 上记录（包括拒绝），于是「没给」与「没填」
在答案里是两个可分辨的东西。消费者是下一个 Parent，经 `serverosctl work continuations`
按 Item 读那张表 —— 交接传的从来不是状态，是**哪一个记录持有状态**。

### 9.38.2 为什么不把它前传给 provider

这是 §9.34.9 说「必须先定」的那一问，也是本片唯一一处**明确写下「没有通道」**的地方，
理由不是懒：`agent.session.resume` 在 `fleet/agent-control-contract.yaml` 里是
`params: []` / `optional: []`，loopback 的 resume 分支（`_dispatch`）不读任何参数。
把 ref 塞进 `target_params` 会得到三层里最坏的一种失败 —— 合约看着对、调用点看着对、
下一层把它丢掉而**没有一层会报错**。这正是这个仓库反复量到的形状（声明了没有读者的
东西），所以写进合约的 `handoff.checkpoint`：

- `is`：ref，不是开关；
- `consumer`：下一个 Parent，经 `work continuations`；
- `is_not_forwarded`：不前传，因为没有通道，并**点名** `params: []` 与 resume 分支；
- `refs_are_not_parsed_here`：router 记录 ref 的**原值**，不解析它的格式 ——
  `checkpoint_key` 拥有格式，第二个解析器就是第二个答案；
- `step_order_against_the_resolver`：下面 9.38.4 那张表。

### 9.38.3 `context_handed_off` 曾经可以在一个什么都没确立的 `ok` 上追加

`created.ok` 只说目标**作答了**，不说目标**持有会话**。改前 `handed = created.result or {}`
之后**无条件** `completed.append("context_handed_off")` —— 于是 `release_source: True` 会在
一个什么都没确立的 `ok` 上停掉源，停掉**唯一一个有人有证据存在的会话**。

这条缺口可达，而且不是靠构造出来的：loopback 的 `_record`（「没有 `session_ref` 的作答
要拒」）**只对 create 生效**（`_dispatch` 里只有 `session.create` 分支走到 `_record`），
`session.resume` 把 provider 的答案原样返回。改后 `session_ref` 缺失即 `INVALID_CONTEXT`、
停在 `context_handed_off`、源保留 —— 与 `_record` 对 create **同词同因**：控制面没有收到
的 ref，就是 ServerOS 叫不出名字的会话。

### 9.38.4 三个步骤名共用一条判据，其中一个是**不可能**被拒绝的

`_stop_step(reason)` 从**理由词**反推步骤。读过生产方之后这条判据对两个词都错：
`DEVICE_UNREACHABLE` 与 `NODE_RUNTIME_UNAVAILABLE` 各有**两个不同平面的生产者** ——
resolver（一个路由都没命名）与 transport（路由已命名、送不到）。按词映射对其中一个必错，
实际对两个都错：`target_available` 成了任何失败都到不了的步骤名，而「设备解析出来了、
节点运行时够不着」被报成 `target_ready` —— 叫调用者去等一个永远不会 ready 的设备。

现在读 resolver **自己写的两个事实**，两个都不在这里推导：

- `resolved["refused_by"]`：`abilities/target.py#resolve` 写的拒绝步骤
  （`record["refused_by"] = refusal["step"]`），经 `HANDOFF_STEP_FOR_RESOLVER_STEP` 映射。
  未映射的步骤抛 `TargetError` —— 与 `_refuse` 对理由词同风格：要么在一张表里拒绝，
  要么不拒绝。
- `answer.route`：resolver 自己的 `route_kind`。`_step_route` 是链条的**最后**一步，
  所以一个命名了的 route 就说明前面每一步都同意了。`NAMED_ROUTES = ("local", "fleet")`。

| resolver 步骤 | 交接步骤 | 为什么 |
|---|---|---|
| `device` / `agent_runtime` / `provider_instance` / `adapter` | `resolve_target` | 这四步回答「**哪一个**目标能承载」，拒绝即没解析出来 |
| `support` / `readiness` / `admission` | `target_ready` | 能承载它的目标**不**现在做 |
| `route` | **没有对应项，而且是点名的** | 见下 |

`route` 是这张表里唯一被点名的缺席：`_step_route` **只在更早的步骤把 `route_kind` 置为
`"none"` 时才拒绝**，而链条在那里就断了 —— 所以 `refused_by == "route"` 不可达。
`test_every_resolver_step_has_a_handoff_step_or_a_reason_it_has_none` 钉的是这个枚举差：
`unmapped == ["route"]`，resolver 加一步而两张顺序表没对齐就是一条转红的判据，不是一个
猜出来的停止点。

### 9.38.5 §9 的第六步此前**没有任何调用者能到达**

`_stop_request` 构造停止请求时不转发 `request.approvals`，于是
`abilities/target.py#_step_admission` 里 `approval.decide(approvals=[])` 恒返回
`APPROVAL_REQUIRED`。`source_released` 被声明、被排序、被写进 `HANDOFF_STEPS`、
被 `work` 的渲染读着 —— 而它是**不可达的**，`release_source` 是一个不可能有效果的参数。
本片转发 `approvals`。

这不是 §22 旁路：批准仍由 `approval.decide` 针对**停止自己**的 action / target / params
判定，为 handoff 铸的批准（action 不符）不匹配，为**别的 session** 铸的批准报
`APPROVAL_BINDING_MISMATCH` 并让源继续跑。两条都有用例：
`test_a_release_a_person_approved_releases_the_source`（第一次到达 `source_released`）与
`test_an_approval_for_another_session_leaves_the_source_running`（真的批准、不同的会话、
源保留）。

### 9.38.6 第五处是写测试时撞上的，不是读出来的

`to_device_ref` 缺席时 `_resume_request(request)` 把目标请求寻址到 `""`，
`abilities/target.py#_validate_request` 抛 `Rejected` —— 一个异常从一个「不是答案就抛异常」
的契约里穿出去，调用者无法与 router 的 bug 区分。合约把 `to_device_ref` 列在
`optional`，而 optional 不等于不读。现在在跑 resolver 之前报 `INVALID_CONTEXT`、停在
`resolve_target`。

**这条是切片 J 里唯一一条「测试先于认识」的缺陷**：五条新用例里有五条同时以
`Rejected` 冒泡失败，而失败信息（`request.device_ref is required`）指向的是一个我原本
以为已经修好的地方。它是这一片真正的第 5 处，而不是 4 处加一个测试写法问题。

### 9.38.7 变异表：10 条，10 杀；其中一条差点存活

| # | 变异 | 结果 |
|---|---|---|
| J1 | checkpoint 退回真值判断 | 3 failed |
| J2 | 忽略 `resolved["refused_by"]`，只按 route 判 | 1 failed |
| J3 | `agent_runtime` 映射到 `target_ready` | 1 failed |
| J4 | 命名了的 route 送不到也报 `target_ready` | 1 failed |
| J5 | 停止重新丢下 approvals | 2 failed |
| J6 | 一个没命名会话的 `ok` 也释放源 | 1 failed |
| J7 | outcome 不再携带 checkpoint | 1 failed |
| J8 | 对应表里少一个 resolver 步骤 | 2 failed |
| J9 | 没有目的地的交接重新寻址到空设备 | 1 failed |
| J10 | 能承载却不肯做的目标报成没解析出来 | 1 failed |

**J2 值得单独记：它在第一版里会存活。** resolver 的每一次拒绝都发生在 route 步骤
**之前**，所以 `answer.route` 在那些答案上都是 `None`，「读 `refused_by`」与「没有 route
就退回 `resolve_target`」给的是同一个答案 —— 判据看着是活的，其实两个读法不可分辨。
补一条在 `support` 步被拒（`ACTION_UNSUPPORTED`，应当是 `target_ready`）的用例，两个
读法才分开，J2 随即被杀。这与 §9.35.7 那条「先存活、再被钉死」是同一种发现：
**变异存活不是电池坏了，是它指出了一个判据的真空白。**

纪律与 G/H/I 三片相同：每轮前 `rm -rf __pycache__`（`tests/` 用 `importlib` 从显式路径
装载模块，**吃 pyc 缓存**，同尺寸同秒的还原会骗过它），每轮后逐字节复核（收尾哈希与
备份相等），全程不用任何 git 还原类动词。

### 9.38.8 本次读数

- `tests/test_agent_control_router.py`：**84 passed, 7 subtests passed in 16.37s**
  （改前 72 条用例，新增 12 条）。
- 本片相关六文件（`test_agent_control_router` ＋ `test_target_resolver` ＋
  `test_approval` ＋ `test_agent_mcp` ＋ `test_coordination` ＋ `test_continuation`）：
  **393 passed, 170 subtests passed in 38.09s**。
- 冻结提交 `a58716da79bac2c3d197f307140787dd33999ccf` 上的两遍全量读数见 §9.38.10。

### 9.38.9 它没做什么

- **没有唤醒/接收方那一半**（§9.37.9 第一条照旧）。本片修的是交接**程序内部**的四步，
  交接完成之后「谁来把下一个 Parent 起来」仍然没有实现者。**但本片顺手量清了它缺的到底是
  什么，结论与直觉相反：缺的不是一个架构决定，是两件具体的东西。** 写在这里以免下一个人
  从零再推一遍。

  先排除一个错误答案。要唤醒一个 Parent 就得**创建会话**，而 `session.create` 按准入是
  `delegation` 类 → `ALLOW`（`runtime/agent-session-contract.yaml:270`），所以卡住它的
  不是准入。卡住它的是 **actor**：每一个变更型 session 动作都从 `context_ref` 解析 actor，
  而全仓只有一个铸 ref 的门 —— `mcp/human_control.py#attach_context`，它**没有 `report`
  参数**（「一个能交出证据的调用者，就是 §5 禁止的那个伪造身份模型」），`provider_pid`
  由内核按 `SO_PEERCRED` 与 socket 对端核对（`runtime/daemon.py:2174`），而 provider
  自己在 BotMux 轮次之外答 BLOCKED —— 一次没验证过的观测**什么都不铸**。所以「daemon 自己
  起来伪造一个 actor」这条路是被关闭的，不是靠约定关闭的。

  但「谁在**没有真人在轮次里**的情况下授权一个 cycle」这个问题，**架构已经回答了**，答案在
  `context/delegation.py`：**「a grant is a scope on a trusted context, and it authorizes a
  cycle only while that context still IS one」** —— grant 的 `delegated_by` 必须解析到一个
  `context/actor.py#authorizes` **此刻仍然接受**的 context，所以 grant 不能比它派生自的那次
  委托活得久、不能靠改自己的 `expires_at` 延长、不能靠写一个文件凭空造出来；而这个映射的
  唯一消费者是 `intake/daily_cycle.select_focus`。也就是说，自动继续的 authority **本来就
  设计成「一个人委托出去的 context」**，而不是「一个 daemon 给自己铸的身份」。一个持有有效
  grant 的 receiver 去唤醒 Parent，不是在伪造身份 —— 它是在搬运一个**人已经委托过的**目标。

  于是真正缺的是两件具体的东西，都不是设计空白：
  1. **一条真的跑这个检查、并且是 trigger owner 的 routine。** `runtime/daemon.py` 的 tick
     既不读准入也不读 checkpoint（`runtime/continuation.py:33-40` 把这句话写成了模块
     docstring），`RECEIVERS`（`runtime/continuation.py:123`）是「唤醒那一半将来要填的
     注册表」，`receivers()` 是它唯一的读口 —— 在填上之前空元组就是本机的量测。
  2. **一条真的被记录下来的 grant。** 本机 store 里 grants / admissions 为 0（§9.37 之后
     的读数），而「记录工作循环的第一次真实委派」是一个**已经摆在用户面前等决定**的条目。
     一条 routine 加一条不存在的 grant，什么都不会发生 —— 这正是 `RECEIVERS = ()` 在说的
     那句话，而不是「不知道 actor 该是谁」。

  这两件的顺序也是确定的：routine 的唯一职责是**去查那条 grant 还在不在**（delegation 的
  四态里只有 `AUTHORIZED` 会把 grant 放进映射），所以没有 grant 就没有可查的对象。**本片
  没有做这两件**：它们属于「工作循环的第一次真实委派」那条线，本片只把它**是什么、缺什么、
  为什么不缺决定**量清楚并留在这里。
- **没有把 checkpoint 投递出去**，并且是明确地、写进合约地不投递（9.38.2）。这不是
  「本片没做」，是这个参数**在目标架构里就不该到 provider 那一层**。
- **没有改 `WAIT_KINDS`**，`waiting_on` 仍然只有 `child` 与 `window`（§9.37.9 第二条照旧）。
- **没有 `child_attempts` 的消费者**（§9.37.9 第三条照旧）。
- **没有跑一次真实 `live_closure.py`**（§9.36.8 最后一条照旧）。
- **没有把 `NodeControlTransport.send` 对 `connector(...)` 的异常包起来。** 探针那条路
  是包的（`resolve_inputs` 的 `try/except Exception: return {}`），投递那条路不是：
  `send()` 第 824 行 `answer = self.connector(envelope) or {}` 裸露，而 `route()` 只捕
  `TransportFailure`（`TransportUnavailable` 继承的是 `FleetError`，不是它）——
  所以 `connector` 抛 `TransportUnavailable` 会一路穿到调用者。今天不可达（部署里
  `connector` 是 `None`），但**装上第一个真实 connector 的那天**它就是「设备没答」
  变成一次未捕获异常。本片**没有修**：它属于 transports 那一层而不是交接程序，而且
  修它要动「哪些异常算拒绝、拒绝用什么词」这条线 —— 那是 `fleet/transport.py` 与
  理由词表的事。记在这里以免丢掉。

### 9.38.10 冻结提交上的全量读数

`a58716da79bac2c3d197f307140787dd33999ccf`（本片提交），跑前 `git status --short` 0 行，
2026-09-26T05:57:09+08:00 起跑，连跑两遍：

| 遍 | 读数 | 用时 |
|---|---|---|
| 1 | 5843 passed, 20 skipped, 3993 subtests passed | 633.50 s (0:10:33) |
| 2 | 5843 passed, 20 skipped, **1 warning**, 3993 subtests passed | 630.63 s (0:10:30) |

跑后 `git status --short` 仍 0 行、`git rev-parse HEAD` 未变，所以跑动期间工作树**没有被
编辑穿过** —— §9.35.8、§9.37.8 记下的两次作废读数都是这个原因，本片把「源码编辑全部在起跑
之前完成并提交」用成了前置条件。

与切片 I 的同一棵树相比：5831 → **5843** 条、3988 → **3993** 个 subtest，正好是本片新增的
12 条用例与 5 个 subtest，没有别的用例被牵动。

**两遍之间唯一的差异是第二遍多出一条 warning，这里不平掉它。** 读数文件只留了
`pytest -q` 摘要的尾三行（`command grep -E "passed|failed|error" | tail -3`），所以那条
warning 的**身份没有留在证据里**，本片没有回去把它补抓 —— 补抓要再跑一遍十分钟的套件，
而那会把一条已知的不一致换成一条未知的。能说的是：两遍在**会红的那些数上逐字相同**
（5843 / 20 / 3993），且两遍摘要行里都没有 `failed` / `error` —— `-q` 的摘要只在有失败时
才印那些词。**pytest 自己的退出码没有被捕获**：那条命令的末端是 `command grep … | tail -3`，
记录的 0 是管道末端的。所以「两遍都通过」的证据是摘要行，不是退出码，这一句不能混着说。

## §9.39 用户点名的那张卡：判词已经在活体上，而且是「不发」

这一节不是一次实现，是一次**量测**。用户 09-24 直接点名要求改一件事：

> ✅ 没有需要处理的变化 / 09-24 18:48 更新 / … / 只读告警 · 未执行终止、重启或清理操作
> 这都没需要报警 为什还弹出来 就别谈出来了啊 去改一下

台账里此前**没有这一条的落点**（`command grep -n "没有需要处理的变化"` 在
`docs/CC-EXECUTION-LEDGER.md` 上 0 命中）。本片顺手把它量清楚，因为 §12 的终报要能对它
给出一个带证据的答案，而不是一句「已经修了」。

### 9.39.1 那句话是谁说的

`runtime/cards.py:291` —— 卡面在既没有事件、也没有 integrity 主体时的返回值就是
`"✅ 没有需要处理的变化"`。所以用户看到的是**卡面模型**的一句真话：这一刻确实没有东西
值得占一行。让这句话变成一条「弹出来」的，不是这句话本身，而是**有一个东西在它上面加了
一句「这也是个事件，发出去」**。

### 9.39.2 闸门与卡面曾经是两条规则

`tests/test_attention.py#TheGateAndTheFaceAreOneRuleTests` 的 docstring 把这个形状记成了
本机真实发生过的事（原文照抄）：

> the gate counted 460 findings at high/medium as worth a card while the face classified
> every one of them `watch` and printed none, so a card went out once an hour saying
> `✅ 没有需要处理的变化`.

也就是说：**闸门**（`alert_digest.decision`）按 severity 算「值一张卡」，**卡面**
（`alert_model` / `presentation`）按另一套判据算「值一行」，两套判据在一台机器上并存，
于是每小时发一张自己都印不出东西的卡。修法不是调阈值，是把两条规则合成一条：
`decision` 只能由**卡面印得出来的东西**造成，并且按 code 在**整个闭合映射**上逐条对齐
（docstring 的判词：「Agreeing per code across the whole closed mapping is the only form
of this check that cannot come back a third time through a code nobody thought about.」）。

### 9.39.3 活体读数：它现在是「不发」，而且理由就是用户那句话

2026-09-26T06:01+08:00 于活体上直接读（`serverosctl alert digest --json`，与 ops 的 timer
每 5 分钟跑的**同一条只读命令**）：

```text
generation 252
counts    NEW 496 · ESCALATED 0 · REOPENED 1 · RECOVERED 7391
backlog   94
decision  send: false
          interval_seconds 3600.0
          seconds_since_last_digest 57786.05
          immediate []
          why: "438 finding(s) at high/medium, and none of them may occupy a line on
                the card — a card caused by a finding it cannot print arrives saying
                there is nothing to do"
```

`why` 那一句就是**用户那句话的判词版**：「一张由它印不出来的 finding 造成的卡，到达时说的
就是没有事情要做」。三个可以核的点：

1. `send: false` —— 这一轮不会发卡，而此刻 `counts.NEW` 是 496，所以它不是「因为没有增量
   才不发」，是**判据算出这些 finding 没有一个能占一行**。
2. `seconds_since_last_digest ≈ 57786s ≈ 16.05h`（约 09-25 13:58 北京时间）—— 距离上一张
   真的发出去的卡已经 16 小时，与「这条闸门在活体上一直在拒」一致。
3. `interval_seconds 3600.0` —— 「一小时一张」的节奏由 `decision` 自己携带，不是由 timer
   的 5 分钟周期决定的；timer 只是来问。

### 9.39.4 它是**活的**，不只是源码里有

用户问的是「为什么还弹出来」，所以「源码里修了」不是答案，**装上的那一版里修了**才是。
三处实测把这条钉住：

- `which serverosctl` → `~/.local/bin/serverosctl` → `runtime-assets/hwz-server-os/
  serveros-linux-work/current` → `revisions/alert-card-scope-20260924@8ce6fe7`。
- `current` 这个符号链接的 mtime 是 **2026-09-24 19:43** —— 在用户那张 18:48 的卡**之后**，
  而该 revision 的提交名就叫 `alert-card-scope`。
- 那一版里 `runtime/cards.py` 有这句话、`runtime/alert_digest.py:863 decision()` 的初值是
  `{"send": False, …}`，只有判据成立的两处（`:931`、`:980`）才置 `send = True`。

所以处置是：**用户 18:48 投诉 → 同日晚 19:43 promote 一个叫 `alert-card-scope` 的 revision
→ 今天在活体上读出来是「不发」**。这不是本片修的，本片做的是把它量成一条**有证据、
有名字、有时间**的结论，因为它此前在台账里没有任何落点。

### 9.39.5 顺带量到、但没有被这条闸门解释的一件事

`backlog 94` 与 `counts.NEW 496` 说明：登记表里仍有 438 条 high/medium、94 条积压，
而**这张卡现在对它们一个字都不说**。这是设计的选择而不是漏报 —— 卡面把它们全部归为
`watch`，而 `watch` 不占行。设计上的承接者是**例行状态卡**（每个工作日一张、同一消息
按小时补丁），不是这张按事件走的告警卡。本片**没有**去改这个分工，只把它记在这里：
「告警卡安静」与「没有东西需要看」在 438 这个数字面前不是同一句话，而读到这份读数的人
应当能自己分开它们。

## §9.40 切片 K：写进记录、校验过、归档过，然后从来没有被读回来

### 9.40.1 缺的那一格，以及它是怎么被量出来的

链上那一格是设计权威自己写的读法：`docs/daily-work-cycle.md:197` 要求恢复前对账
「baseline、writer、effect 和**旧 attempt**」，`docs/SCENARIO-ACCEPTANCE.md:139` 把
「child 在 Parent 退出后完成 → 新 Parent 查原 Task/receipt 并接受一次」和三条反例放在
同一行，其中一条正是「新建第二 Task」。这一格的缺口是**机械的**：

- `child_attempts` 声明在 `runtime/continuation.py:75` 的 `CHECKPOINT_FIELDS` 里（11 个
  字段之一）；
- 由 `work checkpoint --attempt` 写入，它的 help 就是「a child attempt already made」；
- 由 `build_checkpoint` 校验（`continuation.py:309`），而它的拒绝文案
  「an unnamed attempt cannot be looked up again」**自己就说明了这个字段为什么存在**；
- **零读者。** `work continuations` —— 一个恢复中的 Parent 唯一会读的那个面 ——
  此前只渲染 11 个字段里的 7 个。

用 AST 探针在切片 K 的基线提交 `072cb65` 上量（`probe_rendered_fields.py`，只解析
`runtime/cli.py` 里 `cmd_work_continuations` 函数体内的 `record.get(<str>)` /
`record[<str>]`，注释与 docstring 不算）：

```text
before (072cb65): 7 of 11 declared fields read by cmd_work_continuations
  not read: ['epoch', 'requirement_revision', 'evidence_locators', 'child_attempts']
after (working tree): 10 of 11 declared fields read by cmd_work_continuations
  not read: ['epoch']
```

四个没被渲染的字段里有三个是同一类事实：**这条记录是在什么前提下写的**（哪一版
Requirement、已经带着哪些被接受的证据、已经做过哪些 attempt）。留下的唯一豁免是
`epoch`，理由写在测试里：它由 ref 承载（`checkpoint_key` 产生 `{work_item}#{epoch}`，
而 ref 是每条记录那行的标题）。

三条反例里有两条**已经被机制盖住**，只有第三条没有：

| 反例 | 现在的机制 |
|---|---|
| 旧结果晚到覆盖新 epoch | `put_checkpoint` 对低 epoch 的写入具名拒绝；等值重放幂等（§9.35 起） |
| 要求用户复制结果 | `build_checkpoint` 对 `waiting_on` 里 store 看不见的 child 具名拒绝 |
| **新建第二 Task** | **此前没有任何机制**：`child_attempts` 写进去以后没有人读回来，所以一个恢复中的 Parent 看不出这件事已经有人做过了 |

### 9.40.2 本片的四笔

1. **`runtime/continuation.py` 新增 `attempts_state(record, *, admissions=None)`** 与
   三值答案 `ATTEMPT_ANSWERS`（`RETIRED` / `OPEN` / `UNANSWERABLE`）**加第四个
   `ATTEMPT_NONE`**。第四个不是「都完成了」，也不是「说不出来」：它是一个
   `build_checkpoint` 明确允许的事实——这条记录没有具名任何 attempt——而把它印成另外
   两个中的任何一个，都是在告诉一个恢复中的 Parent「你没开始的事都做完了」。
   形状照 `wait_state`：三而不是布尔（中间那个是重点）、`detail` 与 `says` 分开、
   `evidence` 每项带 `because`、absent store 与 empty store 归同一个 state 但
   `detail` 不同、absent 那支**不带** `unheld`（「这个 store 不持有它」是关于一个**被
   读过的** store 的陈述，而没有人把 store 带来）。
   它**不读时钟也不读看板**（`test_it_takes_no_clock` 断言签名里只有 record 和
   admissions）：`wait_state` 需要时刻是因为窗口是一次比较，attempt 不是——store 要么
   持有一条退休、要么不持有。
   docstring 明写 **`ACCEPTED` 不是它的 state**：本机没有任何地方把「接受」记成与记录
   自己的 `evidence_locators` 分开的事实（`work checkpoint --evidence` 的 help 就是
   「an accepted evidence locator」），所以「哪个结果被收进了这条 Item」是从记录读、由
   恢复的人去对账，而不是这个函数去猜。
2. **`runtime/cli.py#cmd_work_continuations` 渲染三个字段＋逐 attempt 一行＋顶层计数行。**
   `requirement` / `accepted` / `attempts`；absent 与 empty 分开印成两句不同的话
   （「does not name a revision」对「carries no accepted evidence」）；
   记录那行现在是三个答案并列
   `SRV-0001#1  AWAITING_CONTINUATION  wait WAIT_OVER  attempts ATTEMPT_RETIRED`。
   逐 attempt 那行只印「哪一个、什么状态、结局是什么」，因为计数行说不出**哪一个**还
   开着，而「哪一个」正是一个恢复中的 Parent 来找的东西。
3. **`runtime/agent_control_store.py#retire_admission` 的第二次退役不再静默。**
   见 9.40.4。
4. **`runtime/agent_control_store.py#supersede_checkpoint` 同一处形状。** 这个方法是
   `retire_admission` 的邻居（它自己的 docstring 就写着「Idempotent on the FIRST
   supersession, like `retire_admission`」），而它的静默那一半更重：第二次给出**不同的
   后继**时，记录会继续说是第一个 —— 一个 scope 两个后继就是两个 owner，而
   `docs/COLLABORATION-CONTRACT.md` 一瞬都不允许。本片只改它的规则，**没有给它加调用
   者**（它在生产里仍无调用者，`put_checkpoint` 在同一个事务里内联做这件事），那是另一
   个问题。

### 9.40.3 两处被改写的既有断言，都要说明

改既有断言在这条线上不是小事，所以两处都留在原地、都写明为什么：

| 位置 | 原来断言的事 | 为什么改 |
|---|---|---|
| `tests/test_admission.py#test_retiring_twice_keeps_the_first_instant` | 第二次退役带 `outcome="ABANDONED"`，断言 `again["outcome"] == "DELIVERED"`，理由写作「the second call must not rewrite how the cycle ended」 | 「保留第一个结局」是对的，**静默**地保留不是。`outcome` 现在是 `attempts_state` 读的那个字段，它会印在一句决定「要不要派第二个 child」的话里；一个说 `DELIVERED` 而同时被告诉过 `ABANDONED` 的记录，是在替一个没人做过的选择作答 |
| `tests/test_continuation.py#test_it_is_idempotent_on_the_first_instant` | 第二次 `supersede_checkpoint` 传 `by="SRV-0001#3"`，断言记录仍说 `SRV-0001#2` | 同上，而这里更硬：两个后继就是一瞬两个 owner |

**这不是在推翻一个被接受的架构决定。** 两处原 docstring 都只为「时刻」辩护
（`retire_admission` 的原文是「it keeps the FIRST retirement … a second call that moved
that instant」），**从未就 `outcome`／`by` 推理**；台账 §9.37.2 里当时量到的也是
「**`retired_at` 是唯一判据**，理由是它是这个 store 里唯一一个**只由 `retire_admission`
写**的字段」——当时那句话是对的：`outcome` 同样只由 `retire_admission` 写，但它那时不
决定任何事，所以「唯一判据」的说法站得住。本片把它变成了决定一件事的字段（`attempts_state`
印它、而那行决定要不要派第二个 child），于是它得按同一条房规走。房规就在同一个文件里，
`confirm_admission` 的 docstring 原文：「idempotent when the evidence is equal and refused
when it is not … picking the newer one here would hide the disagreement rather than record
it」。

### 9.40.4 一条规则，两个方法，四种调用

规则一句话：**一次退休（一次交接）只写一次，写它的是第一次调用；后来的调用只许重复，
不许改。** 落到四种调用上：

| 后来的调用 | 结果 | 为什么 |
|---|---|---|
| 不带 outcome 的重放 | 幂等，记录一字不动 | 它对「结局是什么」没有做出任何声明。拒绝它会告诉一个调用者「你不同意」，而它什么都没说 |
| 带**相同** outcome | 幂等 | 它和记录说的是同一件事 |
| 带**不同** outcome | **具名拒绝**，拒绝里同时印出调用者给的与记录里的 | 两个不同的结局是一个给人看的问题，不是一次更晚的写入 |
| 在记录**没有** outcome 时给出一个 | **具名拒绝** | 「没有结局」是一个值而不是缺席：第一次调用记下的就是「释放了预留、结局没有写下来」。允许补写就等于给了这个字段第二条写入路径，而那条路径不是任何东西的重试 |

`supersede_checkpoint` 照同一条规则（第二个后继等价于「带不同 outcome」那一格）。
`cmd_work_retire` 的 docstring 也一并改了，它此前只讲时刻。

### 9.40.5 枚举差测试：把「声明」和「渲染」连起来

`child_attempts` 能一直不被读回来，机制上的原因是：**没有任何东西把记录的字段表与打印
它的那段代码连起来**。所以本片的修法之一就是补上这条连接。

`tests/test_continuation.py#TestEveryDeclaredFieldIsRenderedOrExempt` 读
`runtime/cli.py` 的 **AST**（不是文本：注释与 docstring 里提到的字段不算被读），取出
`cmd_work_continuations` 函数体内所有 `record.get("X")` / `record["X"]`，然后要求：

- `CHECKPOINT_FIELDS` 里每个字段要么在集合里，要么在一张**具名豁免表**里（唯一的条目是
  `epoch`，理由是它在 ref 里并且 ref 是每条记录的标题）；
- 豁免表里不允许有**已经会被读**的字段（否则理由会和代码漂开，测试就不再意味着什么）；
- 豁免表里不允许出现记录里没有的字段；
- 豁免理由必须是一句真话，不是占位（少于 8 个词算不合格：「没人要」不是理由，
  「它已经在同一屏上了，在这里」才是）。

这条测试是活的，由变异 K8/K9 证明：把 `requirement` 那行的 `record.get(...)` 换成
`None`，或者把 attempts 那行改成读 `made_here["attempts"]`（功能等价，但看不到它在读
字段），枚举差测试都会红。

### 9.40.6 变异表：13 条 13 杀

`mutate_slice_k.py`，每条改**源码**、`rm -rf __pycache__`、只跑声称覆盖它的那些用例、
要求至少一条转红，然后 `cp` 还原并用 sha256 逐字节核对（每一轮都核，收尾再核一次）。
13 条全部被杀，没有幸存者：

| # | 变异 | 转红的用例数 |
|---|---|---|
| K1 | 第四个答案印成第三个（`ATTEMPT_NONE` → `ATTEMPT_RETIRED`） | 1 |
| K2 | 读不到的 attempt 不再是它自己的答案（`if unheld:` → `if False:`） | 2 |
| K3 | 已退休的 attempt 不再带回结局（`outcome` → `None`） | 1 |
| K4 | 没带 store 的那支也给出 `unheld` 列表 | 1 |
| K5 | 词汇表少掉第四个词 | 2 |
| K6 | `attempts_state` 长出一个 `now` 参数 | 1 |
| K7 | 顶层计数行数错那一栏 | 1 |
| K8 | `requirement` 那行不再读记录（枚举差测试同时红） | 2 |
| K9 | attempts 那行不再从记录读字段（功能等价，只有枚举差测试能看见） | 1 |
| K10 | 记录那行的 `attempts` 印成 wait 的答案 | 1（另一条通过，红一条即算杀） |
| K11 | 第二次退役又变回静默吸收 | 2 |
| K12 | 同一次重放变成拒绝 | 1 |
| K13 | 第二个后继又变回静默吸收 | 1 |

跑完 `git status --short` 0 行、HEAD 仍是 `0f00e63`，两个被改的文件与备份逐字节相同。

#### 9.40.7 全量跑：四遍 rc=0，而计数只有第四遍抓到了

冻结提交 `0f00e6312726878448c452680ebab72d5dbb1853`，跑前跑后 `git status --short` 都是 0 行、
HEAD 未变。解释器 `/data/home/vince/envs/python/serveros-test/bin/python -m pytest <worktree>/tests -q`。

| 遍 | 起点 | rc=0 于 | 耗时 | 读数 |
|---|---|---|---|---|
| 1 | 06:28:33 | 06:39:06 | 633 s | **计数丢失**，见下 |
| 2 | 紧接第 1 遍 | 06:49:30 | ≤624 s | **计数丢失** |
| 3 | 06:49:58 | 07:00:19 | 621 s | **计数丢失** |
| 4 | 07:02:14 | 07:12:40 | 623.15 s | **5870 passed, 20 skipped, 3993 subtests passed** |

**前三遍的计数是真的丢了，不是没跑。** 捕获写成 `… | tail -4`（第三遍改成 `tail -12`，仍然
没用），而 `sys:1: ResourceWarning: unclosed file <_io.TextIOWrapper name=… encoding='UTF-8'>`
这一族是**解释器收尾时**打到 stderr 的，位置**在 pytest 摘要行之后**，于是被 tail 窗口整段挤掉：
第一、二遍只留下 4 条 warning、第三遍留下 12 条，摘要行一次都没进文件。退出码这一侧是对的
（`${PIPESTATUS[0]}`），四遍都是 0；第四遍改成「原始日志落盘 + 只 grep 摘要行」才把它读出来。
**本片的全量证据是四遍 rc=0，而计数只有第四遍一份。**

三条与算术有关的读数：

- **passed 的算术闭合。** 上一格（切片 J）的读数是 5843；
  `git diff 072cb65 0f00e63 -- tests/` 数出本片**新增 27 个 `def test_`**
  （`test_continuation.py` 23 条、`test_admission.py` 4 条），**删掉 0 条**。
  5843 ＋ 27 ＝ **5870**，与第四遍逐字相符。（`072cb65` 是切片 J 的台账提交，只动了
  `docs/CC-EXECUTION-LEDGER.md` 一个文件 317 行，所以它和切片 J 的代码提交在测试计数上
  是同一棵树。）
- **subtests 不变：3993 → 3993。** 本片新增的用例没有一条用 subtest，所以这一格不应当动，
  它确实没动。
- **warning 是哪一种要看清楚。** 第四遍摘要行逐字是「5870 passed, 20 skipped, 3993 subtests
  passed in 623.15s」，**里面没有 warning 计数** —— 也就是 pytest 自己计数为 0，而上面那族
  `ResourceWarning` 是在摘要打印**之后**由解释器收尾打出来的，**不在 pytest 的计数里**。
  这同时回答了切片 J 那格留下的悬念：J 第二遍摘要里写的「**1 warning**」是被 pytest **计数**
  的，因此与本片这族收尾期 warning **不是同一个身份**，本片不主张自己这族是它。
- **那族 warning 本片没有追。** 对象是「一个 raw fd 上包出来的 `TextIOWrapper`」
  （`os.fdopen` 的形状，所以 `name` 是描述符号而不是路径，读不出是谁泄漏的）。唯一便宜的
  排查是看调用点：`runtime/` 与 `intake/`、`relations/` 里所有文本模式的 `fdopen` 都在 `with`
  里（`ability_projection.py:336`、`understanding_brief.py:428`、`understanding_patch.py:606`、
  `understanding_semantic.py:2171`、`project.py:433`），`bounded_invocation.py:147` 是二进制
  模式且在调用处被 `with` 包着 —— **所以它不在本片的改动范围内，也不能由本片顺手断定是谁**。
  定位它要再跑一遍十分钟的套件加 `-W error::ResourceWarning`，那是另一件事，本片没有做。

### 9.40.8 它没做什么，以及仍未关闭的

- **`attempts_state` 不回答「哪个结果被接受了」**，这是刻意的而不是没做完：本机没有
  接受记录（`evidence_locators` 是记录级的），加一个 store 是一个更大的决定，本片把它
  写成一条断言（`test_an_acceptance_is_not_a_state_it_can_answer`）而不是悄悄留白。
- **`WAIT_KINDS` 仍然只有 `child` / `window`**，`work continuations` 里因此没有「已
  登记的依赖事件」这一 kind。原因没变（没有任何 store 说得清 Item 之间的依赖），本片
  没有动它。
- **`supersede_checkpoint` 在生产里仍无调用者。** 本片改的是它的规则，不是它的接线；
  给它加调用者需要一个真实的丢场景，且 `put_checkpoint` 已经在同一个事务里内联做了这
  件事，所以「这个方法是死代码」本身是一个独立的问题。
- **唤醒那一半仍然空着**，原因已在 §9.38.9 量清（缺的是一条跑 delegation 检查、并且是
  trigger owner 的 routine，与一条真的被记录下来的 grant）。
- `attempts_state` 的 `outcome` 是 `retire_admission` 的原话，本片没有给它加词表——一个
  自由文本字段在记录里出现一次和出现两次是同一类问题，但收口它需要先有消费者愿意按
  词表判。

### 9.41 P7 切片 L：一条异步边能不能回答「是否已被接受」

#### 9.41.1 链上这一格是设计自己命名的动作，而代码里已经有一句拒绝替它开了口

`docs/CC-EXECUTION-PLAN.md:126` 把 P7 的链写成一步接一步：「…实际事件/有界poll唤醒后继
Parent→**查原attempt并接收一次**。输入冻结accepted revision、效果/预算、确切工作树、
receiver」。切片 K 交付的是「查原attempt」；**「接收一次」是这同一步剩下的那一半**。

这不是我从动词里推出来的。`runtime/continuation.py#build_checkpoint` 拒绝「一个 child 等待
却不具名任何 child」时的原文就是：

> a child wait names no child, so there is nothing for a later Parent to look up **and
> nothing to accept the result of**

**代码自己已经把这个动作命名了**，而本机没有任何地方记它。

设计权威逐字，四处，各自说的是同一件事的不同面：

- `docs/ABILITY-OPERATING-MODEL.md:247`（A6 那一行）：producer = child result / Quay Task
  terminal，receiver = 当前 Work Item Parent，receiver 不可用时「**结果durable保存为待接收**；
  新Parent可接原结果，不重派」。
- `docs/ABILITY-OPERATING-MODEL.md:257`：「每条异步边至少能够回答原producer
  identity/revision、当前receiver、**是否已被接受**、是否仍适用于当前Work Item/epoch。
  timeout/断线先查同一attempt/result，再决定是否补派」。
- `docs/COLLABORATION-CONTRACT.md:52`：交接对象那九行关系里就有一行
  `| accepted_by | 接收者及其验证结果，所接受的确切输入输出版本 |`；`:55` 接着说
  「发送资料只是offered；接收者确认版本、权限、能力和验收边界后才accepted。**accepted表示
  接了任务，不表示结果已验。** producer送达文件后不能自动推进父阶段；**parent根据真实结果
  接受/要求具体修订/登记准确缺口**」。
- `docs/daily-work-cycle.md:162`（B4 的验收清单）：「**一次child结果只被接受一次**」；
  `docs/SCENARIO-ACCEPTANCE.md:139` 把「child在Parent退出后完成 → **新Parent查原
  Task/receipt并接受一次**」和三条反例放在同一行。

`COLLABORATION-CONTRACT:55` 里有**两个** accepted，不能混成一个：接收者接了**任务**
（`ABILITY-OPERATING-MODEL.md:61` 的「接收者确认可执行才accepted」），和 Parent 收了**结果**。
本片做的是后者。前者在这个 fabric 里是 grant/scope 的事，并且它已经落在 admission 的
`phase`/`confirmations` 上（`DISPATCHED` 那一步是被报告过的回执），本片不动它。

#### 9.41.2 读数：本机没有这个事实

`probe_acceptance_gap.py`（只解析源码，不导入树里的任何模块 —— 全量跑在旁边进行时，
导入会写字节码缓存，那是下一轮变异会读到假结论的那条老陷阱）。三条清单一并印出来，
因为缺口是它们之间的**关系**，不是其中任何一条：

```text
1. what an admission record carries
   1a. the record `admit` stores — the whole shape, written once
       line 1383: ['cycle_ref', 'intent_digest', 'selection_digest', 'selection',
                    'grant_ref', 'reservation', 'lease', 'action_key', 'phase',
                    'admitted_at', 'confirmations', 'retired_at', 'outcome']
   1b. the keys the other two writers add afterwards (key -> written by, line)
       confirmations    confirm_admission:1477
       confirmed_at     confirm_admission:1492
       outcome          retire_admission:1566
       phase            confirm_admission:1491
       retired_at       retire_admission:1564

2. the statuses the invocation receipt can carry
   RECEIPT_STATUSES   ['STARTED', 'EXITED', 'REFUSED', 'TIMED_OUT', 'OUTPUT_LIMIT',
                       'START_FAILED', 'IO_FAILED']
   the receipt's own keys   ['action_key', 'descendants_verified',
                             'direct_process_exited', 'effect', 'input_digest',
                             'process_exit_code', 'read_only_requested',
                             'started_at', 'status', 'worker_stopped']

3. the answers the resuming reader can give
   ATTEMPT_ANSWERS    ['ATTEMPT_RETIRED', 'ATTEMPT_OPEN', 'ATTEMPT_UNANSWERABLE',
                       'ATTEMPT_NONE']

4. every line in runtime/ that DEFINES an acceptance-shaped name
   cli.py:468  def accept(self, packet):
   daily_report.py:161  def accepted_kinds(document: dict | None = None) -> tuple:
   daily_report.py:181  def refuse_unaccepted(named_inputs, document: dict | None = None) -> None:
```

- admission 的 13 个字段（`admit` 一次写下的那一份）里没有它；
- 回执的 `status` 是一串**执行**结局（`RECEIPT_STATUSES` 逐字引自
  `runtime/bounded_invocation.invoke_once`：STARTED / EXITED / REFUSED / TIMED_OUT /
  OUTPUT_LIMIT / START_FAILED / IO_FAILED），`effect` 说的是「这个进程有没有留下效果」，
  都不是「这个结果被收下了」；
- 切片 K 刚写下的 `ATTEMPT_ANSWERS` 四个词里也没有它，而且 K 的 docstring 里有一句
  「ACCEPTED IS NOT A STATE THIS FUNCTION HAS」—— 那句在写下时是真的，本片让它不再真，
  所以要**在同一处改写它并写明为什么**，而不是留着一句过期的话。

`command grep -rn "accept" runtime/*.py` 的结果同样只有散文：runtime/ 里带 accept 的**定义**
只有 `cli.py:468 def accept(self, packet)`（node 传输的握手）与 `daily_report.py` 的
`accepted_kinds`（那是文档种类）。没有一个在说「这个结果被收下了」。

#### 9.41.3 记在哪一级：边上，不写进回执，也不新增 store

1. **边上。** 设计说的是「每条**异步边**…是否已被接受」，而这条边在本机就是 admission：
   它是唯一同时持有 cycle_ref / grant_ref / reservation / lease / action_key / retired_at /
   outcome 的记录，也是 `waiting_on.refs`、`child_attempts` 里每一个 ref 指向的东西。
2. **不写进回执。** `runtime/bounded_invocation` 的回执按 action_key 一次性写入，它的
   docstring 写着同一 key 的第二次调用「REPLAYS its receipt instead of executing again」；
   接受不是执行的一部分，而让 Parent 去写另一个 store 的命名空间，就是给接受开出第二条
   写入路径 —— 正是这个仓反复找到的那种形状。
3. **不新增 store。** `docs/context-capsule.md:99-102` 明说 checkpoint「not a transcript
   summary or **a new database**」，而它已经承载 Item 级的写法：`evidence_locators`，它的
   help 逐字就是「an accepted evidence locator」。所以两级各记一件事，**不重复**：

   | 级 | 记什么 | 谁是权威 |
   |---|---|---|
   | 边（admission） | 这件事**发生过一次**，谁收的、收的是哪个 locator、什么时候 | `agent_control_store` |
   | 记录（checkpoint） | Item 现在**是什么状态**：哪些证据已被接受、哪些结果还欠着 | `continuation` + 写它的 Parent |

   这也解释了为什么 `evidence_locators` 是**累积**的、而边上的事实只能是**一次**的：
   `evidence_locators` 是每个 epoch 对 Item 状态的重述（locator 按设计会重复出现，
   §9.39 已量过），而「收下了」是关于一次动作的陈述，动作不能重述。

#### 9.41.4 本片落的四笔

**一、`runtime/agent_control_store.py#accept_admission`**（新动词，插在 `retire_admission`
之后、`replace` 之前）。它是这个事实的唯一写者，形状照 `retire_admission`：

- 两个**参数**守卫：`who` 与 `locator` 都必须具名。理由逐字接
  `docs/COLLABORATION-CONTRACT.md:52` 的 `accepted_by` 定义（「接收者及其验证结果，所接受的
  确切输入输出版本」）—— 一个无法归属到某个 Parent 的接受不是那条记录；一个不说收了哪个
  结果的接受，正是恢复中的 Parent 来找的那件事实而它没给。
- 两个**状态**守卫，都关于「什么能存在」：必须已有 `retired_at`（否则没有结果可收），
  必须到过 `ADMISSION_PHASES[-1]`（`DISPATCHED`，否则这次调用没被报告过回执）。
- **写一次**：第一次调用把 `who` 与 `where` 两件事一起写；后来的调用两件都对 = 重试
  （幂等，即使 `at` 不同 —— 它是同一个陈述）；`by` 或 `locator` 任一不同 = **具名拒绝并
  同时印出记录里的值**，因为「在这里挑新的那个」会把分歧藏起来而不是记下来（房规原文，
  `retire_admission` 的第二次 `outcome` 用的是同一句）。
- 它**不判定** verdict。

**二、`admit` 的存储字典新增三个声明键**（`accepted_at` / `accepted_by` /
`accepted_locator`，初值 None）。声明而不是缺席，理由与 `retired_at` 当初被声明一样：
`attempts_state` 要读这三个键，而在 `accept_admission` 存在之前写下的记录**根本没有这个
键**，那样「键不存在」与「这个 store 答了 None」在读的人那里没有区别。

**三、`runtime/continuation.py`**：`ATTEMPT_ANSWERS` 四词变五词（`ATTEMPT_ACCEPTED` 在最前，
并在常量注释里写明**元组顺序不是记录级答案的判定顺序**）；`attempts_state` 逐 ref 读那三个
键、新增 `accepted` 子集列表、计数改成「N finished (M of them taken up), …」，记录级判定
顺序为 `UNANSWERABLE > OPEN > 有未接受的 retired > 全 accepted`；必要时**原地改写**那段
「ACCEPTED IS NOT A STATE THIS FUNCTION HAS, AND THAT IS NOT AN OMISSION」。

**四、`runtime/cli.py`**：新动词 `work accept <cycle> --by --locator [--at]`（照
`cmd_work_retire` 的形状）；`cmd_work_continuations` 的 docstring 加两段（「这条边现在能回答
ACCEPTED」与「同一个屏幕上的两个 accepted 是两级，不是两份」）、顶层 `attempts` 计数行多一列
`all taken up`、逐 attempt 行多一个 `ATTEMPT_ACCEPTED` 分支并在 `ATTEMPT_RETIRED` 行尾加
「no take-up of its result is recorded」；`cmd_work_admissions` 每个 cycle 多一行
`taken up`。

第 2 级与第 4 级都有消费者，而且是**两个不同的人**：恢复 Item 的 Parent 读
`work continuations`，审计一个 cycle 占了什么、谁收了它的结果的人读 `work admissions`。
同一个字段，两个受众，互不为对方的摘要。

#### 9.41.5 一次当场纠错：拒绝文案里那条「补救」是假的

`accept_admission` 的第二个状态守卫（`phase != DISPATCHED`）初版的拒绝里写着：

> If the invocation did happen, `confirm_admission` is idempotent: reporting the receipt
> it left and then accepting is how a resumed cycle catches up

这是我从「`confirm_admission` 幂等」顺手推出来的一句话，**没有走一遍**。测试
`test_the_remedy_the_refusal_names_is_the_one_that_works` 一走就撞上 `confirm_admission`
自己的守卫：「was retired at … A retired admission is what the cycle did, and a confirmation
arriving after that would rewrite the ending of a cycle already accounted for」。

所以**补救不存在**，而且这是设计上正确的：§9.40 刚立的「一次退休只写一次」同样封住了事后
补报。真实的形状是：一个 retired 在 `DISPATCHED` 之前的 cycle 被记为**被放弃**，这个 store
不会事后把它拆开让一个结果出现；如果那次调用真的发生过，那份 ending 是错的，需要人裁定
两份记录里哪份是错的。

**一条拒绝文案里写的补救，与任何别的句子一样，是要被走一遍的** —— 它走了这一步才暴露。
测试因此拆成两条：一条断言拒绝说明这是人的判断，另一条断言 `confirm_admission` 在 retired
之后**确实**拒绝 —— 让「没有补救」成为关于 store 的事实，而不是一句声称。

#### 9.41.6 被有意识移动的既有断言（四处）

| 断言 | 原文 | 现在 | 为什么移动 / 为什么不移动 |
|---|---|---|---|
| `test_the_four_answers_are_the_closed_vocabulary`（`tests/test_continuation.py:1213`） | 四个词，等号断言 | `test_the_five_answers_are_the_closed_vocabulary`，五个词 | K 的 docstring 自己写着「Asserted as an equality, so a fifth word is a change and not a typo」，本片就是那个 change |
| `test_an_acceptance_is_not_a_state_it_can_answer`（`:1261`） | `assert "ACCEPTED" not in continuation.ATTEMPT_ANSWERS` ＋ evidence 的 7 个键 | `test_an_acceptance_is_a_state_the_edge_records`，旧断言**逐字留在 docstring 里** | K 的代码 docstring 明说它是在为「a later slice adding an acceptance store」留位置；本片是那个 slice |
| 模块 docstring 的 `attempts_state DOES NOT ANSWER «ACCEPTED»` 段（`:85-91`） | 「the absence is deliberate rather than unfinished」 | 「NOW ANSWERS … AND IT DID NOT WHEN THIS FILE WAS WRITTEN」，并列出移了什么、没移什么 | 同一句留下的交接 |
| `test_the_reader_prints_what_was_tried_and_what_became_of_it`（`:1744`） | `assert "1 all finished" in after.stdout` | `assert "1 all finished with a take-up outstanding" in after.stdout` | 旧串仍是新串的子串（所以它并没有失败），改是因为**子串匹配会让计数那句话的新部分无人断言** |

**没有移动的是那条区分**：接受从 **store** 读出，绝不从记录自己的 `evidence_locators`、也
绝不从自由文本 `outcome` 推出。三处钉住它：上面第 2 行那个测试（同一记录，`store.accept_admission`
前后读到两个答案，而记录一字未动）、本片新增的
`test_a_free_text_outcome_is_not_an_acceptance`（本文件 14 处以上 `outcome="accepted"` 的
fixture 因此从「会误导」变成「有用的反例」）、以及 CLI 侧那条
（`--json` 与屏幕都显示记录自己的 `accepted` 行始终为空）。

#### 9.41.7 本片新增的用例

`tests/test_continuation.py`：净 +3 条 `def test_`（101 → 104），先加一个共用 helper
`a_dispatched(store, ref)`。三条是「同一条记录读三次、只有 store 动」、
「自由文本 outcome 不是接受」、「一条未接受混在已接受里 → 记录级 RETIRED 且 `accepted ⊆
retired`」；CLI 侧加一条把 `work accept` 之后的重读钉在屏幕文本上。

`tests/test_admission.py`：净 +18 条（99 → 117），两个类。`TestATakeUpIsWrittenOnceOnTheEdge`
（11 条，store 层：成功、仍开着时拒绝、未到 DISPATCHED 时拒绝、**补报回执同样被拒**、同参重试
幂等、不同 by 具名拒绝、不同 locator 具名拒绝、未 admit 的 cycle、空 by、空 locator、
三个键在 admit 时就已声明）。`TestTheAcceptVerb`（7 条，CLI 层：五行输出、同参重试、
不同 by 是 EXIT_FAULT 且两个名字都印出、未到 DISPATCHED 的句子而非 traceback、store 里没有的
cycle、两个参数在命令行上必填、`--json` 往返）。

**两片合计净 +21 条**（18 ＋ 3），所以本片全量的预期是 `5870 + 21 = 5891`；subtests 那一格
本片没有一条用例用 subtest，预期仍是 3993。

#### 9.41.8 变异电池：15 条，15 杀，0 存活

每轮改一行 → 删掉仓里所有 `__pycache__` → 跑 `tests/test_continuation.py` ＋
`tests/test_admission.py` → 从字节备份还原 → `sha256` 核对。删缓存那一步不是装饰：同尺寸
同秒的还原会让 Python 继续供给过期 `.pyc`，一条活着的断言会读成死的
（`pyc-cache-defeats-same-size-mutation-reverts`）。

| 变异 | 结果 |
|---|---|
| accepted 永远不被识别 | 3 failed, 253 passed |
| `accepted` 列表永远不填 | 3 failed, 253 passed |
| 记录级答案不再谨慎（恒 ACCEPTED） | 8 failed, 248 passed |
| 计数句不说谁被收下了 | 2 failed, 254 passed |
| evidence 里把 locator 当成 Parent 印出 | 2 failed, 254 passed |
| 仍开着的 cycle 也能被接收 | 1 failed, 255 passed |
| 没到 DISPATCHED 的 cycle 也能被接收 | 2 failed, 254 passed |
| 后来的接收被静默吸收（第一次赢） | 3 failed, 253 passed |
| 同一个 Parent 换了 locator 读作重试 | 3 failed, 253 passed |
| 无名的 Parent 也能接收 | 1 failed, 255 passed |
| 无名的 locator 也能接收 | 1 failed, 255 passed |
| `admit` 少声明一个键 | 2 failed, 254 passed |
| 逐 attempt 的 accepted 行不印 | 1 failed, 255 passed |
| locator 用 Parent 的名字写入 | 2 failed, 254 passed |
| 计数从错的答案上读 | 1 failed, 255 passed |

跑完五个文件与备份逐字节相同（脚本自带最后一道 `sha256` 断言，非零退出会写 `SURVIVED`）。

**第一次跑有两条没有按预期进行，两条都记下来**：

1. 「计数句不说谁被收下了」**第一次存活**。它改的是 `says` 里那句
   `N finished (M of them taken up)`，而当时**没有一条用例断言这个括号** —— CLI 断言的
   `1 all taken up` 是顶层计数行，不是这句话。补了三条断言（`(none of them taken up)` /
   `(1 of them taken up)` 两处）之后它被杀。这正是变异电池的用处：**一条我以为是断言的东西
   其实无人读**。
2. 「后来的接收被静默吸收」第一次**锚点未命中**（脚本按 12 空格缩进找，实际是 16/24/20），
   脚本显式 `SKIP` 并以非零码退出而不是继续 —— 锚点对不上是脚本的缺陷，不该当成变异结果。

#### 9.41.9 全量读数

一次跑，捕获方式与切片 K 的第四遍相同（原始日志落盘 ＋ 只 `grep` 摘要行），因为 K 已经量过：
解释器收尾期那族 `ResourceWarning: unclosed file` 打在 pytest 摘要行**之后**，`tail` 窗口会
把计数挤掉。

```text
HEAD 1fc99b58534703aa24615b8806ecfad2df4614ba  dirty 5
start 2026-09-26T07:40:21+08:00
rc=0 at 2026-09-26T07:50:58+08:00
5891 passed, 20 skipped, 3993 subtests passed in 633.26s (0:10:33)
dirty after 5  HEAD 1fc99b5
runtime/agent_control_store.py: OK   runtime/continuation.py: OK   runtime/cli.py: OK
tests/test_admission.py: OK          tests/test_continuation.py: OK
```

- **算术闭合**：上一格 5870 ＋ 本片净新增的 **21** 条 `def test_`（`test_admission.py`
  99 → 117，`test_continuation.py` 101 → 104，删 0 条）＝ **5891**。
- **subtests** 3993 → 3993：本片没有一条用例用 subtest，与上一格相同。
- **`dirty 5` 而不是 0**：K 那一格记的是 0，因为那时本片的前身已经提交；本片的五个文件还在
  工作树里未提交，所以跑前跑后都是 5 —— 这既是读数的一部分，也是它必须写清楚的地方：
  跑前 5、跑后 5、HEAD 未变，说明这一跑**没有额外写进任何东西**。
- 五个被改文件的 `sha256` 在跑前记下、跑后 `sha256sum -c` 逐行 `OK`：这一跑读到的就是
  将要提交的那份字节。
- 那族收尾期 `ResourceWarning` 本片**没有再追**，K 的 §9.40.7 已经把它记在案上；本片没有
  新增关于它的证据，也不打算用一次没做的追查冒充结论。

#### 9.41.10 它没做什么，仍未关闭的

- **不判定 verdict。** 它写的是「这件事发生过一次，谁收的，收的是哪个 locator」，
  `docs/COLLABORATION-CONTRACT.md:55` 的后半 ——「parent 根据真实结果接受/要求具体修订/登记
  准确缺口」—— 落在 Parent **下一次** checkpoint 的 `evidence_locators`/`remaining_results`
  上，本片不代它做，也不从 `outcome` 猜。
- **不校验两级一致。** 记录是否已经重述这个 locator，是那个 Parent 下次写记录时的事；
  两者合法地相差一次写入，把它们的不一致叫成 disagreement 就是在发明设计没写的规则。
- **不做唤醒那一半。** 「让后继 Parent 醒过来」是切片 I/J 关掉的（`receivers()` 的答案 +
  `WAIT_OVER`）；本片只补上醒来之后要做的两件事里的第二件。
- **`WAIT_KINDS` 仍只有 `child` / `window`**，`work continuations` 里仍然没有「已
  完成但没人收」的专门等待种类（§9.40 已记）。
- **新动词 `work accept` 在装好的节点上要等下一次 `build→promote→restart`**，与切片
  F–K 的新模块一样，那一次是 P12 的；本片没有动 `plugin/`、`mcp/` 或任何 composition，
  所以本片自己不触发它。
- 仍然开着的那些与前几片相同（接收方/唤醒那一半的前置、`supersede_checkpoint` 仍无生产者、
  `NodeControlTransport.send` 不包裹 connector 异常、`outcome` 仍是自由文本、
  `max_attempts` 在准入预留上无消费者、`~/.claude/skills/document-stewardship/{scripts,references}`
  不在仓库、`serveros-daily.timer` 已禁用、`SERVEROS_ALERT_CONVERSATION` 无落点……），
  本片一条也没有改变它们的状态。

### 9.42 P7 切片 M：「Parent 替换后原 child 自动接回」

#### 9.42.1 靶子：P7 验收行里最后一条仍是散文的判据

§9.31.8 逐字记着 P7 那一行还剩三条只是散文：「Parent 替换后原 child 自动接回、一项等待不冻
其他合法 scope、`parent_session` / `handoff.completed` 没有生产者」。第二条已由切片 D（§9.32）
关闭，第三条属唤醒那一半（§9.38.9），所以**本片打的是第一条**。它不是一句新需求：它是这一行
自己的验收判据，而判据在这次之前只以散文形态存在。

#### 9.42.2 读数：一条正在跑的 child，与一条说自己没有 child 的记录

`probe-m-before.py`（隔离 store，不碰活数据）先把这个缺口读成一条可复现的事实：

```text
Item SRV-0001   epoch 1  parent-a  child_attempts=["cyc-1"]  superseded_by=SRV-0001#2
                epoch 2  parent-b  （一个 attempt 都不具名）   live
cyc-1           phase DISPATCHED   retired_at None   → 仍在跑
live_checkpoints()          ['SRV-0001#2']
恢复中的 Parent 读到的那条     ATTEMPT_NONE
predecessor_open            ['SRV-0001#1']   ← 只有知道要顺着外键走过去的读者才捞得回来
```

**child 没有丢。** store 里它还在，按 cycle 键着，`unresolved_effects` 也还在。丢的是**读数**：
Item 现在只从 epoch 2 那条记录被读，而那条记录名字里没有任何 child，于是「这个 Item 欠什么」
在每一个按 live 记录取的计数里都答「什么都不欠」—— 一份还在跑的 child 底下。「原 child 自动
接回」之所以只能写成散文，正是因为没有一处读数跨得过 epoch。

#### 9.42.3 单位是 Item，且链不顺着指针走

两句话都是 store 自己说的，本片只是把它们读成定义：

- **单位是 Item：**「The scope is the Item; the epoch is only how it changes hands.」一个在
  epoch 1 下派出的 child 是 epoch 2 下**这个 Item 的**义务，新的 owner 没有被交给它，也不能
  因此不算数。
- **顺序来自 Item 的 epoch，不来自 `superseded_by`：** 指针是「谁接管了」的记录，不是「曾经
  是什么」的答案。**而 `runtime/agent_control_store.supersede_checkpoint` 的守卫只要求被取代
  的 ref 存在 —— 它不要求后继者是本 store、本 Item 的 checkpoint。** 顺着指针走的链会因此继承
  指针失败的全部方式，包括走出它出发的那个 Item。那个缺口本片**量到了、没有改**（见 §9.42.10），
  改为让成功链**只依赖 store 自己强制的那件事**：同 Item 的 epoch 次序。

于是三个答案，而不是一个布尔：

| 前驱 epoch 里的 attempt | 新 owner 欠吗 | 为什么 |
|---|---|---|
| `ATTEMPT_OPEN` | 欠（carried） | store 持有它，没有任何 outcome，有人在付钱 |
| `ATTEMPT_RETIRED` | 欠（carried） | outcome 有了、没人收结果；读那份结果才是新 owner 欠的 |
| `ATTEMPT_ACCEPTED` | 不欠（进 `closed`） | `work accept` 只写一次，Item 已经有那份结果了 |
| `ATTEMPT_UNANSWERABLE` | 不欠、也**不丢** | 记录级答案升为 `ATTEMPTS_CARRIED_UNANSWERABLE`：「我读不到的一个 epoch 可能留下了东西」不是「什么都不欠」 |

**两个读数互不重叠**，这是它可用的前提：记录**自己具名**的 attempt 绝不再计入 carried（那个
Parent 已经手抄了一份，`attempts_state` 为它作答），所以屏幕顶层的 `attempts` 行与 `carried over`
行的计数**相加不会算两遍**。这条性质有专门的用例钉住（`test_a_successor_that_names_the_attempt_itself_does_not_carry_it`）。

#### 9.42.4 本片落的四笔

**一、`runtime/continuation.py` 新增 `predecessors(record, checkpoints)`**：按 Item 的 epoch 次序
给出「这个 Item 从哪些 epoch 走过来」，从最早的开始。三条排除各有理由，并且各自有用例：别的 Item
的记录绝不是前驱；**store 仍算 live 的更低 epoch 不是前驱**（那意味着一个 Item 两个 owner，是
`put_checkpoint` 拒绝、更高 epoch 的写入在同一事务里解掉的形状 —— 盘上真有也只是「一次没人做过的
接管」，读数不得发明它）；`epoch` 不是 epoch 的记录不是本 store 写下的，也就不算一次交接。
不可读的入参答空表而不抛。

**二、`runtime/continuation.py` 新增 `carried_attempts(record, *, checkpoints, admissions)`**：
返回 `{checkpoint_ref, work_item, epoch, predecessors, evidence, carried, closed, unreadable,
state, detail, says}`。`evidence` 每条是 `attempts_state` 的条目**加上** `named_by` /
`named_by_epoch` / `named_by_parent` —— 即「这是哪个 epoch、由谁派出、为什么它现在算这个 Item
的」。记录级判定顺序 `UNANSWERABLE > CARRIED > NOTHING`，与 `attempts_state` 同源；三条 `says`
都做单复数处理。新增闭集词汇 `CARRIED_ANSWERS`（三词，等号断言）。

**三、`runtime/continuation.py` 的 `build_checkpoint` 抽出一个 `_is_epoch`**（正整数且不是 bool），
内联判据换成它 —— 语义逐字相同，本片不借重构改判定。同时把模块 docstring 补一段
「AND A READING OF ONE RECORD IS NOT A READING OF THE ITEM」，写明
`attempts_state` 答的是一个**记录**的问题、`carried_attempts` 答的是一个 **Item** 的问题。

**四、`runtime/cli.py` 的两个动词**：

- `work checkpoint` 在**写入的那一刻**说出这次写入接管了什么（这是唯一一个说这句话不花钱的
  时刻：更高 epoch 的写入与取代在同一个事务里发生）。屏幕多三段：`carried over  <state>`、
  `  <says>`、逐 attempt 的 `    <ref>  <state>  named by <who> (epoch N, <parent>)`；`--json`
  多一个 `carried`。
- `work continuations` 顶层多一条计数行
  `carried over  N attempt(s) left by an earlier epoch and no take-up closes — <refs>`，并在
  **拥有该 Item 的那条记录**下面印 `carried over` 块 —— 不印在被取代的那条下面：义务是 Item 的，
  印在哪一条就是「现在谁欠它」的陈述。被取代的那条自己那块仍然具名同一个 child，两个读数同时在
  屏幕上，互不为对方的摘要。
- 顺带：`live` 改为**调用 store 自己的 `store.live_checkpoints()`**。这个函数在此之前**零调用者**，
  `runtime/cli.py` 曾自行重写了同一条过滤规则（`live_checkpoints()` 在本片拿到它在本仓的第一个
  调用者）；注释写明「one continuation owner per scope」是 store 自己要说的。

#### 9.42.5 屏幕读数（三个瞬间）

`probe-m-screen.sh`，隔离 store，逐屏核对。关键的是**同一块里两行并列**：

```text
##########################################################################
# 替换之后：恢复中的 Parent 读到的
SRV-0001#2  AWAITING_CONTINUATION  wait WAIT_OPEN  attempts ATTEMPT_NONE
  attempts      — this record names no child attempt
  carried over  ATTEMPTS_CARRIED  from SRV-0001#1
    this Item came through 1 earlier epoch and still owes 1 attempt no take-up
    closes (cyc-1), so resuming here means reading it and not dispatching a
    second child for the same work
    cyc-1  ATTEMPT_OPEN  named by SRV-0001#1 (epoch 1, parent-a)
```

顶层同时出现两行，且相加不重复：

```text
attempts      0 recorded on 1 live checkpoint — 0 all taken up, 0 all finished with
              a take-up outstanding, 0 with one still open, 0 this store cannot look
              up, 1 naming none
carried over  1 attempt left by an earlier epoch and no take-up closes — cyc-1
```

**结果被收走之后，同一块变成 `NOTHING_CARRIED`，而证据行仍在**（`cyc-1  ATTEMPT_ACCEPTED
named by SRV-0001#1`）—— 「不再欠」与「从来没听说过」在屏幕上必须长得不一样，这是本片读数的
第三个答案存在的全部理由。

#### 9.42.6 被有意识移动的既有断言（三处）

| 断言 | 原文 | 现在 | 为什么移动 |
|---|---|---|---|
| `tests/test_continuation.py` 模块 docstring（`:14`） | 「Five properties」 | 「Seven properties」＋新增第 7 条（`:71`）「THE RECORD IS NOT THE ITEM, AND THE ITEM IS WHAT OWES」 | 本片就是把那条性质立成第 7 条；旧计数留着会让 docstring 自己说错 |
| 同文件「A SEVENTH THING THAT IS ALSO ASSERTED」（`:83`） | 第七 | 「AN EIGHTH THING」 | 同上，序号顺移 |
| `build_checkpoint` 的 epoch 判据（`runtime/continuation.py:272`） | `if not isinstance(epoch, int) or isinstance(epoch, bool) or epoch < 1:` | `if not _is_epoch(epoch):` | 判据被 `predecessors` 也要用；**语义逐字保留**，本片不借这次抽取改任何判定（F5 那条变异仍然成立） |

#### 9.42.7 本片新增的用例

`tests/test_continuation.py`：净 **+19** 条 `def test_`（104 → 123），两处：

- 新类 `TestTheItemOwesWhatItsEarlierEpochsLeft`（**14** 条，读数层）：闭集词汇等号断言；首个
  epoch 什么都不接；**替换后的 Parent 接住前驱的 open child**（同时断言该记录自己的
  `attempts_state` 仍是 `ATTEMPT_NONE`，并断言 provenance 三键）；retired 未被接受也算欠；
  已接受不算欠（进 `closed`）；后继自己具名就不算 carried（互不重叠）；`cyc-gone` 让记录级答案
  升为 UNANSWERABLE 且**仍列出能读到的 `cyc-1`**；`admissions=None` 是「nobody brought the
  store」；链是 epoch 序而不是指针；**一个 Item 换过两次手就欠两个 epoch**（顺序断言）；
  别的 Item 的记录绝不是前驱；store 仍算 live 的低 epoch 不是前驱；**epoch 不是 epoch 的记录
  不是一次交接**；非 dict 入参答空而不抛（含 `checkpoints=None` 与「没有 Item」两种）。
- `TestTheVerbsDriveTheStore` 末尾 **+5** 条（CLI 层）：写入动词说出它接管了什么、读数的
  `carried over` 块印在拥有它的那条记录下面（含顶层计数行）、没有前驱的 Parent 什么都不印
  （含 `0 attempts … nothing came through with anything open`）、后继自己具名就什么都不印、
  `--json` 带上这份读数且**被取代的那条不在 `carried` 映射里**。

新增 helper `a_second_parent(store, **over)`（epoch 2，**不具名任何 attempt** —— 这正是本片要的
形状，不是 fixture 的疏漏）。`tests/test_admission.py` 未动（117 → 117）。预期全量
`5891 + 19 = 5910`。

#### 9.42.8 变异电池：17 条，17 杀，0 存活

每轮改一行 → `rm -rf` 仓里所有 `__pycache__` → 跑 `tests/test_continuation.py` → 从字节备份
`cp` 还原并 `sha256` **逐字节核对**（`/data/home/vince/artifacts/tmp-p7/mutate_slice_m.py`）。
锚点在文件里必须恰好出现一次，命中不到就显式 `SKIP` 并让电池非零退出 —— 不把锚错读成一条活着的
判据。**首轮 15 条：13 杀、2 存活、1 SKIP。三条都当场处理，一条也没有当成「通过了」**：

- **存活 1（别的 Item 的记录变成前驱）**：原有那条 `test_a_record_of_another_item_is_never_a_predecessor`
  的 fixture 把另一个 Item 的记录也写成 epoch 1，于是 `other["epoch"] < epoch` 这一句**替我挡住了
  那个突变** —— 用例在 Item 过滤被删掉时照样通过。这是**测试自己的假绿**：判据没有被钉住，只是
  被另一个条件遮住了。修法是把另一个 Item 的 epoch 提到 2，让 Item 成为唯一有效的过滤，并把这段
  理由写进 docstring。
- **存活 2（epoch 不是 epoch 的记录不跳过）**：那条守卫**此前没有任何用例**。新增
  `test_an_epoch_the_store_did_not_write_is_not_a_handover`：`build_checkpoint` 写的是整数
  epoch，所以一条形状不对的 epoch 不是本 store 写下的 —— 把它读成一个 epoch 就是从**一个没有任何
  东西校验过的字段**里发明一次交接。它同时补上了 `predecessors` 的 `checkpoints=None` 与
  「没有 Item」两个入参分支。
- **SKIP（缺 admissions 那一支）**：我的锚点 `if not isinstance(admissions, dict):` 在
  `runtime/continuation.py` 里出现 **3 次**（`attempts_state`、`wait_state`、`carried_attempts`
  各一）。按电池自己的规矩，这不是「死了」，是**电池坏了**：换成两行锚点后重跑，杀掉。

补完三条后**重跑整盘 17 条：17 杀、0 存活、0 SKIP**，还原后三个文件回到 `sha256` 逐字节相同。

#### 9.42.9 全量读数

一次跑，捕获方式与 K/L 相同（原始日志落盘 ＋ 只 `grep` 摘要行：收尾期那族
`ResourceWarning: unclosed file` 打在摘要行之后，`tail` 会把计数挤掉）。

```text
HEAD 1341a6ad2af670a6eadae4703d748bad8fb9caf1  dirty 3
start 2026-09-26T08:14:22+08:00
rc=0 at 2026-09-26T08:25:04+08:00
5910 passed, 20 skipped, 3993 subtests passed in 641.67s (0:10:41)
dirty after 3  HEAD 1341a6a
runtime/continuation.py: OK   runtime/cli.py: OK   tests/test_continuation.py: OK
```

- **算术闭合**：上一格 5891 ＋ 本片净新增的 **19** 条 `def test_`（`test_continuation.py`
  104 → 123，删 0 条）＝ **5910**。
- **subtests** 3993 → 3993：本片没有一条用例用 subtest，与前两格相同。
- **`dirty 3` 而不是 0**：本片的三个文件还在工作树里未提交，跑前跑后都是 3、HEAD 未变 ——
  这一跑**没有额外写进任何东西**。
- 三个文件跑前记下 `sha256`、跑后 `sha256sum` 逐行相同：这一跑读到的就是将要提交的那份字节。

#### 9.42.10 它没做什么，仍未关闭的

- **不唤醒、不续跑。** 本片全部在「醒来之后要做什么」这一侧。`receivers()` 仍然答
  `RECEIVERS: tuple = ()`：**这个节点上没有任何东西会因为一份保存好的 checkpoint 而醒过来**，
  屏幕上的 `receivers  none installed — a saved checkpoint is not being woken by anything on
  this node` 就是这句话。§9.38.9 记下的两件前置（一条真的跑这个检查、并且是 trigger owner 的
  routine ＋ 一条真的被记录下来的 grant）**本片仍然不具备**，而那是「工作循环的第一次真实委派」
  那条摆在人面前等决定的线，本片不替人做那个决定，也不伪造一条 grant。
- **不决定一个继承来的 attempt 该不该继续。** 放弃它、取代它、继续它是三个不同的动作，三个都是
  Parent 的。本片只说「它还在那儿、还欠着、是谁派出的」。
- **不检查继承来的 retired attempt 的结果还能不能收。** 那是
  `accept_admission` 自己的问题（§9.41 已立）。本片不代它答。
- **不新增 store、不新增文件、不新增动词。** 只新增两个读函数与两处渲染；`work checkpoint` /
  `work continuations` 的输出面变大，语义不变。
- **`supersede_checkpoint` 那个守卫缺口仍然开着、且仍然没有生产调用者**：它只要求被取代的 ref
  存在，不要求后继者是本 store、本 Item 的 checkpoint，也没有 CLI 动词，只有测试调它。本片
  **量到它、用它论证了「链不顺着指针走」、没有改它** —— 改它要移动 §9.40 刚钉住的 F12/F13 两条
  用例，而那是「确保唯一 logical Parent」那条相邻的环，不是本片这一格。本片改为让成功链不依赖
  指针，并用一条测试把这个决定钉住（把 `superseded_by` 改成不存在的 `SRV-0001#99`，仍然接得住）。
  > **切片 N 更正（这是本片自己的一个判断错了，不是后来者改的口）。** 上面「改它要移动 F12/F13」
  > 是**我在没有量之前写下的推断，而它是错的**：切片 N 加了那三条守卫，F12/F13 两条断言
  > （`test_it_is_idempotent_on_the_first_instant`、`test_it_names_a_successor_or_it_is_refused`）
  > **一条也没有动**，动的只是另外两条用例的 fixture（它们原本点名一个 store 里根本不存在的
  > 后继者）。同样被证伪的还有「本片这一格」这个划分：那三条守卫就是 P7 那一行自己写的
  > 「唯一逻辑 Parent」，并且量出来的形状比记在这里的更糟（见 §9.43.2）。
- **`WAIT_KINDS` 仍只有 `child` / `window`**；接收方/唤醒那一半的前置、`NodeControlTransport.send`
  不包裹 connector 异常、`SERVEROS_ALERT_CONVERSATION` 无落点、`max_attempts` 在准入预留上无
  消费者、`~/.claude/skills/document-stewardship/{scripts,references}` 不在仓库、
  `serveros-daily.timer` 已禁用…… 与前几格相同，本片一条也没有改变它们的状态。
- **切片 F–M 的新模块在装好的节点上仍然没有 `checkpoint` / `continuations` / `accept` 这些动词**
  （P12 的 `build→promote→restart` 那一次）；本片没有动 `plugin/`、`mcp/` 或任何 composition，
  所以本片自己不触发它。

### 9.43 P7 切片 N：唯一逻辑 Parent —— 写这个字段的有两个写者，只有一个在守规则

#### 9.43.1 靶子：P7 那一行自己写的「唯一逻辑 Parent」

P7 的验收链里有一格是**唯一逻辑 Parent**。`runtime/agent_control_store.py` 里写
`superseded_by` 这个字段的有**两个写者**：`put_checkpoint`（同一事务里，更高 epoch 取代更低
的 live 记录）与 `supersede_checkpoint`（独立动词）。第一个写者把三条条件都强制了 —— 同 Item、
更高 epoch、取代发生在同一事务里；**第二个写者只强制了一条**：被取代的 ref 存在。切片 M 是
撞上这一点的（`predecessors` 的 docstring 因此选择按 epoch 排序而不是顺着指针走），而
§9.42.10 把它记成「相邻的环、本片不改」。

#### 9.43.2 读数：三种形状它今天全都接受，而第三种最难看

`probe-n-before.py`，同一支探针跑在两棵树上（HEAD 的 `runtime/` 与工作树），逐条对照：

```text
                                        BEFORE                  AFTER
1. by=<本 store 不持有的 ref>            ACCEPTED                REFUSED
   superseded_by = SRV-0001#99           live = []               live = ['SRV-0001#1']
2. by=<另一个 Item 的 checkpoint>         ACCEPTED                REFUSED
   superseded_by = SRV-0002#1            SRV-0001 由 SRV-0002 的记录承载
3. by=<更早的 epoch>                     ACCEPTED                REFUSED
   #1 ←→ #2 互为后继                      live = []               live = ['SRV-0001#2']
```

- 第一种把「没人为这个 Item 负责」写成了「有，名字在这里」：记录说某个 Parent 承载着这个
  Item，而那个 Parent 在 **store 里任何地方都不存在** —— `live_checkpoints()` 会为这个名字作答，
  可恢复的东西一件也没有。
- 第二种让**一个 Item 由另一个 Item 的记录承载**。
- 第三种最难看，也是最不可辩的：`#1` 说 `#2` 取代了它、`#2` 说 `#1` 取代了它 —— **互为后继，
  零个 live owner**，而 `docs/COLLABORATION-CONTRACT.md` 的「同一个 scope 只有一个父继续
  owner」连一瞬间都不允许。

#### 9.43.3 一条规则、四条检查，以及为什么它们排在幂等分支之后

规则就写在那个函数自己的 docstring 第一段里（「`by` 是**同一个 Item 的更高 epoch** 的
checkpoint」），本片只是把那句话变成机械的：**这个 store 持有它**、**同一个 Item**、
**更高 epoch**，再加一条自我继任（`by == target`：「取代了自己的记录什么都没接管」）。
四条按调用者会修的次序排，每条都附带「拒绝之后这个 Item 仍然有主人」。

**四条都排在幂等分支之后**，理由是 `put_checkpoint` 已经立过的同一条：一个被写下的交接的
重放，无论这个 scope 后来发生了什么，都还是重放；而在这四条检查存在之前写下的 store，必须
仍然能回答写下它的那次调用。**它们管的是「接下来可以写什么」，不是「盘上已经有什么」** ——
这个差别有一条用例专门钉住（一个后继者为 `SRV-0001#99` 的旧 store，重放仍然作答）。至于
「哪个 Parent 承载这个 Item」这种真正没法从已有记录读出的事，拒绝文案就在上一屏，仍然是
人的问题。

#### 9.43.4 一个必须记下的后果：这个动词的「首次写入」在可达状态里不可达

这是本片量出来的结论，不是本片造成的收窄。`probe-n-unreachable.py` 做的是一次**有界穷举**：
从空 store 出发，用 `put_checkpoint` 走 2 个 Item × 3 个 epoch 的所有写入序列（上限 4 次写），
把每个**去重后可达的状态**留下，再在这个状态里把**每一对有序 ref** 交给
`supersede_checkpoint` 在副本上跑一次，看文档有没有被改：

```text
                                  BEFORE(HEAD)         AFTER(工作树)
可达状态（≤4 次写，2 Item×3 epoch）      56                   56
supersede 调用改变了文档的状态数          50                    0
```

也就是说：加了这四条之前，**56 个可达状态里有 50 个**至少有一对 ref 能让这个动词真的写下去，
而打印出来的每一对都是上面三种形状之一（`('SRV-0001#2','SRV-0001#1')` 是更早的 epoch，
`('SRV-0001#1','SRV-0002#1')` 是别的 Item）。**一对待写的「合法」组合都不在里面**，因为
`put_checkpoint` 已经在唯一能发生的时刻把交接做完了：更高 epoch 的 checkpoint 一存在，更低
epoch 的 live 记录就已经被取代。所以这个动词今天实际是「**回放既有交接**」加五条拒绝，
`updated = dict(existing)` 那两行在有界穷举里没有一条路走到。**本片没有删它**（删是一个退役
决定，该走 §20 那张表），但把它记成候选，理由比 §9.42.10 写的更硬：它的写入路径不可达。

#### 9.43.5 本片落的四笔

1. `runtime/agent_control_store.py#supersede_checkpoint`：四条检查 ＋ docstring 扩写
   （「AND THE NAME HAS TO RESOLVE」＋「同一个字段有两个写者，它们必须就什么能写进去达成一致」
   ＋ 为什么这四条排在幂等分支之后）。
2. `runtime/continuation.py#predecessors` 的 docstring：**它引用的事实变了，所以那一段改写**。
   原文说「那个动词写的后继者不必是本 store、本 Item 的 checkpoint，所以顺着指针的链会走出
   它出发的那个 Item」。现在两个写者都强制了同样三条；这一段改成：**盘上的一个 store 是一个
   文件、不是写出它的那些规则的证明**，而这些记录在守卫之前就存在了、`supersede_checkpoint`
   的幂等分支至今仍然**回放**它们而不是拒绝自己的历史 —— 所以「不顺着指针走」这个决定仍然
   成立，只是理由从「那个动词允许」换成了「文件不作证」。
3. `tests/test_continuation.py` 同一条测试的 docstring 同一处改写（同一个句子在两处）。
4. `docs/CC-EXECUTION-LEDGER.md` §9.42.10 加一条**自己的更正**：见 §9.43.8。

#### 9.43.6 被有意识移动的既有断言（两处 fixture，零处断言）

| 用例 | 原文 | 现在 | 为什么移动 |
|---|---|---|---|
| `test_it_is_idempotent_on_the_first_instant` | `by="SRV-0001#2"`，而 `#2` 从未被写进 store；断言第一次调用写下的 `superseded_at == LATER` | fixture 先 `put_checkpoint(a_second_parent)`，断言改成「两次调用都没有把那个时刻挪走」 | 「第一次」现在总是 `put_checkpoint` 写的（§9.43.4），所以那个时刻不能再由这次调用产生；**被断言的性质没变** |
| `test_a_second_different_successor_is_refused` | 同上，`by="SRV-0001#3"` 也不存在 | fixture 先写 epoch 3，再要求它被拒 | 「两个**真实**后继者里哪一个赢」需要一个能被持有的第二者；否则这一条会先撞上「后继者不存在」那条拒绝而**看起来**通过了 |

**`test_it_names_a_successor_or_it_is_refused` 与 `test_a_ref_this_store_does_not_hold_is_refused`
一个字也没有动** —— §9.40 的 F12/F13 两条变异仍然成立（§9.42.10 那条更正说的就是这件事）。

#### 9.43.7 本片新增的用例

`tests/test_continuation.py`：净 **+6** 条 `def test_`（123 → 129），全部在
`TestSupercedingDirectly`：后继者本 store 不持有（含「拒绝之后这个 Item 仍然有主人」）；
后继者是另一个 Item 的 checkpoint（**那个 Item 的 epoch 更高**，否则 epoch 那一腿会替我挡住
这条判据 —— 切片 M 的变异电池刚在同一个坑里抓到我一次）；scope 不被交给更早的 epoch；
一条记录不能是自己的后继；一个 store 比较不了的 epoch 不是一次交接；以及**守卫之前写下的
交接仍然重放**（幂等分支排在这四条之前的证据）。`tests/test_admission.py` 未动（117 → 117）。
预期全量 `5910 + 6 = 5916`。

#### 9.43.8 变异电池：7 条，7 杀，0 存活

`mutate_slice_n.py`，同一套规矩（锚点必须唯一，否则显式 `SKIP` 并非零退出；每轮删
`__pycache__`；从字节备份 `cp` 还原并 `sha256` 逐字节核对）。七条：后继者不存在被接受、
别的 Item 的 checkpoint 被接受、记录可以是自己的后继、可以交给更早的 epoch、比较不了的 epoch
那一腿被删、**重放被当成新交接拒绝**（这条钉的是那四条排在哪一侧）、以及把后继者按 target 的
ref 去查（这条证明查的是**哪一个 ref**）。**7 杀、0 存活、0 SKIP**，还原后
`runtime/agent_control_store.py` 与备份逐字节相同。

一条脚注：删掉「比较不了的 epoch」那一腿时，`theirs <= mine` 会拿 `"2" <= 3` 抛
`TypeError`，所以那条被**崩掉**而不是被判定 —— 杀掉一个变异并不需要它是体面的失败，
但这一腿的作用因此可以说清楚：它把一个 `TypeError` 换成一条具名拒绝。

#### 9.43.9 全量读数

```text
HEAD 574735d  dirty 3
start 2026-09-26T08:32:24+08:00
rc=0 at 2026-09-26T08:43:32+08:00
5916 passed, 20 skipped, 3993 subtests passed in 668.25s (0:11:08)
dirty after 4  HEAD 574735d
runtime/agent_control_store.py: OK   runtime/continuation.py: OK
tests/test_continuation.py: OK
```

- **算术闭合**：上一格 5910 ＋ 本片净新增的 **6** 条 `def test_`（`test_continuation.py`
  123 → 129，删 0 条）＝ **5916**。subtests 3993 → 3993。
- **`dirty 3` → `dirty 4`，而多出来的那一个是台账自己。** 这一跑期间我改了
  `docs/CC-EXECUTION-LEDGER.md`（§9.42.10 的更正），所以跑后多一个 `M`。三个**被测试读到的**
  文件跑前跑后 `sha256` 逐行相同，HEAD 未变 —— 这一跑没有写进任何东西，多的那一个是在跑的过程
  里被我自己改的，不是它。
- 三棵树的读数都是同一支探针：`probe-n-before.py` 跑在 HEAD 的 `runtime/`（`git archive` 取出
  的一棵**未经改动**的树）与工作树上；`probe-n-unreachable.py` 同样跑两棵。用同一支量具量前后，
  而不是两次手写的读数。

#### 9.43.10 它没做什么，仍未关闭的

- **没有删 `supersede_checkpoint`。** 它的写入路径在有界穷举里不可达（§9.43.4），而删除是一个
  退役决定、要走 §20 那张表；本片把它记成**候选**，并把它仍然没有生产调用者这件事与「写入路径
  不可达」并排放着 —— 前者是事实，后者是它为什么可以是事实的解释。
- **没有改 `put_checkpoint`。** 它的三条条件本来就是对的；本片让第二个写者同意第一个。
  唯一被改的排序是**新检查**排在**既有幂等分支**之后，而不是既有分支本身。
- **没有给这个动词加 CLI 动词**，也没有让任何东西开始调它。
- **`WAIT_KINDS` 仍只有 `child` / `window`**；接收方/唤醒那一半的前置（§9.38.9 的两件）、
  `NodeControlTransport.send` 不包裹 connector 异常、`SERVEROS_ALERT_CONVERSATION` 无落点、
  `max_attempts` 在准入预留上无消费者、`~/.claude/skills/document-stewardship/{scripts,references}`
  不在仓库、`serveros-daily.timer` 已禁用，以及切片 F–N 的新模块在装好的节点上都还没有
  `build→promote→restart` 那一次（P12）—— 与前几格相同，本片一条也没有改变它们的状态。
- **一个仍然开着的问题，写在这里以免下一个人重新推**：既然 `supersede_checkpoint` 的写入路径
  不可达，`put_checkpoint` 在事务里做的取代会不会有**它自己**的可达性问题？本片的有界穷举顺带
  量了同一条边 —— 56 个可达状态里 `live_checkpoints()` 从没有同时给出两个 owner，也没有在
  Item 有记录时给出零个。那不是本片证明的定理（穷举是有界的），是 56 个状态上的量测。

### 9.44 P8 切片 A：播报日程 —— 一张卡要先知道「今天是什么样的日子」

#### 9.44.1 靶子：§9 与 §10 各写了一头，中间那一头没有落点

设计文档里 §9 说工作日历是**一个实例声明**（jurisdiction / timezone / source 定位符 + revision /
发布日期，官方调休覆盖 weekday 推导），§10 说常规播报窗口是**工作日 08:00–20:00 含两端**、
每工作日最多 13 次 routine update，§11 说**每工作日一张状态卡、不打补丁不补发**。三节都在，
而没有任何东西把「今天是什么样的日子」变成一个可被卡片读到的答案。实测（`git grep` 在
HEAD 的 `runtime/` 全目录）：`workday|工作日|调休` **零命中**，一个文件都没有。窗口这件事
在 `runtime/` 里当时只以两种形态存在：`runtime/recipe-contract.yaml:84` 的
`window: "08:00-09:00"`（配方触发窗口，与「每日状态播报」是两件事，措辞撞车而已）和若干
偶然的时间戳字面量。

#### 9.44.2 读数：三个问题、三种答案，而 UNKNOWN 必须是第三种

新模块 `runtime/business_calendar.py` 只回答三件事：**今天是什么类型的日子** / **现在在不
在窗口内** / **是 13 个时点里的第几个**。

| 轴 | 取值 |
|---|---|
| 日子 | `WORKDAY` / `NON_WORKING_DAY` / **`UNKNOWN`** |
| 日子的权威 | `declared-workday` / `declared-rest` / `weekday` / `weekday-fallback` |
| 窗口原因 | `IN_WINDOW` / `BEFORE_WINDOW` / `AFTER_WINDOW` / `NON_WORKING_DAY` / `CALENDAR_UNKNOWN` |
| 路由 | `ROUTINE_UPDATE` / `NO_ROUTINE_BROADCAST` |

- **UNKNOWN 不能并进「非工作日」**，这是本片最要紧的一条判决。把「没导入」读成「今天不播报」
  是拿缺数据当结论，而它恰好错在会**静默停播**的方向；`docs/MONITORING-ACCEPTANCE.md` 的
  V42 要求诊断里出现 `calendar=UNKNOWN`，且明确**不得**把周一至周五伪装成官方工作日。
- **窗口边界不落在代码的措辞里**：`WINDOW_FIRST_HOUR = 8`、`WINDOW_LAST_HOUR = 20` 与
  `ROUTINE_UPDATES_PER_WORKDAY = 20 - 8 + 1 = 13` 是同一条设计事实的两种写法，有一条用例
  同时钉住两端与 13，另有一条变异把任一端改成开区间。
- **13 这个数只在代码里出现一次**：它是从两个边界算出来的，不是第二处字面量。

#### 9.44.3 模块形态：不持有时钟、不建卡、不打补丁，无声明时不猜时区

- 日子由**声明**决定：`years.<year>.workdays` / `non_working_days` 覆盖 weekday 推导
  （官方调休就是这么表达的）；实例自己声明的 fallback 规则只有 `weekday` 一条，而**报告权威
  时说的是 `weekday-fallback` 而不是 `weekday`** —— 「这是官方日历说的」与「这是实例说按
  星期算的」必须在卡面上可分辨。有一条变异专钉这件事（A5）。
- **没被覆盖的年份不是「没有工作日」，也不是「照 weekday 算」**：是 UNKNOWN。三条出路各自
  对应第 9 节里的三种成因。
- `read_declaration` 返回的是一个 **READING**（`{state: ABSENT|INVALID|READ, declaration,
  path, reason, detail}`），不是值、也不是 `None`：§9 的 UNKNOWN 有两种成因，**修法不同**
  （没有导入 vs 导入了但不合规），一个 `None` 会把它们并成一件事。
- 模块**不打开卡、不发卡、不补发、不持有时钟**；时间由调用方给。**无声明时也不猜时区** ——
  节点连「今天是哪天」都不该说。这是刻意的：§9 把时区权威放在声明里，第二来源会是一个被
  渲染成事实的猜测。

#### 9.44.4 消费链：读 → 快照 → 模型 → 渲染器 → 命令

本片交付的是一个**链**，不是一座孤岛模块；五笔如下（每一笔都有变异钉住）：

| 位置 | 落点 |
|---|---|
| `runtime/card_source.py` | `load_calendar(path)`；`snapshot(..., calendar=)` 计算 `schedule`，非 READ 时**同时**产一条具名缺口（read 名 `business_calendar`，command 用 READING 自己的 path） |
| `runtime/card_source.py` | `DEFAULT_CALENDAR_PATH` 是 `business_calendar.DEFAULT_PATH` 的**别名而不是第二处字面量** —— 两个名字不能各自漂 |
| `runtime/presentation.py` | `status_model` 新增 `schedule`；**拿不到读数时降级成 `calendar=UNKNOWN` 的措辞**，不是空、也不是工作日 |
| `runtime/cards.py` | 状态卡新增 `**播报日程** …` 一行 |
| `runtime/cli.py` | `card status --business-calendar PATH` |

一条**纯整形器规则**在这里被写实：`snapshot` 不打开任何文件。收不到 reading 的调用方不会得到
schedule 块、也不会得到缺口 —— 这条规则不是洁癖，见 §9.44.6。

#### 9.44.5 手工跑过的五条真路径

同一支合成声明（`artifacts/tmp-p8/synthetic-calendar.yaml`，2031 年：周六 06-07 是调休工作日、
周一 06-09 是休息日），走真 CLI：`card status --json --at … --business-calendar …`，把卡面
解码后逐条读回来：

```text
无日历          calendar=UNKNOWN：没有日历声明，也没有被指定的时区，因此不判断这是哪一种日子
                ——本机不拿自己的时区替它作答。…（<path> 不存在，本年度的官方工作日历尚未导入）
声明的工作日     2031-06-10 是工作日，现在是 09:30（Asia/Shanghai），在常规播报窗口
                08:00–20:00 内 · 今天第 2/13 个时点
声明的休息日     2031-06-09 是休息日，不创建例行状态卡，也不补发；事故卡另按 P1/P2 策略
调休的周六       2031-06-07 是工作日，现在是 08:00（Asia/Shanghai），在常规播报窗口
                08:00–20:00 内 · 今天第 1/13 个时点
窗口之后        2031-06-10 是工作日，但现在是 21:00（Asia/Shanghai），晚于常规播报窗口
                08:00–20:00，当日卡不再因普通采样变化，不新增
```

第四条是这五条里唯一一条**不能由 `datetime.weekday()` 推出**的答案：那一天是周六。

#### 9.44.6 两处被测试抓到的自身缺陷

本片有两处是我自己写出来、被测试抓住、并由测试定住不再回来的东西。它们值得留在账上，因为
两个都是**默认值**出的错 —— 默认值是没人会去读的那种代码。

1. **一个纯整形器去读了宿主的主目录。** `snapshot` 曾把 `calendar=None` 默认成
   `read_declaration()`，于是 `tests/test_cards.py` 在**根本没有日历**的用例里，输出里多出
   一条关于 `/data/home/vince/.serveros/business-calendar.yaml` 的采集缺口。也就是说：一个
   合成输入上的纯函数，其答案取决于**跑它的那台机器上恰好有没有那个文件**。修正是让
   `calendar=None` 意味着「没有读数」：不打开文件、不产缺口、不产 schedule 块。
2. **`calendar=UNKNOWN` 在卡面上找不到。** `routine_window` 的 UNKNOWN 分支此前把句子交给
   `day()` 的某个分支生成，而那个分支在「无声明且无时区」时**只谈时区** —— 于是最需要被
   诊断看见的那个 token 在真正会出现它的那条路径上缺席。修正是在这一条分支里**保证**它
   （缺则前置），并加一条属性用例：四种 UNKNOWN 形状里 `says.count("calendar=UNKNOWN") == 1`
   —— 既要出现，也只出现一次。

#### 9.44.7 本片新增的用例与算术

| 文件 | 净变化 | 内容 |
|---|---|---|
| `tests/test_business_calendar.py`（新） | **+47** | 声明必须说清自己从哪来（13，全是拒绝）、读不到是一个答案而不是异常（5）、官方日历覆盖 weekday 推导（10）、窗口含两端（14，含四种 UNKNOWN）、`day_ref` 是去重会用的那个身份（3）、卡面上的句子来自设计（2） |
| `tests/test_card_cli.py`（新） | **+5** | 这条链此前**没有任何自动化在驱动它**：卡片的每一部分都被测过，命令没有。装载的是 `cli._card_modules` 解析到的**同一个模块对象**，因此被打桩的 `source.gather` 就是命令会调用的那一个 |
| `tests/test_cards.py`（改） | **+8**（57 → 65） | 全部落在 `StatusSourceTests`（13 → 21）：窗口内的读数上卡、身份是去重会用的那个、一天两个时点共用一个 day_ref、休息日不声称例行播报、未导入的日历是一条缺口而不是一个工作日、不给读数的调用方没有 schedule 也没有缺口、默认路径只有一处字面量、窗口边界是设计的而不是本地的 |

算术：**5916 ＋ 60 ＝ 5976**（实测 5976）。

#### 9.44.8 变异电池：18 条，18 杀，0 存活，0 SKIP

`mutate_slice_a.py`，同一套规矩（锚点必须唯一否则显式 `SKIP` 并非零退出、每轮删
`__pycache__`、从字节备份 `cp` 还原并 `sha256` 逐字节核对）。**18 条跨 5 个文件**，覆盖上面
那条链的每一跳：官方工作日/休息日不再覆盖推导、未覆盖年份按已覆盖处理、`_weekly` 永远返回
WORKDAY、fallback 冒报成官方推导、窗口下界/上界改成开区间、UNKNOWN 被答成「窗口之外」、
UNKNOWN 不再指名 `calendar=UNKNOWN`、同一天同时出现在两个列表里被接受、没有定位符的 source
被接受、休息日声称在窗口内、`day_ref` 丢掉管辖区、非 READ 的日历不再成为缺口、快照丢掉
schedule、渲染器丢掉那一行、模型降级时冒报成工作日、命令不再把它被要求读的日历传下去。

**18 杀、0 存活、0 SKIP**，还原后五个文件与备份**逐字节相同**（五个 sha256 见 §9.44.9）。
另有一条脚注记在这里：`tests/test_business_calendar.py` 与 `tests/test_card_cli.py` 里
`subTest` 出现 **0** 次，所以本片对 subtests 总数的贡献不来自这两个新文件 —— 这件事在
§9.44.9 里要用到。

#### 9.44.9 全量读数

```text
HEAD 3ccd6ea  dirty 8
start 2026-09-26T09:07:56+08:00
rc=0 at 2026-09-26T09:18:49+08:00
5976 passed, 20 skipped, 3994 subtests passed in 652.92s (0:10:52)
dirty after 8  HEAD 3ccd6ea

runtime/business_calendar.py  bb4ddb466707173df80c652a2bab8aff490e7487457a37a04137b6373ce7b2ba
runtime/card_source.py        5b616b4a5903a28810e85f4b10f36dcc5a252f48c4aa73ba0ccf3cad57445651
runtime/cards.py              5d47364da72fd0b5a4f773fec319901548754517f1549349ca86cfa4b1de4eb3
runtime/cli.py                e44fef2ba80ae31a26be7062a796734d0fbdc3313b735df778e4b35e67d394c5
runtime/presentation.py       e00cf278721ceaef21fafb3eb1fd310dac44bcf4d9cd0e9704035eb8c0379a9c
tests/test_business_calendar.py  db7f3bdec837b25300e264f9e43af06970e335668773f8e96033fa20eee5dd4c
tests/test_card_cli.py           7b9f74387eb40603f1debb834cfaa7a2070c60e9c7bf6565e438bff4d13d68d5
tests/test_cards.py              dcf29e1735fe26779404243e6547f20653f2722ad18eb7fefda446912131bd6f
```

- **算术闭合**：上一格 5916 ＋ 本片净新增 **60** 条 `def test_`（47 ＋ 5 ＋ 8，删 0 条）
  ＝ **5976** ✓。
- **subtests 3993 → 3994，而多出来的那 1 条不是新用例** —— 本片新增的两个测试文件里
  `subTest` 出现 0 次，`tests/test_cards.py` 在两棵树上都是 2709。我先把差异夹逼到一条
  具体的循环上再写结论：`tests/test_artifact_install_smoke.py::
  test_every_shipped_module_imports_from_the_install` 遍历**装出来的制品**里的每一个 `.py`
  并逐个 import（`tests/test_artifact_install_smoke.py:252-258`），而 `runtime/business_calendar.py`
  进了制品。同一支命令量两棵树：HEAD **112**、工作树 **113**。所以那 +1 是「制品多了一个
  模块」，不是「多了一条断言」；它顺带是**新模块在安装布局下真的能 import** 的证据（构建按
  文件复制，未跟踪的新文件也会进制品 —— 这条本身值得记）。
  量具：`git archive HEAD` 取出的一棵**未经改动**的树放在 `artifacts/tmp-p8/head-tree/`
  （该树跑全量需要三个环境变量，见下），同一支 `pytest` 命令、同一个 `-p no:randomly`。
- **跑前跑后 8 个文件的 sha256 逐字节相同，HEAD 未变，dirty 8 → 8。** 这一跑没有写进任何
  东西；台账这一节是在跑完之后写的，因此台账自己也不在那一跑的输入里（与 §9.43.9 那次
  「dirty 3 → 4」不同）。
- 一条给下一个人的注记：那棵 `git archive` 出来的树**不能直接跑全量**，
  `tests/test_architecture_check.py` 会先要 `SERVEROS_COMPOSITION_EVALUATOR`、再要
  `SERVEROS_OPS_REPO`（`tests/test_packs.py` 还要 `SERVEROS_ABILITY_REPO`），因为
  workspace-root 的祖先回溯在那棵树下面找不到任何 workspace。这不是缺口，是那几条用例
  刻意把「找不到」做成失败而不是静默跳过（§9.18、§9.40 记过它们的由来）；写在这里是为了
  下一个人不要把它当成第二次发现。

#### 9.44.10 它没做什么，仍未关闭的

- **没有建卡、没有补发、没有打补丁。** §11 的「一天一张、最新完整、前序折叠」一件也没做
  —— 本片只回答了「今天是什么样的日子」这一问。
- **没有触到任何生产面。** 节点上的状态卡要等 P12 的 `build→promote→restart` 才会带上这一行。
  今天 live 的 ops 发送方仍在用 `/data/home/vince/workspace/ops/scripts/tokyo-monitor/monitor_card.py:105-106`
  的 `QUIET_START_HOUR = 22` / `QUIET_END_HOUR = 9`：那是**免打扰时段**，与 §10 的**常规播报
  窗口**是两件事，本片**没有**把它们并起来，也没有动那个文件（它是生产部署点上的运行件）。
- **没有导入任何工作日历。** 本节点的 `~/.serveros/business-calendar.yaml` 仍然不存在，这
  正是「今天的默认答案是 `calendar=UNKNOWN`」的原因。导入要的是官方通知的 revision 与
  发布日，那是人的输入，本片**不替它编一个**。
- 仍未关闭（与前几格相同，本片一条也没有改变它们的状态）：P7 唤醒半的两件前置（§9.38.9）、
  `WAIT_KINDS` 仍只有 `child`/`window`、`NodeControlTransport.send` 不包裹 connector 异常、
  `drift_sync.py` 没有调度器、约 40 处未守护的考古站点、`supersede_checkpoint` 的退役评估、
  以及切片 F–N 的新模块在装好的节点上都还没有那一次 `build→promote→restart`（P12）。

### 9.45 P8 切片 B：一天一张卡 —— 建、折、封

#### 9.45.1 靶子：§11 的四句话，第三句此前一个落点都没有

§9.44 交付了「今天是什么样的日子」（`business_calendar`），§11 那四句话则一句都还没做：
**建卡、错过不追发、每次更新先折叠、20:00 之后不再因普通采样变化**。实测（`command grep -r`
在 HEAD `5fbb5a5` 的 `runtime/` 全目录）：`card_day|fold|history` 零命中，「当天卡」这个概念
在运行时里当时**不存在**。而三个读者已经在等它：`presentation.status_model` 新增的 `day`
键、`cards.status_card` 的「当天卡」行、以及 §10 那 13 个时点。

#### 9.45.2 四句话各自的落点，以及四种日子与两种拒绝

| §11 的话 | 落点 |
|---|---|
| 每 WORKDAY 在首个允许时点建一张 | `decide` → `CREATE/NEW_WORKDAY` |
| 错过 08:00 可在后续窗口建、**不追发** | `CREATE` 时 `missed_ticks = tick - 1`，句子里写明「未追发」；已错过的小时**一条也不补建** |
| 每次更新先把旧 latest 压成 compact history row | `fold`（顺序承重，见 §9.45.4） |
| 20:00 后不再因普通采样变化、次一 WORKDAY 新建 | `routine_window` 的 `in_window`；跨日时 `CREATE/NEW_WORKDAY` 且 `rollover=True` |

四种日子各有一条实测路径（§9.45.6）：工作日、休息日（`SKIP/NOT_A_WORKDAY`）、调休的工作
周六（建卡）、以及**没有日历**（`SKIP/CALENDAR_UNKNOWN`）。第四种是本片唯一一条**不能由
另一条推出**的拒绝：它不说「不播报」，它说「这个节点没有身份可归属」，因此它与休息日在
词表里是两个词、在卡面上是两句话。

#### 9.45.3 判决反转：V28 的「整点」不是对秒的拒绝

这是本片最要紧的一条判决，也是我**先写错、被测试与设计文档一起推翻**的一条。

V28 说「08:00–20:00 **整点**只 patch 一张 Status Card」。我最初把它读成「不在整点上的
采样一律 `SKIP`」，并为此加了 `NOT_A_TICK` 这个理由词。这个读法是错的，两条理由都写在
`decide` 的 docstring 里：

- **§11 第四句说 20:00 之后「不再因普通采样变化」**，这句话本身承认窗口**之内**的普通采样
  **确实**会改卡。09:30 的那一拍就是这种采样。拒绝它，是把一句关于例行更新的话读成了对同一
  段话所依赖的那种采样的禁令。
- **`routine_window` 自己就带着答案**：它报告 `on_the_hour` 并在 docstring 里写明小时是
  「推荐」，「推荐不是规则 —— 晚一分钟还是同一个时点」。09:00:20 起跳的定时器就是那一拍。

所以「整点」真正禁止的是**同一小时出现第二行**，而这件事**已经由时点序号保证**：09:00 与
09:30 都是第 2 个时点，第二次看是**替换**这一小时而不是新增一行，10:00 到来时这一小时仍然
只被折叠一次。反过来按秒强制，代价是真实的：一个慢慢漂移的定时器会让当天的卡**此后再也不
更新**，卡面整天停在 08:00，而节点一切正常 —— 这正是这一整套工作要消灭的那种「陈旧却看不
出来」的表象。

`NOT_A_TICK` 因此从词表里消失，`REASONS` 从八项回到八项的另一套组合（末项换成
`OUT_OF_ORDER`）；`20:30` 属于 `AFTER_WINDOW` 而不是「不在整点」；「一小时一行」由序号而
不是时钟的秒来保证。

#### 9.45.4 模块形态：行是白名单、一小时一行、读不到就拒绝

- **行是白名单，不是裁剪。** §11 说 history 行保留「关键人读数字和状态」、不抄
  `PID/code/raw probe`。一条**裁剪**规则必须知道 model 将来会长出哪些机器细节；一条**只能
  按名单构造**的行则根本带不上任何别的东西。所以 `row()` 按名抽取、`check_row()` 拒绝任何
  按名之外手写出来的行 —— 后者才让前者成为**文档的性质**，而不是某个读过 docstring 的调用
  者的习惯。名单是 `ROW_FIELDS = ("tick","hhmm","local","verdict","observation","sections","gaps")`，
  其中 `ROW_SECTIONS` 与 `presentation.SECTIONS` 由测试**互校**，不靠注释承诺。
- **`hhmm`/`local` 取日历的时区，不取本机的。** §9 把时区权威放在声明里，一条按别的时区打的
  行会和它所声称身处其中的那个窗口自相矛盾。
- **同一小时的第二次看是替换，不是折叠。** §11 第三句说那句历史是「按小时」的，追加会让
  一小时占两行。
- **往回的一拍不写（`OUT_OF_ORDER`）。** 让更早的一拍写进去比它看起来更坏：latest 会倒退、
  **下一**个真时点会把错的小时折进历史、面板把这一天倒着列。拒绝它，让「按小时向前」成为
  文档的不变量，而不是对时钟的一个期望。它不是一个错误（那一拍被读对了），所以是带自己理由
  的 `SKIP`。
- **`DayStore` 的三态与「不可读就拒绝」。** 不存在的 store 是答案（今天还没有卡）；能读到
  是 READ；**存在但读不出来是拒绝，不是重置**。理由是本文件独有的：从空文档开始会让**同一个
  工作日**拿到第二张 NEW 卡，而 V28 点名禁止这件事 —— 丢掉记忆不是危害，**重新建卡**才是。
  这条拒绝的文案里写明了这一点，因为下一个遇到它的人会合理地以为「重来」是安全的动作。
- `missed_ticks`、`updates` 都是从行**推**出来的，不是另存的字段：并存两个「这里有几小时」
  就是两个答案，而 §10 的 13 也只能对着行本身检查。

#### 9.45.5 消费链：读 → 快照 → 模型 → 渲染器 → 两个命令

| 位置 | 落点 |
|---|---|
| `runtime/card_day.py` | 新模块：`decide` / `row` / `check_row` / `fold` / `apply` / `summary` / `DayStore` / `day_path` |
| `runtime/card_source.py` | `load_day_store(path)` 三态读数；`snapshot(..., day=)` 把 READ/ABSENT 的当天卡放进模型，**INVALID 变成具名采集缺口**；`DEFAULT_DAY_STORE_PATH` 是 `card_day.DEFAULT_PATH` 的**别名而不是第二处字面量** |
| `runtime/presentation.py` | `status_model` 新增 `day` 键 |
| `runtime/cards.py` | 状态卡新增 `**当天卡** …` 一行，以及「今日历史（N 小时）」折叠面板 |
| `runtime/cli.py` | `card day`（只读：`decide` 的答案）与 `card day-advance`（**唯一的写者**：`DayStore.advance`） |

两个命令的读写分工与 §3 的投递台账同形，理由也一样：**一个既能读又能写的读者，是一次没有
人记录下来的决定**。`card day` 不落盘（实测：store 的 sha256 前后相同），`card day-advance`
在 `SKIP` 时也不落盘且**不需要 model**（今天不播报这一小时，为它写一行就是让历史长出一条这
一天从未播报过的小时）。

**ABSENT 与 INVALID 被区别对待，而这个区别不是不一致**：§9 的验收判据要求诊断上必须出现
`calendar=UNKNOWN`，所以读不到的日历**不可能**沉默、且永远是缺口；没有任何判据说当天的卡
缺席是故障，它也不是 —— 空的一天就是一天开始前的样子 —— 所以它**说话但不报缺口**。
「拿到一个说 ABSENT 的读数」与「什么都没拿到」是两个分支，后者既没有 schedule 块也没有
day 块：一个纯整形器不打开任何文件，否则每个调用方的答案都取决于它**碰巧跑在哪台机器上**。

#### 9.45.6 手工跑过的十二条真路径

同一支合成声明（`artifacts/tmp-p8/synthetic-calendar.yaml`，2031 年：周六 06-07 是调休工作
日、周一 06-09 是休息日），走真 CLI，一拍一个进程，把 JSON 解码后逐条读回来：

```text
 1  无日历 09:00          SKIP  CALENDAR_UNKNOWN  未落盘   「没有可用的当天身份」
 2  工作日 07:30          SKIP  BEFORE_WINDOW     未落盘   「进入窗口后的第一个整点建卡，错过的不追发」
 3  工作日 08:00          CREATE NEW_WORKDAY      落盘     tick=1  latest=1/08:00  updates=1
 4  同小时再看 08:30      PATCH SAME_WORKDAY      落盘     tick=1  latest=1/08:30  updates=1  history=[]
 5  09:00                 PATCH SAME_WORKDAY      落盘     tick=2  latest=2/09:00  updates=2  history=[1]
 6  11:00（跳过 10:00）   PATCH SAME_WORKDAY      落盘     tick=4  latest=4/11:00  updates=3  history=[1,2]
 7  回到 09:30            SKIP  OUT_OF_ORDER      未落盘   「往回的一拍不写，避免历史出现倒序」
 8  20:00                 PATCH SAME_WORKDAY      落盘     tick=13 latest=13/20:00 updates=4  history=[1,2,4]
 9  20:30                 SKIP  AFTER_WINDOW      未落盘   「次一工作日新建」
10  休息日 06-09 08:00    SKIP  NOT_A_WORKDAY     未落盘   06-10 那张开着的卡未被碰
11  调休的周六 06-07 08:00 CREATE NEW_WORKDAY     落盘     翻过 06-10（previous 记下它）
12  06-11 08:00           CREATE NEW_WORKDAY      落盘     rollover，翻过 06-07
```

四条读数只在这一跑里能同时看到，值得单独抄下来：

- **第 4 条是整点语义的实证**：同一小时第二次看，`tick` 仍是 1、`hhmm` 变成 08:30、
  `updates` 仍是 1、`history` 仍是空 —— 这一小时被**替换**了，没有被记第二遍。
- **第 8 条是窗口含上界**：20:00 自身仍是第 13 个时点、仍是 `PATCH`，窗口在**最后一次例行
  patch 之后**才结束；20:30 才是那一跑里唯一一条 `AFTER_WINDOW`。
- **第 7 条**证明往回的一拍连文件都不写（前后 sha256 相同）。
- **第 10 条**：休息日的那一拍照常被拒绝，而当时 store 里开着的 06-10 那张卡**一个字段都没
  动** —— 「不创建」不等于「清空」。

消费者一侧（`card status --json`，同一份 store）：卡面出现两行，且没有重复主语 ——

```text
**播报日程** 2031-06-10 是工作日，现在是 11:30（Asia/Shanghai），在常规播报窗口
             08:00–20:00 内 · 今天第 4/13 个时点
**当天卡**   CN-Mainland/2031-06-10 已更新 3/13 个时点
   └ 今日历史（2 小时）
       08:00 UNCONFIRMED · mine=OK · host=OK · gateway=UNKNOWN · 无采集缺口（部分新鲜）
       09:00 UNCONFIRMED · mine=OK · host=OK · gateway=UNKNOWN · 无采集缺口（部分新鲜）
```

换一个**不存在**的 store 路径同一时刻再渲染：`**当天卡** 今天还没有当天的卡`（照说，不报
缺口）。把一个**坏掉**的 store（`{ this is not json`）交给两个命令：`card day-advance` 退出
`1` 并在 stderr 上给出完整理由、文件 sha256 前后**原样**；`card status` 退 `0`、把
`card_day_store（…）未读到：…` 印成采集缺口、文件**照样原样** —— 渲染不得重置文件，这是
本片唯一一条「读的一方也可能毁掉数据」的路径，实测挡住了。

落盘文档的形状（第 12 条之后）：`schema=serveros/card-day.v1`、`open.day_ref`、
`open.history`、`open.latest`（键恰为那七个）、以及 `previous`。**`previous` 只有一个槽**，
它是「刚刚被翻过的那一张」而不是卡的历史列表 —— 第 11→12 条连着翻两次后它记的是 06-07 而
不是 06-10。这是有意的：一天一张卡的**当天**记忆归这一个文档，历史卡由聊天记录与 §3 的投递
台账承担；在一个文档里堆一份会与那两处各说一套。

#### 9.45.7 三处被自己抓到的东西

1. **重复主语。** `summary()["says"]` 曾以「当天卡」开头，而它的两个消费者（`card day` 的
   `当天卡` 列标签、`cards.status_card` 的 `**当天卡**`）都已经给了主语，于是卡面上读出
   「**当天卡** 当天卡 CN-Mainland/…」。修正是这句话不再带主语，**并把这个理由写进代码**，
   否则下一个人会「顺手」把它加回去。
2. **`snapshot` 的 docstring 与代码不一致。** 文档说 ABSENT 的读数「不算缺口」，代码却
   **照说**「今天还没有当天的卡」。这次是文档错：调用方问过，而这句话就是答案；只有 INVALID
   才是要人去修的状态。已把 docstring 与代码对齐，并写清「拿到一个说 ABSENT 的读数」与
   「什么都没拿到」是两个分支。
3. **整点语义的反转**（§9.45.3）。它同时决定了 `NOT_A_TICK` 从词表消失、`20:30` 归
   `AFTER_WINDOW`、以及「一小时一行」由序号而非时钟的秒保证。

#### 9.45.8 本片新增的用例

| 文件 | 净变化 | 内容 |
|---|---|---|
| `tests/test_card_day.py`（新） | **+75** | 行只留卡说过的东西（11，含 `ROW_SECTIONS == presentation.SECTIONS` 互校、白名单拒绝、PID/raw 扫描、`hhmm` 取自日历时区）、当天的卡在第一个允许时点创建（19，含漂移的定时器、同小时第二次看是同一个时点、窗口在最后一次 patch 之后才结束、没有日历就没有身份、往回的一拍被拒）、每次更新折叠它替换掉的那一小时（13，含折叠不会让一天超过自己的小时数、叫不出名字的动作被拒、`advance` 需要它所折叠的那一小时的 model）、一个工作日是同一张卡上的十三拍（5，含一小时一行无论看多少次、次一工作日新建并关上旧的、中间夹一个休息日两边都不碰）、当天的记忆是投递台账旁边的**一个**文档（14，含与 `card_delivery.ledger_path` 同形、13 是上界、latest/history 手写行被拒、渲染器的默认路径与写者的是同一条）、两个命令读写不同的东西（13，含只读的不写、写后读一致、`--help` 走 `SystemExit`、嵌套面板里能找到当天卡行与折叠行、缺 store 说「还没有」、不可读的 store 成缺口且不擦除） |

`tests/test_card_day.py` 里 `subTest` 出现 **0** 次，所以本片对 subtests 总数的贡献不来自
它 —— 这件事在 §9.45.10 里要用到。另外两个测试文件（`test_card_cli.py`、`test_cards.py`）
本片**一个字节也没有改**，sha256 与 §9.44.9 表里的相同。

#### 9.45.9 变异电池：40 条，40 杀，0 存活，0 SKIP

`mutate_slice_b.py`，同一套规矩（锚点必须唯一否则显式 `SKIP` 并非零退出、每轮删
`__pycache__`、从字节备份 `cp` 还原并 `sha256` 逐字节核对）。**40 条跨 5 个文件**
（`card_day.py`、`card_source.py`、`presentation.py`、`cards.py`、`cli.py`），覆盖上面那条链
的每一跳：行可以带名单外的字段/缺字段/指名第四个区块/把缺口文本当计数/没有 verdict/用固定
小时打戳/用固定状态填三块、晚到的首拍声称什么都没错过、休息日不再是自己的拒绝、没有日期身份
被答成新的一天、窗口之前被答成窗口之后、窗口之后不再被放行、时点序号不受 13 约束、13 是字面
量、往回的一拍照样写、另一天的卡被 patch、同小时第二次看改成折叠、折叠不丢弃超过上界的旧行、
翻过的一天不被记下、一天允许两次创建、没有卡可 patch 时凭空造一张、对着另一天的卡 patch 照
写、带行的 `SKIP` 被记下、叫不出名字的动作被接受、`updates` 忘掉 latest、summary 不再指名它
属于哪一天、折叠行不再说有几处缺口、不可读的 store 被替换成空的、超过一天小时数的 store 被
接受、有名字但读不出的 open day 被接受、读者不再校验它读回来的行、`SKIP` 被当成别的答案写
下去、交给卡片的 day 到不了快照、不可读的 day store 不再是具名缺口、不可读被报成缺席、渲染
器的默认路径与模块的漂开、模型丢掉被交给它的 day、卡片不再印当天卡行、卡片不再印折叠小时、
命令不再读它被告知要读的那个 store。

**40 杀、0 存活、0 SKIP**，还原后五个文件与备份**逐字节相同**（五个 sha256 见 §9.45.10）。

首轮电池是 **36 杀、4 存活**（B2/B24/B26/B31），四条都是**我的测试缺口**而不是冗余代码，且
是两类不同的缺口，记录如下：

- **B2（行缺字段被接受）与 B24（叫不出名字的动作被接受）是锚点选错**：原来的锚点改掉的那
  一行，恰好会被**同一函数里另一条更早的守卫**兜住（`gaps` 的类型检查、行守卫），于是杀掉
  的是守卫而断言的还是守卫。改成让变异**通过所有别的守卫**（B2 改用 `hhmm/local/observation`，
  B24 改成喂一份「其他守卫都接受的良构文档 + 行」）之后两条都被测到。
- **B26（summary 不再指名它属于哪一天）与 B31（读者不再校验它读回来的行）是真缺口**：前者
  `summary()["day_ref"]` 这个键**没有任何断言**，后者 history 行的 `check_row` 循环**没有
  一条用例**把手写的 history 行喂进去。两者都补成了用例
  （`test_a_store_whose_folded_history_was_hand_written_is_refused` 等），而不是靠「代码看
  着对」放过。

次轮电池是**在同一份测试上重跑的**，因此 40 条全部被杀、且没有一条靠改动测试而变绿。

#### 9.45.10 全量读数

```text
HEAD 5fbb5a5  dirty 6
start 2026-09-26T10:11:07+08:00
rc=0 at 2026-09-26T10:22:09+08:00
6051 passed, 20 skipped, 3995 subtests passed in 656.48s (0:10:56)
dirty after 6  HEAD 5fbb5a5

runtime/card_day.py              e0ea77a723fed8b3a8dbcbb0d665995f66d572dad7355f140c1b91d162f48570
runtime/card_source.py           e82241d8b25580e16949531400d0aa9d7a4fd81950a707983e286708a7b4231a
runtime/cards.py                 dd8b9db9d310d5b37462aeaddb8583d72d63a13b97232b72539ce86705eae97b
runtime/cli.py                   e44c30b2d6fc5da37c89073628d18c2957e6c40859daacc3818e23d9e350bf26
runtime/presentation.py          12a65f202607dcd3ab89a48976b0682ee7fc6f2a8fe8bfa26ba3f128e55d58e4
tests/test_card_day.py           bd258540a3424223f9541b2db6e10502b20295f303eefda8bffb89cbdb50247d
tests/test_card_cli.py           7b9f74387eb40603f1debb834cfaa7a2070c60e9c7bf6565e438bff4d13d68d5
tests/test_cards.py              dcf29e1735fe26779404243e6547f20653f2722ad18eb7fefda446912131bd6f
```

- **算术闭合**：上一格 5976 ＋ 本片净新增 **75** 条 `def test_`（全部在 `tests/test_card_day.py`，
  删 0 条）＝ **6051** ✓。
- **subtests 3994 → 3995，而多出来的那 1 条不是新用例** —— 本片新增的测试文件里 `subTest`
  出现 0 次，另外两个测试文件的 sha256 与上一格逐字节相同。夹逼到具体一条，与 §9.44.9 **同一
  条、同一机制**：`tests/test_artifact_install_smoke.py::InstalledArtifactTests::
  test_every_shipped_module_imports_from_the_install` 遍历**装出来的制品**里的每一个 `.py`
  并逐个 import，而 `runtime/card_day.py` 进了制品。同一支命令量同一棵树：上一格 **113**、
  本格 **114**。顺带又是一条「新模块在安装布局下真的能 import」的证据。
- **跑前跑后 8 个文件的 sha256 逐字节相同，HEAD 未变，dirty 6 → 6。** 这一跑没有写进任何
  东西；台账这一节是在跑完之后写的，因此台账自己也不在那一跑的输入里。

#### 9.45.11 它没做什么，仍未关闭的

- **没有发送、没有触到任何生产面。** 「建卡、折卡、封卡」都在**文档**上完成：本片交付的是
  当天的卡**是什么**，以及这一拍**对它做什么**。把它变成一条聊天记录仍然是 §3 投递台账与
  ops 发送方的事，而那条路要求审批（P8 的其余部分）。
- **没有调度器。** 没有任何东西在 08:00 自己跑起来；`card day-advance` 要有人（或 P7/P12 的
  循环）去调。这与 `drift_sync.py` 的调度器是同一类缺口，本片没有改变它。
- **`previous` 只有一个槽**（§9.45.6 末），历史卡不进这个文档，这是有意的。
- **没有导入任何工作日历**，节点上仍然没有 `~/.serveros/business-calendar.yaml`，因此本机
  上 `card day` 的默认答案仍是 `SKIP/CALENDAR_UNKNOWN`；`~/.serveros/cards/day.json` 也尚未
  创建。
- 仍未关闭（与前几格相同，本片一条也没有改变它们的状态）：P7 唤醒半的两件前置（§9.38.9）、
  `WAIT_KINDS` 仍只有 `child`/`window`、`NodeControlTransport.send` 不包裹 connector 异常、
  `drift_sync.py` 没有调度器、约 40 处未守护的考古站点、`supersede_checkpoint` 的退役评估、
  以及切片 F–N 的新模块在装好的节点上都还没有那一次 `build→promote→restart`（P12）。

## §9.46 切片 C：当天卡记住是哪一条**消息**承载它

#### 9.46.1 靶子：§11 的「只 patch 一张」还有聊天消息那一半，此前一个落点也没有

§9.45 交付了「今天是什么样的日子」与「这一拍对当天卡做什么」。四句话里的第三句在**文档**
上完成了。但「只 patch 一张」这句话真正的主语是**消息**：08:00 建的那张卡是一条聊天记录，
09:00 与 10:00 的更新必须落在**同一条**记录上。这棵树里没有任何地方记得它的 id，因此也没有
任何地方能让「下一次是 patch 而不是新发」成为可判定的回答——一个不记得自己发过哪条消息的
系统，只可能再发一条。

本片补的是这半张：文档记住承载它的那条消息（`open.message`），判决同时回答消息那一半
（`decide(...)["message"]`），并给「记下它」一个动词（`card day-announce`）。

#### 9.46.2 八处落点

| 落点 | 是什么 | 在哪 |
| --- | --- | --- |
| 动词 | `im messages patch`——**不是** `im +messages-edit`，后者是文本/富文本编辑器 | `message_transport.LarkMessageSender.update` |
| 载荷 | `--data '{"content": "<把卡片 JSON 序列化成的字符串>"}'` | `message_transport.card_content` |
| 四个本地拒绝 | 没有 id／时刻读不出／超出 14 天／超出 30 KB，**四者都不回落到新发** | 同上 |
| message id 是什么 | 一个 token、必须是文本、不是 `oc_`/`ou_`/`on_` | `card_delivery.check_remote_id`，`record()` 是唯一入口 |
| 回执留住它 | `remote_id` 进台账行 | `card_delivery.record`；daemon 原样透传 |
| 绑定 | `open.message = {remote_id, at}`，三词闭集 BOUND/UNCHANGED/REBOUND | `card_day.DayStore.announce` |
| 判决 | `PATCH_MESSAGE`/`NEW_MESSAGE`/`NO_MESSAGE` ＋ 六种理由 | `card_day.message_verdict` |
| 两个命令 | `card day-announce --message-id`（required）、`card record-delivery --message-id` | `runtime/cli.py` |

`card day` 与 `card day-advance` 的输出里也多了两行（消息判决与它的整句），因为「下一拍是
patch 还是新发」是人真正要看的那个决定。

#### 9.46.3 载荷形态的不对称：两个动词包同一个对象的方式不同（实测钉死）

- `+messages-send --content '<card json>'`：`--content` **就是**卡片 JSON。
- `im messages patch --data '{"content": …}'`：`content` 是那个 JSON **序列化成字符串**之后再
  装进请求体。

写错了**不会报错**：服务接受请求，然后把卡片的正文当**文本**放到屏幕上，读的人看到的是一张
坏掉的卡。所以这个差别只在 `card_content` 一处写下来，`update` 从它取值，
`test_the_body_is_built_by_the_one_helper` 钉住「通道用的就是这一个」。手工读数：请求体里
`content` 的类型是 `str`，`content == card_content(card)` 为 True。

#### 9.46.4 服务自述的三条界，以及为什么两边都拒

`im messages patch` 自己声明：消息必须 14 天内发出、`content` 是不大于 30 KB 的 JSON 序列化
字符串、仅 interactive。

- **14 天**：`CARD_UPDATE_WINDOW_DAYS = 14`，**被读、不被抄**。`card_day.update_window_days()`
  从 transport 取这个常量，`card_day` 里没有第二份 14——两处数字会在服务移动窗口的那天第一次
  分歧，那时本模块会承诺一次 transport 拒绝的 patch（或拒绝一次它本会接受的）。
- **30 KB**：`KB` 没说清是字节还是字符，所以**两边都拒**：`CARD_CONTENT_LIMIT_BYTES = 30*1024`、
  `CARD_CONTENT_LIMIT_CHARS = 30*1000`，哪个先到算哪个。严一点的成本是一句拒绝；松一点的成本是
  调用方以为更新发出去了，而人看着的还是上一张卡。

#### 9.46.5 两条时钟：同一件事在两个模块里是两种形状

- `message_transport.update` 拿**真机器时钟**量窗口（`_age_seconds(sent_at)` 对 now）——因为它
  真的要去改一条消息，而「现在」只有一个。
- `card_day.message_verdict` 的两个时刻**都由参数给**（这一拍的 `at` 与记下的那个 `at`），不读
  时钟——因为它是**判断**：同一份文档在不同时刻必须给出同一个答案，而要回答的是「这一拍」。

这个差别在手工检查里先骗过了我自己的脚本：我拿 `2031-05-01` 当「超窗」样本，通道那半边却答了
`DELIVERED`——那个时刻在真机器时钟（2026-09-26）看来是**未来**，age 为负，窗口当然没超。换成
`2026-01-01`（约 268 天前）后拒绝如期出现。这是脚本自己的 bug，不是代码的；但它顺带把「两条
时钟不是一条」量成了读数。

#### 9.46.6 六种理由，以及一句诚实边界

`NEVER_SENT`／`IN_HAND`／`IDENTITY_MISSING`／`IDENTITY_EXPIRED`／`IDENTITY_UNDATED`／
`NOT_ROUTINE`。判决是**故意**不做成「能 patch 就 patch，否则就发」的：`PATCH_MESSAGE` 只在
「身份确实在手、且还在服务窗口内」时给出，其余一律 `NEW_MESSAGE` **并带上不能 patch 的理由**
——「我们从没记下 id」和「id 太旧用不了」需要不同的修法，一个笼统的 SEND 会把第一件藏起来，
而第一件正是这一节存在的理由。

`IDENTITY_MISSING` 那一句说的是它**不知道**的事：这份文档不知道卡是否真的发出过。真发出过，
这时新发一条就是第二张卡（V28 只允许一张）；没发出过，那就还没有消息。只有投递台账
（`card deliveries`）能分辨这两者，所以句子**指向台账**，而不替读者挑一个世界。

#### 9.46.7 三词闭集代替布尔值

`BOUND`／`UNCHANGED`／`REBOUND`（`ANNOUNCE_CHANGES`）。「这张卡原本还没有消息」与「这张卡的
消息被换掉了」是两件事，一个 `changed: true` 会把第一件报成第二件——读起来像「有人重发了
卡」，而没有人重发。

窗口从**发出时刻**量：同一条 id 重复 announce 保持首次的 `at`。否则每重跑一次公告就把服务的
可改窗口往后推一点，而那个窗口是「消息什么时候发出去的」的属性，不是「谁什么时候又敲了一遍
命令」的属性。

#### 9.46.8 一条规则一处写

- 「message id 是什么」只有 `card_delivery.check_remote_id` 一处实现，`record()` 是唯一入口，
  所以这一条规则覆盖每一个调用方，包括守护进程（socket 上唯一的写入者）。daemon 那边是
  `remote_id=params.get("remote_id")`：**原样转交**，不在自己这里再判一次。
- **deny list 而不是 allow list**：只拒 `oc_`/`ou_`/`on_`。两边代价不对称——拒掉一个本树没见
  过的**合法**形态会丢掉一次真实发送；接受一个 chat id 会让后续每一次 patch 都瞄准**会话**。
- **非文本拒绝而不是 stringify**：`str(b"om_x")` 是 `"b'om_x'"`，一个看起来良构、谁也寻不到
  的值——正是这个函数要拦的静默失败，而它会从一个上游漏掉的 `.decode()` 进来。同理，含空白的
  值也拒（一个 token 里有空白就不指名任何消息），且两句都不回显那个值。

#### 9.46.9 手工跑过的真路径（读数）

六节：A 真 CLI 逐拍（13 拍，一拍一个进程）、B 绑定时限与时刻、C 消费者一侧、D 通道半边（真
模块 + 假 `run`）、E 落盘文档形状、F 说明为什么**不在这里**跑 `card record-delivery`。

A 的读数按顺序为：还没有卡 → 新建且 `NEVER_SENT` → 写入（CREATE）→ `IDENTITY_MISSING`
（卡在、消息不在）→ `BOUND` → `PATCH_MESSAGE/IN_HAND` → 同一条再报是 `UNCHANGED` 且发出时刻
不动 → `REBOUND` → `oc_` 被拒（rc=1，不回显）→ 含空白被拒（rc=1）→ 不带 `--message-id` 被
parser 拒（rc=2）→ 次一工作日新建一张。B：窗口 14 天（20 天前 → `IDENTITY_EXPIRED`）、不带
时区的时刻 → `IDENTITY_UNDATED`。D：四次拒绝里通道只被碰过 1 次（那条成功的 patch）。

两条如实记下、本片**没有擅自改**的观察：

1. **`previous` 是一个五字段摘要**（`day_ref`/`kind`/`opened_at`/`closed_at`/`updates`），
   **没有 `message` 这一栏**，也没有 `history`/`latest`。所以跨日之后「上一张卡的消息」不在
   这个文档里——`announce` 的 docstring 正是这么写的：旧的 id 由投递台账保存，本文件只保留
   当前的那个绑定。
2. **`card status --at <早于文档 open day 的时刻>` 渲染的是文档当前的 open day**，不是传入
   时刻所属的那一天：把 store 推到 2031-06-11 之后，用 06-10T12:00 去读，印出来仍是
   `CN-Mainland/2031-06-11`。这条命令上 `--at` 的主语是「这一拍对**当前文档**说什么」，不是
   「那一天是什么」。

#### 9.46.10 本片新增的用例

**58** 条 `def test_`，删除 **0** 条，全在四个文件里：`test_message_transport.py` +12、
`test_card_delivery.py` +12、`test_card_day.py` +30、`test_daemon.py` +4。`git diff
--name-only -- tests/` 只有这四个文件，测试目录里没有第五个文件被改动一个字节。

承重的几条：`test_the_body_carries_the_card_as_a_string_and_not_as_an_object`、
`test_a_patch_with_no_message_id_is_refused_without_a_call`、`test_content_over_the_ceiling_is_refused_on_either_reading`、`test_a_refusal_never_falls_back_to_sending`、
`test_the_module_borrows_a_declaration_and_not_a_call`、
`test_the_verdict_reads_that_number_rather_than_carrying_a_copy_of_it`（把
`transport.CARD_UPDATE_WINDOW_DAYS` 先改 30 再改 3，看判决跟着走）、
`test_repeating_an_announcement_does_not_move_the_send_time_forward`、
`test_an_announcement_needs_the_instant_it_was_made`、
`test_the_announce_verb_is_not_runnable_without_the_id`（钉 argparse 层的 rc=2）、
`test_the_record_path_applies_the_rule_and_not_only_the_helper`、
`test_a_card_that_does_not_hash_to_the_version_is_refused`、
`test_a_send_the_transport_could_not_name_keeps_the_absence`。

约定沿用前几片：同一个文件用**同一个模块名** `_load(...)`，才能与 CLI/daemon 共享同一份对象，
而不是各自加载出一份看着一样、其实不同的副本；`test_card_day.py`/`test_card_delivery.py` 用
`unittest.TestCase`，`test_message_transport.py`/`test_daemon.py` 用 pytest fixture。

#### 9.46.11 变异电池：35 条，35 杀，0 存活，0 SKIP

覆盖五个实现文件（`message_transport` 的动词与四个拒绝与载荷、`card_delivery` 的 id 规则与
回执、`card_day` 的判决与绑定与读者、`cli` 的两个命令、`daemon` 的透传与版本校验），逐条锚点
在文件里必须只出现一次，`__pycache__` 每轮清一次（同尺寸同秒的还原会骗过 pyc 校验）。

首轮 **33 杀 / 2 存活**。两条都不是冗余代码，而是**我的断言钉错了对象**：

- **C22（判决里的窗口写成字面量 `14`）**：原测试只钉了 `update_window_days()` **返回** 14，没有
  钉判决**用了**它。补一条把 `transport.CARD_UPDATE_WINDOW_DAYS` 先改 30 再改 3 的用例（断言
  `window_days` 30/3、action 与 reason 跟着变），重跑后 C22 被杀。
- **C33（`--message-id` 的 `required=True` → `False`）**：原断言只写了 `code != 0`，而 store 层
  的拒绝也返回非零，于是「参数缺失」与「值不合法」两件事被同一个断言放过。改成断言 `code == 2`
  （argparse 层）并检查文本含 `--message-id`，重跑后 C33 被杀。

子集重跑 **2/2 杀**。**35 杀、0 存活、0 SKIP**，还原后五个文件与备份**逐字节相同**。

#### 9.46.12 全量读数

```text
HEAD 2db4db1  dirty 9
start 2026-09-26T11:10:35+08:00
rc=0 at 2026-09-26T11:21:18+08:00
6109 passed, 20 skipped, 4000 subtests passed in 639.62s (0:10:39)
dirty after 9  HEAD 2db4db1

runtime/message_transport.py   cfdc1cb89d22f9cb302b8c185b04e137776112b554fe8e1d058ac1730b35b959
runtime/card_delivery.py       9cdb8ba6ad1e2038334703ecc74edeb4e84308bc8229f043ad74170017362e04
runtime/card_day.py            001e6ab868dddf262abbd43da80b082848d9e05e5c737696479c653f7ab549ff
runtime/cli.py                 4789a571e58fa5a9136909a7d188322d68e3b011b93f07476acee162b5961b82
runtime/daemon.py              b0f48688de0baac4da6f2d78dc798b11baba0c188c32d04a513db5195c09043e
tests/test_message_transport.py 881c5a284877e6fcabe4cc578a3c7eb74bf0d1ee0d606b3305f630a147cc5210
tests/test_card_delivery.py    b67d77944905440874c5da458ffba91cac1c62a3ee2e45b50d0ee4b2b70bc386
tests/test_card_day.py         e5b232150f01c9c22d7e895843e301b2023ce2bdaf10d6feee2dfd504e74376b
tests/test_daemon.py           4107da0c4b33b8d9b4d0dc53d9f32c2e57fb5867d070652e9d9e670e0c848828
```

- **算术闭合**：上一格 6051 ＋ 本片净新增 **58** 条 `def test_`（`git diff -U0` 逐文件计数
  12/12/30/4，删除 0 条，且测试目录里没有第五个文件）＝ **6109** ✓。
- **subtests 3995 → 4000，+5，而且这 5 条有出处**：本片新增的
  `test_every_message_answer_is_in_the_closed_vocabularies` 里有**一个**
  `self.subTest(instant=instant)`，循环 5 个时刻（休息日 08:00、工作日 07:00/09:00/21:00、次一
  工作日 08:00），一次循环一条 subtest，恰好 +5。**不是** §9.45 里那条制品 import 计数——本片
  没有新增运行时模块，同一支命令（`-k test_every_shipped_module_imports_from_the_install`）
  在同一棵树上仍然量出 **114 个模块**，与上一格逐字相同，这是**量过**的而不是推出来的。
- 前五格的五个 sha256 与变异电池备份的前缀逐项吻合（`cfdc1cb89d22`/`9cdb8ba6ad1e`/
  `001e6ab868dd`/`4789a571e58f`/`b0f48688de0b`），即「还原后逐字节相同」这句话与全量读数来自
  同一份字节。
- **跑前跑后 9 个文件的 sha256 逐字节相同，HEAD 未变，dirty 9 → 9。** 这一跑没有写进任何东西；
  台账这一节是在跑完之后写的，因此台账自己也不在那一跑的输入里。

#### 9.46.13 它没做什么，仍未关闭的

- **没有发送、没有改动任何一条消息。** 手工检查里通道那半边用的是真模块 + 假 `run`
  （`/nonexistent/lark-cli`），一次真实 patch 都没有发生；`card day-announce` 只写当天的 store，
  而且写的是 `artifacts/tmp-p8/` 下的临时文档。
- **没有从文档一侧真的调过 `card record-delivery`。** 台账的唯一写入者是守护进程，而本机**此刻
  就有活着的守护进程**（`serverosd`，状态目录在 `…/work-items/serveros/SRV-0011/RUN-0001/
  release/…` 下，另有 `runtime-assets` 下那一条）。从手工脚本写进去会在一个活着的节点上留下
  真收据。那条路径由 `tests/test_daemon.py` 在临时 state 目录上驱动，C34/C35 两个变异覆盖。
- **ops 发送方还没有切到 `update`。** 本片交付的是「可以 patch 什么」与「记下是哪一条」；真正
  让告警卡从「每次新发」变成「patch 同一条」是 ops 那一侧的改动，属于 P8 的其余部分。
- 本机上仍然没有 `~/.serveros/business-calendar.yaml`、也没有 `~/.serveros/cards/day.json`
  （本片没有创建它们），因此 `card day` 在本机的默认答案仍是 `SKIP/CALENDAR_UNKNOWN`；
  `~/.serveros/` 下只有 `node.yaml` 与 `targets.yaml`。
- 仍未关闭（与前几格相同，本片一条也没有改变它们的状态）：P7 唤醒半的两件前置（§9.38.9）、
  `WAIT_KINDS` 仍只有 `child`/`window`、`NodeControlTransport.send` 不包裹 connector 异常、
  `drift_sync.py` 没有调度器、约 40 处未守护的考古站点、`supersede_checkpoint` 的退役评估、
  以及切片 F–N 的新模块在装好的节点上都还没有那一次 `build→promote→restart`（P12）。

## §9.47 切片 D：一个 episode 一张事故卡，跨小时也跨天

#### 9.47.1 靶子：V29 全句的主语是一个此前零读者的词

V29（`docs/MONITORING-ACCEPTANCE.md:106`）写的是「同episode跨小时/跨天只patch一张Incident
Card，不重复NEW」；`docs/MONITORING-PRODUCT-ARCHITECTURE.md:556–567` 把同一件事展开成一张表
（NEW 只发一次，UPDATED/ESCALATED/ACK/RECOVERED/CLOSED 都 patch），并给出**唯一**允许新建
的出口：「只有平台 patch 窗口到期且 episode 仍持续时，才创建 successor card，并保持同一个
episode continuity」。

§9.46 关掉的是 §11 这句话里**当天卡**那一半（一个工作日一张卡、记住承载它的那条消息）。事故
卡那一半此前一个落点也没有，原因是这句话的主语——`episode`——**全树没有一个读者**：

- `runtime/alert_digest.py:124` `EPISODE_FIELDS = ("first_seen", "resolved_at")`，`:734`
  `episode_of(row)` 用 `sha256(key|first_seen|resolved_at)[:16]` 造出它；
- `runtime/notification-contract.yaml:477–480` 与 `:501` 逐字写明 `episode: "alert_ref +
  first_seen + resolved_at"`、`receipt_field: last_payload`；
- 而全树**没有任何一处**把两条投递按 episode 比对过。台账（§9.46 交付）记得住「发了哪一条
  消息」，却记不住「这条消息是关于哪一起事故的」——于是 09:00 的那一拍只能看见「有一条卡发过
  了」，看不见「它就是 08:00 那条」，唯一能做的事就是再发一条。

本片补的是这半张：**回执记住它关于哪个 episode**，**判决回答这个 episode 下一次该做什么**，
并给「问一次」一个动词（`card incident`）。

#### 9.47.2 八处落点

| 落点 | 是什么 | 在哪 |
| --- | --- | --- |
| episode 是什么 | 非文本拒绝／strip／含空白拒绝；两句都不回显那个值 | `card_delivery.check_episode` |
| 哪些卡有 episode | `PER_EPISODE_KINDS = (ALERT, RECOVERY)`；**对称**两拒 | `card_delivery.record` 是唯一入口 |
| 回执留住它 | `episode` 进台账行（缺失的键保持缺失，不补一个） | `card_delivery.record` |
| 哪条消息承载它 | `CardLedger.carries(kind, episode)` | `card_delivery.CardLedger` |
| **共享判决** | `binding_verdict(binding, at, window_days)`：四个理由 | `card_delivery.binding_verdict` |
| 事故卡的答案 | 五拍：`NEW_MESSAGE·NEVER_SENT`／`PATCH_MESSAGE·IN_HAND`／`SUCCESSOR_MESSAGE·IDENTITY_EXPIRED`／两个 `IDENTITY_MISSING`·`IDENTITY_UNDATED` | `card_delivery.incident_verdict` |
| 两个命令 | `card record-delivery --episode`、`card deliveries` 每行印 episode、新命令 `card incident --kind --episode --at` | `runtime/cli.py` |
| socket 上一处 | op `card.incident`（ops 表与 `handler_map()` 清单**两处**都要写），`episode` 原样透传 | `runtime/daemon.py` |

#### 9.47.3 episode 的形态规则只写一处，而且和行写入侧是同一读法

`check_episode` 的四条：不是文本就拒（`str(b"0f3a…")` 是 `b'0f3a…'`，一个看起来良构、谁也
寻不到的值——正是 §9.46 里 `check_remote_id` 拦的那类静默失败）；`strip()`；strip 之后为空就
拒；含任何空白就拒。三句拒绝**都不回显那个值**：回显会把一个可能来自外部输入的东西复制进日志。

`record()` 是唯一入口，所以这条规则覆盖每一个调用方——包括守护进程（socket 上唯一的写入
者）。daemon 那边是 `episode=params.get("episode")`：原样转交，不在自己这里再判一次。

台账 `_load` 里还有一道**逐行**校验：`episode` 这一栏存在（哪怕是 `null`）就必须是一个合法
token，否则整份台账拒绝服务。理由是这一片才有的：**一行 episode 读不出来的记录，是任何查询
都找不到的记录**，而查询找不到它的后果，就是这个 episode 收到第二张卡。

一个刻意的非对称：**键缺失保持缺失**。§9.46 之前写下的行里没有 `episode` 这一栏，那不是
「格式错误」，是「那时候还没有这个词」——替历史行编一个 episode，会让一次真实发送变成一次
错误归因。

#### 9.47.4 对称的 kind 规则，以及为什么「status 必须不带 episode」不是整洁

两条规则成对：`alert`/`recovery` 的回执**必须**带 episode，`status` 的回执**必须不带**。

第一条是 V29 可判定的前提——没有它，台账说得出「哪张卡发出去了」，说不出「那就是这一拍的
同一起事故」。第二条不是对称的美学：`status` 卡是关于**一个工作日**的，它没有 episode；允许
它带一个，会让 `episode` 这个词同时命名两件东西，而读者只看得见拼写。用本片自己的说法：
**一个能表示「什么都不是」的词，就不再是一个名字。**

#### 9.47.5 `carries`：被拒的那次 patch 之后，还认不认得出那条消息

`carries(kind, episode)` 取的是该 episode **最后一个「指名了消息」的行**，而不是最后一行。
这个区别是在手工检查里被量出来的，它对应一条真实的误诊路径：

```text
08:00  DELIVERED  episode=…9c24  remote_id=om_hand_incident   ← 卡真的发出去了
09:00  FAILED     episode=…9c24  remote_id=None               ← 服务拒绝了这次 patch
```

「最后一行」会答「发出过卡、但回执丢了 id」（`IDENTITY_MISSING`），于是读者去修一张**完好
的**回执——而下一步就是给同一个 episode 发第二张卡。被拒的 patch 陈述的是**一次尝试**，不是
**哪条消息承载这个 episode**，所以它不作数；一条从没记下 id 的行（09:30 那一行）仍然是一行，
于是「记过、但没指名消息」与「什么都没记过」保持可分——这是三种状态，不是两种。

#### 9.47.6 一份判决、两句不同的话：`binding_verdict` 是唯一起草处

「承载它的消息还能不能改」是**一个**问题。§9.46 在 `card_day.message_verdict` 里答过一次，
事故卡要答的是同一个问题——两处实现会在窗口移动的那天第一次分歧，那时当天卡会承诺一次
transport 拒绝的 patch。

所以判决收敛到 `card_delivery.binding_verdict(binding, *, at, window_days)`，返回
`{action, reason, remote_id, window_days, age_days}`，四个理由 `IDENTITY_MISSING`／
`IDENTITY_UNDATED`／`IDENTITY_EXPIRED`／`IN_HAND`（只有最后一个给 `PATCH_MESSAGE`，其余
`NEW_MESSAGE`）。

**它不造句。** 句子留在两个调用方：当天卡说「承载当天卡的消息……」，事故卡说「承载这个 episode
的消息……」。两句都必须说，而且都必须不同——一句共用的话会在两处中的一处说错主语。`card_day`
也不再自己加载 `message_transport`：窗口与词汇都从 `card_delivery` 借（`_delivery()` 用的
是**同一个模块名**，所以是同一份对象，不是看着一样的第二份），`card_day` 自己的
`_transport()`／`_moment()` 删除。

#### 9.47.7 五种答案，两处拒绝而不是猜

`incident_verdict` 的五个出口：

| 台账里 | 答案 | 理由 |
| --- | --- | --- |
| 没有这个 episode 的任何行 | `NEW_MESSAGE` | `NEVER_SENT` |
| 有行、有 id、窗口内 | `PATCH_MESSAGE` | `IN_HAND` |
| 有行、有 id、超窗 | `SUCCESSOR_MESSAGE` | `IDENTITY_EXPIRED`（句子必须要求 continuity） |
| 有行、回执没记 id | `NEW_MESSAGE` | `IDENTITY_MISSING` |
| 有行、时刻读不出 | `NEW_MESSAGE` | `IDENTITY_UNDATED` |

两处**拒绝而不是猜**：`status` 这类关于工作日的卡族（判词会瞄准它先撞上的那一行），以及空的
episode（「那个 episode」不是一个 episode）。

它还**不回答「这件事值不值得说」**。V35 的抑制住在 `alert_digest.decision`，一个顺带决定相关性
的判决就是加在已有闸门前面的第二道闸。

超窗那一句是本片唯一的**允许行动**：`SUCCESSOR_MESSAGE` 不是 `NEW_MESSAGE` 的别名，两者的
区别就是那句话——新卡必须**显式标注**它接续的是同一个 episode。

#### 9.47.8 两条时钟（实测对照）

`message_transport.update` 拿**真机器时钟**量窗口（它真的要去改一条消息）；`binding_verdict`
与 `incident_verdict` 的两个时刻**都是参数**——它们是判断，同一份台账在不同时刻必须给出同一个
答案，而回答的是「这一拍」。`_moment` 对**不带时区**的值返回 `None` 而不是按 UTC 读：窗口宽
14 天，替一个没有时区的值假设一个，能在两个方向上把它挪掉将近一天，那就是「patch 一条消息」
与「拒绝 patch」的差别。

#### 9.47.9 手工跑过的真路径（读数）

六节，脚本在 `artifacts/tmp-p8/handcheck_slice_d.py`：

- **A 真 CLI 打真守护进程**（只读命令）。两条读数**不是**「命令能跑」：本机跑着的守护进程是
  切片 D 之前的 composition，它答 `UNKNOWN_METHOD` 并**逐条报出它服务的 61 个名字**——
  `card.record-delivery`／`card.deliveries` 在，`card.incident` 不在。rc=2，没有假装成功。
  同一个脚本读工作树源码：`"card.incident"` 出现 **2 处**（ops 表、随 socket 报出去的清单）。
  两边对得上，差的是那台机器上跑着的那一份——把切片 D 放上节点是 P12 的事。
- **A 的另两条是 parser 层**：`--kind status` 被拒（rc=2，usage 里 choices 只有 `alert`/
  `recovery`）、不带 `--episode` 被拒（rc=2）。两条都在**没有碰守护进程**的情况下发生。
- **B 六拍**：`PATCH_MESSAGE·IN_HAND`（0.3 天）→ `SUCCESSOR_MESSAGE·IDENTITY_EXPIRED`
  （40 天）→ `NEW_MESSAGE·IDENTITY_MISSING` → 第三条的时刻**晚于**这一拍，答
  `PATCH_MESSAGE·IN_HAND` 且句子里写明「记下的发出时间不早于这一拍」→ 手写进去的一行（见下）
  答 `NEW_MESSAGE·IDENTITY_UNDATED`：「无法判断就不声称可以 patch，也不据此改发一张新的」→
  一条谁也没发出过的 episode 答 `NEW_MESSAGE·NEVER_SENT`。
- **C `carries` 五行**：被拒的那一行（09:00 FAILED）存在时，`carries(…9c24)` 仍答 08:00 那条
  `om_hand_incident`；`carries` 对没有 id 的行返回该行本身（`remote_id=None`），对没有行的
  episode 返回 `None`——三种状态都打出来了。
- **D 同一份判决、两句不同的话**：同一 binding、同一时刻，当天卡说「承载**当天卡**的消息在
  手……」，事故卡说「承载**这个 episode** 的消息在手……」；`"当天卡" in …` 一句 True 一句
  False——这是「一份判决、两句话」的读数，不是修辞。
- **E 借来的词汇与借来的窗口**：七个词 `is` 同一个对象（True×7）；窗口两边都读同一个
  `message_transport`（`day._delivery() is cd` True）；两个动作集合的差集恰好是
  `{NO_MESSAGE}` 与 `{SUCCESSOR_MESSAGE}`，两个理由集合的差集恰好是 `{NOT_ROUTINE}`——**借
  来的只是词本身**，各自多出来的那个词各自说得出口。
- **F 没有在这里跑的命令**：`card record-delivery --episode` 走守护进程，本机此刻就有活着的
  守护进程，拿一张假卡去写会在一个活着的节点上留下**真收据**；那条路径由
  `tests/test_daemon.py` 在临时 state 目录上驱动（D32/D33/D34 三个变异覆盖）。

B 里那第五行是**手写进 JSON 的**：走 `record` 的调用方写的都是它自己那一刻（守护进程写
`_iso(self._now())`，CLI 写 `--at` 或同一个时刻），所以这一棵树里没有一条调用路径会产出
`at` 读不出来的行。`IDENTITY_UNDATED` 这个分支存在，是为了别人写的行——旧 schema、另一个
写入者、被手工改过的文件——而一个只在代码里存在的分支，正是那种「测试全绿、真到手上才发现」
的分支。所以手工检查把它真的造出来读了一次。

#### 9.47.10 本片新增的用例

**45** 条 `def test_`，删除 **0** 条，全在三个文件里：`test_card_delivery.py` +37、
`test_daemon.py` +5、`test_card_day.py` +3。`git diff --name-only -- tests/` 只有这三个文件。

承重的几条：

- `test_the_last_row_wins_only_when_it_names_a_message`——把那两行台账摆出来，钉住被拒的
  patch 不作数；
- `test_the_two_card_families_disagree_about_what_they_call_it`——同一份判决，两句话，主语不同；
- `test_the_judgment_about_the_message_is_the_ledgers_and_not_a_second_one`——把
  `card_delivery.binding_verdict` 换成一个返回凭空理由的桩，看 `card_day` 是否**拒绝**（它
  会 `raise CardDayError`），而不是把那个理由当成第四个自己认识的值；
- `test_the_shared_words_are_borrowed_rather_than_written_out_again`——读源码断言
  `f"{name} = _delivery().{name}"`，因为「借」这件事只有写法能证明；
- `test_the_module_it_reads_is_the_same_copy_the_cli_loads`——`assertIs(card_day._delivery(),
  card_delivery)`；
- `test_the_words_this_verdict_answers_in_are_the_ledgers_own`——七个词逐个 `assertEqual`，
  并断言两个集合的差集**恰好**是各自多出来的那个词；
- `test_a_row_whose_episode_cannot_be_read_stops_the_ledger`——一行坏 episode 让整份台账
  拒绝服务；
- `test_a_workdays_receipt_may_not_carry_an_episode`／`test_an_incident_receipt_must_name_one`
  ——对称的两拒；
- `test_a_verdict_about_no_episode_is_refused`——这条**本来就在**，本片把它改了：原来只断言
  「抛了 `CardDeliveryError`」，而 `carries` 也为空 episode 拒绝，于是断言在两层都能过（见
  9.47.11 的 D16）。现在它断言拒绝句里含 `verdict`，即**这一层**在说话。改在原有用例内部，
  不新增 `def test_`，所以上面 45 这个数不受影响。

约定沿用前几片：同一个文件用**同一个模块名** `_load(...)`；`test_card_day.py`／
`test_card_delivery.py` 用 `unittest.TestCase`，`test_daemon.py` 用 pytest + `daemon_factory`。

#### 9.47.11 变异电池：35 条，35 杀，0 存活，0 SKIP

覆盖四个实现文件（`card_delivery` D1–D22：episode 的形态与三种拒绝、`carries` 的两条取法、
共享判决的四个理由与窗口、事故判决的五个出口；`card_day` D23–D25：窗口不许写字面量、判决不许
自己下、借来的词不许抄写一遍；`cli` D26–D31：`--episode` 的透传与必填、印不印 episode 行、
动词与它的 choices；`daemon` D32–D35：透传、op 名、op 声明的两处清单）。

逐条锚点在文件里必须只出现一次（dry-run 验过 35/35 唯一，其中 D9 的缩进错在 dry-run 里当场
被抓住），`__pycache__` 每轮清一次（同尺寸同秒的还原会骗过 pyc 校验）。

首轮 **34 杀 / 1 存活**，0 SKIP。存活的那一条既不是冗余代码，也不是测试漏了行为，而是一次
**「两个层都会拒绝」**：

- **D16（`incident_verdict` 自己的守卫 `if not episode:` 去掉）**。它下面一行就是
  `ledger.carries(kind, episode)`，而 `carries` **同样**为空 episode 拒绝。所以这个变异在异常
  类型上**等价**：原来那条断言写的是 `with self.assertRaises(CardDeliveryError)`，两层都让
  它过。
- **不靠推理，量一次**：把 `runtime/` 复制到 artifacts 下的一份副本里、**只在那份副本上**做这
  个改动（工作树一个字节都没动），同一组输入（`""`、`"  "`、`None`）喂给两份模块：

  | 输入 | pristine | D16 |
  | --- | --- | --- |
  | `""` / `"  "` / `None` | `an incident verdict is about one episode, and this call named none: «the episode» is not an episode` | `which message carries an episode is a question about one episode, and this call named none` |

  两者都抛 `CardDeliveryError`——**差别是句子**，也就是**哪一层在说话**。
- 所以这条守卫不是死代码：它决定调用方读到的**诊断**。第一句说的是「**这次调用**错了」，
  第二句把读者送到台账去查一个**它没来得及问的**问题。原来的断言只看异常类型，于是「两层都
  拒」这件事让断言在两处都能过——与 §9.46 的 C33 是同一类错误：**断言钉错了对象**。
- 补的是同一条用例的**后半**（在原有测试内部加断言，不新增 `def test_`，所以 9.47.10 的计数
  不受影响）：拒绝句里必须含 `verdict`，三个输入逐个检查。子集重跑 **1/1 杀**。

**35 杀、0 存活、0 SKIP**，还原后四个文件与备份**逐字节相同**（`371adc1918e7`／
`a74c8140857d`／`01f395352805`／`a5ef3323d3d8`）。

#### 9.47.12 全量读数

```text
HEAD 75798c1  dirty 7   (工作树里只有本片这 7 个文件；台账这一节是跑完之后写的)
start 2026-09-26T12:30:31+08:00
rc=0 at 2026-09-26T12:41:28+08:00
6154 passed, 20 skipped, 1 warning, 4000 subtests passed in 653.43s (0:10:53)
dirty after 7  HEAD 75798c1

runtime/card_delivery.py        371adc1918e74594f2fb708d6d14f45acbb8c6761d6fe4f8924ae2dc9b33f2b6
runtime/card_day.py             a74c8140857d2fe010a0e933d033edd4d6d586ed07541a7423ce5cf1c357817b
runtime/cli.py                  01f395352805df88a009c2526ea5125af0c541b822d4718e6458787c5e216ef2
runtime/daemon.py               a5ef3323d3d815e8c4ea19ed964c05ff54f15495f5dbfbc9e72e7ead877061b3
tests/test_card_delivery.py     d9bff86baf5d5032c77adc6bbd6da767b03b74d71bbbf0c2916269af66f3161a
tests/test_card_day.py          383eb3d55c194db6737811b06bdc2a5a4e2491db3b96f88682c4076cf5a9e0c0
tests/test_daemon.py            5165938e667147dd1f45730ecdfa26007dd07274021268dd7af3eaf3ae851ba5
```

- **算术闭合**：上一格 6109 ＋ 本片净新增 **45** 条 `def test_`（`git diff -U0` 逐文件计数
  37/5/3，删除 0 条）＝ **6154** ✓。
- **subtests 仍是 4000**：本片新增的用例里 **0** 个 `subTest`（`git diff -U0 -- tests/ |
  grep -c subTest` = 0）。这个数**没有动**，与「上一格 3995 → 4000 的那 +5 有出处」是同一支
  量具量出来的。
- 本片**没有新增运行时模块**，所以那一支制品 import 计数应仍是 **114 个模块**——这一条是**推**
  的，不是量的（没有为它单独跑一次 `-k` 探针），按 §9.46 的口径记在这里。
- 全量那一跑是本片的**第三次运行**（手工检查、变异电池、全量），跑的是**最终字节**：D16 的补
  断言是在第一次全量跑完之后加的，所以 9.47.12 这一跑重跑了一次，上面 7 个 sha256 就是这一跑
  跑的字节。跑前跑后 7 个文件的 sha256 逐字节相同，HEAD 未变，dirty 7 → 7：这一跑没有写进任
  何东西。

#### 9.47.13 它没做什么，仍未关闭的

- **没有发送、没有改动任何一条消息。** 手工检查里没有一条命令会发出卡；通道那半边的真路径
  （`update`）本片一个字也没有改。
- **没有从本机真的调过 `card record-delivery --episode`。** 本机此刻有活着的守护进程，写进去
  会留下真收据；那条路径由 `tests/test_daemon.py` 在临时 state 目录上驱动。
- **没有改变本机上任何一条已写下的台账行。** 本片新增的 `episode` 栏对历史行保持缺失，`_load`
  也不会替它们补一个。
- **ops 发送方仍然没有切到 `update`，也仍然没有按 episode 查判决。** 本片交付的是「可判定的
  问题」与「问它的动词」；让告警卡真的从「每次新发」变成「patch 同一条」是 ops 那一侧的改动，
  属于 P8 的其余部分，而且**真实效果需要批准**。
- **`card.incident` 还没有在节点上**（读数见 9.47.9 A）：装好的 composition 里没有这个 op，
  那一次 `build→promote→restart` 仍是 P12。
- 仍未关闭（与前几格相同，本片一条也没有改变它们的状态）：P7 唤醒半的两件前置（§9.38.9）、
  `WAIT_KINDS` 仍只有 `child`/`window`、`NodeControlTransport.send` 不包裹 connector 异常、
  `drift_sync.py` 没有调度器、约 40 处未守护的考古站点、`supersede_checkpoint` 的退役评估、
  以及切片 F–N 的新模块在装好的节点上都还没有那一次 `build→promote→restart`（P12）。

## §9.48 P7 唤醒半边：接收器是量出来的，周期是推出来的

#### 9.48.1 靶子：`RECEIVERS` 空着，而「空」此前有两种读法

`runtime/continuation.py` 里有一个模块级名字 `RECEIVERS: tuple = ()`，它自己的 docstring 把
它叫作「the registry the wake half will fill」。本片之前它在本机一直空着，而**空有两种意
思**，此前没有任何东西能把它们分开：

- 这个节点上**没有**接收器（装机时没 enable 那个 routine）；
- 这个节点上**有**接收器，只是没有人去问过它（注册表从 import 起就是那个空元组）。

两者印的是同一句话：「… no continuation receiver is installed on this node, so nothing will
wake it」。第一句是**测量**，第二句是**没测**——而 §9.47.4 那一格刚说过：**一个能表示「什
么都不是」的词，就不再是一个名字。** 本片要交付的是把这两句拆开：**每一个回答这个问题的界
面，都在回答之前先量一次。**

#### 9.48.2 接收器是量出来的，不是声明出来的（`runtime/wake.py`）

新的 `runtime/wake.py` 是唤醒半边的全部。它答的第一个问题是「这个节点上到底有没有东西会去
唤醒一个等待」，而这个答案由**两半**组成，并且分开报：

| 字段 | 量什么 | 怎么量 |
|---|---|---|
| `armed` | timer 在不在 wanted set 里 | 读 `<unit_root>/timers.target.wants/serveros-continuation-wake.timer` 这条 symlink |
| `wired` | 它起来之后真的会去启动一个 Parent 吗 | 从渲染后的 `ExecStart=` 里读出 `--parent` 的值；没渲染的 `@…@` 占位符算「没有」 |
| `installed` | **两半都要** | `armed and wired` |

- **不问 `systemctl`。** 一次 oneshot 完全可能在**没有 bus** 的 manager 里跑，而这里问的是
  「它在不在 wanted set 里」——一个关于目录的事实。（这条在 fixture 里真的被踩到过：一条悬空
  的 symlink 也要算「在」，因为 systemd 的判据就是「文件在不在」。）
- **两半分开，是因为修法不同**：「timer 在、没有 launcher」与「有 launcher、timer 不在」是两
  种不同的修法。
- **不对称的方向是选过的**：少报一个接收器会让读者保守，多报一个会让 `continuation_state` 对
  一个永远不会唤醒任何东西的节点说 `RECEIVER_INSTALLED`——那正是 §17.4 禁止的那种句子。
- **`fill_registry()` 是 `RECEIVERS` 的唯一写入者**，而且是**赋值不是追加**：第二次调用反映
  的是**现在**装了哪些 unit，一个已经卸载的接收器不能继续被报成「有一个后台在跑」。它被*读
  者*（`work continuations`、`work checkpoint`）与 *routine 自己*（它就是这个答案）通过**同
  一个函数**调用——两边问的都是「这个节点上有没有东西会去唤醒一个 checkpoint」，而依赖谁问
  的答案就是两个答案。
- `tests/test_wake.py` 里有一条**扫源码**的用例：全仓对 `RECEIVERS` 的赋值只允许出现在
  `continuation.py` 与 `wake.py` 两处，第三处就是下一次漂移的起点。

#### 9.48.3 一次唤醒＝一次 `invoke_once`

唤醒的执行面没有新机制，它落到既有的 `runtime/bounded_invocation.invoke_once` 上：

- **key 是推出来的，不是收的**：`continuation-wake-<item>-e<epoch>-a<attempt>`。收一个调用方
  给的 key，会让同一 epoch 的两次唤醒都成为「第一次」。
- **一次评估九种出口**：七种 `REFUSE`（`SUPERSEDED`、`WAIT_UNANSWERABLE`、
  `NO_AUTHORIZING_GRANT`、`NO_ATTEMPT_BUDGET`/`NO_WALL_BUDGET`/`BUDGET_EXHAUSTED`、
  `WAKE_IN_FLIGHT`、`ATTEMPTS_EXHAUSTED`、`BRIEFING_OVER_BOUND`）、一种 `HOLD`
  （`WAIT_OPEN`）、一种 `WAKE`（`READY`）。这九种不能塌成一个「没醒」：等待还没结束与没有授
  权是两件事，而前者不该消耗任何东西。
- **一次唤醒的结果词只有四个**：`WOKEN` / `REPLAYED` / `REFUSED` / `NOT_STARTED`。
  `REPLAYED` 不是失败，也不是第二次启动，是 `invoke_once` 在说「这个 key 已经带着这份输
  入」——回执在做它唯一该做的事。
- **回执只声称它量到的东西**（实测读数）：`effect: UNVERIFIED`、`descendants_verified: false`、
  `direct_process_exited: true`、`worker_stopped: false`。它没有说 Parent 的**效果**被验证过
  ——那是 `work accept` 的事。
- **`STARTED` 残留要停下来，不能重放**：回执在 spawn **之前**写，所以残留的 `STARTED` 要么是
  正在跑、要么是写者中途死了；后者按模块自己的话「requires reconciliation, never blind
  replay」。
- **`--parent` 是一个可执行文件、不带参数**，并且安装器对不是路径的值直接拒：`ExecStart=` 由
  systemd 切开、这里的 `launcher_argv` 又读回来，一条要靠两个读者都读对的命令行，是总有一天
  会被其中一个读成别的样子的命令行。

#### 9.48.4 周期不是挑的，是推出来的（本片承重的那个数字）

这是整片里唯一一处「改一个数字」的改动，而那个数字是**算出来的**：

- 唤醒花的授权是一份 **delegation**，而一份 delegation 只在它的可信 context 仍然 FRESH 时授
  权：`context/actor-context-contract.yaml` 是 `stale_after_seconds: 300` /
  `window_seconds: 900`，`context/delegation.derivation` 在**读的那一刻**把存下来的 context
  与那次 tick 的时刻相比——**从不读存下来的判决**。
- 因此一个**周期 ≥ 那个阈值**的接收器不是「慢一点的接收器」，而是**结构上无法触发**的那一
  种：任何在委托最后一次被观察到之后、超过一个周期才结束的等待，唯一能唤醒它的那一拍到达
  时，context 已经 STALE，于是**按名字被拒**——`DELEGATION_DOES_NOT_AUTHORIZE`，而它文档里
  的修法是「刷新上下文」，也就是**要有人再一次被观察到**——那正是接收器存在的意义所在（不
  用人）。**五分钟恰好等于那个阈值**，所以一个五分钟的接收器是「装了、武装了、结构上醒不
  了」。
- 这是**实测**的，不是推演的：本机上老制式（`*-*-* *:1/5:00`）的两拍——21:41 与 21:46——各自到
  达时**等待都已经结束了**（那个 checkpoint 的等待在更早的一轮里就闭合了），两拍都给出
  `REFUSE / NO_AUTHORIZING_GRANT`。原因就是上面那条算术：它们要花的那份委托，上下文最后一次
  被观察到是 13:31:00Z，21:41 那一拍读到 600 秒、21:46 那一拍读到 900 秒——**两拍都过了 300 秒
  的阈值**。用一个更贴切的说法：那两拍是「按计划到达、按名字被拒、什么也没唤醒」的实物。
- 新制式 `*-*-* *:*:20` ＋ `AccuracySec=30s`：**周期 + 精度 = 90 < 300**，可捕获的窗口是
  `300 − (60 + 30) = 210` 秒，而从「child 的结果到达」到「Parent 被重新启动」的**最坏延迟就
  是那 90 秒**。这不是一个队列的轮询间隔，因为**没有队列**：一拍在它跑的那一刻用准入 store
  求值，所以一次早到的 tick 只花一次 store 读，加上每份活 checkpoint 一次回执读。
- **`:20` 也不是口味**，是同机上各 timer 相位的**读数**。这里有一处**订正**，见 9.48.11。
  按 2026-09-26 的实测：`hwz-monitor.timer` 与 `tokyo-status-card.timer` 是 `*-*-* *:00:00`，
  `botmux-watchdog.timer` 是 `*-*-* *:2/5:00`，`tokyo-alert-digest.timer` 是 `*-*-* *:03/5`，
  `serveros-daily.timer` 是 `*-*-* 08:17:00`（已装、未 enable），而
  `runtime/serveros-alert-sync.timer` 的 `*-*-* *:4/5:00` 是**仓库模板、本机没有装**。理由不是
  对称：这个 unit **会启动一个 Parent**，而一个在监控采样窗里启动的 Parent 会把它自己与它的子
  进程放进那个监控正在量的数字里——而那个监控是人要读的东西。
- 这三条算术**钉在测试里**而不是留在注释里：`period + accuracy < stale_after_seconds`、
  `stale − (period + accuracy) >= period`，并且把**本机装过的那条老表达式**当作被拒的对照
  组。一条被改慢的 timer 会**按名字**挂掉那个测试，消息里写出是哪两个数字不合。
- `Persistent=false` 保持不变：持久的是**checkpoint 与回执**，不是 timer。一次漏掉的 tick 救
  不了任何东西（等待结束这件事不会因为没人看而消失，下一拍照常会唤醒它），而一个每分钟且带
  catch-up 的 unit 只会在每次开机时补出一串多余的 attempt。

#### 9.48.5 三处真缺陷（本片的重心，全部是实测发现的）

**(a) 一个自己能写出、却读不回来的 key。** `action_key` 拼出来的名字，与 `attempts_recorded`
用 `ATTEMPT_KEY` 解析回来的，必须互为逆。解析器最初写作 `[A-Za-z0-9_]+`，**读不回**
`SRV-0001` 这种形状——而这个产品里每一个 Item 都是这种形状。后果不是报错，是**静默**：回执
永远不会被数到，于是 attempt 恒为 1，而 `invoke_once` 对同一个 key 给出 `REPLAYED`——模块会
报「已经唤醒了」，而实际上什么都没有跑。修法是把 `ATTEMPT_KEY` 右锚定且贪婪
（`^continuation-wake-(?P<item>.+)-e(?P<epoch>\d+)-a(?P<attempt>\d+)$`），并在 `action_key`
里把**往返本身**断言出来：写得出却读不回的名字，是一个永远找不到回执的名字。

**(b) 一个答案有三个生产者。** `daemon.default_state_dir()` 自己读 `SERVEROS_STATE_DIR`，
`cli._agent_control_store_path()` 读路径合同、从不看那个变量。在不导出变量的节点上两者恰好落
在同一个目录，于是不可见——**但在导出变量的节点上这份不可见性就没了**，而每一个
`scripts/release.py continuation-wake --state-dir <path>` 装过的节点都导出它（渲染出来的
unit 里带着 `Environment=SERVEROS_STATE_DIR=<path>`）。那样一个节点上，守护进程写一个 store、
每个 verb 读另一个，两个都答得像另一个不存在。**发现方式**：拿一个显式 state root 装唤醒
routine，然后看着接收器读到一个空 store。修法是让 `registry.state_root()` 成为唯一生产者，
`daemon` 与 `cli` 都改调它；`tests/test_control_store_path.py` 把「两个组成落成同一个文件」
当作等式断言，并额外钉住「变量不导出时什么都没动」——那是这个修法敢在活节点上做的唯一理由。

**(c) 一个装了接收器的节点上，由一个刚被唤醒的 Parent 印出「没有任何东西会唤醒你」。** 这是
本片最重要的一处。`RECEIVERS` 只由 `wake.fill_registry` 填，而 `cmd_work_checkpoint` 没有调
它，于是它从 import 期的空元组作答——**一次对什么都没有的测量，读起来像「未接线」**。同一时
刻、同一个节点上，`work continuations` 印的是 `RECEIVER_INSTALLED`。发现方式值得记下来：**读
到它的是那个刚被这个接收器启动的 Parent 自己的输出**——正好是最要紧的那个读者。修法是在每个
回答这个问题的动词里做**同一次测量**，并把结果作为 `receivers_seen` 参数传下去；记录里仍然
**不许**出现接收器字段（参数来自读的一侧）。变异验证：把 `fill_registry` 调用临时换成
`pass  # MUTATED`，`equipped=True` 那一格给出 `AWAITING_CONTINUATION`——**与生产上看到的逐字
相同**——而两格对照仍全过；还原后 59 passed。

#### 9.48.6 真实正向实测：两次完整序列

用户对这一片的要求是逐字的：**「至少真实跑一次：Parent 退出 → child 完成 → 用户不发第二条消
息 → successor Parent 自动恢复并继续。没有正向实测，不算 P7 完成。」** 两次都跑了，**同一个
场景，一次没改、一次改了**：

| | run 1（候选 `…c@76a1e6c`） | run 2（候选 `…d@ebbec8c`） |
|---|---|---|
| seed：上下文被观察到、checkpoint 落盘 | 21:46:38 | 21:52:12 |
| tick A：child 还没结束 | 21:47:37 `HOLD / WAIT_OPEN` | 21:52:47 `HOLD / WAIT_OPEN` |
| child 真的结束（真实动词 `work retire`） | 21:47:59 | 21:53:13 |
| tick B：**此后没有任何人的动作** | 21:48:47 `WAKE / READY` | 21:53:24 `WAKE / READY` |

两次 tick A 都**不产生任何回执、不花掉任何 attempt**——等待还没结束的那一拍什么都不做。两次
tick B 都真的启动了 successor Parent，回执都是 `EXITED exit 0`：

```text
run 1  started_at 2026-09-26T13:48:47.485777+00:00  finished_at 2026-09-26T13:48:48.307395+00:00
run 2  started_at 2026-09-26T13:53:23.485705+00:00  finished_at 2026-09-26T13:53:24.332936+00:00
```

两者的 `input_digest` 不同（`4c17701e…` / `21e45397…`），因为 briefing 带着时刻——**同一份回执
不会被两次不同的唤醒共用**。Parent 的替身把它的证据写进了归档：`briefing.txt`（2506 字节，
也正好是回执里 `stdin` 的 2506 字节）、`authority.json`、`continuation.md`、
`checkpoint-epoch-2.json`、`started-by.txt`（`ppid 3558367`、`journal_stream 8:2561682877`、
`parent_exe` 是那条**真实的** `serverosctl work wake --parent …` 命令行）。epoch 2 在
21:48:48.290182（北京时间）落盘，store 里 epoch 1 变成 `SUPERSEDED — superseded at … by
SRV-0001#2`。

**两次的差别正是 §9.48.5(c) 那个缺陷**：同一个场景、同一个动词，Parent 自己印出来的续接状
态，从 run 1 的

```text
continuation  AWAITING_CONTINUATION
  … no continuation receiver is installed on this node, so nothing will wake it
```

变成 run 2 的

```text
continuation  RECEIVER_INSTALLED
  routine:serveros-continuation-wake
```

也就是说：**真的有一个接收器把它唤醒了，而它在自己的记录里此前不承认这一点。**

时间都落在 300 秒的 FRESH 窗口内：委托被观察到，到它被用来唤醒，中间隔了 **129 秒**（run 1：
13:46:38Z → 13:48:47Z）与 **71 秒**（run 2：13:52:12Z → 13:53:23Z）；child 结束到唤醒隔了 48 秒
（run 1）与 11 秒（run 2）。

**把老制式代进去，这两个数都不能说明它行。** 五分钟的一拍在 21:51:00 与 21:56:00：run 1 的等待
在 21:47:59 结束，能唤醒它的那一拍是 21:51:00，那时 context 已经 264 秒——**只剩 36 秒的余量**；
run 2 的那一拍是 21:56:00、age 228 秒，余量 72 秒。也就是说老制式在这两次上会**靠运气赢**，而
运气不是设计：等待结束得再晚 37 秒（run 1），唯一候选的那一拍就会带着 301 秒的 context 到达，
然后**按名字被拒**。这不是推演——21:41 与 21:46 那两拍就是同一件事的实物：等待已经结束、一拍
按计划到达、`REFUSE / NO_AUTHORIZING_GRANT`。**接收器武装着，而它结构上醒不了。**

#### 9.48.7 隔离与拆除

- 现场是一个 scratch 的 runtime root ＋ scratch 的 state root，**唯一的例外是真实
  `~/.config/systemd/user`**——systemd 的 user manager 只读那里，所以「真的被一个 timer 唤
  醒」这件事没法在别处证明。写进去的只有那两个 unit 文件。
- 收尾已执行：`disable --now`、删掉那两个文件、`daemon-reload`。wanted set 已回到
  `tokyo-alert-digest.timer` + `tokyo-status-card.timer`，manager 里已经没有那个 unit，而**节
  点自己的测量随之如实变回**「receivers none installed — a saved checkpoint is not being woken
  by anything on this node」。这最后一句是本片最想留下的性质：**拆掉之后读数变了**，说明读数
  是活的。
- 没有把任何东西长期留在本机（那一次安装属于 P12/R6）；**没有新增
  `continuationd`/`workflowd`/`parentd`**；没有把 tick 接进守护进程（`runtime/daemon.py` 里仍
  然没有对 admissions/checkpoint 的读取，有测试扫源码钉住这一点）。

#### 9.48.8 本片没说什么

- **接收器不做业务规划。** 它只搬运一份**人已经做出的**委托：`work wake` 在跑的那一刻重核
  context，把判决写进 briefing，再启动一个**自己会再重核一遍**的 Parent。全仓只有
  `mcp/human_control.py#attach_context` 一处铸 `context_ref`，守护进程根本没有 `report` 参
  数——**Authority is moved, never created.** 接收器没有自己的 context，也不铸一个；它只负责
  唤醒。
- **长期授权不能越过委托的 FRESH 窗口。** 那条不变式是既有的（授权自己的 `expires_at` 管不
  了它的委托上下文还活着多久），本片一个字也没有动它；本片动的是**周期**。
- **唤醒仍然只在委托还新鲜时发生。** 人离开之后，等待仍然如实显示「待续」，而
  `DELEGATION_DOES_NOT_AUTHORIZE` 的修法仍然是「刷新上下文」——那是设计，不是缺口。
- 仍未关闭（与前几格相同，本片一条也没有改变它们的状态）：P7 唤醒半的两件前置（§9.38.9）、
  `WAIT_KINDS` 仍只有 `child`/`window`、`NodeControlTransport.send` 不包裹 connector 异常、
  `drift_sync.py` 没有调度器、约 40 处未守护的考古站点、`supersede_checkpoint` 的退役评估、
  以及切片 F–N 的新模块在装好的节点上都还没有那一次 `build→promote→restart`（P12）——**本片
  把 `runtime/wake.py` 也加进了这一串**：它在候选 root 上被真实跑过、真的唤醒过一次，但还没
  有进到节点上装着的那个 composition 里。

#### 9.48.9 本片新增的用例

- `tests/test_wake.py`：**新文件**，54 条 `def test_`，9 个类。每一个类钉的是一条判据而不是
  一个函数：

| 类 | 钉住的 |
|---|---|
| `TestAReceiverIsMeasured` | 两半分开报；未渲染的 `@…@` 占位符不算 launcher；「什么都没装」读成「什么都没装」 |
| `TestItRefusesMoreThanItWakes` | 九种出口的**闭集**，以及 `WAIT_OPEN` 是**挂起**而不是拒绝 |
| `TestTheBoundIsTheGrants` | 每一种授权/预算缺失各自是一个理由，含「一个布尔不是预算」 |
| `TestOneAttemptIsOneStart` | 回执是唯一的计数来源；`STARTED` 残留停下来；**key 的往返**（9.48.5(a)） |
| `TestTheBriefingCarriesAndDoesNotDecide` | briefing 的四处「自己再核一遍」；它**不说**工作已完成、不说范围更大；超界**拒绝**而不是截断 |
| `TestTheRoutineIsNotATick` | 守护进程的 tick 仍然不读 checkpoint；唤醒半边只能从自己的入口进 |
| `TestTheVerbDecidesAndReports` | `--dry-run` 什么都不启动；相对的 `--parent` 在计划之前就被拒 |
| `TestTheCadenceFitsInsideTheAuthorityItSpends` | 9.48.4 的三条**算术**，并把老表达式当被拒的对照组 |
| `TestEverySurfaceAnswersTheReceiverQuestionFromOneReading` | 9.48.5(c)：两个动词必须给**同一个**答案；未接线的节点仍印产品自己的字面量；记录里**不许**出现接收器字段 |

- `tests/test_control_store_path.py`：**新文件**，6 条，2 个类。`TestOneStateRoot` 把「两个组成
  落成同一个文件」当作**等式**断言（变量设与不设两格），`TestTheUnitTheInstallerRendersActually
  RedirectsIt` 把「unit 渲染出来的那个变量确实指向 verb 读的那个文件」钉住——这是 9.48.5(b)
  那个缺陷的后果，写在运维会看见的地方。
- `tests/test_continuation.py`：**净 0**（删 2 增 2）。那两条原本写的是「this release has no
  receiver installed」，本片把它们的**理由**换掉了：注册表有写入者了，而一个什么都没装的节点
  上它仍然是空的。改的是理由，不是判据——因为一条「这个 release 没有唤醒半边」的断言，在唤
  醒半边存在之后就不再是一句话了。
- **全片净新增 `def test_` 60 条**（新增 62、删除 2），**0 条 `subTest`**（`git diff -U0`）。

#### 9.48.10 全量读数（两跑：那一跑抓到的两处声明面，与修好后的这一跑）

本片第一次全量跑出了 **2 处失败**，而两处都是**声明面**——即「产品长了一样东西，而某个登记
表还不知道」。修法是**申报**，不是放宽：

1. `tests/test_core_freeze.py::TheResidentSurfaceIsClosed::test_the_tree_declares_exactly_these_units`
   —— 树上多了两个 unit 文件，而 `RESIDENT_UNITS | NON_RESIDENT_UNITS` 里没有它们。修法是给唤
   醒 routine 一个**自己的集合** `WAKE_UNITS`（连同一段说明：前两组一个「发送」、一个「写目
   录」，而这一组**启动一个 Parent**，是三者中唯一花授权的那一个），并把它并进 `NON_RESIDENT_
   UNITS`。并进来之后，那条「每一个其它 unit 都证明不是常驻的」的用例**自动开始检查它们**：
   `.timer` 不许有 `ExecStart=`、必须写 `Unit=`；`.service` 必须是 `Type=oneshot` 且没有
   `Restart=`。
2. `tests/test_mutation_guard.py::TheLiveTreeIsCovered::test_every_product_call_site_is_declared`
   —— 新的 undeclared 恰好一条：`scripts/release.py:1279 exec systemctl cmd_continuation_wake`。
   修法是把 `cmd_continuation_wake` 加进 `release-promotes-and-rolls-back-units` 的 `sites`，并
   把那段 `why` 从「三条子命令」改写成「四条」——因为在合同里，**一句写错条数的 why 是一句会
   被下一个人照着信的 why**。改写顺带把两件运维必须知道的事写进了合同：`daemon-reload` 是**重
   读不是重启**；`--no-reload` **不是** dry-run，unit 照样写下去，只是 manager 还没看见它。

修好后的这一跑（**最终字节**）：

```text
6219 passed, 20 skipped, 4005 subtests passed in 659.45s (0:10:59)
rc=0
```

- 与上一格（§9.47 记的 6154 passed）相比，通过数增加了 **65**：**60 条新 `def test_`**（新增
  62、删除 2 的净数）加上 `test_the_key_reads_back_into_the_attempt_it_names` 的参数化展开
  （5 个 Item 名）。6154 ＋ 60 ＋ 5 ＝ 6219 ——**这个加法闭合**，不是估的。
- **subtests：本片自己的两跑是闭合的，跨格的那 1 不闭合，如实标出。** 本片第一次全量（unit 集
  合还没改、`test_core_freeze.py` 一个循环都没动）读到 **4001**；加上两个新 unit 分别进两个
  `subTest(unit=rel)` 循环，正好 **＋4**，于是这一跑 4005 —— 这一半是**量出来的加法**。
  但 §9.47 记的是 4000，而它的树与被改文件集合与这里相同；同一棵树读出不同的数，说明**这两格
  不是同一支量具量出来的**。本片按自己量到的记，并让 passed/skipped 承担跨格比较（它们在上面
  是闭合的）。
- 这一跑跑的是**最终字节**：台账本节（含 `WAKE_UNITS`、actuator 合同的第四条）与
  `runtime/wake.py` 一起，就是 `rc=0` 覆盖的那棵树。

#### 9.48.11 一处订正：一句自称「读数」的注释，其中一行不是读数

本片在做 R2 的现场核对时，回头读了本片自己刚发出去的那个 timer 注释，发现它的相位表里有一行
**不是这台机器上的文件**。2026-09-26 的实测：

- `~/.config/systemd/user/` 里**没有** `serveros-alert-sync.service` / `.timer`；
  `systemctl --user list-unit-files 'serveros*'` 只列出 `serveros-daily.{service,timer}` 与
  `serverosd.service`，wanted set 里只有 `tokyo-alert-digest.timer` 与 `tokyo-status-card.timer`。
- `runtime/serveros-alert-sync.timer`（仓库模板，1911 字节）确实写着 `*-*-* *:4/5:00`——所以那一
  行是**模板的**相位，不是**装好的**相位。
- 另外两行被本片写成了规范化的形式（`*:02/5:00`、`*:04/5:00`），而文件里的**字面量**是
  `*:2/5:00` 与 `*:4/5:00`。数值等价，但「读数」这个词管的是字面量。
- 表里还漏了一个**已装**的 timer：`serveros-daily.timer` 的 `*-*-* 08:17:00`（已装、未 enable）。

修法：注释改成逐行写出字面量并标注每一项来自哪个文件；未安装的那一个**明确标成模板，并说明为
什么仍然列它**——按已装集合挑偏移，会在它被装上的那一天撞车，而那一天正是 `release
alert-worker` 存在的理由。台账这一节上方的相位表同步订正。

**这条订正改变了什么、没改变什么。** `OnCalendar=*-*-* *:*:20` 与 9.48.4 那一整套算术**一个字
也没有改**，所以本片实测过的性质仍然成立；改的是一段论证的**前提**里不该出现的那个词——「装好
的文件」。一个说自己是读数的句子，读者的信任就是从那里来的。

## §9.49 R2 现场核对：两处「实现没有落在它自己写的意图上」

R2 是「P8 接到真正 live sender」。按 §1 的切换（integration → consumer → install → journey），
动手之前先把**发布面与现场**量清楚，因为这一段要碰的是真机上的生产点。量出来的第一件事不是
「可以开工」，而是两个缺陷——两个都不是设计问题，是**产品没有落进它自己写下的那句话里**。

#### 9.49.1 开工前量到的现场（都是测量，不是推断）

| 事实 | 值 | 怎么量的 |
| --- | --- | --- |
| worktree HEAD | `1cbdd83` | `git rev-parse HEAD` |
| `origin/main` | `1cbdd83` | `git rev-parse origin/main` |
| 正在跑的安装版本 | `alert-card-scope-20260924@8ce6fe7` | `runtime-assets/hwz-server-os/serveros-linux-work/current` 那条软链 |
| 两者关系 | `merge-base = c5d118c`；分支独有提交 2 个（`dbc9144`、`8ce6fe7`） | `git merge-base` / `git log --not` |
| 分支是否带来 HEAD 没有的内容 | **没有**：`git diff --name-status HEAD 8ce6fe7` 中 `^A` 计数 **0**；`plugin/profiles/linux-work.yaml` 只差 cachebuster `24c`→`24d`（HEAD 是超集） | 逐文件 diff |
| 守护进程自报 | socket `/run/user/1002/serveros/serveros.sock`，`state_dir /data/home/vince/state/serveros/runtime` | `serverosctl status --json`（装好的那一版） |
| 投递台账 | `<state>/runtime/cards/delivery.json`，`entries 20`，最新一条 `2026-09-26T13:00:44Z`（北京 21:00）、`kind: status`、`idempotency_key: tokyo-monitor-2026092621`、`payload_bytes: 3658` | 直接读该文件 |
| `~/.serveros/` 内容 | 只有 `node.yaml`、`targets.yaml` | `ls -a` |

最后一行值得单独看一眼：**台账已经在写，而写它的是 ops 那个老发送方**（`tokyo-monitor-*` 的
幂等键）。也就是说「谁在投递」这一跳此刻是**旧发送方 → 新台账**，这正是 R2 要收敛的那个缝。

#### 9.49.2 缺陷一：当天卡的两个默认落点都错（一个没申报、一个在明令不放大状态的 root 里）

`runtime/card_day.py` 的类文档写这张卡在 delivery ledger 旁边，而实现这句话语义的 `day_path()`
**没有任何生产调用者**；默认值是一个常量 `~/.serveros/cards/day.json`。三处独立的反证：

1. `fleet/node-root-contract.yaml` 声明那个 root 只放三个文件，并明写 "Large state does not live
   here" 与 "every file in it can be deleted and re-derived"。当天卡**恰恰不可重新推导**——丢了它
   就是造出 V28 禁止的第二张卡。所以这条路径把一个不可推导的东西放进了「什么都可以删」的目录。
2. 它实际落在 `<state>/runtime/cards/`，而这个 namespace 在**两份 authority 里都 grep 不到**，
   可 live 已经在写 `delivery.json`（上表）。**一个生产者在写一个没有 authority 说的 namespace。**
3. `card_source.DEFAULT_DAY_STORE_PATH` 是同一事实的第二份拷贝，`serverosctl` 四处
   `--day-store` 的 help 印的是那个假地址。

修法用本仓自己的 CONTRACT_GAP 教义「a ruling that widens it, not a move」：`state-root-contract`
的 `runtime.children` 加 `cards:`（producer 记 `runtime/card_delivery.py`——`card_day.day_path` 是
同一个函数的同一种形状，写两个文件名的 producer 会解析不到任何东西），`paths.py` 的
`STATE_SERVEROS_RUNTIME` 加 `"cards"`，`card_day.default_path()` 由**同一条生产者**
`registry.state_root()` 推出的**函数**，`card_source` 的那个常量改为该函数的别名（`is`，不是
`==`），四处 help 改成真话。

判据：`tests/test_card_day.py` 三条——默认路径必须等于
`card_delivery.ledger_path(register).parent / "day.json"`、必须随 `SERVEROS_STATE_DIR` 变、
渲染器与写卡器必须是同一个答案。**实测**（worktree 真解释器）：带与不带
`SERVEROS_STATE_DIR` 两种环境 `card day --json` 都答
`store: /data/home/vince/state/serveros/runtime/cards/day.json`，与守护进程自报的 `state_dir`
同目录——「siblingship」在真机上成立。

#### 9.49.3 缺陷二：日历的默认落点被本机自己的 `path.admit` 按名拒绝（实测）

```
PathRefused: '/data/home/vince/.serveros/business-calendar.yaml':
`~/.serveros` holds identity and binding only (§41). Allowed:
['node.yaml', 'targets.yaml', 'controller.yaml'].
```

这是**守护进程**对 `path.admit` 的回答，而同一个产品另一头
`serverosctl card day --business-calendar` 的 help 印的正是
`(default ~/.serveros/business-calendar.yaml)`。**印出来的补救办法是产品自己拒绝的**：操作者照
写，这个 root 里就躺着一个合同说不存在的文件。同一类还有第二处——`~/.serveros/service-bindings.yaml`
（`card_source` 读的投递绑定）。`command grep -r "Path.home()" runtime/` 里只有这两处，都由 card
子系统引入。

修法**不是搬走，也不是把 `files:` 加宽**：`files:` 的注释明写它是一条声明「node.py 打开这个
文件」，加一个别的模块读的名字就是把那条声明变成假的。修法是**加第二个 key**，各自说各能守住的
话——`fleet/node-root-contract.yaml` 新增 `declarations:`（每条带 `opened_by:` 与
`absent_means:`），`paths.py` 把一条元组拆成 `HOME_DOT_SERVEROS_FILES` 与
`HOME_DOT_SERVEROS_DECLARATIONS`、`ALLOWED` 为两者之和，`conformance.declaration_conflicts`
改成拿**两份 key 的并集**比对 `admit()` 真正读的那条元组（按 key 分别比会把一份完全正确的合同
报成冲突，而决定一次写入成不成功的是并集）。

**这条修法与两处会挡它的条文正面相对，所以写在合同里：** §41 的「bindings only」与
`admit()` 拒绝语里的「move it rather than widen this list」管的是**机器状态**漂进这个 root，而
这两份都不是状态；两者都**只对本机成立**（管辖域、时区、`fallback.declared_by:` 点名是谁定的、
投递目标），所以「放进仓库」也不可用——仓库里的一份副本对两台机器不可能是同一个答案。它仍然过
`never_in_this_root` 全部五项与 `rules:` 全部四条，包括「删掉这个目录只丢一个本机绑定，且没有
不可重新推导的东西」。

**实测行为中性：** `architecture check --json` 与装好的那一版**逐条相同**（6 条，id 一一对应：
`BOOTSTRAP_ABILITY_REGISTERED`、`TRANSITIONAL_GENERATED_SURFACE`、`UNREGISTERED_DATA_ROOT`、
`MAP_VIEW_UNBOUND`×3）。它走的是 `skills/serveros/scripts/architecture_check.py`，全文不读这套
词表（`command grep` 0 命中）；`conformance.collect` 只在测试里被调，本机没有动词暴露它。
`path.admit` 的实际变化要等下一次 promote。

#### 9.49.4 这两条修了什么、没修什么

- 修了：当天卡的落点有 authority 且与台账同目录；日历与投递绑定两个默认落点**从「被拒绝」变成
  「被声明」**，因此 9.44/9.46/9.47 反复写下的那句「本机没有 `~/.serveros/business-calendar.yaml`」
  终于**有了一个合法的地方可以放**。
- **没修**：日历内容仍然是缺的。节点上仍然没有那份文件，`card day` 仍然答
  `SKIP CALENDAR_UNKNOWN` / `day_kind: UNKNOWN`——`9.44.7` 那条裁定继续生效：导入要的是官方
  通知的 revision 与发布日，**那是人的输入，不替它编一个**。现在不同的是：这份缺口有了合法的
  闭合路径，而不是一句指向被拒绝路径的提示。
- **没修**：九个 card 动词在装好的节点上仍然只有五个（`record-delivery` 缺
  `--message-id`/`--episode`），仍等 R6 的 `build→promote→restart`；ops 两个发送方仍未被改写，
  仍是**单一 sender owner**（老的 tokyo 那一个），因此**没有**出现「老的也在发、新的也在发」。
- 仍未关闭（与前几格相同）：P7 唤醒半的两件前置（§9.38.9）、`WAIT_KINDS` 仍只有
  `child`/`window`、`NodeControlTransport.send` 不包裹 connector 异常、`drift_sync.py` 没有调
  度器、约 40 处未守护的考古站点、`supersede_checkpoint` 的退役评估。

## §9.50 P12 的载体半边：那句错话从「装在机上」量到 0

§9 那张表里的 `P12` 行量的是同一个缺陷的两个落点，处置写的是
`build→promote→restart（不手改派生安装）`。这一格记的是：**那条处置已经被执行，
而且执行的结果第一次是量出来的，不是推出来的**——在此之前只有「canonical 改对了」
这个仓库级的读数，没有载体级的读数。

#### 9.50.1 现场

| 事实 | 值 | 怎么量的 |
| --- | --- | --- |
| worktree HEAD | `512f886` | `git rev-parse HEAD` |
| `origin/main` | `512f886` | `git rev-parse origin/main`；工作树 0 porcelain（**02:00 那一刻**，见 §9.50.6） |
| 启用的 ServerOS composition | `hwz-server-os-linux-work@…` 与 `serveros-testing@…` 两条 | `settings.json` 的 `enabledPlugins` |
| 已装（linux-work） | `3.1.0+srv0003.20260927a`，`lastUpdated 2026-09-26T16:11:47Z`（北京 09-27 00:11） | `installed_plugins.json` |
| 已装（testing） | `1.0.0+srvtest0001.20260927a`，`lastUpdated 2026-09-26T16:33:29Z`（北京 09-27 00:33） | 同上 |
| promote 落点 | `serveros-linux-work/current -> revisions/serveros-completion-20260925@50a86fa`；`serveros-testing/current -> …@512f886` | 读两条软链 |
| 守护进程 | PID 3179306，跑的是 `serveros-linux-work/current/bin/serverosd`，每 tick `4 observed, 0 drift` | `systemctl --user status serverosd.service` |

`27a` 这两个字符串单独看只是一次改名，所以「promote 与 install 真的发生了」要单独说：
改名把 `claude plugin update` 的版本比较从「未变即已是最新」变成一次真更新，而
**那一次更新确实落了盘**（上表 `lastUpdated` 两条）。

#### 9.50.2 量法：把仓内自己的规则指向**装好的载荷**，而不是指向仓库

`tests/test_no_node_absolute_paths.py` 的 docstring 把范围写死成 `skills/`，并逐条说明
`runtime/*.py`、`context/`、`abilities/*.yaml` 为什么在范围外——它们带着考古，而考古是代码的
论据，重写它就是删证据。**那份范围说的是仓库。** 而 P12 说的缺陷发生在**交付物**上：一个会话
读到的不是本仓的 `skills/`，是插件缓存里那份装好的 `skills/`；`serveros-linux-work` 的四条
Skill 还是从 `ability-management` 在 `--ability-rev` 上取来的。

于是量具这样接：**同一份 `NODE_PATH` 正则、同一份 `SCANNED_SUFFIXES`、同一份 `node-path-ok`
豁免**（`from test_no_node_absolute_paths import …`），只把根换成两个已装载荷的 `skills/`。
没有写第二条正则——两份规则会漂移，而漂移的那份没人读。

#### 9.50.3 实测

| 载荷 | 装在机上？ | 读到的文件 | 违规 |
| --- | --- | --- | --- |
| `hwz-server-os-linux-work/3.1.0-srv0003.20260927a` | **是，ENABLED** | 26 | **0** |
| `serveros-testing/1.0.0-srvtest0001.20260927a` | **是，ENABLED** | 2 | **0** |
| `hwz-server-os-linux-work/3.1.0-srv0003.20260923d`（上一版） | 否，已被取代 | 26 | **13** |
| `serveros-testing/1.0.0-srvtest0001.20260922b`（上一版） | 否，已被取代 | 2 | **1** |

**两版的 `skills/` 文件清单逐条相同**（26 个文件、5 条 Skill：`ability-management` /
`runtime-management` / `serveros` / `test-management` / `workspace-management`；
`find | sort` 之后连路径清单的 md5 都相同），所以 `13 → 0` / `1 → 0` 是**同一套文件**上的
比较，不是两次不同的扫描。

分母 26 要说出来。`test_the_scan_actually_reaches_files` 这条控制正是为「空的扫描」写的——
那条规则曾经死过一次，它 walk 了一个不存在的目录并报干净。26 是**这条 composition 技能面的
全部**，不是一次被截断的遍历。

被取代的 `23d` 那 13 条（修前）：

```text
ability-management/control_plane.py:3499                      /home/hwz
serveros/scripts/architecture_check.py:1763                   /opt/applications/data
test-management/SKILL.md:17                                   /opt/applications/data
workspace-management/SKILL.md:25,26,27,134,228,229,296,329    /home/hwz, /opt/applications/data
workspace-management/scripts/map_check.py:32,383              /data/scratch/vince
```

三件事值得单独说：

1. **`map_check.py` 那两条指向 `/data/scratch/vince`，是本机自己的路径。** 规则禁的是「一个
   节点的绝对路径」，不区分它是不是本节点——一条把本机 scratch 写死的说明同样只对一台机器
   成立。修后那两行写的是 `` ## §3 `<SCRATCH_ROOT 的实测路径>`（SCRATCH_ROOT） ``。
2. **`architecture_check.py:1763` 在 `23d` 里没有 `node-path-ok`、在 canonical 里有**
   （§9 表 `P12` 行已记过）。`27a` 里它带着标记，所以它计 0 是**被豁免**，不是被删掉——
   考古那句话还在，只是它现在自己声明了它是考古。
3. `workspace-management/SKILL.md` 一个文件占 8 条：`SYSTEM_ROOT = /home/hwz` 与
   `DATA_ROOT = /opt/applications/data/home-hwz` 被当成读者所在节点的事实写下来。修后是
   `本机 host profile 的 node.roots.identity_home   # 不在此写死任何一台机器的路径`——
   句子从「陈述」变成「读者自己那台机器填进去的符号」，这才是这类文字该有的形状。

#### 9.50.4 没修的那一半：`runtime-assets/hwz-server-os/current`

§9 表 `P12` 行点名的第二个落点**仍然落后**，实测与它写的一字不差：

```text
$ diff runtime-assets/hwz-server-os/current/skills/serveros/SKILL.md <canonical>/skills/serveros/SKILL.md
143c143
<   不 `docker volume prune`。`/` 是系统盘，`/opt/applications` 是数据盘，两者分开报。
---
>   不 `docker volume prune`。系统盘（`/`）与数据盘（机器策略里的 `DATA_ROOT`）分开报，不合并成一个数。
```

那份载体的身份是 `hwz-server-os`、`3.1.0+srv0003.20260923c`、`kinds: [node-runtime, plugin]`；
`runtime-assets/hwz-server-os/current` 是一个**实目录**（不是软链），日期停在 09-23 00:59。

**但它现在没有消费者，这是量出来的，不是推断的：**

| 查什么 | 结果 |
| --- | --- |
| `settings.json` 的 `enabledPlugins` | 没有 `hwz-server-os`（只有 `…linux-work` 与 `serveros-testing`） |
| `settings.json` 的 `extraKnownMarketplaces` | 没有 `hwz-server-os` |
| 谁引用 `runtime-assets/hwz-server-os/current` 这棵**不带子目录**的树 | `.claude/settings.json`、`.claude/plugins/`、`.codex/config.toml`、`~/.serveros/` 四处 `command grep` → **0 命中** |
| 活进程 | `ps -eo pid,cmd` 里没有一条 cmdline 含这棵树 |
| 最后一次被读 | `.claude.json` 的 `skillUsage["hwz-server-os:serveros"]`，`lastUsedAt 1789839012132` = 北京 **2026-09-20 01:30** |

于是这里有一句必须分开说的话：**「那句错话还躺在一个载体里」与「有 Agent 正在被这句话教」
是两句不同的话。** 前者成立，后者不成立——`hwz-server-os` 这个基座 composition 没有任何注册
指向它，它的三代缓存（`3.1.0-srv0003.20260915a` / `23a` / `23c`）是孤儿，而 `skillUsage`
说它已经七天没被读过。

**处置因此是 proposal，不是本片的动作**：要么把这个基座 composition 重新走一次
`build→promote`（它没有任何 marketplace 注册，重建要先决定注册到哪），要么把这个被取代的
载体退役。**手改它的第 143 行不在选项里**——那正是 `P12` 行排除的「手改派生安装」，下次
promote 会盖掉它并再次漂移。

#### 9.50.5 这一片关闭了什么、没关闭什么

- **关闭（P12 的载体半边）**：两个**已启用**的插件缓存各自那份 `skills/`，现在按本仓自己
  的规则量到 **0**。这是这半条缺陷第一次有载体级的读数。
- **没关闭**：`runtime-assets/hwz-server-os/current`（§9.50.4），它连同「重建还是退役」一起
  作为一条 proposal 留在 §9 表 `P12` 行里。
- **一句必须附上的限定**：上面量到 0 的那一版是**已装的** `3.1.0-srv0003.20260927a`，而
  `27a` **在量完之后就被取代了**。§J09（`b8f7d21`，02:13）改了 `runtime/daemon.py`、
  `runtime/cli.py`、`scripts/runtime_truth.py` 与 `ARTIFACT.json` 的字节而**没有改任何文件名**，
  于是 cachebuster 移到 `srv0004.20260927a`，promote 落点变成
  `serveros-linux-work/current -> revisions/serveros-j09-identity-20260927@b8f7d21`，
  而**已装缓存仍是 `27a`**（`installed_plugins.json` 未动），且盘上**还没有 `srv0004` 的候选
  构建**。所以这句话的正确读法是：**「`27a` 这一版按规则量到 0」成立，而「这台机器上的载体
  现在是干净的」只在下一次 `build→promote→install` 之前成立**——那一次的产物会有不同的字节。
- **没关闭（与 §9.49.4 结尾同一份清单，本条一条未动）**：P7 唤醒半的两件前置（§9.38.9）、
  `WAIT_KINDS` 仍只有 `child`/`window`、`NodeControlTransport.send` 不包裹 connector 异常、
  `drift_sync.py` 没有调度器、约 40 处未守护的考古站点、`supersede_checkpoint` 的退役评估。
- **没有新开平台、没有新开文件、没有动 `plugin/` 或任何 composition**：这一格的证据就是
  命令与输出，量具是仓内已有的那一条规则换了个根。

#### 9.50.6 落笔时的前提：这个工作树同时有第二个写入者

上面每一个读数都在 **02:00 前后**取得，那一刻工作树是干净的（`git status --porcelain` 空、
`HEAD = origin/main = 512f886`）。**量完之后、落笔之前，同一个工作树里出现了第二个写入者**：
`git status --porcelain` 从 0 行变成 7 行，其中 6 行不是本片写的，mtime 落在
**02:04–02:12**：

```text
02:04:52  scripts/build.py              （+65）
02:07:16  runtime/daemon.py             （+201）
02:11:15  scripts/runtime_truth.py      （+95）
02:12:15  tests/test_runtime_truth.py   （+254）
02:12:22  tests/test_serverosctl.py     （+81）
02:12:30  runtime/cli.py                （+102）
```

本片因此**没有提交**，也有意不提交：一次 commit 要么把这些 WIP 卷进来（禁止），要么在别人
正在写的分支上造一个只含台账的提交。**本片的证据也没有跑全量套件**——全量套件在这个前提下
读不出可信的结论，它测的会是一棵正在被改的树，红绿都归不到任何一个 revision 上。上面
9.50.3 那组读数不依赖套件：它们是一次只读的目录扫描，输入是**磁盘上已装好的载荷**，
与这 7 个文件无关。

这不是本片要处置的事，是**记录**：`serveros-completion-20260925` 此刻有两个写入者，而
机器策略要求它在动手前先取新快照。本片只写 `docs/CC-EXECUTION-LEDGER.md` 一个文件，
它没有被那 6 个文件里的任何一个碰过。

## 10. 后续每包更新哪几行

- 改完一个包，只更新 §3.1 里对应行的四层状态 + §5 表的增删。
- **不新开文件、不新开平台。** 计划原话：「后续每包只更新此处结果/证据，不另建平台。」
- 状态从 `NOT_MEASURED` 变 `DONE` 必须有具体证据（测试名 + 结果，或实测命令 + 输出），
  不能因为「代码在盘上」就改。

## 11. 孤立的外部权限 / HUMAN_EXECUTED 操作（终报 §16 的输入）

这一节存在的唯一目的是**不让东西丢掉**。§16 要的终报里，这些必须是**单独列出**的一类，
不与「本轮做完了什么」混在一起。凡是台账正文里写了「列入 §16」的，条目都落在这里；
台账曾到 §10 就结束，所以当时「§16」只是一个承诺——这一节是那个承诺的**落点**，
而**兑现**它的那一节是 §12（终报），二者的分工是：这里逐条记**为什么本轮做不了**，
§12.10 只给指针与最小解除条件，不重复理由。

### 11.1 需要人（或一次授权）动**另一个仓库**的远端

| # | 动作 | 实测事实 | 为什么不是本轮 |
| --- | --- | --- | --- |
| 1 | **把 `074a518` fast-forward 到 ability-management 的 `main`** | `git merge-base --is-ancestor origin/main HEAD` → 是；`0 10`，10 个提交、35 个路径，全在 ability-management 自己的 `skills/`、`plugins/`、`abilities-manifest.yaml`、`ability-packs.yaml`、capability-family 视图与 `tests/` 里 | 这是对**另一个仓库的 main 的远端写**。§1 用「不覆盖 main」把它括起来，`ops/CLAUDE.md` 默认只读。它是 §5.0.1 那 8 条跨仓红的**唯一**解除条件（另两条路测过，都不够：快进到 `origin/main` 不行——`e7d20f3` 不在其上；在 canonical 检出切分支更差——只把量具弄绿、产品照红） |

### 11.2 会**丢失别人未推送工作**的东西——不碰，但要记

| # | 事实 | 实测 |
| --- | --- | --- |
| 2 | **`src/serveros` 的本地 `main` 上有 2 个提交不在任何远端分支上** | fetch --prune 之后：本地 `main` = `d0a94d6`，`origin/main` = `4d090d6`，`40 2`。那两个是 `e925fbf space: My Document Library is a workbench, not a staging area for the wiki` 与 `d0a94d6 space: name the Item closure artifact, and say what each surface is for`；`git for-each-ref --contains` 只列出 `refs/heads/main`（本机） |

**这一条不是「待办」，是「待保管」**：谁来一次 `git -C src/serveros pull` 或快进，这两个提交就
没了（本地 `main` 会被覆盖或产生分叉）。本轮**不推它们**——它们不是本轮的工作，推等于替别人
决定；也**不 stash、不 reset**。处置只能由那两个提交的作者做，所以它进终报的孤立清单。

| # | 事实 | 实测 |
| --- | --- | --- |
| 2b | **七个 `archive/*` tag 的内容不在任何远端 ref 上**，而它们**只被 tag 持有**——早先那次核对用的判据是 `git log --branches --not --remotes`，**它结构上看不见 tag**，所以当时得出的「只有 3 处」窄于它被写进的那句话 | 本机 17 个 `archive/*` tag，其中 14 个远端没有同名 tag；这 14 个里 7 个的提交在远端 ref 上（同 sha），另 **7 个的提交不在任何远端 ref 且不被任何远端分支包含**：`serveros-migration-orphans-anchor`（**33 个提交不在 `origin/main`**）、`serveros-migration-localonly-anchor`（3 个）、`serveros-agent-sessions-wip`、`serveros-beijing-main-20260914`、`serveros-fs-hygiene-home-data-root-alias`、`serveros-srv-0009-wip`、`serveros-tokyo-enrollment`（各 1 个） |

2b 与第 2 条**同一个类别**，理由也一样：这些是迁移期刻意留在本机的锚点（名字里
`localonly-anchor` / `orphans-anchor` 就是作者自己写的），**推它们等于替别人决定把一个本地
锚点变成公开引用**。当务之急不是同步，是**别把它们弄丢**：`git tag -d`、`git gc` 里任何
把它当悬空对象的动作都会动到它们，所以本轮的处置是**什么都不做，只记下这份清单**。
判据的教训与这份台账别处同形：**`--branches` 看不见 tag**，而「不在任何远程 ref 上」是一句
覆盖所有 ref 的话——一个窄判据被写进一句更宽的话里，得到的绿是量具的绿。

### 11.3 本节点特有的两个缺口

| # | 缺口 | 为什么需要人 |
| --- | --- | --- |
| 3 | **本节点的 root 绑定缺口**：检查器报 5 个 token 未解析，其中 2 个（`SRC_ROOT`、`KNOWLEDGE_ROOT`）是本机 Profile 其实绑了、检查器读错了文档；3 个（`OPS_ROOT`、`RECORDS_ROOT`、`TEST_ENVS_ROOT`）是本机 Profile 真没绑，而目录都在。**现行（2026-09-22 复测，§9.21）：这条账的两半都不成立。** 检查器从那份文档里读到的 `declared` 是**空的**——不是「绑了三个少了两个」，是**一个都没读到**；真因是 `ops/workspace/environment.yaml` **解析不过去**（`ScannerError`，line 70 column 11，一个以反引号开头的 plain scalar），而它同时是 `schema: hwz/environment.v2`、**本来就没有 `roots` 键**。所以「哪几个绑了」这个问题问错了对象 | 修法变成两条，都不在这一侧：**(a) 那份文档的语法**是别人的仓库、别人的提交（一行引号的事，位置见 §9.21），进 §11 孤立清单；**(b) 让 locator 认得 v2 的 `binding_authority`** 需要把三个互不同名的词表对齐（§9.18），猜一个映射正是这个检查器拒绝做的事（「reported as unresolved, never guessed」）。**本单元做掉的是第三件事**：让检查器**说出它读到的到底是什么**，于是这两条以后不必靠推断。§9.21 |
| 3b | **`ops/workspace/environment.yaml` 有一处 YAML 语法错**：`note:` 的值以反引号开头（YAML 保留指示符），`ScannerError` at line 70 column 11；该文档是 `schema: hwz/environment.v2`（2026-09-18 实测）。它使本节点的 machine instance **完全不可读**，五个 root token 因此报未解析 | 改一行引号即可，但那份文件在**另一个仓库**（hwz-ops），ops 策略默认只读。本轮只报不改；位置与判词已由 `serverosctl architecture check` 的 `ROOTS_SOURCE_UNREAD` 逐字给出 |
| 4 | **两处量具缺陷**：`architecture_check.py` 从工作树扫代码、从 ref 的 git object 读契约（§9.18.1，ref ≠ 工作树内容时按构造报出假 HIGH）；`SERVEROS_REPO`/`SERVEROS_ABILITY_REPO` 在 `architecture_check.py:1427`、`scripts/runtime_truth.py:1588` 被当作「要再拼一层的那层」，而 `build.py:1037`、`runtime/cli.py:8911`、`abilities/taxonomy.py:1095` 当作检出本身（§9.18.2） | 9.18.2 是机械的、可做的；9.18.1 要先决定「ref 与工作树不一致时该拒绝还是照报」，而产品里已有先例（缺 root 时拒绝）。本轮**同时改代码与改量具会让「绿」无法归因给任何一边**，所以两处都不落 |

### 11.4 §7.2 的五条 HUMAN_EXECUTED（原文不动，只在此处汇总指向）

重启 `serverosd.service`（PID 2615936）；把 CC marketplace `hwz-server-os` 指到新 release 并
`claude plugin update` 工作台；`systemctl --user enable --now serveros-daily.timer`；
装原生告警投递 `scripts/release.py alert-worker --conversation <id>`；
在 `~/.claude/settings.json` 的 `SessionStart` 下注册 `drift_sync.py`。**五条都是外部/共享面动作**，
理由逐条写在 §7.2，不在这里重复——重复会漂移。第五条是 2026-09-22 新加的那条，
它在此之前**只是 §9 表格里的一句散文**（§9.20 讲了这一句怎么第一次有了读者）。

### 11.5 P2 的 revisions 事实：生产者在一支 2 提交之外的分支上，**而且送货还不够**

**这条是 9.22.6 的落点，写在这里是因为它不是 ServerOS 的产品缺陷，是一次对共享面的写。**

实测（2026-09-25）：

```text
活体   /data/home/vince/workspace/records           main  HEAD 7368e29  工作树干净
分支   worktrees/records/P2-IM-REVISIONS                  HEAD 56783f1  工作树干净
差     main..HEAD = 2 提交 · 7 文件 · +1093 −45
其中   information/raw/sync_feishu_chat.py   ← revisions_checked_at / revisions_compared 的生产者
       information/raw/drift_sync.py
       information/raw/tests/test_sync_feishu_chat.py / test_drift_watchlist.py
活体树里含 `revisions_checked_at` 的文件：**0 个**
```

`capture_coverage._revisions_component` 要的正是 manifest 的 `sync.revisions_checked_at` 与
`sync.revisions_compared`（后者的存在是为了让「0 条改动」是一个**测出来的零**而不是「根本没比」）。
活体 waterline 里 **47 个源**因此报 `revisions` UNDECIDABLE。

**为什么本轮不做，有两层，第二层比第一层重要：**

1. **第一层**：把 `main` 快进 2 个提交是**对 canonical 共享检出的写**，而这个树的干净是**刚量的、
   随时会变**的（记忆里记着它曾有只存在于磁盘的未提交资产）。这一层是纪律问题。
2. **第二层**：**送货不足以让那 47 行变绿。** 字段是 `sync_feishu_chat.py` **跑到真源上**才写进
   manifest 的。所以即使代码到位，只有**此后重新同步过**的源才会有这个字段，
   之前的历史 manifest 仍然没有——那 47 行里有一部分是**不可逆的历史**，
   不是「补一次就没了」。而「跑到真源上」是一次**面向飞书的动作**。

所以正确的解除条件不是「合一支分支」，而是人要决定两件事：**(a)** 允许在
`workspace/records` 上落这 2 个提交；**(b)** 允许对哪些源重跑同步（这一步决定 47 行里有多少能变成
可判，多少永远是历史缺口）。**只做 (a) 而不同时定 (b)，账面上不会有任何变化**——这一条必须先说，
否则下一次读到「47 行仍 UNDECIDABLE」会以为分支没合。

### 11.6 常设维护契约放在哪：这是一个要人定的位置，不是一次搬迁

`litellm-work-doc` 的契约（registry + profile）本轮**修好了，但只在一个 evidence 目录里**：
`ARTIFACTS_ROOT/skills/serveros/RUN-20260925-p4-stewardship/`。那是一份**跑的记录**，不是它该长期
待着的地方——按 CLAUDE.md，Skill 证据写 `ARTIFACTS_ROOT/skills/<skill>/`，而一份**常设契约**
既不是证据也不是产物。

三个候选，各有各的坏处，本轮**不选**：

| 候选 | 好在哪 | 坏在哪 |
| --- | --- | --- |
| 能力自己（`document-stewardship` 的 canonical） | 契约与读它的规则在一起 | 那份能力**不在任何仓库里**（§9.23.6），往里写等于把契约放进一个没有出处的地方 |
| records 树（`workspace/records/`） | 有版本、有历史、跨机可见 | records 的规矩是「raw 只存原始事实，零结论」，而 registry 是**结论**（谁负责、能改到什么程度） |
| ServerOS 仓（`state/` 之外的一棵 `contracts/`） | 与读它的代码同版本 | `state/` 按定义是**会变的东西**、被关在 Git 外面；契约恰恰是**稳定的那半**，放进 `state/` 就把它变成了派生状态 |

**为什么必须由人定**：这个位置一旦选定，就决定了「谁有权改它」——放进 records 意味着改契约是
一次 tokyo 数据面的写；放进 ServerOS 仓意味着改契约要过一个 PR。**两条路都合理，选哪条是在
分配权力**，不是一次目录整理。在定下来之前，那两份文件留在 evidence 目录里，并且**不接任何
常设 routine**——接了就等于用「它在哪」这个未决问题去决定「谁能改」。

### 11.7 P5 留下的三条：两处位置没有定论，一条能力还没有落点

| # | 事实（实测） | 为什么不是本轮 |
| --- | --- | --- |
| 5 | **「外部文档」空间的索引页今天列不出任何东西，而它下面建不了节点。** 那张索引页的 `<sub-page-list>` 指向一个**已被删除**的节点，所以页面上的列表是空的；试着在它下面建节点被飞书拒 `131006`；同一个 space 的**根节点**可见列表里只有未改动的默认模版「首页」。本轮把 REF01 的复核与补齐做掉了，也把这份参考**发布**进这个空间并回读确认落地，但「外部参考笔记长期住在哪」这个问题**没有答案可抄** | 两条路都要人定：**(a)** 修/重建那张索引页——改的是**别人建的一张页**，而 `131006` 说明它的父节点已经不在了，正确做法可能是**新建**一张而不是修那张；**(b)** 认定这个 space 的**根**就是长期落位，那就等于说「外部参考笔记不住在按主题分的子页下面」，与 `EXTERNAL-REFERENCE-LIBRARY.md` 的措辞不一致。这不是一次目录整理：**选哪条决定了以后谁能往里加** |
| 6 | **`lark-cli` 的 `--content @file` 只认三处路径**（当前工作目录、`/tmp`、`~/files`），越界直接报 "outside the built-in allowlist"；而本机策略是**临时文件一律落 `ARTIFACTS_ROOT`**，并且明文**禁止造替代 `~/files` 根**。于是「按策略生成的内容」与「按工具能发的内容」之间有一处**结构性的不重合** | 本轮走的是 `--content -`（从 stdin 读），所以**今天不需要解**。但这是一处**两条规则各有出处、彼此矛盾**的地方，改哪一边都是改别人的规则：放宽白名单是改一个本机安装的工具行为，往 `~/files` 写是违反机器策略 |
| 7 | **`human.notification.transport` 今天读 `UNKNOWN`**，而这是终报里唯一一处**向下**的修正（§9.24.1）。能到人的那条路已经存在并跑到了环（§9.24.3，`DELIVERED` 带回执），但**告警那条腿**仍然指向 BotMux 的 `/api/trigger`——它没有收件人字段，省略路由三键会得到**合成会话**，文本变成模型的任务，且头部禁止发布到飞书，**所以它到不了任何人** | 要人定的是**要不要建一个有地址的触发器**（把 `SERVEROS_ALERT_CONVERSATION` 真正绑到一个会话上），还是**接受告警次通道就这么留着**。另一件更小的：`--idempotency-key` 的去重窗口是 **1 小时且静默**，所以「报告没到」在 1 小时内重发会被**静默吞掉**——窗口过后是否重发是人的决定，不是脚本的 |

这三条**同一类**：都不是「做不到」，而是「**做到需要先决定一件事，而那件事是在分配权力、或者改变别人的东西**」。
第 5 条改的是别人建的页，第 6 条改的是别人的工具或机器策略，第 7 条改的是一个**对外可达的地址**。
三条都记在 §9.24.6 与 §9.24.4 里有实测出处，这里只做汇总与解除条件的分类，不重复理由。

## 12. 最终报告（计划 §16 要求的那一份）

**读它之前先读这一句**：这一节不是「全系统 READY 证明」。它把**已完成的实现**、**已量到的
集成**、**真在链路上跑的**和**被人或权限挡住的**分成四类分开说，因为把它们合成一句「做完了」
正是这份台账从第一节起就在防的读法。逐条证据在 §3 与 §9，本节只给判词与指向；数字都是
2026-09-22 在工作树 `40ffaa5` 上量的，读数留在
`$ARTIFACTS_ROOT/skills/serveros/RUN-20260922-driver-surface/`。

三层口径（§4 的定义，这里不重写）：`DONE`/`ENABLED` 是已落地并测过；`NOT_MEASURED`
是**没测**，不是失败也不是通过；`NOT_ENABLED` 是明确没启用。**「代码在盘上」永远不算 LIVE。**

### 12.1 21 个语义职责的四层状态

`DESIGN` 这一列 21 行全 `DONE`，是**同一个判断**，所以先说清它判的是什么：设计
（`design/ABILITY-CANONICAL-BLUEPRINT.md` 与各领域冻结文档）对每条职责都写了语义、边界与
owner，**这一列问的是「设计有没有」，不是「provider 有没有」**。provider 的空缺在 §3.1 的
Binding 一列，记 `UNBOUND`——把它读进 DESIGN 会让「没绑定」看起来像「没设计」。

| # | 职责 | DESIGN | CODE | INTEGRATION | LIVE | 一句话 |
| --- | --- | --- | --- | --- | --- | --- |
| G1 | 能力治理 | DONE | DONE | DONE | ENABLED | 一个 artifact 一条 plugin 记录，成员按 composition 精确取（§3.7） |
| A1 | 接续 | DONE | DONE | DONE | ENABLED | 实时控制与历史归档是两个 artifact，一个职责（§3.2） |
| A2 | 记忆检索 | DONE | DONE | DONE | PARTIAL | **UNBOUND**：OpenViking provider 在本机无处可绑 |
| A3 | Workspace/Git | DONE | DONE | DONE | ENABLED | 运行时名仍是 `workspace-management` |
| A4 | 运行环境/Fleet | DONE | DONE | DONE | ENABLED | 环境定义**移出**测试，所以一个节点可以定义环境而从不跑 E2E（§3.8） |
| A5 | Fleet 交接 | DONE | DONE | DONE | PARTIAL | **UNBOUND**：单设备路径可用，跨设备动词 absent |
| A6 | 委派 | DONE | DONE | DONE | ENABLED | 真装的是 Quay 0.11.0 |
| A7 | 自动化测试 | DONE | DONE | DONE | ENABLED | 唯一落成 **Plugin composition** 的一条，因为要装到**别的** CC 上（§3.3） |
| A8 | 构建发布 | DONE | DONE | DONE | ENABLED | 已加进 `pack:litellm-development` |
| A9 | 生产观察 | DONE | DONE | NOT_MEASURED | NOT_ENABLED | **UNBOUND**；不进任何 pack，这是决定不是遗漏（§3.8） |
| A10 | 生产变更 | DONE | DONE | NOT_MEASURED | NOT_ENABLED | 同 A9；`READY ≠ 切流授权` |
| B1 | 来源采集 | DONE | DONE | DONE | ENABLED | `records/information/raw/*` |
| B2 | 结构化认识 | DONE | DONE | DONE | NOT_ENABLED | 读侧 §9.7、写侧 §9.9 都在；**没有消费者读那份 patch**，所以不 LIVE |
| B3 | 需求 | DONE | DONE | DONE | ENABLED | — |
| B4 | 工作管理 | DONE | DONE | DONE | ENABLED | — |
| B5 | 调研 | DONE | DONE | NOT_MEASURED | NOT_ENABLED | **UNBOUND**（Serper 有 key 但没跑过真实查询） |
| B6 | 方案 | DONE | DONE | DONE | ENABLED | 旧入口分步迁移未做 |
| B7 | 实现 | DONE | DONE | DONE | ENABLED | 重叠旧入口未迁移 |
| B8 | 报告发布 | DONE | DONE | PARTIAL | PARTIAL | 没有独立 artifact 是**设计选定的落法**；欠的是四处移除，被活动 consumer 挡着（§3.5） |
| B9 | 文档维护 | DONE | DONE | NOT_MEASURED | NOT_ENABLED | 位置/ACL 侧留在 B-空间 |
| B10 | 人机控制 | DONE | DONE | DONE | ENABLED | 告警卡面已在生产链上；status 卡面仍未接（§3.6）。**§9.24 更正一处过高的声明**：`human.notification.transport` 的 `SUPPORTED` 是错的（那条路到不了任何人），已降为 `UNKNOWN`；四层状态不变，因为这一行的 ENABLED 靠的是**卡面**在生产链上，而那一条没变——**降的是一条能力声明，不是这一行的实测面** |

数一下就得到这张表的形状：**INTEGRATION 已测 16 / 21（另 1 条 PARTIAL、4 条从未测），
LIVE 已启用 13 / 21（另 3 条 PARTIAL、5 条 NOT_ENABLED）**。从未集成的四条是
A9/A10/B5/B9，**它们不是「没做完」，是没有消费面**——CODE 是 DONE（artifact 在盘上并有
测试读过），缺的是「有个真实 consumer 真去调它」。B2 是另一种形状：集成是 DONE，
LIVE 不是——两侧都接线了，而**没有任何消费者读那份 patch**。

### 12.2 ServerOS 插件：source / build / install / running —— 四个面，两个已经对齐

这四个词在本机指向**四个不同的东西**，§2 把它们分开列是有原因的：

| 面 | 指向 | 版本 | 状态 |
| --- | --- | --- | --- |
| source | `serveros/fabric-integration-20260922` @ `40ffaa5`，= `origin/main` + 36 | — | 已 non-force 推到同名远端分支，本工作树与远端逐字相同 |
| build | `runtime-assets/hwz-server-os/serveros-linux-work/revisions/cards-and-release-20260922@2f77b02` | `3.1.0+srv0003.20260922a` | 构建产物，`kind: node-runtime`，`tree_hash 5e8694ce…` |
| install（release 面） | 同上目录的 `current` symlink | 同上 | **已对齐**。`previous` = `noc-copy-20260922@c233946`，回滚是一条命令 |
| install（CC 工作台面） | `~/.claude/plugins/cache/hwz-server-os/hwz-server-os/3.1.0-srv0003.20260915a` | `3.1.0+srv0003.20260915a`，3 skills | **没动，也不该由本轮动**：指过去等于给已装 plugin 改名（`hwz-server-os` → `hwz-server-os-linux-work`），是用户可见的身份变更 |
| running | `serverosd.service` PID 2615936，起于 11:00:26 | 仍是 promote 之前那一版 | **没重启**：`current` 与新 shim 都已经是新代码，所以**新进程拿到新的、长驻进程还是旧的**——这正是「代码在盘上 ≠ LIVE」在同一个节点上的实例 |

`~/.local/bin/serverosctl` 这个 shim 现在是路径跳转，指到
`…/serveros-linux-work/revisions/cards-and-release-20260922@2f77b02/bin/serverosctl`；
它是路径跳转，不随进程走。

**所以「ServerOS 插件装了吗」这个问题有两个都对的答案**，取决于问的是哪个面：release 面
已经对齐，工作台安装面差一次 `claude plugin update`，而那次 update 被改名这个副作用挡着
（§7.2 第 2 条，HUMAN_EXECUTED）。daemon 的重启同理（§7.2 第 1 条）。

### 12.3 `serveros-testing`：source / registry / release / install / fresh-session load

五问逐条答，全部在 `2026-09-22` 实测：

| 问 | 答 |
| --- | --- |
| source | `skills/automated-testing` + `plugin/profiles/testing.yaml`（composition `serveros-testing`） |
| registry | **独立的第二条 plugin 记录** `plugin: serveros-testing`，`provides 2`：`automated-testing`、`test-management`。不是从别的记录并出来的（§3.7，commit `e7d20f3`） |
| release | `build 1.0.0+srvtest0001.20260922b` → `revisions/fabric-integration-20260922@2f77b02` → `current`。`kind: plugin`、`kind_basis: chosen`——`release.py` 按 **artifact kind** 分派 verifier，不再由 node runtime 的判据替所有类型做决定（§3.3） |
| install | `claude plugin validate` 通过 → `marketplace add …/current` → `install serveros-testing@serveros-testing`。**没有手改 `installed_plugins.json`**，**没有用 `--plugin-dir`**（那只是集成证据，不是安装证据）。落点 `~/.claude/plugins/cache/serveros-testing/serveros-testing/1.0.0-srvtest0001.20260922b` |
| fresh-session load | 2026-09-22T15:34Z 再验一次：另起 headless 会话点名，返回 `serveros-testing:automated-testing`、`serveros-testing:test-management` 两条。§3.3 记的三次（两次列名、一次真调用）在更早的同一个 revision 上 |

### 12.4 Packs：五个，两处改动，都在能力仓而非本仓

| pack | 成员数 | 本轮动了什么 |
| --- | --- | --- |
| `pack:quality-delivery` | 4 | `automated-testing` 加为 `required` |
| `pack:autonomous-quality-loop` | 6 | 同上 |
| `pack:litellm-development` | 7 | 同上，且 `build-release` 加为 `required` |
| `pack:cross-device-collaboration` | 4 | — |
| `pack:agent-orchestration` | 6 | — |

五包的 `authority.revision` 现在**同一个值** `e7d20f3b50e1a86c9998b5dff3f519fa52b4aa1d`
（在 ability-management 的 `integration/fabric-20260922` 上逐包量过），它是读到的 revision 的
**祖先**，且 `abilities-manifest.yaml` 在两者上逐字节相同——pack pin 的祖先规则要求的两条。

**A9/A10/B5 不进任何 pack，这是决定不是遗漏**：给它们造一个空 composition 会把「还没接线」
读成「已经承诺」（§3.8）。

### 12.5 Daily Cycle：装了、手跑过一次、**没有上弦**

```text
~/.config/systemd/user/serveros-daily.service   装了
~/.config/systemd/user/serveros-daily.timer     装了，is-enabled=disabled, is-active=inactive
手工跑过一次   Result=success  ExecMainStatus=0  → 15 份文档到 $ARTIFACTS_ROOT/serveros/workbench
```

`START-HERE.md` 的头部记着渲染者、三组输入、23 条命令逐条的退出码，其中 `ability resolve`
两条是 **2**——那不是失败，是「没给 ability catalogue，所以答案是 UNKNOWN，且 `inputs_missing`
逐条点名缺哪八个输入」。**把「问不出来」记成 2 而不是 0**，是这条链要的诚实性。

**为什么不上弦**：单元自己的头部写着「enabling is the act §8 puts on the approved list」。
装上（可手工验证、不自动运行）与上弦（每天 08:17 自动产出）是两个动作；本轮只做前者，
后者是 §7.2 第 3 条。

**但"上弦"不是这一条唯一的缺口，而且不是最硬的那个**（2026-09-23 实测）。信息半环当天
真跑通并留下产物（`information captured` 98,685 B、`information watched` 1,944 B、
`work understanding` 44,067 B、`report daily` 写出 `daily-report.md` 3,891 B ＋
`publication-receipt.json` 1,209 B，其中 `blocking` / `claimed_done_without_a_pass` /
`fallbacks` 三项全空）；**工作半环跑不完**：`work focus` 判 `BLOCKED`、理由 `STALE_BOARD`、
`selected` 空，`work grants` 0 条委派，`work admissions` 0 条在办。根不在定时器，在
**板投影没有 producer** —— `focus.not_produced` 逐项列出板还没有生产的字段
（`priorities_unproduced: 34`、无 dependency edge），`grant_ref` 那一句是 ServerOS 自己写的：
「The gap is the board's producer, not the store's」。

所以在这里打开定时器会产出一份**看起来像工作循环的日产物，而 `select_focus` 每次都返回
BLOCKED** —— 那正是这一轮反复遇到的那种绿：动作执行了、退出了 0、结论却不是使用者要的。
上弦要等板有 producer 之后再做，那才是它有意义的时候。

### 12.6 Cards：alert 与 status 两面都在生产链上

链条（§3.6 逐段核过）：

```text
anomaly(detectors) -> alert producer(身份 = code+device+subject)
  唯一生产调用者 = daemon tick -> card model(catalogue card-definitions.yaml)
  -> serverosctl card {status,alert,recovery}（恰好三个）
  -> ops alert_digest_card.py 取面 -> lark-cli 发
```

- **告警卡面已经是 ServerOS 出的**：`ops/…/alert_digest_card.py:107 serveros_card()` 调
  `serverosctl card alert --json` 并把返回的 `body` 当卡面；只有取不到才回落旧渲染器，且回落
  会往 stderr 写一行。所以 `card` 这个面**在生产链上**。
- **status 卡面（每小时那张主卡）2026-09-23 接上了**（`hwz-ops@a8272d4`）：`monitor_card.py`
  改成调 `serverosctl card status --with-frozen --json --gateway-binding <path>`，把返回的
  `body` 当卡面、把 `frozen` 当回执信封写 `card record-delivery`；取不到就回落本脚本原来的
  渲染器并往 stderr 写一行（计时器的安全属性）。分工是 **ops 量网关、措辞归 ServerOS**：
  测量值只能经 `binding["gateway"]` 进卡（`card_source.py:395`），而"一个数该怎么说"只在
  `card-definitions.yaml` 一处声明。
- **冻结块是逐次渲染的，不是一个固定值。** 这里原来引 `status@9820fa98d597`、2967 B，读起来
  像一个版本号；今天两次渲染分别得到 `status@b3bd796bad44` / `status@b7071444bc98`，因为
  `version = "status@" + payload_hash[:12]`。要引就引某一次：2026-09-23 02:50 那次
  `payload_bytes = 3657`、`version = status@b7071444bc98`。
- **更正一处失实**：这里原来写「这一跳本轮做不了，不是因为难……`monitor_card.py` 正被两个
  活动工作树压着」。`alert-card-v2` 一行没改 `monitor_card.py`；只有 `SRV-0011` 改了它，且改
  的是**共享网关端口声明化**那一段，与本跳无关。而 `SRV-0011` 那份未提交的
  `gateway-binding.yaml` 与本跳落地的同名文件 schema/role **逐字相同** —— 同一个设计的两次
  独立实现 —— 已按 §2「重叠文件准备兼容补丁」取并集收敛（`hwz-ops@0958ca9`）：身份字段
  （`service_ref`/`source`/`maintainer`）喂 ServerOS，`endpoint`/`evidence`/`window_minutes`/
  `watched_families` 喂 ops 自己的 `gateway_binding.py#cross_check()`，两类不重叠。缺
  `endpoint` 时 SRV-0011 的 `gateway_port()` 会把「端口退回常量」报上卡 —— 并集不是折衷，
  是两边各缺的那一半。

### 12.7 Laya：provider 已部署并验收，缺的是消费 —— 三条订正、一条仍真

| 问 | 答 |
| --- | --- |
| 有受管进程吗 | **有**（2026-09-23 订正）。`laya-shared.service`：`active (running)`、`enabled`、`Linger=yes`，主 PID 206372，`Tasks 21`，1.3 G RSS（`MemoryMax=3G`），启动 02:24:03 |
| 有 adapter 吗 | 有，在 **MarketOps 仓**：`worktrees/marketops/mvp/marketops/adapters/laya.py`，`LayaDecisionProvider:128`。但它是**进程内模型**，没接到共享 endpoint |
| 有权重吗 | **有**（2026-09-23 订正）。`caches/models/laya-multilingual/052592a1…/model.safetensors`，643,835,514 B，`sha256:9d628fd9…`（本轮复算，与部署回执一致） |
| ServerOS 侧有 consumers 吗 | **仍为 0**。`grep -ril laya runtime/ mcp/ abilities/` 零命中；12 个 serveros 工作树里也全是 0 |

**订正的原因**：这里原来写「没有受管进程、没有权重」——那是在 2026-09-23 01:54 下载权重
**之前**量的。部署与验收由它自己的 Item 完成并留下回执：`ops@ed3166d`
`reports/2026-09-23-laya-shared-provider-deployment.md` —— 契约 §7 八行里 6 行观测通过、
2 行如实记 `NOT_OBSERVED`，并诚实记着 `verified_for_this_request` **恒为 false**（这台机器上
没跑过任何质量评估）。

那条回执自己点名了剩下的缺口，与本轮实测一致：**「没有第二个 consumer 业务接入……真正的
业务 consumer（MarketOps、ServerOS）尚未接」**。而 ServerOS 侧连 provider 消费者都不存在。

`providers/` 这个目录在 ServerOS 里**不存在**——四种独立查法确认，不是没找到。（顺带一条：
全机只有一个 provider 记录 `provider:openviking`，属记忆域，而它被复制进全部 10 个
serveros worktree/revision，所以在每个 worktree 里都「存在」——那本身不是 10 个 provider。）
ServerOS 实际有的是契约与解析器（`abilities/provider-contract.yaml`、`abilities/resolver.py`、
`abilities/provider_instance.py`），其中最后一个自己的 docstring 记着 `instance:` 记录「自契约
写下就声明了，**而没有任何 writer**」。契约写 `is_the_field: instance.status`，问的是
「would calling this provider right now work」，两个轴是控制面可达与执行就绪。

**所以 P10 的判据（「ServerOS 和 MarketOps 同时访问同一个 Laya」）不是"被测对象不存在"** ——
provider 侧的对象在、还在跑；**被测的消费不在**：两个轴各有观测者才有意义，而现在
ServerOS 侧一个观测者都没有。这不是「测了没通过」，是「没有人在问这个问题」。

（一条诊断，不是故障，免得下次被当故障读：journal 里的 `BrokenPipeError` 是无凭据客户端
被正确拒 `UNAUTHORIZED` 后在拒绝写出前断开 —— 它证明拒绝路径是活的。）

### 12.8 MarketOps：代码在，业务 MCP 没注册，SOP 只读

| 对象 | 状态 |
| --- | --- |
| 业务 MCP（`interfaces/mcp/__main__.py` + `adapters/mcp_server/*`） | **没在任何地方注册**——唯一相邻的 `.mcp.json`（`worktrees/quay/marketops-goalwrite/.mcp.json`）是空表 |
| Serper adapter | working（adapter + port + tests）；凭据在 `~/.secrets/vault/serper.env`（0600）。~~**没有跑过真实查询**，所以 LIVE 不可声称~~ → **更正 2026-09-23**：已跑过一次真实付费查询，判 **LIVE**（见 9.4） |
| Jev adapter | ~~**UNBOUND**：无 key~~ → **更正 2026-09-23**：key 存在于 `~/.secrets/vault/typesafe.env`（0600，`TYPESAFE_API_KEY` 非空），但**未注入环境**且 `MARKETOPS_DECISION_PROVIDER` 未设。阻塞在**接线与出站授权**，不在凭据缺失 |
| 原 SOP | 只读 / 提案；本轮**没有**外联、没有 SMTP、没有绕 WAF，**没有**写 Docker/Compose/Hosting 生产 |
| 并行开发 | 他人的 checkout、dirty 文件、活跃 worktree **一律不碰**（§6） |

### 12.9 并行 LiteLLM：只读，没动

本机此刻跑着**两台** Postgres 支撑的网关（`litellm_container0`、`litellm_shared`），它们属于
别人的并行工作。本轮对它们**只做只读观测**：没有重启、没有改配置、没有重跑产品验收、
没有动 `lit-0106-*` 容器（`RESERVED_BY_OTHER_WORK`）。LiteLLM need A（toolcall/typography）
据信已修，本轮**只验证不重实现**。

### 12.10 孤立的外部权限 / HUMAN_EXECUTED —— 单独一类，不与上面混读

这一类的定义是「**本轮做不了，且不是因为难**」。它们已经逐条落在 §11，这里只给指针与
最小解除条件，不重复理由（重复会漂移）：

| # | 类别 | 条目 | 最小解除条件 |
| --- | --- | --- | --- |
| A | **另一个仓库的远端写** | `074a518` fast-forward 到 ability-management 的 `main` | 一次 `git push origin 074a518:main`；它是 §5.0.1 那 8 条跨仓红的**唯一**解除条件（另两条路测过：快进到 `origin/main` 不够——`e7d20f3` 不在其上；在 canonical 检出切分支更差——只把量具弄绿、产品照红）。§11.1 |
| B | **会丢失别人未推送工作的** | (i) `src/serveros` 本地 `main` 上 2 个提交不在任何远端分支上；(ii) **七个 `archive/*` tag 的内容不在任何远端 ref 上，其中 `serveros-migration-orphans-anchor` 一个就持有 33 个不在 `origin/main` 的提交** | 只能由原作者处置。**这两条都不是待办，是待保管**：本轮不推、不 stash、不 reset、不 `tag -d`、不 gc。§11.2 与 §11.2 2b——(ii) 是补量出来的：早先的核对用 `--branches`，**它看不见 tag** |
| C | **另一个仓库的一行错** | `ops/workspace/environment.yaml` 在 line 70 column 11 有一处以反引号开头的 plain scalar，使本节点 machine instance 完全不可读 | 加一对引号；该文件在 hwz-ops，ops 策略默认只读。位置与判词已由 `serverosctl architecture check` 的 `ROOTS_SOURCE_UNREAD` 逐字给出。§11.3 3b |
| D | **一处由现有事实决定不了的具体业务语义** | 「契约 token → Profile 路径键」的映射该由谁署名 | 契约自己那句 `note` 说 `owner: hwz-ops` 的实例文档已退役、接手的 Profile 属 serveros，所以这个映射的归属要**重新署一次名**。本轮唯一一处需要作者（而非授权）的项。§9.18.2 |
| E | **两处量具缺陷** | ref ≠ 工作树时该拒绝还是照报（§9.18.1）；`SERVEROS_REPO` 在五处调用点有两种读法（§9.18.2） | 前者要先定极性（产品里已有先例：缺 root 时拒绝）；后者是机械的。本轮不改的理由是**在晋升链上同时改代码和改量具，就无法把「绿」归因给任何一边**。§11.3 4 |
| F | **五条 HUMAN_EXECUTED** | 重启 `serverosd.service`；把 CC marketplace 指到新 release 并 `claude plugin update`；`enable --now serveros-daily.timer`；装原生告警投递 `release.py alert-worker --conversation <id>`；在 `~/.claude/settings.json` 的 `SessionStart` 下注册 `drift_sync.py` | 逐条见 §7.2。五条都是**外部/共享面动作**。 |

**F 里第 4、第 5 条有一个共同形状**，值得单独点出来：它们是**一个设计好的投递跳从来没装过**，
而不是装坏了。第 4 条的 drop-in 在（`10-botmux-token.conf`）、`.service` 与 `.timer` 都
`not-found`，所以那 740 条 `last_notified_at` 来自 `alert_digest.record()` 而不是
`alerting.record_delivery()`；第 5 条在 §9.20 之前**只是表格里的一句散文**，现在有了自己的
读数：`serverosctl device agents` 报 `NOT WIRED`，`serverosctl information watched` 报
`OVERDUE`，契约把它记成 `UNWIRED_ON_THIS_HOST` / `NEEDS_HUMAN`。

### 12.11 本报告没有说什么

- **没有说无人值守已启用**。计划 §P12 的判据是「一条完整日循环 S6 实际通过前不声称」，
  而 S6 要求真实 delta → 认识 → current board → 授权焦点 → GPT 设计/独立审阅 → 实施/Quay →
  验收 → 报告回流**跑通一次**。本机没有跑过那一整条，所以不声称。可以独立启用的已验证有限
  功能（采集、个人播报的**安装**）各自标了生命周期与权限。
- **没有把计数或费用填成估计值**。本报告里出现的数字全部来自上面点名的 log 文件。
- **没有用 fixture 顶替无权限的 Live 场景**。§12.7 的 Laya 是这一条的例子：判词是
  「被测对象不存在」，不是「测了没通过」。**计划 P11 的十条验收场景逐条落位在 §12.12**——
  那一节此前不存在，而一张写着「做完了」的终报和一列没被记过的验收项放在一起，
  读的人会以为那十条已经过了。
- **代码审阅通过不是上线证明**。本轮 A7 的 fresh-session 验证只证明**加载**，不证明任何一条
  业务需求被自动化测试真的服务过。

**这一节的边界**：它写的是**判词**。任何一条的详细证据、反例与现场输出都在 §3 与 §9 对应的
小节里，本节不复制它们——复制会漂移。而**这次写终报的过程本身就订正了四处**，逐条记在这里，
因为它们不是笔误，是同一族：`declared 25` → 37（读错了字段）、worktree `fb97f77` → `40ffaa5`
（一格里数了三个列了四个，且没跟着重测）、§3.6 的两笔账（同一份台账给过相反判词）、
以及 §11.2 2b（**判据 `--branches` 看不见 tag**，而结论句说的是「任何远程 ref」）。
四处都会让一个读者拿着量具的绿当产品的绿。

### 12.12 P11 的十条验收场景——这一节是它们第一次有落点

计划 §P11 给了一张十行的清单（S1…S10，每行一个「完成标志」）。**在此之前这份台账里没有
对应的小节**，所以这十条的现状只能靠 §9 各节拼出来。补上是必要的，因为一份写着「做完了」
的终报和一列**没被记过的**验收项放在一起，读的人会以为那十条已经过了。

先记一句本仓自己的话，它正好是这一节的口径。`tests/test_live_closure.py` 的 docstring：

> The live run is worth doing and it is not a test — it is an acceptance, and it
> belongs in the report.

所以下表分三列：**实现载体**（代码在哪）、**机制测试**（单测覆盖了什么）、**LIVE 判词与所缺
的具体字段**。按计划 §P11 的要求，缺权限的场景**列具体未满足的字段**，不用 fixture 顶替。

| 场景 | 实现载体 | 机制测试 | LIVE 判词与所缺字段 |
| --- | --- | --- | --- |
| S1 真人 IM 报错 | `records/information/raw/sync_feishu_chat.py`（B1）+ `intake/understanding_delta.py`（B2） | `test_capture_coverage.py`、`test_understanding_delta.py` | **未跑**。缺一条真实用户消息，与一次真实只读调查的产物 |
| S2 机器人/例外 | 黑名单 `DRIFT_WATCHLIST.txt`（采集侧 `drift_sync.py:71`、ServerOS 侧 `serverosctl information watched`，§9.10）+ 告警事件链（§3.6） | `test_watch_coverage.py`、`test_alerting.py`、`test_capture_coverage.py` | **一半在跑**：告警事件链活着（登记表 4364 条、740 条带 `last_notified_at`）。缺一条机器人消息被记成 `NONE` 的现场记录，及其读者 |
| S3 会议 | `space/meeting_relevance.py` + `space/meeting_bundle.py`（§9.15） | `test_meeting_bundle.py`、`test_meeting_relevance.py` | **未跑**。缺一份真实会议 manifest——本机四个探针声明全是合成的 |
| S4 自管指南 | 契约读取：`intake/managed_documents.py`（本轮新增）；写入：`intake/document_publish.py` + `runtime/document_transport.py`（本轮新增）；B9 能力本身在外仓 `~/.claude/skills/document-stewardship/scripts/maintain.py`（**只有 `refresh`/`audit`，从不写正文**，见 §9.23） | `test_managed_documents.py`（29 条）、`test_document_publish.py`（55 条） | **真实写入 2026-09-25 跑通**（§9.23）：一个真实 OWNED 章节 ＋ 一个获准镜像章节，正文 OK、媒体失败后只补那条边、人改保留、权限不扩大、重跑不双份、base 动了停写。两边都在 ServerOS 自己的验收文档上，不是一个真实的 Published Knowledge 对象 |
| S5 协作文档/SOP | `runtime/report-publication-contract.yaml` + `intake/support_handoff.py`（§9.19） | `test_report_publication.py`（31 条）、`test_support_handoff.py` | **未跑**。缺两个锚（收件人与会话）——它们 UNBOUND，所以当前读数是「待投递」而不是「已通知」 |
| S6 日常工作 | `intake/daily_cycle.py`（`select_focus` / `review_gate` / `invalidate_claims` / `fingerprint` / `instant`）+ `runtime/workbench.py` | `test_daily_cycle.py`、`test_workbench.py` | **信息半环 2026-09-23 真的跑通了；工作半环卡在板投影的 producer 上**（§3.9、§12.5）。四条实测全 EXIT 0 且留下真实文件：`information captured`（98,685 B）、`information watched`（1,944 B）、`work understanding`（44,067 B）、`report daily`（写出 `daily-report.md` 3,891 B ＋ `publication-receipt.json` 1,209 B，其 JSON 里 `blocking` / `claimed_done_without_a_pass` / `fallbacks` 三项全空）。工作半环的读数：`work focus` 判 `BLOCKED`、理由是 `STALE_BOARD`、`selected` 空；`work grants` 0 条委派、`work admissions` 0 条在办。所以缺的不是"上弦"——是 ServerOS 自己点名的那个 producer：`focus.not_produced` 逐项列出板**还没有生产**的字段（`priorities_unproduced: 34`、无 dependency edge、`grant_ref` 一句写着 "The gap is the board's producer, not the store's"）|
| S7 卡片 | `runtime/cards.py` + `runtime/card-definitions.yaml` + `runtime/card_delivery.py` | `test_cards.py`、`test_card_delivery.py`、`test_presentation.py` | **两面都在生产链上了。** 告警面 2026-09-22 起取 `serverosctl card alert`；status 面（每小时那张主卡）2026-09-23 起取 `serverosctl card status --with-frozen` 并写 `card record-delivery` 回执（`hwz-ops@a8272d4`），分工是 ops 量网关、措辞归 ServerOS。**原判词的两处失实已更正**：这一跳没有被"两个活动工作树"压着——`alert-card-v2` 一行没改 `monitor_card.py`，只有 `SRV-0011` 改了它，且改的是共享网关端口声明化那一段，与这一跳无关 |
| S8 共享基础设施 | MarketOps 三个 adapter（Laya/Jev/Serper）+ ServerOS `abilities/provider-contract.yaml` | `test_provider_readiness.py`、`test_provider_adapter_surface.py`、`test_memory_provider.py` | **前两句已不成立、第三句仍成立**：受管 Laya 服务在跑（单元 `active` ＋ `enabled`，恰好 1 个进程，权重 `sha256:9d628fd9…` 在位，`/instance` 与记录一致），**缺的只是消费**——ServerOS 侧仍零 consumer（§12.7 已订正）。所以这不是"被测对象不存在"，而是"provider 侧的对象在、被测的消费不在" |
| S9 重启和权限 | `runtime/coordination.py`、`runtime/agent_control_store.py`、`runtime/daily_report.py`（receipt）、`runtime/alert_digest.py`（冻结块）、`runtime/report_publication.py`、`abilities/provider_instance.py` | `test_coordination.py`、`test_alert_digest.py`、`test_report_publication.py`、`test_provider_readiness.py`、`test_daily_cycle.py` | **机制被单测覆盖**（全套 4723 passed），**但「按不同水位恢复」这条链没在真进程上跑过**。最能说明这件事的现场就在 §12.2：`serverosd.service` 至今跑着 promote 之前那一版，因为重启它是 HUMAN_EXECUTED。缺一次安全窗口内的真重启 |
| S10 生产边界 | `runtime/managed_mutation.py` + `managed-mutation-contract.yaml`、`runtime/approval.py`、A9/A10 的 `READY ≠ 切流授权` | `test_managed_mutation.py`、`test_approval.py` | **只在「没有访问」的形态下成立**。缺真实生产访问（A9/A10 的 Binding 是 UNBOUND），所以「有真实访问才读观察」这句话本机**没有反例可测** |

**十条里，S7 的两面与 S2 的告警腿都在生产链上真在跑；S6 的信息半环 2026-09-23 第一次
跑通并留下产物，工作半环仍卡在板投影的 producer 上。** 其余各条没有一条跑通过。
这不是「测了没通过」——两张表分开写，是因为把它们并成一列
`FAILED` 会让「对象不存在」看起来像「产品坏了」，而这两种状态下该做的事完全不同。

---

### 12.13 2026-09-23 收口轮：新增、更正与在途

本节只记**上面各节还没有的**东西，不复制已经写下的结论（复制会漂移）。

#### 结清一项：`FINDING-defer-objects-gone-20260920.md` 的「活风险」不成立

那份 FINDING 写于 **2026-09-20 12:03**，把 4 个「仍 DEFER 却仍被认为在盘上」的对象列为
活风险。逐条复查：**4 个全部有当日晚些时候的处置收据，且存活副本全部实测在盘上**。

| FINDING 列为活风险的对象 | 处置收据 | 存活副本（本轮实测） |
| --- | --- | --- |
| `state/browser/history/beijing-20260918` | `wave3-state.json` `OBJECT-2`：单次 `mv`（同文件系统，inode 保留） | `state/browser/archive/beijing-20260918`，20,690 files / 3,940,834,338 B |
| `envs/history/beijing-20260918/quay-litellm-probe` | `wave2-A.json` `task_2_quay_unique_history`：独有历史 fetch 进 `src/quay`（不 push） | durable refs `refs/heads/migration/beijing/quay-*` **8 条在**，`fsck --unreachable` **0 objects**；未跟踪态归档在 `artifacts/ops/migration/beijing-20260918/quay-litellm-probe-dotquay/`（167 files） |
| `migration-preservation-20260918/quay-quarantine` | `wave3-coordinator-mp18-and-sudo.json`：整个 mp18 根 `RETIRE` | 同上（mp18 是 transport 侧） |
| `migration-preservation-20260918/serveros-release` | 同上，`content_proof` 为 **sha256 逐文件比对**（1408/1408 行相同） | `artifacts/serveros-releases/beijing-20260918`，1408 files |

**一条量具教训，本轮我自己差点制造的假差异**：收据记 mp18 存活副本为 33,578,681 B，
我用 `du -sb` 读得 33,579,207 B，差 526 B，一度像是内容变了。改用
`find -type f -printf '%s\n' | awk` 求和得 **33,578,681**，与收据逐字一致 —— 526 B 是
`du` 的目录项开销。**字节对账必须用与记录同一个量具。** 同类前科见 memory 里
「`git -C` 会把相对路径拼两遍」。

口径与 FINDING 一致：**本轮没有测到任何缺失字节，也不主张任何对象丢失或任何 actor 的行为。**
结论只是：FINDING 与收据的时序差是「12:03」与「当日更晚」，不是记录缺失。

#### 落盘：磁盘分级提案

`artifacts/ops/tokyo-convergence/STATE-ROOT-GRADED-PROPOSAL-20260923.md`。本轮快照为全新采集，
未复用任何更早快照；该文件没有移动、重命名、chmod、删除或链接任何对象。要点：

- `fs_hygiene` 94 entries：`MISPLACED 0` / `OVERSIZED 0` / `DUPLICATE_SEMANTIC_ROOT 0`。
- `UNKNOWN 36` 不是缺口：其中 10 条是用户 shell 环境文件、7 条是发布目录族、6 条是 HOME
  顶层历史文档。**「未声明」是正确的答案**，把它们批量升成 `CANONICAL` 才是制造事实。
- `LEGACY 1`（`~/backups`）是本提案唯一一条「该由人决定」的正向动作。
- 三条真边界问题（`state/serveros/{archive,document-stewardship,laya-shared}`）**不裁决**，
  等各自的生产者/§41 收敛。

#### 份清单实测并不相同，且不应相同；已加一条测试并写明它的局限

`state` root 的既有名字有**三份**清单。实测：

```
① conformance.EXCEPTIONS["state"]          8: README.md archive document-stewardship laya-shared migrations serveros.db serveros.db-shm serveros.db-wal
② state-root-contract#preexisting.entries  8: 与 ① 相同（已被 test_state_root.py 钉住）
③ fs_hygiene.AWAITING_A_RULING             4:       archive document-stewardship laya-shared migrations
```

**差集不是遗漏，是两种例外**：① 多出的 4 项是**文件**，例外理由是「先于规则存在」；
③ 的 4 项是**目录**，理由是「`paths.py` 明写刻意排除、裁定未下」。`paths.py:104-108` 逐字
回指「理由镜像进 `fs_hygiene#AWAITING_A_RULING`，那才是报告实际读的列表」。
所以断言 `③ == ①` 是**错的**（本台账上一稿就是这么写的）—— 它会当场转红，而唯一"修法"
要么把 4 个文件谎称「待裁定」，要么让它们变成 `CONTRACT_GAP`。

已加 `tests/test_fs_hygiene.py::test_every_name_awaiting_a_ruling_is_one_conformance_already_covers`，
断言单向包含 `③ ⊆ ①`。**它的局限写在 docstring 里**：它**抓不到**产生本次修改的那次失效
（`archive` 与 `laya-shared` 当时三份清单**全都没有**，漂移的是 `paths.py` 里的**散文**，
没有测试能读注释）。一个读真实 `state/serveros/` 的测试才抓得到，但那是按机器而异的测试，
不属于单元套件。**这条缝关不上，只能靠人工纪律。**

今日兜底实测：`state/serveros/` 下 9 个目录**全部**落在
`STATE_SERVEROS_TOP_LEVEL ∪ AWAITING_A_RULING` 内 —— 当前没有名字落在所有清单之外。
但 6 个**文件**里有 2 个落在所有清单之外：`TOKYO-SERVEROS-CLOSURE.yaml` 与
`.pre-correction-20260917`（见下条）。

#### 新发现：两个 CLOSURE 文件既没有写者，也不在任何清单上，而生产卡面指向它

`state/serveros/TOKYO-SERVEROS-CLOSURE.yaml`（65,177 B，2026-09-17）与它的兄弟
`.pre-correction-20260917`。全机 grep **找不到写者**：serveros 仓零命中；唯一命中的
`ops/scripts/tokyo-monitor/seal_card.py:84` 只是**引用**，逐字把读者指向这份文件。
而 `content` 停在 `canonical_sha 0b2f3f5`，serveros main 现为 `8a1dace`。

**生产卡面（每小时运行的 `tokyo-status-card.timer`）在把用户指向一份冻结且无人维护的记录。**
与 `archive`/`laya-shared` 是同一种形状（名字在 state root 里、不在任何清单上），区别是它们
是文件，`fs_hygiene` 因此判 `UNKNOWN` 而非 `MISPLACED`，没有产生「一次批准就能执行」的
判决，所以没造成事故。**不做**：改它就是改生产卡面文案，属对外可见变更；且
`artifacts/ops/tokyo-convergence/state-serveros-retired-20260920/` 的三份备份说明它
**曾被有意退役过一次又被放回** —— 没弄清这段历史前不动。

#### 本轮回合的更正：三行失实 + 一条在途缺陷 + 一条我上一轮的误判

1. **Jev adapter「UNBOUND：无 key」失实。** 实测 `~/.secrets/vault/typesafe.env` 存在
   （0600，489 B，`TYPESAFE_API_KEY` 108 字符、非空；**值从未输出**）。它**未注入进程环境**
   且 `MARKETOPS_DECISION_PROVIDER` 未设。**阻塞在接线与出站授权，不在凭据缺失** ——
   这在终报里是两句不同的话：「我们没做」对「我们没权限」。
2. **Serper adapter「没有跑过真实查询」已过期**（`b0a4464`）。已跑过一次真实付费查询，判 LIVE。
3. **Laya adapter「盘上没有权重」已过期。** 权重在 `~/caches/models/laya-multilingual/052592a…/`
   （21 文件 / 647 MB，revision 与 binding 的 pin 一致），并已作为受管服务运行。
4. **`monitor_card.py` 的生产卡面把「声明的端口」印成「观测」。** 实测：探针一次都没连上时
   `_gateway_model()` 仍返回 `endpoint = ':26510'`，而 `_gateway_entry()` 诚实返回 `''`;
   该 docstring 自己写着「卡上『本机入口』这一行说的是后者（观测）」。`:67` 的注释还把这件事
   归给一个**在本文件里不存在**的 `_gateway_endpoint`（全文件 grep 只有那一行注释提到它）。
   **在途，不由本轮修**：`worktrees/hwz-ops/SRV-0011`（分支 `serveros/system-v1-20260920`）
   的未提交改动已在修这一段（注释改成「这是一个声明值，不是观测结果」，取值改成
   `res.get('gateway_port') or GATEWAY_PORT`，无 `KeyError` 隐患）。按 §2，不并行改同一文件。
   **风险**：那份修复**尚未提交**，工作树若丢则修复丢。
5. **更正我上一轮的一条判读，并同时更正那条判读里的一个数字**：`~/CLAUDE.md` 说
   「`.codex/skills/...` 在本机不存在」。实测 `.codex/skills` **存在**（顶层 **15** 项 ——
   我上一轮记的「29 项」是错的），所以我曾想把它记成第二处失实。**复查后不成立**：
   该句的实质是「策略点名的脚本没有可跑的面」，而 `.codex/skills/workspace-management/`
   **整个 `scripts/` 子目录不存在**（`capacity_check.py` 与 `map_check.py` 都不在，连容器
   目录都没有），所以那句的实质**成立**；一个同名目录存在不足以推翻它。
   **只有 `HWZ-MAP.md` 那条是真矛盾**（策略说不存在、实测 15,641 B 在 `ops/maps/`，
   mtime 2026-09-18），且 memory 已记为预期漂移（东京地图集在该 CLAUDE.md 渲染之后才编写）。
   （这条同时说明为什么「数一下目录里有多少项」不能当判据：数出来的数本身也可能错。）

#### MarketOps 侧一条架构冲突（不是接线，是按构造不同）

设计说两侧共用同一个 endpoint 与**同一份 resident checkpoint**。实测：MarketOps 的
`worktrees/marketops/mvp/marketops/adapters/laya.py` **不走共享服务** ——
`:318 import laya`、`:142` 自述「整个进程一个 Router」、`:88` `_LOCAL_MODEL_KEY = "english"`，
全仓 **0 处**引用 `laya-shared-binding.yaml` 或该 socket。所以它按构造会自己加载**第二份**
resident checkpoint，且 pinned 的还不是共享服务用的 `laya-multilingual`。
这不是「少接一根线」，是两侧的**实现形态**不同。

#### §9 飞书：地图三处计数已修，L3 刷新受阻有据，最终回执未发

- `hwz-ops@1993042`：空间总数 24 → **28**（4 managed + 24 external，`+space-list` 27 加
  `My Document Library`）；`配置文档` → **`公开文档`**；`公开文档` 0 → **2 节点**；
  `AI平台知识库` 6 → **5 节点**；两处 UNRESOLVED 关闭。
  （附带澄清：§B 标题「8 节点」**是对的**（顶层 5 + 子孙 3），上一轮记的「8 vs 7 待裁定」
  是**假警报**。）
- **L3 索引刷新受阻且原因已记录**（`FEISHU-TREE-MAP.md:208`）：控制面
  `workengine federation <domain> inventory --evidence-run …` 需要活 store 里的 Run，
  本机 `runs` 表 0 行，且**不得伪造 Run 来点亮**。
- **最终云端回执本轮未发**：现在 03:17，处于 `monitor_card.py:103-106` 定义的
  `[22:00, 09:00)` 静默窗口内。**这不是缺权限，是窗口未到，且不得为收口而破静默。**
  要发须在 09:00–22:00 之间。

#### 孤立的外部权限 / 人的动作 —— 单独一类，不与上面混读

这些是本轮**确实没做**的，每条都写清为什么：

| 动作 | 为什么本轮不做 |
| --- | --- |
| 重启 `serverosd.service` | HUMAN_EXECUTED，需安全窗口（§12.2、S9） |
| Jev 真实调用与对照实验 | **出站**，本轮授权明确不含外联 |
| 打开 `serveros-daily.timer` | 板投影没有 producer，上弦只会产出「看起来像工作循环、`select_focus` 却每次 BLOCKED」的日产物（§12.5） |
| `~/backups` 退役 | 需人先读清单判定唯一内容 |
| 两个 CLOSURE 文件处置 | 改生产卡面文案，对外可见 |
| 推进 `/data/home/vince/workspace/ops` 到 `origin/main` | 该 canonical 检出**同时是生产部署点**（`tokyo-status-card.timer` 的 `ExecStart` 就在它下面），当前 `behind 2` |
| 合并 `SRV-0011` 的卡片修复 | 归属他人的活动工作树 |
| `PENDING_CONSUMER` 四处移除 | 被 `srv-0005-e2e-evidence` 的活动未提交工作树挡着（§12.4 契约 `consumers:`） |
| 发飞书最终回执 | 静默窗口内（见上） |
| LiteLLM 生产切流 / MarketOps 外联 | §1 逐字不授权 |

#### 本节没有说什么

没有说 21 个职责「已完成」。没有说「仅剩外部授权」——上表之外仍有**归属他人**（SRV-0011、
SRV-0005）与**等生产者**（板投影、document-stewardship、会议 manifest）的缺口，它们既不是
权限问题也不是本轮能自行闭合的问题。

---

### 12.14 §J09：运行中的节点现在能证明自己是谁（`b8f7d21` + `e5118e5`）

**缺的是两处，缺法不同**：`ARTIFACT.json`/`RELEASE.json` 都不记 source commit（ability
pin 只在 README 散文里），而 daemon 的 `node.status` 不报任何 revision。于是 `@50a86fa`
按 `node_revision` 自己的 docstring 只是一个 promote 当时的**声称**。

**三处落点**：`build.py` 的 `artifact_manifest` 增 `source` 块（commit/ref/**tree_clean**/
未提交数/basis + ability pin）；daemon 启动经 `_HERE.parent` 读一次并冻结
（**不调 git**），另加 `_pointer_state` 在**查询时**把身份与 `current` 当面对质
（身份冻结因为「我是谁」不变，指针实时因为 promote 会移动它而什么都不重启）；
`serverosctl status` 印出六行且 **DRIFT 退 1**。`tree_clean` 是承重字段——从脏检出构建
**有** commit 而**没有**内容身份，取不到时如实记 basis，**不回退到分支名**。

**一处取舍**：`source` **不进** `runtime_truth` 的逐字节比较。重算发生在**检查者**的检出
（canonical），构建发生在 worktree，两者 HEAD 与未提交数都不同 ⇒ 当作内容比会把每个正确
安装读成漂移。排除不等于不检查：`tree_hash` 覆盖整个 artifact，`node_revision` 用更强
的问法核它。真机上 7 条漂移路径全是 `.claude-plugin/*` 一类，**没有 `ARTIFACT.json`**。

**现场**：`current -> revisions/serveros-j09-identity-20260927@b8f7d21`，
`release … · 3.1.0+srv0004.20260927a · tree 6deb6de3fa61`，
`source b8f7d210161b (serveros-completion-20260925, clean) · ability-management@368c1be63a0d`，
`pointer AGREEMENT`。用户 §3 列的八项逐项对上。restart 前的同一条命令报两行 `UNKNOWN`
并说明理由——那不是故障，是旧 daemon 说不出自己是谁的如实回答。

**测试 8 条**（J09 的 5 个用例 + 排除逻辑的两道相反判据 + CLI 3 条），
**变异 3 处**（恒判 AGREEMENT / 回退分支名 / 清空 `ATTRIBUTION_ONLY`）全部被咬住。
cachebuster 移到 `srv0004.20260927a`：四成员字节变了而文件名没变，同一版本串不能再
描述新载荷。逐项证据见 `SRV-0011/RUN-0001/verification/R9-j09-runtime-identity.md`。

**本片没做**：没有建第二版本数据库；没有让 daemon 去 Git 猜版本；没有把标签当证明。
**遗留一条真实发现**：`runtime_truth` 的 node 行仍报 `SOURCE_RUNTIME_DRIFT`，因为
canonical `src/serveros` 还没有这两个提交（重算得 `srv0003`，artifact 是 `srv0004`）。
这是预期的，等提交进 canonical main 后自然转 OK；它属于 §6/§12 的收敛范围而不是本片。

**一处并发事实**：本文件在 **02:14:12** 被另一个 actor 追加了 8 行（评论本片的
§9.50.4 限定与 cachebuster 移动）。不是我的写入，位于同一 worktree，按政策**不碰、
不丢、不代为提交**，仅此记明。
