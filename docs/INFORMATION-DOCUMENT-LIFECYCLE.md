# 工作信息、会议、文档维护与云空间生命周期

状态：2026-09-22 用户场景的详细架构合同。解释 ARCHITECTURE.md §4/5/13/20/24；不新增事实、工作或权限权威。本轮只读调查并在GitHub改文档，实际抓取/写入/安装由后续实施按[执行计划](CC-EXECUTION-PLAN.md)完成。

## 1. 产品图

```text
已授权IM/文档/Wiki/Minutes/Git工作事实
     |
     +-- capture policy（NONE / SHALLOW默认 / 选中范围DEEP）
     |       -> raw/event manifest + 版本/coverage + 必要材料locator
     |       -> 按需要取得原文/文件，不自动上传云盘
     |
     +-- Follow（指定对象/有界子树）
             -> 新增/编辑/撤回/权限变化
                     |
           Structured Understanding
            事实/推论/决定/冲突/更正 + 来源关系
                     |
          当前工作对账 / 候选 / 已接受需求
                     |
        原生Agent与GPT设计/实施/Quay/测试
                     |
          报告 / 指南patch / 会议纪要 / 证据
                     |
       Stewardship（正文、读者、维护范围）
                     |
       Space Management（对象类型、位置、ACL）
                     |
       Publisher（精确写入、回读、receipt）
                     |
       Human Control（按授权提醒、告警、决策）
                     |
       新的源revision回流，但不产生自我重复循环
```

这是职责图，不是所有任务都要跑完的阶段机。读一个报错可直接调查；维护一段指南可直接使用已有证据；研究可以no-code交付。

## 2. 本轮定下的默认

IM在已授权工作来源范围内默认浅归档。明确机器人/应用DM、指定测试会话黑名单不归档正文；人类群不能因为名字像机器、含bot或者bot占比高就被整组排除。保留已有自聊与东京人类群KEEP例外。

浅档只保存合规消息内容、时间/发送者/编辑撤回/thread、链接/附件引用和manifest；不默认取得全部图片、ZIP或链接文档。深档按消息/线程/材料的工作需要或明确策略展开。policy与实际已取覆盖分开，不能一个deep标志掩盖files.pending。

黑名单先依据平台对端类型/稳定ID，不为凑sender计数读取被排除正文。未知身份先metadata评估；计数未知不是无法登记排除的理由。默认浅档不授权无限历史backfill，也不把所有可读个人领域资料自动纳入工作范围。

## 3. 一份文档同时读和维护

DocumentRef关联原正文身份，独立设置：authoring authority、owner、follow范围、stewardship范围、audience/data class、placement、current/accepted revision。一个文件夹“输入/输出”无法表达这些不同事实，不要求重复存两份。

实际案例：

| 文档 | 输入作用 | 维护作用 | 权威与限制 |
|---|---|---|---|
| 自己的配置/故障指南 | 被Agent/Bot引用、跟进人改 | 在指定Published Knowledge知识库按授权更新正文/截图 | 原指南是正文权威，邻居文档不自动可写 |
| LiteLLM工作文档 | 需求、决定、协作变化 | 获准章节回填真实进度/结果 | 保持别人的原对象、位置与读者，不接管owner |
| Bruce MarketOps SOP | 需求/Scene规则与长期目标 | 默认只提修改建议或本项目映射 | 原SOP不改；新版本不自动解禁外发 |
| 别人维护的公开教程 | 按需参考/Follow | 默认无写；有授权才局部协作 | “公开”不是复刻/搬家/分享许可 |

归档消息中已有AI平台知识库作为自有最新版指南落点的线索。与旧地图“共享空间全只读”冲突，应以默认只读+具体已授权文档/子树例外迁移；当前ACL和目标节点执行时再验，不去占用空“配置文档”空间或创建新知识库。

## 4. 附件与会议需要保留什么

IM文件默认只保留出处、类型/大小（若提供）、权限、fetch状态和locator。报错截图、需求/SOP、会议关键转录、必要交付文件在改变工作判断或复用时展开；其余不为“完整”批量下载。

读取、持久保留、上传、公开和分享是不同效果。原source合法可访问时优先原链接；需要复用且被允许的副本才交空间管理。图片可能含密钥/个人数据，文本脱敏或文件0600不能证明可Git提交/公开上传。

MeetingBundle关联原Minutes、recording、transcript、AI summary和附件。转录/明确人确认支持决定，summary是派生；不将无关背景讲话、推测负责人/期限直接变需求。先metadata/摘要/相关片段，有真实理由再处理音频；缺成员准确PARTIAL。

## 5. 落位模型

MyDocs是当前工作的人读界面：工作台、每日对齐、项目工作区、汇报和真正历史归档。个人笔记放经过整理的私人长期知识。Published Knowledge放面向既定企业读者的维护型指南，不等于互联网公开。

Drive负责获准的输入/会议sidecars/截图trace/交付文件，沿已有收件箱、工作资料、临时、归档；项目和年月桶只在有内容时建。Minutes原对象不是Drive目录。原外部文档保留原位，备份不能升格正文权威。

产物来源不决定位置：来自会议的代码测试图仍可归测试证据，来自IM的SOP仍是源文档。逻辑用途→真实对象/父节点/ACL由空间能力resolve，不让每个Skill硬编码token/path。

## 6. 两条水位与回流

capture水位在原始事件/manifest安全持久后前进；understanding水位在有效Claim patch/依赖处理后前进。模型失败可重试理解，不重新抓整库；显示理解滞后，不把capture成功说成认知已对齐。

来源变化找到依赖Claim、指南段落、任务/计划和记忆；只失效受影响部分。自己的publication receipt去重自身版本，不能无声跳过后来人改，也不能自发重写成无限循环。原始来源与派生报告形成有向来源关系，不靠自我引用证明事实。

## 7. 写保护与资料寿命

更新原文要有对象级维护授权、最新baseline和内容证据。真正条件写只有实际Provider证实才声明；本地writer锁/写前读取不能隔离远端人类同时编辑。不能保护冲突范围就产候选/patch proposal，不自动whole overwrite。

超时先查实际效果，partial只补未完成步骤。journal/receipt保留先intent后观测，重启不空表覆盖。清理必须查正文、children、引用、owner/ACL和唯一副本，MeetingBundle多对象操作不假称原子事务。

源撤回/权限收回使当前召回受限；历史审计按合法retention保存，append-only不意味着敏感内容永远不能清除。大raw与原始媒体不自动进Git或公开库，本轮不会为迁移政策删除已有资料。

## 8. 代码分工与实施入口

现有 records/information/raw/{sync_feishu_chat,expand_feishu_doc,expand_feishu_files,drift_sync,_redact}.py 是采集基础；通用代码经兼容迁移进入能力包，原来源/manifest继续归records，不能维护两份redaction。

ability-management的B1/B2/B8/B9详细设计定义判断/共享数据合同；ServerOS负责跨簇Context/工作/效果衔接；Work Engine仍是控制状态；hwz-ops维护实例Binding和带时间的maps。任何新schema字段都要真producer/consumer。

具体操作、依赖顺序、验收与回退见CC-EXECUTION-PLAN.md。此文不生成新的常驻服务、全库采集授权、云写入或日循环启用声明。
