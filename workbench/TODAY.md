<!-- ⟦snapshot⟧ rendered 2026-09-19T19:12:33+00:00 by `runtime/workbench.py` from ~/workspace/worktrees/serveros/foundation-skeleton -->

# TODAY

> **读数** — 本页内容是这台机器在一个时刻的测量值，重跑会得到**新的**读数，不应当相等。今日重点是当前状态的一次选择。`serverosctl workbench check` 因此只核对这些命令今天仍然回答、页面结构没有塌陷，不比对任何数字。

<!-- ⟦answers⟧
serverosctl work board --json	0
-->

今天实际在推的东西。这一页的输入是 `today.yaml`，而它**不是从 Item 状态猜出来的** ——它是一个人的选择。

### 读数

`today.state = UNPRODUCED`
`today.why = litellm: UNPRODUCED；multi-user-claw: UNPRODUCED；serveros: UNPRODUCED`
`today.items = []`
按项目：{"litellm": {"date": null, "items": [], "path": "~/workspace/records/projects/litellm/board/today.yaml", "state": "UNPRODUCED", "why": "no selection was recorded at ~/workspace/records/projects/litellm/board/today.yaml"}, "multi-user-claw": {"date": null, "items": [], "path": "~/workspace/records/projects/multi-user-claw/board/today.yaml", "state": "UNPRODUCED", "why": "no selection was recorded at ~/workspace/records/projects/multi-user-claw/board/today.yaml"}, "serveros": {"date": null, "items": [], "path": "~/workspace/records/projects/serveros/board/today.yaml", "state": "UNPRODUCED", "why": "no selection was recorded at ~/workspace/records/projects/serveros/board/today.yaml"}}

**这个读数是这一轮的一个真实缺口，不掩饰：** 有四个 ACTIVE 的 Item 在飞，而 `today.yaml` 里没有任何一次选择被记下来。看板不能代替选择 —— 一页列出所有在做的事，和「今天做这两件」是不同的两句话。

### 为什么这一页现在写成选择

`serverosctl work today` 是这组动词里**唯一会写**的一个，而它写的就是一次选择：不碰任何 Item 状态。这是刻意的 —— 一个由状态派生出来的「今天」，会在状态变化的那一刻悄悄变掉，而人已经照着它安排了一天。
