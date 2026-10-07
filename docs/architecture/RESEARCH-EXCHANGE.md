# CC、GPT 与生产证据的研究交接

状态：2026-10-07 目标协议；当前实现状态见 IMPLEMENTATION-DELTA。本文从属于 [目标架构](NATIVE-WORK-ENVIRONMENT.md)，不创建研究平台或第二个 Work owner。

## 1. 默认协作

CC 准备既有事实与最小问题包；Native Agent 可直接进行普通联网和上游调查；复杂且确有收益的部分交 GPT 深入研究。Parent 接收结果，核对引用、实际版本及与本地事实的冲突，再形成交付方案供必要的人审。

研究问题不是“请读完整个仓库想想怎么做”。它应有明确的待决问题、已知条件、已经排除的假设、需要的输出和停止条件。一次研究只回答当前问题，结果不自动获得执行权。

## 2. 仓库、目录与 owner

私有仓库：`ProfessorZhi/serveros-research-exchange`。

```text
README.md                              入口与使用边界
contracts/                             交接格式的版本化实现
  research-packet.schema.json           初版已实现：输入结构
  research-result.schema.json          初版已实现：结果结构
templates/
  packet.json                          空模板，不是可执行请求
  brief.md
  result.json
  report.md
packets/<project>/<request-id>/
  packet.json                          这个输入版本的 manifest
  brief.md                             给研究者的短入口
  inputs/                              明确允许分享的必要材料
  results/<result-id>/
    result.json                        对应输入 commit/digest、覆盖与限制
    report.md                          带引用的结果
```

路径是新交接实现的约定，不声称已有读取器。请求 ID 由 Parent 在已有 Item/Run 下分配，本仓不分配 Work ID。输入由 CC/Parent 写；GPT 或受托回传者只写本次结果目录/PR；父任务接收、业务决定和 Outcome 仍写原 owner。

输入以完整 Git commit 固定；后续修订形成新 commit，并明确是否使原研究失效。结果必须引用实际读取的输入 commit，不能只说读了 main。不同结果保留不同目录，不覆盖他人结果或已登记证据。

## 3. 最小输入契约（v1）

`research-packet.v1` 的内容：

- `request_id`、`project_ref`、`item_ref`、`run_ref`、`objective`。
- `questions`：可以回答/反证的问题；`non_goals`：明确不做的事。
- `input_revision`：发布请求时的完整 commit（避免把自身 commit 写进自身文件的递归：由交接信封/结果携带；manifest 可为空待发布绑定）。
- `inputs[]`：仓内相对路径、字节 SHA256、用途；拒绝绝对路径、`..` 和 symlink 逃逸。
- `source_refs[]`：原始 locator、准确版本/时间、实际覆盖与缺口；外部网页只有参考链接不表示已经读取。
- `known_facts` / `hypotheses` / `ruled_out`：事实、推论和排除项分开，均链接输入或证据。
- `requested_output`：研究报告、方案比较、复现设计或测试设计；允许“证据不足”的有效结论。
- `limits`：实际绑定的时间、模型/工具调用、费用、网络来源和输出预算。数值由此次委派确定，不从示例推断授权。
- `data_policy`：材料分级、允许受众/目的、传播授权引用和到期/保留要求；标签本身不授予传播权限。
- `handoff`：接受结果的 Parent/Item、目标仓库和结果前缀、所需审核面。

先提交最小 brief 和索引，细节按需读。既有代码应优先用准确 repo@commit:path 引用；只有连接器看不到且传播已获准的必要片段才物化进 inputs，不复制整个业务仓库。

## 4. 最小结果契约（v1）

`research-result.v1` 包括：`request_id`、`input_revision`、`result_id`、`status`（COMPLETE/PARTIAL/BLOCKED）、逐问题回答、来源引用、反证与不确定性、推荐和替代、复现/实验建议、未覆盖内容以及实际 Provider/能力信息。

任何结论要区分外部来源陈述、本地测量与研究者推论。上游源码结论引用 repo+commit+path，issue 状态带读取时间；官方材料优先，但与当前本地版本不一致时需说明。不能把上游修复已合并推断成当前部署已包含。

结果文件不包含人类审批、Work 转换或自动扩大预算。GPT 建议的命令作为未执行文本供 Parent 检查，不能下载报告后直接当 shell 执行。

## 5. 三种实际接入方式

| 路线 | 入口与职责 | 读取/写回要求 |
|---|---|---|
| Native Agent 普通研究 | CC/Codex 在既有授权内使用当前网络工具、官方资料和上游仓库 | 直接形成同一结果格式；简单问题不用先创建 GPT 任务 |
| ChatGPT 网页/桌面研究 | CC 准备短启动词、问题包 commit 和连接器/附件；人按当前客户端能力启动研究 | 先验证该入口能读指定私有仓库的指定版本；写 GitHub 是独立能力，不从“能研究/能读”推断 |
| 可编程 Research Provider | 在实际 API、模型、工具、费用和数据政策均绑定后，由有界 Run 发起 | 可自动轮询/接收完成事件、保存结果并由 GitHub writer 提交；不是让 serverosd 运行研究模型 |

如果研究入口只有读取能力：GPT 返回文件/报告，由已有可写的 CC/GitHub Provider 导入同一个结果路径并创建 PR。用户最多承担客户端必须的人工作用，不需要复制整段历史。不存在可靠返回通道时标记 `WAITING_FOR_RESEARCH_RESULT` 并继续独立工作。

连接器可能异步索引。用 commit/digest 与实际文件内容核对，不能把“仓库已连接”当成“最新材料已被研究者读到”。下载包/附件是明确回退方式，生成后记录同一输入版本与传播范围。

**截至 2026-10-07 的平台依据**：官方说明支持在研究入口加入文件/已连接上下文，但可用性依账户与 workspace；GitHub 的读取与写入需分别测试。API 的旧 Deep Research 指南已标旧型号退役，因此实现不得照抄其旧模型 ID；选择当前真实可用的模型/工具组合并独立验证。[研究入口](https://learn.chatgpt.com/docs/web-search)、[GitHub 连接与动作验证](https://learn.chatgpt.com/docs/enterprise/shared-connections)、[API 指南及迁移说明](https://developers.openai.com/api/docs/guides/deep-research)。

这是能力依据，不证明用户的 ChatGPT 研究会话已经获得此仓库访问权。需要在实际入口做一次明确的读取，以及适用的写回验证。API 研究和网页研究分别记录 readiness。

## 6. 生产查询与本地复现

生产观察先由 A9 的已绑定 Provider 执行，目标/时间窗/字段/输出字节/行数/耗时/负载均有限。Windows 企业网络或跳板机凭据留在相应设备。没有权限时不尝试绕过，形成具体请求；正常已授权查询不逐条打扰用户。

分层证据：

1. 生产原始证据留原系统或获准受控存储；记录采集条件、覆盖与缺口。
2. 本地复现取脱敏/合成的最小输入，固定代码、配置、依赖和环境条件；缩小环境时收窄结论。
3. 外部研究只取已获准传播的摘要、聚合、最小片段和源定位。私有 GitHub 仍是外部传播面，不自动允许上传公司原始资料。
4. Parent 将 GPT/上游结论与本地复现交叉核对，形成解释、修复方案或无法复现的限制。

严禁 raw prompt/response、完整日志/ES export、凭据、会话 Cookie 和生产授权信息进入研究库。受限生产地址、人员数据、商业资料按现有数据策略处理；证据需要定位时可用受控 locator，不把原文搬入 GitHub。

复现需要人工的情况限定为：账号登录/MFA、真实用户感知或现场操作不可替代、缺合法数据/访问、会产生未授权外部效果。其余应先尝试可验证的自动复现。不能复现不等于“不存在”，也不能强造 Before。

## 7. Parent 接收与容错

接收顺序：输入版本与 request 对应 → 文件路径/大小/格式有效 → 引用能定位并支持关键结论 → 与现有事实/权限/交付范围比较 → 接受、部分接受或提出具体补充问题。不要重做整份研究。

运行失败可恢复到同一 request；发送、写回和创建 PR 使用稳定幂等身份，响应未知时先查实际远端。过期结果可以留作历史，不能直接应用到新的目标 revision。并行结果冲突形成待决事项，不取“最后写入者赢”。

源撤权后停止新增外传及未来读取，历史按保留政策处置；不要自动删除受托资料或已被引用的结果。只有得到接受的结论才进入 records 当前认识，结果 repo 的 PR merge 不代表正式业务决定已接受。

## 8. 最小验收

一份确有研究价值的实际材料包，通过实际 GPT/Research Provider 读到指定版本；返回带来源的结果；由独立 writer 或该会话获准的 GitHub 写能力写入；Parent 接受后形成交付方案。验证重复返回不会产生重复 Work/决定/PR。

无研究需求时不制造一次深研或新业务 Item。协议 fixtures 可以验证结构与权限负例，但必须标机制验证；完整 LIVE 需上述实际链。首次阶段可以交付 repo、模板和导入/检查工具，并明确研究入口尚未绑定。
