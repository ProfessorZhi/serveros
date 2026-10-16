# Monitoring Scope, Recovery, Work Calendar & Card Lifecycle

状态：2026-09-24 产品架构合同。本文补充 Monitoring Product Architecture，定义 ServerOS 到底监控什么、共享机如何避免监视/干预别人、哪些资源可以自动恢复、常规播报何时发送，以及飞书 Broadcast/Incident 卡片的最终生命周期。DESIGN != LIVE：本轮不修改 timer、service、registry、reconcile mode、sender 或现有卡片。

## 2026-10-17 Fleet 节点服务补充

本文件 2026-09-24 对工作日广播与当前 Python serverosd 的描述保留为历史产品基线。**当前完整 Fleet V1 目标**详见 [Fleet Node topology](architecture/FLEET-V1-TOPOLOGY-OPERATIONS.md)、[campaign plan](plans/plan-02-complete-fleet-v1-delivery-campaign.md)。每个授权 principal 的 C++ Core 负责自己声明的 service readiness 和有界恢复，切换生产前 Python/C++ 同一 unit 不能双写。

对 Windows 的 Tailscale/SSH 隧道、浏览器 local 127.0.0.1:16095 → Selkies Claude Web、Remote Desktop Control 只在实际 owner/profile 已声明且新鲜观察时进入受管集合：Windows Core 只恢复本地合法服务；远端 Selkies 由远端 owner Core 负责。进程、listener、SSH upstream、应用层/WebSocket readiness 不可互相代替。用户手工 disabled、NeedsLogin、AGENT_BUSY、未知或已耗尽预算必须拒绝恢复。

V13 当前发送/展示契约采用受众许可的一张 LIVE 监控+告警卡（固定 message_id PATCH、心跳 :00/:30 与独立告警事件游标）与独立时段 CORE REPORT；可在10分钟采样但不需10分钟发卡。样本统一 source journal，明确样本 TTL/覆盖缺口，Patch失败不算已通知。

## 1. Monitored Set 不是全机扫描

MONITORED_SET = PERSONAL_RESOURCE + PERSONAL_MANAGED_RUNTIME + ACTIVE_FOCUS_DEPENDENCY + STANDING_INFRASTRUCTURE + SHARED_CRITICAL_SERVICE + HOST_AGGREGATE - OTHER_PRINCIPAL_WIP。

看到某个 PID、容器、Session 或 systemd unit 不等于它进入个人监控面；只有 owner、standing dependency、Active Focus 或 device aggregate 让它相关时才进入。

## 2. PERSONAL_RESOURCE：我的资源

默认播报当前 principal 的 CPU 核等效占用、RAM 聚合占用、真实 filesystem quota（used / hard limit / remaining），以及可读时的 task/process/thread limit。

只有真实 OS/cgroup/filesystem quota 才显示“x/y 配额”。没有真实 CPU/RAM 硬配额时只显示占用；未来若配置 soft budget，明确叫“预算/提醒线”，不能冒充 OS quota。

进程数、线程数、Session 数本身不报警。只有接近真实 TasksMax/pids limit、失控增长、造成 resource pressure 或阻断 Work Item 时才升级。

## 3. PERSONAL_MANAGED_RUNTIME：我的工作运行态

监控当前 principal 自己的 Work Item Parent、required Agent Session、required child/Quay Run、task/session scoped runtime、own on-demand container、browser/test service、exclusive lease/write/socket scope。

Session 多、长期打开、idle/background、可 resume 都默认正常。

On-demand/disposable container 只有同时满足 owner 是当前 principal、没有 active Work Item/Run、没有 lease/writer、没有 consumer、没有 effect unknown、idle 超过声明 TTL、且不是 protected，才进入自动 maintenance 候选。cleanup 首先是 maintenance，不是 Incident。

## 4. ACTIVE_FOCUS_DEPENDENCY

只对当前 0–2 个 Active Focus 真正依赖的资源提高观测/告警等级：Parent continuation、workspace/worktree、required container/browser/profile、Quay child receiver、current endpoint/model/provider、publisher/sender、acceptance environment。

没被当前 Focus 使用的开发环境可以存在或停止，但不会仅因 idle/STOPPED 打断用户。

## 5. STANDING_INFRASTRUCTURE

个人实例长期依赖的基础设施目标包括 ServerOS Core、实际Binding声明的Tailscale transport（userspace或受管tailscaled，不凭名称假定部署形态）、CloudCLI / Remote Desktop / BotMux 等登记后的 standing access path、Shared Laya、Shared LiteLLM Gateway，以及其它正式声明的 standing dependency。

具体对象由 Registry + Binding + dependency resolver 解析，不由 renderer 硬编码名字。

## 6. SHARED_CRITICAL_SERVICE

共享 LiteLLM 等按逻辑 failure domain 呈现。app / PostgreSQL / Redis 可以分别探测，但默认只显示一个 AI Gateway；诊断层再展开内部依赖。

## 7. HOST_AGGREGATE：共享机只看少数聚合压力

整机默认只播报 CPU 总体利用率、异常时的 load/runnable/PSI、MemAvailable / memory pressure / OOM、swap activity、系统盘 / 的 used + absolute free、数据盘 /data 的 used + absolute free、inode，以及确实填盘时的 Docker data-root 总占用。

OTHER_PRINCIPAL 的具体 PID、Session、容器、Work Item 不进入个人卡。其它用户负载只有形成 device-wide pressure 时，以“其它工作负载”聚合显示。

## 8. Recovery ownership：先 supervisor，自愈失败才找人

ServerOS 产品拥有 recovery policy，但 resident serverosd 不是第二个 systemd。

R0 Native supervisor recovery：进程异常退出优先由原生 supervisor 恢复。当前实测 Tailscale user unit 为 Restart=always；Laya Shared 为 Restart=on-failure 且启动必须通过 readiness；serverosd 为 Restart=on-failure；容器遵循自身 Docker restart policy / registered lifecycle。普通短暂退出且成功恢复不为每次restart新建Incident，Daily Status按真实周期记录恢复；若期间发生了有实际影响的事故，不因R0最终成功抹掉其episode/影响。

R1 Bounded functional recovery：进程仍活着但 functional probe 持续失败时，只有明确注册且授权的资源才能生成有限 recovery intent。流程是 fresh failure → verify ownership/desired state/no conflicting maintenance → confirm native supervisor did not already recover → finite restart/reconnect worker → re-probe → recovery receipt。

每个资源必须有 recovery model、reconcile mode、max attempts、backoff/cooldown、success probe 与 effect scope。当前 Tokyo registry 默认 reconcile mode 为 observe，因此 R1 是目标能力，不是当前 LIVE 声明。

R2 Human required：auth/key 失效、配置语义错误、数据损坏、crash loop、write/effect unknown、多 writer/lease 冲突、需要扩大权限、reauthorize/redeclare/none 等不能靠不断 restart 解决，进入需要人的 Incident。

特殊边界：serverosd 自己卡死不能靠故障 serverosd 自己恢复；Tailscale peer 睡眠或 DERP relay 不是恢复对象；Laya answer quality 不是 restart 条件；Shared Gateway 不调用会触发付费 provider 请求的健康探针。

## 9. China Work Calendar：常规播报窗口

Monitoring observation、probe、records 和已授权自愈可以 24×7 运行；人类常规 Broadcast 只在工作时段投递。

当前个人实例使用 Asia/Shanghai / 中国大陆工作日历。日历不能简单按周一到周五推导。年度 authority 使用国务院办公厅公布的节假日/调休通知，形成 versioned BusinessCalendar projection。

WORKDAY = 普通周一至周五 + 国务院明确的调休上班周六/周日；NON_WORKING_DAY = 普通周末 + 法定节假日/调休休息日；UNKNOWN = 当前年份官方日历尚未导入或失效。

官方调休日优先覆盖 weekday/weekend 推导。年度 calendar declaration 至少保存 jurisdiction=CN-Mainland、timezone、source URL/document id、source revision/published date、workdays/non-working-days、imported_at/verified_at。

未来年份官方安排尚未发布或尚未导入时，不伪造官方工作日；使用显式 instance fallback，并在 Diagnostic 显示 calendar=UNKNOWN。

## 10. Broadcast schedule：工作日 08:00–20:00

常规资源/状态播报只在 WORKDAY 的 08:00–20:00（含两端）更新，推荐整点 08:00、09:00……19:00、20:00，即每个工作日最多 13 次 routine update。

20:00 之后、08:00 之前、NON_WORKING_DAY 都不做常规 Broadcast。国务院明确的调休工作日即使是周六/周日，也照常执行 08:00–20:00。

20:00 的 Daily Reconciliation / Requirements Alignment 提醒属于业务工作通知，不等于资源播报，由 Daily Cycle/B10 合同管理。

## 11. Broadcast Status Card：一天一张，最新完整，前序折叠

每个WORKDAY在首个允许的播报时点创建一张Daily Status Card；08:00错过可在后续工作窗口创建，之后整点只PATCH。不追发已错过的所有小时。

卡面默认：Tokyo · 今日状态 / 最后更新时间；最新状态完整展开（我的资源、整机、基础设施、我的工作环境、当前问题/今日自动恢复）；今日历史默认折叠，里面按小时保存 compact row。

每次更新先把旧 latest 压缩成 compact history row，再写新 latest。历史行只保留关键人读数字和状态，不复制 PID/code/raw probe。

若接近 Feishu card 大小/客户端限制，优先保留状态变化点和近期小时行；完整 observation 永远在 records。

20:00 完成最后一次 routine patch 后，当日卡不再因普通采样变化；次一 WORKDAY 创建新卡。NON_WORKING_DAY 不创建空白 Status Card。

这样用户可展开回看当天早先播报，但旧状态不会抢占最新状态首屏。

## 12. Incident Card：单独发，一个 episode 一张

Broadcast 和 Incident 完全分离。Status 只在“当前问题”放一行 Incident 摘要，具体 impact/recovery/decision 在独立 Incident Card。

生命周期：NEW send once；UPDATED patch；ESCALATED patch + optional interrupt；ACK/SNOOZE patch；RECOVERED patch；CLOSED final patch。同一 episode 不按小时、天或 timer 轮次重发。

Incident quiet-hours：P1/INTERRUPTING 真实高影响事故可以 24×7 发送，包括夜间和节假日；P2/ATTENTION 默认只在 WORKDAY 08:00–20:00 新发提醒，窗口外 condition 仍记录/自愈，未升级为 P1 则在下一个工作窗口送达；P3/REVIEW 不 push。

已经发出的 Incident Card 可在窗口外由机器 patch recovery/state，避免卡片显示过时；只有新的 interrupt notification 受 quiet-hours 策略控制。

## 13. 默认告警范围

个人资源：quota warning/critical、真实 Tasks/pids limit 接近耗尽、自己的 runaway runtime 造成明显资源影响。

整机：OOM/memory pressure、持续 CPU 饱和并伴随 queue/PSI/业务影响、系统盘/数据盘 percent + absolute-free 双条件危险、inode/I/O/read-only 风险。

基础设施：native supervisor 自愈失败、functional probe 持续失败且 R1 失败/不允许、crash loop、auth 失效、critical observability 失明、standing remote path 不可用。

Work Item：lost Parent continuation、lease/write 冲突、external effect unknown、accepted source/design drift 导致执行不安全、unexpected A7 FAIL、required delivery 持续失败。

默认不报警：Session 多、长期开着的 Claude/Codex/tmux/VS Code、安全 idle container、stale helper、单进程高 RSS、其它 principal WIP、backlog 很大、正常 Research 等待、自动恢复已经成功。

## 14. 当前实现差距

本段是实施核验清单，不声称2026-09-24此刻的installed/runtime状态。需分别验证monitored set真实Binding、R0 supervisor策略、R1授权/reconcile mode/worker/receipt、CloudCLI入口、工作日日历、daily卡更新消费者、Incident去重及空通知抑制。

早期“observe为主”“未接sender”是当时观察，实施前读最新Registry、package、running与消息回执；已实现机制优先复用。只修改设计不能启用自愈、扩大监控或代替真实客户端验收。

## 15. 验收

必须证明：当前用户卡不出现其它 principal 的具体 WIP；无 CPU/RAM 硬 quota 时不显示伪配额；own idle container 安全时 maintenance/安静存在；Tailscale/Laya/serverosd crash 被 native supervisor 拉起后只记 recovery history；functional recovery 只有获准资源有限执行；普通工作日只在 08:00–20:00 更新 Status；普通周末/法定休息日不生成 routine Status；国务院调休上班的周六/周日照常 Broadcast；同一工作日最多13次更新仍只有一张 Status Card；latest 完整可见、旧小时播报可折叠回看；P1夜间/节假日仍能到达，P2在quiet hours延迟而不丢；Incident详情不复制进Status；一个 Incident episode 不因采样或跨天重复 NEW。

## 16. 状态真相与是否打扰的独立验收

每日卡从当前资源/服务/依赖与关键coverage得出状态，不从“可发送告警条数”为0推导healthy。无须人处理的已知异常仍可见；diagnostic-only且无违反期望的迹象不能自动成为active anomaly。年久未清理不让未恢复异常消失，年龄也不把正常后台进程变故障。

新Incident发送前重新检查当前相关性、episode与窗口。不存在通知内容则不发送空绿色告警；已存在卡恢复则patch。P2窗口外发生且已恢复时保留历史，待发内容必须反映当前已恢复事实；是否额外恢复提醒沿已接受的receiver策略，不伪造未恢复事故。

calendar未知不冒充Mon–Fri官方工作日；只有明确fallback才执行该fallback，缺省先保留观察与配置缺口，不静默新增routine群发。此时真实P1仍按24×7策略处理。自动恢复必须有实际执行者/回执，不把observe-only显示成“系统正在修复”。
