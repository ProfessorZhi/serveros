# Monitoring Product Architecture · 资源、基础设施、事件与低噪声告警

状态：2026-09-24 产品架构合同。本文基于当日对 Tokyo live ServerOS、飞书应用1、资源采集、Tailscale userspace daemon、Laya shared service、共享 LiteLLM gateway 与当前 alert registry 的只读实测。**DESIGN != LIVE**：本文不修改 timer、sender、threshold、systemd、container 或 Feishu 卡片。

## 2026-10-17 Fleet V1 服务与来源验收补充

[Complete Fleet V1 plan](plans/plan-02-complete-fleet-v1-delivery-campaign.md) 补充三机每授权 OS principal 的 C++ Core 资源/服务观察、实际 CloudCLI + CC/Codex、Tailscale/SSH/Remote Desktop Control 和 Selkies 网络链。监控是来源可验证的状态投影，**不是** Core 的另一份业务/恢复数据库；原 Python serverosd 指的是当前/历史实现，向 C++ 的切换必须 shadow 和单一恢复写方。未经授权的 Windows 私有数据不得投送公司飞书接收方。

Windows 本地 127.0.0.1:16095 / SSH forward / 上游 Selkies- Claude Web / 交互流是分开的可用性层，HTTP 200 并不代表 WebSocket/会话已可用。Tailscale daemon、peer、实际应用健康也必须分开。只有声明为当前 Work 或 standing infrastructure 的设备/服务才进入告警，停用/未安装/未经观测不能错误显示故障或健康。

新版 V13 LIVE/CORE REPORT 的频率、重复快照账本、受众门、真实投递都以当前 [#233](https://github.com/ProfessorZhi/serveros/pull/233) 和 [#239](https://github.com/ProfessorZhi/serveros/pull/239) 所述版本化消费者/验收为准，早期 10 分钟广播口径不能覆盖后来明确的 :00/:30 LIVE PATCH 决策；**10分钟可作为内部采样节拍**，不等于每10分钟发一条消息。尚没有真实发送/当前 Core route 的独立证据则按 NOT_VERIFIED 报告。监控不得为了展示而重启服务或生成有副作用的 Task。

## 1. 当前 live 基线与问题

2026-09-24 只读实测：

- 飞书应用1最近50条里，35条标题为同一个“⚠️ 需确认 · 3 项”；状态播报按小时新发，告警timer每5分钟检查。
- 最新资源卡首屏仍把 uid / 进程数 / CPU型号 / loopback入口 当摘要，把真正资源余量藏进折叠；并把 mine/host/gateway 三块数据都读到写成“环境可用性无异常”。
- 当前 alert registry 共有 1065 条 OPEN；其中 HIGH_MEMORY_SUSTAINED=727、AGE_WITHOUT_ACTIVITY=138、EXTERNAL_PROCESS_TREE=87、HIGH_CPU_SUSTAINED=41、RUNTIME_OUTLIVED_OWNER=3。大量是治理/进程卫生信号，不适合作为手机通知。
- 当前 host 资源采集能够真实给出：本人 CPU 核等效、RSS聚合、XFS用户quota；整机 CPU/RAM；系统盘 / 与数据盘 /data 的 used/free/inode；swap/load。
- 当前 serverosd status 有独立 progress 字段：last_tick、tick interval、stale_after、failure count，但真正“Core死时还能告警”的旁路仍需实现/验证。
- 当前 Tailscale 是 userspace daemon，真实 socket 状态为 BackendState=Running、Self Online=true、Health=[]；peer offline/relay不能直接解释为故障。
- 当前 Laya shared service 通过 Unix socket Binding，--check 成功、--wait-ready 成功；质量 verified_languages=[] 是能力质量缺口，不是运行告警。
- 当前共享 LiteLLM gateway 的 /health/liveliness 与 /health/readiness 均 HTTP 200，readiness 明确 db=connected；不调用会触发 provider 付费探测的 /health。
- ServerOS runtime registry 当前声明四个 persistent protected runtime：serveros-control-plane（critical）、browser-stack、container0-litellm、container0-postgres（important）。Laya/Tailscale/共享网关还需要通过 Binding/Provider projection 进入统一 monitored set，而不是靠容器名猜。

这些数字只是本轮现场证据，不是未来阈值或成功标准。

## 2. 产品只保留三种监控表面

**每日状态播报卡（Daily Status Card）**回答现在怎么样；一次Status Update只是对当天卡的更新。**事故卡（Incident Card）**跟踪一个有实际意义的异常episode。**Diagnostic View**用于主动查看技术细节、原始finding与历史。这是三种人面，不是三个新状态库。

每日状态卡每个CN-Mainland工作日一张，Asia/Shanghai 08:00–20:00整点patch；非工作日不创建routine卡。Incident跨工作日/休息日保持同episode，依实际影响及通知窗口发送或patch。详见 [Scope/Recovery/Cards](MONITORING-SCOPE-RECOVERY-CARDS.md)。

健康、观测充分性、影响、处理责任、是否需要人、是否现在通知必须分开。不需人处理不是健康；静默不能抹去当前异常；诊断/历史finding数量也不能反推异常。尚未证明异常的生命周期线索只进诊断，不让整机永久黄灯。

卡片类型依据实际delivery intent、message/payload及调用路径确定，不能从绿色、标题或截图外观推断。Provider支持原位更新时优先使用；更新窗口/限制按当前实际API核验，超窗使用有连续关系的successor，不假称永远可patch。

## 3. Current Status Card 的四个默认区块

### 3.1 我的资源

回答“我自己占用了多少，还剩多少可用空间”。

默认直接可见：

- CPU：核等效；有真实个人/cgroup CPU quota时再显示占配额比；
- RAM：当前聚合 GiB；明确 RSS/cgroup口径；
- 数据配额：used / hard limit + remaining；
- 必要时只加一个“进程/任务异常”摘要，不把进程数本身当健康指标。

不在首屏：UID、完整process count、systemd unit列表、RSS采集说明。

### 3.2 整台设备

回答“Tokyo这台机器有没有资源压力”。

默认直接可见：

- CPU使用率；异常时补 load/runnable/pressure；
- RAM used / total + available；
- 系统盘 /：used% + free GiB；
- 数据盘 /data：used% + free TiB/GiB；
- 只有异常时上提 swap、inode、I/O/PSI。

系统盘和数据盘必须按实际 filesystem 去重；STATE_FS / ARTIFACTS_FS / DOCKER_FS 共用 /data 时不能画成三块不同磁盘。

### 3.3 关键基础设施

默认显示真正改变日常工作可用性的少量基础设施：

| 逻辑服务 | 默认证据 | 告警边界 |
|---|---|---|
| ServerOS Core | 独立OS状态 + IPC + tick推进 + 持久化 | 停止、不响应、tick停滞、持久化连续失败 |
| Tailscale | userspace socket BackendState / Health / self auth + 必要endpoint | 本机daemon/auth/control异常；peer只在当前任务明确需要时检查 |
| Laya Shared | Binding check + Unix socket readiness + service restart/queue | 声明需要且持续not-ready；质量未验证不作ops告警 |
| Shared LiteLLM Gateway | liveliness + readiness + DB/必要依赖 +真实窗口错误 | readiness失败、DB/关键依赖失败、持续请求错误；无流量不是异常 |
| Human/remote access | CloudCLI/RDC/其它当前Binding的应用探针 | 只有被声明为standing/当前依赖时才monitor |
| Browser / project runtime | runtime registry + functional probe | 只有desired且当前有standing依赖/active work时才通知 |

基础设施卡片按“逻辑服务”呈现，不按 container 一行一个。LiteLLM app/PostgreSQL/Redis属于一个 gateway failure domain时可合并；诊断层再展开容器。

### 3.4 当前问题

首屏保留改变判断的当前问题、影响与下一责任。默认可先展示少量重要不同故障域，但其余重要当前问题要有准确数量/详情入口，不能把Focus的0–2限制套到异常数量上。

绿色必须有足够的相关观测且没有已知active健康异常；关键未知/过期不能全绿。已知故障加部分未知优先显示故障并标缺口。普通diagnostic、已恢复累计和历史backlog不改变当前健康色。

当前状态与“自上次更新以来”分开：当前0异常、本周期恢复7项可以显示“当前正常，本周期恢复7项”；累计恢复数不能冒充本周期变化。没有影响证据写影响待核，只有实际检查后才能说无影响。

## 4. 资源状态与告警是两件事

所有资源可以持续显示，但只有真正需要人注意时才生成 Incident。

### 4.1 CPU

状态卡持续显示 CPU。

不因一次采样高或单个进程高就报警。Incident 需要持续窗口，并至少有“接近饱和/队列压力/实际依赖变慢”等证据之一。批量 HIGH_CPU_SUSTAINED process-group信号默认进入诊断/聚合，不逐进程推手机。

### 4.2 内存

整机判断优先看 MemAvailable、PSI、swap activity/OOM风险，不用“used%高”单独判故障。

本人RSS常驻展示，但“某个process group内存高”不是默认incident。只有造成本人quota/cgroup限制、host pressure、OOM或重要服务失败时升级。

当前几百条 HIGH_MEMORY_SUSTAINED 不能继续一条=一个告警episode。

### 4.3 磁盘与quota

这是应该主动告警的容量类：

- 本人 /data XFS quota：used%、remaining bytes、soft/hard limit；
- 系统盘 /：used% + absolute free；
- 数据盘 /data：used% + absolute free；
- inode：接近耗尽时独立异常；
- read-only/I/O错误优先级高于普通容量warning。

阈值必须按 mount role 配置，采用“百分比 + 绝对剩余”双判据；不能用同一个85%规则同时解释50GiB系统盘和4TiB数据盘。具体Tokyo阈值属于实例policy，不由renderer硬编码。

### 4.4 Swap / load

Swap有少量使用正常。只有高占用、持续换入换出或内存pressure伴随影响时报警。

Load是调度队列，不等于CPU%。128核节点的load 40不能仅按数字“40很高”告警；结合核数、runnable和CPU/PSI解释。

## 5. 基础设施的监测原则

### ServerOS Core

critical。外部checker必须不依赖故障serverosd的同一IPC链。至少判断：

~~~text
OS process/unit
IPC deadline
tick/progress是否推进
last successful persistence
running revision
~~~

PID alive 不能等于 healthy。

### Tailscale

默认只判断本节点 transport 身份：

~~~text
BackendState
HaveNodeKey/auth
Self online
Health[]
key expiry
~~~

不把手机睡眠、Windows当前不在线、peer走DERP/relay但功能正常、peer没流量当故障。只有当前 work/standing remote path明确需要某peer/endpoint时，再做该peer/application probe。

### Laya

监控 service availability，而不是模型“聪不聪明”：

~~~text
binding valid
checkpoint pin/digest present
service process/unit
unix socket ready
queue saturation/rejections
request deadline failures
~~~

verified_languages=[]、答案质量低属于 G1.5/B5 evaluation，不属于运行incident。

### Shared LiteLLM Gateway

按一个逻辑服务监控：

~~~text
app liveliness
app readiness
PostgreSQL connectivity
Redis/其它必要依赖（若实际声明）
request/error window when traffic exists
~~~

不调用付费provider health。某模型“没流量”显示灰/空，不告警。只有在真实调用存在时才计算provider/model错误影响。

### Browser / container0 / project environment

不是机器基础设施就不要永远占状态卡。当前runtime registry可以声明persistent/important，但是否push还要结合 expected now、standing dependency、active work dependency 和 functional impact。否则只在“受管环境”详情展示。项目环境异常不能把整台Tokyo标题变红。

## 6. Alert taxonomy：只通知真正需要知道的变化

### P1 / Interrupting

典型：

- serverosd停止/IPC失败/采样长期不推进导致监控失明；
- 系统盘/数据盘/quota进入critical容量；
- host内存pressure/OOM风险；
- shared gateway readiness/DB关键依赖持续失败并影响消费；
- Tailscale本节点认证/daemon失败且远程入口是standing dependency；
- 其它明确critical service当前不可用。

### P2 / Attention

典型：

- 容量进入warning；
- CPU/RAM压力持续但尚未造成硬失败；
- gateway真实请求错误率持续升高；
- Laya/重要Provider持续degraded；
- monitoring coverage对重要服务变unknown；
- credential/key expiry临近且会影响已声明服务。

### P3 / Review-only

默认不推飞书incident：

- AGE_WITHOUT_ACTIVITY；
- ORPHAN_PROCESS_TREE；
- EXTERNAL_PROCESS_TREE；
- EXTERNAL_SYSTEM_PROCESS_TREE；
- RUNTIME_UNOWNED；
- 单process/group的 HIGH_CPU_SUSTAINED / HIGH_MEMORY_SUSTAINED，除非资源/业务影响成立；
- 已结束session遗留进程组且无资源/安全影响；
- unknown owner/cleanup candidate；
- stopped on-demand container；
- 历史积压、resolved count、seen count。

这些进入 Diagnostic View 或每日/每周 hygiene review；必要时以“3个遗留进程组共占X GiB/X核”聚合成人类问题，而不是三张或三段同质告警。

## 7. Episode 与通知生命周期

condition的身份与持续过程独立于投递。沿现有episode关系管理NEW、UPDATED、ESCALATED、RECOVERED、CLOSED和必要ACK/SNOOZE；这些词不要求新建状态机服务。一个episode对应一张事故卡，重复观测与跨天不新发。

NEW/ESCALATED/REOPENED或影响/实际所需动作改变可产生通知候选；是否打断再看受众、重要性和窗口。只有age增长不重新发送；已接受的长期reminder策略可以另提醒，但不制造新事故身份。ACK只表示知晓，snooze只改变提醒，不代表健康或授予重启权限。

**没有可提醒的新episode时，不创建“没有需要处理的变化”的空事故通知。** 定时健康播报仍可更新每日状态卡；主动诊断可以返回“当前无待处理事故”，但该句不是全机健康证明。恢复更新原卡/当日变化，不另发每次probe成功的消息。

反复失败/恢复需表达不稳定，避免每次切换重新NEW。稳定观察窗口、合并间隔和升级阈值由目标服务/影响决定，不能从示例的30分钟变成全局默认。窗口外已恢复的P2在下次投递前重核：不发送仿佛仍在故障中的旧正文。

## 8. Incident Card 首层

首屏最多回答：

~~~text
什么坏了
持续多久 / 趋势
影响什么
已经验证到哪一层
下一步是什么
自动动作有没有发生
~~~

PID、异常code、alert_ref、raw timestamp、seen count、probe URL、完整container列表进入详情。

## 9. Status Card 首层示意

以下是产品状态示例，不是Tokyo实测或固定模板：

| 情况 | 首层表达 | 通知行为 |
|---|---|---|
| 关键观测完整、无active异常 | 当前正常；我的资源/整机/基础设施可读 | 工作窗口更新当天卡 |
| 确有异常、无需人操作 | 有异常；影响已核/待核；系统处理或观察责任 | 不因无需人就变绿；是否新Incident按影响 |
| 异常影响当前工作 | 具体依赖、影响和下一动作 | 依P1/P2/受众策略 |
| 关键状态过期/缺失 | 无法完整判断；最后可靠检查与缺口 | 不以空异常列表报健康 |
| 当前恢复、此前有异常 | 当前正常/正在确认稳定；本周期恢复变化 | patch原episode，不每probe新发 |
| 只有正常诊断/历史记录 | 正常资源状态；历史下沉 | 不因几百条finding假黄/假红 |

每日卡保留我的CPU/RAM/真实quota、整机聚合压力与关键存储、声明的基础设施/工作依赖、当前重要情况。未知分母不造百分比；自动处理中必须有真实执行/receipt；未实测影响不统一写“无影响”。前序播报压缩进今日历史，完整证据留records。

## 10. Ownership

- **A4 Runtime & Resource Management**：本人/整机资源、serverosd自身、runtime/Provider readiness、monitored-set projection。
- **A5 Fleet / Device Handoff**：Tailscale/remote peer/endpoint的任务相关readiness。
- **A9 Production Observation**：共享/生产服务的实际请求、错误、用户影响；不负责Node资源。
- **B10 Human Control & Notification**：Current Status / Incident 的人面、ACK/snooze/通知receipt。
- Ops scheduler/sender只负责 cadence/transport，不重新决定severity、incident identity或文案事实。

## 11. 当前实现差距

实现差距必须绑定被读取的版本，不复用早期“live仍是旧代码”的结论代替现在的安装观察。

本轮设计收敛读取serveros `c5d118cc44629e7cb264b73e3026d8ac3abbf7dc`：`runtime/cards.py` 的alert路径存在“没有需要处理的变化”空摘要，以及低优先级/恢复/历史计数；`status_card`是另一个入口。`notification-contract.yaml`已把RUNTIME_OUTLIVED_OWNER作为INFORMATIONAL处理。不能说该码仍未改，也不能仅因此证明sender抑制空事故通知或每日卡健康语义正确。

需验的接缝：真实message/payload目的→alert/status对应调用者；empty-incident send suppression；health/coverage与attention分离；当前变化而非历史累计；同工作日patch与跨天episode连续性；当前monitored set/功能probe；独立Core故障投递。是否安装、实际启用与客户端通过必须另读相应receipt，本轮未改变runtime或发测试消息。

旧§1、§18、§22的现场读数保留为当时证据，不当当前资源/进程状态。source修复、发布cachebuster、安装、running与实际行为各有不同证明。

## 12. 验收

至少必须真实证明：

- 390/360px不展开即可读：本人CPU/RAM/quota、整机CPU/RAM、系统盘、数据盘、基础设施状态；
- 单次高CPU不报警，持续压力才有episode；
- 700个process memory finding不会产生700个用户incident；
- orphan/age/unknown-owner默认只进诊断；
- serverosd死/IPC挂/loop停滞各自能被独立检测；
- Tailscale peer睡眠/relay不误报，本节点auth/backend故障会报；
- Laya ready失败和Laya答案质量差被分成ops vs eval；
- gateway无流量不报警，readiness/DB失败会聚合成一个gateway incident；
- 同一incident 1小时不变不会新增Feishu消息；
- status card一小时更新只patch当前卡；
- escalation/recovery不会被旧delivery receipt吞掉；
- native Feishu真实截图通过，而不仅是HTML/JSON测试。


## 13. Session Hygiene 与 Agent Session 不是一回事

2026-09-24 03:48 live 告警再次暴露一个明确语义缺陷：三条 `RUNTIME_OUTLIVED_OWNER` 卡片标题显示为“Claude 会话 15413/19825/150”，但对应 PID 实际分别是 Vince/Kai/Zhengji 的 **VS Code Server** 进程组。源码反查确认：

- runtime attribution 的 `session_ref` 来自 cgroup/loginctl 的 **OS login session**（例如 `session-15413.scope`）；
- `graph.session_states()` 调的是 `loginctl` session facts；
- presentation 却把 `runtime-group:claude-session:*` 硬编码渲染为“Claude 会话”。

因此这是 attribution/presentation identity mismatch，不能让用户据此判断 Claude native session 是否泄漏。

硬规则：

~~~text
OS login session
≠ Claude/Codex Agent Session
≠ tmux session
≠ BotMux session
≠ Work Item Parent Session
~~~

只有 AgentSessionProvider / A1 的真实 native session identity 能让卡片使用“Claude 会话 / Codex 会话”等名称。只有 loginctl/cgroup 证据时必须写“登录会话 / 用户会话 / 进程组”，不得猜 Agent 类型。

`RUNTIME_OUTLIVED_OWNER` 本质是 lifecycle hygiene observation：login session 正在结束而进程组仍在运行并达到通用资源阈值。它不证明：
- 进程不该存在；
- 进程属于 Claude；
- Work Item owner 已结束；
- 资源泄漏；
- 应该清理。

VS Code Server、tmux、daemonized helper、remote IDE 等均可能合法跨越原 login session。

因此该 code 默认属于 **P3 Review/Diagnostic**，不进入用户 interrupting/attention face。只有 join 到额外高质量证据时才升级，例如：

- 当前 Active Focus/Work Item 明确声明该runtime应随session结束而停止；
- runtime持有冲突lease/write lock，阻断新的合法工作；
- 聚合资源导致本人quota/host memory/CPU/disk pressure；
- 受保护服务或当前执行路径受影响；
- 真实cleanup policy明确要求停止且当前状态违约。

即使升级，Incident按逻辑故障域聚合，不逐session/pid推送。

## 14. CC / Agent Session 的默认健康语义

“Session很多、长期打开、后台待命、未归档”**默认都不是故障**。Session管理与运行监控区分：

~~~text
active/busy        正常
idle/background    正常
dormant/resumable  正常
archived           正常
closed             正常
unknown            需要观测，但不是自动故障
~~~

只有以下情况可能成为 workflow/session incident：

- Active Focus Parent 应继续但真实 Agent Session 意外消失，且无可恢复checkpoint/receiver；
- 同一 Work Item/scope 出现两个并发writer/冲突lease；
- Session/worker停止但外部effect状态 unknown，存在重复执行风险；
- Parent退出时仍持有独占资源/lease且没有handoff；
- 当前工作要求的session/provider连续不可用并实际阻塞 Work Item；
- session/process资源聚合造成真实资源pressure。

“24小时没动”“进程还在”“有很多 /resume 条目”只进入 maintenance/diagnostic。

## 15. Workflow Quality Alerts

ServerOS 还需要一类不同于机器资源的 **workflow integrity incident**。只针对当前 Active Focus / 真实交付义务，而不是 backlog 全库。

### 应通知

- Active Focus Parent 丢失继续责任：无活session、无合法checkpoint、无receiver；
- Work Item 存在未决 external effect/commit-unknown，重试可能重复产生副作用；
- 同一 write scope 出现冲突 writer / lease violation；
- required child Run/Quay Run 结束但结果无法被 Parent 接收，且阻断当前关键路径超过策略窗口；
- A7 对 implementation acceptance 出现**非预期产品 FAIL**，按政策要求停自动修改并需要人决定；
- 需求/AC/source revision 发生实质漂移，使当前 accepted design/plan 失效，而执行仍准备继续；
- 用户明确要求的发布/发送到期但 publisher/sender 连续失败，且没有安全fallback；
- 20:00 对账所需 B1/B2 pipeline 在 expected cutoff 后仍无法形成可用 reconciliation packet，且这会阻断当晚对账。

### 默认不通知

- GPT review 给出 CHANGES_REQUIRED，但 Parent 可在预算内自动修订；
- Quay 一个 child 失败，但 Parent 有合法重试/替代且 Work Item 未阻塞；
- research 暂无结论但预算/时间仍充足；
- Work Item 正常等待已知依赖；
- background Session 多；
- Activity 改计划；
- Work Item overnight 完成；
- backlog 有很多未选 Requirement；
- Daily Reconciliation 没有变化。

这些进入 Workbench/Current Focus/Daily Reconciliation，而不是 Incident。

## 16. 一天一张 Status Card

“一天”指本实例的CN-Mainland工作日。每device/profile第一轮获准routine update创建当天卡；08:00–20:00后续整点只patch；错过08:00可在下一允许时点建当天卡，不追发全部漏掉的小时。次一工作日新建，休息日不创建空白routine卡。

最新资源/服务/工作依赖及重要问题完整可见；前序状态压缩入今日历史。Incident只在状态卡有简短摘要和引用，正文仍在独立episode卡。全量观察属于records，不靠聊天条数保存。

Provider patch能力和限制以实际版本/接口验证，不将历史API窗口说明当永久承诺。具体投递以 [Scope/Recovery/Cards](MONITORING-SCOPE-RECOVERY-CARDS.md) 为唯一日历/生命周期细则。

## 17. 一个 Incident 一张 Card，不按小时/天重复

Incident episode 跨小时、跨天都继续 patch 同一张卡，直到恢复/关闭；不因为跨零点重开 incident。若 episode 超过传输平台可 patch 窗口，才创建 successor card，并显式链接/标注同一 episode continuity。

一个 episode 的 face 只显示：
- 真实subject；
- 当前状态；
- impact；
- age/trend；
- 已验证事实；
- next action / whether automation acted。

不显示：
- seen 3518 times；
- alert_ref；
- raw producer code；
- freshness内部枚举；
- “机器严重度 WARNING (medium)”；
- “建议的效力 建议，不是授权”重复声明；
- 667项低优先级/108项历史积压。

这些全部进入 Diagnostic View。

## 18. 当前 RUNTIME_OUTLIVED_OWNER 三条 live 结论

本轮只读核验：

| live card | 实际 root PID | 实际对象 | 当前聚合资源 |
|---|---:|---|---:|
| “Claude会话15413” | 1315723 | Vince VS Code Server | 约1.22 GiB RSS，约26.5%单核等效CPU |
| “Claude会话19825” | 2021489 | Kai VS Code Server | 约1.73 GiB RSS，约0.6%CPU |
| “Claude会话150” | 28541 | Zhengji VS Code Server | 约0.96 GiB RSS，约0.3%CPU |

这些资源读数说明它们可以参与容量诊断，但**不能仅凭该读数和login session closing就形成“Claude遗留进程”告警**。本轮没有执行停止/清理，也不判断这些VS Code Server是否应该退出。

实现时应同时修：
1. producer/reference naming，避免把login session伪装成Agent Session；
2. attention policy，把 `RUNTIME_OUTLIVED_OWNER` 默认移出alert face；
3. aggregation，把session/process hygiene放到Diagnostic/Hygiene；
4. 只有join真实Work Item/resource impact后再升级为Incident。


## 19. Shared-node principal scoping

Tokyo 是共享开发节点。监控必须区分三种 owner/scope：

~~~text
PERSONAL
  当前 principal 自己的进程、quota、Work Item、Session、runtime

SHARED
  多人共同依赖的基础设施
  ServerOS Core / shared gateway / Laya / Tailscale transport 等

OTHER_PRINCIPAL
  同机其他人的 WIP / runtime / session
~~~

面向当前用户的 Status/Incident 默认只展示 PERSONAL、SHARED 和 DEVICE 级影响。其它 principal 的具体 PID、Session、Work Item、建议清理动作不得混入当前用户的“需确认/需处理”列表。

如果其它用户的负载已经造成整机 pressure，可以展示“整机资源压力，其中一部分来自其它用户工作负载”之类的聚合事实；跨 principal 具体明细只进入 node-admin / authorized diagnostic surface。

## 20. Actionable subject：最小可处理对象，而不是 login session

Incident subject 必须是实际可采取下一步的最小对象：

~~~text
runtime instance / process group
Work Item / Run
logical shared service
filesystem / quota
transport endpoint
verified Agent Session
~~~

OS login session 只是一条 provenance/containment 边，不能默认成为 Incident subject。

真实 15413 案例说明：同一个 OS login scope 内可同时存在仍正常活着的 Claude/native sessions、VS Code/remote IDE helper，以及某个已经结束 Work Item/Run 留下的测试 runtime。因此不能把整段 login session 命名成“遗留 Claude 会话”。

有足够 owner 证据时，应写成类似：

~~~text
SRV-0011 / RUN-0001 · 测试 serverosd 实例仍在运行
~~~

并分别列真正的 runtime/socket/resource evidence。

## 21. Known-owner lifecycle drift

“owner 已知、Run/Work Item 已结束、runtime 仍活着”与 unknown/orphan 不同。

目标语义：

~~~text
KNOWN_OWNER_LIFECYCLE_DRIFT
owner: Work Item / Run / test scope
actual: runtime still running
expected: derived from accepted lifecycle contract
~~~

它默认是 Review/Diagnostic，不是自动 cleanup 许可。升级前必须核：
1. owner identity 与 Work Item/Run revision；
2. owner lifecycle 是否 terminal/parked；
3. active lease / writer / lock；
4. 是否仍有合法 consumer；
5. 当前资源影响；
6. socket/path/write-scope 是否冲突；
7. stop/fence/cleanup 是否在授权范围。

可能升级为 workflow incident 的情况：已结束 Run 的 runtime 持续制造资源压力、竞争同一 socket/write scope、阻塞新 Run、仍持独占 lease，或会造成重复 effect。IDE/tmux/helper 合法跨 login、其它 principal 正常 WIP、没有资源/写域/lease/effect 影响时不升级。

## 22. SRV-0011 / RUN-0001 作为真实架构样本

后续更深的只读现场核验表明，15413 不能整体解释成“遗留 Claude 会话”。当前 principal 范围内可识别到已结束的 SRV-0011 / RUN-0001 测试 runtime：多个测试 serverosd 实例仍存活，使用 Run 自己的测试 registry/state，不是 live production state；存在实例共享同一测试 socket 路径的迹象；每个实例持续占用约半 GiB 级内存。同时 15413 login scope 内仍有正常活着的 Claude 会话。

这类对象是 known-owner lifecycle drift 的代表：归属不未知，login session 本身也没有整体结束；真正要判断的是那个已结束 Run 的测试 runtime 是否仍应存在。

本合同不批准停止这些实例。任何后续处置必须重新获取 fresh snapshot，并检查 Work Item/Run current state、active lease、writer/socket scope 和授权。stale PID、PID file 或历史 card 均不足以 kill。

## 23. 高质量工作流什么时候报警

工作流告警只针对“当前承诺无法安全继续或无法正确完成”，不是“后台东西很多”。

### P1 / 立即打断
- Active Focus 出现并发 writer / lease violation，继续有数据破坏风险；
- external effect commit-unknown，自动重试可能重复真实副作用；
- 当前必需生产/交付路径明确高影响失败且需要人决定；
- Work Item Parent 与 continuation 同时丢失，且无 checkpoint/receiver 可恢复。

### P2 / 需要关注
- A7 unexpected product FAIL，策略要求停止自动修改；
- accepted Requirement/source/design/plan 实质漂移，当前 Run 不能安全继续；
- required child/Quay Run 长期无法被 Parent 接收并阻断关键路径；
- required publisher/sender 持续失败，影响承诺交付；
- 20:00 前 B1/B2 无法形成可用 reconciliation packet，阻断当晚对齐；
- known-owner lifecycle drift 经核验确实造成资源、lease、socket/write-scope 或当前 Work Item 影响。

### 不报警
- 正常 Research 暂无结论；
- GPT review 要求改计划且可自动修；
- 一个可替代 child Run 失败；
- Work Item 正常等待已知依赖；
- Activity 调整；
- Quay 暂时没用；
- 多个 Claude session 后台待命；
- session 很多但资源/写域无问题；
- backlog 很大；
- 当晚 Work Item 提前完成。

## 24. Daily Status Card：一天一张，增量更新

本节与§16同一产品：Daily Status Card = 每日状态播报卡，Status Update = 更新动作。按官方工作日和08:00–20:00窗口维护一张当天卡，latest展开、此前compact rows折叠。没有可报告的新事故不影响合法的routine状态更新；休息日没有routine卡也不意味着停止监测或P1通知。

不要另建“播报卡”“状态卡”“每日卡”三套identity或sender。历史daily卡冻结不承担夜间最新状态，夜间持续episode由Incident卡准确更新。

## 25. Incident Card：一个 episode 一张

~~~text
NEW        → send once
UPDATED    → patch
ESCALATED  → patch + optional interrupt
ACK/SNOOZE → patch
RECOVERED  → patch
CLOSED     → final patch
~~~

Incident 不按整点、天或 timer 轮次重复发送。只有平台 patch 窗口到期且 episode 仍持续时，才创建 successor card，并保持同一个 episode continuity。

因此同一 condition 即使被采样数千次，也不允许数千次 NEW，也不允许每小时/每5分钟重发。

## 26. Monitored set / recovery / work calendar specialization

The canonical specialization for what ServerOS monitors, shared-node principal scoping, native-supervisor vs bounded functional recovery, the CN-Mainland work calendar, the 08:00–20:00 routine broadcast window, and the final latest+collapsible-history Daily Status Card is MONITORING-SCOPE-RECOVERY-CARDS.md.

Where earlier examples imply all-visible-resource monitoring, hourly new status messages, weekend-only inference, or routine broadcasting outside the work window, the specialized contract supersedes them.
