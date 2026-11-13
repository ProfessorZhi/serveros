# ServerOS / Fleet 盘点报告（2026-11-13）

- **目的：** 在做减法之前，先弄清楚哪些代码真的在运行、哪些只被测试引用、哪些没人引用，以及哪些功能实现了不止一遍。**本报告只盘点，不删除任何东西。** 删什么由 Owner 看完后决定。
- **依据：**
  - serveros main 在 `#632` 合并后的代码；
  - ability-management main 在 `#50` 合并后的代码；
  - 三台设备的只读采集报告（hwz-records `information/raw/fleet-collect/2026-11-13/`）。
- **复现：** `python3 scripts/reachability.py --json out.json`。入口清单见 `scripts/reachability-roots.yaml`。

## 0. 结论先行

1. **规模与问题不相称。** serveros 有 1604 个被跟踪的文件。非测试 Python 约 24 万行，测试 Python 约 22.5 万行，Markdown 约 7.6 万行，契约 YAML 约 1.7 万行（44 个文件）。10 月 1 日以后约 6 周内提交了 1180 次。它服务的是一个人、三台设备。
2. **按静态分析，完全没有入口引用的代码不多：** 只有 1.3 万行（42 个文件），主要是一次性 QA 探针。真正的问题不是「死代码」，而是**活着的东西太多、彼此牵连**：
   - 设备上跑的任何一个入口，都会连到 5 万行以上的代码；
   - `serverosctl` 一个命令行入口就连到 17.6 万行，它有 38 个子命令，`runtime/cli.py` 一个文件就有 16,143 行。
3. **只被测试引用的代码有 3.7 万行（86 个文件）。** 大部分是已经完成的一次性迁移、早期的 Fleet Next，以及被新实现取代的旧机制。它们还连带着对应的测试。
4. **同一件事有多套实现：** Fleet V1 与 Fleet Next 双轨；东京跑着 3 个 tailscaled；设备上的服务分别从 4 种不同的代码位置运行；卡片系统有新旧两套。
5. **CI 带着 56 个「已知失败」的测试放行。** ability-management 的 self-test 在 main 上也是红的。
6. **静默故障：** 采集当天至少有 7 处在运行、但已坏或空转的东西，没有任何告警（见 §4）。

## 1. 方法

**入口**是三台设备上实际由 systemd 单元、Windows 计划任务、插件 MCP 启动的程序，再加上安装、构建、发布工具链，共 5 组：东京、A6000、FJPCOS、插件、工具链。

**追踪的边**：
- Python 的 `import`；
- 指向 `*.py` 的路径字符串：本仓库有 150 个文件用 `spec_from_file_location` 按路径加载兄弟模块；
- 模块名字符串（`-m pkg.mod`、`import_module`）；
- shell、PowerShell、systemd 单元、CI workflow 里出现的 `*.py`。

**解析是宽松的**：一个名字可能指向多个文件时，全部算作「被引用」。只有当字符串是说明文字（例如报错信息里提到一个文件名）时，才不算依赖。

**三种状态：**

| 状态 | 含义 |
|---|---|
| LIVE | 从某个运行中的入口或工具链可以静态到达 |
| TEST_ONLY | 只有 `tests/` 里的测试引用它 |
| UNREACHED | 入口和测试都没有引用它 |

**局限**：
- LIVE 只说明「有路径能到达」，不说明「真的被执行过」。比如 `serverosctl` 的 38 个子命令都会被算作 LIVE，即使某个子命令从来没人调用。「实际调用频次」要靠补采（§8）。
- 运行时才拼出来的路径看不到，所以 UNREACHED 是保守结论：真实数字只会更多，不会更少。

## 2. 代码可达性

**总量（不含 tests/ 的 368 个 Python 文件）：**

| 状态 | 行数 | 文件数 |
|---|---|---|
| LIVE | 192,748 | 240 |
| TEST_ONLY | 37,047 | 86 |
| UNREACHED | 12,816 | 42 |

**每个运行中的入口能连到多少代码：**

| 入口 | 在哪跑 | 连到的文件 | 连到的行数 |
|---|---|---|---|
| `bin/serverosctl` | 三个定时单元，状态卡，工作看板，agent 手动调用 | 203 | 175,625 |
| `distribution/device_kit.py` | 安装工具 | 147 | 144,475 |
| `mcp/bin/serveros-agent-mcp` | 插件 MCP | 62 | 64,538 |
| `bin/serverosd` | 东京、A6000 常驻 | 50 | 54,711 |
| `fleet_hub_watch` | 东京常驻 | 88 | 47,183 |
| `bin/serveros-fleet-owned` | 东京常驻（Fleet V1） | 79 | 46,573 |
| `fleet_mcp/node/connector.py` | A6000、FJPCOS 常驻 | 70 | 38,374 |
| `mcp/bin/serveros-node-mcp` | 插件 MCP | 17 | 18,997 |
| `distribution/keycenter/serveros_secret.py` | 三台，每 5 分钟 | 1 | 921 |
| `fleet_mcp/node/status_card.py` | 东京、A6000 | 1 | 133 |

最后两行说明，独立、小而专一的程序完全做得到。上面那些动辄几万行的连接，来自共享的大模块：`runtime/cli.py` 16k 行，`fleet_mcp/fleet.py` 和 `registry.py` 被几乎所有入口引用。

**UNREACHED（12,816 行）：**

| 位置 | 行数 | 是什么 |
|---|---|---|
| `integrations/fleet-v1-qa/` | 9,857 | 30 多个一次性 QA 探针（`qa_probe_*`） |
| `integrations/fleet-v1-release/check_manifest.py` | 1,161 | 发布清单检查 |
| `research-exchange/` 两个 check_packet | 747 | 研究交换协议工具 |
| `integrations/fleet-v1-mcp-conformance/selftest.py` | 470 | 一致性自测 |
| `memory/openviking/backup.py` | 313 | OpenViking 备份 |
| 其他（hub-watch 示例、workengine 演示、两个 `__main__`） | 约 270 | 示例与演示 |

**TEST_ONLY（37,047 行），大头：**

| 位置 | 行数 | 判断 |
|---|---|---|
| `fleet_mcp/deploy/`（next_install、dell_fjd_cutover、binding_migration、next_tailscale_gate、install、release_plan） | 4,636 | 已完成的一次性迁移和 Fleet Next 安装 |
| `runtime/card_period`、`card_surface`、`card_surface_runner`、`card_scenarios` 等 | 4,602 | 旧的卡片系统；现在的卡片由 hub-watch 发 |
| `integrations/fleet-cc-dev-plugin` | 3,351 | 客户端 E2E 测试驱动 |
| `integrations/fleet-v1-verdict-evidence` | 3,014 | 验收证据工具 |
| `runtime/fleet_monitor_source`、`fleet_core_bridge`、`fleet_monitor_plan` 等 | 2,643 | Fleet 监控的旧实现 |
| `integrations/fleet-next-agent-monitor` 及 activation | 2,486 | Fleet Next 的监控（hub-watch 仍复用其中 915 行） |
| `integrations/fleet-v1-network-health` | 1,502 | 网络健康；已被 core-lite 的网络探测（#622）取代 |
| `fleet-app-overlay`、`chatgpt-fleet-next`、`fleet-v1-runtime-parity`、`fleet-v1-milestone-sources`、`v1-effect-reconciliation` | 约 4,000 | 早期打包、平行对照和一次性对账 |
| `mcp/continuation_mcp.py`、`runtime/continuation_control.py`、`runtime/bounded_executor.py`、`workengine/mcp.py`、`memory/handoff.py` 等 | 约 3,000 | 有设计、没接上任何入口 |

## 3. 同一件事的多套实现

| 领域 | 实现 | 现状证据 |
|---|---|---|
| Fleet 控制面 | **Fleet V1**：`bin/serveros-fleet-owned`（东京 `serveros-fleet-v1.service`，公网 `fleet-v1.professorzhi.fun`）<br>**Fleet Next**：`fleet-next-local-goal-supervisor`（代码不在本仓库，跑在 `runtime-assets/<sha>/`）、`fleet-next-tailscaled`、`integrations/fleet-next-*`、`fleet_mcp/deploy/next_*` | Claude 插件指向 V1。东京的 Codex 配置仍指向 Next 的 tailnet 地址，那个地址连不上（`Transport channel closed`）。supervisor 每 2 分钟空转一次，`decision=NO_DISPATCH_CAP`，`fleet: STALE` |
| tailnet | 东京有 3 个 tailscaled：用户级、`tailscaled-fleet`、`fleet-next-tailscaled` | 第三个只服务 Next |
| 监控与播报 | hub-watch（现役）；`runtime/card_*`（旧卡片系统，TEST_ONLY）；fleet-v1-milestone-batch / notify / observer / runner（LIVE）；status card；fleet-next-agent-monitor | 现在发卡的是 hub-watch；milestone 这一套还有没有在发卡，需要补采确认 |
| 网络健康 | core-lite 网络探测（#622，现役）；`fleet-v1-network-health`（TEST_ONLY） | 后者已被取代 |
| 设备侧控制入口 | Fleet 节点连接器（`fleet_mcp/node/connector.py`）；CloudCLI MCP 网关（33 个工具） | 两个控制平面；连接器把 CloudCLI 的工具转发出去 |
| 代码运行位置 | 东京：`runtime-assets/.../current`（77 个 revision）和 `src/serveros-v1` checkout；A6000：`runtime-assets` 和 `~/projects/serveros` 源码树（连接器）以及 `~/projects/fleet-cutover`（隧道）；FJPCOS：`%LOCALAPPDATA%\ServerOS\runtime\fleet-node\<commit>`（7 个版本并存） | 同一台设备上不同服务跑的是不同版本的代码 |
| 条目编号 | Work Engine（`serverosctl workengine`，有 SQLite 分配）与 hwz-records 目录 | LIT-0223 到 0230 已在使用，但没有条目目录（Owner 已定：只由 Git 签发） |

## 4. 运行中但已坏、或空转的东西（采集当天）

| # | 现象 | 持续多久 | 有没有告警 |
|---|---|---|---|
| 1 | 东京 `serveros-fleet-watch` 每轮都报 `TransportFault`，一天 2 万多行日志 | 11-12 19:03 起（#631 修了一个原因，等部署） | 没有 |
| 2 | Fleet V1 的 owner-pat 签名校验失败（401）；A6000 插件显示需要重新认证；Codex 连不上 Fleet MCP | 签名密钥 11-04 轮换以后 | 没有 |
| 3 | 东京 `serveros-continuation-wake.timer` 每 27 毫秒触发一次 | 未知 | 没有 |
| 4 | CloudCLI 里的 Codex 会话显示完成，但读不到回复；run 结束后 `run_get` 返回 `RUN_NOT_FOUND` | 未知（11-09 到 11-11 大量「⚪ 结果不明」与此一致） | 没有 |
| 5 | hwz-records `information/` 停更 | 10-08 起 | 没有 |
| 6 | 东京 swap 32 GB 已用 99.9%；东京的 serverosd 常驻 481 MiB，A6000 上同一个程序只有 33 MiB | 未知 | 没有 |
| 7 | 东京用户级有 12 个 failed 单元（marketops 系列等）；A6000 上 `litellm-daily@final` 被 OOM 杀掉 | 未知 | 没有 |

## 5. 测试与 CI

- `tests/` 下有 395 个文件，约 22.5 万行，和非测试代码差不多一样多。
- CI 用 `tests/hermetic-known-failures.txt` 放行 **56 个已知失败的测试**，分布在 24 个测试文件里（understanding_delta 5 个、workengine_v3_context_resolve 4 个、understanding_patch 4 个、path_authority 4 个，等等）。其中 2 个的结果取决于测试的执行顺序。
- 只测 TEST_ONLY 代码的测试，删掉对应代码时一起删。

## 6. 文档与契约

| 类别 | 行数 |
|---|---|
| `docs/` 下的 Markdown | 40,110 |
| `integrations/` 下的 Markdown | 16,314 |
| `ARCHITECTURE.md` | 1,706 |
| 契约 YAML（44 个文件） | 17,265 |

单元文件和 profile 里有大段注释。例如 `runtime/serverosd.service` 的注释里，有一整张表格解释 `ReadWritePaths`；`plugin/profiles/linux-work.yaml` 有几百行注释在解释 cachebuster。机制需要靠这么长的解释来维护，说明机制本身该改了。

## 7. ability-management

| 指标 | 数值 |
|---|---|
| 非测试 Python | 31,732 行（其中 `control_plane.py` 5,841 行） |
| 测试 Python | 6,823 行 |
| Markdown | 24,472 行（其中设计文档 8,920 行） |
| 登记表 `abilities-manifest.yaml` | 4,508 行，登记 70 条 |
| 实际的 Skill 目录 | 44 个，payload 文件 120 个 |
| 入库的生成文件（标了 GENERATED / DO NOT EDIT） | 79 个 |
| 10 月以来的「重新钉住 / 重新生成」类提交 | 十几次（包钉住要求整份登记表逐字节一致） |
| serveros 里读取 `product_role` 的文件 | 12 个，主要用于渲染视图 |
| serveros 里读取能力族视图的文件 | 1 到 2 个 |

**结论**：登记、钉住、生成这套机制，比它管理的 Skill 还重。Claude Code 和 Codex 都有原生的插件市场，可以直接承担分发和版本管理。

## 8. 还不知道的（需要补采）

1. **实际调用频次。** 过去 30 天里，在三台设备的 Claude Code 和 Codex 会话记录中，统计：
   - `serverosctl <子命令>` 每个子命令出现了多少次；
   - serveros-node 和 serveros-agent 两个 MCP 的每个工具各被调用多少次；
   - Fleet MCP 的每个工具各被调用多少次（东京 Fleet 的审计日志）。
2. **milestone 那一套（batch、notify、observer、runner）还在不在发卡。**
3. **fleet-next-local-goal-supervisor 的代码来源，以及它现在做什么。**
4. **东京 serverosd 为什么常驻 481 MiB。**

```text
只读采集，规则同前（不读密钥值，不读原文，只统计）。写到 hwz-records information/raw/fleet-collect/2026-11-14/usage/，开 PR。
1. 三台设备，最近 30 天的 Claude Code（~/.claude/projects/**/*.jsonl）和 Codex（~/.codex/sessions/**）会话记录：
   - 统计 "serverosctl <子命令>" 出现的次数，按子命令列表；
   - 统计 serveros-node、serveros-agent 两个 MCP 每个工具的调用次数；
   - 统计 serveros-fleet MCP 每个工具的调用次数。
   只输出计数，不输出会话内容。
2. 东京 Fleet V1 的审计日志（fleet_mcp/audit.py 写的位置）：最近 30 天每个工具的调用次数。
3. 东京：
   - milestone-batch / notify / observer / runner 这几个程序有没有在跑：单元或定时任务名，最近一次运行时间；
   - fleet-next-local-goal-supervisor 的 config.json 里的键名（不要值），以及它最近 1 天的决策分布；
   - 它的代码来自哪个仓库、哪个提交。
4. 东京 serverosd：执行 ps -o rss,vsz,cmd --ppid <MainPID> 和 /proc/<pid>/smaps_rollup，看内存占在哪里。
```

## 9. 建议（等 Owner 决定）

按风险从低到高排列。每一步都单独开 PR，CI 全绿才合并，删除前先打 tag 便于找回。

| 步骤 | 内容 | 影响 | 风险 |
|---|---|---|---|
| D1 | 删除 UNREACHED 的 1.3 万行：fleet-v1-qa、fleet-v1-release 的 check_manifest、示例和演示 | 无入口、无测试引用 | 低 |
| D2 | 删除「已完成的一次性迁移」和「已被取代」的 TEST_ONLY 代码，连同它们的测试：`fleet_mcp/deploy/*`、`fleet-v1-network-health`、旧卡片系统 `runtime/card_*` 等 | 约 1.5 万行代码，外加对应的测试 | 低到中（需要 §8 第 2 条的补采确认） |
| D3 | **结束双轨，保留 Fleet V1**：停掉 Next 的 supervisor 和 tailscaled，Codex 配置改指 V1，删除 `integrations/fleet-next-*`（hub-watch 复用的那 915 行先迁入 hub-watch） | 东京少 2 个常驻服务；V1 成为唯一 | 中（需要 §8 第 3 条的补采确认） |
| D4 | **CI 清零**：56 个已知失败的测试，逐个修好，或者连同它们测的代码一起删掉。从此不允许「已知失败」 | CI 回到真正的红绿 | 中 |
| D5 | **一个部署来源**：每台设备的所有服务都从同一个 revision 运行（`runtime-assets/.../current`），不再从源码树、worktree 或 cutover 目录直接跑 | 版本可以对齐、可以读回 | 中 |
| D6 | **拆 `serverosctl`**：按 §8 第 1 条的实际调用频次，保留真正被用的子命令，其余删除或迁出 | `runtime/cli.py` 预计大幅缩小 | 中 |
| D7 | **合并设备侧控制入口**：节点连接器和 CloudCLI MCP 网关只保留一个对外入口 | 链路少一跳 | 中到高（需要单独设计） |
| D8 | **ability-management 改用原生插件市场分发**：版本 = git 提交号，登记表从 Skill 的元数据生成，生成文件不入库，包不再钉整份登记表 | 改一个 Skill 只需动它本身 | 中（需要单独设计） |
| D9 | **文档瘦身**：`docs/` 里的历史文档移到 `docs/archive/`；`ARCHITECTURE.md` 改写为 300 行以内的现状说明；单元文件和 profile 里的长注释移进文档 | 读者和 agent 加载的上下文都变少 | 低 |

**第 4 节的 7 个故障，和减法并行修**：它们是现有功能已经坏了，不属于新功能。

不在本报告范围内的：hwz-records 的结构，以及 CloudCLI fork 的代码。
