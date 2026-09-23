# Node Observability：资源、关键服务与 serverosd 自身

状态：2026-09-23 产品/架构修订，尚非运行实现。细化 ARCHITECTURE.md §17/20/21/26，不改变 frozen resident surface：serverosd仍是唯一Core常驻进程。Provider可常驻；独立检查采用已有OS监督器与有限任务，不新增monitoringd。

## 1. 问题、证据和边界

用户09-23 10:00卡片截图只在外层给UID/进程数、CPU型号和loopback入口，关键用量被折叠；没有必须服务或serverosd自身的可读状态。本轮读取serveros@89887a4的cards.py、card_source.py、presentation.py、daemon.py和现有设计：status_card逐区只取lines[0]，共享_footnote将“只读告警”加到日常状态卡。这是产品信息层级与数据结构问题，不是把字体变小即可解决。

新的日常监控同时回答：我的任务耗了多少资源、整机还有多少余量、共享网关是否完成约定功能、当前工作依赖的服务/网络入口能否使用、这些判断是否仍由健康的监测链生产。卡面详见BROADCAST-ALERT-DESIGN.md；实现验收详见MONITORING-ACCEPTANCE.md。

本轮只读资料与GitHub文档变更；不表示Tailscale/隧道/serverosd的新探针已经运行，也不从截图推断实际服务正常或故障。

## 2. 不把三类事实压成一个“绿”

Observation回答是否读到、何时读到、从哪个身份/端点与范围读到。Health回答目标是否满足当前声明的功能期望。Attention回答现在是否需要人处理、确认或只留详情。三者分开：

- 成功读到systemd的failed是“观测成功、服务失败”。
- TCP连上但鉴权失败是“网络到达、应用未可用”。
- 原数据过期是“当前不可判断”，不能沿用旧绿；关键当前服务失去必要观测可产生一个监控盲区事件。
- 无需求的休眠、按需服务没启动、未安排任务是正常待命，不因进程不存在告警。
- 旧架构文档/历史台账陈旧属于治理待办，不因stale一词自动转为实时运行告警。

总标题只概括当前必需范围。全部必需探针新鲜且通过才可显示“当前监测范围正常”；有未知项写“部分状态待确认”；已有确证异常不被unknown覆盖。永不写“三份JSON已读取，所以环境可用”。

## 3. 资源观测的具体口径

| 资源 | 默认摘要 | 来源与限制 |
|---|---|---|
| 本人CPU | 实际核数；有CPU配额时加占配额比 | cgroup/现有用户资源collector；无配额时明确占整机，不把320%当整机320% |
| 本人内存 | 使用GiB；有内存配额再给使用/限额 | 首选该scope权威cgroup计量；RSS求和可能重复共享页，标聚合口径，不误称精确独占内存 |
| 本人磁盘 | 真实用户quota使用/上限，或受管资料占用及实际可写余量 | 没有quota不造一个；目录大小用低频缓存与年龄，不每分钟du全HOME |
| 整机CPU | 使用率、有效逻辑核数；异常时加load/压力 | 型号名中的192-Core不是当前可用128逻辑核，也不是给本用户的配额 |
| 整机内存 | used/total与可用余量；异常时加swap换入换出/压力 | 使用MemAvailable等明确口径；缓存占用与不可回收内存不能混为泄漏 |
| 磁盘/文件系统 | 系统盘和承载workspace/state/artifacts的实际卷使用率+剩余容量 | 同一文件系统多个角色去重；inode、只读挂载、I/O错误异常时提升首屏 |
| 网络 | 有证据的关键链路/入口；需要时主接口收发速率 | 不把tailscale/bridge/physical相同流量重复汇总；无分母不报链路利用率 |
| GPU/温度/硬件错误 | 仅存在、可读且当前工作需要时展示 | 云机不可读就是不支持，不伪造温度/SMART；不为Laya默认增加GPU |

CPU/load、RAM、磁盘/容量是常驻摘要，进程排行/型号/UID/PID/线程/FD放详情。任何异常指标必须自动升到摘要，不让磁盘满或inode满躲在折叠面板。采样窗口与卡片发送周期不同，1小时发送不意味着1小时才检查。

## 4. 关键服务是动态投影，不是硬编码全机进程清单

从已有runtime registry、实际Provider installation/Binding、当前工作环境和明确用户长期需求生成 monitored set。复用现有数据源，不新建手工“第二服务注册表”。每项最少具有稳定身份、人读名称、用途/consumer、owner、期望运行条件、探针集合/范围、依赖、时间预算、告警重要性和恢复权限。

按期望分类：always-needed、needed-while-dependent-work、on-demand、maintenance/disabled。具体enum复用现有schema或有消费者的版本化扩展，不靠文档自动生效。

| 角色/候选实例 | 判断功能的证据 | 不应告警的情况 |
|---|---|---|
| serverosd / Core观测 | OS状态+IPC+采样推进+持久化成功+独立观察 | 经批准维护；但仍显示监测暂缺，不假称正常 |
| Tailscale | 本机认证/Backend状态、所需peer/路径及业务入口检查 | 手机睡眠、闲置peer、DERP中继但约定功能正常 |
| 当前隧道/Serve入口 | 进程或provider控制状态+监听/映射+从正确观察点验证业务 | 未被当前工作要求的临时隧道已结束 |
| CloudCLI/远程入口 | 独立HTTP/WS/认证可用性，具体访问链 | 用户UI断开不等于后台Session失败 |
| 通知发送器/BotMux或现用传输 | worker运行/有界队列、最近实际投递/失败、回执处理 | 静默窗口无应发消息、队列空；不为测试每分钟发送消息 |
| Quay | driver/worker适用状态、当前Task进展与超时，不只.quay目录 | 队列空为待命，不为保证“每天在跑”造任务 |
| Playwright/浏览器 | 已绑定profile、实际owner、连接/页面能力 | 按需浏览器未启动；不能读取cookie证明登录 |
| Laya/Memory/项目MCP | 进程、readiness、实际支持的查询与最近请求证据 | 可选Provider停用只影响依赖工作；Laya ready不证明答案质量 |
| Docker/测试数据库 | 需要它的活动环境与轻量原生健康 | 已完成测试容器合法停止 |

候选角色不等于本机都部署，也不要求每日把全部展开。默认展示被用户固定关注的核心/远程接入与当前依赖，异常优先；其他健康项折叠但有完整清单。新发现进程只生成候选，不自动获得监控敏感数据、控制或重启权限。

## 5. Tailscale与隧道分层

Tailscale读当前版本支持的原生status/结构化状态；字段适配有版本和fixture。重点分开本机daemon、登录/机器密钥有效性（仅有证据时）、control连接、必要peer可达和具体应用可用。

原生status的idle/no-traffic不是离线，direct/DERP/peer relay是路径方式不是通用好坏。中继仅在实际延迟/稳定性违反当前要求时产生相关偏差。不能用default ping一直等待direct把能用中继测成失败。

只ping明确授权且需要的peer；不扫描整个tailnet。ping是传输证据，不验证远端HTTP/SSH/WS的应用认证。Windows企业目标或手机端未作探测时明确“该端未验证”，不从Tokyo能连自家loopback推导手机可访问。

Serve/Funnel/SSH反向隧道/其他Provider分别记录原生持久化配置、实际连接/forward与endpoint。Serve服务内网与Funnel公网不可互换，监控不会自动开启Funnel、放宽ACL或重连更高权限账号。

endpoint至少分declared、actually_probed、reachable_from；HTTP正确服务/状态/最小内容匹配避免打错端口。被测应为鉴权API时401不能既算业务ready又标链路down；可作为“应用可达、身份未验证”中间证据。

TCP/进程检查不能替代流式入口。对CloudCLI等长连接只采用已授权无副作用的有限WS/stream检查，不发送业务指令。没有合法探针就显示未验证，不伪造全链通过。

## 6. serverosd 的四个健康维度

### 6.1 OS生命周期

OS adapter读取user unit状态、MainPID+启动标识、退出结果/重启次数和实际进程存在性。服务正常启动不等于IPC或采样正常；磁盘current版本不同只提示“待激活更新”，不直接宣布业务故障或自动重启。

### 6.2 控制接口

独立checker以有界时间询问现有最轻IPC health/status，验证响应schema、instance/启动标识和实际running revision。socket文件存在不是有listener；返回旧cache不表示进程在推进。接口未提供独立cheap health时在现有IPC补薄动词，不启动HTTP管理平台。

### 6.3 有效进展与持久化

健康必须包含最后成功采样cycle/序号、周期与耗时、各关键source最后成功/失败、最后成功持久化及关键错误。心跳不得仅由独立线程盲递增掩盖主采样循环卡死。循环在推进但某Provider失败仍是Core活着、该source缺口，不把一切归serverosd死亡。

只读检查最近写入receipt和错误，不为健康探针写业务DB。测试持久化失败用独立临时实例/存储故障注入，不填满真实盘。资源观察关注自身CPU/RSS/FD/队列饱和和异常增长；绝对进程存在数不作为故障。

### 6.4 观察serverosd的独立路径

```text
OS supervisor ───────────────→ process/exit/restart facts
有限外部check（不进serverosd tick） → IPC deadline + last-success progress
                                      ↓
                       daemon-health Observation
                                      ↓
           同一套纯detector/presentation + 既有sender/outbox
```

独立check复用现有周期sender/受管OS有限任务，可单独调用既有原生OS采集器；必须输出它自己的observer/provenance。它不能再调用一个实际依赖serverosd IPC的全量status来判断serverosd自身，否则故障路径循环。

serverosd停止时，保留已有业务/资源观测为“旧值/当前未知”，独立check仍给“监控核心停止/不响应/采样未推进”的事实。告警影响写“资源与服务状态无法继续确认”，不写“全部业务已停止”；只有其他独立证据支持时才说明某业务也失败。

## 7. daemon不可用时仍能记录和发出它的告警

当前daemon源码将部分alert/card回执写入视为daemon唯一writer。独立checker不得趁daemon不可用抢写该register，也不能坚持经死掉的IPC才能发送告警。

复用现有sender本地outbox/journal保存daemon-health事件intent与投递receipt，或在现有持久控制存储的独立namespace加入明确单writer并发协议。纯语义/episode规则仍共用，不能另造第二文案/第二业务工作库。重启后按stable condition+episode+event ID幂等并入可读总视图，不再发送同一故障。

若既有sender只能向daemon写回，实施必须先补这一可恢复边界；没有这个consumer就不声称“serverosd宕机也可报警”。依赖账户/secret/配置是独立已部署Binding，不能临时绕授权取密钥。

## 8. 谁再监控checker，以及真实故障域

同机OS可在daemon死后启动check，但不能保证整机断电/全网络中断时自己通知。服务和checker共用机器是一个故障域，不隐瞒这个边界。

本机checker/sender的最近完成、队列最老年龄、连续投递失败可由现有调度/日志观察；没有待投递事件或处于静默时间不能因last_sent旧判发送器坏。预计应完成的检查超过grace才告警。

只有注册了实际独立观察端/heartbeat接收与不同故障路径的发送通道，才可承诺离机失联提醒。优先复用已授权在线节点/现有监控服务，不默认新增云服务。经同一Tailscale故障路径得到的失联只能证明“路径或目标不可达”，不能区分Tokyo掉电与网络故障。未绑定离机观察准确标覆盖不足，不阻止本机核心监测落地。

## 9. 频率、预算与恢复

初始实现建议：关键进程/IPC/cheap probe以30–60秒级评估，非关键/外网低频，播报沿已有每小时计划；这些是候选预算，不覆盖实例策略。每项有timeout、per-cycle总deadline、并发上限、jitter和取消处理。全部慢探针不能把一个卡片构建拖成串行数分钟。

检测窗口必须覆盖该probe正常上界/启动宽限；例如60秒探测可用连续数次失败或约3周期无成功作为初始参数，验证后配置，不把该例值写成普适生产阈值。确证写失败/OOM/磁盘满可按影响立即告警；CPU繁忙但无饱和/承诺违约不自动红。

建议watchdog仅使用systemd原生机制，前提是应用确实按有意义进度发送WATCHDOG=1并经过测试；只写WatchdogSec不等于有watchdog。自动restart policy与告警独立，checker默认不restart，不给Tailscale修复开启无限循环。维护窗口有范围/到期，正在观察的他人容器不成为受控对象。

## 10. 数据模型、落位和实施

增加观测字段时复用现有Observation/Binding和receipt，不新建每服务手工status文件。按field记录source、expected/actual、observed_at、last_success、TTL、error category、consumer/dependency、policy ref、instance/revision。采集时间、渲染时间、provider接受时间和用户看到时间不是同一时间。

runtime/card_source.py仍组装而不二次测全机；service adapters拥有原生测量；presentation拥有意义/摘要文案；cards只布局；sender处理投递。独立Core检查走上述有明确owner的旁路，而不是额外全机扫描。provider原始错误/命令行、token、私网身份详情受最小披露控制。

CC按MONITORING-ACCEPTANCE.md实现相关P8/P9/P11，不修改并行MarketOps/LiteLLM容器、不为演示断开真实Tailscale、不停止目前唯一observer。单测和隔离故障注入后，真实节点只读验收，再按授权窗口验证受管升级。

## 11. 必须通过的产品反例

- 进程存在、IPC卡住：独立check能超时告警；其他资源保持旧值/未知。
- IPC回应但主loop不前进：不能显示Core正常。
- daemon挂掉且原回执写入依赖daemon：仍能通过合法outbox记下并投递，恢复不重发。
- Tailscale relay可用/手机休眠：不误报；声明隧道在但业务请求失败：显示具体缺口。
- renderer新运行但source旧：不能更新成绿色；重要source停采和历史文档陈旧区分。
- 只剩本机observer且主机断网：报告该检测/通知边界，不能承诺必达。
- 自身监控不触发模型推理、目录全扫描、原始日志外发、生产修改或双重restart。

参考：Tailscale官方CLI与Serve说明（https://tailscale.com/docs/reference/tailscale-cli ，https://tailscale.com/docs/reference/tailscale-cli/serve），systemd官方man/systemd.service.xml中WatchdogSec和Restart段（https://github.com/systemd/systemd/blob/main/man/systemd.service.xml），Google SRE Monitoring（https://sre.google/sre-book/monitoring-distributed-systems/）。上述上游行为不代表本机当前版本全部支持，adapter须核验安装版本。


## 12. 2026-09-24 监控域与alert分层

Node观测正式分为：①本人资源；②整机资源；③关键基础设施；④治理诊断。前3类形成Current Status；只有满足持续/影响条件的1–3类condition可以形成用户Incident。治理诊断默认不push。

当前Tokyo实际监控对象应通过Binding/registry/current dependency join包括ServerOS Core、Tailscale userspace transport、Shared Laya、Shared LiteLLM Gateway，以及真正当前需要的remote/browser/project runtime。Laya答案质量、offline sleeping peer、无流量provider、orphan/age/unknown-owner不属于运行事故。

资源告警不再按per-process finding数量直接投影：CPU看持续饱和/队列与影响，内存看MemAvailable/PSI/OOM，磁盘/quota按role-specific percent+absolute-free，swap要结合活动pressure。详细产品合同见 [MONITORING-PRODUCT-ARCHITECTURE](MONITORING-PRODUCT-ARCHITECTURE.md)。
