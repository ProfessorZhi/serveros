# V13 双卡面 · 候选部署模板（全部 **DISABLED**）

**这些 unit 没有被 enable、没有 start、没有 reload、没有 linger。** 它们是随
release candidate 一起交付的**未启用**模板，供人评审后手工安装。本仓库的交付者
**从未**运行过 `systemctl --user enable/start/reload`，也未创建常驻 daemon。

## 边界（本轮不变）

| 项 | 状态 |
|---|---|
| `runtime/monitoring-policy.yaml`（`hourly` / `one-per-workday`）| **未改** |
| `tokyo-status-card.timer` / `tokyo-alert-digest.timer` | **未动**，仍在跑 |
| linger / 常驻 daemon / 凌晨后台任务 | **未创建** |
| C++ 自愈 / 新权限 / Windows / Dell | **未动** |

## 上线前必做（preflight）

1. **主机余量**：`runtime/surface_cli.py` 的 `load_gate()` 必须为 `ok`。
   当前判据：`load/CPU ≤ 2.0` 且 `swap 使用率 ≤ 0.90`。**读不到就当不安全。**
   ```bash
   python3 runtime/surface_cli.py --canary-user-vince --i-understand-this-sends --json | head -20
   ```
   若返回 `"blocked": "PREFLIGHT"`，**停**，不要在负载下发消息。
2. **身份**：`live_sender.owner_recipient()` 必须返回 `user:<owner open_id>`，且
   配置里声明的 app 与 `~/.local/share/lark-cli` 里存储凭据的 app **一致**。
   不一致即拒绝（不会替调用方挑一个）。
3. **单写者**：确认没有别的进程持有 `<state>/cards/locks/live.lock`
   （`flock`；持有者崩溃时内核自动释放）。
4. **离线回归**：先跑 `--dry-run`，确认 trace 里 `would_send` 与预期一致，
   且 `evidence` 为 `synthetic`。

## 安装（人工，逐步）

**两个模板的 `ExecStart` 指向稳定安装路径 `%h/.local/lib/serveros-v13-surfaces/`，
不是临时 worktree。** 该路径不存在时 `ConditionPathExists` 会让 unit 直接不跑。

```bash
# 0) 把 CLI 与它加载的 runtime 模块装到稳定路径，并给启动器执行位
mkdir -p ~/.local/lib/serveros-v13-surfaces
cp -r runtime ~/.local/lib/serveros-v13-surfaces/
cp runtime/surface_cli.py ~/.local/lib/serveros-v13-surfaces/surface_cli.py
chmod 0755 ~/.local/lib/serveros-v13-surfaces/surface_cli.py
# 1) 复制模板到用户 unit 目录（不 enable）
mkdir -p ~/.config/systemd/user
cp deploy/v13-surfaces/*.service deploy/v13-surfaces/*.timer ~/.config/systemd/user/
# 2) 只 reload daemon（不启动任何 unit）
systemctl --user daemon-reload
# 3) 显式 enable —— 这一步就是「批准上线」，本轮未做
# systemctl --user enable --now v13-live-heartbeat.timer v13-report-period.timer
```

两个 timer 调用的是**不同 mode**（`--live-heartbeat` / `--report-period`），
都**必须**带显式 `--state`；任一 mode 缺 `--state`、缺
`--i-understand-this-sends`、或拿不到 store 上的跨进程锁，都**直接拒绝**。

## 报告边界（需要人工核对，**未自动化**）

`v13-report-period.timer` 的 `OnCalendar` 是**默认时段表**的边界
（09:00 / 12:00 / 18:00）。真实的时段边界由**日历声明**决定，运行时的
`--report-period` 会向 `business_calendar` 询问并封存「已不属于当前时段」的报告，
所以**即使 timer 边界与声明不一致，报告也不会错封**——但会**错过或延后**归档。
上线前请核对：`python3 -c "…card_period.period_at…"` 的边界与本 timer 一致。
**这一步没有自动化测试覆盖。**

## 回滚

```bash
systemctl --user disable --now v13-live-heartbeat.timer v13-report-period.timer
rm -f ~/.config/systemd/user/v13-live-heartbeat.{service,timer} \
      ~/.config/systemd/user/v13-report-period.{service,timer}
systemctl --user daemon-reload
```
回滚**不影响**：旧生产 timer、`monitoring-policy.yaml`、以及已经发出的历史消息
（消息仍在飞书里，`remote_id` 记在既有 `card_delivery` 台账中）。本地状态可留在
`<state>/cards/surfaces.json`（含 `CREATE_PENDING`/`NEEDS_RECONCILE` 时**不要删**，
那会把一次未确认的发送读成「没有绑定」）。

## 仍未验证（不得当成已完成）

- **每半小时自动更新 / 告警即时 PATCH 的真实运行**：离线成立，**真机常驻未启用**。
- **报告边界与 timer 的一致性**：**未自动化**，需上线前人工核对（见上）。
- **`--report-period` 与 `--live-heartbeat` 的真实并发**：两者抢同一把 store 锁，
  离线测过；**真实 timer 下未验证**。
- **跨进程互斥**：`flock` 已实现并测过两进程竞争；但**未在真实 timer 下长期验证**。
- **真实 readback 匹配**：代码存在（`live_sender.readback`），**未对真实消息跑过**。
- **图表**：`charts_verified=False`，未开。
- **重大事故的手机主动通知**：飞书 PATCH 是**静默**的；短文本通知是**另一条策略**，
  需另行批准，本轮**未启用**。
