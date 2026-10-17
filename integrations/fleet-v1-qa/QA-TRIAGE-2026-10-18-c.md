# Fleet V1 独立 QA — Snapshot C（2026-10-18 凌晨）

- **角色**：独立 QA lane（campaign `ProfessorZhi/serveros#244` / `#237`）。非 S2 开发者、非 I1 integrator。
- **状态**：`QA_PARTIAL`。**没有** QA PASS，**没有** signature，**没有** `V1_COMPLETE`。
- **对象**：S2 #252 精确 head `fdc223f15d8e4d826c2759fb884ac1702a00f00f`（相对 Snapshot B 的 `f2b89e1` 新 1 commit）。
- **边界**：仍只改 `integrations/fleet-v1-qa/**`；未改产品代码、I1 或任何 lane 文件；未做 provider/network/credential/production 调用；无 subagents。

## 1. Preflight 与快照

`2026-10-18T01:46+08:00`：

- load `15.61 / 15.32 / 15.61`（128 vCPU）；cpu PSI some avg10 `0.46`，io some/full `0.03`，**memory PSI `0.00`**。
- MemAvailable 169 GiB；swap 已用约 30.0 GiB / 32 GiB（≈89.5%，SwapFree 3.4 GiB）。按约定强制 `MemorySwapMax=0`，判定 **resource GREEN**。
- 无 `pytest` / `serverosctl test` 进程；无其他测试锁。
- 远端刷新 `git fetch --all --prune` exit 0。

| 对象 | 精确 head | live head | 说明 |
|---|---|---|---|
| S2 #252 | `fdc223f15d8e4d826c2759fb884ac1702a00f00f` | 一致 | 本轮唯一被测新产品 SHA |
| F1 #243 | `4a5d0a06f2452424972d66399b7c2b60dc8d03a1` | 一致 | Snapshot B 已独立复跑 guard；本轮**不重复** 10 tests |
| G1 #255 | `619cf637cbdba3020f989cee3d9f72b7f2b25ce5` | 一致 | 未变；present-but-null 问题沿用 Snapshot B，不尝试 repair |
| I1 #254 | `5a07df2a3ebca227893c7d0d38f4987333f4f085` | 一致 | **仍 pin S2 `f2b89e1`**，落后本轮被测 head 1 commit；`qa.status=INDEPENDENT_QA_PARTIAL`，`signature=null` |

## 2. 唯一一条受限命令

```sh
systemd-run --user --scope \
  -p MemoryMax=1G -p MemoryHigh=768M -p CPUQuota=25% -p MemorySwapMax=0 \
  -- bash -c 'ulimit -v 1048576; QA_TREE=<archive> QA_SCRATCH=<scratch> python3 qa_probe_c.py'
```

- 把 `fdc223f` 整树 `git archive` 到证据目录，所有模块从该树加载（`runtime/daemon.py`、`agent_control_store.py`、`dispatch_attempts.py`、`context/actor.py`）。
- 在**真实 daemon handler** `agent.dispatch.record` 上做负例；两次 harness 修正（`_now()` 返回 epoch float）后记录最终运行。
- **command exit = 1**（有意：exit 0 才表示所有 MUST-NOT-PASS 都被拒绝；退出码即 receipt）。24 个检查中 21 通过、**3 个安全负例被违反**。
- 日志：`run-3.log`，sha256 `9485ef1296be61297b1b2b9d47ec6c6ed5dba33e4701b23635a2db5ce73e47be`。
- 探针：`qa_probe_c.py`，sha256 `67c700205be5dd968b1b3e5a2ee27cff4cba89f719f6f2c47de795ef4a02048c`。

## 3. 结果（对照 Snapshot A/B）

### 3.1 已关闭（LOCAL）

| Claim | 检查 | 级别 |
|---|---|---|
| **P0-1 digest 漏 target/runtime** | D12 同 key 换 `session_ref` → `CONFLICT`；D13 换 `principal` → `CONFLICT`；D13b 换 `agent_runtime` → `CONFLICT`；D2 在 daemon 写点同 key 换 session 也被拒 | **LOCAL** |
| identity 取自可信 context | D1 记录 principal=`human:A`；D8 payload 自报 `human:B` → `UNTRUSTED_ACTOR` | **LOCAL（合成 context）** |
| 无 context / 过期 context 拒绝 | D6 → `UNTRUSTED_ACTOR`；D7（超过 freshness window）→ `UNTRUSTED_ACTOR` | **LOCAL（合成 context）** |
| SETTLED 必须带 result 且必须有前序 attempt | D5 → `INVALID_CONTEXT` | **LOCAL** |
| 跨 router/restart retry fenced | D9 新 store 实例读同文件：SETTLED → `REPLAY`，EFFECT_UNKNOWN → `REFUSE_UNKNOWN` | **LOCAL** |
| 模糊结局保持 UNKNOWN | D11 `TIMEOUT` → `EFFECT_UNKNOWN`，retry → `REFUSE_UNKNOWN` | **LOCAL** |
| 晚到 observation 不覆盖终态 | D14 → `REFUSE_NOT_UNKNOWN` | **LOCAL** |

### 3.2 仍违反 MUST-NOT-PASS（P0/P2）

**D3 · P0 · 另一个可信 actor 可以伪造 SETTLED 并注入假 provider result。**
actor A 先写入 `CALL_NOT_SENT`（digest 含 principal=A、session=one、runtime=claude-code）。actor B 用自己的可信 context、复制 A 的 `intent_digest`、phase=`SETTLED`、`result={"ok":true,"provider_result":"fabricated"}` 调 `agent.dispatch.record`：

```text
SECURITY_FAIL D3 cross-actor forged SETTLED is refused: got=True expected=False
PASS D3 record now claims actor B and a fabricated result:
     got=('human:B', {'ok': True, 'provider_result': 'fabricated'})
```

D3 之后 k1 是 `SETTLED` + principal `human:B` + 假 result；随后 D4 又把它改成 `CALL_NOT_SENT`（最终 store 摘要因此显示 `k1: CALL_NOT_SENT`）。

daemon 只检查“claimed principal == 解析出的 principal”（空则不检查），不检查“settler 是否原 dispatcher”；`intent_digest` 是调用方原样传入的，store 只比较 digest 字符串。于是 B 可以把 A 的 attempt 覆盖成自己的 principal + 伪造 result。作者 P0-2 的“principal 取自 context”成立，但“**同一个 attempt 只能由发起它的 actor 结算**”不成立。

**D4 · P0 · 终态 SETTLED 可以被回退为 CALL_NOT_SENT，随后 retry 得到 PROCEED。**
在 D3 已写入 SETTLED 后，同一 key/同一 digest 再写 phase=`CALL_NOT_SENT` 被接受（daemon 只在 `phase == SETTLED` 分支检查前序状态；其他 phase 无 transition guard）。随后 kernel：

```text
SECURITY_FAIL D4 terminal SETTLED cannot be reopened: got=True expected=False
PASS D4 reopened attempt authorizes PROCEED: got='PROCEED' expected='PROCEED'
```

这意味着“延迟写入/第二个写者”可以让一个已终结的 effect 重新变成可发送，retry 会立即产生第二次效果。与“delayed reply must not override terminal result”直接冲突。

**D10 · P2 · 空 current writer 仍被当成当前 writer。**
`_same_writer({}, {})` 仍以 `str(None) == str(None)` 逐字段相等；D10 得到 `SETTLE_APPLIED` 而非 `REFUSE_STALE_WRITER`。`reconcile_dispatch_attempt` 在 `fdc223f` 仍**没有生产调用方**（只有 `tests/test_dispatch_attempts.py`），所以这是“未接线 + 失败不关闭”的组合，不是可用的生产门。

### 3.3 对作者声明的独立质询

| 作者声明 | 独立结论 |
|---|---|
| `501 passed / 171 subtests` | **未独立复跑**（按任务约束不跑全量）。该数字不能覆盖 D3/D4：新增的 8 个 daemon 测试全部是“正例/单点拒绝”，没有“B 结算 A 的 attempt”或“SETTLED 回退”的负例。测试通过 ≠ 安全边界成立。 |
| P0-1 已修 | **成立（LOCAL）**：digest 现在包含 `session_ref`/`agent_runtime`/`principal`，三轴均 CONFLICT。 |
| P0-2 已修 | **部分成立（LOCAL）**：无/过期 context、payload 自报 principal、无 result 的 settle 均被拒；但跨 actor 结算与终态回退仍开放，见 D3/D4。 |
| provider result 可信 | **不成立**：`SETTLED` 只要求 `result` 非 None；daemon 无法验证它来自 provider。测试里传入的 fake result/observation 是 **LOCAL 合成输入**，不是 REAL_DEVICE 证据。`BLOCKED_PROVIDER_SOURCE` 未改变，**不主张 exactly-once**。 |

## 4. 逐 claim 证据级别

| Claim | Level |
|---|---|
| digest target/runtime/principal 冲突 | LOCAL（kernel + daemon 写点，精确 SHA） |
| 无/过期 context、自报 principal 拒绝 | LOCAL（**合成** trusted context；不是 REAL_DEVICE） |
| 跨 actor 伪造 SETTLED 被接受（D3） | LOCAL（真实 daemon handler + 合成 context） |
| SETTLED 回退为 CALL_NOT_SENT 并 PROCEED（D4） | LOCAL（真实 daemon handler + 合成 context） |
| 空 writer → SETTLE_APPLIED（D10） | LOCAL（kernel） |
| 跨 restart retry fenced / EFFECT_UNKNOWN | LOCAL（新 store 实例读同一文件） |
| F1 guard 绿 | Snapshot B 的 LOCAL（`4a5d0a`，10/10），本轮不重复 |
| G1 present-but-null | Snapshot B 的 LOCAL（`619cf63`，SHA 未变）；本轮不重跑、不 repair |
| I1 pin 漂移 | SOURCE |

**没有任何 REAL_DEVICE 或 CLIENT_E2E 证据。**

## 5. 状态汇总（A → B → C）

| ID | A | B | C | 说明 |
|---|---|---|---|---|
| P0-1 digest 漏 target/runtime | OPEN | OPEN | **CLOSED (LOCAL)** | D12/D13/D13b/D2 |
| P0-2 信任判决/可自证 | OPEN | OPEN | **PARTIAL**：基础拒绝关闭；**跨 actor 伪造 SETTLED 与终态回退仍 OPEN（P0）** | D3/D4；D5–D8 关闭 |
| P0-3 F1 guard | OPEN | CLOSED (LOCAL) | CLOSED (LOCAL) | B 已独立 10/10 |
| P1 G1 present-but-null | OPEN | OPEN | OPEN（SHA 未变，沿用 B） | 不 repair、不外推 |
| P2 空 writer 相等 | OPEN | OPEN | OPEN | D10；reconcile 仍无生产调用方 |
| I1 pin 漂移 | OPEN | OPEN | OPEN | `5a07df2` 仍 pin `f2b89e1` |

**Next owners**：D3/D4/D10 → S2（`fdc223f` 之后）；G1 → owner（BLOCKED_ACTION，QA 不发起）；I1 → 下一次 composition 前重 pin 到 live head。

## 6. 证据

| 内容 | 路径 | sha256 |
|---|---|---|
| 受限命令日志（exit 1，3 个 SECURITY_FAIL） | `/data/home/vince/artifacts/skills/automated-testing/v1-qa-20261017/run-3.log` | `9485ef1296be61297b1b2b9d47ec6c6ed5dba33e4701b23635a2db5ce73e47be` |
| 探针源码 | `/data/home/vince/artifacts/skills/automated-testing/v1-qa-20261017/qa_probe_c.py` | `67c700205be5dd968b1b3e5a2ee27cff4cba89f719f6f2c47de795ef4a02048c` |
| 精确树 | `/data/home/vince/artifacts/skills/automated-testing/v1-qa-20261017/c-fdc223f/` | 由 `git archive fdc223f` 生成 |
| 运行内 store | `/data/home/vince/artifacts/skills/automated-testing/v1-qa-20261017/c-scratch/` | 合成测试数据 |

**最终判定：`QA_PARTIAL`。** P0-2 的两个 MUST-NOT-PASS、P2、以及所有 REAL_DEVICE/CLIENT_E2E 未达成前，不签 PASS，不进入 RC composition。
