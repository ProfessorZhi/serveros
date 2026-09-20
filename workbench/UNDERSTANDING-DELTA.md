<!-- ⟦snapshot⟧ rendered 2026-09-20T11:29:56+00:00 by `runtime/workbench.py` from ~/workspace/worktrees/serveros/SRV-0011 · inputs: projects=serveros,litellm,multi-user-claw; project=litellm; item=LIT-0101; quay-run=; quay-bin=; ability-repo=~/workspace/worktrees/ability-management/SRV-0011; reports-dir=~/artifacts/work-items/serveros/SRV-0011/RUN-0001; release-root=~/artifacts/work-items/serveros/SRV-0011/RUN-0001/release/dist/serveros-linux-work -->

# UNDERSTANDING-DELTA

> **读数** — 本页内容是这台机器在一个时刻的测量值，重跑会得到**新的**读数，不应当相等。认识文档的计数与 sources 覆盖随写入而变。`serverosctl workbench check` 因此只核对这些命令今天仍然回答、页面结构没有塌陷，不比对任何数字。

<!-- ⟦answers⟧
serverosctl work skeleton understanding --json	0
-->

关注源和认识变化。这一页把两件事分开：**关注什么**（源清单）和**知道了什么**（认识层的四类卡片）。

### 认识层的四个 scope（骨架声明的形状）

```json
{
  "file": "understanding.md",
  "hop": "structured-understanding",
  "id": "understanding",
  "is": "what is now understood, and what changed",
  "root": "records/information/understanding",
  "scope": "node",
  "scopes": [
    {
      "id": "projects",
      "is": "one card per business, updated as what we know changes",
      "observed_headings": [
        "当前职责",
        "当前系统",
        "当前主要问题",
        "当前优先方向",
        "尚未确定",
        "Sources"
      ],
      "read_from": "2 份文档"
    },
    {
      "also_seen": [
        "架构",
        "产出",
        "数据/权限",
        "已确认事实",
        "当前认识",
        "受控实验约束",
        "证据与观察缺口",
        "职责主线",
        "环境",
        "常见问题与处置"
      ],
      "id": "topics",
      "is": "cross-project subject matter, where the shapes differ most",
      "observed_headings": [
        "核心",
        "平台难点",
        "Sources"
      ],
      "read_from": "5 份文档"
    },
    {
      "id": "people",
      "is": "a person's role, how they work with me, and what they decide",
      "observed_headings": [],
      "read_from": "4 份文档，没有一份带二级小节"
    },
    {
      "id": "responsibilities",
      "is": "what I am accountable for across repositories",
      "observed_headings": [
        "分层说明",
        "产物边界"
      ],
      "read_from": "1 份文档"
    }
  ],
  "traceability": {
    "because": "`understanding/README.md` — 每份认识必须能溯源到 raw source. It is the node's own red line, so `work coverage` counts how many documents carry it. It is counted and not graded: 5 of the 12 documents here are people and responsibilities cards that do not use it, and calling those defective would be this file inventing a standard one scope over.",
    "heading": "Sources"
  }
}
```

### 变化

**这一栏目前是 PARTIAL，原因具名：** 没有一处在读「今天知道了什么、昨天不知道」。
认识层有卡片和 Sources 溯源，但**没有 delta 产生者** —— 一份卡片被改动过，没有一个东西会把它读成一次变化。

要把它做成真的，需要的是：读 `records/information/understanding/` 下每份文件的当前内容，和一个在此之前被固定下来的快照做差。前者存在，后者不存在。

在这一栏变成一张真实列表之前，它不假装自己有内容。
