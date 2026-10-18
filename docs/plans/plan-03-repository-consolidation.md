# Plan 03 — GitHub 仓库收敛与设备同步（并行执行版）

> **状态**：方案，尚未执行。2026-10-18 由账户所有者 ProfessorZhi 确认方向。
> **执行者**：多个 Claude Code 并行执行，每条车道一个独立 CC 和独立 worktree。
> **2026-10-18 所有者已确认**：① 同意公开 work-engine 源码；② `serveros` 采用 MIT 许可证；③ ~~`marketops-session` 保持 Public~~ → **更正（同日）：`ability-management`、`marketops-session` 都保持 Private**。最终 3 Public（serveros、marketops、llmlab）+ 3 Private（ability-management、marketops-session、hwz-records）。
> **本文是执行依据。** 以下仓库归类和文件统计，均来自 2026-10-18 对每个仓库逐一 clone 后的实际查看。

---

## 0. 最终结构

三个项目，6 个仓库：

| 项目 | 仓库 | 可见性 | 并入的旧仓库 |
|---|---|---|---|
| ServerOS | `serveros` | Public | `work-engine`、`serveros-research-exchange`（模板和工具部分） |
| ServerOS | `ability-management` | **Private**（2026-10-18 所有者决定） | `dev-doctor`、`agent-software` |
| ServerOS | `hwz-records` | Private | `hwz-ops`、`agent-session-archive` |
| MarketOps | `marketops` | Public | `marketops-private` |
| MarketOps | `marketops-session` | **Private**（2026-10-18 所有者决定） | `marketops-session-private` |
| LLM Lab | `llmlab`（新建） | Public | `llm-lab`、`llm-lab-portal` |

另外直接删除：第三方项目副本 `quay`、`meta-cc`、`archguard`、`learn-claude-code`，以及空仓库 `claude-workspace`。**`claude-fleet` 不是第三方副本**（所有者自己的提交 81 个），删除前先用 `git bundle` 存档到 `hwz-records/archive/third-party/claude-fleet/`，见 plan-04 派工卡 F。

**本次不处理**：Support Bot（`litellm-support-bot`、`litellm-support`、`troublebuddy-kb-bridge`）以及与三个项目无关的其他仓库。

### 关于 ServerOS 的仓库数

ServerOS 的**代码**只在两个仓库：`serveros`（完整产品，含 Work Engine）和 `ability-management`（能力注册与分发）。

`hwz-records` 是 ServerOS 运行时读写的**私有数据**仓库，存放语义记录、节点拓扑、权限策略、测试机清单，既不能公开也不能删除。仓库名保持不变，因为节点上的路径和设备 profile 都引用了这个名字。

---

## 1. 全局规则（所有车道都必须遵守）

1. **账户级操作只能由所有者完成**：新建、改名、改可见性、归档、删除仓库，以及安装 GitHub App。CC 的 GitHub 授权没有这些权限。CC 准备好清单和命令，然后等待所有者。
2. **设备上的变更必须先获得所有者批准**：东京、Dell、Windows 上的服务、安装、systemd、PATH、Git remote。每台设备单独批准、单独回滚。没有批准时只做只读盘点。
3. **公开前必须脱敏**。Public 仓库中不得出现：密钥、内网地址和主机名、本机路径（`/home/<用户>`、`/data/home/<用户>`、`/opt/applications/data/home-hwz`）、真实用户名和邮箱、公司内部资料、联系人或客户数据、会话语料原文。用 `gitleaks detect`（包含 Git 历史）和 `trufflehog` 扫描，结果必须为 0。
4. **先迁入、再归档、最后删除**。删除一个旧仓库之前，报告里必须写明：哪些内容迁到了哪里，哪些有意不迁、为什么。
5. **测试结果如实报告**，不能删测试或放宽校验来换取通过。
6. **每条车道只写自己负责的仓库**，避免两个 CC 同时改同一个仓库。

---

## 2. 并行车道总览

```
时间 →
T0  所有者：冻结 work-engine、新建 llmlab、给 GitHub App 授权（约 10 分钟）
    │
    ├─ 车道 A  ServerOS：work-engine → serveros ────────┐
    ├─ 车道 B  ability-management 合并（Private）──────────┤
    ├─ 车道 C  hwz-records 合并私有数据 ───────────────┤
    ├─ 车道 D  MarketOps 收敛 ─────────────────────────┤
    ├─ 车道 E  LLM Lab → llmlab ───────────────────────┤
    ├─ 车道 F  第三方副本核对 ────────────────────────┤
    └─ 车道 G0 各设备只读盘点（东京 / Dell / Windows）─┤
                                                        ▼
    车道 G1  各设备同步（A、C 合并后开始，每台设备一个 CC，可并行）
                                                        ▼
    所有者：归档旧仓库 → 观察期 → 删除
```

| 车道 | 负责写入的仓库 | 依赖 | 预计耗时 |
|---|---|---|---|
| A | `serveros` | T0 冻结 | 1–2 天 |
| B | `ability-management` | 无 | 0.5–1 天 |
| C | `hwz-records` | 无 | 0.5 天 |
| D | `marketops`、`marketops-session` | 无 | 0.5 天 |
| E | `llmlab` | T0 新建仓库 | 2–4 天 |
| F | 不写，只读核对 | 无 | 1–2 小时 |
| G0 | 不写，各设备只读盘点 | 无 | 每台约 1 小时 |
| G1 | 各设备的本地 checkout 和安装 | A、C 合并；所有者逐台批准 | 每台约半天 |

**车道 A–F 和 G0 在 T0 之后同时开始。** 整体预计 3–5 天完成，瓶颈是车道 E。

---

## 3. T0：所有者先做的事（约 10 分钟）

1. **冻结 `work-engine`**：在仓库 README 顶部和每个开放 PR 里留言「迁移进行中，暂停合并」。
   当前开放的 PR（2026-10-18）：
   - #16 `feat(lease): serverosd #272 verified-read adapter for OwnerVerifiedLease`（今天更新）
   - #15 `feat(finalize): T10-required entry route with exact runner binding`（今天更新）
   - #14 `feat(authority): fail-closed lease and runner-receipt seams`（今天更新）
   - #13 `feat(lease): exclusive Work write lease with monotonic token fencing`（今天更新）
   - #3 `work-engine: 路径解析与准入（P1）`（9 月 27 日，较旧）
2. 新建 **Public** 空仓库 `ProfessorZhi/llmlab`，不做任何初始化。
3. 在 https://github.com/apps/claude/installations/select_target 给 Claude GitHub App 授权所有最终仓库。
4. ~~确认许可证~~ **已确认（2026-10-18）**：所有者同意公开 work-engine 源码；`serveros` 采用 **MIT**。车道 A 负责在 `serveros` 根目录新增 `LICENSE`（MIT，版权人 ProfessorZhi），并把 `workengine` 的 `license = { text = "PROPRIETARY" }` 改为 MIT。

---

## 4. 车道 A：`work-engine` 并入 `serveros`

### 4.1 已核实的事实

- `work-engine` main = `19d9520`：43 个文件，约 8,700 行 Python，没有第三方依赖。包名 `workengine`，有 CLI、MCP（`serveros-work-mcp`）、`schema.sql` 和 18 个测试文件。
- **命令冲突**：`work-engine` 的 `pyproject.toml` 把 `serverosctl` 和 `workengine` 都注册为命令；`serveros` 的 `serverosctl` 则来自 `bin/serverosctl` → `runtime/cli.py`。同一台机器装了两者，就会争用同一个命令。
- `serveros` **没有 `pyproject.toml`**，节点运行时由 `scripts/release.py` 打包（`REQUIRED_MEMBERS` 里有 `bin/serverosctl`、`runtime/cli.py` 等）。
- `serveros` 目前把 work-engine 当作**外部 checkout** 使用：
  - `tests/test_workchain.py`、`tests/test_global_board.py`：引用外部 `…/workengine` 目录；
  - `tests/work_delivery_slice.py`：执行 `python -m workengine`；
  - `scripts/runtime_truth.py`：计算 `engine_repo/workengine` 的哈希，与节点安装做对比；
  - `runtime/cli.py`：有 `--work-engine` 参数，未绑定时报 `WORK_ENGINE_UNBOUND`。
- `scripts/verify.py` 的 `discovered_tests()` **只收集 `tests/test_*.py` 这一层**，放进子目录的测试不会被统计。

### 4.2 步骤

1. **处理开放 PR**：#13–#16 不再合并进 `work-engine`。迁移完成后，在 `serveros` 上逐个重新提 PR 移植过来；#3 由所有者决定移植或关闭。每个 PR 的去向写进报告。
2. **导入代码，保留历史**：
   ```bash
   git filter-repo --to-subdirectory-filter workengine-import   # 在 work-engine 的临时 clone 上执行
   # 然后在 serveros 中 merge --allow-unrelated-histories，再移动到最终位置
   ```
   如果 `gitleaks` 发现历史里有敏感内容，改为只导入当前文件，提交说明里写明来源提交 `19d9520`。
3. **目录布局**：
   - `workengine/`：原包，**Python 导入路径 `workengine` 保持不变**；
   - `tests/test_workengine_*.py`：测试移到 `tests/` 顶层并加前缀，这样 `verify.py` 能统计到；
   - `docs/workengine/`：原 `docs/`；
   - `demo/` 并入 `docs/workengine/` 或 `scripts/`。
4. **统一入口**：
   - 只保留 `bin/serverosctl` → `runtime/cli.py` 这一个 `serverosctl`；
   - Work Engine 的命令作为 `serverosctl work …` 子命令接入，或者保留 `python -m workengine` 供内部调用；**不再安装第二个 `serverosctl`**；
   - `serveros-work-mcp` 保持为独立进程入口；
   - `scripts/release.py` 的 `REQUIRED_MEMBERS` 加入 `workengine/` 和 `workengine/schema.sql`。
5. **把外部 checkout 改成仓库内路径**：
   - 测试中的外部 `…/workengine` 改为仓库内的 `workengine/`；
   - `runtime_truth.py` 的 `engine_repo` 默认指向仓库根目录；
   - `--work-engine` 默认绑定仓库内的 `python -m workengine`，`WORK_ENGINE_UNBOUND` 只在显式解绑时出现。
6. **脱敏**：5 个文件含本机路径（例如 `tests/test_v3_cognitive_replay.py` 里的 `/data/home/vince/.local/bin/workengine`），改成通用写法。
7. **更新文档**，把「work-engine 是单独仓库」改为新结构：
   - `README.md`、`README.zh.md` 的 Ownership 表；
   - `ARCHITECTURE.md` 中的所有权描述；
   - `docs/architecture/DIRECTORY-CONVERGENCE.md` 中「GitHub 不合仓凑整齐」一段，改为本方案第 0 节的六仓库结构。
8. **验证**（一个全新 clone，0 失败）：
   - `python3 scripts/verify.py`
   - `pytest`（原 serveros 全部测试 + 新增的 `test_workengine_*`）
   - `bin/serverosctl --help` 正常；`python -m workengine --help` 正常
   - `scripts/release.py` 能打出包含 `workengine/` 的节点包
9. 开 Draft PR，合并后通知车道 G1。

### 4.3 本车道不做

**不**把现有的 `runtime/`、`fleet/` 重组成新的 `engine/` 目录。那是单独的大重构，和这次迁移混在一起会让 PR 无法审查。如果需要，等迁移合并后另开 Plan。

---

## 5. 车道 B：`ability-management` 并入两个小仓库（保持 Private，以下公开相关步骤作废）

1. 并入：
   - `dev-doctor`（21 个文件）→ `skills/dev-doctor/`
   - `agent-software`（3 个文件）→ `software/`
2. 脱敏：扫描发现 `ability-management` 有 31 个文件、`dev-doctor` 有 6 个文件含本机路径、内网地址或公司相关字样。逐个改成通用写法，或者移到 `hwz-records`（通知车道 C）。
3. 用 `gitleaks detect` 扫描**包含历史**：
   - 历史干净 → 请所有者把 `ability-management` 改为 Public；
   - 历史不干净 → 向所有者建议：新建一个干净仓库，用单个初始提交导入，旧仓库归档。由所有者决定。
4. 更新 `serveros` 中指向 `ability-management` 的链接（通过车道 A 的 PR 统一修改，不要直接写 `serveros`）。

---

## 6. 车道 C：`hwz-records` 合并私有数据

1. `hwz-ops`（203 个文件）→ `hwz-records/ops/`。保留其中 `GOVERNANCE-SPEC.md`、`maps/`、`inventory/`、`runbooks/`、`scripts/`、`archive/` 的原有结构。
2. `agent-session-archive`（10 个文件，只有元数据）→ `hwz-records/archive/agent-sessions/`。其中 `sessions-with-credentials.json` 属于敏感索引，只能放在这个私有仓库里。
3. 新建 `hwz-records/research-packets/`：今后研究材料包放在这里（原 `serveros-research-exchange` 只是通道，模板和工具由车道 A 并入 `serveros/research-exchange/`）。
4. 更新 `hwz-ops` 原有的引用：`scripts/bootstrap.sh` 中的仓库地址，以及 `skills/workspace-management` 中的路径。`serveros` 中的引用（Ownership 表、`fleet/` 下的 profile）由车道 A 的 PR 修改。
5. 输出一张「旧路径 → 新路径」对照表，交给车道 G1。

---

## 7. 车道 D：MarketOps

1. `marketops-session` **保持 Private**（所有者 2026-10-18 最终决定，已改为 Private）。凭据和密钥仍然不能入库。
2. 对比 `marketops` / `marketops-private` 和 `marketops-session` / `marketops-session-private`，补齐新仓库缺少的内容。
3. `marketops` 公开前严格清理：不能有任何真实人名、公司名、邮箱、电话、社交账号，全部换成合成数据。
4. 把 `marketops-session-private` 中仍有用的内容补进 `marketops-session`（Private），在报告中列明落点。然后 `marketops-session-private` 进入归档和删除流程。
5. `marketops-private` 中仍有效的 Issue 转到 `marketops`。

---

## 8. 车道 E：LLM Lab → `llmlab`

按 `llm-lab-portal` 仓库中 `docs/architecture-v1-simplified.md` 的决策，以及所有者另行提供的 `llmlab-consolidation-plan.md` 执行，要点：

1. 以 `llm-lab-portal` 最新的集成候选为主体，并入 PR #62、#70、#71、#127；修完已知失败的测试，直到全部通过。
2. 整合 `llm-lab`：Gateway 合并成一个薄 Gateway；部署脚本脱敏成模板；旧控制台和 Windows 工具放进 `legacy/`；不迁入 SSH 主机信息、原始评测数据和记录请求正文的代码。
3. 脱敏扫描后，用**一个初始提交**推送到 `llmlab`。
4. 所有者确认 Dell 上没有脚本从旧仓库拉代码（由车道 G0 的盘点结果确认）。

---

## 9. 车道 F：第三方副本核对（只读）

对 `quay`、`meta-cc`、`archguard`、`claude-fleet`、`learn-claude-code`：

1. 找出原作者的上游仓库，对比确认**有没有所有者自己的独有提交**；
2. 有独有内容的，摘要写进 `hwz-records`（通知车道 C）。例如 `quay` 中的 `measurements/`、`experiments/` 是所有者的实验数据；
3. 输出结论：可以安全删除 / 需要先摘录哪些内容。

`claude-workspace` 只有一个空 README，可以直接删除。

---

## 10. 车道 G：各设备同步

设备：**东京共享 Linux**、**Dell A6000**、**Windows**。每台设备一个 CC，可以同时进行。

### 10.1 G0：只读盘点（T0 后立即开始，不需要批准）

在每台设备上运行，**只读，不修改任何东西**：

```bash
# 1. 找出所有 Git checkout，以及它们指向哪个远端仓库
for d in $(find / -xdev -type d -name .git 2>/dev/null); do
  r=$(dirname "$d"); echo "$r  $(git -C "$r" remote get-url origin 2>/dev/null)"
done
# 2. 找出 work-engine 的安装和命令
which -a serverosctl workengine serveros-work-mcp 2>/dev/null
pip list 2>/dev/null | grep -i workengine
# 3. 找出引用旧仓库名的服务、定时任务和脚本
grep -rl -E "work-engine|hwz-ops|serveros-research-exchange|llm-lab-portal|llm-lab|marketops-private|dev-doctor|agent-software" \
  ~/.config/systemd /etc/systemd/system /etc/cron* ~/.local/bin 2>/dev/null
crontab -l 2>/dev/null | grep -E "work-engine|hwz-ops|llm-lab"
```

Windows 上用对应的 PowerShell 命令（`Get-ChildItem -Recurse -Filter .git -Directory -Force`、`Get-Command serverosctl -All`、计划任务等）。

**输出**：每台设备一张表，列出 checkout 路径、远端仓库、它属于哪个旧仓库、对应的新仓库、谁在使用它（服务 / 定时任务 / 手工）。

### 10.2 G1：同步（车道 A、C 合并后，**每台设备分别获得所有者批准**）

对每台设备：

1. **备份**：记录当前每个 checkout 的提交 SHA 和 `remote -v`，导出 `pip list` 和 PATH 中的命令。
2. **更新 checkout**：
   - `hwz-records`：`git pull`；原 `hwz-ops` 的 checkout 按车道 C 的对照表改用 `hwz-records/ops/`；
   - `work-engine` 的 checkout：停止使用，改为 `serveros` 内的 `workengine/`；
   - `marketops-private` → `git remote set-url origin https://github.com/ProfessorZhi/marketops`，然后 `git fetch` 并核对；
   - `llm-lab`、`llm-lab-portal` → 改为 `llmlab`（等车道 E 完成）。
3. **重装 ServerOS**：
   - `pip uninstall workengine`，确认 PATH 中只剩一个 `serverosctl`（来自 `serveros`）；
   - 用 `scripts/release.py` 产出新的节点包，按现有发布流程安装；
   - 更新 `fleet/` 设备 profile 中的路径。
4. **验证**：
   - `which -a serverosctl` 只有一行；
   - `serverosctl --help`、`python -m workengine --help` 正常；
   - Work Engine 的 store 可以读写（用测试 project，不要动真实的 Item）；
   - `serverosd` 状态正常，Fleet 与这台节点的连接正常。
5. **回滚**：任一验证失败，按第 1 步的备份恢复原 checkout 和安装，报告原因。
6. **每台设备单独报告**：改了什么、验证结果、是否回滚。

---

## 11. 收尾：归档与删除（所有者执行）

**归档**（全部车道和设备同步完成后立即进行）：把被并入的旧仓库设为 Archive（只读），包括 `work-engine`、`hwz-ops`、`serveros-research-exchange`、`dev-doctor`、`agent-software`、`agent-session-archive`、`marketops-private`、`marketops-session-private`、`llm-lab`、`llm-lab-portal`。

**观察期**：建议 14 天。期间如果有设备或脚本还在访问旧仓库，可以随时恢复。

**删除**（观察期结束后，所有者在自己电脑上执行，先运行 `gh auth refresh -s delete_repo`）：

```bash
#!/usr/bin/env bash
set -euo pipefail
REPOS=(
  # 第三方副本和空仓库，可以最先删除
  ProfessorZhi/claude-workspace ProfessorZhi/learn-claude-code ProfessorZhi/meta-cc
  ProfessorZhi/archguard ProfessorZhi/quay
  # ProfessorZhi/claude-fleet   # 先完成 bundle 存档（plan-04 派工卡 F）再删
  # 已并入的旧仓库（观察期后）
  # ProfessorZhi/agent-software ProfessorZhi/dev-doctor ProfessorZhi/serveros-research-exchange
  # ProfessorZhi/agent-session-archive ProfessorZhi/hwz-ops ProfessorZhi/work-engine
  # ProfessorZhi/marketops-private ProfessorZhi/marketops-session-private
  # ProfessorZhi/llm-lab ProfessorZhi/llm-lab-portal
)
for r in "${REPOS[@]}"; do
  echo "即将删除 $r（不可恢复）"; gh repo view "$r" --json name,visibility,isArchived,pushedAt
  read -r -p "输入仓库全名 $r 确认删除：" ans
  [ "$ans" = "$r" ] && gh repo delete "$r" --yes || echo "跳过 $r"
done
```

删除仓库会同时删除它的 Issue 和 PR 讨论。需要保留的 Issue，删除前先用「Transfer issue」转到最终仓库。

---

## 12. 完成标准

- [ ] 三个项目只剩 6 个仓库，可见性与第 0 节一致（`hwz-records` 是唯一的 Private）；
- [ ] `serveros` 根目录有 MIT `LICENSE`，`workengine` 不再标记为 PROPRIETARY；
- [ ] 三个 Public 仓库（serveros、marketops、llmlab）的 `gitleaks`（包含历史）和 `trufflehog` 扫描结果均为 0；
- [ ] `serveros` 全部测试通过，包括 `test_workengine_*`；安装后只有一个 `serverosctl`；
- [ ] `work-engine` 的开放 PR 都有明确去向（已移植 / 已关闭）；
- [ ] 东京、Dell、Windows 三台设备都已同步并通过验证，没有任何 checkout、服务或定时任务还指向旧仓库；
- [ ] ServerOS 的 README、ARCHITECTURE、DIRECTORY-CONVERGENCE 已更新为新结构；
- [ ] 旧仓库已归档，观察期后已删除。

---

## 13. 每条车道的汇报格式

- 车道名称，完成了什么，PR 链接和提交 SHA；
- 测试和扫描的命令及结果（通过 / 失败 / 跳过的数量）；
- 迁移清单：旧仓库的哪些内容去了哪里，哪些有意不迁；
- 需要所有者做的操作（网页上的、或设备上需要批准的）；
- 阻塞项和原因。
