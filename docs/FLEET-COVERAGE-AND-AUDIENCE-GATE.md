# Fleet 卡：覆盖、观众门、三时段趋势（issue #170 · 承接 PR#181）

本文件记录这一支（`feat/fleet-coverage-audience-gate-20261016`，stacked on PR#181 的
`4535dca`）做了什么。**只做开发**：不部署、不 enabled 定时器、不发真实飞书、不注册
个人节点、不改生产。真实私有数据不会进公司群——这条由代码在发送前强制，不是文档承诺。

## 0. 纠正的上一版错误：省略 ≠ 覆盖

PR#181 的 fixture **故意省掉了 `device:personal-dell`**。那是错的：把一台设备从卡上
拿掉，和"这台设备不该给人看"是两件事，省掉之后那张卡在"机器掉线"时反而更安静——而
安静正是它最该出声的时候。本分支改为：

- **覆盖是声明出来的，不是发现出来的**：`fleet_monitor_source.EXPECTED_DEVICES` 声明
  要带哪些设备，**每一条都成为卡上的一行**，无论有没有采集结果。没有 provider 的设备
  以 UNKNOWN 出现，绝不省略、绝不编造数字。
- **谁看得到，是观众门的事**，不是 UI 的事（见 §2）。

## 1. 期望设备清单与"从未接入 vs 曾失联"

`EXPECTED_DEVICES`（`runtime/fleet_monitor_source.py`）：

| device_ref | 说明 | visibility | required |
|---|---|---|---|
| `device:tokyo-shared-dev-01` | 东京共享开发机 | corporate | 是 |
| `device:llm-lab-dell` | A6000 推理机 | corporate | 是 |
| `device:personal-dell` | DellPCOS（个人机，**目前无 Fleet provider**） | private | 是 |
| `device:work-fj` | FJPCOS 工作站（可选第四台） | corporate | 否 |

个人机目前没有 provider，所以它的行走 **UNKNOWN 且 `observed_ever: False`**——这是
真话，不是编的。

**两种 UNKNOWN 不是同一件事**，卡上分开表达（`runtime/fleet_monitor_card.py`）：

- **从未接入**（`observed_ever: False`）：覆盖缺口 → 表头「尚未接入」，判词
  **UNCONFIRMED（灰）**，不进"需要关注"。一台还没有 provider 的机器不该把卡永久变红。
- **曾可达、现在失联**（`observed_ever` 缺省即 True）：这是 PR#160 既有的发现规则，
  **保持为 CRITICAL 红**。

这条区分是必需的：把"没有 provider"也渲染成红色事故，会训练读卡的人忽略真正的那次红。

## 2. 观众门：device × recipient，默认关闭

`AudiencePolicy`（`runtime/fleet_monitor_source.py`）：

- **corporate 设备**：对公司收件人默认可见（它是公司的机器）；
- **private 设备**：**只有**一条显式的 `device × recipient` 授权指名了**这个**收件人，
  才可见。`chat:` 群和别的 `user:` 都拿不到。
- **未在策略里出现的设备 = private 无授权**（关闭方向），所以新加入的设备默认**扣下**，
  直到有人写明谁可以看，而不是默认**发布**到有人写明谁不能看。

策略本身是一份 `serveros/fleet-audience.v1` 文档，由 `load_audience_policy` 读入；
`default_policy` 从清单推出默认策略，`audience_policy_document` 把它写回成文档（可往返）。

**门在两处、同一条规则**（`authorize_devices` 是唯一判据）：

1. **建模型时**（`fleet_model` / `model_from_fixture` 的 `apply_audience`）：被拒的设备从
   模型里**移除**——卡上物理上没有这一行，不是把它藏起来。卡上只记一个**被扣下的台数**
   （`audience.withheld`），**不记名字**，因为在公司卡上写名字本身就是泄漏。
2. **发送前**（`fleet_monitor_plan.apply_plan`）：**没有 verdict 就拒绝发送**
   （`GATE_REQUIRED`），verdict 说不行也拒绝（`AUDIENCE_DENIED`）。拒绝时**不触碰
   transport、不产生任何送达收据**（`receipt` 为 `None`、`remote_id` 为 `None`）。

第 2 条是关键：只做隐藏的 UI 是 UI，不是边界；有人手工拼一张带私有设备的卡时，拦住他的
是发送门。而且"被拒"与"发了但失败"是两种不同的记录——前者**没有回执**，后者才是一条
投递记录。把前者也记成后者，就抹掉了这个区别。

## 3. 三时段趋势归档

`TrendArchive`（`runtime/fleet_monitor_source.py`）：按 `(device_ref, label)` 存**带时间戳的、
已授权的**样本，按三个 daypart 分桶：`morning`(05–12) / `afternoon`(12–18) / `night`(18–05)，
**按样本自己的偏移**判断（东京的傍晚不是 UTC 的傍晚）。

拒收（`TrendSampleRefused`）：没有可解析 `observed_at`、`observed_at` 缺席、
`attributed is not True`、值不是数字、缺 `device_ref`/`label`、以及携带
`argv/cmdline/environ/cwd/prompt/token/…` 之一的样本。**不做假数据**：没有时间戳的
样本不会被安上一个时间。

- 主 V13 卡与"人类小时摘要"读**同一份样本、同一个仓**（`device_from_archive`），所以两个
  面不会对同一个趋势各说各话——第二个仓就是第二个状态权威，那正是禁止的东西。
- 归档**只记测量**，不决定什么该发；它是 `card_delivery` 账本的**趋势孪生**（同样的
  单写者 + 整文档写），**没有**账本那种权威。
- 路径 `trend_archive_path(state_path)` 与送达账本同目录。

## 4. 复用、不新增

- 卡片仍是 #160/#181 的 CardKit 2.0 渲染器；`config.update_multi`、`element_id` 契约、
  390px 预算、`chart` 门**都不变**。
- 投递仍走 `runtime/message_transport.py`（send/patch）、`runtime/card_delivery.py`
  （frozen payload、14 天窗口、successor、回执状态）。每 600s 对**同一条** `remote_id`
  PATCH；超窗 successor；幂等键；失败不记 DELIVERED。
- **没有**第二个 renderer、队列或状态权威。

## 5. 明确没做 / 不声称

- 不部署、不 enabled timer、不发真实飞书、不注册个人节点（无 owner 目的地授权）。
- 不向公司群发任何个人数据：由 §2 的门在发送前拦住。
- `chart` 门仍关闭；`column_set`/手机窄屏真机验收仍未做。
- 三时段归档是**测量仓**；真机上尚未有真实样本接入（缺 provider 的设备 UNKNOWN 是真话）。

## 6. 证据

- `tests/test_fleet_coverage_audience.py`（新，**51 例**）：覆盖（全清单成行、无 provider
  仍成行、未声明设备被追加不丢）、从未接入=灰 UNCONFIRMED / 曾失联=红、观众策略
  （corporate 开放 / private 需授权 / 未列出默认关闭 / 文档往返 / 坏词被拒）、
  发送门（无 verdict 即拒、被拒无收据且不触碰 transport、允许才送达、模型与门同一条规则）、
  daypart（三时段、按自身偏移、坏时间无 daypart）、趋势归档（拒假样本、拒私密字段、
  三桶分离、落盘往返、不可读文件不替换）、渲染卡（2.0 + update_multi、390px、element_id、
  公司卡不含私有设备）、mock 投递（同 id PATCH、未变不重复、超窗 successor、失败不记 DELIVERED）。
- `runtime/fleet-monitor-card.fixture.json`（新）：公司观众的渲染卡 JSON，可直接读。
- `tests/test_fleet_monitor_plan.py`（改）：`apply_plan` 现在需要 gate；`MockTransport`
  对 PATCH 回显被改的消息 id（原实现给 patch 新造 id，等于模拟第二张卡的缺陷）。
- `tests/test_fleet_monitor_card.py`（改）：fixture 覆盖全清单（4 台），个人机 UNKNOWN 行。

运行方式见 `memory/serveros-pytest-invocation-rule`；本机资源受限时用有界 scope
（MemoryMax 1G / Swap 0 / CPU 25% / Tasks 64 + timeout）。
