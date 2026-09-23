# Monitoring Product Architecture · 资源、基础设施、事件与低噪声告警

状态：2026-09-24 产品架构合同。本文基于当日对 Tokyo live ServerOS、飞书应用1、资源采集、Tailscale userspace daemon、Laya shared service、共享 LiteLLM gateway 与当前 alert registry 的只读实测。**DESIGN != LIVE**：本文不修改 timer、sender、threshold、systemd、container 或 Feishu 卡片。

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

~~~text
1. Current Status Card
   当前资源与关键基础设施
   一张滚动卡，原位patch

2. Incident Card
   一个真实incident/episode一张
   生命周期内原位patch
   仅NEW/ESCALATED/REOPENED等真正变化打断用户

3. Diagnostic View
   主动查看
   PID/code/ref/历史/原始probe/完整runtime与process hygiene
~~~

状态卡不得混入事件台账；告警卡不得混入完整诊断账本。历史 OPEN/RESOLVED 数量、seen次数、PID/UID/code 默认只在 Diagnostic View。

Feishu 当前支持 im.messages.patch 更新14天内 interactive card，因此“原位更新”是实际可实现路径，不需要每小时新增一条状态消息。

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

状态卡只显示 0–2 个当前最重要且不同故障域的问题。

没有问题就写“当前监测范围正常”；有任何 critical/important UNKNOWN 就不能全绿。

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

~~~text
condition
→ episode key = failure-domain + subject + symptom
→ NEW
→ ACK/SNOOZE optional
→ UPDATED (patch same card)
→ ESCALATED (may notify)
→ RECOVERED (patch same card; critical可简短恢复通知)
→ CLOSED
~~~

新消息通知仅在 NEW、ESCALATED、REOPENED、影响/动作需求发生实质改变，或用户配置的长期未处理 reminder 到期（例如日级，而非小时级）。

STILL_OPEN + 只有持续时间增加，不发新消息。

每个episode默认一张Feishu Incident Card，后续用 message patch 更新。状态卡也使用 rolling card；可按日轮换用于人类历史，但观察历史保存在records/receipts，不靠聊天堆24份。

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

示意仅说明信息层级，不是当前实时数字：

~~~text
Tokyo · 当前状态
02:30 采样 · 1项需注意

我的资源
CPU 1.3 核　RAM 15.5 GiB
/data 配额 59 / 500 GiB · 剩余 441 GiB

整机
CPU 15%　RAM 65 / 247 GiB · 可用 182 GiB
系统盘 60% · 剩余 18 GiB
数据盘 9% · 剩余 3.6 TiB

基础设施
ServerOS 正常
Tailscale 正常
Laya Ready
AI 网关 Ready

需要注意
<only real policy hits>

▸ 服务与链路　▸ 资源明细
~~~

不能再写“采集三项成功→环境可用”。

## 10. Ownership

- **A4 Runtime & Resource Management**：本人/整机资源、serverosd自身、runtime/Provider readiness、monitored-set projection。
- **A5 Fleet / Device Handoff**：Tailscale/remote peer/endpoint的任务相关readiness。
- **A9 Production Observation**：共享/生产服务的实际请求、错误、用户影响；不负责Node资源。
- **B10 Human Control & Notification**：Current Status / Incident 的人面、ACK/snooze/通知receipt。
- Ops scheduler/sender只负责 cadence/transport，不重新决定severity、incident identity或文案事实。

## 11. 当前实现差距

1. live资源卡仍使用旧summary，关键数值折叠；
2. live告警同一事件反复新发；
3. current alert registry被大量process-hygiene信号污染；
4. rolling status-card patch与episode-card patch尚未接入当前sender；
5. Laya/Tailscale/shared gateway还未统一成为monitored-set first-class projection；
6. Core独立监测与daemon故障receipt旁路仍需真实验收；
7. source checkout、installed artifact、running sender identity仍需按release receipt收敛，不能只改source声称上线。

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
