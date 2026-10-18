# Work-engine 配套合同 · ServerOS system v1

> 目标规范，不代表本文件提及的扩展均已实现。总架构：`ProfessorZhi/serveros@ef4386e21c2ed4be65ea9d538308c46925cba966:ARCHITECTURE.md`，重点参见 `docs/architecture/INFORMATION-WORK.md` 与 `ACCEPTANCE.md`。
>
> 本仓库现有 README、SQLite store、allocator、state machine、租约、finalize、federation receipt 继续是实现基础。本文不引入第二工作引擎，不改变线上数据库。

## 1. Authority 保持精确

- stable Item intent：`records/projects/<project>/items/<ITEM>/card.yaml`。
- Item/Activity/Run 状态、候选处置、授权范围、工作事件、lease：现有 work-engine 持久 store；新字段按现有版本化机制扩展。
- 源码 revision 与 PR/merge：Git/GitHub。
- 原始云对象：相应 Provider；本地只存批准范围的 locator/snapshot。
- Evidence：按 project/Item/Run 解析的 artifacts；不能仅数据库成功就宣称文件存在。
- Board、Today展示、日报输入：可重建 projection；**用户的 Today 选择与批准记录本身是持久事实，不是随时可删的 cache**。

角色根解析继续使用 explicit -> env -> device profile -> UNBOUND 的既有链，不默认北京根，不从 cwd 猜 evidence。

## 2. 所有工作共用同一模型

工作 kind 支持开发、缺陷、测试、调研、文档、支持、部署、运维、沟通、报告与决策。需要跟踪且有明确目标/交付的工作才成为 Item；简单即时答复可以不建完整 Item。

Candidate 先于 Item：只需 source、摘要、价值/紧急性依据、未知和下一步；不要求长 brief。已有 source/observation/claim/candidate 能力复用。不得用另一个 JSON/YAML backlog 偷建第二状态数据库。

用户选择或显式授权的 prioritization policy 才能把候选提升为工作承诺。相似标题不自动合并；重复 source ID 应幂等处理。Today 默认聚焦 1–2 项，但不是全系统只能两个 Item，也不能把其他在途 Item 判为闲置可清理。

## 3. Execution Envelope 与 Decision

Envelope 是绑定 Item/Activity、目标/设计 revision 的授权记录或引用：repos/paths、operation classes、数据范围、成本/时长/并发、发布/部署权限、验证要求与停止条件。

它不复制底层 ServerOS/native permissions，也不能放宽平台权限。高风险操作仍按精确 target/action/revision 准入；已批准的常规实施范围允许自主推进，不每命令询问。

目标/验收/重大设计变更使相关批准失效；无关文案修改不强制全量重新审批。Decision Packet 是带 evidence/source refs 的文档，批准事件记录谁在何时选择何方案。ChatGPT 的建议不是人类批准，GitHub push 也不等于 ChatGPT 已读取。

## 4. Activity、Run 与恢复

简单工作默认一个 Activity；复杂依赖才显式 DAG。一个 Item 可以拥有多个 repo context/test-env，禁止以 worktree、PR、native session、Quay Task 取代 Item ID。

继续复用现有 Run begin/finalize/cancel/abandon 的 actor、幂等键、状态合法性、证据与事件审计。request accepted、进程退出、Run verdict、Item DELIVERED/CLOSED 是四类事实，不互相替代。

Checkpoint 是有类型的工作记录，保存下一步、关键 refs、未完成/阻塞、执行边界和最近证据，不复制全部原生 transcript。存储/文件/云 API 非同一事务时使用现有 intent/receipt/journal 思路，使部分成功可重入且不覆盖别人产物；不额外建通用事务平台。

同一写范围 single-writer lease；不同隔离 repo context 可并行。取消工作状态不冒称已杀进程；native session control 由 ServerOS Provider 负责。

## 5. 自动推进与报告

外部 OS scheduler 触发有限 recipe，读取现有 state，选择下一条已批准 Activity。必须具备 scope/period 单次锁、last-success、预算、无进展停止、重入去重；不在本库或 daemon 增加无限 LLM loop。

输入增量 -> relevance-filtered claims -> 候选投影；已批准工作 -> 执行/验证 -> checkpoint；报告窗口 -> 已有工作差量/决定/Quay结果 -> report publication。三者共享工作事实，不相互伪造完成状态。

日报记录真实结果、里程碑、阻塞、未测范围、下一步与 Quay 使用；无进展/无 Quay 使用允许如实为零。原始录音、环境闲聊不能自动成为完成记录。

## 6. Quay 与技术支持

Quay delegation 在父 Activity 中保存真实 native Goal/PHASE/AC/Task/Run refs、输入 revision、允许变更范围、结果与证据。只登记实际执行，Task done 不关闭父 Item，查询次数不算执行次数。

技术支持复用 Source/Candidate/Item：KB检索、诊断/复现、答复、必要实施、知识修订。Support Bot code=`litellm-support-bot`，knowledge=`troublebuddy-kb-bridge`，两者不是一个 repo；本轮不得为演示而改动正在并行开发的业务仓。

Marek 开麦例外与闭麦环境录音规则由 source policy 控制；未绑定 stable source ID 不触发云删除。work-engine 只接受通过 relevance/scope 处理的引用，不自己分析旁人音频。

## 7. 必要验证

映射 SYS-14..SYS-19、SYS-23/24：候选无brief仍可入看板；选择不等于自动审批；全类型Item；默认/复杂Activity；重复Run幂等；竞争writer；设计变更使相关Envelope失效；partial publication恢复；Quay零执行/失败不冒成功；日报无进展/采集缺失不编造；state备份恢复后工作/授权/receipt仍在。

使用现有真实SQLite和文件系统测试，变更中先定向；集成/发行边界才必要全套。不得通过skip/assertion删除或弱化schema获得PASS。

本次为设计提交，不修改store/schema/runtime，也不声称本地或Tokyo测试通过。
