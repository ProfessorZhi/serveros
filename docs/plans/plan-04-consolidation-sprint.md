# Plan 04 — 仓库整合冲刺：派工手册（GitHub + 各设备，高并发）

> **读者：所有者本地的主控 CC。** 你按本文件派出 agent、收回执、做合并。本文件就是全部指令，读完直接开工，不需要再问。
> 依据：同目录的 `plan-03-repository-consolidation.md`（细节正本）。本文件只规定**怎么并行派工**和**不能越过的边界**。
> 已确认：车道 A（work-engine 并入 serveros）只新增文件、不改既有文件，**不需要等那 95 个 Draft PR**。

---

## 0. 主控怎么用本文件（照做即可）

1. 读完第 1–3 节。
2. **按第 4 节的「派工表」一次性派出第 1 波全部 agent**，全部放后台并行（`run_in_background`），不要一个一个等。
3. 每个 agent 的指令 = 第 5 节「共同规则」+ 第 6 节里它自己那张「派工卡」，原样拼起来发给它。
4. 已经在做的车道（见第 4 节「现状」列）**接着原来的分支做**，不要重新开始。
5. agent 完成后，把它的回执贴进下面的状态表（在 serveros PR #279 下维护**一条**评论，每次编辑这条，不要刷屏）。
6. 某个车道第 1 波全部完成后，立刻派出它的第 2 波（集成）。不必等其他车道。第 2 波全部完成后，在 PR #279 下留言「请求验收」（第 7 节）。
7. 第 3 波（设备切换）每台设备都要所有者回复「批准 <设备名>」才开始。

**并发目标**：第 1 波约 20 个 agent 同时跑。如果有空位却派不出去，先在会话 transcript 里搜 `Subagent spawn limit reached`，那表示 subagent 派生次数到了上限，会静默降级为串行。

---

> **2026-10-18 所有者决定：`ability-management`、`marketops-session` 保持 Private。** 最终是 3 个 Public（`serveros`、`marketops`、`llmlab`）+ 3 个 Private（`ability-management`、`marketops-session`、`hwz-records`）。

---

## 1. 红线：不影响正在运行的旧版 Fleet

1. **不合并任何会改运行代码的 PR**：#266、#146 和那 95 个 Draft 都不碰，它们属于 V1 交付。
2. **第 1、2 波不改任何设备**。设备上只做只读盘点（G0）。
3. **设备切换（G1）只在所有者逐台批准后做**，并且：先备份 → 新旧并存安装 → 验证 → 再切换；任何一步失败立即按备份回滚。**不停 `serverosd`、不重启 Fleet connector**，除非所有者在批准里明确写了可以。
4. **旧仓库只归档，不删除。** 归档后仍可 clone / pull，设备上的 `git pull` 照常工作。删除在 14 天观察期之后由所有者做。
5. **不改名**：`hwz-records`、`serveros`（设备路径和 profile 都引用这两个名字）。
6. 不 push 到任何仓库的 main；不 force-push；不 rebase 别人的分支；不改仓库可见性、不删仓库（这些只有所有者能做）。
7. 不暴露凭据。设备上看到的 token、cookie、密钥一律不写进回执。

---

## 2. 所有者需要做的（越早越好，不阻塞第 1 波）

- [ ] 新建 **Public** 空仓库 `llmlab`（不初始化）。**只阻塞车道 E 的第 2 波推送**，其余车道照常进行。
- [ ] 冻结 work-engine 上还开着的 PR（#3、#13–#16）：留言「冻结，内容随 serveros 合并」。
- [ ] 第 2 波结束、**第 7 节验收通过后**：在网页上合并各车道 PR，然后归档旧仓库。
- [ ] 第 3 波：逐台回复「批准 东京」「批准 Dell」「批准 Windows」。

---

## 3. 回执格式（每个 agent 都要交，缺一项算没完成）

1. **改动清单**：新增、修改、删除的文件。
2. **落点映射**：旧仓库里**每一个**顶层文件和目录 → 新位置，或不迁的理由。必须全部有着落，不能抽查。
3. **测试**：命令，以及通过、失败、跳过各多少。区分「既有失败」和「新增失败」。
4. **脱敏**：`gitleaks`、`trufflehog` 结果（Public 仓库必须为 0），以及 grep IPv4、`*.ts.net`、`/home/<用户>`、`/data/home/<用户>`、`sk-` / `ghp_` / `Bearer ` 的结果。
5. **分支名和 Draft PR 链接**。
6. **需要所有者做的事**。

---

## 4. 派工表

| ID | 波次 | 仓库 | 分支 | 只写这些路径 | 依赖 | 现状（2026-10-18 晚） |
|---|---|---|---|---|---|---|
| A1 | 1 | serveros | `feat/workengine-merge-20261019` | `workengine/`、`tests/test_workengine_*.py`、`docs/workengine/`、`scripts/workengine_*` | — | **已在本地做完 41 个新增文件**，继续：跑测试、推送 |
| A2 | 1 | serveros | `consolidate/a2-research-exchange` | `research-exchange/` | — | 未开始 |
| A3 | 1 | serveros | `consolidate/a3-docs` | README / README.zh / ARCHITECTURE / DIRECTORY-CONVERGENCE（**只改文档**） | — | 未开始 |
| B1 | 1 | ability-management | `consolidate/b1-dev-doctor` | `skills/dev-doctor/` | — | 本地进行中 |
| B2 | 1 | ability-management | `consolidate/b2-agent-software` | `software/` | — | 本地进行中 |
| B3 | 1 | ability-management | `consolidate/b3-sanitize` | 21 个含真实值的文件 | — | **已完成**，PR #31（与 main 基线相同的既有失败，无新增） |
| B4 | — | ability-management | — | — | — | **取消**：所有者决定该仓库保持 Private，不需要为公开做历史处理 |
| C1 | 1 | hwz-records | `chore/consolidate-20261019` | `ops/`、`archive/agent-sessions/`、`research-packets/` | — | **已推送**，只需核对和补回执 |
| D1 | 1 | marketops | `consolidate/d1-marketops` | 全仓 | — | 未开始 |
| D2 | 1 | marketops-session | `consolidate/d2-session` | 全仓 | — | 未开始 |
| E1 | 1 | llm-lab-portal | `consolidate/e1-base` | 全仓（合并） | — | 未开始 |
| E2 | 1 | llm-lab-portal | `consolidate/e2-pytests` | `tests/`（不含 `tests/browser/`）、`backend/` | — | 未开始 |
| E3 | 1 | llm-lab-portal | `consolidate/e3-browser` | `tests/browser/`、`prototypes/v6/`（必要时） | — | 未开始 |
| E4 | 1 | llm-lab-portal | `consolidate/e4-import` | 只新增：`legacy/`、`deploy/templates/`、`deploy/grafana/`、`deploy/evalscope/`、`docs/legacy/`、`docs/benchmarks/`、`config/*.example.toml` | — | 未开始 |
| F1 | 1 | quay、meta-cc、archguard | 无（只读） | — | — | 本地已开始取上游，继续 |
| F2 | 1 | claude-fleet、learn-claude-code、claude-workspace | 无（只读） | — | — | **已完成**：后两个可删；claude-fleet 不可直接删（见派工卡 F） |
| G0-T | 1 | 东京设备 | 无（只读） | — | — | 未开始 |
| G0-D | 1 | Dell 设备 | 无（只读） | — | — | 未开始 |
| G0-W | 1 | Windows（本机） | 无（只读） | — | — | 未开始 |
| A-int | 2 | serveros | `consolidate/serveros` | 合并 A1–A3 | A1–A3 | |
| B-int | 2 | ability-management | `consolidate/ability-management` | 合并 B1–B3（PR #30、#32、#31） | B1–B3 | |
| D-int | 2 | — | — | 核对 D1、D2 的回执，列出要转移的 Issue | D1、D2 | |
| E-int | 2 | llmlab | `main` 的初始提交 | E1 + E2 + E3 + E4 → 全仓脱敏 → 一个初始提交 | E1–E4、**llmlab 已建** | |
| G1-T / G1-D / G1-W | 3 | 各设备 | — | 见派工卡 G1 | A-int、C1 已合并，**所有者批准该设备** | |

同一行的 agent 只写自己那几条路径。两个 agent 不会写同一个分支。

---

## 5. 共同规则（拼在每张派工卡前面）

```
你是仓库整合冲刺的一个 agent。任务书正本：ProfessorZhi/serveros 分支 claude/marketops-architecture-audit-u71oo7 的 docs/plans/plan-04-consolidation-sprint.md（细节见同目录 plan-03）。
- 只写派工卡里分给你的路径，只推送你自己的分支，开 Draft PR。不推 main、不合并、不 force-push、不 rebase 别人的分支。
- 不改仓库可见性、不删仓库、不改名、不转 Issue。
- 不合并或改动 #266、#146 和任何 V1 Draft PR 的内容。
- 不删测试、不 skip、不放宽断言。测试结果如实报告。
- Public 仓库不得出现：密钥、IP、内网主机名、/home/<用户>、/data/home/<用户>、真实人名/邮箱/手机号、sk-/ghp_/Bearer。测试里的真实用户名换成 alice/bob/carol。
- 设备上：除非你的派工卡是 G1 且写明已获批准，否则只读，不修改任何东西；不把 token、cookie、密钥写进回执。
- 发现你的工作已经有人做了（同类分支或 PR），停下来报告，不重复做。
- 结束时按 plan-04 第 3 节格式交回执。
```

---

## 6. 派工卡

**A1 — work-engine 并入 serveros（继续）**
分支 `feat/workengine-merge-20261019` 已有 41 个新增文件。按 plan-03 §4.2 补完：
- 只保留一个 `serverosctl`；
- `scripts/release.py` 的 `REQUIRED_MEMBERS` 加上 `workengine/`；
- 测试里指向外部 workengine 的路径改成仓库内路径；
- 5 个文件里的本机路径脱敏。

在全新 clone 里跑 `python3 scripts/verify.py`、`pytest`、`bin/serverosctl --help`、`python -m workengine --help`，推送并开 Draft PR。work-engine 的 PR #13–#16、#3 各自去向写进回执。

**A2 — research-exchange 并入 serveros**
把 `serveros-research-exchange` 的模板、契约、工具和测试放到 `research-exchange/`。README 写明：真正的研究材料包今后放在私有的 `hwz-records/research-packets/`，不放进 serveros。

**A3 — serveros 文档改成六仓库结构**
只改这几个文件：
- `README.md`、`README.zh.md` 的 Ownership 表；
- `ARCHITECTURE.md` 的所有权描述；
- `docs/architecture/DIRECTORY-CONVERGENCE.md` 的仓库结构段落。

内容以 plan-03 §0 为准，单独一个提交。

**B1 / B2 — 小仓库并入 ability-management**
- B1：`dev-doctor` → `skills/dev-doctor/`，其中 6 个文件含本机路径，需要脱敏。
- B2：`agent-software` → `software/`。

**B3 — ability-management 脱敏（已完成，PR #31）**
所有者已决定 `ability-management` **保持 Private**，所以 B3 是可选的清理，不是公开前提。`skills/muc-playwright-e2e/`（含真实姓名、飞书租户、会话 ID）原地保留，不移动。Git 历史不改写。

**C1 — hwz-records（核对已推送分支）**
核对 `chore/consolidate-20261019` 是否覆盖 `hwz-ops` → `ops/`、`agent-session-archive` → `archive/agent-sessions/`、新建 `research-packets/`。补齐缺的部分，交出完整的落点映射，开 Draft PR。这个仓库保持 Private。

**D1 — marketops**
把 `marketops-private` 中仍有用的内容补进 `marketops`。这是 Public 仓库：真实人名、公司名、邮箱、电话、社交账号全部换成合成数据。列出应该转到 `marketops` 的 Issue 编号（只列，不转）。

**D2 — marketops-session（Private）**
所有者已决定该仓库**保持 Private**（2026-10-18 已改为 Private）。把 `marketops-session-private` 的有用内容并入 `marketops-session`；仓库是私有的，不要求换成合成数据，但凭据和密钥仍然不能入库。

**E1 — llmlab 基线**
1. 在 `integration/v1-wave14-green-20261018` 和 `integration/v1-wave4-recovered-20261017`（以及 PR #99）之间选一个作为基线，选最新且测试最全的，在回执里写理由。
2. 在基线上合入 PR #62（head `claude/marketops-architecture-audit-u71oo7`）、#70、#71、#127，以及 `docs/architecture-v1-simplified.md`。
3. 生成文件用仓库工具重新生成；不手改生成文件，不改冻结的 V5。
4. 删除 `.gitignore` 里残留的 `>>>>>>> origin/pr/11`，删除所有 `__pycache__`。
5. 已知失败只记录，不修（E2、E3 在修）。新出现的失败必须修。

**E2 — llmlab 的 Python 已知失败**
- `CrossUidRevocationChain`：过期的 `pending_router_sync` fixture。
- V1-68 叠加层的 9 个失败：过期的 applied-route fixture。
- `ProjectionConstantParityTest`。
- 依赖本机真实用户（fjd、vince、zhengji、kaidong）的身份测试：改成不依赖主机账户，用户名换成 alice/bob/carol。

完成标准：`python3 -m unittest discover -s tests -t .` 0 失败。

**E3 — llmlab 浏览器测试**
- `compute-hero` #1、#2：fixture 写死了时间戳，改成按当前时间生成。
- `portal` #53：改成等条件满足再断言，不加 sleep，不放宽断言。

完成标准：`npm run test:browser` 连续 3 次全部通过。

**E4 — 旧 llm-lab 脱敏导入（只新增目录）**
按本文件**附录 A** 的表格处理。主机、IP、端口、路径、用户名换成 `{{LAB_HOST}}` 之类的占位符。

不迁：`config/ssh/*`；旧 PR #17 的请求正文记录；原始 benchmark 样本；含内网地址的截图。

对新增目录跑 gitleaks、trufflehog，结果必须为 0。

**F1 / F2 — 第三方副本核对（只读）**
对每个仓库：
1. 取上游，找出 ProfessorZhi 副本中**上游没有的提交**（覆盖所有分支）。
2. 判断这些提交是不是所有者自己写的。
3. `quay` 的 `measurements/`、`experiments/` 里如果有值得保留的结论，列出它们，以及要摘到 `hwz-records` 的哪里。
4. 结论二选一：「可以安全删除」，或「删除前先摘录 X → Y」。

**F2 结论（2026-10-18）**：`learn-claude-code`（独有提交 0）、`claude-workspace`（占位 README）可以安全删除。
**`claude-fleet` 不是第三方副本**：所有者自己的提交 81 个，相对基线新增约 309 个文件（`docs/`、`agentmetrics/`、`server/` 等），还有 3 个未合并的 PR（#10–#12），与上游没有共同祖先。删除前必须先存档：
```bash
git clone --mirror https://github.com/ProfessorZhi/claude-fleet.git && cd claude-fleet.git
git bundle create claude-fleet.bundle --all     # 约 5MB
git bundle verify claude-fleet.bundle
```
把 bundle 放进私有仓库 `hwz-records` 的 `archive/third-party/claude-fleet/`（超过 GitHub 普通文件建议大小时用 Git LFS），PR #10–#12 的描述也导出为 Markdown 放在一起。存档验证通过后才能删除。

**设备整理的总原则：旧版 Fleet 一直在跑，整理只在它旁边做。**

每台设备上的每个对象（checkout、安装、服务、定时任务、目录）先由 G0 归入下面三类之一，再按类处理。**归不了类的一律按 R 类处理。**

| 类别 | 判定 | 怎么处理 | 什么时候 |
|---|---|---|---|
| **R 运行路径** | 正在运行的 Fleet / serverosd / connector / Work Engine 会读或会执行它：被 systemd 服务、Windows 服务、计划任务、cron、PATH 中的 `serverosctl`、`fleet/profiles.yaml` 里的路径引用；或者有进程正打开它（`lsof`、`/proc/*/cwd`、Windows 的 `Get-Process` 模块路径） | **原地不动**：不 pull、不切分支、不改 remote、不移动、不删除，不停服务。新版装到**新目录**，和旧版并存 | 第 3 波，所有者逐台批准，在维护窗口切换 |
| **S 旁路** | 不被任何运行中的东西引用，但人或脚本会手工用：开发 checkout、旧仓库的 clone、文档副本 | 备份（记录 SHA 和 `remote -v`）后，把 `git remote set-url` 指向新仓库，然后 `git fetch` 核对。**不删除** | 第 3 波，批准后即可，不需要维护窗口 |
| **X 废弃** | 确认没有任何引用，而且内容已经进了新仓库，例如已退役仓库的重复 clone、临时 scan 目录 | 先挪到同盘的 `_retired-20261019/` 目录（只改名，可以一键还原），观察 14 天后再删 | 观察期满，所有者确认后 |

**为什么这样不会影响旧版 Fleet**
- 旧仓库只归档、不删除，所以 R 类 checkout 的 `git pull` 照常能用，不急着切。
- 改 remote 不影响正在运行的进程。只对 S 类做，R 类在切换前保持原样。
- 新版装在新目录，旧版的文件一个都不动。切换本身就是改服务指向的路径或 PATH 顺序；回滚就是改回去，几秒钟。
- `hwz-records`、`serveros` 都不改名，设备 profile 里的路径保持有效。

**G0-T / G0-D / G0-W — 设备只读盘点**
每台设备一个 agent。东京、Dell 用已有的 SSH 访问方式，Windows 就是本机。运行 plan-03 §10.1 的只读命令（Windows 用 PowerShell 的对应命令），再加上「谁在用」的检查：
- systemd、cron、Windows 服务和计划任务的命令行；
- `fleet/profiles.yaml` 里该设备的路径；
- 正在运行的进程的工作目录和打开的文件。

**不修改任何东西。**

输出每台设备一张表，每一行都要有类别：

| 对象（路径） | 类型（checkout / 安装 / 服务 / 定时任务 / 目录） | 远端或来源仓库 | 对应的新仓库 | 谁在用（证据） | 类别 R / S / X |
|---|---|---|---|---|---|

同时列出：`which -a serverosctl workengine`（Windows 用 `Get-Command -All`）的结果，以及写死旧仓库名的服务、定时任务和脚本。

**G1-T / G1-D / G1-W — 设备整理（第 3 波，必须已获所有者批准该设备）**
1. **备份**：记录所有 R、S 类对象的 SHA、`remote -v`、服务定义、PATH，以及 `pip list`。
2. **S 类**：改 remote 指向新仓库，然后 `git fetch` 核对。
3. **X 类**：移到 `_retired-20261019/`，**不删除**。
4. **R 类，先并存**：把新版 serveros（含 `workengine/`）装到新目录。用新目录里的 `serverosctl --help`、`python -m workengine --help`，以及测试 project 上的读写做验证。**这一步不碰旧版。**
5. **R 类，再切换**：只在所有者批准里写明了维护窗口和切换方式时才做。切换 = 把服务或 PATH 改指向新目录，然后验证 Fleet 和这台设备的连接。失败就立刻改回原路径并报告。没有写明，就停在第 4 步。
6. 每台设备单独交回执：每个对象做了什么、验证结果、有没有回滚。

---

## 7. 验收关卡：新仓库验收通过前，不动任何旧仓库

顺序固定：**先把新仓库做完 → 云端 Claude 会话（serveros PR #279 的作者）独立验收 → 验收通过后才清理旧仓库**。
主控 CC 在第 2 波全部完成后，在 PR #279 下留言「请求验收」并列出各新仓库的分支或 PR。验收前**不归档、不删除任何旧仓库**。

验收方在全新 clone 上逐个仓库检查，每项给出「通过 / 不通过 + 证据」：

| # | 检查项 | 怎么查 |
|---|---|---|
| V1 | **内容全部有着落** | 旧仓库每个顶层文件和目录，在新仓库里找到对应位置，或者回执里写了不迁的理由。逐条核对，不抽查 |
| V2 | **测试全绿** | 在全新 clone 里跑该仓库的全部测试命令，0 失败；不允许出现新增的 skip |
| V3 | **Public 仓库无敏感信息** | `gitleaks detect`（包含历史）、`trufflehog git`，结果为 0；grep IPv4、`*.ts.net`、`/home/<用户>`、`/data/home/<用户>`、真实用户名、`sk-` / `ghp_` / `Bearer ` |
| V4 | **没有残留的旧仓库引用** | grep 旧仓库名（`work-engine`、`hwz-ops`、`llm-lab-portal` 等）。剩下的每一处都必须是有意保留的（例如历史说明） |
| V5 | **入口唯一** | serveros 只有一个 `serverosctl`；`python -m workengine --help` 正常；`scripts/release.py` 打出的包里包含 `workengine/` |
| V6 | **设备不依赖旧仓库** | G0 盘点表里，每个指向旧仓库的 checkout、服务、定时任务，都已经切换（G1），或者明确记录为「归档后仍能 pull，可以等」 |
| V7 | **Issue 已处理** | 旧仓库里需要保留的 Issue 已经列出，并转移到新仓库 |

**验收通过后**才按下面的顺序清理旧仓库（所有者操作）：
1. 合并各车道 PR，把 llmlab 推送上去。
2. 把被并入的 10 个旧仓库**归档**：`work-engine`、`serveros-research-exchange`、`dev-doctor`、`agent-software`、`hwz-ops`、`agent-session-archive`、`marketops-private`、`marketops-session-private`、`llm-lab`、`llm-lab-portal`。第三方副本和空仓库：`learn-claude-code`、`claude-workspace` 可以直接删除；`quay`、`meta-cc`、`archguard` 等 F1 结论；**`claude-fleet` 不能直接删**，先按派工卡 F 的做法存档。
3. 观察 14 天，确认没有设备再访问旧仓库后，运行 plan-03 §11 的删除脚本。

## 8. 不在这次冲刺里做

- 那 95 个 V1 Draft PR 的合并或关闭；#266、#146。
- Support Bot 系列仓库（`litellm-support-bot`、`litellm-support`、`troublebuddy-kb-bridge` 等）的去留，等所有者另行决定。

---

## 附录 A：旧 `llm-lab` 内容落点（E4 用）

「脱敏」指把主机名、IP、端口、路径、用户名、密钥换成占位符（如 `{{LAB_HOST}}`）或示例值。E4 只新增目录；表中写到 `backend/` 的部分先放到 `legacy/` 下，由 E-int 集成时决定是否并入。

| 旧仓库内容 | 新仓库位置 | 处理方式 |
|---|---|---|
| `integrations/strata.py`、`openai_compatible.py` | `backend/observability/` | 已由 PR #127 重写；旧文件中有、新文件没覆盖的功能补进去 |
| `deploy/dell/gateway/*`，以及旧 PR #2、#20 的 Gateway、KeyStore、多引擎路由 | `backend/gateway/` | 按简化架构合并成**一个**薄 Gateway。可以复用旧代码，但只保留一套 Key 存储和一套路由，删掉与 Registry 重复的部分 |
| `deploy/dell/caddy/*`、`deploy/dell/node/*.sh`、`labctl.py`、`remote.sh`、`adapter/` | `deploy/` | 脱敏后保留，作为部署模板和运维脚本 |
| `deploy/dell/grafana/*` | `deploy/grafana/` | 去掉数据源 UID 和主机信息后保留 |
| EvalScope 安装脚本 | `deploy/evalscope/` | 保留，同时补一个 Linux 版本 |
| `evaluation/` | `backend/evaluation/` 或 `docs/` | 合并有用的部分 |
| `portal/server.py`、`portal/web/*`（旧控制台） | `legacy/console/` | 保留作参考，不作为运行入口 |
| `scripts/*.ps1`、`scripts/*.py`（Windows 工具） | `legacy/windows-tools/` | 脱敏后保留 |
| `docs/*`（架构、迁移、安全、设计系统） | `docs/legacy/` | 保留；和新文档重复的合并 |
| `config/*.toml` | `config/*.example.toml` | 改成示例配置：结构保留，真实值换成示例值 |
| `config/ssh/*` | — | **不放入**：SSH 主机指纹和公钥属于主机身份信息 |
| `data/benchmarks/*`、`reports/*` | `docs/benchmarks/` | 只保留汇总结论（`summary.md`、`*_summary.json`）；原始样本和含请求内容的文件不放入 |
| `docs/screenshots/*` | `docs/legacy/screenshots/` | 逐张检查，没有内网地址和密钥的才保留 |
| 旧 PR #12（Vision 契约）、#16（并发 2） | `backend/gateway/` 或 `docs/` | 作为文档或未启用的配置选项保留 |
| 旧 PR #17（记录请求正文） | — | **不并入**默认代码路径：隐私风险高。设计思路可以写进 `docs/` |

## 9. 第二轮：在 Dell A6000 上集中冲刺（2026-10-18 起）

第一轮在 Windows 本机跑了约 4 小时，总进度 32%。慢的原因是 Windows 平台问题和单机并发有限。第二轮改到 **Dell A6000（Linux）上开一个 CC 会话**，一次派出多个子 agent。**从这一轮起，这个会话是唯一的主控，也是进度文件唯一的写入者。** Windows 本机只做第 9.1 节的抢救和 G0-W。

### 9.1 开工前：Windows 本机先抢救（所有者执行，约 2 分钟）

第一轮有成果还留在 Windows 磁盘上，不推到 GitHub，Dell 那边看不到：

```powershell
# B-int：8 个提交还没推送
git -C D:\consolidate-b-int\ability-management push -u origin consolidate/ability-management
# D1：15 项改动没有提交，先存到 WIP 分支
git -C D:\consolidation-d1\marketops switch -c consolidate/d1-marketops-wip
git -C D:\consolidation-d1\marketops add -A
git -C D:\consolidation-d1\marketops commit -m "wip(d1): unfinished marketops consolidation from round 1"
git -C D:\consolidation-d1\marketops push -u origin consolidate/d1-marketops-wip
# 以后提交改用 noreply 邮箱（已经推送的提交不改写）
git config --global user.email "67468685+ProfessorZhi@users.noreply.github.com"
```

### 9.2 所有者已做的决定

- **可以暂停 Dell 上 serveros 以外的服务**，例如 LLM Lab 的管理后台、Gateway，用来给集成和测试腾出资源。
- llmlab 基线改用 **`integration/v1-wave15-public-union-20261018`**（wave-14 的超集，多了 #128、#129），由 E1b 补齐。
- 重新派 E2。

### 9.3 Dell 上的边界（比第 1 节更具体）

1. **ServerOS / Fleet 不能停**：serverosd、Fleet connector、Work Engine，以及 G0-D 标为 R 类的路径，一律不动。
2. **暂停其他服务必须可还原**：
   - 先列出要暂停的服务（只限 LLM Lab 的管理后台、Portal、Gateway 这类），记录每个服务的 `systemctl status`、unit 文件路径和启动命令；
   - 再用 `systemctl stop`（不用 disable、不改 unit 文件）；
   - 把恢复命令写进进度文件；冲刺结束后逐个启动，并验证健康检查。
3. **其他用户的东西不碰**：他们自己启动的推理引擎、GPU 任务、文件都不动。冲刺不使用 GPU，所有命令都设置 `CUDA_VISIBLE_DEVICES=""`。
4. **工作目录独立**：所有 clone 都放在所有者自己的数据目录下的 `consolidation-sprint/` 里，不放进 `/srv/llm-lab`，也不放进 Fleet 的路径。
5. **资源**：重的测试用 `nice -n 10` 跑；浏览器测试只监听 127.0.0.1 上的随机高位端口。
6. **GitHub 凭据**：用一个 fine-grained token，只授权这次涉及的仓库，文件权限设为 600；冲刺结束后撤销。
7. Git 提交邮箱用 noreply（见 9.1）。

### 9.4 派工表（一次全部派出，后台并行）

| ID | 仓库 | 分支 | 做什么 | 依赖 |
|---|---|---|---|---|
| A1w | serveros | `feat/workengine-merge-20261019` | 接着 A1 的导入（43 个新文件）做接线：只保留一个 `serverosctl`；`scripts/release.py` 的 `REQUIRED_MEMBERS` 加 `workengine/`；测试改成用仓库内的 workengine；加 MIT `LICENSE`，把 PROPRIETARY 改成 MIT；5 个本机路径脱敏 | — |
| A-int | serveros | `consolidate/serveros` | 合并 A1w + A2（#281）+ A3（#280），在全新 clone 上跑 `scripts/verify.py`、`pytest`、`serverosctl --help`、`python -m workengine --help`、`release.py` 打包 | A1w |
| B-int | ability-management | `consolidate/ability-management` | 在 9.1 推送上来的分支上，跑 Linux 测试并和 main 基线比较，然后开 PR | 9.1 |
| C2 | hwz-records | `consolidate/c2-agent-sessions` | `agent-session-archive` → `archive/agent-sessions/` | — |
| C3 | hwz-records | `consolidate/c3-research-packets` | 新建 `research-packets/`，写 README 说明用途 | — |
| C4 | hwz-records | `consolidate/c4-claude-fleet-bundle` | `claude-fleet` 用 `git bundle create --all` 打包，验证后放进 `archive/third-party/claude-fleet/`（必要时用 LFS），同时导出 PR #10–#12 的说明 | — |
| C5 | hwz-records | `consolidate/c5-quay-extract` | quay 有 138 个所有者独有的提交（要检查**所有分支**，不能只看默认分支），其中约 36 KB 的 LiteLLM 探针证据要摘进 `archive/third-party/quay/`。`measurements/`、`experiments/` 是上游的内容，不摘 | — |
| D1 | marketops | `consolidate/d1-marketops` | 接着 9.1 的 WIP 分支做完 | 9.1 |
| E1b | llm-lab-portal | `consolidate/e1-base` | 把 wave-15 多出来的 #128、#129 合进 E1（PR #131），PR 的 base 改成 wave-15 | — |
| E2 | llm-lab-portal | `consolidate/e2-pytests` | Python 已知失败（见第 6 节 E2 卡） | — |
| E3 | llm-lab-portal | `consolidate/e3-browser` | 浏览器已知失败（见第 6 节 E3 卡），连续 3 次全部通过 | — |
| E5 | llm-lab-portal | `consolidate/e5-sanitize` | 全仓脱敏扫描并修复（`REDACTED` 这类占位符不算命中） | — |
| E-int | llmlab | `main`（初始提交） | E1b + E2 + E3 + E4（#130）+ E5 → 在全新 clone 上跑第 3 节的全部测试 → 脱敏扫描为 0 → 一个初始提交推送到 `llmlab` main | E1b、E2、E3、E5 |
| D-int | — | — | 核对 D1、D2 的回执，列出要转移的 Issue | D1 |

大约 14 个子 agent。没有依赖的 11 个立即并行开工，A-int、E-int、D-int 等依赖完成后马上接着派。

### 9.5 进度

- 只写 `hwz-records` 分支 `status/consolidation-sprint` 的 `STATUS-consolidation-sprint.md`，按文件里已有的固定公式算百分比，每次状态变化就更新。
- 每有一个仓库集成完成，就在文件的「请求验收」一节加一行。云端 Claude 会话读到后按第 7 节验收，**一个仓库通过就可以合并一个**，不必等全部完成。
- 文件里不写真实邮箱、姓名、凭据内容、内网 IP。
