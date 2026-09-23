# 监控产品专项：接线、迁移与验收

2026-09-23。此文细化CC-EXECUTION-PLAN的P8/P9/P11，不替代整个计划，不新增一套任务状态或21个卡片。输入是用户实际10:00截图、BROADCAST-ALERT-DESIGN、NODE-OBSERVABILITY、CARDS-STYLE及Ability PRODUCT-EXPERIENCE-CONTRACT。

## 1. 本轮静态证据与准确边界

基线serveros@89887a4：

| 已读源码/反馈 | 直接证据 | 实施定位 |
|---|---|---|
| 用户截图 | 首屏UID/型号/端口，关键用量折叠，重复说明和告警尾注 | 以默认折叠态读懂资源为验收，而非“三块首行有字” |
| runtime/cards.py::status_card | 只外露lines[0]；同名panel；共享_footnote | 新明确摘要模型/区块，状态与告警chrome分开 |
| card_source.py::_host_section等 | 先构造CPU规格等身份行 | 业务摘要与详细规格分字段，不只是换数组排序 |
| card_source.py::_default_run | 该函数subprocess.run未显式per-call timeout，nonzero一概作为无数据 | 检查整个调用边界，补有界读取、按真实schema处理带有效payload的状态码；不能声称已复现卡住 |
| runtime/daemon.py | daemon拥有关键回执writer；聚合视图不少通过daemon IPC | 自身检查/告警不能依赖故障daemon写回，设计独立receipt边界 |
| CARDS-STYLE旧R5/R6/R7/R8 | 建议禁操作词、因果词禁令、统一未发现影响、历史计数露面 | 改为权限/证据/用户判断测试；保留必要安全保证而非词表模板 |

这些不是对当前安装/发送进程状态的新测量。此设计轮没有调用远端脚本、改变服务、发送卡片、停止Tailscale或运行native视觉测试。

## 2. 先确认实际consumer与写入范围

读取当前GitHub/main、相关worktree、实际安装release与sender引用。复用runtime/card_source、presentation、cards、probes/inventory/health、card_delivery与已有ops monitor_card/alert_digest_card，避免从旧main复制一套。

SRV-0011/SRV-0005/MarketOps/LiteLLM并行任务仍受保护。真正同文件冲突由明确作者交接commit/interface；可以在独立输入模型/测试/文档并行，但不能接管dirty树。源码与部署同目录的ops变更按正式激活处理，不因为“一行文案”绕过运行回读。

## 3. 依赖顺序与每包完成物

### M1 明确默认可见产品模型

在实际snapshot/presentation协议兼容增加摘要指标、服务状态、观测覆盖与详情；字段有producer和consumer，旧lines只保留兼容输出。严禁在cards正则解析中文lines重算状态。

建立固定合成数据：正常/高CPU且仍有余量/内存压力/磁盘或inode满/本人quota满/缺数据/时钟偏移/重要服务坏/监控自身停更。每个源字段追到真实口径，旧三JSON读取不能给全局green。

完成物：默认可见内容断言与caption/schema测试；不必先部署服务才验证UI语义。原代码未知不伪造默认值。

### M2 资源和必需服务观测

复用原生OS/Provider测量，新增动态必需服务投影：用途/owner/expected条件/endpoint与observer vantage/依赖/预算/TTL。Tailscale、当前隧道、CloudCLI等来自实际Binding，不硬编码every-service-always-up。

核对Tailscale安装版本的status/ping/Serve只读surface；只检查授权peer/endpoint，区别relay、idle、控制面失联、应用失败、鉴权待验。正常readiness probe不发模型付费推理；Laya质量测评不是每分钟健康探针。

完成物：真实采集小样本+fixture回归，能输出人读名称及明确状态/来源；不为测试开公网或改企业网络。

### M3 serverosd独立监测与故障投递

复用OS有限任务独立读unit、IPC deadline、真实采样进展与最后持久化；不依赖故障daemon的同一IPC查询链，不假设PID即healthy。cheap health接口与实际cycle相连，别用另线程heartbeat遮盖卡死。

复用现有sender/outbox或其独立namespace记录daemon-health intent与receipt；不抢serverosd自有寄存。恢复后事件幂等并入，不产生重复警报/抢锁。systemd watchdog是可选原生补充，应用/单元实际支持后才启用。

完成物：隔离实例中kill/hang/IPC responsive但cycle停滞/持久化失败/回执路径不可用的测试；daemon坏时同一纯presentation仍可输出正确告警。整机断网和off-host未绑定的限制准确公开。

### M4 卡片与投递消费者

状态卡默认直接显示CPU、RAM、容量和关键服务。告警默认显示症状/影响/持续/建议。去重复标题与scope英文key，把uid/型号/本机地址放详情，状态卡无“只读告警”。正常历史积压不显示。

同一PresentationModel生成Feishu JSON和HTML预览；read-only text fallback也来自相同模型。不另造ops renderer。保留冻结payload/episode/输入版本与delivery receipt；render失败、send失败和业务故障不同。

完成物：390px/360px/桌面默认折叠预览与授权Feishu测试对话native截图，截图标样例/原始证据脱敏；当前严重问题不隐藏，所有状态名称及单位一致。

### M5 动态服务/故障域和能力联动

同一故障依赖有证据时聚合，不因serverosd丢观测为几十主体各发一条，也不因此说几十服务都死。on-demand/维护/有工作依赖期间期望明确；sender quiet hours和empty queue不是故障。

关键能力提供依赖与最后真实任务效果，不要求每Skill创建常驻服务或合成heartbeat。每日工作推进、记忆/文档新鲜度为其业务维护状态；不能把历史设计台账stale变运行报警。

完成物：新增/退休临时隧道时monitored set随合法需求改变；无工作Quay待命；Memory可选故障不让全部红；状态scope变化能解释，不静默改变分母。

### M6 正式安装与受管验证

在实际使用的composition/release pin上构建安装，核对普通新Session、实际sender及serverosd running revision，避免只让工作树预览好看。更新observer/sender需明确安全窗口，不启动第二个竞争sender或同时写同state的Core。

真实节点先只读验证资源/健康，小范围测试receiver验证消息/回读；破坏性fault injection只在隔离实例。已存在并行容器/profile/tunnel不停止或借用。完成后报告新旧默认折叠卡面对比、证据范围、安装/运行版本和未覆盖的远端vantage。

## 4. 必测矩阵

| ID | 场景 | 关键断言 |
|---|---|---|
| V01 | 正常资源 | 不展开能读CPU/RAM/实际盘余量；型号/uid不挤摘要 |
| V02 | 缺配额/共享RSS/重复卷 | 不造限额、不假装独占内存、不把同盘当多盘 |
| V03 | 0/unknown/旧观测 | 同窗口分母真实；新发送不刷新旧数据 |
| V04 | 高CPU正常计算 | 无饱和/违约证据不自动危险；指标仍可见 |
| V05 | 磁盘或inode满 | 立即进入摘要，无需展开；不自动clean |
| V06 | Tailscale relay/idle/手机休眠 | 无人为故障；peer与app验证分开 |
| V07 | 隧道进程在、应用入口失败 | 提具体入口/身份/路径缺口，不编网络根因 |
| V08 | 已声明loopback但手机未验 | 不给手机假“打开”按钮，不开Funnel补洞 |
| V09 | daemon死/IPC挂/循环停滞 | 独立check分别识别，其他资源保持旧/unknown |
| V10 | daemon回执writer死 | sender仍合法记录该故障，恢复不双发 |
| V11 | 外网/整机观察失败 | 显示实际failure domain，off-host未绑不保证必达 |
| V12 | 同根故障、升级、恢复、重开 | 聚合有依赖证据，旧ACK不能吞新episode |
| V13 | quiet/无待发消息/延迟回执 | sender不误判dead，无无限递归健康告警 |
| V14 | 长名称/多异常/默认折叠 | 390/360px可读，无水平溢出，当前重要问题不隐藏 |
| V15 | 原生Feishu渲染 | 同一payload真实可见，HTML不是替身 |
| V16 | 计划停机/退役临时环境 | 不发假故障，历史证据保留，当前视图不充数 |
| V17 | 新配置未reload旧process | 显示实际running版本；待激活不是已升级 |
| V18 | 未授权动作/错误actor | 没有实际mutation；“已知晓”不是批准重启 |

## 5. 判据与计划关系

完成需同时说明：source/probe、意味正确的model、用户可读默认面、真实consumer、故障恢复与实际安装范围。单元测试数、registry记录数、生成HTML成功、发送后有一个message_id均不能独立替代以上。

这是现有P8/P9的明确产品细节和P11场景补充。监控专项不要求重新做Card/Store或新建消息平台，也不阻止无依赖Daily Cycle代码继续。其余21能力以ability-management/design/PRODUCT-EXPERIENCE-CONTRACT.md验收：真实解决用户问题，不只完成结构化文件。
