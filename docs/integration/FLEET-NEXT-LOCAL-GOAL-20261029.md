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
| `tests/test_fleet_next_local_goal_supervisor.py` | 25 条契约测试 |
| `docs/integration/FLEET-NEXT-LOCAL-GOAL-20261029.md` | 本文 |

### 3.2 运行时（派生安装，绝不回写源码）

```text
源码 commit : 5da979a03db1ed4b819a5fa21c432d1d5335a13a
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
S=~/runtime-assets/fleet-next-local-goal-supervisor/5da979a03db1ed4b819a5fa21c432d1d5335a13a/supervisor.py
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
