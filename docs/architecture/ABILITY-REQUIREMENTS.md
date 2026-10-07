# 21 项 Ability：最终需求与验收合同

2026-10-08。本文定义必须达到的产品行为，不声明当前实现全部完成。产品词表以 ability-management 的 `product-tree.yaml` 为准，artifact 身份以 `abilities-manifest.yaml` 为准；本文不新增 registry。总架构见 [Native Work Environment](NATIVE-WORK-ENVIRONMENT.md)，实施次序见 [唯一执行计划](../plans/plan-01-native-work-environment.md)。

## 共同要求

- 实现可由 Native Agent、原生工具、共享代码、Skill、MCP、Provider 和 Core 组合承担；21 项不要求 21 个 Skill、服务或阶段。
- 按 Support、Readiness、Authorization、Execution mode 分别回答。目录存在、metadata 有映射、组件已安装都不自动证明可执行或已验收。
- 每项区分 CODE、DISTRIBUTED、DISCOVERABLE、CONSUMED、JOURNEY_VERIFIED；观察带范围、版本、时间和证据。未测不能当失败，也不能当通过。
- 公共契约和负例使用少量相关测试；实际消费按有意义的真实任务覆盖。一个旅程可覆盖多个职责，不为每项制造新业务事件。
- 清楚说明输入、输出与接收 owner。下游实际接收并使用才算交接；报告、卡片和 Memory 不替代正式 owner。
- 当前工作所需信息按需读取；不在所有 Session 预装全部正文。普通已授权原生工作保持直接路径。

## G1 Ability Governance

**要求**：发现、来源追溯、身份、许可、版本、兼容性、采用、分发、安装、更新与退休由同一 registry 管理。采用时选择最薄实现：USE_NATIVE / ADOPT / WRAP / COMPOSE / FORK / BUILD / REJECT。第三方原件默认保持原样。

**接缝**：需求/任务缺口 → 少数候选 → 所选实现与 Binding → 实际消费结果 → 必要的能力改进。product_role 表达职责，Thin Link 表达条件和交接，不产生权限。完整产品树与已安装导航分开。

**验收**：一个本来就需要的能力能按需找到并被调用，结果被 Parent 采用；来源不明准确标注，不默认信任执行。canonical、Node 产物、客户端安装和当前加载分别说明。未进常驻 composition 不算缺能力；registry 未观测到也不等于磁盘未安装。

## A1 Session Continuity & Handoff

**要求**：工作身份独立于 PID、tmux、聊天窗口。Session 是资源，可绑定对齐、协调、Parent 或 specialist 角色；角色变化不扩大权限。上下文不适合或客户端不能可靠切换时创建干净 Session。

**接缝**：已有 owner/checkpoint → 后继 activation/epoch 与租约 → 最小 Context Capsule → 接收在途结果 → 下一动作。复用 Agent Control，不原地改不可变旧绑定，不另建 Session DB。

**验收**：本机真实任务换 Session 后继续，旧 epoch 不能写、未知 effect 不重放。受限 Parent 实际能调用自己的 inspect/accept/checkpoint/park 等控制面；工程执行另有真实边界。本机验收不依赖第二台设备；旧 launcher 失败不能代替当前版本验证。

## A2 Memory Retrieval & Correction

**要求**：检索当前认识、正式工作、历史证据和经验；当前 owner 与更正结果优先。来源撤回可立即阻止旧 Claim 当事实召回，不自动取消已接受业务决定。

**接缝**：Claim/Work/source 版本及可用范围 → 检索 Provider → 当前有效性和权限检查 → 有出处的上下文。MetaCC/OpenViking 是 Provider，不拥有正式业务状态。

**验收**：跨 Session 召回有效；一次真实更正/撤回后旧事实不再被当当前结论；Provider 陈旧/断线有明确降级。跨项目权限在可信读取边界执行，不能以一个未被服务端执行的 peer_scope 参数宣称隔离。

## A3 Workspace / Git / PR

**要求**：解析正确仓库、项目和路径，使用隔离 worktree，保护并发 WIP，完成版本与 PR 交付。Git 分支、安装 revision、运行 revision 分开。

**接缝**：Item/仓库 Binding → 写入域与 owner → 实施/检查 → diff/PR → 合并与后续安装。目录操作遵守 [目录实施合同](DIRECTORY-CONVERGENCE.md)。

**验收**：一次实际交付不污染 canonical 或他人修改；路径有归属、引用可解析。mtime/目录名/作者名只能作线索。新产物落位正确，未知历史对象保留；不以 worktree 数量判垃圾。

## A4 Runtime & Resource Management

**要求**：管理已授权范围内资源身份、owner、健康、资源预算、lease 和安全维护条件。Session/worker 可归因但不重复计算 CPU/RAM。OS supervisor 负责进程监督。

**接缝**：声明与实际观察 → 期望差异 → 功能/资源状态 → 维护或恢复的正确 owner。`serverosd` 提供节点事实和机械协调，不做业务 planner。

**验收**：能区分进程存在、功能正常、未观测和声明不完整；当前状态能追溯到实际采样。R1 恢复仅消费已有授权与次数/资源预算；观测异常不获得 kill/restart/cleanup 权限。不为全绿伪造 FUNCTIONAL probe。

## A5 Fleet / Device Handoff

**要求**：依任务选择具有所需网络/工具的设备，保留原 Work owner。凭据留在其网络与身份边界内。远端可以是结构化 Provider，不必复制完整 Node Core。

**接缝**：目标/操作 → device/provider Binding → transport readiness → 有界请求与 receipt；需要远端 Agent 时才创建远端 Run。

**验收**：至少对实际所需路线取得真实返回或精确失败位置；inventory UNKNOWN 不等于 transport 不通。本机接续不被跨设备条件冻结；Windows 未登录造成 Provider 不可用，不被报告为生产故障。

## A6 Delegation & Parallel Execution

**要求**：Parent 委派有价值的有界子问题，明确输入、输出、范围、预算和接收方。原生子代理、Quay、其他受支持 Provider 均可实现。

**接缝**：子任务创建 → 实际执行 → 结果/失败/receipt → Parent 接收与核对 → 主工作继续。控制面和 native 直接委派须说明各自实际路径。

**验收**：一项真实子任务结果被 Parent 采用；重复投递不重复执行或接受，错误 Item/epoch 被拒。child DONE 不关闭 Parent Item，Parent 不重做所有子任务。某子任务等待不阻塞无关工作。

## A7 Automated Testing & Acceptance

**要求**：围绕 AC 和风险选择最少但充分的验证。行为修改优先有意义的安全 A/B 或等效回归与对应用户路径；无法建立对照时明确替代证据。人可见变化保留适用的截图。

**接缝**：AC/测试方案 → 环境 capability/lease → TestRun → evidence/verdict → Item 验收。环境 owner 不拥有 verdict；PASS 不自动关闭工作。

**验收**：结论能回到实际输入、baseline/candidate、环境与观察；无变化复用有效证据。LiteLLM 完整容器验收实际启用 PG、Redis、多 worker、诊断日志、DB-backed 管理员模型维护且整个 lease ≤10 GiB。窄复现的结论不越界。

## A8 Build & Release

**要求**：固定源码与依赖，生成可识别产物，支持候选验证、安装、真实加载、回退。各客户端的安装面分别核对。

**接缝**：source revision → artifact → installed pointer → loaded consumer → 功能读回。不同 kind 的验证器按自己的 payload 合同工作，不要求不同安装形态的整个目录字节完全相同。

**验收**：实际消费者使用目标版本，previous 可恢复；当前符号链接不证明旧进程加载了新代码。活跃缓存不原地覆盖，候选可先准备，切换依实际客户端支持实施；不因某客户端暂不能切换停止其他开发。

## A9 Production Observation

**要求**：对指定生产目标获取有界事实，分清 observed fact、supported inference、unresolved hypothesis。限制对象、时间、输出、负载、敏感字段与证据保留。

**接缝**：问题 → 合法只读 Provider/跳板 → 目标版本/日志/请求或用量 → 事实与限制 → 调研、复现或支持结论。

**验收**：证据确实来自指定生产目标。东京开发机的资源监控不能替代本职责；merge/image 存在不等于运行生产已部署。真实模型调用可能计费并写 usage，另需测试身份、授权与预算，且不是所有验收的前置。

## A10 Production Change

**要求**：准备精确变更包、前置条件、执行者、窗口、停止条件、回退及变更后验证。受保护生产动作继续遵守 HUMAN_EXECUTED 策略。

**接缝**：已接受目标/方案 → 可审阅变更包 → 正确执行者 → 实际 receipt → A9/A7 后验与业务结果。

**验收**：变更包和机制可先验，真实变更到来后再验该事件。不制造生产切流来证明能力，也不把“人执行”判为产品缺陷。方案批准不自动允许 Agent 代执行。

## B1 Source Intake & Follow

**要求**：增量捕获获准 IM、文档/Wiki、会议及相关引用，保存身份、准确版本、读取范围和 gap。重点 LiteLLM 工作文档优先获得必要阅读预算。

**接缝**：Source/Asset → snapshot/manifest → SourceReadingPacket → B2。采集、阅读准备、语义接受水位分别维护；迟到/编辑/撤回更新受影响范围。

**验收**：同版本不重复采算；失败不推进成功水位；一个源失败不冻结其他独立源。富文本完成信号结合正文解释，背景语音不自动成为业务 Claim。新路径/深度策略保持旧来源版本可追溯。

## B2 Structured Understanding

**要求**：Atomic Claim + people/projects/topics/responsibilities Current View，支持事实、推论、提案、决定、冲突、更正、撤回。语义写入复用唯一 writer。

**接缝**：固定版本 packet + 当前认识 → reading → 有效政策或真实决定 → semantic receipt → 当前视图/A2/B3。

**验收**：至少一项实际语义变化被合法提交并消费；prepared 与 accepted 明确分开。普通事实按已授予政策处理，重要含义、责任或目标变化交人。source/card 版本冲突不覆盖，已解决问题不被重复生成新需求。

## B3 Requirements

**要求**：描述长期有意义的目标、scope/non-goals、AC、交付边界及来源。草稿可以接受、修改、合并、拒绝；实施步骤不各变成需求。

**接缝**：理解变化/直接要求 → Candidate/需求 → 真实接受 → 唯一 canonical Item。已有目标 revision 更新同一责任对象。

**验收**：同一决定重放不重复建 Item；完成标记与解决范围对账正确；没有被接受的新目标不自动启动。优先级可以缺省为未声明，不从 state/kind 猜 P 值。补卡不自动接受目标或加入 Focus。

## B4 Work Management & Admission

**要求**：管理唯一工作责任、全局 0–2 Focus、Activity、Run、等待、准入和 Outcome。保持选中工作接续，完成后不自动补 backlog。

**接缝**：被接受目标/当前选择 → 有效范围和环境 → Parent/Run → 验收与交付 → Outcome。正式 records 读面必须能提供实际内容，不能只解释 MISSING。

**验收**：一项真实工作从接受/选择推进到适合的 Outcome，等待和恢复不丢责任。已选目标不因 priority 缺失停摆；feedback 为空不阻止完成。Evidence 拒绝不存在的 Item/Run、跨归属或非法 locator，重放/冲突可区分。

## B5 Research & Evidence

**要求**：围绕真实问题收集实现、生产事实、官方/上游资料、实验与反证，明确结论适用范围。Native Agent 可直接检索，不强制付费研究 Provider。

**接缝**：问题及最小材料 → 原生或选定研究能力 → 引用/证据/不确定性 → Parent 采用。GPT 研究交换使用已获准材料和固定输入版本。

**验收**：实际研究结果影响设计/验证/报告；没有 timer/MCP 不代表没有消费者。交换仓库校验器只证明结构/版本绑定，真实 provider 读取、回传与引用核对分别说明。

## B6 Solution Design

**要求**：给出得到可用结果的方法、关键替代、模块/数据边界、兼容性、风险和验证停止条件。实现、验证、研究、报告或组合均合法。

**接缝**：事实与约束 → Native Agent/按需 specialist → 交付方案 → 重要取舍由正确 owner 决定 → B7/A7。

**验收**：方案被实际工作采用；简单任务可只有短说明。不得为了字符串 IMPLEMENT/VERIFY 等编一个强制选择器，不强制每次设计文档或专家 Skill。第三方建议不产生业务批准或权限。

## B7 Implementation Delivery

**要求**：在已接受范围内实施、实验和修复失败，产出可验结果；原生编码就是合法实现方式，不要求常驻 implementation Skill。

**接缝**：交付方案 → 正确代码/环境/写域 → 变更与产物 → 验证/验收 → Parent/Work owner。

**验收**：真实目标得到可使用结果，未扩范围、未污染并行工作。ServerOS 自身发版只证明 ServerOS 的对应任务，不能当作 LiteLLM/MarketOps 的交付验收。

## B8 Reporting

**要求**：从已有接受结果与证据生成适合受众的专项/PR/日报/周报；内容、排版与投递分别处理。

**接缝**：Item 验收/TestRun/已发生事实 → 报告 → 目标受众与发布接口 → receipt。报告不能重新判测试，生成失败不改变原 verdict。

**验收**：至少一份真实交付报告可追溯到对应工作结果；重复发送幂等。对齐待决简报不能充当已交付 Item 报告。不要为写报告再跑测试或制作多份事实副本。

## B9 Document / Knowledge Stewardship

**要求**：按 OWNED / EXTERNAL_MAINTAINED / FOLLOW_MIRROR 维护受托文档，明确 body_authority、sync_mode、write_policy 与风格。稳定 profile 与派生状态分离。

**接缝**：已有维护关系/内容变化 → base/current/desired 与局部 patch → 条件写或 proposal → readback/冲突处理 → 正式记录。

**验收**：实际需要维护的文档完成一次合法局部更新或有内容的 patch proposal，人工区和受众不变。非法 profile、缺字段和旧 namespace 注释先作工程核对，不能直接变成人类决策门槛。不能为了测试制造空文档或无意义写入。

## B10 Human Control & Notification

**要求**：集中呈现认识/需求、方案取舍、交付与必要授权。身份可信与具体批准意图分别验证，绑定目标、版本、动作和预算；不是任何一条真人消息都能批准任意操作。

**接缝**：实际待决或状态变化 → 正确人类入口 → 可验证决定/授权 → 对应 owner → 状态/投递回执。长期委托的寿命与身份签发时新鲜度分开。

**验收**：用户作一次具体决定后对应范围能推进，到期/撤销确实阻止；受限执行域不能修改信任根或取得签名私钥。播报按实例中国工作日日历，一天一张卡、更新通知幂等；事故按 episode。390px 首屏可辨对象、情况、影响与动作，内部细节折叠；无新观测不等于恢复。

## 联合验收包：复用同一条真实旅程

| 场景 | 覆盖 | 完成证据 |
|---|---|---|
| J1 来源到真实决定与 Focus | B1/B2/B3/B4/B10 | 精确版本、合法语义/需求转换、唯一 Item、选择重放幂等 |
| J2 本机接任与有用子任务 | A1/A2/A3/A4/A6/B4 | 后继 Session 真继续、结果被采用、旧 epoch 拒绝、未知 effect 不重放 |
| J3 实现或验证到交付 | A7/A8/B6/B7/B8/B9 | AC→实际证据→适当 PR/报告/文档→Outcome，不强迫无代码任务写代码 |
| J4 必需的外部能力 | G1/A5/A9/B5 | 真实 Binding/调用/结果接收；缺通道明确范围，不用本地监控替代 |
| J5 目录与效率 | G1/A3/A4/B9 | 新产物符合落位、消费者读对、历史保留合理、少量可比任务结果 |

A10 按真实变更需求及既定人执行策略验收，不强制造事件。场景名是本次实施验收组织，不是新 CLI，也不是每个日常任务必经的五步。
