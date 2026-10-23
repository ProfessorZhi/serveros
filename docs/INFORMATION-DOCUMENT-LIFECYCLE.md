# ServerOS 信息、知识与工作协作生命周期

> **这篇回答什么：** 一条材料从被发现到成为认识、需求、工作、交付，各自的身份与权威在哪里；四种「完成度」为什么必须分开；归档、备份与副作用恢复的边界是什么。
>
> **状态：** 设计 · 2026-09-23 整合版（§11 与文末为 2026-09-24 补充）。本文解释 [ARCHITECTURE.md](../ARCHITECTURE.md) §4/5/13/20/24，保留原 Core / 控制 / 正文权威，不增加新业务族；本次没有业务抓取、写入或部署。
>
> **一条主线：** 统一体系不等于强制流程。B1 捕获、B2 认识、B3 需求、B4 工作、B5/B6 研究设计、B9 参考笔记都可以单独成立，不要求每条材料走完全程。
>
> **怎么读：** §1 是全图；§2–§3 是载体与四种完成度；§4–§5 是文档角色与本人空间；§6–§8 是源目录、稳定身份与发布/恢复；§9 是与 Ability 体系的接口；§10 是当前差距；§11 与文末是 2026-09-24 的补充。

## 1. 统一体系，但不是强制流程

```text
IM / 转发材料 / 飞书doc与Wiki / Minutes / Web / Git / 论文
                 ↓ B1：发现、范围与捕获
  source声明 ── snapshot/material/coverage ── Follow变化
       │                     │
       │                     ├─ B9：我的参考笔记（可收藏后即结束）
       │                     └─ B2：有来源的认识/变化/冲突
       │                                    ↓
       └──────── B3需求 / B5研究 / B6设计 ─ B4当前工作与授权
                                            ↓
                          native CC/Codex / GPT review / Quay
                                            ↓
                             A7验证 / A8制品（需要时）
                                            ↓
                           B8报告 / B9知识与空间 / B10投递
                                            ↓
                             实际回执与结果回到当前认识
```

Source、Snapshot、Reference、Understanding、Owned Knowledge是对象关系，不是每件资料必须晋升五次。只读查询、收藏链接、验证已有能力可直接交付。Parent由原生Agent承担，负责所有已接受的必需交付；child done不自动结束原目标。

## 2. 各仓与物理载体

serveros负责产品/Core/Context/组合/人面，薄Plugin仅入口和必要接入；ability-management拥有可复用Skill/Provider源码与分发；Work Engine拥有当前工作状态/Run，Agent Control拥有真实委托/准入；records/information拥有来源声明/获准观测与当前认识；ops绑定实际路径、身份、环境、受众和触发。

大raw/受限文件/媒体、可再生索引、活动游标/锁/outbox按既有受管存储分别落位，不因information是总入口就全部进入Git。原始对象通过稳定身份连接，不靠绝对路径或名称推导authority。

全21职责与其正常入口/消费者/验收见ability-management/design/ABILITY-REALIZATION-PLAN.md；不是21个插件或21条固定工作流。

## 3. 归档政策与四种独立完成度

普通已授权真人来源默认SHALLOW；明确bot/app/测试黑名单NONE；用户确认的重点对接人私聊及指定项目小群DEEP_REQUIRED；普通来源的某份必要材料可按任务做DEEP，不永久升级整群。

优先联系人当前种子是Bruce.Xu、Nature.Hu、Zhengji.Zhang，绑定稳定chat/identity，不按名字猜或自动纳入其所有群。采集目标历史范围和预算要声明；FULL BACKFILL只是有界历史回填任务，不是全账号数据授权。

必须分开：消息捕获覆盖、材料发现覆盖、材料落地覆盖、语义理解覆盖。capture原始事件提交即可前进；未读材料造成相关语义PARTIAL；附件失败不能拖住所有消息，也不能把只读文本称整包理解完成。

深归档包括实际转发包里的消息/文件/图片/可解析子转发，遍历上限和权限覆盖明确；只跟踪已转发进来的内容，不查原群所有历史。网页/文档链接默认第一层，不能借转发深归档爬完整网站。细节见[DEEP-ARCHIVE-POLICY](DEEP-ARCHIVE-POLICY.md)。

## 4. 文档角色按对象分开

| 对象 | 默认行为 | 正文权威 |
|---|---|---|
| 别人文档/指定知识库子树 | Follow、按范围快照、必要参考笔记 | 原文 |
| 别人的文档但由我们协作维护 | Follow+授权章节patch，原入口/owner不动 | 原共享doc |
| 自己公开指南 | 适用版本核实、局部维护/截图/回读 | 指定飞书doc或Git对象 |
| 外部网页/论文/Git资料 | 读取/收藏/按许可离线，采用另判 | 原Source |
| 自己的参考笔记 | 读者摘要、用途、批注、原文入口 | 我们的参考doc，非原文章 |

follow范围、所有者、stewardship、受众、保存权限、正文权威、placement分别记录。一个doc可以既输入又维护；“共享库”既不表示全只读，也不表示全部可写。具体对象、范围和当前权限共同决定允许动作。

Bruce SOP新版本先作差异/候选规则，不自动改其原文、不解除MarketOps research_only/send限制。LiteLLM工作文档回填真实进度只在获准章节，历史备份不升为权威。

## 5. 本人空间的最小变化

沿用现有MyDocs工作台、每日对齐、项目/需求/报告与汇报；私人知识归个人笔记，自有正式指南归公开文档，外部来源索引/只读镜像归既有外部文档，EXTERNAL_MAINTAINED原位维护。不新增MyDocs参考库、不重编号整套目录，也不建立每项目/月的空桶。

外部文章进入个人参考资料，默认原链接+我们的摘要/批注；不是全文自动转载，不进入团队正式指南。真正采用后由B5/B6/B9在原规则/指南上更新受影响部分。私人资料分享到更广读者需独立授权，不能用摘要名义绕源受众。

Drive用于必要附件/输入/会议sidecar/测试/交付。原Minutes保留，会议纪要按用途进入工作区/报告，不自动Wiki化。复制、移动、分享、删除与正文维护是不同效果。

## 6. 源目录、快照和Git的方向

records/information/sources/是目标desired声明位置：谁/什么范围被关注、为何、body authority/受众/捕获与Follow策略。现有DRIFT_WATCHLIST和not-archived登记经adapter迁移，raw manifest仍只拥有observed覆盖；不让两份可编辑列表同时生效。catalog是派生检索面，不新建source数据库。

默认本地snapshot是证据或备份，不是另一份authoritative正文。不同用途的方向：外部source→本地；飞书原生参考/指南→只读本地snapshot；Git工程正文→飞书发布视图；人对视图的修改→回源patch提案。没有通用mtime双向覆盖。

Git只同步适合该repo实际受众的声明、关系、去敏感manifest/原创认识/获准文本。大媒体、完整受限正文、cookie/签名URL和高敏感资料默认非Git。引用可用原stable locator+所见版本+获准snapshot，取消“为了引用必须全量先归档”的旧限制。

## 7. 稳定身份与失效

对象身份不随标题/路径改名；source revision、captured_at、last_checked、last_changed、实际内容hash与解析器版本分开。正文hash不等于资料真实性证明，ETag或revision字段也不自动提供并发CAS。

来源更正/撤回/权限变化定位受影响Claim、Reference摘要、计划/review和知识snapshot，精确标stale/conflict/不可召回。局部已提交的变化先保留receipt再恢复依赖传播，不能全库重写来“同步”。自己的publication去重自身版本，后续人工编辑仍正常进入Follow。

## 8. 发布、备份与副作用恢复

阅读、下载、长期保留、提交Git、飞书创建/更新、移动/分享、发送是独立effects。目标要求到哪一层预先确定：收藏进飞书需要实际doc回读；只保存链接不要求下载所有附件；发给同事需要sender receipt。

每个有副作用的步骤有intent、输入版本与真实receipt。超时先查目标效果；无法查询就保持UNKNOWN并限制重试，不创建重复对象。多系统之间不假称ACID；原文/媒体/索引/Git/outbox可以PARTIAL，重启只补未完成边。

自动管理分区与人批注分开，current/base/desired冲突不可安全保护时交候选，不whole overwrite。索引失效可重建；来源删改与保留/真正删除政策分别处理，不自动删除唯一历史字节。

## 9. 与整个Ability体系的接口

B1产source/material delta；B2产已追溯认识变化；A2过滤撤回/过期；B3提出/接受目标；B4从各权威组装准入；B5/B6评估外部建议，不能收藏即采用；B7/A6实际执行；A7按用户AC证明；A8按需生成制品；B8/B9/B10分别内容、正文/位置、接收效果。

A3保护并行WIP/路径迁移，A4/B10监测Core/必要服务和可读资源，A5保实际跨端lease与证据，G1管已安装能力，不重复各族框架。source/资料工作不强制先有虚假业务Item；需要后台治理证据时用合法Run或既有operation父类型，不造假Run点绿。

## 10. 当前差距与一次实施入口

本轮读到B2的content_identity/card_digest绑定已写入当前源码；不能再按早期文档宣布它缺失。该模块仍是重读提案，语义writer正向消费者仍需核验。focus_source当前仍列多项NOT_PRODUCED，grant/worker事实要从真正owner join而非塞Card。

外部参考的整个产品合同见[EXTERNAL-REFERENCE-LIBRARY](EXTERNAL-REFERENCE-LIBRARY.md)，归档合同见[DEEP-ARCHIVE-POLICY](DEEP-ARCHIVE-POLICY.md)。执行只用更新的[CC-EXECUTION-PLAN](CC-EXECUTION-PLAN.md) P0–P12，21项门见能力实现矩阵；旧观察保留为历史，不复制一套新的最终台账。


## 11. 2026-09-24 飞书信息面与三类文档关系

前轮带日期的空间观察记录了My Document Library、`个人笔记`、`公开文档`、`外部文档`与Drive。本轮已把旧落位建议修改到正文；实际写入仍须重新核对象/ACL，不由历史观察宣称当前初始化或同步已完成。

- `FOLLOW_MIRROR`：只读长期关注的外部doc/wiki/web等，原Source仍authority；在 `外部文档` 建稳定只读镜像，records/Git保存source+mirror版本。
- `EXTERNAL_MAINTAINED`：别人/共享位置但由我们参与维护，原位维护，不在自己云空间复制第二正文。
- `OWNED`：长期私人知识→`个人笔记`；正式多人指南→`公开文档`；正式日报/周报/专项→MyDocs `汇报`。

MyDocs其余部分继续承担动态工作、每日对齐、项目工作台/认识/报告；Drive承担原始文件和MeetingBundle资产。飞书文字文档可与local `hwz-records`和private GitHub形成版本化三端连续性；Drive大二进制默认只把metadata/hash/locator关系进Git。

详细合同见 ability-management [FEISHU-INFORMATION-SYNC-ARCHITECTURE](https://github.com/ProfessorZhi/ability-management/blob/main/design/FEISHU-INFORMATION-SYNC-ARCHITECTURE.md)。

## 2026-09-24 统一产品落点与恢复

外部长期只读内容的稳定镜像归“外部文档”；共同维护原共享正文不复制第二飞书正文；私人知识归个人笔记，正式指南归公开文档，动态工作/对齐/汇报归My Document Library。Drive是大字节Asset Plane，不做全量Git blob同步。具体关系与例外只维护在能力仓Feishu Sync Architecture，不在本篇另造目录策略。

Source capture、material到达、原生reading、Claim提交、View/Memory刷新、文档发布和通知分别验证。晚到材料只补相关边；权限撤销先限制当前资格；人的直接编辑保留并按单正文authority进行三方比较。没有可靠远端条件写不自称全局原子事务。

Meeting是这些输入对象上的语义source；录音/转录/自动纪要/附件可缺。UNTRUSTED_FOR_BUSINESS远控背景声音不生业务Claim；有价值时才curated note，无变化不为文档数量生成新页面。
