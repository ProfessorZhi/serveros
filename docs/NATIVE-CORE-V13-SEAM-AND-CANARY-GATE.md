# Native C++ Core：只读复核、V13 数据接缝、canary 上线门禁

本文件是 **B 线（Native C++ Core + V13 Monitoring）** 对 PR #180 的**只读复核**，
加上 V13 数据接缝的坐标说明，以及一份**上线门禁**——把「开机后 Core 应管受权
服务 → 健康检查 → 有界自愈 → 复查」的真实 canary 步骤写清楚。

**本文件不是 canary 本身。本任务不执行 canary，不安装、不启用、不重启任何
服务，不打开 `--enable-actions`，不打开 systemd linger，不让 Python `serverosd`
与 C++ Core 双写。** 门禁是给人执行用的检查单。

## 0. 范围与依据

- 复核对象：PR #180（分支 `feat/native-core-stage2-serve-20261014`，尖端
  `ecca25f`），即 `native/node-core/supervisor/` 的 C++20 常驻 Core。
- 依据只来自 **#180 的代码与契约文本本身**（`supervisor/` 下的 header 注释、
  `contract/desired-state.v1.md`），不是记忆或推断。
- 本分支**没有**修改 `native/` 下任何文件。

## 1. #180 的 observe-only 实际生效点（复核结论）

「observe-only」在 #180 不是一句注释，而是分别落在五处、各自可被测试断言：

| 生效点 | 坐标 | 事实 |
|---|---|---|
| 计划的最后一条规则 | `supervisor/include/supervisor/plan.hpp` 规则 10 | `observe_only` 产出**拒绝**、**零动作** |
| 文档默认值 | `contract/desired-state.v1.md` | `observe_only` **默认 true**；写错类型（`"false"` 字符串）是**拒绝**，不是强制转换 |
| 所有权边界 | 同上；`plan.hpp` 规则 2 | `owner` 必须等于 `principal`，否则**拒绝而非过滤**——一个 Core 管一个用户是边界，不是约定 |
| 单位 id | `systemd_user.hpp` + `desired-state.v1.md` | `valid_unit_id` 是**形状白名单**：`a.service --user`、`$(id).service`、`../../etc/passwd.service` **无法表示**，不是被过滤 |
| 未知 ≠ 停止 | `plan.hpp` 规则 5；`systemd_user.hpp#parse_systemctl_show` | 读不到的 ActiveState 是 **UNMEASURED**，绝不是 `Inactive`；未知状态词不会变成故障 |

其余三条我复核为**正确的加固**，也是门禁要检查的前提：

- **has 状态保持**：`serve.hpp` 的时钟/睡眠/停止都是注入的；不回补错过的拍；
  时钟倒退**重新锚定并计数**，不成风暴；tick 内异常**被捕获计数**而非退出
  （进程退出交给 `Restart=on-failure`，一个 Core，不是两个）。
- **有界自愈**：`recovery.hpp` 的预算是**持久化**的（`to_json`/`load`）且
  **时钟注入**的；每小时三次、60/300/900s 梯子、熔断器最后裁决。
- **只读状态面**：`status_socket.hpp` 是**一条只读请求**，无动词、无 argv、
  无路径可触达程序；路径必须落在 `$XDG_RUNTIME_DIR`（**无 `/tmp` 兜底**）、
  socket 0600 / 目录 0700、并额外校验 `SO_PEERCRED`；**已存在的路径拒绝而非
  unlink**；请求有字节与时限上界。
- **回执**：`receipt.hpp` 四件分离（哪个 manifest〔schema/generation/sha256 的
  字节〕、计划了什么、要了什么、什么没做）；**不持任何密钥**，只证明「读了哪些
  字节」，从不证明「谁写的」。

## 2. V13 数据接缝（C++ 采集器 → Python 只读 adapter）

三段的坐标，明确各自**碰机器**的边界：

```
C++ 采集器                      native/node-core/{include/nodecore,src/linux_readers.cpp}
  → serveros/node-resource-snapshot.v1
  contract/node-resource-snapshot.v1.{md,schema.json}
  （adapter/native_node_core.py: collect(enabled=True)）

信封                            serveros/native-node-measurement.v1   (PR #161)

Python 只读 adapter             runtime/fleet_monitor_source.py
  accept_native_measurement(envelope, device_ref, now, freshness_bound)   ← PURE
  → 设备 entry
  → runtime/fleet_monitor_card.monitor_model(...)   ← PURE
  → 时段卡的「三设备摘要」由此渲染（runtime/card_period.py 的 FINAL 归档）
```

**接缝的 fail-closed 点**（复核 `accept_native_measurement` 的 docstring 与代码）：

- `scope` 不是 `host-wide` → **拒绝**（非全机读数不得渲染成全机读数）；
- `provenance.live` 不是布尔 → **拒绝**（是实测还是 fixture 不是可选项）；
- `attribution.scope` 不是 `self` → **拒绝**（C++ 采集器只带它自己 cgroup 的
  账户事实，当成某用户的 host-wide 份额是被禁止的）；
- `readings_available` 不是 `true` → UNKNOWN host 读数（**带 reason，不是 0**）；
- 新鲜度无法确立 → STALE 或 UNKNOWN。

**对时段卡的意义**：`card_period` 的 row 与 FINAL 归档**不新造**三设备读数，
它是 `monitor_model` 的输出；而 `monitor_model` 只吃本接缝的 entry。所以
「C++ 采集器尚未接通」这件事，在时段卡上表现为**读数 UNKNOWN 带 reason**，
**不是**空的绿值——这正是「不能伪造 live」在代码上的落点。

## 3. canary 上线门禁（**本任务不执行**）

以下步骤是**给人执行**的检查单。每一步的产物都应当是**回执/命令输出**，而不是
「看起来对了」。

### 3.1 前置（全部为真才可开始）

1. `desired-state.v1` manifest 已人工编写并复核：`schema` 精确、`owner ==
   principal`、`unit` 命中 allowlist、`generation` 单调递增。
2. **`observe_only: true`**（首轮）。打开它必须是显式改写，不是遗漏。
3. Core 单元（`supervisor/systemd/serveros-native-core.service`）以
   `systemd --user` **安装但未 enable**；**不安装 linger**。
4. **只有一个写者**：每用户一个 Core；Python `serverosd` **不**对同一批受权
   服务写。二者同时写是本门禁要排除的头号风险。
5. 状态 socket 路径落在 `$XDG_RUNTIME_DIR/serveros/`，0600/0700，`SO_PEERCRED`
   校验已就位。
6. 回滚已备：`systemctl --user stop serveros-native-core.service` +
   manifest 回退 + 确认无 linger。

### 3.2 canary 步骤（受权服务 → 健康检查 → 有界自愈 → 复查）

1. **空转一轮**：`observe_only: true` 跑一拍，读回执，确认**零个 verb**、
   拒绝项与计划项都在。
2. **回执自洽**：对 manifest 文件跑 `sha256sum`，与回执里的 manifest sha256
   逐字节相等（这就是「按 generation 7 行动」可核对的意思）。
3. **单点放开**：对**一个非关键、已受权**的服务把 `observe_only` 置 false、
   `generation++`，仅此一个（其余仍 refuse）。
4. **开机路径**：停掉该服务后依 `start_on_boot` 触发；确认只有在**开机**判定里
   「缺失」才被证明，在 tick 上 plain `inactive` 不被当成崩溃（`plan.hpp` 规则 7）。
5. **健康检查**：`systemctl --user show` 读 `ActiveState`/`SubState`；确认一个
   **未知状态词落到 UNMEASURED 带 reason**，而不是 `Inactive`。
6. **有界自愈**：kill 该服务，确认每小时 ≤3 次、梯子 60/300/900s、超限后熔断器
   打开，且**每一次尝试都有一条回执行**。
7. **复查**：canary 窗口结束后确认服务在、且**预算在 Core 重启后仍持久**——
   这是「预算不是每次都重置」的反证（`Restart=on-failure` 否则会每崩一次送三个
   新名额）。
8. **任一步失败即回滚**（§3.1.6），并把回执留档。

## 4. Windows SCM：未实现，不假装覆盖

- #180 的自愈面是 **POSIX/`systemd --user`** 专有
  （`supervisor/include/supervisor/systemd_user.hpp` 是全组件唯一命名程序的
  地方）。**没有** Windows SCM 等价实现。
- 因此对设备 `device:work-fj`（FJPCOS Windows）：**Fleet 监控卡**把它当作**只读
  设备**覆盖（CPU/RAM/GPU/VRAM/网络/服务计数），但 **Core 的「管服务/自愈」能力
  在 Windows 上不存在**，本文件不声称任何 SCM 覆盖。

## 5. 明确没做

- 未安装/启用/重启任何服务，未打开 `--enable-actions`，未打开 linger。
- 未让 Python `serverosd` 与 C++ Core 双写，未读取任何他人账户的监控私密字段。
- 未修改 `native/` 下任何文件；未改 #180 分支。
- **未执行 canary**；§3 是检查单。
