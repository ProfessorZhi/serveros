# ServerOS Fleet V1 总览

> **这篇是 V1 的总入口**：Fleet 由什么组成、信息在哪里流、每台设备的目录长什么样、测试环境怎么放、
> 可观测性 / 日志 / 密钥中心怎么做。细节各有专文，本文只给全貌和链接。
> 状态：2026-11-19 与 Owner 对齐后写下。需求的完成度以 [fleet-requirements.md](../requirements/fleet-requirements.md) 为准。

## 1. 一句话

**东京是唯一的大脑，A6000 和 FJPCOS 是手。** 信息（飞书、你的话、记录、需求、看板）只在东京流转；
执行节点只做三件事：接活、干活、交回结果和读数，自己不存「认识」、不存需求。

```text
                         Owner（飞书 / ChatGPT / Windows 上的 CC）
                                  │ 说话 · 批准 · 看播报
┌─────────────────────────────────▼──────────────────────────────────┐
│ 东京 hub（vince）                                                     │
│  · Fleet MCP：设备与会话发现、派活、授权、追踪（fleet_trace）             │
│  · 管家：飞书播报（派活 / 完成 / 卡住 / 异常 / 日报 / 周报）与问答          │
│  · 信息流：飞书归档 → 原始层 → 认识层 → 每日对齐包 → 需求 → 看板（§4）      │
│  · 记录：fleet-journal（全过程）· 跨设备记忆 fleet_memory                 │
│  · 密钥中心源头 · 观测汇总与判级                                          │
│  · 同时是执行节点：主力开发、A/B 容器                                     │
└───────────────┬──────────────────────────────────┬────────────────┘
                │ 签名节点协议（派活、读结果、读数、事件）                    │
┌───────────────▼────────────────┐      ┌──────────▼─────────────────────┐
│ A6000（fjd）计算节点               │      │ FJPCOS（Windows）客户端节点        │
│  GPU、模型部署、LLM Lab（受保护）    │      │  真实客户端 E2E、浏览器、办公文档     │
│  公司项目 + 闲时个人项目             │      │  拉取 Windows 侧日志              │
│  生产日志只读查询与分析              │      │                                │
└────────────────────────────────┘      └────────────────────────────────┘
三台都有：serverosd · 节点连接器 → CloudCLI → Claude Code / Codex · 设备插件（核心 + 角色包）
          · 每日目录检查 · 密钥同步（agent 只见名字）
```

## 2. 底线原则与验收数字（Owner 2026-11-19 确认）

四条原则：**高效率、高性能、看得见（每次交互可追溯）、好迭代**。每一部分按下面的数字验收，量不到的不算完成。

| 项 | 目标 | 怎么量 | 现在 |
|---|---|---|---|
| 指挥侧和设备侧 agent 开会话时的上下文 | **≤ 约 8 KB**（Skill 描述 + CLAUDE.md/AGENTS.md + 工具清单）；单个常驻 Skill ≤ 4 KB | 设备上新会话第一轮的 system/工具字节数 | 指挥侧 3 KB 核心已达；设备侧常驻 7 个 Skill 正文 84 KB、描述约 15 KB，未达 |
| 派活 → 东京知道 | **≤ 2 秒** | fleet_trace：dispatched → pushed run.started | 轮询 P50 13 s；事件推送上线后复测 |
| 任务做完 → 完成卡 | **≤ 10 秒** | run 结束时间 → 卡片发出时间 | 轮询时代 P50 约 15–30 s；事件推送上线后复测 |
| serverosd / 设备常驻进程空闲开销 | **CPU < 1%，内存 < 20 MB**（每个） | 设备上 30 s 采样 | serveros-eventd 实测 CPU 0.07%、RSS 2.3 MB（1 次/秒轮询，2026-11-19） |
| 监控自身停摆 | **≤ 5 分钟**被发现并报出 | 停掉一个组件，计时到飞书卡 | watchdog 定时器已有；eventd 状态进心跳 |
| 迭代 | 每层独立发布；合并即出包；设备一条命令更新且不丢配置 | 一次真实更新的回读 | 已有；`--journal` 丢失已修 |

## 3. 四部分

| 部分 | 是什么 | 专文 |
|---|---|---|
| ① **serverosd** | 每台设备、每个账号一个常驻进程：硬件监控（CPU/内存/交换/磁盘/GPU/网络）、**agent 监控**（run 开始/完成，目标由设备主动推送，D-4）、本机受控自愈、每日目录检查、版本记录 | [FLEET-FOUR-LAYERS.md](FLEET-FOUR-LAYERS.md) §3 L1 |
| ② **serveros 设备插件与能力族** | 每台设备每种 agent 一个插件：核心 Skill + `serveros-node` MCP（serverosd 的薄适配）+ hooks；其余能力登记在 ability-management，用到时搜索、按 pin 安装、读回；目标「核心包 + 角色包」（C-6）与每台「本机说明」（C-3） | [fleet-v1-device-plugin.md](../archive/plans/fleet-v1-device-plugin.md) |
| ③ **连接层**（今天是 CloudCLI 穿透） | 建会话、发消息、读回复、中断、切模型/权限、审批、列出并调用 Skill 和斜杠命令；接口与实现分开，CloudCLI 可替换 | [agent-access.md](agent-access.md) |
| ④ **指挥侧** | 一个小的核心 Skill + Fleet MCP 工具；派活带 Owner 原话，可追溯 | `integrations/fleet-v1-first-install/plugin/` |

**Rust**：新的性能敏感组件默认用 Rust。按实测收益排序：① 设备→东京的事件推送 ② Windows 读数发布链
③ 节点连接器 ④ 密钥同步 ⑤ 采集器本身。见 [rust-evaluation-2026-11-13.md](../requirements/rust-evaluation-2026-11-13.md)。

## 4. 信息流（只在东京）

```text
飞书（白名单群聊 · 妙记 · 云文档）＋ Owner 的话（ChatGPT / 管家 / CC 对话 → fleet-journal）
   ▼ K-1/K-2 自动归档，只追加
hwz-records information/raw/            原始层：只存事实
   ▼ K-4 每天重算
information/understanding/             认识层：卡片带出处、核实时间、置信度
   ▼ A-1 每日对齐包（新事实、矛盾、建议、≤5 个问题）
Owner 回答 ─A-2─▶ projects/*/requirements（REQ 编号，只收 Owner 确认的）
   ▼
看板 fleet_work_board（A-4 另生成只读的飞书文档）
   ▼
立条目 → 指挥侧派活 → 设备执行 → 结果进 fleet-journal / records → 回到原始层
```

**记忆三层**：本机（可丢）→ 东京 `fleet_memory`（跨设备共享的结论与规矩；OpenViking 是派生召回层）→
GitHub records（唯一权威）。记忆是召回，不是权威；与 Git 冲突以 Git 为准。

## 5. 目录

### 5.1 工作方式：一个仓库一份主 clone，一个条目一个 worktree

- `src/<仓库>`：主 clone，**只拉取、不开发**，停在 main，作为基准；提交历史只存这一份。
- `worktrees/<仓库>/<条目号>`：一个条目一个 worktree、一个分支（分支名同条目号）。多个 agent 并行改同一仓库互不踩；
  Git 不允许同一分支检出两次，正好防撞车。闲置 14 天且无未推送提交、无改动、无 stash 才回收。
- `envs/<条目号>/`：**测试环境，和代码分开**（§5.2）。
- `cache/`：pip / uv / pnpm 共享缓存，worktree 不各装一份依赖。
- `scratch/`：临时，TMPDIR 指这里，30 天自动回收。
- `artifacts/<项目>/<条目号>/`：截图、日志、构建产物等重型证据；records 只登记指针。

规则由 `distribution/workspace/ws.py` 和各台登记表 `distribution/workspace/layouts/<设备>.json` 执行，
每天检查一次（[README](../../distribution/workspace/README.md)）。

### 5.2 测试环境：复现 bug、同一输入、A/B 对照

Owner 的测试大多是 E2E 的 A/B：**用同一份输入，先在旧版本上复现 bug（A），修好后在新版本上跑（B），对比结果。**

```text
work/envs/<条目号>/
├── inputs/     复现输入：请求、prompt、配置、数据样本。录一次，之后只读；A 和 B 用同一份
├── A/          旧版本（固定 revision 或旧配置）的运行目录：容器状态、数据、生成的配置
├── B/          修复后的运行目录
├── results/    同一输入下 A 与 B 的输出和对比（diff、指标）
└── env.yaml    条目号、A/B 的 revision 或配置、端口、容器名
```

- worktree 是**代码**，env 是**运行出来的东西**；分开放，回收代码不会误删数据，清环境不会误删代码。
- 端口和容器名按条目号分配，并行的条目不冲突。
- 截图、录屏等证据进 `artifacts/<项目>/<条目号>/`；结论与指针进 records。
- 条目关闭后 env 到期回收（登记表里 30 天）。
- 能力侧对应 test-management 的「Bug 复现与 A/B」（`AB_TYPE=VERSION|CONFIG`）。

### 5.3 各台设备

**东京（vince；home 在数据盘 `/data`）**

```text
/data/home/vince/
├── work/{src,worktrees,envs,cache,scratch,artifacts}
├── work/records/          ★信息流只在这里：hwz-records、fleet-journal、飞书归档工作区
├── work/.governance/      迁移计划与执行回执
├── .config .local .claude .codex .cloudcli   应用数据（hub 状态在 ~/.local/state/serveros）
└── 个人项目               只登记
```

**东京是共享服务器：Fleet 的一切只在 vince 范围内。** 只扫、只动 `/data/home/vince` 与 `/data/scratch/vince`；
服务都是 `systemctl --user`；不改任何全局配置（`/etc/*`），不看不动其他账号的目录，需要 sudo 的事不做。

待办：全局的 `/etc/profile.d/ai.sh` 给每个用户设 `TMPDIR=/data/scratch/${USER}`，vince 那份只增不减
（11-18 一次删了 3.5 万个）。**不改这个全局文件**，只覆盖 vince 自己的：
- 登录 shell：`~/.profile` 末尾 `export TMPDIR=$HOME/work/scratch/tmp`（在 profile.d 之后执行，只影响 vince）；
- 用户服务（CloudCLI、Claude Code、hub）：`~/.config/environment.d/50-tmpdir.conf` 写 `TMPDIR=%h/work/scratch/tmp`，
  之后 `systemctl --user daemon-reload` 并重启这些服务。

**A6000（fjd；系统盘 `/` 468 G 用 49%，数据盘 `/data` 1.9 T 用 12%）**

```text
/home/fjd/work/{src,worktrees,envs,cache,scratch,artifacts}
/home/fjd/work/logs/<日期>/    生产日志只读查询的切片与分析；原始日志不出机器，结论交回东京
/data/home/fjd/                大文件：models / datasets / checkpoints（home 里只放链接）
/data/models                   LLM Lab 模型仓库 —— 受保护
/srv/llm-lab                   LLM Lab 服务 —— 受保护
~/projects、llm-post-training   个人项目，只登记
```

没有 `records`：A6000 不处理信息。`/data/scratch/<别的账号>` 不看不动。

**FJPCOS（Windows；C 盘 SSD 238 G 用 72%，D 盘机械盘剩约 800 G；两盘都有安全软件实时扫描，无法加排除）**

```text
C:\        只放系统、软件、AppData、.claude、.codex —— 不放任何工作目录
D:\work\{src,worktrees,envs,cache,scratch,artifacts}
D:\work\client-tests\<条目号>\   真实客户端 E2E 的现场与截图
D:\work\logs-pull\<日期>\        拉取的日志
```

- 工作目录放 D 盘：C 盘空间紧；大量小文件在实时扫描下 SSD 的优势也会被吃掉。
- 机械盘 + 扫描慢，所以这台**少开 worktree**（建议同时 ≤5 个），重的构建和开发交给东京；它的角色是真实客户端测试。

## 6. 可观测性、日志、密钥中心

**四类信号，最后都汇到东京，并共用编号（run_id、条目号、plan_id），从一张卡能一路追到设备上的日志行。**

| 类 | 产生 | 存放 | Owner 怎么看到 |
|---|---|---|---|
| 指标（资源读数） | 各台 serverosd | 设备本地状态 → 东京判级；历史进 journal `resources/` | 管家问答、报警卡、日报 |
| 事件与链路（派活→开始→完成→发卡） | serverosd（目标：主动推送）、Fleet MCP、管家 | 东京事件库，每一跳带时间戳（`fleet_trace`） | 完成卡；「卡在哪一步」可查 |
| 程序日志 | 各台服务 | Linux journald 按服务分、Windows 计划任务日志；都限大小、滚动 | 出事时按 run_id 查 |
| 记录（人读的全过程） | 东京 | fleet-journal（Git，按天一页；目录检查日页、周报） | 随时翻 |

管家播报：派活、完成（一次完成一张卡）、卡住提醒、设备异常与恢复、日报、周报；
管家自己停摆由东京的 watchdog 定时器发现并报出。

**密钥中心**：源头一份（东京，加密）；`serveros-secret sync` 同步到各台，只有本账号可读；
每台写 `secret-names.json` 只记名字；agent 只见变量名，配置里只写引用（模型库 `envref`）。
待办：Linux 同步单元加超时（S-3）、散落凭据收编（S-4）、令牌到期前由管家提醒（D-5）。

## 7. V1 收尾的三条主线

1. **事件推送（第一个 Rust 组件）**：一次解决 agent 监控、完成播报、「结果一定读得回」（D-3/D-4）。
2. **信息流复活**：飞书归档 → 认识层 → 每日对齐包 → 看板（K-1/K-4/A-1/A-4），飞书空间地图（R-4）随后。
3. **设备插件瘦身与分角色**：核心包 + 角色包，每台「本机说明」，按需求找能力（C-2/C-3/C-6）。

目录第二轮搬迁、密钥收编在三条主线旁边顺带完成。
