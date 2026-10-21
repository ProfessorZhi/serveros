# Fleet V1 独立 QA — Snapshot D（2026-10-18）

- **角色**：独立 QA lane（campaign `ProfessorZhi/serveros#244` / `#237`）。非 S2 开发者、非 I1 integrator。
- **状态**：`QA_PARTIAL`。**没有** QA PASS，**没有** signature，**没有** `V1_COMPLETE`。
- **对象**：S2 #252 精确 head `7a87e5cd6dc290a339c0d6d1eb6abf167b4b4cf1`（stacked on S1 #241 `d0754ed`）。
- **边界**：只改 `integrations/fleet-v1-qa/**`；未改产品、I1、G1、S2 分支或 `main`；未做 provider/network/credential/production 调用；无 subagents。

## 1. Snapshot 与 preflight

快照 UTC **`2026-10-17T22:15:59Z`**（local `2026-10-18T06:15:59+08:00`）。`git fetch --all --prune` exit 0。

| 对象 | 精确 head | live head |
|---|---|---|
| S2 #252 | `7a87e5cd6dc290a339c0d6d1eb6abf167b4b4cf1` | 一致 |
| S1 #241 | `d0754edfd65a490213a40b3033a70a43b0a4a187` | 一致 |
| F1 #243 | `4a5d0a06f2452424972d66399b7c2b60dc8d03a1` | 一致（guard 已于 Snapshot B 独立 10/10；本轮不重复） |
| G1 #255 | `619cf637cbdba3020f989cee3d9f72b7f2b25ce5` | 一致（未变） |
| I1 #254 | `baabd56223afe308b4cd529de58d1c762369a775` | 一致；**仍 pin S2 `fdc223f`**，落后本轮 head 1 commit |
| `main` | `4a3162864911420309665e92ec38fb68e9ef76ec` | 一致 |

Preflight `2026-10-17T22:15Z`：

- load `12.33 / 13.40 / 13.49`（128 vCPU）；cpu PSI some avg10 `0.42`；io some/full `0.37`；**memory PSI `0.00`**。
- MemAvailable 171 GiB；swap 已用约 30.0 GiB / 32 GiB（≈89.4%，SwapFree 3.5 GiB）；`pswpout` 未在本轮明显增长。
- 无 `pytest` / `serverosctl test` 进程；无测试锁。强制 `MemorySwapMax=0`。
- 判定 **resource GREEN**；执行一条受限本地命令。

## 2. 唯一一条受限命令（red → green 控制）

```sh
systemd-run --user --scope \
  -p MemoryMax=1G -p MemoryHigh=768M -p CPUQuota=25% -p MemorySwapMax=0 \
  -- bash -c 'ulimit -v 1048576; QA_TREE=<tree> QA_SCRATCH=<scratch>
              python3 qa_probe_d.py'
```

同一探针跑两棵树：**旧 head `fdc223f`**（red 控制）与**新 head `7a87e5c`**。

| 运行 | command exit | 结果 |
|---|---|---|
| `fdc223f`（previous） | **1** | 10 项失败：D3 ×4（跨 actor settle 被接受、principal 变 B、attempt 被改写）、D4b/c/d/e（终态回退/UNKNOWN 回退/终态不再 REPLAY）、D10a/b（空 writer、无 principal 仍 settle） |
| `7a87e5c`（claimed fix） | **0** | **33/33 检查通过**；`CONTROL_OK` |

日志：`run-4.log`，sha256 `7f3dd737984c7e476432cac5587303a2a0f49c3986a79fa7f71fdcfe97870330`。
探针：`qa_probe_d.py`，sha256 `0ec22145062c1c6d6ecf83d746f045ad7fbdb6bc01fdc7d569d658121e725f52`。

## 3. 独立结论（LOCAL，精确 SHA）

### 3.1 已关闭

| 项 | 独立负例 | 结论 |
|---|---|---|
| **D3 跨 actor 伪造 SETTLED** | actor B 复制 A 的 digest + 假 result → `UNTRUSTED_ACTOR`；attempt 仍为 `CALL_NOT_SENT`、principal 仍 `human:A` | **CLOSED (LOCAL)** |
| **D4 终态回退** | 先做**真实** settle，再试 `SETTLED→CALL_NOT_SENT`、`SETTLED→EFFECT_UNKNOWN`：均 `INVALID_CONTEXT`；`EFFECT_UNKNOWN→CALL_NOT_SENT` 亦拒；终态 retry 仍 `REPLAY`（不是 `PROCEED`） | **CLOSED (LOCAL)** |
| **D10 空 writer / 无 principal** | `current_writer={}` → `REFUSE_STALE_WRITER`；无 principal 记录 → `REFUSE_PRINCIPAL`；空 reconciler → `REFUSE_PRINCIPAL` | **CLOSED (LOCAL)** |

原子性（SOURCE，`runtime/agent_control_store.py:924`）：intent、actor、transition 三项检查都在 `put_dispatch_attempt` 的 `with self._mutation()` 内完成，再 `_flush()`；检查与写入是同一个锁内步骤，不存在“读后写”窗口。transition 由 `dispatch_attempts.dispatch_transition_allowed` 单点定义。

### 3.2 回归（LOCAL，全部通过）

- **D1**：authentic `CALL_NOT_SENT`；principal 取自 resolved context；`session_ref`/`runtime` 入记录。
- **D2/D2b/D2c**：同 key 换 session / runtime / principal，daemon 写点均拒绝。
- **D5/D5b**：无 result 的 settle、无前序 attempt 的 settle → `INVALID_CONTEXT`。
- **D6/D7/D8**：无 context、过期 context、payload 自报 principal → `UNTRUSTED_ACTOR`。
- **D9**：新 store 实例（模拟 restart）读同文件：`SETTLED→REPLAY`、`EFFECT_UNKNOWN→REFUSE_UNKNOWN`。
- **D11**：`TIMEOUT→EFFECT_UNKNOWN`，retry `REFUSE_UNKNOWN`。
- **D12/D13/D13b**：同 key 换 session / principal / runtime 均 `CONFLICT`。
- **D14**：SETTLED 后的晚到 observation `REFUSE_NOT_UNKNOWN`。

### 3.3 仍未关闭 / 未验证

| 项 | 级别 | 说明 |
|---|---|---|
| owner-observation reconcile 未接生产 | **P1（缺口，非新漏洞）** | `reconcile_dispatch_attempt` 在 `7a87e5c` 仍只有测试调用；`EFFECT_UNKNOWN` 的观测式和解没有 served 入口。dispatch 侧可由原 actor 以带 result 的 `SETTLED` 推进，但“向 provider/owner 求证”的路径未接线。 |
| grant 是否在该边界强制 | **P2 / NOT_RUN** | `agent.dispatch.record` 校验 context 的 validate/freshness，但不检查 grant；grant 强制仍在 action admission 路径。本快照没有把 admission 路径纳入，因此只记为 SOURCE 观察，不判 PASS/FAIL。 |
| provider observation 可信度 | **BLOCKED_PROVIDER_SOURCE** | `SETTLED` 只要求 `result` 非 None。**原 actor 提交的 vendor-fabricated result 不是可信 provider observation**；本轮所有 context/result 都是合成输入，**不证明 exactly-once**。 |
| REAL_DEVICE / CLIENT_E2E | **NOT_RUN** | 无真实设备、client、provider 或登录态。 |
| G1 present-but-null | **P1 仍 OPEN** | `619cf63` 未变；沿用 Snapshot B 结果；owner repair 先前为 `BLOCKED_ACTION`，本轮不尝试。 |
| I1 pin 漂移 | **P1** | `baabd56` 仍 pin S2 `fdc223f`，落后 live head 1 commit；`qa.status=INDEPENDENT_QA_PARTIAL`、`signature=null`。 |

### 3.4 对作者声明的质询

- 开发者声明 **119 pass**、**391 pass/171 subtests**：**未独立复跑**（不跑全量）；这些数字不能替代独立负例。
- 声明 **D3/D4/D10 已修**：在本轮 red→green 控制下**成立（LOCAL）**——旧 head 10 项失败，新 head 33/33 全绿。
- 生产接线（SOURCE）：唯一非测试 router 构造在 `mcp/agent_mcp.py:1155`，`attempts=DaemonAttempts(daemon)`，即写记录经过 daemon 的 actor/transition 检查；CLI 路径（`fleet/agent_control_router.py:1837`）不挂 durable attempt。没有发现把 raw store 直接交给生产 router 的路径。

## 4. 逐 claim 证据级别

| Claim | Level |
|---|---|
| D3 跨 actor settle 被拒 | LOCAL（真实 daemon handler + 合成 context） |
| D4 终态/UNKNOWN 回退被拒、终态 REPLAY | LOCAL（真实 daemon handler + 真实 store 文件） |
| D10 空 writer / 无 principal 被拒 | LOCAL（kernel） |
| transition 检查与写入同锁 | SOURCE（`_mutation` 内） |
| 同 key 换 target/principal/runtime | LOCAL（kernel + daemon 写点） |
| 过期 context 拒绝 | LOCAL（合成 context） |
| restart / late reply | LOCAL（新 store 实例 / kernel） |
| provider result 可信 / exactly-once | **NOT ESTABLISHED**（BLOCKED_PROVIDER_SOURCE） |
| 真实设备 / client E2E | **NOT_RUN** |

## 5. 状态汇总（A → D）

| ID | A | B | C | D |
|---|---|---|---|---|
| P0-1 digest target/runtime | OPEN | OPEN | CLOSED (LOCAL) | CLOSED (LOCAL) |
| P0-2 信任判决/伪造 settle | OPEN | OPEN | PARTIAL | **dispatch 侧 CLOSED (LOCAL)**（D3/D4）；owner-observation 未接线 → P1 缺口 |
| P0-3 F1 guard | OPEN | CLOSED (LOCAL) | CLOSED (LOCAL) | CLOSED (LOCAL)（未重复） |
| P2 空 writer 相等 | OPEN | OPEN | OPEN | **CLOSED (LOCAL)**（D10） |
| P1 G1 present-but-null | OPEN | OPEN | OPEN | OPEN（SHA 未变，不 repair） |
| I1 pin 漂移 | OPEN | OPEN | OPEN | OPEN（`baabd56` pin `fdc223f`） |

**Next owners**：owner-observation 接线 → S2；G1 → owner（`BLOCKED_ACTION`，QA 不发起）；I1 → composition 前重 pin 到 `7a87e5c`。T4/T9/T7 仍需 lane 的 LOCAL receipt；T8/T12 仍 `BLOCKED`（无受信通道）。

## 6. 证据

| 内容 | 路径 | sha256 |
|---|---|---|
| 双树 red→green 日志（exit 0，CONTROL_OK） | `/data/home/vince/artifacts/skills/automated-testing/v1-qa-20261017/run-4.log` | `7f3dd737984c7e476432cac5587303a2a0f49c3986a79fa7f71fdcfe97870330` |
| 探针源码 | `/data/home/vince/artifacts/skills/automated-testing/v1-qa-20261017/qa_probe_d.py` | `0ec22145062c1c6d6ecf83d746f045ad7fbdb6bc01fdc7d569d658121e725f52` |
| 旧树（red 控制） | `.../d-old/`（`git archive fdc223f`） | — |
| 新树（green） | `.../d-new/`（`git archive 7a87e5c`） | — |
| 运行内 store | `.../d-scratch-old/`、`.../d-scratch-new/` | 合成测试数据 |

**最终判定：`QA_PARTIAL`。** dispatch 侧 D3/D4/D10 已在 LOCAL 层关闭；但 provider observation source 仍 `BLOCKED_PROVIDER_SOURCE`，REAL_DEVICE/CLIENT_E2E 与 lane 的 LOCAL receipts 缺失，不签 PASS，不进入 RC composition。
