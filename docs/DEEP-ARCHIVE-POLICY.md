# 重点联系人深归档：范围、转发材料与归档/发布分离

2026-09-23整合版。B1按SOURCE-DOCUMENT-CONTRACT实现，本文件不执行回填或改变资料/权限。普通来源SHALLOW，明确bot/app/test为NONE，已确认对接source为DEEP_REQUIRED。归档完整性、Git发布许可、实际语义理解分别判断；执行只用CC-EXECUTION-PLAN P1/P2/P3/P11。

## 1. 已接受的source范围

Bruce.Xu、Nature.Hu、Zhengji.Zhang是用户明确种子，执行用已有可信记录解析稳定identity/chat_id，不以显示名硬编码、不把Zhengji与Zhang误拆成两人。其对应p2p为DEEP_REQUIRED，指定项目小群可同样登记；大通用群不因包含这些联系人全部deep。

规则按真实授权/deny、明确source政策、已接受对接关系、一般默认解析。具体标签不能绕上级deny，bot改为信息源需实际重新分类。自聊与真人Tokyo群KEEP例外保留。模型推测新对接人只能成为候选，不自动增抓取范围。

## 2. 深度与历史范围独立

NONE只登记必要排除身份/理由，不抓正文凑计数；SHALLOW保存合规消息/版本/thread/材料locator；DEEP_REQUIRED在已声明范围内把材料闭包作为必须完成义务。普通source某次task-scoped DEEP不永久改会话政策。

回填说明历史起点/终点或可获取边界、增量cutoff、页/字节/时间预算。关键聊天需要完整意味着保留未完成范围并有界续填，不是无限抓全账号/原群。新消息先保存，历史工作不能饿死增量。COMPLETE必须注明范围/所见版本/截止时间，不能无分母称所有历史齐全。

## 3. 转发内部结构与外部链接不同

目标材料包括直接image/file/audio/video、post媒体、直接引用的第一层doc/wiki/web，以及实际转发进当前会话的可见消息/原作者时间/附件。

同一个已授权转发包中的子消息/子转发可按depth/node/bytes/time预算有界遍历并去环。不能借“第一层”默默漏掉包内文件；第一层限制用于跳出消息的外部文档/网页引用，不授权递归其所有出链。

不查询原始群全部历史、不换更高权限身份。只返回扁平文本、nested token不可用、子消息没权限、解析器不支持或达到上限时标discovery gap/PARTIAL；“没有解析到”不能等于“没有材料”。真实API/CLI支持需核版本/样本，本设计不发明forward读取命令。

保留转发者、parent_forward、可见原作者/时间/顺序/位置路径；缺原ID时弱关联而不猜造。别人嵌套发言不归当前对接人。同内容被转发两次可复用合法字节，但两条发现/转发边都留。

## 4. Material的N:M关系

一条消息N个材料，同一材料可有多消息来源。provider/tenant/object+实际版本/part定义身份，token不是全球唯一且永不变保证。记录source_message/revision、part path、parent forward、原locator、kind/name/声明size、发现方法/状态、实际bytes/hash、protected locator、时间/权限/retention。

同域合法去重复用字节不丢来源；跨ACL不能靠hash缓存泄露文件存在。legacy by_message单键只作导航；migration需读messages/backfilled/实际文件/manifest，旧碰撞记录不丢，不静默挑最后一个附件给consumer。

## 5. 四个覆盖面

message coverage说明哪些页/时间/版本取得及gap；discovery coverage说明这些消息/forward/post是否都被支持的解析器检查；material coverage说明required字节哪些落地；understanding coverage说明实际读了哪些内容及未读依赖。

范围内消息/发现都完整、required材料全部匹配该source版本落地，才可称内容COMPLETE。0材料且parser未知不得0/0=100%；目录有65文件或pending写无待办也不是完整分母。权限拒绝结束attempt而不是content成功。

归档物化、Git发布面和语义理解可不同状态：内容在合法受控存储且hash可核，未进入Git不必是归档缺失；但只有登记hash、实际字节丢失也不能称归档完整。新编辑产生新材料时旧COMPLETE仍只属于旧版。

## 6. 消息先存、材料续取、理解补读

raw事件/manifest安全持久先推进capture；材料发现/下载按预算继续，各自intent/receipt。B2可先理解独立文本结论，依赖未读图/SOP的范围保持partial；landing仅重核受影响Claim，不全库重算。

大文件/权限错误不挡无关source。下载用临时文件、真实大小/类型/hash验证后落位，不留0字节假成功；超时先查已存内容/目标效果，过期URL只在原scope合法重解析。源改/撤回保留关系，当前访问/传播按新权限控制。

## 7. 历史样本与当前进展

旧hwz-records@2e61200的Bruce三图与pending不一致、Nature仅消息而未有材料、Zhengji有丰富deep资料，是当时的迁移依据，不是恒定现状。

本轮后续读取81673ebb已包含Nature新增消息、docx/图片、SOP、相关认识和publish-excluded机制。应保留这些新成果、核当前关联与coverage，不重复“Nature只有38条无文件”或重新全量下载。

Bruce先检查已有字节/关联/hash再reconcile。Zhengji的丰富目录只能作回归候选，不自称完整：发现unsupported或遗漏时准确partial并补证据。主计划当前源码表同时记录semantic writer/水位已落地，后续聚焦真实reading与consumer。

本轮没有下载、回填、打开原始图片或修改manifest成功标记。

## 8. 归档面与发布面的明确边界

记录总入口沿information/raw/feishu，实际字节可以在原获准位置或受保护非Gitarchive，由安全locator关联。不能为了目录设计立即移动活跃/唯一资料。

**已批准保存的原始敏感二进制证据可以在受控归档面保持原样，但不得因此进入Git、普通提示词、日志、外部模型或公开资料。** 不把“禁止传播凭据”误实现成销毁唯一合法原证据，也不为追求deep完整复制凭据到新的文本/索引。正文按既有单一redaction去敏感；private metadata同样可能需限制。

复用records已落地的`information/publish-excluded.json`及manifest的publish_exclusions：它按内容不适合Git发布登记hash/受控locator/provenance。`heavy-media-externalized.yaml`按体积/存储政策，两本判据不合并、不另建重复登记册。原reason-code与结构身份/工作邮箱的明确分类保留，不能一条泛化规则扩成全库文本重脱敏。

历史远端已经传播的凭据截图，不因从HEAD排除或ignore就消失。最新records说明报告这一风险，实际有效性/轮换状态本轮未核；由合法owner核验/轮换/撤销与必要历史处置，不读出/复制值，不未经授权生产变更或force历史重写。发布排除一致性测试不证明所有既有blob安全。

深归档也不自动上传Drive/Published Knowledge、不变成项目代码或业务授权。后续参考/指南所用材料由B5/B9/Space按目的、许可和受众选择安全版本。

## 9. Follow与外部参考衔接

同doc被聊天引用又被单独Follow，原source身份/版本快照可复用，多个发现边保留。深归档一次不自动注册永远轮询；Follow是明确对象/子树/新子节点/预算政策。

外部网页默认第一层取得。自己的飞书Reference是独立摘要/批注文档，不是原文章权威；用户收藏不自动采用其规则。材料landing、source改动/撤权分别触发B2/A2/B9准确delta；只有待续文件而没真实consumer不得说后台已排队。

## 10. 必须验证的边界

| 场景 | 断言 |
|---|---|
| priority直接文件 | raw先存、字节后落地，范围/版本/receipt一致 |
| 一消息多材料、重复token | N条与各来源边保留，不单message覆盖 |
| 转发包内子转发 | 有界遍历，循环/上限/unsupported可见 |
| 原群不可访问 | 不扩scope/换身份，不假完整 |
| 分页/回填中断 | 已存不丢、不跳gap，增量继续 |
| 0材料但parser未知 | discovery PARTIAL，不0/0成功 |
| 现存文件pending旧 | 核字节/关联后修索引，不重复下 |
| 凭据类原始图片 | 合法受控原证据保留、发布排除可追，不外发真值 |
| 已发布历史凭据 | 不以HEAD排除关闭风险，owner处置独立跟踪 |
| source编辑/撤回 | 旧COMPLETE仅旧版，下游失效可核 |
| 转发第三方发言 | 原说话人/provenance不错误归属 |

P2验证真实捕获/材料，P3复用semantic writer验证实际阅读/正式理解，P5处理合规Reference/发布。三者不能用文件数、free-text pending、Plugin加载或空JSON相互代替。
