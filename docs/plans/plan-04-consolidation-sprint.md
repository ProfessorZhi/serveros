# Plan 04 — 仓库整合冲刺：几小时内并行完成

> 依据：serveros PR #279 里的 `docs/plans/plan-03-repository-consolidation.md`。本文件只改两件事：**节奏**（几小时、多 agent 并行）和**边界**（不影响正在运行的旧版 Fleet）。
> 已确认：engine 合并（车道 A）只新增 41 个文件，不改任何既有文件，**不需要等 95 个 Draft PR**，和其他车道并行推进。

---

## 0. 第一条：不影响正在运行的 Fleet

正在运行的东京、Dell、Windows 节点都用各自的 checkout 或 release 目录。只要遵守下面几条，整合期间这些节点不会受影响：

1. **不登录节点，不改节点。** 设备同步（G1）不在这次冲刺里做。G0 只读盘点可以做，前提是已有只读访问方式；没有就跳过。
2. **不合并任何会改运行代码的 PR**，包括 #266、#146 和那 95 个 Draft。它们属于 V1 交付，以后单独处理。
3. **旧仓库只归档，不删除。** 归档后仓库仍然可以 clone 和 pull，节点上的 `git pull` 照常工作。删除放到 14 天观察期之后。
4. **不改名的仓库**：`hwz-records`、`serveros`。节点上的路径和设备 profile 都引用了这两个名字。
5. `serveros` 的 main 上**只新增文件**。车道 A 以外的车道不改 `serveros` 的既有文件。如果一定要改（例如 README 的 Ownership 表），统一交给车道 A 的集成者，并单独提交。

---

## 1. 所有者先做（约 10 分钟，做完再开工）

- [ ] 新建 **Public** 空仓库 `llmlab`，不做任何初始化。
- [ ] 给 Claude GitHub App 授权：`llmlab`、`serveros`、`ability-management`、`hwz-records`、`marketops`、`marketops-session`。
- [ ] 冻结 work-engine 上还开着的 PR（#3、#13–#16）：在 PR 里留言「冻结，内容随 serveros 合并」即可。
- [ ] `marketops-session` 按所有者决定**保持 Public**：车道 D2 先只读检查，发现会话语料或真实个人信息时立即报告所有者，由所有者决定是否临时改为 Private。

---

## 2. 并行编排：每个最终仓库一位集成者，下面再分子 agent

**写入隔离规则**（这是能放心开大量 agent 的前提）：
- 每个子 agent 用**自己的 clone 或 worktree**，在**自己的分支** `consolidate/<车道>-<子任务>` 上工作，并且只写分给它的目录。
- 只有集成者能把子分支合进车道分支 `consolidate/<车道>`，并对最终仓库开 PR。
- 两个 agent 不能写同一个仓库的同一个分支。

| 车道 | 最终仓库 | 子 agent（并行） | 预计 |
|---|---|---|---|
| A | `serveros` | A1：rebase 已有的 workengine 分支，补跑测试<br>A2：把 research-exchange 的模板、契约和工具并入 `research-exchange/`<br>A3：文档改成六仓库结构（README / README.zh / ARCHITECTURE / DIRECTORY-CONVERGENCE），**单独一个提交** | 1–2 h |
| B | `ability-management` | B1：把 dev-doctor 并入 `skills/dev-doctor/`<br>B2：把 agent-software 并入 `software/`<br>B3–B5：31 个敏感文件按目录分成三份并行脱敏<br>B6：用 gitleaks 扫描**包含历史**，给出建议：直接公开，还是新建干净仓库 | 2–3 h |
| C | `hwz-records`（Private） | C1：把 hwz-ops 并入 `ops/`<br>C2：把 agent-session-archive 并入 `archive/agent-sessions/`<br>C3：新建 `research-packets/` | 1 h |
| D | MarketOps | D1：`marketops` 补齐内容，换成合成数据，脱敏<br>D2：`marketops-session` 保持 Public：只读检查敏感内容，从 `-private` 补齐**可以公开**的部分，语料和真实会话片段不迁 | 2–3 h |
| E | `llmlab` | E1：确定代码基线，并入 PR #62、#70、#71、#127<br>E2：把旧仓库 `llm-lab` 的内容并入 Gateway / deploy / legacy<br>E3：修已知失败的测试（Python）<br>E4：修已知失败的测试（浏览器）<br>E5：全仓脱敏，然后用一个初始提交推送 | **3–6 h，最长** |
| F | 第三方副本（只读） | F1–F6：每个仓库一个 agent，核对有没有所有者自己的独有提交 | 30 min |

总计约 20 个子 agent，同时运行。

**时间盒：**
- T+0 到 T+3h：各车道产出 PR。
- T+3h 到 T+4h：验收（见第 3 节）。所有者批准合并后再归档。
- 车道 E 到 T+4h 还没全绿时，**不放宽标准**：先推送已经通过的部分，剩下的失败逐条列在回执里，下一轮继续。

---

## 3. 每条车道必须交的回执（缺一项就不算完成）

1. **改动清单**：新增、修改、删除各有哪些文件。车道 A 必须证明「既有文件 0 修改」，A3 那个文档提交除外。
2. **落点映射**：旧仓库里**每一个**顶层文件和目录，写明它去了哪里，或者为什么不迁。要求全部有着落，不能抽查。
3. **测试**：命令，以及通过、失败、跳过各多少。不允许删测试。
4. **脱敏**：`gitleaks` 和 `trufflehog` 的结果必须为 0，再附上 grep 检查 IP、`*.ts.net`、`/home/<用户>`、`sk-` / `ghp_` / `Bearer ` 的结果。
5. **需要所有者在网页上做的操作清单**：改可见性、归档、转移 Issue。

---

## 4. 收尾（所有者）

1. 合并各车道的 PR。
2. 把被并入的 10 个旧仓库**归档**；第三方副本经 F 车道确认后可以直接删除。
3. 14 天观察期结束，并且 G0 确认没有节点再拉旧仓库后，再运行 plan-03 第 11 节的删除脚本。

---

## 5. 不在这次冲刺里做

- 合并、关闭或整理那 95 个 Draft PR（V1 交付，单独决策）。
- #266、#146 等运行代码相关的 PR。
- 设备上的重装和切换（G1）。

## 6. 排障提示

如果明明还有空闲名额、任务池里也有活，agent 却不再被派出去，**先检查 subagent 的派生次数上限**。达到上限后会自动降级成串行执行，而且不会报错。识别方法：在会话 transcript 里搜索 `Subagent spawn limit reached`。

## 7. 分工（2026-10-18 起）

两个指挥者同时工作，**按车道划分，不重叠**：

| 车道 | 负责方 | 现有分支 |
|---|---|---|
| A `serveros` ← work-engine | 所有者本地 CC | `feat/workengine-merge-20261019`（本地，尚未推送） |
| B `ability-management` | 所有者本地 CC | 本地脱敏中 |
| C `hwz-records` | 所有者本地 CC | `chore/consolidate-20261019`（已推送） |
| D MarketOps | 云端会话 | `consolidate/marketops-*`、`consolidate/marketops-session-*` |
| E `llmlab`（暂建在 `llm-lab-portal` 上，`llmlab` 建好后整体推送） | 云端会话 | `consolidate/llmlab-*` |
| F 第三方副本核对（只读） | 云端会话 | 无写入，结论写在会话回执里 |

规则：
- 每个会话只推送自己名下的新分支，**不推送、不 rebase、不 force-push 别人的分支**，也不推送 main。
- 每个会话的产出是一个 **Draft PR** 加一份回执，回执格式见第 3 节。合并由所有者决定。
- 发现自己的车道已经被别人做了，停下来并在回执里说明，不重复做。
