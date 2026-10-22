# Fleet V2 分阶段交付与验收：自治组织、Full-Control 与多 Runtime

> **2026-10-22 · DESIGN / NOT_IMPLEMENTED / NOT_DEPLOYED**。本计划将 [V2 产品架构](../architecture/FLEET-V2-AUTONOMOUS-ORGANIZATION.md) 变为可检验的阶段目标。它不授予真实 OS、生产、数据或第三方账户权限；当前受保护操作仍遵守现有生效权限与审批。任何离线测试绿灯不能转成生产上线签收。

## A. 从根因到结果的交付路线

| 阶段 | 工作包 | 核心交付 | 前置 / 当前状态 |
| --- | --- | --- | --- |
| D0 | 固化需求和决策 | V2 产品契约、组织/角色与 Owner Full-Control 目标 | DESIGN，仅本 PR |
| D1 | 只读责任与能力 Join | Work/Owner/epoch/Grant/Lease/Role/Native Session/Device/Effective Ability 同源只读视图 | 可先做 fake/只读；#389 仅纯离线预览 |
| D2 | 真实可信 Writer | #237 B1–B5、原始 DB 写者隔离、效果提交时 epoch/TTL/lease 验证、持久幂等 | 当前 V1 blocker；双 UID / IPC / OS 权限实测另授权 |
| D3 | Owner Full-Control mandate | 受管资产清单、可撤销持续授权、实际 OS/Node/Provider 配置与不逐次询问 | 依赖 D2；目前没有真实启用 |
| D4 | 多 Runtime Fabric | CC/Codex/DSH 可验 native create/resume/send/result/cancel，CloudCLI 可选 UI | CC/Codex 基础路径属 V1 目标；DSH 属 V2 |
| D5 | 自治 Project Parent | 动态 child Work、分管/Expert/Reviewer、协作与持久接续、预算和限额 | D1–D4 |
| D6 | 设备维修团队 | Node Core 事件→设备管家→专家诊断→真实效果→健康复验 | D2/D3/D5 + 受管设备测试 |
| D7 | 自我迭代 | 平台维护分管→隔离实现→独立 QA→canary→真实客户端 readback→rollback | D2/D3/D5；受保护部署政策 |
| D8 | OpenAgents 兼容实验 | native vs SDK 消息/委派/@mention/断连恢复对照 | 必须先有 D5 native 基线 |
| D9 | ChatGPT/Codex 双入口 | 同源 Fleet Skills/MCP，客户端身份和插件加载分别验收 | D3–D5 与真实客户端 |

**D0/D1 和契约级 fake tests 可以并行推进；D2 未结束前不把更大的跨设备写能力投入生产。** 该矩阵是依赖图，不要求所有研究按单一顺序排队。

## B. Owner Full-Control 的实施合同

Owner 需求：在 Owner 真正有权管理、已登记给 Fleet 的设备/账户/服务/工作根内，可以持续授予总管、分管及执行者自主创建项目、Session、Role、运行命令、安装依赖、维护设备、更新插件与部署已纳管服务等能力；正常工作不按逐次工具调用索取人工确认。

### B1 必须分开的维度

- Owner 实际身份与可信启用/撤回入口；聊天文本或角色描述不授予身份。
- Managed asset scope：设备 ID、OS 用户、项目根/仓库、服务实例、数据域和拥有关系。
- Executor scope：Agent 的真实 Runtime、credentials、Node、OS principal，以及可用的实际 OS/服务权限。
- Effect categories：project/session/file/build/test/service/config/permissions/deploy/delete/self-update 等受保护动作。
- Standing risk policy：覆盖哪些生产对象、何种变更允许在长期 mandate 内自动执行、何时必须提交 Owner 的范围外请求；高影响操作需要实际的恢复策略与可观察证据。
- Authorization epoch/revocation：不可因 ABA、Grant 原值恢复、过期缓存、旧 token、角色热更新而重新获得旧写权。
- Budget & scheduling：合理的资源使用策略，不以每新建一个 Session 为人工审批点，也不容许无限并发抢占正在工作的人或 Agent。
- Actual effect readback：Writer 入场、变更生效、健康恢复、客户端插件加载是不同事实。

这些都是待实现的合同，不是独立重复的 Assignment/Grant 数据库。Full-Control 是已登记对象的 Owner 自治，**不是**自动取得 root、他人 UID、别的项目或未经登记生产资产的权利。

### B2 体验验收

Owner 激活一个受管环境后，正常创建工作树/项目、新 Session、派发 Worker、修改文件、安装开发依赖、运行测试不得反复弹「允许吗」。受管生产对象、权限配置变更或删除等高影响动作，若未来真实 mandate 明确覆盖，按配置的受保护效果与恢复策略执行；若不覆盖或真实底层权限不足，必须如实拒绝并标明需要扩大的精确边界，不能自授权。

旧 V1 的生产审批/权限配置不因修改文档生效；实际迁移另审查和验收。

## C. 组件契约与最短执行链

1. **RoleProfile：** 版本化职责、所需 Abilities、Runtime 偏好、独立 Reviewer、汇报方式。角色不持有 grant。
2. **CurrentResponsibility：** 从既有 Work/Owner/epoch/grant/lease/immutable Session binding 动态得出；无独立可编辑 Assignment store。
3. **Session allocator：** 查当前设备+OS principal+真实有效 Provider/模型、空闲容量和 Session provenance；同 Work、同 epoch 且 native support 已证实时才 resume；否则建立隔离 Session。
4. **Project Parent：** 在已接受 Work 下自行分拆可验收 child Work，选择 Worktree/Writer，应用每个 child 的预算、超时与当前凭据。
5. **Native adapter：** Claude Code、Codex、DSH 各返回准确的 supports/readiness/admission、native session、run、事件与错误；不伪装不同协议原生支持一致。
6. **Dispatch attempt & effect journal：** current Owner/lease/epoch 在 mutation admission 和最终效果提交处受信任地校验；未知 Provider 结果记 EFFECT_UNKNOWN，并向真实效果来源对账，不能盲重发。
7. **Parent acceptance：** 子 Agent 声称 DONE 只能变成 RESULT_READY；独立 Reviewer 阅读原始 diff/test/receipt 后 Parent 才能 ACCEPTED。
8. **设备管家：** Node Core 先执行已有批准的 deterministic bounded self-heal；LLM 专家按 Incident 触发，不常驻无限轮询。
9. **平台维护：** 自更新必须提交候选、独立验收、逐步发布与撤销通路、真实消费端 readback；禁止修改自身验收标准获得通过。

## D. 端到端验收矩阵（含反例）

| ID | 场景 | 必须证明的结果 |
| --- | --- | --- |
| V2-01 | Owner 交代新建受管项目 | 不逐次询问、真实目录/仓库和 Work identity 可读回 |
| V2-02 | 项目 Parent 创建多个不同 Runtime Session | 各自 native Session ID、OS principal、model/mode 和真实 Tool surface 不混淆 |
| V2-03 | 同 Role 服务不同 Work | Role 能复用，但 Session/context/epoch 独立 |
| V2-04 | 同 Work/epoch 的原生 Session 可恢复 | Native resume 才叫 resume；新建不能冒充恢复 |
| V2-05 | 其他项目的 Session idle | 不混用历史；需干净 Workspace/新 Session |
| V2-06 | Unknown/busy/stuck Session | 不当作可用空闲槽，不偷停当前 WIP |
| V2-07 | 两位 Worker 争同一 write scope | 只有一个当前 Writer，旧 Holder 即使仍运行也不能提交 |
| V2-08 | Context/Grant A→B→A | 旧 token/epoch 仍失效；同摘要不能复活 |
| V2-09 | 不可信调用方有原始 SQLite 写权 | 不能通过 REPLACE/伪造记录拿到权限；若能则阻断 LIVE |
| V2-10 | 发送已被 Provider 接受但回复丢失、服务重启 | 同一次 attempt 对账或 EFFECT_UNKNOWN，不重复产生付费 Run |
| V2-11 | Worker 声称通过但原始测试失败 | Parent 不 ACCEPTED，失败证据留存 |
| V2-12 | 用户关闭 ChatGPT 改用 Codex | Work 状态持久，能接续，不需要用户重复讲历史 |
| V2-13 | 总管 Session 崩溃 | 既有合法下游 Work 可继续，新总管不会重复创建 child |
| V2-14 | @peer 请求额外 OS/Owner 权限 | @ 是消息，执行端拒绝权限扩张 |
| V2-15 | Full-Control 的项目/Session/测试工作 | 不逐次人工提示，真实 OS 允许范围仍被记录和核验 |
| V2-16 | Full-Control 被撤回/缩小 | 新请求拒绝；旧 Session 的受保护写入被 fence |
| V2-17 | Agent 设定写「我是管理员」 | 有效 Grant/UID/Lease 没有变化 |
| V2-18 | Linux Permission denied | 正确区分错误 UID、路径、失效凭据与本就应该拒绝的访问 |
| V2-19 | 重启指令退出码 0，服务仍未就绪 | HANDLING 不变成 RECOVERED；后续真实健康观测决定 |
| V2-20 | 故障设备完全断线 | UNREACHABLE，不能编造远程修好；仅已有许可救援路径可执行 |
| V2-21 | Project Parent 与 Device Steward 并行 | Work Writer 与设备资源租约互不冒充、不覆盖现有 WIP |
| V2-22 | Codex Parent→DSH Worker→CC Review | 各 Session/Worktree 与测试证据可追溯、独立验收 |
| V2-23 | DSH headless/SDK/ACP 行为不同 | 对恢复、streaming、cancel、权限真实申报；不伪装支持 |
| V2-24 | OpenAgents task duplicate/@/跨身份消息 | 不在 SDK 内自选 Owner 权限；映射 existing Work/Attempt 幂等 |
| V2-25 | PR 合并但消费端未升级 | 不报告 Fleet/插件已更新 |
| V2-26 | 自升级 canary 失败 | 真实回滚 + 稳定的 Owner 停止通路 |
| V2-27 | 自更新开发者想修改 QA 门槛 | 独立校验者阻断自我批准 |
| V2-28 | 东京↔Dell 分区 | Dell 可完成已授权有限工作，不发行新 Grant，不重放未知 Effect |
| V2-29 | ChatGPT/Codex 同时修改 Work 优先级 | revision/epoch 冲突检测，不无声 last-writer-wins |
| V2-30 | 单 Agent / Native 分层 / OpenAgents 比较 | 可重复统计正确率、完成延迟、成本、人介入、接续能力 |
| V2-31 | 全权模式增加第三方设备或新 UID | 不靠聊天权限字符串自动扩权；需要实际 Owner 扩域 |
| V2-32 | Parent 接收全部 Worker transcript | 无关私有上下文不复制，最小证据与来源足够验收 |

上表所有测试都必须给出代码/配置固定 SHA、测试环境、设备/Runtime/credential 实际版本、观测与原始证据 locator，不能用通用「全绿」替代逐项签收。验收主体应与实现者隔离。

## E. 衡量 V2 是否真正成功

对同样一组可接受 Work，对比四个工作流：
- A：当前 ChatGPT 每轮通过 Fleet MCP 逐条转发；
- B：单一原生 Agent 自行执行；
- C：V2 Native Parent + 分管/专家 + 持久调度；
- D：C 增加 OpenAgents 协作传输。

按 Work 记录：人说「继续」次数、总管模型介入轮数、人工确认次数、平均跨层转发文本量、独立验收成功率、p50/p95 完成时间、费用/Token、设备峰值内存和并发、失联后恢复能力。**多 Agent 没有净收益就回退单 Agent 快路径。** Owner Full-Control 的验收还应包含：常规任务无需询问，以及撤销后及时拒绝，而不是仅看被允许的次数。

## F. 低风险实现顺序与代码所有权

首轮可以只做产品合同、fixture、fake provider 和只读 Join：不创建运行 Session，不安装第三方、不改生产。之后尽量保持相互独立的窄范围 PR：
1. RoleProfile/Responsibility view，只读联接 existing Work/Agent Control/Ability，负例先行。
2. V1 #237 真 Writer/epoch/lease、OS 身份/IPC/DB 隔离，先独立 QA；杜绝通过多加 SQLite trigger 冒充真正隔离。
3. Owner full-control 配置/撤回 fake test，最后才在实际 Owner 激活后测试真实 effect。
4. DSH 官方 SDK/ACP 适配 vs CloudCLI 可选 UI；Pin revision，严禁把 Headless 不能恢复当作 DSH 全部能力。
5. 项目分管自治闭环、父子 Work 与 Reviewer、事件驱动继续执行。
6. Device repair 与 Platform update 两个专场 canary，均要求失败/回退。
7. OpenAgents 与 native 通道的可关闭对照试验，最后多客户端同源验收。

**绝不因本计划创建或激活真实用户、root、生产密钥、端口/daemon、第三方 Workspace 上传。** 此类工作必须落在后续经当前 Owner 授权的明确执行任务内。

## G. 规范关联

- [Fleet V2 产品架构](../architecture/FLEET-V2-AUTONOMOUS-ORGANIZATION.md)
- [Agent/Session 既有细节设计](../architecture/AGENT-IDENTITY-SESSION-DELEGATION.md)
- [Native Agent Work Environment](../architecture/NATIVE-WORK-ENVIRONMENT.md)
- [Fleet V1 release gate #237](https://github.com/ProfessorZhi/serveros/issues/237)
- [Fleet V1 完整交付 #244](https://github.com/ProfessorZhi/serveros/issues/244)
- [V2 R0 draft #389](https://github.com/ProfessorZhi/serveros/pull/389)
- [OpenAgents 试点 #187](https://github.com/ProfessorZhi/serveros/issues/187)
- [ChatGPT Fleet Skills #186](https://github.com/ProfessorZhi/serveros/issues/186)
- [旧 federated design](fleet-next-agent-federation-v0.md)

**真实阻塞仍然存在：** 可信 Owner/Writer 身份、Effect 提交门槛、跨设备幂等和真实恢复验证没有因为有文档而自动完成。产品目标、已提交代码、测试、设备运行和真实客户端消费必须持续分级报告。
