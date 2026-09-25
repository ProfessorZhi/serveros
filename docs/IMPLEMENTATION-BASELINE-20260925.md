# Tokyo实施基线 · 2026-09-25

用途：支撑[唯一实施计划](CC-EXECUTION-PLAN.md)，不是另一个运行状态库。观察时间约2026-09-25 08:36–08:45 UTC（Asia/Shanghai 16:36–16:45）。现场会变化，CC实施前必须复核；文档中的历史PID不是停止目标。

## 1. 范围与证据等级

本次读取Tokyo vince用户的相关源码、Git元数据、host profile、已安装artifact/Plugin配置必要字段、用户级unit/timer、ServerOS只读状态和自己进程。未读取凭据值/原始业务聊天，未执行收费模型/真实E2E/飞书发送，未启停服务、清目录、升级依赖或修改控制store。

原始只读输出中远程shell首次`systemctl --user`报`No medium found`，补该用户`XDG_RUNTIME_DIR`及D-Bus地址后正常读取；系统级同名tailscaled/laya inactive也不能推翻用户级active。分别记录观察路径，不能把调用环境错误报业务故障。

源码存在=CODE；安装manifest=已物化；unit/PID=进程运行；业务probe/consumer/verdict才证明指定功能。下面不按章节/文件数估算“完成百分比”，也不从未发现默认配置推导全机绝无自定义部署。

## 2. Git与安装版本

| 对象 | 观察 | 影响 |
|---|---|---|
| serveros canonical checkout | `ee851b7ae548695b2797031211683475a6e68f14`，clean | 落后远端，禁止直接用它构建目标版本 |
| serveros main / 架构候选 | `c5d118cc44629e7cb264b73e3026d8ac3abbf7dc` / `13f2819af52e7e1df8bcb81c0b28eee2814e3b2b` | PR #6仍draft/unmerged |
| ability-management canonical | `8068d0bc63720227b9a149d1e5651365c8af3fdf`，clean | 落后远端，不能作为默认包pin |
| ability main / 架构候选 | `d057b6a4b9ad28589806fd77fa56fd972ea3e4e3` / `5c1963e401fff7766327938dad97549633ec9673` | PR #4仍draft/unmerged |
| 当前LinuxWork release | `alert-card-scope-20260924@8ce6fe7`，`3.1.0+srv0003.20260924c` | Node Runtime current实际指向此release |
| 上一LinuxWork release | `alert-card-scope-20260924@dbc9144`，`3.1.0+srv0003.20260924b` | 真实previous存在，需保留直到安全回退替代 |
| records | `7368e298fe8c07f9289aeadc7fb4016f0fcd2995`，本地与remote相同 | checkout在`workspace/records`而非`src/hwz-records` |
| ops | 本地`a8272d4e6a756e673571f5ddc61ad4f7774b7317`，remote `1993042f18b7d686e32d89d44d0868918264ae2f` | remote只改maps/gateway Binding，但活动脚本就在本地checkout |
| work-engine | 本地`f6be3383b41244045d043f0c3c121ea9709257f7`，remote `a0f370ff9465e090c8e8ca85b50d93a6197a1e32` | 本次diff仅README；数据/安装仍需单独检查 |

当前runtime完整source是`8ce6fe72a2eecd374d7afed739fa5a641646e8d0`，相对main多`dbc9144`（仅能打印的finding驱动卡片）和`8ce6fe7`（attention表单次读取）两笔修复。差异涉及profile、alert_digest、notification-contract及两组测试。**架构候选13f2819不含这两笔源码修复；后续安装必须集成，而不是直接重建架构分支回退现场。**

release回执tree hash为`e2fa01ece5c77fb5d5add7cf05bd74434f16ed5e44e4ff4e997bc061eda2189b`。这是既有发布回执，本轮未全量重算所有已安装字节；后续用独立runtime-truth和fresh consumer核验，不能把回执自报当不可伪造证明。

## 3. 本机服务、入口与目录事实

| 检查对象 | 观察结果 | 实施意义 |
|---|---|---|
| 容量 | `/`50G，47G已用，约500M可用/99%；`/data`4T，465G已用，约3.6T可用 | 先核系统盘安全与写入预算；大写入全部放`/data`，不能擅自清系统盘 |
| vince quota | XFS82.9G已用，soft450G/hard500G | 与整盘12%占用分开；host profile旧3.8G只是历史 |
| serverosd用户unit | active/running，PID3116505，Restart=on-failure | CLI/socket真实可读，不能因此认全业务可用 |
| Node状态 | tick60s、观测fresh、tick failure0、registry4、`reconcile_enabled=false` | 本次未打开自动恢复；后续按具体resource权限启用 |
| Node路径 | `runtime-assets/hwz-server-os/serveros-linux-work/current`→`revisions/alert-card-scope-20260924@8ce6fe7` | unit与CLI已走发布路径，非当前开发tree |
| state/socket | `/data/home/vince/state/serveros/runtime`；`/run/user/1002/serveros/serveros.sock` | 安装state base避免重复`runtime/runtime`；socket不是跨设备授权入口 |
| 核心旧物化目录 | `runtime-assets/hwz-server-os/current`是实目录，core版本`20260923c`，3个Skill | 与LinuxWork current不同；未知consumer前不得删 |
| 两个MCP在包中 | serveros-node、serveros-agent入口和两套manifest均存在 | payload成员证明，不等于已在终端启用 |
| LinuxWork Skill成员 | serveros、ability-management、runtime-management、workspace-management、test-management | 其他能力按需发现/安装，不应全量塞进默认context |
| Claude用户配置 | 检查到的已安装/启用ServerOS条目只有Testing；LinuxWork marketplace路径存在 | 尚无本轮fresh主Plugin加载证明，核所有真实配置作用域后补安装 |
| Codex用户配置 | CLI实际存在，检查到的config无两个ServerOS MCP项 | 主集成待核/补；不推导所有临时profile均不可能使用 |
| 机器指令 | `CLAUDE.md`/`AGENTS.md`仍写Codex未安装、每producer先Run、云写一律proposal、Meeting成员必须齐 | 与现场/目标不符；canonical规则与渲染安装均要迁移 |
| OpenViking | `openviking`/`ov`命令未找到，默认127.0.0.1:1933不可连 | 默认路径无部署证据，真实服务/Binding/native集成未验；不排除未检查自定义端口 |

只列与本计划有关的目录，不把全机树或其他用户WIP搬进Git。原始审计材料保存在本任务受控artifact区，必要时按locator读取。

### 3.1 原生服务和定时消费者

用户级tailscaled实际active，Restart=always，采用userspace私有socket；user级laya-shared实际active，Restart=on-failure。Laya执行环境仍为`/data/home/vince/toolchains/marketops-mvp/bin/python`，cwd为`workspace/src/ability-management`，有独立Binding但与可变源码/其他项目环境耦合。端口/进程存在不证明答案质量或两个真实业务consumer已接入。

用户timer列表见`tokyo-status-card`、`tokyo-alert-digest`和无关cache-clean。前者整点，后者`:03/5`，均Persistent=false；没有在该列表看到完整日循环timer。本次未枚举全部系统cron/外部scheduler，实施前应核实际trigger owner而非据此断言所有自动化不存在。

状态sender直接执行`workspace/ops/scripts/tokyo-monitor/monitor_card.py`，正文源确认：小时key `tokyo-monitor-YYYYMMDDHH`，`+messages-send`，静默22–09，CRITICAL例外；不是目标CN工作日08–20同日一张patch。告警sender`alert_digest_card.py`确实检查`decision.send=false`后安静退出；保持已修行为，继续迁episode/daily独立的目标需求。两个脚本都有旧renderer fallback，验收时需明确当前真正卡片来源。

### 3.2 Registry、控制与历史执行

当前managed registry四项：container0-litellm、container0-postgres、serveros-control-plane、browser-stack；均有明确探测。Laya/Tailscale/CloudCLI未出现在这份registry，可能存在其他owner记录；先查Binding，不把所有探测到的进程自动纳管。生产/共享测试依赖本轮不改。

当前agent-control store仅检查结构/数量：approvals0、bindings0、contexts0、selections5。不能由此给模型合成live grant，也不能证明其他可信控制源全部没有委托；P6必须接正确authoritative owner。

发现同uid另有三个serverosd从`artifacts/work-items/serveros/SRV-0011/RUN-0001/release/dist/...`运行，历史PID1300360/1302716/1329264。它们与canonical PID3116505不同，不能按进程名批量停止；必须重新核各自socket、state、owner、lease和effect。`state/serveros/serveros.db`有WAL/SHM，备份/迁移不能只拷主文件。

## 4. 源码到产品的具体差距

| 源码位置 | 本轮读到什么 | 目标修正，不新增平行平台 |
|---|---|---|
| `intake/focus_source.py` | 保留NOT_PRODUCED的grant/worker/resource/next_action/dependency字段，部分旧理由要求加Card字段 | 改为Work Engine/Agent Control/executor/lease的真实join，缺失明确UNKNOWN |
| `intake/daily_cycle.py:select_focus` | 排候选后填剩余slots；TERMINAL含DELIVERED；缺next_action默认research | 推荐与已接受接续分开，no-refill，按required结果/实际effect释放责任 |
| `runtime/recipes.py:perception` | 一源未回答会中止该scope；source变动直接问模型候选Item | 各源成功/缺口独立，先B2理解再判断B3需求 |
| `runtime/recipes.py:work` | 先要求worktree及历史进展，NONE则NO_PROGRESS，随后才advance | 新任务不能被“尚无产出”阻止启动；准入/进展watch分开，no-code不强依赖Git差异 |
| `understanding_brief/semantic/claim_recall/source_waterline` | 已有真实固定材料、校验提交、资格失效与双水位机制 | 扩reader及真实调用者/下游，不重做writer |
| `documents/document_governance.py` | 文档身份/authority解析内核 | 不是远端publisher成功；补scoped写入/readback/材料/冲突实际consumer |
| `scripts/build.py` / `plugin/*` | 双artifact kind、完整Ability SHA构建、MCP/模块装载闭包 | 复用；隔离candidate输出，勿写live current；新字节更新cache identity |
| `scripts/release.py` | promote stage/verify/swap/unit/CLI；daemon-reload但没有自动restart | 安装验证明确区分链接、配置和运行进程；`--no-reload`不是dry-run |
| `release.py:daily-task/alert-worker` | daily-task只装workbench render；alert-worker默认可能enable | 不以旧installer宣称全日循环，不与活动ops sender重复安装 |
| `memory/provider-contract.yaml` | 仍要求纯Layer-B可重建索引、不得新增服务、无全局Provider | 随真实OpenViking采用修合同/adapter/测试；保daemon独立和业务authority |
| `runtime/cli.py`实际help | 顶层`work`下有board/focus/admit/confirm/semantic等，非旧顶层item/run命令 | 后续执行以实际help为准，不能照旧Skill构造不存在的CLI |
| 已装机器policy和Skill源 | 对象全流程、无条件阅读/审批、旧设备事实 | canonical与渲染消费者一起迁；静态lint不代表真实选择/自治路径正确 |

## 5. 21项责任的实施落点

下表是规划映射：候选设计有owner，相关代码存在；**不是21项已运行通过的清单**。实际E2E和层级结果由P11/P12补证。文件名按本轮版本，实施变动后更新定位。

| Ability | 优先复用 / 源入口 | 必须补或证实 | 主计划 |
|---|---|---|---|
| G1 | Ability manifest/cards/packs、canonical Skills、ServerOS build/profile/runtime_truth | 源/pin/包/安装/实际发现分开；authoring与fresh consumer接通 | P9/P12 |
| A1 | context/resolver、runtime/agent_sessions、fleet/router | logical Coordinator/Parent，真实恢复receiver，不双派 | P6/P7/P9 |
| A2 | claim_recall、memory/*、records、MetaCC | OpenViking真实适配，当前资格失效与跨Session/权限隔离 | P3/P10 |
| A3 | workspace Skill、原生Git、runtime/paths/workspace | 准确owner/root、隔离写入与消费者迁移，不扫空历史 | P0/P9/P12 |
| A4 | daemon/registry/probes/lease/health/infrastructure | 真实受管范围、功能/覆盖/影响、获准恢复与独立Core观察 | P8/P12 |
| A5 | fleet/transport/controller/remote adapter | 目标身份/私网/有界远端执行及结果返回，同意才转writer | P7/P11 |
| A6 | Quay现有driver、bounded_invocation/review_receipt | 原任务预算/worker/receiver、late result和cancel恢复 | P7/P11 |
| A7 | Testing Plugin、test-management、实际browser/API | 真实路径逐AC验证；fixture/缺环境/产品FAIL分别记录 | P9/P11 |
| A8 | scripts/build/release/runtime_truth/verify | 可复现候选、Node与两终端安装、运行回读及兼容回退 | P9/P12 |
| A9 | production-observation Skill及获准只读Provider | request/费用/版本范围的真实取证；不动配置换取证据 | P11 |
| A10 | production-change Skill、现有Runbook | 人执行前准备与后验；本计划不授权真实生产切换 | P11/P12 |
| B1 | records/raw既有sync/expand/drift/redaction，source_policy/material/coverage | 受控增量、多源revision、late材料自动接下一reader | P1/P2 |
| B2 | understanding_brief/semantic/patch/source_waterline | 原生实际阅读、可信提交、四类View及更正回流 | P3 |
| B3 | global_board/workchain/原Requirement语义 | formal唯一工作项，证据变化与目标变化不同，保护Board人改 | P6 |
| B4 | Work Engine、Agent Control、focus_source/daily_cycle | 多权威join、只续接受Focus、真实完成/等待/取消 | P6/P7 |
| B5 | research-evidence、原生搜索/现有模型Provider | 按具体问题/预算取足够证据，不强制多模型 | P7/P10/P11 |
| B6 | solution-design专精Skills、已接受架构 | 仅必要设计/独立review，输入固定，方法不代权限 | P7/P9 |
| B7 | 原生实现、implementation-delivery、Git与实际consumer | 补接线而非只写文件，required文档/验证接续 | P1–P12各切片 |
| B8 | feishu-report、daily_report/report_publication | 周期稳定报告、事实去重、发布/发送分责 | P5/P11 |
| B9 | document-stewardship/space、document_governance/external_reference | 三关系、rich scoped patch、Drive/material、readback及retention | P4/P5 |
| B10 | CloudCLI、human_console、cards/delivery及ops sender | 精确人类边界、daily/incident生命周期、真实接收/窗口 | P7/P8/P12 |

## 6. 不应重复建设或暗中扩大范围

不新增工作流引擎、memoryd、每Ability专用MCP、第二semantic writer、全局完成度数据库。已安装告警修复和已有发布/原子准入/固定reading机制优先保留。MarketOps、Support Bot是独立产品；跨其repo仅限明确同意的接口，不把它们全部MVP搬进本工作项。

资料安全继续沿当前publish-excluded/secret规则；不读取真实`.env`验证旧危险示例，不在计划中复制source消息/收件人token/凭据。目录名称、manifest记录、旧日志、unit active、端口可达各自仅证明对应层。

## 7. 本次交付与后续验证的边界

本轮在从ServerOS架构候选新建的`docs/implementation-plan-20260925`分支更新实施计划，单独保存现场基线及短交接提示；既有架构PR、canonical checkouts、活动runtime与ops文件未修改。新PR应先基于架构分支审阅；其父PR #6合并后再按实际Git关系retarget至main。Ability PR #4和现用告警修复仍是明确依赖，不能把stacked PR误当main已经齐备。

本轮检查计划的P0–P12/21职责覆盖、文件引用、代码块和diff；没有执行上面业务Journey或任何安装/发消息。实际检查命令与结果由提交/PR记录。后续CC第一次启动仍重读当前source/安装/权限，不能把本快照的PID、容量或服务数量当永恒事实。

### 本轮文档检查回执

最终候选仅7份Markdown（5份修改、2份新增）；P0–P12共13阶段、G1/A1–A10/B1–B10共21职责映射完整。81处文件链接目标检查通过，其中16处按配套候选树核对跨仓目标；代码块闭合及`git diff --check`通过。这些不是HTTP可达性或全文语义正确性的自动证明。

在隔离`/data`临时目录、禁止bytecode写入的条件下运行既有`test_core_freeze.py` 27项及`test_core_ability_boundary.py` 16项，全部通过；未改测试断言、安装环境或业务代码。它们只守既有Core边界，不能证明计划中尚未实施的五循环、实际云对象或终端安装已通过。
