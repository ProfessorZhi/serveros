<!-- ⟦snapshot⟧ rendered 2026-09-20T14:06:52+00:00 by `runtime/workbench.py` from ~/workspace/worktrees/serveros/SRV-0011 · inputs: projects=serveros,litellm,multi-user-claw; project=litellm; item=LIT-0101; quay-run=~/artifacts/work-items/serveros/SRV-0011/RUN-0001/quay-run; quay-bin=~/workspace/worktrees/quay/foundation-skeleton/plugin/vendor/quay/dist/quay.js; ability-repo=~/workspace/worktrees/ability-management/SRV-0011; reports-dir=~/artifacts/work-items/serveros/SRV-0011/RUN-0001; release-root=~/artifacts/work-items/serveros/SRV-0011/RUN-0001/release/dist/serveros-linux-work -->

# ITEM-CHAIN

> **读数** — 本页内容是这台机器在一个时刻的测量值，重跑会得到**新的**读数，不应当相等。单项链接读的是当前 Item 状态。`serverosctl workbench check` 因此只核对这些命令今天仍然回答、页面结构没有塌陷，不比对任何数字。

<!-- ⟦answers⟧
serverosctl work item LIT-0101 --project litellm --json	0
serverosctl work skeleton brief --json	0
serverosctl work skeleton design --json	0
serverosctl work skeleton plan --json	0
serverosctl work skeleton report --json	0
-->

### LIT-0101 的文档链

```json
{
  "card": {
    "how": "REBASED",
    "path": "~/workspace/records/projects/litellm/items/LIT-0101/card.yaml",
    "resolution_why": "the locator is absolute and absent on this node; its tail resolves to ~/workspace/records/projects/litellm/items/LIT-0101/card.yaml. The locator was written where the workspace root was '/opt/applications/data/home-hwz', and that declaration outlived the machine it described",
    "state": "PRESENT",
    "why": ""
  },
  "deliverables": 1,
  "documents": [
    {
      "count": 1,
      "document": "card.yaml",
      "path": "~/workspace/records/projects/litellm/items/LIT-0101/card.yaml",
      "required": true,
      "resolved_by": "exact-file",
      "skeleton": {
        "found": [],
        "missing": [],
        "own_headings": [],
        "state": "NOT_CHECKED",
        "why": "no skeleton declares this document, so nothing here has an opinion about its contents"
      },
      "state": "PRESENT",
      "why": ""
    },
    {
      "count": 1,
      "document": "brief.md",
      "path": "~/workspace/records/projects/litellm/items/LIT-0101/brief.md",
      "required": true,
      "resolved_by": "exact-file",
      "skeleton": {
        "found": [],
        "missing": [
          "需求",
          "为什么现在",
          "验收",
          "边界",
          "未知"
        ],
        "own_headings": [
          "LIT-0101 · 缓存命中事实落盘（需求1 / REQ1）",
          "定性（2026-09-14 最终）",
          "最终结论（2026-09-14 正式收口）",
          "⚠️ 结论的作用域（2026-09-14 补查后加上，防止过度解读）",
          "一个脆弱点（不是 bug，是会回归的设计风险）",
          "判据与证据",
          "已作废的方案（v1：给 SpendLogs 加 bigint 列）",
          "已知 coverage gap（不阻止本次收口）",
          "乱投证据登记（foreign ownership / misfiled，不删不搬）",
          "PR 处置台账（2026-09-14 `gh` 实测核验）",
          "运行环境残留"
        ],
        "state": "DIVERGES",
        "why": "the document is written and uses its own 11 heading(s); 5 of the 5 PROPOSED headings (需求, 为什么现在, 验收, 边界, 未知) are not among them. This is a difference and not a defect — the proposed shape is `PROPOSED_NOT_APPROVED` and no document on this node uses it"
      },
      "state": "PRESENT",
      "why": ""
    },
    {
      "count": 0,
      "document": "design",
      "path": "~/workspace/records/projects/litellm/items/LIT-0101/design",
      "required": true,
      "resolved_by": "",
      "state": "MISSING",
      "why": "an Item in ACTIVE is expected to have it, and it resolves under none of ['exact-file', 'directory', 'suffixed-file']"
    },
    {
      "count": 0,
      "document": "plan",
      "path": "~/workspace/records/projects/litellm/items/LIT-0101/plan",
      "required": true,
      "resolved_by": "",
      "state": "MISSING",
      "why": "an Item in ACTIVE is expected to have it, and it resolves under none of ['exact-file', 'directory', 'suffixed-file']"
    },
    {
      "count": 0,
      "document": "report",
      "path": "~/workspace/records/projects/litellm/items/LIT-0101/report",
      "required": false,
      "resolved_by": "",
      "state": "NOT_YET_REQUIRED",
      "why": "an Item in ACTIVE is not expected to have it yet"
    }
  ],
  "found": true,
  "item": "LIT-0101",
  "item_dir": "~/workspace/records/projects/litellm/items/LIT-0101",
  "kind": "feature",
  "outcome": "PARTIAL",
  "priority": {
    "personal": {
      "state": "UNPRODUCED",
      "why": "the card declares no `personal_priority`. The field is proposed in `intake/workchain-contract.yaml#card_extension` and is not approved, so nothing here invents one"
    },
    "source": {
      "state": "UNPRODUCED",
      "why": "the card declares no `source_priority`. The field is proposed in `intake/workchain-contract.yaml#card_extension` and is not approved, so nothing here invents one"
    }
  },
  "project": "litellm",
  "runs": 4,
  "schema": "serveros/item-chain.v1",
  "state": "ACTIVE",
  "superseded_by": null,
  "title": "cache-read token 写入 LiteLLM SpendLogs",
  "undecided": [
    {
      "because": "an Item in ACTIVE is expected to have it, and it resolves under none of ['exact-file', 'directory', 'suffixed-file']",
      "document": "design",
      "state": "MISSING"
    },
    {
      "because": "an Item in ACTIVE is expected to have it, and it resolves under none of ['exact-file', 'directory', 'suffixed-file']",
      "document": "plan",
      "state": "MISSING"
    }
  ]
}
```

### brief 的形状

这是**提议的形状**，不是对任何现有文档的判决：检查器报的是 DIVERGENCE，带文档自己的小节标题，而不是因为没用这些标题就说文档坏了。

```json
{
  "file": "brief.md",
  "hop": "item-brief",
  "id": "brief",
  "is": "one requirement, expanded, after it was chosen",
  "sections": [
    {
      "because": "what is being asked for, in the requester's own terms",
      "heading": "需求"
    },
    {
      "because": "what makes this worth a slot today rather than later. A brief that cannot answer this is a brief for a backlog item.",
      "heading": "为什么现在"
    },
    {
      "because": "what will be true when it is done, written so somebody else can check it. §6's 已完成 is this list met and not \"the run exited 0\".",
      "heading": "验收"
    },
    {
      "because": "what this does NOT cover, so the next document cannot widen it",
      "heading": "边界"
    },
    {
      "because": "what is not known yet. An empty 未知 section is a claim, and a reader is entitled to see it made rather than left out.",
      "heading": "未知"
    }
  ]
}
```

### design 的形状

这是**提议的形状**，不是对任何现有文档的判决：检查器报的是 DIVERGENCE，带文档自己的小节标题，而不是因为没用这些标题就说文档坏了。

```json
{
  "file": "design",
  "hop": "design-and-plan",
  "id": "design",
  "is": "how it will be built",
  "sections": [
    {
      "because": "what is true now, with the evidence a reader can re-run",
      "heading": "现状"
    },
    {
      "because": "the approach, and the alternative that was rejected with why",
      "heading": "方案"
    },
    {
      "because": "what else this touches — files, services, other Items",
      "heading": "影响面"
    },
    {
      "because": "what could go wrong, and what would tell us it had",
      "heading": "风险"
    }
  ]
}
```

### plan 的形状

这是**提议的形状**，不是对任何现有文档的判决：检查器报的是 DIVERGENCE，带文档自己的小节标题，而不是因为没用这些标题就说文档坏了。

```json
{
  "file": "plan",
  "hop": "design-and-plan",
  "id": "plan",
  "is": "in what order",
  "sections": [
    {
      "because": "the parts, in the order they have to happen",
      "heading": "步骤"
    },
    {
      "because": "how each step is known to be done. A step whose completion cannot be observed is a step that will be reported as done because time passed.",
      "heading": "判据"
    },
    {
      "because": "how to undo it. Empty for a change that cannot be undone is a different statement from empty because nobody thought about it.",
      "heading": "回退"
    }
  ]
}
```

### report 的形状

这是**提议的形状**，不是对任何现有文档的判决：检查器报的是 DIVERGENCE，带文档自己的小节标题，而不是因为没用这些标题就说文档坏了。

```json
{
  "file": "report",
  "hop": "report",
  "id": "report",
  "is": "what happened, with the evidence a reader can re-run",
  "sections": [
    {
      "because": "the outcome, stated before the evidence rather than after it",
      "heading": "结论"
    },
    {
      "because": "the commands, revisions and artifacts that produced the conclusion. \"退出 0 不等于通过\" — an exit code is not this section.",
      "heading": "证据"
    },
    {
      "because": "where the work diverged from plan.md, and whether the divergence was the plan being wrong or the work being wrong",
      "heading": "与计划的差异"
    },
    {
      "because": "what is NOT done, named. A report with no such section reads as a report about a finished thing.",
      "heading": "未完成"
    }
  ]
}
```
