# Priority-contact Deep Archive · 项目对接人的完整工作资料闭包

状态：2026-09-23 用户确认的产品策略；本文件定义 ServerOS/B1 如何解释“项目对接人深归档”。它不会自动执行历史回填，也不会把所有群聊/附件都扩大为可读、可外发或公开资料。当前实现与数据差距来自只读 GitHub 检查，真正 materialization 仍需后续 B1 实施与授权运行。

## 1. 为什么需要一层明确的 DEEP_REQUIRED

默认 SHALLOW 对一般工作沟通合理：保存消息、版本、线程和附件/链接 locator，按任务需要取具体材料。但项目对接人的聊天往往本身就是需求、验收、截图、SOP、转发上下文和决策的长期来源。

用户明确要求 Bruce.Xu、Nature.Hu、Zhengji.Zhang 等项目对接人的聊天记录深归档，并包含他们**转发消息里的文件/图片等材料**。这不是“每次碰到附件再判断”的临时取证，而是这些来源的长期捕获策略。

因此 B1 需要三种稳定 capture policy：

- **NONE**：明确 bot/app/test 黑名单；只登记身份/理由，不读正文。
- **SHALLOW**：一般真人工作来源默认；消息正文+版本/thread+材料 locator。
- **DEEP_REQUIRED**：用户指定或项目关系确认的关键对接来源；消息和规定的第一层材料闭包必须持续物化。

DEEP_REQUIRED 是采集政策，不是工作授权、发布授权或“所有内容都送入模型”的许可。

## 2. 谁进入优先联系人策略

策略以稳定 source identity/关系为准，显示名只用于人读。当前用户确认的种子例子包括 Bruce.Xu、Nature.Hu、Zhengji.Zhang；实现时从现有 Feishu manifest/people/project 关系解析实际 open_id/chat_id，不把名字硬编码进同步逻辑。

优先级来源：

1. 用户明确指定“这个人/这个会话必须完整留存”；
2. 某项目的主要需求/验收/运营对接人被正式记录为 priority counterpart；
3. 明确的项目小群被登记为 authoritative working channel。

默认作用域：

- **与 priority counterpart 的 p2p 私聊：DEEP_REQUIRED**。
- **明确登记的项目小群：DEEP_REQUIRED**，例如已知项目对接小组。
- **大型通用群/部门群：仍 SHALLOW**；某关键联系人在群里不自动触发整群 deep。
- **bot/app/test：继续 NONE**，除非用户明确将该具体会话重新分类为 source。

关系变化不会自动删除历史。某人不再是项目对接人时，新消息可降为 SHALLOW，但已有深归档按合法 retention 保留。

## 3. “深归档”不是 files/ 目录存在，而是消息材料闭包

一个 DEEP_REQUIRED chat 的目标闭包：

```text
消息事件 / 编辑 / 撤回 / thread
  + 直接附件
      image / file / media / audio / video
      post 内嵌图片
  + 消息引用的第一层 doc/wiki/link snapshot
  + merge_forward / forwarded bundle
      原转发边界
      可见的原发送者/时间/文本
      转发项里可解析的第一层附件/图片/文件
      转发项里可解析的第一层文档链接
```

**转发只扩展用户实际转发进当前会话的内容和材料，不递归抓取原始会话的全部历史。** 若转发消息中只给扁平文本、API没有暴露嵌套 file token，就保留已看见的 forward 内容和 provenance，并把材料标为 unresolved；不能声称完整，也不能为补齐而扫描另一个未授权聊天。

文档链接默认展开其被引用的当前正文/必要媒体一层；文档内部继续链接到更多页面不递归全站抓取，除非该链接本身被 Follow/任务明确选中。

## 4. 材料身份必须按 material，不按 message 单键

当前历史 manifest 的 `files.by_message` 以 message_id 为键，一条消息多个附件会撞键。深归档不能继续把它当完整索引。

逻辑材料记录至少需要：

- material identity：provider token 或稳定派生 ID；
- source_message_id；
- source_part：direct / post-embedded / forwarded；
- parent_forward_id（如适用）；
- kind / media type / display name；
- discovered_at / source revision；
- fetch status；
- local/protected locator；
- bytes/hash（实际取得后）；
- error/permission state；
- retention/data class；
- derived consumers。

同一 message 可以有 N 个 material。兼容期可保留旧 `by_message`，但它只能是派生导航，不再作为 completeness authority。

## 5. 深归档完成度必须可计算

不要用自由文本 `files.pending: "无附件待展开"` 单独宣称 complete。完整度来自发现集和物化集：

```text
message coverage
+ links discovered / materialized
+ direct materials discovered / materialized
+ forwarded materials discovered / materialized
+ unresolved / inaccessible / source-gone
```

人读可显示：

- COMPLETE：政策要求的材料都已物化、版本和来源关系完整；
- PARTIAL：正文已捕获，但仍有 required material 未取得/权限不足/无法解析；
- SHALLOW_ONLY：只有消息与 locator；
- STALE_COVERAGE：后续新消息/编辑产生新的材料，旧 deep snapshot 不再覆盖当前 source revision。

“扫描完成但权限拒绝”是 attempt 完成，不是 content COMPLETE。

## 6. Capture、materialization、understanding 三件事分开

B1 原始消息/manifest 安全提交后可以推进 capture watermark；DEEP_REQUIRED 的文件下载/链接展开可以随后继续，并在 material coverage 上显示 partial。B2可以先理解纯文本，但涉及未取得的截图/SOP/转发文件时，对应 Claim/范围保持 PARTIAL，材料落地后再补理解。

因此不允许两种错误：

- 等全部附件下载完才保存新消息，导致原始事件也丢；
- 文本先处理就把含未读附件的消息标成“完整理解”。

Daily Work Cycle 应优先处理 priority contacts 的新材料闭包，但一个大文件/权限失败不会冻结无关消息和项目。

## 7. 当前数据反查：为什么这不是理论需求

2026-09-23 对 hwz-records@2e61200 只读检查：

- **Zhengji.Zhang p2p** 已成为参考样板：消息、10个展开链接、65个附件落地，manifest 的 required pending 为无；其中有 `merge_forward` 形态的图片，证明转发材料确实会成为工作证据。
- **Bruce.Xu p2p** 有20条消息和3张图片，实际 `files/` 已存在3张图片，但 manifest 仍写“3张图片未下载”。这说明“文件存在”和“manifest completeness”已经漂移，必须由可计算 material inventory 解决。
- **Nature.Hu p2p** 有38条消息，包含至少1条 `merge_forward` 和1条带图片的 post；当前目录只有 `messages.jsonl + manifest.json`，manifest 没有 files/links coverage。这是 priority DEEP policy 尚未落实的直接例子。
- **Zhengji/Bruce/Vince 项目小群** 已有历史深归档（链接与图片都落地），说明项目小群可以按显式关系而非“所有群”升级 deep。

这些是历史 Git revision 的观察，不代表本轮已经补齐 Bruce/Nature，也不授权现在去下载新附件。

## 8. 转发消息的 provenance 和安全

merge_forward/forwarded bundle 要保留“这是某人转发来的”，不能把嵌套发言错归为当前直接联系人。能取得的原作者、原时间、顺序和嵌套层级保留；拿不到稳定原 message ID 时明确弱 provenance。

转发中的 secret、账号、截图等继续按 raw 私有政策处理：

- 消息正文按既有 redaction；
- 原始二进制只放获准的 private raw/evidence 范围；
- 不把敏感截图自动发给 Jev/Serper/外部模型；
- 不因归档完整就自动上传到 Published Knowledge 或 Drive；
- 参考/报告只引用安全 locator/摘要，不重抄凭据。

深归档扩大的是**保存完整性**，不是数据外发范围。

## 9. 自动执行与预算

Priority DEEP 不应每小时全量重扫历史。推荐：

1. 增量拉新/编辑消息；
2. 从新/变更消息生成 material discovery；
3. 有界 worker 物化 direct/forwarded/link material；
4. 对失败材料按 provider/error 分类有限重试；
5. 大文件、费用、权限或安全边界超出当前 policy 时保持 PARTIAL 并等待相应授权；
6. material landing 触发受影响 B2 understanding retry，而不是全库重算。

下载预算按 bytes/files/request/time 计入 B1 子预算。相同 provider token/hash 在同一授权范围可复用字节，但每个 source-message 关系都要保留。

## 10. 迁移现有资料

不能因为新政策是 DEEP_REQUIRED 就重新下载全部已落地资料。实施先做 inventory/reconcile：

- 从 messages 发现期望 direct/post/forward material；
- 从 links/files 实际目录和 manifest 找已落地 material；
- 对照 provider token/hash；
- 修 stale manifest/索引；
- 只下载真正缺失且当前仍允许获取的材料；
- 不覆盖已验证原字节，不删除历史唯一副本。

Bruce 的 3 张图片属于“字节已在、manifest待纠正”的例子；Nature 属于“需要 discovery + materialization”的例子；Zhengji 用来做回归基线，不能重抓一遍来证明新代码。

## 11. 与 records、飞书文档和 GitHub 的边界

项目对接人聊天归 `hwz-records/information/raw/feishu/chats/**`；其产生的结构化认识归 `information/understanding/**`。这不等于把聊天附件变成项目代码或我们自己的知识正文。

- 外部/他人飞书文档：原对象是 authority，本地保存获准 snapshot/关系；
- 聊天转发来的文件：先作为 raw material 归档，后续是否进入项目输入/Drive由用途和授权决定；
- 我们自己的指南：仍由 B9 在正确飞书/Git authority维护；
- GitHub 只保存符合 private-repo/raw policy 的内容和 metadata；大媒体/高敏感材料按既有 externalization/retention 规则，不用 Git 作为唯一大文件仓。

## 12. 验收矩阵

| 场景 | 必须满足 |
|---|---|
| priority p2p 新图片 | 消息先落盘；图片随后落地；最终 COMPLETE，B2可补理解 |
| priority p2p merge_forward 带文件 | forward边界/原作者时间保留，嵌套file material可单独追踪/落地 |
| forward API不暴露附件token | 保存可见forward内容并标PARTIAL/unresolved，不扫未授权原群 |
| 一条消息多个附件 | N个material全保留，不被message_id单键覆盖 |
| Bruce现存3图 | reconcile后不重复下载，stale pending被修正 |
| Nature现存post图/forward | policy发现缺口并只补缺失材料 |
| Zhengji现有complete样板 | 新实现读取为complete，不重新下载/破坏已有字节 |
| 大通用群含priority person | 仍SHALLOW，除非该群本身被显式升级 |
| secret screenshot | 可在批准private raw留存，但不会自动进入外部模型/公开文档 |
| attachment permission denied | capture继续，material PARTIAL；不把失败改成空文件或complete |
