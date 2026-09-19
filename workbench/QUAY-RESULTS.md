<!-- ⟦snapshot⟧ rendered 2026-09-19T19:15:42+00:00 by `runtime/workbench.py` from ~/workspace/worktrees/serveros/foundation-skeleton -->

# QUAY-RESULTS

> **读数** — 本页内容是这台机器在一个时刻的测量值，重跑会得到**新的**读数，不应当相等。Goal/AC/Task 与 GateEvent 账本按渲染时刻读取。`serverosctl workbench check` 因此只核对这些命令今天仍然回答、页面结构没有塌陷，不比对任何数字。 本页**没有记录可重跑的命令**，所以 `check` 对它只比对小节结构，不重新核对任何来源——它读的东西在本仓之外。

<!-- ⟦answers⟧
(none) 本页不读任何命令
-->

父工作 → Goal → AC → Task → 执行 → 结果。这一页只印**回读**到的东西：status、GateEvent、退出码。没有「创建成功」这种话。

### Goal / AC 记录

| id | kind | status | goal | title / criterion |
|---|---|---|---|---|
| AC-001 | criterion | achieved | GOAL-001 | 产物入口从 /tmp 回答（不是从源码目录、也不是从产物目录） |
| AC-002 | criterion | achieved | GOAL-001 | 按 tree_hash_recipe 重算的摘要等于 RELEASE.json 记下的值 |
| AC-003 | criterion | achieved | GOAL-001 | current/previous 都是符号链接，且 previous 里有一个可执行的 serverosd |
| AC-004 | criterion | achieved | GOAL-001 | runtime/ 与 bin/ 里没有出现构建工作树的绝对路径 |
| AC-005 | criterion | achieved | GOAL-001 | 从已安装 revision 渲染九份 START-HERE 文档到一个外部目录，全部非空 |
| AC-006 | criterion | achieved | GOAL-001 | 对四份文档的子集跑 workbench check（全 15 份 46s 超出判据 60s 上限），判定块、来源命令与结构都一致 |
| AC-900 | criterion | draft | GOAL-900 | 指向一个不存在的产物：必须 exit 1，且账本里带着原因 |
| GOAL-001 | goal | active |  | 工作包 F：构建产物可隔离安装、可回退、可被独立复算 |
| GOAL-900 | goal | draft |  | 负控（不参与 GOAL-001 的达成判断） |

### Task 记录

| id | status | goal-ac | title |
|---|---|---|---|
| PROBE-CHECK-THREE-READINGS | done |  | 负控：同一个谓词的三种读数 |
| PROBE-CHECK-UNSHAPED | todo |  | 负控：body 不是可识别的任务形态 |
| TASK-F-001 | done | AC-004 | 工作包 F：把固定验收变成可执行判据 |

### GateEvent 账本（16 条）

| at | record | gate | actor | verdict | reason |
|---|---|---|---|---|---|
| 2026-09-19T19:01:04 | AC-001 | goal | goal-cli | pass | acceptance passed (exit 0) |
| 2026-09-19T19:01:04 | AC-002 | goal | goal-cli | pass | acceptance passed (exit 0) |
| 2026-09-19T19:01:04 | AC-003 | goal | goal-cli | pass | acceptance passed (exit 0) |
| 2026-09-19T19:01:05 | AC-004 | goal | goal-cli | pass | acceptance passed (exit 0) |
| 2026-09-19T19:01:29 | AC-005 | goal | goal-cli | pass | acceptance passed (exit 0) |
| 2026-09-19T19:02:05 | AC-006 | goal | goal-cli | pass | acceptance passed (exit 0) |
| 2026-09-19T19:02:05 | AC-900 | goal | goal-cli | fail | acceptance failed (exit 1) — AC-900 负控按预期为红：/nonexistent/foundation-skeleton/never-built 不存在，即这一轮没有那个产物 |
| 2026-09-19T19:03:07 | TASK-F-001 | acceptance | quay-cli | pass | acceptance passed (exit 0) |
| 2026-09-19T19:03:08 | TASK-F-001 | dod | quay-cli | pass | all required artifacts present; eligible to move to ready |
| 2026-09-19T19:03:08 | TASK-F-001 | promote | quay-cli | pass |  |
| 2026-09-19T19:03:09 | PROBE-CHECK-THREE-READINGS | dod | quay-cli | pass | all required artifacts present; eligible to move to ready |
| 2026-09-19T19:03:09 | PROBE-CHECK-THREE-READINGS | promote | quay-cli | pass |  |
| 2026-09-19T19:03:10 | PROBE-CHECK-THREE-READINGS | acceptance | quay-cli | pass | acceptance passed (exit 0) |
| 2026-09-19T19:03:10 | PROBE-CHECK-THREE-READINGS | complete | quay-cli | pass |  |
| 2026-09-19T19:03:12 | TASK-F-001 | acceptance | quay-cli | pass | acceptance passed (exit 0) |
| 2026-09-19T19:03:12 | TASK-F-001 | complete | quay-cli | pass |  |

### 运行日志 gate-AC-001.log

```
{
  "id": "AC-001",
  "verdict": "pass",
  "reason": "acceptance passed (exit 0)",
  "timestamp": "2026-09-19T19:01:04.655Z",
  "dryRun": false,
  "event": {
    "id": "09941919-0a00-4f8c-a50f-7e2cd0dcf81e",
    "item_id": "AC-001",
    "pipeline_id": "AC-001",
    "gate": "goal",
    "actor": "goal-cli",
    "verdict": "pass",
    "timestamp": "2026-09-19T19:01:04.655Z",
    "payload": {
      "reason": "acceptance passed (exit 0)"
    }
  }
}
```

### 运行日志 gate-AC-002.log

```
{
  "id": "AC-002",
  "verdict": "pass",
  "reason": "acceptance passed (exit 0)",
  "timestamp": "2026-09-19T19:01:04.796Z",
  "dryRun": false,
  "event": {
    "id": "5b56b536-a588-4cbc-9a3f-cf0e4d6ac1d4",
    "item_id": "AC-002",
    "pipeline_id": "AC-002",
    "gate": "goal",
    "actor": "goal-cli",
    "verdict": "pass",
    "timestamp": "2026-09-19T19:01:04.796Z",
    "payload": {
      "reason": "acceptance passed (exit 0)"
    }
  }
}
```

### 运行日志 gate-AC-003.log

```
{
  "id": "AC-003",
  "verdict": "pass",
  "reason": "acceptance passed (exit 0)",
  "timestamp": "2026-09-19T19:01:04.921Z",
  "dryRun": false,
  "event": {
    "id": "c7d90f8a-e0f3-4b17-9328-ef5ddf1f261c",
    "item_id": "AC-003",
    "pipeline_id": "AC-003",
    "gate": "goal",
    "actor": "goal-cli",
    "verdict": "pass",
    "timestamp": "2026-09-19T19:01:04.921Z",
    "payload": {
      "reason": "acceptance passed (exit 0)"
    }
  }
}
```

### 运行日志 gate-AC-004.log

```
{
  "id": "AC-004",
  "verdict": "pass",
  "reason": "acceptance passed (exit 0)",
  "timestamp": "2026-09-19T19:01:05.041Z",
  "dryRun": false,
  "event": {
    "id": "a2ed44ad-5f44-4872-8df5-0d1b4ddb6b21",
    "item_id": "AC-004",
    "pipeline_id": "AC-004",
    "gate": "goal",
    "actor": "goal-cli",
    "verdict": "pass",
    "timestamp": "2026-09-19T19:01:05.041Z",
    "payload": {
      "reason": "acceptance passed (exit 0)"
    }
  }
}
```

### 运行日志 gate-AC-005.log

```
{
  "id": "AC-005",
  "verdict": "pass",
  "reason": "acceptance passed (exit 0)",
  "timestamp": "2026-09-19T19:01:29.693Z",
  "dryRun": false,
  "event": {
    "id": "ab9192aa-9ca9-43e6-8a15-7fb86ead9e61",
    "item_id": "AC-005",
    "pipeline_id": "AC-005",
    "gate": "goal",
    "actor": "goal-cli",
    "verdict": "pass",
    "timestamp": "2026-09-19T19:01:29.693Z",
    "payload": {
      "reason": "acceptance passed (exit 0)"
    }
  }
}
```

### 运行日志 gate-AC-006.log

```
{
  "id": "AC-006",
  "verdict": "pass",
  "reason": "acceptance passed (exit 0)",
  "timestamp": "2026-09-19T19:02:05.618Z",
  "dryRun": false,
  "event": {
    "id": "3badb00f-21fb-480c-83b0-7aeaf02634d2",
    "item_id": "AC-006",
    "pipeline_id": "AC-006",
    "gate": "goal",
    "actor": "goal-cli",
    "verdict": "pass",
    "timestamp": "2026-09-19T19:02:05.618Z",
    "payload": {
      "reason": "acceptance passed (exit 0)"
    }
  }
}
```

### 运行日志 gate-AC-900.log

```
{
  "id": "AC-900",
  "verdict": "fail",
  "reason": "acceptance failed (exit 1) — AC-900 负控按预期为红：/nonexistent/foundation-skeleton/never-built 不存在，即这一轮没有那个产物",
  "timestamp": "2026-09-19T19:02:05.745Z",
  "dryRun": false,
  "event": {
    "id": "3708ae49-d9f4-4a02-8900-c0d62c70ec8b",
    "item_id": "AC-900",
    "pipeline_id": "AC-900",
    "gate": "goal",
    "actor": "goal-cli",
    "verdict": "fail",
    "timestamp": "2026-09-19T19:02:05.745Z",
    "payload": {
      "reason": "acceptance failed (exit 1) — AC-900 负控按预期为红：/nonexistent/foundation-skeleton/never-built 不存在，即这一轮没有那个产物"
    }
  }
}
```
