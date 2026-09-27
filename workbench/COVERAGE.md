<!-- ⟦snapshot⟧ rendered 2026-09-27T06:51:41+00:00 by `runtime/workbench.py` from ~/workspace/worktrees/serveros/serveros-completion-20260925 · inputs: projects=serveros,litellm,multi-user-claw; project=litellm; item=LIT-0101; quay-run=~/artifacts/work-items/serveros/SRV-0011/RUN-0001/quay-run; quay-bin=~/workspace/worktrees/quay/foundation-skeleton/plugin/vendor/quay/dist/quay.js; ability-repo=~/workspace/src/ability-management; reports-dir=~/artifacts/work-items/serveros/SRV-0011/RUN-0001; release-root=~/runtime-assets/hwz-server-os/serveros-linux-work -->

# COVERAGE

> **读数** — 本页内容是这台机器在一个时刻的测量值，重跑会得到**新的**读数，不应当相等。目录/文档计数随别人写入而变。`serverosctl workbench check` 因此只核对这些命令今天仍然回答、页面结构没有塌陷，不比对任何数字。

<!-- ⟦answers⟧
serverosctl work board --json	0
serverosctl work coverage --project serveros --json	0
serverosctl work coverage --project litellm --json	0
serverosctl work coverage --project multi-user-claw --json	0
-->

磁盘三级地图、三个飞书面、关注源清单，各带分母。分母是这一页最重要的部分：「13/17」和「13」是不同的两句话。

### serveros

**磁盘**：13/17 存在，缺 4，不可读 0，未绑定 0（状态 `READ`）。

| kind | name | path | state |
|---|---|---|---|
| root | artifacts | `~/artifacts` | PRESENT |
| root | caches | `~/caches` | PRESENT |
| root | data | `~` | PRESENT |
| root | home | `~` | PRESENT |
| root | logs | `~/logs/serveros` | MISSING |
| root | runtime-assets | `~/runtime-assets/serveros` | MISSING |
| root | state | `~/state/serveros` | PRESENT |
| root | tmp | `~/tmp` | PRESENT |
| root | toolchains | `~/toolchains` | PRESENT |
| root | workspace | `~/workspace` | PRESENT |
| role | runtime.browser-distribution | `~/runtime-assets/browsers` | PRESENT |
| role | runtime.serveros-distribution | `~/runtime-assets/hwz-server-os` | PRESENT |
| ref | artifacts/work-items | `~/artifacts/work-items` | PRESENT |
| ref | state/boards | `~/state/serveros/boards` | PRESENT |
| ref | state/requirement-board | `~/state/serveros/requirement-board` | PRESENT |
| ref | state/serveros | `~/state/serveros/serveros` | MISSING |
| ref | state/workchain | `~/state/serveros/workchain` | MISSING |

**三个飞书面**：分母 the number of surfaces NAMED, which is three, and not the number of objects in them，状态 `NOT_LIVE`，authored sources 存在：`[]`。

| surface | state | why |
|---|---|---|
| 知识库 | UNKNOWN |  |
| 云盘 | UNKNOWN |  |
| 我的文档库 | UNKNOWN |  |

**关注源**：状态 `UNPRODUCED`，清单文件 `~/workspace/records/projects/serveros/context/sources.yaml`（`UNPRODUCED`）。

> no source list was authored at ~/workspace/records/projects/serveros/context/sources.yaml. The three feishu surfaces are still mapped; what is missing is the list of what a person follows, which is a decision and not a measurement

**认识层**：根 `~/workspace/records/information/understanding`，分母「the 4 scopes the skeleton names」，状态 `PRESENT`。

| scope | documents | 带 Sources | state |
|---|---|---|---|
| projects | 3 | 3 | PRESENT |
| topics | 5 | 5 | PRESENT |
| people | 6 | 0 | PRESENT |
| responsibilities | 1 | 0 | PRESENT |

### litellm

**磁盘**：13/17 存在，缺 4，不可读 0，未绑定 0（状态 `READ`）。

| kind | name | path | state |
|---|---|---|---|
| root | artifacts | `~/artifacts` | PRESENT |
| root | caches | `~/caches` | PRESENT |
| root | data | `~` | PRESENT |
| root | home | `~` | PRESENT |
| root | logs | `~/logs/serveros` | MISSING |
| root | runtime-assets | `~/runtime-assets/serveros` | MISSING |
| root | state | `~/state/serveros` | PRESENT |
| root | tmp | `~/tmp` | PRESENT |
| root | toolchains | `~/toolchains` | PRESENT |
| root | workspace | `~/workspace` | PRESENT |
| role | runtime.browser-distribution | `~/runtime-assets/browsers` | PRESENT |
| role | runtime.serveros-distribution | `~/runtime-assets/hwz-server-os` | PRESENT |
| ref | artifacts/work-items | `~/artifacts/work-items` | PRESENT |
| ref | state/boards | `~/state/serveros/boards` | PRESENT |
| ref | state/requirement-board | `~/state/serveros/requirement-board` | PRESENT |
| ref | state/serveros | `~/state/serveros/serveros` | MISSING |
| ref | state/workchain | `~/state/serveros/workchain` | MISSING |

**三个飞书面**：分母 the number of surfaces NAMED, which is three, and not the number of objects in them，状态 `NOT_LIVE`，authored sources 存在：`[]`。

| surface | state | why |
|---|---|---|
| 知识库 | UNKNOWN |  |
| 云盘 | UNKNOWN |  |
| 我的文档库 | UNKNOWN |  |

**关注源**：状态 `UNPRODUCED`，清单文件 `~/workspace/records/projects/litellm/context/sources.yaml`（`UNPRODUCED`）。

> no source list was authored at ~/workspace/records/projects/litellm/context/sources.yaml. The three feishu surfaces are still mapped; what is missing is the list of what a person follows, which is a decision and not a measurement

**认识层**：根 `~/workspace/records/information/understanding`，分母「the 4 scopes the skeleton names」，状态 `PRESENT`。

| scope | documents | 带 Sources | state |
|---|---|---|---|
| projects | 3 | 3 | PRESENT |
| topics | 5 | 5 | PRESENT |
| people | 6 | 0 | PRESENT |
| responsibilities | 1 | 0 | PRESENT |

### multi-user-claw

**磁盘**：13/17 存在，缺 4，不可读 0，未绑定 0（状态 `READ`）。

| kind | name | path | state |
|---|---|---|---|
| root | artifacts | `~/artifacts` | PRESENT |
| root | caches | `~/caches` | PRESENT |
| root | data | `~` | PRESENT |
| root | home | `~` | PRESENT |
| root | logs | `~/logs/serveros` | MISSING |
| root | runtime-assets | `~/runtime-assets/serveros` | MISSING |
| root | state | `~/state/serveros` | PRESENT |
| root | tmp | `~/tmp` | PRESENT |
| root | toolchains | `~/toolchains` | PRESENT |
| root | workspace | `~/workspace` | PRESENT |
| role | runtime.browser-distribution | `~/runtime-assets/browsers` | PRESENT |
| role | runtime.serveros-distribution | `~/runtime-assets/hwz-server-os` | PRESENT |
| ref | artifacts/work-items | `~/artifacts/work-items` | PRESENT |
| ref | state/boards | `~/state/serveros/boards` | PRESENT |
| ref | state/requirement-board | `~/state/serveros/requirement-board` | PRESENT |
| ref | state/serveros | `~/state/serveros/serveros` | MISSING |
| ref | state/workchain | `~/state/serveros/workchain` | MISSING |

**三个飞书面**：分母 the number of surfaces NAMED, which is three, and not the number of objects in them，状态 `NOT_LIVE`，authored sources 存在：`[]`。

| surface | state | why |
|---|---|---|
| 知识库 | UNKNOWN |  |
| 云盘 | UNKNOWN |  |
| 我的文档库 | UNKNOWN |  |

**关注源**：状态 `UNPRODUCED`，清单文件 `~/workspace/records/projects/multi-user-claw/context/sources.yaml`（`UNPRODUCED`）。

> no source list was authored at ~/workspace/records/projects/multi-user-claw/context/sources.yaml. The three feishu surfaces are still mapped; what is missing is the list of what a person follows, which is a decision and not a measurement

**认识层**：根 `~/workspace/records/information/understanding`，分母「the 4 scopes the skeleton names」，状态 `PRESENT`。

| scope | documents | 带 Sources | state |
|---|---|---|---|
| projects | 3 | 3 | PRESENT |
| topics | 5 | 5 | PRESENT |
| people | 6 | 0 | PRESENT |
| responsibilities | 1 | 0 | PRESENT |

### 覆盖页读出来的一个事实

`work board` 报 `today.state = UNPRODUCED` —— litellm: UNPRODUCED；multi-user-claw: UNPRODUCED；serveros: UNPRODUCED。

看板计数：{"cards_missing": 0, "cards_rebased": 8, "cards_unreadable": 0, "cards_unresolved": 0, "documents_diverging": 21, "documents_empty": 0, "documents_missing": 26, "items": 17, "priorities_unproduced": 34, "projects_read": 3, "projects_unread": 0}。
