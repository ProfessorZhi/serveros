# 空间、地图、会议相关性与保留策略

## 1. 三套问题不要混成一套目录

- 内容是什么/为什么重要：semantic role。
- 谁拥有原件、什么 revision：authority/source identity。
- 在哪里、怎样访问：surface/locator/hierarchy。

磁盘路径、飞书目录、能力导航是不同维度。重排导航不移动文件。内容不相关也不直接证明可以删除。

## 2. 通用节点 roles 与 Tokyo 实例

```text
/data/home/vince/                      # Tokyo 绑定示例，程序按 role 解析
├── workspace/
│   ├── src/<repo>/                    # canonical source checkout
│   ├── worktrees/<repo>/<ITEM>/        # 隔离 source context；按需
│   ├── test-envs/<repo>/<ITEM>/        # HOW TO BUILD，不存卷/源码副本
│   ├── ops/                          # 实例政策、来源/收件人绑定、L1/L2 maps
│   ├── knowledge/                    # 稳定可复用知识或 canonical 引用
│   └── records/
│       ├── information/
│       │   ├── raw/<source>/<id>/      # 仅授权、最小化、受 retention 约束的快照
│       │   └── understanding/         # people/projects/responsibilities/topics
│       └── projects/<project>/
│           ├── context/
│           ├── items/<ITEM>/
│           ├── board/                 # 可重建视图，不是数据库
│           └── reports/               # daily/weekly/decisions 的可读交付
├── state/
│   ├── serveros/                     # 按 namespace 拆 durable 控制状态/observed cache
│   ├── ability-management/            # local observations/install receipts，非 registry 副本
│   └── browser/                      # protected profile/login state；不可自动回收
├── runtime-assets/
│   ├── hwz-server-os/<profile>/        # immutable revisions + current/previous
│   └── abilities/                    # 按 revision 安装产物；具体 binding 不凭空迁移
├── artifacts/
│   ├── work-items/<project>/<ITEM>/<RUN>/
│   ├── skills/<skill>/<RUN>/          # 无业务 Item 的能力运行证据
│   ├── ops/                          # 运维/治理证据
│   ├── repro/                        # 可复现案例证据
│   └── serveros-releases/             # 跨 repo manifest/安装/升级/rollback 证据
├── caches/                           # registry/index/download 等可重建缓存
├── data/                             # 应用数据/受保护业务存储
├── downloads/                        # 有 owner/retention 的下载输入
├── envs/                             # 由锁文件/定义重建的环境；不要搬 venv
├── logs/                             # 有 rotation 和脱敏
├── software/                         # 安装的软件
├── toolchains/                       # 固定工具链
└── tmp/                              # 有限任务 scratch
```

这是 role 落点规范，不是批量 mkdir/mv 指令。原生 `.claude`、`.codex`、配置目录、既有 secret store 保持 Provider 约定；不可为了树漂亮迁移。IPC 用目标 OS 的 runtime socket 目录，例如 `$XDG_RUNTIME_DIR/serveros`，不把 socket 放 Git。

源代码目录按 repo key；records/state/evidence 按 project/Item key。一个项目可有多个 repo，一个 Item 可有多个隔离 repo context。路径缺绑定须具名失败，不回退旧北京路径或当前 cwd。

`state/browser` protected，不因 unused age 清 cookie。Authorization/Item/receipt/cursor 等 durable state 必须备份；纯 observed cache/index 才可重建。重建/缓存类型由 producer 声明而非目录名字猜测。

## 3. 地图职责

| 层 | 写者与内容 | 不应出现 |
|---|---|---|
| L1 desired topology | 经确认的 roots、区域、规则、保护范围 | 实时计数、逐文件表 |
| L2 semantic registry | 重要路径/对象的 owner、purpose、lifecycle、authority、consumer | 自动猜用途、秘密正文 |
| L3 observed inventory | scoped、timestamped 的对象/层级/状态；派生 safe projection | 冒称全覆盖、无授权正文 |

L1/L2 是有意维护的语义声明，L3 由 producer 更新。检查输出 NEW/STALE/UNKNOWN/UNOWNED 时，依据事实选择 adopt/enforce/defer；绝不能为 PASS 删除合法工作或扩大 whitelist。

本轮只要求自己的新增/修改范围登记正确、producer 不制造漂移。保留既有全局 gate 的真实结果；已知 ACTIVE 例外可具名保护，但不能把全局 FAIL 印成 PASS。

## 4. 并行业务硬保护

```text
MarketOps canonical:
  workspace/src/marketops -> ProfessorZhi/marketops-research-pipeline
MarketOps active exception:
  workspace/worktrees/marketops/repository-convergence -> ProfessorZhi/marketops
  ACTIVE_TEMPORARY_STANDALONE_REPO_EXCEPTION
Support Bot code:
  workspace/src/litellm-support-bot -> ProfessorZhi/litellm-support-bot
Support Bot knowledge:
  workspace/src/troublebuddy-kb-bridge -> ProfessorZhi/troublebuddy-kb-bridge
```

这些路径当前不移动、rename、merge、reset、clean、stash。`litellm-support` 不是 Bot 代码仓。其他 ACTIVE/未知 owner 的 repo/runtime 同样不碰，不把例外视为 cleanup backlog 必须清零。

## 5. 飞书三个入口：保留产品事实，不发明三座岛

“云文档”是总称，下面可能包括 Wiki、文档类型、My Docs 和 Drive。用户关心的三个入口必须都能检查：**云文档/知识库入口、云盘、我的文档/我的文档库**。

| 入口 | 可用的原生结构 | ServerOS 管什么 |
|---|---|---|
| 云文档/知识库 | document token/type、Wiki node/parent，权限决定可见子树 | 团队/项目文档、规范、正式交付与发布引用 |
| 云盘 | folder/file parent，分享与管理权限 | 附件、输入文件、导出、交付附件、TEST 暂存 |
| 我的文档库 | Provider 支持的页面树/集合 | 私人工作台、待整理/草稿、个人认识、发布入口 |

不得把“我的文档库”硬编码成永久 flat；也不能因为 UI 有树就宣称当前 API/账号已能全量枚举。按 operation 记录 supported/readiness/coverage；缺 API 时先 metadata/显式 locator/人工导出，不伪造接口或用另一账号。

同一 document 被多个入口展示时按 `(tenant, object_type, native_id)` 归一，保留多个 membership/locator。不按 URL 字符串/标题重复复制，不能将三个入口数量相加冒充独立总文档数。

### 逻辑布局（仅在有授权父节点下创建）

```text
云文档/知识库：
  工作台（引用 Board/Today/Decision）
  项目/<project>/{项目背景,工作项索引,正式交付,架构与运行手册}
  方法与知识/{研发,部署运维,技术支持}
  汇报/{daily,weekly,milestones}
  会议索引（只关联相关 MeetingBundle，不复制全部录音）
云盘：
  收件与导出/<source-or-project>
  项目附件/<project>/<ITEM>
  交付附件/<project>/<deliverable>
  TEST 暂存/<run>
我的文档库：
  个人工作台（引用，不双写状态）
  待整理/草稿
  岗位与项目认识（scope-aware）
  待决策/待发布
```

My Docs/云盘/Wiki 本身的原生层级不同，逻辑同名不强制物理同构。不能移动历史/他人的文档来填这棵树。

## 6. 本地 records 与飞书文档的唯一写权威

每个 DocumentRef 声明 authoring_surface、canonical locator、revision、owner、semantic role、publication targets、source refs。默认代码/架构/runbook 在 Git，飞书为发布视图；协作业务文档可飞书为 canonical，本地仅经授权快照/引用。**一份内容只能有一个当前编辑权威**。

发布前比较预期 revision；目标被人工改过则产生 conflict/merge proposal，不自动 overwrite。报告/Board 展示是 projection，不接受未经控制的双向状态同步。历史版本保留来源；superseded 改引用，不能隐式删除旧验收证据。

## 7. MeetingBundle 与相关性

MeetingBundle 关联 meeting、minute、audio/video、transcript、AI summary、human summary、发布/Claim/Item 引用。它是关系记录，不假设文件都在同一文件夹，也不宣称云端删除具备跨对象原子性。

先识别来源/工作关系，再决定内容是否进入认识：

```text
metadata/scope -> policy match -> bounded content when allowed
 -> RELEVANT | MIXED | IRRELEVANT | UNKNOWN
 -> INCLUDE_SEGMENTS | EXCLUDE_FROM_WORK | REVIEW
```

relevance、ownership、retention、legal/organizational hold、deletability 是独立轴。录音时长、用户静音、语言、标题、参会者或自动摘要都不能单独证明“无业务价值/可删除”。不进行声纹识别来猜旁人是谁。

### 用户的支持场景规则

用户声明：多数技术支持过程闭麦，自动录音/纪要可能录到旁人无关聊天；另有一个服务 **Marek** 的群开麦。

- 对已准确识别的闭麦支持来源，默认不主动下载/转写音频；环境录音及派生摘要先排除出岗位认识、Candidate、日报、KB。支持工作本身仍可从聊天、请求、配置/测试、结果确认形成。
- **Marek 是明确例外**：绑定真实 tenant/chat/meeting 或批准的稳定 selector 后允许相关片段进入正常捕获/认识。不能把字符串“marek”、波兰语或参与者姓名当选择器，更不能所有会议都套例外。
- 目前未获得该群/会议稳定 ID，配置必须 `binding_state: UNBOUND`。未匹配/可能属于例外的录音保留 REVIEW，禁止自动删除；不能用 null/wildcard 匹配全部。
- 即使用户闭麦，对方说的支持问题也可能相关；真正排除依据是已确认来源规则与内容/工作关系，不是静音布尔值。
- MIXED 只提取与支持请求/决定有关的有时段定位片段；不把旁人聊天转化成用户职责、待办或已完成成果。
- 已污染的机器生成理解/草稿可以依照新的过滤结果重建；人工批准的 Claim、Item 或已发布报告只标记待复核/给出差量，不静默篡改或删除历史。

优先减少源头无目的捕获：在权限允许且用户批准的特定源关闭不需要的自动录制/导出；不修改全公司/他人会议配置。支持当前配置无法实现时明确说明，不用再次全量抓取作为 workaround。

## 8. 自动清理的四个等级

| 等级 | 可以自动做什么 | 前提 |
|---|---|---|
| 内容隔离 | 从工作索引/自动摘要输入排除，标记 reason 和恢复入口 | 已匹配批准规则；原件不删 |
| 可重建产物清理 | 清本系统拥有的过期缓存/临时导出/被替代且可重建生成稿 | 无人工独有编辑、无 active refs/lease、retention 已批准、producer 可证明可重建 |
| 云端可恢复处置 | 对批准范围内本系统拥有的对象移动至 Provider 真正支持的回收站 | 精确 object/revision、owner/permission、引用/hold 检查、保护例外、恢复能力、计划/receipt |
| 不可恢复删除 | 永久删除音视频、转写、人工文档、共享对象 | **始终需要本次明确授权**，不能由相关性分类器自动触发 |

默认没有云端删除授权，没有 blanket `delete irrelevant meetings`。自动化设计必须完整实现 plan/eligibility/exclusion/receipt/restore/partial-failure 路径，但生产 apply 在 scope/retention/权限绑定后才允许。

删除前评估哪些 bundle 成员仍被引用、属于别人、属于共享会话或存在保留要求；不必把全部音频备份再删以制造隐私副本。only-copy 保护与资料最小化冲突时交由资料所有者/组织规则裁决。

### 失败与重入

多对象动作逐个记录 intent/result/receipt；中途失败报告 partial state，下次回读已执行对象再继续，不重试已成功删除。Provider 不支持 recycle/restore 时返回 UNSUPPORTED 或 UNKNOWN，不用永久删除冒充“回收站”。401/403 不重试其他身份；429/超时按有限预算退避。

## 9. 可执行 policy 的必要字段

policy revision、source selector（真实稳定 ID）、operation、scope、owner constraint、protected exceptions、relevance rule、retention window、allowed content depth、delete mode、approval、expires_at、references/holds checks、restore capability、receipt store。

未绑定的 destructive policy 必须 fail closed；模板不含真实凭据，不预设 retention 天数代表用户同意。只保存必要 metadata，不在 Git 提交真人录音、群消息正文、密钥或 token。

## 10. 必测案例

已确认闭麦无关录音排除但不删；闭麦但对方提支持问题保留相关片段；Marek 精确例外；Marek 未绑定；同名不同群；多语言混合；shared/non-owner；仍被 Item 引用；人工修订摘要；legal hold；403；局部删除失败；恢复后重索引；候选不重生；过期政策与删除前 revision 改变。
