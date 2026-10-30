# Fleet V1 · 设备侧 ServerOS 插件：核心、外围、记忆与登录

> 状态：设计 + 第一批实现（2026-10-30）。上游：`fleet-v1-four-pillar-tracker.md`（#531）、
> `fleet-v1-resident-monitor-and-tokyo-hub.md`。本文回答五个问题：owner 只说需求时谁来补齐其余；
> 每台设备上的插件里放什么；外围能力怎么被发现、安装和迭代；记忆放在哪；怎么"装好、登录、就能被指挥"。

## 0. 结论

1. **owner 只给需求**（可选：设备、agent 类型；V2 起可以是抽象 agent）。编号、命名、A/B 设计、
   测试层级由设备上的 ability 决定，路由和验收由指挥侧 skill 决定。
2. **每台设备、每个 agent 一个 serveros 插件**，同一份源码，Claude Code 与 Codex 各一个适配。
   插件只放核心：常驻 7 个 ability、node MCP 的 `core` 工具集、过程记录 hooks。
3. **监控不进 agent 上下文。** 资源、异常、报警、清理属于 serverosd 与 Tokyo 中枢；node MCP
   给 agent 只服务 `core` 15 个工具，其余 21 个留给 `ops`（运维或中枢）。
4. **外围靠目录。** 其余能力登记在 ability-management 的目录里，agent 用到时搜索、按 pin 住的
   修订安装、读回。第三方第一次引入必须 owner 批准；agent 只能把它提成看板候选。
5. **记忆三层**：本机（可丢）→ OpenViking@Tokyo（跨设备，非权威）→ GitHub records（权威）。
6. **登录即可被指挥**：插件装好后，用 Tokyo 中枢的账号 + 密码 + 2FA 登录一次，授权范围写进令牌。

## 1. owner 的一天：从需求到交付

```text
来源：线下谈话 / 飞书消息 / 自己的想法 / 线上报错
  → 中枢看板候选（fleet_work_candidate_add，带来源和理由）
  → owner 每天挑 1～2 个（fleet_work_board）
  → 立项：设备 work-management 分配编号（复现之前）
  → 复现：以编号起容器，固定旧 revision —— 这就是 A
  → 修复：worktree 改代码，起 B；同一矩阵 E2E（AB_TYPE=VERSION|CONFIG）
  → 真实客户端：FJPCOS 上用真实 Codex / Claude Code 按用户步骤走 A 和 B
  → 取证：Tokyo 只读查共享网关（Ubuntu 身份）与生产日志
  → PR（证据对应最新提交）→ 合并 → 验收 → 关闭
  → 中枢监控确认完成 → 飞书完成卡 / 报警卡
```

| 环节 | 谁 | 能力 | 状态 |
|---|---|---|---|
| 收集、挑选 | 中枢 + GPT | `fleet_work_board` / `fleet_project_understanding` / `fleet_work_candidate_add` | **本轮实现**（`fleet-mcp-9`） |
| 拆分、派发、验收 | 指挥侧 | skill `fleet-work-drive` | **本轮实现** |
| 编号、命名 | 设备 | `work-management` + `workspace-management` | 已有 |
| 复现 = A、A/B 规则、测试层级 | 设备 | `test-management`「Bug 复现与 A/B」 | **本轮补齐** |
| 项目专属（LIT 命名、PR 九节、预算） | 项目仓库 | 项目测试合同 + PR 模板 | 待项目仓库落地 |
| 真实客户端 E2E | FJPCOS | 真实客户端能力 | 待 Windows 接入 Fleet |
| 网关 / 生产取证 | Tokyo 只读 | `production-observation` + 白名单 | 白名单待 owner 定 |
| 完成通知 | 中枢 | M2 观察 + M3 飞书卡（`lark-cli`） | 已合并，接收人待配置 |

发飞书走 `lark-cli`（`runtime/message_transport.py`）：`im +messages-send --as bot` 发卡，
`im messages patch` 原位更新，带幂等键。Tokyo 的 `lark-cli` 需要登录机器人；接收人默认 owner
本人，配置后用 `--notify lark:user:<open_id>` 开启，在此之前只写本地日志。

## 2. 设备侧插件里有什么

一份源码（`plugin/bundle.yaml` + `plugin/profiles/*.yaml`），出两个适配（Claude Code 插件、Codex 插件）。

| 部分 | 内容 | 说明 |
|---|---|---|
| 常驻 skill（7） | serveros（入口与发现）、workspace-management、work-management、test-management、information-management、session-manager、ability-management | 由 ability-management `catalog.yaml` 的 `resident` 定义 |
| `serveros-node` MCP | **core 15**：device.show、context.current、path.resolve/admit、ability.list/status/tree/inspect/search/resolve、resource.summary、runtime.status/explain、environment.leases、ports.ownership | 插件通过 `SERVEROS_NODE_MCP_TOOLSET=core` 只服务这 15 个；`ops` 服务全部 36 个 |
| `serveros-continuation` MCP | work.checkpoint/park/accept/complete 等 | 过程记录与断点续做 |
| `serveros-agent` MCP | 本机会话 create/send/stop/handoff | 与 Fleet + CloudCLI 的原子控制重叠，V1 不动，V2 抽象 agent 时统一 |
| hooks | 会话开始召回、结束记录过程、陈旧会话归档 | 召回/采集等第 5 节的采集规则 |

**node MCP 是什么：** 它是 serverosd（本机守护进程）的薄适配层，本身不存状态，每个调用转发给
serverosd 的 Unix socket。精简没有删掉任何 daemon 能力，只是让 agent 看不到监控类工具。
C++ 的 `native/node-core` 负责高频采集，读数经 serverosd 汇总，再由中枢判级和播报。

### 2.1 profile 与设备

| 设备 | 角色 | profile | 额外常驻 |
|---|---|---|---|
| Tokyo `device:tokyo-shared-dev-01`（vince） | 主开发、A/B 容器、中枢 | `linux-work` | `build-release`；playwright、cloudcli MCP；指挥侧插件只装这台 |
| Dell A6000 `device:llm-lab-dell`（fjd） | GPU 计算、个人项目 | `linux-work` + 计算礼仪 | 设备说明写明：不抢 GPU、不重启 Strata、显存只读 |
| FJPCOS `device:work-fj`（Windows） | 真实客户端 E2E、浏览器、办公文档 | Windows profile（待建） | computer-use、browser MCP；真实客户端 E2E 能力 |
| DellPCOS `device:personal-dell` | 个人工作站 | 不纳入公司中枢 | — |
| 共享网关、生产服务器 | 取证来源 | 不装 serveros | Tokyo 上只读查询 |

现状差距（来自实测快照，Tokyo 2026-10-20、Windows 2026-09-12 恢复记录、Dell 无记录）：
- Tokyo 两个 agent 都没装 workspace-management / test-management；opencli 一族 7 个、
  MarketOps 4 个插件、muc-playwright-e2e 常驻；PR 与开发流程各有两份。
- Claude Code 与 Codex 装的不一样，同一台设备两种 agent 行为不同。
- Windows 的 skill 名是旧的（`cc-codex-ability-management`、`workspace-maintainer`）且未接入 Fleet；
  Dell 在 ability-management 里没有设备编号。

处理顺序：Tokyo 上 `claude remote-control` → `control_plane.py readback` 实时清点三台 → 按本表出
增删变更单 → owner 批准 → 一个 `--rev` 推送到三台 → 读回一致。

## 3. 外围：发现、安装、第三方审批、自迭代

**目录**（ability-management `catalog.yaml` + `control_plane.py catalog`，本轮实现）：

| tier | 含义 |
|---|---|
| RESIDENT | 常驻 7 个 |
| AVAILABLE | 第一方登记能力，可按需安装 |
| APPROVED_THIRD_PARTY | owner 点名批准的第三方 |
| NEEDS_OWNER_APPROVAL | 已登记但未批准的第三方：不装，给看板候选 |
| NOT_INSTALLABLE | 登记了但本仓库没有 payload |

```text
agent 需要"写 PR"
  → serveros ability.search / control_plane.py catalog search "pull"
  → 本机已装：直接用
  → AVAILABLE / APPROVED_THIRD_PARTY：catalog install NAME --agent X --rev SHA --apply → 读回
  → NEEDS_OWNER_APPROVAL / NOT_IN_CATALOG（exit 3）：回执里带一条看板候选
      → 指挥侧 fleet_work_candidate_add → owner 批准 → 写入 catalog.yaml → 进入目录
```

现有 5 个第三方（good-pr、codex-pr-body、opencli、前后端方案设计）在门禁之前已在 Tokyo 使用，
暂列为已批准、标注"owner 待确认"；`grill-me` 来源不明，需要批准。

**自迭代**：汇报格式、PR 结构、测试合同这些外围内容可以经常改，常驻核心很少改。

```text
agent 干活 → information-management 记录过程和问题
  → 发现规范缺口 → 看板候选
  → owner 批准 → fleet-ability-change：改 ability-management → 新 rev
  → rollout 到各设备 → readback 一致 → fleet-norm-audit 抽查行为 → 不行就回滚
```

## 4. 精简：serveros 不做的事

- 资源监控、异常、报警、播报卡：serverosd 与中枢（`integrations/fleet-hub-watch`）。
- 不把每个 skill 都做成 ability：项目专属规则放项目测试合同；偶尔用的进目录。
- 后续把 runtime 里的监控代码分批迁出（每次一个 PR，CI 全绿），本轮只收窄 agent 能看到的入口。

## 5. 记忆

| 层 | 内容 | 位置 | 地位 |
|---|---|---|---|
| A 本机 | agent 自带记忆、运行状态 | 各设备 | 可丢，丢了重探 |
| C 跨设备 | 偏好、经验、做事轨迹 | OpenViking @ Tokyo（`127.0.0.1:1933`，需密钥，有备份与演练过的恢复） | 持久、非权威，只作提示 |
| B 权威 | 需求、决定、证据、项目理解 | GitHub（hwz-records 与各项目） | 以它为准；GPT 经 GitHub 连接器读 |

现状：OpenViking 已部署，Tokyo 两个 agent 装了 `openviking-memory` 插件，但**只检索、不采集**，
且只有 Tokyo 能连。打通三步：

1. **采集规则**（本文定）：不采集密钥与凭据、生产原始数据与日志原文、个人设备（DellPCOS）内容、
   第三方未授权材料；跨项目记忆默认不召回，除非当前任务明确需要。
2. 在 Tokyo 打开自动采集与召回 hooks —— 会消耗模型额度，owner 决定。
3. Dell、Windows 经 Tailscale 连 OpenViking，每台设备一把密钥 —— 依赖 Tailscale 决定。

## 6. 安装与登录：装好、登录一次、就能被指挥

```text
指挥侧（GPT / Claude 客户端）
  安装 Fleet 插件 → 打开 Tokyo 中枢授权页 → 账号 + 密码 + 2FA 验证码
  → OAuth 同意页按 scope 授权（只读 / 发送 / 创建）→ 令牌只含被授权的 scope

设备侧
  安装 serveros 插件（Claude Code + Codex）→ serveros enroll
  → 显示一个设备码和 Tokyo 的登录地址 → owner 在 Tokyo 登录（账号 + 密码 + 2FA）并确认设备
  → Tokyo 发一张绑定 device_ref 的设备凭据 → 节点连接器上线 → Fleet 可见、可控
```

| 部分 | 状态 |
|---|---|
| 指挥侧 OAuth 2.1 + 动态注册 + 同意页（CloudCLI） | 已有 |
| 同意页写入类 scope 需再次输入密码 | 已有 |
| 登录与同意页的 2FA（TOTP，RFC 6238） | **本轮在 CloudCLI 实现**（见对应 PR） |
| 设备 `serveros enroll` 设备码登录 | 设计；依赖 Tokyo 对外地址（Tailscale / FQDN，owner 决定） |
| 设备凭据轮换与吊销 | 设计；随 enroll 一起做 |

原则：密码与 2FA 只在 Tokyo 页面上输入，插件与 agent 永远拿不到；令牌按 scope 最小授权；
设备凭据只能代表它绑定的那台设备。

## 7. 本轮实现与未做

本轮（serveros）：node MCP `core`/`ops` 工具集；中枢看板三工具（`fleet-mcp-9`，48 个工具）；
指挥侧 `fleet-work-drive`；本文档。
本轮（ability-management）：`catalog.yaml` 与 `catalog list|search|install`；test-management 的 A/B 规则。
本轮（CloudCLI）：登录 2FA。

需要 owner：Tokyo `claude remote-control`（M5 与实时清点）；Dell 设备编号；Windows 接入；
第三方能力确认；OpenViking 采集开关；Tailscale / FQDN；生产日志白名单；飞书接收人。
