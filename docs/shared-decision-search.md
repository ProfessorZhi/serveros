# 共享决策与搜索基础设施 · 当前开发拓扑

Status: accepted user direction / target implementation plan, 2026-09-22. 本轮更新架构与实施方案，不代表已启动共享模型服务。主规范为 ARCHITECTURE.md；协议归 ability-management，业务数据仍归各项目。

## 1. 已确认的范围

用户要 Tokyo 上一套 Laya，让 ServerOS 与 MarketOps 在开发阶段共用；MarketOps 的 Docker 打包以后再做。Jev 作为兼容的远端决策 Provider 接入与对照；Serper 优先复用本机凭据和 MarketOps 已有代码。当前不是购买 GPU、建设模型平台或给每个项目各起一份模型。

```text
ServerOS 原生 Coordinator / Skills       MarketOps 应用 / 单一 MCP
                |                              |
                +----- DecisionProvider --------+
                           |
             +-------------+----------------+
             |                              |
   Laya：一个受管本地推理进程       Jev：上游托管 API 适配器
   同一 resident checkpoint       只接允许外发的数据
   无业务状态、无原始资料读取       无凭据时仅该对照实验受阻

搜索：各调用者 -> 受控 SearchProvider -> Serper -> 候选 URL
取证：URL -> 合法 HTTP / 必要时 Crawl4AI -> 原文、出处与证据
深度判断：现有 Claude / Codex GPT Provider
执行：原生 Agent / Quay / Testing Provider
权限和状态：既有 Agent Control / Work Engine / 各项目业务服务
```

共享的是模型进程和已定义的上游接入，不是合并 ServerOS 与 MarketOps 的数据库、MCP、任务队列、记忆或项目规则。MarketOps 可在 ServerOS 暂停时继续调用受管 Laya；ServerOS 不向 Laya 转交业务控制权。

## 2. 当前证据和不能沿用的推断

2026-09-22 本轮只读核查：ServerOS main 为 a4758a0，ability-management main 为 a7914d3；MarketOps 当前 GitHub 产品仓为 ProfessorZhi/marketops，main 为 9a4fe4e。旧 src/marketops 仍在早期 research-pipeline 基线，不能从它重新初始化产品。

本机 vault 的 `serper.env` 存在，含非空 SERPER_API_KEY，文件权限 0600、owner 为当前用户。**2026-09-23 已判定为 Serper LIVE**：走的是 MarketOps 的真实消费路径，不是裸 curl —— `marketops.search.serper.SerperSearchProvider.from_config(RuntimeConfig.from_env())` → `search(SearchRequest(query=…, num_results=1))`，一次请求 HTTP 成功、返回 1 条、2.31s，且 `SerperSearchProvider.__repr__` 实测不泄露 key。密钥未输出、未写盘。仍为 NOT_MEASURED 的是计费额度余量、并发上限与长期可用性 —— 按配额纪律只发一次，不据此声称；NOT_MEASURED 不等于 FAILED。MarketOps 已有 SerperSearchProvider 和统一 Settings，可复用。

前轮 Laya PoC 使用 @receptron/laya 0.1.2 / CPU、max_len=512、head_max_len=192，缓存以 main 命名，是实验产物。**该 PoC 已不是本机现状**：Laya 现在是一个受管服务，`laya-shared.service`（user unit）`active (running)` + `enabled` + `Linger=yes`，`ExecStart` 走 `providers.laya.serve --binding ~/.config/serveros/laya-shared-binding.yaml`，socket 为 `state/serveros/laya-shared/laya.sock`（目录 0700 / socket 0600）。规格以 binding 为准，不沿用 PoC 参数：`convaiinnovations/laya-multilingual` @ revision `052592a15d198d9ad47da779604259b10b47b7aa`（weights sha256 前 16 位 `9d628fd971b70038`）、`max_len=1024`、`head_max_len=256`、1 个推理槽 / 4 intra-op 线程、队列 16 全局 / 8 每消费者、`memory_max_bytes=3 GiB`、`languages=[en, zh]`。

**但服务可用不等于答案可用。**同一份 binding 的 `verified_languages` 是**空表**，`language_evidence` 逐字写着：验收只证明服务会答、且会正确地拒，**不证明任何答案是好的**。所以 Laya 的状态是「服务 LIVE、答案未经评估」，不得当作权限、状态或业务正确性的裁决器。

前轮的 4/8 线程延迟与约 1.6 GiB RSS 是小样本证据，不能当新 checkpoint 的容量保证。整机 128 vCPU/大内存也不是当前用户可无限使用的配额。旧实验还有高置信错误，所以不允许直接作为权限、状态或业务正确性的裁决器。

上游模型卡现已明确区分 English、multilingual、typed-decisions checkpoint，并提示语言、上下文和未校准过度自信风险。应读取 pin 住的实际 model/tokenizer/config，不能以包名“Laya”认为所有版本语义一致。

## 3. 最小实现形态与所有者

| 对象 | 所有者 / 初期落点 | 边界 |
|---|---|---|
| 跨项目依赖、降级、启用判据 | serveros 主架构及本说明 | 不引入每日工作第二大脑 |
| DecisionProvider 协议、reusable Laya 适配/启动包 | ability-management；无已有等价实现时放 providers/laya/ | 这是目标源代码位置，不是已注册新 ID |
| Laya 权重/tokenizer/校准配置 | 上游原始身份 + 受管模型缓存 | 大权重与秘密不入业务仓；保留上游来源和许可证 |
| CPU/内存/endpoint/认证引用/安装回执 | hwz-ops 与本机受保护 Binding | 不把本机绝对路径写进可移植代码 |
| ServerOS 客户端 | 现有 provider/ability resolution 边界 | 按需调用，不进 serverosd tick、bootstrap 或强制路由 |
| MarketOps 客户端与 DecisionSpecs | MarketOps 现有 DecisionProvider/业务模块 | 不导入 ServerOS 源树，不把业务 profile 塞进共享模型 |
| Serper 搜索与证据处理 | 复用 MarketOps SearchProvider；其他调用者接最薄适配 | 重用上游协议，不要求新常驻搜索网关 |
| Jev | TypeSafe 官方 SDK/API，薄适配 | 不假设存在可下载的自托管权重 |

协议、资源约束与验收详见 ability-management/design/SHARED-PROVIDER-CONTRACT.md；全 Ability 的实现清单详见 design/ABILITY-REALIZATION-PLAN.md。实现者要找到实际生产者和消费者，而不是再添加只有文字的 surface 字段。

## 4. 资源与可见性

初期一个服务实例、一个模型进程、一个 resident checkpoint；每任务/每 Quay worker 只能创建轻客户端。不要使用会在语言切换时重载或默认预加载全部 checkpoint 的配置。

建议初始实例预算：CPU-only、4 个 intra-op 线程、1 个实际推理在途请求、全局队列上限 16，每消费者排队上限 8；OS 级 CPU 总额先按不超过 2 个逻辑核的等效配额规划，内存先设 3 GiB 上限并实测峰值。它们是保守起始参数，不是固定性能指标；不足时先缩减问题批量/上下文，不能自动放开整机配额。线程数不等于 CPUQuota。

以实际中文+英文工作样本选择一个经过验证的 checkpoint，优先评估现有 ONNX 部署能否支持 multilingual。未覆盖的语言/超长状态应拒绝、分块或交原生推理；不静默截掉关键政策再给高置信答案。增加第二 resident 模型或 GPU 需新的容量依据与授权。

默认仅本用户可访问的 Unix socket，或 loopback HTTP 加真实调用凭据。loopback 本身不隔离共享宿主其他用户。客户端 label/tenant 文本不是身份，端点必须验证实际凭据；远端/容器网络、tailnet 发布都不是本阶段默认范围。

健康区分进程存活、模型加载、合成 readiness、业务 DecisionSpec 已验证；列明模型/配置摘要、队列与拒绝/超时。过载拒绝有 retry-after，不无限排队；取消/超时即使无法抢占 forward，也不得提前释放仍被占用的推理名额。

## 5. 使用边界

ServerOS 先用 shadow/advisory：消息相关性、资料分类、候选路由建议、注意力提示。未评测前不能按模型分数丢掉消息、静默淘汰需求、改变事实或开始变更。源证据、原生判断和确定性 policy 继续有效。

MarketOps 先用一个版本化 DecisionSpec，例如有充分出处的角色/职能相关性。Laya/Jev 可排序下一批资料或标可疑绑定；Name/Role/Email 最终归属、A/B/C/D/X、无邮箱的 RouteOpportunity、research_only/send_authorized 都由既有领域约束和证据决定。概率不是原文或授权。

shadow 接入后才对 held-out 人工审查样本计算错误类型、覆盖/拒答、质量、校准与耗时；模型版本、DecisionSpec、语言或候选标签数改变要重新验证。单一高置信阈值不自动迁移到新领域。安全关键永远不依赖 learned score 放行。

Laya 故障时可以跳过 shadow 或在原授权内用原生推理；不得自动把私有上下文发给 Jev。Serper 和 Jev 同样受出站数据政策限制：本地推理不出网，不代表后续搜索 query 可以含生产日志、个人密钥或私有消息。

## 6. 为什么日循环仍会停，以及如何解除

card=稳定语义，Work Engine=当前控制状态，Board=派生投影，已经决定，不再次要求用户二选一。历史投影与当前 store 不一致是迁移/对账问题。只读分类 controlled-with-card / controlled-without-card / record-only / stale-projection-only；旧 ACTIVE 不是今天 ACTIVE 的证据，不将未解析历史候选放入每日焦点。

只迁移有当前证据的控制对象，保留明确历史/取消/替代的处置和来源；不能改成“读取 cards 推算状态”。重新生成各项目和全局 view，保留源观察时间。field missing 不等于 false，也不等于 confirmed-live worker。

站立委托复用现有可信 actor、一次性 approval 和持久控制存储，增加必要的长期有限授权语义；不创建 grant.db、不将一次批准自动变永久通行证。只读候选建议可先生成，但真实 focus admission / 执行必须检查当前有效委托。

GPT 深审复用当前已验证的 CodexAppServerProvider 或 Codex exec 适配；recipe transports 为空只说明这一消费链没绑定，不证明机器没有模型。Quay 已有安装/POC时接真实原生任务路径，不因 cwd 下没有 .quay 就宣布不可用。

Laya 不是上述四处缺口的解决方案。它可并行部署验证，但不能成为“先把小模型装好才能对账/执行”的新阻塞。

## 7. 开发期部署与未来 Docker

现在仅共享受管宿主服务：ServerOS adapter 与 MarketOps 本地服务/MCP连接同一 endpoint，返回相同 instance/model digest。一个客户端退出不能杀掉共享模型；一个项目停机不能停止另一项目使用。

安装脚本必须经既有 release/installation 流程登记，不手改全局启动文件、PATH 或他人 service。systemd 负责有限自启/恢复；模型服务不是调度器。禁止从临时 scratch 目录运行正式服务；先核对完整权重哈希、owner 与活动引用，安全迁移到受管缓存，不删除唯一实验资料。

MarketOps Dockerfile/Compose/镜像内模型、host-network、GPU runtime 都延期。本阶段仅保留 endpoint/凭据/超时配置注入和独立 contract tests，为以后容器化留边界；不得顺手实施未来 Hosting/发送系统。

## 8. 交付状态与下一轮验收

**2026-09-22 首次成文时的状态**（保留，供对照）：本轮只提交设计与实施入口；未部署 Laya、未调用 Serper/Jev、未扩大权限、未发布业务报告。

**2026-09-23 复核后的实际状态**：

| 项 | 状态 | 依据 |
|---|---|---|
| Laya 服务 | **部署并验收** | `laya-shared.service` active+enabled+Linger；验收跑逐行过契约 §7（PASS/FAIL/NOT_OBSERVED 三值） |
| Laya 答案质量 | **NOT_MEASURED** | binding `verified_languages` 空表，`language_evidence` 自述未做质量评估 |
| Serper | **LIVE** | 一次真实付费查询，走 MarketOps provider，见 §2 |
| Jev | **BLOCKED** | 无合法绑定。按契约「没有 key 不假装调用，也不删掉集成项」，如实记 BLOCKED |
| MarketOps 侧消费 | **未接线** | `adapters/laya.py` 是进程内的；`LAYA_*` 未设 |
| ServerOS 侧消费 | **0，且原因已查明** | 见下 |

### ServerOS 侧消费方为 0 —— 这不是「少接一根线」

本轮全仓实测：`ABSTAIN` 在 `.py` 里**零命中**；`laya` 只出现在文档与目录分类表（`runtime/conformance.py`、`runtime/fs_hygiene.py`、`runtime/paths.py`）中，**没有任何读取或调用**；唯一真读「共享网关」的是 `runtime/card_source.load_binding`，而它只渲染一行字、不发请求。

语义上最接近「需要对材料做一次判定」的真实入口是 `work focus`（`runtime/cli.py:6108` → `intake/daily_cycle.py:103 select_focus`）。它**已经**做完全部可计算的部分——排除规则、排序、`limit<=2` 的容量约束都在 `intake/daily_cycle.py:103-198`，返回的 `selection_digest` 是个可复算的哈希而不是判断。它缺的是一个语义输入：`next_action`。而该字段**本机没有生产者**——`intake/focus_source.py:96` 记着缺失时缺省为 `research`；同一文件里 `daily_cycle.py` 又明文裁定「priority 必须来自被接受的板，不能来自 LLM 生成的 popularity」。

所以结论是：**消费方不存在，是因为 ServerOS 里目前没有一个由规则答不了的问题。**契约 §2 要求 DecisionSpec 由消费业务自己声明（`spec.id/version/hash`），因此一个通用 CLI verb 不是合法消费方 —— 它无法提供一份真实的声明。挂点已确定（`work focus` 的 `next_action`），等板拿到该字段的生产者即为接线点；而板 producer 目前是 `PROPOSED_NOT_APPROVED`（见 `intake/workchain-contract.yaml#card_extension`）。**本轮不据此造一个没有调用者的客户端**——那正是契约结尾禁止的「只添加只有文字的 surface」。

下一轮必须实测两个客户端调用同一 Laya 实例；中文/英文/超长/错误类型/队列公平/超时/停服降级；Jev 有合法绑定时同一 DecisionSpec 对照；一个 ServerOS 日循环真实目标与一个 MarketOps research-only 小任务分别完成。合成 gate/模型端口通不代替真实业务链。

参考（上游能力，不是现场保证）：[Laya 模型卡](https://huggingface.co/convaiinnovations/laya)、[Node 适配](https://github.com/receptron/laya)、[Jev API](https://docs.typesafe.ai/api)、[Serper](https://serper.dev/)。
