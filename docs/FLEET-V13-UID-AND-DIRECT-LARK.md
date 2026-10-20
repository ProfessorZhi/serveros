# Fleet V13 卡片 · per-UID 账户块 · 出站直飞书（issue #170 Phase B）

本文件记录这一支独立分支（`feat/fleet-v13-uid-directlark-20261014`，基线
PR #160 / `106d0f7`）做了什么、依据什么、以及**明确没有**做什么。它是开发记录，
不是发布说明：本分支不部署、不打开定时器、不发真实飞书。

## 0. 生产链路（只读核实，本分支不改动）

| timer | 触发 | 脚本 | 数据 | 发送 |
|---|---|---|---|---|
| `tokyo-status-card.timer` | `*:00` 整点 | `ops/scripts/tokyo-monitor/monitor_card.py` | `serverosctl card status --with-frozen --json` | `status_card.transport.deliver()` → `lark-cli im +messages-send` / `im messages patch`，`--as bot`，应用1 |
| `tokyo-alert-digest.timer` | `*:03/5` | `ops/scripts/tokyo-monitor/alert_digest_card.py` | `serverosctl alert digest` | 同上，判决 `patch/send/successor/refuse` |

产品侧另声明 `runtime/serveros-alert-sync.{service,timer}`（`*:4/5:00`）→
`serverosctl alert sync` → `_alert_provider()` → `BotMuxHumanControlProvider.notify`
→ `POST /api/trigger`。**本机未 enabled。**

**关键实测**：`BotMuxHumanControlProvider` 自己把 `human.notification.transport`
标为 **UNKNOWN**（`mcp/human_control.py`），因为无 `target.chatId` 的 trigger 是
合成 session、交给 MODEL、不上飞书；而 `runtime/notifications.py#deliver` 在
state≠SUPPORTED 时**调用前拒绝**。于是 `alert sync` 这条路径**从未真正发出东西**，
一律记 UNKNOWN、留在 pending。本分支修的就是这一条。

## 1. V13 卡片的兼容差异（`runtime/fleet_monitor_card.py`）

基线是 PR #160 的 CardKit 2.0 渲染器（`trend[]`、`DEFAULT_REFRESH_S=600`、
`rich`/`charts_verified` 双门）。三类改动，**主屏三块（设备读数 / 服务 / 账户）与
V13 布局不变**：

1. **可信契约修正**（依据见 §3，不是偏好）：
   - `config`：由 `{"wide_screen_mode": true}`（1.0 拼写）改为
     `{"update_multi": true, "width_mode": "default"}`（2.0 契约）。`update_multi`
     是共享卡标志，`v2 仅支持 true`，也是**消息可被 `im messages patch` 就地更新**
     的前提。
   - `_chart`：把 series 从 `chart_spec` 的**兄弟** `data` 字段移回契约要求的
     `chart_spec.data.values`（`xField`/`yField` 仍在 `chart_spec` 内）。旧写法是
     "看起来像"的猜测，契约并不描述它。
   - `element_id`：新增 `element_id()` 构造器，保证「字母开头、≤20 字符」。旧
     `_slug` 曾产出 `dev-device-tokyo-shared-dev-01-fold`（31 字符），违反契约；
     `validate()` 现在拒绝超长/数字开头的 id。`_slug` 已删除（唯一调用点已替换）。
2. **per-UID 账户块**（新增）：`account()` / `account_unobserved()`、`account_line()`、
   `_accounts_panel()`、`_account_order()`。每台设备多一个折叠面板「账户用量（N）」，
   每行是 `**账户 <名>** · CPU 已用/建议额度 核 · 内存 已用/建议额度 GiB`，第二行是
   `警戒线 …（软线）`。**额度是建议软线，不是 cgroup 硬限制**，`QUOTA_NOTE` 逐面板
   写明，字段名只叫 `quota`、从不叫 `limit`。账户超线（severity ∈ CRITICAL/ATTENTION）
   会被 `_attention` 置顶。排序键 `_account_order` 先按严重度、再按内存用量——这就是
   「管理员折叠的多用户排名」。
   - **隐私边界**：`validate_model` 拒绝账户携带 `argv/cmdline/environ/cwd/open_files/
     prompt/token/secret/password`；`uid` 必须是 int（内核 UID，不信命令行用户名）。
3. **fixture 收敛为三台授权公司设备**：`device:tokyo-shared-dev-01`（Tokyo vince）、
   `device:work-fj`（FJPCOS Windows）、`device:llm-lab-dell`（Dell A6000）。**移除了
   `device:personal-dell`**——私人机不是公司监控目标，摆在公司三台旁边正是要避免的泄漏。

## 2. #167 的字段 adapter（未合并，不读其 worktree）

`runtime/fleet_monitor_source.py::accept_uid_summary()` 是**本分支自己定义**的 typed
seam，契约 `serveros/uid-resource-summary.v1`。字段取自 issue #167 正文自己列的
producer 形状（`process_count`、`anonymous_bytes`（优先）/`rss_estimate_bytes`、
`host_mem_total_bytes`、`host_percent`、`top_process{pid,comm}`、`sampled_at`、
`provenance`）。它：

- **不读 #167 的工作树，也不复制其代码**；
- **不发明阈值、不推导 severity**：`quota`/`warn` 只在文档带有时透传，缺省渲染
  `未定义` 而非 `0`；severity 是 producer 的词；
- `provenance.live` 必须是布尔（fixture 不能被读成 live）、schema 不符即拒、
  无任何读数则 fail-closed 到 `account_unobserved`。

当 #167 落地时，其 producer 的字段名要么命中、要么这道 seam 响亮拒绝，而不会静默
读进另一个形状。

## 3. 依据的可信契约（不是编的 schema）

CardKit 2.0 的字段与组件语义取自 **lark-cli 自身内嵌的官方组件参考**，而非记忆或猜测：

- `lark-cli schema im.messages.patch`：14 天窗口、`content` ≤30 KB、`--data` 请求体、
  仅 interactive 卡可改。
- `lark-cli im +messages-send --help`：`--as bot|user`、`--chat-id`/`--user-id` 互斥、
  `--content/--text/--markdown/…`、`--idempotency-key` ≤50 字符。
- 内嵌的 Card 2.0 组件大纲与 `components/*.md`：
  - 顶层四固定字段（`schema`/`config`/`header`/`body`），`schema` 必须显式 `"2.0"`；
  - `config.update_multi` 默认 `true`，**v2 仅支持 true**；`width_mode`
    `default`(≤600px)/`compact`(400px)/`fill`；
  - 每组件 `element_id` 规则：**字母开头、≤20 字符**；
  - 硬限制：单卡 ≤200 元素、需客户端 ≥7.20；
  - `chart` 是 VChart 组件，series 在 `chart_spec.data.values`；且**移动端不支持部分
    VChart 属性**。

**chart 门仍然关着**：`charts_verified` 默认 `False`，默认卡只渲染文本 sparkline。
"schema 有文档" 不等于 "真机渲染过"，而参考文档自己就警告移动端有限制——所以本分支
只把 `_chart` 的形状对齐到契约，**不**打开门、**不**声称图表已投产可用。

## 4. 出站直飞书（`runtime/direct_lark_notify.py`）

新增 `DirectLarkHumanNotifyProvider`，只承担**出站**通知：

- `capabilities()` 声明 `human.notification.transport: SUPPORTED`（依据：`lark-cli
  im +messages-send` 正是本机状态卡与告警摘要已经在走的路径），入站三项
  (`human.identity.verify` / `human.approval.binding` / `interactive.rendering`)
  诚实标 **UNSUPPORTED**；
- `notify()` 走 `runtime/message_transport.LarkMessageSender.send_text()`；**任何失败
  抛 `DirectLarkNotifyFault`**，于是 `notifications.deliver` 记 `delivered: False`——
  「失败不得记 DELIVERED」由异常语义保证，而不是靠返回值的约定；
- **不持有凭据、不读凭据**：跑的是已认证的 `lark-cli`，凭据仍在 CLI 自己的
  `~/.local/share/lark-cli`（0700）；
- `message_transport` 补了 `send_text()`（告警通知，`--markdown`）与 `send_card()`
  （`--msg-type interactive --content <card json>`），与既有 `update()`（PATCH）共用同一
  个 `run()` seam；`update()` 在失败/无 id/超窗/超限时**绝不改发第二条**。

**`runtime/cli.py#_alert_provider()`**：设了 `SERVEROS_ALERT_RECIPIENT`
（`user:<open_id>` / `chat:<chat_id>`，沿用 transport 的前缀规则）时返回 DirectLark；
否则**照旧**返回 BotMux。**BotMux 未被删除、未被废弃**：其 `notify` 仍报 UNKNOWN，
而其**入站身份/审批**是当前唯一经核验的人类身份面。退役它是另一个决策，前提是一个
签名可验的 Feishu callback——本分支刻意保留其可构造性，不封死这条路。

## 5. 明确没做 / 不声称

- 不改生产：不动 `ops/` 脚本、不 enabled 任何 timer、不发真实飞书、不读生产 token。
- 不重写既有的 ops `status_card.transport` 或卡片主渲染器；V13 卡片是**增量**增强。
- 不删除 `mcp/human_control.py` 的入站身份/审批；出站迁移 ≠ 入站认证。
- **不声称 UI 已生产可用**：`column_set`（`rich=True`）与 `chart` 仍未经真机/手机窄屏
  验收。390px 只由 `MOBILE_LINE_CHARS` 的字符预算保证（见下）。
- 未动 PR #160 / #161 / #172 各自的工作树。

## 6. 证据

- `tests/test_fleet_monitor_uid.py`（新）：账户块渲染、软线注记、缺额度渲染 `未定义`、
  超线置顶、排名序、隐私字段拒绝、uid 必须是 int、`update_multi`、`element_id` 契约、
  chart 在 `chart_spec.data.values` 且仍受门控、`accept_uid_summary` 的映射/降级/拒绝。
- `tests/test_direct_lark_notify.py`（新）：capability SUPPORTED 且与
  `notifications.CAPABILITY` 同名、notify 走 `im +messages-send --as bot`、失败抛异常使
  delivery 记 False、无收件人拒绝、入站职责 UNSUPPORTED、`notifications.deliver` 端到端、
  **card-create → PATCH 同一 remote_id**、无 id/超窗 PATCH 拒绝且**不发第二条**、
  `_alert_provider()` 的选择。
- `tests/test_fleet_monitor_card.py`（改）：fixture 收敛为三台公司设备。
- `tests/test_message_transport.py`（改）：`SENT_AT` 由固定日期改为相对运行时间——
  原来写死 `2026-09-26`，今天已超出 14 天 PATCH 窗口，使该文件 6 个用例在**基线
  `106d0f7` 上就已经是红的**（已复现）；这不是本分支引入的失败，但挡在本任务核心路径
  （PATCH）上，故一并修成不会过期。

运行方式见 `memory/serveros-pytest-invocation-rule`：显式 `tests/test_*.py` +
`timeout` + `ulimit -v`，不裸跑。
