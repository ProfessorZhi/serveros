# ServerOS

[English](README.md) · [完整架构](ARCHITECTURE.md) · [Fleet V1 设备/MCP 运行架构](docs/architecture/FLEET-V1-TOPOLOGY-OPERATIONS.md) · [Agent/Session V2](docs/architecture/AGENT-IDENTITY-SESSION-DELEGATION.md) · [本轮架构审查](docs/architecture/CONSOLIDATION-REVIEW-20260921.md)

**当前 Fleet 完整 V1 的已确认开发范围（2026-10-17）：** [完整三机 Core/Fleet 拓扑](docs/architecture/FLEET-V1-TOPOLOGY-OPERATIONS.md) · [一口气多线交付计划](docs/plans/plan-02-complete-fleet-v1-delivery-campaign.md) · [总 Issue #244](https://github.com/ProfessorZhi/serveros/issues/244)。东京共享 Linux、Dell A6000 和 Windows 各自授权 OS 用户拥有 C++ Native Core、资源/服务/网络状态和受控自愈；CC 与 Codex 的真实 Runtime 经 CloudCLI/Node 接入，Device Ability 按实际安装、健康及权限分配。Windows 127.0.0.1:16095 → SSH 隧道 → Selkies/Claude Web 及 Tailscale/RDC 属于必须核实的节点服务路径，主要代码开发和测试在东京/Dell，Windows 只做必要原生验证。**先本地 Claude Code Fleet Plugin + 同源 Operator Skills 完整 E2E，最后才注册 ChatGPT 插件**。完整 V1 接受目标已包含真实 Codex，不再把基础 Codex Provider 全部推迟到 V2；当前仍不得声称 Codex 已经验证。Hosted GitHub Actions 配额不足只豁免托管CI，不豁免真实本地测试和设备验收。

实施入口：[唯一P0–P12计划](docs/CC-EXECUTION-PLAN.md) · [2026-09-25现场基线](docs/IMPLEMENTATION-BASELINE-20260925.md) · [交给服务器CC的短提示](docs/CC-IMPLEMENTATION-HANDOFF.md)。计划覆盖实现、获准安装、真实验收、定时启用与目录收口，不代表已执行。

**ServerOS 是个人用户态 Agent 工作操作系统：增强 Claude Code、Codex，而不是在它们上面再套一个更笨的调度者。**

## 你每天实际得到什么

```text
白天
├─ 飞书 IM / Doc / Wiki / 妙记 / 文件 / Follow 增量自动进入
├─ routine facts 自动更新 Understanding / Memory
├─ 已选 Focus 由原 Parent 持续推进
├─ 需要时自动准备 worktree / runtime / research / Quay / tests
└─ OWNED / FOLLOW_MIRROR / 获准共享章节按变化维护，不重复造正文

19:xx
└─ source catch-up + understanding reconciliation，缺材料只标 gap

20:00
└─ CloudCLI 只给真正值得你看的：
   自动更新 / 需要你决定 / 冲突 / 理解缺口

你接受或调整 0–2 个 Focus 后
└─ 已授权工作可当晚继续，不等第二天、不要求你逐步说“继续”

第二天或新 Session
└─ 从当前 Requirement / checkpoint / worker / effect 状态继续，
   不重讲历史、不重复派 child、不重放未知外部效果
```

这不是固定流水线。一天可以没有新 Requirement、没有 Quay、没有报告，Focus 也可以为 0；调研可以先实验，测试可以推翻设计，交付物也可以只有验证或报告。系统应自动完成 HOW，只有目标、重要取舍、真正受保护的效果或不可替代的人类输入才打断你。你随时新开 Session，提起某个项目或需求，Agent 应恢复最小有效上下文，而不是要求你重新解释全部历史。

## 核心理念

**正确的环境、上下文、记忆、能力与权限边界，在需要时出现。**

安装的能力可以很多，当前上下文必须很小。ServerOS 提供目标、已知事实、工作连续性、资源和证据；CC/Codex 决定怎么研究、实验、拆分、使用工具和迭代。

```text
默认：很薄的入口
说出需求后：相关目标、checkpoint、约束、来源
真正需要时：记忆详情、Skill 正文、MCP、原始证据
```

不是每步先找 ServerOS 批准方法，不是每次改文档先跑全量测试，更不是为了注册表完整制造工作。

## 一张图看懂整个架构

```text
你 / CloudCLI / 飞书
        │
        ▼
Claude Code / Codex                     ← 主脑：理解、研究、设计、实现
        │
        ▼
ServerOS Plugin                         ← 当前相关 context / Skills / MCP
   ├─ serveros-node-mcp ─────────┐
   └─ serveros-agent-mcp ────────┤
                                 ▼
                              serverosd
                    唯一常驻 Node Core
                 节点事实 / admission / lease /
                 effect coordination / self-health
                                 │
        ┌────────────────────────┼────────────────────────┐
        ▼                        ▼                        ▼
语义/工作权威                外部 Provider             Ability Fabric
B2 Claims/Views              Feishu / GitHub           G1 registry
Requirement/Work Engine      OpenViking / MetaCC       Skills / MCPs
Agent Control                Quay / Browser / OS       Plugins / Providers
```

这里最容易混淆的三点：

- **Ability 是产品职责，Skill/MCP/Plugin 是实现方式。** 不要求每个 Ability 配齐一套 Skill+MCP+Plugin。
- **Plugin 是 Agent 入口，serverosd 是常驻 Node Core。** Plugin 内的两个 MCP 只是适配器，不保存第二份 ServerOS 状态。
- **OpenViking/MetaCC/GitHub 都服务记忆与协作，但不抢当前事实权威。** 当前业务事实仍由 Source、B2、Requirement、Work Engine、Agent Control 各自负责。

## 五条自动循环

ServerOS 的日常自动化不是一条固定流水线，而是五条相互连接、可独立降级的循环：

```text
1. 信息循环
   IM / Doc / Meeting / Web / File
   → B1 Capture / Follow
   → B2 Structured Understanding
   → A2 Memory retrieval / invalidation
   → B3 Requirement candidates

2. 工作循环
   Formal Requirement
   → canonical Work Item
   → 0–2 Active Focus
   → A1 Parent continuity
   → B5/B6/B7 + A6 execution
   → A7 acceptance
   → Outcome

3. 能力循环
   capability gap
   → G1 查 Native / 已装 / 上游
   → 最薄 realization
   → eval / package / install
   → fresh consumer
   → 真实失败再迭代

4. 知识循环
   Source revision
   → Follow / read delta
   → Understanding
   → B9 更新 FOLLOW_MIRROR / EXTERNAL_MAINTAINED / OWNED 文档
   → readback
   → local / private Git continuity

5. 运行循环
   Observe
   → health / impact / responsibility
   → native supervisor / bounded recovery
   → Daily Status / Incident
   → 真正需要人时才进入 Human Control
```

Memory、权限和证据横穿五条循环。某一条 Provider 暂时不可用只让依赖它的部分降级，不把整个 ServerOS 变成全局 `NOT_READY`。

## 日常会自动整理什么

| 对象 | 默认自动行为 | 不会静默做的事 |
|---|---|---|
| Workspace / 目录 | 新产物在Project/Work Item/canonical parent明确时自动落到正确worktree/artifact/test根；低频检查结构漂移 | 不因“目录看着乱”移动历史repo、别人worktree、唯一证据或未知目录；root级move/delete先走对应effect边界 |
| 飞书 IM / 文件 | 增量捕获新消息、编辑、转发和需要的附件；partial保gap，后续自动补 | 不把每条消息变Requirement，不为计数下载全部媒体，不把私密材料自动外发 |
| 关注 Doc / Wiki / Web | 保存稳定source identity/revision；有变化只重读受影响范围并更新理解/mirror | 不因Follow一页扩成全库，不把收藏自动变永久订阅/全文转载 |
| 会议 / 妙记 / 录音 | MeetingBundle关联Minutes、转录、录音、附件；转录/材料晚到后自动补读并触发受影响理解 | 不要求每场会都有MP3+妙记+纪要；背景/远控音频不自动形成业务承诺；无长期阅读价值不强建MyDocs |
| OWNED指南 / FOLLOW_MIRROR | standing stewardship按source revision更新允许区块，保护人改，publish后readback | 不whole overwrite，不因source变化自动扩大受众/ACL/业务政策 |
| 共享维护文档 | 在获准原对象/章节原位维护，变化跟进并三方比较 | 不复制第二份可编辑正文，不接管owner/priority |
| 日报 / 周报 | 启用standing routine后从accepted Outcome/evidence生成同一logical report，发布/送达分开恢复 | 不为日报重跑E2E、不拼Task数量、不让“日报已发”停止夜间Focus |
| 运行状态 | 工作日更新同一Daily Status Card；真实Incident按episode跟踪 | 不因为无需人工就假绿，也不把历史/diagnostic finding全升成事故 |

自动整理遵循同一个原则：**明确的新增/维护路径自动做，历史结构性破坏动作谨慎做。** 系统应该替你完成定位、diff、局部patch、readback、索引和接续，而不是让你每天当文件管理员。

## 一个小核，两大能力家族

每个受管节点/运行范围正常只有一个常驻核心 **serverosd**。它生产真实资源观察、归属、边界准入与有限的环境修复，不做 LLM 规划，也不重复实现 systemd 等进程监督器。

产品 Core 的两个 canonical 成员是 **serveros + ability-management**；这不代表两个常驻 daemon。

| 家族 | 覆盖内容 |
|---|---|
| Agent 工作基础设施 | 合规、凭据、资源、Session、上下文与记忆、能力发现分发、Workspace/Git、构建与测试环境、E2E、跨设备连接、发布运维 |
| 信息到交付 | 飞书归档/关注、结构化认识与对账、需求看板、需求挖掘、调研设计、CC/Quay 执行、知识与文档维护、汇报、定期任务 |

家族和簇是能力导航，不是每个框都造一个仓库、Skill 或服务。

## 几条最重要的分工

**Session：** 一个需求可以有多个干净 Session。原生线程 ID、进程、tmux 窗口不是 Item 身份。手机打开东京会话是远程接入；换到 Windows 执行是工作交接，二者不同。

**记忆：** 目标架构是一份共享的 OpenViking 长期记忆服务运行在 Tokyo，Claude Code、Codex 与获准的远端 Agent 通过各自原生集成/客户端访问同一个用户记忆世界；MetaCC 保留 Session 历史与行为分析，原生 memory 继续作为本地便利层。OpenViking 负责跨 Session / 跨项目 / 跨设备的相关性召回和经验记忆，**不覆盖** B2 当前 Claims/Views、原 Source、Requirement、Work Engine 或权限事实。GitHub 保存可版本化的 records、设计和受众合适的知识，不同步 OpenViking 的向量索引/内部数据库。ChatGPT 当前优先通过 GitHub/项目文件读取这些权威材料，不为了接入 ChatGPT 把 OpenViking 暴露公网。

**能力：** 全设备共享 ability-management；外部作者的能力保留来源。设备根据兼容性、安装、目标、网络和授权形成能力视图，当前 Session 只加载有关部分。登记、安装、可用和授权是四件事。

**Quay：** 用来完成有价值的委派、并行实验、实现和独立验证。CC 可以在已批准范围内自主使用，但 Quay Task 完成不等于业务 Item 完成，也不把全部 Quay 方法论强塞给所有项目。

**后台运行：** 批准的工作可以继续。超时后的外部效果先查清再重试；需要你时保留 checkpoint，只暂停相关边界，不冻结其他项目。

## 你看到的界面

**CloudCLI 与 CC 终端**是主要交流界面。保留原生项目、会话、文件、Git、终端体验，优先通过受支持的插件补充 ServerOS 上下文，不要求先 fork 整个 UI。

**飞书**负责信息来源、提醒、阅读和汇报：

- 我的文档库：当前工作、对账、结构化认识、需求与报告。
- 我的文档库：动态工作、每日对齐、项目工作台和正式汇报。
- 个人笔记：私人长期知识和自己负责维护的方法笔记。
- 公开文档：我们承担正确性责任的长期教程/指南；名字不自动代表互联网公开。
- 外部文档：只读长期关注资料的稳定 Source Card / FOLLOW_MIRROR；原 Source 仍是正文权威。
- 共同维护的别人文档：保持原位，按授权章节维护，不复制第二份飞书正文。
- 云盘：MP3、图片、PDF、PPTX、ZIP、E2E trace 等 Asset Plane；IM、妙记/Minutes 仍保留原始 Source 身份。

日报、周报、专项汇报按同事可读的正式行文生成。生成报告、放到飞书和发送给别人分别受权限约束。日报记录真实 Quay 贡献，不为凑使用记录制造任务。

**GitHub**是 CC/GPT 当前的版本化协作面。较大的需求通常有 brief、design、execution、report；小工作不必凑四个文件。GPT 写回被接受的设计后，CC 在原授权范围内继续，不能只凭一行 READY 就扩大权限。

## 跨项目、跨设备记忆怎样工作

```text
                 ┌─ current Source / B2 Claims & Views ── 权威当前事实
Claude / Codex ──┤
Windows Agent  ──┼─ OpenViking @ Tokyo ──────────────── 长期相关性/经验召回
Tokyo Agent    ──┤          │
                 │          └─ private persistent storage
                 ├─ MetaCC / native transcripts ─────── Session历史/行为分析
                 └─ GitHub private records ──────────── 版本化语义记录/设计/文档
```

目标是一份共享 OpenViking 服务，而不是每台设备各自建向量库再用 Git 同步。Tokyo 本地 Agent 可走本机私有入口；Windows 等远端通过受认证的 Tailscale/private endpoint 使用同一服务和同一用户 namespace。设备只是 provenance/applicability，不形成独立“记忆宇宙”。

Claude Code/Codex 优先复用 OpenViking 官方 Memory Plugin 的 lifecycle hooks + MCP：会话前召回、回合后捕获、compaction/session end 提交。ServerOS **关闭/不采用 OpenViking 的 Skill catalog 作为能力权威**，Skill 仍由 ability-management/G1 发现与分发，避免两套能力目录。

召回默认先当前 Project/Item，再个人稳定偏好/经验；跨项目内容只有相关且 scope 允许才进入。生产秘密、credential、受限原始日志和不应跨项目传播的材料不因“记忆方便”被自动捕获或外发。

OpenViking 当前仍是 **目标/未绑定 Provider**，不是 LIVE 事实。服务挂掉时从 current records、checkpoint、Git/source 和 native history 继续；不会因此阻断工作或复活已经纠正的旧结论。

## 设备与合规

当前完整 Fleet V1 目标拓扑：**Tokyo/vince 主 Work+Fleet 节点、Dell A6000/fjd Compute/Linux Core 节点、Windows FJPCOS 原生 Edge 节点**；手机与 ChatGPT 是后续客户端入口，北京为历史/未验证绑定。设备注册不表示其所有 OS 用户、Codex Runtime、服务或 Session 都有授权。

Tailscale 是默认私网 Provider：**先用 tailnet，特殊需要才开隧道**。节点在线不等于所有端口可用；替换隧道前验证实际客户端、应用、认证和流式连接，并保证服务可自启动。

Tokyo 默认使用 vince。共享网关的 ubuntu 维护例外只限明确目标与操作。**LiteLLM 生产和东京共享网关的生产切换、重启、配置变更、回滚仍由你执行。** Skill 文字和“已批准”卡片本身不构成技术强制边界。

## 仓库职责

| 仓库 | 职责 |
|---|---|
| serveros | 产品架构、插件组合、入口、确定性契约与节点运行时 |
| ability-management | 能力身份、来源、revision、可复用 Skill 与分发 |
| work-engine | 工作控制状态、Run/动作回执、看板投影 |
| hwz-records | 确认的认识、稳定工作意图、设计与人类记录 |
| hwz-ops | 实例目录、拓扑、目标/Binding、策略与带时间的观察地图 |

MarketOps 和 Support Bot 保持并行业务项目。Bot 代码是 litellm-support-bot，知识仓是 troublebuddy-kb-bridge；ServerOS 不接管它们的路线或正在运行的实验。

## 从哪里继续

[ARCHITECTURE.md](ARCHITECTURE.md) 是唯一规范性产品架构；中英文 README 都是说明入口。
[文档索引](docs/README.md) 区分当前契约与旧实现说明。

**设计完成不代表已经上线。** 实施按架构中的场景验收，分别记录 CODE、INTEGRATION、LIVE 证据，不用文件数量、注册表绿色或文档提交代替真实可用性。
