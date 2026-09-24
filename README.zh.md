# ServerOS

[English](README.md) · [完整架构](ARCHITECTURE.md) · [本轮架构审查](docs/architecture/CONSOLIDATION-REVIEW-20260921.md)

**ServerOS 是个人用户态 Agent 工作操作系统：增强 Claude Code、Codex，而不是在它们上面再套一个更笨的调度者。**

## 你每天实际得到什么

```text
昨天的飞书信息与真实工作
  → ServerOS 整理认识变化
  → 在 CloudCLI 和 Coordinator 对账、纠正
  → 个人看板：处理站 / 未完成 / 已完成
  → 你选今天值得推进的需求
  → CC 挖掘意图、整理原始材料
  → GPT 与你参与调研、方案和设计
  → CC / 原生子代理 / Quay 实施、实验、验证
  → 每项工作的结果与有力 E2E 证据
  → 正式的日报、周报、专项汇报
```

这不是固定流水线。调研可以先实验，测试可以推翻设计，交付物也可以只有报告。
你随时新开 Session，提起某个项目或需求，Agent 就应找回相关上下文，而不是要求你重新解释全部历史。

## 核心理念

**正确的环境、上下文、记忆、能力与权限边界，在需要时出现。**

安装的能力可以很多，当前上下文必须很小。ServerOS 提供目标、已知事实、工作连续性、资源和证据；CC/Codex 决定怎么研究、实验、拆分、使用工具和迭代。

```text
默认：很薄的入口
说出需求后：相关目标、checkpoint、约束、来源
真正需要时：记忆详情、Skill 正文、MCP、原始证据
```

不是每步先找 ServerOS 批准方法，不是每次改文档先跑全量测试，更不是为了注册表完整制造工作。

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

当前拓扑：**Tokyo 主节点，Windows 专项执行节点，手机控制端，北京仅保留历史引用。**

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
