# CC 执行计划：原生工作环境 V2

状态：依据用户 2026-10-07 的新方向实施。Parent/Codex 负责架构与难点接收；CC 执行已明确范围的实现和批量工作。此计划替代旧计划的执行顺序，不取消旧 SYS 与项目安全要求。

## 必要输入

1. `docs/architecture/NATIVE-WORK-ENVIRONMENT.md` 当前目标。
2. `docs/architecture/IMPLEMENTATION-DELTA-20261007.md` 差距与真实代码落点。
3. 做研究交接时再读 `docs/architecture/RESEARCH-EXCHANGE.md`。
4. 旧 `docs/CC-EXECUTION-PLAN.md` 仅按子系统引用，不整本重新开设计轮。

沿用 SRV-0011，执行前读取当前 Work owner/checkpoint 和本批依赖的远端/main/安装事实。每次新的独立有限执行分配 Run；普通 grep、改文件不另建 Run。

## 批次 P0：研究交接可用入口（先做）

2026-10-07：仓库/模板/离线校验部分已由 CC 实施，Parent 补正并验收，见 research-exchange PR1/PR2 和 SRV-0011/RUN-0010。实际 GPT 研究与返回仍待对应真实任务验证；下一批工程从 P1 开始，不重复已完成 P0。

在私有 research-exchange 独立 worktree 实施 E1 的最小版本：
- 对应 packet/result JSON Schema 和短模板。
- 标准库优先的离线检查器：检查必填内容、输入版本、仓内路径、symlink/遍历、文件 digest、结果 request/input 关联和引用定位格式；不调用网络、不执行研究建议、不签发授权。
- 检查器只报告结构与绑定完整性，不能以 `data_policy.allowed=true` 一类自填标签授权传播。
- 有限 fixtures 验证正确包、错版本/错 digest、越界文件、未知请求和重复结果；fixture 明确不代表真人授权或业务完成。
- README 给 CC→GPT→结果回传的真实操作入口；读取与写回分开标 readiness。

验收：一个固定输入 fixture 经实际 CLI 验证，错误输入具名拒绝；之后按已获准的实际问题做一次研究交接。没有真实研究材料时，先交付机制，不伪造业务任务。

## 批次 P1：决定与工作 join

先读 `docs/architecture/LITELLM-SOURCE-AND-BOARD-PROFILE.md`，将 LiteLLM 工作文档与已授权聊天作为重点来源。准确读取当前版本、章节及完成标记，先对账旧认识/候选，避免已完成问题再次进入新需求或 Focus。自己的长期看板沿用源文档的优先级标题、需求说明和完成区风格，保留人工内容与正式 Work 状态的权威。

复用 semantic writer、候选表和 Work owner，完成 C1/C2 的单次决定应用：来源/认识/需求 revision 绑定，明确接受/修改/合并/拒绝，正式目标唯一映射。普通事实使用真实 standing policy/grant；一个待决不阻塞独立条目。

组合视图只覆盖三业务项目的当前工作，全局 0–2 Focus。为已有 litellm-observability 身份补 registry/owner 绑定，保留历史 LIT ID 与路径。

验收：同一真实决定重放不重复创建对象；revision 不符不覆盖；Focus 完成后可为 0；未经选中 backlog 不启动。交付方案支持实现、验证、调研和报告。

## 批次 P2：Session 资源与角色激活

先读取现有 immutable thread binding、Agent Control、leases 和 checkpoint 实现。设计并实现后继 activation，兼容 coordinator/execution；增加需要的 role intent 元数据，不造五个常驻 Session。

角色接任消费当前 Context Capsule、有效授权和 workspace/provider readiness；唯一可写 Parent 以 epoch/租约保护。角色切换不产生 grant，不混淆人机通信 thread 与 Native Session；无可靠上下文/权限隔离时创建干净 Session。

验收：已有 Session 接任对齐和 Parent；并发接任只有一方获写权；旧 epoch 拒绝；子任务结果到逻辑 Parent，不绑定死 PID；只读观察可并行。不得接管他人的忙碌 Session。

## 批次 P3：调研、生产观察与复现

连接 E1 与 B5/A9/A5/A7。能力选用保持 native-first。先检查真实 Provider operation 支持/就绪/授权；生产只读 query 有边界、预算和 receipt。

CC 提供最小既有材料；复杂联网研究可交 GPT；结果回传后 Parent 核验引用与本地版本，再形成交付方案。无 GitHub 写能力的研究入口使用已授权 writer 回传，不声称研究模式本身能 push。

验收：一条可安全复现的问题从生产或既有事实到最小本地输入，再到交付方案；不能安全复现时给替代证据与限制。认证/MFA 等人工动作只在确实不可替代时提出。

## 批次 P4：持续执行与交付

完成 E6 真实签发和受限续跑，E7 复用 evidence 生成 PR/报告/知识回写。保持收件人、受众和幂等边界。测试只覆盖本次行为和风险；发布边界才扩大回归，不重复执行未变化证据。

验收：授权跨有限 Run/Session 有效且可撤销；等待恢复不重放未知外部 effect；实现型交 PR、测试型交验证结果、报告型交有依据的报告；是否 merge/deploy/human accept 由 Completion Boundary 决定。

## 批次 P5：真实旅程与效率

选择用户实际接受且尚未交付的目标，不能制造需求或重做已完成 PR。验证同一来源/需求/Item/Run/证据/交付关联，覆盖一次 Session 接任和一个本来就需要的第三方实现。

用少量代表任务比较裸 CC 与 ServerOS 的首次有效动作、上下文/费用、人工介入、总耗时和返工。归因于系统的无收益开销要修；不削弱权限边界换取数字。

## 每批如何结束

形成可审查的差异，运行相关检查，必要时交父任务审查后合并/部署；主干、安装、加载与实际消费分别记录。没有新变化不重复全套，不把文档数量当验收。

阶段没有串行依赖时可以并行，但同一写域只能一个 worker。困难问题两次不同方法仍无信息增益，留下复现、证据、尝试和具体接手点，继续其他工作。

已授权工程不重复请求许可；真正需要人承担的取舍、权限、生产效果或无法替代的输入，先准备具体可审核包，仅暂停依赖它的操作。

## 给执行 CC 的启动词

从本计划接续，不重做已完成的 reader、receipt、Work owner 和目录审计。先完成 P0，随后按依赖推进 P1–P5。新能力首先复用已有工具和 owner；任何拟新增接口必须在变更中明确声明，不假造既有 CLI。保护所有活动工作、秘密、原始生产日志和他人 WIP。阶段交付成功不等于 ServerOS 全部 COMPLETE。
