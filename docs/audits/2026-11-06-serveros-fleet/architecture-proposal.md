# ServerOS Fleet 下一版架构建议

本文件是三设备现场审计后的设计输入，不是已批准实现规范。所有方案都需要 owner 决策；本次没有修改任何设备或仓库代码。

## 1. 产品四支柱与 L0-L4 映射

### 1.1 四个产品支柱

| 产品支柱 | 主要责任 |
|---|---|
| Local ServerOS Plugin + Ability Family | 设备侧 Agent 能力、Skills、指令文件、安装与 readback |
| CloudCLI + Fleet MCP | 跨设备 Runtime / Session 发现、读取、发送、创建、Run receipt、OAuth/grant |
| serverosd + Monitoring | OS 资源/服务观测、有界 reconcile、自愈策略；Agent Run lifecycle 由独立 monitor 消费 |
| Commander Plugin + Operator Skills | 任务理解、能力匹配、设备选择、异步派发、证据验收、汇报 |

### 1.2 L0-L4 架构层

| 层 | 权威与职责 |
|---|---|
| L0 Authority | Device identity、principal identity、policy、grant、single-writer、canonical catalog |
| L1 Device Base | OS runtime、serverosd、Core-Lite、device collector、local service health |
| L2 Control Plane | Node Connector、Tokyo Fleet MCP、CloudCLI control adapter、bounded discovery/dispatch |
| L3 Ability | Device Ability Profile、Plugin、Skills、instructions、runtime bindings |
| L4 Commander | Operator interface、task planning、capability routing、async orchestration、acceptance |

产品支柱与架构层是两个正交视角。例如，Local Ability 支柱主要落在 L3，但它依赖 L0 catalog、L1 host facts 和 L2 readback；Commander 是 L4 产品，但只能通过 L0 授权与 L2 bounded tools 操作设备。

## 2. 端到端数据流与单一权威

建议数据流：

1. L1 collectors 采集设备级事实与 principal-scoped 事实。
2. Node Connector 只暴露固定、类型化、受授权的 read / control routes。
3. Tokyo Fleet MCP 汇总证据，生成 Device Snapshot / Runtime Snapshot / Capability Snapshot。
4. Ability Fabric 根据 Ability × DeviceCapabilities × Profile × Binding × AgentRuntimeCapabilities × RuntimeHealth × Policy × TaskContext 计算 Effective Ability。
5. Commander 读取 Snapshot，选择设备、principal、runtime 与已有 Session。
6. Dispatch 返回 ACK / receipt；ACK 只证明受理。
7. Agent Monitor 读取 Run lifecycle 和实际 terminal output，确认终态。
8. Inbox dedupe 后由唯一 Notification Sender 发送；LLM wakeup 由独立 continuation scheduler 执行。

禁止每层各自维护一套“设备在线”“Agent 空闲”“任务完成”事实。

## 3. Fleet Device Intelligence

### 3.1 事实分层

**Device-level stable facts**
- Device ID / hardware identity
- OS family / architecture
- CPU / RAM / GPU capacity
- Filesystem / mount topology
- Network interface 与静态边界声明
- Owner / trust domain

**Principal-scoped facts**
- OS accounts（只采集允许的账号元数据）
- HOME binding
- Claude / Codex / CloudCLI runtime
- Plugin / Skills / MCP visibility
- per-user services / timers
- model / provider / gateway config metadata

**Runtime-scoped facts**
- Runtime version
- effective model/provider/gateway（只暴露非敏感 host/port/path 与 secret anchor presence）
- Session / Run / current activity
- permission mode / grant status
- Agent process health

**Observed capability facts**
- Docker / Compose availability
- GPU availability and current contention
- network reachability from a specified vantage
- service health
- tool handler binding and per-verb invocation evidence
- installed / ready / granted / verified Ability

**Work Assignment**
- 当前任务、项目、设备、principal、runtime、资源 reservation
- 全部动态计算或显式分配
- 不作为永久设备角色写进 Registry

### 3.2 Fact Envelope

每条事实建议统一携带：

- fact_id
- subject_ref（device / principal / runtime / path / capability）
- fact_type
- value
- status
- source
- observed_at
- expires_at / freshness_policy
- trust_level
- scope
- evidence_ref
- collector_version

状态必须区分 DECLARED、OBSERVED、TESTED、VERIFIED、STALE、UNKNOWN。UNKNOWN 不得自动变成 UNAVAILABLE；声明也不能自动升级成 VERIFIED。

### 3.3 Dynamic Device Selection

任务调度先做硬约束过滤：
1. Device / principal identity matches.
2. Required network zone reachable from the selected vantage.
3. Required Runtime and Ability are installed and verified.
4. Required permission grant exists.
5. Resource capacity and current contention acceptable.
6. No conflicting active Run / reservation.
7. Existing idle Session is reusable.

再进行软评分：
- latency / data locality
- current CPU / RAM / GPU pressure
- queue depth
- reliability / recent failure rate
- workspace canonicality
- task affinity

不能因为设备有某个历史 role，就跳过实时证据。

## 4. Agent Monitor 与 serverosd 的边界

### 4.1 建议责任矩阵

| 子系统 | 唯一责任 |
|---|---|
| Core-Lite / Node Collector | OS / hardware / resource / local service observations |
| serverosd | 设备级采集、策略门控、有界 reconcile；不判定 LLM 任务语义完成 |
| CloudCLI Adapter | Session / Run discovery、dispatch receipt、runtime lifecycle metadata |
| Agent Monitor | 关联 Run，检测 stale / terminal candidate，读取可用 terminal output |
| Run Verifier | 基于实际输出确认 completed / failed / blocked；证据不足保持 UNKNOWN |
| Inbox / Event Store | 单写者去重、episode 状态与证据引用 |
| Notification Sender | Feishu / other IM delivery；不重新判定任务状态 |
| Continuation Wake | 读取明确的 continuation event，按授权唤醒 LLM；不直接修改 Run 事实 |
| Commander | 规划、分派、消费结果和验收；不成为设备 telemetry authority |

### 4.2 避免双重权威

- serverosd 不应与 Agent Monitor 同时写 Run terminal state。
- Agent Monitor 不应直接控制系统服务或任意执行 shell。
- Notification Sender 只发送已去重事件。
- LLM wakeup 不能根据 ACK 推断任务完成。
- 任何无法读取 terminal output 的 Run 都应保留 UNKNOWN / DELIVERY_UNVERIFIED。
- 资源告警与 Agent Run 告警应使用不同 event family。

### 4.3 现场状态的正确解释

- Tokyo：Fleet Watch 与相关 timer 有 active 证据；Agent Monitor 全链仍需验证 heartbeat、Run discovery、terminal confirmation、Inbox、notification 和 wakeup。
- A6000：serverosd、Core-Lite publish、Node Connector 有运行证据；Core-Lite services block 为空，connector block 尚未实现；没有独立 Agent Monitor unit 证据。
- Windows：Core-Lite 已安装但 live=false；Connector 与 CloudCLI 相关进程/任务存在；没有独立 Agent Monitor 安装证据。

## 5. MCP / API 最小实现建议

先提供只读工具，后增加控制面：

1. fleet_device_snapshot(device_ref, refresh_scope?)
2. fleet_principal_inventory(device_ref, principal_ref)
3. fleet_runtime_inventory(device_ref, principal_ref)
4. fleet_path_bindings(device_ref, principal_ref)
5. fleet_directory_inventory(device_ref, principal_ref, roots, depth, budget)
6. fleet_capability_evidence(device_ref, principal_ref, runtime_ref)
7. fleet_tool_invocation_evidence(tool_ref, target_ref)
8. fleet_agent_runs(device_ref, runtime_ref, state_filter)
9. fleet_run_evidence(run_ref)
10. fleet_assignment_plan(task_requirements)

每个工具应有 bounded IO、explicit scope、timeout/budget、evidence refs、principal scope 和 freshness metadata。目录扫描必须按预算分批，并在 CPU / memory / IO pressure 超阈值时自动停止。

## 6. 目录与能力治理测试矩阵

| 测试 | 预期 |
|---|---|
| BOUND path 不存在 | STALE_BINDING；不允许继续按路径执行 |
| path owner 与 principal 不匹配 | BLOCKED_IDENTITY |
| 实际目录在用但 Registry 未声明 | UNDECLARED_IN_USE |
| Git Worktree | 关联 common git dir；不得误报独立 clone |
| dirty worktree | 不进入自动 cleanup candidate |
| stale clean worktree | 只产生 owner review candidate |
| Ability 声明但文件缺失 | UNKNOWN / NOT_INSTALLED，按证据区分 |
| Skill 在真实 HOME 存在但 sandbox 不可见 | 对该 runtime 标记不可见，不全局判 installed |
| tool schema 存在但 handler 未绑定 | HANDLER_BINDING_UNKNOWN |
| safe verb 实际调用成功 | 对该 verb 增加 invocation evidence |
| Core-Lite live=false | 不能作为实时健康事实 |
| Agent ACK 但没有终态输出 | ACCEPTED / DELIVERY_UNVERIFIED，不是 COMPLETED |
| Monitor watcher active 但无 heartbeat | DEPLOYED / HEALTH_UNKNOWN |
| 高负载扫描 | 自动预算终止并返回 PARTIAL / SKIPPED |

## 7. 最小实现顺序

### Phase 0 — 事实源与安全边界
- 定义 Device / Principal / Runtime / Path Role 的唯一 schema。
- Registry 保留稳定设备身份与硬件事实。
- Work Assignment 从 Registry 中解耦。
- Path Binding 改为 per-device + per-principal。
- 明确 single-writer 和 evidence authority。

### Phase 1 — Read-only Device Intelligence
- 增加设备与 principal snapshot。
- 加入事实时间戳、TTL、source、trust、evidence_ref。
- 采集 OS、hardware、resource、network declaration、runtime、services。
- Core-Lite claimed/live/accepted 状态必须真实表达。

### Phase 2 — Directory Intelligence
- 统一 Path Role vocabulary。
- 识别 clone / worktree / orphan / dirty / stale。
- 增加预算、熔断、分批与缓存。
- 只生成 cleanup candidate，不自动移动或删除。

### Phase 3 — Ability / Tool Evidence
- 分开 installed、visible、ready、granted、invocation-verified。
- Ability readback 统一 source-of-truth。
- MCP inherited verbs 做 per-verb handler binding 与安全调用验证。

### Phase 4 — Agent Lifecycle
- 让 CloudCLI Run discovery、terminal verification、Inbox、Notification、Continuation Wake 形成可审计闭环。
- 分开 resource monitor 与 Agent Monitor。
- 增加幂等、stale detection、delivery receipt 和恢复策略。

### Phase 5 — Commander Dynamic Routing
- 根据 task requirements、Effective Ability、网络、资源和权限选择设备。
- 优先复用合适的空闲 Session。
- 派发后异步跟踪，只有读到实际结果才验收。

## 8. 安全不变量

- 不跨 OS principal 读取 HOME。
- 不读取或输出 secret values。
- 不提供 arbitrary shell / arbitrary path / arbitrary exec。
- UNKNOWN fail-closed。
- 权限四门不变。
- 单写者不变。
- ACK 不等于完成。
- 所有自动化清理必须单独 owner approval。
- 压力扫描必须有预算与熔断。
