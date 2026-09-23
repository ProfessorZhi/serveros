# 重点联系人深归档：声明范围、转发闭包与可证明覆盖

状态：2026-09-23整合修订。对应B1和SOURCE-DOCUMENT-CONTRACT，不执行回填、不改变任何原始消息/权限/配置。一般工作来源仍SHALLOW；用户确认的关键对接来源长期DEEP_REQUIRED，包含实际转发进来的材料。执行见CC-EXECUTION-PLAN P1/P2/P3/P11。

## 1. 已接受的范围

当前用户明确的种子联系人为Bruce.Xu、Nature.Hu、Zhengji.Zhang；名字仅作导航，resolver从已有可信人员/会话记录关联稳定identity/chat_id。不能把Zhengji与Zhang误拆为两个联系人，不能把一个显示名下所有同名人自动纳入。

明确对应的p2p会话为DEEP_REQUIRED；指定项目小群可同样登记。大型部门群仍默认SHALLOW，不能因其中含priority联系人而整群深抓。新增对接人/项目群由用户或已有明确委托接受，模型推测的关系只产生候选。

策略顺序：检查真实授权/deny边界→明确source规则→已确认对接关系/指定channel→普通来源默认。更具体规则不能绕过上级deny；机器人要转成可捕获source需真实重新分类，不用“priority=true”忽略黑名单。现有自聊和真人Tokyo群KEEP例外保留。

## 2. 捕获深度与历史范围独立

NONE：只保存必要排除身份/类型/理由，不取正文凑计数。
SHALLOW：消息合规文本、版本、thread、材料locator和manifest。
DEEP_REQUIRED：在已声明时间/对象范围内，把消息材料闭包作为必须完成的采集义务。

旧任务级DEEP保持“为此次任务选择材料”的含义，不自动变长期策略。持久policy与实际物化coverage分开；新schema不能只加字段无人消费。

历史范围以start/end或已有可访问起点、回填cursor、增量cutoff和预算说明。用户希望关键聊天完整，意味着未完成的历史应被显示并逐批续填，不是无界扫描所有账号/群。实时增量先安全保存，后台历史回填不能饿死新消息。结果必须写“在范围X、所见版本Y、截至T的覆盖”，禁止没有分母的“所有历史完整”。

## 3. 转发内容与文件究竟如何闭合

目标集合：消息事件/编辑/撤回/thread、直接image/file/audio/video、post内嵌媒体、被消息直接引用的第一层doc/wiki/web，以及转发bundle可见内容与其中实际材料。

**转发的内部结构与外部链接是两种扩展。** 同一已获准转发包里的可解析子消息、子转发/附件属于被转发内容，在depth/node/byte/time上限内遍历并去环；不能借“只取第一层”漏掉包内文件。第一层限制用于跳出消息访问的网页/文档引用，不授权递归抓其全部出链。

只获取被转发进当前会话的内容，绝不因此查询原群全历史或切换更高权限身份。Provider仅暴露扁平文本、子消息不可访问、token缺失、结构不支持或遍历达到上限时记录discovery gap与原因；不能把“没解析出材料”当“没有材料”。原API的真实可用行为需版本/样本验证，本设计不发明合并转发读取命令。

保留转发者、parent_forward、可见原作者/原时间/顺序与位置路径。拿不到原ID就弱关联，不猜造；转发中某人说的话不能记成当前对接人直接声明。相同嵌套消息被两次转发时内容可复用，但两条发现/转发关系分别保留。

## 4. Material N:M身份

消息可以N个材料，一个文件可以被多个消息引用。不能以message_id→one file作为完整索引。material identity按tenant/provider object/实际版本构造，token不是跨租户普遍唯一或永不过期的保证。

材料记录关联source_message/revision、part/位置路径、parent_forward、原locator、kind/name/size（可空）、发现方法/coverage、fetch状态、protected locator、实际字节hash、时间、权限与retention。声明size与实际bytes区分；文件扩展名不证明媒体类型。

同域合法去重复用下载字节，不抹来源；跨ACL不借hash命中透露或给出别人文件。已有files.by_message为legacy导航，migration必须保留messages、backfilled列表和所有已落地材料，旧键冲突不丢“败者”。旧consumer只能处理单项时准确标不完整，不静默选最后一个文件。

## 5. 四个覆盖面，不以文件数自证

1. message coverage：哪些页/时间/版本已取得，中间有哪些gap。
2. discovery coverage：这些消息/转发/post/链接结构是否都被支持的解析器检查；unknown是缺口。
3. material coverage：已发现的required材料哪些取得、哪些失败/未取/无权限/已失效。
4. understanding coverage：哪些文字/图片/文件真的被语义消费，哪些Claim仍依赖未读材料。

只有声明范围内消息覆盖和发现都完整、required材料全部当前版本落地，才能表示该范围内容COMPLETE。发现0材料且解析失败不能0/0=100%；实际目录有65文件也不能证明required集合被穷尽。权限拒绝可以结束一次尝试，但保存覆盖仍PARTIAL。

允许单独表达TRANSFER_VERIFIED/SHALLOW_ONLY/PARTIAL/STALE等人读含义，代码沿现有正交状态扩展，不给业务Item加归档pipeline枚举。当前内容变化后旧完整度属于旧版本，不能继续显示fresh COMPLETE。

## 6. 消息先存、材料续取、理解差量重试

B1消息/manifest原子或可恢复提交后推进capture；材料discovery与下载按有界队列继续，分别留intent/receipt。B2可先读文字，但有关未取得截图/SOP/forward文件的结论要partial/待核。材料到达只唤醒受影响Claim，不全库重算。

一份大文件/权限失败不阻止其他聊天；retry按错误类别、deadline/总预算，未知下载效果先核已存hash，不无限重新下载。下载文件先临时写/校验/原子落位，失败不留下0字节成功文件。URL过期在同scope合法重新解析，不能扩大原会话范围。

已发生源编辑/撤回保留原事件关系；当前可读/可传播按最新权限与retention控制。普通业务修改不可改历史事实，合法泄密清理/删除需求也不能被“append-only永远不删”阻止；须走原始资料owner的可审计流程。

## 7. 当前迁移依据的正确口径

此前在hwz-records@2e61200的只读检查记下：Zhengji有大量已落地图片/链接并含forward；Bruce有3张图在目录中而manifest仍说待下载；Nature消息含forward/post媒体但无相应coverage。

它们是**历史候选测试素材**，不是本轮新测量：

- Bruce先验证真实消息关联、文件可读与hash，若一致只修stale索引，不重下载。
- Nature先发现可解析/不可解析范围，再按权限补真正缺失材料；不能先承诺Provider一定能拿nested token。
- Zhengji是丰富已有样本，不自动冠以COMPLETE。新discovery若发现unsupported或隐藏缺口，准确降为partial并补证据；不为维持旧表绿色忽略缺口。

本轮未回填、未下载、未检查二进制；不回写manifest成功字段。

## 8. 本地、Git和飞书边界

记录总入口为information/raw/feishu现有布局；新desired source policy走information/sources目标声明/旧watchlist适配。大媒体/敏感字节放已有受保护非Gitarchive root，通过manifest locator关联；不能为设计整齐立即移动有消费者的旧资料。

已有private-repo归档许可保留在原scope，但新增材料仍核实际受众、敏感性与保存/传播许可。文本redaction不证明图片没秘密；credential不得进raw/Git/Prompt/公开卡。metadata有敏感身份时也应最小化/受保护。

深归档不是上传云盘/公共知识库、模型全量外发或生成项目代码的许可。文件被后续真正用于需求/参考/指南时，由相应B1/B5/B9/Space按用途复用安全对象，不在每个项目重复复制相同原文。

## 9. 与Follow及外部参考的关系

聊天引用同一doc与用户单独Follow该doc时保留一份source身份/版本快照及多个discovery来源。该doc持续Follow是独立已接受策略，不因曾深归档一次就无限轮询整个库。外部网页默认第一层抓取，自己的飞书参考卡为独立衍生对象。

材料landing、source edit、permission/retraction分别产生精确delta；B2语义writer、A2失效、B9Reference刷新各自消费。仅写一份pending JSON但没真实worker/consumer，只能标待实施，不说后台已排队。

## 10. 必须验证

| 场景 | 断言 |
|---|---|
| 重点p2p直接文件 | raw先存，文件后落地，按同一范围/版本给覆盖 |
| 一消息多材料、重复token | 全部材料和每条来源关系保留，不message单键覆盖 |
| 同转发包内子转发 | 有界遍历已转发内容；循环/上限/不支持均可见 |
| 原群或原消息不可访问 | 不扩大抓取范围、不换身份、不标完整 |
| 第2页失败/回填中断 | 已存页不丢、不跳gap，增量继续 |
| 0材料但parser不支持 | discovery PARTIAL，不0/0成功 |
| 文件已在而pending陈旧 | reconcile验证后修索引，不重复下载 |
| 重要图像有私密内容 | 私有capture/传播政策分别执行，不自动发布 |
| 旧deep样本新增编辑 | 旧COMPLETE只属于旧版本，准确续取/失效 |
| 转发内第三方发言 | provenance不错误归当前联系人 |

实施作为P2的正向与恢复门，再让P3验证材料真正被理解；文件名/目录数量/自由文本pending都不能单独替代内容覆盖证明。
