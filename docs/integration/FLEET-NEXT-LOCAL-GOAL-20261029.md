# Fleet Next V1 · 东京本地 Goal Supervisor（长程持续执行）

日期：2026-10-29 · 设备：`device:tokyo-shared-dev-01`（vince） · 状态：**ACTIVE / 观察态**

本文是 operator handoff。它记录**本机实测到的事实**，不记录期望中的能力。

---

## 1. 结论摘要

| 问题 | 实测结论 |
|---|---|
| Claude Code 有官方 "Goal Mode" 吗？ | **没有**。2.1.308 的 `claude --help` 无 `goal` 子命令、无 goal 配置项。 |
| 有原生长程/后台能力吗？ | **有**：`claude --bg`、`claude agents --json`、`attach/logs/stop/respawn/rm`。 |
| 已部署本地 supervisor 吗？ | **是**，独立 user-scope 单元，`active (running)`，单实例锁生效。 |
| daemon 能自主观察什么？ | GitHub HEAD / PR / issue、精确-SHA CI、本机 Claude agents、Fleet 快照接缝。 |
| daemon 能自主派发新 Agent 吗？ | **当前不能**：无已验证 dispatch provider（`BLOCKED_ACTIVATION`），且 9 个在途已超过上限 3。 |
| 是否取代 GPT 定时任务？ | 是：不依赖 ChatGPT Scheduled Tasks，不修改旧 Fleet 任何服务。 |

一句话：**监控已真实长期运行；派发被显式闸门挡住，而不是假装在做。**

---

## 2. 第一阶段能力侦察（实测，非假定）

```text
claude 2.1.308
  --bg/--background       真实存在（后台 session）
  agents / attach / logs / stop / respawn / rm   真实存在
  goal / long-running / autopilot / resident     不存在
```

本地服务实测：

| 对象 | 事实 |
|---|---|
| `serveros-fleet-next-mcp.service` | `active (running)`，`127.0.0.1:3411/mcp`，OAuth 鉴权 |
| `serveros-fleet-mcp.service` | `active (running)`（旧 V1 面，未触碰） |
| `cloudcli.service` | `active (running)`，`127.0.0.1:3011` |
| Fleet Next PAT | `auth.pat_enabled: false` → daemon 不能自行认证 |
| 设备可达 | tokyo / work-fj / llm-lab-dell 可达；`personal-dell` = `UNKNOWN_DEVICE`（不是下线） |

因此采用指令允许的 fallback：**本地 user-scope supervisor 原型**，并在需要授权处显式
`BLOCKED_ACTIVATION`。

---

## 3. 已激活的机制

### 3.1 代码归属（canonical，在仓库内）

| 路径 | 内容 |
|---|---|
| `integrations/fleet-next-local-goal-supervisor/supervisor.py` | daemon + CLI |
| `integrations/fleet-next-local-goal-supervisor/README.md` | 契约与边界 |
| `integrations/fleet-next-local-goal-supervisor/fleet-next-local-goal-supervisor.service` | unit 模板 |
| `tests/test_fleet_next_local_goal_supervisor.py` | 27 条契约测试 |
| `docs/integration/FLEET-NEXT-LOCAL-GOAL-20261029.md` | 本文 |

### 3.2 运行时（派生安装，绝不回写源码）

```text
源码 commit : db20d2ed079c7ab3b7f8967a45e1b8701f78e333
运行时副本 : ~/runtime-assets/fleet-next-local-goal-supervisor/f925f2d3…/supervisor.py
配置       : ~/.config/fleet-next-local-goal-supervisor/config.json
状态       : ~/state/fleet-next-local-goal-supervisor/
unit       : ~/.config/systemd/user/fleet-next-local-goal-supervisor.service
```

unit 与 `serveros-fleet-*.service` **命名、cgroup、日志标识全部独立**；本单元只读取
它们的产物，不 restart / 不接管。

### 3.3 状态布局

```text
~/state/fleet-next-local-goal-supervisor/
├── goal.json        # goal_id、boot_epoch、cursor、dispatch ledger、counters
├── goal.lock        # flock 目标（文件内元数据仅作参考）
├── heartbeat.json   # pid、boot_epoch、last_tick_iso、decision、各源状态
├── receipts/        # 每次 tick 一个不可变回执
├── fleet-sessions.json  # 快照接缝（带 producer 与时间戳）
└── STOP             # 仅停止时存在
```

---

## 4. 交付 A：启动、停止、查询、恢复的真实证据

采集时间：2026-10-29T02:50+08:00。

### 4.1 服务与心跳

```text
$ systemctl --user show -p ActiveState,SubState,MainPID --value fleet-next-local-goal-supervisor.service
active
running
3821275

$ cat ~/state/fleet-next-local-goal-supervisor/heartbeat.json
{
  "goal_id": "fleet-next-v1-long-horizon-closure",
  "boot_epoch": "80743c99-89de-4ca4-a423-c8c15d6f4838",
  "pid": 3821275,
  "last_tick_iso": "2026-10-29T02:49:49+08:00",
  "decision": "NO_DISPATCH_CAP",
  "sources": {"github": "OK", "ci": "OK", "local_agents": "OK", "fleet": "FRESH"}
}
```

`heartbeat_age_seconds` 在 `status` 中持续更新；`source_state` 全为 `OK/FRESH`。

### 4.2 单实例锁（三条独立证据）

```text
# 运行中检查：拿不到锁 → exit 1
$ supervisor.py verify-lock
{"event": "supervisor.lock_check", "acquired": false}      exit=1

# 第二个实例：拒绝启动 → exit 3
$ supervisor.py once
{"event": "supervisor.already_running"}                     exit=3

# 运行中 status：lock_held=true
$ supervisor.py status
{"lock_held": true, "heartbeat_age_seconds": 30, ...}
```

锁是 `flock`，不是 PID 文件：holder 进程死亡时内核释放，残留 JSON 不阻塞恢复
（`test_lock_is_released_by_the_os_when_the_process_dies`）。

### 4.3 受控重启（restart ≠ reboot）

```text
old pid=3684241  →  systemctl --user restart  →  new pid=3730185
cursor.head = e69fc0337ae5d33000478e3d5162077f525bc8cc   （保留）
resume_count = 0   （同一 boot epoch，重启不冒充重开机）
ticks 继续增长，dispatch ledger 保留
```

重开机时 `boot_epoch` 变化，`resume_state()` 只增加 `resume_count` 并保留
cursor/ledger；`DISPATCH_ACKED_UNOBSERVED` 不会被重发。

### 4.4 停止 / 恢复

```text
$ supervisor.py stop
{"event": "supervisor.stop_requested"}

# 下一轮 tick 决策 STOPPED，随后优雅退出
{"event": "tick", "decision": "STOPPED", ...}
{"event": "supervisor.stopped_by_file", ...}
{"event": "supervisor.exit", "pid": 3730185}

ActiveState = inactive (dead)，未自动重启

$ supervisor.py resume && systemctl --user start …
ActiveState = active (running)，MainPID = 3821275
```

`Restart=on-failure`，正常停止（exit 0）不会被拉起；这是 kill switch 的语义。

---

## 5. 交付 B：真实监控回执（无凑数派发）

最近一次真实 tick：

| 项 | 真值 |
|---|---|
| `main` HEAD | `e69fc0337ae5d33000478e3d5162077f525bc8cc` |
| #478 / #425 / #424 / #434 | 全部 `OPEN` |
| #507 Wave18 RC | head `05da04f8`，**5/5 PASS**（4 shards + plugin-source） |
| #508 Wave18 QA | head `fb8c4356`，`FAIL`（shard 2/4），shard 4/4 `PENDING` |
| #509 Wave18 artifact builder | head `3520d0c8`，`FAIL`（shard 4/4） |
| #510 Release captain | head `a43adc36`，`FAIL`（shard 4/4） |
| Fleet 快照 | 120 sessions：9 `RUNNING_AGENT` / 74 `MANAGED_IDLE` / 37 `RESIDENT_HOST` |
| 决策 | **`NO_DISPATCH_CAP`**，`effective_inflight=9 ≥ cap=3`，`would_dispatch=0` |

`effective_inflight = max(observed RUNNING_AGENT, active ledger)`：**已在跑的任务计入上限**。
本轮的诚实结论是「没有可安全新增的独立任务」，不是「已派发」。
`#508/#509/#510` 的红灯已被记录为真值，交由原 Owner 续做；supervisor 不抢写、
不改其文件、不 message RUNNING_AGENT。

---

## 6. 交付 D：#478 / #425 工作权威声明

> 自 2026-10-29 起，Fleet Next V1 的长程监控由 **Tokyo/vince 本地 Goal Supervisor**
> 承担，不再依赖 ChatGPT Scheduled Tasks。该 supervisor 单实例、单锁、持久化
> goal/cursor/ledger，断电或重启后恢复；无新鲜证据时不重发 Run。它只做只读观察与
> 有界决策，不修改旧 Fleet 0.1.0 的任何服务、端口、插件、OAuth 或状态，也不合并
> main。任何真实派发/上线仍需独立授权；当前派发闸门为 `BLOCKED_ACTIVATION`
> （无已验证 provider）且 9 个在途任务已超上限。

这段声明同时回执到 #478 与 #425。

---

## 7. 明确 BLOCKED 与具体缺口

| 缺口 | 状态 | 需要谁 |
|---|---|---|
| daemon 直接读 Fleet 会话 | `BLOCKED_ACTIVATION`：Fleet Next MCP 需 OAuth；`auth.pat_enabled: false` | Owner 决定是否启用本地 PAT；当前走快照接缝 |
| 真实派发新 Agent | `BLOCKED_ACTIVATION`：`NO_VERIFIED_DISPATCH_PROVIDER` | Owner 指定已验证 provider 并把 `dispatch.enabled` 置 true |
| 在途上限 | 9 ≥ 3，`NO_DISPATCH_CAP` | 等现有 Owner 收敛；supervisor 不会越限 |
| `personal-dell` | `UNKNOWN_DEVICE`（无 provider binding） | 不是下线；需重新绑定后才能读 |
| 真实上线/灰度 | 未授权 | Release Captain + 逐台 local grant |

**没有**任何一条被报成成功。`UNKNOWN` 不会被涂成绿色。

---

## 8. Operator handoff

```bash
S=~/runtime-assets/fleet-next-local-goal-supervisor/db20d2ed079c7ab3b7f8967a45e1b8701f78e333/supervisor.py
STATE=~/state/fleet-next-local-goal-supervisor
CONF=~/.config/fleet-next-local-goal-supervisor/config.json

# 查看
systemctl --user status fleet-next-local-goal-supervisor.service
python3 $S --state-dir $STATE --config $CONF status
journalctl --user -u fleet-next-local-goal-supervisor.service -n 50 --no-pager

# 暂停（kill switch）
python3 $S --state-dir $STATE stop
# 恢复
python3 $S --state-dir $STATE resume && systemctl --user start fleet-next-local-goal-supervisor.service

# 只跑一次（先停服务，避免锁冲突）
systemctl --user stop fleet-next-local-goal-supervisor.service
python3 $S --state-dir $STATE --config $CONF once
systemctl --user start fleet-next-local-goal-supervisor.service

# 只观察不写状态
python3 $S --state-dir $STATE --config $CONF plan

# 诊断
systemctl --user show -p ActiveState,SubState,MainPID,NRestarts --value fleet-next-local-goal-supervisor.service
cat $STATE/goal.json | python3 -m json.tool | head -40
ls -lt $STATE/receipts | head
```

**刷新 Fleet 快照**（由具备 MCP 授权的 producer 执行）：

```bash
python3 $S snapshot --raw <sessions.json> --out $STATE/fleet-sessions.json --producer <id>
```

快照超过 300 秒即为 `STALE`；缺失为 `UNKNOWN`。两者都不会被当作绿色。

### 启用真实派发（需 Owner 明确授权）

1. 指定并验证一个 dispatch provider；
2. 编辑 `config.json`：`dispatch.enabled=true`、`dispatch.provider=<name>`；
3. 确认 `max_inflight` 与现有在途数；
4. `systemctl --user restart` 后观察 `decision` 是否变为 `DISPATCH`。

在此之前，配置保持 `enabled=false` / `provider=null`。

### 回滚 / 卸载

```bash
systemctl --user disable --now fleet-next-local-goal-supervisor.service
rm ~/.config/systemd/user/fleet-next-local-goal-supervisor.service
systemctl --user daemon-reload
# 状态与证据保留在 ~/state/fleet-next-local-goal-supervisor/（不自动删除）
```

不影响旧 Fleet、CloudCLI、Fleet Next MCP 或任何其它 Owner 的服务。

---

## 9. 边界声明

- 本组件是**负责监督开发的 Goal Supervisor**，不是产品内的 **Agent Monitor**。
  它不投递飞书、不建第二事件总线、不代替 #424 的观察器。
- 不修改旧 Fleet 0.1.0、现有 ChatGPT plugin、旧路由/旧状态/OAuth secrets。
- 不 main merge、不 force-push、不触碰其它 Owner 的 worktree/PR。
- 证据全部落在本机隔离目录；仓库内只提交上述独占路径。

---

## 10. W19E 实时复验（2026-10-29T14:30–14:33+08:00）

本节只记录本次实测，替代此前所有“撰写时 pending”的表述。

### 10.1 修复前的实时状态

```text
now        2026-10-29T14:30:31+08:00
unit       active (running) since 2026-10-29T02:54:05 CST；NRestarts=0
MainPID    4137465（连续运行 11h）
ticks      336
heartbeat  2026-10-29T14:29:07+08:00，decision NO_DISPATCH_CAP
sources    github=OK  ci=OK  local_agents=OK  fleet=STALE
STOP       absent
ledger     0 entries；dispatches=0
cursor     e69fc0337ae5d33000478e3d5162077f525bc8cc
```

**发现缺陷**：远端 `main` 实际为
`44f039d70dc4f593ea9db73aae873be3455fc48c`（2026-10-29T06:12Z），
而 supervisor 报了 `e69fc03`。原因不是网络：`observe_github` 用的是本地
canonical checkout 的 `git rev-parse HEAD`，该 checkout 未 fetch。
**本地 HEAD 是时钟，不是远端事实。**

### 10.2 修复

- 改为 `gh api repos/<repo>/commits/main --jq .sha`，并记录
  `head_source: "remote"`；
- 远端读取失败时返回 `UNKNOWN`，**不回退**到本地 HEAD；
- 新增两条测试：
  `test_github_head_comes_from_the_remote_not_the_local_checkout`、
  `test_github_head_does_not_fall_back_to_a_stale_local_head`；
- 本地测试：**27 passed**（supervisor）+ **4 passed**（collection scope）；
- commit `db20d2ed079c7ab3b7f8967a45e1b8701f78e333`。

### 10.3 受控重启与修复后的实时证据

授权依据：原始 user-scope 任务已授权本单元的启停与验证；本次只重启
`fleet-next-local-goal-supervisor.service`，不触碰任何既有服务。

```text
restart    2026-10-29T14:32:47+08:00
MainPID    4137465 → 130318
boot       same boot epoch；resume_count=0（重启≠重开机）
ticks      338（延续，未重置）
head       44f039d70dc4f593ea9db73aae873be3455fc48c  (head_source=remote)
sources    github=OK  ci=OK  local_agents=OK  fleet=FRESH
decision   NO_DISPATCH_CAP；dispatches=0；ledger=0
verify-lock  exit 1（运行中拿不到锁）
2nd instance exit 3（already_running）
STOP       absent
```

### 10.4 派发能力核查（为什么仍是 observe-only）

| 候选 | 实测 | 结论 |
|---|---|---|
| `claude --bg` | 命令真实存在（`claude --help`） | 每次真实使用都会向模型发 prompt，属**付费模型路径**，W19E 明确排除；未创建 session |
| 既有 user-scope credentialless provider | `serverosctl` 是控制 CLI，无 dispatch 后端；systemd 无 runner/dispatch 单元 | **不存在** |
| Fleet MCP | `127.0.0.1:3411/mcp` 需 OAuth；`auth.pat_enabled: false` | 未启用 PAT/OAuth，未改 auth |
| 本机 Claude agents | 17 个（15 idle / 2 busy） | 只读观察；发消息给 idle session 仍会触发付费模型，未做 |

激活闸门实测（独立状态目录、临时配置、未改永久配置）：

```text
start 2026-10-29T14:32:22+08:00  end 2026-10-29T14:32:24+08:00
tick_id            b24cf1d9ac2f
decision           BLOCKED_ACTIVATION
gap                NO_VERIFIED_DISPATCH_PROVIDER
effective_inflight 9
would_dispatch     0
dispatches         0
ledger             0
```

Fleet 快照实时值：120 sessions，**9 RUNNING_AGENT** / 87 MANAGED_IDLE /
24 RESIDENT_HOST；`device:personal-dell` = `UNKNOWN_DEVICE`。

### 10.5 解锁派发需要 Owner 选择（三选一）

| 选项 | 内容 | 代价 / 边界 |
|---|---|---|
| **A** | 授权 `claude --bg` 的有界付费模型预算（例如 N 次/日，cap 3，STOP 可杀） | 产生模型费用；需预算与审计 |
| **B** | 授权一个确定性的本地命令 provider（只允许 read/test 白名单，无模型） | 无模型费用；需 Owner 明确允许进程级 dispatch |
| **C** | 配置一个已验证的 Fleet provider（PAT/OAuth） | 本任务明确禁止擅自启用；需 Owner 决策 |

在 Owner 选择之前，`dispatch.enabled` 保持 `false`，supervisor 继续 observe-only。

### 10.6 监控面待更新（需下次授权配置变更）

当前永久配置仍 watch `issues [478,425,424,434]`、`prs [507,508,509]`。
#515 已成为唯一收口主表；建议下次授权时更新为 `[515,478]` 与当前有效 PR。
本次未改永久配置。

### 10.7 #512 与当前 main 的合流结果

2026-10-29T14:35+08:00 将 `origin/main`（`44f039d70dc4`）合入本分支，未 rebase、
未 force-push。合流后：

```text
head      ddb23ce12e59c45db47607642403889fa3bdf0f7
base      44f039d70dc4f593ea9db73aae873be3455fc48c
local     27 passed (supervisor) + 4 passed (collection scope)
hosted    5/5 PASS  (4 shards + plugin-source)
```

此前基于 `e69fc03` 的 head 上，shard 4/4 的
`tests/test_direct_lark_notify.py` 失败并非本 PR 引入；该修复已随 main 的
#415 进入基线，合流后消失。**#512 保持 Draft**——它是 main-based candidate，
但是否进入 main 仍取决于 §10.5 的派发选项决定。
