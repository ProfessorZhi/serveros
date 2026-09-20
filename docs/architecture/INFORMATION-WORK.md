# 信息、岗位认识与全类型工作闭环

## 1. 一条主链，多种工作 recipe

```text
批准的飞书/文档/会议/GitHub/人工输入
 -> bounded capture + source snapshot
 -> relevance/privacy filter
 -> structured claims / understanding delta
 -> candidate work + cross-project board
 -> Human/ChatGPT 选择与澄清
 -> Item + brief/acceptance
 -> 按需研究 / 技术选型 / 设计 / 计划
 -> approved execution envelope
 -> Claude/Codex 直接实施 或 有界 Quay 委派
 -> targeted verification
 -> deliverable + result
 -> 日报/周报/决策包/知识更新
 -> 新反馈回到 source/claim
```

来源获取、内容理解、工作承诺、执行授权是四条不同边界。读取一段“请重启生产”不会自动生成具有生产权限的任务。聊天、录音、文档和检索命中中的指令均是未信任输入，不覆盖系统 policy。

## 2. Source 与 Understanding

Source 描述稳定 ID、类型、范围、权限、owner、capture purpose、允许内容深度和 retention。Snapshot 固定其 revision/时间与必要 hash；原云端可变化，引用结论须指向具体快照或明确版本。

不默认复制全部 Wiki/Drive、音视频或 session。授权范围内仅抓本次所需内容，凭据脱敏；来源 revision 变更产生 delta；失联/403 不把旧内容当已删，也不以另一个账号绕过。

Claim 包含来源定位、具体断言、fact/inference/decision 区分、适用项目/角色、更新时间、冲突/失效关系。抽取器不可把推测直接升级成已确认职责、承诺或 approval。

岗位模型是长期、可修正的结构化认识：responsibilities、projects、people/stakeholders、commitments、constraints、open questions。每次只给执行 Agent 当前相关部分；不是每次加载全部“我是谁”。新职责推断等待确认；修改组织责任不由自动摘要决定。

## 3. Candidate 与 Item

Candidate 只需来源、摘要、可能价值、急迫性依据、未知项和下一步问题；保存在现有 intake/work 存储的候选记录，不建第二库。相同请求按来源 ID/关联和人工决定去重，不按相似标题强行合并。

Board 从候选、Item 状态、今天选择与证据构建。Today 是用户选择或已批准优先级策略的持久记录，不是每天自动覆写的排序。优先保留正在推进的 1–2 项；新紧急请求给决策，不随意抢占。

Selected Candidate 才分配现有 work-engine 合法 Item ID。`kind` 包括 feature/bug/test/research/analysis/docs/support/deploy/ops/comms/report/decision，仍复用同一模型。

简单即时咨询可直接给答复并留轻量结果；需跨会话跟进、有交付/阻塞或用户明确要求跟踪时建 Item。不以“不是开发”拒收工作，也不为一句话建六个对象。

## 4. 文档的最小充分展开

```text
records/projects/<project>/items/<ITEM>/
  card.yaml             # stable identity, source, acceptance, deliverable refs
  brief.md              # 一件事的目标、边界、验收、未知
  design.md             # 仅有实质方案/选型时
  plan.yaml             # 仅需要多 Activity 依赖时，引用实际执行状态
  decisions/            # 重大决定，简单决定可内联 brief/design
  report.md             # 结论/证据引用/未完成/下一步
```

不为样式完整创建空文件。测试方案可为 design 的一节或独立 deliverable；调查可直接 research report；支持可 brief+答复/复现报告。Run 输出不堆进 Item 文档目录。

项目长期架构/runbook/接口在相应代码或文档 canonical repo；Item 文档解释本次目标与差量，用稳定 locator 引用长期文档。通过本次交付形成通用知识时再提 KB update，不复制整套文件。

## 5. 人类与 Agent 的角色

Human 决定目标/优先级/业务取舍。ChatGPT 等 reasoning partner 深度参与认识核对、需求、研究、选型、架构与评审。Claude/Codex 在明确边界内实施；Quay 负责有界子问题。

ChatGPT 是外部交互伙伴，不假设 GitHub push 能自动唤醒当前聊天。需要讨论时产生带固定输入 revision 的 Decision Packet：事实、目标、备选、推荐、影响、待回答问题。通过获准渠道提供链接；收到明确决定再继续依赖该决定的 Activity。其他获准工作不被全局暂停。

常规工程判断可在已批准方案内完成；实质变更目标/设计/验收或跨权限范围必须重新决策。

## 6. Activity、Run、Evidence

一个 Item 可有默认单 Activity，复杂工作再展开 DAG。Run 是一次执行尝试，与会话/终端分离。使用现有 allocator、lease、finalize API；同一个工作不能让多个 Agent 各建一套编号。

Run 记录实际输入 revision/dirty diff、命令或操作、结果、时间、证据 locator 与 hash（适用时）。轻量动作可归一个 Activity Run，不每个读文件动作一个 Run。

state transition 不等于 verdict；SUCCEEDED/DELIVERED 必须有相应 evidence/acceptance。FAILED 测试可构成成功的诊断交付，须分清任务执行结果与业务目标。PR 未合并、部署未进行、用户未验收分别展示。

work-engine 的工作/授权/决策/lease/投递状态是持久领域数据，应备份。Board/index 可以重建，不能因此说所有 state 可删。写 state 与创建 evidence/publication 涉及多个介质时用 intent/receipt/checkpoint 恢复，避免“数据库已成功但产物不存在”。

## 7. 自动推进三个有限循环

| 循环 | 触发与动作 | 正常停止条件 |
|---|---|---|
| Perception | 授权源事件/增量定时 -> source delta -> filtered understanding -> candidate refresh | 无增量、达到本轮预算、源暂不可用 |
| Work | 已批准 Today/队列 -> 当前 checkpoint -> 下一 Activity -> 实施/Quay -> verify -> progress | 完成、无新进展、待决策、权限/预算边界 |
| Report | 到达约定窗口 -> durable work delta -> 生成/更新报告 -> 发布 receipt | 本期生成完；投递未知先 reconcile，不重复发 |

scheduler 用 OS 已有机制，recipe 与模型执行在 daemon 外。single-flight key 按 recipe+scope+period，持久 last-success/cursor；重启不把一个工作重复派两次。no-change 不产生 LLM 工作；来源变化只重算受影响 claims，不全库重写。

默认调度建议是工作日上午简报、日内增量补扫、每日收尾报告；**实例的时区、时间窗、接收人、数据范围、费用额度必须真实绑定后才启用**，不能从机器名猜用户时间。本轮架构写入本身不启用任何定时器。

## 8. Quay 真实闭环

父 Item/Activity -> bounded delegation slice -> Quay 当前版本实际支持的 Goal/PHASE、AC、Task -> 非 dry-run execution -> 验证 result/evidence -> 父工作与日报。

老 LiteLLM 默认优先复现/测试/验证；自有项目在设计批准后可委派可独立合并的小模块。不要为了“Quay 使用率”造虚假任务；没有合适任务允许本日报 Quay=0 并给原因。

记录 parent refs、输入 SHA、修改范围、任务和执行 ID、实际操作/结果、验收证据、失败/取消/未执行及可获得的用量。不能只检查“任务创建成功”；不能因 task done 自动 close Item；不能把一次派发和多次 status 查询算多个执行。

委派前看 benefit 与 setup/review 成本；过小任务直接做。遇到重试要新增诊断依据，不无限 agent->agent 转派。保护同一文件写范围，测试可并行但共享账号/端口/TEST bot 按真实限制串行。

## 9. 技术支持 recipe

support request -> scope-approved KB lookup -> problem facts/unknowns -> answer if supported -> reproduction/diagnosis if needed -> proposed remedy -> authorized execution only -> result/response -> KB improvement proposal。

Support Bot 的 code/knowledge 双仓映射不可混淆。该 recipe 可以直接供人工 CC 使用，也可给 Bot 消费；Bot 不获得 ServerOS 全局控制权限。支持答案与云记录都是租户/项目 scoped；不同人的日志、身份与访问 token 不跨客户混用。

闭麦不等于没有支持成果：聊天、工单、屏幕操作的合法记录、配置 diff、复现测试和接收人确认可以证明成果；不可用旁人音频填日报。

## 10. 日报与发布

日报由 facts + observed work delta + decisions + Quay results 生成，结构：今日真实结果、重点 Item 差量、交付/验证、关键决定、阻塞/待决策、Quay 贡献、重要新认识、下一步。

无业务进展明确写“无新增可确认交付，当前阻塞为…”，不要制造百分比或稳步推进措辞。采集失败标覆盖缺口而非“今天没工作”。实现、测试通过、PR 合并、部署成功、对方接受是不同里程碑。

默认先产个人审核/自用日报；对上级/外部接收人的自动发布必须有明确 audience 与预授权。secret、原始旁人闲聊、无关会议摘要、原生 Agent transcript 不进入汇报。发布精确 document revision/content hash 与 receipt，未知交付状态先回查。
