# Fleet V1 milestone runner（#417/#424/#434）

一个**有界本地进程入口**，把已经存在的三块能力装配成一个可启动/可停止的
运行单元：

- #423 `MilestoneNotifier` + receipt ledger（判断与投递收据）
- #426 `SourceRead`（CloudCLI `run_get` / GitHub Actions，注入）
- #428 `MilestoneObserver.run_periodic`（一次 tick 的观察调度）

它**不**重新实现这些模块，也不新增第二套事件总线、Work ledger、常驻 LLM、
多 Agent manager。它只持有：一份授权 config、一个单活锁、一个 provider
factory、一个 source factory、一个 receipt ledger 路径、一个有界生命周期。

## 边界（先读）

- **不做真实发送**：provider 由调用方注入（生产上是已授权的 Tokyo 适配器；
  离线测试是 `tests/fixtures/.../fakes.py`）。runner 不读凭据、不读环境变量、
  不执行 `lark-cli`、不开 socket。
- **不做 OAuth / CloudCLI 客户端**：source 由调用方注入。真实 CloudCLI 适配器
  需要授权 reader；本包不登录。源不可用时由 #428 observer 记为 UNKNOWN，不是绿色。
- **不做服务**：本 PR **不安装、不 enable、不 start、不 stop** systemd unit 或
  Windows Task Scheduler。README 的 §服务提案只是 proposal，未执行。
- **不 24/7 推理**：每次运行必须有 `max_ticks` 或 `max_runtime_seconds`，且有
  硬上限（`MAX_TICKS_HARD` / `MAX_RUNTIME_SECONDS_HARD`）。没有常驻 LLM。
- **唯一 durable 状态**：#423 的 receipt ledger + #428 的 scheduling state；
  runner 自身只保留内存 tick 摘要（最多 200 条），不落第二个 event store。

## 配置（闭合 schema）

```json
{
  "schema": "serveros/fleet-v1-milestone-runner.v1",
  "ledger_path": "/var/lib/serveros/milestones/ledger.sqlite",
  "state_path": "/var/lib/serveros/milestones/observer-state.json",
  "lock_path": "/run/user/1000/serveros-milestone-runner.lock",
  "conversation_ref": "conversation:ops",
  "watches": [
    {"source": "cloudcli", "ref": "run_demo_1",
     "device_ref": "device:tokyo-shared-dev-01", "boot_id": "boot-A"},
    {"source": "github_actions", "ref": "ProfessorZhi/serveros:40486346485",
     "device_ref": "device:ci-ProfessorZhi-serveros",
     "source_sha": "3e9428ab5f057b5ac6a1da1cebda7bf05c47901c"}
  ],
  "interval_seconds": 60,
  "max_ticks": 60,
  "max_runtime_seconds": 3600,
  "max_sends_per_minute": 30,
  "options": {"max_reads_per_source": 8, "min_interval_seconds": 0,
              "base_backoff_seconds": 30, "max_backoff_seconds": 900,
              "reconcile_unknown": true},
  "provider_options": {},
  "source_options": {},
  "summarizer_options": {}
}
```

- `ledger_path` / `state_path` / `lock_path` 必须是**绝对路径**、互不相同、
  非 symlink、非目录；父目录必须存在。
- `watches` 至少一个；cloudcli watch 必须有 `boot_id`，CI watch 必须有
  `source_sha`（#428 的 scope 规则）。
- `max_ticks` / `max_runtime_seconds` 至少一个；`max_ticks` 最多
  `MAX_TICKS_HARD`，`max_runtime_seconds` 最多一天。只给 runtime bound 时
  `interval_seconds >= 0.05`，避免空转。
- `provider_options` / `source_options` / `summarizer_options` 原样传给注入的
  factory；runner 不解释、不记录其中的内容。config 不应放凭据。

## 启动

```bash
python3 integrations/fleet-v1-milestone-runner/runner.py \
  --config /path/to/runner.json \
  --provider-factory my_authorized_module:provider \
  --source-factory my_authorized_module:source \
  [--summarizer-factory my_authorized_module:summarizer]
```

- factory 规格是 `module:callable`；runner 只 import 该模块并调用一次。
- `MilestoneRunner.start()` 顺序：**先拿单活锁** → provider factory → source
  factory → optional summarizer → #423 notifier（唯一 ledger）→ #428 observer。
  锁被占用时在 factory 之前 `ANOTHER_RUNNER_ACTIVE` 失败关闭。
- `MilestoneRunner.run()` 调用 #428 `run_periodic`，每 tick 记录 summary；
  `request_stop()`（Windows 可测试的接口）或 POSIX SIGTERM/SIGINT 可停；
  `stop()` 释放锁并关闭 provider。
- 退出状态：`BOUNDED_STOP` / `STOPPED` → exit 0；`START_REFUSED` / `ERROR` →
  exit 2。stdout 是单个 JSON 文档，含 `service_state: SERVICE_NOT_INSTALLED`。

## 判定仍属于 #423/#428

runner 不重新分类里程碑，也不把异常升级成成功：

- 源/provider 失败 → observer 的 typed UNKNOWN/REFUSED；不发送、不记 DELIVERED。
- UNKNOWN delivery 的 reconcile 仍由 #428 observer 用同一 transport key 发起。
- summarizer 抛错 → #423 的确定性模板仍发送（RED 的 `RUN_FAILED` 兜底）。
- runner 自身的意外异常 → `RUNNER_ERROR`，exit 2；provider 没有被调用。
- 发送速率：`max_sends_per_minute` 的包装器在超限时返回 `delivered: false`，
  #423 记为 `DELIVERY_UNKNOWN`，绝不伪造 DELIVERED。
- 两个 runner 用同一 ledger（不同锁）→ 第二个在账本上 DUPLICATE，不重发。

## 测试

```bash
python3 -m unittest -v tests.test_fleet_v1_milestone_runner
```

15 个 offline tests，其中 **3 个独立 subprocess**：

1. `running → completed`：GPT 已结束的假源状态变更；runner tick 触发一次重大
   成功；重启同 ledger 不重发。
2. `failed` + summarizer 抛错：RED 确定性模板仍发送。
3. `running` + 第二个进程同锁：第二个在 factory 前 `ANOTHER_RUNNER_ACTIVE`；
   SIGTERM 后第一个 `STOPPED`、exit 0；无发送。

另外覆盖：源断线 UNKNOWN 后恢复、一次投递；两个实例共享 ledger 不重复；
send rate limit 记 `DELIVERY_UNKNOWN`；runner 意外异常 `RUNNER_ERROR` 不假报；
config 无界/相对路径/symlink/未知字段/坏 bounds 拒绝；`SERVICE_NOT_INSTALLED`
与 runner 源码无 `subprocess/socket/requests/urllib/systemctl/schtasks`。

测试使用 `tests/fixtures/fleet_v1_milestone_runner/fakes.py`（FakeProvider +
scripted FakeSource + 抛错 summarizer），通过 `PYTHONPATH` 注入 subprocess；
没有网络、CLI、凭据、服务或真实飞书。

## 服务提案（PROPOSAL ONLY — NOT INSTALLED）

本节是**提案**，本 PR 没有安装、enable、start、stop 任何服务。生产启用需要
Owner 的单独授权。

### systemd user timer（proposal）

```ini
# PROPOSAL ONLY. Do not install/start in this wave.
[Unit]
Description=ServerOS milestone runner (bounded, one-shot)
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /opt/serveros/integrations/fleet-v1-milestone-runner/runner.py \
  --config /etc/serveros/milestone-runner.json \
  --provider-factory serveros_lark_adapter:provider \
  --source-factory serveros_source_adapter:source
# The config MUST bound max_ticks/max_runtime_seconds; Type=oneshot exits.
[Install]
WantedBy=default.target
```

```ini
# PROPOSAL ONLY: a timer starts one bounded run; it is not a resident LLM.
[Timer]
OnUnitActiveSec=5min
Unit=serveros-milestone-runner.service
```

**Rollback proposal**：`systemctl --user disable --now` 对应 timer/unit，移除
unit 文件；保留 ledger 与 observer state 供审计，不删除 receipt。`SERVICE_NOT_INSTALLED`
是当前真实状态。

### Windows Task Scheduler（proposal）

```bat
:: PROPOSAL ONLY. Not executed.
schtasks /Create /TN "ServerOS Milestone Runner" /SC MINUTE /MO 5 ^
  /TR "\"C:\Python313\python.exe\" C:\serveros\integrations\fleet-v1-milestone-runner\runner.py --config C:\serveros\milestone-runner.json --provider-factory ... --source-factory ..." ^
  /F
```

**Rollback proposal**：`schtasks /Delete /TN "ServerOS Milestone Runner" /F`；
保留 ledger/state；不删除 receipt。`SERVICE_NOT_INSTALLED` 仍是当前状态。

## 证据分级

- **CODE**：`integrations/fleet-v1-milestone-runner/runner.py`。
- **offline fake E2E**：14 tests（含 3 subprocess），FakeProvider + scripted
  FakeSource，无真实 Feishu/CloudCLI。
- **SERVICE_NOT_INSTALLED**：未安装/未启停任何 unit/task。
- **HOSTED_CI**：Draft PR 的 `Tests`。
- **REAL_FEISHU_DELIVERY / REAL_DEVICE / OAuth**：**未声称**。只有真实 provider
  receipt + Owner 授权上线测试才能说“用户已收到”。

## 兼容性

runner 按路径加载最新的 #428 observer（`NOTIFY` 指向 #423 的 notifier），source
是 #426 的 `SourceRead` 协议实例；测试栈基于包含 #423/#426/#428/#440 的
`f7dbf385`。未修改这些模块的任何文件。
