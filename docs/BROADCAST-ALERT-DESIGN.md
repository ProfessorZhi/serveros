# 资源播报、报警卡片与可靠投递

状态：详细架构设计，2026-09-22。落实ARCHITECTURE.md §20/26与既有notification/presentation合同。不是第二个告警平台；实际阈值/状态词/文案只保留在ServerOS拥有的契约与代码中。

## 1. 当前实现核对

读取GitHub当前notification-contract.yaml以及本地noc-copy的card-definitions.yaml、CARDS-STYLE.md、alert_digest.py。已有anomaly/resource_anomaly、alerting、digest、presentation、cards、notifications边界可复用。alert.opened/resolved有producer；若干Session/Run通知仍为声明未接。

本地CARDS-STYLE.md的GitHub固定基线同路径查询返回404，故其风格/实现必须作为待核对的本地资产，不能假称已经随当前main发布。迁移时先查分支、实际consumer和安装revision，不覆盖并行UX工作。

## 2. 两个产品节奏

| 类型 | 触发 | 输出与正常静默 |
|---|---|---|
| 资源播报 | 既定时间/整点计划或用户主动查看 | 我的资源、整机资源、共享AI网关；按选择频率送，明确窗口/覆盖 |
| 告警 | 有证据的当前偏差/危险趋势、实质升级或重新出现 | 可行动变化优先；重复未变化不因时间到再发 |

正常定时播报不是警报；空delta不创建“暂无告警”推送刷屏。恢复默认更新原告警/进入下一次正常播报或有其他实质变化的digest，沿现有policy不为每个恢复单开通知；只有另有明确receiver策略才单独发恢复。

个人设置给时区、定时点、receiver和静默窗口；ServerOS契约给语义门和state/attention词汇。不能在hwz-ops发送脚本里再造中文severity/根因/建议映射。

## 3. 数据与状态面

采集器提供stable subject/owner、desired/actual、测量时间/窗口、threshold/policy来源、数值和有效coverage；不提供未经契约的自由中文结论。

我的资源仅归本人受管scope；整机只展示获准aggregate，不泄其他用户cmdline/目录；共享AI网关分别展示进程/功能探针/近期traffic，不把其中一个替代全部。

CPU百分比必须说明分母（单核/整机/配额），RSS与cache、磁盘used/free/可写剩余、请求成功/失败/未归类、latency统计的窗口与分母一致。无流量不等于健康；未观测不写0；旧观测不因新render变fresh。

## 4. 告警状态与注意力

沿既有detector拥有finding。识别key使用code+device+stable subject，PID/名字/时间不生成身份。身份复用/重开时需要可分辨episode/version，避免旧ACK影响新故障。

OPEN、ACKNOWLEDGED、SNOOZED、RESOLVED分别是条件与人的处理；ACK/SNOOZE不是恢复。只有拥有该检测的成功覆盖观测证明条件解除才RESOLVE；collector缺失、body空、超时、目标不可见保留unknown/hold。

desired stopped是正确休眠；归属未知/长期存在本身不等于危险。注意力层使用契约已声明词，例如需处理/需确认/值得留意/供参考/无事，而不是UI随意把unsupported画红。未知值保留原信息与中性说明，不能“美化”成正常。

## 5. 卡片信息架构

风格为专业、克制的SRE/NOC：对象/问题明确，状态、影响、建议、首次观测，技术细节折叠；计finding用“项”，计subject用“个”。不要机器字段堆砌，也不逐行讲“本卡不能知道”。

以下仅为合成设计样例，不是当前机器读数：

```text
⚠ 需确认 · 1 项
Claude 会话 · 遗留进程组
状态  归属会话已结束，关联进程仍在运行
影响  暂未发现明确影响
建议  核对进程归属，确认是否仍需保留
首次观测  09-22 10:10
▸ 技术详情
只读告警 · 未执行终止、重启或清理操作
```

影响未知使用唯一定义中的措辞；确认影响需实际受影响consumer证据，例如“2个当前任务无法连接接口”，不能从restart failed编成network down。建议先核对什么，不在只读告警面直接给杀进程/重启操作按钮。

技术详情保留精确subject/ID/PID/epoch、source、revision、阈值/窗口/数据年龄、owner和原始证据。折叠不支持时给安全详情入口，不丢精确身份。排序保持注意力/当前问题优先，top-N之外有计数与下钻，不能显示3项让人以为只有3项。

## 6. 资源播报示例

以下为结构，数值从真实模型填充：

```text
Tokyo · 资源播报
观测窗口 / 数据年龄 / 当前覆盖
我的资源：受管任务与进程贡献、配额占用
整机资源：CPU、内存、存储整体压力
共享AI网关：进程、功能探针、近期请求与异常
需留意：有真实依据的偏差或观测缺口
▸ 资源/请求/告警详情
```

某路由无流量写“本窗口没有流量”，不写100%成功；探针只覆盖部分路径要注明，不能“All paths ready”。报警和播报消费同一事实模型与状态词，provider字段没有值时保持unknown。

## 7. 从事件到回执

```text
owner observation → alerting state/episode
→ digest（NEW / ESCALATED / REOPENED / RECOVERED）
→ frozen presentation payload + payload hash + intended rows/versions
→ durable delivery intent
→ actual transport send/update
→ provider receipt / unknown
→ delivery cursor advances only what was actually shown/accepted
```

初次接通先记录baseline，历史积压不是NEW，不刷出几百张卡；积压仍在状态播报/详情可见。已知未通知但严重待办可随首次受管状态摘要展示，不能从“已baseline”误写成已告诉用户。

## 8. 发送中变化的明确处理

本地alert_digest.record当前按key回读最新register后记录severity/state，而非总是使用已发送快照；静态审查提示：compose后升级、send中恢复或重新打开，可能被记成已经通知新状态。这是待复现风险，不声称本轮已跑出bug。

验收要求receipt绑定冻结的row snapshot、episode/version和payload hash。发送A版本期间变为B，只能标A已送，B仍待处理；旧send ACK不得把新episode标已通知。一次intent的幂等key不能在重试时换成不同payload；重合并产生新版本有明确supersedes，已未知投递先核对。

## 9. 并发、崩溃与未知效果

一个logical sender scope一个owner/lock，不用两个timer同时读改delivery cursor。atomic file replace不等于跨两份store的事务；若alert-store和delivery-cursor分步写，必须能由intent/receipt恢复，不出现store说已发cursor说没发而重复轰炸。

DELIVERED表示transport确认接受，不表示用户已读或同意。超时但provider可能已收则UNKNOWN，先查询消息/幂等receipt，不能盲重发。无法查询时保留未知并限制后续重复，不能承诺exactly once。

cursor损坏不能默默重建空baseline，基线丢失会把所有积压当新告警；生成明确运维缺口并阻止无限重播。静默/冷却时事件仍保存；sender失败不能被业务探针当恢复。

## 10. 动作和闭环

查看详情、ACK、snooze、请求诊断、批准计划、执行变更是不同动作。真实callback校验actor/tenant、action nonce/expiry、target/baseline与当前授权。只读告警不执行修复；人类ACK不改变detector事实；当前生产cutover/rollback永远走人执行。

目前未接通的session.created/turn/waiting/run/handoff事件要接相应权威producer，不能通过渲染样本补“假事件”。没有验证的人机callback不能标SUPPORTED；不为让按钮有响应直接绕control plane。

bot归档黑名单与此结构化消息链独立。自己发的播报不能经B1/B2重新创建需求/告警；但人在线程回复的新决定可在明确来源授权下进入理解。

## 11. 源码与迁移

沿用runtime/anomaly.py、resource_anomaly.py、alerting.py、alert_digest.py、presentation.py、cards.py、notifications.py与相关YAML。每条code词义定义只一份，布局renderer仅消费PresentationModel，ops仅布线。补缺producer/receipt/并发问题，不写新的notifications-v2平台。

旧技术通知模板与新面向人的卡片不同层，迁移必须有真实使用者和兼容回归。CARDS-STYLE本地未发布资产经审核整合，不默默新增第二套style规则。对规则的变更用基于观察的测试验证，不能仅列禁词词表代替语义正确性。

## 12. 验收矩阵

| 场景 | 必须满足 |
|---|---|
| 正常定时播报 | 三块数据同scope/窗口说明，发送按周期，不制造alert |
| 无owner/无traffic/探针部分失败 | 不推断泄漏/全好/业务全挂，unknown准确 |
| 首次接通历史积压 | 不作为NEW逐条发；历史计数/详情可见 |
| 重复未变化/静默 | 不刷屏，不丢事件；policy升级时才更新 |
| compose后severity升级 | receipt只确认已发版本，新升级仍可见 |
| send中resolve/reopen | episode不能被旧ACK覆盖 |
| timeout已被Provider接受 | 查询后复用实际receipt，不重复发送 |
| sender/cursor崩溃或双触发 | 可恢复、只一个owner、不从空表重播 |
| 停采/目标不可见 | hold/unknown，不自动RECOVERED |
| old按钮/错actor/过期baseline | 无实际效果，拒绝原因可见 |
| 未接producer | 不产生伪Session成功/失败通知 |
| 响应最终回读 | 真实飞书内容/按钮/计数/收件人对应冻结payload，日志不泄秘密 |

纯渲染fixture通过只算CODE/组件测试。真实投递/回读与安全callback分别算INTEGRATION；未实际启用的timer/transport不能写LIVE。

## 参考依据

具体用户样例与代码阅读见 ability-management/design/ABILITY-DESIGN-EVIDENCE-20260922.md E7。设计采用 [Google SRE monitoring](https://sre.google/sre-book/monitoring-distributed-systems/) 中症状与原因分离、可行动低噪声的原则；这不是对本机告警效果已达标的实测声明。
