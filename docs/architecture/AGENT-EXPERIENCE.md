# Agent 使用面、控制与验证预算

> 总体约束见 [ARCHITECTURE](../../ARCHITECTURE.md)。本页中的毫秒、token、分钟是初始工程目标，不是测量结果或跳过验收的许可证。

## 1. 插件形态

Claude Code 使用原生插件，Codex 使用当前受支持的插件/Skills/MCP adapter。一个 ServerOS product entry；同一 canonical payload 经确定性 builder 生成不同 runtime 包。不能因两个包名称相同就宣称语义已对等，必须分别验证。

最小启动信息：当前 roots/device 的引用、project/repo/Item（有才给）、执行边界摘要、如何获得 context/search/inspect。Run 在真正执行时分配，不强迫普通聊天预建 Item/Run。

目标：ServerOS 注入正文约 0.5–1.5k tokens；小型本地索引 search 默认 top-k 3–8；无网络热路径的 context/search 目标 p95 300ms 内。超过目标先报实测原因并优化，不能删安全边界来达标。全量 capability metadata 不进入模型默认上下文。

## 2. 暴露面与调用路径

建议少量发现操作：context、ability search、ability inspect、operation resolve。写操作保持有 schema 的专用工具，不用一个自由 `execute(command)` 绕过全部类型。

```text
用户目标/当前 Activity
 -> 相关 context capsule（brief/设计/边界/必要来源）
 -> 本地 lexical + tags + aliases search（已知能力可跳过）
 -> 少数候选及缺口
 -> inspect selected Skill/operation
 -> resolve Provider/Binding：support、readiness、authorization 分开
 -> load 正文/引用片段；attach 必需 MCP 或使用既有 CLI
 -> 执行并返回有限结果/证据 locator
```

Agent native tool search 优先复用。不能假设搜索工具已具备启动任意新 MCP、热修改运行时配置或压缩已经注入的上下文的能力。目标 runtime 支持什么，就声明什么；不支持热 attach 时使用预配置的受控 adapter 或显式下次会话生效，不伪造 READY。

bootstrap hooks 只做轻量本地 context/lifecycle；不调用 LLM、不拉云、不跑 map/full suite。权限检查必须短路、可缓存但按 actor/operation/scope/policy revision 区分；读取敏感内容也受 ACL。复杂工作计划不藏在 PreToolUse hook。

## 3. 缓存与失效

- capability/Skill metadata：以 canonical revision/content digest 为键；内容变才重建。
- policy/context：以 actor、scope、policy revision、Item/设计 revision 为键；撤销立即失效。
- readiness：source、采样时刻、有效期、目标操作一起记录；动态过期后重探当前需要的维度。
- 验证证据：source revision+working diff hash+依赖/工具链/配置+测试/fixture 集合为键；任一影响项改变则相应失效。
- destructive/shared mutation：执行前重取目标 revision、owner/lease、授权，不复用过期快照。

UNKNOWN 只触发能够改变当前决策的最小查证；可选 capability 未知不阻塞无依赖工作。拒绝结果携带缺哪个输入及允许的下一步，不暴露秘密。

## 4. Session、终端与多消费者

AgentSession 是逻辑实例；native provider ID、tmux pane、PID、chat ID 是不同 locator。保留 session lifecycle、turn lifecycle、request acceptance 三层，不互相推断。

- native API/SDK 是首选控制路径；tmux/PTY 可作为已声明 provider，但不能偷偷读取 BotMux 私有文件补事实。
- 同一 native session 的输入串行；并发新 turn 要排队或具名拒绝；重复 request id 去重。
- 同一 Item 可以有多个隔离执行 Session；同一工作树/写范围只允许一个有效 writer lease。
- resume 必须证明 native continuity；否则叫 fresh session + checkpoint handoff。
- archive/restore 只对 Provider 支持且非活跃写入的 transcript，不能中断业务来整理历史。
- 会话目录保持 runtime native；ServerOS 只保存必要规范化引用、关联与 checkpoint，不镜像全部聊天。

## 5. Execution Envelope

Envelope 是 work-engine/Item 持久状态中指向精确目标/设计 revision 的授权记录，包含：允许 repos/paths、operation class、费用/时间/并行度、数据范围、发布/部署权限、验证范围、停止条件。

批准有意义的工作范围后，Agent 可自主实施与定向测试，不逐个 mkdir/编辑请示。要求改变目标/验收、重大选型、跨 repo、共享线上、秘密、破坏性动作或预算超限时才升级。已有批准不因普通会话重启消失；目标/设计发生实质变化则相应授权失效。

文档中的建议 Envelope 不是凭空授予系统权限，必须由当前用户授权/已有策略形成记录。

## 6. 最少充分验证

| 变更类别 | 默认检查 | 扩大条件 | 初始反馈预算 |
|---|---|---|---|
| 阅读/讨论 | 必需来源/权限/版本，不跑测试 | 要提出可执行事实且证据不足 | 几乎无独立测试成本 |
| tiny 文案/引用 | diff、受影响链接/格式；行为指令修改不按文案算 | 改了机器解析内容/政策/操作协议 | 约 1 分钟 |
| small 局部行为 | 最相关 unit/类型/语法检查与一条回归 | 测试失败、消费者受影响、动态加载 | 约 3 分钟 |
| medium 跨模块 | 依赖闭合范围测试 + 一条端到端路径 | 共享 schema/public contract/存储权限变化 | 约 10 分钟 |
| large/contract | 相关契约、安全边界、集成与隔离安装 | 合并后的候选发行 | 计划化，不套小改硬上限 |
| high-risk/live | fresh target/owner/approval、恢复/备份条件、postcondition | 目标变化或动作结果不确定 | 正确性优先，不因超时跳过 |

预算是升级和诊断阈值，不是到时即可 PASS。whole-repo suite 在集成/发行边界跑必要的一轮；修正后先定向复跑，只有最终变更使先前广泛结论失效才重跑受影响广泛检查。不要求每个 Skill 各自再执行全部 gate。

不把“文件很少”当低风险：权限、准入、schema、删除、依赖版本与测试 oracle 变化可以是大改。动态反射/插件/运行配置影响要纳入选测；缺依赖信息时保守扩大相关集成，不假称选测一定发现全部 bug。

失败报告区分产品回归、环境缺失、flaky/待查、基线失败。不得跳过 failing assertions、xfailed 全套、降低阈值或只改测试让实现显绿。验证工具修复走一次自身定向检查，不无限递归。

停止：验收已满足；相关风险已覆盖；新增检查不再改变决定。无信息增益的重复模型调用、probe、全扫描停止，转可解释 blocker 或继续其他独立工作。

## 7. 人类控制与 CloudCLI

飞书负责播报、关键告警、Today、Decision Packet、明确范围的审批与会话操作。Web Console 负责深入查看会话、diff、环境、Quay 和证据。tmux/SSH 负责人工底层访问；不是所有入口都强行穿过 CC 插件。

CloudCLI 是可选、有较大权限的外部应用，不应被概括成“完全无副作用的 UI”。其原生文件/Git/MCP/终端功能可能直接写本机，ServerOS 插件不会自动拦住这些写入。集成必须选择受限 workspace/独立身份/sandbox/native policy，或明确 external/unmanaged。不改变全局 Claude 配置以方便 UI，避免另一 active materialization。

保留上游能力，优先 adapter/plugin，不重建 IDE。插件方案不自动免除上游许可义务；如分发/修改 CloudCLI，应记录 license 与网络服务义务，不能把 AGPL 代码复制成另一个许可证。

### 控制请求

绑定 actor、tenant/conversation、operation、target、target revision、request id、expiry、权限/预算。回调验签、防重放；执行前重解析。聊天里的“好的”只有在唯一明确 pending request 与认证上下文成立时才能关联，不能泛化成授权。

操作 ACK/SNOOZE 只改告警交互状态，不证明故障已修复；pause/interrupt/stop 分别按 Provider 原语提供。输入已接受、任务完成、业务验收通过、报告发送成功分别留 receipt。

## 8. 卡片与信息层级

- Status：三块资源概览 + 当前 required environment readiness；可选能力不把整机刷红；过期显示未知。
- Alert：先事件/对象/影响/建议，默认最多少量高价值事件；聚合须保留原 refs，不能凭同词假定同根因。
- Recovery：仅对此接收目标已通知且现在肯定恢复的事件；采集失联不是恢复。
- Work/Decision：今天重点、下一步、需要的决定、选项/建议/代价；普通成功步骤静默。
- Daily/Weekly：重要结果差量与 Quay 贡献，不展示所有命令。

卡片详情保留 source/freshness/原 code/receipt。第一屏不放 OPEN/RESOLVED 总账、generation、baseline、raw subject URI、大段免责；未知用中性，不强行绿/红。无后端的按钮不得可点击。

统一 presentation semantics；CLI、Feishu JSON、HTML/CloudCLI 用不同 renderer 消费同一模型。HTML 预览不是飞书实际显示证据；窄屏视觉和真实 TEST 接收面分别验收。通知 event 与交付状态分开；记录实际可见事件集合，未显示的条目不能记成已告知。
