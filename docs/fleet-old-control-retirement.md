# Fleet 旧控制面消费者清单与 D5/D6 阻塞（#429）

> 结论：**D6 的“删除”步骤在本轮不成立**。本文与
> `fleet/old-control-retirement.yaml`（机器可读）记录为什么，以及一个可合并的
> guard（`tests/test_fleet_old_control_retirement.py`）如何阻止它悄悄漂移。
> 本 PR **不删除任何旧控制文件**，也不修改 `layers.yaml`（#414 所有）。

## 1. 方法与范围

- 基线：`origin/main` `e69fc033`（2026-10-28 读取）。
- 盘点对象（D6 列出的旧控制入口）：`mcp/agent_mcp.py`、`fleet/controller.py`、
  `fleet/agent_control_router.py`、`fleet/node.py`、`fleet/interfaces.py`、
  `fleet/remote_desktop_commander.py`。
- **不在**本轮范围：`fleet/registry.yaml`、`fleet/profiles.yaml`（L0 身份权威，
  不改不迁）、`fleet/transport.py`/`fleet/view.py`（L0）、
  `fleet/fleet-mcp-contract.yaml` 与 `fleet_mcp/**`（#407）、`layers.yaml`（#414）。
- “消费者”的判定：**真实装载/导入**（`_load*` 该文件、`import fleet.<module>`、
  bin 入口的 `import agent_mcp`）以及插件 bundle 声明；契约/文档里仅提及路径的
  属于 provenance，不计入 live consumer（但仍记录在清单的 `docs` 字段）。
- 只读生效指向：本机 Dell/fjd 仅观察到
  `fleet_mcp.node.connector`（L2 node connector）与一条隧道进程在运行；
  未观察到 `agent_mcp`/`node_mcp` 进程，也无相关 user systemd unit。
  沙箱 HOME 下没有可读的 Claude 插件安装记录，因此**不声称**本机已安装指向。

## 2. 消费者矩阵

完整逐文件列表见 `fleet/old-control-retirement.yaml#entries[].consumers`；
下表是同一事实的摘要。**实时性由 guard 从树中重新推导**，不靠本表维护。

| 旧入口 | 层 | 角色 | live code consumers（摘要） | 插件/生产链 | Fleet MCP 替代 |
| --- | --- | --- | --- | --- | --- |
| `mcp/agent_mcp.py` | L2 | 设备本地 Agent MCP（`agent.*`/`device.*`），经 serverosd 准入 | `mcp/bin/serveros-agent-mcp`、`scripts/live_closure.py`、4 个 tests | `plugin/bundle.yaml` 的 `serveros-agent` | **无** |
| `fleet/agent_control_router.py` | L2 | 本地控制路由：解析/拒绝/上下文绑定/loopback | `mcp/agent_mcp.py`、3 个 QA probes、3 个 tests | 经 `agent_mcp` 进入插件链 | **无** |
| `fleet/node.py` | L2 | 本机控制根绑定（device/targets/controller 声明） | `fleet/interfaces.py`、1 test | 经 `view`→`interfaces`→`agent_mcp` | **无** |
| `fleet/interfaces.py` | L2 | 9 个只读 node/fleet 接口 | `fleet/view.py`、1 test | 同上 | **无** |
| `fleet/controller.py` | L2 | controller 边界：只给描述/拒绝，不给客户端 | `fleet/node.py`、`remote_desktop_commander.py`、1 test | 经 node 链 | **无** |
| `fleet/remote_desktop_commander.py` | L2 | controller transport 适配器（拒绝而非空列表） | 2 个 tests（生产链当前未装载） | — | **无** |

L0 依赖：`fleet/view.py`（layers.yaml 登记为 L0）被 `mcp/agent_mcp.py` 装载，
不属于 D6 退役集，不能被"控制面清理"顺带删除。

跨层依赖（#407 所有）：`fleet_mcp/registry.py` 仍 `from fleet.transport import`。
迁移/退役 `fleet/transport.py` 前必须有 #407 的明确 handoff；guard 会复查它仍存在，
防止清单过期。

## 3. old → new 能力等价表

`fleet_mcp/` 的 `tool_names()` 是 11 个一等 `fleet_*` 工具（`fleet_list_devices`、`fleet_list_sessions`、
`fleet_get_session`、`fleet_read_session`、`fleet_send_to_session`、`fleet_get_run`、
`fleet_status`、`fleet_create_session`、`fleet_interrupt_session`、
`fleet_create_project`、`fleet_delete_project`）；连同 CloudCLI native/mirror 定义，
`SERVED_TOOLS` 共 44 个，全部 `fleet_` 前缀。

| 旧能力 | 最接近的新能力 | 等价？ | 原因 |
| --- | --- | --- | --- |
| `agent.session.send`（本地） | `fleet_send_to_session`（hub） | **否** | 前者是 serverosd 准入 + trusted context/Work 绑定；后者走 Tokyo hub/connector/OAuth，主体与传输都不同 |
| `agent.session.list/result`、`agent.console` | `fleet_read_session`、`fleet_get_run` | **否** | 本地 Session/Work store 与远端 CloudCLI 回读，互不证明 |
| `device.list/inspect`（agent）、`device.show`（node） | `fleet_list_devices` | **否** | 本机绑定 + L0 registry vs hub 的远端 provider 枚举 |
| `router.route/handoff` | `fleet_create_session`/`fleet_interrupt_session` | **否** | 本地解析/审批/幂等 vs 远端会话控制 |
| `controller.py`/`remote_desktop_commander.py` | CloudCLI native tools | **否** | desktop-commander 控制器面 vs CloudCLI 原生工具，不同上游 |

因此**没有一个旧入口满足"完善替代 + 测试覆盖"的退役条件**。

## 4. 为什么不能 `git rm`

1. `plugin/bundle.yaml` 声明 `serveros-agent` → `bin/serveros-agent-mcp` →
   `import agent_mcp`；删除后当前生产插件组合起不来（D6 验收项）。
2. `mcp/agent_mcp.py` 在模块作用域装载 `fleet/view.py` 与
   `fleet/agent_control_router.py`；删除任一即 import 断链。
3. 路由的拒绝/授权语义（未知动作=格式错误、server-side=ACTION_UNSUPPORTED 且
   明示 `handoff()`、无 trusted context 的变更=INVALID_CONTEXT）在#237/#245 的
   安全论证里被引用；删除会丢掉拒绝路径。
4. `fleet_mcp/registry.py → fleet/transport.py` 是 #407 的在用依赖。
5. 删除 `remote_desktop_commander.py` 需要同时改
   `tests/test_fleet_controller.py`、`tests/test_fleet_transport.py`，超出本工作包
   声明的测试 ownership。

## 5. 阻塞项与解除条件（精确 handoff）

| id | 阻塞 | 解除条件 |
| --- | --- | --- |
| `D6_DELETE_AGENT_MCP` | 无设备本地 Fleet MCP 面 | 一个本地 `agent.*` 服务（或 connector 显式委派），保持 `agent.action.admit` 准入证明；bundle `serveros-agent` 迁移；未授权 send 负例；`tests/test_agent_mcp.py` 级覆盖 |
| `D6_DELETE_ROUTER` | 无本地解析/拒绝替代 | 由未来本地控制面接管，保留闭集 reason 词表、`handoff()` 封闭路由、上下文绑定；或 #407 明确 handoff |
| `D6_DELETE_NODE_INTERFACES` | hub 不能回答本机绑定 | L0 身份权威提供等价绑定读取 + 接口名/拒绝词表不变 + 消费者测试随迁 |
| `D6_DELETE_CONTROLLER_ADAPTERS` | 不同上游，无替代 | 受治理的 controller 集成且保持 `ControllerNotExposed`；或 Owner 决定移出 V1；测试随适配器一起移动 |
| `D5_REMOVE_FLEET_SPLIT` | 依赖 D6；`layers.yaml` 属 #414 | D6 真正完成、文件物理迁出后，在同一合并树更新 `layers.yaml`（本 PR 不碰 #414） |

## 6. Guard 做什么

`tests/test_fleet_old_control_retirement.py`（32 项）：

- 清单覆盖 D6 六项且不多不少；身份权威不可列入退役；L0 `view.py` 不被卷入。
- **消费者漂移**：从树中重新推导 live consumer，与清单逐项比较；新增导入即红，
  清单过期也红（双向）。
- **退役契约**：`RETIRED` 必须指向真实 `fleet_mcp` 工具、存在证据测试、且无 live
  consumer；`RETAIN_*` 不得声称替代且必须写清 `why_not_retired`/`unblock`。另有
  正控证明这些规则确实会红。
- **拒绝/授权负例**：未知动作 malformed；server-side handoff 明示迁移；无 trusted
  context 的 send 被拒；每个 control 工具的 schema 都要求 `context_ref`；
  controller 适配器在无声明时抛 `ControllerNotExposed`；hub 不导入本地控制面。
- **入口盘点**：tracked 的 systemd unit / deploy 脚本不得出现未盘点的旧控制引用。
- **插件无第二控制面**：两个 server 名唯一、agent/node 工具名不重叠、每个 control
  action 只出现一次、插件不声明任何 `fleet_*` 工具；并确认 hub 的 11 个工具确实存在。
- **层级守卫**：文件还在时 `fleet` 必须仍是 `split`，且从 L2 描述中**解析出**这些
  文件名（不是子串匹配）；提前去掉 split（D5）会红。
- **生产闭包**：`tests/loader_graph.py#production_closure()` 对 5/6 个路径是硬依赖
  （`remote_desktop_commander.py` 不在闭包），清单单独记录并由 guard 精确比对。
- **匹配器边界（已声明）**：只跟随静态 `*load*(`/`*blob*(`/`import fleet.x`；
  变量拼接、配置读取、单参数 `load("path")` 不跟随，与 `loader_graph` 的限制一致。

## 7. 证据分级

| 级别 | 状态 |
| --- | --- |
| CODE | 本清单 + manifest + guard |
| UNIT | `python3 -m unittest tests.test_fleet_old_control_retirement`（32/32） |
| HOSTED_CI | 本 Draft PR 工作流（待跑；未宣称绿） |
| REAL_DEVICE / PRODUCTION | **NOT_RUN**：未安装、未改服务、未发送、未改 #407/#414 |

本轮**没有**发生：任何旧文件删除、`layers.yaml` 修改、身份文件修改、
`fleet_mcp/**`/`runtime/**` 修改、设备服务/ACL/OAuth/token 变更。
