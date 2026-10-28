# Fleet 卡：覆盖、观众门、三时段趋势（issue #170 · 承接 PR#181/#218）

本文件记录这一支（`feat/fleet-coverage-audience-gate-20261016`，stacked on PR#181 的
`4535dca`）做了什么，以及**第一次交付被审查打回后的修正**。**只做开发**：不部署、
不 enabled 定时器、不发真实飞书、不注册个人节点、不改生产。

## 0. 纠正"省略 ≠ 覆盖"（沿用）

PR#181 的 fixture 曾故意省掉 `device:personal-dell`。那是错的：把一台设备从卡上拿掉，
和"这台设备不该给人看"是两件事——省掉之后那张卡在机器掉线时反而更安静。现在：

- **覆盖由 `EXPECTED_DEVICES` 声明**，每条都成卡上一行（无 provider 也成行、UNKNOWN、
  不编数字）；
- **谁看得到由观众门决定**。

清单：`device:tokyo-shared-dev-01`(corporate) / `device:llm-lab-dell`(corporate) /
`device:personal-dell`(**private，目前无 Fleet provider**) / `device:work-fj`(corporate，可选第四)。

## 1. 从未接入 vs 曾失联

- **从未接入**（`observed_ever=False`）：覆盖缺口 → 表头「尚未接入」、判词
  **UNCONFIRMED（灰）**，不进"需要关注"；
- **曾可达现失联**：保持 PR#160 的 **CRITICAL 红**。

## 2. 观众门：默认拒绝 + 签名判决（本次修正的核心）

### 2.1 第一次错在哪

审查指出的两条，都是**门禁被设计成了可选**：

1. `open_gate()` 返回 `{"ok": True}`，`apply_plan(..., gate=open_gate())` 配任意
   transport 就能绕过收件人检查——**一个调用方自己写的布尔，不是"有人检查过"的证据**。
2. `AudiencePolicy.allows()` 对 `visibility=corporate` 无条件返回 `True`，任何未知
   user/chat 都拿到公司设备读数——**默认发布给所有人，再由策略说谁不能看**。

### 2.2 现在的形状

**A. 策略默认拒绝（`runtime/fleet_monitor_source.py`）**

- corporate 设备只对**显式列在 `approved_corporate` 的收件人**、或**该设备自己的
  `device×recipient` 授权**可见；其余一律不可见。
- private 设备**只看自己的授权**，**永不**参考 corporate 名单：被批准看公司机器，
  对"某人的私人笔记本"不构成任何结论——这条推断必须不存在。
- 策略里没出现的设备 = private 无授权（关闭方向）。

**B. 判决是签名类型，不是布尔（`runtime/fleet_hub/fleet_audience.py`，纯模块）**

- `AudienceVerdict` 是 frozen dataclass，字段含**收件人**、**设备集合**（及
  `device_signature`）、`ok/denied`、`issuer`、**canonical card hash**，以及一个用
  **每进程随机 HMAC 密钥**签出的 `mac`；`__post_init__` 校验签名，**手搓的实例在构造处
  就被拒**。
- 唯一构造器 `issue_verdict`；两个生产者：`authorize_devices`（策略判决）与
  `preview_gate`（本地预览）。
- 该模块被 `fleet_monitor_source` 与 `fleet_monitor_plan` **以同名单例加载**，所以
  签出的判决就是发送方 `isinstance` 检查的那个类型。

**C. 发送前 `apply_plan` 逐项核对（`runtime/fleet_monitor_plan.py`）**

按顺序：**类型**（非 `AudienceVerdict` 一律 `VERDICT_UNTRUSTED`，字典/字符串/None 都拒）
→ **`ok`**（否则 `AUDIENCE_DENIED`）→ **收件人**（判决的 audience 必须等于本次发送的收件人，
取自参数或 transport 自身，不符 `AUDIENCE_MISMATCH`）→ **设备集合**（调用方声明清单的
`device_set_signature` 不符则 `DEVICE_MISMATCH`）→ **卡绑定**：真实 transport 必须是
`policy` 签发（`preview` 一律 `PREVIEW_ONLY`）且 `mac` 绑定的 **canonical card hash
等于这张卡**（不符 `VERDICT_MISMATCH`）。

于是：**preview 只能用于内存 mock**（`type(transport) is MockTransport`，不是
`isinstance`——子类覆写 `send` 就是真 transport）；**为别的卡/别的设备清单/别的收件人
签发的判决都不能用**。所有拒绝都**不触碰 transport、不产生任何送达收据**
（`receipt`/`remote_id` 为 `None`）：被拒 ≠ 发了但失败。

`open_gate()` **已删除**（不是改名）：它的语义就是"任何调用方都能花在真实发送上的
`ok=True`"。

### 2.3 不回显收件人

`parse_recipient` 的拒绝句**不再包含收到的原文**（未校验的、可能形似 token 的标识会被
日志和卡片原样复制，而它正是我们还没对之建立任何结论的东西）；`AudiencePolicy.visible()`
的拒绝句只给**台数与 kind**；模型的 `audience` 字段只留 `{"withheld": n, "kind": …}`，
**不含收件人**。

## 3. 三时段趋势归档（不变）

`TrendArchive` 按 `(device,label)` 存**带时间戳、已 attributed** 的样本，按样本自身偏移
分 `morning/afternoon/night`。**拒收**：无时间戳 / 未 attributed / 非数字 / 缺字段 /
携带 `argv·environ·cwd·prompt·token` 等私密字段——**不造数据**。主 V13 卡与人类小时摘要
读**同一份样本**（`device_from_archive`）。没有第二个 renderer/队列/状态权威。

## 4. 明确没做 / 不声称

不部署、不 enabled timer、不发真实飞书、不注册个人节点。`chart` 门仍关闭；
`column_set`/手机窄屏真机验收仍未做。**没有任何生产 Feishu sender 经过端到端验收**
（见 PR 描述"未验收"一节）；本分支不运行真实 lark-cli。

## 5. 证据

- `tests/test_fleet_coverage_audience.py`：覆盖 / 从未接入=灰 vs 曾失联=红 / 策略
  （**默认拒绝**、未批准 chat 拒、未知收件人拒、grant 精确、corporate 不反推 private、
  文档往返、坏词拒）/ **门禁负例**（字典 OK 拒、字符串/None/对象拒、手搓 verdict 在构造处
  拒、preview 对真 transport 拒、别的收件人拒、别的卡拒、别的设备集合拒、无卡绑定拒、
  未批准收件人拒、私人设备对公司群拒）/ 允许的发送带收据 / **不回显收件人** / daypart /
  趋势归档 / 渲染卡 / mock PATCH 同 id·超窗 successor·失败不记 DELIVERED。
- `runtime/fleet-monitor-card.fixture.json`：公司观众的渲染卡 JSON。
- `tests/test_fleet_monitor_plan.py`：`preview_gate`；无 authorization → `VERDICT_UNTRUSTED`。
- `tests/test_fleet_monitor_card.py`：fixture 覆盖全清单（4 台），个人机 UNKNOWN 行。

运行方式见 `memory/serveros-pytest-invocation-rule`；本机共享压力下用有界 scope
（MemoryMax 1G / SwapMax 0 / CPUQuota 25% / TasksMax 64 + timeout）。
