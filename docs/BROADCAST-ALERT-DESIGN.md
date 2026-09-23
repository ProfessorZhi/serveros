# 日常资源卡、关键服务与可读告警

状态：2026-09-23 根据用户10:00截图修订的产品设计，未声明新卡已部署。沿用ARCHITECTURE.md与唯一ServerOS presentation，运行观测/自身监测详见[NODE-OBSERVABILITY](NODE-OBSERVABILITY.md)，验收见[MONITORING-ACCEPTANCE](MONITORING-ACCEPTANCE.md)。

## 1. 从实际截图与代码定位问题

截图可见的是UID/进程数、CPU型号、本机端口；用量藏在三块折叠内。外层标题与折叠标题重复；“三项均已读到”同时出现两次；机器scope gateway/host/mine、container0分开说明、只读告警免责声明占据底部。监控serverosd和必需网络/服务不可见。

serveros@89887a4 runtime/cards.py::status_card只把各区lines[0]放外面；card_source._host_section先写CPU型号、用户区先写身份。于是源码满足“三板块首行可辨”的旧测试，但用户读不到资源状态。修复必须改变摘要合同和验收判据，不能只换排序、默认展开全部或再修一段文案。

## 2. 两个任务，一套事实

日常状态卡回答“资源余量怎样，关键服务能不能用”；告警回答“什么发生偏差、影响谁、现在做什么”。继续按既定周期发送状态卡、按有意义事件发送告警。正常无变化不额外发“没有告警”；旧账/恢复清单不塞日常首屏。

健康、观测覆盖、注意力独立。绿色只指已验证范围的正常；部分未测/过期不能叫环境全部可用。有确证异常+部分未知时优先显示异常，并简短标监控缺口。

## 3. 日常卡的默认可见层

保留用户原先的三块主资源：我的资源、整机资源、共享AI网关；增加一个紧凑的关键服务状态带，不把全部注册表展开成第四张大仪表盘。

| 位置 | 必须直接可见 | 放详情或省略 |
|---|---|---|
| 标题/次行 | 节点、人读状态、采样时刻/窗口；异常时最重要的1–2件事 | “观测三项已读取”、render scope原始key |
| 我的资源 | CPU核数/有效配额比、内存GiB、磁盘quota或当前可写余量 | UID、进程数量、完整进程排行 |
| 整机 | CPU使用率、内存used/total、实际关键盘使用率和可用量 | CPU型号、完整硬件规格、正常inode/PSI明细 |
| 共享AI网关 | 实际应用/请求状态、窗口内请求/错误与覆盖；无流量明确说明 | 每次强调与container0不同、长loopback端口说明 |
| 关键服务 | serverosd、Tailscale及当前必需隧道/访问入口、实际当前依赖的少量服务；状态与最近有效检查 | 全部units、全部peer、所有Task、未使用Provider |
| 页尾 | 最短必要的时间/partial说明与详情入口 | 旧CLOSURE、历史账本、日常卡“只读告警”尾注 |

任一异常资源/服务都提升到首屏摘要，即使本来在详情。健康service默认显示4–6个实际需要的简短项，超出部分有详情；不能人为填假数或漏掉未测项让“6/6正常”好看。

## 4. 手机与桌面可读性

按390px目标和360px回归宽度，正常状态用户不展开就能回答：本人CPU/RAM、整机CPU/RAM、主要存储余量、重要服务是否异常。保持三资源块总计约6–8行，加短服务带和时间；实际中文换行在native Feishu复核，不能通过缩小字体/删关键指标达标。

每节只一个标题；详情入口叫“资源明细”“服务与链路详情”，不重复整个节名的大边框。默认不把关键数字放collapsible panel内；大屏可两列，手机实际客户端不支持预期自动换列就用一列，不能靠HTML CSS声称飞书会同样排版。

用文字+符号同时传状态，不只靠红绿；故障名称/剩余空间/下一动作不可只在tooltip。卡片展示的名称来自稳定对象的人读标签，同名才加项目/用途消歧，不显示一串Session/PID作标题。

## 5. 合成日常样例（不是本机实测）

```text
Tokyo · 日常监控　🟢 当前监测范围正常
10:00 · 资源近1分钟／网关近1小时

我的资源
CPU 2.4 / 4 核配额　内存 6.8 GiB
磁盘配额 42 / 200 GiB

整机资源
CPU 18%　内存 64 / 247 GiB
系统盘 38%　数据盘剩余 1.2 TiB（30%已用）

共享 AI 网关
应用探针通过 · 本小时 120 次／失败 1 次

关键服务
serverosd 正常 · Tailscale 路径可达
CloudCLI 内网入口通过 · 研究隧道待命

▸ 资源明细　▸ 服务与链路详情
```

样例不预设这些配额/端点/阈值存在。没有quota就写实际占用及来源口径，不补限额；服务“通过”限定于已测路径，不验证任意手机/Windows都能访问。即使一个失败请求未达到告警阈值也保留真实统计，不把120次都写成功。

未知样例：`整机内存暂未取得；CPU/磁盘为10:00采样`。失联样例：`监控核心3分钟未推进；下方资源为09:57最后观测`。不能把持久缓存画成新鲜状态。

## 6. 告警首屏必须回答的问题

标题：节点 + 有人能理解的异常对象/症状，不只“WARNING”或“需确认1项”。正文按实际信息密度可合并字段，但必须能回答：什么异常、多久/趋势、影响、已验证到哪层、当前建议/动作状态。原始code/原始栈/PID/全部历史置于详情。

合成样例：

```text
⚠ Tokyo · 监控核心采样未推进
已持续3分钟 · 09:57后无新采样
影响：当前资源与关键服务状态无法继续确认
已确认：OS进程存在；独立检查发现采样序号未变化
建议：检查serverosd循环与持久化错误；未自动重启
▸ 查看证据
```

```text
⚠ Tokyo · CloudCLI 内网入口不可用
已持续2分钟 · 2次应用探针失败
已确认：本地应用正常；tailnet入口检查失败
影响：该内网入口不可用；其他客户端路径尚未验证
建议：检查入口映射、访问身份与Tailscale链路
▸ 查看证据
```

不把“进程归属未知”常态当故障；不从入口失败直接推断Tailscale根因。证据足够才写原因，不足则状态/影响具体、原因待核对。没有做影响检查不能统一写“暂未发现明确影响”暗示已经排除；可写“影响待确认”或更有范围的“尚未验证手机访问”。

高风险修复不隐藏在按钮里。查看详情、确认看到、暂停提醒、请求诊断、批准计划/执行是不同作用域；当前生产切流和回滚仍人执行。只读告警若有必要可一处写“未自动操作”，不要让日常卡继承长操作免责声明。

## 7. 低噪声与聚合

只对当前期望运行/被工作需要的对象报警。CPU忙不必然异常；DERP中继可用不是故障；手机休眠、按需浏览器没启动是正常状态。性能阈值用持续窗口/hysteresis和实际影响，配置来自政策不由renderer推导。

同一故障域/可信依赖引发多个缺口时合并为一个事件，附受影响路径。例如serverosd停采可导致多块状态陈旧，不能为每个过期metric发一张卡；没有依赖证据不擅自认定共同根因，原独立故障仍保留。

正常卡不展示几百条历史积压/恢复清单。重要当前未展示事件有“另有N项当前问题”并给详情；历史统计只在主动查看的诊断面。恢复默认更新原episode或进入已定摘要，不独立刷屏；实质升级可以打断，维护/静默窗口的紧急例外必须来自明确receiver policy。

## 8. endpoint、身份和时间的呈现

loopback如127.0.0.1只属于服务器本地，不能做手机“打开服务”按钮。只有实际授权且对相应客户端有可达性证据的入口才能展示为可用链接；只声明的地址可在详情标“配置入口／未验证”。不得自动通过Funnel/公网暴露来让链接可点。

时间区分observed_at/window、rendered_at、transport accepted_at；卡片顶部优先采样/业务窗口，不把每小时新发等于资源新采。UTC存储按实例用户时区显示，clock skew/未来timestamp保留异常，不能把负age变“刚刚”去掉证据。

## 9. 产品数据模型替换首行启发式

`lines[0]`不再决定摘要。现有PresentationModel应兼容扩展为明确summary metrics、service summaries、issues/coverage、detail sections及sample标识，名称是逻辑设计不是宣称字段已实现。数值与单位/口径由有来源的model组装，renderer只布局，不从字符串正则重算阈值/健康。

旧消费者需要过渡时保留legacy lines为派生输出，但新status_card使用明确摘要。按数据版本建立producer→model→renderer→send契约测试，不能只改渲染fixture，让生产collector仍没有必需字段。

必需服务观测来自NODE-OBSERVABILITY的动态投影；serverosd停机旁路不能以活daemon为前置。现场原生source未支持的字段显示未测，不在renderer塞虚拟默认。

## 10. 投递仍遵循冻结payload

Observation→当前condition/episode→确定性PresentationModel→frozen payload+input/model版本→delivery intent→actual send/update→精确receipt。发送A时状态已变B只能确认A送达，B不能被旧ACK吞掉。DELIVERED不是用户看过或同意。

初次接入按现有baseline避免历史洪峰，但当前严重未解决问题必须在首次状态/主动查看可见。sender超时先核对provider receipt，不无限重发。schema/render拒绝时可降级为同一PresentationModel的短纯文本，不编造新根因；纯文本fallback也有payload identity和receipt。

正文、告警寄存和sender outbox若跨文件不可假称事务；复用现有恢复协议。daemon不可用时的独立health事件与回执按NODE-OBSERVABILITY专门边界写入，不能抢它的store。

## 11. 可测的完成条件

JSON合法只是格式；HTML preview只是设计预览。必须检查默认折叠态、390/360px、长中文名称、大值/0/unknown、正常/资源吃紧/关键服务失败/监控失明、native Feishu真实渲染以及相同fixture正文一致性。

5–10秒阅读验收：查看者不展开能指出最重要的问题或资源余量，知道影响/建议；不得用文件存在、测试数量或“首行可辨”替代。异常磁盘/关键服务不能折叠隐藏；保留source/revision与样例水印。

只在独立测试接收端投递演示卡，示例在标题/正文标记，故障注入用隔离实例，不断开真实Tailscale/停止并行任务制造截图。本轮未做这些运行验收。

## 12. 来源与迁移

本轮输入为用户截图和serveros@89887a4的实际status renderer/source；此前卡片已接线和历史修复不等于本次UI已经改善。CARDS-STYLE.md同步修订摘要/影响/免责声明/历史统计规则；旧词语禁令或固定五行测试应迁移为语义和视觉验收，不为旧测试绿保留难读卡。

实现复用runtime/card_source.py、presentation.py、cards.py、alerting/alert_digest/card_delivery与hwz-ops现有sender，不新建notifications-v2。外部卡片2.0组件必须按安装SDK/官方schema及实际客户端验证，不能把自制HTML自动换列当飞书保证。


## 13. 2026-09-24 live反馈后的产品收敛

当日飞书应用1实测确认：最近50条中35条为同题“⚠️ 需确认 · 3 项”，资源卡仍把UID/型号/loopback放首屏，并将“三块采集成功”错误表达成“环境可用”。因此本文件的低噪声/默认可见原则升级为硬产品合同：

- Current Status Card = 一张rolling card，按采样patch，不每小时新发；
- Incident = 一个episode一张card，NEW/ESCALATED/REOPENED才打断，STILL_OPEN不重复通知；
- 首屏区块固定为：我的资源、整机、关键基础设施、当前问题；
- 本人CPU/RAM/quota；整机CPU/RAM；系统盘/和数据盘/data必须直接可见；
- ServerOS/Tailscale/Laya/Shared Gateway等按逻辑服务显示，container/PID只在详情；
- process hygiene（age/orphan/external/unowned、无影响的单process高CPU/RAM）默认进入Diagnostic/Review，不自动push。

详细分类、基础设施探针与alert taxonomy见 [MONITORING-PRODUCT-ARCHITECTURE](MONITORING-PRODUCT-ARCHITECTURE.md)。


## 14. 2026-09-24 Session误归属与卡片生命周期

03:48 live卡把三组VS Code Server进程写成“Claude会话遗留进程组”。底层session来自loginctl/cgroup，不是A1 Agent Session；presentation不得从ref字符串猜Agent类型。没有AgentSessionProvider证据时只写“登录会话/用户会话/进程组”。

`RUNTIME_OUTLIVED_OWNER`、session age、orphan/unknown owner 等默认属于Hygiene/Diagnostic。CC/Claude Session多、后台待命、长期开着本身不构成用户告警。只有当前Work Item的继续责任、lease/effect或真实资源pressure被证明受影响，才聚合升级。

Feishu surface固定：

- **Daily Status Card**：每设备/每profile每天一张；首次sample创建，后续hourly sample原位patch，次日新建。
- **Incident Card**：一个episode一张；持续/证据/恢复原位patch，跨天不重复新发。
- **Diagnostic/Hygiene**：session/process历史、PID、code、seen count、积压数等主动查看。

告警卡首层禁止再显示 `seen N times`、alert ref、机器code/freshness枚举、历史积压总量。完整workflow-quality报警范围见 MONITORING-PRODUCT-ARCHITECTURE。


## 15. 共享节点与真正可操作 subject

飞书应用1面向当前 principal 时，默认只列“我的对象 + shared infrastructure + device级影响”。别人的 WIP/session/process 不能混入“需确认”清单，更不能给当前用户“确认是否清理”的建议。其它 principal 负载只有形成整机 pressure 时，以聚合设备影响出现。

Incident 标题必须指向真正 actionable subject。OS login session 只是 provenance；一个 login scope 里可同时有正常 Claude sessions、VS Code helper 和已结束 Run 遗留 runtime。没有 A1 AgentSessionProvider 证据不得写“Claude 会话”。

已知 Work Item/Run → runtime 关系时，标题优先写业务 owner，例如“SRV-0011 / RUN-0001 · 测试 serverosd 仍在运行”。此类 known-owner lifecycle drift 先核 active lease、consumer、socket/write-scope 和资源影响；卡片本身不授权 kill。

状态卡固定“一天一张、小时 patch”；incident 固定“一 episode 一张、状态 patch”。其它采样历史进 records。
