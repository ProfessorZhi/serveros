# ServerOS Fleet 的四层，以及仓库目录怎么对应它们

> **这篇回答什么：** Fleet 由哪四部分组成，每部分管什么、不管什么、跑在哪、怎么发布、可以依赖谁；仓库里每个顶层目录属于哪一部分；现在哪里耦合着，按什么顺序解开。
>
> **状态：** 设计与登记，2026-10-26。目录登记表 [layers.yaml](layers.yaml) 与守卫测试 `tests/test_repository_layers.py` 已生效；§5 的拆分与 §6 的待定事项还没有执行。本文不移动任何文件、不改任何行为。
>
> **与其他文档的关系：** [ARCHITECTURE.md](../../ARCHITECTURE.md) 仍是产品总入口；[FLEET-V1-TOPOLOGY-OPERATIONS.md](FLEET-V1-TOPOLOGY-OPERATIONS.md) 管三台设备的拓扑与验收；本文只管「代码放在哪一层、层与层之间怎么依赖」。

## 1. 为什么按这四层分

按**代码最终跑在哪里、由谁发布**来分，而不是按功能名词分。同一层的东西一起部署、一起回滚；不同层各自发版，一层坏了不拖垮另一层。目标只有三条：

1. **各层独立发布。** 改一个 Skill 不需要重启 serverosd；升级东京的 Fleet MCP 不需要动任何设备。
2. **依赖单向。** 上层通过接口用下层，下层不知道上层存在（§4）。
3. **权威只有一份。** 设备身份、工作状态、能力目录各有一个主人，四层只读它、不复制它（L0）。

## 2. 总图

```text
                 ┌──────────────────────────────────────────────┐
  L4 指挥侧      │ Claude Code / Codex / ChatGPT 指挥端插件     │  plugins/  .claude-plugin/
                 │ + Operator Skills（只通过 MCP 说话）          │
                 └───────────────────────┬──────────────────────┘
                                         │ HTTPS + OAuth（MCP）
                 ┌───────────────────────▼──────────────────────┐
  L2 枢纽        │ 东京 Fleet MCP：发现、路由、授权、幂等、证据   │  fleet_mcp/（除 node/）
                 └───────────────────────┬──────────────────────┘
                                         │ 签名的节点协议
        ┌────────────────────────────────┼────────────────────────────────┐
        │ 每台设备（Tokyo / Dell / Windows / …，每个授权 OS 用户一份）        │
        │  L2 接入   Node Connector ──→ CloudCLI ──→ CC / Codex Session      │  fleet_mcp/node/
        │  L1 底座   serverosd（一个进程，C++ 为主）：观测、受控自愈、本地 lease │  native/ → serverosd，runtime/(节点部分)
        │  L3 能力   该设备装的 ability / skill / plugin 组合 + 安装情况观测  │  plugin/ skills/ abilities/
        └─────────────────────────────────────────────────────────────────┘
  L0 权威：设备身份与拓扑 fleet/registry.yaml·profiles.yaml；工作状态 workengine/；能力目录 ability-management 仓库
```

另外两类不属于 Fleet 四层，但在同一个仓库里：

- **product**：ServerOS 的信息循环（飞书接入、理解、记忆、卡片、日报）——`intake/ context/ memory/ space/ relations/ documents/ workbench/ research-exchange/ deploy/` 以及 `runtime/` 里的卡片与报告部分。
- **shared**：构建、发布、测试、文档——`scripts/ integrations/ integration/ releases/ tests/ docs/ .github/ bin/`。

## 3. 每一层

### L0 权威

| | |
|---|---|
| 管 | 设备身份、角色与拓扑（`fleet/registry.yaml`、`fleet/profiles.yaml`、`fleet/transport.py`）；工作项、Run、lease、effect（`workengine/`，以及暂时在 `runtime/agent_control_store.py` 等处的代理控制记录）；能力目录（ability-management 仓库，不在本仓库） |
| 不管 | 任何执行：不起进程、不发消息、不重启服务 |
| 依赖 | 不依赖 L1–L4 |
| 规则 | 四层只读它；需要缓存时带版本与新鲜度，绝不复制成第二份可写记录 |

### L1 设备运行底座

| | |
|---|---|
| 管 | 每台设备、每个授权 OS 用户**一个**常驻进程 **serverosd**：CPU/内存/磁盘/GPU/网络/服务的观测，本机受控自愈（预算、退避、熔断、忙碌 Agent 不动），本地 lease，自愈收据，以及给 L2 的本机事实 |
| 名字与实现 | 只有一个名字：serverosd。不再另称 Native Core / Core-Lite / supervisor，它们是 serverosd 的内部模块。实现以 C++ 为主（`native/node-core`，约 2.9 万行，含 Windows 部分）；Python `runtime/daemon.py`（约 5400 行）在迁移期继续运行，按 §6.1 把监控与自愈逐项交给 C++ 后退役 |
| 目录 | `native/`（C++ 主体，目标改名 `serverosd/`）；`runtime/` 的节点部分（daemon、health、probes、desired、inventory、resource_*、anomaly、attribution、network_*、native_core_facts…，迁移期）；入口 `bin/serverosd` |
| 跑在 | 每台设备 |
| 产物 | node-runtime 版本目录 + systemd user unit / Windows 计划任务 |
| 依赖 | L0（读身份与策略） |
| 不管 | LLM 决策、任务分解、跨设备路由 |

### L2 接入与枢纽

一层，两个部署目标：

| | 设备接入（L2-node） | 东京枢纽（L2-hub） |
|---|---|---|
| 管 | Node Connector：把本机 CloudCLI 的 Session/Run 能力以签名协议暴露出去 | Fleet MCP：设备与 Session 发现、路由、OAuth、六个 scope、幂等、证据与完整工具面 |
| 目录 | `fleet_mcp/node/` | `fleet_mcp/` 其余部分；装配入口 `bin/serveros-fleet-owned` + `runtime/fleet_mcp_owner.py` |
| 跑在 | 每台设备 | 只在东京 |
| 产物 | connector 版本（随设备发布） | `fleet-mcp-<revision>` 版本目录 + `serveros-fleet-mcp.service` |
| 依赖 | L1（读本机事实）、CloudCLI | L0（身份）；持久派发账本由装配入口从外面注入 |

`mcp/`（每会话的 node/agent MCP 适配器）和 `fleet/controller.py`、`fleet/agent_control_router.py` 是早于 `fleet_mcp/` 的旧控制面，也登记在 L2，见 §6。

### L3 设备能力家族

| | |
|---|---|
| 管 | 每台设备装哪些 ability / skill / plugin（`plugin/bundle.yaml` + `plugin/profiles/<profile>.yaml`、`skills/serveros`），以及对**实际**安装与就绪情况的观测（`abilities/`） |
| 跑在 | 每台设备，装进该设备的 Claude Code / Codex |
| 产物 | 设备侧插件，例如 `hwz-server-os-linux-work`（由 `scripts/build.py` 构建） |
| 依赖 | L0 的能力目录（ability-management，固定 revision） |
| 规则 | 能力是观测出来的，不写进 `fleet/registry.yaml`；每台设备只装一个设备侧插件 |

### L4 指挥侧

| | |
|---|---|
| 管 | 指挥端插件与 Operator Skills：怎么发现设备、复用 Session、异步派发、读证据、安全边界 |
| 源 | `integrations/fleet-v1-first-install/plugin/`（改这里） |
| 产物 | `plugins/serveros-fleet-ingress/`（`scripts/fleet_plugin.py build` 生成，禁止手改）；仓库根 `.claude-plugin/marketplace.json` |
| 跑在 | 指挥端（东京的 Claude Code、本地 Claude Code、Codex，之后 ChatGPT） |
| 依赖 | 只通过 MCP 用 L2-hub；不含可执行代码 |

## 4. 依赖方向

```text
L4 ──MCP──▶ L2-hub ──节点协议──▶ L2-node ──▶ CloudCLI
               │                    │
               ▼                    ▼
               L0  ◀──────────────  L1 ──观测──▶ L3
product ──▶ L0（读）               shared ──▶ 任意层（构建/测试）
```

允许的方向只有图上这些。当前由测试强制的三条（`layers.yaml` 的 `rules`）：

| 规则 | 现状 |
|---|---|
| `fleet_mcp/` 不加载 `runtime/` 或 `mcp/` 的代码 | 成立。需要持久账本时由 `runtime/fleet_mcp_owner.py` 从外面注入 `FleetService` |
| `plugins/` 下没有可执行代码 | 成立 |
| `fleet/` 的身份层不加载 `fleet_mcp/` | 成立（枢纽依赖身份层，反过来不行） |

其余方向目前只写在这里，靠评审守；§5 每完成一步就把对应方向加进测试。

**V1 里程碑投递链**（观察 → 判定 → 投递）的方向与归属见 [FLEET-V1-MILESTONE-DELIVERY.md](FLEET-V1-MILESTONE-DELIVERY.md) §3：L2 观察者只产候选事实、product 只渲染与投递、L3 只写摘要且可缺席、L1 不依赖网络与 Agent。那条链**不新增依赖方向**，只是把上面这张图具体化；其中需要「签名」的三条目前均未登记。合同本身是 `SPEC_PROPOSED`，实现只在 Draft #423 / #426 / #428 上（`CODE` + 单元测试，未合并、未部署），本文不因此改变任何层的职责。

## 5. 现在哪里耦合着，按什么顺序解开

实测（2026-10-26，main `3f5ec20`）的几处耦合，按收益/风险排序：

1. **L1 与 L3 是同一份构建。** `scripts/build.py` 把同一批字节发布两次：一次作为 node-runtime（serverosd），一次作为设备侧插件。结果是改一个设备侧 Skill 要重建节点运行时，反之亦然。
   → 拆成两个产物：`node-runtime` 只含 L1，设备侧插件只含 L3 的清单与 Skill。先在 `plugin/bundle.yaml` 的 artifact kinds 上分开，再分开发布。
2. **`runtime/` 一个目录装了四种东西**（123 个文件）：L1 节点守护、L0 代理控制记录、L2 枢纽装配、product 卡片与报告（明细见 `layers.yaml`）。
   → 不一次性搬。先按 `layers.yaml` 的 `parts` 让每个新文件落到对的地方；再一组一组迁出，每组一个 PR，旧路径留一个转发模块直到引用它的测试改完。目标：
   - `runtime/` 只留 L1；
   - 代理控制记录并进 `workengine/`（L0），与 Work lease 同一个主人；
   - 卡片、报告、告警投递迁到 product 侧（例如 `reports/`）；
   - 枢纽装配迁到 `fleet_mcp/` 旁的装配包。
3. **`fleet/` 半是身份、半是旧控制面。** 身份部分是 L0，所有层都读；`controller.py`、`agent_control_router.py` 是旧控制面。
   → 旧控制面退役后，`fleet/` 只剩身份与拓扑（§6.2）。
4. **`fleet_mcp/` 里两个部署目标。** 枢纽与 Node Connector 在一个包里，各自发版时互相牵连。
   → 保持一个包，但发布物分开：connector 只打 `fleet_mcp/node/` 及其依赖；测试加一条「`fleet_mcp/node/` 不导入枢纽模块」。
5. **L4 的源与产物在两处。** 已经由生成器加测试保证一致（`tests/test_fleet_plugin_marketplace.py`），不需要再动。

**为什么不现在就搬目录：** 测试里大量写死了路径（仅插件与 Skill 相关就有十几个测试文件），一次性重排风险高、收益低。登记表加守卫先把「新东西放哪」定死，旧东西按上面的顺序一组组迁。

## 6. 待定事项

### 6.1 serverosd 收敛为一个 C++ 进程（已定，2026-10-26）

owner 决定：本地监控与自愈只有一个程序、一个名字——serverosd，以 C++ 为主。仓库里现在有两套实现：Python `runtime/daemon.py`（叫 serverosd）与 C++ `native/node-core`（叫 Native Core）。收敛顺序：

1. **一个 unit 一个写入者。** 迁移期间每个受管服务只登记一个恢复者（Python 或 C++），绝不两者同时重启同一个 unit。
2. **逐项迁移。** 按「观测 → 自愈 → 本地 lease → 对 L2 的本机事实接口」的顺序，一项一项由 C++ 接管；每接管一项，Python 侧对应代码只读不动作，验收后删除。
3. **改名。** `native/node-core` 改名为 `serverosd/`，二进制与服务名统一为 serverosd；`bin/serverosd` 指向 C++ 二进制。
4. **退役 Python 守护。** `runtime/daemon.py` 只剩转发后删除；`runtime/` 里非 L1 的部分按 §5 第 2 条迁走。

### 6.2 旧控制面退役（已定：退役，2026-10-26）

`mcp/agent_mcp.py`、`fleet/controller.py`、`fleet/agent_control_router.py` 与 `fleet_mcp/` 并存。owner 决定退役，按三步执行（工作包 D6）：

1. 列出仍在调用它们的入口；
2. 能由 `fleet_mcp/` 覆盖的调用改走 `fleet_mcp/`，覆盖不了的降为 `fleet_mcp` 的适配器；
3. 删除，并把「L2 只有一个控制面」加进测试。

### 6.3 `plugins/` 里的实验插件（已删除，2026-10-26）

`serveros-fleet-zip-lab-20261014` 是 10-14 的打包实验。owner 决定删除，已连同 `.agents/`（只列了它的 marketplace）、它的 CI workflow 与打包脚本 `scripts/build_fleet_plugin_zip.py` 一起删除。`plugins/` 现在只有指挥侧插件。

## 7. 新增代码时怎么做

1. 先问「它跑在哪、谁发布」：对应到 L0–L4、product 或 shared。
2. 放进那一层已有的目录。没有合适目录时，新建顶层目录并在 [layers.yaml](layers.yaml) 登记，否则 `tests/test_repository_layers.py` 不通过。
3. 只沿 §4 的方向依赖。需要反方向时，从外面注入，参考 `runtime/fleet_mcp_owner.py` 给 `FleetService` 注入账本的做法。
4. 新插件放 `plugins/` 前在 `layers.yaml` 的 `plugins` 下写明 `side`。
5. 跨层的薄适配器（例如 V1 里程碑播报）优先放进已登记的 `integrations/`（`shared`），不新开顶层目录：守卫只检查顶层目录，子目录不需要改 `layers.yaml`。见 [FLEET-V1-MILESTONE-DELIVERY.md](FLEET-V1-MILESTONE-DELIVERY.md) §12。
