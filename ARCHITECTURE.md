# ServerOS / Fleet 架构（现状）

> **这份文件说的是「现在是什么、正在往哪改」**，2026-11-14 按 Owner 对齐的需求重写。
>
> **旧版：** 2026-09 至 11-13 的完整规范（1,706 行，35 节）原样保存在
> [docs/archive/ARCHITECTURE-2026-11-13.md](docs/archive/ARCHITECTURE-2026-11-13.md)，
> 中文导读在 [docs/archive/ARCHITECTURE-guide-2026-11-13.zh.md](docs/archive/ARCHITECTURE-guide-2026-11-13.zh.md)。
> 代码注释和旧文档里写的「ARCHITECTURE §N」「invariant N」，指的都是那一版的章节号。
>
> **需求以 [docs/requirements/fleet-requirements.md](docs/requirements/fleet-requirements.md) 为准**；
> 哪些代码在跑、哪些没人用，见 [盘点报告](docs/audit/2026-11-13-inventory.md)。

## 1. 一句话

ServerOS 是给 Claude Code 和 Codex 用的个人工作系统。**Fleet** 是它跨三台设备的部分：
Owner 从任何客户端派活，设备上的原生 agent 去做，结果、时间线和告警回到 Owner 手里。
它不替代模型，也不在模型之上再造调度器。

## 2. 三台设备

| 设备 | 角色 | 主要职责 |
|---|---|---|
| 东京（共享云服务器） | Hub + 完整工作流 | Fleet Hub、监控与飞书播报、信息采集与结构化认识、需求与条目、每日对齐 |
| FJPCOS（Windows） | 个人工作站 | 个人项目；生产日志查询（生产服只读通道、归档机查询与子集拉取）；接受派活 |
| A6000（Dell，GPU） | 计算节点 + LLM Lab | LLM Lab（GPU 资源管理、模型上线与服务）；计算任务；日志分析；接受派活 |

FJPCOS 和 A6000 不按东京的标准整理，也不跑完整工作流（需求清单 §3）。A6000 上的 LLM Lab 是在用的服务，Fleet 的改动不能影响它。

## 3. 运行中的组成部分（2026-11-13 采集）

```text
指挥侧（只有 Owner）
  chat 类：ChatGPT、claude.ai ──┐
  agent 类：各设备的 Claude Code / Codex ──┤  Fleet MCP（OAuth）
                                          ▼
东京 Hub
  serveros-fleet-v1      Fleet MCP 服务（bin/serveros-fleet-owned → runtime/fleet_mcp_owner.py）
  serveros-fleet-watch   监控：发现派发、读运行状态、资源读数、飞书卡片、日志（integrations/fleet-hub-watch）
  事件收件箱             SQLite，归 Hub 所有（fleet_mcp/inbox.py）
  fleet-journal          每次派发的时间线与结果（Git 仓库）
                                          │ ssh 反向隧道
                                          ▼
每台设备
  节点连接器 + 隧道      fleet_mcp/node/connector.py，start-connector / start-tunnel
  CloudCLI              驱动 Claude Code / Codex 会话（ProfessorZhi/claudecodeui 的 fork）
  serverosd             常驻节点进程（bin/serverosd → runtime/daemon.py）
  core-lite 发布         资源与网络读数（native/node-core/core-lite，C++）
  状态卡                fleet_mcp/node/status_card.py
  密钥同步              distribution/keycenter/serveros_secret.py（只下发本设备的 bundle）
  插件 MCP              serveros-node、serveros-agent（mcp/bin/）
```

**一次派活的路径：** 指挥侧调用 Fleet MCP → Hub 记入收件箱并开始盯 → 经隧道到节点连接器 →
CloudCLI 启动或续接会话 → Claude Code / Codex 干活 → Hub 读到完成、取回结果 →
fleet-journal 记时间线 → 飞书私发 Owner 一张卡片（一次完成一张，失败标红，状态不明不报成功）。

链路基线（11 月 69 个 run）：派发到 Hub 发现 P50 13 s；设备完成到取回 P50 1 s；取回到卡片 P50 2 s。
最大的等待是 Hub 轮询，下一步改成设备主动推送（Rust，见 §7）。

## 4. 仓库与权威

| 仓库 | 是什么 | 权威范围 |
|---|---|---|
| `ProfessorZhi/serveros` | Fleet 与节点的代码、需求、盘点 | 运行时代码；需求清单 |
| `ProfessorZhi/ability-management` | Skill、MCP、插件、规范文件、hook 的源码与登记 | 能力的身份、来源、版本、分发 |
| `ProfessorZhi/hwz-records` | 信息与工作记录：`information/`（原始 → 认识）与 `projects/`（项目、条目、报告） | 项目、编号、需求、报告 |
| `ProfessorZhi/claudecodeui` | CloudCLI 的 fork | 设备侧会话驱动（只补薄的一层 MCP 工具） |
| `ProfessorZhi/fleet-journal` | 派发时间线 | 每次派发发生了什么 |

一个事实一个权威。飞书是单向发布的视图，不是第二权威；设备运行事实以设备读回为准。

## 5. 工作模型

```text
信息采集 → 结构化认识 → 需求 → 调研 → 设计 → 开发 → 测试 → 报告
              ↑______________________________________________|   （循环，不是瀑布）
```

| 层级 | 例子 | 说明 |
|---|---|---|
| 项目 | litellm，前缀 `LIT` | 前缀登记在 hwz-records 的 `projects.yaml`；新前缀由 Owner 批准 |
| 条目 | `LIT-0222` | 一件具体的事；指挥侧与设备侧都能开，编号只由 Git 签发（推送成功才算） |
| 活动 | 复现 → 修复 → 测试 → 交付 | 条目内的依赖图（`plan.yaml`） |
| 运行 | `RUN-0007` 失败 → `RUN-0008` 通过 | 每个活动可有多次运行，失败的也留记录 |

## 6. 底线

不因任何改造放松：

1. **凭据永不进仓。** agent 只看得到变量名；不把密钥的值读进上下文或脚本内存；按值查找必须走密钥中心的专用命令。
2. **Git 是权威。** 飞书单向发布。需求、编号、报告只在 Git 存一份。
3. **所有执行都有界。** 外部操作有超时、整体期限、进程树回收、单实例；重负载有内核内存上限
   （production-observation 的资源底线：`systemd-run --scope MemoryMax` / `prlimit`，做不到就不执行）。
4. **如实报告。** 状态不明不报成功；结果不完整标 PARTIAL；没有「已知失败」的测试。
5. **先问 Owner 的事：** 删生产数据、改生产服、开新前缀、新建或改凭据、对外可见的动作。
6. **改造的方式：** 每步一个 PR，CI 全绿才合并；先并行再切换，不中断运行中的服务；部署由本地 Claude Code 执行并回执。

## The resident surface (frozen)

> 这一节原样沿用旧版规范（`tests/test_core_freeze.py` 守着它）。它是仍然生效的约束：常驻的只有 `serverosd`，并点名拒绝另外五个守护进程。里面的「§20」指旧版章节号。

*Unnumbered on purpose.* Every other section here is a design that changes; this
one is a refusal that is supposed to be hard to change, and a numbered section
sits in a document whose whole convention is that numbers move.

**Exactly one thing is resident, and it is `serverosd`.**

Here “one thing” refers to the ServerOS semantic Node Core per managed scope, not
a ban on native supervisors, CloudCLI, Tailscale, shared Providers or currently
executing Agents. Finite scheduled operations may wake an existing Parent after
a result arrives; this is continuation plumbing, not a resident business planner.

This is frozen because the pressure against it is steady and each step is
individually reasonable. Discovery feels like it deserves a supervisor; memory
feels like it deserves a writer; coordination feels like it deserves a
scheduler. Each of those is a *semantic service* — a resolver, a schema, a
refusal rule — and none of them is a process. A service that answers in-process
becomes a question that can be unanswered the moment it is given a process, and
"the answer is unavailable" is the one output this product exists to make
impossible.

```text
serverosd          resident, supervised by systemd (§20)
serverosctl        on demand — the operator surface
serveros-*-mcp     on demand — spawned per Agent session, owned by that session

refused as resident units, by name:
  abilityd                 discovery is a resolver, not a supervisor
  memoryd                  the ledger is written by the operation that needs it
  coordinationd            bindings are a durable record, not a running thing
  resource-governanced     observation is a tick, and admission is a policy read
  feishud                  Feishu is a Provider, reached when a command needs it
```

Each refused name is the obvious way to make one of the four semantic services
into a process. Refusing them is not a claim that a second resident unit could
never be justified — it is that the justification would have to survive this
paragraph, in a diff, with a name.

**What may be a process is a Provider or a Managed Runtime, and both live
outside the core.** BotMux, the Codex App Server, Docker, Feishu and OpenViking
are reached through a module under `mcp/` or `abilities/`; the core's loader
closure may not reach any of them, and `serverosd` starts with every one of
them absent. `tests/test_core_freeze.py` asserts the surface is exactly the
three rows above, that no refused name has appeared on it, and that none of
these outer systems is in the core's closure. `tests/test_core_ability_boundary.py`
asserts the closure itself.

**This section was deleted from the document and is restored here on
2026-09-22.** The gate that holds it — `test_the_refusal_is_written_down`, whose
own failure message is "the freeze was deleted rather than changed" — stayed in
the tree and stayed red while the text was gone. Nothing in the refusal had
become false: `test_no_such_daemon_exists` never stopped passing, and no refused
name has appeared since. What went missing was the paragraph a person reads
before proposing the sixth one, which is the only thing the section was ever
for. The subsection above is where the refusal lives; this one is why it is
still refused.

## 7. 正在改什么

依据：[需求清单](docs/requirements/fleet-requirements.md)、[盘点报告](docs/audit/2026-11-13-inventory.md)。

| 方向 | 内容 | 状态 |
|---|---|---|
| 止血 | 监控的监控（O-3）、认证失效提醒（D-5）、Linux 密钥同步加超时（S-3） | 待做 |
| 减法 | D1 删无人引用的代码（已做，#635）；D9 文档瘦身（本文件）；D2–D6 等调用频次数据 | 进行中 |
| 结束双轨 | 保留 Fleet V1，停掉 Fleet Next 的 supervisor 与 tailscaled | 待数据确认 |
| 一个部署来源 | 每台设备只从一个发布版本运行 | 待做 |
| 设备侧一个控制入口 | 节点连接器与 CloudCLI 网关只留一个对外入口 | 待设计 |
| 能力分发 | 核心包（1 个 MCP + 4 个 Skill）+ 角色包；版本 = 内容指纹；合并后 CI 一键发布 | 待做 |
| 采集与对齐 | 飞书白名单群与全部妙记自动归档；认识卡每日重算；GPT 每日对齐包 | 待做 |
| 性能 | 设备主动推送事件（Rust），轮询只作兜底 | 待做 |

## 8. 文档地图

- [docs/README.md](docs/README.md)：全部文档的入口与分类。
- [docs/requirements/](docs/requirements/README.md)：需求与已定的决定。
- [docs/audit/](docs/audit/2026-11-13-inventory.md)：盘点（可用 `scripts/reachability.py` 复算）。
- [docs/archive/](docs/archive/)：历史版本，保留当时的判断，不代表现状。
