# ServerOS

**给 Claude Code 和 Codex 的个人 Agent 工作系统。**

它不替代模型，也不在模型之上再套一个调度器。它负责让模型在需要的时候，拿到正确的环境、上下文、记忆、能力和权限边界。

[English](README.en.md) · [架构导读](ARCHITECTURE.zh.md) · [完整架构（英文规范）](ARCHITECTURE.md) · [文档地图](docs/README.md)

---

## 它解决什么问题

日常用 Agent，真正耗神的往往不是写代码，而是另外四件事：

- **资料散。** 飞书、GitHub、网页、会议记录各在一处，每次都要重新找、重新讲。
- **会话断。** 新开一个 Session，之前的结论、约束、做到哪一步都要重新交代。
- **环境重。** 想让 Agent 做事，得先替它准备目录、依赖、测试环境和权限。
- **设备隔。** 多台机器、多个项目之间，工作接不上。

ServerOS 把这四件事做成常驻的个人基础设施：节点上的 Core 负责观察和管理这台机器，Skill 与 MCP 负责把能力和上下文交给模型，Work Engine 记录需求、执行和结果。

它**不是**新的聊天客户端、新的模型，也不是第二个任务调度器。模型仍然决定怎么做；ServerOS 只保证它知道该知道的事、能用到该用的东西、做完有据可查。

## 现在处于什么状态

| 部分 | 状态 | 说明 |
|---|---|---|
| 东京节点日常运行 | 使用中 | Core、插件、MCP、Work Engine 在本机常驻 |
| Fleet MCP（跨设备会话） | 候选 | 44 个工具（`fleet-mcp-7`）；东京与 Dell 已接入，Windows 待原生验收；V1 尚未发布 |
| 飞书信息面 | 部分可用 | 采集、Follow、Owned 文档维护按能力逐项接入 |
| 监控与状态卡 | 部分可用 | 资源与运行观察可用；告警与卡片仍在收敛 |
| Fleet V2（自治组织、身份委派） | 仅设计 | 没有对应的服务或版本，不能当成现有能力 |

状态按三层判断：**代码存在 ≠ 已安装 ≠ 真实验收通过**。判据与证据见[文档地图](docs/README.md)。

## 5 分钟上手

下面命令在一个全新 clone、独立 venv 里跑过（Linux，Python 3.12）。ServerOS 是面向一台已授权设备的控制平面，仓库级测试会读取这台设备的 profile 与 records；这里跑的是零配置的核心子集。

```bash
git clone https://github.com/ProfessorZhi/serveros.git
cd serveros

python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install pytest pytest-xdist pytest-timeout pyyaml cryptography

export TMPDIR=/tmp/serveros-test-tmp
mkdir -p "$TMPDIR"
CUDA_VISIBLE_DEVICES="" nice -n 10 python -m pytest -q -n 8 --timeout=300 \
  tests/test_actor_context.py tests/test_agent_control_router.py \
  tests/test_agent_control_store.py tests/test_agent_mcp.py \
  tests/test_agent_sessions.py tests/test_approval.py \
  tests/test_business_calendar.py tests/test_card_delivery.py \
  tests/test_bootstrap_skill_scope.py tests/test_ability_resolver.py \
  tests/test_ability_projection.py
```

实测：`638 passed, 185 subtests passed in 16.07s`。`TMPDIR` 必须短，有些测试会建很深的 socket/work 路径。

CLI 开发不需要启动任何服务，两个入口是：

```bash
bin/serverosctl --help
python -m workengine --help
```

仓库级套件（`python -m pytest tests -q -n 8 --timeout=300`）还需要真实设备 profile、records 树和同级的 `ability-management` 检出。在本机补齐这些根后实测 `7561 passed, 79 failed, 71 skipped, 4532 subtests passed`；剩余失败绑定在实时宿主上，不是干净机器的基线。

## 一张图

```text
你 / CloudCLI / 飞书
        │
        ▼
Claude Code / Codex                     ← 主脑：理解、研究、设计、实现
        │
        ▼
ServerOS Plugin                         ← 只装载当前相关的 context / Skills / MCP
   ├─ serveros-node-mcp ─────────┐
   └─ serveros-agent-mcp ────────┤
                                 ▼
                              serverosd
                     唯一常驻 Node Core
              节点事实 / 准入 / 租约 / 效果协调 / 自检
                                 │
        ┌────────────────────────┼────────────────────────┐
        ▼                        ▼                        ▼
   语义与工作权威            外部 Provider            能力注册与分发
   Claims / Requirement     飞书 / GitHub            Skills / MCPs
   Work Engine / Control    OpenViking / Quay        Plugins / Providers
```

读完这张图只需要记住三点：

- **模型是主脑。** 研究、实验、拆分、使用工具和迭代由 Claude Code / Codex 决定。
- **Core 是常驻层。** `serverosd` 观察真实资源、做准入和租约、协调外部效果，不做 LLM 规划，也不重复实现 systemd。
- **Plugin 是入口。** 两个 MCP 只是适配器，不保存第二份 ServerOS 状态。

## 五条循环

ServerOS 的自动化由五条相互连接、可独立降级的循环组成。它们不是必须照做的流程，某一条 Provider 不可用只影响依赖它的部分：

| 循环 | 从 | 到 |
|---|---|---|
| 信息 | IM / 文档 / 会议 / 网页 / 文件 | 采集 → 结构化理解 → 记忆 → 需求候选 |
| 工作 | 正式需求 | Work Item → 0–2 个 Focus → 执行 → 验收 → Outcome |
| 能力 | 真实能力缺口 | 查原生/已装/上游 → 最薄实现 → 评测安装 → 真实消费者 |
| 知识 | Source revision | 只读变化 → 更新理解 → 维护文档 → 回读 |
| 运行 | 观察 | 健康/影响/归属 → 有限恢复 → 状态卡/Incident → 必要时找人 |

记忆、权限和证据横穿五条循环。细节见[架构导读](ARCHITECTURE.zh.md)。

## 文档地图

| 我想…… | 从这里开始 |
|---|---|
| 先知道它是什么、能做什么 | 本页 + [架构导读](ARCHITECTURE.zh.md) |
| 接手或部署 | [文档地图 · 运行](docs/README.md#运行与部署) |
| 改代码、跑测试 | [文档地图 · 开发](docs/README.md#开发与契约) |
| 看设计与边界 | [ARCHITECTURE.md](ARCHITECTURE.md)（英文规范） |
| 看历史计划与证据 | [docs/plans](docs/plans)、[docs/proposals](docs/proposals)、[docs/reviews](docs/reviews) |

完整索引在 [docs/README.md](docs/README.md)。

## 术语

| 词 | 在这里指什么 |
|---|---|
| Agent | Claude Code、Codex 这类原生智能体。ServerOS 不实现自己的 Agent。 |
| Skill | 给 Agent 的一段可复用工作方法，按需加载。 |
| MCP | Model Context Protocol；这里指把工具暴露给 Agent 的适配层。 |
| Ability | 产品级职责（如“会话管理”），由 Skill / MCP / Plugin 等具体实现。 |
| Node Core | `serverosd`，每台受管设备上的唯一常驻核心。 |
| Fleet | 跨设备连接层：发现设备、路由会话、约束授权。 |
| Item | 一项正式工作，有稳定身份、证据和验收条件。 |
| Run | 一次独立、可恢复、留痕的执行。 |
| Quay | 执行加速器：把有价值的实现、实验和独立验证委派出去。 |
| OpenViking | 长期记忆 Provider，负责跨会话、跨项目的相关性召回。 |

## 仓库

ServerOS 的代码只在两个仓库：**serveros**（完整产品，含 `workengine/`）和 **ability-management**（能力注册与 Skill 分发）。**hwz-records** 是节点读写的私有数据仓库，不是第二个代码仓库。

| 仓库 | 职责 |
|---|---|
| serveros | 产品架构、插件组合、入口、确定性契约、节点运行时、Work Engine |
| ability-management | 能力身份、来源、revision、可复用 Skill 与分发 |
| hwz-records | 理解、工作意图、设计、拓扑、策略与观察（私有数据） |
| marketops | MarketOps 应用/MCP、研究管线与业务真值 |
| marketops-session | MarketOps 会话反推的设计分析与工具 |
| llmlab | LLM Lab 网关、评测与部署模板 |

目标结构为 3 个 Public（`serveros`、`marketops`、`llmlab`）与 3 个 Private（`ability-management`、`marketops-session`、`hwz-records`）。MarketOps 与 Support Bot 是并行业务项目，ServerOS 不接管它们的路线。

## 设计完成不等于已经上线

读这份 README 时，请把三件事分开看：

- **代码存在**：仓库里有实现和测试。
- **已经安装**：目标设备上真的在运行。
- **真实验收**：在真实设备、真实客户端上通过验收场景。

本页、文件数量、工具列表和一次成功的文档提交都不能替代后两项。
