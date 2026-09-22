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

## 2. 本轮 live 状态（2026-09-22 实测）

```text
checkout    /data/home/vince/workspace/src/serveros                     d0a94d6 [main]
worktree    /data/home/vince/workspace/worktrees/serveros/fabric-v1     5deec94 [serveros/fabric-v1-20260922]
canonical   /data/home/vince/workspace/worktrees/ability-management/fabric-v1
                                                                        c993c82 [serveros/fabric-v1-20260922]
packs pin   authority.revision = ce6e1032aae6b2f098fc9f522d72da433b67fdff（五包同一值）
```

**安装面与发布面不是同一棵树**，这是本轮最具体的一个活缺陷：

| 面 | 指向 | 版本 | 时间 |
| --- | --- | --- | --- |
| 已安装 plugin | `~/.claude/plugins/cache/hwz-server-os/hwz-server-os/3.1.0-srv0003.20260915a` | `3.1.0+srv0003.20260915a` | installedAt 2026-09-17 |
| marketplace `hwz-server-os` 的 source | `runtime-assets/hwz-server-os/current`（directory） | `srv0003.20260915a` | lastUpdated 2026-09-16 |
| release 管理的 composition | `runtime-assets/hwz-server-os/serveros-linux-work/current` → `revisions/noc-copy-20260922@c233946` | `3.1.0+srv0003.20260915b` | promote 于 2026-09-22 11:00 |

marketplace 指的是 `hwz-server-os/current`，而 release 把新版本 promote 到了
**另一个目录** `serveros-linux-work/`。两者相差 6 天，且没有任何东西会自己
收敛。装到 CC 里的工作台因此比 release 落后一个版本——这不是推测，是上面三
行读出来的。

已跑的服务：

```text
serverosd.service           active running   ServerOS node runtime
tokyo-status-card.timer     active waiting   每小时 :00（北京整点）
tokyo-alert-digest.timer    active waiting   每 5 分钟
```

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
| A7 | 自动化测试 | automated-testing | `skills/automated-testing` + `plugin/profiles/testing.yaml` | Skill + Plugin composition `serveros-testing` | ce6e103（Skill）/ f5e563b（profile） | 目标 CC 以 `--plugin-dir` 加载 | 原生 | **无** | DONE | DONE | **NOT_ENABLED** | 见 §3.3 |
| A8 | 构建发布 | build-release | `skills/build-release` | Skill | **ce6e103** | 项目 builder/CI | 原生 | 无 | DONE | NOT_MEASURED | NOT_ENABLED | 未装进任何 composition |
| A9 | 生产观察 | production-observation | `skills/production-observation` | Skill | **ce6e103** | 既有 API/CLI/跳板 | **UNBOUND** | 无 | DONE | NOT_MEASURED | NOT_ENABLED | 未装进任何 composition |
| A10 | 生产变更 | production-change | `skills/production-change` | Skill | **ce6e103** | 调用 A9 + 真实 Runbook | **UNBOUND** | 无 | DONE | NOT_MEASURED | NOT_ENABLED | 同 A8；且 READY≠切流授权 |
| B1 | 来源采集 | information-management | `skills/information-management` | Skill | 早于本轮 | `records/information/raw/*` | 原生 | srv0003.20260915a | DONE | DONE | ENABLED | — |
| B2 | 结构化认识 | information-management（共用 payload） | 无独立 artifact | — | — | affected people/project/topic | 原生 | — | PARTIAL | NOT_MEASURED | NOT_ENABLED | 设计就写「可先共用 payload」，独立重算/失效逻辑 not read |
| B3 | 需求 | product-requirements-and-design | `skills/product-requirements-and-design` | Skill | 早于本轮 | — | 原生 | srv0003.20260915a | DONE | DONE | ENABLED | — |
| B4 | 工作管理 | work-management | `skills/work-management` | Skill | 早于本轮 | Work Engine MCP/CLI | 原生 | srv0003.20260915a | DONE | DONE | ENABLED | — |
| B5 | 调研 | research-evidence | `skills/research-evidence` | Skill | **ce6e103** | Serper / 既有专业知识 | **UNBOUND** | 无 | DONE | NOT_MEASURED | NOT_ENABLED | 未装进任何 composition |
| B6 | 方案 | design-and-contract + 专业 Skill | `skills/design-and-contract`、`VenusFennn/Backend-Solution-Design`、`VenusFennn/Frontend-Solution-Design` | Skill ×3 | 早于本轮 | — | 原生 | srv0003.20260915a | DONE | DONE | ENABLED | 旧入口分步迁移未做 |
| B7 | 实现 | implementation-delivery | `skills/implementation-delivery` | Skill | 早于本轮 | — | 原生 | srv0003.20260915a | DONE | DONE | ENABLED | 重叠旧入口未迁移 |
| B8 | 报告发布 | feishu-report | `skills/feishu-report` | Skill | 早于本轮 | `serverosctl report` | 原生 | srv0003.20260915a | PARTIAL | PARTIAL | PARTIAL | 见 §3.5：**没有独立 artifact 是设计选定的落法**，缺的是四处移除没做 |
| B9 | 文档维护 | document-stewardship | `skills/document-stewardship` | Skill | **ce6e103** | feishu-space-management + publisher | 原生（仅正文侧） | 无 | DONE | NOT_MEASURED | NOT_ENABLED | 位置/ACL 侧留在 B-空间 |
| B10 | 人机控制 | human-control | `skills/human-control` | Skill | 早于本轮 | `serverosctl card`/presentation | 原生 | srv0003.20260915a | DONE | DONE | PARTIAL | 见 §3.4 |

图例：`DONE` 已落地并测过；`PARTIAL` 只落了一部分；`NOT_MEASURED` 没测，**不是**失败也不是通过；
`NOT_ENABLED` 已明确没启用；`ENABLED` 在生产/日常链路上真在跑。

### 3.2 为什么 21 不等于 21 个 artifact

- G1 与 A4 各是两个 artifact 担一个职责（`ability-management` + 治理工具；`runtime-management` + `test-management`）。
- B6 是三个 artifact 担一个职责，其中两个是专业 Skill（前后端），设计明说要保留。
- A1 是一个职责两个 artifact：`agent-session-management` 管实时控制，`session-manager` 管历史归档。
- B1 与 B2 是**一个 artifact 担两个职责**：B2 没有独立 payload，设计原话是「可先共用 payload」。
- A7 是唯一一个职责落成 **Plugin composition**（不是因为职责需要插件，而是因为它要装到一个**别的** CC 上——见设计 §10「部署时必须证明 Plugin 从目标 CC 实际加载」）。

### 3.3 A7 的真实 blocker（修正一条先前记错的理由）

先前记录的理由是「`release.py` 没有承载非 node composition 的动词」。**这条是错的**，
本轮实测推翻：

- `runtime-assets/hwz-server-os/serveros-linux-work/` 是完整的 release 布局
  （`revisions/noc-copy-20260922@c233946` + `current` + `previous` + `RELEASE.json`），
  而 `serveros-linux-work` **本身就是** `plugin/profiles/linux-work.yaml` 那个 profile
  composition。所以 `release.py promote` 完全能承载 composition，它天天在这么做。

真正的理由是两条，都比原来那条窄：

1. **两个面本来就是两条路。** `release.py promote` 装的是 **node runtime**——它要求
   artifact 里有 `bin/serverosd`、`bin/serverosctl`、`runtime/daemon.py`、`runtime/cli.py`、
   `runtime/<unit>.name`，还要渲染 systemd unit 并装 CLI shim（实测拒绝时打印的正是这张
   清单，exit 1）。A7 的 artifact 是 `build.py` 产的 **plugin/dist**（`skills/` +
   `.claude-plugin/plugin.json` + `.codex-plugin/plugin.json` + `README.md`），**没有 node runtime**，
   这是设计使然：Testing Plugin 出的是 Skill，不是一个守护进程。所以它走的不是 promote 这条路。
2. **plugin 分发这条路是一次共享面变更。** 持久安装要写
   `~/.claude/plugins/installed_plugins.json` 与 `known_marketplaces.json`，而这台机器
   有并发 agent 在跑，注册 marketplace 不是本轮能替人做的决定。

INTEGRATION 一侧是真测过的，且零共享面变更：`claude --plugin-dir <dir> plugin details serveros-testing`
返回 `serveros-testing 1.0.0+srvtest0001.20260922a`，`Skills (2) automated-testing, test-management`，
`Agents (0) Hooks (0) MCP servers (0) LSP servers (0)`，always-on token 约 218。这满足设计 §10
「从目标 CC 实际加载」。缺的只是持久化那一步。

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

### 3.6 B10 的 PARTIAL 在哪

`serverosctl card` 这个面存在且是「one model, a Feishu payload and a preview, and
NOTHING is sent」——**不发**是它的性质，不是缺口。缺口在**生产链上跑的那张卡不是它渲染的**：
东京主卡与告警摘要由 ops 侧渲染，所以 promote ServerOS 的卡面一个字都不会变。
B10 的 CODE 与 INTEGRATION 成立，LIVE 停在「两套渲染并存」。

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

| 红 | 位置 | 性质 | 处置 |
| --- | --- | --- | --- |
| `test_real_registry_matches_real_bundle` | ability-management `tests/test_materialization_contract.py` | **跨仓库**：它读 `src/serveros`，而 `plugin/profiles/testing.yaml` 只在 worktree 里，于是注册表「多」了 `automated-testing` | 已确诊：`SERVEROS_BUNDLE=<worktree>/plugin` 下全套 91 passed。修法是让 `src/serveros` 前进到含该 profile 的 revision，**不是**改测试 |
| `test_the_committed_view_is_up_to_date` | ability-management `tests/test_capability_families.py` | repin 的派生后果：视图里嵌 `authority_revision` | 已修，并入 repin commit `c993c82`（只动 pin 不动 pin 的渲染，那一格 bisect 出来就是红的） |

第二条值得单独记一笔：它是**同一个族的第四次**——源动了，派生物没跟着动。前三次是
manifest 行、packs 闭包、cards。凡是「X 的渲染嵌了 X 的值」，都必须和 X 同一次改。

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

## 7. 待决决策包

聚合成一包，不逐条问。每条都写「不定会怎样」：

1. **`noc-copy-20260922@c233946` 要不要落到 `main`。** 这是本包里最重的一条，理由比「版本新鲜度」
   硬得多：**P8 的实现已经在那个分支上做完了**（`CARDS-STYLE.md` + 6 个 runtime 文件，
   见 §9.1），而且**那个分支就是活的节点正在跑的东西**。不定的后果不是「落后一点」，
   是两头同时错：从 main 做 P8 会重写一遍已有实现（第二来源，正是 §2 禁止的形态），
   而在 main 上改 `runtime/cli.py` 会和那个分支撞车。
2. **东京卡要不要搬到 `serverosctl card`。** 不定的后果：卡面两套渲染长期并存，Card 模型
   在生产链上永远不被行使。
3. **`.staging-*` 目录怎么处置。** `hwz-server-os/` 下有 `.staging-978a0f9`、`.staging-a4758a0`、
   `.staging-c233946`。不定的后果：占空间，且 `release.py` 下次遇到同名 staging 会
   `shutil.rmtree` 它——**这是 release.py 的行为，不是建议**，所以它们要么是残渣要么是
   正在用的，需要人判。
4. **`automated-testing` 属于哪个 pack。** 候选：`pack:quality-delivery`（「一个节点要测试、
   反馈、报告所需的能力」）或 `pack:autonomous-quality-loop`（「一个工作项自己开发、自测、
   反应」）。两个 purpose 都点名测试，区别在主语——一个是**节点的能力集**，一个是**工作项
   跑的循环**。不定的后果：两个 pack 都对测试的实际承诺含糊。
5. **A8/A9/A10/B5 要不要有 pack。** 同 4 的形状，本轮新增四条。不定的后果：这五个职责
   没有任何 pack 描述它们，而 pack 是「产品承诺了什么」的声明面。
6. **要不要给 `serveros-testing` 注册第二条 plugin 记录。** 需要改 `manifest_members()`
   让它在 plugin 记录上取并集。不定的后果：A7 的 plugin 在注册表里只以 Skill 身份出现，
   composition 本身不被登记。
7. **要不要在这台共享机上一装 `serveros-testing`。** 见 §3.3 第 2 条。
8. **要不要教 `release.py` 一个非 node composition 的形状。** 见 §3.3：现在它只装 node runtime，
   而 plugin/dist 走的是另一条路。不定的后果：plugin/dist 的分发没有 release 级的版本
   与回滚（`revisions/` + `previous` + `RELEASE.json` 那三样它现在享受不到）。

## 8. 卫生发现（只给位置，不给值）

| 位置 | 现象 | 为什么值得看 |
| --- | --- | --- |
| `~/.config/systemd/user/serveros-alert-sync.service.d/10-botmux-token.conf` | drop-in 孤立 | 引用它的 unit 已不在；drop-in 里的东西是否还有效没人知道 |
| `hwz-monitor.service` | dead | 死服务 |
| `~/.claude/settings.json` | `ANTHROPIC_AUTH_TOKEN` 内联，且 `ANTHROPIC_BASE_URL=http://127.0.0.1:26500`，`permissions.defaultMode: bypassPermissions` | 明文凭据落在 HOME 下，而策略说凭据只住 vault |
| `sanitize.default_files()` | fail-open | 脱敏失败时放行，方向反了 |

第 4 条只报行为，不报调用点——本轮没读它的调用链。

## 9. 待改对象的 owner / base / consumer（P1–P7，实测）

计划 P0 要的第三样东西：「盘点每个详细设计对应的 source、实际入口、consumer、现有字段与
真实 producer」。下面每条都是读过盘之后写的；grep 没命中的写 `not found`，没读的写 `not read`。

两条**在 fabric 树里没有消费者**的孤岛，是本轮最实的两处接线缺口：

| 对象 | 在哪 | 谁调它 | 判定 |
| --- | --- | --- | --- |
| records 的 66 份 `information/raw/**/manifest.json` | `/data/home/vince/workspace/records/information/raw/…` | **无**。`grep -rn "pagination_complete\|msg_count" --include=*.py` 在 fabric 树里零命中 | 采集写了 manifest，**ServerOS 侧没有读者**。设计要求「capture policy 与 materialized coverage 分开」，coverage 这一半没有被消费 |
| `space/resolver.py`（402 行，含词汇表） | serveros worktree `space/resolver.py` | **无**。`grep -rn "space\.resolver\|from space\|import space"` 零命中；`runtime/cli.py` 里没有 space 动词 | 只能 `python3 space/resolver.py --request …` 单跑。它的 docstring 自己声明「不认识 lark-cli 且永远不许认识」，所以孤岛是刻意的，缺的是**接线**不是实现 |

| 包 | 对象 | source | 入口 | consumer | 判定 |
| --- | --- | --- | --- | --- | --- |
| P1 | 文档身份与权威链 | `documents/document_governance.py:79`（`serveros/document-identity.v1` 等四个 schema），`document-vocabulary.json` | `serverosctl document resolve --request` (`runtime/cli.py:7260`) | — | working，纯内核无 I/O |
| P1 | Context Capsule | `context/resolver.py:37` | `serverosctl context startup` (`cli.py:1736`) | — | working |
| P1 | Publication receipt | `runtime/daily_report.py:77` `RECEIPT_SCHEMA`，`receipt():924`，`publish():993` | `serverosctl report daily` (`cli.py:7057`) | — | working，**树里唯一实现** |
| P1 | `Source` 类型 | — | — | — | **not found**：没有该类型。最近的字段是 `document_governance.py:252` 的 `source_ref`、`memory/record.py:83` 的 `source`（REQUIRED）、Item 卡的 `source_refs` |
| P1 | 编写的 follow 源清单 | `intake/workchain.py:723` `load_sources()` 读 `<records_root>/<project>/context/sources.yaml` | `serverosctl work coverage --project` (`cli.py:5592`) | — | 有读者，**但盘上没有 `sources.yaml`**（`find` 空） |
| P2 | 五个采集器 | `/data/home/vince/workspace/records/information/raw/{sync_feishu_chat,expand_feishu_doc,expand_feishu_files,drift_sync,_redact}.py` | 各自 `main()`；`drift_sync.py:2` 自称 SessionStart hook driver | lark-cli（`sync_feishu_chat.py:131`、`expand_feishu_doc.py:42`） | working；`information/raw/` 在 records 仓里是 clean |
| P2 | 唯一脱敏实现 | `records/information/raw/_redact.py`（327 行，md5 `fa980373…`） | 库：`redact()`、`persist_secrets()` | `sync_feishu_chat.py:29`、`expand_feishu_doc.py:32`、`redact_backfill.py:54` | **盘上有 5 份**，其中 `worktrees/records/reconcile-20260922` 与主份逐字节相同，另外三份（SRV-0003 75 行 / MKT-0001 90 行 / clean-line-20260920 274 行）与已退役归档 `015de972` 不同。设计要「保留唯一 redaction 实现」，盘上现在是多份——**这是待收敛项，不是既成事实** |
| P2 | 黑名单 | `records/information/raw/DRIFT_WATCHLIST.txt` | — | — | **not read**；注册 `drift_sync` 为 hook 的 `settings*.json` **not found** |
| P3 | people/project/topic 卡 | 手写 markdown：`records/information/understanding/{people,projects,topics}/*.md` | — | — | **no producer found**：全 workspace 的 `*.py` 里没有生产者。设计要的「最小 incremental writer」就是这一条的缺口 |
| P3 | Minutes / MeetingBundle | 无 Python 类。`space/meeting-policy.schema.yaml:30` 声明结构；内核 `space/meeting_relevance.py:69` | `python3 space/meeting_relevance.py --request`（**未接 serverosctl**） | — | 内核 working；bundle 的生产者 not found |
| P3 | 记忆账本/投影/scope | `memory/{record,projection,scope,handoff}.py` | `serverosctl memory {audit,project,status,subject}` (`cli.py:6479`) | — | working；`docs/memory-fabric.md` 逐条点名这些模块 |
| P4 | 文档关系边 | `relations/project.py:47`（`serveros/relations.v1`），`relations/schema.yaml:30` | `python3 project.py --records ROOT --write relations/current.jsonl` | — | **partial**：8 个谓词里只有 2 个（`belongs_to`、`derived_from`）有生产者，其余 6 个 `declared_only` |
| P4 | Feishu 空间适配 / 共享 publisher | **只有 SKILL.md 文本，没有代码**：`skills/feishu-space-management/`、`skills/document-stewardship/` | — | — | partial（只有契约文字） |
| P5 | 员工 KB 生产者 | `src/litellm-support/scripts/build_agent_kb.py`，输出 `dist/employee-kb/`（`dist/` 被 gitignore） | `python3 scripts/build_agent_kb.py` | — | working，但**在 ServerOS 之外** |
| P5 | KB 消费 | `intake/support.py:187,432,534,697,855,977`，契约 `intake/support-contract.yaml` | `serverosctl support {entries,match,answer,check} --kb-root <path>` (`cli.py:7206`) | — | working，但 `--kb-root` 必填且**没有任何东西把它接到 litellm-support 那棵树** |
| P5 | 公开指南生产者 | — | — | — | **not found as code** |
| P6 | 线程准入 / 控制存储 | `runtime/coordination.py:89,116,228`，`runtime/agent_control_store.py:86,97,103,149` | `serverosctl coordination {resolve,bind,close,list}` (`cli.py:6632`) | — | working |
| P6 | provider 侧准入 | `mcp/agent_session_service.py:58,115,159,251` | `mcp/bin/serveros-agent-mcp` | — | working（support→readiness→admission 顺序在 `:229`） |
| P6 | 租约 / 所有权 / 需求板 | `runtime/lease_store.py:61`，`runtime/ownership_gate.py:209`，`intake/global_board.py:84` | `serverosctl {lease,ownership,requirement}` | — | working |
| P6 | focus 授权 | `intake/focus_source.py:56,71,83,225` | `serverosctl work focus` | — | **partial**：文件自己写着「本机没有 grant store」，`--grants` 未绑定 |
| P6 | 委托/授权存储 | — | — | — | **not found**：只找到规则类（`runtime/agent_sessions.py:82`、`fleet/agent_control_router.py:244`） |
| P6 | 原子准入（选择+预算+租约+派发一个事务） | 只有逐文档原语：`agent_control_store.py:103,149`、`lease_store.py:172`、`alerting.py:279`、`alert_digest.py:304`、`bounded_invocation.py:88` | — | — | **partial**：原语读过，**没有找到合并后的准入事务** |
| P7 | 已安装 Quay | `~/.claude/plugins/cache/quay/quay/0.11.0/`，`bin/quay --version` → `0.11.0-dev` | MCP：`…/0.11.0/.mcp.json` → `vendor/quay/dist/quay.js mcp` | — | working（`0.4.0` 没有 `goal`，`0.11.0` 又有了——本机至少三代并存） |
| P7 | codex provider | `mcp/codex_app_server_provider.py:88,89,95,564,1181` | `--live` 才启动，默认只读 | `codex-cli 0.153.4`（`~/.local/bin/codex`，与 `MEASURED_AGAINST` 一致） | working |

### 9.1 两个必须记下的现场事实

**（一）活的节点跑的不是这个 worktree。** 实测 md5 对比：

```text
DIFF  runtime/cards.py              DIFF  runtime/card-definitions.yaml
DIFF  runtime/presentation.py       DIFF  runtime/card-scenarios.yaml
DIFF  runtime/cli.py                DIFF  runtime/notification-contract.yaml
SAME  runtime/coordination.py       SAME  documents/document_governance.py
```

安装面是 `noc-copy-20260922@c233946`（分支 `serveros/noc-copy-20260922`），与 fabric-v1
各 3 个提交分叉。**`runtime/cli.py` 在被别人改**——所以本轮任何 `cli.py` 改动都会撞车，
不做。

**（二）`CARDS-STYLE.md` 只存在于 noc-copy。** 9310 字节，mtime 2026-09-22 11:02，
和 promote 时间（11:00）同一刻；被 `presentation.py` / `cards.py` / `card-definitions.yaml`
引用。fabric-v1 里 `ls CARDS-STYLE.md` → 不存在。

（一）与（二）合起来给出一条硬结论：**P8（广播/告警 producer/presentation/receipt）
的实现已经在未合并的 noc-copy 分支上做完了。** 所以决策包第 1 条卡住的不是版本新鲜度，
是整条 P8——不落 noc-copy，P8 在 main 上无从做起，重做一遍就是第二来源。

## 10. 后续每包更新哪几行

- 改完一个包，只更新 §3.1 里对应行的四层状态 + §5 表的增删。
- **不新开文件、不新开平台。** 计划原话：「后续每包只更新此处结果/证据，不另建平台。」
- 状态从 `NOT_MEASURED` 变 `DONE` 必须有具体证据（测试名 + 结果，或实测命令 + 输出），
  不能因为「代码在盘上」就改。
