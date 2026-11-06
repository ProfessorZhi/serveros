# 三设备现场盘点

采集窗口：2026-11-06 UTC。所有数值均为现场快照；跨节点比较时必须考虑采集时间和负载变化。

## 1. Tokyo Linux

### 1.1 主机与资源

| 项目 | 观测 | 状态 |
|---|---|---|
| OS | Ubuntu 24.04.4 LTS | OBSERVED |
| Kernel / Arch | 6.8 系列 / x86_64 | OBSERVED |
| CPU | AMD EPYC 9K65；64 physical / 128 logical | OBSERVED |
| RAM | 约 246 GiB；采集时约 43 GiB available | OBSERVED |
| Filesystem | Root 约 504 GiB；Data volume 约 4 TiB | OBSERVED |
| Docker / Compose | Docker 29.8.0 / Compose 5.5.1 | OBSERVED |
| Containers | 采集时 44 个运行容器 | OBSERVED |
| GPU | 未发现 NVIDIA GPU 工具 | OBSERVED |

资源压力：
- Load Average 在审计期间从约 121 上升到约 245（128 logical CPU）。
- IO PSI full avg10 最高约 48%；memory PSI 也有非零压力。
- 目录大小统计仅完成部分项目根；其余因负载与超时主动标记 UNKNOWN/SKIPPED。
- 这证明全量 Git status / Worktree 扫描可能对高负载节点造成额外压力。Fleet 的目录盘点应有预算、超时、分批和熔断机制。

### 1.2 Agent / Runtime

| 项目 | 观测 | 状态 |
|---|---|---|
| Claude Code | 2.1.308 | OBSERVED |
| CloudCLI | 1.41.0 | OBSERVED |
| Codex CLI | 0.160.0 | OBSERVED |
| CloudCLI source checkout | 声明版本 1.39.0，commit 前缀 300e5fa9 | STALE |
| Claude model | sonnet | DECLARED |
| Provider | 未获得可靠只读读回 | UNKNOWN |
| Local gateway URL | 仅确认 loopback endpoint 存在；listener owner 未确认 | OBSERVED / UNKNOWN |
| Permission mode | 配置声明较宽松的默认模式 | DECLARED；实际 enforcement UNKNOWN |

不能用源码 checkout 版本替代正在运行的 CloudCLI 版本。模型别名或配置文件也不能单独证明真实上游 Provider。

### 1.3 ServerOS / Fleet 服务

| 组件 | 现场状态 | 证据等级 |
|---|---|---|
| serverosd | user service active | OBSERVED；健康状态 UNKNOWN |
| Fleet V1 | 服务 active；本地与公网 health 检查均返回 200 | VERIFIED（health path） |
| Legacy Fleet | service inactive/dead；旧 endpoint connection refused；退出结果为正常停止 | OBSERVED |
| Next 旧入口 | 未监听 | OBSERVED |
| Fleet Watch | service active | OBSERVED；端到端 Agent Monitor 未 VERIFIED |
| Core-Lite publish timer | 周期性触发 | OBSERVED；消费链 freshness UNKNOWN |
| Continuation wake timer | 周期性 oneshot | OBSERVED；LLM wakeup 端到端 UNKNOWN |
| Work Board timer | active | OBSERVED；产物一致性 UNKNOWN |
| Cloudflare Tunnel | active；公网 health path 200 | VERIFIED（链路层） |

重要更正：旧 Legacy 入口当前不是备用服务。V1 是本次观测窗口内唯一通过 health 验证的 Fleet 入口。不能根据旧文档继续假设 Legacy 正在服务。

### 1.4 Ability / Plugin

- Host Profile 为设备侧实际 profile；与 Fleet Registry 中的 environment profile 不是同一个字段。
- Ability readback 使用的 checkout 为 clean，但位于非 main 的 profile repin branch。
- 15 项 Skill 检查均为 MATCH 或 NOT_MANAGED_HERE；三个 instruction 文件 MATCH；hooks 为 NOT_MANAGED。
- 真实 HOME 与 CloudCLI sandbox 是不同可见范围；报告只覆盖当前 principal 的真实 HOME。
- Claude 插件身份存在命名差异：分发配置中的 MCP 名称与现场安装的 V1 插件标识不同，需统一 source-of-truth。

## 2. Dell A6000 Linux

### 2.1 主机与资源

| 项目 | 观测 | 状态 |
|---|---|---|
| OS | Ubuntu 26.04 LTS | OBSERVED |
| Kernel / Arch | 7.0 系列 / x86_64 | OBSERVED |
| CPU | Intel Core i9-10900X；10 physical / 20 logical | OBSERVED |
| RAM | 约 31 GiB；约 3.5 GiB available | OBSERVED |
| Swap | 8 GiB 总量；采集时约 73% 使用 | OBSERVED |
| Root filesystem | ext4，约 468 GiB；约 36% 使用 | OBSERVED |
| Data filesystem | XFS，约 1.9 TiB；约 9% 使用 | OBSERVED |
| GPU | NVIDIA RTX A6000，48 GiB | OBSERVED |
| GPU driver / CUDA | 595.99.02 / CUDA 13.2 | OBSERVED |
| GPU load | 采集时约 48%；8 个 compute process（仅计数） | OBSERVED |
| Docker / Compose | 未安装 | OBSERVED |

Assessment：磁盘空间充足，但系统内存与 Swap 是主要约束；GPU 由多租户任务共享。不能把“有 A6000”解释成 GPU 或系统内存可随时分配。

### 2.2 Agent / Runtime

| 项目 | 观测 | 状态 |
|---|---|---|
| OS principal | 当前执行用户与 Fleet runtime 绑定一致 | VERIFIED |
| Claude Code | 版本 2.1.302 只从用户状态文件推断，CLI 未观察到 | DECLARED |
| Claude / Codex / CloudCLI binary | 不在当前 principal PATH | OBSERVED |
| CloudCLI service | user service active，运行约 7 天 | OBSERVED |
| Claude model | sonnet | DECLARED |
| model aliases | 多个 alias 指向同一自定义模型 ID | DECLARED |
| Provider / Gateway | 配置声明经共享 Gateway 与 SSH forward | DECLARED |
| 上游是否 DeepSeek | 仅凭模型 ID 推测，没有实际请求验证 | UNKNOWN / PROBABLE |

未发送模型请求。未读取密钥值。

### 2.3 ServerOS / Monitoring

| 组件 | 现场状态 | 证据等级 |
|---|---|---|
| serverosd | enabled / active，运行于已安装 runtime artifact | OBSERVED |
| Core-Lite publish | 周期 timer active；status snapshot fresh | OBSERVED |
| Node Connector | enabled / active；本地 listener 存在 | OBSERVED；signed peer authorization UNKNOWN |
| Fleet tunnel | enabled / active | OBSERVED；端到端健康 UNKNOWN |
| Agent Monitor | 未发现独立专用 unit | UNKNOWN |
| Core-Lite services block | 存在但为空数组 | OBSERVED；不能解释为服务健康 |
| Core-Lite connector block | NOT_IMPLEMENTED / UNAVAILABLE | SOURCE_CODE / OBSERVED |

设备资源数据可由 Core-Lite 读取，但 services 与 connector 两个能力域仍不完整。

### 2.4 Ability / Plugin

- 当前 principal 的真实 HOME 有 8 个 Skills。
- CloudCLI sandbox HOME 中 Skills 数量为 0。
- 本机 Ability Management checkout 较旧，缺少当前 readback 子命令。
- 临时使用当前上游 checkout 执行 readback 得到 CONVERGED；8 MATCH、5 NOT_MANAGED_HERE。
- 设备没有 Ability Host Profile 文件；environment profile 不能代替 host profile。
- Codex Runtime 的 Ability 可见性与 Claude 不同，不能假设共享 Skill 集合。

## 3. FJPCOS Windows

### 3.1 主机与资源

| 项目 | 观测 | 状态 |
|---|---|---|
| OS | Windows 10 Pro，build 19045 | OBSERVED |
| CPU | Intel Core i5-1135G7，4 cores / 8 threads | OBSERVED |
| RAM | 约 16 GiB | OBSERVED |
| 初始可用内存 | 约 685 MiB；commit 约 94.2% | OBSERVED |
| System drive | 约 238 GiB | OBSERVED |
| Data drive | 约 932 GiB | OBSERVED |
| GPU | Intel Iris Xe | OBSERVED |
| Network | WLAN + Tailscale interface | OBSERVED；具体地址在公开版省略 |

因内存压力，目录扫描采用有界轻量读取；未进行 depth=3 全量扫描、全仓批量哈希或全盘统计。

### 3.2 Agent / Runtime

| 项目 | 观测 | 状态 |
|---|---|---|
| Claude Code | 2.1.295 | OBSERVED |
| Codex CLI | 0.164.0 | OBSERVED |
| Node.js / npm | 24.19.0 / 11.17.0 | OBSERVED |
| CloudCLI | 版本 1.39.0 来自计划任务名称 | OBSERVED（弱证据） |
| CloudCLI install path | 未安全读取计划任务参数，故未知 | UNKNOWN |
| Claude model | sonnet | DECLARED |
| Codex model | gpt-6-astra；provider custom | DECLARED |
| Effective provider / gateway | 未取得安全有效配置读回 | UNKNOWN |
| Permission / sandbox | 配置声明为较宽松模式 | DECLARED；实际 enforcement UNKNOWN |

### 3.3 ServerOS / Fleet

- Core-Lite 二进制与 resource snapshot collector 已安装。
- Core-Lite collector version 0.1.0。
- Core-Lite status：observe-only、live=false、freshness NOT_ASSERTED。
- Core-Lite publish scheduled task 存在，但 Ready 不代表 live telemetry 正在被消费。
- Fleet Node Connector 相关进程与本地 listener 已观察到；一个 health path 返回 404，另一个认证路径返回 401。它们不能单独证明 Connector 不可用；远端路由与 peer authorization 仍 UNKNOWN。
- CloudCLI gateway 相关计划任务正在运行，本地 health path 返回 200。
- 未发现独立 Agent Monitor 安装或专用任务。

### 3.4 Ability / Plugin

- 设备版本状态文件记录 Ability profile work-fj-windows 与 revision。
- 当前用户目录发现 16 个 Skills、11 个 installed plugins。
- Codex 有独立的 MCP server 与 plugin inventory。
- Claude / Codex 的 model、provider、approval、sandbox 配置必须分开读取。
- Core-Lite installed 与 live telemetry 是两个独立状态。

## 4. 跨设备结论

1. A6000 是内网工程节点，但并非无限资源 GPU 节点；任务调度必须考虑 RAM、Swap、GPU 多租户负载。
2. Tokyo 是高容量开发/控制节点，但本次负载极高；目录盘点必须限流。
3. Windows 是 Windows-native、GUI、浏览器和公司客户端验收节点；不应承担无差别的后台重任务。
4. OS principal、Agent Runtime、Model/Gateway、Ability visibility 必须按 principal/runtime 维度记录。
5. 设备角色是当前用途，不是不可变的硬件事实；调度应基于能力、权限、负载和网络证据动态计算。
