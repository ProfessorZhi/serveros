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
                                                                       fb97f77 [serveros/fabric-integration-20260922]
canonical   /data/home/vince/workspace/worktrees/ability-management/fabric-integration-20260922
                                                                       074a518 [integration/fabric-20260922]
packs pin   authority.revision = e7d20f3b50e1a86c9998b5dff3f519fa52b4aa1d
```

`fabric-integration-20260922` 是 `origin/main` + 10 个线性 commit（`b007c8c` …
`074a518`）上的集成分支，本轮三个 commit 落在它上面（`aef122d`、`dc23707`、
`2f77b02`、`fb97f77`），已 non-force 推到同名远端分支。

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
| A7 | 自动化测试 | automated-testing | `skills/automated-testing` + `plugin/profiles/testing.yaml` | Skill + Plugin composition `serveros-testing` | ce6e103（Skill）/ f5e563b（profile） | 目标 CC 以正规 install 加载 | 原生 | `1.0.0+srvtest0001.20260922b` | DONE | DONE | **ENABLED** | 见 §3.3 |
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

登记表现状：`serverosctl alert list --json` exit 0，**4117 条**（795 OPEN / 3322 RESOLVED），
其中 701 条带 `last_notified_at`。

缺口在三处，都实测过：

1. **没有冻结 payload / payload hash。** 设计 `BROADCAST-ALERT-DESIGN.md:80` 要求
   `frozen presentation payload + payload hash + intended rows/versions`；
   `grep -rn 'payload_hash' runtime/*.py` → 0 命中。这是读过的 absent，不是没找到。
2. **`alert_digest.record()` 盖的是活登记表，不是发出去的那一版。**
   `alert_digest.py:730 _shown_pairs()` 把冻结文档压成 `(key, kind)`，写回时
   `:790` 取 `alerts.get(key)` 的**当前**值，`:811` 和 `:822` 于是把注册表的现值
   记成「这一版就是这么播的」。compose 与 record 之间若发生 severity 升级，
   记录会把它写成以新 severity 播过。（相邻的 `alert is None` 分支 `:792-800`
   反而正确写了 `None`，说明这个坑是知道的。）
3. **ServerOS 原生投递那一跳从没在这台机上跑过。** `~/.config/systemd/user/` 里有
   `serveros-alert-sync.service.d/10-botmux-token.conf`，但**没有**
   `serveros-alert-sync.service`、也没有 `.timer`；`is-enabled` → `not-found`，
   `journalctl -u` → 无条目。所以那 701 条 `last_notified_at` 来自
   `alert_digest.record()`（`alert_digest.py:821`），**不是**来自
   `alerting.record_delivery()`（`alerting.py:947`）。unit 模板在树里
   （`runtime/serveros-alert-sync.service`/`.timer`），安装动词是
   `scripts/release.py:528 cmd_alert_worker`，它在没有 `--conversation` 时拒绝。

第 3 条同时解释了 §8 里那个「孤立的 drop-in」：它不是残渣，是**一个设计好的跳没装**。

还有一处**已测但没接线**：`monitor_card.py:994-1009`（每小时那张主卡）只调
`resource summary` / `resource containers` / `runtime list` / `runtime status`，
从不调 `serverosctl card status`。所以 `presentation.status_model()` 与
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
| serveros（`dc23707`，带覆盖） | 4207 passed, 23 skipped, 3703 subtests passed, 0 failed（459.73s） | 同上 |
| serveros（起点） | 3 failed, 4171 passed | — |
| ability-management | **105 passed, 2 skipped**（`074a518`）；三个 renderer `--check` 干净 | 加 `SERVEROS_BUNDLE=<serveros worktree>/plugin` |

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

### 7.2 只剩要人做的四件（其余都已做完）

| # | 动作 | 为什么不是本轮能做的 |
| --- | --- | --- |
| 1 | **重启 `serverosd.service`**（PID 2615936，起于 11:00:26） | `current` 已是新 revision，shim 也已指向它，所以**新开的进程拿到的是新代码**；但长驻进程仍在跑 promote 之前那一版。重启会打断这台共享机上别人正在跑的东西。等一个安全窗口，`HUMAN_EXECUTED` |
| 2 | **把 CC marketplace `hwz-server-os` 指到新 release，并 `claude plugin update` 工作台** | 遗留目录 `hwz-server-os/current` 声明的 plugin 名是 `hwz-server-os`（3 skills），新 release 声明的名字是 `hwz-server-os-linux-work`（5 skills）。指过去等于**给已装 plugin 改名**——用户可见的身份变更，不是基础设施细节。已装那一份仍是 `3.1.0+srv0003.20260915a` |
| 3 | **给日渲染上弦**：`systemctl --user enable --now serveros-daily.timer` | 单元已装、已手工跑通一次（§3.9），但没有启用。单元头部写明「enabling is the act §8 puts on the approved list」——自动产出与手工验证是两个动作 |
| 4 | **装原生告警投递那一跳**：`scripts/release.py alert-worker --conversation <id>` | 它与它的 timer **从没装过**（§3.6 第 3 条），所以 701 条 `last_notified_at` 来自 `alert_digest.record()` 而不是原生投递。这个动词在没有 `--conversation` 时拒绝——而「往哪个会话发」是一次外联决定 |

四条都是**外部/共享面动作**，不是内部架构选择——所以它们出现在这里，而不是被包装成
「请用户决定」。其余七条已按设计落地。

## 8. 卫生发现（只给位置，不给值）

| 位置 | 现象 | 为什么值得看 |
| --- | --- | --- |
| `~/.config/systemd/user/serveros-alert-sync.service.d/10-botmux-token.conf` | drop-in 存在，但它属于的 `serveros-alert-sync.service` 与 `.timer` **都不存在** | 不是残渣：**一个设计好的投递跳从没装过**，见 §3.6 第 3 条。装它是 `release.py cmd_alert_worker` 的活，且需要 `--conversation` |
| `hwz-monitor.service` | dead | 死服务 |
| `~/.claude/settings.json` | `ANTHROPIC_AUTH_TOKEN` 内联，且 `ANTHROPIC_BASE_URL=http://127.0.0.1:26500`，`permissions.defaultMode: bypassPermissions` | 明文凭据落在 HOME 下，而策略说凭据只住 vault |
| `sanitize.default_files()` | fail-open | 脱敏失败时放行，方向反了 |

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
| P2 | 黑名单 | `records/information/raw/DRIFT_WATCHLIST.txt`（5 行，1 条 `wiki-litellm-work-doc`） | **两侧都有读者了**：采集器侧 `drift_sync.py:71` `load_watchlist()`（`DRIFT_WATCHLIST` 可覆写，缺失时回退内置默认，支持行内 `#` 注释）+ `locate()` 按 `link_id` 反查；ServerOS 侧**本轮补上** `serverosctl information watched`（`intake/watch_coverage.py`，§9.10） | `drift_sync.py`（自称 SessionStart hook driver） | **上一版这一格写的「not read」是错的**（采集器侧读得好好的），2026-09-22 改。**现在只剩一件真的缺**：注册 `drift_sync` 为 SessionStart hook 的 `settings*.json` 在本机 **not found**，所以它是「装好了、手动能跑、没有上弦」，与 §3.9 的日报渲染腿同一形状。上一版说「ServerOS 这一侧没有读者」时**把 `last_verified_at` 也连写了进去，那一项是错的**（`intake/capture_coverage.py:436` 一直在读，§9.6 就是为它写的）；真正零命中只有 `DRIFT_WATCHLIST` 与 `drift-sync-state` 两个串，缺的是 **watchlist 与 manifest 的 join**——本轮已补，见 §9.10 |
| P3 | people/project/topic 卡 | 手写 markdown：`records/information/understanding/{people,projects,topics}/*.md` | **两侧都已接线**：读 `serverosctl work understanding`（`intake/understanding_delta.py`）、写 `serverosctl work understanding-patch`（`intake/understanding_patch.py`） | — | **两半都补上了**：读的一侧见 §9.7（14 份卡逐份对账到源，`BEHIND 3 · UNMEASURABLE 7 · UNTRACEABLE 4`）；写的一侧见 §9.9（15 条提议全 `AWAITING`，`--write`+`--verify` ok）。设计要的「最小 incremental writer」不再缺口。**仍未 LIVE**：没有任何消费者读这份 patch |
| P3 | Minutes / MeetingBundle | 无 Python 类。`space/meeting-policy.schema.yaml:30` 声明结构；内核 `space/meeting_relevance.py:69`；**生产者 `space/meeting_bundle.py`（§9.15）** + 契约 `space/meeting-bundle-contract.yaml` | ~~`python3 space/meeting_relevance.py --request`（**未接 serverosctl**）~~ **已接线**：`serverosctl space meeting`（`--request` 与 `--sidecars` 两条入口） | — | 内核 working，**接线已补**（§9.5）；**生产者也补上了**（§9.15）：会议自己声明的 sidecar 现在能组装成内核收得下的 bundle，`COMPLETE` 才把问题递给内核，`INCOMPLETE`/`MIXED_SCOPE` 只给读数不给问题。**仍未 LIVE**：本机没有任何真实会议 manifest 可测，四个探针声明是合成的（`RUN-20260922-raw-coverage/meeting-bundle/*.json`） |
| P3 | 记忆账本/投影/scope | `memory/{record,projection,scope,handoff}.py` | `serverosctl memory {audit,project,status,subject}` (`cli.py:6479`) | — | working；`docs/memory-fabric.md` 逐条点名这些模块 |
| P4 | 文档关系边 | `relations/project.py:61`（`serveros/relations.v1`），`relations/schema.yaml:29` | `python3 project.py --records ROOT --write relations/current.jsonl` | — | **partial**（比上一版进了一步）：8 个谓词里 3 个有生产者（`belongs_to`、`derived_from`、`attached_to`），其余 5 个 `declared_only`。本机实测 239 行 / 19 条报出，见 §9.8；另 5 个谓词逐条查过，**本机没有载体**，不是差功夫 |
| P4 | Feishu 空间适配 / 共享 publisher | `skills/feishu-space-management/`：**只有 SKILL.md 文本，本仓没有代码**。`skills/document-stewardship/`：本仓同样只有文本，但**外仓有代码**——`~/.claude/skills/document-stewardship/scripts/maintain.py`（publisher 循环：取 revision N → 定位 → 校验 → 候选 patch → 重取仍为 N? → apply/STOP → 重取 → 校验），且 `plugin/profiles/linux-work.yaml:119-123` 声明它 `available` | — | — | partial。**上一版这一格把两者合成了一句「只有 SKILL.md 文本，没有代码」，那是错的**：`document-stewardship` 在外仓有实现，且 ability-management 注册表（HEAD `8d98aff`）**不持有**它——与那 8 条跨仓红同族 |
| P5 | 员工 KB 生产者 | `src/litellm-support/scripts/build_agent_kb.py`，输出 `dist/employee-kb/`（`dist/` 被 gitignore） | `python3 scripts/build_agent_kb.py` | — | working，但**在 ServerOS 之外** |
| P5 | KB 消费 | `intake/support.py:187,432,534,697,855,977`（另 `read_entries` 的第三个字段 `unrecognised`，见 §9.14），契约 `intake/support-contract.yaml` | `serverosctl support {entries,match,answer,check} --kb-root <path>` (`cli.py:7206`) | — | working，但 `--kb-root` 必填且**没有任何东西把它接到 litellm-support 那棵树**。**§9.14 修掉了一个把「没读到」印成「干净」的洞**：契约写就的那棵树读数不变（`21 entries` / `21 index rows` / exit 0），员工树现在 `entries`/`check` 都是 exit 2 并点名读过哪些文件、哪个 pattern 没匹配上 |
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
| Laya adapter | `worktrees/marketops/mvp/marketops/adapters/laya.py`，`LayaDecisionProvider:128` | **partial**：盘上**没有权重**（`find *laya*` 无模型目录），`LAYA_CHECKPOINT_DIR` 只在 `mvp/.env.example:42` 出现且为空 |
| Jev adapter | `…/adapters/jev.py`，形状镜像 `laya.py` | **UNBOUND**：无 key，`MARKETOPS_DECISION_PROVIDER` 未设 |
| Serper adapter | `src/marketops/marketops/search/serper.py`，`SerperSearchProvider:24` | working（adapter+port+tests）；凭据在 `~/.secrets/vault/serper.env`（0600，152 B）。**没有跑过真实查询**，所以 LIVE 不可声称 |
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

**判词。** 用工作树跑 `skills/serveros/scripts/architecture_check.py`（`--canonical-ref HEAD`，
`--ability-repo` 指向 fiber 工作树的 `HEAD`，`--ops-repo` 用产品自己的默认值
`<WORKSPACE_ROOT>/ops`）：

```
architecture_status: NONCONFORMANT
findings: 6   blocking_high_open: 1
  HIGH    ROOT_TOKEN_UNRESOLVED  {unresolved: [KNOWLEDGE_ROOT, OPS_ROOT, RECORDS_ROOT, SRC_ROOT, TEST_ENVS_ROOT],
                                  declared: [], roots_source: ops/workspace/environment.yaml}
```

`blocking_high_open: 1` 就是它。**这不是本轮代码的问题，也不是我调用方式的问题**——
`ROOT_SOURCES["ops_repo"]` 的默认值就是 `<workspace_root>/ops`，我用的是产品自己的默认。

**根因：`root_tokens()` 读的是东京已经退役的那份文档。** 契约的
`roots_source.locator` 是 `ops/workspace/environment.yaml`、`key` 是 `roots`。实测：

| 事实 | 量 |
| --- | --- |
| 活的 `ops/workspace/environment.yaml`（`main`，8643 B）里 `roots` 这个词 | **一次都不出现**；顶层键是 `schema: hwz/environment.v2` |
| 它能不能被 YAML 解析 | **不能**：`ScannerError … line 70, column 11`（`note:` 的值以反引号开头） |
| 带 `roots:` 的那一份在哪 | 只在 `ops/archive/beijing-20260918/workspace/environment.yaml:7` |
| 东京 v2 把绑定交给了谁 | 文件自己写着 `binding_authority: serveros:fleet/profiles.yaml#linux-shared-multiuser` |

而产品里**已经有一个能正确回答的解析器**：`runtime/paths.py`（Profile 绑定），实测
`python -m runtime.cli path contract` 在这台机器上零环境变量给出 **12 个根，全在
`/data/home/vince` 下**。`architecture_check.py` 里 `root_tokens()` 对
`PERSISTENT_ROOT` / `profiles.yaml` / `paths.py` **零引用**——它自己又算了一遍。

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
   `TEST_ENVS_ROOT→workspace.test-envs`，这四条是机械的；**`KNOWLEDGE_ROOT→knowledge.corporate`
   是唯一一处语义判断**（Profile 那个名字说的是「corporate」，契约的 token 说的是根目录，
   同一个路径，但两个词不是同一个主张）。把两个词汇表对上是一次**著作行为**，
   而按约定它只能写在 `roots_source` 里（那里现在只有 `owner/locator/key/required_keys/derivations`）。
   **猜一个映射正是这个检查器拒绝做的事**（「reported as unresolved, never guessed」），
   所以本轮不替它猜。

**因此：只做了一半就不做另一半，等于宣布一个没人读的修复——本轮两半都没落**，
它作为 §16 那条外部权限项的一部分列出，并且是本轮**唯一一处「由现有事实决定不了」的
具体业务语义**（不是架构选择，也不是「请人拍板」：映射该由谁署名的答案在契约自己那句
`note` 里——`owner: hwz-ops` 的实例文档已经退役，而接手的 Profile 属于 serveros，
所以这个映射的归属本身要重新署一次名）。

## 10. 后续每包更新哪几行

- 改完一个包，只更新 §3.1 里对应行的四层状态 + §5 表的增删。
- **不新开文件、不新开平台。** 计划原话：「后续每包只更新此处结果/证据，不另建平台。」
- 状态从 `NOT_MEASURED` 变 `DONE` 必须有具体证据（测试名 + 结果，或实测命令 + 输出），
  不能因为「代码在盘上」就改。
