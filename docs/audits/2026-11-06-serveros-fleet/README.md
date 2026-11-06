# ServerOS Fleet 三设备只读审计与下一版设计输入

- 采集窗口：2026-11-06 UTC
- 审计对象：Tokyo Linux、Dell A6000 Linux、FJPCOS Windows
- 代码基线：6832bf1c5b936c5238ce4c15f3f0eda3bfe2b9a6
- 范围：设备、Runtime、ServerOS、Ability、CloudCLI/Fleet MCP、监控、目录与仓库治理
- 性质：只读现场审计；不是部署验收，也不是代码变更

> **公开仓库安全说明**：本报告为公开版脱敏材料。已省略个人 OS 账号、精确用户目录、私有 IP/DNS、内部服务地址与端口、凭据锚点及敏感运行配置。原始采集没有读取或输出 Token、密码、Cookie 或密钥值。目录路径使用 Path Role / HOME 相对表示。状态结论仅适用于所列采集窗口。

## 1. Executive summary

这次审计确认：ServerOS Fleet 的下一阶段不应只是增加设备列表字段，而应建立统一的 **Device Intelligence + Directory Intelligence + Agent Lifecycle Evidence**。

最重要的发现：

1. **设备登记与实际 principal/runtime 存在错配**：A6000 的 Fleet Path Role 指向另一个 OS principal 的 HOME，而当前执行 Runtime 属于不同 principal；部分声明子目录不存在。
2. **目录与仓库身份缺少治理**：三个节点均存在多 checkout、Worktree、未声明目录或 Path Role 冲突。必须区分合法 Worktree、独立 clone、dirty worktree 和 stale worktree，不能按目录数量直接清理。
3. **声明、部署、运行和验证没有统一证据状态**：Core-Lite、Node Connector、MCP tools、Skills、Agent Monitor 等常出现“配置存在”被误读成“功能已验证”。
4. **监控产品边界需要收敛**：设备资源监控、Agent Run 发现、终态确认、Inbox 去重、通知发送和 LLM 唤醒是不同职责，应有单一权威与明确接口。
5. **工作分配不应写死在 Device Registry**：设备硬件、OS、容量和客观网络边界属于稳定事实；任务角色、项目归属和工作分配应动态计算。

## 2. 三节点关键结论

| 节点 | 关键事实 | 主要风险 |
|---|---|---|
| Tokyo Linux | 128 logical CPU；约 246 GiB RAM；Docker/Compose 可用；44 个容器；Claude Code 2.1.308、CloudCLI 1.41.0、Codex CLI 0.160.0 | 审计期间 Load Average 从约 121 升至约 245；IO PSI 很高；源码 checkout 的 CloudCLI 版本落后于运行版本；两个源码根并存 |
| Dell A6000 Linux | RTX A6000 48 GiB；10 physical / 20 logical CPU；约 31 GiB RAM；ServerOSD、Core-Lite 发布和 Node Connector 有运行证据 | 系统内存约 3.5 GiB available、Swap 约 73% 使用；GPU 已有其他计算任务；Docker 未安装；设备 Path Role principal 错配 |
| FJPCOS Windows | Windows 10 Pro build 19045；约 16 GiB RAM；Claude Code 2.1.295、Codex 0.164.0、Node 24.19.0；CloudCLI gateway 与 Fleet Connector 相关任务在运行 | 审计开始时可用内存约 685 MiB；Core-Lite 已安装但 live=false；Path Role 与 Fleet Profile 对知识目录、状态目录和工作树根存在冲突 |

详细设备报告见：
- [Device Inventory](./device-inventory.md)
- [Directory Intelligence](./directory-intelligence.md)
- [Fleet Architecture Proposal](./architecture-proposal.md)

## 3. 系统分类：四个产品支柱与 L0-L4

四个产品支柱是**产品/责任边界**：

1. Local ServerOS Plugin + Ability Family
2. CloudCLI + Fleet MCP
3. serverosd + Resource / Agent Monitoring
4. Commander Plugin + Operator Skills

L0-L4 是**架构与部署层次**：

- L0：Authority、身份、策略、授权与单写者边界
- L1：设备运行底座、serverosd、Core-Lite
- L2：Node Connector、Tokyo Fleet MCP、CloudCLI 控制面
- L3：设备侧 Ability、Plugin、Skills、Runtime binding
- L4：Commander、Operator Skills、动态调度与验收

二者不能混用。一个产品支柱可以跨越多个 L 层。

## 4. 证据状态约定

| 状态 | 含义 |
|---|---|
| DECLARED | 配置、Registry、Profile 或文档声明 |
| SOURCE_CODE | 代码中存在实现，不代表部署 |
| DEPLOYED | 目标设备存在安装产物或服务定义 |
| OBSERVED | 采集窗口内从设备/进程/文件直接读取 |
| TESTED | 对指定接口或行为执行过测试 |
| VERIFIED | 有明确、可复核的成功证据 |
| UNKNOWN | 当前证据不足，不得推断为支持或不支持 |
| STALE | 证据已过期，或与更新事实冲突 |
| SKIPPED | 因资源、安全或范围限制主动未采集 |

所有数值均是时间窗口内的快照，不应当被写成永久设备事实。

## 5. 高优先级问题

### P0
- A6000 的设备级 Path Role 指向错误 principal 的 HOME；消费者若信任该路径，可能解析到其他用户私有目录或不存在路径。
- Tokyo 审计期间资源压力急剧上升；目录全量扫描不能在高负载下无条件执行。
- Windows 的知识目录绑定在两份配置中均指向不存在的位置。
- 设备身份、principal、Runtime 必须先匹配，再允许发布该 principal 的路径和能力。

### P1
- Tokyo 的 src 与 workspace/src 并存；ServerOS 和 Ability Management 有多份 checkout。
- A6000 有 53 个注册 Worktree，其中 51 个超过 7 天未活动；另有一个 dirty orphan clone。不得自动清理。
- Windows 有 54 个 ServerOS Worktree，另有多个备份、scratch 和归档根。
- Tokyo CloudCLI 运行版本 1.41.0，而源码 checkout 声明 1.39.0。
- Agent Monitor、Core-Lite live、Node Connector peer authorization 和工具 Handler Binding 必须按证据逐项验证。

### P2
- Plugin / Skills 数量在 README、Tracker、Issue 与仓库目录之间存在 8 / 11 / 13 的漂移。
- Path Role 词表、host-profile roots 和 CLI context 投影不完全一致。
- MCP tool 声明不等于 handler 已绑定或端到端可用。
- 需要建立目录生命周期、Worktree 清理候选和 owner approval 工作流。

## 6. 推荐的下一步

先完成统一的事实模型与只读盘点接口，再建设动态调度。建议顺序：

1. Device Identity / OS Principal / Runtime 的三层身份模型。
2. Per-principal Path Role Binding 与目录事实。
3. Observed Capability / Invocation Evidence / Effective Ability。
4. Agent Run lifecycle 的发现、终态确认与去重。
5. Resource Monitor 与 Agent Monitor 分层。
6. Commander 的动态设备选择和异步执行验收。

安全不变量：
- 不跨 OS 用户读取 HOME。
- 不采集或输出 Secret 值。
- 不提供任意 shell、任意路径或任意执行入口。
- UNKNOWN fail-closed，不把 UNKNOWN 压成 UNAVAILABLE 或 AVAILABLE。
- 权限四门与单写者边界不变。
- 异步 ACK 不等于任务完成；必须读取终态和实际输出。
- 目录清理必须经过证据、owner 审批、保留期和单独删除授权。
